//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------
//
// FINAL CONVERGENT EXPERIMENT driver — exercise the gpu_core fbwq AW-ahead
// OVERLAP path to a BYTE-EXACT verdict under a W-channel-EXCLUSIVE stall.
//
// Method:
//   * Render a DENSE multi-span scene (Doom/Quake wall pattern: many
//     horizontal contiguous runs => 2..8-beat fbwq write bursts) into the FB
//     through the REAL gpu_core fbwq -> arbiter -> slave -> io_sdram stack.
//   * Backpressure ONLY the FB-write W channel via the testbench's write-
//     completion stall (wstall_*).  The command-fetch READ path is
//     uncontended -> fences NEVER time out.
//   * Maintain a byte-exact GOLDEN framebuffer (computed independently from
//     the geometry; constant-texture so pixel VALUE is decoupled from read
//     timing).  After frame completion, read the WHOLE FB back through the
//     SDRAM model and assert EVERY pixel.  ANY pixel left at clear/sentinel
//     (or wrong) = a dropped/corrupt FB write = THE BUG.
//   * Sweep W-stall depth + phase to find the trigger.
//   * Confirm via the AW-ahead counters that the path actually FIRED — a
//     clean result is meaningless unless fbwq_aw_ahead_issue/w_chain_start
//     fired many times.
//
// Deterministic pseudo-randomness only (xorshift32).
//
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <vector>
#include "Vtb_gpu_wstall.h"
#include "verilated.h"

static Vtb_gpu_wstall *tb;
static uint64_t sim_time = 0;

static uint32_t rng_state = 0xBADC0DEu;
static inline uint32_t rnd() {
    uint32_t x = rng_state; x ^= x << 13; x ^= x >> 17; x ^= x << 5;
    rng_state = x; return x;
}
static inline uint32_t rnd_range(uint32_t lo, uint32_t hi) { return lo + (rnd() % (hi - lo + 1)); }

// ============================================================
// Memory map (word/byte addresses into sdram_model_full)
// ============================================================
static const uint32_t FB_BASE       = 0x00080000; // framebuffer
static const uint32_t BATCH_BUF_BYTE = 0x00040000; // doorbell-DMA scratch
static const uint32_t TEX_BASE       = 0x00060000; // textures (one per colormap)
static const uint32_t PALOOKUP_BASE  = 0x03FC0000; // matches gpu_core
static const uint32_t FB_W = 320, FB_H = 200;
static const uint32_t FB_BYTES = FB_W * FB_H;
static const uint8_t  CLEAR_VAL = 0x00;

// ---- MMIO register map (word index) ----
enum {
    REG_CTRL = 0, REG_RING_WRPTR = 1, REG_DMA_SRC = 3, REG_RING_RDPTR = 4,
    REG_STATUS = 5, REG_FENCE = 6, REG_DMA_LEN = 7, REG_DMA_KICK = 11,
    REG_PALOOKUP_BASE = 12,
};

