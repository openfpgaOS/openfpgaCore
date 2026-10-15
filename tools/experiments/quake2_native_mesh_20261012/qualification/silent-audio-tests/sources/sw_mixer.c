//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * openfpgaOS software audio mixer — C port of audio_mixer.v (v3).
 *
 * See sw_mixer.h for the role.  This file is ALWAYS compiled and linked now
 * (ONE os.bin runs on every bitstream); the render/pump entry points
 * early-return when of_mixer_use_sw==0 (a HW audio_mixer.v is present), so
 * the HW build pays ~nothing.  It is the active mixer only on cores that cut
 * audio_mixer.v (OS30 Pocket, HW_FEATURES OF_HW_MIXER_HW clear → boot sets
 * of_mixer_use_sw=1).  Every numeric step below is a deliberate, byte-exact
 * mirror of the RTL in src/fpga/common/audio_mixer.v and is validated against
 * that module's Verilator reference test (src/fpga/test/tb_audio_mixer_main.cpp).
 * When changing the math here, change it there too.
 *
 * Register-write visibility is CHUNK-boundary exact: sw_mixer_render()
 * snapshots the read-only voice parameters (ADDR, RATE, LEN, LOOP_START,
 * LOOP_END, CTRL flags, group, VOL_TARGET, VOL_RATE) and the active set
 * once per call, so a
 * MIX_VOICE_* write lands at the next render call (<= SW_PUMP_CHUNK frames,
 * ~1.3 ms).  On this single-core CPU that only differs from per-frame reads
 * when an ISR writes voice registers while a main-thread pump is mid-render;
 * no ISR path does that today.  POS_INT/POS_FRAC/VOL_LR stay live in the
 * array every frame so MIX_VOICE_POS reads remain coherent.
 *
 * Voice-table field layout (matches audio_mixer.v's VTBL_* localparams and
 * the MIX_VOICE_* MMIO macros in regs.h):
 *   field 0  ADDR        absolute SDRAM byte address of sample 0
 *   field 1  LEN         total sample count (22-bit)
 *   field 2  RATE        Q16.16 playback step (0x10000 = 1.0)
 *   field 3  CTRL        {stream[3], loop[2], stereo[1], active[0]}
 *   field 4  POS_INT     integer sample position (22-bit)
 *   field 5  POS_FRAC    Q0.16 fractional position
 *   field 6  VOL_LR      {vol_r[15:8], vol_l[7:0]} current (ramped) volume
 *   field 7  LOOP_END    loop end sample index (22-bit)
 *   field 8  LOOP_START  loop start sample index (22-bit)
 *   field 9  VOL_TARGET  {tgt_r[15:8], tgt_l[7:0]} pre-gxm target
 *   field 10 VOL_RATE    ramp step (0 = snap)
 *   field 11 WPTR        stream-mode producer write pointer (22-bit)
 */

#include "sw_mixer.h"
#include "regs.h"

#define SW_MIX_VOICES        32u
#define SW_MIX_VOICE_STRIDE  16u   /* 16 words per voice (matches HW stride) */

/* Field indices within a voice's 16-word slot. */
#define VF_ADDR        0u
#define VF_LEN         1u
#define VF_RATE        2u
#define VF_CTRL        3u
#define VF_POS_INT     4u
#define VF_POS_FRAC    5u
#define VF_VOL_LR      6u
#define VF_LOOP_END    7u
#define VF_LOOP_START  8u
#define VF_VOL_TARGET  9u
#define VF_VOL_RATE   10u
#define VF_WPTR       11u

/* Stream-mode thresholds — must match audio_mixer.v's STREAM_LOW /
 * STREAM_FLOOR so SW and HW builds sound identical. */
#define SW_STREAM_LOW    256u
#define SW_STREAM_FLOOR  4u

/* Control-register byte offsets inside the emulated MMIO aperture. */
#define OFF_MASTER_VOL   0x800u
#define OFF_GROUP_VOL0   0x804u
#define OFF_GROUP_LO     0x814u
#define OFF_GROUP_HI     0x818u
#define OFF_CTRL         0x820u
#define OFF_IRQ          0x824u
#define OFF_ACTIVE_MASK  0x830u
#define OFF_POS_RD_BASE  0x880u

