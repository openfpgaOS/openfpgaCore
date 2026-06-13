//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * openfpgaOS Video HAL Implementation — Triple-buffered
 *
 * Three framebuffers in SDRAM. Hardware tracks which buffer is being
 * displayed and which is queued ("ready") for the next vsync.  The CPU
 * picks its own draw target from whichever buffer is free.
 *
 * of_video_flip()      — non-blocking: queues draw buffer, picks new draw target
 */

#include "video.h"
#include "regs.h"
#include "cache.h"
#include "analogizer.h"
#include "terminal.h"
#include "../os_string.h"

static const uint32_t fb_addr[3] = { FB0_BASE, FB1_BASE, FB2_BASE };

/* Software-tracked buffer roles */
static int buf_display = 0;   /* buffer the hardware is scanning out        */
static int buf_draw    = 1;   /* buffer the CPU is drawing into             */
static int buf_ready   = -1;  /* buffer queued for next vsync (-1 = none)   */

/* Display mode (tracked here for overlay compositing in of_video_flip) */
static int vid_display_mode = DISPLAY_MODE_FRAMEBUFFER;
/* Set once an app has taken the display (called of_video_set_mode). Until then
 * the OS owns the screen (boot console / no-app), so the Pocket LCD mode is not
 * applied -- it must never override the boot/console display. */
static int video_app_active = 0;
/* Pocket LCD = Terminal engaged: the analog output keeps the app FB while the
 * LCD shows the console (TERM_FB_CTRL bit 1). When clear, the analog mirrors
 * the LCD selection -- terminal included -- so the boot console, OS terminal
 * and trap screens stay visible on the analog DAC. */
static int vid_pocket_terminal = 0;
static of_video_mode_t vid_mode = {
    FB_WIDTH, FB_HEIGHT, FB_STRIDE, COLOR_MODE_8BIT, 0
};
static uint32_t vid_frame_bytes = FB_SIZE;

/* Common presets for app menus/enumeration. of_video_set_mode() accepts any
 * geometry that passes video_normalize_mode(); this table is not exhaustive. */
static const of_video_mode_t video_modes[] = {
    {256, 224, 0, COLOR_MODE_8BIT, 0},
    {256, 240, 0, COLOR_MODE_8BIT, 0},
    {320, 200, 0, COLOR_MODE_8BIT, 0},
    {320, 224, 0, COLOR_MODE_8BIT, 0},
    {320, 240, 0, COLOR_MODE_8BIT, 0},
    {320, 256, 0, COLOR_MODE_8BIT, 0},
    {320, 288, 0, COLOR_MODE_8BIT, 0},
    {400, 300, 0, COLOR_MODE_8BIT, 0},
    {512, 384, 0, COLOR_MODE_8BIT, 0},
    {640, 360, 0, COLOR_MODE_8BIT, 0},
    {640, 400, 0, COLOR_MODE_8BIT, 0},
    {640, 480, 0, COLOR_MODE_8BIT, 0},
    {800, 600, 0, COLOR_MODE_8BIT, 0},
};

typedef struct {
    uint16_t width;
    uint16_t height;
    uint8_t slot;
} video_scaler_mode_t;

static const video_scaler_mode_t scaler_modes[] = {
    {320, 240, 0},
    {320, 200, 1},
    {320, 224, 2},
    {320, 256, 3},
    {320, 288, 4},
    {400, 300, 5},
    {256, 240, 6},
    {640, 480, 7},   /* 4:3; full-res slot 7 (core_top.v scaler_slot_*(7)) */
};

static const video_scaler_mode_t *video_scaler_mode_for_size(uint16_t width,
                                                             uint16_t height) {
    for (unsigned i = 0; i < sizeof(scaler_modes) / sizeof(scaler_modes[0]); i++) {
        if (scaler_modes[i].width == width && scaler_modes[i].height == height)
            return &scaler_modes[i];
    }
    return &scaler_modes[0];
}

static uint32_t video_line_bytes(uint16_t width, uint8_t color_mode) {
    switch (color_mode) {
    case COLOR_MODE_4BIT:
        return ((uint32_t)width + 1u) >> 1;
    case COLOR_MODE_2BIT:
        return ((uint32_t)width + 3u) >> 2;
    case COLOR_MODE_RGB565:
    case COLOR_MODE_RGB555:
    case COLOR_MODE_RGBA5551:
        return (uint32_t)width * 2u;
    default:
        return width;
    }
}

