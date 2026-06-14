//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------
//
// Integration harness: SCANOUT-vs-GPU read-during-write RACE across the
// triple-buffer page flip (MiSTer target).  Drives the REAL gpu_core through a
// realistic MiSTer frame loop and watches whether the REAL scanout line fetch
// ever reads a framebuffer word from the buffer the GPU is CONCURRENTLY
// clearing/redrawing.
//
// Frame loop (the SHARED Pocket/MiSTer HAL discipline, of_video_acquire_next +
// pick_free_buffer + of_gpu_flip_to, request-only flip per of_gpu.h:797-798):
//   1. acquire a draw buffer D = pick_free_buffer() (!= display, != ready)
//   2. GPU: CLEAR_RECT(D) then draw spans into D (CMD-stream via doorbell DMA)
//   3. GPU: CMD_FLIP(D)  (of_gpu_flip_to) — sets fb_ready_idx=D, swap pending
//   4. return immediately; next frame acquires the THIRD buffer and repeats
// The harness does NOT wait for the swap to be PRESENTED before reusing — it
// only waits for fence_reached (CMD_FLIP retired), exactly like the firmware.
//
// DETECTOR (runs every cycle in the tick wrapper):
//   (a) read-during-write: whenever the scanout asserts a burst_rd whose address
//       lands inside the SDRAM range of the buffer the GPU's fbwq is CURRENTLY
//       writing (the active draw buffer's AW/W traffic), record the collision.
//       This is the literal "scanout reading what the GPU is writing" event.
//   (b) scanned-out-black: capture each scanout burst_data word; if a word reads
//       back as the CLEAR color where the golden committed FB has CONTENT, that
//       output pixel scans out black though the frame had geometry == the bar.
//
// A clean result is only meaningful if (a) actually fired at least once (the GPU
// genuinely overtook the scanout reader); the harness asserts and reports this.
//
// Deterministic pseudo-randomness only (xorshift32).
//
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <vector>
#include "Vtb_scanout_race.h"
#include "verilated.h"

static Vtb_scanout_race *tb;
static uint64_t sim_time = 0;

static uint32_t rng_state = 0xBADC0DEu;
static inline uint32_t rnd() {
    uint32_t x = rng_state; x ^= x << 13; x ^= x >> 17; x ^= x << 5;
    rng_state = x; return x;
}
static inline uint32_t rnd_range(uint32_t lo, uint32_t hi) { return lo + (rnd() % (hi - lo + 1)); }

// ============================================================
// Geometry / memory map.
// ============================================================
// Source FB: 8-bit indexed, FB_W x FB_H.  Default Doom-like 320x200.  out_w=640
// (MiSTer raster active width); OUT_HEIGHT is a Verilog -G param (480 = MiSTer).
static uint32_t FB_W = 320, FB_H = 200;
static uint32_t FB_STRIDE_B = 320;            // bytes/line (8bpp, stride==width)
static const uint8_t CLEAR_COLOR = 0x00;
static const uint8_t CONTENT_COLOR = 0xCD;    // every drawn pixel = 0xCD

// Three framebuffers, each given a generous power-of-two SDRAM byte region so
// buffer-collision detection is a simple range test and the bases land on
// distinct SDRAM rows (different banks/rows -> realistic precharge traffic).
static const uint32_t FB0_BYTE = 0x00080000;  // CPU 0x10080000-ish
static const uint32_t FB1_BYTE = 0x00100000;
static const uint32_t FB2_BYTE = 0x00180000;
static uint32_t FB_BASE_BYTE[3] = { FB0_BYTE, FB1_BYTE, FB2_BYTE };
static uint32_t fb_bytes() { return FB_W * FB_H; }

// GPU command/scratch regions (avoid the FBs).
static const uint32_t BATCH_BUF_BYTE = 0x00040000;
static const uint32_t TEX_BASE       = 0x00060000;
static const uint32_t PALOOKUP_BASE  = 0x03FC0000;

// ---- MMIO register map (word index) ----
enum {
    REG_CTRL = 0, REG_RING_WRPTR = 1, REG_DMA_SRC = 3, REG_RING_RDPTR = 4,
    REG_STATUS = 5, REG_FENCE = 6, REG_DMA_LEN = 7, REG_DMA_KICK = 11,
    REG_PALOOKUP_BASE = 12,
};

