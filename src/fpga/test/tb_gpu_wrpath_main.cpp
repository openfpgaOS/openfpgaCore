//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------
//
// DECISIVE escalation: REAL gpu_core framebuffer-WRITE path through the REAL
// MiSTer shared-SDRAM stack (arbiter + slave + io_sdram + sdram_model_full),
// under multi-master contention.
//
// This closes the gap left by tb_sdram_cont (which drove the arbiter M0 port
// with an IDEALIZED AXI generator): here the AW/W bursts that reach the
// arbiter are produced by gpu_core's OWN framebuffer-write coalescer (fbwq,
// gpu_core.v:2813-2941), including its AW-ahead overlap path (2894-2937) that
// only activates when the m_wr_* W channel sees real backpressure mid-burst —
// the MiSTer-only condition the always-fast sink in tb_gpu never reproduces.
//
// Differential method:
//   Run an identical dense-FB-write GPU command stream TWICE:
//     (A) reference: aggressors OFF, GPU sees a fast write path (~Pocket load)
//     (B) DUT:       aggressors ON  (CPU+bridge+audio hammer + scanout inject),
//                    so the GPU's m_wr_* channel is backpressured (~MiSTer load)
//   Both render into DISTINCT framebuffers via the SAME real write stack.
//   Then diff the two framebuffers byte-for-byte.  ANY divergence = the GPU
//   write path produced different pixels under contention = a dropped/corrupted
//   beat = the bug (black bars / missing geometry).
//
// Deterministic pseudo-randomness only (xorshift32); no real RNG.
//
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <vector>
#include "Vtb_gpu_wrpath.h"
#include "verilated.h"

static Vtb_gpu_wrpath *tb;
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
static const uint32_t FB_A_BASE     = 0x00080000; // reference FB
static const uint32_t FB_B_BASE      = 0x00180000; // DUT FB
static const uint32_t BATCH_BUF_BYTE = 0x00040000; // doorbell-DMA scratch
static const uint32_t TEX_BASE       = 0x00060000; // 1-texel texture
static const uint32_t PALOOKUP_BASE  = 0x03FC0000; // matches gpu_core
static const uint32_t FB_W = 320, FB_H = 200;
static const uint32_t FB_BYTES = FB_W * FB_H;

// Aggressor regions (avoid FB/batch/tex)
static const uint32_t CPU_BASE = 0x00C00000;
static const uint32_t BRG_BASE = 0x00D00000;
static const uint32_t AUD_BASE = 0x00E00000;

// ---- MMIO register map (word index) ----
enum {
    REG_CTRL = 0, REG_RING_WRPTR = 1, REG_DMA_SRC = 3, REG_RING_RDPTR = 4,
    REG_STATUS = 5, REG_FENCE = 6, REG_DMA_LEN = 7, REG_DMA_KICK = 11,
    REG_PALOOKUP_BASE = 12,
};

// ============================================================
// Aggressor FSM state (M1 CPU rw, M2 bridge rw, M3 audio r) + scanout inject
// ============================================================
static int m1_mode = 0; static uint32_t m1_addr = 0; static int m1_len = 0, m1_beat = 0;
static bool m1_aw_done = false, m1_w_done = false; static int m1_cooldown = 0;
static int m2_mode = 0; static uint32_t m2_addr = 0; static int m2_len = 0, m2_beat = 0;
static bool m2_aw_done = false, m2_w_done = false; static int m2_cooldown = 0;
static bool m3_active = false; static uint32_t m3_addr = 0; static int m3_len = 0; static int m3_cooldown = 0;
static int inj_busy = 0, inj_cooldown = 0;
static bool g_aggr = false;
// Mean inter-launch interval for each aggressor (cycles).  Smaller = heavier
// contention.  Tuned so the slave is heavily loaded but the GPU command DMA
// is not starved (renders complete).  Overridable via argv[2].
static uint32_t g_aggr_intv = 40;

