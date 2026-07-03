//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

//
// tb_hps_bridge_main.cpp — drives tb_hps_bridge.v.
//
// Plays the HPS (ioctl boot.rom streaming + the sd_rd/sd_wr sector
// protocol against three host "disk images" — hps_io VDNUM=3 semantics:
// one-hot sd_ack for the requesting disk, per-disk img_mounted pulses
// with scalar size/readonly valid only during the pulse) and the
// firmware (data-slot level strobes with DS_SLOT_ID = disk index), and
// verifies SDRAM contents through the arbiter's M1 port.  See
// tb_hps_bridge.v for the wiring.
//

#include <cstdio>
#include <cstdint>
#include <cstring>
#include <vector>
#include "Vtb_hps_bridge.h"
#include "verilated.h"

static Vtb_hps_bridge *tb;
static vluint64_t main_time = 0;

static int tests_passed = 0;
static int tests_failed = 0;

#define CHECK(cond, name) do { \
    if (cond) { printf("  OK  %s\n", name); tests_passed++; } \
    else      { printf("  FAIL %s\n", name); tests_failed++; } \
} while (0)

// ── disk image models (S0 / S1 / S2, distinct sizes + patterns) ─────
static const uint32_t DISK0_BYTES = 1u << 20;   // 1 MB   (S0 family/legacy)
static const uint32_t DISK1_BYTES = 1u << 19;   // 512 KB (S1 instance)
static const uint32_t DISK2_BYTES = 1u << 18;   // 256 KB (S2 borrow)
static uint8_t disk0[DISK0_BYTES];
static uint8_t disk1[DISK1_BYTES];
static uint8_t disk2[DISK2_BYTES];
static uint8_t *const disks[3] = { disk0, disk1, disk2 };
static bool hps_answer_sd = true;               // false = timeout test

// Protocol invariants, accumulated while servicing:
//   - the engine must never assert more than one sd_rd/sd_wr bit
//   - only the active op's sd_lba element may be nonzero
static int inv_multibit    = 0;
static int inv_lba_nonzero = 0;

static uint32_t lba_of(int n) {
    return n == 0 ? tb->sd_lba0 : n == 1 ? tb->sd_lba1 : tb->sd_lba2;
}

// sd servicing state
enum SdState { SD_IDLE, SD_DELAY, SD_RD_XFER, SD_WR_ADDR, SD_WR_SAMPLE, SD_END };
static SdState sd_state = SD_IDLE;
static bool     sd_is_read = false;
static int      sd_disk = 0;
static uint32_t sd_cur_lba = 0;
static int      sd_delay = 0;
static int      sd_word = 0;
static int      sd_sub = 0;

struct SdReq { int disk; uint32_t lba; };
static std::vector<SdReq> sd_log;

static void sd_service() {
    tb->sd_buff_wr = 0;

    switch (sd_state) {
    case SD_IDLE:
        tb->sd_ack = 0;
        if ((tb->sd_rd || tb->sd_wr) && hps_answer_sd) {
            uint32_t mask = (uint32_t)(tb->sd_rd | tb->sd_wr);
            if (mask & (mask - 1)) inv_multibit++;
            int n = (mask & 1) ? 0 : (mask & 2) ? 1 : 2;
            sd_disk = n;
            sd_is_read = (tb->sd_rd >> n) & 1;
            sd_cur_lba = lba_of(n);
            for (int i = 0; i < 3; i++)
                if (i != n && lba_of(i) != 0) inv_lba_nonzero++;
            sd_log.push_back({n, sd_cur_lba});
            sd_delay = 25;          // HPS latency stand-in
            sd_state = SD_DELAY;
        }
        break;

    case SD_DELAY:
        if (--sd_delay == 0) {
            tb->sd_ack = (uint8_t)(1u << sd_disk);   // one-hot, hps_io style
            sd_word = 0;
            sd_sub = 0;
            sd_state = sd_is_read ? SD_RD_XFER : SD_WR_ADDR;
        }
        break;

    case SD_RD_XFER:
        // one 16-bit word every other cycle
        if (sd_sub == 0) {
            uint32_t off = sd_cur_lba * 512u + (uint32_t)sd_word * 2u;
            const uint8_t *d = disks[sd_disk];
            tb->sd_buff_addr = (uint8_t)sd_word;
            tb->sd_buff_dout = (uint16_t)(d[off] | (d[off + 1] << 8));
            tb->sd_buff_wr = 1;
            sd_sub = 1;
        } else {
            sd_sub = 0;
            if (++sd_word == 256) { sd_delay = 4; sd_state = SD_END; }
        }
        break;

    case SD_WR_ADDR:
        tb->sd_buff_addr = (uint8_t)sd_word;
        sd_sub = 0;
        sd_state = SD_WR_SAMPLE;
        break;

    case SD_WR_SAMPLE:
        // bridge registers buf read + din: data valid 2 cycles after addr
        if (++sd_sub >= 3) {
            uint32_t off = sd_cur_lba * 512u + (uint32_t)sd_word * 2u;
            uint8_t *d = disks[sd_disk];
            d[off]     = (uint8_t)(tb->sd_buff_din & 0xFF);
            d[off + 1] = (uint8_t)(tb->sd_buff_din >> 8);
            if (++sd_word == 256) { sd_delay = 4; sd_state = SD_END; }
            else                  sd_state = SD_WR_ADDR;
        }
        break;

    case SD_END:
        if (--sd_delay == 0) {
            tb->sd_ack = 0;
            sd_state = SD_IDLE;
        }
        break;
    }
}

