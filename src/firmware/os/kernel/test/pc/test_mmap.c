/*
 * harness.c -- host harness for openfpgaOS's app mmap allocator.
 *
 * Chasing Diablo issue #4: on hardware a 41 KB allocation failed while a
 * 16 MB one succeeded moments later, which rules out plain exhaustion and
 * points somewhere inside the mmap arena or musl's mallocng on top of it.
 * Round-tripping that through an SD card is far too slow, so this drives the
 * REAL sys_mmap2/sys_munmap sources -- extracted verbatim from
 * kernel/syscall.c by extract.sh, never retyped -- against realistic
 * workloads on the host.
 *
 * The arena is mapped at the target's own address (0x10400000) so the
 * kernel's hard sanity check on the 0x10000000-0x40000000 window, and the
 * memset of freshly carved regions, both behave exactly as they do on device.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <errno.h>

#define ENOMEM 12
#define EINVAL 22

/* Arena geometry mirrored from the Pocket's app window (app.ld SDRAM region
 * 0x10400000 + 0x03600000) and the observed boot values. */
#define ARENA_BASE 0x10400000u
#define ARENA_SIZE 0x03600000u /* 54 MB */

static uintptr_t current_brk;
static uintptr_t brk_base;
static uintptr_t mmap_bottom;

/* kernel logging stub -- the allocator now warns once when its slot table
 * fills, and the harness wants to see that fire. */
static int g_slotwarns;
#define of_term_printf(...) do { g_slotwarns++; printf("  [kernel] " __VA_ARGS__); } while (0)

#include "extracted.inc" /* real MMAP_SLOTS / sys_mmap2 / sys_munmap */

/* ---- helpers ---------------------------------------------------------- */

static void arena_reset(void)
{
    memset(mmap_slots, 0, sizeof mmap_slots);
    mmap_slots_used = 0;
    brk_base = current_brk = ARENA_BASE + 0x2A4750u; /* past .text/.data/.bss */
    mmap_bottom = ARENA_BASE + ARENA_SIZE;
}

static unsigned long arena_free_bytes(void)
{
    unsigned long gap = (unsigned long)(mmap_bottom - current_brk);
    unsigned long reusable = 0;
    for (int i = 0; i < MMAP_SLOTS; i++)
        if (mmap_slots[i].base && mmap_slots[i].free)
            reusable += (unsigned long)mmap_slots[i].len;
    return gap + reusable;
}

static void *xmmap(unsigned long n)
{
    long r = sys_mmap2(0, (long)n, 0, 0, -1, 0);
    return r < 0 ? NULL : (void *)(uintptr_t)r;
}

static void xmunmap(void *p, unsigned long n)
{
    sys_munmap((long)(uintptr_t)p, (long)n);
}

static void report(const char *label)
{
    printf("  %-34s slots_used=%4d  bottom=%08lx  gap=%luKB  reusable_free=%luKB\n",
        label, mmap_slots_used, (unsigned long)mmap_bottom,
        (unsigned long)(mmap_bottom - current_brk) / 1024,
        (arena_free_bytes() - (unsigned long)(mmap_bottom - current_brk)) / 1024);
}

/* Largest CONTIGUOUS free region: the bulk gap below mmap_bottom, or the
 * longest run of adjacent free slots. Total free is the wrong yardstick --
 * a request can legitimately fail when the free space is real but scattered
 * between live blocks, and calling that a bug would be a false alarm. */
static unsigned long largest_contiguous_free(void)
{
    unsigned long best = (unsigned long)(mmap_bottom - current_brk);
    for (int i = 0; i < MMAP_SLOTS; i++) {
        if (!mmap_slots[i].base || !mmap_slots[i].free)
            continue;
        /* Walk forward over free neighbours starting at this slot. */
        uintptr_t end = mmap_slots[i].base + mmap_slots[i].len;
        unsigned long run = (unsigned long)mmap_slots[i].len;
        int grew;
        do {
            grew = 0;
            for (int j = 0; j < MMAP_SLOTS; j++) {
                if (!mmap_slots[j].base || !mmap_slots[j].free)
                    continue;
                if (mmap_slots[j].base == end) {
                    run += (unsigned long)mmap_slots[j].len;
                    end += mmap_slots[j].len;
                    grew = 1;
                }
            }
        } while (grew);
        if (run > best)
            best = run;
    }
    return best;
}

/* Can the arena still serve `n`, and is that answer consistent with the
 * largest contiguous region it holds? A NULL while a big enough contiguous
 * region exists is the pathology observed on hardware. */
static int g_spurious;   /* non-zero => regression; drives the exit status */

static int probe(unsigned long n, const char *label)
{
    unsigned long freeb = largest_contiguous_free();
    void *p = xmmap(n);
    int ok = p != NULL;
    if (p)
        xmunmap(p, n);
    int spurious = !ok && freeb >= n;
    if (spurious)
        g_spurious++;
    printf("  probe %-10s %8lu B -> %-4s (largest contiguous %luKB)%s\n", label, n,
        ok ? "ok" : "FAIL", freeb / 1024,
        spurious ? "   <== SPURIOUS ENOMEM" : "");
    return ok;
}

