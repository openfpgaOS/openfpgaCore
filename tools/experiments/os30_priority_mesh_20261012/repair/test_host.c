#include "async_mesh_repair.h"
#include <assert.h>
#include <fenv.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
static unsigned checks;
#define CHECK(x) do {checks++;assert(x);}while(0)
static uint32_t words[18000];static unsigned emitted,limit;
static int emit(void *p,uint32_t word) {(void)p;if(emitted==limit)return 0;CHECK(emitted<18000);words[emitted++]=word;return 1;}
static uint32_t bits(float f) {uint32_t u;memcpy(&u,&f,4);return u;}
static uint32_t rng=0x414a3031;
static uint32_t random32(void) {rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
static unsigned decode(void) {
 unsigned at=0,draws=0,loaded=0;
 while(at<emitted) {
  unsigned op=words[at]>>24,n=words[at]&0xffffff;
  CHECK((op==0x56&&n==8)||(op==0x54&&n==1)||(op==0x4e&&n==17));CHECK(at+n<emitted);
  if(op==0x56){CHECK(words[at+1]<32);loaded|=1u<<words[at+1];}
  if(op==0x54){uint32_t p=words[at+1];for(unsigned k=0;k<3;k++)CHECK(loaded&(1u<<((p>>(5*k))&31)));draws++;}
  if(op==0x4e)draws++;
  at+=n+1;
 }
 return draws;
}
int main(void) {
 uint32_t raw[32][8]={{0}},cmd[64][2]={{0x05000204,0}};
 struct am_repair_job j={.count=3,.command_count=1,.raw_valid=1,.raw=raw,.commands=cmd,.material={.flags_rgb=0x7ffff,.u_offset=8,.v_offset=16,.alpha=255,.xcenter=160,.ycenter=120,.xscale=160,.yscale=120}};
 struct am_repair_result r;
 unsigned draws=0,indexed=0,direct=0,clipped=0;
 for(unsigned i=0;i<8192;i++) {
  for(unsigned k=0;k<3;k++) {
   float w=(i%8==0)?1.0f:0.125f+(random32()%65535)/32.0f;
   float x=((int)(random32()%65535)-32767)/16384.0f*w,y=((int)(random32()%65535)-32767)/16384.0f*w,z=((int)(random32()%65535)-32767)/16384.0f*w;
   if(i%3==0)x*=0.25f,y*=0.25f,z*=0.25f;
   if(i%31==0)w=-w;
   raw[k][0]=bits(x);raw[k][1]=bits(y);raw[k][2]=bits(z);raw[k][3]=bits(w);raw[k][4]=bits(1.0f/w);
   raw[k][5]=random32();raw[k][6]=random32();raw[k][7]=(x< -w)*32+(x>w)*16+(y< -w)*8+(y>w)*4+(z< -w)*2+(z>w);
  }
  j.cull=i%4;j.material.flags_rgb=random32()&0x7ffff;j.material.u_offset=(random32()&65535)*8;j.material.v_offset=(random32()&65535)*8;
  int mode=(i%3==0)?FE_DOWNWARD:(i%3==1)?FE_UPWARD:FE_TOWARDZERO;fesetround(mode);feclearexcept(FE_ALL_EXCEPT);feraiseexcept(FE_INVALID|FE_INEXACT);
  int flags=fetestexcept(FE_ALL_EXCEPT);emitted=0;limit=18000;
  CHECK(am_repair(&j,emit,0,&r)==AM_REPAIR_OK);CHECK(fegetround()==mode);CHECK(fetestexcept(FE_ALL_EXCEPT)==flags);CHECK(r.emitted_words==emitted);
  unsigned n=decode();CHECK(n==r.indexed_triangles+r.direct_triangles);draws+=n;indexed+=r.indexed_triangles;direct+=r.direct_triangles;clipped+=r.clipped_triangles;
  if(emitted&&i%17==0) {
   unsigned original=emitted;uint32_t saved[18000];memcpy(saved,words,original*4);
   for(unsigned stop=0;stop<original;stop++) {emitted=0;limit=stop;CHECK(am_repair(&j,emit,0,&r)==AM_REPAIR_CANCELLED);CHECK(emitted==stop);CHECK(!memcmp(words,saved,stop*4));CHECK(fegetround()==mode);CHECK(fetestexcept(FE_ALL_EXCEPT)==flags);}
  }
 }
 /* Malformed task must not emit even an earlier valid triangle. */
 j.command_count=2;cmd[1][0]=0x050002fe;emitted=0;limit=18000;CHECK(am_repair(&j,emit,0,&r)==AM_REPAIR_BAD_INPUT);CHECK(emitted==0);
 cmd[1][0]=0x04000204;CHECK(am_repair(&j,emit,0,&r)==AM_REPAIR_BAD_INPUT);CHECK(emitted==0);
 j.command_count=1;j.count=33;CHECK(am_repair(&j,emit,0,&r)==AM_REPAIR_BAD_INPUT);j.count=3;
 j.material.flags_rgb=0x80000;CHECK(am_repair(&j,emit,0,&r)==AM_REPAIR_BAD_INPUT);
 j.material.flags_rgb=1u<<16;j.material.u_offset=1;CHECK(am_repair(&j,emit,0,&r)==AM_REPAIR_BAD_INPUT);
 j.material.u_offset=524288;CHECK(am_repair(&j,emit,0,&r)==AM_REPAIR_BAD_INPUT);
 CHECK(indexed>0&&direct>0&&clipped>0);fesetround(FE_TONEAREST);
 printf("{\"status\":\"PASS\",\"checks\":%u,\"tasks\":8192,\"draws\":%u,\"indexed\":%u,\"direct\":%u,\"clipped\":%u}\n",checks,draws,indexed,direct,clipped);
 return 0;
}
