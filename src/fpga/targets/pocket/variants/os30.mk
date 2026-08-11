#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------
# Pocket variant: os30 — Quake2 / SM64 (3D, pure triangles).
#
# Hardware vert-tri plane derivation (0x4A/0x4B), truecolor RGB565, the
# texel*C+D combiner (HILITE/specular,
# e.g. the Mario head), HW mixer.  INCLUDE_Z_BURST forces GPU_Z_READ_WINDOW=4
# (SM64 z-tests every fragment).  VEXII_CPU_OS30 selects the os30 CPU netlist
# behavior in RTL that keys on it.  Ships as os30.rbf_r.
#
# Pays for the triangle path by cutting what Quake2 never uses — NOT listed
# (= off, additive): PALETTE (truecolor-only), PARAM_TRI/PARAM_TRI_RECS,
# COMPACT_SPAN/COLUMN_LIST, ANALOGIZER, TRANSLUC, PARALLEL_DIVS, LINK,
# 4PLAYER.  The XFORM transform front-end is back IN as of 2026-08-04 (see
# the INCLUDE_XFORM note below) — this header previously listed it among
# the cuts.
#
# TEX_MEM (CRAM1 fast textures) is back IN (2026-07 perf review): every
# texture fill otherwise contends with z reads + scanout + CPU on the one
# SDRAM and is hard-masked behind DMA/blend M0 ownership — CRAM1 is a
# private 100 MHz bus whose fills overlap all of that (~25 cy/line vs
# 50-100).  The lost-pulse race that killed it on HW (adapter/controller
# ST_IDLE-only pulse sampling — the wrong-colormap signature) is fixed by
# the controller's pending-command capture; tb_cram1_tex_chain now proves
# mid-burst interleave lossless.  Costs ~300-500 ALM, 0 M10K; needs an
# A/B fit + HW re-validation of the colormap scenario.  This is bit-identical to the
# prior EXCLUDE_* form.  Quake1 and the 2.5D span-group titles stay on os25
# (caps bits 21/23 clear here; the SDK emitters self-gate).  See
# the cut list below.
#
# NOTE: combiner is KEPT (INCLUDE_COMBINE) — gating it freed ~884 ALM but
# gave NO WNS gain (wall is the CPU decode→FPU cone, not GPU congestion) AND
# darkened the Mario head (its brightness is the combiner's additive D term).
# The OF_HW_GPU_COMBINE caps bit + app g_combine gate stay, so it can be
# gated cleanly in a FUTURE ALM-starved build.  See docs/MODULES.md.
#
# Firmware is ONE caps-driven os.bin for os25 AND os30 — the bitstream sets
# HW_FEATURES and the os.bin selects features at boot.  os30 ships the HW
# mixer (INCLUDE_HW_MIXER); there is no per-variant firmware flag.
#
# The variant's CPU config (reduced-accuracy FMA) lives in
# src/fpga/vendor/vexriscv/configs/os30.cfg; its committed fitter seed in
# seeds/os30.seed.

# INCLUDE_CLK90: os30 runs the os20-proven 90 MHz clock.
# HW reported glitches on the 100 MHz wave-2 build (WNS -1.385 / TNS -609,
# spread across hundreds of CPU decode->regfile/predictor-M10K endpoints —
# a plateau no single-cone fix lifts; knob survey receipts in memory).
# The runtime CLK_FREQ_HZ sysreg (0xD4) means the shared os.bin adapts;
# cost ~4% CPU/SDRAM. Remove this line to return to 100 MHz.
# INCLUDE_CB_WINDOW2 (2026-07-26): the truecolor-blend dst read window at
# 2 words instead of 4.  A/B/C fits at the stored seed: W=4 17,899 ALM
# (97%) WNS -1.513; W=2 17,098 (93%) -1.120; W=1 (window OFF) 17,861 (97%)
# -2.054 — i.e. the window's true cost is ~40 ALM and the ~800-ALM swings
# are synthesis/packing chaos at the >95% cliff; W=2 lands the leanest
# netlist AND keeps ~most of the blend speedup (one barrier+burst per 4
# pixels vs per pixel).  Byte-identical behavior at every size
# (gpu-acceptance-all covers W=2 in the os30-exact config, W=4 elsewhere).
# INCLUDE_XFORM + EXCLUDE_GPU_LIGHT + EXCLUDE_CLIP_TRI (2026-08-04): the GPU
# vertex cache (0x53 LOAD_VERTS / 0x54 DRAW_INDEXED_TRI, transform-once/
# draw-many) plus the new 0x56 LOAD_VERT_CLIP (clip-space cache load for the
# CPU-geometry path).  Made affordable by moving vc_mem MLAB->M10K (9 LABs
# back) — measured A/B: full XFORM = 1,857/1,848 LABs (DOES NOT FIT); this
# config fitted at 1,846/1,848 when it landed, but os30 NO LONGER FITS at all
# (1864 LABs needed, 1848 available — Error 170012).  The lighting cone (0x55/0x57)
# and 0x4F draw-clip-tri are excluded; caps bits are honest about it:
# bit 26 = matrix front-end (0x50/52/53/54), bit 29 = 0x56 clip load,
# bit 30 = lighting (clear here).  The MAC must stay: 0x53 transforms
# through the sticky 0x50 matrix with NO bypass — a MAC-less 0x53 collapses
# every vert to the viewport center (2026-08-04 review, CONFIRMED).
# INCLUDE_VI_FILTER (2026-07-26): N64 VI-style output softening — 2-tap
# horizontal average on the LCD stream, direct-color modes only (terminal
# stays crisp).  Chosen over per-texel bilinear as the fix for the sampling
# artifacts (letter lines / water moiré): the port samples nearest-only,
# the real N64 hid the same aliasing behind its VI resampler.  ~30 ALMs in
# the slack-rich clk_analog domain.
DEFS := INCLUDE_VERT_TRI INCLUDE_DIRECT_COLOR INCLUDE_COMBINE INCLUDE_Z_BURST \
        VEXII_CPU_OS30 INCLUDE_HW_MIXER INCLUDE_TEX_MEM INCLUDE_CLK90 \
        INCLUDE_CB_WINDOW2 INCLUDE_VI_FILTER \
        INCLUDE_XFORM EXCLUDE_GPU_LIGHT EXCLUDE_CLIP_TRI
