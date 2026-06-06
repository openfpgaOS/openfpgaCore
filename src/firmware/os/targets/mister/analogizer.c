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
 * built-in DAC), invisible to apps.
 *
 * This stub cannot be removed: of_analogizer_* is a HAL contract, not a
 * Pocket driver.  hal/hal.c initializes it and kernel/syscall.c answers
 * the app-facing OF_ANALOGIZER ecalls through it — portable apps call
 * of_analogizer_is_enabled() to gate analog features, and on MiSTer the
 * correct answer is a clean "not present" (state reads disabled, apps
 * also see OF_HW_ANALOGIZER clear in HW_FEATURES).  Contrast snac.c,
 * which is a Pocket peripheral driver with no kernel callers and is
 * simply not built for this target.
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
