//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * Verilator C++ harness for tb_scanout — checks analog TIMING and DATA.
 *
 * Timing: analog_480p=1 must stay ~31.5 kHz / 525 lines (VGA path, preserved);
 *         analog_480p=0 must be ~15.75 kHz / ~262 lines (dedicated 240p path).
 * Data:   each analog active line must show the correct framebuffer source row.
 *         The SDRAM model encodes source-row R into every RGB565 pixel of row
 *         R, so the decoded analog color is recovered to R in C++. For this
 *         320x240 / out_height=240 config, analog active line A must show row A.
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
};

static Result measure(int mode_480p, int analog_split, int analog_upscale) {
    const uint64_t RUN = 6000000ULL;
    tb->analog_480p = mode_480p;
    tb->analog_split = analog_split;
    tb->analog_upscale = analog_upscale;
    tb->reset_n = 0;
    for (int i = 0; i < 40; i++) tick();
    tb->reset_n = 1;
    for (int i = 0; i < 10; i++) tick();

    std::vector<uint64_t> hs_edges, vs_edges;
    std::vector<long> active_per_frame;
    int prev_hs = 0, prev_vs = 0, prev_vb = 1;
    long active_lines = 0;

    // data capture for a single settled frame (>=480 for the 480p upscale case)
    const int MAXL = 512;
    std::vector<int> src_by_line(MAXL, -1);
    int frames_seen = 0;
    int cap_line = -1;             // active-line index within the captured frame
    bool capturing = false;

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

        if (!prev_hs && hs) { hs_edges.push_back(cycles); if (!vb) active_lines++; }
        if (!prev_vs && vs) {
            vs_edges.push_back(cycles);
            frames_seen++;
            if (frames_seen == 4) { capturing = true; cap_line = -1; }
            else if (frames_seen == 5) { capturing = false; }
        }
        if (!prev_vb && vb) { active_per_frame.push_back(active_lines); active_lines = 0; }

        if (capturing) {
            if (!prev_hs && hs && !vb) cap_line++;     // new active line
            if (!hb && !vb && cap_line >= 0 && cap_line < MAXL)
                src_by_line[cap_line] = rgb565_src(col);   // last sample in line
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
    // expected: active line k shows source row k (320x240, out_height 240)
    int nl = (r.active_lines > 0 && r.active_lines < MAXL) ? (int)r.active_lines : 240;
    // When split, the analog framebuffer is offset by +1000 rows, so analog
    // active line k must show source row k+1000 (proving it reads its OWN fb).
    // When upscaling (out 480 from a 240-tall source), active line k shows
    // source row k/2 (the analog vertical scaler doubles each row to fill 480p).
    int aoff = analog_split ? 1000 : 0;
    if (analog_upscale) nl = (r.active_lines > 0 && r.active_lines < MAXL) ? (int)r.active_lines : 480;
    r.data_mismatch = 0; r.data_checked = 0;
    for (int k = 0; k < nl; k++) {
        if (src_by_line[k] < 0) continue;
        r.data_checked++;
        int expected = (analog_upscale ? k / 2 : k) + aoff;
        if (src_by_line[k] != expected) r.data_mismatch++;
    }
    // LCD steady-state check. Skip line 0 (its expected src 0 aliases a black
    // starved line, so it can't distinguish correct from starved).
    r.lcd_mismatch = 0; r.lcd_checked = 0;
    for (int k = 1; k < 240; k++) {
        if (lcd_src_by_line[k] < 0) continue;
        r.lcd_checked++;
        if (lcd_src_by_line[k] != k) r.lcd_mismatch++;
    }
    return r;
}

static void report(const char *name, const Result &r) {
    const double CLK_HZ = 49.152e6;
    printf("--- %s ---\n", name);
    printf("  line  = %ld clk -> %.2f kHz\n", r.line_cyc, r.line_cyc>0?CLK_HZ/r.line_cyc/1000.0:0);
    printf("  frame = %ld clk -> %.2f Hz\n", r.frame_cyc, r.frame_cyc>0?CLK_HZ/r.frame_cyc:0);
    printf("  lines/frame = %.1f, active lines = %ld\n", r.lines_per_frame, r.active_lines);
    printf("  analog data: %d/%d active lines show the WRONG source row\n",
           r.data_mismatch, r.data_checked);
    printf("  LCD    data: %d/%d active lines show the WRONG source row\n",
           r.lcd_mismatch, r.lcd_checked);
}

static bool nearv(double v, double t, double tol) { return v >= t*(1-tol) && v <= t*(1+tol); }

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    tb = new Vtb_scanout;
    const double CLK_HZ = 49.152e6;
    printf("=== scanout analog timing + data ===\n");

    Result p480 = measure(1, 0, 0); report("analog_480p=1 (VGA / scandoubler path)", p480);
    Result p240 = measure(0, 0, 0); report("analog_480p=0 (dedicated 240p / 15 kHz path)", p240);
    // Split: analog framebuffer offset +1000 rows vs the LCD framebuffer. The
    // analog path must show rows 1000.. while the LCD shows rows 0.. -> proves
    // the two paths read INDEPENDENT framebuffers (console-on-LCD-only basis).
    Result psplit = measure(0, 1, 0);
    report("analog_480p=0, SPLIT fb (analog=app, LCD=other)", psplit);
    // The RGBHV case: the 480p analog path must ALSO read its own framebuffer
    // (not the LCD line buffer) so Pocket LCD = Terminal can keep the app on the
    // analog while the LCD shows the console.
    Result p480split = measure(1, 1, 0);
    report("analog_480p=1, SPLIT fb (480p analog=app, LCD=other)", p480split);
    // RGBHV upscale: a 240-tall native app must be scaled to fill the 480p
    // analog frame (out=480). Bug = only 240 active lines (bottom half black).
    Result p480up = measure(1, 0, 1);
    report("analog_480p=1, UPSCALE (240-tall app -> 480p analog)", p480up);

    double l480 = p480.line_cyc>0 ? CLK_HZ/p480.line_cyc/1000.0 : 0;
    double l240 = p240.line_cyc>0 ? CLK_HZ/p240.line_cyc/1000.0 : 0;
    double f480 = p480.frame_cyc>0 ? CLK_HZ/p480.frame_cyc : 0;
    double f240 = p240.frame_cyc>0 ? CLK_HZ/p240.frame_cyc : 0;

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
    bool ok = timing && data480 && data240 && lcd480 && lcd240 &&
              split_analog && split_lcd && split480_analog && split480_lcd &&
              up_fills && up_data;
    printf("RESULT: %s\n", ok ? "PASS" : "FAIL");
    delete tb;
    return ok ? 0 : 1;
}
