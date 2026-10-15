#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
typedef struct {unsigned n,slots[6];} Gfx;
static uint32_t load_bram_slots,load_queued_safe,load_queued_hash[4];
static unsigned load_bram_first;
static unsigned gfx_geometry_decode(const Gfx *c,unsigned *slots){memcpy(slots,c->slots,sizeof c->slots);return c->n;}
static Gfx *collect(Gfx *cmd,uint32_t *jobs,unsigned *result_count){
    const uint32_t owned = load_bram_slots & load_queued_safe;
    const unsigned raw_first = load_bram_first;
    const uint32_t h0 = load_queued_hash[0], h1 = load_queued_hash[1];
    const uint32_t h2 = load_queued_hash[2], h3 = load_queued_hash[3];
    unsigned count = 0;
    for (;;) {
        unsigned slots[6];
        unsigned n = gfx_geometry_decode(cmd, slots);
        if (!n || count + n > 64) break;
        /* Validate the entire command before recording either TRI2 half. */
        for (unsigned t = 0; t < n; ++t) {
            const unsigned *v = &slots[t * 3];
            if ((v[0] | v[1] | v[2]) >= 32) goto collected;
            const uint32_t used = (1u << v[0]) | (1u << v[1]) | (1u << v[2]);
            if ((owned & used) != used) goto collected;
            const uint32_t p0 = h0 & used, p1 = h1 & used, p2 = h2 & used, p3 = h3 & used;
            if ((!p0 || p0 == used) && (!p1 || p1 == used)
                && (!p2 || p2 == used) && (!p3 || p3 == used)) goto collected;
        }
        for (unsigned t = 0; t < n; ++t) {
            const unsigned *v = &slots[t * 3];
            jobs[count++] = (v[0] - raw_first) | ((v[1] - raw_first) << 5) | ((v[2] - raw_first) << 10);
        }
        ++cmd;
    }
collected:
*result_count=count;return cmd;
}
static unsigned long checks;
static uint32_t rng=0x51474631;
static unsigned next(void){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
static void check(int b,const char*m){++checks;if(!b){fprintf(stderr,"FAIL %s\n",m);exit(1);}}
static unsigned fingerprint(unsigned index){unsigned h=0;for(unsigned p=0;p<4;p++)h|=((load_queued_hash[p]>>index)&1u)<<p;return h;}
static void verify(Gfx *commands,unsigned n){
 uint32_t actual[64],expected[64];unsigned ac=999,ec=0,consumed=0;
 for(;consumed<n;consumed++){
  Gfx *c=&commands[consumed];if(!c->n||ec+c->n>64)break;int valid=1;
  for(unsigned tri=0;tri<c->n;tri++){
   unsigned *v=&c->slots[tri*3],h[3];
   for(unsigned k=0;k<3;k++){
    if(v[k]>=32||!(load_bram_slots&(1u<<v[k]))||!(load_queued_safe&(1u<<v[k]))){valid=0;break;}
    h[k]=fingerprint(v[k]);
   }
   if(!valid)break;if(h[0]==h[1]&&h[1]==h[2]){valid=0;break;}
  }
  if(!valid)break;
  for(unsigned tri=0;tri<c->n;tri++){
   unsigned *v=&c->slots[tri*3];expected[ec++]=(v[0]-load_bram_first)|((v[1]-load_bram_first)<<5)|((v[2]-load_bram_first)<<10);
  }
 }
 Gfx *end=collect(commands,actual,&ac);check(end==commands+consumed,"exact atomic command boundary");check(ac==ec,"count/64-job boundary");check(!memcmp(actual,expected,ec*sizeof *actual),"raw local indices and exact order");
}
int main(void){
 Gfx c[70];unsigned batches=0;
 // Directed TRI2: supported first half followed by equal-hash/invalid slot
 // must leave BOTH halves for CPU fallback.
 load_bram_first=5;load_bram_slots=load_queued_safe=0xffffffe0;load_queued_hash[0]=0xaaaaaaaa;load_queued_hash[1]=load_queued_hash[2]=load_queued_hash[3]=0;
 memset(c,0,sizeof c);c[0]=(Gfx){1,{5,6,7}};c[1]=(Gfx){2,{5,6,7,5,7,9}};verify(c,3);batches++;
 c[1]=(Gfx){2,{5,6,7,5,6,32}};verify(c,3);batches++;
 // 65 triangles cannot partially admit a TRI2 crossing the64 limit.
 for(unsigned i=0;i<32;i++)c[i]=(Gfx){2,{5,6,7,6,7,8}};c[32]=(Gfx){1,{5,6,7}};c[33].n=0;verify(c,34);batches++;
 for(unsigned batch=0;batch<20000;batch++){
  unsigned count=1+next()%32;load_bram_first=next()%(33-count);
  load_bram_slots=(0xffffffffu>>(32-count))<<load_bram_first;
  load_queued_safe=load_bram_slots&next();for(unsigned p=0;p<4;p++)load_queued_hash[p]=next()&load_bram_slots;
  // Half the streams deliberately contain only valid indices and metadata,
  // so the actual loop reaches deep runs and not just its first rejection.
  if(batch&1)load_queued_safe=load_bram_slots;
  unsigned length=1+next()%64;
  for(unsigned i=0;i<length;i++){
   c[i].n=1+next()%2;
   for(unsigned k=0;k<c[i].n*3;k++)c[i].slots[k]=(batch&1)?load_bram_first+next()%count:next()%64;
  }
  c[length].n=0;verify(c,length+1);batches++;
 }
 printf("{\"pass\":true,\"streams\":%u,\"checks\":%lu}\n",batches,checks);return 0;
}
