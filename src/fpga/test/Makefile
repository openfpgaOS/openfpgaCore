#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------

# Verilator Test Suite for Memory Controllers
#
# `make all` (the default `make test` entry point) runs every test that
# currently passes:
#   sdram, sram, gpu, axi-periph, bridge-cmd, audio
#
# v2 memory-arch: cram1 and cram1-burst-mmio tests retired with the
# chip; cram1_controller.v / cram1_phy.sv / axi_cram1_slave.v /
# cram1_burst_mmio.v are deleted from the RTL tree.

COMMON_DIR = ../common
TARGET_DIR = ../targets/pocket

VERILATOR = verilator
VERILATOR_FLAGS = --cc --exe --build \
	-Wall -Wno-fatal -Wno-BADVLTPRAGMA \
	-CFLAGS "-std=c++17 -O2" \
	-I$(TARGET_DIR)/third_party/analogizer \
	--trace

# Trace-free flags used by the heavyweight system tb (full CPU) — VCD
# tracing is the dominant sim cost; leave it off so boot completes in
# a few seconds instead of minutes.
VERILATOR_FLAGS_NOTRACE = --cc --exe --build \
	-Wall -Wno-fatal -Wno-BADVLTPRAGMA \
	-CFLAGS "-std=c++17 -O2" \
	-I$(TARGET_DIR)/third_party/analogizer

# ============================================================
# SDRAM
# ============================================================
SDRAM_SRCS = \
	tb_sdram.v \
	sdram_model.v \
	$(COMMON_DIR)/axi_sdram_slave.v \
	io_sdram_test.v

SDRAM_SYNCH = $(TARGET_DIR)/apf/common.v
SDRAM_CPP = tb_sdram_main.cpp
SDRAM_TOP = tb_sdram
SDRAM_DIR = obj_dir
SDRAM_SERIAL_DIR = obj_dir_sdram_serial
SDRAM_NATIVE2_DIR = obj_dir_sdram_native2
SDRAM_WCONT0_DIR = obj_dir_sdram_wcont0

# Generate test variant of io_sdram with split DQ (no inout for Verilator)
io_sdram_test.v: $(TARGET_DIR)/io_sdram.v
	sed \
	  -e 's/inout   wire    \[15:0\]  phy_dq,/input   wire    [15:0]  phy_dq_in,\noutput  wire    [15:0]  phy_dq_out_port,\noutput  wire            phy_dq_oe_port,/' \
	  -e "s/assign          phy_dq = phy_dq_oe ? phy_dq_out : 16'bZZZZZZZZZZZZZZZZ;/assign phy_dq_out_port = phy_dq_out;\nassign phy_dq_oe_port = phy_dq_oe;/" \
	  -e 's/phy_dq_latched <= phy_dq;/phy_dq_latched <= phy_dq_in;/' \
	  $< > $@

# Same split-DQ generation from the MISTER io_sdram copy (kept twinned
# with pocket except the SS1 A[12:11] DQM write-mask mirror).  Used by
# the system-sdram boot repro so the SHIPPING mister controller is the
# one under test.
io_sdram_mister_test.v: ../targets/mister/io_sdram.v
	sed \
	  -e 's/inout   wire    \[15:0\]  phy_dq,/input   wire    [15:0]  phy_dq_in,\noutput  wire    [15:0]  phy_dq_out_port,\noutput  wire            phy_dq_oe_port,/' \
	  -e "s/assign          phy_dq = phy_dq_oe ? phy_dq_out : 16'bZZZZZZZZZZZZZZZZ;/assign phy_dq_out_port = phy_dq_out;\nassign phy_dq_oe_port = phy_dq_oe;/" \
	  -e 's/phy_dq_latched <= phy_dq;/phy_dq_latched <= phy_dq_in;/' \
	  $< > $@

# ============================================================
# SRAM
# ============================================================
SRAM_SRCS = \
	tb_sram.v \
	sram_chip_model.v \
	$(TARGET_DIR)/sram_controller.v

SRAM_CPP = tb_sram_main.cpp
SRAM_TOP = tb_sram
SRAM_DIR = obj_dir_sram

# ============================================================
# GPU
# ============================================================
GPU_SRCS = \
	tb_gpu.v \
	altsyncram_stub.v \
	$(COMMON_DIR)/gpu_core.v \
	$(COMMON_DIR)/gpu_edge_walker.v \
	$(COMMON_DIR)/gpu_tex_cache.v

GPU_CPP = tb_gpu_main.cpp
GPU_TOP = tb_gpu
GPU_DIR = obj_dir_gpu
GPU_PERSP_DIR = obj_dir_gpu_persp

# ============================================================
# AXI periph slave — BRAM read bursts, peripheral reads/writes, GPU MMIO
# write hammer. Uses altsyncram_stub.v in place of the Cyclone V
# altsyncram primitive (1-cycle latency, matching M10K).
# ============================================================
AXI_PERIPH_SRCS = \
	tb_axi_periph.v \
	altsyncram_stub.v \
	$(COMMON_DIR)/axi_periph_slave.v \
	$(TARGET_DIR)/snac_shifter.v \
	$(COMMON_DIR)/sync_fifo.v \
	$(TARGET_DIR)/apf/common.v

AXI_PERIPH_CPP = tb_axi_periph_main.cpp
AXI_PERIPH_TOP = tb_axi_periph
AXI_PERIPH_DIR = obj_dir_axi_periph
AXI_PERIPH_FLAGS = -Wno-WIDTHEXPAND -Wno-CASEINCOMPLETE -Wno-SYNCASYNCNET

# ============================================================
# Pocket bridge command FSM — warm Reset Enter must re-issue the
# Ready-To-Run target command while reset is held, even when setup_done
# never drops across an instance switch.
# ============================================================
BRIDGE_CMD_SRCS = \
	tb_core_bridge_cmd.v \
	altsyncram_stub.v \
	$(TARGET_DIR)/core_bridge_cmd.v \
	$(TARGET_DIR)/apf/mf_datatable.v \
	$(TARGET_DIR)/apf/common.v

BRIDGE_CMD_CPP = tb_core_bridge_cmd_main.cpp
BRIDGE_CMD_TOP = tb_core_bridge_cmd
BRIDGE_CMD_DIR = obj_dir_bridge_cmd
BRIDGE_CMD_FLAGS = -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC -Wno-CASEINCOMPLETE \
                   -Wno-SYNCASYNCNET -Wno-UNUSEDSIGNAL

# ============================================================
# Pocket bridge RX CDC — phase sweep of the REAL io_bridge_peripheral.
# bridge_spiclk and clk_74a are both 74.25 MHz with a power-up-latched
# relative phase; the harness sweeps that phase in sub-ns steps while
# playing full-rate Aristotle-style bulk write bursts and checks that
# every (addr, word) lands exactly once, in order, on the pmp port.
# ============================================================
BRIDGE_RX_SRCS = \
	tb_io_bridge.v \
	$(TARGET_DIR)/apf/io_bridge_peripheral.v \
	$(TARGET_DIR)/apf/common.v

BRIDGE_RX_CPP = tb_io_bridge_main.cpp
BRIDGE_RX_TOP = tb_io_bridge
BRIDGE_RX_DIR = obj_dir_bridge_rx
BRIDGE_RX_FLAGS = -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC -Wno-CASEINCOMPLETE \
                  -Wno-SYNCASYNCNET -Wno-UNUSEDSIGNAL -Wno-PINMISSING

# ============================================================
# GPU chain — exercises (slices + cpu_target_port + axi_periph_slave)
# in isolation to repro / disprove cr-gpu-and-tri-wedges issue 1.
# ============================================================
GPU_CHAIN_SRCS = \
	tb_gpu_chain.v \
	altsyncram_stub.v \
	$(COMMON_DIR)/axi_register_slice.v \
	$(COMMON_DIR)/cpu_target_port.v \
	$(COMMON_DIR)/axi_periph_slave.v \
	$(COMMON_DIR)/sync_fifo.v \
	lsu_axi_shim.v \
	$(TARGET_DIR)/apf/common.v
GPU_CHAIN_CPP = tb_gpu_chain_main.cpp
GPU_CHAIN_TOP = tb_gpu_chain
GPU_CHAIN_DIR = obj_dir_gpu_chain
GPU_CHAIN_FLAGS = -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC -Wno-CASEINCOMPLETE \
                  -Wno-SYNCASYNCNET -Wno-PINMISSING -Wno-UNUSEDSIGNAL \
                  -Wno-UNDRIVEN -Wno-UNUSEDPARAM

# ============================================================
# Audio pipeline (audio_output.v + dcfifo stub)
# ============================================================
AUDIO_SRCS = \
	tb_audio_pipeline.v \
	dcfifo_stub.v \
	$(TARGET_DIR)/audio_output.v

AUDIO_CPP = tb_audio_pipeline_main.cpp
AUDIO_TOP = tb_audio_pipeline
AUDIO_DIR = obj_dir_audio_pipeline
AUDIO_FLAGS = -Wno-WIDTHEXPAND -Wno-CASEINCOMPLETE -Wno-SYNCASYNCNET -Wno-UNOPTFLAT

# ============================================================
# Audio mixer (audio_mixer.v v3 — flat MMIO + HW group/master compose)
# ============================================================
MIXER_SRCS = \
	tb_audio_mixer.v \
	$(COMMON_DIR)/audio_mixer.v

MIXER_CPP = tb_audio_mixer_main.cpp
MIXER_TOP = tb_audio_mixer
MIXER_DIR = obj_dir_audio_mixer
MIXER_FLAGS = -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC -Wno-CASEINCOMPLETE -Wno-SYNCASYNCNET -Wno-UNOPTFLAT

# AXI skid buffer (full-throughput valid/ready register slice primitive)
SKID_SRCS = $(COMMON_DIR)/skid_buffer.v
SKID_CPP  = tb_skid_buffer_main.cpp
SKID_TOP  = skid_buffer
SKID_DIR  = obj_dir_skid

# MiSTer direct-framebuffer pipeline (SDRAM->DDR3 frame DMA + burst arbiter)
DDR3FB_SRCS = tb_ddr3_fb.v ../targets/mister/ddr3_fb.sv
DDR3FB_CPP  = tb_ddr3_fb_main.cpp
DDR3FB_TOP  = tb_ddr3_fb
DDR3FB_DIR  = obj_dir_ddr3_fb
DDR3FB_FLAGS = -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC -Wno-CASEINCOMPLETE

# ============================================================
# MiSTer SDRAM write-path multi-master contention — the convergent
# experiment.  Wires the REAL production write path that only MiSTer's
# 4-master shared SDRAM exercises under load:
#   axi_sdram_arbiter -> axi_sdram_slave -> io_sdram_test (== MiSTer
#   io_sdram.v, DQ-split) -> sdram_model (cycle-accurate, refresh-enforced).
# Drives GPU FB-write traffic (M0) against CPU/bridge/audio aggressors
# (M1/M2/M3) + scanout burst_rd injection and verifies every committed byte
# read back through the slave is byte-exact (golden shadow).  Additive,
# test-only.  Internal signal access (sdram_model.errors) needs the
# verilated internals header, enabled by --public-flat-rw.
# ============================================================
SDRAM_CONT_SRCS = \
	tb_sdram_cont.v \
	sdram_model_full.v \
	$(COMMON_DIR)/axi_sdram_arbiter.v \
	$(COMMON_DIR)/axi_sdram_slave.v \
	../targets/mister/synch_3.v \
	io_sdram_test.v

