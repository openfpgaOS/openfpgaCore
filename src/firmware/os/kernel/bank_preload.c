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

    /* Second pass: APF's filename lookup sometimes returns nothing and
     * dir_probe_slots falls back to "slot:N" — sniff the file's first 4
     * bytes for the OFSF magic so we can still identify the bank. */
    if (!name) {
        for (int i = 0; i < count; i++) {
            uint32_t sid;
            const char *bn = file_slot_get(i, &sid);
            if (!bn) continue;
            long sz = of_file_size(sid);
            if (sz <= 0) continue;
            if (slot_has_ofsf_magic(sid, (uint32_t)sz)) {
                slot_id = sid;
                name    = bn;
                break;
            }
        }
    }

    if (!name) {
        of_term_puts("  SoundFont init...  \033[93mno bank\033[0m\n");
        return -1;
    }

    long sz = of_file_size(slot_id);
    if (sz <= 0) {
        of_term_printf("  SoundFont init...  \033[91mslot %u size=%ld\033[0m\n",
                       (unsigned)slot_id, sz);
        return -2;
    }

    void *buf = of_mixer_alloc_samples((uint32_t)sz);
    if (!buf) {
        of_term_printf("  SoundFont init...  \033[91mCRAM1 alloc %lu failed\033[0m\n",
                       (unsigned long)sz);
        return -3;
    }

    if (of_file_read_chunked(slot_id, 0, buf, (uint32_t)sz) < 0) {
        of_term_puts("  SoundFont init...  \033[91mread failed\033[0m\n");
        return -4;
    }

    /* Flush D-cache so the mixer DMA sees the data via the bridge alias. */
    of_cache_flush();

    const ofsf_header_t *hdr = (const ofsf_header_t *)buf;
    if (hdr->magic != OFSF_MAGIC || hdr->version != OFSF_VERSION) {
        of_term_printf("  SoundFont init...  \033[91mbad hdr magic=%08x ver=%u\033[0m\n",
                       (unsigned)hdr->magic, (unsigned)hdr->version);
        return -5;
    }

    g_bank_base = buf;
    g_bank_size = (uint32_t)sz;
    services_table_set_smp_bank(buf, (uint32_t)sz);

    /* Boot line: "  SoundFont init... <name> by <author>" — terminal is
     * 40 cols, so strip a trailing ".sf2" from the embedded INAM (which
     * often carries the source filename verbatim) to leave room. */
    of_term_puts("  SoundFont init... ");
    const char *display = hdr->bank_name[0] ? hdr->bank_name : name;
    int dlen = 0;
    while (display[dlen]) dlen++;
    if (dlen >= 4) {
        const char *tail = display + dlen - 4;
        if ((tail[0] == '.') &&
            ((tail[1] | 0x20) == 's') &&
            ((tail[2] | 0x20) == 'f') &&
            (tail[3] == '2')) {
            /* Print without the ".sf2" suffix */
            for (int i = 0; i < dlen - 4; i++)
                of_term_putchar(display[i]);
        } else {
            of_term_puts(display);
        }
    } else {
        of_term_puts(display);
    }
    if (hdr->bank_author[0])
        of_term_printf(" by %s", hdr->bank_author);
    of_term_puts("\n");

    return 0;
}
