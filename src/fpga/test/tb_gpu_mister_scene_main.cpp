//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * tb_gpu_mister_scene_main.cpp — Doom MAP01 courtyard "fence scene"
 * interleave test (MiSTer field-bug symptom shape).
 *
 * Symptom being modeled (MiSTer, Doom II MAP01 outdoor courtyard): through
 * the fence gaps, the region that must be SKY (the sky-hack band between the
 * z=64 rim and the terrace) renders as another surface's texture (CEMENT9).
 * The suite's existing cases prove every primitive in isolation; none
 * interleaves the full per-frame surface mix the Doom renderer actually
 * emits for that view:
 *
 *   1. walls   — AXIS_Y PERSP_Q29 param-span records (terrace lower tier)
 *   2. planes  — AXIS_X PERSP_Q29 param-span records (grass + ledge flats)
 *   3. sky     — CMD_DRAW_COLUMN_LIST (0x4C) colormap columns, TWO separate
 *                visplanes (upper sky + the fence-gap band)
 *   4. masked  — AXIS_Y PERSP_Q29 records for the fence bars, drawn last,
 *                overdrawing the sky (painter order)
 *
 * The test renders the scene TWICE with byte-identical records:
 *   PASS A (FB_BASE): all surfaces in frame order, phase-batched kicks, no
 *           drains between surfaces — the app's real submission cadence.
 *   PASS B (FB_ALT):  the same commands one surface at a time, with a fence
 *           and full drain between surfaces — the isolated oracle (each
 *           individual primitive is already proven byte-exact elsewhere).
 * The two framebuffers must be byte-identical.  Each surface's texture uses
 * a disjoint byte range, so a per-region content check names the offending
 * surface directly if the band (or any region) is painted with the wrong
 * texture — the exact field symptom, not just "pixel differs".
 *
 * Runs against the EXACT shipped MiSTer GPU config via the
 * gpu-acceptance-mister-scene target (INCLUDE_TEX_MEM=0 fold + triangles +
 * 2.5D fastpaths + TRANSLUC).
 *
 * Reuses the acceptance harness (tick/ring/SDRAM/emitters) by including it
 * with its runner renamed; only main() below executes.
 */

#define main gpu_acceptance_suite_main
#include "tb_gpu_acceptance_main.cpp"
#undef main

// ---------------------------------------------------------------------------
// Scene geometry: 320x200 FB, view window x in [40,296), y in [0,168).
// Per-column boundaries step every 8 columns (>>3 slopes), so any 4-aligned
// column quad shares extents (allows 4-lane 0x4C groups like the app).
// Vertical order in a fence-gap column (top -> bottom), disjoint:
//   [0            .. usky_bot(x)]   upper-sky visplane      (0x4C columns)
//   [usky_bot+1   .. band_bot(x)]   BAND sky visplane       (0x4C columns)
//   [band_bot+1   .. band_bot+2]    ledge flat sliver       (AXIS_X Q29)
//   [band_bot+3   .. cem_bot(x)]    terrace lower tier      (AXIS_Y Q29)
//   [cem_bot+1    .. 167]           grass                   (AXIS_X Q29)
// Fence bars: columns with (s % 24) < 5, rows [bar_top(x) .. cem_bot(x)],
// drawn LAST, overdrawing sky/flat/cement in those columns (masked overdraw).
// ---------------------------------------------------------------------------
static const int SCN_X0 = 40, SCN_X1 = 296;   // [SCN_X0, SCN_X1)
static const int SCN_H  = 168;
static const int FBW = 320, FBH = 200;

static inline int scn_usky_bot(int x) { int s = x - SCN_X0; return 40 + ((s >> 3) & 7); }
static inline int scn_band_bot(int x) { int s = x - SCN_X0; return 78 + ((s >> 3) & 7); }
static inline int scn_flat_bot(int x) { return scn_band_bot(x) + 2; }
static inline int scn_cem_bot(int x)  { int s = x - SCN_X0; return 110 + ((s >> 3) & 31); }
static inline bool scn_is_bar(int x)  { int s = x - SCN_X0; return (s % 24) < 5; }
static inline int scn_bar_top(int x)  { return scn_usky_bot(x) - 20; }

