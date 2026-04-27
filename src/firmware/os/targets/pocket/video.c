/*
 * openfpgaOS Video HAL Implementation — Triple-buffered
 *
 * Three framebuffers in SDRAM. Hardware tracks which buffer is being
 * displayed and which is queued ("ready") for the next vsync.  The CPU
 * picks its own draw target from whichever buffer is free.
 *
 * of_video_flip()      — non-blocking: queues draw buffer, picks new draw target
 * of_video_flip_wait() — blocking: flip then wait for vsync (frame-locked)
 */

#include "video.h"
#include "regs.h"
#include "cache.h"
#include "analogizer.h"
#include "terminal.h"
#include "../os_string.h"

static const uint32_t fb_addr[3] = { FB0_BASE, FB1_BASE, FB2_BASE };
/* Uncached FB aliases — available for future GPU/DMA use:
 * (FB0_BASE - SDRAM_BASE + SDRAM_UNCACHED_BASE), etc. */

/* Software-tracked buffer roles */
static int buf_display = 0;   /* buffer the hardware is scanning out        */
static int buf_draw    = 1;   /* buffer the CPU is drawing into             */
static int buf_ready   = -1;  /* buffer queued for next vsync (-1 = none)   */

/* Display mode (tracked here for overlay compositing in of_video_flip) */
static int vid_display_mode = DISPLAY_MODE_FRAMEBUFFER;

/* Palette shadow — needed to dim the app palette in overlay mode.
 * Hardware palette is write-only so we track it here. */
static uint32_t pal_shadow[256];

/* VGA bright colors for overlay terminal text (palette indices 240-255) */
static const uint32_t overlay_term_pal[16] = {
    0x000000, 0x0000AA, 0x00AA00, 0x00AAAA,  /*  0-3: black, dk blue/green/cyan */
    0xAA0000, 0xAA00AA, 0xAA5500, 0xAAAAAA,  /*  4-7: dk red/magenta, brown, lt gray */
    0x555555, 0x5555FF, 0x55FF55, 0x55FFFF,  /*  8-11: dk gray, blue, green, cyan */
    0xFF5555, 0xFF55FF, 0xFFFF55, 0xFFFFFF,  /* 12-15: red, magenta, yellow, white */
};

/* ---- VRR (Variable Refresh Rate) ---- */
#define VRR_LINES_PER_SEC   15720u  /* 12576000 / 800 */
#define VRR_HZ_MIN          42      /* 15720/375 ≈ 41.9 Hz */
#define VRR_HZ_MAX          60      /* 15720/262 = 60.0 Hz (default) */
#define VRR_VT_MIN          262     /* 60 Hz — Pocket scaler max */
#define VRR_VT_MAX          375     /* ~42 Hz — Pocket scaler min */
#define VRR_VT_DEFAULT      262
#define VRR_VT_PAL          314     /* 15720/314 ≈ 50.06 Hz — PAL */
#define VRR_STABLE_FRAMES   4       /* frames before applying a rate change */
#define VRR_TOLERANCE       3       /* v_total jitter tolerance in lines */

static uint64_t vrr_last_flip;
static int      vrr_current_vt  = VRR_VT_DEFAULT;
static int      vrr_pending_vt  = VRR_VT_DEFAULT;
static int      vrr_stable_count;
static int      vrr_skip_frames = 8;  /* skip initial frames to stabilize */
static int      vrr_in_gap;           /* 1 = fps 30-40 gap, using swap hold */

static int vrr_abs(int a, int b) { return a > b ? a - b : b - a; }

