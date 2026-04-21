#!/bin/bash
# Generate VexiiRiscv for openfpgaOS using the stock vexiiriscv.Generate
# entry point (no custom Scala wrapper).  FetchL1 and LsuL1 are exposed
# directly as AXI4 masters; all region routing is handled by the fabric
# downstream (cpu_system.v).  Matches PocketQuake's proven-stable
# topology, with our current cache sizing and Zicbom retained.
#
# Config highlights:
#   I-cache: 64 KB (512 sets × 2 ways × 64 B line, NL prefetch)
#   D-cache: 64 KB (512 sets × 2 ways × 64 B line, RPT prefetch)
#   Branch : BTB 512 sets + GShare + RAS
#   FPU    : single-precision (F extension)
#   Zicbom : cache block management (cbo.clean/flush/inval)
#   Bypass : --allow-bypass-from=0 (conservative)
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

echo "Generating VexiiRiscv (stock Generate, Quake-topology, our cache sizing)..."
cd "$VEXII_DIR"

sbt "Test/runMain vexiiriscv.Generate \
      --xlen=32 \
      --with-rvm --with-rva --with-rvf --with-rvc \
      --with-rvZcbm \
      --with-fetch-l1 --fetch-l1-sets=512 --fetch-l1-ways=2 --fetch-l1-refill-count=2 \
      --fetch-l1-hardware-prefetch=nl --fetch-axi4 \
      --with-lsu-l1 --lsu-l1-sets=512 --lsu-l1-ways=2 \
      --lsu-l1-refill-count=2 --lsu-l1-writeback-count=2 \
      --lsu-l1-store-buffer-slots=2 --lsu-l1-store-buffer-ops=32 \
      --lsu-l1-axi4 \
      --lsu-software-prefetch --lsu-hardware-prefetch rpt \
      --with-btb --btb-sets=512 --relaxed-btb --relaxed-btb-hit \
      --with-gshare --with-ras \
      --allow-bypass-from=0 \
      --relaxed-src \
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
