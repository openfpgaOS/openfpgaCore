//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/* MiSTer shares the Pocket's video HAL byte-for-byte (same MMIO contract,
 * same scanout/mixer RTL) — include the canonical implementation like the
 * sim target does, so fixes propagate instead of silently diverging. */
#include "../pocket/video.c"
