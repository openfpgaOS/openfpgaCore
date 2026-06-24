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
# Quartus is bind-mounted read-only from the host (not baked into the image).
# The host must have already generated <build-dir>/ap_core.qsf (+ .qpf) and the
# CPU netlist + firmware.mif it references — all by ABSOLUTE path, so they
# resolve identically inside the container (the repo is mounted at the same path).
#
# Usage: quartus-container.sh <absolute-build-dir>
set -euo pipefail

BDIR="${1:?usage: quartus-container.sh <absolute-build-dir>}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ALTERA="${ALTERA_ROOT:-/home/alberto/altera_lite}"
QROOT="$ALTERA/25.1std/quartus"
IMG="${QUARTUS_IMG:-openfpgaos-quartus}"

[ -f "$BDIR/ap_core.qsf" ] || { echo "ERROR: $BDIR/ap_core.qsf missing (generate it first)"; exit 1; }

# Allocate a pseudo-TTY only when OUR stdout is a terminal, so an interactive
# `make build` keeps Quartus's colored output (tools disable color when they see
# a pipe instead of a tty).  When stdout is redirected — build-all/sweep write
# to bld/<job>.log — we omit -t so the logs stay clean (no \r / escape codes).
TTY=()
[ -t 1 ] && TTY=(-t)

# The fitter's processor count is pinned in the generated qsf
# (NUM_PARALLEL_PROCESSORS), so a single build and every parallel sweep fit place
# identically and reproducibly — no per-invocation flag needed.  MAXJOBS x that
# count is what keeps concurrent fits from oversubscribing the host cores.
# --user keeps outputs owned by the host user.  HOME + /tmp are container-private
# tmpfs => the Quartus per-user state that segfaults under concurrency is isolated.
exec docker run --rm "${TTY[@]}" \
  --user "$(id -u):$(id -g)" \
  -v "$REPO:$REPO" \
  -v "$ALTERA:$ALTERA:ro" \
  --tmpfs /tmp:exec --tmpfs /qhome:exec \
  -e HOME=/qhome -e QUARTUS_ROOTDIR="$QROOT" \
  -e PATH="$QROOT/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
  -e LANG=en_US.UTF-8 -e LC_ALL=en_US.UTF-8 \
  -w "$BDIR" \
  "$IMG" \
  bash -c 'rm -rf db incremental_db && quartus_map ap_core && quartus_fit ap_core && quartus_asm ap_core && quartus_sta ap_core'
