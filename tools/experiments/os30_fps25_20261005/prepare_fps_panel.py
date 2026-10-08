#!/usr/bin/env python3
"""Keep useful frame timing while removing detailed renderer instrumentation."""
import difflib
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / 'docs/measurements/os30-fps25-20261005/SM64/src/sm64/pocket'
DEST = ROOT / 'docs/measurements/os30-fps25-20261005/fps-panel-v1'
HASHES = {
    'perf_pocket.h': '54677ecbc353a46e63c4459f5c017e5ad6eb2148155d96508a0ac35c936b7a5d',
    'perf_pocket.c': '2f6a9c14520db623a183218c232ea40a4abacd8ce9902d0c654af2c216f78328',
}


def sha(b): return hashlib.sha256(b).hexdigest()


def once(s, old, new):
    assert s.count(old) == 1, (s.count(old), old[:100])
    return s.replace(old, new, 1)


def header(s):
    s = once(s, '#define SM64_PERF_DIAG 0\n#endif', '''#define SM64_PERF_DIAG 0
#endif

/* FPS/frame timing is the default diagnostic mode. Opt in to the detailed
 * P3 renderer buckets when investigating a specific subsystem; those scopes
 * add branches/register pressure even on frames that are not sampled. */
#ifndef SM64_PERF_DETAILS
#define SM64_PERF_DETAILS 0
#endif''')
    s = once(s, 'static inline __attribute__((always_inline)) void sm64_perf_enter(unsigned bucket)', '#if SM64_PERF_DETAILS\nstatic inline __attribute__((always_inline)) void sm64_perf_enter(unsigned bucket)')
    s = once(s, '\nvoid sm64_perf_render_begin(uint32_t begin);\n', '\n')
    s = once(s, '''        sm64_perf_scope_begin(bucket)

uint32_t sm64_perf_step_begin(void);''', '''        sm64_perf_scope_begin(bucket)
#else
#define SM64_PERF_SCOPE(bucket) ((void)0)
#define SM64_PERF_COUNT(route,count) ((void)0)
#endif

void sm64_perf_render_begin(uint32_t begin);
uint32_t sm64_perf_step_begin(void);''')
    return s


def implementation(s):
    s = once(s, '''void sm64_perf_render_begin(uint32_t begin) {
    sm64_perf_detail_active=0;''', '''void sm64_perf_render_begin(uint32_t begin) {
#if SM64_PERF_DETAILS
    sm64_perf_detail_active=0;''')
    s = once(s, '''    sm64_perf_detail_active=1;
}

static uint32_t perf_hz''', '''    sm64_perf_detail_active=1;
#else
    (void)begin;
    sm64_perf_detail_active=0;
#endif
}

static uint32_t perf_hz''')
    s = once(s, '''void sm64_perf_render_end(uint32_t begin,uint32_t end) {
    if(sm64_perf_detail_active)''', '''void sm64_perf_render_end(uint32_t begin,uint32_t end) {
#if SM64_PERF_DETAILS
    if(sm64_perf_detail_active)''')
    s = once(s, '''    unsigned flip,acquire,spins,free_words;
    gpu_present_prof_get''', '''#endif
    unsigned flip,acquire,spins,free_words;
    gpu_present_prof_get''')
    s = once(s, '''    s->version=3;s->samples=window.samples;''', '''#if SM64_PERF_DETAILS
    s->version=3;s->samples=window.samples;''')
    s = once(s, '''    s->sample_render_ms10=window.samples
        ?(sample_render*10000u)/(tick_hz*(uint64_t)window.samples):0;
}''', '''    s->sample_render_ms10=window.samples
        ?(sample_render*10000u)/(tick_hz*(uint64_t)window.samples):0;
#else
    /* Keep the exported snapshot layout unchanged. Version4/samples0 means
     * detailed buckets were not measured; the FPS panel does not show them. */
    s->version=4;s->samples=0;s->sample_render_ms10=0;
    memset(s->detail_ms10,0,sizeof(s->detail_ms10));
    memset(s->vertex_ms10,0,sizeof(s->vertex_ms10));
    memset(s->vertex_counts,0,sizeof(s->vertex_counts));
#endif
}''')
    s = once(s, '''    print_text(12,210,"PERF P3 VERT");''', '''#if SM64_PERF_DETAILS
    print_text(12,210,"PERF P3 VERT");
#else
    print_text(12,210,"PERF F4 FPS");
#endif''')
    s = once(s, '''        pair_line(170,"REND",s->render_ms10,"VERT",s->detail_ms10[SM64_PERF_VTX]);''', '''#if SM64_PERF_DETAILS
        pair_line(170,"REND",s->render_ms10,"VERT",s->detail_ms10[SM64_PERF_VTX]);''')
    s = once(s, '''        print_text(12,50,text);
        snprintf(text,sizeof(text),"RING %u GEO %u",s->ring_spins,s->geometry);''', '''        print_text(12,50,text);
#else
        pair_line(170,"GAME",s->game_ms10,"AUD",s->audio_ms10);
        pair_line(150,"REND",s->render_ms10,"WAIT",s->wait_ms10);
        pair_line(130,"SIM",s->sim_fps10,"GAP",s->gap_ms10);
        snprintf(text,sizeof(text),"LEVEL %u CPU %u MHZ",s->level,s->clock_hz/1000000u);
        print_text(12,90,text);
        print_text(12,50,"TIMES IN MS");
#endif
        snprintf(text,sizeof(text),"RING %u GEO %u",s->ring_spins,s->geometry);''')
    return s


def main():
    original = {name: (BASE/name).read_bytes() for name in HASHES}
    for name, data in original.items(): assert sha(data) == HASHES[name], name
    changed = {'perf_pocket.h': header(original['perf_pocket.h'].decode()).encode(),
               'perf_pocket.c': implementation(original['perf_pocket.c'].decode()).encode()}
    DEST.mkdir(exist_ok=False)
    files = {}
    for name, data in changed.items():
        (DEST/name).write_bytes(data)
        (DEST/('baseline-'+name)).write_bytes(original[name])
        (DEST/(name+'.patch')).write_text(''.join(difflib.unified_diff(
            original[name].decode().splitlines(True),data.decode().splitlines(True),
            fromfile='a/'+name,tofile='b/'+name)))
        files[name] = {'baseline_sha256': sha(original[name]),'candidate_sha256': sha(data)}
    (DEST/Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    record = {'candidate':DEST.name,'qualified':False,'files':files,'recipe_sha256':sha(Path(__file__).read_bytes()),
        'macro':'SM64_PERF_DETAILS','default':0,'requires_for_panel':'SM64_PERF_DIAG=1',
        'scope':'Default diagnostic panel measures frame-level FPS/Game/Audio/Render/Wait without per-scope/count renderer instrumentation.',
        'snapshot_abi':'Identical struct and offsets; version4/samples0 explicitly means detailed arrays unavailable. Opt-in DETAILS1 restores original version3.',
        'preserved':['Select+L+R input handling and frozen two-second snapshot.','Game/audio/render/wait/FPS timing formulas and all original first18 snapshot fields.','Detailed P3 mode by compiling SM64_PERF_DETAILS=1.','Normal DIAG0 compile-time stubs and behavior.'],
        'required_checks':['Preprocessed DETAILS1 and DIAG0 equivalent to baseline.','DIAG1 DETAILS0 hot macros preprocess to no-op and timing/panel remains callable.','Exact normal ELF and selected diagnostic capture/render tests with snapshot layout check.'],
        'stage_changed':False,'sibling_changed':False,'card_changed':False}
    (DEST/'manifest.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2))


if __name__=='__main__':main()
