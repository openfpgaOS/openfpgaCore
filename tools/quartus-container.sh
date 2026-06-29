#!/bin/bash
#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------
#
# Run a Quartus full compile (map -> fit -> asm -> sta) for ONE build job inside
# an isolated Docker container.  Each container gets its OWN $HOME and /tmp, so
# N builds run concurrently WITHOUT the shared-state segfault that crashes
# concurrent Quartus on a single host (the per-user ~/.altera.quartus and the
# shared /tmp scratch are the contended resources; here each job gets private
# ones).  Combined with the per-job bld/<job>/ dir (own db/ + output_files/),
# this gives full build isolation: 4+ fits of the SAME variant at once.
#
# Two image modes — preferred FIRST:
#   1. Baked image   (openfpgaos-quartus-full) — Quartus is inside the image.
#      No bind-mount, no host install needed.  Built once per machine via
#      `make quartus-image SRC=<altera_lite-path>` / tools/build-quartus-image.sh.
#      Use this on macOS / fresh CI / contributor machines.
#   2. Bind-mount    (openfpgaos-quartus)      — host Quartus mounted read-only.
#      Smaller image, but every machine needs $ALTERA_ROOT pointing at an
#      installed Quartus tree.  Original setup; still works on Linux dev boxes.
#
# The host must have already generated <build-dir>/ap_core.qsf (+ .qpf) and the
# CPU netlist + firmware.mif it references — all by ABSOLUTE path, so they
# resolve identically inside the container (the repo is mounted at the same path).
#
# Usage: quartus-container.sh <absolute-build-dir>
set -euo pipefail

BDIR="${1:?usage: quartus-container.sh <absolute-build-dir>}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMG_FULL="${QUARTUS_FULL_IMG:-openfpgaos-quartus-full}"
IMG_BIND="${QUARTUS_IMG:-openfpgaos-quartus}"
ALTERA="${ALTERA_ROOT:-/home/alberto/altera_lite}"
QROOT="$ALTERA/25.1std/quartus"
source "$REPO/tools/oci.sh"   # $OCI + oci_run/oci_build/oci_image_exists/oci_rm_force

[ -f "$BDIR/ap_core.qsf" ] || { echo "ERROR: $BDIR/ap_core.qsf missing (generate it first)"; exit 1; }

# Allocate a pseudo-TTY only when OUR stdout is a terminal, so an interactive
# `make build` keeps Quartus's colored output (tools disable color when they see
# a pipe instead of a tty).  When stdout is redirected — build-all/sweep write
# to bld/<job>.log — we omit -t so the logs stay clean (no \r / escape codes).
# The ${TTY[@]+"${TTY[@]}"} idiom is required for bash 3.2 (macOS default) under
# set -u — plain "${TTY[@]}" errors on an empty array there.
TTY=()
[ -t 1 ] && TTY=(-t)

# ── Ctrl+C handling ────────────────────────────────────────────────────
# `docker run` does NOT forward signals to the container when a -t TTY is
# allocated without -i (the case here — interactive `make build` allocates a
# TTY but we never attach stdin).  So a bare Ctrl+C kills the docker CLI but
# leaves Quartus running headless in a detached container — holding the build
# dir, pegging a CPU, and surviving the build.  Fix: give the container a
# unique name, run it in the background, and on INT/TERM/EXIT force-remove it.
# The trap runs whether the build finishes, fails, or is interrupted.
CNAME="ofpgaos-q-$$-$(basename "$BDIR")"
trap 'oci_rm_force "$CNAME"' EXIT INT TERM
# Apple `container` caps each guest VM at ~1 GiB by default — Quartus map/fit
# blow past that and get OOM-killed ("Killed" mid-synthesis).  Give the VM a
# generous slice (override with CONTAINER_MEM).  Docker shares host RAM, so it
# gets no --memory and the array stays empty.
QMEM=()
oci_is_apple && QMEM=(--memory "${CONTAINER_MEM:-16g}")
run_quartus() {                      # args: all docker-run flags + image + command
    oci_run --rm --name "$CNAME" ${QMEM[@]+"${QMEM[@]}"} "$@" &
    wait $!                           # wait is interruptible -> Ctrl+C reaches the trap
}

# ── License passthrough (Quartus Standard / Pro / IP) ────────────────────
# Quartus Lite needs no license; Standard/Pro do.  If the user has a license
# file pointed at by $LM_LICENSE_FILE (or a host-side license at one of the
# usual paths), bind-mount it into the container at the same path and forward
# the env var.  No-op for Lite users.
LIC_MOUNTS=()
LIC_ENV=()
LIC_HOST=""
if [ -n "${LM_LICENSE_FILE:-}" ] && [ -f "$LM_LICENSE_FILE" ]; then
    LIC_HOST="$LM_LICENSE_FILE"
elif [ -f "$HOME/flexlm/license.dat" ]; then
    LIC_HOST="$HOME/flexlm/license.dat"
elif [ -f "$ALTERA/license.dat" ]; then
    LIC_HOST="$ALTERA/license.dat"
