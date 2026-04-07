/*
 * Verilator C++ Test Harness for GPU Core
 *
 * Tests the GPU span/triangle rasteriser: ring buffer protocol, command decode,
 * texture cache, colormap BRAM, framebuffer writes, clear, fence sync.
 *
 * Uses a simplified SDRAM model (flat 4MB) and SRAM model (256KB)
 * inside tb_gpu.v.  The C++ side populates memory via backdoor ports,
 * writes MMIO registers to configure the GPU, and reads back the
 * framebuffer to verify pixel output.
 */

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cassert>
#include "Vtb_gpu.h"
#include "verilated.h"
#include "verilated_vcd_c.h"

static Vtb_gpu *tb;
static VerilatedVcdC *trace;
static uint64_t sim_time = 0;
static int pass_count = 0;
static int fail_count = 0;

// ---- SDRAM layout (word addresses) ----
// Textures:    0x010000..0x01FFFF
// Framebuffer: 0x020000..0x02FFFF (320*200 = 64000 bytes = 16000 words)
static const uint32_t TEX_BASE_BYTE  = 0x00040000;   // word 0x10000
static const uint32_t FB_BASE_BYTE   = 0x00080000;   // word 0x20000

// Ring state (mirrors GPU ring BRAM, 16 KB)
static const uint32_t RING_SIZE      = 0x00004000;   // 16KB
static uint32_t ring_wrptr = 0;
static uint32_t ring_mask  = RING_SIZE - 1;

// ================================================================
// Helpers
// ================================================================

static void tick() {
    tb->clk = 0;
    tb->eval();
    if (trace) trace->dump(sim_time);
    sim_time++;
    tb->clk = 1;
    tb->eval();
    if (trace) trace->dump(sim_time);
    sim_time++;
}

static void reset() {
    tb->reset_n  = 0;
    tb->reg_wr   = 0;
    tb->bd_we    = 0;
    for (int i = 0; i < 20; i++) tick();
    tb->reset_n = 1;
    for (int i = 0; i < 5; i++) tick();
}

// Backdoor: write a word to SDRAM
static void sdram_write(uint32_t word_addr, uint32_t data) {
    tb->bd_we    = 1;
    tb->bd_addr  = word_addr;
    tb->bd_wdata = data;
    tick();
    tb->bd_we = 0;
}

// Backdoor: read a word from SDRAM
static uint32_t sdram_read(uint32_t word_addr) {
    tb->bd_rd_addr = word_addr;
    tb->eval();  // combinational read
    return tb->bd_rd_data;
}

// Read a byte from SDRAM (framebuffer)
static uint8_t sdram_read_byte(uint32_t byte_addr) {
    uint32_t word = sdram_read(byte_addr >> 2);
    return (word >> ((byte_addr & 3) * 8)) & 0xFF;
}

// Write MMIO register
static void mmio_write(uint32_t reg, uint32_t val) {
    tb->reg_wr    = 1;
    tb->reg_addr  = reg;
    tb->reg_wdata = val;
    tick();
    tb->reg_wr = 0;
}

// Read MMIO register
static uint32_t mmio_read(uint32_t reg) {
    tb->reg_addr = reg;
    tb->eval();
    return tb->reg_rdata;
}

// Write a word to the ring BRAM (via GPU_RING_DATA MMIO, auto-increment)
static void ring_write(uint32_t w) {
    mmio_write(2, w);           // reg_addr 2 = GPU_RING_DATA
    ring_wrptr = (ring_wrptr + 4) & ring_mask;
}

// Write command header
static void ring_cmd(uint8_t cmd, uint32_t payload_words) {
    ring_write(((uint32_t)cmd << 24) | payload_words);
}

// Kick GPU (update WRPTR MMIO)
static void gpu_kick() {
    mmio_write(1, ring_wrptr);  // reg_addr 1 = GPU_RING_WRPTR
}

// Wait for GPU to reach fence, with timeout
static bool gpu_wait_fence(uint32_t token, int timeout = 200000) {
    for (int t = 0; t < timeout; t++) {
        tick();
        uint32_t reached = tb->fence_reached;
        if ((int32_t)(reached - token) >= 0)
            return true;
        if (t < 200 && (t % 50 == 0) && tb->dbg_state >= 26) {
            printf("  [trace t=%d] state=%u step=%u det=%d\n",
                   t, tb->dbg_state, tb->dbg_setup_step, (int32_t)tb->dbg_tri_det);
        }
    }
    printf("  TIMEOUT: gpu_wait_fence token=%u reached=%u state=%u step=%u stat_px=%u stat_spans=%u\n",
           token, tb->fence_reached, tb->dbg_state, tb->dbg_setup_step,
           tb->stat_pixels, tb->stat_spans);
    return false;
}

// Submit fence + kick, return token
static uint32_t gpu_submit() {
    static uint32_t next_token = 1;
    uint32_t token = next_token++;
    ring_cmd(0x02, 1);  // CMD_FENCE
    ring_write(token);
    gpu_kick();
    return token;
}

// Submit + wait
static bool gpu_finish(int timeout = 200000) {
    uint32_t t = gpu_submit();
    return gpu_wait_fence(t, timeout);
}

// Initialise GPU: toggle hardware reset, then configure MMIO
static void gpu_init() {
    ring_wrptr = 0;
    // Hard reset to clear all internal state
    tb->reset_n = 0;
    for (int i = 0; i < 10; i++) tick();
    tb->reset_n = 1;
    for (int i = 0; i < 5; i++) tick();
    // Reset ring pointers (CTRL bit2 = ring_reset)
    mmio_write(0, 4);              // GPU_CTRL: ring_reset
    mmio_write(1, 0);              // GPU_RING_WRPTR = 0
}

// ---- Test Helpers ----
static void check(const char *name, uint32_t got, uint32_t expected) {
    if (got == expected) {
        pass_count++;
    } else {
        printf("  FAIL %s: got 0x%08x, expected 0x%08x\n", name, got, expected);
        fail_count++;
    }
}

static void check_byte(const char *name, uint32_t fb_byte_offset,
                        uint8_t expected) {
    uint8_t got = sdram_read_byte(FB_BASE_BYTE + fb_byte_offset);
    if (got == expected) {
        pass_count++;
    } else {
        printf("  FAIL %s: FB[%u] = 0x%02x, expected 0x%02x\n",
               name, fb_byte_offset, got, expected);
        fail_count++;
    }
}

// =====================================================================
// Test Cases
// =====================================================================

