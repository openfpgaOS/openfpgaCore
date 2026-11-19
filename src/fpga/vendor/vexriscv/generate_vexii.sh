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
# Usage: ./generate_vexii.sh [<variant>]        (default: os25)
#
# ── Per-variant CPU config: configs/<variant>.cfg ────────────────────
# Every variant/target sources its OWN config file next to this script:
#   configs/os25.cfg   configs/os30.cfg   configs/mister.cfg   ...
# A config sets the cache geometry (ICACHE_SETS/DCACHE_SETS), any extra
# vexiiriscv.Generate flags (EXTRA_FLAGS — e.g. dual issue, FMA accuracy),
# and post-generation synthesis hints (MAXFAN_HINT).  Adding a variant is a
# file drop — no edits to this script.  The shared base config below (ISA,
# PMA map, pipeline/branch/FPU tuning, AXI interface) is identical for all
# variants; the downstream cpu_system.v fabric never changes.
#
# Each variant generates its OWN netlist file (VexiiRiscv_<variant>.v); the
# per-variant build names it directly in its qsf.  No shared "default"
# netlist.  The module inside stays named VexiiRiscv.

set -e

VARIANT="${1:-os25}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VEXII_DIR="$SCRIPT_DIR/VexiiRiscv"

# ── Load the variant config ───────────────────────────────────────────
# Defaults (overridden by the sourced .cfg): 32 KB I$ / 128 KB D$, no extra
# flags, no maxfan hint.  Cache sets are (sets × 2 ways × 64 B line):
#   I$ sets:  256 = 32 KB,  128 = 16 KB
#   D$ sets: 1024 = 128 KB,  512 = 64 KB
# The .cfg is the ONLY place a variant's cache size / CPU flags are set —
# change it and re-run `make cpu VARIANT=<v>`; nothing else (no qsf macro)
# encodes CPU config.
ICACHE_SETS=256          # 32 KB I$
DCACHE_SETS=1024         # 128 KB D$
GSHARE_BYTES=4096        # GShare predictor RAM (4 M10K at 4096)
BTB_SETS=512             # BTB sets (3 M10K at 512)
LSU_SW_PREFETCH=1        # Zicbop prefetch.r/w decode (0 = omit)
FPU_WB_AT=1              # FPU→int writeback stage (upstream default 2; we
                         # ship 1 — tighter.  Overridable per-variant .cfg.)
FETCH_REFILL_COUNT=2    # I$ outstanding refill slots (os30 qualifies one).
LSU_REFILL_COUNT=2      # D$ outstanding refill slots.
LSU_WRITEBACK_COUNT=2   # D$ outstanding writeback slots.
OS30_FP_RS3_GROUPED=0   # Exact checked os30 FP RS3 selection factoring.
OS30_FRONTEND_FLAT=0    # Exact checked os30 frontend Boolean factoring.
LSU_L1_WAYS=2            # D$ associativity (sets x ways x 64 B line)
LSU_SB_SLOTS=2           # D$ store-buffer line slots
LSU_SB_OPS=16            # D$ store-buffer op capacity
DIV_RADIX=2             # Integer and FP divider radix; qualified per variant.
EXTRA_FLAGS=""
DISPATCHER_AT=2         # Preserve every existing variant unless explicitly selected.
OS30_RVC_PIPELINE=0     # os30-only isolated Scala source overlay.
OS20_LSU_DISPATCH=0     # os20-only isolated Scala source overlay.
RELAXED_SHIFT=1         # 0 = single-cycle shifts (no extra execute stage).
MAXFAN_HINT=0
FETCH_READ_HOLD=0        # Preserve stalled reads using a registered selector.
ALIGNER_PC_HOLD=0        # Preserve the buffered PC with a registered enable.
FETCH_READY_FACTOR=0     # Factor the late dispatch hazard out of fetch control.
FPU_MATCH_REG=0          # Precompute FPU register matches alongside pipeline IDs.
FETCH_PIPELINE_HOLD=0    # Preserve fetch payloads with registered capture selectors.
OS20_TIMING=0            # Qualified dual-issue frontend and FPU selection rewrites.
FETCH_L1_PREFETCH=nl     # I$ hardware prefetch: nl (next line) or none.
RELAXED_LEARN=0          # 1 = register the branch-predictor update (+1 cycle).
NO_SHIFT_TAPS=0          # 1 = keep Quartus from packing CPU pipeline FFs into RAM.
FPU_I2F_PIPELINED=0      # 1 = fcvt.s.w[u] normalizes in-lane (no freeze, patch 0003).
LSU_WRITE_REG=0          # 1 = register D$ bank writes, bypass them into reads.

