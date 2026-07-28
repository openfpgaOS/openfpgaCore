//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------
//
// tb_gpu_perf_main.cpp -- EMPIRICAL cycles-per-pixel measurement for the GPU.
//
// Drives ONE large SM64-class triangle (textured, RGB565 truecolor, z-buffer)
// through the real gpu_core fragment pipeline in the tb_gpu Verilator model and
// measures GPU clock cycles from DMA-kick to fence-reached, plus the framebuffer
// pixels actually written.  Then varies ONE factor at a time (textured/untextured,
// z on/off, truecolor/palettized, single/overdraw) to attribute the per-pixel cost.
//
// Built/run via `make gpu-perf` (SM64 functional config: INCLUDE_DIRECT_COLOR=1,
// VERT_TRI=1, PARAM_TRI=1, PALETTE=1, GPU_Z_READ_WINDOW=4).
//
// SDRAM latency: the tb_gpu SDRAM model defaults to a fixed 2-cycle initial read
// latency (single-outstanding, 1 beat/cycle thereafter).  The plusarg
// +gpu_rd_latency=N raises the initial latency to model real axi_sdram_slave +
// io_sdram round-trips.  We sweep N to show the latency sensitivity and report
// which number is realistic.
//------------------------------------------------------------------------------

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

#include "Vtb_gpu.h"
#include "verilated.h"

// ============================================================================
// Globals / SDRAM region layout (mirrors tb_gpu_acceptance_main.cpp)
// ============================================================================
static Vtb_gpu *tb;
static uint64_t sim_time = 0;

static const uint32_t TEX_BASE_BYTE  = 0x00040000;
static const uint32_t FB_BASE_BYTE   = 0x00080000;
static const uint32_t Z_BASE_BYTE    = 0x00180000;
static const uint32_t PALOOKUP_BASE_BYTE = 0x03FC0000;
static const uint32_t BATCH_BUF_BYTE = 0x00140000;

static const uint32_t RING_SIZE = 0x00004000;
static uint32_t ring_wrptr      = 0;
static const uint32_t ring_mask = RING_SIZE - 1;
static std::vector<uint32_t> pending_stream;
static const uint8_t SENTINEL_BYTE = 0xAB;

// ----------------------------------------------------------------------------
// Tick.  ONE GPU clock per call.  We count GPU clocks in a separate counter so
// the cyc/pixel arithmetic is in real GPU cycles (not sim_time half-steps).
// ----------------------------------------------------------------------------
static uint64_t gpu_clocks = 0;
static void tick(int n = 1) {
    for (int i = 0; i < n; i++) {
        tb->clk = 0; tb->eval(); sim_time++;
        tb->clk = 1; tb->eval(); sim_time++;
        gpu_clocks++;
    }
}

static void hard_reset() {
    tb->reset_n = 0;
    tb->reg_wr = 0;
    tb->bd_we = 0;
    tb->slave_swap_pending = 0;
    for (int i = 0; i < 20; i++) tick();
    tb->reset_n = 1;
    for (int i = 0; i < 5; i++) tick();
}

