#!/usr/bin/env python3
"""Apply P2 only to the private, hashed copy of the current SM64 client."""
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/sm64-render-perf-20261004'
STAGE = OUT / 'SM64'
before = json.loads((OUT / 'before.json').read_text())

def read(rel):
    p = STAGE / rel
    assert hashlib.sha256(p.read_bytes()).hexdigest() == before[rel], rel
    return p, p.read_text()

def replace(s, old, new):
    assert s.count(old) == 1, old
    return s.replace(old, new)

def scope(s, function, bucket):
    pattern = r'(^[^\n;{}]*\b' + function + r'\([^;{}]*\)\s*\{)'
    s, n = re.subn(pattern, r'\1\n    SM64_PERF_SCOPE(SM64_PERF_' + bucket + ');', s, flags=re.M)
    assert n == 1, (function, n)
    return s

p, s = read('src/sm64/pocket/perf_pocket.h')
s = replace(s, '#if SM64_PERF_DIAG\n', '''/* Exclusive CPU wall-time buckets. SEND includes backend conversion,
 * command packing, MMIO uploads and any ring stalls; it is not GPU busy time.
 * TEX includes SDRAM publication; SYNC isolates its reuse fence. */
enum Sm64PerfBucket {
    SM64_PERF_DL, SM64_PERF_VTX, SM64_PERF_TRI, SM64_PERF_MAT,
    SM64_PERF_TEX, SM64_PERF_SEND, SM64_PERF_SYNC, SM64_PERF_MISC,
    SM64_PERF_WAIT, SM64_PERF_BUCKETS
};

#if SM64_PERF_DIAG
''')
s = replace(s, 'uint32_t sm64_perf_step_begin(void);', '''extern unsigned sm64_perf_detail_active;
struct Sm64PerfDetail {
    unsigned bucket,depth,parents[32];
    uint32_t stamp,ticks[SM64_PERF_BUCKETS];
};
extern struct Sm64PerfDetail sm64_perf_detail;
static inline __attribute__((always_inline)) void sm64_perf_enter(unsigned bucket) {
    /* No tokens stay live in the renderer's hot functions. This avoids
     * register spills even on the seven frames that are not sampled. */
    if(sm64_perf_detail.depth==32) {sm64_perf_detail_active=0;return;}
    unsigned previous=sm64_perf_detail.bucket;
    sm64_perf_detail.parents[sm64_perf_detail.depth++]=previous;
    if(previous==bucket)return;
    uint32_t now=sm64_perf_clock();
    sm64_perf_detail.ticks[previous]+=now-sm64_perf_detail.stamp;
    sm64_perf_detail.stamp=now;sm64_perf_detail.bucket=bucket;
}

static inline __attribute__((always_inline)) void sm64_perf_leave(void) {
    if(!sm64_perf_detail.depth) {sm64_perf_detail_active=0;return;}
    unsigned previous=sm64_perf_detail.parents[--sm64_perf_detail.depth];
    if(previous==sm64_perf_detail.bucket)return;
    uint32_t now=sm64_perf_clock();
    sm64_perf_detail.ticks[sm64_perf_detail.bucket]+=now-sm64_perf_detail.stamp;
    sm64_perf_detail.stamp=now;sm64_perf_detail.bucket=previous;
}


void sm64_perf_render_begin(uint32_t begin);

static inline unsigned sm64_perf_scope_begin(unsigned bucket) {
    if(__builtin_expect(sm64_perf_detail_active,0))sm64_perf_enter(bucket);
    return 0;
}
static inline void sm64_perf_scope_end(unsigned *unused) {
    (void)unused;
    if(__builtin_expect(sm64_perf_detail_active,0))sm64_perf_leave();
}
/* GCC cleanup restores the parent on every return/break/goto leaving scope.
 * Same-bucket recursion is free of timer reads. Inactive frames only test a
 * flag; PERF_DIAG=0 removes even that test. Use once per lexical scope. */
#define SM64_PERF_SCOPE(bucket) \\
    unsigned sm64_perf_unused __attribute__((cleanup(sm64_perf_scope_end))) = \\
        sm64_perf_scope_begin(bucket)

uint32_t sm64_perf_step_begin(void);''')
s = replace(s, 'static inline uint32_t sm64_perf_clock(void) { return 0; }', '''#define SM64_PERF_SCOPE(bucket) ((void)0)
static inline void sm64_perf_render_begin(uint32_t begin) { (void)begin; }
static inline uint32_t sm64_perf_clock(void) { return 0; }''')
p.write_text(s)

