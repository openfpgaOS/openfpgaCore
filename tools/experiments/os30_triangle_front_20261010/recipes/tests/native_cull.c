#include <stdint.h>
#include "vectors.h"
#define STATUS ((volatile uint32_t *)0x5007f000u)
#define OUTPUT ((volatile uint32_t *)0x50a00000u)

/* Instructions deliberately reproduce the compiled fallback operation DAG.
 * The target CPU, including its reduced-FMA product, supplies the oracle. */
__attribute__((noinline)) static uint32_t cull(float out[9],const uint32_t b[14]) {
 const float *v=(const float *)b;
 if(b[10]&b[11]&b[12])return 1;
 if(b[13]==3)return 2;
 if(b[13]==0)return 0;
 float cx,cy,dx1,dy1,dx2,dy2,p0,p1,cross;
 __asm__ volatile("fmul.s %0,%2,%3\nfmul.s %1,%4,%3"
  :"=&f"(cx),"=&f"(cy):"f"(v[3]),"f"(v[5]),"f"(v[4]));
 __asm__ volatile("fmsub.s %0,%4,%6,%8\nfmsub.s %1,%5,%6,%9\nfmsub.s %2,%7,%10,%8\nfmsub.s %3,%11,%10,%9"
  :"=&f"(dx1),"=&f"(dy1),"=&f"(dx2),"=&f"(dy2)
  :"f"(v[0]),"f"(v[1]),"f"(v[2]),"f"(v[6]),"f"(cx),"f"(cy),"f"(v[8]),"f"(v[7]));
 __asm__ volatile("fmul.s %0,%2,%3\nfmul.s %1,%4,%5"
  :"=&f"(p0),"=&f"(p1):"f"(dx1),"f"(dy2),"f"(dy1),"f"(dx2));
 unsigned parity=b[9]^(b[9]>>1)^(b[9]>>2);
 if(parity&1)__asm__ volatile("fsub.s %0,%1,%2":"=f"(cross):"f"(p1),"f"(p0));
 else __asm__ volatile("fsub.s %0,%1,%2":"=f"(cross):"f"(p0),"f"(p1));
 out[0]=cx;out[1]=cy;out[2]=dx1;out[3]=dy1;out[4]=dx2;out[5]=dy2;out[6]=p0;out[7]=p1;out[8]=cross;
 return b[13]==1?(cross<=0.0f?2:0):(cross>=0.0f?2:0);
}
void kernel(void) {
 for(unsigned i=0;i<VECTOR_COUNT;i++) {
  union {float f[9];uint32_t u[9];}out={{0}};
  __asm__ volatile("csrwi fcsr,0":::"memory");
  unsigned reject=cull(out.f,vectors[i]),flags;
  __asm__ volatile("csrr %0,fcsr":"=r"(flags));
  for(unsigned j=0;j<9;j++)OUTPUT[i*11+j]=out.u[j];
  OUTPUT[i*11+9]=reject;OUTPUT[i*11+10]=flags;
  if((i&127)==0)STATUS[2]=i;
 }
 __asm__ volatile("fence rw,rw":::"memory");
 STATUS[2]=VECTOR_COUNT;STATUS[0]=0x600dcafe;
 for(;;)__asm__ volatile("nop");
}
