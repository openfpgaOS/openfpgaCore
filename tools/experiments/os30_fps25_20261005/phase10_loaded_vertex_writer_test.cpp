#include "Vgeometry_loaded_vertex_writer.h"
#include "verilated.h"
#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iostream>
#include <random>
#include <stdexcept>
#include <vector>
static void need(bool b,const char*s){if(!b)throw std::runtime_error(s);}
static uint32_t bits(float f){uint32_t u;memcpy(&u,&f,4);return u;}
using Row=std::array<uint32_t,8>;
struct Test {
 Vgeometry_loaded_vertex_writer d;std::mt19937 rng{0x569724};std::array<Row,32> memory;
 std::vector<uint32_t> received;uint32_t base=0,address=0;unsigned beats=0,beat=0,bursts=0,delay=0;
 bool active=false,response=false,awheld=false,wheld=false;uint64_t awtag=0,wtag=0;
 unsigned error_burst=~0u,job_bresp=2,stall_aw=0,stall_w=0,stall_b=0;
 bool random_stalls=true;unsigned consecutive=0,max_consecutive=0;
 uint64_t clocks=0,jobs=0,words=0,total_bursts=0,error_jobs=0,invalid_jobs=0,reset_jobs=0;
 void tick(){
  d.m_awready=!active&&!response && stall_aw==0 && (!random_stalls || rng()%4!=0);
  d.m_wready=active && stall_w==0 && (!random_stalls || rng()%4!=0);
  d.m_bvalid=response && delay==0 && stall_b==0;d.m_bresp=bursts-1==error_burst?job_bresp:0;
  d.clk=0;d.eval();
  bool rd=d.raw_read_valid;unsigned ra=d.raw_read_addr;
  bool aw=d.m_awvalid&&d.m_awready,w=d.m_wvalid&&d.m_wready,b=d.m_bvalid&&d.m_bready;
  uint64_t at=(uint64_t(d.m_awaddr)<<8)|d.m_awlen;
  uint64_t wt=(uint64_t(d.m_wdata)<<5)|(d.m_wstrb<<1)|d.m_wlast;
  if(!d.reset){
   if(awheld)need(d.m_awvalid&&at==awtag,"AW changed while stalled");
   if(wheld)need(d.m_wvalid&&wt==wtag,"W changed while stalled");
   awheld=d.m_awvalid&&!d.m_awready;awtag=at;wheld=d.m_wvalid&&!d.m_wready;wtag=wt;
  }
  if(aw){
   need(!active&&!response,"more than one outstanding burst");address=d.m_awaddr;beats=d.m_awlen+1;beat=0;active=true;
   need(beats>=1&&beats<=16,"burst size");need((address&4095)+beats*4<=4096,"4KB burst crossing");
   need(address==base+received.size()*4,"noncontiguous DMA output");bursts++;total_bursts++;
  }
  if(w){
   need(active&&beat<beats,"W without accepted AW");need(d.m_wstrb==15,"partial strobe");
   need(bool(d.m_wlast)==(beat+1==beats),"incorrect WLAST");received.push_back(d.m_wdata);words++;beat++;
   consecutive++;max_consecutive=std::max(max_consecutive,consecutive);
   if(beat==beats){active=false;response=true;delay=random_stalls?rng()%6:0;}
  }else consecutive=0;
  if(b){need(response&&!active,"early B accepted");response=false;}
  if(delay&&!w)delay--;
  if(stall_aw)stall_aw--;if(stall_w)stall_w--;if(stall_b)stall_b--;
  d.clk=1;d.eval();if(rd)d.raw_read_data=memory[ra&31][ra>>5];d.eval();d.clk=0;d.eval();clocks++;
 }
 void reset(){
  d.reset=1;d.start=0;active=response=awheld=wheld=false;delay=0;stall_aw=stall_w=stall_b=0;
  tick();tick();d.reset=0;received.clear();tick();
 }
 static std::vector<uint32_t> expected(const std::vector<Row>&rows){
  std::vector<uint32_t> v;for(auto&r:rows){v.insert(v.end(),r.begin(),r.begin()+5);v.push_back(bits(float(int16_t(r[5]))));v.push_back(bits(float(int16_t(r[5]>>16))));v.push_back(r[6]);v.push_back(r[7]&255);}return v;
 }
 void run(const std::vector<Row>&rows,unsigned dest,uint32_t output,unsigned error_at=~0u,bool invalid=false,bool cancel=false){
  unsigned count=rows.size();base=output;received.clear();bursts=0;error_burst=error_at;
  active=response=awheld=wheld=false;consecutive=0;d.start=0;
  for(unsigned i=0;i<count&&i+dest<32;i++)memory[dest+i]=rows[i];
  d.output_base=output;d.count=count;d.dest=dest;d.start=1;tick();d.start=0;
  unsigned watchdog=0;
  while(!d.done){
   need(watchdog++<10000,"writer timeout");
   if(d.busy){d.start=rng()%7==0;d.output_base=rng();d.count=rng()&63;d.dest=rng()&31;}
   if(cancel&&active&&beat>=3){reset();reset_jobs++;return;}
   tick();
  }
  d.start=0;need(!d.busy&&!active&&!response,"completion before accepted burst/B drained");
  auto exp=expected(rows);
  if(invalid){need(d.error==1&&bursts==0&&received.empty(),"invalid descriptor issued IO");invalid_jobs++;}
  else if(error_at!=~0u){
   need(d.error==2&&bursts==error_at+1,"write response error not reported/stopped");
   need(received.size()==std::min(exp.size(),size_t((error_at+1)*16)),"error burst did not fully drain");error_jobs++;
  }else need(d.error==0&&received.size()==exp.size(),"missing completed words");
  for(unsigned i=0;i<received.size();i++)if(received[i]!=exp[i]){
   std::cerr<<"job "<<jobs<<" word "<<i<<" expected "<<std::hex<<exp[i]<<" got "<<received[i]<<std::dec<<"\n";throw std::runtime_error("LoadedVertex mismatch");
  }
  jobs++;tick();need(!d.done,"done not pulse");
 }
};
int main(int argc,char**argv){
 try{
  need(argc==2,"usage writer-test native.bin");Test t;t.reset();std::ifstream native(argv[1],std::ios::binary);
  auto get=[&](){uint32_t v;native.read((char*)&v,4);need(bool(native),"short native input");return v;};unsigned native_jobs=get(),native_vertices=0;
  for(unsigned job=0;job<native_jobs;job++){
   uint32_t h[27];for(auto&v:h)v=get();std::vector<Row> rows;
   for(unsigned v=0;v<h[0];v++){uint32_t r[15];for(auto&q:r)q=get();Row a;std::copy_n(r+6,8,a.begin());rows.push_back(a);native_vertices++;}
   unsigned dest=(job*7)%(33-rows.size());t.run(rows,dest,0x10000fc0+job*0x1000);
  }
  need(native.peek()==std::ifstream::traits_type::eof(),"extra native input");
  // Every signed16 input reaches both float encodings, in opposite order.
  for(unsigned start=0;start<65536;start+=32){
   std::vector<Row> rows(32);for(unsigned i=0;i<32;i++){auto&r=rows[i];for(auto&v:r)v=t.rng();r[5]=(start+i)|((65535-start-i)<<16);}
   t.run(rows,0,0x10100fc0+(start/32%200)*0x1000);
  }
  for(unsigned j=0;j<1500;j++){
   unsigned n=1+t.rng()%32,dest=t.rng()%(33-n);std::vector<Row> rows(n);for(auto&r:rows)for(auto&v:r)v=t.rng();
   uint32_t base=0x10200000+(t.rng()&0x3ffff)*64;
   t.run(rows,dest,base);
  }
  std::vector<Row> full(32);for(auto&r:full)for(auto&v:r)v=t.rng();
  for(unsigned burst:{0u,1u,8u,17u})for(unsigned response:{1u,2u,3u}){t.job_bresp=response;t.run(full,0,0x10000fc0,burst);}
  std::vector<Row> one(1);one[0]=full[0];
  t.run(one,31,0x13ffffc0);
  t.run({},0,0x10000000,~0u,true);t.run(std::vector<Row>(33),0,0x10000000,~0u,true);
  t.run(std::vector<Row>(63),0,0x10000000,~0u,true);t.run(std::vector<Row>(2),31,0x10000000,~0u,true);
  for(uint32_t base:{0u,0x0fffffc0u,0x10000001u,0x10000020u,0x14000000u,0xffffffc0u})t.run(one,0,base,~0u,true);
  t.run(std::vector<Row>(2),0,0x13ffffc0,~0u,true);
  // Long independent stalls, then a fabric reset while an accepted burst is active.
  t.stall_aw=200;t.stall_w=400;t.stall_b=600;t.run(full,0,0x10000fc0);
  t.run(full,0,0x10000fc0,~0u,false,true);t.run(full,0,0x10000fc0);
  t.random_stalls=false;t.max_consecutive=0;t.run(full,0,0x10000fc0);need(t.max_consecutive==16,"writer cannot sustain one beat per cycle");
  std::cout<<"{\"passed\":true,\"native_jobs\":"<<native_jobs<<",\"native_vertices\":"<<native_vertices<<",\"all_signed16_values\":65536,\"jobs\":"<<t.jobs<<",\"words\":"<<t.words<<",\"bursts\":"<<t.total_bursts<<",\"response_error_jobs\":"<<t.error_jobs<<",\"invalid_jobs\":"<<t.invalid_jobs<<",\"fabric_resets\":"<<t.reset_jobs<<",\"max_consecutive_W\":"<<t.max_consecutive<<",\"cycles\":"<<t.clocks<<"}\n";
 }catch(const std::exception&e){std::cerr<<e.what()<<"\n";return 1;}
}
