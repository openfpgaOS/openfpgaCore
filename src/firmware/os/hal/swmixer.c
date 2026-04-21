/*
 * openfpgaOS software block mixer — see swmixer.h for overview.
 *
 * Hot path (per block, 1 ms at 48 kHz):
 *   1. Zero accumulators.
 *   2. For each active voice, advance phase and accumulate 48 stereo
 *      samples into int32 accumulators.
 *   3. Saturate to s16, store 48 stereo pairs into the SDRAM ring,
 *      then cbo.clean the three 64-byte cache lines down to physical
 *      SDRAM.  audio_dma.v (AXI M4 on the SDRAM arbiter) drains the
 *      ring into audio_output's dcfifo at 48 kHz, so the CPU is off
 *      the sample-accurate deadline — Doom's renderer can block the
 *      ISR for up to ~85 ms without audible glitches.
 */

#include "swmixer.h"
#include "regs.h"
#include <string.h>

/* DMA ring in cached SDRAM.  Declared as a static array so the linker
 * places it inside the OS's .osdata section (SDRAM 0x10320000..0x103E0000)
 * — safely away from the app's stack (0x13F00000..0x13F80000), the OS
 * runtime stack (0x13F80000..0x14000000), the heap/brk, the mmap region,
 * and the three framebuffers (0x10000000..0x10300000).  Previously the
 * ring sat at a hard-coded 0x13F78000, which — despite the comment that
 * said otherwise — was actually INSIDE the top of the app stack.  Doom
 * stack pushes clobbered ring data and ring writes clobbered stack
 * frames (e.g. return addresses became saturated sample values like
 * 0x9F9F9F9F), producing crashes that persisted even with the ISR
 * disabled.
 *
 * 4096 stereo pairs = 16 KB = ~85 ms at 48 kHz.  Length is a multiple
 * of 8 so no burst wraps the end (audio_dma.v's FSM assumes this), AND
 * a multiple of 16 so the 48-word block write always aligns with
 * 64-byte D-cache lines (SWMIXER_BLOCK_SAMPLES = 48 = 3 × 16). */
#define DMA_RING_PAIRS     4096u

/* Cache-line (64 B) aligned so the block's 3 cache-line writeback runs
 * over exactly 3 lines regardless of where the linker placed us. */
static volatile uint32_t dma_ring[DMA_RING_PAIRS]
    __attribute__((aligned(64)));

/* Writes go through the cached SDRAM alias — each store hits the L1
 * D-cache (~1 cyc/word), not physical SDRAM (~tens of cyc).  After
 * we finish a block, cbo.clean flushes the three 64-byte cache lines
 * down to SDRAM so the DMA master reads fresh data.  Net cost per
 * 48-sample block: ~48 cycles of store + ~3 × 10 cycles of flush. */

static inline void cbo_clean(const void *addr) {
    /* cbo.clean rs1 — Zicbom extension.  Encoded as .insn because the
     * assembler's -march selection doesn't always include Zicbom. */
    __asm__ volatile(".insn i 0x0F, 2, x0, %0, 1" :: "r"(addr) : "memory");
}

/* State pinned to BRAM (.fastdata) — the timer ISR writes here every
 * tick, and ISR SDRAM stores race with concurrent GPU/bridge bus
 * traffic (see the fence comment in start.S::_trap_entry).  Keeping
 * these in BRAM avoids the SDRAM bus entirely for the hot path.
 * Total footprint: ~2.6 KB (voices 1.5 KB + accums 0.4 KB + voice
 * sample cache 0.6 KB + misc). */
#define OF_FASTDATA __attribute__((section(".fastdata")))

static swmixer_voice_t voices[SWMIXER_MAX_VOICES]        OF_FASTDATA;
static uint32_t        ended_mask                        OF_FASTDATA;
static uint32_t        dma_write_idx                     OF_FASTDATA;
static uint8_t         dma_enabled                       OF_FASTDATA;

/* v2 arch: CRAM1 retired.  Sample data lives in the 8 MB SDRAM sample
 * pool at OF_TARGET_SAMPLE_BASE (0x13700000).  Reads go through the L1
 * D$ and the SDRAM bus at full CPU speed — the per-voice 8-word burst
 * cache and cram1_burst_mmio side channel are no longer needed. */

