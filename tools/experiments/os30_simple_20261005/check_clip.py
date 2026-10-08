#!/usr/bin/env python3
"""Compare extracted original/candidate clip functions, including emitted fans."""
import hashlib
import importlib.util
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT/'docs/measurements/os30-simple-20261005/clip-exact'
BASE = OUT.parent/'baseline/gfx_pc.c'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
spec = importlib.util.spec_from_file_location('clip_recipe', Path(__file__).with_name('prepare_clip.py'))
recipe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recipe)

PRE = r'''
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include <math.h>
typedef float rspv_t;
#define RSPV_TO_INT(v) ((int)(v))
#define RSPV_FROM_INT(v) ((float)(v))
#define RSPV_MUL(a,b) ((a)*(b))
#define RSPV_RCP(a) (1.0f/(a))
#define RSPV_DIV(a,b) ((a)/(b))
'''
SINK = r'''
static unsigned which, counts[2], cases, fields, fans, unclipped, discarded;
static struct LoadedVertex submitted[2][32][3];
static int gfx_push_triangle(const struct LoadedVertex *a,const struct LoadedVertex *b,
                             const struct LoadedVertex *c,bool geometry_only) {
    assert(!geometry_only && counts[which]<32);
    const unsigned i=counts[which]++;
    submitted[which][i][0]=*a;submitted[which][i][1]=*b;submitted[which][i][2]=*c;
    return 1;
}
'''
TEST = r'''
static uint32_t rng=0xc04be218;
static uint32_t rnd(void) {rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
static float rf(void) {return ((int)(rnd()&0xffff)-32768)*(1.0f/2048.0f);}
static unsigned active_planes[64], empty_flags, multi_planes;
static uint8_t clip(const struct LoadedVertex *v) {
    return (v->z>v->w?1:0)|(v->z < -v->w?2:0)|(v->y>v->w?4:0)|
           (v->y < -v->w?8:0)|(v->x>v->w?16:0)|(v->x < -v->w?32:0);
}
static void compare(struct LoadedVertex v[3]) {
    unsigned mask=v[0].clip_rej|v[1].clip_rej|v[2].clip_rej;
    active_planes[mask]++;if(!mask)empty_flags++;
    if(mask && (mask&(mask-1)))multi_planes++;
    memset(submitted,0xa5,sizeof(submitted));counts[0]=counts[1]=0;
    which=0;bool a=reference_clip(v,v+1,v+2,v[0].clip_rej&v[1].clip_rej&v[2].clip_rej);
    which=1;bool b=candidate_clip(v,v+1,v+2,v[0].clip_rej&v[1].clip_rej&v[2].clip_rej);
    if(a!=b || counts[0]!=counts[1]) {
        printf("FAIL topology case=%u mask=%x returned=%u/%u counts=%u/%u\n",cases,mask,a,b,counts[0],counts[1]);abort();
    }
    for(unsigned i=0;i<counts[0];i++)for(unsigned j=0;j<3;j++) {
        /* Submission consumes exactly x/y/z/w/w_inv/u/v and RGBA. It never
         * observes scratch clip_rej or struct padding. Compare all32 bytes. */
        const struct LoadedVertex *x=&submitted[0][i][j],*y=&submitted[1][i][j];
        if(memcmp(x,y,offsetof(struct LoadedVertex,clip_rej))) {
            uint32_t xb[8],yb[8];memcpy(xb,x,32);memcpy(yb,y,32);
            for(unsigned k=0;k<8;k++)if(xb[k]!=yb[k])
                printf("FAIL payload case=%u mask=%x tri=%u vert=%u word=%u %08x/%08x\n",cases,mask,i,j,k,xb[k],yb[k]);
            abort();
        }
        fields+=8;
    }
    cases++;fans+=counts[0];unclipped+=!a;discarded+=a&&!counts[0];
}
static void init(struct LoadedVertex v[3]) {
    for(unsigned i=0;i<3;i++) {
        memset(v+i,0,sizeof(v[i]));v[i].x=rf();v[i].y=rf();v[i].z=rf();
        v[i].w=0.25f+(rnd()%32768)*(1.0f/1024.0f);v[i].w_inv=1/v[i].w;
        v[i].u=rf()*1024;v[i].v=rf()*1024;
        v[i].color=(struct RGBA){rnd(),rnd(),rnd(),rnd()};v[i].clip_rej=clip(v+i);
    }
}
int main(void) {
    assert(sizeof(struct LoadedVertex)==36 && offsetof(struct LoadedVertex,clip_rej)==32);
    struct LoadedVertex v[3];
    for(unsigned n=0;n<20000;n++) {init(v);compare(v);}
    /* All six planes, all inside/outside/on-plane combinations, both windings,
     * positive/negative/zero w and signed zero coordinates. */
    static const float ws[]={-4.0f,-0.0f,0.0f,1.0f,16.0f};
    for(unsigned p=0;p<6;p++)for(unsigned w=0;w<5;w++)for(unsigned t=0;t<125;t++) {
        init(v);unsigned code=t,axis=p<2?2:p<4?1:0;
        for(unsigned i=0;i<3;i++) {
            float xyz[3]={-0.0f,0.0f,0.0f};
            float d=(int)(code%5)-2;code/=5;
            xyz[axis]=((p&1)?-1:1)*(ws[w]+d*(1.0f/256.0f));
            xyz[(axis+1)%3]=(i==1?0.5f:-0.5f)*fabsf(ws[w]);
            v[i].x=xyz[0];v[i].y=xyz[1];v[i].z=xyz[2];
            v[i].w=ws[w];v[i].w_inv=ws[w]?1/ws[w]:0;v[i].clip_rej=clip(v+i);
        }
        compare(v);struct LoadedVertex tmp=v[1];v[1]=v[2];v[2]=tmp;compare(v);
    }
    /* Arbitrary clip-bit subsets, repeated/collinear vertices, and finite
     * powers-of-two scale extremes. All distance expressions stay unchanged. */
    for(unsigned mask=0;mask<64;mask++)for(unsigned n=0;n<32;n++) {
        init(v);for(unsigned i=0;i<3;i++)v[i].clip_rej=mask;
        if(n&1)v[1]=v[0];if(n&2)v[2]=v[1];compare(v);
    }
    for(int e=-20;e<=20;e++)for(unsigned n=0;n<64;n++) {
        init(v);float s=ldexpf(1.0f,e);
        for(unsigned i=0;i<3;i++) {
            v[i].x*=s;v[i].y*=s;v[i].z*=s;v[i].w*=s;
            v[i].w_inv=1/v[i].w;v[i].clip_rej=clip(v+i);
        }
        compare(v);
    }
    unsigned distinct=0;for(unsigned i=0;i<64;i++)distinct+=active_planes[i]!=0;
    assert(distinct==64 && empty_flags && multi_planes && discarded && unclipped && fans);
    printf("PASS exact clipped payload/fan order: cases=%u fans=%u words=%u all_masks=%u multi=%u no_clip=%u discarded=%u\n",
           cases,fans,fields,distinct,multi_planes,unclipped,discarded);
    return 0;
}
'''

