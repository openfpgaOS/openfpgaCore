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

// ---------------------------------------------------------------------------
// SEPARATE aggressor PRNG (2026-09-01).  CRITICAL: the scene generator picks
// each span's x0/len from rnd(), and the aggressor driver used to draw from
// that SAME stream — but only in the contention run.  The two passes therefore
// rendered DIFFERENT PICTURES, and the byte diff reported that as "dropped/
// corrupted writes" (the count even moved with unrelated RTL edits, because
// any timing change alters how many aggressor draws happen).  Keeping the
// aggressors on their own stream makes the two passes render the IDENTICAL
// scene, which is the whole premise of the differential test.
static uint32_t aggr_rng_state = 0xC0FFEEu;
static inline uint32_t arnd() {
    uint32_t x = aggr_rng_state; x ^= x << 13; x ^= x >> 17; x ^= x << 5;
    aggr_rng_state = x; return x;
}
static inline uint32_t arnd_range(uint32_t lo, uint32_t hi) { return lo + (arnd() % (hi - lo + 1)); }

// ============================================================
// Memory map (word/byte addresses into sdram_model_full)
// ============================================================
static const uint32_t FB_A_BASE     = 0x00080000; // reference FB
static const uint32_t FB_B_BASE      = 0x00180000; // DUT FB
static const uint32_t BATCH_BUF_BYTE = 0x00040000; // doorbell-DMA scratch
static const uint32_t TEX_BASE       = 0x00060000; // 1-texel texture
static const uint32_t PALOOKUP_BASE  = 0x03FC0000; // matches gpu_core
static const uint32_t FB_W = 320, FB_H = 240;
static const uint32_t FB_BYTES = FB_W * FB_H * 2;

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

// ---- word-write stream recorder (2026-09-01, +1-column displacement hunt) --
// Every command the slave hands io_sdram, captured per run so the reference
// and contention streams can be diffed op-by-op.  Addresses are normalized to
// the run's FB base so the two runs are directly comparable.
struct WrOp { uint32_t addr; uint32_t data; uint8_t strb; uint8_t blen; };
static std::vector<WrOp> g_ref_ops, g_dut_ops;
static std::vector<WrOp>* g_rec = nullptr;
static uint32_t g_rec_wordbase = 0;
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
    if (m1_mode == 0 && m1_cooldown == 0 && (arnd() % g_aggr_intv) == 0) {
        int pick = arnd() % 2;
        if (pick == 0) { m1_mode = 1; m1_addr = CPU_BASE + arnd_range(0, 8192) * 4; m1_len = arnd_range(0, 7); m1_beat = 0; }
        else { m1_mode = 2; m1_addr = CPU_BASE + arnd_range(0, 8192) * 4; m1_len = arnd_range(0, 7); m1_beat = 0; m1_aw_done = m1_w_done = false; }
    }
    if (m1_cooldown) m1_cooldown--;
    if (m1_mode == 1) { tb->m1_arvalid = 1; tb->m1_araddr = m1_addr; tb->m1_arlen = m1_len; tb->m1_rready = 1; }
    else if (m1_mode == 2) {
        if (!m1_aw_done) { tb->m1_awvalid = 1; tb->m1_awaddr = m1_addr; tb->m1_awlen = m1_len; }
        if (!m1_w_done) { tb->m1_wvalid = 1; tb->m1_wdata = arnd(); tb->m1_wstrb = 0xF; tb->m1_wlast = (m1_beat == m1_len); }
    }
    // M2 bridge — sparse read/write.
    if (m2_mode == 0 && m2_cooldown == 0 && (arnd() % g_aggr_intv) == 0) {
        if (arnd() & 1) { m2_mode = 1; m2_addr = BRG_BASE + arnd_range(0, 4096) * 4; m2_len = arnd_range(0, 7); m2_beat = 0; }
        else { m2_mode = 2; m2_addr = BRG_BASE + arnd_range(0, 4096) * 4; m2_len = arnd_range(0, 7); m2_beat = 0; m2_aw_done = m2_w_done = false; }
    }
    if (m2_cooldown) m2_cooldown--;
    if (m2_mode == 1) { tb->m2_arvalid = 1; tb->m2_araddr = m2_addr; tb->m2_arlen = m2_len; }
    else if (m2_mode == 2) {
        if (!m2_aw_done) { tb->m2_awvalid = 1; tb->m2_awaddr = m2_addr; tb->m2_awlen = m2_len; }
        if (!m2_w_done) { tb->m2_wvalid = 1; tb->m2_wdata = arnd(); tb->m2_wstrb = 0xF; tb->m2_wlast = (m2_beat == m2_len); }
    }
    // M3 audio — sparse short reads.
    if (!m3_active && m3_cooldown == 0 && (arnd() % g_aggr_intv) == 0) { m3_active = true; m3_addr = AUD_BASE + arnd_range(0, 4096) * 4; m3_len = arnd_range(0, 3); }
    if (m3_cooldown) m3_cooldown--;
    if (m3_active) { tb->m3_arvalid = 1; tb->m3_araddr = m3_addr; tb->m3_arlen = m3_len; }
    // scanout inject — ONE bounded line-fetch burst per "scanline" with a long
    // gap, matching real scanout (NOT back-to-back).  The video burst_rd
    // outranks even the GPU in io_sdram, so frequent injection starves the
    // GPU command-fetch DMA (an artifact).  Keep it sparse + short.
    if (inj_busy == 0 && inj_cooldown == 0 && (arnd() % (g_aggr_intv * 8)) == 0) {
        tb->inj_burst_rd = 1; tb->inj_burst_addr = ((FB_B_BASE >> 2) << 1) & 0x1FFFFFF; tb->inj_burst_len = arnd_range(8, 32); inj_busy = 1;
    }
    if (inj_cooldown) inj_cooldown--;
}

