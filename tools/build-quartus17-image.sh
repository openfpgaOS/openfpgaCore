#!/bin/bash
#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------
#
# Bake Quartus Prime Lite 17.0.x into a Docker image (openfpgaos-quartus17)
# for the mister target.  Sibling to build-quartus-image.sh (which bakes
# Quartus 25.1 for pocket).  One-time setup per machine.  Image is NOT
# redistributable — local-only.
#
# Auto-detects what's available and picks the right install path:
#
#   1. Pre-extracted Q17 tree (altera-17-quartus.tar*)  → skip install,
#      just extract + ffreep patch.  ~2 min.  Universal — works on any host.
#
#   2. Installer tarball (Quartus-*17.0*-linux.tar) + Linux x86_64 host
#      → run installer natively in amd64 container.  ~5-10 min.  No emulation.
#
#   3. Installer tarball + Apple Silicon / Linux ARM64 host
#      → run installer through QEMU inside an arm64 container (avoids
#        Rosetta's BSS-overflow on Q17's Bitrock installer).  ~30 min.
#        Requires a privileged container for binfmt registration; restores
#        host binfmt state on exit.
#
# After install, the artifact tarball is automatically used to build the
# runtime image.  The runtime image is IDENTICAL across all three paths.
#
# Overrides:
#   Q17_INSTALL_PATH=prebuilt|native|qemu   force a specific path
#   QUARTUS17_IMG=<tag>                     override runtime image tag
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMG="${QUARTUS17_IMG:-openfpgaos-quartus17}"
HOST_ARCH="$(uname -m)"
source "$REPO/tools/oci.sh"   # $OCI + oci_run/oci_build
URL="https://www.intel.com/content/www/us/en/software-kit/666220/intel-quartus-prime-lite-edition-design-software-version-17-0-for-linux.html"

# ── Discover inputs ────────────────────────────────────────────────────
# Pre-extracted tar (preferred).  Newest by mtime if multiple.
PREBUILT="$(find "$REPO/tools" -maxdepth 4 -type f \
    \( -name 'altera-17-quartus.tar*' \
    -o -name 'altera-17*quartus*.tar*' \
    -o -name 'intelFPGA*17*quartus*.tar*' \) 2>/dev/null \
    | xargs ls -1t 2>/dev/null | head -1 || true)"

# Installer tar (fallback, requires an install step).
INSTALLER="$(find "$REPO/tools" -maxdepth 4 -type f \
    -name 'Quartus-*17.0*-linux.tar' 2>/dev/null \
    | xargs ls -1t 2>/dev/null | head -1 || true)"

# Decide which install path to take.
INSTALL_PATH="${Q17_INSTALL_PATH:-}"
if [ -z "$INSTALL_PATH" ]; then
    if [ -n "$PREBUILT" ]; then
        INSTALL_PATH="prebuilt"
    elif [ -n "$INSTALLER" ]; then
        case "$HOST_ARCH" in
            x86_64|amd64)    INSTALL_PATH="native" ;;
            # Apple `container` runs amd64 natively via Rosetta-in-VM, so the
            # install needs no qemu binfmt — treat it like a native amd64 host.
            arm64|aarch64)   oci_is_apple && INSTALL_PATH="native" || INSTALL_PATH="qemu" ;;
            *) echo "ERROR: unknown host arch $HOST_ARCH"; exit 1 ;;
        esac
    fi
fi

# ── Error path: no inputs ──────────────────────────────────────────────
if [ -z "$INSTALL_PATH" ]; then
    cat >&2 <<EOF

ERROR: no Quartus 17 tarball found under tools/.

  Looked for:
    pre-extracted:  altera-17-quartus.tar*
    installer:      Quartus-*17.0*-linux.tar

  Get the installer (~6 GB) from Altera:
    $URL
  Drop the .tar at:
    $REPO/tools/Quartus-lite-17.0.0.595-linux.tar
  Then rerun:
    make quartus17-image

  Or supply a pre-extracted tree (faster bake, useful for CI/sharing):
    Built on a Linux box with Q17 installed:
      tar czf altera-17-quartus.tar.gz \\
          -C /home/alberto/intelFPGA_lite/17.0 quartus
    Drop at:
      $REPO/tools/altera-17-quartus.tar.gz
    Rerun:  make quartus17-image

EOF
    exit 1
fi

