#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------
# Pocket variant: os25 (the default) — 2.5D games.
#
# Doom/Duke3D/ScummVM/Wolf + Quake1 SW alias: full feature set, full HW audio
# mixer, no hardware vertex-triangle.  Ships as os25.rbf_r.
#
# DEFS is the variant's ADDITIVE module list (docs/MODULES.md): a feature is
# PRESENT iff its INCLUDE_* macro is listed; everything else is off (zero
# cost).  core_top wires every gpu_core/periph param off these macros, so the
# gpu_core param defaults never apply here.  Dependency pull-ups
# (combine/xform ⇒ truecolor) live in core_top.  Each entry is forwarded to
# quartus_map as --verilog_macro=<NAME> via the generated per-job qsf.
#
# The variant's CPU config lives in
# src/fpga/vendor/vexriscv/configs/os25.cfg; its committed fitter seed in
# seeds/os25.seed.
#
# OS25 keeps: analogizer, full HW mixer, translucency, the palettized/
# colormap lane, column lists, compact span groups.  NO triangle path (edge
# walker folds away), NO truecolor/combine/xform, NO fast tex.  (Dropping
# INCLUDE_XFORM also sheds the previously-dead 0x50/0x51/0x4F cones os25
# used to synthesize via defaults.)  INCLUDE_4PLAYER wires APF dock slots
# 2/3 (cont3/cont4) through to the core so the firmware's keyboard/mouse
# decode (poll_hid_slots) sees real data instead of the tied-0 default;
# without it those slots are hardcoded 0 (core_top.v).

DEFS := INCLUDE_ANALOGIZER INCLUDE_HW_MIXER INCLUDE_TRANSLUC \
        INCLUDE_PALETTE INCLUDE_COLUMN_LIST INCLUDE_COMPACT_SPAN \
        INCLUDE_PARAM_SPAN_Q29 INCLUDE_4PLAYER INCLUDE_BANK_ROW_TRACK \
        INCLUDE_CPU_RING INCLUDE_GPU_WRITE_BURSTS EXCLUDE_GPU_COMMAND_DMA \
        INCLUDE_SPAN_DEDICATED_MULT

# Separate span products from the shared perspective DSP operand mux.
# Bit-exact and cycle-identical in Quake/Doom replays; paired seeds 15/20
# improve setup slack without additional M10K use. The fresh 2026-10-30
# seed 20 fit estimates 96.87 MHz: setup -0.323 ns, worst hold +0.078 ns;
# 15,490 ALMs and 296 M10Ks. 100 MHz still does not close timing.
# Fetch/FPU, clamp staging and audio/RAM trials did not improve this fit.

# CPU uploads reuse the existing GPU command ring and avoid SDRAM DMA reads.
# Gather masked framebuffer writes into SDRAM bursts; no triangle/depth
# combining logic or additional texture storage is required.
# Command-DMA removal requires the SDK's automatic CPU-ring selection.

# CPU, textures, mixer samples and scanout can occupy different SDRAM banks.
# Preserve each bank's open row across master switches to avoid unnecessary
# PRECHARGE/ACTIVATE commands. See docs/POCKET_PERFORMANCE.md for measurements
# and the separate 90/100 MHz timing results.
