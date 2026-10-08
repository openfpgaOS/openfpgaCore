#include "Vgeometry_color_rtz.h"
#include "verilated.h"
#include <cstdint>
#include <cstring>
#include <cmath>
#include <deque>
#include <iostream>
#include <random>
struct Expected{bool bad;uint32_t color;};
int main(){
 Vgeometry_color_rtz d;auto tick=[&](){d.clk=0;d.eval();d.clk=1;d.eval();};
 d.reset=1;d.in_valid=0;for(int i=0;i<8;i++)tick();d.reset=0;
 std::mt19937 rng(0x20261007);std::deque<Expected> pending;
 unsigned checks=0,bad=0;uint32_t directed[]={0,0x80000000,0x3f7fffff,0x3f800000,0x437effff,0x437f0000,0x437fffff,0x43800000,0x4effffff,0x4f000000,0xcf000000,0xcf000001,0x7f800000,0xff800000,0x7fc00000,1,0x807fffff};
 for(unsigned i=0;i<1000016+8;i++){
  d.in_valid=i<1000016 && (i%13)!=12;d.value=i<17?directed[i]:rng();
  if(d.in_valid){
   float f;uint32_t b=d.value;memcpy(&f,&b,4);double x=f;
   bool unsupported=!std::isfinite(x)||x>=2147483648.0||x< -2147483648.0;
   uint32_t c=unsupported?0:x<0?0:x>=255?255:uint32_t(x);
   pending.push_back({unsupported,c});
  }
  tick();if(d.out_valid){
   if(pending.empty())return 2;auto e=pending.front();pending.pop_front();
   if(bool(d.unsupported)!=e.bad || (!e.bad && d.fixed_value!=e.color)){std::cerr<<"mismatch at "<<i<<"\n";return 3;}
   checks++;bad+=e.bad;
  }
 }
 if(!pending.empty())return 4;
 std::cout<<"{\"passed\":true,\"checks\":"<<checks<<",\"unsupported\":"<<bad<<",\"first_output_cycle\":3}\n";
}
