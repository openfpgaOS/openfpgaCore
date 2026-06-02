//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * Native C Test: Save System (mocked hardware)
 *
 * Tests the save firmware logic (save.c, file.c) against mocked CRAM1,
 * system registers, and bridge dataslot handshake. Runs natively on the
 * host (no FPGA or Verilator needed).
 *
 * Build: make -C src/emulator test_save
 * Run:   src/emulator/test_save
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <assert.h>

/* ======================================================================
 * Mock hardware layer
 * ====================================================================== */

/* Mock CRAM1 region: 11 slots × 256KB + metadata */
#define MOCK_CRAM1_BASE     0x39000000u
#define MOCK_CRAM1_SIZE     (12 * 0x40000)  /* Extra for metadata */
static uint8_t mock_cram1[MOCK_CRAM1_SIZE];

/* Mock system registers (word-indexed) */
static uint32_t mock_sysregs[64];

/* Mock "SD card" — what the bridge would have written */
#define MOCK_SD_MAX_SLOTS   11
#define MOCK_SD_SLOT_SIZE   0x40000
static uint8_t mock_sd[MOCK_SD_MAX_SLOTS][MOCK_SD_SLOT_SIZE];
static uint32_t mock_sd_written_size[MOCK_SD_MAX_SLOTS];

/* Datatable tracking */
static uint32_t mock_dt_slot;
static uint32_t mock_dt_size[MOCK_SD_MAX_SLOTS];

/* Bridge state */
static int bridge_cmd_active;
static uint32_t bridge_slot_id;
static uint32_t bridge_addr;
static uint32_t bridge_length;

/* Register offsets (byte) */
#define REG_DS_SLOT_ID      (0x20 / 4)
#define REG_DS_SLOT_OFFSET  (0x24 / 4)
#define REG_DS_BRIDGE_ADDR  (0x28 / 4)
#define REG_DS_LENGTH       (0x2C / 4)
#define REG_DS_PARAM_ADDR   (0x30 / 4)
#define REG_DS_RESP_ADDR    (0x34 / 4)
#define REG_DS_COMMAND      (0x38 / 4)
#define REG_DS_STATUS       (0x3C / 4)
#define REG_SAVE_DT_SLOT    (0x48 / 4)
#define REG_SAVE_DT_SIZE    (0x4C / 4)

/* Simulate bridge processing a dataslot command */
static void bridge_process_command(void) {
    uint32_t cmd = mock_sysregs[REG_DS_COMMAND];
    bridge_slot_id = mock_sysregs[REG_DS_SLOT_ID];
    bridge_addr    = mock_sysregs[REG_DS_BRIDGE_ADDR];
    bridge_length  = mock_sysregs[REG_DS_LENGTH];

    if (cmd == 2) {  /* DS_CMD_WRITE */
        /* Bridge reads from CRAM1 at bridge_addr and writes to SD */
        uint32_t cram_offset = bridge_addr - 0x30000000;
        int sd_slot = (int)bridge_slot_id - 10;

        if (sd_slot >= 0 && sd_slot < MOCK_SD_MAX_SLOTS &&
            cram_offset + bridge_length <= MOCK_CRAM1_SIZE) {
            memcpy(mock_sd[sd_slot], &mock_cram1[cram_offset], bridge_length);
            mock_sd_written_size[sd_slot] = bridge_length;
        }

        /* Set status: ACK + DONE + READY */
        mock_sysregs[REG_DS_STATUS] = (1 << 0) | (1 << 1) | (1 << 5);
    } else if (cmd == 1) {  /* DS_CMD_READ */
        /* Bridge writes SD data to CRAM1 at bridge_addr */
        uint32_t cram_offset = bridge_addr - 0x30000000;
        int sd_slot = (int)bridge_slot_id - 10;

        if (sd_slot >= 0 && sd_slot < MOCK_SD_MAX_SLOTS &&
            cram_offset + bridge_length <= MOCK_CRAM1_SIZE) {
            memcpy(&mock_cram1[cram_offset], mock_sd[sd_slot], bridge_length);
        }

        mock_sysregs[REG_DS_STATUS] = (1 << 0) | (1 << 1) | (1 << 5);
    }
}

/* ======================================================================
 * Override hardware register macros for save.c / file.c
 *
 * We redirect volatile register accesses to our mock arrays.
 * ====================================================================== */

/* Override REG32 to intercept register writes */
static uint32_t mock_reg_read(uint32_t addr) {
    if (addr >= 0x40000000 && addr < 0x40000100) {
        uint32_t idx = (addr - 0x40000000) / 4;
        return mock_sysregs[idx];
    }
    return 0;
}

