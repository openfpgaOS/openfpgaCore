#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------

# Chip32 Loader Makefile
# Assembles the Chip32 VM bytecode for the APF bridge loader

BASS ?= bass
ARCH = architectures/chip32.vm.arch

LOADER_SRC = loader.asm
LOADER_BIN = loader.bin

all: $(LOADER_BIN)

# Always regenerate: bass on a ~150-line source is a ~10ms deterministic
# (byte-identical) assemble, so we force it rather than trust mtimes.  A plain
# `loader.bin: loader.asm` rule no-ops when the two share an mtime (e.g. a
# checkout writing both in the same second) — which is exactly how a stale
# pre-`=os30`-scan loader.bin shipped and made every app boot os25.
$(LOADER_BIN): $(LOADER_SRC) $(ARCH) FORCE
	cd $(dir $<) && $(BASS) $(notdir $<)

FORCE:

clean:
	rm -f $(LOADER_BIN)

.PHONY: all clean FORCE
