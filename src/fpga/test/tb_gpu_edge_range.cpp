// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
// Analytic coverage over the complete signed-16 coordinate input range.
// The oracle uses edge equations, not the RTL's divider or DDA algorithm.
#include "Vgpu_edge_walker.h"
#include "verilated.h"
#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <random>
#include <vector>

struct Point { int x, y; };
using Tri = std::array<Point, 3>;
struct Rect { int x, y, w, h; };
static Vgpu_edge_walker dut;
static uint64_t cycles, checked, uncertain, errors, triangles, pixels;

static void tick() {
    dut.clk = 0; dut.eval(); dut.clk = 1; dut.eval(); ++cycles;
}
static int64_t edge(Point a, Point b, Point p) {
    return int64_t(b.x-a.x)*(p.y-a.y)-int64_t(b.y-a.y)*(p.x-a.x);
}
static void fatal(const char *message) {
    std::fprintf(stderr, "%s\n", message); std::exit(2);
}

// 2 means a mathematical boundary or the small, explicitly bounded band
// affected by the existing Q16.16 slope quantization. For an edge starting
// at ya, the maximum X error is (sample_y-ya)/65536 pixels in integer-Y
// mode, or (sample_y-ya)/1048576 + 1/65536 in Q4-Y mode. The latter also
// allows the prestep product's four discarded fractional bits. No fixed
// pixel tolerance: tall integer triangles get their actual precision bound.
static int coverage(Tri t, int x, int y, bool subpix) {
    auto area = edge(t[0], t[1], t[2]);
    if (!area) return 0;
    if (area < 0) std::swap(t[1], t[2]);
    Point p{x*16+(subpix?8:0), subpix?y*16+8:y};
    bool outside = false, boundary = false;
    for (unsigned i=0; i<3; ++i) {
        Point a=t[i], b=t[(i+1)%3];
        auto e=edge(a,b,p);
        int64_t dy=std::abs(b.y-a.y);
        int64_t elapsed=std::max(0,p.y-std::min(a.y,b.y));
        int64_t band=dy*(elapsed+(subpix?16:0));
        bool near=std::abs(e)*(subpix?65536:4096)<=band;
        boundary |= near;
        if (e<0 && !near) outside=true;
    }
    if (outside) return 0;
    return boundary?2:1;
}

static std::vector<unsigned> draw(Tri t, Rect r, bool subpix) {
    std::vector<unsigned> fb(r.w*r.h);
    dut.subpix_y=subpix;
    dut.clip_x0=r.x; dut.clip_x1=r.x+r.w;
    dut.clip_y0=r.y; dut.clip_y1=r.y+r.h;
    dut.v0_x=t[0].x; dut.v0_y=t[0].y;
    dut.v1_x=t[1].x; dut.v1_y=t[1].y;
    dut.v2_x=t[2].x; dut.v2_y=t[2].y;
    dut.start=1; tick(); dut.start=0;
    bool stalled=false;
    int held_x=0, held_y=0, held_count=0;
    for (unsigned n=0; n<100000; ++n) {
        dut.rec_ready=(cycles%11)>3; dut.eval();
        if (stalled && (!dut.rec_valid || int16_t(dut.rec_u)!=held_x ||
                int16_t(dut.rec_v)!=held_y || dut.rec_count!=held_count))
            fatal("Record changed under backpressure");
        stalled=dut.rec_valid && !dut.rec_ready;
        if (dut.rec_valid) {
            held_x=int16_t(dut.rec_u); held_y=int16_t(dut.rec_v);
            held_count=dut.rec_count;
        }
        if (dut.rec_valid && dut.rec_ready) {
            int x=int16_t(dut.rec_u), y=int16_t(dut.rec_v), count=dut.rec_count;
            if (!count || x<r.x || y<r.y || x+count>r.x+r.w || y>=r.y+r.h)
                fatal("Span outside scissor");
            for (int j=0; j<count; ++j) {
                if (++fb[(y-r.y)*r.w+x-r.x+j]>1) fatal("Duplicate span");
                ++pixels;
            }
        }
        tick();
        if (!dut.busy) {
            if (dut.rec_valid) fatal("Valid record after completion");
            return fb;
        }
    }
    fatal("Walker timeout"); return {};
}

static void check(Tri t, Rect r, bool subpix, bool permutations=true) {
    std::vector<unsigned> first;
    for (unsigned order=0; order<(permutations?6u:1u); ++order) {
        auto got=draw(t,r,subpix); ++triangles;
        if (!order) first=got;
        else if (first!=got) fatal("Vertex order changed coverage");
        for (int y=0; y<r.h; ++y) for (int x=0; x<r.w; ++x) {
            int ref=coverage(t,x+r.x,y+r.y,subpix);
            if (ref==2) { ++uncertain; continue; }
            ++checked;
            if (got[y*r.w+x]!=unsigned(ref)) {
                if (errors<12) std::printf(
                    "Range mismatch subpix=%d tri=(%d,%d),(%d,%d),(%d,%d) "
                    "pixel=(%d,%d) got=%u expected=%d\n",subpix,
                    t[0].x,t[0].y,t[1].x,t[1].y,t[2].x,t[2].y,
                    x+r.x,y+r.y,got[y*r.w+x],ref);
                ++errors;
            }
        }
        if (order==2) std::swap(t[0],t[1]);
        else std::rotate(t.begin(),t.begin()+1,t.end());
    }
}

