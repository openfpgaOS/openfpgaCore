#include "vectors.h"
volatile unsigned traps;
static void put(const char *s) {
 while (*s) {while (!(*(volatile unsigned*)0x4f000000u&2)) {}
 *(volatile unsigned*)0x4f000004u=(unsigned char)*s++;}
}
static void hex(unsigned x) {
 char b[10];for(unsigned i=0;i<8;i++)b[i]="0123456789abcdef"[(x>>(28-4*i))&15];
 b[8]=' ';b[9]=0;put(b);
}
static unsigned now(void) {return *(volatile unsigned*)0x40000004u;}
static void fail(unsigned row,unsigned op,unsigned actual,unsigned expected) {
 put("DIVISA FAIL ");hex(row);hex(op);hex(actual);hex(expected);put("\n");for(;;){}
}
static void report(unsigned round,unsigned kind,unsigned cycles,unsigned result) {
 put("DIVLAT ");hex(round);hex(kind);hex(1024);hex(cycles);hex(result);put("\n");
}
#define INTTEST(KIND,OP) do {unsigned r,start=now(); \
 __asm__ volatile("li t0,64\nli t1,65537\nli t2,0x12345678\nli t3,0x81000001\n1:\n.rept 16\n" \
 OP " t2,t2,t1\nadd t2,t2,t3\n.endr\naddi t0,t0,-1\nbnez t0,1b\nmv %0,t2\n" \
 : "=r"(r)::"t0","t1","t2","t3","memory");report(round,KIND,now()-start,r);}while(0)
#define FPTEST(KIND,OP) do {unsigned r,start=now(); \
 __asm__ volatile("li t0,0x3f800000\nfmv.w.x fa0,t0\nfmv.w.x fa1,t0\nli t0,64\n1:\n.rept 16\n" \
 OP "\n.endr\naddi t0,t0,-1\nbnez t0,1b\nfmv.x.w %0,fa0\n" \
 : "=r"(r)::"t0","fa0","fa1","memory");if(r!=0x3f800000)fail(round,KIND,r,0x3f800000);report(round,KIND,now()-start,r);}while(0)
int main(void) {
 for(unsigned i=0;i<sizeof(cases)/sizeof(cases[0]);i++){
  const unsigned *v=cases[i];unsigned got[4];
  __asm__ volatile("div %0,%4,%5\nrem %1,%4,%5\ndivu %2,%4,%5\nremu %3,%4,%5"
   :"=&r"(got[0]),"=&r"(got[1]),"=&r"(got[2]),"=&r"(got[3]):"r"(v[0]),"r"(v[1]));
  for(unsigned j=0;j<4;j++)if(got[j]!=v[j+2])fail(i,j,got[j],v[j+2]);
 }
 put("DIVISA PASS ");hex(sizeof(cases)/sizeof(cases[0])*4);put("\n");
 for(unsigned round=0;round<2;round++){
  INTTEST(0,"divu");INTTEST(1,"remu");INTTEST(2,"div");INTTEST(3,"rem");
  FPTEST(4,"fdiv.s fa0,fa0,fa1");FPTEST(5,"fmul.s fa0,fa0,fa1");FPTEST(6,"fsqrt.s fa0,fa0");
 }
 put("DIVLAT PASS HAL init\n");return 0;
}
