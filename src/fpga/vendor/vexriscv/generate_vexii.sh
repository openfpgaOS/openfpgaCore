#!/bin/bash
# Generate VexiiRiscv for openfpgaOS using the stock vexiiriscv.Generate
# entry point (no custom Scala wrapper).  FetchL1 and LsuL1 are exposed
# directly as AXI4 masters; all region routing is handled by the fabric
# downstream (cpu_system.v).  Cache sizing and Zicbom retained from the
# pre-FMax openfpgaOS config.
#
# Config highlights:
#   I-cache: 32 KB (256 sets × 2 ways × 64 B line, NL prefetch)
#            readAt=1 / ctrlAt=3 gives the fitter one extra fetch stage
#            between execute-side backpressure and the M10K read-enable
#            cone. This recovers much of the old high-Fmax pipeline win
#            without disabling the proven-safe bypass network.
#   D-cache: 128 KB (1024 sets × 2 ways × 64 B line, 32-bit banks, NO HW
#            prefetch — the `rpt` prefetcher speculated past PMA
#            boundaries and surfaced bus faults to commit).  Fits the
#            Pocket ONLY with the qsf PHYSICAL_SYNTHESIS_* knobs OFF
#            (their register duplication/retiming inflated the design
#            ~1,000 ALMs; measured 2026-06-09: 92% ALM / 308/308 M10K /
#            WNS -0.73 with them off vs LAB overflow with them on).
#            64-bit banks buy nothing through the 32-bit AXI fabric and
#            their 512-set emission crashes Quartus 25.1's Verific
#            frontend (VRFX operators.cpp:2349) — keep 32-bit banks.
#   FPU    : shared add/FMA pipeline starts pre-shift one stage later so
#            ctrl5 FMA lane selection is captured before the exponent and
#            mantissa compare cone. The packer writeback stage is shortened
#            because subnormal recoding is disabled in this core.
#   Branch : BTB 256 sets + GShare 1 KB + RAS, relaxed branch pipeline
#            (jumpAt=1 — gives the fitter an extra cycle on BTB-hit paths)
#   FPU    : single-precision (F extension), subnormals flushed/ignored to
#            remove the unpacker normalizer freeze path from the 100 MHz
#            execute-control critical path.
#   Zicbom : cache block management (cbo.clean/flush/inval)
#   Bypass : --allow-bypass-from=0 (full combinational bypass enabled)
#            Bypass-from=2 disables the early-ALU bypass entirely and
#            relies on stall insertion to resolve back-to-back RAWs.
#            Hit a miscompare on hardware running gpudemo Mode 0:
#              10324550: lui  a4, 0x1036e        ; a4 <= 0x1036e000
#              10324554: sw   zero, -1772(a4)    ; trap, mtval=0x000000e3
#            The SW's ctrl1 operand read saw a stale a4 (last value from
#            the byte-write loop at 0x10324514) instead of the freshly
#            LUI'd 0x1036e000, producing a misaligned effective address
#            into BRAM space.  Reverting to bypass=0 restores the
#            combinational ctrl4->ctrl1 forwarding; trades the -1.99 ns
#            FPU F2I cone (which --relaxed-src already partly mitigates)
#            for provably correct execution on the integer pipeline.
#
# Memory regions (PMA):
#   0x00000000  32 KB  BRAM    — main=0, exe=1  (non-speculative, executable)
#   0x10000000  64 MB  SDRAM   — main=1, exe=1  (cached, executable)
#   0x20000000 256 MB  reserved — main=0, exe=0
#   0x30000000  16 MB  CRAM0   — main=0, exe=0  (uncached, non-executable:
#                                ALL CRAM0 traffic goes down the LSU's
#                                uncached per_axi path; cpu_system.v's
#                                port_cram0 per_axi-only specialization
#                                depends on these flags — re-extend it to
#                                the full cpu_target_port if main/exe is
#                                ever re-enabled here)
#   0x38000000 128 MB  uncached alias — main=0, exe=0
#   0x40000000   1 GB  IO + SDRAM_UC — main=0, exe=0
#
# Prerequisites: java, sbt
# Usage: ./generate_vexii.sh [pocket|mister]
#
# Variants (same ISA, same PMA map, same AXI master interface — the
# downstream cpu_system.v fabric is identical for both):
#   pocket (default) — single-issue, every flag above tuned against the
#                      Pocket's Cyclone V E A4 / C8 grade at 100 MHz.
#                      Output: VexiiRiscv/VexiiRiscv.v
#   mister           — the same config plus DUAL ISSUE (--decoders=2
#                      --lanes=2) for the DE10-Nano's Cyclone V SE A6 /
#                      I7 grade (2.3x the ALMs, faster grade than C8).
#                      Issue width is microarchitectural: firmware and
#                      apps are unchanged.  Late-ALU is a deliberate
#                      follow-up knob — enable only after STA shows
#                      headroom on a plain dual-issue build.
#                      Output: VexiiRiscv/VexiiRiscv_mister.v

