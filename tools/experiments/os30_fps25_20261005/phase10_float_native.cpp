#include "Vgeometry_float_vertex.h"
#include "verilated.h"
#include <cstdint>
#include <fstream>
#include <iostream>
int main(int argc,char**argv){Verilated::commandArgs(argc,argv);if(argc!=2)return 2;std::ifstream in(argv[1]);Vgeometry_float_vertex d;d.reset=1;d.in_valid=0;d.out_ready=1;d.matrix_write=0;unsigned clocks=0;auto tick=[&](){d.clk=0;d.eval();d.clk=1;d.eval();clocks++;};for(int i=0;i<4;i++)tick();d.reset=0;unsigned checks=0,bad=0,unsupported=0,vertices=0,mincy=1000,maxcy=0;
 uint32_t xyz[3],matrix[16],aspect,expected[4];
 while(in>>std::hex>>xyz[0]>>xyz[1]>>xyz[2]){
  for(auto& x:matrix)in>>x;in>>aspect;for(auto& x:expected)in>>x;if(!in)return 3;
  for(int i=0;i<16;i++){d.matrix_write=1;d.matrix_index=i;d.matrix_data=matrix[i];tick();}d.matrix_write=0;while(!d.in_ready)tick();d.vertex_x=xyz[0];d.vertex_y=xyz[1];d.vertex_z=xyz[2];d.aspect=aspect;d.in_valid=1;auto begin=clocks;tick();d.in_valid=0;while(!d.out_valid&&clocks-begin<200)tick();if(!d.out_valid)return 4;auto cy=clocks-begin;mincy=std::min(mincy,cy);maxcy=std::max(maxcy,cy);if(d.unsupported)unsupported++;
  uint32_t got[]={d.clip_x,d.clip_y,d.clip_z,d.clip_w};for(int i=0;i<4;i++){checks++;if(got[i]!=expected[i]){if(bad++<16)std::cerr<<"vertex "<<vertices<<" component "<<i<<" got "<<std::hex<<got[i]<<" expected "<<expected[i]<<std::dec<<"\n";}}vertices++;tick();
 }
 std::cout<<"{\"vertices\":"<<vertices<<",\"component_checks\":"<<checks<<",\"mismatches\":"<<bad<<",\"unsupported\":"<<unsupported<<",\"min_vertex_cycles\":"<<mincy<<",\"max_vertex_cycles\":"<<maxcy<<"}\n";return bad||unsupported||vertices!=230;}
