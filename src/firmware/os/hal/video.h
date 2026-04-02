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

/* Request vsync-synchronized buffer swap */
void of_video_flip(void);

/* Block until pending swap completes */
void of_video_wait_flip(void);

/* Swap and wait (convenience: requests swap then blocks) */
void of_video_flip_wait(void);

/* Set a single palette entry (index 0-255, RGB888) */
void of_video_set_palette(uint8_t index, uint8_t r, uint8_t g, uint8_t b);

/* Load an entire 256-entry palette. Each entry is 0x00RRGGBB. */
void of_video_set_palette_bulk(const uint32_t *palette, int count);

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