enum SceneRegion : uint8_t {
    RGN_OUT = 0, RGN_USKY, RGN_BAND, RGN_FLAT, RGN_CEM, RGN_GRASS, RGN_BAR
};

// Disjoint texture byte ranges (sentinel 0xAB avoided everywhere).
static inline uint8_t sky_byte(size_t i)   { return (uint8_t)(0x01 + (i % 15));   } // 01..0F
static inline uint8_t bar_byte(size_t i)   { return (uint8_t)(0x11 + (i % 14));   } // 11..1E
static inline uint8_t cem_byte(size_t i)   { return (uint8_t)(0x41 + (i % 62));   } // 41..7E
static inline uint8_t grass_byte(size_t i) { return (uint8_t)(0x81 + (i % 41));   } // 81..A9
static inline uint8_t flat_byte(size_t i)  { return (uint8_t)(0xC1 + (i % 46));   } // C1..EE

static bool byte_in_class(uint8_t b, SceneRegion r) {
    switch (r) {
        case RGN_USKY: case RGN_BAND: return b >= 0x01 && b <= 0x0F;
        case RGN_BAR:   return b >= 0x11 && b <= 0x1E;
        case RGN_CEM:   return b >= 0x41 && b <= 0x7E;
        case RGN_GRASS: return b >= 0x81 && b <= 0xA9;
        case RGN_FLAT:  return b >= 0xC1 && b <= 0xEE;
        default:        return b == SENTINEL_BYTE;
    }
}
static const char *region_name(SceneRegion r) {
    switch (r) {
        case RGN_USKY: return "upper-sky"; case RGN_BAND: return "BAND-sky";
        case RGN_FLAT: return "ledge-flat"; case RGN_CEM: return "cement";
        case RGN_GRASS: return "grass"; case RGN_BAR: return "fence-bar";
        default: return "outside";
    }
}

// Texture SDRAM layout (inside the suite's TEX_BASE_BYTE region).
static const uint32_t TEX_CEM   = TEX_BASE_BYTE + 0x00000;  // 64x64 flat-style
static const uint32_t TEX_GRASS = TEX_BASE_BYTE + 0x01000;
static const uint32_t TEX_FLAT  = TEX_BASE_BYTE + 0x02000;
static const uint32_t TEX_BARS  = TEX_BASE_BYTE + 0x03000;
static const uint32_t TEX_SKY   = TEX_BASE_BYTE + 0x04000;  // 64 cols x 128 rows col-major

static void scene_upload_textures() {
    std::vector<uint8_t> t(64 * 64);
    for (size_t i = 0; i < t.size(); i++) t[i] = cem_byte(i);
    upload_texture(TEX_CEM, t);
    for (size_t i = 0; i < t.size(); i++) t[i] = grass_byte(i);
    upload_texture(TEX_GRASS, t);
    for (size_t i = 0; i < t.size(); i++) t[i] = flat_byte(i);
    upload_texture(TEX_FLAT, t);
    for (size_t i = 0; i < t.size(); i++) t[i] = bar_byte(i);
    upload_texture(TEX_BARS, t);
    std::vector<uint8_t> sky(64 * 128);
    for (size_t i = 0; i < sky.size(); i++) sky[i] = sky_byte(i);
    upload_texture(TEX_SKY, sky);
}

// ---------------------------------------------------------------------------
// Record extraction from the region map.
// ---------------------------------------------------------------------------
struct SkyRun { int x, top, bot; };

static std::vector<uint8_t> scene_region_map() {
    std::vector<uint8_t> rgn((size_t)FBW * FBH, RGN_OUT);
    for (int x = SCN_X0; x < SCN_X1; x++) {
        for (int y = 0; y < SCN_H; y++) {
            SceneRegion r;
            if (y <= scn_usky_bot(x))      r = RGN_USKY;
            else if (y <= scn_band_bot(x)) r = RGN_BAND;
            else if (y <= scn_flat_bot(x)) r = RGN_FLAT;
            else if (y <= scn_cem_bot(x))  r = RGN_CEM;
            else                           r = RGN_GRASS;
            rgn[(size_t)y * FBW + x] = r;
        }
        if (scn_is_bar(x)) {
            for (int y = scn_bar_top(x); y <= scn_cem_bot(x); y++)
                rgn[(size_t)y * FBW + x] = RGN_BAR;   // painter: bars win
        }
    }
    return rgn;
}