SDRAM_CONT_CPP = tb_sdram_cont_main.cpp
SDRAM_CONT_TOP = tb_sdram_cont
SDRAM_CONT_DIR = obj_dir_sdram_cont
SDRAM_CONT_FLAGS = -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC -Wno-CASEINCOMPLETE \
                   -Wno-SYNCASYNCNET -Wno-UNUSEDSIGNAL -Wno-PINMISSING \
                   --public-flat-rw

sdram-cont: $(SDRAM_CONT_DIR)/V$(SDRAM_CONT_TOP)
	@printf "\n=== MiSTer SDRAM write-path multi-master contention ===\n"
	./$(SDRAM_CONT_DIR)/V$(SDRAM_CONT_TOP)

$(SDRAM_CONT_DIR)/V$(SDRAM_CONT_TOP): $(SDRAM_CONT_SRCS) $(SDRAM_CONT_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS_NOTRACE) $(SDRAM_CONT_FLAGS) \
		--top-module $(SDRAM_CONT_TOP) --Mdir $(SDRAM_CONT_DIR) \
		-I$(COMMON_DIR) \
		$(SDRAM_CONT_SRCS) $(SDRAM_CONT_CPP)

# ============================================================
# REAL gpu_core FB-write path through the MiSTer SDRAM stack under
# multi-master contention.  Differential test: render an identical dense
# FB-write scene with aggressors OFF (reference / Pocket-like) and ON
# (DUT / MiSTer-like), both through gpu_core's own fbwq coalescer + the
# real arbiter+slave+io_sdram, then diff the framebuffers.  Closes the gap
# left by sdram-cont (idealized M0 generator) by driving the REAL fbwq AW/W
# bursts under genuine W-channel backpressure.  Additive, test-only.
# ============================================================
GPU_WRPATH_SRCS = \
	tb_gpu_wrpath.v \
	sdram_model_full.v \
	altsyncram_stub.v \
	$(COMMON_DIR)/gpu_core.v \
	$(COMMON_DIR)/gpu_edge_walker.v \
	$(COMMON_DIR)/gpu_tex_cache.v \
	$(COMMON_DIR)/axi_sdram_arbiter.v \
	$(COMMON_DIR)/axi_sdram_slave.v \
	../targets/mister/synch_3.v \
	io_sdram_test.v

GPU_WRPATH_CPP = tb_gpu_wrpath_main.cpp
GPU_WRPATH_TOP = tb_gpu_wrpath
GPU_WRPATH_DIR = obj_dir_gpu_wrpath
GPU_WRPATH_FLAGS = -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC -Wno-CASEINCOMPLETE \
                   -Wno-SYNCASYNCNET -Wno-UNUSEDSIGNAL -Wno-PINMISSING \
                   -Wno-UNOPTFLAT -Wno-UNDRIVEN -Wno-UNUSEDPARAM \
                   -Wno-MULTIDRIVEN -Wno-LATCH -Wno-CASEOVERLAP

gpu-wrpath: $(GPU_WRPATH_DIR)/V$(GPU_WRPATH_TOP)
	@printf "\n=== REAL gpu_core FB-write path under MiSTer SDRAM contention ===\n"
	./$(GPU_WRPATH_DIR)/V$(GPU_WRPATH_TOP)

$(GPU_WRPATH_DIR)/V$(GPU_WRPATH_TOP): $(GPU_WRPATH_SRCS) $(GPU_WRPATH_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS_NOTRACE) $(GPU_WRPATH_FLAGS) \
		--top-module $(GPU_WRPATH_TOP) --Mdir $(GPU_WRPATH_DIR) \
		-I$(COMMON_DIR) \
		$(GPU_WRPATH_SRCS) $(GPU_WRPATH_CPP)

# ============================================================
# gpu-wstall — FINAL convergent experiment: exercise the gpu_core fbwq
# AW-ahead OVERLAP path to a BYTE-EXACT verdict by backpressuring the
# FB-write W channel EXCLUSIVELY (command-fetch READ uncontended -> fences
# never starve).  Real gpu_core fbwq -> arbiter -> slave -> io_sdram ->
# sdram_model_full, golden FB check, AW-ahead path-fired counters.
# Additive, test-only; no production RTL change.  --hierarchical-child off so
# the testbench can tap gpu.fbwq_* internals.
# ============================================================
GPU_WSTALL_SRCS = \
	tb_gpu_wstall.v \
	sdram_model_full.v \
	altsyncram_stub.v \
	$(COMMON_DIR)/gpu_core.v \
	$(COMMON_DIR)/gpu_edge_walker.v \
	$(COMMON_DIR)/gpu_tex_cache.v \
	$(COMMON_DIR)/axi_sdram_arbiter.v \
	$(COMMON_DIR)/axi_sdram_slave.v \
	../targets/mister/synch_3.v \
	io_sdram_test.v

GPU_WSTALL_CPP = tb_gpu_wstall_main.cpp
GPU_WSTALL_TOP = tb_gpu_wstall
GPU_WSTALL_DIR = obj_dir_gpu_wstall
GPU_WSTALL_FLAGS = -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC -Wno-CASEINCOMPLETE \
                   -Wno-SYNCASYNCNET -Wno-UNUSEDSIGNAL -Wno-PINMISSING \
                   -Wno-UNOPTFLAT -Wno-UNDRIVEN -Wno-UNUSEDPARAM \
                   -Wno-MULTIDRIVEN -Wno-LATCH -Wno-CASEOVERLAP

gpu-wstall: $(GPU_WSTALL_DIR)/V$(GPU_WSTALL_TOP)
	@printf "\n=== gpu_core fbwq AW-ahead path: W-channel-exclusive stall verdict ===\n"
	./$(GPU_WSTALL_DIR)/V$(GPU_WSTALL_TOP)

$(GPU_WSTALL_DIR)/V$(GPU_WSTALL_TOP): $(GPU_WSTALL_SRCS) $(GPU_WSTALL_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS_NOTRACE) $(GPU_WSTALL_FLAGS) \
		--top-module $(GPU_WSTALL_TOP) --Mdir $(GPU_WSTALL_DIR) \
		-I$(COMMON_DIR) \
		$(GPU_WSTALL_SRCS) $(GPU_WSTALL_CPP)

# ============================================================
# scanout-race — SCANOUT vs GPU read-during-write across the page flip.
# Wires the REAL scanout (io_sdram burst_rd master, hard-real-time) against the
# REAL gpu_core FB-write path (arbiter+slave+io_sdram word ports) on a SHARED
# sdram_model_full, plus a byte-faithful copy of axi_periph_slave's triple-
# buffer swap SM.  Runs a realistic MiSTer 640x480 frame loop with the firmware's
# request-only flip discipline and detects (a) scanout reading the buffer the GPU
# is concurrently drawing and (b) any pixel that scans out as the clear color
# where the golden frame had content.  OUT_HEIGHT defaults to 480 (MiSTer: the
# raster is pinned to 480 for every mode, emu.sv:990); the -pocket variant uses
# the app-native height to model Pocket's lower scanout bandwidth.
# Additive, test-only; no production RTL change.
# ============================================================
SCANOUT_RACE_SRCS = \
	tb_scanout_race.v \
	sdram_model_full.v \
	altsyncram_stub.v \
	$(COMMON_DIR)/video_CRT_scanout_indexed_BRAM.v \
	$(COMMON_DIR)/gpu_core.v \
	$(COMMON_DIR)/gpu_edge_walker.v \
	$(COMMON_DIR)/gpu_tex_cache.v \
	$(COMMON_DIR)/axi_sdram_arbiter.v \
	$(COMMON_DIR)/axi_sdram_slave.v \
	../targets/mister/synch_3.v \
	io_sdram_test.v

SCANOUT_RACE_CPP = tb_scanout_race_main.cpp
SCANOUT_RACE_TOP = tb_scanout_race
SCANOUT_RACE_DIR = obj_dir_scanout_race
SCANOUT_RACE_POCKET_DIR = obj_dir_scanout_race_pocket
SCANOUT_RACE_FLAGS = -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC -Wno-CASEINCOMPLETE \
                     -Wno-SYNCASYNCNET -Wno-UNUSEDSIGNAL -Wno-PINMISSING \
                     -Wno-UNOPTFLAT -Wno-UNDRIVEN -Wno-UNUSEDPARAM \
                     -Wno-MULTIDRIVEN -Wno-LATCH -Wno-CASEOVERLAP

# MiSTer model: out_height pinned to 480 (DDA-upscales the 320x200 source).
# Runs: (1) the REAL GPU-triggered flip path (must REFUTE the race),
#       (2) the CPU FB_SWAP_CTRL no-backpressure flip path (must REFUTE too),
#       (3) the detector SELF-TEST (forced 2-buffer reuse, must REPRODUCE) so a
#           clean (1)/(2) is provably meaningful.
scanout-race: $(SCANOUT_RACE_DIR)/V$(SCANOUT_RACE_TOP)
	@printf "\n=== SCANOUT vs GPU read-during-write race (MiSTer: out 640x480) ===\n"
	@printf "\n--- (1) REAL GPU-triggered flip path (slave_swap_pending backpressure) ---\n"
	./$(SCANOUT_RACE_DIR)/V$(SCANOUT_RACE_TOP) 16 0 0 0
	@printf "\n--- (2) CPU FB_SWAP_CTRL flip path (NO backpressure) ---\n"
	./$(SCANOUT_RACE_DIR)/V$(SCANOUT_RACE_TOP) 16 0 0 1
	@printf "\n--- (3) detector SELF-TEST (forced 2-buffer reuse; MUST reproduce) ---\n"
	./$(SCANOUT_RACE_DIR)/V$(SCANOUT_RACE_TOP) 8 0 0 2

$(SCANOUT_RACE_DIR)/V$(SCANOUT_RACE_TOP): $(SCANOUT_RACE_SRCS) $(SCANOUT_RACE_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS_NOTRACE) $(SCANOUT_RACE_FLAGS) \
		-GOUT_HEIGHT=480 \
		--top-module $(SCANOUT_RACE_TOP) --Mdir $(SCANOUT_RACE_DIR) \
		-I$(COMMON_DIR) \
		$(SCANOUT_RACE_SRCS) $(SCANOUT_RACE_CPP)

