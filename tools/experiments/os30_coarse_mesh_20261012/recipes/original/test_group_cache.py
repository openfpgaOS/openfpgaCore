#!/usr/bin/env python3
"""Actual AJ03 cached compiler plus real allocator under ownership/recycle tests."""
from pathlib import Path
import hashlib,json,os,re,subprocess
E=Path(__file__).resolve().parent;W=E.parent;G=W/'src/sm64/sm64/src/pc/gfx';O=E/'group-cache-tests-v1'
assert not O.exists();O.mkdir()
gpu=(G/'gfx_gpu.c').read_text();pc=(G/'gfx_pc.c').read_text()
def fn(s,name):
 m=re.search(r'^[^\n;]*\b'+name+r'\([^;]*?\)\s*\{',s,re.M);assert m,name
 a=m.start();b=s.index('{',a);i=b+1;depth=1
 while depth:depth+=(s[i]=='{')-(s[i]=='}');i+=1
 return s[a:i]+'\n'
def decl(name):
 a=pc.index('struct '+name+' {');return pc[a:pc.index('};',a)+2]+'\n'
pool=gpu[gpu.index('#define CRAM_VERTEX_BEGIN'):gpu.index('static __attribute__((cold, noinline)) void gpu_fast_tex_fallback')]
names=['gfx_async_forget_sources','gfx_async_sources','gfx_async_note_repair','gfx_async_dead_suffix',
       'gfx_async_dead_caller','gfx_async_task_proof','gfx_async_route_task','gfx_mesh_lookup_impl','gfx_mesh_lookup','gfx_async_group_refresh','gfx_async_group_build','gfx_async_group_sources']
