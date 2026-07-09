#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------
# Pocket variant: os20 — 2D games (Diablo/DevilutionX, ScummVM, point-and-
# click / framebuffer titles) with a DUAL-ISSUE CPU.
#
# These games render on the CPU and present through of_video (8-bit indexed
# framebuffer + hardware palette + flip) plus the BASE GPU rasterizer for
# fills — none of the 2.5D span machinery.  Cutting the three span input
# front-ends that Doom/Duke/Wolf need is what buys the ALM budget for the
# dual-issue VexiiRiscv (configs/os20.cfg — the same --decoders=2 --lanes=2
# config MiSTer ships, first time on the Pocket's A4/C8):
#   NOT listed (= off, additive):
#     INCLUDE_COMPACT_SPAN   0x48 compact span groups   (caps bit 23 clears)
#     INCLUDE_COLUMN_LIST    0x4C column lists          (caps bit 21 clears)
#     INCLUDE_PARAM_SPAN_Q29 Q29 perspective span cone  (caps bit 18 clears)
#   plus everything os25 already omits (all triangle paths, truecolor/
#   combine/xform, fast tex).  The BASE rasterizer is always present, and
#   the SDK emitters self-gate on the cleared caps bits — ONE caps-driven
#   os.bin runs this bitstream unchanged.
#
# Kept (full os25 parity otherwise — per 2026-07-09 decision):
#   INCLUDE_PALETTE    8-bit colormap lane (CLUT8 titles)
#   INCLUDE_TRANSLUC   translucency LUT + FBSS_BLEND (UI fades)
#   INCLUDE_HW_MIXER   HW audio mixer (ScummVM leans on SMP/MIDI voices)
#   INCLUDE_ANALOGIZER analog video + SNAC
#   INCLUDE_4PLAYER    dock slots 2/3 — feeds the firmware keyboard/MOUSE
#                      decode; point-and-click games need it most of all.
#
# Ships as os20.rbf_r.  CPU config: ../../vendor/vexriscv/configs/os20.cfg;
# committed fitter seed: seeds/os20.seed.  Timing policy: ship the best
# sweep seed (same negative-WNS-at-slow-corner policy as os25/os30);
# dual-issue on the C8 grade is unproven — HW-validate before adopting.

DEFS := INCLUDE_ANALOGIZER INCLUDE_HW_MIXER INCLUDE_TRANSLUC \
        INCLUDE_PALETTE INCLUDE_4PLAYER
