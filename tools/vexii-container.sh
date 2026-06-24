#!/bin/bash
#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------
#
# Generate the VexiiRiscv CPU netlist for ONE variant inside an isolated Docker
# container (mirrors quartus-container.sh, but for the sbt/SpinalHDL toolchain).
# A fresh checkout needs only Docker + internet — no host JDK/sbt.
#
# The whole toolchain is baked into the image (openfpgaos-vexii), which is built
# on demand the first time.  Scala/SpinalHDL deps download once into a PERSISTENT,
# host-owned HOME (tools/.vexii-home), so the first generation is slow (downloads)
# and the rest are fast/offline.  --user keeps the generated VexiiRiscv_<variant>.v
# owned by the host user, and the repo is bind-mounted at the SAME path so
# generate_vexii.sh's relative paths resolve identically inside the container.
#
# Usage: vexii-container.sh <variant>          (os25 | os30 | mister)
set -euo pipefail

VARIANT="${1:?usage: vexii-container.sh <variant>}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMG="${VEXII_IMG:-openfpgaos-vexii}"
VEXII_DIR="$REPO/src/fpga/vendor/vexriscv/VexiiRiscv"
HOME_CACHE="$REPO/tools/.vexii-home"   # persistent sbt/ivy/coursier dep cache (gitignored)

if [ ! -f "$VEXII_DIR/build.sbt" ]; then
    echo "ERROR: $VEXII_DIR/build.sbt missing — VexiiRiscv submodule is not checked out."
    echo "       Run: git submodule update --init --recursive"
    exit 1
fi
mkdir -p "$HOME_CACHE"

# Build the image on first use (one-time) so `make cpu` just works on a fresh
# machine with only Docker installed.
if ! docker image inspect "$IMG" >/dev/null 2>&1; then
    echo "[vexii] building $IMG image (one-time, ~2-3 min)..."
    docker build -t "$IMG" -f "$REPO/tools/docker/Dockerfile.vexii" "$REPO/tools/docker/"
fi

# Pseudo-TTY only when our stdout is a terminal, so an interactive run keeps sbt's
# colored output but a redirected/piped run keeps logs clean.
TTY=()
[ -t 1 ] && TTY=(-t)

# --user => host-owned outputs.  HOME is a persistent mounted dir (the dep cache),
# NOT tmpfs — that is the whole point: download Scala/SpinalHDL once, reuse it.
exec docker run --rm ${TTY[@]+"${TTY[@]}"} \
  --user "$(id -u):$(id -g)" \
  -v "$REPO:$REPO" \
  -v "$HOME_CACHE:/vexiihome" \
  --tmpfs /tmp:exec \
  -e HOME=/vexiihome \
  -w "$VEXII_DIR" \
  "$IMG" \
  bash -c "bash ../generate_vexii.sh '$VARIANT'"
