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
	$(COMMON_DIR)/axi_psram_slave.v

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
	$(COMMON_DIR)/sram_controller.v \
	psram_controller_test.v \
	psram_test.sv

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

psram_controller_test.v: $(COMMON_DIR)/psram_controller.v
	sed \
	  -e 's/^module psram_controller/module psram_controller_test/' \
	  -e 's/inout  wire \[15:0\]  cram_dq,/input  wire [15:0]  cram_dq_in,\n    output wire [15:0]  cram_dq_out,\n    output wire         cram_dq_oe,/' \
	  -e 's/\.cram_dq(cram_dq),/.cram_dq_in(cram_dq_in),\n    .cram_dq_out(cram_dq_out),\n    .cram_dq_oe(cram_dq_oe),/' \
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
# Targets
# ============================================================
.PHONY: all sdram psram psram-rtl sram run run-psram run-sram build clean

all: sdram psram psram-rtl sram

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

# --- Clean ---
clean:
	rm -rf $(SDRAM_DIR) $(PSRAM_DIR) $(PSRAM_RTL_DIR) $(SRAM_DIR) io_sdram_test.v psram_test.sv psram_controller_test.v