# Pocket model: out_height = app-native (200) — lower scanout bandwidth, the
# source buffer is freed faster, so the GPU should never overtake the reader.
scanout-race-pocket: $(SCANOUT_RACE_POCKET_DIR)/V$(SCANOUT_RACE_TOP)
	@printf "\n=== SCANOUT vs GPU read-during-write race (Pocket: out 640x200) ===\n"
	@printf "\n--- REAL GPU-triggered flip path (Pocket scanout bandwidth) ---\n"
	./$(SCANOUT_RACE_POCKET_DIR)/V$(SCANOUT_RACE_TOP) 16 0 0 0
	@printf "\n--- detector SELF-TEST (forced 2-buffer reuse; MUST reproduce) ---\n"
	./$(SCANOUT_RACE_POCKET_DIR)/V$(SCANOUT_RACE_TOP) 8 0 0 2

$(SCANOUT_RACE_POCKET_DIR)/V$(SCANOUT_RACE_TOP): $(SCANOUT_RACE_SRCS) $(SCANOUT_RACE_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS_NOTRACE) $(SCANOUT_RACE_FLAGS) \
		-GOUT_HEIGHT=200 \
		--top-module $(SCANOUT_RACE_TOP) --Mdir $(SCANOUT_RACE_POCKET_DIR) \
		-I$(COMMON_DIR) \
		$(SCANOUT_RACE_SRCS) $(SCANOUT_RACE_CPP)

# ============================================================
# AXI SDRAM arbiter — focused 4-master grant FSM probe.
# Hunts the read-vs-write priority pathology suspected of wedging
# the GPU's framebuffer-flush during Duke3D hangs.
# ============================================================
ARBITER_SRCS = \
	tb_arbiter.v \
	$(COMMON_DIR)/axi_sdram_arbiter.v

ARBITER_CPP = tb_arbiter_main.cpp
ARBITER_TOP = tb_arbiter
ARBITER_DIR = obj_dir_arbiter
ARBITER_FLAGS = -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC -Wno-CASEINCOMPLETE -Wno-UNUSEDSIGNAL

# ============================================================
# MiSTer hps_bridge — boot.rom ioctl DMA + disk sector engine
# ============================================================
HPSBRG_SRCS = \
	tb_hps_bridge.v \
	sdram_fast_model.v \
	$(MISTER_DIR)/hps_bridge.v \
	$(COMMON_DIR)/axi_sdram_arbiter.v \
	$(COMMON_DIR)/axi_sdram_slave.v

MISTER_DIR = ../targets/mister
HPSBRG_CPP = tb_hps_bridge_main.cpp
HPSBRG_TOP = tb_hps_bridge
HPSBRG_DIR = obj_dir_hps_bridge
HPSBRG_FLAGS = -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC -Wno-CASEINCOMPLETE -Wno-UNUSEDSIGNAL -Wno-PINCONNECTEMPTY

# ============================================================
# MiSTer hps_mouse — framework mouse → input-hub slot 3 encoder.
# UNUSEDSIGNAL: the hps_io ports are contract-width; the PS/2 always-1
# bit and the wheel/reserved ext bits are deliberately ignored (frozen
# of_mouse_state_t ABI).
# ============================================================
HPSMOUSE_SRCS = \
	tb_hps_mouse.v \
	$(MISTER_DIR)/hps_mouse.v

HPSMOUSE_CPP = tb_hps_mouse_main.cpp
HPSMOUSE_TOP = tb_hps_mouse
HPSMOUSE_DIR = obj_dir_hps_mouse
HPSMOUSE_FLAGS = -Wno-UNUSEDSIGNAL

# ============================================================
# Full system bring-up — CPU + SDRAM + CRAM0 CDC + BRAM
# ============================================================
# Boots the VexiiRiscv through the v2 fabric, loads os.bin into the
# simulated CRAM0, and prints anything the kernel pushes out UART.
# Requires firmware.mif + os.bin to be present in ../../firmware/os/.
# The CPU netlist is per-target since the MiSTer went dual-issue:
# VexiiRiscv_os25.v = Pocket single-issue, VexiiRiscv_mister.v = MiSTer
# --decoders=2 --lanes=2 (same module name and AXI interface, so the
# surrounding fabric sources are identical).  `make system` boots the
# Pocket netlist, `make system-mister` the dual-issue one.
VEXII_POCKET = ../vendor/vexriscv/VexiiRiscv/VexiiRiscv_os25.v
VEXII_MISTER = ../vendor/vexriscv/VexiiRiscv/VexiiRiscv_mister.v

SYSTEM_SRCS_BASE = \
	tb_system.v \
	bram_model.v \
	sdram_fast_model.v \
	cram0_sim_model.v \
	$(COMMON_DIR)/cpu_system.v \
	$(COMMON_DIR)/cpu_target_port.v \
	$(COMMON_DIR)/cpu_target_port_per.v \
	$(TARGET_DIR)/cram0_cdc.v \
	$(COMMON_DIR)/axi_register_slice.v \
	$(COMMON_DIR)/axi_sdram_arbiter.v \
	$(COMMON_DIR)/axi_sdram_slave.v \
	$(COMMON_DIR)/axi_periph_slave.v \
	$(COMMON_DIR)/sync_fifo.v \
	$(TARGET_DIR)/snac_shifter.v \
	$(COMMON_DIR)/gpu_core.v \
	$(COMMON_DIR)/gpu_edge_walker.v \
	$(COMMON_DIR)/gpu_tex_cache.v \
	$(TARGET_DIR)/apf/common.v

SYSTEM_SRCS = $(SYSTEM_SRCS_BASE) $(VEXII_POCKET)

SYSTEM_CPP = tb_system_main.cpp
SYSTEM_TOP = tb_system
SYSTEM_DIR = obj_dir_system
SYSTEM_MISTER_DIR = obj_dir_system_mister
SYSTEM_FLAGS = -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC -Wno-CASEINCOMPLETE \
               -Wno-SYNCASYNCNET -Wno-UNOPTFLAT -Wno-STMTDLY -Wno-PINMISSING \
               -Wno-UNUSEDSIGNAL -Wno-UNDRIVEN -Wno-UNUSEDPARAM \
               -Wno-CASEOVERLAP -Wno-SELRANGE -Wno-WIDTHCONCAT \
               -Wno-MULTIDRIVEN -Wno-LATCH -Wno-IMPLICIT -Wno-ASCRANGE

# ============================================================
# Targets
# ============================================================
.PHONY: all sdram sdram-serialize sdram-native2 sdram-wcont0 sdram-all sdram-cont sram scanout scanout-lean analogchain gpu gpu-persp gpu-floor gpu-gpudemo gpu-acceptance gpu-acceptance-single gpu-acceptance-os30 gpu-acceptance-zraw gpu-acceptance-burst2 gpu-acceptance-all gpu-chain \
        gpu-wrpath gpu-wstall scanout-race scanout-race-pocket axi-periph bridge-cmd bridge-rx audio audio-mixer arbiter system system-mister run run-sram build clean \
        cram1-tex-chain cram1-cmap-chain gpu-cram1-repro

all: sdram-all sram gpu gpu-persp axi-periph bridge-cmd bridge-rx audio audio-mixer arbiter hps-bridge hps-mouse skid ddr3-fb

# --- SDRAM ---
sdram: $(SDRAM_DIR)/V$(SDRAM_TOP)
	./$(SDRAM_DIR)/V$(SDRAM_TOP)

$(SDRAM_DIR)/V$(SDRAM_TOP): $(SDRAM_SRCS) $(SDRAM_SYNCH) $(SDRAM_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) --top-module $(SDRAM_TOP) --Mdir $(SDRAM_DIR) \
		$(SDRAM_SRCS) $(SDRAM_SYNCH) $(SDRAM_CPP)

sdram-serialize: $(SDRAM_SERIAL_DIR)/V$(SDRAM_TOP)
	./$(SDRAM_SERIAL_DIR)/V$(SDRAM_TOP)

$(SDRAM_SERIAL_DIR)/V$(SDRAM_TOP): $(SDRAM_SRCS) $(SDRAM_SYNCH) $(SDRAM_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) -GSERIALIZE_WRITE_BURSTS=1 \
		--top-module $(SDRAM_TOP) --Mdir $(SDRAM_SERIAL_DIR) \
		$(SDRAM_SRCS) $(SDRAM_SYNCH) $(SDRAM_CPP)

sdram-native2: $(SDRAM_NATIVE2_DIR)/V$(SDRAM_TOP)
	./$(SDRAM_NATIVE2_DIR)/V$(SDRAM_TOP)

$(SDRAM_NATIVE2_DIR)/V$(SDRAM_TOP): $(SDRAM_SRCS) $(SDRAM_SYNCH) $(SDRAM_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) -GMAX_NATIVE_WRITE_BURST_LEN=1 \
		--top-module $(SDRAM_TOP) --Mdir $(SDRAM_NATIVE2_DIR) \
		$(SDRAM_SRCS) $(SDRAM_SYNCH) $(SDRAM_CPP)

# Same 491 checks with s_axi_wcont tied low: every multi-beat write takes
# the buffered writeback path (S_WR_FILL FIFO → one native io_sdram burst),
# the route CPU dirty-line evictions use on hardware.
sdram-wcont0: $(SDRAM_WCONT0_DIR)/V$(SDRAM_TOP)
	./$(SDRAM_WCONT0_DIR)/V$(SDRAM_TOP)

$(SDRAM_WCONT0_DIR)/V$(SDRAM_TOP): $(SDRAM_SRCS) $(SDRAM_SYNCH) $(SDRAM_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) -GWCONT_TIE=0 \
		--top-module $(SDRAM_TOP) --Mdir $(SDRAM_WCONT0_DIR) \
		$(SDRAM_SRCS) $(SDRAM_SYNCH) $(SDRAM_CPP)

sdram-all: sdram sdram-serialize sdram-native2 sdram-wcont0

# Backward compat aliases
build: $(SDRAM_DIR)/V$(SDRAM_TOP)
run: sdram

# --- SRAM ---
sram: $(SRAM_DIR)/V$(SRAM_TOP)
	./$(SRAM_DIR)/V$(SRAM_TOP)

$(SRAM_DIR)/V$(SRAM_TOP): $(SRAM_SRCS) $(SRAM_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) --top-module $(SRAM_TOP) --Mdir $(SRAM_DIR) \
		$(SRAM_SRCS) $(SRAM_CPP)

run-sram: sram

# ============================================================
# Scanout — analog (Analogizer) video timing measurement
# ============================================================
SCANOUT_SRCS = \
	tb_scanout.v \
	altsyncram_stub.v \
	$(COMMON_DIR)/video_CRT_scanout_indexed_BRAM.v
SCANOUT_CPP = tb_scanout_main.cpp
SCANOUT_TOP = tb_scanout
SCANOUT_DIR = obj_dir_scanout
SCANOUT_LEAN_DIR = obj_dir_scanout_lean

scanout: $(SCANOUT_DIR)/V$(SCANOUT_TOP)
	@printf "\n=== Scanout analog-timing test ===\n"
	./$(SCANOUT_DIR)/V$(SCANOUT_TOP)