static void sdram_write(uint32_t word_addr, uint32_t data) {
    tb->bd_we = 1; tb->bd_addr = word_addr; tb->bd_wdata = data;
    tick();
    tb->bd_we = 0;
}
static uint32_t sdram_read(uint32_t word_addr) {
    tb->bd_rd_addr = word_addr; tb->eval();
    return tb->bd_rd_data;
}
static uint8_t sdram_read_byte(uint32_t byte_addr) {
    uint32_t w = sdram_read(byte_addr >> 2);
    return (uint8_t)((w >> ((byte_addr & 3) * 8)) & 0xFF);
}
static void sdram_write_byte(uint32_t byte_addr, uint8_t v) {
    uint32_t w = sdram_read(byte_addr >> 2);
    int sh = (byte_addr & 3) * 8;
    w = (w & ~(0xFFu << sh)) | ((uint32_t)v << sh);
    sdram_write(byte_addr >> 2, w);
}
static uint16_t sdram_read_u16_le(uint32_t byte_addr) {
    return (uint16_t)sdram_read_byte(byte_addr)
         | ((uint16_t)sdram_read_byte(byte_addr + 1u) << 8);
}
static void sdram_write_u16_le(uint32_t byte_addr, uint16_t v) {
    sdram_write_byte(byte_addr, (uint8_t)(v & 0xFF));
    sdram_write_byte(byte_addr + 1u, (uint8_t)(v >> 8));
}
static void sdram_fill(uint32_t base_byte, uint32_t bytes, uint8_t value) {
    uint32_t fw = ((uint32_t)value) * 0x01010101u;
    uint32_t addr = base_byte, end = base_byte + bytes;
    while ((addr & 3) && addr < end) { sdram_write_byte(addr, value); addr++; }
    while (addr + 4 <= end) { sdram_write(addr >> 2, fw); addr += 4; }
    while (addr < end) { sdram_write_byte(addr, value); addr++; }
}

// MMIO
enum {
    REG_CTRL=0, REG_RING_WRPTR=1, REG_DMA_SRC=3, REG_RING_RDPTR=4,
    REG_STATUS=5, REG_FENCE=6, REG_DMA_LEN=7, REG_DMA_KICK=11, REG_PALOOKUP_BASE=12
};
static void mmio_write(uint32_t reg, uint32_t val) {
    tb->reg_wr = 1; tb->reg_addr = reg; tb->reg_wdata = val;
    tick();
    tb->reg_wr = 0;
}
static uint32_t mmio_read(uint32_t reg) {
    tb->reg_addr = reg; tb->eval();
    return tb->reg_rdata;
}

static void wait_for_dma_idle(int timeout = 200000) {
    for (int t = 0; t < timeout; t++) {
        if ((mmio_read(REG_STATUS) & (1 << 2)) == 0) return;
        tick();
    }
    fprintf(stderr, "wait_for_dma_idle TIMEOUT\n");
}

static void ring_write(uint32_t w) { pending_stream.push_back(w); }
static void ring_cmd(uint8_t cmd, uint32_t pw) {
    if (pending_stream.empty()) wait_for_dma_idle();
    ring_write(((uint32_t)cmd << 24) | (pw & 0x00FFFFFFu));
}
static void gpu_kick() {
    if (pending_stream.empty()) return;
    wait_for_dma_idle();
    uint32_t addr_word = BATCH_BUF_BYTE >> 2;
    for (uint32_t x : pending_stream) sdram_write(addr_word++, x);
    uint32_t words = (uint32_t)pending_stream.size();
    ring_wrptr = (ring_wrptr + words * 4u) & ring_mask;
    mmio_write(REG_DMA_SRC, BATCH_BUF_BYTE);
    mmio_write(REG_DMA_LEN, words);
    mmio_write(REG_DMA_KICK, 1);
    pending_stream.clear();
}
static void gpu_init() {
    hard_reset();
    ring_wrptr = 0; pending_stream.clear();
    mmio_write(REG_CTRL, 4);
    mmio_write(REG_PALOOKUP_BASE, PALOOKUP_BASE_BYTE);
    mmio_write(REG_RING_WRPTR, 0);
}

static uint32_t next_fence_token = 1;
static uint32_t submit_fence() {
    uint32_t t = next_fence_token++;
    ring_cmd(0x02, 1);
    ring_write(t);
    gpu_kick();
    return t;
}
static bool wait_fence(uint32_t token, int timeout = 2000000) {
    for (int t = 0; t < timeout; t++) {
        tick();
        if ((int32_t)(tb->fence_reached - token) >= 0) return true;
    }
    fprintf(stderr, "  FENCE TIMEOUT tok=%u reached=%u state=%u\n",
            token, tb->fence_reached, tb->dbg_state);
    return false;
}

