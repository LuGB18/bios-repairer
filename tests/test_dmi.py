"""DMI variable-level transplant — surgical NVAR record swap."""

from __future__ import annotations

import bios_heal


def test_find_nvar_entries_picks_up_injected_records(base_with_nvars: bytes) -> None:
    entries = bios_heal.find_nvar_entries(base_with_nvars, 0x500000, 0x520000)
    assert "Setup" in entries
    assert "SystemSerialNumber" in entries
    assert "SystemUuid" in entries


def test_find_nvar_entries_returns_empty_on_blank_volume(base_image: bytes) -> None:
    # base_image has no NVAR records injected — fill is 0xC3, not NVAR sig
    entries = bios_heal.find_nvar_entries(base_image, 0x500000, 0x520000)
    assert entries == {}


def test_transplant_copies_dump_value_into_base(base_with_nvars: bytes,
                                                dump_with_nvars: bytes) -> None:
    buf = bytearray(base_with_nvars)
    result = bios_heal.transplant_dmi_variables(buf, dump_with_nvars, (0x500000, 0x520000))

    assert "Setup" in result["transplanted"]
    assert "SystemSerialNumber" in result["transplanted"]
    assert "SystemUuid" in result["transplanted"]

    # The dump's record bytes should now appear verbatim somewhere in buf
    assert b"DUMP-SERIAL-9999" in bytes(buf)
    assert b"BASE-SERIAL-0000" not in bytes(buf)


def test_transplant_skips_size_mismatch(base_with_nvars: bytes, dump_image: bytes) -> None:
    """If DUMP's record for a variable is a different size, transplant must
    refuse to swap (would corrupt the NVAR chain)."""
    from tests.conftest import inject_nvars, make_nvar_record

    bad_dump = bytearray(dump_image)
    inject_nvars(bad_dump, [
        make_nvar_record("Setup", b"\x11" * 0x40),  # half the size of base's Setup
    ])
    buf = bytearray(base_with_nvars)
    result = bios_heal.transplant_dmi_variables(buf, bytes(bad_dump), (0x500000, 0x520000))

    assert "Setup" not in result["transplanted"]
    sizes = [r["name"] for r in result["size_mismatch"]]
    assert "Setup" in sizes


def test_transplant_skips_unknown_variable(base_with_nvars: bytes,
                                            dump_with_nvars: bytes) -> None:
    buf = bytearray(base_with_nvars)
    # Whitelist that does NOT include any of the injected vars
    result = bios_heal.transplant_dmi_variables(
        buf, dump_with_nvars, (0x500000, 0x520000),
        names=frozenset({"NonexistentVar"}),
    )
    assert result["transplanted"] == []
    assert bytes(buf) == base_with_nvars


def test_transplant_records_missing_in_base(base_image: bytes, dump_with_nvars: bytes) -> None:
    """If DUMP has the variable but BASE doesn't, transplant reports missing
    and does not modify the base buffer."""
    buf = bytearray(base_image)
    result = bios_heal.transplant_dmi_variables(buf, dump_with_nvars, (0x500000, 0x520000))
    assert "Setup" in result["missing_in_base"]
    assert bytes(buf) == base_image


def test_cli_preserve_dmi_swaps_serial(run_cli, tmp_bin, base_with_nvars: bytes,
                                        dump_with_nvars: bytes) -> None:
    base_path = tmp_bin("base.bin", base_with_nvars)
    dump_path = tmp_bin("dump.bin", dump_with_nvars)
    out_path = base_path.parent / "out.bin"
    code, out, _ = run_cli(
        str(base_path), str(dump_path), "-o", str(out_path),
        "--force", "--preserve", "me,dmi", "--no-backup",
    )
    assert code == 0
    healed = out_path.read_bytes()
    # ME from dump
    assert healed[0x1000:0x500000] == dump_with_nvars[0x1000:0x500000]
    # NVRAM region NOT bulk-copied (we passed dmi, not nvram).
    # But the three named variables ARE swapped.
    assert b"DUMP-SERIAL-9999" in healed
    assert b"BASE-SERIAL-0000" not in healed
    assert "dmi transplant" in out
