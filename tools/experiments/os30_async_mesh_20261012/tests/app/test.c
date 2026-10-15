
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
static unsigned gfx_async_dead_suffix(const Gfx *scan,unsigned count) {
    uint32_t pending=count==32?0xffffffffu:((1u<<count)-1u);
    const uintptr_t end=(uintptr_t)__EH_FRAME_BEGIN__;
    for(unsigned steps=0;steps<256;++steps,++scan) {
        const uintptr_t p=(uintptr_t)scan;
        if((p&7u)||p<(uintptr_t)&_start||p>end-8u)return 0;
        const uint32_t a=scan->words.w0,b=scan->words.w1;
        const unsigned op=a>>24;
        if(op==(uint8_t)G_VTX) {
            const unsigned n=(a>>12)&255u,limit=(a>>1)&127u;
            if(!n||n>32||limit<n||limit>32)return 0;
            const unsigned first=limit-n;
            const uint32_t mask=n==32?0xffffffffu:((1u<<n)-1u)<<first;
            pending&=~mask;
            if(!pending)return 1;
        } else if(op==(uint8_t)G_TRI1||op==(uint8_t)G_TRI2) {
            const unsigned n=op==(uint8_t)G_TRI2?2u:1u;
            for(unsigned t=0;t<n;++t) {
                const uint32_t w=t?b:a;
                for(unsigned shift=0;shift<=16;shift+=8) {
                    const unsigned slot=((w>>shift)&255u)/2u;
                    if(slot>=32||(pending&(1u<<slot)))return 0;
                }
            }
        } else return 0;
    }
    return 0;
}
static int gpu_async_epoch_check(void) {
#ifndef OF_PC
    const uint32_t now=BRAM_GEO_REGS[0x2e];
    if(!async_epoch_valid||now!=async_epoch) {
        const int changed=async_epoch_valid;
        async_epoch=now;async_epoch_valid=1;
        async_sequence=BRAM_GEO_REGS[0x23];
        async_context_valid=0;async_finished_repair_valid=0;
        /* Consumer reset preserves a granted legacy lease and raw bank. */
        if(async_legacy_held && (!(BRAM_GEO_REGS[0x21]&16u)||
           BRAM_GEO_REGS[0x2f]!=async_ticket))gpu_async_fault();
        return !changed;
    }
#endif
    return 1;
}
void gfx_gpu_async_drain(void) {
#ifndef OF_PC
    if(sm64_async_front_capability!=1u)return;
    gpu_async_epoch_check();
    if(BRAM_GEO_REGS[0x23]!=async_sequence)of_gpu_kick_now();
    for(;;) {
        if(!gpu_async_epoch_check())return;
        if(BRAM_GEO_REGS[0x23]==async_sequence)return;
        gfx_gpu_async_service();++gfx_async_stats[12];
    }
#endif
}
int gfx_gpu_async_raw_acquire(void) {
#ifndef OF_PC
    if(sm64_async_front_capability!=1u)return 0;
    gpu_async_epoch_check();
    if(async_legacy_held)return 0;
    if(BRAM_GEO_REGS[0x23]!=async_sequence)of_gpu_kick_now();
retry:
    gpu_async_epoch_check();
    if(!++async_ticket)++async_ticket;
    BRAM_GEO_REGS[0x2b]=async_sequence;
    BRAM_GEO_REGS[0x2c]=async_ticket;
    BRAM_GEO_REGS[0x2d]=1;
    for(;;) {
        if(!gpu_async_epoch_check())goto retry;
        const uint32_t status=BRAM_GEO_REGS[0x21];
        if(status&4u)gpu_async_fault();
        if((status&16u)&&BRAM_GEO_REGS[0x2f]==async_ticket)break;
        gfx_gpu_async_service();++gfx_async_stats[11];
    }
    async_legacy_held=1;
    ++gfx_async_stats[10];return 1;
#else
    return 0;
#endif
}
void gfx_gpu_async_raw_release(void) {
#ifndef OF_PC
    if(sm64_async_front_capability!=1u||!async_legacy_held)return;
    gpu_async_epoch_check();
    /* Host fixture replaces ordering instruction only. */
    BRAM_GEO_REGS[0x2d]=2;
    async_legacy_held=0;async_context_valid=0;
#endif
}

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
