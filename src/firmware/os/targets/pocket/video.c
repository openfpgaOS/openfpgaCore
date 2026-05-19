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

static void palette_commit_staged(void) {
    PAL_INDEX = PAL_INDEX_COMMIT;
}

static void palette_wait_ready(void) {
    uint64_t deadline = read_cycles() + (CPU_FREQ_HZ / 30);
    while (PAL_INDEX & PAL_INDEX_BUSY) {
        if (read_cycles() > deadline)
            break;
    }
}

static void palette_upload_shadow(void) {
    palette_wait_ready();
    PAL_INDEX = 0;
    for (int i = 0; i < 256; i++)
        PAL_WRITE = pal_shadow[i];
    palette_commit_staged();
}

/* ---- Display refresh policy ----
 * Firmware owns the adaptive refresh policy and writes VIDEO_VTOTAL. The
 * FPGA still clamps the value and latches it only at frame boundary. When
 * Analogizer video or SNAC is active, RTL overrides this register with fixed
 * NTSC/PAL timing so external hardware does not see adaptive changes. */
static uint64_t vrr_last_swap_cycles;
static uint32_t vrr_current_v_total = VIDEO_VTOTAL_60HZ;
static uint32_t vrr_manual_v_total;  /* 0 = automatic render-period policy */

static uint32_t vrr_clamp_v_total(uint32_t v_total) {
    if (v_total < VIDEO_VTOTAL_MIN)
        return VIDEO_VTOTAL_MIN;
    if (v_total > VIDEO_VTOTAL_MAX)
        return VIDEO_VTOTAL_MAX;
    return v_total;
}

static uint32_t vrr_v_total_from_period(uint32_t cycles) {
    if      (cycles < 1666666u) return VIDEO_VTOTAL_60HZ;
    else if (cycles < 1833333u) return VIDEO_VTOTAL_55HZ;
    else if (cycles < 2022222u) return VIDEO_VTOTAL_50HZ;
    else if (cycles < 2222222u) return VIDEO_VTOTAL_45HZ;
    else if (cycles < 2400000u) return VIDEO_VTOTAL_42HZ;
    else if (cycles < 3333333u) return VIDEO_VTOTAL_60HZ;
    else if (cycles < 3666666u) return VIDEO_VTOTAL_55HZ;
    else if (cycles < 4044444u) return VIDEO_VTOTAL_50HZ;
    else if (cycles < 4444444u) return VIDEO_VTOTAL_45HZ;
    else if (cycles < 4800000u) return VIDEO_VTOTAL_42HZ;
    else if (cycles < 5500000u) return VIDEO_VTOTAL_55HZ;
    else if (cycles < 6066666u) return VIDEO_VTOTAL_50HZ;
    else if (cycles < 6666666u) return VIDEO_VTOTAL_45HZ;
    else                        return VIDEO_VTOTAL_42HZ;
}

static void vrr_apply_v_total(uint32_t v_total) {
    v_total = vrr_clamp_v_total(v_total);
    if (vrr_current_v_total == v_total)
        return;
    VIDEO_VTOTAL = v_total;
    vrr_current_v_total = v_total;
}

static void vrr_update_on_swap(void) {
    uint64_t now = read_cycles();
    uint32_t v_total = vrr_manual_v_total;

    if (v_total == 0) {
        if (vrr_last_swap_cycles == 0) {
            v_total = VIDEO_VTOTAL_60HZ;
        } else {
            uint64_t elapsed = now - vrr_last_swap_cycles;
            v_total = vrr_v_total_from_period(elapsed > 0xFFFFFFFFu
                                              ? 0xFFFFFFFFu
                                              : (uint32_t)elapsed);
        }
    }

    vrr_last_swap_cycles = now;
    vrr_apply_v_total(v_total);
}

void of_video_set_refresh_vtotal(uint32_t v_total) {
    vrr_manual_v_total = v_total ? vrr_clamp_v_total(v_total) : 0;
    vrr_last_swap_cycles = read_cycles();
    vrr_apply_v_total(vrr_manual_v_total ? vrr_manual_v_total
                                         : VIDEO_VTOTAL_60HZ);
}

/* swap_kicked: set when we KNOW fb_swap_pending was at 1 at some point
 * since buf_ready was set.  Without it, sync_swap_state's "buf_ready
 * set + pending=0" check is ambiguous in the GPU-triggered path —
 * the kernel sets buf_ready BEFORE the GPU's CMD_FLIP has actually
 * pulsed gpu_swap_req, so pending=0 might just mean "GPU hasn't fired
 * yet", not "swap completed".  Forcing sync to first observe pending=1
 * disambiguates. */
static int swap_kicked = 0;

static uint32_t buf_ready_serial;
static uint32_t counted_ready_serial;
static volatile uint32_t timing_vblank_count;
static volatile uint32_t timing_present_count;
static volatile uint32_t timing_last_presented_idx;
static volatile uint64_t timing_last_vblank_cycles;
static volatile uint64_t timing_last_present_cycles;

