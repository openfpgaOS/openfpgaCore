#!/usr/bin/env python3
"""Actual source allocator plus parallel task memo; generation/shape/lifetime tests."""
from pathlib import Path
import hashlib,json,os,re,subprocess
E=Path(__file__).resolve().parent;W=E.parent;G=W/'src/sm64/sm64/src/pc/gfx';O=E/'source-cache-tests-v1'
assert not O.exists();O.mkdir()
gpu=(G/'gfx_gpu.c').read_text();pc=(G/'gfx_pc.c').read_text()
def fn(s,name):
 m=re.search(r'^[^\n;]*\b'+name+r'\([^;]*?\)\s*\{',s,re.M);assert m,name
 a=m.start();b=s.index('{',a);i=b+1;depth=1
 while depth:depth+=(s[i]=='{')-(s[i]=='}');i+=1
 return s[a:i]+'\n'
def declaration(name):
 a=pc.index('struct '+name+' {');b=pc.index('};',a)+2;return pc[a:b]+'\n'
pool=gpu[gpu.index('#define CRAM_VERTEX_BEGIN'):gpu.index('static __attribute__((cold, noinline)) void gpu_fast_tex_fallback')]
assert 'gfx_async_forget_sources();'in pool
lookup=fn(pc,'gfx_mesh_lookup')
assert lookup.index('entry->source=cmd;')<lookup.index('async_task_sources[entry-mesh_tasks].vertices=NULL;')<lookup.index('entry->prefer_legacy=0;')
assert 'gfx_async_route_task(e,asynchronous)'in lookup and 'gfx_async_route_task(entry,asynchronous)'in lookup
tr=fn(pc,'gfx_async_try_load')
assert 'struct am_transform transform={0}'not in tr
assert 'transform.coefficient[0]=transform.coefficient[1]=transform.coefficient[2]=0;'in tr
assert 'transform.ambient=transform.diffuse=0;'in tr
assert tr.index('if(async_armed->prefer_legacy)')<tr.index('gfx_async_sources(')
head=r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef struct {struct {uint32_t w0,w1;} words;} Gfx;
static uint32_t memory[0x1000000/4];
static unsigned owner,waits,drains,uploads,checks;
static uint32_t gfx_async_stats[16],gfx_async_hint_stats[4],gfx_async_source_stats[4];
static void check(int v){++checks;assert(v);}
void gfx_gpu_async_drain(void){++drains;owner=0;}
void gfx_gpu_mesh_wait_source(void){++waits;owner=0;}
void gfx_async_forget_sources(void);
void _of_gpu_fast_tex_upload(uint32_t address,const uint32_t *source,unsigned count){
 check(!owner);check(!(address&3));check(address+count*4<=sizeof(memory));
 memcpy((uint8_t*)memory+address,source,count*4);++uploads;
}
'''
decl=declaration('MeshTask')+declaration('AsyncTaskSources')+'static struct MeshTask mesh_tasks[512];\nstatic struct AsyncTaskSources async_task_sources[512];\n'
functions=''.join(fn(pc,n)for n in ['gfx_async_forget_sources','gfx_async_sources','gfx_async_route_task'])
tail=r'''
static uint32_t vertices[32*6],other_vertices[32*6];
static Gfx command_memory[65],other_commands[65];
static void reset(void){
 memset(g_cram_vertex_source,0,sizeof(g_cram_vertex_source));memset(g_cram_mesh_source,0,sizeof(g_cram_mesh_source));
 memset(async_task_sources,0,sizeof(async_task_sources));memset(mesh_tasks,0,sizeof(mesh_tasks));memset(gfx_async_source_stats,0,sizeof(gfx_async_source_stats));
 memset(gfx_async_stats,0,sizeof(gfx_async_stats));memset(gfx_async_hint_stats,0,sizeof(gfx_async_hint_stats));
 g_cram_vertex_top=CRAM_VERTEX_BEGIN;g_cram_source_generation=1;g_cram_vertex_enabled=1;g_cram_vertex_recycled=0;
 g_cram_vertex_frame=g_cram_vertex_last_recycle=0;uploads=waits=owner=drains=0;
 mesh_tasks[0].source=command_memory;mesh_tasks[0].commands=64;mesh_tasks[0].next=command_memory+64;
 mesh_tasks[1].source=other_commands;mesh_tasks[1].commands=1;mesh_tasks[1].next=other_commands+1;
}
static void pair(unsigned slot,const void *vertex,unsigned count,uint32_t v,uint32_t c){
 check(v+count*24<=c||c+mesh_tasks[slot].commands*8<=v);
 check(!memcmp((uint8_t*)memory+v,vertex,count*24));
 check(!memcmp((uint8_t*)memory+c,mesh_tasks[slot].source+1,mesh_tasks[slot].commands*8));
}
int main(void){
 for(unsigned i=0;i<32*6;i++){vertices[i]=0x72481111u^(i*1217u);other_vertices[i]=vertices[i]^0xffffffffu;}
 for(unsigned i=0;i<65;i++){command_memory[i].words.w0=0x05224466u+i;command_memory[i].words.w1=0xabcdef12u^i;other_commands[i]=command_memory[64-i];}
 uint32_t v,c,v2,c2;
 reset();owner=1;check(gfx_async_sources(mesh_tasks,vertices,32,&v,&c));pair(0,vertices,32,v,c);check(uploads==2&&gfx_async_source_stats[1]==1);
 owner=1;unsigned w=waits,u=uploads;check(gfx_async_sources(mesh_tasks,vertices,32,&v2,&c2));check(owner&&waits==w&&uploads==u&&v==v2&&c==c2);check(gfx_async_source_stats[0]==1);
 check(gfx_async_sources(mesh_tasks,other_vertices,32,&v2,&c2));pair(0,other_vertices,32,v2,c2);check(v2!=v&&c2==c);
 check(gfx_async_sources(mesh_tasks,other_vertices,2,&v2,&c2));pair(0,other_vertices,2,v2,c2);check(async_task_sources[0].counts==(2u|(64u<<16)));
 mesh_tasks[0].commands=1;check(gfx_async_sources(mesh_tasks,other_vertices,2,&v2,&c2));pair(0,other_vertices,2,v2,c2);check(async_task_sources[0].counts==(2u|(1u<<16)));
 /* Real allocator recycle between vertex and command resolution invalidates
  * every memo, including the one being rebound; it then retries both offsets. */
 reset();g_cram_vertex_top=CRAM_VERTEX_END-64;mesh_tasks[0].commands=1;
 check(gfx_async_sources(mesh_tasks,vertices,2,&v,&c));check(g_cram_source_generation==2&&gfx_async_source_stats[3]==1&&drains==1);pair(0,vertices,2,v,c);
 check(async_task_sources[0].generation==2);u=uploads;check(gfx_async_sources(mesh_tasks,vertices,2,&v2,&c2));check(uploads==u&&v==v2&&c==c2);
 /* A32-bit generation wrap does not let an ancient generation-zero memo
  * survive: the actual recycle callback clears keys before storage reuse. */
 reset();g_cram_source_generation=0xffffffffu;mesh_tasks[0].commands=1;
 check(gfx_async_sources(mesh_tasks,vertices,2,&v,&c));check(async_task_sources[0].vertices==vertices);
 g_cram_vertex_top=CRAM_VERTEX_END-64;check(gfx_async_sources(mesh_tasks+1,other_vertices,2,&v2,&c2));
 check(g_cram_source_generation==0&&async_task_sources[0].vertices==NULL&&async_task_sources[1].generation==0);pair(1,other_vertices,2,v2,c2);
 check(gfx_async_sources(mesh_tasks,vertices,2,&v,&c));pair(0,vertices,2,v,c);
 reset();g_cram_vertex_top=CRAM_VERTEX_END;g_cram_vertex_recycled=1;
 check(!gfx_async_sources(mesh_tasks,vertices,32,&v,&c));check(!async_task_sources[0].vertices&&gfx_async_source_stats[2]==1);
 /* Static dead-slot/historical route hints only select the unchanged legacy
  * path. Preserve hint precedence and accounting; MTF callers retain entries. */
 for(unsigned asynchronous=0;asynchronous<2;asynchronous++)for(unsigned live=0;live<2;live++)for(unsigned dead=0;dead<2;dead++)for(unsigned hint=0;hint<2;hint++){
  reset();struct MeshTask *t=mesh_tasks;t->next=live?command_memory+1:NULL;t->dead_after=dead;t->prefer_legacy=hint;
  const struct MeshTask *got=gfx_async_route_task(t,asynchronous);
  unsigned filtered=live&&asynchronous&&(hint||!dead);
  check(got==(live&&!filtered?t:NULL));check(gfx_async_stats[0]==filtered);check(gfx_async_hint_stats[3]==(filtered&&hint));check(gfx_async_stats[1]==(filtered&&!hint));
 }
 uint32_t rng=0x49183271;
 for(unsigned i=0;i<4096;i++){
  reset();rng=rng*1664525u+1013904223u;unsigned nv=1+(rng&31);
  rng=rng*1664525u+1013904223u;unsigned nc=1+(rng&63);mesh_tasks[0].commands=nc;
  rng=rng*1664525u+1013904223u;g_cram_vertex_top=CRAM_VERTEX_END-64*(rng%22);
  owner=1;check(gfx_async_sources(mesh_tasks,vertices,nv,&v,&c));pair(0,vertices,nv,v,c);
  u=uploads;w=waits;owner=1;check(gfx_async_sources(mesh_tasks,vertices,nv,&v2,&c2));
  check(owner&&waits==w&&uploads==u&&v2==v&&c2==c);check(async_task_sources[0].generation==g_cram_source_generation);
 }
 printf("{\"status\":\"PASS\",\"checks\":%u,\"random_source_pairs\":4096,\"route_combinations\":16}\n",checks);
}
'''
(O/'test.c').write_text(head+decl+pool+functions+tail)
cmd=['cc','-std=c11','-O1','-g','-fsanitize=address,undefined',str(O/'test.c'),'-o',str(O/'test')]
subprocess.run(cmd,check=True);r=subprocess.run([str(O/'test')],capture_output=True,text=True,env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0'))
(O/'run.log').write_text(r.stdout+r.stderr);assert r.returncode==0,r.stderr
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();result=json.loads(r.stdout)
result.update(inputs_sha256={str(p):sha(p)for p in [Path(__file__).resolve(),G/'gfx_gpu.c',G/'gfx_pc.c',O/'test.c']},
 scope=['Actual unchanged pool allocator/source API and new task memo/route functions are compiled together. Stub upload checks exact bytes/nonoverlap; owner drain is an API assumption, separately verified by native/RTL lifetime checks.',
 'Tests include true allocator generation change between vertex/command allocations, full32-bit wrap, shape/key changes, throttled failure, immutable cache hits while a reader owns old sources, and exact route-counter preservation.',
 'Static source assertion requires topology slot initialization to invalidate its parallel offset key. Full runtime caches/topology/native rendering require the paired replay.'],commands=[cmd,[str(O/'test')]],sanitizers='ASan+UBSan; LSan disabled under ptrace')
(O/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
