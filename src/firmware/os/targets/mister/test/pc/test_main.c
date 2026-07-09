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
 *
 * The legacy suite runs TWICE: once with the multidisk capability clear
 * (old RTL) and once with it set but only S0 mounted — both must behave
 * identically to the historical single-volume stack.
 *
 * Multi-vhd coverage (s0/s1/s2 fixture images, multidisk cap set):
 *   - three-volume mount; os.ini resolves S1-first (S0 fallback shadowed)
 *   - app.elf falls back S1 -> S2 -> S0 (only S0 carries it)
 *   - by-name search order: S1/S2/S0-unique files all reachable
 *   - S1 shadows S0 for a duplicate basename (single registration)
 *   - of_file_resolve_name: dyn passthrough, overflow ids (>= 32) for
 *     library files beyond the 16-entry dyn table, id stability, misses
 *   - writes (nvslot saves + shared.cfg + duke3d.cfg) land ONLY on the S1
 *     image (S0/S2 backing files byte-identical before/after), durable
 *     across power cycles, and readable from a lone-S1 mount
 *   - cap=0 with extra images attached behaves exactly like legacy S0
 *   - cap=1 without S1 (S0+S2) sends writes to volume 0 (legacy rule)
 *   - read-only S0 mount with writable S1; absent S2 skipped cleanly
 *   - late mount: of_disk re-probes on the next access (no permanent
 *     active_disk=NULL trap); instance_root path-budget bounds
 *
 * CD-music streaming (the DOOMMUS.WAD field scenario, legacy + multi):
 *   - >=2 MB wad at /assets/, resolved once by name (overflow id on the
 *     multi mount, dyn id on legacy), then hundreds of 4 KB
 *     of_file_read_async + poll refills at an advancing offset,
 *     INTERLEAVED with nvslot save/config writes, config-style reads,
 *     >4 by-name opens per churn round (FIL-cache eviction pressure) and
 *     periodic sync reads of the same slot — every chunk byte-verified,
 *     every return code checked, id stability re-verified afterwards
 */

#include "file.h"
#include "save.h"
#include "disk.h"
#include "blockdev.h"
#include "fatfs/ff.h"

#include "test_support.h"
#include "fixture_pattern.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <sys/mman.h>   /* MAP_FIXED for the F-loaded ELF staging window */

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
static const char *g_s0_path, *g_s1_path, *g_s2_path;
static int g_cap;   /* multidisk capability for the current legacy pass */

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
 * of_file_init() resets the FatFs mount + FIL cache state.  g_cap selects
 * whether the modeled RTL advertises the multidisk capability; the legacy
 * suite must pass with either value (lone S0 = legacy mode by contract). */
static void mount_image(int read_only) {
    of_test_blockdev_close();
    of_test_blockdev_set_cap(g_cap);
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
/* Multi-vhd (S0/S1/S2) suite                                             */
/* ====================================================================== */

/* FNV-1a 64 over a host file — cheap byte-identity check for the "writes
 * never touch the library images" isolation tests. */
static uint64_t file_hash(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) { perror(path); exit(2); }
    uint64_t h = 0xcbf29ce484222325ull;
    static uint8_t buf[1u << 20];
    size_t n;
    while ((n = fread(buf, 1, sizeof(buf), f)) > 0)
        for (size_t i = 0; i < n; i++)
            h = (h ^ buf[i]) * 0x100000001b3ull;
    fclose(f);
    return h;
}

/* Mount a disk set (NULL path = disk absent) with the multidisk cap set,
 * then cold-boot the FAT stack in hal/hal.c order. */
static void mount_set(const char *s0, int s0_ro,
                      const char *s1, const char *s2) {
    of_test_blockdev_close();
    of_test_blockdev_set_cap(1);
    if (s0 && of_test_blockdev_open_disk(0, s0, s0_ro) != 0) exit(2);
    if (s1 && of_test_blockdev_open_disk(1, s1, 0) != 0) exit(2);
    if (s2 && of_test_blockdev_open_disk(2, s2, 0) != 0) exit(2);
    of_disk_init();
    of_file_init();
    of_save_init();
}

static void mount_multi(void) {
    mount_set(g_s0_path, 0, g_s1_path, g_s2_path);
}

static const char S1_OSINI_TEXT[] = OF_FIX_S1_OSINI;
static const char S0_OSINI_TEXT[] = OF_FIX_S0_OSINI;

static void test_multi_osini_prefers_s1(void) {
    mount_multi();
    CHECK(of_disk_active() == &of_disk_bridge, "bridge backend on multi mount");

    int64_t sz = of_file_size64(2);
    CHECK(sz == (int64_t)(sizeof(S1_OSINI_TEXT) - 1),
          "os.ini size should be S1's (%lld)", (long long)sz);

    uint8_t buf[256];
    int rc = of_file_read(2, 0, buf, sizeof(S1_OSINI_TEXT) - 1);
    CHECK(rc == 0, "read os.ini rc=%d", rc);
    CHECK(memcmp(buf, S1_OSINI_TEXT, sizeof(S1_OSINI_TEXT) - 1) == 0,
          "os.ini content should come from S1");
}

static void test_multi_appelf_falls_back_to_s0(void) {
    /* Only S0 carries app.elf: S1 -> S2 -> S0 search must land on S0. */
    CHECK(of_file_size64(3) == (int64_t)OF_FIX_S0_APP_SIZE,
          "app.elf size64 = %lld want %u",
          (long long)of_file_size64(3), OF_FIX_S0_APP_SIZE);

    uint8_t buf[4096];
    int rc = of_file_read(3, 12345, buf, sizeof(buf));
    CHECK(rc == 0, "read app.elf rc=%d", rc);
    CHECK(pattern_ok(OF_FIX_SEED_S0_APP, buf, 12345, sizeof(buf)),
          "app.elf bytes should come from S0's copy");
}

/* Kernel-style by-name resolution: the registry (dyn ids, mirrored here by
 * find_dyn_slot) first, then the of_file_resolve_name registry-miss
 * fall-through — exactly what openat/of_file_slot_find do. */
static int find_slot_by_name(const char *name) {
    int slot = find_dyn_slot(name);
    if (slot >= 0)
        return slot;
    return of_file_resolve_name(name);
}

static void check_named_read(const char *name, uint32_t seed, uint32_t size,
                             const char *volume_label) {
    int slot = find_slot_by_name(name);
    CHECK(slot >= 0, "%s (%s) not reachable by name", name, volume_label);
    if (slot < 0) return;
    CHECK(of_file_size64((uint32_t)slot) == (int64_t)size,
          "%s size mismatch", name);
    uint8_t *buf = malloc(size);
    CHECK(buf != NULL, "malloc %s", name);
    if (!buf) return;
    int rc = of_file_read((uint32_t)slot, 0, buf, size);
    CHECK(rc == 0, "read %s rc=%d", name, rc);
    CHECK(pattern_ok(seed, buf, 0, size),
          "%s bytes should come from %s", name, volume_label);
    free(buf);
}

static void test_multi_search_order(void) {
    check_named_read("inst_only.dat", OF_FIX_SEED_INST,
                     OF_FIX_INST_SIZE, "S1");
    check_named_read("borrow.dat", OF_FIX_SEED_BORROW,
                     OF_FIX_BORROW_SIZE, "S2");
    check_named_read("family.dat", OF_FIX_SEED_FAMILY,
                     OF_FIX_FAMILY_SIZE, "S0");
}

