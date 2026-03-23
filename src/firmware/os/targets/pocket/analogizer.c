/*
 * openfpgaOS Analogizer HAL Implementation
 * Reads configuration from bridge-synced registers
 */

#include "analogizer.h"

static of_analogizer_state_t anlg_state;

void of_analogizer_init(void) {
    /* Read current bridge settings.
     * The Analogizer configuration is set by the Pocket menu (interact.json)
     * and synced to the FPGA via bridge registers at 0xF7000000.
     * The game_id register at 0x40000068 carries the combined settings. */
    uint32_t settings = SYS_GAME_ID;

    anlg_state.snac_type      = settings & 0x1F;
    anlg_state.snac_assignment = (settings >> 6) & 0xF;
    anlg_state.video_mode     = (settings >> 10) & 0xF;
    anlg_state.enabled        = (settings >> 15) & 1;
    anlg_state.h_offset       = 0;
    anlg_state.v_offset       = 0;
}

const of_analogizer_state_t *of_analogizer_get_state(void) {
    return &anlg_state;
}

int of_analogizer_is_enabled(void) {
    return anlg_state.enabled;
}

int of_analogizer_get_video_mode(void) {
    return anlg_state.video_mode;
}
