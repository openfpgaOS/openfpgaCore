//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------
//
// tb_gpu_setup_main.cpp -- per-triangle SETUP (write-channel-idle) vs PER-PIXEL
// (write-bound) cost SPLIT for SM64-class small triangles.
//
// Method: render single 0x4E truecolor z-buffered triangles of varying area
// (1px ... ~thousands), each as its OWN kick->fence, and measure GPU clocks.
// cycles(N_px) is then a line:  cycles = SETUP + PERPX * N_px.
//   * SETUP  = fixed per-triangle cost paid with the write channel idle
//              (decode + S_TRI_DERIVE sort/prod/det/serial-divide/plane +
//               per-span S_SPANPROD setup + walker latency + DMA/fence drain).
//   * PERPX  = marginal cost of one more covered fragment (z-read + z-write +
//              color-write + frag pipe), the write-bound part.
//
// We fit (SETUP, PERPX) by least squares over the size sweep, then report the
// setup FRACTION for representative SM64 triangle sizes.  We ALSO directly
// measure the degenerate 1px / few-px triangles where setup dominates.
//
// Realistic regime: pass +gpu_rd_latency=24 (io_sdram round trip).  Default 2
// is the optimistic in-FPGA-BRAM case.
//
// Built/run via `make gpu-setup`.  Same RTL config as gpu-perf / acceptance-sm64.
//------------------------------------------------------------------------------

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

#include "Vtb_gpu.h"
#include "verilated.h"

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

static uint64_t gpu_clocks = 0;
static void tick(int n = 1) {
    for (int i = 0; i < n; i++) {
        tb->clk = 0; tb->eval(); sim_time++;
        tb->clk = 1; tb->eval(); sim_time++;
        gpu_clocks++;
    }
}
static void hard_reset() {
    tb->reset_n = 0; tb->reg_wr = 0; tb->bd_we = 0; tb->slave_swap_pending = 0;
    for (int i = 0; i < 20; i++) tick();
    tb->reset_n = 1;
    for (int i = 0; i < 5; i++) tick();
}
static void sdram_write(uint32_t word_addr, uint32_t data) {
    tb->bd_we = 1; tb->bd_addr = word_addr; tb->bd_wdata = data; tick(); tb->bd_we = 0;
}
static uint32_t sdram_read(uint32_t word_addr) {
    tb->bd_rd_addr = word_addr; tb->eval(); return tb->bd_rd_data;
}
static uint8_t sdram_read_byte(uint32_t byte_addr) {
    uint32_t w = sdram_read(byte_addr >> 2); return (uint8_t)((w >> ((byte_addr & 3) * 8)) & 0xFF);
}
static void sdram_write_byte(uint32_t byte_addr, uint8_t v) {
    uint32_t w = sdram_read(byte_addr >> 2); int sh = (byte_addr & 3) * 8;
    w = (w & ~(0xFFu << sh)) | ((uint32_t)v << sh); sdram_write(byte_addr >> 2, w);
}
static uint16_t sdram_read_u16_le(uint32_t byte_addr) {
    return (uint16_t)sdram_read_byte(byte_addr) | ((uint16_t)sdram_read_byte(byte_addr + 1u) << 8);
}
static void sdram_write_u16_le(uint32_t byte_addr, uint16_t v) {
    sdram_write_byte(byte_addr, (uint8_t)(v & 0xFF)); sdram_write_byte(byte_addr + 1u, (uint8_t)(v >> 8));
}
static void sdram_fill(uint32_t base_byte, uint32_t bytes, uint8_t value) {
    uint32_t fw = ((uint32_t)value) * 0x01010101u;
    uint32_t addr = base_byte, end = base_byte + bytes;
    while ((addr & 3) && addr < end) { sdram_write_byte(addr, value); addr++; }
    while (addr + 4 <= end) { sdram_write(addr >> 2, fw); addr += 4; }
    while (addr < end) { sdram_write_byte(addr, value); addr++; }
}