$(SCANOUT_DIR)/V$(SCANOUT_TOP): $(SCANOUT_SRCS) $(SCANOUT_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS_NOTRACE) --top-module $(SCANOUT_TOP) --Mdir $(SCANOUT_DIR) \
		-I$(COMMON_DIR) \
		$(SCANOUT_SRCS) $(SCANOUT_CPP)

# OS30 lean variant: HAS_ANALOG_RASTER=0 prunes the dedicated analog
# raster/fetch/line-cache.  The harness (-DSCANOUT_TEST_LEAN) skips the
# now-vacuous analog timing/data assertions but KEEPS the LCD-side
# assertions, including the 1-line-deadline check against the brutal
# latency SDRAM model.
scanout-lean: $(SCANOUT_LEAN_DIR)/V$(SCANOUT_TOP)
	@printf "\n=== Scanout LCD-only test (HAS_ANALOG_RASTER=0) ===\n"
	./$(SCANOUT_LEAN_DIR)/V$(SCANOUT_TOP)

$(SCANOUT_LEAN_DIR)/V$(SCANOUT_TOP): $(SCANOUT_SRCS) $(SCANOUT_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS_NOTRACE) \
		-CFLAGS "-std=c++17 -O2 -DSCANOUT_TEST_LEAN" \
		-GHAS_ANALOG_RASTER=0 \
		--top-module $(SCANOUT_TOP) --Mdir $(SCANOUT_LEAN_DIR) \
		-I$(COMMON_DIR) \
		$(SCANOUT_SRCS) $(SCANOUT_CPP)

# ============================================================
# Analogizer chain — scanout -> real Analogizer -> cart pins (VGA)
# ============================================================
ANALOGCHAIN_SRCS = \
	tb_analogizer_chain.v \
	altsyncram_stub.v \
	$(COMMON_DIR)/video_CRT_scanout_indexed_BRAM.v \
	$(TARGET_DIR)/third_party/analogizer/openFPGA_Pocket_Analogizer.v \
	scandoubler_test.v
ANALOGCHAIN_CPP = tb_analogizer_chain_main.cpp
ANALOGCHAIN_TOP = tb_analogizer_chain
ANALOGCHAIN_DIR = obj_dir_analogchain

# Verilator chokes on scandoubler.v's zero-width part-select [..-:n] in its
# DEAD (n=0, since COLOR_DEPTH==OUT_COLOR_DEPTH) color-expand branch; Quartus
# prunes it. Patch -:n -> -:1 in that unreachable branch for sim only.
scandoubler_test.v: $(TARGET_DIR)/third_party/analogizer/scandoubler.v
	sed 's/ -:n\]/ -:1]/g' $< > $@

analogchain: $(ANALOGCHAIN_DIR)/V$(ANALOGCHAIN_TOP)
	@printf "\n=== Analogizer cart-pin chain test ===\n"
	./$(ANALOGCHAIN_DIR)/V$(ANALOGCHAIN_TOP)

$(ANALOGCHAIN_DIR)/V$(ANALOGCHAIN_TOP): $(ANALOGCHAIN_SRCS) $(ANALOGCHAIN_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS_NOTRACE) --top-module $(ANALOGCHAIN_TOP) --Mdir $(ANALOGCHAIN_DIR) \
		-I$(COMMON_DIR) -I$(TARGET_DIR)/third_party/analogizer \
		$(ANALOGCHAIN_SRCS) $(ANALOGCHAIN_CPP)

# --- GPU ---
# Single GPU build. Triangles + perspective spans + pipelined fragment
# processor are all unconditional in the hardware.
gpu: $(GPU_DIR)/V$(GPU_TOP)
	@printf "\n=== GPU tests ===\n"
	./$(GPU_DIR)/V$(GPU_TOP)

# Full-feature config: define INCLUDE_TRANSLUC so the blend path is present
# (matches the hardware default — translucency is in the OS25/MiSTer builds).
$(GPU_DIR)/V$(GPU_TOP): $(GPU_SRCS) $(GPU_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) --top-module $(GPU_TOP) --Mdir $(GPU_DIR) \
		+define+INCLUDE_TRANSLUC \
		-I$(COMMON_DIR) \
		$(GPU_SRCS) $(GPU_CPP)

gpu-persp: $(GPU_PERSP_DIR)/V$(GPU_TOP)
	@printf "\n=== GPU perspective-span tests ===\n"
	./$(GPU_PERSP_DIR)/V$(GPU_TOP)

$(GPU_PERSP_DIR)/V$(GPU_TOP): $(GPU_SRCS) $(GPU_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) \
		-CFLAGS "-std=c++17 -O2 -DGPU_TEST_ENABLE_PERSP=1" \
		+define+INCLUDE_TRANSLUC \
		--top-module $(GPU_TOP) --Mdir $(GPU_PERSP_DIR) \
		-I$(COMMON_DIR) \
		$(GPU_SRCS) $(GPU_CPP)

# --- GPU floor-span coverage (BUILD/Duke3D hlineasm4 vs GPU multiply) ---
# Focused regression suite that drives scalar span payloads with BUILD-style
# inputs and compares against a CPU-side hlineasm4 reference.  Some
# cases (F2/F3/F6/F8) require shift-mode tex addressing that the RTL
# retired — those XFAIL with a documented gap message instead of
# counting as failures.  Run `make gpu-floor` standalone; intentionally
# excluded from `make all` until the gap closes.
GPU_FLOOR_CPP = tb_gpu_floor_span_main.cpp
GPU_FLOOR_DIR = obj_dir_gpu_floor
gpu-floor: $(GPU_FLOOR_DIR)/V$(GPU_TOP)
	@printf "\n=== GPU floor-span coverage ===\n"
	./$(GPU_FLOOR_DIR)/V$(GPU_TOP) || true

$(GPU_FLOOR_DIR)/V$(GPU_TOP): $(GPU_SRCS) $(GPU_FLOOR_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) --top-module $(GPU_TOP) \
		--Mdir $(GPU_FLOOR_DIR) \
		-I$(COMMON_DIR) \
		$(GPU_SRCS) $(GPU_FLOOR_CPP)

# --- gpudemo reproducer (long-running span-heavy stream) ---
# Mirrors openfpgaOS-SDK/src/apps/gpudemo's per-frame submission so we
# can chase on-hardware hangs (commit 8ae32d6's "fps=0001" trap) in the
# Verilator harness.  Run `make gpu-gpudemo` standalone.
GPU_GPUDEMO_CPP = tb_gpu_gpudemo_main.cpp
GPU_GPUDEMO_DIR = obj_dir_gpu_gpudemo
gpu-gpudemo: $(GPU_GPUDEMO_DIR)/V$(GPU_TOP)
	@printf "\n=== GPU gpudemo repro ===\n"
	./$(GPU_GPUDEMO_DIR)/V$(GPU_TOP) || true

$(GPU_GPUDEMO_DIR)/V$(GPU_TOP): $(GPU_SRCS) $(GPU_GPUDEMO_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) --top-module $(GPU_TOP) \
		--Mdir $(GPU_GPUDEMO_DIR) \
		-I$(COMMON_DIR) \
		$(GPU_SRCS) $(GPU_GPUDEMO_CPP)

# --- GPU acceptance suite (testgpu.md plan) ---
# Byte-exact CPU-reference comparison for every command, plus the
# combination matrices (state-change, paint-order, cmap cache, tex
# cache, accumulator/blend, ring/DMA).  Hard-fails on any byte
# mismatch.  Run `make gpu-acceptance` standalone; intentionally not
# part of `make all` until first green run is captured.
GPU_ACCEPT_CPP = tb_gpu_acceptance_main.cpp
GPU_ACCEPT_DIR = obj_dir_gpu_acceptance
GPU_ACCEPT_SINGLE_DIR = obj_dir_gpu_acceptance_single
GPU_ACCEPT_OS30_DIR = obj_dir_gpu_acceptance_os30
GPU_ACCEPT_ZRAW_DIR = obj_dir_gpu_acceptance_zraw
gpu-acceptance: $(GPU_ACCEPT_DIR)/V$(GPU_TOP)
	@printf "\n=== GPU acceptance suite ===\n"
	./$(GPU_ACCEPT_DIR)/V$(GPU_TOP)

# Full-feature config: define INCLUDE_TRANSLUC so the blend path is present
# (the tb_gpu -G params already default the triangle/compact/column gates ON).
$(GPU_ACCEPT_DIR)/V$(GPU_TOP): $(GPU_SRCS) $(GPU_ACCEPT_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) --top-module $(GPU_TOP) \
		+define+INCLUDE_TRANSLUC \
		--Mdir $(GPU_ACCEPT_DIR) \
		-I$(COMMON_DIR) \
		$(GPU_SRCS) $(GPU_ACCEPT_CPP)

gpu-acceptance-single: $(GPU_ACCEPT_SINGLE_DIR)/V$(GPU_TOP)
	@printf "\n=== GPU acceptance suite (single-beat FB writes) ===\n"
	./$(GPU_ACCEPT_SINGLE_DIR)/V$(GPU_TOP)

$(GPU_ACCEPT_SINGLE_DIR)/V$(GPU_TOP): $(GPU_SRCS) $(GPU_ACCEPT_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) \
		--top-module $(GPU_TOP) \
		+define+INCLUDE_TRANSLUC \
		--Mdir $(GPU_ACCEPT_SINGLE_DIR) \
		-I$(COMMON_DIR) \
		$(GPU_SRCS) $(GPU_ACCEPT_CPP)

gpu-acceptance-burst2: gpu-acceptance

# OS30 lean GPU variant (Pocket Quake/Quake2 build): compact-direct 0x48
# (INCLUDE_COMPACT_SPAN=0) and 0x4C column lists (INCLUDE_COLUMN_LIST=0)
# drain as no-ops, the translucency LUT is chokepointed out (INCLUDE_TRANSLUC
# not defined), and the edge walker runs the serial divider
# (GPU_EW_PARALLEL_DIVS=0).  0x49 param-tri, 0x4A/0x4B vert-tri, 0x4D recs-tri,
# long-form 0x48 and the z window all stay ENABLED.  -DGPU_TEST_OS30_LEAN
# compiles out the harness tests whose vehicle is the removed compact/column/
# transluc paths and switches the lean negative tests' expectations.
gpu-acceptance-os30: $(GPU_ACCEPT_OS30_DIR)/V$(GPU_TOP)
	@printf "\n=== GPU acceptance suite (OS30 lean variant) ===\n"
	./$(GPU_ACCEPT_OS30_DIR)/V$(GPU_TOP)

$(GPU_ACCEPT_OS30_DIR)/V$(GPU_TOP): $(GPU_SRCS) $(GPU_ACCEPT_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) \
		-CFLAGS "-std=c++17 -O2 -DGPU_TEST_OS30_LEAN" \
		-GINCLUDE_COMPACT_SPAN=0 \
		-GINCLUDE_COLUMN_LIST=0 \
		-GGPU_EW_PARALLEL_DIVS=0 \
		-GINCLUDE_PARAM_TRI=1 \
		-GINCLUDE_VERT_TRI=1 \
		-GINCLUDE_PARAM_TRI_RECS=1 \
		-GGPU_Z_READ_WINDOW=4 \
		--top-module $(GPU_TOP) \
		--Mdir $(GPU_ACCEPT_OS30_DIR) \
		-I$(COMMON_DIR) \
		$(GPU_SRCS) $(GPU_ACCEPT_CPP)

