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

// Upload `count` bytes to the colormap starting at byte `start`.
// GPU_CMAP_DATA is word-oriented (4 bytes stored per write, addr += 4).
// `start` and `count` must be 4-aligned.
static void cmap_upload_bytes(uint32_t start, const uint8_t *bytes, int count) {
    mmio_write(8, start);
    for (int i = 0; i < count; i += 4) {
        uint32_t w = (uint32_t)bytes[i]
                  | ((uint32_t)bytes[i + 1] << 8)
                  | ((uint32_t)bytes[i + 2] << 16)
                  | ((uint32_t)bytes[i + 3] << 24);
        mmio_write(9, w);
    }
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
    { uint8_t cm[256]; for (int i = 0; i < 256; i++) cm[i] = (uint8_t)i; cmap_upload_bytes(0, cm, 256); }

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
    { uint8_t cm[256]; for (int i = 0; i < 256; i++) cm[i] = (uint8_t)i; cmap_upload_bytes(0, cm, 256); }

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
    { uint8_t cm[512]; for (int i = 0; i < 256; i++) cm[i] = (uint8_t)i; for (int i = 0; i < 256; i++) cm[256+i] = 0x77; cmap_upload_bytes(0, cm, 512); }

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
    { uint8_t cm[256]; for (int i = 0; i < 256; i++) cm[i] = (uint8_t)i; cmap_upload_bytes(0, cm, 256); }

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
    { uint8_t cm[256]; for (int i = 0; i < 256; i++) cm[i] = (uint8_t)i; cmap_upload_bytes(0, cm, 256); }

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
    { uint8_t cm[256]; for (int i = 0; i < 256; i++) cm[i] = (uint8_t)i; cmap_upload_bytes(0, cm, 256); }

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
    { uint8_t cm[256]; for (int i = 0; i < 256; i++) cm[i] = (uint8_t)i; cmap_upload_bytes(0, cm, 256); }

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
    { uint8_t _cm[256]; for (int i = 0; i < 256; i++) _cm[i] = (uint8_t)i; cmap_upload_bytes(0, _cm, 256); }

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

    { uint8_t _cm[256]; for (int i = 0; i < 256; i++) _cm[i] = (uint8_t)i; cmap_upload_bytes(0, _cm, 256); }

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

    { uint8_t _cm[256]; for (int i = 0; i < 256; i++) _cm[i] = (uint8_t)i; cmap_upload_bytes(0, _cm, 256); }

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

// ============================================================
// Test P4: Mid-segment curvature error (Issue #1 from review)
// ------------------------------------------------------------
// Sets up a 16-pixel span with enough 1/z curvature that the piecewise-
// affine interp between endpoints differs from TRUE perspective by more
// than one texel at mid-segment pixels. Currently fails with the fixed
// 16-pixel subdivision; passes when subdivision shrinks to 8 or less.
//
//   zinv: 1.0 → 0.5 over 16 pixels  (z: 1.0 → 2.0)
//   sZ  : 0   → 1.0 over 16 pixels  (s/z ramp)
//   → endpoint s_0 = 0, s_16 = 2.0
//   → affine slope = 0.125 per pixel
// TRUE s at pixel x = (x * sZstep) / (zinv_0 + x * zinv_step)
//   x=4 : (0.25) / (0.875) = 0.286 → texel 0
//   x=8 : (0.5)  / (0.75)  = 0.667 → texel 0       ← 16-px interp gives 1.0
//   x=10: (0.625)/ (0.6875)= 0.909 → texel 0       ← 16-px interp gives 1.25
//   x=12: (0.75) / (0.625) = 1.200 → texel 1
//   x=15: (0.9375)/(0.53125)= 1.765 → texel 1
// Affine interp pixel 8 = 1.0 → texel 1 ≠ true texel 0   (VISIBLE DEFORM)
// ============================================================
static void test_persp_curvature_accuracy() {
    printf("TEST: Persp span — mid-segment curvature accuracy\n");
    gpu_init();

    { uint8_t _cm[256]; for (int i = 0; i < 256; i++) _cm[i] = (uint8_t)i; cmap_upload_bytes(0, _cm, 256); }

    ring_cmd(0x23, 2); ring_write(FB_BASE_BYTE); ring_write(320);
    ring_cmd(0x10, 2); ring_write((1 << 16) | 0x00); ring_write(0);

    // Texture: 16 bytes, texel[i] = i. Identity colormap gives FB[x] = s_int(x).
    for (int w = 0; w < 4; w++) {
        uint32_t v = (w*4) | ((w*4+1) << 8) | ((w*4+2) << 16) | ((w*4+3) << 24);
        sdram_write((TEX_BASE_BYTE >> 2) + w, v);
    }

    // zinv: 1.0 → 0.5 over 16 pixels. zinv_step = -0.5/16 = -0x800 in 16.16.
    // sZ:   0   → 1.0. sZstep = 1.0/16 = 0x1000 in 16.16.
    persp_draw_span(FB_BASE_BYTE, TEX_BASE_BYTE,
                    /*count*/16, /*light*/0, /*tex_width*/16,
                    /*sdivz*/0,         /*tdivz*/0,
                    /*zi_persp*/0x10000,
                    /*sdivz_step*/0x1000, /*tdivz_step*/0,
                    /*zi_step*/-0x800);
    check("persp_curv_done", gpu_finish() ? 1 : 0, 1);

    // Endpoints must be exact by construction (anchors).
    check_byte("persp_curv_px0",  0,  0);    // s=0
    check_byte("persp_curv_px15", 15, 1);    // s≈1.77 → texel 1 (both schemes)

    // Mid-segment must match TRUE perspective, NOT the affine extrapolation.
    // With 16-px subdivision, pixels 8-10 currently over-report by 1 texel.
    check_byte("persp_curv_px8",  8,  0);    // true: 0.667; affine16: 1.0
    check_byte("persp_curv_px10", 10, 0);    // true: 0.909; affine16: 1.25
}

// ============================================================
// Test P5: Reciprocal LUT handles small |zinv| (Issue #3)
// ------------------------------------------------------------
// At |sp_zinv| ≤ 2 (as 32-bit int; z ≥ 32768), the scale-back shift in
// PSS_MUL_S overflows the 32-bit recip_q16 into the sign bit (or to 0).
// Subsequent sZ×recip product produces wildly wrong s (often zero).
//
// Realistically unreachable in Quake/BUILD (clip distance < 8192), but we
// want a defined-behavior floor. The expected fix is saturation to
// INT32_MAX when the shift would overflow.
//
// Setup: zinv = 4 (z ≈ 16384, borderline but no overflow). sZ = 0.
//   → s = 0 × 16384 = 0. Output texel 0 across the span.
// If LUT path overflows and produces negative recip, sZ=0 still gives 0,
// but any nonzero sZ would flip sign. So pair with sZ = small positive:
// sZ=0x0040 (= 1/1024 in 16.16), sZstep = 0. True s = (1/1024) × 16384 = 16.
// → texel 16 mod 16 = 0. Hmm — need different tex_width.
// ============================================================
static void test_persp_small_zinv() {
    printf("TEST: Persp span — small |zinv| LUT scale (z ≈ 16384)\n");
    gpu_init();

    { uint8_t _cm[256]; for (int i = 0; i < 256; i++) _cm[i] = (uint8_t)i; cmap_upload_bytes(0, _cm, 256); }

    ring_cmd(0x23, 2); ring_write(FB_BASE_BYTE); ring_write(320);
    ring_cmd(0x10, 2); ring_write((1 << 16) | 0x00); ring_write(0);

    // 64-byte texture, identity
    for (int w = 0; w < 16; w++) {
        uint32_t v = (w*4) | ((w*4+1) << 8) | ((w*4+2) << 16) | ((w*4+3) << 24);
        sdram_write((TEX_BASE_BYTE >> 2) + w, v);
    }

    // zinv_persp = 4 (Q16.16 = 6.1e-5, z ≈ 16384). zinv_step = 0 (constant z).
    // sZ = 0, sZ_step: pick so s_16 = small value. s = sZ × z = sZstep × 16 × 16384.
    // For s_16 = 16: sZstep × 16 × 16384 = 16 → sZstep = 1/16384 = 0x4 in 16.16.
    // Linear slope per pixel = 16/16 = 1.0 → texel(x) = x.
    persp_draw_span(FB_BASE_BYTE, TEX_BASE_BYTE,
                    /*count*/16, /*light*/0, /*tex_width*/64,
                    /*sdivz*/0,         /*tdivz*/0,
                    /*zi_persp*/4,
                    /*sdivz_step*/4,    /*tdivz_step*/0,
                    /*zi_step*/0);
    check("persp_szinv_done", gpu_finish() ? 1 : 0, 1);

    // With constant zinv, linear interp IS exact perspective.
    // s_int(x) = x → texel x. Any LUT overflow would collapse s to 0.
    check_byte("persp_szinv_px0",  0,  0);
    check_byte("persp_szinv_px4",  4,  4);
    check_byte("persp_szinv_px8",  8,  8);
    check_byte("persp_szinv_px15", 15, 15);
}

// ============================================================
// Test P6: Slope rounding bias (Issue #2)
// ------------------------------------------------------------
// Arithmetic `(diff) >>> 4` floors toward -∞. After 16 steps we reach
// anchor + (diff & ~15) instead of anchor + diff. Worst-case error:
// 15 Q16.16 units = 2.3e-4 texels per segment. Over one segment this
// is invisible, but with round-to-nearest we'd reach the endpoint on
// average and the bias would be zero across many segments.
//
// This test is tight-tolerance: set up so the affine sstep's fractional
// remainder is exactly 15 (worst case), then check the computed
// persp_s_end (= next anchor) matches the extrapolated sp_s to within
// 1 texel integer at the segment boundary. Serves as a regression watch
// more than a bug-surface test; currently passes with healthy margin.
// ============================================================
static void test_persp_slope_rounding() {
    printf("TEST: Persp span — slope-divide rounding bias\n");
    gpu_init();

    { uint8_t _cm[256]; for (int i = 0; i < 256; i++) _cm[i] = (uint8_t)i; cmap_upload_bytes(0, _cm, 256); }

    ring_cmd(0x23, 2); ring_write(FB_BASE_BYTE); ring_write(320);
    ring_cmd(0x10, 2); ring_write((1 << 16) | 0x00); ring_write(0);

    for (int w = 0; w < 16; w++) {
        uint32_t v = (w*4) | ((w*4+1) << 8) | ((w*4+2) << 16) | ((w*4+3) << 24);
        sdram_write((TEX_BASE_BYTE >> 2) + w, v);
    }

    // 32 pixel span crossing segment boundary. Constant zinv = 1.0 so slope
    // is fully determined by sZstep. Pick sZstep = 0x000F (fractional mod16=15)
    // so the >>> 4 floor discards 15/65536 per segment.
    //   s_16 = 16 × 0x000F / 0x10000 = 0x00F0/65536 ≈ 0.00366
    //   texel at pixel 16 should be 0 (s < 1) after both anchor and linear math.
    persp_draw_span(FB_BASE_BYTE, TEX_BASE_BYTE,
                    /*count*/32, /*light*/0, /*tex_width*/32,
                    /*sdivz*/0,         /*tdivz*/0,
                    /*zi_persp*/0x10000,
                    /*sdivz_step*/0x000F, /*tdivz_step*/0,
                    /*zi_step*/0);
    check("persp_slope_done", gpu_finish() ? 1 : 0, 1);
    // All 32 pixels in texel 0 (s << 1 throughout); rounding bias stays sub-texel.
    for (int i = 0; i < 32; i++) {
        char name[32]; snprintf(name, sizeof(name), "persp_slope_px%d", i);
        check_byte(name, i, 0);
    }
}

// ============================================================
// Test P7: Negative zinv defensive behavior (Issue #4)
// ------------------------------------------------------------
// If the CPU sends a span whose 1/z crosses zero (e.g. an un-culled
// near-clip polygon), the `abs(sp_zinv)` in PSS_RECIP_NA drops the sign
// — we recip |zinv| and multiply by sZ, losing one sign bit. There is
// no defined behavior today; apps must cull before submission.
//
// This test just confirms the path doesn't hang / assert. Output values
// are not checked against a spec — only that the span completes and the
// fence returns.
// ============================================================
static void test_persp_negative_zinv() {
    printf("TEST: Persp span — negative zinv (defensive/no-hang)\n");
    gpu_init();

    { uint8_t _cm[256]; for (int i = 0; i < 256; i++) _cm[i] = (uint8_t)i; cmap_upload_bytes(0, _cm, 256); }

    ring_cmd(0x23, 2); ring_write(FB_BASE_BYTE); ring_write(320);
    ring_cmd(0x10, 2); ring_write((1 << 16) | 0x00); ring_write(0);

    for (int w = 0; w < 4; w++) {
        uint32_t v = (w*4) | ((w*4+1) << 8) | ((w*4+2) << 16) | ((w*4+3) << 24);
        sdram_write((TEX_BASE_BYTE >> 2) + w, v);
    }

    // Start zinv = 0.5 (z=2), step very negative so zinv crosses zero by pixel 8.
    persp_draw_span(FB_BASE_BYTE, TEX_BASE_BYTE,
                    /*count*/16, /*light*/0, /*tex_width*/16,
                    /*sdivz*/0,         /*tdivz*/0,
                    /*zi_persp*/0x08000,
                    /*sdivz_step*/0x1000, /*tdivz_step*/0,
                    /*zi_step*/-0x1000);  // zinv = 0.5 → -0.5 across the span
    // Only require the span COMPLETES. Values are undefined-behavior.
    check("persp_negz_done", gpu_finish() ? 1 : 0, 1);
}
#endif // GPU_PERSP_IMPL

// Set depth test via CMD_SET_DEPTH_FUNC (available in all variants)
static void ring_depth_func(uint32_t func) {
    ring_cmd(0x21, 1);
    ring_write(func);
}

// Span-based depth test — exercises the GEQUAL / GREATER / NOTEQUAL
// compare ops added alongside the original LESS / LEQUAL / EQUAL.
// LITE-compatible (no triangle path).
static void ring_span_flat(uint32_t fb_off, uint32_t tex_word,
                           uint32_t count, uint32_t flags,
                           uint32_t z_addr, uint32_t zi) {
    // Each span uses its own tex slot — texture cache would hit stale
    // data if we reused a single tex address across backdoor rewrites.
    static uint32_t tex_slot = 0;
    uint32_t tex_base = TEX_BASE_BYTE + (tex_slot * 64);
    tex_slot = (tex_slot + 1) & 0x3F;
    sdram_write(tex_base >> 2, tex_word);
    ring_cmd(0x40, 18);  // CMD_DRAW_SPAN
    ring_write(FB_BASE_BYTE + fb_off);
    ring_write(tex_base);
    ring_write(0); ring_write(0);       // s, t
    ring_write(0); ring_write(0);       // sstep, tstep
    ring_write((count << 16) | (0 << 8) | flags);
    ring_write((1 << 16) | 1);          // fb_stride=1, tex_width=1
    ring_write(0);                       // tex_shift=0, tex_bits=0
    ring_write(z_addr);
    ring_write(zi);
    ring_write(0);                       // zistep=0 (flat z across span)
    for (int i = 0; i < 6; i++) ring_write(0);  // persp params unused
}

static void test_span_depth_gequal() {
    printf("TEST: Span depth compare — GEQUAL / GREATER / NOTEQUAL\n");
    gpu_init();
    { uint8_t _cm[256]; for (int i = 0; i < 256; i++) _cm[i] = (uint8_t)i; cmap_upload_bytes(0, _cm, 256); }

    ring_cmd(0x23, 2); ring_write(FB_BASE_BYTE); ring_write(320);
    ring_cmd(0x24, 2); ring_write(0); ring_write(640);  // ZB base=0

    // Order matters — we put the PASS span first so its write+z-update
    // becomes the occluder, then the FAIL span must NOT overwrite it.
    // This actually discriminates "depth works" vs "depth silently always
    // writes" (which would show the fail color instead of the pass color).

    // --- GEQUAL ---------------------------------------------------------
    ring_depth_func(5);  // GEQUAL
    ring_cmd(0x10, 2); ring_write((3 << 16) | 0x00); ring_write(0x4000);
    // z=0x8000: pass GEQUAL (0x8000 >= 0x4000). Writes 0xBB + ZB=0x8000.
    ring_span_flat(0, 0xBBBBBBBB, 8, 0x19, 0, 0x80000000);
    // z=0x2000: fail (0x2000 >= 0x8000 is false). Must not overwrite.
    ring_span_flat(0, 0xAAAAAAAA, 8, 0x19, 0, 0x20000000);
    check("span_depth_gequal_done", gpu_finish() ? 1 : 0, 1);
    for (int i = 0; i < 8; i++) {
        char name[32]; snprintf(name, sizeof(name), "gequal_px%d", i);
        check_byte(name, i, 0xBB);
    }

    // --- GREATER (strict) -----------------------------------------------
    // ZB = 0x8000 (from GEQUAL pass). Equal-z must FAIL under GREATER.
    ring_depth_func(6);
    // z=0x9000: pass. Writes 0xDD + ZB=0x9000.
    ring_span_flat(0, 0xDDDDDDDD, 8, 0x19, 0, 0x90000000);
    // z=0x9000: equal — fail GREATER. Must not overwrite.
    ring_span_flat(0, 0xCCCCCCCC, 8, 0x19, 0, 0x90000000);
    check("span_depth_greater_done", gpu_finish() ? 1 : 0, 1);
    for (int i = 0; i < 8; i++) {
        char name[32]; snprintf(name, sizeof(name), "greater_px%d", i);
        check_byte(name, i, 0xDD);
    }

    // --- NOTEQUAL -------------------------------------------------------
    // ZB = 0x9000. Non-equal passes, equal fails.
    ring_depth_func(7);
    // z=0x1000: pass NOTEQUAL. Writes 0x77 + ZB=0x1000.
    ring_span_flat(0, 0x77777777, 8, 0x19, 0, 0x10000000);
    // z=0x1000: equal — fail. Must not overwrite.
    ring_span_flat(0, 0xEEEEEEEE, 8, 0x19, 0, 0x10000000);
    check("span_depth_notequal_done", gpu_finish() ? 1 : 0, 1);
    for (int i = 0; i < 8; i++) {
        char name[32]; snprintf(name, sizeof(name), "notequal_px%d", i);
        check_byte(name, i, 0x77);
    }

    // --- LESS regression (make sure we didn't break existing ops) -------
    ring_depth_func(2);
    // z=0x0800: !(0x0800 < 0x1000) = false, so pass. Writes 0x44 + ZB=0x0800.
    ring_span_flat(0, 0x44444444, 8, 0x19, 0, 0x08000000);
    // z=0x2000: !(0x2000 < 0x0800) = true, so fail. Must not overwrite.
    ring_span_flat(0, 0x33333333, 8, 0x19, 0, 0x20000000);
    check("span_depth_less_done", gpu_finish() ? 1 : 0, 1);
    for (int i = 0; i < 8; i++) {
        char name[32]; snprintf(name, sizeof(name), "less_px%d", i);
        check_byte(name, i, 0x44);
    }
}

// Verify the span unit handles a non-power-of-2 tex_width (Quake console
// surface = 320×200, alias skins = arbitrary). Hits the multiply-mode path
// (sp_tex_width != 0): addr = tex_base + t_int * tex_width + s_int.
static void test_span_tex_width_nonpow2(void) {
    printf("TEST: Span tex_width=300 (non-power-of-2)\n");
    gpu_init();
    { uint8_t _cm[256]; for (int i = 0; i < 256; i++) _cm[i] = (uint8_t)i; cmap_upload_bytes(0, _cm, 256); }

    // Build a 300-wide × 2-tall "texture" in SDRAM: row 0 = (x & 0xFF),
    // row 1 = ((x + 128) & 0xFF). Packed 4 bytes per word.
    const uint32_t W = 300;
    uint32_t tex_base = TEX_BASE_BYTE;
    for (uint32_t row = 0; row < 2; row++) {
        uint32_t row_base = tex_base + row * W;
        for (uint32_t x = 0; x < W; x += 4) {
            uint8_t b0 = (uint8_t)((x + 0 + (row ? 128 : 0)) & 0xFF);
            uint8_t b1 = (uint8_t)((x + 1 + (row ? 128 : 0)) & 0xFF);
            uint8_t b2 = (uint8_t)((x + 2 + (row ? 128 : 0)) & 0xFF);
            uint8_t b3 = (uint8_t)((x + 3 + (row ? 128 : 0)) & 0xFF);
            sdram_write((row_base + x) >> 2, b0 | (b1<<8) | (b2<<16) | (b3<<24));
        }
    }

    ring_cmd(0x23, 2); ring_write(FB_BASE_BYTE); ring_write(320);
    ring_cmd(0x10, 2); ring_write((1 << 16) | 0x00); ring_write(0);  // clear FB
    ring_depth_func(0);  // no depth

    // Row 0 span: s=0, t=0, sstep=1.0 → pixels should read tex[0..7] = 0..7.
    ring_cmd(0x40, 18);
    ring_write(FB_BASE_BYTE);
    ring_write(tex_base);
    ring_write(0); ring_write(0);                          // s=0, t=0
    ring_write(0x00010000); ring_write(0);                 // sstep=1.0, tstep=0
    ring_write((8 << 16) | (0 << 8) | 0x01);               // 8 px, colormap
    ring_write((1 << 16) | W);                             // fb_stride=1, tex_width=300
    ring_write(0);                                          // tex_shift/bits ignored
    ring_write(0); ring_write(0); ring_write(0);           // z unused
    for (int i = 0; i < 6; i++) ring_write(0);

    // Row 1 span: s=100, t=1, sstep=1.0 → pixels should read tex[(100+128..107+128) & 0xFF].
    // addr = tex_base + 1 * 300 + (100..107), values = (100..107 + 128) & 0xFF = 228..235 (0xE4..0xEB).
    ring_cmd(0x40, 18);
    ring_write(FB_BASE_BYTE + 8);
    ring_write(tex_base);
    ring_write(100 << 16); ring_write(1 << 16);            // s=100.0, t=1.0
    ring_write(0x00010000); ring_write(0);                 // sstep=1.0, tstep=0
    ring_write((8 << 16) | (0 << 8) | 0x01);
    ring_write((1 << 16) | W);
    ring_write(0);
    ring_write(0); ring_write(0); ring_write(0);
    for (int i = 0; i < 6; i++) ring_write(0);

    check("span_w300_done", gpu_finish() ? 1 : 0, 1);

    // Row 0 checks: expect 0..7 at FB[0..7]
    for (int i = 0; i < 8; i++) {
        char name[32]; snprintf(name, sizeof(name), "w300_r0_px%d", i);
        check_byte(name, i, (uint8_t)i);
    }
    // Row 1 checks: expect 0xE4..0xEB at FB[8..15]
    for (int i = 0; i < 8; i++) {
        char name[32]; snprintf(name, sizeof(name), "w300_r1_px%d", i);
        check_byte(name, 8 + i, (uint8_t)(0xE4 + i));
    }
}

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

// =====================================================================
// Triangle Tests
// =====================================================================

// Test T1: Simple flat-color triangle (no texture stepping)
// Draws a small right triangle covering known pixels
static void test_triangle_flat() {
    printf("TEST: Flat-color triangle\n");

    gpu_init();

    // Identity colormap for light=0
    { uint8_t cm[256]; for (int i = 0; i < 256; i++) cm[i] = (uint8_t)i; cmap_upload_bytes(0, cm, 256); }

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
    { uint8_t cm[256]; for (int i = 0; i < 256; i++) cm[i] = (uint8_t)i; cmap_upload_bytes(0, cm, 256); }

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

    { uint8_t cm[256]; for (int i = 0; i < 256; i++) cm[i] = (uint8_t)i; cmap_upload_bytes(0, cm, 256); }

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

    { uint8_t _cm[256]; for (int i = 0; i < 256; i++) _cm[i] = (uint8_t)i; cmap_upload_bytes(0, _cm, 256); }

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
    {
        static uint8_t cm[64 * 256];
        for (int light = 0; light < 64; light++)
            for (int i = 0; i < 256; i++)
                cm[light * 256 + i] = (uint8_t)light;
        cmap_upload_bytes(0, cm, 64 * 256);
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

    // Phase 4d — Gouraud now interpolates light per-pixel.  Expected
    // values: light(x, 0) = (x * 30) / 10 = 3*x.  cmap[light * 256 +
    // tex_value] = light, so FB[x, 0] should be 3*x ± LUT precision.
    // Tolerance is ±1 because the recip LUT loses one unit on this
    // particular determinant.
    for (int x = 0; x < 10; x++) {
        uint8_t v = sdram_read_byte(FB_BASE_BYTE + x + 0*320);
        uint8_t expected = (uint8_t)(3 * x);
        int diff = (int)v - (int)expected;
        if (diff < 0) diff = -diff;
        if (diff <= 1) { pass_count++; }
        else {
            printf("  FAIL vcol_px(%d,0) = 0x%02x, expected ~0x%02x (±1)\n",
                   x, v, expected);
            fail_count++;
        }
    }
}

// Test T8: Two adjacent triangles (shared edge — no gap)
static void test_triangle_shared_edge() {
    printf("TEST: Shared edge (no gap between triangles)\n");

    gpu_init();

    { uint8_t _cm[256]; for (int i = 0; i < 256; i++) _cm[i] = (uint8_t)i; cmap_upload_bytes(0, _cm, 256); }

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
// Phase 4c.2 — perspective pre-multiply state (dormant consumer).
//
// Submitting a triangle with non-unit w now routes through
// S_TRI_PERSP_PREMUL before S_TRI_SETUP.  Until 4c.3 wires v_sw/v_tw
// into the gradient mux, those regs are unused and the triangle
// renders affinely.
//
// Smoke test: submit a perspective triangle (w = 0x20000) and verify
// the FSM completes without hanging, AND that some pixel inside the
// triangle was actually written.  Guards against the new state
// stalling forward progress.
static void test_triangle_persp_premul_dormant(void) {
    printf("TEST: Triangle perspective premul state (4c.2 — dormant)\n");

    gpu_init();
    { uint8_t cm[256]; for (int i = 0; i < 256; i++) cm[i] = (uint8_t)i;
      cmap_upload_bytes(0, cm, 256); }
    ring_cmd(0x23, 2);
    ring_write(FB_BASE_BYTE);
    ring_write(320);
    ring_cmd(0x10, 2);
    ring_write((1 << 16) | 0x00);
    ring_write(0);
    sdram_write(TEX_BASE_BYTE >> 2, 0xDDCCBBAA);
    ring_bind_texture(TEX_BASE_BYTE, 1, 1);

    auto write_v = [&](int16_t x, int16_t y, int32_t s, int32_t t, int32_t w) {
        ring_write(((uint32_t)(uint16_t)x << 16) | (uint16_t)y);
        ring_write(0);                 // z
        ring_write((uint32_t)s);
        ring_write((uint32_t)t);
        ring_write((uint32_t)w);
        ring_write(0);                 // r
    };

    // CCW triangle with non-unit w → premul state runs.  1×1 texture
    // is constant 0xAA so any inside pixel must read 0xAA.
    ring_cmd(0x30, 19);
    ring_write(3);
    write_v(8*16, 0,    0, 0, 0x00020000);
    write_v(0,    8*16, 0, 0, 0x00020000);
    write_v(0,    0,    0, 0, 0x00020000);

    bool ok = gpu_finish();
    check("tri_premul_persp_done", ok ? 1 : 0, 1);

    // Pixel (1, 1) is firmly inside; with the affine path still being
    // the live one, it must have been written from the 1×1 texture.
    check_byte("tri_premul_persp_pixel_written", 1 + 1 * 320, 0xAA);
}

// Phase 4c.3 / 4c.4 — perspective-correct triangle texture mapping.
//
// Renders the SAME geometry twice: once affine (w_i = 1.0 per vertex),
// once perspective (one vertex has w = 0.5 to push it "far").  With
// the SPAN_PERSP path now wired up for triangles, the two passes
// must produce DIFFERENT framebuffers — at least at the midline of
// the shared edge between the near and far vertices.
//
// Pre-fix: persp_active was hard-coded 0 for triangles, so the two
// passes produced identical output.  Now persp_active = 1 when any
// v_w != 0x10000, and the existing perspective-span machinery does
// the divide.
static void test_triangle_persp_vs_affine(void) {
    printf("TEST: Triangle perspective vs affine (4c.4 — visible)\n");

    auto render_one = [&](int32_t w0, int32_t w1, int32_t w2) {
        gpu_init();
        { uint8_t cm[256]; for (int i = 0; i < 256; i++) cm[i] = (uint8_t)i;
          cmap_upload_bytes(0, cm, 256); }
        ring_cmd(0x23, 2);
        ring_write(FB_BASE_BYTE);
        ring_write(320);
        ring_cmd(0x10, 2);
        ring_write((1 << 16) | 0x00);
        ring_write(0);
        // 16x16 distinctive texture: byte = (t<<4) | s.
        for (int t = 0; t < 16; t++) {
            for (int s = 0; s < 16; s += 4) {
                uint32_t w = ((uint32_t)((t << 4) | (s + 3)) << 24)
                           | ((uint32_t)((t << 4) | (s + 2)) << 16)
                           | ((uint32_t)((t << 4) | (s + 1)) <<  8)
                           | ((uint32_t)((t << 4) | (s + 0)) <<  0);
                sdram_write((TEX_BASE_BYTE >> 2) + (t * 16 + s) / 4, w);
            }
        }
        ring_bind_texture(TEX_BASE_BYTE, 16, 16);

        auto write_v = [&](int16_t x, int16_t y, int32_t s, int32_t t, int32_t w) {
            ring_write(((uint32_t)(uint16_t)x << 16) | (uint16_t)y);
            ring_write(0);
            ring_write((uint32_t)s);
            ring_write((uint32_t)t);
            ring_write((uint32_t)w);
            ring_write(0);
        };

        // CCW right triangle covering pixels (0..7, 0..7).
        ring_cmd(0x30, 19);
        ring_write(3);
        write_v(0,    0,           0,        0, w0);  // v0 (top-left)
        write_v(8*16, 0,    8 << 16,        0, w1);  // v1 (top-right)
        write_v(0,    8*16,       0, 8 << 16, w2);  // v2 (bottom-left)

        return gpu_finish();
    };

    // Affine reference run.
    bool ok_a = render_one(0x00010000, 0x00010000, 0x00010000);
    check("tri_persp_affine_done", ok_a ? 1 : 0, 1);
    uint8_t fb_aff[8][8];
    for (int y = 0; y < 8; y++)
        for (int x = 0; x < 8; x++)
            fb_aff[y][x] = sdram_read_byte(FB_BASE_BYTE + x + y * 320);

    // Perspective run: v1 has 1/W = 0.5 (= W = 2.0, "far").  The other
    // two stay at W = 1 ("near").  Texcoords land differently along
    // the v0→v1 edge.
    bool ok_p = render_one(0x00010000, 0x00008000, 0x00010000);
    check("tri_persp_persp_done", ok_p ? 1 : 0, 1);

    // Count differing pixels inside the triangle.  Diagonal y < 8-x
    // (open right edge) is the rough interior.
    int diff_count = 0;
    for (int y = 0; y < 8; y++) {
        for (int x = 0; x < 8 - y; x++) {
            uint8_t aff = fb_aff[y][x];
            uint8_t per = sdram_read_byte(FB_BASE_BYTE + x + y * 320);
            if (aff != per) diff_count++;
        }
    }

    // A non-trivial number of pixels must differ.  The exact count
    // depends on the fragment pipe's segment granularity; what matters
    // is that perspective produces DIFFERENT output from affine when
    // w varies between vertices.  Lower bound: a few pixels along the
    // top edge near v1 should sample a different texel.
    if (diff_count >= 4) {
        pass_count++;
        printf("  OK  tri_persp_diff_count=%d (>= 4)\n", diff_count);
    } else {
        fail_count++;
        printf("  FAIL tri_persp_diff_count=%d, expected >= 4\n", diff_count);
    }
}

// Phase 4a — bbox-origin attribute init.
//
// Triangle setup currently initialises tri_row_z/s/t at v0 instead of at the
// bbox origin (xmin, ymin).  When v0 is not at the bbox corner — e.g. v0 is
// the top-right of a right triangle whose bbox-origin is v1 — the per-pixel
// walk drifts by a constant (xmin - v0.x) * grad_dx + (ymin - v0.y) * grad_dy.
//
// This test renders a 16x16-textured right triangle whose v0 sits at the
// top-right corner.  v0.y == ymin (no Y bias), but v0.x != xmin so every
// pixel's S coordinate is off by (xmin - v0.x) * grad_s_dx == 4.  Texture
// is filled with byte = (t<<4)|s so a 4-step S offset is unambiguous.
//
// Pre-fix: pixel (4, 4) reads texel(s=0, t=0) = 0x00 instead of 0x04.
// Post-fix: pixel (4, 4) reads texel(s=4, t=0) = 0x04.
static void test_triangle_bbox_init(void) {
    printf("TEST: Triangle bbox-origin attribute init (v0 != bbox.origin)\n");

    gpu_init();

    { uint8_t cm[256]; for (int i = 0; i < 256; i++) cm[i] = (uint8_t)i;
      cmap_upload_bytes(0, cm, 256); }

    ring_cmd(0x23, 2);
    ring_write(FB_BASE_BYTE);
    ring_write(320);

    ring_cmd(0x10, 2);
    ring_write((1 << 16) | 0x00);
    ring_write(0);

    // 16x16 texture: byte at (s, t) = (t << 4) | s.
    for (int t = 0; t < 16; t++) {
        for (int s = 0; s < 16; s += 4) {
            uint32_t w = ((uint32_t)((t << 4) | (s + 3)) << 24)
                       | ((uint32_t)((t << 4) | (s + 2)) << 16)
                       | ((uint32_t)((t << 4) | (s + 1)) <<  8)
                       | ((uint32_t)((t << 4) | (s + 0)) <<  0);
            sdram_write((TEX_BASE_BYTE >> 2) + (t * 16 + s) / 4, w);
        }
    }
    ring_bind_texture(TEX_BASE_BYTE, 16, 16);

    // CCW right triangle, v0 NOT at bbox origin.
    //   v0 = (8, 0) tex(s=8, t=0)   ← top-right (xmax, ymin) — non-origin v0
    //   v1 = (0, 8) tex(s=0, t=8)   ← bottom-left
    //   v2 = (0, 0) tex(s=0, t=0)   ← bbox origin (xmin, ymin)
    // Cross-product (v1-v0) × (v2-v0) = +64 → CCW (no winding flip).
    // bbox = (0..8, 0..8).  delta_x_subpix = -128 (= -8 pixels), delta_y = 0.
    //
    //   grad_s_dx = +1/pixel, grad_s_dy = 0.    Correct s(x,y) = x.
    //   grad_t_dx = 0,        grad_t_dy = +1/pixel.  Correct t(x,y) = y.
    //
    //   Buggy at (xmin, ymin) initialises tri_row_s = v0.s = 8, t = 0.
    //   Buggy s(x,y) = 8 + x (mod 16, since walk continues from v0.s).
    //   Buggy t(x,y) = y      (matches correct because v0.y == ymin).
    ring_cmd(0x30, 19);
    ring_write(3);
    ring_write_vertex(8*16, 0*16, 0, 8 << 16,        0, 0);  // v0
    ring_write_vertex(0*16, 8*16, 0,        0, 8 << 16, 0);  // v1
    ring_write_vertex(0*16, 0*16, 0,        0,        0, 0);  // v2

    bool ok = gpu_finish();
    check("tri_bbox_init_done", ok ? 1 : 0, 1);

    // Pixel (1, 1) — interior near v2=(0,0).
    //   correct s = 1, t = 1 → texel = (1<<4)|1 = 0x11
    //   buggy   s = 9, t = 1 → 0x19
    check_byte("tri_bbox_init_1_1", 1 + 1 * 320, 0x11);

    // Pixel (2, 2).
    //   correct s = 2, t = 2 → 0x22
    //   buggy   s = 10, t = 2 → 0x2A
    check_byte("tri_bbox_init_2_2", 2 + 2 * 320, 0x22);

    // Pixel (3, 3).
    //   correct s = 3, t = 3 → 0x33
    //   buggy   s = 11, t = 3 → 0x3B
    check_byte("tri_bbox_init_3_3", 3 + 3 * 320, 0x33);
}
#endif // GPU_FEAT_TRIANGLE

// =====================================================================
// CMD_SET_SKIP_ZERO — color-key transparency for triangle span-emit
// ---------------------------------------------------------------------
// Sprites used to have their own primitive (CMD_DRAW_SPRITE, now removed).
// Apps emit 2 textured triangles instead; for color-key transparency
// (e.g. Duke3D's 0xFF sentinel) they set this global state bit first and
// the triangle rasterizer passes SKIP_ZERO through to the fragment pipe.
// =====================================================================
#ifdef GPU_FEAT_TRIANGLE
static void test_triangle_skip_zero(void) {
    printf("TEST: Triangle with SKIP_ZERO (color-key via triangles)\n");
    gpu_init();
    { uint8_t _cm[256]; for (int i = 0; i < 256; i++) _cm[i] = (uint8_t)i; cmap_upload_bytes(0, _cm, 256); }

    ring_cmd(0x23, 2); ring_write(FB_BASE_BYTE); ring_write(320);
    ring_cmd(0x10, 2); ring_write((1 << 16) | 0x22); ring_write(0);  // clear to 0x22

    // Enable color-key. 1 word payload, low bit = enable.
    ring_cmd(0x27, 1); ring_write(1);

    // Texture: 8×8, every row is {0x10, 0xFF, 0x20, 0xFF, 0x30, 0xFF, 0x40, 0xFF}.
    // Making it 8 tall ensures the triangle's t interpolation (t=1 at y=1,
    // t=2 at y=2, …) lands on valid rows that all have the same pattern.
    uint8_t cells[8] = {0x10, 0xFF, 0x20, 0xFF, 0x30, 0xFF, 0x40, 0xFF};
    for (int row = 0; row < 8; row++) {
        for (int x = 0; x < 8; x += 4) {
            uint32_t w = cells[x] | (cells[x+1] << 8) | (cells[x+2] << 16) | (cells[x+3] << 24);
            sdram_write((TEX_BASE_BYTE + row * 8 + x) >> 2, w);
        }
    }
    ring_bind_texture(TEX_BASE_BYTE, 8, 8);

    // Single full-covering triangle with s=0..7, t=0 at row 0.
    // Vertex positions in 12.4 sub-pixel format (shift left 4).
    // Triangle covers (0,0) to (7,0) and (0,1)-ish — use (0,0), (8,0), (0,8) so s stepping matches columns.
    ring_cmd(0x30, 19);
    ring_write(3);
    ring_write_vertex(0,         0,        0,       0,      0, 0);
    ring_write_vertex(8 * 16,    0,        0,       8 << 16,0, 0);  // s=8 at right
    ring_write_vertex(0,         8 * 16,   0,       0,      8 << 16, 0);

    check("tri_skipzero_done", gpu_finish() ? 1 : 0, 1);

    // Row 0 check: odd x (where src was 0xFF) stays at clear 0x22; even lands the texel.
    // NOTE: because the triangle spans from (0,0) to (8,0), pixel 0 at y=0 is on the edge.
    // Only check pixels we know are covered — y=1..6 is the safe interior.
    // For this test we check y=1 (well inside the triangle).
    check_byte("triCK_y1_x0", 0 + 1*320, 0x10);
    check_byte("triCK_y1_x1", 1 + 1*320, 0x22);  // transparent
    check_byte("triCK_y1_x2", 2 + 1*320, 0x20);
    check_byte("triCK_y1_x3", 3 + 1*320, 0x22);
    check_byte("triCK_y1_x4", 4 + 1*320, 0x30);
    check_byte("triCK_y1_x5", 5 + 1*320, 0x22);

    // Disable color-key for subsequent tests.
    ring_cmd(0x27, 1); ring_write(0);
    gpu_finish();
}

// Test T9: Back-to-back triangle stress — 8 textured triangles with
// varying orientations and sizes, rendered in rapid succession. Exercises
// the 2-cycle tri_ymin_x_stride pipeline (S_TRI_MUL_WAIT2) and the 7-cycle
// S_TRI_GRAD with grad_sub_r / dsp_p_shifted register stages. Modelled
// after gpudemo's Mode 1 rotating cube: multiple triangles per frame
// with triangles that change orientation each iteration.
static void test_triangle_back_to_back_many() {
    printf("TEST: 8 back-to-back textured triangles (pipeline stress)\n");

    gpu_init();

    { uint8_t cm[256]; for (int i = 0; i < 256; i++) cm[i] = (uint8_t)i; cmap_upload_bytes(0, cm, 256); }

    ring_cmd(0x23, 2);
    ring_write(FB_BASE_BYTE);
    ring_write(320);

    ring_cmd(0x10, 2);
    ring_write((1 << 16) | 0x00);
    ring_write(0);

    // 8×8 texture: each row is its row index × 0x10, so sampling at t=r
    // produces 0x10..0x80 and lets the gradient test land predictable
    // values even when S_TRI_GRAD pipelining shifts per-gradient timing.
    for (int row = 0; row < 8; row++) {
        uint32_t cell = (uint32_t)(0x10 * (row + 1));
        uint32_t w = cell | (cell << 8) | (cell << 16) | (cell << 24);
        sdram_write((TEX_BASE_BYTE + row * 8 + 0) >> 2, w);
        sdram_write((TEX_BASE_BYTE + row * 8 + 4) >> 2, w);
    }
    ring_bind_texture(TEX_BASE_BYTE, 8, 8);

    // 8 triangles placed across the screen at varying sizes. Each is a
    // right-triangle with different hypotenuse orientations, and their
    // tri_det values span a wide range (small ... large) to exercise
    // the pre-registered tri_det_is_small_r compare at the boundary.
    struct Tri { int x0, y0, x1, y1, x2, y2; };
    Tri tris[8] = {
        {  4,  4,  14,  4,   4, 14 },   // large right tri
        { 30,  4,  38,  4,  30, 12 },   // medium right tri
        { 50,  4,  55,  4,  50,  9 },   // small right tri
        { 70,  4,  85,  8,  70, 18 },   // oblique
        {  4, 30,  18, 30,  11, 42 },   // upward-pointing isoceles
        { 40, 30,  54, 30,  40, 44 },   // right tri, different orientation
        { 70, 30,  90, 45,  72, 50 },   // odd-shaped
        {100, 30, 115, 30, 100, 45 },   // right tri, larger
    };
    for (int i = 0; i < 8; i++) {
        ring_cmd(0x30, 19);
        ring_write(3);
        ring_write_vertex(tris[i].x0*16, tris[i].y0*16, 0, 0,       0,       0);
        ring_write_vertex(tris[i].x1*16, tris[i].y1*16, 0, 7 << 16, 0,       0);
        ring_write_vertex(tris[i].x2*16, tris[i].y2*16, 0, 0,       7 << 16, 0);
    }

    bool ok = gpu_finish(400000);
    check("tri_b2b_done", ok ? 1 : 0, 1);

    // Pick a pixel known to be interior for each triangle and verify
    // it's textured (non-zero). Exact texel depends on gradient rounding
    // so we just check for non-clear values — a pipeline bug would
    // silently drop pixels or render garbage off-screen.
    int sample_x[8] = { 6, 32, 51, 75,  8, 43, 78, 104 };
    int sample_y[8] = { 6,  6,  6,  9, 34, 34, 40,  35 };
    for (int i = 0; i < 8; i++) {
        uint8_t px = sdram_read_byte(FB_BASE_BYTE + sample_x[i] + sample_y[i] * 320);
        char name[32];
        snprintf(name, sizeof(name), "tri_b2b_t%d_inside", i);
        if (px != 0x00) {
            pass_count++;
        } else {
            printf("  FAIL %s: FB[%d,%d] = 0x%02x, expected non-zero\n",
                   name, sample_x[i], sample_y[i], px);
            fail_count++;
        }
    }
}

// Test T10: Texture cache flush + swap. After the M10K conversion of
// valid_mem, flush goes through a 1024-cycle S_INIT walk-clear instead
// of bulk-clearing a FF array. This test verifies: (a) cache returns
// new texture data after a flush when the texture base changes, and
// (b) rendering resumes cleanly after the S_INIT walk.
static void test_triangle_tex_flush_swap() {
    printf("TEST: Triangle with texture flush + swap\n");

    gpu_init();

    { uint8_t cm[256]; for (int i = 0; i < 256; i++) cm[i] = (uint8_t)i; cmap_upload_bytes(0, cm, 256); }

    ring_cmd(0x23, 2);
    ring_write(FB_BASE_BYTE);
    ring_write(320);

    ring_cmd(0x10, 2);
    ring_write((1 << 16) | 0x00);
    ring_write(0);

    // Texture A at TEX_BASE_BYTE: all 0x55
    for (int i = 0; i < 16; i++) sdram_write((TEX_BASE_BYTE >> 2) + i, 0x55555555);
    // Texture B at TEX_BASE_BYTE + 0x1000: all 0xAA
    uint32_t tex_b_addr = TEX_BASE_BYTE + 0x1000;
    for (int i = 0; i < 16; i++) sdram_write((tex_b_addr >> 2) + i, 0xAAAAAAAA);

    // Draw triangle with texture A
    ring_bind_texture(TEX_BASE_BYTE, 4, 4);
    ring_cmd(0x30, 19);
    ring_write(3);
    ring_write_vertex(2*16,  2*16, 0, 0, 0, 0);
    ring_write_vertex(10*16, 2*16, 0, 0, 0, 0);
    ring_write_vertex(2*16,  8*16, 0, 0, 0, 0);
    check("tex_swap_tA_done", gpu_finish() ? 1 : 0, 1);
    check_byte("tex_swap_tA_px", 4 + 4*320, 0x55);

    // Flush tex cache (GPU_TEX_FLUSH = reg_addr 10). Pulse lasts 1 cycle
    // in the controller; the cache transitions to S_INIT and walks
    // valid_mem for 1024 cycles. The bind + ring cmds below are accepted
    // by the ring BRAM but the GPU front-end waits for tex_req_ready,
    // so the render naturally stalls until init completes.
    mmio_write(10, 0x1);

    // Bind texture B + draw triangle at different position
    ring_bind_texture(tex_b_addr, 4, 4);
    ring_cmd(0x30, 19);
    ring_write(3);
    ring_write_vertex(20*16, 2*16, 0, 0, 0, 0);
    ring_write_vertex(28*16, 2*16, 0, 0, 0, 0);
    ring_write_vertex(20*16, 8*16, 0, 0, 0, 0);
    check("tex_swap_tB_done", gpu_finish(400000) ? 1 : 0, 1);

    // Pixels of tri A should still be 0x55 (rendered before flush)
    check_byte("tex_swap_tA_persists", 4 + 4*320, 0x55);
    // Pixels of tri B should be 0xAA (rendered after flush from new texture)
    check_byte("tex_swap_tB_px", 22 + 4*320, 0xAA);
}
#endif // GPU_FEAT_TRIANGLE

// =====================================================================
// gpudemo Mode 0 replay — reproduce the ~270-frame freeze in Verilator
// =====================================================================
//
// gpudemo's Mode 0 renders a Wolfenstein-style raycaster.  Per frame:
//   - ~60 horizontal floor spans   (SPAN_COLORMAP)
//   - ~60 horizontal ceiling spans (SPAN_COLORMAP)
//   - ~320 vertical wall spans     (SPAN_COLORMAP | SPAN_COLUMN)
//   - 1 CMD_FENCE at the end
//
// On hardware the app freezes after ~108-300 frames with the GPU
// drained to IDLE but fence_reached short of the app's target and
// gpu_bad_waddr hit=1 (stray write caught outside the FB band).
// This test replays the command pattern for many frames and watches
// for:
//   (a) fence lag between submissions and completions
//   (b) any address escape outside 0x080000..0x08FFFF (our FB band
//       in the scaled-down testbench address space)
//   (c) timeouts in gpu_wait_fence
//
// If the hang reproduces here, we have a deterministic Verilator
// repro and can trace every cycle at the freeze.  If it DOESN'T
// reproduce, the bug is at the CPU↔GPU boundary (axi_periph_slave
// or MMIO write race) which this harness doesn't exercise.

/* Ring is 16 KB = 4096 words = 215 CMD_DRAW_SPAN commands max in flight.
 * gpudemo's real Mode 0 submits ~440 spans per frame; it only works because
 * the SDK's `_gpu_ring_ensure()` blocks until the GPU has drained enough.
 * This simplified harness submits-then-waits, so we keep each frame below
 * the ring size.  The FSM-level behaviour we're hunting (GPU drains to
 * idle with fence lagging) doesn't depend on exactly 380 spans — any
 * many-frames-in-sequence pattern will trip it if it's real. */
static void submit_mode0_frame(uint32_t fb_base, uint32_t wall_tex, uint32_t floor_tex,
                                int fb_cycle) {
    const uint32_t SPAN_COLORMAP = 0x01;
    const uint32_t SPAN_COLUMN   = 0x02;
    /* Bind wall tex for the column pass. */
    ring_cmd(0x20, 4);              // CMD_SET_TEXTURE
    ring_write(wall_tex);
    ring_write((64u << 16) | 64u);  // width=64, height=64
    ring_write(0);
    ring_write(0);
    /* Point the GPU at the current FB buffer for this "frame". */
    ring_cmd(0x23, 2);              // CMD_SET_FB
    ring_write(fb_base);
    ring_write(320);
    /* 10 horizontal floor spans. */
    for (int y = 120; y < 130; y++) {
        ring_cmd(0x40, 18);         // CMD_DRAW_SPAN
        ring_write(fb_base + y * 320);          // fb_addr
        ring_write(floor_tex);                  // tex_addr
        ring_write((uint32_t)(y * 0x1234));     // s (arbitrary walk)
        ring_write((uint32_t)(fb_cycle * 0x55)); // t (varies per frame)
        ring_write(0x00010000);                 // sstep = 1.0
        ring_write(0x00000000);                 // tstep = 0
        ring_write((320u << 16) | (0 << 8) | SPAN_COLORMAP);
        ring_write((1u << 16) | 64u);           // fb_stride=1, tex_width=64
        ring_write(0);
        ring_write(0); ring_write(0); ring_write(0);
        ring_write(0); ring_write(0); ring_write(0);
        ring_write(0); ring_write(0); ring_write(0);
    }
    /* 50 vertical wall columns — 16-pixel tall at each x. */
    for (int x = 0; x < 50; x++) {
        int draw_start = 100;
        int span_count = 16;
        uint32_t col_fb = fb_base + draw_start * 320 + x;
        ring_cmd(0x40, 18);
        ring_write(col_fb);
        ring_write(wall_tex);
        ring_write((uint32_t)((x & 63) << 16));   // s = x mod tex width
        ring_write(0);                            // t
        ring_write(0);                            // sstep = 0 (column mode)
        ring_write(0x00010000);                   // tstep = 1.0
        ring_write(((uint32_t)span_count << 16) | (0 << 8)
                   | SPAN_COLORMAP | SPAN_COLUMN);
        ring_write((320u << 16) | 64u);           // fb_stride=320, tex_width=64
        ring_write(0);
        ring_write(0); ring_write(0); ring_write(0);
        ring_write(0); ring_write(0); ring_write(0);
        ring_write(0); ring_write(0); ring_write(0);
    }
}

static void test_gpudemo_mode0_replay(void) {
    printf("TEST: gpudemo Mode 0 replay (multi-frame raycaster command stream)\n");
    gpu_init();

    /* Identity colormap (light=0 row). */
    { uint8_t cm[256]; for (int i = 0; i < 256; i++) cm[i] = (uint8_t)i; cmap_upload_bytes(0, cm, 256); }

    /* Small non-trivial textures. */
    const uint32_t WALL_TEX  = TEX_BASE_BYTE;
    const uint32_t FLOOR_TEX = TEX_BASE_BYTE + 64 * 64;   /* 4 KB apart */
    for (uint32_t w = 0; w < (64 * 64) / 4; w++) {
        sdram_write((WALL_TEX  >> 2) + w, 0x80808080u + w);
        sdram_write((FLOOR_TEX >> 2) + w, 0x40404040u + w);
    }

    /* Three FBs — rotate across frames like gpudemo's triple-buffer. */
    const uint32_t FBS[3] = { FB_BASE_BYTE, FB_BASE_BYTE + 0x14000, FB_BASE_BYTE + 0x28000 };

    const int N_FRAMES = 500;
    int last_fence_ok = 0;
    for (int f = 0; f < N_FRAMES; f++) {
        submit_mode0_frame(FBS[f % 3], WALL_TEX, FLOOR_TEX, f);
        bool ok = gpu_finish(500000);
        if (!ok) {
            printf("  FAIL: frame %d (%d/%04x) gpu_wait_fence timed out\n", f, f, f);
            printf("  stats: stat_pixels=%u stat_spans=%u\n",
                   tb->stat_pixels, tb->stat_spans);
            fail_count++;
            break;
        }
        last_fence_ok = f;
        if ((f & 0x3F) == 0)
            printf("  frame %d ok (stat_pixels=%u stat_spans=%u)\n",
                   f, tb->stat_pixels, tb->stat_spans);
    }
    printf("  gpudemo replay: %d/%d frames completed\n", last_fence_ok + 1, N_FRAMES);
    check("gpudemo_mode0_replay", last_fence_ok == N_FRAMES - 1 ? 1 : 0, 1);
}

// =====================================================================
// Simulated-MMIO-drop variant of the gpudemo replay
// =====================================================================
// Hypothesis: on hardware, GPU_RING_DATA MMIO writes are occasionally
// lost — some fence commands never reach the ring, so fence_reached
// lags the app's target token.  Reproduce that symptom here by
// deliberately skipping 1-in-N ring writes and confirm the GPU ends
// up in the "drained to idle with fence short" state.
//
// If this test trips gpu_wait_fence timeout, it validates the
// lost-MMIO theory — the real-hardware freeze matches what we see
// when ring words are dropped.
static int  drop_every_n = 0;  /* 0 = no drops; N = drop 1 in N writes */
static int  drop_counter = 0;
static void ring_write_maybe_drop(uint32_t w) {
    drop_counter++;
    if (drop_every_n > 0 && (drop_counter % drop_every_n) == 0) {
        /* Simulate a lost MMIO write: still bump the app-side wrptr
         * (so the KICK covers the "logical" range), but don't actually
         * post the word to GPU_RING_DATA.  The ring_bram slot keeps
         * whatever garbage / previous value was there. */
        ring_wrptr = (ring_wrptr + 4) & ring_mask;
        return;
    }
    ring_write(w);
}

static void submit_mode0_frame_dropy(uint32_t fb_base, uint32_t wall_tex,
                                      uint32_t floor_tex, int fb_cycle) {
    const uint32_t SPAN_COLORMAP = 0x01;
    const uint32_t SPAN_COLUMN   = 0x02;
    ring_cmd(0x20, 4);
    ring_write_maybe_drop(wall_tex);
    ring_write_maybe_drop((64u << 16) | 64u);
    ring_write_maybe_drop(0);
    ring_write_maybe_drop(0);
    ring_cmd(0x23, 2);
    ring_write_maybe_drop(fb_base);
    ring_write_maybe_drop(320);
    for (int y = 120; y < 130; y++) {
        ring_cmd(0x40, 18);
        ring_write_maybe_drop(fb_base + y * 320);
        ring_write_maybe_drop(floor_tex);
        ring_write_maybe_drop((uint32_t)(y * 0x1234));
        ring_write_maybe_drop((uint32_t)(fb_cycle * 0x55));
        ring_write_maybe_drop(0x00010000);
        ring_write_maybe_drop(0);
        ring_write_maybe_drop((320u << 16) | SPAN_COLORMAP);
        ring_write_maybe_drop((1u << 16) | 64u);
        ring_write_maybe_drop(0);
        ring_write_maybe_drop(0); ring_write_maybe_drop(0); ring_write_maybe_drop(0);
        ring_write_maybe_drop(0); ring_write_maybe_drop(0); ring_write_maybe_drop(0);
        ring_write_maybe_drop(0); ring_write_maybe_drop(0); ring_write_maybe_drop(0);
    }
    for (int x = 0; x < 50; x++) {
        uint32_t col_fb = fb_base + 100 * 320 + x;
        ring_cmd(0x40, 18);
        ring_write_maybe_drop(col_fb);
        ring_write_maybe_drop(wall_tex);
        ring_write_maybe_drop((uint32_t)((x & 63) << 16));
        ring_write_maybe_drop(0);
        ring_write_maybe_drop(0);
        ring_write_maybe_drop(0x00010000);
        ring_write_maybe_drop((16u << 16) | SPAN_COLORMAP | SPAN_COLUMN);
        ring_write_maybe_drop((320u << 16) | 64u);
        ring_write_maybe_drop(0);
        ring_write_maybe_drop(0); ring_write_maybe_drop(0); ring_write_maybe_drop(0);
        ring_write_maybe_drop(0); ring_write_maybe_drop(0); ring_write_maybe_drop(0);
        ring_write_maybe_drop(0); ring_write_maybe_drop(0); ring_write_maybe_drop(0);
    }
}

static void test_gpudemo_mode0_replay_with_drops(void) {
    printf("TEST: gpudemo Mode 0 replay WITH simulated MMIO drops (1 in 500)\n");
    gpu_init();
    { uint8_t cm[256]; for (int i = 0; i < 256; i++) cm[i] = (uint8_t)i; cmap_upload_bytes(0, cm, 256); }
    const uint32_t WALL_TEX  = TEX_BASE_BYTE;
    const uint32_t FLOOR_TEX = TEX_BASE_BYTE + 64 * 64;
    for (uint32_t w = 0; w < (64 * 64) / 4; w++) {
        sdram_write((WALL_TEX  >> 2) + w, 0x80808080u + w);
        sdram_write((FLOOR_TEX >> 2) + w, 0x40404040u + w);
    }
    const uint32_t FBS[3] = { FB_BASE_BYTE, FB_BASE_BYTE + 0x14000, FB_BASE_BYTE + 0x28000 };

    drop_every_n = 500;   /* drop 1 in 500 ring-BRAM writes */
    drop_counter = 0;

    const int N_FRAMES = 100;
    int last_ok = -1;
    for (int f = 0; f < N_FRAMES; f++) {
        submit_mode0_frame_dropy(FBS[f % 3], WALL_TEX, FLOOR_TEX, f);
        bool ok = gpu_finish(500000);
        if (!ok) {
            printf("  FAIL @ frame %d: drops=%d (1 in %d), fence timeout\n",
                   f, drop_counter / drop_every_n, drop_every_n);
            printf("  state=%u stat_px=%u stat_spans=%u\n",
                   tb->dbg_state, tb->stat_pixels, tb->stat_spans);
            break;
        }
        last_ok = f;
    }
    drop_every_n = 0;  /* disable drops for subsequent tests */
    printf("  drop-sim: %d/%d frames completed (%d MMIO writes dropped)\n",
           last_ok + 1, N_FRAMES, drop_counter / (drop_every_n ? drop_every_n : 500));

    /* Expected: if drops cause the hang, this test hits timeout early.
     * If the GPU recovers (e.g. because garbage decodes as NOP and
     * real fences still land), this passes and we learn the theory
     * needs a different mechanism. */
}

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
    test_span_depth_gequal();
    test_span_tex_width_nonpow2();

    /* gpudemo Mode 0 replay — tries to reproduce the hardware freeze
     * after ~300 frames in a deterministic Verilator environment. */
    test_gpudemo_mode0_replay();
    test_gpudemo_mode0_replay_with_drops();

#ifdef GPU_PERSP_IMPL
    // Perspective spans (Lite only — Full's triangle FSM doesn't share the
    // pipelined fragment processor yet)
    test_persp_constant_z();
    test_persp_two_segments();
    test_persp_varying_z();
    test_persp_curvature_accuracy();
    test_persp_small_zinv();
    test_persp_slope_rounding();
    test_persp_negative_zinv();
#endif

#ifdef GPU_FEAT_TRIANGLE
    // Triangle tests (Full variant only)
    test_triangle_flat();
    test_triangle_degenerate();
    test_triangle_textured();
    test_triangle_multi();
    test_triangle_depth();
    test_triangle_vertex_color();
    test_triangle_shared_edge();
    test_triangle_bbox_init();
    test_triangle_persp_premul_dormant();
    test_triangle_persp_vs_affine();
    test_triangle_skip_zero();
    test_triangle_back_to_back_many();
    test_triangle_tex_flush_swap();
#endif

    printf("\n=== Results: %d passed, %d failed ===\n",
           pass_count, fail_count);

    delete tb;
    return fail_count > 0 ? 1 : 0;
}