def main():
    parent = BASE.read_text()
    candidate = (OUT/'gfx_pc.c').read_text()
    assert recipe.transform(parent)[0] == candidate
    out = OUT/'function-checks'
    out.mkdir(exist_ok=True)
    rgba = parent[parent.index('struct RGBA {'):parent.index('\n};', parent.index('struct RGBA {'))+3]
    vertex = parent[parent.index('struct LoadedVertex {'):parent.index('\n};', parent.index('struct LoadedVertex {'))+3]
    helpers = parent[parent.index('static inline rspv_t rspv_lerp('):parent.index('static inline bool gfx_clip_triangle(')]
    old = recipe.extract(parent)[2].replace('static inline bool gfx_clip_triangle(', 'static __attribute__((noinline)) bool reference_clip(')
    new = recipe.extract(candidate)[2].replace('static inline bool gfx_clip_triangle(', 'static __attribute__((noinline)) bool candidate_clip(')
    path = out/'functions.c'
    path.write_text(PRE+rgba+'\n'+vertex+'\n'+SINK+helpers+old+'\n'+new+'\n'+TEST)
    variants = [('strict', ['-O2','-ffp-contract=off']),
                ('fast', ['-O3','-ffast-math','-fno-math-errno','-fno-trapping-math','-fsingle-precision-constant']),
                ('sanitized', ['-O1','-ffp-contract=off','-fsanitize=address,undefined','-fno-omit-frame-pointer'])]
    results=[]
    for name, flags in variants:
        exe=out/name
        command=['gcc','-std=c11',*flags,str(path),'-lm','-o',str(exe)]
        subprocess.run(command, check=True)
        env=os.environ.copy()
        if name=='sanitized':
            # The harness has no allocation; LSAN cannot run under this
            # sandbox's ptrace. ASan bounds and UBSan remain enabled.
            env['ASAN_OPTIONS']='detect_leaks=0'
        run=subprocess.run([str(exe)],capture_output=True,text=True,env=env)
        (out/f'{name}.log').write_text(run.stdout+run.stderr)
        results.append(dict(name=name,command=command,returncode=run.returncode,output=run.stdout.strip(),binary_sha256=sha(exe)))
        assert run.returncode==0, (name,run.stdout,run.stderr)
        print(name,run.stdout.strip())
    (out/'check_clip.py').write_bytes(Path(__file__).read_bytes())
    record=dict(passed=True,parent_sha256=sha(BASE),candidate_sha256=sha(OUT/'gfx_pc.c'),
                actual_function_harness_sha256=sha(path),script_sha256=sha(Path(__file__)),runs=results,
                comparison='Exact return, output fan order/count, all7float payload bits and4color bytes',
                excluded='Unused scratch clip_rej and padding; FP exception flags are not a renderer output',
                target_status='RV32 execution/codegen not covered by host runs; functions.c is standalone SDK-app input')
    (out/'manifest.json').write_text(json.dumps(record,indent=2)+'\n')

if __name__=='__main__':
    main()
