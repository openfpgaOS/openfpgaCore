/*
 * openfpgaOS Cache Management HAL
 *
 * VexiiRiscv D-cache: 32KB, direct-mapped, write-back, 64B lines
 * (512 sets x 1 way x 64B).
 *
 * Uses targeted conflict eviction for range operations: only touches
 * the cache sets that overlap the target range, instead of sweeping
 * all 512 sets.  For a 4KB DMA read this is 64 reads vs 512.
 *
 */

#include "cache.h"
#include "regs.h"

#define DCACHE_LINE_SIZE  64
#define DCACHE_SETS       256
#define DCACHE_WAYS       2
#define DCACHE_TOTAL      (DCACHE_SETS * DCACHE_WAYS * DCACHE_LINE_SIZE)  /* 32KB */

/* Eviction region: top of SDRAM, above all active data */
#define EVICT_BASE  (SDRAM_BASE + SDRAM_SIZE - DCACHE_TOTAL)

void of_cache_init(void) { }

/* Evict specific cache sets covering [addr, addr+size).
 * 2-way set-associative: set index = bits [13:6] of address.
 * Must read 2 addresses per set (different tags) to evict both ways. */
static void dcache_evict_range(void *addr, uint32_t size) {
    __asm__ volatile("fence" ::: "memory");

    uintptr_t start = (uintptr_t)addr & ~(DCACHE_LINE_SIZE - 1);
    uintptr_t end   = ((uintptr_t)addr + size + DCACHE_LINE_SIZE - 1)
                      & ~(DCACHE_LINE_SIZE - 1);
    uint32_t lines = (end - start) / DCACHE_LINE_SIZE;

    if (lines >= DCACHE_SETS) {
        /* Full eviction: read 32KB to fill all 256 sets × 2 ways */
        volatile char *p = (volatile char *)EVICT_BASE;
        for (uint32_t i = 0; i < DCACHE_TOTAL; i += DCACHE_LINE_SIZE)
            (void)p[i];
    } else {
        /* Targeted: touch both ways of each overlapping set */
        uint32_t half = DCACHE_SETS * DCACHE_LINE_SIZE;  /* 16KB */
        for (uintptr_t a = start; a < end; a += DCACHE_LINE_SIZE) {
            uint32_t set = (a >> 6) & (DCACHE_SETS - 1);
            uint32_t off = set * DCACHE_LINE_SIZE;
            (void)*(volatile char *)(EVICT_BASE + off);
            (void)*(volatile char *)(EVICT_BASE + half + off);
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

void dma_copy(void *dst, const void *src, uint32_t len) {
    if (len == 0) return;
    /* Flush src dirty lines to SDRAM so DMA reads correct data */
    dcache_evict_range((void *)src, len);
    /* Evict dst lines so CPU doesn't hold stale copies */
    dcache_evict_range(dst, len);
    /* Start DMA and wait */
    dma_start_copy((uint32_t)(uintptr_t)dst,
                   (uint32_t)(uintptr_t)src, len);
    dma_wait();
}

void dma_fill(void *dst, uint32_t value, uint32_t len) {
    if (len == 0) return;
    /* Evict dst lines so CPU doesn't hold stale copies */
    dcache_evict_range(dst, len);
    dma_start_fill((uint32_t)(uintptr_t)dst, value, len);
    dma_wait();
}

void dma_copy_async(void *dst, const void *src, uint32_t len) {
    if (len == 0) return;
    dcache_evict_range((void *)src, len);
    dcache_evict_range(dst, len);
    dma_start_copy((uint32_t)(uintptr_t)dst,
                   (uint32_t)(uintptr_t)src, len);
    /* Returns without waiting — caller must call dma_wait() */
}

void dma_fill_async(void *dst, uint32_t value, uint32_t len) {
    if (len == 0) return;
    dcache_evict_range(dst, len);
    dma_start_fill((uint32_t)(uintptr_t)dst, value, len);
    /* Returns without waiting — caller must call dma_wait() */
}
