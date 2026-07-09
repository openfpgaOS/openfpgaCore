#!/usr/bin/env python3
#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------

# Stamps os.bin with image-carried metadata + an integrity trailer:
#
#   [image bytes]
#   [magic 'OABI'][boot_abi LE]                          (8 B, optional)
#   [magic 'OSE2'][entry LE][bss_start LE][bss_end LE]   (16 B, optional)
#   [magic 'OFC1'][crc32 LE]                             (8 B, always last)
#
# The CRC (CRC-32/ISO-HDLC, zlib.crc32, matching boot_crc32_uncached)
# covers everything before the 8-byte trailer, including the OABI + OSE2
# blocks.
#
# WHY OABI (boot-ABI id): the CRC is content-, not layout-checked, so a
# wrong-target or stale bootloader(mif)+os.bin pair still passes it and then
# black-boots (the bootloader loads/runs the image at the WRONG addresses).
# OABI carries a hash of the target + the memory-map constants the bootloader
# bakes in; boot.c compares it to its own baked id and stops with a visible
# error on mismatch.  It sits BEFORE OSE2 so the OSE2 block stays immediately
# before the CRC trailer (older bootloaders that only know OSE2 are unaffected).
#
# WHY OSE2 exists (2026-07-02 "kernel-layout boot lottery"): the BRAM
# bootloader is baked into the bitstream while os.bin swaps freely.  The
# bootloader used to zero .bss and jump to os_main using ITS OWN linked
# symbols — bounds and entry frozen at bitstream build time.  Any kernel
# whose layout shifted got a stale .bss clear (tail statics left holding
# power-on DRAM junk → layout-dependent trap/hang) and/or a stale entry
# jump (os_main moved → wild jump).  OSE2 carries the loaded image's own
# entry + .bss bounds so the bootloader preps memory for the image it
# actually loaded.  Unstamped images fall back to the legacy baked
# symbols (old behaviour, old risk).
#
# Usage:
#   append_os_crc.py <os.bin> [<entry_hex> <bss_start_hex> <bss_end_hex> [<boot_abi_hex>]]

import sys
import zlib

CRC_MAGIC = b"OFC1"   # read little-endian from SDRAM as 0x3143464F
META_MAGIC = b"OSE2"  # read little-endian from SDRAM as 0x3245534F
ABI_MAGIC = b"OABI"   # read little-endian from SDRAM as 0x4942414F


def main():
    if len(sys.argv) not in (2, 5, 6):
        sys.stderr.write(
            "usage: append_os_crc.py <os.bin> "
            "[<entry> <bss_start> <bss_end> [<boot_abi>]]\n")
        return 2
    path = sys.argv[1]
    data = open(path, "rb").read()

    # Idempotent: don't double-stamp if the rule re-runs without a rebuild.
    if len(data) >= 8 and data[-8:-4] == CRC_MAGIC:
        sys.stderr.write("  OS CRC trailer already present; skipping\n")
        return 0

    ose2 = b""
    oabi = b""
    if len(sys.argv) >= 5:
        entry = int(sys.argv[2], 16)
        bss_lo = int(sys.argv[3], 16)
        bss_hi = int(sys.argv[4], 16)
        if not (entry and bss_lo and bss_hi >= bss_lo):
            sys.stderr.write("append_os_crc.py: implausible metadata "
                             "(entry=%#x bss=%#x..%#x)\n" % (entry, bss_lo, bss_hi))
            return 2
        ose2 = (META_MAGIC + entry.to_bytes(4, "little")
                + bss_lo.to_bytes(4, "little") + bss_hi.to_bytes(4, "little"))
    if len(sys.argv) == 6:
        boot_abi = int(sys.argv[5], 16) & 0xFFFFFFFF
        if boot_abi:
            # Boot-ABI block, placed BEFORE the OSE2 block so OSE2 stays
            # immediately before the CRC trailer — bootloaders that only know
            # OSE2 keep reading it at the same offset, unchanged.
            oabi = ABI_MAGIC + boot_abi.to_bytes(4, "little")

    # Tail order: [image bytes][OABI][OSE2][OFC1 crc]
    meta = oabi + ose2
    stamped = data + meta
    crc = zlib.crc32(stamped) & 0xFFFFFFFF
    with open(path, "wb") as f:
        f.write(stamped + CRC_MAGIC + crc.to_bytes(4, "little"))
    if meta:
        sys.stderr.write(
            "  stamped %sOSE2 meta (entry=%s bss=%s..%s) + CRC trailer: "
            "crc32=0x%08x over %d bytes\n"
            % (("OABI(%s)+" % sys.argv[5]) if oabi else "",
               sys.argv[2], sys.argv[3], sys.argv[4], crc, len(stamped)))
    else:
        sys.stderr.write(
            "  stamped OS CRC trailer: crc32=0x%08x over %d bytes (no OSE2 meta)\n"
            % (crc, len(stamped)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
