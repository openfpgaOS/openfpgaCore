//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * openfpgaOS Hardware Abstraction Layer - Master Include
 */

#ifndef OFOS_HAL_H
#define OFOS_HAL_H

#include "regs.h"
#include "video.h"
#include "audio.h"
#include "input.h"
#include "save.h"
#include "file.h"
#include "disk.h"
#include "analogizer.h"
#include "terminal.h"
#include "timer.h"
#include "cache.h"
#include "link.h"
#include "net.h"
#include "mixer.h"
#include "codec.h"
#include "lzw.h"

/* Initialize all HAL subsystems */
void of_init(void);

#endif /* OFOS_HAL_H */
