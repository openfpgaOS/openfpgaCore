//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * openfpgaOS Analogizer HAL Implementation
 *
 * Boot starts from the Pocket interact.json values, mirrored into CPU
 * sysregs by the FPGA. The kernel exposes a refresh API so apps can
 * pick up Pocket-menu changes after boot.
 */

#include "analogizer.h"
#include "save.h"
#include "snac.h"
#include "syscall.h"
#include "video.h"   /* of_video_set_display_mode for the Pocket LCD mode */
#include <stddef.h>
#include <stdint.h>
#include <string.h>

static of_analogizer_state_t anlg_state;

static int clamp_int(int v, int lo, int hi) {
    return v < lo ? lo : (v > hi ? hi : v);
}


static int snac_type_supported(uint8_t type) {
    switch (type) {
    case SNAC_NONE:
    case SNAC_DB15:
    case SNAC_NES:
    case SNAC_SNES:
    case SNAC_PCE_2BTN:
    case SNAC_PCE_6BTN:
    case SNAC_PCE_MULTITAP:
    case SNAC_DB15_FAST:
    case SNAC_SNES_SWAP:
    case SNAC_PSX:
    case SNAC_PSX_FAST:
        return 1;
    default:
        return 0;
    }
}


static void analogizer_clamp_state(of_analogizer_state_t *s) {
    s->enabled = s->enabled ? 1 : 0;
    s->video_mode = (uint8_t)clamp_int(s->video_mode, 0, 15);
    s->video_mode &= (uint8_t)~ANLG_VIDEO_POCKET_OFF;
    s->snac_type = (uint8_t)clamp_int(s->snac_type, 0, 31);
    if (!snac_type_supported(s->snac_type))
        s->snac_type = SNAC_NONE;
    s->snac_assignment = (uint8_t)clamp_int(s->snac_assignment, 0, 15);
    s->h_offset = (int8_t)clamp_int(s->h_offset, -32, 31);
    s->v_offset = (int8_t)clamp_int(s->v_offset, -16, 15);
}

static uint32_t analogizer_pack_settings(const of_analogizer_state_t *s) {
    return ((uint32_t)s->snac_type & 0x1Fu) |
           (((uint32_t)s->snac_assignment & 0x0Fu) << 6) |
           (((uint32_t)s->video_mode & 0x0Fu) << 10) |
           (s->enabled ? (1u << 15) : 0u);
}

/* Settings bits owned by OTHER layers than the analogizer state machine:
 * bits 17:16 = "Pocket LCD" interact variable (On/Off/Terminal), owned by
 * the video layer; bits 19:18 = "Analogizer 15kHz Timing" interact variable
 * (ANLG_TIMING_*: 240p/480i/576i), consumed directly by the FPGA's analog
 * raster with no firmware action (reserved encoding 3 falls back to 240p in
 * hardware).  analogizer_pack_settings() never sets these bits, so every
 * write-back must OR back the current hardware value or the per-frame
 * normalize in of_analogizer_refresh() would reset the user's choice. */
/* Widened 19:16 -> 23:16 (2026-07-30): bits 22:20 carry the "Mouse
 * Speed" interact variable (read by apps straight off the settings
 * sysreg); 23 is spare.  analogizer_pack_settings() never produces
 * bits above 15, so preserving the whole byte is free. */
#define ANLG_PRESERVED_SETTINGS_MASK 0x00FF0000u

static void analogizer_write_settings(uint32_t packed) {
    ANALOGIZER_SETTINGS = (packed & ~ANLG_PRESERVED_SETTINGS_MASK) |
                          (ANALOGIZER_SETTINGS & ANLG_PRESERVED_SETTINGS_MASK);
}

static void analogizer_init_snac(void) {
    if (anlg_state.snac_type != SNAC_NONE)
        snac_init(anlg_state.snac_type);
    else
        snac_init(SNAC_NONE);
}

static void analogizer_read_hardware_defaults(void) {
    uint32_t settings = ANALOGIZER_SETTINGS;

    anlg_state.snac_type = settings & 0x1Fu;
    anlg_state.snac_assignment = (settings >> 6) & 0x0Fu;
    anlg_state.video_mode = (settings >> 10) & 0x0Fu;
    anlg_state.enabled = (settings >> 15) & 1u;
    anlg_state.h_offset = (int8_t)ANALOGIZER_H_OFFSET;
    anlg_state.v_offset = (int8_t)ANALOGIZER_V_OFFSET;
    analogizer_clamp_state(&anlg_state);
    analogizer_write_settings(analogizer_pack_settings(&anlg_state));
}

static void analogizer_decode_hardware_state(of_analogizer_state_t *out) {
    uint32_t settings = ANALOGIZER_SETTINGS;

    out->snac_type = settings & 0x1Fu;
    out->snac_assignment = (settings >> 6) & 0x0Fu;
    out->video_mode = (settings >> 10) & 0x0Fu;
    out->enabled = (settings >> 15) & 1u;
    out->h_offset = (int8_t)ANALOGIZER_H_OFFSET;
    out->v_offset = (int8_t)ANALOGIZER_V_OFFSET;
    analogizer_clamp_state(out);
}

static int analogizer_state_changed(const of_analogizer_state_t *a,
                                    const of_analogizer_state_t *b) {
    return a->enabled != b->enabled ||
           a->video_mode != b->video_mode ||
           a->snac_type != b->snac_type ||
           a->snac_assignment != b->snac_assignment ||
           a->h_offset != b->h_offset ||
           a->v_offset != b->v_offset;
}


void of_analogizer_init(void) {
    analogizer_read_hardware_defaults();
    analogizer_init_snac();
}

void of_analogizer_refresh(void) {
    of_analogizer_state_t hw;
    analogizer_decode_hardware_state(&hw);
    uint32_t cur_settings = ANALOGIZER_SETTINGS;
    /* Normalize the analogizer fields, but keep the Pocket LCD mode (bits
     * 17:16, video layer) and the 15 kHz timing (bits 19:18, FPGA raster)
     * so this per-frame write never resets them. */
    uint32_t normalized_settings = analogizer_pack_settings(&hw) |
                                   (cur_settings & ANLG_PRESERVED_SETTINGS_MASK);
    if (cur_settings != normalized_settings)
        ANALOGIZER_SETTINGS = normalized_settings;

    /* Apply the Pocket LCD mode (bits 17:16). The video layer no-ops until an
     * app owns the display and only reprograms on an actual change, so this is
     * safe to call every frame and applies a persisted Terminal once the app
     * starts. */
    of_video_apply_pocket_lcd((uint8_t)((cur_settings >> 16) & 0x3u));

    if (!analogizer_state_changed(&hw, &anlg_state))
        return;

    uint8_t old_snac_type = anlg_state.snac_type;
    anlg_state = hw;

    if (anlg_state.snac_type != old_snac_type)
        analogizer_init_snac();
}


const of_analogizer_state_t *of_analogizer_get_state(void) {
    of_analogizer_refresh();
    return &anlg_state;
}

int of_analogizer_is_enabled(void) {
    of_analogizer_refresh();
    return anlg_state.enabled;
}
