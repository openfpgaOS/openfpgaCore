/*
 * openfpgaOS Analogizer HAL
 * Configuration for analog video output and SNAC controller adapters
 */

#ifndef OFOS_ANALOGIZER_H
#define OFOS_ANALOGIZER_H

#include <stdint.h>
#include "regs.h"

typedef struct {
    uint8_t  enabled;           /* Analogizer present and enabled */
    uint8_t  video_mode;        /* ANLG_VIDEO_* value */
    uint8_t  snac_type;         /* SNAC_* controller type */
    uint8_t  snac_assignment;   /* Player assignment */
    int8_t   h_offset;          /* Horizontal offset (-32 to +31) */
    int8_t   v_offset;          /* Vertical offset (-16 to +15) */
} of_analogizer_state_t;

/* Initialize Analogizer subsystem (reads current bridge state) */
void of_analogizer_init(void);

/* Get current Analogizer state (read from bridge-synced registers) */
const of_analogizer_state_t *of_analogizer_get_state(void);

/* Check if Analogizer is enabled */
int of_analogizer_is_enabled(void);

/* Get the current video output mode */
int of_analogizer_get_video_mode(void);

#endif /* OFOS_ANALOGIZER_H */
