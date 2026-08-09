#!/bin/bash
#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------
#
# netlist_hash <generated-qsf> -> one md5 over every HDL source + macro the
# qsf names.  A stored fitter seed is only meaningful for the exact netlist
# it was swept on: ANY RTL change (even one constant) reshuffles placement
# and turns the stored seed into a lottery ticket -- this shipped a
# WNS -1.08/TNS -346 os20 (black screen) from a seed swept at -0.156/-1.5
# on 2026-08-02, and earlier made two sweep tables silently incomparable.
# sweep.sh records the hash next to the seed; `make build` compares.
netlist_hash() {
    local qsf="$1"
    [ -f "$qsf" ] || { echo "no-qsf"; return; }
    {
        grep -E "VERILOG_MACRO|SEED " "$qsf" | grep -v "SEED " | sort
        grep -E "(SYSTEM)?VERILOG_FILE|QIP_FILE" "$qsf" \
        | awk '{print $NF}' \
        | while read -r f; do
            # qsf paths are absolute (generated) or relative to the qsf dir.
            # Hash CONTENT ONLY -- `md5sum <file>` prints "<hash>  <path>", and
            # the path carries the per-seed build dir (bld/<variant>-s<seed>/).
            # Including it made the fingerprint differ between two runs of the
            # SAME netlist: sweep.sh stores the hash from bld/<v>-s<best>/ while
            # `make build` recomputes it from bld/<v>/, so the staleness warning
            # fired on every build and the real check it guards was lost in the
            # noise.
            if [ -f "$f" ]; then md5sum < "$f"
            elif [ -f "$(dirname "$qsf")/$f" ]; then md5sum < "$(dirname "$qsf")/$f"
            fi
          done | sort
    } | md5sum | cut -d' ' -f1
}
