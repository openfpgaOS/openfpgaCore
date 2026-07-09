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
# MiSTer-specific DUAL-ISSUE netlist (--decoders=2 --lanes=2): the
# DE10-Nano's A6/I7 part has the area and speed-grade headroom the
# Pocket lacks.  Same module name / AXI interface — only the file
# differs (see generate_vexii.sh variants).
VEXII_OUT  = $(VEXII_DIR)/VexiiRiscv_mister.v
TEST_DIR   = ../../test
ROOT_DIR   = ../../../..

# Quartus 17.0.x runs in Docker by DEFAULT — the mister sys/ framework is a
# hard Q17 requirement, and containerizing it removes the host-toolchain
# dependency (so a fresh checkout on Linux OR macOS only needs Docker).
# Image is openfpgaos-quartus17, auto-baked on first use from a contributor-
# supplied tarball at tools/Quartus-*17.0*-linux.tar; see
# tools/build-quartus17-image.sh.
#
# Set USE_QUARTUS_CONTAINER=0 to use a host install instead — $(QUARTUS)
# default still points at the conventional MiSTer Q17 path.
USE_QUARTUS_CONTAINER ?= 1
ifeq ($(USE_QUARTUS_CONTAINER),1)
Q = bash $(ROOT_DIR)/tools/quartus17-container.sh
else
QUARTUS   ?= /home/alberto/intelFPGA_lite/17.0/quartus/bin
export PATH := $(QUARTUS):$(PATH)
Q =
endif

BUILD_OUT = $(ROOT_DIR)/build/mister

# ── MiSTer core release (make package / make release) ────────────────
# The core = the game-agnostic bitstream + OS kernel (openfpgaOS.rbf +
# boot.rom).  `make package` stages build/mister/ AND builds a release zip
# (+ a Downloader DB); `make release` drafts a GitHub release from it.
# Paths are absolutised because the release recipe cd's into the stage dir.
ABS_ROOT  = $(abspath $(ROOT_DIR))
REL_DIR   = $(ABS_ROOT)/releases/mister
REL_STAGE = $(ABS_ROOT)/build/mister/release
CORE_VER  = $(shell python3 -c "import json;print(json.load(open('$(ABS_ROOT)/dist/core/core.json'))['core']['metadata']['version'])" 2>/dev/null || echo 0.0.0)
CORE_ZIP  = $(REL_DIR)/openfpgaos-core-v$(CORE_VER).zip
# Downloader-DB generator lives in the SDK (not vendored here); overridable.
MKDB     ?= $(abspath $(ROOT_DIR)/../openfpgaSDK)/src/sdk/platforms/mister/mkdb.py
MISTER_DB_ID       ?= thinkelastic/openfpgaos
MISTER_DB_BASE_URL ?= https://raw.githubusercontent.com/thinkelastic/openfpgaOS/mister-dist/
MISTER_DB_URL      ?= https://raw.githubusercontent.com/thinkelastic/openfpgaOS/mister-dist/openfpgaos.json.zip

# ── Feature include-list ─────────────────────────────────────────────
# Positive include-set (a feature is PRESENT iff its INCLUDE_* macro is
# defined).  MiSTer has the full feature set EXCEPT analogizer (Pocket-only
# HW) and fast texture memory (no CRAM1 chip — textures render from SDRAM).
# Forwarded to quartus_map as --verilog_macro=<NAME>; the gpu_core /
# axi_periph_slave instances in emu.sv echo each macro into their INCLUDE_*
# params via the `ifdef INCLUDE_X 1 `else 0 `endif pattern.
# LINK + 4PLAYER dropped 2026-06-27 (module audit): LINK is stubbed in emu.sv
# (advertised cap, no HW, no firmware check) and 4PLAYER is a dead macro on MiSTer
# (the only gate is pocket core_top.v; MiSTer multiplayer comes via HPS/USB).
# INCLUDE_LINK=0 folds the periph link logic — the same config pocket already
# ships — so this is low-risk, but RE-FIT MiSTer on Quartus 17 to confirm.
INCLUDE_DEFS = INCLUDE_HW_MIXER INCLUDE_TRANSLUC \
               INCLUDE_COLUMN_LIST INCLUDE_COMPACT_SPAN INCLUDE_PARAM_TRI \
               INCLUDE_VERT_TRI INCLUDE_PARAM_TRI_RECS
