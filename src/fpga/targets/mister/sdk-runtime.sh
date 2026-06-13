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

# MiSTer loads a plain (non-reversed) .rbf.
if [ -f "$SCRIPT_DIR/output_files/mister.rbf" ]; then
    cp "$SCRIPT_DIR/output_files/mister.rbf" "$DEST/runtime/mister/openfpgaOS.rbf"
    ok "openfpgaOS.rbf" "runtime/mister/"
fi

# Kernel — only when the on-disk os.bin was built for THIS target.
if [ "$(cat "$ROOT/src/firmware/os/.last_target" 2>/dev/null)" = "mister" ] && \
   [ -f "$ROOT/src/firmware/os/os.bin" ]; then
    cp "$ROOT/src/firmware/os/os.bin" "$DEST/runtime/mister/os.bin"
    ok "os.bin" "runtime/mister/"
fi

exit 0
