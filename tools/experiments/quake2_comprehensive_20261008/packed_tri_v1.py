#!/usr/bin/env python3
"""Prepare and check a packed 17-word CPU triangle payload with one submit owner."""
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

R=Path(__file__).resolve().parents[3]
P=R/'docs/measurements/quake2-comprehensive-20261008'
B=P/'work/base';W=P/'work/packed';C=P/'components/packed-v1'
GL='src/quake2/openfpga/of_gl1.c';EM='src/quake2/openfpga/of_emit_q2.c';HD='src/quake2/openfpga/of_emit_q2.h';TEST='tools/tests/quake2_renderer_test.c'
BASE='29818e61c88a358f30cde8a4282676c6904a71b12b77c98934844233ea84fbdc'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,v):p.write_text(json.dumps(v,indent=2,sort_keys=True)+'\n')
def function(text, signature):
    a=text.index(signature); b=text.index('{',a); depth=1;i=b+1
    while depth:
        if text[i]=='{':depth+=1
        elif text[i]=='}':depth-=1
        i+=1
    return text[a:i]

assert sha(B/GL)==BASE and not W.exists() and not C.exists()
C.mkdir(parents=True);shutil.copytree(B,W,ignore=shutil.ignore_patterns('.obj','build','__pycache__'))
old=(B/GL).read_text();new=old
new=new.replace('''\tint16_t X[3], Y[3];
\tint32_t S[3], T[3], ZI[3], DEPTH[3];
\tuint16_t RGB[3];''','''\t/* The submit owner accepts the final wire layout directly: XY, S, T,
\t * ZI, packed RGB565, then independent depth. */
\tuint32_t words[17];''',1)
new=new.replace('''\t\tX[k] = v[k]->px;
\t\tY[k] = v[k]->py;
\t\tS[k] = (int32_t)lrintf((v[k]->s - base_s) * 65536.0f);
\t\tT[k] = (int32_t)lrintf((v[k]->t - base_t) * 65536.0f);''','''\t\twords[k] = ((uint32_t)(uint16_t)v[k]->py << 16) | (uint16_t)v[k]->px;
\t\twords[3 + k] = (uint32_t)(int32_t)lrintf((v[k]->s - base_s) * 65536.0f);
\t\twords[6 + k] = (uint32_t)(int32_t)lrintf((v[k]->t - base_t) * 65536.0f);''',1)
new=new.replace('\t\tZI[k] = (z < 1) ? 1 : z;', '\t\twords[9 + k] = (uint32_t)((z < 1) ? 1 : z);',1)
new=new.replace('''\t\tDEPTH[k] = v[k]->depth;

\t\tRGB[k] = v[k]->rgb;
\t}''','''\t\twords[14 + k] = (uint32_t)v[k]->depth;
\t}
\twords[12] = ((uint32_t)v1->rgb << 16) | v0->rgb;
\twords[13] = v2->rgb;''',1)
new=new.replace('return OFQ2_DrawVertTriRGB(X, Y, S, T, ZI, RGB, DEPTH);','return OFQ2_DrawVertTriRGBPacked(words);',1)
assert new!=old and 'OFQ2_DrawVertTriRGB(X, Y' not in new
(W/GL).write_text(new)
new_owner='''/* Packed 0x4E payload: three XY, S, T and ZI words, two RGB565 words,
 * and three independent depth words. Keep reservation and publication here
 * so no renderer translation unit acquires its own SDK ring state. */
int
OFQ2_DrawVertTriRGBPacked(const uint32_t words[17])
{
\tif (!OFQ2_Supports(OFQ2_CAP_VERT_TRI_RGB) || !ofq2_tristate_valid ||
\t\tofq2_tristate_dirty)
\t\treturn 0;

#ifndef OF_PC
\tif (of_has_feature(OF_HW_GPU_VERT_TRI) && of_has_feature(OF_HW_GPU_VCOLOR))
#endif
\t{
\t\t_gpu_stream_reserve_words(18u);
\t\tuint32_t *w = _gpu_ring_claim();
\t\tw[0] = ((uint32_t)GPU_CMD_DRAW_VERT_TRI_RGB << 24) | 17u;
\t\tmemcpy(w + 1, words, 17u * sizeof(*words));
\t\t_gpu_ring_commit(18u);
\t}
\tofq2_pending = true;
\treturn 1;
}

'''
em=(B/EM).read_text();sig='int\nOFQ2_DrawVertTriRGB('
assert em.count(sig)==1
(W/EM).write_text(em.replace(sig,new_owner+sig,1))
hd=(B/HD).read_text();at=hd.index('int OFQ2_DrawVertTriRGB(')
(W/HD).write_text(hd[:at]+'/* Final 17-word 0x4E payload; all words are required. */\nint OFQ2_DrawVertTriRGBPacked(const uint32_t words[17]);\n'+hd[at:])
adapter='''int OFQ2_DrawVertTriRGBPacked(const uint32_t words[17])
{
\tint16_t x[3], y[3]; int32_t s[3], t[3], zi[3], depth[3]; uint16_t rgb[3];
\tfor (int i = 0; i < 3; i++)
\t{
\t\tx[i] = (int16_t)words[i]; y[i] = (int16_t)(words[i] >> 16);
\t\ts[i] = (int32_t)words[3+i]; t[i] = (int32_t)words[6+i];
\t\tzi[i] = (int32_t)words[9+i]; depth[i] = (int32_t)words[14+i];
\t}
\trgb[0] = (uint16_t)words[12]; rgb[1] = (uint16_t)(words[12] >> 16); rgb[2] = (uint16_t)words[13];
\treturn OFQ2_DrawVertTriRGB(x,y,s,t,zi,rgb,depth);
}

'''
test=(B/TEST).read_text();at=test.index('static void test_video_modes(')
(W/TEST).write_text(test[:at]+adapter+test[at:])
changes=[GL,EM,HD,TEST]
for rel in changes:
    target=C/Path(rel).name;shutil.copy2(W/rel,target)
    (C/(Path(rel).name+'.patch')).write_text(''.join(difflib.unified_diff((B/rel).read_text().splitlines(True),(W/rel).read_text().splitlines(True),fromfile='a/'+rel,tofile='b/'+rel)))
