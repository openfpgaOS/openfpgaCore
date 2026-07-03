#!/usr/bin/env python3
#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------

# Stamps os.bin with image-carried metadata + an integrity trailer:
#
#   [image bytes]
#   [magic 'OSE2'][entry LE][bss_start LE][bss_end LE]   (16 B, optional)
#   [magic 'OFC1'][crc32 LE]                             (8 B, always last)
#
# The CRC (CRC-32/ISO-HDLC, zlib.crc32, matching boot_crc32_uncached)
# covers everything before the 8-byte trailer, including the OSE2 block.
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
#   append_os_crc.py <os.bin> [<entry_hex> <bss_start_hex> <bss_end_hex>]

import sys
import zlib

CRC_MAGIC = b"OFC1"   # read little-endian from SDRAM as 0x3143464F
META_MAGIC = b"OSE2"  # read little-endian from SDRAM as 0x3245534F


def main():
    if len(sys.argv) not in (2, 5):
        sys.stderr.write(
            "usage: append_os_crc.py <os.bin> [<entry> <bss_start> <bss_end>]\n")
        return 2
    path = sys.argv[1]
    data = open(path, "rb").read()

    # Idempotent: don't double-stamp if the rule re-runs without a rebuild.
    if len(data) >= 8 and data[-8:-4] == CRC_MAGIC:
        sys.stderr.write("  OS CRC trailer already present; skipping\n")
        return 0

    meta = b""
    if len(sys.argv) == 5:
        entry = int(sys.argv[2], 16)
        bss_lo = int(sys.argv[3], 16)
        bss_hi = int(sys.argv[4], 16)
        if not (entry and bss_lo and bss_hi >= bss_lo):
            sys.stderr.write("append_os_crc.py: implausible metadata "
                             "(entry=%#x bss=%#x..%#x)\n" % (entry, bss_lo, bss_hi))
            return 2
        meta = (META_MAGIC + entry.to_bytes(4, "little")
                + bss_lo.to_bytes(4, "little") + bss_hi.to_bytes(4, "little"))

    stamped = data + meta
    crc = zlib.crc32(stamped) & 0xFFFFFFFF
    with open(path, "wb") as f:
        f.write(stamped + CRC_MAGIC + crc.to_bytes(4, "little"))
    if meta:
        sys.stderr.write(
            "  stamped OSE2 meta (entry=%s bss=%s..%s) + CRC trailer: "
            "crc32=0x%08x over %d bytes\n"
            % (sys.argv[2], sys.argv[3], sys.argv[4], crc, len(stamped)))
    else:
        sys.stderr.write(
            "  stamped OS CRC trailer: crc32=0x%08x over %d bytes (no OSE2 meta)\n"
            % (crc, len(stamped)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