static void clear_aggr_inputs() {
    tb->m1_arvalid = 0; tb->m1_awvalid = 0; tb->m1_wvalid = 0; tb->m1_rready = 1;
    tb->m2_arvalid = 0; tb->m2_awvalid = 0; tb->m2_wvalid = 0; tb->m2_rready = 1;
    tb->m3_arvalid = 0; tb->m3_rready = 1;
    tb->inj_burst_rd = 0;
}

// Drive aggressors for one cycle (called inside the tick wrapper).
static void drive_aggressors_pre() {
    clear_aggr_inputs();
    if (!g_aggr) return;

    // Aggressors model HEAVY-BUT-REAL MiSTer load: bursty, NOT saturating.
    // The GPU is arbiter M0 (highest after the audio/CPU-guard), so its own
    // command-fetch reads must still make progress — saturating aggressors
    // (continuous + RREADY-stall) starve the doorbell DMA and wedge the render
    // (an artifact, not the bug).  These fire as occasional short bursts.
    //
    // g_aggr_lvl scales intensity: launch only when the low PRNG bits are 0,
    // i.e. ~1/INTV cycles, then a multi-cycle cooldown.  This keeps the slave
    // ~50-80% busy (real heavy load) so the GPU write queue periodically fills
    // and the fbwq AW-ahead backpressure path activates, without starving reads.

    // M1 CPU — read or serialized write, sparse.
    if (m1_mode == 0 && m1_cooldown == 0 && (rnd() % g_aggr_intv) == 0) {
        int pick = rnd() % 2;
        if (pick == 0) { m1_mode = 1; m1_addr = CPU_BASE + rnd_range(0, 8192) * 4; m1_len = rnd_range(0, 7); m1_beat = 0; }
        else { m1_mode = 2; m1_addr = CPU_BASE + rnd_range(0, 8192) * 4; m1_len = rnd_range(0, 7); m1_beat = 0; m1_aw_done = m1_w_done = false; }
    }
    if (m1_cooldown) m1_cooldown--;
    if (m1_mode == 1) { tb->m1_arvalid = 1; tb->m1_araddr = m1_addr; tb->m1_arlen = m1_len; tb->m1_rready = 1; }
    else if (m1_mode == 2) {
        if (!m1_aw_done) { tb->m1_awvalid = 1; tb->m1_awaddr = m1_addr; tb->m1_awlen = m1_len; }
        if (!m1_w_done) { tb->m1_wvalid = 1; tb->m1_wdata = rnd(); tb->m1_wstrb = 0xF; tb->m1_wlast = (m1_beat == m1_len); }
    }
    // M2 bridge — sparse read/write.
    if (m2_mode == 0 && m2_cooldown == 0 && (rnd() % g_aggr_intv) == 0) {
        if (rnd() & 1) { m2_mode = 1; m2_addr = BRG_BASE + rnd_range(0, 4096) * 4; m2_len = rnd_range(0, 7); m2_beat = 0; }
        else { m2_mode = 2; m2_addr = BRG_BASE + rnd_range(0, 4096) * 4; m2_len = rnd_range(0, 7); m2_beat = 0; m2_aw_done = m2_w_done = false; }
    }
    if (m2_cooldown) m2_cooldown--;
    if (m2_mode == 1) { tb->m2_arvalid = 1; tb->m2_araddr = m2_addr; tb->m2_arlen = m2_len; }
    else if (m2_mode == 2) {
        if (!m2_aw_done) { tb->m2_awvalid = 1; tb->m2_awaddr = m2_addr; tb->m2_awlen = m2_len; }
        if (!m2_w_done) { tb->m2_wvalid = 1; tb->m2_wdata = rnd(); tb->m2_wstrb = 0xF; tb->m2_wlast = (m2_beat == m2_len); }
    }
    // M3 audio — sparse short reads.
    if (!m3_active && m3_cooldown == 0 && (rnd() % g_aggr_intv) == 0) { m3_active = true; m3_addr = AUD_BASE + rnd_range(0, 4096) * 4; m3_len = rnd_range(0, 3); }
    if (m3_cooldown) m3_cooldown--;
    if (m3_active) { tb->m3_arvalid = 1; tb->m3_araddr = m3_addr; tb->m3_arlen = m3_len; }
    // scanout inject — ONE bounded line-fetch burst per "scanline" with a long
    // gap, matching real scanout (NOT back-to-back).  The video burst_rd
    // outranks even the GPU in io_sdram, so frequent injection starves the
    // GPU command-fetch DMA (an artifact).  Keep it sparse + short.
    if (inj_busy == 0 && inj_cooldown == 0 && (rnd() % (g_aggr_intv * 8)) == 0) {
        tb->inj_burst_rd = 1; tb->inj_burst_addr = ((FB_B_BASE >> 2) << 1) & 0x1FFFFFF; tb->inj_burst_len = rnd_range(8, 32); inj_busy = 1;
    }
    if (inj_cooldown) inj_cooldown--;
}