static void test_multi_s1_shadows_s0(void) {
    /* shared_lib.dat exists on S0 AND S1 with different content/size.
     * Exactly one registration, and it must be S1's copy. */
    int slot = find_dyn_slot("shared_lib.dat");
    CHECK(slot >= 0, "shared_lib.dat not registered");
    if (slot < 0) return;

    int dup = 0;
    char nm[64];
    for (uint32_t id = 4; id < 32; id++) {
        if ((int)id == slot) continue;
        if (of_file_get_name(id, nm, sizeof(nm)) == 0 &&
            strcmp(nm, "shared_lib.dat") == 0)
            dup++;
    }
    CHECK(dup == 0, "shared_lib.dat registered %d extra time(s)", dup);

    CHECK(of_file_size64((uint32_t)slot) == (int64_t)OF_FIX_SHARED_S1_SIZE,
          "shared_lib.dat size should be S1's copy");
    uint8_t buf[OF_FIX_SHARED_S1_SIZE];
    int rc = of_file_read((uint32_t)slot, 0, buf, sizeof(buf));
    CHECK(rc == 0, "read shared_lib.dat rc=%d", rc);
    CHECK(pattern_ok(OF_FIX_SEED_SHARED_S1, buf, 0, sizeof(buf)),
          "shared_lib.dat bytes should be S1's (shadow failed)");
}

static void test_multi_overflow_resolve(void) {
    /* The S0 library was sized to overflow the 16-entry dyn table, so at
     * least the last family files are NOT enumerable... */
    int dyn = find_dyn_slot("family14.dat");
    CHECK(dyn == -1, "family14.dat unexpectedly fit the dyn table (%d)", dyn);

    /* ...but by-name resolution must still find them via overflow ids. */
    int id = of_file_resolve_name("family14.dat");
    CHECK(id >= 32, "resolve(family14.dat) = %d want an overflow id", id);
    if (id < 0) return;

    CHECK(of_file_resolve_name("family14.dat") == id,
          "overflow id must be stable across resolves");

    char nm[64];
    CHECK(of_file_get_name((uint32_t)id, nm, sizeof(nm)) == 0 &&
          strcmp(nm, "family14.dat") == 0,
          "overflow slot name = '%s'", nm);
    CHECK(of_file_size64((uint32_t)id) == (int64_t)OF_FIX_FAMILY_N_SIZE,
          "family14.dat size mismatch");

    uint8_t buf[OF_FIX_FAMILY_N_SIZE];
    int rc = of_file_read((uint32_t)id, 0, buf, sizeof(buf));
    CHECK(rc == 0, "read family14.dat rc=%d", rc);
    CHECK(pattern_ok(OF_FIX_SEED_FAMILY_N + 14u, buf, 0, sizeof(buf)),
          "family14.dat pattern mismatch");

    /* A second distinct overflow name gets a distinct id. */
    int id2 = of_file_resolve_name("family13.dat");
    CHECK(id2 >= 32 && id2 != id, "resolve(family13.dat) = %d", id2);

    /* Dyn passthrough: an enumerated name resolves to its dyn id (< 32). */
    int inst = of_file_resolve_name("inst_only.dat");
    CHECK(inst == find_dyn_slot("inst_only.dat"),
          "resolve(inst_only.dat) should return the dyn id, got %d", inst);

    /* Fixed-slot names and misses do not bind overflow ids. */
    CHECK(of_file_resolve_name("os.ini") == -1, "os.ini must not resolve here");
    CHECK(of_file_resolve_name("nosuchfile.xyz") == -1,
          "missing name must not resolve");
}

static void test_multi_writes_pin_to_s1(void) {
    mount_multi();   /* fresh mount so this test is order-independent */

    uint64_t h0_before = file_hash(g_s0_path);
    uint64_t h2_before = file_hash(g_s2_path);
    uint64_t h1_before = file_hash(g_s1_path);

    /* Save slot 12 + both config slots (8 = shared.cfg, 9 = duke3d.cfg).
     * Salted against the slot's CURRENT first byte so a repeat run against
     * the same images is guaranteed to flip the S1 hash (rewriting
     * identical bytes would leave it unchanged). */
    uint8_t salt = 0;
    (void)of_nvslot_read(12, &salt, 0, 1);
    salt = (uint8_t)(salt + 1u);
    uint8_t save[257], cfg8[64], cfg9[97];
    /* save[0] = old_byte + 1: differs from the stored byte by construction. */
    for (uint32_t i = 0; i < sizeof(save); i++)
        save[i] = (uint8_t)(salt + i);
    for (uint32_t i = 0; i < sizeof(cfg8); i++)
        cfg8[i] = (uint8_t)(0x08 + i + salt);
    for (uint32_t i = 0; i < sizeof(cfg9); i++)
        cfg9[i] = (uint8_t)(0x99 - i - salt);

    CHECK(of_nvslot_write(12, save, 0, sizeof(save)) == (int)sizeof(save),
          "nvslot_write(12) failed");
    CHECK(of_nvslot_write(8, cfg8, 128, sizeof(cfg8)) == (int)sizeof(cfg8),
          "nvslot_write(8/shared.cfg) failed");
    CHECK(of_nvslot_write(9, cfg9, 0, sizeof(cfg9)) == (int)sizeof(cfg9),
          "nvslot_write(9/duke3d.cfg) failed");

    /* Power cycle the full disk set: durability. */
    mount_multi();
    uint8_t rb[257];
    int rc = of_nvslot_read(12, rb, 0, sizeof(save));
    CHECK(rc == (int)sizeof(save) && memcmp(rb, save, sizeof(save)) == 0,
          "save did not persist across multi power cycle (rc=%d)", rc);
    rc = of_nvslot_read(8, rb, 128, sizeof(cfg8));
    CHECK(rc == (int)sizeof(cfg8) && memcmp(rb, cfg8, sizeof(cfg8)) == 0,
          "shared.cfg did not persist (rc=%d)", rc);
    rc = of_nvslot_read(9, rb, 0, sizeof(cfg9));
    CHECK(rc == (int)sizeof(cfg9) && memcmp(rb, cfg9, sizeof(cfg9)) == 0,
          "duke3d.cfg did not persist (rc=%d)", rc);

    /* Isolation: ONLY the S1 backing image changed. */
    CHECK(file_hash(g_s0_path) == h0_before,
          "S0 image bytes changed — a write leaked to the family library");
    CHECK(file_hash(g_s2_path) == h2_before,
          "S2 image bytes changed — a write leaked to the borrow library");
    CHECK(file_hash(g_s1_path) != h1_before,
          "S1 image unchanged — writes did not land on the instance volume");

    /* The data must be readable from a LONE S1 mount (proof of placement,
     * independent of the search logic). */
    mount_set(NULL, 0, g_s1_path, NULL);
    rc = of_nvslot_read(12, rb, 0, sizeof(save));
    CHECK(rc == (int)sizeof(save) && memcmp(rb, save, sizeof(save)) == 0,
          "save not readable from a lone-S1 mount (rc=%d)", rc);
}

