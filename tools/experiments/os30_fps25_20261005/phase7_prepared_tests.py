#!/usr/bin/env python3
"""Compare actual F8 and prepared backends, including ring and kick events."""
from pathlib import Path
import hashlib
import json
import os
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
CAMP = ROOT / 'docs/measurements/os30-fps25-20261005'
BASE = CAMP / 'phase6/candidate'
CAND = CAMP / 'phase7' / (sys.argv[1] if len(sys.argv)>1 else 'prepared-v2')
OUT = CAND / 'focused'

def function(source, name):
    match = re.search(r'^(?:static inline |static |void )[^{;\n]*\b' + name + r'\(', source, re.M)
    assert match, name
    brace = source.index('{', match.end())
    pos, depth = brace+1, 1
    while depth:
        depth += (source[pos]=='{') - (source[pos]=='}')
        pos += 1
    return source[match.start():pos]

PREFIX = r'''
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <fenv.h>
#include <assert.h>
#pragma STDC FENV_ACCESS ON
#define RSPV_TO_FLOAT(x) (x)
#define SM64_PERF_SCOPE(x)
#define GPU_CMD_LOAD_VERT_CLIP 0x56
#define GPU_CMD_DRAW_INDEXED_TRI 0x54
#define VC_W_PRESCALE (1.0f/256.0f)
struct LoadedVertex {float x,y,w,w_inv;};
typedef struct {int32_t xcenter,ycenter,xscale,yscale,near_clip,rows;} of_gpu_object_state_t;
static float g_cx=160, g_cy=120, g_hw=160, g_hh=120, g_vc_vp[4];
static int g_cd_active,g_subpix_tri,g_vc_textured,g_vc_rgb_input;
static int g_surf_alpha,g_vc_vp_valid,g_geometry_matrix_valid;
static unsigned g_vc_words,g_vc_loads,g_vc_tris_cached,g_vc_kick;
static uint32_t wire[4096],events[1024];
static unsigned nw,ne,reserved,ring_used;
static void event(uint32_t e) {assert(ne<1024);events[ne++]=e;}
static void emit_tri_state(int textured) {event(0xA0000000u|((unsigned)g_surf_alpha<<8)|textured);}
static void of_gpu_set_object_state(const of_gpu_object_state_t *os) {
    event(0x50000000u);assert(os->xcenter==160 && os->ycenter==1920);
    assert(os->xscale==160 && os->yscale==1920 && os->near_clip==256 && os->rows==3);
}
static void _gpu_stream_reserve_words(unsigned n) {
    assert(n<=29 && reserved==0);event(0x10000000u|n);
    if(ring_used+n>4095){event(0x20000000u|ring_used);ring_used=0;}
    assert(nw+n<=4096);reserved=n;
}
static uint32_t *_gpu_ring_claim(void) {assert(reserved);return wire+nw;}
static void _gpu_ring_commit(unsigned n) {
    assert(reserved==n);event(0x30000000u|n);reserved=0;nw+=n;ring_used+=n;
}
static void of_gpu_kick(void) {event(0x40000000u|ring_used);ring_used=0;}
static void of_gpu_load_vert_clip(uint8_t s,int32_t x,int32_t y,int32_t w,
                                  int32_t u,int32_t v,uint16_t rgb,uint32_t depth) {
    _gpu_stream_reserve_words(9);uint32_t *p=_gpu_ring_claim();
    *p++=0x56000008;*p++=s;*p++=x;*p++=y;*p++=w;*p++=u;*p++=v;*p++=rgb;*p++=depth;
    _gpu_ring_commit(9);
}
static void of_gpu_draw_indexed_tri(uint8_t x,uint8_t y,uint8_t z) {
    _gpu_stream_reserve_words(2);uint32_t *p=_gpu_ring_claim();
    *p++=0x54000001;*p++=x|(y<<5)|(z<<10);_gpu_ring_commit(2);
}
'''
TEST = r'''
static uint32_t seed=0x19840523;
static uint32_t rng(void) {seed^=seed<<13;seed^=seed>>17;seed^=seed<<5;return seed;}
static void reset(unsigned trial) {
    nw=ne=reserved=0;ring_used=4095-(trial%30);
    g_cd_active=1;g_subpix_tri=0;g_vc_textured=trial&1;
    g_vc_rgb_input=(trial&2)?0:-1;g_surf_alpha=0;
    g_vc_vp_valid=trial&1;g_geometry_matrix_valid=1;
    g_vc_words=g_vc_loads=g_vc_tris_cached=0;g_vc_kick=trial%32;
}
struct result {uint32_t w[4096],e[1024],n[11];};
static void save(struct result *r) {
    memcpy(r->w,wire,nw*4);memcpy(r->e,events,ne*4);
    const uint32_t n[]={nw,ne,ring_used,g_vc_words,g_vc_loads,g_vc_tris_cached,
        g_vc_kick,g_cd_active,g_subpix_tri,g_vc_vp_valid,g_geometry_matrix_valid};
    memcpy(r->n,n,sizeof n);
}
static void run(unsigned trial) {
    struct gfx_vc_vtx old[32];struct gfx_vc_prepared prepared[32];uint32_t jobs[64];
    static const float inverse[]={-0.125f,0,0x1p-40f,0x1p-30f,0x1p-20f,0.01f,
        nextafterf(2.0f,0),2.0f,4.0f};
    for(unsigned i=0;i<32;i++) {
        struct LoadedVertex v;
        v.x=((int32_t)(rng()%16777216)-8388608)/256.0f;
        v.y=((int32_t)(rng()%16777216)-8388608)/256.0f;
        v.w=0.015625f+(rng()%8388352)/256.0f;
        v.w_inv=inverse[(trial+i)%9];
        old[i]=(struct gfx_vc_vtx){.cx=v.x,.cy=v.y,.cw=v.w,.w_inv=v.w_inv,
            .u=((int32_t)(rng()%1073741824)-536870912)/65536.0f,
            .v=((int32_t)(rng()%1073741824)-536870912)/65536.0f,
            .r=rng(),.g=rng(),.b=rng(),.a=trial,.use_exact_depth=0};
        /* Exercise half-way rounding around both signs and the depth clamp. */
        if(i<4) {v.x=old[i].cx=(i&1?-1.0f:1.0f)*(10.5f+i)/256.0f;
                  old[i].u=(i&1?-1.0f:1.0f)*(12.5f+i)/65536.0f;}
        uint16_t rgb=(trial&2)?rgba_to_565(old[i].r,old[i].g,old[i].b):0xffff;
        gfx_cpu_prepare_clip(&prepared[i],&v,old[i].u,old[i].v,rgb);
    }
    unsigned count=trial%65;
    for(unsigned j=0;j<count;j++) {
        unsigned a=rng()%32,b=rng()%32,c=rng()%32;
        if((trial&12)==0)b=a;if((trial&12)==4)c=b;if((trial&12)==8)c=a;
        if((trial&12)==12)b=c=a;
        jobs[j]=a|(b<<5)|(c<<10)|(((trial+j)&7)<<15);
    }
    struct result a={0},b={0};
    reset(trial);reference_batch(old,jobs,count,(uint8_t)trial);save(&a);
    reset(trial);candidate_batch(prepared,jobs,count,(uint8_t)trial);save(&b);
    if(memcmp(&a,&b,sizeof a)) {fprintf(stderr,"mismatch trial=%u mode=%d\n",trial,fegetround());abort();}
}
int main(void) {
    assert(sizeof(struct gfx_vc_prepared)==28 && sizeof(struct gfx_vc_vtx)==36);
    int modes[]={FE_TONEAREST,FE_DOWNWARD,FE_UPWARD,FE_TOWARDZERO};
    for(unsigned r=0;r<4;r++){assert(fesetround(modes[r])==0);
        for(unsigned i=0;i<4160;i++)run(i);}
    puts("16640 exact batch cases: all 0..64 lengths, dirty masks, duplicate slots, kicks, ring boundaries, 4 rounding modes");
}
'''