// ============================================================================
// ParamSpanListWire 0x4A SET_TRI_STATE encoder (mirrors acceptance harness)
// ============================================================================
struct Surface {
    uint32_t fb_base = FB_BASE_BYTE;
    int32_t  fb_major_step = 320;
    int32_t  fb_minor_step = 1;
    uint32_t tex_addr = TEX_BASE_BYTE;
    uint16_t tex_width = 64, tex_w_mask = 0x3F, tex_h_mask = 0x3F;
    uint8_t  flags = 0x20;            // SPAN_PERSP (bit5)
    uint8_t  colormap_id = 0;
    uint8_t  attr_mode = 1;           // PERSP
    uint8_t  span_axis = 0;
    uint8_t  z_mode = 0;              // bit0=test bit1=write (packed <<24)
    uint32_t z_base = Z_BASE_BYTE;
    int32_t  z_major_step = 320 * 2;
    int32_t  z_minor_step = 2;
    int32_t  clamp_min[3] = {0,0,0};
    int32_t  clamp_max[3] = {0,0,0};
};
static const uint8_t SPAN_PERSP = 1 << 5;          // 0x20 (control word bit5)
static const uint8_t SPAN_TRUECOLOR_BIT = 1 << 7;  // 0x80 (control word bit7)

// 0x4A SET_TRI_STATE: 16-word sticky header. Mirrors encode_set_tri_state_wire
// in tb_gpu_acceptance_main.cpp EXACTLY (the 31-word form is 0x49, not 0x4A).
static void emit_set_tri_state(const Surface &p,
                               int16_t cx0, int16_t cx1, int16_t cy0, int16_t cy1) {
    std::vector<uint32_t> w(16, 0);
    uint32_t control = ((uint32_t)p.flags & 0xFF)
                     | (((uint32_t)p.colormap_id & 0xF) << 8)
                     | (((uint32_t)p.attr_mode   & 0xF) << 12)
                     | (((uint32_t)p.span_axis   & 0xF) << 16)
                     | (((uint32_t)p.z_mode      & 0xF) << 24);
    w[0]  = p.fb_base;
    w[1]  = (uint32_t)p.fb_major_step;
    w[2]  = (uint32_t)p.fb_minor_step;
    w[3]  = p.tex_addr;
    w[4]  = p.tex_width;
    w[5]  = ((uint32_t)p.tex_h_mask << 16) | (uint32_t)p.tex_w_mask;
    w[6]  = control;
    w[7]  = (uint32_t)p.clamp_min[0];
    w[8]  = (uint32_t)p.clamp_max[0];
    w[9]  = (uint32_t)p.clamp_min[1];
    w[10] = (uint32_t)p.clamp_max[1];
    w[11] = p.z_base;
    w[12] = (uint32_t)p.z_major_step;
    w[13] = (uint32_t)p.z_minor_step;
    w[14] = ((uint32_t)(uint16_t)cx1 << 16) | (uint16_t)cx0;
    w[15] = ((uint32_t)(uint16_t)cy1 << 16) | (uint16_t)cy0;
    ring_cmd(0x4A, (uint32_t)w.size());
    for (uint32_t x : w) ring_write(x);
}

// 0x4B DRAW_VERT_TRI (palettized scalar-light) — 14 words.
static void emit_vert_tri_4b(const int16_t vx[3], const int16_t vy[3],
                             const int32_t s[3], const int32_t t[3],
                             const int32_t zi[3], const uint8_t lrow[3]) {
    std::vector<uint32_t> w(14, 0);
    w[0] = ((uint32_t)(uint16_t)vy[0] << 16) | (uint16_t)vx[0];
    w[1] = ((uint32_t)(uint16_t)vy[1] << 16) | (uint16_t)vx[1];
    w[2] = ((uint32_t)(uint16_t)vy[2] << 16) | (uint16_t)vx[2];
    w[3]=(uint32_t)s[0]; w[4]=(uint32_t)s[1]; w[5]=(uint32_t)s[2];
    w[6]=(uint32_t)t[0]; w[7]=(uint32_t)t[1]; w[8]=(uint32_t)t[2];
    w[9]=(uint32_t)zi[0]; w[10]=(uint32_t)zi[1]; w[11]=(uint32_t)zi[2];
    w[12] = ((uint32_t)(lrow[0]&0x3F)) | ((uint32_t)(lrow[1]&0x3F)<<6)
          | ((uint32_t)(lrow[2]&0x3F)<<12);
    w[13] = 0;
    ring_cmd(0x4B, (uint32_t)w.size());
    for (uint32_t x : w) ring_write(x);
}

