#!/usr/bin/env python3
"""Exact copied-source transform-cache checks, independent of scene replay."""
from common import *
import re,subprocess,os,argparse

p=argparse.ArgumentParser(description=__doc__);p.add_argument('label',choices=['position-memo-v3','position-memo-v4']);a=p.parse_args()
candidate=OUT.parent/a.label/'gfx_pc.c';baseline=OUT.parent/'baseline/gfx_pc.c'
assert sha(candidate)=={'position-memo-v3':'ef7227b8287daefc14522bca8d20c2f2f1a80b8c7436c5a928809d6813a21cbd','position-memo-v4':'0dedf26abc01e2d28a1f0623a603ad0ae70cf7041615223bdd011d49b9e77dbc'}[a.label]
b=baseline.read_text();c=candidate.read_text();out=OUT/(a.label+'-focused');out.mkdir(exist_ok=False)
(out/'check_position_memo.py').write_text(Path(__file__).read_text())
def section(text,start,end):
 assert text.count(start)==1,start
 return text.split(start,1)[1].split(end,1)[0]
struct='struct PositionMemo {'+section(c,'struct PositionMemo {','\n#endif')
if 'static inline uint32_t gfx_position_bits' in c:
 struct='static inline uint32_t gfx_position_bits'+section(c,'static inline uint32_t gfx_position_bits','struct PositionMemo {')+struct
