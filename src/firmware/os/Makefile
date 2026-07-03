#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------

# openfpgaOS Kernel Makefile
# Builds bootloader (BRAM) + OS kernel (SDRAM)

# Toolchain
CROSS ?= $(shell which riscv64-unknown-elf-gcc >/dev/null 2>&1 && echo riscv64-unknown-elf- || echo riscv64-elf-)
CC = $(CROSS)gcc
AS = $(CROSS)gcc
LD = $(CROSS)gcc
OBJCOPY = $(CROSS)objcopy
OBJDUMP = $(CROSS)objdump
SIZE = $(CROSS)size
NM = $(CROSS)nm

# Target
# Binary name
FW_TARGET = firmware

# Target platform (inherited from top-level Makefile)
# Default target: command line > environment > repo-root .target file
# (written by `make use-<target>` at the root) > pocket.
TARGET ?= $(strip $(shell cat ../../../.target 2>/dev/null))
ifeq ($(TARGET),)
override TARGET := pocket
endif

# Directories
FPGA_CORE_DIR = ../../fpga/targets/$(TARGET)
HAL_DIR = hal
TARGET_DIR = targets/$(TARGET)
KERNEL_DIR = kernel
BOOT_DIR = targets/$(TARGET)/boot
API_DIR = ../api
SHARED_DIR = shared
LIB_DIR = ../lib
ISO9660_DIR = $(LIB_DIR)/iso9660

# Compiler flags
ARCH = rv32imafc
ABI = ilp32f
CFLAGS = -march=$(ARCH) -mabi=$(ABI) -Os -Wall -Wextra -fdiagnostics-color=always
CFLAGS += -ffreestanding -nostdlib -nostartfiles
CFLAGS += -ffunction-sections -fdata-sections
CFLAGS += -fno-builtin
CFLAGS += -fsingle-precision-constant
CFLAGS += $(EXTRA_CFLAGS)
# Cache-line align hot code so I$ refills are one transaction.
CFLAGS += -falign-functions=64 -falign-loops=64
# Don't auto-rewrite plain loops as memset/memcpy calls.
CFLAGS += -fno-tree-loop-distribute-patterns
CFLAGS += -I. -I$(HAL_DIR) -I$(TARGET_DIR) -I$(KERNEL_DIR) -I$(BOOT_DIR) -I$(API_DIR) -I$(SHARED_DIR) -I$(ISO9660_DIR)
CFLAGS += -MMD -MP
LTO_FLAGS = -flto=2

# OS kernel flags (optimized for speed)
OS_CFLAGS = $(filter-out -Os,$(CFLAGS)) -O2 $(LTO_FLAGS) -std=gnu11

# Assembler flags
ASFLAGS = -march=$(ARCH)_zicsr -mabi=$(ABI)

# Linker flags
LDFLAGS = -march=$(ARCH) -mabi=$(ABI)
LDFLAGS += -T os.ld -nostdlib -nostartfiles
LDFLAGS += -Wl,--gc-sections -Wl,--no-warn-rwx-segments
LDFLAGS += -Wl,-Map=$(FW_TARGET).map
LDFLAGS += $(LTO_FLAGS)

# Libraries
LIBGCC = $(shell $(CC) -march=$(ARCH) -mabi=$(ABI) -print-libgcc-file-name)
LIBS = $(LIBGCC)

# musl libc — provides memcmp/strncpy/etc. that the bootloader+OS call.
# Auto-built on first use by build_musl.sh (downloads musl 1.2.5, patches
# for ilp32f, cross-compiles).  Treated as a required dependency so a fresh
# clone builds end-to-end; see the $(MUSL_LIBC) rule below.
MUSL_DIR = ../musl
MUSL_LIBC = $(MUSL_DIR)/lib/libc.a
LIBS += $(MUSL_LIBC)
CFLAGS += -I$(MUSL_DIR)/include
OS_CFLAGS += -I$(MUSL_DIR)/include

# Source files - Bootloader (BRAM)
BOOT_SRCS_C = $(BOOT_DIR)/boot.c
BOOT_SRCS_S = $(BOOT_DIR)/start.S