functions=''.join(fn(pc,n) for n in names)
assert 'cpu_fog_pending&=~loaded_mask;' in fn(pc,'gfx_async_try_load')
assert 'vc_slot_stale=0xffffffffu;' in fn(pc,'gfx_async_try_load')
assert 'load_bram_slots=0;load_queued_safe=0;' in fn(pc,'gfx_async_try_load').replace('\n    ','')
assert 'cmd=async_next;async_armed=NULL;continue;' in pc
assert 'gfx_run_dl(commands,NULL)' in pc
assert 'gfx_run_dl((Gfx *)seg_addr(cmd->words.w1),cmd+1)' in pc
assert 'mesh_armed=NULL;async_return_command=return_command;async_armed=gfx_mesh_lookup(cmd);' in pc
head=r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef struct {struct {uint32_t w0,w1;} words;} Gfx;
typedef struct {uint32_t words[6];} Vtx;
#define G_VTX 1
#define G_TRI1 5
#define G_TRI2 6
#define G_DL 0xde
#define G_ENDDL 0xdf
static uint32_t image[65536] __attribute__((aligned(8)));
#define _start image[0]
#define __EH_FRAME_BEGIN__ (image+65536)
static uint32_t memory[0x1000000/4];
static unsigned checks,uploads,waits,drains,owner,group_supported=1,inject_wait;
static const void *inject_repair;
static uint32_t gfx_async_stats[16],gfx_mesh_stats[16],gfx_async_source_stats[4],gfx_async_hint_stats[4],gfx_async_group_stats[8];
static uint32_t async_hint_epoch=1;
static const Gfx *async_hint_sources[512];
static unsigned async_hint_count;
static void check(int ok){++checks;assert(ok);}
void gfx_async_note_repair(const void *commands);
void gfx_async_forget_sources(void);
int gfx_gpu_async_supported(void){return 1;}
int gfx_gpu_async_group_supported(void){return group_supported;}
int gfx_gpu_mesh_supported(void){return 0;}
void gfx_gpu_async_drain(void){++drains;owner=0;}
void gfx_gpu_mesh_wait_source(void){++waits;owner=0;if(inject_wait&&!--inject_wait)gfx_async_note_repair(inject_repair);}
void _of_gpu_fast_tex_upload(uint32_t address,const uint32_t *source,unsigned words){
 check(!owner);check(!(address&7));check(address+words*4<=sizeof(memory));
 memcpy((uint8_t *)memory+address,source,words*4);++uploads;
}
'''
declarations=''.join(decl(n) for n in ['MeshTask','AsyncTaskSources','AsyncDeadProof','AsyncGroup'])+'''
static struct MeshTask mesh_tasks[512];
static struct AsyncTaskSources async_task_sources[512];
static struct AsyncGroup async_groups[512];
static struct AsyncDeadProof async_dead_proofs[512];
static Gfx *async_return_command;
'''
tail=r'''
static Gfx *commands(void){return (Gfx *)(image+64);}
static Vtx *vertices(unsigned i){return (Vtx *)(image+4096+i*192);}
static void reset(void){
 memset(async_dead_proofs,0,sizeof async_dead_proofs);async_return_command=NULL;
 memset(image,0,sizeof image);memset(mesh_tasks,0,sizeof mesh_tasks);memset(async_task_sources,0,sizeof async_task_sources);
 memset(async_groups,0,sizeof async_groups);memset(g_cram_vertex_source,0,sizeof g_cram_vertex_source);memset(g_cram_mesh_source,0,sizeof g_cram_mesh_source);
 memset(gfx_async_stats,0,sizeof gfx_async_stats);memset(gfx_mesh_stats,0,sizeof gfx_mesh_stats);memset(gfx_async_source_stats,0,sizeof gfx_async_source_stats);
 memset(gfx_async_hint_stats,0,sizeof gfx_async_hint_stats);memset(gfx_async_group_stats,0,sizeof gfx_async_group_stats);
 async_hint_epoch=1;async_hint_count=0;memset(async_hint_sources,0,sizeof async_hint_sources);group_supported=1;g_cram_vertex_enabled=1;g_cram_source_generation=1;
 g_cram_vertex_top=CRAM_VERTEX_BEGIN;g_cram_vertex_recycled=0;g_cram_vertex_frame=g_cram_vertex_last_recycle=0;
 uploads=waits=drains=owner=inject_wait=0;inject_repair=NULL;
}
static void build(unsigned members,unsigned varying){
 Gfx *c=commands();
 for(unsigned i=0;i<members;i++){
  unsigned n=varying?3+(i*7%30):3;
  c[2*i].words.w0=0x01000000|(n<<12)|(n<<1);c[2*i].words.w1=(uint32_t)(uintptr_t)vertices(i);
  c[2*i+1].words.w0=0x05000204;c[2*i+1].words.w1=0;
  for(unsigned j=0;j<32*6;j++)((uint32_t*)vertices(i))[j]=0x52491000u+i*104729u+j;
 }
 /* A final LOAD overwrites every group slot before ENDDL. It is a legacy
  * terminal, since it has no TRI. Thus every member's dead proof is real. */
 c[2*members].words.w0=0x01020040;c[2*members].words.w1=(uint32_t)(uintptr_t)vertices(members);
 c[2*members+1].words.w0=0xdf000000;
}
static const struct MeshTask *first(void){const struct MeshTask *p=gfx_mesh_lookup(commands());check(p!=NULL);return p;}
static void verify(const struct AsyncGroup *g,unsigned members,unsigned cull){
 check(g&&g->members==members&&g->triangles==members&&g->next==commands()+2*members);
 check(g->generation==g_cram_source_generation&&g->hint_epoch==async_hint_epoch&&g->hint_cursor==async_hint_count&&g->cull==cull);
 const uint32_t *d=(const uint32_t *)((uint8_t *)memory+g->descriptor_offset);uint32_t mask=0;
 for(unsigned i=0;i<members;i++,d+=6){
  unsigned n=commands()[2*i].words.w0>>12&255;mask|=n==32?~0u:((1u<<n)-1);
  check(d[1]==commands()[2*i].words.w0&&d[3]==(1u|(cull<<8)));
  check(d[4]==(uint32_t)(uintptr_t)vertices(i)&&d[5]==(uint32_t)(uintptr_t)(commands()+2*i+1));
  check(!memcmp((uint8_t *)memory+d[0],vertices(i),n*24));
  check(!memcmp((uint8_t *)memory+d[2],commands()+2*i+1,8));
  check(d[0]<g->descriptor_offset&&d[2]<g->descriptor_offset);
 }
 check(g->loaded_mask==mask);
}