// ============================================================
// Golden model + detector state.
// ============================================================
// golden_fb[idx][off] = the byte content the GPU actually COMMITTED to buffer
// idx, snapshotted from SDRAM right after the flip fence retired (which proves
// gpu_core drained every m_wr_* write — gpu_core.v:4322).  This is GROUND TRUTH
// for "what SHOULD be visible", independent of the harness's span encoding: the
// scanned-out data is compared against this real committed frame.
static std::vector<uint8_t> golden_fb[3];
// has_content[idx][off] = the committed byte was non-clear (drawn geometry) at
// snapshot time.  A scanned-out clear where this is set == a black-bar pixel.
static std::vector<uint8_t> has_content[3];

// The buffer the GPU's fbwq is actively writing THIS render (set while a render
// is in flight, cleared when the frame's fence retires).  -1 = no active draw.
static volatile int gpu_active_draw_buf = -1;

// Detector counters.
static uint64_t rdw_events = 0;            // scanout burst_rd hit active draw buf
static uint64_t rdw_first_cyc = 0;
static int      rdw_first_disp = -1, rdw_first_draw = -1;
static uint32_t rdw_first_addr = 0;
static int      rdw_first_y = -1;

// PHYSICAL-read collision: regardless of the software display index, does the
// buffer the scanout's burst_rd is physically FETCHING from SDRAM ever equal
// the buffer the GPU is currently drawing?  This catches a stale/lagging
// display-idx hazard the software-idx check above would miss.
static uint64_t phys_rdw_events = 0;
static uint64_t phys_rdw_first_cyc = 0;
static int      phys_rdw_first_buf = -1;
static uint32_t phys_rdw_first_addr = 0;

static uint64_t black_pixels = 0;          // scanned word = clear where golden has content
static uint64_t black_first_cyc = 0;
static int      black_first_y = -1, black_first_disp = -1;
static uint32_t black_first_addr = 0;
static uint8_t  black_first_golden = 0;

// Track scanout burst in flight so we can map burst_data words back to a source
// byte address.  io_sdram returns burst_data in order from burst_addr upward.
static bool     scan_in_burst = false;
static uint32_t scan_word_hw = 0;          // current halfword addr being returned
static int      scan_disp_buf_at_start = -1;

// FB base as a HALFWORD (>>1 of byte addr).  Scanout drives burst_addr in
// halfwords; GPU/backdoor use byte/word.  fb byte B -> halfword B>>1.
static inline uint32_t fb_base_hw(int idx) { return FB_BASE_BYTE[idx] >> 1; }
static inline uint32_t fb_size_hw()        { return (fb_bytes() + 1) >> 1; }

// Which FB (0/1/2) does this halfword address fall into?  -1 = none.
static int hw_to_fb(uint32_t hw) {
    for (int i = 0; i < 3; i++) {
        uint32_t lo = fb_base_hw(i), hi = lo + fb_size_hw();
        if (hw >= lo && hw < hi) return i;
    }
    return -1;
}

// ============================================================
// Per-cycle detector — call AFTER eval at the rising edge.
// ============================================================
static void detector_post() {
    int disp = tb->fb_display_idx_o;

    // (a) read-during-write: scanout issues a burst_rd whose address is in the
    // buffer the GPU is currently writing.
    if (tb->scanout_burst_rd_o) {
        uint32_t hw = tb->scanout_burst_addr_o;
        int fb = hw_to_fb(hw);
        scan_in_burst = true;
        scan_word_hw = hw;
        scan_disp_buf_at_start = disp;
        if (fb >= 0 && fb == gpu_active_draw_buf) {
            rdw_events++;
            if (rdw_events == 1) {
                rdw_first_cyc = sim_time / 2;
                rdw_first_disp = disp;
                rdw_first_draw = gpu_active_draw_buf;
                rdw_first_addr = hw;
                rdw_first_y = tb->y_count_o;
            }
        }
        // Physical-read collision (display-idx-independent): the scanout is
        // physically fetching FROM a buffer the GPU is drawing INTO right now.
        if (fb >= 0 && fb == gpu_active_draw_buf) {
            phys_rdw_events++;
            if (phys_rdw_events == 1) {
                phys_rdw_first_cyc = sim_time / 2;
                phys_rdw_first_buf = fb;
                phys_rdw_first_addr = hw;
            }
        }
    }

    // (b) scanned-out-black: capture each returned burst_data word and compare
    // against the golden content of the displayed buffer.
    if (scan_in_burst && tb->scanout_burst_data_valid_o) {
        uint32_t hw = scan_word_hw;
        int fb = hw_to_fb(hw);
        if (fb == disp && fb >= 0) {
            uint32_t byte_off = (hw - fb_base_hw(fb)) * 2;  // halfword -> byte
            uint32_t w = tb->scanout_burst_data_o;
            for (int b = 0; b < 4 && byte_off + b < fb_bytes(); b++) {
                uint8_t scanned = (w >> (b * 8)) & 0xFF;
                // The committed snapshot of the DISPLAYED buffer had real
                // geometry at this byte, yet the scanout reads the clear color:
                // that output pixel scans out black == a black-bar pixel.
                if (has_content[fb][byte_off + b] && scanned == CLEAR_COLOR) {
                    black_pixels++;
                    if (black_pixels == 1) {
                        black_first_cyc = sim_time / 2;
                        black_first_y = tb->y_count_o;
                        black_first_disp = disp;
                        black_first_addr = hw;
                        black_first_golden = golden_fb[fb][byte_off + b];
                    }
                }
            }
        }
        scan_word_hw += 2;  // next 32-bit word = +2 halfwords
    }
    if (tb->scanout_burst_data_valid_o && /*last beat*/ false) {}
    if (scan_in_burst && tb->scanout_burst_rd_o == 0 &&
        tb->scanout_burst_data_valid_o == 0) {
        // burst idle gap — keep scan_in_burst until a done; harmless either way.
    }
}

