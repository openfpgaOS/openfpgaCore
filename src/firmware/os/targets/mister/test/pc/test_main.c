//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * Host (PC-native) unit tests for the MiSTer firmware FAT stack.
 *
 * Drives the real targets/mister/file.c + save.c (compiled unmodified) over
 * the vendored FatFs, backed by a host pread/pwrite block-device shim
 * (blockdev_host.c) on the .vhd image the SDK .mkimage tool builds from the
 * genfix.c fixtures.  Nothing in the firmware tree is touched.
 *
 * Coverage:
 *   - init order (of_disk_init before of_file_init, mirroring hal/hal.c)
 *   - slot→path fixed map + byte-exact reads (os.ini, app.elf)
 *   - multi-cluster / chunked reads (of_file_read_chunked, 140 KB app.elf)
 *   - unaligned offset/length reads
 *   - read past EOF -> error; size64 on missing slot -> -1
 *   - of_file_get_name for fixed + dynamic asset slots
 *   - dynamic enumeration assigns ids 4..6 then 20+
 *   - of_file_flags present/absent
 *   - of_file_datatable_word id/size synthesis
 *   - nvslot read/write round-trip + power-cycle durability
 *   - nvslot set_size(0) zeroes first sector, rest intact
 *   - nvslot capacity + out-of-range rejection
 *   - async read into a host buffer (token/result/data + busy semantics)
 */

#include "file.h"
#include "save.h"
#include "disk.h"
#include "fatfs/ff.h"

#include "test_support.h"
#include "fixture_pattern.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

/* Must match genfix.c. */
#define APP_ELF_SIZE   140000u
#define DATA1_SIZE     6053u
#define LEVEL2_SIZE    1031u

#define NV_CAP         (256u * 1024u)   /* OF_TARGET_SAVE_SLOT_SIZE */
#define SECTOR_BYTES   512u

static const char OS_INI_TEXT[] =
    "[os]\n"
    "ELF=app.elf\n"
    "name=fat-stack-host-test\n"
    "; deterministic fixture for targets/mister/test/pc\n";

/* ---- assertion plumbing --------------------------------------------- */

static int g_pass, g_fail;
static const char *g_img_path;

#define CHECK(cond, ...) do {                                            \
    if (cond) { g_pass++; }                                              \
    else {                                                              \
        g_fail++;                                                        \
        fprintf(stderr, "FAIL %s:%d: ", __func__, __LINE__);            \
        fprintf(stderr, __VA_ARGS__);                                   \
        fprintf(stderr, "\n");                                          \
    }                                                                   \
} while (0)

/* ---- helpers -------------------------------------------------------- */

/* (Re)mount the image: simulate cold boot / power cycle.  Mirrors the real
 * hal/hal.c init order — of_disk_init() selects the bridge backend, then
 * of_file_init() resets the FatFs mount + FIL cache state. */
static void mount_image(int read_only) {
    if (of_test_blockdev_open(g_img_path, read_only) != 0) {
        fprintf(stderr, "FATAL: cannot open image %s\n", g_img_path);
        exit(2);
    }
    of_disk_init();
    of_file_init();
    of_save_init();
}

static void power_cycle(void) {
    of_test_blockdev_close();
    mount_image(0);
}

/* Verify a region read from app.elf matches the generator pattern. */
static int pattern_ok(uint32_t seed, const uint8_t *got, uint32_t off, uint32_t len) {
    for (uint32_t i = 0; i < len; i++)
        if (got[i] != of_fix_byte(seed, off + i))
            return 0;
    return 1;
}

/* ====================================================================== */
/* Tests                                                                  */
/* ====================================================================== */

static void test_init_and_present(void) {
    /* mount_image already ran of_disk_init/of_file_init in main. */
    const of_disk_driver_t *drv = of_disk_active();
    CHECK(drv == &of_disk_bridge, "active backend should be the FAT bridge");
    CHECK(of_file_size64(2) == (int64_t)(sizeof(OS_INI_TEXT) - 1),
          "os.ini size mismatch");
}

static void test_osini_byte_exact(void) {
    uint8_t buf[256];
    int rc = of_file_read(2, 0, buf, sizeof(OS_INI_TEXT) - 1);
    CHECK(rc == 0, "of_file_read(os.ini) rc=%d", rc);
    CHECK(memcmp(buf, OS_INI_TEXT, sizeof(OS_INI_TEXT) - 1) == 0,
          "os.ini content mismatch");
}