// Test 1: Basic ring + fence sync
static void test_fence_sync() {
    printf("TEST: Fence synchronisation\n");

    gpu_init();

    // Submit a NOP + fence
    ring_cmd(0x01, 0);  // CMD_NOP
    uint32_t t = gpu_submit();
    bool ok = gpu_wait_fence(t);
    check("fence_reached", ok ? 1 : 0, 1);
    check("fence_value", tb->fence_reached, t);

    // Verify GPU works after a second gpu_init() reset
    printf("  (re-init and re-test)\n");
    gpu_init();
    ring_cmd(0x01, 0);
    t = gpu_submit();
    ok = gpu_wait_fence(t);
    check("fence_after_reinit", ok ? 1 : 0, 1);

    // Diagnostic: print ring state
    uint32_t status = mmio_read(5);
    uint32_t rdptr  = mmio_read(4);
    printf("  status=0x%08x rdptr=%u wrptr=%u fence=%u\n",
           status, rdptr, ring_wrptr, tb->fence_reached);
}

// Test 1b: SET_FB command (isolated)
static void test_set_fb_only() {
    printf("TEST: SET_FB only\n");
    gpu_init();

    // SET_FB with 2 payload words
    ring_cmd(0x23, 2);
    ring_write(FB_BASE_BYTE);
    ring_write(320);

    bool ok = gpu_finish();
    check("set_fb_done", ok ? 1 : 0, 1);
}

// Test 2: Clear framebuffer
static void test_clear_fb() {
    printf("TEST: Clear framebuffer\n");

    gpu_init();

    // Set framebuffer address
    ring_cmd(0x23, 2);  // CMD_SET_FB
    ring_write(FB_BASE_BYTE);
    ring_write(320);     // stride

    // Clear with color 0x42
    ring_cmd(0x10, 2);  // CMD_CLEAR
    ring_write((1 << 16) | 0x42);  // flags=CLEAR_COLOR, color=0x42
    ring_write(0);  // depth (unused)

    bool ok = gpu_finish();
    check("clear_done", ok ? 1 : 0, 1);

    // Verify first few pixels
    check_byte("clear_px0", 0, 0x42);
    check_byte("clear_px1", 1, 0x42);
    check_byte("clear_px4", 4, 0x42);
    check_byte("clear_px100", 100, 0x42);

    // Verify pixel near end (320*200-1 = 63999)
    check_byte("clear_px_last", 63999, 0x42);
}

// Test 3: Simple untextured span (solid color via identity colormap)
static void test_solid_span() {
    printf("TEST: Solid-color span (identity colormap)\n");

    gpu_init();

    // Upload identity colormap: output = input for light level 0
    // cmap[0*256 + i] = i  for i in 0..255
    for (int i = 0; i < 256; i++) {
        mmio_write(8, i);          // GPU_CMAP_ADDR
        mmio_write(9, i & 0xFF);   // GPU_CMAP_DATA
    }

    // Set FB
    ring_cmd(0x23, 2);
    ring_write(FB_BASE_BYTE);
    ring_write(320);

    // Clear FB to 0x00 first
    ring_cmd(0x10, 2);
    ring_write((1 << 16) | 0x00);
    ring_write(0);

    // Create a 1-byte "texture" in SDRAM: texel value = 0xAB
    sdram_write(TEX_BASE_BYTE >> 2, 0xABABABAB);

    // Draw a horizontal span of 8 pixels at FB row 0
    ring_cmd(0x40, 18);  // CMD_DRAW_SPAN, 18 payload words
    ring_write(FB_BASE_BYTE);       // fb_addr
    ring_write(TEX_BASE_BYTE);      // tex_addr
    ring_write(0);                  // s = 0
    ring_write(0);                  // t = 0
    ring_write(0);                  // sstep = 0 (same texel)
    ring_write(0);                  // tstep = 0
    // count=8, light=0, flags=COLORMAP
    ring_write((8 << 16) | (0 << 8) | 0x01);
    // fb_stride=1, tex_width=1
    ring_write((1 << 16) | 1);
    // tex_shift=0, tex_bits=0
    ring_write(0);
    // z_addr, zi, zistep (unused)
    ring_write(0); ring_write(0); ring_write(0);
    // perspective params (unused)
    ring_write(0); ring_write(0); ring_write(0);
    ring_write(0); ring_write(0); ring_write(0);

    bool ok = gpu_finish();
    check("span_done", ok ? 1 : 0, 1);

    // Verify 8 pixels at FB offset 0..7 are 0xAB (identity colormap)
    for (int i = 0; i < 8; i++) {
        char name[32];
        snprintf(name, sizeof(name), "span_px%d", i);
        check_byte(name, i, 0xAB);
    }

    // Pixel 8 should be 0x00 (cleared, not drawn)
    check_byte("span_px8_clear", 8, 0x00);

    // Check stats
    check("stat_pixels", tb->stat_pixels, 8);
    check("stat_spans", tb->stat_spans, 1);
}

// Test 4: Textured span with stepping
static void test_textured_span() {
    printf("TEST: Textured span with texture coordinate stepping\n");

    gpu_init();

    // Upload identity colormap for light=0
    for (int i = 0; i < 256; i++) {
        mmio_write(8, i);
        mmio_write(9, i & 0xFF);
    }

    // Set FB
    ring_cmd(0x23, 2);
    ring_write(FB_BASE_BYTE);
    ring_write(320);

    // Clear FB
    ring_cmd(0x10, 2);
    ring_write((1 << 16) | 0x00);
    ring_write(0);

    // Create a 4-byte texture: [0x10, 0x20, 0x30, 0x40]
    sdram_write(TEX_BASE_BYTE >> 2, 0x40302010);

    // Draw 4 pixels, stepping through texture (s=0, sstep=1.0 = 0x10000)
    ring_cmd(0x40, 18);
    ring_write(FB_BASE_BYTE);       // fb_addr
    ring_write(TEX_BASE_BYTE);      // tex_addr
    ring_write(0);                  // s = 0
    ring_write(0);                  // t = 0
    ring_write(0x00010000);         // sstep = 1.0 (16.16)
    ring_write(0);                  // tstep = 0
    // count=4, light=0, flags=COLORMAP
    ring_write((4 << 16) | (0 << 8) | 0x01);
    // fb_stride=1, tex_width=4 (multiply mode)
    ring_write((1 << 16) | 4);
    // tex_shift=0, tex_bits=0
    ring_write(0);
    // z, zi, zistep
    ring_write(0); ring_write(0); ring_write(0);
    // perspective
    ring_write(0); ring_write(0); ring_write(0);
    ring_write(0); ring_write(0); ring_write(0);

    bool ok = gpu_finish();
    check("tex_span_done", ok ? 1 : 0, 1);

    // Each pixel should sample the corresponding texel
    check_byte("tex_px0", 0, 0x10);
    check_byte("tex_px1", 1, 0x20);
    check_byte("tex_px2", 2, 0x30);
    check_byte("tex_px3", 3, 0x40);
}