/* ---------------------------------------------------------------------
 * Register backing store.  Mirrors the HW slave's 0x000..0x8FF decode.
 * Word-indexed (byte >> 2).  Shared with regs.h's SW_MIX_W() macro and
 * read directly by the render loop.  volatile because both the 1 kHz pump
 * ISR and the main thread touch it through the HAL.
 * ------------------------------------------------------------------- */
volatile uint32_t sw_mix_regs[SW_MIX_APERTURE_WORDS];

/* Voice-end pending bitmap + W1C doorbell (see regs.h).  The render loop
 * sets bits when a one-shot voice retires; readers drain the doorbell so
 * a `MIX_IRQ_CLEAR = bits` write takes effect as W1C regardless of when it
 * is observed. */
static volatile uint32_t sw_irq_pending;
static volatile uint32_t sw_irq_doorbell;

/* Active-voice shadow, mirrored from CTRL[0] writes and cleared when a
 * one-shot voice ends.  Exposed via MIX_ACTIVE_MASK. */
static volatile uint32_t sw_active_mask;

static volatile uint32_t sw_mixer_inited;

static inline volatile uint32_t *vfield(uint32_t voice, uint32_t field)
{
    return &sw_mix_regs[(voice * SW_MIX_VOICE_STRIDE) + field];
}

/* Apply any pending W1C clear bits to the pending bitmap. */
static inline void drain_irq_doorbell(void)
{
    uint32_t d = sw_irq_doorbell;
    if (d) {
        sw_irq_pending &= ~d;
        sw_irq_doorbell = 0;
    }
}

/* =====================================================================
 * regs.h backing-store hooks
 * ===================================================================== */

/* Read side for the read/write-divergent registers (regs.h routes the
 * plain RW fields straight to the array via SW_MIX_W). */
uint32_t sw_mixer_reg_read(uint32_t mix_byte_addr)
{
    switch (mix_byte_addr) {
    case OFF_IRQ:
        drain_irq_doorbell();
        return sw_irq_pending;
    case OFF_ACTIVE_MASK:
        return sw_active_mask;
    default:
        /* Per-voice POS read aperture (0x880 + voice*4): return pos_int. */
        if (mix_byte_addr >= OFF_POS_RD_BASE && mix_byte_addr < OFF_POS_RD_BASE + (SW_MIX_VOICES << 2)) {
            uint32_t voice = (mix_byte_addr - OFF_POS_RD_BASE) >> 2;
            return *vfield(voice, VF_POS_INT) & 0x3FFFFFu;
        }
        return sw_mix_regs[mix_byte_addr >> 2];
    }
}

/* MIX_IRQ_CLEAR = bits  ->  *sw_mix_irq_doorbell() = bits.  Returning the
 * doorbell pointer FLUSHES any prior staged clear into the pending bitmap
 * first, so the subsequent overwriting store can never drop an earlier
 * un-drained clear — back-to-back `MIX_IRQ_CLEAR = a; MIX_IRQ_CLEAR = b;`
 * applies both.  The render loop and pending reads also flush, so W1C is
 * correct in every observed order. */
volatile uint32_t *sw_mix_irq_doorbell(void)
{
    drain_irq_doorbell();
    return &sw_irq_doorbell;
}

/* =====================================================================
 * CTRL[0] active-shadow tracking.
 *
 * The HW maintains voice_active from CTRL writes + voice-end clears.  In
 * the SW model the array store of MIX_VOICE_CTRL(v) lands in the array
 * (via SW_MIX_W), but the active shadow has to track it.  Rather than
 * intercept the store, the render loop reads CTRL[0] from the array each
 * pass — so sw_active_mask is recomputed live and MIX_ACTIVE_MASK reflects
 * exactly the voices the render loop considers active.  No separate
 * intercept is required.
 * ===================================================================== */

