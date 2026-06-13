//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * openfpgaOS Analogizer HAL
 * Configuration for analog video output and SNAC controller adapters
 */

#ifndef OFOS_ANALOGIZER_H
#define OFOS_ANALOGIZER_H

#include <stdint.h>
#include "regs.h"

/* NOTE: this struct is memcpy'd verbatim to apps via the
 * OF_ANALOGIZER_FID_GET_STATE syscall and mirrored in api/of_analogizer.h —
 * layout changes break the app ABI.  The 15 kHz timing field (settings bits
 * 19:18, ANLG_TIMING_*) is deliberately NOT part of this state: the FPGA
 * raster consumes those bits directly and the firmware only preserves them
 * (see ANLG_PRESERVED_SETTINGS_MASK in targets/pocket/analogizer.c). */
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

/* Get current Analogizer state (read from bridge-synced registers) */
const of_analogizer_state_t *of_analogizer_get_state(void);

/* Check if analog video output is enabled */
int of_analogizer_is_enabled(void);

#endif /* OFOS_ANALOGIZER_H */
