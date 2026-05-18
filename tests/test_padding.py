"""Padding run detection."""

from __future__ import annotations

import bios_heal


def test_detect_padding_empty() -> None:
    assert bios_heal.detect_padding(b"") == []


def test_detect_padding_no_runs() -> None:
    assert bios_heal.detect_padding(b"\x00" * 1024, min_run=256) == []


def test_detect_padding_single_run() -> None:
    data = b"\x00" * 100 + b"\xFF" * 300 + b"\x00" * 50
    runs = bios_heal.detect_padding(data, min_run=256)
    assert runs == [(100, 400)]


def test_detect_padding_exact_min_run() -> None:
    data = b"\xFF" * 256
    assert bios_heal.detect_padding(data, min_run=256) == [(0, 256)]


def test_detect_padding_just_below_min_run() -> None:
    data = b"\xFF" * 255
    assert bios_heal.detect_padding(data, min_run=256) == []


def test_detect_padding_multiple_runs() -> None:
    data = (
        b"\xFF" * 300
        + b"\x00" * 10
        + b"\xFF" * 500
        + b"\x00"
        + b"\xFF" * 100  # below threshold, ignored
    )
    runs = bios_heal.detect_padding(data, min_run=256)
    assert runs == [(0, 300), (310, 810)]


def test_detect_padding_at_end_of_file() -> None:
    data = b"\x00" * 50 + b"\xFF" * 400
    runs = bios_heal.detect_padding(data, min_run=256)
    assert runs == [(50, 450)]