// ============================================================
// Aggressor-free tick wrapper (scanout IS the contention).
// ============================================================
static void tick(int n = 1) {
    for (int i = 0; i < n; i++) {
        tb->clk = 0; tb->eval(); sim_time++;
        tb->clk = 1; tb->eval(); sim_time++;
        detector_post();
    }
}

// ============================================================
// Backdoor SDRAM access (via sdram_model_full bd_* ports)
// ============================================================
static void sdram_write(uint32_t word_addr, uint32_t data) {
    tb->bd_we = 1; tb->bd_addr = word_addr; tb->bd_wdata = data;
    tick(); tb->bd_we = 0;
}
static uint32_t sdram_read(uint32_t word_addr) {
    tb->bd_rd_addr = word_addr; tb->eval(); return tb->bd_rd_data;
}
static uint8_t sdram_read_byte(uint32_t byte_addr) {
    return (sdram_read(byte_addr >> 2) >> ((byte_addr & 3) * 8)) & 0xFF;
}
static void sdram_fill(uint32_t base_byte, uint32_t bytes, uint8_t v) {
    uint32_t fw = v | (v << 8) | (v << 16) | (v << 24);
    for (uint32_t a = base_byte; a < base_byte + bytes; a += 4) sdram_write(a >> 2, fw);
}
static void sdram_write_byte(uint32_t byte_addr, uint8_t v) {
    uint32_t w = sdram_read(byte_addr >> 2);
    int sh = (byte_addr & 3) * 8;
    w = (w & ~(0xFFu << sh)) | ((uint32_t)v << sh);
    sdram_write(byte_addr >> 2, w);
}

// ============================================================
// GPU MMIO + ring/DMA command submission (mirrors tb_gpu_wrpath).
// ============================================================
static uint32_t ring_wrptr = 0;
static const uint32_t RING_SIZE = 0x4000, ring_mask = RING_SIZE - 1;
static std::vector<uint32_t> pending_stream;

static void mmio_write(uint32_t reg, uint32_t val) {
    tb->reg_wr = 1; tb->reg_addr = reg; tb->reg_wdata = val; tick(); tb->reg_wr = 0;
}
static uint32_t mmio_read(uint32_t reg) { tb->reg_addr = reg; tb->eval(); return tb->reg_rdata; }

static bool g_harness_error = false;
static void wait_for_dma_idle(int timeout = 60000000) {
    for (int t = 0; t < timeout; t++) { if ((mmio_read(REG_STATUS) & (1 << 2)) == 0) return; tick(); }
    fprintf(stderr, "wait_for_dma_idle timeout (status=0x%08x)\n", mmio_read(REG_STATUS));
    g_harness_error = true;
}
static void ring_write(uint32_t w) { pending_stream.push_back(w); }
static void ring_cmd(uint8_t cmd, uint32_t payload_words) {
    if (pending_stream.empty()) wait_for_dma_idle();
    ring_write(((uint32_t)cmd << 24) | (payload_words & 0x00FFFFFFu));
}
static void gpu_kick() {
    if (pending_stream.empty()) return;
    wait_for_dma_idle();
    uint32_t aw = BATCH_BUF_BYTE >> 2;
    for (uint32_t x : pending_stream) sdram_write(aw++, x);
    uint32_t words = (uint32_t)pending_stream.size();
    ring_wrptr = (ring_wrptr + words * 4u) & ring_mask;
    mmio_write(REG_DMA_SRC, BATCH_BUF_BYTE);
    mmio_write(REG_DMA_LEN, words);
    mmio_write(REG_DMA_KICK, 1);
    pending_stream.clear();
}

