# bios-repairer

[![tests](https://github.com/LuGB18/bios-repairer/actions/workflows/test.yml/badge.svg)](https://github.com/LuGB18/bios-repairer/actions/workflows/test.yml)
[![codecov](https://codecov.io/github/LuGB18/bios-repairer/graph/badge.svg?token=V0843PTOC2)](https://codecov.io/github/LuGB18/bios-repairer)
[![ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/downloads/)
[![license](https://img.shields.io/github/license/LuGB18/bios-repairer)](LICENSE)

> Repair corrupted dumps of a BIOS with a base BIOS. Keeps your unique board secrets.

`bios_heal.py` — SPI BIOS dump healer for Intel-descriptor flash images
(8 MiB / W25Q64BV class). Tested platform: ASRock B75M-DGS (Ivy Bridge,
AMI Aptio IV).

---

## What it does

You have two BIOS images:

- **BASE** — a clean, known-good reference (factory ROM or a modded ROM
  you trust). Source of *healing bytes*.
- **DUMP** — an SPI dump from the real board. May be corrupted in some
  areas but contains the board's unique secrets: serial number, UUID,
  NIC MAC, Intel ME fuse/config. Source of *preserved bytes*.

`bios_heal.py` merges them into a single output `.bin`:

```
output = BASE everywhere
       EXCEPT the "preserve zones", which are copied verbatim from DUMP.
```

It writes a human-readable `<output>.report.txt` next to the output,
showing:

- Region layout parsed from the Intel Flash Descriptor (FD)
- Per-region similarity (DUMP vs BASE)
- Every FFSv2 volume in the BIOS region, with CRC32 from each side
- Padding (`0xFF`) runs gained/lost by the heal
- Byte-diff totals (must be 0 inside preserve zones)

## When to use

- SPI dump shows random corruption in code areas but you still trust the
  ME region and the board-specific NVRAM.
- You want to flash a modded BIOS image but keep the board's original
  serial / UUID / MAC / MEBx config.
- You want a forensic diff report between two ROMs without flashing
  anything (use `--dry-run`).

**Not** a substitute for:

- [UEFITool](https://github.com/LongSoft/UEFITool) — visual structure inspection, GUID editing
- [MEAnalyzer](https://github.com/platomav/MEAnalyzer) — Intel ME firmware sanity / version check
- `flashrom` / CH341A — actually programming the SPI chip

## Requirements

- Python 3.10+ (uses PEP 604 union syntax `X | None`)
- No third-party packages — stdlib only (`argparse`, `hashlib`, `zlib`, `shutil`)

## Quick start

1. Dump your board twice with a CH341A and verify the two dumps match
   (md5 on both — they MUST be identical, otherwise re-dump).
2. Obtain a clean BASE of the same size (8 MiB for B75M-DGS / W25Q64BV).
   Factory ROM padded to full size, or a known-good modded ROM.
3. Dry-run first — read the report without writing anything:
   ```sh
   python bios_heal.py BASE.bin DUMP.bin -o OUT.bin --dry-run
   ```
4. Read `OUT.bin.report.txt`. Confirm:
   - Global similarity is reasonable (>80% normal, >90% ideal)
   - FFSv2 volume table shows volumes you expect
   - Per-region similarity looks sane
5. Run the real heal:
   ```sh
   python bios_heal.py BASE.bin DUMP.bin -o OUT.bin
   ```
6. Flash `OUT.bin` with CH341A + AsProgrammer / NeoProgrammer
   (chip: W25Q64BV). Always **Erase → Write → Verify**.

## Arguments

### Positional

| Arg | Purpose |
|---|---|
| `BASE` | clean reference BIOS image (`.bin`) — source of healing bytes |
| `DUMP` | corrupted/board-specific dump (`.bin`) — source of preserved zones |

### Required

| Flag | Purpose |
|---|---|
| `-o, --output FILE` | output `.bin` path. A sibling `FILE.report.txt` is also written |

### Tuning

| Flag | Default | Purpose |
|---|---|---|
| `--threshold FLOAT` | `0.90` | min global byte-similarity to apply heal. Below this, DUMP copied unchanged (unless `--force`) |
| `--preserve LIST` | `me,nvram` | comma-separated zones copied verbatim from DUMP. Choices: `fd,me,bios,gbe,pdr,nvram` |
| `--padding-min N` | `256` | min consecutive `0xFF` bytes counted as a padding run |

### Mode flags

| Flag | Purpose |
|---|---|
| `--force` | apply heal even when similarity below `--threshold` |
| `--dry-run` | compute the `.report.txt` only; never write the `.bin` |
| `--no-backup` | skip the automatic `<dump>.bak` copy |
| `--json` | also emit a machine-readable `<output>.report.json` (stable schema) |
| `--version` | print tool version and exit |

## Region layout (auto-detected from Intel FD)

For ASRock B75M-DGS / W25Q64BV the FD declares:

| Offset | Region | Length | Heal action |
|---|---|---|---|
| `0x000000` | Descriptor | 4 KiB | heal from BASE |
| `0x001000` | ME | ~5 MiB | **PRESERVED from DUMP** (board fuse / MEBx) |
| `0x500000` | NVRAM volume | 128 KiB | **PRESERVED from DUMP** (serial / UUID / MAC) |
| `0x520000` | BIOS code | ~3 MiB | heal from BASE (DXE / PEI / BB) |

The NVRAM zone is *not* in the FD — it is auto-derived by scanning the
BIOS region for the first FFSv2 volume header signature (`_FVH`,
GUID `8C8CE578-8A3D-4F1C-9935-896185C32DD3`).

## Report file format

`OUTPUT.bin.report.txt` sections:

| Section | Content |
|---|---|
| Layout | per-region size + similarity, `[PRESERVED]` tag |
| Global similarity | overall byte-match ratio + threshold + decision |
| FFSv2 volumes | offset, length, base CRC32, dump CRC32, header check, status |
| Padding runs | counts in base / dump / healed, lost & gained sets |
| Diff summary | bytes changed vs dump; preserve-zone diff (must be 0) |

### JSON report (`--json`)

When `--json` is passed, a sibling `OUTPUT.bin.report.json` is written
with the same data in machine-readable form. Top-level schema:

```json
{
  "schema_version": 1,
  "tool_version": "1.2.0",
  "timestamp": "2026-05-18T15:30:00",
  "files":  { "base": {...}, "dump": {...}, "output": {...} },
  "mode":   { "dry_run": false, "force": true },
  "layout": { "fd": { "start": 0, "end": 4096, "length": 4096,
                       "similarity": 0.9963, "preserved": false }, ... },
  "preserve": ["me", "nvram"],
  "similarity": { "global": 0.8551, "threshold": 0.9 },
  "decision": "heal applied (forced)",
  "volumes":  [{ "offset": 5242880, "length": 131072,
                  "base_crc32": "2EA34BBC", "dump_crc32": "FF2EA259",
                  "header_checksum_ok": true, "guid_type": "FFSv2",
                  "status": "diff" }, ...],
  "padding":  { "min_run": 256, "base_runs": 72, "dump_runs": 56,
                "healed_runs": 55,
                "lost":   [{ "start": 17603, "end": 32768, "length": 15165 }, ...],
                "gained": [...] },
  "diff":     { "total_bytes": 791288, "in_preserve": 0,
                "outside": 791288, "percent": 9.4329 }
}
```

Schema is versioned via `schema_version`. Field semantics are stable
within a major bump.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | heal applied successfully (output written) |
| `1` | similarity below threshold and `--force` not set (output = dump unchanged), or `--dry-run` completed without heal |
| `2` | base and dump have different sizes |
| `3` | output size sanity check failed |

## Common scenarios

**Standard heal, modded BIOS into healthy board dump**
```sh
python bios_heal.py modded.bin board_dump.bin -o final.bin
```

**Forensic comparison only, no write**
```sh
python bios_heal.py clean.bin dump.bin -o stub.bin --dry-run
```

**Heavily corrupted dump, override threshold**
```sh
python bios_heal.py clean.bin dump.bin -o out.bin --force --threshold 0.5
```

**Preserve NIC MAC if board uses Intel GbE region**
```sh
python bios_heal.py clean.bin dump.bin -o out.bin \
    --preserve me,nvram,gbe
```

**Quiet padding output (only count runs ≥ 4 KiB)**
```sh
python bios_heal.py clean.bin dump.bin -o out.bin --padding-min 4096
```

## Safety notes

- Always keep `DUMP.bak` (the script makes one automatically). If the
  healed image bricks the board, restore the original dump.
- **Do NOT flash a full 8 MiB image** through the motherboard's
  "Instant Flash" / EFI flash utility — the FD+ME regions are protected
  and the BIOS-only path will refuse or corrupt. Use an external CH341A
  programmer with the chip out of socket (or chip-clip with PSU off).
- If similarity is suspiciously low (< 50%), you probably picked the
  wrong BASE. Different vendors / BIOS versions for the same chipset
  are not interchangeable.
- The heal does **NOT** validate the ME region's internal FPT/FTPR/NFTP
  checksums. If your ME is corrupted, use MEAnalyzer + the matching
  clean ME firmware (Intel ME 8.x consumer for B75) to fix it
  separately, then heal.

## Files touched

Created or overwritten:

- `OUTPUT.bin`
- `OUTPUT.bin.report.txt`
- `DUMP.bin.bak` (only if it does not already exist — never overwritten)

## Troubleshooting

| Message | Cause |
|---|---|
| `no Intel FD in base — using DEFAULT_LAYOUT` | base has no FD signature `5A A5 F0 0F` @ `0x10`. Either BIOS-region-only image or non-Intel platform. Provide a full 8 MiB Intel image. |
| `size mismatch: base=X dump=Y` | files differ in length. Re-dump or pad the base to match SPI chip capacity. |
| `below threshold — copying dump unchanged` | the two images differ significantly. Inspect the report; lower `--threshold` or pick a different BASE. |

## Development

```sh
# install dev deps (pytest, pytest-cov, ruff)
python -m pip install -e ".[dev]"

# run tests
pytest

# run tests with coverage
pytest --cov=bios_heal --cov-report=term-missing

# lint
ruff check bios_heal.py tests/
```

The test suite uses **synthetic** SPI images built in-memory by
`tests/conftest.py`. No real firmware, no board-specific data, no
sensitive content is committed to the repository.

## License

[MIT](LICENSE) © 2026 Luan Bogo
