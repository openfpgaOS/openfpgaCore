# Chip32 Loader Makefile
# Assembles the Chip32 VM bytecode for the APF bridge loader

BASS ?= bass
ARCH = architectures/chip32.vm.arch

LOADER_SRC = loader.asm
LOADER_BIN = loader.bin

all: $(LOADER_BIN)

$(LOADER_BIN): $(LOADER_SRC) $(ARCH)
	cd $(dir $<) && $(BASS) $(notdir $<)

clean:
	rm -f $(LOADER_BIN)

.PHONY: all clean
