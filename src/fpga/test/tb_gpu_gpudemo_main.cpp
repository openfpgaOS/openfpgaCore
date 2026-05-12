/*
 * Verilator harness — reproduces gpudemo's per-frame submission pattern
 * to chase the on-hardware hang reported after commit 8ae32d6
 * ("gpu: POT wrap masks for span addressing + cache stall fix").
 *
 * gpudemo on hardware traps inside of_gpu_wait()'s 5 s timeout with
 *   gpu_status=0x00610085 → state=S_FRAG_PIPE, p1_valid=1, p3_valid=1,
 *                            tex_state=S_FILL_OUT, tex_pipe_valid=1,
 *                            fbss=IDLE, fp_pipe_stall=0
 * after one frame ("fps=0001").  Cache holds a fill response; pipeline
 * has p1 + p3 valid but isn't draining.  No depth detour pending
 * (fbss_depth_entry=0 — flags are COLORMAP-only on these spans).
 *
 * The frame layout copied here mirrors gpudemo's main mode:
 *   - ~120 horizontal floor spans (320 px wide, fb_stride=1, COLORMAP)
 *   - ~120 horizontal ceiling spans (mirror of the floor)
 *   - 320 vertical wall column spans (variable count, fb_stride=320,
 *     COLORMAP|COLUMN)
 *   - One textured triangle (cube facet, depth-test + write enabled)
 *   - Fence + drain via gpu_finish.
 *
 * The whole batch is submitted in ONE ring burst (no per-span reset)
 * so the cache state from prior spans carries forward — the hardware
 * scenario.  If the GPU hangs (fence never advances within the budget)
 * we report it; otherwise we move on.
 *
 * The test does NOT verify pixel values — gpudemo's hang is about
 * pipeline progress, not correctness.  Pass criteria: every frame's
 * fence advances within the timeout.
 *
 * Build/run: from src/fpga/test/, `make gpu-gpudemo`.
 */

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <vector>
#include "Vtb_gpu.h"
#include "verilated.h"
#include "verilated_vcd_c.h"

static Vtb_gpu *tb;
static uint64_t sim_time = 0;
static int pass_count = 0;
static int fail_count = 0;

static const uint32_t TEX_BASE       = 0x00040000;
static const uint32_t FLOOR_TEX_OFF  = 0x00000000;  // 64x64 = 4096 bytes
static const uint32_t WALL_TEX_OFF   = 0x00001000;  // 64x64 = 4096 bytes
static const uint32_t CEIL_TEX_OFF   = 0x00002000;  // 64x64
static const uint32_t SPRITE_OFF     = 0x00003000;  // 64x64 (cube face)
static const uint32_t FB_BASE        = 0x00080000;
static const uint32_t ZB_BASE        = 0x000C0000;
static const uint32_t CMD_UPLOAD_BYTE = 0x00380000;
static const uint32_t RING_SIZE      = 0x00004000;
static uint32_t ring_wrptr = 0;
static const uint32_t ring_mask = RING_SIZE - 1;
static std::vector<uint32_t> pending_ring_words;

// Reduced screen so one frame's command stream fits in the 16 KB ring.
// gpudemo on hardware uses 320x240; this test scales the geometry by
// ~5x while preserving the same submission order + cache pressure
// pattern.  At 64x32, one frame = 16 floors + 16 ceilings + 64 walls
// + 1 triangle ≈ 7.4 KB, comfortably under the ring.
static const int SCREEN_W = 320;
static const int SCREEN_H = 240;

