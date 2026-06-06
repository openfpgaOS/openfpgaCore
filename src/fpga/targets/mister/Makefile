#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------

# openfpgaOS — MiSTer (DE10-Nano / SuperStation One) Build System
#
# Quick start:
#   make full     Full build: cpu → firmware → compile
#   make firmware Bootloader + os.bin + MIF patch (~10 sec)
#   make deploy   Refresh build/mister/ (rbf + boot.rom)
#   make sdk DEST=path   Sync runtime artifacts to an SDK checkout
#   make help     Show all targets
# Device pushing lives in the SDK: platforms/mister/copy.sh
#
# NOTE: the MiSTer sys/ framework requires Quartus Prime 17.0.x — newer
# versions are known to miscompile parts of it.  Point QUARTUS at a
# 17.0.x install (the Pocket target keeps its own 25.1std toolchain).

# ── Configuration ────────────────────────────────────────────────────
PROJECT    = mister
TARGET     = mister
FW_DIR     = ../../../firmware/os
VEXII_DIR  = ../../vendor/vexriscv/VexiiRiscv
VEXII_OUT  = $(VEXII_DIR)/VexiiRiscv.v
TEST_DIR   = ../../test
ROOT_DIR   = ../../../..

QUARTUS   ?= /home/alberto/intelFPGA_lite/17.0/quartus/bin
export PATH := $(QUARTUS):$(PATH)

BUILD_OUT = $(ROOT_DIR)/build/mister

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
full: cpu bootloader os build summary

# ── Help ─────────────────────────────────────────────────────────────
help:
	@printf "$(C_LOGO)"
	@echo "  openfpgaOS — $(TARGET)"
	@printf "$(C_RESET)\n"
	@printf "  $(C_HEAD)Build:$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)full$(C_RESET)              bootloader → os → build\n"
	@printf "    $(C_CMD)make $(C_CMD)build$(C_RESET)             cpu → Quartus compile → mister.rbf\n"
	@printf "    $(C_CMD)make $(C_CMD)firmware$(C_RESET)          bootloader → os → MIF patch       $(C_DIM)(~10 sec)$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)os$(C_RESET)                Builds os.bin (= boot.rom)\n"
	@printf "    $(C_CMD)make $(C_CMD)clean$(C_RESET)             Remove all build artifacts\n"
	@echo ""
	@printf "  $(C_HEAD)Development:$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)test$(C_RESET)              Run Verilator test suite\n"
	@printf "    $(C_CMD)make $(C_CMD)check$(C_RESET)             RTL check (Analysis & Synthesis)\n"
	@printf "    $(C_CMD)make $(C_CMD)timing$(C_RESET)            Show Fmax and slack from last build\n"
	@printf "    $(C_CMD)make $(C_CMD)deploy$(C_RESET)            Refresh build/mister/ (rbf + boot.rom)\n"
	@printf "    $(C_DIM)Push to hardware from the SDK: platforms/mister/copy.sh$(C_RESET)\n"
	@echo ""

# ── CPU generation (file target — shared netlist with the Pocket) ────
$(VEXII_OUT):
	@printf "$(C_HEAD)[cpu]$(C_RESET) Generating VexiiRiscv (file missing)...\n"
	@cd $(VEXII_DIR) && bash ../generate_vexii.sh
	@printf "$(C_OK)[cpu] Done$(C_RESET)\n"

cpu: $(VEXII_OUT)

# ── Bootloader / OS / Firmware ──────────────────────────────────────
#
#   bootloader — firmware.mif into this dir (Quartus altsyncram init)
#   os         — os.bin; deployed to the MiSTer as boot.rom
#   firmware   — bootloader → os → MIF patch of an existing .sof
bootloader:
	@printf "$(C_HEAD)[boot]$(C_RESET) Building bootloader (firmware.mif)...\n"
	@$(MAKE) -C $(FW_DIR) install TARGET=$(TARGET)
	@printf "$(C_OK)[boot] Done$(C_RESET)\n"

os:
	@printf "$(C_HEAD)[os]$(C_RESET) Building os.bin...\n"
	@$(MAKE) -C $(FW_DIR) os.bin TARGET=$(TARGET)
	@printf "$(C_OK)[os] Done$(C_RESET)\n"

firmware: bootloader os
	@if [ -f output_files/$(PROJECT).sof ]; then \
		printf "$(C_HEAD)[fw]$(C_RESET) Patching .sof MIF with new bootloader...\n"; \
		quartus_cdb $(PROJECT) --update_mif > /dev/null 2>&1; \
		quartus_asm $(PROJECT) > /dev/null 2>&1; \
	fi
	@printf "$(C_OK)[fw] Done$(C_RESET)\n"

