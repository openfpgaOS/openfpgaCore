#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------

# openfpgaOS Makefile
#
# Quick start:
#   make              Show this help
#   make full         os → build → test
#   make build        Quartus clean compile (auto-runs cpu if missing)
#   make firmware     Bootloader + os.bin + bitstream MIF patch
#   make os           Just os.bin → build/

# ── Target ───────────────────────────────────────────────────────────
# Default target resolution, highest priority first:
#   1. command line:  make build TARGET=mister
#   2. environment:   export TARGET=mister
#   3. .target file:  make use-mister   (gitignored, per-checkout sticky)
#   4. fallback:      pocket
TARGET ?= $(strip $(shell cat .target 2>/dev/null))
ifeq ($(TARGET),)
override TARGET := pocket
endif
TARGET_DIR = src/fpga/targets/$(TARGET)
# Directories only (trailing-slash glob) so a stray file never reads as a target.
TARGETS = $(notdir $(patsubst %/,%,$(wildcard src/fpga/targets/*/)))

# ── Paths ────────────────────────────────────────────────────────────
# The root knows only the SDK-sync inputs and the shared clean targets.
# Each target owns its own build/<target>/ layout and packaging — the
# root delegates `package` and per-target `sdk-runtime.sh` so no target
# name appears here (see docs/ADDING_A_TARGET.md).
CORE_NAME    = ThinkElastic.openfpgaOS
CHIP32_DIR   = src/chip32/$(TARGET)   # cleaned per-target; absent targets no-op
BUILD_DIR    = build
TOOLS_DIR    = tools
DIST_DIR     = dist/core
REVERSE_BITS = $(TOOLS_DIR)/reverse_bits

# ── Colors ───────────────────────────────────────────────────────────
ifneq ($(shell tput colors 2>/dev/null),)
C_LOGO  := \033[96m
C_HEAD  := \033[1m
C_CMD   := \033[93m
C_OK    := \033[32m
C_DIM   := \033[2m
C_RESET := \033[0m
else
C_LOGO  :=
C_HEAD  :=
C_CMD   :=
C_OK    :=
C_DIM   :=
C_RESET :=
endif

# ── Default ──────────────────────────────────────────────────────────
all: help

# ── Help ─────────────────────────────────────────────────────────────
help:
	@printf "$(C_LOGO)"
	@echo "           ___  ___  ___ ___"
	@echo "          / _ \\/ _ \\/ -_) _ \\"
	@echo "          \\___/ .__/\\__/_//_/"
	@echo "         ____/_/  ________"
	@echo "        / __/ _ \\/ ___/ _ |"
	@echo "       / _// ___/ (_ / __ |"
	@echo "      /_/_/_/___\\___/_/ |_|"
	@echo "     / __ \\/ __/"
	@echo "    / /_/ /\\ \\"
	@printf '    \\____/___/  '
	@printf "$(C_CMD)v0.6$(C_RESET) OS\n"
	@printf "$(C_RESET)\n"
	@printf "  $(C_HEAD)Target:$(C_RESET) $(C_CMD)$(TARGET)$(C_RESET) $(C_DIM)(available: $(TARGETS))$(C_RESET)\n\n"
	@printf "  $(C_HEAD)Targets:$(C_RESET)\n"
	@printf "    Every goal below takes $(C_CMD)TARGET=$(C_CMD)<name>$(C_RESET) — e.g. $(C_CMD)make full TARGET=$(firstword $(filter-out $(TARGET),$(TARGETS)) <name>)$(C_RESET)\n"
	@# Target list is generated from the directories under src/fpga/targets/,
	@# each with an optional one-line about.txt — add a target = add a dir.
	@for t in $(TARGETS); do \
		a=""; [ -f src/fpga/targets/$$t/about.txt ] && a="$$(cat src/fpga/targets/$$t/about.txt)"; \
		printf "    $(C_DIM)%-8s %s$(C_RESET)\n" "$$t" "$$a"; \
	done
	@printf "    $(C_DIM)Switching targets auto-rebuilds the firmware objects — no manual clean needed.$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)use-<target>$(C_RESET)      Make a target the default for this checkout $(C_DIM)(stored in .target;$(C_RESET)\n"
	@printf "    $(C_DIM)                       undo with make use-default — built-in default is pocket)$(C_RESET)\n\n"
	@printf "  $(C_HEAD)Build:$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)full$(C_RESET)              cpu → bootloader → os → build      $(C_DIM)(~9 min)$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)build$(C_RESET)             cpu → Quartus compile → bitstream  $(C_DIM)(~7 min)$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)firmware$(C_RESET)          bootloader → os → MIF patch        $(C_DIM)(~10 sec)$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)os$(C_RESET)                Builds os.bin                      $(C_DIM)(~5 sec)$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)clean$(C_RESET)             Remove all build artifacts\n"
	@echo ""
	@printf "  $(C_HEAD)Development:$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)test$(C_RESET)              Run Verilator test suite           $(C_DIM)(~30 sec)$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)check$(C_RESET)             RTL check (Analysis & Synthesis)   $(C_DIM)(~45 sec)$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)timing$(C_RESET)            Show Fmax and slack from last build\n"
	@printf "    $(C_CMD)make $(C_CMD)report$(C_RESET)            Resource summary $(C_DIM)(FULL=true: +top entities, 25 worst paths)$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)sweep $(C_CMD)SEEDS=$(C_CMD)1-30$(C_RESET)  Seed sweep, pick best Fmax         $(C_DIM)(~7 min/seed)$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)sdk $(C_CMD)DEST=$(C_CMD)\"path\"$(C_RESET)   Sync headers + runtime to SDK repo(s)\n"
	@printf "    $(C_CMD)make $(C_CMD)program$(C_RESET)           JTAG program via USB Blaster $(C_DIM)(pocket)$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)deploy$(C_RESET)            Refresh build/<target>/ core artifacts $(C_DIM)(device push lives in the SDK)$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)package$(C_RESET)           SD-card layout → build/<target>/ $(C_DIM)(pocket=APF tree, mister=rbf+boot.rom)$(C_RESET)\n"
	@echo ""

# ── Sticky default target ────────────────────────────────────────────
# `make use-mister` / `make use-pocket` writes .target (gitignored);
# subsequent makes default to it. `make use-default` removes the file.
use-%:
	@test -d src/fpga/targets/$* || { \
		printf "$(C_ERR)Error: unknown target '$*'$(C_RESET) (available: $(TARGETS))\n"; \
		exit 1; \
	}
	@echo $* > .target
	@printf "  $(C_OK)default target$(C_RESET) → $(C_CMD)$*$(C_RESET) (stored in .target)\n"

use-default:
	@rm -f .target
	@printf "  $(C_OK)default target$(C_RESET) → $(C_CMD)pocket$(C_RESET) (built-in)\n"

# ── Target validation ────────────────────────────────────────────────
check-target:
	@test -d $(TARGET_DIR) || { \
		printf "$(C_ERR)Error: unknown target '$(TARGET)'$(C_RESET)\n"; \
		printf "Available: $(TARGETS)\n"; \
		exit 1; \
	}

# ── Full build ───────────────────────────────────────────────────────
full: check-target
	@$(MAKE) -C $(TARGET_DIR) full

# ── Seed sweep (default 1-30) ────────────────────────────────────────
SEEDS ?= 1-30
SWEEP_MIN = $(word 1,$(subst -, ,$(SEEDS)))
SWEEP_MAX = $(word 2,$(subst -, ,$(SEEDS)))

# ── Delegate to target Makefile ──────────────────────────────────────
# Every standard goal is forwarded verbatim to the selected target's
# Makefile (src/fpga/targets/<target>/Makefile).  The shared root names
# no target: a new target is "add a directory implementing this goal
# set" (see docs/ADDING_A_TARGET.md for the contract).  `package` is in
# the list too — each target owns its own packaging layout (pocket = APF
# SD tree, mister = rbf + boot.rom, …).
cpu bootloader firmware os compile build check test timing report program deploy package: check-target
	@$(MAKE) -C $(TARGET_DIR) $@ FULL=$(FULL)

sweep: check-target
	@$(MAKE) -C $(TARGET_DIR) sweep SWEEP_MIN=$(SWEEP_MIN) SWEEP_MAX=$(SWEEP_MAX)

# (package now lives entirely inside each target's Makefile — see the
# delegated-goal list above and docs/ADDING_A_TARGET.md.)

# ── SDK sync ─────────────────────────────────────────────────────────
#
# Push the canonical SDK files from the OS source tree (the source of
# truth) into one or more SDK checkouts. Replaces the SDK's contents:
#
#   src/sdk/include/of_*.h     <- src/firmware/api/of_*.h
#   src/sdk/sdk.mk             <- src/firmware/api/sdk.mk
#   src/sdk/app.ld             <- src/firmware/api/app.ld
#   src/sdk/of_midi.c          <- src/firmware/api/of_midi.c
#   src/sdk/pc/of_sdl2.c       <- src/firmware/api/pc/of_sdl2.c
#   src/sdk/musl/include/      <- src/firmware/musl/include/
#   src/sdk/musl/lib/          <- src/firmware/musl/lib/
#                                  (libc.a, libm.a, crt1.o, crti.o, crtn.o)
#   runtime/…                  <- each target's sdk-runtime.sh (bitstream,
#                                  kernel, loader, .sof — per-target layout)
#   runtime/bank.ofsf          <- SC-55 GM bank (target-agnostic)
#
# Stale files retired in earlier OS-source cleanups (libc/, of_libc.h,
# of_link.h, of_bram.h, of_posix.c, crt/start.S, ...) are removed from
# the SDK so the new musl-static build path takes effect cleanly.
#
sdk: check-target
	@test -n "$(DEST)" || { \
		printf "$(C_ERR)Usage: make sdk DEST=\"path/to/sdk\"$(C_RESET)\n"; \
		exit 1; \
	}
	@test -f src/firmware/musl/lib/libc.a || { \
		printf "$(C_ERR)musl not built -- run src/firmware/musl/build_musl.sh first$(C_RESET)\n"; \
		exit 1; \
	}
	@test -f src/firmware/musl/lib/crt1.o || { \
		printf "$(C_ERR)musl crt1.o missing -- rerun build_musl.sh$(C_RESET)\n"; \
		exit 1; \
	}
	@for dir in $(DEST); do \
		test -d "$$dir/src/sdk" || { \
			printf "$(C_ERR)SDK not found at $$dir$(C_RESET)\n"; \
			continue; \
		}; \
		printf "$(C_HEAD)[sdk]$(C_RESET) $$dir\n"; \
		\
		# Headers — all .h recursively under src/firmware/api/ (includes \
		# the SDL2/ subtree) mirror into the SDK's include/.  --delete so \
		# retired headers vanish from the SDK on their own. \
		rsync -a --delete \
			--include='*/' --include='*.h' --exclude='*' \
			src/firmware/api/ "$$dir/src/sdk/include/"; \
		printf "  $(C_OK)headers + SDL2$(C_RESET) → src/sdk/include/\n"; \
		\
		# OS-owned files at src/sdk/ top level: opt-in SDK sources \
		# (of_*.c, of_*.cpp), build rules (sdk.mk), and linker script \
		# (app.ld).  --delete + --include/--exclude filters make this a \
		# one-way mirror of exactly these patterns. \
		rsync -a --delete \
			--include='of_*.c' --include='of_*.cpp' \
			--include='sdk.mk' --include='app.ld' \
			--exclude='*' \
			src/firmware/api/ "$$dir/src/sdk/"; \
		printf "  $(C_OK)sources + sdk.mk + app.ld$(C_RESET) → src/sdk/\n"; \
		\
		# PC SDL backend used by sdk.mk's app_pc target. \
		mkdir -p "$$dir/src/sdk/pc"; \
		rsync -a --delete src/firmware/api/pc/ "$$dir/src/sdk/pc/"; \
		printf "  $(C_OK)pc SDL backend$(C_RESET) → src/sdk/pc/\n"; \
		\
		# musl headers + static library + crt objects \
		mkdir -p "$$dir/src/sdk/musl/include" "$$dir/src/sdk/musl/lib"; \
		rsync -a --delete src/firmware/musl/include/ "$$dir/src/sdk/musl/include/"; \
		cp src/firmware/musl/lib/libc.a    "$$dir/src/sdk/musl/lib/"; \
		cp src/firmware/musl/lib/libm.a    "$$dir/src/sdk/musl/lib/" 2>/dev/null || true; \
		cp src/firmware/musl/lib/crt1.o    "$$dir/src/sdk/musl/lib/"; \
		cp src/firmware/musl/lib/crti.o    "$$dir/src/sdk/musl/lib/"; \
		cp src/firmware/musl/lib/crtn.o    "$$dir/src/sdk/musl/lib/"; \
		printf "  $(C_OK)musl$(C_RESET)           → src/sdk/musl/\n"; \
		\
		# Core metadata JSON (APF manifests).  Keep the SDK-deployed core \
		# description in step with this repo.  Refresh any already-built \
		# demo-core tree and any platform that keeps APF templates — both \
		# discovered per target ([ -d ] guards), so no target is named. \
		mkdir -p "$$dir/dist/sdk/Cores/$(CORE_NAME)"; \
		for f in core.json video.json audio.json input.json data.json variants.json interact.json; do \
			cp "$(DIST_DIR)/$$f" "$$dir/dist/sdk/Cores/$(CORE_NAME)/"; \
			for t in $(TARGETS); do \
				[ -d "$$dir/build/$$t/sdk/Cores/$(CORE_NAME)" ] && \
					cp "$(DIST_DIR)/$$f" "$$dir/build/$$t/sdk/Cores/$(CORE_NAME)/" || true; \
			done; \
		done; \
		for t in $(TARGETS); do \
			[ -d "$$dir/src/sdk/platforms/$$t/templates" ] && \
				cp "$(DIST_DIR)/video.json" "$$dir/src/sdk/platforms/$$t/templates/video.json" || true; \
		done; \
		printf "  $(C_OK)core json$(C_RESET)      → dist/sdk/Cores/$(CORE_NAME)/\n"; \
		\
		# Legacy directories that used to live under src/sdk/ — the \
		# rsync above won't touch them because they're outside its \
		# filter, so nuke them here once. \
		rm -rf  "$$dir/src/sdk/libc" "$$dir/src/sdk/crt"; \
		\
		# Target-specific runtime artifacts (bitstream, kernel, loader, \
		# .sof, …).  Each target exports its OWN via sdk-runtime.sh, run \
		# from the repo root with the destination SDK dir as $$1, so \
		# adding a target needs NO edit here — it just appears in \
		# $(TARGETS).  os.bin is gated on the firmware build stamp inside \
		# each script so a target never publishes another target's kernel. \
		mkdir -p "$$dir/runtime"; \
		absdest=$$(cd "$$dir" && pwd); \
		for t in $(TARGETS); do \
			[ -f src/fpga/targets/$$t/sdk-runtime.sh ] && \
				bash src/fpga/targets/$$t/sdk-runtime.sh "$$absdest" || true; \
		done; \
		\
		# SC-55 General MIDI bank — target-agnostic, shared by all targets. \
		test -f assets/banks/sc55.ofsf && cp assets/banks/sc55.ofsf "$$dir/runtime/bank.ofsf" && \
			printf "  $(C_OK)sc55.ofsf$(C_RESET)      → runtime/bank.ofsf\n" || true; \
		printf "$(C_OK)[sdk] Done$(C_RESET) $$dir\n\n"; \
	done

# ── Clean ────────────────────────────────────────────────────────────
clean:
	@$(MAKE) -C $(TARGET_DIR) clean-all
	@$(MAKE) -C $(CHIP32_DIR) clean 2>/dev/null || true
	@rm -rf $(BUILD_DIR)
	@rm -f $(REVERSE_BITS)

.PHONY: all help check-target full cpu bootloader firmware os compile build check test timing report program sdk deploy
.PHONY: sweep package clean use-default
