# Verilator Test Suite for Memory Controllers
#
# `make all` (the default `make test` entry point) runs every test that
# currently passes:
#   sdram, sram, gpu, axi-periph, audio
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
	$(COMMON_DIR)/gpu_core.v \
	$(COMMON_DIR)/gpu_tex_cache.v

GPU_CPP = tb_gpu_main.cpp
GPU_TOP = tb_gpu
GPU_DIR = obj_dir_gpu

# ============================================================
# AXI periph slave — BRAM read bursts, peripheral reads/writes, GPU MMIO
# write hammer. Uses altsyncram_stub.v in place of the Cyclone V
# altsyncram primitive (1-cycle latency, matching M10K).
# ============================================================
AXI_PERIPH_SRCS = \
	tb_axi_periph.v \
	altsyncram_stub.v \
	$(COMMON_DIR)/axi_periph_slave.v \
	$(TARGET_DIR)/apf/common.v

AXI_PERIPH_CPP = tb_axi_periph_main.cpp
AXI_PERIPH_TOP = tb_axi_periph
AXI_PERIPH_DIR = obj_dir_axi_periph
AXI_PERIPH_FLAGS = -Wno-WIDTHEXPAND -Wno-CASEINCOMPLETE -Wno-SYNCASYNCNET

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
	$(COMMON_DIR)/axi_sdram_arbiter.v \
	$(COMMON_DIR)/axi_sdram_slave.v \
	$(COMMON_DIR)/axi_periph_slave.v \
	$(COMMON_DIR)/snac_shifter.v \
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
.PHONY: all sdram sram gpu gpu-floor gpu-gpudemo axi-periph \
        audio system run run-sram build clean

all: sdram sram gpu axi-periph audio

# --- SDRAM ---
sdram: $(SDRAM_DIR)/V$(SDRAM_TOP)
	./$(SDRAM_DIR)/V$(SDRAM_TOP)

$(SDRAM_DIR)/V$(SDRAM_TOP): $(SDRAM_SRCS) $(SDRAM_SYNCH) $(SDRAM_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) --top-module $(SDRAM_TOP) --Mdir $(SDRAM_DIR) \
		$(SDRAM_SRCS) $(SDRAM_SYNCH) $(SDRAM_CPP)

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

# --- GPU ---
# Single GPU build. Triangles + perspective spans + pipelined fragment
# processor are all unconditional in the hardware (see gpu_config.vh).
# The C++ test bench enables the triangle and persp test suites via
# GPU_FEAT_TRIANGLE and GPU_PERSP_IMPL; those defines steer test
# COMPILATION in tb_gpu_main.cpp, not RTL behaviour.
gpu: $(GPU_DIR)/V$(GPU_TOP)
	@printf "\n=== GPU tests ===\n"
	./$(GPU_DIR)/V$(GPU_TOP)

$(GPU_DIR)/V$(GPU_TOP): $(GPU_SRCS) $(GPU_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) --top-module $(GPU_TOP) --Mdir $(GPU_DIR) \
		+define+GPU_STATS +define+GPU_DEBUG \
		-CFLAGS -DGPU_PERSP_IMPL \
		-CFLAGS -DGPU_FEAT_TRIANGLE \
		-I$(COMMON_DIR) \
		$(GPU_SRCS) $(GPU_CPP)

# --- GPU floor-span coverage (BUILD/Duke3D hlineasm4 vs GPU multiply) ---
# Focused regression suite that drives CMD_DRAW_SPAN with BUILD-style
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
		+define+GPU_STATS +define+GPU_DEBUG \
		-CFLAGS -DGPU_PERSP_IMPL \
		-CFLAGS -DGPU_FEAT_TRIANGLE \
		-I$(COMMON_DIR) \
		$(GPU_SRCS) $(GPU_FLOOR_CPP)

# --- gpudemo reproducer (long-running multi-span + textured triangle) ---
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
		+define+GPU_STATS +define+GPU_DEBUG \
		-CFLAGS -DGPU_PERSP_IMPL \
		-CFLAGS -DGPU_FEAT_TRIANGLE \
		-I$(COMMON_DIR) \
		$(GPU_SRCS) $(GPU_GPUDEMO_CPP)

# --- AXI periph slave ---
axi-periph: $(AXI_PERIPH_DIR)/V$(AXI_PERIPH_TOP)
	@printf "\n=== AXI periph slave ===\n"
	./$(AXI_PERIPH_DIR)/V$(AXI_PERIPH_TOP)

$(AXI_PERIPH_DIR)/V$(AXI_PERIPH_TOP): $(AXI_PERIPH_SRCS) $(AXI_PERIPH_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) $(AXI_PERIPH_FLAGS) \
		--top-module $(AXI_PERIPH_TOP) --Mdir $(AXI_PERIPH_DIR) \
		-I$(COMMON_DIR) \
		$(AXI_PERIPH_SRCS) $(AXI_PERIPH_CPP)

# --- Audio pipeline ---
audio: $(AUDIO_DIR)/V$(AUDIO_TOP)
	@printf "\n=== Audio pipeline (audio_output + dcfifo stub) ===\n"
	./$(AUDIO_DIR)/V$(AUDIO_TOP)

$(AUDIO_DIR)/V$(AUDIO_TOP): $(AUDIO_SRCS) $(AUDIO_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) $(AUDIO_FLAGS) \
		--top-module $(AUDIO_TOP) --Mdir $(AUDIO_DIR) \
		-I$(COMMON_DIR) \
		$(AUDIO_SRCS) $(AUDIO_CPP)

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
	rm -rf $(SDRAM_DIR) $(SRAM_DIR) $(GPU_DIR) $(GPU_FLOOR_DIR) \
	       $(GPU_GPUDEMO_DIR) \
	       $(AXI_PERIPH_DIR) $(AUDIO_DIR) $(SYSTEM_DIR) \
	       obj_dir_gpu obj_dir_gpu_floor obj_dir_gpu_gpudemo \
	       obj_dir_cdc io_sdram_test.v
