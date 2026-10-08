#!/usr/bin/env python3
"""Extract actual texture memo functions and exercise their lifetime boundary."""
import hashlib,json,subprocess,tempfile,os
from pathlib import Path
R=Path(__file__).resolve().parents[3];C=R/'docs/measurements/os30-simple-20261005';O=C/'vc-material-memo-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def function(s,key):
 start=s.index(key);b=s.index('{',start);i=b+1;n=1
 while n:n+=(s[i]=='{')-(s[i]=='}');i+=1
 return s[start:i]
a=(C/'baseline/gfx_pc.c').read_text();b=(O/'gfx_pc.c').read_text()
k='static inline __attribute__((always_inline)) bool gfx_vc_texture_state('
ref=function(a,k).replace('gfx_vc_texture_state','ref_texture').replace('vc_tex_sig','ref_sig').replace('vc_slot_stale','ref_stale')
new=function(b,k).replace('gfx_vc_texture_state','new_texture').replace('vc_tex_sig','new_sig').replace('vc_slot_stale','new_stale')
sig=a[a.index('static struct {\n    const uint8_t *addr;'):a.index('} vc_tex_sig;')+len('} vc_tex_sig;')]
pre='''#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <assert.h>
#include <string.h>
struct ColorCombiner {uint32_t cc_id;};
static struct {struct {const uint8_t *addr;} loaded_texture[2];struct {uint16_t uls,ult;uint8_t cms,cmt;} texture_tile;} rdp;
static struct {struct {uint16_t s,t;} texture_scaling_factor;} rsp;
static uint32_t ref_stale,new_stale;static bool triangle_vc_texture_ready;
'''
tests=r'''
static uint32_t random_state=0x28934291;
static uint32_t rnd(void) {random_state^=random_state<<13;random_state^=random_state>>17;random_state^=random_state<<5;return random_state;}
static struct ColorCombiner comb;static uint8_t textures[16];
static int textured;static float bias;static unsigned draws,changed,reloads,cold,skipped;
static void draw(void) {
 bool x=ref_texture(&comb,textured,bias),y=new_texture(&comb,textured,bias);
 assert(x==y && ref_stale==new_stale && !memcmp(&ref_sig,&new_sig,sizeof(ref_sig)));
 draws++;changed+=x;
 uint32_t used=rnd();ref_stale&=~used;new_stale&=~used;
}
static void material(unsigned mask) {
 /* Exactly the ten existing signature inputs. Backend material preparation
  * establishes a new epoch even when the material value happens to repeat. */
 if(mask&1)rdp.loaded_texture[0].addr=textures+(rnd()&15);
 if(mask&2)comb.cc_id=rnd();
 if(mask&4)rdp.texture_tile.uls=rnd();
 if(mask&8)rdp.texture_tile.ult=rnd();
 if(mask&16)rsp.texture_scaling_factor.s=rnd();
 if(mask&32)rsp.texture_scaling_factor.t=rnd();
 if(mask&64)textured=rnd()&1;
 if(mask&128)bias=(rnd()&1)?0.5f:0;
 if(mask&256)rdp.texture_tile.cms=rnd();
 if(mask&512)rdp.texture_tile.cmt=rnd();
 triangle_vc_texture_ready=false;cold++;
}
int main(void) {
 ref_stale=new_stale=~0u;
 for(unsigned mask=0;mask<1024;mask++)for(unsigned n=0;n<8;n++) {
  material(mask); /* first use can be delayed by rejected/clipped triangles */
  skipped+=rnd()&31;draw();
  for(unsigned j=0;j<8;j++) {
   uint32_t overwritten=rnd();ref_stale|=overwritten;new_stale|=overwritten;reloads++;
   draw();
  }
 }
 for(unsigned n=0;n<1000000;n++) {
  unsigned event=rnd()%8;
  if(event<2)material(rnd()&1023);
  else if(event==2){uint32_t overwritten=rnd();ref_stale|=overwritten;new_stale|=overwritten;reloads++;}
  else if(event==3)skipped++; /* call/return/matrix or wholly rejected draw */
  else draw();
 }
 assert(changed && reloads && skipped);
 printf("PASS actual texture signatures: draws=%u changes=%u cold=%u reloads=%u skipped=%u all1024mutation-subsets\n",draws,changed,cold,reloads,skipped);
 return 0;
}
'''
out=O/'focused';out.mkdir(exist_ok=True)
h=pre+sig.replace('vc_tex_sig','ref_sig')+'\n'+sig.replace('vc_tex_sig','new_sig')+'\n'+ref+'\n'+new+'\n'+tests
(out/'actual-texture.c').write_text(h)
results={}
for name,flags in [('checked',['-O2','-fsanitize=undefined,address']),('fast',['-O3','-ffast-math'])]:
 exe=out/name;cmd=['gcc','-std=c11',*flags,str(out/'actual-texture.c'),'-o',str(exe)]
 subprocess.run(cmd,check=True)
 env=os.environ.copy();env['ASAN_OPTIONS']='detect_leaks=0'
 r=subprocess.run([str(exe)],capture_output=True,text=True,env=env);(out/(name+'.log')).write_text(r.stdout+r.stderr)
 assert r.returncode==0,(name,r.stdout,r.stderr)
 results[name]={'command':cmd,'output':r.stdout.strip(),'binary_sha256':sha(exe)}
with tempfile.TemporaryDirectory(prefix='vc-memo-replay-') as d:
 p=Path(d)
 subprocess.run(['python3',str(Path(__file__).with_name('prepare_vc_memo.py')),'--output',str(p/'replay')],check=True,stdout=subprocess.DEVNULL)
 assert (p/'replay/gfx_pc.c').read_bytes()==(O/'gfx_pc.c').read_bytes()
 (p/'gfx_pc.c').write_text(b)
 subprocess.run(['patch','-R','--batch','-p1','-i',str(O/'change.patch')],cwd=p,capture_output=True,check=True)
 assert (p/'gfx_pc.c').read_text()==a
record={'passed':True,'parent_sha256':sha(C/'baseline/gfx_pc.c'),'candidate_sha256':sha(O/'gfx_pc.c'),'actual_texture_harness_sha256':sha(out/'actual-texture.c'),'script_sha256':sha(Path(__file__)),'results':results,'replay_exact':True,'patch_reverse_exact':True,'proof_boundary':'Actual original/candidate texture functions, arbitrary changes to every signature field at material boundaries, arbitrary vertex-stale updates and lazy draws between boundaries. Source-lifetime contract separately audited; this does not prove callers independently.','unchanged_math':'All scalar/vector/clip/quantization expressions are byte-identical; no FP reassociation.'}
(O/'focused-proof.json').write_text(json.dumps(record,indent=2)+'\n');(out/'check_vc_memo.py').write_bytes(Path(__file__).read_bytes());print(json.dumps(record,indent=2))
