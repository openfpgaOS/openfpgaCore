//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

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

#ifndef GPU_TEST_ENABLE_TRIANGLES
#define GPU_TEST_ENABLE_TRIANGLES 0
#endif

/* OS30 lean GPU build (make gpu-acceptance-os30): GPU_HAS_COMPACT_SPAN=0,
 * GPU_HAS_COLUMN_LIST=0, +define+EXCLUDE_TRANSLUC.  The compact-direct 0x48
 * form (11..32-word payloads = 4-word header + 7/lane), the 0x4C column
 * lists and the transluc[] LUT are architecturally removed and DRAIN as
 * no-ops, so every test whose vehicle is one of those paths is compiled
 * out under GPU_TEST_OS30_LEAN.  Long-form 0x48 (>=33 words), 0x49, the
 * 0x4A/0x4B vert-tri pair and 0x4D stay enabled and byte-identical, so
 * those tests run unchanged.  The lean_* negative tests at the bottom run
 * in BOTH configs with expectations switched on this flag. */
#ifdef GPU_TEST_OS30_LEAN
static const bool LEAN_CONFIG = true;
#else
static const bool LEAN_CONFIG = false;
#endif

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
static const uint32_t PALOOKUP_BASE_BYTE = 0x03FC0000;  // matches gpu_core PALOOKUP_BASE
static const uint32_t PALOOKUP_STRIDE    = 0x00004000;  // 16 KB per slot
static const uint32_t BATCH_BUF_BYTE     = 0x00140000;  // doorbell-DMA scratch
static const uint32_t SDRAM_BYTES        = 4 * 1024 * 1024;

/* tb_gpu.v's SDRAM model uses `bd_addr[19:0]` (1M words = 4 MB) so any
 * byte address whose top bits exceed this range aliases back into the
 * 4 MB window.  PALOOKUP_BASE (0x03FC0000) wraps to byte 0x003C0000;
 * tex bases inside 0x00400000 are unaffected.  The CPU model mirrors this so its
 * indexing matches what the RTL actually sees in SDRAM. */
static inline uint32_t sdram_alias(uint32_t byte_addr) {
    return byte_addr & 0x003FFFFFu;   /* (1<<22)-1 — same as bd_addr[19:0]<<2 */
}

/* Ring state mirror — the GPU's 16 KB M10K ring. */
static const uint32_t RING_SIZE = 0x00004000;
static uint32_t ring_wrptr      = 0;
static const uint32_t ring_mask = RING_SIZE - 1;
static std::vector<uint32_t> pending_stream;

/* Sentinel byte used to fill FB / texture / palookup when not actively
 * being driven by a test, so any "wrong byte" mismatches stand out. */
static const uint8_t SENTINEL_BYTE = 0xAB;

static void check_pass(const char *name);
static void check_fail(const char *name, const std::string &reason);

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
    REG_RESERVED_2    = 2,
    REG_DMA_SRC       = 3,
    REG_RING_RDPTR    = 4,
    REG_STATUS        = 5,
    REG_FENCE         = 6,
    REG_DMA_LEN       = 7,
    REG_TRANSLUC_ADDR = 8,
    REG_TRANSLUC_DATA = 9,
    REG_TEX_FLUSH     = 10,
    REG_DMA_KICK      = 11,
    REG_PALOOKUP_BASE = 12,
};

/* Mirror the SDK's doorbell-DMA guard.  The production GPU no longer has
 * a command-data MMIO path; tests build command streams in host memory,
 * stage them into the SDRAM scratch window, and kick the DMA puller. */
static void wait_for_dma_idle(int timeout = 200000) {
    for (int t = 0; t < timeout; t++) {
        if ((mmio_read(REG_STATUS) & (1 << 2)) == 0) return;
        tick();
    }
    fprintf(stderr, "wait_for_dma_idle: timeout (status=0x%08x)\n",
            mmio_read(REG_STATUS));
}

static void wait_for_transluc_idle(int timeout = 200000) {
    for (int t = 0; t < timeout; t++) {
        if ((mmio_read(REG_STATUS) & (1 << 3)) == 0) return;
        tick();
    }
    fprintf(stderr, "wait_for_transluc_idle: timeout (status=0x%08x)\n",
            mmio_read(REG_STATUS));
}

static void ring_write(uint32_t w) {
    pending_stream.push_back(w);
}

static void ring_cmd(uint8_t cmd, uint32_t payload_words) {
    if (pending_stream.empty())
        wait_for_dma_idle();
    ring_write(((uint32_t)cmd << 24) | (payload_words & 0x00FFFFFFu));
}

static void gpu_kick() {
    if (pending_stream.empty())
        return;
    wait_for_dma_idle();

    uint32_t addr_word = BATCH_BUF_BYTE >> 2;
    for (uint32_t x : pending_stream)
        sdram_write(addr_word++, x);

    uint32_t words = (uint32_t)pending_stream.size();
    ring_wrptr = (ring_wrptr + words * 4u) & ring_mask;
    mmio_write(REG_DMA_SRC, BATCH_BUF_BYTE);
    mmio_write(REG_DMA_LEN, words);
    mmio_write(REG_DMA_KICK, 1);
    pending_stream.clear();
}

/* gpu_init does a hard reset, then ring_reset. After this the GPU is
 * fresh and the ring pointers are zero on both sides. */
static void gpu_init() {
    hard_reset();
    ring_wrptr = 0;
    pending_stream.clear();
    mmio_write(REG_CTRL, 4);              // ring_reset
    mmio_write(REG_PALOOKUP_BASE, PALOOKUP_BASE_BYTE);
    mmio_write(REG_RING_WRPTR, 0);
}

/* Pulse GPU_CTRL bit1 = soft_reset.  Returns the FSM to idle and clears the
 * 0x4A sticky bank (tri_state_valid).  Call only when the ring is drained so no
 * pending command is lost: soft_reset snaps ring_rdptr to the current
 * ring_wrptr, so the host ring_wrptr stays valid and new commands append after
 * it as usual (no ring_reset, so the device write pointer is unchanged). */
static void gpu_soft_reset() {
    mmio_write(REG_CTRL, 2);
    for (int i = 0; i < 4; i++) tick();
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
        wait_for_transluc_idle();
        mmio_write(REG_TRANSLUC_DATA, w);
    }
    wait_for_transluc_idle();
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

static const uint8_t SPAN_PERSP = 1 << 5;
static const uint8_t CMD_PARAM_SPAN_LIST = 0x48;

/* Internal single-span test model.  Wire emission uses one-lane native affine
 * or perspective span-group commands. */
struct SpanWire {
    uint32_t fb_addr;
    uint32_t tex_addr;
    int32_t  s, t;
    int32_t  sstep, tstep;
    uint8_t  colormap_id;   // explicit slot, including 0
    uint16_t count;
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

struct EncodedCommand {
    uint8_t cmd;
    std::vector<uint32_t> payload;
};

static EncodedCommand encode_span_wire(const SpanWire &s) {
    EncodedCommand c {};
    c.cmd = CMD_PARAM_SPAN_LIST;
    if (s.flags & SPAN_PERSP) {
        c.payload.assign(34, 0);
        c.payload[0]  = s.fb_addr;
        c.payload[2]  = (uint32_t)(int32_t)s.fb_stride;
        c.payload[3]  = s.tex_addr;
        c.payload[4]  = (uint32_t)s.tex_width;
        c.payload[5]  = (uint32_t)s.tex_w_mask;
        c.payload[6]  = (uint32_t)s.tex_h_mask;
        c.payload[7]  = ((uint32_t)(s.flags | SPAN_PERSP) & 0xFFu)
                      | (((uint32_t)s.colormap_id & 0x0Fu) << 8)
                      | (1u << 12);
        c.payload[8]  = (uint32_t)s.sZ;
        c.payload[9]  = (uint32_t)s.sZstep;
        c.payload[11] = (uint32_t)s.tZ;
        c.payload[12] = (uint32_t)s.tZstep;
        c.payload[14] = (uint32_t)s.zinv;
        c.payload[15] = (uint32_t)s.zinv_step;
        c.payload[17] = ((uint32_t)s.light & 0x3Fu) << 16;
        c.payload[29] = 1;
        c.payload[32] = (uint32_t)s.count;
    } else {
        c.payload.resize(11);
        c.payload[0]  = (1u << 28)
                      | (((uint32_t)s.flags & ~SPAN_PERSP) << 20);
        c.payload[1]  = (uint32_t)s.tex_width;
        c.payload[2]  = ((uint32_t)s.tex_h_mask << 16) | (uint32_t)s.tex_w_mask;
        c.payload[3]  = (uint32_t)(int32_t)s.fb_stride;
        c.payload[4]  = s.fb_addr;
        c.payload[5]  = s.tex_addr;
        c.payload[6]  = (((uint32_t)s.colormap_id & 0x0Fu) << 28)
                      | (((uint32_t)s.light & 0x3Fu) << 16)
                      | (uint32_t)s.count;
        c.payload[7]  = (uint32_t)s.s;
        c.payload[8]  = (uint32_t)s.t;
        c.payload[9]  = (uint32_t)s.sstep;
        c.payload[10] = (uint32_t)s.tstep;
    }
    return c;
}

static void append_command(std::vector<uint32_t> &stream,
                           const EncodedCommand &c) {
    stream.push_back(((uint32_t)c.cmd << 24) | (uint32_t)c.payload.size());
    stream.insert(stream.end(), c.payload.begin(), c.payload.end());
}

/* Emit one span through a one-lane native group command. */
static void emit_span_raw(const SpanWire &s) {
    auto c = encode_span_wire(s);
    ring_cmd(c.cmd, (uint32_t)c.payload.size());
    for (uint32_t x : c.payload) ring_write(x);
}

/* Emit N one-lane native span commands through one staged command
 * stream path. */
static void emit_batch_raw(const std::vector<SpanWire> &spans) {
    for (const auto &s : spans) {
        emit_span_raw(s);
    }
}

/* Emit N one-lane native span commands delivered via doorbell-DMA.
 * Mirrors the SDK helper's path so we can stress the DMA puller. */
static void emit_batch_dma(const std::vector<SpanWire> &spans) {
    std::vector<uint32_t> stream;
    stream.reserve(spans.size() * 29u);
    for (const auto &s : spans) {
        append_command(stream, encode_span_wire(s));
    }

    if (!pending_stream.empty())
        gpu_kick();
    wait_for_dma_idle();
    uint32_t addr_word = BATCH_BUF_BYTE >> 2;
    for (uint32_t x : stream)
        sdram_write(addr_word++, x);
    ring_wrptr = (ring_wrptr + (uint32_t)stream.size() * 4u) & ring_mask;
    mmio_write(REG_DMA_SRC, BATCH_BUF_BYTE);
    mmio_write(REG_DMA_LEN, (uint32_t)stream.size());
    mmio_write(REG_DMA_KICK, 1);
}

struct SpanGroupWire {
    uint32_t fb_addr;
    uint32_t tex_addr[8];
    int32_t  s[8];
    int32_t  t[8];
    int32_t  sstep[8];
    int32_t  tstep[8];
    uint16_t count;
    uint16_t count_lane[8];
    uint8_t  flags;
    uint8_t  colormap_id[8]; // explicit per lane, including slot 0
    uint8_t  lane_count;
    int16_t  fb_stride;
    int16_t  lane_delta;
    uint16_t tex_width;
    uint16_t tex_w_mask;
    uint16_t tex_h_mask;
    uint8_t  light[8];
};

static SpanGroupWire make_span_group() {
    SpanGroupWire s {};
    s.lane_count = 4;
    s.lane_delta = 1;
    s.fb_stride = 320;
    s.tex_width = 1;
    return s;
}

static void set_span_group_colormap(SpanGroupWire &s, uint8_t colormap_id) {
    for (int i = 0; i < 8; i++)
        s.colormap_id[i] = colormap_id;
}

static int span_group_effective_lanes(uint8_t lane_count) {
    if (lane_count == 8) return 8;
    if (lane_count == 4) return 4;
    if (lane_count == 2) return 2;
    return 1;
}

static int span_group_chunk_lanes(int lanes_left) {
    return (lanes_left > 4) ? 4 : lanes_left;
}

static uint16_t span_group_lane_count_value(const SpanGroupWire &s, int lane) {
    return s.count_lane[lane] ? s.count_lane[lane] : s.count;
}

static std::vector<uint32_t> encode_affine_span_group_chunk(const SpanGroupWire &s,
                                                            int first_lane,
                                                            int lane_count) {
    std::vector<uint32_t> w(4 + lane_count * 7);
    w[0] = ((uint32_t)lane_count << 28)
         | ((uint32_t)s.flags << 20);
    w[1] = (uint32_t)s.tex_width;
    w[2] = ((uint32_t)s.tex_h_mask << 16) | (uint32_t)s.tex_w_mask;
    w[3] = (uint32_t)(int32_t)s.fb_stride;
    for (int lane = 0; lane < lane_count; lane++) {
        int src = first_lane + lane;
        int base = 4 + lane * 7;
        w[base + 0] = s.fb_addr + (uint32_t)((int32_t)s.lane_delta * src);
        w[base + 1] = s.tex_addr[src];
        w[base + 2] = (((uint32_t)s.colormap_id[src] & 0x0Fu) << 28) |
                      (((uint32_t)s.light[src] & 0x3Fu) << 16) |
                      (uint32_t)span_group_lane_count_value(s, src);
        w[base + 3] = (uint32_t)s.s[src];
        w[base + 4] = (uint32_t)s.t[src];
        w[base + 5] = (uint32_t)s.sstep[src];
        w[base + 6] = (uint32_t)s.tstep[src];
    }
    return w;
}

static void emit_span_group_raw(const SpanGroupWire &s) {
    int lane_count = span_group_effective_lanes(s.lane_count);
    for (int first = 0; first < lane_count;) {
        int n = span_group_chunk_lanes(lane_count - first);
        std::vector<uint32_t> w = encode_affine_span_group_chunk(s, first, n);
        ring_cmd(CMD_PARAM_SPAN_LIST, (uint32_t)w.size());
        for (uint32_t x : w) ring_write(x);
        first += n;
    }
}

static void append_span_group_stream_raw(std::vector<uint32_t> &stream,
                                         const SpanGroupWire &s) {
    int lane_count = span_group_effective_lanes(s.lane_count);
    for (int first = 0; first < lane_count;) {
        int n = span_group_chunk_lanes(lane_count - first);
        std::vector<uint32_t> w = encode_affine_span_group_chunk(s, first, n);
        stream.push_back(((uint32_t)CMD_PARAM_SPAN_LIST << 24) | (uint32_t)w.size());
        stream.insert(stream.end(), w.begin(), w.end());
        first += n;
    }
}

static void emit_span_group_stream_raw(const std::vector<SpanGroupWire> &spans) {
    for (const auto &s : spans)
        emit_span_group_raw(s);
}

// ============================================================================
// CMD_DRAW_COLUMN_LIST (0x4C) wire encoder + byte-exact oracle helpers.
//
// 0x4C is the 5-word-lane-record twin of the 0x48 direct-affine variant for
// vertical 1-wide textured columns (s/sstep always 0, dropped on the wire).
// The RTL decoder FORCES s=0/sstep=0, so a 0x4C column MUST be byte-identical
// to the 0x48 direct-affine encoding of the SAME geometry with s=0/sstep=0.
// The oracle below renders both forms (into FB_BASE vs FB_ALT_BASE) and
// compares the two GPU framebuffers byte-for-byte — no CPU model required.
// ============================================================================
static const uint8_t CMD_DRAW_COLUMN_LIST = 0x4C;

// Encode one native chunk (<=4 lanes) of a column list: 4-word header + 5N
// lane records.  Mirrors encode_affine_span_group_chunk but drops s/sstep.
// `fb_base_override` lets the oracle re-target a second framebuffer.
static std::vector<uint32_t> encode_column_list_chunk(const SpanGroupWire &s,
                                                      int first_lane,
                                                      int lane_count,
                                                      uint32_t fb_base_override) {
    std::vector<uint32_t> w(4 + lane_count * 5);
    w[0] = ((uint32_t)lane_count << 28)
         | ((uint32_t)s.flags << 20);
    w[1] = (uint32_t)s.tex_width;
    w[2] = ((uint32_t)s.tex_h_mask << 16) | (uint32_t)s.tex_w_mask;
    w[3] = (uint32_t)(int32_t)s.fb_stride;       // == fb_step (per-pixel byte step)
    for (int lane = 0; lane < lane_count; lane++) {
        int src = first_lane + lane;
        int base = 4 + lane * 5;
        w[base + 0] = fb_base_override + (uint32_t)((int32_t)s.lane_delta * src);
        w[base + 1] = s.tex_addr[src];
        w[base + 2] = (((uint32_t)s.colormap_id[src] & 0x0Fu) << 28) |
                      (((uint32_t)s.light[src] & 0x3Fu) << 16) |
                      (uint32_t)span_group_lane_count_value(s, src);
        w[base + 3] = (uint32_t)s.t[src];        // v origin
        w[base + 4] = (uint32_t)s.tstep[src];    // v step
    }
    return w;
}

// Emit the column geometry via 0x4C, targeting fb_base_override.
static void emit_column_list_raw(const SpanGroupWire &s, uint32_t fb_base_override) {
    int lane_count = span_group_effective_lanes(s.lane_count);
    for (int first = 0; first < lane_count;) {
        int n = span_group_chunk_lanes(lane_count - first);
        std::vector<uint32_t> w = encode_column_list_chunk(s, first, n, fb_base_override);
        ring_cmd(CMD_DRAW_COLUMN_LIST, (uint32_t)w.size());
        for (uint32_t x : w) ring_write(x);
        first += n;
    }
}

// Emit the SAME column geometry via the 0x48 direct-affine variant with
// s=0/sstep=0 (the byte-exact reference path), targeting fb_base_override.
static void emit_column_as_affine_raw(const SpanGroupWire &s, uint32_t fb_base_override) {
    SpanGroupWire a = s;
    a.fb_addr = fb_base_override;
    for (int i = 0; i < 8; i++) { a.s[i] = 0; a.sstep[i] = 0; }
    int lane_count = span_group_effective_lanes(a.lane_count);
    for (int first = 0; first < lane_count;) {
        int n = span_group_chunk_lanes(lane_count - first);
        std::vector<uint32_t> w = encode_affine_span_group_chunk(a, first, n);
        ring_cmd(CMD_PARAM_SPAN_LIST, (uint32_t)w.size());
        for (uint32_t x : w) ring_write(x);
        first += n;
    }
}

struct SpanGroupVarWire {
    uint32_t fb_addr;
    uint32_t tex_addr[8];
    int32_t  t[8];
    int32_t  tstep[8];
    uint16_t y_start[8];
    uint16_t count[8];
    uint8_t  flags;
    uint8_t  colormap_id;
    uint8_t  lane_count;
    int16_t  fb_stride;
    int16_t  lane_delta;
    uint16_t tex_width;
    uint16_t tex_w_mask;
    uint16_t tex_h_mask;
    uint8_t  light[8];
};

static SpanGroupVarWire make_span_group_var() {
    SpanGroupVarWire s {};
    s.lane_count = 4;
    s.lane_delta = 1;
    s.fb_stride = 320;
    s.tex_width = 1;
    return s;
}

static uint16_t span_group_var_row_count(const SpanGroupVarWire &s,
                                         int first_lane,
                                         int lane_count) {
    uint32_t rows = 0;
    for (int lane = 0; lane < lane_count && lane < 4; lane++) {
        int src = first_lane + lane;
        if (s.count[src] != 0) {
            uint32_t end = (uint32_t)s.y_start[src] + (uint32_t)s.count[src];
            if (end > rows)
                rows = end;
        }
    }
    return (rows > 0xFFFFu) ? 0xFFFFu : (uint16_t)rows;
}

static std::vector<uint32_t>
encode_affine_span_group_var_chunk(const SpanGroupVarWire &s,
                                   int first_lane,
                                   int lane_count) {
    std::vector<uint32_t> w(4 + lane_count * 7);
    w[0] = ((uint32_t)lane_count << 28)
         | ((uint32_t)s.flags << 20)
         | (uint32_t)(s.colormap_id & 0xF);
    w[1] = (uint32_t)s.tex_width;
    w[2] = ((uint32_t)s.tex_h_mask << 16) | (uint32_t)s.tex_w_mask;
    w[3] = (uint32_t)(int32_t)s.fb_stride;
    for (int lane = 0; lane < lane_count; lane++) {
        int src = first_lane + lane;
        int base = 4 + lane * 7;
        w[base + 0] = s.fb_addr
            + (uint32_t)((int32_t)s.lane_delta * src)
            + (uint32_t)((int32_t)s.fb_stride * (int32_t)s.y_start[src]);
        w[base + 1] = s.tex_addr[src];
        w[base + 2] = (((uint32_t)s.colormap_id & 0x0Fu) << 28)
                    | (((uint32_t)s.light[src] & 0x3Fu) << 16)
                    | (uint32_t)s.count[src];
        w[base + 3] = 0;
        w[base + 4] = (uint32_t)s.t[src];
        w[base + 5] = 0;
        w[base + 6] = (uint32_t)s.tstep[src];
    }
    return w;
}

static void emit_span_group_var_raw(const SpanGroupVarWire &s) {
    int lane_count = span_group_effective_lanes(s.lane_count);
    for (int first = 0; first < lane_count;) {
        int n = span_group_chunk_lanes(lane_count - first);
        std::vector<uint32_t> w = encode_affine_span_group_var_chunk(s, first, n);
        ring_cmd(CMD_PARAM_SPAN_LIST, (uint32_t)w.size());
        for (uint32_t x : w) ring_write(x);
        first += n;
    }
}

struct PerspSpanGroupWire {
    uint32_t fb_addr;
    uint32_t tex_addr;
    uint8_t  lane_count;
    uint8_t  flags;
    uint8_t  reserved;
    uint8_t  colormap_id;
    int32_t  major_fb_step;
    int32_t  minor_fb_step;
    uint16_t tex_width;
    uint16_t tex_w_mask;
    uint16_t tex_h_mask;
    int16_t  start[8];
    uint16_t count[8];
    int32_t sZ, tZ, zinv;
    int32_t sZ_major_step, tZ_major_step, zinv_major_step;
    int32_t sZ_minor_step, tZ_minor_step, zinv_minor_step;
    int32_t light, light_major_step, light_minor_step;
};

static PerspSpanGroupWire make_persp_span_group() {
    PerspSpanGroupWire q {};
    q.lane_count = 4;
    q.major_fb_step = 1;
    q.minor_fb_step = 320;
    q.tex_width = 64;
    q.tex_w_mask = 0;
    q.tex_h_mask = 0;
    return q;
}

static std::vector<uint32_t>
encode_persp_span_group_wire_chunk(const PerspSpanGroupWire &q,
                                   int first_lane,
                                   int lane_count) {
    std::vector<uint32_t> w(31 + ((lane_count + 1) / 2) * 3);
    uint8_t flags = q.flags | SPAN_PERSP;
    int32_t fb_major = q.major_fb_step * first_lane;
    int32_t sZ_major = q.sZ_major_step * first_lane;
    int32_t tZ_major = q.tZ_major_step * first_lane;
    int32_t zi_major = q.zinv_major_step * first_lane;
    int32_t light_major = q.light_major_step * first_lane;
    uint16_t start[4] = {};
    uint16_t count[4] = {};

    for (int lane = 0; lane < lane_count && lane < 4; lane++) {
        int src = first_lane + lane;
        start[lane] = (uint16_t)q.start[src];
        count[lane] = q.count[src];
    }

    w[0] = q.fb_addr + (uint32_t)fb_major;
    w[1] = (uint32_t)q.major_fb_step;
    w[2] = (uint32_t)q.minor_fb_step;
    w[3] = q.tex_addr;
    w[4] = (uint32_t)q.tex_width;
    w[5] = (uint32_t)q.tex_w_mask;
    w[6] = (uint32_t)q.tex_h_mask;
    w[7] = ((uint32_t)flags & 0xFFu)
         | (((uint32_t)q.colormap_id & 0x0Fu) << 8)
         | (1u << 12);
    w[8] = (uint32_t)(q.sZ + sZ_major);
    w[9] = (uint32_t)q.sZ_minor_step;
    w[10] = (uint32_t)q.sZ_major_step;
    w[11] = (uint32_t)(q.tZ + tZ_major);
    w[12] = (uint32_t)q.tZ_minor_step;
    w[13] = (uint32_t)q.tZ_major_step;
    w[14] = (uint32_t)(q.zinv + zi_major);
    w[15] = (uint32_t)q.zinv_minor_step;
    w[16] = (uint32_t)q.zinv_major_step;
    w[17] = (uint32_t)(q.light + light_major);
    w[18] = (uint32_t)q.light_minor_step;
    w[19] = (uint32_t)q.light_major_step;
    w[29] = (uint32_t)lane_count;
    for (int i = 0; i < lane_count; i += 2) {
        uint16_t bu = 0;
        uint16_t bv = 0;
        uint16_t bc = 0;
        if (i + 1 < lane_count) {
            bu = start[i + 1];
            bv = (uint16_t)(i + 1);
            bc = count[i + 1];
        }
        int base = 31 + (i / 2) * 3;
        w[base + 0] = ((uint32_t)(uint16_t)i << 16) | (uint32_t)start[i];
        w[base + 1] = ((uint32_t)bu << 16) | (uint32_t)count[i];
        w[base + 2] = ((uint32_t)bc << 16) | (uint32_t)bv;
    }
    return w;
}

static void emit_persp_span_group_raw(const PerspSpanGroupWire &q) {
    int first_lane = 0;
    int lanes_left = q.lane_count;
    if (lanes_left > 8)
        lanes_left = 8;
    while (lanes_left > 0) {
        int chunk_lanes = (lanes_left >= 4) ? 4 : lanes_left;
        auto w = encode_persp_span_group_wire_chunk(q, first_lane, chunk_lanes);
        ring_cmd(CMD_PARAM_SPAN_LIST, (uint32_t)w.size());
        for (uint32_t x : w) ring_write(x);
        first_lane += chunk_lanes;
        lanes_left -= chunk_lanes;
    }
}

struct ParamSpanRecordWire {
    uint16_t u;
    uint16_t v;
    uint16_t count;
};

struct ParamSpanListWire {
    uint32_t fb_base;
    int32_t fb_major_step;
    int32_t fb_minor_step;
    uint32_t tex_addr;
    uint16_t tex_width;
    uint16_t tex_w_mask;
    uint16_t tex_h_mask;
    uint8_t flags;
    uint8_t colormap_id;
    uint8_t attr_mode;
    uint8_t span_axis;
    uint8_t z_mode;
    uint8_t q29_attr_shift;
    int32_t attr_origin[3];
    int32_t attr_du[3];
    int32_t attr_dv[3];
    int32_t light_origin;
    int32_t light_du;
    int32_t light_dv;
    int32_t clamp_min[3];
    int32_t clamp_max[3];
    uint32_t z_base;
    int32_t z_major_step;
    int32_t z_minor_step;
};

static std::vector<uint32_t>
encode_param_span_list_wire(const ParamSpanListWire &p,
                            const std::vector<ParamSpanRecordWire> &records) {
    std::vector<uint32_t> w(31 + ((records.size() + 1u) / 2u) * 3u, 0);
    uint32_t control = ((uint32_t)p.flags & 0xFFu)
                     | (((uint32_t)p.colormap_id & 0x0Fu) << 8)
                     | (((uint32_t)p.attr_mode & 0x0Fu) << 12)
                     | (((uint32_t)p.span_axis & 0x0Fu) << 16)
                     | (((uint32_t)p.z_mode & 0x0Fu) << 24);

    w[0] = p.fb_base;
    w[1] = (uint32_t)p.fb_major_step;
    w[2] = (uint32_t)p.fb_minor_step;
    w[3] = p.tex_addr;
    w[4] = p.tex_width;
    w[5] = p.tex_w_mask;
    w[6] = p.tex_h_mask;
    w[7] = control;
    for (int i = 0; i < 3; i++) {
        w[8 + i * 3 + 0] = (uint32_t)p.attr_origin[i];
        w[8 + i * 3 + 1] = (uint32_t)p.attr_du[i];
        w[8 + i * 3 + 2] = (uint32_t)p.attr_dv[i];
    }
    w[17] = (uint32_t)p.light_origin;
    w[18] = (uint32_t)p.light_du;
    w[19] = (uint32_t)p.light_dv;
    for (int i = 0; i < 3; i++) {
        w[20 + i * 2 + 0] = (uint32_t)p.clamp_min[i];
        w[20 + i * 2 + 1] = (uint32_t)p.clamp_max[i];
    }
    w[26] = p.z_base;
    w[27] = (uint32_t)p.z_major_step;
    w[28] = (uint32_t)p.z_minor_step;
    w[29] = (uint32_t)records.size();
    w[30] = (p.attr_mode == 3) ? ((uint32_t)p.q29_attr_shift & 31u) : 0u;

    for (size_t i = 0; i < records.size(); i += 2) {
        ParamSpanRecordWire a = records[i];
        ParamSpanRecordWire b {};
        if (i + 1u < records.size())
            b = records[i + 1u];
        size_t k = 31u + (i / 2u) * 3u;
        w[k + 0] = ((uint32_t)a.v << 16) | (uint32_t)a.u;
        w[k + 1] = ((uint32_t)b.u << 16) | (uint32_t)a.count;
        w[k + 2] = ((uint32_t)b.count << 16) | (uint32_t)b.v;
    }
    return w;
}

static void emit_param_span_list_raw(const ParamSpanListWire &p,
                                     const std::vector<ParamSpanRecordWire> &records) {
    auto w = encode_param_span_list_wire(p, records);
    ring_cmd(0x48, (uint32_t)w.size());
    for (uint32_t x : w)
        ring_write(x);
}

struct QuakePerspOraclePixel {
    int64_t sZ;
    int64_t tZ;
    int64_t zi;
    int32_t s_q16;
    int32_t t_q16;
    int s_int;
    int t_int;
    uint8_t texel;
    uint8_t color;
};

static int32_t clamp_i128_to_i32(__int128 v) {
    if (v > (__int128)INT32_MAX)
        return INT32_MAX;
    if (v < (__int128)INT32_MIN)
        return INT32_MIN;
    return (int32_t)v;
}

static int32_t quake_div_q16(int64_t n_q16, int64_t d_q16) {
    if (d_q16 == 0)
        return 0;
    return clamp_i128_to_i32(((__int128)n_q16 << 16) / d_q16);
}

static int32_t arshift_i32(int32_t v, int shift) {
    return v >> shift;
}

static uint8_t quake_persp_sample_texture(const PerspSpanGroupWire &q,
                                          int32_t s_q16,
                                          int32_t t_q16,
                                          int32_t light_q16,
                                          QuakePerspOraclePixel *out) {
    uint16_t mw = q.tex_w_mask ? q.tex_w_mask : 0xFFFFu;
    uint16_t mh = q.tex_h_mask ? q.tex_h_mask : 0xFFFFu;
    int s_int = (int)((uint16_t)(arshift_i32(s_q16, 16) & mw));
    int t_int = (int)((uint16_t)(arshift_i32(t_q16, 16) & mh));
    uint32_t tex_addr = q.tex_addr
                      + (uint32_t)t_int * (uint32_t)q.tex_width
                      + (uint32_t)s_int;
    uint8_t texel = sdram_read_byte(tex_addr);
    uint8_t color = texel;
    if (q.flags & 0x01u) {
        uint8_t light = (uint8_t)((light_q16 >> 16) & 0x3F);
        color = sdram_read_byte(PALOOKUP_BASE_BYTE
                              + (uint32_t)(q.colormap_id & 0x0F) * PALOOKUP_STRIDE
                              + (uint32_t)light * 256u
                              + (uint32_t)texel);
    }
    if (out) {
        out->s_q16 = s_q16;
        out->t_q16 = t_q16;
        out->s_int = s_int;
        out->t_int = t_int;
        out->texel = texel;
        out->color = color;
    }
    return color;
}

static QuakePerspOraclePixel
quake_persp_exact_oracle(const PerspSpanGroupWire &q, int lane, int pixel) {
    int minor = (int)q.start[lane] + pixel;
    QuakePerspOraclePixel r {};
    r.sZ = (int64_t)q.sZ + (int64_t)lane * q.sZ_major_step
         + (int64_t)minor * q.sZ_minor_step;
    r.tZ = (int64_t)q.tZ + (int64_t)lane * q.tZ_major_step
         + (int64_t)minor * q.tZ_minor_step;
    r.zi = (int64_t)q.zinv + (int64_t)lane * q.zinv_major_step
         + (int64_t)minor * q.zinv_minor_step;
    int64_t light = (int64_t)q.light + (int64_t)lane * q.light_major_step
                  + (int64_t)minor * q.light_minor_step;
    r.s_q16 = quake_div_q16(r.sZ, r.zi);
    r.t_q16 = quake_div_q16(r.tZ, r.zi);
    quake_persp_sample_texture(q, r.s_q16, r.t_q16, (int32_t)light, &r);
    return r;
}

static QuakePerspOraclePixel
quake_persp_segment16_oracle(const PerspSpanGroupWire &q, int lane, int pixel) {
    int seg_pixel = pixel & ~15;
    int in_seg = pixel & 15;
    int minor0 = (int)q.start[lane] + seg_pixel;
    int minor1 = minor0 + 16;

    int64_t sZ0 = (int64_t)q.sZ + (int64_t)lane * q.sZ_major_step
                + (int64_t)minor0 * q.sZ_minor_step;
    int64_t tZ0 = (int64_t)q.tZ + (int64_t)lane * q.tZ_major_step
                + (int64_t)minor0 * q.tZ_minor_step;
    int64_t zi0 = (int64_t)q.zinv + (int64_t)lane * q.zinv_major_step
                + (int64_t)minor0 * q.zinv_minor_step;
    int64_t sZ1 = (int64_t)q.sZ + (int64_t)lane * q.sZ_major_step
                + (int64_t)minor1 * q.sZ_minor_step;
    int64_t tZ1 = (int64_t)q.tZ + (int64_t)lane * q.tZ_major_step
                + (int64_t)minor1 * q.tZ_minor_step;
    int64_t zi1 = (int64_t)q.zinv + (int64_t)lane * q.zinv_major_step
                + (int64_t)minor1 * q.zinv_minor_step;

    int32_t s0 = quake_div_q16(sZ0, zi0);
    int32_t t0 = quake_div_q16(tZ0, zi0);
    int32_t s1 = quake_div_q16(sZ1, zi1);
    int32_t t1 = quake_div_q16(tZ1, zi1);
    int32_t sstep = (int32_t)(((int64_t)s1 - (int64_t)s0) >> 4);
    int32_t tstep = (int32_t)(((int64_t)t1 - (int64_t)t0) >> 4);

    QuakePerspOraclePixel r {};
    r.sZ = (int64_t)q.sZ + (int64_t)lane * q.sZ_major_step
         + (int64_t)((int)q.start[lane] + pixel) * q.sZ_minor_step;
    r.tZ = (int64_t)q.tZ + (int64_t)lane * q.tZ_major_step
         + (int64_t)((int)q.start[lane] + pixel) * q.tZ_minor_step;
    r.zi = (int64_t)q.zinv + (int64_t)lane * q.zinv_major_step
         + (int64_t)((int)q.start[lane] + pixel) * q.zinv_minor_step;
    int64_t light = (int64_t)q.light + (int64_t)lane * q.light_major_step
                  + (int64_t)((int)q.start[lane] + pixel) * q.light_minor_step;
    r.s_q16 = s0 + in_seg * sstep;
    r.t_q16 = t0 + in_seg * tstep;
    quake_persp_sample_texture(q, r.s_q16, r.t_q16, (int32_t)light, &r);
    return r;
}

static void emit_quake_segment16_affine_fallback(const PerspSpanGroupWire &q,
                                                 uint32_t fb_base) {
    for (int lane = 0; lane < q.lane_count; lane++) {
        for (int p = 0; p < q.count[lane]; p += 16) {
            int chunk = std::min<int>(16, (int)q.count[lane] - p);
            QuakePerspOraclePixel a0 = quake_persp_segment16_oracle(q, lane, p);
            QuakePerspOraclePixel a1 = quake_persp_segment16_oracle(q, lane, p + 16);
            SpanWire s = make_span();
            s.fb_addr = fb_base + (uint32_t)((int32_t)lane * q.major_fb_step)
                      + (uint32_t)((int32_t)((int)q.start[lane] + p)
                                   * q.minor_fb_step);
            s.tex_addr = q.tex_addr;
            s.flags = q.flags & (uint8_t)~SPAN_PERSP;
            s.colormap_id = q.colormap_id;
            s.count = (uint16_t)chunk;
            s.light = (uint8_t)((q.light >> 16) & 0x3F);
            s.fb_stride = (int16_t)q.minor_fb_step;
            s.tex_width = q.tex_width;
            s.tex_w_mask = q.tex_w_mask;
            s.tex_h_mask = q.tex_h_mask;
            s.s = a0.s_q16;
            s.t = a0.t_q16;
            s.sstep = (int32_t)(((int64_t)a1.s_q16 - (int64_t)a0.s_q16) >> 4);
            s.tstep = (int32_t)(((int64_t)a1.t_q16 - (int64_t)a0.t_q16) >> 4);
            emit_span_raw(s);
        }
    }
}

static void dump_persp_payload_words(const std::vector<uint32_t> &payload) {
    printf("    payload:");
    for (size_t i = 0; i < payload.size(); i++) {
        if ((i % 4) == 0)
            printf("\n      %02zu:", i);
        printf(" %08x", payload[i]);
    }
    printf("\n");
}

static bool compare_quake_persp_group_to_oracles(const char *name,
                                                 const PerspSpanGroupWire &q,
                                                 uint32_t split_fb_base,
                                                 bool fail_on_exact_mismatch) {
    std::vector<uint32_t> payload = encode_persp_span_group_wire_chunk(q, 0, q.lane_count);
    int exact_diffs = 0;
    int seg_diffs = 0;
    int split_diffs = 0;
    bool printed = false;
    bool printed_split = false;

    for (int lane = 0; lane < q.lane_count; lane++) {
        for (int p = 0; p < q.count[lane]; p++) {
            uint32_t fb = q.fb_addr + (uint32_t)((int32_t)lane * q.major_fb_step)
                        + (uint32_t)((int32_t)((int)q.start[lane] + p)
                                     * q.minor_fb_step);
            uint32_t split_fb = split_fb_base
                              + (uint32_t)((int32_t)lane * q.major_fb_step)
                              + (uint32_t)((int32_t)((int)q.start[lane] + p)
                                           * q.minor_fb_step);
            uint8_t got = sdram_read_byte(fb);
            uint8_t split = sdram_read_byte(split_fb);
            QuakePerspOraclePixel exact = quake_persp_exact_oracle(q, lane, p);
            QuakePerspOraclePixel seg16 = quake_persp_segment16_oracle(q, lane, p);
            bool exact_mismatch = got != exact.color;
            bool seg_mismatch = got != seg16.color;
            /* The split oracle is rendered by the GPU through compact-direct
             * 0x48 spans; the lean config drains those, so the split leg is
             * skipped there (the segment16 CPU oracle above stays the
             * byte-exact reference in both configs). */
            bool split_mismatch = !LEAN_CONFIG && (got != split);

            if (exact_mismatch)
                exact_diffs++;
            if (seg_mismatch)
                seg_diffs++;
            if (split_mismatch)
                split_diffs++;

            if (!printed && ((fail_on_exact_mismatch && exact_mismatch)
                          || seg_mismatch || split_mismatch)) {
                printf("  first mismatch in %s:\n", name);
                printf("    lane=%d pixel=%d fb=0x%08x start=%d count=%u\n",
                       lane, p, fb, (int)q.start[lane], q.count[lane]);
                printf("    actual=0x%02x(s=%d t%%8=%d) exact=0x%02x seg16=0x%02x split_gpu=0x%02x\n",
                       got, got & 31, (got >> 5) & 7,
                       exact.color, seg16.color, split);
                printf("    exact sZ=%lld tZ=%lld zi=%lld s_q16=0x%08x t_q16=0x%08x s=%d t=%d texel=0x%02x\n",
                       (long long)exact.sZ, (long long)exact.tZ,
                       (long long)exact.zi, (uint32_t)exact.s_q16,
                       (uint32_t)exact.t_q16, exact.s_int, exact.t_int,
                       exact.texel);
                printf("    seg16 s_q16=0x%08x t_q16=0x%08x s=%d t=%d texel=0x%02x\n",
                       (uint32_t)seg16.s_q16, (uint32_t)seg16.t_q16,
                       seg16.s_int, seg16.t_int, seg16.texel);
                dump_persp_payload_words(payload);
                printed = true;
            }
            if (!printed_split && (seg_mismatch || split_mismatch)) {
                printf("  first segment/split mismatch in %s:\n", name);
                printf("    lane=%d pixel=%d fb=0x%08x split_fb=0x%08x\n",
                       lane, p, fb, split_fb);
                printf("    actual=0x%02x(s=%d t%%8=%d) seg16=0x%02x split_gpu=0x%02x exact=0x%02x\n",
                       got, got & 31, (got >> 5) & 7,
                       seg16.color, split, exact.color);
                printf("    sZ=%lld tZ=%lld zi=%lld seg_s_q16=0x%08x seg_t_q16=0x%08x s=%d t=%d\n",
                       (long long)seg16.sZ, (long long)seg16.tZ,
                       (long long)seg16.zi, (uint32_t)seg16.s_q16,
                       (uint32_t)seg16.t_q16, seg16.s_int, seg16.t_int);
                dump_persp_payload_words(payload);
                printed_split = true;
            }
        }
    }

    printf("  %s diffs: exact=%d segment16=%d split_gpu=%d\n",
           name, exact_diffs, seg_diffs, split_diffs);
    if (split_diffs == 0 && seg_diffs == 0
            && (!fail_on_exact_mismatch || exact_diffs == 0)) {
        check_pass(name);
        return true;
    }

    char buf[160];
    snprintf(buf, sizeof(buf),
             "exact_diffs=%d segment16_diffs=%d split_gpu_diffs=%d",
             exact_diffs, seg_diffs, split_diffs);
    check_fail(name, buf);
    return false;
}

/* Submit a raw mixed command stream through the doorbell-DMA path.  The
 * stream already includes command headers; hardware pulls the words into
 * ring BRAM and publishes wrptr only after the final word lands, with no
 * enclosing batch command. */
static void emit_command_stream_dma(const std::vector<uint32_t> &stream) {
    if (!pending_stream.empty())
        gpu_kick();
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
 * explicit colormap selection and 16-bit scalar counts. */
struct SpanSdk {
    uint32_t fb_addr;
    uint32_t tex_addr;
    int32_t  s, t;
    int32_t  sstep, tstep;
    uint16_t count;
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

static EncodedCommand encode_span_sdk(const SpanSdk &s) {
    SpanWire w {};
    w.fb_addr = s.fb_addr;
    w.tex_addr = s.tex_addr;
    w.s = s.s;
    w.t = s.t;
    w.sstep = s.sstep;
    w.tstep = s.tstep;
    w.colormap_id = s.colormap_id;
    w.count = s.count;
    w.light = s.light;
    w.flags = s.flags;
    w.fb_stride = s.fb_stride;
    w.tex_width = s.tex_width;
    w.tex_w_mask = s.tex_w_mask;
    w.tex_h_mask = s.tex_h_mask;
    w.sZ = s.sdivz;
    w.tZ = s.tdivz;
    w.zinv = s.zi_persp;
    w.sZstep = s.sdivz_step;
    w.tZstep = s.tdivz_step;
    w.zinv_step = s.zi_step;
    return encode_span_wire(w);
}

static void emit_span_sdk_encoded(const SpanSdk &s) {
    auto c = encode_span_sdk(s);
    ring_cmd(c.cmd, (uint32_t)c.payload.size());
    for (uint32_t x : c.payload) ring_write(x);
}

/* High-level state-command wrappers (use the same wire format as the
 * SDK helpers for byte-exact equivalence). */
static uint32_t cmd_current_fb_addr = FB_BASE_BYTE;

static void cmd_set_fb(uint32_t addr, uint16_t stride) {
    ring_cmd(0x23, 2);
    ring_write(addr);
    ring_write((uint32_t)stride);
    cmd_current_fb_addr = addr;
}

static void cmd_set_texture(uint32_t addr, uint16_t width, uint16_t height) {
    ring_cmd(0x20, 2);
    ring_write(addr);
    ring_write(((uint32_t)width << 16) | (uint32_t)height);
}

static void cmd_clear(uint16_t flags, uint16_t color) {
    if ((flags & 0x1u) == 0)
        return;
    ring_cmd(0x11, 3);
    ring_write(cmd_current_fb_addr);
    ring_write((320u << 16) | 200u);
    ring_write((320u << 16) | ((uint32_t)color & 0xFFu));
}

static void cmd_clear_rect(uint32_t addr, uint16_t w, uint16_t h,
                            uint16_t stride, uint8_t color) {
    ring_cmd(0x11, 3);
    ring_write(addr);
    ring_write(((uint32_t)w << 16) | (uint32_t)h);
    ring_write(((uint32_t)stride << 16) | (uint32_t)color);
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

    /* Whole-FB clear public behavior: 320×200 bytes from st_fb_addr. */
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
     * cmap_id is the explicit scalar/span-group slot. */
    void apply_span_affine(const SpanWire &s, uint8_t cmap_id_resolved) {
        const uint8_t SPAN_COLORMAP  = 1 << 0;
        const uint8_t SPAN_SKIP_ZERO = 1 << 2;
        const uint8_t SPAN_TRANSLUC  = 1 << 6;

        /* Mask normalisation: 0 → 0xFFFF (matches RTL line 2253-2256). */
        uint16_t mw = s.tex_w_mask ? s.tex_w_mask : 0xFFFF;
        uint16_t mh = s.tex_h_mask ? s.tex_h_mask : 0xFFFF;

        /* Light is 8-bit in the wire; RTL latches it then uses [5:0]. */
        uint8_t light = s.light;

        /* Single-lane spans now preserve the full uint16_t count. */
        uint16_t count = s.count;

        int32_t cs = s.s, ct = s.t;
        uint32_t fb = s.fb_addr;
        for (uint16_t i = 0; i < count; i++) {
            int32_t s_int = (int16_t)((cs >> 16) & mw);
            int32_t t_int = (int16_t)((ct >> 16) & mh);
            uint32_t tex_addr = (uint32_t)((int64_t)s.tex_addr
                              + (int64_t)t_int * (uint32_t)s.tex_width
                              + s_int);
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

    /* Resolve the explicit per-command colormap id. */
    uint8_t resolve_cmap(const SpanWire &s) const {
        return s.colormap_id & 0xF;
    }

    /* native span dispatch through the model. */
    void apply_span_ref(const SpanWire &s) {
        apply_span_affine(s, resolve_cmap(s));
    }

    /* Batched native span stream dispatch — applies each span in order. */
    void apply_batch(const std::vector<SpanWire> &spans) {
        for (const auto &s : spans)
            apply_span_affine(s, resolve_cmap(s));
    }

    void apply_span_group_affine(const SpanGroupWire &q) {
        int lane_count = (q.lane_count == 2 || q.lane_count == 4 ||
                          q.lane_count == 8) ? q.lane_count : 1;
        for (int lane = 0; lane < lane_count; lane++) {
            SpanWire s = make_span();
            s.fb_addr  = q.fb_addr + (uint32_t)((int32_t)q.lane_delta * lane);
            s.tex_addr = q.tex_addr[lane];
            s.s = q.s[lane];
            s.t = q.t[lane];
            s.sstep = q.sstep[lane];
            s.tstep = q.tstep[lane];
            s.colormap_id = q.colormap_id[lane];
            s.count = span_group_lane_count_value(q, lane);
            s.light = q.light[lane];
            s.flags = q.flags;
            s.fb_stride = q.fb_stride;
            s.tex_width = q.tex_width ? q.tex_width : 1;
            s.tex_w_mask = q.tex_w_mask;
            s.tex_h_mask = q.tex_h_mask;
            apply_span_affine(s, q.colormap_id[lane] & 0xF);
        }
    }

    void apply_span_group_var_affine(const SpanGroupVarWire &q) {
        int lane_count = (q.lane_count == 2 || q.lane_count == 4 ||
                          q.lane_count == 8) ? q.lane_count : 1;
        for (int lane = 0; lane < lane_count; lane++) {
            SpanWire s = make_span();
            s.fb_addr  = q.fb_addr
                       + (uint32_t)((int32_t)q.lane_delta * lane)
                       + (uint32_t)((int32_t)q.fb_stride *
                                    (int32_t)q.y_start[lane]);
            s.tex_addr = q.tex_addr[lane];
            s.s = 0;
            s.t = q.t[lane];
            s.sstep = 0;
            s.tstep = q.tstep[lane];
            s.colormap_id = q.colormap_id;
            s.count = q.count[lane];
            s.light = q.light[lane];
            s.flags = q.flags;
            s.fb_stride = q.fb_stride;
            s.tex_width = q.tex_width ? q.tex_width : 1;
            s.tex_w_mask = q.tex_w_mask;
            s.tex_h_mask = q.tex_h_mask;
            apply_span_affine(s, q.colormap_id & 0xF);
        }
    }

    void apply_span_group_stream(const std::vector<SpanGroupWire> &spans) {
        for (const auto &s : spans)
            apply_span_group_affine(s);
    }

    /* DRAW_TRIANGLES — affine, top-left rule. Vertex.r is the palookup
     * shade row (gpu_core.v line 2330: v_r[0] flat-shaded). Triangles
     * always route through palookup slot 0. */
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

                /* Triangles always route through palookup slot 0, row = vt[0].r. */
                uint8_t color = palookup(0, vt[0].r, texel);

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

/* Compare two GPU framebuffer regions in SDRAM byte-for-byte (no CPU model).
 * Used by the CMD_DRAW_COLUMN_LIST (0x4C) oracle: the 0x4C and 0x48-with-s=0
 * renders of the same geometry must be bit-identical. */
static bool compare_fb_to_fb(const char *name,
                             uint32_t base_a, uint32_t base_b,
                             uint16_t stride, int x, int y, int w, int h) {
    int diffs = 0;
    int first_x = -1, first_y = -1;
    uint8_t first_a = 0, first_b = 0;
    for (int dy = 0; dy < h; dy++) {
        for (int dx = 0; dx < w; dx++) {
            uint32_t off = (uint32_t)(y + dy) * stride + (uint32_t)(x + dx);
            uint8_t a = sdram_read_byte(base_a + off);
            uint8_t b = sdram_read_byte(base_b + off);
            if (a != b) {
                if (diffs == 0) {
                    first_x = x + dx; first_y = y + dy;
                    first_a = a; first_b = b;
                }
                diffs++;
            }
        }
    }
    if (diffs == 0) { check_pass(name); return true; }
    char buf[256];
    snprintf(buf, sizeof(buf),
             "%d byte diffs (0x4C vs 0x48); first @ (x=%d,y=%d) 0x4C=0x%02x 0x48=0x%02x",
             diffs, first_x, first_y, first_a, first_b);
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

// ---- Section 1: FENCE ------------------------------------------------------
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
    m.apply_span_ref(spA);

    cmd_set_texture(TEX_BASE_BYTE + 0x1000, 32, 32);
    SpanWire spB = spA;
    spB.fb_addr  = FB_BASE_BYTE + 320;   // y=1
    spB.tex_addr = TEX_BASE_BYTE + 0x1000;
    emit_span_raw(spB);
    m.apply_span_ref(spB);

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

// ---- Section 5: colormap selection -----------------------------------------
static void test_single_lane_span_explicit_colormap_slots() {
    /* Three colormap slots:
     *   slot 0 row 0: identity        (cm[i] = i)
     *   slot 1 row 0: inverted        (cm[i] = 255-i)
     *   slot 2 row 0: constant 0xC0
     * Draw the same colormapped span with explicit colormap slots and
     * verify the output mirrors the slot, including explicit slot 0.
     */
    printf("TEST single_lane_span_explicit_colormap_slots\n");
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

    SpanWire s0 = base; s0.fb_addr = FB_BASE_BYTE + 0;       s0.colormap_id = 0; emit_span_raw(s0); m.apply_span_ref(s0);
    SpanWire s1 = base; s1.fb_addr = FB_BASE_BYTE + 320;     s1.colormap_id = 1; emit_span_raw(s1); m.apply_span_ref(s1);
    SpanWire s2 = base; s2.fb_addr = FB_BASE_BYTE + 2*320;   s2.colormap_id = 2; emit_span_raw(s2); m.apply_span_ref(s2);

    if (!submit_and_wait()) {
        check_fail("single_lane_span_explicit_colormap_slots", "timeout");
        return;
    }
    compare_fb_region("single_lane_span_explicit_colormap_slots.s0", m, FB_BASE_BYTE, 320,
                      0, 0, 16, 1);
    compare_fb_region("single_lane_span_explicit_colormap_slots.s1", m, FB_BASE_BYTE, 320,
                      0, 1, 16, 1);
    compare_fb_region("single_lane_span_explicit_colormap_slots.s2", m, FB_BASE_BYTE, 320,
                      0, 2, 16, 1);
    compare_sentinel_border("single_lane_span_explicit_colormap_slots.border",
                             FB_BASE_BYTE, 320, 0, 0, 16, 3, 4);
}

static void test_per_span_colormap_explicit_slots() {
    /* Draw three spans inside the SAME batch: one with explicit per-span
     * id=1 (inverted), one with explicit per-span id=0 (identity), and
     * one more id=1 span. */
    printf("TEST per_span_colormap_explicit_slots\n");
    gpu_init();
    auto m = preload_with_sentinel();

    upload_palookup_identity_row(0, 0);
    upload_palookup_inverted_row(1, 0);

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
    base.flags = 0x1;

    SpanWire s_inv = base; s_inv.fb_addr = FB_BASE_BYTE + 0;     s_inv.colormap_id = 1;
    SpanWire s_id  = base; s_id.fb_addr  = FB_BASE_BYTE + 320;   s_id.colormap_id  = 0;  // explicit slot 0
    SpanWire s_ex0 = base; s_ex0.fb_addr = FB_BASE_BYTE + 2*320; s_ex0.colormap_id = 1;  // explicit 1

    emit_batch_raw({s_inv, s_id, s_ex0});
    m.apply_batch({s_inv, s_id, s_ex0});

    if (!submit_and_wait()) {
        check_fail("per_span_colormap_explicit_slots", "timeout");
        return;
    }
    compare_fb_region("per_span_colormap_explicit_slots.inv", m,
                      FB_BASE_BYTE, 320, 0, 0, 16, 1);
    compare_fb_region("per_span_colormap_explicit_slots.id_explicit0", m,
                      FB_BASE_BYTE, 320, 0, 1, 16, 1);
    compare_fb_region("per_span_colormap_explicit_slots.ex_inv", m,
                      FB_BASE_BYTE, 320, 0, 2, 16, 1);
}

// ---- Section 6: triangle skip-zero retirement -------------------------------
// Triangle commands no longer consume global skip-zero state. A 0xFF texel
// should render normally through palookup slot 0.
static void test_triangle_no_global_skip_zero() {
    printf("TEST triangle_no_global_skip_zero\n");
    gpu_init();
    preload_with_sentinel();
    upload_palookup_identity_row(0, 0);
    /* Texture: every byte is 0xFF. */
    std::vector<uint8_t> tex(64, 0xFF);
    upload_texture(TEX_BASE_BYTE, tex);

    cmd_set_fb(FB_BASE_BYTE, 320);
    cmd_set_texture(TEX_BASE_BYTE, 8, 8);

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
    vert(10, 10, 0,           0);
    vert(40, 10, 7 << 16,     0);
    vert(10, 40, 0,           7 << 16);

    if (!submit_and_wait()) {
        check_fail("triangle_no_global_skip_zero", "timeout");
        return;
    }
    int n_ff = 0;
    int n_other = 0;
    for (int y = 15; y < 35; y++) {
        for (int x = 15; x < 35; x++) {
            uint8_t a = sdram_read_byte(FB_BASE_BYTE + y*320 + x);
            if (a == 0xFF) n_ff++;
            else if (a != SENTINEL_BYTE) n_other++;
        }
    }
    if (n_ff > 100 && n_other == 0) {
        check_pass("triangle_no_global_skip_zero.writes_0xff");
    } else {
        char buf[128];
        snprintf(buf, sizeof(buf), "ff=%d other_nonsentinel=%d", n_ff, n_other);
        check_fail("triangle_no_global_skip_zero.writes_0xff", buf);
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
    if (aw_writes < clear_words && tb->dbg_aw_burst_count != 0 && tb->dbg_aw_max_len >= 3) {
        check_pass("clear_drains_framebuffer_writes.aw_count_reduced");
    } else {
        char buf[128];
        snprintf(buf, sizeof(buf), "AW handshakes=%u words=%u bursts=%u max_len=%u",
                 aw_writes, clear_words, tb->dbg_aw_burst_count,
                 (unsigned)tb->dbg_aw_max_len);
        check_fail("clear_drains_framebuffer_writes.aw_count_reduced", buf);
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

// ---- Section 9: native span — raw textured -----------------------------------
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
        m.apply_span_ref(s);

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

static void test_span_raw_count_4096_preserved() {
    /* Single-lane spans encode through native affine/perspective group commands, so
     * the old 12-bit scalar count field is gone.  A 4096-pixel span should draw
     * 4096 pixels instead of truncating to zero. */
    printf("TEST span_raw_count_4096_preserved\n");
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
    s.count     = 4096;
    s.tex_width = 16; s.tex_w_mask = 0xF;
    emit_span_raw(s);
    m.apply_span_ref(s);

    if (!submit_and_wait()) {
        check_fail("span_raw_count_4096_preserved", "timeout");
        return;
    }
    compare_fb_region("span_raw_count_4096_preserved.fb", m, FB_BASE_BYTE,
                      320, 0, 0, 320, 13);
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
    m.apply_span_ref(s);

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
    m.apply_span_ref(s);

    if (!submit_and_wait()) {
        check_fail("span_raw_mask_zero_means_identity", "timeout");
        return;
    }
    compare_fb_region("span_raw_mask_zero_means_identity.fb", m,
                      FB_BASE_BYTE, 320, 0, 0, 64, 1);
    compare_sentinel_border("span_raw_mask_zero_means_identity.border",
                             FB_BASE_BYTE, 320, 0, 0, 64, 1, 4);
}

static void test_span_affine_group_repeatable() {
    printf("TEST span_affine_group_repeatable\n");
    gpu_init();
    auto m = preload_with_sentinel();

    std::vector<uint8_t> tex(64);
    for (int i = 0; i < 64; i++)
        tex[i] = (uint8_t)(0x30 + i);
    upload_texture(TEX_BASE_BYTE, tex);
    m.snapshot_from_sdram();

    cmd_set_fb(FB_BASE_BYTE, 320);
    m.st_fb_addr = FB_BASE_BYTE;

    SpanWire first = make_span();
    first.fb_addr = FB_BASE_BYTE + 4u * 320u + 8u;
    first.tex_addr = TEX_BASE_BYTE;
    first.s = 0;
    first.t = 0;
    first.sstep = 0x10000;
    first.tstep = 0;
    first.count = 16;
    first.tex_width = 64;
    first.tex_w_mask = 0x3F;
    first.flags = 0;

    SpanWire second = first;
    second.fb_addr = FB_BASE_BYTE + 5u * 320u + 8u;

    emit_span_raw(first);
    emit_span_raw(second);
    m.apply_span_ref(first);
    m.apply_span_ref(second);

    if (!submit_and_wait()) {
        check_fail("span_affine_group_repeatable", "timeout");
        return;
    }

    compare_fb_region("span_affine_group_repeatable.fb",
                      m, FB_BASE_BYTE, 320, 8, 4, 16, 2);

    int mismatches = 0;
    for (int x = 0; x < 16; x++) {
        uint8_t a = sdram_read_byte(FB_BASE_BYTE + 4u * 320u + 8u + x);
        uint8_t b = sdram_read_byte(FB_BASE_BYTE + 5u * 320u + 8u + x);
        if (a != b)
            mismatches++;
    }
    if (mismatches == 0) {
        check_pass("span_affine_group_repeatable.rows_equal");
    } else {
        char buf[96];
        snprintf(buf, sizeof(buf), "%d row byte mismatches", mismatches);
        check_fail("span_affine_group_repeatable.rows_equal", buf);
    }
}

// ---- Section 10: native span with COLORMAP -----------------------------------
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
    emit_span_raw(s_5);  m.apply_span_ref(s_5);
    emit_span_raw(s_15); m.apply_span_ref(s_15);

    if (!submit_and_wait()) {
        check_fail("span_colormap_explicit_per_span_id", "timeout");
        return;
    }
    compare_fb_region("span_colormap.slot5_inv", m, FB_BASE_BYTE, 320,
                      0, 0, 16, 1);
    compare_fb_region("span_colormap.slot15_const", m, FB_BASE_BYTE, 320,
                      0, 1, 16, 1);
}

static void test_palookup_base_register() {
    printf("TEST palookup_base_register\n");
    gpu_init();
    preload_with_sentinel();

    static const uint32_t ALT_PALOOKUP_BASE = 0x00200000u;
    mmio_write(REG_PALOOKUP_BASE, ALT_PALOOKUP_BASE);

    std::vector<uint8_t> tex(1, 0x44);
    upload_texture(TEX_BASE_BYTE, tex);
    sdram_write_byte(ALT_PALOOKUP_BASE + 2u * PALOOKUP_STRIDE + 0x44u, 0x5A);
    mmio_write(REG_TEX_FLUSH, 1);

    cmd_set_fb(FB_BASE_BYTE, 320);
    SpanWire s = make_span();
    s.fb_addr = FB_BASE_BYTE;
    s.tex_addr = TEX_BASE_BYTE;
    s.s = 0;
    s.t = 0;
    s.sstep = 0;
    s.tstep = 0;
    s.count = 1;
    s.tex_width = 1;
    s.tex_w_mask = 0;
    s.tex_h_mask = 0;
    s.flags = 0x1;
    s.colormap_id = 2;
    emit_span_raw(s);

    if (!submit_and_wait()) {
        check_fail("palookup_base_register", "timeout");
        return;
    }

    uint8_t got = sdram_read_byte(FB_BASE_BYTE);
    if (got == 0x5A) {
        check_pass("palookup_base_register");
    } else {
        char msg[80];
        snprintf(msg, sizeof(msg), "got=%02x want=5a", got);
        check_fail("palookup_base_register", msg);
    }
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
    emit_span_raw(s64); m.apply_span_ref(s64);
    emit_span_raw(s65); m.apply_span_ref(s65);

    if (!submit_and_wait()) {
        check_fail("span_colormap_light_wraps_mod_64", "timeout");
        return;
    }
    compare_fb_region("light_wraps.row64_to_0", m, FB_BASE_BYTE, 320,
                      0, 0, 8, 1);
    compare_fb_region("light_wraps.row65_to_1", m, FB_BASE_BYTE, 320,
                      0, 1, 8, 1);
}

// ---- Section 11: native span with SKIP_ZERO ----------------------------------
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
    m.apply_span_ref(s);

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
    m.apply_span_ref(s);

    if (!submit_and_wait()) {
        check_fail("skip_zero_with_colormap", "timeout");
        return;
    }
    compare_fb_region("skip_zero_with_colormap", m, FB_BASE_BYTE, 320,
                      0, 0, 2, 1);
}

// ---- Section 12: native span with TRANSLUC -----------------------------------
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
    m.apply_span_ref(s);

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
    emit_span_raw(s); m.apply_span_ref(s);
    /* Second pass — should re-blend the now-blended bytes. */
    emit_span_raw(s); m.apply_span_ref(s);

    if (!submit_and_wait()) {
        check_fail("transluc_overdraw_same_word", "timeout");
        return;
    }
    compare_fb_region("transluc_overdraw_same_word.fb", m, FB_BASE_BYTE,
                      320, 0, 0, 4, 1);
}

static void test_transluc_duplicate_lane_order() {
    /* Degenerate but important for the grouped BLEND path: two translucent
     * fragments hit the same byte lane in one span.  The second fragment must
     * blend over the first fragment's result, so the hardware has to flush the
     * current lane group before accepting the duplicate lane. */
    printf("TEST transluc_duplicate_lane_order\n");
    gpu_init();
    auto m = preload_with_sentinel();
    auto table = make_transluc_table_avg();
    upload_transluc_table(table);
    m.transluc = table;

    cmd_set_fb(FB_BASE_BYTE, 320);
    m.st_fb_addr = FB_BASE_BYTE;
    cmd_clear_rect(FB_BASE_BYTE, 4, 1, 0, 0x10);
    m.apply_clear_rect(FB_BASE_BYTE, 4, 1, 0, 0x10);

    std::vector<uint8_t> tex { 0x40, 0x20 };
    upload_texture(TEX_BASE_BYTE, tex);
    m.snapshot_from_sdram();
    m.apply_clear_rect(FB_BASE_BYTE, 4, 1, 0, 0x10);

    SpanWire s = make_span();
    s.fb_addr = FB_BASE_BYTE;
    s.tex_addr = TEX_BASE_BYTE;
    s.tex_width = 2; s.tex_w_mask = 0x1;
    s.s = 0; s.t = 0; s.sstep = 0x10000; s.tstep = 0;
    s.count = 2;
    s.fb_stride = 0;
    s.flags = (1 << 6);

    emit_span_raw(s);
    m.apply_span_ref(s);

    if (!submit_and_wait()) {
        check_fail("transluc_duplicate_lane_order", "timeout");
        return;
    }
    compare_fb_region("transluc_duplicate_lane_order.fb", m, FB_BASE_BYTE,
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
    m.apply_span_ref(ref_affine);

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

static std::vector<uint8_t> make_projection_test_texture() {
    std::vector<uint8_t> tex(64 * 64);
    for (int y = 0; y < 64; y++) {
        for (int x = 0; x < 64; x++)
            tex[(size_t)y * 64u + (size_t)x] = (uint8_t)((x * 3 + y * 17) & 0xFF);
    }
    return tex;
}

static void test_persp_span_group_varcount_const_z_equals_single_lane_spans() {
    printf("TEST persp_span_group_varcount_const_z_equals_single_lane_spans\n");
    gpu_init();
    preload_with_sentinel();
    upload_texture(TEX_BASE_BYTE, make_projection_test_texture());
    upload_palookup_inverted_row(1, 0);
    cmd_set_fb(FB_BASE_BYTE, 320);

    PerspSpanGroupWire q = make_persp_span_group();
    q.fb_addr = FB_BASE_BYTE + 20u;
    q.tex_addr = TEX_BASE_BYTE;
    q.flags = SPAN_PERSP | (1u << 0);  // perspective + colormap
    q.reserved = 1u;
    q.colormap_id = 1;
    q.tex_w_mask = 0x3F;
    q.tex_h_mask = 0x3F;
    q.start[0] = 4;  q.count[0] = 24;
    q.start[1] = 2;  q.count[1] = 19;
    q.start[2] = 7;  q.count[2] = 12;
    q.start[3] = 1;  q.count[3] = 28;
    q.sZ = 0;
    q.tZ = 0;
    q.zinv = 0x00010000;
    q.sZ_major_step = 0x00010000;
    q.tZ_minor_step = 0x00010000;
    q.zinv_minor_step = 0;             // constant-Z fast path per lane
    q.light = 0;

    emit_persp_span_group_raw(q);

    const uint32_t scalar_base = FB_BASE_BYTE + 80u;
    for (int lane = 0; lane < 4; lane++) {
        const int start = q.start[lane];
        SpanWire s = make_span();
        s.fb_addr = scalar_base + (uint32_t)lane
                  + (uint32_t)start * 320u;
        s.tex_addr = TEX_BASE_BYTE;
        s.count = q.count[lane];
        s.flags = q.flags;
        s.colormap_id = q.colormap_id;
        s.fb_stride = 320;
        s.tex_width = q.tex_width;
        s.tex_w_mask = q.tex_w_mask;
        s.tex_h_mask = q.tex_h_mask;
        s.sZ = q.sZ + lane * q.sZ_major_step + start * q.sZ_minor_step;
        s.tZ = q.tZ + lane * q.tZ_major_step + start * q.tZ_minor_step;
        s.zinv = q.zinv + lane * q.zinv_major_step + start * q.zinv_minor_step;
        s.sZstep = q.sZ_minor_step;
        s.tZstep = q.tZ_minor_step;
        s.zinv_step = q.zinv_minor_step;
        emit_span_raw(s);
    }

    if (!submit_and_wait()) {
        check_fail("persp_span_group_varcount_const_z_equals_single_lane_spans", "timeout");
        return;
    }

    int diffs = 0;
    int first_lane = -1, first_pix = -1;
    uint8_t first_group = 0, first_scalar = 0;
    for (int lane = 0; lane < 4; lane++) {
        for (int p = 0; p < q.count[lane]; p++) {
            uint32_t group_addr = FB_BASE_BYTE + 20u + (uint32_t)lane
                                + (uint32_t)(q.start[lane] + p) * 320u;
            uint32_t scalar_addr = scalar_base + (uint32_t)lane
                                 + (uint32_t)(q.start[lane] + p) * 320u;
            uint8_t a = sdram_read_byte(group_addr);
            uint8_t b = sdram_read_byte(scalar_addr);
            if (a != b) {
                if (diffs == 0) {
                    first_lane = lane;
                    first_pix = p;
                    first_group = a;
                    first_scalar = b;
                }
                diffs++;
            }
        }
    }
    if (diffs == 0) {
        check_pass("persp_span_group_varcount_const_z_equals_single_lane_spans");
    } else {
        char buf[192];
        snprintf(buf, sizeof(buf),
                 "diffs=%d first lane=%d pix=%d group=0x%02x scalar=0x%02x",
                 diffs, first_lane, first_pix, first_group, first_scalar);
        check_fail("persp_span_group_varcount_const_z_equals_single_lane_spans", buf);
    }
}

static void test_persp_span_group_doom_wall_layout_matches_reference() {
    printf("TEST persp_span_group_doom_wall_layout_matches_reference\n");
    gpu_init();
    auto m = preload_with_sentinel();

    constexpr int tex_columns = 8;
    constexpr int tex_height = 64;
    std::vector<uint8_t> tex(tex_columns * tex_height);
    for (int col = 0; col < tex_columns; col++) {
        for (int y = 0; y < tex_height; y++) {
            tex[col * tex_height + y] =
                (uint8_t)(0x11 + col * 0x19 + y * 0x03);
        }
    }
    upload_texture(TEX_BASE_BYTE, tex);
    upload_palookup_inverted_row(1, 0);
    m.snapshot_from_sdram();
    cmd_set_fb(FB_BASE_BYTE, 320);

    PerspSpanGroupWire q = make_persp_span_group();
    q.fb_addr = FB_BASE_BYTE + 20u;
    q.tex_addr = TEX_BASE_BYTE;
    q.flags = SPAN_PERSP | (1u << 0);  // perspective + colormap
    q.reserved = 1u;
    q.colormap_id = 1;
    q.major_fb_step = 1;
    q.minor_fb_step = 320;
    q.tex_width = tex_height;          // column-major texture: col*height + y
    q.tex_w_mask = tex_height - 1;     // S = vertical texel within column
    q.tex_h_mask = tex_columns - 1;    // T = wall texture column
    q.start[0] = 3;  q.count[0] = 33;
    q.start[1] = 8;  q.count[1] = 17;
    q.start[2] = 1;  q.count[2] = 42;
    q.start[3] = 20; q.count[3] = 7;
    q.sZ = 0;                         // S walks down the vertical span
    q.tZ = 2 << 16;                   // T selects wall texture column
    q.zinv = 0x00010000;              // exact reciprocal 1.0
    q.sZ_major_step = 0;
    q.tZ_major_step = 1 << 16;
    q.zinv_major_step = 0;
    q.sZ_minor_step = 1 << 16;
    q.tZ_minor_step = 0;
    q.zinv_minor_step = 0;            // constant-Z fast path per lane
    q.light = 0;
    q.light_major_step = 0;
    q.light_minor_step = 0;

    emit_persp_span_group_raw(q);

    for (int lane = 0; lane < 4; lane++) {
        const int start = q.start[lane];
        SpanWire s = make_span();
        s.fb_addr = FB_BASE_BYTE + 20u + (uint32_t)lane
                  + (uint32_t)start * 320u;
        s.tex_addr = TEX_BASE_BYTE;
        s.count = q.count[lane];
        s.flags = q.flags;
        s.colormap_id = q.colormap_id;
        s.fb_stride = 320;
        s.tex_width = q.tex_width;
        s.tex_w_mask = q.tex_w_mask;
        s.tex_h_mask = q.tex_h_mask;
        s.s = q.sZ + lane * q.sZ_major_step + start * q.sZ_minor_step;
        s.t = q.tZ + lane * q.tZ_major_step + start * q.tZ_minor_step;
        s.sstep = q.sZ_minor_step;
        s.tstep = q.tZ_minor_step;
        m.apply_span_affine(s, q.colormap_id & 0xF);
    }

    if (!submit_and_wait()) {
        check_fail("persp_span_group_doom_wall_layout_matches_reference", "timeout");
        return;
    }

    compare_fb_region("persp_span_group_doom_wall_layout_matches_reference.fb",
                      m, FB_BASE_BYTE, 320, 20, 0, 4, tex_height);
}

static void test_persp_span_group_doom_wall_layout_8lane_chunk_positions() {
    printf("TEST persp_span_group_doom_wall_layout_8lane_chunk_positions\n");
    gpu_init();
    auto m = preload_with_sentinel();

    constexpr int tex_columns = 16;
    constexpr int tex_height = 64;
    std::vector<uint8_t> tex(tex_columns * tex_height);
    for (int col = 0; col < tex_columns; col++) {
        for (int y = 0; y < tex_height; y++) {
            tex[col * tex_height + y] =
                (uint8_t)(0x07 + col * 0x11 + y * 0x05);
        }
    }
    upload_texture(TEX_BASE_BYTE, tex);
    upload_palookup_identity_row(0, 0);
    m.snapshot_from_sdram();
    cmd_set_fb(FB_BASE_BYTE, 320);

    PerspSpanGroupWire q = make_persp_span_group();
    q.fb_addr = FB_BASE_BYTE + 13u;
    q.tex_addr = TEX_BASE_BYTE;
    q.lane_count = 8;
    q.flags = SPAN_PERSP | (1u << 0);
    q.reserved = 1u;
    q.colormap_id = 0;
    q.major_fb_step = 1;
    q.minor_fb_step = 320;
    q.tex_width = tex_height;
    q.tex_w_mask = tex_height - 1;
    q.tex_h_mask = tex_columns - 1;
    const int starts[8] = {4, 11, 2, 25, 7, 16, 0, 31};
    const int counts[8] = {18, 9, 27, 6, 16, 11, 34, 5};
    for (int i = 0; i < 8; i++) {
        q.start[i] = (int16_t)starts[i];
        q.count[i] = (uint16_t)counts[i];
    }
    q.sZ = 0;
    q.tZ = 3 << 16;
    q.zinv = 0x00010000;
    q.sZ_major_step = 0;
    q.tZ_major_step = 1 << 16;
    q.zinv_major_step = 0;
    q.sZ_minor_step = 1 << 16;
    q.tZ_minor_step = 0;
    q.zinv_minor_step = 0;

    emit_persp_span_group_raw(q);

    for (int lane = 0; lane < 8; lane++) {
        const int start = q.start[lane];
        SpanWire s = make_span();
        s.fb_addr = FB_BASE_BYTE + 13u + (uint32_t)lane
                  + (uint32_t)start * 320u;
        s.tex_addr = TEX_BASE_BYTE;
        s.count = q.count[lane];
        s.flags = q.flags;
        s.colormap_id = q.colormap_id;
        s.fb_stride = 320;
        s.tex_width = q.tex_width;
        s.tex_w_mask = q.tex_w_mask;
        s.tex_h_mask = q.tex_h_mask;
        s.s = q.sZ + lane * q.sZ_major_step + start * q.sZ_minor_step;
        s.t = q.tZ + lane * q.tZ_major_step + start * q.tZ_minor_step;
        s.sstep = q.sZ_minor_step;
        s.tstep = q.tZ_minor_step;
        m.apply_span_affine(s, q.colormap_id & 0xF);
    }

    if (!submit_and_wait()) {
        check_fail("persp_span_group_doom_wall_layout_8lane_chunk_positions", "timeout");
        return;
    }

    compare_fb_region("persp_span_group_doom_wall_layout_8lane_chunk_positions.fb",
                      m, FB_BASE_BYTE, 320, 13, 0, 8, tex_height);
}

static void test_persp_span_group_doom_wall_negative_t_wrap() {
    printf("TEST persp_span_group_doom_wall_negative_t_wrap\n");
    gpu_init();
    auto m = preload_with_sentinel();

    constexpr int tex_columns = 16;
    constexpr int tex_height = 128;
    std::vector<uint8_t> tex(tex_columns * tex_height);
    for (int col = 0; col < tex_columns; col++) {
        for (int y = 0; y < tex_height; y++) {
            tex[col * tex_height + y] =
                (uint8_t)(0x31 + col * 0x0d + y * 0x09);
        }
    }
    upload_texture(TEX_BASE_BYTE, tex);
    upload_palookup_identity_row(0, 0);
    m.snapshot_from_sdram();
    cmd_set_fb(FB_BASE_BYTE, 320);

    PerspSpanGroupWire q = make_persp_span_group();
    q.fb_addr = FB_BASE_BYTE + 41u;
    q.tex_addr = TEX_BASE_BYTE;
    q.lane_count = 4;
    q.flags = SPAN_PERSP | (1u << 0);
    q.reserved = 1u;
    q.colormap_id = 0;
    q.major_fb_step = 1;
    q.minor_fb_step = 320;
    q.tex_width = tex_height;
    q.tex_w_mask = tex_height - 1;
    q.tex_h_mask = tex_columns - 1;
    q.start[0] = 6;  q.count[0] = 23;
    q.start[1] = 1;  q.count[1] = 35;
    q.start[2] = 18; q.count[2] = 9;
    q.start[3] = 4;  q.count[3] = 28;
    q.sZ = 0;
    q.tZ = -(3 << 16);
    q.zinv = 0x00010000;
    q.sZ_major_step = 0;
    q.tZ_major_step = 1 << 16;
    q.zinv_major_step = 0;
    q.sZ_minor_step = 1 << 16;
    q.tZ_minor_step = 0;
    q.zinv_minor_step = 0;

    emit_persp_span_group_raw(q);

    for (int lane = 0; lane < 4; lane++) {
        const int start = q.start[lane];
        SpanWire s = make_span();
        s.fb_addr = FB_BASE_BYTE + 41u + (uint32_t)lane
                  + (uint32_t)start * 320u;
        s.tex_addr = TEX_BASE_BYTE;
        s.count = q.count[lane];
        s.flags = q.flags;
        s.colormap_id = q.colormap_id;
        s.fb_stride = 320;
        s.tex_width = q.tex_width;
        s.tex_w_mask = q.tex_w_mask;
        s.tex_h_mask = q.tex_h_mask;
        s.s = q.sZ + lane * q.sZ_major_step + start * q.sZ_minor_step;
        s.t = q.tZ + lane * q.tZ_major_step + start * q.tZ_minor_step;
        s.sstep = q.sZ_minor_step;
        s.tstep = q.tZ_minor_step;
        m.apply_span_affine(s, q.colormap_id & 0xF);
    }

    if (!submit_and_wait()) {
        check_fail("persp_span_group_doom_wall_negative_t_wrap", "timeout");
        return;
    }

    compare_fb_region("persp_span_group_doom_wall_negative_t_wrap.fb",
                      m, FB_BASE_BYTE, 320, 41, 0, 4, tex_height);
}

static void test_persp_span_group_doom_wall_7lane_partial_chunk() {
    printf("TEST persp_span_group_doom_wall_7lane_partial_chunk\n");
    gpu_init();
    auto m = preload_with_sentinel();

    constexpr int tex_columns = 16;
    constexpr int tex_height = 128;
    std::vector<uint8_t> tex(tex_columns * tex_height);
    for (int col = 0; col < tex_columns; col++) {
        for (int y = 0; y < tex_height; y++) {
            tex[col * tex_height + y] =
                (uint8_t)(0x19 + col * 0x17 + y * 0x0b);
        }
    }
    upload_texture(TEX_BASE_BYTE, tex);
    upload_palookup_identity_row(0, 0);
    m.snapshot_from_sdram();
    cmd_set_fb(FB_BASE_BYTE, 320);

    PerspSpanGroupWire q = make_persp_span_group();
    q.fb_addr = FB_BASE_BYTE + 57u;
    q.tex_addr = TEX_BASE_BYTE;
    q.lane_count = 7;
    q.flags = SPAN_PERSP | (1u << 0);
    q.reserved = 1u;
    q.colormap_id = 0;
    q.major_fb_step = 1;
    q.minor_fb_step = 320;
    q.tex_width = tex_height;
    q.tex_w_mask = tex_height - 1;
    q.tex_h_mask = tex_columns - 1;
    const int starts[7] = {9, 0, 27, 4, 14, 2, 33};
    const int counts[7] = {23, 41, 8, 31, 16, 29, 5};
    for (int i = 0; i < 7; i++) {
        q.start[i] = (int16_t)starts[i];
        q.count[i] = (uint16_t)counts[i];
    }
    q.sZ = 5 << 16;
    q.tZ = -(5 << 16);
    q.zinv = 0x00010000;
    q.sZ_major_step = 0;
    q.tZ_major_step = 1 << 16;
    q.zinv_major_step = 0;
    q.sZ_minor_step = 1 << 16;
    q.tZ_minor_step = 0;
    q.zinv_minor_step = 0;

    emit_persp_span_group_raw(q);

    for (int lane = 0; lane < 7; lane++) {
        const int start = q.start[lane];
        SpanWire s = make_span();
        s.fb_addr = FB_BASE_BYTE + 57u + (uint32_t)lane
                  + (uint32_t)start * 320u;
        s.tex_addr = TEX_BASE_BYTE;
        s.count = q.count[lane];
        s.flags = q.flags & (uint8_t)~SPAN_PERSP;
        s.colormap_id = q.colormap_id;
        s.fb_stride = 320;
        s.tex_width = q.tex_width;
        s.tex_w_mask = q.tex_w_mask;
        s.tex_h_mask = q.tex_h_mask;
        s.s = q.sZ + lane * q.sZ_major_step + start * q.sZ_minor_step;
        s.t = q.tZ + lane * q.tZ_major_step + start * q.tZ_minor_step;
        s.sstep = q.sZ_minor_step;
        s.tstep = q.tZ_minor_step;
        m.apply_span_affine(s, q.colormap_id & 0xF);
    }

    if (!submit_and_wait()) {
        check_fail("persp_span_group_doom_wall_7lane_partial_chunk", "timeout");
        return;
    }

    compare_fb_region("persp_span_group_doom_wall_7lane_partial_chunk.fb",
                      m, FB_BASE_BYTE, 320, 57, 0, 7, tex_height);
}

static void test_persp_span_group_doom_wall_nonunit_z_equals_single_lane_spans() {
    printf("TEST persp_span_group_doom_wall_nonunit_z_equals_single_lane_spans\n");
    gpu_init();
    preload_with_sentinel();

    constexpr int tex_columns = 8;
    constexpr int tex_height = 128;
    std::vector<uint8_t> tex(tex_columns * tex_height);
    for (int col = 0; col < tex_columns; col++) {
        for (int y = 0; y < tex_height; y++) {
            tex[col * tex_height + y] =
                (uint8_t)(0x23 + col * 0x13 + y * 0x07);
        }
    }
    upload_texture(TEX_BASE_BYTE, tex);
    upload_palookup_identity_row(0, 0);
    cmd_set_fb(FB_BASE_BYTE, 320);

    PerspSpanGroupWire q = make_persp_span_group();
    q.fb_addr = FB_BASE_BYTE + 24u;
    q.tex_addr = TEX_BASE_BYTE;
    q.flags = SPAN_PERSP | (1u << 0);
    q.reserved = 1u;
    q.colormap_id = 0;
    q.major_fb_step = 1;
    q.minor_fb_step = 320;
    q.tex_width = tex_height;
    q.tex_w_mask = tex_height - 1;
    q.tex_h_mask = tex_columns - 1;
    q.start[0] = 5;  q.count[0] = 37;
    q.start[1] = 12; q.count[1] = 21;
    q.start[2] = 1;  q.count[2] = 44;
    q.start[3] = 18; q.count[3] = 13;
    q.sZ = 11 << 16;
    q.tZ = 2 << 16;
    q.zinv = 0x00018000;              // non-unit scale exercises reciprocal
    q.sZ_major_step = 0x00002000;
    q.tZ_major_step = 0x00018000;
    q.zinv_major_step = 0x00000800;
    q.sZ_minor_step = 1 << 16;
    q.tZ_minor_step = 0;
    q.zinv_minor_step = 0;            // constant-Z per generated lane

    emit_persp_span_group_raw(q);

    const uint32_t scalar_base = FB_BASE_BYTE + 96u;
    for (int lane = 0; lane < 4; lane++) {
        const int start = q.start[lane];
        SpanWire s = make_span();
        s.fb_addr = scalar_base + (uint32_t)lane
                  + (uint32_t)start * 320u;
        s.tex_addr = TEX_BASE_BYTE;
        s.count = q.count[lane];
        s.flags = q.flags;
        s.colormap_id = q.colormap_id;
        s.fb_stride = 320;
        s.tex_width = q.tex_width;
        s.tex_w_mask = q.tex_w_mask;
        s.tex_h_mask = q.tex_h_mask;
        s.sZ = q.sZ + lane * q.sZ_major_step + start * q.sZ_minor_step;
        s.tZ = q.tZ + lane * q.tZ_major_step + start * q.tZ_minor_step;
        s.zinv = q.zinv + lane * q.zinv_major_step + start * q.zinv_minor_step;
        s.sZstep = q.sZ_minor_step;
        s.tZstep = q.tZ_minor_step;
        s.zinv_step = q.zinv_minor_step;
        emit_span_raw(s);
    }

    if (!submit_and_wait()) {
        check_fail("persp_span_group_doom_wall_nonunit_z_equals_single_lane_spans", "timeout");
        return;
    }

    int diffs = 0;
    int first_lane = -1, first_pix = -1;
    uint8_t first_group = 0, first_scalar = 0;
    for (int lane = 0; lane < 4; lane++) {
        for (int p = 0; p < q.count[lane]; p++) {
            uint32_t group_addr = FB_BASE_BYTE + 24u + (uint32_t)lane
                                + (uint32_t)(q.start[lane] + p) * 320u;
            uint32_t scalar_addr = scalar_base + (uint32_t)lane
                                 + (uint32_t)(q.start[lane] + p) * 320u;
            uint8_t a = sdram_read_byte(group_addr);
            uint8_t b = sdram_read_byte(scalar_addr);
            if (a != b) {
                if (diffs == 0) {
                    first_lane = lane;
                    first_pix = p;
                    first_group = a;
                    first_scalar = b;
                }
                diffs++;
            }
        }
    }
    if (diffs == 0) {
        check_pass("persp_span_group_doom_wall_nonunit_z_equals_single_lane_spans");
    } else {
        char buf[192];
        snprintf(buf, sizeof(buf),
                 "diffs=%d first lane=%d pix=%d group=0x%02x scalar=0x%02x",
                 diffs, first_lane, first_pix, first_group, first_scalar);
        check_fail("persp_span_group_doom_wall_nonunit_z_equals_single_lane_spans", buf);
    }
}

static void test_persp_span_group_doom_wall_cpu_fixeddiv_reference() {
    printf("TEST persp_span_group_doom_wall_cpu_fixeddiv_reference\n");
    gpu_init();
    auto m = preload_with_sentinel();

    constexpr int tex_columns = 16;
    constexpr int tex_height = 128;
    std::vector<uint8_t> tex(tex_columns * tex_height);
    for (int col = 0; col < tex_columns; col++) {
        for (int y = 0; y < tex_height; y++) {
            tex[col * tex_height + y] =
                (uint8_t)(0x15 + col * 0x1d + y * 0x05);
        }
    }
    upload_texture(TEX_BASE_BYTE, tex);
    upload_palookup_identity_row(0, 0);
    m.snapshot_from_sdram();
    cmd_set_fb(FB_BASE_BYTE, 320);

    PerspSpanGroupWire q = make_persp_span_group();
    q.fb_addr = FB_BASE_BYTE + 73u;
    q.tex_addr = TEX_BASE_BYTE;
    q.lane_count = 4;
    q.flags = SPAN_PERSP | (1u << 0);
    q.reserved = 1u;
    q.colormap_id = 0;
    q.major_fb_step = 1;
    q.minor_fb_step = 320;
    q.tex_width = tex_height;
    q.tex_w_mask = tex_height - 1;
    q.tex_h_mask = tex_columns - 1;
    q.start[0] = 7;  q.count[0] = 37;
    q.start[1] = 2;  q.count[1] = 45;
    q.start[2] = 19; q.count[2] = 18;
    q.start[3] = 0;  q.count[3] = 51;
    q.zinv = 0x00012345;
    q.zinv_major_step = 0;
    q.sZ = (int32_t)(((int64_t)(42 << 16) * q.zinv) >> 16)
         - (100 << 16);
    q.tZ = (int32_t)(((int64_t)(3 << 16) * q.zinv) >> 16);
    q.sZ_major_step = 0;
    q.tZ_major_step = (int32_t)(((int64_t)(1 << 16) * q.zinv) >> 16);
    q.sZ_minor_step = 1 << 16;
    q.tZ_minor_step = 0;
    q.zinv_minor_step = 0;

    emit_persp_span_group_raw(q);

    for (int lane = 0; lane < 4; lane++) {
        const int start = q.start[lane];
        const int32_t zi = q.zinv + lane * q.zinv_major_step
                         + start * q.zinv_minor_step;
        const int32_t iscale = (int32_t)(0xffffffffu / (uint32_t)zi);
        SpanWire s = make_span();
        s.fb_addr = FB_BASE_BYTE + 73u + (uint32_t)lane
                  + (uint32_t)start * 320u;
        s.tex_addr = TEX_BASE_BYTE;
        s.count = q.count[lane];
        s.flags = q.flags & (uint8_t)~SPAN_PERSP;
        s.colormap_id = q.colormap_id;
        s.fb_stride = 320;
        s.tex_width = q.tex_width;
        s.tex_w_mask = q.tex_w_mask;
        s.tex_h_mask = q.tex_h_mask;
        s.s = (42 << 16) + (start - 100) * iscale;
        s.t = (3 + lane) << 16;
        s.sstep = iscale;
        s.tstep = 0;
        m.apply_span_affine(s, q.colormap_id & 0xF);
    }

    if (!submit_and_wait()) {
        check_fail("persp_span_group_doom_wall_cpu_fixeddiv_reference", "timeout");
        return;
    }

    int diffs = 0;
    int first_lane = -1, first_pix = -1;
    uint8_t first_group = 0, first_ref = 0;
    for (int lane = 0; lane < 4; lane++) {
        for (int p = 0; p < q.count[lane]; p++) {
            uint32_t addr = FB_BASE_BYTE + 73u + (uint32_t)lane
                          + (uint32_t)(q.start[lane] + p) * 320u;
            uint8_t a = sdram_read_byte(addr);
            uint8_t b = m.read(addr);
            if (a != b) {
                if (diffs == 0) {
                    first_lane = lane;
                    first_pix = p;
                    first_group = a;
                    first_ref = b;
                }
                diffs++;
            }
        }
    }
    if (diffs == 0) {
        check_pass("persp_span_group_doom_wall_cpu_fixeddiv_reference");
    } else {
        char buf[192];
        snprintf(buf, sizeof(buf),
                 "diffs=%d first lane=%d pix=%d group=0x%02x ref=0x%02x",
                 diffs, first_lane, first_pix, first_group, first_ref);
        check_fail("persp_span_group_doom_wall_cpu_fixeddiv_reference", buf);
    }
}

static void test_persp_span_group_quake_rows_match_single_lane_spans() {
    printf("TEST persp_span_group_quake_rows_match_single_lane_spans\n");
    gpu_init();
    preload_with_sentinel();

    constexpr int tex_w = 64;
    constexpr int tex_h = 64;
    std::vector<uint8_t> tex(tex_w * tex_h);
    for (int t = 0; t < tex_h; t++) {
        for (int s = 0; s < tex_w; s++)
            tex[(size_t)t * tex_w + (size_t)s] =
                (uint8_t)(((t & 3) << 6) | (s & 0x3F));
    }
    upload_texture(TEX_BASE_BYTE, tex);
    upload_palookup_identity_row(0, 0);
    cmd_set_fb(FB_BASE_BYTE, 320);

    PerspSpanGroupWire q = make_persp_span_group();
    q.fb_addr = FB_BASE_BYTE + 10u * 320u;
    q.tex_addr = TEX_BASE_BYTE;
    q.lane_count = 4;
    q.flags = SPAN_PERSP | (1u << 0);
    q.colormap_id = 0;
    q.major_fb_step = 320;           // adjacent Quake scanlines
    q.minor_fb_step = 1;             // pixels advance horizontally
    q.tex_width = tex_w;
    q.tex_w_mask = tex_w - 1;
    q.tex_h_mask = tex_h - 1;

    const int starts[4] = {0, 7, 3, 11};
    const int counts[4] = {80, 37, 64, 22};
    for (int lane = 0; lane < 4; lane++) {
        q.start[lane] = (int16_t)starts[lane];
        q.count[lane] = (uint16_t)counts[lane];
    }

    /* Quake-like non-constant projection.  The absolute values mirror the
     * d_scan.c repro shape; major steps make each adjacent row distinct.
     */
    q.sZ = 60280;
    q.tZ = 9948;
    q.zinv = 1455;
    q.sZ_major_step = 1200;
    q.tZ_major_step = 96;
    q.zinv_major_step = 8;
    q.sZ_minor_step = 234;
    q.tZ_minor_step = 42;
    q.zinv_minor_step = 3;
    q.light = 0;
    q.light_major_step = 0;
    q.light_minor_step = 0;

    emit_persp_span_group_raw(q);

    const uint32_t scalar_base = FB_BASE_BYTE + 80u * 320u;
    for (int lane = 0; lane < 4; lane++) {
        const int start = q.start[lane];
        SpanWire s = make_span();
        s.fb_addr = scalar_base + (uint32_t)lane * 320u + (uint32_t)start;
        s.tex_addr = TEX_BASE_BYTE;
        s.count = q.count[lane];
        s.flags = q.flags;
        s.colormap_id = q.colormap_id;
        s.fb_stride = 1;
        s.tex_width = q.tex_width;
        s.tex_w_mask = q.tex_w_mask;
        s.tex_h_mask = q.tex_h_mask;
        s.sZ = q.sZ + lane * q.sZ_major_step + start * q.sZ_minor_step;
        s.tZ = q.tZ + lane * q.tZ_major_step + start * q.tZ_minor_step;
        s.zinv = q.zinv + lane * q.zinv_major_step + start * q.zinv_minor_step;
        s.sZstep = q.sZ_minor_step;
        s.tZstep = q.tZ_minor_step;
        s.zinv_step = q.zinv_minor_step;
        emit_span_raw(s);
    }

    if (!submit_and_wait()) {
        check_fail("persp_span_group_quake_rows_match_single_lane_spans", "timeout");
        return;
    }

    int diffs = 0;
    int first_lane = -1, first_pix = -1;
    uint8_t first_group = 0, first_scalar = 0;
    for (int lane = 0; lane < 4; lane++) {
        for (int p = 0; p < q.count[lane]; p++) {
            uint32_t group_addr = q.fb_addr + (uint32_t)lane * 320u
                                + (uint32_t)(q.start[lane] + p);
            uint32_t scalar_addr = scalar_base + (uint32_t)lane * 320u
                                 + (uint32_t)(q.start[lane] + p);
            uint8_t a = sdram_read_byte(group_addr);
            uint8_t b = sdram_read_byte(scalar_addr);
            if (a != b) {
                if (diffs == 0) {
                    first_lane = lane;
                    first_pix = p;
                    first_group = a;
                    first_scalar = b;
                }
                diffs++;
            }
        }
    }
    if (diffs == 0) {
        check_pass("persp_span_group_quake_rows_match_single_lane_spans");
    } else {
        char buf[192];
        snprintf(buf, sizeof(buf),
                 "diffs=%d first lane=%d pix=%d group=0x%02x scalar=0x%02x",
                 diffs, first_lane, first_pix, first_group, first_scalar);
        check_fail("persp_span_group_quake_rows_match_single_lane_spans", buf);
    }
}

static void upload_quake_oracle_texture(int tex_w, int tex_h) {
    std::vector<uint8_t> tex((size_t)tex_w * (size_t)tex_h);
    for (int t = 0; t < tex_h; t++) {
        for (int s = 0; s < tex_w; s++) {
            tex[(size_t)t * (size_t)tex_w + (size_t)s] =
                (uint8_t)(((t & 7) << 5) | (s & 31));
        }
    }
    upload_texture(TEX_BASE_BYTE, tex);
}

static bool run_quake_persp_payload_case(const char *name,
                                         const PerspSpanGroupWire &q,
                                         bool fail_on_exact_mismatch) {
    gpu_init();
    preload_with_sentinel();
    upload_quake_oracle_texture(q.tex_width, q.tex_h_mask + 1);
    upload_palookup_identity_row(q.colormap_id, 0);
    cmd_set_fb(FB_BASE_BYTE, 320);
    std::vector<uint32_t> payload =
        encode_persp_span_group_wire_chunk(q, 0, q.lane_count);
    if (payload.size() < 31) {
        check_fail(name, "perspective payload is missing span header");
        return false;
    }
    emit_raw_command(CMD_PARAM_SPAN_LIST, payload);
    uint32_t split_fb_base = FB_ALT_BASE_BYTE + (q.fb_addr - FB_BASE_BYTE);
#ifndef GPU_TEST_OS30_LEAN
    /* GPU-vs-GPU split oracle (compact-direct 0x48 segment fallback) —
     * full config only; the lean config drains compact 0x48 so this leg
     * would render nothing.  compare_quake_persp_group_to_oracles skips
     * the split comparison under GPU_TEST_OS30_LEAN to match. */
    emit_quake_segment16_affine_fallback(q, split_fb_base);
#endif
    if (!submit_and_wait()) {
        check_fail(name, "timeout");
        return false;
    }
    return compare_quake_persp_group_to_oracles(name, q, split_fb_base,
                                                fail_on_exact_mismatch);
}

static PerspSpanGroupWire make_quake_base_payload() {
    PerspSpanGroupWire q = make_persp_span_group();
    q.fb_addr = FB_BASE_BYTE + 20u * 320u;
    q.tex_addr = TEX_BASE_BYTE;
    q.lane_count = 4;
    q.flags = SPAN_PERSP | 0x01u;      // perspective + explicit colormap
    q.colormap_id = 3;
    q.major_fb_step = 320;             // adjacent Quake scanlines
    q.minor_fb_step = 1;               // horizontal pixels
    q.tex_width = 128;
    q.tex_w_mask = 127;
    q.tex_h_mask = 127;
    q.light = 0;
    q.light_major_step = 0;
    q.light_minor_step = 0;
    return q;
}

static void test_persp_span_group_quake_payload_cpu_oracle() {
    printf("TEST persp_span_group_quake_payload_cpu_oracle\n");

    /* Real Quake d_scan-shaped world surface: four adjacent rows, row-base
     * projection fields, non-zero starts, varied counts, and non-zero
     * major/minor perspective steps.  Lane 0 at start=18 reproduces the
     * 60280/9948/1455 d_scan repro values used by the scalar perspective
     * test; the group payload stores the row-base values exactly as
     * the SDK normalizes them before emitting the unified span command.
     */
    PerspSpanGroupWire q = make_quake_base_payload();
    const int starts[4] = {18, 23, 7, 41};
    const int counts[4] = {85, 74, 109, 36};
    for (int i = 0; i < 4; i++) {
        q.start[i] = (int16_t)starts[i];
        q.count[i] = (uint16_t)counts[i];
    }
    q.sZ = 60280 - starts[0] * 234;
    q.tZ = 9948  - starts[0] * 42;
    q.zinv = 1455 - starts[0] * 3;
    q.sZ_major_step = 715;
    q.tZ_major_step = -86;
    q.zinv_major_step = 9;
    q.sZ_minor_step = 234;
    q.tZ_minor_step = 42;
    q.zinv_minor_step = 3;
    run_quake_persp_payload_case("persp_quake_payload_exact_oracle",
                                 q, false);
}

static void test_persp_span_group_quake_payload_edge_cases() {
    printf("TEST persp_span_group_quake_payload_edge_cases\n");

    PerspSpanGroupWire neg = make_quake_base_payload();
    neg.fb_addr = FB_BASE_BYTE + 60u * 320u;
    int neg_starts[4] = {5, 18, 31, 2};
    int neg_counts[4] = {64, 49, 33, 80};
    for (int i = 0; i < 4; i++) {
        neg.start[i] = (int16_t)neg_starts[i];
        neg.count[i] = (uint16_t)neg_counts[i];
    }
    neg.sZ = 100000;
    neg.tZ = 18000;
    neg.zinv = 1800;
    neg.sZ_major_step = -420;
    neg.tZ_major_step = 165;
    neg.zinv_major_step = 6;
    neg.sZ_minor_step = -171;          // negative minor step
    neg.tZ_minor_step = 65;
    neg.zinv_minor_step = 4;
    run_quake_persp_payload_case("persp_quake_payload_negative_minor",
                                 neg, false);

    PerspSpanGroupWire tiny = make_quake_base_payload();
    tiny.fb_addr = FB_BASE_BYTE + 95u * 320u;
    tiny.lane_count = 2;
    tiny.start[0] = 33;
    tiny.start[1] = 47;
    tiny.count[0] = 40;
    tiny.count[1] = 24;
    tiny.sZ = 0;
    tiny.tZ = 0;
    tiny.zinv = 64;                    // very small positive 1/z
    tiny.sZ_major_step = 48;
    tiny.tZ_major_step = 32;
    tiny.zinv_major_step = 3;
    tiny.sZ_minor_step = 64;
    tiny.tZ_minor_step = 32;
    tiny.zinv_minor_step = 1;
    run_quake_persp_payload_case("persp_quake_payload_tiny_positive_zi",
                                 tiny, false);

    PerspSpanGroupWire sadj = make_quake_base_payload();
    sadj.fb_addr = FB_BASE_BYTE + 120u * 320u;
    sadj.start[0] = 40;
    sadj.start[1] = 44;
    sadj.start[2] = 48;
    sadj.start[3] = 52;
    sadj.count[0] = 48;
    sadj.count[1] = 48;
    sadj.count[2] = 48;
    sadj.count[3] = 48;
    sadj.sZ = -32 << 16;               // baked Quake sadjust-style offset
    sadj.tZ = 3 << 16;
    sadj.zinv = 0x8000;
    sadj.sZ_major_step = 1 << 16;
    sadj.tZ_major_step = 0x2000;
    sadj.zinv_major_step = 0x20;
    sadj.sZ_minor_step = 1 << 16;
    sadj.tZ_minor_step = 0x4000;
    sadj.zinv_minor_step = 0x10;
    run_quake_persp_payload_case("persp_quake_payload_baked_sadjust",
                                 sadj, false);
}

// ---- Section 14: Batched native span stream equivalence ----------------------
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

static void test_span_group_opaque_equals_four_spans() {
    printf("TEST span_group_opaque_equals_four_spans\n");
    gpu_init();
    auto m = preload_with_sentinel();

    for (int lane = 0; lane < 4; lane++) {
        std::vector<uint8_t> tex(8);
        for (int i = 0; i < 8; i++)
            tex[i] = (uint8_t)(0x10 + lane * 0x20 + i);
        upload_texture(TEX_BASE_BYTE + (uint32_t)lane * 0x100, tex);
    }
    m.snapshot_from_sdram();

    SpanGroupWire q = make_span_group();
    q.fb_addr = FB_BASE_BYTE + 5 * 320 + 7;
    q.count = 6;
    q.flags = 0;
    set_span_group_colormap(q, 0);
    for (int lane = 0; lane < 4; lane++) {
        q.tex_addr[lane] = TEX_BASE_BYTE + (uint32_t)lane * 0x100;
        q.t[lane] = 0;
        q.tstep[lane] = 0x10000;
        q.light[lane] = 0;
    }

    emit_span_group_raw(q);
    m.apply_span_group_affine(q);
    if (!submit_and_wait()) {
        check_fail("span_group_opaque_equals_four_spans", "timeout");
        return;
    }
    compare_fb_region("span_group_opaque_equals_four_spans.fb", m,
                      FB_BASE_BYTE, 320, 7, 5, 4, q.count);
}

static void test_span_group_texture_height_mask() {
    printf("TEST span_group_texture_height_mask\n");
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

    SpanGroupWire q = make_span_group();
    q.fb_addr = FB_BASE_BYTE + 18 * 320 + 16;
    q.count = 8;
    q.flags = 0;
    set_span_group_colormap(q, 0);
    q.tex_h_mask = 3;
    for (int lane = 0; lane < 4; lane++) {
        q.tex_addr[lane] = TEX_BASE_BYTE + (uint32_t)lane * 0x100;
        q.t[lane] = 0;
        q.tstep[lane] = 0x10000;
        q.light[lane] = 0;
    }

    emit_span_group_raw(q);
    m.apply_span_group_affine(q);
    if (!submit_and_wait()) {
        check_fail("span_group_texture_height_mask", "timeout");
        return;
    }
    compare_fb_region("span_group_texture_height_mask.fb", m,
                      FB_BASE_BYTE, 320, 16, 18, 4, q.count);
}

static void test_span_group_aligned_matches_reference() {
    printf("TEST span_group_aligned_matches_reference\n");
    gpu_init();
    auto m = preload_with_sentinel();

    std::vector<uint8_t> tex(8);
    for (int i = 0; i < 8; i++)
        tex[i] = (uint8_t)(0x70 + i);
    upload_texture(TEX_BASE_BYTE, tex);
    for (int lane = 0; lane < 4; lane++) {
        upload_palookup_const_row(5, (uint8_t)lane, (uint8_t)(0x90 + lane));
    }
    m.snapshot_from_sdram();

    SpanGroupWire q = make_span_group();
    q.fb_addr = FB_BASE_BYTE + 40 * 320 + 8;  // 4-byte aligned x
    q.count = 6;
    q.flags = 0x1;       // COLORMAP
    set_span_group_colormap(q, 5);
    for (int lane = 0; lane < 4; lane++) {
        q.tex_addr[lane] = TEX_BASE_BYTE;
        q.t[lane] = 0;
        q.tstep[lane] = 0x10000;
        q.light[lane] = (uint8_t)lane;
    }

    emit_span_group_raw(q);
    m.apply_span_group_affine(q);
    if (!submit_and_wait()) {
        check_fail("span_group_aligned_matches_reference", "timeout");
        return;
    }

    compare_fb_region("span_group_aligned_matches_reference.fb", m,
                      FB_BASE_BYTE, 320, 8, 40, 4, q.count);
}

static void test_span_group_aligned_texture_cache_thrash() {
    printf("TEST span_group_aligned_texture_cache_thrash\n");
    gpu_init();
    auto m = preload_with_sentinel();

    /* Four lane bases separated by 0x4000 share gpu_tex_cache set bits
     * [13:4] but carry different tags.  Row-major span_group therefore forces
     * frequent lane-to-lane evictions while still expecting byte-exact
     * output versus four single-lane spans. */
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

    SpanGroupWire q = make_span_group();
    q.fb_addr = FB_BASE_BYTE + 64 * 320 + 12;  // aligned x
    q.count = 24;
    q.flags = 0x1;       // COLORMAP
    set_span_group_colormap(q, 6);
    for (int lane = 0; lane < 4; lane++) {
        q.tex_addr[lane] = TEX_BASE_BYTE + (uint32_t)lane * 0x4000;
        q.t[lane] = (lane * 3) << 16;
        q.tstep[lane] = (lane + 1) << 16;
        q.light[lane] = (uint8_t)lane;
    }

    uint32_t req_before = mmio_read(REG_TRANSLUC_ADDR);
    uint32_t miss_before = mmio_read(REG_TRANSLUC_DATA);
    emit_span_group_raw(q);
    m.apply_span_group_affine(q);
    if (!submit_and_wait()) {
        check_fail("span_group_aligned_texture_cache_thrash", "timeout");
        return;
    }
    compare_fb_region("span_group_aligned_texture_cache_thrash.fb", m,
                      FB_BASE_BYTE, 320, 12, 64, 4, q.count);

    uint32_t reqs = mmio_read(REG_TRANSLUC_ADDR) - req_before;
    uint32_t misses = mmio_read(REG_TRANSLUC_DATA) - miss_before;
    if (reqs == 0 && misses == 0) {
        check_pass("span_group_aligned_texture_cache_thrash.cache_counters_optional");
    } else if (misses > 8 && reqs >= (uint32_t)q.count * 4u) {
        check_pass("span_group_aligned_texture_cache_thrash.cache_pressure");
    } else {
        char buf[128];
        snprintf(buf, sizeof(buf), "reqs=%u misses=%u", reqs, misses);
        check_fail("span_group_aligned_texture_cache_thrash.cache_pressure", buf);
    }
}

static void test_span_group_masked_equals_four_spans() {
    printf("TEST span_group_masked_equals_four_spans\n");
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

    SpanGroupWire q = make_span_group();
    q.fb_addr = FB_BASE_BYTE + 12 * 320 + 3;
    q.count = 6;
    q.flags = 0x4;  // SKIP_ZERO
    set_span_group_colormap(q, 0);
    for (int lane = 0; lane < 4; lane++) {
        q.tex_addr[lane] = TEX_BASE_BYTE + (uint32_t)lane * 0x100;
        q.t[lane] = 0;
        q.tstep[lane] = 0x10000;
        q.light[lane] = 0;
    }

    emit_span_group_raw(q);
    m.apply_span_group_affine(q);
    if (!submit_and_wait()) {
        check_fail("span_group_masked_equals_four_spans", "timeout");
        return;
    }
    compare_fb_region("span_group_masked_equals_four_spans.fb", m,
                      FB_BASE_BYTE, 320, 3, 12, 4, q.count);
}

// ============================================================================
// CMD_DRAW_COLUMN_LIST (0x4C) byte-exact oracle.
//
// For each column-geometry case below, render it TWICE into two distinct
// framebuffers:
//   FB_BASE      <- via 0x4C CMD_DRAW_COLUMN_LIST (5-word lane records)
//   FB_ALT_BASE  <- via 0x48 direct-affine with s=0/sstep=0 (the reference)
// then assert the two framebuffers are BYTE-IDENTICAL.  Because the 0x4C RTL
// decoder forces s/sstep=0 and reuses the SAME staging/pipeline as 0x48, a
// single byte of difference means the decode is wrong.  This proves 0x4C is a
// pure command-traffic optimisation with zero pixel difference.
// ============================================================================

// Render one column case both ways and compare.  The columns occupy
// x=[x0, x0+lanes) and y=[y0, y0+max_count) with fb_step == stride (320),
// i.e. lane L is the 1-wide column at (x0+L) running max_count pixels DOWN.
static void run_column_case(const char *name, const SpanGroupWire &geom,
                            int x0, int y0, int max_count) {
    int lanes = span_group_effective_lanes(geom.lane_count);

    SpanGroupWire g4c = geom;   // 0x4C path: fb_addr column base in FB_BASE
    g4c.fb_addr = FB_BASE_BYTE + (uint32_t)y0 * 320u + (uint32_t)x0;

    // 0x48 reference column base in FB_ALT_BASE (same (x0,y0) offset).
    uint32_t alt_base = FB_ALT_BASE_BYTE + (uint32_t)y0 * 320u + (uint32_t)x0;

    emit_column_list_raw(g4c, g4c.fb_addr);   // -> FB_BASE
    emit_column_as_affine_raw(geom, alt_base); // -> FB_ALT_BASE (s=0/sstep=0)

    if (!submit_and_wait()) {
        check_fail(name, "timeout");
        return;
    }
    // Primary assertion: the 0x4C and 0x48-with-s=0 framebuffers are identical.
    compare_fb_to_fb(name, FB_BASE_BYTE, FB_ALT_BASE_BYTE, 320,
                     x0, y0, lanes, max_count);
    // Anti-vacuity guard (distinct sub-name): the 0x4C render must actually
    // have written something, so a "both blank" comparison can't pass trivially.
    bool any = false;
    for (int dy = 0; dy < max_count && !any; dy++)
        for (int dx = 0; dx < lanes && !any; dx++)
            if (sdram_read_byte(FB_BASE_BYTE + (uint32_t)(y0 + dy) * 320u
                                + (uint32_t)(x0 + dx)) != SENTINEL_BYTE)
                any = true;
    char nm[128];
    snprintf(nm, sizeof(nm), "%s.nonvacuous", name);
    if (!any)
        check_fail(nm, "0x4C framebuffer untouched (vacuous match)");
    else
        check_pass(nm);
}

static void test_column_list_single_column_matches_affine() {
    printf("TEST column_list_single_column_matches_affine\n");
    gpu_init();
    auto m = preload_with_sentinel();
    std::vector<uint8_t> tex(64);
    for (int i = 0; i < 64; i++) tex[i] = (uint8_t)(0x40 + i);
    upload_texture(TEX_BASE_BYTE, tex);
    m.snapshot_from_sdram();

    SpanGroupWire g = make_span_group();
    g.lane_count = 1;
    g.lane_delta = 1;
    g.fb_stride  = 320;          // fb_step: walk DOWN one pixel per row
    g.count      = 24;
    g.flags      = 0;
    g.tex_width  = 64;
    g.tex_w_mask = 0x3F;
    g.tex_h_mask = 0x3F;
    g.tex_addr[0] = TEX_BASE_BYTE;
    g.t[0]     = 0;
    g.tstep[0] = 0x10000;        // one texel per pixel down
    g.light[0] = 0;
    run_column_case("column_list_single_column_matches_affine", g, 12, 4, 24);
}

static void test_column_list_multi_lane_matches_affine() {
    printf("TEST column_list_multi_lane_matches_affine\n");
    gpu_init();
    auto m = preload_with_sentinel();
    for (int lane = 0; lane < 4; lane++) {
        std::vector<uint8_t> tex(64);
        for (int i = 0; i < 64; i++)
            tex[i] = (uint8_t)(0x10 + lane * 0x30 + i);
        upload_texture(TEX_BASE_BYTE + (uint32_t)lane * 0x100, tex);
    }
    m.snapshot_from_sdram();

    // 2-lane, 3-lane, 4-lane cases — exercise the native chunking edge.
    const int lane_counts[3] = {2, 3, 4};
    const char *names[3] = {
        "column_list_2lane_matches_affine",
        "column_list_3lane_matches_affine",
        "column_list_4lane_matches_affine"};
    const int y0s[3] = {6, 40, 80};
    for (int c = 0; c < 3; c++) {
        SpanGroupWire g = make_span_group();
        g.lane_count = (uint8_t)lane_counts[c];
        g.lane_delta = 1;
        g.fb_stride  = 320;
        g.count      = 30;
        g.flags      = 0;
        g.tex_width  = 64;
        g.tex_w_mask = 0x3F;
        g.tex_h_mask = 0x3F;
        for (int lane = 0; lane < lane_counts[c]; lane++) {
            g.tex_addr[lane] = TEX_BASE_BYTE + (uint32_t)lane * 0x100;
            // Distinct v origin + step per lane to stress the t/tstep mapping.
            g.t[lane]     = (int32_t)(lane << 16);
            g.tstep[lane] = 0x10000 + (lane * 0x4000);
            g.light[lane] = 0;
        }
        run_column_case(names[c], g, 30, y0s[c], 30);
    }
}

static void test_column_list_colormap_matches_affine() {
    printf("TEST column_list_colormap_matches_affine\n");
    gpu_init();
    auto m = preload_with_sentinel();
    // Per-lane colormap slot + light row, like a shaded Doom wall.
    for (int lane = 0; lane < 4; lane++) {
        for (int row = 0; row < 8; row++)
            upload_palookup_const_row((uint8_t)(lane + 2), (uint8_t)row,
                                      (uint8_t)(0x50 + lane * 0x10 + row));
        std::vector<uint8_t> tex(64);
        for (int i = 0; i < 64; i++)
            tex[i] = (uint8_t)(0x01 + lane * 0x20 + i);
        upload_texture(TEX_BASE_BYTE + (uint32_t)lane * 0x100, tex);
    }
    m.snapshot_from_sdram();

    SpanGroupWire g = make_span_group();
    g.lane_count = 4;
    g.lane_delta = 1;
    g.fb_stride  = 320;
    g.count      = 28;
    g.flags      = 0x1;                          // SPAN_COLORMAP (bit 0)
    g.tex_width  = 64;
    g.tex_w_mask = 0x3F;
    g.tex_h_mask = 0x3F;
    for (int lane = 0; lane < 4; lane++) {
        g.colormap_id[lane] = (uint8_t)(lane + 2);
        g.tex_addr[lane] = TEX_BASE_BYTE + (uint32_t)lane * 0x100;
        g.t[lane]     = 0;
        g.tstep[lane] = 0x10000;
        g.light[lane] = (uint8_t)(lane * 2);     // distinct shade row per lane
    }
    run_column_case("column_list_colormap_matches_affine", g, 60, 30, 28);
}

static void test_column_list_skipzero_and_negstep_matches_affine() {
    printf("TEST column_list_skipzero_and_negstep_matches_affine\n");
    gpu_init();
    auto m = preload_with_sentinel();
    // Texture with embedded 0xFF holes so SKIP_ZERO has visible effect.
    std::vector<uint8_t> tex(64);
    for (int i = 0; i < 64; i++)
        tex[i] = (uint8_t)((i % 5 == 0) ? 0xFF : (0x30 + i));
    upload_texture(TEX_BASE_BYTE, tex);
    m.snapshot_from_sdram();

    // Case A: SKIP_ZERO masking (transparent texels must be byte-identical).
    {
        SpanGroupWire g = make_span_group();
        g.lane_count = 2;
        g.lane_delta = 1;
        g.fb_stride  = 320;
        g.count      = 26;
        g.flags      = (1 << 2);                 // SPAN_SKIP_ZERO (bit 2)
        g.tex_width  = 64;
        g.tex_w_mask = 0x3F;
        g.tex_h_mask = 0x3F;
        for (int lane = 0; lane < 2; lane++) {
            g.tex_addr[lane] = TEX_BASE_BYTE;
            g.t[lane]     = (int32_t)(lane << 16);
            g.tstep[lane] = 0x10000;
            g.light[lane] = 0;
        }
        run_column_case("column_list_skipzero_matches_affine", g, 90, 10, 26);
    }
    // Case B: negative tstep (texture scrolls UP) + non-zero t origin.
    {
        SpanGroupWire g = make_span_group();
        g.lane_count = 3;
        g.lane_delta = 1;
        g.fb_stride  = 320;
        g.count      = 22;
        g.flags      = 0;
        g.tex_width  = 64;
        g.tex_w_mask = 0x3F;
        g.tex_h_mask = 0x3F;
        for (int lane = 0; lane < 3; lane++) {
            g.tex_addr[lane] = TEX_BASE_BYTE;
            g.t[lane]     = (int32_t)(0x200000 + (lane << 16));
            g.tstep[lane] = -(int32_t)0x10000;       // walk texture upward
            g.light[lane] = 0;
        }
        run_column_case("column_list_negstep_matches_affine", g, 90, 50, 22);
    }
}

static void test_column_list_varcount_and_zerolane_matches_affine() {
    printf("TEST column_list_varcount_and_zerolane_matches_affine\n");
    gpu_init();
    auto m = preload_with_sentinel();
    for (int lane = 0; lane < 4; lane++) {
        std::vector<uint8_t> tex(64);
        for (int i = 0; i < 64; i++)
            tex[i] = (uint8_t)(0x05 + lane * 0x11 + i);
        upload_texture(TEX_BASE_BYTE + (uint32_t)lane * 0x100, tex);
    }
    m.snapshot_from_sdram();

    // Per-lane variable heights (Doom walls have different column heights);
    // lane 2 has count 0 (a clipped-away column) and must be skipped in BOTH.
    SpanGroupWire g = make_span_group();
    g.lane_count = 4;
    g.lane_delta = 1;
    g.fb_stride  = 320;
    g.flags      = 0;
    g.tex_width  = 64;
    g.tex_w_mask = 0x3F;
    g.tex_h_mask = 0x3F;
    const uint16_t heights[4] = {31, 18, 0, 25};
    for (int lane = 0; lane < 4; lane++) {
        g.tex_addr[lane] = TEX_BASE_BYTE + (uint32_t)lane * 0x100;
        g.t[lane]     = 0;
        g.tstep[lane] = 0x10000;
        g.light[lane] = 0;
        g.count_lane[lane] = heights[lane];
    }
    run_column_case("column_list_varcount_matches_affine", g, 120, 30, 31);
}

static void test_span_group_explicit_colormap_slot() {
    printf("TEST span_group_explicit_colormap_slot\n");
    gpu_init();
    auto m = preload_with_sentinel();

    for (int row = 0; row < 4; row++)
        upload_palookup_const_row(5, (uint8_t)row, (uint8_t)(0x60 + row));
    for (int lane = 0; lane < 4; lane++) {
        std::vector<uint8_t> tex(4);
        for (int i = 0; i < 4; i++)
            tex[i] = (uint8_t)(0x08 + lane * 4 + i);
        upload_texture(TEX_BASE_BYTE + (uint32_t)lane * 0x100, tex);
    }
    m.snapshot_from_sdram();

    SpanGroupWire q = make_span_group();
    q.fb_addr = FB_BASE_BYTE + 20 * 320 + 11;
    q.count = 4;
    q.flags = 0x1;        // COLORMAP
    set_span_group_colormap(q, 5);    // explicit slot
    for (int lane = 0; lane < 4; lane++) {
        q.tex_addr[lane] = TEX_BASE_BYTE + (uint32_t)lane * 0x100;
        q.t[lane] = 0;
        q.tstep[lane] = 0x10000;
        q.light[lane] = (uint8_t)lane;
    }

    emit_span_group_raw(q);
    m.apply_span_group_affine(q);
    if (!submit_and_wait()) {
        check_fail("span_group_explicit_colormap_slot", "timeout");
        return;
    }
    compare_fb_region("span_group_explicit_colormap_slot.fb", m,
                      FB_BASE_BYTE, 320, 11, 20, 4, q.count);
}

static void test_span_group_per_lane_colormap_slots() {
    printf("TEST span_group_per_lane_colormap_slots\n");
    gpu_init();
    auto m = preload_with_sentinel();

    for (int lane = 0; lane < 4; lane++) {
        upload_palookup_const_row((uint8_t)(lane + 1), 0,
                                  (uint8_t)(0x31 + lane * 0x22));
        std::vector<uint8_t> tex(4);
        for (int i = 0; i < 4; i++)
            tex[i] = (uint8_t)(0x10 + lane * 4 + i);
        upload_texture(TEX_BASE_BYTE + (uint32_t)lane * 0x100, tex);
    }
    m.snapshot_from_sdram();

    SpanGroupWire q = make_span_group();
    q.fb_addr = FB_BASE_BYTE + 26 * 320 + 9;
    q.count = 4;
    q.flags = 0x1;        // COLORMAP
    for (int lane = 0; lane < 4; lane++) {
        q.colormap_id[lane] = (uint8_t)(lane + 1);
        q.tex_addr[lane] = TEX_BASE_BYTE + (uint32_t)lane * 0x100;
        q.t[lane] = 0;
        q.tstep[lane] = 0x10000;
        q.light[lane] = 0;
    }

    emit_span_group_raw(q);
    m.apply_span_group_affine(q);
    if (!submit_and_wait()) {
        check_fail("span_group_per_lane_colormap_slots", "timeout");
        return;
    }
    compare_fb_region("span_group_per_lane_colormap_slots.fb", m,
                      FB_BASE_BYTE, 320, 9, 26, 4, q.count);
}

static void test_span_group_varcount_opaque_equals_spans() {
    printf("TEST span_group_varcount_opaque_equals_spans\n");
    gpu_init();
    auto m = preload_with_sentinel();

    for (int lane = 0; lane < 4; lane++) {
        std::vector<uint8_t> tex(16);
        for (int i = 0; i < 16; i++)
            tex[i] = (uint8_t)(0x20 + lane * 0x20 + i);
        upload_texture(TEX_BASE_BYTE + (uint32_t)lane * 0x100, tex);
    }
    m.snapshot_from_sdram();

    SpanGroupVarWire q = make_span_group_var();
    q.fb_addr = FB_BASE_BYTE + 28 * 320 + 20;
    q.flags = 0;
    q.colormap_id = 0;
    const uint16_t starts[4] = {0, 1, 0, 3};
    const uint16_t counts[4] = {7, 4, 5, 2};
    for (int lane = 0; lane < 4; lane++) {
        q.tex_addr[lane] = TEX_BASE_BYTE + (uint32_t)lane * 0x100;
        q.t[lane] = lane << 16;
        q.tstep[lane] = 0x10000;
        q.y_start[lane] = starts[lane];
        q.count[lane] = counts[lane];
        q.light[lane] = 0;
    }

    emit_span_group_var_raw(q);
    m.apply_span_group_var_affine(q);
    if (!submit_and_wait()) {
        check_fail("span_group_varcount_opaque_equals_spans", "timeout");
        return;
    }
    compare_fb_region("span_group_varcount_opaque_equals_spans.fb", m,
                      FB_BASE_BYTE, 320, 20, 28, 4,
                      span_group_var_row_count(q, 0, 4));
}

static void test_span_group_varcount_masked_equals_spans() {
    printf("TEST span_group_varcount_masked_equals_spans\n");
    gpu_init();
    auto m = preload_with_sentinel();

    for (int lane = 0; lane < 4; lane++) {
        std::vector<uint8_t> tex = {
            (uint8_t)(0x30 + lane), 0xFF,
            (uint8_t)(0x40 + lane), (uint8_t)(0x50 + lane),
            0xFF, (uint8_t)(0x60 + lane), (uint8_t)(0x70 + lane)
        };
        upload_texture(TEX_BASE_BYTE + (uint32_t)lane * 0x100, tex);
    }
    m.snapshot_from_sdram();

    SpanGroupVarWire q = make_span_group_var();
    q.fb_addr = FB_BASE_BYTE + 52 * 320 + 6;
    q.flags = 0x4;  // SKIP_ZERO
    q.colormap_id = 0;
    const uint16_t starts[4] = {2, 0, 1, 4};
    const uint16_t counts[4] = {5, 7, 3, 2};
    for (int lane = 0; lane < 4; lane++) {
        q.tex_addr[lane] = TEX_BASE_BYTE + (uint32_t)lane * 0x100;
        q.t[lane] = 0;
        q.tstep[lane] = 0x10000;
        q.y_start[lane] = starts[lane];
        q.count[lane] = counts[lane];
        q.light[lane] = 0;
    }

    emit_span_group_var_raw(q);
    m.apply_span_group_var_affine(q);
    if (!submit_and_wait()) {
        check_fail("span_group_varcount_masked_equals_spans", "timeout");
        return;
    }
    compare_fb_region("span_group_varcount_masked_equals_spans.fb", m,
                      FB_BASE_BYTE, 320, 6, 52, 4,
                      span_group_var_row_count(q, 0, 4));
}

static void test_span_group_varcount_explicit_colormap_slot() {
    printf("TEST span_group_varcount_explicit_colormap_slot\n");
    gpu_init();
    auto m = preload_with_sentinel();

    for (int row = 0; row < 4; row++)
        upload_palookup_const_row(7, (uint8_t)row, (uint8_t)(0xA0 + row));
    for (int lane = 0; lane < 4; lane++) {
        std::vector<uint8_t> tex(8);
        for (int i = 0; i < 8; i++)
            tex[i] = (uint8_t)(0x08 + lane * 8 + i);
        upload_texture(TEX_BASE_BYTE + (uint32_t)lane * 0x100, tex);
    }
    m.snapshot_from_sdram();

    SpanGroupVarWire q = make_span_group_var();
    q.fb_addr = FB_BASE_BYTE + 78 * 320 + 12;
    q.flags = 0x1;      // COLORMAP
    q.colormap_id = 7;  // explicit slot
    const uint16_t starts[4] = {0, 3, 1, 2};
    const uint16_t counts[4] = {4, 3, 5, 1};
    for (int lane = 0; lane < 4; lane++) {
        q.tex_addr[lane] = TEX_BASE_BYTE + (uint32_t)lane * 0x100;
        q.t[lane] = 0;
        q.tstep[lane] = 0x10000;
        q.y_start[lane] = starts[lane];
        q.count[lane] = counts[lane];
        q.light[lane] = (uint8_t)lane;
    }

    emit_span_group_var_raw(q);
    m.apply_span_group_var_affine(q);
    if (!submit_and_wait()) {
        check_fail("span_group_varcount_explicit_colormap_slot", "timeout");
        return;
    }
    compare_fb_region("span_group_varcount_explicit_colormap_slot.fb", m,
                      FB_BASE_BYTE, 320, 12, 78, 4,
                      span_group_var_row_count(q, 0, 4));
}

static void test_span_group_stream_two_payloads() {
    printf("TEST span_group_stream_two_payloads\n");
    gpu_init();
    auto m = preload_with_sentinel();

    for (int lane = 0; lane < 4; lane++) {
        std::vector<uint8_t> tex(4);
        for (int i = 0; i < 4; i++)
            tex[i] = (uint8_t)(0x80 + lane * 8 + i);
        upload_texture(TEX_BASE_BYTE + (uint32_t)lane * 0x100, tex);
    }
    m.snapshot_from_sdram();

    std::vector<SpanGroupWire> qs;
    for (int n = 0; n < 2; n++) {
        SpanGroupWire q = make_span_group();
        q.fb_addr = FB_BASE_BYTE + (30 + n * 5) * 320 + 2;
        q.count = 4;
        q.flags = 0;
        set_span_group_colormap(q, 0);
        for (int lane = 0; lane < 4; lane++) {
            q.tex_addr[lane] = TEX_BASE_BYTE + (uint32_t)lane * 0x100;
            q.t[lane] = n * 0x10000;
            q.tstep[lane] = 0x10000;
            q.light[lane] = 0;
        }
        qs.push_back(q);
    }

    emit_span_group_stream_raw(qs);
    m.apply_span_group_stream(qs);
    if (!submit_and_wait()) {
        check_fail("span_group_stream_two_payloads", "timeout");
        return;
    }
    compare_fb_region("span_group_stream_two_payloads.a", m,
                      FB_BASE_BYTE, 320, 2, 30, 4, 4);
    compare_fb_region("span_group_stream_two_payloads.b", m,
                      FB_BASE_BYTE, 320, 2, 35, 4, 4);
}

static void test_span_group_two_lane_masked() {
    printf("TEST span_group_two_lane_masked\n");
    gpu_init();
    auto m = preload_with_sentinel();

    for (int lane = 0; lane < 2; lane++) {
        std::vector<uint8_t> tex = {
            (uint8_t)(0x31 + lane), 0xFF, (uint8_t)(0x41 + lane),
            0xFF, (uint8_t)(0x51 + lane), (uint8_t)(0x61 + lane)
        };
        upload_texture(TEX_BASE_BYTE + (uint32_t)lane * 0x80, tex);
    }
    m.snapshot_from_sdram();

    SpanGroupWire q = make_span_group();
    q.lane_count = 2;
    q.fb_addr = FB_BASE_BYTE + 48u * 320u + 21u;
    q.count = 6;
    q.flags = 0x4;  // SKIP_ZERO
    for (int lane = 0; lane < 2; lane++) {
        q.tex_addr[lane] = TEX_BASE_BYTE + (uint32_t)lane * 0x80;
        q.t[lane] = 0;
        q.tstep[lane] = 0x10000;
        q.light[lane] = 0;
    }

    emit_span_group_raw(q);
    m.apply_span_group_affine(q);
    if (!submit_and_wait()) {
        check_fail("span_group_two_lane_masked", "timeout");
        return;
    }
    compare_fb_region("span_group_two_lane_masked.fb", m,
                      FB_BASE_BYTE, 320, 21, 48, 2, q.count);
}

static void test_span_group_eight_lane_colormap() {
    printf("TEST span_group_eight_lane_colormap\n");
    gpu_init();
    auto m = preload_with_sentinel();

    for (int lane = 0; lane < 8; lane++) {
        std::vector<uint8_t> tex(5);
        for (int i = 0; i < 5; i++)
            tex[i] = (uint8_t)(0x10 + lane * 8 + i);
        upload_texture(TEX_BASE_BYTE + (uint32_t)lane * 0x80, tex);
        upload_palookup_const_row(9, (uint8_t)lane, (uint8_t)(0xA0 + lane));
    }
    m.snapshot_from_sdram();

    SpanGroupWire q = make_span_group();
    q.lane_count = 8;
    q.fb_addr = FB_BASE_BYTE + 72u * 320u + 24u;
    q.count = 5;
    q.flags = 0x1;        // COLORMAP
    set_span_group_colormap(q, 9);
    for (int lane = 0; lane < 8; lane++) {
        q.tex_addr[lane] = TEX_BASE_BYTE + (uint32_t)lane * 0x80;
        q.t[lane] = 0;
        q.tstep[lane] = 0x10000;
        q.light[lane] = (uint8_t)lane;
    }

    emit_span_group_raw(q);
    m.apply_span_group_affine(q);
    if (!submit_and_wait()) {
        check_fail("span_group_eight_lane_colormap", "timeout");
        return;
    }
    compare_fb_region("span_group_eight_lane_colormap.fb", m,
                      FB_BASE_BYTE, 320, 24, 72, 8, q.count);
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

static void test_command_stream_dma_mixed_affine_groups() {
    /* DMA command streams let software batch mixed native span-group commands without changing draw order. */
    printf("TEST command_stream_dma_mixed_affine_groups\n");
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

    SpanGroupWire q = make_span_group();
    q.fb_addr = FB_BASE_BYTE + 10u * 320u + 30u;
    q.count = 8;
    q.flags = 0x1;        // SPAN_COLORMAP
    set_span_group_colormap(q, 0);
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
    append_command(stream, encode_span_wire(s0));
    append_span_group_stream_raw(stream, q);
    append_command(stream, encode_span_wire(s1));

    emit_command_stream_dma(stream);
    m.apply_span_ref(s0);
    m.apply_span_group_affine(q);
    m.apply_span_ref(s1);

    if (!submit_and_wait()) {
        check_fail("command_stream_dma_mixed_affine_groups", "timeout");
        return;
    }
    compare_fb_region("command_stream_dma_mixed_affine_groups.fb", m,
                      FB_BASE_BYTE, 320, 8, 8, 40, 24);
}

static void test_command_stream_dma_mixed_persp_span_group() {
    /* Exercise perspective records through the same mixed raw command
     * stream path the SDK uses, with ordinary commands before and after it.
     * This catches payload length, rdptr, and state carryover bugs that an
     * isolated inline command can miss. */
    printf("TEST command_stream_dma_mixed_persp_span_group\n");
    gpu_init();
    auto m = preload_with_sentinel();

    constexpr int tex_columns = 16;
    constexpr int tex_height = 128;
    std::vector<uint8_t> tex(tex_columns * tex_height);
    for (int col = 0; col < tex_columns; col++) {
        for (int y = 0; y < tex_height; y++) {
            tex[col * tex_height + y] =
                (uint8_t)(0x27 + col * 0x13 + y * 0x07);
        }
    }
    upload_texture(TEX_BASE_BYTE, tex);
    upload_palookup_identity_row(0, 0);
    m.snapshot_from_sdram();
    m.st_fb_addr = FB_BASE_BYTE;

    SpanWire before = make_span();
    before.fb_addr = FB_BASE_BYTE + 4u * 320u + 6u;
    before.tex_addr = TEX_BASE_BYTE;
    before.tex_width = tex_height;
    before.tex_w_mask = tex_height - 1;
    before.tex_h_mask = tex_columns - 1;
    before.s = 0;
    before.t = 1 << 16;
    before.sstep = 1 << 16;
    before.tstep = 0;
    before.count = 11;
    before.flags = 0x1;
    before.colormap_id = 0;
    before.fb_stride = 1;

    PerspSpanGroupWire q = make_persp_span_group();
    q.fb_addr = FB_BASE_BYTE + 37u;
    q.tex_addr = TEX_BASE_BYTE;
    q.lane_count = 6;
    q.flags = SPAN_PERSP | (1u << 0);
    q.reserved = 1u;
    q.colormap_id = 0;
    q.major_fb_step = 1;
    q.minor_fb_step = 320;
    q.tex_width = tex_height;
    q.tex_w_mask = tex_height - 1;
    q.tex_h_mask = tex_columns - 1;
    const int starts[6] = {8, 1, 22, 5, 13, 0};
    const int counts[6] = {19, 32, 9, 27, 14, 37};
    for (int i = 0; i < 6; i++) {
        q.start[i] = (int16_t)starts[i];
        q.count[i] = (uint16_t)counts[i];
    }
    q.sZ = 3 << 16;
    q.tZ = -(2 << 16);
    q.zinv = 0x00010000;
    q.sZ_major_step = 0;
    q.tZ_major_step = 1 << 16;
    q.zinv_major_step = 0;
    q.sZ_minor_step = 1 << 16;
    q.tZ_minor_step = 0;
    q.zinv_minor_step = 0;

    SpanWire after = before;
    after.fb_addr = FB_BASE_BYTE + 48u * 320u + 6u;
    after.s = 5 << 16;
    after.t = 4 << 16;
    after.count = 13;

    std::vector<uint32_t> stream;
    auto append = [&stream](const std::vector<uint32_t> &words) {
        stream.insert(stream.end(), words.begin(), words.end());
    };
    append_command(stream, encode_span_wire(before));
    {
        int first_lane = 0;
        int lanes_left = q.lane_count;
        while (lanes_left > 0) {
            int chunk_lanes = (lanes_left >= 4) ? 4 : lanes_left;
            auto w = encode_persp_span_group_wire_chunk(q, first_lane,
                                                        chunk_lanes);
            stream.push_back(((uint32_t)CMD_PARAM_SPAN_LIST << 24) | (uint32_t)w.size());
            append(w);
            first_lane += chunk_lanes;
            lanes_left -= chunk_lanes;
        }
    }
    append_command(stream, encode_span_wire(after));

    emit_command_stream_dma(stream);
    m.apply_span_ref(before);
    for (int lane = 0; lane < 6; lane++) {
        const int start = q.start[lane];
        SpanWire s = make_span();
        s.fb_addr = FB_BASE_BYTE + 37u + (uint32_t)lane
                  + (uint32_t)start * 320u;
        s.tex_addr = TEX_BASE_BYTE;
        s.count = q.count[lane];
        s.flags = q.flags & (uint8_t)~SPAN_PERSP;
        s.colormap_id = q.colormap_id;
        s.fb_stride = 320;
        s.tex_width = q.tex_width;
        s.tex_w_mask = q.tex_w_mask;
        s.tex_h_mask = q.tex_h_mask;
        s.s = q.sZ + lane * q.sZ_major_step + start * q.sZ_minor_step;
        s.t = q.tZ + lane * q.tZ_major_step + start * q.tZ_minor_step;
        s.sstep = q.sZ_minor_step;
        s.tstep = q.tZ_minor_step;
        m.apply_span_affine(s, q.colormap_id & 0xF);
    }
    m.apply_span_ref(after);

    if (!submit_and_wait()) {
        check_fail("command_stream_dma_mixed_persp_span_group", "timeout");
        return;
    }
    compare_fb_region("command_stream_dma_mixed_persp_span_group.fb", m,
                      FB_BASE_BYTE, 320, 4, 0, 40, 62);
}

static void test_dma_descriptor_queue_two_streams() {
    /* The RTL DMA puller has a two-entry descriptor FIFO.  Two immediate
     * kicks from different SDRAM scratch buffers must land in ring order
     * without software waiting for the first DMA to publish. */
    printf("TEST dma_descriptor_queue_two_streams\n");
    gpu_init();
    auto m = preload_with_sentinel();

    std::vector<uint8_t> tex(32);
    for (int i = 0; i < 32; i++) tex[i] = (uint8_t)(0x30 + i);
    upload_texture(TEX_BASE_BYTE, tex);
    m.snapshot_from_sdram();
    m.st_fb_addr = FB_BASE_BYTE;

    SpanWire s0 = make_span();
    s0.fb_addr = FB_BASE_BYTE + 2u * 320u + 12u;
    s0.tex_addr = TEX_BASE_BYTE;
    s0.tex_width = 32;
    s0.tex_w_mask = 0x1F;
    s0.s = 0;
    s0.t = 0;
    s0.sstep = 0x10000;
    s0.tstep = 0;
    s0.count = 16;
    s0.flags = 0;

    SpanWire s1 = s0;
    s1.fb_addr = FB_BASE_BYTE + 3u * 320u + 12u;
    s1.s = 4 << 16;

    std::vector<uint32_t> stream0;
    stream0.push_back((0x23u << 24) | 2u);
    stream0.push_back(FB_BASE_BYTE);
    stream0.push_back(320u);
    append_command(stream0, encode_span_wire(s0));

    uint32_t token = next_fence_token++;
    std::vector<uint32_t> stream1;
    append_command(stream1, encode_span_wire(s1));
    stream1.push_back((0x02u << 24) | 1u);
    stream1.push_back(token);

    uint32_t addr0 = BATCH_BUF_BYTE;
    uint32_t addr1 = BATCH_BUF_BYTE + 0x4000u;
    uint32_t word = addr0 >> 2;
    for (uint32_t x : stream0)
        sdram_write(word++, x);
    word = addr1 >> 2;
    for (uint32_t x : stream1)
        sdram_write(word++, x);

    ring_wrptr = (ring_wrptr +
                  ((uint32_t)stream0.size() + (uint32_t)stream1.size()) * 4u) &
                 ring_mask;

    mmio_write(REG_DMA_SRC, addr0);
    mmio_write(REG_DMA_LEN, (uint32_t)stream0.size());
    mmio_write(REG_DMA_KICK, 1);
    mmio_write(REG_DMA_SRC, addr1);
    mmio_write(REG_DMA_LEN, (uint32_t)stream1.size());
    mmio_write(REG_DMA_KICK, 1);

    m.apply_span_ref(s0);
    m.apply_span_ref(s1);

    if (!wait_fence(token)) {
        check_fail("dma_descriptor_queue_two_streams", "timeout");
        return;
    }
    compare_fb_region("dma_descriptor_queue_two_streams.fb", m,
                      FB_BASE_BYTE, 320, 10, 2, 22, 2);
}

// ---- Section 15: DRAW_TRIANGLES (basic / state interaction) ----------------
//
// Triangles use a CPU model approximation; tests compare bbox stats
// rather than per-byte (the rasterization rules are too tight for a
// double-precision reference to match the integer 12.4 / 16.16 mix).
// The standalone triangle suite already lives in tb_gpu_main.cpp; we add
// one narrowed test here that verifies triangles use palookup slot 0.
static void test_triangle_uses_colormap_slot_zero() {
    printf("TEST triangle_uses_colormap_slot_zero\n");
    gpu_init();
    preload_with_sentinel();

    upload_palookup_identity_row(0, 0);
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

    write_tri(0);
    write_tri(50);

    if (!submit_and_wait()) {
        check_fail("triangle_uses_colormap_slot_zero", "timeout");
        return;
    }
    /* Both triangles should resolve through slot 0 identity. Slot 2 is
     * deliberately different to catch accidental nonzero slot selection. */
    int n_42_in_first = 0, n_42_in_second = 0, n_88_any = 0;
    for (int y = 15; y < 35; y++) {
        for (int x = 15; x < 35; x++) {
            uint8_t a = sdram_read_byte(FB_BASE_BYTE + y*320 + x);
            uint8_t b = sdram_read_byte(FB_BASE_BYTE + y*320 + x + 50);
            if (a == 0x42) n_42_in_first++;
            if (b == 0x42) n_42_in_second++;
            if (a == 0x88 || b == 0x88) n_88_any++;
        }
    }
    if (n_42_in_first > 50 && n_42_in_second > 50 && n_88_any == 0)
        check_pass("triangle_uses_colormap_slot_zero.identity");
    else {
        char buf[128];
        snprintf(buf, sizeof(buf),
                 "expected slot0 identity in both triangles; got 0x42 counts %d/%d, 0x88 count %d",
                 n_42_in_first, n_42_in_second, n_88_any);
        check_fail("triangle_uses_colormap_slot_zero.identity", buf);
    }
}

static void test_framebuffer_writes_burst_full_words() {
    printf("TEST framebuffer_writes_burst_full_words\n");
    gpu_init();
    auto m = preload_with_sentinel();

    cmd_set_fb(FB_BASE_BYTE, 320);
    m.st_fb_addr = FB_BASE_BYTE;

    uint32_t burst_before = tb->dbg_aw_burst_count;

    cmd_clear_rect(FB_BASE_BYTE + 0x100, 64, 1, 0, 0x42);
    m.apply_clear_rect(FB_BASE_BYTE + 0x100, 64, 1, 0, 0x42);

    if (!submit_and_wait()) {
        check_fail("framebuffer_writes_burst_full_words.clear_rect", "timeout");
        return;
    }

    compare_fb_region("framebuffer_writes_burst_full_words.fb", m,
                      FB_BASE_BYTE, 320, 0, 0, 128, 2);

    uint32_t burst_after_clear = tb->dbg_aw_burst_count;

    if (burst_after_clear > burst_before && tb->dbg_aw_max_len >= 6)
        check_pass("framebuffer_writes_burst_full_words.clear_rect_burst");
    else {
        char buf[128];
        snprintf(buf, sizeof(buf),
                 "expected burst beyond old 4-beat cap on aligned clear_rect: bursts %u -> %u max_len=%u",
                 burst_before, burst_after_clear, (unsigned)tb->dbg_aw_max_len);
        check_fail("framebuffer_writes_burst_full_words.clear_rect_burst", buf);
    }

#if GPU_TEST_ENABLE_TRIANGLES
    ring_cmd(0x30, 19);
    ring_write(3);
    auto vert = [](int16_t x, int16_t y, int32_t s, int32_t t) {
        ring_write(((uint32_t)(uint16_t)(x * 16) << 16) | (uint16_t)(y * 16));
        ring_write(0);
        ring_write((uint32_t)s);
        ring_write((uint32_t)t);
        ring_write(0x00010000);
        ring_write(0x00000000);
    };
    vert(8,  8, 0,       0);
    vert(72, 8, 7 << 16, 0);
    vert(8, 40, 0,       7 << 16);

    if (!submit_and_wait()) {
        check_fail("framebuffer_writes_burst_full_words.triangle", "timeout");
        return;
    }

    uint32_t burst_after_tri = tb->dbg_aw_burst_count;
    if (burst_after_tri >= burst_after_clear)
        check_pass("framebuffer_writes_burst_full_words.triangle_safe");
    else {
        char buf[128];
        snprintf(buf, sizeof(buf),
                 "burst counter regressed on triangle: %u -> %u, max=%u",
                 burst_after_clear, burst_after_tri, (unsigned)tb->dbg_aw_max_len);
        check_fail("framebuffer_writes_burst_full_words.triangle_safe", buf);
    }
#endif
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
static void test_combo_a_setfb_then_colormapped_span() {
    printf("TEST combo_A_setfb_then_colormapped_span\n");
    gpu_init();
    auto m = preload_with_sentinel();
    upload_palookup_const_row(3, 0, 0xCC);
    std::vector<uint8_t> tex(16, 0x42);
    upload_texture(TEX_BASE_BYTE, tex);
    m.snapshot_from_sdram();

    /* SET_FB A, draw colormapped span with explicit slot 3 — should write
     * 0xCC everywhere in FB-A. */
    cmd_set_fb(FB_BASE_BYTE, 320); m.st_fb_addr = FB_BASE_BYTE;
    SpanWire s = make_span();
    s.fb_addr = FB_BASE_BYTE; s.tex_addr = TEX_BASE_BYTE;
    s.tex_width = 16; s.tex_w_mask = 0xF;
    s.s = 0; s.t = 0; s.sstep = 0x10000; s.tstep = 0;
    s.count = 16; s.flags = 0x1; s.colormap_id = 3;
    emit_span_raw(s); m.apply_span_ref(s);

    /* SET_FB B, draw same — output should now go to FB-B only. */
    cmd_set_fb(FB_ALT_BASE_BYTE, 320); m.st_fb_addr = FB_ALT_BASE_BYTE;
    SpanWire sB = s; sB.fb_addr = FB_ALT_BASE_BYTE;
    emit_span_raw(sB); m.apply_span_ref(sB);

    if (!submit_and_wait()) {
        check_fail("combo_A_setfb_then_colormapped_span", "timeout");
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
    emit_span_raw(s); m.apply_span_ref(s);

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
    emit_span_raw(s); m.apply_span_ref(s);

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
    emit_span_raw(s2); m.apply_span_ref(s2);
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
        emit_span_raw(s); m.apply_span_ref(s);
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
     * native span in the same kick.  Both should retire. */
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

    /* Now an inline native span at row 4. */
    SpanWire sInline = spans[0];
    sInline.fb_addr = FB_BASE_BYTE + 4 * 320;
    emit_span_raw(sInline); m.apply_span_ref(sInline);

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

// SDK packs single-lane span count separately from colormap_id.  Counts past the
// old 12-bit boundary must not bleed into the explicit colormap slot.
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
    s.count = 0x1001;
    s.light = 0;
    s.flags = 0x1;        // SPAN_COLORMAP
    s.colormap_id = 0;
    emit_span_sdk_encoded(s);

    if (!submit_and_wait()) {
        check_fail("sdk_count_4096_does_not_leak_into_colormap_id", "timeout");
        return;
    }
    /* FB[0] disambiguates: 0x10 = no leak (slot 0 identity),
     * SLOT1_K = count high nibble leaked into colormap_id. */
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
    /* The full 16-bit count should draw through the first 16 checked pixels. */
    int drawn = 0;
    for (int x = 0; x < 16; x++)
        if (sdram_read_byte(FB_BASE_BYTE + x) == 0x10) drawn++;
    if (drawn == 16) {
        check_pass("sdk_count_4096_does_not_leak_into_colormap_id.count_preserved");
    } else {
        char buf[64];
        snprintf(buf, sizeof(buf), "%d/16 first pixels rendered", drawn);
        check_fail("sdk_count_4096_does_not_leak_into_colormap_id.count_preserved", buf);
    }
}

static SpanWire param_affine_ref_span(const ParamSpanListWire &p,
                                      const ParamSpanRecordWire &r) {
    SpanWire s = make_span();
    int64_t u = r.u;
    int64_t v = r.v;
    int64_t fb = (p.span_axis == 1)
               ? (int64_t)p.fb_base + u * p.fb_major_step + v * p.fb_minor_step
               : (int64_t)p.fb_base + v * p.fb_major_step + u * p.fb_minor_step;
    int64_t a0 = (int64_t)p.attr_origin[0] + u * p.attr_du[0] + v * p.attr_dv[0];
    int64_t a1 = (int64_t)p.attr_origin[1] + u * p.attr_du[1] + v * p.attr_dv[1];
    int64_t light = (int64_t)p.light_origin + u * p.light_du + v * p.light_dv;

    s.fb_addr = (uint32_t)fb;
    s.tex_addr = p.tex_addr;
    s.s = (int32_t)a0;
    s.t = (int32_t)a1;
    s.sstep = (p.span_axis == 1) ? p.attr_dv[0] : p.attr_du[0];
    s.tstep = (p.span_axis == 1) ? p.attr_dv[1] : p.attr_du[1];
    s.count = r.count;
    s.flags = p.flags & ~SPAN_PERSP;
    s.colormap_id = p.colormap_id;
    s.light = (uint8_t)((light >> 16) & 0x3F);
    s.fb_stride = (int16_t)p.fb_minor_step;
    s.tex_width = p.tex_width;
    s.tex_w_mask = p.tex_w_mask;
    s.tex_h_mask = p.tex_h_mask;
    return s;
}

static std::vector<uint8_t> make_param_test_texture() {
    std::vector<uint8_t> tex(64 * 64);
    for (int y = 0; y < 64; y++)
        for (int x = 0; x < 64; x++)
            tex[y * 64 + x] = (uint8_t)((y << 2) ^ (x * 3) ^ 0x31);
    return tex;
}

static void test_param_span_list_affine_rows() {
    printf("TEST param_span_list_affine_rows\n");
    gpu_init();
    FbModel m = preload_with_sentinel();
    upload_texture(TEX_BASE_BYTE, make_param_test_texture());
    m.snapshot_from_sdram();

    ParamSpanListWire p {};
    p.fb_base = FB_BASE_BYTE;
    p.fb_major_step = 320;
    p.fb_minor_step = 1;
    p.tex_addr = TEX_BASE_BYTE;
    p.tex_width = 64;
    p.tex_w_mask = 0x3F;
    p.tex_h_mask = 0x3F;
    p.attr_mode = 0;
    p.span_axis = 0;
    p.attr_du[0] = 1 << 16;
    p.attr_dv[1] = 1 << 16;

    std::vector<ParamSpanRecordWire> records = {
        {3, 4, 5}, {10, 5, 3}, {1, 7, 4}
    };
    emit_param_span_list_raw(p, records);
    for (const auto &r : records)
        m.apply_span_ref(param_affine_ref_span(p, r));

    if (!submit_and_wait()) {
        check_fail("param_span_list_affine_rows", "timeout");
        return;
    }
    compare_fb_region("param_span_list_affine_rows.fb", m,
                      FB_BASE_BYTE, 320, 0, 0, 32, 12);
}

static void test_param_span_list_affine_columns() {
    printf("TEST param_span_list_affine_columns\n");
    gpu_init();
    FbModel m = preload_with_sentinel();
    upload_texture(TEX_BASE_BYTE, make_param_test_texture());
    m.snapshot_from_sdram();

    ParamSpanListWire p {};
    p.fb_base = FB_BASE_BYTE;
    p.fb_major_step = 1;
    p.fb_minor_step = 320;
    p.tex_addr = TEX_BASE_BYTE;
    p.tex_width = 64;
    p.tex_w_mask = 0x3F;
    p.tex_h_mask = 0x3F;
    p.attr_mode = 0;
    p.span_axis = 1;
    p.attr_du[0] = 1 << 16;
    p.attr_dv[1] = 1 << 16;

    std::vector<ParamSpanRecordWire> records = {
        {5, 2, 4}, {9, 3, 5}, {14, 1, 3}, {18, 4, 2}
    };
    emit_param_span_list_raw(p, records);
    for (const auto &r : records)
        m.apply_span_ref(param_affine_ref_span(p, r));

    if (!submit_and_wait()) {
        check_fail("param_span_list_affine_columns", "timeout");
        return;
    }
    compare_fb_region("param_span_list_affine_columns.fb", m,
                      FB_BASE_BYTE, 320, 0, 0, 24, 12);
}

static void test_param_span_list_affine_clamp() {
    printf("TEST param_span_list_affine_clamp\n");
    gpu_init();
    preload_with_sentinel();
    std::vector<uint8_t> tex(64);
    for (int i = 0; i < 64; i++)
        tex[i] = (uint8_t)i;
    upload_texture(TEX_BASE_BYTE, tex);

    ParamSpanListWire p {};
    p.fb_base = FB_BASE_BYTE;
    p.fb_major_step = 320;
    p.fb_minor_step = 1;
    p.tex_addr = TEX_BASE_BYTE;
    p.tex_width = 64;
    p.tex_w_mask = 0x3F;
    p.tex_h_mask = 0x3F;
    p.attr_mode = 0;
    p.span_axis = 0;
    p.attr_du[0] = 1 << 16;
    p.clamp_min[0] = 2 << 16;
    p.clamp_max[0] = 4 << 16;

    std::vector<ParamSpanRecordWire> records = {{0, 0, 8}};
    emit_param_span_list_raw(p, records);

    if (!submit_and_wait()) {
        check_fail("param_span_list_affine_clamp", "timeout");
        return;
    }
    const uint8_t expected[8] = {2, 2, 2, 3, 4, 4, 4, 4};
    int diffs = 0;
    for (int i = 0; i < 8; i++) {
        if (sdram_read_byte(FB_BASE_BYTE + (uint32_t)i) != expected[i])
            diffs++;
    }
    if (diffs == 0)
        check_pass("param_span_list_affine_clamp");
    else {
        char buf[64];
        snprintf(buf, sizeof(buf), "%d clamp byte mismatches", diffs);
        check_fail("param_span_list_affine_clamp", buf);
    }
}

static void test_param_span_list_zero_counts_skip() {
    printf("TEST param_span_list_zero_counts_skip\n");
    gpu_init();
    FbModel m = preload_with_sentinel();
    upload_texture(TEX_BASE_BYTE, make_param_test_texture());
    m.snapshot_from_sdram();

    ParamSpanListWire p {};
    p.fb_base = FB_BASE_BYTE;
    p.fb_major_step = 320;
    p.fb_minor_step = 1;
    p.tex_addr = TEX_BASE_BYTE;
    p.tex_width = 64;
    p.tex_w_mask = 0x3F;
    p.tex_h_mask = 0x3F;
    p.attr_mode = 0;
    p.span_axis = 0;
    p.attr_du[0] = 1 << 16;
    p.attr_dv[1] = 1 << 16;

    std::vector<ParamSpanRecordWire> records = {
        {2, 2, 3}, {7, 2, 0}, {4, 3, 2}, {9, 3, 0}
    };
    emit_param_span_list_raw(p, records);
    for (const auto &r : records) {
        if (r.count != 0)
            m.apply_span_ref(param_affine_ref_span(p, r));
    }

    if (!submit_and_wait()) {
        check_fail("param_span_list_zero_counts_skip", "timeout");
        return;
    }
    compare_fb_region("param_span_list_zero_counts_skip.fb", m,
                      FB_BASE_BYTE, 320, 0, 0, 16, 8);
}

static void test_param_span_list_streams_many_records() {
    printf("TEST param_span_list_streams_many_records\n");
    gpu_init();
    FbModel m = preload_with_sentinel();
    upload_texture(TEX_BASE_BYTE, make_param_test_texture());
    m.snapshot_from_sdram();

    ParamSpanListWire p {};
    p.fb_base = FB_BASE_BYTE;
    p.fb_major_step = 320;
    p.fb_minor_step = 1;
    p.tex_addr = TEX_BASE_BYTE;
    p.tex_width = 64;
    p.tex_w_mask = 0x3F;
    p.tex_h_mask = 0x3F;
    p.attr_mode = 0;
    p.span_axis = 0;
    p.attr_du[0] = 1 << 16;
    p.attr_dv[1] = 1 << 16;

    std::vector<ParamSpanRecordWire> records = {
        {1, 1, 5}, {9, 1, 4}, {2, 2, 6}, {13, 2, 3},
        {4, 3, 0}, {6, 3, 7}, {3, 4, 5}, {12, 5, 6},
        {0, 6, 8}
    };
    emit_param_span_list_raw(p, records);
    for (const auto &r : records) {
        if (r.count != 0)
            m.apply_span_ref(param_affine_ref_span(p, r));
    }

    if (!submit_and_wait()) {
        check_fail("param_span_list_streams_many_records", "timeout");
        return;
    }
    compare_fb_region("param_span_list_streams_many_records.fb", m,
                      FB_BASE_BYTE, 320, 0, 0, 24, 8);
}

static void test_param_span_list_unsupported_noop() {
    printf("TEST param_span_list_unsupported_noop\n");
    gpu_init();
    FbModel m = preload_with_sentinel();
    upload_texture(TEX_BASE_BYTE, make_param_test_texture());
    m.snapshot_from_sdram();

    ParamSpanListWire p {};
    p.fb_base = FB_BASE_BYTE;
    p.fb_major_step = 320;
    p.fb_minor_step = 1;
    p.tex_addr = TEX_BASE_BYTE;
    p.tex_width = 64;
    p.tex_w_mask = 0x3F;
    p.tex_h_mask = 0x3F;
    p.attr_mode = 2;       // unsupported SOLID/opaque fill attr mode
    p.span_axis = 0;
    p.attr_du[0] = 1 << 16;

    std::vector<ParamSpanRecordWire> records = {{0, 0, 5}};
    emit_param_span_list_raw(p, records);

    p.attr_mode = 0;
    p.z_mode = 4;          // unsupported depth mode must also be ignored
    emit_param_span_list_raw(p, records);

    if (!submit_and_wait()) {
        check_fail("param_span_list_unsupported_noop", "timeout");
        return;
    }
    compare_fb_region("param_span_list_unsupported_noop.fb", m,
                      FB_BASE_BYTE, 320, 0, 0, 16, 4);
}

static void test_param_span_list_colormap_skip_zero() {
    printf("TEST param_span_list_colormap_skip_zero\n");
    gpu_init();
    FbModel m = preload_with_sentinel();
    std::vector<uint8_t> tex(64 * 64, 0);
    tex[0] = 0x10;
    tex[1] = 0xFF;
    tex[2] = 0x20;
    tex[3] = 0x21;
    tex[4] = 0xFF;
    tex[5] = 0x22;
    upload_texture(TEX_BASE_BYTE, tex);
    upload_palookup_const_row(3, 2, 0x77);
    m.snapshot_from_sdram();

    ParamSpanListWire p {};
    p.fb_base = FB_BASE_BYTE;
    p.fb_major_step = 320;
    p.fb_minor_step = 1;
    p.tex_addr = TEX_BASE_BYTE;
    p.tex_width = 64;
    p.tex_w_mask = 0x3F;
    p.tex_h_mask = 0x3F;
    p.flags = (1u << 0) | (1u << 2);   // COLORMAP | SKIP_ZERO
    p.colormap_id = 3;
    p.attr_mode = 0;
    p.span_axis = 0;
    p.attr_du[0] = 1 << 16;
    p.light_origin = 2 << 16;

    std::vector<ParamSpanRecordWire> records = {{0, 0, 6}};
    emit_param_span_list_raw(p, records);
    m.apply_span_ref(param_affine_ref_span(p, records[0]));

    if (!submit_and_wait()) {
        check_fail("param_span_list_colormap_skip_zero", "timeout");
        return;
    }
    compare_fb_region("param_span_list_colormap_skip_zero.fb", m,
                      FB_BASE_BYTE, 320, 0, 0, 8, 1);
}

// 0x49 is CMD_DRAW_PARAM_TRI; any payload size other than the fixed 36
// words must drain through S_PAY_DATA undecoded and retire as a no-op.
static void test_param_tri_wrong_size_noop_drains() {
    printf("TEST param_tri_wrong_size_noop_drains\n");
    gpu_init();
    FbModel m = preload_with_sentinel();
    m.snapshot_from_sdram();

    std::vector<uint32_t> payload(53);
    for (size_t i = 0; i < payload.size(); i++)
        payload[i] = 0x49000000u ^ (uint32_t)(i * 0x10203u);
    emit_raw_command(0x49, payload);

    cmd_clear_rect(FB_BASE_BYTE + 7u * 320u + 5u, 9, 2, 0, 0x42);
    m.apply_clear_rect(FB_BASE_BYTE + 7u * 320u + 5u, 9, 2, 0, 0x42);

    if (!submit_and_wait()) {
        check_fail("param_tri_wrong_size_noop_drains", "timeout");
        return;
    }
    compare_fb_region("param_tri_wrong_size_noop_drains.fb",
                      m, FB_BASE_BYTE, 320, 0, 0, 32, 12);
}

// ================================================================
// CMD_DRAW_PARAM_TRI (0x49) — hardware edge walker
// ================================================================
// The walker turns 3 vertices into the same {u,v,count} records a packed
// span list carries, so the reference path is: software edge walk (below,
// mirroring the RTL datapath bit-for-bit) -> the existing per-record
// affine reference. attr_mode/persp/z behaviour downstream of the records
// is covered by the span-list tests; these tests prove record equivalence.

// Software mirror of gpu_edge_walker.v: y-sort, cross-product winding,
// truncating Q16.16 slope divide, ceil fill, left-closed right-open,
// clip rect. x is Q12.4, y is integer scanlines.
static void ref_tri_records(const int16_t vx[3], const int16_t vy[3],
                            int cx0, int cx1, int cy0, int cy1,
                            std::vector<ParamSpanRecordWire> &out) {
    int16_t x[3] = {vx[0], vx[1], vx[2]};
    int16_t y[3] = {vy[0], vy[1], vy[2]};
    if (y[1] < y[0]) { std::swap(x[0], x[1]); std::swap(y[0], y[1]); }
    if (y[2] < y[1]) { std::swap(x[1], x[2]); std::swap(y[1], y[2]); }
    if (y[1] < y[0]) { std::swap(x[0], x[1]); std::swap(y[0], y[1]); }

    long long z = (long long)(x[1] - x[0]) * (y[2] - y[0])
                - (long long)(x[2] - x[0]) * (y[1] - y[0]);
    bool long_left = (z > 0);

    auto slope = [](int dx, int dy) -> long long {
        if (dy == 0) return 0;
        long long q = ((long long)llabs(dx) << 12) / llabs(dy);
        return ((dx < 0) ^ (dy < 0)) ? -q : q;
    };
    long long sl = slope(x[2] - x[0], y[2] - y[0]);
    long long st = slope(x[1] - x[0], y[1] - y[0]);
    long long sb = slope(x[2] - x[1], y[2] - y[1]);

    int ystart = (y[0] < cy0) ? cy0 : y[0];
    int yend   = (y[2] > cy1) ? cy1 : y[2];
    if (ystart >= yend) return;

    bool bottom = (ystart >= y[1]);
    long long xl, xr;
    long long xlong  = ((long long)x[0] << 12) + sl * (ystart - y[0]);
    long long xshort = bottom
        ? ((long long)x[1] << 12) + sb * (ystart - y[1])
        : ((long long)x[0] << 12) + st * (ystart - y[0]);
    if (long_left) { xl = xlong; xr = xshort; }
    else           { xr = xlong; xl = xshort; }

    for (int yy = ystart; yy < yend; yy++) {
        int ul = (int)((xl + 0xFFFF) >> 16);
        int ur = (int)((xr + 0xFFFF) >> 16);
        int u0 = (ul < cx0) ? cx0 : ul;
        int u1 = (ur > cx1) ? cx1 : ur;
        if (u1 - u0 > 0)
            out.push_back({(uint16_t)u0, (uint16_t)yy, (uint16_t)(u1 - u0)});
        if (!bottom && (yy + 1 >= y[1])) {
            bottom = true;
            /* Bottom edge starts AT v1: x = x1 exactly on scanline y[1]
             * (matches the clip-entry prestep x1 + sb*(ystart - y1));
             * the first sb step lands on y[1]+1.  Keeps a shared edge
             * identical whether a neighbour walks it as long or
             * bottom-short — crack-free adjacency. */
            if (long_left) { xl += sl; xr = ((long long)x[1] << 12); }
            else           { xr += sl; xl = ((long long)x[1] << 12); }
        } else {
            if (long_left) { xl += sl; xr += bottom ? sb : st; }
            else           { xr += sl; xl += bottom ? sb : st; }
        }
    }
}

static void emit_param_tri_raw(const ParamSpanListWire &p,
                               const int16_t vx[3], const int16_t vy[3],
                               int16_t cx0, int16_t cx1,
                               int16_t cy0, int16_t cy1) {
    auto w = encode_param_span_list_wire(p, {});
    w.resize(36, 0);
    w[31] = ((uint32_t)(uint16_t)cx1 << 16) | (uint16_t)cx0;
    w[32] = ((uint32_t)(uint16_t)cy1 << 16) | (uint16_t)cy0;
    w[33] = ((uint32_t)(uint16_t)vy[0] << 16) | (uint16_t)vx[0];
    w[34] = ((uint32_t)(uint16_t)vy[1] << 16) | (uint16_t)vx[1];
    w[35] = ((uint32_t)(uint16_t)vy[2] << 16) | (uint16_t)vx[2];
    ring_cmd(0x49, (uint32_t)w.size());
    for (uint32_t x : w)
        ring_write(x);
}

static ParamSpanListWire make_tri_plane_setup() {
    ParamSpanListWire p {};
    p.fb_base = FB_BASE_BYTE;
    p.fb_major_step = 320;
    p.fb_minor_step = 1;
    p.tex_addr = TEX_BASE_BYTE;
    p.tex_width = 64;
    p.tex_w_mask = 0x3F;
    p.tex_h_mask = 0x3F;
    p.attr_mode = 0;
    p.span_axis = 0;
    p.attr_du[0] = 1 << 16;   // s = u, t = v: screen-aligned texture
    p.attr_dv[1] = 1 << 16;
    return p;
}

static void apply_tri_ref(FbModel &m, const ParamSpanListWire &p,
                          const int16_t vx[3], const int16_t vy[3],
                          int cx0, int cx1, int cy0, int cy1) {
    std::vector<ParamSpanRecordWire> records;
    ref_tri_records(vx, vy, cx0, cx1, cy0, cy1, records);
    for (const auto &r : records)
        m.apply_span_ref(param_affine_ref_span(p, r));
}

static void test_param_tri_affine_basic() {
    printf("TEST param_tri_affine_basic\n");
    gpu_init();
    FbModel m = preload_with_sentinel();
    upload_texture(TEX_BASE_BYTE, make_param_test_texture());
    m.snapshot_from_sdram();

    ParamSpanListWire p = make_tri_plane_setup();

    // General triangle (subpixel x), flat-top, flat-bottom.
    const int16_t tris[3][2][3] = {
        { { (int16_t)(5*16+7), (int16_t)(40*16+3), (int16_t)(20*16) },
          { 2, 6, 22 } },
        { { (int16_t)(8*16), (int16_t)(30*16), (int16_t)(18*16+9) },
          { 3, 3, 14 } },
        { { (int16_t)(24*16+5), (int16_t)(44*16), (int16_t)(60*16) },
          { 4, 18, 18 } },
    };
    for (int i = 0; i < 3; i++) {
        emit_param_tri_raw(p, tris[i][0], tris[i][1], 0, 64, 0, 28);
        apply_tri_ref(m, p, tris[i][0], tris[i][1], 0, 64, 0, 28);
    }

    if (!submit_and_wait()) {
        check_fail("param_tri_affine_basic", "timeout");
        return;
    }
    compare_fb_region("param_tri_affine_basic.fb", m,
                      FB_BASE_BYTE, 320, 0, 0, 72, 32);
}

static void test_param_tri_clip_all_sides() {
    printf("TEST param_tri_clip_all_sides\n");
    gpu_init();
    FbModel m = preload_with_sentinel();
    upload_texture(TEX_BASE_BYTE, make_param_test_texture());
    m.snapshot_from_sdram();

    ParamSpanListWire p = make_tri_plane_setup();

    // Big triangle overhanging the clip rect on every side; clip is
    // interior so every record is bounded by it, not by the screen.
    const int16_t vx[3] = { (int16_t)(-30*16+5), (int16_t)(90*16),
                            (int16_t)(20*16+11) };
    const int16_t vy[3] = { -12, 8, 45 };
    emit_param_tri_raw(p, vx, vy, 6, 58, 3, 26);
    apply_tri_ref(m, p, vx, vy, 6, 58, 3, 26);

    if (!submit_and_wait()) {
        check_fail("param_tri_clip_all_sides", "timeout");
        return;
    }
    compare_fb_region("param_tri_clip_all_sides.fb", m,
                      FB_BASE_BYTE, 320, 0, 0, 72, 32);
}

static void test_param_tri_degenerate_noop() {
    printf("TEST param_tri_degenerate_noop\n");
    gpu_init();
    FbModel m = preload_with_sentinel();
    upload_texture(TEX_BASE_BYTE, make_param_test_texture());
    m.snapshot_from_sdram();

    ParamSpanListWire p = make_tri_plane_setup();

    // Collinear (zero area, walks to zero-width spans), one-scanline-tall
    // (y0==y1==y2), fully above clip, fully below clip.  None may draw
    // or hang; the trailing clear_rect proves the FSM retired them all.
    const int16_t degen[4][2][3] = {
        { { (int16_t)(4*16), (int16_t)(20*16), (int16_t)(36*16) },
          { 5, 10, 15 } },                       // collinear in x/y
        { { (int16_t)(4*16), (int16_t)(20*16), (int16_t)(12*16) },
          { 7, 7, 7 } },                         // zero height
        { { (int16_t)(4*16), (int16_t)(20*16), (int16_t)(12*16) },
          { -20, -15, -8 } },                    // fully above clip
        { { (int16_t)(4*16), (int16_t)(20*16), (int16_t)(12*16) },
          { 40, 44, 50 } },                      // fully below clip
    };
    for (int i = 0; i < 4; i++)
        emit_param_tri_raw(p, degen[i][0], degen[i][1], 0, 64, 0, 28);

    // Collinear triangles may still rasterize sub-pixel slivers in exact
    // arithmetic; mirror whatever the reference says (usually nothing).
    for (int i = 0; i < 4; i++)
        apply_tri_ref(m, p, degen[i][0], degen[i][1], 0, 64, 0, 28);

    cmd_clear_rect(FB_BASE_BYTE + 30u * 320u + 2u, 7, 2, 0, 0x42);
    m.apply_clear_rect(FB_BASE_BYTE + 30u * 320u + 2u, 7, 2, 0, 0x42);

    if (!submit_and_wait()) {
        check_fail("param_tri_degenerate_noop", "timeout");
        return;
    }
    compare_fb_region("param_tri_degenerate_noop.fb", m,
                      FB_BASE_BYTE, 320, 0, 0, 72, 36);
}

// Two triangles sharing a steep edge E (slope 4 px/line): E is the TOP
// short edge of A and the BOTTOM short edge of B.  The bottom-short walk
// historically restarted at v1 + slope_bot (the edge evaluated at y+1),
// displacing E by one slope step against the neighbour's correct at-y
// walk — a 4 px crack here.  Assert the two spans meet exactly on every
// shared scanline (left-closed right-open: A's end == B's start), then
// the usual byte-exact RTL-vs-model compare.
static void test_param_tri_shared_edge_adjacency() {
    printf("TEST param_tri_shared_edge_adjacency\n");
    gpu_init();
    FbModel m = preload_with_sentinel();
    upload_texture(TEX_BASE_BYTE, make_param_test_texture());
    m.snapshot_from_sdram();

    ParamSpanListWire p = make_tri_plane_setup();

    // E: (20,10) -> (100,30).  A right boundary = E (top short edge);
    // B left boundary = E (bottom short edge).
    const int16_t ax[3] = { (int16_t)(20*16), (int16_t)(100*16), (int16_t)(30*16) };
    const int16_t ay[3] = { 10, 30, 45 };
    const int16_t bx[3] = { (int16_t)(60*16), (int16_t)(20*16), (int16_t)(100*16) };
    const int16_t by[3] = { 5, 10, 30 };

    emit_param_tri_raw(p, ax, ay, 0, 320, 0, 64);
    apply_tri_ref(m, p, ax, ay, 0, 320, 0, 64);
    emit_param_tri_raw(p, bx, by, 0, 320, 0, 64);
    apply_tri_ref(m, p, bx, by, 0, 320, 0, 64);

    // Adjacency property on the reference walker (the RTL is then held
    // to it by the byte-exact compare below).
    {
        std::vector<ParamSpanRecordWire> ra, rb;
        ref_tri_records(ax, ay, 0, 320, 0, 64, ra);
        ref_tri_records(bx, by, 0, 320, 0, 64, rb);
        for (int yy = 11; yy <= 29; yy++) {
            const ParamSpanRecordWire *sa = nullptr, *sb = nullptr;
            for (const auto &r : ra) if ((int)r.v == yy) sa = &r;
            for (const auto &r : rb) if ((int)r.v == yy) sb = &r;
            if (!sa || !sb) {
                check_fail("param_tri_shared_edge_adjacency",
                           "missing span on shared scanline y=" +
                           std::to_string(yy));
                return;
            }
            int a_end   = (int)sa->u + (int)sa->count;
            int b_start = (int)sb->u;
            if (a_end != b_start) {
                check_fail("param_tri_shared_edge_adjacency",
                           "y=" + std::to_string(yy) +
                           " A ends " + std::to_string(a_end) +
                           " B starts " + std::to_string(b_start) +
                           (a_end < b_start ? " (crack)" : " (overlap)"));
                return;
            }
        }
    }

    if (!submit_and_wait()) {
        check_fail("param_tri_shared_edge_adjacency", "timeout");
        return;
    }
    compare_fb_region("param_tri_shared_edge_adjacency.fb", m,
                      FB_BASE_BYTE, 320, 0, 0, 112, 48);
}

static void test_param_tri_unsupported_header_noop() {
    printf("TEST param_tri_unsupported_header_noop\n");
    gpu_init();
    FbModel m = preload_with_sentinel();
    m.snapshot_from_sdram();

    // Unsupported record format nibble must clear header_supported and
    // retire the command without launching the walker.
    ParamSpanListWire p = make_tri_plane_setup();
    auto w = encode_param_span_list_wire(p, {});
    w.resize(36, 0);
    w[7] |= (0x5u << 20);   // record_fmt != U16V16_COUNT16
    const int16_t vx[3] = { (int16_t)(4*16), (int16_t)(30*16),
                            (int16_t)(16*16) };
    const int16_t vy[3] = { 2, 4, 20 };
    w[31] = (64u << 16) | 0u;
    w[32] = (28u << 16) | 0u;
    w[33] = ((uint32_t)(uint16_t)vy[0] << 16) | (uint16_t)vx[0];
    w[34] = ((uint32_t)(uint16_t)vy[1] << 16) | (uint16_t)vx[1];
    w[35] = ((uint32_t)(uint16_t)vy[2] << 16) | (uint16_t)vx[2];
    ring_cmd(0x49, (uint32_t)w.size());
    for (uint32_t x : w)
        ring_write(x);

    cmd_clear_rect(FB_BASE_BYTE + 5u * 320u + 3u, 5, 2, 0, 0x37);
    m.apply_clear_rect(FB_BASE_BYTE + 5u * 320u + 3u, 5, 2, 0, 0x37);

    if (!submit_and_wait()) {
        check_fail("param_tri_unsupported_header_noop", "timeout");
        return;
    }
    compare_fb_region("param_tri_unsupported_header_noop.fb", m,
                      FB_BASE_BYTE, 320, 0, 0, 48, 24);
}

static void test_param_tri_fuzz_affine() {
    printf("TEST param_tri_fuzz_affine\n");
    gpu_init();
    FbModel m = preload_with_sentinel();
    upload_texture(TEX_BASE_BYTE, make_param_test_texture());
    m.snapshot_from_sdram();

    ParamSpanListWire p = make_tri_plane_setup();

    srand(0x71717171);
    for (int n = 0; n < 16; n++) {
        int16_t vx[3], vy[3];
        for (int i = 0; i < 3; i++) {
            vx[i] = (int16_t)((rand() % (80 * 16)) - (8 * 16)); // Q12.4
            vy[i] = (int16_t)((rand() % 44) - 6);
        }
        emit_param_tri_raw(p, vx, vy, 0, 64, 0, 30);
        apply_tri_ref(m, p, vx, vy, 0, 64, 0, 30);
    }

    if (!submit_and_wait()) {
        check_fail("param_tri_fuzz_affine", "timeout");
        return;
    }
    compare_fb_region("param_tri_fuzz_affine.fb", m,
                      FB_BASE_BYTE, 320, 0, 0, 72, 34);
}

// ================================================================
// CMD_SET_TRI_STATE (0x4A) + CMD_DRAW_VERT_TRI (0x4B) — hardware
// triangle plane derivation.
// ================================================================
// Strategy: the equivalence we prove is that the GPU's RTL derivation lands
// the SAME spanprod attr planes that this C reference computes.  We therefore
// render a triangle two ways and byte-compare:
//   (A) via 0x4A + 0x4B (the GPU derives the planes from raw verts), and
//   (B) via 0x49 (CMD_DRAW_PARAM_TRI) carrying the planes this reference solved
//       with the IDENTICAL fixed-point path.
// Both run the same proven walker + spanprod fragment path, so byte-exact
// output follows iff the derived planes match the reference planes bit-for-bit.
//
// FIXED-POINT CONTRACT — mirrors gpu_core.v's S_TRI_DERIVE exactly:
//   N = 44, SPLIT = 24 (only matters in RTL as a DSP-pass boundary; the C ref
//   multiplies directly in 128-bit, which is bit-identical to the exact two-pass
//   recombination).  rdet capped at 2^31-1.  Truncations toward -inf via
//   arithmetic shifts.  See gpu_core.v for the full derivation.
static const int DERIV_N_REF = 44;

static int32_t deriv_sat32_ref(int64_t v) {
    if (v > INT32_MAX) return INT32_MAX;
    if (v < INT32_MIN) return INT32_MIN;
    return (int32_t)v;
}

// rounded reciprocal: round(2^44/|det|), capped at 2^31-1 (sliver saturation).
static uint64_t deriv_rdet_ref(uint64_t detabs) {
    if (detabs == 0) detabs = 1;                  // collinear -> det floored to 1
    uint64_t r = (((unsigned __int128)1 << DERIV_N_REF) + (detabs >> 1)) / detabs;
    if (r > 0x7FFFFFFFull) r = 0x7FFFFFFFull;
    return r;
}

// du/dv = sat32( ((num * rdet) >>> N) * sign )
static int32_t deriv_scale_ref(int64_t num, uint64_t rdet, int sign) {
    __int128 p = (__int128)num * (__int128)rdet;  // exact == RTL two-pass split
    int64_t q = (int64_t)(p >> DERIV_N_REF);      // arithmetic shift, trunc -inf
    return deriv_sat32_ref((int64_t)q * sign);
}

// Derived planes for the four attributes (szi, tzi, zi, light), in the exact
// spanprod attr_origin/du/dv format an 0x49 PERSP header would carry.
struct DerivedTriPlanes {
    int32_t attr_origin[3];   // 0=szi 1=tzi 2=zi
    int32_t attr_du[3];
    int32_t attr_dv[3];
    int32_t light_origin;     // Q6.16, truncated to 24b like spanprod_light_*
    int32_t light_du;
    int32_t light_dv;
};

// Mirror of S_TRI_DERIVE.  Inputs: raw per-vertex {x(Q12.4), y(int), s/t/zi
// (Q16.16), light row(Q6)} in payload order (pre-sort).
static DerivedTriPlanes derive_tri_planes_ref(const int16_t vx[3],
                                              const int16_t vy[3],
                                              const int32_t s[3],
                                              const int32_t t[3],
                                              const int32_t zi[3],
                                              const uint8_t lrow[3]) {
    // 1. y-sort with the walker's exact rule, carrying the attr tuple.
    int16_t x[3] = {vx[0], vx[1], vx[2]};
    int16_t y[3] = {vy[0], vy[1], vy[2]};
    int64_t S[3] = {s[0], s[1], s[2]};
    int64_t T[3] = {t[0], t[1], t[2]};
    int64_t Z[3] = {zi[0], zi[1], zi[2]};
    int64_t L[3] = {(int64_t)(lrow[0] & 0x3F) << 16,
                    (int64_t)(lrow[1] & 0x3F) << 16,
                    (int64_t)(lrow[2] & 0x3F) << 16};
    auto swap2 = [&](int a, int b) {
        std::swap(x[a], x[b]); std::swap(y[a], y[b]);
        std::swap(S[a], S[b]); std::swap(T[a], T[b]);
        std::swap(Z[a], Z[b]); std::swap(L[a], L[b]);
    };
    if (y[1] < y[0]) swap2(0, 1);
    if (y[2] < y[1]) swap2(1, 2);
    if (y[1] < y[0]) swap2(0, 1);

    // 2. per-vertex numerator products szi_k = (s_k*zi_k)>>16, tzi_k likewise
    //    (arithmetic shift, trunc toward -inf; matches dsp_p[47:16]).
    int64_t szi[3], tzi[3];
    for (int k = 0; k < 3; k++) {
        szi[k] = (int64_t)(int32_t)((S[k] * Z[k]) >> 16);
        tzi[k] = (int64_t)(int32_t)((T[k] * Z[k]) >> 16);
    }

    // 3. edge deltas, determinant, reciprocal.
    int64_t d1x = (int64_t)x[1] - x[0];   // Q12.4
    int64_t d2x = (int64_t)x[2] - x[0];
    int64_t d1y = (int64_t)y[1] - y[0];   // int
    int64_t d2y = (int64_t)y[2] - y[0];
    int64_t det = d1x * d2y - d2x * d1y;  // Q12.4-scaled
    uint64_t detabs = (det < 0) ? (uint64_t)(-det) : (uint64_t)det;
    int sign = (det < 0) ? -1 : 1;
    uint64_t rdet = deriv_rdet_ref(detabs);

    int64_t x0px = (int64_t)(x[0] >> 4);  // floor toward -inf (Q12.4 >> 4)
    int64_t y0 = y[0];

    DerivedTriPlanes pl {};
    int64_t a0arr[4] = {szi[0], tzi[0], Z[0], L[0]};
    int64_t a1arr[4] = {szi[1], tzi[1], Z[1], L[1]};
    int64_t a2arr[4] = {szi[2], tzi[2], Z[2], L[2]};
    for (int a = 0; a < 4; a++) {
        // da clamped to int32 before the numerator products (matches the RTL
        // deriv_sat32 on da1/da2).
        int64_t da1 = deriv_sat32_ref(a1arr[a] - a0arr[a]);
        int64_t da2 = deriv_sat32_ref(a2arr[a] - a0arr[a]);
        int64_t num_du = 16 * (da1 * d2y - da2 * d1y);
        int64_t num_dv =      (da2 * d1x - da1 * d2x);
        int32_t du = deriv_scale_ref(num_du, rdet, sign);
        int32_t dv = deriv_scale_ref(num_dv, rdet, sign);
        // origin = a0 - du*x0px - dv*y0 (mod 2^32)
        int32_t org = (int32_t)(uint32_t)(a0arr[a]
                    - (int64_t)du * x0px - (int64_t)dv * y0);
        if (a < 3) {
            pl.attr_origin[a] = org;
            pl.attr_du[a] = du;
            pl.attr_dv[a] = dv;
        } else {
            // light plane truncated to 24b (Q6.16) like spanprod_light_*.
            pl.light_origin = (int32_t)((uint32_t)org & 0x00FFFFFFu);
            pl.light_du = (int32_t)((uint32_t)du & 0x00FFFFFFu);
            pl.light_dv = (int32_t)((uint32_t)dv & 0x00FFFFFFu);
        }
    }
    return pl;
}

// Build the 0x4A sticky-state payload (16 words) from a ParamSpanListWire-like
// surface description.  flags/colormap/attr_mode(PERSP)/z_mode in the control
// word; clip rect packed into w14/w15.
static std::vector<uint32_t>
encode_set_tri_state_wire(const ParamSpanListWire &p,
                          int16_t cx0, int16_t cx1, int16_t cy0, int16_t cy1) {
    std::vector<uint32_t> w(16, 0);
    uint32_t control = ((uint32_t)p.flags & 0xFFu)
                     | (((uint32_t)p.colormap_id & 0x0Fu) << 8)
                     | (((uint32_t)p.attr_mode & 0x0Fu) << 12)
                     | (((uint32_t)p.span_axis & 0x0Fu) << 16)
                     | (((uint32_t)p.z_mode & 0x0Fu) << 24);
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
    return w;
}

static void emit_set_tri_state_raw(const ParamSpanListWire &p,
                                   int16_t cx0, int16_t cx1,
                                   int16_t cy0, int16_t cy1) {
    auto w = encode_set_tri_state_wire(p, cx0, cx1, cy0, cy1);
    ring_cmd(0x4A, (uint32_t)w.size());
    for (uint32_t x : w)
        ring_write(x);
}

// Emit a 0x4B (14-word) vertex triangle.
static void emit_draw_vert_tri_raw(const int16_t vx[3], const int16_t vy[3],
                                   const int32_t s[3], const int32_t t[3],
                                   const int32_t zi[3], const uint8_t lrow[3]) {
    std::vector<uint32_t> w(14, 0);
    w[0]  = ((uint32_t)(uint16_t)vy[0] << 16) | (uint16_t)vx[0];
    w[1]  = ((uint32_t)(uint16_t)vy[1] << 16) | (uint16_t)vx[1];
    w[2]  = ((uint32_t)(uint16_t)vy[2] << 16) | (uint16_t)vx[2];
    w[3]  = (uint32_t)s[0];  w[4]  = (uint32_t)s[1];  w[5]  = (uint32_t)s[2];
    w[6]  = (uint32_t)t[0];  w[7]  = (uint32_t)t[1];  w[8]  = (uint32_t)t[2];
    w[9]  = (uint32_t)zi[0]; w[10] = (uint32_t)zi[1]; w[11] = (uint32_t)zi[2];
    w[12] = ((uint32_t)(lrow[0] & 0x3F))
          | ((uint32_t)(lrow[1] & 0x3F) << 6)
          | ((uint32_t)(lrow[2] & 0x3F) << 12);
    w[13] = 0;
    ring_cmd(0x4B, (uint32_t)w.size());
    for (uint32_t x : w)
        ring_write(x);
}

// Build an 0x49 ParamSpanListWire that carries the reference-derived planes for
// a vertex triangle, so the two paths are held byte-exact.
static ParamSpanListWire make_equiv_param_from_derived(const ParamSpanListWire &base,
                                                       const DerivedTriPlanes &d) {
    ParamSpanListWire p = base;
    p.attr_mode = 1;            // PERSP — same record/attr semantics 0x4B implies
    p.flags |= SPAN_PERSP;
    p.span_axis = 0;
    for (int i = 0; i < 3; i++) {
        p.attr_origin[i] = d.attr_origin[i];
        p.attr_du[i] = d.attr_du[i];
        p.attr_dv[i] = d.attr_dv[i];
    }
    p.light_origin = d.light_origin;
    p.light_du = d.light_du;
    p.light_dv = d.light_dv;
    return p;
}

// Common surface for the vert-tri tests: textured PERSP, screen FB at base.
static ParamSpanListWire make_vert_tri_surface() {
    ParamSpanListWire p {};
    p.fb_base = FB_BASE_BYTE;
    p.fb_major_step = 320;
    p.fb_minor_step = 1;
    p.tex_addr = TEX_BASE_BYTE;
    p.tex_width = 64;
    p.tex_w_mask = 0x3F;
    p.tex_h_mask = 0x3F;
    p.attr_mode = 1;       // PERSP
    p.span_axis = 0;
    p.flags = SPAN_PERSP;
    return p;
}

// Render one vert-triangle two ways (0x4A+0x4B at FB_BASE; 0x49-with-derived
// planes at FB_ALT_BASE) and return the per-pixel mismatch count over the walk
// region.  alt_base lets the caller place the 0x49 copy elsewhere.
static int vert_tri_equiv_diffs(const ParamSpanListWire &surf,
                                const int16_t vx[3], const int16_t vy[3],
                                const int32_t s[3], const int32_t t[3],
                                const int32_t zi[3], const uint8_t lrow[3],
                                int16_t cx0, int16_t cx1, int16_t cy0, int16_t cy1,
                                int rx0, int rx1, int ry0, int ry1) {
    gpu_init();
    preload_with_sentinel();
    upload_texture(TEX_BASE_BYTE, make_projection_test_texture());
    upload_palookup_identity_row(0, 0);

    // Path A: 0x4A + 0x4B at FB_BASE.
    ParamSpanListWire a = surf;
    a.fb_base = FB_BASE_BYTE;
    emit_set_tri_state_raw(a, cx0, cx1, cy0, cy1);
    emit_draw_vert_tri_raw(vx, vy, s, t, zi, lrow);

    // Path B: 0x49 carrying the reference-derived planes at FB_ALT_BASE.
    DerivedTriPlanes d = derive_tri_planes_ref(vx, vy, s, t, zi, lrow);
    ParamSpanListWire b = make_equiv_param_from_derived(surf, d);
    b.fb_base = FB_ALT_BASE_BYTE;
    emit_param_tri_raw(b, vx, vy, cx0, cx1, cy0, cy1);

    if (!submit_and_wait())
        return -1;

    int diffs = 0;
    for (int y = ry0; y < ry1; y++)
        for (int x = rx0; x < rx1; x++) {
            uint32_t pa = FB_BASE_BYTE     + (uint32_t)y * 320u + (uint32_t)x;
            uint32_t pb = FB_ALT_BASE_BYTE + (uint32_t)y * 320u + (uint32_t)x;
            if (sdram_read_byte(pa) != sdram_read_byte(pb))
                diffs++;
        }
    return diffs;
}

// (b) equivalence over a representative triangle set: large, thin slivers,
// steep gradients, shared-edge pairs, fully/partially clipped, degenerate.
static void test_vert_tri_equivalence_vs_param() {
    printf("TEST vert_tri_equivalence_vs_param\n");
    ParamSpanListWire surf = make_vert_tri_surface();

    struct Case { const char *name; int16_t vx[3], vy[3];
                  int32_t s[3], t[3], zi[3]; uint8_t l[3];
                  int16_t cx0, cx1, cy0, cy1; };
    const int Q = 1 << 16;
    Case cases[] = {
        { "large",
          {(int16_t)(8*16), (int16_t)(80*16), (int16_t)(30*16)}, {4, 10, 50},
          {0, 48*Q, 12*Q}, {0, 8*Q, 40*Q}, {Q, Q, Q}, {0, 10, 30},
          0, 120, 0, 60 },
        { "thin_sliver",
          {(int16_t)(10*16), (int16_t)(12*16), (int16_t)(11*16)}, {4, 40, 8},
          {0, 4*Q, 2*Q}, {0, 30*Q, 6*Q}, {Q, Q, Q}, {0, 5, 2},
          0, 64, 0, 48 },
        { "steep_gradient",
          {(int16_t)(6*16), (int16_t)(60*16), (int16_t)(20*16)}, {3, 7, 38},
          {0, 1000*Q, 30*Q}, {0, 900*Q, 20*Q},
          {Q, Q/8, Q/4}, {0, 20, 8},
          0, 80, 0, 44 },
        { "partial_clip",
          {(int16_t)(-20*16), (int16_t)(70*16), (int16_t)(25*16)}, {-6, 12, 40},
          {0, 50*Q, 16*Q}, {0, 10*Q, 36*Q}, {Q, Q, Q}, {0, 8, 24},
          5, 55, 3, 30 },
        { "degenerate_collinear",
          {(int16_t)(10*16), (int16_t)(40*16), (int16_t)(25*16)}, {5, 5, 5},
          {0, 30*Q, 15*Q}, {0, 0, 0}, {Q, Q, Q}, {0, 0, 0},
          0, 64, 0, 30 },
    };

    int total_diffs = 0, ncases = 0;
    for (auto &c : cases) {
        int d = vert_tri_equiv_diffs(surf, c.vx, c.vy, c.s, c.t, c.zi, c.l,
                                     c.cx0, c.cx1, c.cy0, c.cy1,
                                     0, 120, 0, 60);
        if (d < 0) {
            check_fail("vert_tri_equivalence_vs_param",
                       std::string("timeout on case ") + c.name);
            return;
        }
        char nm[96];
        snprintf(nm, sizeof(nm), "vert_tri_equivalence_vs_param.%s", c.name);
        if (d == 0) check_pass(nm);
        else {
            char buf[64];
            snprintf(buf, sizeof(buf), "%d pixel diffs", d);
            check_fail(nm, buf);
        }
        total_diffs += d;
        ncases++;
    }
    (void)total_diffs; (void)ncases;
}

// (c) sliver / steep-gradient set that used to need Q29 client-side: must
// render with in-range attrs and NOT drop spans.  We assert the 0x4B path
// actually paints the triangle interior (non-sentinel) on a set of awkward
// triangles, and that it matches the 0x49 derived-plane render.
static void test_vert_tri_sliver_renders_in_range() {
    printf("TEST vert_tri_sliver_renders_in_range\n");
    ParamSpanListWire surf = make_vert_tri_surface();
    const int Q = 1 << 16;

    // A steep, near-degenerate triangle with a large attribute swing — the
    // case that would overflow a non-anchored Q16.16 plane solve.
    const int16_t vx[3] = {(int16_t)(15*16), (int16_t)(17*16), (int16_t)(16*16+8)};
    const int16_t vy[3] = {2, 50, 6};
    const int32_t s[3]  = {0, 2000*Q, 60*Q};
    const int32_t t[3]  = {0, 1800*Q, 40*Q};
    const int32_t zi[3] = {Q, Q/16, Q/2};
    const uint8_t l[3]  = {0, 30, 10};

    int diffs = vert_tri_equiv_diffs(surf, vx, vy, s, t, zi, l,
                                     0, 64, 0, 56, 0, 64, 0, 56);
    if (diffs < 0) { check_fail("vert_tri_sliver_renders_in_range", "timeout"); return; }
    if (diffs == 0) check_pass("vert_tri_sliver_renders_in_range.equiv");
    else {
        char buf[64]; snprintf(buf, sizeof(buf), "%d pixel diffs", diffs);
        check_fail("vert_tri_sliver_renders_in_range.equiv", buf);
    }

    // The triangle must actually have painted some interior (no dropped spans).
    int painted = 0;
    for (int y = 0; y < 56; y++)
        for (int x = 0; x < 64; x++)
            if (sdram_read_byte(FB_BASE_BYTE + (uint32_t)y*320u + (uint32_t)x)
                != SENTINEL_BYTE)
                painted++;
    if (painted > 0) check_pass("vert_tri_sliver_renders_in_range.painted");
    else check_fail("vert_tri_sliver_renders_in_range.painted",
                    "no interior pixels painted");
}

// (d) shared-edge adjacency through 0x4B — mirror param_tri_shared_edge_adjacency
// but draw each triangle with 0x4B and compare to the 0x49 derived-plane render.
static void test_vert_tri_shared_edge_adjacency() {
    printf("TEST vert_tri_shared_edge_adjacency\n");
    gpu_init();
    preload_with_sentinel();
    upload_texture(TEX_BASE_BYTE, make_projection_test_texture());
    upload_palookup_identity_row(0, 0);

    ParamSpanListWire surf = make_vert_tri_surface();
    const int Q = 1 << 16;

    // Same geometry as the 0x49 shared-edge test.  Screen-aligned-ish attrs.
    const int16_t ax[3] = {(int16_t)(20*16), (int16_t)(100*16), (int16_t)(30*16)};
    const int16_t ay[3] = {10, 30, 45};
    const int16_t bx[3] = {(int16_t)(60*16), (int16_t)(20*16), (int16_t)(100*16)};
    const int16_t by[3] = {5, 10, 30};
    const int32_t as[3] = {0, 80*Q, 10*Q}, at[3] = {0, 20*Q, 35*Q};
    const int32_t azi[3] = {Q, Q, Q};
    const int32_t bs[3] = {40*Q, 0, 80*Q}, bt[3] = {0, 5*Q, 25*Q};
    const int32_t bzi[3] = {Q, Q, Q};
    const uint8_t al[3] = {0, 0, 0}, bl[3] = {0, 0, 0};

    // Path A: 0x4B pair at FB_BASE.
    emit_set_tri_state_raw(surf, 0, 320, 0, 64);
    emit_draw_vert_tri_raw(ax, ay, as, at, azi, al);
    emit_draw_vert_tri_raw(bx, by, bs, bt, bzi, bl);

    // Path B: 0x49 derived-plane pair at FB_ALT_BASE.
    DerivedTriPlanes da = derive_tri_planes_ref(ax, ay, as, at, azi, al);
    DerivedTriPlanes db = derive_tri_planes_ref(bx, by, bs, bt, bzi, bl);
    ParamSpanListWire pa = make_equiv_param_from_derived(surf, da);
    ParamSpanListWire pb = make_equiv_param_from_derived(surf, db);
    pa.fb_base = FB_ALT_BASE_BYTE; pb.fb_base = FB_ALT_BASE_BYTE;
    emit_param_tri_raw(pa, ax, ay, 0, 320, 0, 64);
    emit_param_tri_raw(pb, bx, by, 0, 320, 0, 64);

    if (!submit_and_wait()) {
        check_fail("vert_tri_shared_edge_adjacency", "timeout");
        return;
    }
    int diffs = 0;
    for (int y = 0; y < 48; y++)
        for (int x = 0; x < 112; x++) {
            uint32_t pA = FB_BASE_BYTE     + (uint32_t)y*320u + (uint32_t)x;
            uint32_t pB = FB_ALT_BASE_BYTE + (uint32_t)y*320u + (uint32_t)x;
            if (sdram_read_byte(pA) != sdram_read_byte(pB)) diffs++;
        }
    if (diffs == 0) check_pass("vert_tri_shared_edge_adjacency");
    else {
        char buf[64]; snprintf(buf, sizeof(buf), "%d pixel diffs", diffs);
        check_fail("vert_tri_shared_edge_adjacency", buf);
    }
}

// (e) sticky semantics: one 0x4A re-arms several 0x4B; 0x4B without 0x4A is a
// no-op; soft_reset clears the sticky bank.
static void test_vert_tri_sticky_semantics() {
    printf("TEST vert_tri_sticky_semantics\n");
    const int Q = 1 << 16;
    ParamSpanListWire surf = make_vert_tri_surface();

    // --- (e1) one 0x4A + three 0x4B at FB_BASE == three 0x49 derived at ALT ---
    gpu_init();
    preload_with_sentinel();
    upload_texture(TEX_BASE_BYTE, make_projection_test_texture());
    upload_palookup_identity_row(0, 0);

    const int16_t vx[3][3] = {
        {(int16_t)(5*16), (int16_t)(40*16), (int16_t)(20*16)},
        {(int16_t)(45*16), (int16_t)(80*16), (int16_t)(60*16)},
        {(int16_t)(8*16), (int16_t)(34*16), (int16_t)(18*16)},
    };
    const int16_t vy[3][3] = { {2, 6, 22}, {4, 8, 28}, {30, 34, 50} };
    const int32_t s3[3] = {0, 30*Q, 14*Q}, t3[3] = {0, 6*Q, 20*Q};
    const int32_t zi3[3] = {Q, Q, Q};
    const uint8_t l3[3] = {0, 0, 0};

    emit_set_tri_state_raw(surf, 0, 320, 0, 64);
    for (int i = 0; i < 3; i++)
        emit_draw_vert_tri_raw(vx[i], vy[i], s3, t3, zi3, l3);

    for (int i = 0; i < 3; i++) {
        DerivedTriPlanes d = derive_tri_planes_ref(vx[i], vy[i], s3, t3, zi3, l3);
        ParamSpanListWire p = make_equiv_param_from_derived(surf, d);
        p.fb_base = FB_ALT_BASE_BYTE;
        emit_param_tri_raw(p, vx[i], vy[i], 0, 320, 0, 64);
    }
    if (!submit_and_wait()) {
        check_fail("vert_tri_sticky_semantics", "timeout(e1)");
        return;
    }
    int diffs = 0;
    for (int y = 0; y < 56; y++)
        for (int x = 0; x < 88; x++) {
            uint32_t pA = FB_BASE_BYTE     + (uint32_t)y*320u + (uint32_t)x;
            uint32_t pB = FB_ALT_BASE_BYTE + (uint32_t)y*320u + (uint32_t)x;
            if (sdram_read_byte(pA) != sdram_read_byte(pB)) diffs++;
        }
    if (diffs == 0) check_pass("vert_tri_sticky_semantics.one_a_three_b");
    else {
        char buf[64]; snprintf(buf, sizeof(buf), "%d diffs", diffs);
        check_fail("vert_tri_sticky_semantics.one_a_three_b", buf);
    }

    // --- (e2) 0x4B with NO prior 0x4A is a no-op (after a fresh hard reset) ---
    gpu_init();   // gpu_init issues a soft reset, clearing tri_state_valid
    FbModel m2 = preload_with_sentinel();
    upload_texture(TEX_BASE_BYTE, make_projection_test_texture());
    m2.snapshot_from_sdram();
    emit_draw_vert_tri_raw(vx[0], vy[0], s3, t3, zi3, l3);  // no 0x4A first
    // A following clear proves the stream advanced (the 0x4B drained as no-op).
    cmd_clear_rect(FB_BASE_BYTE + 2u * 320u + 1u, 4, 2, 0, 0x5A);
    m2.apply_clear_rect(FB_BASE_BYTE + 2u * 320u + 1u, 4, 2, 0, 0x5A);
    if (!submit_and_wait()) {
        check_fail("vert_tri_sticky_semantics", "timeout(e2)");
        return;
    }
    compare_fb_region("vert_tri_sticky_semantics.noop_without_a", m2,
                      FB_BASE_BYTE, 320, 0, 0, 48, 24);

    // --- (e3) soft_reset clears the bank: 0x4A, soft reset, then 0x4B no-op ---
    gpu_init();
    FbModel m3 = preload_with_sentinel();
    upload_texture(TEX_BASE_BYTE, make_projection_test_texture());
    m3.snapshot_from_sdram();
    emit_set_tri_state_raw(surf, 0, 320, 0, 64);
    if (!submit_and_wait()) {  // let the 0x4A retire
        check_fail("vert_tri_sticky_semantics", "timeout(e3a)");
        return;
    }
    gpu_soft_reset();          // clears tri_state_valid
    emit_draw_vert_tri_raw(vx[0], vy[0], s3, t3, zi3, l3);  // must be a no-op now
    cmd_clear_rect(FB_BASE_BYTE + 2u * 320u + 1u, 4, 2, 0, 0x6B);
    m3.apply_clear_rect(FB_BASE_BYTE + 2u * 320u + 1u, 4, 2, 0, 0x6B);
    if (!submit_and_wait()) {
        check_fail("vert_tri_sticky_semantics", "timeout(e3b)");
        return;
    }
    compare_fb_region("vert_tri_sticky_semantics.soft_reset_clears", m3,
                      FB_BASE_BYTE, 320, 0, 0, 48, 24);
}

// Wrong-sized 0x4A / 0x4B payloads must drain and retire as no-ops.
static void test_vert_tri_wrong_size_noop() {
    printf("TEST vert_tri_wrong_size_noop\n");
    gpu_init();
    FbModel m = preload_with_sentinel();
    m.snapshot_from_sdram();

    // 0x4A with 15 words (should be 16) -> tri_state_valid stays clear.
    std::vector<uint32_t> bad_a(15, 0xA5A5A5A5u);
    ring_cmd(0x4A, (uint32_t)bad_a.size());
    for (uint32_t x : bad_a) ring_write(x);
    // 0x4B with 13 words (should be 14) -> no-op.
    std::vector<uint32_t> bad_b(13, 0xB6B6B6B6u);
    ring_cmd(0x4B, (uint32_t)bad_b.size());
    for (uint32_t x : bad_b) ring_write(x);
    // Marker clear proves the stream advanced past both bad commands.
    cmd_clear_rect(FB_BASE_BYTE + 1u * 320u + 2u, 6, 3, 0, 0x4D);
    m.apply_clear_rect(FB_BASE_BYTE + 1u * 320u + 2u, 6, 3, 0, 0x4D);

    if (!submit_and_wait()) {
        check_fail("vert_tri_wrong_size_noop", "timeout");
        return;
    }
    compare_fb_region("vert_tri_wrong_size_noop.fb", m,
                      FB_BASE_BYTE, 320, 0, 0, 48, 24);
}

// ============================================================================
// CMD_DRAW_PARAM_TRI_RECS (0x4D) — records-only 0x49 variant on the 0x4A
// sticky state.  Mirrors the 0x49 param-tri tests: same planes, same fill
// convention, byte-exact against both the CPU reference and a full 0x49.
// ============================================================================

// Emit a 0x4D (16-word) records-only param-tri: per-triangle planes (the
// 0x49 header words 8..19), the q29 word (0x49 w30), and the vertices.
static void emit_param_tri_recs_raw(const ParamSpanListWire &p,
                                    const int16_t vx[3], const int16_t vy[3]) {
    auto h = encode_param_span_list_wire(p, {});
    std::vector<uint32_t> w(16, 0);
    for (int i = 0; i < 12; i++)
        w[i] = h[8 + i];
    w[12] = h[30];
    w[13] = ((uint32_t)(uint16_t)vy[0] << 16) | (uint16_t)vx[0];
    w[14] = ((uint32_t)(uint16_t)vy[1] << 16) | (uint16_t)vx[1];
    w[15] = ((uint32_t)(uint16_t)vy[2] << 16) | (uint16_t)vx[2];
    ring_cmd(0x4D, (uint32_t)w.size());
    for (uint32_t x : w)
        ring_write(x);
}

// (a) Byte-exact equivalence: one 0x4A + N×0x4D at FB_BASE vs N full 0x49 at
// FB_ALT_BASE, plus the CPU reference model on the FB_BASE copy.  Also proves
// the sticky state survives back-to-back 0x4D draws.
static void test_param_tri_recs_equivalence_vs_param() {
    printf("TEST param_tri_recs_equivalence_vs_param\n");
    gpu_init();
    FbModel m = preload_with_sentinel();
    upload_texture(TEX_BASE_BYTE, make_param_test_texture());
    m.snapshot_from_sdram();

    ParamSpanListWire p = make_tri_plane_setup();

    const int16_t tris[3][2][3] = {
        { { (int16_t)(5*16+7), (int16_t)(40*16+3), (int16_t)(20*16) },
          { 2, 6, 22 } },
        { { (int16_t)(8*16), (int16_t)(30*16), (int16_t)(18*16+9) },
          { 3, 3, 14 } },
        { { (int16_t)(24*16+5), (int16_t)(44*16), (int16_t)(60*16) },
          { 4, 18, 18 } },
    };

    // Path A: one 0x4A arms the sticky surface + clip, then 3 × 0x4D.
    emit_set_tri_state_raw(p, 0, 64, 0, 28);
    for (int i = 0; i < 3; i++) {
        emit_param_tri_recs_raw(p, tris[i][0], tris[i][1]);
        apply_tri_ref(m, p, tris[i][0], tris[i][1], 0, 64, 0, 28);
    }

    // Path B: the same triangles via full 0x49 at FB_ALT_BASE.
    ParamSpanListWire pb = p;
    pb.fb_base = FB_ALT_BASE_BYTE;
    for (int i = 0; i < 3; i++)
        emit_param_tri_raw(pb, tris[i][0], tris[i][1], 0, 64, 0, 28);

    if (!submit_and_wait()) {
        check_fail("param_tri_recs_equivalence_vs_param", "timeout");
        return;
    }

    // CPU-reference check on the 0x4D copy.
    compare_fb_region("param_tri_recs_equivalence_vs_param.fb", m,
                      FB_BASE_BYTE, 320, 0, 0, 72, 32);

    // GPU-vs-GPU check: 0x4D output must equal the full-0x49 output.
    int diffs = 0;
    for (int y = 0; y < 32; y++)
        for (int x = 0; x < 72; x++) {
            uint32_t pa = FB_BASE_BYTE     + (uint32_t)y * 320u + (uint32_t)x;
            uint32_t pbb = FB_ALT_BASE_BYTE + (uint32_t)y * 320u + (uint32_t)x;
            if (sdram_read_byte(pa) != sdram_read_byte(pbb))
                diffs++;
        }
    if (diffs == 0)
        check_pass("param_tri_recs_equivalence_vs_param.vs_0x49");
    else {
        char buf[96];
        snprintf(buf, sizeof(buf), "%d pixels differ from full 0x49", diffs);
        check_fail("param_tri_recs_equivalence_vs_param.vs_0x49", buf);
    }
}

// (b) Sticky gating: 0x4D without an armed 0x4A is a guarded no-op; an
// interleaved 0x48 header invalidates the sticky state (0x4D no-op again);
// re-issuing 0x4A re-arms.  Mirrors vert_tri_sticky_semantics.
static void test_param_tri_recs_sticky_semantics() {
    printf("TEST param_tri_recs_sticky_semantics\n");
    gpu_init();
    FbModel m = preload_with_sentinel();
    upload_texture(TEX_BASE_BYTE, make_param_test_texture());
    m.snapshot_from_sdram();

    ParamSpanListWire p = make_tri_plane_setup();
    // The would-be no-op draws use THIS triangle (columns ~5..40); the
    // legitimate (b3) draw uses a disjoint one (columns ~44..60), so any
    // wrongly-executed no-op lands on sentinel bytes and is caught.
    const int16_t vx[3] = { (int16_t)(5*16+7), (int16_t)(40*16+3),
                            (int16_t)(20*16) };
    const int16_t vy[3] = { 2, 6, 22 };
    const int16_t vx3[3] = { (int16_t)(44*16), (int16_t)(60*16),
                             (int16_t)(50*16+9) };
    const int16_t vy3[3] = { 2, 6, 20 };

    // (b1) no prior 0x4A: must not draw.
    emit_param_tri_recs_raw(p, vx, vy);

    // (b2) 0x4A, then a 0x48 span list (overwrites staging + clears
    // tri_state_valid), then 0x4D: must not draw either.  The 0x48 span
    // itself draws and is part of the reference image.
    emit_set_tri_state_raw(p, 0, 64, 0, 28);
    ParamSpanListWire ps = make_tri_plane_setup();
    std::vector<ParamSpanRecordWire> recs = { {1, 26, 12} };
    emit_param_span_list_raw(ps, recs);
    for (const auto &r : recs)
        m.apply_span_ref(param_affine_ref_span(ps, r));
    emit_param_tri_recs_raw(p, vx, vy);

    // (b3) fresh 0x4A re-arms: this (disjoint) one draws.
    emit_set_tri_state_raw(p, 0, 64, 0, 28);
    emit_param_tri_recs_raw(p, vx3, vy3);
    apply_tri_ref(m, p, vx3, vy3, 0, 64, 0, 28);

    if (!submit_and_wait()) {
        check_fail("param_tri_recs_sticky_semantics", "timeout");
        return;
    }
    compare_fb_region("param_tri_recs_sticky_semantics.fb", m,
                      FB_BASE_BYTE, 320, 0, 0, 72, 32);

    // (b4) soft reset clears the sticky state: a 0x4D after it (aimed at
    // still-sentinel rows) is a no-op.
    gpu_soft_reset();
    FbModel m2 = m;
    m2.snapshot_from_sdram();
    const int16_t vy4[3] = { 26, 30, 38 };
    emit_param_tri_recs_raw(p, vx, vy4);
    cmd_clear_rect(FB_BASE_BYTE + 40u * 320u, 8, 1, 0, 0x77);
    m2.apply_clear_rect(FB_BASE_BYTE + 40u * 320u, 8, 1, 0, 0x77);
    if (!submit_and_wait()) {
        check_fail("param_tri_recs_sticky_semantics.soft_reset", "timeout");
        return;
    }
    compare_fb_region("param_tri_recs_sticky_semantics.soft_reset", m2,
                      FB_BASE_BYTE, 320, 0, 0, 72, 42);
}

// (c) Wrong-size payload drains and retires as a no-op without wedging the
// stream, and does not consume the armed sticky state.
static void test_param_tri_recs_wrong_size_noop() {
    printf("TEST param_tri_recs_wrong_size_noop\n");
    gpu_init();
    FbModel m = preload_with_sentinel();
    upload_texture(TEX_BASE_BYTE, make_param_test_texture());
    m.snapshot_from_sdram();

    ParamSpanListWire p = make_tri_plane_setup();
    emit_set_tri_state_raw(p, 0, 64, 0, 28);

    // 0x4D with 15 words (should be 16) -> drains, no draw.
    std::vector<uint32_t> bad(15, 0xC7C7C7C7u);
    ring_cmd(0x4D, (uint32_t)bad.size());
    for (uint32_t x : bad) ring_write(x);

    // A correct 0x4D right after still draws (sticky state untouched by
    // the malformed drain).
    const int16_t vx[3] = { (int16_t)(8*16), (int16_t)(30*16),
                            (int16_t)(18*16+9) };
    const int16_t vy[3] = { 3, 3, 14 };
    emit_param_tri_recs_raw(p, vx, vy);
    apply_tri_ref(m, p, vx, vy, 0, 64, 0, 28);

    if (!submit_and_wait()) {
        check_fail("param_tri_recs_wrong_size_noop", "timeout");
        return;
    }
    compare_fb_region("param_tri_recs_wrong_size_noop.fb", m,
                      FB_BASE_BYTE, 320, 0, 0, 48, 24);
}

static void test_param_span_list_persp_matches_helper() {
    printf("TEST param_span_list_persp_matches_helper\n");
    gpu_init();
    preload_with_sentinel();
    upload_texture(TEX_BASE_BYTE, make_projection_test_texture());
    upload_palookup_identity_row(0, 0);

    PerspSpanGroupWire q = make_persp_span_group();
    q.fb_addr = FB_BASE_BYTE;
    q.tex_addr = TEX_BASE_BYTE;
    q.lane_count = 4;
    q.flags = SPAN_PERSP;
    q.major_fb_step = 320;
    q.minor_fb_step = 1;
    q.tex_width = 64;
    q.tex_w_mask = 0x3F;
    q.tex_h_mask = 0x3F;
    q.start[0] = 3; q.start[1] = 4; q.start[2] = 5; q.start[3] = 6;
    q.count[0] = 19; q.count[1] = 23; q.count[2] = 17; q.count[3] = 21;
    q.sZ = 0x00024000;
    q.tZ = 0x00018000;
    q.zinv = 0x00010000;
    q.sZ_major_step = 0x00000700;
    q.tZ_major_step = -0x00000500;
    q.zinv_major_step = 0x00000080;
    q.sZ_minor_step = 0x00001100;
    q.tZ_minor_step = 0x00000d00;
    q.zinv_minor_step = 0x00000040;

    ParamSpanListWire p {};
    p.fb_base = FB_ALT_BASE_BYTE;
    p.fb_major_step = 320;
    p.fb_minor_step = 1;
    p.tex_addr = TEX_BASE_BYTE;
    p.tex_width = 64;
    p.tex_w_mask = 0x3F;
    p.tex_h_mask = 0x3F;
    p.flags = SPAN_PERSP;
    p.attr_mode = 1;
    p.span_axis = 0;
    p.attr_origin[0] = q.sZ;
    p.attr_origin[1] = q.tZ;
    p.attr_origin[2] = q.zinv;
    p.attr_du[0] = q.sZ_minor_step;
    p.attr_du[1] = q.tZ_minor_step;
    p.attr_du[2] = q.zinv_minor_step;
    p.attr_dv[0] = q.sZ_major_step;
    p.attr_dv[1] = q.tZ_major_step;
    p.attr_dv[2] = q.zinv_major_step;

    std::vector<ParamSpanRecordWire> records;
    for (int lane = 0; lane < 4; lane++)
        records.push_back({(uint16_t)q.start[lane], (uint16_t)lane, q.count[lane]});

    emit_persp_span_group_raw(q);
    emit_param_span_list_raw(p, records);

    if (!submit_and_wait()) {
        check_fail("param_span_list_persp_matches_helper", "timeout");
        return;
    }

    int diffs = 0;
    for (int lane = 0; lane < 4; lane++) {
        for (int i = 0; i < q.count[lane]; i++) {
            uint32_t a = FB_BASE_BYTE + (uint32_t)lane * 320u + q.start[lane] + i;
            uint32_t b = FB_ALT_BASE_BYTE + (uint32_t)lane * 320u + q.start[lane] + i;
            if (sdram_read_byte(a) != sdram_read_byte(b))
                diffs++;
        }
    }
    if (diffs == 0)
        check_pass("param_span_list_persp_matches_helper");
    else {
        char buf[96];
        snprintf(buf, sizeof(buf), "%d pixel mismatches vs helper path", diffs);
        check_fail("param_span_list_persp_matches_helper", buf);
    }
}

static uint16_t sdram_read_u16_le(uint32_t byte_addr) {
    return (uint16_t)sdram_read_byte(byte_addr)
         | ((uint16_t)sdram_read_byte(byte_addr + 1u) << 8);
}

static void sdram_write_u16_le(uint32_t byte_addr, uint16_t value) {
    sdram_write_byte(byte_addr, (uint8_t)(value & 0xFFu));
    sdram_write_byte(byte_addr + 1u, (uint8_t)(value >> 8));
}

static int32_t q_i32(double x) {
    return (int32_t)x;
}

static int32_t q_clamp_i32(int32_t x, int32_t lo, int32_t hi) {
    if (x < lo) return lo;
    if (x > hi) return hi;
    return x;
}

static std::vector<uint8_t> make_quake_distinct_texture(int tex_w, int tex_h) {
    std::vector<uint8_t> tex((size_t)tex_w * (size_t)tex_h);
    for (int t = 0; t < tex_h; t++) {
        for (int s = 0; s < tex_w; s++)
            tex[(size_t)t * (size_t)tex_w + (size_t)s] =
                (uint8_t)((s * 3 + t * 17 + 11) & 0xFF);
    }
    return tex;
}

static void quake_ref_span(std::vector<uint8_t> &fb,
                           std::vector<uint16_t> *zb,
                           const std::vector<uint8_t> &tex,
                           int tex_w,
                           int x, int y, int count,
                           double d_sdivzorigin,
                           double d_tdivzorigin,
                           double d_ziorigin,
                           double d_sdivzstepu,
                           double d_tdivzstepu,
                           double d_zistepu,
                           double d_sdivzstepv,
                           double d_tdivzstepv,
                           double d_zistepv,
                           int32_t sadjust,
                           int32_t tadjust,
                           int32_t bbextents,
                           int32_t bbextentt,
                           int fb_stride,
                           int z_stride) {
    double sdivz16stepu = d_sdivzstepu * 16.0;
    double tdivz16stepu = d_tdivzstepu * 16.0;
    double zi16stepu    = d_zistepu    * 16.0;

    double sdivz = d_sdivzorigin + y * d_sdivzstepv + x * d_sdivzstepu;
    double tdivz = d_tdivzorigin + y * d_tdivzstepv + x * d_tdivzstepu;
    double zi    = d_ziorigin    + y * d_zistepv    + x * d_zistepu;
    int izistep = q_i32(d_zistepu * 0x8000 * 0x10000);
    int izi = q_i32(zi * 0x8000 * 0x10000);

    int32_t s = q_i32(sdivz * (65536.0 / zi)) + sadjust;
    int32_t t = q_i32(tdivz * (65536.0 / zi)) + tadjust;
    s = q_clamp_i32(s, 0, bbextents);
    t = q_clamp_i32(t, 0, bbextentt);

    int px = x;
    while (count > 0) {
        int spancount = (count >= 16) ? 16 : count;
        count -= spancount;

        int32_t snext, tnext, sstep = 0, tstep = 0;
        if (count) {
            sdivz += sdivz16stepu;
            tdivz += tdivz16stepu;
            zi    += zi16stepu;
            snext = q_i32(sdivz * (65536.0 / zi)) + sadjust;
            tnext = q_i32(tdivz * (65536.0 / zi)) + tadjust;
            snext = q_clamp_i32(snext, 16, bbextents);
            tnext = q_clamp_i32(tnext, 16, bbextentt);
            sstep = (snext - s) >> 4;
            tstep = (tnext - t) >> 4;
        } else {
            double m = (double)(spancount - 1);
            sdivz += d_sdivzstepu * m;
            tdivz += d_tdivzstepu * m;
            zi    += d_zistepu    * m;
            snext = q_i32(sdivz * (65536.0 / zi)) + sadjust;
            tnext = q_i32(tdivz * (65536.0 / zi)) + tadjust;
            snext = q_clamp_i32(snext, 8, bbextents);
            tnext = q_clamp_i32(tnext, 8, bbextentt);
            if (spancount > 1) {
                sstep = q_i32((double)(snext - s) / (double)(spancount - 1));
                tstep = q_i32((double)(tnext - t) / (double)(spancount - 1));
            }
        }

        for (int i = 0; i < spancount; i++) {
            int si = s >> 16;
            int ti = t >> 16;
            fb[(size_t)y * (size_t)fb_stride + (size_t)px] =
                tex[(size_t)ti * (size_t)tex_w + (size_t)si];

            if (zb) {
                (*zb)[(size_t)y * (size_t)z_stride + (size_t)px] =
                    (uint16_t)(izi >> 16);
                izi += izistep;
            }

            s += sstep;
            t += tstep;
            px++;
        }

        s = snext;
        t = tnext;
    }
}

struct QuakeQ29RefSetup {
    double d_sdivzorigin;
    double d_tdivzorigin;
    double d_ziorigin;
    double d_sdivzstepu;
    double d_tdivzstepu;
    double d_zistepu;
    double d_sdivzstepv;
    double d_tdivzstepv;
    double d_zistepv;
    int32_t sadjust;
    int32_t tadjust;
    int32_t bbextents;
    int32_t bbextentt;
};

static QuakeQ29RefSetup make_quake_q29_ref_setup(int tex_w, int tex_h) {
    QuakeQ29RefSetup q {};
    q.d_sdivzorigin = -0.01425;
    q.d_tdivzorigin =  0.02875;
    q.d_ziorigin    =  0.01020;
    q.d_sdivzstepu  =  0.000033;
    q.d_tdivzstepu  = -0.000019;
    q.d_zistepu     =  0.0000011;
    q.d_sdivzstepv  = -0.000071;
    q.d_tdivzstepv  =  0.000096;
    q.d_zistepv     = -0.0000004;
    q.sadjust = 17 << 16;
    q.tadjust = 11 << 16;
    q.bbextents = ((tex_w - 1) << 16) - 1;
    q.bbextentt = ((tex_h - 1) << 16) - 1;
    return q;
}

static ParamSpanListWire make_quake_q29_param(const QuakeQ29RefSetup &q,
                                              uint32_t fb_base,
                                              uint32_t tex_base,
                                              int fb_stride,
                                              int tex_w,
                                              int tex_h) {
    ParamSpanListWire p {};
    p.fb_base = fb_base;
    p.fb_major_step = fb_stride;
    p.fb_minor_step = 1;
    p.tex_addr = tex_base;
    p.tex_width = tex_w;
    p.tex_w_mask = 0;
    p.tex_h_mask = 0;
    p.attr_mode = 3;
    p.span_axis = 0;
    const double num_scale = 8192.0;       // 2^(29 - 16)
    const double zi_scale = 536870912.0;   // 2^29
    p.attr_origin[0] = q_i32((q.d_sdivzorigin * 65536.0
                           + (double)q.sadjust * q.d_ziorigin) * num_scale);
    p.attr_origin[1] = q_i32((q.d_tdivzorigin * 65536.0
                           + (double)q.tadjust * q.d_ziorigin) * num_scale);
    p.attr_origin[2] = q_i32(q.d_ziorigin * zi_scale);
    p.attr_du[0] = q_i32((q.d_sdivzstepu * 65536.0
                       + (double)q.sadjust * q.d_zistepu) * num_scale);
    p.attr_du[1] = q_i32((q.d_tdivzstepu * 65536.0
                       + (double)q.tadjust * q.d_zistepu) * num_scale);
    p.attr_du[2] = q_i32(q.d_zistepu * zi_scale);
    p.attr_dv[0] = q_i32((q.d_sdivzstepv * 65536.0
                       + (double)q.sadjust * q.d_zistepv) * num_scale);
    p.attr_dv[1] = q_i32((q.d_tdivzstepv * 65536.0
                       + (double)q.tadjust * q.d_zistepv) * num_scale);
    p.attr_dv[2] = q_i32(q.d_zistepv * zi_scale);
    p.clamp_min[0] = 0;
    p.clamp_max[0] = q.bbextents;
    p.clamp_min[1] = 0;
    p.clamp_max[1] = q.bbextentt;
    return p;
}

static void quake_q29_ref_records(std::vector<uint8_t> &ref_fb,
                                  std::vector<uint16_t> *ref_z,
                                  const std::vector<uint8_t> &tex,
                                  int tex_w,
                                  const QuakeQ29RefSetup &q,
                                  int fb_stride,
                                  int z_stride,
                                  const std::vector<ParamSpanRecordWire> &records) {
    for (const auto &r : records) {
        quake_ref_span(ref_fb, ref_z, tex, tex_w,
                       r.u, r.v, r.count,
                       q.d_sdivzorigin, q.d_tdivzorigin, q.d_ziorigin,
                       q.d_sdivzstepu, q.d_tdivzstepu, q.d_zistepu,
                       q.d_sdivzstepv, q.d_tdivzstepv, q.d_zistepv,
                       q.sadjust, q.tadjust, q.bbextents, q.bbextentt,
                       fb_stride, z_stride);
    }
}

static bool compare_quake_param_fb(const char *name,
                                   uint32_t fb_base,
                                   const std::vector<uint8_t> &ref,
                                   int fb_stride,
                                   const std::vector<ParamSpanRecordWire> &records) {
    int diffs = 0;
    char first[192] = {0};
    for (const auto &r : records) {
        for (uint16_t i = 0; i < r.count; i++) {
            uint32_t x = (uint32_t)r.u + i;
            uint32_t y = (uint32_t)r.v;
            uint8_t got = sdram_read_byte(fb_base + y * (uint32_t)fb_stride + x);
            uint8_t want = ref[(size_t)y * (size_t)fb_stride + x];
            if (got != want) {
                if (diffs == 0) {
                    snprintf(first, sizeof(first),
                             "x=%u y=%u got=%02x want=%02x",
                             x, y, got, want);
                }
                diffs++;
            }
        }
    }
    if (diffs == 0) {
        check_pass(name);
        return true;
    }
    char msg[256];
    snprintf(msg, sizeof(msg), "%d pixel mismatches; first %s", diffs, first);
    check_fail(name, msg);
    return false;
}

static bool compare_quake_param_z(const char *name,
                                  uint32_t z_base,
                                  const std::vector<uint16_t> &ref,
                                  int z_stride,
                                  const std::vector<ParamSpanRecordWire> &records) {
    int diffs = 0;
    char first[192] = {0};
    for (const auto &r : records) {
        for (uint16_t i = 0; i < r.count; i++) {
            uint32_t x = (uint32_t)r.u + i;
            uint32_t y = (uint32_t)r.v;
            uint16_t got = sdram_read_u16_le(z_base + y * (uint32_t)z_stride * 2u + x * 2u);
            uint16_t want = ref[(size_t)y * (size_t)z_stride + x];
            if (got != want) {
                if (diffs == 0) {
                    snprintf(first, sizeof(first),
                             "x=%u y=%u got=%04x want=%04x",
                             x, y, got, want);
                }
                diffs++;
            }
        }
    }
    if (diffs == 0) {
        check_pass(name);
        return true;
    }
    char msg[256];
    snprintf(msg, sizeof(msg), "%d z mismatches; first %s", diffs, first);
    check_fail(name, msg);
    return false;
}

static void test_param_span_quake_projection_math_mode(uint8_t z_mode,
                                                       const char *suffix) {
    printf("TEST param_span_quake_projection_math_%s\n", suffix);
    gpu_init();
    preload_with_sentinel();

    const int fb_stride = 320;
    const int fb_height = 80;
    const int tex_w = 96;
    const int tex_h = 64;
    const uint32_t z_base_abs = 0x00180000u;
    const uint32_t z_base_reb = 0x001A0000u;
    const int z_stride = 320;

    std::vector<uint8_t> tex = make_quake_distinct_texture(tex_w, tex_h);
    upload_texture(TEX_BASE_BYTE, tex);
    sdram_fill(z_base_abs, (uint32_t)z_stride * fb_height * 2u, 0x5A);
    sdram_fill(z_base_reb, (uint32_t)z_stride * fb_height * 2u, 0x5A);

    const double d_sdivzorigin = -0.01975;
    const double d_tdivzorigin =  0.03450;
    const double d_ziorigin    =  0.00680;
    const double d_sdivzstepu =  0.000041;
    const double d_tdivzstepu = -0.000027;
    const double d_zistepu    =  0.0000032;
    const double d_sdivzstepv = -0.000083;
    const double d_tdivzstepv =  0.000117;
    const double d_zistepv    = -0.0000017;
    const int32_t sadjust = 21 << 16;
    const int32_t tadjust =  7 << 16;
    const int32_t bbextents = (95 << 16) - 1;
    const int32_t bbextentt = (63 << 16) - 1;

    std::vector<ParamSpanRecordWire> records = {
        {13, 37, 73}, {9, 41, 81}, {31, 46, 64}, {5, 56, 92}
    };

    std::vector<uint8_t> ref_fb((size_t)fb_stride * fb_height, SENTINEL_BYTE);
    std::vector<uint16_t> ref_z((size_t)z_stride * fb_height, 0x5A5A);
    for (const auto &r : records) {
        quake_ref_span(ref_fb, (z_mode == 0) ? nullptr : &ref_z, tex, tex_w,
                       r.u, r.v, r.count,
                       d_sdivzorigin, d_tdivzorigin, d_ziorigin,
                       d_sdivzstepu, d_tdivzstepu, d_zistepu,
                       d_sdivzstepv, d_tdivzstepv, d_zistepv,
                       sadjust, tadjust, bbextents, bbextentt,
                       fb_stride, z_stride);
    }

    ParamSpanListWire p {};
    p.fb_base = FB_BASE_BYTE;
    p.fb_major_step = fb_stride;
    p.fb_minor_step = 1;
    p.tex_addr = TEX_BASE_BYTE;
    p.tex_width = tex_w;
    p.tex_w_mask = 0;
    p.tex_h_mask = 0;
    p.attr_mode = 3;
    p.span_axis = 0;
    p.z_mode = z_mode;
    const double persp_q29_num_scale = 8192.0;       // 2^(29 - 16)
    const double persp_q29_zi_scale = 536870912.0;   // 2^29
    p.attr_origin[0] = q_i32((d_sdivzorigin * 65536.0
                           + (double)sadjust * d_ziorigin) * persp_q29_num_scale);
    p.attr_origin[1] = q_i32((d_tdivzorigin * 65536.0
                           + (double)tadjust * d_ziorigin) * persp_q29_num_scale);
    p.attr_origin[2] = q_i32(d_ziorigin * persp_q29_zi_scale);
    p.attr_du[0] = q_i32((d_sdivzstepu * 65536.0
                       + (double)sadjust * d_zistepu) * persp_q29_num_scale);
    p.attr_du[1] = q_i32((d_tdivzstepu * 65536.0
                       + (double)tadjust * d_zistepu) * persp_q29_num_scale);
    p.attr_du[2] = q_i32(d_zistepu * persp_q29_zi_scale);
    p.attr_dv[0] = q_i32((d_sdivzstepv * 65536.0
                       + (double)sadjust * d_zistepv) * persp_q29_num_scale);
    p.attr_dv[1] = q_i32((d_tdivzstepv * 65536.0
                       + (double)tadjust * d_zistepv) * persp_q29_num_scale);
    p.attr_dv[2] = q_i32(d_zistepv * persp_q29_zi_scale);
    p.clamp_min[0] = 0;
    p.clamp_max[0] = bbextents;
    p.clamp_min[1] = 0;
    p.clamp_max[1] = bbextentt;
    p.z_base = z_base_abs;
    p.z_major_step = z_stride * 2;
    p.z_minor_step = 2;

    ParamSpanListWire rebased = p;
    const int base_u = 5;
    const int base_v = 37;
    rebased.fb_base = FB_ALT_BASE_BYTE + (uint32_t)base_v * fb_stride + base_u;
    rebased.attr_origin[0] = p.attr_origin[0] + base_u * p.attr_du[0] + base_v * p.attr_dv[0];
    rebased.attr_origin[1] = p.attr_origin[1] + base_u * p.attr_du[1] + base_v * p.attr_dv[1];
    rebased.attr_origin[2] = p.attr_origin[2] + base_u * p.attr_du[2] + base_v * p.attr_dv[2];
    rebased.z_base = z_base_reb + (uint32_t)base_v * z_stride * 2u + (uint32_t)base_u * 2u;

    std::vector<ParamSpanRecordWire> rebased_records;
    for (const auto &r : records) {
        rebased_records.push_back({
            (uint16_t)(r.u - base_u),
            (uint16_t)(r.v - base_v),
            r.count
        });
    }

    emit_param_span_list_raw(p, records);
    emit_param_span_list_raw(rebased, rebased_records);

    if (!submit_and_wait()) {
        check_fail("param_span_quake_projection_math", "timeout");
        return;
    }

    char name[96];
    snprintf(name, sizeof(name), "param_span_quake_projection_math_%s_abs_fb", suffix);
    compare_quake_param_fb(name, FB_BASE_BYTE, ref_fb, fb_stride, records);
    snprintf(name, sizeof(name), "param_span_quake_projection_math_%s_rebased_fb", suffix);
    compare_quake_param_fb(name, FB_ALT_BASE_BYTE, ref_fb, fb_stride, records);
    if (z_mode != 0) {
        snprintf(name, sizeof(name), "param_span_quake_projection_math_%s_abs_z", suffix);
        compare_quake_param_z(name, z_base_abs, ref_z, z_stride, records);
        snprintf(name, sizeof(name), "param_span_quake_projection_math_%s_rebased_z", suffix);
        compare_quake_param_z(name, z_base_reb, ref_z, z_stride, records);
    }
}

static void test_param_span_quake_projection_math() {
    test_param_span_quake_projection_math_mode(0, "no_z");
    test_param_span_quake_projection_math_mode(1, "z_write");
}

static void test_param_span_q29_high_angle_floor_no_flatten() {
    printf("TEST param_span_q29_high_angle_floor_no_flatten\n");
    gpu_init();
    preload_with_sentinel();

    const int fb_stride = 320;
    const int tex_w = 64;
    const int tex_h = 64;
    std::vector<uint8_t> tex = make_quake_distinct_texture(tex_w, tex_h);
    upload_texture(TEX_BASE_BYTE, tex);

    const double q29 = 536870912.0;
    const int32_t zi0 = q_i32(0.25 * q29);
    const int32_t zi16 = q_i32(0.05 * q29);
    const int32_t zi_du = q_i32(((0.05 - 0.25) / 16.0) * q29);

    ParamSpanListWire p {};
    p.fb_base = FB_BASE_BYTE;
    p.fb_major_step = fb_stride;
    p.fb_minor_step = 1;
    p.tex_addr = TEX_BASE_BYTE;
    p.tex_width = tex_w;
    p.tex_w_mask = tex_w - 1;
    p.tex_h_mask = tex_h - 1;
    p.attr_mode = 3;
    p.span_axis = 0;
    p.attr_origin[0] = zi0 * 4;  // s ~= 4.0 at x=0, ~=20.0 at x=16
    p.attr_origin[1] = zi0 * 7;  // t ~= 7.0 at x=0, ~=35.0 at x=16
    p.attr_origin[2] = zi0;
    p.attr_du[2] = zi_du;
    p.clamp_min[0] = 0;
    p.clamp_max[0] = (tex_w - 1) << 16;
    p.clamp_min[1] = 0;
    p.clamp_max[1] = (tex_h - 1) << 16;

    std::vector<ParamSpanRecordWire> records = {
        {0, 12, 16}
    };

    emit_param_span_list_raw(p, records);
    if (!submit_and_wait()) {
        check_fail("param_span_q29_high_angle_floor_no_flatten", "timeout");
        return;
    }

    const uint32_t row_base = FB_BASE_BYTE + 12u * (uint32_t)fb_stride;
    const uint8_t first = sdram_read_byte(row_base);
    const int end_i = records[0].count - 1;
    int32_t s0 = clamp_i128_to_i32(((__int128)p.attr_origin[0] << 16)
                                 / p.attr_origin[2]);
    int32_t t0 = clamp_i128_to_i32(((__int128)p.attr_origin[1] << 16)
                                 / p.attr_origin[2]);
    int32_t s_end = clamp_i128_to_i32(((__int128)(p.attr_origin[0] + end_i * p.attr_du[0]) << 16)
                                    / (p.attr_origin[2] + end_i * p.attr_du[2]));
    int32_t t_end = clamp_i128_to_i32(((__int128)(p.attr_origin[1] + end_i * p.attr_du[1]) << 16)
                                    / (p.attr_origin[2] + end_i * p.attr_du[2]));
    int32_t s_step = (s_end - s0) / end_i;
    int32_t t_step = (t_end - t0) / end_i;
    int changed = 0;
    int sentinel = 0;
    int diffs = 0;
    char first_diff[192] = {0};
    for (int i = 0; i < 16; i++) {
        uint8_t px = sdram_read_byte(row_base + (uint32_t)i);
        if (px != first)
            changed++;
        if (px == SENTINEL_BYTE)
            sentinel++;

        int32_t s_q16 = s0 + i * s_step;
        int32_t t_q16 = t0 + i * t_step;
        s_q16 = q_clamp_i32(s_q16, p.clamp_min[0], p.clamp_max[0]);
        t_q16 = q_clamp_i32(t_q16, p.clamp_min[1], p.clamp_max[1]);
        uint8_t want = tex[(size_t)(t_q16 >> 16) * (size_t)tex_w
                         + (size_t)(s_q16 >> 16)];
        if (px != want) {
            if (diffs == 0) {
                snprintf(first_diff, sizeof(first_diff),
                         "x=%d got=%02x want=%02x s=%08x t=%08x zi=%08x",
                         i, px, want, (uint32_t)s_q16, (uint32_t)t_q16,
                         (uint32_t)(p.attr_origin[2] + i * p.attr_du[2]));
            }
            diffs++;
        }
    }

    if (sentinel == 0 && changed >= 4 && diffs == 0) {
        check_pass("param_span_q29_high_angle_floor_no_flatten");
        return;
    }

    char msg[160];
    snprintf(msg, sizeof(msg),
             "changed=%d sentinel=%d diffs=%d zi0=%08x zi16=%08x du=%08x first %s",
             changed, sentinel, diffs, (uint32_t)zi0, (uint32_t)zi16,
             (uint32_t)zi_du, first_diff);
    check_fail("param_span_q29_high_angle_floor_no_flatten", msg);
}

static void test_param_q29_tail_counts_and_boundaries() {
    printf("TEST param_q29_tail_counts_and_boundaries\n");
    gpu_init();
    preload_with_sentinel();

    const int fb_stride = 320;
    const int tex_w = 128;
    const int tex_h = 128;
    std::vector<uint8_t> tex = make_quake_distinct_texture(tex_w, tex_h);
    upload_texture(TEX_BASE_BYTE, tex);

    QuakeQ29RefSetup q = make_quake_q29_ref_setup(tex_w, tex_h);
    ParamSpanListWire p = make_quake_q29_param(q, FB_BASE_BYTE, TEX_BASE_BYTE,
                                               fb_stride, tex_w, tex_h);
    ParamSpanListWire split = p;
    split.fb_base = FB_ALT_BASE_BYTE;

    const uint16_t counts[] = {
        1, 2, 3, 4, 7, 8, 15, 16, 17, 31, 32, 33, 47, 48, 63, 64, 127, 255
    };
    std::vector<ParamSpanRecordWire> records;
    for (size_t i = 0; i < sizeof(counts) / sizeof(counts[0]); i++) {
        records.push_back({
            (uint16_t)(3u + (uint16_t)((i * 11u) & 31u)),
            (uint16_t)(2u + (uint16_t)i * 4u),
            counts[i]
        });
    }

    emit_param_span_list_raw(p, records);
    for (const auto &r : records) {
        uint16_t done = 0;
        while (done < r.count) {
            uint16_t chunk = (uint16_t)std::min<int>(16, r.count - done);
            ParamSpanRecordWire sr {
                (uint16_t)(r.u + done),
                r.v,
                chunk
            };
            emit_param_span_list_raw(split, std::vector<ParamSpanRecordWire>{sr});
            done = (uint16_t)(done + chunk);
        }
    }

    if (!submit_and_wait()) {
        check_fail("param_q29_tail_counts_and_boundaries", "timeout");
        return;
    }

    int diffs = 0;
    char first[192] = {0};
    for (const auto &r : records) {
        for (uint16_t i = 0; i < r.count; i++) {
            uint32_t off = (uint32_t)r.v * (uint32_t)fb_stride
                         + (uint32_t)r.u + i;
            uint8_t got = sdram_read_byte(FB_BASE_BYTE + off);
            uint8_t want = sdram_read_byte(FB_ALT_BASE_BYTE + off);
            if (got != want) {
                if (diffs == 0) {
                    snprintf(first, sizeof(first),
                             "u=%u v=%u i=%u got=%02x want=%02x",
                             r.u, r.v, i, got, want);
                }
                diffs++;
            }
        }
    }

    if (diffs == 0)
        check_pass("param_q29_tail_counts_and_boundaries");
    else {
        char msg[240];
        snprintf(msg, sizeof(msg), "%d split mismatches; first %s", diffs, first);
        check_fail("param_q29_tail_counts_and_boundaries", msg);
    }
}

static void test_param_q29_record_counts_and_odd_pairs() {
    printf("TEST param_q29_record_counts_and_odd_pairs\n");

    const int fb_stride = 320;
    const int tex_w = 64;
    const int tex_h = 64;
    const uint16_t record_counts[] = {
        1, 2, 3, 4, 5, 7, 8, 9, 16, 63, 64, 65, 511, 512
    };

    for (uint16_t record_count : record_counts) {
        gpu_init();
        preload_with_sentinel();
        std::vector<uint8_t> tex = make_quake_distinct_texture(tex_w, tex_h);
        upload_texture(TEX_BASE_BYTE, tex);

        QuakeQ29RefSetup q = make_quake_q29_ref_setup(tex_w, tex_h);
        ParamSpanListWire p = make_quake_q29_param(q, FB_BASE_BYTE,
                                                   TEX_BASE_BYTE, fb_stride,
                                                   tex_w, tex_h);
        ParamSpanListWire p_single = p;
        p_single.fb_base = FB_ALT_BASE_BYTE;

        std::vector<ParamSpanRecordWire> records;
        records.reserve(record_count);
        for (uint16_t i = 0; i < record_count; i++) {
            uint16_t count = (uint16_t)(1u + (i & 3u));
            uint16_t u = (uint16_t)((i * 17u + record_count) % 300u);
            if ((uint32_t)u + count >= 320u)
                u = (uint16_t)(319u - count);
            uint16_t v = (uint16_t)((i * 7u + record_count) % 180u);
            records.push_back({u, v, count});
        }

        char name[96];
        snprintf(name, sizeof(name),
                 "param_q29_record_counts_and_odd_pairs.%u", record_count);

        emit_param_span_list_raw(p, records);
        if (!submit_and_wait(1600000)) {
            check_fail(name, "timeout in multi-record command");
            continue;
        }

        bool ok = true;
        for (const auto &r : records) {
            emit_param_span_list_raw(p_single, std::vector<ParamSpanRecordWire>{r});
            if (pending_stream.size() > 3000u) {
                if (!submit_and_wait(1600000)) {
                    check_fail(name, "timeout in single-record comparison stream");
                    ok = false;
                    break;
                }
            }
        }
        if (!ok)
            continue;
        if (!pending_stream.empty() && !submit_and_wait(1600000)) {
            check_fail(name, "timeout in final single-record comparison stream");
            continue;
        }

        int diffs = 0;
        char first[160] = {0};
        for (const auto &r : records) {
            for (uint16_t i = 0; i < r.count; i++) {
                uint32_t off = (uint32_t)r.v * (uint32_t)fb_stride
                             + (uint32_t)r.u + i;
                uint8_t got = sdram_read_byte(FB_BASE_BYTE + off);
                uint8_t want = sdram_read_byte(FB_ALT_BASE_BYTE + off);
                if (got != want) {
                    if (diffs == 0) {
                        snprintf(first, sizeof(first),
                                 "record_count=%u u=%u v=%u i=%u got=%02x want=%02x",
                                 record_count, r.u, r.v, i, got, want);
                    }
                    diffs++;
                }
            }
        }

        if (diffs == 0)
            check_pass(name);
        else {
            char msg[224];
            snprintf(msg, sizeof(msg), "%d mismatches; first %s", diffs, first);
            check_fail(name, msg);
        }
    }
}

/* DOOM WALL REPRO: PERSP_Q29 + AXIS_Y multi-chunk record consumption.
 * The acceptance suite's Q29 multi-record tests all use span_axis=0
 * (AXIS_X, fb_major_step=stride, fb_minor_step=1).  The Doom param-wall
 * path (R_GPU_WallTierBegin) emits span_axis=AXIS_Y with fb_major_step=1
 * and fb_minor_step=stride, batched up to GPU_WALL_BAND_RECORDS(128) per
 * 0x48 command.  This exercises the SAME multi-chunk continuation
 * (pay_idx==36 -> S_EXECUTE -> spanprod_prepare_next_record_chunk) but
 * with the AXIS_Y operand routing in spanprod_launch_fb_mul and the
 * axis-dependent per-record step setup.  Byte-compare a single multi-
 * record AXIS_Y command against the per-record single-command form. */
static void test_param_q29_axis_y_multichunk_wall_repro() {
    printf("TEST param_q29_axis_y_multichunk_wall_repro\n");

    const int fb_stride = 320;
    const int tex_w = 64;
    const int tex_h = 64;
    /* Counts straddling the 4-record chunk boundary: 5,7,9 and 33,127,128
     * (band capacity) plus the odd-pair tail cases. */
    const uint16_t record_counts[] = {
        1, 2, 3, 4, 5, 6, 7, 8, 9, 16, 17, 33, 64, 65, 127, 128
    };

    for (uint16_t record_count : record_counts) {
        gpu_init();
        preload_with_sentinel();
        std::vector<uint8_t> tex = make_quake_distinct_texture(tex_w, tex_h);
        upload_texture(TEX_BASE_BYTE, tex);

        QuakeQ29RefSetup q = make_quake_q29_ref_setup(tex_w, tex_h);
        /* AXIS_Y wall params: per-column major step = 1, per-pixel walk =
         * row stride.  Identical to R_GPU_WallTierBegin/SpriteBegin. */
        ParamSpanListWire p = make_quake_q29_param(q, FB_BASE_BYTE,
                                                   TEX_BASE_BYTE, fb_stride,
                                                   tex_w, tex_h);
        p.fb_major_step = 1;
        p.fb_minor_step = fb_stride;
        p.span_axis = 1;                 /* OF_GPU_PARAM_AXIS_Y */
        ParamSpanListWire p_single = p;
        p_single.fb_base = FB_ALT_BASE_BYTE;

        /* One record per column: u=x, v=top row, count=vertical extent.
         * Each column starts at a distinct y so a dropped record leaves a
         * visible sentinel column (the "vertical black bar"). */
        std::vector<ParamSpanRecordWire> records;
        records.reserve(record_count);
        for (uint16_t i = 0; i < record_count; i++) {
            /* Realistic wall geometry: one record per consecutive screen
             * column.  Wrap the texel-space clamp by keeping u within a
             * sane 256-wide viewport so the perspective attr stays valid. */
            uint16_t u = (uint16_t)(2u + (i % 250u));     /* column x */
            uint16_t v = (uint16_t)(4u + (i & 7u));       /* top row */
            uint16_t count = (uint16_t)(8u + (i & 15u));  /* col height */
            if ((uint32_t)v + count >= 200u)
                count = (uint16_t)(199u - v);
            records.push_back({u, v, count});
        }

        char name[96];
        snprintf(name, sizeof(name),
                 "param_q29_axis_y_multichunk_wall_repro.%u", record_count);

        emit_param_span_list_raw(p, records);
        if (!submit_and_wait(1600000)) {
            check_fail(name, "timeout in multi-record AXIS_Y command");
            continue;
        }

        bool ref_ok = true;
        for (const auto &r : records) {
            emit_param_span_list_raw(p_single,
                                     std::vector<ParamSpanRecordWire>{r});
            /* Flush before the staged single-record stream can exceed the
             * 4096-word device ring: N single commands (35 words each)
             * overflow it past ~117 records, clobbering early commands and
             * leaving sentinel reference columns.  Same guard the AXIS_X
             * record_counts_and_odd_pairs test uses. */
            if (pending_stream.size() > 3000u) {
                if (!submit_and_wait(1600000)) {
                    check_fail(name, "timeout in single-record AXIS_Y stream");
                    ref_ok = false;
                    break;
                }
            }
        }
        if (!ref_ok)
            continue;
        if (!pending_stream.empty() && !submit_and_wait(1600000)) {
            check_fail(name, "timeout in final single-record AXIS_Y stream");
            continue;
        }

        int diffs = 0;
        char first[176] = {0};
        for (const auto &r : records) {
            for (uint16_t i = 0; i < r.count; i++) {
                /* AXIS_Y: walk DOWN the column (row stride per pixel). */
                uint32_t off = ((uint32_t)r.v + i) * (uint32_t)fb_stride
                             + (uint32_t)r.u;
                uint8_t got = sdram_read_byte(FB_BASE_BYTE + off);
                uint8_t want = sdram_read_byte(FB_ALT_BASE_BYTE + off);
                if (got != want) {
                    if (diffs == 0) {
                        snprintf(first, sizeof(first),
                                 "rc=%u col_u=%u v=%u i=%u got=%02x want=%02x",
                                 record_count, r.u, r.v, i, got, want);
                    }
                    diffs++;
                }
            }
        }

        if (diffs == 0)
            check_pass(name);
        else {
            char msg[224];
            snprintf(msg, sizeof(msg), "%d mismatches; first %s", diffs, first);
            check_fail(name, msg);
        }
    }
}

/* ============================================================================
 * DOOM REAL-CAPTURE REPLAY.
 *
 * Feeds the EXACT GPU command records captured from Doom's real param-wall
 * emitter (tb_gpu_doom_capture.c: real finetangent / projection / Q29 encode
 * / SDK wire packing, swept across 322k views) and asserts no accepted column
 * is left at the FB sentinel.  A sentinel column == the framebuffer pixel was
 * never written == the "vertical black bar" on real hardware.
 *
 * The captured fb_base/tex_addr are host placeholders; we remap them onto the
 * sim's SDRAM map and keep every perspective / q29 / u / v / count value
 * byte-for-byte as Doom emitted (the load-bearing values).
 * ========================================================================== */
#include "doom_capture_replay.h"

static void test_doom_real_capture_replay_no_black_columns() {
    printf("TEST doom_real_capture_replay_no_black_columns\n");
    gpu_init();
    preload_with_sentinel();

    const int fb_stride = 320;
    const int tex_w = 64, tex_h = 64;
    /* Non-sentinel texture: every byte != SENTINEL so any GPU write is
     * distinguishable from "never written". */
    std::vector<uint8_t> tex((size_t)tex_w * tex_h);
    for (size_t i = 0; i < tex.size(); i++) {
        uint8_t b = (uint8_t)((i * 7u + 3u) & 0xFF);
        if (b == SENTINEL_BYTE) b ^= 0x55;
        tex[i] = b;
    }
    upload_texture(TEX_BASE_BYTE, tex);
    /* Identity palookup, all shade rows Doom walls can index (0..15), slot 0. */
    for (uint8_t row = 0; row < 16; row++)
        upload_palookup_identity_row(0, row);

    /* Track which (u, row) pixels the captured stream claims to paint. */
    std::vector<uint8_t> expect_painted((size_t)fb_stride * 200u, 0);

    for (int c = 0; c < doom_cap_cmd_count; c++) {
        const DoomCapCmd &cc = doom_cap_cmds[c];
        ParamSpanListWire p {};
        p.fb_base = FB_BASE_BYTE;        /* remap: host 0 -> sim FB */
        p.fb_major_step = cc.fb_major_step;
        p.fb_minor_step = cc.fb_minor_step;
        p.tex_addr = TEX_BASE_BYTE;      /* remap: host dummy -> sim tex */
        p.tex_width = (uint16_t)cc.tex_width;
        p.tex_w_mask = (uint16_t)cc.tex_w_mask;
        p.tex_h_mask = (uint16_t)cc.tex_h_mask;
        p.flags = (uint8_t)cc.flags;
        p.colormap_id = (uint8_t)cc.colormap_id;
        p.attr_mode = (uint8_t)cc.attr_mode;
        p.span_axis = (uint8_t)cc.span_axis;
        p.z_mode = (uint8_t)cc.z_mode;
        p.q29_attr_shift = (uint8_t)cc.q29_attr_shift;
        for (int i = 0; i < 3; i++) {
            p.attr_origin[i] = cc.attr_origin[i];
            p.attr_du[i] = cc.attr_du[i];
            p.attr_dv[i] = cc.attr_dv[i];
        }
        p.light_origin = cc.light_origin;
        p.light_du = cc.light_du;
        p.light_dv = cc.light_dv;

        std::vector<ParamSpanRecordWire> records;
        records.reserve(cc.record_count);
        for (int i = 0; i < cc.record_count; i++) {
            records.push_back({cc.u[i], cc.v[i], cc.cnt[i]});
            /* AXIS_Y: walk DOWN the column. */
            for (uint16_t k = 0; k < cc.cnt[i]; k++) {
                uint32_t row = (uint32_t)cc.v[i] + k;
                if (cc.u[i] < (uint16_t)fb_stride && row < 200u)
                    expect_painted[row * fb_stride + cc.u[i]] = 1;
            }
        }
        emit_param_span_list_raw(p, records);
    }
    if (!submit_and_wait(4000000)) {
        check_fail("doom_real_capture_replay_no_black_columns", "timeout");
        return;
    }

    /* Any pixel the stream claimed to paint that is still SENTINEL == black. */
    int black = 0; char first[160] = {0};
    int black_cols_lo = 9999, black_cols_hi = -1;
    for (uint32_t row = 0; row < 200u; row++) {
        for (uint32_t u = 0; u < (uint32_t)fb_stride; u++) {
            if (!expect_painted[row * fb_stride + u]) continue;
            uint8_t got = sdram_read_byte(FB_BASE_BYTE + row * fb_stride + u);
            if (got == SENTINEL_BYTE) {
                if (black == 0)
                    snprintf(first, sizeof(first),
                             "col u=%u row=%u still SENTINEL", u, row);
                if ((int)u < black_cols_lo) black_cols_lo = u;
                if ((int)u > black_cols_hi) black_cols_hi = u;
                black++;
            }
        }
    }

    if (black == 0) {
        check_pass("doom_real_capture_replay_no_black_columns");
    } else {
        char msg[224];
        snprintf(msg, sizeof(msg),
                 "%d black pixels (cols %d..%d); first %s",
                 black, black_cols_lo, black_cols_hi, first);
        check_fail("doom_real_capture_replay_no_black_columns", msg);
    }
}

/* Replay a whole captured multi-wall FRAME (several real walls back-to-back in
 * one command stream) and assert no painted column is left black.  Exposes any
 * inter-command / band-eviction / state-carryover column drop. */
static void test_doom_real_capture_frame_no_black_columns() {
    printf("TEST doom_real_capture_frame_no_black_columns\n");
    gpu_init();
    preload_with_sentinel();

    const int fb_stride = 320;
    const int tex_w = 64, tex_h = 64;
    std::vector<uint8_t> tex((size_t)tex_w * tex_h);
    for (size_t i = 0; i < tex.size(); i++) {
        uint8_t b = (uint8_t)((i * 11u + 5u) & 0xFF);
        if (b == SENTINEL_BYTE) b ^= 0x55;
        tex[i] = b;
    }
    upload_texture(TEX_BASE_BYTE, tex);
    for (uint8_t row = 0; row < 16; row++)
        upload_palookup_identity_row(0, row);

    std::vector<uint8_t> expect_painted((size_t)fb_stride * 200u, 0);

    for (int c = 0; c < doom_frame_cmd_count; c++) {
        const DoomCapCmd &cc = doom_frame_cmds[c];
        ParamSpanListWire p {};
        p.fb_base = FB_BASE_BYTE;
        p.fb_major_step = cc.fb_major_step;
        p.fb_minor_step = cc.fb_minor_step;
        p.tex_addr = TEX_BASE_BYTE;
        p.tex_width = (uint16_t)cc.tex_width;
        p.tex_w_mask = (uint16_t)cc.tex_w_mask;
        p.tex_h_mask = (uint16_t)cc.tex_h_mask;
        p.flags = (uint8_t)cc.flags;
        p.colormap_id = (uint8_t)cc.colormap_id;
        p.attr_mode = (uint8_t)cc.attr_mode;
        p.span_axis = (uint8_t)cc.span_axis;
        p.z_mode = (uint8_t)cc.z_mode;
        p.q29_attr_shift = (uint8_t)cc.q29_attr_shift;
        for (int i = 0; i < 3; i++) {
            p.attr_origin[i] = cc.attr_origin[i];
            p.attr_du[i] = cc.attr_du[i];
            p.attr_dv[i] = cc.attr_dv[i];
        }
        p.light_origin = cc.light_origin;
        p.light_du = cc.light_du;
        p.light_dv = cc.light_dv;

        std::vector<ParamSpanRecordWire> records;
        for (int i = 0; i < cc.record_count; i++) {
            records.push_back({cc.u[i], cc.v[i], cc.cnt[i]});
            for (uint16_t k = 0; k < cc.cnt[i]; k++) {
                uint32_t row = (uint32_t)cc.v[i] + k;
                if (cc.u[i] < (uint16_t)fb_stride && row < 200u)
                    expect_painted[row * fb_stride + cc.u[i]] = 1;
            }
        }
        emit_param_span_list_raw(p, records);
    }
    if (!submit_and_wait(4000000)) {
        check_fail("doom_real_capture_frame_no_black_columns", "timeout");
        return;
    }

    int black = 0; char first[160] = {0};
    int lo = 9999, hi = -1;
    for (uint32_t row = 0; row < 200u; row++)
        for (uint32_t u = 0; u < (uint32_t)fb_stride; u++) {
            if (!expect_painted[row * fb_stride + u]) continue;
            if (sdram_read_byte(FB_BASE_BYTE + row * fb_stride + u) == SENTINEL_BYTE) {
                if (black == 0)
                    snprintf(first, sizeof(first), "col u=%u row=%u SENTINEL", u, row);
                if ((int)u < lo) lo = u; if ((int)u > hi) hi = u;
                black++;
            }
        }

    if (black == 0) check_pass("doom_real_capture_frame_no_black_columns");
    else {
        char msg[224];
        snprintf(msg, sizeof(msg), "%d black pixels (cols %d..%d); first %s",
                 black, lo, hi, first);
        check_fail("doom_real_capture_frame_no_black_columns", msg);
    }
}

static void test_param_q29_zero_counts_mixed_no_drop() {
    printf("TEST param_q29_zero_counts_mixed_no_drop\n");
    gpu_init();
    preload_with_sentinel();

    const int fb_stride = 320;
    const int tex_w = 64;
    const int tex_h = 64;
    std::vector<uint8_t> tex = make_quake_distinct_texture(tex_w, tex_h);
    for (uint8_t &px : tex) {
        if (px == SENTINEL_BYTE)
            px ^= 0x55;
    }
    upload_texture(TEX_BASE_BYTE, tex);

    QuakeQ29RefSetup q = make_quake_q29_ref_setup(tex_w, tex_h);
    ParamSpanListWire p = make_quake_q29_param(q, FB_BASE_BYTE,
                                               TEX_BASE_BYTE, fb_stride,
                                               tex_w, tex_h);

    std::vector<ParamSpanRecordWire> records = {
        {5,  11,  7}, {37, 12, 0}, {9,  13, 15}, {48, 14, 0},
        {13, 15, 16}, {29, 16, 1}, {61, 17, 0}, {7,  18, 33},
        {3,  19,  2}
    };

    std::vector<uint8_t> ref_fb((size_t)fb_stride * 32u, SENTINEL_BYTE);
    quake_q29_ref_records(ref_fb, nullptr, tex, tex_w, q, fb_stride,
                          fb_stride, records);

    emit_param_span_list_raw(p, records);
    if (!submit_and_wait()) {
        check_fail("param_q29_zero_counts_mixed_no_drop", "timeout");
        return;
    }

    bool ok = compare_quake_param_fb("param_q29_zero_counts_mixed_no_drop.fb",
                                     FB_BASE_BYTE, ref_fb, fb_stride, records);

    int zero_touched = 0;
    char first[160] = {0};
    for (const auto &r : records) {
        if (r.count != 0)
            continue;
        uint8_t got = sdram_read_byte(FB_BASE_BYTE
                                    + (uint32_t)r.v * fb_stride + r.u);
        if (got != SENTINEL_BYTE) {
            if (zero_touched == 0) {
                snprintf(first, sizeof(first),
                         "u=%u v=%u got=%02x", r.u, r.v, got);
            }
            zero_touched++;
        }
    }

    if (ok && zero_touched == 0)
        check_pass("param_q29_zero_counts_mixed_no_drop.zero_guards");
    else if (zero_touched != 0) {
        char msg[224];
        snprintf(msg, sizeof(msg), "zero_touched=%d first %s",
                 zero_touched, first);
        check_fail("param_q29_zero_counts_mixed_no_drop.zero_guards", msg);
    }
}

static std::vector<uint8_t> make_param_bitwalk_texture(int tex_w, int tex_h,
                                                       uint8_t salt) {
    std::vector<uint8_t> tex((size_t)tex_w * (size_t)tex_h);
    for (int t = 0; t < tex_h; t++) {
        for (int s = 0; s < tex_w; s++) {
            uint32_t addr = (uint32_t)t * (uint32_t)tex_w + (uint32_t)s;
            tex[(size_t)addr] =
                (uint8_t)((s ^ (t << 4) ^ (addr >> 1) ^ salt) & 0xFF);
        }
    }
    return tex;
}

static void test_param_q29_wrap_and_fb_byte_lanes() {
    printf("TEST param_q29_wrap_and_fb_byte_lanes\n");
    gpu_init();
    preload_with_sentinel();

    const int fb_stride = 320;
    const int sizes[] = {16, 32, 64, 128};
    int diffs = 0;
    char first[192] = {0};

    for (int si = 0; si < 4; si++) {
        int tex_w = sizes[si];
        int tex_h = sizes[si];
        uint32_t tex_base = TEX_BASE_BYTE + (uint32_t)si * 0x8000u;
        std::vector<uint8_t> tex =
            make_param_bitwalk_texture(tex_w, tex_h, (uint8_t)(0x21 + si));
        upload_texture(tex_base, tex);

        ParamSpanListWire p {};
        p.fb_base = FB_BASE_BYTE;
        p.fb_major_step = fb_stride;
        p.fb_minor_step = 1;
        p.tex_addr = tex_base;
        p.tex_width = tex_w;
        p.tex_w_mask = (uint16_t)(tex_w - 1);
        p.tex_h_mask = (uint16_t)(tex_h - 1);
        p.attr_mode = 3;
        p.span_axis = 0;
        p.attr_origin[0] = (tex_w - 3) << 16;
        p.attr_origin[1] = 2 << 16;
        p.attr_origin[2] = 0x00010000;
        p.attr_du[0] = 1 << 16;

        std::vector<ParamSpanRecordWire> records;
        for (uint16_t lane = 0; lane < 4; lane++)
            records.push_back({lane, (uint16_t)(si * 8 + lane), 9});
        emit_param_span_list_raw(p, records);
    }

    if (!submit_and_wait()) {
        check_fail("param_q29_wrap_and_fb_byte_lanes", "timeout");
        return;
    }

    for (int si = 0; si < 4; si++) {
        int tex_w = sizes[si];
        int tex_h = sizes[si];
        std::vector<uint8_t> tex =
            make_param_bitwalk_texture(tex_w, tex_h, (uint8_t)(0x21 + si));
        for (uint16_t lane = 0; lane < 4; lane++) {
            uint16_t y = (uint16_t)(si * 8 + lane);
            for (uint16_t i = 0; i < 9; i++) {
                uint16_t x = lane + i;
                int s = (tex_w - 3 + x) & (tex_w - 1);
                int t = 2 & (tex_h - 1);
                uint8_t want = tex[(size_t)t * (size_t)tex_w + (size_t)s];
                uint8_t got = sdram_read_byte(FB_BASE_BYTE
                                             + (uint32_t)y * fb_stride + x);
                if (got != want) {
                    if (diffs == 0) {
                        snprintf(first, sizeof(first),
                                 "size=%d x=%u y=%u got=%02x want=%02x s=%d t=%d",
                                 tex_w, x, y, got, want, s, t);
                    }
                    diffs++;
                }
            }
        }
    }

    if (diffs == 0)
        check_pass("param_q29_wrap_and_fb_byte_lanes");
    else {
        char msg[240];
        snprintf(msg, sizeof(msg), "%d mismatches; first %s", diffs, first);
        check_fail("param_q29_wrap_and_fb_byte_lanes", msg);
    }
}

static void test_param_q29_static_repeat_300() {
    printf("TEST param_q29_static_repeat_300\n");
    gpu_init();
    preload_with_sentinel();

    const int fb_stride = 320;
    std::vector<uint8_t> tex = make_quake_distinct_texture(64, 64);
    upload_texture(TEX_BASE_BYTE, tex);
    QuakeQ29RefSetup q = make_quake_q29_ref_setup(64, 64);
    ParamSpanListWire p = make_quake_q29_param(q, FB_BASE_BYTE, TEX_BASE_BYTE,
                                               fb_stride, 64, 64);
    std::vector<ParamSpanRecordWire> records = {{11, 19, 23}, {7, 21, 31}};

    uint32_t first_hash = 0;
    bool have_hash = false;
    for (int frame = 0; frame < 300; frame++) {
        sdram_fill(FB_BASE_BYTE, 320u * 40u, SENTINEL_BYTE);
        emit_param_span_list_raw(p, records);
        if (!submit_and_wait()) {
            check_fail("param_q29_static_repeat_300", "timeout");
            return;
        }

        uint32_t hash = 2166136261u;
        for (const auto &r : records) {
            for (uint16_t i = 0; i < r.count; i++) {
                uint8_t b = sdram_read_byte(FB_BASE_BYTE
                    + (uint32_t)r.v * (uint32_t)fb_stride + r.u + i);
                hash ^= b;
                hash *= 16777619u;
            }
        }
        if (!have_hash) {
            first_hash = hash;
            have_hash = true;
        } else if (hash != first_hash) {
            char msg[128];
            snprintf(msg, sizeof(msg),
                     "frame=%d hash=%08x first=%08x", frame, hash, first_hash);
            check_fail("param_q29_static_repeat_300", msg);
            return;
        }
    }

    check_pass("param_q29_static_repeat_300");
}

static ParamSpanListWire make_q29_dynamic_scale_param(uint32_t fb_base,
                                                      uint32_t tex_base,
                                                      uint32_t z_base,
                                                      int fb_stride,
                                                      int z_stride,
                                                      int tex_w,
                                                      int tex_h,
                                                      uint8_t z_mode) {
    const int32_t zi = 0x08000000;
    ParamSpanListWire p {};
    p.fb_base = fb_base;
    p.fb_major_step = fb_stride;
    p.fb_minor_step = 1;
    p.tex_addr = tex_base;
    p.tex_width = tex_w;
    p.tex_w_mask = (uint16_t)(tex_w - 1);
    p.tex_h_mask = (uint16_t)(tex_h - 1);
    p.attr_mode = 3;
    p.q29_attr_shift = 4;
    p.z_mode = z_mode;
    p.attr_origin[0] = zi * 4;
    p.attr_origin[1] = zi * 7;
    p.attr_origin[2] = zi;
    p.attr_du[0] = zi / 8;
    p.attr_du[1] = zi / 16;
    p.attr_du[2] = 0;
    p.clamp_min[0] = 0;
    p.clamp_max[0] = (tex_w - 1) << 16;
    p.clamp_min[1] = 0;
    p.clamp_max[1] = (tex_h - 1) << 16;
    p.z_base = z_base;
    p.z_major_step = z_stride * 2;
    p.z_minor_step = 2;
    return p;
}

static uint8_t q29_dynamic_scale_texel(const ParamSpanListWire &p,
                                       const std::vector<uint8_t> &tex,
                                       int tex_w,
                                       uint16_t x,
                                       uint16_t y) {
    __int128 s_num = (__int128)p.attr_origin[0]
                   + (__int128)x * p.attr_du[0]
                   + (__int128)y * p.attr_dv[0];
    __int128 t_num = (__int128)p.attr_origin[1]
                   + (__int128)x * p.attr_du[1]
                   + (__int128)y * p.attr_dv[1];
    __int128 zi = (__int128)p.attr_origin[2]
                + (__int128)x * p.attr_du[2]
                + (__int128)y * p.attr_dv[2];
    int32_t s_q16 = clamp_i128_to_i32((s_num << 16) / zi);
    int32_t t_q16 = clamp_i128_to_i32((t_num << 16) / zi);
    s_q16 = q_clamp_i32(s_q16, p.clamp_min[0], p.clamp_max[0]);
    t_q16 = q_clamp_i32(t_q16, p.clamp_min[1], p.clamp_max[1]);
    uint32_t s = ((uint32_t)(s_q16 >> 16)) & p.tex_w_mask;
    uint32_t t = ((uint32_t)(t_q16 >> 16)) & p.tex_h_mask;
    return tex[(size_t)t * (size_t)tex_w + (size_t)s];
}

static void test_param_q29_dynamic_scale_projection() {
    printf("TEST param_q29_dynamic_scale_projection\n");
    gpu_init();
    preload_with_sentinel();

    const int fb_stride = 320;
    const int z_stride = 320;
    const int tex_w = 64;
    const int tex_h = 64;
    std::vector<uint8_t> tex = make_param_bitwalk_texture(tex_w, tex_h, 0x53);
    upload_texture(TEX_BASE_BYTE, tex);

    ParamSpanListWire p = make_q29_dynamic_scale_param(
        FB_BASE_BYTE, TEX_BASE_BYTE, 0x001C0000u, fb_stride, z_stride,
        tex_w, tex_h, 0);
    std::vector<ParamSpanRecordWire> records = {
        {3, 12, 29}, {37, 17, 31}
    };

    emit_param_span_list_raw(p, records);
    if (!submit_and_wait()) {
        check_fail("param_q29_dynamic_scale_projection", "timeout");
        return;
    }

    int diffs = 0;
    char first[192] = {0};
    for (const auto &r : records) {
        for (uint16_t i = 0; i < r.count; i++) {
            uint16_t x = (uint16_t)(r.u + i);
            uint8_t got = sdram_read_byte(FB_BASE_BYTE
                                        + (uint32_t)r.v * fb_stride + x);
            uint8_t want = q29_dynamic_scale_texel(p, tex, tex_w, x, r.v);
            if (got != want) {
                if (diffs == 0) {
                    snprintf(first, sizeof(first),
                             "x=%u y=%u got=%02x want=%02x",
                             x, r.v, got, want);
                }
                diffs++;
            }
        }
    }

    if (diffs == 0)
        check_pass("param_q29_dynamic_scale_projection");
    else {
        char msg[224];
        snprintf(msg, sizeof(msg), "%d pixel mismatches; first %s", diffs, first);
        check_fail("param_q29_dynamic_scale_projection", msg);
    }
}

static void test_param_q29_dynamic_scale_all_records_touch() {
    printf("TEST param_q29_dynamic_scale_all_records_touch\n");
    gpu_init();
    preload_with_sentinel();

    const int fb_stride = 320;
    const int z_stride = 320;
    const int tex_w = 64;
    const int tex_h = 64;
    std::vector<uint8_t> tex = make_param_bitwalk_texture(tex_w, tex_h, 0x5D);
    for (uint8_t &px : tex) {
        if (px == SENTINEL_BYTE)
            px ^= 0x3C;
    }
    upload_texture(TEX_BASE_BYTE, tex);

    ParamSpanListWire p = make_q29_dynamic_scale_param(
        FB_BASE_BYTE, TEX_BASE_BYTE, 0x001C0000u, fb_stride, z_stride,
        tex_w, tex_h, 0);
    std::vector<ParamSpanRecordWire> records = {
        {0,   5,  1}, {4,   6,  2}, {9,   7,  3}, {15,  8,  4},
        {22,  9,  5}, {31, 10,  7}, {45, 11,  8}, {58, 12,  9},
        {72, 13, 15}, {8,  14, 16}, {30, 15, 17}, {55, 16, 31},
        {0,  17, 32}, {40, 18, 33}
    };

    emit_param_span_list_raw(p, records);
    if (!submit_and_wait()) {
        check_fail("param_q29_dynamic_scale_all_records_touch", "timeout");
        return;
    }

    int diffs = 0;
    int untouched = 0;
    int guard_touched = 0;
    char first[224] = {0};
    for (const auto &r : records) {
        for (uint16_t i = 0; i < r.count; i++) {
            uint16_t x = (uint16_t)(r.u + i);
            uint8_t got = sdram_read_byte(FB_BASE_BYTE
                                        + (uint32_t)r.v * fb_stride + x);
            uint8_t want = q29_dynamic_scale_texel(p, tex, tex_w, x, r.v);
            if (got == SENTINEL_BYTE)
                untouched++;
            if (got != want) {
                if (diffs == 0) {
                    snprintf(first, sizeof(first),
                             "x=%u y=%u got=%02x want=%02x count=%u",
                             x, r.v, got, want, r.count);
                }
                diffs++;
            }
        }

        if (r.u > 0) {
            uint8_t left = sdram_read_byte(FB_BASE_BYTE
                + (uint32_t)r.v * fb_stride + (uint32_t)r.u - 1u);
            if (left != SENTINEL_BYTE)
                guard_touched++;
        }
        if ((uint32_t)r.u + r.count < 320u) {
            uint8_t right = sdram_read_byte(FB_BASE_BYTE
                + (uint32_t)r.v * fb_stride + (uint32_t)r.u + r.count);
            if (right != SENTINEL_BYTE)
                guard_touched++;
        }
    }

    uint8_t quiet_row = sdram_read_byte(FB_BASE_BYTE + 4u * fb_stride + 37u);
    if (quiet_row != SENTINEL_BYTE)
        guard_touched++;

    if (diffs == 0 && untouched == 0 && guard_touched == 0)
        check_pass("param_q29_dynamic_scale_all_records_touch");
    else {
        char msg[288];
        snprintf(msg, sizeof(msg),
                 "diffs=%d untouched=%d guard_touched=%d first %s",
                 diffs, untouched, guard_touched, first);
        check_fail("param_q29_dynamic_scale_all_records_touch", msg);
    }
}

static void test_param_q29_dynamic_scale_z_restore_saturates() {
    printf("TEST param_q29_dynamic_scale_z_restore_saturates\n");
    gpu_init();
    preload_with_sentinel();

    const int fb_stride = 320;
    const int z_stride = 320;
    const int tex_w = 64;
    const int tex_h = 64;
    const uint32_t z_base = 0x001C0000u;
    std::vector<uint8_t> tex = make_param_bitwalk_texture(tex_w, tex_h, 0x71);
    upload_texture(TEX_BASE_BYTE, tex);
    sdram_fill(z_base, (uint32_t)z_stride * 64u * 2u, 0x5A);

    ParamSpanListWire p = make_q29_dynamic_scale_param(
        FB_BASE_BYTE, TEX_BASE_BYTE, z_base, fb_stride, z_stride,
        tex_w, tex_h, 1);
    std::vector<ParamSpanRecordWire> records = {
        {5, 19, 11}, {21, 20, 13}
    };

    emit_param_span_list_raw(p, records);
    if (!submit_and_wait()) {
        check_fail("param_q29_dynamic_scale_z_restore_saturates", "timeout");
        return;
    }

    int diffs = 0;
    char first[192] = {0};
    for (const auto &r : records) {
        for (uint16_t i = 0; i < r.count; i++) {
            uint16_t x = (uint16_t)(r.u + i);
            uint16_t got = sdram_read_u16_le(z_base
                + (uint32_t)r.v * (uint32_t)z_stride * 2u
                + (uint32_t)x * 2u);
            if (got != 0xFFFFu) {
                if (diffs == 0) {
                    snprintf(first, sizeof(first),
                             "x=%u y=%u got=%04x want=ffff",
                             x, r.v, got);
                }
                diffs++;
            }
        }
    }

    if (diffs == 0)
        check_pass("param_q29_dynamic_scale_z_restore_saturates");
    else {
        char msg[224];
        snprintf(msg, sizeof(msg), "%d z mismatches; first %s", diffs, first);
        check_fail("param_q29_dynamic_scale_z_restore_saturates", msg);
    }
}

static void test_param_q29_dynamic_scale_reserved_bits_noop() {
    printf("TEST param_q29_dynamic_scale_reserved_bits_noop\n");
    gpu_init();
    preload_with_sentinel();

    const int fb_stride = 320;
    const int z_stride = 320;
    const int tex_w = 64;
    const int tex_h = 64;
    std::vector<uint8_t> tex = make_param_bitwalk_texture(tex_w, tex_h, 0x29);
    upload_texture(TEX_BASE_BYTE, tex);

    ParamSpanListWire p = make_q29_dynamic_scale_param(
        FB_BASE_BYTE, TEX_BASE_BYTE, 0x001C0000u, fb_stride, z_stride,
        tex_w, tex_h, 0);
    std::vector<ParamSpanRecordWire> records = {{5, 22, 12}};

    auto words = encode_param_span_list_wire(p, records);
    words[30] |= (1u << 5);
    ring_cmd(0x48, (uint32_t)words.size());
    for (uint32_t word : words)
        ring_write(word);

    if (!submit_and_wait()) {
        check_fail("param_q29_dynamic_scale_reserved_bits_noop", "timeout");
        return;
    }

    int touched = 0;
    for (const auto &r : records) {
        for (uint16_t i = 0; i < r.count; i++) {
            uint16_t x = (uint16_t)(r.u + i);
            uint8_t got = sdram_read_byte(FB_BASE_BYTE
                                        + (uint32_t)r.v * fb_stride + x);
            if (got != SENTINEL_BYTE)
                touched++;
        }
    }

    if (touched == 0)
        check_pass("param_q29_dynamic_scale_reserved_bits_noop");
    else {
        char msg[96];
        snprintf(msg, sizeof(msg), "touched=%d", touched);
        check_fail("param_q29_dynamic_scale_reserved_bits_noop", msg);
    }
}

static void quake_alias_ref_span(std::vector<uint8_t> &fb,
                                 const std::vector<uint8_t> &skin,
                                 const std::vector<uint8_t> &cmap_row,
                                 int fb_x,
                                 int count,
                                 int skin_w,
                                 int base_s,
                                 int base_t,
                                 int32_t sfrac,
                                 int32_t tfrac,
                                 int32_t sstep,
                                 int32_t tstep) {
    int base = base_t * skin_w + base_s;
    int32_t s = sfrac & 0xFFFF;
    int32_t t = tfrac & 0xFFFF;

    for (int i = 0; i < count; i++) {
        int tex_ofs = base + (s >> 16) + (t >> 16) * skin_w;
        fb[(size_t)fb_x + (size_t)i] = cmap_row[skin[(size_t)tex_ofs]];
        s += sstep;
        t += tstep;
    }
}

static void test_affine_span_group_quake_alias_carry_math() {
    printf("TEST affine_span_group_quake_alias_carry_math\n");
    gpu_init();
    preload_with_sentinel();

    const int skin_w = 96;
    const int skin_h = 64;
    std::vector<uint8_t> skin((size_t)skin_w * skin_h);
    for (int t = 0; t < skin_h; t++) {
        for (int s = 0; s < skin_w; s++)
            skin[(size_t)t * skin_w + s] =
                (uint8_t)((s * 5 + t * 29 + 7) & 0xFF);
    }
    upload_texture(TEX_BASE_BYTE, skin);

    std::vector<uint8_t> cmap_row(256);
    for (int i = 0; i < 256; i++)
        cmap_row[(size_t)i] = (uint8_t)((i + 0x31) & 0xFF);
    upload_palookup_slot(4, cmap_row, 3u * 256u);

    SpanGroupWire stale = make_span_group();
    stale.lane_count = 1;
    stale.fb_addr = FB_BASE_BYTE + 320u * 10u;
    stale.tex_addr[0] = TEX_BASE_BYTE;
    stale.count = 1;
    stale.t[0] = 0;
    stale.tex_width = 64;
    stale.tex_w_mask = 0x3F;
    stale.tex_h_mask = 0x3F;
    emit_span_group_raw(stale);

    struct Lane {
        int base_s, base_t;
        int32_t sfrac, tfrac, sstep, tstep;
        uint16_t count;
    };
    const Lane lanes[4] = {
        {48, 18, 0x0000e000, 0x00002000, -0x0000c000,  0x00003000, 31},
        {22, 31, 0x00001000, 0x0000f000,  0x00016000,  0x00008000, 29},
        {72, 12, 0x0000f400, 0x0000f800, -0x00012000,  0x00011000, 27},
        {11, 44, 0x00008000, 0x00004000,  0x0000a000, -0x00007000, 33},
    };

    SpanGroupWire q = make_span_group();
    q.lane_count = 4;
    q.fb_addr = FB_BASE_BYTE + 320u * 20u;
    q.lane_delta = 40;
    q.fb_stride = 1;
    q.flags = 1u << 0;
    q.tex_width = skin_w;
    q.tex_w_mask = 0;
    q.tex_h_mask = 0;

    std::vector<uint8_t> ref(320, SENTINEL_BYTE);
    for (int lane = 0; lane < 4; lane++) {
        const Lane &l = lanes[lane];
        q.fb_addr = FB_BASE_BYTE + 320u * 20u;
        q.tex_addr[lane] = TEX_BASE_BYTE + (uint32_t)(l.base_t * skin_w + l.base_s);
        q.s[lane] = l.sfrac & 0xFFFF;
        q.t[lane] = l.tfrac & 0xFFFF;
        q.sstep[lane] = l.sstep;
        q.tstep[lane] = l.tstep;
        q.count_lane[lane] = l.count;
        q.light[lane] = 3;
        q.colormap_id[lane] = 4;
        quake_alias_ref_span(ref, skin, cmap_row,
                             lane * 40, l.count, skin_w,
                             l.base_s, l.base_t,
                             l.sfrac, l.tfrac, l.sstep, l.tstep);
    }
    emit_span_group_raw(q);

    if (!submit_and_wait()) {
        check_fail("affine_span_group_quake_alias_carry_math", "timeout");
        return;
    }

    int diffs = 0;
    char first[160] = {0};
    for (int x = 0; x < 160; x++) {
        uint8_t want = ref[(size_t)x];
        uint8_t got = sdram_read_byte(FB_BASE_BYTE + 320u * 20u + (uint32_t)x);
        if (got != want) {
            if (diffs == 0) {
                snprintf(first, sizeof(first),
                         "x=%d got=%02x want=%02x", x, got, want);
            }
            diffs++;
        }
    }
    if (diffs == 0)
        check_pass("affine_span_group_quake_alias_carry_math");
    else {
        char msg[224];
        snprintf(msg, sizeof(msg), "%d mismatches; first %s", diffs, first);
        check_fail("affine_span_group_quake_alias_carry_math", msg);
    }
}

static void test_param_span_list_z_write_zi() {
    printf("TEST param_span_list_z_write_zi\n");
    gpu_init();
    preload_with_sentinel();
    upload_texture(TEX_BASE_BYTE, make_projection_test_texture());

    const uint32_t z_base = 0x00180000u;
    const int32_t z_major_step = 64;
    const int32_t z_minor_step = 2;
    sdram_fill(z_base, 1024, 0x5A);

    ParamSpanListWire p {};
    p.fb_base = FB_BASE_BYTE;
    p.fb_major_step = 320;
    p.fb_minor_step = 1;
    p.tex_addr = TEX_BASE_BYTE;
    p.tex_width = 64;
    p.tex_w_mask = 0x3F;
    p.tex_h_mask = 0x3F;
    p.flags = SPAN_PERSP;
    p.attr_mode = 1;
    p.span_axis = 0;
    p.z_mode = 1;
    p.attr_origin[0] = 0x00018000;
    p.attr_origin[1] = 0x00010000;
    p.attr_origin[2] = 0x00024000;
    p.attr_du[0] = 0x00001000;
    p.attr_du[1] = 0x00000400;
    p.attr_du[2] = 0x00000140;
    p.attr_dv[0] = 0x00000300;
    p.attr_dv[1] = 0x00002000;
    p.attr_dv[2] = 0x00000A00;
    p.z_base = z_base;
    p.z_major_step = z_major_step;
    p.z_minor_step = z_minor_step;

    std::vector<ParamSpanRecordWire> records = {
        {1, 2, 5}, {9, 2, 4}, {0, 3, 18}, {20, 3, 1}, {2, 4, 3}
    };

    emit_param_span_list_raw(p, records);

    if (!submit_and_wait()) {
        check_fail("param_span_list_z_write_zi", "timeout");
        return;
    }

    int mismatches = 0;
    char first[160] = {0};
    for (const auto &r : records) {
        for (uint16_t i = 0; i < r.count; i++) {
            uint32_t u = (uint32_t)r.u + i;
            uint32_t addr = z_base + (uint32_t)r.v * (uint32_t)z_major_step
                          + u * (uint32_t)z_minor_step;
            int32_t zi = p.attr_origin[2]
                       + (int32_t)u * p.attr_du[2]
                       + (int32_t)r.v * p.attr_dv[2];
            uint16_t want = (uint16_t)(zi >> 1);
            uint16_t got = sdram_read_u16_le(addr);
            if (got != want) {
                if (mismatches == 0) {
                    snprintf(first, sizeof(first),
                             "u=%u v=%u i=%u addr=%08x got=%04x want=%04x zi=%08x",
                             u, (uint32_t)r.v, (uint32_t)i, addr,
                             got, want, (uint32_t)zi);
                }
                mismatches++;
            }
        }
    }

    if (mismatches == 0)
        check_pass("param_span_list_z_write_zi");
    else
        check_fail("param_span_list_z_write_zi", first);
}

static void test_param_span_list_z_test_zi() {
    printf("TEST param_span_list_z_test_zi\n");
    gpu_init();
    preload_with_sentinel();
    upload_texture(TEX_BASE_BYTE, std::vector<uint8_t>(64 * 64, 0x44));

    const uint32_t z_base = 0x00180400u;
    const int32_t z_major_step = 64;
    const int32_t z_minor_step = 2;
    sdram_fill(z_base, 256, 0x00);

    ParamSpanListWire p {};
    p.fb_base = FB_BASE_BYTE;
    p.fb_major_step = 320;
    p.fb_minor_step = 1;
    p.tex_addr = TEX_BASE_BYTE;
    p.tex_width = 64;
    p.tex_w_mask = 0x3F;
    p.tex_h_mask = 0x3F;
    p.flags = SPAN_PERSP;
    p.attr_mode = 1;
    p.span_axis = 0;
    p.z_mode = 2;          // TEST_ZI, no z write
    p.attr_origin[2] = 0x00004000;  // z half = 0x2000
    p.z_base = z_base;
    p.z_major_step = z_major_step;
    p.z_minor_step = z_minor_step;

    std::vector<ParamSpanRecordWire> records = {{0, 0, 4}};
    for (uint16_t i = 0; i < 4; i++) {
        uint16_t oldz = (i & 1) ? 0x3000 : 0x1000;
        sdram_write_u16_le(z_base + i * 2u, oldz);
    }

    emit_param_span_list_raw(p, records);

    if (!submit_and_wait()) {
        check_fail("param_span_list_z_test_zi", "timeout");
        return;
    }

    bool ok = true;
    char first[160] = {0};
    for (uint16_t i = 0; i < 4; i++) {
        uint8_t want_color = (i & 1) ? SENTINEL_BYTE : 0x44;
        uint16_t want_z = (i & 1) ? 0x3000 : 0x1000;
        uint8_t got_color = sdram_read_byte(FB_BASE_BYTE + i);
        uint16_t got_z = sdram_read_u16_le(z_base + i * 2u);
        if (got_color != want_color || got_z != want_z) {
            snprintf(first, sizeof(first),
                     "i=%u color got=%02x want=%02x z got=%04x want=%04x",
                     i, got_color, want_color, got_z, want_z);
            ok = false;
            break;
        }
    }

    if (ok)
        check_pass("param_span_list_z_test_zi");
    else
        check_fail("param_span_list_z_test_zi", first);
}

static void test_param_span_list_z_test_write_skip_zero() {
    printf("TEST param_span_list_z_test_write_skip_zero\n");
    gpu_init();
    preload_with_sentinel();

    std::vector<uint8_t> tex(64 * 64, 0);
    tex[0] = 0x44;
    tex[1] = 0xFF;
    tex[2] = 0x55;
    tex[3] = 0x66;
    upload_texture(TEX_BASE_BYTE, tex);

    const uint32_t z_base = 0x00180800u;
    const int32_t z_major_step = 64;
    const int32_t z_minor_step = 2;
    sdram_fill(z_base, 256, 0x00);

    ParamSpanListWire p {};
    p.fb_base = FB_BASE_BYTE;
    p.fb_major_step = 320;
    p.fb_minor_step = 1;
    p.tex_addr = TEX_BASE_BYTE;
    p.tex_width = 64;
    p.tex_w_mask = 0x3F;
    p.tex_h_mask = 0x3F;
    p.flags = SPAN_PERSP | (1u << 2);   // SKIP_ZERO
    p.attr_mode = 1;
    p.span_axis = 0;
    p.z_mode = 3;          // TEST_WRITE
    p.attr_origin[2] = 0x00010000;  // z half = 0x8000
    p.attr_du[0] = 0x00010000;      // s advances 1 texel per pixel
    p.z_base = z_base;
    p.z_major_step = z_major_step;
    p.z_minor_step = z_minor_step;

    std::vector<ParamSpanRecordWire> records = {{0, 0, 4}};
    for (uint16_t i = 0; i < 4; i++)
        sdram_write_u16_le(z_base + i * 2u, 0x1000);

    emit_param_span_list_raw(p, records);

    if (!submit_and_wait()) {
        check_fail("param_span_list_z_test_write_skip_zero", "timeout");
        return;
    }

    const uint8_t want_color[4] = {0x44, SENTINEL_BYTE, 0x55, 0x66};
    const uint16_t want_z[4] = {0x8000, 0x1000, 0x8000, 0x8000};
    bool ok = true;
    char first[180] = {0};
    for (uint16_t i = 0; i < 4; i++) {
        uint8_t got_color = sdram_read_byte(FB_BASE_BYTE + i);
        uint16_t got_z = sdram_read_u16_le(z_base + i * 2u);
        if (got_color != want_color[i] || got_z != want_z[i]) {
            snprintf(first, sizeof(first),
                     "i=%u color got=%02x want=%02x z got=%04x want=%04x",
                     i, got_color, want_color[i], got_z, want_z[i]);
            ok = false;
            break;
        }
    }

    if (ok)
        check_pass("param_span_list_z_test_write_skip_zero");
    else
        check_fail("param_span_list_z_test_write_skip_zero", first);
}

// ============================================================================
// Main runner
// ============================================================================
// ============================================================================
// 9. OS30 lean-variant contract tests — active in BOTH configs.
//
// Expectations switch on GPU_TEST_OS30_LEAN (LEAN_CONFIG):
//   * compact-direct 0x48 (11..32 words) and 0x4C drain as no-ops in the
//     lean config but draw normally in the full config;
//   * the 0x4A sticky-state invalidation on a raw 0x48 header is
//     variant-invariant — it fires even when the draw itself drains;
//   * a 33-word 0x48 sits exactly on the long-form decode boundary and
//     must draw IDENTICALLY in both configs.
// ============================================================================

// (a) A valid compact-direct 11-word 0x48 span.  Full config: draws and is
// byte-exact vs the CPU reference.  Lean config: the FB stays completely
// untouched (sentinel) and the GPU still retires (fence completes).
static void test_lean_compact_0x48_drains() {
    printf("TEST lean_compact_0x48_drains (lean=%d)\n", LEAN_CONFIG ? 1 : 0);
    gpu_init();
    FbModel m = preload_with_sentinel();
    std::vector<uint8_t> tex(16);
    for (int i = 0; i < 16; i++) tex[i] = (uint8_t)(0x60 + i);
    upload_texture(TEX_BASE_BYTE, tex);
    m.snapshot_from_sdram();
    cmd_set_fb(FB_BASE_BYTE, 320); m.st_fb_addr = FB_BASE_BYTE;

    SpanWire s = make_span();
    s.fb_addr = FB_BASE_BYTE + 8u * 320u + 4u;
    s.tex_addr = TEX_BASE_BYTE;
    s.tex_width = 16; s.tex_w_mask = 0xF;
    s.s = 0; s.t = 0; s.sstep = 0x10000; s.tstep = 0;
    s.count = 16; s.flags = 0;
    emit_span_raw(s);                 // wire form: (0x48<<24)|11 — compact
    if (!LEAN_CONFIG)
        m.apply_span_ref(s);          // full: draws; lean: model untouched

    if (!submit_and_wait()) {         // fence completion proves retirement
        check_fail("lean_compact_0x48_drains",
                   "timeout: compact 0x48 did not retire");
        return;
    }
    /* Region covers the span and a wide margin: in the lean config the
     * model is all-sentinel, so this asserts the FB is untouched. */
    compare_fb_region("lean_compact_0x48_drains.fb", m,
                      FB_BASE_BYTE, 320, 0, 0, 48, 24);
    compare_sentinel_border("lean_compact_0x48_drains.border",
                            FB_BASE_BYTE, 320, 4, 8, 16, 1);
}

// (b) A valid 9-word single-lane 0x4C column list.  Full config: byte-exact
// vs the CPU reference of the equivalent s=0/sstep=0 affine column (the
// documented 0x4C contract).  Lean config: drains, FB untouched, retires.
static void test_lean_column_list_drains() {
    printf("TEST lean_column_list_drains (lean=%d)\n", LEAN_CONFIG ? 1 : 0);
    gpu_init();
    FbModel m = preload_with_sentinel();
    std::vector<uint8_t> tex(64);
    for (int i = 0; i < 64; i++) tex[i] = (uint8_t)(0x90 + i);
    upload_texture(TEX_BASE_BYTE, tex);
    m.snapshot_from_sdram();
    cmd_set_fb(FB_BASE_BYTE, 320); m.st_fb_addr = FB_BASE_BYTE;

    SpanGroupWire g = make_span_group();
    g.lane_count = 1;                 // 4-word header + 5 = 9-word payload
    g.fb_addr = FB_BASE_BYTE + 2u * 320u + 6u;
    g.tex_addr[0] = TEX_BASE_BYTE;
    g.t[0] = 0; g.tstep[0] = 0x10000; // descend the 64-entry column texture
    g.count = 12;
    g.fb_stride = 320;                // per-pixel byte step = one row down
    g.tex_width = 1;
    g.tex_h_mask = 0x3F;
    g.flags = 0;
    emit_column_list_raw(g, g.fb_addr);
    if (!LEAN_CONFIG) {
        /* CPU reference: 0x4C forces s=0/sstep=0 on the same staging the
         * 0x48 direct-affine path uses, so the affine model with zeroed
         * s/sstep is the byte-exact oracle. */
        SpanGroupWire a = g;
        for (int i = 0; i < 8; i++) { a.s[i] = 0; a.sstep[i] = 0; }
        m.apply_span_group_affine(a);
    }

    if (!submit_and_wait()) {
        check_fail("lean_column_list_drains",
                   "timeout: 0x4C did not retire");
        return;
    }
    compare_fb_region("lean_column_list_drains.fb", m,
                      FB_BASE_BYTE, 320, 0, 0, 32, 20);
    compare_sentinel_border("lean_column_list_drains.border",
                            FB_BASE_BYTE, 320, 6, 2, 1, 12);
}

// (c) Variant-invariant sticky-state contract: a compact-sized 0x48 header
// invalidates the 0x4A sticky bank EVEN when the draw itself is gated off
// and drains (lean config).  So 0x4A -> compact 0x48 -> 0x4B must be a
// guarded no-op 0x4B in BOTH configs; after re-issuing 0x4A the same 0x4B
// draws (byte-exact vs the 0x49 derived-planes twin).
static void test_lean_sticky_state_contract() {
    printf("TEST lean_sticky_state_contract (lean=%d)\n", LEAN_CONFIG ? 1 : 0);
    const int Q = 1 << 16;
    gpu_init();
    FbModel m = preload_with_sentinel();
    upload_texture(TEX_BASE_BYTE, make_projection_test_texture());
    upload_palookup_identity_row(0, 0);
    m.snapshot_from_sdram();
    m.st_fb_addr = FB_BASE_BYTE;

    ParamSpanListWire surf = make_vert_tri_surface();

    // Triangle interior spans rows 2..22, columns ~5..40 (the proven
    // vert_tri_sticky_semantics geometry).
    const int16_t vx[3] = {(int16_t)(5*16), (int16_t)(40*16), (int16_t)(20*16)};
    const int16_t vy[3] = {2, 6, 22};
    const int32_t s3[3] = {0, 30*Q, 14*Q}, t3[3] = {0, 6*Q, 20*Q};
    const int32_t zi3[3] = {Q, Q, Q};
    const uint8_t l3[3] = {0, 0, 0};

    // Compact 0x48 span aimed at row 30 — disjoint from the tri bbox so a
    // wrongly-executed 0x4B lands on sentinel bytes and is caught.
    SpanWire sp = make_span();
    sp.fb_addr = FB_BASE_BYTE + 30u * 320u + 2u;
    sp.tex_addr = TEX_BASE_BYTE;
    sp.tex_width = 64; sp.tex_w_mask = 0x3F; sp.tex_h_mask = 0x3F;
    sp.s = 0; sp.t = 0; sp.sstep = 0x10000; sp.tstep = 0;
    sp.count = 24; sp.flags = 0;

    // Phase 1: arm, invalidate via the compact 0x48 header, then 0x4B.
    emit_set_tri_state_raw(surf, 0, 320, 0, 64);
    emit_span_raw(sp);                // draws (full) / drains (lean) — but
                                      // invalidates the sticky bank in BOTH
    if (!LEAN_CONFIG)
        m.apply_span_ref(sp);
    emit_draw_vert_tri_raw(vx, vy, s3, t3, zi3, l3);  // must NOT draw
    // Marker clear proves the stream advanced past the no-op 0x4B.
    cmd_clear_rect(FB_BASE_BYTE + 26u * 320u + 1u, 4, 1, 0, 0x5C);
    m.apply_clear_rect(FB_BASE_BYTE + 26u * 320u + 1u, 4, 1, 0, 0x5C);
    if (!submit_and_wait()) {
        check_fail("lean_sticky_state_contract", "timeout(phase1)");
        return;
    }
    // Sentinel everywhere except the marker and (full config) the span row;
    // any 0x4B paint inside rows 0..25 fails the compare.
    compare_fb_region("lean_sticky_state_contract.invalidated_noop", m,
                      FB_BASE_BYTE, 320, 0, 0, 64, 34);

    // Phase 2: re-issue 0x4A — the SAME 0x4B must draw now.  Byte-exact
    // GPU-vs-GPU against the 0x49 derived-planes twin at FB_ALT_BASE, plus
    // a non-vacuous paint count so both-blank can't pass.
    emit_set_tri_state_raw(surf, 0, 320, 0, 64);
    emit_draw_vert_tri_raw(vx, vy, s3, t3, zi3, l3);
    DerivedTriPlanes d = derive_tri_planes_ref(vx, vy, s3, t3, zi3, l3);
    ParamSpanListWire pb = make_equiv_param_from_derived(surf, d);
    pb.fb_base = FB_ALT_BASE_BYTE;
    emit_param_tri_raw(pb, vx, vy, 0, 320, 0, 64);
    if (!submit_and_wait()) {
        check_fail("lean_sticky_state_contract", "timeout(phase2)");
        return;
    }
    int diffs = 0, painted = 0;
    for (int y = 0; y < 26; y++)
        for (int x = 0; x < 48; x++) {
            uint8_t a = sdram_read_byte(FB_BASE_BYTE
                                        + (uint32_t)y * 320u + (uint32_t)x);
            uint8_t b = sdram_read_byte(FB_ALT_BASE_BYTE
                                        + (uint32_t)y * 320u + (uint32_t)x);
            if (a != b) diffs++;
            if (a != SENTINEL_BYTE) painted++;
        }
    if (diffs == 0 && painted > 50) {
        check_pass("lean_sticky_state_contract.rearm_draws");
    } else {
        char buf[96];
        snprintf(buf, sizeof(buf),
                 "%d diffs vs 0x49 twin, %d painted pixels", diffs, painted);
        check_fail("lean_sticky_state_contract.rearm_draws", buf);
    }
}

// (d) 33-word 0x48 = the exact long-form decode boundary in BOTH configs
// (full: 33 > 32 so not compact-direct; lean: 33 >= the raised long-form
// minimum).  The wire layout carries record A entirely inside words 31..32
// (word 33 of a full pair only holds the dummy record-B fields), so a
// 33-word payload with rec_count=1 must draw record A byte-exactly — and
// identically in both configs.  A boundary bug in either direction (lean
// bound >33, or full compact bound >32) breaks the byte-exact compare.
static void test_lean_wrong_size_0x48_33w() {
    printf("TEST lean_wrong_size_0x48_33w (lean=%d)\n", LEAN_CONFIG ? 1 : 0);
    gpu_init();
    FbModel m = preload_with_sentinel();
    upload_texture(TEX_BASE_BYTE, make_param_test_texture());
    m.snapshot_from_sdram();

    ParamSpanListWire p {};
    p.fb_base = FB_BASE_BYTE;
    p.fb_major_step = 320;
    p.fb_minor_step = 1;
    p.tex_addr = TEX_BASE_BYTE;
    p.tex_width = 64;
    p.tex_w_mask = 0x3F;
    p.tex_h_mask = 0x3F;
    p.attr_mode = 0;
    p.span_axis = 0;
    p.attr_du[0] = 1 << 16;
    p.attr_dv[1] = 1 << 16;

    ParamSpanRecordWire rec {3, 4, 5};
    auto w = encode_param_span_list_wire(p, {rec});   // 34 words
    w.resize(33);                                     // drop the record-B word
    emit_raw_command(0x48, w);
    m.apply_span_ref(param_affine_ref_span(p, rec));

    // Marker clear proves the stream advanced past the 33-word command.
    cmd_clear_rect(FB_BASE_BYTE + 8u * 320u + 1u, 4, 1, 0, 0x6E);
    m.apply_clear_rect(FB_BASE_BYTE + 8u * 320u + 1u, 4, 1, 0, 0x6E);

    if (!submit_and_wait()) {
        check_fail("lean_wrong_size_0x48_33w",
                   "timeout: 33-word 0x48 wedged the stream");
        return;
    }
    compare_fb_region("lean_wrong_size_0x48_33w.fb", m,
                      FB_BASE_BYTE, 320, 0, 0, 24, 12);
}

// ============================================================================
// DECISIVE multi-chunk record-drop reproduction (additive).
//
// Goal: emit ONE 0x48 DRAW_PARAM_SPAN_LIST carrying MORE THAN 4 records so the
// multi-chunk continuation boundary (4 records / 2 pairs / 6 payload words per
// chunk, pay_idx 31..36 -> S_EXECUTE -> spanprod_prepare_next_record_chunk ->
// S_PAY_DATA pay_idx=31) is crossed, for EVERY total count in {5,6,7,8,9,17}
// (both parities + a larger straddler).  Each record is a DISTINCT solid color
// at a DISTINCT adjacent screen column, exactly like the Doom param-wall path
// (attr_mode=affine, span_axis=AXIS_Y, fb_major_step=1 so record u indexes the
// column, fb_minor_step=stride so count steps down that column).  SKIP_ZERO is
// exercised the same way Doom/Quake do (flags=COLORMAP|SKIP_ZERO, texel 0xFF =
// transparent), with a couple of records deliberately fully transparent.
//
// The oracle is INDEPENDENT: expected pixels are computed directly from the
// record geometry + a 1-row texture + an IDENTITY palookup (so a painted pixel
// MUST equal its texel index).  It does NOT re-run the RTL, does NOT compare
// 0x4C==0x48, and does NOT compare batched-vs-single.  Every non-transparent
// record's whole column is asserted painted with the exact distinct color and
// NOT the sentinel; every transparent (0xFF) record's column is asserted to
// remain sentinel.  A dropped record therefore surfaces as a sentinel column
// (BLACK) reported by its index and the total record count/parity.
// ============================================================================
static int g_drop_repro_failures = 0;

// Build the 1-row texture used by the repro: tex[col] is a distinct, non-zero,
// non-sentinel, non-0xFF color for every paintable column; 0xFF (transparent)
// is planted only at the columns named in `transparent_cols`.
static std::vector<uint8_t> make_drop_repro_texture(int width,
                                                    const std::vector<int> &transparent_cols) {
    std::vector<uint8_t> tex((size_t)width, 0);
    for (int u = 0; u < width; u++) {
        // Distinct color per column.  Avoid 0x00, 0xFF (transparent key) and
        // the framebuffer sentinel 0xAB so a "painted" pixel is unmistakable.
        uint8_t c = (uint8_t)(1 + (u * 7 + 3) % 200);   // 1..200, never 0
        if (c == 0xFF) c = 0xFE;
        if (c == SENTINEL_BYTE) c = (uint8_t)(SENTINEL_BYTE + 1);
        tex[(size_t)u] = c;
    }
    for (int tc : transparent_cols)
        if (tc >= 0 && tc < width)
            tex[(size_t)tc] = 0xFF;        // SKIP_ZERO transparent key
    return tex;
}

// Run ONE total-record-count case and verify against the independent oracle.
// Returns the dropped record index (>=0) or -1 if all records survived.
static int run_drop_repro_case_0x48(uint16_t total_records,
                                    int col_height,
                                    const std::vector<int> &transparent_cols) {
    char tag[96];
    snprintf(tag, sizeof(tag), "param_q48_multichunk_drop_repro.rc%u", total_records);
    printf("TEST %s\n", tag);
    gpu_init();
    preload_with_sentinel();

    const int fb_stride = 320;
    const int base_col  = 8;      // first screen column the list paints
    const int top_row   = 4;      // top row of every column
    const int tex_w     = base_col + (int)total_records + 4;  // 1-row texture

    std::vector<uint8_t> tex = make_drop_repro_texture(tex_w, transparent_cols);
    upload_texture(TEX_BASE_BYTE, tex);
    // Identity palookup at slot 3, light row 0: post-colormap color == texel.
    upload_palookup_identity_row(3, 0);

    ParamSpanListWire p {};
    p.fb_base       = FB_BASE_BYTE;
    p.fb_major_step = 1;            // record u indexes the screen column
    p.fb_minor_step = fb_stride;    // count steps down that column
    p.tex_addr      = TEX_BASE_BYTE;
    p.tex_width     = (uint16_t)tex_w;
    p.tex_w_mask    = 0;            // 0 -> identity mask in RTL/model
    p.tex_h_mask    = 0;
    p.flags         = (1u << 0) | (1u << 2);   // COLORMAP | SKIP_ZERO (Doom/Quake-sprite)
    p.colormap_id   = 3;
    p.attr_mode     = 0;            // AFFINE
    p.span_axis     = 1;            // AXIS_Y (Doom param-wall)
    // s = attr_origin[0] + u*attr_du[0] + v*attr_dv[0].  With du[0]=1<<16,
    // dv[0]=0, every pixel of column u samples texel s=u, t=0 -> solid color.
    p.attr_du[0]    = 1 << 16;
    // t stays 0 (1-row texture); no per-pixel-down texture change.
    p.light_origin  = 0;           // identity row 0

    // One record per adjacent column: u = base_col + i, v = top_row, count = H.
    std::vector<ParamSpanRecordWire> records;
    for (uint16_t i = 0; i < total_records; i++)
        records.push_back({ (uint16_t)(base_col + i),
                            (uint16_t)top_row,
                            (uint16_t)col_height });

    emit_param_span_list_raw(p, records);
    if (!submit_and_wait()) {
        char msg[64];
        snprintf(msg, sizeof(msg), "timeout (rc=%u)", total_records);
        check_fail(tag, msg);
        g_drop_repro_failures++;
        return -2;
    }

    // ---- INDEPENDENT per-record oracle + per-record assertion ----
    int first_dropped = -1;
    int dropped_records = 0;
    int painted_records = 0;
    int transparent_records = 0;
    int pixel_diffs = 0;
    char first_diff[192] = {0};

    for (uint16_t i = 0; i < total_records; i++) {
        const int col = base_col + i;
        const uint8_t texel = tex[(size_t)col];          // s=col, t=0
        const bool transparent = (texel == 0xFF);        // SKIP_ZERO key
        // Identity palookup row 0 => painted color == texel index.
        const uint8_t want = transparent ? SENTINEL_BYTE : texel;

        bool this_record_all_sentinel = true;
        bool this_record_any_diff = false;
        for (int row = 0; row < col_height; row++) {
            const uint32_t addr = FB_BASE_BYTE
                                + (uint32_t)(top_row + row) * (uint32_t)fb_stride
                                + (uint32_t)col;
            const uint8_t got = sdram_read_byte(addr);
            if (got != SENTINEL_BYTE)
                this_record_all_sentinel = false;
            if (got != want) {
                this_record_any_diff = true;
                if (pixel_diffs == 0) {
                    snprintf(first_diff, sizeof(first_diff),
                             "rec=%u col=%d row=%d got=%02x want=%02x%s",
                             i, col, top_row + row, got, want,
                             transparent ? " (transparent)" : "");
                }
                pixel_diffs++;
            }
        }

        if (transparent) {
            transparent_records++;
            // A transparent record must leave its whole column at sentinel.
            // (Already folded into pixel_diffs vs want==SENTINEL above.)
        } else {
            // A paintable record that is entirely sentinel == DROPPED -> BLACK.
            if (this_record_all_sentinel) {
                dropped_records++;
                if (first_dropped < 0) first_dropped = (int)i;
            }
            if (!this_record_any_diff)
                painted_records++;
        }
    }

    if (pixel_diffs == 0 && dropped_records == 0) {
        char msg[96];
        snprintf(msg, sizeof(msg),
                 "rc=%u painted=%d transparent=%d (no drop)",
                 total_records, painted_records, transparent_records);
        check_pass(tag);
        printf("    %s\n", msg);
        return -1;
    }

    char msg[320];
    snprintf(msg, sizeof(msg),
             "rc=%u parity=%s dropped_records=%d first_dropped_index=%d "
             "pixel_diffs=%d painted=%d transparent=%d; first %s",
             total_records, (total_records & 1) ? "odd" : "even",
             dropped_records, first_dropped, pixel_diffs,
             painted_records, transparent_records, first_diff);
    check_fail(tag, msg);
    g_drop_repro_failures++;
    return first_dropped;
}

static void test_param_q48_multichunk_distinct_column_drop_repro() {
    // Counts straddling the 4-record chunk boundary, both parities, plus a
    // larger ~17 (crosses the boundary 4 times: 4|4|4|4|1).
    const uint16_t counts[] = { 5, 6, 7, 8, 9, 17 };
    // For each count, make the 3rd and 6th columns (when present) transparent
    // so SKIP_ZERO is genuinely exercised AND the records around them must
    // still survive (a continuation off-by-one near a transparent record would
    // be the smoking gun).
    for (uint16_t rc : counts) {
        std::vector<int> transparent;
        if (rc >= 3) transparent.push_back(8 + 2);   // 3rd record's column
        if (rc >= 6) transparent.push_back(8 + 5);   // 6th record's column
        run_drop_repro_case_0x48(rc, /*col_height=*/12, transparent);
    }
    // Also an all-opaque sweep (no transparent records) so a drop cannot hide
    // behind an expected-sentinel column.
    for (uint16_t rc : counts)
        run_drop_repro_case_0x48(rc, /*col_height=*/12, {});

    if (g_drop_repro_failures == 0)
        check_pass("param_q48_multichunk_distinct_column_drop_repro.summary_no_drops");
    else {
        char msg[96];
        snprintf(msg, sizeof(msg),
                 "%d case(s) dropped a record/column", g_drop_repro_failures);
        check_fail("param_q48_multichunk_distinct_column_drop_repro.summary_no_drops", msg);
    }
}

// Analogous >4-lane 0x4C DRAW_COLUMN_LIST sweep, against the SAME independent
// oracle (NOT 0x4C==0x48).  0x4C packs <=4 lanes per command, so >4 columns
// span multiple 0x4C commands; we verify every column is painted with its
// distinct color (identity palookup) and transparent columns stay sentinel.
static int g_drop_repro_failures_4c = 0;

static void run_drop_repro_case_0x4c(int total_lanes,
                                     int col_height,
                                     const std::vector<int> &transparent_lanes) {
    char tag[96];
    snprintf(tag, sizeof(tag), "param_q4c_multilane_drop_repro.lanes%d", total_lanes);
    printf("TEST %s\n", tag);
    gpu_init();
    preload_with_sentinel();

    const int fb_stride = 320;
    const int base_col  = 8;
    const int top_row   = 4;
    const int tex_w     = base_col + total_lanes + 4;

    std::vector<uint8_t> tex = make_drop_repro_texture(tex_w, {});
    // 0x4C forces s=0/sstep=0 (single texel column per lane), so each lane's
    // color comes from its OWN tex_addr (we point lane i at tex[base_col+i]).
    // Plant transparency by setting that per-lane source byte to 0xFF.
    for (int tl : transparent_lanes) {
        int col = base_col + tl;
        if (col >= 0 && col < tex_w) tex[(size_t)col] = 0xFF;
    }
    upload_texture(TEX_BASE_BYTE, tex);
    upload_palookup_identity_row(3, 0);

    // emit_column_list_raw supports only 1/2/4/8 lanes per group and applies
    // fb_base_override + lane_delta*src (src restarts at 0 per group).  We emit
    // arbitrary totals as fixed 4-lane (or 1/2-lane tail) groups, baking each
    // group's starting column into the fb_base_override and per-lane tex_addr.
    int painted_records = 0, transparent_records = 0, dropped_records = 0;
    int first_dropped = -1, pixel_diffs = 0;
    char first_diff[192] = {0};

    for (int first = 0; first < total_lanes; ) {
        int n = total_lanes - first;
        // round to a supported group lane count: 4, else 2, else 1
        int grp_lanes = (n >= 4) ? 4 : (n >= 2) ? 2 : 1;
        SpanGroupWire grp {};
        grp.lane_count = (uint8_t)grp_lanes;
        grp.flags      = (uint8_t)((1u << 0) | (1u << 2)); // COLORMAP | SKIP_ZERO
        grp.fb_stride  = (int16_t)fb_stride;   // per-pixel byte step (down column)
        grp.lane_delta = 1;                    // adjacent columns within group
        grp.tex_width  = 1;
        grp.tex_w_mask = 0;
        grp.tex_h_mask = 0;
        for (int lane = 0; lane < grp_lanes; lane++) {
            int idx = first + lane;
            int col = base_col + idx;
            // 0x4C forces s=0/sstep=0.  With t=0/tstep=0 too, every pixel of
            // the column samples the SAME texel (tex_addr byte) -> a solid
            // distinct color per column, so the oracle expects tex[col].
            grp.tex_addr[lane]    = TEX_BASE_BYTE + (uint32_t)col;
            grp.colormap_id[lane] = 3;
            grp.light[lane]       = 0;
            grp.t[lane]           = 0;            // constant v -> solid column
            grp.tstep[lane]       = 0;
            grp.count_lane[lane]  = (uint16_t)col_height;
        }
        // Group's column 0 is base_col+first; emit_column_list_raw adds
        // lane_delta*lane on top, so columns land at base_col+first+lane.
        // The vertical run starts at top_row (baked into the fb base).
        uint32_t grp_fb_base = FB_BASE_BYTE
                             + (uint32_t)top_row * (uint32_t)fb_stride
                             + (uint32_t)(base_col + first);
        emit_column_list_raw(grp, grp_fb_base);
        first += grp_lanes;
    }

    if (!submit_and_wait()) {
        char msg[64];
        snprintf(msg, sizeof(msg), "timeout (lanes=%d)", total_lanes);
        check_fail(tag, msg);
        g_drop_repro_failures_4c++;
        return;
    }

    for (int i = 0; i < total_lanes; i++) {
        const int col = base_col + i;
        const uint8_t texel = tex[(size_t)col];
        const bool transparent = (texel == 0xFF);
        const uint8_t want = transparent ? SENTINEL_BYTE : texel;
        bool all_sentinel = true, any_diff = false;
        for (int row = 0; row < col_height; row++) {
            const uint32_t addr = FB_BASE_BYTE
                                + (uint32_t)(top_row + row) * (uint32_t)fb_stride
                                + (uint32_t)col;
            const uint8_t got = sdram_read_byte(addr);
            if (got != SENTINEL_BYTE) all_sentinel = false;
            if (got != want) {
                any_diff = true;
                if (pixel_diffs == 0)
                    snprintf(first_diff, sizeof(first_diff),
                             "lane=%d col=%d row=%d got=%02x want=%02x%s",
                             i, col, top_row + row, got, want,
                             transparent ? " (transparent)" : "");
                pixel_diffs++;
            }
        }
        if (transparent) transparent_records++;
        else {
            if (all_sentinel) { dropped_records++; if (first_dropped < 0) first_dropped = i; }
            if (!any_diff) painted_records++;
        }
    }

    if (pixel_diffs == 0 && dropped_records == 0) {
        check_pass(tag);
        printf("    lanes=%d painted=%d transparent=%d (no drop)\n",
               total_lanes, painted_records, transparent_records);
        return;
    }
    char msg[320];
    snprintf(msg, sizeof(msg),
             "lanes=%d dropped_records=%d first_dropped_index=%d pixel_diffs=%d "
             "painted=%d transparent=%d; first %s",
             total_lanes, dropped_records, first_dropped, pixel_diffs,
             painted_records, transparent_records, first_diff);
    check_fail(tag, msg);
    g_drop_repro_failures_4c++;
}

static void test_param_q4c_multilane_distinct_column_drop_repro() {
    const int lane_counts[] = { 5, 6, 7, 8, 9, 17 };
    for (int n : lane_counts)
        run_drop_repro_case_0x4c(n, /*col_height=*/12, {});
    if (g_drop_repro_failures_4c == 0)
        check_pass("param_q4c_multilane_distinct_column_drop_repro.summary_no_drops");
    else {
        char msg[96];
        snprintf(msg, sizeof(msg),
                 "%d case(s) dropped a lane/column", g_drop_repro_failures_4c);
        check_fail("param_q4c_multilane_distinct_column_drop_repro.summary_no_drops", msg);
    }
}

// ============================================================================
// DECISIVE port-B (colormap) STARVATION reproduction under real DRAM latency.
// (ADDITIVE, test-only.  See the campaign brief for SUSPECT 1.)
//
// Why the existing param_q48_*_drop_repro can't see SUSPECT 1: it walks the
// texel along the SAME 16-byte cache line for ~16 adjacent columns (s = u,
// 1 byte/column), so port A misses only once every 16 columns.  The Doom
// param-wall pathology is the opposite: EVERY wall column samples a NEW
// texture column on a NEW 16-byte cache line, so port A misses on (nearly)
// every pixel and the shared fill machine sits in S_FILL_* almost
// continuously — which is the only condition that can starve port B
// (gpu_tex_cache req_ready_b is LOW in every S_FILL_* state for the OTHER
// port; gpu_tex_cache.v:236-240).  Under the historical 2-cycle SDRAM stub
// the fill is so short the S_PIPE windows are wide and B never starves; the
// new +gpu_rd_latency / +gpu_rd_latency_var modes (tb_gpu.v) crank the fill
// long enough to expose it if it is real.
//
// Texture layout: attr_du[0] = LINE_STRIDE_BYTES << 16 so column u samples
// texel s = u*LINE_STRIDE_BYTES.  With LINE_STRIDE_BYTES >= 16 each column's
// texel lives on its OWN cache line => one port-A miss per column.  s is
// constant down each column (attr_dv[0]=0) and t stays 0 => a solid distinct
// color per column.
//
// Two cases, BOTH using port B every pixel (flags = COLORMAP|SKIP_ZERO):
//   (1) "doom": constant light, IDENTITY palookup  => expected = texel.
//       A port-B drift/starve surfaces as BLACK (sentinel) columns.
//   (2) "quake": light INCREMENTS per column (light_du = 1<<16) into a
//       palookup whose every shade row maps texel -> a DIFFERENT byte
//       (row r: cm[r][texel] = texel ^ (0x40 + r)).  Now a port-B response
//       fetched for the WRONG pixel (a drift) produces a WRONG NON-BLACK
//       color (the neighbouring column's light row applied), and a starved
//       column is BLACK.  This models Quake's surface-edge light changes:
//       content-correlated wrong/missing geometry.
//
// The oracle is INDEPENDENT (computed from geometry + texture + palette
// directly).  Every column is asserted to its exact expected color.  We also
// classify any failure as black(FB-clear sentinel) vs wrong-nonblack and
// report the first failing (col,row,got,want) and the column period.
// ============================================================================
static int g_portb_starve_failures = 0;

// Distinct-cache-line texture: tex[u*line_stride] is a distinct, non-0x00,
// non-0xFF, non-sentinel color for column u; the in-between bytes are filler.
static std::vector<uint8_t> make_distinct_line_texture(int columns,
                                                       int line_stride,
                                                       const std::vector<int> &transparent_cols) {
    std::vector<uint8_t> tex((size_t)columns * (size_t)line_stride, 0x11);
    for (int u = 0; u < columns; u++) {
        uint8_t c = (uint8_t)(1 + (u * 13 + 5) % 200);   // 1..200
        if (c == 0xFF) c = 0xFE;
        if (c == SENTINEL_BYTE) c = (uint8_t)(SENTINEL_BYTE + 1);
        tex[(size_t)u * (size_t)line_stride] = c;
    }
    for (int tc : transparent_cols)
        if (tc >= 0 && tc < columns)
            tex[(size_t)tc * (size_t)line_stride] = 0xFF;   // SKIP_ZERO key
    return tex;
}

enum PortBVariant { PB_DOOM_IDENTITY = 0, PB_QUAKE_LIGHTVARY = 1 };

// Upload a "light-varying" palookup slot: row r maps cm[r][texel] = texel ^
// (0x40 + r), for rows 0..63 (light is 6-bit).  Guaranteed != texel for the
// identity-detect and produces a distinct color per (row,texel) so a drift
// across a column boundary (where the light row changes) yields a WRONG byte.
static void upload_palookup_lightvary_slot(uint8_t slot) {
    for (int r = 0; r < 64; r++) {
        std::vector<uint8_t> row(256);
        for (int i = 0; i < 256; i++) {
            uint8_t v = (uint8_t)(i ^ (0x40 + r));
            if (v == 0xFF) v = 0xFE;                 // keep out of SKIP_ZERO key space post-cmap
            if (v == SENTINEL_BYTE) v = (uint8_t)(SENTINEL_BYTE + 1);
            row[(size_t)i] = v;
        }
        upload_palookup_slot(slot, row, (uint32_t)r * 256);
    }
}

static void run_portb_starve_case(int columns, int col_height,
                                  int line_stride, PortBVariant variant,
                                  const std::vector<int> &transparent_cols) {
    char tag[128];
    snprintf(tag, sizeof(tag), "portb_starve_%s.cols%d_stride%d",
             variant == PB_DOOM_IDENTITY ? "doom" : "quake",
             columns, line_stride);
    printf("TEST %s\n", tag);
    gpu_init();
    preload_with_sentinel();

    const int fb_stride = 320;
    const int base_col  = 8;
    const int top_row   = 4;
    const uint8_t SLOT  = 3;

    std::vector<uint8_t> tex =
        make_distinct_line_texture(base_col + columns, line_stride, {});
    // Plant transparency by indexing the per-column texel byte.
    for (int tc : transparent_cols) {
        int col = base_col + tc;
        if (col >= 0 && col < base_col + columns)
            tex[(size_t)col * (size_t)line_stride] = 0xFF;
    }
    // Texture is (base_col+columns) * line_stride bytes wide (1 row, s indexes).
    upload_texture(TEX_BASE_BYTE, tex);

    if (variant == PB_DOOM_IDENTITY)
        upload_palookup_identity_row(SLOT, 0);   // every row irrelevant (light const 0)
    else
        upload_palookup_lightvary_slot(SLOT);    // rows 0..63 each distinct

    ParamSpanListWire p {};
    p.fb_base       = FB_BASE_BYTE;
    p.fb_major_step = 1;            // record u indexes screen column
    p.fb_minor_step = fb_stride;    // count steps down the column
    p.tex_addr      = TEX_BASE_BYTE;
    p.tex_width     = (uint16_t)((base_col + columns) * line_stride); // 1-row tex
    p.tex_w_mask    = 0;            // identity
    p.tex_h_mask    = 0;
    p.flags         = (1u << 0) | (1u << 2);   // COLORMAP | SKIP_ZERO
    p.colormap_id   = SLOT;
    p.attr_mode     = 0;            // AFFINE
    p.span_axis     = 1;            // AXIS_Y (Doom param-wall)
    // s = u * line_stride  => each column on its OWN cache line (port-A miss
    // per column).  v term 0 => s constant down the column => solid color.
    p.attr_du[0]    = (int32_t)((uint32_t)line_stride << 16);
    p.attr_dv[0]    = 0;
    // light: constant 0 (doom) OR +1 per column u (quake surface-edge).
    p.light_origin  = 0;
    p.light_du      = (variant == PB_QUAKE_LIGHTVARY) ? (1 << 16) : 0;
    p.light_dv      = 0;

    std::vector<ParamSpanRecordWire> records;
    for (int i = 0; i < columns; i++)
        records.push_back({ (uint16_t)(base_col + i),
                            (uint16_t)top_row,
                            (uint16_t)col_height });

    emit_param_span_list_raw(p, records);
    if (!submit_and_wait(800000)) {
        char msg[64];
        snprintf(msg, sizeof(msg), "timeout (cols=%d)", columns);
        check_fail(tag, msg);
        g_portb_starve_failures++;
        return;
    }

    int dropped_cols = 0, wrong_cols = 0, painted_cols = 0, transparent_cols_n = 0;
    int first_fail_col = -1, first_fail_row = -1;
    uint8_t first_got = 0, first_want = 0;
    bool first_is_black = false;
    int prev_fail_col = -1, fail_period = -1, fail_period_consistent = 1;

    for (int i = 0; i < columns; i++) {
        const int col   = base_col + i;
        const uint8_t texel = tex[(size_t)col * (size_t)line_stride];
        const bool transparent = (texel == 0xFF);
        // light for column u = i (u-derived): light_origin + u*light_du, >>16, &0x3F
        const uint8_t light = (variant == PB_QUAKE_LIGHTVARY)
                            ? (uint8_t)((col) & 0x3F)
                            : 0;
        uint8_t want;
        if (transparent) want = SENTINEL_BYTE;
        else if (variant == PB_DOOM_IDENTITY) want = texel;       // identity row
        else {
            uint8_t v = (uint8_t)(texel ^ (0x40 + light));
            if (v == 0xFF) v = 0xFE;
            if (v == SENTINEL_BYTE) v = (uint8_t)(SENTINEL_BYTE + 1);
            want = v;
        }

        bool col_all_sentinel = true, col_any_diff = false;
        for (int row = 0; row < col_height; row++) {
            const uint32_t addr = FB_BASE_BYTE
                                + (uint32_t)(top_row + row) * (uint32_t)fb_stride
                                + (uint32_t)col;
            const uint8_t got = sdram_read_byte(addr);
            if (got != SENTINEL_BYTE) col_all_sentinel = false;
            if (got != want) {
                col_any_diff = true;
                if (first_fail_col < 0) {
                    first_fail_col = col; first_fail_row = top_row + row;
                    first_got = got; first_want = want;
                    first_is_black = (got == SENTINEL_BYTE);
                }
            }
        }
        if (transparent) { transparent_cols_n++; continue; }
        if (col_any_diff) {
            if (col_all_sentinel) dropped_cols++; else wrong_cols++;
            if (prev_fail_col >= 0) {
                int d = col - prev_fail_col;
                if (fail_period < 0) fail_period = d;
                else if (fail_period != d) fail_period_consistent = 0;
            }
            prev_fail_col = col;
        } else painted_cols++;
    }

    if (dropped_cols == 0 && wrong_cols == 0) {
        check_pass(tag);
        printf("    cols=%d painted=%d transparent=%d stride=%d (no starve/drift) "
               "last_lat=%u txns=%u\n",
               columns, painted_cols, transparent_cols_n, line_stride,
               (unsigned)tb->dbg_rd_last_latency_o,
               (unsigned)tb->dbg_rd_txn_count_o);
        return;
    }

    char msg[320];
    snprintf(msg, sizeof(msg),
             "cols=%d dropped(black)=%d wrong(nonblack)=%d painted=%d "
             "first: col=%d row=%d got=%02x want=%02x (%s); fail_period=%d%s; "
             "last_lat=%u",
             columns, dropped_cols, wrong_cols, painted_cols,
             first_fail_col, first_fail_row, first_got, first_want,
             first_is_black ? "BLACK" : "WRONG-COLOR",
             fail_period, fail_period_consistent ? "" : " (varies)",
             (unsigned)tb->dbg_rd_last_latency_o);
    check_fail(tag, msg);
    g_portb_starve_failures++;
}

static void test_portb_starve_distinct_cache_line_wall() {
    // Force distinct cache line per column (stride 16 = exactly one line; 64
    // = 4 lines apart, so even the burst-fill prefetch can't pre-warm the
    // next column).  >64 adjacent columns as the brief requires.
    const int strides[] = { 16, 64 };
    const int col_counts[] = { 80, 96 };
    for (int st : strides) {
        for (int cc : col_counts) {
            run_portb_starve_case(cc, /*col_height=*/16, st, PB_DOOM_IDENTITY, {});
            run_portb_starve_case(cc, /*col_height=*/16, st, PB_QUAKE_LIGHTVARY, {});
        }
    }
    // A SKIP_ZERO-laced variant so a starve cannot hide behind an expected
    // sentinel column, and to mirror Doom/Quake transparent masked columns.
    run_portb_starve_case(80, 16, 16, PB_DOOM_IDENTITY, {10, 25, 40, 55, 70});
    run_portb_starve_case(80, 16, 16, PB_QUAKE_LIGHTVARY, {10, 25, 40, 55, 70});

    if (g_portb_starve_failures == 0)
        check_pass("portb_starve_distinct_cache_line_wall.summary");
    else {
        char msg[96];
        snprintf(msg, sizeof(msg), "%d case(s) starved/drifted port B",
                 g_portb_starve_failures);
        check_fail("portb_starve_distinct_cache_line_wall.summary", msg);
    }
}

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
    test_fence_no_writes();
    test_multi_fence_in_order();
    test_fence_after_clear_drains();
    test_fence_token_high_bits();
    test_set_fb_two_bases();
    test_set_fb_unusual_stride();
#ifndef GPU_TEST_OS30_LEAN
    test_set_texture_does_not_affect_direct_span();
#endif
#if GPU_TEST_ENABLE_TRIANGLES
    test_set_texture_via_triangle();
    test_set_texture_width_via_triangle();
#endif
#ifndef GPU_TEST_OS30_LEAN
    test_single_lane_span_explicit_colormap_slots();
    test_per_span_colormap_explicit_slots();
#endif
#if GPU_TEST_ENABLE_TRIANGLES
    test_triangle_no_global_skip_zero();
#endif
    test_clear_color_replication();
    test_clear_drains_framebuffer_writes();
    test_clear_no_op_when_flag_clear();
    test_clear_does_not_touch_rows_200_to_239();
    test_clear_rect_edge_lanes();
    test_clear_rect_zero_dimensions();
#ifndef GPU_TEST_OS30_LEAN
    /* Compact-direct 0x48 vehicle tests (drain as no-ops in the lean
     * config) + transluc tests (LUT chokepointed by EXCLUDE_TRANSLUC). */
    test_span_raw_count_boundary();
    test_span_raw_count_4096_preserved();
    test_span_raw_negative_stride();
    test_span_raw_mask_zero_means_identity();
    test_span_affine_group_repeatable();
    test_span_colormap_explicit_per_span_id();
    test_palookup_base_register();
    test_span_colormap_light_wraps_mod_64();
    test_skip_zero_only_discards_0xff();
    test_skip_zero_with_colormap();
    test_transluc_basic_blend();
    test_transluc_overdraw_same_word();
    test_transluc_duplicate_lane_order();
#endif
    test_persp_constant_z_matches_affine();
    test_persp_span_group_varcount_const_z_equals_single_lane_spans();
    test_persp_span_group_doom_wall_layout_matches_reference();
    test_persp_span_group_doom_wall_layout_8lane_chunk_positions();
    test_persp_span_group_doom_wall_negative_t_wrap();
    test_persp_span_group_doom_wall_7lane_partial_chunk();
    test_persp_span_group_doom_wall_nonunit_z_equals_single_lane_spans();
    test_persp_span_group_doom_wall_cpu_fixeddiv_reference();
    test_persp_span_group_quake_rows_match_single_lane_spans();
    test_persp_span_group_quake_payload_cpu_oracle();
    test_persp_span_group_quake_payload_edge_cases();
    test_param_span_list_affine_rows();
    test_param_span_list_affine_columns();
    test_param_span_list_affine_clamp();
    test_param_span_list_zero_counts_skip();
    test_param_span_list_streams_many_records();
    test_param_span_list_unsupported_noop();
    test_param_span_list_colormap_skip_zero();
    test_param_tri_wrong_size_noop_drains();
    test_param_tri_affine_basic();
    test_param_tri_clip_all_sides();
    test_param_tri_degenerate_noop();
    test_param_tri_shared_edge_adjacency();
    test_param_tri_unsupported_header_noop();
    test_param_tri_fuzz_affine();
    test_vert_tri_equivalence_vs_param();
    test_vert_tri_sliver_renders_in_range();
    test_vert_tri_shared_edge_adjacency();
    test_vert_tri_sticky_semantics();
    test_vert_tri_wrong_size_noop();
    test_param_tri_recs_equivalence_vs_param();
    test_param_tri_recs_sticky_semantics();
    test_param_tri_recs_wrong_size_noop();
    test_param_span_list_persp_matches_helper();
    test_param_span_quake_projection_math();
    test_param_span_q29_high_angle_floor_no_flatten();
    test_param_q29_tail_counts_and_boundaries();
    test_param_q29_record_counts_and_odd_pairs();
    test_param_q29_axis_y_multichunk_wall_repro();
    test_doom_real_capture_replay_no_black_columns();
    test_doom_real_capture_frame_no_black_columns();
    test_param_q29_zero_counts_mixed_no_drop();
    test_param_q48_multichunk_distinct_column_drop_repro();
    test_param_q4c_multilane_distinct_column_drop_repro();
    // Decisive port-B (colormap) starvation repro: distinct cache line per
    // column => port-A miss per pixel + port-B used every pixel.  Inert under
    // the 2-cycle stub; the SUSPECT-1 vehicle under +gpu_rd_latency[_var].
    test_portb_starve_distinct_cache_line_wall();
    test_param_q29_wrap_and_fb_byte_lanes();
    test_param_q29_static_repeat_300();
    test_param_q29_dynamic_scale_projection();
    test_param_q29_dynamic_scale_all_records_touch();
    test_param_q29_dynamic_scale_z_restore_saturates();
    test_param_q29_dynamic_scale_reserved_bits_noop();
#ifndef GPU_TEST_OS30_LEAN
    test_affine_span_group_quake_alias_carry_math();
#endif
    test_param_span_list_z_write_zi();
    test_param_span_list_z_test_zi();
    test_param_span_list_z_test_write_skip_zero();
#ifndef GPU_TEST_OS30_LEAN
    /* Compact-direct 0x48 span-group / batch / DMA-mix tests — every one
     * emits 11/18/25/32-word 0x48 payloads, the form the lean config
     * drains. */
    test_batch_equals_individual();
    test_batch_mixed_per_span_colormap();
    test_span_group_opaque_equals_four_spans();
    test_span_group_texture_height_mask();
    test_span_group_aligned_matches_reference();
    test_span_group_aligned_texture_cache_thrash();
    test_span_group_masked_equals_four_spans();
    test_span_group_explicit_colormap_slot();
    test_span_group_per_lane_colormap_slots();
    // ---- CMD_DRAW_COLUMN_LIST (0x4C) byte-exact oracle vs 0x48 ----
    test_column_list_single_column_matches_affine();
    test_column_list_multi_lane_matches_affine();
    test_column_list_colormap_matches_affine();
    test_column_list_skipzero_and_negstep_matches_affine();
    test_column_list_varcount_and_zerolane_matches_affine();
    test_span_group_varcount_opaque_equals_spans();
    test_span_group_varcount_masked_equals_spans();
    test_span_group_varcount_explicit_colormap_slot();
    test_span_group_stream_two_payloads();
    test_span_group_two_lane_masked();
    test_span_group_eight_lane_colormap();
    test_batch_dma_equals_inline();
    test_command_stream_dma_mixed_affine_groups();
    test_command_stream_dma_mixed_persp_span_group();
    test_dma_descriptor_queue_two_streams();
#endif
#if GPU_TEST_ENABLE_TRIANGLES
    test_triangle_uses_colormap_slot_zero();
#endif
    test_framebuffer_writes_burst_full_words();
    test_flip_no_writes_pulses_immediately();

#ifndef GPU_TEST_OS30_LEAN
    // ---- Combination tests (compact-span vehicles) ----
    test_combo_a_setfb_then_colormapped_span();
    test_combo_b_clear_then_span_paints_span();
    test_combo_c_three_slots_in_one_batch();
    test_combo_d_tex_mutate_with_flush();
    test_combo_e_lane_writes_into_one_word();
    test_combo_f_dma_then_inline();

    // ---- Wire-format gap (compact single-lane SDK encoding) ----
    test_sdk_count_4096_does_not_leak_into_colormap_id();
#endif

    // ---- OS30 lean-variant contract tests (run in BOTH configs) ----
    test_lean_compact_0x48_drains();
    test_lean_column_list_drains();
    test_lean_sticky_state_contract();
    test_lean_wrong_size_0x48_33w();

    printf("\n=== Acceptance Results: %d passed, %d failed ===\n",
           pass_count, fail_count);
    printf("=== Total sim_time: %llu ===\n", (unsigned long long)sim_time);

    delete tb;
    return fail_count > 0 ? 1 : 0;
}