// ── clocking ────────────────────────────────────────────────────────
static void tick() {
    tb->clk = 0; tb->eval();
    sd_service();               // HPS reacts on the low phase
    tb->clk = 1; tb->eval();
    main_time++;
}

static void ticks(int n) { while (n--) tick(); }

// ── M1 AXI helpers ──────────────────────────────────────────────────
static bool axi_write(uint32_t addr, uint32_t data) {
    tb->m1_awvalid = 1; tb->m1_awaddr = addr; tb->m1_awlen = 0;
    tb->m1_wvalid = 1;  tb->m1_wdata = data;  tb->m1_wlast = 1;
    int aw_done = 0, w_done = 0;
    for (int i = 0; i < 5000 && !(aw_done && w_done); i++) {
        tick();
        if (tb->m1_awvalid && tb->m1_awready) { tb->m1_awvalid = 0; aw_done = 1; }
        if (tb->m1_wvalid && tb->m1_wready)   { tb->m1_wvalid = 0; tb->m1_wlast = 0; w_done = 1; }
    }
    if (!(aw_done && w_done)) return false;
    for (int i = 0; i < 5000; i++) {
        if (tb->m1_bvalid) { tick(); return true; }
        tick();
    }
    return false;
}

static bool axi_read(uint32_t addr, uint32_t *out) {
    tb->m1_arvalid = 1; tb->m1_araddr = addr; tb->m1_arlen = 0;
    tb->m1_rready = 1;
    int ar_done = 0;
    for (int i = 0; i < 5000; i++) {
        tick();
        if (tb->m1_arvalid && tb->m1_arready) { tb->m1_arvalid = 0; ar_done = 1; }
        if (ar_done && tb->m1_rvalid) {
            *out = tb->m1_rdata;
            tick();
            tb->m1_rready = 0;
            return true;
        }
    }
    return false;
}

static bool sdram_check(uint32_t base, const uint8_t *expect, uint32_t len,
                        const char *what) {
    for (uint32_t off = 0; off < len; off += 4) {
        uint32_t got = 0;
        if (!axi_read(base + off, &got)) {
            printf("    [%s] AXI read timeout @0x%08x\n", what, base + off);
            return false;
        }
        uint32_t want;
        memcpy(&want, expect + off, 4);
        if (got != want) {
            printf("    [%s] mismatch @0x%08x got=%08x want=%08x\n",
                   what, base + off, got, want);
            return false;
        }
    }
    return true;
}

