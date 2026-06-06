//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * openfpgaOS Analogizer HAL — MiSTer stub
 *
 * There is no Analogizer on MiSTer: analog video output is a framework
 * feature (direct video / VGA via the IO board or the SuperStation One's
 * built-in DAC), invisible to apps.  The state object always reads
 * disabled; apps gate on of_has_feature(OF_HW_ANALOGIZER), which the
 * MiSTer RTL HW_FEATURES register leaves clear.
 */

#include "analogizer.h"

static const of_analogizer_state_t state; /* zeroed: disabled, no SNAC */

void of_analogizer_init(void) {}
void of_analogizer_refresh(void) {}

const of_analogizer_state_t *of_analogizer_get_state(void) {
    return &state;
}

int of_analogizer_is_enabled(void) {
    return 0;
}