static void test_appelf_size_and_full_read(void) {
    CHECK(of_file_size64(3) == (int64_t)APP_ELF_SIZE,
          "app.elf size64 = %lld want %u",
          (long long)of_file_size64(3), APP_ELF_SIZE);
    CHECK(of_file_size(3) == (long)APP_ELF_SIZE, "app.elf size mismatch");

    uint8_t *buf = malloc(APP_ELF_SIZE);
    CHECK(buf != NULL, "malloc app buffer");
    if (!buf) return;

    /* Exercise the 32 KB DMA chunking through of_file_read_chunked. */
    memset(buf, 0xAB, APP_ELF_SIZE);
    int rc = of_file_read_chunked(3, 0, buf, APP_ELF_SIZE);
    CHECK(rc == 0, "of_file_read_chunked(app.elf) rc=%d", rc);
    CHECK(pattern_ok(OF_FIX_SEED_APP, buf, 0, APP_ELF_SIZE),
          "app.elf full-read pattern mismatch");
    free(buf);
}

static void test_unaligned_reads(void) {
    /* (offset, length) pairs: odd offsets, prime-ish lengths, cluster edges. */
    static const struct { uint32_t off, len; } cases[] = {
        { 0,      1 },
        { 1,      3 },
        { 7,      511 },
        { 511,    513 },
        { 513,    1 },
        { 1000,   4095 },
        { 32768 - 5, 4097 },   /* straddle a 32 KB chunk boundary */
        { 65535,  4096 },
        { APP_ELF_SIZE - 1, 1 },
        { APP_ELF_SIZE - 4097, 4097 },
    };
    uint8_t buf[8192];
    for (size_t i = 0; i < sizeof(cases)/sizeof(cases[0]); i++) {
        uint32_t off = cases[i].off, len = cases[i].len;
        memset(buf, 0x5A, sizeof(buf));
        int rc = of_file_read(3, off, buf, len);
        CHECK(rc == 0, "read off=%u len=%u rc=%d", off, len, rc);
        CHECK(pattern_ok(OF_FIX_SEED_APP, buf, off, len),
              "pattern mismatch off=%u len=%u", off, len);
    }
}

static void test_read_eof_and_missing(void) {
    uint8_t buf[64];
    /* Read straddling EOF must fail (short read -> OF_ERR_BAD_RANGE). */
    int rc = of_file_read(3, APP_ELF_SIZE - 10, buf, 64);
    CHECK(rc < 0, "read past EOF should fail, rc=%d", rc);

    /* Read entirely past EOF must fail. */
    rc = of_file_read(3, APP_ELF_SIZE + 100, buf, 16);
    CHECK(rc < 0, "read beyond EOF should fail, rc=%d", rc);

    /* size64 on an unmapped slot -> -1.  Slot 25 is a dynamic id with no
     * file behind it (only 2 assets, so ids 4,5 used; 6 and 20+ are unused). */
    CHECK(of_file_size64(25) == -1, "missing slot size64 should be -1");
    CHECK(of_file_size64(30) == -1, "missing slot size64 should be -1");
}

/* Find the dynamic slot id whose basename matches `name`, or -1. */
static int find_dyn_slot(const char *name) {
    char nm[64];
    for (uint32_t id = 4; id < 32; id++) {
        if (of_file_get_name(id, nm, sizeof(nm)) == 0 && strcmp(nm, name) == 0)
            return (int)id;
    }
    return -1;
}

static void test_get_name(void) {
    char nm[64];

    CHECK(of_file_get_name(2, nm, sizeof(nm)) == 0 && strcmp(nm, "os.ini") == 0,
          "slot 2 name = '%s'", nm);
    CHECK(of_file_get_name(3, nm, sizeof(nm)) == 0 && strcmp(nm, "app.elf") == 0,
          "slot 3 name = '%s'", nm);
    CHECK(of_file_get_name(8, nm, sizeof(nm)) == 0 && strcmp(nm, "shared.cfg") == 0,
          "slot 8 name = '%s'", nm);
    CHECK(of_file_get_name(9, nm, sizeof(nm)) == 0 && strcmp(nm, "duke3d.cfg") == 0,
          "slot 9 name = '%s'", nm);
    CHECK(of_file_get_name(10, nm, sizeof(nm)) == 0 && strcmp(nm, "slot_0.sav") == 0,
          "slot 10 name = '%s'", nm);
    CHECK(of_file_get_name(19, nm, sizeof(nm)) == 0 && strcmp(nm, "slot_9.sav") == 0,
          "slot 19 name = '%s'", nm);

    /* boot.rom (id 1) is RAM-backed but still names itself. */
    CHECK(of_file_get_name(1, nm, sizeof(nm)) == 0 && strcmp(nm, "boot.rom") == 0,
          "slot 1 name = '%s'", nm);
}