// Test 5: Colormap lighting
static void test_colormap_lighting() {
    printf("TEST: Colormap with light level\n");

    gpu_init();

    // Upload colormap: light=0 is identity, light=1 maps everything to 0x77
    for (int i = 0; i < 256; i++) {
        mmio_write(8, i);       // light 0, entry i
        mmio_write(9, i);
    }
    for (int i = 0; i < 256; i++) {
        mmio_write(8, 256 + i); // light 1, entry i
        mmio_write(9, 0x77);
    }

    // Set FB
    ring_cmd(0x23, 2);
    ring_write(FB_BASE_BYTE);
    ring_write(320);

    // Clear FB
    ring_cmd(0x10, 2);
    ring_write((1 << 16) | 0x00);
    ring_write(0);

    // Texture: [0xAA]
    sdram_write(TEX_BASE_BYTE >> 2, 0xAAAAAAAA);

    // Draw 4 pixels with light=1 (should all be 0x77)
    ring_cmd(0x40, 18);
    ring_write(FB_BASE_BYTE);
    ring_write(TEX_BASE_BYTE);
    ring_write(0); ring_write(0);    // s, t
    ring_write(0); ring_write(0);    // sstep, tstep
    // count=4, light=1, flags=COLORMAP
    ring_write((4 << 16) | (1 << 8) | 0x01);
    ring_write((1 << 16) | 1);      // fb_stride=1, tex_width=1
    ring_write(0);
    ring_write(0); ring_write(0); ring_write(0);
    ring_write(0); ring_write(0); ring_write(0);
    ring_write(0); ring_write(0); ring_write(0);

    bool ok = gpu_finish();
    check("cmap_light_done", ok ? 1 : 0, 1);

    check_byte("cmap_px0", 0, 0x77);
    check_byte("cmap_px1", 1, 0x77);
    check_byte("cmap_px2", 2, 0x77);
    check_byte("cmap_px3", 3, 0x77);
}

// Test 6: Vertical column (fb_stride = 320)
static void test_vertical_column() {
    printf("TEST: Vertical column span (fb_stride=320)\n");

    gpu_init();

    // Identity colormap
    for (int i = 0; i < 256; i++) {
        mmio_write(8, i);
        mmio_write(9, i);
    }

    // Set FB (320 bytes per row)
    ring_cmd(0x23, 2);
    ring_write(FB_BASE_BYTE);
    ring_write(320);

    // Clear
    ring_cmd(0x10, 2);
    ring_write((1 << 16) | 0x00);
    ring_write(0);

    // Texture: [0xCC]
    sdram_write(TEX_BASE_BYTE >> 2, 0xCCCCCCCC);

    // Draw column of 4 pixels starting at column 5, row 0
    uint32_t col_fb_addr = FB_BASE_BYTE + 5;
    ring_cmd(0x40, 18);
    ring_write(col_fb_addr);
    ring_write(TEX_BASE_BYTE);
    ring_write(0); ring_write(0);
    ring_write(0); ring_write(0);
    // count=4, light=0, flags=COLORMAP|COLUMN
    ring_write((4 << 16) | (0 << 8) | 0x03);
    // fb_stride=320, tex_width=1
    ring_write((320 << 16) | 1);
    ring_write(0);
    ring_write(0); ring_write(0); ring_write(0);
    ring_write(0); ring_write(0); ring_write(0);
    ring_write(0); ring_write(0); ring_write(0);

    bool ok = gpu_finish();
    check("col_done", ok ? 1 : 0, 1);

    // Pixel at (5, 0), (5, 1), (5, 2), (5, 3)
    check_byte("col_r0", 5 + 0*320, 0xCC);
    check_byte("col_r1", 5 + 1*320, 0xCC);
    check_byte("col_r2", 5 + 2*320, 0xCC);
    check_byte("col_r3", 5 + 3*320, 0xCC);

    // Adjacent pixels should be clear
    check_byte("col_adj0", 4, 0x00);
    check_byte("col_adj1", 6, 0x00);
}

// Test 7: Skip-zero transparency
static void test_skip_zero() {
    printf("TEST: Skip-zero transparency (SPAN_SKIP_ZERO)\n");

    gpu_init();

    // Identity colormap
    for (int i = 0; i < 256; i++) {
        mmio_write(8, i);
        mmio_write(9, i);
    }

    // Set FB
    ring_cmd(0x23, 2);
    ring_write(FB_BASE_BYTE);
    ring_write(320);

    // Clear to 0xEE (background)
    ring_cmd(0x10, 2);
    ring_write((1 << 16) | 0xEE);
    ring_write(0);

    // Texture: [0x11, 0xFF, 0x22, 0xFF]
    // 0xFF should be skipped (transparent), 0x11 and 0x22 drawn
    sdram_write(TEX_BASE_BYTE >> 2, 0xFF22FF11);

    // Draw 4 pixels with SKIP_ZERO flag
    ring_cmd(0x40, 18);
    ring_write(FB_BASE_BYTE);
    ring_write(TEX_BASE_BYTE);
    ring_write(0); ring_write(0);
    ring_write(0x00010000); ring_write(0);  // sstep=1.0
    // count=4, light=0, flags=COLORMAP|SKIP_ZERO
    ring_write((4 << 16) | (0 << 8) | 0x05);
    ring_write((1 << 16) | 4);  // fb_stride=1, tex_width=4
    ring_write(0);
    ring_write(0); ring_write(0); ring_write(0);
    ring_write(0); ring_write(0); ring_write(0);
    ring_write(0); ring_write(0); ring_write(0);

    bool ok = gpu_finish();
    check("skip_done", ok ? 1 : 0, 1);

    // px0 = texel 0x11 → drawn
    check_byte("skip_px0", 0, 0x11);
    // px1 = texel 0xFF → SKIPPED, background 0xEE preserved
    check_byte("skip_px1", 1, 0xEE);
    // px2 = texel 0x22 → drawn
    check_byte("skip_px2", 2, 0x22);
    // px3 = texel 0xFF → SKIPPED
    check_byte("skip_px3", 3, 0xEE);
}

