#!/bin/bash
# Generate VexiiRiscv for openfpgaOS using the stock vexiiriscv.Generate
# entry point (no custom Scala wrapper).  FetchL1 and LsuL1 are exposed
# directly as AXI4 masters; all region routing is handled by the fabric
# downstream (cpu_system.v).  Cache sizing and Zicbom retained from the
# pre-FMax openfpgaOS config.
#
# Config highlights:
#   I-cache: 32 KB (256 sets × 2 ways × 64 B line, NL prefetch)
#   D-cache: 32 KB (256 sets × 2 ways × 64 B line, NO HW prefetch —
#            the `rpt` prefetcher speculated past PMA boundaries and
#            surfaced bus faults to commit; disabling also frees ~N ALMs
#            of prefetcher logic and reduces placement pressure.)
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
#   0x30000000  16 MB  CRAM0   — main=1, exe=1  (cacheable bridge staging)
#   0x38000000 128 MB  uncached alias — main=0, exe=0
#   0x40000000   1 GB  IO + SDRAM_UC — main=0, exe=0
#
# Prerequisites: java, sbt
# Usage: ./generate_vexii.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VEXII_DIR="$SCRIPT_DIR/VexiiRiscv"

if [ ! -d "$VEXII_DIR" ]; then
    echo "Error: VexiiRiscv directory not found at $VEXII_DIR"
    exit 1
fi

echo "Generating VexiiRiscv (stock vexiiriscv.Generate, openfpgaOS cache sizing)..."
cd "$VEXII_DIR"

sbt "Test/runMain vexiiriscv.Generate \
      --xlen=32 \
      --with-rvm --with-rva --with-rvf --with-rvc \
      --with-rvZcbm \
      --with-fetch-l1 --fetch-l1-sets=256 --fetch-l1-ways=2 --fetch-l1-refill-count=2 \
      --fetch-l1-hardware-prefetch=nl --fetch-axi4 \
      --with-lsu-l1 --lsu-l1-sets=512 --lsu-l1-ways=2 \
      --lsu-l1-refill-count=2 --lsu-l1-writeback-count=2 \
      --lsu-l1-store-buffer-slots=2 --lsu-l1-store-buffer-ops=32 \
      --lsu-l1-axi4 \
      --lsu-software-prefetch --lsu-hardware-prefetch=nl \
      --with-btb --btb-sets=256 --relaxed-btb --relaxed-btb-hit \
      --with-gshare --gshare-bytes=1024 --with-ras \
      --allow-bypass-from=0 \
      --fpu-ignore-subnormal \
      --fpu-wb-at=1 \
      --fpu-add-preshift-stage=1 --fpu-add-shifter-stage=2 \
      --fpu-add-math-stage=3 --fpu-add-norm-stage=4 --fpu-add-pack-at=5 \
      --relaxed-src --relaxed-branch --relaxed-div \
      --reset-vector=0 \
      --region base=0,size=8000,main=0,exe=1 \
      --region base=10000000,size=4000000,main=1,exe=1 \
      --region base=20000000,size=10000000,main=0,exe=0 \
      --region base=30000000,size=1000000,main=0,exe=0 \
      --region base=40000000,size=40000000,main=0,exe=0"

OUTPUT="$VEXII_DIR/VexiiRiscv.v"
if [ ! -f "$OUTPUT" ]; then
    echo "ERROR: VexiiRiscv.v not found after generation"
    exit 1
fi
echo ""
echo "Done! Generated $OUTPUT"
echo "Top module: VexiiRiscv"
echo "Masters   : FetchL1Axi4Plugin_logic_axi_* (I\$), LsuL1Axi4Plugin_logic_axi_* (D\$)"