VERILOG_MACROS = $(foreach d,$(INCLUDE_DEFS),--verilog_macro="$(d)")

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
	@printf "    $(C_CMD)make $(C_CMD)deploy$(C_RESET)            Stage build/mister/; $(C_CMD)MISTER_IP=<ip>$(C_RESET) also pushes to HW\n"
	@printf "    $(C_DIM)Push to hardware from the SDK: platforms/mister/copy.sh$(C_RESET)\n"
	@echo ""

# ── CPU generation (file target — MiSTer dual-issue variant) ─────────
$(VEXII_OUT):
	@printf "$(C_HEAD)[cpu]$(C_RESET) Generating VexiiRiscv (dual-issue, file missing)...\n"
	@cd $(VEXII_DIR) && bash ../generate_vexii.sh mister
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
		$(Q) quartus_cdb $(PROJECT) --update_mif > /dev/null 2>&1; \
		$(Q) quartus_asm $(PROJECT) > /dev/null 2>&1; \
	fi
	@printf "$(C_OK)[fw] Done$(C_RESET)\n"

# ── FPGA compile ─────────────────────────────────────────────────────
compile: build

# Full compile with fresh placement.  Quartus emits output_files/mister.rbf
# directly (GENERATE_RBF_FILE ON) — no reverse_bits step; MiSTer loads a
# plain .rbf.
#
# Explicit stages (not `quartus_sh --flow compile`) so the feature
# include-list reaches synthesis: --verilog_macro is only valid on
# quartus_map.  The sys/build_id.tcl PRE_FLOW script still runs ahead of
# quartus_map (stamps build_id.v); quartus_asm emits the .rbf.
build: $(VEXII_OUT) bootloader build_id.v
	@printf "$(C_HEAD)[fpga]$(C_RESET) Clean compile (Quartus 17.0.x)...\n"
	@rm -rf db incremental_db
	@# One container call for the whole map/fit/asm/sta chain — Q17's
	@# per-tool startup adds up under Rosetta; one container start amortizes.
	@$(Q) bash -c 'quartus_map $(PROJECT) $(VERILOG_MACROS) && \
	               quartus_fit $(PROJECT) && \
	               quartus_asm $(PROJECT) && \
	               quartus_sta $(PROJECT)'
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
	@$(Q) quartus_map $(PROJECT) $(VERILOG_MACROS)
	@printf "$(C_OK)[check] Done$(C_RESET)\n"

# Bake the Quartus 17.0.x container image (one-time per machine; the build
# rules auto-trigger this on first use too).  See tools/build-quartus17-image.sh.
.PHONY: quartus17-image
quartus17-image:
	@bash $(ROOT_DIR)/tools/build-quartus17-image.sh

# ── Deploy ───────────────────────────────────────────────────────────
# Refresh the core artifacts in build/mister/ from whatever is on disk
# (like the Pocket target).  Add MISTER_IP=<ip> to ALSO push them to a
# MiSTer over the network: scp's the bitstream + kernel straight to the
# card (then reload the core / relaunch a .mgl).  One ssh ControlMaster
# connection means it asks for the root password just once — set up an
# SSH key to skip it.  `copy.sh core` in the SDK does the same push from
# an SDK checkout if you prefer that path.
HW_CORE_DIR  = /media/fat/_Computer
HW_GAMES_DIR = /media/fat/games/OpenfpgaOS
SSH_OPTS     = -o ControlMaster=auto -o ControlPath=/tmp/.of-mister-mux-%r@%h:%p -o ControlPersist=20
deploy:
	@mkdir -p $(BUILD_OUT)
	@test -f output_files/$(PROJECT).rbf && \
		cp output_files/$(PROJECT).rbf $(BUILD_OUT)/openfpgaOS.rbf && \
		printf "  $(C_OK)openfpgaOS.rbf$(C_RESET) → $(BUILD_OUT)/\n" || \
		printf "  $(C_DIM)[ ] bitstream (run make build)$(C_RESET)\n"
	@if [ -f $(FW_DIR)/bld/mister/os.bin ]; then \
		cp $(FW_DIR)/bld/mister/os.bin $(BUILD_OUT)/boot.rom; \
		printf "  $(C_OK)boot.rom$(C_RESET)       → $(BUILD_OUT)/\n"; \
	else \
		printf "  $(C_DIM)[ ] boot.rom (run make os TARGET=mister first)$(C_RESET)\n"; \
	fi
