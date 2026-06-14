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
	--trace

# Trace-free flags used by the heavyweight system tb (full CPU) — VCD
# tracing is the dominant sim cost; leave it off so boot completes in
# a few seconds instead of minutes.
VERILATOR_FLAGS_NOTRACE = --cc --exe --build \
	-Wall -Wno-fatal -Wno-BADVLTPRAGMA \
	-CFLAGS "-std=c++17 -O2"

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

# ============================================================
# SRAM
# ============================================================
SRAM_SRCS = \
	tb_sram.v \
	sram_chip_model.v \
	$(COMMON_DIR)/sram_controller.v

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
	$(COMMON_DIR)/snac_shifter.v \
	$(COMMON_DIR)/snac_psx_poller.v \
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
	$(COMMON_DIR)/lsu_axi_shim.v \
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
	$(COMMON_DIR)/audio_output.v

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
# Full system bring-up — CPU + SDRAM + CRAM0 CDC + BRAM
# ============================================================
# Boots the VexiiRiscv through the v2 fabric, loads os.bin into the
# simulated CRAM0, and prints anything the kernel pushes out UART.
# Requires firmware.mif + os.bin to be present in ../../firmware/os/.
# The CPU netlist is per-target since the MiSTer went dual-issue:
# VexiiRiscv.v = Pocket single-issue, VexiiRiscv_mister.v = MiSTer
# --decoders=2 --lanes=2 (same module name and AXI interface, so the
# surrounding fabric sources are identical).  `make system` boots the
# Pocket netlist, `make system-mister` the dual-issue one.
VEXII_POCKET = ../vendor/vexriscv/VexiiRiscv/VexiiRiscv.v
VEXII_MISTER = ../vendor/vexriscv/VexiiRiscv/VexiiRiscv_mister.v

SYSTEM_SRCS_BASE = \
	tb_system.v \
	bram_model.v \
	sdram_fast_model.v \
	cram0_sim_model.v \
	$(COMMON_DIR)/cpu_system.v \
	$(COMMON_DIR)/cpu_target_port.v \
	$(COMMON_DIR)/cpu_target_port_per.v \
	$(COMMON_DIR)/cram0_cdc.v \
	$(COMMON_DIR)/axi_register_slice.v \
	$(COMMON_DIR)/axi_sdram_arbiter.v \
	$(COMMON_DIR)/axi_sdram_slave.v \
	$(COMMON_DIR)/axi_periph_slave.v \
	$(COMMON_DIR)/sync_fifo.v \
	$(COMMON_DIR)/snac_shifter.v \
	$(COMMON_DIR)/snac_psx_poller.v \
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
.PHONY: all sdram sdram-serialize sdram-native2 sdram-wcont0 sdram-all sdram-cont sram scanout scanout-lean analogchain gpu gpu-persp gpu-floor gpu-gpudemo gpu-acceptance gpu-acceptance-single gpu-acceptance-os30 gpu-acceptance-burst2 gpu-acceptance-all gpu-chain \
        gpu-wrpath gpu-wstall scanout-race scanout-race-pocket axi-periph bridge-cmd bridge-rx audio audio-mixer arbiter system system-mister run run-sram build clean

all: sdram-all sram gpu gpu-persp axi-periph bridge-cmd bridge-rx audio audio-mixer arbiter hps-bridge

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
	$(TARGET_DIR)/analogizer/openFPGA_Pocket_Analogizer.v \
	scandoubler_test.v
ANALOGCHAIN_CPP = tb_analogizer_chain_main.cpp
ANALOGCHAIN_TOP = tb_analogizer_chain
ANALOGCHAIN_DIR = obj_dir_analogchain

# Verilator chokes on scandoubler.v's zero-width part-select [..-:n] in its
# DEAD (n=0, since COLOR_DEPTH==OUT_COLOR_DEPTH) color-expand branch; Quartus
# prunes it. Patch -:n -> -:1 in that unreachable branch for sim only.
scandoubler_test.v: $(TARGET_DIR)/analogizer/scandoubler.v
	sed 's/ -:n\]/ -:1]/g' $< > $@

analogchain: $(ANALOGCHAIN_DIR)/V$(ANALOGCHAIN_TOP)
	@printf "\n=== Analogizer cart-pin chain test ===\n"
	./$(ANALOGCHAIN_DIR)/V$(ANALOGCHAIN_TOP)

$(ANALOGCHAIN_DIR)/V$(ANALOGCHAIN_TOP): $(ANALOGCHAIN_SRCS) $(ANALOGCHAIN_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS_NOTRACE) --top-module $(ANALOGCHAIN_TOP) --Mdir $(ANALOGCHAIN_DIR) \
		-I$(COMMON_DIR) -I$(TARGET_DIR)/analogizer \
		$(ANALOGCHAIN_SRCS) $(ANALOGCHAIN_CPP)

# --- GPU ---
# Single GPU build. Triangles + perspective spans + pipelined fragment
# processor are all unconditional in the hardware.
gpu: $(GPU_DIR)/V$(GPU_TOP)
	@printf "\n=== GPU tests ===\n"
	./$(GPU_DIR)/V$(GPU_TOP)

$(GPU_DIR)/V$(GPU_TOP): $(GPU_SRCS) $(GPU_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) --top-module $(GPU_TOP) --Mdir $(GPU_DIR) \
		-I$(COMMON_DIR) \
		$(GPU_SRCS) $(GPU_CPP)

