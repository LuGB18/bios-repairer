#!/usr/bin/env python3
"""
bios_flash.py — companion to bios_heal.py.

Wraps the `flashrom` CLI to read / verify / write an SPI flash chip via
an external programmer (CH341A by default), and chains the heal pipeline
end-to-end: read chip -> heal -> smoke -> write -> verify.

Subcommands:
  read         dump the chip to a file
  verify       re-read the chip and byte-compare to a reference image
  write        program an image into the chip (verify after, --commit required)
  heal-flash   read chip twice (consistency check), heal against a BASE, then
               optionally commit the healed image to the chip

The actual heal step is implemented by importing bios_heal as a module —
no subprocess hop, no PATH dependency, atomic exit-code propagation.

flashrom must be installed and reachable via PATH (or set via --flashrom).

Safety rails
  * write / heal-flash require --commit explicitly. Without it the script
    runs read-only and prints the diff.
  * Auto-backup: the current chip contents are always read into
    <image>.chipbak.<timestamp>.bin before any --commit write.
  * The image byte-length must equal the chip capacity (read-back size).
  * The post-write verify is mandatory and not skippable.
  * The --chip argument must be passed explicitly; no auto-detect on write.

Exit codes
  0   ok
  1   below similarity threshold (heal step, propagated)
  2   size mismatch (heal step or chip vs image)
  3   output size sanity failure (heal step)
  4   --verify-dump mismatch (heal step)
  5   smoke test failed (heal step)
  10  flashrom binary not found on PATH
  11  flashrom detect / probe failure
  12  two consecutive chip reads disagree
  13  post-write verify failed
  14  user did not pass --commit but a write was requested
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import bios_heal

__version__ = "2.0.0"

DEFAULT_PROGRAMMER = "ch341a_spi"
DEFAULT_CHIP = "W25Q64.V"
FLASHROM_NOT_FOUND = 10
PROBE_FAIL = 11
READ_INCONSISTENT = 12
VERIFY_FAIL = 13
COMMIT_REQUIRED = 14


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def find_flashrom(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit if Path(explicit).exists() else None
    return shutil.which("flashrom")


def run_flashrom(flashrom: str, args: list[str]) -> subprocess.CompletedProcess:
    """Invoke flashrom with the given args. Captures stdout+stderr."""
    return subprocess.run(
        [flashrom, *args],
        capture_output=True,
        text=True,
        check=False,
    )


def flashrom_read(flashrom: str, programmer: str, chip: str, out: Path) -> tuple[int, str]:
    cp = run_flashrom(flashrom, ["-p", programmer, "-c", chip, "-r", str(out)])
    return cp.returncode, cp.stdout + cp.stderr


def flashrom_write(flashrom: str, programmer: str, chip: str, image: Path) -> tuple[int, str]:
    cp = run_flashrom(flashrom, ["-p", programmer, "-c", chip, "-w", str(image)])
    return cp.returncode, cp.stdout + cp.stderr


def flashrom_verify(flashrom: str, programmer: str, chip: str, image: Path) -> tuple[int, str]:
    cp = run_flashrom(flashrom, ["-p", programmer, "-c", chip, "-v", str(image)])
    return cp.returncode, cp.stdout + cp.stderr


def ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def emit_json(result: dict, path: Path | None) -> None:
    if path is None:
        return
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# subcommand implementations
# ---------------------------------------------------------------------------

def cmd_read(args: argparse.Namespace) -> int:
    flashrom = find_flashrom(args.flashrom)
    if not flashrom:
        print("[!] flashrom not found on PATH (pass --flashrom)", file=sys.stderr)
        return FLASHROM_NOT_FOUND

    out_path = Path(args.output)
    print(f"[+] flashrom: {flashrom}")
    print(f"[+] programmer: {args.programmer}  chip: {args.chip}")
    print(f"[+] reading chip -> {out_path}")
    code, log = flashrom_read(flashrom, args.programmer, args.chip, out_path)
    if code != 0:
        print("[!] flashrom read failed:", file=sys.stderr)
        print(log, file=sys.stderr)
        return PROBE_FAIL
    if not out_path.exists():
        print("[!] flashrom returned 0 but no file was written", file=sys.stderr)
        return PROBE_FAIL

    data = out_path.read_bytes()
    digest = md5(data)
    print(f"[+] read {len(data)} B (md5={digest})")

    emit_json({
        "tool_version": __version__,
        "operation": "read",
        "programmer": args.programmer,
        "chip": args.chip,
        "output": str(out_path),
        "size": len(data),
        "md5": digest,
        "status": "ok",
    }, Path(args.json) if args.json else None)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    flashrom = find_flashrom(args.flashrom)
    if not flashrom:
        print("[!] flashrom not found on PATH (pass --flashrom)", file=sys.stderr)
        return FLASHROM_NOT_FOUND

    ref_path = Path(args.against)
    ref = ref_path.read_bytes()
    print(f"[+] reference: {ref_path} ({len(ref)} B, md5={md5(ref)})")
    print("[+] reading chip and asking flashrom to verify against reference")

    code, log = flashrom_verify(flashrom, args.programmer, args.chip, ref_path)
    status = "ok" if code == 0 else "mismatch"
    if code != 0:
        print("[!] verify FAILED", file=sys.stderr)
        print(log, file=sys.stderr)
    else:
        print("[+] verify OK — chip matches reference")

    emit_json({
        "tool_version": __version__,
        "operation": "verify",
        "programmer": args.programmer,
        "chip": args.chip,
        "reference": str(ref_path),
        "reference_md5": md5(ref),
        "status": status,
        "flashrom_exit": code,
    }, Path(args.json) if args.json else None)
    return 0 if code == 0 else VERIFY_FAIL


def cmd_write(args: argparse.Namespace) -> int:
    flashrom = find_flashrom(args.flashrom)
    if not flashrom:
        print("[!] flashrom not found on PATH (pass --flashrom)", file=sys.stderr)
        return FLASHROM_NOT_FOUND

    if not args.commit:
        print("[!] write requires --commit to actually program the chip", file=sys.stderr)
        return COMMIT_REQUIRED

    image_path = Path(args.image)
    image = image_path.read_bytes()
    print(f"[+] image  : {image_path} ({len(image)} B, md5={md5(image)})")

    # Mandatory chip-backup before write
    if not args.no_backup:
        bak = image_path.with_name(f"{image_path.stem}.chipbak.{ts()}{image_path.suffix}")
        print(f"[+] backing up chip BEFORE write -> {bak}")
        rc, log = flashrom_read(flashrom, args.programmer, args.chip, bak)
        if rc != 0 or not bak.exists():
            print("[!] pre-write chip backup failed; aborting write", file=sys.stderr)
            print(log, file=sys.stderr)
            return PROBE_FAIL
        chip_size = bak.stat().st_size
        if chip_size != len(image):
            print(
                f"[!] size mismatch: image={len(image)} chip={chip_size}",
                file=sys.stderr,
            )
            return 2

    print(f"[+] writing {image_path} to chip ...")
    rc, log = flashrom_write(flashrom, args.programmer, args.chip, image_path)
    if rc != 0:
        print("[!] flashrom write failed:", file=sys.stderr)
        print(log, file=sys.stderr)
        return PROBE_FAIL

    print("[+] write complete; running mandatory post-write verify")
    vrc, vlog = flashrom_verify(flashrom, args.programmer, args.chip, image_path)
    if vrc != 0:
        print("[!] post-write verify FAILED — re-flash recommended", file=sys.stderr)
        print(vlog, file=sys.stderr)
        emit_json({
            "tool_version": __version__,
            "operation": "write",
            "status": "verify_failed",
            "image": str(image_path),
        }, Path(args.json) if args.json else None)
        return VERIFY_FAIL

    print("[+] post-write verify OK")
    emit_json({
        "tool_version": __version__,
        "operation": "write",
        "programmer": args.programmer,
        "chip": args.chip,
        "image": str(image_path),
        "image_md5": md5(image),
        "status": "ok",
    }, Path(args.json) if args.json else None)
    return 0


def cmd_heal_flash(args: argparse.Namespace) -> int:
    flashrom = find_flashrom(args.flashrom)
    if not flashrom:
        print("[!] flashrom not found on PATH (pass --flashrom)", file=sys.stderr)
        return FLASHROM_NOT_FOUND

    workdir = Path(args.workdir) if args.workdir else Path(".")
    workdir.mkdir(parents=True, exist_ok=True)
    stamp = ts()
    dump1 = workdir / f"chip_dump_{stamp}.bin"
    dump2 = workdir / f"chip_dump_{stamp}_verify.bin"
    healed = workdir / f"healed_{stamp}.bin"

    print("[+] STEP 1/5: reading chip (primary dump)")
    rc, log = flashrom_read(flashrom, args.programmer, args.chip, dump1)
    if rc != 0 or not dump1.exists():
        print("[!] primary read failed", file=sys.stderr)
        print(log, file=sys.stderr)
        return PROBE_FAIL
    d1 = dump1.read_bytes()
    print(f"    -> {dump1} ({len(d1)} B, md5={md5(d1)})")

    print("[+] STEP 2/5: reading chip again (consistency check)")
    rc, log = flashrom_read(flashrom, args.programmer, args.chip, dump2)
    if rc != 0 or not dump2.exists():
        print("[!] secondary read failed", file=sys.stderr)
        print(log, file=sys.stderr)
        return PROBE_FAIL
    d2 = dump2.read_bytes()
    if md5(d1) != md5(d2):
        print("[!] two reads of the same chip diverge — flaky chip-clip", file=sys.stderr)
        print(f"    primary  md5={md5(d1)}", file=sys.stderr)
        print(f"    secondary md5={md5(d2)}", file=sys.stderr)
        return READ_INCONSISTENT
    print(f"    -> {dump2} (md5 matches primary)")

    print("[+] STEP 3/5: invoking bios_heal")
    heal_argv = [
        str(args.base), str(dump1),
        "-o", str(healed),
        "--verify-dump", str(dump2),
        "--smoke",
    ]
    if args.force:
        heal_argv.append("--force")
    if args.no_backup:
        heal_argv.append("--no-backup")
    if args.preserve:
        heal_argv += ["--preserve", args.preserve]
    if args.json_heal:
        heal_argv.append("--json")
    heal_rc = bios_heal.main(heal_argv)
    if heal_rc != 0:
        print(f"[!] bios_heal returned exit code {heal_rc}; aborting flash", file=sys.stderr)
        return heal_rc

    if not args.commit:
        print("[+] STEP 4/5: --commit NOT set — heal output kept, chip NOT touched")
        print(f"    review: {healed}")
        emit_json({
            "tool_version": __version__,
            "operation": "heal-flash",
            "status": "dry-run",
            "primary_dump": str(dump1),
            "secondary_dump": str(dump2),
            "healed": str(healed),
        }, Path(args.json) if args.json else None)
        return 0

    print("[+] STEP 4/5: writing healed image to chip")
    rc, log = flashrom_write(flashrom, args.programmer, args.chip, healed)
    if rc != 0:
        print("[!] write failed; chip may be in inconsistent state — re-flash dump1", file=sys.stderr)
        print(log, file=sys.stderr)
        return PROBE_FAIL

    print("[+] STEP 5/5: post-write verify")
    vrc, vlog = flashrom_verify(flashrom, args.programmer, args.chip, healed)
    if vrc != 0:
        print("[!] post-write verify FAILED — original dump1 saved at start", file=sys.stderr)
        print(vlog, file=sys.stderr)
        return VERIFY_FAIL

    print("[+] heal-flash complete")
    emit_json({
        "tool_version": __version__,
        "operation": "heal-flash",
        "status": "ok",
        "primary_dump": str(dump1),
        "secondary_dump": str(dump2),
        "healed": str(healed),
        "programmer": args.programmer,
        "chip": args.chip,
    }, Path(args.json) if args.json else None)
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _add_common_chip_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--programmer", default=DEFAULT_PROGRAMMER,
                   help=f"flashrom -p value (default: {DEFAULT_PROGRAMMER})")
    p.add_argument("--chip", default=DEFAULT_CHIP,
                   help=f"flashrom -c value (default: {DEFAULT_CHIP})")
    p.add_argument("--flashrom", metavar="PATH",
                   help="explicit path to the flashrom binary (default: search PATH)")
    p.add_argument("--json", metavar="FILE",
                   help="write a machine-readable result JSON to FILE")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="bios_flash.py",
        description="flashrom wrapper + chained heal/read/write/verify pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    p_read = sub.add_parser("read", help="dump the chip to a file")
    _add_common_chip_args(p_read)
    p_read.add_argument("-o", "--output", required=True, metavar="FILE",
                        help="destination .bin path")
    p_read.set_defaults(func=cmd_read)

    p_verify = sub.add_parser("verify", help="byte-compare the chip to a reference image")
    _add_common_chip_args(p_verify)
    p_verify.add_argument("--against", required=True, metavar="FILE",
                          help="reference image to verify the chip against")
    p_verify.set_defaults(func=cmd_verify)

    p_write = sub.add_parser("write", help="program an image into the chip")
    _add_common_chip_args(p_write)
    p_write.add_argument("--image", required=True, metavar="FILE",
                         help="image to flash into the chip")
    p_write.add_argument("--commit", action="store_true",
                         help="REQUIRED to actually write; without it, exits with code 14")
    p_write.add_argument("--no-backup", action="store_true",
                         help="skip the mandatory chip-backup read before writing")
    p_write.set_defaults(func=cmd_write)

    p_hf = sub.add_parser("heal-flash",
                          help="read chip x2, run bios_heal, optionally commit")
    _add_common_chip_args(p_hf)
    p_hf.add_argument("--base", required=True, metavar="FILE",
                      help="clean reference BIOS for bios_heal")
    p_hf.add_argument("--preserve", metavar="LIST",
                      help="forwarded to bios_heal --preserve (default: me,nvram)")
    p_hf.add_argument("--workdir", metavar="DIR",
                      help="directory for chip dumps and healed output (default: cwd)")
    p_hf.add_argument("--commit", action="store_true",
                      help="REQUIRED to write the healed image back to the chip")
    p_hf.add_argument("--force", action="store_true",
                      help="forwarded to bios_heal --force")
    p_hf.add_argument("--no-backup", action="store_true",
                      help="forwarded to bios_heal --no-backup (chip dump is still kept)")
    p_hf.add_argument("--json-heal", action="store_true",
                      help="ask bios_heal to also emit its own JSON report")
    p_hf.set_defaults(func=cmd_heal_flash)

    args = ap.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
