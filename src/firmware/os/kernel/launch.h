//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * openfpgaOS in-OS application relaunch (Architecture A).
 *
 * A launcher app (menu.elf) asks the kernel to swap to a different app without
 * resetting the FPGA: the kernel tears down the outgoing app's HAL/kernel
 * state, re-points the file system at the selected instance, re-reads its
 * os.ini, and execs the chosen ELF.  Implemented in kernel/main.c and gated on
 * OF_TARGET_SUPPORTS_RELAUNCH (MiSTer); the syscall returns OF_ERR_NOT_SUPPORTED
 * on targets without it.
 */

#ifndef OFOS_LAUNCH_H
#define OFOS_LAUNCH_H

#include "../hal/regs.h"   /* OF_TARGET_SUPPORTS_RELAUNCH */

#ifdef OF_TARGET_SUPPORTS_RELAUNCH

/* Capture the relaunch target (copies the strings out of the caller's memory
 * before any teardown), then perform it.  os_relaunch() does not return on
 * success. instance: "/games/Quake" or NULL/"" for the image root. elf:
 * "quake.elf" / "slot:3" / NULL to take [os] ELF from the instance os.ini. */
void os_request_relaunch(const char *instance, const char *elf);
void os_relaunch(void) __attribute__((noreturn));

/* Register / query the launcher to re-enter when an app exit()s. */
void os_set_menu(const char *instance, const char *elf);
int  os_menu_pending(void);
void os_request_menu_relaunch(void);

#endif /* OF_TARGET_SUPPORTS_RELAUNCH */

#endif /* OFOS_LAUNCH_H */