shutil.copy2(__file__,C/'recipe.py')
put(C/'prepared.json',dict(parent_sha256={rel:sha(B/rel) for rel in changes},candidate_sha256={rel:sha(W/rel) for rel in changes},worktree=str(W),scope='Same float arithmetic and 17-word 0x4E payload; final layout built directly, one owner reserves header+payload and commits once. Old public API retained.'))
O=C/'checks';O.mkdir();G=W/'src/quake2'
includes=[G,G/'src/common/header',G/'src/client/refresh/gl1/glad-gles1/include',W/'src/sdk/include',G/'openfpga']
flags=['cc','-std=gnu11','-O1','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer','-fno-pie','-no-pie','-DOF_PC','-DYQ2_OPENFPGA=1','-DYQ2_GL1_GLES=1','-ffunction-sections','-fdata-sections','-DYQ2OSTYPE="test"','-DYQ2ARCH="host"']+['-I'+str(p) for p in includes]
commands=[];env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0',UBSAN_OPTIONS='halt_on_error=1')
def run(cmd,name):
    commands.append(dict(argv=cmd,name=name))
    with (O/(name+'.log')).open('w') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,env=env,check=True)
run(flags+[str(W/TEST),'-Wl,--gc-sections','-lm','-o',str(O/'renderer')],'renderer-build')
run([str(O/'renderer')],'renderer-run')
sdk=(B/'src/sdk/include/of_gpu.h').read_text()
leaf=function(sdk,'static inline void of_gpu_draw_vert_tri_rgb(')
original=function(em,'int\nOFQ2_DrawVertTriRGB(').replace('OFQ2_DrawVertTriRGB(','original_draw(',1)
owner=function(new_owner,'int\nOFQ2_DrawVertTriRGBPacked(')
packet=r'''#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <setjmp.h>
#define OFQ2_CAP_VERT_TRI_RGB 1u
#define OF_HW_GPU_VERT_TRI 1u
#define OF_HW_GPU_VCOLOR 2u
#define GPU_CMD_DRAW_VERT_TRI_RGB 0x4e
static unsigned supported,hw,ofq2_tristate_valid,ofq2_tristate_dirty,ofq2_pending;
static uint32_t staging[258],stream[8192];
static unsigned cap,used,total,reserved,reserves,flushes,commits;
static jmp_buf trap;
static int OFQ2_Supports(unsigned bit){return (supported&bit)==bit;}
static int of_has_feature(unsigned bit){return (hw&bit)==bit;}
static void flush(void){assert(!reserved && total+used<8192);memcpy(stream+total,staging+1,used*4);total+=used;used=0;flushes++;}
static void _gpu_stream_reserve_words(unsigned n){assert(!reserved);reserves++;if(n>cap)longjmp(trap,1);if(used+n>cap)flush();reserved=n;}
static uint32_t *_gpu_ring_claim(void){assert(reserved&&used+reserved<=cap);return staging+1+used;}
static void _gpu_ring_commit(unsigned n){assert(n<=reserved);used+=n;reserved-=n;commits++;assert(staging[0]==0xabcde123&&staging[cap+1]==0x321edcba);}
static void _gpu_cmd_header(unsigned command,unsigned payload){_gpu_stream_reserve_words(payload+1);*_gpu_ring_claim()=(command<<24)|payload;_gpu_ring_commit(1);}
'''+leaf+'\n'+original+'\n'+owner+r'''
static uint32_t rng=0xf28aa101;
static uint32_t rnd(void){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
static void reset(unsigned size){cap=size;used=total=reserved=reserves=flushes=commits=ofq2_pending=0;memset(staging,0,sizeof staging);memset(stream,0,sizeof stream);staging[0]=0xabcde123;staging[cap+1]=0x321edcba;}
static unsigned cases;
static void check(unsigned capacity,unsigned triangles){
  int16_t x[128][3],y[128][3];int32_t s[128][3],t[128][3],zi[128][3],d[128][3];uint16_t rgb[128][3];uint32_t words[128][17];
  unsigned old_returns=0,new_returns=0;
  for(unsigned n=0;n<triangles;n++){
    for(unsigned k=0;k<3;k++){
      x[n][k]=rnd();y[n][k]=rnd();s[n][k]=rnd();t[n][k]=rnd();zi[n][k]=rnd();d[n][k]=rnd();rgb[n][k]=rnd();
      words[n][k]=((uint32_t)(uint16_t)y[n][k]<<16)|(uint16_t)x[n][k];words[n][3+k]=s[n][k];words[n][6+k]=t[n][k];words[n][9+k]=zi[n][k];words[n][14+k]=d[n][k];
    }
    words[n][12]=((uint32_t)rgb[n][1]<<16)|rgb[n][0];words[n][13]=rgb[n][2];
  }
  reset(capacity);int old_trap=setjmp(trap);
  if(!old_trap)for(unsigned n=0;n<triangles;n++)old_returns+=original_draw(x[n],y[n],s[n],t[n],zi[n],rgb[n],d[n]);
  assert(!reserved);flush();uint32_t expected[8192];memcpy(expected,stream,sizeof expected);
  unsigned old_total=total,old_reserves=reserves,old_flushes=flushes,old_pending=ofq2_pending;
  reset(capacity);int new_trap=setjmp(trap);
  if(!new_trap)for(unsigned n=0;n<triangles;n++)new_returns+=OFQ2_DrawVertTriRGBPacked(words[n]);
  assert(!reserved);flush();
  assert(old_trap==new_trap&&old_returns==new_returns&&old_total==total&&old_reserves==reserves&&old_flushes==flushes&&old_pending==ofq2_pending);
  assert(!memcmp(expected,stream,sizeof expected));cases++;
}
int main(void){
  const unsigned capacities[]={0,17,18,19,35,36,37,64,127,256};
  for(supported=0;supported<2;supported++)for(hw=0;hw<4;hw++)for(ofq2_tristate_valid=0;ofq2_tristate_valid<2;ofq2_tristate_valid++)for(ofq2_tristate_dirty=0;ofq2_tristate_dirty<2;ofq2_tristate_dirty++)for(unsigned i=0;i<sizeof capacities/sizeof *capacities;i++)check(capacities[i],128);
  supported=1;hw=3;ofq2_tristate_valid=1;ofq2_tristate_dirty=0;
  for(unsigned i=0;i<1000;i++)check(18+rnd()%239,1+rnd()%128);
  printf("PASS packet_cases=%u caps_state_hw_routes=32 reserve_capacity_boundaries=10 random_packets>64000\n",cases);return 0;
}
'''
(O/'packet.c').write_text(packet)
run(['cc','-std=gnu11','-O1','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer','-fno-pie','-no-pie',str(O/'packet.c'),'-o',str(O/'packet')],'packet-build')
run([str(O/'packet')],'packet-run')
put(O/'commands.json',commands)
put(C/'result.json',dict(passed=True,actual_renderer_sanitizers=True,packet_state_and_caps_cases=1320,packet_harness='Literal original SDK leaf and old/new owner functions; recording reserve/flush model with canaries, all17words, guard routes and reserve-overflow equivalence. Full hardware waits covered later by qsim/RTL.',performance_measured=False))
put(C/'manifest.json',dict(passed=True,evidence_sha256={str(p.relative_to(R)):sha(p) for p in C.rglob('*') if p.is_file()}))
print((O/'packet-run.log').read_text().strip());print('gl',sha(W/GL),'owner',sha(W/EM),'manifest',sha(C/'manifest.json'))
