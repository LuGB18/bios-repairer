================================================================================
  bios_heal.py — README
  SPI BIOS dump healer for Intel-descriptor flash images (8 MiB / W25Q64BV)
  Tested platform: ASRock B75M-DGS (Ivy Bridge, AMI Aptio IV)
================================================================================


WHAT IT DOES
------------
You have two BIOS images:

  * BASE — a clean, known-good reference (factory ROM, or a modded ROM that
           you trust). Used as the source of *healing bytes*.

  * DUMP — an SPI dump from a real board. May be corrupted in some areas,
           but contains the board's unique secrets (serial number, UUID,
           NIC MAC, Intel ME fuse/config). Used as the source of
           *preserved bytes*.

bios_heal.py merges them into a single output .bin:

    output = BASE everywhere
           EXCEPT the "preserve zones", which are copied verbatim from DUMP.

It then writes a human-readable .report.txt next to the output, showing:

  * Region layout parsed from the Intel Flash Descriptor (FD)
  * Per-region similarity DUMP vs BASE
  * Every FFSv2 volume in the BIOS region, with CRC32 from each side
  * Padding (0xFF) runs gained/lost by the heal
  * Byte-diff totals (must be 0 inside preserve zones)


WHEN TO USE IT
--------------
  * SPI dump shows random corruption in code areas but you still trust the
    ME region and the board-specific NVRAM.
  * You want to flash a modded BIOS image but keep the board's original
    serial / UUID / MAC / MEBx config.
  * You want a forensic diff report between two ROMs without flashing
    anything (use --dry-run).

It is NOT a substitute for:
  * UEFITool        — visual structure inspection, GUID editing
  * MEAnalyzer      — Intel ME firmware sanity / version check
  * Flashrom / CH341A — actually programming the SPI chip


REQUIREMENTS
------------
  * Python 3.10 or newer (uses PEP 604 union syntax: `X | None`)
  * No third-party packages — stdlib only (argparse, hashlib, zlib, shutil)
  * Read access to BASE and DUMP, write access to the output directory


QUICK START
-----------
  1. Dump your board twice with a CH341A and verify the two dumps match
     (md5sum on both — they MUST be identical, otherwise re-dump).

  2. Obtain a clean BASE of the same size (8 MiB for B75M-DGS / W25Q64BV).
     Either factory ROM padded to full size, or a known-good modded ROM.

  3. Run a dry-run first to read the report without writing anything:

         python bios_heal.py BASE.bin DUMP.bin -o OUT.bin --dry-run

  4. Read OUT.bin.report.txt. Confirm:
        * Global similarity is reasonable (>80% normal, >90% ideal)
        * FFSv2 volume table shows volumes you expect
        * Per-region similarity looks sane

  5. Run the real heal:

         python bios_heal.py BASE.bin DUMP.bin -o OUT.bin

  6. Flash OUT.bin to the SPI chip with CH341A + AsProgrammer / NeoProgrammer
     (chip: W25Q64BV). Always Erase → Write → Verify.


ARGUMENT REFERENCE
------------------
Positional:
  BASE                clean reference BIOS image (.bin)
  DUMP                corrupted/board-specific dump (.bin)

Required:
  -o, --output FILE   output .bin path. A sibling FILE.report.txt is also
                      written.

Tuning:
  --threshold FLOAT   default 0.90. Minimum byte-similarity DUMP must have
                      with BASE before heal is applied. Below this, DUMP
                      is copied unchanged unless --force is given. Useful
                      guard against healing the wrong base into the wrong
                      board.

  --preserve LIST     default "me,nvram". Comma-separated zones copied
                      verbatim from DUMP. Choices:
                        fd     Flash Descriptor
                        me     Intel Management Engine region
                        bios   entire BIOS region (rarely useful)
                        gbe    integrated GbE (NIC MAC) region
                        pdr    Platform Data Region
                        nvram  first FFSv2 volume inside BIOS region
                               (auto-derived; holds serial/UUID/MAC vars)
                      Unknown names are warned and ignored.

  --padding-min N     default 256. Minimum consecutive 0xFF bytes to count
                      as a padding run. Lower = noisier report.