static uint32_t video_align_stride(uint32_t bytes) {
    return (bytes + 1u) & ~1u;
}

static int video_normalize_mode(const of_video_mode_t *in,
                                of_video_mode_t *out,
                                uint32_t *frame_bytes_out) {
    if (!in || in->width == 0 || in->height == 0)
        return -1;
    if (in->width > FB_MODE_MAX_WIDTH || in->height > FB_MODE_MAX_HEIGHT)
        return -1;
    if (in->color_mode > COLOR_MODE_RGBA5551)
        return -1;

    uint32_t line = video_align_stride(video_line_bytes(in->width,
                                                        in->color_mode));
    uint32_t stride = in->stride ? video_align_stride(in->stride) : line;
    if (stride < line || stride > FB_MODE_MAX_STRIDE)
        return -1;

    uint32_t frame_bytes = stride * (uint32_t)in->height;
    if (frame_bytes == 0 || frame_bytes > (FB1_BASE - FB0_BASE))
        return -1;

    if (out) {
        *out = *in;
        out->stride = (uint16_t)stride;
        out->reserved = 0;
    }
    if (frame_bytes_out)
        *frame_bytes_out = frame_bytes;
    return 0;
}

static void video_program_app_mode(void) {
    SYS_COLOR_MODE = vid_mode.color_mode;
    FB_MODE_SIZE = ((uint32_t)vid_mode.height << 16) | vid_mode.width;
    FB_MODE_STRIDE = vid_mode.stride;
    VIDEO_SCALER_MODE =
        video_scaler_mode_for_size(vid_mode.width, vid_mode.height)->slot;
    /* Keep the analog-output framebuffer geometry = the app's, so the analog
     * DAC keeps showing the app even when the LCD is switched to the terminal
     * (Pocket LCD = Terminal). The analog base tracks the app's display buffer
     * in hardware; only the geometry needs mirroring here. Programmed on every
     * app-mode update and NOT touched by video_program_terminal_mode, so it
     * survives the LCD going to the 320x240 terminal framebuffer. */
    ANALOG_FB_SIZE = ((uint32_t)vid_mode.height << 16) | vid_mode.width;
    ANALOG_FB_STRIDE = vid_mode.stride;
    ANALOG_COLOR_MODE = vid_mode.color_mode;
}

static void video_program_terminal_mode(void) {
    SYS_COLOR_MODE = COLOR_MODE_8BIT;
    FB_MODE_SIZE = ((uint32_t)FB_HEIGHT << 16) | FB_WIDTH;
    FB_MODE_STRIDE = FB_STRIDE;
    VIDEO_SCALER_MODE = VIDEO_SCALER_SLOT_DEFAULT_320X240;
}

static void video_program_visible_mode(void) {
    if (vid_display_mode == DISPLAY_MODE_TERMINAL)
        video_program_terminal_mode();
    else
        video_program_app_mode();
}

static uint32_t video_clamp_frame_bytes(uint32_t bytes) {
    uint32_t slot_bytes = FB1_BASE - FB0_BASE;
    if (bytes == 0 || bytes > slot_bytes)
        return slot_bytes;
    return bytes;
}

static void video_fill_all_buffers_bytes(uint8_t color, uint32_t bytes) {
    bytes = video_clamp_frame_bytes(bytes);
    for (int i = 0; i < 3; i++)
        memset((void *)fb_addr[i], color, bytes);
    for (int i = 0; i < 3; i++)
        of_cache_clean_range((void *)fb_addr[i], bytes);
}

static void video_fill_all_buffers(uint8_t color) {
    video_fill_all_buffers_bytes(color, vid_frame_bytes);
}

static void video_clear_all_buffers(void) {
    video_fill_all_buffers(0);
}

static void video_clear_mode_change_buffers(uint32_t old_frame_bytes) {
    uint32_t bytes = vid_frame_bytes;
    if (old_frame_bytes > bytes)
        bytes = old_frame_bytes;
    if (bytes < FB_SIZE)
        bytes = FB_SIZE;
    video_fill_all_buffers_bytes(0, bytes);
}

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