gpu-acceptance-all: gpu-acceptance gpu-acceptance-single gpu-acceptance-os30

# In-flight z-RAW coherency variant.  Same full-feature config as
# gpu-acceptance (GPU_Z_READ_WINDOW=4, INCLUDE_TRANSLUC), but the binary is
# RUN with +gpu_wr_latency=24 so the SDRAM write stub holds the z-write
# commit and the directed test_param_span_z_raw_inflight_same_line assertion
# goes active (it skips when +gpu_wr_latency<8).  Proves the z-read /
# color-write drain decouple does not surface stale in-flight z.
gpu-acceptance-zraw: $(GPU_ACCEPT_ZRAW_DIR)/V$(GPU_TOP)
	@printf "\n=== GPU acceptance suite (in-flight z-RAW, +gpu_wr_latency=24) ===\n"
	./$(GPU_ACCEPT_ZRAW_DIR)/V$(GPU_TOP) +gpu_wr_latency=24

$(GPU_ACCEPT_ZRAW_DIR)/V$(GPU_TOP): $(GPU_SRCS) $(GPU_ACCEPT_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) --top-module $(GPU_TOP) \
		+define+INCLUDE_TRANSLUC \
		--Mdir $(GPU_ACCEPT_ZRAW_DIR) \
		-I$(COMMON_DIR) \
		$(GPU_SRCS) $(GPU_ACCEPT_CPP)

# Truecolor (RGB565 direct-color) variant: os30 lean config + INCLUDE_DIRECT_COLOR=1.
# -DGPU_TEST_TRUECOLOR compiles in the truecolor-specific tests and enables the
# direct-color RTL path (which is parameter-gated, never exercised by the other
# configs).  This is the ONLY config that runs the truecolor fragment pipeline.
GPU_ACCEPT_TC_DIR = obj_dir_gpu_acceptance_tc
gpu-acceptance-tc: $(GPU_ACCEPT_TC_DIR)/V$(GPU_TOP)
	@printf "\n=== GPU acceptance suite (truecolor RGB565 variant) ===\n"
	./$(GPU_ACCEPT_TC_DIR)/V$(GPU_TOP)

$(GPU_ACCEPT_TC_DIR)/V$(GPU_TOP): $(GPU_SRCS) $(GPU_ACCEPT_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) \
		-CFLAGS "-std=c++17 -O2 -DGPU_TEST_OS30_LEAN -DGPU_TEST_TRUECOLOR" \
		-GINCLUDE_COMPACT_SPAN=0 \
		-GINCLUDE_COLUMN_LIST=0 \
		-GGPU_EW_PARALLEL_DIVS=0 \
		-GINCLUDE_PARAM_TRI=1 \
		-GINCLUDE_VERT_TRI=1 \
		-GINCLUDE_PARAM_TRI_RECS=1 \
		-GGPU_Z_READ_WINDOW=4 \
		-GINCLUDE_DIRECT_COLOR=1 \
		-GINCLUDE_CLIP_TRI=0 \
		--top-module $(GPU_TOP) \
		--Mdir $(GPU_ACCEPT_TC_DIR) \
		-I$(COMMON_DIR) \
		$(GPU_SRCS) $(GPU_ACCEPT_CPP)

# SM64 FUNCTIONAL test target: verifies the new GPU transform/lighting front-end
# (T1 0x52 xform-rgb, T3 0x53/0x54 vertex cache, T4 0x55 lighting).  This is a
# VERIFICATION config, NOT the shipped SM64 bitstream gating: PARAM_TRI/RECS and
# the palette lane stay ON so the full existing suite (which exercises 0x49/0x4D
# and palettized paths) stays green alongside the new -DGPU_TEST_SM64 tests.  The
# shipped SM64 prune (PARAM_TRI=0, PALETTE=0, TEX off) is validated separately by
# the elaboration smoke target `gpu-elab-sm64` and the Quartus fit.
GPU_ACCEPT_SM64_DIR = obj_dir_gpu_acceptance_sm64
gpu-acceptance-sm64: $(GPU_ACCEPT_SM64_DIR)/V$(GPU_TOP)
	@printf "\n=== GPU acceptance suite (SM64 functional config) ===\n"
	./$(GPU_ACCEPT_SM64_DIR)/V$(GPU_TOP)

$(GPU_ACCEPT_SM64_DIR)/V$(GPU_TOP): $(GPU_SRCS) $(GPU_ACCEPT_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) \
		-CFLAGS "-std=c++17 -O2 -DGPU_TEST_OS30_LEAN -DGPU_TEST_TRUECOLOR -DGPU_TEST_SM64" \
		-GINCLUDE_COMPACT_SPAN=0 \
		-GINCLUDE_COLUMN_LIST=0 \
		-GGPU_EW_PARALLEL_DIVS=0 \
		-GINCLUDE_PARAM_TRI=1 \
		-GINCLUDE_VERT_TRI=1 \
		-GINCLUDE_PARAM_TRI_RECS=1 \
		-GGPU_Z_READ_WINDOW=4 \
		-GINCLUDE_DIRECT_COLOR=1 \
		-GINCLUDE_XFORM_RGB=1 \
		-GINCLUDE_VTX_CACHE=1 \
		-GINCLUDE_GPU_LIGHT=1 \
		-GINCLUDE_PALETTE=1 \
		-GINCLUDE_COMBINE=1 \
		--top-module $(GPU_TOP) \
		--Mdir $(GPU_ACCEPT_SM64_DIR) \
		-I$(COMMON_DIR) \
		$(GPU_SRCS) $(GPU_ACCEPT_CPP)

# Truecolor-ONLY functional build (INCLUDE_PALETTE=0) — proves the palette-gated
# truecolor + SM64 paths still render with the colormap lane pruned.  Palettized
# tests in the suite are EXPECTED to fail here (their lane is gone); only the
# truecolor_* and SM64 (xform/indexed/lit) tests are asserted by the caller.
GPU_ACCEPT_NOPAL_DIR = obj_dir_gpu_acceptance_nopal
gpu-acceptance-sm64-nopal: $(GPU_ACCEPT_NOPAL_DIR)/V$(GPU_TOP)
	@printf "\n=== GPU acceptance (SM64, truecolor-only / palette gated out) ===\n"
	-./$(GPU_ACCEPT_NOPAL_DIR)/V$(GPU_TOP)

$(GPU_ACCEPT_NOPAL_DIR)/V$(GPU_TOP): $(GPU_SRCS) $(GPU_ACCEPT_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) \
		-CFLAGS "-std=c++17 -O2 -DGPU_TEST_OS30_LEAN -DGPU_TEST_TRUECOLOR -DGPU_TEST_SM64" \
		-GINCLUDE_COMPACT_SPAN=0 -GINCLUDE_COLUMN_LIST=0 -GGPU_EW_PARALLEL_DIVS=0 \
		-GINCLUDE_PARAM_TRI=1 -GINCLUDE_VERT_TRI=1 -GINCLUDE_PARAM_TRI_RECS=1 \
		-GGPU_Z_READ_WINDOW=4 -GINCLUDE_DIRECT_COLOR=1 -GINCLUDE_XFORM_RGB=1 \
		-GINCLUDE_VTX_CACHE=1 -GINCLUDE_GPU_LIGHT=1 -GINCLUDE_PALETTE=0 \
		--top-module $(GPU_TOP) --Mdir $(GPU_ACCEPT_NOPAL_DIR) -I$(COMMON_DIR) \
		$(GPU_SRCS) $(GPU_ACCEPT_CPP)

# EXACT shipped os30 bitstream GPU config (mirrors core_top.v gpu_core params for
# DEFS_os30 = VERT_TRI DIRECT_COLOR COMBINE Z_BURST HW_MIXER): every other module
# folds OFF — PARAM_TRI/RECS=0, COMPACT/COLUMN=0, XFORM/CLIP/VTX/LIGHT/XFORM_MAC=0,
# PALETTE=0, TEX_MEM=0, PARAM_SPAN_Q29=0.  Runs the truecolor fragment suite in the
# REAL os30 fold so the untextured-truecolor (white-texel Gouraud = Mario base face)
# path is exercised in the config that ships — the kitchen-sink sm64 target never is.
GPU_ACCEPT_OS30X_DIR = obj_dir_gpu_acceptance_os30x
gpu-acceptance-os30-exact: $(GPU_ACCEPT_OS30X_DIR)/V$(GPU_TOP)
	@printf "\n=== GPU acceptance suite (EXACT shipped os30 GPU config) ===\n"
	-./$(GPU_ACCEPT_OS30X_DIR)/V$(GPU_TOP)

$(GPU_ACCEPT_OS30X_DIR)/V$(GPU_TOP): $(GPU_SRCS) $(GPU_ACCEPT_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) \
		-CFLAGS "-std=c++17 -O2 -DGPU_TEST_OS30_LEAN -DGPU_TEST_TRUECOLOR" \
		-GINCLUDE_COMPACT_SPAN=0 -GINCLUDE_COLUMN_LIST=0 -GGPU_EW_PARALLEL_DIVS=0 \
		-GINCLUDE_PARAM_TRI=0 -GINCLUDE_VERT_TRI=1 -GINCLUDE_PARAM_TRI_RECS=0 \
		-GGPU_Z_READ_WINDOW=4 -GINCLUDE_DIRECT_COLOR=1 -GINCLUDE_COMBINE=1 \
		-GINCLUDE_XFORM_RGB=0 -GINCLUDE_CLIP_TRI=0 -GINCLUDE_VTX_CACHE=0 \
		-GINCLUDE_GPU_LIGHT=0 -GINCLUDE_PALETTE=0 \
		-GINCLUDE_PARAM_SPAN_Q29=0 \
		--top-module $(GPU_TOP) --Mdir $(GPU_ACCEPT_OS30X_DIR) -I$(COMMON_DIR) \
		$(GPU_SRCS) $(GPU_ACCEPT_CPP)

# EXACT shipped MiSTer bitstream GPU config (mirrors emu.sv's gpu_core params
# for targets/mister INCLUDE_DEFS = HW_MIXER TRANSLUC COLUMN_LIST COMPACT_SPAN
# PARAM_TRI VERT_TRI PARAM_TRI_RECS).  emu.sv passes exactly six params —
# PARAM_TRI/VERT_TRI/PARAM_TRI_RECS/COMPACT_SPAN/COLUMN_LIST=1, TEX_MEM=0 —
# and leaves the rest at gpu_core defaults (Z_WINDOW=4, EW_DIVS=1,
# DIRECT_COLOR=0, XFORM_RGB=0, CLIP_TRI=1, VTX/LIGHT=0, PALETTE=1, COMBINE=1,
# PARAM_SPAN_Q29=1) with the INCLUDE_TRANSLUC macro defined by the qsf.  The
# ONLY delta vs the always-green full-feature gpu-acceptance build is
# INCLUDE_TEX_MEM=0 — the fold no other acceptance config has ever elaborated
# (same coverage-gap class the os30-exact variant closed).  Full suite, full
# assertions: must be green.
GPU_ACCEPT_MISTERX_DIR = obj_dir_gpu_acceptance_misterx
gpu-acceptance-mister-exact: $(GPU_ACCEPT_MISTERX_DIR)/V$(GPU_TOP)
	@printf "\n=== GPU acceptance suite (EXACT shipped MiSTer GPU config) ===\n"
	./$(GPU_ACCEPT_MISTERX_DIR)/V$(GPU_TOP)

