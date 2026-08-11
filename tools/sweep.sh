#!/bin/bash
#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------
#
# Quartus fitter seed sweep.  Tries a range of seeds, ranks them by setup WNS on
# the target 100 MHz clock (Fmax/TNS shown alongside), and writes the winning
# seed into the variant's stored seed file so the next `make build` uses it.
#
# Two backends, selected by USE_CONTAINER:
#
#   container (default — Pocket / Quartus 25.1): each seed compiles in its OWN
#     bld/<variant>-s<seed>/ dir inside an isolated Docker container, so MAXJOBS
#     fits run AT ONCE without the shared-state segfault that crashes concurrent
#     host Quartus (each container has a private $HOME + /tmp).  The per-seed qsf
#     (verilog macros + seed baked in) is regenerated via `make bld/<job>/...`.
#     Needs: VARIANT, TARGET_DIR (dir holding the target Makefile).
#
#   in-place (USE_CONTAINER=0 — MiSTer / Quartus 17): one quartus compile per
#     seed, serial (the in-place db/ + output_files/ leave nothing to
#     parallelize).  Sweeps in BLD_DIR (or CWD), sed-patching the seed into
#     <PROJECT>.qsf, and does a final rebuild with the best seed.  Every
#     quartus invocation is prefixed with $QRUN — the mister target passes
#     the quartus17-container.sh wrapper so the sweep uses the SAME
#     containerized Quartus 17 as `make build` (empty QRUN = host quartus
#     on PATH, the USE_QUARTUS_CONTAINER=0 escape hatch).
#     Needs: BLD_DIR (optional), VARIANT_DEFS (optional verilog macros),
#     QRUN (optional container-wrapper prefix).
#
# Usage: sweep.sh <min> <max>
# Common env: PROJECT(=ap_core)  CLOCK_RE  MAXJOBS(=4)  USE_CONTAINER(=1)
#             VARIANT  SEED_FILE  QRUN
#
# No `set -e`: Quartus exits non-zero when timing isn't met, but still produces a
# valid bitstream — we keep going and rank every seed; only a fitter crash (no
# STA report) drops a seed from consideration.
set -uo pipefail

PROJECT=${PROJECT:-ap_core}
CLOCK_RE=${CLOCK_RE:-mp_ram.*general\[0\]}
MIN=${1:-1}; MAX=${2:-30}
USE_CONTAINER=${USE_CONTAINER:-1}
MAXJOBS=${MAXJOBS:-4}
VARIANT=${VARIANT:-}
TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Shared STA parsing (sta_wns_tns).  Source BEFORE any cd — BASH_SOURCE resolves
# relative to the invocation CWD.
. "$TOOLS/sta_lib.sh"

C_HEAD='\033[1m'; C_OK='\033[32m'; C_WARN='\033[33m'; C_ERR='\033[31m'
C_DIM='\033[2m'; C_RESET='\033[0m'

TOTAL=$(( MAX - MIN + 1 ))
declare -A R_WNS R_TNS R_FMAX R_DQ R_HOLD R_RAN

# parse_sta <sta.rpt> -> sets _wns _tns _fmax (restricted Fmax on CLOCK_RE).
parse_sta() {
    _wns=""; _tns=""; _fmax="failed"
    [ -f "$1" ] || return
    read -r _wns _tns < <(sta_wns_tns "$1")
    _fmax=$(awk -F';' -v re="$CLOCK_RE" \
        '/^; [0-9]/ && $4 ~ re { gsub(/^ +| +$/, "", $3); print $3; exit }' \
        "$1" 2>/dev/null)
    [ -z "$_fmax" ] && _fmax="failed"
}

