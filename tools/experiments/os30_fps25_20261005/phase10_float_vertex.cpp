#include "Vgeometry_float_vertex.h"
#include "verilated.h"
#include <boost/multiprecision/cpp_bin_float.hpp>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <random>
static float f(uint32_t x){float v;memcpy(&v,&x,4);return v;}static uint32_t u(float x){uint32_t v;memcpy(&v,&x,4);return v;}
static float fm(float a,float b,float c){using Q=boost::multiprecision::number<boost::multiprecision::cpp_bin_float<100>>;Q p=Q(a)*Q(b);if(p!=0){int e;Q m=frexp(abs(p),&e);Q mag=ldexp(floor(ldexp(m,26)),e-26);p=p<0?-mag:mag;}return static_cast<float>(p+Q(c));}
int main(int argc,char**argv){Verilated::commandArgs(argc,argv);Vgeometry_float_vertex d;d.reset=1;d.in_valid=0;d.out_ready=1;d.matrix_write=0;unsigned clocks=0;auto tick=[&](){d.clk=0;d.eval();d.clk=1;d.eval();clocks++;};for(int i=0;i<4;i++)tick();d.reset=0;std::mt19937 r(0x734524);unsigned bad=0,count=0,mincy=10000,maxcy=0,unsupported=0;
 for(unsigned trial=0;trial<4096;trial++){
  float m[16];for(int i=0;i<16;i++){m[i]=(int(r()%200001)-100000)/4096.0f;d.matrix_index=i;d.matrix_data=u(m[i]);d.matrix_write=1;tick();}d.matrix_write=0;
  float x=(int(r())%10000000)/16.0f,y=(int(r())%10000000)/16.0f,z=(int(r())%10000000)/16.0f;float aspect=0.75f;
  float expect[4];for(int i=0;i<4;i++){float a=(float)y*m[4+i];a=fm(x,m[i],a);a=fm(1,m[12+i],a);a=fm(z,m[8+i],a);expect[i]=i==0?a*aspect:a;}
  while(!d.in_ready)tick();d.vertex_x=u(x);d.vertex_y=u(y);d.vertex_z=u(z);d.aspect=u(aspect);d.in_valid=1;unsigned begin=clocks;tick();d.in_valid=0;while(!d.out_valid && clocks-begin<200)tick();unsigned elapsed=clocks-begin;mincy=std::min(mincy,elapsed);maxcy=std::max(maxcy,elapsed);if(!d.out_valid){std::cerr<<"timeout\n";return 2;}if(d.unsupported)unsupported++;
  uint32_t got[]={d.clip_x,d.clip_y,d.clip_z,d.clip_w};for(int i=0;i<4;i++){count++;if(got[i]!=u(expect[i])){if(bad++<8)std::cerr<<"mismatch trial "<<trial<<" component "<<i<<" got "<<std::hex<<got[i]<<" expected "<<u(expect[i])<<std::dec<<"\n";}}tick();
 }
 std::cout<<"{\"component_checks\":"<<count<<",\"mismatches\":"<<bad<<",\"unsupported\":"<<unsupported<<",\"min_vertex_cycles\":"<<mincy<<",\"max_vertex_cycles\":"<<maxcy<<"}\n";return bad!=0;}