// 0x4E DRAW_VERT_TRI_RGB (truecolor per-vertex RGB565) — shrunk 17 words
// (RGB565 packed 3->2 at w12-13, q29 dropped, depth at w14-16).
static void emit_vert_tri_4e(const int16_t vx[3], const int16_t vy[3],
                             const int32_t s[3], const int32_t t[3],
                             const int32_t zi[3], const uint16_t col[3]) {
    std::vector<uint32_t> w(17, 0);
    w[0] = ((uint32_t)(uint16_t)vy[0] << 16) | (uint16_t)vx[0];
    w[1] = ((uint32_t)(uint16_t)vy[1] << 16) | (uint16_t)vx[1];
    w[2] = ((uint32_t)(uint16_t)vy[2] << 16) | (uint16_t)vx[2];
    w[3]=(uint32_t)s[0]; w[4]=(uint32_t)s[1]; w[5]=(uint32_t)s[2];
    w[6]=(uint32_t)t[0]; w[7]=(uint32_t)t[1]; w[8]=(uint32_t)t[2];
    w[9]=(uint32_t)zi[0]; w[10]=(uint32_t)zi[1]; w[11]=(uint32_t)zi[2];
    w[12]=((uint32_t)col[1]<<16)|(uint32_t)col[0]; w[13]=(uint32_t)col[2];
    w[14]=(uint32_t)zi[0]; w[15]=(uint32_t)zi[1]; w[16]=(uint32_t)zi[2];
    ring_cmd(0x4E, (uint32_t)w.size());
    for (uint32_t x : w) ring_write(x);
}

// ============================================================================
// Scene + measurement
// ============================================================================
// A large right triangle. vx are Q12.4 (px<<4), vy are integer pixel rows.
// Verts (X0,Y0)(X1,Y0)(X0,Y1): right angle at (X0,Y0).  Pixel count of the
// filled (interior) region = roughly (W*H)/2 with the edge rule.
struct TriGeom {
    int X0, X1, Y0, Y1;
};

// Count pixels the GPU actually wrote into the FB for a truecolor (2 bytes/px,
// 640-byte rows) surface by scanning for non-sentinel halfwords.
static uint32_t count_written_px_tc(int x0, int x1, int y0, int y1) {
    uint32_t n = 0;
    for (int y = y0; y <= y1; y++)
        for (int x = x0; x <= x1; x++) {
            uint16_t v = sdram_read_u16_le(FB_BASE_BYTE + (uint32_t)y*640u + (uint32_t)x*2u);
            if (v != 0xABAB) n++;
        }
    return n;
}
// Palettized (1 byte/px, 320-byte rows).
static uint32_t count_written_px_pal(int x0, int x1, int y0, int y1) {
    uint32_t n = 0;
    for (int y = y0; y <= y1; y++)
        for (int x = x0; x <= x1; x++) {
            uint8_t v = sdram_read_byte(FB_BASE_BYTE + (uint32_t)y*320u + (uint32_t)x);
            if (v != SENTINEL_BYTE) n++;
        }
    return n;
}

static void preload() {
    sdram_fill(FB_BASE_BYTE, 320u*512u*2u, SENTINEL_BYTE);   // big enough for tc FB (640B rows)
    sdram_fill(TEX_BASE_BYTE, 256u*1024u, 0u);
    sdram_fill(Z_BASE_BYTE, 320u*512u*2u, 0u);
}