/* Deferred palette upload.
 *
 * The staged palette commits at vblank, so an upload issued while the
 * previous commit is pending used to SPIN in palette_wait_ready for up
 * to a frame (33 ms cap).  Apps that animate the palette every frame
 * (SVid movie fades, game palette cycling) paid that stall per frame --
 * visible stutter exactly when palettes change.  Instead: pal_shadow is
 * the source of truth; try the upload immediately when the engine is
 * free, otherwise mark dirty and let the vblank IRQ retire it (which is
 * also the correct latch point -- palette and frame swap then apply at
 * the same vsync).  Multiple updates before retire coalesce to the
 * newest shadow.  pal_uploading interlocks the IRQ against a main-line
 * upload in progress (single core: the ISR can preempt main, never the
 * reverse). */
static volatile int pal_dirty;
static volatile int pal_uploading;

static int palette_try_upload_shadow(void) {
    if (PAL_INDEX & PAL_INDEX_BUSY)
        return 0;
    PAL_INDEX = 0;
    for (int i = 0; i < 256; i++)
        PAL_WRITE = pal_shadow[i];
    palette_commit_staged();
    return 1;
}

static void palette_upload_shadow(void) {
    pal_uploading = 1;
    pal_dirty = !palette_try_upload_shadow();
    pal_uploading = 0;
}

static volatile uint32_t timing_vblank_count;
static volatile uint32_t timing_present_count;
static volatile uint32_t timing_last_presented_idx;
static volatile uint64_t timing_last_vblank_cycles;
static volatile uint64_t timing_last_present_cycles;

/* ---- Display refresh policy ----
 * Firmware owns the adaptive refresh policy and writes VIDEO_VTOTAL. The
 * normal LCD policy measures the last frame-ready period, derives a direct
 * integer repeat cadence inside the legal LCD Hz window, and only nudges the
 * final planned repeat by a few scanlines if the last two frame times show a
 * rising or falling trend. The awkward 30-42 fps band cannot map to one legal
 * refresh interval or two legal intervals, so it is presented as a stable
 * 30 fps cadence until the frame time fits one period. Planned repeats run at
 * the computed cadence. If the next frame is still missing after those
 * repeats, extra repeats switch to the fastest safe LCD timing until a new
 * frame arrives, creating quick recovery opportunities without disturbing the
 * normal repeat cadence. If the late window is missed too, the next repeat
 * also uses that fastest timing.
 * The FPGA clamps again and can consume shorter requests in the current frame
 * after active video has completed. The fastest normal-LCD timing is
 * V_TOTAL=514, which measures about 61.30 Hz with the 24.576 MHz video clock
 * and 780-line horizontal total. When Analogizer video or SNAC is active, RTL
 * overrides this register with fixed NTSC/PAL timing so external hardware
 * does not see adaptive changes. */
#define VRR_FASTEST_HZ_MILLI         61299u
#define VRR_CYCLES_PER_LINE_X1024    \
    (((uint64_t)CPU_FREQ_HZ * 1000u * 1024u) / \
     ((uint64_t)VRR_FASTEST_HZ_MILLI * VIDEO_VTOTAL_MIN))
#define VRR_READY_GUARD_LINES        3u
#define VRR_LATE_STRETCH_LINES       32u
#define VRR_TREND_NUDGE_MAX_LINES    8u
#define VRR_MAX_REPEAT_CANDIDATES    8u

static uint64_t vrr_last_swap_cycles;
static uint64_t vrr_ready_period_cycles;
static uint32_t vrr_current_v_total = VIDEO_VTOTAL_60HZ;
static uint32_t vrr_steady_v_total = VIDEO_VTOTAL_60HZ;
static uint32_t vrr_expected_v_total = VIDEO_VTOTAL_60HZ;
static uint32_t vrr_late_v_total = VIDEO_VTOTAL_60HZ + VRR_LATE_STRETCH_LINES;
static uint32_t vrr_repeats_per_frame = 1;
static uint32_t vrr_repeat_phase;
static int32_t vrr_trend_nudge_lines;
static int vrr_recovery_fast;
static int vrr_trend_nudge_allowed;
static uint32_t vrr_manual_v_total;  /* 0 = automatic cadence/recovery policy */

/* APF cont1 type nibble (cont1_key[31:28]): 0x1 = Pocket built-in
 * buttons, which the bridge only reports handheld.  Docked, slot 1
 * carries a paired controller type (or 0x0 when none is connected). */
#define CONT_TYPE_POCKET_BUILTIN 0x1u

