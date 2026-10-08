#include <boost/multiprecision/cpp_bin_float.hpp>
#include <cstdint>
#include <cstring>
#include <cfenv>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <random>
using Q=boost::multiprecision::number<boost::multiprecision::cpp_bin_float<100>>;
static float f(uint32_t x){float v;memcpy(&v,&x,4);return v;}
static uint32_t u(float x){uint32_t v;memcpy(&v,&x,4);return v;}
static uint32_t madd(uint32_t ai,uint32_t bi,uint32_t ci){
 Q p=Q(f(ai))*Q(f(bi));
 if(p!=0){int e;Q m=frexp(abs(p),&e);Q mag=ldexp(floor(ldexp(m,26)),e-26);p=p<0?-mag:mag;}
 return u(static_cast<float>(p+Q(f(ci))));
}
int main(int argc,char **argv){
 if(argc!=2)return 2;std::ofstream out(argv[1]);std::mt19937 rng(0x301005c0);fesetround(FE_TONEAREST);
 unsigned classes[3]={};
 for(unsigned job=0;job<24000;job++){
  uint32_t a[16]={};for(unsigned v=0;v<3;v++){
   a[v*3]=u(float(int(rng()%1048576)-524288)/32);
   a[v*3+1]=u(float(int(rng()%1048576)-524288)/32);
   float w=float(1+rng()%262144)/16;if(rng()&1)w=-w;a[v*3+2]=u(1.0f/w);
  }
  a[9]=rng()%8;a[10]=job%7==0?rng()%64:0;a[11]=job%7==0?rng()%64:0;a[12]=job%7==0?rng()%64:0;a[13]=job%4;
  if(job%31==0){for(unsigned k=0;k<3;k++)a[6+k]=a[k];} // degenerate equal vertices
  if(a[10]&a[11]&a[12])a[15]=1;
  else if(a[13]==3)a[15]=2;
  else if(a[13]){
   uint32_t cx=u(f(a[3])*f(a[5])),cy=u(f(a[4])*f(a[5]));
   uint32_t dx1=madd(a[0],a[2],cx^0x80000000u),dy1=madd(a[1],a[2],cy^0x80000000u);
   uint32_t dx2=madd(a[6],a[8],cx^0x80000000u),dy2=madd(a[7],a[8],cy^0x80000000u);
   float p0=f(dx1)*f(dy2),p1=f(dy1)*f(dx2);bool parity=(a[9]^(a[9]>>1)^(a[9]>>2))&1;
   float cross=parity?p1-p0:p0-p1;a[14]=u(cross);
   a[15]=a[13]==1?(cross<=0?2:0):(cross>=0?2:0);
  }
  classes[a[15]]++;for(unsigned i=0;i<16;i++)out<<std::hex<<std::setw(8)<<std::setfill('0')<<a[i]<<(i==15?'\n':' ');
 }
 std::cout<<"{\"vectors\":24000,\"keep\":"<<classes[0]<<",\"clip_reject\":"<<classes[1]<<",\"cull_reject\":"<<classes[2]<<",\"oracle\":\"333-bit reduced-FMA plus RNE multiply/subtract\"}\n";
}
