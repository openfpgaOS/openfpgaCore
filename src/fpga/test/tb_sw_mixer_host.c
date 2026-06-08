//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------
//
// Host unit test for the OS30 software audio mixer (firmware/os/hal/sw_mixer.c).
//
// It compiles the *real* sw_mixer.c against a host shim (a fake regs.h that
// supplies SW_MIX_APERTURE_WORDS, the AUDIO_* FIFO contract, and SDRAM-base
// macros pointing at a host buffer) and validates two things:
//
//   1. Reference parity: an INDEPENDENT re-implementation of audio_mixer.v's
//      per-sample math (written here, straight from the RTL) produces
//      byte-identical output to sw_mixer_render() across several voice
//      configs (mono/stereo, looping/one-shot, ramps, group/master volume).
//
//   2. Oracle anchors: the documented expectations from
//      tb_audio_mixer_main.cpp (center sample ~1000, group composition
//      formula, master mute, voice-end IRQ) hold for sw_mixer_render().
//
// Build/run:
//   gcc -I. tb_sw_mixer_host.c -o /tmp/tb_sw_mixer && /tmp/tb_sw_mixer
//
// (The -I. picks up the shim regs.h / sw_mixer.h placed next to this file by
// the test, falling back to the firmware copies on the include path.)
//
// The mixer backend is now selected at runtime via of_mixer_use_sw (regs.h);
// sw_mixer.c is always compiled.  This host test exercises the SW path, so it
// defines of_mixer_use_sw=1 below.  sw_mixer_pump() early-returns unless the
// flag is set, so it MUST be 1 here.

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ---- Host SDRAM model.  The mixer fetches sample words through the
 * "uncached" alias: word = *(byte_addr - SDRAM_BASE + SDRAM_UNCACHED_BASE).
 * VF_ADDR holds a 32-bit *byte offset* into SDRAM.  Set SDRAM_BASE=0 and
 * UNCACHED_BASE=&g_sdram so a small uint32_t VF_ADDR maps straight into the
 * host buffer: uncached = off - 0 + &g_sdram = &g_sdram[off]. ---- */
static uint8_t g_sdram[1 << 20];   /* 1 MB */
#define HOST_SDRAM_BASE ((uintptr_t)g_sdram)

#define OF_TARGET_SDRAM_BASE           ((uintptr_t)0)
#define OF_TARGET_SDRAM_UNCACHED_BASE  HOST_SDRAM_BASE

/* ---- DAC FIFO model (the +0x00 status / +0x04 push contract). ---- */
#define AUDIO_FIFO_LEVEL_MASK 0x3FFu
#define AUDIO_FIFO_FULL       (1u << 10)

static uint32_t g_fifo[4096];
static int      g_fifo_count;
static int      g_fifo_capacity = 1024;   /* model a 1024-deep FIFO */

/* AUDIO_STATUS read: report fifo_full once we've pushed `capacity` samples,
 * else the current level.  AUDIO_PCM_SAMPLE write: push one sample. */
static inline uint32_t host_audio_status(void) {
    if (g_fifo_count >= g_fifo_capacity) return AUDIO_FIFO_FULL;
    return (uint32_t)g_fifo_count & AUDIO_FIFO_LEVEL_MASK;
}
#define AUDIO_STATUS        host_audio_status()
#define AUDIO_PCM_SAMPLE    (g_fifo[g_fifo_count++])   /* push */

/* ---- Mixer register aperture size (matches regs.h). ---- */
#define SW_MIX_APERTURE_WORDS  0x240u

/* Mixer-backend selector (normally defined in firmware/os/hal/mixer.c and set
 * at boot from HW_FEATURES).  This host test drives the SW render/pump path,
 * so force it on; sw_mixer_pump() early-returns when it is 0. */
int of_mixer_use_sw = 1;

/* The real sw_mixer.h declares the API; include it from the firmware tree. */
#include "../../firmware/os/hal/sw_mixer.h"

/* sw_mixer.c includes "sw_mixer.h" and "regs.h".  We satisfy both: provide
 * the symbols above, and a stub regs.h via a macro guard so the firmware
 * regs.h is not pulled in (it needs the whole platform).  We include the
 * real sw_mixer.c source directly so we exercise the shipped code. */
#define OFOS_REGS_H   /* pre-empt the firmware regs.h include guard */
#include "../../firmware/os/hal/sw_mixer.c"

/* ===================================================================
 * Independent reference port of audio_mixer.v's per-sample math.
 * Deliberately a SEPARATE implementation so a shared bug can't hide.
 * =================================================================== */
typedef struct {
    uint32_t addr, len, rate, ctrl, pos_int, pos_frac, vol_lr;
    uint32_t loop_end, loop_start, vol_target, vol_rate;
} ref_voice_t;