/* The adaptive VRR policy rewrites VIDEO_VTOTAL continuously (cadence
 * recomputes, trend nudges, late-recovery stretches) across a
 * 42.01-61.30 Hz span — outside the APF video contract's 47-61 Hz
 * window at BOTH ends.  Only the Pocket's self-syncing LCD tolerates
 * that; fixed-rate sinks need a stable raster:
 *   - the Dock's HDMI output must lock a rate (the 2026-06 "orange
 *     square" symptom — the dock-verified pre-VRR build ran a fixed
 *     525-line 60.02 Hz raster);
 *   - the Analogizer DAC drives TVs/scalers with the same expectation.
 * So: adaptive VRR only when handheld (cont1 type = built-in buttons,
 * only reportable undocked) with analog video off; otherwise pin to
 * 60.02 Hz.  Every policy write then collapses to VIDEO_VTOTAL_60HZ
 * and vrr_apply_v_total()'s change-check makes VIDEO_VTOTAL
 * effectively write-once until the sink changes (dock/undock and menu
 * toggles re-evaluate on the next policy write). */
static int vrr_fixed_rate_sink(void) {
    if (of_analogizer_is_enabled())
        return 1;
    return ((CONT1_KEY >> 28) & 0xFu) != CONT_TYPE_POCKET_BUILTIN;
}

static uint32_t vrr_clamp_v_total(uint32_t v_total) {
    if (vrr_fixed_rate_sink())
        return VIDEO_VTOTAL_60HZ;
    if (v_total < VIDEO_VTOTAL_MIN)
        return VIDEO_VTOTAL_MIN;
    if (v_total > VIDEO_VTOTAL_MAX)
        return VIDEO_VTOTAL_MAX;
    return v_total;
}

static void vrr_apply_v_total(uint32_t v_total) {
    v_total = vrr_clamp_v_total(v_total);
    if (vrr_current_v_total == v_total)
        return;
    VIDEO_VTOTAL = v_total;
    vrr_current_v_total = v_total;
}

static uint32_t vrr_cycles_to_lines_ceil(uint64_t cycles) {
    uint64_t lines = (cycles * 1024u + VRR_CYCLES_PER_LINE_X1024 - 1)
                   / VRR_CYCLES_PER_LINE_X1024;
    if (lines > 0xFFFFFFFFu)
        return 0xFFFFFFFFu;
    return (uint32_t)lines;
}

static uint32_t vrr_cycles_to_lines_nearest(uint64_t cycles) {
    uint64_t lines = (cycles * 1024u + (VRR_CYCLES_PER_LINE_X1024 / 2u))
                   / VRR_CYCLES_PER_LINE_X1024;
    if (lines > 0xFFFFFFFFu)
        return 0xFFFFFFFFu;
    return (uint32_t)lines;
}

static uint32_t vrr_elapsed_lines_since_vblank(uint64_t now) {
    uint64_t frame_start = timing_last_vblank_cycles;
    if (frame_start == 0 || now < frame_start)
        return 0;
    return vrr_cycles_to_lines_ceil(now - frame_start);
}

static void vrr_update_cadence_from_period(void) {
    if (vrr_ready_period_cycles == 0) {
        vrr_repeats_per_frame = 1;
        vrr_steady_v_total = VIDEO_VTOTAL_60HZ;
        vrr_trend_nudge_allowed = 0;
    } else {
        uint32_t period_lines = vrr_cycles_to_lines_nearest(vrr_ready_period_cycles);

        vrr_repeats_per_frame = 1;
        vrr_steady_v_total = VIDEO_VTOTAL_60HZ;
        vrr_trend_nudge_allowed = 0;

        for (uint32_t repeats = 1; repeats <= VRR_MAX_REPEAT_CANDIDATES; repeats++) {
            uint32_t v_total = (period_lines + (repeats / 2u)) / repeats;
            if (v_total >= VIDEO_VTOTAL_MIN && v_total <= VIDEO_VTOTAL_MAX) {
                vrr_repeats_per_frame = repeats;
                vrr_steady_v_total = v_total;
                vrr_trend_nudge_allowed = 1;
                break;
            }
        }

        if (period_lines < VIDEO_VTOTAL_MIN) {
            vrr_repeats_per_frame = 1;
            vrr_steady_v_total = VIDEO_VTOTAL_MIN;
            vrr_trend_nudge_allowed = 0;
        } else if (period_lines > (VIDEO_VTOTAL_MAX * VRR_MAX_REPEAT_CANDIDATES)) {
            vrr_repeats_per_frame = VRR_MAX_REPEAT_CANDIDATES;
            vrr_steady_v_total = VIDEO_VTOTAL_MAX;
            vrr_trend_nudge_allowed = 0;
        } else if (period_lines > VIDEO_VTOTAL_MAX &&
                   period_lines < (VIDEO_VTOTAL_MIN * 2u)) {
            vrr_repeats_per_frame = 2;
            vrr_steady_v_total = VIDEO_VTOTAL_MIN;
            vrr_trend_nudge_allowed = 0;
        }
    }

    int32_t expected = (int32_t)vrr_steady_v_total;
    if (vrr_trend_nudge_allowed)
        expected += vrr_trend_nudge_lines;
    if (expected < (int32_t)VIDEO_VTOTAL_MIN)
        expected = VIDEO_VTOTAL_MIN;
    if (expected > (int32_t)VIDEO_VTOTAL_MAX)
        expected = VIDEO_VTOTAL_MAX;

    vrr_expected_v_total = (uint32_t)expected;
    vrr_late_v_total = vrr_clamp_v_total(vrr_expected_v_total +
                                         VRR_LATE_STRETCH_LINES);
}

