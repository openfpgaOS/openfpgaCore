#include <stdint.h>
#include <stddef.h>
#include <string.h>
volatile unsigned traps,irq_count;
extern const unsigned char corpus_start[],corpus_end[];
extern void invoke_vertex(unsigned n,unsigned dest,unsigned address,unsigned original_sp);
static void put(char c){while(!(*(volatile unsigned*)0x4f000000u&2)){}*(volatile unsigned*)0x4f000004u=(unsigned char)c;}
static void text(const char*s){while(*s)put(*s++);}
static void hex(unsigned v){for(unsigned i=0;i<8;i++)put("0123456789abcdef"[(v>>(28-4*i))&15]);put(' ');}
static unsigned cycle(void){return *(volatile unsigned*)0x40000004u;}
static void fail(unsigned job,unsigned offset,unsigned got,unsigned wanted){text("KERNEL FAIL ");hex(job);hex(offset);hex(got);hex(wanted);text("\n");for(;;){}}
static const uint32_t ranges[][2]={{0x1115cca0u,6564},{0x10fdb740u,20},{0x10fdbb0cu,8},{0x11327ca8u,12}};
#define STATE_BYTES 6604u
#define HEAD_BYTES 40u
static const unsigned char *restore(const unsigned char *record){
 const uint32_t*h=(const uint32_t*)record;const unsigned char*p=record+HEAD_BYTES;
 for(unsigned i=0;i<4;i++){memcpy((void*)ranges[i][0],p,ranges[i][1]);p+=ranges[i][1];}
 memcpy((void*)h[5],p,h[3]*24);return p+h[3]*24;
}
static unsigned check(unsigned job,const unsigned char*expected){
 unsigned hash=2166136261u,offset=0;
 for(unsigned i=0;i<4;i++){
  const unsigned char*actual=(const unsigned char*)ranges[i][0];
  for(unsigned j=0;j<ranges[i][1];j++,offset++){
   if(actual[j]!=expected[offset])fail(job,offset,actual[j],expected[offset]);
   hash=(hash^actual[j])*16777619u;
  }
 }
 return hash;
}
static unsigned char reference[STATE_BYTES] __attribute__((aligned(4)));
static unsigned reference_hash[256],qsim_difference_words;
static void snapshot_reference(void) {
 unsigned offset=0;
 for(unsigned i=0;i<4;i++){memcpy(reference+offset,(const void*)ranges[i][0],ranges[i][1]);offset+=ranges[i][1];}
}
static void log_qsim_differences(unsigned job,const unsigned char *expected) {
 for(unsigned offset=0;offset<STATE_BYTES;offset+=4){
  uint32_t actual,wanted;memcpy(&actual,reference+offset,4);memcpy(&wanted,expected+offset,4);
  if(actual!=wanted){qsim_difference_words++;text("QSIM_DIFF ");hex(job);hex(offset);hex(actual);hex(wanted);text("\n");}
 }
}
static volatile unsigned eviction[65536],eviction_sink;
static void cold_prepare(void){unsigned sum=0;for(unsigned i=0;i<65536;i+=16)sum+=eviction[i];eviction_sink=sum;__asm__ volatile("fence.i":::"memory");}
int main(void){
 unsigned jobs=0,vertices=0;const unsigned char*p=corpus_start;
 while(p<corpus_end){const uint32_t*h=(const uint32_t*)p;if(h[0]!=0x33585456u||h[8]!=0)fail(jobs,0,h[0],0x33585456u);vertices+=h[3];jobs++;p+=HEAD_BYTES+2*STATE_BYTES+h[3]*24+56;}
 if(p!=corpus_end)fail(jobs,1,0,0);
 text("CORPUS ");hex(jobs);hex(vertices);text("\n");
 for(unsigned cold=0;cold<2;cold++){
  p=corpus_start;
  for(unsigned job=0;job<jobs;job++){
   const uint32_t*h=(const uint32_t*)p;const unsigned char*expected=restore(p);
   invoke_vertex(h[3],h[4],h[5],h[7]);snapshot_reference();
   unsigned reference_digest=check(job,reference);
   if(!cold){reference_hash[job]=reference_digest;log_qsim_differences(job,expected);}
   else if(reference_hash[job]!=reference_digest)fail(job,2,reference_digest,reference_hash[job]);
   restore(p);if(cold)cold_prepare();
   text("RUN ");hex(cold);hex(job);hex(h[3]);put('@');
   unsigned begin=cycle();invoke_vertex(h[3],h[4],h[5],h[7]);unsigned elapsed=cycle()-begin;
   put('#');text(" TIMER ");hex(elapsed);hex(check(job,reference));text("\n");
   p=expected+STATE_BYTES+56;
  }
 }
 text("QSIM_DIFFERENCE_WORDS ");hex(qsim_difference_words);text("\nACTUAL_A_REPEATABILITY_PASS\nKERNEL PASS HAL init\n");return 0;
}