def main():
    OUT.mkdir(exist_ok=False)
    old=(BASE/'gfx_gpu.c').read_text();new=(CAND/'gfx_gpu.c').read_text()
    head=(CAND/'gfx_gpu.h').read_text();pc=(CAND/'gfx_pc.c').read_text()
    structs='\n'.join(re.search(r'struct '+name+r' \{.*?\n\};',head,re.S).group()
                       for name in ('gfx_vc_vtx','gfx_vc_prepared'))
    text=PREFIX+structs+'\n'+function(old,'rgba_to_565')+'\n'
    text+=function(pc,'gfx_cpu_prepare_clip')+'\n'
    text+=function(old,'gfx_gpu_vtx_cache_batch').replace('gfx_gpu_vtx_cache_batch','reference_batch')+'\n'
    text+=function(new,'gfx_gpu_vtx_cache_batch').replace('gfx_gpu_vtx_cache_batch','candidate_batch')+'\n'+TEST
    # Constant-expression C initialization cannot call nextafterf.
    text=text.replace('nextafterf(2.0f,0)','0x1.fffffep0f')
    (OUT/'test.c').write_text(text)
    logs=[]
    for mode in ('ring','pc'):
        exe=OUT/mode
        cmd=['cc','-std=gnu99','-O2','-g','-ffp-contract=off','-frounding-math',
             '-fsanitize=address,undefined','-fno-omit-frame-pointer','-fno-pie','-no-pie']
        if mode=='pc':cmd+=['-DOF_PC']
        subprocess.run(cmd+[str(OUT/'test.c'),'-lm','-o',str(exe)],check=True)
        # The harness is stack/static-only. LeakSanitizer cannot attach under
        # the managed runner; retain address and undefined-behavior checks.
        p=subprocess.run([str(exe)],text=True,capture_output=True,
                         env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0'))
        logs.append(mode+': '+p.stdout+p.stderr);assert p.returncode==0,logs[-1]
    # A changed prescale must be caught by the command-word oracle.
    negative=text.replace('RSPV_TO_FLOAT(v->x) * 256.0f','RSPV_TO_FLOAT(v->x) * 255.0f')
    assert negative!=text
    (OUT/'negative.c').write_text(negative)
    subprocess.run(['cc','-std=gnu99','-O2','-ffp-contract=off','-frounding-math',
                    str(OUT/'negative.c'),'-lm','-o',str(OUT/'negative')],check=True)
    p=subprocess.run([str(OUT/'negative')],text=True,capture_output=True)
    assert p.returncode!=0 and 'mismatch' in p.stderr
    logs.append('Negative control detected: '+p.stderr)
    (OUT/'results.txt').write_text('\n'.join(logs))
    result={'pass':True,'cases':33280,'rounding_modes':4,'negative_control_detected':True,
            'source_sha256':{n:hashlib.sha256((CAND/n).read_bytes()).hexdigest()
                             for n in ('gfx_pc.c','gfx_gpu.c','gfx_gpu.h')},
            'files_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.iterdir() if p.is_file()}}
    (OUT/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print('\n'.join(logs))

if __name__=='__main__':main()
