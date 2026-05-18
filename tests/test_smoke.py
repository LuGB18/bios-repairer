"""--smoke: pre-write structural re-parse of the healed image."""

from __future__ import annotations

import bios_heal


def test_smoke_passes_on_clean_heal(base_image: bytes) -> None:
    # Healing base against itself is a no-op; result must pass smoke.
    healed = bios_heal.heal(base_image, base_image, bios_heal.parse_fd(base_image), set())
    result = bios_heal.smoke_test(healed, base_image)
    assert result["status"] == "ok"
    assert result["findings"] == []


def test_smoke_detects_lost_fd_signature(base_image: bytes) -> None:
    """If the FD signature is corrupted in the healed image, smoke fails."""
    mangled = bytearray(base_image)
    mangled[0x10:0x14] = b"\xDE\xAD\xBE\xEF"
    result = bios_heal.smoke_test(bytes(mangled), base_image)
    assert result["status"] == "fail"
    assert any("FD" in f for f in result["findings"])


def test_smoke_detects_missing_volume(base_image: bytes) -> None:
    """Wipe the FFSv2 volume header in the healed image; smoke catches it."""
    mangled = bytearray(base_image)
    mangled[0x500028:0x50002C] = b"\x00\x00\x00\x00"  # destroy _FVH signature
    result = bios_heal.smoke_test(bytes(mangled), base_image)
    assert result["status"] == "fail"
    assert any("missing FFSv2 volume" in f for f in result["findings"])


def test_cli_smoke_failure_returns_5_and_skips_write(run_cli, tmp_bin, base_image: bytes) -> None:
    """Build a DUMP whose BIOS region contains 0xFF padding (no FFSv2 volume).
    Preserving the bios region from that dump strips the volume in the healed
    image, which smoke must catch with exit 5."""
    bad_dump = bytearray(base_image)
    # Wipe BIOS region in dump (still keeps FD intact so verify-dump/size OK)
    bad_dump[0x500000:0x800000] = b"\xFF" * 0x300000
    base_path = tmp_bin("base.bin", base_image)
    dump_path = tmp_bin("dump.bin", bytes(bad_dump))
    out_path = base_path.parent / "out.bin"
    code, _, err = run_cli(
        str(base_path), str(dump_path), "-o", str(out_path),
        "--force", "--preserve", "bios", "--smoke", "--no-backup",
    )
    assert code == 5
    assert "smoke test FAILED" in err
    assert not out_path.exists()
    # Reports should still be written for forensics
    assert (base_path.parent / "out.bin.report.txt").exists()


def test_cli_smoke_happy_path(run_cli, tmp_bin, base_image: bytes, dump_image: bytes) -> None:
    base_path = tmp_bin("base.bin", base_image)
    dump_path = tmp_bin("dump.bin", dump_image)
    out_path = base_path.parent / "out.bin"
    code, out, _ = run_cli(
        str(base_path), str(dump_path), "-o", str(out_path),
        "--force", "--smoke", "--no-backup",
    )
    assert code == 0
    assert "smoke test: OK" in out
    assert out_path.exists()


def test_cli_smoke_status_appears_in_json(run_cli, tmp_bin, base_image: bytes, dump_image: bytes) -> None:
    import json
    base_path = tmp_bin("base.bin", base_image)
    dump_path = tmp_bin("dump.bin", dump_image)
    out_path = base_path.parent / "out.bin"
    run_cli(
        str(base_path), str(dump_path), "-o", str(out_path),
        "--force", "--smoke", "--json", "--no-backup",
    )
    payload = json.loads((base_path.parent / "out.bin.report.json").read_text(encoding="utf-8"))
    assert payload["smoke"]["status"] == "ok"
