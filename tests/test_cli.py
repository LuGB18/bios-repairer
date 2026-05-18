"""End-to-end CLI behavior — exit codes, dry-run, force, threshold gate.

Uses the in-process run_cli fixture so pytest-cov records coverage of
main(), argparse handling, report writing, and file I/O — code paths
that subprocess.run() would hide from the coverage instrument.
"""

from __future__ import annotations


def test_cli_help_exits_zero(run_cli) -> None:
    code, out, _ = run_cli("--help")
    assert code == 0
    assert "BIOS dump" in out


def test_cli_size_mismatch_returns_2(run_cli, tmp_bin, base_image: bytes) -> None:
    base_path = tmp_bin("base.bin", base_image)
    short = tmp_bin("short.bin", base_image[: 0x1000])
    out_path = base_path.parent / "out.bin"
    code, out, err = run_cli(str(base_path), str(short), "-o", str(out_path))
    assert code == 2
    assert "size mismatch" in (out + err).lower()


def test_cli_dry_run_writes_report_not_bin(run_cli, tmp_bin, base_image: bytes, dump_image: bytes) -> None:
    base_path = tmp_bin("base.bin", base_image)
    dump_path = tmp_bin("dump.bin", dump_image)
    out_path = base_path.parent / "out.bin"
    code, _, _ = run_cli(str(base_path), str(dump_path), "-o", str(out_path), "--dry-run", "--force")
    assert code == 0
    assert not out_path.exists()
    report = base_path.parent / "out.bin.report.txt"
    assert report.exists()
    assert "Layout" in report.read_text(encoding="utf-8")


def test_cli_threshold_abort_returns_1(run_cli, tmp_bin, base_image: bytes, dump_image: bytes) -> None:
    """dump_image differs in ME+BIOS → below default 90% threshold → abort."""
    base_path = tmp_bin("base.bin", base_image)
    dump_path = tmp_bin("dump.bin", dump_image)
    out_path = base_path.parent / "out.bin"
    code, _, _ = run_cli(str(base_path), str(dump_path), "-o", str(out_path))
    assert code == 1
    assert out_path.exists()
    assert out_path.read_bytes() == dump_image


def test_cli_force_applies_heal(run_cli, tmp_bin, base_image: bytes, dump_image: bytes) -> None:
    base_path = tmp_bin("base.bin", base_image)
    dump_path = tmp_bin("dump.bin", dump_image)
    out_path = base_path.parent / "out.bin"
    code, _, _ = run_cli(
        str(base_path), str(dump_path), "-o", str(out_path),
        "--force", "--preserve", "me", "--no-backup",
    )
    assert code == 0
    healed = out_path.read_bytes()
    assert len(healed) == len(base_image)
    assert healed[0x1000:0x500000] == dump_image[0x1000:0x500000]
    assert healed[0x500000:0x800000] == base_image[0x500000:0x800000]


def test_cli_auto_backup_created(run_cli, tmp_bin, base_image: bytes, dump_image: bytes) -> None:
    base_path = tmp_bin("base.bin", base_image)
    dump_path = tmp_bin("dump.bin", dump_image)
    out_path = base_path.parent / "out.bin"
    code, _, _ = run_cli(str(base_path), str(dump_path), "-o", str(out_path), "--force")
    assert code == 0
    bak = dump_path.with_suffix(dump_path.suffix + ".bak")
    assert bak.exists()
    assert bak.read_bytes() == dump_image


def test_cli_no_backup_flag_skips_bak(run_cli, tmp_bin, base_image: bytes, dump_image: bytes) -> None:
    base_path = tmp_bin("base.bin", base_image)
    dump_path = tmp_bin("dump.bin", dump_image)
    out_path = base_path.parent / "out.bin"
    code, _, _ = run_cli(str(base_path), str(dump_path), "-o", str(out_path), "--force", "--no-backup")
    assert code == 0
    bak = dump_path.with_suffix(dump_path.suffix + ".bak")
    assert not bak.exists()


def test_cli_unknown_preserve_zone_warns(run_cli, tmp_bin, base_image: bytes, dump_image: bytes) -> None:
    base_path = tmp_bin("base.bin", base_image)
    dump_path = tmp_bin("dump.bin", dump_image)
    out_path = base_path.parent / "out.bin"
    code, _, err = run_cli(
        str(base_path), str(dump_path), "-o", str(out_path),
        "--force", "--preserve", "me,xyz", "--no-backup",
    )
    assert code == 0
    assert "unknown preserve" in err.lower()


def test_cli_no_intel_fd_falls_back_to_default_layout(run_cli, tmp_bin, base_image: bytes) -> None:
    """Strip the FD signature from base — script should warn and proceed."""
    mangled = bytearray(base_image)
    mangled[0x10:0x14] = b"\xDE\xAD\xBE\xEF"
    base_path = tmp_bin("base.bin", bytes(mangled))
    dump_path = tmp_bin("dump.bin", base_image)
    out_path = base_path.parent / "out.bin"
    code, out, _ = run_cli(str(base_path), str(dump_path), "-o", str(out_path), "--force", "--no-backup")
    assert code == 0
    assert "default_layout" in out.lower() or "no intel fd" in out.lower()
