#include "Vgeometry_float_batch_recip.h"
#include "verilated.h"
#include <cstdint>
#include <fstream>
#include <iostream>
#include <vector>
int main(int argc,char **argv){
 if(argc!=2)return 2;std::ifstream in(argv[1],std::ios::binary);
 auto read=[&](){uint32_t x;in.read((char*)&x,4);if(!in)throw 1;return x;};
 Vgeometry_float_batch_recip d;auto tick=[&](){d.clk=0;d.eval();d.clk=1;d.eval();};
 d.reset=1;d.start=d.matrix_we=d.position_we=0;for(int i=0;i<10;i++)tick();d.reset=0;
 unsigned jobs=read(),vertices=0,checks=0,cycles=0;
 for(unsigned job=0;job<jobs;job++){
  unsigned n=read();uint32_t aspect=read();
  for(unsigned i=0;i<16;i++){d.matrix_we=1;d.matrix_addr=i;d.matrix_data=read();tick();}d.matrix_we=0;
  std::vector<uint32_t> expected;
  for(unsigned v=0;v<n;v++){
   for(unsigned k=0;k<3;k++){d.position_we=1;d.position_addr=3*v+k;d.position_data=read();tick();}
   for(unsigned k=0;k<5;k++)expected.push_back(read());
  }d.position_we=0;d.count=n;d.aspect=aspect;d.start=1;tick();d.start=0;
  for(unsigned elapsed=0;!d.done;elapsed++){if(elapsed>3000)return 3;tick();}
  if(d.unsupported){std::cerr<<"unsupported job "<<job<<"\n";return 4;}
  cycles+=d.cycles;
  for(unsigned v=0;v<n;v++)for(unsigned k=0;k<5;k++){
   d.output_addr=k*32+v;tick();
   if(d.output_data!=expected[v*5+k]){std::cerr<<"job "<<job<<" vertex "<<v<<" coordinate "<<k<<" expected "<<std::hex<<expected[v*5+k]<<" actual "<<d.output_data<<"\n";return 5;}checks++;
  }vertices+=n;
 }
 std::cout<<"{\"passed\":true,\"native_A_jobs\":"<<jobs<<",\"vertices\":"<<vertices<<",\"exact_xyzw_reciprocal\":"<<checks<<",\"transform_reciprocal_cycles\":"<<cycles<<",\"scope\":\"Transform and reciprocal component only; excludes input loading, lighting, clip/cull, DMA and GPU\"}\n";
}