CONFIG="$SCRIPT_DIR/configs/$VARIANT.cfg"
if [ ! -f "$CONFIG" ]; then
    echo "Error: unknown variant '$VARIANT' — no $CONFIG"
    echo "Available configs:"
    for f in "$SCRIPT_DIR"/configs/*.cfg; do
        [ -f "$f" ] && echo "  $(basename "$f" .cfg)"
    done
    exit 1
fi
# shellcheck source=/dev/null
. "$CONFIG"
OUTPUT_NAME="VexiiRiscv_$VARIANT.v"

LSU_SW_PREFETCH_FLAG="--lsu-software-prefetch"
[ "$LSU_SW_PREFETCH" = 0 ] && LSU_SW_PREFETCH_FLAG=""
RELAXED_SHIFT_FLAG="--relaxed-shift"
[ "$RELAXED_SHIFT" = 0 ] && RELAXED_SHIFT_FLAG=""
RELAXED_LEARN_FLAG=""
[ "$RELAXED_LEARN" = 1 ] && RELAXED_LEARN_FLAG="--relaxed-learn"
[ "$FPU_I2F_PIPELINED" = 1 ] && RELAXED_LEARN_FLAG="$RELAXED_LEARN_FLAG --fpu-i2f-pipelined"

if [ ! -d "$VEXII_DIR" ]; then
    echo "Error: VexiiRiscv directory not found at $VEXII_DIR"
    exit 1
fi

echo "Generating VexiiRiscv [$VARIANT] (stock vexiiriscv.Generate, openfpgaOS cache sizing)..."
cd "$VEXII_DIR"

# Keep local generator fixes reproducible across fresh submodule checkouts.
# Accept an already-applied patch, but stop if the source no longer matches.
for source_patch in "$SCRIPT_DIR"/patches/*.patch; do
    [ -f "$source_patch" ] || continue
    if git apply --reverse --check "$source_patch" >/dev/null 2>&1; then
        continue
    fi
    git apply --check "$source_patch"
    git apply "$source_patch"
done

# The optional RVC pipeline must never patch the shared generator tree:
# other variants (including os30_worker) can elaborate concurrently. A killed
# process can leave only its private temporary copy, never changed source.
GENERATION_DIR="$VEXII_DIR"
if [ "$OS30_RVC_PIPELINE" = 1 ]; then
    [ "$VARIANT" = os30 ] || { echo "RVC pipeline is qualified only for os30" >&2; exit 1; }
    GENERATION_DIR=$(mktemp -d "$SCRIPT_DIR/.os30-rvc.XXXXXXXX")
    cleanup_os30_source() { rm -rf -- "$GENERATION_DIR"; }
    trap cleanup_os30_source EXIT
    # Preserve all build inputs, but never reuse compiled classes or outputs.
    (set -o pipefail; tar --exclude=.git --exclude=target --exclude='VexiiRiscv*.v' \
        --exclude=vexii_active.v -cf - . | tar -xf - -C "$GENERATION_DIR")
    cd "$GENERATION_DIR"
    sha256sum -c <<'OS30_SOURCE_BEFORE'
868df74562a7d566a56ee5f3f4fbfb976978279c37b686b610cb1915ff7af9a6  src/main/scala/vexiiriscv/Param.scala
f4caaf7bb9f885ac252ca32cd751a9394076d26da789ebf324af9ea946626460  src/main/scala/vexiiriscv/decode/AlignerPlugin.scala
OS30_SOURCE_BEFORE
    git apply --check "$SCRIPT_DIR/os30-rvc-pipeline.patch"
    git apply "$SCRIPT_DIR/os30-rvc-pipeline.patch"
    sha256sum -c <<'OS30_SOURCE_AFTER'
d77c0726a3c33dafe4cc4736530c80a0a35818e038a389f7ed8ffe6b68769e6e  src/main/scala/vexiiriscv/Param.scala
4d6e618b6ee7d504e40fa11523200765cb36a8747ff5379a24d7c2e4286ecb32  src/main/scala/vexiiriscv/decode/AlignerPlugin.scala
OS30_SOURCE_AFTER
fi

# os20 store-buffer occupancy counter and decode-stage bypass flags. Applied
# to a private copy for the same reason: every other variant keeps generating
# from unchanged source.
if [ "$OS20_LSU_DISPATCH" = 1 ]; then
    [ "$VARIANT" = os20 ] || { echo "LSU/dispatch overlay is qualified only for os20" >&2; exit 1; }
    GENERATION_DIR=$(mktemp -d "$SCRIPT_DIR/.os20-lsu-dispatch.XXXXXXXX")
    cleanup_os20_source() { rm -rf -- "$GENERATION_DIR"; }
    trap cleanup_os20_source EXIT
    (set -o pipefail; tar --exclude=.git --exclude=target --exclude='VexiiRiscv*.v' \
        --exclude=vexii_active.v -cf - . | tar -xf - -C "$GENERATION_DIR")
    cd "$GENERATION_DIR"
    sha256sum -c <<'OS20_SOURCE_BEFORE'
de0ca41f2905bcfb80b030b208ca29c9ff9d4a3fbb8846ca3f0c35afc7c249c4  src/main/scala/vexiiriscv/schedule/DispatchPlugin.scala
2f29a2fdcd8983cdf94d6bf3da5620bdfaa8d8d10cca42e7293c8539f35f6622  src/main/scala/vexiiriscv/execute/lsu/LsuPlugin.scala
OS20_SOURCE_BEFORE
    git apply --check "$SCRIPT_DIR/os20-lsu-dispatch.patch"
    git apply "$SCRIPT_DIR/os20-lsu-dispatch.patch"
    sha256sum -c <<'OS20_SOURCE_AFTER'
178aad52609a4c3270da08ad720fd6167659de09d4b62afdba8866968b2f4094  src/main/scala/vexiiriscv/schedule/DispatchPlugin.scala
6d1239371d3fa492fd6ff2eff96203a1c7be4ed2e9e4e755da8a4dbc20d68c72  src/main/scala/vexiiriscv/execute/lsu/LsuPlugin.scala
OS20_SOURCE_AFTER
fi

# sbt always emits the bare name VexiiRiscv.v here; clear any stale copy from
# an interrupted run so the post-gen rename can't pick up an old netlist.
# Each variant's final file is VexiiRiscv_<variant>.v (no shared default).
rm -f "$GENERATION_DIR/VexiiRiscv.v"

sbt -Dsbt.server.forcestart=true --batch "Test/runMain vexiiriscv.Generate \
      --xlen=32 \
      --with-rvm --with-rva --with-rvf --with-rvc \
      --with-rvZcbm \
      --with-fetch-l1 --fetch-l1-sets=$ICACHE_SETS --fetch-l1-ways=2 --fetch-l1-refill-count=$FETCH_REFILL_COUNT \
      --fetch-l1-mem-data-width-min=64 \
      --fetch-l1-read-at=1 --fetch-l1-hits-at=2 --fetch-l1-hit-at=2 \
      --fetch-l1-bank-muxes-at=2 --fetch-l1-bank-mux-at=3 --fetch-l1-ctrl-at=3 \
      --fetch-l1-hardware-prefetch=$FETCH_L1_PREFETCH --fetch-axi4 \
      --with-lsu-l1 --lsu-l1-sets=$DCACHE_SETS --lsu-l1-ways=$LSU_L1_WAYS \
      --lsu-l1-refill-count=$LSU_REFILL_COUNT --lsu-l1-writeback-count=$LSU_WRITEBACK_COUNT \
      --lsu-l1-store-buffer-slots=$LSU_SB_SLOTS --lsu-l1-store-buffer-ops=$LSU_SB_OPS \
      --lsu-l1-axi4 \
      $LSU_SW_PREFETCH_FLAG --lsu-hardware-prefetch=none \
      --with-btb --btb-sets=$BTB_SETS --relaxed-btb --relaxed-btb-hit \
      --with-gshare --gshare-bytes=$GSHARE_BYTES --with-ras \
      --allow-bypass-from=0 \
      --fpu-ignore-subnormal \
      --fpu-wb-at=$FPU_WB_AT \
      --fpu-add-preshift-stage=1 --fpu-add-shifter-stage=2 \
      --fpu-add-math-stage=3 --fpu-add-norm-stage=4 --fpu-add-pack-at=5 \
      --relaxed-src --relaxed-branch --relaxed-div $RELAXED_SHIFT_FLAG \
      --div-radix=$DIV_RADIX --with-store-rs2-late \
      --dispatcher-at=$DISPATCHER_AT \
      --reset-vector=0 \
      --region base=0,size=8000,main=0,exe=1 \
      --region base=10000000,size=4000000,main=1,exe=1 \
      --region base=20000000,size=10000000,main=0,exe=0 \
      --region base=30000000,size=1000000,main=0,exe=0 \
      --region base=40000000,size=40000000,main=0,exe=0 \
      $RELAXED_LEARN_FLAG $EXTRA_FLAGS"

GENERATED="$GENERATION_DIR/VexiiRiscv.v"
FINAL_OUTPUT="$VEXII_DIR/$OUTPUT_NAME"
if [ ! -f "$GENERATED" ]; then
    echo "ERROR: VexiiRiscv.v not found after generation"
    exit 1
fi
# Transform the staging file first. A failed post-processing check must not
# replace a previously built CPU or leave an invalid netlist newer than its
# configuration, which would let the next make invocation reuse it.
OUTPUT="$GENERATED"

if [ "${MAXFAN_HINT:-0}" != 0 ]; then
# The generated execute_freeze_valid cone fans into the whole CPU
# ready/valid network.  Quartus otherwise tends to keep it as one huge
# high-fanout control net, which leaves the worst path from execute/FPU
# control back to the I-cache read enable route-limited.  Add a local
# synthesis hint after generation so the fitter can insert replicas.
# Enabled per-variant via MAXFAN_HINT in configs/<variant>.cfg (the hint
# and the rejected ones in the notes below were A/B-tuned against the
# Pocket A4/C8 fitter; the MiSTer A6/I7 config leaves it off).
perl -0pi -e 's/  wire                execute_freeze_valid;/  (* maxfan = '"$MAXFAN_HINT"' *) wire                execute_freeze_valid;/' "$OUTPUT"
if ! grep -q "maxfan = $MAXFAN_HINT.*execute_freeze_valid" "$OUTPUT"; then
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

# Quartus' automatic shift-register replacement packs runs of same-shape
# pipeline control flops (FPU/int writeback selects, format ids) into
# altshift_taps M10Ks; the RAM output then sits on writeback/bypass paths
# (-0.83 ns on os30).  A module-level logic option keeps them as flops.
if [ "$NO_SHIFT_TAPS" = 1 ]; then
    perl -0pi -e 's/^module VexiiRiscv \(/(* altera_attribute = "-name AUTO_SHIFT_REGISTER_RECOGNITION OFF" *)\nmodule VexiiRiscv (/m' "$OUTPUT"
    if ! grep -q 'AUTO_SHIFT_REGISTER_RECOGNITION OFF' "$OUTPUT"; then
        echo "ERROR: failed to annotate VexiiRiscv shift-register recognition"
        exit 1
    fi
fi

# Keep late fetch-ready signals off the MiSTer RAM read-enable ports.
# The generated read and stall behavior remains cycle-for-cycle identical.
if [ "$FETCH_READ_HOLD" = 1 ]; then
    perl "$SCRIPT_DIR/retime_fetch_reads.pl" "$OUTPUT"
fi
if [ "$ALIGNER_PC_HOLD" = 1 ]; then
    perl "$SCRIPT_DIR/retime_aligner_pc.pl" "$OUTPUT"
fi
if [ "$FETCH_READY_FACTOR" = 1 ]; then
    perl "$SCRIPT_DIR/factor_fetch_ready.pl" "$OUTPUT"
fi
if [ "$FPU_MATCH_REG" = 1 ]; then
    perl "$SCRIPT_DIR/retime_fpu_matches.pl" "$OUTPUT"
fi
if [ "$FETCH_PIPELINE_HOLD" = 1 ]; then
    perl "$SCRIPT_DIR/retime_fetch_pipeline.pl" "$OUTPUT"
fi
if [ "$OS20_TIMING" = 1 ]; then
    perl "$SCRIPT_DIR/optimize_os20_timing.pl" "$OUTPUT"
fi
# Take the store hit/redo cone off the D$ M10K write enables; reads bypass the
# pending write, so every load and victim read returns the same data.
if [ "$LSU_WRITE_REG" = 1 ]; then
    perl "$SCRIPT_DIR/retime_dcache_writes.pl" "$OUTPUT"
fi

# Keep this final: its hash guard includes every preceding qualified helper.
# Failure leaves the previous published CPU intact, as with the other passes.
if [ "$OS30_FP_RS3_GROUPED" = 1 ]; then
    perl "$SCRIPT_DIR/factor_os30_fp_rs3.pl" "$VARIANT" "$OUTPUT"
fi

# This pass binds the exact output of the qualified RS3/helper chain.
if [ "$OS30_FRONTEND_FLAT" = 1 ]; then
    perl "$SCRIPT_DIR/factor_os30_frontend.pl" "$VARIANT" "$OUTPUT"
fi

# Exact os30 registered-RVC output only; other variants retain their chain.
if [ "$OS30_RVC_PIPELINE" = 1 ]; then
    perl "$SCRIPT_DIR/factor_os30_load_forward.pl" "$VARIANT" "$OUTPUT"
fi

# Exact os30 registered-RVC output only; other variants retain their chain.
if [ "$OS30_RVC_PIPELINE" = 1 ]; then
    perl "$SCRIPT_DIR/factor_os30_control_paths.pl" "$VARIANT" "$OUTPUT"
fi

# Exact os30 registered-RVC output only; other variants retain their chain.
if [ "$OS30_RVC_PIPELINE" = 1 ]; then
    perl "$SCRIPT_DIR/factor_os30_parallel_paths.pl" "$VARIANT" "$OUTPUT"
fi

# Exact os30 registered-RVC output only; other variants retain their chain.
if [ "$OS30_RVC_PIPELINE" = 1 ]; then
    perl "$SCRIPT_DIR/factor_os30_endpoint_paths.pl" "$VARIANT" "$OUTPUT"
fi

mv -f "$OUTPUT" "$FINAL_OUTPUT"
OUTPUT="$FINAL_OUTPUT"
echo ""
echo "Done! Generated $OUTPUT ($VARIANT)"
echo "Top module: VexiiRiscv"
echo "Masters   : FetchL1Axi4Plugin_logic_axi_* (I\$), LsuL1Axi4Plugin_logic_axi_* (D\$)"