static void react_aggressors_post() {
    if (!g_aggr) return;
    if (m1_mode == 1) { if (tb->m1_rvalid && tb->m1_rready && tb->m1_rlast) { m1_mode = 0; m1_cooldown = arnd_range(4, 24); } }
    else if (m1_mode == 2) {
        if (tb->m1_awvalid && tb->m1_awready) m1_aw_done = true;
        if (tb->m1_wvalid && tb->m1_wready) { m1_beat++; if (m1_beat > m1_len) m1_w_done = true; }
        if (tb->m1_bvalid) { m1_mode = 0; m1_cooldown = arnd_range(4, 24); }
    }
    if (m2_mode == 1) { if (tb->m2_rvalid && tb->m2_rready && tb->m2_rlast) { m2_mode = 0; m2_cooldown = arnd_range(4, 24); } }
    else if (m2_mode == 2) {
        if (tb->m2_awvalid && tb->m2_awready) m2_aw_done = true;
        if (tb->m2_wvalid && tb->m2_wready) { m2_beat++; if (m2_beat > m2_len) m2_w_done = true; }
        if (tb->m2_bvalid) { m2_mode = 0; m2_cooldown = arnd_range(4, 24); }
    }
    if (m3_active && tb->m3_rvalid && tb->m3_rready && tb->m3_rlast) { m3_active = false; m3_cooldown = arnd_range(1, 8); }
    if (inj_busy && tb->inj_burst_data_done) { inj_busy = 0; inj_cooldown = arnd_range(4, 32); }
}

// Tick wrapper: drive aggressors, evaluate, react.
static void record_write() {
    if (!g_rec || !tb->tap_wr_fire) return;
    uint32_t a = tb->tap_wr_addr;
    if (a < g_rec_wordbase) return;
    uint32_t rel = a - g_rec_wordbase;
    if (rel >= (FB_BYTES / 4) + 64) return;   // outside this run's FB window
    g_rec->push_back({rel, (uint32_t)tb->tap_wr_data,
                      (uint8_t)tb->tap_wr_strb, (uint8_t)tb->tap_wr_blen});
}

