#!/usr/bin/env python3
"""Extract actual AJ03 GROUP and AJ02 C ownership helpers and atomic packet builders."""
from pathlib import Path
import hashlib,json,os,re,subprocess
E=Path(__file__).resolve().parent;W=E.parent;G=W/'src/sm64/sm64/src/pc/gfx';O=E/'priority-tests-v1'
assert not O.exists();O.mkdir()
gpu=(G/'gfx_gpu.c').read_text();pc=(G/'gfx_pc.c').read_text()
def fn(s,name):
    m=re.search(r'^[^\n;]*\b'+name+r'\([^;]*?\)\s*\{',s,re.M)
    assert m,name
    a=m.start();b=s.index('{',m.start());i=b+1;depth=1
    while depth:depth+=(s[i]=='{')-(s[i]=='}');i+=1
    return s[a:i]+'\n'
backend=['gpu_async_epoch_check','gpu_async_raw_blocks_jobs','gfx_gpu_async_raw_release_if_pending',
         'gfx_gpu_async_before_wait','gfx_gpu_wait_service','gfx_gpu_async_raw_release',
         'gfx_gpu_async_raw_acquire','gfx_gpu_async_job','gfx_gpu_async_group']
frontend=['load_alias_copy','load_alias_materialize','load_alias_materialize_all','gfx_async_materialize_raw']
original={n:fn(gpu,n)for n in backend}|{n:fn(pc,n)for n in frontend}
driver=fn(pc,'load_alias_vertex_legacy')
assert driver.index('!gfx_gpu_cram_vertex_source(')<driver.index('if(priority) {')<driver.index('gfx_cpu_submit_pending();')<driver.index('gfx_gpu_async_raw_acquire()')<driver.index('load_alias_transaction=1;')<driver.index('unsigned writes=3;')
assert 'if(!priority&&(LOAD_ALIAS_REGS->status&1u))'in driver
assert driver.index('load_lazy_slots = loaded;')<driver.index('load_alias_transaction=0;')
assert 'gfx_gpu_async_raw_acquire'not in fn(pc,'load_alias_vertex')
assert 'gfx_gpu_async_before_wait();'in fn(gpu,'gfx_gpu_async_drain')
assert '#define OF_GPU_WAIT_HOOK() gfx_gpu_wait_service()'in gpu
assert 'gfx_gpu_wait_service();'in fn(gpu,'gpu_wait_flip_fence')
submit=fn(pc,'gfx_async_try_load')
assert submit.index('gfx_gpu_async_raw_release();')<submit.index('gfx_gpu_async_job(')
job=original['gfx_gpu_async_job']
assert job.count('_gpu_stream_reserve_words(')==1 and '_gpu_stream_reserve_words(42u)'in job
assert job.count('_gpu_ring_commit(')==1 and '_gpu_ring_commit(42u)'in job
assert job.index('_gpu_stream_reserve_words(42u)')<job.index('++async_sequence')<job.index('_gpu_ring_commit(42u)')
assert 'async_context'not in gpu
def host(s):
    s=re.sub(r'__asm__ volatile\("fence rw,rw" ::: "memory"\);','host_fence();',s)
    s=re.sub(r'BRAM_GEO_REGS\[(0x[0-9a-f]+)\]=(?!=)([^;]+);',r'write_mmio(\1,\2);',s)
    s=re.sub(r'BRAM_GEO_REGS\[(0x[0-9a-f]+)\]',r'read_mmio(\1)',s)
    s=s.replace('LOAD_ALIAS_REGS->status','raw_status()').replace('__builtin_trap()','test_trap()')
    return s