static void test_multi_cap0_ignores_extra_disks(void) {
    /* Old RTL model: images attached but the capability bit clear —
     * behavior must be exactly single-volume S0. */
    of_test_blockdev_close();
    of_test_blockdev_set_cap(0);
    if (of_test_blockdev_open_disk(0, g_s0_path, 0) != 0) exit(2);
    if (of_test_blockdev_open_disk(1, g_s1_path, 0) != 0) exit(2);
    if (of_test_blockdev_open_disk(2, g_s2_path, 0) != 0) exit(2);
    of_disk_init();
    of_file_init();
    of_save_init();

    uint8_t buf[256];
    CHECK(of_file_size64(2) == (int64_t)(sizeof(S0_OSINI_TEXT) - 1),
          "cap=0: os.ini must be S0's");
    int rc = of_file_read(2, 0, buf, sizeof(S0_OSINI_TEXT) - 1);
    CHECK(rc == 0 && memcmp(buf, S0_OSINI_TEXT, sizeof(S0_OSINI_TEXT) - 1) == 0,
          "cap=0: os.ini content must come from S0");
    CHECK(find_dyn_slot("inst_only.dat") == -1,
          "cap=0: S1 files must be invisible");
    CHECK(find_dyn_slot("borrow.dat") == -1,
          "cap=0: S2 files must be invisible");
    CHECK(find_dyn_slot("family.dat") >= 0,
          "cap=0: S0 enumeration broken");
}

static void test_multi_no_s1_writes_to_volume0(void) {
    /* cap set but S1 absent (S0+S2): not instance mode — writes go to
     * volume 0 per the contract's legacy rule. */
    mount_set(g_s0_path, 0, NULL, g_s2_path);

    uint64_t h2_before = file_hash(g_s2_path);
    uint64_t h0_before = file_hash(g_s0_path);

    /* Salt from the slot's current byte: the write must differ from what
     * is stored so the S0 hash-change assertion holds on repeat runs. */
    uint8_t salt = 0;
    (void)of_nvslot_read(13, &salt, 0, 1);
    salt = (uint8_t)(salt + 1u);
    uint8_t w[33];
    for (uint32_t i = 0; i < sizeof(w); i++)
        w[i] = (uint8_t)(salt + i);
    CHECK(of_nvslot_write(13, w, 0, sizeof(w)) == (int)sizeof(w),
          "nvslot_write without S1 failed");

    CHECK(file_hash(g_s0_path) != h0_before,
          "write without S1 must land on volume 0 (S0)");
    CHECK(file_hash(g_s2_path) == h2_before,
          "write without S1 leaked to the borrow library");

    /* Search still works: borrow.dat (S2) + family.dat (S0). */
    check_named_read("borrow.dat", OF_FIX_SEED_BORROW,
                     OF_FIX_BORROW_SIZE, "S2");
    check_named_read("family.dat", OF_FIX_SEED_FAMILY,
                     OF_FIX_FAMILY_SIZE, "S0");
}

static void test_multi_readonly_s0(void) {
    /* Read-only family library + writable instance volume. */
    mount_set(g_s0_path, 1, g_s1_path, g_s2_path);

    uint8_t buf[64];
    int slot = find_slot_by_name("family.dat");
    CHECK(slot >= 0 && of_file_read((uint32_t)slot, 0, buf, sizeof(buf)) == 0,
          "read from RO family library failed");

    uint8_t w[16] = {1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16};
    CHECK(of_nvslot_write(14, w, 0, sizeof(w)) == (int)sizeof(w),
          "nvslot_write must succeed on writable S1 despite RO S0");

    /* Low-level: disk 0 write refused outright. */
    uint8_t sect[512] = {0};
    CHECK(of_blockdev_write(0, sect, 0, 1) < 0,
          "of_blockdev_write(0) must fail on a read-only disk");
}

static void test_multi_absent_s2(void) {
    mount_set(g_s0_path, 0, g_s1_path, NULL);
    CHECK(find_dyn_slot("borrow.dat") == -1,
          "borrow.dat visible without S2 mounted");
    CHECK(of_file_resolve_name("borrow.dat") == -1,
          "resolve(borrow.dat) must miss without S2");
    check_named_read("inst_only.dat", OF_FIX_SEED_INST,
                     OF_FIX_INST_SIZE, "S1");
    check_named_read("family.dat", OF_FIX_SEED_FAMILY,
                     OF_FIX_FAMILY_SIZE, "S0");
}

static void test_late_mount_reprobe(void) {
    /* Cold boot with NO disk: of_disk_init leaves active_disk NULL.  A
     * disk mounted later must be picked up on the next access (the old
     * code failed every read forever). */
    of_test_blockdev_close();
    of_test_blockdev_set_cap(1);
    of_disk_init();
    of_file_init();
    of_save_init();
    CHECK(of_disk_active() == NULL, "no backend should probe with no disk");

    uint8_t buf[256];
    CHECK(of_file_read(2, 0, buf, 16) < 0, "read with no disk should fail");

    /* The image appears (legacy test.vhd on disk 0)... */
    if (of_test_blockdev_open_disk(0, g_img_path, 0) != 0) exit(2);

    /* ...and the very next access must succeed WITHOUT re-running init. */
    CHECK(of_disk_size(2) == (long)(sizeof(OS_INI_TEXT) - 1),
          "of_disk_size must re-probe after a late mount");
    int rc = of_file_read(2, 0, buf, sizeof(OS_INI_TEXT) - 1);
    CHECK(rc == 0 && memcmp(buf, OS_INI_TEXT, sizeof(OS_INI_TEXT) - 1) == 0,
          "of_file_read must recover after a late mount (rc=%d)", rc);
    CHECK(of_disk_active() == &of_disk_bridge,
          "bridge backend should be bound after the late mount");
}

static void test_instance_root_path_budget(void) {
    /* Longest legal instance root + volume prefix must stay within every
     * pathbuf (ASAN would flag any overflow).  The /games tree does not
     * exist on the fixtures, so lookups just miss — cleanly. */
    mount_image(0);
    of_file_set_instance_root("/games/A-very-long-instance-name-0123456789012");
    char nm[64];
    CHECK(of_file_size64(19) == -1, "size64 under long root should miss");
    CHECK(of_file_get_name(19, nm, sizeof(nm)) == 0 &&
          strcmp(nm, "slot_9.sav") == 0,
          "get_name under long root = '%s'", nm);
    CHECK(of_file_size64(2) == -1, "os.ini under long root should miss");
    of_file_set_instance_root("");
    CHECK(of_file_size64(2) > 0, "os.ini must resolve again after root reset");
}

/* ====================================================================== */
/* CD-music streaming (the DOOMMUS.WAD field scenario)                    */
/* ====================================================================== */

#define STREAM_CHUNK    4096u
#define STREAM_FAIL_CAP 25      /* stop flooding stderr on systemic failure */

typedef struct { const char *name; uint32_t seed, size; } churn_file_t;

/* Open-and-verify one named file: the FIL-cache eviction pressure the app
 * generates by opening save/config/asset files during gameplay. */
static void churn_named_file(const churn_file_t *cf) {
    int slot = find_slot_by_name(cf->name);
    CHECK(slot >= 0, "churn: %s not resolvable", cf->name);
    if (slot < 0) return;
    uint8_t buf[192];
    uint32_t len = cf->size < sizeof(buf) ? cf->size : (uint32_t)sizeof(buf);
    uint32_t off = (cf->size > len) ? (cf->size - len) / 2u : 0u;
    int rc = of_file_read((uint32_t)slot, off, buf, len);
    CHECK(rc == 0 && pattern_ok(cf->seed, buf, off, len),
          "churn: %s read rc=%d / bytes wrong (off=%u len=%u)",
          cf->name, rc, off, len);
}

/* The app's CD-music pump, verbatim: hundreds of of_file_read_async +
 * of_file_async_poll refills of one slot at an advancing offset, byte-
 * verified, interleaved with nvslot writes, config-style reads, by-name
 * opens (>4 per churn round) and periodic sync reads of the same slot. */
