#include "Vgeometry_float_fixed_pipe.h"
#include "verilated.h"
#include <cstdint>
#include <deque>
#include <fstream>
#include <iostream>
#include <vector>
struct Test { uint32_t value,expected; unsigned scale,trunc,depth,bad,native,mode; };
int main(int argc,char **argv) {
 Verilated::commandArgs(argc,argv);
 if(argc!=2)return 2;
 std::ifstream f(argv[1]);std::vector<Test> tests;Test t;
 while(f>>std::hex>>t.value>>t.expected>>std::dec>>t.scale>>t.trunc>>t.depth>>t.bad>>t.native>>t.mode)tests.push_back(t);
 if(tests.empty())return 2;
 Vgeometry_float_fixed_pipe d;d.reset=1;d.in_valid=0;
 auto tick=[&](){d.clk=0;d.eval();d.clk=1;d.eval();};
 for(int i=0;i<4;i++)tick();d.reset=0;
 std::deque<Test> q;unsigned i=0,checks=0,bad=0,unsupported=0,nativeUnsupported=0,nativeRne=0,nativeRtz=0,boundary=0,first=999;
 for(unsigned cycle=0;cycle<tests.size()*2+16;cycle++) {
  d.in_valid=i<tests.size() && cycle%11!=5;
  if(d.in_valid){t=tests[i++];q.push_back(t);d.value=t.value;d.scale=t.scale;d.truncate=t.trunc;d.depth_mode=t.depth;}
  tick();
  if(d.out_valid) {
   if(first==999)first=cycle;if(q.empty())return 3;t=q.front();q.pop_front();checks++;
   if(t.native){if(t.mode==0)nativeRne++;else nativeRtz++;}else boundary++;
   if(d.unsupported){unsupported++;if(t.native)nativeUnsupported++;}
   if(d.unsupported!=t.bad || (!t.bad && d.fixed_value!=t.expected)) {
    if(bad++<12)std::cerr<<std::hex<<"value="<<t.value<<" got="<<d.fixed_value<<" expected="<<t.expected<<std::dec<<" mode="<<t.mode<<" unsupported="<<(unsigned)d.unsupported<<" expected_bad="<<t.bad<<"\n";
   }
  }
 }
 std::cout<<"{\"native_RNE_checks\":"<<nativeRne<<",\"native_RTZ_checks\":"<<nativeRtz<<",\"boundary_checks\":"<<boundary<<",\"checks\":"<<checks<<",\"mismatches\":"<<bad<<",\"unsupported\":"<<unsupported<<",\"native_unsupported\":"<<nativeUnsupported<<",\"first_result_cycle\":"<<first<<",\"pending\":"<<q.size()<<"}\n";
 return bad || !q.empty() || i!=tests.size();
}
