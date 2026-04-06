/*
 * openfpgaOS Analogizer HAL Implementation
 * Reads configuration from bridge-synced registers.
 * Initializes software-driven SNAC if a controller is configured.
 * Handles UART/SNAC pin mux: when SNAC is active, UART is disabled
 * on the shared cart pins (bank0, pin31) to prevent garbage data.
 */

#include "analogizer.h"
#include "snac.h"

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

    /* Initialize SNAC subsystem.
     * If the analogizer is enabled with a SNAC controller type, this
     * switches the cart pins from UART to SNAC mode.  The UART RX
     * sees idle-high (gated in RTL) so no garbage enters the FIFO.
     * If no SNAC is configured (type=0 or analogizer disabled),
     * snac_init(SNAC_NONE) keeps UART mode active. */
    if (anlg_state.enabled && anlg_state.snac_type != SNAC_NONE) {
        snac_init(anlg_state.snac_type);
    } else {
        snac_init(SNAC_NONE);
    }
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
