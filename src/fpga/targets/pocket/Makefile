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
# bootloader → os → build → test. `build` itself auto-runs the cpu
# generation (file-target rule below) when VexiiRiscv_Full.v is missing.
full: bootloader os build test summary

# ── Help ─────────────────────────────────────────────────────────────
help:
	@printf "$(C_LOGO)"
	@echo "  openfpgaOS — $(TARGET)"
	@printf "$(C_RESET)\n"
	@printf "  $(C_HEAD)Build:$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)full$(C_RESET)              bootloader → os → build → test     $(C_DIM)(~9 min)$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)build$(C_RESET)             cpu → compile → reverse bits       $(C_DIM)(~7 min)$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)firmware$(C_RESET)          bootloader → os → MIF patch        $(C_DIM)(~10 sec)$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)os$(C_RESET)                Builds os.bin                      $(C_DIM)(~5 sec)$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)clean$(C_RESET)             Remove all build artifacts\n"
	@echo ""
	@printf "  $(C_HEAD)Development:$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)test$(C_RESET)              Run Verilator test suite           $(C_DIM)(~30 sec)$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)check$(C_RESET)             RTL check (Analysis & Synthesis)   $(C_DIM)(~45 sec)$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)timing$(C_RESET)            Show Fmax and slack from last build\n"
	@printf "    $(C_CMD)make $(C_CMD)sweep $(C_CMD)SEEDS=$(C_CMD)1-30$(C_RESET)  Try seeds, report best Fmax        $(C_DIM)(~7 min/seed)$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)sdk $(C_CMD)DEST=$(C_CMD)\"path\"$(C_RESET)   Sync headers + runtime to SDK repo(s)\n"
	@printf "    $(C_CMD)make $(C_CMD)program$(C_RESET)           JTAG program via USB Blaster\n"
	@echo ""

# ── CPU generation (file target) ─────────────────────────────────────
# $(VEXII_OUT) = src/fpga/vendor/vexriscv/VexiiRiscv_Full.v. Generated
# from SpinalHDL by generate_vexii.sh. Not tracked in git — anyone
# checking out fresh will trigger the recipe automatically the first
# time `make build` (or anything else that needs it) runs.
$(VEXII_OUT):
	@printf "$(C_HEAD)[cpu]$(C_RESET) Generating VexiiRiscv (file missing)...\n"
	@cd $(VEXII_DIR) && bash ../generate_vexii.sh
	@cp $(VEXII_DIR)/VexiiRiscv.v $(VEXII_OUT)
	@printf "$(C_OK)[cpu] Done$(C_RESET)\n"

# Phony alias to force regeneration: rm $(VEXII_OUT) then `make cpu`,
# or just `make cpu` after deleting the file. Keeps muscle memory alive.
cpu: $(VEXII_OUT)

# ── Build artifact installers ───────────────────────────────────────
#
# Each substantive build step (os/build/firmware) calls one of these to
# refresh its output in $(RELEASE_DIR) so build/ always matches the
# latest in-tree binary. The SDK / SD-card sync runs separately against
# build/.
install-os: $(FW_DIR)/os.bin
	@mkdir -p $(RELEASE_DIR) $(ASSETS_DIR)
	@cp $(FW_DIR)/os.bin $(RELEASE_DIR)/os.bin
	@cp $(FW_DIR)/os.bin $(ASSETS_DIR)/os.bin
	@printf "  $(C_OK)os.bin$(C_RESET)     → $(RELEASE_DIR)/\n"

install-bitstream: $(REVERSE_BITS) output_files/$(PROJECT).rbf
	@mkdir -p $(RELEASE_DIR)
	@$(REVERSE_BITS) output_files/$(PROJECT).rbf $(RELEASE_DIR)/bitstream.rbf_r > /dev/null
	@printf "  $(C_OK)bitstream$(C_RESET)  → $(RELEASE_DIR)/\n"

# ── Bootloader / OS / Firmware ──────────────────────────────────────
#
# Two source artifacts:
#   - bootloader (firmware.mif): baked into the FPGA BRAM, runs at
#     reset, loads os.bin from CRAM/SD into SDRAM, jumps to it.
#   - os.bin: loaded by the bootloader; everything you actually run.
#
# Three targets:
#   `bootloader` — rebuild firmware.mif and copy into the pocket dir
#                  (where Quartus picks it up at the next compile).
#   `os`         — rebuild os.bin and install into build/.
#   `firmware`   — bootloader → os → MIF patch. The MIF patch updates
#                  any existing .sof in place so you don't need to
#                  re-run Quartus just to ship a new bootloader.
bootloader:
	@printf "$(C_HEAD)[boot]$(C_RESET) Building bootloader (firmware.mif)...\n"
	@$(MAKE) -C $(FW_DIR) install
	@printf "$(C_OK)[boot] Done$(C_RESET)\n"

os:
	@printf "$(C_HEAD)[os]$(C_RESET) Building os.bin...\n"
	@$(MAKE) -C $(FW_DIR) os.bin
	@$(MAKE) --no-print-directory install-os
	@printf "$(C_OK)[os] Done$(C_RESET)\n"