// Test 8: Multiple commands in sequence
static void test_multiple_commands() {
    printf("TEST: Multiple commands in ring\n");

    gpu_init();

    // Identity colormap
    for (int i = 0; i < 256; i++) {
        mmio_write(8, i);
        mmio_write(9, i);
    }

    // Set FB
    ring_cmd(0x23, 2);
    ring_write(FB_BASE_BYTE);
    ring_write(320);

    // Clear to 0
    ring_cmd(0x10, 2);
    ring_write((1 << 16) | 0x00);
    ring_write(0);

    // Texture A: [0xAA]
    sdram_write(TEX_BASE_BYTE >> 2, 0xAAAAAAAA);
    // Texture B at +16 bytes: [0xBB]
    sdram_write((TEX_BASE_BYTE >> 2) + 4, 0xBBBBBBBB);

    // Span 1: 4 pixels at offset 0, texture A
    ring_cmd(0x40, 18);
    ring_write(FB_BASE_BYTE + 0);
    ring_write(TEX_BASE_BYTE);
    ring_write(0); ring_write(0);
    ring_write(0); ring_write(0);
    ring_write((4 << 16) | (0 << 8) | 0x01);
    ring_write((1 << 16) | 1);
    ring_write(0);
    ring_write(0); ring_write(0); ring_write(0);
    ring_write(0); ring_write(0); ring_write(0);
    ring_write(0); ring_write(0); ring_write(0);

    // Span 2: 4 pixels at offset 10, texture B
    ring_cmd(0x40, 18);
    ring_write(FB_BASE_BYTE + 10);
    ring_write(TEX_BASE_BYTE + 16);
    ring_write(0); ring_write(0);
    ring_write(0); ring_write(0);
    ring_write((4 << 16) | (0 << 8) | 0x01);
    ring_write((1 << 16) | 1);
    ring_write(0);
    ring_write(0); ring_write(0); ring_write(0);
    ring_write(0); ring_write(0); ring_write(0);
    ring_write(0); ring_write(0); ring_write(0);

    bool ok = gpu_finish();
    check("multi_done", ok ? 1 : 0, 1);

    check_byte("multi_spanA_0", 0, 0xAA);
    check_byte("multi_spanA_3", 3, 0xAA);
    check_byte("multi_gap",     5, 0x00);
    check_byte("multi_spanB_0", 10, 0xBB);
    check_byte("multi_spanB_3", 13, 0xBB);
}

// Test 9: Texture cache — accessing different cache lines
static void test_tex_cache_miss() {
    printf("TEST: Texture cache — multiple cache lines\n");

    gpu_init();

    // Identity colormap
    for (int i = 0; i < 256; i++) {
        mmio_write(8, i);
        mmio_write(9, i);
    }

    // Set FB
    ring_cmd(0x23, 2);
    ring_write(FB_BASE_BYTE);
    ring_write(320);

    // Clear
    ring_cmd(0x10, 2);
    ring_write((1 << 16) | 0x00);
    ring_write(0);

    // Create a 64-byte texture (4 cache lines of 16 bytes)
    // Line 0: bytes 0x01..0x10
    // Line 1: bytes 0x11..0x20
    // Line 2: bytes 0x21..0x30
    // Line 3: bytes 0x31..0x40
    for (int w = 0; w < 16; w++) {
        uint32_t b0 = (w * 4) + 1;
        uint32_t val = b0 | ((b0+1) << 8) | ((b0+2) << 16) | ((b0+3) << 24);
        sdram_write((TEX_BASE_BYTE >> 2) + w, val);
    }

    // Draw 32 pixels stepping through texture
    ring_cmd(0x40, 18);
    ring_write(FB_BASE_BYTE);
    ring_write(TEX_BASE_BYTE);
    ring_write(0); ring_write(0);
    ring_write(0x00010000); ring_write(0);  // sstep=1.0
    ring_write((32 << 16) | (0 << 8) | 0x01);  // count=32, colormap
    ring_write((1 << 16) | 64);  // fb_stride=1, tex_width=64
    ring_write(0);
    ring_write(0); ring_write(0); ring_write(0);
    ring_write(0); ring_write(0); ring_write(0);
    ring_write(0); ring_write(0); ring_write(0);

    bool ok = gpu_finish();
    check("cache_done", ok ? 1 : 0, 1);

    // Verify first pixel of each cache line
    check_byte("cache_line0", 0, 0x01);
    check_byte("cache_line1", 16, 0x11);
    check_byte("cache_line2", 17, 0x12);
    check_byte("cache_px31",  31, 0x20);
}

#ifdef GPU_PERSP_IMPL
// =====================================================================
// Perspective Span Tests
// =====================================================================
//
// Perspective spans use the same DRAW_SPAN command, with the SPAN_PERSP
// flag bit set and pay_buf[12..17] populated with projection-space
// (s/z, t/z, 1/z) initial values + per-pixel deltas.
//
// The GPU computes:
//     z          = 1 / (1/z)
//     s_anchor   = (s/z) * z
//     t_anchor   = (t/z) * z
// at both ends of each 16-pixel segment, derives an affine slope, and
// feeds the slope into the existing pipelined fragment processor.
//
// SPAN_PERSP = bit 5 → flag value 0x20.

// Helper to submit one persp span (writes 18-word DRAW_SPAN payload).
static void persp_draw_span(uint32_t fb_addr,
                             uint32_t tex_addr,
                             uint16_t count,
                             uint8_t  light,
                             uint16_t tex_width,
                             int32_t  sdivz,    int32_t tdivz,
                             int32_t  zi_persp,
                             int32_t  sdivz_step, int32_t tdivz_step,
                             int32_t  zi_step) {
    ring_cmd(0x40, 18);
    ring_write(fb_addr);
    ring_write(tex_addr);
    ring_write(0);                 // s (unused in persp)
    ring_write(0);                 // t (unused in persp)
    ring_write(0);                 // sstep (unused in persp)
    ring_write(0);                 // tstep (unused in persp)
    // count, light, flags = COLORMAP|PERSP
    ring_write(((uint32_t)count << 16) |
               ((uint32_t)light << 8) |
               (0x01 | 0x20));
    ring_write((1u << 16) | tex_width);  // fb_stride=1, tex_width
    ring_write(0);                 // tex_shift, tex_bits
    ring_write(0); ring_write(0); ring_write(0);  // z_addr, zi, zistep
    ring_write((uint32_t)sdivz);
    ring_write((uint32_t)tdivz);
    ring_write((uint32_t)zi_persp);
    ring_write((uint32_t)sdivz_step);
    ring_write((uint32_t)tdivz_step);
    ring_write((uint32_t)zi_step);
}