static void stream_wad_loop(int slot, int iters,
                            const churn_file_t *churn, int nchurn) {
    uint8_t *buf  = malloc(STREAM_CHUNK);
    uint8_t *sbuf = malloc(STREAM_CHUNK);
    CHECK(buf != NULL && sbuf != NULL, "malloc stream buffers");
    if (!buf || !sbuf) { free(buf); free(sbuf); return; }

    int fail0 = g_fail;
    uint32_t off = 0;
    int churn_idx = 0;

    for (int iter = 0; iter < iters; iter++) {
        /* Refill: async read, poll to completion, verify data + codes. */
        cb_fired = 0; cb_token = -1; cb_result = -999;
        memset(buf, 0xEE, STREAM_CHUNK);
        int tok = of_file_read_async((uint32_t)slot, off, buf,
                                     STREAM_CHUNK, async_cb);
        CHECK(tok >= 0, "iter %d off %u: read_async rc=%d", iter, off, tok);
        if (tok >= 0) {
            int drained = of_file_async_poll();
            CHECK(drained == 1 && cb_fired == 1 && cb_token == tok &&
                  cb_result == 0,
                  "iter %d off %u: poll=%d fired=%d tok=%d want %d res=%d",
                  iter, off, drained, cb_fired, cb_token, tok, cb_result);
            CHECK(pattern_ok(OF_FIX_SEED_WAD, buf, off, STREAM_CHUNK),
                  "iter %d off %u: chunk bytes wrong", iter, off);
        }

        /* Interleaved gameplay I/O. */
        if (iter % 3 == 1) {            /* save write (rotating slots) */
            uint32_t s = 10u + (uint32_t)(iter % 5);
            uint8_t w[257];
            for (uint32_t i = 0; i < sizeof(w); i++)
                w[i] = (uint8_t)((uint32_t)iter + i);
            uint32_t woff = ((uint32_t)iter * 977u) % (NV_CAP - sizeof(w));
            int rc = of_nvslot_write(s, w, woff, sizeof(w));
            CHECK(rc == (int)sizeof(w), "iter %d: nvslot_write(%u) rc=%d",
                  iter, s, rc);
        }
        if (iter % 5 == 2) {            /* of_config-style read (shared.cfg) */
            uint8_t cfg[128];
            int rc = of_nvslot_read(8, cfg, 0, sizeof(cfg));
            CHECK(rc == (int)sizeof(cfg), "iter %d: cfg read rc=%d", iter, rc);
        }
        if (iter % 7 == 3) {            /* os.ini re-read (fixed-slot search) */
            uint8_t ini[32];
            int rc = of_file_read(2, 0, ini, sizeof(ini));
            CHECK(rc == 0, "iter %d: os.ini read rc=%d", iter, rc);
        }
        if (iter % 11 == 4 && nchurn > 0) {  /* >4 by-name opens per round */
            for (int k = 0; k < 5; k++) {
                churn_named_file(&churn[churn_idx % nchurn]);
                churn_idx++;
            }
        }
        if (iter % 13 == 5) {           /* periodic SYNC read of the slot */
            uint32_t soff = ((uint32_t)iter * 40961u)
                            % (OF_FIX_WAD_SIZE - 1024u);
            int rc = of_file_read((uint32_t)slot, soff, sbuf, 1024u);
            CHECK(rc == 0 && pattern_ok(OF_FIX_SEED_WAD, sbuf, soff, 1024u),
                  "iter %d: sync read off=%u rc=%d / bytes wrong",
                  iter, soff, rc);
        }
        if (iter % 17 == 6) {           /* track-length query (size memo) */
            int64_t sz = of_file_size64((uint32_t)slot);
            CHECK(sz == (int64_t)OF_FIX_WAD_SIZE,
                  "iter %d: stream slot size64 drifted (%lld)",
                  iter, (long long)sz);
        }

        off += STREAM_CHUNK;
        if (off + STREAM_CHUNK > OF_FIX_WAD_SIZE)
            off = 0;                    /* loop the track */

        if (g_fail - fail0 > STREAM_FAIL_CAP) {
            fprintf(stderr, "  (streaming loop aborted at iter %d after %d "
                    "failures)\n", iter, g_fail - fail0);
            break;
        }
    }

    free(buf);
    free(sbuf);
}

/* Legacy image: the same stream drill against a dyn-slot wad. */
static void test_streaming_legacy(void) {
    mount_image(0);

    int slot = find_slot_by_name("DOOMMUS.WAD");
    CHECK(slot >= 4 && slot < 32, "legacy wad slot %d should be a dyn id",
          slot);
    if (slot < 0) return;
    CHECK(of_file_size64((uint32_t)slot) == (int64_t)OF_FIX_WAD_SIZE,
          "legacy wad size64 mismatch");

    static const churn_file_t churn[] = {
        { "data1.bin",  OF_FIX_SEED_ASSET1, DATA1_SIZE },
        { "level2.dat", OF_FIX_SEED_ASSET2, LEVEL2_SIZE },
    };
    stream_wad_loop(slot, 240, churn, 2);

    CHECK(find_slot_by_name("doommus.wad") == slot,
          "legacy wad id lost after churn");
}

/* Multi-vhd: the field scenario proper — the wad on the S2 borrow volume,
 * resolved through the overflow registry (ids 32+). */
static void test_multi_s2_streaming(void) {
    mount_multi();

    /* ROOT-CAUSE demonstration (the "CD music repeats" field failure).
     * The kernel slot registry only holds the probed ids <= 31 — modeled
     * exactly by find_dyn_slot — and the dyn table is full, so the wad is
     * invisible to it.  kernel/syscall.c's file_slot_find had NO
     * of_file_resolve_name fall-through (unlike sys_openat/sys_mount_iso),
     * so of_file_slot_find("DOOMMUS.WAD") returned -1 and the app streamed
     * against an unresolved slot id... */
    CHECK(find_dyn_slot("DOOMMUS.WAD") == -1,
          "fixture: the wad must overflow the registry-visible dyn table");
    /* ...and every refill against an unresolved id fails — continuously,
     * OSD or not — with the error delivered through the callback: */
    uint8_t probe[64];
    cb_fired = 0; cb_result = 0;
    int bad = of_file_read_async(0xFFFFFFFFu, 0, probe, sizeof(probe),
                                 async_cb);
    CHECK(bad >= 0, "read_async hands out a token even for a bad slot");
    (void)of_file_async_poll();
    CHECK(cb_fired == 1 && cb_result < 0,
          "unresolved-slot refill must fail via the callback (res=%d)",
          cb_result);

    /* The FIX: file_slot_find now falls through to of_file_resolve_name —
     * find_slot_by_name models the fixed kernel resolution.  The dyn table
     * is packed by S1's files, so the wad must land in the overflow range. */
    int slot = find_slot_by_name("DOOMMUS.WAD");
    CHECK(slot >= 32 && slot < 32 + 64, "wad slot %d not an overflow id",
          slot);
    if (slot < 0) return;
    CHECK(of_file_size64((uint32_t)slot) == (int64_t)OF_FIX_WAD_SIZE,
          "wad size64 mismatch");

    static const churn_file_t churn[] = {
        { "borrow.dat",    OF_FIX_SEED_BORROW,        OF_FIX_BORROW_SIZE },
        { "inst_only.dat", OF_FIX_SEED_INST,          OF_FIX_INST_SIZE },
        { "fill01.dat",    OF_FIX_SEED_S1_FILL + 1u,  OF_FIX_S1_FILL_SIZE },
        { "family01.dat",  OF_FIX_SEED_FAMILY_N + 1u, OF_FIX_FAMILY_N_SIZE },
        { "family02.dat",  OF_FIX_SEED_FAMILY_N + 2u, OF_FIX_FAMILY_N_SIZE },
        { "family03.dat",  OF_FIX_SEED_FAMILY_N + 3u, OF_FIX_FAMILY_N_SIZE },
    };
    stream_wad_loop(slot, 600, churn, 6);

    CHECK(find_slot_by_name("doommus.wad") == slot,
          "wad id lost/rebound after churn");
}

