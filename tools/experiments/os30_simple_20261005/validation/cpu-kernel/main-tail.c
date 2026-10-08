static void put(char c) {while(!(*(volatile unsigned *)0x4f000000u&2)){} *(volatile unsigned *)0x4f000004u=(unsigned char)c;}
static void puts_uart(const char*s) {while(*s)put(*s++);}
static void hex(unsigned v) {for(unsigned n=0;n<8;n++)put("0123456789abcdef"[(v>>(28-4*n))&15]);put(' ');}
static void fail(void) {puts_uart("KERNEL FAIL\n");for(;;){}}
static unsigned cycle(void) {return *(volatile unsigned *)0x40000004u;}
static unsigned fold(unsigned h,unsigned x) {return (h^x)*16777619u;}
static void load(unsigned n,struct LoadedVertex v[3]) {memcpy(v,input_words[n],108);}
static unsigned oracle_mask(struct LoadedVertex*d) {return (d->z>d->w?1:0)|(d->z< -d->w?2:0)|(d->y>d->w?4:0)|(d->y< -d->w?8:0)|(d->x>d->w?16:0)|(d->x< -d->w?32:0);}
static unsigned run_one(unsigned n,unsigned mode) {
 struct LoadedVertex v[3];load(n,v);which=0;counts[0]=0;
 for(unsigned i=0;i<3;i++)if(mode&2)candidate_mask(v+i);else reference_mask(v+i);
 bool clipped=(mode&1)?candidate_clip(v,v+1,v+2,v[0].clip_rej&v[1].clip_rej&v[2].clip_rej):reference_clip(v,v+1,v+2,v[0].clip_rej&v[1].clip_rej&v[2].clip_rej);
 if(!clipped)gfx_push_triangle(v,v+1,v+2,false);
 unsigned h=fold(2166136261u,counts[0]);
 for(unsigned t=0;t<counts[0];t++)for(unsigned i=0;i<3;i++)for(unsigned k=0;k<8;k++) {uint32_t word;memcpy(&word,(char*)&submitted[0][t][i]+k*4,4);h=fold(h,word);}
 return h;
}
__attribute__((noinline)) static unsigned batch(unsigned mode) {unsigned h=2166136261u;for(unsigned n=0;n<JOBS;n++)h=fold(h,run_one(n,mode));return h;}
static volatile unsigned eviction[65536],eviction_sink;
static void cold_prepare(void) {unsigned h=0;for(unsigned i=0;i<65536;i+=16)h+=eviction[i];eviction_sink=h;__asm__ volatile("fence.i":::"memory");}
int main(void) {
 assert(sizeof(struct LoadedVertex)==36 && offsetof(struct LoadedVertex,clip_rej)==32);
 unsigned masks=0,active=0;
 /* Exact field-by-field qualification precedes measurements. */
 for(unsigned n=0;n<JOBS;n++) {
  struct LoadedVertex v[3];load(n,v);
  for(unsigned j=0;j<3;j++) {unsigned expected=oracle_mask(v+j);reference_mask(v+j);assert(v[j].clip_rej==expected);candidate_mask(v+j);assert(v[j].clip_rej==expected);masks|=expected;}
  which=0;counts[0]=counts[1]=0;bool a=reference_clip(v,v+1,v+2,v[0].clip_rej&v[1].clip_rej&v[2].clip_rej);
  which=1;bool b=candidate_clip(v,v+1,v+2,v[0].clip_rej&v[1].clip_rej&v[2].clip_rej);
  assert(a==b && counts[0]==counts[1]);active+=counts[0];
  for(unsigned t=0;t<counts[0];t++)for(unsigned j=0;j<3;j++)assert(!memcmp(&submitted[0][t][j],&submitted[1][t][j],32));
 }
 puts_uart("INPUTS ");hex(JOBS);hex(masks);hex(active);puts_uart("\n");
 unsigned expected=batch(0);
 for(unsigned cold=0;cold<2;cold++)for(unsigned order=0;order<2;order++)for(unsigned j=0;j<4;j++) {
  unsigned mode=order?3-j:j;
  if(cold)cold_prepare();else assert(batch(mode)==expected);
  puts_uart("RUN ");hex(cold);hex(order);hex(mode);put('@');
  unsigned start=cycle(),got=batch(mode),elapsed=cycle()-start;
  put('#');puts_uart(" TIMER ");hex(elapsed);hex(got);puts_uart("\n");assert(got==expected);
 }
 puts_uart("KERNEL PASS HAL init\n");return 0;
}