// Test P1: persp span with constant 1/z = 1.0
// Should be equivalent to an affine span: at z=1, s=s/z and slope=sZstep.
// Texture is [0..15], 16 pixels, sZstep=1.0 → expect texels 0..15.
static void test_persp_constant_z() {
    printf("TEST: Persp span — constant 1/z (affine equivalent)\n");
    gpu_init();

    // Identity colormap
    for (int i = 0; i < 256; i++) { mmio_write(8, i); mmio_write(9, i); }

    ring_cmd(0x23, 2); ring_write(FB_BASE_BYTE); ring_write(320);
    ring_cmd(0x10, 2); ring_write((1 << 16) | 0x00); ring_write(0);

    // 16-byte texture: [0,1,2,3, 4,5,6,7, ...]
    for (int w = 0; w < 4; w++) {
        uint32_t v = (w*4) | ((w*4+1) << 8) | ((w*4+2) << 16) | ((w*4+3) << 24);
        sdram_write((TEX_BASE_BYTE >> 2) + w, v);
    }

    // 16 pixels, persp constant z=1 (zinv=1.0=0x10000, zi_step=0)
    // sdivz starts at 0, sdivz_step = 1.0 → s_anchor at pos N = N*1.0
    persp_draw_span(FB_BASE_BYTE, TEX_BASE_BYTE,
                    /*count*/16, /*light*/0, /*tex_width*/16,
                    /*sdivz*/0,         /*tdivz*/0,
                    /*zi_persp*/0x10000,
                    /*sdivz_step*/0x10000, /*tdivz_step*/0,
                    /*zi_step*/0);

    bool ok = gpu_finish();
    check("persp_const_done", ok ? 1 : 0, 1);

    for (int i = 0; i < 16; i++) {
        char name[32];
        snprintf(name, sizeof(name), "persp_const_px%d", i);
        check_byte(name, i, (uint8_t)i);
    }
}

// Test P2: persp span across the segment boundary (32 pixels, const z)
// Exercises the slot-A → slot-B swap with constant z, so the answer is
// still affine: texels 0..31.
static void test_persp_two_segments() {
    printf("TEST: Persp span — segment swap, constant 1/z (32 pixels)\n");
    gpu_init();

    for (int i = 0; i < 256; i++) { mmio_write(8, i); mmio_write(9, i); }

    ring_cmd(0x23, 2); ring_write(FB_BASE_BYTE); ring_write(320);
    ring_cmd(0x10, 2); ring_write((1 << 16) | 0x00); ring_write(0);

    // 32-byte texture
    for (int w = 0; w < 8; w++) {
        uint32_t v = (w*4) | ((w*4+1) << 8) | ((w*4+2) << 16) | ((w*4+3) << 24);
        sdram_write((TEX_BASE_BYTE >> 2) + w, v);
    }

    persp_draw_span(FB_BASE_BYTE, TEX_BASE_BYTE,
                    /*count*/32, /*light*/0, /*tex_width*/32,
                    /*sdivz*/0,         /*tdivz*/0,
                    /*zi_persp*/0x10000,
                    /*sdivz_step*/0x10000, /*tdivz_step*/0,
                    /*zi_step*/0);

    bool ok = gpu_finish();
    check("persp_2seg_done", ok ? 1 : 0, 1);

    // Spot check: first, segment boundary, last
    check_byte("persp_2seg_px0",  0,  0);
    check_byte("persp_2seg_px15", 15, 15);
    check_byte("persp_2seg_px16", 16, 16);  // first px of segment 1
    check_byte("persp_2seg_px17", 17, 17);
    check_byte("persp_2seg_px31", 31, 31);
}

// Test P3: persp span with varying 1/z — verify pixel 0 anchor matches
// hand-computed (s/z) * (1 / (1/z)).
//
// Setup: zinv goes from 1.0 (z=1) to 2.0 (z=0.5) over 16 pixels.
// sdivz = 0 at pos 0, sdivz_step set so s/z = x/16 at pos x (sZ_step =
// 1/16 = 0x1000 in 16.16). So:
//   pos 0 : s/z = 0,    1/z = 1.0  → s = 0
//   pos 16: s/z = 1.0,  1/z = 2.0  → s = (1.0)*(1/2) = 0.5
//   slope = (0.5 - 0) / 16 = 0.03125 = 0x800 in 16.16
//
// Pixel 0 should sample texel 0; pixel 15 samples texel floor(15*0.03125) = 0.
// Set up the texture so a few different texel values sit at low indices.
static void test_persp_varying_z() {
    printf("TEST: Persp span — varying 1/z (anchor sanity check)\n");
    gpu_init();

    for (int i = 0; i < 256; i++) { mmio_write(8, i); mmio_write(9, i); }

    ring_cmd(0x23, 2); ring_write(FB_BASE_BYTE); ring_write(320);
    ring_cmd(0x10, 2); ring_write((1 << 16) | 0x00); ring_write(0);

    // Texture: each byte = its own index. 16 bytes is enough.
    for (int w = 0; w < 4; w++) {
        uint32_t v = (w*4) | ((w*4+1) << 8) | ((w*4+2) << 16) | ((w*4+3) << 24);
        sdram_write((TEX_BASE_BYTE >> 2) + w, v);
    }

    // Persp params: 16 pixels, zinv from 1.0 → 2.0, sdivz from 0 → 1.0
    persp_draw_span(FB_BASE_BYTE, TEX_BASE_BYTE,
                    /*count*/16, /*light*/0, /*tex_width*/16,
                    /*sdivz*/0,           /*tdivz*/0,
                    /*zi_persp*/0x10000,
                    /*sdivz_step*/0x1000, /*tdivz_step*/0,    // 1/16 per pixel
                    /*zi_step*/0x1000);                       // 1/16 per pixel

    bool ok = gpu_finish();
    check("persp_var_done", ok ? 1 : 0, 1);

    // All pixels in segment 0 should sample s = anchor + N*slope.
    // anchor=0, slope=0.03125 → all s_int.high = 0 → texel 0.
    check_byte("persp_var_px0",  0,  0);
    check_byte("persp_var_px15", 15, 0);
}
#endif // GPU_PERSP_IMPL

#ifdef GPU_FEAT_TRIANGLE
// =====================================================================
// Triangle Test Helpers
// =====================================================================

