#!/bin/bash
# openfpgaOS Deploy Script
#
# Usage:
#   ./deploy.sh                          Deploy to Pocket SD card (auto-detect)
#   ./deploy.sh /path/to/openfpgaOS-SDK  Update SDK runtime + deploy to SD card
#
# Both paths: package build/ from source artifacts, then deploy.
# No rebuilding — just copies what's already been compiled.
#
set -e

GREEN='\033[92m'
CYAN='\033[96m'
YELLOW='\033[93m'
RESET='\033[0m'

DEST="$1"

# ════════════════════════════════════════════════════════════════════
# Step 1: Always package build/ from source artifacts
# ════════════════════════════════════════════════════════════════════
echo -e "${CYAN}Packaging build/ from source artifacts...${RESET}"
make package
echo ""

# ════════════════════════════════════════════════════════════════════
# Step 2 (optional): Update SDK runtime if SDK path given
# ════════════════════════════════════════════════════════════════════
if [ -n "$DEST" ] && [ -f "$DEST/src/sdk/sdk.mk" ]; then
    echo -e "${CYAN}Updating SDK: $DEST${RESET}"

    BUILD_DIR="build/Cores/ThinkElastic.openfpgaOS"
    RUNTIME="$DEST/runtime"
    mkdir -p "$RUNTIME"

    cp "$BUILD_DIR/bitstream.rbf_r" "$RUNTIME/" && \
        echo -e "  ${GREEN}✓${RESET} bitstream.rbf_r"
    cp "$BUILD_DIR/loader.bin" "$RUNTIME/" && \
        echo -e "  ${GREEN}✓${RESET} loader.bin"
    cp "build/Assets/openfpgaos/common/os.bin" "$RUNTIME/" && \
        echo -e "  ${GREEN}✓${RESET} os.bin"

    # Core configs
    mkdir -p "$DEST/dist/sdk/core"
    for f in "$BUILD_DIR"/*.json "$BUILD_DIR"/icon.bin; do
        [ -f "$f" ] && cp "$f" "$DEST/dist/sdk/core/"
    done
    echo -e "  ${GREEN}✓${RESET} Core configs"

    # Platform files
    mkdir -p "$DEST/dist/sdk/platform/_images"
    [ -f build/Platforms/openfpgaos.json ] && \
        cp build/Platforms/openfpgaos.json "$DEST/dist/sdk/platform/"
    [ -f build/Platforms/_images/openfpgaos.bin ] && \
        cp build/Platforms/_images/openfpgaos.bin "$DEST/dist/sdk/platform/_images/"
    echo -e "  ${GREEN}✓${RESET} Platform files"

    # SDK API headers
    mkdir -p "$DEST/src/sdk/include" "$DEST/src/sdk/libc"
    for f in src/firmware/api/of*.h; do
        [ -f "$f" ] && cp "$f" "$DEST/src/sdk/include/"
    done
    [ -f "$DEST/src/sdk/include/of_libc.h" ] && mv "$DEST/src/sdk/include/of_libc.h" "$DEST/src/sdk/libc/"
    echo -e "  ${GREEN}✓${RESET} SDK API headers"

    # Libc headers
    for f in src/firmware/api/libc_include/*.h; do
        [ -f "$f" ] && cp "$f" "$DEST/src/sdk/libc/"
    done
    for d in src/firmware/api/libc_include/*/; do
        [ -d "$d" ] || continue
        subdir=$(basename "$d")
        mkdir -p "$DEST/src/sdk/libc/$subdir"
        cp "$d"*.h "$DEST/src/sdk/libc/$subdir/" 2>/dev/null
    done
    sed -i 's|"../of_\([^"]*\)"|"of_\1"|g' "$DEST"/src/sdk/libc/*.h 2>/dev/null
    echo -e "  ${GREEN}✓${RESET} Libc headers"

    echo -e "  ${GREEN}✓${RESET} SDK updated"
    echo ""
fi

# ════════════════════════════════════════════════════════════════════
# Step 3: Always deploy to Pocket SD card
# ════════════════════════════════════════════════════════════════════
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
    echo -e "${YELLOW}No SD card found — SDK updated but SD card not deployed.${RESET}"
    echo "Insert SD card and run: ./deploy.sh"
    exit 0
fi

echo -e "${CYAN}Deploying to SD card: $POCKET_SD${RESET}"

# If SDK path given, use SDK deploy (includes apps)
if [ -n "$DEST" ] && [ -f "$DEST/scripts/deploy.sh" ]; then
    bash "$DEST/scripts/deploy.sh" "$POCKET_SD"
else
    # Direct deploy from build/
    rsync -av --checksum build/ "$POCKET_SD/"
fi

sync

if [ "$DID_MOUNT" = "1" ]; then
    echo "Unmounting $POCKET_SD..."
    udisksctl unmount -b "$dev" --no-user-interaction
fi

echo -e "\n${GREEN}Deploy complete!${RESET}"