pre=r'''
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include <setjmp.h>
#include <math.h>
#include "async_mesh_repair.h"
static unsigned checks;
#define C(x) do{++checks;assert(x);}while(0)
#define SM64_PERF_SCOPE(x) ((void)0)
struct Vertex {uint32_t word[9];};
static struct {struct Vertex loaded_vertices[32];} rsp;
static uint32_t raw[32][8];
#define LOAD_ALIAS_VIEW ((const uint32_t *)raw)
static uint32_t load_lazy_slots,load_lazy_first,load_alias_transaction;
static uint32_t gfx_front_stats[16],gfx_async_stats[16];
static uint32_t async_sequence,async_ticket,async_epoch,async_epoch_valid;
static uint32_t async_servicing,async_yielding,async_finished_repair_valid;
static unsigned async_legacy_held,async_legacy_quiescent,sm64_async_front_capability;
static uint32_t hw_epoch,hw_completed,hw_ticket,hw_grant_ticket,hw_target;
static unsigned hw_lease,hw_pending,hw_fault,grant_delay,raw_delay;
static unsigned reads,writes,releases,acquires,fences,services,repair_services,recursion;
static unsigned trap_count,commands,commit_count,reserve_count,kicks,forced,staged,published;
static unsigned force_reserve_wait;
static uint32_t packet[8192],wire[8192];
static jmp_buf jumped;
static void test_trap(void){++trap_count;longjmp(jumped,1);}
static void gpu_async_fault(void){test_trap();}
static void host_fence(void){++fences;}
static uint32_t read_mmio(unsigned r){
 ++reads;
 if(hw_pending&&grant_delay&&!--grant_delay){hw_pending=0;hw_lease=1;hw_grant_ticket=hw_ticket;}
 switch(r){case0:break;default:break;}
 if(r==0x2e)return hw_epoch;
 if(r==0x23)return hw_completed;
 if(r==0x21)return(hw_lease?16u:0u)|(hw_pending?8u:0u)|(hw_fault?4u:0u);
 if(r==0x2f)return hw_lease?hw_grant_ticket:0;
 abort();
}
static void write_mmio(unsigned r,uint32_t v){
 ++writes;
 if(r==0x2b){hw_target=v;return;}
 if(r==0x2c){hw_ticket=v;return;}
 if(r==0x2d&&v==1){C(!hw_lease&&!hw_pending);hw_pending=1;++acquires;if(!grant_delay)grant_delay=4;return;}
 if(r==0x2d&&v==2){C(hw_lease);C(hw_grant_ticket==async_ticket);C(!raw_delay);hw_lease=0;++releases;return;}
 abort();
}
static uint32_t raw_status(void){if(raw_delay){--raw_delay;return 1;}return 6;}
void gfx_gpu_async_raw_release(void);
void gfx_gpu_async_before_wait(void);
void gfx_gpu_async_raw_release_if_pending(void);
void gfx_async_materialize_raw(void);
void gfx_gpu_wait_service(void);
static void gfx_gpu_queued_front_service(void){++services;}
static void gfx_gpu_async_service(void){++repair_services;if(recursion){recursion=0;gfx_gpu_wait_service();}}
static void of_gpu_kick_now(void){++forced;memcpy(wire+published,packet,staged*4);published+=staged;staged=0;}
static void of_gpu_kick(void){++kicks;if(staged>=512)of_gpu_kick_now();}
static unsigned sequence_drains;
static void gfx_gpu_async_drain(void){of_gpu_kick_now();hw_completed=async_sequence;++sequence_drains;}
static void _gpu_stream_reserve_words(unsigned n){
 C(n==42||n==38);++reserve_count;
 if(force_reserve_wait){unsigned old=async_sequence;gfx_gpu_wait_service();C(async_sequence==old);force_reserve_wait=0;}
 C(staged+n<8192);
}
static uint32_t *_gpu_ring_claim(void){return packet+staged;}
static void _gpu_ring_commit(unsigned n){C(n==42||n==38);C(packet[staged]==0x6300001d);C(packet[staged+1]==0);C(packet[staged+30]==(n==42?0x6400000b:0x65000007));staged+=n;++commit_count;}
static int g_cd_active,g_subpix_tri,g_surf_alpha,g_vc_textured,g_vc_vp_valid,g_geometry_matrix_valid;
static int32_t g_clip_cx,g_clip_cy,g_clip_hw,g_clip_hh;
static float g_cx,g_cy,g_hw,g_hh,g_vc_vp[4];
static uint32_t g_vc_words;
struct gfx_bram_import {int32_t u_offset,v_offset;uint32_t flags_rgb;};
typedef struct {int32_t xcenter,ycenter,xscale,yscale,near_clip,rows;} of_gpu_object_state_t;
static void emit_tri_state(int a){(void)a;++commands;}
static void of_gpu_set_object_state(const of_gpu_object_state_t *s){(void)s;++commands;}
'''.replace(' switch(r){case0:break;default:break;}','')
body=''.join(host(original[n])for n in backend+frontend)
post=r'''
static void reset(void){
 memset(&rsp,0xa5,sizeof(rsp));memset(raw,0,sizeof(raw));memset(gfx_front_stats,0,sizeof(gfx_front_stats));
 async_sequence=10;async_ticket=7;async_epoch=hw_epoch=2;async_epoch_valid=1;
 hw_completed=9;hw_ticket=hw_grant_ticket=7;hw_lease=async_legacy_held=1;hw_pending=hw_fault=0;
 async_yielding=async_servicing=load_alias_transaction=async_legacy_quiescent=0;sm64_async_front_capability=1;
 load_lazy_slots=0;load_lazy_first=0;raw_delay=grant_delay=0;
 reads=writes=releases=acquires=fences=services=repair_services=recursion=0;
 trap_count=commands=commit_count=reserve_count=kicks=forced=staged=published=force_reserve_wait=0;
 sequence_drains=0;g_vc_vp_valid=1;g_vc_words=0;
 for(unsigned i=0;i<32;i++){for(unsigned j=0;j<8;j++)raw[i][j]=0x40000000+i*256+j;raw[i][5]=(uint16_t)(-32768+(int)i*2000)|((uint32_t)(uint16_t)(32767-(int)i*1900)<<16);}
}
static void expected(struct Vertex *v,unsigned source){
 memcpy(v->word,raw[source],5*4);float u=(int16_t)raw[source][5],w=(int16_t)(raw[source][5]>>16);
 memcpy(v->word+5,&u,4);memcpy(v->word+6,&w,4);v->word[7]=raw[source][6];v->word[8]=raw[source][7];
}
int main(void){
 reset();async_legacy_held=0;gfx_gpu_async_before_wait();C(!reads&&!releases&&!commands);
 reset();sm64_async_front_capability=2;gfx_gpu_async_before_wait();C(!reads&&!releases);
 reset();hw_completed=async_sequence;load_lazy_slots=31;gfx_gpu_async_before_wait();C(async_legacy_held&&load_lazy_slots==31&&!releases);
 unsigned old_reads=reads;for(unsigned i=0;i<1000;i++){gfx_gpu_async_before_wait();C(!gfx_gpu_async_raw_acquire());}C(reads==old_reads);
 /* Quiescent ownership remains safe after a consumer reset: no job can be
  * introduced without releasing; the actual release still checks the epoch. */
 hw_epoch++;gfx_gpu_async_before_wait();C(reads==old_reads&&async_legacy_held);gfx_gpu_async_raw_release();C(!async_legacy_held&&!async_legacy_quiescent&&releases==1);
 reset();load_lazy_first=3;load_lazy_slots=31u<<3;gfx_gpu_async_before_wait();
 C(!async_legacy_held&&!load_lazy_slots&&releases==1&&!commands&&!staged);
 for(unsigned i=0;i<32;i++){struct Vertex e;memset(&e,0xa5,sizeof e);if(i>=3&&i<8)expected(&e,i-3);C(!memcmp(&e,&rsp.loaded_vertices[i],sizeof e));}
 /* Delayed already-accepted raw reads drain; a reentrant wait cannot recurse
  * into copying/releasing the same raw owner. */
 reset();load_lazy_slots=15;raw_delay=8;recursion=1;gfx_gpu_async_before_wait();C(repair_services==8&&services==1&&releases==1&&!async_yielding);
 reset();load_lazy_slots=15;load_alias_transaction=1;if(!setjmp(jumped)){gfx_gpu_async_before_wait();abort();}C(trap_count==1&&!releases&&load_lazy_slots==15);
 reset();hw_epoch++;hw_completed=10;load_lazy_slots=7;gfx_gpu_async_before_wait();C(async_legacy_held&&load_lazy_slots==7&&!releases);
 reset();hw_epoch++;hw_lease=0;if(!setjmp(jumped)){gfx_gpu_async_before_wait();abort();}C(trap_count==1&&!releases);
 /* Materialize masks preserve untouched canonical/fog words and leave a
  * sparse intermediate mask owned. The remaining runs finish before release. */
 for(unsigned first=0;first<32;first++)for(unsigned count=1;count<=32-first;count++){
  reset();load_lazy_first=first;uint32_t mask=(count==32?~0u:((1u<<count)-1u))<<first;load_lazy_slots=mask;
  uint32_t half=mask&0x55555555u;load_alias_materialize(half);C(load_lazy_slots==(mask&~half));
  C(releases==(load_lazy_slots==0));load_alias_materialize(~half);C(!load_lazy_slots&&releases==1);
  for(unsigned i=0;i<32;i++){struct Vertex e;memset(&e,0xa5,sizeof e);if(mask&(1u<<i))expected(&e,i-first);C(!memcmp(&e,&rsp.loaded_vertices[i],sizeof e));}
 }
 /* Partial overwrite barrier: copy survivors, drop overwritten raw rows;
  * no discarded row may leak into unrelated canonical/fog slots. */
 for(unsigned first=0;first<32;first++)for(unsigned count=1;count<=32-first;count++)for(unsigned dest=0;dest<32;dest+=3){
  reset();load_lazy_first=first;uint32_t mask=(count==32?~0u:((1u<<count)-1u))<<first;load_lazy_slots=mask;
  uint32_t overwritten=0xffffffffu<<dest;load_alias_materialize(~overwritten);load_lazy_slots&=~overwritten;if(!load_lazy_slots)gfx_gpu_async_raw_release_if_pending();
  C(!load_lazy_slots&&releases==1);for(unsigned i=0;i<32;i++){struct Vertex e;memset(&e,0xa5,sizeof e);if((mask&~overwritten)&(1u<<i))expected(&e,i-first);C(!memcmp(&e,&rsp.loaded_vertices[i],sizeof e));}
 }
 /* Entire context+JOB is one staged unit. An injected reserve wait happens
  * before sequence advancement/any context stores; source pointers and all
  * context words survive unchanged. Legacy overwrite does not use a delta. */
 for(unsigned trial=0;trial<1024;trial++){
  reset();hw_lease=async_legacy_held=0;struct am_transform t;uint32_t *tw=(uint32_t *)&t;
  for(unsigned i=0;i<24;i++)tw[i]=trial*65537u+i*101u;
  struct gfx_bram_import m={.u_offset=(int)trial*8,.v_offset=(int)trial*16,.flags_rgb=trial|0x10000};
  g_clip_cx=trial;g_clip_cy=trial+1;g_clip_hw=trial+2;g_clip_hh=trial+3;force_reserve_wait=1;
  gfx_gpu_async_job(0x1000,0x01003006,0x2000,7,2,(void *)(uintptr_t)0x10000000,(void *)(uintptr_t)0x10000100,&t,77,&m);
  C(staged==42&&published==0&&async_sequence==11&&reserve_count==1&&commit_count==1);
  for(unsigned i=0;i<24;i++)C(packet[i+2]==tw[i]);
  C(packet[26]==trial&&packet[27]==trial+1&&packet[28]==trial+2&&packet[29]==trial+3);
  C(packet[31]==11&&packet[32]==0x1000&&packet[33]==0x01003006&&packet[34]==0x2000&&packet[35]==(7|(2<<8)));
  C(packet[36]==m.flags_rgb&&packet[37]==(uint32_t)m.u_offset&&packet[38]==(uint32_t)m.v_offset&&packet[39]==0x10000000&&packet[40]==0x10000100&&packet[41]==77);
  /* Acquire publishes the staged bundle but can be granted while completed
   * remains nine: grant is deliberately not a source-lifetime fence. */
  grant_delay=11;C(gfx_gpu_async_raw_acquire()==1);C(async_legacy_held&&published==42&&hw_completed==9&&hw_target==11);
  C(!memcmp(wire,packet,42*4));load_lazy_slots=7;gfx_gpu_async_before_wait();C(!async_legacy_held&&!load_lazy_slots&&releases==1&&hw_completed==9);
 }

 /* Each GROUP is one atomic context+descriptor pointer. Sequence accounting
  * reserves every member before any following legacy acquire; partial member
  * completion cannot make the remaining source readers invisible. */
 for(unsigned members=1;members<=32;members++)for(unsigned wrap=0;wrap<2;wrap++){
  reset();hw_lease=async_legacy_held=0;
  struct am_transform t;uint32_t *tw=(uint32_t *)&t;
  for(unsigned i=0;i<24;i++)tw[i]=0x52491234u+i*91u+members;
  struct gfx_bram_import m={.u_offset=32,.v_offset=64,.flags_rgb=0x73456};
  g_clip_cx=12;g_clip_cy=34;g_clip_hw=56;g_clip_hh=78;
  if(wrap)async_sequence=UINT32_MAX-members+1;
  uint32_t first=wrap?1:11,last=first+members-1;
  force_reserve_wait=1;
  gfx_gpu_async_group(0x00fff000,members,&t,201,&m);
  C(staged==38&&async_sequence==last&&reserve_count==1&&commit_count==1);
  C(sequence_drains==wrap);
  for(unsigned i=0;i<24;i++)C(packet[i+2]==tw[i]);
  C(packet[26]==12&&packet[27]==34&&packet[28]==56&&packet[29]==78);
  C(packet[31]==first&&packet[32]==0x00fff000&&packet[33]==members);
  C(packet[34]==m.flags_rgb&&packet[35]==32&&packet[36]==64&&packet[37]==201);
  hw_completed=first-1;grant_delay=5;C(gfx_gpu_async_raw_acquire()==1);
  C(hw_target==last&&hw_completed==first-1&&published==38);
  load_lazy_slots=7;gfx_gpu_async_before_wait();C(releases==1&&!load_lazy_slots&&!async_legacy_held);
 }
 reset();hw_lease=async_legacy_held=0;struct am_transform invalid_t={0};struct gfx_bram_import invalid_m={0};
 if(!setjmp(jumped)){gfx_gpu_async_group(0,0,&invalid_t,0,&invalid_m);abort();}
 C(trap_count==1&&reserve_count==0);
 reset();hw_lease=async_legacy_held=0;
 if(!setjmp(jumped)){gfx_gpu_async_group(0,33,&invalid_t,0,&invalid_m);abort();}
 C(trap_count==1&&reserve_count==0);
 printf("{\"status\":\"PASS\",\"checks\":%u,\"atomic_bundle_cases\":1088,\"mask_cases\":6336}\n",checks);
}
'''
(O/'test.c').write_text(pre+body+post)
cmd=['cc','-std=c11','-O1','-g','-fsanitize=address,undefined','-I'+str(G),str(O/'test.c'),'-lm','-o',str(O/'test')]
subprocess.run(cmd,check=True)
r=subprocess.run([str(O/'test')],capture_output=True,text=True,env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0'))
(O/'run.log').write_text(r.stdout+r.stderr);assert r.returncode==0,r.stderr
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
result=json.loads(r.stdout)
result.update(inputs_sha256={str(p):sha(p)for p in [Path(__file__).resolve(),G/'gfx_gpu.c',G/'gfx_pc.c',G/'gfx_gpu.h',G/'async_mesh_repair.h',O/'test.c']},
 extracted_functions_sha256={n:hashlib.sha256(s.encode()).hexdigest()for n,s in original.items()},
 substitutions=['MMIO accesses use independent model read/write functions; raw status uses a delayed completion model; target fence becomes an observed host fence; trap becomes longjmp. Conditions and control flow unchanged.',
 'Canonical/raw storage layout is exact nine/eight words; reserve/commit/MMIO/other GPU calls are host stubs that check ownership and emission order. This is not RTL equivalence or timing evidence.'],
 source_order_checks=['Source resolution and pending submit precede last-safe-point acquisition/context writes.',
 'Result lazy mask is registered before transaction flag clears.',
 'New async submission explicitly releases the previous raw lease.',
 'SDK wait macro, explicit flip fence and source lifetime drain invoke the central nonemitting yield.',
 'One42-word context+JOB or38-word context+GROUP reserve/claim/commit; complete member sequence reservation, wrap drains before reuse, no callback inside bundle.'],
 commands=[cmd,[str(O/'test')]],sanitizers='ASan+UBSan; LSan disabled under ptrace')
(O/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
