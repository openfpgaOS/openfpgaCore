/*
 * openfpgaOS Cache Management HAL Implementation
 *
 * VexiiRiscv D-cache: 32KB, direct-mapped (512 sets x 1 way), write-back, 64B lines.
 * No Zicbom hardware — uses conflict eviction instead:
 *   To flush a cache line, we read a different address that maps to the
 *   same cache set. This evicts the dirty line (writing it back) and
 *   replaces it with clean data.
 *
 * D-cache total size: 512 * 64 = 32,768 bytes (32 KB).
 */

#include "cache.h"
#include "regs.h"

#define DCACHE_LINE_SIZE    64
#define DCACHE_SETS         512
#define DCACHE_SIZE         (DCACHE_SETS * DCACHE_LINE_SIZE)

/* Eviction region: top 32KB of SDRAM, used solely for conflict eviction. */
#define EVICT_BASE  ((volatile uint8_t *)(SDRAM_BASE + SDRAM_SIZE - DCACHE_SIZE))

void of_cache_init(void) {
    /* No initialization required */
}

/* Flush entire D-cache by conflict eviction. */
void of_cache_flush_dcache(void) {
    __asm__ volatile("fence" ::: "memory");
    volatile uint8_t *p = EVICT_BASE;
    for (uint32_t i = 0; i < DCACHE_SIZE; i += DCACHE_LINE_SIZE)
        (void)p[i];
    __asm__ volatile("fence" ::: "memory");
}

void of_cache_inval_range(void *addr, uint32_t size) {
    (void)addr; (void)size;
    of_cache_flush_dcache();
}

void of_cache_clean_range(void *addr, uint32_t size) {
    (void)addr; (void)size;
    of_cache_flush_dcache();
}

void of_cache_flush_range(void *addr, uint32_t size) {
    (void)addr; (void)size;
    of_cache_flush_dcache();
}

void of_cache_invalidate_icache(void) {
    fence_i();
}

void of_cache_flush(void) {
    of_cache_flush_dcache();
    of_cache_invalidate_icache();
}