int main(int argc, char **argv) {
    Verilated::commandArgs(argc,argv);
    dut.reset_n=0; dut.__SYM__abort=0; dut.start=0; dut.rec_ready=1;
    tick(); tick(); dut.reset_n=1; tick();
    Rect screen{0,0,64,64};
    // Reported Quake2/SM64 repro, including its exact full-screen pixel count.
    Tri repro{{{-1600,-6400},{800,1600},{7680,9600}}};
    auto got=draw(repro,{0,0,320,240},true);
    unsigned count=0; for (auto n:got) count+=n;
    if (count!=29457) {
        std::printf("Handoff coverage: %u pixels, expected 29457\n",count); ++errors;
    }
    check(repro,{0,0,320,240},true);
    for (bool subpix:{false,true}) {
        // Around the old 13-bit divisor wrap and the signed-16 winding wrap.
        for (int dy:{8191,8192,8193,16383,16384,16385,32767,32768,32769,65534,65535}) {
            int lo=-dy/2, hi=lo+dy;
            if (hi>32767) { --lo; --hi; }
            check(Tri{{{-1600,lo},{4800,0},{-2400,hi}}},screen,subpix);
            check(Tri{{{4800,lo},{-1600,0},{6400,hi}}},screen,subpix);
        }
        // Prestep from the extreme negative endpoint and into the bottom half.
        for (Rect r:{screen,Rect{-32,-32,64,64},Rect{0,240,64,64},
                     Rect{1950,1950,64,64},Rect{-2048,-2048,64,64}}) {
            check(Tri{{{-32768,-32768},{32767,32767},{32767,-32768}}},r,subpix);
            check(Tri{{{-32768,-32768},{32767,32767},{-32768,32767}}},r,subpix);
            check(Tri{{{-32768,-32768},{32767,-1000},{0,32767}}},r,subpix);
        }
        // Horizontal, vertical and zero-area edges.
        for (Tri t:{Tri{{{0,0},{1024,0},{512,0}}},
                    Tri{{{512,-32768},{512,0},{512,32767}}},
                    Tri{{{-32768,-32768},{-32768,-32768},{32767,32767}}},
                    Tri{{{-1600,0},{4800,0},{800,subpix?1024:64}}}})
            check(t,screen,subpix);
    }
    // Extreme slopes whose scanline starts lie inside a subpixel edge.
    // Saturating a slope to +/-1024 pixels/line moves visible intersections.
    for (int dy:{1,2,3,4,8,15,16,17,31,32,63,64}) {
        for (int direction:{-1,1}) {
            int left=direction<0?32767:-32768, right=direction<0?-32768:32767;
            check(Tri{{{left,7},{right,7+dy},{0,1024}}},screen,true);
            check(Tri{{{left,-1024},{right,7},{left,7+dy}}},screen,true);
        }
    }
    // Full integer-Y endpoint range with a scissor near its positive end.
    check(Tri{{{-32768,-32768},{32767,32767},{-32768,32767}}},
          {0,32000,64,64},false);
    std::mt19937 random(0x81923277);
    std::uniform_int_distribution<int> coordinate(-32768,32767);
    for (bool subpix:{false,true}) for (unsigned n=0; n<600; ++n) {
        Tri t; for (auto &p:t) p={coordinate(random),coordinate(random)};
        check(t,(n%3)?screen:Rect{-32,-32,64,64},subpix,false);
    }
    // Reset/abort while a large triangle is holding a span.
    dut.v0_x=-1600; dut.v0_y=-6400; dut.v1_x=800; dut.v1_y=1600;
    dut.v2_x=7680; dut.v2_y=9600; dut.subpix_y=1;
    dut.clip_x0=0; dut.clip_x1=64; dut.clip_y0=0; dut.clip_y1=64;
    dut.rec_ready=0; dut.start=1; tick(); dut.start=0;
    for (unsigned n=0; !dut.rec_valid && n<1000; ++n) tick();
    if (!dut.rec_valid) fatal("Abort test failed to reach stalled record");
    dut.__SYM__abort=1; tick(); dut.__SYM__abort=0;
    if (dut.busy || dut.rec_valid) fatal("Abort failed to clear walker");
    check(repro,screen,true);
    std::printf("Range coverage: %llu triangles, %llu checked pixels, "
                "%llu boundary/quantization pixels, %llu mismatches; "
                "%llu cycles, %llu emitted pixels\n",
                (unsigned long long)triangles,(unsigned long long)checked,
                (unsigned long long)uncertain,(unsigned long long)errors,
                (unsigned long long)cycles,(unsigned long long)pixels);
    return errors?1:0;
}