static void vrr_update(void) {
    uint64_t now = read_cycles();
    uint64_t elapsed = now - vrr_last_flip;
    vrr_last_flip = now;

    /* Let the system stabilize before touching VRR */
    if (vrr_skip_frames > 0) {
        vrr_skip_frames--;
        return;
    }

    /* Analogizer: use fixed PAL or NTSC timing instead of VRR */
    if (of_analogizer_is_enabled()) {
        int video_mode = of_analogizer_get_video_mode();
        int target = (video_mode == ANLG_VIDEO_YC_PAL) ? VRR_VT_PAL : VRR_VT_DEFAULT;
        if (vrr_current_vt != target) {
            vrr_current_vt = target;
            VRR_V_TOTAL = target;
            VRR_SWAP_HOLD = 0;
        }
        return;
    }

    /* Only act on reasonable frame times: 7 fps .. 60 fps
     * Below 7 fps no multiplier N=1..6 can reach 41 Hz, so leave VRR alone.
     * Above 60 fps the content is already at or above the scaler max. */
    uint64_t delta_min = CPU_FREQ_HZ / 60;   /*  1,666,666 cycles (~60 fps) */
    uint64_t delta_max = CPU_FREQ_HZ / 7;    /* 14,285,714 cycles (~7 fps) */
    if (elapsed < delta_min || elapsed > delta_max)
        return;

    uint32_t delta = (uint32_t)elapsed;

    /* fps×10 for sub-Hz precision without floats */
    uint32_t fps_x10 = (uint32_t)((uint64_t)CPU_FREQ_HZ * 10 / delta);

    int new_vt = -1;  /* -1 = no valid target found */
    int gap = 0;

    /* Find smallest multiplier N where fps×N ∈ [41, 58] */
    for (int n = 1; n <= 6; n++) {
        uint32_t hz_x10 = fps_x10 * n;
        if (hz_x10 > VRR_HZ_MAX * 10)
            break;
        if (hz_x10 >= VRR_HZ_MIN * 10) {
            new_vt = (int)((uint64_t)delta * VRR_LINES_PER_SEC /
                           ((uint32_t)n * CPU_FREQ_HZ));
            if (new_vt < VRR_VT_MIN) new_vt = VRR_VT_MIN;
            if (new_vt > VRR_VT_MAX) new_vt = VRR_VT_MAX;
            break;
        }
    }

    /* Gap: fps 30-40 where N=1 < 41 Hz, N=2 > 58 Hz.
     * Use default refresh (58.6 Hz) + hold each frame for 2 vsyncs
     * to get consistent ~29.3 FPS cadence with no judder. */
    if (new_vt < 0 && fps_x10 >= 290) {
        new_vt = VRR_VT_MIN;
        gap = 1;
    }

    /* No valid target (fps too slow or too fast) — don't touch VRR */
    if (new_vt < 0)
        return;

    /* Hysteresis: require consistent target for several frames */
    if (vrr_abs(new_vt, vrr_pending_vt) <= VRR_TOLERANCE) {
        if (++vrr_stable_count >= VRR_STABLE_FRAMES) {
            if (vrr_abs(new_vt, vrr_current_vt) > VRR_TOLERANCE) {
                vrr_current_vt = new_vt;
                VRR_V_TOTAL = new_vt;
            }
            /* Update swap hold when gap state changes */
            if (gap != vrr_in_gap) {
                vrr_in_gap = gap;
                VRR_SWAP_HOLD = gap ? 1 : 0;
            }
        }
    } else {
        vrr_pending_vt = new_vt;
        vrr_stable_count = 1;
    }
}

/* Check whether a pending swap has completed and update software state. */
static void sync_swap_state(void) {
    if (buf_ready >= 0 && !(FB_SWAP_CTRL & 1)) {
        buf_display = buf_ready;
        buf_ready = -1;
    }
}

void of_video_init(void) {
    /* Switch scanout to app triple-buffered FB */
    TERM_FB_CTRL = 0;

    buf_display = 0;
    buf_draw    = 1;
    buf_ready   = -1;
    vid_display_mode = DISPLAY_MODE_FRAMEBUFFER;

    /* Initialize VRR state */
    vrr_last_flip = read_cycles();
    vrr_current_vt = VRR_VT_DEFAULT;
    vrr_pending_vt = VRR_VT_DEFAULT;
    vrr_stable_count = 0;
    vrr_skip_frames = 8;
    vrr_in_gap = 0;
    VRR_V_TOTAL = VRR_VT_DEFAULT;
    VRR_SWAP_HOLD = 0;

    /* FBs live at the CACHED SDRAM alias (FBn_BASE = 0x10xxxxxx) — these
     * memsets populate L1 D$ dirty lines. Clean ranges after each so
     * whichever buffer HW starts scanning sees zeros in SDRAM, not
     * whatever stale data happens to still be in SDRAM at boot. */
    memset((void *)FB0_BASE, 0, FB_SIZE);
    memset((void *)FB1_BASE, 0, FB_SIZE);
    memset((void *)FB2_BASE, 0, FB_SIZE);
    of_cache_clean_range((void *)FB0_BASE, FB_SIZE);
    of_cache_clean_range((void *)FB1_BASE, FB_SIZE);
    of_cache_clean_range((void *)FB2_BASE, FB_SIZE);
}

uint8_t *of_video_get_surface(void) {
    return (uint8_t *)fb_addr[buf_draw];
}

void of_video_flush_cache(void) {
    of_cache_clean_range((void *)fb_addr[buf_draw], FB_SIZE);
}

uint8_t *of_video_flip(void) {
    vrr_update();

    /* Refresh our view of hardware state */
    sync_swap_state();

    /* Overlay mode: composite terminal text over app draw buffer.
     * App pixels use the dimmed palette (set in overlay_install_palette).
     * Terminal text pixels are remapped to 240-255 (bright VGA colors). */
    if (vid_display_mode == DISPLAY_MODE_OVERLAY) {
        const uint8_t *term = (const uint8_t *)TERM_FB_BASE;
        uint8_t *app = (uint8_t *)fb_addr[buf_draw];
        for (int i = 0; i < FB_WIDTH * FB_HEIGHT; i++) {
            if (term[i])
                app[i] = 240 + (term[i] & 0x0F);
        }
    }

    /* Flush dirty FB lines to SDRAM so the scanout DMA sees them.
     * 1,200 cache lines × ~7 cycles per cbo.clean ≈ 84 µs. */
    of_cache_clean_range((void *)fb_addr[buf_draw], FB_SIZE);

    /* Queue draw buffer for display at next vsync.
     * Write format: bits[2:1] = buffer index, bit[0] = trigger */
    FB_SWAP_CTRL = (buf_draw << 1) | 1;

    int old_draw = buf_draw;

    if (buf_ready >= 0) {
        /* A previously queued buffer was replaced — recycle it */
        buf_draw = buf_ready;
    } else {
        /* Pick the free buffer: the one not displaying and not just queued */
        buf_draw = 3 - buf_display - old_draw;
    }

    buf_ready = old_draw;
    return (uint8_t *)fb_addr[buf_draw];
}