// ============================================================
// Tick wrapper.  No aggressors — backpressure is purely the W-stall, which
// lives inside the testbench pulse adapter and is controlled by wstall_*.
// ============================================================
static void tick(int n = 1) {
    for (int i = 0; i < n; i++) {
        tb->clk = 0; tb->eval(); sim_time++;
        tb->clk = 1; tb->eval(); sim_time++;
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
// MMIO + ring/DMA command submission (mirrors acceptance harness)
// ============================================================
static uint32_t ring_wrptr = 0;
static const uint32_t RING_SIZE = 0x4000, ring_mask = RING_SIZE - 1;
static std::vector<uint32_t> pending_stream;

static void mmio_write(uint32_t reg, uint32_t val) {
    tb->reg_wr = 1; tb->reg_addr = reg; tb->reg_wdata = val; tick(); tb->reg_wr = 0;
}
static uint32_t mmio_read(uint32_t reg) { tb->reg_addr = reg; tb->eval(); return tb->reg_rdata; }

static bool g_harness_error = false;
static int g_trace_left = 0;
static void wait_for_dma_idle(int timeout = 4000000) {
    for (int t = 0; t < timeout; t++) { if ((mmio_read(REG_STATUS) & (1 << 2)) == 0) return; tick(); }
    fprintf(stderr, "wait_for_dma_idle TIMEOUT (status=0x%08x)\n", mmio_read(REG_STATUS));
    g_harness_error = true;
}
static void ring_write(uint32_t w) { pending_stream.push_back(w); }
static void ring_cmd(uint8_t cmd, uint32_t payload_words) {
    if (pending_stream.empty()) wait_for_dma_idle();
    ring_write(((uint32_t)cmd << 24) | (payload_words & 0x00FFFFFFu));
}
// Wait for the GPU to fully consume the ring + retire all writes (busy low,
// dma/transluc idle).  Bounds ring occupancy so the doorbell DMA never laps
// unconsumed commands (which would silently overwrite them in the ring BRAM).
static void wait_for_idle(int timeout = 4000000) {
    for (int t = 0; t < timeout; t++) {
        if (g_trace_left > 0) { tb->trace_en = 1; g_trace_left--; } else tb->trace_en = 0;
        if (!tb->busy && (mmio_read(REG_STATUS) & ((1 << 2) | (1 << 3))) == 0) return;
        tick();
    }
    fprintf(stderr, "wait_for_idle TIMEOUT (status=0x%08x busy=%d)\n",
            mmio_read(REG_STATUS), tb->busy);
    g_harness_error = true;
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
static bool submit_and_wait(int timeout = 8000000) {
    uint32_t t = next_fence++;
    ring_cmd(0x02, 1); ring_write(t); gpu_kick();
    for (int i = 0; i < timeout; i++) {
        if (g_trace_left > 0) { tb->trace_en = 1; g_trace_left--; }
        else tb->trace_en = 0;
        tick();
        if ((int32_t)(tb->fence_reached - t) >= 0) return true;
    }
    fprintf(stderr, "  FENCE TIMEOUT tok=%u reached=%u dbg_state=%u wq=%u fbwq=%u\n",
            t, tb->fence_reached, tb->dbg_state, tb->dbg_gpu_wq_count, tb->dbg_fbwq_count);
    g_harness_error = true;
    return false;
}

static void hard_reset() {
    tb->reset_n = 0; tb->reg_wr = 0; tb->bd_we = 0;
    tb->wstall_en = 0; tb->wstall_depth = 0; tb->wstall_phase = 0; tb->trace_en = 0;
    for (int i = 0; i < 64; i++) tick();
    tb->reset_n = 1;
    for (int i = 0; i < 31200; i++) tick();   // io_sdram power-up + init
}
static void gpu_init() {
    hard_reset();
    ring_wrptr = 0; pending_stream.clear();
    mmio_write(REG_CTRL, 4);                  // ring_reset
    mmio_write(REG_PALOOKUP_BASE, PALOOKUP_BASE);
    mmio_write(REG_RING_WRPTR, 0);
}

// ============================================================
// Affine span-group encoder (subset of acceptance harness 0x48 path).
// ============================================================
struct SpanGroupWire {
    uint32_t fb_addr;
    uint32_t tex_addr[8];
    int32_t  s[8], t[8], sstep[8], tstep[8];
    uint16_t count, count_lane[8];
    uint8_t  flags, colormap_id[8], lane_count;
    int16_t  fb_stride, lane_delta;
    uint16_t tex_width, tex_w_mask, tex_h_mask;
    uint8_t  light[8];
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
        w[b + 3] = (uint32_t)s.s[src];
        w[b + 4] = (uint32_t)s.t[src];
        w[b + 5] = (uint32_t)s.sstep[src];
        w[b + 6] = (uint32_t)s.tstep[src];
    }
    return w;
}
static void emit_affine(const SpanGroupWire &s) {
    int lanes = (s.lane_count == 8) ? 8 : (s.lane_count == 4) ? 4 : (s.lane_count == 2) ? 2 : 1;
    int first = 0;
    while (lanes > 0) {
        int lc = lanes > 4 ? 4 : lanes;
        auto w = encode_affine_chunk(s, first, lc);
        ring_cmd(0x48, (uint32_t)w.size());
        for (uint32_t x : w) ring_write(x);
        first += lc; lanes -= lc;
    }
}

// ============================================================
// DIFFERENTIAL reference framebuffer.
//
// The byte-exact "golden" is the GPU's OWN output rendered with the W-stall
// DISABLED (depth=0): same RTL, same scene => guaranteed-correct expected
// pixels, immune to any C++ model error in colormap/span semantics.  Each
// stalled run renders the IDENTICAL scene and is diffed against this
// reference.  ANY divergence is attributable ONLY to the W-stall perturbing
// the GPU write path => a dropped/corrupt FB write = THE BUG.
//
// To keep pixel VALUE decoupled from READ timing (so a diff can only come
// from the WRITE path), every span uses the identity colormap (slot 0, which
// IS initialised) over a CONSTANT texture whose single texel value varies per
// span — the rendered span is a uniform run of that value.  A missing write
// reads back CLEAR; a corrupt beat reads a wrong (but deterministic) value.
// ============================================================
static std::vector<uint8_t> ref_fb;     // captured reference render (FB_BYTES)
static std::vector<uint8_t> golden;     // independently-computed expected FB
static bool g_build_golden = false;     // render_scene fills 'golden' when set

// Texel value per span position so neighbouring spans differ (a dropped
// boundary beat then shows as a wrong/clear value distinct from its row).
static uint8_t span_texel(uint32_t y, uint32_t x) {
    uint8_t v = (uint8_t)(0x40 + ((y * 7 + x * 13 + 1) & 0x7F)); // 0x40..0xBF, never 0x00
    return v;
}

// Render a DENSE Doom/Quake wall pattern: every row is fully covered by a
// run of horizontal spans of varying start/length => the GPU fbwq coalesces
// 2..8-beat write bursts exactly like wall/floor fills.
static uint32_t g_rows = FB_H;
static int g_trace_budget = 0;          // >0: emit AW-ahead trace for this many fence-wait cycles
static void render_scene(uint32_t fb_base) {
    rng_state = 0x5EED1234u;            // SAME geometry every call
    if (g_build_golden) golden.assign(FB_BYTES, CLEAR_VAL);
    // FB_W=320 is word-aligned (320%4==0).  Tile each row with WORD-ALIGNED
    // spans whose length is a MULTIPLE OF 4 so every FB word is full-strobe
    // (0xF) and word-consecutive => the fbwq forms long 2..8-beat bursts
    // (the Doom/Quake wall/floor fill pattern that the AW-ahead path
    // coalesces).  Span fill value is constant within a span (identity
    // colormap over a constant texel), so a dropped beat reads back CLEAR
    // and a corrupt beat reads a deterministic wrong value.
    for (uint32_t y = 0; y < g_rows; y += 1) {
        uint32_t x = 0;
        while (x < FB_W) {
            uint32_t words = rnd_range(1, 10);          // 1..10 words per span
            uint32_t len = words * 4;
            if (x + len > FB_W) len = FB_W - x;          // FB_W%4==0 keeps it aligned
            if (len == 0) break;
            uint8_t tv = span_texel(y, x);

            SpanGroupWire s {};
            s.lane_count = 1;
            s.lane_delta = 0;
            // fb_stride is the PER-PIXEL BYTE STEP (gpu_core.v:1152).  =1 makes
            // a HORIZONTAL run of word-consecutive byte writes -> 4 pixels fill
            // one full-strobe word -> consecutive words LINK in the fbwq ->
            // multi-beat (2..8) write bursts (the floor/wall-fill pattern the
            // AW-ahead coalescer was built for).  (=FB_W would walk DOWN a
            // 1-wide column, giving only single-beat writes.)
            s.fb_stride = (int16_t)1;
            s.tex_width = 256;
            s.tex_w_mask = 0xFF;
            s.tex_h_mask = 0xFF;
            s.flags = 0;
            s.fb_addr = fb_base + y * FB_W + x;          // word-aligned start
            s.tex_addr[0] = TEX_BASE + (uint32_t)tv;
            s.count = (uint16_t)len;
            s.colormap_id[0] = 0;
            s.light[0] = 0;
            s.s[0] = 0; s.t[0] = 0; s.sstep[0] = 0; s.tstep[0] = 0;
            emit_affine(s);
            if (g_build_golden)
                for (uint32_t i = 0; i < len; i++) {
                    uint32_t o = y * FB_W + x + i;
                    if (o < FB_BYTES) golden[o] = tv;
                }
            x += len;
        }
        // Kick + DRAIN every few rows so the doorbell DMA never laps
        // unconsumed ring entries (the ring BRAM has no producer backpressure;
        // overrunning it silently drops commands -> looks like missing FB
        // writes).  Draining bounds ring occupancy to one small batch.
        if ((y % 8) == 7) { gpu_kick(); wait_for_idle(); }
    }
    gpu_kick();
    bool ok = submit_and_wait();
    // Robust drain: GPU fully idle AND DMA/transluc status clear so every
    // fbwq write has retired into SDRAM before readback.
    for (int i = 0; i < 4000000; i++) {
        if (!tb->busy && (mmio_read(REG_STATUS) & ((1 << 2) | (1 << 3))) == 0) break;
        tick();
    }
    if (!ok) fprintf(stderr, "  render: fence did not complete!\n");
}

static void upload_textures() {
    // A 256-entry byte texture where texel[v] == v, so a span sampling s=0 of
    // a block based at TEX_BASE+v reads value v.  Identity colormap slot 0.
    for (uint32_t v = 0; v < 256; v++) sdram_write_byte(TEX_BASE + v, (uint8_t)v);
    for (uint32_t i = 0; i < 256; i++) sdram_write_byte(PALOOKUP_BASE + i, (uint8_t)i);
}

// ============================================================
// One full run at a given W-stall (depth, phase).  If is_ref, captures the
// rendered FB into ref_fb (the differential golden).  Otherwise diffs the
// rendered FB against ref_fb byte-exact.
// ============================================================
struct RunStats {
    int ndiff, n_missing, n_corrupt, first_diff;
    uint32_t aw_issue, w_chain, w_chain_noW, wstall_applied, wready_low, wq_full;
    uint64_t cyc;
    bool invalid;
};
static RunStats do_run(uint8_t depth, uint8_t phase, bool is_ref) {
    RunStats r {}; r.first_diff = -1;
    g_harness_error = false;
    gpu_init();
    upload_textures();
    sdram_fill(FB_BASE, FB_BYTES, CLEAR_VAL);
    uint64_t c0 = sim_time;
    tb->wstall_en = (depth > 0) ? 1 : 0;
    tb->wstall_depth = depth;
    tb->wstall_phase = phase;
    g_build_golden = is_ref;            // build the independent golden on the ref pass
    render_scene(FB_BASE);
    g_build_golden = false;
    tb->wstall_en = 0; tb->wstall_depth = 0; tb->wstall_phase = 0;
    for (int i = 0; i < 1000; i++) tick();
    r.cyc = (sim_time - c0) / 2;

    r.aw_issue       = tb->cnt_aw_ahead_issue;
    r.w_chain        = tb->cnt_w_chain_start;
    r.w_chain_noW    = tb->cnt_w_chain_noW;
    r.wstall_applied = tb->cnt_wstall_applied;
    r.wready_low     = tb->cnt_wready_low_burst0;
    r.wq_full        = tb->cnt_gpu_wq_full;
    r.invalid = g_harness_error;
    tb->trace_en = 0;

    if (is_ref) {
        ref_fb.resize(FB_BYTES);
        int nz = 0, gold_diff = 0, gfirst = -1, gmiss = 0, gcorrupt = 0;
        for (uint32_t off = 0; off < FB_BYTES; off++) {
            ref_fb[off] = sdram_read_byte(FB_BASE + off);
            if (ref_fb[off] != CLEAR_VAL) nz++;
            if (ref_fb[off] != golden[off]) {
                gold_diff++;
                if (gfirst < 0) gfirst = (int)off;
                if (ref_fb[off] == CLEAR_VAL) gmiss++; else gcorrupt++;
                if (gold_diff <= 12)
                    printf("    REF/GOLD DIFF @ off 0x%05x (x=%u y=%u): gold=0x%02x ref=0x%02x\n",
                           off, off % FB_W, off / FB_W, golden[off], ref_fb[off]);
            }
        }
        printf("  reference captured: %d / %u non-clear FB bytes\n", nz, FB_BYTES);
        printf("  ref/gold breakdown: missing(ref=clear)=%d corrupt(ref!=gold,!=clear)=%d\n",
               gmiss, gcorrupt);
        // ANCHOR: the reference render MUST match the independently-computed
        // golden, else the differential baseline is itself corrupt.
        r.ndiff = gold_diff;
        r.first_diff = gfirst;
        if (gold_diff == 0)
            printf("  reference vs INDEPENDENT golden: byte-exact (anchor OK)\n");
        else
            printf("  reference vs INDEPENDENT golden: %d MISMATCH (first off 0x%05x x=%u y=%u) "
                   "-- baseline NOT trustworthy\n",
                   gold_diff, gfirst, gfirst % FB_W, gfirst / FB_W);
        return r;
    }

    for (uint32_t off = 0; off < FB_BYTES; off++) {
        uint8_t got = sdram_read_byte(FB_BASE + off);
        uint8_t exp = ref_fb[off];
        if (got != exp) {
            r.ndiff++;
            if (r.first_diff < 0) r.first_diff = (int)off;
            if (got == CLEAR_VAL) r.n_missing++; else r.n_corrupt++;
            if (r.ndiff <= 16)
                printf("    DIFF @ off 0x%05x (x=%u y=%u): ref=0x%02x got=0x%02x\n",
                       off, off % FB_W, off / FB_W, exp, got);
        }
    }
    return r;
}

int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IONBF, 0);
    Verilated::commandArgs(argc, argv);
    tb = new Vtb_gpu_wstall;
    ref_fb.reserve(FB_BYTES);

    if (argc > 1) { int r = atoi(argv[1]); if (r > 0 && (uint32_t)r <= FB_H) g_rows = r; }

    printf("================================================================\n");
    printf(" gpu_core fbwq AW-ahead path — W-channel-EXCLUSIVE stall verdict\n");
    printf(" command-fetch READ uncontended; FB-write W channel stalled only\n");
    printf(" rows=%u  DIFFERENTIAL byte-exact FB check (vs no-stall reference)\n", g_rows);
    printf("================================================================\n");

    // ---- Focused AW-ahead handshake trace (bounded window) ----
    if (argc > 2 && atoi(argv[2]) > 0) {
        printf("\n[trace] AW-ahead handshake, first %d render cycles (depth=32) ...\n", atoi(argv[2]));
        g_trace_left = atoi(argv[2]);
        do_run(32, 0, /*is_ref=*/false);   // result ignored; just to emit trace
        g_trace_left = 0;
    }

    // ---- Capture the differential reference (depth=0, no stall) ----
    printf("\n[reference] render scene with W-stall DISABLED ...\n");
    RunStats ref = do_run(0, 0, /*is_ref=*/true);
    printf("  ref cyc=%llu  AW-ahead(even w/o stall): issue=%u w_chain=%u  "
           "wready_low@burst0=%u wq_full=%u\n",
           (unsigned long long)ref.cyc, ref.aw_issue, ref.w_chain,
           ref.wready_low, ref.wq_full);
    if (ref.invalid) {
        printf("RESULT: INVALID — reference render did not complete (fence timeout).\n");
        delete tb; return 2;
    }
    if (ref.ndiff != 0) {
        printf("RESULT: INVALID — reference render does NOT match the independent "
               "golden (%d bytes); the no-stall baseline is itself dropping/corrupting "
               "writes, so the differential cannot be trusted.  Investigate before "
               "concluding anything about the AW-ahead path.\n", ref.ndiff);
        delete tb; return 2;
    }

    // ---- Sweep the W-stall and diff each against the reference ----
    // depth = #cycles per 64-cycle period that WRITE forwards are blocked
    // (duty cycle).  Sweep light->heavy and several phase offsets so the
    // blocked window aligns to many burst boundaries.
    struct { uint8_t depth, phase; const char *tag; } sweep[] = {
        {4,  0,  "depth=4  phase=0  (6% duty)"},
        {8,  0,  "depth=8  phase=0  (12% duty)"},
        {16, 0,  "depth=16 phase=0  (25% duty)"},
        {24, 0,  "depth=24 phase=0  (37% duty)"},
        {32, 0,  "depth=32 phase=0  (50% duty)"},
        {40, 0,  "depth=40 phase=0  (62% duty)"},
        {48, 0,  "depth=48 phase=0  (75% duty)"},
        {16, 7,  "depth=16 phase=7"},
        {24, 13, "depth=24 phase=13"},
        {32, 5,  "depth=32 phase=5"},
        {32, 17, "depth=32 phase=17"},
        {40, 9,  "depth=40 phase=9"},
        {40, 23, "depth=40 phase=23"},
        {48, 11, "depth=48 phase=11"},
    };
    int nsweep = sizeof(sweep) / sizeof(sweep[0]);

    int worst_diff = 0, total_fail = 0, invalid_runs = 0;
    bool any_path_fired = (ref.aw_issue > 0 || ref.w_chain > 0);
    uint32_t max_issue = ref.aw_issue, max_chain = ref.w_chain;
    for (int i = 0; i < nsweep; i++) {
        printf("\n[run %d] %s ...\n", i, sweep[i].tag);
        RunStats r = do_run(sweep[i].depth, sweep[i].phase, /*is_ref=*/false);
        printf("  cyc=%llu  AW-ahead: issue=%u w_chain=%u (noW=%u)  "
               "wstall_applied=%u wready_low@burst0=%u wq_full=%u\n",
               (unsigned long long)r.cyc, r.aw_issue, r.w_chain, r.w_chain_noW,
               r.wstall_applied, r.wready_low, r.wq_full);
        if (r.invalid) {
            printf("  RESULT: INVALID — fence/DMA timeout (command-fetch starved? should NOT happen).\n");
            invalid_runs++;
            continue;
        }
        printf("  FB diffs vs reference: %d  (missing=%d corrupt=%d)\n",
               r.ndiff, r.n_missing, r.n_corrupt);
        if (r.first_diff >= 0)
            printf("  FIRST DIFF off 0x%05x (x=%u y=%u)\n",
                   r.first_diff, r.first_diff % FB_W, r.first_diff / FB_W);
        if (r.aw_issue > max_issue) max_issue = r.aw_issue;
        if (r.w_chain  > max_chain) max_chain = r.w_chain;
        if (r.aw_issue > 0 || r.w_chain > 0) any_path_fired = true;
        if (r.ndiff > worst_diff) worst_diff = r.ndiff;
        if (r.ndiff > 0) total_fail++;
        printf("  -> %s\n", r.ndiff == 0 ? "byte-exact" : "MISMATCH (dropped/corrupt FB write)");
    }

    printf("\n================================================================\n");
    printf(" SUMMARY\n");
    printf("   AW-ahead path fired: %s  (max issue=%u, max w_chain=%u)\n",
           any_path_fired ? "YES" : "NO (path NOT exercised!)", max_issue, max_chain);
    printf("   stalled runs with FB mismatch: %d / %d   worst byte-diff: %d   invalid: %d\n",
           total_fail, nsweep, worst_diff, invalid_runs);
    printf("================================================================\n");

    if (!any_path_fired) {
        printf("RESULT: INCONCLUSIVE — the AW-ahead path never fired; a clean FB "
               "does NOT clear it.  Increase stall depth / scene density.\n");
        delete tb; return 3;
    }
    if (total_fail > 0) {
        printf("RESULT: FAIL — the AW-ahead path under W-stall DROPPED/CORRUPTED FB "
               "writes (the bug reproduced).\n");
        delete tb; return 1;
    }
    printf("RESULT: PASS — AW-ahead path provably FIRED and FB stayed byte-exact "
           "across the W-stall sweep.  fbwq AW-ahead path CLEARED.\n");
    delete tb; return 0;
}