static void tick(int n = 1) {
    for (int i = 0; i < n; i++) {
        drive_aggressors_pre();
        tb->clk = 0; tb->eval(); sim_time++;
        // sample handshakes at the rising edge for the react step
        tb->clk = 1; tb->eval(); record_write(); sim_time++;
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
    for (uint32_t word: pending_stream) {
        while (((ring_wrptr/4u + 1u) & 4095u) == (mmio_read(REG_RING_RDPTR) & 4095u)) tick();
        mmio_write(REG_RING_WRPTR, word);
        ring_wrptr = (ring_wrptr+4u)&ring_mask;
    }
    mmio_write(REG_CTRL, 8);
    if (mmio_read(REG_STATUS)&(1u<<9)) { fprintf(stderr,"CPU ring overflow\n"); g_harness_error=true; }
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
    mmio_write(REG_CTRL, 16);
}

// Exact-current-QSF triangle and clear commands; no disabled compact-span op.
static uint32_t g_rows = FB_H;
static void emit_state(uint32_t fb_base) {
    uint32_t w[16]={};
    w[0]=fb_base; w[1]=FB_W*2; w[2]=2; w[3]=TEX_BASE;
    w[4]=1; w[5]=0; w[6]=0xA0u | (3u<<24); // RGB565 perspective, depth test+write
    w[11]=fb_base+0x40000; w[12]=FB_W*2; w[13]=2;
    w[14]=FB_W<<16; w[15]=FB_H<<16;
    ring_cmd(0x4A,16); for(uint32_t v:w)ring_write(v);
}
static void emit_tri(int x0,int y0,int x1,int y1,int x2,int y2,uint16_t color,int depth) {
    uint32_t w[17]={};
    w[0]=((uint32_t)y0<<16)|(x0*16); w[1]=((uint32_t)y1<<16)|(x1*16); w[2]=((uint32_t)y2<<16)|(x2*16);
    w[9]=w[10]=w[11]=1u<<16; w[12]=(uint32_t)color*0x10001u;w[13]=color;
    w[14]=w[15]=w[16]=depth;
    ring_cmd(0x4E,17);for(uint32_t v:w)ring_write(v);
}
static void render_scene(uint32_t fb_base) {
    rng_state=0x5EED1234u;
    // Arbitrary byte-aligned clears exercise mixed first/last strobes and
    // the no-allocate cache path before normal cached triangle writebacks.
    for(unsigned k=0;k<16;k++) {
        ring_cmd(0x11,3);ring_write(fb_base+k*FB_W*2+(k&3));
        ring_write(((33+k*7)<<16)|2);ring_write((FB_W*2<<16)|(0x40+k));
    }
    emit_state(fb_base);
    for(unsigned y=0;y<g_rows;y+=4) {
        int x0=rnd_range(1,31),x1=rnd_range(260,319),y1=y+4;
        uint16_t color=(uint16_t)(rnd()|0x0841u);
        emit_tri(x0,y,x1,y,x0,y1,color,65536+y*128);
        emit_tri(x1,y,x1,y1,x0,y1,color,65536+y*128);
        if((y&31)==28) {if(!submit_and_wait())return;}
    }
    submit_and_wait();
}

// Byte write into SDRAM (read-modify-write the containing word).
static void sdram_write_byte(uint32_t byte_addr, uint8_t v) {
    uint32_t w = sdram_read(byte_addr >> 2);
    int sh = (byte_addr & 3) * 8;
    w = (w & ~(0xFFu << sh)) | ((uint32_t)v << sh);
    sdram_write(byte_addr >> 2, w);
}

static void upload_texture() { sdram_write(TEX_BASE/4,0xffffffffu); }

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
    sdram_fill(FB_A_BASE+0x40000,FB_BYTES,0x00);
    tb->aggr_en = 0; g_aggr = false;
    g_rec = &g_ref_ops; g_rec_wordbase = FB_A_BASE / 4;
    render_scene(FB_A_BASE);
    g_rec = nullptr;
    printf("  reference render complete (cyc=%llu).\n", (unsigned long long)(sim_time / 2));

    // ---------- Pass B: DUT (aggressors ON) ----------
    printf("\n[Pass B] DUT render (aggressors ON: CPU+bridge+audio+scanout)...\n");
    gpu_init();
    upload_texture();
    sdram_fill(FB_B_BASE, FB_BYTES, 0x00);
    sdram_fill(FB_B_BASE+0x40000,FB_BYTES,0x00);
    tb->aggr_en = 1; g_aggr = true;
    g_rec = &g_dut_ops; g_rec_wordbase = FB_B_BASE / 4;
    render_scene(FB_B_BASE);
    g_rec = nullptr;
    tb->aggr_en = 0; g_aggr = false;
    clear_aggr_inputs();
    for (int i = 0; i < 500; i++) tick();
    printf("  DUT render complete (cyc=%llu).\n", (unsigned long long)(sim_time / 2));

    // ---------- Diff the two framebuffers ----------
    printf("\n[Stream] reference writes=%zu  contention writes=%zu\n",
           g_ref_ops.size(), g_dut_ops.size());
    {
        size_t n = g_ref_ops.size() < g_dut_ops.size() ? g_ref_ops.size() : g_dut_ops.size();
        size_t shown = 0, ndiff = 0, addrdiff = 0;
        for (size_t i = 0; i < n; i++) {
            const WrOp&a2=g_ref_ops[i]; const WrOp&b2=g_dut_ops[i];
            if (a2.addr==b2.addr && a2.data==b2.data && a2.strb==b2.strb && a2.blen==b2.blen) continue;
            ndiff++; if (a2.addr!=b2.addr) addrdiff++;
            if (shown < 8) {
                printf("  STREAM DIFF #%zu: ref addr=%06x data=%08x strb=%x blen=%u | dut addr=%06x data=%08x strb=%x blen=%u%s\n",
                       i, a2.addr,a2.data,a2.strb,a2.blen, b2.addr,b2.data,b2.strb,b2.blen,
                       (a2.addr!=b2.addr) ? "   <-- ADDRESS DIFFERS" : "");
                shown++;
            }
        }
        printf("[Stream] op differences: %zu of %zu compared (address differs in %zu)\n", ndiff, n, addrdiff);
        // window around the FIRST divergence: is an op INSERTED (stream shifts)
        // or MODIFIED in place?  Inserted => GPU emitted an extra write;
        // modified => same op with wrong address/data.
        for (size_t i = 0; i < n; i++) {
            if (g_ref_ops[i].addr==g_dut_ops[i].addr && g_ref_ops[i].data==g_dut_ops[i].data
                && g_ref_ops[i].strb==g_dut_ops[i].strb) continue;
            size_t lo = i>4 ? i-4 : 0, hi = i+6 < n ? i+6 : n;
            printf("[Stream] window around first divergence (op %zu):\n", i);
            for (size_t k=lo;k<hi;k++)
                printf("   %s%zu ref %06x/%x/%08x   dut %06x/%x/%08x\n",
                       k==i?">>":"  ", k,
                       g_ref_ops[k].addr,g_ref_ops[k].strb,g_ref_ops[k].data,
                       g_dut_ops[k].addr,g_dut_ops[k].strb,g_dut_ops[k].data);
            // does the ref op reappear later in the dut stream (insertion)?
            for (size_t k=i;k<i+12 && k<g_dut_ops.size();k++)
                if (g_dut_ops[k].addr==g_ref_ops[i].addr && g_dut_ops[k].strb==g_ref_ops[i].strb) {
                    printf("[Stream] ref op %zu reappears in dut at %zu (skew +%zu) => INSERTION\n", i, k, k-i);
                    break;
                }
            break;
        }
        printf("[Stream] => %s\n", (ndiff==0 && g_ref_ops.size()==g_dut_ops.size())
               ? "streams IDENTICAL: fault is BELOW the slave (io_sdram/PHY/model)"
               : "streams DIFFER: fault is AT OR ABOVE the slave");
    }
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
    uint32_t nonzero=0,depthdiff=0;uint64_t fbhash=14695981039346656037ull,zhash=fbhash;
    for(uint32_t off=0;off<FB_BYTES;off++) {
        uint8_t a=sdram_read_byte(FB_A_BASE+off),z=sdram_read_byte(FB_A_BASE+0x40000+off);
        if(a)nonzero++;
        fbhash=(fbhash^a)*1099511628211ull; zhash=(zhash^z)*1099511628211ull;
        if(z!=sdram_read_byte(FB_B_BASE+0x40000+off))depthdiff++;
    }
    printf("QSF gate: nonzero=%u depthdiff=%u fbhash=%016llx zhash=%016llx\n",nonzero,depthdiff,(unsigned long long)fbhash,(unsigned long long)zhash);
    if(nonzero<10000||depthdiff)g_harness_error=true;


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
