# Verilator Test Suite for SDRAM Controller Stack

COMMON_DIR = ../common
TARGET_DIR = ../targets/pocket

VERILOG_SRCS = \
	tb_sdram.v \
	sdram_model.v \
	$(COMMON_DIR)/axi_sdram_slave.v \
	io_sdram_test.v

# Verilator needs synch_3 module (used by io_sdram)
SYNCH_SRC = $(TARGET_DIR)/apf/common.v

CPP_SRCS = tb_sdram_main.cpp

TOP = tb_sdram

VERILATOR = verilator
VERILATOR_FLAGS = --cc --exe --build \
	-Wall -Wno-fatal -Wno-BADVLTPRAGMA \
	--top-module $(TOP) \
	-CFLAGS "-std=c++17 -O2" \
	--trace

OBJ_DIR = obj_dir

.PHONY: all clean run

all: build

# Generate test variant of io_sdram with split DQ (no inout for Verilator)
io_sdram_test.v: $(TARGET_DIR)/io_sdram.v
	sed \
	  -e 's/inout   wire    \[15:0\]  phy_dq,/input   wire    [15:0]  phy_dq_in,\noutput  wire    [15:0]  phy_dq_out_port,\noutput  wire            phy_dq_oe_port,/' \
	  -e "s/assign          phy_dq = phy_dq_oe ? phy_dq_out : 16'bZZZZZZZZZZZZZZZZ;/assign phy_dq_out_port = phy_dq_out;\nassign phy_dq_oe_port = phy_dq_oe;/" \
	  -e 's/phy_dq_latched <= phy_dq;/phy_dq_latched <= phy_dq_in;/' \
	  $< > $@

build: $(OBJ_DIR)/V$(TOP)

$(OBJ_DIR)/V$(TOP): $(VERILOG_SRCS) $(SYNCH_SRC) $(CPP_SRCS)
	$(VERILATOR) $(VERILATOR_FLAGS) $(VERILOG_SRCS) $(SYNCH_SRC) $(CPP_SRCS)

run: build
	./$(OBJ_DIR)/V$(TOP)

clean:
	rm -rf $(OBJ_DIR)
