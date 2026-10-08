#!/usr/bin/env python3
"""Actual direct-record/state/packet differential proof with mock transport."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from phase7_residency_tests import function

ROOT=Path(__file__).resolve().parents[3]
CAMP=ROOT/'docs/measurements/os30-fps25-20261005'
BASE=CAMP/'phase7/combined-v1'
DEST=CAMP/'phase9/direct-state-v1'
OUT=DEST/'state-tests'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

PREFIX=r'''
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <assert.h>
#define OF_PC 1
#define SM64_PERF_SCOPE(x)
#define SCR_W 320
#define FB_STRIDE SCR_W
#define ZBUF_ELEM 2
#define RAMP_W 256
#define ZI_SCALE 64
#define ZI_DERIVE_TARGET 8192.0f
#define VC_W_PRESCALE (1.0f/256.0f)
#define OF_GPU_SPAN_COLORMAP 1
#define OF_GPU_SPAN_BLEND 2
#define OF_GPU_SPAN_SKIP_ZERO 4
#define OF_GPU_SPAN_TRUECOLOR 128
#define GPU_CMD_SET_TRI_STATE 0x4A
#define GPU_CMD_DRAW_VERT_TRI_RGB 0x4E
enum {OF_GPU_PARAM_ATTR_PERSP=1,OF_GPU_PARAM_ATTR_PERSP_Q29=3,OF_GPU_PARAM_AXIS_X=0,
 OF_GPU_PARAM_RECORD_U16V16_COUNT16=0,OF_GPU_PARAM_Z_NONE=0,OF_GPU_PARAM_Z_WRITE_ZI=1,
 OF_GPU_PARAM_Z_TEST_ZI=2,OF_GPU_PARAM_Z_TEST_WRITE=3};
enum {SHADER_0,SHADER_INPUT_1,SHADER_INPUT_2,SHADER_INPUT_3,SHADER_INPUT_4,SHADER_TEXEL0,SHADER_TEXEL0A,SHADER_TEXEL1};
struct CCFeatures {uint8_t c[2][4];bool opt_alpha,opt_texture_edge;};
struct ShaderProgram {struct CCFeatures cc;};
static uint32_t staging[4096],transcript[65536],used,words,capacity,flushes,commands;
static int _gpu_span_hdr_valid;
static void flush(void){assert(words+used<65536);memcpy(transcript+words,staging,used*4);words+=used;used=0;flushes++;}
static void _gpu_cmd_header(uint8_t op,uint32_t n){if(used+n+1>capacity)flush();assert(n+1<=capacity);staging[used++]=((uint32_t)op<<24)|n;commands++;}
static uint32_t *_gpu_ring_claim(void){return staging+used;}
static void _gpu_ring_commit(uint32_t n){used+=n;assert(used<=capacity);}
static void of_gpu_kick(void){flush();}
'''

GLOBALS=r'''
static struct GTex textures[2],*g_cur_tex[2];
static struct ShaderProgram shader,*g_cur_shader;
static uint32_t g_tex_epoch,g_draw_fb,g_white_addr,g_ramp_addr;
static int g_fast_tex_active,g_st_cache_valid,g_tri_state_dirty,g_st_textured,g_st_cd,g_st_subpix,g_st_alpha,g_st_alpha_mask;
static int g_cd_active,g_subpix_tri,g_alpha_mask,g_truecolor,g_alpha_cvg,g_fb_bpp,g_z_test,g_z_write,g_has_gpu,g_vc_textured,g_vc_rgb_input;
static int16_t g_clip_x0,g_clip_x1,g_clip_y0,g_clip_y1;
static int32_t g_clip_cx,g_clip_cy,g_clip_hw,g_clip_hh;
static uint8_t g_surf_alpha;
static void *g_zbuf;
static unsigned g_vc_words,g_vc_tris_legacy,g_cd_fallbacks;
static of_gpu_tri_state_t g_st_cache;
'''

SUFFIX=r'''
struct result {uint32_t transcript[65536],words,flushes,commands,counters[3],epochs[4];int flags[9];of_gpu_tri_state_t cache;};
static void setup(unsigned variant) {
 memset(textures,0,sizeof(textures));memset(&shader,0,sizeof(shader));memset(&g_st_cache,0,sizeof(g_st_cache));
 memset(transcript,0,sizeof(transcript));used=words=flushes=commands=0;capacity=18+(variant%77);
 for(unsigned i=0;i<2;i++){
  textures[i].ci8=(uint8_t*)(uintptr_t)(0x10000000+4096*i);textures[i].cur=(variant>>8)&1;
  textures[i].w=16+i*9;textures[i].h=16+i*3;textures[i].wmask=i?65535:15;textures[i].hmask=i?65535:15;
  textures[i].cms=variant&3;textures[i].cmt=(variant>>2)&3;textures[i].addr=0x10000000+4096*i;
  for(unsigned j=0;j<2;j++) {textures[i].slot_epoch[j]=3+j;
   textures[i].fast_addr[j]=0x30000000+4096*i+1024*j;textures[i].fast_mask_addr[j]=0x31000000+4096*i+1024*j;
   textures[i].alpha_mask[j]=(uint8_t*)(uintptr_t)(0x11000000+4096*i+1024*j);}
 }
 shader.cc.opt_alpha=(variant>>3)&1;shader.cc.opt_texture_edge=(variant>>4)&1;
 if(variant&32){shader.cc.c[0][0]=SHADER_TEXEL0;shader.cc.c[0][2]=SHADER_TEXEL0A;shader.cc.c[0][1]=shader.cc.c[0][3]=SHADER_INPUT_1;}
 g_cur_shader=&shader;g_cur_tex[0]=&textures[0];g_cur_tex[1]=&textures[1];
 g_tex_epoch=17;g_draw_fb=0x12000000;g_white_addr=0x32000000;g_ramp_addr=0x13000000;g_zbuf=(void*)(uintptr_t)0x14000000;
 g_st_cache_valid=0;g_tri_state_dirty=1;g_st_textured=g_st_cd=g_st_subpix=g_st_alpha=g_st_alpha_mask=-1;
 g_fast_tex_active=(variant>>5)&1;g_cd_active=1;g_subpix_tri=0;g_alpha_mask=(variant>>6)&1;
 g_truecolor=(variant>>7)&1;g_alpha_cvg=(variant>>8)&1;g_fb_bpp=g_truecolor?2:1;
 g_z_test=(variant>>9)&1;g_z_write=(variant>>10)&1;g_has_gpu=1;g_vc_textured=(variant>>11)&1;g_vc_rgb_input=(variant&1)?0:-1;
 g_clip_x0=0;g_clip_x1=320;g_clip_y0=0;g_clip_y1=240;
 g_clip_cx=160;g_clip_cy=120*16;g_clip_hw=160;g_clip_hh=-120*16;
 g_surf_alpha=255;g_vc_words=19;g_vc_tris_legacy=3;g_cd_fallbacks=2;_gpu_span_hdr_valid=1;
}
static void collect(struct result*r){
 flush();memset(r,0,sizeof(*r));memcpy(r->transcript,transcript,words*4);r->words=words;r->flushes=flushes;r->commands=commands;
 r->counters[0]=g_vc_words;r->counters[1]=g_vc_tris_legacy;r->counters[2]=g_cd_fallbacks;
 for(unsigned i=0;i<2;i++)for(unsigned j=0;j<2;j++)r->epochs[2*i+j]=textures[i].slot_epoch[j];
 int f[]={g_st_cache_valid,g_tri_state_dirty,g_st_textured,g_st_cd,g_st_subpix,g_st_alpha,g_st_alpha_mask,g_surf_alpha,_gpu_span_hdr_valid};
 memcpy(r->flags,f,sizeof(f));r->cache=g_st_cache;
}
static void sequence(void(*draw)(const struct gfx_vc_vtx*,size_t),unsigned variant,struct result*r){
 setup(variant);struct gfx_vc_vtx vertices[24]={0};
 for(unsigned i=0;i<24;i++) {vertices[i].cx=(int)(i%3)*5-5;vertices[i].cy=(int)(i/3)-4;vertices[i].cw=16+i;
  vertices[i].w_inv=1.0f/vertices[i].cw;vertices[i].u=i*0.375f;vertices[i].v=3-i*0.25f;
  vertices[i].r=i*9;vertices[i].g=255-i*5;vertices[i].b=i*7;}
 unsigned lengths[]={0,8,8,8,1,8,8,0,8,8};
 for(unsigned step=0;step<10;step++) {
  unsigned base=(variant*17+step*23)&255;
  uint8_t alpha[]={base,base,(base+1)&255,(base+1)&255,0,0,base,255};
  for(unsigned i=0;i<8;i++)vertices[i*3].a=alpha[i];
  if(step==2){g_cur_tex[0]=&textures[1];g_tex_epoch++;g_tri_state_dirty=1;}
  if(step==3){g_cur_tex[0]->cur^=1;g_clip_x0++;g_tri_state_dirty=1;}
  if(step==4){g_cd_active=1;g_subpix_tri=0;g_surf_alpha=37;emit_tri_state(!g_vc_textured);}
  if(step==5){g_fast_tex_active^=1;g_white_addr^=0x01000000;g_tri_state_dirty=1;}
  if(step==6){g_z_write^=1;g_tri_state_dirty=1;}
  if(step==8)g_cur_shader=NULL;
  if(step==9){g_cur_shader=&shader;g_has_gpu=0;}
  draw(vertices,lengths[step]);
 }
 collect(r);
}
int main(void){
 static struct result a,b;uint64_t hash=1469598103934665603ull;unsigned cases=0;
 for(unsigned v=0;v<8192;v++){
  sequence(reference,v,&a);sequence(candidate,v,&b);
  if(memcmp(&a,&b,sizeof(a))){fprintf(stderr,"mismatch variant%u\n",v);return 1;}
  for(unsigned i=0;i<a.words;i++){hash^=a.transcript[i];hash*=1099511628211ull;}cases++;
 }
 printf("{\"passed\":true,\"cases\":%u,\"semantic_hash\":\"%016llx\"}\n",cases,(unsigned long long)hash);
}
'''


def main():
    OUT.mkdir(exist_ok=False)
    base=(BASE/'gfx_gpu.c').read_text();candidate=(DEST/'gfx_gpu.c').read_text();sdk=(DEST/'of_gpu.h').read_text()
    assert function(base,'emit_tri_state')==function(candidate,'emit_tri_state')
    state=re.search(r'typedef struct \{\n    uint32_t fb_base;\n    int32_t  fb_major_step;.*?\} of_gpu_tri_state_t;',sdk,re.S)[0]
    # The nearest opening before of_gpu_tri_state_t is its own typedef.
    end=sdk.index('} of_gpu_tri_state_t;')+len('} of_gpu_tri_state_t;')
    state=sdk[sdk.rfind('typedef struct {',0,end):end]
    tex=re.search(r'struct GTex \{.*?\n\};',base,re.S)[0]
    vertex=re.search(r'struct gfx_vc_vtx \{.*?\n\};',(DEST/'gfx_gpu.h').read_text(),re.S)[0]
    helpers='\n'.join(function(sdk,n) for n in ['of_gpu_set_tri_state','of_gpu_draw_vert_tri_rgb'])
    helpers+='\n'+'\n'.join(function(base,n) for n in ['gpu_tex_active_address','gpu_z_mode','cc_blend_texel_alpha',
        'emit_tri_state','gpu_q29_word','gpu_clip_reciprocal','gpu_project_clip','rgba_to_565'])
    reference=function(base,'gfx_gpu_draw_direct_records').replace('gfx_gpu_draw_direct_records(','reference(')
    actual=function(candidate,'gfx_gpu_draw_direct_records').replace('gfx_gpu_draw_direct_records(','candidate(')
    text=PREFIX+state+tex+vertex+GLOBALS+helpers+reference+actual+SUFFIX
    source=OUT/'state-test.c';source.write_text(text)
    runs={}
    for name,flags in [('checked',['-O2','-fsanitize=address,undefined','-fno-pie','-no-pie']),('fast',['-O3','-ffast-math'])]:
        exe=OUT/name;cmd=['cc','-std=c11',*flags,str(source),'-lm','-o',str(exe)]
        subprocess.run(cmd,check=True)
        proc=subprocess.run([str(exe)],capture_output=True,text=True,
            env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0',UBSAN_OPTIONS='halt_on_error=1'))
        (OUT/(name+'.stdout')).write_text(proc.stdout);(OUT/(name+'.stderr')).write_text(proc.stderr)
        assert proc.returncode==0,proc.stderr
        runs[name]=dict(command=cmd,result=json.loads(proc.stdout),executable_sha256=sha(exe))
    # The oracle must notice both missing initial state and missing alpha changes.
    negatives={}
    for label,condition in [('missing-initial','g_surf_alpha != v[0].a'),('missing-alpha','tri == 0')]:
        bad=actual.replace('tri == 0 || g_surf_alpha != v[0].a',condition)
        assert bad!=actual
        p=OUT/(label+'.c');p.write_text(text.replace(actual,bad));exe=OUT/label
        subprocess.run(['cc','-std=c11','-O2',str(p),'-lm','-o',str(exe)],check=True)
        result=subprocess.run([str(exe)],capture_output=True,text=True)
        (OUT/(label+'.stdout')).write_text(result.stdout);(OUT/(label+'.stderr')).write_text(result.stderr)
        assert result.returncode!=0,(label,'Oracle failed to detect mutation')
        negatives[label]=dict(detected=True,returncode=result.returncode,source_sha256=sha(p),executable_sha256=sha(exe))
    result=dict(passed=True,source_sha256={str(p.relative_to(ROOT)):sha(p) for p in
        [BASE/'gfx_gpu.c',DEST/'gfx_gpu.c',DEST/'gfx_gpu.h',DEST/'of_gpu.h',Path(__file__),source]},
        extraction_helper_sha256=sha(Path(__file__).with_name('phase7_residency_tests.py')),
        runs=runs,negative_controls=negatives,
        scope='Complete actual direct-record renderer, projection, material memo and SDK 0x4A/0x4E packet builders; mock staging transport with varied capacity, no actual CPU/GPU RTL.',
        coverage='8192 variants each: repeated/changed/reverting alpha including0/255, empty batches, first dirty state, texture slot/generation/route and viewport changes between batches, prior rectangle state, z changes, no shader/GPU; exact packets, texture epochs, final memo and counters.',
        invariants='Within each direct batch there is no texture upload, frame transition, material callback or route change. Existing SDK wait hook defaults to no-op; diagnostic hooks only update counters. First state call marks slot ownership.')
    (OUT/'RESULT.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(passed=True,runs=runs,negative_controls=negatives),indent=2))

if __name__=='__main__':main()
