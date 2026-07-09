#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------
# Pocket variant: os30 — Quake2 / SM64 (3D, pure triangles).
#
# Hardware vert-tri plane derivation (0x4A/0x4B) + records-only param-tri
# (0x4D) ungated, truecolor RGB565, the texel*C+D combiner (HILITE/specular,
# e.g. the Mario head), HW mixer.  INCLUDE_Z_BURST forces GPU_Z_READ_WINDOW=4
# (SM64 z-tests every fragment).  VEXII_CPU_OS30 selects the os30 CPU netlist
# behavior in RTL that keys on it.  Ships as os30.rbf_r.
#
# Pays for the triangle path by cutting what Quake2 never uses — NOT listed
# (= off, additive): PALETTE (truecolor-only), the XFORM transform front-end
# (CPU does geometry — sheds 0x52/0x50/0x51/0x53-57/0x4F + xf_M M10K),
# PARAM_TRI/PARAM_TRI_RECS, COMPACT_SPAN/COLUMN_LIST, TEX_MEM, ANALOGIZER,
# TRANSLUC, PARALLEL_DIVS, LINK, 4PLAYER.  This is bit-identical to the
# prior EXCLUDE_* form.  Quake1 and the 2.5D span-group titles stay on os25
# (caps bits 21/23 clear here; the SDK emitters self-gate).  See
# docs/os30-quake2-cut-plan.md.
#
# NOTE: combiner is KEPT (INCLUDE_COMBINE) — gating it freed ~884 ALM but
# gave NO WNS gain (wall is the CPU decode→FPU cone, not GPU congestion) AND
# darkened the Mario head (its brightness is the combiner's additive D term).
# The OF_HW_GPU_COMBINE caps bit + app g_combine gate stay, so it can be
# gated cleanly in a FUTURE ALM-starved build.  See docs/MODULES.md.
#
# Firmware is ONE caps-driven os.bin for os25 AND os30 — the bitstream sets
# HW_FEATURES (this variant clears OF_HW_MIXER_HW → the single os.bin
# auto-selects the CPU software mixer at boot).  No per-variant build flag.
#
# The variant's CPU config (reduced-accuracy FMA) lives in
# src/fpga/vendor/vexriscv/configs/os30.cfg; its committed fitter seed in
# seeds/os30.seed.

DEFS := INCLUDE_VERT_TRI INCLUDE_DIRECT_COLOR INCLUDE_COMBINE INCLUDE_Z_BURST \
        VEXII_CPU_OS30 INCLUDE_HW_MIXER