init='    uint32_t position_aspect;'+section(c,'    uint32_t position_aspect;','\n#endif')
matrix='    const float m00'+section(b,'    const float m00','    for (size_t i = 0; i < n_vertices; i++, dest_index++)')
transform_b='        const float ox = v->ob[0], oy = v->ob[1], oz = v->ob[2];'+section(b,'        const float ox = v->ob[0], oy = v->ob[1], oz = v->ob[2];','        short U')
transform_c='        float x, y, z, w, position_w_inv;'+section(c,'        float x, y, z, w, position_w_inv;','        short U')
finish_b='        // trivial clip rejection'+section(b,'        // trivial clip rejection','        if (configEnableFog')
finish_c='        // trivial clip rejection'+section(c,'        // trivial clip rejection','        if (configEnableFog')
attributes_b=section(b,'        short U','        // trivial clip rejection')
attributes_c=section(c,'        short U','        // trivial clip rejection')
assert attributes_b==attributes_c,'UV, color or lighting work changed'
fog_b=section(b,'        if (configEnableFog','\n    }\n    }\n#ifdef TARGET_POCKET')
fog_c=section(c,'        if (configEnableFog','\n    }\n    }\n#ifdef TARGET_POCKET')
assert fog_b==fog_c,'Fog or alpha work changed'
enums='enum {\n'+section(b,'enum {\n','\n};')+'\n};\n'
src='''#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>
#include <math.h>
#include <assert.h>
#define TARGET_OPENFPGA 1
#define GBI_FLOATS 1
#define RSPV_RCP(x) (1.0f/(x))
typedef struct {float ob[3];short tc[2];unsigned char cn[4];} Vtx_t;
struct LoadedVertex {float x,y,z,w,w_inv;float u,v;unsigned char color[4],clip_rej;};
static struct {float MP_matrix[4][4];} rsp;
static uint32_t geometry_mvp_epoch=1;
static unsigned tests,hits,misses;
'''+enums+struct+'\n'
src+='static void reference(const Vtx_t*v,struct LoadedVertex*d,float aspect_scale) {\n'+matrix+transform_b+finish_b+'}\n'
src+='static void candidate(const Vtx_t*v,struct LoadedVertex*d,float aspect_scale) {\n'+init+matrix+transform_c+'if(position_hit)hits++;else misses++;\n'+finish_c+'}\n'
src+='''
static uint32_t bits(float x) {uint32_t u;memcpy(&u,&x,4);return u;}
static float value(uint32_t u) {float f;memcpy(&f,&u,4);return f;}
static void check(const Vtx_t*v,float aspect) {
 struct LoadedVertex a,b;memset(&a,0xa5,sizeof(a));memset(&b,0xa5,sizeof(b));
 reference(v,&a,aspect);candidate(v,&b,aspect);
 if(memcmp(&a,&b,sizeof(a))) {fprintf(stderr,"Mismatch test%u xyz=%08x,%08x,%08x aspect%08x epoch%u\\n",tests,bits(v->ob[0]),bits(v->ob[1]),bits(v->ob[2]),bits(aspect),geometry_mvp_epoch);abort();}
 tests++;
}
static void miss_then_hit(Vtx_t v,float aspect) {
 memset(position_memo,0,sizeof(position_memo));position_memo_tag=0;
 unsigned h=hits,m=misses;check(&v,aspect);assert(hits==h && misses==m+1);check(&v,aspect);assert(hits==h+1 && misses==m+1);
}
static uint32_t rng=0xa5823117u;
static uint32_t random_word(void) {rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
static float random_finite(void) {return (int32_t)(random_word()%131071u-65535u)*0.03125f;}
static void identity(void) {memset(rsp.MP_matrix,0,sizeof(rsp.MP_matrix));for(unsigned i=0;i<4;i++)rsp.MP_matrix[i][i]=1.f;}
static unsigned hash(const Vtx_t*v) {return ((bits(v->ob[0])^(bits(v->ob[1])>>7)^(bits(v->ob[2])>>13))*2654435761u)>>25;}
int main(void) {
 identity();Vtx_t v={{1.25f,-2.5f,3.75f},{0,0},{1,2,3,4}};miss_then_hit(v,1.f);
 /* The failed int16 proposal aliases these distinct fractional positions. */
 unsigned oldmiss=misses;v.ob[0]=1.75f;check(&v,1.f);assert(misses==oldmiss+1);
 /* Full bits participate independently in x, y and z, including signed zero. */
 uint32_t patterns[]={0,0x80000000u,0x3f800000u,0xbf800000u,0x3f800001u,0x3f7fffffu,0x477fff00u,0xc77fff00u};
 for(unsigned axis=0;axis<3;axis++)for(unsigned k=0;k<sizeof(patterns)/sizeof(patterns[0]);k++){v.ob[axis]=value(patterns[k]);miss_then_hit(v,1.f);}
 /* Key collisions must replace entries without accepting another vertex. */
 Vtx_t byhash[128];unsigned used[128]={0},collisions=0;
 for(unsigned i=0;i<4096;i++) {
  Vtx_t a={{random_finite(),random_finite(),random_finite()},{0,0},{0,0,0,0}};unsigned h=hash(&a);
  if(used[h] && memcmp(a.ob,byhash[h].ob,12)) {Vtx_t b=byhash[h];check(&a,1.f);unsigned m=misses;check(&b,1.f);check(&a,1.f);assert(misses==m+2);collisions++;}
  used[h]=1;byhash[h]=a;
 }
 assert(collisions>3000);
 /* Geometry reuse must not key on or alter per-source UV/color attributes. */
 v=(Vtx_t){{1.25f,-2.5f,3.75f},{0,0},{1,2,3,4}};miss_then_hit(v,1.f);
 unsigned h=hits;for(unsigned i=0;i<256;i++){v.cn[0]=i;v.cn[3]=255-i;v.tc[0]=i*3;v.tc[1]=-i;check(&v,1.f);}assert(hits==h+256);
 /* MVP changes, including all sixteen scalars and a returned prior epoch. */
 for(unsigned i=0;i<4;i++)for(unsigned j=0;j<4;j++) {
  identity();rsp.MP_matrix[i][j]+=0.125f;geometry_mvp_epoch++;unsigned m=misses;check(&v,1.f);assert(misses==m+1);check(&v,1.f);
 }
 geometry_mvp_epoch=1;identity();oldmiss=misses;check(&v,1.f);assert(misses==oldmiss+1);
 float aspects[]={1.f,0.75f,1.3333334f,0.f,-0.f,1.f};
 for(unsigned i=0;i<6;i++){oldmiss=misses;check(&v,aspects[i]);check(&v,aspects[i]);if(i)assert(misses==oldmiss+1);}
 /* w=0, negative w, and exact/on-either-side clipping planes. */
 for(int w=-1;w<=1;w++)for(int axis=0;axis<3;axis++)for(int side=-1;side<=1;side++) {
  identity();rsp.MP_matrix[3][3]=(float)w;geometry_mvp_epoch++;v=(Vtx_t){{0,0,0},{0,0},{0,0,0,0}};
  v.ob[axis]=side;miss_then_hit(v,1.f);v.ob[axis]=side+0.00001f;check(&v,1.f);v.ob[axis]=side-0.00001f;check(&v,1.f);
 }
 /* Generation wrap must clear a stale entry tagged1 before reusing tag1. */
 identity();geometry_mvp_epoch=10;v=(Vtx_t){{1,2,3},{0,0},{0,0,0,0}};miss_then_hit(v,1.f);
 assert(position_memo_tag==1);position_memo_tag=0xffffffffu;position_memo_epoch=10;geometry_mvp_epoch=11;rsp.MP_matrix[3][0]=9.f;
 oldmiss=misses;check(&v,1.f);assert(misses==oldmiss+1 && position_memo_tag==1);
 geometry_mvp_epoch=0xffffffffu;check(&v,1.f);geometry_mvp_epoch=0;oldmiss=misses;check(&v,1.f);assert(misses==oldmiss+1);
 /* Finite randomized matrices, fractional coordinates, aspect and repeated hits. */
 for(unsigned i=0;i<20000;i++) {
  if(!(i%17)){for(unsigned a=0;a<4;a++)for(unsigned b=0;b<4;b++)rsp.MP_matrix[a][b]=random_finite()*0.125f;geometry_mvp_epoch++;}
  v.ob[0]=random_finite();v.ob[1]=random_finite();v.ob[2]=random_finite();float aspect=(1+(random_word()%31))*0.125f;check(&v,aspect);check(&v,aspect);
 }
 printf("PASS tests=%u hits=%u misses=%u forced_collisions=%u all_output_bytes_exact\\n",tests,hits,misses,collisions);return 0;
}
'''
(out/'functions.c').write_text(src)
results=[]
for name,flags in [('checked',['-O2','-fsanitize=address,undefined','-fno-omit-frame-pointer']),('fast',['-O3','-ffast-math','-fno-math-errno','-fno-trapping-math','-fsingle-precision-constant','-fno-strict-aliasing','-fwrapv'])]:
 binary=out/name
 subprocess.run(['gcc','-std=c11','-Wall','-Wextra',*flags,str(out/'functions.c'),'-lm','-o',str(binary)],check=True)
 r=subprocess.run([str(binary)],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0'));(out/(name+'.log')).write_text(r.stdout);assert r.returncode==0,r.stdout
 print(name,r.stdout.strip());results.append({'name':name,'flags':flags,'output':r.stdout.strip(),'binary_sha256':sha(binary)})
result={'passed':True,'candidate_sha256':sha(candidate),'baseline_sha256':sha(baseline),'fixture_sha256':sha(out/'functions.c'),'recipe_sha256':sha(Path(__file__)),'attribute_lighting_UV_body_exact':True,'fog_alpha_body_exact':True,'actual_source_blocks':'Copied cache declarations, initialization, key+miss math+hit reads, clip mask+stores, unchanged matrix locals. Only attributes/fog omitted and source-compared byte-identical.','scope':'Host exact-bit component correctness in ordinary and production-like optimization modes, not RV32 timing or full renderer. Full target scene/capture/replay checked separately.','runs':results}
(out/'results.json').write_text(json.dumps(result,indent=2)+'\n')
