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

# Auto-bake on first use, if a tarball is available.  Same UX as the q25
# auto-bake hook in quartus-container.sh.
if ! oci_image_exists "$IMG"; then
    PREBUILT="$(find "$REPO/tools" -maxdepth 4 -type f \
        \( -name 'altera-17-quartus.tar*' \
        -o -name 'altera-17*quartus*.tar*' \
        -o -name 'intelFPGA*17*quartus*.tar*' \) 2>/dev/null | head -1 || true)"
    if [ -n "$PREBUILT" ]; then
        echo "[quartus17] image '$IMG' not present, but found pre-extracted tree at:"
        echo "            $PREBUILT"
        echo "[quartus17] baking image (one-time, ~2 min)..."
        bash "$REPO/tools/build-quartus17-image.sh"
    else
        cat >&2 <<EOF

ERROR: cannot run mister Quartus -- no Q17.0.x image or pre-extracted tar.

  Image '$IMG' isn't built, and no altera-17-quartus.tar* under tools/.

  Q17's Bitrock installer can't run under Rosetta on Apple Silicon
  (rosetta error: bss_size overflow), so this image is baked from an
  already-installed Q17 tree.

  Procedure:
    1. On a Linux box that has Q17 installed:
         tar czf altera-17-quartus.tar.gz \\
             -C /home/alberto/intelFPGA_lite/17.0 quartus
    2. Copy to this machine and drop in tools/:
         mv ~/Downloads/altera-17-quartus.tar.gz tools/
    3. Rerun -- the build auto-bakes:
         make build
       or bake explicitly:
         make quartus17-image

  Don't have Q17 on a Linux box?  Install it from:
    https://www.intel.com/content/www/us/en/software-kit/666220/intel-quartus-prime-lite-edition-design-software-version-17-0-for-linux.html
  on any Linux x86_64 machine (a throwaway VM is fine), then steps 1-3.

EOF
        exit 1
    fi
fi

# Pseudo-TTY only when stdout is a terminal.  Same idiom as the other
# wrappers (bash 3.2 compat for the empty array case).
TTY=()
[ -t 1 ] && TTY=(-i -t)

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
oci_run --rm --name "$CNAME" ${QMEM[@]+"${QMEM[@]}"} ${TTY[@]+"${TTY[@]}"} \
  --platform linux/amd64 \
  --user "$(id -u):$(id -g)" \
  -v "$REPO:$REPO" \
  --tmpfs /tmp:exec --tmpfs /qhome:exec \
  -e HOME=/qhome \
  -w "$WORKDIR" \
  "$IMG" \
  "$@" &
wait $!
