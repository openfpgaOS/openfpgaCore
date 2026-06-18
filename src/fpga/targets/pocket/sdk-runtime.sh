#!/bin/bash
#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------
#
# openfpgaOS — Pocket target: export this target's runtime artifacts into
# an SDK checkout.  The root `make sdk` calls one of these per target
# (sdk-runtime.sh) so the shared sync names no target — adding a target =
# add a directory with this script.  Run from anywhere; paths resolve via
# $0.  Usage: sdk-runtime.sh <sdk_checkout_dir>
#
DEST="$1"
[ -n "$DEST" ] || { echo "usage: $0 <sdk_checkout_dir>"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
GREEN='\033[32m'; RESET='\033[0m'
ok() { printf "  ${GREEN}%s${RESET} → %s\n" "$1" "$2"; }

# Pocket keeps its target-specific artifacts under runtime/pocket/ —
# uniform with runtime/mister/.  (The shared, target-agnostic bank.ofsf
# is written by the root sdk rule to runtime/ itself.)
RTP="$DEST/runtime/pocket"
mkdir -p "$RTP"

# reverse_bits host tool (APF loads the bitstream MSB-first).
RB="$ROOT/tools/reverse_bits"
[ -x "$RB" ] || gcc -O2 -o "$RB" "$ROOT/tools/reverse_bits.c"

# Reversed FPGA bitstream, named by the variant that produced the current
# rbf (os25 → os25.rbf_r, os30 → os30.rbf_r — the name is the variant token,
# kept short for the Pocket's ~15-char filename cap).  The build stamps
# output_files/.last_variant; successive os25/os30 builds + syncs accumulate
# BOTH files here so one SDK can build cores of either variant.
VARIANT="$(cat "$SCRIPT_DIR/output_files/.last_variant" 2>/dev/null || echo os25)"
case "$VARIANT" in os30) BNAME=os30 ;; *) BNAME=os25 ;; esac
if [ -f "$SCRIPT_DIR/output_files/ap_core.rbf" ]; then
    "$RB" "$SCRIPT_DIR/output_files/ap_core.rbf" "$RTP/$BNAME.rbf_r" >/dev/null
    ok "$BNAME.rbf_r" "runtime/pocket/"
fi

# Kernel — only when the on-disk os.bin was built for THIS target (the
# firmware build stamp), so a mister kernel never lands in the pocket slot.
if [ "$(cat "$ROOT/src/firmware/os/.last_target" 2>/dev/null)" = "pocket" ] && \
   [ -f "$ROOT/src/firmware/os/os.bin" ]; then
    cp "$ROOT/src/firmware/os/os.bin" "$RTP/os.bin"
    ok "os.bin" "runtime/pocket/"
fi

# chip32 loader + .sof for JTAG reset (scripts/debug.sh on the SDK side).
[ -f "$ROOT/src/chip32/pocket/loader.bin" ] && \
    cp "$ROOT/src/chip32/pocket/loader.bin" "$RTP/" && ok "loader.bin" "runtime/pocket/"
[ -f "$SCRIPT_DIR/output_files/ap_core.sof" ] && \
    cp "$SCRIPT_DIR/output_files/ap_core.sof" "$RTP/" && ok "ap_core.sof" "runtime/pocket/ (JTAG reset)"

exit 0
