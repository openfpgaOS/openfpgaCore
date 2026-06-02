//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * bank_preload.c -- Auto-detect and preload an .ofsf SoundFont at boot.
 *
 * After filesystem_init() has populated the file slot registry, scan for
 * the first *.ofsf file. If found, reserve exactly that file size in the
 * persistent audio area, DMA the file in, and validate the header. The
 * loaded base + size are exposed through the services table so apps'
 * of_smp_bank_load can reuse the preload instead of re-reading the file.
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
#include "../hal/regs.h"
#include "../hal/terminal.h"

/* Declared in targets/<target>/file.c; not in hal/file.h because size
 * is served straight from the APF datatable on the Pocket. */
extern long of_file_size(uint32_t slot_id);

static const void *g_bank_base;
static uint32_t    g_bank_size;

/* Anti-aliasing pre-filter applied to the SF2 sample blob at load time.
 *
 * The HW mixer's per-voice playback uses 2-tap linear interpolation,
 * which is a poor reconstruction filter at rate > 1.0 (= the source is
 * being decimated for upshifted notes).  Anything in the source above
 * (output_rate / 2 / playback_rate) folds back as broadband alias
 * noise — audible as random crackle/grit specifically on high-pitch
 * notes (the symptom).  No RTL change can move the LERP order; the
 * cheapest fix is to bandlimit the source itself before storing it.
 *
 * 2nd-order Butterworth LPF at fc = fs/4 (half-band).  At fs/4 cutoff
 * a sample played at 2× upshift (one octave above the SF2 zone's
 * root_key) has no content above (output_rate/2)/2 = 12 kHz at 48 kHz
 * output — alias-free.  3× and beyond still alias somewhat, but the
 * fold-back band is much narrower.  Treble loss on native-pitch
 * playback: about 3 dB at 0.4 × fs (~9 kHz for 22.05 kHz samples) —
 * minor.
 *
 * Coefficients in Q15 fixed-point, computed offline for omega=π/2,
 * Q=0.7071 (RBJ cookbook):
 *   y[n] = (B0*x[n] + B1*x[n-1] + B2*x[n-2] - A1*y[n-1] - A2*y[n-2]) >> 15
 */
#define BQ_B0   9598   /* 0.2929 × 32768 */
#define BQ_B1  19196   /* 0.5858 × 32768 */
#define BQ_B2   9598   /* 0.2929 × 32768 */
#define BQ_A1      0   /* 0      × 32768 (cos(π/2) = 0) */
#define BQ_A2   5624   /* 0.1716 × 32768 */

static void bank_lpf_biquad(int16_t *samples, uint32_t count)
{
    int32_t x1 = 0, x2 = 0, y1 = 0, y2 = 0;
    for (uint32_t i = 0; i < count; i++) {
        int32_t x0 = samples[i];
        /* Direct Form I.  Promote intermediates to int64 to keep
         * head-room: BQ_B1 × INT16_MAX = 19196 × 32767 ≈ 6.3e8, plus
         * accumulating four similar terms — fits comfortably in 64 bits.
         * Final >>15 brings result back to 16-bit-ish magnitude. */
        int64_t y0 = (int64_t)BQ_B0 * x0
                   + (int64_t)BQ_B1 * x1
                   + (int64_t)BQ_B2 * x2
                   - (int64_t)BQ_A1 * y1
                   - (int64_t)BQ_A2 * y2;
        y0 >>= 15;
        if (y0 >  32767) y0 =  32767;
        if (y0 < -32768) y0 = -32768;
        samples[i] = (int16_t)y0;
        x2 = x1; x1 = x0;
        y2 = y1; y1 = (int32_t)y0;
    }
}


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

    void *buf = of_mixer_reserve_persistent((uint32_t)sz,
                                            OF_TARGET_AUDIO_RESERVE_ALIGN);
    if (!buf) {
        of_term_puts(" \033[93mNONE\033[0m\n");
        return -3;
    }

    if (of_file_read_chunked(slot_id, 0, buf, (uint32_t)sz) < 0) {
        of_term_puts(" \033[93mNONE\033[0m\n");
        return -4;
    }

    /* Validate header BEFORE filtering — if the file is malformed we
     * skip the filter pass and fall through to the existing error
     * return (cache flush of unfiltered bytes is harmless). */
    const ofsf_header_t *hdr = (const ofsf_header_t *)buf;
    if (hdr->magic != OFSF_MAGIC || hdr->version != OFSF_VERSION) {
        /* Cache flush still required — same rationale as the success path. */
        of_cache_flush_range(buf, (uint32_t)sz);
        of_cache_invalidate_icache();
        of_term_puts(" \033[93mNONE\033[0m\n");
        return -5;
    }

    /* Anti-aliasing pre-filter pass on the sample blob (see
     * bank_lpf_biquad comment).  ~10 cycles/sample × N samples; for a
     * typical 3 MB SF2 (~1.5 M samples) that's ~150 ms at 100 MHz —
     * one-time boot cost, paid before the first note plays. */
    if (hdr->sample_data_size > 0 &&
        hdr->sample_data_offset + hdr->sample_data_size <= (uint32_t)sz) {
        int16_t *blob = (int16_t *)((uint8_t *)buf + hdr->sample_data_offset);
        uint32_t blob_samples = hdr->sample_data_size / sizeof(int16_t);
        bank_lpf_biquad(blob, blob_samples);
    }

    /* Range-based cbo.flush (writeback + invalidate) of just the bank
     * buffer.  The old of_cache_flush() relied on conflict-eviction over
     * the top 64 KB of SDRAM, which only evicts one way of a 2-way LRU
     * set reliably — leaving ~half of the bank still dirty in L1.  The
     * HW mixer reads SDRAM on its own AXI master and would see zeros
     * for any line that never made it out of cache.  cbo.flush is
     * line-granular and guaranteed per Zicbom.  Follow with fence.i to
     * also invalidate I-cache in case code sits in this region (no-op
     * for data banks).  Done AFTER the LPF pass so the filtered
     * samples are what reaches DRAM. */
    of_cache_flush_range(buf, (uint32_t)sz);
    of_cache_invalidate_icache();

    g_bank_base = buf;
    g_bank_size = (uint32_t)sz;
    services_table_set_smp_bank(buf, (uint32_t)sz);
    of_term_puts(" \033[92mOK\033[0m\n");
    return 0;
}
