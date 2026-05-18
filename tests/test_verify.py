"""--verify-dump: two-dump consistency check."""

from __future__ import annotations


def test_verify_matching_dumps_proceeds(run_cli, tmp_bin, base_image: bytes, dump_image: bytes) -> None:
    base_path = tmp_bin("base.bin", base_image)
    dump_path = tmp_bin("dump.bin", dump_image)
    verify_path = tmp_bin("dump2.bin", dump_image)  # byte-identical second dump
    out_path = base_path.parent / "out.bin"
    code, out, _ = run_cli(
        str(base_path), str(dump_path), "-o", str(out_path),
        "--verify-dump", str(verify_path), "--force", "--no-backup",
    )
    assert code == 0
    assert "verify: OK" in out


def test_verify_differing_dumps_returns_4(run_cli, tmp_bin, base_image: bytes,
                                           dump_image: bytes) -> None:
    base_path = tmp_bin("base.bin", base_image)
    dump_path = tmp_bin("dump.bin", dump_image)
    # flip one byte deep inside the BIOS region
    bad = bytearray(dump_image)
    bad[0x600000] ^= 0xFF
    verify_path = tmp_bin("dump2.bin", bytes(bad))
    out_path = base_path.parent / "out.bin"
    code, _, err = run_cli(
        str(base_path), str(dump_path), "-o", str(out_path),
        "--verify-dump", str(verify_path), "--force",
    )
    assert code == 4
    assert "verify-dump differs" in err
    assert not out_path.exists()


def test_verify_size_mismatch_returns_2(run_cli, tmp_bin, base_image: bytes,
                                         dump_image: bytes) -> None:
    base_path = tmp_bin("base.bin", base_image)
    dump_path = tmp_bin("dump.bin", dump_image)
    verify_path = tmp_bin("short.bin", dump_image[: 0x1000])
    out_path = base_path.parent / "out.bin"
    code, _, err = run_cli(
        str(base_path), str(dump_path), "-o", str(out_path),
        "--verify-dump", str(verify_path), "--force",
    )
    assert code == 2
    assert "verify-dump size mismatch" in err


def test_verify_status_appears_in_report(run_cli, tmp_bin, base_image: bytes,
                                          dump_image: bytes) -> None:
    base_path = tmp_bin("base.bin", base_image)
    dump_path = tmp_bin("dump.bin", dump_image)
    verify_path = tmp_bin("dump2.bin", dump_image)
    out_path = base_path.parent / "out.bin"
    run_cli(
        str(base_path), str(dump_path), "-o", str(out_path),
        "--verify-dump", str(verify_path), "--force", "--no-backup", "--json",
    )
    import json
    payload = json.loads((base_path.parent / "out.bin.report.json").read_text(encoding="utf-8"))
    assert payload["verify_dump"]["status"] == "ok"
