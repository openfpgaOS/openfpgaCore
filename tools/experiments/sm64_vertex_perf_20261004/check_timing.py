#!/usr/bin/env python3
"""Test the real snapshot accounting with a synthetic hardware clock."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/sm64-vertex-perf-20261004'
SOURCE=OUT/'SM64/src/sm64/pocket/perf_pocket.c'
source=SOURCE.read_text()
logic=source[source.index('struct PerfWindow {'):source.index('static void pair_line(')]
pre=r'''
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <assert.h>
#include <stddef.h>
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
    sm64_perf_render_begin(tick);
    tick+=200000; /* miscellaneous frame work: 2ms */
    {SM64_PERF_SCOPE(SM64_PERF_WAIT);tick+=1000000;}
    {SM64_PERF_SCOPE(SM64_PERF_DL);tick+=400000;
        {SM64_PERF_SCOPE(SM64_PERF_TRI);tick+=300000;
            {SM64_PERF_SCOPE(SM64_PERF_VTX);tick+=20000;
                {SM64_PERF_SCOPE(SM64_PERF_VTX_MATRIX);tick+=40000;}
                {SM64_PERF_SCOPE(SM64_PERF_VTX_PREP);tick+=50000;
                    {SM64_PERF_SCOPE(SM64_PERF_VTX_MATRIX);tick+=10000;}}
                {SM64_PERF_SCOPE(SM64_PERF_VTX_ATTR);tick+=30000;}
                {SM64_PERF_SCOPE(SM64_PERF_VTX_CPU);tick+=50000;}
                SM64_PERF_COUNT(SM64_PERF_RAW,32);
                SM64_PERF_COUNT(SM64_PERF_CPU,7);
                SM64_PERF_COUNT(SM64_PERF_OUT,11);
                SM64_PERF_COUNT(SM64_PERF_RECOVER,2);
            }
            {SM64_PERF_SCOPE(SM64_PERF_MAT);tick+=100000;
                {SM64_PERF_SCOPE(SM64_PERF_TEX);tick+=200000;
                    {SM64_PERF_SCOPE(SM64_PERF_SYNC);tick+=100000;}}}
            {SM64_PERF_SCOPE(SM64_PERF_SEND);tick+=100000;
                {SM64_PERF_SCOPE(SM64_PERF_SEND);tick+=100000;}
                tick+=100000;}}}
    assert(tick==start+3600000);
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
    sm64_perf_detail_active=detail_serial=0;
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
    const uint32_t expected[]={40,20,30,10,20,30,10,20,100};
    assert(s->version==3 && s->samples>=5 && s->samples<=6);
    assert(s->sample_render_ms10==180);
    for(unsigned i=0;i<SM64_PERF_LEGACY_BUCKETS;i++)assert(s->detail_ms10[i]==expected[i]);
    const uint32_t vertex[]={5,5,3,5,2},counts[]={32,7,11,2};
    assert(offsetof(struct PerfSnapshot,vertex_ms10)==120);
    assert(sizeof(struct PerfSnapshot)==39*4);
    for(unsigned i=0;i<SM64_PERF_VERTEX_PARTS;i++)assert(s->vertex_ms10[i]==vertex[i]);
    for(unsigned i=0;i<SM64_PERF_VERTEX_COUNTS;i++)assert(s->vertex_counts[i]==counts[i]);
    assert(!sm64_perf_detail_active);
    assert(s->ring_spins==120 && s->geometry==1 && s->vertices==32 && s->triangles==12);
}
static int scope_return(void) {
    SM64_PERF_SCOPE(SM64_PERF_TEX);
    tick+=250;
    return 7;
}
static void cleanup_test(void) {
    reset(0xfffffff0u);warmup=0;last_level=gCurrLevelNum;
    sm64_perf_step_begin();sm64_perf_render_begin(tick);
    assert(sm64_perf_detail_active);
    uint32_t begin=tick;
    {SM64_PERF_SCOPE(SM64_PERF_TRI);
        assert(scope_return()==7 && sm64_perf_detail.bucket==SM64_PERF_TRI);
        for(unsigned i=0;i<3;i++) {SM64_PERF_SCOPE(SM64_PERF_SEND);tick+=10;if(i==1)break;}
        assert(sm64_perf_detail.bucket==SM64_PERF_TRI);
        {SM64_PERF_SCOPE(SM64_PERF_VTX);tick+=25;goto done;}
    }
done:
    assert(sm64_perf_detail.bucket==SM64_PERF_MISC);
    sm64_perf_render_end(begin,tick);
    assert(window.samples==1 && window.detail[SM64_PERF_TEX]==250
        && window.detail[SM64_PERF_SEND]==20 && window.detail[SM64_PERF_VTX]==25);
    uint64_t total=0;
    for(unsigned i=0;i<SM64_PERF_BUCKETS;i++)total+=window.detail[i];
    assert(total==(uint32_t)(tick-begin));
    for(unsigned i=0;i<7;i++) {
        sm64_perf_render_begin(tick);assert(!sm64_perf_detail_active);
        assert(scope_return()==7);
        sm64_perf_render_end(tick,tick);
    }
    assert(window.samples==1);
    sm64_perf_render_begin(tick);assert(sm64_perf_detail_active);
    sm64_perf_render_end(tick,tick);
    assert(window.samples==2);
}
static void no_samples_test(void) {
    reset(100);warmup=0;last_level=gCurrLevelNum;
    uint32_t start=sm64_perf_step_begin();tick=start+200000000u;
    sm64_perf_render_end(start,tick);
    sm64_perf_step_end(start,tick,tick,tick);
    assert(sm64_perf_snapshot.valid && sm64_perf_snapshot.samples==0);
    assert(sm64_perf_snapshot.detail_ms10[SM64_PERF_VTX]==0);
    for(unsigned i=0;i<SM64_PERF_VERTEX_PARTS;i++)assert(sm64_perf_snapshot.vertex_ms10[i]==0);
    for(unsigned i=0;i<SM64_PERF_VERTEX_COUNTS;i++)assert(sm64_perf_snapshot.vertex_counts[i]==0);
}
int main(void) {
    no_samples_test();
    cleanup_test();
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
    puts("PASS: P3 vertex partition, sampled route counts, P2 prefix ABI, zero-sample windows, frame/step/display rates, exclusive nested buckets, 1/8 sampling, early-return/break/goto cleanup, counter wrap, catch-up steps, frozen panel, input chord and pause rejection");
}
'''
c=OUT/'test-perf-timing.c';exe=OUT/'test-perf-timing'
header=(SOURCE.parent/'perf_pocket.h').read_text()
assert header.count('return *(volatile uint32_t *)0x40000004;')==1
header=header.replace('return *(volatile uint32_t *)0x40000004;','return tick;')
c.write_text(pre+'\n#define SM64_PERF_DIAG 1\n'+header+logic+test)
cmd=['cc','-std=c99','-O2','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer',str(c),'-o',str(exe)]
subprocess.run(cmd,check=True)
r=subprocess.run([str(exe)],capture_output=True,text=True,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'})
print(r.stdout,r.stderr,end='');r.check_returncode()
(OUT/'timing-tests.json').write_text(json.dumps(dict(result=r.stdout.strip(),command=cmd,
    source_sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(), header_sha256=hashlib.sha256((SOURCE.parent/'perf_pocket.h').read_bytes()).hexdigest()),indent=2)+'\n')
