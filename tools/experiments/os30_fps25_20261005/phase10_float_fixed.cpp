#include "Vgeometry_float_fixed.h"
#include "verilated.h"
#include <cmath>
#include <cfenv>
#include <cstdint>
#include <cstring>
#include <deque>
#include <iostream>
#include <random>
static float f(uint32_t x){float v;memcpy(&v,&x,4);return v;}
struct Test {uint32_t in,expected;unsigned scale;bool trunc,depth,bad;};
int main(int argc,char**argv){Verilated::commandArgs(argc,argv);fesetround(FE_TONEAREST);Vgeometry_float_fixed d;d.reset=1;d.in_valid=0;auto tick=[&](){d.clk=0;d.eval();d.clk=1;d.eval();};for(int i=0;i<4;i++)tick();d.reset=0;std::mt19937 r(0x753218);unsigned checks=0,bad=0,unsupported=0,first=999;std::deque<Test> q;
 for(unsigned cycle=0;cycle<300008;cycle++){
  d.in_valid=cycle<300000 && cycle%19!=7;
  if(d.in_valid){Test t;t.in=r();t.scale=(cycle%4==0?8:cycle%4==1?16:cycle%4==2?30:0);t.trunc=cycle%5==0;t.depth=cycle%7==0;
   if(cycle%23==0)t.in=(r()&0x80000000u)|((r()%9+123)<<23)|(r()&0x7fffffu);
   float v=std::ldexp(f(t.in),t.depth?30:t.scale);
   if(t.depth){if(v<1)v=1;else if(v>2147483520.0f)v=2147483520.0f;}
   double rounded=t.trunc&&!t.depth?std::trunc((double)v):std::nearbyint((double)v);
   t.bad=!std::isfinite(rounded)||rounded>2147483647.0||rounded< -2147483648.0;
   t.expected=t.bad?0:(uint32_t)(int32_t)rounded;
   q.push_back(t);d.value=t.in;d.scale=t.scale;d.truncate=t.trunc;d.depth_mode=t.depth;
  }
  tick();if(d.out_valid){if(first==999)first=cycle;if(q.empty())return 2;Test t=q.front();q.pop_front();checks++;if(d.unsupported)unsupported++;
   if(d.unsupported!=t.bad || (!t.bad && d.fixed_value!=t.expected)){if(bad++<12)std::cerr<<std::hex<<"input="<<t.in<<" got="<<d.fixed_value<<" expected="<<t.expected<<std::dec<<" scale="<<t.scale<<" trunc="<<t.trunc<<" depth="<<t.depth<<" unsupported="<<(unsigned)d.unsupported<<" expectedbad="<<t.bad<<"\n";}}
 }
 std::cout<<"{\"checks\":"<<checks<<",\"mismatches\":"<<bad<<",\"unsupported\":"<<unsupported<<",\"first_result_cycle\":"<<first<<",\"pending\":"<<q.size()<<"}\n";return bad||!q.empty();}
