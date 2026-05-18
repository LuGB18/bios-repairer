"""JSON report mode — schema stability, content cross-check.

Uses run_cli (in-process main) so write_json_report is exercised under
coverage instrumentation.
"""

from __future__ import annotations

import json


def test_json_flag_creates_json_report(run_cli, tmp_bin, base_image: bytes, dump_image: bytes) -> None:
    base_path = tmp_bin("base.bin", base_image)
    dump_path = tmp_bin("dump.bin", dump_image)
    out_path = base_path.parent / "out.bin"
    code, _, _ = run_cli(str(base_path), str(dump_path), "-o", str(out_path),
                         "--force", "--json", "--no-backup")
    assert code == 0
    assert (base_path.parent / "out.bin.report.json").exists()
    assert (base_path.parent / "out.bin.report.txt").exists()


def test_json_schema_has_required_top_keys(run_cli, tmp_bin, base_image: bytes, dump_image: bytes) -> None:
    base_path = tmp_bin("base.bin", base_image)
    dump_path = tmp_bin("dump.bin", dump_image)
    out_path = base_path.parent / "out.bin"
    run_cli(str(base_path), str(dump_path), "-o", str(out_path), "--force", "--json", "--no-backup")
    payload = json.loads((base_path.parent / "out.bin.report.json").read_text(encoding="utf-8"))

    required = {
        "schema_version", "tool_version", "timestamp", "files", "mode",
        "layout", "preserve", "similarity", "decision", "volumes",
        "padding", "diff",
    }
    assert required.issubset(payload.keys())
    assert payload["schema_version"] == 1


def test_json_layout_contains_regions(run_cli, tmp_bin, base_image: bytes, dump_image: bytes) -> None:
    base_path = tmp_bin("base.bin", base_image)
    dump_path = tmp_bin("dump.bin", dump_image)
    out_path = base_path.parent / "out.bin"
    run_cli(str(base_path), str(dump_path), "-o", str(out_path), "--force", "--json", "--no-backup")
    payload = json.loads((base_path.parent / "out.bin.report.json").read_text(encoding="utf-8"))

    assert "fd" in payload["layout"]
    assert "me" in payload["layout"]
    assert "bios" in payload["layout"]
    me = payload["layout"]["me"]
    assert me["start"] == 0x1000
    assert me["end"] == 0x500000
    assert me["preserved"] is True
    assert isinstance(me["similarity"], float)


def test_json_volumes_match_text_report(run_cli, tmp_bin, base_image: bytes, dump_image: bytes) -> None:
    base_path = tmp_bin("base.bin", base_image)
    dump_path = tmp_bin("dump.bin", dump_image)
    out_path = base_path.parent / "out.bin"
    run_cli(str(base_path), str(dump_path), "-o", str(out_path), "--force", "--json", "--no-backup")
    payload = json.loads((base_path.parent / "out.bin.report.json").read_text(encoding="utf-8"))

    vols = payload["volumes"]
    assert len(vols) == 1
    v = vols[0]
    assert v["offset"] == 0x500000
    assert v["status"] == "diff"
    assert v["header_checksum_ok"] is True
    assert v["guid_type"] == "FFSv2"


def test_json_diff_in_preserve_is_zero(run_cli, tmp_bin, base_image: bytes, dump_image: bytes) -> None:
    base_path = tmp_bin("base.bin", base_image)
    dump_path = tmp_bin("dump.bin", dump_image)
    out_path = base_path.parent / "out.bin"
    run_cli(str(base_path), str(dump_path), "-o", str(out_path),
            "--force", "--json", "--preserve", "me", "--no-backup")
    payload = json.loads((base_path.parent / "out.bin.report.json").read_text(encoding="utf-8"))

    assert payload["diff"]["in_preserve"] == 0
    assert payload["diff"]["outside"] > 0


def test_json_omitted_when_flag_absent(run_cli, tmp_bin, base_image: bytes, dump_image: bytes) -> None:
    base_path = tmp_bin("base.bin", base_image)
    dump_path = tmp_bin("dump.bin", dump_image)
    out_path = base_path.parent / "out.bin"
    code, _, _ = run_cli(str(base_path), str(dump_path), "-o", str(out_path), "--force", "--no-backup")
    assert code == 0
    assert not (base_path.parent / "out.bin.report.json").exists()


def test_json_works_with_dry_run(run_cli, tmp_bin, base_image: bytes, dump_image: bytes) -> None:
    base_path = tmp_bin("base.bin", base_image)
    dump_path = tmp_bin("dump.bin", dump_image)
    out_path = base_path.parent / "out.bin"
    code, _, _ = run_cli(str(base_path), str(dump_path), "-o", str(out_path),
                         "--dry-run", "--force", "--json")
    assert code == 0
    assert not out_path.exists()
    assert (base_path.parent / "out.bin.report.json").exists()
    assert (base_path.parent / "out.bin.report.txt").exists()


def test_version_flag(run_cli) -> None:
    code, out, _ = run_cli("--version")
    assert code == 0
    assert "bios_heal" in out
