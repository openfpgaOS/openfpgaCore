#include "Vgeometry_transform_light.h"
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
 Verilated::commandArgs(argc,argv);Vgeometry_transform_light d;
 auto tick=[&](){d.clk=0;d.eval();d.clk=1;d.eval();};
 auto reset=[&](){d.reset=1;d.start=0;d.matrix_we=0;d.position_we=0;d.normal_we=0;for(int i=0;i<10;i++)tick();d.reset=0;};
 reset();std::mt19937 rng(0x20261007);unsigned checks=0,jobs=0,aborts=0;uint64_t vertices=0;
 uint32_t cycle_by_count[33]={};unsigned reciprocal_checks=0,rgb_checks=0;
 for(unsigned job=0;job<384;job++){
  unsigned n=1+job%32;uint32_t m[16],p[96],a=u(1.0f+float(job%4)/8);
  for(unsigned i=0;i<16;i++){m[i]=u(float(int(rng()%65536)-32768)/512);d.matrix_we=1;d.matrix_addr=i;d.matrix_data=m[i];tick();}d.matrix_we=0;
  for(unsigned i=0;i<n*3;i++){p[i]=u(float(int(rng()%1048576)-524288)/32);d.position_we=1;d.position_addr=i;d.position_data=p[i];tick();}d.position_we=0;
  uint32_t normals[32],coef[3],amb=rng()&0xffffff,dif=rng()&0xffffff;
  for(unsigned k=0;k<3;k++)coef[k]=u(float(int(rng()%8192)-4096)/4096);
  for(unsigned v=0;v<n;v++){normals[v]=rng()&0xffffff;d.normal_we=1;d.normal_addr=v;d.normal_data=normals[v];tick();}d.normal_we=0;
  bool lighting=(job%5)!=0;
  d.coefficient_x=coef[0];d.coefficient_y=coef[1];d.coefficient_z=coef[2];d.ambient_rgb=amb;d.diffuse_rgb=dif;d.lighting_enabled=lighting;
  d.count=n;d.aspect=a;d.start=1;tick();d.start=0;
  bool aborted=(job%19)==5;
  for(unsigned elapsed=0;!d.done;elapsed++){
   if(elapsed>3000){std::cerr<<"timeout\n";return 2;}
   // Writes and starts while busy cannot modify ownership or active parameters.
   if(elapsed%11==3){d.start=1;d.count=32;d.aspect=u(77);d.matrix_we=1;d.position_we=1;d.matrix_data=0;d.position_data=0;d.normal_we=1;d.normal_data=0;d.lighting_enabled=0;d.ambient_rgb=0;d.coefficient_x=0;}
   else {d.start=0;d.matrix_we=0;d.position_we=0;d.normal_we=0;}
   tick();
   if(aborted && elapsed==31){reset();aborts++;break;}
  }
  d.start=0;d.matrix_we=0;d.position_we=0;d.normal_we=0;
  if(aborted)continue;
  if(d.unsupported){std::cerr<<"unexpected unsupported\n";return 3;}
  if(lighting && cycle_by_count[n] && cycle_by_count[n]!=d.cycles){std::cerr<<"cycle mismatch\n";return 4;}if(lighting)cycle_by_count[n]=d.cycles;
  for(unsigned v=0;v<n;v++)for(unsigned r=0;r<4;r++){
   uint32_t q=mul(p[3*v+1],m[4+r]);q=madd(p[3*v],m[r],q);
   q=madd(0x3f800000,m[12+r],q);q=madd(p[3*v+2],m[8+r],q);if(r==0)q=mul(q,a);
   d.output_addr=r*32+v;tick();
   if(d.output_data!=q){std::cerr<<"mismatch job "<<job<<" v "<<v<<" row "<<r<<std::hex<<" expected "<<q<<" actual "<<d.output_data<<"\n";return 5;}checks++;
  }
  for(unsigned v=0;v<n;v++){
   uint32_t q=mul(p[3*v+1],m[7]);q=madd(p[3*v],m[3],q);q=madd(0x3f800000,m[15],q);q=madd(p[3*v+2],m[11],q);
   uint32_t expect=u(1.0f/f(q));d.output_addr=128+v;tick();
   if(d.output_data!=expect){std::cerr<<"reciprocal mismatch job "<<job<<" vertex "<<v<<std::hex<<" expected "<<expect<<" actual "<<d.output_data<<"\n";return 6;}reciprocal_checks++;
  }
  for(unsigned v=0;v<n;v++){
   uint32_t expected=normals[v];
   if(lighting){
    uint32_t nx=u(float(int8_t(normals[v]))),ny=u(float(int8_t(normals[v]>>8))),nz=u(float(int8_t(normals[v]>>16)));
    uint32_t intensity=mul(ny,coef[1]);intensity=madd(coef[0],nx,intensity);intensity=madd(coef[2],nz,intensity);intensity=mul(intensity,0x3c010204);
    if(intensity&0x80000000)intensity=0;
    expected=0;
    for(unsigned k=0;k<3;k++){int c=int(f(madd(u(float((dif>>(8*k))&255)),intensity,u(float((amb>>(8*k))&255)))));if(c<0)c=0;if(c>255)c=255;expected|=uint32_t(c)<<(8*k);}
   }
   d.output_addr=v;tick();if(d.output_rgb!=expected){std::cerr<<"RGB mismatch job "<<job<<" vertex "<<v<<std::hex<<" expected "<<expected<<" actual "<<d.output_rgb<<"\n";return 7;}rgb_checks++;
  }
  jobs++;vertices+=n;
 }
 std::cout<<"{\"passed\":true,\"jobs\":"<<jobs<<",\"vertices\":"<<vertices<<",\"exact_coordinates\":"<<checks<<",\"exact_rgb\":"<<rgb_checks<<",\"exact_reciprocals\":"<<reciprocal_checks<<",\"cancellations\":"<<aborts<<",\"cycles_by_vertex_count\":{";
 for(unsigned n=1;n<=32;n++)std::cout<<(n>1?",":"")<<"\""<<n<<"\":"<<cycle_by_count[n];
 std::cout<<"}}\n";
}
