#!/bin/bash
#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------
#
# Post-build resource / timing report.  Run from a target dir (the
# Makefiles wire this up as `make report [FULL=true]`).
#
#   report.sh <project> [full]
#
# Default: the Fitter Summary resource table.
# full:    + top entities by ALM usage (from the fit report)
#          + the N worst setup paths (re-runs quartus_sta, ~1-2 min)
#
# Env: TOP (entity rows, default 20), PATHS (worst paths, default 25).

set -e

PROJECT="$1"
FULL="${2:-}"
TOP="${TOP:-20}"
NPATHS="${PATHS:-25}"
FIT="output_files/$PROJECT.fit.rpt"
STA="output_files/$PROJECT.sta.rpt"
TOOLS_DIR="$(cd "$(dirname "$0")" && pwd)"

B=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GRN=$'\033[32m'; RST=$'\033[0m'
[ -t 1 ] || { B=""; DIM=""; RED=""; GRN=""; RST=""; }

[[ -z "$PROJECT" ]] && { echo "usage: report.sh <project> [full]"; exit 1; }
[[ -f "$FIT" ]] || { echo "${RED}no $FIT — run make build first${RST}"; exit 1; }

# ── Resource summary ─────────────────────────────────────────────────
echo "${B}Resource Summary${RST} ${DIM}($PROJECT)${RST}"
awk -F';' '
    /^; Fitter Summary/   { insum=1 }
    insum && /^\+--/      { dash++; if (dash == 2) exit }
    insum && /^;/ {
        key=$2; val=$3
        gsub(/^[ \t]+|[ \t]+$/, "", key)
        gsub(/^[ \t]+|[ \t]+$/, "", val)
        if (key ~ /^(Fitter Status|Revision Name|Device|Logic utilization|Total registers|Total pins|Total block memory bits|Total RAM Blocks|Total DSP Blocks|Total PLLs|Total DLLs)/)
            printf "  %-28s %s\n", key, val
    }
' "$FIT"

# Quick timing verdict from the existing sta report — anchored on the
# panel title row (the TOC repeats the phrase without a leading ';').
# Prefer the explicit slow-corner model panel; older/aggregate reports
# only carry a plain "Setup Summary".
if [[ -f "$STA" ]]; then
    if grep -q '^; Slow[^;]* Model Setup Summary' "$STA"; then
        ANCHOR='^; Slow[^;]* Model Setup Summary'
    else
        ANCHOR='^; Setup Summary'
    fi
    awk -F';' -v anchor="$ANCHOR" '
        !f && $0 ~ anchor { f=1; next }
        f && /^\+/ { dash++; if (dash >= 3) exit; next }
        f && /^;/ && $3 ~ /-?[0-9]+\.[0-9]/ {
            slack=$3; gsub(/[ \t]/, "", slack)
            if (worst == "" || slack+0 < worst+0) worst=slack
        }
        END {
            if (worst != "")
                printf "  %-28s %s ns (slow corner, worst clock)\n", "Worst setup slack", worst
        }
    ' "$STA" | sed "s/\(-[0-9.]*\)/${RED}\1${RST}/; s/ \([0-9][0-9.]*\) ns/ ${GRN}\1${RST} ns/"
fi

[[ "$FULL" != "full" ]] && exit 0

