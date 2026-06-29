#!/bin/bash
#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------
#
# Bake a Quartus install into a Docker image (openfpgaos-quartus-full) so
# `make build` works without the host needing Quartus on PATH / a Linux
# install at /home/alberto/altera_lite.  One-time setup per machine; subsequent
# fits reuse the image.  The image is NOT redistributable (Intel/Altera Quartus
# EULA forbids it) — built locally only.
#
# Workflow:
#   1. Download the Quartus offline tarball (~9 GB) from Altera.  Either
#      edition works — both ship the same setup.sh + Bitrock installer:
#        Lite (free, no license, covers Cyclone V — recommended):
#          https://www.altera.com/downloads/fpga-development-tools/quartus-prime-lite-edition-design-software-version-25-1-linux
#        Standard (license required, broader devices):
#          https://www.altera.com/downloads/fpga-development-tools/quartus-prime-standard-edition-design-software-version-25-1-linux
#      You'll get a file like:
#        Quartus-lite-25.1std.0.1129-linux.tar
#        Quartus-standard-25.1std.0.1129-linux.tar
#   2. Drop it ANYWHERE under tools/ — top level is easiest:
#        mv ~/Downloads/Quartus-*-linux.tar tools/
#   3. Either:
#        make quartus-image            # explicit bake
#        make build                    # auto-bakes if tarball is present
#
# The Dockerfile bind-mounts the tarball (never lands in a layer), extracts,
# runs setup.sh in unattended mode (~3-5 min, all offline — the tarball is
# the full install), then patches the Rosetta workaround.  No network needed
# during bake.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMG="${QUARTUS_FULL_IMG:-openfpgaos-quartus-full}"
source "$REPO/tools/oci.sh"   # $OCI + oci_build (docker buildx | container build)
URL_LITE="https://www.altera.com/downloads/fpga-development-tools/quartus-prime-lite-edition-design-software-version-25-1-linux"
URL_STD="https://www.altera.com/downloads/fpga-development-tools/quartus-prime-standard-edition-design-software-version-25-1-linux"

# Search ANYWHERE under tools/ for a Quartus 25.x offline tarball.  Newest by
# mtime wins (handles multiple downloads sitting around).  Plain while-read for
# bash 3.2 compat — macOS ships bash 3.2 with no `mapfile`/`readarray`.
# NOTE the '25' in the glob: this is the *pocket* (Quartus 25.1) bake, and a
# coexisting *mister* tarball (Quartus-lite-17.0.0.*-linux.tar) ALSO matches a
# bare 'Quartus-*-linux.tar' — and would win on mtime, baking the wrong (and
# Rosetta-incompatible) 2017 installer.  Constrain to 25.x so the two never collide.
QUARTUS_CANDIDATES=()
while IFS= read -r line; do
    QUARTUS_CANDIDATES+=("$line")
done < <(find "$REPO/tools" -maxdepth 4 -type f -name 'Quartus-*25*-linux.tar' 2>/dev/null)

if [ ${#QUARTUS_CANDIDATES[@]} -eq 0 ]; then
    cat >&2 <<EOF

ERROR: no Quartus tarball found under tools/  (looked for Quartus-*-linux.tar)

  This bake step needs an Altera Quartus 25.1 (Linux) offline tarball
  placed somewhere under tools/.  The file is gitignored — Intel/Altera's
  EULA forbids redistributing the binaries, so each contributor must
  supply their own copy.

  How to get it (either edition works — same Bitrock installer):

    1. Download a 25.1 Linux tarball (~9 GB) from Altera:

       RECOMMENDED (free, no license, covers Cyclone V):
         $URL_LITE
         → Quartus-lite-25.1std.0.1129-linux.tar

       Standard (license required, broader devices):
         $URL_STD
         → Quartus-standard-25.1std.0.1129-linux.tar

    2. Move it under tools/ (any subdir works; top level is easiest):
         mv ~/Downloads/Quartus-*-linux.tar tools/

    3. Rerun:  make quartus-image      (or just \`make build\` — it auto-bakes)
       (~10 min on first build: ~5 min for the install, ~5 min for the
        apt layer setup.  Cached after that.  No network used during bake;
        the tarball is the full offline install.)

EOF
    exit 1
fi

# Pick newest by mtime (ls -1t).
QUARTUS_PATH="$(printf '%s\n' "${QUARTUS_CANDIDATES[@]}" | xargs ls -1t 2>/dev/null | head -1)"
QUARTUS_NAME="$(basename "$QUARTUS_PATH")"
echo "[quartus-image] using tarball: $QUARTUS_PATH"

# Stage a minimal build context: just the Dockerfile + a hardlink (or copy
# if cross-fs) of the tarball at a fixed name.  This way the .tar can live
# anywhere under tools/ and the Dockerfile still references a stable filename.
CTX="$(mktemp -d -t openfpgaos-qbuild-XXXXXX)"
trap 'rm -rf "$CTX"' EXIT
cp "$REPO/tools/docker/Dockerfile.quartus-full" "$CTX/Dockerfile"
ln "$QUARTUS_PATH" "$CTX/$QUARTUS_NAME" 2>/dev/null || cp "$QUARTUS_PATH" "$CTX/$QUARTUS_NAME"

echo "[quartus-image] $OCI build (linux/amd64 — Quartus is x86_64 only; ~10 min on first build)..."
oci_build \
    --platform linux/amd64 \
    --build-arg "QUARTUS_TAR=$QUARTUS_NAME" \
    -t "$IMG" \
    "$CTX"

echo ""
echo "[quartus-image] Done.  Tagged: $IMG"
echo "  \`make build\` will now use this image automatically (no ALTERA_ROOT bind-mount needed)."