enum { REG_CTRL=0, REG_RING_WRPTR=1, REG_DMA_SRC=3, REG_RING_RDPTR=4,
       REG_STATUS=5, REG_FENCE=6, REG_DMA_LEN=7, REG_DMA_KICK=11, REG_PALOOKUP_BASE=12 };
static void mmio_write(uint32_t reg, uint32_t val) {
    tb->reg_wr = 1; tb->reg_addr = reg; tb->reg_wdata = val; tick(); tb->reg_wr = 0;
}
static uint32_t mmio_read(uint32_t reg) { tb->reg_addr = reg; tb->eval(); return tb->reg_rdata; }
static void wait_for_dma_idle(int timeout = 200000) {
    for (int t = 0; t < timeout; t++) { if ((mmio_read(REG_STATUS) & (1 << 2)) == 0) return; tick(); }
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
    hard_reset(); ring_wrptr = 0; pending_stream.clear();
    mmio_write(REG_CTRL, 4);
    mmio_write(REG_PALOOKUP_BASE, PALOOKUP_BASE_BYTE);
    mmio_write(REG_RING_WRPTR, 0);
}
static uint32_t next_fence_token = 1;
static uint32_t submit_fence() {
    uint32_t t = next_fence_token++;
    ring_cmd(0x02, 1); ring_write(t); gpu_kick(); return t;
}
static bool wait_fence(uint32_t token, int timeout = 4000000) {
    for (int t = 0; t < timeout; t++) {
        tick();
        if ((int32_t)(tb->fence_reached - token) >= 0) return true;
    }
    fprintf(stderr, "  FENCE TIMEOUT tok=%u reached=%u\n", token, tb->fence_reached);
    return false;
}

struct Surface {
    uint32_t fb_base = FB_BASE_BYTE; int32_t fb_major_step = 320; int32_t fb_minor_step = 1;
    uint32_t tex_addr = TEX_BASE_BYTE; uint16_t tex_width = 64, tex_w_mask = 0x3F, tex_h_mask = 0x3F;
    uint8_t flags = 0x20; uint8_t colormap_id = 0; uint8_t attr_mode = 1; uint8_t span_axis = 0;
    uint8_t z_mode = 0; uint32_t z_base = Z_BASE_BYTE; int32_t z_major_step = 320*2; int32_t z_minor_step = 2;
    int32_t clamp_min[3] = {0,0,0}; int32_t clamp_max[3] = {0,0,0};
};
static const uint8_t SPAN_PERSP = 1 << 5;
static const uint8_t SPAN_TRUECOLOR_BIT = 1 << 7;

static void emit_set_tri_state(const Surface &p, int16_t cx0, int16_t cx1, int16_t cy0, int16_t cy1) {
    std::vector<uint32_t> w(16, 0);
    uint32_t control = ((uint32_t)p.flags & 0xFF) | (((uint32_t)p.colormap_id & 0xF) << 8)
                     | (((uint32_t)p.attr_mode & 0xF) << 12) | (((uint32_t)p.span_axis & 0xF) << 16)
                     | (((uint32_t)p.z_mode & 0xF) << 24);
    w[0]=p.fb_base; w[1]=(uint32_t)p.fb_major_step; w[2]=(uint32_t)p.fb_minor_step;
    w[3]=p.tex_addr; w[4]=p.tex_width; w[5]=((uint32_t)p.tex_h_mask<<16)|(uint32_t)p.tex_w_mask;
    w[6]=control; w[7]=(uint32_t)p.clamp_min[0]; w[8]=(uint32_t)p.clamp_max[0];
    w[9]=(uint32_t)p.clamp_min[1]; w[10]=(uint32_t)p.clamp_max[1];
    w[11]=p.z_base; w[12]=(uint32_t)p.z_major_step; w[13]=(uint32_t)p.z_minor_step;
    w[14]=((uint32_t)(uint16_t)cx1<<16)|(uint16_t)cx0; w[15]=((uint32_t)(uint16_t)cy1<<16)|(uint16_t)cy0;
    ring_cmd(0x4A, (uint32_t)w.size());
    for (uint32_t x : w) ring_write(x);
}
static void emit_vert_tri_4e(const int16_t vx[3], const int16_t vy[3],
                             const int32_t s[3], const int32_t t[3],
                             const int32_t zi[3], const uint16_t col[3]) {
    std::vector<uint32_t> w(19, 0);
    w[0]=((uint32_t)(uint16_t)vy[0]<<16)|(uint16_t)vx[0];
    w[1]=((uint32_t)(uint16_t)vy[1]<<16)|(uint16_t)vx[1];
    w[2]=((uint32_t)(uint16_t)vy[2]<<16)|(uint16_t)vx[2];
    w[3]=(uint32_t)s[0]; w[4]=(uint32_t)s[1]; w[5]=(uint32_t)s[2];
    w[6]=(uint32_t)t[0]; w[7]=(uint32_t)t[1]; w[8]=(uint32_t)t[2];
    w[9]=(uint32_t)zi[0]; w[10]=(uint32_t)zi[1]; w[11]=(uint32_t)zi[2];
    w[12]=col[0]; w[13]=col[1]; w[14]=col[2]; w[15]=0;
    w[16]=(uint32_t)zi[0]; w[17]=(uint32_t)zi[1]; w[18]=(uint32_t)zi[2];
    ring_cmd(0x4E, (uint32_t)w.size());
    for (uint32_t x : w) ring_write(x);
}

