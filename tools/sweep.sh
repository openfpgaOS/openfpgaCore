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
#   host (USE_CONTAINER=0 — MiSTer / Quartus 17): one host quartus compile per
#     seed, MAXJOBS-throttled to 1 (the host Quartus concurrency segfault makes
#     parallel host fits unsafe).  Sweeps in BLD_DIR (or CWD), sed-patching the
#     seed into <PROJECT>.qsf, and does a final rebuild with the best seed.
#     Needs: BLD_DIR (optional), VARIANT_DEFS (optional verilog macros).
#
# Usage: sweep.sh <min> <max>
# Common env: PROJECT(=ap_core)  CLOCK_RE  MAXJOBS(=4)  USE_CONTAINER(=1)
#             VARIANT  SEED_FILE
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
declare -A R_WNS R_TNS R_FMAX

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

    # Shared inputs once: the CPU netlist + firmware are identical across seeds.
    make --no-print-directory cpu VARIANT="$VARIANT" >/dev/null
    make --no-print-directory bootloader >/dev/null

    run_one() {
        local s="$1" job="${VARIANT}-s$1"
        make --no-print-directory "bld/$job/ap_core.qsf" \
            VARIANT="$VARIANT" JOB="$job" SEED="$s" >/dev/null 2>&1
        bash "$TOOLS/quartus-container.sh" "$TARGET_DIR/bld/$job" >"bld/$job.log" 2>&1
    }

    for s in $(seq "$MIN" "$MAX"); do
        run_one "$s" &
        while [ "$(jobs -rp | wc -l)" -ge "$MAXJOBS" ]; do wait -n; done
    done
    wait

    for s in $(seq "$MIN" "$MAX"); do
        parse_sta "bld/${VARIANT}-s$s/output_files/${PROJECT}.sta.rpt"
        R_WNS[$s]=$_wns; R_TNS[$s]=$_tns; R_FMAX[$s]=$_fmax
    done
else
    # ── Host backend: serial in-place compile per seed ───────────────────
    [ -n "${BLD_DIR:-}" ] && cd "$BLD_DIR"
    mkdir -p output_files
    printf "${C_HEAD}[sweep]${C_RESET} Seeds $MIN-$MAX ($TOTAL builds, host serial)\n"
    [ -n "${VARIANT_DEFS:-}" ] && printf "${C_HEAD}[sweep]${C_RESET} Variant defs: ${VARIANT_DEFS}\n"

    VERILOG_MACROS=()
    if [ -n "${VARIANT_DEFS:-}" ]; then
        for def in ${VARIANT_DEFS}; do VERILOG_MACROS+=(--verilog_macro="${def}"); done
    fi

    n=0
    for s in $(seq "$MIN" "$MAX"); do
        n=$((n + 1))
        printf "${C_DIM}[%d/%d]${C_RESET} Seed %-3s " "$n" "$TOTAL" "$s"
        sed -i "s/^set_global_assignment -name SEED .*/set_global_assignment -name SEED ${s}/" "${PROJECT}.qsf"
        rm -rf db incremental_db
        if [ ${#VERILOG_MACROS[@]} -gt 0 ]; then
            {
                quartus_map ${PROJECT} "${VERILOG_MACROS[@]}" &&
                quartus_fit ${PROJECT} &&
                quartus_asm ${PROJECT} &&
                quartus_sta ${PROJECT}
            } > "output_files/seed_${s}_compile.log" 2>&1 || true
        else
            quartus_sh --flow compile ${PROJECT} > "output_files/seed_${s}_compile.log" 2>&1 || true
        fi
        parse_sta "output_files/${PROJECT}.sta.rpt"
        cp "output_files/${PROJECT}.sta.rpt" "output_files/seed_${s}_sta.log" 2>/dev/null || true
        R_WNS[$s]=$_wns; R_TNS[$s]=$_tns; R_FMAX[$s]=$_fmax
        if [ -n "$_wns" ]; then
            printf "${C_OK}%-10s${C_RESET} ${C_DIM}WNS %-8s TNS %s${C_RESET}\n" "$_fmax" "${_wns:--}" "${_tns:--}"
        else
            printf "${C_ERR}failed${C_RESET}\n"
        fi
    done
fi

# ── Rank by setup WNS on the target clock (robust; fmax ~ 1/(T-wns)) ─────
echo ""
printf "${C_HEAD}Results:${C_RESET}\n"
BEST=""; BEST_WNS=""
for s in $(seq "$MIN" "$MAX"); do
    w=${R_WNS[$s]:-}
    if [ -z "$w" ]; then printf "  ${C_ERR}seed %-3s failed${C_RESET}\n" "$s"; continue; fi
    printf "  seed %-3s Fmax %-10s WNS %-9s TNS %s\n" "$s" "${R_FMAX[$s]:--}" "$w" "${R_TNS[$s]:--}"
    if [ -z "$BEST" ] || awk -v a="$w" -v b="$BEST_WNS" 'BEGIN{exit !(a>b)}'; then
        BEST=$s; BEST_WNS=$w
    fi
done

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
fi

# Host mode rebuilds the best seed in place (MiSTer relies on the .sof landing
# here).  Container mode already produced a full bitstream per seed in
# bld/<variant>-s<best>/ and wrote the seed file, so `make build` is ready —
# no in-place rebuild needed.
if [ "$USE_CONTAINER" != 1 ]; then
    printf "${C_HEAD}[rebuild]${C_RESET} Final compile with seed $BEST...\n"
    sed -i "s/^set_global_assignment -name SEED .*/set_global_assignment -name SEED ${BEST}/" "${PROJECT}.qsf"
    rm -rf db incremental_db
    if [ -n "${VARIANT_DEFS:-}" ]; then
        quartus_map ${PROJECT} "${VERILOG_MACROS[@]}" &&
        quartus_fit ${PROJECT} && quartus_asm ${PROJECT} && quartus_sta ${PROJECT} || true
    else
        quartus_sh --flow compile ${PROJECT} || true
    fi
    if [ ! -f "output_files/${PROJECT}.sof" ]; then
        printf "\n${C_ERR}[sweep] Final rebuild failed — no .sof produced${C_RESET}\n"; exit 1
    fi
fi

printf "\n${C_OK}[sweep] Done${C_RESET} — seed $BEST (${R_FMAX[$BEST]})\n"
