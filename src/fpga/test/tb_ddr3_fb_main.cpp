//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------
//
// Scenarios for the MiSTer direct-framebuffer pipeline:
//   1. 8bpp frame copy: pattern-exact DDR3 slot contents, correct FB_*
//      publish, slot alternation across frames.
//   2. Copy under scanout contention (priority port firing mid-copy),
//      both clients pattern-exact.
//   3. RGB565 frame: format code + geometry.
//   4. Mode gating: 4bpp and RGBA5551 drop FB_EN; disable drops FB_EN.
//   5. Palette: 256-entry commit streamed to FB_PAL_* after vblank.
//

#include "Vtb_ddr3_fb.h"
#include "verilated.h"
#include <cstdio>
#include <cstdint>

static Vtb_ddr3_fb* tb;
static vluint64_t ticks = 0;
static int pass = 0, fail = 0;

static void step() {
    tb->clk = 0; tb->eval();
    tb->clk = 1; tb->eval();
    ticks++;
}

static void steps(int n) { while (n--) step(); }

#define CHECK(cond, name) do { \
    if (cond) { pass++; } \
    else { fail++; printf("FAIL: %s (line %d)\n", name, __LINE__); } \
} while (0)

// must match tb_ddr3_fb.v::sdram_word
static uint32_t sdram_word(uint32_t byte_addr) {
    return ((byte_addr >> 2) * 0x9E3779B9u) ^ 0xC0FFEE00u;
}

static const uint32_t SLOT0 = 0x22000000u;
static const uint32_t SLOT1 = 0x22800000u;
static const uint32_t WIN_BASE_W64 = SLOT0 >> 3;

static uint64_t ddr_read(uint32_t byte_addr) {
    tb->ddr_probe_idx = (byte_addr >> 3) - WIN_BASE_W64;
    tb->eval();
    return tb->ddr_probe_data;
}

// pulse one input-frame vsync and wait for a copy to complete
static void frame_vsync(int settle_cycles = 40000) {
    tb->crt_vs = 1; steps(20);
    tb->crt_vs = 0;
    steps(settle_cycles);
}