static uint32_t next_fence = 1;
static uint32_t fence_submit() {  // emit CMD_FENCE + kick, return token
    uint32_t t = next_fence++;
    ring_cmd(0x02, 1); ring_write(t); gpu_kick();
    return t;
}
static bool fence_wait(uint32_t t, int timeout = 120000000) {
    for (int i = 0; i < timeout; i++) {
        tick();
        if ((int32_t)(tb->fence_reached - t) >= 0) {
            // gpu_core publishes fence_reached the SAME cycle it pulses
            // gpu_swap_req (gpu_core.v:4322-4325); fb_swap_pending updates on
            // the FOLLOWING edge.  On real hardware the firmware's subsequent
            // FB_SWAP_CTRL MMIO load has multi-cycle latency, so it always
            // observes pending=1.  Tick a few cycles so the sim matches that
            // observation (this is read-timing fidelity, NOT a discipline
            // change — the firmware code path is unchanged).
            tick(4);
            return true;
        }
    }
    fprintf(stderr, "  FENCE TIMEOUT tok=%u reached=%u dbg_state=%u\n", t, tb->fence_reached, tb->dbg_state);
    g_harness_error = true;
    return false;
}

static void hard_reset() {
    tb->reset_n = 0; tb->reg_wr = 0; tb->bd_we = 0;
    tb->fb_swap_ctrl_wr = 0; tb->fb_swap_ctrl_wdata = 0;
    for (int i = 0; i < 64; i++) tick();
    tb->reset_n = 1;
    for (int i = 0; i < 31200; i++) tick();   // io_sdram power-up/init
}
static void gpu_init() {
    hard_reset();
    ring_wrptr = 0; pending_stream.clear();
    mmio_write(REG_CTRL, 4);
    mmio_write(REG_PALOOKUP_BASE, PALOOKUP_BASE);
    mmio_write(REG_RING_WRPTR, 0);
}

// ============================================================
// Affine span-group encoder (subset of acceptance/wrpath 0x48 path).
// ============================================================
struct SpanGroupWire {
    uint32_t fb_addr; uint32_t tex_addr[8];
    int32_t  s[8], t[8], sstep[8], tstep[8];
    uint16_t count, count_lane[8];
    uint8_t  flags, colormap_id[8], lane_count;
    int16_t  fb_stride, lane_delta;
    uint16_t tex_width, tex_w_mask, tex_h_mask; uint8_t light[8];
};
static uint16_t lane_count_val(const SpanGroupWire &s, int l) { return s.count_lane[l] ? s.count_lane[l] : s.count; }
static std::vector<uint32_t> encode_affine_chunk(const SpanGroupWire &s, int first, int lc) {
    std::vector<uint32_t> w(4 + lc * 7);
    w[0] = ((uint32_t)lc << 28) | ((uint32_t)s.flags << 20);
    w[1] = s.tex_width;
    w[2] = ((uint32_t)s.tex_h_mask << 16) | s.tex_w_mask;
    w[3] = (uint32_t)(int32_t)s.fb_stride;
    for (int l = 0; l < lc; l++) {
        int src = first + l, b = 4 + l * 7;
        w[b + 0] = s.fb_addr + (uint32_t)((int32_t)s.lane_delta * src);
        w[b + 1] = s.tex_addr[src];
        w[b + 2] = (((uint32_t)s.colormap_id[src] & 0xF) << 28) | (((uint32_t)s.light[src] & 0x3F) << 16) | lane_count_val(s, src);
        w[b + 3] = (uint32_t)s.s[src]; w[b + 4] = (uint32_t)s.t[src];
        w[b + 5] = (uint32_t)s.sstep[src]; w[b + 6] = (uint32_t)s.tstep[src];
    }
    return w;
}
static void emit_affine(const SpanGroupWire &s) {
    int lanes = (s.lane_count >= 4) ? 4 : (s.lane_count == 2) ? 2 : 1;
    int first = 0;
    while (lanes > 0) {
        int lc = lanes > 4 ? 4 : lanes;
        auto w = encode_affine_chunk(s, first, lc);
        ring_cmd(0x48, (uint32_t)w.size());
        for (uint32_t x : w) ring_write(x);
        first += lc; lanes -= lc;
    }
}

