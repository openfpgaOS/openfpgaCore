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
        INCLUDE_PARAM_SPAN_Q29 INCLUDE_4PLAYER