void sw_mixer_init(void)
{
    if (sw_mixer_inited)
        return;

    for (uint32_t i = 0; i < SW_MIX_APERTURE_WORDS; i++)
        sw_mix_regs[i] = 0;

    /* group/master defaults: full-scale (matches HW slave reset). */
    sw_mix_regs[OFF_MASTER_VOL >> 2] = 0xFFu;
    sw_mix_regs[OFF_GROUP_VOL0 >> 2] = 0xFFu;
    sw_mix_regs[(OFF_GROUP_VOL0 + 4) >> 2] = 0xFFu;
    sw_mix_regs[(OFF_GROUP_VOL0 + 8) >> 2] = 0xFFu;
    sw_mix_regs[(OFF_GROUP_VOL0 + 12) >> 2] = 0xFFu;

    sw_irq_pending  = 0;
    sw_irq_doorbell = 0;
    sw_active_mask  = 0;
    sw_mixer_inited = 1;
}

/* =====================================================================
 * Sample data access.
 *
 * VF_ADDR holds an absolute SDRAM byte address (cached alias 0x1000_0000).
 * The of_audio stream voice writes its ring through the UNCACHED alias and
 * stores VF_ADDR as the cached base, while SFX voices are cbo.flushed to
 * SDRAM by mixer.c before play.  To avoid any cache-coherency hazard the
 * render loop reads sample words through the UNCACHED SDRAM alias, so it
 * always sees the latest producer writes (stream ring, SFX, retrigger).
 *
 * Each uncached read is a full p_axi round trip (~25-45 cycles), so a
 * per-voice 2-entry memo {word addr, word value} short-circuits repeat
 * fetches of the same word: at rate < 1.0 the same source word serves
 * several consecutive output frames, and mono tap0/tap1 share a word half
 * the time.  A memo hit returns the identical bytes by construction, so
 * the mix stays byte-exact.  The memo is invalidated at every chunk-start
 * scan, which closes the producer-rewrite hazard: sample memory cannot
 * change MID-render on this single-core CPU (producers run in the main
 * thread; nested ISR pumps no-op on sw_pump_busy), and any write landing
 * between renders — stream ring refill, SFX (re)load, ADDR/POS retrigger —
 * happens before the next scan.
 * ===================================================================== */
#define SW_MEMO_INVALID  (~(uintptr_t)0)   /* unaligned: matches no word addr */

typedef struct {
    uintptr_t addr[2];   /* memoized word addresses, slot per tap */
    uint32_t  word[2];
} sw_memo_t;

static sw_memo_t sw_memo[SW_MIX_VOICES];

/* Both slots are probed on lookup (so mono taps and rate-1.0 stereo, where
 * this frame's tap0 word is last frame's tap1 word, hit across slots) but
 * only the caller's tap slot is replaced on miss, keeping the slow-moving
 * tap0 and tap1 word streams from evicting each other at odd positions. */
static inline uint32_t fetch_sample_word(uintptr_t word_addr, sw_memo_t *m,
                                         uint32_t tap)
{
    if (m->addr[0] == word_addr) return m->word[0];
    if (m->addr[1] == word_addr) return m->word[1];
    uint32_t word = *(volatile uint32_t *)word_addr;
    m->addr[tap] = word_addr;
    m->word[tap] = word;
    return word;
}

static inline int16_t fetch_mono_sample(uint32_t base_byte, uint32_t idx,
                                        sw_memo_t *m, uint32_t tap)
{
    /* mono stride = 2 bytes; word-align and pick the half. */
    uint32_t byte_addr = base_byte + (idx << 1);
    uintptr_t uncached = (uintptr_t)byte_addr - (uintptr_t)OF_TARGET_SDRAM_BASE
                       + (uintptr_t)OF_TARGET_SDRAM_UNCACHED_BASE;
    uint32_t word = fetch_sample_word(uncached & ~(uintptr_t)3u, m, tap);
    return (byte_addr & 2u) ? (int16_t)(word >> 16) : (int16_t)(word & 0xFFFFu);
}

