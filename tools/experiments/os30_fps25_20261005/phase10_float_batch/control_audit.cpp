#include "Vgeometry_float_batch_recip.h"
#include "verilated.h"
#include <cstdint>
#include <cstring>
#include <iostream>
static uint32_t u(float x){uint32_t v;memcpy(&v,&x,4);return v;}
static float f(uint32_t x){float v;memcpy(&v,&x,4);return v;}
int main(int argc,char **argv){
 Verilated::commandArgs(argc,argv);Vgeometry_float_batch_recip d;
 auto tick=[&](){d.clk=0;d.eval();d.clk=1;d.eval();};
 d.start=0;d.position_we=0;d.matrix_we=0;d.reset=1;for(int i=0;i<5;i++)tick();d.reset=0;
 auto load=[&](uint32_t w){
  for(unsigned i=0;i<16;i++){d.matrix_we=1;d.matrix_addr=i;d.matrix_data=i==15?w:(i==0||i==5||i==10?0x3f800000:0);tick();}d.matrix_we=0;
  for(unsigned i=0;i<96;i++){d.position_we=1;d.position_addr=i;d.position_data=u(float(1+i%9));tick();}d.position_we=0;
 };
 auto run=[&](unsigned n){d.start=1;d.count=n;d.aspect=0x3f800000;tick();d.start=0;
  for(unsigned i=0;!d.done;i++){if(i>3000)return false;tick();}return true;};
 unsigned invalid=0,exceptional=0,recoveries=0,exact=0;
 for(unsigned n:{0u,33u,63u}){if(!run(n)||d.busy||!d.unsupported)return 2;invalid++;tick();if(d.done)return 3;}
 const uint32_t cases[]={0,0x80000000u,0x3f800000u,0xbf800000u,0x3fc00000u,0xbfc00000u,0x7f800000u,0xff800000u,0x7fc00123u,0x00800000u,0x7f7fffffu,0x00000001u};
 for(unsigned j=0;j<sizeof(cases)/sizeof(cases[0]);j++){
  uint32_t w=cases[j];unsigned exponent=(w>>23)&255;bool bad=exponent==0||exponent>=254;
  load(w);if(!run(1+j%32))return 4;
  if(bool(d.unsupported)!=bad){std::cerr<<"unexpected classification w="<<std::hex<<w<<" actual="<<unsigned(d.unsupported)<<"\n";return 5;}
  if(bad)exceptional++;
  else for(unsigned i=0;i<1+j%32;i++){d.output_addr=128+i;tick();if(d.output_data!=u(1.0f/f(w)))return 6;exact++;}
  // The unsupported flag is sticky per job, never across jobs. A subsequent
  // valid job must replace all of its results without a chip-level reset.
  load(0x3f800000);if(!run(32)||d.unsupported||d.busy)return 7;
  for(unsigned v=0;v<32;v++){
   for(unsigned row=0;row<3;row++){d.output_addr=row*32+v;tick();if(d.output_data!=u(float(1+(3*v+row)%9)))return 8;exact++;}
   d.output_addr=96+v;tick();if(d.output_data!=0x3f800000)return 9;exact++;
   d.output_addr=128+v;tick();if(d.output_data!=0x3f800000)return 10;exact++;
  }recoveries++;
 }
 std::cout<<"{\"passed\":true,\"invalid_count_jobs\":"<<invalid<<",\"unsupported_jobs\":"<<exceptional<<",\"recovery_jobs\":"<<recoveries<<",\"exact_values\":"<<exact<<"}\n";
}