/* Block-scope accumulators — static so they don't blow the trap stack. */
static int32_t accum_l[SWMIXER_BLOCK_SAMPLES]            OF_FASTDATA;
static int32_t accum_r[SWMIXER_BLOCK_SAMPLES]            OF_FASTDATA;

/* Read a single sample from the SDRAM sample pool.
 *
 * sample_byte_addr is a byte offset into the 8 MB pool.  PMA marks the
 * SDRAM region cacheable, so the first read in a cache line pays a
 * line-fill burst and subsequent samples in the same line hit L1 at
 * ~1 cycle each.
 *
 * Hard bounds check: a corrupt voice (pos_int runaway, bad v->sample)
 * can produce an out-of-range offset.  Clamping to silence here
 * contains the blast radius: the voice plays zeros until advance_phase
 * retires it, but the ISR never takes a load access fault. */
static inline int32_t voice_read_sample(int vi, uint32_t sample_byte_addr, int fmt16)
{
    (void)vi;
    if (sample_byte_addr >= OF_TARGET_SAMPLE_SIZE) return 0;
    const uint8_t *base = (const uint8_t *)OF_TARGET_SAMPLE_BASE;
    if (fmt16) {
        return *(const int16_t *)(base + sample_byte_addr);
    } else {
        return (int32_t)(*(const int8_t *)(base + sample_byte_addr)) << 8;
    }
}

static inline int16_t sat16(int32_t x)
{
    if (x >  32767) return  32767;
    if (x < -32768) return -32768;
    return (int16_t)x;
}

/* Byte offset inside the SDRAM sample pool for the given voice
 * position.  v->sample is a CPU pointer into the pool; subtracting
 * the pool base gives the 0..SAMPLE_SIZE-1 offset voice_read_sample
 * wants.  (v2 arch: CRAM1 retired, samples moved to SDRAM.) */
static inline uint32_t voice_sample_byte(const swmixer_voice_t *v, uint32_t idx)
{
    uint32_t base = (uint32_t)v->sample - (uint32_t)OF_TARGET_SAMPLE_BASE;
    uint32_t stride = v->fmt16 ? (v->stereo ? 4u : 2u) : (v->stereo ? 2u : 1u);
    return base + idx * stride;
}

static inline int32_t fetch_mono(int vi, const swmixer_voice_t *v, uint32_t idx)
{
    return voice_read_sample(vi, voice_sample_byte(v, idx), v->fmt16);
}

static inline void fetch_stereo(int vi, const swmixer_voice_t *v, uint32_t idx,
                                int32_t *l, int32_t *r)
{
    /* Stereo samples are interleaved {L, R} in the sample pool.
     * Left is at the base byte offset, right is at +stride_per_channel. */
    uint32_t base_byte = voice_sample_byte(v, idx);
    uint32_t half = v->fmt16 ? 2u : 1u;
    *l = voice_read_sample(vi, base_byte,        v->fmt16);
    *r = voice_read_sample(vi, base_byte + half, v->fmt16);
}

/* Ramp vol_cur toward vol_tgt by one step of vol_rate.  Rate 0 snaps. */
static inline uint8_t ramp_step(uint8_t cur, uint8_t tgt, uint8_t rate)
{
    if (rate == 0) return tgt;
    if (cur < tgt) {
        int n = cur + rate;
        return n >= tgt ? tgt : (uint8_t)n;
    } else if (cur > tgt) {
        int n = (int)cur - rate;
        return n <= tgt ? tgt : (uint8_t)n;
    }
    return cur;
}

/* Advance phase by rate, handling loops and end detection.
 * Returns 1 if the voice is still playable, 0 if it reached the end. */
