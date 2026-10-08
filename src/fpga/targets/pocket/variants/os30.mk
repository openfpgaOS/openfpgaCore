#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------
# Pocket variant: os30 — Quake2 / SM64 (3D, pure triangles).
#
# Hardware vert-tri plane derivation (0x4A/0x4B), truecolor RGB565, the
# texel*C+D combiner (HILITE/specular,
# e.g. the Mario head), HW mixer. INCLUDE_Z_WINDOW8 selects an eight-word
# depth window (SM64 z-tests every fragment). VEXII_CPU_OS30 selects the os30 CPU netlist
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

# CPU/SDRAM runs at 100 MHz without INCLUDE_CLK90 or INCLUDE_CLK96.
# This clock setting is experimental and has not passed timing.
# The shared os.bin adapts through the runtime CLK_FREQ_HZ sysreg (0xD4).
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
# config fitted at 1,846/1,848 when it landed. Later additions exceeded
# capacity; CPU command uploads and explicit disabled-feature guards restore
# space without removing rendering capabilities. The lighting cone (0x55/0x57)
# and 0x4F draw-clip-tri are excluded; caps bits are honest about it:
# bit 26 = matrix front-end (0x50/52/53/54), bit 29 = 0x56 clip load,
# bit 30 = lighting (clear here).
# EXCLUDE_GPU_XFORM_MAC (2026-09-23): neither shipping os30 title uses the
# GPU matrix — SM64 and Quake2 gl1 both transform on the CPU and load
# clip-space verts through 0x56/0x54 (bit 29).  The MAC-less decode drains
# 0x51/0x52/0x53 as no-ops (so a MAC-less 0x53 can no longer collapse verts)
# and bit 26 is cleared to match.  Fit A/B at seed 31: -353 FFs, -1 M10K,
# -4 LABs.
# INCLUDE_VI_FILTER (2026-07-26): N64 VI-style output softening — 2-tap
# horizontal average on the LCD stream, direct-color modes only (terminal
# stays crisp).  Chosen over per-texel bilinear as the fix for the sampling
# artifacts (letter lines / water moiré): the port samples nearest-only,
# the real N64 hid the same aliasing behind its VI resampler.  ~30 ALMs in
# the slack-rich clk_analog domain.
DEFS := INCLUDE_VERT_TRI INCLUDE_DIRECT_COLOR INCLUDE_COMBINE INCLUDE_Z_BURST \
        VEXII_CPU_OS30 INCLUDE_HW_MIXER INCLUDE_TEX_MEM \
        INCLUDE_CB_WINDOW2 INCLUDE_VI_FILTER \
        INCLUDE_XFORM EXCLUDE_GPU_LIGHT EXCLUDE_CLIP_TRI EXCLUDE_GPU_XFORM_MAC \
        INCLUDE_CPU_RING EXCLUDE_GPU_COMMAND_DMA \
        EXCLUDE_GPU_PARAM_SPAN_LIST \
        INCLUDE_XFORM_VERTEX_RAM INCLUDE_XFORM_RATIO_PIPE \
        INCLUDE_GPU_CLAMP_PIPE INCLUDE_Z_COMPARE_PARALLEL \
        INCLUDE_CB_SPLIT_MULT INCLUDE_PSS_SPECULATIVE_STEP \
        INCLUDE_SPAN_DEDICATED_MULT INCLUDE_TEX_QUEUE_RAM INCLUDE_TEX_QUEUE_MLAB \
        INCLUDE_EW_STEP_PREDECODE INCLUDE_EARLY_Z_CAPTURE \
        EXCLUDE_SYSREG_DIAG_READBACK INCLUDE_Z_WINDOW8 \
        INCLUDE_GPU_RENDER_CACHE \
        INCLUDE_DERIVE_PARALLEL_SCALE INCLUDE_Z_EARLY_RESTART \
        INCLUDE_GPU_CLAMP_FLAGS_PIPE INCLUDE_GPU_WQ16 \
        INCLUDE_Z_WINDOW_PREDECODE

