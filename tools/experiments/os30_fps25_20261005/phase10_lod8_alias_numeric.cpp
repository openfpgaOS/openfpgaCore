#include "Vgeometry_bram_alias.h"
#include "verilated.h"
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <stdexcept>

static Vgeometry_bram_alias t;
static uint32_t raw[256], q;
static uint64_t cycles, words, stalled, consecutive, longest;
static uint32_t rng=0x72065bc7;
static bool random_stalls=true, hold=false;
static uint32_t hold_data,hold_resp;static bool hold_last;
static void need(bool v,const char*m){if(!v)throw std::runtime_error(m);}
static uint32_t rnd(){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
struct Beat{bool ar,r,last;uint32_t data,resp;};
static Beat tick(){
 t.clk=0;t.view_read_data=q;t.eval();
 if(hold)need(t.s_rvalid&&t.s_rdata==hold_data&&t.s_rresp==hold_resp&&t.s_rlast==hold_last,"stalled data changed");
 Beat b{bool(t.s_arvalid&&t.s_arready),bool(t.s_rvalid&&t.s_rready),bool(t.s_rlast),t.s_rdata,t.s_rresp};
 hold=t.s_rvalid&&!t.s_rready;hold_data=t.s_rdata;hold_resp=t.s_rresp;hold_last=t.s_rlast;
 if(hold)stalled++;
 if(b.r){words++;consecutive++;if(consecutive>longest)longest=consecutive;}else consecutive=0;
 unsigned addr=t.view_read_addr;
 t.clk=1;t.eval();q=raw[addr];t.view_read_data=q;t.eval();cycles++;
 return b;
}
static uint32_t fp16(uint16_t v){float f=float(int16_t(v));uint32_t u;std::memcpy(&u,&f,4);return u;}
static uint32_t want(unsigned word){
 unsigned slot=word/9,field=word%9;
 if(slot>=32||!(t.view_valid_slots&(1u<<slot)))return 0;
 if(field<5)return raw[field*32+slot];
 if(field==5)return fp16(raw[5*32+slot]);
 if(field==6)return fp16(raw[5*32+slot]>>16);
 if(field==7)return raw[6*32+slot];
 return raw[7*32+slot]&255;
}
static void read(unsigned first,unsigned n,bool uncached=false){
 t.s_araddr=(uncached?0x53f80000:0x13f80000)+first*4;t.s_arlen=n-1;t.s_arvalid=1;t.s_rready=0;
 bool accepted=false;for(unsigned i=0;i<100&&!accepted;i++)accepted=tick().ar;
 need(accepted,"AR timeout");t.s_arvalid=0;unsigned got=0;
 for(unsigned i=0;i<2000&&got<n;i++){
  t.s_rready=!random_stalls||rnd()%4;auto b=tick();if(!b.r)continue;
  bool error=first+n>512;need(b.resp==(error?2:0),"response");need(b.last==(got+1==n),"last");
  if(b.data!=(error?0:want(first+got))){std::fprintf(stderr,"word%u got%08x want%08x\n",first+got,b.data,want(first+got));throw std::runtime_error("field mapping");}got++;
 }
 need(got==n,"read timeout");t.s_rready=0;
}
int main(int argc,char**argv){Verilated::commandArgs(argc,argv);try{
 t.reset=1;for(int i=0;i<3;i++)tick();t.reset=0;t.view_valid_slots=~0u;
 // Every signed16 value is consumed from both halves through the actual
 // synchronous alias pipeline, not merely a duplicated conversion formula.
 for(unsigned batch=0;batch<2048;batch++){
  for(unsigned slot=0;slot<32;slot++){
   uint32_t u=batch*32+slot;for(unsigned field=0;field<8;field++)raw[field*32+slot]=rnd();
   raw[5*32+slot]=u|((u^65535u)<<16);
  }
  read(0,144,batch&1);read(144,144,batch&1);
 }
 // Every starting index, all quotient/remainder values and validity bit
 // positions. Exercise cacheline edges and unused aperture padding.
 for(unsigned first=0;first<512;first++){
  t.view_valid_slots=rnd();read(first,1);read(first,16,first&1);
 }
 t.view_valid_slots=~0u;random_stalls=false;read(0,256);read(256,256);
 need(longest>=256,"cannot sustain one word per cycle");
 std::printf("{\"passed\":true,\"signed16_values_both_halves\":65536,\"all_start_indices\":512,\"alias_words_checked\":%llu,\"stalled_cycles\":%llu,\"longest_consecutive_R_beats\":%llu,\"cycles\":%llu}\n",(unsigned long long)words,(unsigned long long)stalled,(unsigned long long)longest,(unsigned long long)cycles);
 }catch(const std::exception&e){std::fprintf(stderr,"FAIL %s cycle%llu\n",e.what(),(unsigned long long)cycles);return 1;}}
