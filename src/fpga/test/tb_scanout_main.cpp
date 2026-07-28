//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * Verilator C++ harness for tb_scanout — checks analog TIMING and DATA.
 *
 * Timing: timing=1 (480p) must stay ~31.5 kHz / 525 lines (VGA path);
 *         timing=0 (240p) must be ~15.75 kHz / ~262 lines;
 *         timing=2 (480i NTSC) must be 15.75 kHz / 262.5 lines per field
 *         (~60 Hz fields) with the half-line vsync offset alternating;
 *         timing=3 (576i PAL) must be 15.63 kHz / 312.5 lines per field
 *         (~50 Hz fields), same alternation.
 * Data:   each analog active line must show the correct framebuffer source
 *         row. The SDRAM model encodes source-row R into every RGB565 pixel
 *         of row R, so the decoded analog color is recovered to R in C++.
 *         Progressive: active line A shows row A (320x240, out_height=240).
 *         Interlaced (480-tall fb): the LONG field (vsync aligned to line
 *         start) shows rows 0,2,4,..; the SHORT field (vsync delayed half a
 *         line) shows rows 1,3,5,..
 */
#include <cstdio>
#include <cstdlib>
#include <vector>
#include <algorithm>
#include "Vtb_scanout.h"
#include "verilated.h"

static Vtb_scanout *tb;
static uint64_t cycles = 0;

static void tick() { tb->clk = 0; tb->eval(); tb->clk = 1; tb->eval(); cycles++; }

static long typical_period(const std::vector<uint64_t> &ts) {
    if (ts.size() < 4) return -1;
    std::vector<long> d;
    for (size_t i = 1; i < ts.size(); i++) d.push_back((long)(ts[i] - ts[i - 1]));
    std::sort(d.begin(), d.end());
    return d[d.size() / 2];
}

// Recover the source-row number from an RGB565-decoded 24-bit color.
static int rgb565_src(uint32_t c) {
    return (int)((((c >> 19) & 0x1F) << 11) | (((c >> 10) & 0x3F) << 5) | ((c >> 3) & 0x1F));
}

struct Result {
    long line_cyc, frame_cyc;
    double lines_per_frame;
    long active_lines;
    int data_mismatch;     // # analog active lines whose shown row != expected
    int data_checked;      // # analog active lines compared
    int lcd_mismatch;      // # LCD active lines whose shown row != expected
    int lcd_checked;       // # LCD active lines compared (steady state)
    int field_seq_bad;     // interlace: # consecutive vsyncs with SAME parity
    long vs_off_short;     // median vsync->line-start offset, short-offset class
    long vs_off_long;      // median offset, long-offset class (~half line)
    int fetch_reqs;        // analog SDRAM line-fetch requests issued by the DUT
};

