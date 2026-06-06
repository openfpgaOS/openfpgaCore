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
SYSTEM_SRCS = \
	tb_system.v \
	bram_model.v \
	sdram_fast_model.v \
	cram0_sim_model.v \
	$(COMMON_DIR)/cpu_system.v \
	$(COMMON_DIR)/cpu_target_port.v \
	$(COMMON_DIR)/cram0_cdc.v \
	$(COMMON_DIR)/axi_register_slice.v \
	$(COMMON_DIR)/axi_sdram_arbiter.v \
	$(COMMON_DIR)/axi_sdram_slave.v \
	$(COMMON_DIR)/axi_periph_slave.v \
	$(COMMON_DIR)/sync_fifo.v \
	$(COMMON_DIR)/snac_shifter.v \
	$(COMMON_DIR)/snac_psx_poller.v \
	$(COMMON_DIR)/gpu_core.v \
	$(COMMON_DIR)/gpu_tex_cache.v \
	../vendor/vexriscv/VexiiRiscv/VexiiRiscv.v \
	$(TARGET_DIR)/apf/common.v

SYSTEM_CPP = tb_system_main.cpp
SYSTEM_TOP = tb_system
SYSTEM_DIR = obj_dir_system
SYSTEM_FLAGS = -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC -Wno-CASEINCOMPLETE \
               -Wno-SYNCASYNCNET -Wno-UNOPTFLAT -Wno-STMTDLY -Wno-PINMISSING \
               -Wno-UNUSEDSIGNAL -Wno-UNDRIVEN -Wno-UNUSEDPARAM \
               -Wno-CASEOVERLAP -Wno-SELRANGE -Wno-WIDTHCONCAT \
               -Wno-MULTIDRIVEN -Wno-LATCH -Wno-IMPLICIT -Wno-ASCRANGE

# ============================================================
# Targets
# ============================================================
.PHONY: all sdram sdram-serialize sdram-native2 sdram-all sram scanout analogchain gpu gpu-persp gpu-floor gpu-gpudemo gpu-acceptance gpu-acceptance-single gpu-acceptance-burst2 gpu-acceptance-all gpu-chain \
        axi-periph bridge-cmd audio audio-mixer arbiter system run run-sram build clean

all: sdram-all sram gpu gpu-persp axi-periph bridge-cmd audio audio-mixer arbiter hps-bridge

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

sdram-all: sdram sdram-serialize sdram-native2

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

scanout: $(SCANOUT_DIR)/V$(SCANOUT_TOP)
	@printf "\n=== Scanout analog-timing test ===\n"
	./$(SCANOUT_DIR)/V$(SCANOUT_TOP)

$(SCANOUT_DIR)/V$(SCANOUT_TOP): $(SCANOUT_SRCS) $(SCANOUT_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS_NOTRACE) --top-module $(SCANOUT_TOP) --Mdir $(SCANOUT_DIR) \
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

gpu-acceptance-all: gpu-acceptance gpu-acceptance-single

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

# --- Clean ---
clean:
	rm -rf $(SDRAM_DIR) $(SDRAM_SERIAL_DIR) $(SDRAM_NATIVE2_DIR) $(SRAM_DIR) $(GPU_DIR) $(GPU_FLOOR_DIR) \
	       $(GPU_GPUDEMO_DIR) $(GPU_ACCEPT_DIR) $(GPU_ACCEPT_SINGLE_DIR) \
	       $(AXI_PERIPH_DIR) \
	       $(AUDIO_DIR) $(MIXER_DIR) $(SYSTEM_DIR) \
	       $(ARBITER_DIR) \
	       obj_dir_gpu obj_dir_gpu_floor obj_dir_gpu_gpudemo \
	       obj_dir_gpu_acceptance \
	       obj_dir_cdc io_sdram_test.v
