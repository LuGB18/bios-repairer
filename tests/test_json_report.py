"""JSON report mode — schema stability, content cross-check."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "bios_heal.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )


def test_json_flag_creates_json_report(tmp_bin, base_image: bytes, dump_image: bytes) -> None:
    base_path = tmp_bin("base.bin", base_image)
    dump_path = tmp_bin("dump.bin", dump_image)
    out_path = base_path.parent / "out.bin"
    cp = _run(str(base_path), str(dump_path), "-o", str(out_path), "--force", "--json", "--no-backup")
    assert cp.returncode == 0
    json_path = base_path.parent / "out.bin.report.json"
    assert json_path.exists()
    txt_path = base_path.parent / "out.bin.report.txt"
    assert txt_path.exists()  # text report still written alongside


def test_json_schema_has_required_top_keys(tmp_bin, base_image: bytes, dump_image: bytes) -> None:
    base_path = tmp_bin("base.bin", base_image)
    dump_path = tmp_bin("dump.bin", dump_image)
    out_path = base_path.parent / "out.bin"
    _run(str(base_path), str(dump_path), "-o", str(out_path), "--force", "--json", "--no-backup")
    payload = json.loads((base_path.parent / "out.bin.report.json").read_text(encoding="utf-8"))

    required = {
        "schema_version", "tool_version", "timestamp", "files", "mode",
        "layout", "preserve", "similarity", "decision", "volumes",
        "padding", "diff",
    }
    assert required.issubset(payload.keys())
    assert payload["schema_version"] == 1


def test_json_layout_contains_regions(tmp_bin, base_image: bytes, dump_image: bytes) -> None:
    base_path = tmp_bin("base.bin", base_image)
    dump_path = tmp_bin("dump.bin", dump_image)
    out_path = base_path.parent / "out.bin"
    _run(str(base_path), str(dump_path), "-o", str(out_path), "--force", "--json", "--no-backup")
    payload = json.loads((base_path.parent / "out.bin.report.json").read_text(encoding="utf-8"))

    assert "fd" in payload["layout"]
    assert "me" in payload["layout"]
    assert "bios" in payload["layout"]
    me = payload["layout"]["me"]
    assert me["start"] == 0x1000
    assert me["end"] == 0x500000
    assert me["preserved"] is True
    assert isinstance(me["similarity"], float)


def test_json_volumes_match_text_report(tmp_bin, base_image: bytes, dump_image: bytes) -> None:
    base_path = tmp_bin("base.bin", base_image)
    dump_path = tmp_bin("dump.bin", dump_image)
    out_path = base_path.parent / "out.bin"
    _run(str(base_path), str(dump_path), "-o", str(out_path), "--force", "--json", "--no-backup")
    payload = json.loads((base_path.parent / "out.bin.report.json").read_text(encoding="utf-8"))

    vols = payload["volumes"]
    assert len(vols) == 1
    v = vols[0]
    assert v["offset"] == 0x500000
    assert v["status"] == "diff"
    assert v["header_checksum_ok"] is True
    assert v["guid_type"] == "FFSv2"


def test_json_diff_in_preserve_is_zero(tmp_bin, base_image: bytes, dump_image: bytes) -> None:
    base_path = tmp_bin("base.bin", base_image)
    dump_path = tmp_bin("dump.bin", dump_image)
    out_path = base_path.parent / "out.bin"
    _run(str(base_path), str(dump_path), "-o", str(out_path),
         "--force", "--json", "--preserve", "me", "--no-backup")
    payload = json.loads((base_path.parent / "out.bin.report.json").read_text(encoding="utf-8"))

    assert payload["diff"]["in_preserve"] == 0
    assert payload["diff"]["outside"] > 0


def test_json_omitted_when_flag_absent(tmp_bin, base_image: bytes, dump_image: bytes) -> None:
    base_path = tmp_bin("base.bin", base_image)
    dump_path = tmp_bin("dump.bin", dump_image)
    out_path = base_path.parent / "out.bin"
    cp = _run(str(base_path), str(dump_path), "-o", str(out_path), "--force", "--no-backup")
    assert cp.returncode == 0
    json_path = base_path.parent / "out.bin.report.json"
    assert not json_path.exists()


def test_json_works_with_dry_run(tmp_bin, base_image: bytes, dump_image: bytes) -> None:
    base_path = tmp_bin("base.bin", base_image)
    dump_path = tmp_bin("dump.bin", dump_image)
    out_path = base_path.parent / "out.bin"
    cp = _run(str(base_path), str(dump_path), "-o", str(out_path),
              "--dry-run", "--force", "--json")
    assert cp.returncode == 0
    assert not out_path.exists()  # dry-run skips .bin
    assert (base_path.parent / "out.bin.report.json").exists()
    assert (base_path.parent / "out.bin.report.txt").exists()


def test_version_flag() -> None:
    cp = _run("--version")
    assert cp.returncode == 0
    assert "bios_heal" in cp.stdout