// =====================================================================
// Tick / reset / MMIO / SDRAM helpers
// =====================================================================
static void tick() {
    tb->clk = 0; tb->eval();
    sim_time++;
    tb->clk = 1; tb->eval();
    sim_time++;
}
static void reset() {
    tb->reset_n = 0; tb->reg_wr = 0; tb->bd_we = 0;
    for (int i = 0; i < 20; i++) tick();
    tb->reset_n = 1;
    for (int i = 0; i < 5; i++) tick();
}
static void sdram_write(uint32_t word_addr, uint32_t data) {
    tb->bd_we = 1; tb->bd_addr = word_addr; tb->bd_wdata = data;
    tick(); tb->bd_we = 0;
}
static void mmio_write(uint32_t reg, uint32_t val) {
    tb->reg_wr = 1; tb->reg_addr = reg; tb->reg_wdata = val;
    tick(); tb->reg_wr = 0;
}
static uint32_t mmio_read(uint32_t reg) {
    tb->reg_addr = reg;
    tb->eval();
    return tb->reg_rdata;
}
static uint32_t read_rdptr() {
    tb->reg_addr = 4;  // GPU_RING_RDPTR
    tb->eval();
    return tb->reg_rdata & 0xFFFF;
}
static void gpu_kick();

// Match SDK's _gpu_ring_ensure: wait until at least `bytes` of ring
// space is free.  Without this the CPU outruns the GPU and overwrites
// in-flight commands.  Kicks the GPU to make sure it's draining.
static void ring_ensure(uint32_t bytes) {
    for (int retry = 0; retry < 1000000; retry++) {
        uint32_t rdptr = read_rdptr();
        uint32_t free = (rdptr - ring_wrptr - 4) & ring_mask;
        if (free >= bytes) return;
        tick();
    }
    printf("ring_ensure: TIMED OUT waiting for %u bytes free\n", bytes);
}
static void ring_write(uint32_t w) {
    pending_ring_words.push_back(w);
    ring_wrptr = (ring_wrptr + 4) & ring_mask;
}
static void ring_cmd(uint8_t cmd, uint32_t payload_words) {
    uint32_t words = 1u + payload_words;
    if (pending_ring_words.size() + words > (RING_SIZE / 4u - 1u))
        gpu_kick();
    ring_ensure(words * 4u);
    ring_write(((uint32_t)cmd << 24) | payload_words);
}
static bool wait_dma_idle(int timeout_cycles = 1000000) {
    while ((mmio_read(5) & 0x4u) && timeout_cycles > 0) {
        tick();
        timeout_cycles--;
    }
    return timeout_cycles > 0;
}
static void gpu_kick() {
    if (pending_ring_words.empty())
        return;
    if (!wait_dma_idle())
        return;
    for (size_t i = 0; i < pending_ring_words.size(); i++)
        sdram_write((CMD_UPLOAD_BYTE >> 2) + (uint32_t)i, pending_ring_words[i]);
    mmio_write(3, CMD_UPLOAD_BYTE);
    mmio_write(7, (uint32_t)pending_ring_words.size());
    mmio_write(11, 1);
    pending_ring_words.clear();
}
static bool gpu_wait_fence(uint32_t token, int timeout) {
    for (int t = 0; t < timeout; t++) {
        tick();
        tb->reg_addr = 6;  // GPU_FENCE_REACHED
        tb->eval();
        if (tb->reg_rdata == token) return true;
    }
    return false;
}
static uint32_t gpu_submit_fence() {
    static uint32_t next_token = 1;
    uint32_t token = next_token++;
    ring_cmd(0x02, 1);  // CMD_FENCE
    ring_write(token);
    gpu_kick();
    return token;
}
static uint32_t read_status() {
    tb->reg_addr = 5;  // GPU_STATUS
    tb->eval();
    return tb->reg_rdata;
}