$(GPU_ACCEPT_MISTERX_DIR)/V$(GPU_TOP): $(GPU_SRCS) $(GPU_ACCEPT_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) \
		+define+INCLUDE_TRANSLUC \
		-GINCLUDE_PARAM_TRI=1 -GINCLUDE_VERT_TRI=1 -GINCLUDE_PARAM_TRI_RECS=1 \
		-GINCLUDE_COMPACT_SPAN=1 -GINCLUDE_COLUMN_LIST=1 \
		-GINCLUDE_TEX_MEM=0 \
		-GGPU_Z_READ_WINDOW=4 -GGPU_EW_PARALLEL_DIVS=1 \
		-GINCLUDE_DIRECT_COLOR=0 -GINCLUDE_XFORM_RGB=0 -GINCLUDE_CLIP_TRI=1 \
		-GINCLUDE_VTX_CACHE=0 -GINCLUDE_GPU_LIGHT=0 -GINCLUDE_PALETTE=1 \
		-GINCLUDE_COMBINE=1 -GINCLUDE_PARAM_SPAN_Q29=1 \
		--top-module $(GPU_TOP) --Mdir $(GPU_ACCEPT_MISTERX_DIR) -I$(COMMON_DIR) \
		$(GPU_SRCS) $(GPU_ACCEPT_CPP)

# MAP01 courtyard "fence scene" interleave test against the EXACT MiSTer GPU
# config (same -G set as gpu-acceptance-mister-exact).  Models the field
# symptom shape — near-wall AXIS_Y Q29 columns + AXIS_X Q29 visplane spans +
# two 0x4C sky visplanes (incl. the fence-gap BAND) + masked-bar overdraw —
# in the app's real phase-batched submission cadence, byte-compared against
# a drained isolated-oracle render, plus a per-region wrong-texture check.
GPU_SCENE_MISTER_CPP = tb_gpu_mister_scene_main.cpp
GPU_SCENE_MISTER_DIR = obj_dir_gpu_scene_misterx
gpu-acceptance-mister-scene: $(GPU_SCENE_MISTER_DIR)/V$(GPU_TOP)
	@printf "\n=== GPU MiSTer courtyard scene test (EXACT shipped MiSTer config) ===\n"
	./$(GPU_SCENE_MISTER_DIR)/V$(GPU_TOP)

$(GPU_SCENE_MISTER_DIR)/V$(GPU_TOP): $(GPU_SRCS) $(GPU_SCENE_MISTER_CPP) $(GPU_ACCEPT_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) \
		+define+INCLUDE_TRANSLUC \
		-GINCLUDE_PARAM_TRI=1 -GINCLUDE_VERT_TRI=1 -GINCLUDE_PARAM_TRI_RECS=1 \
		-GINCLUDE_COMPACT_SPAN=1 -GINCLUDE_COLUMN_LIST=1 \
		-GINCLUDE_TEX_MEM=0 \
		-GGPU_Z_READ_WINDOW=4 -GGPU_EW_PARALLEL_DIVS=1 \
		-GINCLUDE_DIRECT_COLOR=0 -GINCLUDE_XFORM_RGB=0 -GINCLUDE_CLIP_TRI=1 \
		-GINCLUDE_VTX_CACHE=0 -GINCLUDE_GPU_LIGHT=0 -GINCLUDE_PALETTE=1 \
		-GINCLUDE_COMBINE=1 -GINCLUDE_PARAM_SPAN_Q29=1 \
		--top-module $(GPU_TOP) --Mdir $(GPU_SCENE_MISTER_DIR) -I$(COMMON_DIR) \
		$(GPU_SRCS) $(GPU_SCENE_MISTER_CPP)

# Elaboration smoke test of the SHIPPED SM64 bitstream gating: PARAM_TRI/RECS off,
# palette off, fast-tex off (z-burst kept).  Must elaborate cleanly — the pruned
# paths fold to constant 0.  Catches prune-folding errors before the Quartus fit.
gpu-elab-sm64:
	@printf "\n=== GPU elaboration smoke (SM64 shipped prune) ===\n"
	$(VERILATOR) --lint-only -Wall -Wno-fatal -Wno-BADVLTPRAGMA \
		-Wno-WIDTH -Wno-UNOPTFLAT -Wno-CASEINCOMPLETE \
		-GINCLUDE_COMPACT_SPAN=0 \
		-GINCLUDE_COLUMN_LIST=0 \
		-GGPU_EW_PARALLEL_DIVS=0 \
		-GINCLUDE_PARAM_TRI=0 \
		-GINCLUDE_VERT_TRI=1 \
		-GINCLUDE_PARAM_TRI_RECS=0 \
		-GGPU_Z_READ_WINDOW=4 \
		-GINCLUDE_DIRECT_COLOR=1 \
		-GINCLUDE_XFORM_RGB=1 \
		-GINCLUDE_VTX_CACHE=1 \
		-GINCLUDE_GPU_LIGHT=1 \
		-GINCLUDE_PALETTE=0 \
		--top-module $(GPU_TOP) \
		-I$(COMMON_DIR) \
		$(GPU_SRCS)
	@printf "=== SM64 prune elaborates clean ===\n"

# --- GPU cyc/pixel EMPIRICAL measurement (SM64-class triangle) ---
# Drives ONE large textured RGB565-truecolor z-buffered triangle through the
# real gpu_core fragment pipeline and measures GPU clocks (kick->fence) and
# pixels written => cyc/pixel.  Varies tex / z / truecolor / overdraw one at a
# time to attribute the cost.  Same RTL config as gpu-acceptance-sm64
# (INCLUDE_DIRECT_COLOR=1, VERT_TRI=1, PARAM_TRI=1, PALETTE=1, Z_READ_WINDOW=4)
# so both the 0x4E truecolor and 0x4B palettized paths are live.
#
# SDRAM read latency: tb_gpu's model defaults to 2-cycle initial read latency.
# Pass +gpu_rd_latency=N at runtime to model realistic axi_sdram_slave latency,
# e.g.  make gpu-perf GPU_PERF_ARGS="+gpu_rd_latency=24"
GPU_PERF_CPP = tb_gpu_perf_main.cpp
GPU_PERF_DIR = obj_dir_gpu_perf
GPU_PERF_ARGS ?=
gpu-perf: $(GPU_PERF_DIR)/V$(GPU_TOP)
	@printf "\n=== GPU cyc/pixel measurement (SM64-class tri) ===\n"
	./$(GPU_PERF_DIR)/V$(GPU_TOP) $(GPU_PERF_ARGS)

$(GPU_PERF_DIR)/V$(GPU_TOP): $(GPU_SRCS) $(GPU_PERF_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS_NOTRACE) \
		-CFLAGS "-std=c++17 -O2" \
		-GINCLUDE_COMPACT_SPAN=0 -GINCLUDE_COLUMN_LIST=0 -GGPU_EW_PARALLEL_DIVS=0 \
		-GINCLUDE_PARAM_TRI=1 -GINCLUDE_VERT_TRI=1 -GINCLUDE_PARAM_TRI_RECS=1 \
		-GGPU_Z_READ_WINDOW=4 -GINCLUDE_DIRECT_COLOR=1 -GINCLUDE_XFORM_RGB=1 \
		-GINCLUDE_VTX_CACHE=1 -GINCLUDE_GPU_LIGHT=1 -GINCLUDE_PALETTE=1 \
		--top-module $(GPU_TOP) --Mdir $(GPU_PERF_DIR) -I$(COMMON_DIR) \
		$(GPU_SRCS) $(GPU_PERF_CPP)

# --- GPU per-triangle SETUP vs PER-PIXEL split (small-triangle sweep) ---
# Renders single 0x4A+0x4E truecolor z-buffered triangles of varying area as
# their own kick->fence and fits cycles = SETUP + PERPX*pixels to separate the
# fixed write-channel-idle setup cost from the marginal write-bound per-pixel
# cost.  Same RTL config as gpu-perf.  Pass +gpu_rd_latency=N for io_sdram.
GPU_SETUP_CPP = tb_gpu_setup_main.cpp
GPU_SETUP_DIR = obj_dir_gpu_setup
GPU_SETUP_ARGS ?=
gpu-setup: $(GPU_SETUP_DIR)/V$(GPU_TOP)
	@printf "\n=== GPU setup-vs-pixel split (SM64 small tris) ===\n"
	./$(GPU_SETUP_DIR)/V$(GPU_TOP) $(GPU_SETUP_ARGS)

$(GPU_SETUP_DIR)/V$(GPU_TOP): $(GPU_SRCS) $(GPU_SETUP_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS_NOTRACE) \
		-CFLAGS "-std=c++17 -O2" \
		-GINCLUDE_COMPACT_SPAN=0 -GINCLUDE_COLUMN_LIST=0 -GGPU_EW_PARALLEL_DIVS=0 \
		-GINCLUDE_PARAM_TRI=1 -GINCLUDE_VERT_TRI=1 -GINCLUDE_PARAM_TRI_RECS=1 \
		-GGPU_Z_READ_WINDOW=4 -GINCLUDE_DIRECT_COLOR=1 -GINCLUDE_XFORM_RGB=1 \
		-GINCLUDE_VTX_CACHE=1 -GINCLUDE_GPU_LIGHT=1 -GINCLUDE_PALETTE=1 \
		--top-module $(GPU_TOP) --Mdir $(GPU_SETUP_DIR) -I$(COMMON_DIR) \
		$(GPU_SRCS) $(GPU_SETUP_CPP)

# --- Shared Verilator flags for the texture-chain TBs (cram1-tex/cmap-chain) ---
TEX_CHAIN_VFLAGS = --binary --timing -Wno-fatal -Wno-WIDTH -Wno-UNOPTFLAT -Wno-CASEINCOMPLETE -I../common -I.


# --- CRAM1 sync-burst texture path: GPU tex fill -> burst read, REAL models ---
# Real cram1_controller + cram1_phy + cram_chip_model (pin-level).  Proves the
# sync-burst read AND that async word_wr upload coexists with sync-burst-read BCR.
cram1-tex-chain: obj_dir_cram1_tex_chain/Vtb_cram1_tex_chain
	@printf "\n=== CRAM1 tex chain (tex_cache -> burst adapter -> cram1_controller+phy -> chip model) ===\n"
	./obj_dir_cram1_tex_chain/Vtb_cram1_tex_chain
