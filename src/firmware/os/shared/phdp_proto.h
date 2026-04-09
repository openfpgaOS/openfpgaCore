/*
 * shared/phdp_proto.h — PHDP wire constants
 *
 * Single source of truth for the on-the-wire PHDP protocol used by:
 *   - boot ROM   (boot/boot.c)         — loads os.bin via UART
 *   - kernel     (targets/pocket/disk_phdp.c) — loads app slots via UART
 *   - host tool  (ofos_mem_sdk/src/tools/phdp/phdp_proto.h)
 *
 * The Pocket-side codec functions live in phdp_codec.inc.c — that file
 * is text-included by both consumers so each TU can stamp the funcs
 * with its own section attribute (BRAM for boot, SDRAM for kernel).
 *
 * Versioning: this header MUST stay byte-compatible with the host
 * tool's phdp_proto.h. Add fields by bumping PHDP_VERSION; never
 * silently change opcode values.
 */

#ifndef OFOS_SHARED_PHDP_PROTO_H
#define OFOS_SHARED_PHDP_PROTO_H

#include <stdint.h>

#define PHDP_STX                0x02
#define PHDP_HEADER_SIZE        7
#define PHDP_CRC_SIZE           2
/* 256 bytes — kept small because the bootloader's PHDP buffer lives
 * in 8 KB BRAM. The host (phdpd) reads our advertised max from
 * EVT_BOOT_ALIVE and uses min(its-own-PHDP_MAX_CHUNK, ours) per
 * stream, so this is the authoritative limit for Pocket-side
 * buffers regardless of what the host was compiled with. */
#define PHDP_MAX_CHUNK          256

/* Opcodes */
#define PHDP_EVT_BOOT_ALIVE     0x01
#define PHDP_CMD_CLIENT_READY   0x02
#define PHDP_REQ_OVERRIDE       0x10
#define PHDP_RES_STREAM         0x11
#define PHDP_RES_USE_SD         0x12
#define PHDP_DATA_CHUNK         0x20
#define PHDP_REPORT_PROGRESS    0x21
#define PHDP_CMD_NAK_RETRY      0x22
#define PHDP_EVT_EXEC_START     0x31

/* Identification fields used in EVT_BOOT_ALIVE payload */
#define PHDP_CORE_ID            0x4F464F53  /* "OFOS" */
#define PHDP_VERSION            0x0100

/* Total wire size of the largest possible packet — used to size
 * caller-owned packet buffers. Both boot.c and disk_phdp.c allocate
 * one of these. */
#define PHDP_BUF_SIZE           (PHDP_HEADER_SIZE + PHDP_MAX_CHUNK + PHDP_CRC_SIZE)

#endif /* OFOS_SHARED_PHDP_PROTO_H */