# Source files - HAL (portable)
#
# sw_mixer.c is always linked now: ONE os.bin runs on every bitstream and
# picks the mixer backend at boot.  of_init() reads the HW_FEATURES
# OF_HW_MIXER_HW bit and sets of_mixer_use_sw — 0 (HW audio_mixer.v MMIO) on
# OS25/MiSTer/default Pocket, 1 (CPU software mixer) on the OS30 Pocket
# variant that cuts audio_mixer.v.  The regs.h MIX_* macros branch on that
# flag at runtime, so hal/mixer.c and targets/pocket/audio.c stay byte-
# identical source.  No MIXER= flag is required.
HAL_SRCS = $(HAL_DIR)/hal.c \
           $(HAL_DIR)/platform.c \
           $(HAL_DIR)/cache.c \
           $(HAL_DIR)/mixer.c \
           $(HAL_DIR)/sw_mixer.c \
           $(HAL_DIR)/net.c \
           $(HAL_DIR)/link.c \
           $(HAL_DIR)/codec.c \
           $(HAL_DIR)/lzw.c \
           $(HAL_DIR)/disk.c \
           ../api/of_smp_tables.c

# Optional debug force-override: MIXER=sw pins of_mixer_use_sw=1 (CPU software
# mixer) regardless of the HW caps bit, for testing the SW path on a core that
# still has audio_mixer.v.  Normal builds leave this unset and select at boot.
ifeq ($(MIXER),sw)
CFLAGS    += -DOF_MIXER_FORCE_SW
OS_CFLAGS += -DOF_MIXER_FORCE_SW
endif

HAL_SRCS_S = $(HAL_DIR)/fast_mem.S

# Source files - HAL (target-specific)
TARGET_SRCS = $(TARGET_DIR)/video.c \
              $(TARGET_DIR)/audio.c \
              $(TARGET_DIR)/input.c \
              $(TARGET_DIR)/save.c \
              $(TARGET_DIR)/file.c \
              $(TARGET_DIR)/analogizer.c \
              $(TARGET_DIR)/terminal.c \
              $(TARGET_DIR)/timer.c \
              $(TARGET_DIR)/disk_boot.c \

# SNAC is a Pocket peripheral driver, not a HAL contract the kernel
# links against — only targets with the Analogizer/SNAC port build it
# (sim models the Pocket periph, so it builds the same driver).
# analogizer.c, by contrast, IS a HAL contract: kernel/syscall.c
# answers the app-facing of_analogizer_* ecalls through it, so every
# target needs at least the "not present" implementation.
ifneq ($(filter $(TARGET),pocket sim),)
TARGET_SRCS += $(TARGET_DIR)/snac.c
endif

# MiSTer: block-device layer + vendored FatFs (file/save HAL backend)
ifeq ($(TARGET),mister)
TARGET_SRCS += $(TARGET_DIR)/blockdev.c \
               $(TARGET_DIR)/fatfs/ff.c \
               $(TARGET_DIR)/fatfs/ffunicode.c
endif

# Source files - Kernel
KERNEL_SRCS = $(KERNEL_DIR)/main.c \
              $(KERNEL_DIR)/syscall.c \
              $(KERNEL_DIR)/loader.c \
              $(KERNEL_DIR)/config.c \
              $(KERNEL_DIR)/misaligned.c \
              $(KERNEL_DIR)/caps_table.c \
              $(KERNEL_DIR)/services_table.c \
              $(KERNEL_DIR)/of_malloc.c \
              $(KERNEL_DIR)/irq.c \
              $(KERNEL_DIR)/bank_preload.c \
              $(ISO9660_DIR)/lib9660.c \
              $(ISO9660_DIR)/iso9660_vfs.c

# All sources
ALL_C_SRCS = $(BOOT_SRCS_C) $(HAL_SRCS) $(TARGET_SRCS) $(KERNEL_SRCS)
OBJS = $(ALL_C_SRCS:.c=.o) $(BOOT_SRCS_S:.S=.o) $(HAL_SRCS_S:.S=.o)