static int advance_phase(swmixer_voice_t *v)
{
    uint32_t new_frac = (uint32_t)v->pos_frac + (v->rate_fp16 & 0xFFFF);
    uint32_t inc_int  = (v->rate_fp16 >> 16) + (new_frac >> 16);
    v->pos_frac = (uint16_t)(new_frac & 0xFFFF);

    if (v->loop_mode == SWMIXER_LOOP_BIDI && v->dir_rev) {
        if (inc_int >= v->pos_int) {
            /* Bounce at loop_start */
            v->pos_int = v->loop_start;
            v->pos_frac = 0;
            v->dir_rev = 0;
        } else {
            v->pos_int -= inc_int;
        }
        return 1;
    }

    v->pos_int += inc_int;

    uint32_t end = (v->loop_mode == SWMIXER_LOOP_NONE) ? v->len : v->loop_end;
    if (v->pos_int >= end) {
        if (v->loop_mode == SWMIXER_LOOP_FORWARD) {
            uint32_t span = v->loop_end - v->loop_start;
            if (span == 0) return 0;
            v->pos_int = v->loop_start + ((v->pos_int - v->loop_end) % span);
            return 1;
        } else if (v->loop_mode == SWMIXER_LOOP_BIDI) {
            v->pos_int = v->loop_end - 1;
            v->dir_rev = 1;
            return 1;
        } else {
            return 0;  /* one-shot done */
        }
    }
    return 1;
}

void swmixer_init(void)
{
    /* v2 arch: no per-voice burst cache to invalidate — samples are
     * read directly from the SDRAM pool via the L1 D$. */
    memset(voices, 0, sizeof(voices));
    ended_mask = 0;
}

swmixer_voice_t *swmixer_voice(int idx)
{
    if (idx < 0 || idx >= SWMIXER_MAX_VOICES) return (swmixer_voice_t *)0;
    return &voices[idx];
}

void swmixer_stop(int idx)
{
    if (idx < 0 || idx >= SWMIXER_MAX_VOICES) return;
    swmixer_voice_t *v = &voices[idx];
    v->active = 0;
    v->end_pending = 0;
    v->vol_l_cur = v->vol_r_cur = 0;
    v->vol_l_tgt = v->vol_r_tgt = 0;
    /* v2 arch: no burst cache to invalidate — the L1 D$ stays
     * coherent via normal RAW ordering on the SDRAM sample pool. */
}

void swmixer_stop_all(void)
{
    for (int i = 0; i < SWMIXER_MAX_VOICES; i++) swmixer_stop(i);
}

uint32_t swmixer_poll_ended(void)
{
    uint32_t m = ended_mask;
    ended_mask = 0;
    return m;
}

/* Linear interpolate two int16-range samples by a Q0.16 fraction.
 * Algebraic trick to stay in int32: (s1-s0) is 17 bits signed (±65535),
 * (frac >> 1) is 15 bits unsigned (0..32767); their product is ≤
 * 65535 × 32767 = 2,147,418,945 — exactly inside signed int32 range
 * (INT32_MAX = 2,147,483,647).  Shift right 15 gives delta; add to s0.
 * Same result as (s1-s0)*frac>>16 but one 32-bit MUL instead of the
 * MUL+MULH pair a 64-bit multiply compiles to on RV32. */
static inline int32_t lerp16(int32_t s0, int32_t s1, uint32_t frac)
{
    return s0 + (((s1 - s0) * (int32_t)(frac >> 1)) >> 15);
}

/* Nearest-neighbour sampling — dropped the 2-tap linear interp to
 * halve the inner-loop cost (no second fetch, no lerp16 multiply).
 * SF2 samples at 22/44 kHz pitched around a root key get some
 * aliasing, but Doom's SC-55 bank has already lived with much worse
 * reconstruction filters on the original SC-55 hardware.  The CPU
 * saved here goes to Doom's renderer. */
static inline int32_t voice_sample_mono(int vi, const swmixer_voice_t *v)
{
    return fetch_mono(vi, v, v->pos_int);
}

static inline void voice_sample_stereo(int vi, const swmixer_voice_t *v,
                                       int32_t *out_l, int32_t *out_r)
{
    fetch_stereo(vi, v, v->pos_int, out_l, out_r);
}

/* Timing probe: max cycles observed per swmixer_tick since last print,
 * call count since last print.  Printed via UART once per second from
 * inside the tick itself so we don't need a main-thread consumer. */
static uint32_t probe_max_cycles OF_FASTDATA;
static uint32_t probe_calls      OF_FASTDATA;
static uint32_t probe_cycles_sum OF_FASTDATA;
static uint64_t probe_last_print OF_FASTDATA;

