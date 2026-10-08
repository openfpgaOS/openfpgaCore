#!/usr/bin/env python3
"""Check the staged visibility helpers against independent corner oracles."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/os30-frontend-20261004'
GFX = OUT / 'SM64/src/sm64/sm64/src/pc/gfx'

PRE = r'''
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <string.h>
#include <math.h>
#include <assert.h>
#include <stdio.h>
#define OF_PC 1
#define SM64_PERF_SCOPE(x) ((void)0)
#define SM64_PERF_COUNT(x,y) ((void)0)
static int gfx_gpu_attribute_cull(void) {return 0;}
#define MAX_LIGHTS 2
#define G_CULL_FRONT 1
#define G_CULL_BACK 2
#define G_CULL_BOTH 3
#define G_ZBUFFER 4
#define G_FOG 8
#define G_TEXTURE_GEN 16
#define G_LIGHTING 32
#define LEVEL_UNKNOWN_3 3
#define CLIP_NEAR 1
#define CLIP_FAR 2
#define CLIP_TOP 4
#define CLIP_BOTTOM 8
#define CLIP_RIGHT 16
#define CLIP_LEFT 32
#define RSPV_FROM_INT(x) ((float)(x))
#define RSPV_RCP(x) (1.0f/(x))
typedef struct {float ob[3];uint16_t flag;int16_t tc[2];uint8_t cn[4];} Vtx_t;
typedef struct {float ob[3];uint16_t flag;int16_t tc[2];int8_t n[3];uint8_t a;} Vtx_tn;
typedef union {Vtx_t v;Vtx_tn n;uint64_t align;} Vtx;
typedef struct {uint8_t col[3],pad,colc[3],pad2;int8_t dir[3];uint8_t pad3;} Light_t;
struct RGBA {uint8_t r,g,b,a;};
struct LoadedVertex {float x,y,z,w,w_inv,u,v;struct RGBA color;unsigned clip_rej;};
static struct {
    float MP_matrix[4][4],current_lights_coeffs[MAX_LIGHTS][3];
    Light_t current_lights[MAX_LIGHTS+1];
    unsigned current_num_lights,geometry_mode;
    struct {uint16_t s,t;} texture_scaling_factor;
    int16_t fog_mul,fog_offset;
    struct LoadedVertex loaded_vertices[64];
} rsp;
static bool configEnableFog;
static uint32_t vertex_state_epoch=1;
static unsigned gCurrLevelNum=9;
static int gfx_gpu_geometry_enabled(void) {return 1;}
'''

TEST = r'''
static uint32_t random_state=0x19c294f7;
static uint32_t random32(void) {
    random_state^=random_state<<13;random_state^=random_state>>17;
    random_state^=random_state<<5;return random_state;
}
static float range(float a,float b) {return a+(b-a)*(random32()*(1.0f/4294967296.0f));}

/* Evaluate all corners directly from the MVP, without the cached planes,
 * selected support corners used by the implementation. */
static void exact_corners(const float box[6],float aspect,int result) {
    for(unsigned corner=0;corner<8;corner++) {
        double position[4]={box[(corner>>0)&1],box[2+((corner>>1)&1)],box[4+((corner>>2)&1)],1};
        double clip[4]={0};
        for(unsigned row=0;row<4;row++)for(unsigned col=0;col<4;col++)
            clip[col]+=position[row]*(double)rsp.MP_matrix[row][col];
        clip[0]*=aspect;
        double x=clip[0],y=clip[1],z=clip[2],w=clip[3];
        if(result==0) {
            assert(w>4 && w<32760);
            assert(4*w-x>2 && 4*w+x>2 && 4*w-y>2 && 4*w+y>2);
            assert(w-z>2 && w+z>2);
        } else {
            double plane=result==CLIP_RIGHT?w-x:result==CLIP_LEFT?w+x:
                         result==CLIP_TOP?w-y:result==CLIP_BOTTOM?w+y:
                         result==CLIP_NEAR?w-z:w+z;
            assert(plane < -2);
        }
    }
}


static Vtx immutable_vertices[40000];
static unsigned cache_checks;
static void check_inputs(const Vtx *vertices,unsigned count) {
    float temporary[6];const float *box=geometry_bounds(vertices,count,temporary);
    float lo[3]={vertices[0].v.ob[0],vertices[0].v.ob[1],vertices[0].v.ob[2]};
    float hi[3]={lo[0],lo[1],lo[2]};
    assert(geometry_inputs_valid);
    for(unsigned i=0;i<count;i++) {
        for(unsigned k=0;k<3;k++) {
            float value=vertices[i].v.ob[k];
            assert(geometry_inputs[i].v.ob[k]==value);
            if(value<lo[k])lo[k]=value;if(value>hi[k])hi[k]=value;
        }
        assert(geometry_inputs[i].v.tc[0]==vertices[i].v.tc[0]);
        assert(geometry_inputs[i].v.tc[1]==vertices[i].v.tc[1]);
        assert(memcmp(geometry_inputs[i].v.cn,vertices[i].v.cn,4)==0);
    }
    for(unsigned k=0;k<3;k++)assert(box[2*k]==lo[k]&&box[2*k+1]==hi[k]);
    cache_checks++;
}
static void cache_tests(void) {
    test_ro_begin=(uintptr_t)immutable_vertices;
    test_ro_end=(uintptr_t)(immutable_vertices+40000);
    for(unsigned i=0;i<40000;i++) {
        for(unsigned k=0;k<3;k++)immutable_vertices[i].v.ob[k]=(int)(random32()%65536)-32768;
        for(unsigned k=0;k<2;k++)immutable_vertices[i].v.tc[k]=random32();
        for(unsigned k=0;k<4;k++)immutable_vertices[i].v.cn[k]=random32();
    }
    unsigned pool_recycles=0,table_recycles=0;
    /* 32 vertices exhaust the input pool; eight exhaust the hash table first.
     * Alternate source/count keys and revisit entries through collisions. */
    for(unsigned pass=0;pass<4;pass++) {
        unsigned count=(pass&1)?8:32;
        memset(geometry_bounds_cache,0,sizeof(geometry_bounds_cache));geometry_input_used=0;
        for(unsigned i=0;i<1200;i++) {
            unsigned used=geometry_input_used;
            check_inputs(immutable_vertices+i*count,count);
            if(geometry_input_used<used) {
                if(count==32)pool_recycles++;else table_recycles++;
            }
            used=geometry_input_used;
            const GeometryInput *input=geometry_inputs;
            check_inputs(immutable_vertices+i*count,count);
            assert(geometry_input_used==used&&geometry_inputs==input);
            if(i%19==0)check_inputs(immutable_vertices+i*count,count-1);
        }
    }
    assert(pool_recycles>=4&&table_recycles>=2);
    /* The complete source must be inside the read-only range. */
    geometry_input_used=0;memset(geometry_bounds_cache,0,sizeof(geometry_bounds_cache));
    check_inputs(immutable_vertices+39996,4);assert(geometry_input_used==4);
    test_ro_end=(uintptr_t)(immutable_vertices+39998);
    check_inputs(immutable_vertices+39996,4);assert(geometry_input_used==4);
    /* Invalid immutable entries preserve validity on a cache hit. */
    immutable_vertices[0].v.ob[0]=0.5f;
    float box[6];geometry_bounds(immutable_vertices,8,box);assert(!geometry_inputs_valid);
    unsigned used=geometry_input_used;
    geometry_bounds(immutable_vertices,8,box);assert(!geometry_inputs_valid&&used==geometry_input_used);
    printf("PASS: %u immutable input checks; %u pool and %u table recycles, cache hits and full-range checks\n",
           cache_checks,pool_recycles,table_recycles);
}
int main(void) {
    cache_tests();
    unsigned checked=0,raw=0,outside=0,fallback=0;
    rsp.geometry_mode=G_ZBUFFER;
    for(unsigned trial=0;trial<1500;trial++) {
        float box[6];Vtx corners[8]={0};
        for(unsigned k=0;k<3;k++) {
            int center=(trial&1)?(int)range(-12000,12000):0;
            int extent=(trial&1)?(int)range(1,1000):(int)range(1,40);
            box[2*k]=center-extent;box[2*k+1]=center+extent;
        }
        for(unsigned corner=0;corner<8;corner++)for(unsigned k=0;k<3;k++)
            corners[corner].v.ob[k]=box[2*k+((corner>>k)&1)];
        float yaw=range(-3.1f,3.1f),pitch=range(-1.4f,1.4f);
        float tx=(trial&1)?range(-6000,6000):0;
        float ty=(trial&1)?range(-6000,6000):0;
        float tz=(trial&1)?range(-24000,10000):range(-16000,-1000);
        float scale=range(0.01f,8.0f),depth=(trial&1)?range(1.0001f,1.2f):1.00004f;
        float near=range(40,400);
        for(unsigned step=0;step<96;step++) {
            yaw+=range(-0.004f,0.004f);pitch+=range(-0.004f,0.004f);
            tx+=range(-12,12);ty+=range(-12,12);tz+=range(-12,12);
            if(step==48) {yaw+=0.7f;tz+=6000;}
            float cy=cosf(yaw),sy=sinf(yaw),cp=cosf(pitch),sp=sinf(pitch);
            float rotation[3][3]={{cy,sy*sp,sy*cp},{0,cp,-sp},{-sy,cy*sp,cy*cp}};
            memset(rsp.MP_matrix,0,sizeof(rsp.MP_matrix));
            for(unsigned k=0;k<3;k++) {
                rsp.MP_matrix[k][0]=rotation[k][0]*scale*1.8f;
                rsp.MP_matrix[k][1]=rotation[k][1]*scale*2.4f;
                rsp.MP_matrix[k][2]=-rotation[k][2]*scale*depth;
                rsp.MP_matrix[k][3]=-rotation[k][2]*scale;
            }
            rsp.MP_matrix[3][0]=tx*1.8f;rsp.MP_matrix[3][1]=ty*2.4f;
            rsp.MP_matrix[3][2]=-tz*depth-near;rsp.MP_matrix[3][3]=-tz;
            float aspect=step<72?1.0f:1.2f;
            ++geometry_mvp_epoch;++vertex_state_epoch;
            vertex_cull_release(0,32);
            int result=geometry_prepare(corners,8,0,aspect);
            if(result==1) {
                assert(!geometry_outside(box));
                for(unsigned p=0;p<8;p++) {
                    const struct GeometryPlane *plane=&geometry_planes[p];
                    float minimum=box[plane->corner[0]]*plane->c[0]
                        +box[plane->corner[1]]*plane->c[1]+box[plane->corner[2]]*plane->c[2]+plane->c[3];
                    assert(minimum>0.0f);
                }
                exact_corners(box,aspect,0);raw++;
            }
            else if(result==2) {exact_corners(box,aspect,rsp.loaded_vertices[0].clip_rej);outside++;}
            else fallback++;
            checked++;
        }
    }
    assert(raw>10000&&outside>10000&&fallback>500);
    /* Mutable inputs are rescanned. Fractions and out-of-range positions must
     * not silently truncate into the hardware's signed 16-bit coordinates. */
    Vtx vertices[32]={0};float box[6];
    for(unsigned i=0;i<32;i++)vertices[i].v.ob[2]=i;
    geometry_bounds(vertices,32,box);assert(geometry_inputs_valid&&box[5]==31);
    vertices[31].v.ob[2]=99;geometry_bounds(vertices,32,box);assert(box[5]==99);
    vertices[0].v.ob[0]=0.5f;geometry_bounds(vertices,32,box);assert(!geometry_inputs_valid);
    vertices[0].v.ob[0]=32768;geometry_bounds(vertices,32,box);assert(!geometry_inputs_valid);
    vertices[0].v.ob[0]=-32768;geometry_bounds(vertices,32,box);assert(geometry_inputs_valid);

    /* A skipped batch can be used by a later mixed triangle. Recover its
     * original vertices, lighting, texture scale and fog after state changes. */
    memset(&rsp,0,sizeof(rsp));memset(vertices,0,sizeof(vertices));
    rsp.MP_matrix[0][0]=rsp.MP_matrix[1][1]=rsp.MP_matrix[2][2]=1;
    rsp.MP_matrix[3][0]=-5000;rsp.MP_matrix[3][3]=1000;
    rsp.geometry_mode=G_LIGHTING|G_FOG;rsp.current_num_lights=2;
    rsp.current_lights[0].col[0]=200;rsp.current_lights[1].col[1]=40;
    rsp.current_lights_coeffs[0][0]=1;
    rsp.texture_scaling_factor.s=rsp.texture_scaling_factor.t=65535;
    rsp.fog_mul=100;rsp.fog_offset=70;configEnableFog=true;
    vertices[0].n.n[0]=127;vertices[0].v.tc[0]=32;vertices[0].v.tc[1]=-64;
    vertex_cull_release(0,8);++vertex_state_epoch;
    vertex_cull_capture(vertices,8,0,1,CLIP_LEFT);
    memset(vertices,0xff,sizeof(vertices));memset(&rsp,0,sizeof(rsp));
    ++vertex_state_epoch;configEnableFog=false;
    vertex_cull_materialize(0);
    assert(rsp.loaded_vertices[0].x==-5000&&rsp.loaded_vertices[0].w==1000);
    assert(rsp.loaded_vertices[0].u==31&&rsp.loaded_vertices[0].v==-64);
    assert(rsp.loaded_vertices[0].color.r==200&&rsp.loaded_vertices[0].color.g==40);
    assert(rsp.loaded_vertices[0].color.a==70&&rsp.loaded_vertices[0].clip_rej==CLIP_LEFT);
    vertex_cull_release(0,32);assert(culled_pending==0);
    for(unsigned i=0;i<32;i++)assert(culled_contexts[i].owners==0);
    printf("PASS: %u moving frusta, %u raw, %u outside, %u fallback; mutable inputs and deferred state recovery\n",checked,raw,outside,fallback);
}
'''


def main():
    dest = OUT / 'visibility-checks'
    dest.mkdir(exist_ok=True)
    headers = ['gfx_vertex_cull.h', 'gfx_geometry.h']
    # Substitute only the ELF address range in the host fixture, retaining the
    # actual production cache, classification and vertex recovery functions.
    fixture = PRE.replace('#define OF_PC 1', '')
    fixture += '\nstatic uintptr_t test_ro_begin,test_ro_end;\n'
    fixture += 'static void gfx_materialize_vertex_attributes(unsigned slot) {(void)slot;}\n'
    production = ''.join((GFX / name).read_text() for name in headers)
    production = production.replace('(uintptr_t)&_start', 'test_ro_begin')
    production = production.replace('(uintptr_t)__EH_FRAME_BEGIN__', 'test_ro_end')
    source = fixture + production + TEST
    c = dest / 'visibility.c'
    c.write_text(source)
    exe = dest / 'visibility'
    cmd = ['cc', '-std=gnu11', '-O2', '-g', '-fsanitize=address,undefined',
           '-fno-omit-frame-pointer', str(c), '-lm', '-o', str(exe)]
    subprocess.run(cmd, check=True)
    env = dict(os.environ, ASAN_OPTIONS='detect_leaks=0')
    result = subprocess.run([str(exe)], env=env, capture_output=True, text=True)
    if result.returncode:
        print(result.stdout, result.stderr)
        result.check_returncode()
    report = dict(result=result.stdout.strip(), command=cmd,
                  host_substitution="ELF immutable address range replaced with fixture array",
                  source_sha256={n: hashlib.sha256((GFX / n).read_bytes()).hexdigest() for n in headers})
    (OUT / 'visibility-checks.json').write_text(json.dumps(report, indent=2) + '\n')
    print(result.stdout, end='')


if __name__ == '__main__':
    main()
