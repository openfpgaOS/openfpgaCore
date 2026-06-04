#!/usr/bin/env python3
#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------

# Stamps an 8-byte integrity trailer onto os.bin: [magic 'OFC1'][crc32 LE].
#
# The bootloader streams os.bin SD -> CRAM0 scratch -> SDRAM: a two-stage copy
# through the CRAM0 PSRAM async page-mode path.  An intermittent corruption in
# that pipeline bakes a bad word into the OS image and later surfaces as an
# illegal-instruction trap at boot.  boot.c re-computes this CRC over the loaded
# SDRAM image and reloads on mismatch.  The trailer is self-describing (magic),
# so an unstamped image is simply accepted without verification.
#
# CRC is the standard CRC-32/ISO-HDLC (zlib.crc32), matching boot_crc32_uncached.

import sys
import zlib

MAGIC = b"OFC1"  # read little-endian from SDRAM as OS_CRC_MAGIC = 0x3143464F


def main():
    if len(sys.argv) != 2:
        sys.stderr.write("usage: append_os_crc.py <os.bin>\n")
        return 2
    path = sys.argv[1]
    data = open(path, "rb").read()

    # Idempotent: don't double-stamp if the rule re-runs without a rebuild.
    if len(data) >= 8 and data[-8:-4] == MAGIC:
        sys.stderr.write("  OS CRC trailer already present; skipping\n")
        return 0

    crc = zlib.crc32(data) & 0xFFFFFFFF
    with open(path, "ab") as f:
        f.write(MAGIC + crc.to_bytes(4, "little"))
    sys.stderr.write(
        "  stamped OS CRC trailer: crc32=0x%08x over %d bytes\n" % (crc, len(data))
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
