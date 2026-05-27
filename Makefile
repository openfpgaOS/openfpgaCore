# openfpgaOS Makefile
#
# Quick start:
#   make              Show this help
#   make full         os → build → test
#   make build        Quartus clean compile (auto-runs cpu if missing)
#   make firmware     Bootloader + os.bin + bitstream MIF patch
#   make os           Just os.bin → build/

# ── Target ───────────────────────────────────────────────────────────
TARGET ?= pocket
TARGET_DIR = src/fpga/targets/$(TARGET)
TARGETS = $(notdir $(wildcard src/fpga/targets/*))

# ── Paths ────────────────────────────────────────────────────────────
CORE_NAME    = ThinkElastic.openfpgaOS
OS_DIR       = src/firmware/os
CHIP32_DIR   = src/chip32/$(TARGET)
BUILD_DIR    = build
RELEASE_DIR  = $(BUILD_DIR)/Cores/$(CORE_NAME)
ASSETS_DIR   = $(BUILD_DIR)/Assets/openfpgaos/common
TOOLS_DIR    = tools
DIST_DIR     = dist/core
ANALOGIZER_CFG = dist/common/analogizer.cfg
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
	@echo "    \\____/___/  \033[93mv0.5\033[0m OS"
	@printf "$(C_RESET)\n"
	@printf "  $(C_HEAD)Target: $(TARGET)$(C_RESET)\n\n"
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
	@printf "    $(C_CMD)make $(C_CMD)sweep $(C_CMD)SEEDS=$(C_CMD)1-30$(C_RESET)  Seed sweep, pick best Fmax         $(C_DIM)(~7 min/seed)$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)sdk $(C_CMD)DEST=$(C_CMD)\"path\"$(C_RESET)   Sync headers + runtime to SDK repo(s)\n"
	@printf "    $(C_CMD)make $(C_CMD)program$(C_RESET)           JTAG program via USB Blaster\n"
	@echo ""

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
cpu bootloader firmware os compile build check test timing program: check-target
	@$(MAKE) -C $(TARGET_DIR) $@

sweep: check-target
	@$(MAKE) -C $(TARGET_DIR) sweep SWEEP_MIN=$(SWEEP_MIN) SWEEP_MAX=$(SWEEP_MAX)

# `flash` removed — use `make firmware` instead. The two targets did
# the same thing (firmware build + MIF patch + bitstream re-install).

# ── Package ──────────────────────────────────────────────────────────
package: $(REVERSE_BITS) package-dirs package-bitstream package-chip32 package-firmware package-json package-analogizer-config package-platform package-icon package-install
	@echo ""
	@printf "  $(C_OK)Package ready$(C_RESET) → $(BUILD_DIR)/\n"
	@echo ""
	@tree -L 4 $(BUILD_DIR) 2>/dev/null || find $(BUILD_DIR) -type f | sort

# Lightweight package (skip checks, for use after flash)
package-only: $(REVERSE_BITS) package-dirs package-bitstream package-chip32 package-firmware package-json package-analogizer-config package-platform package-icon package-install

package-dirs:
	@rm -rf $(BUILD_DIR)
	@mkdir -p $(RELEASE_DIR) $(BUILD_DIR)/Platforms/_images $(ASSETS_DIR)

$(REVERSE_BITS): tools/reverse_bits.c
	@gcc -O2 -o $@ $<

package-bitstream: $(REVERSE_BITS)
	@$(REVERSE_BITS) $(TARGET_DIR)/output_files/ap_core.rbf $(RELEASE_DIR)/bitstream.rbf_r

package-chip32:
	@cp $(CHIP32_DIR)/loader.bin $(RELEASE_DIR)/loader.bin

package-firmware:
	@cp $(OS_DIR)/os.bin $(RELEASE_DIR)/os.bin
	@cp $(OS_DIR)/os.bin $(ASSETS_DIR)/os.bin

package-json:
	@for f in core.json video.json audio.json input.json data.json variants.json interact.json; do \
		cp $(DIST_DIR)/$$f $(RELEASE_DIR)/; \
	done

package-analogizer-config:
	@cp $(ANALOGIZER_CFG) $(ASSETS_DIR)/analogizer.cfg

package-platform:
	@cp dist/platforms/*.json $(BUILD_DIR)/Platforms/
	@cp dist/platforms/_images/*.bin $(BUILD_DIR)/Platforms/_images/

package-icon:
	@test -f dist/core/icon.bin && cp dist/core/icon.bin $(RELEASE_DIR)/ || true

define INSTALL_TEXT
openfpgaOS — Installation

1. Copy the contents of this folder to your SD card root (merge with existing)
2. Power on your Analogue Pocket → Cores → $(CORE_NAME)

For game development, use the SDK: https://github.com/ThinkElastic/openfpgaOS-SDK
endef
export INSTALL_TEXT

package-install:
	@echo "$$INSTALL_TEXT" > $(BUILD_DIR)/INSTALL.txt

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
#   runtime/bitstream.rbf_r    <- pocket build output
#   runtime/os.bin             <- kernel build output
#   runtime/loader.bin         <- chip32 loader
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
		# Shared Analogizer config seed. The SDK demo core maps this file \
		# through nonvolatile data slot 8; putting it under dist/sdk makes \
		# `make -C src/apps release/copy` deploy it to Assets/openfpgaos/common. \
		mkdir -p "$$dir/dist/sdk/Assets/openfpgaos/common"; \
		cp $(ANALOGIZER_CFG) "$$dir/dist/sdk/Assets/openfpgaos/common/analogizer.cfg"; \
		mkdir -p "$$dir/runtime"; \
		cp $(ANALOGIZER_CFG) "$$dir/runtime/analogizer.cfg"; \
		if [ -d "$$dir/build/sdk/Assets/openfpgaos/common" ]; then \
			cp $(ANALOGIZER_CFG) "$$dir/build/sdk/Assets/openfpgaos/common/analogizer.cfg"; \
		fi; \
		printf "  $(C_OK)analogizer.cfg$(C_RESET) → dist/sdk/Assets/openfpgaos/common/\n"; \
		\
		# Legacy directories that used to live under src/sdk/ — the \
		# rsync above won't touch them because they're outside its \
		# filter, so nuke them here once. \
		rm -rf  "$$dir/src/sdk/libc" "$$dir/src/sdk/crt"; \
		\
		# Runtime binaries (bitstream, kernel, loader) \
		mkdir -p "$$dir/runtime"; \
		if [ -f $(TARGET_DIR)/output_files/ap_core.rbf ]; then \
			$(REVERSE_BITS) $(TARGET_DIR)/output_files/ap_core.rbf "$$dir/runtime/bitstream.rbf_r"; \
			printf "  $(C_OK)bitstream$(C_RESET)      → runtime/\n"; \
		fi; \
		test -f $(OS_DIR)/os.bin && cp $(OS_DIR)/os.bin "$$dir/runtime/" && \
			printf "  $(C_OK)os.bin$(C_RESET)         → runtime/\n" || true; \
		test -f $(CHIP32_DIR)/loader.bin && cp $(CHIP32_DIR)/loader.bin "$$dir/runtime/" && \
			printf "  $(C_OK)loader.bin$(C_RESET)     → runtime/\n" || true; \
		\
		# SC-55 General MIDI bank (for of_midi playback). \
		test -f assets/banks/sc55.ofsf && cp assets/banks/sc55.ofsf "$$dir/runtime/bank.ofsf" && \
			printf "  $(C_OK)sc55.ofsf$(C_RESET)      → runtime/bank.ofsf\n" || true; \
		\
		# .sof for JTAG reset via quartus_pgm. scripts/debug.sh in the \
		# SDK consumer reloads this over JTAG between push and stream so \
		# the core comes up clean before the new ELF runs. \
		test -f $(TARGET_DIR)/output_files/ap_core.sof && \
			cp $(TARGET_DIR)/output_files/ap_core.sof "$$dir/runtime/" && \
			printf "  $(C_OK)ap_core.sof$(C_RESET)    → runtime/ (JTAG reset)\n" || true; \
		printf "$(C_OK)[sdk] Done$(C_RESET) $$dir\n\n"; \
	done

# ── Clean ────────────────────────────────────────────────────────────
clean:
	@$(MAKE) -C $(TARGET_DIR) clean-all
	@$(MAKE) -C $(CHIP32_DIR) clean 2>/dev/null || true
	@rm -rf $(BUILD_DIR)
	@rm -f $(REVERSE_BITS)

.PHONY: all help check-target full cpu bootloader firmware os compile build check test timing program sdk
.PHONY: sweep
.PHONY: package package-only package-dirs package-bitstream package-chip32
.PHONY: package-firmware package-json package-analogizer-config package-platform package-icon package-install
.PHONY: clean
