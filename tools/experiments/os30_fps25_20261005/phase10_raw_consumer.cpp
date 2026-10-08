#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iostream>
#include <vector>
static float f(uint32_t x){float y;memcpy(&y,&x,4);return y;}
static uint32_t u(float x){uint32_t y;memcpy(&y,&x,4);return y;}
static uint32_t rd(const uint8_t*p){uint32_t y;memcpy(&y,p,4);return y;}
static int16_t sh(const uint8_t*p){int16_t y;memcpy(&y,p,2);return y;}
int main(int argc,char**argv){
 if(argc!=2)return 2;std::ifstream in(argv[1],std::ios::binary);
 auto read=[&](void*p,unsigned n){in.read((char*)p,n);if(!in)throw 1;};
 uint32_t jobs;read(&jobs,4);uint64_t vertices=0,coordinates=0,recip=0,clips=0,uvs=0,colors=0,alphas=0,bad[6]={};unsigned shown=0;
 for(unsigned job=0;job<jobs;job++){
  uint32_t h[4],m[17],light[22],control[3];read(h,sizeof h);read(m,sizeof m);read(light,sizeof light);read(control,sizeof control);
  std::vector<uint8_t> raw(h[2]*24),expected(h[2]*36);read(raw.data(),raw.size());read(expected.data(),expected.size());
  for(unsigned i=0;i<h[2];i++){
   auto s=raw.data()+24*i;auto d=expected.data()+36*i;
   float p[3]={f(rd(s)),f(rd(s+4)),f(rd(s+8))},q[4];
   auto fail=[&](unsigned field,uint32_t a,uint32_t b){bad[field]++;if(shown++<16)std::cerr<<"job "<<job<<" frame "<<h[0]<<" v "<<i<<" field "<<field<<" expected "<<std::hex<<b<<" actual "<<a<<std::dec<<"\n";};
   for(unsigned j=0;j<4;j++){
    float t=p[1]*f(m[4+j]);t=fmaf(p[0],f(m[j]),t);t=t+f(m[12+j]);t=fmaf(p[2],f(m[8+j]),t);if(!j)t*=f(m[16]);q[j]=t;
    if(u(t)!=rd(d+j*4))fail(0,u(t),rd(d+j*4));coordinates++;
   }
   float r=1.0f/q[3];if(u(r)!=rd(d+16))fail(1,u(r),rd(d+16));recip++;
   unsigned clip=(q[0]<-q[3]?32:0)|(q[0]>q[3]?16:0)|(q[1]<-q[3]?8:0)|(q[1]>q[3]?4:0)|(q[2]<-q[3]?2:0)|(q[2]>q[3]?1:0);
   if(clip!=d[32])fail(2,clip,d[32]);clips++;
   const bool lighting=control[0]&0x20000,texgen=lighting&&(control[0]&0x40000);
   if(!texgen){
    for(unsigned k=0;k<2;k++){
     int32_t tc=sh(s+14+2*k),scale=(control[2]>>(16*k))&65535;
     float value=(int16_t)((tc*scale)>>16);
     if(u(value)!=rd(d+20+4*k))fail(3,u(value),rd(d+20+4*k));uvs++;
    }
   }
   uint32_t rgb=0;
   if(!lighting){rgb=s[18]|(s[19]<<8)|(s[20]<<16);}
   else if(light[21]>=1&&light[21]<=3){
    const uint8_t* lights=(const uint8_t*)light;unsigned ambient=light[21]-1;
    int c[3]={lights[ambient*12],lights[ambient*12+1],lights[ambient*12+2]};
    for(unsigned l=0;l<ambient;l++){
     float dot=(int8_t)s[19]*f(light[9+3*l+1]);
     dot=fmaf((int8_t)s[18],f(light[9+3*l]),dot);
     dot=fmaf((int8_t)s[20],f(light[9+3*l+2]),dot);
     dot*=1.0f/127.0f;
     if(dot>0)for(unsigned k=0;k<3;k++)c[k]=(int)fmaf((float)lights[12*l+k],dot,(float)c[k]);
    }
    for(unsigned k=0;k<3;k++)rgb|=(uint32_t)std::clamp(c[k],0,255)<<(8*k);
   }else continue;
   uint32_t expected_rgb=d[28]|(d[29]<<8)|(d[30]<<16);
   if(rgb!=expected_rgb)fail(4,rgb,expected_rgb);colors++;
   // Fog alpha is deliberately lazy in F11; the stream carries coefficients,
   // but this LOAD-only consumer verifies alpha only when fog is disabled.
   if(!(control[0]&0x10000)){if(s[21]!=d[31])fail(5,s[21],d[31]);alphas++;}
   vertices++;
  }
 }
 bool passed=true;for(auto x:bad)passed&=x==0;
 std::cout<<"{\"passed\":"<<(passed?"true":"false")<<",\"jobs\":"<<jobs<<",\"vertices\":"<<vertices<<",\"coordinates\":"<<coordinates<<",\"reciprocals\":"<<recip<<",\"clip_masks\":"<<clips<<",\"UV_coordinates\":"<<uvs<<",\"RGB_values\":"<<colors<<",\"nonfog_alpha_values\":"<<alphas<<",\"mismatches\":[";
 for(unsigned i=0;i<6;i++)std::cout<<(i?",":"")<<bad[i];std::cout<<"]}\n";
 return passed?0:1;
}