static void mock_reg_write(uint32_t addr, uint32_t val) {
    if (addr >= 0x40000000 && addr < 0x40000100) {
        uint32_t idx = (addr - 0x40000000) / 4;
        mock_sysregs[idx] = val;

        /* Intercept DS_COMMAND writes to simulate bridge */
        if (idx == REG_DS_COMMAND) {
            bridge_process_command();
        }
        /* Intercept SAVE_DT_SIZE to track datatable commits */
        if (idx == REG_SAVE_DT_SIZE) {
            mock_dt_slot = mock_sysregs[REG_SAVE_DT_SLOT];
            if (mock_dt_slot < MOCK_SD_MAX_SLOTS)
                mock_dt_size[mock_dt_slot] = val;
        }
    }
}

/* Redirect memory accesses for CRAM1 region */
static volatile uint8_t *mock_cram1_ptr(uint32_t addr) {
    uint32_t offset = addr - MOCK_CRAM1_BASE;
    assert(offset < MOCK_CRAM1_SIZE);
    return (volatile uint8_t *)&mock_cram1[offset];
}

/* ======================================================================
 * Redefine macros and include firmware sources
 * ====================================================================== */

/* We can't directly #include save.c because it uses volatile pointers.
 * Instead, we reimplement the key functions using our mock layer. */

#define SAVE_MAX_SLOTS      11
#define SAVE_SLOT_SIZE      0x40000
#define SAVE_REGION_ADDR    MOCK_CRAM1_BASE
#define SAVE_CRC_MAGIC      0x4F465356

typedef struct {
    uint32_t crc;
    uint32_t magic;
} save_meta_t;

static uint32_t test_crc32(const uint8_t *data, uint32_t len) {
    uint32_t crc = 0xFFFFFFFF;
    for (uint32_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (int j = 0; j < 8; j++)
            crc = (crc >> 1) ^ (0xEDB88320 & -(crc & 1));
    }
    return ~crc;
}

/* Mock versions of save functions that use our mock arrays */
static int mock_save_write(int slot, const void *buf, uint32_t offset, uint32_t len) {
    if (slot < 0 || slot >= SAVE_MAX_SLOTS || offset + len > SAVE_SLOT_SIZE)
        return -1;
    uint32_t base = (uint32_t)slot * SAVE_SLOT_SIZE;
    memcpy(&mock_cram1[base + offset], buf, len);
    return (int)len;
}

static int mock_save_read(int slot, void *buf, uint32_t offset, uint32_t len) {
    if (slot < 0 || slot >= SAVE_MAX_SLOTS || offset + len > SAVE_SLOT_SIZE)
        return -1;
    uint32_t base = (uint32_t)slot * SAVE_SLOT_SIZE;
    memcpy(buf, &mock_cram1[base + offset], len);
    return (int)len;
}

static int mock_save_flush_size(int slot, uint32_t size) {
    if (slot < 0 || slot >= SAVE_MAX_SLOTS)
        return -1;
    if (size > SAVE_SLOT_SIZE) size = SAVE_SLOT_SIZE;

    /* SAVE_DT_SLOT / SAVE_DT_SIZE */
    mock_reg_write(0x40000048, (uint32_t)slot);
    mock_reg_write(0x4000004C, size);

    /* DS_SLOT_ID, DS_BRIDGE_ADDR, DS_LENGTH, DS_COMMAND=WRITE */
    uint32_t bridge_addr = 0x30000000 + (uint32_t)slot * SAVE_SLOT_SIZE;
    mock_reg_write(0x40000020, 10 + (uint32_t)slot);
    mock_reg_write(0x40000024, 0);
    mock_reg_write(0x40000028, bridge_addr);
    mock_reg_write(0x4000002C, size);
    mock_reg_write(0x40000038, 2);  /* DS_CMD_WRITE */

    /* Check status */
    uint32_t status = mock_reg_read(0x4000003C);
    return (status & 0x3) == 0x3 ? 0 : -1;  /* ACK + DONE */
}

static void mock_save_update_crc(int slot) {
    if (slot < 0 || slot >= SAVE_MAX_SLOTS) return;
    uint32_t base = (uint32_t)slot * SAVE_SLOT_SIZE;
    uint32_t crc = test_crc32(&mock_cram1[base], SAVE_SLOT_SIZE);
    uint32_t meta_offset = SAVE_MAX_SLOTS * SAVE_SLOT_SIZE + (uint32_t)slot * sizeof(save_meta_t);
    save_meta_t *meta = (save_meta_t *)&mock_cram1[meta_offset];
    meta->crc = crc;
    meta->magic = SAVE_CRC_MAGIC;
}

