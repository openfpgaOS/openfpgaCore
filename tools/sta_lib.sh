#!/bin/bash
#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------
#
# Shared Quartus STA-report parsing, sourced by sweep.sh and report.sh
# (one parser to fix when a Quartus version shifts the panel format).

# Worst setup slack (WNS) + summed per-clock TNS from the slow-corner
# setup panel.  25.1std titles it "; Slow ... Model Setup Summary"; Q17
# single-corner runs use a plain "; Setup Summary" (the TOC repeats the
# phrase without a leading ';', hence the anchored grep).  Echoes
# "wns tns" or nothing when the panel is missing.
sta_wns_tns() {
    local rpt="$1" anchor
    [ -f "$rpt" ] || return 0
    if grep -q '^; Slow[^;]* Model Setup Summary' "$rpt"; then
        anchor='^; Slow[^;]* Model Setup Summary'
    else
        anchor='^; Setup Summary'
    fi
    awk -F';' -v anchor="$anchor" '
        !f && $0 ~ anchor { f=1; next }
        f && /^\+/ { dash++; if (dash >= 3) exit; next }
        f && /^;/ && $3 ~ /-?[0-9]+\.[0-9]/ {
            slack=$3; gsub(/[ \t]/, "", slack)
            t=$4;     gsub(/[ \t]/, "", t)
            if (wns == "" || slack+0 < wns+0) wns=slack
            tns += t+0
        }
        END { if (wns != "") printf "%s %.1f\n", wns, tns }
    ' "$rpt"
}

# Worst hold slack across EVERY hold-summary panel (multicorner runs have
# four: slow/fast x 85C/0C; Q17 single-corner has a plain "Hold Summary").
# The fitter hold-fixes every ANALYZED path, so a residual negative here
# means it tried and gave up — such a fit razor-races in silicon no matter
# how good its setup WNS is (2026-08-05: the vid/analog clock-group merge
# exposed exactly this on the scanout x_count crossing; intentional
# clock-as-data samplers must be set_false_path'd or they pollute this
# number).  Echoes the worst slack, or nothing when no hold panel exists.
sta_hold_wns() {
    local rpt="$1"
    [ -f "$rpt" ] || return 0
    awk -F';' '
        /^; ([A-Za-z0-9 ]* Model )?Hold Summary/ { f=1; dash=0; next }
        f && /^\+/ { dash++; if (dash >= 3) f=0; next }
        f && /^;/ && $3 ~ /-?[0-9]+\.[0-9]/ {
            slack=$3; gsub(/[ \t]/, "", slack)
            if (wns == "" || slack+0 < wns+0) wns=slack
        }
        END { if (wns != "") print wns }
    ' "$rpt"
}
