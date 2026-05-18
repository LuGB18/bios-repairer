# `bios_flash.py` — Technical Report

**Tool:** `bios_flash.py` (companion to `bios_heal.py`)
**Version:** 2.0.0
**Date:** 2026-05-18
**Repository:** <https://github.com/LuGB18/bios-repairer>

---

## 1. Purpose

`bios_flash.py` brings the SPI flash chip itself into the bios-repairer
workflow. Until v1.3.0 the project produced healed `.bin` files but
stopped at the file boundary — the operator had to switch to an
external GUI (AsProgrammer / NeoProgrammer / flashrom CLI by hand) to
actually program the chip. v2.0.0 closes that loop.

The script drives `flashrom` to:

1. Read the live chip contents into a file.
2. Verify the chip against a reference image.
3. Write an image into the chip.
4. **Chain** all of the above with `bios_heal`: read chip twice for
   consistency, run the heal logic in-process, optionally commit the
   healed image, and verify the write byte-for-byte.

## 2. Architecture

### 2.1 Layered responsibility

```
+-----------------------------------------------------------+
|                       bios_flash.py                       |
|  CLI / subcommands / safety gates / chain orchestration   |
+---------------------------+-------------------------------+
            |                              |
            v                              v
  +-------------------+         +----------------------+
  |   bios_heal       |         | flashrom (subprocess)|
  | (Python import)   |         | external binary      |
  +-------------------+         +----------------------+
```

`bios_flash` does **no** hardware I/O of its own — every chip-side
operation is a `subprocess.run(["flashrom", ...])` call. The heal step
is `import bios_heal; bios_heal.main(argv)`. No PATH dependency between
the two scripts, no IPC, no JSON shuttling between processes — exit
codes propagate as Python integers.

### 2.2 Why subprocess for flashrom, import for bios_heal

| Component | Strategy | Reason |
|---|---|---|
| `flashrom` | subprocess | Mature C tool, supports ~40 programmers and ~3000 chips, OS-level USB / kernel-driver concerns. Reimplementing in pure Python is a maintenance black hole. |
| `bios_heal` | in-process import | Same language, same project, same release cadence. Subprocess hop would add launch latency, lose Python tracebacks on internal bugs, and split the PyInstaller binary in two. |

### 2.3 The `argv` refactor

`bios_heal.main()` historically read `sys.argv` directly. v2.0.0 changed
the signature to `main(argv: list[str] | None = None)`, with
`argparse.parse_args(argv)`. When `argv is None`, argparse falls back to
`sys.argv[1:]` — so the CLI behavior is identical for direct
`python bios_heal.py ...` invocations. The new path lets `bios_flash`
construct an argv list and call `bios_heal.main(my_argv)` to run the
heal step under the same exit-code contract a CLI invocation would
produce.

## 3. Subcommands

| Subcommand | What it runs | Mandatory output |
|---|---|---|
| `read` | `flashrom -p P -c C -r FILE` | the `.bin` dump |
| `verify` | `flashrom -p P -c C -v FILE` | exit 0 = match, exit 13 = mismatch |
| `write` | pre-read backup → `flashrom -p P -c C -w FILE` → mandatory `flashrom -p P -c C -v FILE` | the chip is programmed and verified |
| `heal-flash` | read → read → `bios_heal.main(...)` → (optional) write → verify | a healed `.bin`, plus chip update if `--commit` |

### 3.1 `heal-flash` pipeline (the chained workflow)

```
       +-----------+        +-----------+        +------------+
chip -> | flashrom  | dump1->| flashrom  | dump2->| md5 match? |
       |  -r       |        |  -r       |        |            |
       +-----------+        +-----------+        +------+-----+
                                                        |
                                            no match -> exit 12
                                                        |
                                                        v match
                                              +--------------------+
                                              | bios_heal.main(    |
                                              |   base, dump1,     |
                                              |   --verify-dump    |
                                              |   --smoke, ...     |
                                              | )                  |
                                              +---------+----------+
                                                        |
                                                  exit != 0 -> propagate
                                                        |
                                                        v
                                                +---------------+
                                                | --commit set? |
                                                +-+-----+-+-----+
                                                  | yes | no
                                                  v     v
                                            flashrom    keep .bin,
                                              -w        return 0
                                                  |
                                                  v
                                            flashrom -v
                                                  |
                                            ok / 13
```