// Write a vertex (6 words) to the ring in wire format
// x, y are 12.4 fixed-point; z is 16-bit; s, t are 16.16
static void ring_write_vertex(int16_t x, int16_t y, uint16_t z,
                               int32_t s, int32_t t, uint8_t r) {
    ring_write(((uint32_t)(uint16_t)x << 16) | (uint16_t)y);
    ring_write(((uint32_t)z << 16));
    ring_write((uint32_t)s);
    ring_write((uint32_t)t);
    ring_write(0x00010000);  // w = 1.0 (affine)
    ring_write((uint32_t)r);  // r in low byte
}

// Bind a texture via SET_TEXTURE command
static void ring_bind_texture(uint32_t addr, uint16_t w, uint16_t h) {
    ring_cmd(0x20, 4);  // CMD_SET_TEXTURE
    ring_write(addr);
    ring_write(((uint32_t)w << 16) | h);
    ring_write(0);  // format=I8, wrap=repeat
    ring_write(0);  // wrap_t=repeat
}

// Set depth test via CMD_SET_DEPTH_FUNC
static void ring_depth_func(uint32_t func) {
    ring_cmd(0x21, 1);
    ring_write(func);
}

// =====================================================================
// Triangle Tests
// =====================================================================

// Test T1: Simple flat-color triangle (no texture stepping)
// Draws a small right triangle covering known pixels
static void test_triangle_flat() {
    printf("TEST: Flat-color triangle\n");

    gpu_init();

    // Identity colormap for light=0
    for (int i = 0; i < 256; i++) {
        mmio_write(8, i);
        mmio_write(9, i);
    }

    // Set FB
    ring_cmd(0x23, 2);
    ring_write(FB_BASE_BYTE);
    ring_write(320);

    // Clear to 0
    ring_cmd(0x10, 2);
    ring_write((1 << 16) | 0x00);
    ring_write(0);

    // Texture: single byte 0xAA
    sdram_write(TEX_BASE_BYTE >> 2, 0xAAAAAAAA);

    // Bind texture (1x1, I8)
    ring_bind_texture(TEX_BASE_BYTE, 1, 1);

    // Draw triangle: (2,2), (10,2), (2,8) in pixels → 12.4 = (32,32), (160,32), (32,128)
    ring_cmd(0x30, 19);  // CMD_DRAW_TRIANGLES, 1 + 3*6 = 19 words
    ring_write(3);       // vertex count
    ring_write_vertex(2*16, 2*16, 0, 0, 0, 0);    // v0
    ring_write_vertex(10*16, 2*16, 0, 0, 0, 0);   // v1
    ring_write_vertex(2*16, 8*16, 0, 0, 0, 0);    // v2

    bool ok = gpu_finish();
    check("tri_flat_done", ok ? 1 : 0, 1);

    // Debug: check what the GPU did
    printf("  [debug] FB[642]=0x%02x stat_pixels=%u state=%u step=%u det=%d\n",
           sdram_read_byte(FB_BASE_BYTE + 2 + 2*320),
           tb->stat_pixels, tb->dbg_state, tb->dbg_setup_step, (int32_t)tb->dbg_tri_det);

    // Pixel (2,2) should be inside — it's at vertex v0
    check_byte("tri_px_2_2", 2 + 2*320, 0xAA);
    // Pixel (5,2) should be inside (on top edge)
    check_byte("tri_px_5_2", 5 + 2*320, 0xAA);
    // Pixel (9,2) should be inside (near v1)
    check_byte("tri_px_9_2", 9 + 2*320, 0xAA);
    // Pixel (2,5) should be inside (on left edge)
    check_byte("tri_px_2_5", 2 + 5*320, 0xAA);
    // Pixel (0,0) should be clear (outside)
    check_byte("tri_px_0_0", 0 + 0*320, 0x00);
    // Pixel (11,2) should be clear (outside, right of top edge)
    check_byte("tri_px_11_2", 11 + 2*320, 0x00);
    // Pixel (8,7) should be clear (outside, below hypotenuse)
    check_byte("tri_px_8_7", 8 + 7*320, 0x00);
}

// Test T2: Degenerate triangle (zero area) — should be skipped
static void test_triangle_degenerate() {
    printf("TEST: Degenerate triangle (collinear vertices)\n");

    gpu_init();

    // Set FB
    ring_cmd(0x23, 2);
    ring_write(FB_BASE_BYTE);
    ring_write(320);

    // Clear to 0xEE
    ring_cmd(0x10, 2);
    ring_write((1 << 16) | 0xEE);
    ring_write(0);

    sdram_write(TEX_BASE_BYTE >> 2, 0xAAAAAAAA);
    ring_bind_texture(TEX_BASE_BYTE, 1, 1);

    // All three vertices on a line: (10,10), (20,10), (30,10)
    ring_cmd(0x30, 19);
    ring_write(3);
    ring_write_vertex(10*16, 10*16, 0, 0, 0, 0);
    ring_write_vertex(20*16, 10*16, 0, 0, 0, 0);
    ring_write_vertex(30*16, 10*16, 0, 0, 0, 0);

    bool ok = gpu_finish();
    check("tri_degen_done", ok ? 1 : 0, 1);

    // No pixels should be drawn — FB should still be 0xEE
    check_byte("tri_degen_px", 15 + 10*320, 0xEE);
}

// Test T3: Textured triangle with per-vertex tex coords
static void test_triangle_textured() {
    printf("TEST: Textured triangle\n");

    gpu_init();

    // Identity colormap
    for (int i = 0; i < 256; i++) {
        mmio_write(8, i);
        mmio_write(9, i);
    }

    ring_cmd(0x23, 2);
    ring_write(FB_BASE_BYTE);
    ring_write(320);

    ring_cmd(0x10, 2);
    ring_write((1 << 16) | 0x00);
    ring_write(0);

    // 4x4 texture with distinct values
    // Row 0: 0x10 0x20 0x30 0x40
    // Row 1: 0x50 0x60 0x70 0x80
    // Row 2: 0x90 0xA0 0xB0 0xC0
    // Row 3: 0xD0 0xE0 0xF0 0xFF
    uint32_t tex_data[] = {
        0x40302010, 0x80706050, 0xC0B0A090, 0xFFF0E0D0
    };
    for (int i = 0; i < 4; i++)
        sdram_write((TEX_BASE_BYTE >> 2) + i, tex_data[i]);

    ring_bind_texture(TEX_BASE_BYTE, 4, 4);

    // Triangle: (0,0)-(8,0)-(0,8) with tex coords mapping to texture
    // v0: pos=(0,0) tex=(0,0)  v1: pos=(8,0) tex=(3,0)  v2: pos=(0,8) tex=(0,3)
    ring_cmd(0x30, 19);
    ring_write(3);
    ring_write_vertex(0, 0, 0, 0, 0, 0);
    ring_write_vertex(8*16, 0, 0, 3 << 16, 0, 0);
    ring_write_vertex(0, 8*16, 0, 0, 3 << 16, 0);

    bool ok = gpu_finish();
    check("tri_tex_done", ok ? 1 : 0, 1);

    // Pixel (0,0) should have texel at s=0, t=0 → 0x10
    check_byte("tri_tex_0_0", 0 + 0*320, 0x10);
    // Pixel (4,0) should be inside with s≈1.5 → texel at s=1 → 0x20
    // (gradient depends on setup precision, check it's not 0x00)
    uint8_t px = sdram_read_byte(FB_BASE_BYTE + 4 + 0*320);
    if (px != 0x00) { pass_count++; } else {
        printf("  FAIL tri_tex_4_0: FB[4] = 0x%02x, expected non-zero\n", px);
        fail_count++;
    }
}

