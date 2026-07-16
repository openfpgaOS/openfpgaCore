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
oci_require_daemon || exit 1  # CLI on PATH is not enough — need a live daemon/VM
URL="https://www.intel.com/content/www/us/en/software-kit/666220/intel-quartus-prime-lite-edition-design-software-version-17-0-for-linux.html"

# ── Discover inputs ────────────────────────────────────────────────────
# Pre-extracted tar (preferred).  Newest by mtime if multiple.
PREBUILT="$(find "$REPO/tools" -maxdepth 4 -type f \
    \( -name 'altera-17-quartus.tar*' \
    -o -name 'altera-17*quartus*.tar*' \
    -o -name 'intelFPGA*17*quartus*.tar*' \) 2>/dev/null \
    | xargs -r ls -1t 2>/dev/null | head -1 || true)"

# Installer tar (fallback, requires an install step).
INSTALLER="$(find "$REPO/tools" -maxdepth 4 -type f \
    -name 'Quartus-*17.0*-linux.tar' 2>/dev/null \
    | xargs -r ls -1t 2>/dev/null | head -1 || true)"

# Decide which install path to take.
INSTALL_PATH="${Q17_INSTALL_PATH:-}"
if [ -z "$INSTALL_PATH" ]; then
    if [ -n "$PREBUILT" ]; then
        INSTALL_PATH="prebuilt"
    elif [ -n "$INSTALLER" ]; then
        case "$HOST_ARCH" in
            x86_64|amd64)    INSTALL_PATH="native" ;;
            arm64|aarch64)
                # Q17's Bitrock installer overflows Rosetta ("bss_size
                # overflow"), so x86_64 emulation for the INSTALL must be QEMU
                # user-mode — never Rosetta.  The qemu path registers binfmt via
                # `docker run --privileged tonistiigi/binfmt`, so it needs
                # Docker/OrbStack.  Apple `container`'s only x86 engine IS
                # Rosetta, so it cannot run this installer at all — route it to a
                # clear early error instead of crashing mid-install.
                if oci_is_apple; then
                    INSTALL_PATH="rosetta-unsupported"
                else
                    INSTALL_PATH="qemu"
                fi
                ;;
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

# ── Installer + Apple `container`: borrow Docker for the install step ───
# Apple container's only x86_64 engine is Rosetta, and Q17's Bitrock installer
# overflows it ("rosetta error: bss_size overflow").  The QEMU install path
# works, but QEMU needs binfmt_misc registration (CAP_SYS_ADMIN / --privileged),
# which Apple container doesn't expose (apple/container#206) — only Docker does.
# So if Docker is up, use it JUST for this bake (leaving the user's default
# runtime, Apple container, for everything else); otherwise print how to get it.
if [ "$INSTALL_PATH" = "rosetta-unsupported" ]; then
    if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
        echo "[quartus17-image] Apple 'container' can't run the Q17 Bitrock installer"
        echo "                  (Rosetta bss_size overflow); Docker is up -> using it"
        echo "                  with QEMU for this one-time install/bake."
        OCI=docker                 # force Docker for the oci_run/oci_build below
        INSTALL_PATH="qemu"        # take the QEMU-under-Docker install path
    else
        cat >&2 <<EOF

