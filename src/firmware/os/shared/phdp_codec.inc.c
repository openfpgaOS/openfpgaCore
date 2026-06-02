//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * shared/phdp_codec.inc.c — PHDP encode/decode (Pocket side)
 *
 * Text-included by both callers (boot/boot.c and kernel
 * targets/pocket/disk_phdp.c). The includer is responsible for:
 *
 *   - Defining SHARED_ATTR before include so each function lands in
 *     the right runtime memory region (.text.boot vs .text).
 *   - Defining PHDP_BUF (an lvalue array of at least PHDP_BUF_SIZE
 *     bytes) and PHDP_SEQ (an lvalue uint8_t) before include. The
 *     codec uses these as globals — passing them as function
 *     parameters instead added enough per-call overhead to push the
 *     boot ROM over its 8 KB BRAM budget, so we let the includer
 *     name its own storage and the codec references it directly.
 *   - Including shared/uart_poll.inc.c first (this file calls
 *     uart_putc and uart_getc).
 *   - Having SYS_CYCLE_LO available (from regs.h) for phdp_recv's
 *     timeout loop.
 *
 * Wire format (matches host phdp_proto.h):
 *   [STX][seq][cmd][len:4 LE][payload...][crc16 LE]
 *   crc16: CCITT, init 0xFFFF, poly 0x1021, computed over [STX..payload].
 */

#ifndef SHARED_ATTR
#  define SHARED_ATTR
#endif

#if !defined(PHDP_BUF) || !defined(PHDP_SEQ)
#  error "phdp_codec.inc.c: define PHDP_BUF and PHDP_SEQ before include"
#endif

SHARED_ATTR
static uint16_t phdp_crc16(const uint8_t *data, uint32_t len) {
    uint16_t crc = 0xFFFF;
    for (uint32_t i = 0; i < len; i++) {
        crc ^= (uint16_t)data[i] << 8;
        for (int j = 0; j < 8; j++)
            crc = (crc & 0x8000) ? (crc << 1) ^ 0x1021 : crc << 1;
    }
    return crc;
}

/* Encode + transmit one PHDP packet using the includer's buffer. */
SHARED_ATTR
static void phdp_send(uint8_t cmd, const void *payload, uint32_t len) {
    if (len > PHDP_MAX_CHUNK) return;

    PHDP_BUF[0] = PHDP_STX;
    PHDP_BUF[1] = PHDP_SEQ++;
    PHDP_BUF[2] = cmd;
    PHDP_BUF[3] = (len >>  0) & 0xFF;
    PHDP_BUF[4] = (len >>  8) & 0xFF;
    PHDP_BUF[5] = (len >> 16) & 0xFF;
    PHDP_BUF[6] = (len >> 24) & 0xFF;

    if (len > 0 && payload) {
        const uint8_t *src = (const uint8_t *)payload;
        for (uint32_t i = 0; i < len; i++)
            PHDP_BUF[PHDP_HEADER_SIZE + i] = src[i];
    }

    uint32_t body = PHDP_HEADER_SIZE + len;
    uint16_t crc = phdp_crc16(PHDP_BUF, body);
    PHDP_BUF[body + 0] = (crc >> 0) & 0xFF;
    PHDP_BUF[body + 1] = (crc >> 8) & 0xFF;

    uint32_t total = body + PHDP_CRC_SIZE;
    for (uint32_t i = 0; i < total; i++)
        uart_putc(PHDP_BUF[i]);
}

/* Receive a complete packet, blocking up to timeout_cycles. Returns
 * the cmd byte on success or -1 on timeout; *out_len is the payload
 * length and the payload starts at PHDP_BUF + PHDP_HEADER_SIZE.
 *
 * Resync rules: drop non-STX bytes at position 0; drop in-progress
 * frames whose length exceeds PHDP_MAX_CHUNK or whose CRC mismatches. */
SHARED_ATTR
static int phdp_recv(uint32_t timeout_cycles, uint32_t *out_len) {
    uint32_t start = SYS_CYCLE_LO;
    uint32_t pos = 0;
    uint32_t payload_len = 0;
    uint32_t need = PHDP_HEADER_SIZE;
    int have_header = 0;

    while ((SYS_CYCLE_LO - start) < timeout_cycles) {
        uint8_t c;
        if (!uart_getc(&c))
            continue;

        if (pos == 0 && c != PHDP_STX)
            continue;

        PHDP_BUF[pos++] = c;

        if (!have_header && pos == PHDP_HEADER_SIZE) {
            payload_len = (uint32_t)PHDP_BUF[3]
                        | ((uint32_t)PHDP_BUF[4] << 8)
                        | ((uint32_t)PHDP_BUF[5] << 16)
                        | ((uint32_t)PHDP_BUF[6] << 24);
            if (payload_len > PHDP_MAX_CHUNK) {
                pos = 0;
                continue;
            }
            need = PHDP_HEADER_SIZE + payload_len + PHDP_CRC_SIZE;
            have_header = 1;
        }

        if (have_header && pos == need) {
            uint32_t body = PHDP_HEADER_SIZE + payload_len;
            uint16_t expected = phdp_crc16(PHDP_BUF, body);
            uint16_t got = (uint16_t)PHDP_BUF[body]
                         | ((uint16_t)PHDP_BUF[body + 1] << 8);
            if (expected != got) {
                pos = 0;
                have_header = 0;
                need = PHDP_HEADER_SIZE;
                continue;
            }
            if (out_len) *out_len = payload_len;
            return (int)PHDP_BUF[2];
        }
    }
    return -1;
}
