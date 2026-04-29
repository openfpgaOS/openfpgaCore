/*
 * openfpgaOS Video HAL
 * 320x240 8-bit indexed color, triple-buffered, hardware palette
 */

#ifndef OFOS_VIDEO_H
#define OFOS_VIDEO_H

#include <stdint.h>
#include "regs.h"

/* Initialize framebuffer subsystem (sets display mode, initial palette) */
void of_video_init(void);

/* Get pointer to current draw buffer (write pixels here) */
uint8_t *of_video_get_surface(void);

/* Request vsync-synchronized buffer swap. Returns new back-buffer pointer. */
uint8_t *of_video_flip(void);

/* Block until pending swap completes */
void of_video_wait_flip(void);

/* GPU-triggered flip path (see docs/cr-gpu-triggered-flip.md).  The
 * caller renders to the buffer at `of_video_buffer_addr(idx)`, then
 * emits CMD_FLIP via `of_gpu_flip_to(idx, fence_token)` (SDK helper),
 * then calls of_video_acquire_next(idx) to (a) mark that idx as
 * pending-swap and (b) return the idx of the next free draw slot.
 * On the very first call, pass `just_flipped_idx = -1` and the kernel
 * returns the initial draw idx (typically 1) without any handoff.
 *
 * Blocks if the previous flip hasn't yet completed (3-buffer ceiling
 * hit — at most one previous flip can be pending at a time).  Vs.
 * of_video_flip() this is ~10 µs (just the book-keeping syscall),
 * not ~79 µs, since the actual swap is queued asynchronously by the
 * GPU's command processor when CMD_FLIP reaches the head. */
int of_video_acquire_next(int just_flipped_idx);

/* Address of buffer `idx` (0/1/2).  Companion to of_video_acquire_next
 * — apps draw via this addr and pass `idx` to of_gpu_flip_to. */
uint8_t *of_video_buffer_addr(int idx);

/* Swap and wait (convenience: requests swap then blocks) */
void of_video_flip_wait(void);

/* Wait for next vertical blank without flipping buffers.
 * Use for palette animations or effects at 60Hz. */
void of_video_vsync(void);

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