set -e

VARIANT="${1:-os25}"

# ── Per-variant cache sizing ──────────────────────────────────────────
# Both are (sets × 2 ways × 64 B line):
#   I$ sets:  256 = 32 KB,  128 = 16 KB
#   D$ sets: 1024 = 128 KB,  512 = 64 KB
# All current Pocket/MiSTer variants run 32 KB I$ / 128 KB D$.  These are the
# ONLY place cache size is set — change a variant's sets here and re-run
# `make cpu VARIANT=<v>`; nothing else (no qsf macro) encodes cache size.
ICACHE_SETS=256          # 32 KB I$
DCACHE_SETS=1024         # 128 KB D$

# Each variant generates its OWN netlist file (VexiiRiscv_<variant>.v); the
# per-variant build picks it up directly.  No shared "default" netlist.
case "$VARIANT" in
    os25)
        EXTRA_FLAGS=""
        OUTPUT_NAME="VexiiRiscv_os25.v"
        ;;
    os30)
        EXTRA_FLAGS=""
        OUTPUT_NAME="VexiiRiscv_os30.v"
        ;;
    mister)
        EXTRA_FLAGS="--decoders=2 --lanes=2"
        OUTPUT_NAME="VexiiRiscv_mister.v"
        ;;
    *)
        echo "Error: unknown variant '$VARIANT' (os25|os30|mister)"
        exit 1
        ;;
esac

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VEXII_DIR="$SCRIPT_DIR/VexiiRiscv"

if [ ! -d "$VEXII_DIR" ]; then
    echo "Error: VexiiRiscv directory not found at $VEXII_DIR"
    exit 1
fi

echo "Generating VexiiRiscv [$VARIANT] (stock vexiiriscv.Generate, openfpgaOS cache sizing)..."
cd "$VEXII_DIR"

# sbt always emits the bare name VexiiRiscv.v here; clear any stale copy from
# an interrupted run so the post-gen rename can't pick up an old netlist.
# Each variant's final file is VexiiRiscv_<variant>.v (no shared default).
rm -f "$VEXII_DIR/VexiiRiscv.v"

sbt -Dsbt.server.forcestart=true --batch "Test/runMain vexiiriscv.Generate \
      --xlen=32 \
      --with-rvm --with-rva --with-rvf --with-rvc \
      --with-rvZcbm \
      --with-fetch-l1 --fetch-l1-sets=$ICACHE_SETS --fetch-l1-ways=2 --fetch-l1-refill-count=2 \
      --fetch-l1-mem-data-width-min=64 \
      --fetch-l1-read-at=1 --fetch-l1-hits-at=2 --fetch-l1-hit-at=2 \
      --fetch-l1-bank-muxes-at=2 --fetch-l1-bank-mux-at=3 --fetch-l1-ctrl-at=3 \
      --fetch-l1-hardware-prefetch=nl --fetch-axi4 \
      --with-lsu-l1 --lsu-l1-sets=$DCACHE_SETS --lsu-l1-ways=2 \
      --lsu-l1-refill-count=2 --lsu-l1-writeback-count=2 \
      --lsu-l1-store-buffer-slots=2 --lsu-l1-store-buffer-ops=16 \
      --lsu-l1-axi4 \
      --lsu-software-prefetch --lsu-hardware-prefetch=none \
      --with-btb --btb-sets=512 --relaxed-btb --relaxed-btb-hit \
      --with-gshare --gshare-bytes=4096 --with-ras \
      --allow-bypass-from=0 \
      --fpu-ignore-subnormal \
      --fpu-wb-at=1 \
      --fpu-add-preshift-stage=1 --fpu-add-shifter-stage=2 \
      --fpu-add-math-stage=3 --fpu-add-norm-stage=4 --fpu-add-pack-at=5 \
      --relaxed-src --relaxed-branch --relaxed-div --relaxed-shift \
      --div-radix=2 --with-store-rs2-late \
      --dispatcher-at=2 \
      --reset-vector=0 \
      --region base=0,size=8000,main=0,exe=1 \
      --region base=10000000,size=4000000,main=1,exe=1 \
      --region base=20000000,size=10000000,main=0,exe=0 \
      --region base=30000000,size=1000000,main=0,exe=0 \
      --region base=40000000,size=40000000,main=0,exe=0 \
      $EXTRA_FLAGS"