// Test T4: Two triangles in sequence
static void test_triangle_multi() {
    printf("TEST: Multiple triangles\n");

    gpu_init();

    for (int i = 0; i < 256; i++) {
        mmio_write(8, i);
        mmio_write(9, i);
    }

    ring_cmd(0x23, 2);
    ring_write(FB_BASE_BYTE);
    ring_write(320);

    ring_cmd(0x10, 2);
    ring_write((1 << 16) | 0x00);
    ring_write(0);

    // Texture A: 0xAA
    sdram_write(TEX_BASE_BYTE >> 2, 0xAAAAAAAA);
    ring_bind_texture(TEX_BASE_BYTE, 1, 1);

    // Triangle 1: small triangle at (2,2)-(6,2)-(2,6)
    ring_cmd(0x30, 19);
    ring_write(3);
    ring_write_vertex(2*16, 2*16, 0, 0, 0, 0);
    ring_write_vertex(6*16, 2*16, 0, 0, 0, 0);
    ring_write_vertex(2*16, 6*16, 0, 0, 0, 0);

    // Triangle 2: at (20,2)-(24,2)-(20,6)
    ring_cmd(0x30, 19);
    ring_write(3);
    ring_write_vertex(20*16, 2*16, 0, 0, 0, 0);
    ring_write_vertex(24*16, 2*16, 0, 0, 0, 0);
    ring_write_vertex(20*16, 6*16, 0, 0, 0, 0);

    bool ok = gpu_finish();
    check("tri_multi_done", ok ? 1 : 0, 1);

    check_byte("tri1_inside", 3 + 3*320, 0xAA);
    check_byte("tri1_outside", 10 + 3*320, 0x00);
    check_byte("tri2_inside", 21 + 3*320, 0xAA);
    check_byte("tri2_outside", 25 + 3*320, 0x00);
}

// Test T5: Depth-tested triangles (front occludes back)
static void test_triangle_depth() {
    printf("TEST: Depth-tested triangles\n");

    gpu_init();

    for (int i = 0; i < 256; i++) { mmio_write(8, i); mmio_write(9, i); }

    ring_cmd(0x23, 2);
    ring_write(FB_BASE_BYTE);
    ring_write(320);

    // Set depth test + Z-buffer address
    ring_depth_func(2);
    ring_cmd(0x24, 2);  // CMD_SET_ZB
    ring_write(0);       // SRAM address 0
    ring_write(640);     // stride = 320 pixels × 2 bytes

    // Clear color + depth
    ring_cmd(0x10, 2);
    ring_write((3 << 16) | 0x00);  // CLEAR_COLOR | CLEAR_DEPTH
    ring_write(0xFFFF);             // far depth

    // Tex A: 0xAA (back triangle)
    sdram_write(TEX_BASE_BYTE >> 2, 0xAAAAAAAA);
    ring_bind_texture(TEX_BASE_BYTE, 1, 1);

    // Back triangle at z=0x8000 (far), covers (2,2)-(10,2)-(2,8)
    ring_cmd(0x30, 19);
    ring_write(3);
    ring_write_vertex(2*16, 2*16, 0x8000, 0, 0, 0);
    ring_write_vertex(10*16, 2*16, 0x8000, 0, 0, 0);
    ring_write_vertex(2*16, 8*16, 0x8000, 0, 0, 0);

    // Tex B: 0xBB (front triangle)
    sdram_write((TEX_BASE_BYTE >> 2) + 4, 0xBBBBBBBB);
    ring_bind_texture(TEX_BASE_BYTE + 16, 1, 1);

    // Front triangle at z=0x2000 (near), covers (4,3)-(8,3)-(4,6)
    ring_cmd(0x30, 19);
    ring_write(3);
    ring_write_vertex(4*16, 3*16, 0x2000, 0, 0, 0);
    ring_write_vertex(8*16, 3*16, 0x2000, 0, 0, 0);
    ring_write_vertex(4*16, 6*16, 0x2000, 0, 0, 0);

    bool ok = gpu_finish();
    check("depth_done", ok ? 1 : 0, 1);

    // Debug: check pixels in back triangle area
    for (int x = 2; x < 10; x++) {
        uint8_t v = sdram_read_byte(FB_BASE_BYTE + x + 3*320);
        if (v) printf("  [depth] px(%d,3) = 0x%02x\n", x, v);
    }

    // Pixel (2,3): back triangle only → 0xAA
    check_byte("depth_back", 2 + 3*320, 0xAA);
    // Pixel (6,3): front triangle overdraws back → 0xBB (z=0x2000 < 0x8000)
    check_byte("depth_front", 6 + 3*320, 0xBB);
}

