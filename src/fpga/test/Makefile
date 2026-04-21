# Verilator Test Suite for Memory Controllers
#
# `make all` (the default `make test` entry point) runs every test that
# currently passes:
#   sdram, sram, gpu (lite + full), audio
#
# Build-only / known-failing harnesses kept around for debugging:
#   axi-periph    builds; cacheline-burst tests fail (off-by-one)
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
# Separate Verilator output dirs for the Lite and Full GPU test binaries.
# Both build from the same gpu_core.v sources but with different
# +define+GPU_VARIANT_* macros, exercising both ifdef paths.
GPU_LITE_DIR = obj_dir_gpu_lite
GPU_FULL_DIR = obj_dir_gpu_full

# ============================================================
# AXI periph (build-only — cacheline burst returns +1 off-by-one)
# Uses altsyncram_stub.v in place of the Cyclone V altsyncram primitive.
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
# Targets
# ============================================================
.PHONY: all sdram sram gpu gpu-lite gpu-full axi-periph \
        audio run run-sram build clean

# `all` runs every passing test. Build-only debug harnesses
# (axi-periph) are intentionally excluded.
all: sdram sram gpu audio

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
# Two binaries: gpu-lite (default Pocket variant, span renderer only) and
# gpu-full (everything, including triangle rasterizer). Both share the same
# Verilog sources and same C++ test bench; ifdef'd code in gpu_core.v and
# tb_gpu_main.cpp selects which features compile in.
#
# `make gpu` runs both, in order. Lite first so a Lite-only failure shows
# clearly without the longer Full output flooding the terminal.
gpu: gpu-lite gpu-full

gpu-lite: $(GPU_LITE_DIR)/V$(GPU_TOP)
	@printf "\n=== GPU LITE variant ===\n"
	./$(GPU_LITE_DIR)/V$(GPU_TOP)

$(GPU_LITE_DIR)/V$(GPU_TOP): $(GPU_SRCS) $(GPU_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) --top-module $(GPU_TOP) --Mdir $(GPU_LITE_DIR) \
		+define+GPU_VARIANT_LITE \
		-CFLAGS -DGPU_PERSP_IMPL \
		-I$(COMMON_DIR) \
		$(GPU_SRCS) $(GPU_CPP)

gpu-full: $(GPU_FULL_DIR)/V$(GPU_TOP)
	@printf "\n=== GPU FULL variant ===\n"
	./$(GPU_FULL_DIR)/V$(GPU_TOP)

$(GPU_FULL_DIR)/V$(GPU_TOP): $(GPU_SRCS) $(GPU_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) --top-module $(GPU_TOP) --Mdir $(GPU_FULL_DIR) \
		+define+GPU_VARIANT_FULL \
		-CFLAGS -DGPU_FEAT_TRIANGLE \
		-I$(COMMON_DIR) \
		$(GPU_SRCS) $(GPU_CPP)

# --- AXI periph (build-only debug; tests currently fail) ---
axi-periph: $(AXI_PERIPH_DIR)/V$(AXI_PERIPH_TOP)
	@printf "\n=== AXI periph (debug — known failures) ===\n"
	./$(AXI_PERIPH_DIR)/V$(AXI_PERIPH_TOP) || true

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

# --- Clean ---
clean:
	rm -rf $(SDRAM_DIR) $(SRAM_DIR) $(GPU_LITE_DIR) $(GPU_FULL_DIR) \
	       $(AXI_PERIPH_DIR) $(AUDIO_DIR) \
	       obj_dir_gpu obj_dir_cdc io_sdram_test.v
