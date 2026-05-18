"""bios_flash subcommands with flashrom mocked.

We never touch real hardware; subprocess.run is patched to fake
flashrom's behavior. Each fake stores the args it was called with so
tests can assert the correct flashrom invocation.
"""

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


class FakeFlashrom:
    """Pluggable subprocess.run replacement that mimics flashrom -r / -w / -v."""

    def __init__(self, chip_content: bytes, write_corrupts: bool = False) -> None:
        self.chip = bytearray(chip_content)
        self.write_corrupts = write_corrupts
        self.calls: list[list[str]] = []

    def __call__(self, cmd, capture_output=True, text=True, check=False, **kw):
        self.calls.append(list(cmd))
        argv = cmd[1:]  # drop flashrom path
        # Parse minimal flashrom subset: -p prog -c chip {-r|-w|-v} file
        op = None
        target: str | None = None
        i = 0
        while i < len(argv):
            tok = argv[i]
            if tok in ("-r", "-w", "-v"):
                op = tok
                target = argv[i + 1] if i + 1 < len(argv) else None
                i += 2
                continue
            i += 1

        if op == "-r" and target:
            Path(target).write_bytes(bytes(self.chip))
            return SimpleNamespace(returncode=0, stdout="OK\n", stderr="")
        if op == "-w" and target:
            data = Path(target).read_bytes()
            self.chip = bytearray(data)
            if self.write_corrupts:
                self.chip[0] ^= 0xFF
            return SimpleNamespace(returncode=0, stdout="OK\n", stderr="")
        if op == "-v" and target:
            data = Path(target).read_bytes()
            if bytes(self.chip) == data:
                return SimpleNamespace(returncode=0, stdout="VERIFY OK\n", stderr="")
            return SimpleNamespace(returncode=1, stdout="", stderr="VERIFY MISMATCH\n")
        return SimpleNamespace(returncode=2, stdout="", stderr="unknown op\n")


@pytest.fixture
def fake_chip(base_image, monkeypatch):
    """Yield a FakeFlashrom seeded with the synthetic BASE image."""
    fake = FakeFlashrom(base_image)
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(bios_flash, "find_flashrom", lambda explicit=None: "flashrom")
    return fake


def _run(argv: list[str]) -> int:
    return bios_flash.main(argv)


def test_read_dumps_chip(tmp_path, fake_chip, base_image: bytes) -> None:
    out = tmp_path / "dump.bin"
    code = _run(["read", "-o", str(out)])
    assert code == 0
    assert out.exists()
    assert out.read_bytes() == base_image
    # flashrom was called once with -r
    assert any("-r" in c for c in fake_chip.calls)


def test_read_missing_flashrom(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(bios_flash, "find_flashrom", lambda explicit=None: None)
    out = tmp_path / "dump.bin"
    code = _run(["read", "-o", str(out)])
    assert code == bios_flash.FLASHROM_NOT_FOUND


def test_verify_match_returns_zero(tmp_path, fake_chip, base_image: bytes) -> None:
    ref = tmp_path / "ref.bin"
    ref.write_bytes(base_image)
    code = _run(["verify", "--against", str(ref)])
    assert code == 0


def test_verify_mismatch_returns_13(tmp_path, fake_chip, base_image: bytes,
                                     dump_image: bytes) -> None:
    ref = tmp_path / "ref.bin"
    ref.write_bytes(dump_image)  # different content
    code = _run(["verify", "--against", str(ref)])
    assert code == bios_flash.VERIFY_FAIL


def test_write_without_commit_refuses(tmp_path, fake_chip, base_image: bytes) -> None:
    img = tmp_path / "img.bin"
    img.write_bytes(base_image)
    code = _run(["write", "--image", str(img)])
    assert code == bios_flash.COMMIT_REQUIRED
    # flashrom never touched
    assert not any("-w" in c for c in fake_chip.calls)


def test_write_with_commit_runs_write_then_verify(tmp_path, fake_chip, base_image: bytes) -> None:
    img = tmp_path / "img.bin"
    img.write_bytes(base_image)
    code = _run(["write", "--image", str(img), "--commit"])
    assert code == 0
    ops = [next((t for t in c if t in ("-r", "-w", "-v")), None) for c in fake_chip.calls]
    # pre-write backup (-r), then write (-w), then verify (-v)
    assert "-r" in ops
    assert "-w" in ops
    assert "-v" in ops


def test_write_post_verify_fail_returns_13(tmp_path, monkeypatch, base_image: bytes) -> None:
    fake = FakeFlashrom(base_image, write_corrupts=True)
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(bios_flash, "find_flashrom", lambda explicit=None: "flashrom")
    img = tmp_path / "img.bin"
    img.write_bytes(base_image)
    code = _run(["write", "--image", str(img), "--commit", "--no-backup"])
    assert code == bios_flash.VERIFY_FAIL


def test_write_size_mismatch_returns_2(tmp_path, fake_chip, base_image: bytes) -> None:
    img = tmp_path / "img.bin"
    img.write_bytes(base_image[: 0x1000])  # smaller than chip
    code = _run(["write", "--image", str(img), "--commit"])
    assert code == 2


def test_version_flag() -> None:
    with pytest.raises(SystemExit) as exc:
        _run(["--version"])
    assert exc.value.code == 0


def test_json_output_for_read(tmp_path, fake_chip, base_image: bytes) -> None:
    import json
    out = tmp_path / "dump.bin"
    j = tmp_path / "result.json"
    _run(["read", "-o", str(out), "--json", str(j)])
    payload = json.loads(j.read_text(encoding="utf-8"))
    assert payload["operation"] == "read"
    assert payload["status"] == "ok"
    assert payload["size"] == len(base_image)
