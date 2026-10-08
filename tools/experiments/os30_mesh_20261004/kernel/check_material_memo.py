#!/usr/bin/env python3
"""Compare the real memo helper with its frozen, previously validated helper.

The draw-material stub has the renderer's existing invalidation contract. Every
successful result is compared across all64 words; failures must leave output
untouched. Shader words, LOAD fog and guard acceptance vary independently of
warm draw state. Counters verify warm calls avoid selection and snapshot work.
"""
from pathlib import Path
import json, os, subprocess
HERE=Path(__file__).resolve().parent
OUT=HERE/'results/material-memo'
ORIGINAL=OUT/'original_sm64_material.h'
old=ORIGINAL.read_text()
a=old.index('enum {');b=old.index('};',a)+2
old=old[:a]+old[b:]
for name in ['mesh_capture_material_state','mesh_capture_material','mesh_material_status','mesh_material_state']:
    old=old.replace(name,'original_'+name)
(OUT/'original_renamed.h').write_text(old)
source=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define MESH_SHADOW_JOBS 1
#define G_FOG 0x10000
#define G_CULL_FRONT 0x1000
#define G_CULL_BACK 0x2000
#define G_MDSFT_TEXTFILT 0
#define G_TF_POINT 0
#define CC_SHADE 1
#define CC_LOD 2
struct MeshOwnedJob { uint32_t state[64]; };
struct RGBA { uint8_t r,g,b,a; };
struct ColorCombiner { unsigned shader_input_mapping[2][4]; };
static struct { unsigned geometry_mode; } rsp;
static struct { unsigned other_mode_h; struct { int uls,ult; } texture_tile; } rdp;
static struct ColorCombiner comb;
static bool triangle_material_ready,triangle_mesh_material_ready;
static unsigned serial,chosen_eligible,chosen_rgb,chosen_alpha,chosen_fog;
static unsigned selects,snapshots;
static struct ColorCombiner *gfx_prepare_triangle_material(bool *fog,bool *alpha,bool *texture) {
    if(!triangle_material_ready) {
        triangle_mesh_material_ready=false;
        triangle_material_ready=true;
    }
    *fog=chosen_fog;*alpha=chosen_alpha!=4;*texture=(chosen_eligible&2)!=0;
    return &comb;
}
static int gfx_gpu_mesh_select(int *rgb,int *alpha) {
    selects++;*rgb=(int)chosen_rgb-1;*alpha=(int)chosen_alpha-1;return chosen_eligible;
}
static const struct RGBA *gfx_vc_resolve(unsigned m,void *a,void *b,struct RGBA *c) {
    (void)a;(void)b;*c=(struct RGBA){serial*m,serial+m,serial^m,serial-m};return c;
}
static void gfx_gpu_mesh_snapshot(uint32_t *o,int texture,uint8_t alpha) {
    snapshots++;
    for(unsigned i=24;i<47;i++)o[i]=serial*0x1234u+i*0x4567u+(texture?0xabcd:0);
    o[40]=alpha;
}
#include "sm64_material.h"
#include "original_renamed.h"
static uint32_t rng=0x138ded13;
static uint32_t next(void) {rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
int main(void) {
    unsigned tests=0,hits=0,failures=0,statuses[5]={0};
    for(unsigned draw=0;draw<50000;draw++) {
        serial=next();chosen_eligible=next()&7;chosen_rgb=next()%5;chosen_alpha=next()%5;
        chosen_fog=next()&1;rsp.geometry_mode=next();rdp.other_mode_h=next();
        rdp.texture_tile.uls=(int)(next()&65535)-32768;
        rdp.texture_tile.ult=(int)(next()&65535)-32768;
        for(unsigned a=0;a<2;a++)for(unsigned b=0;b<4;b++)comb.shader_input_mapping[a][b]=next()%8;
        triangle_material_ready=false;
        unsigned load_fog=next()&1;
        for(unsigned job=0;job<8;job++) {
            struct MeshOwnedJob in;uint32_t old[64],out[64];
            for(unsigned i=0;i<64;i++)in.state[i]=next();
            if(job==5)load_fog^=1;
            in.state[22]=(in.state[22]&~G_FOG)|(load_fog?G_FOG:0);
            in.state[57]=job&1;
            for(unsigned i=0;i<64;i++)old[i]=out[i]=next();
            unsigned a=original_mesh_capture_material(&in,old);
            bool hit=triangle_mesh_material_ready&&mesh_material_template_load_fog==(bool)load_fog;
            unsigned sb=selects,pb=snapshots;
            unsigned b=mesh_capture_material(&in,out);
            if(a!=b||memcmp(old,out,sizeof(old))) {
                fprintf(stderr,"mismatch draw%u job%u status%u/%u\n",draw,job,a,b);return 1;
            }
            assert(b<5);statuses[b]++;tests++;
            if(hit) {assert(selects==sb&&snapshots==pb);hits++;}
            else assert(selects==sb+1);
            if(b)failures++;
        }
    }
    assert(hits==300000&&tests==400000);
    for(unsigned i=0;i<5;i++)assert(statuses[i]>0);
    printf("{\"cases\":%u,\"warm_hits\":%u,\"rejected_cases\":%u,\"status_counts\":[%u,%u,%u,%u,%u]}\n",
       tests,hits,failures,statuses[0],statuses[1],statuses[2],statuses[3],statuses[4]);
}
'''
(OUT/'test.c').write_text(source)
results={}
for name,flags in [('optimized',['-O3','-ffast-math','-ffp-contract=off']),('sanitized',['-O1','-fsanitize=address,undefined'])]:
    exe=OUT/('test-'+name)
    subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',*flags,
       '-I'+str(HERE.parent/'client'),'-I'+str(OUT),str(OUT/'test.c'),'-o',str(exe)],check=True)
    env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0')
    output=subprocess.check_output([str(exe)],text=True,env=env)
    results[name]=json.loads(output)
(OUT/'result.json').write_text(json.dumps(results,indent=2)+'\n')
print(json.dumps(results,indent=2))
