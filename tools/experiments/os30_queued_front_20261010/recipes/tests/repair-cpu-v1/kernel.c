#include <stdint.h>
#include "vectors.h"
#define STATUS ((volatile uint32_t *)0x5007f000u)
#define OUTPUT ((volatile uint32_t *)0x50a00000u)
__attribute__((noinline)) static unsigned repair(const float v[9],unsigned cull,uint32_t output[9]){
    unsigned saved_fcsr;
    __asm__ volatile("csrr %0,fcsr" : "=r"(saved_fcsr));
    float center_x, center_y, dx0, dy0, dx2, dy2, product0, product1, cross;
    __asm__ volatile(
        "fmul.s %[cx],%[x1],%[iw1],rne\n\t"
        "fmul.s %[cy],%[y1],%[iw1],rne\n\t"
        "fmsub.s %[dx0],%[x0],%[iw0],%[cx],rne\n\t"
        "fmsub.s %[dy0],%[y0],%[iw0],%[cy],rne\n\t"
        "fmsub.s %[dx2],%[x2],%[iw2],%[cx],rne\n\t"
        "fmsub.s %[dy2],%[y2],%[iw2],%[cy],rne\n\t"
        "fmul.s %[p0],%[dx0],%[dy2],rne\n\t"
        "fmul.s %[p1],%[dy0],%[dx2],rne\n\t"
        "fsub.s %[cross],%[p0],%[p1],rne"
        : [cx] "=&f"(center_x), [cy] "=&f"(center_y),
          [dx0] "=&f"(dx0), [dy0] "=&f"(dy0), [dx2] "=&f"(dx2), [dy2] "=&f"(dy2),
          [p0] "=&f"(product0), [p1] "=&f"(product1), [cross] "=&f"(cross)
        : [x0] "f"(v[0]), [y0] "f"(v[1]), [iw0] "f"(v[2]),
          [x1] "f"(v[3]), [y1] "f"(v[4]), [iw1] "f"(v[5]),
          [x2] "f"(v[6]), [y2] "f"(v[7]), [iw2] "f"(v[8]));
    unsigned reject = cull == 3u;
    /* Preserve the original ordered comparison even for exceptional NaNs:
     * -ffast-math must not turn !(cross<=0) into cross>0. */
    if (cull == 1u)
        __asm__ volatile("fle.s %0,%1,%2" : "=r"(reject) : "f"(cross), "f"(0.0f));
    else if (cull == 2u)
        __asm__ volatile("fle.s %0,%1,%2" : "=r"(reject) : "f"(0.0f), "f"(cross));
    const unsigned keep = !reject;
    /* Finish the comparisons before restoring flags. */
    __asm__ volatile("" : : "r"(keep) : "memory");
    __asm__ volatile("csrw fcsr,%0" : : "r"(saved_fcsr) : "memory");

 float values[9]={center_x,center_y,dx0,dy0,dx2,dy2,product0,product1,cross};
 for(unsigned j=0;j<9;j++)output[j]=((uint32_t *)values)[j];
 return keep;
}
void kernel(void){for(unsigned i=0;i<VECTOR_COUNT;i++){
 uint32_t outputs[9],csr,initial=((i%5)<<5)|(i&31);
 __asm__ volatile("csrw fcsr,%0"::"r"(initial):"memory");
 unsigned keep=repair((const float *)vectors[i],vectors[i][13],outputs);
 __asm__ volatile("csrr %0,fcsr":"=r"(csr));
 for(unsigned j=0;j<9;j++)OUTPUT[i*11+j]=outputs[j];
 OUTPUT[i*11+9]=keep;OUTPUT[i*11+10]=csr;
 if((i&127)==0)STATUS[2]=i;
 }__asm__ volatile("fence rw,rw":::"memory");STATUS[2]=VECTOR_COUNT;STATUS[0]=0x600dcafe;for(;;)__asm__ volatile("nop");}