static int vrr_next_interval_expects_frame(void) {
    return (vrr_repeats_per_frame <= 1u) ||
           ((vrr_repeat_phase + 1u) >= vrr_repeats_per_frame);
}

static uint32_t vrr_interval_v_total(void) {
    if (vrr_recovery_fast)
        return VIDEO_VTOTAL_60HZ;
    return vrr_next_interval_expects_frame() ? vrr_late_v_total
                                             : vrr_steady_v_total;
}

static void vrr_note_presented(void) {
    if (vrr_manual_v_total)
        return;
    vrr_repeat_phase = 0;
    vrr_recovery_fast = 0;
    vrr_apply_v_total(vrr_interval_v_total());
}

static uint32_t vrr_v_total_for_ready_time(uint64_t now) {
    uint64_t frame_start = timing_last_vblank_cycles;
    if (frame_start == 0 || now < frame_start)
        frame_start = vrr_last_swap_cycles;
    if (frame_start == 0 || now < frame_start)
        return VIDEO_VTOTAL_60HZ;

    uint64_t elapsed = now - frame_start;
    uint64_t lines = (elapsed * 1024u + VRR_CYCLES_PER_LINE_X1024 - 1)
                   / VRR_CYCLES_PER_LINE_X1024;
    uint32_t target = (uint32_t)lines + VRR_READY_GUARD_LINES;
    return vrr_clamp_v_total(target);
}

static void vrr_note_frame_ready(uint64_t now) {
    if (vrr_last_swap_cycles != 0 && now > vrr_last_swap_cycles) {
        uint64_t delta = now - vrr_last_swap_cycles;
        if (delta > (CPU_FREQ_HZ / 120u) && delta < (CPU_FREQ_HZ / 5u)) {
            if (vrr_ready_period_cycles != 0) {
                uint64_t diff = (delta > vrr_ready_period_cycles)
                              ? (delta - vrr_ready_period_cycles)
                              : (vrr_ready_period_cycles - delta);
                uint32_t nudge = (vrr_cycles_to_lines_nearest(diff) + 1u) / 2u;
                if (nudge > VRR_TREND_NUDGE_MAX_LINES)
                    nudge = VRR_TREND_NUDGE_MAX_LINES;
                vrr_trend_nudge_lines = (delta > vrr_ready_period_cycles)
                                      ? (int32_t)nudge : -(int32_t)nudge;
            } else {
                vrr_trend_nudge_lines = 0;
            }
            vrr_ready_period_cycles = delta;
            vrr_update_cadence_from_period();
        }
    }
    vrr_last_swap_cycles = now;
}

static void vrr_update_on_swap(void) {
    uint64_t now = read_cycles();
    uint32_t v_total = vrr_manual_v_total;

    vrr_note_frame_ready(now);

    if (v_total == 0) {
        uint32_t elapsed_lines = vrr_elapsed_lines_since_vblank(now);

        if (vrr_recovery_fast) {
            v_total = vrr_v_total_for_ready_time(now);
        } else if (elapsed_lines + VRR_READY_GUARD_LINES <= vrr_expected_v_total) {
            v_total = vrr_expected_v_total;
        } else {
            v_total = vrr_v_total_for_ready_time(now);
        }
    }

    vrr_apply_v_total(v_total);
}

