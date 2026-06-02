//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * shared/dcache_evict.inc.c — D-cache flush by conflict eviction
 *
 * Pure-software flush of the L1 D-cache by reading 128 KB of SDRAM
 * (one byte per cache line). The VexiiRiscv L1 is 2-way associative,
 * 1024 sets × 2 ways × 64-byte lines = 128 KB total, so streaming
 * 128 KB through any contiguous SDRAM region forces every line out.
 *
 * Used by:
 *   - boot ROM: flushes deferload writes before jumping to os_main
 *
 * Why no `cbo.flush`: the boot ROM shares this file with builds that
 * cannot rely on Zicbom being available before the OS cache HAL starts.
 *
 * Provided by the includer:
 *   SHARED_ATTR — section attribute (or empty)
 * Required: regs.h (SDRAM_BASE, SDRAM_SIZE).
 */

#ifndef SHARED_ATTR
#  define SHARED_ATTR
#endif

SHARED_ATTR
static void flush_dcache_evict(void) {
    __asm__ volatile("fence" ::: "memory");
    volatile char *p = (volatile char *)(SDRAM_BASE + SDRAM_SIZE - 131072);
    for (uint32_t i = 0; i < 131072; i += 64)
        (void)p[i];
    __asm__ volatile("fence" ::: "memory");
}