// =====================================================================
// Texture and Z-buffer setup
// =====================================================================
static void upload_texture_64x64(uint32_t off_byte, uint8_t seed) {
    // Procedural pattern unique per texture so misses cover varied data.
    for (int y = 0; y < 64; y++) {
        for (int x = 0; x < 64; x += 4) {
            uint32_t w = 0;
            for (int b = 0; b < 4; b++) {
                int xx = x + b;
                uint8_t v = (uint8_t)((y * 7 + xx * 13 + seed * 23) ^ 0x5A);
                w |= (uint32_t)v << (b * 8);
            }
            sdram_write((TEX_BASE + off_byte + y * 64 + x) >> 2, w);
        }
    }
}
static void clear_zb(uint16_t depth) {
    // Initialize Z-buffer to "far" so depth tests pass.
    uint32_t pair = ((uint32_t)depth << 16) | depth;
    for (int i = 0; i < SCREEN_W * SCREEN_H / 2; i++)
        sdram_write((ZB_BASE >> 2) + i, pair);
}
static void clear_fb() {
    for (int i = 0; i < SCREEN_W * SCREEN_H / 4; i++)
        sdram_write((FB_BASE >> 2) + i, 0xCCCCCCCCu);
}
// Identity cmap preloaded into SDRAM at the slot-0 palookup base —
// matches gpu_core.v's PALOOKUP_BASE = 0x100000.  cmap_bram retired in
// favour of tex_cache port B reads from SDRAM.
static const uint32_t PALOOKUP_BASE_BYTE = 0x03400000;
static void upload_identity_cmap_row0() {
    for (int i = 0; i < 256; i += 4) {
        uint32_t w = (uint32_t)(uint8_t)(i + 0)
                   | ((uint32_t)(uint8_t)(i + 1) <<  8)
                   | ((uint32_t)(uint8_t)(i + 2) << 16)
                   | ((uint32_t)(uint8_t)(i + 3) << 24);
        sdram_write((PALOOKUP_BASE_BYTE + i) >> 2, w);
    }
}

// =====================================================================
// Scalar span payload — 15 words.
// Modelled after of_gpu_draw_span() in src/firmware/api/of_gpu.h.
// =====================================================================
static void emit_span(uint32_t fb_addr, uint32_t tex_addr,
                      int32_t s, int32_t t,
                      int32_t sstep, int32_t tstep,
                      uint16_t count, uint8_t light, uint8_t flags,
                      int16_t fb_stride, uint16_t tex_width,
                      uint16_t tex_w_mask, uint16_t tex_h_mask)
{
    ring_cmd(0x43, 15);
    ring_write(fb_addr);
    ring_write(tex_addr);
    ring_write((uint32_t)s);
    ring_write((uint32_t)t);
    ring_write((uint32_t)sstep);
    ring_write((uint32_t)tstep);
    ring_write(((uint32_t)count << 16) | ((uint32_t)light << 8) | flags);
    ring_write(((uint32_t)(uint16_t)fb_stride << 16) | tex_width);
    ring_write(((uint32_t)tex_h_mask << 16) | tex_w_mask);  // word 8
    ring_write(0);  // z_addr (unused for non-depth spans)
    ring_write(0);  // zi
    ring_write(0);  // zistep
    for (int i = 12; i < 15; i++) ring_write(0);
}

// CMD_DRAW_TRIANGLES payload — 19 words: count + 6 words per vertex × 3.
static void emit_triangle(int16_t x0, int16_t y0, uint16_t z0,
                          int32_t s0, int32_t t0,
                          int16_t x1, int16_t y1, uint16_t z1,
                          int32_t s1, int32_t t1,
                          int16_t x2, int16_t y2, uint16_t z2,
                          int32_t s2, int32_t t2,
                          uint8_t light)
{
    ring_cmd(0x30, 19);  // CMD_DRAW_TRIANGLES
    ring_write(1);  // vertex count
    auto v = [light](int16_t x, int16_t y, uint16_t z, int32_t s, int32_t t) {
        ring_write(((uint32_t)(uint16_t)x << 16) | (uint16_t)y);  // word 0: x|y
        ring_write(((uint32_t)z) << 16);                          // word 1: z
        ring_write((uint32_t)s);                                  // word 2: s
        ring_write((uint32_t)t);                                  // word 3: t
        ring_write(0x00010000);                                   // word 4: w (affine)
        ring_write((uint32_t)light);                              // word 5: r in low byte
    };
    v(x0, y0, z0, s0, t0);
    v(x1, y1, z1, s1, t1);
    v(x2, y2, z2, s2, t2);
}