gpu-persp: $(GPU_PERSP_DIR)/V$(GPU_TOP)
	@printf "\n=== GPU perspective-span tests ===\n"
	./$(GPU_PERSP_DIR)/V$(GPU_TOP)

$(GPU_PERSP_DIR)/V$(GPU_TOP): $(GPU_SRCS) $(GPU_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) \
		-CFLAGS "-std=c++17 -O2 -DGPU_TEST_ENABLE_PERSP=1" \
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
gpu-acceptance: $(GPU_ACCEPT_DIR)/V$(GPU_TOP)
	@printf "\n=== GPU acceptance suite ===\n"
	./$(GPU_ACCEPT_DIR)/V$(GPU_TOP)

$(GPU_ACCEPT_DIR)/V$(GPU_TOP): $(GPU_SRCS) $(GPU_ACCEPT_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) --top-module $(GPU_TOP) \
		--Mdir $(GPU_ACCEPT_DIR) \
		-I$(COMMON_DIR) \
		$(GPU_SRCS) $(GPU_ACCEPT_CPP)

gpu-acceptance-single: $(GPU_ACCEPT_SINGLE_DIR)/V$(GPU_TOP)
	@printf "\n=== GPU acceptance suite (single-beat FB writes) ===\n"
	./$(GPU_ACCEPT_SINGLE_DIR)/V$(GPU_TOP)

$(GPU_ACCEPT_SINGLE_DIR)/V$(GPU_TOP): $(GPU_SRCS) $(GPU_ACCEPT_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) \
		--top-module $(GPU_TOP) \
		--Mdir $(GPU_ACCEPT_SINGLE_DIR) \
		-I$(COMMON_DIR) \
		$(GPU_SRCS) $(GPU_ACCEPT_CPP)

gpu-acceptance-burst2: gpu-acceptance

# OS30 lean GPU variant (Pocket Quake/Quake2 build): compact-direct 0x48
# (GPU_HAS_COMPACT_SPAN=0) and 0x4C column lists (GPU_HAS_COLUMN_LIST=0)
# drain as no-ops, the translucency LUT is chokepointed out
# (+define+EXCLUDE_TRANSLUC), and the edge walker runs the serial divider
# (GPU_EW_PARALLEL_DIVS=0).  0x4A/0x4B vert-tri, 0x4D recs-tri, long-form
# 0x48 and the z window all stay ENABLED.  -DGPU_TEST_OS30_LEAN compiles
# out the harness tests whose vehicle is the removed compact/column/
# transluc paths and switches the lean negative tests' expectations.
gpu-acceptance-os30: $(GPU_ACCEPT_OS30_DIR)/V$(GPU_TOP)
	@printf "\n=== GPU acceptance suite (OS30 lean variant) ===\n"
	./$(GPU_ACCEPT_OS30_DIR)/V$(GPU_TOP)

$(GPU_ACCEPT_OS30_DIR)/V$(GPU_TOP): $(GPU_SRCS) $(GPU_ACCEPT_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) \
		-CFLAGS "-std=c++17 -O2 -DGPU_TEST_OS30_LEAN" \
		+define+EXCLUDE_TRANSLUC \
		-GGPU_HAS_COMPACT_SPAN=0 \
		-GGPU_HAS_COLUMN_LIST=0 \
		-GGPU_EW_PARALLEL_DIVS=0 \
		-GGPU_HAS_VERT_TRI=1 \
		-GGPU_HAS_PARAM_TRI_RECS=1 \
		-GGPU_Z_READ_WINDOW=4 \
		--top-module $(GPU_TOP) \
		--Mdir $(GPU_ACCEPT_OS30_DIR) \
		-I$(COMMON_DIR) \
		$(GPU_SRCS) $(GPU_ACCEPT_CPP)

gpu-acceptance-all: gpu-acceptance gpu-acceptance-single gpu-acceptance-os30

# --- AXI periph slave ---
axi-periph: $(AXI_PERIPH_DIR)/V$(AXI_PERIPH_TOP)
	@printf "\n=== AXI periph slave ===\n"
	./$(AXI_PERIPH_DIR)/V$(AXI_PERIPH_TOP)

$(AXI_PERIPH_DIR)/V$(AXI_PERIPH_TOP): $(AXI_PERIPH_SRCS) $(AXI_PERIPH_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) $(AXI_PERIPH_FLAGS) \
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

# --- Clean ---
clean:
	rm -rf $(SDRAM_DIR) $(SDRAM_SERIAL_DIR) $(SDRAM_NATIVE2_DIR) $(SRAM_DIR) $(GPU_DIR) $(GPU_FLOOR_DIR) \
	       $(GPU_GPUDEMO_DIR) $(GPU_ACCEPT_DIR) $(GPU_ACCEPT_SINGLE_DIR) \
	       $(GPU_ACCEPT_OS30_DIR) $(SCANOUT_LEAN_DIR) \
	       $(AXI_PERIPH_DIR) \
	       $(AUDIO_DIR) $(MIXER_DIR) $(SYSTEM_DIR) \
	       $(ARBITER_DIR) \
	       obj_dir_gpu obj_dir_gpu_floor obj_dir_gpu_gpudemo \
	       obj_dir_gpu_acceptance \
	       obj_dir_cdc io_sdram_test.v