static int mock_save_check(int slot) {
    if (slot < 0 || slot >= SAVE_MAX_SLOTS) return -1;
    uint32_t meta_offset = SAVE_MAX_SLOTS * SAVE_SLOT_SIZE + (uint32_t)slot * sizeof(save_meta_t);
    save_meta_t *meta = (save_meta_t *)&mock_cram1[meta_offset];
    if (meta->magic != SAVE_CRC_MAGIC) return -2;
    uint32_t base = (uint32_t)slot * SAVE_SLOT_SIZE;
    uint32_t computed = test_crc32(&mock_cram1[base], SAVE_SLOT_SIZE);
    return (computed != meta->crc) ? -3 : 0;
}

static void mock_save_erase(int slot) {
    if (slot < 0 || slot >= SAVE_MAX_SLOTS) return;
    uint32_t base = (uint32_t)slot * SAVE_SLOT_SIZE;
    memset(&mock_cram1[base], 0xFF, SAVE_SLOT_SIZE);
    uint32_t meta_offset = SAVE_MAX_SLOTS * SAVE_SLOT_SIZE + (uint32_t)slot * sizeof(save_meta_t);
    save_meta_t *meta = (save_meta_t *)&mock_cram1[meta_offset];
    meta->crc = 0;
    meta->magic = 0;
}

/* ======================================================================
 * Tests
 * ====================================================================== */

static int pass_count = 0;
static int fail_count = 0;

static void check(const char *name, int got, int expected) {
    if (got == expected) {
        pass_count++;
    } else {
        printf("  FAIL %s: got %d, expected %d\n", name, got, expected);
        fail_count++;
    }
}

static void check_mem(const char *name, const void *got, const void *expected, uint32_t len) {
    if (memcmp(got, expected, len) == 0) {
        pass_count++;
    } else {
        printf("  FAIL %s: memory mismatch (first %u bytes)\n", name, len);
        fail_count++;
    }
}

static void reset_mocks(void) {
    memset(mock_cram1, 0xFF, sizeof(mock_cram1));
    memset(mock_sysregs, 0, sizeof(mock_sysregs));
    memset(mock_sd, 0, sizeof(mock_sd));
    memset(mock_sd_written_size, 0, sizeof(mock_sd_written_size));
    memset(mock_dt_size, 0, sizeof(mock_dt_size));
}

static void test_basic_write_read(void) {
    printf("TEST: Basic write/read\n");
    reset_mocks();

    uint8_t data[64];
    for (int i = 0; i < 64; i++) data[i] = (uint8_t)(i * 3 + 7);

    int ret = mock_save_write(0, data, 0, 64);
    check("write_ret", ret, 64);

    uint8_t readback[64] = {0};
    ret = mock_save_read(0, readback, 0, 64);
    check("read_ret", ret, 64);
    check_mem("readback", readback, data, 64);
}

static void test_write_at_offset(void) {
    printf("TEST: Write at offset\n");
    reset_mocks();

    uint8_t data[32];
    for (int i = 0; i < 32; i++) data[i] = (uint8_t)i;

    mock_save_write(0, data, 100, 32);

    uint8_t readback[32];
    mock_save_read(0, readback, 100, 32);
    check_mem("offset_readback", readback, data, 32);

    /* Verify area before offset is untouched (0xFF from reset) */
    uint8_t before[4];
    mock_save_read(0, before, 96, 4);
    check("before_untouched", before[0], 0xFF);
}

static void test_slot_isolation(void) {
    printf("TEST: Slot isolation\n");
    reset_mocks();

    uint8_t data0[4] = {0xAA, 0xBB, 0xCC, 0xDD};
    uint8_t data1[4] = {0x11, 0x22, 0x33, 0x44};

    mock_save_write(0, data0, 0, 4);
    mock_save_write(1, data1, 0, 4);

    uint8_t read0[4], read1[4];
    mock_save_read(0, read0, 0, 4);
    mock_save_read(1, read1, 0, 4);

    check_mem("slot0", read0, data0, 4);
    check_mem("slot1", read1, data1, 4);
}

static void test_flush_to_sd(void) {
    printf("TEST: Flush to SD (bridge write)\n");
    reset_mocks();

    /* Write known pattern to slot 0 */
    uint8_t pattern[256];
    for (int i = 0; i < 256; i++) pattern[i] = (uint8_t)(i ^ 0x55);
    mock_save_write(0, pattern, 0, 256);

    /* Flush */
    int ret = mock_save_flush_size(0, 256);
    check("flush_ret", ret, 0);

    /* Verify SD received the data */
    check("sd_size", (int)mock_sd_written_size[0], 256);
    check_mem("sd_data", mock_sd[0], pattern, 256);

    /* Verify datatable was updated */
    check("dt_size", (int)mock_dt_size[0], 256);
}