/* ====================================================================== */
/* By-name config writes (the "settings never saved" field bug)           */
/* ====================================================================== */

/* Deterministic text config of exactly `len` bytes: A-Z lines, no
 * 0x00/0xFF, '\n'-terminated.  buf[0] is derived from `salt` alone so a
 * salt of old_first_byte+1 is GUARANTEED to differ from what is stored
 * (repeat harness runs must flip the image hash). */
static void cfg_gen_text(uint8_t *buf, uint32_t len, uint32_t salt) {
    for (uint32_t i = 0; i < len; i++) {
        if (i + 1u == len || (i % 40u) == 39u)
            buf[i] = '\n';
        else
            buf[i] = (uint8_t)('A' + (of_fix_byte(salt, i) % 26u));
    }
    if (len > 1u)
        buf[0] = (uint8_t)('A' + (salt % 26u));
}

/* Model the kernel nv-fd fopen("...","wb") flow at the HAL layer the
 * harness compiles: O_TRUNC -> of_nvslot_set_size(0), sys_write ->
 * of_nvslot_write, sys_close (dirty) -> of_nvslot_set_size(final). */
static void cfg_model_save(int cfg, const uint8_t *text, uint32_t len) {
    CHECK(of_nvslot_set_size((uint32_t)cfg, 0) == 0, "cfg O_TRUNC rewrite");
    CHECK(of_nvslot_write((uint32_t)cfg, text, 0, len) == (int)len,
          "cfg write (%u bytes)", len);
    CHECK(of_nvslot_set_size((uint32_t)cfg, len) == 0, "cfg close/set_size");
}

/* Read back `total` bytes and verify: content[0,len) matches, tail
 * [len,total) is all zeros (stale-tail contract), logical size == len. */
static void cfg_verify(int cfg, const uint8_t *text, uint32_t len,
                       uint32_t total, const char *tag) {
    int64_t sz = of_file_size64((uint32_t)cfg);
    CHECK(sz == (int64_t)len, "%s: logical size %lld want %u",
          tag, (long long)sz, len);
    uint8_t *rb = malloc(total);
    CHECK(rb != NULL, "malloc cfg readback");
    if (!rb) return;
    int rc = of_nvslot_read((uint32_t)cfg, rb, 0, total);
    CHECK(rc == (int)total, "%s: readback rc=%d", tag, rc);
    if (rc == (int)total) {
        CHECK(memcmp(rb, text, len) == 0, "%s: content mismatch", tag);
        int tail_zero = 1;
        for (uint32_t i = len; i < total; i++)
            if (rb[i]) { tail_zero = 0; break; }
        CHECK(tail_zero, "%s: stale tail not zeroed", tag);
    }
    free(rb);
}

/* Legacy image: full settings round-trip on volume 0. */
static void test_config_by_name_legacy(void) {
    mount_image(0);

    /* Cache a READ handle for the same physical file under its dyn id
     * first — the FF_FS_LOCK trap the alias eviction must clear before
     * the writable config open. */
    int dyn = find_dyn_slot("doom.cfg");
    CHECK(dyn >= 0, "doom.cfg should enumerate as a dyn slot (legacy)");
    if (dyn >= 0) {
        uint8_t t[32];
        (void)of_file_read((uint32_t)dyn, 0, t, sizeof(t));
    }

    /* Resolution: the app passes the -config name with its own case. */
    int cfg = of_file_config_slot("Doom.cfg");
    CHECK(cfg >= (int)OF_FILE_CFG_SLOT_BASE &&
          cfg < (int)(OF_FILE_CFG_SLOT_BASE + OF_FILE_CFG_SLOT_COUNT),
          "config slot %d out of range", cfg);
    if (cfg < 0) return;
    CHECK(of_file_config_slot("doom.cfg") == cfg,
          "case-insensitive rebind must be stable");
    CHECK(of_file_config_slot("shared.cfg") == -1,
          "fixed-slot name must not bind a config slot");
    CHECK(of_file_config_slot("nosuch.cfg") == -1,
          "un-preallocated name must fail cleanly");
    CHECK(of_nvslot_is_supported((uint32_t)cfg) == 1,
          "config slot must be nvslot-capable");
    CHECK(of_nvslot_capacity((uint32_t)cfg) == NV_CAP,
          "config slot capacity");

    /* Salt from the stored first byte so repeat runs write fresh bytes. */
    uint8_t prev = 0;
    (void)of_nvslot_read((uint32_t)cfg, &prev, 0, 1);
    uint8_t longtext[1500], shorttext[700];
    cfg_gen_text(longtext, sizeof(longtext), (uint32_t)prev + 1u);
    cfg_gen_text(shorttext, sizeof(shorttext), (uint32_t)prev + 77u);

    /* Long write (> 1 sector, so a later stale tail spans sector 0). */
    cfg_model_save(cfg, longtext, sizeof(longtext));
    cfg_verify(cfg, longtext, sizeof(longtext), sizeof(longtext),
               "legacy long");

    /* Short rewrite after long: the stale-tail case. */
    cfg_model_save(cfg, shorttext, sizeof(shorttext));
    cfg_verify(cfg, shorttext, sizeof(shorttext), sizeof(longtext),
               "legacy short-after-long");

    /* Power-cycle durability + fresh binding. */
    power_cycle();
    cfg = of_file_config_slot("DOOM.CFG");
    CHECK(cfg >= (int)OF_FILE_CFG_SLOT_BASE, "rebind after power cycle");
    if (cfg >= 0)
        cfg_verify(cfg, shorttext, sizeof(shorttext), sizeof(longtext),
                   "legacy durability");

    /* Read-only mount: binding (reads) works, writes fail cleanly. */
    of_test_blockdev_close();
    if (of_test_blockdev_open(g_img_path, 1) != 0) {
        CHECK(0, "cannot reopen image read-only");
        return;
    }
    of_disk_init();
    of_file_init();
    of_save_init();
    cfg = of_file_config_slot("doom.cfg");
    CHECK(cfg >= (int)OF_FILE_CFG_SLOT_BASE, "bind on RO mount");
    if (cfg >= 0)
        CHECK(of_nvslot_write((uint32_t)cfg, shorttext, 0, 16) < 0,
              "config write on a read-only image must fail");
    power_cycle();
}

/* Field-failure repro for the "settings never persist on MiSTer" bug.
 * Two root causes were on the table; this test pins BOTH:
 *
 *   A  IMAGE GAP (the confirmed cause).  The old app-mode image builder
 *      preallocated only /config/shared.cfg + duke3d.cfg, never the app's
 *      own /config/doom.cfg.  With no backing file the OS refuses the
 *      by-name config open, so the app's M_SaveDefaults writes nothing and
 *      the settings silently vanish.  Fixed in the SDK builder
 *      (mkgame.sh preallocates each instance's /config/<name>.cfg slots).
 *
 *   B  FIRMWARE FLUSH (ruled out).  Hypothesis: config close() commits the
 *      logical size but not the DATA (like the historical save-close
 *      set_size-vs-flush bug).  NOT a bug on MiSTer: of_nvslot_write is
 *      write-through (f_write + f_sync — the SAME durable path saves use,
 *      confirmed working on hardware), so the payload is on the medium
 *      before close() runs.  close() only zeros the stale tail PAST the new
 *      logical size and never touches [0,size).
 *
 * The firmware-boundary shape of (A) is a clean of_file_config_slot()==-1
 * refusal; (B) is proven by round-tripping the exact fopen(wb)->fwrite->
 * fclose kernel sequence across a power cycle, including the app's
 * write-BOTH-main-and-extra-to-doom.cfg pattern (two O_TRUNC rewrites of the
 * same name in one session, with a read alias cached between them — the
 * FF_FS_LOCK dual-handle trap). */
