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

/* Initialize all HAL subsystems.  of_init() = of_init_early() +
 * of_init_late(); os_main() calls the halves separately so the
 * core/os contract handshake and boot memtest run between them,
 * before the full register surface is touched (see kernel/main.c). */
void of_init(void);
void of_init_early(void);  /* clocks, cache, timer, video, terminal */
void of_init_late(void);   /* input, disk, file, mixer, save, analogizer, link */

#endif /* OFOS_HAL_H */
