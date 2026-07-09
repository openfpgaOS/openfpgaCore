#!/bin/bash
#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------

# openfpgaOS Deploy Script
#
# Usage:
#   ./deploy.sh                          Deploy to Pocket SD card (auto-detect)
#   ./deploy.sh /path/to/openfpgaOS-SDK  Update SDK runtime folder (no SD deploy)
#
set -e

# Always run from repo root (where this script lives)
cd "$(dirname "$0")"

GREEN='\033[92m'
CYAN='\033[96m'
YELLOW='\033[93m'
RESET='\033[0m'

DEST="$1"

# ════════════════════════════════════════════════════════════════════
# SDK runtime update — copy bitstream, loader, os.bin directly
# ════════════════════════════════════════════════════════════════════
if [ -n "$DEST" ] && [ -f "$DEST/src/sdk/sdk.mk" ]; then
    echo -e "${CYAN}Rebuilding OS...${RESET}"
    make os
    echo ""

    RUNTIME="$DEST/runtime"
    BITSTREAM="src/fpga/targets/pocket/output_files/ap_core.rbf"
    LOADER="src/fpga/targets/pocket/chip32/loader.bin"
    OS_BIN="src/firmware/os/bld/pocket/os.bin"

    echo -e "${CYAN}Updating SDK runtime: $RUNTIME${RESET}"
    mkdir -p "$RUNTIME"

    # Bitstream (reverse bits)
    if [ -f "$BITSTREAM" ]; then
        if [ ! -x tools/reverse_bits ]; then
            gcc -O2 -o tools/reverse_bits tools/reverse_bits.c
        fi
        tools/reverse_bits "$BITSTREAM" "$RUNTIME/bitstream.rbf_r"
        echo -e "  ${GREEN}✓${RESET} bitstream.rbf_r"
    else
        echo -e "  ${YELLOW}⚠${RESET} bitstream not found — run 'make fpga' first"
    fi

    # Loader — reassemble first (bass no-ops when loader.asm is unchanged)
    # so a stale loader.bin is never deployed.
    make -s -C src/fpga/targets/pocket/chip32 >/dev/null 2>&1 || true
    if [ -f "$LOADER" ]; then
        cp "$LOADER" "$RUNTIME/"
        echo -e "  ${GREEN}✓${RESET} loader.bin"
    else
        echo -e "  ${YELLOW}⚠${RESET} loader.bin not found — run 'make -C src/fpga/targets/pocket/chip32'"
    fi

    # OS
    if [ -f "$OS_BIN" ]; then
        cp "$OS_BIN" "$RUNTIME/"
        echo -e "  ${GREEN}✓${RESET} os.bin"
    else
        echo -e "  ${YELLOW}⚠${RESET} os.bin not found"
    fi

    echo -e "\n${GREEN}SDK runtime updated!${RESET}"
    exit 0
fi

# ════════════════════════════════════════════════════════════════════
# SD card deploy — rebuild firmware, package, then copy to SD
# ════════════════════════════════════════════════════════════════════
echo -e "${CYAN}Rebuilding OS...${RESET}"
make os
echo ""
echo -e "${CYAN}Packaging build/ from source artifacts...${RESET}"
make package
echo ""

DID_MOUNT=0

find_pocket_sd() {
    for mount in /run/media/"$USER"/*; do
        if [ -d "$mount/Cores" ] && [ -d "$mount/Assets" ]; then
            echo "$mount"
            return
        fi
    done
}

POCKET_SD="$(find_pocket_sd)"

if [ -z "$POCKET_SD" ]; then
    echo "No mounted Pocket SD card found, looking for unmounted partitions..."
    for dev in /dev/sd*1 /dev/mmcblk*p1; do
        [ -b "$dev" ] || continue
        if mountpoint -q "$dev" 2>/dev/null || mount | grep -q "^$dev "; then
            continue
        fi
        base_dev="$(lsblk -no PKNAME "$dev" 2>/dev/null)"
        [ -n "$base_dev" ] || continue
        if [ "$(cat /sys/block/"$base_dev"/removable 2>/dev/null)" = "1" ]; then
            echo "Found unmounted removable partition: $dev — mounting..."
            udisksctl mount -b "$dev" --no-user-interaction
            sleep 1
            POCKET_SD="$(find_pocket_sd)"
            if [ -n "$POCKET_SD" ]; then
                DID_MOUNT=1
                break
            else
                udisksctl unmount -b "$dev" --no-user-interaction
            fi
        fi
    done
fi

if [ -z "$POCKET_SD" ]; then
    echo -e "${YELLOW}No SD card found.${RESET}"
    echo "Insert SD card and run: ./deploy.sh"
    exit 0
fi

echo -e "${CYAN}Deploying to SD card: $POCKET_SD${RESET}"
rsync -av --checksum build/ "$POCKET_SD/"
sync

if [ "$DID_MOUNT" = "1" ]; then
    echo "Unmounting $POCKET_SD..."
    udisksctl unmount -b "$dev" --no-user-interaction
fi

echo -e "\n${GREEN}Deploy complete!${RESET}"
