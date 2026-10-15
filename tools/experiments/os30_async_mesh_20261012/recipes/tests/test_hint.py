#!/usr/bin/env python3
"""Actual task-cache helper tests; hints affect routing, never geometry validity."""
from pathlib import Path
import hashlib,json,os,subprocess
E=Path(__file__).resolve().parent;W=E.parent;G=W/'src/sm64/sm64/src/pc/gfx';O=E/'hint-tests-v1'
assert not O.exists();O.mkdir();pc=(G/'gfx_pc.c').read_text();gpu=(G/'gfx_gpu.c').read_text()
def fn(text,name):
 a=text.index(name+'(');a=text.rfind('\n',0,a)+1;b=text.index('{',a);depth=1;i=b+1
 while depth:depth+=(text[i]=='{')-(text[i]=='}');i+=1
 return text[a:i]+'\n'
names=['gfx_async_note_repair','gfx_async_dead_suffix','gfx_mesh_lookup']
body=''.join(fn(pc,n)for n in names)
start=pc.index('struct MeshTask {');end=pc.index('};',start)+2
struct=pc[start:end]
try_load=pc[pc.index('static __attribute__((noinline)) int gfx_async_try_load'):]
assert try_load.index('if(async_armed->prefer_legacy)')<try_load.index('if(!async_armed->dead_after)')<try_load.index('if(!triangle_material_ready')
service=gpu[gpu.index('void gfx_gpu_async_service(void) {'):gpu.index('void gfx_gpu_async_drain(void) {')]
assert service.index('BRAM_GEO_REGS[0x2a]=1;')<service.index('gfx_async_note_repair(job.commands);')
assert service.index('BRAM_GEO_REGS[0x25]!=sink.token')<service.index('gfx_async_note_repair(job.commands);')
pre=r'''
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <assert.h>
typedef struct {struct {uint32_t w0,w1;} words;} Gfx;
static Gfx memory[65536];
#define _start memory[0]
#define __EH_FRAME_BEGIN__ (memory+65536)
enum {G_VTX=1,G_TRI1=5,G_TRI2=6};
static int gfx_gpu_async_supported(void){return 1;}
static int gfx_gpu_mesh_supported(void){return 0;}
static uint32_t gfx_mesh_stats[16],gfx_async_hint_stats[4];
static unsigned checks;
#define C(x) do{++checks;assert(x);}while(0)
'''
post=r'''
static unsigned bucket(const Gfx *p){return (((uintptr_t)p>>3)^((uintptr_t)p>>13))&511;}
static void topology(Gfx *p){p[0].words.w0=0x01003006;p[1].words.w0=0x05000204;p[2].words.w0=0x01003006;p[3].words.w0=0xdf000000;}
static unsigned flags(void){unsigned n=0;for(unsigned i=0;i<512;i++)n+=mesh_tasks[i].prefer_legacy!=0;return n;}
int main(void) {
 Gfx *keys[20];unsigned found=0,target=bucket(memory+4);
 for(unsigned i=4;i+4<65536&&found<20;i+=4)if(bucket(memory+i)==target)keys[found++]=memory+i;
 C(found==20);
 for(unsigned i=0;i<20;i++)topology(keys[i]);
 for(unsigned i=0;i<16;i++) {
  const struct MeshTask *entry=gfx_mesh_lookup(keys[i]);C(entry&&entry->source==keys[i]&&!entry->prefer_legacy&&entry->dead_after);
 }
 C(!gfx_mesh_lookup(keys[16]));
 for(unsigned i=0;i<16;i++) {
  gfx_async_note_repair(keys[i]+1);C(flags()==i+1);
  C(gfx_mesh_lookup(keys[i])->prefer_legacy==1);
  for(unsigned j=0;j<16;j++)C((gfx_mesh_lookup(keys[j])->prefer_legacy!=0)==(j<=i));
 }
 C(gfx_async_hint_stats[0]==16&&gfx_async_hint_stats[1]==16&&gfx_async_hint_stats[2]==16);
 for(unsigned i=0;i<10000;i++) {
  unsigned k=i%16;gfx_async_note_repair(keys[k]+1);C(flags()==16);C(gfx_mesh_lookup(keys[k])->prefer_legacy);
 }
 C(gfx_async_hint_stats[2]==16);gfx_async_note_repair(keys[16]+1);C(flags()==16);
 /* Different keys in the same bucket cannot inherit the route hint. The
  * existing cache does not evict, but clearing/replacing an entry explicitly
  * must clear the hint through the real initialization path. */
 struct MeshTask *entry=&mesh_tasks[target];C(entry->source==keys[0]);entry->source=NULL;
 const struct MeshTask *replacement=gfx_mesh_lookup(keys[16]);C(replacement==entry);C(!replacement->prefer_legacy);C(flags()==15);
 gfx_async_note_repair(keys[0]+1);C(!replacement->prefer_legacy);C(flags()==15);
 gfx_async_note_repair(keys[16]+1);C(replacement->prefer_legacy);C(flags()==16);
 /* Invalid/uncompiled slots do not acquire a hint. */
 entry->next=NULL;entry->prefer_legacy=0;gfx_async_note_repair(keys[16]+1);C(!entry->prefer_legacy);
 gfx_async_note_repair(NULL);gfx_async_note_repair((void *)((uintptr_t)memory+1));gfx_async_note_repair(memory);gfx_async_note_repair(memory+65536);C(flags()==15);
 memset(mesh_tasks,0,sizeof(mesh_tasks));C(!flags());C(!gfx_mesh_lookup(keys[0])->prefer_legacy);
 printf("{\"status\":\"PASS\",\"checks\":%u,\"repeated_exact_keys\":10000,\"collision_chain\":16}\n",checks);
}
'''
source=pre+struct+'\nstatic struct MeshTask mesh_tasks[512];\n'+body+post
(O/'test.c').write_text(source);cmd=['cc','-std=c11','-O1','-g','-fsanitize=address,undefined',str(O/'test.c'),'-o',str(O/'test')]
subprocess.run(cmd,check=True);r=subprocess.run([str(O/'test')],capture_output=True,text=True,env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0'))
(O/'run.log').write_text(r.stdout+r.stderr);assert r.returncode==0,r.stderr
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();result=json.loads(r.stdout)
result.update(inputs_sha256={str(p):sha(p)for p in [Path(__file__).resolve(),G/'gfx_pc.c',G/'gfx_gpu.c',O/'test.c']},extracted_functions_sha256={n:hashlib.sha256(fn(pc,n).encode()).hexdigest()for n in names},scope=['Unmodified actual C note-repair, topology lookup and suffix helpers. Exact key, full16-entry collision chain, probe exhaustion, repeated feedback, cache reset/replacement and invalid input.','Static source assertions require the hint route check before all material work and feedback only after completed repair/epoch-token checks.','The hint is conservative route selection only; geometry does not inherit prior clip/cull outcomes. Full-frame performance/equality remains a separate gate.'],commands=[cmd,[str(O/'test')]],sanitizers='ASan+UBSan; LSan disabled under ptrace')
(O/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