fi
if [ -n "$LIC_HOST" ]; then
    LIC_MOUNTS=(-v "$LIC_HOST:$LIC_HOST:ro")
    LIC_ENV=(-e "LM_LICENSE_FILE=$LIC_HOST")
fi

# ── Auto-bake the image if user dropped the installer under tools/ ──────
# When the baked image is missing but a Quartus tarball is sitting in
# tools/, run the bake automatically — saves the user from a separate
# `make quartus-image` step.  ~10 min first time; cached after that.
if ! oci_image_exists "$IMG_FULL"; then
    INSTALLER="$(find "$REPO/tools" -maxdepth 4 -type f -name 'Quartus-*25*-linux.tar' 2>/dev/null | head -1 || true)"
    if [ -n "$INSTALLER" ]; then
        echo "[quartus] image '$IMG_FULL' not present, but found tarball at:"
        echo "         $INSTALLER"
        echo "[quartus] baking image (one-time, ~10 min)..."
        bash "$REPO/tools/build-quartus-image.sh"
    fi
fi

# ── Mode selection: baked image first, bind-mount fallback ─────────────
# Note: --user keeps outputs owned by the host user.  HOME + /tmp are container-
# private tmpfs => the Quartus per-user state that segfaults under concurrency
# is isolated.  NUM_PARALLEL_PROCESSORS is pinned in the generated qsf so
# parallel sweep fits place reproducibly with no per-invocation flag.
if oci_image_exists "$IMG_FULL"; then
    # Baked image — Quartus is INSIDE.  No -v $ALTERA, no PATH/QUARTUS_ROOTDIR
    # env (already in the image ENV).
    run_quartus ${TTY[@]+"${TTY[@]}"} \
      --platform linux/amd64 \
      --user "$(id -u):$(id -g)" \
      -v "$REPO:$REPO" \
      ${LIC_MOUNTS[@]+"${LIC_MOUNTS[@]}"} \
      --tmpfs /tmp:exec --tmpfs /qhome:exec \
      -e HOME=/qhome \
      ${LIC_ENV[@]+"${LIC_ENV[@]}"} \
      -w "$BDIR" \
      "$IMG_FULL" \
      bash -c 'rm -rf db incremental_db && quartus_map ap_core && quartus_fit ap_core && quartus_asm ap_core && quartus_sta ap_core'
    exit $?
elif oci_image_exists "$IMG_BIND" && [ -x "$QROOT/bin/quartus_map" ]; then
    # Bind-mount image + host install — original setup.
    run_quartus ${TTY[@]+"${TTY[@]}"} \
      --user "$(id -u):$(id -g)" \
      -v "$REPO:$REPO" \
      -v "$ALTERA:$ALTERA:ro" \
      ${LIC_MOUNTS[@]+"${LIC_MOUNTS[@]}"} \
      --tmpfs /tmp:exec --tmpfs /qhome:exec \
      -e HOME=/qhome -e QUARTUS_ROOTDIR="$QROOT" \
      -e PATH="$QROOT/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
      -e LANG=en_US.UTF-8 -e LC_ALL=en_US.UTF-8 \
      ${LIC_ENV[@]+"${LIC_ENV[@]}"} \
      -w "$BDIR" \
      "$IMG_BIND" \
      bash -c 'rm -rf db incremental_db && quartus_map ap_core && quartus_fit ap_core && quartus_asm ap_core && quartus_sta ap_core'
    exit $?
fi

# ── Neither mode is set up: print actionable instructions ──────────────
cat >&2 <<EOF

ERROR: cannot run Quartus — no usable build environment found.

  Looked for:
    1. Baked image     '$IMG_FULL'     (preferred)            ── not present
    2. Bind-mount image '$IMG_BIND' + Quartus at $QROOT       ── not present

  RECOMMENDED — bake Quartus into a local image (one-time, ~10–30 min,
  ~5 GB).  The image stays on YOUR machine (Intel/Altera Quartus EULA is
  not OK with redistribution).  Works on Linux and macOS (Apple Silicon
  via Rosetta).

  Steps:
    1. Download a Quartus 25.1 (Linux) offline tarball (~9 GB).  Either
       edition works — both ship the same setup.sh + Bitrock installer.
       Lite is free and covers Cyclone V (what the Pocket uses), so it's
       the simplest choice:
         Lite (free, no license):
           https://www.altera.com/downloads/fpga-development-tools/quartus-prime-lite-edition-design-software-version-25-1-linux
         Standard (license required):
           https://www.altera.com/downloads/fpga-development-tools/quartus-prime-standard-edition-design-software-version-25-1-linux

    2. Move it into the repo (gitignored path, anywhere under tools/):
         mv ~/Downloads/Quartus-*-linux.tar tools/

    3. Bake the image (this script will then pick it up automatically):
         make -C src/fpga/targets/pocket quartus-image
         # or directly:
         tools/build-quartus-image.sh

  ALTERNATIVE — bind-mount mode (Linux only, host Quartus required):
    Build the minimal image once:
      docker build -t $IMG_BIND tools/docker/
    Set ALTERA_ROOT so this script finds your install:
      export ALTERA_ROOT=/path/to/altera_lite        # parent of 25.1std/
    Then rerun \`make build\`.

EOF
exit 1
