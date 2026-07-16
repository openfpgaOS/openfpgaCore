#!/bin/bash
#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------
#
# Run any command inside the Quartus 17.0.x container (openfpgaos-quartus17)
# for the mister target.  Mirrors sdk-container.sh / firmware-container.sh
# / the pocket quartus-container.sh, but pinned to Q17.0.x because the
# MiSTer sys/ framework requires it.
#
# Auto-bakes the image on first use if a Q17.0.x tarball is sitting under
# tools/.  Otherwise prints a clear instruction block pointing at
# build-quartus17-image.sh.
#
# Usage:
#   bash quartus17-container.sh quartus_map ap_core --verilog_macro=FOO
#   bash quartus17-container.sh bash -c 'quartus_map x && quartus_fit x && quartus_asm x && quartus_sta x'
#   bash quartus17-container.sh bash                           # interactive shell
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMG="${QUARTUS17_IMG:-openfpgaos-quartus17}"
source "$REPO/tools/oci.sh"   # $OCI + oci_run/oci_build/oci_image_exists/oci_rm_force

[ $# -ge 1 ] || { echo "usage: quartus17-container.sh <command> [args...]"; exit 2; }

# Runtime must be usable before we probe for the image or (maybe) bake it.
# oci.sh already exit'd if NO runtime CLI is on PATH; this additionally catches
# a present-but-stopped daemon/VM so `oci_image_exists` can't misfire the bake.
oci_require_daemon || exit 1

# Auto-bake on first use, if a tarball is available.  Same UX as the q25
# auto-bake hook in quartus-container.sh.  Two accepted inputs (build-quartus17-
# image.sh auto-picks the right install path):
#   * pre-extracted tree  (altera-17-quartus.tar*)          → ~2 min bake
#   * installer tarball   (Quartus-*17.0*-linux.tar)        → install-in-container
#                                                             first (native ~5-10 min,
#                                                             Apple Silicon/qemu ~30 min)
if ! oci_image_exists "$IMG"; then
    PREBUILT="$(find "$REPO/tools" -maxdepth 4 -type f \
        \( -name 'altera-17-quartus.tar*' \
        -o -name 'altera-17*quartus*.tar*' \
        -o -name 'intelFPGA*17*quartus*.tar*' \) 2>/dev/null | head -1 || true)"
    INSTALLER="$(find "$REPO/tools" -maxdepth 4 -type f \
        -name 'Quartus-*17.0*-linux.tar' 2>/dev/null | head -1 || true)"
    if [ -n "$PREBUILT" ]; then
        echo "[quartus17] image '$IMG' not present, but found pre-extracted tree at:"
        echo "            $PREBUILT"
        echo "[quartus17] baking image (one-time, ~2 min)..."
        bash "$REPO/tools/build-quartus17-image.sh"
    elif [ -n "$INSTALLER" ]; then
        echo "[quartus17] image '$IMG' not present, but found installer tarball at:"
        echo "            $INSTALLER"
        echo "[quartus17] baking image -- installs Q17 in a container first"
        echo "            (native amd64 ~5-10 min; Apple Silicon via qemu ~30 min)..."
        bash "$REPO/tools/build-quartus17-image.sh"
    else
        cat >&2 <<EOF

ERROR: cannot run mister Quartus -- no Q17.0.x image or tarball under tools/.

  Image '$IMG' isn't built, and no Q17 tarball was found under tools/.
  Two inputs are accepted -- either one auto-bakes the image on next build:

  A. Installer tarball (simplest -- just download & drop):
       Get the Q17.0 Lite installer (~6 GB) for Linux from Altera:
         https://www.intel.com/content/www/us/en/software-kit/666220/intel-quartus-prime-lite-edition-design-software-version-17-0-for-linux.html
       Drop it at:
         $REPO/tools/Quartus-lite-17.0.0.595-linux.tar
       Rerun -- the build installs Q17 in a container and bakes the image:
         make build
       (On Apple Silicon the install runs x86_64 under qemu, ~30 min one-time;
        the extracted tree is cached to tools/ so future bakes skip it.)

  B. Pre-extracted tree (faster bake; handy for CI/sharing):
       On a Linux box that has Q17 installed:
         tar czf altera-17-quartus.tar.gz \\
             -C /home/alberto/intelFPGA_lite/17.0 quartus
       Copy here and drop in tools/:
         mv ~/Downloads/altera-17-quartus.tar.gz tools/
       Rerun:  make build   (or bake explicitly:  make quartus17-image)

EOF
        exit 1
    fi
fi

# Pseudo-TTY only when BOTH stdin and stdout are terminals.  `-i -t` fails with
# "cannot attach stdin to a TTY-enabled container..." when stdin is not a
# terminal — which is the case under `make` recipes even in an interactive
# shell.  Same idiom as the other wrappers (bash 3.2 compat for empty array).
TTY=()
[ -t 0 ] && [ -t 1 ] && TTY=(-i -t)

# Resolve cwd via pwd -P (macOS /tmp -> /private/tmp consistency), same fix
# we made for sdk-container.sh.
WORKDIR="$(pwd -P)"

# --user keeps outputs owned by host user.  HOME on /qhome tmpfs so each
# container gets its own ~/.altera.quartus (the per-user-state contention
# that segfaults concurrent Quartus on a single host).  Repo bind-mounted
# at SAME path so absolute paths in qsf/mif refs resolve identically.
# Name the container + tear it down from a trap so Ctrl+C (SIGINT) cleanly
# kills the build instead of orphaning it: `docker run` does not forward
# signals to the container when a -t TTY is allocated without -i.  Run in the
# background and `wait` (interruptible) so the trap fires on INT/TERM/EXIT.
CNAME="ofpgaos-q17-$$"
trap 'oci_rm_force "$CNAME"' EXIT INT TERM
# Apple `container` caps each VM at ~1 GiB; Quartus map/fit need more or they're
# OOM-killed.  Docker shares host RAM, so QMEM stays empty there.
QMEM=()
oci_is_apple && QMEM=(--memory "${CONTAINER_MEM:-16g}")
# On an ARM host (Apple Silicon) the amd64 Quartus runs emulated (Rosetta on
# Docker Desktop, or QEMU).  With NUM_PARALLEL_PROCESSORS=ALL, Quartus otherwise
# spawns one worker per host core (e.g. 18), and under Rosetta's per-thread
# translation + locking overhead that is both slow and destabilising (cf. the
# Error 112002 note in CLAUDE.md).  Pin the container to a small CPU SET —
# `--cpuset-cpus`, not `--cpus`: cpuset is honoured by sched_getaffinity, so
# `nproc` (and therefore Quartus) sees exactly that many CPUs and spawns that
# many threads.  Default 4; override with QCPUS.  Native x86_64 stays uncapped.
QCPU=()
case "$(uname -m)" in
    arm64|aarch64) QCPU=(--cpuset-cpus "0-$(( ${QCPUS:-4} - 1 ))") ;;
esac
# Backgrounding below (so the EXIT/INT trap cleans up the container on Ctrl+C)
# detaches stdin to /dev/null, which a -t TTY rejects ("cannot attach stdin to
# a TTY-enabled container").  When a TTY was allocated, hand the controlling
# terminal (/dev/tty) to the backgrounded run; otherwise /dev/null is correct.
STDIN_SRC=/dev/null
[ ${#TTY[@]} -gt 0 ] && STDIN_SRC=/dev/tty
oci_run --rm --name "$CNAME" ${QMEM[@]+"${QMEM[@]}"} ${QCPU[@]+"${QCPU[@]}"} ${TTY[@]+"${TTY[@]}"} \
  --platform linux/amd64 \
  --user "$(id -u):$(id -g)" \
  -v "$REPO:$REPO" \
  --tmpfs /tmp:exec --tmpfs /qhome:exec \
  -e HOME=/qhome \
  -w "$WORKDIR" \
  "$IMG" \
  "$@" < "$STDIN_SRC" &
wait $!