static void preload() {
    sdram_fill(FB_BASE_BYTE, 320u*512u*2u, SENTINEL_BYTE);
    sdram_fill(TEX_BASE_BYTE, 256u*1024u, 0u);
    sdram_fill(Z_BASE_BYTE, 320u*512u*2u, 0u);
    sdram_write_u16_le(TEX_BASE_BYTE, 0xFFFF);
}

static uint32_t count_written_px_tc(int x0, int x1, int y0, int y1) {
    uint32_t n = 0;
    for (int y = y0; y <= y1; y++)
        for (int x = x0; x <= x1; x++)
            if (sdram_read_u16_le(FB_BASE_BYTE + (uint32_t)y*640u + (uint32_t)x*2u) != 0xABAB) n++;
    return n;
}

struct Result { uint64_t cycles; uint32_t pixels; };

// Right triangle, right-angle at (X0,Y0), legs W (x) and H (y).  Pixel count
// of the interior ~ W*H/2 (top-left fill rule).  Independent kick->fence each.
static Result measure_one(int X0, int Y0, int W, int H, bool zbuf, bool textured) {
    gpu_init();
    preload();
    if (textured) {
        for (int i = 0; i < 64*64; i++) {
            uint16_t texel = (uint16_t)((i*2654435761u) >> 16);
            if (texel == 0) texel = 0x0841;
            sdram_write_u16_le(TEX_BASE_BYTE + (uint32_t)i*2u, texel);
        }
    }
    Surface p;
    p.flags = SPAN_PERSP | SPAN_TRUECOLOR_BIT;
    p.fb_base = FB_BASE_BYTE; p.fb_minor_step = 2; p.fb_major_step = 320*2;
    p.tex_addr = TEX_BASE_BYTE;
    p.tex_width = textured ? 64 : 1;
    p.tex_w_mask = textured ? 0x3F : 0; p.tex_h_mask = textured ? 0x3F : 0;
    p.z_mode = zbuf ? 3 : 0; p.z_base = Z_BASE_BYTE; p.z_major_step = 320*2; p.z_minor_step = 2;

    const int32_t Q = 1 << 16;
    int X1 = X0 + W, Y1 = Y0 + H;
    int16_t vx[3] = { (int16_t)(X0*16), (int16_t)(X1*16), (int16_t)(X0*16) };
    int16_t vy[3] = { (int16_t)Y0, (int16_t)Y0, (int16_t)Y1 };
    int32_t s[3], t[3];
    if (textured) { s[0]=0; s[1]=(int32_t)(63<<16); s[2]=0; t[0]=0; t[1]=0; t[2]=(int32_t)(63<<16); }
    else { s[0]=s[1]=s[2]=0; t[0]=t[1]=t[2]=0; }
    int32_t zi[3] = { Q, Q, Q };
    uint16_t col[3] = { 0xFFFF, 0xFFFF, 0xFFFF };

    emit_set_tri_state(p, 0, 320, 0, 480);
    emit_vert_tri_4e(vx, vy, s, t, zi, col);

    uint64_t c0 = gpu_clocks;
    uint32_t fence = submit_fence();
    bool ok = wait_fence(fence);
    uint64_t c1 = gpu_clocks;

    Result r{};
    r.cycles = ok ? (c1 - c0) : 0;
    int bx1 = (X1 > 319 ? 319 : X1), by1 = (Y1 > 479 ? 479 : Y1);
    r.pixels = count_written_px_tc(X0, bx1, Y0, by1);
    return r;
}