obj_dir_cram1_tex_chain/Vtb_cram1_tex_chain: tb_cram1_tex_chain.v $(TARGET_DIR)/gpu_cram1_tex_adapter.v $(TARGET_DIR)/cram1_controller.v $(TARGET_DIR)/cram1_phy.sv ../common/gpu_tex_cache.v cram_chip_model.v
	$(VERILATOR) $(TEX_CHAIN_VFLAGS) -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC --Mdir obj_dir_cram1_tex_chain \
		--top-module tb_cram1_tex_chain \
		tb_cram1_tex_chain.v $(TARGET_DIR)/gpu_cram1_tex_adapter.v $(TARGET_DIR)/cram1_controller.v $(TARGET_DIR)/cram1_phy.sv ../common/gpu_tex_cache.v cram_chip_model.v

# --- CRAM1 sync-burst COLORMAP path: GPU tex_cache PORT B (cmap) fill ->
# burst read, REAL models.  Fills the coverage hole where no test drives
# gpu_tex_cache port B through the real cram1 sync-burst chain (the SDRAM-stub
# acceptance suite uses a different R-beat pattern; tb_cram1_tex_chain ties
# port B to 0).  Port A runs concurrently to thrash/evict.
cram1-cmap-chain: obj_dir_cram1_cmap_chain/Vtb_cram1_cmap_chain
	@printf "\n=== CRAM1 COLORMAP chain (tex_cache PORT B -> burst adapter -> cram1_controller+phy -> chip model) ===\n"
	./obj_dir_cram1_cmap_chain/Vtb_cram1_cmap_chain
obj_dir_cram1_cmap_chain/Vtb_cram1_cmap_chain: tb_cram1_cmap_chain.v $(TARGET_DIR)/gpu_cram1_tex_adapter.v $(TARGET_DIR)/cram1_controller.v $(TARGET_DIR)/cram1_phy.sv ../common/gpu_tex_cache.v cram_chip_model.v
	$(VERILATOR) $(TEX_CHAIN_VFLAGS) -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC --Mdir obj_dir_cram1_cmap_chain \
		--top-module tb_cram1_cmap_chain \
		tb_cram1_cmap_chain.v $(TARGET_DIR)/gpu_cram1_tex_adapter.v $(TARGET_DIR)/cram1_controller.v $(TARGET_DIR)/cram1_phy.sv ../common/gpu_tex_cache.v cram_chip_model.v

# --- CRAM1 colormap repro through the REAL gpu_core (NOT the tex_cache alone) ---
# Drives the REAL gpu_core's texture/colormap fills through the REAL CRAM1
# sync-burst chain (adapter + controller + phy + chip model) by wiring the
# core's cram0_tex_* redirect master to the chain and setting reg14 bit0=1.
# Renders a light-varying colormap wall twice (SDRAM route reg14=0 vs CRAM1
# route reg14=1) and reports whether CRAM1 yields a wrong/shifted palette.
# Built with the C++ harness (--cc --exe) so it shares tb_gpu's plumbing; the
# cram1 RTL is synthesizable (no --timing needed) and links straight in.
GPU_CRAM1_SRCS = \
	tb_gpu_cram1.v \
	altsyncram_stub.v \
	$(COMMON_DIR)/gpu_core.v \
	$(COMMON_DIR)/gpu_edge_walker.v \
	$(COMMON_DIR)/gpu_tex_cache.v \
	$(TARGET_DIR)/gpu_cram1_tex_adapter.v \
	$(TARGET_DIR)/cram1_controller.v \
	$(TARGET_DIR)/cram1_phy.sv \
	cram_chip_model.v
GPU_CRAM1_CPP = tb_gpu_cram1_main.cpp
GPU_CRAM1_DIR = obj_dir_gpu_cram1
gpu-cram1-repro: $(GPU_CRAM1_DIR)/Vtb_gpu
	@printf "\n=== GPU CRAM1 colormap (palookup) repro through the REAL gpu_core ===\n"
	./$(GPU_CRAM1_DIR)/Vtb_gpu

$(GPU_CRAM1_DIR)/Vtb_gpu: $(GPU_CRAM1_SRCS) $(GPU_CRAM1_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS_NOTRACE) -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC \
		-Wno-UNOPTFLAT -Wno-CASEINCOMPLETE \
		--top-module tb_gpu --Mdir $(GPU_CRAM1_DIR) \
		-I$(COMMON_DIR) \
		$(GPU_CRAM1_SRCS) $(GPU_CRAM1_CPP)

# --- AXI periph slave ---
axi-periph: $(AXI_PERIPH_DIR)/V$(AXI_PERIPH_TOP)
	@printf "\n=== AXI periph slave ===\n"
	./$(AXI_PERIPH_DIR)/V$(AXI_PERIPH_TOP)

# Full-feature config: the harness expects the analogizer SNAC path present
# and the LINK caps bit set, so define both INCLUDE_ macros (post-polarity-flip
# these gate the SNAC instances and FEAT_LINK).
$(AXI_PERIPH_DIR)/V$(AXI_PERIPH_TOP): $(AXI_PERIPH_SRCS) $(AXI_PERIPH_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) $(AXI_PERIPH_FLAGS) \
		+define+INCLUDE_ANALOGIZER +define+INCLUDE_LINK \
		--top-module $(AXI_PERIPH_TOP) --Mdir $(AXI_PERIPH_DIR) \
		-I$(COMMON_DIR) \
		$(AXI_PERIPH_SRCS) $(AXI_PERIPH_CPP)

# --- Pocket bridge command FSM ---
bridge-cmd: $(BRIDGE_CMD_DIR)/V$(BRIDGE_CMD_TOP)
	@printf "\n=== Pocket bridge command FSM ===\n"
	./$(BRIDGE_CMD_DIR)/V$(BRIDGE_CMD_TOP)

$(BRIDGE_CMD_DIR)/V$(BRIDGE_CMD_TOP): $(BRIDGE_CMD_SRCS) $(BRIDGE_CMD_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) $(BRIDGE_CMD_FLAGS) \
		--top-module $(BRIDGE_CMD_TOP) --Mdir $(BRIDGE_CMD_DIR) \
		-I$(COMMON_DIR) \
		$(BRIDGE_CMD_SRCS) $(BRIDGE_CMD_CPP)

# --- Pocket bridge RX CDC phase sweep ---
bridge-rx: $(BRIDGE_RX_DIR)/V$(BRIDGE_RX_TOP)
	@printf "\n=== Pocket bridge RX CDC (io_bridge_peripheral phase sweep) ===\n"
	./$(BRIDGE_RX_DIR)/V$(BRIDGE_RX_TOP)

$(BRIDGE_RX_DIR)/V$(BRIDGE_RX_TOP): $(BRIDGE_RX_SRCS) $(BRIDGE_RX_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) $(BRIDGE_RX_FLAGS) \
		--top-module $(BRIDGE_RX_TOP) --Mdir $(BRIDGE_RX_DIR) \
		-I$(COMMON_DIR) \
		$(BRIDGE_RX_SRCS) $(BRIDGE_RX_CPP)

# --- GPU chain (slices + cpu_target_port + axi_periph_slave) ---
gpu-chain: $(GPU_CHAIN_DIR)/V$(GPU_CHAIN_TOP)
	@printf "\n=== GPU chain (slice + target_port + slave) ===\n"
	./$(GPU_CHAIN_DIR)/V$(GPU_CHAIN_TOP)

$(GPU_CHAIN_DIR)/V$(GPU_CHAIN_TOP): $(GPU_CHAIN_SRCS) $(GPU_CHAIN_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) $(GPU_CHAIN_FLAGS) \
		--top-module $(GPU_CHAIN_TOP) --Mdir $(GPU_CHAIN_DIR) \
		-I$(COMMON_DIR) \
		$(GPU_CHAIN_SRCS) $(GPU_CHAIN_CPP)

# --- Audio pipeline ---
audio: $(AUDIO_DIR)/V$(AUDIO_TOP)
	@printf "\n=== Audio pipeline (audio_output + dcfifo stub) ===\n"
	./$(AUDIO_DIR)/V$(AUDIO_TOP)

$(AUDIO_DIR)/V$(AUDIO_TOP): $(AUDIO_SRCS) $(AUDIO_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) $(AUDIO_FLAGS) \
		--top-module $(AUDIO_TOP) --Mdir $(AUDIO_DIR) \
		-I$(COMMON_DIR) \
		$(AUDIO_SRCS) $(AUDIO_CPP)

# --- Audio mixer (audio_mixer.v v3) ---
audio-mixer: $(MIXER_DIR)/V$(MIXER_TOP)
	@printf "\n=== Audio mixer (audio_mixer.v v3) ===\n"
	./$(MIXER_DIR)/V$(MIXER_TOP)

$(MIXER_DIR)/V$(MIXER_TOP): $(MIXER_SRCS) $(MIXER_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) $(MIXER_FLAGS) \
		--top-module $(MIXER_TOP) --Mdir $(MIXER_DIR) \
		-I$(COMMON_DIR) \
		$(MIXER_SRCS) $(MIXER_CPP)

# --- AXI skid buffer (register slice primitive) ---
skid: $(SKID_DIR)/V$(SKID_TOP)
	@printf "\n=== AXI skid buffer (register slice) ===\n"
	./$(SKID_DIR)/V$(SKID_TOP)

# --- MiSTer direct-framebuffer pipeline (ddr3_fb + video_burst_arb) ---
ddr3-fb: $(DDR3FB_DIR)/V$(DDR3FB_TOP)
	@printf "\n=== MiSTer direct-FB pipeline (SDRAM->DDR3 frame DMA) ===\n"
	./$(DDR3FB_DIR)/V$(DDR3FB_TOP)

$(DDR3FB_DIR)/V$(DDR3FB_TOP): $(DDR3FB_SRCS) $(DDR3FB_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS_NOTRACE) $(DDR3FB_FLAGS) \
		--top-module $(DDR3FB_TOP) --Mdir $(DDR3FB_DIR) \
		-I$(COMMON_DIR) \
		$(DDR3FB_SRCS) $(DDR3FB_CPP)

$(SKID_DIR)/V$(SKID_TOP): $(SKID_SRCS) $(SKID_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) \
		--top-module $(SKID_TOP) --Mdir $(SKID_DIR) \
		-I$(COMMON_DIR) \
		$(SKID_SRCS) $(SKID_CPP)

# --- AXI SDRAM arbiter ---
arbiter: $(ARBITER_DIR)/V$(ARBITER_TOP)
	@printf "\n=== AXI SDRAM arbiter ===\n"
	./$(ARBITER_DIR)/V$(ARBITER_TOP)

$(ARBITER_DIR)/V$(ARBITER_TOP): $(ARBITER_SRCS) $(ARBITER_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) $(ARBITER_FLAGS) \
		--top-module $(ARBITER_TOP) --Mdir $(ARBITER_DIR) \
		-I$(COMMON_DIR) \
		$(ARBITER_SRCS) $(ARBITER_CPP)

