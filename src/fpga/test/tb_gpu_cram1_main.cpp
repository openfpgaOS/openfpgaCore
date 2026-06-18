//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------
//
// tb_gpu_cram1_main.cpp -- REPRO driver: render the SAME light-varying
// colormap (palookup) wall TWICE through the REAL gpu_core --
//   (1) reg14 bit0 = 0  -> texture/colormap fills go to the SDRAM stub  (control)
//   (2) reg14 bit0 = 1  -> fills go to the REAL CRAM1 sync-burst chain   (suspect)
// and decide whether the CRAM1 route produces a WRONG / SHIFTED PALETTE
// while the SDRAM route is correct -- reproducing the Analogue Pocket HW bug
// with the REAL consumer (gpu_core port-B cmap path + gpu_tex_cache + adapter
// + cram1_controller + cram1_phy + chip model).
//
// The frame is the DECISIVE port-B starvation wall from the acceptance suite:
// a distinct-cache-line texture (one port-A miss per column => the shared fill
// FSM sits in S_FILL_* almost continuously, which is the ONLY condition that
// can starve port B) combined with a per-column light increment into a
// light-varying palookup (each shade row maps texel -> a DIFFERENT byte).  A
// port-B response fetched for the WRONG pixel yields a WRONG NON-BLACK color
// (neighbouring column's light row), a starved column is BLACK.  The oracle is
// computed independently from geometry + texture + palette.
//
// Addressing: PALOOKUP_BASE and TEX_BASE are placed in the LOW SDRAM region
// (byte < 0x200000) so the CRAM1 word address (araddr[23:2]) does not alias
// and fits the chip-model backdoor's 1:1 mirror (see tb_gpu_cram1.v).
//

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>

#include "Vtb_gpu.h"
#include "verilated.h"

// ============================================================================
// Globals / layout (relocated to the low, non-aliasing CRAM1-safe region)
// ============================================================================
static Vtb_gpu *tb;
static uint64_t sim_time = 0;

static const uint32_t TEX_BASE_BYTE      = 0x00010000;  // textures
static const uint32_t FB_BASE_BYTE       = 0x00080000;  // primary FB (SDRAM only)
static const uint32_t PALOOKUP_BASE_BYTE = 0x00100000;  // colormap slots (CRAM1-safe)
static const uint32_t PALOOKUP_STRIDE    = 0x00004000;  // 16 KB per slot
static const uint32_t BATCH_BUF_BYTE     = 0x00140000;  // doorbell-DMA scratch (SDRAM only)

static const uint8_t SENTINEL_BYTE = 0xAB;

static const uint32_t RING_SIZE = 0x00004000;
static uint32_t ring_wrptr      = 0;
static const uint32_t ring_mask = RING_SIZE - 1;
static std::vector<uint32_t> pending_stream;

enum {
    REG_CTRL          = 0,
    REG_RING_WRPTR    = 1,
    REG_DMA_SRC       = 3,
    REG_RING_RDPTR    = 4,
    REG_STATUS        = 5,
    REG_FENCE         = 6,
    REG_DMA_LEN       = 7,
    REG_TEX_FLUSH     = 10,
    REG_DMA_KICK      = 11,
    REG_PALOOKUP_BASE = 12,
    REG_CRAM0_TEX_EN  = 14,   // bit0 = route tex/cmap fills to CRAM1
};

// ============================================================================
// Tick / MMIO / SDRAM backdoor helpers
// ============================================================================
static void tick(int n = 1) {
    for (int i = 0; i < n; i++) {
        tb->clk = 0; tb->eval(); sim_time++;
        tb->clk = 1; tb->eval(); sim_time++;
    }
}