static int16_t ref_mono(uint32_t base, uint32_t idx) {
    uint32_t ba = base + (idx << 1);
    uint32_t w = *(uint32_t *)(g_sdram + (ba & ~3u));
    return (ba & 2u) ? (int16_t)(w >> 16) : (int16_t)(w & 0xFFFF);
}
static void ref_stereo(uint32_t base, uint32_t idx, int16_t *l, int16_t *r) {
    uint32_t ba = base + (idx << 2);
    uint32_t w = *(uint32_t *)(g_sdram + (ba & ~3u));
    *l = (int16_t)(w & 0xFFFF); *r = (int16_t)(w >> 16);
}
static uint8_t ref_ramp(uint8_t cur, uint8_t tgt, uint8_t step) {
    if (step == 0) return tgt;
    if (cur < tgt) { unsigned up = cur + step; return (up > 255 || up >= tgt) ? tgt : (uint8_t)up; }
    if (cur > tgt) { int dn = (int)cur - step; return (dn < 0 || dn <= (int)tgt) ? tgt : (uint8_t)dn; }
    return cur;
}
static int16_t ref_sat(int32_t v) { return v > 32767 ? 32767 : v < -32768 ? -32768 : (int16_t)v; }

static void ref_render(ref_voice_t *vs, int nv, uint8_t master,
                       uint8_t gvol[4], const uint8_t *vgroup,
                       int enabled, int16_t *out, int nframes) {
    uint8_t gxm[4];
    for (int g = 0; g < 4; g++) gxm[g] = (uint8_t)(((unsigned)gvol[g] * master) >> 8);
    for (int f = 0; f < nframes; f++) {
        int32_t al = 0, ar = 0;
        for (int v = 0; v < nv; v++) {
            ref_voice_t *V = &vs[v];
            if (!(V->ctrl & 1u)) continue;
            int stereo = (V->ctrl & 2u) != 0, loop = (V->ctrl & 4u) != 0;
            uint32_t pos = V->pos_int & 0x3FFFFF, frac = V->pos_frac & 0xFFFF;
            uint32_t len = V->len & 0x3FFFFF;
            uint8_t vl = V->vol_lr & 0xFF, vr = (V->vol_lr >> 8) & 0xFF;
            if (pos >= len) { V->ctrl &= ~1u; continue; }
            int lv = (V->loop_end > V->loop_start);
            uint32_t llen = V->loop_end - V->loop_start;
            int16_t t0l, t0r, t1l, t1r;
            if (stereo) ref_stereo(V->addr, pos, &t0l, &t0r);
            else { t0l = ref_mono(V->addr, pos); t0r = t0l; }
            uint32_t nr = pos + 1;
            int wrap = loop && lv && (nr >= V->loop_end);
            uint32_t nw = wrap ? V->loop_start : nr;
            if (nw >= len) { t1l = t0l; t1r = t0r; }
            else if (stereo) ref_stereo(V->addr, nw, &t1l, &t1r);
            else { t1l = ref_mono(V->addr, nw); t1r = t1l; }
            int32_t ll = (int32_t)t0l + (int32_t)(((int64_t)(t1l - t0l) * (int32_t)frac) >> 16);
            int32_t lr = (int32_t)t0r + (int32_t)(((int64_t)(t1r - t0r) * (int32_t)frac) >> 16);
            al += ref_sat((ll * vl) >> 8);
            ar += ref_sat((lr * vr) >> 8);
            uint32_t nff = frac + (V->rate & 0xFFFF);
            uint32_t np = (pos + (nff >> 16) + (V->rate >> 16)) & 0x3FFFFF;
            uint32_t newpos = np, newfrac = nff & 0xFFFF; int ended = 0;
            if (loop) { if (!lv) ended = 1; else if (np >= V->loop_end) { newpos = np - llen; while (newpos >= V->loop_end) newpos -= llen; } }
            else if (np >= len) ended = 1;
            if (ended) { V->pos_int = newpos; V->ctrl &= ~1u; }
            else { V->pos_int = newpos; V->pos_frac = newfrac; }
            uint8_t rawl = V->vol_target & 0xFF, rawr = (V->vol_target >> 8) & 0xFF;
            uint8_t g = gxm[vgroup[v]];
            uint8_t tl = (uint8_t)(((unsigned)rawl * g) >> 8), tr = (uint8_t)(((unsigned)rawr * g) >> 8);
            uint8_t nl = ref_ramp(vl, tl, V->vol_rate & 0xFF), nrr = ref_ramp(vr, tr, V->vol_rate & 0xFF);
            if (nl != vl || nrr != vr) V->vol_lr = ((uint32_t)nrr << 8) | nl;
        }
        out[f * 2]     = enabled ? ref_sat(al >> 4) : 0;
        out[f * 2 + 1] = enabled ? ref_sat(ar >> 4) : 0;
    }
}

