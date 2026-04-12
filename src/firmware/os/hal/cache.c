/*
 * openfpgaOS Cache Management HAL
 *
 * VexiiRiscv D-cache: 64KB, 4-way set-associative, write-back, 64B lines
 * (256 sets × 4 ways × 64B).
 *
 * Zicbom (cbo.clean/inval/flush) is NOT available in this build — it is
 * mutually exclusive with the LsuL1 coherency hub we depend on, per the
 * `assert(!withCoherency)` in VexiiRiscv's LsuL1Plugin. Range ops
 * therefore fall back to a full D$ conflict-eviction sweep. This is
 * heavy-handed (kills the whole working set per call), but correct.
 * Callers should prefer the uncached SDRAM alias (0x50xxxxxx) for
 * DMA-coherent regions so they don't need these helpers in the hot path.
 */

#include "cache.h"
#include "regs.h"

#define DCACHE_LINE_SIZE  64
#define DCACHE_SETS       256
#define DCACHE_WAYS       4
#define DCACHE_TOTAL      (DCACHE_SETS * DCACHE_WAYS * DCACHE_LINE_SIZE)  /* 64KB */

/* Eviction region: top of SDRAM, above all active data */
#define EVICT_BASE  (SDRAM_BASE + SDRAM_SIZE - DCACHE_TOTAL)

void of_cache_init(void) { }

/* Full eviction via conflict reads. Reads DCACHE_TOTAL bytes from a
 * dedicated region at the top of SDRAM, guaranteed to evict every line
 * in the D$ regardless of the range the caller asked about. */
static void dcache_evict_all(void) {
    __asm__ volatile("fence" ::: "memory");
    volatile char *p = (volatile char *)EVICT_BASE;
    for (uint32_t i = 0; i < DCACHE_TOTAL; i += DCACHE_LINE_SIZE)
        (void)p[i];
    __asm__ volatile("fence" ::: "memory");
}

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

/* Convert cached address (0x10xxxxxx) to uncached alias (0x50xxxxxx).
 * Returns unmodified if not in the cached SDRAM range. */
static inline uint32_t to_uncached(uint32_t addr) {
    if ((addr & 0xF0000000) == SDRAM_BASE)
        return (addr & ~0xF0000000) | SDRAM_UNCACHED_BASE;
    return addr;
}
