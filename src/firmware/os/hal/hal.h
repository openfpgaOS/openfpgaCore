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