// ── dataslot op driver (mimics axi_periph_slave's level strobes) ────
static int ds_op(bool is_write, uint16_t id, uint32_t offset,
                 uint32_t bridgeaddr, uint32_t length, bool *handshake_ok) {
    tb->ds_id = id;
    tb->ds_offset = offset;
    tb->ds_bridgeaddr = bridgeaddr;
    tb->ds_length = length;
    if (is_write) tb->ds_write = 1; else tb->ds_read = 1;

    bool ok = true;
    int i;
    // ack must rise before done
    for (i = 0; i < 2000000 && !tb->ds_ack; i++) {
        if (tb->ds_done) ok = false;
        tick();
    }
    if (i == 2000000) { ok = false; }
    for (i = 0; i < 2000000 && !tb->ds_done; i++) tick();
    if (i == 2000000) ok = false;
    int err = tb->ds_err;

    // periph captures DONE and drops the strobe
    tb->ds_read = 0;
    tb->ds_write = 0;
    for (i = 0; i < 1000 && (tb->ds_ack || tb->ds_done); i++) tick();
    if (tb->ds_ack || tb->ds_done) ok = false;     // must release
    for (i = 0; i < 1000 && !tb->wr_idle; i++) tick();
    if (!tb->wr_idle) ok = false;

    if (handshake_ok) *handshake_ok = ok;
    ticks(4);
    return err;
}

// Per-disk mount pulse: mask bit = disk, size/readonly scalar and valid
// only during the pulse (hps_io contract).  size 0 = unmount.
static void mount(int disk_idx, uint64_t size, bool readonly) {
    tb->img_size = size;
    tb->img_readonly = readonly;
    tb->img_mounted = (uint8_t)(1u << disk_idx);
    tick();
    tb->img_mounted = 0;
    // scalar size/readonly go stale immediately after the pulse — the
    // bridge must have latched per disk
    tb->img_size = 0xDEADDEADDEADDEADull;
    tb->img_readonly = !readonly;
    ticks(2);
}

// hps_status bit positions (see hps_bridge.v)
static const uint32_t ST_BOOT      = 1u << 0;
static const uint32_t ST_MOUNT0    = 1u << 1;
static const uint32_t ST_RO0       = 1u << 4;
static const uint32_t ST_CAP       = 1u << 5;   // MULTIDISK_CAP
static const uint32_t ST_MOUNT1    = 1u << 6;
static const uint32_t ST_MOUNT2    = 1u << 7;
static const uint32_t ST_RO1       = 1u << 8;
static const uint32_t ST_RO2       = 1u << 9;

// ── ioctl boot.rom streamer ─────────────────────────────────────────
// violate_wait=true models an HPS that observes ioctl_wait one word
// late: each word is sent immediately even if wait just asserted — the
// bridge's one-entry skid must absorb it without dropping data.
static void ioctl_boot(const uint8_t *data, uint32_t len,
                       bool violate_wait = false) {
    tb->ioctl_index = 0;
    tb->ioctl_download = 1;
    ticks(4);
    bool violated = false;
    for (uint32_t a = 0; a < len; a += 2) {
        if (!violate_wait || violated) {
            int guard = 0;
            while (tb->ioctl_wait && guard++ < 10000) tick();
            violated = false;
        } else if (tb->ioctl_wait) {
            violated = true;    // send THIS word despite wait (depth 1)
        }
        tb->ioctl_dout = (uint16_t)(data[a] | ((a + 1 < len ? data[a + 1] : 0) << 8));
        tb->ioctl_wr = 1;
        tick();
        tb->ioctl_wr = 0;
        tick();
    }
    int guard = 0;
    while (tb->ioctl_wait && guard++ < 10000) tick();
    tb->ioctl_download = 0;
    ticks(400);     // final word/skid/half drains, boot_loaded latches
}