static inline void fetch_stereo_sample(uint32_t base_byte, uint32_t idx,
                                       sw_memo_t *m, uint32_t tap,
                                       int16_t *l, int16_t *r)
{
    /* stereo stride = 4 bytes; low word L, high word R. */
    uint32_t byte_addr = base_byte + (idx << 2);
    uintptr_t uncached = (uintptr_t)byte_addr - (uintptr_t)OF_TARGET_SDRAM_BASE
                       + (uintptr_t)OF_TARGET_SDRAM_UNCACHED_BASE;
    uint32_t word = fetch_sample_word(uncached & ~(uintptr_t)3u, m, tap);
    *l = (int16_t)(word & 0xFFFFu);
    *r = (int16_t)(word >> 16);
}

/* ramp_step() — byte-exact port of audio_mixer.v's ramp_step function. */
static inline uint8_t ramp_step(uint8_t cur, uint8_t tgt, uint8_t step)
{
    if (step == 0)
        return tgt;
    if (cur < tgt) {
        uint32_t up = (uint32_t)cur + (uint32_t)step;
        return (up > 0xFFu || up >= tgt) ? tgt : (uint8_t)up;
    } else if (cur > tgt) {
        int32_t dn = (int32_t)cur - (int32_t)step;
        return (dn < 0 || dn <= (int32_t)tgt) ? tgt : (uint8_t)dn;
    }
    return cur;
}

static inline int16_t sat16(int32_t v)
{
    if (v > 32767) return 32767;
    if (v < -32768) return -32768;
    return (int16_t)v;
}

/* =====================================================================
 * Per-frame render.  Mirrors the audio_mixer.v FSM's per-sample loop.
 *
 * The read-only voice parameters are snapshotted ONCE per call (see the
 * chunk-boundary contract note at the top of this file), so the per-frame
 * loop touches only the voices that were active at chunk start instead of
 * scanning all 32 voice slots with ~11 volatile loads each at 48 kHz.
 * ===================================================================== */
typedef struct {
    uint32_t base;        /* VF_ADDR */
    uint32_t rate;        /* VF_RATE */
    uint32_t length;      /* VF_LEN, masked */
    uint32_t loop_end;    /* VF_LOOP_END, masked */
    uint32_t loop_start;  /* VF_LOOP_START, masked */
    uint32_t ctrl;        /* raw CTRL word (retire write-back source) */
    uint32_t vol_target;  /* VF_VOL_TARGET */
    uint8_t  vol_rate;
    uint8_t  gxm;         /* (group_vol[group] * master) >> 8, pre-composed */
    uint8_t  voice;
    uint8_t  stereo;
    uint8_t  loop;
    uint8_t  live;        /* cleared when the voice retires mid-chunk */
} sw_snap_t;

/* Static, not stack: render runs in the 1 kHz timer ISR and a ~1.2 KB
 * frame would eat the ISR stack budget.  Render is non-reentrant by
 * contract (sw_pump_busy guards the only call path). */
static sw_snap_t sw_snap[SW_MIX_VOICES];