static void test_config_field_failure_repro(void) {
    mount_image(0);

    /* ---- Cause A: an un-preallocated config name is refused cleanly --- */
    /* This is exactly what killed persistence on boot.vhd: no backing
     * file -> no bind -> the write path is a no-op, never a create and
     * never corruption.  If the builder gap ever regressed for doom.cfg,
     * the very next check would fail. */
    CHECK(of_file_config_slot("notmade.cfg") == -1,
          "un-preallocated config must refuse to bind (Cause A shape)");
    CHECK(of_file_config_slot("doom.cfg") >= (int)OF_FILE_CFG_SLOT_BASE,
          "preallocated doom.cfg MUST bind (image builder fix)");

    /* ---- Cause B: full open->write->close->power-cycle round trip ----- */
    /* Cache a READ handle for the same physical file under its dyn id, so
     * the first writable config open must evict the alias (FF_FS_LOCK). */
    int dyn = find_dyn_slot("doom.cfg");
    if (dyn >= 0) { uint8_t t[16]; (void)of_file_read((uint32_t)dyn, 0, t, sizeof(t)); }

    int cfg = of_file_config_slot("doom.cfg");
    if (cfg < 0) { CHECK(0, "doom.cfg config slot unavailable"); return; }

    uint8_t prev = 0;
    (void)of_nvslot_read((uint32_t)cfg, &prev, 0, 1);
    uint8_t maincfg[3000], extracfg[1200];
    cfg_gen_text(maincfg, sizeof(maincfg), (uint32_t)prev + 3u);
    cfg_gen_text(extracfg, sizeof(extracfg), (uint32_t)prev + 131u);

    /* App writes the MAIN config first: fopen("doom.cfg","wb") -> ... */
    cfg_model_save(cfg, maincfg, sizeof(maincfg));
    /* Write-through: visible on THIS mount, before any close/cycle. */
    cfg_verify(cfg, maincfg, sizeof(maincfg), sizeof(maincfg), "main pre-cycle");

    /* Then the EXTRA config to the SAME name (M_SetConfigFilenames forces
     * both to doom.cfg).  Re-cache the read alias first so the SECOND
     * writable open also has to clear the FF_FS_LOCK dual handle — a
     * write-then-write of one name in one session must not wedge. */
    if (dyn >= 0) { uint8_t t[16]; (void)of_file_read((uint32_t)dyn, 0, t, sizeof(t)); }
    cfg_model_save(cfg, extracfg, sizeof(extracfg));
    cfg_verify(cfg, extracfg, sizeof(extracfg), sizeof(maincfg),
               "extra after main (write-then-write same name)");

    /* Simulated power cycle: the bytes must already be on the medium.
     * This FAILS if the config close path ever stops flushing DATA (the
     * Cause B regression), catching the field failure a size-only close
     * would let through. */
    power_cycle();
    cfg = of_file_config_slot("doom.cfg");
    CHECK(cfg >= (int)OF_FILE_CFG_SLOT_BASE, "rebind doom.cfg after power cycle");
    if (cfg >= 0)
        cfg_verify(cfg, extracfg, sizeof(extracfg), sizeof(maincfg),
                   "config durability after power cycle");
}

/* Multi-vhd: pinning — config writes land ONLY on the S1 instance image
 * (or volume 0 without S1), never on a library volume. */
static void test_multi_config_by_name(void) {
    mount_multi();

    uint64_t h0 = file_hash(g_s0_path);
    uint64_t h2 = file_hash(g_s2_path);

    /* dyn alias read first (S1's /config/doom.cfg shadows S0's). */
    int dyn = find_slot_by_name("doom.cfg");
    CHECK(dyn >= 0, "doom.cfg unreachable by name on the multi mount");
    if (dyn >= 0) {
        uint8_t t[16];
        (void)of_file_read((uint32_t)dyn, 0, t, sizeof(t));
    }

    int cfg = of_file_config_slot("Doom.cfg");
    CHECK(cfg >= (int)OF_FILE_CFG_SLOT_BASE &&
          cfg < (int)(OF_FILE_CFG_SLOT_BASE + OF_FILE_CFG_SLOT_COUNT),
          "config slot %d out of range (multi)", cfg);
    if (cfg < 0) return;

    uint8_t prev = 0;
    (void)of_nvslot_read((uint32_t)cfg, &prev, 0, 1);
    uint8_t longtext[1400], shorttext[640];
    cfg_gen_text(longtext, sizeof(longtext), (uint32_t)prev + 1u);
    cfg_gen_text(shorttext, sizeof(shorttext), (uint32_t)prev + 91u);

    cfg_model_save(cfg, longtext, sizeof(longtext));
    cfg_model_save(cfg, shorttext, sizeof(shorttext));
    cfg_verify(cfg, shorttext, sizeof(shorttext), sizeof(longtext),
               "multi short-after-long");

    /* Durability across a full multi power cycle. */
    mount_multi();
    cfg = of_file_config_slot("doom.cfg");
    CHECK(cfg >= (int)OF_FILE_CFG_SLOT_BASE, "rebind after multi cycle");
    if (cfg >= 0)
        cfg_verify(cfg, shorttext, sizeof(shorttext), sizeof(longtext),
                   "multi durability");

    /* Pinning proof: the library images are byte-identical. */
    CHECK(file_hash(g_s0_path) == h0,
          "config write leaked to the S0 family library");
    CHECK(file_hash(g_s2_path) == h2,
          "config write leaked to the S2 borrow library");

    /* Placement proof: a LONE S1 mount reads the same config back. */
    mount_set(NULL, 0, g_s1_path, NULL);
    cfg = of_file_config_slot("doom.cfg");
    CHECK(cfg >= (int)OF_FILE_CFG_SLOT_BASE, "bind on lone-S1 mount");
    if (cfg >= 0)
        cfg_verify(cfg, shorttext, sizeof(shorttext), sizeof(longtext),
                   "lone-S1 placement");

    /* Without S1 (cap set, S0+S2): pinning falls back to volume 0. */
    mount_set(g_s0_path, 0, NULL, g_s2_path);
    uint64_t h2b = file_hash(g_s2_path);
    cfg = of_file_config_slot("doom.cfg");
    CHECK(cfg >= (int)OF_FILE_CFG_SLOT_BASE, "bind in the no-S1 world");
    if (cfg >= 0) {
        uint8_t p0 = 0;
        (void)of_nvslot_read((uint32_t)cfg, &p0, 0, 1);
        uint8_t s0text[80];
        cfg_gen_text(s0text, sizeof(s0text), (uint32_t)p0 + 1u);
        cfg_model_save(cfg, s0text, sizeof(s0text));
        cfg_verify(cfg, s0text, sizeof(s0text), sizeof(s0text),
                   "no-S1 volume-0 round-trip");
        CHECK(file_hash(g_s0_path) != h0,
              "no-S1 config write must land on volume 0 (S0)");
        CHECK(file_hash(g_s2_path) == h2b,
              "no-S1 config write leaked to the borrow library");
    }
}

/* ====================================================================== */
/* Gapped-volume boot (the field combo: S0 + S2 mounted, S1 a GAP)        */
/* ====================================================================== */

