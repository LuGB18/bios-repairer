"""FFSv2 volume scanning and header checksum."""

from __future__ import annotations

import bios_heal


def test_fvh_uint16_checksum_zero_on_synthetic_header(base_image: bytes) -> None:
    # Synthetic builder writes a valid header at start of BIOS region (0x500000)
    header = base_image[0x500000:0x500048]
    assert bios_heal.fvh_uint16_checksum(header) == 0


def test_scan_ffsv2_volumes_finds_synthetic_volume(base_image: bytes) -> None:
    vols = bios_heal.scan_ffsv2_volumes(base_image, 0x500000, 0x800000)
    assert len(vols) == 1
    v = vols[0]
    assert v["offset"] == 0x500000
    assert v["length"] == 0x20000
    assert v["hdr_len"] == 0x48
    assert v["csum_ok"] is True
    assert v["guid_type"] == "FFSv2"


def test_scan_ffsv2_returns_empty_on_empty_region() -> None:
    empty = b"\xFF" * 0x10000
    assert bios_heal.scan_ffsv2_volumes(empty, 0, len(empty)) == []


def test_derive_nvram_zone_finds_first_volume(base_image: bytes) -> None:
    zone = bios_heal.derive_nvram_zone(base_image, (0x500000, 0x800000))
    assert zone == (0x500000, 0x520000)


def test_derive_nvram_zone_returns_none_when_no_volume() -> None:
    blank = b"\xFF" * 0x800000
    assert bios_heal.derive_nvram_zone(blank, (0x500000, 0x800000)) is None


def test_diff_volumes_marks_identical_when_crc_matches(base_image: bytes) -> None:
    base_vols = bios_heal.scan_ffsv2_volumes(base_image, 0x500000, 0x800000)
    rows = bios_heal.diff_volumes(base_vols, base_vols)
    assert all(r["status"] == "identical" for r in rows)


def test_diff_volumes_marks_diff(base_image: bytes, dump_image: bytes) -> None:
    base_vols = bios_heal.scan_ffsv2_volumes(base_image, 0x500000, 0x800000)
    dump_vols = bios_heal.scan_ffsv2_volumes(dump_image, 0x500000, 0x800000)
    rows = bios_heal.diff_volumes(base_vols, dump_vols)
    # Volume exists in both but bodies differ (different bios fill) → diff
    assert len(rows) == 1
    assert rows[0]["status"] == "diff"
