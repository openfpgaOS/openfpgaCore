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

# Target
# Binary name
FW_TARGET = firmware

# Target platform (inherited from top-level Makefile)
TARGET ?= pocket

# Directories
FPGA_CORE_DIR = ../../fpga/targets/$(TARGET)
HAL_DIR = hal
TARGET_DIR = targets/$(TARGET)
KERNEL_DIR = kernel
BOOT_DIR = targets/$(TARGET)/boot

# Compiler flags
ARCH = rv32imafc
ABI = ilp32f
CFLAGS = -march=$(ARCH) -mabi=$(ABI) -Os -Wall -Wextra
CFLAGS += -ffreestanding -nostdlib -nostartfiles
CFLAGS += -ffunction-sections -fdata-sections
CFLAGS += -fno-builtin
CFLAGS += -fsingle-precision-constant
CFLAGS += -I. -I$(HAL_DIR) -I$(TARGET_DIR) -I$(KERNEL_DIR) -I$(BOOT_DIR)
CFLAGS += -MMD -MP

# OS kernel flags (optimized for speed)
OS_CFLAGS = $(filter-out -Os,$(CFLAGS)) -O2 -flto -std=gnu11

# Assembler flags
ASFLAGS = -march=$(ARCH)_zicsr -mabi=$(ABI)

# Linker flags
LDFLAGS = -march=$(ARCH) -mabi=$(ABI)
LDFLAGS += -T os.ld -nostdlib -nostartfiles
LDFLAGS += -Wl,--gc-sections -Wl,--no-warn-rwx-segments
LDFLAGS += -Wl,-Map=$(FW_TARGET).map
LDFLAGS += -flto

# Libraries
LIBGCC = $(shell $(CC) -march=$(ARCH) -mabi=$(ABI) -print-libgcc-file-name)
LIBS = $(LIBGCC)

# Check for musl libc.a
MUSL_DIR = ../musl
MUSL_LIBC = $(MUSL_DIR)/lib/libc.a
ifneq ($(wildcard $(MUSL_LIBC)),)
    LIBS += $(MUSL_LIBC)
    CFLAGS += -I$(MUSL_DIR)/include
    OS_CFLAGS += -I$(MUSL_DIR)/include
endif

# Source files - Bootloader (BRAM)
BOOT_SRCS_C = $(BOOT_DIR)/boot.c
BOOT_SRCS_S = $(BOOT_DIR)/start.S

# Source files - HAL (portable)
HAL_SRCS = $(HAL_DIR)/hal.c \
           $(HAL_DIR)/cache.c \
           $(HAL_DIR)/mixer.c \
           $(HAL_DIR)/codec.c \
           $(HAL_DIR)/lzw.c

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
              $(TARGET_DIR)/link.c \
              $(TARGET_DIR)/tile.c

# Source files - Kernel
KERNEL_SRCS = $(KERNEL_DIR)/main.c \
              $(KERNEL_DIR)/syscall.c \
              $(KERNEL_DIR)/loader.c \
              $(KERNEL_DIR)/misaligned.c \
              $(KERNEL_DIR)/libc_table.c \
              $(KERNEL_DIR)/of_malloc.c

# All sources
ALL_C_SRCS = $(BOOT_SRCS_C) $(HAL_SRCS) $(TARGET_SRCS) $(KERNEL_SRCS)
OBJS = $(ALL_C_SRCS:.c=.o) $(BOOT_SRCS_S:.S=.o) $(HAL_SRCS_S:.S=.o)

# RAM size for MIF generation
RAM_WORDS = 16384

# Default target
all: $(FW_TARGET).bin $(FW_TARGET).mif $(FW_TARGET).lst os.bin
	$(SIZE) $(FW_TARGET).elf

# Link
$(FW_TARGET).elf: $(OBJS) os.ld
	$(LD) $(LDFLAGS) -o $@ $(OBJS) $(LIBS)

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

# OS binary (loaded by bootloader via deferload)
os.bin: $(FW_TARGET).elf
	$(OBJCOPY) -O binary --only-section=.text --only-section=.data $< $@
	@echo "Generated $@ ($$(wc -c < $@) bytes)"

# Disassembly
$(FW_TARGET).lst: $(FW_TARGET).elf
	$(OBJDUMP) -d -S $< > $@

# Compile rules
$(BOOT_DIR)/%.o: $(BOOT_DIR)/%.c
	$(CC) $(CFLAGS) -c -o $@ $<

$(BOOT_DIR)/%.o: $(BOOT_DIR)/%.S
	$(AS) $(ASFLAGS) -c -o $@ $<

$(HAL_DIR)/%.o: $(HAL_DIR)/%.S
	$(AS) $(ASFLAGS) -c -o $@ $<

$(HAL_DIR)/%.o: $(HAL_DIR)/%.c
	$(CC) $(OS_CFLAGS) -c -o $@ $<

$(TARGET_DIR)/%.o: $(TARGET_DIR)/%.c
	$(CC) $(OS_CFLAGS) -c -o $@ $<

$(KERNEL_DIR)/%.o: $(KERNEL_DIR)/%.c
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

rebuild: clean all

mem: $(FW_TARGET).elf
	$(SIZE) -A -x $(FW_TARGET).elf

.PHONY: all clean rebuild install mem
