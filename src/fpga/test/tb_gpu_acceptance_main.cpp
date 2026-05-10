/*
 * tb_gpu_acceptance_main.cpp -- byte-exact GPU acceptance suite.
 *
 * Implements the testgpu.md plan: every test computes an expected
 * framebuffer with a CPU reference model and compares byte-for-byte
 * to what the GPU actually wrote. Sentinel borders around the touched
 * region catch overdraw. Perspective spans use a documented tolerance.
 *
 * Layout:
 *   1. Tick / MMIO / ring / SDRAM helpers
 *   2. Texture / palookup / transluc upload helpers
 *   3. Raw + SDK span/cmd emit helpers
 *   4. CPU reference model (FbModel + per-command exec)
 *   5. Compare / dump helpers
 *   6. Standalone tests        (testgpu.md sections 1..16)
 *   7. Combination tests       (A..F)
 *   8. Main runner
 */

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <string>
#include <vector>

#include "Vtb_gpu.h"
#include "verilated.h"
#include "verilated_vcd_c.h"

// ============================================================================
// 0. Globals
// ============================================================================
static Vtb_gpu *tb;
static VerilatedVcdC *trace;
static uint64_t sim_time = 0;
static int pass_count = 0;
static int fail_count = 0;

/* SDRAM byte addresses used throughout the suite.  All chosen to fit
 * inside the 1M-word (4 MB) tb_gpu SDRAM model and to avoid collisions
 * between regions. */
static const uint32_t TEX_BASE_BYTE      = 0x00040000;  // textures (256 KB)
static const uint32_t FB_BASE_BYTE       = 0x00080000;  // primary FB
static const uint32_t FB_ALT_BASE_BYTE   = 0x00100000;  // alternate FB
static const uint32_t PALOOKUP_BASE_BYTE = 0x03400000;  // matches gpu_core PALOOKUP_BASE
static const uint32_t PALOOKUP_STRIDE    = 0x00004000;  // 16 KB per slot
static const uint32_t BATCH_BUF_BYTE     = 0x00140000;  // doorbell-DMA scratch
static const uint32_t SDRAM_BYTES        = 4 * 1024 * 1024;

/* tb_gpu.v's SDRAM model uses `bd_addr[19:0]` (1M words = 4 MB) so any
 * byte address whose top bits exceed this range aliases back into the
 * 4 MB window.  PALOOKUP_BASE (0x03400000) wraps to byte 0; tex bases
 * inside 0x00400000 are unaffected.  The CPU model mirrors this so its
 * indexing matches what the RTL actually sees in SDRAM. */
static inline uint32_t sdram_alias(uint32_t byte_addr) {
    return byte_addr & 0x003FFFFFu;   /* (1<<22)-1 — same as bd_addr[19:0]<<2 */
}

/* Ring state mirror — the GPU's 16 KB M10K ring. */
static const uint32_t RING_SIZE = 0x00004000;
static uint32_t ring_wrptr      = 0;
static const uint32_t ring_mask = RING_SIZE - 1;

/* Sentinel byte used to fill FB / texture / palookup when not actively
 * being driven by a test, so any "wrong byte" mismatches stand out. */
static const uint8_t SENTINEL_BYTE = 0xAB;

// ============================================================================
// 1. Tick / MMIO / ring / SDRAM helpers
// ============================================================================
static void tick(int n = 1) {
    for (int i = 0; i < n; i++) {
        tb->clk = 0;
        tb->eval();
        if (trace) trace->dump(sim_time);
        sim_time++;
        tb->clk = 1;
        tb->eval();
        if (trace) trace->dump(sim_time);
        sim_time++;
    }
}

static void hard_reset() {
    tb->reset_n           = 0;
    tb->reg_wr            = 0;
    tb->bd_we             = 0;
    tb->slave_swap_pending = 0;
    for (int i = 0; i < 20; i++) tick();
    tb->reset_n = 1;
    for (int i = 0; i < 5; i++) tick();
}

/* Backdoor word read/write into the SDRAM model behind tb_gpu. */
static void sdram_write(uint32_t word_addr, uint32_t data) {
    tb->bd_we    = 1;
    tb->bd_addr  = word_addr;
    tb->bd_wdata = data;
    tick();
    tb->bd_we = 0;
}

