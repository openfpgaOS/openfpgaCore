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

# Reversed FPGA bitstream(s).  Each variant builds in its OWN isolated dir
# bld/<variant>/output_files/; publish every variant that has been built,
# named by the variant token (os25.rbf_r / os30.rbf_r — kept short for the
# Pocket's ~15-char filename cap).  One SDK can then build cores of either.
for d in "$SCRIPT_DIR"/bld/*/output_files; do
    [ -f "$d/ap_core.rbf" ] || continue
    v="$(basename "$(dirname "$d")")"
    # Skip the seed-sweep scratch dirs (bld/<variant>-s<N>/): they are not
    # deployable builds, just per-seed timing fits.  Publish only the canonical
    # per-variant dirs (bld/<variant>/), so `make sdk` after a sweep ships one
    # bitstream per variant, not one per seed.
    case "$v" in *-s[0-9]*) continue ;; esac
    "$RB" "$d/ap_core.rbf" "$RTP/$v.rbf_r" >/dev/null
    ok "$v.rbf_r" "runtime/pocket/"
done

# Kernel — the pocket build tree is per-target now, so its os.bin is
# unambiguously a pocket build (a mister kernel can't land here).
if [ -f "$ROOT/src/firmware/os/bld/pocket/os.bin" ]; then
    cp "$ROOT/src/firmware/os/bld/pocket/os.bin" "$RTP/os.bin"
    ok "os.bin" "runtime/pocket/"
fi

# chip32 loader + .sof for JTAG reset (scripts/debug.sh on the SDK side).
# Reassemble first (bass no-ops when loader.asm is unchanged) so a stale
# loader.bin is never propagated into the SDK.
make -s -C "$ROOT/src/fpga/targets/pocket/chip32" >/dev/null 2>&1 || true
[ -f "$ROOT/src/fpga/targets/pocket/chip32/loader.bin" ] && \
    cp "$ROOT/src/fpga/targets/pocket/chip32/loader.bin" "$RTP/" && ok "loader.bin" "runtime/pocket/"
for d in "$SCRIPT_DIR"/bld/*/output_files; do \
    v="$(basename "$(dirname "$d")")"; case "$v" in *-s[0-9]*) continue ;; esac; \
    if [ -f "$d/ap_core.sof" ]; then cp "$d/ap_core.sof" "$RTP/ap_core.sof"; ok "ap_core.sof" "runtime/pocket/ (JTAG reset)"; break; fi; \
done

exit 0
