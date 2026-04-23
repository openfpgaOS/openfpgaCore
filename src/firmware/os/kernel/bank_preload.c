/*
 * bank_preload.c -- Auto-detect and preload an .ofsf SoundFont at boot.
 *
 * After filesystem_init() has populated the file slot registry, scan for
 * the first *.ofsf file. If found, allocate CRAM1 through the mixer's
 * sample pool, DMA the file in, and validate the header. The loaded base
 * + size are exposed through the services table so apps' of_smp_bank_load
 * can reuse the preload instead of re-reading the file (and re-allocating
 * from the same small CRAM1 pool).
 */

#include <stdint.h>
#include <stddef.h>

#include "syscall.h"
#include "bank_preload.h"
#include "services_table.h"
#include "of_smp_bank.h"

#include "../hal/mixer.h"
#include "../hal/cache.h"
#include "../hal/file.h"
#include "../hal/terminal.h"

/* Declared in targets/<target>/file.c; not in hal/file.h because size
 * is served straight from the APF datatable on the Pocket. */
extern long of_file_size(uint32_t slot_id);

static const void *g_bank_base;
static uint32_t    g_bank_size;

const void *bank_preload_base(void) { return g_bank_base; }
uint32_t    bank_preload_size(void) { return g_bank_size; }

static int ends_with_ofsf(const char *name) {
    int n = 0;
    while (name[n]) n++;
    if (n < 5) return 0;
    const char *s = name + n - 5;
    if (s[0] != '.') return 0;
    /* case-insensitive "ofsf" */
    char c1 = s[1] | 0x20, c2 = s[2] | 0x20, c3 = s[3] | 0x20, c4 = s[4] | 0x20;
    return c1 == 'o' && c2 == 'f' && c3 == 's' && c4 == 'f';
}

/* Read the first 16 bytes of a slot and check for the OFSF magic. Used
 * as a fallback when the APF filename lookup in dir_probe_slots() fell
 * back to "slot:N" (so the .ofsf extension isn't visible by name). */
static int slot_has_ofsf_magic(uint32_t slot_id, uint32_t file_size) {
    if (file_size < sizeof(ofsf_header_t))
        return 0;
    /* Read into a 16-byte aligned CRAM1 scratch — of_mixer_alloc_samples
     * gives us a CRAM1 buffer that the bridge DMA can target, and a
     * successful later full-file read will overwrite whatever we sampled
     * here. The buffer stays leased from the pool either way. */
    uint8_t probe[16] __attribute__((aligned(4)));
    if (of_file_read_chunked(slot_id, 0, probe, sizeof(probe)) < 0)
        return 0;
    const uint32_t *p = (const uint32_t *)probe;
    return p[0] == OFSF_MAGIC;
}

int bank_preload(void) {
    if (g_bank_base)
        return 0;

    /* Print the label up front so the user sees it while the scan +
     * DMA are in flight — a 3 MB .ofsf takes noticeable time to load. */
    of_term_puts("  SoundFont init.... ");

    uint32_t    slot_id = 0;
    const char *name    = NULL;
    int         count   = file_slot_get_count();

    /* First pass: match by filename (covers the common case). */
    for (int i = 0; i < count; i++) {
        uint32_t sid;
        const char *bn = file_slot_get(i, &sid);
        if (bn && ends_with_ofsf(bn)) {
            slot_id = sid;
            name    = bn;
            break;
        }
    }

    if (!name) {
        of_term_puts(" \033[93mNONE\033[0m\n");
        return -1;
    }

    long sz = of_file_size(slot_id);
    if (sz <= 0) {
        of_term_puts(" \033[93mNONE\033[0m\n");
        return -2;
    }

    void *buf = of_mixer_alloc_samples((uint32_t)sz);
    if (!buf) {
        of_term_puts(" \033[93mNONE\033[0m\n");
        return -3;
    }

    if (of_file_read_chunked(slot_id, 0, buf, (uint32_t)sz) < 0) {
        of_term_puts(" \033[93mNONE\033[0m\n");
        return -4;
    }

    /* Range-based cbo.flush (writeback + invalidate) of just the bank
     * buffer.  The old of_cache_flush() relied on conflict-eviction over
     * the top 64 KB of SDRAM, which only evicts one way of a 2-way LRU
     * set reliably — leaving ~half of the bank still dirty in L1.  The
     * HW mixer reads SDRAM on its own AXI master and would see zeros
     * for any line that never made it out of cache.  cbo.flush is
     * line-granular and guaranteed per Zicbom.  Follow with fence.i to
     * also invalidate I-cache in case code sits in this region (no-op
     * for data banks). */
    of_cache_flush_range(buf, (uint32_t)sz);
    of_cache_invalidate_icache();

    const ofsf_header_t *hdr = (const ofsf_header_t *)buf;
    if (hdr->magic != OFSF_MAGIC || hdr->version != OFSF_VERSION) {
        of_term_puts(" \033[93mNONE\033[0m\n");
        return -5;
    }

    g_bank_base = buf;
    g_bank_size = (uint32_t)sz;
    services_table_set_smp_bank(buf, (uint32_t)sz);

    of_term_puts(" \033[92mOK\033[0m\n");
    return 0;
}