p, s = read('src/sm64/pocket/perf_pocket.c')
s = replace(s, '    uint64_t game,render,audio,wait,tail,ring;', '''    uint64_t game,render,audio,wait,tail,ring;
    uint64_t detail[SM64_PERF_BUCKETS];
    uint32_t samples;''')
s = replace(s, '    uint32_t ring_spins,geometry,vertices,triangles;', '''    uint32_t ring_spins,geometry,vertices,triangles;
    /* Append only: the original P1 fields keep their capture ABI. */
    uint32_t version,samples,sample_render_ms10;
    uint32_t detail_ms10[SM64_PERF_BUCKETS];''')
s = replace(s, 'static uint32_t perf_hz(void) {', '''unsigned sm64_perf_detail_active;
static unsigned detail_serial;
struct Sm64PerfDetail sm64_perf_detail;

void sm64_perf_render_begin(uint32_t begin) {
    sm64_perf_detail_active=0;
    if(!window_active || visible)return;
    /* Keep phase across windows so sampling is not reset by a 2s boundary. */
    if((detail_serial++ & 7u)!=0)return;
    memset(sm64_perf_detail.ticks,0,sizeof(sm64_perf_detail.ticks));
    sm64_perf_detail.depth=0;sm64_perf_detail.bucket=SM64_PERF_MISC;sm64_perf_detail.stamp=begin;
    sm64_perf_detail_active=1;
}

static uint32_t perf_hz(void) {''')
s = replace(s, 'void sm64_perf_render_end(uint32_t begin,uint32_t end) {', '''void sm64_perf_render_end(uint32_t begin,uint32_t end) {
    if(sm64_perf_detail_active) {
        sm64_perf_detail.ticks[sm64_perf_detail.bucket]+=end-sm64_perf_detail.stamp;
        sm64_perf_detail_active=0;
        if(window_active && !visible) {
            for(unsigned i=0;i<SM64_PERF_BUCKETS;i++)window.detail[i]+=sm64_perf_detail.ticks[i];
            window.samples++;
        }
    }''')
s = replace(s, '    s->triangles=(sm64_geometry_triangles-window.triangles)/window.frames;', '''    s->triangles=(sm64_geometry_triangles-window.triangles)/window.frames;
    s->version=2;s->samples=window.samples;
    uint64_t sample_render=0;
    for(unsigned i=0;i<SM64_PERF_BUCKETS;i++) {
        s->detail_ms10[i]=window.samples
            ?(window.detail[i]*10000u)/(tick_hz*(uint64_t)window.samples):0;
        if(i!=SM64_PERF_WAIT)sample_render+=window.detail[i];
    }
    s->sample_render_ms10=window.samples
        ?(sample_render*10000u)/(tick_hz*(uint64_t)window.samples):0;''')
s = replace(s, '    gDPFillRectangle(gDisplayListHead++,4,28,315,239);',
               '    gDPFillRectangle(gDisplayListHead++,4,8,315,239);')