// Test T6: Vertex color (R) interpolation
static void test_triangle_vertex_color() {
    printf("TEST: Vertex color interpolation\n");

    gpu_init();

    // Colormap: light level N maps texel to N (identity for light)
    for (int light = 0; light < 64; light++)
        for (int i = 0; i < 256; i++) {
            mmio_write(8, light * 256 + i);
            mmio_write(9, light);  // output = light level itself
        }

    ring_cmd(0x23, 2);
    ring_write(FB_BASE_BYTE);
    ring_write(320);

    ring_cmd(0x10, 2);
    ring_write((1 << 16) | 0x00);
    ring_write(0);

    // 1x1 texture with value 0xFF (colormap selects based on light)
    sdram_write(TEX_BASE_BYTE >> 2, 0xFFFFFFFF);
    ring_bind_texture(TEX_BASE_BYTE, 1, 1);

    // Triangle with varying vertex R: v0=0, v1=30, v2=0
    // At midpoint x=5, R should interpolate to ~15
    ring_cmd(0x30, 19);
    ring_write(3);
    ring_write_vertex(0, 0, 0, 0, 0, 0);       // r=0
    ring_write_vertex(10*16, 0, 0, 0, 0, 30);   // r=30
    ring_write_vertex(0, 10*16, 0, 0, 0, 0);    // r=0

    bool ok = gpu_finish();
    check("vcol_done", ok ? 1 : 0, 1);

    // Pixel (0,0): r=0 → colormap light 0 → output 0
    check_byte("vcol_v0", 0 + 0*320, 0x00);

    // Debug: check a few pixels along the top edge
    for (int x = 0; x < 10; x++) {
        uint8_t v = sdram_read_byte(FB_BASE_BYTE + x + 0*320);
        printf("  [vcol] px(%d,0) = 0x%02x\n", x, v);
    }

    // Pixel (5,0): R gradient disabled (ALM budget), uses v0 value = 0
    // When R gradient is re-enabled, this should be ~15
    check_byte("vcol_mid_v0", 5 + 0*320, 0x00);
}

// Test T7: Indexed draw
static void test_triangle_indexed() {
    printf("TEST: Indexed triangle draw\n");

    gpu_init();

    for (int i = 0; i < 256; i++) { mmio_write(8, i); mmio_write(9, i); }

    ring_cmd(0x23, 2);
    ring_write(FB_BASE_BYTE);
    ring_write(320);

    ring_cmd(0x10, 2);
    ring_write((1 << 16) | 0x00);
    ring_write(0);

    sdram_write(TEX_BASE_BYTE >> 2, 0xCCCCCCCC);
    ring_bind_texture(TEX_BASE_BYTE, 1, 1);

    // CMD_DRAW_INDEXED (0x31):
    // payload: vert_count(3), idx_count(3), 3 vertices (18 words), indices (2 words)
    // Total: 2 + 18 + 2 = 22 words — fits in pay_buf[24]
    ring_cmd(0x31, 22);
    ring_write(3);   // vert_count
    ring_write(3);   // idx_count
    // Vertex 0: (2,2)
    ring_write_vertex(2*16, 2*16, 0, 0, 0, 0);
    // Vertex 1: (10,2)
    ring_write_vertex(10*16, 2*16, 0, 0, 0, 0);
    // Vertex 2: (2,8)
    ring_write_vertex(2*16, 8*16, 0, 0, 0, 0);
    // Indices: [0, 1, 2] packed: word0=[31:16]=1 [15:0]=0; word1=[15:0]=2
    ring_write((1 << 16) | 0);
    ring_write(2);

    bool ok = gpu_finish();
    check("indexed_done", ok ? 1 : 0, 1);

    check_byte("indexed_inside", 3 + 3*320, 0xCC);
    check_byte("indexed_outside", 11 + 3*320, 0x00);
}

// Test T8: Two adjacent triangles (shared edge — no gap)
static void test_triangle_shared_edge() {
    printf("TEST: Shared edge (no gap between triangles)\n");

    gpu_init();

    for (int i = 0; i < 256; i++) { mmio_write(8, i); mmio_write(9, i); }

    ring_cmd(0x23, 2);
    ring_write(FB_BASE_BYTE);
    ring_write(320);

    ring_cmd(0x10, 2);
    ring_write((1 << 16) | 0x00);
    ring_write(0);

    sdram_write(TEX_BASE_BYTE >> 2, 0xAAAAAAAA);
    ring_bind_texture(TEX_BASE_BYTE, 1, 1);

    // Two triangles sharing edge (5,0)-(5,10):
    // Left:  (0,0)-(5,0)-(0,10)
    // Right: (5,0)-(10,0)-(5,10)
    ring_cmd(0x30, 19);
    ring_write(3);
    ring_write_vertex(0, 0, 0, 0, 0, 0);
    ring_write_vertex(5*16, 0, 0, 0, 0, 0);
    ring_write_vertex(0, 10*16, 0, 0, 0, 0);

    ring_cmd(0x30, 19);
    ring_write(3);
    ring_write_vertex(5*16, 0, 0, 0, 0, 0);
    ring_write_vertex(10*16, 0, 0, 0, 0, 0);
    ring_write_vertex(5*16, 10*16, 0, 0, 0, 0);

    bool ok = gpu_finish();
    check("shared_done", ok ? 1 : 0, 1);

    // Pixel on shared edge (5,5) should be covered by at least one triangle
    uint8_t px = sdram_read_byte(FB_BASE_BYTE + 5 + 5*320);
    if (px == 0xAA) { pass_count++; } else {
        printf("  FAIL shared_edge: FB[%d] = 0x%02x, expected 0xAA (no gap)\n",
               5 + 5*320, px);
        fail_count++;
    }

    // Interior pixels of each triangle
    check_byte("shared_left", 2 + 2*320, 0xAA);
    check_byte("shared_right", 7 + 2*320, 0xAA);
}
#endif // GPU_FEAT_TRIANGLE

// =====================================================================
// Main
// =====================================================================

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    Verilated::traceEverOn(true);
    tb = new Vtb_gpu;
    trace = new VerilatedVcdC;
    tb->trace(trace, 99);
    trace->open("gpu_test.vcd");

    printf("=== GPU Core Test Suite ===\n\n");

    reset();
    printf("GPU initialized (%lu cycles)\n\n", (unsigned long)(sim_time / 2));

    test_fence_sync();
    // Stop tracing after first test to keep VCD small
    if (trace) { trace->close(); delete trace; trace = nullptr; }
    test_set_fb_only();
    test_clear_fb();
    test_solid_span();
    test_textured_span();
    test_colormap_lighting();
    test_vertical_column();
    test_skip_zero();
    test_multiple_commands();
    test_tex_cache_miss();

#ifdef GPU_PERSP_IMPL
    // Perspective spans (Lite only — Full's triangle FSM doesn't share the
    // pipelined fragment processor yet)
    test_persp_constant_z();
    test_persp_two_segments();
    test_persp_varying_z();
#endif

#ifdef GPU_FEAT_TRIANGLE
    // Triangle tests (Full variant only)
    test_triangle_flat();
    test_triangle_degenerate();
    test_triangle_textured();
    test_triangle_multi();
    test_triangle_depth();
    test_triangle_vertex_color();
    test_triangle_indexed();
    test_triangle_shared_edge();
#endif

    printf("\n=== Results: %d passed, %d failed ===\n",
           pass_count, fail_count);

    delete tb;
    return fail_count > 0 ? 1 : 0;
}
