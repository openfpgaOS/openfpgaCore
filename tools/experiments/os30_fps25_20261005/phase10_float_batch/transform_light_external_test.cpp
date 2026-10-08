#include "Vgeometry_transform_light_external_probe.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <vector>
int main(int argc,char **argv){
 if(argc!=2)return 2;std::ifstream in(argv[1],std::ios::binary);
 auto read=[&](){uint32_t x;in.read((char*)&x,4);if(!in)throw 1;return x;};
 Vgeometry_transform_light_external_probe d;
 auto tick=[&](){d.clk=0;d.eval();d.clk=1;d.eval();};
 d.reset=1;d.start=d.matrix_we=d.position_we=d.normal_we=0;
 d.scratch_read_valid=d.scratch_write=0;
 for(int i=0;i<10;i++)tick();d.reset=0;
 std::array<std::array<uint32_t,5>,32> cache{};
 std::array<uint32_t,32> colors{};
 std::array<bool,32> valid{},valid_rgb{};
 unsigned jobs=read(),vertices=0,checks=0,rgb_checks=0,scratch_checks=0,invalid_jobs=0;
 unsigned operations=0,busy_attacks=0,persistent_checks=0;
 auto verify_cache=[&](unsigned first,unsigned count){
  for(unsigned v=0;v<32;v++)if(valid[v]){
   for(unsigned k=0;k<5;k++){
    d.output_addr=k*32+v;tick();
    if(d.output_data!=cache[v][k]){
     std::cerr<<"cached coordinate mismatch slot "<<v<<" row "<<k<<" expected "<<std::hex<<cache[v][k]<<" actual "<<d.output_data<<"\n";return false;
    }
    checks++;if(v<first||v>=first+count)persistent_checks++;
   }
   if(valid_rgb[v]){
    if(d.output_rgb!=colors[v]){std::cerr<<"cached RGB mismatch slot "<<v<<"\n";return false;}rgb_checks++;
   }
  }return true;
 };
 for(unsigned job=0;job<jobs;job++){
  unsigned n=read(),dest=(job*7)%(33-n);uint32_t aspect=read();
  for(unsigned i=0;i<16;i++){d.matrix_we=1;d.matrix_addr=i;d.matrix_data=read();tick();}d.matrix_we=0;
  d.coefficient_x=read();d.coefficient_y=read();d.coefficient_z=read();
  d.ambient_rgb=read();d.diffuse_rgb=read();d.lighting_enabled=read();
  bool lighting=d.lighting_enabled;
  for(unsigned v=0;v<n;v++){
   for(unsigned k=0;k<3;k++){d.position_we=1;d.position_addr=3*v+k;d.position_data=read();tick();}
   d.position_we=0;
   for(unsigned k=0;k<5;k++)cache[dest+v][k]=read();valid[dest+v]=true;
   d.normal_we=1;d.normal_addr=dest+v;d.normal_data=read();tick();d.normal_we=0;
   colors[dest+v]=read();valid_rgb[dest+v]=read();
  }
  d.count=n;d.dest=dest;d.aspect=aspect;d.start=1;tick();d.start=0;
  for(unsigned elapsed=0;!d.done;elapsed++){
   if(elapsed>3000)return 3;
   // External scratch and changed destination cannot disturb owned work.
   d.dest=(elapsed*3)&31;d.count=63;
   d.scratch_write=1;d.scratch_write_addr=elapsed%96;d.scratch_write_data=0x7fc00000;
   d.scratch_read_valid=1;d.scratch_read_addr=(elapsed*7)%96;
   if(elapsed%11==3){d.start=1;d.count=32;}else d.start=0;
   tick();busy_attacks++;
  }
  d.start=0;d.scratch_write=d.scratch_read_valid=0;
  if(d.unsupported||d.owner_collision||d.operation_count!=n*(lighting?24:17)){
   std::cerr<<"protocol job "<<job<<" unsupported "<<unsigned(d.unsupported)<<" owner "<<unsigned(d.owner_collision)<<" operations "<<d.operation_count<<"\n";return 4;
  }operations+=d.operation_count;
  if(!verify_cache(dest,n))return 5;
  // Scratch is local to the load, independent of destination and global outputs.
  for(unsigned k=0;k<96;k++){
   d.scratch_write=1;d.scratch_write_addr=k;d.scratch_write_data=0x3f800000+job*256+k;tick();
  }d.scratch_write=0;
  for(unsigned k=0;k<96;k++){
   d.scratch_read_valid=1;d.scratch_read_addr=k;tick();
   if(d.scratch_read_data!=0x3f800000+job*256+k){std::cerr<<"scratch mismatch "<<k<<"\n";return 6;}scratch_checks++;
  }d.scratch_read_valid=0;
  if(!verify_cache(0,0))return 7;
  for(auto bad:std::vector<std::pair<unsigned,unsigned>>{{0,0},{33,0},{2,31},{32,1},{63,31}}){
   d.count=bad.first;d.dest=bad.second;d.start=1;tick();d.start=0;
   if(!d.done||!d.unsupported||d.busy){std::cerr<<"invalid range accepted\n";return 8;}invalid_jobs++;tick();
  }
  if(!verify_cache(0,0))return 9;
  vertices+=n;
 }
 std::cout<<"{\"passed\":true,\"native_A_jobs\":"<<jobs<<",\"vertices\":"<<vertices
 <<",\"exact_cached_coordinates\":"<<checks<<",\"exact_cached_rgb\":"<<rgb_checks
 <<",\"preserved_outside_load_coordinates\":"<<persistent_checks<<",\"scratch_checks\":"<<scratch_checks
 <<",\"invalid_ranges\":"<<invalid_jobs<<",\"busy_access_attacks\":"<<busy_attacks<<",\"FMA_operations\":"<<operations<<"}\n";
}