static inline uint32_t video_irq_save_local(void) {
    uint32_t prev;
    __asm__ volatile("csrrci %0, mstatus, 0x8"
                     : "=r"(prev) :: "memory");
    return prev & 0x8u;
}

static inline void video_irq_restore_local(uint32_t prev) {
    if (prev)
        __asm__ volatile("csrrsi zero, mstatus, 0x8" ::: "memory");
}

static inline uint64_t cycles_to_us(uint64_t cycles) {
    return cycles / (CPU_FREQ_HZ / 1000000u);
}

static void mark_buffer_ready(int idx) {
    buf_ready = idx;
    buf_ready_serial++;
    if (buf_ready_serial == counted_ready_serial)
        buf_ready_serial++;
}

static uint64_t present_timestamp_cycles(void) {
    if (VSYNC_IRQ_PENDING)
        return read_cycles();
    return timing_last_vblank_cycles ? timing_last_vblank_cycles : read_cycles();
}

static void timing_record_present_if_needed(int idx) {
    uint32_t irq = video_irq_save_local();
    if (buf_ready_serial != counted_ready_serial) {
        timing_present_count++;
        timing_last_presented_idx = (uint32_t)idx;
        timing_last_present_cycles = present_timestamp_cycles();
        counted_ready_serial = buf_ready_serial;
    }
    video_irq_restore_local(irq);
}

/* Check whether a pending swap has completed and update software state.
 * The display index bits are authoritative; using them matters in the
 * GPU-triggered path where one pending swap can clear and the next
 * CMD_FLIP can set fb_swap_pending again before the kernel samples the
 * low interval. */
static void sync_swap_state(void) {
    uint32_t hw_state = FB_SWAP_CTRL;
    int hw_pending = hw_state & 1;
    int hw_display = (int)((hw_state >> 1) & 0x3);
    if ((unsigned)hw_display < 3)
        buf_display = hw_display;
    if (hw_pending) swap_kicked = 1;
    if (buf_ready >= 0 && !hw_pending &&
        (buf_ready == buf_display || swap_kicked)) {
        timing_record_present_if_needed(buf_ready);
    }
    if (buf_ready == buf_display) {
        buf_ready = -1;
        swap_kicked = 0;
    } else if (buf_ready >= 0 && swap_kicked && !hw_pending) {
        buf_display = buf_ready;
        buf_ready = -1;
        swap_kicked = 0;
    }
}

static int pick_free_buffer(void) {
    for (int i = 0; i < 3; i++) {
        if (i != buf_display && i != buf_ready)
            return i;
    }
    return buf_draw;
}