/* ---- workloads -------------------------------------------------------- */

/* mallocng mmaps a fresh group the first time each size class is touched and
 * unmaps it when the class empties. A level load touches many classes in a
 * short burst, so this walks a spread of sizes, freeing each before the next
 * -- the pattern the kernel's exact-fit-only reuse handles worst. */
static void workload_varied_sizes(int rounds)
{
    for (int r = 0; r < rounds; r++) {
        for (unsigned long n = 4096; n <= 512u * 1024; n += 4096) {
            void *p = xmmap(n);
            if (p)
                xmunmap(p, n);
        }
    }
}

/* Same spread, but holding every block live before releasing any -- closer to
 * a level load building its sprite tables. */
static void workload_hold_then_free(unsigned long lo, unsigned long hi,
    unsigned long step, int freeThem)
{
    void *held[512];
    unsigned long sizes[512];
    int n = 0;
    for (unsigned long s = lo; s <= hi && n < 512; s += step) {
        void *p = xmmap(s);
        if (!p)
            break;
        held[n] = p;
        sizes[n] = s;
        n++;
    }
    if (freeThem)
        for (int i = 0; i < n; i++)
            xmunmap(held[i], sizes[i]);
}

int main(void)
{
    void *backing = mmap((void *)(uintptr_t)ARENA_BASE, ARENA_SIZE,
        PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE,
        -1, 0);
    if (backing != (void *)(uintptr_t)ARENA_BASE) {
        fprintf(stderr, "could not map the arena at %08x (%s); "
                        "raise vm.mmap_min_addr or pick another base\n",
            ARENA_BASE, strerror(errno));
        return 77;
    }

    printf("openfpgaOS app-mmap allocator harness (real sys_mmap2/sys_munmap)\n");
    printf("arena %08x + %luKB, MMAP_SLOTS=%d\n\n", ARENA_BASE,
        (unsigned long)ARENA_SIZE / 1024, MMAP_SLOTS);

    /* --- 1. sanity: a fresh arena serves both sizes from the report --- */
    printf("[1] fresh arena\n");
    arena_reset();
    report("after reset");
    probe(42530, "failing");
    probe(16u * 1024 * 1024, "16MB");

    /* --- 2. LIFO churn: should be perfectly recoverable --- */
    printf("\n[2] after LIFO alloc/free churn (varied sizes, 3 rounds)\n");
    arena_reset();
    workload_varied_sizes(3);
    report("after churn");
    probe(42530, "failing");
    probe(16u * 1024 * 1024, "16MB");

    /* --- 3. interleaved hold/free, the fragmenting pattern --- */
    printf("\n[3] hold 400 varied blocks, free every other, then probe\n");
    arena_reset();
    {
        void *held[400];
        unsigned long sizes[400];
        int n = 0;
        for (unsigned long s = 8192; n < 400; s += 4096) {
            void *p = xmmap(s);
            if (!p)
                break;
            held[n] = p;
            sizes[n] = s;
            n++;
        }
        for (int i = 0; i < n; i += 2)
            xmunmap(held[i], sizes[i]);
        printf("  (allocated %d blocks, freed %d)\n", n, (n + 1) / 2);
    }
    report("after interleaved free");
    probe(42530, "failing");
    probe(16u * 1024 * 1024, "16MB");

    /* --- 4. slot-table pressure --- */
    printf("\n[4] slot table pressure (small blocks held live)\n");
    arena_reset();
    workload_hold_then_free(4096, 4096 * 1100, 4096, 0);
    report("after holding");
    probe(42530, "failing");
    probe(16u * 1024 * 1024, "16MB");

    /* --- 5. fill the 1024-entry slot table with small live blocks --- */
    printf("\n[5] slot table FULL (1030 x 4KB live), arena still mostly empty\n");
    arena_reset();
    {
        static void *held[1200];
        int n = 0;
        for (; n < 1030; n++) {
            held[n] = xmmap(4096);
            if (!held[n]) break;
        }
        printf("  (allocated %d x 4KB = %luKB; table holds %d)\n", n,
            (unsigned long)n * 4, mmap_slots_used);
        report("table full");
        probe(42530, "failing");
        probe(16u * 1024 * 1024, "16MB");

        /* Free ONE block, then ask for exactly that size vs a different one.
         * Exact-fit reuse needs no new slot; anything else must carve, which
         * requires a slot the full table cannot give. */
        printf("  -- free one 4KB block, then:\n");
        xmunmap(held[500], 4096);
        probe(4096, "exact-fit");
        probe(8192, "needs-slot");
    }

    printf("\n");
    if (g_spurious) {
        printf("FAIL: %d spurious ENOMEM(s) -- the allocator refused a request "
               "it had contiguous room for.\n", g_spurious);
        return 1;
    }
    printf("PASS: no request was refused while contiguous space existed.\n");
    return 0;
}