# A second eight-word posted burst can queue while SDRAM commits the first.
# B responses and slot retirement still wait for the physical write commit.
# Clamp flags and depth address predecode shorten combinational paths
# without adding rendering cycles. The depth
# data, tags and accumulator forwarding stay live through pipeline stalls.
# See docs/measurements/os30-memory-opt-20261002 for saved-seed fits and
# rendering, queue-integrity and real-mixer contention checks.
# Saved seed 2: 16,993 ALMs, 306 M10Ks, worst setup -0.902 ns;
# minimum main-clock Fmax 91.73 MHz. 100 MHz timing remains open.

# Parallel reciprocal scaling uses both existing triangle DSP ports, removing
# two wait/capture states per derivative without changing the arithmetic.
# Depth early restart evaluates the requested word while the rest of its read
# window fills; bus ownership and write invalidations remain live to the end.

# SM64 and the current Quake2 gl1 renderer submit triangles, not long-form
# 0x48 span lists. Drop only that command's header/record frontend and clear
# capability bit 15. The shared span producer, perspective/depth pipeline,
# RGB combiner and alpha blending remain available to triangle commands.
# Saved-seed A/B (seed 2, 2026-10-02): 17,128 -> 16,874 ALMs,
# 306 -> 305 M10Ks; worst setup -1.004 -> -0.887 ns at 100 MHz.
# 38 retained workloads have identical pixels/cycles across three memory
# schedules. The clock remains experimental; the full sweep is deferred.

# Experimental 100 MHz candidate measured with seed 31. These options retain
# rendering features and arithmetic while moving work off critical paths.

# CPU uploads reuse the command ring and avoid its SDRAM DMA reader.
# Applications must use the SDK with automatic CPU-ring selection; older
# DMA-only binaries need rebuilding. Texture, framebuffer and mixer transfers
# retain their existing hardware paths. See docs/OS30_REPAIR_20260915.md.

# Coordinate block RAM uses the matrix read-address cycle; clip-fed vertices
# need one address cycle. Projection ratios reuse the expired translation
# register to break DSP feedback, adding two cycles per transformed vertex.

# EXCLUDE_SYSREG_DIAG_READBACK (2026-09-23): sysreg words no firmware reads
# (data-slot staging 8-13, mouse speed, Analogizer offsets, timer period/
# counter, SAVE_DT_WORD, FB geometry, the SDRAM/scanout diagnostic counters)
# read 0, so their sources and CDC syncs prune.  The DS_BRIDGE_WCNT CRAM0
# overrun sticky (bit 31) stays; video.c's diagnostic watchdog is inert at 0.

# INCLUDE_Z_WINDOW8 (2026-09-23): depth-line fills fetch 8 words instead of 4.
# INCLUDE_GPU_SELECTIVE_READ_WAIT (window fills wait only for writes to the
# same 64-byte line instead of a full drain) was adopted with it and dropped
# again once the render cache landed; see below.  Measured together in the
# live SM64 model (tools/experiments/sm64_srw_20260923): castle 21.18 -> 22.50 FPS, castle
# under CPU memory traffic 12.82 -> 15.41, Bowser 18.46 -> 18.88, every frame
# byte-identical.  Cost vs the 90 MHz reference fit: +434 ALUTs.  A 16-word
# window (+cb4) is faster under contention but costs ~+300 more ALUTs.

