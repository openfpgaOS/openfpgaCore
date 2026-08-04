#!/bin/bash
#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------
#
# Hardware/OS contract tripwire.
#
# Hashes the files that DEFINE the fw<->os interface (register map, memory
# boundaries, app link layout, caps ABI) and compares against the blessed
# hash committed in src/firmware/os/.contract.md5.  Runs from the os.bin
# build: if the contract surface changed and the hash wasn't re-blessed,
# the build FAILS -- so a boundary or register change can never ship
# silently.  The change author must then decide, explicitly:
#
#   breaking change  -> bump HW_CONTRACT_REV (src/fpga/common/axi_periph_slave.v)
#                       AND OS_EXPECTED_HW_CONTRACT (src/firmware/os/hal/regs.h),
#                       then re-bless
#   additive change  -> just re-bless (the commit diff shows the blessing,
#                       which reviewers read as "author claims compatible")
#
#   Re-bless:  tools/contract-check.sh --bless
#
# Semantics-only changes (same fields, new meaning) don't alter these
# files; those still rely on the manual bump rule.
#
set -e
cd "$(dirname "$0")/.."

# The contract surface.  Implementation files (axi_periph_slave.v etc.)
# are deliberately NOT hashed -- they churn constantly for non-contract
# reasons; the interface is what these files define.
CONTRACT_FILES=(
    src/firmware/os/hal/regs.h
    src/firmware/os/targets/pocket/target_platform.h
    src/firmware/os/targets/mister/target_platform.h
    src/firmware/os/targets/sim/target_platform.h
    src/firmware/api/app.ld
    src/firmware/api/of_caps.h
    src/firmware/api/of_app_abi.h
)

BLESSED=src/firmware/os/.contract.md5

# A missing surface file (rename/move) or a missing md5sum must be a hard
# error: silently skipping it would hash fewer files, and the next
# --bless would permanently drop it from coverage.
command -v md5sum >/dev/null 2>&1 || {
    echo "contract-check: md5sum not found on PATH" >&2; exit 1; }
for f in "${CONTRACT_FILES[@]}"; do
    [ -f "$f" ] || { echo "contract-check: MISSING contract file $f (renamed? update CONTRACT_FILES)" >&2; exit 1; }
done

hash_now() {
    for f in "${CONTRACT_FILES[@]}"; do
        md5sum "$f"
    done | md5sum | cut -d' ' -f1
}

NOW=$(hash_now)

if [ "$1" = "--bless" ]; then
    echo "$NOW" > "$BLESSED"
    echo "contract-check: blessed $NOW"
    exit 0
fi

if [ ! -f "$BLESSED" ]; then
    echo "contract-check: no blessed hash yet — run tools/contract-check.sh --bless" >&2
    exit 1
fi

WANT=$(cat "$BLESSED")
if [ "$NOW" != "$WANT" ]; then
    echo "" >&2
    echo "contract-check: FW/OS CONTRACT SURFACE CHANGED (hash $NOW, blessed $WANT)" >&2
    echo "  A file defining the register map / memory boundaries / app layout changed." >&2
    echo "  Decide explicitly, then re-bless:" >&2
    echo "    breaking -> bump HW_CONTRACT_REV (axi_periph_slave.v) + OS_EXPECTED_HW_CONTRACT (regs.h)" >&2
    echo "    additive -> no bump needed" >&2
    echo "  then: tools/contract-check.sh --bless   (commit .contract.md5 with the change)" >&2
    echo "" >&2
    exit 1
fi
exit 0