static void hard_reset() {
    tb->reset_n = 0;
    tb->reg_wr = 0;
    tb->bd_we = 0;
    tb->bd_no_mirror = 0;
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

// SDRAM-ONLY write (NOT mirrored into CRAM1) — used to poison the SDRAM
// colormap copy so a still-correct CRAM1-route render proves the data came
// from CRAM1.
static void sdram_write_no_mirror(uint32_t word_addr, uint32_t data) {
    tb->bd_no_mirror = 1;
    tb->bd_we = 1; tb->bd_addr = word_addr; tb->bd_wdata = data;
    tick();
    tb->bd_we = 0; tb->bd_no_mirror = 0;
}
static uint32_t sdram_read(uint32_t word_addr) {
    tb->bd_rd_addr = word_addr; tb->eval();
    return tb->bd_rd_data;
}
static uint8_t sdram_read_byte(uint32_t byte_addr) {
    return (uint8_t)((sdram_read(byte_addr >> 2) >> ((byte_addr & 3) * 8)) & 0xFF);
}
static void sdram_write_byte(uint32_t byte_addr, uint8_t v) {
    uint32_t w = sdram_read(byte_addr >> 2);
    int sh = (byte_addr & 3) * 8;
    w = (w & ~(0xFFu << sh)) | ((uint32_t)v << sh);
    sdram_write(byte_addr >> 2, w);
}
static void sdram_fill(uint32_t base_byte, uint32_t bytes, uint8_t value) {
    uint32_t fw = ((uint32_t)value) * 0x01010101u;
    uint32_t addr = base_byte, end = base_byte + bytes;
    while ((addr & 3) && addr < end) { sdram_write_byte(addr, value); addr++; }
    while (addr + 4 <= end) { sdram_write(addr >> 2, fw); addr += 4; }
    while (addr < end) { sdram_write_byte(addr, value); addr++; }
}

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
    fprintf(stderr, "wait_for_dma_idle: timeout (status=0x%08x)\n", mmio_read(REG_STATUS));
}

static void ring_write(uint32_t w) { pending_stream.push_back(w); }
static void ring_cmd(uint8_t cmd, uint32_t payload_words) {
    if (pending_stream.empty()) wait_for_dma_idle();
    ring_write(((uint32_t)cmd << 24) | (payload_words & 0x00FFFFFFu));
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
    ring_wrptr = 0;
    pending_stream.clear();
    mmio_write(REG_CTRL, 4);                          // ring_reset
    mmio_write(REG_PALOOKUP_BASE, PALOOKUP_BASE_BYTE);
    mmio_write(REG_RING_WRPTR, 0);
}

static uint32_t next_fence_token = 1;
static bool submit_and_wait(int timeout = 800000) {
    uint32_t t = next_fence_token++;
    ring_cmd(0x02, 1);
    ring_write(t);
    gpu_kick();
    for (int k = 0; k < timeout; k++) {
        tick();
        if ((int32_t)(tb->fence_reached - t) >= 0) return true;
    }
    fprintf(stderr, "  TIMEOUT waiting for fence %u (reached=%u)\n", t, tb->fence_reached);
    return false;
}

// ============================================================================
// Texture / palookup upload (SDRAM backdoor; tb_gpu_cram1.v mirrors to CRAM1)
// ============================================================================
static void upload_texture(uint32_t base_byte, const std::vector<uint8_t> &bytes) {
    for (size_t i = 0; i < bytes.size(); i++) sdram_write_byte(base_byte + i, bytes[i]);
}
static void upload_palookup_slot(uint8_t slot, const std::vector<uint8_t> &bytes, uint32_t start = 0) {
    uint32_t base = PALOOKUP_BASE_BYTE + (uint32_t)slot * PALOOKUP_STRIDE + start;
    for (size_t i = 0; i < bytes.size(); i++) sdram_write_byte(base + i, bytes[i]);
}
static void upload_palookup_identity_row(uint8_t slot, uint8_t row) {
    std::vector<uint8_t> idr(256);
    for (int i = 0; i < 256; i++) idr[i] = (uint8_t)i;
    upload_palookup_slot(slot, idr, (uint32_t)row * 256);
}
static void upload_palookup_lightvary_slot(uint8_t slot) {
    for (int r = 0; r < 64; r++) {
        std::vector<uint8_t> row(256);
        for (int i = 0; i < 256; i++) {
            uint8_t v = (uint8_t)(i ^ (0x40 + r));
            if (v == 0xFF) v = 0xFE;
            if (v == SENTINEL_BYTE) v = (uint8_t)(SENTINEL_BYTE + 1);
            row[(size_t)i] = v;
        }
        upload_palookup_slot(slot, row, (uint32_t)r * 256);
    }
}