start = s.index('    print_text(12,190,"PERF P1 SNAPSHOT");')
end = s.index('    render_text_labels();', start)
s = s[:start] + '''    print_text(12,210,"PERF P2 SNAPSHOT");
    if(!s->valid) {
        print_text(12,178,"WAIT 5 SECONDS IN LEVEL");
        print_text(12,146,"THEN SELECT L R");
    } else {
        char text[40];
        pair_line(190,"FPS",s->render_fps10,"LCD",s->display_fps10);
        pair_line(170,"GAME",s->game_ms10,"AUD",s->audio_ms10);
        pair_line(150,"REND",s->render_ms10,"WAIT",s->wait_ms10);
        pair_line(130,"DL",s->detail_ms10[SM64_PERF_DL],"VERT",s->detail_ms10[SM64_PERF_VTX]);
        pair_line(110,"TRI",s->detail_ms10[SM64_PERF_TRI],"MAT",s->detail_ms10[SM64_PERF_MAT]);
        pair_line(90,"TILE",s->detail_ms10[SM64_PERF_TEX],"SEND",s->detail_ms10[SM64_PERF_SEND]);
        pair_line(70,"SYNC",s->detail_ms10[SM64_PERF_SYNC],"MISC",s->detail_ms10[SM64_PERF_MISC]);
        snprintf(text,sizeof(text),"SMP %u SUM %u.%u",s->samples,
            s->sample_render_ms10/10,s->sample_render_ms10%10);
        print_text(12,50,text);
        snprintf(text,sizeof(text),"RING %u GEO %u",s->ring_spins,s->geometry);
        print_text(12,30,text);
    }
    print_text(12,10,"SELECT L R TO CLOSE");
''' + s[end:]
p.write_text(s)

p, s = read('src/sm64/sm64/src/pc/gfx/gfx_pc.c')
s = replace(s, '#include "gfx_pc.h"', '''#ifndef TARGET_OPENFPGA
#define SM64_PERF_SCOPE(bucket) ((void)0)
#endif
#include "gfx_pc.h"''')
for function, bucket in [
    ('gfx_sp_matrix','VTX'), ('gfx_sp_pop_matrix','VTX'),
    ('gfx_sp_vertex','VTX'), ('gfx_load_geometry_attributes','VTX'),
    ('gfx_geometry_run','TRI'), ('gfx_sp_tri1','TRI'),
    ('gfx_draw_rectangle','TRI'), ('gfx_update_textures','TEX')]:
    s = scope(s, function, bucket)
s = replace(s, '    const bool depth_test = (rsp.geometry_mode & G_ZBUFFER) == G_ZBUFFER;', '''    SM64_PERF_SCOPE(SM64_PERF_MAT);
    const bool depth_test = (rsp.geometry_mode & G_ZBUFFER) == G_ZBUFFER;''')
s = replace(s, '    gfx_run_dl(commands);', '''    { SM64_PERF_SCOPE(SM64_PERF_DL); gfx_run_dl(commands); }''')
s = replace(s, '    uint32_t perf_render_begin=sm64_perf_clock();', '''    uint32_t perf_render_begin=sm64_perf_clock();
    sm64_perf_render_begin(perf_render_begin);''')
p.write_text(s)

p, s = read('src/sm64/sm64/src/pc/gfx/gfx_gpu.c')
s = replace(s, '#include "gfx_gpu.h"', '#include "gfx_gpu.h"\n#include "pocket/perf_pocket.h"')
for function in ('gfx_gpu_present','gfx_gpu_vtx_cache_tri',
                 'gfx_gpu_geometry_tri','gfx_gpu_geometry_batch',
                 'gpu_draw_triangles','gpu_fill_rect','gpu_tex_rect','gpu_end_frame'):
    s = scope(s, function, 'SEND')
s = replace(s, '        of_gpu_finish();', '''        {
            SM64_PERF_SCOPE(SM64_PERF_SYNC);
            of_gpu_finish();
        }''')
s = replace(s, '        PROF_ACQ_BEGIN();', '''        {
        SM64_PERF_SCOPE(SM64_PERF_WAIT);
        PROF_ACQ_BEGIN();''')
s = replace(s, '        PROF_ACQ_END();', '        PROF_ACQ_END();\n        }')
s = replace(s, '    static uint8_t cleared_bufs;', '''    SM64_PERF_SCOPE(SM64_PERF_SEND);
    static uint8_t cleared_bufs;''')
p.write_text(s)
print('Instrumented private P2 source; vendored SDK and runtime unchanged.')
