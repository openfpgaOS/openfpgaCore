/*
 * openfpgaOS Cache Management HAL
 *
 * VexiiRiscv D-cache: 64KB, 4-way set-associative, write-back, 64B lines
 * (256 sets × 4 ways × 64B).
 *
 * Range operations (clean, invalidate) use Zicbom instructions (cbo.clean,
 * cbo.inval) which operate on a single cache line by address. These only
 * touch lines that overlap the target range, preserving all other cached
 * data.
 *
 * Full eviction (flush_dcache) still uses conflict eviction as a fallback
 * for situations where we need to flush everything (e.g. code loading).
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

/* cbo.clean: write back dirty line at addr, keep line valid in cache.
 * Encoding: .insn i 0x0F, 2, x0, rs1, 0x001 */
static inline void cbo_clean(uintptr_t addr) {
    __asm__ volatile(".insn i 0x0F, 2, x0, %0, 0x001" :: "r"(addr) : "memory");
}

/* cbo.inval: invalidate cache line at addr, discard dirty data.
 * Encoding: .insn i 0x0F, 2, x0, rs1, 0x000 */
static inline void cbo_inval(uintptr_t addr) {
    __asm__ volatile(".insn i 0x0F, 2, x0, %0, 0x000" :: "r"(addr) : "memory");
}

/* cbo.flush: write back dirty line at addr, then invalidate.
 * Encoding: .insn i 0x0F, 2, x0, rs1, 0x002 */
static inline void cbo_flush(uintptr_t addr) {
    __asm__ volatile(".insn i 0x0F, 2, x0, %0, 0x002" :: "r"(addr) : "memory");
}

/* Full eviction via conflict reads — used only for flush_dcache(). */
static void dcache_evict_all(void) {
    __asm__ volatile("fence" ::: "memory");
    volatile char *p = (volatile char *)EVICT_BASE;
    for (uint32_t i = 0; i < DCACHE_TOTAL; i += DCACHE_LINE_SIZE)
        (void)p[i];
    __asm__ volatile("fence" ::: "memory");
}

void of_cache_inval_range(void *addr, uint32_t size) {
    uintptr_t a   = (uintptr_t)addr & ~(DCACHE_LINE_SIZE - 1);
    uintptr_t end = ((uintptr_t)addr + size + DCACHE_LINE_SIZE - 1)
                    & ~(DCACHE_LINE_SIZE - 1);
    for (; a < end; a += DCACHE_LINE_SIZE)
        cbo_inval(a);
    __asm__ volatile("fence" ::: "memory");
}

void of_cache_clean_range(void *addr, uint32_t size) {
    uintptr_t a   = (uintptr_t)addr & ~(DCACHE_LINE_SIZE - 1);
    uintptr_t end = ((uintptr_t)addr + size + DCACHE_LINE_SIZE - 1)
                    & ~(DCACHE_LINE_SIZE - 1);
    for (; a < end; a += DCACHE_LINE_SIZE)
        cbo_clean(a);
    __asm__ volatile("fence" ::: "memory");
}

void of_cache_flush_range(void *addr, uint32_t size) {
    uintptr_t a   = (uintptr_t)addr & ~(DCACHE_LINE_SIZE - 1);
    uintptr_t end = ((uintptr_t)addr + size + DCACHE_LINE_SIZE - 1)
                    & ~(DCACHE_LINE_SIZE - 1);
    for (; a < end; a += DCACHE_LINE_SIZE)
        cbo_flush(a);
    __asm__ volatile("fence" ::: "memory");
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
