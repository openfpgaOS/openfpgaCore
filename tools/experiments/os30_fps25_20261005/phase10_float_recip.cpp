#include "Vgeometry_float_recip.h"
#include "verilated.h"
#include <cmath>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <random>
static float f(uint32_t x){float v;memcpy(&v,&x,4);return v;}static uint32_t u(float x){uint32_t v;memcpy(&v,&x,4);return v;}
int main(int argc,char**argv){Verilated::commandArgs(argc,argv);Vgeometry_float_recip d;d.reset=1;d.in_valid=0;d.out_ready=1;unsigned clocks=0;auto tick=[&](){d.clk=0;d.eval();d.clk=1;d.eval();clocks++;};for(int i=0;i<4;i++)tick();d.reset=0;std::mt19937 r(0x625302);unsigned bad=0,count=0,mincy=10000,maxcy=0,unsupported=0;
 for(unsigned trial=0;trial<250000;trial++){
  uint32_t x=r();if(trial%37==0)x&=0xff800000u;while(!d.in_ready)tick();d.denominator=x;d.in_valid=1;unsigned begin=clocks;tick();d.in_valid=0;while(!d.out_valid && clocks-begin<80)tick();unsigned elapsed=clocks-begin;mincy=std::min(mincy,elapsed);maxcy=std::max(maxcy,elapsed);if(!d.out_valid){std::cerr<<"timeout\n";return 2;}
  uint32_t expected=u(1.0f/f(x));unsigned e=(x>>23)&255,oe=(expected>>23)&255;bool accept=e!=0 && e!=255 && oe!=0 && oe!=255;
  if(d.unsupported)unsupported++;if(d.unsupported==accept){std::cerr<<"classification mismatch\n";return 3;}
  if(accept){count++;if(d.reciprocal!=expected){if(bad++<8)std::cerr<<"mismatch "<<std::hex<<x<<" got "<<d.reciprocal<<" expected "<<expected<<std::dec<<"\n";}}
  // Result must remain stable under downstream backpressure.
  if(trial%113==0){d.out_ready=0;uint32_t keep=d.reciprocal;for(int i=0;i<3;i++){tick();if(!d.out_valid||d.reciprocal!=keep)return 4;}d.out_ready=1;}
  tick();
 }
 std::cout<<"{\"reciprocal_checks\":"<<count<<",\"mismatches\":"<<bad<<",\"unsupported\":"<<unsupported<<",\"min_cycles\":"<<mincy<<",\"max_cycles\":"<<maxcy<<"}\n";return bad!=0;}