// =====================================================================
// One gpudemo-style frame: floors, ceilings, walls, one cube triangle.
// Returns true if the frame's fence advanced within the timeout.
// =====================================================================
static bool one_frame(int frame_no)
{
    // ---- Floor + ceiling spans (horizontal) ----
    // gpudemo runs from horizon to bottom; we do half the screen.
    int horizon = SCREEN_H / 2;
    for (int y = horizon; y < SCREEN_H; y++) {
        // Vary the (s, t) start per row to get cache-line variety.
        int32_t s0    = (int32_t)((frame_no * 13 + y * 17) << 12);
        int32_t t0    = (int32_t)((frame_no * 11 + y * 19) << 12);
        int32_t sstep = 0x4000;   // 0.25 in 16.16 — same as F1
        int32_t tstep = 0x2000;   // 0.125 in 16.16

        emit_span(FB_BASE + y * SCREEN_W, TEX_BASE + FLOOR_TEX_OFF,
                  s0, t0, sstep, tstep,
                  SCREEN_W, /*light*/ 16,
                  /*flags*/ 0x01,           // SPAN_COLORMAP
                  /*fb_stride*/ 1,
                  /*tex_width*/ 64,
                  /*tex_w_mask*/ 0,    // gpudemo doesn't set masks → word 8 = 0
                  /*tex_h_mask*/ 0);   // RTL decodes 0 → no wrap (legacy default)
        // Mirror as ceiling.
        int cy = (SCREEN_H - 1) - y;
        emit_span(FB_BASE + cy * SCREEN_W, TEX_BASE + CEIL_TEX_OFF,
                  s0, t0, sstep, tstep,
                  SCREEN_W, /*light*/ 22,
                  /*flags*/ 0x01,
                  /*fb_stride*/ 1,
                  /*tex_width*/ 64,
                  /*tex_w_mask*/ 0,    // gpudemo doesn't set masks → word 8 = 0
                  /*tex_h_mask*/ 0);   // RTL decodes 0 → no wrap (legacy default)
    }

    // ---- Wall column spans (vertical, COLUMN flag) ----
    for (int x = 0; x < SCREEN_W; x++) {
        int height = 64 + ((x + frame_no) & 31);    // varying wall height
        int top = (SCREEN_H - height) / 2;
        int32_t texX = (frame_no + x * 7) & 0x3F;
        int32_t t0   = ((frame_no * 13) & 0xFFFF) << 16;
        int32_t tstep = (int32_t)((64 << 16) / height);

        emit_span(FB_BASE + top * SCREEN_W + x, TEX_BASE + WALL_TEX_OFF,
                  /*s*/ texX << 16, /*t*/ t0,
                  /*sstep*/ 0, /*tstep*/ tstep,
                  /*count*/ height,
                  /*light*/ 8,
                  /*flags*/ 0x01 | 0x02,    // SPAN_COLORMAP | SPAN_COLUMN
                  /*fb_stride*/ SCREEN_W,
                  /*tex_width*/ 64,
                  /*tex_w_mask*/ 0,    // gpudemo doesn't set masks → word 8 = 0
                  /*tex_h_mask*/ 0);   // RTL decodes 0 → no wrap (legacy default)
    }

    // ---- One textured triangle (cube facet) with depth ----
    // Set depth state first.
    ring_cmd(0x21, 1);              // CMD_SET_DEPTH_FUNC
    ring_write(2);                  // LESS
    // Set Z-buffer.
    ring_cmd(0x24, 2);              // CMD_SET_ZB
    ring_write(ZB_BASE);
    ring_write(SCREEN_W * 2);       // stride in bytes
    // Set tex state for the triangle (uses st_tex_addr / st_tex_width).
    // CMD_SET_TEXTURE: 4 payload words (addr, {h, w}, format, {wrap_t, wrap_s}).
    ring_cmd(0x20, 4);              // CMD_SET_TEXTURE
    ring_write(TEX_BASE + SPRITE_OFF);
    ring_write(((uint32_t)64 << 16) | 64);  // height | width
    ring_write(0);                          // format (I8)
    ring_write(0);                          // wraps
    // Triangle covering ~half the screen.
    int16_t cx = (SCREEN_W / 2) << 4;   // 12.4 subpixel
    int16_t cy = (SCREEN_H / 2) << 4;
    int16_t r  = 60 << 4;
    emit_triangle(cx,       cy - r, 0x4000, 0,           0,
                  cx + r,   cy + r, 0x4000, 64 << 16,    64 << 16,
                  cx - r,   cy + r, 0x4000, 0,           64 << 16,
                  /*light*/ 32);

    // Restore depth=NONE so subsequent draws don't pay the depth detour.
    ring_cmd(0x21, 1);
    ring_write(0);

    // ---- Fence + drain ----
    uint32_t token = gpu_submit_fence();
    int timeout = 2000000;     // generous: 2M tics ≈ 1 ms wall
    bool ok = gpu_wait_fence(token, timeout);
    if (!ok) {
        uint32_t status = read_status();
        uint32_t f      = tb->dbg_frag;
        printf("  FRAME %3d HANG: fence=%u not reached, status=0x%08x dbg_frag=0x%08x\n",
               frame_no, token, status, f);
        // GPU_STATUS new layout: bit0=busy, 1=ring_empty, 2=dma_busy, [13:8]=state.
        printf("    state=%u  ring_empty=%u  busy=%u  dma_busy=%u\n",
               (status >> 8) & 0x3F, (status >> 1) & 1, status & 1, (status >> 2) & 1);
        // dbg_frag layout — see gpu_core.v assign dbg_frag.
        printf("    p0=%u p1=%u p2=%u p2b=%u p3=%u src_done=%u\n",
               (f>>0)&1, (f>>1)&1, (f>>2)&1, (f>>3)&1, (f>>4)&1, (f>>5)&1);
        printf("    tex_req_v=%u tex_req_r=%u fbss=%u dma_state=%u stall=%u\n",
               (f>>6)&1, (f>>7)&1, (f>>8)&0xF, (f>>12)&3, (f>>15)&1);
        printf("    tex_state=%u axi_ar=%u axi_r=%u cmap_resp=%u sp_count8=%u\n",
               (f>>16)&7, (f>>19)&1, (f>>20)&1, (f>>21)&1, (f>>24)&0xFF);
    }
    return ok;
}

