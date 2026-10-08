#include "Vraw_vtx_dma.h"
#include "verilated.h"
#include <algorithm>
#include <cstdint>
#include <iostream>
#include <random>
#include <stdexcept>
#include <vector>
#ifdef VARIABLE_WORDS_TEST
static constexpr unsigned item_words=1,max_items=192;
#else
static constexpr unsigned item_words=6,max_items=32;
#endif
static uint32_t value(uint32_t a){return (a*0x9e3779b9u)^(a>>7)^0x6d643331u;}
static void require(bool b,const char*s){if(!b)throw std::runtime_error(s);}
int main(int argc,char**argv){
 Verilated::commandArgs(argc,argv);Vraw_vtx_dma d;std::mt19937 rng(0x64767478);
 auto edge=[&](){d.clk=0;d.eval();d.clk=1;d.eval();};
 auto reset=[&](){d.reset=1;d.start=d.m_arready=d.m_rvalid=d.word_ready=0;for(int i=0;i<3;i++)edge();d.reset=0;edge();};
 reset();unsigned tests=0,bursts=0,words=0,error_jobs=0,invalid_jobs=0,reset_jobs=0;
 uint64_t cycles=0;
 auto job=[&](uint32_t base,unsigned count,int fault,int fault_burst,int fault_beat,bool abort_reset){
  require(!d.busy,"not idle at start");d.job_base=base;d.job_count=count;d.job_word_count=count;d.start=1;d.m_rvalid=0;d.m_arready=0;d.word_ready=0;edge();d.start=0;
  const uint64_t end=uint64_t(base)+count*item_words*4;
  bool invalid=count<1||count>max_items||(base&3)||base<0x10000000u||base>=0x14000000u||end>0x14000000u;
  if(invalid){require(d.done&&d.error==1&&!d.busy&&!d.m_arvalid&&!d.word_valid,"invalid descriptor issued work");invalid_jobs++;tests++;edge();return;}
  require(d.busy&&!d.done&&d.error==0,"valid start not accepted");
  bool active=false,rv=false,ar_stall=false,out_stall=false;uint32_t ar_addr=0,stalled_ar=0,stalled_word=0;unsigned ar_n=0,actual_n=0,beat=0,job_bursts=0,next_addr=base,received=0,stalled_len=0,stalled_index=0;
  bool fault_used=false,finished=false;unsigned idle_error_hold=0;
  for(unsigned cycle=0;cycle<30000;cycle++){
   cycles++;d.clk=0;
   // Changing config and asserting start while busy must never change ownership.
   d.start=(rng()%5)==0;d.job_base=rng();d.job_count=rng()%64;d.job_word_count=rng()%256;
   d.m_arready=rng()%4!=0;d.word_ready=rng()%4!=0;
   if(fault_used && idle_error_hold<35){d.word_ready=0;idle_error_hold++;}
   if(!rv && active && rng()%4!=0){
    rv=true;d.m_rdata=value(ar_addr+beat*4);d.m_rresp=0;
    if(fault==1 && int(job_bursts)==fault_burst && int(beat)==fault_beat){d.m_rresp=2;}
    d.m_rlast=beat+1==actual_n;
   }
   d.m_rvalid=rv;d.eval();
   if(ar_stall)require(d.m_arvalid&&d.m_araddr==stalled_ar&&d.m_arlen==stalled_len,"AR changed under stall");
   if(out_stall)require(d.word_valid&&d.word_data==stalled_word&&d.word_index==stalled_index,"output changed under stall");
   ar_stall=d.m_arvalid&&!d.m_arready;stalled_ar=d.m_araddr;stalled_len=d.m_arlen;
   out_stall=d.word_valid&&!d.word_ready;stalled_word=d.word_data;stalled_index=d.word_index;
   if(d.word_valid&&d.word_ready){
    require(d.word_index==received,"output indices not consecutive");
    require(d.word_data==value(base+4*received),"output data wrong");received++;words++;
   }
   const bool ar=d.m_arvalid&&d.m_arready,rr=d.m_rvalid&&d.m_rready;
   if(ar){
    require(!active,"more than one outstanding burst");require(!fault_used,"AR issued after accepted error");
    ar_addr=d.m_araddr;ar_n=d.m_arlen+1;require(ar_addr==next_addr,"address did not follow frozen job");
    require(ar_n>=1&&ar_n<=16,"illegal burst length");require((ar_addr&4095)+ar_n*4<=4096,"4KB crossed");
    require(uint64_t(ar_addr)+ar_n*4<=end,"read beyond exact source bytes");
    unsigned expect=std::min({16u,(4096-(ar_addr&4095))/4,(unsigned)((end-ar_addr)/4)});
    require(ar_n==expect,"burst splitting mismatch");
    next_addr+=ar_n*4;job_bursts++;bursts++;actual_n=ar_n;
    if(fault==2 && int(job_bursts)==fault_burst)actual_n=ar_n>1?ar_n-1:ar_n+1;
    if(fault==3 && int(job_bursts)==fault_burst)actual_n=ar_n+3;
    active=true;beat=0;
   }
   if(rr){
    require(active,"unsolicited response accepted");
    if(d.m_rresp || (bool(d.m_rlast)!=(beat+1==ar_n)))fault_used=true;
    beat++;rv=false;if(d.m_rlast){require(beat==actual_n,"RLAST source test bug");active=false;}
   }
   if(abort_reset && cycle==9){
    // Only global fabric reset may abandon a transaction: reset responder too.
    reset();active=false;rv=false;reset_jobs++;finished=true;break;
   }
   edge();
   if(d.done){
    require(!d.busy&&!d.word_valid&&!d.m_arvalid,"done before output retirement");
    require(!active&&!rv,"accepted burst was not drained");
    if(fault_used){require(d.error==(fault==1?2:3),"wrong error class");error_jobs++;}
    else {require(d.error==0&&received==count*item_words&&next_addr==end,"successful output incomplete");}
    finished=true;break;
   }
  }
  require(finished,"job timeout");d.start=0;d.m_rvalid=0;edge();tests++;
 };
 try{
  for(unsigned n=1;n<=max_items;n++)for(uint32_t off:{0u,4u,60u,4080u,4084u,4088u,4092u})job(0x10000000+off,n,0,0,0,false);
  for(unsigned n=1;n<=max_items;n++)job(0x14000000-n*item_words*4,n,0,0,0,false);
  for(unsigned i=0;i<256;i++)job(0x10000000+(rng()%(0x1000000-192))*4,1+rng()%max_items,0,0,0,false);
  for(unsigned n:{1u,3u,16u,32u})for(int b:{1,2})for(int beat:{0,2,5})if(n*item_words>unsigned((b-1)*16+beat))job(0x10000100,n,1,b,beat,false);
  for(int f:{2,3})for(unsigned n:{1u,3u,32u})job(0x10000ffcu,n,f,1,0,false);
  for(uint32_t base:{0u,0x0ffffffcu,0x10000001u,0x10000002u,0x10000003u,0x13fffffcu,0x14000000u,0xfffffffcu})job(base,32,0,0,0,false);
  for(unsigned n:{0u,max_items+1u,255u})job(0x10000000,n,0,0,0,false);
  job(0x10000ffcu,32,0,0,0,true);job(0x10001000u,32,0,0,0,false);
  std::cout<<"{\"passed\":true,\"tests\":"<<tests<<",\"bursts\":"<<bursts<<",\"delivered_words\":"<<words<<",\"error_jobs_drained\":"<<error_jobs<<",\"invalid_jobs_rejected\":"<<invalid_jobs<<",\"fabric_resets\":"<<reset_jobs<<",\"cycles\":"<<cycles<<"}\n";
 }catch(const std::exception&e){std::cerr<<"test "<<tests<<": "<<e.what()<<"\n";return 1;}
}