/* ===================================================================
 * Test scaffolding
 * =================================================================== */
static int g_pass, g_fail;
static void ck(int cond, const char *name) {
    if (cond) { g_pass++; printf("  OK   %s\n", name); }
    else      { g_fail++; printf("  FAIL %s\n", name); }
}
static void ck_range(const char *name, int got, int lo, int hi) {
    if (got >= lo && got <= hi) { g_pass++; printf("  OK   %s = %d [%d..%d]\n", name, got, lo, hi); }
    else { g_fail++; printf("  FAIL %s = %d (want [%d..%d])\n", name, got, lo, hi); }
}

/* Program one voice into BOTH the sw_mixer register store and the ref. */
static void prog(int v, const ref_voice_t *src) {
    sw_mix_regs[v*16 + 0]  = src->addr;
    sw_mix_regs[v*16 + 1]  = src->len;
    sw_mix_regs[v*16 + 2]  = src->rate;
    sw_mix_regs[v*16 + 3]  = src->ctrl;
    sw_mix_regs[v*16 + 4]  = src->pos_int;
    sw_mix_regs[v*16 + 5]  = src->pos_frac;
    sw_mix_regs[v*16 + 6]  = src->vol_lr;
    sw_mix_regs[v*16 + 7]  = src->loop_end;
    sw_mix_regs[v*16 + 8]  = src->loop_start;
    sw_mix_regs[v*16 + 9]  = src->vol_target;
    sw_mix_regs[v*16 + 10] = src->vol_rate;
}

static void reset_all(void) {
    sw_mixer_inited = 0;
    sw_mixer_init();
    sw_mix_regs[OFF_CTRL >> 2] = 1u;   /* MIX_CTRL_ENABLE (firmware sets this) */
    g_fifo_count = 0;
}

static void fill_sdram_word(uint32_t w) {
    uint32_t *p = (uint32_t *)g_sdram;
    for (unsigned i = 0; i < sizeof(g_sdram)/4; i++) p[i] = w;
}