# ── FPGA compile ─────────────────────────────────────────────────────
compile: build

# Full compile with fresh placement.  Quartus emits output_files/mister.rbf
# directly (GENERATE_RBF_FILE ON) — no reverse_bits step; MiSTer loads a
# plain .rbf.
build: $(VEXII_OUT) bootloader
	@printf "$(C_HEAD)[fpga]$(C_RESET) Clean compile (Quartus 17.0.x)...\n"
	@rm -rf db incremental_db
	@quartus_sh --flow compile $(PROJECT)
	@printf "$(C_OK)[fpga] Done$(C_RESET) → output_files/$(PROJECT).rbf\n"

# ── Test ─────────────────────────────────────────────────────────────
test:
	@printf "$(C_HEAD)[test]$(C_RESET) Running Verilator tests...\n"
	@$(MAKE) -C $(TEST_DIR) all
	@printf "$(C_OK)[test] Done$(C_RESET)\n"

# ── RTL check ────────────────────────────────────────────────────────
# build_id.v is gitignored (the framework PRE_FLOW script re-stamps it on
# every full compile, which would keep the tree perpetually dirty); a
# fresh clone needs a placeholder for standalone quartus_map / lint runs.
build_id.v:
	@printf '`define BUILD_DATE "000000"\n' > $@

check: build_id.v
	@printf "$(C_HEAD)[check]$(C_RESET) Analysis & Synthesis...\n"
	@quartus_map $(PROJECT)
	@printf "$(C_OK)[check] Done$(C_RESET)\n"

# ── Deploy ───────────────────────────────────────────────────────────
# Same semantics as the Pocket target: refresh the core artifacts in
# build/mister/ from whatever is on disk.  Pushing to hardware is the
# SDK's job (openfpgaSDK: src/sdk/platforms/mister/copy.sh) — sync the
# artifacts there with `make sdk DEST=path/to/sdk` first.
deploy:
	@mkdir -p $(BUILD_OUT)
	@test -f output_files/$(PROJECT).rbf && \
		cp output_files/$(PROJECT).rbf $(BUILD_OUT)/openfpgaOS.rbf && \
		printf "  $(C_OK)openfpgaOS.rbf$(C_RESET) → $(BUILD_OUT)/\n" || \
		printf "  $(C_DIM)[ ] bitstream (run make build)$(C_RESET)\n"
	@if [ -f $(FW_DIR)/os.bin ] && \
	    [ "$$(cat $(FW_DIR)/.last_target 2>/dev/null)" = "mister" ]; then \
		cp $(FW_DIR)/os.bin $(BUILD_OUT)/boot.rom; \
		printf "  $(C_OK)boot.rom$(C_RESET)       → $(BUILD_OUT)/\n"; \
	else \
		printf "  $(C_DIM)[ ] boot.rom (run make os — current os.bin is not a mister build)$(C_RESET)\n"; \
	fi
	@printf "$(C_OK)[deploy] Done$(C_RESET) $(C_DIM)(push to hardware from the SDK: platforms/mister/copy.sh)$(C_RESET)\n"

# ── JTAG program — Pocket-only ───────────────────────────────────────
# The root Makefile delegates `program` to every target; MiSTer has no
# JTAG flow — cores load from the menu / copy.sh.  Fail with guidance
# instead of make's bare "No rule" error.
program:
	@printf "$(C_ERR)JTAG programming is a Pocket flow.$(C_RESET)\n"
	@printf "Deploy to a MiSTer from the SDK instead: platforms/mister/copy.sh core <ip>\n"
	@exit 1

# ── SDK sync (delegates to top-level Makefile) ───────────────────────
# Lets `make sdk DEST=path/to/sdk` work from this directory; the actual
# sync logic lives at the repo root (it ships BOTH targets' runtime
# artifacts, including runtime/mister/{openfpgaOS.rbf, os.bin}).
sdk:
	@$(MAKE) -C $(ROOT_DIR) sdk DEST="$(DEST)"

# ── Seed sweep ───────────────────────────────────────────────────────
# Same flow as the Pocket: try fitter seeds, rank by the 100 MHz
# domain's restricted Fmax (WNS/TNS reported alongside), rebuild best.
SWEEP_MIN ?= 1
SWEEP_MAX ?= 30

sweep: firmware
	@PROJECT=$(PROJECT) \
		CLOCK_RE='emu|pll|pll_inst.*general\[0\]' \
		bash $(ROOT_DIR)/tools/sweep.sh $(SWEEP_MIN) $(SWEEP_MAX)