// timing: 0=240p, 1=480p, 2=480i NTSC, 3=576i PAL (tb/DUT ATIMING_* encoding)
static Result measure(int timing, int analog_split, int analog_upscale,
                      int analog_tall, int fetch_ena = 1) {
    const uint64_t RUN = 6000000ULL;
    const bool interlaced = (timing >= 2);
    tb->analog_timing = timing;
    tb->analog_split = analog_split;
    tb->analog_upscale = analog_upscale;
    tb->analog_tall = analog_tall;
    tb->analog_fetch_ena = fetch_ena;
    tb->reset_n = 0;
    for (int i = 0; i < 40; i++) tick();
    tb->reset_n = 1;
    for (int i = 0; i < 10; i++) tick();

    std::vector<uint64_t> hs_edges, vs_edges;
    std::vector<long> active_per_frame;
    std::vector<long> off_short, off_long;
    int prev_hs = 0, prev_vs = 0, prev_vb = 1;
    long active_lines = 0;
    uint64_t last_hs_rise = 0;
    int last_parity = -1, field_seq_bad = 0;

    // data capture: one settled frame for progressive (>=480 for the 480p
    // upscale case), or one field of EACH parity for interlaced.
    const int MAXL = 512;
    std::vector<std::vector<int>> src_field(2, std::vector<int>(MAXL, -1));
    int frames_seen = 0;
    int cap_line = -1;             // active-line index within the captured field
    int cap_parity = 0;            // which src_field[] the capture writes
    bool capturing = false;
    const int cap_first = 4;
    const int cap_count = interlaced ? 2 : 1;   // capture both field parities

    // LCD capture: the LCD always runs the tb 480p raster (out_height=240 =>
    // vid_v_active_start=18, active lines 18..257 show source rows 0..239). The
    // oracle gives every pixel of source row R the value R, so the decoded LCD
    // pixel sampled mid-active-line must equal (y_count-18). Last-write-wins
    // over the whole run captures steady state; a starved LCD fetch would leave
    // wrong/black (src 0) lines that this check flags.
    std::vector<int> lcd_src_by_line(240, -1);

    for (uint64_t i = 0; i < RUN; i++) {
        tick();
        int hs = tb->analog_hsync, vs = tb->analog_vsync;
        int hb = tb->analog_hblank, vb = tb->analog_vblank;
        uint32_t col = tb->analog_pixel_color_o;

        if (!prev_hs && hs) {
            hs_edges.push_back(cycles);
            last_hs_rise = cycles;
            if (!vb) active_lines++;
        }
        if (!prev_vs && vs) {
            vs_edges.push_back(cycles);
            // Field parity from the vsync's offset to the last line start:
            // the long field's vsync rises with hsync (offset ~0); the short
            // field's is delayed ~H_TOTAL/2 (1560/1572 clk NTSC/PAL).
            long off = (long)(cycles - last_hs_rise);
            int parity = (off > 700) ? 1 : 0;
            (parity ? off_long : off_short).push_back(off);
            if (frames_seen >= 2 && interlaced) {
                if (parity == last_parity) field_seq_bad++;
            }
            last_parity = parity;
            frames_seen++;
            if (frames_seen >= cap_first && frames_seen < cap_first + cap_count) {
                capturing = true; cap_line = -1; cap_parity = parity;
            } else if (frames_seen == cap_first + cap_count) {
                capturing = false;
            }
        }
        if (!prev_vb && vb) { active_per_frame.push_back(active_lines); active_lines = 0; }

        if (capturing) {
            if (!prev_hs && hs && !vb) cap_line++;     // new active line
            if (!hb && !vb && cap_line >= 0 && cap_line < MAXL)
                src_field[cap_parity][cap_line] = rgb565_src(col);  // last sample
        }

        // LCD sample: mid-active column (x in [120,440)) of each active line.
        int xc = tb->x_count_o, yc = tb->y_count_o;
        if (yc >= 18 && yc < 258 && xc == 300) {
            int line = yc - 18;
            if (line >= 0 && line < 240)
                lcd_src_by_line[line] = rgb565_src(tb->pixel_color_o);
        }

        prev_hs = hs; prev_vs = vs; prev_vb = vb;
    }

    Result r;
    r.line_cyc  = typical_period(hs_edges);
    r.frame_cyc = typical_period(vs_edges);
    r.lines_per_frame = (r.line_cyc > 0 && r.frame_cyc > 0)
                        ? (double)r.frame_cyc / (double)r.line_cyc : 0.0;
    r.active_lines = active_per_frame.empty() ? -1
                     : active_per_frame[active_per_frame.size() / 2];
    r.field_seq_bad = field_seq_bad;
    auto median = [](std::vector<long> &v) -> long {
        if (v.empty()) return -1;
        std::sort(v.begin(), v.end());
        return v[v.size() / 2];
    };
    r.vs_off_short = median(off_short);
    r.vs_off_long  = median(off_long);

    // When split, the analog framebuffer is offset by +1000 rows, so analog
    // active line k must show source row k+1000 (proving it reads its OWN fb).
    int aoff = analog_split ? 1000 : 0;
    r.data_mismatch = 0; r.data_checked = 0;
    if (interlaced) {
        // 480-tall source on a 240-line field: long field (parity 0) shows
        // rows 0,2,4,..; short field (parity 1) shows rows 1,3,5,..
        for (int f = 0; f < 2; f++) {
            for (int k = 0; k < 240; k++) {
                if (src_field[f][k] < 0) continue;
                r.data_checked++;
                int expected = 2 * k + f + aoff;
                if (src_field[f][k] != expected) r.data_mismatch++;
            }
        }
    } else {
        // expected: active line k shows source row k (320x240, out_height 240).
        // When upscaling (out 480 from a 240-tall source), active line k shows
        // source row k/2 (the vertical scaler doubles each row to fill 480p).
        int nl = (r.active_lines > 0 && r.active_lines < MAXL)
                 ? (int)r.active_lines : (analog_upscale ? 480 : 240);
        for (int k = 0; k < nl; k++) {
            if (src_field[0][k] < 0) continue;
            r.data_checked++;
            int expected = (analog_upscale ? k / 2 : k) + aoff;
            if (src_field[0][k] != expected) r.data_mismatch++;
        }
    }
    // LCD steady-state check. Skip line 0 (its expected src 0 aliases a black
    // starved line, so it can't distinguish correct from starved).
    r.lcd_mismatch = 0; r.lcd_checked = 0;
    for (int k = 1; k < 240; k++) {
        if (lcd_src_by_line[k] < 0) continue;
        r.lcd_checked++;
        if (lcd_src_by_line[k] != k) r.lcd_mismatch++;
    }
    r.fetch_reqs = (int)tb->analog_fetch_reqs;
    return r;
}