// Per-column runs for a column-shaped region (before bar overdraw — the
// UNDERLYING surface extents; bars overdraw them exactly like Doom's masked
// pass overdraws the sky visplane behind the fence).
static void scene_column_runs(SceneRegion which, std::vector<SkyRun> &out) {
    for (int x = SCN_X0; x < SCN_X1; x++) {
        int top = -1, bot = -1;
        for (int y = 0; y < SCN_H; y++) {
            SceneRegion r;
            if (y <= scn_usky_bot(x))      r = RGN_USKY;
            else if (y <= scn_band_bot(x)) r = RGN_BAND;
            else if (y <= scn_flat_bot(x)) r = RGN_FLAT;
            else if (y <= scn_cem_bot(x))  r = RGN_CEM;
            else                           r = RGN_GRASS;
            if (r == which) { if (top < 0) top = y; bot = y; }
        }
        if (top >= 0) out.push_back({x, top, bot});
    }
}

// Per-row spans for a row-shaped region (flats).
static void scene_row_spans(SceneRegion which,
                            std::vector<ParamSpanRecordWire> &out) {
    for (int y = 0; y < SCN_H; y++) {
        int start = -1;
        for (int x = SCN_X0; x <= SCN_X1; x++) {
            SceneRegion r = RGN_OUT;
            if (x < SCN_X1) {
                if (y <= scn_usky_bot(x))      r = RGN_USKY;
                else if (y <= scn_band_bot(x)) r = RGN_BAND;
                else if (y <= scn_flat_bot(x)) r = RGN_FLAT;
                else if (y <= scn_cem_bot(x))  r = RGN_CEM;
                else                           r = RGN_GRASS;
            }
            if (r == which) { if (start < 0) start = x; }
            else if (start >= 0) {
                out.push_back({(uint16_t)start, (uint16_t)y,
                               (uint16_t)(x - start)});
                start = -1;
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Emit helpers for each surface (records identical across both passes; only
// fb base and submission cadence differ).
// ---------------------------------------------------------------------------

// AXIS_Y Q29 wall tier (R_GPU_WallTierBegin shape).  128 records per 0x48
// command = GPU_WALL_BAND_RECORDS, the app's flush threshold.
static void emit_wall_tier_q29(uint32_t fb_base, uint32_t tex_addr,
                               const std::vector<SkyRun> &cols) {
    QuakeQ29RefSetup q = make_quake_q29_ref_setup(64, 64);
    ParamSpanListWire p = make_quake_q29_param(q, fb_base, tex_addr,
                                               FBW, 64, 64);
    p.fb_major_step = 1;          // AXIS_Y: per-column
    p.fb_minor_step = FBW;        // per-pixel walk = row stride
    p.span_axis = 1;
    p.flags = 0x01;               // OF_GPU_SPAN_COLORMAP, light plane 0 -> row 0
    p.colormap_id = 0;

    std::vector<ParamSpanRecordWire> recs;
    recs.reserve(128);
    for (const auto &c : cols) {
        recs.push_back({(uint16_t)c.x, (uint16_t)c.top,
                        (uint16_t)(c.bot - c.top + 1)});
        if (recs.size() == 128) { emit_param_span_list_raw(p, recs); recs.clear(); }
    }
    if (!recs.empty()) emit_param_span_list_raw(p, recs);
}

// AXIS_X Q29 visplane (R_GPU_BeginPlaneSpans shape).
static void emit_plane_q29(uint32_t fb_base, uint32_t tex_addr,
                           const std::vector<ParamSpanRecordWire> &rows) {
    QuakeQ29RefSetup q = make_quake_q29_ref_setup(64, 64);
    ParamSpanListWire p = make_quake_q29_param(q, fb_base, tex_addr,
                                               FBW, 64, 64);
    p.flags = 0x01;
    p.colormap_id = 0;
    std::vector<ParamSpanRecordWire> recs;
    recs.reserve(128);
    for (const auto &r : rows) {
        recs.push_back(r);
        if (recs.size() == 128) { emit_param_span_list_raw(p, recs); recs.clear(); }
    }
    if (!recs.empty()) emit_param_span_list_raw(p, recs);
}

// Sky visplane as 0x4C column lists (R_DrawPlanes sky loop -> gpu_add_column
// shape: tex_width=1, w_mask=0, h_mask=127, colormap light row, 4 lanes max).
// Boundaries are constant across each 4-aligned quad, so lanes batch like the
// app's same-range column batches.
static void emit_sky_columns_4c(uint32_t fb_base,
                                const std::vector<SkyRun> &cols) {
    size_t i = 0;
    while (i < cols.size()) {
        // Gather up to 4 consecutive-x same-extent columns.
        size_t n = 1;
        while (n < 4 && i + n < cols.size()
               && cols[i + n].x == cols[i].x + (int)n
               && cols[i + n].top == cols[i].top
               && cols[i + n].bot == cols[i].bot)
            n++;

        SpanGroupWire g = make_span_group();
        g.lane_count = (n >= 4) ? 4 : ((n >= 2) ? 2 : 1);
        n = g.lane_count;                       // emit exactly what we claim
        g.lane_delta = 1;
        g.fb_stride = FBW;                      // per-pixel byte step (down)
        g.tex_width = 1;
        g.tex_w_mask = 0;
        g.tex_h_mask = 127;
        g.flags = 0x01;                         // COLORMAP
        g.fb_addr = fb_base + (uint32_t)cols[i].top * FBW + (uint32_t)cols[i].x;
        g.count = (uint16_t)(cols[i].bot - cols[i].top + 1);
        for (size_t l = 0; l < n; l++) {
            int colidx = (cols[i + l].x) & 63;
            g.tex_addr[l] = TEX_SKY + (uint32_t)colidx * 128u;
            g.t[l] = (100 << 16) + (int32_t)(cols[i + l].top) * 0x8000;
            g.tstep[l] = 0x8000;                // half-texel per pixel
            g.colormap_id[l] = 0;
            g.light[l] = (uint8_t)((cols[i + l].x >> 4) & 15);
        }
        emit_column_list_raw(g, g.fb_addr);
        i += n;
    }
}

// ---------------------------------------------------------------------------
// The test.
// ---------------------------------------------------------------------------
static void test_mister_courtyard_scene_interleave() {
    printf("TEST mister_courtyard_scene_interleave\n");
    gpu_init();
    preload_with_sentinel();
    scene_upload_textures();
    for (uint8_t row = 0; row < 16; row++)
        upload_palookup_identity_row(0, row);

    std::vector<uint8_t> rgn = scene_region_map();

    std::vector<SkyRun> cem_cols, usky_cols, band_cols, bar_cols_all;
    scene_column_runs(RGN_CEM, cem_cols);
    scene_column_runs(RGN_USKY, usky_cols);
    scene_column_runs(RGN_BAND, band_cols);
    for (int x = SCN_X0; x < SCN_X1; x++)
        if (scn_is_bar(x))
            bar_cols_all.push_back({x, scn_bar_top(x), scn_cem_bot(x)});

    std::vector<ParamSpanRecordWire> grass_rows, flat_rows;
    scene_row_spans(RGN_GRASS, grass_rows);
    scene_row_spans(RGN_FLAT, flat_rows);

    // ---- PASS A: interleaved frame (app cadence: phase kicks, no drains) --
    emit_wall_tier_q29(FB_BASE_BYTE, TEX_CEM, cem_cols);          // walls
    gpu_kick();
    emit_plane_q29(FB_BASE_BYTE, TEX_GRASS, grass_rows);          // planes
    emit_plane_q29(FB_BASE_BYTE, TEX_FLAT, flat_rows);
    emit_sky_columns_4c(FB_BASE_BYTE, usky_cols);                 // sky #1
    emit_sky_columns_4c(FB_BASE_BYTE, band_cols);                 // sky #2 (BAND)
    gpu_kick();
    emit_wall_tier_q29(FB_BASE_BYTE, TEX_BARS, bar_cols_all);     // masked
    if (!submit_and_wait(8000000)) {
        check_fail("mister_courtyard_scene_interleave", "timeout (interleaved pass)");
        return;
    }

    // ---- PASS B: isolated oracle (drain + fence between surfaces) --------
    struct Step { std::function<void()> emit; const char *what; };
    std::vector<Step> steps = {
        {[&]{ emit_wall_tier_q29(FB_ALT_BASE_BYTE, TEX_CEM, cem_cols); },   "cement"},
        {[&]{ emit_plane_q29(FB_ALT_BASE_BYTE, TEX_GRASS, grass_rows); },   "grass"},
        {[&]{ emit_plane_q29(FB_ALT_BASE_BYTE, TEX_FLAT, flat_rows); },     "flat"},
        {[&]{ emit_sky_columns_4c(FB_ALT_BASE_BYTE, usky_cols); },          "upper sky"},
        {[&]{ emit_sky_columns_4c(FB_ALT_BASE_BYTE, band_cols); },          "band sky"},
        {[&]{ emit_wall_tier_q29(FB_ALT_BASE_BYTE, TEX_BARS, bar_cols_all); }, "bars"},
    };
    for (auto &s : steps) {
        s.emit();
        if (!submit_and_wait(8000000)) {
            char msg[96];
            snprintf(msg, sizeof(msg), "timeout (isolated pass: %s)", s.what);
            check_fail("mister_courtyard_scene_interleave", msg);
            return;
        }
    }

    // ---- Assertions -------------------------------------------------------
    // 1. Byte-exact: interleaved == isolated over the whole FB.
    int diffs = 0;
    char first[192] = {0};
    for (int y = 0; y < FBH; y++) {
        for (int x = 0; x < FBW; x++) {
            uint32_t off = (uint32_t)y * FBW + (uint32_t)x;
            uint8_t a = sdram_read_byte(FB_BASE_BYTE + off);
            uint8_t b = sdram_read_byte(FB_ALT_BASE_BYTE + off);
            if (a != b) {
                if (diffs == 0)
                    snprintf(first, sizeof(first),
                             "x=%d y=%d region=%s interleaved=%02x isolated=%02x",
                             x, y, region_name((SceneRegion)rgn[off]), a, b);
                diffs++;
            }
        }
    }
    if (diffs != 0) {
        char msg[256];
        snprintf(msg, sizeof(msg),
                 "%d interleaved-vs-isolated byte diffs; first %s", diffs, first);
        check_fail("mister_courtyard_scene_interleave", msg);
        return;
    }

    // 2. Region-content: every painted pixel carries a byte from ITS OWN
    //    surface's texture class (both passes are identical by (1), so check
    //    the interleaved FB).  Wrong-class == the field symptom (e.g. the
    //    BAND-sky region holding cement bytes).
    int wrong = 0, band_wrong = 0;
    char wfirst[160] = {0};
    for (int y = 0; y < FBH; y++) {
        for (int x = 0; x < FBW; x++) {
            uint32_t off = (uint32_t)y * FBW + (uint32_t)x;
            SceneRegion r = (SceneRegion)rgn[off];
            uint8_t b = sdram_read_byte(FB_BASE_BYTE + off);
            if (!byte_in_class(b, r)) {
                if (wrong == 0)
                    snprintf(wfirst, sizeof(wfirst),
                             "x=%d y=%d region=%s byte=%02x", x, y,
                             region_name(r), b);
                wrong++;
                if (r == RGN_BAND) band_wrong++;
            }
        }
    }
    if (wrong != 0) {
        char msg[256];
        snprintf(msg, sizeof(msg),
                 "%d wrong-surface bytes (%d inside the BAND-sky region); first %s",
                 wrong, band_wrong, wfirst);
        check_fail("mister_courtyard_scene_interleave", msg);
        return;
    }

    // 3. Anti-vacuity: the band region exists and was painted with sky.
    int band_px = 0;
    for (size_t i = 0; i < rgn.size(); i++)
        if (rgn[i] == RGN_BAND) band_px++;
    if (band_px < 1000) {
        check_fail("mister_courtyard_scene_interleave",
                   "band region unexpectedly small (test geometry bug)");
        return;
    }

    check_pass("mister_courtyard_scene_interleave");
}

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    Verilated::traceEverOn(false);
    tb = new Vtb_gpu;
    trace = nullptr;

    printf("=== GPU MiSTer courtyard scene test ===\n\n");
    hard_reset();

    test_mister_courtyard_scene_interleave();

    printf("\n=== Acceptance Results: %d passed, %d failed ===\n",
           pass_count, fail_count);
    delete tb;
    return fail_count > 0 ? 1 : 0;
}