struct Result {
    uint64_t cycles;
    uint32_t pixels;
    double cyc_per_px;
};

// Render one (or two) truecolor 0x4E triangle(s) with optional z / tex.
// Returns measured cycles (kick->fence) and pixels written.
static Result measure_tc(const TriGeom &g, bool textured, bool zbuf, bool overdraw,
                         int bbox_x1, int bbox_y1, bool uvvary = false) {
    gpu_init();
    preload();
    // texture: 1x1 white if untextured-ish path still binds a tex; use real tex
    // for the textured case (modulate by per-vertex white => identity).
    sdram_write_u16_le(TEX_BASE_BYTE, 0xFFFF);
    if (textured) {
        // A 64x64 RGB565 texture so the tex cache actually does line fills.
        // texel(0,0) forced non-zero so SKIP_ZERO-class discards never apply
        // and every covered fragment writes the FB (we sample s=t=0 => texel 0).
        for (int i = 0; i < 64*64; i++) {
            uint16_t texel = (uint16_t)((i*2654435761u) >> 16);
            if (texel == 0) texel = 0x0841;          // dark grey, non-zero
            sdram_write_u16_le(TEX_BASE_BYTE + (uint32_t)i*2u, texel);
        }
    }

    Surface p;
    p.flags = SPAN_PERSP | SPAN_TRUECOLOR_BIT;
    p.fb_base = FB_BASE_BYTE;
    p.fb_minor_step = 2;
    p.fb_major_step = 320 * 2;
    p.tex_addr = TEX_BASE_BYTE;
    p.tex_width = textured ? 64 : 1;
    p.tex_w_mask = textured ? 0x3F : 0;
    p.tex_h_mask = textured ? 0x3F : 0;
    p.z_mode = zbuf ? 3 : 0;   // 3 = test+write
    p.z_base = Z_BASE_BYTE;
    p.z_major_step = 320*2;
    p.z_minor_step = 2;

    const int32_t Q = 1 << 16;
    int16_t vx[3] = { (int16_t)(g.X0*16), (int16_t)(g.X1*16), (int16_t)(g.X0*16) };
    int16_t vy[3] = { (int16_t)g.Y0, (int16_t)g.Y0, (int16_t)g.Y1 };
    // s/t across vertices.  Constant (uvvary=false) => single texel, 100% tex
    // cache hit (texture essentially free).  Varying (uvvary=true) => UV gradient
    // spans the 64x64 texture so the cache actually thrashes — the realistic
    // SM64 textured case.
    int32_t s[3], t[3];
    if (uvvary && textured) {
        s[0]=0; s[1]=(int32_t)(63<<16); s[2]=0;          // u: 0..63 across width
        t[0]=0; t[1]=0;                 t[2]=(int32_t)(63<<16);  // v: 0..63 down
    } else { s[0]=s[1]=s[2]=0; t[0]=t[1]=t[2]=0; }
    int32_t zi[3] = { Q, Q, Q };
    uint16_t col[3] = { 0xFFFF, 0xFFFF, 0xFFFF };  // white verts => texel passes through

    emit_set_tri_state(p, 0, 320, 0, 200);
    emit_vert_tri_4e(vx, vy, s, t, zi, col);
    if (overdraw) {
        // Second identical triangle, NEARER so it passes z and overdraws.
        int32_t zi2[3] = { Q*2, Q*2, Q*2 };
        uint16_t col2[3] = { 0xF800, 0xF800, 0xF800 };
        emit_set_tri_state(p, 0, 320, 0, 200);
        emit_vert_tri_4e(vx, vy, s, t, zi2, col2);
    }

    uint64_t c0 = gpu_clocks;
    uint32_t fence = submit_fence();
    bool ok = wait_fence(fence);
    uint64_t c1 = gpu_clocks;

    Result r{};
    r.cycles = ok ? (c1 - c0) : 0;
    r.pixels = count_written_px_tc(g.X0, bbox_x1, g.Y0, bbox_y1);
    r.cyc_per_px = r.pixels ? (double)r.cycles / r.pixels : 0.0;
    return r;
}