## 4. Safety analysis

The chip is unforgiving: an interrupted or wrong write leaves the
motherboard unbootable. Every chip-touching operation has explicit
gates.

### 4.1 Gates on the write path

| Gate | Implementation | Failure mode |
|---|---|---|
| Explicit `--commit` | `if not args.commit: return 14` | accidental `bios_flash write` does nothing |
| `--chip` explicit | argparse default is the project chip, but the value is always sent to flashrom | no flashrom auto-detect ambiguity on cheap clones |
| Pre-write chip backup | `flashrom -r` to `*.chipbak.<timestamp>.bin` BEFORE the `-w` call | bricked-write recovery image always exists |
| Image size sanity | `chip_size != len(image) -> return 2` | refuse to flash a 4 MiB image onto an 8 MiB chip |
| Post-write verify | unconditional `flashrom -v` after the `-w` call | catches programmer / chip / clip failures |
| Two-read consistency | `heal-flash` reads the chip twice; differing md5 -> exit 12 | flaky chip-clip detection before a heal-based write |

### 4.2 Threat model and mitigations

| Threat | Mitigation |
|---|---|
| Operator types `write` while half-asleep | `--commit` required; without it the subcommand is a no-op |
| Chip-clip contact intermittent during read | Two consecutive reads compared; exit 12 if they disagree |
| Wrong chip is socketed, write would brick a different ROM | Pre-write `flashrom -r` succeeds only if the same chip is still there with the right size |
| flashrom write succeeds but bytes don't land (worn cells, noise) | Mandatory post-write `flashrom -v`, exit 13 on mismatch |
| Operator passes a 4 MiB modded ROM onto an 8 MiB chip | Pre-write backup compared to image length; size mismatch returns 2 |
| flashrom not installed | `find_flashrom` returns None, exit 10 with a clear message |

### 4.3 Reportable state

All four subcommands accept `--json FILE` and emit a result document
that includes the operation name, programmer/chip, status, md5s, and
exit-relevant detail (e.g. `verify_failed`, `dry-run`). Same idea as
the bios_heal `.report.json`, scoped to the chip event.

## 5. Exit-code map

| Code | Source | Meaning |
|---|---|---|
| 0 | both | success |
| 1 | bios_heal | similarity below threshold and not `--force` |
| 2 | both | size mismatch (heal step, chip vs image, or verify-dump) |
| 3 | bios_heal | output size sanity failure |
| 4 | bios_heal | `--verify-dump` mismatch |
| 5 | bios_heal | smoke test failed |
| 10 | bios_flash | flashrom binary not found |
| 11 | bios_flash | flashrom probe / read / write failure |
| 12 | bios_flash | two consecutive chip reads disagree |
| 13 | bios_flash | post-write verify failed |
| 14 | bios_flash | `--commit` not passed but a write was requested |

Codes 0–5 propagate cleanly through `heal-flash` because the chain
calls `bios_heal.main(argv)` in-process and returns its exit code
unchanged when non-zero.

## 6. Workflow examples

### 6.1 Cold start on an unfamiliar board

```sh
# 1. Just look. No --commit. heal-flash dumps the chip twice, heals
#    against a candidate base, and stops with a healed .bin to inspect.
bios_flash heal-flash --base candidate.bin --preserve me,dmi

# 2. Review the healed_<ts>.bin and its .report.txt with UEFITool.
# 3. When happy, commit.
bios_flash heal-flash --base candidate.bin --preserve me,dmi --commit
```

