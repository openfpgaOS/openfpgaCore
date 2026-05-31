/*
 * openfpgaOS Analogizer HAL
 * Configuration for analog video output and SNAC controller adapters
 */

#ifndef OFOS_ANALOGIZER_H
#define OFOS_ANALOGIZER_H

#include <stdint.h>
#include "regs.h"

typedef struct {
    uint8_t  enabled;           /* analog video output enabled */
    uint8_t  video_mode;        /* ANLG_VIDEO_* value */
    uint8_t  snac_type;         /* SNAC_* controller type; independent of video */
    uint8_t  snac_assignment;   /* Player assignment */
    int8_t   h_offset;          /* Horizontal offset (-32 to +31) */
    int8_t   v_offset;          /* Vertical offset (-16 to +15) */
} of_analogizer_state_t;

/* Initialize Analogizer subsystem (reads current bridge state) */
void of_analogizer_init(void);

/* Refresh from bridge/interact registers.  This picks up Pocket menu
 * changes made after boot, including SNAC enable/type/assignment changes. */
void of_analogizer_refresh(void);

/* Load and apply analogizer.cfg after filesystem_init().
 *
 * Text configs use key=value lines. Supported keys are enabled, video,
 * snac, assignment, h_offset, and v_offset. Lines starting with # or ;
 * are comments, and inline #/; comments are ignored. The legacy binary
 * ACFG format is still accepted for compatibility.
 *
 * Returns 0 when a valid config was applied, negative when the file is
 * absent or invalid. The boot default from interact.json remains active
 * on failure. */
int of_analogizer_load_config(const char *filename);

/* Get current Analogizer state (read from bridge-synced registers) */
const of_analogizer_state_t *of_analogizer_get_state(void);

/* Check if analog video output is enabled */
int of_analogizer_is_enabled(void);

#endif /* OFOS_ANALOGIZER_H */