static void test_dynamic_enumeration(void) {
    /* Exactly two assets exist; enumeration assigns the low ids 4,5 first
     * (DYN_SLOT_FIRST_LOW), order = FatFs directory order, so check by name
     * rather than fixed id. */
    int s1 = find_dyn_slot("data1.bin");
    int s2 = find_dyn_slot("level2.dat");
    CHECK(s1 >= 0, "data1.bin not registered as a dynamic slot");
    CHECK(s2 >= 0, "level2.dat not registered as a dynamic slot");
    CHECK(s1 != s2, "two assets collapsed onto one slot");

    /* Both must land in the low band 4..6 (only two files, so 4 and 5). */
    CHECK(s1 >= 4 && s1 <= 6, "data1.bin slot %d outside dynamic low band", s1);
    CHECK(s2 >= 4 && s2 <= 6, "level2.dat slot %d outside dynamic low band", s2);

    /* Byte-exact read of each asset via its dynamic slot. */
    if (s1 >= 0) {
        uint8_t buf[DATA1_SIZE];
        int rc = of_file_read((uint32_t)s1, 0, buf, DATA1_SIZE);
        CHECK(rc == 0, "read data1.bin rc=%d", rc);
        CHECK(pattern_ok(OF_FIX_SEED_ASSET1, buf, 0, DATA1_SIZE),
              "data1.bin pattern mismatch");
        CHECK(of_file_size64((uint32_t)s1) == (int64_t)DATA1_SIZE,
              "data1.bin size mismatch");
    }
    if (s2 >= 0) {
        uint8_t buf[LEVEL2_SIZE];
        int rc = of_file_read((uint32_t)s2, 0, buf, LEVEL2_SIZE);
        CHECK(rc == 0, "read level2.dat rc=%d", rc);
        CHECK(pattern_ok(OF_FIX_SEED_ASSET2, buf, 0, LEVEL2_SIZE),
              "level2.dat pattern mismatch");
    }
}

static void test_flags(void) {
    /* Present slots return their own id. */
    CHECK(of_file_flags(2) == 2, "flags(os.ini)");
    CHECK(of_file_flags(3) == 3, "flags(app.elf)");
    /* Pre-created nonvolatile slots report present even when logically
     * empty (zero-filled by mkimage). */
    CHECK(of_file_flags(8) == 8, "flags(shared.cfg) should be present");
    CHECK(of_file_flags(10) == 10, "flags(slot_0.sav) should be present");
    CHECK(of_file_flags(19) == 19, "flags(slot_9.sav) should be present");
    /* Absent dynamic slot -> -1. */
    CHECK(of_file_flags(25) == -1, "flags(absent) should be -1");
}

static void test_datatable_word(void) {
    uint32_t v;
    /* entry 1 -> slot id 1: word 2 = id, word 3 = size. */
    CHECK(of_file_datatable_word(2, &v) == 0 && v == 1,
          "datatable id word for entry 1 = %u", v);
    /* boot slot has no boot loaded in the harness, so size synthesises 0. */
    CHECK(of_file_datatable_word(3, &v) == 0 && v == 0,
          "datatable size word for entry 1 (boot, unloaded) = %u", v);

    /* entry 3 -> slot id 3 (app.elf): size word == APP_ELF_SIZE. */
    CHECK(of_file_datatable_word(6, &v) == 0 && v == 3,
          "datatable id word for entry 3 = %u", v);
    CHECK(of_file_datatable_word(7, &v) == 0 && v == APP_ELF_SIZE,
          "datatable size word for entry 3 (app.elf) = %u want %u", v, APP_ELF_SIZE);

    /* entry 8 maps to slot id 8 (presave). entries 9..18 -> 10..19. */
    CHECK(of_file_datatable_word(16, &v) == 0 && v == 8,
          "datatable id word for entry 8 = %u", v);
    CHECK(of_file_datatable_word(18, &v) == 0 && v == 10,
          "datatable id word for entry 9 = %u", v);

    /* Out-of-range entry (>18) -> -1. */
    CHECK(of_file_datatable_word(40, &v) == -1, "datatable entry 20 should fail");
}