if [ "$USE_CONTAINER" = 1 ]; then
    # ── Container backend: MAXJOBS-wide parallel fits ────────────────────
    : "${VARIANT:?VARIANT required for container sweep}"
    : "${TARGET_DIR:?TARGET_DIR required for container sweep}"
    cd "$TARGET_DIR"
    # Per-fit processor count is pinned in each generated qsf (NUM_PARALLEL_PROCESSORS
    # = QPROCS in the Makefile), so MAXJOBS fits share the host cores without
    # oversubscribing — and each fit places identically to the production build.
    printf "${C_HEAD}[sweep]${C_RESET} $VARIANT seeds $MIN-$MAX, ${MAXJOBS}-wide (containers; procs pinned in qsf)\n"

    # Per-seed results are parsed and persisted THE MOMENT each fit finishes
    # (one atomic line append per seed), so the final ranking never depends on
    # the bld/<variant>-s*/ scratch dirs still existing.  A 2.5 h 50-seed run
    # once lost EVERY result because an external disk cleanup removed the
    # scratch dirs between fit completion and the end-of-run parse.  The live
    # per-seed lines printed below land in the caller's log as a second copy.
    RESULTS="bld/${VARIANT}-sweep.results"
    : > "$RESULTS"

    # Shared inputs once: the CPU netlist + firmware are identical across seeds.
    make --no-print-directory cpu VARIANT="$VARIANT" >/dev/null
    make --no-print-directory bootloader >/dev/null

    run_one() {
        local s="$1" job="${VARIANT}-s$1"
        make --no-print-directory "bld/$job/ap_core.qsf" \
            VARIANT="$VARIANT" JOB="$job" SEED="$s" >/dev/null 2>&1
        bash "$TOOLS/quartus-container.sh" "$TARGET_DIR/bld/$job" >"bld/$job.log" 2>&1
        # Harvest this seed's numbers NOW (subshell-local parse; single-line
        # O_APPEND write is atomic across the MAXJOBS parallel jobs).
        parse_sta "bld/$job/output_files/${PROJECT}.sta.rpt"
        # Per-class DQ-capture slack (path-class-blind ranking shipped the
        # blank-booting s23 and the read-marginal s41; see sta_dq.tcl).
        local _dq=""
        if [ -f "$TOOLS/sta_dq.tcl" ]; then
            cp "$TOOLS/sta_dq.tcl" "bld/$job/" 2>/dev/null
            _dq=$(bash "$TOOLS/quartus-container.sh" "$TARGET_DIR/bld/$job"                     quartus_sta -t sta_dq.tcl 2>/dev/null                   | grep -o 'DQCLASS_WNS=[-0-9.]*' | head -1 | cut -d= -f2)
        fi
        # Worst hold across all corners: a residual negative = the fitter
        # gave up hold-fixing an analyzed path = silicon razor (see sta_lib).
        local _hold=""
        _hold=$(sta_hold_wns "bld/$job/output_files/${PROJECT}.sta.rpt")
        printf "%s|%s|%s|%s|%s|%s\n" "$s" "$_wns" "$_tns" "$_fmax" "$_dq" "$_hold" >> "$RESULTS"
        if [ -n "$_wns" ]; then
            printf "  seed %-3s ${C_DIM}fit done:${C_RESET} WNS %-9s TNS %-9s HOLD %s\n" "$s" "$_wns" "${_tns:--}" "${_hold:--}"
        else
            printf "  seed %-3s ${C_ERR}fit failed (no STA)${C_RESET}\n" "$s"
        fi
    }

    for s in $(seq "$MIN" "$MAX"); do
        run_one "$s" &
        while [ "$(jobs -rp | wc -l)" -ge "$MAXJOBS" ]; do wait -n; done
    done
    wait

    # Load the harvested results; fall back to a live dir parse only for a
    # seed whose line is missing AND whose scratch dir still exists.
    while IFS='|' read -r s w t f d h; do
        R_WNS[$s]=$w; R_TNS[$s]=$t; R_FMAX[$s]=$f; R_DQ[$s]=${d:-}; R_HOLD[$s]=${h:-}
        R_RAN[$s]=1
    done < "$RESULTS"
    for s in $(seq "$MIN" "$MAX"); do
        [ -n "${R_WNS[$s]:-}" ] && continue
        parse_sta "bld/${VARIANT}-s$s/output_files/${PROJECT}.sta.rpt"
        [ -n "$_wns" ] && { R_WNS[$s]=$_wns; R_TNS[$s]=$_tns; R_FMAX[$s]=$_fmax;
                            R_HOLD[$s]=$(sta_hold_wns "bld/${VARIANT}-s$s/output_files/${PROJECT}.sta.rpt"); }
    done
