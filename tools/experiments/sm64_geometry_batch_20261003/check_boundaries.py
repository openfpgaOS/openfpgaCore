#!/usr/bin/env python3
"""Compile the actual staged batch/geometry functions with a capture backend.

Checks whole-opcode consumption, alpha/fallback boundaries, the 64-triangle
limit, partial slot ownership and mutable bounds. RTL replay checks rendering.
The host build disables the ELF-only immutable cache; ELF placement and its
output are checked separately using the linked RISC-V app and scene capture.
"""
from pathlib import Path
import hashlib,json,os,subprocess
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/sm64-geometry-batch-20261003'
GFX=OUT/'SM64/src/sm64/sm64/src/pc/gfx'
s=(GFX/'gfx_pc.c').read_text()
front=s[s.index('static unsigned gfx_geometry_decode('):s.index('#endif\n\n#define C0')]
backend=(GFX/'gfx_gpu.c').read_text()
backend=backend[backend.index('void gfx_gpu_geometry_batch('):backend.index('/* Match the GPU\'s quantization')]
pre=r'''
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <string.h>
#include <math.h>
#include <assert.h>
#include <stdio.h>
#define OF_PC 1
#define F3DEX_GBI_2 1
#define F3DEX_GBI 1
#define G_TRI1 5
#define G_TRI2 6
#define G_CULL_FRONT 1
#define G_CULL_BACK 2
#define G_CULL_BOTH 3
#define G_ZBUFFER 4
#define G_FOG 8
#define G_TEXTURE_GEN 16
#define LEVEL_UNKNOWN_3 3
#define G_MDSFT_TEXTFILT 12
#define G_TF_POINT 0
#define GFX_VC_TEXTURED 2
#define CC_LOD 8
#define RSPV_RCP(x) (1.0f/(x))
#define RSPV_TO_FLOAT(x) (x)
typedef struct {struct {int16_t ob[3];uint8_t rest[10];} v;} Vtx;
typedef struct {struct {uint32_t w0,w1;} words;} Gfx;
struct RGBA {uint8_t r,g,b,a;};
struct LoadedVertex {float x,y,z,w,w_inv,u,v;struct RGBA color;unsigned clip_rej;};
struct {float MP_matrix[4][4];unsigned geometry_mode;struct LoadedVertex loaded_vertices[64];} rsp;
struct {struct {int uls,ult;} texture_tile;unsigned other_mode_h;} rdp;
struct ColorCombiner {uint8_t shader_input_mapping[2][4];};
struct gfx_geometry_upload {uint32_t words[5];const int32_t *matrix;uint32_t serial;};
unsigned gCurrLevelNum=9;
static int gfx_gpu_geometry_enabled(void) {return 1;}
'''
post=r'''
static struct ColorCombiner material;
static int rgb_input=-1,alpha_input=-1,eligibility=1;
static bool fog;
static uint32_t vc_slot_stale;
static int buf_vbo_len,flushes,batches;
static unsigned saved_count,saved_tris;
static uint8_t saved_alpha;
static struct gfx_geometry_upload saved_upload[32];
static uint32_t saved_index[64];
static struct ColorCombiner *gfx_prepare_triangle_material(bool *f,bool *a,bool *t) {
    *f=fog;*a=false;*t=(eligibility&2)!=0;return &material;
}
static int gfx_gpu_vtx_cache_begin(int *r,int *a) {*r=rgb_input;*a=alpha_input;return eligibility;}
static const struct RGBA *gfx_vc_resolve(unsigned c,const struct LoadedVertex *v,
        const struct LoadedVertex *first,struct RGBA *tmp) {(void)c;(void)first;(void)tmp;return &v->color;}
static bool gfx_vc_texture_state(const struct ColorCombiner *c,int t,float b) {(void)c;(void)t;(void)b;return false;}
static void gfx_flush(void) {flushes++;buf_vbo_len=0;}
static void gfx_gpu_geometry_batch(const struct gfx_geometry_upload *u,unsigned n,
                                  const uint32_t *tri,unsigned nt,uint8_t a) {
    assert(n<=32&&nt<=64);batches++;saved_count=n;saved_tris=nt;saved_alpha=a;
    memcpy(saved_upload,u,n*sizeof(*u));memcpy(saved_index,tri,nt*sizeof(*tri));
}
'''
tests=r'''
static uint32_t tri(unsigned a,unsigned b,unsigned c) {return (a<<17)|(b<<9)|(c<<1);}
static void reset(void) {
    memset(&rsp,0,sizeof(rsp));memset(&rdp,0,sizeof(rdp));memset(&material,0,sizeof(material));
    memset(geometry_contexts,0,sizeof(geometry_contexts));
    geometry_valid=vc_slot_stale=0xffffffffu;geometry_pending=0;
    batches=flushes=buf_vbo_len=0;rgb_input=alpha_input=-1;eligibility=1;fog=false;
    for(unsigned i=0;i<32;i++) {
        rsp.loaded_vertices[i].color=(struct RGBA){255,128,64,255};
        rsp.loaded_vertices[i].u=32; rsp.loaded_vertices[i].v=64;
        geometry_vertices[i].context=i/8;
        geometry_vertices[i].pos[0]=i;geometry_vertices[i].pos[1]=-2;geometry_vertices[i].pos[2]=3;
        geometry_contexts[i/8].serial=42+i/8;
    }
}
int main(void) {
    Gfx dl[67]={0};
    reset();for(unsigned i=0;i<65;i++)dl[i].words.w0=(G_TRI1<<24)|tri(0,1,2);
    buf_vbo_len=3;
    assert(gfx_geometry_run(dl)==dl+64&&batches==1&&saved_tris==64&&saved_count==3&&flushes==1);
    assert(gfx_geometry_run(dl+64)==dl+65&&batches==2&&saved_count==0&&saved_tris==1);
    assert(vc_slot_stale==0xfffffff8u);
    reset();memset(dl,0,sizeof(dl));alpha_input=0;
    rsp.loaded_vertices[0].color.a=30;rsp.loaded_vertices[3].color.a=60;
    dl[0].words.w0=(G_TRI1<<24)|tri(0,1,2);
    dl[1].words.w0=(G_TRI2<<24)|tri(0,1,2);dl[1].words.w1=tri(3,4,5);
    assert(gfx_geometry_run(dl)==dl+1&&batches==1&&saved_alpha==30&&saved_tris==1);
    assert(gfx_geometry_run(dl+1)==dl+1&&batches==1); /* TRI2 remains whole. */
    dl[1].words.w0=(G_TRI1<<24)|tri(3,4,5);
    assert(gfx_geometry_run(dl+1)==dl+2&&batches==2&&saved_alpha==60&&saved_tris==1);
    reset();geometry_valid&=~(1u<<5);
    assert(gfx_geometry_run(dl)==dl+1&&batches==1&&saved_tris==1);
    reset();rsp.geometry_mode=G_CULL_BOTH;
    assert(gfx_geometry_run(dl)==dl+2&&batches==0&&vc_slot_stale==0xffffffffu);
    reset();memset(dl,0,sizeof(dl));dl[0].words.w0=(G_TRI1<<24)|tri(0,9,18);eligibility=3;rgb_input=0;
    assert(gfx_geometry_run(dl)==dl+1&&saved_count==3&&saved_tris==1);
    assert(saved_index[0]==(0|(9<<5)|(18<<10)));
    for(unsigned i=0;i<3;i++) {
        unsigned slot=9*i;assert(saved_upload[i].words[0]==slot);
        assert(saved_upload[i].serial==42+slot/8);
        assert(saved_upload[i].words[1]==(slot|0xfffe0000u));
        assert(saved_upload[i].words[2]==(3|0xfc080000u));
        assert(saved_upload[i].words[3]==65536&&saved_upload[i].words[4]==131072);
    }
    reset();eligibility=0;assert(gfx_geometry_run(dl)==dl&&batches==0);
    reset();fog=true;assert(gfx_geometry_run(dl)==dl&&batches==0);
    reset();rgb_input=0;material.shader_input_mapping[0][0]=CC_LOD;
    assert(gfx_geometry_run(dl)==dl&&batches==0);
    reset();memset(geometry_contexts,0,sizeof(geometry_contexts));
    geometry_valid=geometry_pending=0;geometry_cached_context=-1;geometry_context_next=0;
    rsp.geometry_mode=G_ZBUFFER;rsp.MP_matrix[0][0]=rsp.MP_matrix[1][1]=1;
    rsp.MP_matrix[2][3]=0.01f;rsp.MP_matrix[3][3]=100;geometry_mvp_epoch++;
    Vtx vertices[32]={0};for(unsigned i=0;i<32;i++)vertices[i].v.ob[2]=i;
    assert(geometry_prepare(vertices,8,0,1)==1);unsigned c0=geometry_vertices[0].context;
    assert(geometry_prepare(vertices,8,4,1)==1&&geometry_valid==0xfff&&geometry_contexts[c0].owners==0xfff);
    rsp.MP_matrix[3][0]=5;geometry_mvp_epoch++;
    assert(geometry_prepare(vertices,8,8,1)==1);unsigned c1=geometry_vertices[8].context;
    assert(c1!=c0&&geometry_contexts[c0].owners==0xff&&geometry_contexts[c1].owners==0xff00);
    rsp.geometry_mode|=G_FOG;
    assert(geometry_prepare(vertices,8,4,1)==0&&geometry_valid==0xf00f);
    assert(geometry_contexts[c0].owners==0xf&&geometry_contexts[c1].owners==0xf000);
    assert(geometry_prepare(vertices,32,0,1)==0&&geometry_valid==0&&geometry_pending==0);
    assert(geometry_contexts[c0].owners==0&&geometry_contexts[c1].owners==0);
    float box[6];geometry_bounds(vertices,32,box);assert(box[5]==31);
    vertices[31].v.ob[2]=99;geometry_bounds(vertices,32,box);assert(box[5]==99);
    puts("PASS: raw batch boundaries, alpha splits, matrix ownership, mutable bounds");
}
'''
# Separate capture of the real wire emitter. No frontend mocks are used here.
wire_pre=r'''
#include <stdint.h>
#include <stdbool.h>
#include <math.h>
#include <assert.h>
#include <stdio.h>
struct gfx_geometry_upload {uint32_t words[5];const int32_t *matrix;uint32_t serial;};
static int g_cd_active,g_subpix_tri,g_surf_alpha,g_vc_textured;
static int g_geometry_matrix_valid,g_vc_vp_valid;
static uint32_t g_geometry_serial,g_vc_words,g_vc_loads,g_vc_tris_cached,g_vc_kick;
static float g_vc_vp[4],g_cx=160,g_cy=120,g_hw=160,g_hh=-120;
static uint32_t stream[4095],reserved,committed;static int kicks,states;
static void emit_tri_state(int t) {(void)t;states++;}
static void _gpu_stream_reserve_words(unsigned n) {assert(n<=4095);reserved=n;}
static uint32_t *_gpu_ring_claim(void) {return stream;}
static void _gpu_ring_commit(unsigned n) {assert(n==reserved);committed=n;}
static void of_gpu_kick(void) {kicks++;}
'''
wire_tests=r'''
int main(void) {
    struct gfx_geometry_upload u[32];int32_t matrix[32][12];uint32_t tri[64];
    for(unsigned i=0;i<32;i++) {
        u[i].serial=i+1;u[i].matrix=matrix[i];
        for(unsigned k=0;k<12;k++)matrix[i][k]=(i+1)*100+k;
        u[i].words[0]=i;for(unsigned k=1;k<5;k++)u[i].words[k]=i*10+k;
    }
    for(unsigned i=0;i<64;i++)tri[i]=(i%30)|((i%30+1)<<5)|((i%30+2)<<10)|(2<<15);
    gfx_gpu_geometry_batch(u,32,tri,64,123);
    assert(committed==1184&&states==1&&g_surf_alpha==123&&kicks==1);
    unsigned p=0,load=0,draw=0,bound=0;
    while(p<committed) {
        unsigned header=stream[p++],op=header>>24,n=header&0xffffffu;
        assert(p+n<=committed);
        if(op==0x50) {
            assert(n==26);for(unsigned k=0;k<12;k++)assert(stream[p+k]==(bound+1)*100+k);
            assert(stream[p+20]==160&&stream[p+21]==1920&&stream[p+25]==3);bound++;
        } else if(op==0x59) {
            assert(n==5);for(unsigned k=0;k<5;k++)assert(stream[p+k]==u[load].words[k]);load++;
        } else {assert(op==0x5b&&n==1&&stream[p]==tri[draw]);draw++;}
        p+=n;
    }
    assert(load==32&&draw==64&&bound==32);
    gfx_gpu_geometry_batch(u,0,tri,1,255);assert(committed==2&&stream[0]==0x5b000001u);
    for(unsigned i=0;i<32;i++)u[i].serial=32; /* Already bound: groups of 3, last 2. */
    gfx_gpu_geometry_batch(u,32,tri,1,255);assert(committed==173);
    p=0;load=0;unsigned commands=0;
    while((stream[p]>>24)==0x59) {
        unsigned n=stream[p++]&0xffffffu;assert(n==15||n==10);load+=n/5;p+=n;commands++;
    }
    assert(commands==11&&load==32&&p+2==committed);
    puts("PASS: maximum reservation, matrix switches, 3-vertex ABI, zero uploads, draw order");
}
'''
OUT.mkdir(exist_ok=True)
records=[]
for name,code in [('boundaries',pre+(GFX/'gfx_geometry.h').read_text()+post+front+tests),
                  ('wire',wire_pre+backend+wire_tests)]:
    c=OUT/f'test-{name}.c';exe=OUT/f'test-{name}';c.write_text(code)
    subprocess.run(['cc','-std=c99','-O2','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer',str(c),'-lm','-o',str(exe)],check=True)
    result=subprocess.run([str(exe)],capture_output=True,text=True,env={**os.environ,"ASAN_OPTIONS":"detect_leaks=0"})
    if result.returncode:
        print(result.stdout,result.stderr);result.check_returncode()
    print(result.stdout,end='');records.append(dict(test=name,output=result.stdout))
(OUT/'boundary-checks.json').write_text(json.dumps(dict(tests=records,source_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (GFX/n for n in ('gfx_pc.c','gfx_gpu.c','gfx_gpu.h','gfx_geometry.h'))}),indent=2)+'\n')
