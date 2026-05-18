"""Core heal logic — preserve zones from dump, restore code from base."""

from __future__ import annotations

import bios_heal


def test_heal_output_size_matches_base(base_image: bytes, dump_image: bytes) -> None:
    layout = bios_heal.parse_fd(base_image)
    healed = bios_heal.heal(base_image, dump_image, layout, {"me"})
    assert len(healed) == len(base_image)


def test_heal_preserve_me_only_keeps_me_from_dump(base_image: bytes, dump_image: bytes) -> None:
    layout = bios_heal.parse_fd(base_image)
    healed = bios_heal.heal(base_image, dump_image, layout, {"me"})
    # ME bytes match dump
    me_start, me_end = layout["me"]
    assert healed[me_start:me_end] == dump_image[me_start:me_end]
    # BIOS bytes match base (not dump)
    bios_start, bios_end = layout["bios"]
    assert healed[bios_start:bios_end] == base_image[bios_start:bios_end]


def test_heal_preserve_nothing_equals_base(base_image: bytes, dump_image: bytes) -> None:
    layout = bios_heal.parse_fd(base_image)
    healed = bios_heal.heal(base_image, dump_image, layout, set())
    assert healed == base_image


def test_heal_preserve_unknown_zone_ignored(base_image: bytes, dump_image: bytes) -> None:
    layout = bios_heal.parse_fd(base_image)
    # 'xyz' not in layout — should not crash, should not preserve anything
    healed = bios_heal.heal(base_image, dump_image, layout, {"xyz"})
    assert healed == base_image


def test_heal_multiple_preserve_zones(base_image: bytes, dump_image: bytes) -> None:
    layout = bios_heal.parse_fd(base_image)
    layout["nvram"] = (0x500000, 0x520000)
    healed = bios_heal.heal(base_image, dump_image, layout, {"me", "nvram"})

    me_start, me_end = layout["me"]
    nv_start, nv_end = layout["nvram"]
    assert healed[me_start:me_end] == dump_image[me_start:me_end]
    assert healed[nv_start:nv_end] == dump_image[nv_start:nv_end]
    # BIOS code after NVRAM came from base
    assert healed[nv_end:0x800000] == base_image[nv_end:0x800000]