static void report(const char *name, const Result &r) {
    const double CLK_HZ = 49.152e6;
    printf("--- %s ---\n", name);
    printf("  line  = %ld clk -> %.2f kHz\n", r.line_cyc, r.line_cyc>0?CLK_HZ/r.line_cyc/1000.0:0);
    printf("  frame = %ld clk -> %.2f Hz\n", r.frame_cyc, r.frame_cyc>0?CLK_HZ/r.frame_cyc:0);
    printf("  lines/frame = %.1f, active lines = %ld\n", r.lines_per_frame, r.active_lines);
    printf("  vsync offset to line start: short=%ld clk, long=%ld clk, seq_bad=%d\n",
           r.vs_off_short, r.vs_off_long, r.field_seq_bad);
    printf("  analog data: %d/%d active lines show the WRONG source row\n",
           r.data_mismatch, r.data_checked);
    printf("  LCD    data: %d/%d active lines show the WRONG source row\n",
           r.lcd_mismatch, r.lcd_checked);
    printf("  analog fetch requests issued: %d\n", r.fetch_reqs);
}

static bool nearv(double v, double t, double tol) { return v >= t*(1-tol) && v <= t*(1+tol); }

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    tb = new Vtb_scanout;
    const double CLK_HZ = 49.152e6;

#ifdef SCANOUT_TEST_LEAN
    /* HAS_ANALOG_RASTER=0 build (OS30 lean): the dedicated analog raster /
     * fetch / line-cache are pruned, so the analog hsync/vsync never fire and
     * every analog timing/data assertion is vacuous — skip them.  The LCD
     * path through the shared output pipeline is untouched, so KEEP the LCD
     * assertions: the same brutal-latency SDRAM model still has to meet the
     * LCD 1-line deadline (lcd_mismatch == 0 over the steady-state frame). */
    (void)CLK_HZ;
    printf("=== scanout LCD-only (HAS_ANALOG_RASTER=0) timing + data ===\n");

    Result p480 = measure(1, 0, 0, 0);
    report("timing=480p (pruned analog; LCD deadline check)", p480);
    Result p240 = measure(0, 0, 0, 0);
    report("timing=240p (pruned analog; LCD deadline check)", p240);

    bool lcd480 = (p480.lcd_checked > 200 && p480.lcd_mismatch == 0);
    bool lcd240 = (p240.lcd_checked > 200 && p240.lcd_mismatch == 0);

    printf("\nLCD DATA (1-line deadline) 480p: %s   240p: %s\n",
           lcd480 ? "PASS" : "FAIL", lcd240 ? "PASS" : "FAIL");
    printf("ANALOG checks: SKIPPED (HAS_ANALOG_RASTER=0 — raster pruned)\n");
    bool ok = lcd480 && lcd240;
    printf("RESULT: %s\n", ok ? "PASS" : "FAIL");
    delete tb;
    return ok ? 0 : 1;
