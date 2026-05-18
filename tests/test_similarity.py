"""Byte-match similarity calculator."""

from __future__ import annotations

import bios_heal


def test_similarity_identical() -> None:
    data = b"hello world" * 100
    assert bios_heal.similarity(data, data) == 1.0


def test_similarity_completely_different() -> None:
    a = b"\x00" * 1024
    b = b"\xFF" * 1024
    assert bios_heal.similarity(a, b) == 0.0


def test_similarity_half_diff() -> None:
    a = b"\x00" * 1000
    b = b"\x00" * 500 + b"\xFF" * 500
    assert bios_heal.similarity(a, b) == 0.5


def test_similarity_length_mismatch_returns_zero() -> None:
    assert bios_heal.similarity(b"abc", b"abcd") == 0.0


def test_similarity_empty_returns_zero() -> None:
    assert bios_heal.similarity(b"", b"") == 0.0


def test_per_region_similarity(base_image: bytes, dump_image: bytes) -> None:
    layout = bios_heal.parse_fd(base_image)
    assert layout is not None
    sim = bios_heal.per_region_similarity(base_image, dump_image, layout)
    # FD untouched between base/dump → exact match
    assert sim["fd"] == 1.0
    # ME differs entirely (different fill byte)
    assert sim["me"] == 0.0
    # BIOS region also differs except for the small FFSv2 volume header (0x48
    # bytes) that the synthetic builder writes identically into both images.
    assert sim["bios"] < 0.001
