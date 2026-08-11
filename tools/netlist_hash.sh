#!/bin/bash
#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------
#
# netlist_hash <generated-qsf> -> one md5 over every source + macro the qsf
# names.  A stored fitter seed only means anything for the exact netlist it was
# swept on: any RTL change reshuffles placement and makes the seed a lottery
# ticket.  That once shipped a WNS -1.08 black-screen os20 from a seed swept at
# -0.156.  sweep.sh records the hash beside the seed; `make build` compares.
netlist_hash() {
    local qsf="$1"
    [ -f "$qsf" ] || { echo "no-qsf"; return; }
    {
        grep -E "VERILOG_MACRO|SEED " "$qsf" | grep -v "SEED " | sort
        # MIF_FILE counts: the boot ROM is baked into the bitstream, so a
        # firmware rebuild changes what the fitter places even when every .v is
        # byte-identical.  Leaving it out once let an os30 sweep fail 12/12
        # against an unchanged RTL fingerprint.
        grep -E "(SYSTEM)?VERILOG_FILE|QIP_FILE|MIF_FILE" "$qsf" \
        | awk '{print $NF}' \
        | while read -r f; do
            # Hash CONTENT only: md5sum <file> would include the path, which
            # carries the per-seed build dir, so the same netlist hashed
            # differently from bld/<v>-s<best>/ and bld/<v>/ and the staleness
            # warning fired on every build.
            if [ -f "$f" ]; then md5sum < "$f"
            elif [ -f "$(dirname "$qsf")/$f" ]; then md5sum < "$(dirname "$qsf")/$f"
            fi
          done | sort
    } | md5sum | cut -d' ' -f1
}