void of_video_init(void) {
    /* The scanout color decoder is sticky across apps.  Duke and the
     * default framebuffer API render 8-bit indexed pixels, so reset the
     * decoder here before exposing the app FBs.  Apps that want RGB565,
     * 4-bit, etc. call video_set_color_mode() after init. */
    SYS_COLOR_MODE = COLOR_MODE_8BIT;

    /* Switch scanout to app triple-buffered FB */
    TERM_FB_CTRL = 0;

    buf_display = 0;
    buf_draw    = 1;
    buf_ready   = -1;
    swap_kicked = 0;
    buf_ready_serial = 0;
    counted_ready_serial = 0;
    timing_vblank_count = 0;
    timing_present_count = 0;
    timing_last_presented_idx = 0;
    timing_last_vblank_cycles = 0;
    timing_last_present_cycles = 0;
    vrr_last_swap_cycles = read_cycles();
    vrr_current_v_total = VIDEO_VTOTAL_60HZ;
    vrr_manual_v_total = 0;
    VIDEO_VTOTAL = VIDEO_VTOTAL_60HZ;
    vid_display_mode = DISPLAY_MODE_FRAMEBUFFER;
    IRQ_MASK |= IRQ_MASK_VSYNC;

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
    /* Refresh our view of hardware state */
    sync_swap_state();

    /* Overlay mode: composite terminal text over app draw buffer.
     * App pixels use the dimmed palette (set in overlay_install_palette).
     * Terminal text pixels are remapped to 240-255 (bright VGA colors). */
    if (vid_display_mode == DISPLAY_MODE_OVERLAY) {
        const uint8_t *term = (const uint8_t *)TERM_FB_BASE;
        uint8_t *app = (uint8_t *)fb_addr[buf_draw];
        for (uint32_t i = 0; i < FB_WIDTH * FB_HEIGHT; i++) {
            if (term[i])
                app[i] = 240 + (term[i] & 0x0F);
        }
    }

    /* Flush dirty FB lines to SDRAM so the scanout DMA sees them.
     * 1,200 cache lines × ~7 cycles per cbo.clean ≈ 84 µs. */
    of_cache_clean_range((void *)fb_addr[buf_draw], FB_SIZE);

    /* Queue draw buffer for display at next vsync.
     * Write format: bits[2:1] = buffer index, bit[0] = trigger */
    vrr_update_on_swap();
    FB_SWAP_CTRL = (buf_draw << 1) | 1;
    /* Kernel-driven kick — fb_swap_pending is high NOW (sysreg write
     * is committed by the time this returns), so sync_swap_state's
     * positive-observation requirement is already satisfied. */
    swap_kicked = 1;

    int old_draw = buf_draw;

    if (buf_ready >= 0) {
        /* A previously queued buffer was replaced — recycle it */
        buf_draw = buf_ready;
    } else {
        /* Pick the free buffer: the one not displaying and not just queued */
        buf_draw = 3 - buf_display - old_draw;
    }

    mark_buffer_ready(old_draw);
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

uint8_t *of_video_buffer_addr(int idx) {
    if ((unsigned)idx >= 3) return (uint8_t *)0;
    return (uint8_t *)fb_addr[idx];
}

int of_video_acquire_next(int just_flipped_idx, uint32_t fence_token) {
    /* CMD_FLIP path: gpu_core pulses gpu_swap_req after its m_wr drain,
     * then publishes fence_reached.  This function does one bounded
     * wait:
     *
     *   1. fence_reached >= fence_token — proves the GPU finished
     *      its m_wr drain and the slave latched fb_swap_pending=1.
     *      Bounded ~5 ms in case CMD_FLIP wedged.
     *
     * It then returns the third buffer: not the current scanout buffer
     * and not the buffer queued for the next vsync.  Do not wait for
     * fb_swap_pending to clear here; that defeats triple buffering by
     * charging the app for scanout time.  Callers that want to limit
     * themselves to one outstanding flip should call of_video_wait_flip()
     * before queuing the next CMD_FLIP. */
    sync_swap_state();

    if (just_flipped_idx < 0) {
        return buf_draw;
    }

    int fence_ok = 0;
    {
        uint32_t spins = 500000u;            /* ~5 ms @ 100 MHz */
        while ((int32_t)(GPU_FENCE_REACHED_REG - fence_token) < 0) {
            if (--spins == 0) break;
        }
        fence_ok = ((int32_t)(GPU_FENCE_REACHED_REG - fence_token) >= 0);
    }

    sync_swap_state();

    vrr_update_on_swap();
    if (!fence_ok) {
        /* Fallback for a wedged/missing CMD_FLIP: queue the same buffer
         * through the CPU path so the app degrades instead of freezing.
         * Normal successful CMD_FLIP must not write FB_SWAP_CTRL again;
         * re-kicking the same idx can miss the current vsync and insert
         * an avoidable extra frame of latency. */
        FB_SWAP_CTRL = ((uint32_t)(just_flipped_idx & 0x3) << 1) | 1;
    }

    mark_buffer_ready(just_flipped_idx);
    swap_kicked = 1;
    sync_swap_state();
    buf_draw = pick_free_buffer();
    return buf_draw;
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

void of_video_vsync_irq_service(void) {
    timing_vblank_count++;
    timing_last_vblank_cycles = read_cycles();
    sync_swap_state();
}

void of_video_get_timing(of_video_timing_t *out) {
    if (!out)
        return;

    uint32_t irq = video_irq_save_local();
    uint32_t vblank_count = timing_vblank_count;
    uint32_t present_count = timing_present_count;
    uint32_t last_presented_idx = timing_last_presented_idx;
    uint64_t last_vblank_cycles = timing_last_vblank_cycles;
    uint64_t last_present_cycles = timing_last_present_cycles;
    video_irq_restore_local(irq);

    out->vblank_count = vblank_count;
    out->present_count = present_count;
    out->last_presented_idx = last_presented_idx;
    out->reserved = 0;
    out->last_vblank_us = cycles_to_us(last_vblank_cycles);
    out->last_flip_presented_us = cycles_to_us(last_present_cycles);
}

/* Install overlay palette: dim app colors by 50%, bright terminal at 240-255 */
static void overlay_install_palette(void) {
    palette_wait_ready();
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
    palette_commit_staged();
}

/* Restore original app palette from shadow */
static void overlay_restore_palette(void) {
    palette_upload_shadow();
}

void of_video_set_palette(uint8_t index, uint8_t r, uint8_t g, uint8_t b) {
    uint32_t rgb = ((uint32_t)r << 16) | ((uint32_t)g << 8) | b;
    pal_shadow[index] = rgb;
    if (vid_display_mode == DISPLAY_MODE_OVERLAY) {
        overlay_install_palette();
    } else {
        palette_upload_shadow();
    }
}

void of_video_set_palette_bulk(const uint32_t *palette, int count) {
    for (int i = 0; i < count && i < 256; i++)
        pal_shadow[i] = palette[i];
    if (vid_display_mode == DISPLAY_MODE_OVERLAY) {
        overlay_install_palette();
    } else {
        palette_upload_shadow();
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
        palette_upload_shadow();
    }
}

void of_video_set_display_mode(int mode) {
    int prev = vid_display_mode;
    vid_display_mode = mode;

    /* Terminal and overlay rendering are palette-indexed.  If a previous
     * app left direct/low-bit color mode selected, terminal glyphs and
     * overlay pixels decode as black or scrambled data. */
    if (mode == DISPLAY_MODE_TERMINAL || mode == DISPLAY_MODE_OVERLAY)
        SYS_COLOR_MODE = COLOR_MODE_8BIT;

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
