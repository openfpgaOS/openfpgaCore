# Verilator Test Suite for Memory Controllers
#
# Targets: sdram, psram, sram (or 'all' for everything)

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
# PSRAM
# ============================================================
PSRAM_SRCS = \
	tb_psram.v \
	psram_backend_model.v \
	$(COMMON_DIR)/axi_psram_slave.v \
	$(COMMON_DIR)/axi_cram0_slave.v \
	$(COMMON_DIR)/axi_cram1_slave.v \
	$(COMMON_DIR)/axi_sram_slave.v

PSRAM_CPP = tb_psram_main.cpp
PSRAM_TOP = tb_psram
PSRAM_DIR = obj_dir_psram

# ============================================================
# PSRAM Full-RTL (real controllers + behavioral chip models)
# ============================================================
PSRAM_RTL_SRCS = \
	tb_psram_full.v \
	cram_chip_model.v \
	sram_chip_model.v \
	async_fifo.v \
	$(COMMON_DIR)/axi_psram_slave.v \
	$(COMMON_DIR)/axi_cram0_slave.v \
	$(COMMON_DIR)/axi_cram1_slave.v \
	$(COMMON_DIR)/axi_sram_slave.v \
	$(COMMON_DIR)/sram_controller.v \
	psram_controller_test.v \
	psram_test.sv \
	psram_cram0_drv_test.sv

PSRAM_RTL_CPP = tb_psram_full_main.cpp
PSRAM_RTL_TOP = tb_psram_full
PSRAM_RTL_DIR = obj_dir_psram_rtl

# Generate test variants with split DQ (no inout for Verilator)
psram_test.sv: $(COMMON_DIR)/psram.sv
	sed \
	  -e 's/inout wire \[15:0\] cram_dq,/input  wire [15:0] cram_dq_in,\n    output wire [15:0] cram_dq_out,\n    output wire        cram_dq_oe,/' \
	  -e "s/assign cram_dq = data_out_en ? cram_data : 16'hZZ;/assign cram_dq_out = cram_data;\n  assign cram_dq_oe = data_out_en;/" \
	  -e 's/cram_dq_r <= cram_dq;/cram_dq_r <= cram_dq_in;/' \
	  $< > $@

# Same transform on the CRAM0-only fork (psram_cram0_drv.sv) — needed
# because psram_controller_test.v (a CRAM0 wrapper) now instantiates
# `psram_cram0_drv` instead of `psram` after the driver-fork refactor.
psram_cram0_drv_test.sv: $(COMMON_DIR)/psram_cram0_drv.sv
	sed \
	  -e 's/inout wire \[15:0\] cram_dq,/input  wire [15:0] cram_dq_in,\n    output wire [15:0] cram_dq_out,\n    output wire        cram_dq_oe,/' \
	  -e "s/assign cram_dq = data_out_en ? cram_data : 16'hZZ;/assign cram_dq_out = cram_data;\n  assign cram_dq_oe = data_out_en;/" \
	  -e 's/cram_dq_r <= cram_dq;/cram_dq_r <= cram_dq_in;/' \
	  $< > $@

psram_controller_test.v: $(COMMON_DIR)/psram_controller.v
	sed \
	  -e 's/^module psram_controller/module psram_controller_test/' \
	  -e 's/inout  wire \[15:0\]  cram_dq,/input  wire [15:0]  cram_dq_in,\n    output wire [15:0]  cram_dq_out,\n    output wire         cram_dq_oe,/' \
	  -e 's/\.cram_dq(cram_dq),/.cram_dq_in(cram_dq_in),\n    .cram_dq_out(cram_dq_out),\n    .cram_dq_oe(cram_dq_oe),/' \
	  $< > $@

# CRAM0 wrapper test variant — same DQ-split transform as the
# controller, plus a module rename so both psram_cram0 and the test
# variant can coexist if a future test ever needs both. Used by
# tb_psram_cram0 to validate item 1B / 1A / 1E edits to psram_cram0.v
# before the FPGA build.
psram_cram0_test.v: $(COMMON_DIR)/psram_cram0.v
	sed \
	  -e 's/^module psram_cram0/module psram_cram0_test/' \
	  -e 's/inout  wire \[15:0\]  cram_dq,/input  wire [15:0]  cram_dq_in,\n    output wire [15:0]  cram_dq_out,\n    output wire         cram_dq_oe,/' \
	  -e 's/\.cram_dq(cram_dq),/.cram_dq_in(cram_dq_in),\n    .cram_dq_out(cram_dq_out),\n    .cram_dq_oe(cram_dq_oe),/' \
	  $< > $@

# ============================================================
# PSRAM CRAM0 wrapper (real psram_cram0 + real driver + cram_chip_model)
# Drives the word-level interface that the CPU uses on the FPGA.
# ============================================================
PSRAM_CRAM0_SRCS = \
	tb_psram_cram0.v \
	cram_chip_model.v \
	psram_cram0_test.v \
	psram_cram0_drv_test.sv

