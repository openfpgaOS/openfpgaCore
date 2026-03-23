#!/bin/bash
# openfpgaOS Deploy Script
#
# Usage:
#   ./deploy                          Deploy to Pocket SD card (auto-detect)
#   ./deploy /path/to/openfpgaOS-SDK  Deploy runtime + apps to SDK folder
#
set -e

GREEN='\033[92m'
CYAN='\033[96m'
RESET='\033[0m'

DEST="$1"

# ════════════════════════════════════════════════════════════════════
# Deploy to SDK folder
# ════════════════════════════════════════════════════════════════════
if [ -n "$DEST" ] && [ -f "$DEST/src/sdk/sdk.mk" ]; then
    echo -e "${CYAN}Deploying to SDK: $DEST${RESET}"

    # ── Runtime (gitignored binaries) ─────────────────────────────
    RUNTIME="$DEST/runtime"
    mkdir -p "$RUNTIME"

    # Bitstream (pre-convert to RBF_R)
    RBF=src/fpga/targets/pocket/output_files/ap_core.rbf
    if [ -f "$RBF" ]; then
        if [ ! -x tools/reverse_bits ]; then
            gcc -O2 -o tools/reverse_bits tools/reverse_bits.c
        fi
        tools/reverse_bits "$RBF" "$RUNTIME/bitstream.rbf_r"
        echo -e "  ${GREEN}✓${RESET} bitstream.rbf_r"
    fi

    # Loader
    [ -f src/chip32/pocket/loader.bin ] && cp src/chip32/pocket/loader.bin "$RUNTIME/" && \
        echo -e "  ${GREEN}✓${RESET} loader.bin"

    # OS binary
    [ -f src/firmware/os/os.bin ] && cp src/firmware/os/os.bin "$RUNTIME/" && \
        echo -e "  ${GREEN}✓${RESET} os.bin"

    # ── Core configs → dist/sdk/core/ ─────────────────────────────
    mkdir -p "$DEST/dist/sdk/core"
    for f in core.json data.json audio.json video.json input.json interact.json variants.json; do
        [ -f "dist/core/$f" ] && cp "dist/core/$f" "$DEST/dist/sdk/core/"
    done
    [ -f dist/core/icon.bin ] && cp dist/core/icon.bin "$DEST/dist/sdk/core/"
    echo -e "  ${GREEN}✓${RESET} Core configs"

    # ── Platform files → dist/sdk/platform/ ────────────────────────
    mkdir -p "$DEST/dist/sdk/platform/_images"
    [ -f dist/platforms/openfpgaos.json ] && \
        cp dist/platforms/openfpgaos.json "$DEST/dist/sdk/platform/"
    [ -f dist/platforms/_images/openfpgaos.bin ] && \
        cp dist/platforms/_images/openfpgaos.bin "$DEST/dist/sdk/platform/_images/"
    echo -e "  ${GREEN}✓${RESET} Platform files"

    # ── SDK API headers (committed) ────────────────────────────────
    mkdir -p "$DEST/src/sdk/include" "$DEST/src/sdk/libc"
    for f in src/firmware/api/of*.h; do
        [ -f "$f" ] && cp "$f" "$DEST/src/sdk/include/"
    done
    # Move libc jump table to libc/
    [ -f "$DEST/src/sdk/include/of_libc.h" ] && mv "$DEST/src/sdk/include/of_libc.h" "$DEST/src/sdk/libc/"
    echo -e "  ${GREEN}✓${RESET} SDK API headers"

    # ── Libc headers (must match of_libc.h signatures) ───────────
    for f in src/firmware/api/libc_include/*.h; do
        [ -f "$f" ] && cp "$f" "$DEST/src/sdk/libc/"
    done
    # Also copy subdirectories (sys/stat.h, etc.)
    for d in src/firmware/api/libc_include/*/; do
        [ -d "$d" ] || continue
        subdir=$(basename "$d")
        mkdir -p "$DEST/src/sdk/libc/$subdir"
        cp "$d"*.h "$DEST/src/sdk/libc/$subdir/" 2>/dev/null
    done
    # Fix include paths: OS uses "../of_*.h", SDK has them in include/
    sed -i 's|"../of_\([^"]*\)"|"of_\1"|g' "$DEST"/src/sdk/libc/*.h 2>/dev/null
    echo -e "  ${GREEN}✓${RESET} Libc headers"

    # ── Pre-built CRT objects (of_posix.o, of_midi.o) ────────────
    # Rebuild from SDK source against the just-updated headers.
    mkdir -p "$DEST/src/sdk/crt"
    CROSS=$(which riscv64-unknown-elf-gcc >/dev/null 2>&1 && echo riscv64-unknown-elf- || echo riscv64-elf-)
    GCC_INC=$(${CROSS}gcc -print-file-name=include)
    CRT_CFLAGS="-march=rv32imafc -mabi=ilp32f -O2 -Wall -Wextra -ffreestanding -nostdlib -nostartfiles -ffunction-sections -fdata-sections -fno-builtin -nostdinc -I$DEST/src/sdk/libc -I$DEST/src/sdk/include -isystem $GCC_INC"
    for src_c in "$DEST"/src/sdk/of_posix.c "$DEST"/src/sdk/of_midi.c; do
        [ -f "$src_c" ] || continue
        obj="$DEST/src/sdk/crt/$(basename "${src_c%.c}.o")"
        ${CROSS}gcc $CRT_CFLAGS -c -o "$obj" "$src_c" 2>/dev/null
        echo -e "  ${GREEN}✓${RESET} Rebuilt $(basename "$obj")"
    done

    # App source lives in the SDK repo (single source of truth).
    # deploy.sh only syncs runtime, configs, and API headers.
    echo -e "  ${GREEN}✓${RESET} Apps maintained in SDK (not synced)"


    echo -e "\n${GREEN}SDK deploy complete!${RESET}"
    exit 0
fi

# ════════════════════════════════════════════════════════════════════
# Deploy to Pocket SD card (original behavior)
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
    echo "Error: No Analogue Pocket SD card found"
    echo "Usage: $0 [/path/to/openfpgaOS-SDK]"
    exit 1
fi

echo "Found Pocket SD card at: $POCKET_SD"

make fw

rsync -av --checksum build/ "$POCKET_SD/"

sync

if [ "$DID_MOUNT" = "1" ]; then
    echo "Unmounting $POCKET_SD..."
    udisksctl unmount -b "$dev" --no-user-interaction
fi

echo -e "${GREEN}Deploy complete${RESET}"
