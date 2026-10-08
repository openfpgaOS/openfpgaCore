#include "Vgeometry_float_recip.h"
#include "verilated.h"
#include <fstream>
#include <iostream>
#include <cstdint>
int main(int argc,char**argv){Verilated::commandArgs(argc,argv);if(argc!=2)return 2;std::ifstream in(argv[1]);Vgeometry_float_recip d;d.reset=1;d.in_valid=0;d.out_ready=1;unsigned clocks=0;auto tick=[&](){d.clk=0;d.eval();d.clk=1;d.eval();clocks++;};for(int i=0;i<4;i++)tick();d.reset=0;unsigned checks=0,bad=0,unsupported=0,mincy=1000,maxcy=0;uint32_t x,expected;
 while(in>>std::hex>>x>>expected){while(!d.in_ready)tick();d.denominator=x;d.in_valid=1;auto begin=clocks;tick();d.in_valid=0;while(!d.out_valid&&clocks-begin<80)tick();if(!d.out_valid)return 3;auto cy=clocks-begin;mincy=std::min(mincy,cy);maxcy=std::max(maxcy,cy);unsupported+=d.unsupported;checks++;if(d.reciprocal!=expected){if(bad++<12)std::cerr<<std::hex<<x<<" got "<<d.reciprocal<<" expected "<<expected<<std::dec<<"\n";}tick();}
 std::cout<<"{\"native_reciprocal_checks\":"<<checks<<",\"mismatches\":"<<bad<<",\"unsupported\":"<<unsupported<<",\"min_cycles\":"<<mincy<<",\"max_cycles\":"<<maxcy<<"}\n";return bad||unsupported||checks!=230;}