PSRAM_CRAM0_CPP = tb_psram_cram0_main.cpp
PSRAM_CRAM0_TOP = tb_psram_cram0
PSRAM_CRAM0_DIR = obj_dir_psram_cram0

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
# CRAM1 CDC (bridge collision test)
# ============================================================
CDC_SRCS = \
	tb_cram1_cdc.v \
	psram_word_model.v \
	$(COMMON_DIR)/cpu_psram1_cdc.v

CDC_CPP = tb_cram1_cdc_main.cpp
CDC_TOP = tb_cram1_cdc
CDC_DIR = obj_dir_cdc

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
# Targets
# ============================================================
.PHONY: all sdram psram psram-rtl psram-cram0 sram cdc gpu gpu-lite gpu-full run run-psram run-sram build clean

all: sdram psram psram-cram0 sram
# psram-rtl disabled: requires burst write ports not in current RTL

# --- SDRAM ---
sdram: $(SDRAM_DIR)/V$(SDRAM_TOP)
	./$(SDRAM_DIR)/V$(SDRAM_TOP)

$(SDRAM_DIR)/V$(SDRAM_TOP): $(SDRAM_SRCS) $(SDRAM_SYNCH) $(SDRAM_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) --top-module $(SDRAM_TOP) --Mdir $(SDRAM_DIR) \
		$(SDRAM_SRCS) $(SDRAM_SYNCH) $(SDRAM_CPP)

# Backward compat aliases
build: $(SDRAM_DIR)/V$(SDRAM_TOP)
run: sdram

# --- PSRAM ---
psram: $(PSRAM_DIR)/V$(PSRAM_TOP)
	./$(PSRAM_DIR)/V$(PSRAM_TOP)

$(PSRAM_DIR)/V$(PSRAM_TOP): $(PSRAM_SRCS) $(PSRAM_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) --top-module $(PSRAM_TOP) --Mdir $(PSRAM_DIR) \
		$(PSRAM_SRCS) $(PSRAM_CPP)

run-psram: psram

# --- PSRAM CRAM0 wrapper ---
psram-cram0: $(PSRAM_CRAM0_DIR)/V$(PSRAM_CRAM0_TOP)
	./$(PSRAM_CRAM0_DIR)/V$(PSRAM_CRAM0_TOP)

$(PSRAM_CRAM0_DIR)/V$(PSRAM_CRAM0_TOP): $(PSRAM_CRAM0_SRCS) $(PSRAM_CRAM0_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) --top-module $(PSRAM_CRAM0_TOP) --Mdir $(PSRAM_CRAM0_DIR) \
		$(PSRAM_CRAM0_SRCS) $(PSRAM_CRAM0_CPP)

# --- PSRAM Full-RTL ---
psram-rtl: $(PSRAM_RTL_DIR)/V$(PSRAM_RTL_TOP)
	./$(PSRAM_RTL_DIR)/V$(PSRAM_RTL_TOP)

$(PSRAM_RTL_DIR)/V$(PSRAM_RTL_TOP): $(PSRAM_RTL_SRCS) $(PSRAM_RTL_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) --top-module $(PSRAM_RTL_TOP) --Mdir $(PSRAM_RTL_DIR) \
		$(PSRAM_RTL_SRCS) $(PSRAM_RTL_CPP)

# --- SRAM ---
sram: $(SRAM_DIR)/V$(SRAM_TOP)
	./$(SRAM_DIR)/V$(SRAM_TOP)

$(SRAM_DIR)/V$(SRAM_TOP): $(SRAM_SRCS) $(SRAM_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) --top-module $(SRAM_TOP) --Mdir $(SRAM_DIR) \
		$(SRAM_SRCS) $(SRAM_CPP)

run-sram: sram

# --- CRAM1 CDC ---
cdc: $(CDC_DIR)/V$(CDC_TOP)
	./$(CDC_DIR)/V$(CDC_TOP)

$(CDC_DIR)/V$(CDC_TOP): $(CDC_SRCS) $(CDC_CPP)
	$(VERILATOR) $(VERILATOR_FLAGS) --timing --top-module $(CDC_TOP) --Mdir $(CDC_DIR) \
		$(CDC_SRCS) $(CDC_CPP)

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

# --- Clean ---
clean:
	rm -rf $(SDRAM_DIR) $(PSRAM_DIR) $(PSRAM_RTL_DIR) $(PSRAM_CRAM0_DIR) $(SRAM_DIR) $(CDC_DIR) $(GPU_LITE_DIR) $(GPU_FULL_DIR) obj_dir_gpu io_sdram_test.v psram_test.sv psram_cram0_drv_test.sv psram_controller_test.v psram_cram0_test.v