static void vrr_update_on_vblank(int presented) {
    if (vrr_manual_v_total) {
        vrr_apply_v_total(vrr_manual_v_total);
        return;
    }

    if (presented) {
        vrr_note_presented();
    } else if (vrr_repeat_phase < 0xFFFFFFFFu) {
        vrr_repeat_phase++;
        if (vrr_repeat_phase >= vrr_repeats_per_frame)
            vrr_recovery_fast = 1;
    }

    vrr_apply_v_total(vrr_interval_v_total());
}

void of_video_set_refresh_vtotal(uint32_t v_total) {
    vrr_manual_v_total = v_total ? vrr_clamp_v_total(v_total) : 0;
    vrr_last_swap_cycles = read_cycles();
    vrr_repeat_phase = 0;
    vrr_recovery_fast = 0;
    vrr_apply_v_total(vrr_manual_v_total ? vrr_manual_v_total
                                         : vrr_interval_v_total());
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
        vrr_note_presented();
    }
    video_irq_restore_local(irq);
}

static void video_wait_pending_swap_for_mode_change(void) {
    uint64_t deadline = read_cycles() + (CPU_FREQ_HZ / 30);
    while (FB_SWAP_CTRL & 1) {
        if (read_cycles() > deadline)
            break;
    }
}

static int video_first_draw_buffer(int display) {
    return display == 0 ? 1 : 0;
}

static void video_reset_buffer_roles(void) {
    uint32_t hw_state = FB_SWAP_CTRL;
    int hw_display = (int)((hw_state >> 1) & 0x3);

    buf_display = ((unsigned)hw_display < 3) ? hw_display : 0;
    buf_draw = video_first_draw_buffer(buf_display);
    buf_ready = -1;
    swap_kicked = 0;
    buf_ready_serial = 0;
    counted_ready_serial = 0;
}

static void sync_swap_state(void);
static int pick_free_buffer(void);

static void video_force_present_clean_buffer(void) {
    sync_swap_state();

    int idx = pick_free_buffer();
    of_cache_clean_range((void *)fb_addr[idx], vid_frame_bytes);
    vrr_update_on_swap();
    FB_SWAP_CTRL = ((uint32_t)idx << 1) | 1u;
    swap_kicked = 1;
    mark_buffer_ready(idx);
    of_video_wait_flip();
    video_reset_buffer_roles();
}

int of_video_set_mode(const of_video_mode_t *mode) {
    of_video_mode_t normalized;
    uint32_t frame_bytes;
    uint32_t old_frame_bytes = vid_frame_bytes;
    if (video_normalize_mode(mode, &normalized, &frame_bytes) < 0)
        return -1;

    video_wait_pending_swap_for_mode_change();

    uint32_t irq = video_irq_save_local();
    vid_mode = normalized;
    vid_frame_bytes = frame_bytes;
    vid_display_mode = DISPLAY_MODE_FRAMEBUFFER;
    video_reset_buffer_roles();
    video_program_app_mode();
    TERM_FB_CTRL = 0;
    video_irq_restore_local(irq);

    video_clear_mode_change_buffers(old_frame_bytes);
    video_force_present_clean_buffer();
    of_term_set_display_mode(DISPLAY_MODE_FRAMEBUFFER);
    /* An app now owns the display.  The next of_analogizer_refresh() applies the
     * persisted Pocket LCD mode (e.g. boot-with-Terminal, or re-asserting
     * Terminal after the app changes its framebuffer mode). */
    video_app_active = 1;
    return 0;
}

void of_video_get_mode(of_video_mode_t *out) {
    if (!out)
        return;
    *out = vid_mode;
}

int of_video_get_mode_count(void) {
    return (int)(sizeof(video_modes) / sizeof(video_modes[0]));
}

int of_video_get_mode_info(int index, of_video_mode_t *out) {
    if (!out || index < 0 || index >= of_video_get_mode_count())
        return -1;

    uint32_t frame_bytes;
    return video_normalize_mode(&video_modes[index], out, &frame_bytes);
}

void of_video_get_caps(of_video_caps_t *out) {
    if (!out)
        return;

    out->max_width = FB_MODE_MAX_WIDTH;
    out->max_height = FB_MODE_MAX_HEIGHT;
    out->max_stride = FB_MODE_MAX_STRIDE;
    const video_scaler_mode_t *scaler =
        video_scaler_mode_for_size(vid_mode.width, vid_mode.height);
    out->physical_width = scaler->width;
    out->physical_height = scaler->height;
    out->default_width = FB_WIDTH;
    out->default_height = FB_HEIGHT;
    out->default_stride = FB_STRIDE;
    out->max_frame_bytes = FB1_BASE - FB0_BASE;
    out->color_mode_mask = (1u << COLOR_MODE_8BIT) |
                           (1u << COLOR_MODE_4BIT) |
                           (1u << COLOR_MODE_2BIT) |
                           (1u << COLOR_MODE_RGB565) |
                           (1u << COLOR_MODE_RGB555) |
                           (1u << COLOR_MODE_RGBA5551);
}

