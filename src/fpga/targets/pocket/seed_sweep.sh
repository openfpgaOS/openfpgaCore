#!/bin/bash
# Seed sweep — clean build for each seed, print results live
PROJECT=ap_core
QSF=${PROJECT}.qsf
QUARTUS=/home/alberto/altera_lite/25.1std/quartus/bin
export PATH=${QUARTUS}:${PATH}

SEEDS="${1:-1 2 3 4 5 6 7 8 9 10}"

printf "\n%-6s %-12s %-12s %-12s %s\n" "Seed" "SDRAM Fmax" "Video Fmax" "Slack" "Status"
printf "%-6s %-12s %-12s %-12s %s\n" "----" "----------" "----------" "-----" "------"

for s in $SEEDS; do
    # Set seed in QSF
    sed -i "s/^set_global_assignment -name SEED .*/set_global_assignment -name SEED $s/" $QSF

    # Clean build
    rm -rf db incremental_db
    quartus_sh --flow compile $PROJECT > /dev/null 2>&1
    rc=$?

    if [ $rc -ne 0 ]; then
        printf "%-6s %-12s %-12s %-12s %s\n" "$s" "-" "-" "-" "COMPILE FAIL"
        continue
    fi

    # Extract Fmax for SDRAM (mp_ram general[0]) and Video (mp1 general[3])
    STA=output_files/${PROJECT}.sta.rpt
    SDRAM_FMAX=$(grep -A12 "^; Fmax.*Restricted" $STA | grep "mp_ram.*general\[0\]" | head -1 | awk -F';' '{print $2}' | xargs)
    VIDEO_FMAX=$(grep -A12 "^; Fmax.*Restricted" $STA | grep "mp1.*general\[3\]" | head -1 | awk -F';' '{print $2}' | xargs)

    # Extract worst setup slack for SDRAM
    SLACK=$(grep -A10 "^; Slow 1100mV 85C Model Setup Summary" $STA | grep "mp_ram.*general\[0\]" | head -1 | awk -F';' '{print $3}' | xargs)

    if echo "$SLACK" | grep -q "^-"; then
        STATUS="FAIL"
    else
        STATUS="PASS"
    fi

    printf "%-6s %-12s %-12s %-12s %s\n" "$s" "$SDRAM_FMAX" "$VIDEO_FMAX" "$SLACK" "$STATUS"

    # Save logs
    cp $STA output_files/seed_${s}_sta.log 2>/dev/null
    cp output_files/${PROJECT}.fit.rpt output_files/seed_${s}_fit.log 2>/dev/null
done

# Restore default seed
sed -i "s/^set_global_assignment -name SEED .*/set_global_assignment -name SEED 7/" $QSF
echo ""
echo "Default seed 7 restored."
