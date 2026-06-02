//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * openfpgaOS Cache Management HAL
 *
 * VexiiRiscv D-cache: 128KB, 2-way set-associative, write-back, 64B lines
 * (1024 sets × 2 ways × 64B).
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
#define DCACHE_SETS       1024
#define DCACHE_WAYS       2
#define DCACHE_TOTAL      (DCACHE_SETS * DCACHE_WAYS * DCACHE_LINE_SIZE)  /* 128KB */

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

/* ---- cbo address guard ----
 * cbo.clean/inval/flush are only legal on the cacheable SDRAM region — the
 * sole PMA main=1 window, 0x10000000..0x14000000 (see generate_vexii.sh).  A
 * cbo to any other address takes a load/store ACCESS FAULT and panics the OS.
 *
 * That is exactly the intermittent boot trap: a marginal-timing bit flip in
 * the clk_ram_controller (100 MHz CPU/RAM) domain — which misses slow-corner
 * setup (WNS -0.56 ns) — occasionally corrupts a value read from SDRAM (e.g.
 * the framebuffer base 0x10000000 -> 0x18000000, bit 27), and of_video_init
 * then hands that garbage address to of_cache_clean_range.
 *
 * This guard clamps every range to the cacheable window so an out-of-range
 * line is skipped instead of faulting.  It is a BAND-AID: it neutralizes the
 * fatal cbo, but the same corruption can still hit data the guard never sees
 * (lengths, non-cbo pointers).  cbo_guard_skips counts every range that had to
 * be clamped/dropped so the corruption rate stays observable — read it via
 * of_cache_guard_skips().  The real fix is closing the clk_ram_controller
 * timing (drop it toward ~90 MHz, or retime the failing paths). */
#define CBO_REGION_LO  ((uintptr_t)(SDRAM_BASE))
#define CBO_REGION_HI  ((uintptr_t)(SDRAM_BASE + SDRAM_SIZE))

static uint32_t cbo_guard_skips;

/* Clamp [*pa, *pend) to the cacheable SDRAM window.  Returns nonzero if any
 * cacheable lines remain; bumps cbo_guard_skips when it had to clamp/drop. */
static int cbo_clamp_range(uintptr_t *pa, uintptr_t *pend) {
    uintptr_t a = *pa, end = *pend;
    if (a < CBO_REGION_LO || end > CBO_REGION_HI || a >= end)
        cbo_guard_skips++;
    if (a   < CBO_REGION_LO) a   = CBO_REGION_LO;
    if (end > CBO_REGION_HI) end = CBO_REGION_HI;
    *pa = a; *pend = end;
    return a < end;
}

uint32_t of_cache_guard_skips(void) { return cbo_guard_skips; }

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
    uintptr_t a = (uintptr_t)addr & ~(DCACHE_LINE_SIZE - 1);
    uintptr_t end = (uintptr_t)addr + size;
    if (!cbo_clamp_range(&a, &end)) return;   /* out of cacheable range — skip */
    __asm__ volatile("fence" ::: "memory");
    for (; a < end; a += DCACHE_LINE_SIZE)
        cbo_inval((void *)a);
    __asm__ volatile("fence" ::: "memory");
}

void of_cache_clean_range(void *addr, uint32_t size) {
    if (size == 0) return;
    uintptr_t a = (uintptr_t)addr & ~(DCACHE_LINE_SIZE - 1);
    uintptr_t end = (uintptr_t)addr + size;
    if (!cbo_clamp_range(&a, &end)) return;   /* out of cacheable range — skip */
    __asm__ volatile("fence" ::: "memory");
    for (; a < end; a += DCACHE_LINE_SIZE)
        cbo_clean((void *)a);
    __asm__ volatile("fence" ::: "memory");
}

/* of_cache_flush_range issues cbo.flush per line.  The trailing fence
 * orders subsequent CPU memory ops with respect to the flushed lines
 * but does NOT wait for the AXI writeback to actually reach DRAM.
 *
 * On this 3-master CPU topology (i_axi + d_axi + p_axi), cached
 * writes/writebacks ride d_axi while uncached accesses ride p_axi.
 * The two masters synchronize only at the SDRAM arbiter, not inside
 * the CPU — so a read on p_axi (uncached) cannot drain a write
 * still buffered in d_axi's outgoing FIFO.  We tried that as an
 * AXI-drain trailer; didn't work.
 *
 * For callers whose downstream HW (mixer, GPU, video controller)
 * reads on the order of milliseconds after the flush, d_axi's
 * write FIFO settles naturally before the read fires and cbo.flush
 * is sufficient (this is the bank_preload, FB swap, palookup
 * upload pattern).  For sub-millisecond gaps (audio stream voice
 * 31 in audio.c), the only reliable path is to write through the
 * uncached SDRAM alias from the start so each store stalls on its
 * AXI B-response — see audio.c's audio_ring. */
void of_cache_flush_range(void *addr, uint32_t size) {
    if (size == 0) return;
    uintptr_t a = (uintptr_t)addr & ~(DCACHE_LINE_SIZE - 1);
    uintptr_t end = (uintptr_t)addr + size;
    if (!cbo_clamp_range(&a, &end)) return;   /* out of cacheable range — skip */
    __asm__ volatile("fence" ::: "memory");
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