static void react_aggressors_post() {
    if (!g_aggr) return;
    if (m1_mode == 1) { if (tb->m1_rvalid && tb->m1_rready && tb->m1_rlast) { m1_mode = 0; m1_cooldown = rnd_range(4, 24); } }
    else if (m1_mode == 2) {
        if (tb->m1_awvalid && tb->m1_awready) m1_aw_done = true;
        if (tb->m1_wvalid && tb->m1_wready) { m1_beat++; if (m1_beat > m1_len) m1_w_done = true; }
        if (tb->m1_bvalid) { m1_mode = 0; m1_cooldown = rnd_range(4, 24); }
    }
    if (m2_mode == 1) { if (tb->m2_rvalid && tb->m2_rready && tb->m2_rlast) { m2_mode = 0; m2_cooldown = rnd_range(4, 24); } }
    else if (m2_mode == 2) {
        if (tb->m2_awvalid && tb->m2_awready) m2_aw_done = true;
        if (tb->m2_wvalid && tb->m2_wready) { m2_beat++; if (m2_beat > m2_len) m2_w_done = true; }
        if (tb->m2_bvalid) { m2_mode = 0; m2_cooldown = rnd_range(4, 24); }
    }
    if (m3_active && tb->m3_rvalid && tb->m3_rready && tb->m3_rlast) { m3_active = false; m3_cooldown = rnd_range(1, 8); }
    if (inj_busy && tb->inj_burst_data_done) { inj_busy = 0; inj_cooldown = rnd_range(4, 32); }
}

