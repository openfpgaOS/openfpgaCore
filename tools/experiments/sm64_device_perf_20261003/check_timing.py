#!/usr/bin/env python3
"""Test the real snapshot accounting with a synthetic hardware clock."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/sm64-device-perf-20261003'
SOURCE=OUT/'SM64/src/sm64/pocket/perf_pocket.c'
source=SOURCE.read_text()
logic=source[source.index('struct PerfWindow {'):source.index('static void pair_line(')]
pre=r'''
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <assert.h>
#define OF_BTN_SELECT (1u<<14)
#define OF_BTN_L1 (1u<<8)
#define OF_BTN_R1 (1u<<9)
static uint32_t tick,presented,acquire_ticks,ring_spins;
static int gCurrLevelNum=9;
static unsigned sm64_geometry_vertices,sm64_geometry_triangles;
struct of_capabilities {uint32_t cpu_freq_hz;};
static struct of_capabilities caps={100000000};
static const struct of_capabilities *of_get_caps(void) {return &caps;}
typedef struct {uint32_t present_count;} of_video_timing_t;
static void of_video_get_timing(of_video_timing_t *t) {t->present_count=presented;}
static uint32_t sm64_perf_clock(void) {return tick;}
static int gfx_gpu_geometry_enabled(void) {return 1;}
static void gpu_ring_prof_get(unsigned *s,unsigned *f) {*s=ring_spins;*f=12;}
static void gpu_present_prof_get(unsigned *f,unsigned *a) {*f=1000;*a=acquire_ticks;}
'''
test=r'''
static void frame(unsigned catchup) {
    uint32_t start=sm64_perf_step_begin();
    tick=start+800000;
    acquire_ticks=1000000;ring_spins=120;
    sm64_geometry_vertices+=32;sm64_geometry_triangles+=12;
    tick=start+3600000;
    sm64_perf_render_end(start+800000,tick);
    presented++;
    sm64_perf_step_end(start,start+4000000,start+4200000,start+4300000);
    if(catchup) {
        tick=start+4400000;uint32_t dropped=sm64_perf_step_begin();
        tick=start+4800000;
        sm64_perf_step_end(dropped,dropped+200000,dropped+300000,tick);
    }
    tick=start+5000000;
}
static void reset(uint32_t start) {
    memset(&window,0,sizeof(window));memset(&sm64_perf_snapshot,0,sizeof(sm64_perf_snapshot));
    tick=start;tick_hz=0;step_render=step_frames=0;
    window_active=visible=chord_held=0;warmup=2;last_level=-1;
    presented=0;sm64_geometry_vertices=0xfffffc00u;sm64_geometry_triangles=0xffffff00u;
}
static void check(void) {
    const struct PerfSnapshot *s=&sm64_perf_snapshot;
    assert(s->valid && s->clock_hz==100000000 && s->level==9);
    assert(s->render_fps10>=195 && s->render_fps10<=205);
    assert(s->display_fps10==s->render_fps10);
    assert(s->sim_fps10>=290 && s->sim_fps10<=310);
    assert(s->game_ms10>=129 && s->game_ms10<=131);
    assert(s->audio_ms10>=24 && s->audio_ms10<=26);
    assert(s->render_ms10==180 && s->wait_ms10==100);
    assert(s->gap_ms10>=48 && s->gap_ms10<=52);
    assert(s->ring_spins==120 && s->geometry==1 && s->vertices==32 && s->triangles==12);
}
int main(void) {
    const uint32_t chord=OF_BTN_SELECT|OF_BTN_L1|OF_BTN_R1;
    for(unsigned trial=0;trial<2;trial++) {
        reset(trial?0xff000000u:100);
        for(unsigned i=0;i<90;i++)frame(i&1);
        check();
        struct PerfSnapshot saved=sm64_perf_snapshot;
        assert(sm64_perf_input(1u<<4)==(1u<<4));
        assert(sm64_perf_input(chord|(1u<<4))==(1u<<4) && visible);
        assert(sm64_perf_input(chord)==0 && visible); /* held: no repeat */
        assert(sm64_perf_input(OF_BTN_SELECT|OF_BTN_R1)==0 && visible);
        assert(sm64_perf_input(OF_BTN_R1)==0 && visible);
        sm64_perf_input(0);
        for(unsigned i=0;i<90;i++)frame(i&1);
        assert(memcmp(&saved,&sm64_perf_snapshot,sizeof(saved))==0);
        assert(sm64_perf_input(chord)==0 && !visible);sm64_perf_input(0);
        for(unsigned i=0;i<90;i++)frame(i&1);
        check();
        saved=sm64_perf_snapshot;
        /* A long paused window is discarded, without replacing good data. */
        window_active=0;warmup=0;
        uint32_t start=sm64_perf_step_begin();
        tick=start+tick_hz*5u;
        sm64_perf_render_end(start,tick);
        sm64_perf_step_end(start,tick,tick,tick);
        assert(!window_active && warmup==2);
        assert(memcmp(&saved,&sm64_perf_snapshot,sizeof(saved))==0);
    }
    puts("PASS: frame/step/display rates, exclusive timing buckets, counter wrap, catch-up steps, frozen panel, input chord and pause rejection");
}
'''
c=OUT/'test-perf-timing.c';exe=OUT/'test-perf-timing'
c.write_text(pre+logic+test)
cmd=['cc','-std=c99','-O2','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer',str(c),'-o',str(exe)]
subprocess.run(cmd,check=True)
r=subprocess.run([str(exe)],capture_output=True,text=True,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'})
print(r.stdout,r.stderr,end='');r.check_returncode()
(OUT/'timing-tests.json').write_text(json.dumps(dict(result=r.stdout.strip(),command=cmd,
    source_sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest()),indent=2)+'\n')
