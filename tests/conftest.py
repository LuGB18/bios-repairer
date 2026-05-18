"""Shared fixtures and synthetic SPI-image builders.

These builders produce in-memory bytes that LOOK like an Intel-descriptor
SPI flash image, with valid FD signatures, region maps, and minimally
valid FFSv2 volumes. They contain NO real firmware — all bodies are
deterministic synthetic fill, safe to commit and share.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

# Make the project root importable so `import bios_heal` works from tests/
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

IMAGE_SIZE = 0x800000  # 8 MiB, matches W25Q64BV

# Default region map mirroring ASRock B75M-DGS (4 KiB-aligned, in 4 KiB units)
DEFAULT_REGIONS = {
    "fd":   (0x000, 0x000),  # 4 KiB
    "bios": (0x500, 0x7FF),  # 0x500000-0x7FFFFF
    "me":   (0x001, 0x4FF),  # 0x001000-0x4FFFFF
    "gbe":  (0x1FFF, 0x000),  # disabled
    "pdr":  (0x1FFF, 0x000),  # disabled
}

FFS_V2_GUID = bytes.fromhex("78E58C8C3D8A1C4F99358961") + b"\x85\xC3\x2D\xD3"


def _flreg(base_4k: int, limit_4k: int) -> bytes:
    word = (base_4k & 0xFFF) | ((limit_4k & 0xFFF) << 16)
    return struct.pack("<I", word)


def build_intel_image(
    regions: dict[str, tuple[int, int]] | None = None,
    fill: int = 0xFF,
    bios_fill: int | None = None,
    me_fill: int | None = None,
) -> bytes:
    """Build an 8 MiB synthetic Intel-FD image with the given region map."""
    regions = regions if regions is not None else DEFAULT_REGIONS
    buf = bytearray([fill]) * IMAGE_SIZE

    # FD signature at 0x10
    buf[0x10:0x14] = b"\x5A\xA5\xF0\x0F"
    # FLMAP0: NR=3 (4 regions), FRBA=0x40, NC=0, FCBA=0x30
    buf[0x14:0x18] = b"\x03\x00\x04\x02"
    buf[0x18:0x1C] = b"\x06\x02\x10\x12"
    buf[0x1C:0x20] = b"\x20\x01\x21\x00"

    # FRBA at 0x40 — 5 region descriptors
    frba = 0x40
    names = ["fd", "bios", "me", "gbe", "pdr"]
    for i, name in enumerate(names):
        base, limit = regions[name]
        buf[frba + i * 4: frba + i * 4 + 4] = _flreg(base, limit)

    # Fill ME with deterministic non-FF pattern so similarity tests are stable
    me_lo, me_hi = regions["me"]
    if me_hi > me_lo:
        me_start = me_lo * 0x1000
        me_end = (me_hi + 1) * 0x1000
        me_fill_byte = me_fill if me_fill is not None else 0xA5
        buf[me_start:me_end] = bytes([me_fill_byte]) * (me_end - me_start)

    # Fill BIOS region with deterministic non-FF pattern
    bios_lo, bios_hi = regions["bios"]
    if bios_hi > bios_lo:
        bios_start = bios_lo * 0x1000
        bios_end = (bios_hi + 1) * 0x1000
        bios_fill_byte = bios_fill if bios_fill is not None else 0xC3
        buf[bios_start:bios_end] = bytes([bios_fill_byte]) * (bios_end - bios_start)

        # Inject a minimal FFSv2 volume header at bios_start
        vol_len = 0x20000  # 128 KiB
        hdr_len = 0x48
        _write_ffsv2_header(buf, bios_start, vol_len, hdr_len)

    return bytes(buf)


def _write_ffsv2_header(buf: bytearray, offset: int, vol_len: int, hdr_len: int) -> None:
    """Inject a minimal FFSv2 volume header whose UINT16 checksum sums to 0."""
    # ZeroVector (16 bytes of 0)
    buf[offset:offset + 0x10] = b"\x00" * 0x10
    # FileSystemGuid
    buf[offset + 0x10:offset + 0x20] = FFS_V2_GUID
    # FvLength (8 bytes LE)
    buf[offset + 0x20:offset + 0x28] = struct.pack("<Q", vol_len)
    # Signature "_FVH"
    buf[offset + 0x28:offset + 0x2C] = b"_FVH"
    # Attributes (4 bytes)
    buf[offset + 0x2C:offset + 0x30] = struct.pack("<I", 0x0004FEFF)
    # HeaderLength (UINT16)
    buf[offset + 0x30:offset + 0x32] = struct.pack("<H", hdr_len)
    # Checksum placeholder (UINT16) — fixed below
    buf[offset + 0x32:offset + 0x34] = b"\x00\x00"
    # ExtHeaderOffset, Reserved, Revision
    buf[offset + 0x34:offset + 0x36] = b"\x00\x00"
    buf[offset + 0x36] = 0x00
    buf[offset + 0x37] = 0x02
    # BlockMap[0] = (NumBlocks=vol_len/4K, BlockLength=4K)
    buf[offset + 0x38:offset + 0x40] = struct.pack("<II", vol_len // 0x1000, 0x1000)
    # BlockMap terminator
    buf[offset + 0x40:offset + 0x48] = b"\x00" * 8

    # Compute UINT16 checksum so total mod 0x10000 = 0
    total = 0
    for i in range(offset, offset + hdr_len, 2):
        total = (total + int.from_bytes(buf[i:i + 2], "little")) & 0xFFFF
    csum = (-total) & 0xFFFF
    buf[offset + 0x32:offset + 0x34] = struct.pack("<H", csum)


@pytest.fixture
def base_image() -> bytes:
    """A clean synthetic BASE image."""
    return build_intel_image()


@pytest.fixture
def dump_image() -> bytes:
    """A 'board dump' — BASE with ME and BIOS regions filled with a different
    pattern, so per-region diff is observable."""
    return build_intel_image(me_fill=0x55, bios_fill=0x3C)


@pytest.fixture
def tmp_bin(tmp_path):
    """Factory for writing an in-memory image to a tmp file."""
    def _write(name: str, data: bytes) -> Path:
        p = tmp_path / name
        p.write_bytes(data)
        return p
    return _write