GENERATED="$VEXII_DIR/VexiiRiscv.v"
OUTPUT="$VEXII_DIR/$OUTPUT_NAME"
if [ ! -f "$GENERATED" ]; then
    echo "ERROR: VexiiRiscv.v not found after generation"
    exit 1
fi
# The module inside stays named VexiiRiscv (cpu_system.v instantiates it by
# name); only the file is per-variant.  Rename the bare sbt output.
mv -f "$GENERATED" "$OUTPUT"

if [ "$VARIANT" = "os25" ] || [ "$VARIANT" = "os30" ]; then
# The generated execute_freeze_valid cone fans into the whole CPU
# ready/valid network.  Quartus otherwise tends to keep it as one huge
# high-fanout control net, which leaves the worst path from execute/FPU
# control back to the I-cache read enable route-limited.  Add a local
# synthesis hint after generation so the fitter can insert replicas.
# POCKET-ONLY: this hint (and the rejected ones in the notes below) was
# A/B-tuned against the A4/C8 fitter; the MiSTer A6/I7 netlist starts
# clean and gets its own pass only if STA demands one.
perl -0pi -e 's/  wire                execute_freeze_valid;/  (* maxfan = 16 *) wire                execute_freeze_valid;/' "$OUTPUT"
if ! grep -q "maxfan = 16.*execute_freeze_valid" "$OUTPUT"; then
    echo "ERROR: failed to annotate execute_freeze_valid maxfan hint"
    exit 1
fi
fi

# NOTE: maxfan=8 annotations on LsuL1 banksWrite_mask/waysWrite_mask were
# A/B-tested 2026-06-09 and REGRESSED (WNS -1.119 -> -1.252, TNS -254 ->
# -464) — same backfire mode as the decode_ctrls_0_up_isReady trial.
# Mid-cone replication hurts this design; do not re-add either.

# NOTE: a decode_ctrls_0_up_isReady maxfan=16 annotation was trialled
# (2026-06-09) and REGRESSED WNS -0.95 -> -1.75: the replicas fed the
# AlignerPlugin buffer through extra hops.  The structural fix for the
# fetch->aligner->decode->dispatch cone is --dispatcher-at=2 above
# (registers the rsHazardChecker inputs); do not re-add the annotation.

# Some SpinalHDL emitter paths produce simulation randomisation
# ($urandom) that Quartus rejects during Analysis & Synthesis.  The
# current flag set emits none, but any future flag/version change can
# reintroduce it — scrub to deterministic zero (the values are not
# architecturally observable) and fail fast if any survive.
perl -pi -e 's/= \{\$urandom\};/= 0;/' "$OUTPUT"
if grep -q '\$urandom' "$OUTPUT"; then
    echo "ERROR: unsupported \$urandom remains in generated Verilog"
    exit 1
fi

echo ""
echo "Done! Generated $OUTPUT ($VARIANT)"
echo "Top module: VexiiRiscv"
echo "Masters   : FetchL1Axi4Plugin_logic_axi_* (I\$), LsuL1Axi4Plugin_logic_axi_* (D\$)"