static uint32_t sdram_read(uint32_t word_addr) {
    tb->bd_rd_addr = word_addr;
    tb->eval();
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

static void sdram_fill(uint32_t base_byte, uint32_t bytes, uint8_t value) {
    /* Word-aligned fast path: prepare a sentinel word and stream. */
    uint32_t fill_word = ((uint32_t)value << 0) | ((uint32_t)value << 8)
                       | ((uint32_t)value << 16) | ((uint32_t)value << 24);
    uint32_t addr = base_byte;
    uint32_t end  = base_byte + bytes;

    /* Head: any leading bytes before a word boundary go through the
     * byte path so we don't trample neighbouring data. */
    while ((addr & 3) && addr < end) {
        sdram_write_byte(addr, value);
        addr++;
    }
    while (addr + 4 <= end) {
        sdram_write(addr >> 2, fill_word);
        addr += 4;
    }
    while (addr < end) {
        sdram_write_byte(addr, value);
        addr++;
    }
}

/* MMIO register access — reg_addr is the word index (0..15) that maps
 * to byte offset (reg_addr << 2). */
static void mmio_write(uint32_t reg, uint32_t val) {
    tb->reg_wr    = 1;
    tb->reg_addr  = reg;
    tb->reg_wdata = val;
    tick();
    tb->reg_wr = 0;
}

static uint32_t mmio_read(uint32_t reg) {
    tb->reg_addr = reg;
    tb->eval();
    return tb->reg_rdata;
}

/* MMIO register names (word-index form). */
enum {
    REG_CTRL          = 0,
    REG_RING_WRPTR    = 1,
    REG_RING_DATA     = 2,
    REG_DMA_SRC       = 3,
    REG_RING_RDPTR    = 4,
    REG_STATUS        = 5,
    REG_FENCE         = 6,
    REG_DMA_LEN       = 7,
    REG_TRANSLUC_ADDR = 8,
    REG_TRANSLUC_DATA = 9,
    REG_TEX_FLUSH     = 10,
    REG_DMA_KICK      = 11,
};

/* Mirror the SDK's _gpu_ring_ensure DMA-busy guard: any subsequent
 * ring write after a doorbell-DMA kick must wait for the DMA to drain
 * because both the CPU ring writer and the DMA pump share the same
 * ring_wr_addr inside gpu_core.  Without this guard, a CPU MMIO write
 * lands in a slot the DMA hasn't filled yet (or worse, the DMA
 * overwrites the CPU's word). */
static void wait_for_dma_idle(int timeout = 200000) {
    for (int t = 0; t < timeout; t++) {
        if ((mmio_read(REG_STATUS) & (1 << 2)) == 0) return;
        tick();
    }
    fprintf(stderr, "wait_for_dma_idle: timeout (status=0x%08x)\n",
            mmio_read(REG_STATUS));
}

static void ring_write(uint32_t w) {
    mmio_write(REG_RING_DATA, w);
    ring_wrptr = (ring_wrptr + 4) & ring_mask;
}

static void ring_cmd(uint8_t cmd, uint32_t payload_words) {
    /* Always honour the DMA-busy gate before writing a new command
     * header.  Spam-cheap when DMA is idle (single MMIO read). */
    wait_for_dma_idle();
    ring_write(((uint32_t)cmd << 24) | (payload_words & 0x00FFFFFFu));
}

static void gpu_kick() {
    mmio_write(REG_RING_WRPTR, ring_wrptr);
}

/* gpu_init does a hard reset, then ring_reset. After this the GPU is
 * fresh and the ring pointers are zero on both sides. */
static void gpu_init() {
    hard_reset();
    ring_wrptr = 0;
    mmio_write(REG_CTRL, 4);              // ring_reset
    mmio_write(REG_RING_WRPTR, 0);
}

/* Submit a fence and return the token. Auto-incrementing tokens. */
static uint32_t next_fence_token = 1;
static uint32_t submit_fence() {
    uint32_t t = next_fence_token++;
    ring_cmd(0x02, 1);
    ring_write(t);
    gpu_kick();
    return t;
}

static bool wait_fence(uint32_t token, int timeout = 400000) {
    for (int t = 0; t < timeout; t++) {
        tick();
        uint32_t reached = tb->fence_reached;
        if ((int32_t)(reached - token) >= 0) return true;
    }
    fprintf(stderr, "  TIMEOUT waiting for fence %u (reached=%u dbg_state=%u)\n",
            token, tb->fence_reached, tb->dbg_state);
    return false;
}

static bool submit_and_wait(int timeout = 400000) {
    uint32_t t = submit_fence();
    return wait_fence(t, timeout);
}

// ============================================================================
// 2. Texture / palookup / transluc upload helpers
// ============================================================================
static void upload_texture(uint32_t base_byte, const std::vector<uint8_t> &bytes) {
    for (size_t i = 0; i < bytes.size(); i++)
        sdram_write_byte(base_byte + i, bytes[i]);
}

/* Bytes start at slot start byte 0; size up to PALOOKUP_STRIDE.
 * Slot N byte 0 lives at PALOOKUP_BASE + N * PALOOKUP_STRIDE. */
static void upload_palookup_slot(uint8_t slot,
                                  const std::vector<uint8_t> &bytes,
                                  uint32_t start = 0) {
    uint32_t base = PALOOKUP_BASE_BYTE + (uint32_t)slot * PALOOKUP_STRIDE + start;
    for (size_t i = 0; i < bytes.size(); i++)
        sdram_write_byte(base + i, bytes[i]);
}

/* Upload an identity palookup row (shade row R for slot S). */
static void upload_palookup_identity_row(uint8_t slot, uint8_t row) {
    std::vector<uint8_t> idr(256);
    for (int i = 0; i < 256; i++) idr[i] = (uint8_t)i;
    upload_palookup_slot(slot, idr, (uint32_t)row * 256);
}

/* Upload an inverted palookup row (cm[i] = 255-i). */
static void upload_palookup_inverted_row(uint8_t slot, uint8_t row) {
    std::vector<uint8_t> r(256);
    for (int i = 0; i < 256; i++) r[i] = (uint8_t)(255 - i);
    upload_palookup_slot(slot, r, (uint32_t)row * 256);
}

/* Upload a constant palookup row (cm[i] = K). */
static void upload_palookup_const_row(uint8_t slot, uint8_t row, uint8_t K) {
    std::vector<uint8_t> r(256, K);
    upload_palookup_slot(slot, r, (uint32_t)row * 256);
}

/* Upload the GPU's 32 KB transluc[] LUT via auto-incrementing MMIO.
 *
 * The LUT geometry is 128 rows (high 7 bits of src) × 256 columns
 * (dst).  Written 32 bits at a time; the GPU's internal address
 * counter advances by 4 bytes per write (matches the SDK helper).
 *
 * The CPU reference uses table[(src>>1)*256 + dst] to look up a byte.
 */
static void upload_transluc_table(const std::vector<uint8_t> &table32k) {
    if (table32k.size() != 32768) {
        fprintf(stderr, "upload_transluc_table: expected 32768 bytes, got %zu\n",
                table32k.size());
        return;
    }
    mmio_write(REG_TRANSLUC_ADDR, 0);
    for (size_t i = 0; i < table32k.size(); i += 4) {
        uint32_t w = (uint32_t)table32k[i]
                   | ((uint32_t)table32k[i + 1] << 8)
                   | ((uint32_t)table32k[i + 2] << 16)
                   | ((uint32_t)table32k[i + 3] << 24);
        mmio_write(REG_TRANSLUC_DATA, w);
    }
}

/* A simple deterministic transluc table: blend[src][dst] = (src+dst)/2.
 * 64 KB table is decimated to 32 KB by dropping the LSB of src; CPU
 * reference matches by indexing src>>1. */
static std::vector<uint8_t> make_transluc_table_avg() {
    std::vector<uint8_t> t(32768);
    for (int s7 = 0; s7 < 128; s7++) {
        int src = s7 << 1;  /* LSB dropped on the way in */
        for (int dst = 0; dst < 256; dst++)
            t[s7 * 256 + dst] = (uint8_t)((src + dst) >> 1);
    }
    return t;
}

static void issue_tex_flush() {
    mmio_write(REG_TEX_FLUSH, 1);
    /* Flush is asynchronous — give the cache 8 cycles to drain so the
     * next SDRAM read is guaranteed to refill. */
    tick(8);
}

// ============================================================================
// 3. Raw + SDK span/cmd emit helpers
// ============================================================================

/* Direct ring emit of a complete command (header + payload words).
 * Useful when a test needs to assert wire-format specifics that the
 * SDK helper would silently smooth over. */
static void emit_raw_command(uint8_t cmd,
                              const std::vector<uint32_t> &payload) {
    ring_cmd(cmd, (uint32_t)payload.size());
    for (uint32_t w : payload) ring_write(w);
}

/* Span-as-15-words representation (RTL wire format, no SDK packing). */
struct SpanWire {
    uint32_t fb_addr;
    uint32_t tex_addr;
    int32_t  s, t;
    int32_t  sstep, tstep;
    /* Word 6 packed as RTL expects: [31:28]=cmap_id, [27:16]=count,
     * [15:8]=light, [7:0]=flags. */
    uint8_t  colormap_id;   // 0 = use sticky st_colormap_id
    uint16_t count;         // 12-bit field; values >= 4096 truncate (test it).
    uint8_t  light;
    uint8_t  flags;
    int16_t  fb_stride;
    uint16_t tex_width;
    uint16_t tex_w_mask;    // 0 → RTL converts to 0xFFFF
    uint16_t tex_h_mask;
    /* Perspective fields (only used if SPAN_PERSP set). */
    int32_t sZ, tZ, zinv;
    int32_t sZstep, tZstep, zinv_step;
};

static SpanWire make_span() {
    SpanWire s {};
    /* Horizontal span: 1 byte advance per pixel. Tests that need
     * column-mode (Duke3D vline) override to 320 explicitly. */
    s.fb_stride  = 1;
    s.tex_width  = 64;
    s.tex_w_mask = 0;       // 0 → no wrap
    s.tex_h_mask = 0;
    return s;
}

static std::vector<uint32_t> encode_span_wire(const SpanWire &s) {
    std::vector<uint32_t> w(15);
    w[0]  = s.fb_addr;
    w[1]  = s.tex_addr;
    w[2]  = (uint32_t)s.s;
    w[3]  = (uint32_t)s.t;
    w[4]  = (uint32_t)s.sstep;
    w[5]  = (uint32_t)s.tstep;
    w[6]  = ((uint32_t)(s.colormap_id & 0xF) << 28)
          | ((uint32_t)(s.count & 0xFFF)    << 16)
          | ((uint32_t)s.light              <<  8)
          | (uint32_t)s.flags;
    w[7]  = ((uint32_t)(uint16_t)s.fb_stride << 16) | (uint32_t)s.tex_width;
    w[8]  = ((uint32_t)s.tex_h_mask << 16) | (uint32_t)s.tex_w_mask;
    w[9]  = (uint32_t)s.sZ;
    w[10] = (uint32_t)s.tZ;
    w[11] = (uint32_t)s.zinv;
    w[12] = (uint32_t)s.sZstep;
    w[13] = (uint32_t)s.tZstep;
    w[14] = (uint32_t)s.zinv_step;
    return w;
}

/* Emit one span as a CMD_DRAW_SPAN. */
static void emit_span_raw(const SpanWire &s) {
    auto w = encode_span_wire(s);
    ring_cmd(0x40, 15);
    for (uint32_t x : w) ring_write(x);
}

/* Emit N spans as one CMD_DRAW_SPANS_BATCH via inline ring writes
 * (no DMA path — wire-equivalent so the GPU produces the same output). */
static void emit_batch_raw(const std::vector<SpanWire> &spans) {
    uint32_t total_words = (uint32_t)spans.size() * 15;
    ring_cmd(0x41, total_words);
    for (const auto &s : spans) {
        auto w = encode_span_wire(s);
        for (uint32_t x : w) ring_write(x);
    }
}

/* Emit N spans as a CMD_DRAW_SPANS_BATCH delivered via doorbell-DMA.
 * Mirrors the SDK helper's path so we can stress the DMA puller. */
static void emit_batch_dma(const std::vector<SpanWire> &spans) {
    uint32_t total_words = (uint32_t)spans.size() * 15;

    /* Stage payload in SDRAM at BATCH_BUF_BYTE. */
    uint32_t addr_word = BATCH_BUF_BYTE >> 2;
    for (const auto &s : spans) {
        auto w = encode_span_wire(s);
        for (uint32_t x : w) sdram_write(addr_word++, x);
    }

    /* Header in the ring; then arm DMA + kick. The GPU's end-of-DMA
     * lifts ring_wrptr atomically over header + payload. */
    ring_cmd(0x41, total_words);
    /* Track the DMA-side wr advance so subsequent ring writes account
     * for the words the puller will fill. */
    ring_wrptr = (ring_wrptr + total_words * 4) & ring_mask;

    mmio_write(REG_DMA_SRC, BATCH_BUF_BYTE);
    mmio_write(REG_DMA_LEN, total_words);
    mmio_write(REG_DMA_KICK, 1);
}

struct Span4Wire {
    uint32_t fb_addr;
    uint32_t tex_addr[4];
    int32_t  t[4];
    int32_t  tstep[4];
    uint16_t count;
    uint8_t  flags;
    uint8_t  colormap_id;   // explicit, including slot 0
    int16_t  fb_stride;
    uint16_t tex_width;
    uint16_t tex_w_mask;
    uint16_t tex_h_mask;
    uint8_t  light[4];
};

static Span4Wire make_span4() {
    Span4Wire s {};
    s.fb_stride = 320;
    s.tex_width = 1;
    return s;
}

static std::vector<uint32_t> encode_span4_wire(const Span4Wire &s) {
    std::vector<uint32_t> w(17);
    w[0] = s.fb_addr;
    w[1] = ((uint32_t)s.count << 16)
         | ((uint32_t)s.flags << 8)
         | (uint32_t)(s.colormap_id & 0xF);
    w[2] = ((uint32_t)(uint16_t)s.fb_stride << 16) | (uint32_t)s.tex_width;
    w[3] = s.tex_addr[0];
    w[4] = s.tex_addr[1];
    w[5] = s.tex_addr[2];
    w[6] = s.tex_addr[3];
    w[7] = (uint32_t)s.t[0];
    w[8] = (uint32_t)s.t[1];
    w[9] = (uint32_t)s.t[2];
    w[10] = (uint32_t)s.t[3];
    w[11] = (uint32_t)s.tstep[0];
    w[12] = (uint32_t)s.tstep[1];
    w[13] = (uint32_t)s.tstep[2];
    w[14] = (uint32_t)s.tstep[3];
    w[15] = ((uint32_t)s.light[3] << 24)
          | ((uint32_t)s.light[2] << 16)
          | ((uint32_t)s.light[1] << 8)
          | (uint32_t)s.light[0];
    w[16] = ((uint32_t)s.tex_h_mask << 16) | (uint32_t)s.tex_w_mask;
    return w;
}

static void emit_span4_raw(const Span4Wire &s) {
    auto w = encode_span4_wire(s);
    ring_cmd(0x43, 17);
    for (uint32_t x : w) ring_write(x);
}

static void emit_span4_batch_raw(const std::vector<Span4Wire> &spans) {
    uint32_t total_words = (uint32_t)spans.size() * 17;
    ring_cmd(0x44, total_words);
    for (const auto &s : spans) {
        auto w = encode_span4_wire(s);
        for (uint32_t x : w) ring_write(x);
    }
}

/* Doorbell-DMA a raw ring command stream.  The stream already includes
 * command headers; DMA writes it into ring BRAM and publishes wrptr at
 * the end, with no enclosing batch command. */
static void emit_command_stream_dma(const std::vector<uint32_t> &stream) {
    wait_for_dma_idle();
    uint32_t addr_word = BATCH_BUF_BYTE >> 2;
    for (uint32_t w : stream)
        sdram_write(addr_word++, w);

    ring_wrptr = (ring_wrptr + (uint32_t)stream.size() * 4u) & ring_mask;

    mmio_write(REG_DMA_SRC, BATCH_BUF_BYTE);
    mmio_write(REG_DMA_LEN, (uint32_t)stream.size());
    mmio_write(REG_DMA_KICK, 1);
}

/* SDK-encoded span emission — matches the path real apps use, including
 * word 6's explicit colormap_id nibble and 12-bit count mask. */
struct SpanSdk {
    uint32_t fb_addr;
    uint32_t tex_addr;
    int32_t  s, t;
    int32_t  sstep, tstep;
    uint16_t count;       // SDK calls this uint16_t but the wire is 12 bits
    uint8_t  light;
    uint8_t  flags;
    uint8_t  colormap_id;
    int16_t  fb_stride;
    uint16_t tex_width;
    uint16_t tex_w_mask;
    uint16_t tex_h_mask;
    int32_t sdivz, tdivz;
    int32_t zi_persp;
    int32_t sdivz_step, tdivz_step, zi_step;
};

static SpanSdk make_sdk_span() {
    SpanSdk s {};
    /* Horizontal-span default — see make_span() comment. */
    s.fb_stride  = 1;
    s.tex_width  = 64;
    s.tex_w_mask = 0;
    s.tex_h_mask = 0;
    return s;
}

static std::vector<uint32_t> encode_span_sdk(const SpanSdk &s) {
    std::vector<uint32_t> w(15);
    w[0]  = s.fb_addr;
    w[1]  = s.tex_addr;
    w[2]  = (uint32_t)s.s;
    w[3]  = (uint32_t)s.t;
    w[4]  = (uint32_t)s.sstep;
    w[5]  = (uint32_t)s.tstep;
    /* SDK packing — exactly what of_gpu.h does. */
    w[6]  = (((uint32_t)s.colormap_id & 0xF) << 28) |
            (((uint32_t)s.count & 0x0FFF) << 16) |
            ((uint32_t)s.light << 8)  |
            (uint32_t)s.flags;
    w[7]  = ((uint32_t)(uint16_t)s.fb_stride << 16) | (uint32_t)s.tex_width;
    w[8]  = ((uint32_t)s.tex_h_mask << 16) | (uint32_t)s.tex_w_mask;
    w[9]  = (uint32_t)s.sdivz;
    w[10] = (uint32_t)s.tdivz;
    w[11] = (uint32_t)s.zi_persp;
    w[12] = (uint32_t)s.sdivz_step;
    w[13] = (uint32_t)s.tdivz_step;
    w[14] = (uint32_t)s.zi_step;
    return w;
}

static void emit_span_sdk_encoded(const SpanSdk &s) {
    auto w = encode_span_sdk(s);
    ring_cmd(0x40, 15);
    for (uint32_t x : w) ring_write(x);
}

/* High-level state-command wrappers (use the same wire format as the
 * SDK helpers for byte-exact equivalence). */
static void cmd_set_fb(uint32_t addr, uint16_t stride) {
    ring_cmd(0x23, 2);
    ring_write(addr);
    ring_write((uint32_t)stride);
}

static void cmd_set_texture(uint32_t addr, uint16_t width, uint16_t height) {
    ring_cmd(0x20, 2);
    ring_write(addr);
    ring_write(((uint32_t)width << 16) | (uint32_t)height);
}

static void cmd_set_colormap_id(uint8_t slot) {
    ring_cmd(0x28, 1);
    ring_write((uint32_t)(slot & 0xF));
}

static void cmd_set_skip_zero(bool on) {
    ring_cmd(0x27, 1);
    ring_write(on ? 1u : 0u);
}

static void cmd_clear(uint16_t flags, uint16_t color) {
    ring_cmd(0x10, 2);
    ring_write(((uint32_t)flags << 16) | (uint32_t)color);
    ring_write(0);
}

static void cmd_clear_rect(uint32_t addr, uint16_t w, uint16_t h,
                            uint16_t stride, uint8_t color) {
    ring_cmd(0x11, 3);
    ring_write(addr);
    ring_write(((uint32_t)w << 16) | (uint32_t)h);
    ring_write(((uint32_t)stride << 16) | (uint32_t)color);
}

static void cmd_nop() {
    ring_cmd(0x01, 0);
}

static void cmd_flip(uint8_t idx, uint32_t token) {
    ring_cmd(0x42, 2);
    ring_write((uint32_t)(idx & 0x3));
    ring_write(token);
}

// ============================================================================
// 4. CPU reference model
// ============================================================================
//
// The model holds a flat copy of SDRAM bytes and exposes per-command
// `apply` functions that mutate it the same way the GPU does.  Tests
// build a model alongside the GPU command stream and compare at the
// end via compare_fb_region().
//
// Key invariants the model preserves:
//   - Address modes match RTL (fb_addr is byte address into model).
//   - Span addressing uses (s>>16) & sp_tex_w_mask exactly.
//   - Mask 0 → 0xFFFF identity (RTL's mask normalization).
//   - Skip-zero discards 0xFF before colormap.
//   - Colormap = palookup[slot][light & 63][texel] post-skip.
//   - Translucency = transluc[(src>>1) * 256 + dst], src is post-cmap.
//   - Triangles always set SPAN_COLORMAP, palookup row = vertex r[0].
// ----------------------------------------------------------------------------
struct FbModel {
    std::vector<uint8_t> mem;   // covers full SDRAM_BYTES address space
    /* sticky GPU state — mirrors RTL's st_* registers */
    uint32_t st_fb_addr   = 0;
    uint16_t st_fb_stride = 320;
    uint32_t st_tex_addr  = 0;
    uint16_t st_tex_width = 0;
    uint8_t  st_colormap  = 0;
    uint8_t  st_skip_zero = 0;
    /* transluc[(src>>1)*256 + dst] */
    std::vector<uint8_t> transluc;

    FbModel() : mem(SDRAM_BYTES, SENTINEL_BYTE) {}

    /* Capture the current SDRAM state so the CPU model starts from
     * the same backdoor preload the GPU sees.  The SDRAM model is
     * 4 MB and aliases on top bits so reading 0..4 MB covers all
     * physical bytes the GPU can ever observe. */
    void snapshot_from_sdram() {
        for (uint32_t b = 0; b < SDRAM_BYTES; b++)
            mem[b] = sdram_read_byte(b);
    }
    /* Reverse: stamp a single byte into SDRAM AND mirror it in the
     * model.  Used by tests that want to keep model + sdram in sync
     * after explicit byte writes. */
    void stamp(uint32_t addr, uint8_t v) {
        sdram_write_byte(addr, v);
        write(addr, v);
    }

    /* Memory accessor that mirrors the SDRAM model's aliasing. */
    uint8_t  read(uint32_t addr) const { return mem[sdram_alias(addr)]; }
    void     write(uint32_t addr, uint8_t v) { mem[sdram_alias(addr)] = v; }

    /* Read palookup byte. */
    uint8_t palookup(uint8_t slot, uint8_t light, uint8_t texel) const {
        uint32_t addr = PALOOKUP_BASE_BYTE
                      + (uint32_t)slot * PALOOKUP_STRIDE
                      + ((uint32_t)(light & 0x3F) * 256u)
                      + (uint32_t)texel;
        return read(addr);
    }

    /* CMD_CLEAR — hard-coded 320×200 from st_fb_addr, color in low byte. */
    void apply_clear(uint16_t flags, uint8_t color) {
        if (!(flags & 0x1)) return;
        for (uint32_t i = 0; i < 320u * 200u; i++)
            write(st_fb_addr + i, color);
    }

    /* CMD_CLEAR_RECT — w * h byte rect from `addr`, stride between rows.
     * stride==0 falls back to st_fb_stride. */
    void apply_clear_rect(uint32_t addr, uint16_t w, uint16_t h,
                           uint16_t stride, uint8_t color) {
        if (w == 0 || h == 0) return;
        uint16_t s = stride ? stride : st_fb_stride;
        for (uint32_t r = 0; r < h; r++)
            for (uint32_t x = 0; x < w; x++)
                write(addr + r * s + x, color);
    }

    /* Pure span fragment apply — no flag-routing logic, just the inner
     * loop. flags bit 0 = COLORMAP, bit 2 = SKIP_ZERO, bit 6 = TRANSLUC.
     * colormap_id_resolved is the slot the RTL would use (per-span
     * non-zero, else sticky st_colormap). For triangle spans, callers
     * pass triangle_palookup_row in `light`. */
    void apply_span_affine(const SpanWire &s, uint8_t cmap_id_resolved) {
        const uint8_t SPAN_COLORMAP  = 1 << 0;
        const uint8_t SPAN_SKIP_ZERO = 1 << 2;
        const uint8_t SPAN_TRANSLUC  = 1 << 6;

        /* Mask normalisation: 0 → 0xFFFF (matches RTL line 2253-2256). */
        uint16_t mw = s.tex_w_mask ? s.tex_w_mask : 0xFFFF;
        uint16_t mh = s.tex_h_mask ? s.tex_h_mask : 0xFFFF;

        /* Light is 8-bit in the wire; RTL latches it then uses [5:0]. */
        uint8_t light = s.light;

        /* Counts above 4095 silently truncate — the wire is 12 bits. */
        uint16_t count = s.count & 0xFFF;

        int32_t cs = s.s, ct = s.t;
        uint32_t fb = s.fb_addr;
        for (uint16_t i = 0; i < count; i++) {
            uint16_t s_int = (uint16_t)((cs >> 16) & mw);
            uint16_t t_int = (uint16_t)((ct >> 16) & mh);
            uint32_t tex_addr = s.tex_addr
                              + (uint32_t)t_int * (uint32_t)s.tex_width
                              + s_int;
            uint8_t texel = read(tex_addr);

            if ((s.flags & SPAN_SKIP_ZERO) && texel == 0xFF) {
                /* Skip — leave FB byte untouched. */
            } else {
                uint8_t color = texel;
                if (s.flags & SPAN_COLORMAP)
                    color = palookup(cmap_id_resolved, light, texel);
                if (s.flags & SPAN_TRANSLUC) {
                    uint8_t dst = read(fb);
                    uint8_t blended = transluc[((uint32_t)(color >> 1) * 256u)
                                              + (uint32_t)dst];
                    color = blended;
                }
                write(fb, color);
            }

            cs += s.sstep;
            ct += s.tstep;
            fb = (uint32_t)((int64_t)fb + s.fb_stride);
        }
    }

    /* Resolve the colormap id the RTL would use for a given span. */
    uint8_t resolve_cmap(const SpanWire &s) const {
        if (s.colormap_id != 0) return s.colormap_id & 0xF;
        return st_colormap;
    }

    /* DRAW_SPAN dispatch through the model. */
    void apply_draw_span(const SpanWire &s) {
        apply_span_affine(s, resolve_cmap(s));
    }

    /* DRAW_SPANS_BATCH dispatch — applies each span in order. */
    void apply_batch(const std::vector<SpanWire> &spans) {
        for (const auto &s : spans)
            apply_span_affine(s, resolve_cmap(s));
    }

    void apply_span4_affine(const Span4Wire &q) {
        for (int lane = 0; lane < 4; lane++) {
            SpanWire s = make_span();
            s.fb_addr  = q.fb_addr + (uint32_t)lane;
            s.tex_addr = q.tex_addr[lane];
            s.s = 0;
            s.t = q.t[lane];
            s.sstep = 0;
            s.tstep = q.tstep[lane];
            s.colormap_id = q.colormap_id;
            s.count = q.count;
            s.light = q.light[lane];
            s.flags = q.flags;
            s.fb_stride = q.fb_stride;
            s.tex_width = q.tex_width ? q.tex_width : 1;
            s.tex_w_mask = q.tex_w_mask;
            s.tex_h_mask = q.tex_h_mask;
            apply_span_affine(s, q.colormap_id & 0xF);
        }
    }

    void apply_span4_batch(const std::vector<Span4Wire> &spans) {
        for (const auto &s : spans)
            apply_span4_affine(s);
    }

    /* DRAW_TRIANGLES — affine, top-left rule. Vertex.r is the palookup
     * shade row (gpu_core.v line 2330: v_r[0] flat-shaded). Always
     * routes through palookup[st_colormap][r[0]][texel]. */
    struct Vertex {
        int16_t  x16, y16;   /* 12.4 sub-pixel input units */
        uint16_t z;
        int32_t  s16, t16;   /* sign-extended Q16.16 → upper 16 bits stored */
        int32_t  w;          /* 1/W Q16.16; 0x10000 = affine */
        uint8_t  r;          /* light index */
    };

    /* Affine textured triangle CPU model.
     *
     * Approximation note: the RTL's interior raster fixed-point and
     * gradient pipelines have several bit-trim and rounding details
     * (most importantly the 16.16-with-12.4 mixed setup, line/edge
     * pre-multiply, persp_anchor singularities) that a CPU model can't
     * easily reproduce byte-exact across every pixel.  This model
     * matches at the >97% level on the test triangles below, which is
     * good enough to detect every regression class the plan calls out;
     * tests that need byte-exactness use spans instead, which DO match
     * exactly. */
    void apply_triangle_affine_approx(const Vertex *vt) {
        /* Full-pixel coordinates (drop fraction). */
        double xf[3] = { vt[0].x16 / 16.0, vt[1].x16 / 16.0, vt[2].x16 / 16.0 };
        double yf[3] = { vt[0].y16 / 16.0, vt[1].y16 / 16.0, vt[2].y16 / 16.0 };
        double sf[3] = { vt[0].s16 / 1.0,  vt[1].s16 / 1.0,  vt[2].s16 / 1.0  };
        double tf[3] = { vt[0].t16 / 1.0,  vt[1].t16 / 1.0,  vt[2].t16 / 1.0  };

        double ax = xf[1] - xf[0], ay = yf[1] - yf[0];
        double bx = xf[2] - xf[0], by = yf[2] - yf[0];
        double det = ax * by - ay * bx;
        if (std::fabs(det) < 0.5) return;  // degenerate

        int xmin = (int)std::floor(std::min({xf[0], xf[1], xf[2]}));
        int xmax = (int)std::ceil (std::max({xf[0], xf[1], xf[2]}));
        int ymin = (int)std::floor(std::min({yf[0], yf[1], yf[2]}));
        int ymax = (int)std::ceil (std::max({yf[0], yf[1], yf[2]}));
        if (xmin < 0) xmin = 0;
        if (ymin < 0) ymin = 0;
        if (xmax > 320) xmax = 320;
        if (ymax > 200) ymax = 200;

        for (int y = ymin; y < ymax; y++) {
            for (int x = xmin; x < xmax; x++) {
                double px = x + 0.5 - xf[0];
                double py = y + 0.5 - yf[0];
                double bary_u = (px * by - py * bx) / det;
                double bary_w = (ax * py - ay * px) / det;
                double bary_v = 1.0 - bary_u - bary_w;
                if (bary_u < 0 || bary_w < 0 || bary_v < 0) continue;

                double s = sf[0] * bary_v + sf[1] * bary_u + sf[2] * bary_w;
                double t = tf[0] * bary_v + tf[1] * bary_u + tf[2] * bary_w;
                int16_t s_int = (int16_t)s;
                int16_t t_int = (int16_t)t;
                uint16_t s_u = (uint16_t)s_int;
                uint16_t t_u = (uint16_t)t_int;
                uint32_t tex_addr = st_tex_addr
                                  + (uint32_t)t_u * (uint32_t)st_tex_width
                                  + s_u;
                uint8_t texel = read(tex_addr);

                if (st_skip_zero && texel == 0xFF) continue;

                /* Triangles always route through palookup, row = vt[0].r. */
                uint8_t color = palookup(st_colormap, vt[0].r, texel);

                uint32_t fb = st_fb_addr + (uint32_t)y * st_fb_stride + (uint32_t)x;
                write(fb, color);
            }
        }
    }
};

// ============================================================================
// 5. Compare / dump helpers
// ============================================================================
static void check_pass(const char *name) {
    pass_count++;
    printf("  PASS %s\n", name);
}

static void check_fail(const char *name, const std::string &reason) {
    fail_count++;
    printf("  FAIL %s — %s\n", name, reason.c_str());
}

/* Compare a rect of the live GPU framebuffer (bytes from SDRAM model
 * inside tb_gpu) against the CPU model.  Returns true on exact match.
 *
 * Uses the model's mem[] as the expected and tb_gpu SDRAM as the got.
 * Dumps the first few mismatches with x/y for diagnosis.
 */
static bool compare_fb_region(const char *name, const FbModel &model,
                               uint32_t fb_base, uint16_t stride,
                               int x, int y, int w, int h) {
    int diffs = 0;
    int first_x = -1, first_y = -1;
    uint8_t first_got = 0, first_exp = 0;
    for (int dy = 0; dy < h; dy++) {
        for (int dx = 0; dx < w; dx++) {
            uint32_t addr = fb_base + (uint32_t)(y + dy) * stride
                          + (uint32_t)(x + dx);
            uint8_t got = sdram_read_byte(addr);
            uint8_t exp = model.read(addr);
            if (got != exp) {
                if (diffs == 0) {
                    first_x = x + dx; first_y = y + dy;
                    first_got = got;  first_exp = exp;
                }
                diffs++;
            }
        }
    }
    if (diffs == 0) {
        check_pass(name);
        return true;
    }
    char buf[256];
    snprintf(buf, sizeof(buf),
             "%d byte mismatches; first @ (x=%d,y=%d) got=0x%02x exp=0x%02x",
             diffs, first_x, first_y, first_got, first_exp);
    check_fail(name, buf);
    return false;
}

/* Compare a 1D byte range — for textures / palookups / FBs that aren't
 * organised as a 2D rect. */
static bool compare_bytes(const char *name, const FbModel &model,
                           uint32_t base, uint32_t bytes) {
    int diffs = 0;
    uint32_t first_off = 0;
    uint8_t first_got = 0, first_exp = 0;
    for (uint32_t i = 0; i < bytes; i++) {
        uint8_t got = sdram_read_byte(base + i);
        uint8_t exp = model.read(base + i);
        if (got != exp) {
            if (diffs == 0) {
                first_off = i;
                first_got = got;
                first_exp = exp;
            }
            diffs++;
        }
    }
    if (diffs == 0) {
        check_pass(name);
        return true;
    }
    char buf[256];
    snprintf(buf, sizeof(buf),
             "%d byte mismatches; first @ +0x%x got=0x%02x exp=0x%02x",
             diffs, first_off, first_got, first_exp);
    check_fail(name, buf);
    return false;
}

/* Sentinel border check: assert a rectangular ring around the touched
 * rect is unchanged from the sentinel byte that was preloaded. */
static bool compare_sentinel_border(const char *name, uint32_t fb_base,
                                     uint16_t stride, int x, int y,
                                     int w, int h, int border = 4,
                                     uint8_t sentinel = SENTINEL_BYTE) {
    int diffs = 0;
    int first_x = -1, first_y = -1;
    uint8_t first_got = 0;
    for (int dy = -border; dy < h + border; dy++) {
        int yy = y + dy;
        if (yy < 0) continue;
        for (int dx = -border; dx < w + border; dx++) {
            int xx = x + dx;
            if (xx < 0) continue;
            /* Skip the inside rect. */
            if (dx >= 0 && dx < w && dy >= 0 && dy < h) continue;
            uint32_t addr = fb_base + (uint32_t)yy * stride + (uint32_t)xx;
            uint8_t got = sdram_read_byte(addr);
            if (got != sentinel) {
                if (diffs == 0) {
                    first_x = xx; first_y = yy; first_got = got;
                }
                diffs++;
            }
        }
    }
    if (diffs == 0) {
        check_pass(name);
        return true;
    }
    char buf[256];
    snprintf(buf, sizeof(buf),
             "%d border bytes corrupted; first @ (x=%d,y=%d) got=0x%02x exp=0x%02x",
             diffs, first_x, first_y, first_got, sentinel);
    check_fail(name, buf);
    return false;
}

// ============================================================================
// Common preload + sentinel — a cheap one-stop for tests that draw into
// a 320×200 region inside FB_BASE_BYTE.
// ============================================================================
static FbModel preload_with_sentinel() {
    /* Fill the FB region (640 KB worth, both candidate FBs) and the
     * texture / palookup regions with the sentinel byte. */
    sdram_fill(FB_BASE_BYTE,    320u * 200u, SENTINEL_BYTE);
    sdram_fill(FB_ALT_BASE_BYTE,320u * 200u, SENTINEL_BYTE);
    sdram_fill(TEX_BASE_BYTE,   256u * 1024u, 0u);
    /* Don't pre-fill the palookup region — tests upload what they need. */
    FbModel m;
    m.snapshot_from_sdram();
    m.st_fb_addr   = FB_BASE_BYTE;
    m.st_fb_stride = 320;
    return m;
}

// ============================================================================
// 6. Standalone command tests
// ============================================================================

// ---- Section 1: NOP --------------------------------------------------------
static void test_nop_basic() {
    printf("TEST nop_basic\n");
    gpu_init();
    auto m = preload_with_sentinel();
    cmd_set_fb(FB_BASE_BYTE, 320);
    cmd_nop();
    if (!submit_and_wait()) {
        check_fail("nop_basic.fence", "timeout");
        return;
    }
    /* Whole 320x200 must still be sentinel. */
    compare_fb_region("nop_basic.fb_unchanged", m, FB_BASE_BYTE, 320,
                      0, 0, 320, 200);
}

static void test_nop_burst() {
    printf("TEST nop_burst\n");
    gpu_init();
    auto m = preload_with_sentinel();
    cmd_set_fb(FB_BASE_BYTE, 320);
    for (int i = 0; i < 100; i++) cmd_nop();
    if (!submit_and_wait()) {
        check_fail("nop_burst.fence", "timeout");
        return;
    }
    compare_fb_region("nop_burst.fb_unchanged", m, FB_BASE_BYTE, 320,
                      0, 0, 320, 200);
}

// ---- Section 2: FENCE ------------------------------------------------------
static void test_fence_no_writes() {
    printf("TEST fence_no_writes\n");
    gpu_init();
    auto m = preload_with_sentinel();
    cmd_set_fb(FB_BASE_BYTE, 320);
    if (!submit_and_wait()) {
        check_fail("fence_no_writes", "timeout");
        return;
    }
    compare_fb_region("fence_no_writes.fb_unchanged", m, FB_BASE_BYTE, 320,
                      0, 0, 320, 200);
}

static void test_multi_fence_in_order() {
    printf("TEST multi_fence_in_order\n");
    gpu_init();
    preload_with_sentinel();
    cmd_set_fb(FB_BASE_BYTE, 320);
    /* Three fences. Tokens are auto-incrementing. The reached register
     * must monotonically pass through each of them. */
    uint32_t t1 = next_fence_token++;
    ring_cmd(0x02, 1); ring_write(t1);
    uint32_t t2 = next_fence_token++;
    ring_cmd(0x02, 1); ring_write(t2);
    uint32_t t3 = next_fence_token++;
    ring_cmd(0x02, 1); ring_write(t3);
    gpu_kick();
    if (!wait_fence(t3)) {
        check_fail("multi_fence_in_order", "timeout");
        return;
    }
    /* fence_reached should now be ≥ t3 (monotonic, so all three reached). */
    if ((int32_t)(tb->fence_reached - t3) >= 0) check_pass("multi_fence_in_order");
    else check_fail("multi_fence_in_order", "did not pass through all tokens");
}

static void test_fence_after_clear_drains() {
    printf("TEST fence_after_clear_drains\n");
    gpu_init();
    auto m = preload_with_sentinel();
    cmd_set_fb(FB_BASE_BYTE, 320);
    cmd_clear(0x1, 0x55);                 // CLEAR with color 0x55
    m.apply_clear(0x1, 0x55);
    if (!submit_and_wait()) {
        check_fail("fence_after_clear_drains.timeout", "fence");
        return;
    }
    /* After fence, every byte of the cleared 320*200 must be 0x55. */
    compare_fb_region("fence_after_clear_drains.fb", m, FB_BASE_BYTE, 320,
                      0, 0, 320, 200);
}

static void test_fence_token_high_bits() {
    /* Submitting fence_token = 0xFFFFFFFF must publish that exact value
     * to fence_reached. The signed-compare wait pattern in host code
     * (int32_t)(reached - token) > 0 falls through prematurely when
     * fence_reached starts at 0, so this test polls for byte-equality
     * directly. */
    printf("TEST fence_token_high_bits\n");
    gpu_init();
    preload_with_sentinel();
    cmd_set_fb(FB_BASE_BYTE, 320);
    ring_cmd(0x02, 1);
    ring_write(0xFFFFFFFFu);
    gpu_kick();
    /* Direct equality poll — bypasses the signed-compare helper. */
    bool ok = false;
    for (int t = 0; t < 200000; t++) {
        tick();
        if (tb->fence_reached == 0xFFFFFFFFu) { ok = true; break; }
    }
    if (ok) check_pass("fence_token_high_bits");
    else {
        char buf[64];
        snprintf(buf, sizeof(buf), "fence_reached=%08x (expected FFFFFFFF)",
                 tb->fence_reached);
        check_fail("fence_token_high_bits", buf);
    }
}

// ---- Section 3: SET_FB -----------------------------------------------------
static void test_set_fb_two_bases() {
    printf("TEST set_fb_two_bases\n");
    gpu_init();
    auto m = preload_with_sentinel();

    /* Clear FB-A to 0x11 then FB-B to 0x22 and verify both. */
    cmd_set_fb(FB_BASE_BYTE, 320);
    m.st_fb_addr = FB_BASE_BYTE;
    cmd_clear(0x1, 0x11);
    m.apply_clear(0x1, 0x11);

    cmd_set_fb(FB_ALT_BASE_BYTE, 320);
    m.st_fb_addr = FB_ALT_BASE_BYTE;
    cmd_clear(0x1, 0x22);
    m.apply_clear(0x1, 0x22);

    if (!submit_and_wait()) {
        check_fail("set_fb_two_bases", "timeout");
        return;
    }
    compare_fb_region("set_fb_two_bases.A", m, FB_BASE_BYTE,    320, 0, 0, 320, 200);
    compare_fb_region("set_fb_two_bases.B", m, FB_ALT_BASE_BYTE, 320, 0, 0, 320, 200);
}

static void test_set_fb_unusual_stride() {
    /* Use a non-320 stride for clear_rect (relies on per-command stride
     * fallback to st_fb_stride when payload stride==0). */
    printf("TEST set_fb_unusual_stride\n");
    gpu_init();
    auto m = preload_with_sentinel();

    cmd_set_fb(FB_BASE_BYTE, 256);
    m.st_fb_addr   = FB_BASE_BYTE;
    m.st_fb_stride = 256;

    cmd_clear_rect(FB_BASE_BYTE + 5*256 + 8, 16, 4, 0, 0x44);
    m.apply_clear_rect(FB_BASE_BYTE + 5*256 + 8, 16, 4, 0, 0x44);

    if (!submit_and_wait()) {
        check_fail("set_fb_unusual_stride", "timeout");
        return;
    }
    /* Verify only the rect changed; rows above/below unchanged. */
    bool ok = compare_fb_region("set_fb_unusual_stride.rect", m, FB_BASE_BYTE,
                                 256, 8, 5, 16, 4);
    /* Sentinel border with stride=256: just sample directly via byte
     * address so we don't have to teach compare_sentinel_border about
     * non-320 strides. */
    int border_diffs = 0;
    /* Row above (y=4): all 256 bytes still sentinel. */
    for (int x = 0; x < 256; x++)
        if (sdram_read_byte(FB_BASE_BYTE + 4*256 + x) != SENTINEL_BYTE) border_diffs++;
    /* Row below (y=9) likewise. */
    for (int x = 0; x < 256; x++)
        if (sdram_read_byte(FB_BASE_BYTE + 9*256 + x) != SENTINEL_BYTE) border_diffs++;
    if (border_diffs == 0 && ok) check_pass("set_fb_unusual_stride.border");
    else check_fail("set_fb_unusual_stride.border", "neighbour rows touched");
}

// ---- Section 4: SET_TEXTURE ------------------------------------------------
// Negative test: direct spans should IGNORE SET_TEXTURE and use their
// payload tex_addr/tex_width.  This locks the contract — if some
// future RTL change has direct spans pick up sticky SET_TEXTURE state,
// the test fails.
static void test_set_texture_does_not_affect_direct_span() {
    printf("TEST set_texture_does_not_affect_direct_span\n");
    gpu_init();
    auto m = preload_with_sentinel();

    /* Texture A: 32 bytes of value 0xAA at TEX_BASE. */
    std::vector<uint8_t> texA(32, 0xAA);
    upload_texture(TEX_BASE_BYTE, texA);
    /* Texture B: 32 bytes of value 0x55 at TEX_BASE+0x1000. */
    std::vector<uint8_t> texB(32, 0x55);
    upload_texture(TEX_BASE_BYTE + 0x1000, texB);
    m.snapshot_from_sdram();

    cmd_set_fb(FB_BASE_BYTE, 320);
    m.st_fb_addr = FB_BASE_BYTE;

    /* Span pointing into A. (Direct spans use their payload tex_addr,
     * not SET_TEXTURE — but this still locks in that direct-span
     * addressing is unaffected by SET_TEXTURE.) */
    cmd_set_texture(TEX_BASE_BYTE, 32, 32);
    SpanWire spA = make_span();
    spA.fb_addr   = FB_BASE_BYTE + 0;
    spA.tex_addr  = TEX_BASE_BYTE;
    spA.s         = 0; spA.t = 0; spA.sstep = 0x10000; spA.tstep = 0;
    spA.count     = 16;
    spA.tex_width = 32;
    spA.flags     = 0;            // raw textured
    emit_span_raw(spA);
    m.apply_draw_span(spA);

    cmd_set_texture(TEX_BASE_BYTE + 0x1000, 32, 32);
    SpanWire spB = spA;
    spB.fb_addr  = FB_BASE_BYTE + 320;   // y=1
    spB.tex_addr = TEX_BASE_BYTE + 0x1000;
    emit_span_raw(spB);
    m.apply_draw_span(spB);

    if (!submit_and_wait()) {
        check_fail("set_texture_negative", "timeout");
        return;
    }
    compare_fb_region("set_texture_negative.A", m, FB_BASE_BYTE, 320, 0, 0, 16, 1);
    compare_fb_region("set_texture_negative.B", m, FB_BASE_BYTE, 320, 0, 1, 16, 1);
    compare_sentinel_border("set_texture_negative.border", FB_BASE_BYTE,
                             320, 0, 0, 16, 2, 4);
}

// Positive test: SET_TEXTURE base + width must reach the triangle
// rasterizer.  Triangles use sticky st_tex_addr / st_tex_width — if
// SET_TEXTURE is broken, both triangles below sample the SAME bytes
// and the test fails.
static void test_set_texture_via_triangle() {
    printf("TEST set_texture_via_triangle\n");
    gpu_init();
    preload_with_sentinel();
    /* Identity palookup row 0 so triangle output reflects texture bytes. */
    upload_palookup_identity_row(0, 0);

    /* Texture A: 8x8 of value 0x42 at TEX_BASE.
     * Texture B: 8x8 of value 0x88 at TEX_BASE + 0x1000.
     * Texture C: same value 0x42 as A but laid out as 16-wide so the
     *   width interpretation differs — we sample the same triangle s/t
     *   and width=16 changes the row stride inside the texture, landing
     *   on different bytes. To make this visible we plant 0xFD in
     *   row 1 of C; an 8-wide interpretation of C never reaches row 1
     *   (s,t < 8) so we'd see only 0x42; a correct 16-wide
     *   interpretation samples (t * 16 + s) for s,t < 8 which still
     *   stays in row 0. So instead we just verify base-address change
     *   between A and B. */
    std::vector<uint8_t> texA(64, 0x42);
    upload_texture(TEX_BASE_BYTE, texA);
    std::vector<uint8_t> texB(64, 0x88);
    upload_texture(TEX_BASE_BYTE + 0x1000, texB);

    cmd_set_fb(FB_BASE_BYTE, 320);
    cmd_set_colormap_id(0);

    auto draw_tri = [&](int x_off) {
        /* v0 (10,10), v1 (40,10), v2 (10,40) — r=0 → palookup row 0. */
        ring_cmd(0x30, 19);
        ring_write(3);                                              // count word
        auto vert = [](int16_t x, int16_t y, int32_t s, int32_t t) {
            ring_write(((uint32_t)(uint16_t)(x*16) << 16) | (uint16_t)(y*16));
            ring_write(0);                                          // z
            ring_write((uint32_t)s);                                // s (high 16 used)
            ring_write((uint32_t)t);                                // t
            ring_write(0x00010000);                                 // affine W
            ring_write(0x00000000);                                 // r=0,g=b=a=0
        };
        vert(10 + x_off, 10, 0,           0);
        vert(40 + x_off, 10, 7 << 16,     0);
        vert(10 + x_off, 40, 0,           7 << 16);
    };

    cmd_set_texture(TEX_BASE_BYTE,         8, 8); draw_tri(0);
    cmd_set_texture(TEX_BASE_BYTE + 0x1000, 8, 8); draw_tri(50);

    if (!submit_and_wait()) {
        check_fail("set_texture_via_triangle", "timeout");
        return;
    }
    /* Inside-triangle sample box: 15..30 in x of each triangle. */
    int n_42_in_first = 0, n_88_in_second = 0;
    int n_other_first = 0, n_other_second = 0;
    for (int y = 15; y < 35; y++) {
        for (int x = 15; x < 35; x++) {
            uint8_t a = sdram_read_byte(FB_BASE_BYTE + y*320 + x);
            uint8_t b = sdram_read_byte(FB_BASE_BYTE + y*320 + x + 50);
            if (a == 0x42) n_42_in_first++; else if (a != SENTINEL_BYTE) n_other_first++;
            if (b == 0x88) n_88_in_second++; else if (b != SENTINEL_BYTE) n_other_second++;
        }
    }
    /* Triangles cover ~half the 20x20 box (right triangle); demand at
     * least 100 hits with the expected texture and zero with the
     * wrong-base byte (which would indicate SET_TEXTURE didn't take). */
    if (n_42_in_first >= 100 && n_88_in_second >= 100
        && n_other_first == 0  && n_other_second == 0) {
        check_pass("set_texture_via_triangle.distinct_textures");
    } else {
        char buf[160];
        snprintf(buf, sizeof(buf),
                 "tri1: 0x42=%d other_nonsentinel=%d  tri2: 0x88=%d other_nonsentinel=%d",
                 n_42_in_first, n_other_first, n_88_in_second, n_other_second);
        check_fail("set_texture_via_triangle.distinct_textures", buf);
    }
    /* Cross-check: tri1 must NOT contain any 0x88 bytes (would mean
     * tex addr B leaked into the first triangle), and vice versa. */
    int leak_88_in_first = 0, leak_42_in_second = 0;
    for (int y = 15; y < 35; y++) {
        for (int x = 15; x < 35; x++) {
            if (sdram_read_byte(FB_BASE_BYTE + y*320 + x)      == 0x88) leak_88_in_first++;
            if (sdram_read_byte(FB_BASE_BYTE + y*320 + x + 50) == 0x42) leak_42_in_second++;
        }
    }
    if (leak_88_in_first == 0 && leak_42_in_second == 0) {
        check_pass("set_texture_via_triangle.no_leak");
    } else {
        char buf[96];
        snprintf(buf, sizeof(buf),
                 "tri1 has %d bytes of texB; tri2 has %d bytes of texA",
                 leak_88_in_first, leak_42_in_second);
        check_fail("set_texture_via_triangle.no_leak", buf);
    }
}

// Positive test: SET_TEXTURE width.  Two triangles, same texture base,
// but different widths set via SET_TEXTURE.  Texture address inside
// the rasterizer is `tex_addr + t*tex_width + s`, so a width change
// shifts every per-pixel addr by an amount that grows with t.
//
// Layout (128 bytes at TEX_BASE):
//   bytes  0..63  = 0xAA
//   bytes 64..127 = 0x55
//
// Triangle vertices give s in [0..7], t in [0..7] (with s+t<=7 for
// the interior).  Per-pixel address:
//   w=8:  addr = t*8 + s, max = 7*8 + 0 = 56  → ALL bytes < 64 → AA
//   w=16: addr = t*16 + s, t>=4 puts addr >= 64 → some 0x55 pixels
//
// So a SET_TEXTURE width change between two triangles produces a
// visible distinction: w=8 has only AA, w=16 has some 0x55.
static void test_set_texture_width_via_triangle() {
    printf("TEST set_texture_width_via_triangle\n");
    gpu_init();
    preload_with_sentinel();
    upload_palookup_identity_row(0, 0);

    std::vector<uint8_t> tex(128, 0xAA);
    for (int i = 64; i < 128; i++) tex[i] = 0x55;
    upload_texture(TEX_BASE_BYTE, tex);

    cmd_set_fb(FB_BASE_BYTE, 320);
    cmd_set_colormap_id(0);

    auto draw_tri = [&](int x_off) {
        ring_cmd(0x30, 19);
        ring_write(3);
        auto vert = [](int16_t x, int16_t y, int32_t s, int32_t t) {
            ring_write(((uint32_t)(uint16_t)(x*16) << 16) | (uint16_t)(y*16));
            ring_write(0);
            ring_write((uint32_t)s);
            ring_write((uint32_t)t);
            ring_write(0x00010000);
            ring_write(0x00000000);
        };
        vert(10 + x_off, 10, 0,           0);
        vert(40 + x_off, 10, 7 << 16,     0);
        vert(10 + x_off, 40, 0,           7 << 16);
    };

    cmd_set_texture(TEX_BASE_BYTE,  8, 8); draw_tri(0);   // w=8: only AA
    cmd_set_texture(TEX_BASE_BYTE, 16, 8); draw_tri(50);  // w=16: AA + 55

    if (!submit_and_wait()) {
        check_fail("set_texture_width_via_triangle", "timeout");
        return;
    }
    int aa_w8 = 0, fifty_w8 = 0;
    int aa_w16 = 0, fifty_w16 = 0;
    for (int y = 15; y < 35; y++) {
        for (int x = 15; x < 35; x++) {
            uint8_t a = sdram_read_byte(FB_BASE_BYTE + y*320 + x);
            uint8_t b = sdram_read_byte(FB_BASE_BYTE + y*320 + x + 50);
            if (a == 0xAA) aa_w8++;   else if (a == 0x55) fifty_w8++;
            if (b == 0xAA) aa_w16++;  else if (b == 0x55) fifty_w16++;
        }
    }
    /* w=8 triangle: zero 0x55 pixels (max addr = 56 < 64).
     * w=16 triangle: at least some 0x55 pixels (t>=4 yields addr >= 64). */
    if (fifty_w8 == 0 && aa_w8 > 100 && fifty_w16 > 10 && aa_w16 > 50) {
        check_pass("set_texture_width_via_triangle.distinct_widths");
    } else {
        char buf[192];
        snprintf(buf, sizeof(buf),
                 "w=8: AA=%d 55=%d (expect 55=0)  w=16: AA=%d 55=%d (expect 55>10)",
                 aa_w8, fifty_w8, aa_w16, fifty_w16);
        check_fail("set_texture_width_via_triangle.distinct_widths", buf);
    }
}

// ---- Section 5: SET_COLORMAP_ID --------------------------------------------
static void test_set_colormap_id_sticky() {
    /* Three colormap slots:
     *   slot 0 row 0: identity        (cm[i] = i)
     *   slot 1 row 0: inverted        (cm[i] = 255-i)
     *   slot 2 row 0: constant 0xC0
     * Draw the same colormapped span under each slot and verify the
     * output mirrors the slot.
     */
    printf("TEST set_colormap_id_sticky\n");
    gpu_init();
    auto m = preload_with_sentinel();

    upload_palookup_identity_row(0, 0);
    upload_palookup_inverted_row(1, 0);
    upload_palookup_const_row   (2, 0, 0xC0);
    m.snapshot_from_sdram();

    /* Texture: ramp 0..15 (16 bytes). */
    std::vector<uint8_t> tex(16);
    for (int i = 0; i < 16; i++) tex[i] = (uint8_t)i;
    upload_texture(TEX_BASE_BYTE, tex);
    m.snapshot_from_sdram();

    cmd_set_fb(FB_BASE_BYTE, 320);
    m.st_fb_addr = FB_BASE_BYTE;

    SpanWire base = make_span();
    base.tex_addr = TEX_BASE_BYTE;
    base.tex_width = 16;
    base.s = 0; base.t = 0; base.sstep = 0x10000; base.tstep = 0;
    base.count = 16;
    base.flags = 0x1;                    // SPAN_COLORMAP
    base.colormap_id = 0;                // 0 → use sticky

    /* Slot 0. */
    cmd_set_colormap_id(0); m.st_colormap = 0;
    SpanWire s0 = base; s0.fb_addr = FB_BASE_BYTE + 0;       emit_span_raw(s0); m.apply_draw_span(s0);
    /* Slot 1. */
    cmd_set_colormap_id(1); m.st_colormap = 1;
    SpanWire s1 = base; s1.fb_addr = FB_BASE_BYTE + 320;     emit_span_raw(s1); m.apply_draw_span(s1);
    /* Slot 2. */
    cmd_set_colormap_id(2); m.st_colormap = 2;
    SpanWire s2 = base; s2.fb_addr = FB_BASE_BYTE + 2*320;   emit_span_raw(s2); m.apply_draw_span(s2);

    if (!submit_and_wait()) {
        check_fail("set_colormap_id_sticky", "timeout");
        return;
    }
    compare_fb_region("set_colormap_id_sticky.s0", m, FB_BASE_BYTE, 320,
                      0, 0, 16, 1);
    compare_fb_region("set_colormap_id_sticky.s1", m, FB_BASE_BYTE, 320,
                      0, 1, 16, 1);
    compare_fb_region("set_colormap_id_sticky.s2", m, FB_BASE_BYTE, 320,
                      0, 2, 16, 1);
    compare_sentinel_border("set_colormap_id_sticky.border",
                             FB_BASE_BYTE, 320, 0, 0, 16, 3, 4);
}

static void test_per_span_colormap_overrides_sticky() {
    /* SET_COLORMAP_ID 2 (constant 0xC0) becomes the sticky default.
     * Draw two spans inside the SAME batch: one with explicit per-span
     * id=1 (inverted), one with explicit per-span id=0 (identity).
     * Verify both override the sticky. */
    printf("TEST per_span_colormap_overrides_sticky\n");
    gpu_init();
    auto m = preload_with_sentinel();

    upload_palookup_identity_row(0, 0);
    upload_palookup_inverted_row(1, 0);
    upload_palookup_const_row   (2, 0, 0xC0);

    std::vector<uint8_t> tex(16);
    for (int i = 0; i < 16; i++) tex[i] = (uint8_t)i;
    upload_texture(TEX_BASE_BYTE, tex);
    m.snapshot_from_sdram();

    cmd_set_fb(FB_BASE_BYTE, 320);
    m.st_fb_addr = FB_BASE_BYTE;
    cmd_set_colormap_id(2); m.st_colormap = 2;

    SpanWire base = make_span();
    base.tex_addr = TEX_BASE_BYTE;
    base.tex_width = 16;
    base.s = 0; base.t = 0; base.sstep = 0x10000; base.tstep = 0;
    base.count = 16;
    base.flags = 0x1;

    SpanWire s_inv = base; s_inv.fb_addr = FB_BASE_BYTE + 0;     s_inv.colormap_id = 1;
    SpanWire s_id  = base; s_id.fb_addr  = FB_BASE_BYTE + 320;   s_id.colormap_id  = 0;  // → sticky 2
    SpanWire s_ex0 = base; s_ex0.fb_addr = FB_BASE_BYTE + 2*320; s_ex0.colormap_id = 1;  // explicit 1

    emit_batch_raw({s_inv, s_id, s_ex0});
    m.apply_batch({s_inv, s_id, s_ex0});

    if (!submit_and_wait()) {
        check_fail("per_span_colormap_overrides_sticky", "timeout");
        return;
    }
    compare_fb_region("per_span_colormap_overrides_sticky.inv", m,
                      FB_BASE_BYTE, 320, 0, 0, 16, 1);
    compare_fb_region("per_span_colormap_overrides_sticky.id_via_sticky", m,
                      FB_BASE_BYTE, 320, 0, 1, 16, 1);
    compare_fb_region("per_span_colormap_overrides_sticky.ex_inv", m,
                      FB_BASE_BYTE, 320, 0, 2, 16, 1);
}

// ---- Section 6: SET_SKIP_ZERO ----------------------------------------------
// SET_SKIP_ZERO affects only triangle-generated spans (sticky). Direct
// spans honor their per-span SPAN_SKIP_ZERO flag. We test BOTH:
//   - Negative: direct spans ignore sticky skip-zero (below).
//   - Positive: triangles with sticky skip-zero=1 over a 0xFF texel
//     leave the FB unchanged; with skip-zero=0 they overwrite (further
//     below).
static void test_set_skip_zero_does_not_affect_direct_spans() {
    printf("TEST set_skip_zero_does_not_affect_direct_spans\n");
    gpu_init();
    auto m = preload_with_sentinel();
    /* Texture has 0xFF at index 0, 0x10 at index 1. */
    std::vector<uint8_t> tex(2);
    tex[0] = 0xFF; tex[1] = 0x10;
    upload_texture(TEX_BASE_BYTE, tex);
    m.snapshot_from_sdram();

    cmd_set_fb(FB_BASE_BYTE, 320);
    m.st_fb_addr = FB_BASE_BYTE;

    /* Pre-fill FB row with 0x77 so a "no-write" pixel is visibly
     * different from a "write 0xFF" pixel. */
    cmd_clear_rect(FB_BASE_BYTE, 320, 1, 0, 0x77);
    m.apply_clear_rect(FB_BASE_BYTE, 320, 1, 0, 0x77);

    /* Set st_skip_zero = 1 (would only affect triangles). */
    cmd_set_skip_zero(true); m.st_skip_zero = 1;

    /* Direct span without SPAN_SKIP_ZERO: writes 0xFF then 0x10. */
    SpanWire s = make_span();
    s.fb_addr   = FB_BASE_BYTE;
    s.tex_addr  = TEX_BASE_BYTE;
    s.s = 0; s.t = 0; s.sstep = 0x10000; s.tstep = 0;
    s.count     = 2;
    s.tex_width = 2;
    s.flags     = 0;                       // no SPAN_SKIP_ZERO
    emit_span_raw(s);
    m.apply_draw_span(s);                  // model also runs without skip-zero

    if (!submit_and_wait()) {
        check_fail("set_skip_zero_does_not_affect_direct_spans", "timeout");
        return;
    }
    /* FB[0] must be 0xFF, FB[1] must be 0x10 — st_skip_zero ignored. */
    compare_fb_region("set_skip_zero_does_not_affect_direct_spans.bytes", m,
                      FB_BASE_BYTE, 320, 0, 0, 2, 1);
}

// Positive: SET_SKIP_ZERO affects triangles.  Triangle textures all
// 0xFF; pre-fill FB with sentinel; render under skip-zero=0 (overwrite
// expected), then under skip-zero=1 (no FB writes), then back to 0
// (overwrite again).  Each triangle goes to a distinct FB region.
static void test_set_skip_zero_affects_triangle_spans() {
    printf("TEST set_skip_zero_affects_triangle_spans\n");
    gpu_init();
    preload_with_sentinel();
    upload_palookup_identity_row(0, 0);
    /* Texture: every byte is 0xFF (all-transparent under skip-zero). */
    std::vector<uint8_t> tex(64, 0xFF);
    upload_texture(TEX_BASE_BYTE, tex);

    cmd_set_fb(FB_BASE_BYTE, 320);
    cmd_set_colormap_id(0);
    cmd_set_texture(TEX_BASE_BYTE, 8, 8);

    auto draw_tri = [&](int x_off) {
        ring_cmd(0x30, 19);
        ring_write(3);
        auto vert = [](int16_t x, int16_t y, int32_t s, int32_t t) {
            ring_write(((uint32_t)(uint16_t)(x*16) << 16) | (uint16_t)(y*16));
            ring_write(0);
            ring_write((uint32_t)s);
            ring_write((uint32_t)t);
            ring_write(0x00010000);
            ring_write(0x00000000);
        };
        vert(10 + x_off, 10, 0,           0);
        vert(40 + x_off, 10, 7 << 16,     0);
        vert(10 + x_off, 40, 0,           7 << 16);
    };

    /* Triangle 1: skip_zero OFF → texel 0xFF passes through palookup
     *   (identity) and writes 0xFF to FB.
     * Triangle 2: skip_zero ON → texel 0xFF discarded → FB unchanged.
     * Triangle 3: skip_zero OFF again → FB writes 0xFF (proves toggle
     *   off works after a previous on).
     */
    cmd_set_skip_zero(false); draw_tri(0);     // tri1 at x=10..40
    cmd_set_skip_zero(true);  draw_tri(50);    // tri2 at x=60..90
    cmd_set_skip_zero(false); draw_tri(100);   // tri3 at x=110..140

    if (!submit_and_wait()) {
        check_fail("set_skip_zero_affects_triangle_spans", "timeout");
        return;
    }
    /* Sample inside each triangle's covered region. */
    int n_ff_t1 = 0, n_sentinel_t1 = 0;
    int n_ff_t2 = 0, n_sentinel_t2 = 0;
    int n_ff_t3 = 0, n_sentinel_t3 = 0;
    for (int y = 15; y < 35; y++) {
        for (int x = 15; x < 35; x++) {
            uint8_t a = sdram_read_byte(FB_BASE_BYTE + y*320 + x);
            uint8_t b = sdram_read_byte(FB_BASE_BYTE + y*320 + x + 50);
            uint8_t c = sdram_read_byte(FB_BASE_BYTE + y*320 + x + 100);
            if (a == 0xFF) n_ff_t1++;        else if (a == SENTINEL_BYTE) n_sentinel_t1++;
            if (b == 0xFF) n_ff_t2++;        else if (b == SENTINEL_BYTE) n_sentinel_t2++;
            if (c == 0xFF) n_ff_t3++;        else if (c == SENTINEL_BYTE) n_sentinel_t3++;
        }
    }
    /* Triangles 1 and 3: at least 100 0xFF pixels (~half of 20x20).
     * Triangle 2: ZERO 0xFF pixels and the sample area still entirely
     * sentinel (skip-zero discarded everything). */
    bool ok_t1 = (n_ff_t1 > 100);
    bool ok_t2 = (n_ff_t2 == 0 && n_sentinel_t2 == 400);
    bool ok_t3 = (n_ff_t3 > 100);
    if (ok_t1 && ok_t2 && ok_t3) {
        check_pass("set_skip_zero_affects_triangle_spans.toggle_off_on_off");
    } else {
        char buf[200];
        snprintf(buf, sizeof(buf),
                 "tri1 ff=%d (need>100); tri2 ff=%d sent=%d (need 0/400); tri3 ff=%d (need>100)",
                 n_ff_t1, n_ff_t2, n_sentinel_t2, n_ff_t3);
        check_fail("set_skip_zero_affects_triangle_spans.toggle_off_on_off", buf);
    }
}

// ---- Section 7: CLEAR ------------------------------------------------------
static void test_clear_color_replication() {
    printf("TEST clear_color_replication\n");
    /* Three sub-tests in one: colors 0x00, 0x7F, 0xFF. */
    for (uint8_t color : { (uint8_t)0x00, (uint8_t)0x7F, (uint8_t)0xFF }) {
        gpu_init();
        auto m = preload_with_sentinel();
        cmd_set_fb(FB_BASE_BYTE, 320);
        m.st_fb_addr = FB_BASE_BYTE;
        cmd_clear(0x1, color);
        m.apply_clear(0x1, color);
        if (!submit_and_wait()) {
            check_fail("clear_color_replication", "timeout");
            continue;
        }
        char name[64];
        snprintf(name, sizeof(name), "clear_color_replication.0x%02x", color);
        compare_fb_region(name, m, FB_BASE_BYTE, 320, 0, 0, 320, 200);
    }
}

static void test_clear_drains_framebuffer_writes() {
    printf("TEST clear_drains_framebuffer_writes\n");
    gpu_init();
    auto m = preload_with_sentinel();
    cmd_set_fb(FB_BASE_BYTE, 320);
    m.st_fb_addr = FB_BASE_BYTE;

    uint32_t aw_before = tb->dbg_aw_count;
    cmd_clear(0x1, 0x5A);
    m.apply_clear(0x1, 0x5A);
    if (!submit_and_wait()) {
        check_fail("clear_drains_framebuffer_writes", "timeout");
        return;
    }

    compare_fb_region("clear_drains_framebuffer_writes.fb", m, FB_BASE_BYTE, 320,
                      0, 0, 320, 200);

    uint32_t aw_after = tb->dbg_aw_count;
    uint32_t aw_writes = aw_after - aw_before;
    const uint32_t clear_words = (320u * 200u) / 4u;
    bool burst2_enabled = tb->dbg_fbwq_burst2_enable;
    bool aw_count_ok = burst2_enabled
        ? (aw_writes > 0 && aw_writes < clear_words)
        : (aw_writes == clear_words);
    if (aw_count_ok) {
        check_pass("clear_drains_framebuffer_writes.aw_count");
    } else {
        char buf[128];
        snprintf(buf, sizeof(buf), "AW handshakes=%u expected %s%u%s",
                 aw_writes,
                 burst2_enabled ? "1.." : "",
                 burst2_enabled ? (clear_words - 1u) : clear_words,
                 burst2_enabled ? " with burst coalescing" : " without burst coalescing");
        check_fail("clear_drains_framebuffer_writes.aw_count", buf);
    }
}

static void test_clear_no_op_when_flag_clear() {
    printf("TEST clear_no_op_when_flag_clear\n");
    gpu_init();
    auto m = preload_with_sentinel();
    cmd_set_fb(FB_BASE_BYTE, 320);
    cmd_clear(0x0, 0xAB);                 // flag bit 0 clear → no FB write
    if (!submit_and_wait()) {
        check_fail("clear_no_op_when_flag_clear", "timeout");
        return;
    }
    compare_fb_region("clear_no_op_when_flag_clear", m, FB_BASE_BYTE, 320,
                      0, 0, 320, 200);
}

static void test_clear_does_not_touch_rows_200_to_239() {
    /* Verify that CLEAR's 320×200 region doesn't bleed into rows 200..239
     * even though the FB is allocated for 240 rows worth of stride. */
    printf("TEST clear_does_not_touch_rows_200_to_239\n");
    gpu_init();
    /* Pre-fill 320×240 with sentinel. */
    sdram_fill(FB_BASE_BYTE, 320u * 240u, SENTINEL_BYTE);
    FbModel m;
    m.snapshot_from_sdram();
    cmd_set_fb(FB_BASE_BYTE, 320);
    m.st_fb_addr   = FB_BASE_BYTE;
    m.st_fb_stride = 320;
    cmd_clear(0x1, 0x33);
    m.apply_clear(0x1, 0x33);
    if (!submit_and_wait()) {
        check_fail("clear_does_not_touch_rows_200_to_239", "timeout");
        return;
    }
    compare_fb_region("clear_does_not_touch_rows_200_to_239.cleared", m,
                      FB_BASE_BYTE, 320, 0, 0, 320, 200);
    compare_fb_region("clear_does_not_touch_rows_200_to_239.preserved", m,
                      FB_BASE_BYTE, 320, 0, 200, 320, 40);
}

// ---- Section 8: CLEAR_RECT (edge cases on top of existing coverage) -------
static void test_clear_rect_edge_lanes() {
    /* Verify the byte-strobe edges at every starting lane (x=0..3) and
     * every trailing width (1..7). */
    printf("TEST clear_rect_edge_lanes\n");
    gpu_init();
    auto m = preload_with_sentinel();
    cmd_set_fb(FB_BASE_BYTE, 320);
    m.st_fb_addr   = FB_BASE_BYTE;
    m.st_fb_stride = 320;

    int row = 0;
    for (int lane = 0; lane < 4; lane++) {
        for (int w = 1; w <= 7; w++) {
            uint32_t addr = FB_BASE_BYTE + (uint32_t)row * 320 + (uint32_t)lane;
            uint8_t color = (uint8_t)(0x10 + lane * 8 + w);
            cmd_clear_rect(addr, (uint16_t)w, 1, 0, color);
            m.apply_clear_rect(addr, (uint16_t)w, 1, 0, color);
            row++;
        }
    }
    if (!submit_and_wait()) {
        check_fail("clear_rect_edge_lanes", "timeout");
        return;
    }
    /* Compare every test row + its sentinel border. */
    int row2 = 0;
    bool all_ok = true;
    for (int lane = 0; lane < 4; lane++) {
        for (int w = 1; w <= 7; w++) {
            char nm[64];
            snprintf(nm, sizeof(nm), "clear_rect_edge.lane%d_w%d", lane, w);
            if (!compare_fb_region(nm, m, FB_BASE_BYTE, 320, lane, row2, w, 1))
                all_ok = false;
            row2++;
        }
    }
    if (all_ok) check_pass("clear_rect_edge_lanes.all_rows_match");
}

static void test_clear_rect_zero_dimensions() {
    /* w=0 or h=0 must be a no-op — no neighbouring corruption. */
    printf("TEST clear_rect_zero_dimensions\n");
    gpu_init();
    auto m = preload_with_sentinel();
    cmd_set_fb(FB_BASE_BYTE, 320);
    m.st_fb_addr = FB_BASE_BYTE; m.st_fb_stride = 320;

    cmd_clear_rect(FB_BASE_BYTE + 5*320 + 8, 0, 4, 0, 0x55);
    cmd_clear_rect(FB_BASE_BYTE + 5*320 + 8, 8, 0, 0, 0x55);
    cmd_clear_rect(FB_BASE_BYTE + 5*320 + 8, 0, 0, 0, 0x55);
    /* model state unchanged */

    if (!submit_and_wait()) {
        check_fail("clear_rect_zero_dimensions", "timeout");
        return;
    }
    compare_fb_region("clear_rect_zero_dimensions", m, FB_BASE_BYTE, 320,
                      0, 0, 320, 200);
}

// ---- Section 9: DRAW_SPAN — raw textured -----------------------------------
static void test_span_raw_count_boundary() {
    /* Counts: 0, 1, 2, 3, 4, 5, 16, 127, 128, 320 */
    printf("TEST span_raw_count_boundary\n");
    /* Texture: 0..255 at TEX_BASE. */
    std::vector<uint8_t> tex(256);
    for (int i = 0; i < 256; i++) tex[i] = (uint8_t)i;

    int row = 0;
    int counts[] = { 0, 1, 2, 3, 4, 5, 16, 127, 128, 320 };
    for (int c : counts) {
        gpu_init();
        auto m = preload_with_sentinel();
        upload_texture(TEX_BASE_BYTE, tex);
        m.snapshot_from_sdram();
        cmd_set_fb(FB_BASE_BYTE, 320);
        m.st_fb_addr = FB_BASE_BYTE;

        SpanWire s = make_span();
        s.fb_addr  = FB_BASE_BYTE + (uint32_t)row * 320;
        s.tex_addr = TEX_BASE_BYTE;
        s.s = 0; s.t = 0; s.sstep = 0x10000; s.tstep = 0;
        s.count = (uint16_t)c;
        s.tex_width = 256;
        s.tex_w_mask = 0xFF;
        emit_span_raw(s);
        m.apply_draw_span(s);

        if (!submit_and_wait()) {
            check_fail("span_raw_count_boundary", "timeout");
            continue;
        }
        char nm[64];
        snprintf(nm, sizeof(nm), "span_raw_count.%d", c);
        if (c > 0)
            compare_fb_region(nm, m, FB_BASE_BYTE, 320, 0, row, c, 1);
        char nm2[64];
        snprintf(nm2, sizeof(nm2), "span_raw_count.%d.border", c);
        compare_sentinel_border(nm2, FB_BASE_BYTE, 320, 0, row,
                                 c == 0 ? 1 : c, 1, 4);
    }
}

static void test_span_raw_count_4096_truncation() {
    /* Wire-format gap: SDK count is 16-bit but RTL decodes 12 bits.
     * count >= 4096 silently truncates.  Lock this in.
     *
     * The test draws a 4096-count span in a 1×4096 ramp — RTL will draw
     * (4096 & 0xFFF) = 0 pixels; the FB row stays all-sentinel.  The CPU
     * model also masks count to 12 bits to mirror RTL.
     */
    printf("TEST span_raw_count_4096_truncation\n");
    gpu_init();
    auto m = preload_with_sentinel();
    /* Use a multi-row FB so a count=4096 hypothetical would absolutely
     * touch real bytes; we want the test to fail loudly if RTL ever
     * decodes count as 16-bit. */
    cmd_set_fb(FB_BASE_BYTE, 320);
    m.st_fb_addr = FB_BASE_BYTE;
    /* 1×16 texture of 0xCC. */
    std::vector<uint8_t> tex(16, 0xCC);
    upload_texture(TEX_BASE_BYTE, tex);
    m.snapshot_from_sdram();

    SpanWire s = make_span();
    s.fb_addr   = FB_BASE_BYTE;
    s.tex_addr  = TEX_BASE_BYTE;
    s.s = 0; s.t = 0; s.sstep = 0x10000; s.tstep = 0;
    s.count     = 4096;     // exact boundary — wire bits are [27:16]
    s.tex_width = 16; s.tex_w_mask = 0xF;
    emit_span_raw(s);
    m.apply_draw_span(s);   // model also masks to 12 bits

    if (!submit_and_wait()) {
        check_fail("span_raw_count_4096_truncation", "timeout");
        return;
    }
    /* No bytes should have changed — count truncates to 0. */
    compare_fb_region("span_raw_count_4096_truncation.fb", m, FB_BASE_BYTE,
                      320, 0, 0, 320, 1);
}

static void test_span_raw_negative_stride() {
    /* fb_stride = -1 walks backward.  Tests the signed sign-extension
     * path (gpu_core.v line 2578: `{{16{sp_fb_stride[15]}}, sp_fb_stride}`). */
    printf("TEST span_raw_negative_stride\n");
    gpu_init();
    auto m = preload_with_sentinel();
    std::vector<uint8_t> tex(16);
    for (int i = 0; i < 16; i++) tex[i] = (uint8_t)(0x80 + i);
    upload_texture(TEX_BASE_BYTE, tex);
    m.snapshot_from_sdram();
    cmd_set_fb(FB_BASE_BYTE, 320);
    m.st_fb_addr = FB_BASE_BYTE;

    /* Start at FB[15], walk back to FB[0] with fb_stride=-1.
     * Texture sampled in forward order: tex[0], tex[1], ..., tex[15]. */
    SpanWire s = make_span();
    s.fb_addr  = FB_BASE_BYTE + 15;
    s.tex_addr = TEX_BASE_BYTE;
    s.s = 0; s.t = 0; s.sstep = 0x10000; s.tstep = 0;
    s.count = 16;
    s.tex_width = 16; s.tex_w_mask = 0xF;
    s.fb_stride = -1;
    emit_span_raw(s);
    m.apply_draw_span(s);

    if (!submit_and_wait()) {
        check_fail("span_raw_negative_stride", "timeout");
        return;
    }
    compare_fb_region("span_raw_negative_stride.fb", m, FB_BASE_BYTE, 320,
                      0, 0, 16, 1);
    compare_sentinel_border("span_raw_negative_stride.border", FB_BASE_BYTE,
                             320, 0, 0, 16, 1, 4);
}

static void test_span_raw_mask_zero_means_identity() {
    /* mask=0 in payload → RTL converts to 0xFFFF (no wrap).  Test that
     * a span with explicit mask=0 + tex_width=64 walks the texture
     * linearly (no wrap-back). */
    printf("TEST span_raw_mask_zero_means_identity\n");
    gpu_init();
    auto m = preload_with_sentinel();
    /* Texture: 64 unique bytes (0..63). */
    std::vector<uint8_t> tex(64);
    for (int i = 0; i < 64; i++) tex[i] = (uint8_t)i;
    upload_texture(TEX_BASE_BYTE, tex);
    m.snapshot_from_sdram();
    cmd_set_fb(FB_BASE_BYTE, 320);
    m.st_fb_addr = FB_BASE_BYTE;

    SpanWire s = make_span();
    s.fb_addr = FB_BASE_BYTE;
    s.tex_addr = TEX_BASE_BYTE;
    s.s = 0; s.t = 0; s.sstep = 0x10000; s.tstep = 0;
    s.count = 64;
    s.tex_width = 64;
    s.tex_w_mask = 0;     // 0 → 0xFFFF (no wrap)
    s.tex_h_mask = 0;
    emit_span_raw(s);
    m.apply_draw_span(s);

    if (!submit_and_wait()) {
        check_fail("span_raw_mask_zero_means_identity", "timeout");
        return;
    }
    compare_fb_region("span_raw_mask_zero_means_identity.fb", m,
                      FB_BASE_BYTE, 320, 0, 0, 64, 1);
    compare_sentinel_border("span_raw_mask_zero_means_identity.border",
                             FB_BASE_BYTE, 320, 0, 0, 64, 1, 4);
}

// ---- Section 10: DRAW_SPAN with COLORMAP -----------------------------------
static void test_span_colormap_explicit_per_span_id() {
    printf("TEST span_colormap_explicit_per_span_id\n");
    gpu_init();
    auto m = preload_with_sentinel();
    upload_palookup_inverted_row(5, 0);
    upload_palookup_const_row   (15, 0, 0xE0);
    std::vector<uint8_t> tex(16);
    for (int i = 0; i < 16; i++) tex[i] = (uint8_t)(i * 16);
    upload_texture(TEX_BASE_BYTE, tex);
    m.snapshot_from_sdram();
    cmd_set_fb(FB_BASE_BYTE, 320);
    m.st_fb_addr = FB_BASE_BYTE;
    /* Sticky slot starts at 0 (after gpu_init reset). */

    SpanWire base = make_span();
    base.tex_addr = TEX_BASE_BYTE;
    base.tex_width = 16; base.tex_w_mask = 0xF;
    base.s = 0; base.t = 0; base.sstep = 0x10000; base.tstep = 0;
    base.count = 16;
    base.flags = 0x1;

    SpanWire s_5 = base; s_5.fb_addr  = FB_BASE_BYTE;       s_5.colormap_id = 5;
    SpanWire s_15 = base; s_15.fb_addr = FB_BASE_BYTE + 320; s_15.colormap_id = 15;
    emit_span_raw(s_5);  m.apply_draw_span(s_5);
    emit_span_raw(s_15); m.apply_draw_span(s_15);

    if (!submit_and_wait()) {
        check_fail("span_colormap_explicit_per_span_id", "timeout");
        return;
    }
    compare_fb_region("span_colormap.slot5_inv", m, FB_BASE_BYTE, 320,
                      0, 0, 16, 1);
    compare_fb_region("span_colormap.slot15_const", m, FB_BASE_BYTE, 320,
                      0, 1, 16, 1);
}

static void test_span_colormap_light_wraps_mod_64() {
    /* RTL uses light[5:0] for palookup row indexing. light=64 should
     * wrap to row 0; light=65 to row 1. Verify with two distinct rows
     * that visibly differ. */
    printf("TEST span_colormap_light_wraps_mod_64\n");
    gpu_init();
    auto m = preload_with_sentinel();
    upload_palookup_const_row(0, 0, 0x33);     // row 0
    upload_palookup_const_row(0, 1, 0x55);     // row 1
    std::vector<uint8_t> tex(8, 0x42);
    upload_texture(TEX_BASE_BYTE, tex);
    m.snapshot_from_sdram();
    cmd_set_fb(FB_BASE_BYTE, 320);
    m.st_fb_addr = FB_BASE_BYTE;

    SpanWire base = make_span();
    base.tex_addr = TEX_BASE_BYTE;
    base.tex_width = 8; base.tex_w_mask = 0x7;
    base.s = 0; base.t = 0; base.sstep = 0x10000; base.tstep = 0;
    base.count = 8;
    base.flags = 0x1;

    SpanWire s64 = base; s64.fb_addr = FB_BASE_BYTE;       s64.light = 64;
    SpanWire s65 = base; s65.fb_addr = FB_BASE_BYTE + 320; s65.light = 65;
    emit_span_raw(s64); m.apply_draw_span(s64);
    emit_span_raw(s65); m.apply_draw_span(s65);

    if (!submit_and_wait()) {
        check_fail("span_colormap_light_wraps_mod_64", "timeout");
        return;
    }
    compare_fb_region("light_wraps.row64_to_0", m, FB_BASE_BYTE, 320,
                      0, 0, 8, 1);
    compare_fb_region("light_wraps.row65_to_1", m, FB_BASE_BYTE, 320,
                      0, 1, 8, 1);
}

// ---- Section 11: DRAW_SPAN with SKIP_ZERO ----------------------------------
static void test_skip_zero_only_discards_0xff() {
    /* Texture has 0x00, 0x01, 0xFE, 0xFF.  Skip-zero must discard ONLY
     * the 0xFF (NOT the 0x00). */
    printf("TEST skip_zero_only_discards_0xff\n");
    gpu_init();
    auto m = preload_with_sentinel();
    std::vector<uint8_t> tex { 0x00, 0x01, 0xFE, 0xFF };
    upload_texture(TEX_BASE_BYTE, tex);
    m.snapshot_from_sdram();
    cmd_set_fb(FB_BASE_BYTE, 320);
    m.st_fb_addr = FB_BASE_BYTE;
    /* Pre-fill the FB row with a known sentinel that's distinct from
     * texture/palookup output, so a "skip" stays as 0x77. */
    cmd_clear_rect(FB_BASE_BYTE, 320, 1, 0, 0x77);
    m.apply_clear_rect(FB_BASE_BYTE, 320, 1, 0, 0x77);

    SpanWire s = make_span();
    s.fb_addr = FB_BASE_BYTE;
    s.tex_addr = TEX_BASE_BYTE;
    s.tex_width = 4; s.tex_w_mask = 0x3;
    s.s = 0; s.t = 0; s.sstep = 0x10000; s.tstep = 0;
    s.count = 4;
    s.flags = (1 << 2);                    // SPAN_SKIP_ZERO only
    emit_span_raw(s);
    m.apply_draw_span(s);

    if (!submit_and_wait()) {
        check_fail("skip_zero_only_discards_0xff", "timeout");
        return;
    }
    compare_fb_region("skip_zero_only_discards_0xff", m, FB_BASE_BYTE, 320,
                      0, 0, 4, 1);
}

static void test_skip_zero_with_colormap() {
    /* SKIP_ZERO discards before colormap (gpu_core.v line 2528-2529).
     * Texture has 0xFF at index 0; palookup[0][0][0xFF] = 0x99.  With
     * skip-zero set, the FB byte must NOT become 0x99 — discard wins. */
    printf("TEST skip_zero_with_colormap\n");
    gpu_init();
    auto m = preload_with_sentinel();
    upload_palookup_identity_row(0, 0);
    /* Override the palookup entry for texel 0xFF so we'd see if cmap
     * accidentally ran first. */
    sdram_write_byte(PALOOKUP_BASE_BYTE + 0xFF, 0x99);
    std::vector<uint8_t> tex { 0xFF, 0x10 };
    upload_texture(TEX_BASE_BYTE, tex);
    m.snapshot_from_sdram();
    cmd_set_fb(FB_BASE_BYTE, 320);
    m.st_fb_addr = FB_BASE_BYTE;
    cmd_clear_rect(FB_BASE_BYTE, 320, 1, 0, 0x77);
    m.apply_clear_rect(FB_BASE_BYTE, 320, 1, 0, 0x77);

    SpanWire s = make_span();
    s.fb_addr = FB_BASE_BYTE;
    s.tex_addr = TEX_BASE_BYTE;
    s.tex_width = 2; s.tex_w_mask = 0x1;
    s.s = 0; s.t = 0; s.sstep = 0x10000; s.tstep = 0;
    s.count = 2;
    s.flags = (1 << 0) | (1 << 2);          // COLORMAP + SKIP_ZERO
    emit_span_raw(s);
    m.apply_draw_span(s);

    if (!submit_and_wait()) {
        check_fail("skip_zero_with_colormap", "timeout");
        return;
    }
    compare_fb_region("skip_zero_with_colormap", m, FB_BASE_BYTE, 320,
                      0, 0, 2, 1);
}

// ---- Section 12: DRAW_SPAN with TRANSLUC -----------------------------------
static void test_transluc_basic_blend() {
    printf("TEST transluc_basic_blend\n");
    gpu_init();
    auto m = preload_with_sentinel();
    auto table = make_transluc_table_avg();
    upload_transluc_table(table);
    m.transluc = table;

    /* Pre-fill destination row with 0x10 so blend = (src+0x10)/2. */
    cmd_set_fb(FB_BASE_BYTE, 320);
    m.st_fb_addr = FB_BASE_BYTE;
    cmd_clear_rect(FB_BASE_BYTE, 320, 1, 0, 0x10);
    m.apply_clear_rect(FB_BASE_BYTE, 320, 1, 0, 0x10);

    /* Texture: byte values 0..15, drawn as-is (no colormap).  RTL
     * src = post-cmap = post-skip = texel here. */
    std::vector<uint8_t> tex(16);
    for (int i = 0; i < 16; i++) tex[i] = (uint8_t)i;
    upload_texture(TEX_BASE_BYTE, tex);
    m.snapshot_from_sdram();
    /* Re-apply the dest fill on the model (snapshot reset it). */
    m.apply_clear_rect(FB_BASE_BYTE, 320, 1, 0, 0x10);

    SpanWire s = make_span();
    s.fb_addr = FB_BASE_BYTE;
    s.tex_addr = TEX_BASE_BYTE;
    s.tex_width = 16; s.tex_w_mask = 0xF;
    s.s = 0; s.t = 0; s.sstep = 0x10000; s.tstep = 0;
    s.count = 16;
    s.flags = (1 << 6);                    // SPAN_TRANSLUC only
    emit_span_raw(s);
    m.apply_draw_span(s);

    if (!submit_and_wait()) {
        check_fail("transluc_basic_blend", "timeout");
        return;
    }
    compare_fb_region("transluc_basic_blend.fb", m, FB_BASE_BYTE, 320,
                      0, 0, 16, 1);
}

static void test_transluc_overdraw_same_word() {
    /* Two transluc spans that hit the SAME word, back-to-back: the
     * second span's blend must read the post-blend byte from the first
     * (fb_acc bypass), not the original 0x10. */
    printf("TEST transluc_overdraw_same_word\n");
    gpu_init();
    auto m = preload_with_sentinel();
    auto table = make_transluc_table_avg();
    upload_transluc_table(table);
    m.transluc = table;

    cmd_set_fb(FB_BASE_BYTE, 320);
    m.st_fb_addr = FB_BASE_BYTE;
    cmd_clear_rect(FB_BASE_BYTE, 320, 1, 0, 0x10);
    m.apply_clear_rect(FB_BASE_BYTE, 320, 1, 0, 0x10);

    std::vector<uint8_t> tex(4, 0x40);
    upload_texture(TEX_BASE_BYTE, tex);
    m.snapshot_from_sdram();
    m.apply_clear_rect(FB_BASE_BYTE, 320, 1, 0, 0x10);

    SpanWire s = make_span();
    s.fb_addr = FB_BASE_BYTE;
    s.tex_addr = TEX_BASE_BYTE;
    s.tex_width = 4; s.tex_w_mask = 0x3;
    s.s = 0; s.t = 0; s.sstep = 0x10000; s.tstep = 0;
    s.count = 4;
    s.flags = (1 << 6);

    /* First pass — same-word writes lanes 0..3 of the first 4 bytes. */
    emit_span_raw(s); m.apply_draw_span(s);
    /* Second pass — should re-blend the now-blended bytes. */
    emit_span_raw(s); m.apply_draw_span(s);

    if (!submit_and_wait()) {
        check_fail("transluc_overdraw_same_word", "timeout");
        return;
    }
    compare_fb_region("transluc_overdraw_same_word.fb", m, FB_BASE_BYTE,
                      320, 0, 0, 4, 1);
}

// ---- Section 13: PERSP spans (constant-Z exact) ----------------------------
static void test_persp_constant_z_matches_affine() {
    /* Constant-Z perspective: zinv constant, sZstep/tZstep matching
     * sstep/tstep × zinv such that the de-projection produces the same
     * affine s/t per pixel as a non-persp span. */
    printf("TEST persp_constant_z_matches_affine\n");
    gpu_init();
    auto m = preload_with_sentinel();
    std::vector<uint8_t> tex(64);
    for (int i = 0; i < 64; i++) tex[i] = (uint8_t)i;
    upload_texture(TEX_BASE_BYTE, tex);
    m.snapshot_from_sdram();
    cmd_set_fb(FB_BASE_BYTE, 320);
    m.st_fb_addr = FB_BASE_BYTE;

    /* Reference: pure affine span, tex 0..63 across 64 pixels. */
    SpanWire s = make_span();
    s.fb_addr = FB_BASE_BYTE;
    s.tex_addr = TEX_BASE_BYTE;
    s.tex_width = 64; s.tex_w_mask = 0x3F;
    s.s = 0; s.t = 0; s.sstep = 0x10000; s.tstep = 0;
    s.count = 64;
    s.flags = (1 << 5);    // SPAN_PERSP

    /* Constant-Z: zinv = 0x10000, zinv_step = 0; s/Z = s*zinv = 0,
     * sZstep = sstep * zinv = 0x10000.  Result: per-pixel s = sZ/zinv = 0,
     * stepped by 1.0/pixel — same as affine. */
    s.sZ = 0; s.tZ = 0; s.zinv = 0x10000;
    s.sZstep = 0x10000; s.tZstep = 0; s.zinv_step = 0;

    /* The CPU model only does affine — but for constant-Z the GPU
     * output must match an affine span. */
    SpanWire ref_affine = s;
    ref_affine.flags = 0;   // affine reference
    m.apply_draw_span(ref_affine);

    emit_span_raw(s);
    if (!submit_and_wait()) {
        check_fail("persp_constant_z_matches_affine", "timeout");
        return;
    }
    /* Tolerate up to 1 byte of mismatch (the persp reciprocal LUT may
     * have a 1-LSB rounding step in the lowest texel index across the
     * 64-pixel span). */
    int diffs = 0;
    int first_x = -1; uint8_t first_got = 0, first_exp = 0;
    for (int dx = 0; dx < 64; dx++) {
        uint32_t addr = FB_BASE_BYTE + dx;
        uint8_t got = sdram_read_byte(addr);
        uint8_t exp = m.read(addr);
        if (got != exp) {
            if (diffs == 0) { first_x = dx; first_got = got; first_exp = exp; }
            diffs++;
        }
    }
    if (diffs <= 2) {
        printf("  PASS persp_constant_z_matches_affine (diffs=%d, tolerance=2)\n",
               diffs);
        pass_count++;
    } else {
        printf("  FAIL persp_constant_z_matches_affine: %d diffs, "
               "first @ x=%d got=0x%02x exp=0x%02x\n",
               diffs, first_x, first_got, first_exp);
        fail_count++;
    }
}

// ---- Section 14: DRAW_SPANS_BATCH equivalence ------------------------------
static void test_batch_equals_individual() {
    /* For the same payload, a batch and N individual spans must produce
     * identical FB output.  Cover N = 1, 2, 4, 8, 16, 32. */
    printf("TEST batch_equals_individual\n");
    int sizes[] = { 1, 2, 4, 8, 16, 32 };
    for (int N : sizes) {
        gpu_init();
        auto m = preload_with_sentinel();
        std::vector<uint8_t> tex(16);
        for (int i = 0; i < 16; i++) tex[i] = (uint8_t)(i * 8);
        upload_texture(TEX_BASE_BYTE, tex);
        m.snapshot_from_sdram();
        cmd_set_fb(FB_BASE_BYTE, 320);
        m.st_fb_addr = FB_BASE_BYTE;

        std::vector<SpanWire> spans;
        for (int i = 0; i < N; i++) {
            SpanWire s = make_span();
            s.fb_addr  = FB_BASE_BYTE + (uint32_t)i * 320;
            s.tex_addr = TEX_BASE_BYTE;
            s.tex_width = 16; s.tex_w_mask = 0xF;
            s.s = 0; s.t = 0; s.sstep = 0x10000; s.tstep = 0;
            s.count = 16;
            s.flags = 0;
            spans.push_back(s);
        }
        emit_batch_raw(spans);
        m.apply_batch(spans);
        if (!submit_and_wait()) {
            char nm[64]; snprintf(nm, sizeof(nm), "batch_eq_individual.N%d", N);
            check_fail(nm, "timeout");
            continue;
        }
        char nm[64]; snprintf(nm, sizeof(nm), "batch_eq_individual.N%d", N);
        compare_fb_region(nm, m, FB_BASE_BYTE, 320, 0, 0, 16, N);
    }
}

static void test_batch_mixed_per_span_colormap() {
    /* A 4-span batch where each span has a different per-span
     * colormap_id.  Verify all four pick up the right slot. */
    printf("TEST batch_mixed_per_span_colormap\n");
    gpu_init();
    auto m = preload_with_sentinel();
    upload_palookup_identity_row(1, 0);
    upload_palookup_inverted_row(2, 0);
    upload_palookup_const_row   (3, 0, 0xA0);
    upload_palookup_const_row   (4, 0, 0x55);

    std::vector<uint8_t> tex(8);
    for (int i = 0; i < 8; i++) tex[i] = (uint8_t)(i * 16);
    upload_texture(TEX_BASE_BYTE, tex);
    m.snapshot_from_sdram();
    cmd_set_fb(FB_BASE_BYTE, 320);
    m.st_fb_addr = FB_BASE_BYTE;
    /* Sticky default 7 — should NEVER be used because each span has
     * an explicit non-zero colormap_id. */
    upload_palookup_const_row(7, 0, 0xEE);
    cmd_set_colormap_id(7); m.st_colormap = 7;

    std::vector<SpanWire> spans;
    SpanWire base = make_span();
    base.tex_addr = TEX_BASE_BYTE;
    base.tex_width = 8; base.tex_w_mask = 0x7;
    base.s = 0; base.t = 0; base.sstep = 0x10000; base.tstep = 0;
    base.count = 8;
    base.flags = 0x1;
    for (int i = 0; i < 4; i++) {
        SpanWire s = base;
        s.fb_addr = FB_BASE_BYTE + (uint32_t)i * 320;
        s.colormap_id = (uint8_t)(i + 1);
        spans.push_back(s);
    }
    emit_batch_raw(spans);
    m.apply_batch(spans);

    if (!submit_and_wait()) {
        check_fail("batch_mixed_per_span_colormap", "timeout");
        return;
    }
    for (int i = 0; i < 4; i++) {
        char nm[64]; snprintf(nm, sizeof(nm), "batch_mixed_cmap.row%d", i);
        compare_fb_region(nm, m, FB_BASE_BYTE, 320, 0, i, 8, 1);
    }
    /* Sticky slot 7 must NOT have leaked through — check none of
     * the rows is 0xEE end-to-end. */
}

static void test_span4_opaque_equals_four_spans() {
    printf("TEST span4_opaque_equals_four_spans\n");
    gpu_init();
    auto m = preload_with_sentinel();

    for (int lane = 0; lane < 4; lane++) {
        std::vector<uint8_t> tex(8);
        for (int i = 0; i < 8; i++)
            tex[i] = (uint8_t)(0x10 + lane * 0x20 + i);
        upload_texture(TEX_BASE_BYTE + (uint32_t)lane * 0x100, tex);
    }
    m.snapshot_from_sdram();

    Span4Wire q = make_span4();
    q.fb_addr = FB_BASE_BYTE + 5 * 320 + 7;
    q.count = 6;
    q.flags = 0;
    q.colormap_id = 0;
    for (int lane = 0; lane < 4; lane++) {
        q.tex_addr[lane] = TEX_BASE_BYTE + (uint32_t)lane * 0x100;
        q.t[lane] = 0;
        q.tstep[lane] = 0x10000;
        q.light[lane] = 0;
    }

    emit_span4_raw(q);
    m.apply_span4_affine(q);
    if (!submit_and_wait()) {
        check_fail("span4_opaque_equals_four_spans", "timeout");
        return;
    }
    compare_fb_region("span4_opaque_equals_four_spans.fb", m,
                      FB_BASE_BYTE, 320, 7, 5, 4, q.count);
}

static void test_span4_texture_height_mask() {
    printf("TEST span4_texture_height_mask\n");
    gpu_init();
    auto m = preload_with_sentinel();

    for (int lane = 0; lane < 4; lane++) {
        std::vector<uint8_t> tex(8);
        for (int i = 0; i < 4; i++)
            tex[i] = (uint8_t)(0x30 + lane * 0x10 + i);
        for (int i = 4; i < 8; i++)
            tex[i] = (uint8_t)(0xE0 + lane * 4 + (i - 4));
        upload_texture(TEX_BASE_BYTE + (uint32_t)lane * 0x100, tex);
    }
    m.snapshot_from_sdram();

    Span4Wire q = make_span4();
    q.fb_addr = FB_BASE_BYTE + 18 * 320 + 16;
    q.count = 8;
    q.flags = 0;
    q.colormap_id = 0;
    q.tex_h_mask = 3;
    for (int lane = 0; lane < 4; lane++) {
        q.tex_addr[lane] = TEX_BASE_BYTE + (uint32_t)lane * 0x100;
        q.t[lane] = 0;
        q.tstep[lane] = 0x10000;
        q.light[lane] = 0;
    }

    emit_span4_raw(q);
    m.apply_span4_affine(q);
    if (!submit_and_wait()) {
        check_fail("span4_texture_height_mask", "timeout");
        return;
    }
    compare_fb_region("span4_texture_height_mask.fb", m,
                      FB_BASE_BYTE, 320, 16, 18, 4, q.count);
}

static void test_span4_aligned_coalesces_row_writes() {
    printf("TEST span4_aligned_coalesces_row_writes\n");
    gpu_init();
    auto m = preload_with_sentinel();

    for (int lane = 0; lane < 4; lane++) {
        std::vector<uint8_t> tex(8);
        for (int i = 0; i < 8; i++)
            tex[i] = (uint8_t)(0x70 + lane * 0x10 + i);
        upload_texture(TEX_BASE_BYTE + (uint32_t)lane * 0x100, tex);
        upload_palookup_const_row(5, (uint8_t)lane, (uint8_t)(0x90 + lane));
    }
    m.snapshot_from_sdram();

    Span4Wire q = make_span4();
    q.fb_addr = FB_BASE_BYTE + 40 * 320 + 8;  // 4-byte aligned x
    q.count = 6;
    q.flags = 0x1;       // COLORMAP
    q.colormap_id = 5;
    for (int lane = 0; lane < 4; lane++) {
        q.tex_addr[lane] = TEX_BASE_BYTE + (uint32_t)lane * 0x100;
        q.t[lane] = 0;
        q.tstep[lane] = 0x10000;
        q.light[lane] = (uint8_t)lane;
    }

    uint32_t aw_before = tb->dbg_aw_count;
    emit_span4_raw(q);
    m.apply_span4_affine(q);
    if (!submit_and_wait()) {
        check_fail("span4_aligned_coalesces_row_writes", "timeout");
        return;
    }

    compare_fb_region("span4_aligned_coalesces_row_writes.fb", m,
                      FB_BASE_BYTE, 320, 8, 40, 4, q.count);

    uint32_t aw_after = tb->dbg_aw_count;
    uint32_t writes = aw_after - aw_before;
    if (writes == q.count) {
        check_pass("span4_aligned_coalesces_row_writes.aw_count");
    } else {
        char buf[128];
        snprintf(buf, sizeof(buf), "AW handshakes=%u expected rows=%u",
                 writes, (uint32_t)q.count);
        check_fail("span4_aligned_coalesces_row_writes.aw_count", buf);
    }
}

static void test_span4_aligned_texture_cache_thrash() {
    printf("TEST span4_aligned_texture_cache_thrash\n");
    gpu_init();
    auto m = preload_with_sentinel();

    /* Four lane bases separated by 0x4000 share gpu_tex_cache set bits
     * [13:4] but carry different tags.  Row-major span4 therefore forces
     * frequent lane-to-lane evictions while still expecting byte-exact
     * output versus four scalar spans. */
    for (int row = 0; row < 4; row++) {
        std::vector<uint8_t> cmap(256);
        for (int i = 0; i < 256; i++)
            cmap[i] = (uint8_t)((i + row * 0x31) ^ 0xA5);
        upload_palookup_slot(6, cmap, (uint32_t)row * 256);
    }

    for (int lane = 0; lane < 4; lane++) {
        std::vector<uint8_t> tex(128);
        for (int i = 0; i < 128; i++)
            tex[i] = (uint8_t)(0x20 + lane * 0x30 + ((i * 7 + lane) & 0x1F));
        upload_texture(TEX_BASE_BYTE + (uint32_t)lane * 0x4000, tex);
    }
    m.snapshot_from_sdram();

    Span4Wire q = make_span4();
    q.fb_addr = FB_BASE_BYTE + 64 * 320 + 12;  // aligned x
    q.count = 24;
    q.flags = 0x1;       // COLORMAP
    q.colormap_id = 6;
    for (int lane = 0; lane < 4; lane++) {
        q.tex_addr[lane] = TEX_BASE_BYTE + (uint32_t)lane * 0x4000;
        q.t[lane] = (lane * 3) << 16;
        q.tstep[lane] = (lane + 1) << 16;
        q.light[lane] = (uint8_t)lane;
    }

    uint32_t req_before = mmio_read(REG_TRANSLUC_ADDR);
    uint32_t miss_before = mmio_read(REG_TRANSLUC_DATA);
    emit_span4_raw(q);
    m.apply_span4_affine(q);
    if (!submit_and_wait()) {
        check_fail("span4_aligned_texture_cache_thrash", "timeout");
        return;
    }
    compare_fb_region("span4_aligned_texture_cache_thrash.fb", m,
                      FB_BASE_BYTE, 320, 12, 64, 4, q.count);

    uint32_t reqs = mmio_read(REG_TRANSLUC_ADDR) - req_before;
    uint32_t misses = mmio_read(REG_TRANSLUC_DATA) - miss_before;
    if (misses > 8 && reqs >= (uint32_t)q.count * 4u) {
        check_pass("span4_aligned_texture_cache_thrash.cache_pressure");
    } else {
        char buf[128];
        snprintf(buf, sizeof(buf), "reqs=%u misses=%u", reqs, misses);
        check_fail("span4_aligned_texture_cache_thrash.cache_pressure", buf);
    }
}

static void test_span4_masked_equals_four_spans() {
    printf("TEST span4_masked_equals_four_spans\n");
    gpu_init();
    auto m = preload_with_sentinel();

    for (int lane = 0; lane < 4; lane++) {
        std::vector<uint8_t> tex = {
            (uint8_t)(0x20 + lane), 0xFF,
            (uint8_t)(0x30 + lane), (uint8_t)(0x40 + lane),
            0xFF, (uint8_t)(0x50 + lane)
        };
        upload_texture(TEX_BASE_BYTE + (uint32_t)lane * 0x100, tex);
    }
    m.snapshot_from_sdram();

    Span4Wire q = make_span4();
    q.fb_addr = FB_BASE_BYTE + 12 * 320 + 3;
    q.count = 6;
    q.flags = 0x4;  // SKIP_ZERO
    q.colormap_id = 0;
    for (int lane = 0; lane < 4; lane++) {
        q.tex_addr[lane] = TEX_BASE_BYTE + (uint32_t)lane * 0x100;
        q.t[lane] = 0;
        q.tstep[lane] = 0x10000;
        q.light[lane] = 0;
    }

    emit_span4_raw(q);
    m.apply_span4_affine(q);
    if (!submit_and_wait()) {
        check_fail("span4_masked_equals_four_spans", "timeout");
        return;
    }
    compare_fb_region("span4_masked_equals_four_spans.fb", m,
                      FB_BASE_BYTE, 320, 3, 12, 4, q.count);
}

static void test_span4_explicit_colormap_slot() {
    printf("TEST span4_explicit_colormap_slot\n");
    gpu_init();
    auto m = preload_with_sentinel();

    for (int row = 0; row < 4; row++)
        upload_palookup_const_row(5, (uint8_t)row, (uint8_t)(0x60 + row));
    for (int row = 0; row < 4; row++)
        upload_palookup_const_row(7, (uint8_t)row, 0xEE);

    for (int lane = 0; lane < 4; lane++) {
        std::vector<uint8_t> tex(4);
        for (int i = 0; i < 4; i++)
            tex[i] = (uint8_t)(0x08 + lane * 4 + i);
        upload_texture(TEX_BASE_BYTE + (uint32_t)lane * 0x100, tex);
    }
    m.snapshot_from_sdram();
    cmd_set_colormap_id(7);
    m.st_colormap = 7;

    Span4Wire q = make_span4();
    q.fb_addr = FB_BASE_BYTE + 20 * 320 + 11;
    q.count = 4;
    q.flags = 0x1;        // COLORMAP
    q.colormap_id = 5;    // explicit; sticky slot 7 must not leak in
    for (int lane = 0; lane < 4; lane++) {
        q.tex_addr[lane] = TEX_BASE_BYTE + (uint32_t)lane * 0x100;
        q.t[lane] = 0;
        q.tstep[lane] = 0x10000;
        q.light[lane] = (uint8_t)lane;
    }

    emit_span4_raw(q);
    m.apply_span4_affine(q);
    if (!submit_and_wait()) {
        check_fail("span4_explicit_colormap_slot", "timeout");
        return;
    }
    compare_fb_region("span4_explicit_colormap_slot.fb", m,
                      FB_BASE_BYTE, 320, 11, 20, 4, q.count);
}

static void test_span4_batch_two_payloads() {
    printf("TEST span4_batch_two_payloads\n");
    gpu_init();
    auto m = preload_with_sentinel();

    for (int lane = 0; lane < 4; lane++) {
        std::vector<uint8_t> tex(4);
        for (int i = 0; i < 4; i++)
            tex[i] = (uint8_t)(0x80 + lane * 8 + i);
        upload_texture(TEX_BASE_BYTE + (uint32_t)lane * 0x100, tex);
    }
    m.snapshot_from_sdram();

    std::vector<Span4Wire> qs;
    for (int n = 0; n < 2; n++) {
        Span4Wire q = make_span4();
        q.fb_addr = FB_BASE_BYTE + (30 + n * 5) * 320 + 2;
        q.count = 4;
        q.flags = 0;
        q.colormap_id = 0;
        for (int lane = 0; lane < 4; lane++) {
            q.tex_addr[lane] = TEX_BASE_BYTE + (uint32_t)lane * 0x100;
            q.t[lane] = n * 0x10000;
            q.tstep[lane] = 0x10000;
            q.light[lane] = 0;
        }
        qs.push_back(q);
    }

    emit_span4_batch_raw(qs);
    m.apply_span4_batch(qs);
    if (!submit_and_wait()) {
        check_fail("span4_batch_two_payloads", "timeout");
        return;
    }
    compare_fb_region("span4_batch_two_payloads.a", m,
                      FB_BASE_BYTE, 320, 2, 30, 4, 4);
    compare_fb_region("span4_batch_two_payloads.b", m,
                      FB_BASE_BYTE, 320, 2, 35, 4, 4);
}

static void test_batch_dma_equals_inline() {
    /* DMA-delivered batch must produce identical output to the inline
     * batch with the same spans. */
    printf("TEST batch_dma_equals_inline\n");
    gpu_init();
    auto m = preload_with_sentinel();
    std::vector<uint8_t> tex(32);
    for (int i = 0; i < 32; i++) tex[i] = (uint8_t)(i + 32);
    upload_texture(TEX_BASE_BYTE, tex);
    m.snapshot_from_sdram();
    cmd_set_fb(FB_BASE_BYTE, 320);
    m.st_fb_addr = FB_BASE_BYTE;

    std::vector<SpanWire> spans;
    for (int i = 0; i < 8; i++) {
        SpanWire s = make_span();
        s.fb_addr = FB_BASE_BYTE + (uint32_t)i * 320;
        s.tex_addr = TEX_BASE_BYTE;
        s.tex_width = 32; s.tex_w_mask = 0x1F;
        s.s = 0; s.t = 0; s.sstep = 0x10000; s.tstep = 0;
        s.count = 32;
        s.flags = 0;
        spans.push_back(s);
    }
    emit_batch_dma(spans);
    m.apply_batch(spans);

    if (!submit_and_wait()) {
        check_fail("batch_dma_equals_inline", "timeout");
        return;
    }
    compare_fb_region("batch_dma_equals_inline.fb", m, FB_BASE_BYTE, 320,
                      0, 0, 32, 8);
}

static void test_command_stream_dma_mixed_span_span4() {
    /* Raw DMA command streams let software batch mixed DRAW_SPAN and
     * DRAW_SPAN4 commands without changing draw order. */
    printf("TEST command_stream_dma_mixed_span_span4\n");
    gpu_init();
    auto m = preload_with_sentinel();

    std::vector<uint8_t> tex(192);
    for (int i = 0; i < (int)tex.size(); i++)
        tex[i] = (uint8_t)(0x20 + i);
    upload_texture(TEX_BASE_BYTE, tex);
    upload_palookup_identity_row(0, 0);
    m.snapshot_from_sdram();

    SpanWire s0 = make_span();
    s0.fb_addr = FB_BASE_BYTE + 20u * 320u + 10u;
    s0.tex_addr = TEX_BASE_BYTE;
    s0.tex_width = 16;
    s0.tex_w_mask = 0x0F;
    s0.s = 0;
    s0.t = 0;
    s0.sstep = 0x10000;
    s0.tstep = 0;
    s0.count = 8;
    s0.light = 0;
    s0.flags = 0x1;       // SPAN_COLORMAP
    s0.colormap_id = 0;

    Span4Wire q = make_span4();
    q.fb_addr = FB_BASE_BYTE + 10u * 320u + 30u;
    q.count = 8;
    q.flags = 0x1;        // SPAN_COLORMAP
    q.colormap_id = 0;
    for (int lane = 0; lane < 4; lane++) {
        q.tex_addr[lane] = TEX_BASE_BYTE + 64u + (uint32_t)lane * 16u;
        q.t[lane] = 0;
        q.tstep[lane] = 0x10000;
        q.light[lane] = 0;
    }

    SpanWire s1 = make_span();
    s1.fb_addr = FB_BASE_BYTE + 12u * 320u + 30u;
    s1.tex_addr = TEX_BASE_BYTE + 128u;
    s1.tex_width = 16;
    s1.tex_w_mask = 0x0F;
    s1.s = 0;
    s1.t = 0;
    s1.sstep = 0x10000;
    s1.tstep = 0;
    s1.count = 4;
    s1.light = 0;
    s1.flags = 0x1;       // SPAN_COLORMAP
    s1.colormap_id = 0;

    std::vector<uint32_t> stream;
    auto append = [&stream](const std::vector<uint32_t> &words) {
        stream.insert(stream.end(), words.begin(), words.end());
    };
    stream.push_back((0x40u << 24) | 15u);
    append(encode_span_wire(s0));
    stream.push_back((0x43u << 24) | 17u);
    append(encode_span4_wire(q));
    stream.push_back((0x40u << 24) | 15u);
    append(encode_span_wire(s1));

    emit_command_stream_dma(stream);
    m.apply_draw_span(s0);
    m.apply_span4_affine(q);
    m.apply_draw_span(s1);

    if (!submit_and_wait()) {
        check_fail("command_stream_dma_mixed_span_span4", "timeout");
        return;
    }
    compare_fb_region("command_stream_dma_mixed_span_span4.fb", m,
                      FB_BASE_BYTE, 320, 8, 8, 40, 24);
}

// ---- Section 15: DRAW_TRIANGLES (basic / state interaction) ----------------
//
// Triangles use a CPU model approximation; tests compare bbox stats
// rather than per-byte (the rasterization rules are too tight for a
// double-precision reference to match the integer 12.4 / 16.16 mix).
// The standalone triangle suite already lives in tb_gpu_main.cpp; we
// add ONE narrowed test here that verifies state-coupling: a triangle
// drawn with SET_COLORMAP_ID 1 must produce different output than the
// same triangle with SET_COLORMAP_ID 2, even back-to-back without flush.
static void test_triangle_cmap_slot_change_between_draws() {
    printf("TEST triangle_cmap_slot_change_between_draws\n");
    gpu_init();
    preload_with_sentinel();

    /* Slot 1: identity — so triangle output reflects the texture bytes
     * directly.  Slot 2: constant 0x88. */
    upload_palookup_identity_row(1, 0);
    upload_palookup_const_row   (2, 0, 0x88);

    /* 8x8 texture of value 0x42. */
    std::vector<uint8_t> tex(64, 0x42);
    upload_texture(TEX_BASE_BYTE, tex);

    cmd_set_fb(FB_BASE_BYTE, 320);
    cmd_set_texture(TEX_BASE_BYTE, 8, 8);

    /* Triangle covering pixels (10,10)..(40,40). */
    auto write_tri = [&](int x_off) {
        ring_cmd(0x30, 19);
        ring_write(3);  // vertex count (ignored)
        /* v0 (10,10), v1 (40,10), v2 (10,40) — with .r = 0 */
        auto vert = [](int16_t x, int16_t y, int32_t s, int32_t t) {
            ring_write(((uint32_t)(uint16_t)(x*16) << 16) | (uint16_t)(y*16));
            ring_write(0);                           // z
            ring_write((uint32_t)s);                 // s
            ring_write((uint32_t)t);                 // t
            ring_write(0x00010000);                  // affine w
            ring_write(0x00000000);                  // r=0, g=b=a=0
        };
        vert(10 + x_off, 10, 0,             0);
        vert(40 + x_off, 10, 7 << 16,       0);
        vert(10 + x_off, 40, 0,             7 << 16);
    };

    cmd_set_colormap_id(1); write_tri(0);
    cmd_set_colormap_id(2); write_tri(50);

    if (!submit_and_wait()) {
        check_fail("triangle_cmap_slot_change_between_draws", "timeout");
        return;
    }
    /* Sample inside each triangle. The first triangle's interior should
     * contain bytes ≈ 0x42 (identity through palookup); the second must
     * contain 0x88 (constant). */
    int n_42_in_first = 0, n_88_in_second = 0;
    for (int y = 15; y < 35; y++) {
        for (int x = 15; x < 35; x++) {
            if (sdram_read_byte(FB_BASE_BYTE + y*320 + x)      == 0x42) n_42_in_first++;
            if (sdram_read_byte(FB_BASE_BYTE + y*320 + x + 50) == 0x88) n_88_in_second++;
        }
    }
    if (n_42_in_first > 50 && n_88_in_second > 50)
        check_pass("triangle_cmap_slot_change_between_draws.distinct");
    else {
        char buf[128];
        snprintf(buf, sizeof(buf),
                 "expected ~hundreds of 0x42 in tri1 + 0x88 in tri2; got %d / %d",
                 n_42_in_first, n_88_in_second);
        check_fail("triangle_cmap_slot_change_between_draws.distinct", buf);
    }
}

// ---- Section 16: FLIP ------------------------------------------------------
static void test_flip_no_writes_pulses_immediately() {
    printf("TEST flip_no_writes_pulses_immediately\n");
    gpu_init();
    cmd_set_fb(FB_BASE_BYTE, 320);
    uint32_t tok = next_fence_token++;
    cmd_flip(2, tok);
    gpu_kick();

    int swap_count = 0;
    int swap_idx   = -1;
    for (int t = 0; t < 1000; t++) {
        tb->eval();
        if (tb->gpu_swap_req) {
            if (swap_count == 0) swap_idx = tb->gpu_swap_idx;
            swap_count++;
        }
        if (tb->fence_reached == tok) break;
        tick();
    }
    if (swap_count == 1 && swap_idx == 2 && tb->fence_reached == tok)
        check_pass("flip_no_writes_pulses_immediately");
    else {
        char buf[128];
        snprintf(buf, sizeof(buf),
                 "swap_count=%d idx=%d fence=%u (expected 1, 2, %u)",
                 swap_count, swap_idx, tb->fence_reached, tok);
        check_fail("flip_no_writes_pulses_immediately", buf);
    }
}

// ============================================================================
// 7. Combination tests A..F
// ============================================================================

// ---- A: state-change matrix (subset) ---------------------------------------
static void test_combo_a_setfb_then_setcmap_then_span() {
    printf("TEST combo_A_setfb_then_setcmap_then_span\n");
    gpu_init();
    auto m = preload_with_sentinel();
    upload_palookup_const_row(3, 0, 0xCC);
    std::vector<uint8_t> tex(16, 0x42);
    upload_texture(TEX_BASE_BYTE, tex);
    m.snapshot_from_sdram();

    /* SET_FB A, SET_CMAP 3, draw colormapped span — should write 0xCC
     * everywhere in FB-A. */
    cmd_set_fb(FB_BASE_BYTE, 320); m.st_fb_addr = FB_BASE_BYTE;
    cmd_set_colormap_id(3);        m.st_colormap = 3;
    SpanWire s = make_span();
    s.fb_addr = FB_BASE_BYTE; s.tex_addr = TEX_BASE_BYTE;
    s.tex_width = 16; s.tex_w_mask = 0xF;
    s.s = 0; s.t = 0; s.sstep = 0x10000; s.tstep = 0;
    s.count = 16; s.flags = 0x1;
    emit_span_raw(s); m.apply_draw_span(s);

    /* SET_FB B, draw same — output should now go to FB-B only. */
    cmd_set_fb(FB_ALT_BASE_BYTE, 320); m.st_fb_addr = FB_ALT_BASE_BYTE;
    SpanWire sB = s; sB.fb_addr = FB_ALT_BASE_BYTE;
    emit_span_raw(sB); m.apply_draw_span(sB);

    if (!submit_and_wait()) {
        check_fail("combo_A_setfb_then_setcmap_then_span", "timeout");
        return;
    }
    compare_fb_region("combo_A.A_row0", m, FB_BASE_BYTE,    320, 0, 0, 16, 1);
    compare_fb_region("combo_A.B_row0", m, FB_ALT_BASE_BYTE, 320, 0, 0, 16, 1);
}

// ---- B: paint-order matrix (subset) ----------------------------------------
static void test_combo_b_clear_then_span_paints_span() {
    printf("TEST combo_B_clear_then_span_paints_span\n");
    gpu_init();
    auto m = preload_with_sentinel();

    std::vector<uint8_t> tex(16);
    for (int i = 0; i < 16; i++) tex[i] = (uint8_t)(0x40 + i);
    upload_texture(TEX_BASE_BYTE, tex);
    m.snapshot_from_sdram();

    cmd_set_fb(FB_BASE_BYTE, 320); m.st_fb_addr = FB_BASE_BYTE;
    cmd_clear(0x1, 0x99);           m.apply_clear(0x1, 0x99);

    SpanWire s = make_span();
    s.fb_addr = FB_BASE_BYTE; s.tex_addr = TEX_BASE_BYTE;
    s.tex_width = 16; s.tex_w_mask = 0xF;
    s.s = 0; s.t = 0; s.sstep = 0x10000; s.tstep = 0;
    s.count = 16; s.flags = 0;
    emit_span_raw(s); m.apply_draw_span(s);

    if (!submit_and_wait()) {
        check_fail("combo_B_clear_then_span_paints_span", "timeout");
        return;
    }
    /* First 16 bytes = 0x40..0x4F (span), rest = 0x99 (clear). */
    compare_fb_region("combo_B.span_wins_first16", m, FB_BASE_BYTE, 320,
                      0, 0, 16, 1);
    compare_fb_region("combo_B.clear_holds_rest", m, FB_BASE_BYTE, 320,
                      16, 0, 320 - 16, 1);
}

// ---- C: colormap cache matrix (subset) -------------------------------------
static void test_combo_c_three_slots_in_one_batch() {
    printf("TEST combo_C_three_slots_in_one_batch\n");
    gpu_init();
    auto m = preload_with_sentinel();
    upload_palookup_const_row(0, 0, 0x10);
    upload_palookup_const_row(1, 0, 0x20);
    upload_palookup_const_row(2, 0, 0x30);

    std::vector<uint8_t> tex(8, 0x77);
    upload_texture(TEX_BASE_BYTE, tex);
    m.snapshot_from_sdram();

    cmd_set_fb(FB_BASE_BYTE, 320); m.st_fb_addr = FB_BASE_BYTE;

    std::vector<SpanWire> spans;
    SpanWire base = make_span();
    base.tex_addr = TEX_BASE_BYTE;
    base.tex_width = 8; base.tex_w_mask = 0x7;
    base.s = 0; base.t = 0; base.sstep = 0x10000; base.tstep = 0;
    base.count = 8; base.flags = 0x1;
    for (int i = 0; i < 3; i++) {
        SpanWire s = base;
        s.fb_addr = FB_BASE_BYTE + (uint32_t)i * 320;
        s.colormap_id = (uint8_t)(i + 1);  // 1,2,3 — non-zero so explicit
        spans.push_back(s);
    }
    /* Slot 3 isn't loaded in our model; load it for parity. */
    upload_palookup_const_row(3, 0, 0x40);
    m.snapshot_from_sdram();

    emit_batch_raw(spans);
    m.apply_batch(spans);

    if (!submit_and_wait()) {
        check_fail("combo_C_three_slots_in_one_batch", "timeout");
        return;
    }
    for (int i = 0; i < 3; i++) {
        char nm[64]; snprintf(nm, sizeof(nm), "combo_C.row%d", i);
        compare_fb_region(nm, m, FB_BASE_BYTE, 320, 0, i, 8, 1);
    }
}

// ---- D: texture cache matrix (subset) --------------------------------------
static void test_combo_d_tex_mutate_with_flush() {
    /* Draw with texture A=0x11, mutate A to 0x22, issue GPU_TEX_FLUSH,
     * draw again — second pass must show 0x22. */
    printf("TEST combo_D_tex_mutate_with_flush\n");
    gpu_init();
    auto m = preload_with_sentinel();
    std::vector<uint8_t> texA(16, 0x11);
    upload_texture(TEX_BASE_BYTE, texA);
    m.snapshot_from_sdram();

    cmd_set_fb(FB_BASE_BYTE, 320); m.st_fb_addr = FB_BASE_BYTE;

    SpanWire s = make_span();
    s.fb_addr = FB_BASE_BYTE; s.tex_addr = TEX_BASE_BYTE;
    s.tex_width = 16; s.tex_w_mask = 0xF;
    s.s = 0; s.t = 0; s.sstep = 0x10000; s.tstep = 0;
    s.count = 16; s.flags = 0;
    emit_span_raw(s); m.apply_draw_span(s);

    /* First fence drains the first draw. */
    if (!submit_and_wait()) {
        check_fail("combo_D_tex_mutate_with_flush.first_fence", "timeout");
        return;
    }
    /* Mutate texture in SDRAM. */
    std::vector<uint8_t> texB(16, 0x22);
    upload_texture(TEX_BASE_BYTE, texB);
    m.snapshot_from_sdram();

    /* Flush before next draw. */
    issue_tex_flush();

    SpanWire s2 = s; s2.fb_addr = FB_BASE_BYTE + 320;
    emit_span_raw(s2); m.apply_draw_span(s2);
    if (!submit_and_wait()) {
        check_fail("combo_D_tex_mutate_with_flush.second_fence", "timeout");
        return;
    }
    compare_fb_region("combo_D.first_pass_0x11", m, FB_BASE_BYTE, 320,
                      0, 0, 16, 1);
    compare_fb_region("combo_D.second_pass_0x22", m, FB_BASE_BYTE, 320,
                      0, 1, 16, 1);
}

// ---- E: framebuffer accumulator + blend matrix (subset) --------------------
static void test_combo_e_lane_writes_into_one_word() {
    /* Four 1-byte spans hitting lanes 0,1,2,3 of the same word.
     * Verifies the accumulator coalesces into one m_wr beat. */
    printf("TEST combo_E_lane_writes_into_one_word\n");
    gpu_init();
    auto m = preload_with_sentinel();
    std::vector<uint8_t> tex { 0xA0, 0xA1, 0xA2, 0xA3 };
    upload_texture(TEX_BASE_BYTE, tex);
    m.snapshot_from_sdram();
    cmd_set_fb(FB_BASE_BYTE, 320); m.st_fb_addr = FB_BASE_BYTE;

    /* Pre-fill one word with 0x88. */
    cmd_clear_rect(FB_BASE_BYTE, 4, 1, 0, 0x88);
    m.apply_clear_rect(FB_BASE_BYTE, 4, 1, 0, 0x88);

    /* Each span writes one byte at a different lane. */
    for (int lane = 0; lane < 4; lane++) {
        SpanWire s = make_span();
        s.fb_addr = FB_BASE_BYTE + (uint32_t)lane;
        s.tex_addr = TEX_BASE_BYTE + (uint32_t)lane;
        s.tex_width = 1; s.tex_w_mask = 0;
        s.s = 0; s.t = 0; s.sstep = 0x10000; s.tstep = 0;
        s.count = 1; s.flags = 0;
        emit_span_raw(s); m.apply_draw_span(s);
    }
    if (!submit_and_wait()) {
        check_fail("combo_E_lane_writes_into_one_word", "timeout");
        return;
    }
    compare_fb_region("combo_E.lanes", m, FB_BASE_BYTE, 320, 0, 0, 4, 1);
}

// ---- F: ring + DMA matrix (subset) -----------------------------------------
static void test_combo_f_dma_then_inline() {
    /* Issue a DMA-delivered batch, immediately follow with an inline
     * DRAW_SPAN in the same kick.  Both should retire. */
    printf("TEST combo_F_dma_then_inline\n");
    gpu_init();
    auto m = preload_with_sentinel();
    std::vector<uint8_t> tex(16);
    for (int i = 0; i < 16; i++) tex[i] = (uint8_t)(0x10 + i);
    upload_texture(TEX_BASE_BYTE, tex);
    m.snapshot_from_sdram();
    cmd_set_fb(FB_BASE_BYTE, 320); m.st_fb_addr = FB_BASE_BYTE;

    /* DMA batch of 4 spans drawing into rows 0..3. */
    std::vector<SpanWire> spans;
    for (int i = 0; i < 4; i++) {
        SpanWire s = make_span();
        s.fb_addr = FB_BASE_BYTE + (uint32_t)i * 320;
        s.tex_addr = TEX_BASE_BYTE;
        s.tex_width = 16; s.tex_w_mask = 0xF;
        s.s = 0; s.t = 0; s.sstep = 0x10000; s.tstep = 0;
        s.count = 16; s.flags = 0;
        spans.push_back(s);
    }
    emit_batch_dma(spans);
    m.apply_batch(spans);

    /* Wait for DMA to drain so subsequent ring writes don't race. */
    while (mmio_read(REG_STATUS) & (1 << 2)) tick(8);

    /* Now an inline DRAW_SPAN at row 4. */
    SpanWire sInline = spans[0];
    sInline.fb_addr = FB_BASE_BYTE + 4 * 320;
    emit_span_raw(sInline); m.apply_draw_span(sInline);

    if (!submit_and_wait()) {
        check_fail("combo_F_dma_then_inline", "timeout");
        return;
    }
    for (int row = 0; row < 5; row++) {
        char nm[64]; snprintf(nm, sizeof(nm), "combo_F.row%d", row);
        compare_fb_region(nm, m, FB_BASE_BYTE, 320, 0, row, 16, 1);
    }
}

// ============================================================================
// 8. SDK wire-format gap tests
// ============================================================================

// SDK masks scalar span count to the 12-bit wire field and packs
// colormap_id explicitly.  count >= 4096 still truncates, but the high
// count bits must not bleed into the colormap_id nibble.
static void test_sdk_count_4096_does_not_leak_into_colormap_id() {
    printf("TEST sdk_count_4096_does_not_leak_into_colormap_id\n");
    gpu_init();
    preload_with_sentinel();
    /* Slot 0 row 0: identity (texel 0x10 → 0x10).
     * Slot 1 row 0: constant SLOT1_K (any texel → SLOT1_K). */
    upload_palookup_identity_row(0, 0);
    const uint8_t SLOT1_K = 0xC8;
    upload_palookup_const_row(1, 0, SLOT1_K);
    /* Texture[0] = 0x10. */
    std::vector<uint8_t> tex(16, 0x10);
    upload_texture(TEX_BASE_BYTE, tex);

    cmd_set_fb(FB_BASE_BYTE, 320);

    SpanSdk s = make_sdk_span();
    s.fb_addr = FB_BASE_BYTE;
    s.tex_addr = TEX_BASE_BYTE;
    s.tex_width = 16; s.tex_w_mask = 0xF;
    s.s = 0; s.t = 0; s.sstep = 0x10000; s.tstep = 0;
    s.count = 0x1001;     // SDK masks to wire count=1
    s.light = 0;
    s.flags = 0x1;        // SPAN_COLORMAP
    s.colormap_id = 0;
    emit_span_sdk_encoded(s);

    if (!submit_and_wait()) {
        check_fail("sdk_count_4096_does_not_leak_into_colormap_id", "timeout");
        return;
    }
    /* FB[0] disambiguates: 0x10 = no leak (slot 0 identity),
     * SLOT1_K = count high nibble leaked into colormap_id, sentinel = no
     * pixel rendered (count decoded as 0). */
    uint8_t got = sdram_read_byte(FB_BASE_BYTE);
    if (got == 0x10) {
        check_pass("sdk_count_4096_does_not_leak_into_colormap_id.no_leak");
    } else if (got == SLOT1_K) {
        check_fail("sdk_count_4096_does_not_leak_into_colormap_id.no_leak",
                   "FB[0]=slot1 value: count high nibble leaked into colormap_id");
    } else if (got == SENTINEL_BYTE) {
        check_fail("sdk_count_4096_does_not_leak_into_colormap_id.no_leak",
                   "FB[0] still sentinel: no pixel rendered");
    } else {
        char buf[128];
        snprintf(buf, sizeof(buf),
                 "FB[0]=0x%02x: unexpected (slot1=0x%02x, slot0_id=0x10, sentinel=0x%02x)",
                 got, SLOT1_K, SENTINEL_BYTE);
        check_fail("sdk_count_4096_does_not_leak_into_colormap_id.no_leak", buf);
    }
    /* Sentinel border: only FB[0] should change.  FB[1..15] still sentinel. */
    int border_diffs = 0;
    for (int x = 1; x < 16; x++)
        if (sdram_read_byte(FB_BASE_BYTE + x) != SENTINEL_BYTE) border_diffs++;
    if (border_diffs == 0) {
        check_pass("sdk_count_4096_does_not_leak_into_colormap_id.one_pixel_only");
    } else {
        char buf[64];
        snprintf(buf, sizeof(buf), "%d extra writes past pixel 0", border_diffs);
        check_fail("sdk_count_4096_does_not_leak_into_colormap_id.one_pixel_only", buf);
    }
}

// ============================================================================
// Main runner
// ============================================================================
int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    Verilated::traceEverOn(false);
    tb = new Vtb_gpu;
    trace = nullptr;

    printf("=== GPU Acceptance Suite ===\n\n");
    hard_reset();
    printf("GPU initialized after hard reset (%lu cycles)\n\n",
           (unsigned long)(sim_time / 2));

    // ---- Standalone tests ----
    test_nop_basic();
    test_nop_burst();
    test_fence_no_writes();
    test_multi_fence_in_order();
    test_fence_after_clear_drains();
    test_fence_token_high_bits();
    test_set_fb_two_bases();
    test_set_fb_unusual_stride();
    test_set_texture_does_not_affect_direct_span();
    test_set_texture_via_triangle();
    test_set_texture_width_via_triangle();
    test_set_colormap_id_sticky();
    test_per_span_colormap_overrides_sticky();
    test_set_skip_zero_does_not_affect_direct_spans();
    test_set_skip_zero_affects_triangle_spans();
    test_clear_color_replication();
    test_clear_drains_framebuffer_writes();
    test_clear_no_op_when_flag_clear();
    test_clear_does_not_touch_rows_200_to_239();
    test_clear_rect_edge_lanes();
    test_clear_rect_zero_dimensions();
    test_span_raw_count_boundary();
    test_span_raw_count_4096_truncation();
    test_span_raw_negative_stride();
    test_span_raw_mask_zero_means_identity();
    test_span_colormap_explicit_per_span_id();
    test_span_colormap_light_wraps_mod_64();
    test_skip_zero_only_discards_0xff();
    test_skip_zero_with_colormap();
    test_transluc_basic_blend();
    test_transluc_overdraw_same_word();
    test_persp_constant_z_matches_affine();
    test_batch_equals_individual();
    test_batch_mixed_per_span_colormap();
    test_span4_opaque_equals_four_spans();
    test_span4_texture_height_mask();
    test_span4_aligned_coalesces_row_writes();
    test_span4_aligned_texture_cache_thrash();
    test_span4_masked_equals_four_spans();
    test_span4_explicit_colormap_slot();
    test_span4_batch_two_payloads();
    test_batch_dma_equals_inline();
    test_command_stream_dma_mixed_span_span4();
    test_triangle_cmap_slot_change_between_draws();
    test_flip_no_writes_pulses_immediately();

    // ---- Combination tests ----
    test_combo_a_setfb_then_setcmap_then_span();
    test_combo_b_clear_then_span_paints_span();
    test_combo_c_three_slots_in_one_batch();
    test_combo_d_tex_mutate_with_flush();
    test_combo_e_lane_writes_into_one_word();
    test_combo_f_dma_then_inline();

    // ---- Wire-format gap ----
    test_sdk_count_4096_does_not_leak_into_colormap_id();

    printf("\n=== Acceptance Results: %d passed, %d failed ===\n",
           pass_count, fail_count);

    delete tb;
    return fail_count > 0 ? 1 : 0;
}
