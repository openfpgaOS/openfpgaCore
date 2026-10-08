#include "Vgeometry_float_batch.h"
#include "verilated.h"
#include <boost/multiprecision/cpp_bin_float.hpp>
#include <cstdint>
#include <cstring>
#include <cmath>
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
static uint32_t mul(uint32_t a,uint32_t b){return u(f(a)*f(b));}
int main(int argc,char**argv){
 Verilated::commandArgs(argc,argv);Vgeometry_float_batch d;
 auto tick=[&](){d.clk=0;d.eval();d.clk=1;d.eval();};
 auto reset=[&](){d.reset=1;d.start=0;d.matrix_we=0;d.position_we=0;for(int i=0;i<10;i++)tick();d.reset=0;};
 reset();std::mt19937 rng(0x20261007);unsigned checks=0,jobs=0,aborts=0;uint64_t vertices=0;
 uint32_t cycle_by_count[33]={};
 for(unsigned job=0;job<384;job++){
  unsigned n=1+job%32;uint32_t m[16],p[96],a=u(1.0f+float(job%4)/8);
  for(unsigned i=0;i<16;i++){m[i]=u(float(int(rng()%65536)-32768)/512);d.matrix_we=1;d.matrix_addr=i;d.matrix_data=m[i];tick();}d.matrix_we=0;
  for(unsigned i=0;i<n*3;i++){p[i]=u(float(int(rng()%1048576)-524288)/32);d.position_we=1;d.position_addr=i;d.position_data=p[i];tick();}d.position_we=0;
  d.count=n;d.aspect=a;d.start=1;tick();d.start=0;
  bool aborted=(job%19)==5;
  for(unsigned elapsed=0;!d.done;elapsed++){
   if(elapsed>2000){std::cerr<<"timeout\n";return 2;}
   // Writes and starts while busy cannot modify ownership or active parameters.
   if(elapsed%11==3){d.start=1;d.count=32;d.aspect=u(77);d.matrix_we=1;d.position_we=1;d.matrix_data=0;d.position_data=0;}
   else {d.start=0;d.matrix_we=0;d.position_we=0;}
   tick();
   if(aborted && elapsed==31){reset();aborts++;break;}
  }
  d.start=0;d.matrix_we=0;d.position_we=0;
  if(aborted)continue;
  if(d.unsupported){std::cerr<<"unexpected unsupported\n";return 3;}
  if(cycle_by_count[n] && cycle_by_count[n]!=d.cycles){std::cerr<<"cycle mismatch\n";return 4;}cycle_by_count[n]=d.cycles;
  for(unsigned v=0;v<n;v++)for(unsigned r=0;r<4;r++){
   uint32_t q=mul(p[3*v+1],m[4+r]);q=madd(p[3*v],m[r],q);
   q=madd(0x3f800000,m[12+r],q);q=madd(p[3*v+2],m[8+r],q);if(r==0)q=mul(q,a);
   d.output_addr=r*32+v;tick();
   if(d.output_data!=q){std::cerr<<"mismatch job "<<job<<" v "<<v<<" row "<<r<<std::hex<<" expected "<<q<<" actual "<<d.output_data<<"\n";return 5;}checks++;
  }
  jobs++;vertices+=n;
 }
 std::cout<<"{\"passed\":true,\"jobs\":"<<jobs<<",\"vertices\":"<<vertices<<",\"exact_coordinates\":"<<checks<<",\"cancellations\":"<<aborts<<",\"cycles_by_vertex_count\":{";
 for(unsigned n=1;n<=32;n++)std::cout<<(n>1?",":"")<<"\""<<n<<"\":"<<cycle_by_count[n];
 std::cout<<"}}\n";
}