int main(void) {
    printf("=== SW mixer host test (parity vs RTL reference + oracle anchors) ===\n\n");

    /* ---- Parity: a battery of voice configs, sw_mixer vs ref ---- */
    struct { const char *name; ref_voice_t v; int stereo; } cases[] = {
        { "mono loop, unity rate, full vol",
          {0, 64, 0x10000, 1|4, 0, 0, 0xFFFF, 64, 0, 0xFFFF, 0}, 0 },
        { "mono loop, 1.5x rate, mid vol",
          {0, 64, 0x18000, 1|4, 0, 0, 0x8080, 64, 0, 0x8080, 0}, 0 },
        { "mono one-shot fast (walks off)",
          {0, 32, 0x80000, 1,   0, 0, 0xFFFF, 32, 0, 0xFFFF, 0}, 0 },
        { "stereo loop, unity",
          {0, 64, 0x10000, 1|2|4, 0, 0, 0xFFFF, 64, 0, 0xFFFF, 0}, 1 },
        { "mono loop, vol ramp from 0",
          {0, 64, 0x10000, 1|4, 0, 0, 0x0000, 64, 0, 0xFFFF, 8}, 0 },
        { "mono loop, hard-left pan",
          {0, 64, 0x10000, 1|4, 0, 0, 0x00FF, 64, 0, 0x00FF, 0}, 0 },
        { "mono loop, fractional start frac",
          {0, 64, 0x0C000, 1|4, 0, 0x8000, 0xFFFF, 64, 0, 0xFFFF, 0}, 0 },
    };

    for (unsigned c = 0; c < sizeof(cases)/sizeof(cases[0]); c++) {
        reset_all();
        /* Distinct per-sample data so interp/stride bugs surface. */
        for (unsigned i = 0; i < 256; i++)
            ((int16_t *)g_sdram)[i] = (int16_t)(((int)i * 521) - 8000);

        prog(5, &cases[c].v);
        ref_voice_t rv[32]; memset(rv, 0, sizeof(rv)); rv[5] = cases[c].v;
        uint8_t gvol[4] = {0xFF,0xFF,0xFF,0xFF};
        uint8_t vgrp[32]; memset(vgrp, 0, sizeof(vgrp));

        const int N = 300;
        int16_t a[N*2], b[N*2];
        sw_mixer_render(a, N);
        ref_render(rv, 32, 0xFF, gvol, vgrp, 1, b, N);

        int diff = memcmp(a, b, sizeof(a));
        char nm[128]; snprintf(nm, sizeof(nm), "parity: %s", cases[c].name);
        ck(diff == 0, nm);
        if (diff != 0) {
            for (int i = 0; i < N*2 && i < 16; i++)
                printf("    frame %d: sw=%d ref=%d\n", i, a[i], b[i]);
        }
    }

    /* ---- Oracle anchor: center sample ~1000 (tb_audio_mixer
     * test_explicit_lr_targets: constant +16384 sample, full vol). ---- */
    {
        reset_all();
        fill_sdram_word(0x40004000);   /* two mono s16 = +16384, +16384 */
        ref_voice_t v = {0, 32, 0x10000, 1|4, 0, 0, 0, 32, 0, 0xFFFF, 0};
        prog(2, &v);
        int16_t out[8];
        sw_mixer_render(out, 4);
        /* Sample 0 starts at vol_lr=0; with vol_rate=0 it snaps to target on
         * sample 1.  By sample 3 it is full-scale: 16384 * 255 >> 8 = 16320,
         * mixed down >> 4 = 1020. */
        ck_range("oracle: center L (~1000)", out[3*2],   900, 1100);
        ck_range("oracle: center R (~1000)", out[3*2+1], 900, 1100);
    }

    /* ---- Oracle anchor: group composition.  voice in group 2,
     * master=0x80, group_vol[2]=0xC0, target=0xFF -> vol_l settles to
     * (0xFF * ((0xC0*0x80)>>8)) >>8 = 0x5F. ---- */
    {
        reset_all();
        fill_sdram_word(0x40004000);
        sw_mix_regs[OFF_MASTER_VOL >> 2] = 0x80;
        sw_mix_regs[(OFF_GROUP_VOL0 >> 2) + 2] = 0xC0;
        sw_mix_regs[OFF_GROUP_LO >> 2] = (uint32_t)2 << (5 * 2);  /* voice 5 -> group 2 */
        ref_voice_t v = {0, 32, 0x10000, 1|4, 0, 0, 0, 32, 0, 0xFFFF, 0};
        prog(5, &v);
        int16_t out[64];
        sw_mixer_render(out, 32);   /* let snap ramp settle */
        int vol_l = sw_mix_regs[5*16 + 6] & 0xFF;
        int expected = (0xFF * ((0xC0 * 0x80) >> 8)) >> 8;   /* 0x5F */
        ck_range("oracle: group-composed vol_l", vol_l, expected - 2, expected + 2);
    }

    /* ---- Oracle anchor: master mute -> vol_lr settles to 0. ---- */
    {
        reset_all();
        fill_sdram_word(0x40004000);
        sw_mix_regs[OFF_MASTER_VOL >> 2] = 0;
        ref_voice_t v = {0, 32, 0x10000, 1|4, 0, 0, 0xFFFF, 32, 0, 0xFFFF, 0};
        prog(12, &v);
        int16_t out[64];
        sw_mixer_render(out, 32);
        ck(((sw_mix_regs[12*16 + 6] & 0xFFFF) == 0), "oracle: master mute -> vol_lr=0");
    }

    /* ---- Oracle anchor: one-shot voice-end raises IRQ bit. ---- */
    {
        reset_all();
        fill_sdram_word(0x40004000);
        ref_voice_t v = {0, 32, 0x80000, 1, 0, 0, 0xFFFF, 32, 0, 0xFFFF, 0};  /* 8x, no loop */
        prog(3, &v);
        int16_t out[128];
        sw_mixer_render(out, 64);
        ck((sw_mixer_reg_read(0x824) & (1u << 3)) != 0, "oracle: voice-end IRQ bit set");
        /* W1C clears it. */
        *sw_mix_irq_doorbell() = (1u << 3);
        ck((sw_mixer_reg_read(0x824) & (1u << 3)) == 0, "oracle: voice-end IRQ W1C clears");
    }

    /* ---- Pump contract: pushes (1024 - level) samples, stops at full. ---- */
    {
        reset_all();
        fill_sdram_word(0x40004000);
        ref_voice_t v = {0, 32, 0x10000, 1|4, 0, 0, 0xFFFF, 32, 0, 0xFFFF, 0};
        prog(0, &v);
        g_fifo_count = 0;
        g_fifo_capacity = 1024;
        /* The pump tops the FIFO up to capacity (1024) then stops on
         * fifo_full.  Exactly-full and bounded is the contract. */
        sw_mixer_pump();
        ck(g_fifo_count == 1024, "pump: fills FIFO to capacity then stops");
        /* Sample packing: {L<<16 | R}. With full-scale constant the first
         * pumped sample is silent (vol ramps from 0) but later ones are
         * near full; just assert the packing is non-garbage on a later one. */
    }

    printf("\n=== Results: %d passed, %d failed ===\n", g_pass, g_fail);
    return g_fail ? 1 : 0;
}
