#!/usr/bin/env python3
"""Compile actual app admission/lease helpers against independent host fixtures."""
from pathlib import Path
import hashlib,json,subprocess,os
E=Path(__file__).resolve().parent;W=E.parents[1];G=W/'src/sm64/sm64/src/pc/gfx'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
assert not(E/'result.json').exists()
def function(src,name):
 start=src.index(name+'(');start=src.rfind('\n',0,start)+1
 begin=src.index('{',start);depth=1;i=begin+1
 while depth:
  depth+=(src[i]=='{')-(src[i]=='}');i+=1
 return src[start:i]+'\n'
pc=(G/'gfx_pc.c').read_text();gpu=(G/'gfx_gpu.c').read_text()
admit=function(pc,'gfx_async_dead_suffix')
lease=''.join(function(gpu,n) for n in ['gpu_async_epoch_check','gfx_gpu_async_drain','gfx_gpu_async_raw_acquire','gfx_gpu_async_raw_release'])
lease=lease.replace('__asm__ volatile("fence rw,rw" ::: "memory");','/* Host fixture replaces ordering instruction only. */')
pre=r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
typedef struct {struct {uint32_t w0,w1;} words;} Gfx;
enum {G_VTX=1,G_TRI1=5,G_TRI2=6};
static Gfx memory[512];
#define _start memory[0]
#define __EH_FRAME_BEGIN__ (memory+512)
static uint32_t regs[64];
#define BRAM_GEO_REGS regs
static uint32_t sm64_async_front_capability,gfx_async_stats[16];
static uint32_t async_sequence,async_ticket,async_epoch,async_epoch_valid;
static uint32_t async_context_valid,async_finished_repair_valid,async_legacy_held;
static unsigned kicks,services,checks;
static void gpu_async_fault(void){fprintf(stderr,"unexpected fault\n");abort();}
static void of_gpu_kick_now(void){++kicks;}
static void gfx_gpu_async_service(void){++services;regs[0x23]=async_sequence;}
#define C(x) do{++checks;assert(x);}while(0)
'''
post=r'''
static uint32_t rng=0x19930623;
static uint32_t rnd(void){rng^=rng<<13;rng^=rng>>17;return rng^=rng<<5;}
static unsigned oracle(unsigned start,unsigned n) {
 unsigned live[32];for(unsigned i=0;i<32;i++)live[i]=i<n;
 for(unsigned step=0;step<256;step++,start++) {
  if(start>=512)return 0;
  uint32_t a=memory[start].words.w0,b=memory[start].words.w1;unsigned op=a>>24;
  if(op==1) {
   unsigned count=(a>>12)&255,end=(a>>1)&127;
   if(!count||count>32||end<count||end>32)return 0;
   for(unsigned i=end-count;i<end;i++)live[i]=0;
   unsigned any=0;for(unsigned i=0;i<32;i++)any|=live[i];if(!any)return 1;
  } else if(op==5||op==6) {
   unsigned words=op==6?2:1;
   for(unsigned w=0;w<words;w++)for(unsigned s=0;s<3;s++) {
    unsigned v=(((w?b:a)>>(s*8))&255)/2;
    if(v>=32||live[v])return 0;
   }
  } else return 0;
 }
 return 0;
}
static uint32_t vtx(unsigned n,unsigned first){return 0x01000000u|(n<<12)|((first+n)<<1);}
int main(void) {
 for(unsigned test=0;test<30000;test++) {
  memset(memory,0,sizeof(memory));unsigned n=rnd()%32+1;
  for(unsigned i=0;i<512;i++) {
   unsigned op=rnd()%7;
   if(op<3){unsigned count=rnd()%32+1,first=rnd()%(33-count);memory[i].words.w0=vtx(count,first);}
   else if(op<6) {
    uint32_t a=0,b=0;for(unsigned j=0;j<3;j++){a|=((rnd()%36)*2)<<(j*8);b|=((rnd()%36)*2)<<(j*8);}
    memory[i].words.w0=a|((op==5?6:5)<<24);memory[i].words.w1=b;
   } else memory[i].words.w0=(rnd()%256)<<24;
  }
  unsigned start=rnd()%512;
  C(gfx_async_dead_suffix(memory+start,n)==oracle(start,n));
 }
 memset(memory,0,sizeof(memory));memory[0].words.w0=vtx(16,0);C(gfx_async_dead_suffix(memory,16));
 memory[0].words.w0=vtx(8,0);memory[1].words.w0=0x05000000;memory[2].words.w0=vtx(8,8);C(gfx_async_dead_suffix(memory,16));
 memory[1].words.w0=0x05101010;C(!gfx_async_dead_suffix(memory,16));
 for(unsigned op=0;op<256;op++)if(op!=1&&op!=5&&op!=6){memory[0].words.w0=op<<24;memory[1].words.w0=vtx(32,0);C(!gfx_async_dead_suffix(memory,32));}
 memory[511].words.w0=0x05000000;C(!gfx_async_dead_suffix(memory+511,32));
 /* Unsupported capability is inert, even with stale local held state. */
 regs[0x2d]=99;C(!gfx_gpu_async_raw_acquire());gfx_gpu_async_raw_release();C(regs[0x2d]==99);
 sm64_async_front_capability=1;regs[0x2e]=7;regs[0x23]=3;regs[0x21]=16;regs[0x2f]=1;
 C(gfx_gpu_async_raw_acquire()==1);C(regs[0x2b]==3&&regs[0x2c]==1&&regs[0x2d]==1&&async_legacy_held);C(!kicks);
 regs[0x2d]=77;C(gfx_gpu_async_raw_acquire()==0);C(regs[0x2d]==77&&async_ticket==1);
 /* Draining earlier jobs must not wait on our own idle raw lease. */
 gfx_gpu_async_drain();C(!services&&!kicks&&async_legacy_held);
 /* Consumer reset retains the ticket/raw view but invalidates sent context. */
 async_context_valid=1;regs[0x2e]=8;regs[0x23]=0;C(!gpu_async_epoch_check());C(async_legacy_held&&!async_context_valid&&async_sequence==0);
 C(!gfx_gpu_async_raw_acquire());gfx_gpu_async_raw_release();C(!async_legacy_held&&regs[0x2d]==2);
 regs[0x2d]=88;gfx_gpu_async_raw_release();C(regs[0x2d]==88);
 /* Outstanding staged jobs are published before acquire and source drain. */
 async_sequence=4;regs[0x23]=3;regs[0x2f]=2;regs[0x21]=16;C(gfx_gpu_async_raw_acquire());C(kicks==1&&regs[0x2b]==4&&async_ticket==2);
 regs[0x23]=4;gfx_gpu_async_raw_release();async_sequence=5;gfx_gpu_async_drain();C(kicks==2&&services==1&&regs[0x23]==5);
 printf("%u\n",checks);return 0;
}
'''
source=E/'test.c';source.write_text(pre+admit+lease+post)
cmd=['cc','-std=c11','-O1','-g','-fno-omit-frame-pointer','-fsanitize=address,undefined',str(source),'-o',str(E/'test')]
subprocess.run(cmd,check=True);p=subprocess.run([str(E/'test')],capture_output=True,text=True,check=True,env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0'))
checks=int(p.stdout.strip());assert checks>=30200
report={'status':'PASS','checks':checks,'scope':['Actual C cached suffix helper vs independent slot-array oracle, 30,000 random streams and directed overwrite/read/state/bounds cases.','Actual acquire/release/epoch/drain functions against inert MMIO fixture: absent capability, same-ticket reuse, retained lease on consumer reset, source drain under own idle lease, publication before pending-job waits. Host replaces only fence instruction.','Does not simulate RTL repair insertion or establish full-frame performance.'], 'command':cmd,'inputs_sha256':{str(p):sha(p) for p in [Path(__file__).resolve(),G/'gfx_pc.c',G/'gfx_gpu.c',source]},'helper_sha256':{'admission':hashlib.sha256(admit.encode()).hexdigest(),'lease':hashlib.sha256(lease.encode()).hexdigest()}}
(E/'result.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'status':'PASS','checks':checks}))
