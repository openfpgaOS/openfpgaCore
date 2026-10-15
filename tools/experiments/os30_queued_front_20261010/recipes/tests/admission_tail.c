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
