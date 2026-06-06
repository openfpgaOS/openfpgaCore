#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------

# Host (PC-native) unit-test harness for the MiSTer firmware FAT stack.
#
# Builds and runs the REAL targets/mister/file.c + save.c (unmodified) over
# the vendored FatFs, with a pread/pwrite block-device shim standing in for
# the hps_bridge sector engine.  Nothing in the firmware tree is touched.
#
#   make run     build fixtures + image + harness, then run the tests
#   make clean   remove all generated artifacts
#
# This directory is self-contained; the firmware Makefile globs nothing here.

# ── Layout ───────────────────────────────────────────────────────────
# test/pc -> targets/mister -> os
MISTER_DIR := ../..
OS_DIR     := $(MISTER_DIR)/../..
HAL_DIR    := $(OS_DIR)/hal
API_DIR    := $(OS_DIR)/../api
FATFS_DIR  := $(MISTER_DIR)/fatfs

# SDK image builder (prebuilt; rebuilt on demand if missing).
SDK_MISTER := /home/alberto/Repos/openfpgaSDK/src/sdk/platforms/mister
MKIMAGE    := $(SDK_MISTER)/.mkimage

BUILD := build
IMAGE := $(BUILD)/test.vhd
SIZE_MB ?= 64

# ── Toolchain ────────────────────────────────────────────────────────
CC ?= cc
# Stub headers must come FIRST so regs.h/cache.h/terminal.h shadow the HAL.
INCLUDES := -Istub -I$(MISTER_DIR) -I$(HAL_DIR) -I$(API_DIR) -I.
# Force-include the stub regs.h: HAL headers reach regs.h via a quote-include
# ("regs.h"), which the preprocessor resolves against the *including* header's
# own directory (hal/) BEFORE -Istub.  Pre-defining the OFOS_REGS_H guard from
# our stub makes the later hal/regs.h body a no-op, so the rv32-only inline
# asm (fence, csrr) never reaches the host assembler.
FORCE_REGS := -include stub/regs.h
WARN     := -Wall -Wextra
SAN      := -fsanitize=address,undefined -fno-sanitize-recover=all
CFLAGS   := -O1 -g $(WARN) $(SAN) $(INCLUDES) $(FORCE_REGS) -std=gnu11
LDFLAGS  := $(SAN)

# ── Sources ──────────────────────────────────────────────────────────
# Firmware sources under test (compiled unmodified, in place).
FW_SRCS := $(MISTER_DIR)/file.c \
           $(MISTER_DIR)/save.c \
           $(HAL_DIR)/disk.c \
           $(MISTER_DIR)/disk_boot.c \
           $(FATFS_DIR)/ff.c \
           $(FATFS_DIR)/ffunicode.c

# Host harness sources.
HOST_SRCS := test_main.c \
             blockdev_host.c \
             stub/stub.c

FW_OBJS   := $(patsubst %.c,$(BUILD)/fw/%.o,$(notdir $(FW_SRCS)))
HOST_OBJS := $(patsubst %.c,$(BUILD)/host/%.o,$(notdir $(HOST_SRCS)))

HARNESS := $(BUILD)/fat_tests
GENFIX  := $(BUILD)/genfix

# ── Default ──────────────────────────────────────────────────────────
.PHONY: all run clean
all: $(HARNESS) $(IMAGE)

run: $(HARNESS) $(IMAGE)
	@echo "── running MiSTer FAT-stack host tests ──"
	@$(HARNESS) $(IMAGE)

# ── Harness link ─────────────────────────────────────────────────────
$(HARNESS): $(FW_OBJS) $(HOST_OBJS)
	$(CC) $(LDFLAGS) -o $@ $^

# Per-source object rules (flat output dirs, sources live in 4 trees).
$(BUILD)/fw/file.o: $(MISTER_DIR)/file.c | $(BUILD)/fw
	$(CC) $(CFLAGS) -c -o $@ $<
$(BUILD)/fw/save.o: $(MISTER_DIR)/save.c | $(BUILD)/fw
	$(CC) $(CFLAGS) -c -o $@ $<
$(BUILD)/fw/disk.o: $(HAL_DIR)/disk.c | $(BUILD)/fw
	$(CC) $(CFLAGS) -c -o $@ $<
$(BUILD)/fw/disk_boot.o: $(MISTER_DIR)/disk_boot.c | $(BUILD)/fw
	$(CC) $(CFLAGS) -c -o $@ $<
$(BUILD)/fw/ff.o: $(FATFS_DIR)/ff.c | $(BUILD)/fw
	$(CC) $(CFLAGS) -Wno-unused-parameter -c -o $@ $<
$(BUILD)/fw/ffunicode.o: $(FATFS_DIR)/ffunicode.c | $(BUILD)/fw
	$(CC) $(CFLAGS) -c -o $@ $<

$(BUILD)/host/test_main.o: test_main.c | $(BUILD)/host
	$(CC) $(CFLAGS) -c -o $@ $<
$(BUILD)/host/blockdev_host.o: blockdev_host.c | $(BUILD)/host
	$(CC) $(CFLAGS) -c -o $@ $<
$(BUILD)/host/stub.o: stub/stub.c | $(BUILD)/host
	$(CC) $(CFLAGS) -c -o $@ $<

# ── Fixtures + image ─────────────────────────────────────────────────
$(GENFIX): genfix.c fixture_pattern.h | $(BUILD)
	$(CC) $(WARN) -O1 -I. -o $@ genfix.c

# mkimage is prebuilt in the SDK; rebuild it if it has gone missing.
$(MKIMAGE):
	cc -O2 -I"$(SDK_MISTER)" -o "$(MKIMAGE)" \
		"$(SDK_MISTER)/mkimage.c" \
		"$(SDK_MISTER)/fatfs/ff.c" \
		"$(SDK_MISTER)/fatfs/ffunicode.c"

$(IMAGE): $(GENFIX) $(MKIMAGE) | $(BUILD)
	@rm -rf $(BUILD)/fix && mkdir -p $(BUILD)/fix
	$(GENFIX) $(BUILD)/fix
	"$(MKIMAGE)" $(IMAGE) $(SIZE_MB) \
		$(BUILD)/fix/os.ini=/os.ini \
		$(BUILD)/fix/app.elf=/app.elf \
		$(BUILD)/fix/data1.bin=/assets/data1.bin \
		$(BUILD)/fix/level2.dat=/assets/level2.dat

# ── Dirs ─────────────────────────────────────────────────────────────
$(BUILD) $(BUILD)/fw $(BUILD)/host:
	@mkdir -p $@

clean:
	rm -rf $(BUILD)
