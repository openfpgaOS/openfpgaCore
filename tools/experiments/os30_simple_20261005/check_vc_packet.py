#!/usr/bin/env python3
"""Extract both renderer functions and original SDK packet helpers for comparison."""
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/os30-simple-20261005/vc-packet-v1'
SDK = ROOT / 'docs/measurements/os30-simple-20261005/SM64/src/sdk/include/of_gpu.h'
GPU_H = ROOT / 'docs/measurements/os30-simple-20261005/SM64/src/sm64/sm64/src/pc/gfx/gfx_gpu.h'

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def function(text, name):
    match = re.search(r'^static inline [^\n]+\b' + re.escape(name) + r'\(', text, re.M)
    assert match, name
    start = match.start()
    pos = text.index('{', start) + 1
    depth = 1
    while depth:
        depth += (text[pos] == '{') - (text[pos] == '}')
        pos += 1
    return text[start:pos]

sdk = SDK.read_text()
struct = re.search(r'struct gfx_vc_vtx \{.*?\n\};', GPU_H.read_text(), re.S)[0]
object_struct = re.search(r'typedef struct \{\n    int32_t  matrix\[5\]\[4\].*?\n\} of_gpu_object_state_t;', sdk, re.S)[0]
helpers = '\n\n'.join(function(sdk, n) for n in ['_gpu_stream_reserve_words', '_gpu_ring_write', '_gpu_ring_claim', '_gpu_ring_commit', '_gpu_cmd_header', 'of_gpu_set_object_state', 'of_gpu_load_vert_clip', 'of_gpu_draw_indexed_tri', 'of_gpu_kick'])
reference = (OUT / 'reference-function.c').read_text().replace('void gfx_gpu_vtx_cache_tri(', 'void reference(')
candidate = (OUT / 'candidate-function.c').read_text().replace('void gfx_gpu_vtx_cache_tri(', 'void candidate(')
prefix = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <assert.h>
#define SM64_PERF_SCOPE(x) ((void)0)
#define VC_W_PRESCALE (1.0f / 256.0f)
#define OF_HW_GPU_VERT_TRI (1u<<20)
#define OF_HW_GPU_XFORM_RGB (1u<<26)
#define OF_HW_GPU_CLIP_LOAD (1u<<29)
#define GPU_CMD_SET_OBJECT_STATE 0x50u
#define GPU_CMD_LOAD_VERT_CLIP 0x56u
#define GPU_CMD_DRAW_INDEXED_TRI 0x54u
#define OF_GPU_COMMAND_STREAM_BATCH_WORDS 4095u
#define OF_GPU_KICK_MIN_WORDS 512u
static const uint32_t _gpu_ring_mask=16383;
struct caps {uint32_t hw_features;};
static struct caps caps;
static const struct caps *of_get_caps(void) {return &caps;}
static int of_has_feature(uint32_t f) {return (caps.hw_features&f)!=0;}
static uint32_t staging[4095], transcript[8192];
static uint32_t *_gpu_batch_buf=staging;
static uint32_t _gpu_cmd_words,_gpu_wrptr,_gpu_known_rdptr,_gpu_unflushed_sync;
static unsigned transcript_words,reserves,publications;
static void _gpu_flush_cmd_stream(void) {
    assert(transcript_words+_gpu_cmd_words<8192);
    memcpy(transcript+transcript_words,staging,_gpu_cmd_words*4);
    transcript_words+=_gpu_cmd_words;_gpu_cmd_words=0;publications++;
}
static void _gpu_ring_ensure(uint32_t bytes) {
    assert(bytes<=16380 && (bytes&3)==0);reserves++;
    if (((_gpu_known_rdptr-_gpu_wrptr-4)&_gpu_ring_mask)<bytes) {
        _gpu_flush_cmd_stream();
        _gpu_known_rdptr=_gpu_wrptr; /* GPU drains to the published writer. */
    }
    assert(((_gpu_known_rdptr-_gpu_wrptr-4)&_gpu_ring_mask)>=bytes);
}
static int g_cd_active,g_subpix_tri,g_vc_textured,g_vc_vp_valid,g_geometry_matrix_valid,g_vc_rgb_input;
static uint8_t g_surf_alpha;
static float g_vc_vp[4],g_cx,g_cy,g_hw,g_hh;
static uint32_t g_vc_words,g_vc_loads,g_vc_tris_cached,g_vc_kick;
static unsigned material_calls;
static void emit_tri_state(int textured) {material_calls+=(unsigned)textured+1;}
static uint16_t rgba_to_565(uint8_t r,uint8_t g,uint8_t b) {
    return ((uint16_t)(r>>3)<<11)|((uint16_t)(g>>2)<<5)|(b>>3);
}
'''
suffix = r'''
struct result {
    uint32_t words[8192],count,wrptr,counters[5];
    int cd,subpix,vpvalid,matrix; uint8_t alpha;float vp[4];unsigned material;
};
static uint32_t rng=0x45612389;
static uint32_t next(void) {rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
static void setup(unsigned initial,unsigned f,unsigned variant) {
    caps.hw_features=((f&1)?OF_HW_GPU_VERT_TRI:0)|((f&2)?OF_HW_GPU_CLIP_LOAD:0)|((f&4)?OF_HW_GPU_XFORM_RGB:0);
    _gpu_cmd_words=initial;_gpu_wrptr=(variant*1176u+initial*4u)&16383u;
    _gpu_known_rdptr=(_gpu_wrptr+4u+((variant*444u)&16380u))&16383u;
    _gpu_unflushed_sync=0;transcript_words=reserves=publications=0;
    for(unsigned i=0;i<initial;i++)staging[i]=0xCA000000u+i;
    g_cd_active=1;g_subpix_tri=0;g_vc_textured=variant&1;g_surf_alpha=0;
    g_cx=160;g_cy=120;g_hw=160;g_hh=-120;
    g_vc_vp[0]=g_cx;g_vc_vp[1]=g_cy;g_vc_vp[2]=g_hw;g_vc_vp[3]=g_hh;
    g_vc_vp_valid=(variant>>1)&1;g_geometry_matrix_valid=1;g_vc_rgb_input=(variant&8)?0:-1;
    g_vc_words=19;g_vc_loads=2;g_vc_tris_cached=3;g_vc_kick=variant%32;material_calls=0;
}
static void collect(struct result *r) {
    _gpu_flush_cmd_stream();memset(r,0,sizeof(*r));
    memcpy(r->words,transcript,transcript_words*4);r->count=transcript_words;r->wrptr=_gpu_wrptr;
    r->counters[0]=g_vc_words;r->counters[1]=g_vc_loads;r->counters[2]=g_vc_tris_cached;r->counters[3]=g_vc_kick;
    r->cd=g_cd_active;r->subpix=g_subpix_tri;r->vpvalid=g_vc_vp_valid;r->matrix=g_geometry_matrix_valid;
    r->alpha=g_surf_alpha;memcpy(r->vp,g_vc_vp,sizeof(r->vp));r->material=material_calls;
}
static void check(unsigned initial,unsigned f,unsigned variant,unsigned mask,struct gfx_vc_vtx v[3],uint8_t slot[3]) {
    struct result a,b;
    setup(initial,f,variant);reference(v,slot,mask);collect(&a);
    setup(initial,f,variant);candidate(v,slot,mask);collect(&b);
    if(memcmp(&a,&b,sizeof(a))) {
        fprintf(stderr,"mismatch initial=%u caps=%u variant=%u mask=%u\n",initial,f,variant,mask);abort();
    }
}
int main(void) {
    struct gfx_vc_vtx v[3]={0};uint8_t slots[3]={0,15,31};unsigned long cases=0;
    for(unsigned k=0;k<3;k++) {
        v[k].cx=-(float)k*13.25f;v[k].cy=k*0.33333f;v[k].cw=2+k*99.5f;v[k].u=k*2.875f;v[k].v=-v[k].u;
        v[k].w_inv=1.0f/v[k].cw;v[k].r=17+k*20;v[k].g=254-k*42;v[k].b=134+k*51;v[k].a=120+k;
        v[k].use_exact_depth=k&1;v[k].exact_depth=0x80000001u+k;
    }
    for(unsigned initial=0;initial<=4095;initial++)for(unsigned f=0;f<8;f++)for(unsigned mask=0;mask<8;mask++) {
        check(initial,f,initial+mask+f,mask,v,slots);cases++;
    }
    for(unsigned trial=0;trial<100000;trial++) {
        for(unsigned k=0;k<3;k++) {
            slots[k]=next();v[k].cx=(int16_t)next()*0.015625f;v[k].cy=(int16_t)next()*0.015625f;
            v[k].cw=(next()&0x7fff)+0.25f;v[k].u=(int16_t)next()*0.125f;v[k].v=(int16_t)next()*0.125f;
            v[k].w_inv=(trial&8)?(next()%10000)/1000.0f:1.0f/v[k].cw;
            v[k].r=next();v[k].g=next();v[k].b=next();v[k].a=next();
            v[k].exact_depth=next();v[k].use_exact_depth=next()&1;
        }
        check(next()%4096,next()%8,next(),next(),v,slots);cases++;
    }
    printf("PASS cases=%lu all4096 staging positions x8capabilities x8dirtymasks; random quantization/maskedslots/depth/state cases=100000\n",cases);
    return 0;
}
'''
harness = prefix + struct + '\n' + object_struct + '\n' + helpers + '\n' + reference + candidate + suffix
(OUT / 'packet-proof.c').write_text(harness)
logs = {}
for name, flags in [('checked', ['-O2', '-fsanitize=undefined,address', '-fno-omit-frame-pointer']), ('fast', ['-O3', '-ffast-math', '-ffp-contract=fast'])]:
    exe = OUT / ('packet-proof-' + name)
    command = ['gcc', '-std=c11', *flags, str(OUT / 'packet-proof.c'), '-lm', '-o', str(exe)]
    subprocess.run(command, check=True)
    env = dict(os.environ, ASAN_OPTIONS='detect_leaks=0')
    result = subprocess.run([str(exe)], check=True, capture_output=True, text=True, env=env)
    (OUT / (name + '.log')).write_text(result.stdout + result.stderr)
    logs[name] = {'command': command, 'stdout': result.stdout.strip(), 'binary_sha256': sha(exe), 'ASAN_OPTIONS': env['ASAN_OPTIONS']}
proof = {'passed': True, 'candidate_sha256': sha(OUT / 'gfx_gpu.c'), 'sdk_sha256': sha(SDK), 'gpu_header_sha256': sha(GPU_H),
         'harness_sha256': sha(OUT / 'packet-proof.c'), 'script_sha256': sha(Path(__file__)), 'runs': logs,
         'scope': 'Actual complete renderer functions and actual SDK load/draw/header/reserve/claim/commit/object/kick helpers. Abstract transport records ordered words; all stream positions/capability masks/dirty masks plus random payload/state cases. Compare words, pointer, viewport/material/geometry flags and counters; publication times and FP exception flags are not compared.',
         'limitations': 'Host arithmetic test; RV32 target and complete render replay/performance remain separate qualification. Leak checking disabled because the execution environment uses ptrace (ASan bounds and UBSan remain enabled); harness performs no heap allocation.'}
(OUT / 'focused-proof.json').write_text(json.dumps(proof, indent=2) + '\n')
print(json.dumps(proof, indent=2))