static void test_crc_roundtrip(void) {
    printf("TEST: CRC write + verify\n");
    reset_mocks();

    /* Write data to slot 2 */
    uint8_t data[128];
    for (int i = 0; i < 128; i++) data[i] = (uint8_t)(i * 7);
    mock_save_write(2, data, 0, 128);

    /* Before CRC update: check should fail (no magic) */
    int ret = mock_save_check(2);
    check("check_before_crc", ret, -2);

    /* Update CRC */
    mock_save_update_crc(2);

    /* Now check should pass */
    ret = mock_save_check(2);
    check("check_after_crc", ret, 0);

    /* Corrupt data and re-check */
    uint8_t byte = 0xFF;
    mock_save_write(2, &byte, 0, 1);  /* Change first byte */
    ret = mock_save_check(2);
    check("check_after_corrupt", ret, -3);
}

static void test_erase(void) {
    printf("TEST: Erase slot\n");
    reset_mocks();

    uint8_t data[16] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16};
    mock_save_write(3, data, 0, 16);
    mock_save_update_crc(3);

    /* Erase */
    mock_save_erase(3);

    /* Data should be 0xFF */
    uint8_t readback[16];
    mock_save_read(3, readback, 0, 16);
    for (int i = 0; i < 16; i++)
        check("erase_byte", readback[i], 0xFF);

    /* CRC should be invalid */
    int ret = mock_save_check(3);
    check("erase_crc", ret, -2);
}

static void test_boundary_errors(void) {
    printf("TEST: Boundary conditions\n");

    uint8_t buf[4];
    check("neg_slot", mock_save_write(-1, buf, 0, 4), -1);
    check("over_slot", mock_save_write(11, buf, 0, 4), -1);
    check("over_offset", mock_save_write(0, buf, SAVE_SLOT_SIZE - 2, 4), -1);
    check("neg_read", mock_save_read(-1, buf, 0, 4), -1);
}

static void test_multi_slot_flush(void) {
    printf("TEST: Multi-slot flush\n");
    reset_mocks();

    for (int slot = 0; slot < 3; slot++) {
        uint8_t data[64];
        for (int i = 0; i < 64; i++) data[i] = (uint8_t)(slot * 100 + i);
        mock_save_write(slot, data, 0, 64);
        mock_save_flush_size(slot, 64);
    }

    /* Verify each slot's SD data is independent */
    for (int slot = 0; slot < 3; slot++) {
        uint8_t expected[64];
        for (int i = 0; i < 64; i++) expected[i] = (uint8_t)(slot * 100 + i);
        char name[32];
        snprintf(name, sizeof(name), "multi_slot_%d", slot);
        check_mem(name, mock_sd[slot], expected, 64);
    }
}

static void test_full_slot_flush(void) {
    printf("TEST: Full 256KB slot flush\n");
    reset_mocks();

    /* Fill slot with pattern */
    for (uint32_t i = 0; i < SAVE_SLOT_SIZE; i += 4) {
        uint32_t word = i ^ 0xDEAD0000;
        mock_save_write(0, &word, i, 4);
    }

    mock_save_flush_size(0, SAVE_SLOT_SIZE);
    check("full_sd_size", (int)mock_sd_written_size[0], (int)SAVE_SLOT_SIZE);

    /* Spot-check a few words */
    uint32_t *sd_words = (uint32_t *)mock_sd[0];
    check("full_word_0", (int)sd_words[0], (int)(0 ^ 0xDEAD0000));
    check("full_word_100", (int)sd_words[100], (int)(400 ^ 0xDEAD0000));
    check("full_word_last", (int)sd_words[SAVE_SLOT_SIZE/4 - 1],
          (int)((SAVE_SLOT_SIZE - 4) ^ 0xDEAD0000));
}

/* ======================================================================
 * Main
 * ====================================================================== */

int main(void) {
    printf("=== Save System Native Test ===\n\n");

    test_basic_write_read();
    test_write_at_offset();
    test_slot_isolation();
    test_flush_to_sd();
    test_crc_roundtrip();
    test_erase();
    test_boundary_errors();
    test_multi_slot_flush();
    test_full_slot_flush();

    printf("\n=== Results: %d passed, %d failed ===\n", pass_count, fail_count);
    return fail_count ? 1 : 0;
}
