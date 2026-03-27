#!/bin/bash
# Generate VexiiRiscv for openfpgaOS
#
# RV32IMAFCB + Zicbom (cache block management)
#
# NOTE: The parameters below request a larger cache (1024x2 D$, 512x1 I$)
# but the CURRENT generated VexiiRiscv_Full.v has:
#   I-cache: 8KB  (128 sets x 1 way x 64B line)
#   D-cache: 32KB (512 sets x 1 way x 64B line, write-back, direct-mapped)
#   Branch: BTB + GShare + RAS
#   FPU: single-precision (F extension)
#   Cache mgmt: Zicbom enabled in hardware (not used by firmware)
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

echo "Generating VexiiRiscv with Zicbom..."
cd "$VEXII_DIR"

sbt "runMain vexiiriscv.Generate \
      --xlen=32 \
      --with-rvm --with-rva --with-rvf --with-rvc \
      --with-rvZcbm \
      --with-fetch-l1 --fetch-l1-sets=512 --fetch-l1-ways=1 --fetch-l1-refill-count=2 \
      --fetch-l1-hardware-prefetch=nl --fetch-axi4 \
      --with-lsu-l1 --lsu-l1-sets=1024 --lsu-l1-ways=2 \
      --lsu-l1-refill-count=2 --lsu-l1-writeback-count=2 \
      --lsu-l1-store-buffer-slots=2 --lsu-l1-store-buffer-ops=32 \
      --lsu-l1-axi4 \
      --with-btb --btb-sets=512 --relaxed-btb --relaxed-btb-hit \
      --with-gshare --with-ras \
      --regfile-async --allow-bypass-from=0 \
      --relaxed-src \
      --reset-vector=0 \
      --region base=0,size=30000,main=0,exe=1 \
      --region base=10000000,size=4000000,main=1,exe=1 \
      --region base=20000000,size=10000000,main=0,exe=0 \
      --region base=30000000,size=8000000,main=1,exe=1 \
      --region base=38000000,size=8000000,main=0,exe=0 \
      --region base=40000000,size=40000000,main=0,exe=0"

# Memory regions:
#   0x00000000 192KB  BRAM           (uncached, executable)
#   0x10000000  64MB  SDRAM          (cached, executable)
#   0x20000000 256MB  VRAM/reserved  (uncached, non-exec)
#   0x30000000 128MB  CRAM cached    (cached, executable)
#   0x38000000 128MB  CRAM uncached  (uncached, non-exec)
#   0x40000000   1GB  IO + SDRAM_UC  (uncached, non-exec) — covers 0x50000000 SDRAM uncached alias

OUTPUT="$VEXII_DIR/VexiiRiscv.v"
if [ -f "$OUTPUT" ]; then
    if [ -f "$SCRIPT_DIR/VexiiRiscv_Full.v" ]; then
        cp "$SCRIPT_DIR/VexiiRiscv_Full.v" "$SCRIPT_DIR/VexiiRiscv_Full.v.bak"
        echo "Backed up old VexiiRiscv_Full.v"
    fi
    cp "$OUTPUT" "$SCRIPT_DIR/VexiiRiscv_Full.v"
    echo ""
    echo "Done! Copied to VexiiRiscv_Full.v"
    echo "Extensions: RV32IMAFCB + Zicbom"
    echo "D-cache: check generated Verilog for actual size"
    echo "I-cache: check generated Verilog for actual size"
    echo "Cache mgmt: Zicbom enabled in hardware"
else
    echo "ERROR: VexiiRiscv.v not found after generation"
    exit 1
fi