# ── Top entities by ALM usage ────────────────────────────────────────
echo ""
echo "${B}Top $TOP entities by ALM usage${RST} ${DIM}(total incl. children / self; from $FIT)${RST}"
printf "  ${DIM}%9s %9s %9s %6s %5s  %s${RST}\n" "ALM-total" "ALM-self" "Regs" "M10K" "DSP" "instance [entity]"
awk -F';' -v top="$TOP" '
    /^; Fitter Resource Utilization by Entity/ { sect=1; next }
    sect && /^; Compilation Hierarchy Node/ {
        # resolve column indexes by header name (Quartus-version-proof)
        for (i = 2; i <= NF; i++) {
            h=$i; gsub(/^[ \t]+|[ \t]+$/, "", h)
            if (h ~ /^ALMs needed/)               c_alm=i
            if (h == "Dedicated Logic Registers") c_reg=i
            if (h == "M10Ks")                     c_m10k=i
            if (h == "DSP Blocks")                c_dsp=i
            if (h == "Entity Name")               c_ent=i
        }
        hdr=1; next
    }
    sect && hdr && /^\+--/ { dash++; if (dash >= 2) exit; next }
    sect && hdr && /^;/ {
        node=$2; alm=$c_alm
        gsub(/[ \t]+$/, "", node)
        total=alm; sub(/ *\(.*/, "", total); gsub(/[ \t]/, "", total)
        self=alm;  sub(/.*\(/, "", self); sub(/\).*/, "", self)
        if (total + 0 < 0.5) next
        reg=$c_reg;   sub(/ *\(.*/, "", reg);  gsub(/[ \t]/, "", reg)
        m10k=$c_m10k; sub(/ *\(.*/, "", m10k); gsub(/[ \t]/, "", m10k)
        dsp=$c_dsp;   sub(/ *\(.*/, "", dsp);  gsub(/[ \t]/, "", dsp)
        ent=$c_ent;   gsub(/[ \t]/, "", ent)
        name=node; gsub(/^[ |]+/, "", name)
        printf "%012.1f\t  %9.1f %9.1f %9d %6d %5d  %s [%s]\n", \
               total, total, self, reg, m10k, dsp, name, ent
    }
' "$FIT" | sort -rn | head -"$TOP" | cut -f2-

# ── Worst setup paths (fresh quartus_sta run) ────────────────────────
echo ""
echo "${B}Worst $NPATHS setup paths${RST} ${DIM}(slow model — running quartus_sta, ~1-2 min)${RST}"
PATHS_OUT=$(mktemp /tmp/report_paths.XXXXXX.txt)
if quartus_sta -t "$TOOLS_DIR/report_paths.tcl" "$PROJECT" "$NPATHS" "$PATHS_OUT" \
        > /tmp/report_sta.log 2>&1; then
    # Compact two-line format: hierarchy prefixes stripped to the leaf
    # register names, PLL clock paths abbreviated to instance[counter].
    awk -F';' '
        # "emu|pll|pll_inst|altera_pll_i|general[0]...divclk" -> "pll[0]"
        function shortclk(c,    n, parts, i, name, idx) {
            gsub(/^[ \t]+|[ \t]+$/, "", c)
            if (c !~ /\|/) return c
            idx = ""
            if (match(c, /(general|counter)\[[0-9]+\]/))
                idx = substr(c, RSTART, RLENGTH); sub(/.*\[/, "[", idx)
            n = split(c, parts, "|"); name = parts[1]
            for (i = 1; i <= n; i++)
                if (parts[i] !~ /^(altera_pll_i|cyclonev_pll|.*_inst|pll_inst|general.*|counter.*|divclk)$/)
                    name = parts[i]
            return name idx
        }
        function leaf(node) {
            gsub(/^[ \t]+|[ \t]+$/, "", node)
            sub(/.*\|/, "", node)
            return node
        }
        /^;/ && $2 ~ /-?[0-9]+\.[0-9]/ {
            slack=$2; gsub(/[ \t]/, "", slack)
            skew=$8;  gsub(/[ \t]/, "", skew)
            dly=$9;   gsub(/[ \t]/, "", dly)
            lc = shortclk($5); tc = shortclk($6)
            clk = (lc == tc) ? lc : lc "->" tc
            n++
            printf "  %2d %8s %7s %7s  %-10s %s\n", n, slack, skew, dly, clk, leaf($3)
            printf "  %2s %8s %7s %7s  %-10s -> %s\n", "", "", "", "", "", leaf($4)
        }
        BEGIN {
            printf "  %2s %8s %7s %7s  %-10s %s\n", "#", "Slack", "Skew", "Delay", "Clock", "From / To (leaf registers)"
        }
    ' "$PATHS_OUT" | sed "s/\(-[0-9][0-9.]*\)/${RED}\1${RST}/g"
else
    echo "  ${RED}quartus_sta failed — see /tmp/report_sta.log${RST}"
    rm -f "$PATHS_OUT"
    exit 1
fi
rm -f "$PATHS_OUT"