// ============================================================================
// CMD_PARAM_SPAN_LIST (0x48) wire emit (faithful copy of acceptance encoder)
// ============================================================================
struct ParamSpanRecordWire { uint16_t u, v, count; };
struct ParamSpanListWire {
    uint32_t fb_base; int32_t fb_major_step, fb_minor_step;
    uint32_t tex_addr; uint16_t tex_width, tex_w_mask, tex_h_mask;
    uint8_t flags, colormap_id, attr_mode, span_axis, z_mode, q29_attr_shift;
    int32_t attr_origin[3], attr_du[3], attr_dv[3];
    int32_t light_origin, light_du, light_dv;
    int32_t clamp_min[3], clamp_max[3];
    uint32_t z_base; int32_t z_major_step, z_minor_step;
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
    w[0]=p.fb_base; w[1]=(uint32_t)p.fb_major_step; w[2]=(uint32_t)p.fb_minor_step;
    w[3]=p.tex_addr; w[4]=p.tex_width; w[5]=p.tex_w_mask; w[6]=p.tex_h_mask; w[7]=control;
    for (int i=0;i<3;i++){ w[8+i*3+0]=(uint32_t)p.attr_origin[i];
        w[8+i*3+1]=(uint32_t)p.attr_du[i]; w[8+i*3+2]=(uint32_t)p.attr_dv[i]; }
    w[17]=(uint32_t)p.light_origin; w[18]=(uint32_t)p.light_du; w[19]=(uint32_t)p.light_dv;
    for (int i=0;i<3;i++){ w[20+i*2+0]=(uint32_t)p.clamp_min[i]; w[20+i*2+1]=(uint32_t)p.clamp_max[i]; }
    w[26]=p.z_base; w[27]=(uint32_t)p.z_major_step; w[28]=(uint32_t)p.z_minor_step;
    w[29]=(uint32_t)records.size();
    w[30]=(p.attr_mode==3)?((uint32_t)p.q29_attr_shift & 31u):0u;
    for (size_t i=0;i<records.size();i+=2){
        ParamSpanRecordWire a=records[i], b{};
        if (i+1u<records.size()) b=records[i+1u];
        size_t k=31u+(i/2u)*3u;
        w[k+0]=((uint32_t)a.v<<16)|(uint32_t)a.u;
        w[k+1]=((uint32_t)b.u<<16)|(uint32_t)a.count;
        w[k+2]=((uint32_t)b.count<<16)|(uint32_t)b.v;
    }
    return w;
}
static void emit_param_span_list_raw(const ParamSpanListWire &p,
                                     const std::vector<ParamSpanRecordWire> &records) {
    auto w = encode_param_span_list_wire(p, records);
    ring_cmd(0x48, (uint32_t)w.size());
    for (uint32_t x : w) ring_write(x);
}

// ============================================================================
// Distinct-cache-line texture (faithful copy)
// ============================================================================
static std::vector<uint8_t> make_distinct_line_texture(int columns, int line_stride) {
    std::vector<uint8_t> tex((size_t)columns * (size_t)line_stride, 0x11);
    for (int u = 0; u < columns; u++) {
        uint8_t c = (uint8_t)(1 + (u * 13 + 5) % 200);
        if (c == 0xFF) c = 0xFE;
        if (c == SENTINEL_BYTE) c = (uint8_t)(SENTINEL_BYTE + 1);
        tex[(size_t)u * (size_t)line_stride] = c;
    }
    return tex;
}

