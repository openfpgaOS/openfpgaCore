#!/bin/bash
#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------
#
# Run a firmware Makefile target (bootloader / os.bin / install) inside an
# isolated Docker container with a newlib-equipped riscv64-unknown-elf toolchain.
# Mirrors quartus-container.sh and vexii-container.sh.
#
# Why this exists: many hosts (notably Homebrew's riscv64-elf-gcc on macOS)
# ship the compiler WITHOUT a C library, so `#include <string.h>` fails even
# in -ffreestanding mode.  This image bakes in a known-good toolchain so the
# firmware build is identical across Linux and macOS hosts.
#
# Usage: firmware-container.sh <make-targets-and-args...>
#        firmware-container.sh install                    # bootloader + firmware.mif
#        firmware-container.sh os.bin                     # OS image only
#        firmware-container.sh clean                      # wipe firmware build
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FW_DIR="$REPO/src/firmware/os"
IMG="${FIRMWARE_IMG:-openfpgaos-firmware}"

[ -d "$FW_DIR" ] || { echo "ERROR: $FW_DIR missing"; exit 1; }

# Build the image on first use (one-time) so `make bootloader` / `make os`
# just works on a fresh machine with only Docker installed.
if ! docker image inspect "$IMG" >/dev/null 2>&1; then
    echo "[firmware] building $IMG image (one-time, ~2-3 min)..."
    docker build -t "$IMG" -f "$REPO/tools/docker/Dockerfile.firmware" "$REPO/tools/docker/"
fi

# Pseudo-TTY only when our stdout is a terminal, so an interactive run keeps
# gcc's colored output but a redirected/piped run keeps logs clean.  The
# ${TTY[@]+"${TTY[@]}"} idiom is required for bash 3.2 (macOS default) under
# set -u — plain "${TTY[@]}" errors on an empty array there.
TTY=()
[ -t 1 ] && TTY=(-t)

# --user keeps generated objects/.elf/.bin/.mif owned by the host user.
# Repo bind-mounted at the SAME path so absolute paths inside the firmware
# Makefile (and any generated .d depfiles) resolve identically inside and
# outside the container.
exec docker run --rm ${TTY[@]+"${TTY[@]}"} \
  --user "$(id -u):$(id -g)" \
  -v "$REPO:$REPO" \
  --tmpfs /tmp:exec \
  -e HOME=/tmp \
  -w "$FW_DIR" \
  "$IMG" \
  make "$@"
