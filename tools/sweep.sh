#!/bin/bash
# Seed sweep for Quartus fitter
#
# Usage: ./sweep.sh [MIN] [MAX]
#   MIN   First seed (default: 1)
#   MAX   Last seed  (default: 30)
#
# Compiles each seed sequentially, tracks Fmax, rebuilds with the best.

set -e

PROJECT=ap_core
MIN=${1:-1}
MAX=${2:-30}
RESULTS=output_files/seed_map.log

# Colors
C_HEAD='\033[1m'
C_OK='\033[32m'
C_WARN='\033[33m'
C_ERR='\033[31m'
C_DIM='\033[2m'
C_RESET='\033[0m'

TOTAL=$(( MAX - MIN + 1 ))
printf "${C_HEAD}[sweep]${C_RESET} Seeds ${MIN}-${MAX} (${TOTAL} builds)\n\n"

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

    # Clean and compile
    rm -rf db incremental_db
    quartus_sh --flow compile ${PROJECT} > "output_files/seed_${seed}_compile.log" 2>&1
    rc=$?

    # Extract Fmax
    fmax="failed"
    if [ $rc -eq 0 ]; then
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
printf "${C_HEAD}Best: seed ${BEST_SEED} (${BEST_FMAX})${C_RESET}\n"

# Rebuild with best seed
printf "${C_HEAD}[rebuild]${C_RESET} Final compile with seed ${BEST_SEED}...\n"
sed -i "s/^set_global_assignment -name SEED .*/set_global_assignment -name SEED ${BEST_SEED}/" ${PROJECT}.qsf
rm -rf db incremental_db
quartus_sh --flow compile ${PROJECT}

printf "\n${C_OK}[sweep] Done${C_RESET} — seed ${BEST_SEED} (${BEST_FMAX})\n"