static void probe_print(uint64_t now, uint32_t peak, uint32_t calls, uint32_t total)
{
    (void)now;
    /* Raw UART TX — can't trust term_emit in ISR; write directly to the
     * TX register and spin briefly on TX_RDY.  Keeps the probe cheap
     * and safe to call every second from the ISR. */
    const char *hex = "0123456789abcdef";
    char buf[48];
    int i = 0;
    buf[i++] = 'a'; buf[i++] = 'u'; buf[i++] = 'd'; buf[i++] = ' ';
    buf[i++] = 'p'; buf[i++] = 'k'; buf[i++] = '=';
    for (int k = 7; k >= 0; k--) buf[i++] = hex[(peak >> (k*4)) & 0xf];
    buf[i++] = ' '; buf[i++] = 'n'; buf[i++] = '=';
    for (int k = 3; k >= 0; k--) buf[i++] = hex[(calls >> (k*4)) & 0xf];
    buf[i++] = ' '; buf[i++] = 'a'; buf[i++] = 'v'; buf[i++] = '=';
    uint32_t avg = calls ? (total / calls) : 0;
    for (int k = 5; k >= 0; k--) buf[i++] = hex[(avg >> (k*4)) & 0xf];
    buf[i++] = '\n';
    for (int j = 0; j < i; j++) {
        for (int w = 0; w < 5000; w++) {
            if (UART_STATUS & UART_TX_RDY) { UART_TX_DATA = (uint8_t)buf[j]; break; }
        }
    }
}

void swmixer_tick(void)
{
    uint64_t t0 = read_cycles();

    /* Bail out if the DMA ring is already ahead enough.  In steady
     * state this rarely fires (producer == consumer), but it's our
     * safety net against producing past wrap. */
    uint32_t dma_rp = AUDIO_DMA_READ_PTR;
    uint32_t gap    = (dma_write_idx + DMA_RING_PAIRS - dma_rp) & (DMA_RING_PAIRS - 1);
    if (gap + SWMIXER_BLOCK_SAMPLES >= DMA_RING_PAIRS)
        return;

    for (int i = 0; i < SWMIXER_BLOCK_SAMPLES; i++) {
        accum_l[i] = 0;
        accum_r[i] = 0;
    }

    for (int vi = 0; vi < SWMIXER_MAX_VOICES; vi++) {
        swmixer_voice_t *v = &voices[vi];
        if (!v->active) continue;

        /* Fast path: no vol ramp in progress on either channel.
         * Most voices are in steady state (note-on fade-in reaches
         * target within the first block).  Calling ramp_step per sample
         * is pure waste in that case — pull vol_l/r into registers and
         * use the same value for the whole block. */
        int ramping = (v->vol_l_cur != v->vol_l_tgt) ||
                      (v->vol_r_cur != v->vol_r_tgt);
        int32_t vl = v->vol_l_cur;
        int32_t vr = v->vol_r_cur;

        for (int s = 0; s < SWMIXER_BLOCK_SAMPLES; s++) {
            /* Defensive: drop the voice if pos_int has escaped v->len
             * (corrupt state, or runaway rate_fp16).  Prevents piling
             * silence reads into the loop until advance_phase gets
             * around to noticing. */
            if (v->pos_int >= v->len) {
                v->active = 0;
                v->end_pending = 1;
                ended_mask |= (1u << vi);
                break;
            }

            int32_t sl, sr;
            if (v->stereo) {
                voice_sample_stereo(vi, v, &sl, &sr);
            } else {
                int32_t m = voice_sample_mono(vi, v);
                sl = m;
                sr = m;
            }
            accum_l[s] += (sl * vl) >> 8;
            accum_r[s] += (sr * vr) >> 8;

            if (ramping) {
                vl = ramp_step((uint8_t)vl, v->vol_l_tgt, v->vol_rate);
                vr = ramp_step((uint8_t)vr, v->vol_r_tgt, v->vol_rate);
            }

            if (!advance_phase(v)) {
                v->active = 0;
                v->end_pending = 1;
                ended_mask |= (1u << vi);
                break;
            }
        }

        v->vol_l_cur = (uint8_t)vl;
        v->vol_r_cur = (uint8_t)vr;
    }

    /* Write 48 stereo pairs into the SDRAM ring.  Mix-down by 8 (>> 3)
     * gives polyphony headroom; audio_output's post-boost + soft
     * saturation recovers loudness.  The ring is in the cached SDRAM
     * alias so each store hits L1, which is the fastest path we have
     * on RV32 — uncached SDRAM writes would cost ~10× more. */
    uint32_t idx = dma_write_idx;
    const uint32_t mask = DMA_RING_PAIRS - 1;
    for (int i = 0; i < SWMIXER_BLOCK_SAMPLES; i++) {
        uint16_t l = (uint16_t)sat16(accum_l[i] >> 3);
        uint16_t r = (uint16_t)sat16(accum_r[i] >> 3);
        dma_ring[idx] = ((uint32_t)l << 16) | r;
        idx = (idx + 1) & mask;
    }

    /* Flush the three 64-byte cache lines we just wrote down to SDRAM.
     * SWMIXER_BLOCK_SAMPLES = 48 = 3 × 16 words, and dma_write_idx is
     * always a multiple of 16 (ring size 4096 is divisible by 16; each
     * step of 48 stays on 16-word boundaries), so every block occupies
     * exactly three consecutive cache lines — possibly wrapping the end
     * of the ring, which the mask handles. */
    for (int i = 0; i < 3; i++) {
        uint32_t a = (dma_write_idx + (uint32_t)i * 16u) & mask;
        cbo_clean((const void *)&dma_ring[a]);
    }
    fence();

    dma_write_idx = (dma_write_idx + SWMIXER_BLOCK_SAMPLES) & mask;

    /* Lazy enable: flip the DMA on from the first tick.  Deferring the
     * enable until after the bridge has finished streaming the ELF
     * avoids starving the loader — by the time the 1 kHz timer is
     * firing the app is already running. */
    if (!dma_enabled) {
        AUDIO_DMA_CTRL = AUDIO_DMA_ENABLE;
        dma_enabled = 1;
    }

    /* Timing probe — prints "aud pk=... n=... av=..." to UART once per
     * ~1 s so an outside observer can track ISR cost as voice count
     * changes under MIDI load.  The UART spin in probe_print runs
     * from inside the ISR; with 2 Mbaud and ~30-byte prints this is
     * ~150 µs once per second — acceptable overhead. */
    uint32_t elapsed = (uint32_t)(read_cycles() - t0);
    if (elapsed > probe_max_cycles) probe_max_cycles = elapsed;
    probe_cycles_sum += elapsed;
    probe_calls++;
    uint64_t now = read_cycles();
    if ((uint32_t)(now - probe_last_print) >= 100000000u) {
        probe_print(now, probe_max_cycles, probe_calls, probe_cycles_sum);
        probe_max_cycles = 0;
        probe_calls      = 0;
        probe_cycles_sum = 0;
        probe_last_print = now;
    }
}