static void put(Gfx *c,unsigned op,uint32_t b){c->words.w0=op;c->words.w1=b;}
static void overwrite(Gfx *c,unsigned n,unsigned first){put(c,0x01000000u|(n<<12)|((first+n)<<1),0);}
static void call(Gfx *c,Gfx *target,int branch){put(c,0xde000000u|((unsigned)branch<<16),(uint32_t)(uintptr_t)target);}
static int neutral(unsigned op){
 switch(op){case 0xd7:case 0xfd:case 0xd9:case 0xfb:case 0xf5:case 0xf2:case 0xf3:case 0xf4:
 case 0xf0:case 0xfa:case 0xfc:case 0xf8:case 0xe6:case 0xe7:case 0xe8:case 0xe9:return 1;default:return 0;}
}
static void proof_cases(void){
 Gfx *c=commands(),*ret=(Gfx *)(image+2000),*bad=ret+16,*nested=(Gfx *)(image+2300);
 /* Only the independently audited sixteen state opcodes may be transparent.
  * E2/E3, all other draw/state opcodes and unrecognized bytes fail closed. */
 for(unsigned op=0;op<256;op++){
  if(op==G_VTX||op==G_TRI1||op==G_TRI2||op==G_DL||op==G_ENDDL)continue;
  reset();put(c,op<<24,0);overwrite(c+1,32,0);
  check(gfx_async_dead_caller(c,32,NULL)==(unsigned)neutral(op));
 }
 reset();put(c,0xdf000000,0);overwrite(ret,32,0);put(bad,0x05000204,0);
 check(gfx_async_dead_caller(c,3,NULL)==0);
 check(gfx_async_dead_caller(c,3,ret)==2);
 check(gfx_async_dead_caller(c,3,bad)==0); /* same callee, canonical reader */
 check(gfx_async_dead_caller(c,3,(Gfx *)((uintptr_t)ret+4))==0);
 check(gfx_async_dead_caller(c,3,(Gfx *)(image+65536))==0);
 check(gfx_async_dead_caller(c,3,(Gfx *)memory)==0); /* mutable outside ELF */
 put(bad,0xdf000000,0);check(gfx_async_dead_caller(c,3,bad)==0); /* second external return */
 /* A locally pushed list returns to the immediate caller's continuation;
  * a tail branch retains that continuation instead of inventing a return. */
 call(ret,nested,0);overwrite(ret+1,32,0);put(nested,0xfb000000,0);put(nested+1,0xdf000000,0);
 check(gfx_async_dead_caller(c,3,ret)==2);
 call(ret,nested,1);check(gfx_async_dead_caller(c,3,ret)==0);
 overwrite(nested+1,32,0);check(gfx_async_dead_caller(c,3,ret)==2);
 call(c,nested,0);put(c+1,0xdf000000,0);
 check(gfx_async_dead_caller(c,3,NULL)==1); /* no external return needed */
 call(c,(Gfx *)((uintptr_t)nested+4),0);check(!gfx_async_dead_caller(c,3,ret));
 call(c,(Gfx *)(image+65536),1);check(!gfx_async_dead_caller(c,3,ret));
 call(c,(Gfx *)memory,0);check(!gfx_async_dead_caller(c,3,ret));
 /* Stack overflow, branches/cycles and the inclusive last scan position. */
 for(unsigned depth=0;depth<=9;depth++){
  reset();for(unsigned i=0;i<depth;i++){call(c+2*i,c+2*i+2,0);put(c+2*i+1,0xdf000000,0);}
  overwrite(c+2*depth,32,0);check(gfx_async_dead_caller(c,32,NULL)==(depth<=8));
 }
 reset();call(c,c,1);check(!gfx_async_dead_caller(c,3,ret));
 for(unsigned length=255;length<=256;length++){
  reset();for(unsigned i=0;i<length;i++)put(c+i,0xe7000000,0);
  overwrite(c+length,32,0);check(gfx_async_dead_caller(c,32,NULL)==(length==255));
 }
 /* Partial overwrite and every original slot: the first read of any still
  * live row rejects, including TRI2's second triangle and bit31. */
 for(unsigned slot=0;slot<32;slot++)for(unsigned tri2=0;tri2<2;tri2++){
  reset();unsigned encoded=slot*2,indices=encoded|(encoded<<8)|(encoded<<16);
  put(c,tri2?0x06000000:(0x05000000|indices),tri2?indices:0);overwrite(c+1,32,0);
  check(!gfx_async_dead_caller(c,32,NULL));
 }
 reset();overwrite(c,1,0);put(c+1,0x05000000,0);overwrite(c+2,2,1);
 check(gfx_async_dead_caller(c,3,NULL)==1);
 put(c+1,0x05000200,0);check(!gfx_async_dead_caller(c,3,NULL));
 put(c+1,0x06000000,0x00000200);check(!gfx_async_dead_caller(c,3,NULL));
 reset();put(c,0x05404040,0);overwrite(c+1,32,0);check(!gfx_async_dead_caller(c,3,NULL));
 for(unsigned n=0;n<=33;n+=33){reset();overwrite(c,n,0);check(!gfx_async_dead_caller(c,3,NULL));}
 reset();overwrite(c,3,31);check(!gfx_async_dead_caller(c,3,NULL));
 /* Per-task negative and positive cache entries bind the exact continuation.
  * A rejected last member must make even the shorter group caller-sensitive. */
 reset();build(3,0);put(c+6,0xdf000000,0);overwrite(ret,32,0);put(bad,0x05000204,0);
 async_return_command=bad;const struct MeshTask *p=first();
 const struct AsyncGroup *g=gfx_async_group_sources(p,0);verify(g,2,0);
 check(g->caller_sensitive&&g->return_command==bad);
 const struct MeshTask *last=gfx_mesh_lookup_impl(c+4,0,0);check(last&&!last->dead_after);
 check(async_dead_proofs[last-mesh_tasks].kind==3);
 unsigned up=uploads;check(!gfx_mesh_lookup_impl(c+4,1,0)&&uploads==up);
 async_return_command=ret;g=gfx_async_group_sources(p,0);verify(g,3,0);
 check(g->caller_sensitive&&g->return_command==ret&&async_dead_proofs[last-mesh_tasks].kind==2);
 check(gfx_mesh_lookup_impl(c+4,1,0)==last);
 up=uploads;check(gfx_async_group_sources(p,0)==g&&uploads==up);
 async_return_command=bad;g=gfx_async_group_sources(p,0);verify(g,2,0);check(!gfx_mesh_lookup_impl(c+4,1,0));
 async_return_command=ret;g=gfx_async_group_sources(p,0);verify(g,3,0);
 gfx_async_note_repair(c+3);check(!gfx_async_group_sources(p,0)); /* per-member hint still controls route */
 /* Source-generation recycle reuploads all descriptors; caller binding must
  * survive retries and no caller-only key may preserve stale source offsets. */
 reset();build(3,0);put(c+6,0xdf000000,0);overwrite(ret,32,0);async_return_command=ret;p=first();
 verify(gfx_async_group_sources(p,1),3,1);gfx_async_forget_sources();++g_cram_source_generation;
 verify(gfx_async_group_sources(p,1),3,1);
 /* Looking past state for lifetime does not consume state in GROUP. This
  * caller-independent proof remains reusable even with an invalid caller. */
 reset();build(3,0);put(c+6,0xfc000000,0);overwrite(c+7,32,0);async_return_command=ret;p=first();
 g=gfx_async_group_sources(p,0);verify(g,3,0);check(!g->caller_sensitive);
 last=gfx_mesh_lookup_impl(c+4,0,0);check(!last->dead_after&&async_dead_proofs[last-mesh_tasks].kind==1);
 async_return_command=(Gfx *)memory;up=uploads;check(gfx_async_group_sources(p,0)==g&&uploads==up);
 check(gfx_mesh_lookup_impl(c+4,1,0)==last);
}

