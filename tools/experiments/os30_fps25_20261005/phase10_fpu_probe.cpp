#include "VGeometryFmaProbe.h"
#include "verilated.h"
#include <algorithm>
#include <boost/multiprecision/cpp_bin_float.hpp>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <deque>
#include <iostream>
#include <random>
struct Item {uint32_t a,b,c,expected;bool mul;};
static float f(uint32_t x){float v;memcpy(&v,&x,4);return v;}
static uint32_t u(float x){uint32_t v;memcpy(&v,&x,4);return v;}
static uint32_t oracle(uint32_t ai,uint32_t bi,uint32_t ci,bool mul){
  if(mul)return u(f(ai)*f(bi));
  using Q=boost::multiprecision::number<boost::multiprecision::cpp_bin_float<100>>;
  Q p=Q(f(ai))*Q(f(bi));
  if(p!=0){int e;Q m=frexp(abs(p),&e);Q mag=ldexp(floor(ldexp(m,26)),e-26);p=p<0?-mag:mag;}
  return u(static_cast<float>(p+Q(f(ci))));
}
int main(int argc,char**argv){Verilated::commandArgs(argc,argv);VGeometryFmaProbe d;d.clk=0;d.reset=1;d.io_valid=0;
 auto tick=[&](){d.clk=0;d.eval();d.clk=1;d.eval();};for(int i=0;i<4;i++)tick();d.reset=0;
 std::mt19937 rng(0x102607);std::deque<Item> q;unsigned checks=0,bad=0,unsupported=0;int first=-1;
 for(unsigned cycle=0;cycle<200016;cycle++){
  bool send=cycle<200000 && cycle%13!=7;d.io_valid=send;
  if(send){auto get=[&](){return (rng()&0x807fffffu)|((rng()%150+45)<<23);};Item it;it.a=get();it.b=get();it.c=get();it.mul=cycle&1;
    if(cycle%19==0)it.a=0;if(cycle%23==0)it.b=0;if(cycle%17==0)it.c=0;
    if(cycle%31==0){it.a=0x3f800000;it.b=get();it.c=it.b^0x80000000u;it.mul=false;}
    it.expected=oracle(it.a,it.b,it.c,it.mul);d.io_a=it.a;d.io_b=it.b;d.io_c=it.c;d.io_multiplyOnly=it.mul;q.push_back(it);
  }
  tick();if(d.io_resultValid){if(first<0)first=cycle; if(q.empty()){std::cerr<<"unsolicited result\n";return 3;}auto it=q.front();q.pop_front();if(d.io_unsupported){unsupported++;continue;}checks++;if(d.io_result!=it.expected){if(bad++<12)std::cerr<<std::hex<<"mismatch a="<<it.a<<" b="<<it.b<<" c="<<it.c<<" mul="<<it.mul<<" got="<<d.io_result<<" want="<<it.expected<<std::dec<<"\n";}}
 }
 std::cout<<"{\"checks\":"<<checks<<",\"mismatches\":"<<bad<<",\"unsupported\":"<<unsupported<<",\"first_result_cycle\":"<<first<<",\"pending\":"<<q.size()<<"}\n";return bad||!q.empty();}