firmware: bootloader os
	@if [ -f output_files/$(PROJECT).sof ]; then \
		printf "$(C_HEAD)[fw]$(C_RESET) Patching .sof MIF with new bootloader...\n"; \
		quartus_cdb $(PROJECT) --update_mif > /dev/null 2>&1; \
		quartus_asm $(PROJECT) > /dev/null 2>&1; \
		$(MAKE) --no-print-directory install-bitstream; \
	fi
	@printf "$(C_OK)[fw] Done$(C_RESET)\n"

# ── FPGA compile ─────────────────────────────────────────────────────

# Variant defines — set via VARIANT_DEFS for core variant builds.
# Example: VARIANT_DEFS="EXCLUDE_OPL3 EXCLUDE_LINK" makes a lite build.
VARIANT_DEFS ?=
VERILOG_MACROS = $(foreach d,$(VARIANT_DEFS),--verilog_macro="$(d)")

# `compile` is kept as an alias for `build` so old muscle memory and
# anything that calls `make compile` still works. Both run a clean
# Quartus full compile (rm -rf db incremental_db + quartus_sh --flow).
compile: build

# Full compile with fresh placement (clears incremental DB first)
# Pocket only ships the Lite GPU variant — no GPU_VARIANT_* macro is passed,
# so gpu_features.vh defaults to GPU_VARIANT_LITE. The Full GPU variant is
# kept compilable for Verilator coverage and future hardware targets.
build: $(VEXII_OUT)
	@printf "$(C_HEAD)[fpga]$(C_RESET) Clean compile (GPU = Lite)...\n"
	@rm -rf db incremental_db
	@quartus_sh --flow compile $(PROJECT) $(VERILOG_MACROS)
	@$(MAKE) --no-print-directory install-bitstream
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
# Honors VARIANT_DEFS so you can `VARIANT_DEFS="GPU_VARIANT_FULL" make check`
# to sanity-check the Full GPU path even though Pocket only ships Lite.
check:
	@printf "$(C_HEAD)[check]$(C_RESET) Analysis & Synthesis...\n"
	@quartus_map $(PROJECT) $(VERILOG_MACROS)
	@printf "$(C_OK)[check] Done$(C_RESET)\n"

# ── Deploy ───────────────────────────────────────────────────────────
# `deploy` is now an explicit no-op that just refreshes both artifacts
# in build/ from whatever is currently on disk. Mostly useful as a
# top-level "make sure build/ matches the source tree" command. The
# individual build targets (os, build, firmware) already install their
# own outputs as a post-step, so you only need this if you want to
# re-sync without rebuilding anything.
deploy: install-os install-bitstream
	@printf "$(C_OK)[deploy] Done$(C_RESET)\n"

# `flash` removed — `firmware` now does the same thing (it builds os,
# patches the .sof's MIF, and reinstalls the bitstream + os.bin to
# build/). The old `flash` was identical except for the recipe order.

# ── SDK sync (delegates to top-level Makefile) ───────────────────────
# Lets you run `make sdk DEST=path/to/sdk` from this directory and
# have it forward to the openfpgaOS root, where the actual sync logic
# lives (it copies of_*.h into <DEST>/src/sdk/include and the runtime
# files into <DEST>/runtime).
sdk:
	@$(MAKE) -C $(ROOT_DIR) sdk DEST="$(DEST)"

# ── JTAG program ─────────────────────────────────────────────────────
program:
	@printf "$(C_HEAD)[jtag]$(C_RESET) Programming via USB Blaster...\n"
	@quartus_pgm -m jtag -o "p;output_files/$(PROJECT).sof"
	@printf "$(C_OK)[jtag] Done$(C_RESET)\n"

# ── Timing report ────────────────────────────────────────────────────
timing:
	@printf "$(C_HEAD)Fmax Summary (Slow 85°C):$(C_RESET)\n"
	@printf "  %-12s %-12s %s\n" "Achieved" "Target" "Clock"
	@printf "  %-12s %-12s %s\n" "--------" "------" "-----"
	@grep -E "^\; [0-9]" output_files/$(PROJECT).sta.rpt 2>/dev/null | head -6 | while IFS=';' read _ fmax rfmax clock _rest; do \
		fmax=$$(echo "$$fmax" | xargs); \
		clock=$$(echo "$$clock" | xargs); \
		short=$$(echo "$$clock" | sed 's/.*mp_ram.*general\[\([0-9]\)\].*/ram_out\1/' | sed 's/.*mp1.*general\[\([0-9]\)\].*/pll_out\1/' | sed 's/.*bridge_spiclk/spi/' | sed 's/.*clk_74a/clk_74a/'); \
		case "$$short" in \
			ram_out[0-2]) target="100.00 MHz" ;; \
			pll_out[01])  target="12.29 MHz" ;; \
			pll_out2)     target="49.15 MHz" ;; \
			pll_out[34])  target="12.57 MHz" ;; \
			clk_74a|spi)  target="74.25 MHz" ;; \
			*)            target="—" ;; \
		esac; \
		printf "  %-12s %-12s %s\n" "$$fmax" "$$target" "$$short"; \
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

.PHONY: all full help cpu bootloader firmware os compile build test check deploy sdk program
.PHONY: install-os install-bitstream timing summary sweep clean clean-all