/* Mirror the REAL boot sequence (hal/hal.c order: of_disk_init ->
 * of_file_init -> of_save_init) against a volume set with a HOLE at
 * index 1 — the field state that blank-screened: S0 = boot image
 * (framework auto-mount), S2 = remembered doommusic.vhd with a large
 * /assets file, S1 absent.  Then walk the same probes the kernel issues
 * before handing control to the app: dir_probe_slots (ids 1..31 through
 * size64/flags/get_name), the os.ini config load, the app.elf load and
 * by-name resolution of the S2 wad. */
static void gapped_boot_body(const char *s0, const char *tag) {
    of_test_blockdev_close();
    of_test_blockdev_set_cap(1);
    if (of_test_blockdev_open_disk(0, s0, 0) != 0) exit(2);
    if (of_test_blockdev_open_disk(2, g_s2_path, 0) != 0) exit(2);

    /* Real init order — a hang/crash here IS the field repro. */
    of_disk_init();
    of_file_init();
    of_save_init();
    CHECK(of_disk_active() == &of_disk_bridge,
          "%s: bridge backend after gapped init", tag);

    /* dir_probe_slots model: every id the kernel walks at boot. */
    for (uint32_t id = 1; id < 32; id++) {
        char nm[64];
        (void)of_file_size64(id);
        (void)of_file_flags(id);
        (void)of_file_get_name(id, nm, sizeof(nm));
    }

    /* Config load (os.ini, slot 2) — resolves on volume 0 with vol 1 gapped. */
    uint8_t ini[64];
    CHECK(of_file_read(2, 0, ini, 16) == 0,
          "%s: os.ini read across the volume gap", tag);

    /* App load (slot 3) — search order 1 (gap) -> 2 -> 0. */
    CHECK(of_file_size64(3) > 0, "%s: app.elf size across the gap", tag);
    CHECK(of_file_read(3, 0, ini, sizeof(ini)) == 0,
          "%s: app.elf read across the gap", tag);

    /* The remembered S2 volume's big asset resolves and streams. */
    int slot = find_slot_by_name("DOOMMUS.WAD");
    CHECK(slot >= 0, "%s: wad unreachable with S1 gapped", tag);
    if (slot >= 0) {
        CHECK(of_file_size64((uint32_t)slot) == (int64_t)OF_FIX_WAD_SIZE,
              "%s: wad size across the gap", tag);
        uint8_t buf[4096];
        for (int i = 0; i < 4; i++) {
            uint32_t off = ((uint32_t)i * 700001u)
                           % (OF_FIX_WAD_SIZE - (uint32_t)sizeof(buf));
            int rc = of_file_read((uint32_t)slot, off, buf, sizeof(buf));
            CHECK(rc == 0 &&
                  pattern_ok(OF_FIX_SEED_WAD, buf, off, sizeof(buf)),
                  "%s: wad chunk off=%u rc=%d", tag, off, rc);
        }
    }

    /* nv pinning with the gap: no S1 -> volume 0 (legacy rule). */
    uint8_t w[16] = {9,8,7,6,5,4,3,2,1,0,1,2,3,4,5,6};
    CHECK(of_nvslot_write(15, w, 0, sizeof(w)) == (int)sizeof(w),
          "%s: nvslot write with S1 gapped", tag);
    uint8_t r[16];
    CHECK(of_nvslot_read(15, r, 0, sizeof(r)) == (int)sizeof(r) &&
          memcmp(r, w, sizeof(w)) == 0,
          "%s: nvslot readback with S1 gapped", tag);
}

static void test_gapped_boot(void) {
    /* Field-shaped S0: the sparse legacy image (os.ini/app.elf + a few
     * assets — closest to boot.vhd), and the family-library S0 variant. */
    gapped_boot_body(g_img_path, "gap/sparse-S0");
    gapped_boot_body(g_s0_path, "gap/library-S0");

    /* Boot-window transport policy: retries stay DISARMED through the
     * whole init/probe sequence above (a not-yet-responding HPS must fail
     * fast — the blank-screen regression), and the kernel's boot-complete
     * call arms them for post-boot operational reads. */
    CHECK(of_test_blockdev_retries_enabled == 0,
          "DS retries must stay off during init/probe");
    of_file_boot_complete();
    CHECK(of_test_blockdev_retries_enabled == 1,
          "of_file_boot_complete must arm the DS retry loop");

    /* Cold-boot variant: S2 appears only AFTER init ran (the remembered
     * mount restored late by Main) — the state machine must pick it up. */
    of_test_blockdev_close();
    of_test_blockdev_set_cap(1);
    if (of_test_blockdev_open_disk(0, g_img_path, 0) != 0) exit(2);
    of_disk_init();
    of_file_init();
    of_save_init();
    CHECK(find_slot_by_name("doommus.wad") >= 0,
          "late-S2: wad should resolve on the lone S0 (legacy copy)");
    if (of_test_blockdev_open_disk(2, g_s2_path, 0) != 0) exit(2);
    uint8_t buf[512];
    int slot = find_slot_by_name("borrow.dat");
    CHECK(slot >= 0, "late-S2: S2 file must resolve after late mount");
    if (slot >= 0)
        CHECK(of_file_read((uint32_t)slot, 0, buf, 128) == 0,
              "late-S2: S2 read after late mount");
}

/* ====================================================================== */

static void run_legacy_suite(void) {
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
    test_streaming_legacy();
    test_config_by_name_legacy();
    test_config_field_failure_repro();
}

/* ====================================================================== */
/* F-loaded app.elf staging (Phase-2 update-safe app delivery)            */
/* ====================================================================== */

/* Target-physical uncached address the firmware's elf_slot_read() memcpy's
 * from — computed identically to file.c's ELF_STAGE_UNCACHED so a host page
 * mapped here (MAP_FIXED) stands in for the HPS elf DMA target. */
#define ELF_STAGE_UNCACHED_HOST                                              \
    ((uintptr_t)OF_TARGET_SDRAM_UNCACHED_BASE + OF_TARGET_CRAM0_BRIDGE +     \
     OF_TARGET_CRAM0_ELF_STAGE_OFFSET)

/* HPS status block MMIO byte addresses (see hps_regs.h). */
#define HPS_STATUS_ADDR   0x49000000u
#define HPS_ELF_LEN_ADDR  0x49000024u
#define HPS_ELF_LOADED    (1u << 11)

static uint8_t elf_stage_byte(uint32_t off) {
    return (uint8_t)(off * 31u + 0x5Cu);   /* != of_fix_byte(SEED_APP, .) */
}

/* Prove the routing: with HPS_STATUS_ELF_LOADED set, a slot-3 (APP) read must
 * return the STAGED ELF bytes, not the FAT app.elf on the mounted image. */