int of_video_check_mode(const of_video_mode_t *mode,
                        of_video_mode_t *normalized) {
    uint32_t frame_bytes;
    return video_normalize_mode(mode, normalized, &frame_bytes);
}

void of_video_set_color_mode(int mode) {
    of_video_mode_t next = vid_mode;
    uint32_t old_frame_bytes = vid_frame_bytes;
    next.color_mode = (uint8_t)mode;
    next.stride = 0;

    of_video_mode_t normalized;
    uint32_t frame_bytes;
    if (video_normalize_mode(&next, &normalized, &frame_bytes) < 0)
        return;

    video_wait_pending_swap_for_mode_change();

    uint32_t irq = video_irq_save_local();
    vid_mode = normalized;
    vid_frame_bytes = frame_bytes;
    video_reset_buffer_roles();
    video_program_visible_mode();
    video_irq_restore_local(irq);

    video_clear_mode_change_buffers(old_frame_bytes);
    video_force_present_clean_buffer();
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
    of_video_mode_t default_mode = {
        FB_WIDTH, FB_HEIGHT, FB_STRIDE, COLOR_MODE_8BIT, 0
    };
    uint32_t frame_bytes;

    video_wait_pending_swap_for_mode_change();

    (void)video_normalize_mode(&default_mode, &vid_mode, &frame_bytes);
    vid_frame_bytes = frame_bytes;
    video_program_app_mode();

    /* Switch scanout to app triple-buffered FB */
    TERM_FB_CTRL = 0;

    video_reset_buffer_roles();
    timing_vblank_count = 0;
    timing_present_count = 0;
    timing_last_presented_idx = 0;
    timing_last_vblank_cycles = 0;
    timing_last_present_cycles = 0;
    vrr_last_swap_cycles = read_cycles();
    vrr_ready_period_cycles = 0;
    vrr_current_v_total = VIDEO_VTOTAL_60HZ;
    vrr_steady_v_total = VIDEO_VTOTAL_60HZ;
    vrr_expected_v_total = VIDEO_VTOTAL_60HZ;
    vrr_late_v_total = vrr_clamp_v_total(VIDEO_VTOTAL_60HZ +
                                         VRR_LATE_STRETCH_LINES);
    vrr_repeats_per_frame = 1;
    vrr_repeat_phase = 0;
    vrr_trend_nudge_lines = 0;
    vrr_recovery_fast = 0;
    vrr_trend_nudge_allowed = 0;
    vrr_manual_v_total = 0;
    VIDEO_VTOTAL = VIDEO_VTOTAL_60HZ;
    vid_display_mode = DISPLAY_MODE_FRAMEBUFFER;
    IRQ_MASK |= IRQ_MASK_VSYNC;

    /* FBs live at the CACHED SDRAM alias (FBn_BASE = 0x10xxxxxx) — these
     * memsets populate L1 D$ dirty lines. Clean ranges after each so
     * whichever buffer HW starts scanning sees zeros in SDRAM, not
     * whatever stale data happens to still be in SDRAM at boot. */
    video_clear_all_buffers();
}

uint8_t *of_video_get_surface(void) {
    return (uint8_t *)fb_addr[buf_draw];
}

void of_video_flush_cache(void) {
    of_cache_clean_range((void *)fb_addr[buf_draw], vid_frame_bytes);
}

