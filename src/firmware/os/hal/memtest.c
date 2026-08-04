//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * memtest.c -- boot-time memory integrity check (see memtest.h).
 *
 * Two probe kinds, chosen for the fault classes actually seen:
 *
 *   address-tag sweep  writes each sampled word its own address (tagged)
 *                      across a WIDE span, then verifies after all
 *                      writes.  Two addresses aliasing onto one cell
 *                      (bank/row interleave mismatch between the RTL
 *                      controller and what this OS was built for) leave
 *                      the LATER address's tag behind -- caught here,
 *                      invisible to a per-word write-then-read.
 *
 *   dense lane block   floods a small block with byte-lane patterns
 *                      (write whole block, then read whole block, so a
 *                      stuck/miscaptured DQ lane can't be masked by bus
 *                      locality).  Catches DDIO capture faults and
 *                      stuck/shorted data lines.
 *
 * Region choice (boot-order sensitive -- caller runs this after
 * of_init_early() + the contract handshake, BEFORE of_init_late(), so
 * no disk/file/save/bridge traffic exists yet):
 *   - app SDRAM window [APP_LOAD_ADDR, OF_TARGET_APP_STATIC_END): empty
 *     until elf_load.  Probed via the UNCACHED alias so traffic hits the
 *     SDRAM controller, not the D-cache.
 *   - CRAM0 DMA scratch + app staging windows: idle until of_init_late.
 *     On pocket the bridge owns the CRAM0 mux at boot, so the probe
 *     takes CPU ownership and hands it back (same discipline as
 *     save.c/file.c -- a CPU access while the bridge owns the mux is
 *     silently dropped and wedges the CDC forever).  On targets whose
 *     staging arena is cached SDRAM (mister) the probe goes through the
 *     uncached alias so it exercises the controller and leaves no dirty
 *     lines over the sector-DMA bounce.
 * Never touched: OS image/stacks, framebuffers (live scanout), CRAM0
 * OS/FTAB staging, presave config, save slots.
 */

#include "memtest.h"
#include "regs.h"
#include "terminal.h"
#include "cache.h"

static inline void memtest_fence(void) {
    __asm__ volatile("fence" ::: "memory");
}

/* CRAM0 mux ownership bracket (pocket).  Mirrors save.c's
 * cram0_acquire_cpu/cram0_release_to_bridge: flip the mux, then wait
 * ~4 clk_74a cycles for the select to propagate before the next access
 * lands.  On mister/sim the register exists in the shared periph slave
 * but nothing consumes it, so the bracket is harmless. */
static inline void cram0_probe_settle(void) {
    for (volatile int s = 0; s < 8; s++) {}
}

static inline void cram0_probe_acquire(void) {
    memtest_fence();
    CRAM0_MODE = CRAM0_MODE_CPU;
    cram0_probe_settle();
    memtest_fence();
}

static inline void cram0_probe_release(void) {
    memtest_fence();
    CRAM0_MODE = CRAM0_MODE_BRIDGE;
    cram0_probe_settle();
    memtest_fence();
}

/* App ELF load base -- keep equal to APP_LOAD_ADDR (kernel/main.c) and
 * app.ld's SDRAM ORIGIN; the loader rejects apps outside this window. */
#define MEMTEST_APP_BASE 0x10400000u

#define ADDR_TAG(a) ((uint32_t)(a) ^ 0xA5A5F00Du)

static const char *fail_region;
static uintptr_t   fail_addr;
static uint32_t    fail_want, fail_got;

static int check(const char *region, uintptr_t a, uint32_t want, uint32_t got) {
    if (want == got)
        return 0;
    fail_region = region;
    fail_addr   = a;
    fail_want   = want;
    fail_got    = got;
    return -1;
}

/* Write tags across [base, base+span) at a stride, verify after all
 * writes so aliased cells are caught holding the later tag. */
static int addrtag_sweep(const char *region, uintptr_t base, uint32_t span,
                         uint32_t samples) {
    uint32_t stride = (span / samples) & ~3u;
    if (stride < 4u)
        stride = 4u;
    for (uintptr_t a = base; a + 4u <= base + span; a += stride)
        *(volatile uint32_t *)a = ADDR_TAG(a);
    for (uintptr_t a = base; a + 4u <= base + span; a += stride) {
        uint32_t got = *(volatile uint32_t *)a;
        if (check(region, a, ADDR_TAG(a), got))
            return -1;
    }
    return 0;
}

static int dense_block(const char *region, uintptr_t base, uint32_t bytes) {
    static const uint32_t pat[] = {
        0x00FF00FFu, 0xFF00FF00u,   /* opposing byte lanes            */
        0x55AA55AAu, 0xAA55AA55u,   /* alternating bits across lanes  */
        0x01020408u, 0x80402010u,   /* walking bits per byte lane     */
    };
    const uint32_t words = bytes / 4u;
    for (unsigned p = 0; p < sizeof(pat) / sizeof(pat[0]); p++) {
        volatile uint32_t *w = (volatile uint32_t *)base;
        for (uint32_t i = 0; i < words; i++)
            w[i] = pat[p] ^ (i << 24);  /* per-word twist: catches word-order swaps */
        for (uint32_t i = 0; i < words; i++) {
            uint32_t got = w[i];
            if (check(region, base + 4u * i, pat[p] ^ (i << 24), got))
                return -1;
        }
    }
    return 0;
}

int of_memtest_boot(void) {
    int rc = 0;

    /* SDRAM app window via the uncached alias. */
    {
        const uintptr_t delta = OF_TARGET_SDRAM_UNCACHED_BASE - OF_TARGET_SDRAM_BASE;
        const uintptr_t base  = MEMTEST_APP_BASE + delta;
        const uint32_t  span  = OF_TARGET_APP_STATIC_END - MEMTEST_APP_BASE;
        rc = addrtag_sweep("sdram", base, span, 4096u);
        if (!rc) rc = dense_block("sdram", base, 4096u);
        if (!rc) rc = dense_block("sdram", base + (span / 2u & ~3u), 4096u);
        if (!rc) rc = dense_block("sdram", base + span - 4096u, 4096u);
    }



    /* CRAM0 staging windows (idle: of_init_late has not run).  Take CPU
     * ownership of the mux for the probes and hand it back -- the bridge
     * owns it at boot, and a CPU access it doesn't own is dropped and
     * wedges the CDC.  On mister the arena is cached SDRAM: probe the
     * uncached alias so the controller is exercised and no dirty lines
     * are left over the sector-DMA bounce / app staging pool. */
    if (!rc) {
        uintptr_t cram0 = CRAM0_BASE;
#ifdef OF_TARGET_CRAM0_IS_CACHED_SDRAM
        cram0 += OF_TARGET_SDRAM_UNCACHED_BASE - OF_TARGET_SDRAM_BASE;
#endif
        const uintptr_t scratch = cram0 + OF_TARGET_CRAM0_SCRATCH_OFFSET;
        const uintptr_t appdma  = cram0 + OF_TARGET_CRAM0_APP_DMA_OFFSET;
        cram0_probe_acquire();
        rc = addrtag_sweep("cram0", scratch, OF_TARGET_CRAM0_DMA_CHUNK_SIZE, 1024u);
        if (!rc) rc = dense_block("cram0", scratch, 4096u);
        if (!rc) rc = dense_block("cram0", appdma, 4096u);
        cram0_probe_release();
    }

    if (rc)
        of_term_printf("\n  \033[91m%s fault\033[0m @ %08x wrote %08x read %08x\n",
                       fail_region, (unsigned)fail_addr,
                       (unsigned)fail_want, (unsigned)fail_got);
    return rc;
}