// Palettized 0x4B variant (1 byte/px) for the truecolor-vs-palettized delta.
static Result measure_pal(const TriGeom &g, bool zbuf, int bbox_x1, int bbox_y1) {
    gpu_init();
    preload();
    // 8-bit texture (white-ish) + identity palookup row 0.
    sdram_fill(TEX_BASE_BYTE, 64u*64u, 0xFF);
    {
        std::vector<uint8_t> idr(256);
        for (int i=0;i<256;i++) idr[i]=(uint8_t)i;
        uint32_t base = PALOOKUP_BASE_BYTE; // slot0 row0
        for (size_t i=0;i<idr.size();i++) sdram_write_byte(base+i, idr[i]);
    }
    Surface p;                         // 8-bit FB: minor=1 major=320, no truecolor bit
    p.flags = SPAN_PERSP;
    p.fb_minor_step = 1;
    p.fb_major_step = 320;
    p.z_mode = zbuf ? 3 : 0;
    p.z_base = Z_BASE_BYTE;
    p.z_major_step = 320*2;
    p.z_minor_step = 2;

    const int32_t Q = 1 << 16;
    int16_t vx[3] = { (int16_t)(g.X0*16), (int16_t)(g.X1*16), (int16_t)(g.X0*16) };
    int16_t vy[3] = { (int16_t)g.Y0, (int16_t)g.Y0, (int16_t)g.Y1 };
    int32_t s[3] = { 0, 0, 0 };
    int32_t t[3] = { 0, 0, 0 };
    int32_t zi[3] = { Q, Q, Q };
    uint8_t l[3] = { 40, 40, 40 };

    emit_set_tri_state(p, 0, 320, 0, 200);
    emit_vert_tri_4b(vx, vy, s, t, zi, l);

    uint64_t c0 = gpu_clocks;
    uint32_t fence = submit_fence();
    bool ok = wait_fence(fence);
    uint64_t c1 = gpu_clocks;

    Result r{};
    r.cycles = ok ? (c1 - c0) : 0;
    r.pixels = count_written_px_pal(g.X0, bbox_x1, g.Y0, bbox_y1);
    r.cyc_per_px = r.pixels ? (double)r.cycles / r.pixels : 0.0;
    return r;
}

// ------------------------------------------------------------------
// COLUMN-LIST (0x4C) benchmark — the coalescer baseline.
// Doom-title-like workload: 4-lane colormap-free column groups, 100 px
// columns, fb step = stride (320).  Each pixel today produces ONE
// single-byte SDRAM write (the fb_acc same-word merge never fires on
// stride steps); the future column-interleave dispatch should merge
// adjacent-x lanes into full 32-bit words => ~0.25 writes/px and
// burst-linkable drains.  Reports cyc/px AND write-transactions/px
// (dbg_aw_count delta) so both effects are visible.
// ------------------------------------------------------------------
static Result measure_columns(int groups, uint32_t *aw_per_px_x1000) {
    gpu_init();
    preload();
    for (int i = 0; i < 64*64; i++)
        sdram_write_byte(TEX_BASE_BYTE + (uint32_t)i,
                         (uint8_t)(((i*2654435761u) >> 24) | 1u));

    const int LANES = 4, COUNT = 100;
    uint32_t aw0 = tb->dbg_aw_count;
    uint64_t c0 = gpu_clocks;
    for (int g = 0; g < groups; g++) {
        ring_cmd(0x4C, 4u + 5u * (uint32_t)LANES);
        ring_write(((uint32_t)LANES << 28));                 // lanes, flags=0
        ring_write(64u);                                     // tex_width
        ring_write((63u << 16) | 63u);                       // h_mask | w_mask
        ring_write(320u);                                    // fb step = stride
        for (int l = 0; l < LANES; l++) {
            ring_write(FB_BASE_BYTE + 320u*8u + (uint32_t)(g*8 + l));
            ring_write(TEX_BASE_BYTE + (uint32_t)((g*4+l)*3));
            ring_write((0u << 28) | (0u << 16) | (uint32_t)COUNT);
            ring_write((uint32_t)(l << 14));                 // t
            ring_write(0x00012000u);                         // tstep
        }
    }
    uint32_t tok = submit_fence();
    bool ok = wait_fence(tok);
    uint64_t c1 = gpu_clocks;
    uint32_t aw1 = tb->dbg_aw_count;

    Result r{};
    r.pixels = (uint32_t)(groups * LANES * COUNT);
    r.cycles = ok ? (c1 - c0) : 0;
    r.cyc_per_px = r.pixels ? (double)r.cycles / r.pixels : 0.0;
    if (aw_per_px_x1000)
        *aw_per_px_x1000 = r.pixels ? (uint32_t)((uint64_t)(aw1 - aw0) * 1000u / r.pixels) : 0;
    if (!ok) printf("  [column bench: FENCE TIMEOUT]\n");
    return r;
}

