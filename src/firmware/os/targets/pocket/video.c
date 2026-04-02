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
#include "../os_string.h"

static const uint32_t fb_addr[3] = { FB0_BASE, FB1_BASE, FB2_BASE };

/* Software-tracked buffer roles */
static int buf_display = 0;   /* buffer the hardware is scanning out        */
static int buf_draw    = 1;   /* buffer the CPU is drawing into             */
static int buf_ready   = -1;  /* buffer queued for next vsync (-1 = none)   */

/* Check whether a pending swap has completed and update software state. */
static void sync_swap_state(void) {
    if (buf_ready >= 0 && !(FB_SWAP_CTRL & 1)) {
        buf_display = buf_ready;
        buf_ready = -1;
    }
}

void of_video_init(void) {
    SYS_DISPLAY_MODE = DISPLAY_MODE_FRAMEBUFFER;

    buf_display = 0;
    buf_draw    = 1;
    buf_ready   = -1;

    memset((void *)FB0_BASE, 0, FB_SIZE);
    memset((void *)FB1_BASE, 0, FB_SIZE);
    memset((void *)FB2_BASE, 0, FB_SIZE);
}

uint8_t *of_video_get_surface(void) {
    return (uint8_t *)fb_addr[buf_draw];
}

void of_video_flush_cache(void) {
    of_cache_clean_range((uint8_t *)fb_addr[buf_draw], 320 * 240 * 2);
}

void of_video_flip(void) {
    /* Refresh our view of hardware state */
    sync_swap_state();

    /* Flush draw buffer so scanout sees the pixels */
    of_cache_clean_range((uint8_t *)fb_addr[buf_draw], 320 * 240 * 2);

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
}

void of_video_wait_flip(void) {
    while (FB_SWAP_CTRL & 1) {}
    sync_swap_state();
}

void of_video_flip_wait(void) {
    of_video_flip();
    of_video_wait_flip();
}

void of_video_set_palette(uint8_t index, uint8_t r, uint8_t g, uint8_t b) {
    PAL_INDEX = index;
    PAL_WRITE = ((uint32_t)r << 16) | ((uint32_t)g << 8) | b;
}

void of_video_set_palette_bulk(const uint32_t *palette, int count) {
    PAL_INDEX = 0;
    for (int i = 0; i < count && i < 256; i++)
        PAL_WRITE = palette[i];
}

void of_video_set_display_mode(int mode) {
    SYS_DISPLAY_MODE = mode;
}

void of_video_clear(uint8_t color) {
    memset(of_video_get_surface(), color, FB_SIZE);
}
