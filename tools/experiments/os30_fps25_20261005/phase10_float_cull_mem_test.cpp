#include "Vgeometry_float_cull_mem_probe.h"
#include "verilated.h"
#include <cstdint>
#include <fstream>
#include <iostream>
int main(int argc,char**argv){
 Verilated::commandArgs(argc,argv);if(argc!=2)return 2;std::ifstream input(argv[1]);
 Vgeometry_float_cull_mem_probe d;d.reset=1;d.in_valid=0;d.out_ready=0;d.vertex_we=0;
 auto tick=[&](){d.clk=0;d.eval();d.clk=1;d.eval();};for(int i=0;i<5;i++)tick();d.reset=0;
 uint32_t words[16];unsigned checks=0,mismatches=0,unsupported=0,minCycles=999,maxCycles=0,held=0,arithmetic=0;
 while(input>>std::hex>>words[0]){
  for(unsigned i=1;i<16;i++)if(!(input>>std::hex>>words[i]))return 3;
  if(!d.in_ready)return 4;
  unsigned index[3]={checks%32,(checks+11)%32,(checks+23)%32};
  for(unsigned v=0;v<3;v++)for(unsigned field=0;field<3;field++){
   d.vertex_we=1;d.vertex_write_address=field*32+index[v];d.vertex_write_data=words[3*v+field];tick();
  }d.vertex_we=0;
  d.vertex_indices=index[0]|(index[1]<<5)|(index[2]<<10);
  d.negative_w=words[9];d.clip_bits=words[10]|(words[11]<<6)|(words[12]<<12);d.cull_mode=words[13];d.in_valid=1;tick();d.in_valid=0;
  unsigned cycles=1;while(!d.out_valid){tick();if(++cycles>200){std::cerr<<"timeout case="<<checks<<"\n";return 5;}}
  if(cycles<minCycles)minCycles=cycles;if(cycles>maxCycles)maxCycles=cycles;
  bool math=!(words[10]&words[11]&words[12])&&(words[13]==1||words[13]==2);arithmetic+=math;
  checks++;if(d.unsupported)unsupported++;
  if(d.cross_bits!=words[14]||d.reject_class!=words[15]||d.clip_or!=(words[10]|words[11]|words[12])||d.unsupported||d.operation_count!=(math?9:0)||d.memory_collision){
   if(mismatches++<10)std::cerr<<"case="<<checks-1<<std::hex<<" cross="<<d.cross_bits<<" expected="<<words[14]<<" reject="<<unsigned(d.reject_class)<<" wanted="<<words[15]<<" unsupported="<<unsigned(d.unsupported)<<" ops="<<unsigned(d.operation_count)<<" collision="<<unsigned(d.memory_collision)<<"\n";
  }
  uint32_t cross=d.cross_bits;unsigned reject=d.reject_class,clip=d.clip_or,bad=d.unsupported;
  for(unsigned i=0;i<checks%7;i++){tick();if(d.in_ready||!d.out_valid||d.cross_bits!=cross||d.reject_class!=reject||d.clip_or!=clip||d.unsupported!=bad)return 6;held++;}
  d.out_ready=1;tick();d.out_ready=0;
 }
 std::cout<<"{\"checks\":"<<checks<<",\"arithmetic_triangles\":"<<arithmetic<<",\"mismatches\":"<<mismatches<<",\"unsupported\":"<<unsupported<<",\"min_cycles\":"<<minCycles<<",\"max_cycles\":"<<maxCycles<<",\"backpressure_cycles\":"<<held<<"}\n";
 return mismatches||unsupported||!checks;
}
