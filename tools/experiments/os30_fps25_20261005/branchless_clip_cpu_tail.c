
static void put(char c) {while(!(*(volatile unsigned *)0x4f000000u&2)){} *(volatile unsigned *)0x4f000004u=(unsigned char)c;}
static void puts_uart(const char*s) {while(*s)put(*s++);}
static void hex(unsigned v) {for(unsigned n=0;n<8;n++)put("0123456789abcdef"[(v>>(28-4*n))&15]);put(' ');}
static void fail(void) {puts_uart("KERNEL FAIL\n");for(;;){}}
#define assert(x) do {if(!(x))fail();}while(0)
static unsigned cycle(void) {return *(volatile unsigned *)0x40000004u;}
static unsigned fold(unsigned h,unsigned x) {return (h^x)*16777619u;}
static struct LoadedVertex inputs[JOBS*3];
static uint32_t random_state=0x391d2a45;
static uint32_t next(void) {unsigned x=random_state;x^=x<<13;x^=x>>17;x^=x<<5;return random_state=x;}
static void check_one(struct LoadedVertex *v) {
 reference_mask(v);unsigned expected=v->clip_rej;candidate_mask(v);assert(v->clip_rej==expected);
}
static void check(void) {
 for(unsigned i=0;i<JOBS;i++)memcpy(inputs+i*3,input_words[i],108);
 for(unsigned i=0;i<JOBS*3;i++)check_one(inputs+i);
 const uint32_t edge[]={0,0x80000000u,0x3f800000u,0xbf800000u,0x3f7fffffu,0x3f800001u,0x7f800000u,0xff800000u,0x7fc00000u,0x7f800001u,1,0x80000001u};
 for(unsigned i=0;i<12;i++)for(unsigned j=0;j<12;j++)for(unsigned k=0;k<12;k++) {
  struct LoadedVertex v={0};uint32_t xyz[]={edge[i],edge[j],edge[k],edge[(i+j+k)%12]};
  memcpy(&v,xyz,16);check_one(&v);
 }
 for(unsigned i=0;i<1024;i++) {
  struct LoadedVertex v={0};uint32_t xyz[]={next(),next(),next(),next()};
  memcpy(&v,xyz,16);check_one(&v);
 }
}
#define DEFINE_BATCH(name) \
static __attribute__((noinline,noclone)) unsigned name##_batch(void) { \
 unsigned h=2166136261u;__asm__ volatile("":::"memory"); \
 for(unsigned rep=0;rep<8;rep++)for(unsigned i=0;i<JOBS*3;i++) { \
  name##_mask(inputs+i);h=fold(h,inputs[i].clip_rej); \
 } \
 return h; \
}
DEFINE_BATCH(reference)
DEFINE_BATCH(candidate)
static volatile unsigned eviction[65536],eviction_sink;
static void cold_prepare(void) {unsigned h=0;for(unsigned i=0;i<65536;i+=16)h+=eviction[i];eviction_sink=h;__asm__ volatile("fence.i":::"memory");}
int main(void) {
 check();unsigned expected=reference_batch();assert(candidate_batch()==expected);
 puts_uart("CHECK PASS captured plus finite/nonfinite/zero/subnormal boundary inputs\n");
 for(unsigned cold=0;cold<2;cold++)for(unsigned order=0;order<2;order++)for(unsigned j=0;j<2;j++) {
  unsigned mode=order?1-j:j;
  if(cold)cold_prepare();else assert((mode?candidate_batch():reference_batch())==expected);
  puts_uart("RUN ");hex(cold);hex(order);hex(mode);put('@');
  unsigned start=cycle(),got=mode?candidate_batch():reference_batch(),elapsed=cycle()-start;
  put('#');puts_uart(" TIMER ");hex(elapsed);hex(got);puts_uart("\n");assert(got==expected);
 }
 puts_uart("KERNEL PASS HAL init\n");return 0;
}
