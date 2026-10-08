/* Private RTL performance/correctness fixtures. Component cycles, not FPS.
 * SM64 mtxf_mul_vec3s is frozen from engine/math_util.c at prepare time.
 * The triangle packet arithmetic consumes captured projected scene records;
 * it is the private software implementation, not current production work. */
#include <stdint.h>
#include "fixtures.h"
#include "sm64_vector.h"
#include "triangle_packet.h"
volatile unsigned traps, irq_count;
static volatile unsigned data[32768];
static void puts_uart(const char *s) {
    while (*s) {
        while (!(*(volatile unsigned *)0x4f000000u & 2)) {}
        *(volatile unsigned *)0x4f000004u = (unsigned char)*s++;
    }
}
static void hex(unsigned v) {
    char s[10];
    for (unsigned i=0;i<8;i++) s[i]="0123456789abcdef"[(v>>(28-4*i))&15];
    s[8]=' ';s[9]=0;puts_uart(s);
}
static void fail(unsigned got,unsigned expected) {
    puts_uart("KERNEL FAIL ");hex(got);hex(expected);puts_uart("\n");for(;;){}
}
/* os30 omits the optional RISC-V cycle CSR; use its existing MMIO counter. */
static unsigned cycle(void) {return *(volatile unsigned *)0x40000004u;}
static unsigned fold(unsigned hash,unsigned value) {return (hash^value)*16777619u;}
__attribute__((noinline)) static unsigned independent_alu(void) {
    unsigned a=1,b=2,c=3,d=4,n=4096;
    __asm__ volatile(".option push\n.option norvc\n.balign 8\n1:\n"
        "addi %0,%0,1\naddi %1,%1,3\naddi %2,%2,5\naddi %3,%3,7\n"
        "addi %0,%0,2\naddi %1,%1,4\naddi %2,%2,6\naddi %3,%3,8\n"
        "addi %4,%4,-1\nbnez %4,1b\n.option pop"
        :"+r"(a),"+r"(b),"+r"(c),"+r"(d),"+r"(n)::"memory");
    return fold(fold(fold(a,b),c),d);
}
__attribute__((noinline)) static unsigned shift_branch(void) {
    unsigned x=0x12345678,h=2166136261u;
    for(unsigned i=0;i<4096;i++) {
        x^=x<<13;x^=x>>17;x^=x<<5;
        if(x&1) h+=x;else h^=x;
        h=(h<<3)|(h>>29);
    }
    return h;
}
__attribute__((noinline)) static unsigned memory_raw(void) {
    unsigned h=2166136261u;
    for(unsigned i=0;i<4096;i++) {
        unsigned x=i*0x10204081u;
        data[i]=x;
        unsigned a=data[i];
        ((volatile unsigned char*)&data[i])[1]=(unsigned char)i;
        unsigned b=data[i];
        ((volatile unsigned short*)&data[i])[1]=(unsigned short)(i^0xabcd);
        unsigned c=data[i];
        h=fold(fold(fold(h,a),b),c);
    }
    return h;
}
__attribute__((noinline)) static unsigned memory_scan(unsigned n) {
    unsigned h=0;
    for(unsigned pass=0;pass<4;pass++) {
        unsigned index=0;
        for(unsigned i=0;i<n;i++) {h+=data[index];index+=17;if(index>=n)index-=n;}
    }
    return h;
}
static Mat4 matrix={{1.0f,0.25f,-0.5f,0},{-0.25f,1,0.125f,0},
                    {0.5f,-0.125f,1,0},{4,-8,16,1}};
__attribute__((noinline)) static unsigned sm64_vectors(void) {
    unsigned h=2166136261u;
    for(unsigned round=0;round<4;round++) for(unsigned i=0;i<1024;i++) {
        Vec3s v={(int16_t)((i*13)%1024-512),(int16_t)((i*17)%1024-512),
                 (int16_t)((i*29)%1024-512)};
        mtxf_mul_vec3s(matrix,v);
        for(unsigned j=0;j<3;j++) h=fold(h,(unsigned)(int)v[j]);
    }
    return h;
}
__attribute__((noinline)) static unsigned triangle_scene(void) {
    unsigned h=2166136261u,index=0;uint32_t out[40];
    for(unsigned j=0;j<TRIANGLE_JOBS;j++) {
        unsigned n=packet(triangle_input+j*24,out);
        if(n!=triangle_expected[index++]) fail(n,triangle_expected[index-1]);
        h=fold(h,n);
        for(unsigned i=0;i<n;i++) {
            if(out[i]!=triangle_expected[index++]) fail(out[i],triangle_expected[index-1]);
            h=fold(h,out[i]);
        }
    }
    return h;
}
static void report(const char *name,unsigned start,unsigned result,unsigned expected) {
    unsigned elapsed=cycle()-start;
    if(result!=expected)fail(result,expected);
    puts_uart("KERNEL ");puts_uart(name);puts_uart(" ");hex(elapsed);hex(result);puts_uart("\n");
}
int main(void) {
    for(unsigned pass=0;pass<2;pass++) {
        unsigned t=cycle();unsigned result=independent_alu();report("independent_alu",t,result,EXPECT_ALU);
        t=cycle();result=shift_branch();report("shift_branch",t,result,EXPECT_SHIFT);
        t=cycle();result=memory_raw();report("memory_raw",t,result,EXPECT_RAW);
        t=cycle();result=sm64_vectors();report("sm64_vectors",t,result,EXPECT_VECTORS);
        t=cycle();result=triangle_scene();report("triangle_scene",t,result,EXPECT_TRIANGLES);
        for(unsigned i=0;i<32768;i++)data[i]=i*0x10204081u;
        t=cycle();result=memory_scan(4096);report("cache16",t,result,EXPECT_CACHE16);
        t=cycle();result=memory_scan(24576);report("cache96",t,result,EXPECT_CACHE96);
    }
    puts_uart("KERNEL PASS HAL init\n");return 0;
}