// verify a copied frame: stride*height bytes linear from src_byte in SDRAM
// pattern to ddr_base in the capture window
static bool frame_matches(uint32_t src_byte, uint32_t ddr_base,
                          int stride, int height) {
    for (int off = 0; off < stride * height; off += 8) {
        uint64_t expect = (uint64_t)sdram_word(src_byte + off) |
                          ((uint64_t)sdram_word(src_byte + off + 4) << 32);
        if (ddr_read(ddr_base + off) != expect) {
            printf("  frame mismatch at +0x%x: got %016llx want %016llx\n",
                   off, (unsigned long long)ddr_read(ddr_base + off),
                   (unsigned long long)expect);
            return false;
        }
    }
    return true;
}

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    tb = new Vtb_ddr3_fb;

    // reset
    tb->reset_n = 0;
    tb->enable = 0;
    tb->crt_vs = 0;
    tb->fb_vbl = 0;
    tb->pal_wr = 0;
    tb->pal_commit = 0;
    tb->vid_kick = 0;
    tb->ddr_probe_idx = 0;
    tb->pal_probe_idx = 0;
    steps(10);
    tb->reset_n = 1;
    steps(10);

    // ---- scenario 1: 8bpp 64x32, stride 64 ------------------------------
    const uint32_t SRC_HALF = 0x0004000;   // halfword addr -> byte 0x8000
    const uint32_t SRC_BYTE = SRC_HALF << 1;
    tb->fb_display_addr = SRC_HALF;
    tb->color_mode = 0;                    // 8bpp indexed
    tb->fb_width = 64;
    tb->fb_height = 32;
    tb->fb_stride = 64;
    tb->enable = 1;
    steps(10);

    CHECK(tb->FB_EN == 0, "FB_EN low before first copy");

    frame_vsync(8000);                     // 64*32/4 = 512 words + overhead
    CHECK(tb->FB_EN == 1, "FB_EN set after first copy");
    CHECK(tb->FB_BASE == SLOT0, "first frame lands in slot 0");
    CHECK(tb->FB_FORMAT == 0x03, "8bpp palette format code");
    CHECK(tb->FB_WIDTH == 64 && tb->FB_HEIGHT == 32, "geometry published");
    CHECK(tb->FB_STRIDE == 64, "stride published");
    CHECK(frame_matches(SRC_BYTE, SLOT0, 64, 32), "slot0 frame pattern exact");
    CHECK(tb->ddr_proto_err == 0, "no Avalon protocol violations");

    // ---- scenario 1b: slot alternation ----------------------------------
    frame_vsync(8000);
    CHECK(tb->FB_BASE == SLOT1, "second frame lands in slot 1");
    CHECK(frame_matches(SRC_BYTE, SLOT1, 64, 32), "slot1 frame pattern exact");

    // ---- scenario 2: copy under scanout contention ----------------------
    // Fire priority-port bursts while a copy runs; both must stay exact.
    const uint32_t VID_HALF = 0x0100000;
    tb->crt_vs = 1; steps(20); tb->crt_vs = 0;
    int kicks = 0;
    for (int i = 0; i < 6000 && kicks < 12; i++) {
        step();
        if ((i % 400) == 100) {
            tb->vid_kick_addr = VID_HALF + kicks * 0x100;
            tb->vid_kick_len = 40;
            tb->vid_kick = 1;
            step();
            tb->vid_kick = 0;
            kicks++;
        }
    }
    steps(6000);
    CHECK(tb->vid_done_cnt == kicks, "all scanout bursts served under contention");
    CHECK(tb->vid_err_cnt == 0, "scanout data exact under contention");
    CHECK(tb->FB_BASE == SLOT0, "third frame lands in slot 0 again");
    CHECK(frame_matches(SRC_BYTE, SLOT0, 64, 32), "contended copy pattern exact");
    CHECK(tb->ddr_proto_err == 0, "no protocol violations under contention");

    // ---- scenario 3: RGB565 ---------------------------------------------
    tb->color_mode = 3;
    tb->fb_stride = 128;                   // 64 px * 2 B
    steps(10);
    frame_vsync(12000);
    CHECK(tb->FB_EN == 1, "FB_EN stays up in RGB565");
    CHECK(tb->FB_FORMAT == 0x04, "565 format code");
    CHECK(tb->FB_STRIDE == 128, "565 stride published");
    CHECK(frame_matches(SRC_BYTE, SLOT1, 128, 32), "565 frame pattern exact");

    // ---- scenario 4: unsupported modes gate FB_EN -----------------------
    tb->color_mode = 1;                    // 4bpp
    steps(10);
    frame_vsync(2000);
    CHECK(tb->FB_EN == 0, "4bpp drops FB_EN");
    tb->color_mode = 5;                    // RGBA5551 (alpha in bit0)
    steps(10);
    frame_vsync(2000);
    CHECK(tb->FB_EN == 0, "RGBA5551 drops FB_EN");

    tb->color_mode = 0;
    tb->fb_stride = 64;
    steps(10);
    frame_vsync(8000);
    CHECK(tb->FB_EN == 1, "8bpp re-enables");
    tb->enable = 0;
    steps(20);
    CHECK(tb->FB_EN == 0, "OSD disable drops FB_EN");
    tb->enable = 1;
    steps(10);

    // ---- scenario 5: palette stream -------------------------------------
    for (int i = 0; i < 256; i++) {
        tb->pal_wr = 1;
        tb->pal_addr = i;
        tb->pal_data = (i << 16) | ((255 - i) << 8) | (i ^ 0x5A);
        step();
    }
    tb->pal_wr = 0;
    tb->pal_commit = 1; step(); tb->pal_commit = 0;
    steps(50);
    CHECK(tb->pal_wr_cnt == 0, "palette held until vblank");
    tb->fb_vbl = 1; steps(400); tb->fb_vbl = 0; steps(50);
    CHECK(tb->pal_wr_cnt == 256, "256 palette entries streamed at vblank");
    bool pal_ok = true;
    for (int i = 0; i < 256 && pal_ok; i++) {
        tb->pal_probe_idx = i; tb->eval();
        uint32_t want = ((unsigned)i << 16) | ((unsigned)(255 - i) << 8) | (i ^ 0x5A);
        if (tb->pal_probe_data != want) {
            printf("  palette mismatch at %d: got %06x want %06x\n",
                   i, tb->pal_probe_data, want);
            pal_ok = false;
        }
    }
    CHECK(pal_ok, "palette contents exact");

    printf("=== Results: %d passed, %d failed ===\n", pass, fail);
    delete tb;
    return fail ? 1 : 0;
}
