#!/bin/bash
# Generate VexiiRiscv for openfpgaOS
#
# RV32IMAFCB + Zicbom (cache block management)
#
# Current VexiiRiscv_Full.v config:
#   I-cache: 64KB (512 sets x 2 ways x 64B line, 2-way set-associative)
#   D-cache: 64KB (256 sets x 4 ways x 64B line, write-back, 4-way set-associative)
#   Branch: BTB + GShare + RAS
#   FPU: single-precision (F extension)
#   Cache mgmt: Zicbom enabled in hardware
#   Store buffer: 4 slots, 32 ops
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

sbt "runMain vexiiriscv.soc.openfpgaos.GenOpenFpgaVexii \
      --xlen=32 \
      --with-rvm --with-rva --with-rvf --with-rvc \
      --with-fetch-l1 --fetch-l1-sets=512 --fetch-l1-ways=2 --fetch-l1-refill-count=2 \
      --fetch-l1-hardware-prefetch=nl \
      --with-lsu-l1 --lsu-l1-sets=512 --lsu-l1-ways=2 \
      --lsu-l1-refill-count=2 --lsu-l1-writeback-count=2 \
      --lsu-l1-store-buffer-slots=4 --lsu-l1-store-buffer-ops=32 \
      --lsu-software-prefetch --lsu-hardware-prefetch rpt \
      --with-rvZcbm \
      --with-btb --btb-sets=256 --relaxed-btb --relaxed-btb-hit \
      --with-gshare --with-ras \
      --regfile-async --allow-bypass-from=2 \
      --relaxed-src --relaxed-branch --relaxed-shift \
      --reset-vector=0 \
      --region base=0,size=8000,main=0,exe=1 \
      --region base=10000000,size=4000000,main=1,exe=1 \
      --region base=20000000,size=10000000,main=0,exe=0 \
      --region base=30000000,size=1000000,main=0,exe=0 \
      --region base=40000000,size=40000000,main=0,exe=0"

# Memory regions (v2: CRAM1 retired, CRAM0 on bridge clock via CDC):
#   0x00000000  32KB   BRAM region       (main=0 — NON-SPECULATIVE, executable)
#   0x10000000  64MB   SDRAM             (main=1 cacheable, executable) — OS, apps, heap, stack, FB, audio ring, samples
#   0x20000000 256MB   VRAM/reserved     (uncached, non-exec)
#   0x30000000  16MB   CRAM0             (uncached, non-exec) — bridge staging only; CPU memcpy's via CDC on save/load
#   0x40000000   1GB   IO + SDRAM_UC     (uncached, non-exec) — covers 0x50000000 SDRAM uncached alias
#
# BRAM main=0 is deliberate (matches PocketQuake).  Marking BRAM as
# main=1 enables speculative prefetch across the BRAM end at 0x8000,
# and VexiiRiscv surfaces speculative-access faults as architectural
# load access faults with mtval=0x8000 against the currently-committing
# instruction.  Non-speculative semantics disallow the prefetch entirely.
#
# Retired regions:
#   0x31000000 / 0x39000000 — CRAM1 cached / uncached aliases (chip removed from bitstream)
#   0x38000000              — CRAM0 uncached alias (CRAM0 is uncached at 0x30000000 now, no alias needed)
#   0x3A000000              — SRAM    (GPU-private, not on AXI)

OUTPUT="$VEXII_DIR/OpenFpgaVexii.v"
if [ ! -f "$OUTPUT" ]; then
    echo "ERROR: OpenFpgaVexii.v not found after generation"
    exit 1
fi
echo ""
echo "Done! Generated $OUTPUT"
echo "Extensions: RV32IMAFCB + Zicbom"
