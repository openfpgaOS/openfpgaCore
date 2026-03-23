/*
 * openfpgaOS Video HAL Implementation
 */

#include "video.h"
#include "regs.h"
#include "cache.h"
#include "../os_string.h"

/* Track which buffer is currently the draw target */
static int draw_buffer_index = 1;  /* Start drawing to buffer 1, displaying buffer 0 */

void of_video_init(void) {
    /* Set framebuffer display mode */
    SYS_DISPLAY_MODE = DISPLAY_MODE_FRAMEBUFFER;

    /* Set initial buffer assignments */
    draw_buffer_index = 1;

    /* Clear both framebuffers */
    memset((void *)FB0_BASE, 0, FB_SIZE);
    memset((void *)FB1_BASE, 0, FB_SIZE);
}

uint8_t *of_video_get_surface(void) {
    return (uint8_t *)(draw_buffer_index ? FB1_BASE : FB0_BASE);
}

void of_video_flush_cache(void) {
    /* Clean the draw buffer so scanout sees latest pixels */
    uint8_t *fb = (uint8_t *)(draw_buffer_index ? FB1_BASE : FB0_BASE);
    of_cache_clean_range(fb, 320 * 240 * 2);  /* 16-bit mode worst case */
}

void of_video_flip(void) {
    /* Clean D-cache for draw buffer so scanout sees latest pixels */
    uint8_t *fb = (uint8_t *)(draw_buffer_index ? FB1_BASE : FB0_BASE);
    of_cache_clean_range(fb, 320 * 240 * 2);  /* 16-bit mode worst case */

    /* Request swap at next vsync */
    FB_SWAP_CTRL = 1;

    /* Wait for vsync to actually perform the swap before toggling.
     * Without this, the game can start drawing to what the FPGA
     * is still displaying, causing flicker on fast frames. */
    while (FB_SWAP_CTRL & 1) {}

    /* Now the FPGA has swapped — safe to toggle draw target */
    draw_buffer_index ^= 1;
}

void of_video_wait_flip(void) {
    /* Poll until swap completes (bit 0 clears) */
    while (FB_SWAP_CTRL & 1) {}
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
