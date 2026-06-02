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
    int data_mismatch;     // # active lines whose shown row != expected
    int data_checked;      // # active lines compared
};

static Result measure(int mode_480p) {
    const uint64_t RUN = 6000000ULL;
    tb->analog_480p = mode_480p;
    tb->reset_n = 0;
    for (int i = 0; i < 40; i++) tick();
    tb->reset_n = 1;
    for (int i = 0; i < 10; i++) tick();

    std::vector<uint64_t> hs_edges, vs_edges;
    std::vector<long> active_per_frame;
    int prev_hs = 0, prev_vs = 0, prev_vb = 1;
    long active_lines = 0;

    // data capture for a single settled frame
    const int MAXL = 320;
    std::vector<int> src_by_line(MAXL, -1);
    int frames_seen = 0;
    int cap_line = -1;             // active-line index within the captured frame
    bool capturing = false;

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
    r.data_mismatch = 0; r.data_checked = 0;
    for (int k = 0; k < nl; k++) {
        if (src_by_line[k] < 0) continue;
        r.data_checked++;
        if (src_by_line[k] != k) r.data_mismatch++;
    }
    return r;
}

static void report(const char *name, const Result &r) {
    const double CLK_HZ = 49.152e6;
    printf("--- %s ---\n", name);
    printf("  line  = %ld clk -> %.2f kHz\n", r.line_cyc, r.line_cyc>0?CLK_HZ/r.line_cyc/1000.0:0);
    printf("  frame = %ld clk -> %.2f Hz\n", r.frame_cyc, r.frame_cyc>0?CLK_HZ/r.frame_cyc:0);
    printf("  lines/frame = %.1f, active lines = %ld\n", r.lines_per_frame, r.active_lines);
    printf("  data: %d/%d active lines show the WRONG source row\n",
           r.data_mismatch, r.data_checked);
}

static bool nearv(double v, double t, double tol) { return v >= t*(1-tol) && v <= t*(1+tol); }

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    tb = new Vtb_scanout;
    const double CLK_HZ = 49.152e6;
    printf("=== scanout analog timing + data ===\n");

    Result p480 = measure(1); report("analog_480p=1 (VGA / scandoubler path)", p480);
    Result p240 = measure(0); report("analog_480p=0 (dedicated 240p / 15 kHz path)", p240);

    double l480 = p480.line_cyc>0 ? CLK_HZ/p480.line_cyc/1000.0 : 0;
    double l240 = p240.line_cyc>0 ? CLK_HZ/p240.line_cyc/1000.0 : 0;
    double f480 = p480.frame_cyc>0 ? CLK_HZ/p480.frame_cyc : 0;
    double f240 = p240.frame_cyc>0 ? CLK_HZ/p240.frame_cyc : 0;

    bool timing = nearv(l480,31.5,0.03) && nearv(p480.lines_per_frame,525,0.03) && nearv(f480,60,0.05)
               && nearv(l240,15.75,0.03) && nearv(p240.lines_per_frame,262,0.05) && nearv(f240,60,0.05);
    bool data480 = (p480.data_checked > 200 && p480.data_mismatch == 0);
    bool data240 = (p240.data_checked > 200 && p240.data_mismatch == 0);

    printf("\nTIMING : %s\n", timing ? "PASS" : "FAIL");
    printf("DATA 480p: %s   DATA 240p: %s\n", data480 ? "PASS" : "FAIL", data240 ? "PASS" : "FAIL");
    bool ok = timing && data480 && data240;
    printf("RESULT: %s\n", ok ? "PASS" : "FAIL");
    delete tb;
    return ok ? 0 : 1;
}