// ============================================================================
// One render pass.  variant: 0=DOOM_IDENTITY, 1=QUAKE_LIGHTVARY.
// Returns: dropped(black) and wrong(nonblack) column counts, first failure.
// ============================================================================
struct PassResult {
    int columns, painted, dropped, wrong;
    int first_fail_col, first_fail_row;
    uint8_t first_got, first_want;
    bool first_is_black;
    int fail_period; bool fail_period_consistent;
    uint32_t cram1_fills;
    uint32_t chip_errors;
    uint32_t skid_pushes;   // port-B skid stale-push events during this pass
    uint32_t b_stall_cyc;   // cycles port B was held off during this pass
    uint32_t b_accepts;     // port-B requests accepted during this pass
    bool timed_out;
};

static PassResult render_wall(int columns, int col_height, int line_stride,
                              int variant, bool cram1_route,
                              bool poison_sdram_colormap = false) {
    PassResult r{};
    r.columns = columns;
    r.first_fail_col = -1; r.fail_period = -1; r.fail_period_consistent = true;

    gpu_init();

    // Wait for the CRAM1 BCR-init FSM to finish before routing fills to it.
    for (int t = 0; t < 2000 && !tb->cram1_bcr_done; t++) tick();

    // Pre-fill FB + texture region with sentinel.
    sdram_fill(FB_BASE_BYTE, 320u * 200u, SENTINEL_BYTE);
    sdram_fill(TEX_BASE_BYTE, 64u * 1024u, 0u);

    const int fb_stride = 320;
    const int base_col  = 8;
    const int top_row   = 4;
    const uint8_t SLOT  = 3;

    std::vector<uint8_t> tex = make_distinct_line_texture(base_col + columns, line_stride);
    upload_texture(TEX_BASE_BYTE, tex);

    if (variant == 0) upload_palookup_identity_row(SLOT, 0);
    else              upload_palookup_lightvary_slot(SLOT);

    // Optional provenance probe: overwrite the SDRAM colormap copy (slot SLOT,
    // 16 KB) with 0x00 WITHOUT mirroring into CRAM1.  After this, the SDRAM
    // colormap is all-zero but CRAM1 still holds the correct palette.  A
    // byte-exact CRAM1-route render then proves the colormap came from CRAM1.
    if (poison_sdram_colormap) {
        uint32_t base = PALOOKUP_BASE_BYTE + (uint32_t)SLOT * PALOOKUP_STRIDE;
        for (uint32_t w = 0; w < PALOOKUP_STRIDE; w += 4)
            sdram_write_no_mirror((base + w) >> 2, 0x00000000u);
    }

    // Route selection: reg14 bit0.  Done AFTER backdoor preload + BCR so the
    // chip mirror is populated and CRAM1 is ready before the first fill.
    mmio_write(REG_CRAM0_TEX_EN, cram1_route ? 1u : 0u);
    // Flush the tex cache so the FIRST fetch on the selected route is a real
    // miss/fill (no stale line carried over from a prior pass).
    mmio_write(REG_TEX_FLUSH, 1);
    tick(8);

    ParamSpanListWire p{};
    p.fb_base = FB_BASE_BYTE;
    p.fb_major_step = 1;
    p.fb_minor_step = fb_stride;
    p.tex_addr = TEX_BASE_BYTE;
    p.tex_width = (uint16_t)((base_col + columns) * line_stride);
    p.tex_w_mask = 0; p.tex_h_mask = 0;
    p.flags = (1u << 0) | (1u << 2);   // COLORMAP | SKIP_ZERO
    p.colormap_id = SLOT;
    p.attr_mode = 0;                   // AFFINE
    p.span_axis = 1;                   // AXIS_Y
    p.attr_du[0] = (int32_t)((uint32_t)line_stride << 16);
    p.attr_dv[0] = 0;
    p.light_origin = 0;
    p.light_du = (variant == 1) ? (1 << 16) : 0;
    p.light_dv = 0;

    std::vector<ParamSpanRecordWire> records;
    for (int i = 0; i < columns; i++)
        records.push_back({ (uint16_t)(base_col + i), (uint16_t)top_row, (uint16_t)col_height });

    uint32_t fills_before  = tb->cram1_fill_count;
    uint32_t skid_before   = tb->cram1_skid_push_count;
    uint32_t stall_before  = tb->cram1_b_stall_cycles;
    uint32_t accept_before = tb->cram1_b_accept_count;

    emit_param_span_list_raw(p, records);
    if (!submit_and_wait(900000)) { r.timed_out = true; return r; }

    r.cram1_fills  = tb->cram1_fill_count - fills_before;
    r.skid_pushes  = tb->cram1_skid_push_count - skid_before;
    r.b_stall_cyc  = tb->cram1_b_stall_cycles - stall_before;
    r.b_accepts    = tb->cram1_b_accept_count - accept_before;
    r.chip_errors  = tb->cram1_chip_errors;

    int prev_fail_col = -1;
    for (int i = 0; i < columns; i++) {
        const int col = base_col + i;
        const uint8_t texel = tex[(size_t)col * (size_t)line_stride];
        const uint8_t light = (variant == 1) ? (uint8_t)(col & 0x3F) : 0;
        uint8_t want;
        if (variant == 0) want = texel;
        else {
            uint8_t v = (uint8_t)(texel ^ (0x40 + light));
            if (v == 0xFF) v = 0xFE;
            if (v == SENTINEL_BYTE) v = (uint8_t)(SENTINEL_BYTE + 1);
            want = v;
        }
        bool col_all_sentinel = true, col_any_diff = false;
        for (int row = 0; row < col_height; row++) {
            const uint32_t addr = FB_BASE_BYTE + (uint32_t)(top_row + row) * (uint32_t)fb_stride + (uint32_t)col;
            const uint8_t got = sdram_read_byte(addr);
            if (got != SENTINEL_BYTE) col_all_sentinel = false;
            if (got != want) {
                col_any_diff = true;
                if (r.first_fail_col < 0) {
                    r.first_fail_col = col; r.first_fail_row = top_row + row;
                    r.first_got = got; r.first_want = want;
                    r.first_is_black = (got == SENTINEL_BYTE);
                }
            }
        }
        if (col_any_diff) {
            if (col_all_sentinel) r.dropped++; else r.wrong++;
            if (prev_fail_col >= 0) {
                int d = col - prev_fail_col;
                if (r.fail_period < 0) r.fail_period = d;
                else if (r.fail_period != d) r.fail_period_consistent = false;
            }
            prev_fail_col = col;
        } else r.painted++;
    }
    return r;
}