# ── Resource / timing report ─────────────────────────────────────────
# Summary of resource usage; FULL=true adds the top ALM-consuming
# entities and the 25 worst setup paths (re-runs quartus_sta).
report:
	@chmod +x $(ROOT_DIR)/tools/report.sh 2>/dev/null || true
	@bash $(ROOT_DIR)/tools/report.sh $(PROJECT) $(if $(filter true,$(FULL)),full)

# ── Timing report ────────────────────────────────────────────────────
timing:
	@printf "$(C_HEAD)Fmax Summary (slow corner):$(C_RESET)\n"
	@grep -E "^\; [0-9]" output_files/$(PROJECT).sta.rpt 2>/dev/null | head -8 | while IFS=';' read _ fmax rfmax clock _rest; do \
		fmax=$$(echo "$$fmax" | xargs); \
		clock=$$(echo "$$clock" | xargs); \
		short=$$(echo "$$clock" | sed 's/.*emu|pllv.*general\[0\].*/clk_vid/' | sed 's/.*emu|pll.*general\[\([0-9]\)\].*/pll_out\1/' | sed 's/.*pll_hdmi.*/pll_hdmi/' | sed 's/.*pll_audio.*/pll_audio/' | sed 's/.*FPGA_CLK\([0-9]\)_50.*/clk\1_50/'); \
		case "$$short" in \
			pll_out[01]) target="100.00 MHz" ;; \
			clk_vid)     target="24.58 MHz" ;; \
			clk[0-9]_50) target="50.00 MHz" ;; \
			*)           target="—" ;; \
		esac; \
		printf "  %-12s %-12s %s\n" "$$fmax" "$$target" "$$short"; \
	done
	@echo ""
	@printf "$(C_HEAD)Setup Slack (worst per clock):$(C_RESET)\n"
	@grep -A24 "Slow 1100mV 85C Model Setup Summary" output_files/$(PROJECT).sta.rpt 2>/dev/null \
		| grep "^;" | grep -E "\-?[0-9]+\.[0-9]+" \
		| awk -F';' '{ \
			clock=$$2; gsub(/^[ \t]+|[ \t]+$$/, "", clock); \
			slack=$$3; gsub(/^[ \t]+|[ \t]+$$/, "", slack); \
			tns=$$4; gsub(/^[ \t]+|[ \t]+$$/, "", tns); \
			if (!(clock in seen) || slack+0 < seen[clock]+0) { seen[clock]=slack; tns_map[clock]=tns; } \
		} END { \
			for (c in seen) print seen[c], tns_map[c], c; \
		}' | sort -n | head -8 | while read slack tns clock; do \
		short=$$(echo "$$clock" | sed 's/.*emu|pll.*general\[\([0-9]\)\].*/pll_out\1/' | sed 's/.*pll_hdmi.*/pll_hdmi/' | sed 's/.*pll_audio.*/pll_audio/' | sed 's/.*sdram_clk_pin.*/sdram/'); \
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
	@test -f firmware.mif \
		&& printf "  $(C_OK)[x]$(C_RESET) Firmware built\n" \
		|| printf "  $(C_DIM)[ ] Firmware$(C_RESET)\n"
	@test -f $(FW_DIR)/os.bin \
		&& SIZE=$$(stat -c%s $(FW_DIR)/os.bin 2>/dev/null) \
		&& printf "  $(C_OK)[x]$(C_RESET) OS built ($$SIZE bytes)\n" \
		|| printf "  $(C_DIM)[ ] OS$(C_RESET)\n"
	@test -f output_files/$(PROJECT).rbf \
		&& printf "  $(C_OK)[x]$(C_RESET) FPGA compiled\n" \
		|| printf "  $(C_DIM)[ ] FPGA$(C_RESET)\n"
	@echo ""

# ── Clean ────────────────────────────────────────────────────────────
clean:
	@printf "$(C_HEAD)[clean]$(C_RESET) Removing build artifacts...\n"
	@rm -rf db incremental_db
	@rm -f output_files/*.rpt output_files/*.summary output_files/*.sof
	@rm -f output_files/*.rbf output_files/*.done output_files/*.smsg
	@rm -f output_files/*.pin output_files/*.jdi
	@$(MAKE) -s -C $(FW_DIR) clean
	@rm -f $(VEXII_OUT)

clean-all: clean

.PHONY: all full help cpu bootloader firmware os compile build test check deploy report sweep sdk program
.PHONY: timing summary clean clean-all