uint8_t *of_video_flip(void) {
    /* Refresh our view of hardware state */
    sync_swap_state();

    if ((unsigned)buf_draw >= 3 ||
        buf_draw == buf_display ||
        buf_draw == buf_ready) {
        buf_draw = pick_free_buffer();
    }

    /* Overlay mode: composite terminal text over app draw buffer.
     * App pixels use the dimmed palette (set in overlay_install_palette).
     * Terminal text pixels are remapped to 240-255 (bright VGA colors). */
    if (vid_display_mode == DISPLAY_MODE_OVERLAY) {
        const uint8_t *term = (const uint8_t *)TERM_FB_BASE;
        uint8_t *app = (uint8_t *)fb_addr[buf_draw];
        uint32_t w = vid_mode.width < FB_WIDTH ? vid_mode.width : FB_WIDTH;
        uint32_t h = vid_mode.height < FB_HEIGHT ? vid_mode.height : FB_HEIGHT;
        for (uint32_t y = 0; y < h; y++) {
            const uint8_t *term_row = term + y * FB_STRIDE;
            uint8_t *app_row = app + y * vid_mode.stride;
            for (uint32_t x = 0; x < w; x++) {
                if (term_row[x])
                    app_row[x] = 240 + (term_row[x] & 0x0F);
            }
        }
    }

    /* Flush dirty FB lines to SDRAM so the scanout DMA sees them. */
    of_cache_clean_range((void *)fb_addr[buf_draw], vid_frame_bytes);

    /* Queue draw buffer for display at next vsync.
     * Write format: bits[2:1] = buffer index, bit[0] = trigger */
    vrr_update_on_swap();
    FB_SWAP_CTRL = (buf_draw << 1) | 1;
    /* Kernel-driven kick — fb_swap_pending is high NOW (sysreg write
     * is committed by the time this returns), so sync_swap_state's
     * positive-observation requirement is already satisfied. */
    swap_kicked = 1;

    int old_draw = buf_draw;
    mark_buffer_ready(old_draw);
    buf_draw = pick_free_buffer();
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
    uint32_t present_count_before = timing_present_count;
    timing_vblank_count++;
    timing_last_vblank_cycles = read_cycles();
    sync_swap_state();
    vrr_update_on_vblank(timing_present_count != present_count_before);

    /* Retire a deferred palette upload (see palette_upload_shadow).  The
     * previous commit latched at this vblank, so the engine is normally
     * free here; if not, the next vblank retries.  Skipped in overlay
     * mode (the dimmed overlay palette owns the hardware there) and
     * while the main line is mid-upload. */
    if (pal_dirty && !pal_uploading && vid_display_mode != DISPLAY_MODE_OVERLAY) {
        if (palette_try_upload_shadow())
            pal_dirty = 0;
    }
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

    /* Terminal rendering is always the fixed 320x240 8-bit surface.
     * Framebuffer and overlay modes scan out the active app mode. */
    if (mode == DISPLAY_MODE_TERMINAL)
        video_program_terminal_mode();
    else
        video_program_app_mode();

    /* Switch scanout between terminal FB and app triple-buffered FB. Bit 1
     * (set only for Pocket LCD = Terminal) makes the analog output keep the
     * app FB while the LCD shows the console; otherwise the analog mirrors
     * the LCD selection. */
    TERM_FB_CTRL = (mode == DISPLAY_MODE_TERMINAL)
                       ? (vid_pocket_terminal ? 3u : 1u) : 0u;

    /* Overlay mode: dim app palette, install bright terminal colors */
    if (mode == DISPLAY_MODE_OVERLAY && prev != DISPLAY_MODE_OVERLAY)
        overlay_install_palette();
    else if (mode != DISPLAY_MODE_OVERLAY && prev == DISPLAY_MODE_OVERLAY)
        overlay_restore_palette();

    of_term_set_display_mode(mode);
}

void of_video_apply_pocket_lcd(uint8_t mode) {
    /* mode: 0=On, 1=Off, 2=Terminal. On/Off keep the app on the LCD path (Off
     * is blanked in hardware); Terminal shows the OS console on the LCD while
     * the analog DAC keeps the app. No-op until an app owns the display so the
     * boot/console screen is never overridden, and only reprograms when the
     * target actually differs (cheap to call every frame / idempotent across an
     * app re-setting its mode). */
    if (!video_app_active)
        return;
    int target = (mode == 2u) ? DISPLAY_MODE_TERMINAL : DISPLAY_MODE_FRAMEBUFFER;
    vid_pocket_terminal = (mode == 2u);
    if (target == vid_display_mode) {
        /* Display mode unchanged, but keep TERM_FB_CTRL bit 1 in sync (e.g.
         * the console was already shown when Terminal engaged/disengaged). */
        if (vid_display_mode == DISPLAY_MODE_TERMINAL)
            TERM_FB_CTRL = vid_pocket_terminal ? 3u : 1u;
        return;
    }
    of_video_set_display_mode(target);
}

void of_video_clear(uint8_t color) {
    video_fill_all_buffers(color);
}
