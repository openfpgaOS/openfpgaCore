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
        INCLUDE_SPAN_DEDICATED_MULT INCLUDE_SDRAM_WRITE_PIPE \
        INCLUDE_SDRAM_MASKED_BURSTS INCLUDE_SDRAM_WHOLE_BURSTS \
        INCLUDE_GPU_CMAP_STORE INCLUDE_GPU_TEX_LINE64 \
        INCLUDE_GPU_SPAN_EARLY_HANDOFF INCLUDE_GPU_SPAN_PREP

# INCLUDE_GPU_SPAN_EARLY_HANDOFF: opaque depth-free parametric (long-form 0x48)
# records take the compact-lane early record handoff, so the next record's
# setup and perspective anchor pass overlap the previous record's pipe tail.
# Pixel output unchanged; see build/gpu-candidates/c_overlap.
# INCLUDE_GPU_SPAN_PREP: the next parametric record's setup products are
# computed during the current record's pixels (shadow walk on the dedicated
# span multipliers); see build/gpu-candidates/c_overlap_prep.

# Separate span products from the shared perspective DSP operand mux.
# Bit-exact and cycle-identical in Quake/Doom replays; paired seeds 15/20
# improve setup slack without additional M10K use. The fresh 2026-10-30
# seed 20 fit estimates 96.87 MHz: setup -0.323 ns, worst hold +0.078 ns;
# 15,490 ALMs and 296 M10Ks. 100 MHz still does not close timing.
# Fetch/FPU, clamp staging and audio/RAM trials did not improve this fit.
# 2026-11-17, /4 mixer mix-down and scanout sub-bursts: seed 25 estimates
# 96.16 MHz, setup -0.399 ns, worst hold +0.102 ns; 15,525 ALMs, 296 M10Ks.

# CPU uploads reuse the existing GPU command ring and avoid SDRAM DMA reads.
# Gather masked framebuffer writes into SDRAM bursts; no triangle/depth
# combining logic or additional texture storage is required.
# Command-DMA removal requires the SDK's automatic CPU-ring selection.

# INCLUDE_GPU_CMAP_STORE: palookup (colormap) reads use their own 4 KB
# direct-mapped store in gpu_tex_cache (+5 M10K) instead of aliasing texture
# lines in the shared 16 KB cache.  Software invisible, byte-identical.
# Its tags are palookup-relative (6-bit hit compare); a GPU_PALOOKUP_BASE
# write therefore also flushes the texture cache.
# INCLUDE_GPU_TEX_LINE64: the 16 KB texture cache uses 256 x 64-byte lines
# (16-beat fills) instead of 1024 x 16-byte lines; tag RAM 1 M10K instead
# of 2.  Software invisible, byte-identical.

# CPU, textures, mixer samples and scanout can occupy different SDRAM banks.
# Preserve each bank's open row across master switches to avoid unnecessary
# PRECHARGE/ACTIVATE commands. See docs/POCKET_PERFORMANCE.md for measurements
# and the separate 90/100 MHz timing results.

# GPU framebuffer writes no longer hold the SDRAM slave until io_sdram's
# write-done: the slave releases a GPU queue write once io_sdram has accepted
# it (io_sdram latches write data at dispatch), so the next request already
# waits in io_sdram's queue.  The GPU's B still waits for the commit.
# Consecutive GPU framebuffer words that carry partial byte enables join the
# arbiter's native SDRAM burst like full words (DQM applies the byte enables),
# and a burst is drained once its final beat is queued instead of 2 + 6.
