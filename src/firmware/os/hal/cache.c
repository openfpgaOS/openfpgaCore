/*
 * openfpgaOS Cache Management HAL
 *
 * VexiiRiscv D-cache: 64KB, 2-way set-associative, write-back, 64B lines
 * (512 sets × 2 ways × 64B).
 *
 * Zicbom (cbo.clean/inval/flush) is enabled: lsuL1Coherency is off and
 * the HubFiber is removed so the LsuL1Plugin CBM area is active. Range
 * ops use per-line cbo instructions — dramatically cheaper than the old
 * full-cache conflict-eviction sweep.
 *
 * Full-sweep of_cache_flush_dcache() still uses the conflict-eviction
 * approach (can't enumerate cached addresses); called only on cold paths
 * (ELF loader, bank preload).
 */

#include "cache.h"
#include "regs.h"

#define DCACHE_LINE_SIZE  64
#define DCACHE_SETS       512
#define DCACHE_WAYS       2
#define DCACHE_TOTAL      (DCACHE_SETS * DCACHE_WAYS * DCACHE_LINE_SIZE)  /* 64KB */

/* Eviction region: top of SDRAM, above all active data */
#define EVICT_BASE  (SDRAM_BASE + SDRAM_SIZE - DCACHE_TOTAL)

/* ---- Zicbom per-line helpers ---- */

static inline void cbo_clean(void *addr) {
    __asm__ volatile(".insn i 0x0F, 2, x0, %0, 1" :: "r"(addr) : "memory");
}

static inline void cbo_inval(void *addr) {
    __asm__ volatile(".insn i 0x0F, 2, x0, %0, 0" :: "r"(addr) : "memory");
}

static inline void cbo_flush(void *addr) {
    __asm__ volatile(".insn i 0x0F, 2, x0, %0, 2" :: "r"(addr) : "memory");
}

void of_cache_init(void) { }

/* Full eviction via conflict reads. Reads DCACHE_TOTAL bytes from a
 * dedicated region at the top of SDRAM, guaranteed to evict every line
 * in the D$ regardless of the range the caller asked about. Only used
 * for the full-sweep flush (cold paths). */
static void dcache_evict_all(void) {
    __asm__ volatile("fence" ::: "memory");
    volatile char *p = (volatile char *)EVICT_BASE;
    for (uint32_t i = 0; i < DCACHE_TOTAL; i += DCACHE_LINE_SIZE)
        (void)p[i];
    __asm__ volatile("fence" ::: "memory");
}

void of_cache_inval_range(void *addr, uint32_t size) {
    if (size == 0) return;
    __asm__ volatile("fence" ::: "memory");
    uintptr_t a = (uintptr_t)addr & ~(DCACHE_LINE_SIZE - 1);
    uintptr_t end = (uintptr_t)addr + size;
    for (; a < end; a += DCACHE_LINE_SIZE)
        cbo_inval((void *)a);
    __asm__ volatile("fence" ::: "memory");
}

void of_cache_clean_range(void *addr, uint32_t size) {
    if (size == 0) return;
    __asm__ volatile("fence" ::: "memory");
    uintptr_t a = (uintptr_t)addr & ~(DCACHE_LINE_SIZE - 1);
    uintptr_t end = (uintptr_t)addr + size;
    for (; a < end; a += DCACHE_LINE_SIZE)
        cbo_clean((void *)a);
    __asm__ volatile("fence" ::: "memory");
}

void of_cache_flush_range(void *addr, uint32_t size) {
    if (size == 0) return;
    __asm__ volatile("fence" ::: "memory");
    uintptr_t a = (uintptr_t)addr & ~(DCACHE_LINE_SIZE - 1);
    uintptr_t end = (uintptr_t)addr + size;
    for (; a < end; a += DCACHE_LINE_SIZE)
        cbo_flush((void *)a);
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
