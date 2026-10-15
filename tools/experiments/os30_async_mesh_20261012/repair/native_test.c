#include "async_mesh_repair.h"
#include <stdint.h>
#include <stddef.h>
#include "vectors.h"
#define STATUS ((volatile uint32_t *)0x5007f000u)
#define OUTPUT ((volatile uint32_t *)0x50a00000u)
void *memcpy(void *d,const void *s,size_t n) {unsigned char *a=d;const unsigned char *b=s;while(n--)*a++=*b++;return d;}
void *memset(void *d,int c,size_t n) {unsigned char *a=d;while(n--)*a++=(unsigned char)c;return d;}
unsigned repair_reference(const struct am_repair_job *,uint32_t *);
void repair_reference_transform(const struct am_repair_job *,uint32_t [32][8]);
static uint32_t actual[18000],expected[18000],transformed[32][8],reference_raw[32][8];
static unsigned actual_count;
static int emit(void *unused,uint32_t w) {(void)unused;if(actual_count>=18000)return 0;actual[actual_count++]=w;return 1;}
void kernel(void) {
 for(unsigned i=0;i<VECTOR_COUNT;i++) {
  const struct test_vector *v=&vectors[i];struct am_repair_job j=v->job;
  j.raw=v->raw;j.commands=v->commands;j.vertices=v->vertices;
  uint32_t raw_bad=~0u,raw_got=0,raw_want=0,after;
  if(v->check_transform) {
   const uint32_t (*raw_expected)[8]=v->raw;
   if(v->check_transform==2) {__asm__ volatile("csrwi fcsr,0":::"memory");repair_reference_transform(&j,reference_raw);raw_expected=reference_raw;}
   __asm__ volatile("li t0,0x65\ncsrw fcsr,t0":::"t0","memory");
   am_repair_transform(&j,transformed);
   __asm__ volatile("csrr %0,fcsr":"=r"(after));
   for(unsigned k=0;k<j.count*8;k++)if(((uint32_t *)transformed)[k]!=((const uint32_t *)raw_expected)[k]) {raw_bad=k;raw_got=((uint32_t *)transformed)[k];raw_want=((const uint32_t *)raw_expected)[k];break;}
   if(after!=0x65){raw_bad=~1u;raw_got=after;raw_want=0x65;}
  }
  actual_count=0;struct am_repair_result result;
  __asm__ volatile("li t0,0x65\ncsrw fcsr,t0":::"t0","memory");
  unsigned rc=am_repair(&j,emit,0,&result);
  __asm__ volatile("csrr %0,fcsr":"=r"(after));
  __asm__ volatile("csrwi fcsr,0":::"memory");
  if(v->check_transform==2)j.raw=reference_raw;
  unsigned n=repair_reference(&j,expected),bad=~0u,got=0,want=0;
  unsigned both=n<actual_count?n:actual_count;
  for(unsigned k=0;k<both;k++)if(actual[k]!=expected[k]){bad=k;got=actual[k];want=expected[k];break;}
  OUTPUT[11*i]=rc;OUTPUT[11*i+1]=actual_count;OUTPUT[11*i+2]=n;
  OUTPUT[11*i+3]=bad;OUTPUT[11*i+4]=got;OUTPUT[11*i+5]=want;
  OUTPUT[11*i+6]=raw_bad;OUTPUT[11*i+7]=raw_got;OUTPUT[11*i+8]=raw_want;
  OUTPUT[11*i+9]=0x65;OUTPUT[11*i+10]=after;
  if((i&15)==0)STATUS[2]=i;
 }
 __asm__ volatile("fence rw,rw":::"memory");STATUS[2]=VECTOR_COUNT;STATUS[0]=0x600dcafe;
 for(;;)__asm__ volatile("nop");
}