void sw_mixer_render(int16_t *out, int nframes)
{
    if (!sw_mixer_inited)
        sw_mixer_init();

    /* Global enable mirrors MIX_CTRL[0]; when disabled emit silence. */
    int enabled = (sw_mix_regs[OFF_CTRL >> 2] & 1u) != 0;

    uint8_t master = (uint8_t)(sw_mix_regs[OFF_MASTER_VOL >> 2] & 0xFFu);
    uint8_t gvol[4];
    for (int g = 0; g < 4; g++)
        gvol[g] = (uint8_t)(sw_mix_regs[(OFF_GROUP_VOL0 >> 2) + g] & 0xFFu);

    /* gxm[g] = (group_vol[g] * master) >> 8  (HW pre-composition). */
    uint8_t gxm[4];
    for (int g = 0; g < 4; g++)
        gxm[g] = (uint8_t)(((uint32_t)gvol[g] * (uint32_t)master) >> 8);

    uint32_t group_lo = sw_mix_regs[OFF_GROUP_LO >> 2];
    uint32_t group_hi = sw_mix_regs[OFF_GROUP_HI >> 2];

    /* ---- chunk-start scan: active list + read-only param snapshot ---- */
    uint32_t live_mask = 0;
    uint32_t n_active  = 0;
    for (uint32_t v = 0; v < SW_MIX_VOICES; v++) {
        uint32_t ctrl = *vfield(v, VF_CTRL);
        if (!(ctrl & 1u))             /* active bit */
            continue;
        sw_snap_t *s = &sw_snap[n_active++];
        s->voice      = (uint8_t)v;
        s->ctrl       = ctrl;
        s->stereo     = (ctrl & 2u) != 0;
        s->loop       = (ctrl & 4u) != 0;
        s->base       = *vfield(v, VF_ADDR);
        s->rate       = *vfield(v, VF_RATE);
        s->length     = *vfield(v, VF_LEN) & 0x3FFFFFu;
        s->loop_end   = *vfield(v, VF_LOOP_END) & 0x3FFFFFu;
        s->loop_start = *vfield(v, VF_LOOP_START) & 0x3FFFFFu;
        s->vol_target = *vfield(v, VF_VOL_TARGET);
        s->vol_rate   = (uint8_t)(*vfield(v, VF_VOL_RATE) & 0xFFu);
        uint32_t group = ((v < 16) ? (group_lo >> (v * 2))
                                   : (group_hi >> ((v - 16) * 2))) & 3u;
        s->gxm  = gxm[group];
        s->live = 1;
        live_mask |= (1u << v);
        /* Any parameter/sample write since the last chunk may alias the
         * memoized words; a fresh chunk starts with a cold memo. */
        sw_memo[v].addr[0] = SW_MEMO_INVALID;
        sw_memo[v].addr[1] = SW_MEMO_INVALID;
    }

    for (int f = 0; f < nframes; f++) {
        /* 21-bit signed accumulators: 32 saturated s16 voices fit exactly. */
        int32_t accum_l = 0;
        int32_t accum_r = 0;

        drain_irq_doorbell();

        for (uint32_t si = 0; si < n_active; si++) {
            sw_snap_t *s = &sw_snap[si];
            if (!s->live)             /* retired earlier in this chunk */
                continue;
            uint32_t v = s->voice;

            int stereo = s->stereo;
            int loop   = s->loop;

            uint32_t pos_int  = *vfield(v, VF_POS_INT) & 0x3FFFFFu;
            uint32_t pos_frac = *vfield(v, VF_POS_FRAC) & 0xFFFFu;
            uint32_t base     = s->base;
            uint32_t rate     = s->rate;
            uint32_t length   = s->length;
            uint32_t vol_lr   = *vfield(v, VF_VOL_LR);
            uint8_t  vol_l    = (uint8_t)(vol_lr & 0xFFu);
            uint8_t  vol_r    = (uint8_t)((vol_lr >> 8) & 0xFFu);
            uint8_t  vol_rate = s->vol_rate;
            uint32_t loop_end   = s->loop_end;
            uint32_t loop_start = s->loop_start;
            sw_memo_t *memo = &sw_memo[v];

            /* ---- stream-mode backlog check (S_STREAM_CHK) ----
             * WPTR is re-read every frame (not snapshotted) to mirror the
             * HW's per-sample re-read — the producer may advance it
             * mid-chunk and resume must be immediate. */
            int stream_quiet = 0;
            if (s->ctrl & 8u) {
                uint32_t wptr  = *vfield(v, VF_WPTR) & 0x3FFFFFu;
                /* Same contract as the HW (audio_mixer.v): the backlog
                 * wraps at LENGTH while the advance wraps at LOOP_END→
                 * LOOP_START, so stream voices require loop_start==0,
                 * loop_end==length==ring size, wptr<length. */
                uint32_t avail = (wptr >= pos_int)
                               ? (wptr - pos_int)
                               : (wptr + length - pos_int);
                stream_quiet = (avail < SW_STREAM_LOW);
                if (avail < SW_STREAM_FLOOR) {
                    /* Starved: no fetch, no advance — silence with the
                     * position held.  Jump straight to the volume ramp so
                     * the stream_quiet fade still progresses (the HW skip
                     * to S_RAMP_STEP). */
                    goto volume_ramp;
                }
            }

            /* ---- one-shot end check (S_FETCH_TAP0_CALC) ---- */
            if (pos_int >= length) {
                /* S_VOICE_END: retire one-shot, raise IRQ, clear active. */
                sw_irq_pending |= (1u << v);
                *vfield(v, VF_CTRL) = s->ctrl & ~1u;  /* deactivate (shadow + array) */
                s->live = 0;
                live_mask &= ~(1u << v);
                continue;
            }

            int loop_valid = (loop_end > loop_start);
            uint32_t loop_len = loop_end - loop_start;

            /* ---- tap0 ---- */
            int16_t tap0_l, tap0_r, tap1_l, tap1_r;
            if (stereo) {
                fetch_stereo_sample(base, pos_int, memo, 0, &tap0_l, &tap0_r);
            } else {
                tap0_l = fetch_mono_sample(base, pos_int, memo, 0);
                tap0_r = tap0_l;
            }

            /* ---- tap1 = sample[pos+1] with loop-wrap / clamp ---- */
            uint32_t nxt_raw = pos_int + 1u;
            int wrap_only = loop && loop_valid && (nxt_raw >= loop_end);
            uint32_t nxt_wrapped = wrap_only ? loop_start : nxt_raw;
            int clamp_after_wrap = (nxt_wrapped >= length);

            if (clamp_after_wrap) {
                /* one-shot end next sample: tap1 = tap0 (sample-and-hold). */
                tap1_l = tap0_l;
                tap1_r = tap0_r;
            } else {
                uint32_t tap1_idx = nxt_wrapped;
                if (stereo) {
                    fetch_stereo_sample(base, tap1_idx, memo, 1, &tap1_l, &tap1_r);
                } else {
                    tap1_l = fetch_mono_sample(base, tap1_idx, memo, 1);
                    tap1_r = tap1_l;
                }
            }

            /* ---- LERP (S_LERP_INTERP): tap0 + ((tap1-tap0)*frac >> 16) ---- */
            int32_t diff_l = (int32_t)tap1_l - (int32_t)tap0_l;
            int32_t diff_r = (int32_t)tap1_r - (int32_t)tap0_r;
            int32_t lerp_l = (int32_t)tap0_l + (int32_t)(((int64_t)diff_l * (int32_t)pos_frac) >> 16);
            int32_t lerp_r = (int32_t)tap0_r + (int32_t)(((int64_t)diff_r * (int32_t)pos_frac) >> 16);

            /* ---- MIX (S_LERP_MIX): * current vol, >> 8, saturate, accum ---- */
            int32_t scaled_l = lerp_l * (int32_t)vol_l;
            int32_t scaled_r = lerp_r * (int32_t)vol_r;
            int16_t samp_l = sat16(scaled_l >> 8);
            int16_t samp_r = sat16(scaled_r >> 8);
            accum_l += (int32_t)samp_l;
            accum_r += (int32_t)samp_r;

            /* ---- ADVANCE (S_ADV_*): pos += rate, wrap / end ---- */
            uint32_t nf_full = pos_frac + (rate & 0xFFFFu);
            uint32_t carry   = nf_full >> 16;
            uint32_t np_raw  = (pos_int + carry + (rate >> 16)) & 0x3FFFFFu;
            uint32_t new_frac = nf_full & 0xFFFFu;
            uint32_t new_pos  = np_raw;
            int voice_ended = 0;

            if (loop) {
                if (!loop_valid) {
                    voice_ended = 1;
                } else if (np_raw >= loop_end) {
                    new_pos = np_raw - loop_len;
                    /* repeat-wrap for extreme rates (S_ADV_WRAP). */
                    while (new_pos >= loop_end)
                        new_pos -= loop_len;
                }
            } else {
                if (np_raw >= length)
                    voice_ended = 1;
            }

            if (voice_ended) {
                /* S_VOICE_END after advance. */
                *vfield(v, VF_POS_INT) = new_pos;
                sw_irq_pending |= (1u << v);
                *vfield(v, VF_CTRL) = s->ctrl & ~1u;
                s->live = 0;
                live_mask &= ~(1u << v);
            } else {
                *vfield(v, VF_POS_INT)  = new_pos;
                *vfield(v, VF_POS_FRAC) = new_frac;
            }

            /* ---- VOLUME compose + ramp (S_RAMP_STEP..S_WR_VOL) ----
             * Composed target = (raw_target * gxm[group]) >> 8 per channel,
             * then ramp the current vol toward it by vol_rate.  Updates
             * VF_VOL_LR for the NEXT sample (this sample mixed with the
             * pre-ramp current vol, exactly as the HW pipeline does).
             * A stream voice running low on backlog substitutes a raw
             * target of 0 (the HW's S_WR_VOL_LOAD mux) so it fades out
             * click-free before starving and back in on recovery. */
            volume_ramp:;
            uint32_t vt = stream_quiet ? 0u : s->vol_target;
            uint8_t raw_l = (uint8_t)(vt & 0xFFu);
            uint8_t raw_r = (uint8_t)((vt >> 8) & 0xFFu);
            uint8_t g = s->gxm;
            uint8_t tgt_l = (uint8_t)(((uint32_t)raw_l * (uint32_t)g) >> 8);
            uint8_t tgt_r = (uint8_t)(((uint32_t)raw_r * (uint32_t)g) >> 8);
            uint8_t nxt_l = ramp_step(vol_l, tgt_l, vol_rate);
            uint8_t nxt_r = ramp_step(vol_r, tgt_r, vol_rate);
            if (nxt_l != vol_l || nxt_r != vol_r)
                *vfield(v, VF_VOL_LR) = ((uint32_t)nxt_r << 8) | (uint32_t)nxt_l;
        }

        sw_active_mask = live_mask;

        /* ---- OUTPUT (S_OUTPUT): /16 mixdown + s16 saturate ---- */
        int16_t out_l, out_r;
        if (enabled) {
            out_l = sat16(accum_l >> 4);
            out_r = sat16(accum_r >> 4);
        } else {
            out_l = 0;
            out_r = 0;
        }
        out[(f << 1)]     = out_l;
        out[(f << 1) + 1] = out_r;
    }
}

