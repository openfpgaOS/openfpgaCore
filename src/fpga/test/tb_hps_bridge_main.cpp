//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

//
// tb_hps_bridge_main.cpp — drives tb_hps_bridge.v.
//
// Plays the HPS (ioctl boot.rom streaming + the sd_rd/sd_wr sector
// protocol against a host "disk image") and the firmware (data-slot
// level strobes), and verifies SDRAM contents through the arbiter's
// M1 port.  See tb_hps_bridge.v for the wiring.
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

// ── disk image model ────────────────────────────────────────────────
static const uint32_t DISK_BYTES = 1u << 20;   // 1 MB
static uint8_t disk[DISK_BYTES];
static bool hps_answer_sd = true;              // false = timeout test

// sd servicing state
enum SdState { SD_IDLE, SD_DELAY, SD_RD_XFER, SD_WR_ADDR, SD_WR_SAMPLE, SD_END };
static SdState sd_state = SD_IDLE;
static bool     sd_is_read = false;
static uint32_t sd_cur_lba = 0;
static int      sd_delay = 0;
static int      sd_word = 0;
static int      sd_sub = 0;
static std::vector<uint32_t> sd_lba_log;

static void sd_service() {
    tb->sd_buff_wr = 0;

    switch (sd_state) {
    case SD_IDLE:
        tb->sd_ack = 0;
        if ((tb->sd_rd || tb->sd_wr) && hps_answer_sd) {
            sd_is_read = tb->sd_rd;
            sd_cur_lba = tb->sd_lba;
            sd_lba_log.push_back(sd_cur_lba);
            sd_delay = 25;          // HPS latency stand-in
            sd_state = SD_DELAY;
        }
        break;

    case SD_DELAY:
        if (--sd_delay == 0) {
            tb->sd_ack = 1;
            sd_word = 0;
            sd_sub = 0;
            sd_state = sd_is_read ? SD_RD_XFER : SD_WR_ADDR;
        }
        break;

    case SD_RD_XFER:
        // one 16-bit word every other cycle
        if (sd_sub == 0) {
            uint32_t off = sd_cur_lba * 512u + (uint32_t)sd_word * 2u;
            tb->sd_buff_addr = (uint8_t)sd_word;
            tb->sd_buff_dout = (uint16_t)(disk[off] | (disk[off + 1] << 8));
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
            disk[off]     = (uint8_t)(tb->sd_buff_din & 0xFF);
            disk[off + 1] = (uint8_t)(tb->sd_buff_din >> 8);
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

static void mount(uint64_t size, bool readonly) {
    tb->img_size = size;
    tb->img_readonly = readonly;
    tb->img_mounted = 1;
    tick();
    tb->img_mounted = 0;
    ticks(2);
}

// ── ioctl boot.rom streamer ─────────────────────────────────────────
static void ioctl_boot(const uint8_t *data, uint32_t len) {
    tb->ioctl_index = 0;
    tb->ioctl_download = 1;
    ticks(4);
    for (uint32_t a = 0; a < len; a += 2) {
        int guard = 0;
        while (tb->ioctl_wait && guard++ < 10000) tick();
        tb->ioctl_addr = a;
        tb->ioctl_dout = (uint16_t)(data[a] | ((a + 1 < len ? data[a + 1] : 0) << 8));
        tb->ioctl_wr = 1;
        tick();
        tb->ioctl_wr = 0;
        tick();
    }
    int guard = 0;
    while (tb->ioctl_wait && guard++ < 10000) tick();
    tb->ioctl_download = 0;
    ticks(200);     // final word drains, boot_loaded latches
}

// ── main ────────────────────────────────────────────────────────────
int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    tb = new Vtb_hps_bridge;

    // patterns
    for (uint32_t i = 0; i < DISK_BYTES; i++)
        disk[i] = (uint8_t)(i * 7u + (i >> 9));

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
        CHECK((tb->hps_status & 0x1) != 0, "status bit0 = boot loaded");
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
        mount(DISK_BYTES, false);
        CHECK((tb->hps_status & 0x2) != 0, "status bit1 = mounted");
    }

    printf("test_sector_read:\n");
    {
        sd_lba_log.clear();
        bool hs = false;
        int err = ds_op(false, 0, 1024, 0x00100000, 1024, &hs);
        CHECK(err == 0, "2-block read completes");
        CHECK(hs, "ack/done/release sequencing");
        CHECK(sd_lba_log.size() == 2 && sd_lba_log[0] == 2 && sd_lba_log[1] == 3,
              "sd_lba sequence 2,3");
        CHECK(sdram_check(0x00100000u, disk + 1024, 1024, "rd"),
              "disk bytes landed in SDRAM");
    }

    printf("test_request_validation:\n");
    {
        int err = ds_op(false, 0, 256, 0x00100000, 512, nullptr);
        CHECK(err == 2, "unaligned offset -> ERR_BADREQ");
        err = ds_op(false, 0, 0, 0x00100000, 100, nullptr);
        CHECK(err == 2, "non-multiple length -> ERR_BADREQ");
        err = ds_op(false, 3, 0, 0x00100000, 512, nullptr);
        CHECK(err == 2, "non-zero slot id -> ERR_BADREQ");
        err = ds_op(false, 0, DISK_BYTES - 512 + 512, 0x00100000, 512, nullptr);
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
        sd_lba_log.clear();
        bool hs = false;
        int err = ds_op(true, 0, 4096, 0x00200000, 1024, &hs);
        CHECK(err == 0, "2-block write completes");
        CHECK(hs, "write handshake well-formed");
        CHECK(sd_lba_log.size() == 2 && sd_lba_log[0] == 8 && sd_lba_log[1] == 9,
              "write sd_lba sequence 8,9");
        CHECK(memcmp(disk + 4096, src, sizeof(src)) == 0,
              "SDRAM bytes landed on disk");
    }

    printf("test_readonly:\n");
    {
        mount(DISK_BYTES, true);
        int err = ds_op(true, 0, 0, 0x00200000, 512, nullptr);
        CHECK(err == 3, "write to RO image -> ERR_READONLY");
        int err2 = ds_op(false, 0, 0, 0x00300000, 512, nullptr);
        CHECK(err2 == 0, "read from RO image still works");
        mount(DISK_BYTES, false);
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

    printf("\n=== Results: %d passed, %d failed ===\n", tests_passed, tests_failed);
    delete tb;
    return tests_failed ? 1 : 0;
}
