"""End-to-end CLI behavior — exit codes, dry-run, force, threshold gate."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "bios_heal.py"


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )


def test_cli_help_exits_zero() -> None:
    cp = run("--help")
    assert cp.returncode == 0
    assert "BIOS dump" in cp.stdout


def test_cli_size_mismatch_returns_2(tmp_bin, base_image: bytes) -> None:
    base_path = tmp_bin("base.bin", base_image)
    short = tmp_bin("short.bin", base_image[: 0x1000])
    out_path = base_path.parent / "out.bin"
    cp = run(str(base_path), str(short), "-o", str(out_path))
    assert cp.returncode == 2
    assert "size mismatch" in cp.stderr.lower() or "size mismatch" in cp.stdout.lower()


def test_cli_dry_run_writes_report_not_bin(tmp_bin, base_image: bytes, dump_image: bytes) -> None:
    base_path = tmp_bin("base.bin", base_image)
    dump_path = tmp_bin("dump.bin", dump_image)
    out_path = base_path.parent / "out.bin"
    cp = run(str(base_path), str(dump_path), "-o", str(out_path), "--dry-run", "--force")
    assert cp.returncode == 0
    assert not out_path.exists()
    report = base_path.parent / "out.bin.report.txt"
    assert report.exists()
    assert "Layout" in report.read_text(encoding="utf-8")


def test_cli_threshold_abort_returns_1(tmp_bin, base_image: bytes, dump_image: bytes) -> None:
    """dump_image differs in ME+BIOS → below default 90% threshold → abort."""
    base_path = tmp_bin("base.bin", base_image)
    dump_path = tmp_bin("dump.bin", dump_image)
    out_path = base_path.parent / "out.bin"
    cp = run(str(base_path), str(dump_path), "-o", str(out_path))
    assert cp.returncode == 1
    assert out_path.exists()
    # Output is dump unchanged
    assert out_path.read_bytes() == dump_image


def test_cli_force_applies_heal(tmp_bin, base_image: bytes, dump_image: bytes) -> None:
    base_path = tmp_bin("base.bin", base_image)
    dump_path = tmp_bin("dump.bin", dump_image)
    out_path = base_path.parent / "out.bin"
    cp = run(
        str(base_path), str(dump_path), "-o", str(out_path),
        "--force", "--preserve", "me", "--no-backup",
    )
    assert cp.returncode == 0
    assert out_path.exists()
    healed = out_path.read_bytes()
    assert len(healed) == len(base_image)
    # ME came from dump
    assert healed[0x1000:0x500000] == dump_image[0x1000:0x500000]
    # BIOS came from base
    assert healed[0x500000:0x800000] == base_image[0x500000:0x800000]


def test_cli_auto_backup_created(tmp_bin, base_image: bytes, dump_image: bytes) -> None:
    base_path = tmp_bin("base.bin", base_image)
    dump_path = tmp_bin("dump.bin", dump_image)
    out_path = base_path.parent / "out.bin"
    cp = run(str(base_path), str(dump_path), "-o", str(out_path), "--force")
    assert cp.returncode == 0
    bak = dump_path.with_suffix(dump_path.suffix + ".bak")
    assert bak.exists()
    assert bak.read_bytes() == dump_image


def test_cli_no_backup_flag_skips_bak(tmp_bin, base_image: bytes, dump_image: bytes) -> None:
    base_path = tmp_bin("base.bin", base_image)
    dump_path = tmp_bin("dump.bin", dump_image)
    out_path = base_path.parent / "out.bin"
    cp = run(str(base_path), str(dump_path), "-o", str(out_path), "--force", "--no-backup")
    assert cp.returncode == 0
    bak = dump_path.with_suffix(dump_path.suffix + ".bak")
    assert not bak.exists()
