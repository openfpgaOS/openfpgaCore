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
# The container is ALWAYS amd64: native on x86_64 hosts, emulated by QEMU on
# ARM hosts (Docker Desktop with Rosetta disabled, or arm64-Linux + binfmt).
# It is deliberately NOT arm64+binfmt — the Bitrock installer is a
# dynamically-linked x86_64 ELF and needs the x86 loader/libs, which only a
# fully-amd64 userland provides (arm64+qemu dies with exit 127).  The host bake
# script sets the platform; this script just installs Q17 and tars it.
#
# Bind mounts (set up by the host — DIRECTORIES only; Apple `container` can't
# mount a single file):
#   /work        read-only dir holding the Altera Q17 installer .tar
#                (its name is passed in $INSTALLER_TAR)
#   /tools       read-only repo tools/ dir (this script runs from here)
#   /out         host-writable artifact dir (gets the produced tar)
set -euo pipefail

# Path to the installer tar inside the container.  The host mounts the tar's
# PARENT dir at /work and passes the full in-container path here; fall back to
# the legacy fixed path so older callers still work.
INSTALLER_TAR="${INSTALLER_TAR:-/work/quartus-installer.tar}"
ARTIFACT=/out/altera-17-quartus.tar.gz

[ -f "$INSTALLER_TAR" ] || { echo "ERROR: $INSTALLER_TAR not bind-mounted"; exit 1; }
[ -d /out ]            || { echo "ERROR: /out not bind-mounted (writable)"; exit 1; }

export DEBIAN_FRONTEND=noninteractive
echo "[install] apt-get update + libs..."
apt-get update -qq
# Lib set the Bitrock installer dynamically links.  NO i386 multilib here —
# the installer step only unpacks files (no 32-bit-helper execution); the
# RUNTIME image (Dockerfile.quartus17) adds i386 libs separately.  This
# container is amd64, so apt fetches from archive.ubuntu.com.
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
# Neutralize the optional sub-installers BEFORE the main setup runs.  The main
# installer spawns QuartusHelpSetup (docs) — and ModelSimSetup — as SEPARATE
# .run sub-installers with a HARD-CODED `--unattendedmodeui minimal`, and that
# minimal UI DEADLOCKS forever under QEMU (even with a virtual framebuffer): the
# main installer then blocks in wait() and the whole bake wedges (observed: the
# main .run pinned at ~0% CPU for an hour with docs still "installing", and the
# device .qdz packages — incl. Cyclone V — never unpacked → a broken tree that
# fails builds with "Error 292025: License file is not specified").  Neither
# docs nor ModelSim are needed to build bitstreams.  The main setup exec's these
# component .run files directly (confirmed via ps), so replacing each with a
# `#!/bin/sh; exit 0` stub makes it record an instant successful "install" and
# move straight on to unpacking the device families (the part we DO need).
for h in /tmp/qsrc/components/*HelpSetup*.run /tmp/qsrc/components/*ModelSimSetup*.run; do
    [ -e "$h" ] || continue
    echo "[install] stubbing optional sub-installer: $(basename "$h")"
    printf '#!/bin/sh\nexit 0\n' > "$h"
    chmod +x "$h"
done
# Skip setup.sh — it does a `uname -m != x86_64` check that triggers an
# interactive "continue? (y/n)" loop when run in an arm64 container.  Call the
# Bitrock .run directly.  xvfb-run still provides a virtual display for any
# component that briefly touches X.  `timeout` is a backstop so an unforeseen
# hang can't wedge the bake indefinitely — we verify the REAL result
# (quartus_map) right after regardless of the exit status.
timeout --preserve-status 3600 xvfb-run -a "$SETUP_RUN" \
    --mode unattended \
    --unattendedmodeui none \
    --installdir /opt/altera-17 \
    || echo "[install] installer exited non-zero / timed out — verifying tree..."

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