/* =====================================================================
 * DAC pump.  Polls AUDIO_STATUS (+0x00) and pushes one stereo sample to
 * AUDIO_PCM_SAMPLE (+0x04) per !fifo_full poll.  Renders in small chunks
 * to bound stack + keep the FIFO topped up.  Reentrancy-guarded so a 1 kHz
 * ISR firing while a main-thread pump is in flight is a no-op.
 * ===================================================================== */
#define SW_PUMP_CHUNK  64   /* stereo frames per render burst */

static volatile uint8_t sw_pump_busy;

void sw_mixer_pump(void)
{
    /* HW-mixer build: audio_mixer.v feeds the DAC FIFO autonomously, so the
     * CPU pump is a no-op.  This is the only cost the HW path pays for the
     * always-linked SW mixer (one global load + branch per 1 kHz tick). */
    if (!of_mixer_use_sw)
        return;

    if (sw_pump_busy)
        return;
    sw_pump_busy = 1;

    if (!sw_mixer_inited)
        sw_mixer_init();

    int16_t chunk[SW_PUMP_CHUNK * 2];

    /* Top up the FIFO until full.  fifo_full is the authoritative "no room"
     * signal (a completely full 1024-deep dcfifo reads level == 0). */
    for (;;) {
        uint32_t status = AUDIO_STATUS;
        if (status & AUDIO_FIFO_FULL)
            break;

        uint32_t level = status & AUDIO_FIFO_LEVEL_MASK;
        uint32_t room  = (level <= 1023u) ? (1024u - level) : 0u;
        if (room == 0)
            break;

        uint32_t n = (room < SW_PUMP_CHUNK) ? room : SW_PUMP_CHUNK;
        sw_mixer_render(chunk, (int)n);

        for (uint32_t i = 0; i < n; i++) {
            /* {left[15:0], right[15:0]} — same packing the HW mixer used. */
            uint32_t l = (uint32_t)(uint16_t)chunk[(i << 1)];
            uint32_t r = (uint32_t)(uint16_t)chunk[(i << 1) + 1];
            AUDIO_PCM_SAMPLE = (l << 16) | r;
        }

        /* If we rendered a partial chunk because room < CHUNK, loop again to
         * re-poll; once room is satisfied fifo_full will break us out. */
        if (n < SW_PUMP_CHUNK)
            break;
    }

    sw_pump_busy = 0;
}