// Tick wrapper: drive aggressors, evaluate, react.
static void tick(int n = 1) {
    for (int i = 0; i < n; i++) {
        drive_aggressors_pre();
        tb->clk = 0; tb->eval(); sim_time++;
        // sample handshakes at the rising edge for the react step
        tb->clk = 1; tb->eval(); sim_time++;
        react_aggressors_post();
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

static bool g_harness_error = false;  // any DMA/fence timeout => invalid run
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
static bool submit_and_wait(int timeout = 120000000) {
    uint32_t t = next_fence++;
    ring_cmd(0x02, 1); ring_write(t); gpu_kick();
    for (int i = 0; i < timeout; i++) { tick(); if ((int32_t)(tb->fence_reached - t) >= 0) return true; }
    fprintf(stderr, "  FENCE TIMEOUT tok=%u reached=%u dbg_state=%u\n", t, tb->fence_reached, tb->dbg_state);
    g_harness_error = true;
    return false;
}

static void hard_reset() {
    tb->reset_n = 0; tb->reg_wr = 0; tb->bd_we = 0; tb->aggr_en = 0;
    clear_aggr_inputs();
    for (int i = 0; i < 64; i++) tick();
    tb->reset_n = 1;
    // io_sdram needs a full 30000-cycle power-up + init before it issues
    // refreshes at the normal cadence and accepts word ops.  reset_n was just
    // pulsed (resets the whole stack incl. io_sdram), so always re-boot fully.
    for (int i = 0; i < 31200; i++) tick();
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
// Produces dense contiguous FB-word writes that drive fbwq coalescing.
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

// Render a dense scene of many horizontal affine spans into fb_base.
// Each span writes a contiguous run of FB words -> exercises the GPU fbwq
// burst coalescer (2..8-beat AW bursts) exactly as Doom/Quake floor/wall fills.
static uint32_t g_rows = FB_H;
static void render_scene(uint32_t fb_base) {
    rng_state = 0x5EED1234u;  // SAME geometry every call (reference vs DUT)
    for (uint32_t y = 0; y < g_rows; y += 1) {
        SpanGroupWire s {};
        s.lane_count = 1;
        s.lane_delta = 0;
        s.fb_stride = (int16_t)FB_W;
        s.tex_width = 256;
        s.tex_w_mask = 0xFF;
        s.tex_h_mask = 0xFF;
        s.flags = 0;
        // one lane: a horizontal run across the row
        uint32_t x0 = rnd_range(0, 32);
        uint32_t len = rnd_range(64, FB_W - 40);
        if (x0 + len > FB_W) len = FB_W - x0;
        s.fb_addr = fb_base + y * FB_W + x0;
        s.tex_addr[0] = TEX_BASE;
        s.count = (uint16_t)len;
        s.colormap_id[0] = 0;
        s.light[0] = 0;
        // CONSTANT texture sampling: s/t fixed, no walk.  Every pixel in a
        // span samples the SAME texel -> the rendered span is a UNIFORM value.
        // This decouples pixel VALUE from read timing, so a diff can ONLY come
        // from the WRITE path (a missing pixel reads back as 0x00 clear; a
        // corrupted beat reads back as a wrong value).  Isolates the suspect.
        s.s[0] = 0;
        s.t[0] = 0;
        s.sstep[0] = 0;
        s.tstep[0] = 0;
        emit_affine(s);
        // Kick in modest batches so the ring never overflows (16KB/4=4096
        // words) but DMA waits are infrequent.  ~28 words/row * 64 rows ~=
        // 1800 words fits, but kick every 32 rows to bound ring occupancy.
        if ((y % 32) == 31) gpu_kick();
    }
    gpu_kick();
    bool ok = submit_and_wait();
    // Final robust drain: wait until the GPU is fully idle (busy=0) AND the
    // DMA/transluc status bits are clear, so every fbwq write has retired into
    // SDRAM before we read the framebuffer back.
    for (int i = 0; i < 4000000; i++) {
        if (!tb->busy && (mmio_read(REG_STATUS) & ((1 << 2) | (1 << 3))) == 0) break;
        tick();
    }
    if (!ok) fprintf(stderr, "  render_scene: fence did not complete!\n");
}

// Byte write into SDRAM (read-modify-write the containing word).
static void sdram_write_byte(uint32_t byte_addr, uint8_t v) {
    uint32_t w = sdram_read(byte_addr >> 2);
    int sh = (byte_addr & 3) * 8;
    w = (w & ~(0xFFu << sh)) | ((uint32_t)v << sh);
    sdram_write(byte_addr >> 2, w);
}

static void upload_texture() {
    // CONSTANT texture: every texel = 0xCD, so every rendered pixel is 0xCD
    // (after the identity colormap).  A clean span is a solid 0xCD run.
    for (uint32_t i = 0; i < 256; i++) sdram_write_byte(TEX_BASE + i, 0xCD);
    // Identity colormap slot 0 (256 bytes; lane 0 uses colormap_id 0).
    for (uint32_t i = 0; i < 256; i++) sdram_write_byte(PALOOKUP_BASE + i, (uint8_t)i);
}

int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IONBF, 0);  // unbuffered: progress visible live
    Verilated::commandArgs(argc, argv);
    tb = new Vtb_gpu_wrpath;

    // Optional overrides: argv[1] = rows, argv[2] = aggressor interval.
    if (argc > 1) { int r = atoi(argv[1]); if (r > 0 && (uint32_t)r <= FB_H) g_rows = r; }
    if (argc > 2) { int v = atoi(argv[2]); if (v >= 4) g_aggr_intv = v; }

    printf("================================================================\n");
    printf(" REAL gpu_core FB-write path through MiSTer SDRAM under contention\n");
    printf(" gpu_core fbwq -> arbiter M0 -> slave -> io_sdram -> sdram_model\n");
    printf(" rows=%u\n", g_rows);
    printf("================================================================\n");

    int total_diff = 0;

    // ---------- Pass A: reference (aggressors OFF) ----------
    printf("\n[Pass A] reference render (aggressors OFF, fast write path)...\n");
    gpu_init();
    upload_texture();
    sdram_fill(FB_A_BASE, FB_BYTES, 0x00);
    tb->aggr_en = 0; g_aggr = false;
    render_scene(FB_A_BASE);
    printf("  reference render complete (cyc=%llu).\n", (unsigned long long)(sim_time / 2));

    // ---------- Pass B: DUT (aggressors ON) ----------
    printf("\n[Pass B] DUT render (aggressors ON: CPU+bridge+audio+scanout)...\n");
    gpu_init();
    upload_texture();
    sdram_fill(FB_B_BASE, FB_BYTES, 0x00);
    tb->aggr_en = 1; g_aggr = true;
    render_scene(FB_B_BASE);
    tb->aggr_en = 0; g_aggr = false;
    clear_aggr_inputs();
    for (int i = 0; i < 500; i++) tick();
    printf("  DUT render complete (cyc=%llu).\n", (unsigned long long)(sim_time / 2));

    // ---------- Diff the two framebuffers ----------
    printf("\n[Diff] comparing reference FB vs contention FB byte-for-byte...\n");
    int first_diff = -1, ndiff = 0;
    for (uint32_t off = 0; off < FB_BYTES; off++) {
        uint8_t a = sdram_read_byte(FB_A_BASE + off);
        uint8_t b = sdram_read_byte(FB_B_BASE + off);
        if (a != b) {
            ndiff++;
            if (first_diff < 0) first_diff = (int)off;
            if (ndiff <= 20)
                printf("  DIFF @ off 0x%05x (x=%u y=%u): ref=0x%02x dut=0x%02x\n",
                       off, off % FB_W, off / FB_W, a, b);
        }
    }
    total_diff += ndiff;

    // Count how many diffs are "missing pixel" (ref!=0, dut==0) vs corruption.
    int n_missing = 0, n_corrupt = 0;
    for (uint32_t off = 0; off < FB_BYTES; off++) {
        uint8_t a = sdram_read_byte(FB_A_BASE + off), b = sdram_read_byte(FB_B_BASE + off);
        if (a != b) { if (b == 0x00) n_missing++; else n_corrupt++; }
    }

    printf("\n----------------------------------------------------------------\n");
    printf("framebuffer byte diffs (reference vs contention): %d / %u\n", ndiff, FB_BYTES);
    printf("  of which missing-pixel (dut=0x00): %d   corrupted (dut!=ref,!=0): %d\n",
           n_missing, n_corrupt);
    if (first_diff >= 0)
        printf("FIRST DIFF: off 0x%05x (x=%u y=%u)\n", first_diff, first_diff % FB_W, first_diff / FB_W);
    printf("total simulated cycles: %llu\n", (unsigned long long)(sim_time / 2));
    printf("harness DMA/fence timeouts: %s\n", g_harness_error ? "YES (run INVALID)" : "none");
    printf("----------------------------------------------------------------\n");

    if (g_harness_error) {
        printf("RESULT: INVALID — harness DMA/fence timed out; render incomplete, "
               "diff not trustworthy. (Reduce aggressor intensity / raise timeout.)\n");
        delete tb; return 2;
    }
    if (total_diff == 0)
        printf("RESULT: PASS — GPU FB output IDENTICAL with/without contention.\n");
    else
        printf("RESULT: FAIL — contention changed %d FB bytes (dropped/corrupt write).\n", total_diff);

    delete tb;
    return total_diff ? 1 : 0;
}