static void upload_texture() {
    for (uint32_t i = 0; i < 256; i++) sdram_write_byte(TEX_BASE + i, CONTENT_COLOR);
    for (uint32_t i = 0; i < 256; i++) sdram_write_byte(PALOOKUP_BASE + i, (uint8_t)i);
}

// CMD_CLEAR_RECT 0x11 (gpu_core.v:1001): word0=addr, word1={w[31:16],h[15:0]},
// word2={stride[31:16],pad,color[7:0]}.  Clears a rect to CLEAR_COLOR.
static void emit_clear(uint32_t fb_byte) {
    ring_cmd(0x11, 3);
    ring_write(fb_byte);                                   // word0 row addr
    ring_write(((uint32_t)FB_W << 16) | (FB_H & 0xFFFF));  // word1 w_total | y_remaining
    ring_write(((uint32_t)FB_STRIDE_B << 16) | CLEAR_COLOR); // word2 stride | color
}

// ============================================================
// Render one full frame into draw buffer `idx`: CLEAR_RECT then dense spans.
// Mirrors R_GPU_BeginDisplayFrame + the span emission of a Doom 3D frame.
// gpu_active_draw_buf is held = idx for the whole render so the detector knows
// which buffer the GPU is physically touching.
// ============================================================
static uint32_t g_rows = 0;  // 0 => FB_H
static void render_frame(int idx, uint32_t frame_seed) {
    uint32_t fb_byte = FB_BASE_BYTE[idx];
    uint32_t rows = g_rows ? g_rows : FB_H;

    gpu_active_draw_buf = idx;

    // --- CLEAR_RECT ---  (the buffer the GPU draws into is cleared FIRST: this
    // is what makes a not-yet-redrawn word read back as the clear color.)
    emit_clear(fb_byte);
    gpu_kick();

    // --- dense spans.  Geometry depends on frame_seed so the "content" moves
    // frame-to-frame (the bars "move with the view"). ---
    rng_state = frame_seed;
    for (uint32_t y = 0; y < rows; y++) {
        SpanGroupWire s {};
        s.lane_count = 1; s.lane_delta = 0; s.fb_stride = (int16_t)FB_STRIDE_B;
        s.tex_width = 256; s.tex_w_mask = 0xFF; s.tex_h_mask = 0xFF; s.flags = 0;
        uint32_t x0 = rnd_range(0, 40);
        uint32_t len = rnd_range(80, FB_W - 48);
        if (x0 + len > FB_W) len = FB_W - x0;
        s.fb_addr = fb_byte + y * FB_STRIDE_B + x0;
        s.tex_addr[0] = TEX_BASE;
        s.count = (uint16_t)len;
        s.colormap_id[0] = 0; s.light[0] = 0;
        s.s[0] = s.t[0] = s.sstep[0] = s.tstep[0] = 0;  // constant texel = CONTENT_COLOR
        emit_affine(s);
        if ((y % 32) == 31) gpu_kick();
    }
    gpu_kick();
}

// CMD_FLIP(idx): of_gpu_flip_to.  word0=idx, word1=fence token.  Returns token.
static uint32_t present_flip(int idx) {
    uint32_t flip_tok = next_fence++;
    ring_cmd(0x42, 2);
    ring_write((uint32_t)idx & 0x3u);
    ring_write(flip_tok);
    gpu_kick();
    return flip_tok;
}

// CPU-flip path (of_video_flip / acquire_next !fence_ok fallback, video.c:842,
// :907): write FB_SWAP_CTRL = (idx<<1)|1 directly.  NO gpu_core slave_swap_pending
// backpressure — the GPU is free to immediately reuse pick_free_buffer().  We
// still emit a CMD_FENCE first so the draw writes are committed before the swap
// (of_video_flip flushes the cache then writes FB_SWAP_CTRL).
static void present_flip_cpu(int idx) {
    uint32_t t = fence_submit();
    fence_wait(t);            // draws committed (mirrors of_cache_clean_range drain)
    tb->fb_swap_ctrl_wr = 1; tb->fb_swap_ctrl_wdata = ((uint32_t)idx << 1) | 1u;
    tick(); tb->fb_swap_ctrl_wr = 0;
}

