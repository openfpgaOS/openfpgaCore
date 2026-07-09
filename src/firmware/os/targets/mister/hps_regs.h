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
 *   DS_SLOT_ID     = disk index in the low 4 bits (0 = S0 family/legacy
 *                    image, 1 = S1 instance volume, 2 = S2 borrow volume).
 *                    Pre-multidisk RTL rejects nonzero with ERR_BADREQ, so
 *                    firmware only issues nonzero indices when
 *                    HPS_STATUS_MULTIDISK_CAP is set.
 *   DS_SLOT_OFFSET = byte offset into the image; must be 512-aligned
 *   DS_BRIDGE_ADDR = SDRAM byte offset of the DMA target/source
 *   DS_LENGTH      = byte count; must be a 512 multiple
 *   DS_COMMAND     = DS_CMD_READ / DS_CMD_WRITE
 *   DS_STATUS      = same ACK/DONE/ERR/READY/WR_IDLE/IRQ semantics as Pocket
 *
 * Byte offsets keep Pocket symmetry; they cap each image at 4 GB, which is
 * fine for the .vhd contract (FAT16/32 volumes only).
 */

#ifndef OFOS_MISTER_HPS_REGS_H
#define OFOS_MISTER_HPS_REGS_H

#include "regs.h"

#define HPS_BASE            0x49000000u
#define HPS_STATUS          REG32(HPS_BASE + 0x00)
#define   HPS_STATUS_BOOT_LOADED   (1u << 0)  /* boot.rom ioctl DMA complete */
#define   HPS_STATUS_IMG_MOUNTED   (1u << 1)  /* disk 0 image mounted in OSD */
#define   HPS_STATUS_SECTOR_BUSY   (1u << 2)  /* sector engine mid-transfer */
#define   HPS_STATUS_SECTOR_ERR    (1u << 3)  /* last sector op failed */
#define   HPS_STATUS_IMG_READONLY  (1u << 4)  /* disk 0 mounted read-only */
/* Multi-disk extension (S0/S1/S2 .vhd model).  Pre-multidisk RTL hardwires
 * bits 5..9 to 0, so MULTIDISK_CAP doubles as the capability probe: when it
 * reads 0 the firmware never issues a nonzero DS_SLOT_ID and disks 1/2
 * report "not present". */
#define   HPS_STATUS_MULTIDISK_CAP (1u << 5)  /* RTL supports disk index 0-2 */
#define   HPS_STATUS_IMG1_MOUNTED  (1u << 6)  /* disk 1 (S1 instance) mounted */
#define   HPS_STATUS_IMG2_MOUNTED  (1u << 7)  /* disk 2 (S2 borrow) mounted */
#define   HPS_STATUS_IMG1_READONLY (1u << 8)  /* disk 1 mounted read-only */
#define   HPS_STATUS_IMG2_READONLY (1u << 9)  /* disk 2 mounted read-only */
/* F-load instance model: set once the HPS has DMA'd a menu/MGL-picked .ini
 * into the ini staging window (bridge offset 0x00600000 from CRAM0_BRIDGE,
 * read via the uncached alias — see INI_STAGE_UNCACHED in file.c).  When set,
 * the firmware serves os.ini (slot 2) from staging instead of the vhd. */
#define   HPS_STATUS_INI_LOADED    (1u << 10) /* instance ini staged in SDRAM */
/* F-load app model (Phase 2): set once the HPS has DMA'd a menu/MGL-picked
 * app.elf (ioctl index 2) into the elf staging window (bridge offset
 * OF_TARGET_CRAM0_ELF_STAGE_OFFSET from CRAM0_BRIDGE, read via the uncached
 * alias — see ELF_STAGE_UNCACHED in file.c).  When set, the firmware serves
 * the app ELF (slot 3) from staging instead of the vhd, so an engine update
 * is a single loose-file replacement (pure Downloader). */
#define   HPS_STATUS_ELF_LOADED    (1u << 11) /* instance app.elf staged in SDRAM */
#define HPS_IMG_SIZE_LO     REG32(HPS_BASE + 0x04)  /* disk 0 bytes [31:0] */
#define HPS_IMG_SIZE_HI     REG32(HPS_BASE + 0x08)  /* disk 0 bytes [63:32] */
#define HPS_BOOT_LEN        REG32(HPS_BASE + 0x0C)  /* boot.rom bytes delivered */
#define HPS_IMG1_SIZE_LO    REG32(HPS_BASE + 0x10)  /* disk 1 bytes [31:0] */
#define HPS_IMG1_SIZE_HI    REG32(HPS_BASE + 0x14)  /* disk 1 bytes [63:32] */
#define HPS_IMG2_SIZE_LO    REG32(HPS_BASE + 0x18)  /* disk 2 bytes [31:0] */
#define HPS_IMG2_SIZE_HI    REG32(HPS_BASE + 0x1C)  /* disk 2 bytes [63:32] */
#define HPS_INI_LEN         REG32(HPS_BASE + 0x20)  /* instance-ini (F-load) bytes */
#define HPS_ELF_LEN         REG32(HPS_BASE + 0x24)  /* app.elf (F-load) bytes */

#endif /* OFOS_MISTER_HPS_REGS_H */