static void test_elf_fload_staging(void) {
    mount_image(0);   /* slot 3 = FAT app.elf under the bridge backend */

    /* Baseline: with no elf staged, slot 3 resolves to the FAT app.elf. */
    CHECK(of_file_size64(3) == (int64_t)APP_ELF_SIZE,
          "slot 3 = FAT app.elf before elf F-load (size64=%lld)",
          (long long)of_file_size64(3));

    /* Map a host page at the staging address and stage a DISTINCT pattern. */
    const uint32_t elf_len = 4096u * 7u + 123u;   /* not a chunk multiple */
    void *stage = mmap((void *)ELF_STAGE_UNCACHED_HOST,
                       OF_TARGET_CRAM0_ELF_STAGE_SIZE,
                       PROT_READ | PROT_WRITE,
                       MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED, -1, 0);
    CHECK(stage == (void *)ELF_STAGE_UNCACHED_HOST,
          "mmap ELF staging @0x%lx (got %p)",
          (unsigned long)ELF_STAGE_UNCACHED_HOST, stage);
    if (stage != (void *)ELF_STAGE_UNCACHED_HOST) return;
    uint8_t *sb = stage;
    for (uint32_t i = 0; i < elf_len; i++)
        sb[i] = elf_stage_byte(i);

    /* Model the HPS status block: ELF loaded + byte count. */
    of_test_mmio_set(HPS_STATUS_ADDR, HPS_ELF_LOADED);
    of_test_mmio_set(HPS_ELF_LEN_ADDR, elf_len);

    /* New cross-target routing hooks: the kernel app loader
     * (kernel/main.c prepare_app_launch_ex) forces the resolved app slot to
     * APP_SLOT_ID when of_file_app_from_staging() reports an F-loaded ELF, so
     * load_app reads through elf_slot_read instead of the by-name vhd lookup;
     * of_file_app_staging_len() feeds the [elf_fload=..] boot diagnostic. */
    CHECK(of_file_app_from_staging() == 1,
          "of_file_app_from_staging()=1 when ELF_LOADED set");
    CHECK(of_file_app_staging_len() == elf_len,
          "of_file_app_staging_len()=%u want %u",
          (unsigned)of_file_app_staging_len(), elf_len);

    /* Model the kernel routing (prepare_app_launch_ex): os.ini "ELF=doom.elf"
     * resolves to some by-name/vhd slot, then the F-load override forces
     * APP_SLOT_ID.  Prove the routed slot is 3 and reading it returns the
     * STAGED bytes — the whole point of the fix (an unrouted by-name load would
     * read the FAT app.elf or fail the name lookup, never the staged engine). */
    {
        const uint32_t name_slot = 3u + 100u;  /* stands in for resolve_app_slot("doom.elf") */
        uint32_t routed = of_file_app_from_staging() ? 3u : name_slot;
        CHECK(routed == 3u,
              "ELF_LOADED routes app-load to APP_SLOT_ID (got %u)", routed);
        uint8_t rb[256];
        int rrc = of_file_read(routed, 0, rb, sizeof(rb));
        int rok = (rrc == 0);
        for (uint32_t j = 0; rok && j < sizeof(rb); j++)
            if (rb[j] != elf_stage_byte(j)) rok = 0;
        CHECK(rok, "routed app-load reads STAGED elf bytes (rc=%d)", rrc);
    }

    /* size64 now reports the staged length, not the FAT app.elf size. */
    CHECK(of_file_size64(3) == (int64_t)elf_len,
          "slot 3 size64 = staged elf len (%lld want %u)",
          (long long)of_file_size64(3), elf_len);

    /* Reads return the STAGED bytes (would be the FAT pattern if unrouted). */
    static const struct { uint32_t off, len; } cases[] = {
        { 0, 1 }, { 1, 3 }, { 0, 4096 }, { 4097, 2000 },
        { elf_len - 1, 1 }, { elf_len - 100, 100 },
    };
    uint8_t buf[8192];
    for (size_t i = 0; i < sizeof(cases)/sizeof(cases[0]); i++) {
        uint32_t off = cases[i].off, len = cases[i].len;
        memset(buf, 0xEE, sizeof(buf));
        int rc = of_file_read(3, off, buf, len);
        CHECK(rc == 0, "staged elf read off=%u len=%u rc=%d", off, len, rc);
        int ok = 1;
        for (uint32_t j = 0; j < len; j++)
            if (buf[j] != elf_stage_byte(off + j)) { ok = 0; break; }
        CHECK(ok, "staged elf bytes off=%u len=%u (served FAT instead?)",
              off, len);
    }

    /* Whole-file chunked read exercises the 32 KB DMA path through staging. */
    uint8_t *whole = malloc(elf_len);
    if (whole) {
        int rc = of_file_read_chunked(3, 0, whole, elf_len);
        int ok = (rc == 0);
        for (uint32_t j = 0; ok && j < elf_len; j++)
            if (whole[j] != elf_stage_byte(j)) ok = 0;
        CHECK(ok, "chunked staged elf read (rc=%d)", rc);
        free(whole);
    }

    /* Out-of-range guard: a read past the staged length must fail. */
    CHECK(of_file_read(3, elf_len - 4, buf, 64) < 0,
          "read past staged elf end must fail");

    /* Clear the modeled HPS state; slot 3 reverts to the FAT app.elf. */
    of_test_mmio_set(HPS_STATUS_ADDR, 0);
    of_test_mmio_set(HPS_ELF_LEN_ADDR, 0);
    munmap(stage, OF_TARGET_CRAM0_ELF_STAGE_SIZE);
    CHECK(of_file_size64(3) == (int64_t)APP_ELF_SIZE,
          "slot 3 reverts to FAT app.elf after elf F-load cleared");

    /* Hooks and routing revert with ELF_LOADED clear: the app loader keeps the
     * by-name/vhd slot (backward compat — a non-F-loaded core, and step 1 of
     * this bring-up, read app.elf from the mounted vhd exactly as before). */
    CHECK(of_file_app_from_staging() == 0,
          "of_file_app_from_staging()=0 when ELF_LOADED clear");
    CHECK(of_file_app_staging_len() == 0,
          "of_file_app_staging_len()=0 when ELF_LOADED clear");
    {
        const uint32_t name_slot = 3u + 100u;
        uint32_t routed = of_file_app_from_staging() ? 3u : name_slot;
        CHECK(routed == name_slot,
              "ELF_LOADED clear keeps the by-name/vhd slot (got %u)", routed);
    }
}

int main(int argc, char **argv) {
    if (argc != 5) {
        fprintf(stderr,
                "usage: %s <image.vhd> <s0.vhd> <s1.vhd> <s2.vhd>\n", argv[0]);
        return 2;
    }
    g_img_path = argv[1];
    g_s0_path  = argv[2];
    g_s1_path  = argv[3];
    g_s2_path  = argv[4];

    /* Legacy suite, old RTL (no multidisk capability). */
    g_cap = 0;
    run_legacy_suite();
    int legacy_cap0 = g_pass;

    /* Legacy suite again, new RTL with only S0 mounted — the contract's
     * "legacy mode" must be behavior-identical. */
    g_cap = 1;
    run_legacy_suite();
    int legacy_cap1 = g_pass - legacy_cap0;

    /* Multi-vhd suite. */
    test_multi_osini_prefers_s1();
    test_multi_appelf_falls_back_to_s0();
    test_multi_search_order();
    test_multi_s1_shadows_s0();
    test_multi_overflow_resolve();
    test_multi_writes_pin_to_s1();
    test_multi_s2_streaming();
    test_multi_config_by_name();
    test_multi_cap0_ignores_extra_disks();
    test_multi_no_s1_writes_to_volume0();
    test_multi_readonly_s0();
    test_multi_absent_s2();
    test_gapped_boot();
    test_late_mount_reprobe();
    test_instance_root_path_budget();
    test_elf_fload_staging();

    of_test_blockdev_close();

    printf("\n==== MiSTer FAT-stack host tests ====\n");
    printf("  legacy (cap=0): %d checks\n", legacy_cap0);
    printf("  legacy (cap=1): %d checks\n", legacy_cap1);
    printf("  passed: %d\n", g_pass);
    printf("  failed: %d\n", g_fail);
    printf("  result: %s\n", g_fail ? "FAIL" : "OK");
    return g_fail ? 1 : 0;
}
