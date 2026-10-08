#!/usr/bin/env python3
"""Private cached fallback versus original upload quantization and GPU math."""
from pathlib import Path
import os
import subprocess
import tempfile

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
GFX=ROOT/'docs/measurements/os30-mesh-20261004/SM64/src/sm64/sm64/src/pc/gfx'
gpu=(GFX/'gfx_gpu.c').read_text();header=(GFX/'gfx_gpu.h').read_text()
start=header.index('struct gfx_vc_vtx {');vertex=header[start:header.index('\n};',start)+3]
start=gpu.index('static inline uint32_t gpu_clip_reciprocal(')
end=gpu.index('static void gpu_draw_triangles(',start)
projection=gpu[start:end]
source=r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <math.h>
#define VC_W_PRESCALE (1.0f/256.0f)
VERTEX
static float g_cx=160,g_cy=120,g_hw=160,g_hh=120;
static int g_vc_rgb_input;
static unsigned g_vc_loads,g_vc_words,g_vc_tris_cached;
static uint32_t emitted[3][7];
static uint16_t rgba_to_565(int r,int g,int b){return ((r&248)<<8)|((g&252)<<3)|(b>>3);}
static void mesh_projected_triangle(const int16_t *x,const int16_t *y,
    const int32_t *s,const int32_t *t,const int32_t *zi,const uint16_t *rgb,
    const int32_t *depth,const uint16_t *additive,unsigned normalize){
    assert(!additive&&normalize==1);
    for(unsigned i=0;i<3;i++) {
        uint32_t *p=emitted[i];p[0]=(uint16_t)x[i]|((uint32_t)(uint16_t)y[i]<<16);
        p[1]=s[i];p[2]=t[i];p[3]=zi[i];p[4]=depth[i];p[5]=rgb[i];p[6]=0;
    }
}
#include "mesh_cached_projection.h"
PROJECTION
static unsigned seed=0x56ca5e01;
static unsigned rnd(void){seed^=seed<<13;seed^=seed>>17;seed^=seed<<5;return seed;}
static uint32_t reference[32][7];
static void original_slot(const struct gfx_vc_vtx *v,unsigned slot) {
    // Exactly the old application's 0x56 upload: roundedQ24.8 positions,
    // rounded texels and explicit legacy depth. GPU reciprocal is independent.
    int32_t xq=lrintf(v->cx*256.0f),yq=lrintf(v->cy*256.0f),wq=lrintf(v->cw*256.0f);
    uint32_t zi=(UINT64_C(1)<<32)/(wq<256?256u:(uint32_t)wq);
    int32_t rx=(int64_t)xq*zi>>16,ry=(int64_t)yq*zi>>16;
    int64_t x=lrintf(g_cx)*16+((lrintf(g_hw)*(int64_t)rx)>>12);
    int64_t y=lrintf(g_cy*16.0f)-((lrintf(g_hh*16.0f)*(int64_t)ry)>>16);
    if(x<-32768)x=-32768;if(x>32767)x=32767;if(y<-32768)y=-32768;if(y>32767)y=32767;
    float depth=v->w_inv*1073741824.0f;
    if(depth<1.0f)depth=1.0f;if(depth>2147483520.0f)depth=2147483520.0f;
    uint32_t *p=reference[slot];p[0]=(uint16_t)x|((uint32_t)(uint16_t)y<<16);
    p[1]=(int32_t)lrintf(v->u*65536.0f);p[2]=(int32_t)lrintf(v->v*65536.0f);
    p[3]=zi;p[4]=v->use_exact_depth?v->exact_depth:(uint32_t)lrintf(depth);
    p[5]=g_vc_rgb_input<0?65535:rgba_to_565(v->r,v->g,v->b);p[6]=0;
}
int main(void){
    uint32_t valid=0;unsigned loads=0;
    for(unsigned job=0;job<100000;job++) {
        struct gfx_vc_vtx v[3];uint8_t slots[3];unsigned dirty=rnd()&7;
        if(job%17==0){g_vc_rgb_input=(job&1)?-1:0;dirty=7;}
        if(job%31==0){g_cx=(rnd()%640)/2.0f;g_cy=(rnd()%480)/2.0f;dirty=7;}
        for(unsigned i=0;i<3;i++) {
            do{slots[i]=rnd()%32;}while((i&&slots[i]==slots[0])||(i==2&&slots[i]==slots[1]));
            if(!(valid&(1u<<slots[i])))dirty|=1u<<i;
            memset(&v[i],0,sizeof v[i]);
            if(!(dirty&(1u<<i))){v[i].cx=v[i].cy=v[i].cw=NAN;continue;}
            v[i].cx=((int)(rnd()%20000000)-10000000)/100.0f;
            v[i].cy=((int)(rnd()%20000000)-10000000)/100.0f;
            v[i].cw=(rnd()%3276500+1)/100.0f;v[i].w_inv=1.0f/v[i].cw;
            v[i].u=((int)(rnd()%204800)-102400)/100.0f;
            v[i].v=((int)(rnd()%204800)-102400)/100.0f;
            v[i].r=rnd();v[i].g=rnd();v[i].b=rnd();v[i].a=rnd();
            v[i].use_exact_depth=rnd()&1;v[i].exact_depth=rnd();
            original_slot(&v[i],slots[i]);valid|=1u<<slots[i];loads++;
        }
        mesh_draw_cached_triangle(v,slots,dirty);
        for(unsigned i=0;i<3;i++)assert(!memcmp(emitted[i],reference[slots[i]],7*4));
    }
    assert(g_vc_loads==loads&&g_vc_tris_cached==100000&&g_vc_words==800000);
    puts("PASS 100000 cached triangles: partial slot reuse, old projection/zi/depth/UV/color, normalize=true");
}
'''.replace('VERTEX',vertex).replace('PROJECTION',projection)
with tempfile.TemporaryDirectory(prefix='mesh-cached-projection-') as name:
    dest=Path(name);(dest/'test.c').write_text(source)
    subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Wno-misleading-indentation',
                    '-fsanitize=address,undefined','-ffp-contract=off','-I'+str(HERE),str(dest/'test.c'),
                    '-lm','-o',str(dest/'test')],check=True)
    env=os.environ.copy();env['ASAN_OPTIONS']='detect_leaks=0'
    subprocess.run([str(dest/'test')],check=True,env=env)
