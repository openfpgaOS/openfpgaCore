//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * MiSTer HPS bridge MMIO (REGION_HPS, 0x49000000)
 *
 * Status block exposed by hps_bridge.v through axi_periph_slave.  The data
 * path (boot.rom ioctl DMA + disk-image sector engine) reuses the existing
 * data-slot registers (DS_* in hal/regs.h):
 *
 *   DS_SLOT_ID     = 0  (raw disk device — the OSD-mounted .vhd)
 *   DS_SLOT_OFFSET = byte offset into the image; must be 512-aligned
 *   DS_BRIDGE_ADDR = SDRAM byte offset of the DMA target/source
 *   DS_LENGTH      = byte count; must be a 512 multiple
 *   DS_COMMAND     = DS_CMD_READ / DS_CMD_WRITE
 *   DS_STATUS      = same ACK/DONE/ERR/READY/WR_IDLE/IRQ semantics as Pocket
 *
 * Byte offsets keep Pocket symmetry; they cap the image at 4 GB, which is
 * fine for the v1 .vhd contract.
 */

#ifndef OFOS_MISTER_HPS_REGS_H
#define OFOS_MISTER_HPS_REGS_H

#include "regs.h"

#define HPS_BASE            0x49000000u
#define HPS_STATUS          REG32(HPS_BASE + 0x00)
#define   HPS_STATUS_BOOT_LOADED   (1u << 0)  /* boot.rom ioctl DMA complete */
#define   HPS_STATUS_IMG_MOUNTED   (1u << 1)  /* disk image mounted in OSD */
#define   HPS_STATUS_SECTOR_BUSY   (1u << 2)  /* sector engine mid-transfer */
#define   HPS_STATUS_SECTOR_ERR    (1u << 3)  /* last sector op failed */
#define   HPS_STATUS_IMG_READONLY  (1u << 4)  /* image mounted read-only */
#define HPS_IMG_SIZE_LO     REG32(HPS_BASE + 0x04)  /* mounted image bytes [31:0] */
#define HPS_IMG_SIZE_HI     REG32(HPS_BASE + 0x08)  /* mounted image bytes [63:32] */
#define HPS_BOOT_LEN        REG32(HPS_BASE + 0x0C)  /* boot.rom bytes delivered */

#endif /* OFOS_MISTER_HPS_REGS_H */