else
    # ── In-place backend: serial compile per seed (mister / Quartus 17) ──
    [ -n "${BLD_DIR:-}" ] && cd "$BLD_DIR"
    mkdir -p output_files
    QRUN=${QRUN:-}
    printf "${C_HEAD}[sweep]${C_RESET} Seeds $MIN-$MAX ($TOTAL builds, in-place serial${QRUN:+, containerized})\n"
    [ -n "${VARIANT_DEFS:-}" ] && printf "${C_HEAD}[sweep]${C_RESET} Variant defs: ${VARIANT_DEFS}\n"

    # The macros MUST be part of every sweep fit — a sweep without them
    # compiles a different (featureless) design, so its seed ranking and
    # in-place artifacts are meaningless for the real build.  Elements are
    # bare identifiers, so inlining ${VERILOG_MACROS[*]} into the one-shot
    # chain below is safe.
    VERILOG_MACROS=()
    if [ -n "${VARIANT_DEFS:-}" ]; then
        for def in ${VARIANT_DEFS}; do VERILOG_MACROS+=(--verilog_macro="${def}"); done
    fi
    # One $QRUN call runs the whole map→fit→asm→sta chain: Q17's per-tool
    # startup adds up (especially under Rosetta), so a single container
    # start per seed amortizes it — same shape as the mister `make build`.
    FLOW="quartus_map ${PROJECT}${VERILOG_MACROS[*]:+ ${VERILOG_MACROS[*]}} && quartus_fit ${PROJECT} && quartus_asm ${PROJECT} && quartus_sta ${PROJECT}"

    n=0
    for s in $(seq "$MIN" "$MAX"); do
        n=$((n + 1))
        printf "${C_DIM}[%d/%d]${C_RESET} Seed %-3s " "$n" "$TOTAL" "$s"
        sed -i "s/^set_global_assignment -name SEED .*/set_global_assignment -name SEED ${s}/" "${PROJECT}.qsf"
        rm -rf db incremental_db
        $QRUN bash -c "$FLOW" > "output_files/seed_${s}_compile.log" 2>&1 || true
        parse_sta "output_files/${PROJECT}.sta.rpt"
        cp "output_files/${PROJECT}.sta.rpt" "output_files/seed_${s}_sta.log" 2>/dev/null || true
        R_WNS[$s]=$_wns; R_TNS[$s]=$_tns; R_FMAX[$s]=$_fmax
        R_HOLD[$s]=$(sta_hold_wns "output_files/${PROJECT}.sta.rpt")
        R_RAN[$s]=1
        if [ -n "$_wns" ]; then
            printf "${C_OK}%-10s${C_RESET} ${C_DIM}WNS %-8s TNS %s${C_RESET}\n" "$_fmax" "${_wns:--}" "${_tns:--}"
        else
            printf "${C_ERR}failed${C_RESET}\n"
        fi
        # STOP_ON_PASS: a seed that CLOSES timing is the best outcome the
        # ranking can report -- no later seed can beat "passes" -- so on a
        # target with headroom (mister) the remaining fits are pure wall
        # clock at ~15-20 min each.  Hold must be clean too, or we would
        # stop on a seed the hold veto below throws out anyway.  Serial mode
        # only: container mode has already launched its whole batch.
        if [ "${STOP_ON_PASS:-0}" = "1" ] && [ -n "$_wns" ]; then
            _h=${R_HOLD[$s]:-}
            if awk -v w="$_wns" 'BEGIN{exit !(w>=0)}' &&
               { [ -z "$_h" ] || awk -v h="$_h" -v v="${SWEEP_HOLD_VETO:-0}" 'BEGIN{exit !(h>=v)}'; }; then
                printf "${C_OK}[sweep]${C_RESET} seed %s closes timing (WNS %s, HOLD %s) — stopping early\n" \
                       "$s" "$_wns" "${_h:--}"
                break
            fi
        fi
    done
