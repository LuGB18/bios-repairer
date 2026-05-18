#!/usr/bin/env python3
"""
bios_heal.py — sanitize/heal a BIOS dump against a clean base image.

Preserves board-specific data (ME region, BIOS NVRAM volume with serial/UUID/MAC).
Restores corruption in code regions (FD, DXE, PEI, boot block) from the base.

Workflow:
  1. Parse Intel Flash Descriptor of base to discover region layout.
  2. Auto-derive NVRAM zone from first FFSv2 volume inside BIOS region.
  3. Scan all FFSv2 volumes in base and dump; CRC32 + header checksum each.
  4. Compute per-region similarity (dump vs base).
  5. If global similarity < threshold (default 90%) -> abort and emit dump unchanged
     (unless --force).
  6. Otherwise build healed image: base bytes everywhere EXCEPT preserve zones
     (which are copied verbatim from dump).
  7. Auto-backup the dump to <dump>.bak before any write (unless --no-backup).
  8. Validate output size == base size; re-scan volumes/padding.
  9. Emit healed .bin and a human-readable <output>.report.txt.

Usage:
  python bios_heal.py BASE DUMP -o OUTPUT [--threshold 0.90]
                                          [--preserve me,nvram,gbe]
                                          [--padding-min 256]
                                          [--force] [--dry-run] [--no-backup]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import zlib
from datetime import datetime
from pathlib import Path

__version__ = "1.2.0"

FD_SIG_OFFSET = 0x10
FD_SIG = b"\x5A\xA5\xF0\x0F"

FFS_V2_GUID = bytes.fromhex("78E58C8C3D8A1C4F99358961") + b"\x85\xC3\x2D\xD3"
FFS_V3_GUID = bytes.fromhex("78E58C8C3D8A6D4D99358961") + b"\x85\xC3\x2D\xD3"  # FFSv3 (rare on B75)
FVH_SIG = b"_FVH"

DEFAULT_LAYOUT: dict[str, tuple[int, int]] = {
    "fd":    (0x000000, 0x001000),
    "me":    (0x001000, 0x500000),
    "nvram": (0x500000, 0x520000),
    "bios":  (0x520000, 0x800000),
}

DEFAULT_PRESERVE = {"me", "nvram"}

PADDING_MIN_RUN = 256
SIMILARITY_THRESHOLD = 0.90


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def crc32(data: bytes) -> str:
    return f"{zlib.crc32(data) & 0xFFFFFFFF:08X}"


def parse_fd(data: bytes) -> dict[str, tuple[int, int]] | None:
    if data[FD_SIG_OFFSET:FD_SIG_OFFSET + 4] != FD_SIG:
        return None
    flmap0 = int.from_bytes(data[0x14:0x18], "little")
    frba = ((flmap0 >> 16) & 0xFF) << 4
    names = ["fd", "bios", "me", "gbe", "pdr"]
    regions: dict[str, tuple[int, int]] = {}
    for i, name in enumerate(names):
        reg = int.from_bytes(data[frba + i * 4: frba + i * 4 + 4], "little")
        base = (reg & 0xFFF) << 12
        limit = (((reg >> 16) & 0xFFF) << 12) | 0xFFF
        if base < limit:
            regions[name] = (base, limit + 1)
    return regions or None


def fvh_uint16_checksum(header: bytes) -> int:
    """FFSv2 header integrity: sum of all UINT16 words must equal 0 (mod 0x10000)."""
    total = 0
    for i in range(0, len(header), 2):
        total = (total + int.from_bytes(header[i:i + 2], "little")) & 0xFFFF
    return total


def scan_ffsv2_volumes(data: bytes, start: int, end: int) -> list[dict]:
    """Find all FFSv2/v3 volumes in [start, end). Returns list of dicts with metadata."""
    volumes: list[dict] = []
    pos = start
    while pos < end - 0x40:
        guid = data[pos + 0x10:pos + 0x20]
        sig = data[pos + 0x28:pos + 0x2C]
        if sig == FVH_SIG and guid in (FFS_V2_GUID, FFS_V3_GUID):
            vol_len = int.from_bytes(data[pos + 0x20:pos + 0x28], "little")
            hdr_len = int.from_bytes(data[pos + 0x30:pos + 0x32], "little")
            stored_csum = int.from_bytes(data[pos + 0x32:pos + 0x34], "little")
            if 0 < vol_len <= end - pos and 0 < hdr_len <= vol_len:
                header = data[pos:pos + hdr_len]
                csum_ok = fvh_uint16_checksum(header) == 0
                body = data[pos:pos + vol_len]
                volumes.append({
                    "offset": pos,
                    "length": vol_len,
                    "hdr_len": hdr_len,
                    "stored_csum": stored_csum,
                    "csum_ok": csum_ok,
                    "crc32": crc32(body),
                    "guid_type": "FFSv3" if guid == FFS_V3_GUID else "FFSv2",
                })
                pos += vol_len
                continue
        pos += 1
    return volumes


def derive_nvram_zone(base_bytes: bytes, bios_region: tuple[int, int]) -> tuple[int, int] | None:
    vols = scan_ffsv2_volumes(base_bytes, bios_region[0], bios_region[1])
    if not vols:
        return None
    v = vols[0]
    return (v["offset"], v["offset"] + v["length"])


def detect_padding(data: bytes, min_run: int = PADDING_MIN_RUN) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    n = len(data)
    i = 0
    while i < n:
        if data[i] == 0xFF:
            j = i
            while j < n and data[j] == 0xFF:
                j += 1
            if j - i >= min_run:
                runs.append((i, j))
            i = j
        else:
            i += 1
    return runs


def similarity(a: bytes, b: bytes) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    return sum(1 for x, y in zip(a, b, strict=True) if x == y) / len(a)


def per_region_similarity(base: bytes, dump: bytes,
                          layout: dict[str, tuple[int, int]]) -> dict[str, float]:
    return {name: similarity(base[s:e], dump[s:e]) for name, (s, e) in layout.items()}


def heal(base: bytes, dump: bytes,
         layout: dict[str, tuple[int, int]],
         preserve: set[str]) -> bytes:
    out = bytearray(base)
    for name in preserve:
        if name not in layout:
            continue
        s, e = layout[name]
        out[s:e] = dump[s:e]
    return bytes(out)


def fmt_zone(s: int, e: int) -> str:
    return f"0x{s:08X}-0x{e - 1:08X} ({e - s} bytes)"


def diff_volumes(base_vols: list[dict], dump_vols: list[dict]) -> list[dict]:
    """Match volumes by offset, report status."""
    base_map = {v["offset"]: v for v in base_vols}
    dump_map = {v["offset"]: v for v in dump_vols}
    rows: list[dict] = []
    for off in sorted(set(base_map) | set(dump_map)):
        b = base_map.get(off)
        d = dump_map.get(off)
        if b and d:
            status = "identical" if b["crc32"] == d["crc32"] else "diff"
        elif b:
            status = "missing_in_dump"
        else:
            status = "extra_in_dump"
        rows.append({"offset": off, "base": b, "dump": d, "status": status})
    return rows


def write_json_report(path: Path, ctx: dict) -> None:
    """Machine-readable report — stable schema, byte offsets as integers,
    similarities as floats in [0,1], CRC32 as hex strings (no 0x prefix)."""
    layout_out: dict[str, dict] = {}
    for name, (s, e) in ctx["layout"].items():
        layout_out[name] = {
            "start": s,
            "end": e,
            "length": e - s,
            "similarity": ctx["region_sim"].get(name, 0.0),
            "preserved": name in ctx["preserve"],
        }

    volumes_out = []
    for row in ctx["volume_diff"]:
        b = row["base"]
        d = row["dump"]
        volumes_out.append({
            "offset": row["offset"],
            "length": (b["length"] if b else (d["length"] if d else 0)),
            "base_crc32": b["crc32"] if b else None,
            "dump_crc32": d["crc32"] if d else None,
            "header_checksum_ok": (b["csum_ok"] if b else None),
            "guid_type": (b["guid_type"] if b else (d["guid_type"] if d else None)),
            "status": row["status"],
        })

    base_set = set(ctx["pad_base"])
    out_set = set(ctx["pad_out"])
    pad_lost = sorted(base_set - out_set)
    pad_gained = sorted(out_set - base_set)

    payload = {
        "schema_version": 1,
        "tool_version": __version__,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "files": {
            "base":   {"path": ctx["base_path"], "md5": ctx["base_md5"], "size": ctx["size"]},
            "dump":   {"path": ctx["dump_path"], "md5": ctx["dump_md5"], "size": ctx["size"]},
            "output": {"path": ctx["out_path"],  "md5": ctx["out_md5"]},
        },
        "mode": {
            "dry_run": ctx["dry_run"],
            "force":   ctx["force"],
        },
        "layout": layout_out,
        "preserve": sorted(ctx["preserve"]),
        "similarity": {
            "global":    ctx["global_sim"],
            "threshold": ctx["threshold"],
        },
        "decision": ctx["decision"],
        "volumes": volumes_out,
        "padding": {
            "min_run":     ctx["padding_min"],
            "base_runs":   len(ctx["pad_base"]),
            "dump_runs":   len(ctx["pad_dump"]),
            "healed_runs": len(ctx["pad_out"]),
            "lost":   [{"start": s, "end": e, "length": e - s} for s, e in pad_lost],
            "gained": [{"start": s, "end": e, "length": e - s} for s, e in pad_gained],
        },
        "diff": {
            "total_bytes":   ctx["diff_total"],
            "in_preserve":   ctx["diff_preserve"],
            "outside":       ctx["diff_outside"],
            "percent":       (ctx["diff_total"] / ctx["size"] * 100) if ctx["size"] else 0.0,
        },
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_report(path: Path, ctx: dict) -> None:
    lines: list[str] = []
    lines.append(f"bios_heal report — {datetime.now().isoformat(timespec='seconds')}")
    lines.append("=" * 72)
    lines.append(f"base   : {ctx['base_path']}  md5={ctx['base_md5']}  ({ctx['size']} B)")
    lines.append(f"dump   : {ctx['dump_path']}  md5={ctx['dump_md5']}  ({ctx['size']} B)")
    lines.append(f"output : {ctx['out_path']}  md5={ctx['out_md5']}")
    lines.append(f"mode   : {'dry-run' if ctx['dry_run'] else 'write'}  force={ctx['force']}")
    lines.append("")
    lines.append("Layout")
    lines.append("-" * 72)
    for name, (s, e) in sorted(ctx["layout"].items(), key=lambda kv: kv[1][0]):
        sim_pct = ctx["region_sim"].get(name, 0.0) * 100
        preserved = " [PRESERVED]" if name in ctx["preserve"] else ""
        lines.append(f"  {name:6s} {fmt_zone(s, e)}  similarity={sim_pct:6.2f}%{preserved}")
    lines.append("")
    lines.append(f"Global similarity   : {ctx['global_sim'] * 100:.2f}%")
    lines.append(f"Threshold           : {ctx['threshold'] * 100:.0f}%")
    lines.append(f"Decision            : {ctx['decision']}")
    lines.append("")
    lines.append("FFSv2 volumes (BIOS region)")
    lines.append("-" * 72)
    lines.append(f"  {'offset':>10}  {'length':>10}  {'base_crc':>10}  {'dump_crc':>10}  {'hdr':>5}  status")
    for row in ctx["volume_diff"]:
        b = row["base"]
        d = row["dump"]
        bcrc = b["crc32"] if b else "----"
        dcrc = d["crc32"] if d else "----"
        blen = b["length"] if b else (d["length"] if d else 0)
        hdr = ("OK" if (b and b["csum_ok"]) else "BAD") if b else "--"
        lines.append(f"  0x{row['offset']:08X}  {blen:10d}  {bcrc:>10}  {dcrc:>10}  {hdr:>5}  {row['status']}")
    lines.append("")
    lines.append("Padding runs (>= "
                 f"{ctx['padding_min']} bytes 0xFF)")
    lines.append("-" * 72)
    lines.append(f"  base   : {len(ctx['pad_base'])} runs")
    lines.append(f"  dump   : {len(ctx['pad_dump'])} runs")
    lines.append(f"  healed : {len(ctx['pad_out'])} runs")
    base_set = set(ctx["pad_base"])
    out_set = set(ctx["pad_out"])
    lost = sorted(base_set - out_set)
    gained = sorted(out_set - base_set)
    if lost:
        lines.append(f"  WARN — padding lost vs base: {len(lost)} runs")
        for s, e in lost[:10]:
            lines.append(f"    - 0x{s:08X}-0x{e - 1:08X} ({e - s} B)")
    if gained:
        lines.append(f"  INFO — padding gained vs base: {len(gained)} runs")
    lines.append("")
    lines.append("Diff summary")
    lines.append("-" * 72)
    lines.append(f"  bytes changed vs dump : {ctx['diff_total']} ({ctx['diff_total'] / ctx['size'] * 100:.4f}%)")
    lines.append(f"  inside preserve       : {ctx['diff_preserve']} (must be 0)")
    lines.append(f"  outside (healed code) : {ctx['diff_outside']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    epilog = """\
