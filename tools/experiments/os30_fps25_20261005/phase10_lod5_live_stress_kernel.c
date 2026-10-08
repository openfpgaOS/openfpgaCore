#include <stdint.h>
#include "vectors.h"
volatile unsigned traps,irq_count;
extern void invoke_copy(void*,unsigned);
extern int invoke_load(unsigned,unsigned,const void*,float,int);
#define WORD(a) (*(volatile uint32_t*)(uintptr_t)(a))
#define RSP ((volatile unsigned char*)0x1115d908u)
#define DST ((volatile uint32_t*)(0x1115d908u+940))
#define STATUS WORD(0x4b000004u)
#define DIRTY WORD(0x10fda72cu)
#define PRESENT WORD(0x10fdab00u)
#define CONTROL_VALID WORD(0x10fdaafcu)
#define CV_STATS ((volatile uint32_t*)0x11347c18u)
static unsigned checks,calls,success,vertices;
static void put(char c){while(!(WORD(0x4f000000u)&2)){}WORD(0x4f000004u)=(unsigned char)c;}
static void text(const char*s){while(*s)put(*s++);}
static void hex(unsigned v){for(unsigned i=0;i<8;i++)put("0123456789abcdef"[(v>>(28-4*i))&15]);put(' ');}
static void eq(unsigned job,unsigned index,uint32_t got,uint32_t want){checks++;if(got!=want){text("KERNEL FAIL ");hex(job);hex(index);hex(got);hex(want);put('\n');for(;;){}}}
static float fp(uint32_t u){union{uint32_t u;float f;}v;v.u=u;return v.f;}
static void context(const Lod5NativeVector*j){
 for(unsigned i=0;i<16;i++)*(volatile uint32_t*)(RSP+708+i*4)=j->h[3+i];
 for(unsigned i=0;i<3;i++){
  *(volatile uint32_t*)(RSP+876+i*4)=j->h[19+i];
  RSP[849+i]=(j->h[22]>>(8*i))&255;RSP[837+i]=(j->h[23]>>(8*i))&255;
 }
 RSP[924]=2;*(volatile uint32_t*)(RSP+928)=j->h[24]?0x20000u:0;
 *(volatile uint32_t*)(RSP+936)=j->h[25]|j->h[26]<<16;
 DIRTY=3;CONTROL_VALID=0;
}
static void clear_dest(void){for(unsigned i=0;i<36*9;i++)DST[i]=0xa5a5a5a5u;}
static void check_dest(unsigned index,const Lod5NativeVector*j,unsigned first){
 for(unsigned i=0;i<36;i++)for(unsigned w=0;w<9;w++){
  unsigned expected=0xa5a5a5a5u,mask=~0u;
  if(i>=first&&i<first+j->h[0]){unsigned k=(i-first)*9+w;expected=j->expected[k];mask=j->mask[k];}
  eq(index,i*9+w,DST[i*9+w]&mask,expected&mask);
 }
}
static void rejected(unsigned id,const Lod5NativeVector*j,unsigned n,unsigned first,const void*p,int fog){
 clear_dest();int r=invoke_load(n,first,p,fp(j->h[2]),fog);calls++;eq(id,999,r,0);
 for(unsigned i=0;i<36*9;i++)eq(id,i,DST[i],0xa5a5a5a5u);
}
static void fence_commands(void){
 WORD(0x4a000000u)=6;WORD(0x4a000000u)=16;
 for(unsigned pass=0;pass<5;pass++){
  unsigned count=pass==4?2047:16+pass*17;
  unsigned expected=(WORD(0x4a000004u)+count*8)&16383u;
  for(unsigned n=0;n<count;n++){WORD(0x4a000004u)=0x02000001u;WORD(0x4a000004u)=0x81200000u+pass*4096+n;}
  WORD(0x4a000000u)=8;
  unsigned limit=500000;
  while((WORD(0x4a000010u)!=expected||(WORD(0x4a000014u)&1))&&--limit){}
  eq(200+pass,0,!!limit,1);eq(200+pass,1,WORD(0x4a000018u),0x81200000u+pass*4096+count-1);
 }
 text("FENCE_COMMANDS_EXACT\n");
}
int main(void){
 __asm__ volatile("csrw fcsr,zero":::"memory");
 if(WORD(0x53180000u)==0x4c4f4435u){
  for(unsigned i=0;i<200000&&(STATUS&1);i++){}
  eq(300,0,STATUS&0xefu,6);
  checks=WORD(0x53180004u);calls=WORD(0x53180008u);success=WORD(0x5318000cu);vertices=WORD(0x53180010u);
  const Lod5NativeVector*j=&lod5_native_vectors[0];clear_dest();
  invoke_copy((void*)(DST+5*9),j->h[0]);check_dest(300,j,5);
  text("CPU_RESET_PRESERVED_LOAD\n");WORD(0x4b0003f0u)=0;fence_commands();
  text("COUNTS ");hex(checks);hex(calls);hex(success);hex(vertices);put('\n');
  text("LIVE_LOD5_COMPLETE\nKERNEL PASS HAL init\n");return 0;
 }
 text("LIVE_LOD5_BEGIN\n");WORD(0x4b0003f0u)=1;
 eq(0,1000,WORD(0x4b000000u),0x4c4f4405u);
 WORD(0x10fdac74u)=0x4a000000u;WORD(0x10fdab68u)=1;WORD(0x10fda748u)=0xfc0000u;PRESENT=0;
 for(unsigned i=0;i<2048;i++)WORD(0x11163b48u+i*4)=0;
 for(unsigned i=0;i<8;i++)CV_STATS[i]=0;
 for(unsigned i=0;i<LOD5_NATIVE_VECTOR_COUNT;i++){
  const Lod5NativeVector*j=&lod5_native_vectors[i];context(j);
  for(unsigned repeat=0;repeat<2;repeat++){
   unsigned first=repeat?32-j->h[0]:(i%(33-j->h[0]));clear_dest();
   unsigned uploads=CV_STATS[2],hits=CV_STATS[1];
   int r=invoke_load(j->h[0],first,j->raw,fp(j->h[2]),0);calls++;
   eq(i,1001,r,1);eq(i,1002,STATUS&0xefu,6u);
   check_dest(i,j,first);success++;vertices+=j->h[0];
   eq(i,1003,CV_STATS[2]-uploads,repeat?0:1);eq(i,1004,CV_STATS[1]-hits,repeat?1:0);
  }
  text("JOB ");hex(i);hex(j->h[0]);put('\n');
 }
 const Lod5NativeVector*j=&lod5_native_vectors[0];context(j);
 rejected(100,j,0,0,j->raw,0);rejected(101,j,33,0,j->raw,0);rejected(102,j,2,31,j->raw,0);
 rejected(103,j,1,0,(const char*)j->raw+1,0);rejected(104,j,1,0,j->raw,1);
 __asm__ volatile("csrwi frm,1":::"memory");rejected(105,j,1,0,j->raw,0);__asm__ volatile("csrwi frm,0":::"memory");
 // Unsupported arithmetic must complete before fallback is permitted and leave destination untouched.
 *(volatile uint32_t*)(RSP+708)=0x7fc00001u;DIRTY=3;
 rejected(106,j,j->h[0],0,j->raw,0);eq(106,1000,STATUS&9,8);
 context(j);clear_dest();eq(107,1000,invoke_load(j->h[0],3,j->raw,fp(j->h[2]),0),1);check_dest(107,j,3);
 text("COUNTS ");hex(checks);hex(calls);hex(success);hex(vertices);hex(CV_STATS[2]);hex(CV_STATS[1]);put('\n');
 WORD(0x53180000u)=0x4c4f4435u;WORD(0x53180004u)=checks;WORD(0x53180008u)=calls;WORD(0x5318000cu)=success;WORD(0x53180010u)=vertices;
 __asm__ volatile("fence rw,rw":::"memory");
 WORD(0x4b0003f0u)=2;WORD(0x4b00000cu)=0xfc0000u;WORD(0x4b000010u)=lod5_native_vectors[0].h[0];WORD(0x4b000008u)=1;
 for(;;)__asm__ volatile("":::"memory");
}