ifeq ($(strip $(MISTER_IP)),)
	@printf "$(C_OK)[deploy] Done$(C_RESET) $(C_DIM)(add MISTER_IP=<ip> to also push to hardware)$(C_RESET)\n"
else
	@printf "$(C_HEAD)[deploy]$(C_RESET) pushing to root@$(MISTER_IP) ...\n"
	@ssh $(SSH_OPTS) root@$(MISTER_IP) 'mkdir -p $(HW_CORE_DIR) $(HW_GAMES_DIR)' || \
		{ printf "  $(C_ERR)ssh to root@$(MISTER_IP) failed (host reachable? password?)$(C_RESET)\n"; exit 1; }
	@if [ -f $(BUILD_OUT)/openfpgaOS.rbf ]; then \
		scp $(SSH_OPTS) $(BUILD_OUT)/openfpgaOS.rbf root@$(MISTER_IP):$(HW_CORE_DIR)/OpenfpgaOS.rbf && \
		printf "  $(C_OK)OpenfpgaOS.rbf$(C_RESET) → root@$(MISTER_IP):$(HW_CORE_DIR)/\n"; \
	else printf "  $(C_ERR)no bitstream to push — run make build$(C_RESET)\n"; fi
	@if [ -f $(BUILD_OUT)/boot.rom ]; then \
		scp $(SSH_OPTS) $(BUILD_OUT)/boot.rom root@$(MISTER_IP):$(HW_GAMES_DIR)/boot.rom && \
		printf "  $(C_OK)boot.rom$(C_RESET)       → root@$(MISTER_IP):$(HW_GAMES_DIR)/\n"; \
	else printf "  $(C_ERR)no boot.rom to push — run make os TARGET=mister$(C_RESET)\n"; fi
	@printf "$(C_OK)[deploy] pushed to root@$(MISTER_IP)$(C_RESET) $(C_DIM)— reload the core (relaunch a .mgl) to run it$(C_RESET)\n"
endif

