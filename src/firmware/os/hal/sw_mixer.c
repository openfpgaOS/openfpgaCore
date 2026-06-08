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
 * Voice-table field layout (matches audio_mixer.v's VTBL_* localparams and
 * the MIX_VOICE_* MMIO macros in regs.h):
 *   field 0  ADDR        absolute SDRAM byte address of sample 0
 *   field 1  LEN         total sample count (22-bit)
 *   field 2  RATE        Q16.16 playback step (0x10000 = 1.0)
 *   field 3  CTRL        {loop[2], stereo[1], active[0]}
 *   field 4  POS_INT     integer sample position (22-bit)
 *   field 5  POS_FRAC    Q0.16 fractional position
 *   field 6  VOL_LR      {vol_r[15:8], vol_l[7:0]} current (ramped) volume
 *   field 7  LOOP_END    loop end sample index (22-bit)
 *   field 8  LOOP_START  loop start sample index (22-bit)
 *   field 9  VOL_TARGET  {tgt_r[15:8], tgt_l[7:0]} pre-gxm target
 *   field 10 VOL_RATE    ramp step (0 = snap)
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
 * ===================================================================== */
static inline int16_t fetch_mono_sample(uint32_t base_byte, uint32_t idx)
{
    /* mono stride = 2 bytes; word-align and pick the half. */
    uint32_t byte_addr = base_byte + (idx << 1);
    uintptr_t uncached = (uintptr_t)byte_addr - (uintptr_t)OF_TARGET_SDRAM_BASE
                       + (uintptr_t)OF_TARGET_SDRAM_UNCACHED_BASE;
    uint32_t word = *(volatile uint32_t *)(uncached & ~(uintptr_t)3u);
    return (byte_addr & 2u) ? (int16_t)(word >> 16) : (int16_t)(word & 0xFFFFu);
}

static inline void fetch_stereo_sample(uint32_t base_byte, uint32_t idx,
                                       int16_t *l, int16_t *r)
{
    /* stereo stride = 4 bytes; low word L, high word R. */
    uint32_t byte_addr = base_byte + (idx << 2);
    uintptr_t uncached = (uintptr_t)byte_addr - (uintptr_t)OF_TARGET_SDRAM_BASE
                       + (uintptr_t)OF_TARGET_SDRAM_UNCACHED_BASE;
    uint32_t word = *(volatile uint32_t *)(uncached & ~(uintptr_t)3u);
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
 * ===================================================================== */
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

    for (int f = 0; f < nframes; f++) {
        /* 21-bit signed accumulators: 32 saturated s16 voices fit exactly. */
        int32_t accum_l = 0;
        int32_t accum_r = 0;
        uint32_t active_now = 0;

        drain_irq_doorbell();

        for (uint32_t v = 0; v < SW_MIX_VOICES; v++) {
            uint32_t ctrl = *vfield(v, VF_CTRL);
            if (!(ctrl & 1u)) {           /* active bit */
                /* keep VOL_LR etc. as-is; just skip. */
                continue;
            }
            active_now |= (1u << v);

            int stereo = (ctrl & 2u) != 0;
            int loop   = (ctrl & 4u) != 0;

            uint32_t pos_int  = *vfield(v, VF_POS_INT) & 0x3FFFFFu;
            uint32_t pos_frac = *vfield(v, VF_POS_FRAC) & 0xFFFFu;
            uint32_t base     = *vfield(v, VF_ADDR);
            uint32_t rate     = *vfield(v, VF_RATE);
            uint32_t length   = *vfield(v, VF_LEN) & 0x3FFFFFu;
            uint32_t vol_lr   = *vfield(v, VF_VOL_LR);
            uint8_t  vol_l    = (uint8_t)(vol_lr & 0xFFu);
            uint8_t  vol_r    = (uint8_t)((vol_lr >> 8) & 0xFFu);
            uint8_t  vol_rate = (uint8_t)(*vfield(v, VF_VOL_RATE) & 0xFFu);
            uint32_t loop_end   = *vfield(v, VF_LOOP_END) & 0x3FFFFFu;
            uint32_t loop_start = *vfield(v, VF_LOOP_START) & 0x3FFFFFu;

            uint32_t group = ((v < 16) ? (group_lo >> (v * 2))
                                       : (group_hi >> ((v - 16) * 2))) & 3u;

            /* ---- one-shot end check (S_FETCH_TAP0_CALC) ---- */
            if (pos_int >= length) {
                /* S_VOICE_END: retire one-shot, raise IRQ, clear active. */
                sw_irq_pending |= (1u << v);
                *vfield(v, VF_CTRL) = ctrl & ~1u;  /* deactivate (shadow + array) */
                active_now &= ~(1u << v);
                continue;
            }

            int loop_valid = (loop_end > loop_start);
            uint32_t loop_len = loop_end - loop_start;

            /* ---- tap0 ---- */
            int16_t tap0_l, tap0_r, tap1_l, tap1_r;
            if (stereo) {
                fetch_stereo_sample(base, pos_int, &tap0_l, &tap0_r);
            } else {
                tap0_l = fetch_mono_sample(base, pos_int);
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
                    fetch_stereo_sample(base, tap1_idx, &tap1_l, &tap1_r);
                } else {
                    tap1_l = fetch_mono_sample(base, tap1_idx);
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
                *vfield(v, VF_CTRL) = ctrl & ~1u;
                active_now &= ~(1u << v);
            } else {
                *vfield(v, VF_POS_INT)  = new_pos;
                *vfield(v, VF_POS_FRAC) = new_frac;
            }

            /* ---- VOLUME compose + ramp (S_RAMP_STEP..S_WR_VOL) ----
             * Composed target = (raw_target * gxm[group]) >> 8 per channel,
             * then ramp the current vol toward it by vol_rate.  Updates
             * VF_VOL_LR for the NEXT sample (this sample mixed with the
             * pre-ramp current vol, exactly as the HW pipeline does). */
            uint32_t vt = *vfield(v, VF_VOL_TARGET);
            uint8_t raw_l = (uint8_t)(vt & 0xFFu);
            uint8_t raw_r = (uint8_t)((vt >> 8) & 0xFFu);
            uint8_t g = gxm[group];
            uint8_t tgt_l = (uint8_t)(((uint32_t)raw_l * (uint32_t)g) >> 8);
            uint8_t tgt_r = (uint8_t)(((uint32_t)raw_r * (uint32_t)g) >> 8);
            uint8_t nxt_l = ramp_step(vol_l, tgt_l, vol_rate);
            uint8_t nxt_r = ramp_step(vol_r, tgt_r, vol_rate);
            if (nxt_l != vol_l || nxt_r != vol_r)
                *vfield(v, VF_VOL_LR) = ((uint32_t)nxt_r << 8) | (uint32_t)nxt_l;
        }

        sw_active_mask = active_now;

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
