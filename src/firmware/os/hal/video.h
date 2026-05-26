/*
 * openfpgaOS Video HAL
 * Dynamic framebuffer geometry, triple-buffered, hardware palette
 */

#ifndef OFOS_VIDEO_H
#define OFOS_VIDEO_H

#include <stdint.h>
#include "regs.h"

#ifndef OF_VIDEO_TIMING_T_DEFINED
#define OF_VIDEO_TIMING_T_DEFINED
typedef struct of_video_timing {
    uint32_t vblank_count;
    uint32_t present_count;
    uint32_t last_presented_idx;
    uint32_t reserved;
    uint64_t last_vblank_us;
    uint64_t last_flip_presented_us;
} of_video_timing_t;
#endif

#ifndef OF_VIDEO_MODE_T_DEFINED
#define OF_VIDEO_MODE_T_DEFINED
typedef struct of_video_mode {
    uint16_t width;
    uint16_t height;
    uint16_t stride;
    uint8_t color_mode;
    uint8_t reserved;
} of_video_mode_t;
#endif

/* Initialize framebuffer subsystem (sets display mode, initial palette) */
void of_video_init(void);

/* Set/query the active app framebuffer geometry. */
int of_video_set_mode(const of_video_mode_t *mode);
void of_video_get_mode(of_video_mode_t *out);
int of_video_get_mode_count(void);
int of_video_get_mode_info(int index, of_video_mode_t *out);

/* Get pointer to current draw buffer (write pixels here) */
uint8_t *of_video_get_surface(void);

/* Request vsync-synchronized buffer swap. Returns new back-buffer pointer. */
uint8_t *of_video_flip(void);

/* Block until pending swap completes */
void of_video_wait_flip(void);

/* GPU-triggered flip path (see docs/cr-gpu-triggered-flip.md).  The
 * caller renders to the buffer at `of_video_buffer_addr(idx)`, then
 * emits CMD_FLIP via `of_gpu_flip_to(idx)` (SDK helper, returns a
 * fence token), then calls of_video_acquire_next(idx, fence_token)
 * to (a) wait for CMD_FLIP to retire + the swap to vsync, (b) update
 * kernel buf_display/buf_ready state, and (c) return the idx of the
 * next free draw slot.  On the very first call (no previous flip),
 * pass just_flipped_idx=-1 and fence_token=0; the kernel reads the
 * initial draw idx out of buf_draw without waiting.
 *
 * Why fence_token: the GPU's CMD_FLIP pulse to fb_swap_pending is one
 * cycle wide; the slave then holds pending=1 until vsync clears it
 * (~16 ms window).  If the CPU's polling loop runs entirely outside
 * that window (because vsync fired between the SDK kick and the
 * kernel call), it'd see pending=0 immediately and can't tell whether
 * the swap completed or hasn't fired yet.  fence_reached is the
 * positive signal: CMD_FLIP retires AT THE SAME CYCLE it pulses
 * gpu_swap_req, so once fence_reached >= token, the slave has
 * definitely seen the pulse, and any subsequent pending=0 means
 * vsync completed the swap.  See cr-gpu-triggered-flip.md for the
 * race analysis. */
int of_video_acquire_next(int just_flipped_idx, uint32_t fence_token);

/* Address of buffer `idx` (0/1/2).  Companion to of_video_acquire_next
 * — apps draw via this addr and pass `idx` to of_gpu_flip_to. */
uint8_t *of_video_buffer_addr(int idx);

/* Swap and wait (convenience: requests swap then blocks) */
void of_video_flip_wait(void);

/* Wait for next vertical blank without flipping buffers.
 * Use for palette animations or effects at 60Hz. */
void of_video_vsync(void);

/* Copy a coherent snapshot of vblank/present timing. */
void of_video_get_timing(of_video_timing_t *out);

/* Force a scanout V_TOTAL, or pass 0 to restore automatic policy. */
void of_video_set_refresh_vtotal(uint32_t v_total);

/* Internal IRQ hook called by the kernel vsync dispatcher. */
void of_video_vsync_irq_service(void);

/* Set a single palette entry (index 0-255, RGB888) */
void of_video_set_palette(uint8_t index, uint8_t r, uint8_t g, uint8_t b);

/* Load an entire 256-entry palette. Each entry is 0x00RRGGBB. */
void of_video_set_palette_bulk(const uint32_t *palette, int count);

/* Load palette from VGA 4-byte format: [B6,G6,R6,pad] per entry.
 * Standard VGA DAC register order, used by BUILD engine, DOS games, etc.
 * Kernel converts 6-bit → 8-bit in one pass. */
void of_video_set_palette_vga4(const uint8_t *vga_pal, int count);

/* Switch between terminal overlay and framebuffer mode */
void of_video_set_display_mode(int mode);

/* Switch color decoder and update active stride/frame size. */
void of_video_set_color_mode(int mode);

/* Flush D-cache to ensure draw buffer is visible to video scanout */
void of_video_flush_cache(void);

/* Clear the draw buffer to a palette index */
void of_video_clear(uint8_t color);

/* Draw a single pixel */
static inline void of_video_pixel(uint8_t *buf, int x, int y, uint8_t color) {
    if ((unsigned)x < FB_WIDTH && (unsigned)y < FB_HEIGHT)
        buf[y * FB_STRIDE + x] = color;
}

#endif /* OFOS_VIDEO_H */