# INCLUDE_GPU_RENDER_CACHE (2026-09-24): 8 KiB, 2-way, 64-byte-line write-back
# cache for all GPU SDRAM traffic (common/gpu_color_depth_cache.sv, wired in
# core_top.v); replaces the GPU write combiner.  Live SM64 model on top of the
# selective waits, 8-word window and SM64's clear removal: castle +2%, Bowser
# +9%, castle under CPU memory traffic +17% FPS, frames byte-identical
# (tools/experiments/sm64_cache_20260924).  Its 10 M10Ks come from the write
# combiner (2) and the texture request/result/metadata queues (5), which keep
# the metadata-FIFO form but live in MLAB (INCLUDE_TEX_QUEUE_MLAB).
#
# Timing round with the cache (2026-09-25, 40-seed sweeps at 100 MHz): the
# cache fills the device, so the selective read waits came out: -361 ALUTs,
# median WNS -1.708 -> -1.463, median TNS -817 -> -479, for castle -1.3% FPS
# (Bowser unchanged).  The write queue's skid swap is gated off under the
# cache (no SM64 timestamp changes; its link compare fed the pixel-pipeline
# stall), and the CPU registers its D$ bank writes (LSU_WRITE_REG in
# configs/os30.cfg).  Burst linking stays: without it SM64 loses 10-26%.
#
# Posted writebacks (2026-09-29): the render cache refills while a dirty
# victim's B is still pending (a fill of that line waits).  Live model of the
# current SM64 binary (tools/experiments/sm64_bottleneck_20260929): castle
# 24.0 -> 27.3 FPS, castle under CPU traffic 18.7 -> 23.3, Bowser 22.4 -> 24.4,
# matching frames byte-identical.  40-seed sweep: best WNS -0.791 -> -0.693,
# median -1.388 -> -1.295; +16 ALUT, +112 FF.

# Opt-in span-start depth prefetch. Reuses the depth window and idle P3
# address register; no rendering features, precision or clocks change.
# 26 SM64 captures x 3 traffic schedules match the baseline byte-for-byte:
# aggregate GPU throughput +8.38% quiet, +7.85% scanout/audio, +4.82% with
# added CPU reads. These are replay measurements, not whole-game FPS.
# Seed 2: 17,023 ALMs (+30), 306 M10Ks (unchanged), setup -1.190 ns;
# minimum main-clock Fmax 89.37 MHz (baseline 91.73). Timing regresses,
# so keep this disabled in normal builds pending further timing work.
# make build VARIANT=os30 OS30_DEPTH_PREFETCH=1 JOB=os30-depth-prefetch
# See tools/experiments/os30_depth_prefetch_20261002 and the corresponding
# docs/measurements campaign for exact tests and the experimental bitstream.
ifeq ($(OS30_DEPTH_PREFETCH),1)
DEFS += INCLUDE_Z_PREFETCH
endif

# Experimental SM64 geometry frontend. Advertises its own GPU_STATUS bit 10;
# legacy matrix commands/caps stay disabled. The app retains CPU lighting and
# clipping fallbacks. Build only with the matching experimental SM64 renderer.
# make build VARIANT=os30 OS30_GEOMETRY=1 JOB=os30-geometry
ifeq ($(OS30_GEOMETRY),1)
DEFS := $(filter-out EXCLUDE_GPU_XFORM_MAC,$(DEFS)) INCLUDE_GPU_SM64_GEOMETRY
endif

# Experimental independent geometry command worker. Replaces the serialized
# matrix/projection path and overlaps preparation with the pixel pipeline.
# Keeps the normal 128 KiB CPU data cache; its bounded 2 KiB output FIFO can
# pause the rasterizer's command loader inside long payloads. Status bit 11
# advertises worker culling and 1..32-vertex 0x59 batches. The existing SM64
# renderer works unchanged; CPU-culling removal remains a private experiment.
# No full seed sweep or device performance qualification has been completed.
# make build VARIANT=os30 OS30_GEOMETRY_WORKER=1 OS30_DEPTH_PREFETCH=1 JOB=os30-worker
ifeq ($(OS30_GEOMETRY_WORKER),1)
DEFS := $(filter-out EXCLUDE_GPU_XFORM_MAC INCLUDE_GPU_SM64_GEOMETRY,$(DEFS)) \
        EXCLUDE_GPU_XFORM_MAC INCLUDE_GPU_SM64_GEOMETRY INCLUDE_GPU_GEOMETRY_WORKER
endif

# Opt-in experiment, never selected by a normal production build or sweep.
# Simulation passes; SDRAM physical timing is NOT closed. Not for deployment.
# make build VARIANT=os30 OS30_SDRAM166=1 JOB=os30-sdram166
ifeq ($(OS30_SDRAM166),1)
DEFS += INCLUDE_OS30_SDRAM166
endif

# Exact compact-v7 same-triangle span lookahead (private integration candidate).
DEFS += INCLUDE_SPAN_LOOKAHEAD