/* Initialise the SDRAM → audio_output DMA.  Called from of_audio_init
 * once the OS is up.  Zeroes the ring, programs the base/length, and
 * enables streaming.  Mixing starts producing samples as soon as the
 * timer ISR begins firing swmixer_tick. */
uint32_t swmixer_ring_gap(void)
{
    uint32_t dma_rp = AUDIO_DMA_READ_PTR;
    return (dma_write_idx + DMA_RING_PAIRS - dma_rp) & (DMA_RING_PAIRS - 1);
}

void swmixer_dma_start(void)
{
    /* Zero the ring via cached writes, then flush every line to SDRAM
     * so the first DMA burst sees silence.  4096 pairs / 16 per line
     * = 256 cbo.clean operations — one-time cost at boot. */
    for (uint32_t i = 0; i < DMA_RING_PAIRS; i++) dma_ring[i] = 0;
    for (uint32_t i = 0; i < DMA_RING_PAIRS; i += 16)
        cbo_clean((const void *)&dma_ring[i]);
    fence();

    dma_write_idx = 0;
    /* audio_dma.v takes a 32-bit SDRAM byte address; pass the linker's
     * actual placement of dma_ring[] so we never depend on a magic
     * literal that drifts out of the reserved OS data region. */
    AUDIO_DMA_BASE = (uint32_t)&dma_ring[0];
    AUDIO_DMA_LEN  = DMA_RING_PAIRS;

    /* CTRL is NOT set here.  swmixer_tick() flips the enable bit on its
     * first call, which only happens once the app has started its 1 kHz
     * timer — by then the bridge has finished streaming the ELF and
     * app initialisation is complete. */
    dma_enabled = 0;
}
