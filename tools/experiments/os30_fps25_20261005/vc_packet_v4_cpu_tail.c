
static void put(char c) {while(!(*(volatile unsigned *)0x4f000000u&2)){} *(volatile unsigned *)0x4f000004u=(unsigned char)c;}
static void puts_uart(const char*s) {while(*s)put(*s++);}
static void hex(unsigned v) {for(unsigned n=0;n<8;n++)put("0123456789abcdef"[(v>>(28-4*n))&15]);put(' ');}
static void fail(void) {puts_uart("KERNEL FAIL\n");for(;;){}}
#define assert(x) do {if(!(x))fail();}while(0)
static unsigned cycle(void) {return *(volatile unsigned *)0x40000004u;}
static unsigned fold(unsigned h,unsigned x) {return (h^x)*16777619u;}
#define JOBS 512u
struct job {struct gfx_vc_vtx v[3];uint8_t slot[3];unsigned mask;int rgb;};
static struct job jobs[JOBS];
static uint32_t packet[32];
static uint32_t random_state=0x56924acbu;
static uint32_t next(void) {unsigned x=random_state;x^=x<<13;x^=x>>17;x^=x<<5;return random_state=x;}
static void inputs(void) {
 for(unsigned i=0;i<JOBS;i++) {
  jobs[i].mask=i&7;jobs[i].rgb=(i&8)?0:-1;
  for(unsigned k=0;k<3;k++) {
   struct gfx_vc_vtx *v=&jobs[i].v[k];jobs[i].slot[k]=next()&31;
   v->cx=(int16_t)next()*0.03125f+0.001953125f;
   v->cy=(int16_t)next()*0.03125f-0.001953125f;
   v->cw=(next()%32000u)+0.25f;
   v->u=(int16_t)next()*0.125f+0.00000762939453125f;
   v->v=(int16_t)next()*0.125f-0.00000762939453125f;
   v->w_inv=1.0f/v->cw;
   if((i&48)==16)v->w_inv=4.0f;
   if((i&48)==32)v->w_inv=0.0f;
   if((i&48)==48)v->w_inv=1.5f;
   v->r=next();v->g=next();v->b=next();v->a=next();
   v->use_exact_depth=(i>>k)&1;v->exact_depth=next();
  }
 }
}
static void check(void) {
 for(unsigned mode=0;mode<5;mode++) {
  __asm__ volatile("csrw frm,%0"::"r"(mode):"memory");
  for(unsigned i=0;i<JOBS;i++) {
   struct job *j=&jobs[i];uint32_t a[32],b[32];
   for(unsigned k=0;k<32;k++)a[k]=b[k]=0x98765432u+k;
   g_vc_rgb_input=j->rgb;g_vc_words=g_vc_loads=0;
   __asm__ volatile("csrw fflags,zero":::"memory");
   unsigned an=reference(j->v,j->slot,j->mask,a),af;
   __asm__ volatile("csrr %0,fflags":"=r"(af)::"memory");
   unsigned aw=g_vc_words,al=g_vc_loads;
   g_vc_words=g_vc_loads=0;
   __asm__ volatile("csrw fflags,zero":::"memory");
   unsigned bn=candidate(j->v,j->slot,j->mask,b),bf;
   __asm__ volatile("csrr %0,fflags":"=r"(bf)::"memory");
   assert(an==bn && an<=29 && af==bf && aw==g_vc_words && al==g_vc_loads);
   for(unsigned k=0;k<32;k++)assert(a[k]==b[k]);
  }
 }
 __asm__ volatile("csrw fcsr,zero":::"memory");
}
#define DEFINE_BATCH(name) \
static __attribute__((noinline,noclone)) unsigned name##_batch(void) { \
 unsigned h=2166136261u;g_vc_words=g_vc_loads=0; \
 __asm__ volatile("":::"memory"); \
 for(unsigned rep=0;rep<2;rep++)for(unsigned i=0;i<JOBS;i++) { \
  const struct job *j=&jobs[i];g_vc_rgb_input=j->rgb; \
  unsigned n=name(j->v,j->slot,j->mask,packet); \
  for(unsigned k=0;k<n;k++)h=fold(h,packet[k]); \
 } \
 return fold(fold(h,g_vc_words),g_vc_loads); \
}
DEFINE_BATCH(reference)
DEFINE_BATCH(candidate)
static volatile unsigned eviction[65536],eviction_sink;
static void cold_prepare(void) {unsigned h=0;for(unsigned i=0;i<65536;i+=16)h+=eviction[i];eviction_sink=h;__asm__ volatile("fence.i":::"memory");}
int main(void) {
 inputs();check();unsigned expected=reference_batch();assert(candidate_batch()==expected);
 puts_uart("CHECK PASS 512 triangles x5 rounding modes; words/counts/flags/counters\n");
 for(unsigned cold=0;cold<2;cold++)for(unsigned order=0;order<2;order++)for(unsigned j=0;j<2;j++) {
  unsigned mode=order?1-j:j;
  if(cold)cold_prepare();else assert((mode?candidate_batch():reference_batch())==expected);
  puts_uart("RUN ");hex(cold);hex(order);hex(mode);put('@');
  unsigned start=cycle(),got=mode?candidate_batch():reference_batch(),elapsed=cycle()-start;
  put('#');puts_uart(" TIMER ");hex(elapsed);hex(got);puts_uart("\n");assert(got==expected);
 }
 puts_uart("KERNEL PASS HAL init\n");return 0;
}