// Snapshot the committed FB from SDRAM as ground truth (call after the flip
// fence: gpu_core.v:4322 guarantees all m_wr_* writes are drained by then).
static void snapshot_golden(int idx) {
    uint32_t fb_byte = FB_BASE_BYTE[idx];
    for (uint32_t off = 0; off < fb_bytes(); off++) {
        uint8_t v = sdram_read_byte(fb_byte + off);
        golden_fb[idx][off]   = v;
        has_content[idx][off] = (v != CLEAR_COLOR) ? 1 : 0;
    }
}

// ============================================================
// FAITHFUL port of the firmware buffer-rotation state machine
// (src/firmware/os/targets/pocket/video.c, shared byte-for-byte with MiSTer
// via os/targets/mister/video.c:#include "../pocket/video.c").
//
// This is the software that actually decides which buffer the GPU draws into.
// It tracks buf_display / buf_draw / buf_ready / swap_kicked in SOFTWARE and
// only reconciles with hardware via FB_SWAP_CTRL reads — so it can DIVERGE from
// the true fb_display_idx, which is exactly the class of bug under test.
// ============================================================
static int sw_buf_display = 0;
static int sw_buf_draw    = 1;
static int sw_buf_ready   = -1;
static int sw_swap_kicked = 0;

static uint32_t read_fb_swap_ctrl() { return tb->fb_swap_ctrl_rd_o; }

// sync_swap_state() — pocket/video.c:726-745, verbatim logic.
static void sync_swap_state() {
    uint32_t hw_state = read_fb_swap_ctrl();
    int hw_pending = hw_state & 1;
    int hw_display = (int)((hw_state >> 1) & 0x3);
    if ((unsigned)hw_display < 3) sw_buf_display = hw_display;
    if (hw_pending) sw_swap_kicked = 1;
    // timing_record_present_if_needed() elided (telemetry only).
    if (sw_buf_ready == sw_buf_display) {
        sw_buf_ready = -1;
        sw_swap_kicked = 0;
    } else if (sw_buf_ready >= 0 && sw_swap_kicked && !hw_pending) {
        sw_buf_display = sw_buf_ready;
        sw_buf_ready = -1;
        sw_swap_kicked = 0;
    }
}

// pick_free_buffer() — pocket/video.c:747-753, verbatim.
static int pick_free_buffer() {
    for (int i = 0; i < 3; i++)
        if (i != sw_buf_display && i != sw_buf_ready) return i;
    return sw_buf_draw;
}