static void test_nvslot_capacity_and_bounds(void) {
    CHECK(of_nvslot_capacity(10) == NV_CAP, "save slot capacity = %u",
          of_nvslot_capacity(10));
    CHECK(of_nvslot_capacity(8) == NV_CAP, "config slot capacity = %u",
          of_nvslot_capacity(8));
    CHECK(of_nvslot_is_supported(10) == 1, "slot 10 supported");
    CHECK(of_nvslot_is_supported(7) == 0, "slot 7 (bank) not nvslot");
    CHECK(of_nvslot_is_supported(20) == 0, "slot 20 not nvslot");

    uint8_t b[16];
    /* offset > cap rejected. */
    CHECK(of_nvslot_write(10, b, NV_CAP + 1, 1) < 0, "write past cap should fail");
    /* len overruns cap rejected. */
    CHECK(of_nvslot_write(10, b, NV_CAP - 4, 8) < 0, "write overrun should fail");
    CHECK(of_nvslot_read(10, b, NV_CAP - 4, 8) < 0, "read overrun should fail");
}

static void test_nvslot_roundtrip_and_durability(void) {
    /* Write a recognisable pattern at a few offsets in slot 12. */
    uint8_t wbuf[300];
    for (uint32_t i = 0; i < sizeof(wbuf); i++)
        wbuf[i] = (uint8_t)(0xC0 ^ (i * 7u));

    int rc = of_nvslot_write(12, wbuf, 0, sizeof(wbuf));
    CHECK(rc == (int)sizeof(wbuf), "nvslot_write @0 rc=%d", rc);

    uint8_t wbuf2[123];
    for (uint32_t i = 0; i < sizeof(wbuf2); i++)
        wbuf2[i] = (uint8_t)(0x3C + i);
    rc = of_nvslot_write(12, wbuf2, 4096, sizeof(wbuf2));
    CHECK(rc == (int)sizeof(wbuf2), "nvslot_write @4096 rc=%d", rc);

    /* Read back immediately (same handle, through the FIL cache). */
    uint8_t rbuf[300];
    rc = of_nvslot_read(12, rbuf, 0, sizeof(wbuf));
    CHECK(rc == (int)sizeof(wbuf) && memcmp(rbuf, wbuf, sizeof(wbuf)) == 0,
          "nvslot read-back @0 mismatch (rc=%d)", rc);

    /* Power cycle: close + reopen the image, re-init the FAT state, and
     * confirm the write-through + f_sync actually persisted to the medium. */
    power_cycle();

    uint8_t pbuf[300];
    rc = of_nvslot_read(12, pbuf, 0, sizeof(wbuf));
    CHECK(rc == (int)sizeof(wbuf) && memcmp(pbuf, wbuf, sizeof(wbuf)) == 0,
          "nvslot @0 did not persist across power cycle (rc=%d)", rc);

    uint8_t pbuf2[123];
    rc = of_nvslot_read(12, pbuf2, 4096, sizeof(wbuf2));
    CHECK(rc == (int)sizeof(wbuf2) && memcmp(pbuf2, wbuf2, sizeof(wbuf2)) == 0,
          "nvslot @4096 did not persist across power cycle (rc=%d)", rc);
}

static void test_nvslot_set_size_zero(void) {
    /* Fill the first 1 KB of slot 13, then set_size(0) should zero only the
     * first sector (512 B) and leave the rest intact. */
    uint8_t fill[1024];
    for (uint32_t i = 0; i < sizeof(fill); i++)
        fill[i] = (uint8_t)(0xA5 ^ (i & 0xFF));
    int rc = of_nvslot_write(13, fill, 0, sizeof(fill));
    CHECK(rc == (int)sizeof(fill), "prefill slot 13 rc=%d", rc);

    rc = of_nvslot_set_size(13, 0);
    CHECK(rc == 0, "set_size(13,0) rc=%d", rc);

    uint8_t rbuf[1024];
    rc = of_nvslot_read(13, rbuf, 0, sizeof(rbuf));
    CHECK(rc == (int)sizeof(rbuf), "read slot 13 after set_size rc=%d", rc);

    int first_zero = 1;
    for (uint32_t i = 0; i < SECTOR_BYTES; i++)
        if (rbuf[i] != 0) { first_zero = 0; break; }
    CHECK(first_zero, "set_size(0) did not zero the first sector");

    int tail_intact = 1;
    for (uint32_t i = SECTOR_BYTES; i < sizeof(fill); i++)
        if (rbuf[i] != fill[i]) { tail_intact = 0; break; }
    CHECK(tail_intact, "set_size(0) corrupted bytes beyond the first sector");

    /* Durable across power cycle too. */
    power_cycle();
    rc = of_nvslot_read(13, rbuf, 0, sizeof(rbuf));
    CHECK(rc == (int)sizeof(rbuf), "reread slot 13 after cycle rc=%d", rc);
    first_zero = 1;
    for (uint32_t i = 0; i < SECTOR_BYTES; i++)
        if (rbuf[i] != 0) { first_zero = 0; break; }
    CHECK(first_zero, "set_size(0) first sector not durable");
}