fi

# ── Rank by setup WNS on the target clock (robust; fmax ~ 1/(T-wns)) ─────
echo ""
printf "${C_HEAD}Results:${C_RESET}\n"
# Winner policy (2026-08-01, after the s41/s21/s23 field failures; hold veto
# added 2026-08-05 after the scanout razor):
#   0. VETO any seed whose worst HOLD slack (all corners, all clocks) is
#      below SWEEP_HOLD_VETO (default 0): the fitter hold-fixes every
#      analyzed path, so a residual negative means it gave up — that fit
#      razor-races in silicon (wiggle/shear class) regardless of setup WNS.
#   1. VETO any seed whose DQ-capture class WNS is worse than SWEEP_DQ_VETO
#      (default -0.40): those read-marginal placements boot-loop or corrupt
#      reads in the field regardless of global WNS.
#   2. Shortlist the WNS band: every surviving seed within 0.10 ns of the
#      best WNS (at a clamped WNS floor the band is where the information is).
#   3. Winner = smallest |TNS| in the band (fewest paths hovering at the
#      wall -- pure argmax-WNS picked the field-corrupting s41 over s43/s48).
DQ_VETO=${SWEEP_DQ_VETO:--0.40}
HOLD_VETO=${SWEEP_HOLD_VETO:-0}
BEST=""; BEST_WNS=""
declare -A R_VETO
for s in $(seq "$MIN" "$MAX"); do
    # Skip seeds the run never reached (STOP_ON_PASS ended it early) -- they
    # are not failures and must not be printed as such.
    [ -n "${R_RAN[$s]:-}" ] || continue
    w=${R_WNS[$s]:-}
    if [ -z "$w" ]; then printf "  ${C_ERR}seed %-3s failed${C_RESET}\n" "$s"; continue; fi
    dq=${R_DQ[$s]:-}
    hold=${R_HOLD[$s]:-}
    veto=""
    if [ -n "$hold" ] && awk -v a="$hold" -v v="$HOLD_VETO" 'BEGIN{exit !(a<v)}'; then
        veto=" ${C_ERR}(HOLD ${hold} < ${HOLD_VETO}: VETOED)${C_RESET}"
        R_VETO[$s]=1
    fi
    if [ -n "$dq" ] && awk -v a="$dq" -v v="$DQ_VETO" 'BEGIN{exit !(a<v)}'; then
        veto="$veto ${C_ERR}(DQ ${dq} < ${DQ_VETO}: VETOED)${C_RESET}"
        R_VETO[$s]=1
    fi
    printf "  seed %-3s Fmax %-10s WNS %-9s TNS %-9s DQ %-8s HOLD %s%b\n"         "$s" "${R_FMAX[$s]:--}" "$w" "${R_TNS[$s]:--}" "${dq:--}" "${hold:--}" "$veto"
    [ -n "$veto" ] && continue
    if [ -z "$BEST" ] || awk -v a="$w" -v b="$BEST_WNS" 'BEGIN{exit !(a>b)}'; then
        BEST=$s; BEST_WNS=$w
    fi