static void print_pass(const char *label, const PassResult &r) {
    if (r.timed_out) { printf("  %-22s TIMEOUT\n", label); return; }
    printf("  %-22s cols=%d painted=%d dropped=%d wrong=%d "
           "cram1_fills=%u B_accepts=%u B_stall_cyc=%u skid_push=%u chip_err=%u",
           label, r.columns, r.painted, r.dropped, r.wrong,
           (unsigned)r.cram1_fills, (unsigned)r.b_accepts, (unsigned)r.b_stall_cyc,
           (unsigned)r.skid_pushes, (unsigned)r.chip_errors);
    if (r.dropped || r.wrong) {
        printf("  first: col=%d row=%d got=%02x want=%02x (%s) period=%d%s",
               r.first_fail_col, r.first_fail_row, r.first_got, r.first_want,
               r.first_is_black ? "BLACK" : "WRONG-COLOR",
               r.fail_period, r.fail_period_consistent ? "" : " (varies)");
    }
    printf("\n");
}

// ============================================================================
// main: render the SAME wall under SDRAM route then CRAM1 route, compare.
// ============================================================================
int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    tb = new Vtb_gpu;

    printf("=== GPU CRAM1 colormap (palookup) repro ===\n");
    printf("Same light-varying wall rendered through the REAL gpu_core,\n");
    printf("once via SDRAM stub (reg14=0) and once via the REAL CRAM1 chain (reg14=1).\n\n");

    // Case matrix: distinct cache line per column (stride 16 / 64), wide walls.
    const int strides[]    = { 16, 64 };
    const int col_counts[] = { 80, 96 };

    int cram1_wrong_total = 0, cram1_dropped_total = 0;
    int sdram_wrong_total = 0, sdram_dropped_total = 0;
    bool any_cram1_fills = false;
    uint32_t cram1_skid_total = 0, cram1_bstall_total = 0, cram1_baccept_total = 0;

    for (int st : strides) {
        for (int cc : col_counts) {
            printf("CASE cols=%d stride=%d  (QUAKE light-varying palookup, port-B every pixel)\n", cc, st);

            PassResult sdram = render_wall(cc, 16, st, /*variant=*/1, /*cram1_route=*/false);
            print_pass("SDRAM route (reg14=0)", sdram);

            PassResult cram1 = render_wall(cc, 16, st, /*variant=*/1, /*cram1_route=*/true);
            print_pass("CRAM1 route (reg14=1)", cram1);

            sdram_wrong_total   += sdram.wrong;   sdram_dropped_total += sdram.dropped;
            cram1_wrong_total   += cram1.wrong;   cram1_dropped_total += cram1.dropped;
            cram1_skid_total    += cram1.skid_pushes;
            cram1_bstall_total  += cram1.b_stall_cyc;
            cram1_baccept_total += cram1.b_accepts;
            if (cram1.cram1_fills > 0) any_cram1_fills = true;

            // Classify the CRAM1 failure relative to the (correct) oracle.
            if ((cram1.wrong || cram1.dropped) && cram1.first_fail_col >= 0) {
                const char *cls;
                if (cram1.dropped && !cram1.wrong) cls = "DROPPED/BLACK (starved)";
                else if (cram1.wrong && !cram1.dropped) cls = "WRONG-PALETTE (shifted/region)";
                else cls = "MIXED (some black + some wrong)";
                printf("    >>> CRAM1 classification: %s\n", cls);
            }
            printf("\n");
        }
    }

    // Also run a DOOM-identity control (a wrong palette here = pure drift, not
    // light-row arithmetic) for one wide case on the CRAM1 route.
    {
        printf("CONTROL cols=96 stride=64 (DOOM identity palookup)\n");
        PassResult d_sdram = render_wall(96, 16, 64, /*variant=*/0, /*cram1_route=*/false);
        print_pass("SDRAM route (reg14=0)", d_sdram);
        PassResult d_cram1 = render_wall(96, 16, 64, /*variant=*/0, /*cram1_route=*/true);
        print_pass("CRAM1 route (reg14=1)", d_cram1);
        printf("\n");
    }

    // PROVENANCE PROOF: poison the SDRAM colormap (all-zero, NOT mirrored to
    // CRAM1) then render the light-varying wall on the CRAM1 route.  If still
    // byte-exact, the colormap data provably came from CRAM1 (had it come from
    // SDRAM, every pixel would read the zeroed palette).  We ALSO render the
    // same poisoned frame on the SDRAM route to confirm the poison is real
    // (that pass MUST go wrong).
    bool provenance_proven = false;
    {
        printf("PROVENANCE cols=96 stride=64 (QUAKE light-varying, SDRAM colormap POISONED to 0)\n");
        PassResult psd = render_wall(96, 16, 64, /*variant=*/1, /*cram1_route=*/false,
                                     /*poison_sdram_colormap=*/true);
        print_pass("SDRAM route (poisoned)", psd);
        PassResult pcr = render_wall(96, 16, 64, /*variant=*/1, /*cram1_route=*/true,
                                     /*poison_sdram_colormap=*/true);
        print_pass("CRAM1 route (poisoned)", pcr);
        bool sdram_went_wrong = (psd.wrong != 0 || psd.dropped != 0);
        bool cram1_still_ok   = (pcr.wrong == 0 && pcr.dropped == 0 && pcr.cram1_fills > 0);
        provenance_proven = sdram_went_wrong && cram1_still_ok;
        printf("    >>> provenance: SDRAM-poisoned render %s; CRAM1-route render %s\n",
               sdram_went_wrong ? "BROKE (poison real)" : "stayed correct (?? poison not biting)",
               cram1_still_ok ? "stayed BYTE-EXACT => colormap came from CRAM1"
                              : "broke (colormap NOT sourced from CRAM1)");
        printf("\n");
    }

    printf("----------------------------------------------------------------\n");
    printf("SDRAM route totals: wrong(nonblack)=%d dropped(black)=%d\n",
           sdram_wrong_total, sdram_dropped_total);
    printf("CRAM1 route totals: wrong(nonblack)=%d dropped(black)=%d  (cram1_fills_seen=%s)\n",
           cram1_wrong_total, cram1_dropped_total, any_cram1_fills ? "yes" : "NO");
    printf("CRAM1 sourced the colormap (provenance probe): %s\n",
           provenance_proven ? "PROVEN (SDRAM copy poisoned, CRAM1 render still byte-exact)"
                             : "NOT proven this run");
    printf("CRAM1 port-B handshake evidence: B_accepts=%u  B_stall_cycles=%u  "
           "SKID_STALE_PUSH=%u\n",
           (unsigned)cram1_baccept_total, (unsigned)cram1_bstall_total,
           (unsigned)cram1_skid_total);
    printf("  (B_stall_cycles>0 proves port B is genuinely held off during CRAM1 fills;\n");
    printf("   SKID_STALE_PUSH is the gpu_tex_cache.v L425-431 event that would corrupt\n");
    printf("   the NEXT colormap read.  Zero => the real consumer keeps cmap_resp_pop_b\n");
    printf("   coupled to accept_b, so the skid never carries a stale byte.)\n");

    bool sdram_clean = (sdram_wrong_total == 0 && sdram_dropped_total == 0);
    bool cram1_bad   = (cram1_wrong_total != 0 || cram1_dropped_total != 0);

    printf("----------------------------------------------------------------\n");
    if (!any_cram1_fills) {
        printf("HARNESS ERROR: no CRAM1 fills observed -- the CRAM1 route never\n");
        printf("engaged (reg14 / cram0_tex wiring / BCR-init).  Result inconclusive.\n");
        delete tb; return 2;
    }
    if (sdram_clean && cram1_bad) {
        printf("RESULT: REPRODUCED.  The SDRAM route renders the palette CORRECTLY\n");
        printf("but the CRAM1 route produces a WRONG/SHIFTED palette with the REAL\n");
        printf("gpu_core consumer.  This matches the Analogue Pocket HW symptom.\n");
        delete tb; return 1;
    }
    if (sdram_clean && !cram1_bad) {
        printf("RESULT: NOT reproduced.  The CRAM1 route renders the palette CORRECTLY\n");
        printf("through the real consumer (chip_err=0, oracle byte-exact).\n");
        if (cram1_bstall_total > 0 && cram1_skid_total == 0) {
            printf("DECISIVE: port B WAS held off during CRAM1 fills (B_stall_cycles=%u) yet the\n",
                   (unsigned)cram1_bstall_total);
            printf("skid stale-push NEVER fired (SKID_STALE_PUSH=0).  The real gpu_core consumer\n");
            printf("keeps cmap_resp_pop_b coupled to accept_b under CRAM1 burst timing -- it does\n");
            printf("NOT decouple the way the one-cycle-late tb_cram1_cmap_chain driver did.  The\n");
            printf("wrong-palette HW bug is therefore NOT this skid/pop path under this sim's\n");
            printf("CRAM1 model; look elsewhere (real-silicon CRAM1 timing vs this model, the\n");
            printf("upload/addressing path, or a different consumer interaction).\n");
        }
        delete tb; return 0;
    }
    printf("RESULT: INCONCLUSIVE.  SDRAM route itself is not clean (wrong=%d dropped=%d);\n",
           sdram_wrong_total, sdram_dropped_total);
    printf("the control case must pass before the CRAM1 comparison is meaningful.\n");
    delete tb; return 3;
}