### 6.2 Diagnostic-only

```sh
# Is the chip currently what I think it is?
bios_flash verify --against known-good.bin
# Exit 0 = match, exit 13 = chip diverged.
```

### 6.3 Programmer-agnostic

```sh
# Same script, different programmer hardware.
bios_flash read --programmer dediprog --chip W25Q64.V -o dump.bin
bios_flash write --programmer ft2232_spi --chip MX25L6406E \
                 --image healed.bin --commit
```

`flashrom -L` enumerates supported programmers and chips; any string
flashrom recognizes is valid for the `--programmer` and `--chip`
arguments.

## 7. Test strategy

Hardware-in-the-loop tests are impractical in CI (no programmer
attached to GitHub-hosted runners), and they would also be dangerous —
nobody wants a CI job that bricks a chip. Instead, the test suite
patches `subprocess.run` with a `FakeFlashrom` callable that maintains
an in-memory chip buffer:

- `-r FILE` writes the chip buffer to `FILE`
- `-w FILE` overwrites the chip buffer with the contents of `FILE`
- `-v FILE` compares the chip buffer to `FILE` and returns exit 0 or 1

15 tests cover the four subcommands plus failure modes:

| Failure | Test |
|---|---|
| flashrom not on PATH | `test_read_missing_flashrom` |
| verify mismatch | `test_verify_mismatch_returns_13` |
| write without `--commit` | `test_write_without_commit_refuses` |
| post-write corrupts chip | `test_write_post_verify_fail_returns_13` |
| image size != chip size | `test_write_size_mismatch_returns_2` |
| two reads disagree | `test_heal_flash_two_reads_disagree_returns_12` |
| heal step itself fails | `test_heal_flash_propagates_heal_exit_code` |

Total project suite: 78 tests, 78 passing, ruff lint clean.

## 8. Limitations

### 8.1 No real-hardware CI

The CI matrix builds binaries for Windows and Linux but does not
attempt to talk to any chip. The PyInstaller `--version` smoke is the
only on-runner check.

### 8.2 flashrom is a runtime dependency

`bios_flash` requires flashrom to be installed and on PATH (or passed
via `--flashrom`). The Windows .exe and Linux ELF artifacts bundle
Python but not flashrom — operators still need to install it
separately. Bundling flashrom would mean shipping its libusb / driver
stack on Windows, which is well outside this project's scope.

### 8.3 Programmer-specific quirks not modeled

CH341A clones sometimes report bogus chip IDs and need
`flashrom -c W25Q64.V` to be forced explicitly. `bios_flash` already
makes `--chip` mandatory, but it does not warn about known cloned-VID
patterns. A future enhancement could ship a small JSON of programmer
quirks and surface them as warnings.

### 8.4 No partial-region writes

`flashrom` supports `--include` / `--noverify-all` to write specific
flash layout regions. `bios_flash` always writes the whole image. For
the workflow this project targets (whole-chip heal, whole-chip flash),
that is the right default; an `--include` flag could be added later
if a use case appears.

## 9. Future work

1. **Programmer matrix in CI smoke** — run `flashrom --help` and
   `flashrom -L` on the runner so binary regressions surface
   immediately.
2. **GUI front-end** — a small Tkinter shell that wraps `read`,
   `heal-flash`, `verify` for non-CLI users; same exit-code contract.
3. **macOS arm64 binary** — extend the release matrix once a Mac runner
   is available and `flashrom` is reachable via Homebrew on the runner.
4. **`--include LAYOUT` passthrough** — let advanced users target a
   single flash layout region without re-writing the whole chip.
5. **PyPI publication** — `pip install bios-repairer` would let
   downstream users skip the binary download path.

## 10. Acknowledgements

`flashrom` (<https://flashrom.org>) does the actual hardware work.
This project is a thin orchestration layer plus the bios_heal image
surgery; flashrom is the load-bearing component.
