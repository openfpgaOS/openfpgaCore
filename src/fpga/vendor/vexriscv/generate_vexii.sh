#!/bin/bash
# Generate VexiiRiscv for openfpgaOS
#
# RV32IMAFCB + Zicbom (cache block management)
#   I-cache: 32KB (512 sets x 1 way x 64B line)
#   D-cache: 128KB (1024 sets x 2 ways x 64B line, write-back)
#   Branch: BTB + GShare + RAS
#   FPU: single-precision (F extension)
#   Cache mgmt: CBO.CLEAN, CBO.INVAL, CBO.FLUSH (Zicbom)
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
      --region base=0,size=10000,main=0,exe=1 \
      --region base=10000000,size=4000000,main=1,exe=1 \
      --region base=20000000,size=10000000,main=0,exe=0 \
      --region base=30000000,size=8000000,main=1,exe=1 \
      --region base=38000000,size=8000000,main=0,exe=0 \
      --region base=40000000,size=40000000,main=0,exe=0"

# Memory regions:
#   0x00000000  64KB  BRAM           (uncached, executable)
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
    echo "D-cache: 128KB (1024x2x64B, write-back)"
    echo "I-cache: 32KB (512x1x64B)"
    echo "Cache mgmt: cbo.clean, cbo.inval, cbo.flush"
else
    echo "ERROR: VexiiRiscv.v not found after generation"
    exit 1
fi