# ── Package: rbf + boot.rom → build/mister/ ──────────────────────────
# The MiSTer deliverable is a plain bitstream + kernel pair (the SDK's
# per-game packager, platforms/mister/mkgame.sh, assembles the .vhd images).
# Invoked by the root `make package`, which delegates per target — the
# root names no target.  Stricter than `deploy`: errors out if the
# on-disk os.bin is not a MiSTer build, so a release can't ship a Pocket
# kernel as boot.rom.
package:
	@mkdir -p $(BUILD_OUT)
	@cp output_files/$(PROJECT).rbf $(BUILD_OUT)/openfpgaOS.rbf
	@if [ ! -f $(FW_DIR)/bld/mister/os.bin ]; then \
		printf "  $(C_ERR)no mister os.bin — run make os TARGET=mister first$(C_RESET)\n"; \
		exit 1; \
	fi
	@cp $(FW_DIR)/bld/mister/os.bin $(BUILD_OUT)/boot.rom
	@printf "  $(C_OK)staged$(C_RESET)         → build/mister/ (rbf + boot.rom)\n"
	@# Release bundle: an SD-rooted zip plus an optional Downloader DB.
	@rm -rf $(REL_STAGE)
	@mkdir -p "$(REL_STAGE)/_Computer" "$(REL_STAGE)/games/OpenfpgaOS" "$(REL_DIR)"
	@cp $(BUILD_OUT)/openfpgaOS.rbf "$(REL_STAGE)/_Computer/OpenfpgaOS.rbf"
	@cp $(BUILD_OUT)/boot.rom "$(REL_STAGE)/games/OpenfpgaOS/boot.rom"
	@{ \
		echo "openfpgaOS core for MiSTer  v$(CORE_VER)"; \
		echo ""; \
		echo "The game-agnostic core (bitstream + OS kernel).  Games ship as"; \
		echo "separate per-game packages that drop into games/OpenfpgaOS/."; \
		echo ""; \
		echo "Install once:"; \
		echo "  1. Unzip this archive into /media/fat/ (merge folders):"; \
		echo "       _Computer/OpenfpgaOS.rbf    the core bitstream"; \
		echo "       games/OpenfpgaOS/boot.rom   the OS kernel"; \
		echo "  2. Install a game package (e.g. Doom) into games/OpenfpgaOS/."; \
		echo "  3. Launch a game .mgl from the MiSTer menu."; \
		echo ""; \
		echo "Or via MiSTer Downloader: add the [$(MISTER_DB_ID)] snippet from"; \
		echo "openfpgaos.downloader.ini to /media/fat/downloader.ini and sync."; \
	} > "$(REL_STAGE)/INSTALL.txt"
	@cd "$(REL_STAGE)" && rm -f "$(CORE_ZIP)" && zip -rq "$(CORE_ZIP)" .
	@printf "  $(C_OK)core zip$(C_RESET)       → releases/mister/openfpgaos-core-v$(CORE_VER).zip\n"
	@if [ -x "$(MKDB)" ]; then \
		"$(MKDB)" --staging "$(REL_STAGE)" --sd-prefix "" \
			--base-url "$(MISTER_DB_BASE_URL)" --db-id "$(MISTER_DB_ID)" \
			--reboot '_Computer/OpenfpgaOS.rbf' --reboot 'games/OpenfpgaOS/boot.rom' \
			--db-url "$(MISTER_DB_URL)" \
			--output "$(REL_DIR)/openfpgaos.json.zip" \
			--ini-out "$(REL_DIR)/openfpgaos.downloader.ini" \
			&& printf "  $(C_OK)Downloader DB$(C_RESET)  → releases/mister/openfpgaos.json.zip\n" || true; \
	else \
		printf "  $(C_DIM)[ ] Downloader DB skipped — SDK mkdb.py not at $(MKDB)$(C_RESET)\n"; \
	fi
	@printf "$(C_OK)[package] Done$(C_RESET) → releases/mister/\n"

# ── Release: publish the core zip (+ DB) to GitHub ───────────────────
# Builds the package first, then drafts a GitHub release.  PUBLISH=1 = live
# (default draft); PREV=<tag> overrides the changelog baseline.
release: package
	@CORE_VER="$(CORE_VER)" MISTER_DB_ID="$(MISTER_DB_ID)" PREV="$(PREV)" PUBLISH="$(PUBLISH)" \
		bash "$(ABS_ROOT)/dist/mister/release.sh"

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
	@USE_CONTAINER=0 MAXJOBS=1 PROJECT=$(PROJECT) \
		CLOCK_RE='emu|pll|pll_inst.*general\[0\]' \
		bash $(ROOT_DIR)/tools/sweep.sh $(SWEEP_MIN) $(SWEEP_MAX)

# ── Resource / timing report ─────────────────────────────────────────
# Summary of resource usage; FULL=true adds the top ALM-consuming
# entities and the 25 worst setup paths (re-runs quartus_sta).
report:
	@chmod +x $(ROOT_DIR)/tools/report.sh 2>/dev/null || true
	@TARGET="$(TARGET)" bash $(ROOT_DIR)/tools/report.sh $(PROJECT) $(if $(filter true,$(FULL)),full)

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
	@test -f $(FW_DIR)/bld/mister/os.bin \
		&& SIZE=$$(stat -c%s $(FW_DIR)/bld/mister/os.bin 2>/dev/null) \
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

.PHONY: all full help cpu bootloader firmware os compile build test check deploy report sweep sdk program package
.PHONY: timing summary clean clean-all
