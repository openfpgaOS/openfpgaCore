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
source "$REPO/tools/oci.sh"   # $OCI + oci_run/oci_build/oci_image_exists/oci_rm_force

if [ ! -f "$VEXII_DIR/build.sbt" ]; then
    echo "ERROR: $VEXII_DIR/build.sbt missing — VexiiRiscv submodule is not checked out."
    echo "       Run: git submodule update --init --recursive"
    exit 1
fi
mkdir -p "$HOME_CACHE"

# Build the image on first use (one-time) so `make cpu` just works on a fresh
# machine with only Docker installed.
if ! oci_image_exists "$IMG"; then
    echo "[vexii] building $IMG image with $OCI (one-time, ~2-3 min)..."
    oci_build -t "$IMG" -f "$REPO/tools/docker/Dockerfile.vexii" "$REPO/tools/docker/"
fi

# Pseudo-TTY only when our stdout is a terminal, so an interactive run keeps sbt's
# colored output but a redirected/piped run keeps logs clean.
TTY=()
[ -t 1 ] && TTY=(-t)

# --user => host-owned outputs.  HOME is a persistent mounted dir (the dep cache),
# NOT tmpfs — that is the whole point: download Scala/SpinalHDL once, reuse it.
# Name the container + tear it down from a trap so Ctrl+C (SIGINT) cleanly
# kills the generation instead of orphaning it: `docker run` does not forward
# signals to the container when a -t TTY is allocated without -i.  Run in the
# background and `wait` (interruptible) so the trap fires on INT/TERM/EXIT.
CNAME="ofpgaos-vexii-$$-$VARIANT"
trap 'oci_rm_force "$CNAME"' EXIT INT TERM
# Apple `container` caps each VM at ~1 GiB; the sbt/SpinalHDL JVM needs more.
# Docker shares host RAM, so QMEM stays empty there.
QMEM=()
oci_is_apple && QMEM=(--memory "${CONTAINER_MEM:-8g}")
oci_run --rm --name "$CNAME" ${QMEM[@]+"${QMEM[@]}"} ${TTY[@]+"${TTY[@]}"} \
  --user "$(id -u):$(id -g)" \
  -v "$REPO:$REPO" \
  -v "$HOME_CACHE:/vexiihome" \
  --tmpfs /tmp:exec \
  -e HOME=/vexiihome \
  -w "$VEXII_DIR" \
  "$IMG" \
  bash -c "bash ../generate_vexii.sh '$VARIANT'" &
wait $!
