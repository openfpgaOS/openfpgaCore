/*
 * shared/dcache_evict.inc.c — D-cache flush by conflict eviction
 *
 * Pure-software flush of the L1 D-cache by reading 64 KB of SDRAM
 * (one byte per cache line). The VexiiRiscv L1 is 4-way associative,
 * 256 sets × 4 ways × 64-byte lines = 64 KB total, so streaming
 * 64 KB through any contiguous SDRAM region forces every line out.
 *
 * Used by:
 *   - boot ROM: flushes deferload writes before jumping to os_main
 *
 * Why no `cbo.flush`: the VexiiRiscv core in this target doesn't
 * implement Zicbom, so we have to evict the hard way.
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
    volatile char *p = (volatile char *)(SDRAM_BASE + SDRAM_SIZE - 65536);
    for (uint32_t i = 0; i < 65536; i += 64)
        (void)p[i];
    __asm__ volatile("fence" ::: "memory");
}
