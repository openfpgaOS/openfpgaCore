/*
 * openfpgaOS Tile & Sprite HAL
 * Hardware tile layer (64x32 tilemap, 256 tiles, 8x8 4bpp)
 * Hardware sprite engine (64 sprites, 8x8 4bpp)
 */

#ifndef OFOS_TILE_H
#define OFOS_TILE_H

#include <stdint.h>
#include "regs.h"

/* ======================================================================
 * Tile Layer
 * ====================================================================== */

/* Initialize tile/sprite subsystem */
void of_tile_init(void);

/* Enable/disable tile layer. priority: 0=behind FB, 1=over FB */
void of_tile_enable(int enable, int priority);

/* Set tile scroll position */
void of_tile_scroll(int x, int y);

/* Set a single tilemap entry at (col, row) */
void of_tile_set(int col, int row, uint16_t entry);

/* Load a rectangular region of tilemap data */
void of_tile_load_map(const uint16_t *data, int x, int y, int w, int h);

/* Load tile character data (4bpp, 32 bytes per tile) */
void of_tile_load_chr(int first_tile, const void *data, int num_tiles);

/* Build a tilemap entry value */
static inline uint16_t of_tile_entry(int tile_id, int palette,
                                      int hflip, int vflip) {
    return (uint16_t)(
        (tile_id & 0xFF) |
        ((palette & 0xF) << 10) |
        (hflip ? (1 << 14) : 0) |
        (vflip ? (1 << 15) : 0)
    );
}

/* ======================================================================
 * Sprite Engine
 * ====================================================================== */

/* Enable/disable sprite engine */
void of_sprite_enable(int enable);

/* Set sprite position and attributes */
void of_sprite_set(int index, int x, int y, int tile_id, int palette,
                   int hflip, int vflip, int enable);

/* Set sprite position only (faster than full set) */
void of_sprite_move(int index, int x, int y);

/* Load sprite character data (same format as tile chr) */
void of_sprite_load_chr(int first_tile, const void *data, int num_tiles);

/* Hide a sprite */
void of_sprite_hide(int index);

/* Hide all sprites */
void of_sprite_hide_all(void);

#endif /* OFOS_TILE_H */
