/*
 * openfpgaOS Tile & Sprite HAL Implementation
 */

#include "tile.h"

void of_tile_init(void) {
    /* No initialization required */
}

/* ======================================================================
 * Tile Layer
 * ====================================================================== */

void of_tile_enable(int enable, int priority) {
    TILE_CTRL = (enable ? TILE_CTRL_ENABLE : 0) |
                (priority ? TILE_CTRL_PRIORITY : 0);
}

void of_tile_scroll(int x, int y) {
    TILE_SCROLL = ((y & 0xFF) << 16) | (x & 0x1FF);
}

void of_tile_set(int col, int row, uint16_t entry) {
    TILE_MAP_ADDR = (row & 31) * TILE_MAP_COLS + (col & 63);
    TILE_MAP_DATA = entry;
}

void of_tile_load_map(const uint16_t *data, int x, int y, int w, int h) {
    for (int row = 0; row < h; row++) {
        TILE_MAP_ADDR = ((y + row) & 31) * TILE_MAP_COLS + (x & 63);
        for (int col = 0; col < w; col++) {
            TILE_MAP_DATA = *data++;
        }
    }
}

void of_tile_load_chr(int first_tile, const void *data, int num_tiles) {
    TILE_CHR_ADDR = first_tile * 8;
    const uint32_t *p = (const uint32_t *)data;
    int count = num_tiles * 8;  /* 8 words (rows) per tile */
    for (int i = 0; i < count; i++) {
        TILE_CHR_DATA = *p++;
    }
}

/* ======================================================================
 * Sprite Engine
 * ====================================================================== */

void of_sprite_enable(int enable) {
    SPR_CTRL = enable ? SPR_CTRL_ENABLE : 0;
}

void of_sprite_set(int index, int x, int y, int tile_id, int palette,
                   int hflip, int vflip, int enable) {
    SPR_SAT_IDX = index & 63;
    SPR_SAT_XY = ((y & 0xFF) << 16) | (x & 0x1FF);
    SPR_SAT_ATTR = (tile_id & 0xFF) |
                   ((palette & 0xF) << 8) |
                   (hflip ? (1 << 13) : 0) |
                   (vflip ? (1 << 14) : 0) |
                   (enable ? (1 << 15) : 0);
    /* SAT_ATTR write auto-increments sat_idx */
}

void of_sprite_move(int index, int x, int y) {
    SPR_SAT_IDX = index & 63;
    SPR_SAT_XY = ((y & 0xFF) << 16) | (x & 0x1FF);
}

void of_sprite_load_chr(int first_tile, const void *data, int num_tiles) {
    SPR_CHR_ADDR = first_tile * 8;
    const uint32_t *p = (const uint32_t *)data;
    int count = num_tiles * 8;
    for (int i = 0; i < count; i++) {
        SPR_CHR_DATA = *p++;
    }
}

void of_sprite_hide(int index) {
    SPR_SAT_IDX = index & 63;
    SPR_SAT_ATTR = 0;  /* enable=0 */
}

void of_sprite_hide_all(void) {
    for (int i = 0; i < SPRITE_MAX; i++) {
        SPR_SAT_IDX = i;
        SPR_SAT_ATTR = 0;
    }
}