int main(void){
 proof_cases();
 check((uintptr_t)image<=UINT32_MAX-sizeof image);
 for(unsigned members=2;members<=32;members++)for(unsigned varying=0;varying<2;varying++){
  reset();build(members,varying);const struct MeshTask *p=first();
  const struct AsyncGroup *g=gfx_async_group_sources(p,2);verify(g,members,2);
  unsigned up=uploads,w=waits;owner=1;uint32_t oldoff=g->descriptor_offset;
  check(gfx_async_group_sources(p,2)==g);check(owner&&uploads==up&&waits==w&&g->descriptor_offset==oldoff);
  owner=0;g=gfx_async_group_sources(p,1);verify(g,members,1);check(g->descriptor_offset!=oldoff);
 }
 reset();build(40,1);verify(gfx_async_group_sources(first(),0),32,0);
 reset();build(1,0);const struct MeshTask *p=first();check(!gfx_async_group_sources(p,0));unsigned up=uploads;
 check(!gfx_async_group_sources(p,0)&&uploads==up);
 /* Exact task hint changes split a cached immutable group; no slot mutation
  * is interpreted as geometry correctness and a32-bit epoch wrap clears all. */
 reset();build(8,1);p=first();verify(gfx_async_group_sources(p,3),8,3);
 gfx_async_note_repair(commands()+2*3+1);check(async_hint_epoch==2);
 verify(gfx_async_group_sources(p,3),3,3);
 async_hint_epoch=UINT32_MAX;gfx_async_note_repair(commands()+2*5+1);check(async_hint_epoch==1);
 for(unsigned i=0;i<512;i++)check(!async_groups[i].members);
 /* Unrelated new hints, including exactly the half-open end boundary, must
  * preserve the uploaded descriptor table and perform no allocation/wait. */
 reset();build(40,0);p=first();const struct AsyncGroup *g=gfx_async_group_sources(p,2);verify(g,32,2);
 uint32_t offset=g->descriptor_offset;up=uploads;unsigned oldwait=waits,oldbuilds=gfx_async_group_stats[3];
 for(unsigned i=32;i<40;i++) {
  check(gfx_mesh_lookup_impl(commands()+2*i,1,0)!=NULL);
  unsigned epoch=async_hint_epoch;
  gfx_async_note_repair(commands()+2*i+1);check(async_hint_epoch==epoch+1);
  gfx_async_note_repair(commands()+2*i+1);check(async_hint_epoch==epoch+1);
  owner=1;check(gfx_async_group_sources(p,2)==g);verify(g,32,2);
  check(owner&&uploads==up&&waits==oldwait&&gfx_async_group_stats[3]==oldbuilds&&g->descriptor_offset==offset);
  owner=0;
 }
 /* A hint at this group's own begin invalidates it; a hint before a later
  * cached group does not. Unknown immutable keys cannot append a hint. */
 const struct MeshTask *later=gfx_mesh_lookup_impl(commands()+16,1,0);check(later!=NULL);
 const struct AsyncGroup *lg=gfx_async_group_sources(later,1);check(lg&&lg->members==24);
 offset=lg->descriptor_offset;up=uploads;oldwait=waits;oldbuilds=gfx_async_group_stats[3];
 gfx_async_note_repair(commands()+1);check(mesh_tasks[p-mesh_tasks].prefer_legacy);
 check(gfx_async_group_sources(later,1)==lg&&lg->descriptor_offset==offset);
 check(uploads==up&&waits==oldwait&&gfx_async_group_stats[3]==oldbuilds);
 check(!gfx_async_group_sources(p,2));
 unsigned epoch=async_hint_epoch;gfx_async_note_repair(commands()+511);check(async_hint_epoch==epoch);
 /* A future change that permits more than512 unique marks must fail closed
  * on log exhaustion, just like the 32-bit epoch wrap. */
 reset();build(8,0);p=first();verify(gfx_async_group_sources(p,0),8,0);
 async_hint_count=512;gfx_async_note_repair(commands()+3);
 check(async_hint_count==1&&async_hint_epoch==1&&async_hint_sources[0]==commands()+2);
 for(unsigned i=0;i<512;i++)check(!async_groups[i].members);
 /* An unrelated repair during a cold build still uses the conservative
  * epoch retry. It must not retain a half-built descriptor vector. */
 reset();build(40,0);p=first();check(gfx_mesh_lookup_impl(commands()+70,1,0)!=NULL);
 inject_wait=4;inject_repair=commands()+71;
 verify(gfx_async_group_sources(p,2),32,2);check(gfx_async_group_stats[4]==1);
 /* A repair callback during a source-resolution wait invalidates a partial
  * build. The retry must exclude that exact member and every later member. */
 reset();build(8,1);p=first();inject_wait=4;inject_repair=commands()+2*3+1;
 verify(gfx_async_group_sources(p,0),3,0);check(gfx_async_group_stats[4]==1);
 /* Real pool recycle during member allocation, then specifically during the
  * table allocation, invalidates every earlier physical offset. */
 reset();build(8,1);p=first();g_cram_vertex_top=CRAM_VERTEX_END-64;
 verify(gfx_async_group_sources(p,2),8,2);check(g_cram_source_generation==2&&drains==1);
 reset();build(8,1);p=first();verify(gfx_async_group_sources(p,2),8,2);
 async_groups[p-mesh_tasks].members=0;g_cram_vertex_top=CRAM_VERTEX_END;
 verify(gfx_async_group_sources(p,2),8,2);check(g_cram_source_generation==2&&drains==1);
 reset();build(8,1);p=first();g_cram_source_generation=UINT32_MAX;g_cram_vertex_top=CRAM_VERTEX_END-64;
 verify(gfx_async_group_sources(p,2),8,2);check(g_cram_source_generation==0);
 reset();build(8,1);p=first();g_cram_vertex_top=CRAM_VERTEX_END;g_cram_vertex_recycled=1;
 check(!gfx_async_group_sources(p,0)&&gfx_async_group_stats[5]==1);
 /* Group availability cannot change AJ02's existing single-job route. */
 reset();build(8,0);p=first();group_supported=0;
 check(!gfx_async_group_sources(p,0)&&!uploads&&!waits);check(p->next==commands()+2);
 /* State and return boundaries terminate the compiler; they are never
  * elided just because the subsequent command bytes look like geometry. */
 reset();build(8,0);commands()[6].words.w0=0xdf000000;
 p=first();verify(gfx_async_group_sources(p,0),2,0);
 reset();build(8,0);commands()[6].words.w0=0xd7000000;
 p=first();verify(gfx_async_group_sources(p,0),2,0);
 uint32_t random=0x72910643;
 for(unsigned trial=0;trial<512;trial++){
  reset();random=random*1664525u+1013904223u;unsigned n=2+random%31;
  build(n,trial&1);p=first();random=random*1664525u+1013904223u;
  g_cram_vertex_top=CRAM_VERTEX_END-64*(random%70);
  verify(gfx_async_group_sources(p,trial&3),n,trial&3);
 }
 printf("{\"status\":\"PASS\",\"checks\":%u,\"random_groups\":512,\"bounded_shape_cases\":62}\n",checks);
}
'''
(O/'test.c').write_text(head+declarations+pool+functions+tail)
cmd=['cc','-std=c11','-O1','-g','-fno-pie','-no-pie','-fsanitize=address,undefined',str(O/'test.c'),'-o',str(O/'test')]
with (O/'build.log').open('w') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
r=subprocess.run([str(O/'test')],capture_output=True,text=True,env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0'))
(O/'run.log').write_text(r.stdout+r.stderr);assert r.returncode==0,r.stderr
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
result=json.loads(r.stdout)
result.update(inputs_sha256={str(p):sha(p) for p in [Path(__file__).resolve(),G/'gfx_gpu.c',G/'gfx_pc.c',G/'gfx_gpu.h',O/'test.c']},
 extracted_functions_sha256={n:hashlib.sha256(fn(pc,n).encode()).hexdigest() for n in names},
 scope=['Actual topology compiler, suffix proof, source memo, repair hint, group builder, allocator and group uploader compiled together. Immutable ELF address bounds use a fixed synthetic image; real32-bit source pointers use a non-PIE executable.',
 'F28 actual lifetime helpers cover all sixteen audited neutral opcodes, unknown/E2/E3 rejection, identical callees with distinct continuations, local calls/branches, second external return, mutable/unaligned/out-of-range targets, stack/scan/cycle bounds, partial overwrites and every TRI/slot read. Group tests prove caller-dependent rejected prefixes rebuild longer under another caller and preserve hint/source-generation invalidation. Actual dispatcher recursion is bound by source assertions; full native replay independently witnesses its runtime continuation.',
 'Upload and hardware-drain callbacks are models that check allocation immutability and copied bytes; this is not RTL progress or full-render equivalence evidence.',
 'Cases cover all2..32member counts and mixed LOAD counts,32member cap, cached singleton/AJ02 fallback, changed cull, per-task repair splits, hint epoch wrap, repair during partial build, whole-group retry on member/table allocation recycle, pool generation wrap, throttle failure, state/end barriers and512 randomized groups. New targeted-hint cases prove unrelated/before/end-boundary hints preserve cache offsets without wait/upload, related hints rebuild, duplicate/unknown callbacks do not advance, log capacity/epoch wrap clear all caches, and unrelated mid-build callbacks conservatively retry.'],
 commands=[cmd,[str(O/'test')]],sanitizers='ASan+UBSan; LSan disabled under ptrace')
(O/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