// Render T copies of the same small triangle in ONE kick, tiled across the FB
// (non-overlapping) so the per-KICK overhead (DMA descriptor + ring + fence
// drain) is paid ONCE but per-TRIANGLE setup is paid T times.  Comparing T vs 1
// isolates the true per-triangle setup from per-kick overhead.  This is the
// realistic SM64 case: a whole frame's triangles arrive in one (few) batch(es).
static Result measure_batch(int W, int H, int T, bool zbuf, bool textured) {
    gpu_init();
    preload();
    if (textured) {
        for (int i = 0; i < 64*64; i++) {
            uint16_t texel = (uint16_t)((i*2654435761u) >> 16);
            if (texel == 0) texel = 0x0841;
            sdram_write_u16_le(TEX_BASE_BYTE + (uint32_t)i*2u, texel);
        }
    }
    Surface p;
    p.flags = SPAN_PERSP | SPAN_TRUECOLOR_BIT;
    p.fb_base = FB_BASE_BYTE; p.fb_minor_step = 2; p.fb_major_step = 320*2;
    p.tex_addr = TEX_BASE_BYTE;
    p.tex_width = textured ? 64 : 1;
    p.tex_w_mask = textured ? 0x3F : 0; p.tex_h_mask = textured ? 0x3F : 0;
    p.z_mode = zbuf ? 3 : 0; p.z_base = Z_BASE_BYTE; p.z_major_step = 320*2; p.z_minor_step = 2;

    const int32_t Q = 1 << 16;
    // Sticky 0x4A once (clip wide), then T draws tiled in a grid.
    emit_set_tri_state(p, 0, 320, 0, 480);
    int cols = 320 / (W + 2); if (cols < 1) cols = 1;
    int placed = 0, total_px_target = 0;
    for (int i = 0; i < T; i++) {
        int gx = i % cols, gy = i / cols;
        int X0 = gx * (W + 2);
        int Y0 = gy * (H + 2);
        if (Y0 + H >= 470) break;            // ran out of FB rows
        int X1 = X0 + W, Y1 = Y0 + H;
        int16_t vx[3] = { (int16_t)(X0*16), (int16_t)(X1*16), (int16_t)(X0*16) };
        int16_t vy[3] = { (int16_t)Y0, (int16_t)Y0, (int16_t)Y1 };
        int32_t s[3], t[3];
        if (textured) { s[0]=0; s[1]=(int32_t)(63<<16); s[2]=0; t[0]=0; t[1]=0; t[2]=(int32_t)(63<<16); }
        else { s[0]=s[1]=s[2]=0; t[0]=t[1]=t[2]=0; }
        int32_t zi[3] = { Q, Q, Q };
        uint16_t col[3] = { 0xFFFF, 0xFFFF, 0xFFFF };
        emit_vert_tri_4e(vx, vy, s, t, zi, col);
        placed++;
        (void)total_px_target;
    }

    uint64_t c0 = gpu_clocks;
    uint32_t fence = submit_fence();
    bool ok = wait_fence(fence);
    uint64_t c1 = gpu_clocks;

    Result r{};
    r.cycles = ok ? (c1 - c0) : 0;
    r.pixels = (uint32_t)placed;   // here .pixels carries the placed-tri count
    return r;
}

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    tb = new Vtb_gpu;
    hard_reset();

    // Size sweep: legs (W,H) from degenerate to large.  Each is its OWN tri.
    struct Sz { int w, h; const char *label; };
    Sz sizes[] = {
        {1,  1,   "1x1   (~1 px)        "},
        {2,  2,   "2x2   (~2 px)        "},
        {4,  4,   "4x4   (~8 px)        "},
        {6,  6,   "6x6   (~18 px)       "},
        {8,  8,   "8x8   (~32 px)       "},
        {12, 12,  "12x12 (~72 px)       "},
        {16, 16,  "16x16 (~128 px)      "},
        {24, 24,  "24x24 (~288 px)      "},
        {32, 32,  "32x32 (~512 px)      "},
        {48, 48,  "48x48 (~1152 px)     "},
        {64, 64,  "64x64 (~2048 px)     "},
        {100,100, "100x100 (~5000 px)   "},
        {150,150, "150x150 (~11250 px)  "},
    };
    const int NS = (int)(sizeof(sizes)/sizeof(sizes[0]));

    printf("============================================================\n");
    printf(" GPU per-triangle SETUP vs PER-PIXEL split  (0x4A+0x4E SM64 path)\n");
    printf(" tex+truecolor+z, latency=%s\n",
           "see [SDRAM] line below");
    printf("============================================================\n");

    Result res[NS];
    for (int i = 0; i < NS; i++) {
        res[i] = measure_one(20, 20, sizes[i].w, sizes[i].h, /*z*/true, /*tex*/true);
        printf("  %s  pixels=%6u  cycles=%8llu  cyc/px=%8.2f\n",
               sizes[i].label, res[i].pixels,
               (unsigned long long)res[i].cycles,
               res[i].pixels ? (double)res[i].cycles/res[i].pixels : 0.0);
    }
    printf("  [SDRAM model: last initial read latency = %u cyc]\n",
           (unsigned)tb->dbg_rd_last_latency_o);

    // Least-squares fit cycles = SETUP + PERPX * pixels over the points with
    // pixels>0.  Weight all equally.
    double sx=0, sy=0, sxx=0, sxy=0; int n=0;
    for (int i = 0; i < NS; i++) {
        if (res[i].pixels == 0) continue;
        double x = res[i].pixels, y = res[i].cycles;
        sx+=x; sy+=y; sxx+=x*x; sxy+=x*y; n++;
    }
    double denom = (n*sxx - sx*sx);
    double perpx = denom!=0 ? (n*sxy - sx*sy)/denom : 0;
    double setup = n!=0 ? (sy - perpx*sx)/n : 0;

    // Also a robust 2-point estimate from the two largest tris (slope) and
    // back-solve setup, which is less sensitive to the tiny-tri pipe-fill noise.
    double pa_px = res[NS-1].pixels, pa_cy = res[NS-1].cycles;
    double pb_px = res[NS-3].pixels, pb_cy = res[NS-3].cycles;
    double perpx2 = (pa_px!=pb_px) ? (pa_cy-pb_cy)/(pa_px-pb_px) : perpx;
    double setup2 = pa_cy - perpx2*pa_px;

    printf("\n------------------------------------------------------------\n");
    printf(" FIT  cycles = SETUP + PERPX * pixels\n");
    printf("   least-squares : SETUP = %.0f cyc   PERPX = %.2f cyc/px\n", setup, perpx);
    printf("   2-pt (big tris): SETUP = %.0f cyc   PERPX = %.2f cyc/px\n", setup2, perpx2);
    printf("------------------------------------------------------------\n");

    // Setup fraction vs triangle size, using the 2-point (cleaner) model.
    printf(" SETUP fraction = SETUP / (SETUP + PERPX*N) :\n");
    int probe_px[] = {1, 5, 10, 20, 40, 80, 160, 500, 2000, 11000};
    for (int pp : probe_px) {
        double tot = setup2 + perpx2*pp;
        printf("   N=%6d px : setup-idle = %5.1f%%   (setup=%.0f, write=%.0f cyc)\n",
               pp, tot>0 ? 100.0*setup2/tot : 0.0, setup2, perpx2*pp);
    }

    printf("\n------------------------------------------------------------\n");
    printf(" PIPELINING CEILING (overlap tri N+1 setup w/ tri N writes):\n");
    printf("   The reclaimable time is the setup that runs WHILE the write\n");
    printf("   channel is idle.  Per frame of T triangles avg N px each:\n");
    printf("     serial   = T*(SETUP + PERPX*N)\n");
    printf("     pipelined>= T*max(SETUP_writeidle, PERPX*N) + one SETUP\n");
    printf("   Max speedup = (SETUP+PERPX*N) / max(SETUP, PERPX*N).\n");
    int frame_n[] = {5, 10, 20, 40, 80};
    for (int nn : frame_n) {
        double serial = setup2 + perpx2*nn;
        double pipe   = (setup2 > perpx2*nn) ? setup2 : perpx2*nn;
        printf("   avg N=%4d px : serial=%.0f  pipelined-floor=%.0f  max speedup=%.2fx\n",
               nn, serial, pipe, pipe>0 ? serial/pipe : 0.0);
    }
    printf("============================================================\n");

    // ========================================================================
    // BATCH experiment: T identical small tris in ONE kick.  Marginal cost per
    // added triangle = TRUE per-triangle setup + its pixel writes.  Subtracting
    // the per-pixel write portion gives the reclaimable (write-idle) setup.
    // This removes the per-KICK overhead that contaminated the single-tri fit.
    // ========================================================================
    printf("\n============================================================\n");
    printf(" BATCH: T identical tris / ONE kick (isolates per-tri setup\n");
    printf("        from per-kick DMA/fence overhead)\n");
    printf("============================================================\n");
    // Choose an SM64-representative small triangle: ~8x8 leg => ~32px.
    struct B { int w,h; const char *lab; };
    B btris[] = {
        {6,6,  "6x6 (~18px)"},
        {8,8,  "8x8 (~32px)"},
        {12,12,"12x12 (~72px)"},
    };
    for (auto &b : btris) {
        Result r1  = measure_batch(b.w, b.h, 1,   true, true);
        Result rN  = measure_batch(b.w, b.h, 100, true, true);
        // marginal cyc per added triangle (per-kick overhead cancels):
        double dT = (double)rN.pixels - (double)r1.pixels;
        double marg = dT>0 ? ((double)rN.cycles - (double)r1.cycles)/dT : 0.0;
        // pixels per triangle ~ interior of right tri; measure exact from a single
        Result rsz = measure_one(20,20,b.w,b.h,true,true);
        double px_per_tri = rsz.pixels;
        // write-bound per-pixel cost: take the large-tri asymptote (cleanest)
        double perpx_asym = (double)res[NS-1].cycles / (double)res[NS-1].pixels; // ~7.06
        double write_part = perpx_asym * px_per_tri;
        double setup_part = marg - write_part;
        if (setup_part < 0) setup_part = 0;
        printf("  %-14s : marginal=%.1f cyc/tri  (write~%.1f + setup~%.1f)\n",
               b.lab, marg, write_part, setup_part);
        printf("                   per-kick overhead (T=1 - 1*marg) ~ %.0f cyc\n",
               (double)r1.cycles - marg);
        printf("                   setup-idle FRACTION of a tri = %.0f%%   reclaimable-by-pipelining ceiling = %.2fx\n",
               marg>0?100.0*setup_part/marg:0.0,
               (write_part>0||setup_part>0) ? marg/((setup_part>write_part)?setup_part:write_part) : 1.0);
    }
    printf("============================================================\n");
    printf(" NOTE: 'reclaimable ceiling' = marginal / max(setup,write).\n");
    printf("       If setup>>write (tiny tris) pipelining hides setup => up to\n");
    printf("       that ceiling.  If write>=setup, writes serialize => ~1x.\n");
    printf("============================================================\n");

    // ========================================================================
    // DIRECT write-channel-idle measurement (ground truth, not a fit).
    // For each small-tri batch, count cycles the write channel was BUSY vs the
    // total render window.  write-idle = total - busy.  The setup is exactly the
    // write-idle time; pipelining can reclaim AT MOST that fraction.
    // ========================================================================
    printf("\n============================================================\n");
    printf(" DIRECT write-channel occupancy (ground truth)\n");
    printf("   busy = cycles a write txn occupied the channel (AW/W/B/commit)\n");
    printf("   idle = total - busy  (== setup / fragment-stall window)\n");
    printf("============================================================\n");
    struct DC { int w,h,T; const char *lab; };
    DC dcs[] = {
        {6,6,  64, "6x6 x64  (~18px/tri) "},
        {8,8,  64, "8x8 x64  (~32px/tri) "},
        {12,12,48, "12x12x48 (~72px/tri) "},
        {150,150,1,"150x150 x1 (big)     "},
    };
    printf("  %-22s %8s %8s %8s %9s %10s\n",
           "config", "total", "wr_busy", "rd_busy", "sdram_busy", "sdram_idle");
    // z-OFF control: removes the per-pixel z-read round trip. If sdram_idle
    // collapses with z off, the small-tri idle is per-pixel z-read LATENCY wait
    // (reclaimable by a parallel tri); if it stays high, it is pure FSM setup.
    {
        Result r = measure_batch(8, 8, 64, /*z*/false, /*tex*/true);
        uint32_t wbusy=tb->dbg_w_busy_cycles, rbusy=tb->dbg_rd_busy_cycles, ovl=tb->dbg_rw_overlap_cycles;
        double total=(double)r.cycles, sb=(double)rbusy+wbusy-ovl; if(sb>total)sb=total;
        printf("  %-22s %8.0f %7u%% %7u%% %8.0f%% %9.1f%%   [Z OFF]\n",
               "8x8 x64 z-OFF", total,
               total>0?(unsigned)(100.0*wbusy/total):0, total>0?(unsigned)(100.0*rbusy/total):0,
               total>0?100.0*sb/total:0.0, total>0?100.0*(total-sb)/total:0.0);
    }
    for (auto &d : dcs) {
        Result r = measure_batch(d.w, d.h, d.T, true, true);
        // measure_batch ends right after fence; counters are cumulative from the
        // post-reset (gpu_init) start, and each call resets via hard_reset, so the
        // counters reflect exactly this render window.
        uint32_t wbusy = tb->dbg_w_busy_cycles;
        uint32_t rbusy = tb->dbg_rd_busy_cycles;
        uint32_t ovl   = tb->dbg_rw_overlap_cycles;
        uint32_t beat  = tb->dbg_w_beat_cycles;
        double total = (double)r.cycles;
        // True single-port io_sdram occupancy = UNION = rd_busy + wr_busy - overlap.
        double sdram_busy = (double)rbusy + (double)wbusy - (double)ovl;
        if (sdram_busy > total) sdram_busy = total;
        double sdram_idle = total - sdram_busy;
        printf("  %-22s %8.0f %7u%% %7u%% %8.0f%% %9.1f%%   [wbeats=%u ovl=%u]\n",
               d.lab, total,
               total>0?(unsigned)(100.0*wbusy/total):0,
               total>0?(unsigned)(100.0*rbusy/total):0,
               total>0?100.0*sdram_busy/total:0.0,
               total>0?100.0*sdram_idle/total:0.0, beat, ovl);
    }
    printf("------------------------------------------------------------\n");
    printf(" sdram_busy = rd_busy + wr_busy (one real io_sdram port serializes\n");
    printf(" both). sdram_IDLE is the upper bound on what ANY parallelism (more\n");
    printf(" triangles in flight) could reclaim: that idle is pure compute SETUP\n");
    printf(" with NO memory op outstanding. If SDRAM is near-saturated even for\n");
    printf(" tiny tris, the writes/reads serialize and parallel tris cannot help.\n");
    printf("============================================================\n");

    delete tb;
    return 0;
}