# --- MiSTer hps_bridge ---
hps-bridge: $(HPSBRG_DIR)/V$(HPSBRG_TOP)
	@printf "\n=== MiSTer hps_bridge (boot ioctl + sector engine) ===\n"
	./$(HPSBRG_DIR)/V$(HPSBRG_TOP)

$(HPSBRG_DIR)/V$(HPSBRG_TOP): $(HPSBRG_SRCS) $(HPSBRG_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) $(HPSBRG_FLAGS) \
		--top-module $(HPSBRG_TOP) --Mdir $(HPSBRG_DIR) \
		-I$(COMMON_DIR) -I$(MISTER_DIR) \
		$(HPSBRG_SRCS) $(HPSBRG_CPP)

# --- MiSTer hps_mouse ---
hps-mouse: $(HPSMOUSE_DIR)/V$(HPSMOUSE_TOP)
	@printf "\n=== MiSTer hps_mouse (framework mouse -> input-hub slot 3) ===\n"
	./$(HPSMOUSE_DIR)/V$(HPSMOUSE_TOP)

$(HPSMOUSE_DIR)/V$(HPSMOUSE_TOP): $(HPSMOUSE_SRCS) $(HPSMOUSE_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) $(HPSMOUSE_FLAGS) \
		--top-module $(HPSMOUSE_TOP) --Mdir $(HPSMOUSE_DIR) \
		-I$(COMMON_DIR) -I$(MISTER_DIR) \
		$(HPSMOUSE_SRCS) $(HPSMOUSE_CPP)

# --- System bring-up ---
system: $(SYSTEM_DIR)/V$(SYSTEM_TOP)
	@printf "\n=== system bring-up (tb_system) ===\n"
	./$(SYSTEM_DIR)/V$(SYSTEM_TOP)

$(SYSTEM_DIR)/V$(SYSTEM_TOP): $(SYSTEM_SRCS) $(SYSTEM_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS_NOTRACE) $(SYSTEM_FLAGS) \
		--top-module $(SYSTEM_TOP) --Mdir $(SYSTEM_DIR) \
		-I$(COMMON_DIR) -I$(TARGET_DIR) -I. \
		$(SYSTEM_SRCS) $(SYSTEM_CPP)

# Same bring-up against the MiSTer dual-issue netlist. Requires
# TARGET=sim firmware in ../../firmware/os/ exactly like `make system`.
system-mister: $(SYSTEM_MISTER_DIR)/V$(SYSTEM_TOP)
	@printf "\n=== system bring-up (tb_system, dual-issue MiSTer CPU) ===\n"
	./$(SYSTEM_MISTER_DIR)/V$(SYSTEM_TOP)

$(SYSTEM_MISTER_DIR)/V$(SYSTEM_TOP): $(SYSTEM_SRCS_BASE) $(VEXII_MISTER) $(SYSTEM_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS_NOTRACE) $(SYSTEM_FLAGS) \
		-CFLAGS -DSYSTEM_NO_REGFILE_PEEK \
		--top-module $(SYSTEM_TOP) --Mdir $(SYSTEM_MISTER_DIR) \
		-I$(COMMON_DIR) -I$(TARGET_DIR) -I. \
		$(SYSTEM_SRCS_BASE) $(VEXII_MISTER) $(SYSTEM_CPP)

# --- Read-storm vs scanout phase hammer (mister io_sdram layer) -------
# Data-checked word-READ bursts colliding with scanout burst_rd at every
# phase alignment — the coverage hole behind the boot-lottery hunt.
SDRAM_RDSCAN_DIR = obj_dir_sdram_rdscan
SDRAM_RDSCAN_SRCS = \
	tb_sdram_rdscan.v \
	sdram_model_full.v \
	$(COMMON_DIR)/axi_sdram_slave.v \
	io_sdram_mister_test.v

sdram-rdscan: $(SDRAM_RDSCAN_DIR)/Vtb_sdram_rdscan
	./$(SDRAM_RDSCAN_DIR)/Vtb_sdram_rdscan

$(SDRAM_RDSCAN_DIR)/Vtb_sdram_rdscan: $(SDRAM_RDSCAN_SRCS) $(SDRAM_SYNCH) tb_sdram_rdscan_main.cpp
	$(VERILATOR) $(VERILATOR_FLAGS) $(SDRAM_FLAGS) \
		--top-module tb_sdram_rdscan --Mdir $(SDRAM_RDSCAN_DIR) \
		-I$(COMMON_DIR) -I. \
		$(SDRAM_RDSCAN_SRCS) $(SDRAM_SYNCH) tb_sdram_rdscan_main.cpp

# --- System bring-up over the REAL MiSTer SDRAM chain -----------------
# tb_system with sdram_fast_model swapped for io_sdram (mister copy) +
# sdram_model_full, plus scanout burst injection and a C++ R-beat data
# checker.  Boot-lottery repro vehicle: same TARGET=sim firmware as
# `make system`, MiSTer dual-issue netlist.
SYSTEM_SDRAM_DIR = obj_dir_system_sdram
SYSTEM_SDRAM_SRCS = \
	tb_system_sdram.v \
	bram_model.v \
	io_sdram_mister_test.v \
	sdram_model_full.v \
	cram0_sim_model.v \
	$(COMMON_DIR)/cpu_system.v \
	$(COMMON_DIR)/cpu_target_port.v \
	$(COMMON_DIR)/cpu_target_port_per.v \
	$(TARGET_DIR)/cram0_cdc.v \
	$(COMMON_DIR)/axi_register_slice.v \
	$(COMMON_DIR)/axi_sdram_arbiter.v \
	$(COMMON_DIR)/axi_sdram_slave.v \
	$(COMMON_DIR)/axi_periph_slave.v \
	$(COMMON_DIR)/sync_fifo.v \
	$(TARGET_DIR)/snac_shifter.v \
	$(COMMON_DIR)/gpu_core.v \
	$(COMMON_DIR)/gpu_edge_walker.v \
	$(COMMON_DIR)/gpu_tex_cache.v \
	$(TARGET_DIR)/apf/common.v

SYSTEM_SDRAM_CPP = tb_system_sdram_main.cpp

system-sdram: $(SYSTEM_SDRAM_DIR)/V$(SYSTEM_TOP)
	@printf "\n=== system bring-up (tb_system_sdram: real io_sdram + scanout) ===\n"
	./$(SYSTEM_SDRAM_DIR)/V$(SYSTEM_TOP)

$(SYSTEM_SDRAM_DIR)/V$(SYSTEM_TOP): $(SYSTEM_SDRAM_SRCS) $(VEXII_MISTER) $(SYSTEM_SDRAM_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS_NOTRACE) $(SYSTEM_FLAGS) \
		-CFLAGS -DSYSTEM_NO_REGFILE_PEEK \
		--top-module $(SYSTEM_TOP) --Mdir $(SYSTEM_SDRAM_DIR) \
		-I$(COMMON_DIR) -I$(TARGET_DIR) -I. \
		$(SYSTEM_SDRAM_SRCS) $(VEXII_MISTER) $(SYSTEM_SDRAM_CPP)

# --- Clean ---
clean:
	rm -rf $(SDRAM_DIR) $(SDRAM_SERIAL_DIR) $(SDRAM_NATIVE2_DIR) $(SRAM_DIR) $(GPU_DIR) $(GPU_FLOOR_DIR) \
	       $(GPU_GPUDEMO_DIR) $(GPU_ACCEPT_DIR) $(GPU_ACCEPT_SINGLE_DIR) \
	       $(GPU_ACCEPT_OS30_DIR) $(GPU_ACCEPT_ZRAW_DIR) $(SCANOUT_LEAN_DIR) \
	       $(AXI_PERIPH_DIR) \
	       $(AUDIO_DIR) $(MIXER_DIR) $(SYSTEM_DIR) \
	       $(ARBITER_DIR) \
	       obj_dir_gpu obj_dir_gpu_floor obj_dir_gpu_gpudemo \
	       obj_dir_gpu_acceptance \
	       obj_dir_cram1_tex_chain \
	       obj_dir_cdc io_sdram_test.v

# ============================================================
# gpu-transluc — MiSTer translucent-sprite span-dropout repro rig.
# Full-stack gpu_core WITH +define+INCLUDE_TRANSLUC (the acceptance-build
# mechanism) on the REAL MiSTer SDRAM stack (io_sdram_mister_test.v =
# shipping targets/mister/io_sdram.v, DQ-split), parameterized exactly like
# emu.sv.  Native 0x4C translucent column lists (Doom II fireball path)
# under scanout injection + M1/M2/M3 contention, verified byte-exact
# against a CPU reference; slave drop-arm / DMA-starvation / ring-desync
# taps counted per frame.  Additive, test-only.
#   ./obj_dir_gpu_transluc/Vtb_gpu_transluc [all|sanity|soak] [soak_frames]
# ============================================================
GPU_TRANSLUC_SRCS = \
	tb_gpu_transluc.v \
	sdram_model_full.v \
	altsyncram_stub.v \
	$(COMMON_DIR)/gpu_core.v \
	$(COMMON_DIR)/gpu_edge_walker.v \
	$(COMMON_DIR)/gpu_tex_cache.v \
	$(COMMON_DIR)/axi_sdram_arbiter.v \
	$(COMMON_DIR)/axi_sdram_slave.v \
	../targets/mister/synch_3.v \
	io_sdram_mister_test.v

GPU_TRANSLUC_CPP = tb_gpu_transluc_main.cpp
GPU_TRANSLUC_TOP = tb_gpu_transluc
GPU_TRANSLUC_DIR = obj_dir_gpu_transluc
GPU_TRANSLUC_FLAGS = -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC -Wno-CASEINCOMPLETE \
                   -Wno-SYNCASYNCNET -Wno-UNUSEDSIGNAL -Wno-PINMISSING \
                   -Wno-UNOPTFLAT -Wno-UNDRIVEN -Wno-UNUSEDPARAM \
                   -Wno-MULTIDRIVEN -Wno-LATCH -Wno-CASEOVERLAP

gpu-transluc: $(GPU_TRANSLUC_DIR)/V$(GPU_TRANSLUC_TOP)
	@printf "\n=== MiSTer translucent-column span-dropout repro (full SDRAM stack) ===\n"
	./$(GPU_TRANSLUC_DIR)/V$(GPU_TRANSLUC_TOP)

$(GPU_TRANSLUC_DIR)/V$(GPU_TRANSLUC_TOP): $(GPU_TRANSLUC_SRCS) $(GPU_TRANSLUC_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS_NOTRACE) $(GPU_TRANSLUC_FLAGS) \
		--top-module $(GPU_TRANSLUC_TOP) --Mdir $(GPU_TRANSLUC_DIR) \
		+define+INCLUDE_TRANSLUC \
		-I$(COMMON_DIR) \
		$(GPU_TRANSLUC_SRCS) $(GPU_TRANSLUC_CPP)