#else
    printf("=== scanout analog timing + data ===\n");

    Result p480 = measure(1, 0, 0, 0); report("timing=480p (VGA / scandoubler path)", p480);
    Result p240 = measure(0, 0, 0, 0); report("timing=240p (dedicated 15 kHz path)", p240);
    // Split: analog framebuffer offset +1000 rows vs the LCD framebuffer. The
    // analog path must show rows 1000.. while the LCD shows rows 0.. -> proves
    // the two paths read INDEPENDENT framebuffers (console-on-LCD-only basis).
    Result psplit = measure(0, 1, 0, 0);
    report("timing=240p, SPLIT fb (analog=app, LCD=other)", psplit);
    // The RGBHV case: the 480p analog path must ALSO read its own framebuffer
    // (not the LCD line buffer) so Pocket LCD = Terminal can keep the app on the
    // analog while the LCD shows the console.
    Result p480split = measure(1, 1, 0, 0);
    report("timing=480p, SPLIT fb (480p analog=app, LCD=other)", p480split);
    // RGBHV upscale: a 240-tall native app must be scaled to fill the 480p
    // analog frame (out=480). Bug = only 240 active lines (bottom half black).
    Result p480up = measure(1, 0, 1, 0);
    report("timing=480p, UPSCALE (240-tall app -> 480p analog)", p480up);
    // Interlace: 480-tall fb on the 15 kHz raster. 480i NTSC stays at the
    // 15.75 kHz line with 262.5-line fields (~60 Hz, locked to the LCD wrap);
    // 576i PAL runs the 786-clock line (15.63 kHz) with 312.5-line fields
    // (50.03 Hz, free-running). Both must alternate the half-line vsync and
    // fetch even rows in the long field / odd rows in the short field.
    Result p480i = measure(2, 0, 0, 1);
    report("timing=480i NTSC (480-tall fb, interlaced)", p480i);
    Result p576i = measure(3, 0, 0, 1);
    report("timing=576i PAL (480-tall fb, interlaced)", p576i);
    // Fetch gate: with analog_fetch_ena=0 the DUT must issue ZERO analog line
    // fetches (the whole point of the gate: no wasted SDRAM stream when the
    // Analogizer is off) while the LCD stream still renders correctly.  The
    // analog DATA check is expectedly wrong here (buffer never written) and is
    // deliberately not asserted.
    Result pgate = measure(0, 1, 0, 0, /*fetch_ena=*/0);
    report("timing=240p, FETCH GATE (analog_fetch_ena=0)", pgate);

    double l480 = p480.line_cyc>0 ? CLK_HZ/p480.line_cyc/1000.0 : 0;
    double l240 = p240.line_cyc>0 ? CLK_HZ/p240.line_cyc/1000.0 : 0;
    double f480 = p480.frame_cyc>0 ? CLK_HZ/p480.frame_cyc : 0;
    double f240 = p240.frame_cyc>0 ? CLK_HZ/p240.frame_cyc : 0;
    double l480i = p480i.line_cyc>0 ? CLK_HZ/p480i.line_cyc/1000.0 : 0;
    double f480i = p480i.frame_cyc>0 ? CLK_HZ/p480i.frame_cyc : 0;
    double l576i = p576i.line_cyc>0 ? CLK_HZ/p576i.line_cyc/1000.0 : 0;
    double f576i = p576i.frame_cyc>0 ? CLK_HZ/p576i.frame_cyc : 0;

    bool timing = nearv(l480,31.5,0.03) && nearv(p480.lines_per_frame,525,0.03) && nearv(f480,60,0.05)
               && nearv(l240,15.75,0.03) && nearv(p240.lines_per_frame,262,0.05) && nearv(f240,60,0.05);
    bool data480 = (p480.data_checked > 200 && p480.data_mismatch == 0);
    bool data240 = (p240.data_checked > 200 && p240.data_mismatch == 0);
    // The LCD must render correctly in BOTH modes. The 240p case is the real
    // test: it has the dedicated analog fetch interleaved on the burst port, so
    // a passing LCD here proves the dual-fetch does NOT starve the LCD.
    bool lcd480 = (p480.lcd_checked > 200 && p480.lcd_mismatch == 0);
    bool lcd240 = (p240.lcd_checked > 200 && p240.lcd_mismatch == 0);
    // Split scenario: analog reads its offset fb (rows 1000..), LCD reads base.
    bool split_analog = (psplit.data_checked > 200 && psplit.data_mismatch == 0);
    bool split_lcd    = (psplit.lcd_checked > 200 && psplit.lcd_mismatch == 0);
    bool split480_analog = (p480split.data_checked > 200 && p480split.data_mismatch == 0);
    bool split480_lcd    = (p480split.lcd_checked > 200 && p480split.lcd_mismatch == 0);
    // Upscale: must fill ~480 active analog lines (not 240) with the source
    // doubled. Catches the "blank in Terminal" (native app not upscaled).
    bool up_fills = (p480up.active_lines >= 460 && p480up.active_lines <= 500);
    bool up_data  = (p480up.data_checked > 400 && p480up.data_mismatch == 0);
    // 480i NTSC: 15.75 kHz line, 262.5 lines vsync-to-vsync, ~60 Hz fields,
    // 240 active lines per field. 576i PAL: 15.63 kHz, 312.5 lines, ~50 Hz.
    // lines/frame tolerance is TIGHT (0.1%): the field period must be
    // UNIFORM at N+0.5 lines — a wrong long/short-field pairing shows up as
    // 263.5/261.5 (313.5/311.5) alternation, only ~0.4% off the median.
    bool i480_timing = nearv(l480i,15.75,0.03) && nearv(p480i.lines_per_frame,262.5,0.001)
                    && nearv(f480i,60,0.05) && nearv((double)p480i.active_lines,240,0.02);
    bool i576_timing = nearv(l576i,15.634,0.03) && nearv(p576i.lines_per_frame,312.5,0.001)
                    && nearv(f576i,50,0.05) && nearv((double)p576i.active_lines,240,0.02);
    // Field structure: parities must strictly alternate and the short field's
    // vsync must sit ~H_TOTAL/2 after the line start (390px*4=1560 clk NTSC,
    // 393px*4=1572 clk PAL; the output pipeline quantizes to 4-clk steps).
    bool i480_fields = (p480i.field_seq_bad == 0)
                    && (labs(p480i.vs_off_long - 1560) <= 64)
                    && (p480i.vs_off_short >= 0 && p480i.vs_off_short <= 64);
    bool i576_fields = (p576i.field_seq_bad == 0)
                    && (labs(p576i.vs_off_long - 1572) <= 64)
                    && (p576i.vs_off_short >= 0 && p576i.vs_off_short <= 64);
    // Interlaced data: long field rows 0,2,4,.. / short field rows 1,3,5,..
    bool i480_data = (p480i.data_checked > 400 && p480i.data_mismatch == 0);
    bool i576_data = (p576i.data_checked > 400 && p576i.data_mismatch == 0);
    bool i480_lcd  = (p480i.lcd_checked > 200 && p480i.lcd_mismatch == 0);
    bool i576_lcd  = (p576i.lcd_checked > 200 && p576i.lcd_mismatch == 0);

    printf("\nTIMING : %s\n", timing ? "PASS" : "FAIL");
    printf("ANALOG DATA  480p: %s   240p: %s\n", data480 ? "PASS" : "FAIL", data240 ? "PASS" : "FAIL");
    printf("LCD    DATA  480p: %s   240p: %s   (240p = dual-fetch starvation test)\n",
           lcd480 ? "PASS" : "FAIL", lcd240 ? "PASS" : "FAIL");
    printf("SPLIT 240p   analog(rows 1000..): %s   LCD(rows 0..): %s\n",
           split_analog ? "PASS" : "FAIL", split_lcd ? "PASS" : "FAIL");
    printf("SPLIT 480p   analog(rows 1000..): %s   LCD(rows 0..): %s   (RGBHV console-on-LCD)\n",
           split480_analog ? "PASS" : "FAIL", split480_lcd ? "PASS" : "FAIL");
    printf("UPSCALE 480p fills(%ld lines): %s   data(rows k/2): %s   (RGBHV native->480p)\n",
           p480up.active_lines, up_fills ? "PASS" : "FAIL", up_data ? "PASS" : "FAIL");
    printf("480i NTSC    timing: %s   fields: %s   data(2k/2k+1): %s   LCD: %s\n",
           i480_timing ? "PASS" : "FAIL", i480_fields ? "PASS" : "FAIL",
           i480_data ? "PASS" : "FAIL", i480_lcd ? "PASS" : "FAIL");
    printf("576i PAL     timing: %s   fields: %s   data(2k/2k+1): %s   LCD: %s\n",
           i576_timing ? "PASS" : "FAIL", i576_fields ? "PASS" : "FAIL",
           i576_data ? "PASS" : "FAIL", i576_lcd ? "PASS" : "FAIL");
    bool gate_no_fetch = (pgate.fetch_reqs == 0);
    bool gate_lcd      = (pgate.lcd_checked > 200 && pgate.lcd_mismatch == 0);
    // Sanity: the same scenario with the gate open must actually fetch.
    bool gate_ctrl     = (psplit.fetch_reqs > 200);
    printf("FETCH GATE   ena=0 fetches(%d): %s   LCD: %s   ena=1 control(%d): %s\n",
           pgate.fetch_reqs, gate_no_fetch ? "PASS" : "FAIL",
           gate_lcd ? "PASS" : "FAIL",
           psplit.fetch_reqs, gate_ctrl ? "PASS" : "FAIL");
    bool ok = timing && data480 && data240 && lcd480 && lcd240 &&
              gate_no_fetch && gate_lcd && gate_ctrl &&
              split_analog && split_lcd && split480_analog && split480_lcd &&
              up_fills && up_data &&
              i480_timing && i480_fields && i480_data && i480_lcd &&
              i576_timing && i576_fields && i576_data && i576_lcd;
    printf("RESULT: %s\n", ok ? "PASS" : "FAIL");
    delete tb;
    return ok ? 0 : 1;
#endif
}
