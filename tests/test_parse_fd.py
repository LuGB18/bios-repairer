"""Flash Descriptor parsing tests."""

from __future__ import annotations

from conftest import IMAGE_SIZE, build_intel_image

import bios_heal


def test_parse_fd_returns_layout(base_image: bytes) -> None:
    layout = bios_heal.parse_fd(base_image)
    assert layout is not None
    assert layout["fd"] == (0x000000, 0x001000)
    assert layout["me"] == (0x001000, 0x500000)
    assert layout["bios"] == (0x500000, 0x800000)
    # gbe/pdr have base==0x1FFF, limit==0 → disabled, not present
    assert "gbe" not in layout
    assert "pdr" not in layout


def test_parse_fd_returns_none_when_signature_missing() -> None:
    img = bytearray([0xFF]) * IMAGE_SIZE
    # No FD signature anywhere
    assert bios_heal.parse_fd(bytes(img)) is None


def test_parse_fd_returns_none_when_signature_wrong() -> None:
    img = bytearray([0xFF]) * IMAGE_SIZE
    img[0x10:0x14] = b"\xDE\xAD\xBE\xEF"
    assert bios_heal.parse_fd(bytes(img)) is None


def test_parse_fd_handles_custom_region_map() -> None:
    custom = {
        "fd":   (0x000, 0x000),
        "bios": (0x600, 0x7FF),  # smaller BIOS
        "me":   (0x001, 0x5FF),  # bigger ME
        "gbe":  (0x1FFF, 0x000),
        "pdr":  (0x1FFF, 0x000),
    }
    img = build_intel_image(regions=custom)
    layout = bios_heal.parse_fd(img)
    assert layout is not None
    assert layout["me"] == (0x001000, 0x600000)
    assert layout["bios"] == (0x600000, 0x800000)