done
if [ -n "$BEST" ]; then
    BAND_MIN=$(awk -v b="$BEST_WNS" 'BEGIN{printf "%.3f", b-0.10}')
    W_SEED=""; W_ABS_TNS=""; W_WNS=""; W_HOLD=""
    for s in $(seq "$MIN" "$MAX"); do
        w=${R_WNS[$s]:-}; [ -z "$w" ] && continue
        [ -n "${R_VETO[$s]:-}" ] && continue
        awk -v a="$w" -v m="$BAND_MIN" 'BEGIN{exit !(a>=m)}' || continue
        t=${R_TNS[$s]:-0}
        at=$(awk -v t="$t" 'BEGIN{printf "%.3f", (t<0)?-t:t}')
        h=${R_HOLD[$s]:-0}
        # Smallest |TNS| wins; within 0.05 ns the two are indistinguishable, so
        # break by setup WNS and then by hold margin.  Plain "<" made ties fall
        # to seed ITERATION ORDER: on 2026-08-06 that auto-picked s9 over s12,
        # which had identical TNS but better WNS (-0.473 vs -0.558) AND better
        # hold (+0.075 vs +0.041) -- strictly the better fit on every axis.
        if [ -z "$W_SEED" ] || awk -v a="$at" -v b="$W_ABS_TNS" \
                                   -v aw="$w"  -v bw="$W_WNS" \
                                   -v ah="$h"  -v bh="$W_HOLD" 'BEGIN{
                 d = a - b; if (d < 0) d = -d
                 if (d > 0.05)          { exit !(a+0  < b+0)  }
                 if (aw+0 != bw+0)      { exit !(aw+0 > bw+0) }
                 exit !(ah+0 > bh+0)
             }'; then
            W_SEED=$s; W_ABS_TNS=$at; W_WNS=$w; W_HOLD=$h
        fi
    done
    if [ -n "$W_SEED" ] && [ "$W_SEED" != "$BEST" ]; then
        printf "  ${C_WARN}band pick: seed %s (|TNS| %s) over argmax-WNS seed %s${C_RESET}\n"             "$W_SEED" "$W_ABS_TNS" "$BEST"
        BEST=$W_SEED; BEST_WNS=${R_WNS[$W_SEED]}
    fi
fi

if [ -z "$BEST" ]; then printf "\n${C_ERR}[sweep] All seeds failed${C_RESET}\n"; exit 1; fi

printf "\n${C_HEAD}Best: seed %s (Fmax %s, WNS %s, TNS %s)${C_RESET}" \
    "$BEST" "${R_FMAX[$BEST]}" "$BEST_WNS" "${R_TNS[$BEST]:--}"
awk -v a="$BEST_WNS" 'BEGIN{exit !(a<0)}' && \
    printf " ${C_WARN}(below 100 MHz target — shipping anyway)${C_RESET}"
echo ""

# Persist the winner into the variant's stored seed file (the source of truth
# `make build` reads).  The per-candidate qsf patching above is just the test.
if [ -n "${SEED_FILE:-}" ]; then
    printf "%s\n" "$BEST" > "$SEED_FILE"
    printf "${C_OK}[sweep]${C_RESET} stored seed $BEST → $SEED_FILE\n"
    # Fingerprint the exact netlist this seed was ranked on (sources +
    # macros from the winning job's generated qsf).  `make build` compares
    # and warns loudly: a seed on a changed netlist is a placement lottery.
    if [ -f "$TOOLS/netlist_hash.sh" ]; then
        . "$TOOLS/netlist_hash.sh"
        _bq="bld/${VARIANT}-s${BEST}/ap_core.qsf"
        [ -f "$_bq" ] || _bq="${PROJECT}.qsf"
        netlist_hash "$_bq" > "${SEED_FILE}.src"
        printf "${C_OK}[sweep]${C_RESET} netlist fingerprint → ${SEED_FILE}.src\n"
    fi
fi

# In-place mode rebuilds the best seed where it swept (MiSTer relies on the
# .sof landing here), through the same $QRUN wrapper/macros as the sweep fits.
# Container mode already produced a full bitstream per seed in
# bld/<variant>-s<best>/ and wrote the seed file, so `make build` is ready —
# no in-place rebuild needed.
if [ "$USE_CONTAINER" != 1 ]; then
    printf "${C_HEAD}[rebuild]${C_RESET} Final compile with seed $BEST...\n"
    sed -i "s/^set_global_assignment -name SEED .*/set_global_assignment -name SEED ${BEST}/" "${PROJECT}.qsf"
    rm -rf db incremental_db
    $QRUN bash -c "$FLOW" || true
    if [ ! -f "output_files/${PROJECT}.sof" ]; then
        printf "\n${C_ERR}[sweep] Final rebuild failed — no .sof produced${C_RESET}\n"; exit 1
    fi
fi

printf "\n${C_OK}[sweep] Done${C_RESET} — seed $BEST (${R_FMAX[$BEST]})\n"