/* ---- async ---------------------------------------------------------- */

static volatile int   cb_fired;
static volatile int   cb_token;
static volatile int   cb_result;

static void async_cb(int token, int result) {
    cb_fired  = 1;
    cb_token  = token;
    cb_result = result;
}

static void test_async_read(void) {
    /* Read into a HOST buffer (NOT the target-physical CRAM0 stage pool). */
    enum { N = 4096 };
    uint8_t *buf = malloc(N);
    CHECK(buf != NULL, "malloc async buffer");
    if (!buf) return;
    memset(buf, 0xEE, N);

    cb_fired = 0; cb_token = -999; cb_result = -999;
    uint32_t off = 12345;
    int token = of_file_read_async(3, off, buf, N, async_cb);
    CHECK(token >= 0, "of_file_read_async returned token=%d", token);

    /* Deferred-callback model: the read landed inline, but the callback is
     * pending until poll/irq drains it. */
    CHECK(of_file_async_busy() == 1, "async should report busy before drain");
    CHECK(cb_fired == 0, "callback fired before drain");

    /* Data is already in the host buffer (inline read). */
    CHECK(pattern_ok(OF_FIX_SEED_APP, buf, off, N),
          "async data mismatch before drain");

    int drained = of_file_async_poll();
    CHECK(drained == 1, "async_poll should drain exactly one completion");
    CHECK(cb_fired == 1, "callback did not fire on poll");
    CHECK(cb_token == token, "callback token=%d want %d", cb_token, token);
    CHECK(cb_result == 0, "callback result=%d", cb_result);
    CHECK(of_file_async_busy() == 0, "async still busy after drain");

    /* A second poll with nothing pending returns 0. */
    CHECK(of_file_async_poll() == 0, "spurious second async drain");

    /* IRQ-drain path: start another, drain via of_file_async_irq_service. */
    cb_fired = 0;
    int t2 = of_file_read_async(3, 0, buf, N, async_cb);
    CHECK(t2 >= 0 && t2 != token, "second async token=%d", t2);
    of_file_async_irq_service();
    CHECK(cb_fired == 1 && cb_token == t2 && cb_result == 0,
          "irq-service drain failed (fired=%d tok=%d res=%d)",
          cb_fired, cb_token, cb_result);

    /* Oversized async request rejected (> CRAM0 DMA chunk = 32 KB). */
    int rej = of_file_read_async(3, 0, buf, 33u * 1024u, async_cb);
    CHECK(rej < 0, "oversized async should be rejected, rc=%d", rej);

    free(buf);
}

/* ---- read-only mount ------------------------------------------------- */

static void test_readonly_mount(void) {
    /* Re-mount the same image write-protected and confirm reads still work
     * but nvslot writes are refused (disk_write -> RES_WRPRT). */
    of_test_blockdev_close();
    if (of_test_blockdev_open(g_img_path, 1) != 0) {
        CHECK(0, "cannot reopen image read-only");
        return;
    }
    of_disk_init();
    of_file_init();
    of_save_init();

    uint8_t buf[64];
    CHECK(of_file_read(2, 0, buf, 16) == 0, "read os.ini on RO mount");

    uint8_t w[16] = {1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16};
    CHECK(of_nvslot_write(11, w, 0, sizeof(w)) < 0,
          "nvslot_write must fail on a read-only image");

    /* Restore a writable mount for any later work. */
    power_cycle();
}

/* ====================================================================== */

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s <image.vhd>\n", argv[0]);
        return 2;
    }
    g_img_path = argv[1];

    mount_image(0);

    test_init_and_present();
    test_osini_byte_exact();
    test_appelf_size_and_full_read();
    test_unaligned_reads();
    test_read_eof_and_missing();
    test_get_name();
    test_dynamic_enumeration();
    test_flags();
    test_datatable_word();
    test_nvslot_capacity_and_bounds();
    test_nvslot_roundtrip_and_durability();
    test_nvslot_set_size_zero();
    test_async_read();
    test_readonly_mount();

    of_test_blockdev_close();

    printf("\n==== MiSTer FAT-stack host tests ====\n");
    printf("  passed: %d\n", g_pass);
    printf("  failed: %d\n", g_fail);
    printf("  result: %s\n", g_fail ? "FAIL" : "OK");
    return g_fail ? 1 : 0;
}
