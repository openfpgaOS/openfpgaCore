#!/bin/bash
#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------

# Seed sweep for Quartus fitter
#
# Usage: ./sweep.sh [MIN] [MAX]
#   MIN   First seed (default: 1)
#   MAX   Last seed  (default: 30)
#
# Compiles each seed sequentially, tracks Fmax, rebuilds with the best.
#
# IMPORTANT: "best" means highest restricted Fmax, regardless of whether
# the seed meets the 100 MHz timing target. Negative-slack seeds are
# included in the ranking -- we'd rather ship the closest-to-meeting
# bitstream than fail the build outright. Quartus is allowed to exit
# with timing warnings; only a hard fitter/compile crash counts as
# "failed" and removes a seed from consideration.

# Note: no `set -e`. We deliberately want the script to keep going past
# Quartus warnings (timing not met) and to record every seed's result.

PROJECT=ap_core
MIN=${1:-1}
MAX=${2:-30}
RESULTS=output_files/seed_map.log
VERILOG_MACROS=()
if [ -n "${VARIANT_DEFS:-}" ]; then
    for def in ${VARIANT_DEFS}; do
        VERILOG_MACROS+=(--verilog_macro="${def}")
    done
fi

# Colors
C_HEAD='\033[1m'
C_OK='\033[32m'
C_WARN='\033[33m'
C_ERR='\033[31m'
C_DIM='\033[2m'
C_RESET='\033[0m'

TOTAL=$(( MAX - MIN + 1 ))
printf "${C_HEAD}[sweep]${C_RESET} Seeds ${MIN}-${MAX} (${TOTAL} builds)\n\n"
if [ -n "${VARIANT_DEFS:-}" ]; then
    printf "${C_HEAD}[sweep]${C_RESET} Variant defs: ${VARIANT_DEFS}\n\n"
fi

mkdir -p output_files
echo "seed,fmax_mhz" > "${RESULTS}"

BEST_SEED=""
BEST_FMAX="0"
COUNT=0

for seed in $(seq ${MIN} ${MAX}); do
    COUNT=$((COUNT + 1))
    printf "${C_DIM}[${COUNT}/${TOTAL}]${C_RESET} Seed %-3s " "${seed}"

    # Patch seed in QSF
    sed -i "s/^set_global_assignment -name SEED .*/set_global_assignment -name SEED ${seed}/" ${PROJECT}.qsf

    # Clean and compile. Quartus may exit non-zero when timing isn't
    # met; we don't care -- as long as the STA report exists with an
    # Fmax line, we'll consider this seed for the "best" ranking.
    rm -rf db incremental_db
    if [ ${#VERILOG_MACROS[@]} -gt 0 ]; then
        {
            quartus_map ${PROJECT} "${VERILOG_MACROS[@]}" &&
            quartus_fit ${PROJECT} &&
            quartus_asm ${PROJECT} &&
            quartus_sta ${PROJECT}
        } > "output_files/seed_${seed}_compile.log" 2>&1 || true
    else
        quartus_sh --flow compile ${PROJECT} > "output_files/seed_${seed}_compile.log" 2>&1 || true
    fi

    # Extract restricted Fmax for the SDRAM clock domain (mp_ram general[0]).
    # The STA report exists whether or not timing was met, so we read it
    # unconditionally and only mark a seed "failed" if the report is
    # missing or unparseable (= the fitter crashed before STA ran).
    fmax="failed"
    if [ -f "output_files/${PROJECT}.sta.rpt" ]; then
        fmax=$(grep -E "^\; [0-9]" "output_files/${PROJECT}.sta.rpt" 2>/dev/null \
            | grep "mp_ram.*general\[0\]" | head -1 \
            | awk -F';' '{print $2}' | xargs)
        [ -z "$fmax" ] && fmax="failed"
    fi

    echo "${seed},${fmax}" >> "${RESULTS}"
    cp "output_files/${PROJECT}.sta.rpt" "output_files/seed_${seed}_sta.log" 2>/dev/null || true

    # Track best
    if [ "$fmax" != "failed" ]; then
        printf "${C_OK}%s${C_RESET}\n" "${fmax}"
        fmax_num=$(echo "$fmax" | sed 's/ MHz//' | tr -d ' ')
        best_num=$(echo "$BEST_FMAX" | sed 's/ MHz//' | tr -d ' ')
        if [ "$(echo "$fmax_num > $best_num" | bc -l 2>/dev/null || echo 0)" = "1" ]; then
            BEST_SEED=${seed}
            BEST_FMAX=${fmax}
        fi
    else
        printf "${C_ERR}failed${C_RESET}\n"
    fi
done

# Summary
echo ""
printf "${C_HEAD}Results:${C_RESET}\n"
tail -n +2 "${RESULTS}" | sort -t, -k2 -rn | while IFS=, read seed fmax; do
    if echo "$fmax" | grep -qE "^.*(9[5-9]|[1-9][0-9]{2})"; then
        printf "  ${C_OK}Seed %-3s  %s${C_RESET}\n" "$seed" "$fmax"
    elif echo "$fmax" | grep -qE "^.*(9[0-4])"; then
        printf "  ${C_WARN}Seed %-3s  %s${C_RESET}\n" "$seed" "$fmax"
    else
        printf "  ${C_DIM}Seed %-3s  %s${C_RESET}\n" "$seed" "$fmax"
    fi
done

if [ -z "$BEST_SEED" ]; then
    printf "\n${C_ERR}All seeds failed${C_RESET}\n"
    exit 1
fi

echo ""
printf "${C_HEAD}Best: seed ${BEST_SEED} (${BEST_FMAX})${C_RESET}"
# Warn if even the best seed doesn't meet the 100 MHz target -- but
# still ship it. Closer-to-meeting beats not-shipping-at-all.
best_num=$(echo "$BEST_FMAX" | sed 's/ MHz//' | tr -d ' ')
if [ "$(echo "$best_num < 100" | bc -l 2>/dev/null || echo 0)" = "1" ]; then
    printf " ${C_WARN}(below 100 MHz target -- shipping anyway)${C_RESET}"
fi
echo ""

# Rebuild with best seed. Tolerate timing-not-met -- Quartus will exit
# non-zero with a warning but still produce a valid bitstream.
printf "${C_HEAD}[rebuild]${C_RESET} Final compile with seed ${BEST_SEED}...\n"
sed -i "s/^set_global_assignment -name SEED .*/set_global_assignment -name SEED ${BEST_SEED}/" ${PROJECT}.qsf
rm -rf db incremental_db
if [ ${#VERILOG_MACROS[@]} -gt 0 ]; then
    quartus_map ${PROJECT} "${VERILOG_MACROS[@]}" &&
    quartus_fit ${PROJECT} &&
    quartus_asm ${PROJECT} &&
    quartus_sta ${PROJECT} || true
else
    quartus_sh --flow compile ${PROJECT} || true
fi

# Verify the bitstream actually came out -- this is the real success
# criterion, not Quartus's exit code.
if [ ! -f "output_files/${PROJECT}.sof" ]; then
    printf "\n${C_ERR}[sweep] Final rebuild failed -- no .sof produced${C_RESET}\n"
    exit 1
fi

printf "\n${C_OK}[sweep] Done${C_RESET} — seed ${BEST_SEED} (${BEST_FMAX})\n"
