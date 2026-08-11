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
TEST_DIR   = ../../test
ROOT_DIR   = ../../../..

# ── Fitter seed ──────────────────────────────────────────────────────
# The seed is COMMITTED in seeds/mister.seed and sed-patched into
# mister.qsf by `build`, mirroring the Pocket's seeds/<variant>.seed.
# It used to live only in the qsf, so a sweep's winner survived as an
# untracked edit to a tracked file and NOTHING recorded which netlist it
# had been ranked on — the one guard that catches a seed gone stale under
# an RTL change (see tools/netlist_hash.sh for what that costs).
# seeds/mister.seed.src carries that fingerprint; `build` compares.
# Falls back to the qsf's own seed when the file is absent.
SEED_FILE  = seeds/mister.seed
SEED       = $(shell [ -f $(SEED_FILE) ] && cat $(SEED_FILE) || \
                     awk '/^set_global_assignment -name SEED /{print $$NF}' $(PROJECT).qsf)

# ── Variant registry + resolution (same model as the pocket target) ──
# MiSTer currently has ONE variant ("mister", the default) — but it uses
# the same auto-discovered variants/<name>.mk registry, so VARIANT= is
# validated instead of silently ignored, and a future variant is a file
# drop.  The sticky root .variant is SOFT (applies only when it names one
# of THIS target's variants — a sticky pocket os30 never breaks a mister
# build); an explicit unknown VARIANT= is a hard error.
# NOTE: unlike pocket, mister builds in place (no bld/<job>/ isolation —
# a Quartus 17 / sys-framework constraint), so its variants cannot build
# in parallel; one at a time.
VARIANTS := $(sort $(patsubst variants/%.mk,%,$(wildcard variants/*.mk)))
DEFAULT_VARIANT := mister
STICKY_VARIANT := $(strip $(shell cat $(ROOT_DIR)/.variant 2>/dev/null))
ifeq ($(strip $(VARIANT)),)
override VARIANT := $(if $(filter $(STICKY_VARIANT),$(VARIANTS)),$(STICKY_VARIANT),$(DEFAULT_VARIANT))
endif
$(if $(filter $(VARIANT),$(VARIANTS)),,$(error Unknown VARIANT '$(VARIANT)' — known mister variants: $(VARIANTS)))

# Per-variant CPU netlist — the mister config is DUAL-ISSUE (--decoders=2
# --lanes=2): the DE10-Nano's A6/I7 part has the area and speed-grade
# headroom the Pocket lacks.  Same module name / AXI interface — only the
# file differs (see src/fpga/vendor/vexriscv/configs/$(VARIANT).cfg).
VEXII_OUT  = $(VEXII_DIR)/VexiiRiscv_$(VARIANT).v

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

# Firmware (bootloader + os.bin) runs in the SAME Docker container as the
# pocket target's — one riscv64-unknown-elf toolchain for every target, so
# an os.bin never depends on which host (or which target) built it.  LTO
# objects are compiler-locked, so mixing host and container objects is a
# hard error (the firmware Makefile's .ccid guard).  Set
# USE_FIRMWARE_CONTAINER=0 to use the host toolchain instead.
USE_FIRMWARE_CONTAINER ?= 1
ifeq ($(USE_FIRMWARE_CONTAINER),1)
FW_MAKE = bash $(ROOT_DIR)/tools/firmware-container.sh
else
FW_MAKE = $(MAKE) -C $(FW_DIR)
endif

# CPU netlist generation runs in the shared vexii container by default
# (mirrors the pocket target) — no host JDK/sbt needed.  Set
# USE_VEXII_CONTAINER=0 to use host sbt instead.
USE_VEXII_CONTAINER ?= 1

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

# ── Feature include-list: variants/<name>.mk ─────────────────────────
# The variant file owns the ADDITIVE module list (a feature is PRESENT iff
# its INCLUDE_* macro is listed) — see variants/mister.mk for the full
# rationale.  Forwarded to quartus_map as --verilog_macro=<NAME>.
DEFS :=
include variants/$(VARIANT).mk
INCLUDE_DEFS := $(DEFS)
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
	@printf "$(C_HEAD)[cpu]$(C_RESET) Generating VexiiRiscv ($(VARIANT), file missing)...\n"
ifeq ($(USE_VEXII_CONTAINER),1)
	@bash $(ROOT_DIR)/tools/vexii-container.sh $(VARIANT)
else
	@cd $(VEXII_DIR) && bash ../generate_vexii.sh $(VARIANT)
endif
	@printf "$(C_OK)[cpu] Done$(C_RESET)\n"

cpu: $(VEXII_OUT)

# ── Bootloader / OS / Firmware ──────────────────────────────────────
#
#   bootloader — firmware.mif into this dir (Quartus altsyncram init)
#   os         — os.bin; deployed to the MiSTer as boot.rom
#   firmware   — bootloader → os → MIF patch of an existing .sof
bootloader:
	@printf "$(C_HEAD)[boot]$(C_RESET) Building bootloader (firmware.mif)...\n"
	@$(FW_MAKE) install TARGET=$(TARGET)
	@printf "$(C_OK)[boot] Done$(C_RESET)\n"

os:
	@printf "$(C_HEAD)[os]$(C_RESET) Building os.bin...\n"
	@$(FW_MAKE) os.bin TARGET=$(TARGET)
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

# Root-delegated aliases: mister has a single variant and no bld/<job>/
# isolation, so "all variants" degenerates to the one build/sweep.
build-all: build
sweep-all: sweep

# Full compile with fresh placement.  Quartus emits output_files/mister.rbf
# directly (GENERATE_RBF_FILE ON) — no reverse_bits step; MiSTer loads a
# plain .rbf.
#
# Explicit stages (not `quartus_sh --flow compile`) so the feature
# include-list reaches synthesis: --verilog_macro is only valid on
# quartus_map.  The sys/build_id.tcl PRE_FLOW script still runs ahead of
# quartus_map (stamps build_id.v); quartus_asm emits the .rbf.
build: $(VEXII_OUT) bootloader build_id.v
	@# Apply the persisted seed.  Rewriting only on a real difference keeps a
	@# clean tree clean (mister.qsf is tracked, unlike the Pocket's generated
	@# per-job qsf).
	@if [ -f "$(SEED_FILE)" ]; then \
		cur=$$(awk '/^set_global_assignment -name SEED /{print $$NF}' $(PROJECT).qsf); \
		if [ "$$cur" != "$(SEED)" ]; then \
			sed -i 's/^set_global_assignment -name SEED .*/set_global_assignment -name SEED $(SEED)/' $(PROJECT).qsf; \
			printf "$(C_HEAD)[fpga]$(C_RESET) seed $$cur → $(SEED) (from $(SEED_FILE))\n"; \
		fi; \
	fi
	@# Same stale-seed guard as the Pocket flow: a stored seed is only
	@# meaningful for the exact netlist it was ranked on.  Warn, don't fail —
	@# a deliberate rebuild mid-work is fine, shipping one is not.
	@if [ -f "$(SEED_FILE).src" ] && [ -f $(ROOT_DIR)/tools/netlist_hash.sh ]; then \
		. $(ROOT_DIR)/tools/netlist_hash.sh; \
		now=$$(netlist_hash "$(PROJECT).qsf"); \
		want=$$(cat "$(SEED_FILE).src"); \
		if [ "$$now" != "$$want" ]; then \
			printf "$(C_ERR)[fpga] WARNING: netlist changed since mister was swept —$(C_RESET)\n"; \
			printf "$(C_ERR)        seed $(SEED) is a placement lottery ticket on this netlist.$(C_RESET)\n"; \
			printf "$(C_ERR)        Re-run: make sweep TARGET=mister (then build) before shipping.$(C_RESET)\n"; \
		fi; \
	fi
	@printf "$(C_HEAD)[fpga]$(C_RESET) Clean compile (Quartus 17.0.x, seed $(SEED))...\n"
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
# GUARD (2026-08-09): os.bin and the bitstream's BRAM bootloader are two halves
# of ONE link.  start.S bakes ABSOLUTE addresses of os.bin symbols -- notably
# `la t0, irq_handler` and `la t0, syscall_dispatch` -- and mtvec is written
# once, in BRAM, and never re-pointed.  So ANY change to os.bin's size relocates
# those symbols and the baked vectors dispatch into the wrong code for the whole
# runtime.  Removing three of_term_printf calls shrank os.bin 512 B and moved
# both vectors -384 B: blank screen + endless trap/reset loop, HW-reproduced.
# `make os` rebuilds ONLY os.bin, so `make os && make deploy` ships a new half
# against a stale one.  The BOOT_ABI guard in boot.c does NOT catch it -- it
# hashes TARGET + six memory-map #defines (Makefile:61), i.e. the memory MAP,
# not the LAYOUT, so it matches and passes silently.
# Until the vectors move into the OSE2 image-carried metadata (the proper fix),
# refuse to ship a pair that cannot have been baked together.
#
# NOTE ON PLACEMENT: this rule MUST stay ABOVE `deploy`.  The push half of
# `deploy` lives in an `ifeq ($(MISTER_IP),)` block further down, and make
# attaches those recipe lines to whatever target was defined LAST.  Defining
# deploy-pair-check between `deploy` and that ifeq (as this rule originally
# was) silently hands the push block to the GUARD -- which is a prerequisite
# of deploy, so it runs FIRST and scp's the PREVIOUS contents of $(BUILD_OUT),
# then deploy stages the fresh ones afterwards.  The device ends up one build
# behind and a second `make deploy` appears to "fix" it.  Keep this rule here.
deploy-pair-check:
	@sof=output_files/$(PROJECT).sof; osbin=$(FW_DIR)/bld/mister/os.bin; \
	if [ -f "$$osbin" ] && [ -f "$$sof" ] && [ "$$osbin" -nt "$$sof" ]; then \
		printf "$(C_ERR)[deploy] REFUSING: os.bin is NEWER than the .sof.$(C_RESET)\n"; \
		printf "         The .sof carries the BRAM bootloader baked from an OLDER\n"; \
		printf "         os.bin, and the two are one link: the bootloader holds\n"; \
		printf "         ABSOLUTE addresses of irq_handler and syscall_dispatch, and\n"; \
		printf "         mtvec is set once in BRAM and never re-pointed.  Shipping\n"; \
		printf "         them apart points those vectors at the wrong code for the\n"; \
		printf "         whole runtime: blank screen + endless trap/reset loop.\n"; \
		printf "         (BOOT_ABI does not catch this -- it hashes the memory MAP,\n"; \
		printf "         not the LAYOUT, so it matches and passes silently.)\n"; \
		printf "$(C_CMD)         Run: make firmware$(C_RESET)  (rebuilds both and re-bakes the MIF)\n"; \
		exit 1; \
	fi

deploy: deploy-pair-check
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
# Same ranking as the Pocket (setup WNS on the 100 MHz clock), but in-place
# serial: mister has no bld/<job>/ isolation, so one fit at a time, seed
# sed-patched into mister.qsf, best seed rebuilt in place.  Two fixes over
# the old form are load-bearing:
#   VARIANT_DEFS — every sweep fit compiles WITH the variant's INCLUDE_*
#     macros.  Without them the sweep ranked (and left in output_files/) a
#     featureless design whose best seed means nothing for the real build.
#   QRUN — every quartus call runs in the same Q17 container as `make
#     build` (empty when USE_QUARTUS_CONTAINER=0 → host PATH).
# `firmware` runs FIRST so the .mif the fits bake in is fresh — a stale
# baked bootloader after a sweep is the classic mister black-boot.
SWEEP_MIN ?= 1
SWEEP_MAX ?= 30

# Stop the serial sweep as soon as a seed CLOSES timing (setup WNS >= 0 with
# hold still clean).  The Pocket variants sit permanently below the wall so
# they must rank the whole range, but mister has headroom: once a seed passes
# there is nothing better to find, and each further fit costs ~15-20 min of
# wall clock for no ranking value.  Set 0 to force the full range.
SWEEP_STOP_ON_PASS ?= 1

sweep: firmware
	@USE_CONTAINER=0 MAXJOBS=1 PROJECT=$(PROJECT) \
		CLOCK_RE='emu|pll|pll_inst.*general\[0\]' \
		VARIANT_DEFS="$(INCLUDE_DEFS)" \
		QRUN='$(Q)' \
		SEED_FILE='$(SEED_FILE)' \
		STOP_ON_PASS='$(SWEEP_STOP_ON_PASS)' \
		bash $(ROOT_DIR)/tools/sweep.sh $(SWEEP_MIN) $(SWEEP_MAX)

# ── Resource / timing report ─────────────────────────────────────────
# Summary of resource usage; FULL=true adds the top ALM-consuming
# entities and the 25 worst setup paths (re-runs quartus_sta).
# QRUN sends the FULL-mode quartus_sta re-run through the same Q17
# container as the build (empty with USE_QUARTUS_CONTAINER=0 → host PATH).
report:
	@chmod +x $(ROOT_DIR)/tools/report.sh 2>/dev/null || true
	@TARGET="$(TARGET)" VARIANT="$(VARIANT)" QRUN='$(Q)' \
		bash $(ROOT_DIR)/tools/report.sh $(PROJECT) $(if $(filter true,$(FULL)),full)

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
	@$(FW_MAKE) -s clean
	@rm -f $(VEXII_OUT)

clean-all: clean

.PHONY: all full help cpu bootloader firmware os compile build build-all test check deploy report sweep sweep-all sdk program package
.PHONY: timing summary clean clean-all