Mode flags:
  --force             apply heal even when similarity < threshold.
  --dry-run           generate the .report.txt only; never write the .bin.
  --no-backup         do not create DUMP.bak. By default a .bak is written
                      next to DUMP before the output is generated (unless
                      DUMP.bak already exists — never overwritten).


REGION LAYOUT (auto-detected from Intel FD)
-------------------------------------------
For ASRock B75M-DGS / W25Q64BV the FD declares:

  Offset       Region        Length    Heal action
  ---------    ----------    -------   ------------------------------------
  0x000000     Descriptor    4 KiB     heal from BASE
  0x001000     ME            ~5 MiB    PRESERVED from DUMP (board fuse)
  0x500000     NVRAM volume  128 KiB   PRESERVED from DUMP (serial/UUID/MAC)
  0x520000     BIOS code     ~3 MiB    heal from BASE (DXE / PEI / BB)

The NVRAM zone is *not* in the FD — it is auto-derived by scanning the
BIOS region for the first FFSv2 volume header signature ("_FVH").


REPORT FILE FORMAT
------------------
OUTPUT.bin.report.txt sections:

  Layout            per-region size + similarity, [PRESERVED] tag
  Global similarity overall byte-match ratio + threshold + decision
  FFSv2 volumes     offset, length, base_crc32, dump_crc32, header check,
                    status (identical / diff / extra_in_dump / missing)
  Padding runs      counts in base / dump / healed, lost & gained sets
  Diff summary      bytes changed vs dump; preserve-zone diff (must = 0)


EXIT CODES
----------
  0    heal applied successfully (output written)
  1    similarity below threshold and --force not set (output = dump
       unchanged), or --dry-run completed without heal
  2    base and dump have different sizes
  3    output size sanity check failed (should never happen)


COMMON SCENARIOS
----------------

1) Standard heal, modded BIOS into healthy board dump
       python bios_heal.py modded.bin board_dump.bin -o final.bin

2) Forensic comparison only, no write
       python bios_heal.py clean.bin dump.bin -o stub.bin --dry-run

3) Heavily corrupted dump, override threshold
       python bios_heal.py clean.bin dump.bin -o out.bin --force \
           --threshold 0.5

4) Preserve NIC MAC if board uses Intel GbE region
       python bios_heal.py clean.bin dump.bin -o out.bin \
           --preserve me,nvram,gbe

5) Quiet padding output (only count runs >= 4 KiB)
       python bios_heal.py clean.bin dump.bin -o out.bin \
           --padding-min 4096


SAFETY NOTES
------------
  * Always keep DUMP.bak (the script makes one automatically). If the
    healed image bricks the board, you must be able to restore the
    original dump.

  * Do NOT flash a full 8 MiB image through the motherboard's "Instant
    Flash" / EFI flash utility — the FD+ME regions are protected and the
    BIOS-only path will refuse or corrupt. Use an external CH341A
    programmer with the chip out of socket (or chip-clip with PSU off).

  * If similarity is suspiciously low (< 50%), you probably picked the
    wrong BASE. Different vendors / BIOS versions for the same chipset
    are not interchangeable.

  * The heal does NOT validate the ME region's internal FPT/FTPR/NFTP
    checksums. If your ME is corrupted, use MEAnalyzer + the matching
    clean ME firmware (Intel ME 8.x consumer for B75) to fix it
    separately, then heal.


FILES TOUCHED
-------------
  Created/overwritten:
    OUTPUT.bin
    OUTPUT.bin.report.txt
    DUMP.bin.bak              (only if it does not already exist)


CONTACT / TROUBLESHOOTING
-------------------------
  * "no Intel FD in base — using DEFAULT_LAYOUT"
      Base does not have the FD signature 5A A5 F0 0F at offset 0x10.
      Either the base is BIOS-region-only (not a full SPI image) or it
      is for a non-Intel platform. Provide a full 8 MiB Intel image.

  * "size mismatch: base=X dump=Y"
      Files differ in length. Re-dump or trim/pad the base to match
      the actual SPI chip capacity.

  * "below threshold — copying dump unchanged"
      Expected when the two images differ significantly. Inspect the
      report to decide whether to lower --threshold or pick a different
      BASE.

================================================================================