// =====================================================================
// Main: boot, set up textures + framebuffer, run N frames.
// =====================================================================
int main(int argc, char **argv)
{
    setvbuf(stdout, nullptr, _IOLBF, 0);
    Verilated::commandArgs(argc, argv);
    tb = new Vtb_gpu;
    printf("=== gpudemo Repro Test ===\n");
    fflush(stdout);

    // Boot.
    ring_wrptr = 0;
    pending_ring_words.clear();
    reset();
    mmio_write(0, 4);   // GPU_CTRL: ring_reset
    mmio_write(1, 0);   // RING_WRPTR = 0

    // Static setup (matches gpudemo's startup ordering).
    upload_identity_cmap_row0();
    upload_texture_64x64(FLOOR_TEX_OFF,  0x10);
    upload_texture_64x64(WALL_TEX_OFF,   0x20);
    upload_texture_64x64(CEIL_TEX_OFF,   0x30);
    upload_texture_64x64(SPRITE_OFF,     0x40);
    clear_zb(0xFFFF);
    clear_fb();

    // Set framebuffer.
    ring_cmd(0x04, 2);                  // CMD_SET_FB
    ring_write(FB_BASE);
    ring_write(SCREEN_W);
    gpu_kick();
    // Drain the initial setup commands.
    if (!gpu_wait_fence(gpu_submit_fence(), 200000)) {
        printf("FAIL: setup fence didn't drain\n");
        delete tb;
        return 1;
    }

    // Run several frames; gpudemo trapped after one on hardware.  In
    // simulation we run more to catch any rare pipeline interaction.
    const int N_FRAMES = 32;
    for (int f = 0; f < N_FRAMES; f++) {
        if (one_frame(f)) {
            printf("  FRAME %3d OK\n", f);
            pass_count++;
        } else {
            fail_count++;
            // Keep going so the user sees whether the hang is on the
            // first frame, on a specific frame, or random.
        }
    }

    printf("\n=== gpudemo Repro: %d frames passed, %d hung ===\n",
           pass_count, fail_count);
    delete tb;
    return fail_count ? 1 : 0;
}