# ── Target-switch detection ──────────────────────────────────────────
# Shared objects (hal/kernel/api) are compiled against the selected
# target's target_platform.h, so reusing them across targets would link
# a wrong memory map.  The .last_target stamp rule clears stale objects
# when TARGET changes — `make TARGET=mister` after a pocket build (or
# sim, in any order) Just Works without a manual clean.  Same-target
# rebuilds stay fully incremental.
#
# This is a real RECIPE on purpose (an earlier version was a parse-time
# $(shell rm ...), which executed during `make -n`/`make help`/`make
# clean` and raced parallel sub-makes).  Recipe-time means dry runs only
# print it, -j orders it before any compile via the order-only prereq,
# and it cleans with globs over EVERY target's objects — including
# target-exclusive ones (e.g. mister's fatfs/*.o) that the new TARGET's
# $(OBJS) wouldn't name.
# The stamp rule below is textually the first rule in this Makefile —
# keep `make` (no goal) building the firmware, not the stamp.
.DEFAULT_GOAL := all

RETARGET_GLOBS = hal/*.o kernel/*.o boot/*.o targets/*/*.o \
                 targets/*/boot/*.o targets/*/fatfs/*.o \
                 ../api/*.o ../lib/iso9660/*.o
.last_target: FORCE
	@if [ "$$(cat .last_target 2>/dev/null)" != "$(TARGET)" ]; then \
		rm -f $(RETARGET_GLOBS) $(RETARGET_GLOBS:.o=.d) \
		      $(FW_TARGET).elf $(FW_TARGET).bin $(FW_TARGET).map os.bin; \
		echo $(TARGET) > .last_target; \
	fi

FORCE:
.PHONY: FORCE

# RAM size for MIF generation
RAM_WORDS = 8192

# Default target
all: $(FW_TARGET).bin $(FW_TARGET).mif $(FW_TARGET).lst os.bin
	$(SIZE) $(FW_TARGET).elf

# Every object (and the link) is ordered after the retarget stamp check.
$(OBJS): | .last_target

# Link
$(FW_TARGET).elf: $(OBJS) os.ld $(MUSL_LIBC) | .last_target
	$(LD) $(LDFLAGS) -o $@ $(OBJS) $(LIBS)

# Auto-build musl libc on first use (downloads + cross-compiles ~1 min).
# The build runs with whatever toolchain is on PATH — inside the firmware
# container that's riscv64-unknown-elf-gcc with picolibc headers.  Output
# is gitignored.
$(MUSL_LIBC):
	@echo "[musl] libc.a missing — building (~1 min, downloads musl 1.2.5)..."
	@bash $(MUSL_DIR)/build_musl.sh

# Full binary
$(FW_TARGET).bin: $(FW_TARGET).elf
	$(OBJCOPY) -O binary $< $@

# BRAM image for MIF
$(FW_TARGET).mif: $(FW_TARGET).elf
	$(OBJCOPY) -O binary \
		--only-section=.boot \
		--only-section=.boot_data \
		--only-section=.boot_bss \
		--only-section=.fasttext \
		--only-section=.fastdata \
		$< boot.bin
	@echo "-- Firmware RAM initialization - $(RAM_WORDS) x 32-bit words" > $@
	@echo "-- Auto-generated from boot sections" >> $@
	@echo "" >> $@
	@echo "WIDTH=32;" >> $@
	@echo "DEPTH=$(RAM_WORDS);" >> $@
	@echo "" >> $@
	@echo "ADDRESS_RADIX=DEC;" >> $@
	@echo "DATA_RADIX=HEX;" >> $@
	@echo "" >> $@
	@echo "CONTENT BEGIN" >> $@
	@hexdump -v -e '1/4 "%08X\n"' boot.bin | awk '{printf "%d : %s;\n", NR-1, $$1}' >> $@
	@LAST=$$(hexdump -v -e '1/4 "%08X\n"' boot.bin | wc -l); \
	if [ $$LAST -lt $(RAM_WORDS) ]; then \
		echo "[$$LAST..$$(($(RAM_WORDS)-1))] : 00000013;" >> $@; \
	fi
	@echo "END;" >> $@
	@echo "Generated $@ with $$(hexdump -v -e '1/4 "%08X\n"' boot.bin | wc -l) words of BRAM image"

# OS binary (loaded by bootloader via deferload).  Includes the
# split-memory sections: .text stays in CRAM0, .osdata_rodata and
# .osdata_data have their LMA packed into CRAM0 right after .text
# (AT> CRAM0 in os.ld).  The bootloader splits the blob at runtime
# and streams the .rodata / .data portion directly to its SDRAM VMA.
os.bin: $(FW_TARGET).elf
	$(OBJCOPY) -O binary \
		--only-section=.text \
		--only-section=.osdata \
		$< $@
	@python3 $(firstword $(wildcard $(BOOT_DIR)/append_os_crc.py) targets/pocket/boot/append_os_crc.py) $@ \
		$$($(NM) $< | awk '$$3=="os_main"        {print $$1}') \
		$$($(NM) $< | awk '$$3=="__os_bss_start" {print $$1}') \
		$$($(NM) $< | awk '$$3=="__os_bss_end"   {print $$1}')
	@echo "Generated $@ ($$(wc -c < $@) bytes)"

# Disassembly
$(FW_TARGET).lst: $(FW_TARGET).elf
	$(OBJDUMP) -d -S $< > $@

# Compile rules
#
# Boot C sources use tighter function alignment than the rest of
# the firmware (-falign-functions=4 vs =64) because the BRAM budget
# is only 8 KB for .boot + .boot_bss + .fasttext and the 64-byte
# alignment pads push .fasttext past the APP_BRAM_BASE (0x2000)
# assertion.  The boot code isn't performance-critical (runs once),
# so losing the I-cache line alignment is fine.
$(BOOT_DIR)/%.o: $(BOOT_DIR)/%.c
	$(CC) $(filter-out -falign-functions=64 -falign-loops=64,$(CFLAGS)) -falign-functions=4 -falign-loops=4 -c -o $@ $<

$(BOOT_DIR)/%.o: $(BOOT_DIR)/%.S
	$(AS) $(ASFLAGS) -c -o $@ $<

$(HAL_DIR)/%.o: $(HAL_DIR)/%.S
	$(AS) $(ASFLAGS) -c -o $@ $<

$(HAL_DIR)/%.o: $(HAL_DIR)/%.c
	$(CC) $(OS_CFLAGS) -c -o $@ $<

$(TARGET_DIR)/%.o: $(TARGET_DIR)/%.c
	$(CC) $(OS_CFLAGS) -c -o $@ $<

# syscall.c without LTO — LTO strips functions called only via
# runtime-determined if-chains from the assembly trap handler.
$(KERNEL_DIR)/syscall.o: $(KERNEL_DIR)/syscall.c
	$(CC) $(filter-out -flto -flto=%,$(OS_CFLAGS)) -c -o $@ $<

# irq.c without LTO — called from assembly trap handler via function pointer.
$(KERNEL_DIR)/irq.o: $(KERNEL_DIR)/irq.c
	$(CC) $(filter-out -flto -flto=%,$(OS_CFLAGS)) -c -o $@ $<

$(KERNEL_DIR)/%.o: $(KERNEL_DIR)/%.c
	$(CC) $(OS_CFLAGS) -c -o $@ $<

$(ISO9660_DIR)/%.o: $(ISO9660_DIR)/%.c
	$(CC) $(OS_CFLAGS) -c -o $@ $<

# Auto-dependency files (header tracking)
DEPS = $(ALL_C_SRCS:.c=.d)
-include $(DEPS)

# Install MIF to FPGA
install: $(FW_TARGET).mif
	cp $(FW_TARGET).mif $(FPGA_CORE_DIR)/firmware.mif
	@echo "MIF installed to $(FPGA_CORE_DIR)/firmware.mif"

# Clean
clean:
	rm -f $(OBJS) $(FW_TARGET).elf $(FW_TARGET).bin $(FW_TARGET).mif $(FW_TARGET).map $(FW_TARGET).lst
	rm -f boot.bin os.bin
	rm -f $(DEPS)
	rm -f .last_target

rebuild: clean all

mem: $(FW_TARGET).elf
	$(SIZE) -A -x $(FW_TARGET).elf

# Run the SDK app portability acceptance checks. See
# tools/check_api_portability.sh for what it verifies. Catches
# regressions in the API contract that would prevent a single SDK
# app .elf from running on multiple targets.
check-api:
	@bash ../../../tools/check_api_portability.sh

.PHONY: all clean rebuild install mem check-api