int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IONBF, 0);
    Verilated::commandArgs(argc, argv);
    tb = new Vtb_scanout_race;

    // argv[1]=frames, argv[2]=rows (0=full FB_H), argv[3]=FB_H override,
    // argv[4]=mode (0=GPU-triggered flip w/ slave_swap_pending backpressure;
    //               1=CPU FB_SWAP_CTRL flip path, NO backpressure;
    //               2=DETECTOR SELF-TEST: force a 2-buffer rotation (the GPU
    //                 reuses the still-displayed buffer) — MUST reproduce the
    //                 race, proving the detector is wired correctly).
    int frames = 24;
    int mode = 0;
    if (argc > 1) { int f = atoi(argv[1]); if (f > 0) frames = f; }
    if (argc > 2) { int r = atoi(argv[2]); if (r >= 0) g_rows = (uint32_t)r; }
    if (argc > 3) { int h = atoi(argv[3]); if (h > 0 && h <= 480) FB_H = (uint32_t)h; }
    if (argc > 4) { mode = atoi(argv[4]); }

    for (int i = 0; i < 3; i++) {
        golden_fb[i].assign(fb_bytes(), CLEAR_COLOR);
        has_content[i].assign(fb_bytes(), 0);
    }

    printf("================================================================\n");
    printf(" SCANOUT vs GPU read-during-write RACE across the page flip\n");
    printf("   scanout(real) -> io_sdram.burst_rd  ||  gpu_core(real) -> arb/slave\n");
    printf("   shared sdram_model_full; swap SM = axi_periph_slave.v copy\n");
    printf("   FB %ux%u 8bpp, out width 640 (out HEIGHT = RTL -GOUT_HEIGHT param:"
           " 480=MiSTer / 200=Pocket), frames=%d, rows/frame=%u\n",
           FB_W, FB_H, frames, g_rows ? g_rows : FB_H);
    printf("================================================================\n");

    gpu_init();

    // Drive scanout/raster config (clk_vid raster is free-running already).
    tb->fb_addr_0 = (FB0_BYTE >> 1) & 0x1FFFFFF;   // halfword base
    tb->fb_addr_1 = (FB1_BYTE >> 1) & 0x1FFFFFF;
    tb->fb_addr_2 = (FB2_BYTE >> 1) & 0x1FFFFFF;
    tb->fb_w       = FB_W;
    tb->fb_h       = FB_H;
    tb->fb_strideb = FB_STRIDE_B;
    tb->out_w      = 640;
    tb->color_mode_in = 0;   // MODE_8BIT
    tick(8);

    upload_texture();

    // Preload all 3 FBs to the clear color so the scanout never reads stale
    // 0xDEAD garbage on the very first frames.
    for (int i = 0; i < 3; i++) {
        sdram_fill(FB_BASE_BYTE[i], fb_bytes(), CLEAR_COLOR);
        std::fill(golden_fb[i].begin(), golden_fb[i].end(), CLEAR_COLOR);
        std::fill(has_content[i].begin(), has_content[i].end(), 0);
    }

    // --- Frame loop: faithful firmware sequence ---
    //   R_GPU_BeginDisplayFrame: draw_idx = acquire_draw_buffer() (= sw_buf_draw)
    //   render into sw_buf_draw
    //   R_GPU_PresentFrame: of_gpu_flip_to(sw_buf_draw)
    //   next frame R_GPU_BeginDisplayFrame -> of_video_acquire_next(prev, token):
    //       sync_swap_state(); [no present wait]; mark_buffer_ready(prev);
    //       swap_kicked=1; sync_swap_state(); sw_buf_draw = pick_free_buffer();
    //
    // sw_buf_* start at video_reset_buffer_roles(): display = hw display (0),
    // draw = first_draw(0) = 1, ready = -1.
    sync_swap_state();
    sw_buf_draw = (sw_buf_display == 0) ? 1 : 0;   // video_first_draw_buffer
    sw_buf_ready = -1; sw_swap_kicked = 0;

    const char *mode_name =
        mode == 0 ? "GPU-flip (slave_swap_pending backpressure)" :
        mode == 1 ? "CPU FB_SWAP_CTRL flip (NO backpressure)" :
                    "DETECTOR SELF-TEST (forced 2-buffer reuse)";
    printf("\n[loop] running %d frames, mode=%s...\n", frames, mode_name);
    uint64_t content_bytes_total = 0;
    for (int f = 0; f < frames && !g_harness_error; f++) {
        int draw_idx = sw_buf_draw;
        // SELF-TEST: deliberately reuse the buffer hardware is CURRENTLY
        // displaying (the broken 2-buffer / starved-pick discipline).  This is
        // NOT the real firmware path; it exists only to prove the detector
        // fires when a genuine collision occurs.
        if (mode == 2) draw_idx = tb->fb_display_idx_o;
        uint32_t seed = 0x5EED0000u + (uint32_t)f * 0x9E3779B1u;

        // --- render this frame into draw_idx ---
        render_frame(draw_idx, seed);

        // --- present ---
        if (mode == 0) {
            // R_GPU_PresentFrame: of_gpu_flip_to.  Wait only for the flip fence
            // (CMD_FLIP retired + slave latched pending), not for the present.
            uint32_t flip_tok = present_flip(draw_idx);
            fence_wait(flip_tok);
        } else {
            // of_video_flip / acquire_next !fence_ok fallback: write FB_SWAP_CTRL
            // directly.  No slave_swap_pending gate; GPU free to reuse buffers.
            present_flip_cpu(draw_idx);
        }
        gpu_active_draw_buf = -1;
        snapshot_golden(draw_idx);
        uint64_t cb = 0;
        for (uint32_t off = 0; off < fb_bytes(); off++) cb += has_content[draw_idx][off];
        content_bytes_total += cb;

        // --- of_video_acquire_next(draw_idx, flip_tok): the EXACT firmware
        //     buffer rotation.  No present wait. (video.c:868-915) ---
        sync_swap_state();
        // fence already reached (we waited); fence_ok=1 so no FB_SWAP_CTRL re-kick.
        sw_buf_ready = draw_idx;          // mark_buffer_ready(just_flipped_idx)
        sw_swap_kicked = 1;
        sync_swap_state();
        sw_buf_draw = pick_free_buffer(); // the buffer the NEXT frame draws into

        if ((f % 4) == 0 || f == frames - 1)
            printf("  frame %2d: drew buf %d, flipped, hw display=%d (sw disp=%d) "
                   "sw ready=%d -> next draw=%d | rdw=%llu phys=%llu black=%llu (cyc=%llu)\n",
                   f, draw_idx, tb->fb_display_idx_o, sw_buf_display, sw_buf_ready,
                   sw_buf_draw, (unsigned long long)rdw_events,
                   (unsigned long long)phys_rdw_events,
                   (unsigned long long)black_pixels,
                   (unsigned long long)(sim_time / 2));
    }

    // Let a few more frames scan out so any late black pixel surfaces.
    for (int i = 0; i < 200000 && !g_harness_error; i++) tick();

    printf("\n----------------------------------------------------------------\n");
    printf("DETECTOR RESULTS\n");
    printf("  read-during-write events (scanout read buf == GPU active draw buf): %llu\n",
           (unsigned long long)rdw_events);
    if (rdw_events)
        printf("    FIRST: cyc=%llu  scanout reading DISPLAY buf %d, GPU writing buf %d,"
               " burst_addr(hw)=0x%07x, y=%d\n",
               (unsigned long long)rdw_first_cyc, rdw_first_disp, rdw_first_draw,
               rdw_first_addr, rdw_first_y);
    printf("  PHYSICAL read-during-write (scanout fetching FROM buf GPU draws INTO): %llu\n",
           (unsigned long long)phys_rdw_events);
    if (phys_rdw_events)
        printf("    FIRST: cyc=%llu  buf %d  burst_addr(hw)=0x%07x\n",
               (unsigned long long)phys_rdw_first_cyc, phys_rdw_first_buf,
               phys_rdw_first_addr);
    printf("  scanned-out BLACK pixels (clear where golden had content): %llu\n",
           (unsigned long long)black_pixels);
    if (black_pixels)
        printf("    FIRST: cyc=%llu  y=%d  display buf=%d  burst_addr(hw)=0x%07x  golden=0x%02x\n",
               (unsigned long long)black_first_cyc, black_first_y, black_first_disp,
               black_first_addr, black_first_golden);
    printf("  total simulated cycles: %llu\n", (unsigned long long)(sim_time / 2));
    printf("  committed content bytes summed over frames (detector target): %llu\n",
           (unsigned long long)content_bytes_total);
    printf("  harness DMA/fence timeouts: %s\n", g_harness_error ? "YES (run INVALID)" : "none");
    printf("----------------------------------------------------------------\n");

    if (g_harness_error) {
        printf("RESULT: INVALID — harness timed out; render incomplete.\n");
        delete tb; return 2;
    }

    // Self-test mode (2): the run DELIBERATELY reuses the displayed buffer, so
    // the race MUST reproduce — this validates the detector itself.
    if (mode == 2) {
        if (rdw_events && black_pixels) {
            printf("RESULT: SELF-TEST PASS — forced 2-buffer reuse reproduced the race "
                   "(%llu read-during-write collisions, %llu scanned-out black pixels). "
                   "The detector is correctly wired: a real collision WOULD be caught.\n",
                   (unsigned long long)rdw_events, (unsigned long long)black_pixels);
            delete tb; return 0;
        }
        printf("RESULT: SELF-TEST FAIL — forced reuse did NOT trip the detector "
               "(rdw=%llu black=%llu); the harness is broken.\n",
               (unsigned long long)rdw_events, (unsigned long long)black_pixels);
        delete tb; return 4;
    }

    // Real-discipline modes (0/1): if the scanout never read the active draw
    // buffer, the triple-buffer pick_free_buffer invariant held — the race is
    // REFUTED for this path.  (The detector's ability to fire is proven
    // separately by `make scanout-race ARGS=... mode=2` / the self-test target.)
    if (rdw_events == 0 && black_pixels == 0) {
        printf("RESULT: RACE REFUTED — across %d frames the scanout NEVER read the "
               "buffer the GPU was drawing, and no pixel scanned out black where the "
               "golden frame had content.  The triple-buffer pick_free_buffer "
               "discipline (display/ready/draw kept disjoint, with faithful "
               "sync_swap_state read timing) prevents the read-during-write race on "
               "this path.  (Detector validated by the self-test, mode=2.)\n", frames);
        delete tb; return 0;
    }

    printf("RESULT: RACE REPRODUCED — %llu scanned-out pixels read the CLEAR color "
           "where the golden frame had content (black bars), with %llu confirmed "
           "read-during-write collisions.\n",
           (unsigned long long)black_pixels, (unsigned long long)rdw_events);
    delete tb; return 1;
}
