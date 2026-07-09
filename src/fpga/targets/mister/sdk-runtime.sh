#!/bin/bash
#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------
#
# openfpgaOS — MiSTer target: export this target's runtime artifacts into
# an SDK checkout.  See the Pocket sibling for the contract.  MiSTer keeps
# its artifacts under runtime/mister/ (consumed by the SDK's
# platforms/mister/copy.sh).  Usage: sdk-runtime.sh <sdk_checkout_dir>
#
DEST="$1"
[ -n "$DEST" ] || { echo "usage: $0 <sdk_checkout_dir>"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
GREEN='\033[32m'; RESET='\033[0m'
ok() { printf "  ${GREEN}%s${RESET} → %s\n" "$1" "$2"; }

mkdir -p "$DEST/runtime/mister"

# NB: the MiSTer core bitstream (openfpgaOS.rbf) is intentionally NOT synced
# here.  The core is released independently from the openfpgaOS repo
# (`make package/release TARGET=mister` -> a release zip + Downloader DB) and
# installed once to _Computer/OpenfpgaOS.rbf.  Game repos reference the
# released core; they no longer vendor the bitstream in their runtime tree.

# Kernel (boot.rom) — the mister build tree is per-target, so its os.bin
# is unambiguously a mister build (copied only if it exists).
if [ -f "$ROOT/src/firmware/os/bld/mister/os.bin" ]; then
    cp "$ROOT/src/firmware/os/bld/mister/os.bin" "$DEST/runtime/mister/os.bin"
    ok "os.bin" "runtime/mister/"
fi

exit 0
