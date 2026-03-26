/*
 * openfpgaOS Cache Management HAL Implementation
 *
 * VexiiRiscv D-cache: 32KB, direct-mapped (1-way), write-back, 64B lines
 * (512 sets x 1 way x 64B).
 *
 * Cache coherency is maintained via conflict eviction: reading through
 * a 32KB eviction region forces all dirty lines to be written back.
 * No Zicbom (cbo.*) instructions are used.
 */

#include "cache.h"
#include "regs.h"

#define DCACHE_LINE_SIZE  64
#define DCACHE_SETS       512
#define DCACHE_WAYS       1
#define DCACHE_TOTAL      (DCACHE_SETS * DCACHE_WAYS * DCACHE_LINE_SIZE)  /* 32KB */

void of_cache_init(void) {
    /* No initialization required */
}

/* Flush entire D-cache by conflict eviction.
 * Reads 32KB from the top of SDRAM, forcing all dirty cache lines
 * to be written back.  The eviction region is well above any active
 * data (app heap grows from 0x10400000, eviction is near 0x13FE0000). */
static void dcache_evict_all(void) {
    __asm__ volatile("fence" ::: "memory");
    volatile char *p = (volatile char *)(SDRAM_BASE + SDRAM_SIZE - DCACHE_TOTAL);
    for (uint32_t i = 0; i < DCACHE_TOTAL; i += DCACHE_LINE_SIZE)
        (void)p[i];
    __asm__ volatile("fence" ::: "memory");
}

/* Without per-line cache management instructions, all range operations
 * fall back to a full cache eviction.  This is less granular but correct
 * for DMA coherency. */

void of_cache_inval_range(void *addr, uint32_t size) {
    (void)addr; (void)size;
    dcache_evict_all();
}

void of_cache_clean_range(void *addr, uint32_t size) {
    (void)addr; (void)size;
    dcache_evict_all();
}

void of_cache_flush_range(void *addr, uint32_t size) {
    (void)addr; (void)size;
    dcache_evict_all();
}

void of_cache_flush_dcache(void) {
    dcache_evict_all();
}

void of_cache_invalidate_icache(void) {
    fence_i();
}

void of_cache_flush(void) {
    of_cache_flush_dcache();
    of_cache_invalidate_icache();
}
