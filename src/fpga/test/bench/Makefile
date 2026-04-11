# Memory benchmarks for Verilator system simulation
CROSS ?= riscv64-elf-
CC = $(CROSS)gcc
OBJCOPY = $(CROSS)objcopy

CFLAGS = -march=rv32imafcb -mabi=ilp32f -O2 -nostdlib -nostartfiles -ffreestanding
LDFLAGS = -T bench.ld -Wl,--no-warn-rwx-segments

all: membench.bin cram0bench.bin

%.elf: %.c bench.ld
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $<

%.bin: %.elf
	$(OBJCOPY) -O binary $< $@
	@echo "Built $@ ($$(wc -c < $@) bytes)"

run: membench.bin
	../obj_dir_system/Vtb_system membench.bin 10000000

run-cram0: cram0bench.bin
	../obj_dir_system/Vtb_system cram0bench.bin 2000000

clean:
	rm -f membench.elf membench.bin cram0bench.elf cram0bench.bin

.PHONY: all run run-cram0 clean
