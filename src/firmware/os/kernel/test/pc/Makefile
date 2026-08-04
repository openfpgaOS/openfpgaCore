#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------

# Host (PC-native) harness for the app mmap allocator in kernel/syscall.c.
#
# Builds and runs the REAL sys_mmap2/sys_munmap, lifted out of syscall.c by
# extract.sh (never retyped), against workloads that model what musl's
# mallocng does to the arena.  Nothing in the firmware tree is touched.
#
#   make run     extract, build, run the tests
#   make clean   remove generated artifacts
#
# Regression this locks down: the allocator used to return ENOMEM for any
# request that was not an exact-size match once its slot table filled, with
# tens of MB of arena still free.  That wedged DevilutionX's level loads
# (openfpgaOS/Diablo issue #4) after only ~4 MB of live mappings.
#
# This directory is self-contained; the firmware Makefile globs nothing here.

SYSCALL_C := ../../syscall.c
CFLAGS    := -O1 -g -Wall -Wextra -Wno-unused-parameter

.PHONY: run clean
run: test_mmap
	./test_mmap

extracted.inc: $(SYSCALL_C) extract.sh
	./extract.sh $(SYSCALL_C) $@

test_mmap: test_mmap.c extracted.inc
	$(CC) $(CFLAGS) -o $@ test_mmap.c

clean:
	rm -f test_mmap extracted.inc extracted.inc.part1 extracted.inc.part2
