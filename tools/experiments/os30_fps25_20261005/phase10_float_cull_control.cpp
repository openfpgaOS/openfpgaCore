#include "Vgeometry_float_cull_probe.h"
#include "verilated.h"
#include <cstdint>
#include <iostream>
int main(int argc,char**argv){
 Verilated::commandArgs(argc,argv);Vgeometry_float_cull_probe d;
 auto tick=[&](){d.clk=0;d.eval();d.clk=1;d.eval();};
 auto reset=[&](){d.reset=1;d.in_valid=0;d.out_ready=0;for(int i=0;i<4;i++)tick();d.reset=0;};
 auto normal=[&](){uint32_t v[9]={0x3f800000,0x3f800000,0x3f800000,0,0,0x3f800000,0xbf800000,0x3f800000,0x3f800000};for(unsigned i=0;i<9;i++)d.vertex_data[i]=v[i];d.negative_w=0;d.clip_bits=0;d.cull_mode=2;};
 auto begin=[&](){if(!d.in_ready)return false;d.in_valid=1;tick();d.in_valid=0;return true;};
 auto finish=[&](){for(unsigned i=0;!d.out_valid;i++){if(i>100)return false;tick();}return true;};
 auto consume=[&](){d.out_ready=1;tick();d.out_ready=0;};
 reset();unsigned checks=0,badJobs=0,recoveries=0,cancelled=0;
 for(unsigned special=0;special<4;special++)for(unsigned clip=0;clip<2;clip++)for(unsigned mode=0;mode<4;mode++){
  normal();d.cull_mode=mode;d.clip_bits=clip?0x1041:0;
  const unsigned slot[4]={0,3,7,5};const uint32_t value[4]={0x7fc12345,0x7f800000,1,0xffc00123};d.vertex_data[slot[special]]=value[special];
  if(!begin()||!finish())return 2;
  bool wantBad=!clip&&(mode==1||mode==2);if(bool(d.unsupported)!=wantBad)return 3;if(wantBad)badJobs++;
  if(clip&&d.reject_class!=1)return 4;if(!clip&&mode==3&&d.reject_class!=2)return 5;if(!clip&&mode==0&&d.reject_class!=0)return 6;
  checks++;consume();normal();if(!begin()||!finish()||d.unsupported||d.cross_bits!=0x40000000||d.reject_class!=2)return 7;consume();recoveries++;
 }
 for(unsigned delay=0;delay<37;delay++){
  normal();if(!begin())return 8;for(unsigned i=0;i<delay;i++)tick();reset();cancelled++;
  normal();if(!begin()||!finish()||d.unsupported||d.cross_bits!=0x40000000||d.reject_class!=2)return 9;consume();recoveries++;
 }
 std::cout<<"{\"passed\":true,\"exception_control_checks\":"<<checks<<",\"unsupported_jobs\":"<<badJobs<<",\"valid_recoveries\":"<<recoveries<<",\"global_reset_cancellations\":"<<cancelled<<"}\n";
}
