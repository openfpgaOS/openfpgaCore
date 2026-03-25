/*
 * openfpgaOS Cache Management HAL Implementation
 *
 * VexiiRiscv D-cache: 128KB, 2-way set-associative, write-back, 64B lines.
 * Uses Zicbom (Cache-Block Management Operations) for precise control:
 *   cbo.clean  addr  — write-back dirty line, keep in cache
 *   cbo.inval  addr  — discard line (no write-back!)
 *   cbo.flush  addr  — write-back dirty line + discard
 */

#include "cache.h"
#include "regs.h"

#define DCACHE_LINE_SIZE  64

void of_cache_init(void) {
    /* No initialization required */
}

/* Zicbom instruction encodings (rs1 in bits 19:15):
 * cbo.clean  = 0x0010200F with rs1
 * cbo.inval  = 0x0000200F with rs1
 * cbo.flush  = 0x0020200F with rs1
 *
 * We use inline asm with the .insn directive for portability. */

/* Write-back dirty data for one cache line, keep line in cache.
 * Use before DMA reads FROM memory (ensures peripheral sees latest data). */
static inline void cbo_clean(void *addr) {
    __asm__ volatile(".insn i 0x0F, 2, x0, %0, 0x001" :: "r"(addr) : "memory");
}

/* Invalidate one cache line WITHOUT write-back.
 * Use after DMA writes TO memory (discards stale cached copy). */
static inline void cbo_inval(void *addr) {
    __asm__ volatile(".insn i 0x0F, 2, x0, %0, 0x000" :: "r"(addr) : "memory");
}

/* Write-back + invalidate one cache line.
 * Use when both directions may be dirty. */
static inline void cbo_flush(void *addr) {
    __asm__ volatile(".insn i 0x0F, 2, x0, %0, 0x002" :: "r"(addr) : "memory");
}

/* Invalidate D-cache lines for a memory range.
 * Call AFTER DMA writes to memory — discards stale cached data
 * so the CPU fetches fresh DMA results on next access. */
void of_cache_inval_range(void *addr, uint32_t size) {
    uint8_t *p = (uint8_t *)((uintptr_t)addr & ~(DCACHE_LINE_SIZE - 1));
    uint8_t *end = (uint8_t *)addr + size;
    while (p < end) {
        cbo_inval(p);
        p += DCACHE_LINE_SIZE;
    }
}

/* Write-back D-cache lines for a memory range.
 * Call BEFORE a peripheral reads from memory — ensures dirty
 * data is visible in SDRAM. */
void of_cache_clean_range(void *addr, uint32_t size) {
    uint8_t *p = (uint8_t *)((uintptr_t)addr & ~(DCACHE_LINE_SIZE - 1));
    uint8_t *end = (uint8_t *)addr + size;
    while (p < end) {
        cbo_clean(p);
        p += DCACHE_LINE_SIZE;
    }
}

/* Write-back + invalidate D-cache lines for a memory range. */
void of_cache_flush_range(void *addr, uint32_t size) {
    uint8_t *p = (uint8_t *)((uintptr_t)addr & ~(DCACHE_LINE_SIZE - 1));
    uint8_t *end = (uint8_t *)addr + size;
    while (p < end) {
        cbo_flush(p);
        p += DCACHE_LINE_SIZE;
    }
}

/* Flush entire D-cache (write-back all dirty lines + invalidate).
 * Walks known dirty regions rather than the full address space. */
void of_cache_flush_dcache(void) {
    /* Flush all cacheable SDRAM: framebuffers, DMA buffer, kernel, app.
     * 128KB D-cache = 2048 lines × 64B.  Walking 4MB covers all possible
     * dirty lines many times over (4MB / 64B = 65536 > 2048 lines). */
    of_cache_flush_range((void *)0x10000000, 0x400000);
}

void of_cache_invalidate_icache(void) {
    fence_i();
}

void of_cache_flush(void) {
    of_cache_flush_dcache();
    of_cache_invalidate_icache();
}