void of_video_wait_flip(void) {
    /* Timeout after ~2 frames at 60Hz (~3.3M cycles) to prevent infinite hang */
    uint64_t deadline = read_cycles() + (CPU_FREQ_HZ / 30);
    while (FB_SWAP_CTRL & 1) {
        if (read_cycles() > deadline) break;
    }
    sync_swap_state();
}

void of_video_flip_wait(void) {
    of_video_flip();
    of_video_wait_flip();
}

void of_video_vsync(void) {
    /* Request a swap to the current display buffer (visual no-op).
     * Hardware still waits for vblank to "complete" the swap,
     * giving us a bare vsync wait without cycling the triple buffer. */
    FB_SWAP_CTRL = (buf_display << 1) | 1;

    uint64_t deadline = read_cycles() + (CPU_FREQ_HZ / 30);
    while (FB_SWAP_CTRL & 1) {
        if (read_cycles() > deadline) break;
    }
}

/* Install overlay palette: dim app colors by 50%, bright terminal at 240-255 */
static void overlay_install_palette(void) {
    PAL_INDEX = 0;
    for (int i = 0; i < 240; i++) {
        uint32_t c = pal_shadow[i];
        uint32_t r = (c >> 16) & 0xFF;
        uint32_t g = (c >>  8) & 0xFF;
        uint32_t b =  c        & 0xFF;
        PAL_WRITE = ((r >> 1) << 16) | ((g >> 1) << 8) | (b >> 1);
    }
    for (int i = 0; i < 16; i++)
        PAL_WRITE = overlay_term_pal[i];
}

/* Restore original app palette from shadow */
static void overlay_restore_palette(void) {
    PAL_INDEX = 0;
    for (int i = 0; i < 256; i++)
        PAL_WRITE = pal_shadow[i];
}

void of_video_set_palette(uint8_t index, uint8_t r, uint8_t g, uint8_t b) {
    uint32_t rgb = ((uint32_t)r << 16) | ((uint32_t)g << 8) | b;
    pal_shadow[index] = rgb;
    if (vid_display_mode == DISPLAY_MODE_OVERLAY) {
        overlay_install_palette();
    } else {
        PAL_INDEX = index;
        PAL_WRITE = rgb;
    }
}

void of_video_set_palette_bulk(const uint32_t *palette, int count) {
    for (int i = 0; i < count && i < 256; i++)
        pal_shadow[i] = palette[i];
    if (vid_display_mode == DISPLAY_MODE_OVERLAY) {
        overlay_install_palette();
    } else {
        PAL_INDEX = 0;
        for (int i = 0; i < count && i < 256; i++)
            PAL_WRITE = palette[i];
    }
}

void of_video_set_palette_vga4(const uint8_t *vga_pal, int count) {
    if (count > 256) count = 256;
    for (int i = 0; i < count; i++) {
        uint32_t b = (vga_pal[i * 4 + 0] * 255 + 31) / 63;
        uint32_t g = (vga_pal[i * 4 + 1] * 255 + 31) / 63;
        uint32_t r = (vga_pal[i * 4 + 2] * 255 + 31) / 63;
        pal_shadow[i] = (r << 16) | (g << 8) | b;
    }
    if (vid_display_mode == DISPLAY_MODE_OVERLAY) {
        overlay_install_palette();
    } else {
        PAL_INDEX = 0;
        for (int i = 0; i < count; i++)
            PAL_WRITE = pal_shadow[i];
    }
}

void of_video_set_display_mode(int mode) {
    int prev = vid_display_mode;
    vid_display_mode = mode;
    /* Switch scanout between terminal FB and app triple-buffered FB */
    TERM_FB_CTRL = (mode == DISPLAY_MODE_TERMINAL) ? 1 : 0;

    /* Overlay mode: dim app palette, install bright terminal colors */
    if (mode == DISPLAY_MODE_OVERLAY && prev != DISPLAY_MODE_OVERLAY)
        overlay_install_palette();
    else if (mode != DISPLAY_MODE_OVERLAY && prev == DISPLAY_MODE_OVERLAY)
        overlay_restore_palette();

    of_term_set_display_mode(mode);
}

void of_video_clear(uint8_t color) {
    memset(of_video_get_surface(), color, FB_SIZE);
}