# ── Stage build context for the runtime image ──────────────────────────
CTX="$(mktemp -d -t openfpgaos-q17build-XXXXXX)"
trap 'rm -rf "$CTX"' EXIT
cp "$REPO/tools/docker/Dockerfile.quartus17" "$CTX/Dockerfile"

# ── Install path: produce the pre-extracted tar if we don't have one ───
case "$INSTALL_PATH" in
prebuilt)
    echo "[quartus17-image] using pre-extracted tree: $PREBUILT"
    PREBUILT_NAME="$(basename "$PREBUILT")"
    # Sanity-check layout — same check as before.
    if ! tar -tf "$PREBUILT" 2>/dev/null | grep -q '^quartus/bin/quartus_map'; then
        echo "ERROR: $PREBUILT_NAME missing quartus/bin/quartus_map at top level."
        echo "       Re-tar from the PARENT of quartus/, not from inside it."
        exit 1
    fi
    ln "$PREBUILT" "$CTX/$PREBUILT_NAME" 2>/dev/null || cp "$PREBUILT" "$CTX/$PREBUILT_NAME"
    BUILD_TAR_NAME="$PREBUILT_NAME"
    ;;

native|qemu)
    echo "[quartus17-image] using installer: $INSTALLER"
    echo "[quartus17-image] install path:    $INSTALL_PATH ($HOST_ARCH host)"
    INSTALLER_NAME="$(basename "$INSTALLER")"

    # Run the install in a container.  Output: $CTX/altera-17-quartus.tar.gz
    # which the runtime Dockerfile then extracts.
    INSTALL_PLATFORM=""
    case "$INSTALL_PATH" in
        native) INSTALL_PLATFORM="linux/amd64" ;;
        qemu)   INSTALL_PLATFORM="linux/arm64" ;;
    esac

    # Register QEMU for x86_64 inside the arm64 install container.  On
    # OrbStack this temporarily overrides Rosetta's binfmt for amd64;
    # `tonistiigi/binfmt --uninstall` at the end restores normal routing.
    # On native amd64 hosts we skip this entirely.
    if [ "$INSTALL_PATH" = "qemu" ]; then
        echo "[quartus17-image] registering qemu-x86_64 binfmt (privileged)..."
        docker run --privileged --rm tonistiigi/binfmt --install amd64 >/dev/null
        # Cleanup hook: restore Rosetta after install (the install dance
        # is the only thing that needed qemu; everything else uses Rosetta).
        trap 'rm -rf "$CTX"; \
              [ "$INSTALL_PATH" = "qemu" ] && \
                  docker run --privileged --rm tonistiigi/binfmt --uninstall qemu-x86_64 >/dev/null 2>&1 || true' EXIT
    fi

    BASE_IMAGE="ubuntu:18.04"

    echo "[quartus17-image] running install in $BASE_IMAGE ($INSTALL_PLATFORM)..."
    oci_run --rm \
        --platform "$INSTALL_PLATFORM" \
        --user 0:0 \
        -v "$INSTALLER:/work/quartus-installer.tar:ro" \
        -v "$REPO/tools/install-quartus17-in-container.sh:/install.sh:ro" \
        -v "$CTX:/out" \
        "$BASE_IMAGE" \
        bash /install.sh

    [ -f "$CTX/altera-17-quartus.tar.gz" ] || {
        echo "ERROR: install step did not produce altera-17-quartus.tar.gz"
        exit 1
    }
    BUILD_TAR_NAME="altera-17-quartus.tar.gz"

    # Offer to save the produced tar for future bakes / sharing.
    SAVE_PATH="$REPO/tools/altera-17-quartus.tar.gz"
    if [ ! -f "$SAVE_PATH" ]; then
        echo "[quartus17-image] saving extracted tree → tools/altera-17-quartus.tar.gz"
        echo "                  (so future bakes skip the install step entirely)"
        cp "$CTX/altera-17-quartus.tar.gz" "$SAVE_PATH"
    fi
    ;;
esac

# ── Bake the runtime image ─────────────────────────────────────────────
echo "[quartus17-image] building runtime image $IMG with $OCI (linux/amd64, ~2 min)..."
oci_build \
    --platform linux/amd64 \
    --build-arg "QUARTUS_TAR=$BUILD_TAR_NAME" \
    -t "$IMG" \
    "$CTX"

echo ""
echo "[quartus17-image] Done.  Tagged: $IMG"
echo "  Mister builds will now use this image automatically."