// ── main ────────────────────────────────────────────────────────────
int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    tb = new Vtb_hps_bridge;

    // distinct patterns per disk so cross-routing is detectable
    for (uint32_t i = 0; i < DISK0_BYTES; i++) disk0[i] = (uint8_t)(i * 7u + (i >> 9));
    for (uint32_t i = 0; i < DISK1_BYTES; i++) disk1[i] = (uint8_t)(i * 13u + 0x40 + (i >> 10));
    for (uint32_t i = 0; i < DISK2_BYTES; i++) disk2[i] = (uint8_t)(i * 5u + 0x9C + (i >> 8));

    tb->reset_n = 0;
    tb->sd_ack = 0;
    tb->m1_rready = 0;
    ticks(10);
    tb->reset_n = 1;
    ticks(10);

    printf("test_boot_ioctl:\n");
    {
        static uint8_t boot[2048];
        for (uint32_t i = 0; i < sizeof(boot); i++) boot[i] = (uint8_t)(i ^ 0xA5);
        ioctl_boot(boot, sizeof(boot));
        CHECK(tb->boot_loaded, "boot-loaded flag");
        CHECK(tb->hps_boot_len == sizeof(boot), "boot length latched");
        CHECK((tb->hps_status & ST_BOOT) != 0, "status bit0 = boot loaded");
        CHECK(sdram_check(0x03300000u, boot, sizeof(boot), "boot"),
              "boot.rom bytes in staging SDRAM");
    }

    printf("test_unmounted_read_errors:\n");
    {
        bool hs = false;
        int err = ds_op(false, 0, 0, 0x00100000, 512, &hs);
        CHECK(err == 1, "read before mount -> ERR_NOMOUNT");
        CHECK(hs, "handshake well-formed on error path");
    }

    printf("test_mount_status:\n");
    {
        mount(0, DISK0_BYTES, false);
        CHECK((tb->hps_status & ST_MOUNT0) != 0, "status bit1 = disk0 mounted");
        CHECK((tb->hps_status & ST_CAP) != 0, "status bit5 = MULTIDISK_CAP");
        CHECK((tb->hps_status & (ST_MOUNT1 | ST_MOUNT2)) == 0,
              "disk1/disk2 report unmounted");
        CHECK(tb->hps_img_size == DISK0_BYTES, "IMG0 size latched");
        CHECK(tb->hps_img1_size == 0 && tb->hps_img2_size == 0,
              "IMG1/IMG2 sizes still 0");
    }

    printf("test_sector_read:\n");
    {
        sd_log.clear();
        bool hs = false;
        int err = ds_op(false, 0, 1024, 0x00100000, 1024, &hs);
        CHECK(err == 0, "2-block read completes");
        CHECK(hs, "ack/done/release sequencing");
        CHECK(sd_log.size() == 2 &&
              sd_log[0].disk == 0 && sd_log[0].lba == 2 &&
              sd_log[1].disk == 0 && sd_log[1].lba == 3,
              "sd_lba sequence 2,3 on disk 0");
        CHECK(sdram_check(0x00100000u, disk0 + 1024, 1024, "rd"),
              "disk bytes landed in SDRAM");
    }

    printf("test_request_validation:\n");
    {
        int err = ds_op(false, 0, 256, 0x00100000, 512, nullptr);
        CHECK(err == 2, "unaligned offset -> ERR_BADREQ");
        err = ds_op(false, 0, 0, 0x00100000, 100, nullptr);
        CHECK(err == 2, "non-multiple length -> ERR_BADREQ");
        err = ds_op(false, 3, 0, 0x00100000, 512, nullptr);
        CHECK(err == 2, "slot id 3 -> ERR_BADREQ");
        err = ds_op(false, 0x0010, 0, 0x00100000, 512, nullptr);
        CHECK(err == 2, "slot id bits 15:4 set (0x0010) -> ERR_BADREQ");
        err = ds_op(false, 0x8001, 0, 0x00100000, 512, nullptr);
        CHECK(err == 2, "slot id 0x8001 -> ERR_BADREQ");
        err = ds_op(true, 0xFFFF, 0, 0x00100000, 512, nullptr);
        CHECK(err == 2, "slot id 0xFFFF write -> ERR_BADREQ");
        err = ds_op(false, 0, DISK0_BYTES - 512 + 512, 0x00100000, 512, nullptr);
        CHECK(err == 2, "offset+len beyond image -> ERR_BADREQ");
    }

    printf("test_sector_write:\n");
    {
        static uint8_t src[1024];
        for (uint32_t i = 0; i < sizeof(src); i++) src[i] = (uint8_t)(0x33 + i * 11u);
        for (uint32_t i = 0; i < sizeof(src); i += 4) {
            uint32_t w;
            memcpy(&w, src + i, 4);
            if (!axi_write(0x00200000u + i, w)) { printf("    preload write timeout\n"); break; }
        }
        sd_log.clear();
        bool hs = false;
        int err = ds_op(true, 0, 4096, 0x00200000, 1024, &hs);
        CHECK(err == 0, "2-block write completes");
        CHECK(hs, "write handshake well-formed");
        CHECK(sd_log.size() == 2 &&
              sd_log[0].disk == 0 && sd_log[0].lba == 8 &&
              sd_log[1].disk == 0 && sd_log[1].lba == 9,
              "write sd_lba sequence 8,9 on disk 0");
        CHECK(memcmp(disk0 + 4096, src, sizeof(src)) == 0,
              "SDRAM bytes landed on disk");
    }

    printf("test_readonly:\n");
    {
        mount(0, DISK0_BYTES, true);
        int err = ds_op(true, 0, 0, 0x00200000, 512, nullptr);
        CHECK(err == 3, "write to RO image -> ERR_READONLY");
        int err2 = ds_op(false, 0, 0, 0x00300000, 512, nullptr);
        CHECK(err2 == 0, "read from RO image still works");
        mount(0, DISK0_BYTES, false);
    }

    printf("test_openfile_fails_fast:\n");
    {
        tb->ds_openfile = 1;
        int i;
        for (i = 0; i < 1000 && !tb->ds_done; i++) tick();
        int err = tb->ds_err;
        tb->ds_openfile = 0;
        for (i = 0; i < 1000 && (tb->ds_ack || tb->ds_done); i++) tick();
        CHECK(err == 7, "OPENFILE -> ERR_NOSUPPORT");
        CHECK(!tb->ds_ack && !tb->ds_done, "handshake releases");
    }

    printf("test_sd_timeout:\n");
    {
        hps_answer_sd = false;
        int err = ds_op(false, 0, 0, 0x00100000, 512, nullptr);
        CHECK(err == 5, "unanswered sd_rd -> ERR_TIMEOUT");
        hps_answer_sd = true;
        // engine must recover for a normal op afterwards
        int err2 = ds_op(false, 0, 512, 0x00100000, 512, nullptr);
        CHECK(err2 == 0, "engine recovers after timeout");
    }

    printf("test_boot_redownload:\n");
    {
        static uint8_t boot2[1024];
        for (uint32_t i = 0; i < sizeof(boot2); i++) boot2[i] = (uint8_t)(i + 1);
        ioctl_boot(boot2, sizeof(boot2));
        CHECK(tb->boot_loaded && tb->hps_boot_len == sizeof(boot2),
              "re-download updates length");
        CHECK(sdram_check(0x03300000u, boot2, sizeof(boot2), "boot2"),
              "re-downloaded bytes in staging");
    }

    printf("test_boot_wait_violation:\n");
    {
        // HPS that reacts to ioctl_wait one word late — skid must absorb.
        static uint8_t bootv[4096];
        for (uint32_t i = 0; i < sizeof(bootv); i++) bootv[i] = (uint8_t)(i * 13 + 7);
        ioctl_boot(bootv, sizeof(bootv), true);
        CHECK(tb->boot_loaded && tb->hps_boot_len == sizeof(bootv),
              "length exact under wait violations");
        CHECK(sdram_check(0x03300000u, bootv, sizeof(bootv), "bootv"),
              "no words dropped under wait violations");
    }

    printf("test_boot_odd_length:\n");
    {
        // Image with a dangling 16-bit half (len % 4 == 2).
        static uint8_t booto[514];
        for (uint32_t i = 0; i < sizeof(booto); i++) booto[i] = (uint8_t)(i ^ 0x5C);
        ioctl_boot(booto, sizeof(booto));
        CHECK(tb->boot_loaded && tb->hps_boot_len == sizeof(booto),
              "odd length latched");
        CHECK(sdram_check(0x03300000u, booto, 512, "booto-body"),
              "body bytes in staging");
        uint32_t last = 0;
        CHECK(axi_read(0x03300000u + 512, &last) &&
              (last & 0xFFFF) == (uint32_t)(booto[512] | (booto[513] << 8)),
              "dangling half flushed (zero-padded)");
    }

    // ════════════════ multi-vhd (VDNUM=3) coverage ════════════════

    printf("test_multidisk_mount:\n");
    {
        // NOMOUNT on a never-mounted index before anything is latched
        int err = ds_op(false, 1, 0, 0x00100000, 512, nullptr);
        CHECK(err == 1, "op on unmounted S1 -> ERR_NOMOUNT");
        err = ds_op(true, 2, 0, 0x00100000, 512, nullptr);
        CHECK(err == 1, "op on unmounted S2 -> ERR_NOMOUNT");

        mount(1, DISK1_BYTES, false);
        mount(2, DISK2_BYTES, true);        // S2 readonly
        uint32_t st = tb->hps_status;
        CHECK((st & ST_MOUNT1) && (st & ST_MOUNT2), "bits 6/7 = disk1/2 mounted");
        CHECK(!(st & ST_RO1) && (st & ST_RO2), "bits 8/9 = disk1 RW, disk2 RO");
        CHECK((st & ST_MOUNT0) && !(st & ST_RO0),
              "disk0 bits 1/4 unchanged by S1/S2 mounts");
        CHECK(tb->hps_img_size == DISK0_BYTES &&
              tb->hps_img1_size == DISK1_BYTES &&
              tb->hps_img2_size == DISK2_BYTES,
              "three per-disk sizes latched independently");
    }

    printf("test_multidisk_read_routing:\n");
    {
        sd_log.clear();
        bool hs = false;
        int err = ds_op(false, 1, 2048, 0x00400000, 1024, &hs);
        CHECK(err == 0 && hs, "S1 2-block read completes");
        CHECK(sd_log.size() == 2 &&
              sd_log[0].disk == 1 && sd_log[0].lba == 4 &&
              sd_log[1].disk == 1 && sd_log[1].lba == 5,
              "sd_rd bit 1 asserted with lba 4,5");
        CHECK(sdram_check(0x00400000u, disk1 + 2048, 1024, "rd1"),
              "disk1 bytes landed in SDRAM");

        sd_log.clear();
        err = ds_op(false, 2, 512, 0x00410000, 512, &hs);
        CHECK(err == 0 && hs, "S2 1-block read completes");
        CHECK(sd_log.size() == 1 &&
              sd_log[0].disk == 2 && sd_log[0].lba == 1,
              "sd_rd bit 2 asserted with lba 1");
        CHECK(sdram_check(0x00410000u, disk2 + 512, 512, "rd2"),
              "disk2 bytes landed in SDRAM");

        // same offset back on S0 must fetch the *disk0* pattern
        sd_log.clear();
        err = ds_op(false, 0, 2048, 0x00420000, 512, &hs);
        CHECK(err == 0 && sd_log.size() == 1 && sd_log[0].disk == 0 &&
              sdram_check(0x00420000u, disk0 + 2048, 512, "rd0"),
              "S0 read at same offset returns disk0 pattern");
    }

    printf("test_multidisk_write_routing:\n");
    {
        static uint8_t src[1024];
        for (uint32_t i = 0; i < sizeof(src); i++) src[i] = (uint8_t)(0x77 ^ (i * 3u));
        for (uint32_t i = 0; i < sizeof(src); i += 4) {
            uint32_t w;
            memcpy(&w, src + i, 4);
            if (!axi_write(0x00500000u + i, w)) { printf("    preload write timeout\n"); break; }
        }
        uint8_t before0[1024], before2[1024];
        memcpy(before0, disk0 + 8192, sizeof(before0));
        memcpy(before2, disk2 + 8192, sizeof(before2));

        sd_log.clear();
        bool hs = false;
        int err = ds_op(true, 1, 8192, 0x00500000, 1024, &hs);
        CHECK(err == 0 && hs, "S1 2-block write completes");
        CHECK(sd_log.size() == 2 &&
              sd_log[0].disk == 1 && sd_log[0].lba == 16 &&
              sd_log[1].disk == 1 && sd_log[1].lba == 17,
              "sd_wr bit 1 asserted with lba 16,17");
        CHECK(memcmp(disk1 + 8192, src, sizeof(src)) == 0,
              "bytes landed on disk1");
        CHECK(memcmp(disk0 + 8192, before0, sizeof(before0)) == 0 &&
              memcmp(disk2 + 8192, before2, sizeof(before2)) == 0,
              "disk0/disk2 untouched at the same offset");
    }

    printf("test_per_disk_size_validation:\n");
    {
        // in range for S0 (1MB) but beyond S2 (256KB)
        int err = ds_op(false, 2, DISK2_BYTES, 0x00100000, 512, nullptr);
        CHECK(err == 2, "read past disk2 size -> ERR_BADREQ");
        err = ds_op(false, 0, DISK2_BYTES, 0x00100000, 512, nullptr);
        CHECK(err == 0, "same offset on disk0 -> OK");
        err = ds_op(false, 1, DISK1_BYTES - 512, 0x00100000, 512, nullptr);
        CHECK(err == 0, "read up to disk1's last block -> OK");
        err = ds_op(false, 1, DISK1_BYTES - 512, 0x00100000, 1024, nullptr);
        CHECK(err == 2, "read crossing disk1's end -> ERR_BADREQ");
    }

    printf("test_per_disk_readonly:\n");
    {
        int err = ds_op(true, 2, 0, 0x00500000, 512, nullptr);
        CHECK(err == 3, "write to RO disk2 -> ERR_READONLY");
        err = ds_op(true, 1, 0, 0x00500000, 512, nullptr);
        CHECK(err == 0, "write to RW disk1 unaffected");
        err = ds_op(false, 2, 0, 0x00510000, 512, nullptr);
        CHECK(err == 0, "read from RO disk2 still works");
    }

    printf("test_unmount_one_disk:\n");
    {
        mount(1, 0, false);                 // size-0 pulse = unmount S1
        uint32_t st = tb->hps_status;
        CHECK(!(st & ST_MOUNT1), "disk1 reports unmounted");
        CHECK((st & ST_MOUNT0) && (st & ST_MOUNT2),
              "disk0/disk2 mount state untouched");
        CHECK(tb->hps_img1_size == 0, "IMG1 size cleared");
        CHECK(tb->hps_img_size == DISK0_BYTES && tb->hps_img2_size == DISK2_BYTES,
              "IMG0/IMG2 sizes untouched");
        int err = ds_op(false, 1, 0, 0x00100000, 512, nullptr);
        CHECK(err == 1, "op on unmounted S1 -> ERR_NOMOUNT");
        err = ds_op(false, 2, 0, 0x00100000, 512, nullptr);
        CHECK(err == 0, "S2 still serviceable");
        err = ds_op(false, 0, 0, 0x00100000, 512, nullptr);
        CHECK(err == 0, "S0 still serviceable");
        mount(1, DISK1_BYTES, false);       // remount
        err = ds_op(false, 1, 0, 0x00100000, 512, nullptr);
        CHECK(err == 0, "S1 serviceable again after remount");
    }

    printf("test_legacy_slot0:\n");
    {
        // Old firmware writes DS_SLOT_ID = 0 always: with all three
        // disks mounted, id 0 must keep exact single-disk behavior.
        sd_log.clear();
        bool hs = false;
        int err = ds_op(false, 0, 1024, 0x00600000, 1024, &hs);
        CHECK(err == 0 && hs && sd_log.size() == 2 &&
              sd_log[0].disk == 0 && sd_log[0].lba == 2 &&
              sd_log[1].disk == 0 && sd_log[1].lba == 3 &&
              sdram_check(0x00600000u, disk0 + 1024, 1024, "legacy"),
              "id=0 routes to disk0 exactly as single-disk RTL");
        CHECK((tb->hps_status & 0x1F) == (ST_BOOT | ST_MOUNT0),
              "hps_status bits 4:0 keep legacy disk-0 encoding");
    }

    printf("test_sd_protocol_invariants:\n");
    {
        CHECK(inv_multibit == 0, "sd_rd/sd_wr always one-hot");
        CHECK(inv_lba_nonzero == 0, "inactive sd_lba elements held at 0");
    }

    printf("\n=== Results: %d passed, %d failed ===\n", tests_passed, tests_failed);
    delete tb;
    return tests_failed ? 1 : 0;
}
