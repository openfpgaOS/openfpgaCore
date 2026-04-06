# openfpgaOS — Analogue Pocket Build System
#
# Quick start:
#   make          Full build: cpu → firmware → compile → test → deploy
#   make flash    Quick: firmware → patch bitstream → deploy (~10 sec)
#   make help     Show all targets

# ── Configuration ────────────────────────────────────────────────────
PROJECT    = ap_core
TARGET     = pocket
FW_DIR     = ../../../firmware/os
VEXII_DIR  = ../../vendor/vexriscv/VexiiRiscv
VEXII_OUT  = ../../vendor/vexriscv/VexiiRiscv_Full.v
TEST_DIR   = ../../test
ROOT_DIR   = ../../../..
RELEASE_DIR = $(ROOT_DIR)/build/Cores/ThinkElastic.openfpgaOS
ASSETS_DIR  = $(ROOT_DIR)/build/Assets/openfpgaos/common
TOOLS_DIR   = $(ROOT_DIR)/tools
REVERSE_BITS = $(TOOLS_DIR)/reverse_bits

QUARTUS    = /home/alberto/altera_lite/25.1std/quartus/bin
export PATH := $(QUARTUS):$(PATH)

# ── Colors ───────────────────────────────────────────────────────────
ifneq ($(shell tput colors 2>/dev/null),)
C_LOGO  := \033[96m
C_HEAD  := \033[1m
C_CMD   := \033[93m
C_OK    := \033[32m
C_WARN  := \033[33m
C_ERR   := \033[31m
C_DIM   := \033[2m
C_RESET := \033[0m
else
C_LOGO  :=
C_HEAD  :=
C_CMD   :=
C_OK    :=
C_WARN  :=
C_ERR   :=
C_DIM   :=
C_RESET :=
endif

# ── Default ──────────────────────────────────────────────────────────
all: help

# ── Full build ───────────────────────────────────────────────────────
full: cpu firmware compile test deploy summary

# ── Help ─────────────────────────────────────────────────────────────
help:
	@printf "$(C_LOGO)"
	@echo "  openfpgaOS — $(TARGET)"
	@printf "$(C_RESET)\n"
	@printf "  $(C_HEAD)Full build:$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)full$(C_RESET)             cpu → firmware → compile → test → deploy   $(C_DIM)(~9 min)$(C_RESET)\n"
	@echo ""
	@printf "  $(C_HEAD)Incremental:$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)flash$(C_RESET)            Firmware → patch bitstream → deploy        $(C_DIM)(~10 sec)$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)build$(C_RESET)            Quartus incremental compile                $(C_DIM)(~7 min)$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)firmware$(C_RESET)         Rebuild bootloader + os.bin                $(C_DIM)(~5 sec)$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)cpu$(C_RESET)              Regenerate VexiiRiscv from SpinalHDL       $(C_DIM)(~15 sec)$(C_RESET)\n"
	@echo ""
	@printf "  $(C_HEAD)Verification:$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)test$(C_RESET)             Run Verilator test suite                   $(C_DIM)(~30 sec)$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)check$(C_RESET)            RTL check (Analysis & Synthesis)           $(C_DIM)(~45 sec)$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)timing$(C_RESET)           Show Fmax and slack from last build\n"
	@echo ""
	@printf "  $(C_HEAD)Deployment:$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)deploy$(C_RESET)           Copy bitstream + os.bin to build/\n"
	@printf "    $(C_CMD)make $(C_CMD)program$(C_RESET)          JTAG flash via USB Blaster (dev)\n"
	@echo ""
	@printf "  $(C_HEAD)Seed sweep:$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)sweep$(C_RESET)            Try seeds 1-30, report best Fmax           $(C_DIM)(~7 min/seed)$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)sweep-results$(C_RESET)    Show results from previous sweep\n"
	@printf "    $(C_CMD)make $(C_CMD)sweep-best$(C_RESET)       Rebuild with the best seed\n"
	@echo ""
	@printf "  $(C_HEAD)Cleanup:$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)clean$(C_RESET)            Remove all build artifacts\n"
	@echo ""

# ── CPU generation ───────────────────────────────────────────────────
cpu:
	@printf "$(C_HEAD)[cpu]$(C_RESET) Generating VexiiRiscv...\n"
	@cd $(VEXII_DIR) && bash ../generate_vexii.sh
	@cp $(VEXII_DIR)/VexiiRiscv.v $(VEXII_OUT)
	@printf "$(C_OK)[cpu] Done$(C_RESET)\n"

# ── Firmware ─────────────────────────────────────────────────────────
firmware:
	@printf "$(C_HEAD)[fw]$(C_RESET) Building firmware...\n"
	@$(MAKE) -C $(FW_DIR) clean all install
	@printf "$(C_OK)[fw] Done$(C_RESET)\n"

# ── FPGA compile ─────────────────────────────────────────────────────

# Variant defines — set via VARIANT_DEFS for core variant builds.
# Example: VARIANT_DEFS="EXCLUDE_OPL3 EXCLUDE_LINK" makes a lite build.
VARIANT_DEFS ?=
VERILOG_MACROS = $(foreach d,$(VARIANT_DEFS),--verilog_macro="$(d)")

compile:
	@printf "$(C_HEAD)[fpga]$(C_RESET) Compiling (this takes a while)...\n"
	@rm -rf db incremental_db
	@quartus_sh --flow compile $(PROJECT) $(VERILOG_MACROS)
	@printf "$(C_OK)[fpga] Done$(C_RESET)\n"

# Incremental compile (keeps Quartus cache)
build:
	@printf "$(C_HEAD)[fpga]$(C_RESET) Incremental compile...\n"
	@quartus_sh --flow compile $(PROJECT) $(VERILOG_MACROS)
	@printf "$(C_OK)[fpga] Done$(C_RESET)\n"

# ── Core variant builds ──────────────────────────────────────────────
# variant-full:  All hardware (mixer + OPL3 + link + analogizer)
# variant-lite:  Mixer only (no OPL3/link — smaller, better timing)
# variant-3d:    Mixer + future GPU3D (no OPL3 — ALMs freed for rasterizer)

variant-full: firmware
	@printf "$(C_HEAD)[variant]$(C_RESET) Building FULL variant...\n"
	@rm -rf db incremental_db
	@quartus_sh --flow compile $(PROJECT)

variant-lite: firmware
	@printf "$(C_HEAD)[variant]$(C_RESET) Building LITE variant (no OPL3, no link)...\n"
	@rm -rf db incremental_db
	@quartus_sh --flow compile $(PROJECT) --verilog_macro="EXCLUDE_OPL3" --verilog_macro="EXCLUDE_LINK"

variant-3d: firmware
	@printf "$(C_HEAD)[variant]$(C_RESET) Building 3D variant (no OPL3)...\n"
	@rm -rf db incremental_db
	@quartus_sh --flow compile $(PROJECT) --verilog_macro="EXCLUDE_OPL3"

# ── Test ─────────────────────────────────────────────────────────────
test:
	@printf "$(C_HEAD)[test]$(C_RESET) Running Verilator tests...\n"
	@$(MAKE) -C $(TEST_DIR) all
	@printf "$(C_OK)[test] Done$(C_RESET)\n"

# ── RTL check ────────────────────────────────────────────────────────
check:
	@printf "$(C_HEAD)[check]$(C_RESET) Analysis & Synthesis...\n"
	@quartus_map $(PROJECT)
	@printf "$(C_OK)[check] Done$(C_RESET)\n"

# ── Deploy ───────────────────────────────────────────────────────────
deploy: $(REVERSE_BITS)
	@printf "$(C_HEAD)[deploy]$(C_RESET)\n"
	@$(REVERSE_BITS) output_files/$(PROJECT).rbf $(RELEASE_DIR)/bitstream.rbf_r > /dev/null
	@cp $(FW_DIR)/os.bin $(RELEASE_DIR)/os.bin
	@cp $(FW_DIR)/os.bin $(ASSETS_DIR)/os.bin
	@printf "  $(C_OK)bitstream$(C_RESET)  → build/\n"
	@printf "  $(C_OK)os.bin$(C_RESET)     → build/\n"

# ── Flash (firmware-only, fast) ──────────────────────────────────────
flash: firmware $(REVERSE_BITS)
	@printf "$(C_HEAD)[flash]$(C_RESET) Patching bitstream...\n"
	@quartus_cdb $(PROJECT) --update_mif > /dev/null 2>&1
	@quartus_asm $(PROJECT) > /dev/null 2>&1
	@$(REVERSE_BITS) output_files/$(PROJECT).rbf $(RELEASE_DIR)/bitstream.rbf_r > /dev/null
	@cp $(FW_DIR)/os.bin $(RELEASE_DIR)/os.bin
	@cp $(FW_DIR)/os.bin $(ASSETS_DIR)/os.bin
	@printf "  $(C_OK)bitstream$(C_RESET)  → build/\n"
	@printf "  $(C_OK)os.bin$(C_RESET)     → build/\n"

# ── JTAG program ─────────────────────────────────────────────────────
program:
	@printf "$(C_HEAD)[jtag]$(C_RESET) Programming via USB Blaster...\n"
	@quartus_pgm -m jtag -o "p;output_files/$(PROJECT).sof"
	@printf "$(C_OK)[jtag] Done$(C_RESET)\n"

# ── Timing report ────────────────────────────────────────────────────
timing:
	@printf "$(C_HEAD)Fmax Summary (Slow 85°C):$(C_RESET)\n"
	@grep -E "^\; [0-9]" output_files/$(PROJECT).sta.rpt 2>/dev/null | head -6 | while IFS=';' read _ fmax rfmax clock _rest; do \
		fmax=$$(echo "$$fmax" | xargs); \
		clock=$$(echo "$$clock" | xargs); \
		short=$$(echo "$$clock" | sed 's/.*mp_ram.*general\[\([0-9]\)\].*/ram_out\1/' | sed 's/.*mp1.*general\[\([0-9]\)\].*/pll_out\1/' | sed 's/.*bridge_spiclk/spi/' | sed 's/.*clk_74a/clk_74a/'); \
		printf "  %-12s %s\n" "$$fmax" "$$short"; \
	done
	@echo ""
	@printf "$(C_HEAD)Setup Slack (Slow 85°C, worst per clock):$(C_RESET)\n"
	@grep -A20 "Slow 1100mV 85C Model Setup Summary" output_files/$(PROJECT).sta.rpt 2>/dev/null \
		| grep "^;" | grep -E "\-?[0-9]+\.[0-9]+" \
		| awk -F';' '{ \
			clock=$$2; gsub(/^[ \t]+|[ \t]+$$/, "", clock); \
			slack=$$3; gsub(/^[ \t]+|[ \t]+$$/, "", slack); \
			tns=$$4; gsub(/^[ \t]+|[ \t]+$$/, "", tns); \
			if (!(clock in seen) || slack+0 < seen[clock]+0) { seen[clock]=slack; tns_map[clock]=tns; } \
		} END { \
			for (c in seen) print seen[c], tns_map[c], c; \
		}' | sort -n | while read slack tns clock; do \
		short=$$(echo "$$clock" | sed 's/.*mp_ram.*general\[\([0-9]\)\].*/ram_out\1/' | sed 's/.*mp1.*general\[\([0-9]\)\].*/pll_out\1/' | sed 's/.*bridge_spiclk/spi/' | sed 's/.*clk_74a/clk_74a/' | sed 's/.*cram0_clk_pin/cram0/' | sed 's/.*dram_clk_pin/dram/'); \
		if echo "$$slack" | grep -q "^-"; then \
			printf "  $(C_ERR)%-10s$(C_RESET) TNS %-12s %s\n" "$$slack" "$$tns" "$$short"; \
		else \
			printf "  $(C_OK)%-10s$(C_RESET) TNS %-12s %s\n" "$$slack" "$$tns" "$$short"; \
		fi; \
	done

# ── Build summary ────────────────────────────────────────────────────
summary:
	@echo ""
	@printf "$(C_HEAD)Build Summary$(C_RESET)\n"
	@test -f $(VEXII_OUT) \
		&& printf "  $(C_OK)[x]$(C_RESET) CPU generated\n" \
		|| printf "  $(C_DIM)[ ] CPU$(C_RESET)\n"
	@test -f $(FW_DIR)/firmware.mif \
		&& printf "  $(C_OK)[x]$(C_RESET) Firmware built\n" \
		|| printf "  $(C_DIM)[ ] Firmware$(C_RESET)\n"
	@test -f $(FW_DIR)/os.bin \
		&& SIZE=$$(stat -c%s $(FW_DIR)/os.bin 2>/dev/null || stat -f%z $(FW_DIR)/os.bin 2>/dev/null) \
		&& printf "  $(C_OK)[x]$(C_RESET) OS built ($$SIZE bytes)\n" \
		|| printf "  $(C_DIM)[ ] OS$(C_RESET)\n"
	@if test -f output_files/$(PROJECT).rbf; then \
		FMAX=$$(grep -E "^\; [0-9]" output_files/$(PROJECT).sta.rpt 2>/dev/null \
			| grep "mp_ram.*general\[0\]" | head -1 \
			| awk -F';' '{print $$2}' | xargs); \
		if [ -n "$$FMAX" ]; then \
			printf "  $(C_OK)[x]$(C_RESET) FPGA compiled $(C_DIM)($$FMAX)$(C_RESET)\n"; \
		else \
			printf "  $(C_OK)[x]$(C_RESET) FPGA compiled\n"; \
		fi; \
	else \
		printf "  $(C_DIM)[ ] FPGA$(C_RESET)\n"; \
	fi
	@test -f $(RELEASE_DIR)/bitstream.rbf_r \
		&& printf "  $(C_OK)[x]$(C_RESET) Deployed\n" \
		|| printf "  $(C_DIM)[ ] Deploy$(C_RESET)\n"
	@echo ""

# ── Seed sweep ───────────────────────────────────────────────────────
SWEEP_MIN ?= 1
SWEEP_MAX ?= 30

sweep: firmware
	@bash $(ROOT_DIR)/tools/sweep.sh $(SWEEP_MIN) $(SWEEP_MAX)

# ── Tools ────────────────────────────────────────────────────────────
$(REVERSE_BITS): $(TOOLS_DIR)/reverse_bits.c
	@gcc -O2 -o $@ $<

# ── Clean ────────────────────────────────────────────────────────────
clean:
	@printf "$(C_HEAD)[clean]$(C_RESET) Removing build artifacts...\n"
	@rm -rf db incremental_db
	@rm -f output_files/*.rpt output_files/*.summary output_files/*.sof
	@rm -f output_files/*.rbf output_files/*.done output_files/*.smsg
	@rm -f output_files/*.pin output_files/*.jdi
	@rm -f sweep_s*.qsf
	@$(MAKE) -s -C $(FW_DIR) clean
	@rm -f $(VEXII_OUT)

# Alias for root Makefile compatibility
clean-all: clean

.PHONY: all full help cpu firmware compile build test check deploy flash program
.PHONY: timing summary sweep clean clean-all
