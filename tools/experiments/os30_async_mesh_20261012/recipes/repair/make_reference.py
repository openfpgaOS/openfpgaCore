#!/usr/bin/env python3
"""Extract unchanged baseline clipping/projection/packing code for differential tests."""
import hashlib,json,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
BASE=HERE.parent.parent/'mesh-integrated-v1/work-app-v4/src'
PC=BASE/'sm64/sm64/src/pc/gfx/gfx_pc.c'
GPU=BASE/'sm64/sm64/src/pc/gfx/gfx_gpu.c'
HDR=BASE/'sm64/sm64/src/pc/gfx/gfx_gpu.h'
SDK=BASE/'sdk/include/of_gpu.h'
def block(s,start):
 a=s.index(start);b=s.index('{',a);depth=1;i=b+1
 while depth:
  depth+=(s[i]=='{')-(s[i]=='}');i+=1
 return s[a:i]
def main():
 out=Path(sys.argv[1]);pc=PC.read_text();gpu=GPU.read_text();sdk=SDK.read_text()
 fragments={
 'LoadedVertex':block(pc,'struct LoadedVertex {')+';',
 'gfx_vc_vtx':block(HDR.read_text(),'struct gfx_vc_vtx {')+';',
 'rspv_lerp':block(pc,'static inline rspv_t rspv_lerp'),
 'rgba_lerp':block(pc,'static inline struct RGBA rgba_lerp'),
 'clip':block(pc,'static inline bool gfx_clip_triangle'),
 'cull':block(pc,'static inline int gfx_cpu_batch_reject'),
 'prepare':block(pc,'static inline void gfx_cpu_prepare_clip'),
 'reciprocal':block(gpu,'static inline uint32_t gpu_clip_reciprocal'),
 'project':block(gpu,'static void gpu_project_clip'),
 'direct':block(gpu,'void gfx_gpu_draw_direct_records'),
 'pack':block(sdk,'static inline void of_gpu_draw_vert_tri_rgb'),
 }
 body=PREAMBLE+'\n'+fragments['LoadedVertex']+'\n'+fragments['gfx_vc_vtx']+'\n'+STATE+'\n'
 body+='\n'.join(fragments[k]for k in ['pack','reciprocal','project','direct','prepare','cull'])
 body+='\n'+PUSH+'\n'+'\n'.join(fragments[k]for k in ['rspv_lerp','rgba_lerp','clip'])+'\n'+TRANSFORM+'\n'+ENTRY
 out.write_text(body)
 record={'source_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest()for p in [PC,GPU,HDR,SDK,Path(__file__)]},'extracted_sha256':{k:hashlib.sha256(v.encode()).hexdigest()for k,v in fragments.items()},'generated_sha256':hashlib.sha256(body.encode()).hexdigest(),'scope':'Unmodified extracted baseline clip/cull/quantization/project/direct/SDK packing functions. Adapter replaces global material lookup with supplied uniform context and forces all three indexed loads for comparison. No renderer timing claim.'}
 out.with_suffix('.json').write_text(json.dumps(record,indent=2)+'\n')
PREAMBLE=r'''
#include "async_mesh_repair.h"
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <string.h>
#include <math.h>
typedef float rspv_t;
struct RGBA {uint8_t r,g,b,a;};
#define RSPV_MUL(a,b) ((a)*(b))
#define RSPV_DIV(a,b) ((a)/(b))
#define RSPV_RCP(a) (1.0f/(a))
#define RSPV_TO_INT(a) ((int)(a))
#define RSPV_FROM_INT(a) ((float)(a))
#define RSPV_TO_FLOAT(a) (a)
#define G_CULL_FRONT 1
#define G_CULL_BACK 2
#define G_CULL_BOTH 3
#define OF_PC 1
#define GPU_CMD_DRAW_VERT_TRI_RGB 0x4e
#define SM64_PERF_SCOPE(x) ((void)0)
#define VC_W_PRESCALE (1.0f/256.0f)
#define ZI_SCALE 64
#define ZI_DERIVE_TARGET 8192.0f
'''
STATE=r'''
static struct {unsigned geometry_mode;struct LoadedVertex loaded_vertices[32];} rsp;
static struct am_material material;
static uint32_t *output;static unsigned output_count;
static int g_has_gpu=1,g_cur_shader=1,g_vc_textured,g_vc_rgb_input=0;
static int g_cd_fallbacks,g_cd_active,g_subpix_tri,g_surf_alpha;
static uint32_t g_vc_words,g_vc_tris_legacy;
static int32_t g_clip_cx,g_clip_cy,g_clip_hw,g_clip_hh;
struct gfx_vc_prepared {int32_t x,y,w,u,v;uint32_t rgb,depth;};
static void emit_tri_state(int unused) {(void)unused;}
static uint16_t rgba_to_565(unsigned r,unsigned g,unsigned b) {return (r>>3)<<11|(g>>2)<<5|(b>>3);}
static uint32_t gpu_q29_word(const int32_t *x) {(void)x;return 0;}
static void of_gpu_kick(void) {}
static void _gpu_cmd_header(unsigned op,unsigned n) {output[output_count++]=op<<24|n;}
static uint32_t *_gpu_ring_claim(void) {return output+output_count;}
static void _gpu_ring_commit(unsigned n) {output_count+=n;}
'''
PUSH=r'''
/* Baseline eligibility plus the original payload expression. Material resolution
 * is intentionally uniform by admission contract; there are no shader calls. */
static int gfx_push_triangle(const struct LoadedVertex *a,const struct LoadedVertex *b,const struct LoadedVertex *c,bool ignored) {
 (void)ignored;const struct LoadedVertex *v[3]={a,b,c};
 struct gfx_vc_vtx records[3];unsigned indices[3];int direct=0;
 for(unsigned k=0;k<3;k++) {
  uintptr_t off=(uintptr_t)v[k]-(uintptr_t)rsp.loaded_vertices;
  if(off>=32*sizeof(struct LoadedVertex))direct=1;
  else indices[k]=off/sizeof(struct LoadedVertex);
  if(!(v[k]->w>0.0f&&v[k]->w<32767.0f))direct=1;
 }
 if(a->w==b->w&&a->w==c->w)direct=1;
 float uofs=(float)material.u_offset,vofs=(float)material.v_offset;
 float bias=(material.flags_rgb&(1u<<17))?0.5f:0.0f;
 for(unsigned k=0;k<3;k++) {
  records[k].cx=v[k]->x;records[k].cy=v[k]->y;records[k].cw=v[k]->w;records[k].w_inv=v[k]->w_inv;
  records[k].u=g_vc_textured?(v[k]->u-uofs)/32.0f+bias:0.0f;
  records[k].v=g_vc_textured?(v[k]->v-vofs)/32.0f+bias:0.0f;
  records[k].r=(material.flags_rgb&(1u<<18))?v[k]->color.r:(material.flags_rgb>>11&31)<<3;
  records[k].g=(material.flags_rgb&(1u<<18))?v[k]->color.g:(material.flags_rgb>>5&63)<<2;
  records[k].b=(material.flags_rgb&(1u<<18))?v[k]->color.b:(material.flags_rgb&31)<<3;
  records[k].a=material.alpha;
 }
 if(direct)gfx_gpu_draw_direct_records(records,1);
 else {
  for(unsigned k=0;k<3;k++) {
   struct gfx_vc_prepared p;gfx_cpu_prepare_clip(&p,v[k],records[k].u,records[k].v,rgba_to_565(records[k].r,records[k].g,records[k].b));
   output[output_count++]=0x56000008;output[output_count++]=indices[k];
   output[output_count++]=p.x;output[output_count++]=p.y;output[output_count++]=p.w;
   output[output_count++]=p.u;output[output_count++]=p.v;output[output_count++]=p.rgb;output[output_count++]=p.depth;
  }
  output[output_count++]=0x54000001;output[output_count++]=indices[0]|indices[1]<<5|indices[2]<<10;
 }
 return 1;
}
'''
ENTRY=r'''
unsigned repair_reference(const struct am_repair_job *j,uint32_t *words) {
 output=words;output_count=0;material=j->material;rsp.geometry_mode=j->cull;
 g_vc_textured=!!(material.flags_rgb&(1u<<16));g_clip_cx=material.xcenter;g_clip_cy=material.ycenter;g_clip_hw=material.xscale;g_clip_hh=material.yscale;
 for(unsigned i=0;i<j->count;i++) {
  const uint32_t *r=j->raw[i];struct LoadedVertex *v=&rsp.loaded_vertices[i];
  memcpy(&v->x,r,20);v->u=(float)(int16_t)r[5];v->v=(float)(int16_t)(r[5]>>16);
  memcpy(&v->color,r+6,4);v->clip_rej=r[7];
 }
 for(unsigned i=0;i<j->command_count;i++) {
  unsigned n=j->commands[i][0]>>24==6?2:1;
  for(unsigned k=0;k<n;k++) {
   uint32_t word=j->commands[i][k];struct LoadedVertex *a=&rsp.loaded_vertices[(word>>16&255)/2],*b=&rsp.loaded_vertices[(word>>8&255)/2],*c=&rsp.loaded_vertices[(word&255)/2];
   if(gfx_cpu_batch_reject(a,b,c))continue;
   if(!gfx_clip_triangle(a,b,c,a->clip_rej&b->clip_rej&c->clip_rej))gfx_push_triangle(a,b,c,false);
  }
 }
 return output_count;
}
'''
TRANSFORM=r'''
/* Small source extraction changes GCC register pressure and hence reassociation
 * under -ffast-math. Pin this oracle to the ORIGINAL ELF's instructions instead:
 * gfx_sp_vertex 104cff60..104cffb4; clip tests 104d0018..104d007a.
 * This is an independently assembled target oracle, not a host IEEE model. */
static float original_axis(float x,float y,float z,float a,float b,float c,float d) {
 float q;
 __asm__ volatile("fmul.s %0,%1,%2\nfmadd.s %0,%3,%4,%0\nfadd.s %0,%0,%5\nfmadd.s %0,%6,%7,%0"
  :"=&f"(q):"f"(b),"f"(y),"f"(a),"f"(x),"f"(d),"f"(c),"f"(z));return q;
}
static int original_lt(float a,float b) {int r;__asm__ volatile("flt.s %0,%1,%2":"=r"(r):"f"(a),"f"(b));return r;}
void repair_reference_transform(const struct am_repair_job *j,uint32_t out[32][8]) {
 float m[16],co[3],aspect_scale;memcpy(m,j->transform.matrix,64);memcpy(co,j->transform.coefficient,12);memcpy(&aspect_scale,&j->transform.aspect,4);
 const float m00=m[0],m01=m[1],m02=m[2],m03=m[3],m10=m[4],m11=m[5],m12=m[6],m13=m[7];
 const float m20=m[8],m21=m[9],m22=m[10],m23=m[11],m30=m[12],m31=m[13],m32=m[14],m33=m[15];
 for(unsigned i=0;i<j->count;i++) {
  const unsigned char *v=j->vertices+24*i;float ob[3];memcpy(ob,v,12);const float ox=ob[0],oy=ob[1],oz=ob[2];
  float x = original_axis(ox,oy,oz,m00,m10,m20,m30) * aspect_scale;
  float y = original_axis(ox,oy,oz,m01,m11,m21,m31);
  float z = original_axis(ox,oy,oz,m02,m12,m22,m32);
  float w = original_axis(ox,oy,oz,m03,m13,m23,m33);
  short uv[2];memcpy(uv,v+14,4);
  short U=uv[0]*(int32_t)(j->transform.scales&65535)>>16,V=uv[1]*(int32_t)(j->transform.scales>>16)>>16;
  int r=v[18],g=v[19],b=v[20];
  if(j->transform.lighting) {
   r=j->transform.ambient&255;g=j->transform.ambient>>8&255;b=j->transform.ambient>>16&255;
   float intensity=0;
   intensity+=(int8_t)v[18]*co[0];intensity+=(int8_t)v[19]*co[1];intensity+=(int8_t)v[20]*co[2];intensity/=127.0f;
   if(original_lt(0.0f,intensity)) {r+=intensity*(j->transform.diffuse&255);g+=intensity*(j->transform.diffuse>>8&255);b+=intensity*(j->transform.diffuse>>16&255);}
   r=r<0?0:r>255?255:r;g=g<0?0:g>255?255:g;b=b<0?0:b>255?255:b;
  }
  unsigned clip=0;if(original_lt(x,-w))clip|=32;if(original_lt(w,x))clip|=16;if(original_lt(y,-w))clip|=8;if(original_lt(w,y))clip|=4;if(original_lt(z,-w))clip|=2;if(original_lt(w,z))clip|=1;
  float wi=1.0f/w;memcpy(out[i],&x,4);memcpy(out[i]+1,&y,4);memcpy(out[i]+2,&z,4);memcpy(out[i]+3,&w,4);memcpy(out[i]+4,&wi,4);
  out[i][5]=(uint16_t)U|(uint32_t)(uint16_t)V<<16;out[i][6]=r|g<<8|b<<16|(uint32_t)v[21]<<24;out[i][7]=clip;
 }
}
'''
if __name__=='__main__':main()
