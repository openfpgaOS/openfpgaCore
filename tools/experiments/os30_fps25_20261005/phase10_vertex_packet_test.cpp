#include "Vvertex_packet_top.h"
#include "verilated.h"
#include <array>
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iostream>
#include <random>
#include <stdexcept>
#include <vector>
static void need(bool b,const char*m){if(!b)throw std::runtime_error(m);}
static uint32_t bits(float f){uint32_t u;memcpy(&u,&f,4);return u;}
static float flt(uint32_t u){float f;memcpy(&f,&u,4);return f;}
struct Test {
 Vvertex_packet_top d;std::mt19937 random{0x509617};uint64_t clocks=0,jobs=0,words=0,errors=0,stalls=0,aborted_words=0;
 std::array<uint32_t,8> mem{};unsigned owned_slot=0;std::vector<uint32_t> output;
 bool held=false;uint32_t held_data=0;
 void tick(){
  d.clk=0;d.eval();bool read=d.raw_read_valid;unsigned address=d.raw_read_addr;
  if(!d.reset && d.word_valid){
   if(held)need(d.word_data==held_data,"output changed under backpressure");
   held=!d.word_ready;held_data=d.word_data;
   if(d.word_ready){output.push_back(d.word_data);words++;}else stalls++;
  }else{need(d.reset||!held,"held output disappeared");held=false;}
  d.clk=1;d.eval();
  if(read){need((address&31)==owned_slot,"raw slot was not frozen");d.raw_read_data=mem[address>>5];}
  d.eval();d.clk=0;d.eval();clocks++;
 }
 void reset(){d.reset=1;d.start=0;d.word_ready=0;tick();tick();d.reset=0;held=false;output.clear();tick();}
 void run(const std::array<uint32_t,15>&in,const std::array<uint32_t,7>&expected,bool bad=false,bool cancel=false){
  owned_slot=in[0];for(unsigned j=0;j<8;j++)mem[j]=in[7+j];output.clear();
  d.slot=in[0];d.draw_flags=in[1];d.prim_rgba=in[2];d.env_rgba=in[3];d.u_offset=in[4];d.v_offset=in[5];d.uv_bias=in[6];
  d.tile_uls=std::isfinite(flt(in[4]))&&flt(in[4])>=0&&flt(in[4])<=524280?unsigned(flt(in[4]))/8:0;
  d.tile_ult=std::isfinite(flt(in[5]))&&flt(in[5])>=0&&flt(in[5])<=524280?unsigned(flt(in[5]))/8:0;d.filter_linear=in[6]==bits(.5f);
  d.start=1;d.word_ready=0;tick();d.start=0;
  unsigned n=0;
  while(!d.done){
   need(n++<500,"job timed out");
   d.word_ready=(random()%4)!=0;
   if(d.busy){d.start=random()%7==0;d.slot=random()&31;uint32_t noise[9];for(auto&v:noise)v=random();
#ifndef HELD_CONTEXT
    d.draw_flags=noise[0];d.prim_rgba=noise[1];d.env_rgba=noise[2];d.u_offset=noise[3];d.v_offset=noise[4];d.uv_bias=noise[5];d.tile_uls=noise[6];d.tile_ult=noise[7];d.filter_linear=noise[8]&1;
#endif
   }
   if(cancel&&n==15){aborted_words+=output.size();words-=output.size();reset();return;}
   tick();
  }
  d.start=0;need(bool(d.unsupported)==bad,"unexpected arithmetic status");
  if(bad){need(output.empty(),"unsupported job emitted partial packet");errors++;}
  else{
   need(output.size()==9,"wrong packet length");need(output[0]==0x56000008&&output[1]==in[0],"header/slot mismatch");
   for(unsigned j=0;j<7;j++)if(output[j+2]!=expected[j]){
    std::cerr<<"job "<<jobs<<" field "<<j<<" expected "<<std::hex<<expected[j]<<" got "<<output[j+2]<<std::dec<<"\n";throw std::runtime_error("payload mismatch");
   }
   unsigned a=(in[1]>>20)&15,alpha=a==8||a==4?255:a==3?in[2]>>24:a==5?in[3]>>24:0;
   need(d.surface_alpha==alpha,"surface alpha mismatch");
  }
  need(!d.busy,"done busy");jobs++;tick();need(!d.done,"done is not a pulse");
 }
};
static std::array<uint32_t,7> reference(const std::array<uint32_t,15>&a){
 auto quant=[](float f){return uint32_t(int32_t(lrintf(f)));};bool textured=a[1]&(1u<<9);
 float u=textured?fmaf(float(int16_t(a[12]))-flt(a[4]),0.03125f,flt(a[6])):0;
 float v=textured?fmaf(float(int16_t(a[12]>>16))-flt(a[5]),0.03125f,flt(a[6])):0;
 unsigned source=a[1]>>24;uint32_t color=source==255?0xffffff:source==4?a[13]:source==3?a[2]:source==5?a[3]:0;
 return {quant(flt(a[7])*256),quant(flt(a[8])*256),quant(flt(a[10])*256),quant(u*65536),quant(v*65536),
         ((color&255)>>3)<<11|((color>>8&255)>>2)<<5|((color>>16&255)>>3),quant(std::max(1.f,std::min(2147483520.f,flt(a[11])*1073741824.f)))};
}
int main(int argc,char**argv){
 try{
  need(argc==3,"usage: packet-test cases.bin native-load.bin");Test t;t.reset();std::ifstream file(argv[1],std::ios::binary);need(bool(file),"cases open");
  std::array<uint32_t,22> record;uint64_t record_count=0,checked=0;
  while(file.read((char*)record.data(),sizeof record)){
   record_count++; // Deterministic coverage through every captured frame.
   // Every exported dirty-slot case is checked.
   std::array<uint32_t,15> in;std::array<uint32_t,7> expected;std::copy_n(record.begin(),15,in.begin());std::copy_n(record.begin()+15,7,expected.begin());
   need(expected==reference(in),"worker/reference disagreement");t.run(in,expected);checked++;
  }
  need(file.eof()&&file.gcount()==0,"partial case record");
  std::array<uint32_t,15> a={31,0x04810301,0x12345678,0x87654321,0,0,bits(.5f),bits(.5f),bits(-.5f),0,bits(1),bits(1),0,0x80a5b6c7,0};
  std::vector<float> xy={0.f,-0.f,1.f,-1.f,.5f/256.f,-.5f/256.f,1.5f/256.f,-1.5f/256.f,8388607.f,-8388607.f};
  std::vector<uint32_t> dep={0x00000000,0x30800000,0x307fffff,0x30800001,0x3fffffff,0x40000000,0x40800000,0xb0800000};
  for(float x:xy)for(auto inv:dep)for(unsigned source:{0u,3u,4u,5u,255u}){
   a[1]=(a[1]&0x00ffffff)|(source<<24);a[7]=bits(x);a[8]=bits(-x);a[11]=inv;a[12]=uint16_t(-32768)|(uint32_t(uint16_t(32767))<<16);t.run(a,reference(a));
  }
  // Every signed16 conversion, including zero and -32768, plus quarter/half
  // texel offsets and both filtering biases.
  for(unsigned v=0;v<65536;v++){a[7]=bits(1);a[8]=bits(-1);a[11]=bits(.25f);a[12]=v|((65535-v)<<16);a[4]=bits((v&3)*.25f);a[5]=bits((v&7)*.5f);
#ifdef INTEGER_UV
   a[4]=bits(float((v*541u&65535)*8u));a[5]=bits(float((v*379u&65535)*8u));
#endif
   a[6]=(v&1)?bits(.5f):0;t.run(a,reference(a));}
#ifdef INTEGER_UV
  for(int uv:{-32768,-32767,-1,0,1,32766,32767})for(unsigned tile:{0u,1u,4095u,4096u,65534u,65535u})for(unsigned bias:{0u,1u}){
   a[12]=uint16_t(uv)|(uint32_t(uint16_t(-1-uv))<<16);a[4]=bits(float(tile*8));a[5]=bits(float((65535-tile)*8));a[6]=bias?bits(.5f):0;t.run(a,reference(a));
  }
#endif
  auto good=a;
  for(unsigned which=0;which<7;which++){
   a=good;
   if(which==0)a[1]&=~0xff00u;
   if(which==1)a[1]=(a[1]&0x00ffffff)|0x07000000;
   if(which==2)a[1]=(a[1]&~0xf00000)|0x400000,a[1]&=~0x10000u;
   if(which==3)a[7]=0x7fc00000;
   if(which==4)a[8]=0x4f000000;
   if(which==5)a[11]=0x7fc00000;
   if(which==6){
#ifdef INTEGER_UV
    a[1]=(a[1]&~0xf00000)|0x700000;
#else
    a[4]=0x7f800000;
#endif
   }
   t.run(a,{},true);
  }
  std::ifstream native(argv[2],std::ios::binary);auto get=[&](){uint32_t v;native.read((char*)&v,4);need(bool(native),"short native input");return v;};
  unsigned native_jobs=get(),native_vertices=0;
  for(unsigned j=0;j<native_jobs;j++){
   uint32_t h[27];for(auto&v:h)v=get();
   for(unsigned v=0;v<h[0];v++){
    uint32_t record[15];for(auto&q:record)q=get();
    a=good;a[0]=v;for(unsigned k=0;k<8;k++)a[7+k]=record[6+k];
    for(unsigned source:{0u,3u,4u,5u,255u}){
     a[1]=0x00810301|(source<<24);a[4]=bits(8.f);a[5]=bits(-24.f);a[6]=bits(.5f);
#ifdef INTEGER_UV
     a[5]=bits(24.f);
#endif
     t.run(a,reference(a));
    }
    native_vertices++;
   }
  }
  need(native.peek()==std::ifstream::traits_type::eof(),"extra native bytes");
  t.run(good,reference(good),false,true);t.run(good,reference(good));
  std::cout<<"{\"passed\":true,\"worker_cases_available\":"<<record_count<<",\"worker_cases_checked\":"<<checked<<",\"native_vertices\":"<<native_vertices<<",\"native_packet_cases\":"<<native_vertices*5<<",\"total_jobs\":"<<t.jobs<<",\"error_jobs\":"<<t.errors<<",\"words\":"<<t.words<<",\"reset_aborted_words\":"<<t.aborted_words<<",\"backpressure_cycles\":"<<t.stalls<<",\"cycles\":"<<t.clocks<<",\"fabric_reset_tests\":1}\n";
 }catch(const std::exception&e){std::cerr<<e.what()<<"\n";return 1;}
}