ERROR: can't install Quartus 17 from the installer tar under Apple 'container'.

  Found only the installer:
    $INSTALLER
  Apple container's ONLY x86_64 engine is Rosetta, and Q17's Bitrock installer
  overflows Rosetta ("rosetta error: bss_size overflow").  The install path
  that works emulates x86_64 with QEMU, which needs privileges Apple container
  doesn't grant (apple/container#206) — so it must run under Docker/OrbStack.

  Fix (one-time — Docker is only needed to BUILD the image, not to use it):

  1. Install OrbStack (lightweight; provides the 'docker' CLI):
       brew install --cask orbstack
     then launch it once so its daemon starts:
       open -a OrbStack
     (Docker Desktop works too.  Verify with:  docker info)

  2. Rerun the bake — it auto-detects Docker and installs Q17 under QEMU:
       make quartus17-image      # or: make build TARGET=mister
     (~30 min under QEMU; caches tools/altera-17-quartus.tar.gz so future
      bakes — and other machines — skip the install entirely.)

  Alternative (no Docker at all): supply a pre-extracted Q17 tree from a Linux
  box with Q17 installed, then rerun:
       tar czf altera-17-quartus.tar.gz \\
           -C /home/alberto/intelFPGA_lite/17.0 quartus
       # drop it under $REPO/tools/

  (Advanced: force a path with Q17_INSTALL_PATH=native|qemu|prebuilt — but
   'native' under Apple container is the Rosetta route that just overflowed.)

EOF
        exit 1
    fi
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
    # Sanity-check layout.  NOTE: `|| true` on tar is required — grep -q closes
    # the pipe on first match, tar dies with SIGPIPE (141), and under the
    # script's `set -o pipefail` that 141 would become the pipeline status and
    # spuriously fail the check even though quartus_map WAS found.
    if ! { tar -tf "$PREBUILT" 2>/dev/null || true; } | grep -q '^quartus/bin/quartus_map'; then
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
    # BOTH the native and qemu paths run the install container as amd64.  The
    # Bitrock installer is a dynamically-linked x86_64 ELF, so it needs the x86
    # loader + libs present — a fully-amd64 container has them.  An arm64
    # container + qemu-binfmt does NOT (no /lib64/ld-linux-x86-64.so.2), and the
    # installer dies with exit 127.  On x86_64 hosts amd64 runs natively; on ARM
    # hosts it runs under QEMU.
    INSTALL_PLATFORM="linux/amd64"

    # Ensure QEMU is registered for amd64 so the amd64 install container runs on
    # an ARM host.  Docker Desktop with Rosetta DISABLED provides this itself
    # (Rosetta must be off — it overflows on Q17's Bitrock installer with
    # "bss_size overflow"); a generic arm64 Linux host needs the explicit
    # binfmt registration below.  We leave the registration in place afterward
    # (no uninstall): QEMU-for-amd64 is the correct steady state here.
    if [ "$INSTALL_PATH" = "qemu" ]; then
        echo "[quartus17-image] ensuring qemu-x86_64 binfmt is registered (privileged)..."
        echo "[quartus17-image] NOTE: this INSTALL needs QEMU — on Docker Desktop,"
        echo "                  'Use Rosetta for x86/amd64' must be OFF (Rosetta overflows"
        echo "                  the installer).  Flip it back ON afterward to RUN Quartus."
        docker run --privileged --rm tonistiigi/binfmt --install amd64 >/dev/null
    fi

    BASE_IMAGE="ubuntu:18.04"

    echo "[quartus17-image] running install in $BASE_IMAGE ($INSTALL_PLATFORM)..."
    # Bind-mount DIRECTORIES, never single files: Apple `container` (virtiofs)
    # rejects a file mount source with "path '<file>' is not a directory", while
    # Docker accepts dir mounts too — so dir mounts are portable across both
    # runtimes.  The installer tar's parent goes to /work (tar reachable as
    # /work/$INSTALLER_BASE, passed via env); tools/ goes to /tools so the
    # in-container install script is reachable there.
    INSTALLER_DIR="$(cd "$(dirname "$INSTALLER")" && pwd)"
    INSTALLER_BASE="$(basename "$INSTALLER")"
    oci_run --rm \
        --platform "$INSTALL_PLATFORM" \
        --user 0:0 \
        -v "$INSTALLER_DIR:/work:ro" \
        -v "$REPO/tools:/tools:ro" \
        -v "$CTX:/out" \
        -e "INSTALLER_TAR=/work/$INSTALLER_BASE" \
        "$BASE_IMAGE" \
        bash /tools/install-quartus17-in-container.sh

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
