#!/bin/bash
#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------
#
# Runs INSIDE the install container.  Installs Quartus 17 from the bind-mounted
# Altera installer tarball, then tars the result to /out/altera-17-quartus.tar.gz
# for the host to pick up.
#
# The same script runs on both amd64 (native, no emulation) and arm64+qemu
# (Bitrock installer goes through qemu-x86_64; helper binaries via binfmt).
# Architecture differences are handled by the host bake script before docker
# run; this script just installs Q17 and tars it.
#
# Bind mounts (set up by the host):
#   /work/quartus-installer.tar   read-only Altera Q17 installer .tar
#   /out                          host-writable artifact dir (gets the tar)
set -euo pipefail

INSTALLER_TAR=/work/quartus-installer.tar
ARTIFACT=/out/altera-17-quartus.tar.gz

[ -f "$INSTALLER_TAR" ] || { echo "ERROR: $INSTALLER_TAR not bind-mounted"; exit 1; }
[ -d /out ]            || { echo "ERROR: /out not bind-mounted (writable)"; exit 1; }

export DEBIAN_FRONTEND=noninteractive
echo "[install] apt-get update + libs..."
apt-get update -qq
# Lib set the Bitrock installer dynamically links.  NO i386 multilib here —
# the installer step only unpacks files (no 32-bit-helper execution).  The
# RUNTIME image (Dockerfile.quartus17) adds i386 libs separately; that
# image is amd64 so its apt fetches from archive.ubuntu.com (which has
# i386), whereas this install container is arm64 on Mac hosts and goes
# through ports.ubuntu.com (which doesn't ship i386 packages).
apt-get install -y --no-install-recommends \
    libglib2.0-0 libsm6 libice6 libxext6 libxrender1 libxft2 libxt6 \
    libxmu6 libxi6 libxtst6 libxfixes3 libxcursor1 libxinerama1 \
    libxrandr2 libxdamage1 libxcomposite1 libfontconfig1 libfreetype6 \
    libgl1 libglu1-mesa zlib1g libpng16-16 libnss3 libxshmfence1 \
    ca-certificates locales tar gzip xvfb > /dev/null

echo "[install] extracting installer payload..."
mkdir -p /tmp/qsrc
tar -xf "$INSTALLER_TAR" -C /tmp/qsrc
SETUP_RUN="$(ls /tmp/qsrc/components/QuartusLiteSetup-*-linux.run 2>/dev/null | head -1)"
[ -x "$SETUP_RUN" ] || chmod +x "$SETUP_RUN" 2>/dev/null
[ -f "$SETUP_RUN" ] || { echo "ERROR: QuartusLiteSetup-*-linux.run missing in tar"; exit 1; }

echo "[install] running Q17 installer in unattended mode..."
echo "          (under QEMU on arm64 hosts allow ~30 min)"
# Skip setup.sh — it does a `uname -m != x86_64` check that triggers an
# interactive "continue? (y/n)" loop when run in an arm64 container, even
# though the actual binaries it'd invoke are x86_64 and would run fine via
# binfmt qemu-x86_64.  Call the Bitrock .run directly; binfmt routes the
# exec automatically when the registration is in place.  Same Bitrock
# flags as setup.sh would have passed.
#
# Wrap in xvfb-run: the main installer honors --unattendedmodeui none, but it
# spawns sub-installers (e.g. QuartusHelpSetup) with --unattendedmodeui minimal
# hardcoded, and that minimal UI hangs forever trying to init an X display in a
# headless container.  A virtual framebuffer lets those sub-installers draw
# their (non-interactive) progress UI and finish.
xvfb-run -a "$SETUP_RUN" \
    --mode unattended \
    --unattendedmodeui none \
    --installdir /opt/altera-17

[ -x /opt/altera-17/quartus/bin/quartus_map ] || {
    echo "ERROR: quartus_map missing after install"
    ls -la /opt/altera-17/ 2>&1 || true
    exit 1
}

echo "[install] packaging install tree → $ARTIFACT ..."
# Tar with `quartus` as the top-level entry so the runtime Dockerfile's
# `tar -xf -C /opt/altera-17` lands quartus_map at the expected path.
tar czf "$ARTIFACT" -C /opt/altera-17 quartus
echo "[install] done.  artifact size: $(du -sh "$ARTIFACT" | cut -f1)"
