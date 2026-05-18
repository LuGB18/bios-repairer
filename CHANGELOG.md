# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.0] — 2026-05-18

### Added

- **`--preserve dmi`** — surgical NVAR-record-level transplant. Walks the
  AMI NVAR store inside the NVRAM volume and only swaps records whose
  variable name is in `DMI_VARIABLE_NAMES` (Setup, SystemSerialNumber,
  SystemUuid, SystemSKU, BoardSerialNumber, ChassisSerialNumber,
  MemoryTypeInformation, PreviousMemoryTypeInformation, AMITSESetup,
  PlatformLang, SmbiosData, DmiData). Records of mismatched length are
  skipped to keep the NVAR chain intact, and the skip list is reported.
  Typical use: `--preserve me,dmi`.
- **`--verify-dump FILE`** — second independent dump of the same board.
  Compared by md5 before any heal runs. Mismatch aborts with **exit
  code 4** and prints the first 8 differing offsets so the user can spot
  a flaky chip-clip read.
- **`--smoke`** — post-heal, pre-write structural re-parse. Asserts the
  healed buffer still has the Flash Descriptor signature and that every
  FFSv2 volume present in BASE is at the same offset and length in the
  healed image. Failure aborts with **exit code 5**; the `.bin` is NOT
  written, but the `.report.txt` (and `.report.json` if requested) ARE
  written for forensics.
- **Prebuilt binaries** in every GitHub Release — Windows `bios_heal.exe`
  and a Linux ELF `bios_heal`, built by `.github/workflows/release.yml`
  via PyInstaller on `windows-latest` and `ubuntu-latest` runners.
- New module-level constants `DMI_VARIABLE_NAMES`, `KNOWN_ZONES`,
  `NVAR_SIG`.
- New module-level functions `find_nvar_entries`,
  `transplant_dmi_variables`, `verify_dump`, `smoke_test`.
- JSON report schema (schema_version still `1`) now includes optional
  top-level keys `verify_dump`, `dmi_transplant`, `smoke`.
- 17 new tests (`test_dmi.py`, `test_verify.py`, `test_smoke.py`)
  covering the new features. Total 63 tests, all passing.

### Changed

- Exit-code table extended with codes 4 and 5. Codes 0–3 unchanged.
- `unknown preserve zones` check now uses an explicit `KNOWN_ZONES` set
  rather than the FD-derived layout, so `dmi` is not flagged as unknown
  on a typical Intel-FD image.

### Fixed

- `heal()` now skips zone name `"dmi"` (it operates at variable
  granularity post-heal, not at region granularity).

## [1.2.0] — 2026-05-18

### Added

- `--json` flag emits a machine-readable `<output>.report.json`
  alongside the human-readable `.report.txt` (`schema_version: 1`).
- `--version` flag and module-level `__version__`.

## [1.1.0] — 2026-05-18

### Added

- Full pytest suite (36 tests across 6 modules) using synthetic
  in-memory SPI images — no real firmware or PII committed.
- GitHub Actions CI: `ruff` lint job plus `pytest` matrix on
  Python 3.10 / 3.11 / 3.12 / 3.13, coverage upload to Codecov on 3.12.
- `pyproject.toml` with `pytest`, `pytest-cov`, `ruff` dev extras.

### Changed

- Source touchups driven by `ruff` (`zip(..., strict=True)`, removal of
  one-line semicolon splits, `set(...)` over set comprehensions).

## [1.0.0] — 2026-05-18

### Added

- Initial release of `bios_heal.py` — SPI BIOS dump healer with
  region-granularity preserve zones (default `me,nvram`), Intel Flash
  Descriptor auto-parse, FFSv2 volume scan with CRC32 + UINT16 header
  checksum, padding-run diff, automatic `<dump>.bak`, `--dry-run`,
  `--force`, `--no-backup`, `--threshold`, `--preserve`, `--padding-min`.
- Human-readable `<output>.report.txt`.
- MIT license.

[1.3.0]: https://github.com/LuGB18/bios-repairer/releases/tag/v1.3.0
[1.2.0]: https://github.com/LuGB18/bios-repairer/releases/tag/v1.2.0
[1.1.0]: https://github.com/LuGB18/bios-repairer/releases/tag/v1.1.0
[1.0.0]: https://github.com/LuGB18/bios-repairer/releases/tag/v1.0.0
