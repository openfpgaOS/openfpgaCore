/*
 * openfpgaOS Cache Management HAL
 *
 * VexiiRiscv D-cache: 64KB, 4-way set-associative, write-back, 64B lines
 * (256 sets x 4 ways x 64B).
 *
 * Uses targeted conflict eviction for range operations: only touches
 * the cache sets that overlap the target range, instead of sweeping
 * all 256 sets.
 *
 */

#include "cache.h"
#include "regs.h"

#define DCACHE_LINE_SIZE  64
#define DCACHE_SETS       256
#define DCACHE_WAYS       4
#define DCACHE_TOTAL      (DCACHE_SETS * DCACHE_WAYS * DCACHE_LINE_SIZE)  /* 64KB */

/* One stride = all lines in one way = 256 sets × 64B = 16KB */
#define DCACHE_STRIDE     (DCACHE_SETS * DCACHE_LINE_SIZE)

/* Eviction region: top of SDRAM, above all active data */
#define EVICT_BASE  (SDRAM_BASE + SDRAM_SIZE - DCACHE_TOTAL)

void of_cache_init(void) { }

/* Evict specific cache sets covering [addr, addr+size).
 * 4-way set-associative: set index = bits [13:6] of address.
 * Must read 4 addresses per set (different tags) to evict all ways. */
static void dcache_evict_range(void *addr, uint32_t size) {
    __asm__ volatile("fence" ::: "memory");

    uintptr_t start = (uintptr_t)addr & ~(DCACHE_LINE_SIZE - 1);
    uintptr_t end   = ((uintptr_t)addr + size + DCACHE_LINE_SIZE - 1)
                      & ~(DCACHE_LINE_SIZE - 1);
    uint32_t lines = (end - start) / DCACHE_LINE_SIZE;

    if (lines >= DCACHE_SETS) {
        /* Full eviction: read 64KB to fill all 256 sets × 4 ways */
        volatile char *p = (volatile char *)EVICT_BASE;
        for (uint32_t i = 0; i < DCACHE_TOTAL; i += DCACHE_LINE_SIZE)
            (void)p[i];
    } else {
        /* Targeted: touch all 4 ways of each overlapping set */
        for (uintptr_t a = start; a < end; a += DCACHE_LINE_SIZE) {
            uint32_t set = (a >> 6) & (DCACHE_SETS - 1);
            uint32_t off = set * DCACHE_LINE_SIZE;
            (void)*(volatile char *)(EVICT_BASE + off);
            (void)*(volatile char *)(EVICT_BASE + DCACHE_STRIDE + off);
            (void)*(volatile char *)(EVICT_BASE + DCACHE_STRIDE * 2 + off);
            (void)*(volatile char *)(EVICT_BASE + DCACHE_STRIDE * 3 + off);
        }
    }

    __asm__ volatile("fence" ::: "memory");
}

static void dcache_evict_all(void) {
    __asm__ volatile("fence" ::: "memory");
    volatile char *p = (volatile char *)EVICT_BASE;
    for (uint32_t i = 0; i < DCACHE_TOTAL; i += DCACHE_LINE_SIZE)
        (void)p[i];
    __asm__ volatile("fence" ::: "memory");
}

void of_cache_inval_range(void *addr, uint32_t size) {
    dcache_evict_range(addr, size);
}

void of_cache_clean_range(void *addr, uint32_t size) {
    dcache_evict_range(addr, size);
}

void of_cache_flush_range(void *addr, uint32_t size) {
    dcache_evict_range(addr, size);
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

/* Convert cached address (0x10xxxxxx) to uncached alias (0x50xxxxxx).
 * Returns unmodified if not in the cached SDRAM range. */
static inline uint32_t to_uncached(uint32_t addr) {
    if ((addr & 0xF0000000) == SDRAM_BASE)
        return (addr & ~0xF0000000) | SDRAM_UNCACHED_BASE;
    return addr;
}