EXAMPLES
  # Inspect only (no write), force heal report even if below threshold
  python bios_heal.py base.bin dump.bin -o out.bin --dry-run --force

  # Standard heal (aborts if dump <90% similar to base)
  python bios_heal.py clean.bin board_dump.bin -o repaired.bin

  # Aggressive heal of badly corrupted dump, also preserve GbE MAC
  python bios_heal.py clean.bin dump.bin -o out.bin --force \\
      --threshold 0.5 --preserve me,nvram,gbe

PRESERVE ZONES (board-specific, copied from DUMP)
  me     Intel Management Engine region (MEBx config, board fuse)
  nvram  First FFSv2 volume inside BIOS region (serial / UUID / NIC MAC)
  gbe    Integrated GbE region (NIC MAC) — only if FD declares it
  fd     Flash Descriptor (rarely preserved; usually heal from base)
  bios   Entire BIOS region (rarely preserved; defeats the heal)

EXIT CODES
  0  heal applied
  1  below threshold and not --force (output = dump unchanged) or dry-run abort
  2  base/dump size mismatch
  3  output size sanity check failed
"""
    ap = argparse.ArgumentParser(
        prog="bios_heal.py",
        description="Heal a corrupted SPI BIOS dump using a clean reference image.\n"
                    "Preserves board-specific zones (ME, NVRAM, optionally GbE) from the\n"
                    "dump and restores everything else from the base.",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    pos = ap.add_argument_group("input files")
    pos.add_argument("base", metavar="BASE",
                     help="clean reference BIOS image (.bin) — source of healing bytes")
    pos.add_argument("dump", metavar="DUMP",
                     help="corrupted/board-specific dump (.bin) to heal — source of preserved zones")

    out = ap.add_argument_group("output")
    out.add_argument("-o", "--output", required=True, metavar="FILE",
                     help="path for healed .bin (a sibling FILE.report.txt is also written)")

    tun = ap.add_argument_group("tuning")
    tun.add_argument("--threshold", type=float, default=SIMILARITY_THRESHOLD, metavar="FLOAT",
                     help=f"min global byte-similarity (0.0–1.0) required to apply heal "
                          f"(default {SIMILARITY_THRESHOLD}); below this, dump is copied unchanged")
    tun.add_argument("--preserve", default=",".join(sorted(DEFAULT_PRESERVE)), metavar="LIST",
                     help="comma-separated zones copied verbatim from DUMP "
                          "(default: me,nvram). Choices: fd,me,bios,gbe,pdr,nvram")
    tun.add_argument("--padding-min", type=int, default=PADDING_MIN_RUN, metavar="N",
                     help=f"min consecutive 0xFF bytes counted as a padding run "
                          f"(default {PADDING_MIN_RUN})")

    mode = ap.add_argument_group("mode flags")
    mode.add_argument("--force", action="store_true",
                      help="apply heal even when global similarity is below --threshold")
    mode.add_argument("--dry-run", action="store_true",
                      help="compute and write the .report.txt only; never produce the .bin")
    mode.add_argument("--no-backup", action="store_true",
                      help="skip the automatic <dump>.bak copy that bios_heal writes before "
                           "any output (.bak is never overwritten if it already exists)")
    mode.add_argument("--json", action="store_true",
                      help="also emit a machine-readable <output>.report.json alongside the "
                           "human-readable .report.txt (stable schema, see README)")
    mode.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    args = ap.parse_args()

    base_path = Path(args.base)
    dump_path = Path(args.dump)
    out_path = Path(args.output)
    report_path = out_path.with_suffix(out_path.suffix + ".report.txt")

    base = base_path.read_bytes()
    dump = dump_path.read_bytes()
    base_md5 = md5(base)
    dump_md5 = md5(dump)

    print(f"[+] base : {base_path} ({len(base)} B, md5={base_md5})")
    print(f"[+] dump : {dump_path} ({len(dump)} B, md5={dump_md5})")

    if len(base) != len(dump):
        print(f"[!] size mismatch: base={len(base)} dump={len(dump)}", file=sys.stderr)
        return 2

    layout = parse_fd(base)
    if layout is None:
        print("[!] no Intel FD in base — using DEFAULT_LAYOUT")
        layout = dict(DEFAULT_LAYOUT)
    if "bios" in layout:
        nvram = derive_nvram_zone(base, layout["bios"])
        if nvram:
            layout["nvram"] = nvram

    print("[+] layout:")
    for k, (s, e) in sorted(layout.items(), key=lambda kv: kv[1][0]):
        print(f"    {k:6s} {fmt_zone(s, e)}")

    region_sim = per_region_similarity(base, dump, layout)
    print("[+] per-region similarity:")
    for k, v in sorted(region_sim.items()):
        print(f"    {k:6s} {v * 100:6.2f}%")
    global_sim = similarity(base, dump)
    print(f"[+] global similarity: {global_sim * 100:.2f}%")

    bios_range = layout.get("bios", (0, len(base)))
    base_vols = scan_ffsv2_volumes(base, *bios_range)
    dump_vols = scan_ffsv2_volumes(dump, *bios_range)
    vol_diff = diff_volumes(base_vols, dump_vols)
    print(f"[+] FFSv2 volumes: base={len(base_vols)} dump={len(dump_vols)}")
    for row in vol_diff:
        b = row["base"]
        d = row["dump"]
        bcrc = b["crc32"] if b else "----"
        dcrc = d["crc32"] if d else "----"
        print(f"    0x{row['offset']:08X}  base={bcrc} dump={dcrc}  {row['status']}")

    preserve = {z.strip() for z in args.preserve.split(",") if z.strip()}
    unknown = preserve - layout.keys()
    if unknown:
        print(f"[!] unknown preserve zones ignored: {sorted(unknown)}", file=sys.stderr)
        preserve &= layout.keys()

    decision = ""
    healed: bytes
    if global_sim < args.threshold and not args.force:
        decision = f"abort (similarity {global_sim * 100:.2f}% < threshold {args.threshold * 100:.0f}%)"
        print(f"[!] {decision} — output = dump unchanged")
        healed = dump
        preserve_used: set[str] = set()
    else:
        decision = "heal applied" + (" (forced)" if global_sim < args.threshold else "")
        preserve_used = preserve
        print(f"[+] preserving from dump: {sorted(preserve_used)}")
        healed = heal(base, dump, layout, preserve_used)

    if len(healed) != len(base):
        print("[!] output size mismatch — aborting", file=sys.stderr)
        return 3

    diff_total = sum(1 for a, b in zip(dump, healed, strict=True) if a != b)
    diff_preserve = 0
    for name in preserve_used:
        s, e = layout[name]
        diff_preserve += sum(1 for x, y in zip(dump[s:e], healed[s:e], strict=True) if x != y)
    diff_outside = diff_total - diff_preserve

    pad_base = detect_padding(base, args.padding_min)
    pad_dump = detect_padding(dump, args.padding_min)
    pad_out = detect_padding(healed, args.padding_min)

    ctx = {
        "base_path": str(base_path), "dump_path": str(dump_path), "out_path": str(out_path),
        "base_md5": base_md5, "dump_md5": dump_md5, "out_md5": md5(healed),
        "size": len(base), "layout": layout, "preserve": preserve_used,
        "region_sim": region_sim, "global_sim": global_sim, "threshold": args.threshold,
        "decision": decision, "volume_diff": vol_diff,
        "pad_base": pad_base, "pad_dump": pad_dump, "pad_out": pad_out,
        "padding_min": args.padding_min,
        "diff_total": diff_total, "diff_preserve": diff_preserve, "diff_outside": diff_outside,
        "dry_run": args.dry_run, "force": args.force,
    }

    write_report(report_path, ctx)
    print(f"[+] report: {report_path}")
    if args.json:
        json_path = out_path.with_suffix(out_path.suffix + ".report.json")
        write_json_report(json_path, ctx)
        print(f"[+] report: {json_path}")

    if args.dry_run:
        print("[+] dry-run — output not written")
        return 0 if decision.startswith("heal") else 1

    if not args.no_backup and dump_path.resolve() != out_path.resolve():
        bak = dump_path.with_suffix(dump_path.suffix + ".bak")
        if not bak.exists():
            shutil.copy2(dump_path, bak)
            print(f"[+] dump backup: {bak}")

    out_path.write_bytes(healed)
    print(f"[+] wrote {out_path} ({len(healed)} B, md5={ctx['out_md5']})")
    print(f"[+] bytes changed vs dump: {diff_total} ({diff_total / len(dump) * 100:.4f}%)")
    print(f"    inside preserve: {diff_preserve}  outside: {diff_outside}")

    return 0 if decision.startswith("heal") else 1


if __name__ == "__main__":
    sys.exit(main())