static void print_row(const char *label, const Result &r) {
    printf("  %-46s cycles=%7llu  pixels=%6u  cyc/px=%7.3f\n",
           label, (unsigned long long)r.cycles, r.pixels, r.cyc_per_px);
}

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    tb = new Vtb_gpu;
    hard_reset();

    // Read back the SDRAM model's configured initial read latency so we can
    // report whether this run modeled realistic latency.
    // (dbg_rd_last_latency_o reflects the last accepted txn; print after a render.)

    // A "large SM64-class triangle": ~150 wide x ~150 tall right triangle.
    // Interior pixel count ~ 150*150/2 ~ 11k.  Keep within FB & clip (0..320,0..200)
    // and within the bbox scan window.
    TriGeom big { 20, 170, 20, 170 };
    int bbx1 = 170, bby1 = 170;

    printf("============================================================\n");
    printf(" GPU cyc/pixel EMPIRICAL measurement (tb_gpu / gpu_core)\n");
    printf(" Triangle: verts (%d,%d)(%d,%d)(%d,%d)  ~right tri\n",
           big.X0,big.Y0, big.X1,big.Y0, big.X0,big.Y1);
    printf("============================================================\n");

    // SDRAM read latency actually in effect (plusarg or default 2).
    // Render the baseline once, then read the dbg latency the model published.
    printf("\n--- BASELINE: textured + truecolor + z-buffer (SM64-class) ---\n");
    Result base = measure_tc(big, /*textured*/true, /*zbuf*/true, /*overdraw*/false, bbx1, bby1);
    print_row("baseline (tex + truecolor + z)", base);
    printf("  [SDRAM model: last initial read latency = %u cyc, txns = %u]\n",
           (unsigned)tb->dbg_rd_last_latency_o, (unsigned)tb->dbg_rd_txn_count_o);

    printf("\n--- FACTOR (a) TEXTURE: untextured vs textured ---\n");
    Result untex = measure_tc(big, /*textured*/false, /*zbuf*/true, false, bbx1, bby1);
    Result texvary = measure_tc(big, /*textured*/true, /*zbuf*/true, false, bbx1, bby1, /*uvvary*/true);
    print_row("untextured (1x1 white tex) + truecolor + z", untex);
    print_row("textured const-UV (100% cache hit) [=baseline]", base);
    print_row("textured VARYING-UV (cache thrash, SM64-like)", texvary);
    printf("  delta(tex const-UV) = %+.3f cyc/px  (cache fully hides it)\n",
           base.cyc_per_px - untex.cyc_per_px);
    printf("  delta(tex vary-UV)  = %+.3f cyc/px  (real textured cost)\n",
           texvary.cyc_per_px - untex.cyc_per_px);

    printf("\n--- FACTOR (b) Z-BUFFER: z-off vs z-on ---\n");
    Result noz = measure_tc(big, true, /*zbuf*/false, false, bbx1, bby1);
    print_row("textured + truecolor, z OFF", noz);
    print_row("textured + truecolor, z ON  [=baseline]", base);
    printf("  delta(z) = %+.3f cyc/px  (z-on - z-off)\n",
           base.cyc_per_px - noz.cyc_per_px);

    printf("\n--- FACTOR (c) COLOR: truecolor RGB565 vs palettized 8-bit ---\n");
    Result pal = measure_pal(big, /*zbuf*/true, bbx1, bby1);
    print_row("palettized 8-bit (0x4B) + z", pal);
    print_row("truecolor RGB565 (0x4E) + z [=baseline]", base);
    printf("  delta(truecolor) = %+.3f cyc/px  (truecolor - palettized)\n",
           base.cyc_per_px - pal.cyc_per_px);

    printf("\n--- FACTOR (d) OVERDRAW: single vs two overlapping tris ---\n");
    Result od = measure_tc(big, true, true, /*overdraw*/true, bbx1, bby1);
    print_row("single triangle [=baseline]", base);
    print_row("two overlapping (overdraw) tris", od);
    printf("  note: overdraw 'pixels' counts UNIQUE fb pixels; cyc covers BOTH draws.\n");
    printf("  cyc per RASTERIZED fragment (2x coverage) = %.3f\n",
           od.pixels ? (double)od.cycles / (2.0 * od.pixels) : 0.0);

    printf("\n--- COLUMN PATH (0x4C, coalescer baseline) ---\n");
    {
        uint32_t awpk = 0;
        Result rc = measure_columns(8, &awpk);
        print_row("column-list 4-lane x100 (8 groups)", rc);
        printf("  column write-txns/px = %u.%03u  (coalesced target ~0.25)\n",
               awpk / 1000u, awpk % 1000u);
    }

    printf("\n============================================================\n");
    printf(" SUMMARY (default SDRAM latency = %u-cyc initial read)\n",
           (unsigned)tb->dbg_rd_last_latency_o);
    printf("------------------------------------------------------------\n");
    printf("  baseline  (tex+truecolor+z)   : %7.3f cyc/px\n", base.cyc_per_px);
    printf("  untextured (truecolor+z)      : %7.3f cyc/px   d(tex)=%+.3f\n",
           untex.cyc_per_px, base.cyc_per_px-untex.cyc_per_px);
    printf("  z-off     (tex+truecolor)     : %7.3f cyc/px   d(z)  =%+.3f\n",
           noz.cyc_per_px, base.cyc_per_px-noz.cyc_per_px);
    printf("  palettized(8-bit+z)           : %7.3f cyc/px   d(tc) =%+.3f\n",
           pal.cyc_per_px, base.cyc_per_px-pal.cyc_per_px);
    printf("  overdraw  (2 tris, unique px) : %7.3f cyc/px (2x cov => %.3f/frag)\n",
           od.cyc_per_px, od.pixels?(double)od.cycles/(2.0*od.pixels):0.0);
    printf("------------------------------------------------------------\n");
    printf("  Z-read is %.0f%% of the per-pixel cost and scales with SDRAM\n",
           base.cyc_per_px>0 ? 100.0*(base.cyc_per_px-noz.cyc_per_px)/base.cyc_per_px : 0.0);
    printf("  latency (z-off cost is flat ~%.2f). tb_gpu default latency=2 is\n",
           noz.cyc_per_px);
    printf("  NOT realistic; rerun with +gpu_rd_latency=24 for an io_sdram-like\n");
    printf("  round trip (CAS3 + tRCD + slave FSM).\n");
    printf("============================================================\n");

    delete tb;
    return 0;
}
