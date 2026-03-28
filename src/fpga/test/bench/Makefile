# Memory benchmark for Verilator system simulation
CROSS ?= riscv64-elf-
CC = $(CROSS)gcc
OBJCOPY = $(CROSS)objcopy

CFLAGS = -march=rv32imafcb -mabi=ilp32f -O2 -nostdlib -nostartfiles -ffreestanding
LDFLAGS = -T bench.ld -Wl,--no-warn-rwx-segments

all: membench.bin

membench.elf: membench.c bench.ld
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ membench.c

membench.bin: membench.elf
	$(OBJCOPY) -O binary $< $@
	@echo "Built $@ ($$(wc -c < $@) bytes)"

run: membench.bin
	../obj_dir_system/Vtb_system membench.bin 10000000

clean:
	rm -f membench.elf membench.bin

.PHONY: all run clean
