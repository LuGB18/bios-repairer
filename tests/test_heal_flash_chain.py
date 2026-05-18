"""End-to-end heal-flash chain with flashrom mocked."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bios_flash  # noqa: E402
from tests.test_flashrom import FakeFlashrom  # reuse


@pytest.fixture
def flashrom_returning_dump(dump_image, monkeypatch):
    """Pretend the chip currently holds the DUMP image (so heal has work to do)."""
    fake = FakeFlashrom(dump_image)
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(bios_flash, "find_flashrom", lambda explicit=None: "flashrom")
    return fake


def _run(argv: list[str]) -> int:
    return bios_flash.main(argv)


def test_heal_flash_dry_run_no_commit(tmp_path, flashrom_returning_dump,
                                       base_image: bytes) -> None:
    base_path = tmp_path / "base.bin"
    base_path.write_bytes(base_image)
    code = _run([
        "heal-flash",
        "--base", str(base_path),
        "--workdir", str(tmp_path),
        "--force", "--no-backup",
    ])
    assert code == 0
    # Chip dumps exist
    dumps = list(tmp_path.glob("chip_dump_*.bin"))
    assert len(dumps) >= 2
    healed = list(tmp_path.glob("healed_*.bin"))
    assert len(healed) == 1
    # No -w call without --commit
    ops = [t for c in flashrom_returning_dump.calls for t in c if t in ("-r", "-w", "-v")]
    assert "-w" not in ops


def test_heal_flash_commit_writes_and_verifies(tmp_path, flashrom_returning_dump,
                                                 base_image: bytes) -> None:
    base_path = tmp_path / "base.bin"
    base_path.write_bytes(base_image)
    code = _run([
        "heal-flash",
        "--base", str(base_path),
        "--workdir", str(tmp_path),
        "--force", "--no-backup", "--commit",
    ])
    assert code == 0
    ops = [t for c in flashrom_returning_dump.calls for t in c if t in ("-r", "-w", "-v")]
    # Read x2 (consistency), write x1, verify x1
    assert ops.count("-r") == 2
    assert ops.count("-w") == 1
    assert ops.count("-v") == 1


def test_heal_flash_two_reads_disagree_returns_12(tmp_path, monkeypatch,
                                                    base_image: bytes,
                                                    dump_image: bytes) -> None:
    """Make the FakeFlashrom return different bytes on each read."""
    state = {"first": True}

    def alternating_run(cmd, capture_output=True, text=True, check=False, **kw):
        argv = cmd[1:]
        if "-r" in argv:
            target = argv[argv.index("-r") + 1]
            content = base_image if state["first"] else dump_image
            state["first"] = False
            Path(target).write_bytes(content)
            return SimpleNamespace(returncode=0, stdout="OK", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", alternating_run)
    monkeypatch.setattr(bios_flash, "find_flashrom", lambda explicit=None: "flashrom")

    base_path = tmp_path / "base.bin"
    base_path.write_bytes(base_image)
    code = _run([
        "heal-flash",
        "--base", str(base_path),
        "--workdir", str(tmp_path),
        "--force", "--no-backup",
    ])
    assert code == bios_flash.READ_INCONSISTENT


def test_heal_flash_propagates_heal_exit_code(tmp_path, monkeypatch,
                                                base_image: bytes) -> None:
    """If the heal step aborts (e.g., size mismatch), the chain returns that
    code, not 0."""
    # Chip returns base_image, but base file is the wrong size → heal exits 2
    fake = FakeFlashrom(base_image)
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(bios_flash, "find_flashrom", lambda explicit=None: "flashrom")

    base_path = tmp_path / "base.bin"
    base_path.write_bytes(base_image[: 0x1000])  # wrong size

    code = _run([
        "heal-flash",
        "--base", str(base_path),
        "--workdir", str(tmp_path),
        "--no-backup",
    ])
    assert code == 2  # propagated from bios_heal


def test_heal_flash_json_output(tmp_path, flashrom_returning_dump,
                                 base_image: bytes) -> None:
    import json
    base_path = tmp_path / "base.bin"
    base_path.write_bytes(base_image)
    j = tmp_path / "result.json"
    _run([
        "heal-flash",
        "--base", str(base_path),
        "--workdir", str(tmp_path),
        "--force", "--no-backup",
        "--json", str(j),
    ])
    payload = json.loads(j.read_text(encoding="utf-8"))
    assert payload["operation"] == "heal-flash"
    assert payload["status"] == "dry-run"
