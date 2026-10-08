#include "Vtb_axi_periph.h"
#include "verilated.h"
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <vector>
static Vtb_axi_periph t;
static uint32_t random_state=0xb365f28a;
static uint32_t rnd(){random_state^=random_state<<13;random_state^=random_state>>17;random_state^=random_state<<5;return random_state;}
static uint64_t cycles=0, cpu_words=0, geometry_words=0, commits=0, aborts=0, overlap=0, blocked_cycles=0;
static uint32_t gold[4096]={},app=0,pub=0,left=0;
static bool owner=false;
struct Beat{bool ar,aw,w,b,r,grant,word;uint32_t data;};
static Beat tick(){
 t.clk=0;t.eval();
 Beat b{bool(t.s_axi_arvalid&&t.s_axi_arready),bool(t.s_axi_awvalid&&t.s_axi_awready),bool(t.s_axi_wvalid&&t.s_axi_wready),bool(t.s_axi_bvalid&&t.s_axi_bready),bool(t.s_axi_rvalid&&t.s_axi_rready),bool(t.geo_request&&t.geo_grant),bool(t.geo_valid&&t.geo_ready),t.s_axi_rdata};
 bool done=false;uint32_t before_read=t.read_ptr,old_app=app;bool wrote=false;
 if(!t.reset_n){app=pub=left=0;owner=false;}
 else {
  if(t.dbg_gpu_reg_wr){
   assert(!owner);
   if(t.dbg_gpu_reg_addr==1){assert(((app+1)&4095)!=t.read_ptr);gold[app]=t.dbg_gpu_reg_wdata;app=(app+1)&4095;cpu_words++;wrote=true;}
   if(t.dbg_gpu_reg_addr==0){if(t.dbg_gpu_reg_wdata&8)pub=app;if(t.dbg_gpu_reg_wdata&4)app=pub=0;}
  }
  if(owner&&t.gpu_write_pending){blocked_cycles++;assert(!t.dbg_gpu_reg_wr);}
  if(b.grant){assert(!owner&&!t.gpu_write_pending&&!t.dbg_gpu_reg_wr&&app==pub);assert(t.geo_words&&t.geo_words<4096);owner=true;left=t.geo_words;}
  else if(owner&&t.geo_abort){assert(!b.word);app=pub;owner=false;left=0;aborts++;}
  else if(b.word){assert(owner&&left);gold[app]=t.geo_data;app=(app+1)&4095;geometry_words++;wrote=true;if(!--left){pub=app;owner=false;done=true;commits++;}}
 }
 t.clk=1;t.eval();cycles++;
 assert(t.append_ptr==app&&t.publish_ptr==pub&&bool(t.geo_owned)==owner&&bool(t.geo_done)==done);
 if(wrote){t.peek_addr=old_app;t.eval();assert(t.peek_data==gold[old_app]);}
 if(owner&&t.read_ptr!=before_read)overlap++;
 if(b.b)assert(t.s_axi_bresp==0);if(b.r)assert(t.s_axi_rresp==0);
 return b;
}
static void idle(unsigned n){while(n--)tick();}
static void write(uint32_t off,const std::vector<uint32_t>&v,bool delayed=false){
 t.s_axi_awaddr=0x4a000000+off;t.s_axi_awlen=v.size()-1;t.s_axi_awburst=0;t.s_axi_awvalid=1;t.s_axi_bready=0;
 bool aw=false;unsigned n=0;
 for(unsigned k=0;k<100000&&(!aw||n<v.size());k++){
  t.s_axi_wvalid=n<v.size()&&(!delayed||k>8);t.s_axi_wdata=v[n<v.size()?n:0];t.s_axi_wstrb=15;t.s_axi_wlast=n+1==v.size();
  auto b=tick();if(b.aw){aw=true;t.s_axi_awvalid=0;}if(b.w)n++;
 }
 assert(aw&&n==v.size());t.s_axi_wvalid=0;t.s_axi_bready=1;bool done=false;for(unsigned k=0;k<100000&&!done;k++)done=tick().b;assert(done);t.s_axi_bready=0;
}
static void write(uint32_t off,uint32_t v,bool delay=false){write(off,std::vector<uint32_t>{v},delay);}
static uint32_t read(uint32_t off){
 t.s_axi_araddr=0x4a000000+off;t.s_axi_arlen=0;t.s_axi_arvalid=1;t.s_axi_rready=0;bool ar=false;
 for(unsigned k=0;k<10000&&!ar;k++)ar=tick().ar;assert(ar);t.s_axi_arvalid=0;t.s_axi_rready=1;
 for(unsigned k=0;k<10000;k++){auto b=tick();if(b.r){t.s_axi_rready=0;return b.data;}}assert(false);return 0;
}
static void claim(unsigned words){
 t.geo_words=words;t.geo_request=1;bool grant=false;for(unsigned k=0;k<100000&&!grant;k++)grant=tick().grant;assert(grant);t.geo_request=0;assert(t.geo_owned);
}
static void send(const std::vector<uint32_t>&v,bool stalls=true){
 for(auto x:v){t.geo_data=x;t.geo_valid=1;if(stalls){t.geo_valid=0;idle(rnd()%5);t.geo_valid=1;}assert(tick().word);}t.geo_valid=0;
}
static void complete_fence(uint32_t x){for(unsigned k=0;k<200000&&t.fence_reached!=x;k++)tick();assert(t.fence_reached==x);}
static void reset(){t.reset_n=0;t.geo_request=t.geo_valid=t.geo_abort=0;t.s_axi_awvalid=t.s_axi_wvalid=t.s_axi_arvalid=t.s_axi_bready=t.s_axi_rready=0;idle(5);t.reset_n=1;idle(5);write(0,0x10);}
int main(int argc,char**argv){
 Verilated::commandArgs(argc,argv);t.mixer_ready=1;t.allow_barrier=1;reset();
 // Invalid reservations never grant, including 4096-word full-ring ambiguity.
 for(auto n:{0u,4096u,8191u}){t.geo_words=n;t.geo_request=1;for(int i=0;i<8;i++)assert(!tick().grant);}t.geo_request=0;
 // Unpublished CPU state holds claim until an unconditional CPU publication.
 write(4,0xfe000000);t.geo_request=1;t.geo_words=2;for(int i=0;i<20;i++)assert(!tick().grant);
 write(0,8);bool got=t.geo_owned;for(int i=0;i<100&&!got;i++)got=tick().grant;assert(got);t.geo_request=0;
 send({0x02000001,1});complete_fence(1);assert(read(4)==pub*4);
 // Same-cycle AW/claim and split AW-before-W: pending CPU transaction wins.
 for(bool delay:{false,true}){
  t.geo_request=1;t.geo_words=2;write(4,0xfe000000,delay);
  assert(!owner);write(0,8);got=owner;for(int i=0;i<100&&!got;i++)got=tick().grant;assert(got);t.geo_request=0;
  send({0x02000001,uint32_t(2+delay)});complete_fence(2+delay);
 }
 // Atomic visibility: neither header nor fence payload is decoded early.
 uint32_t oldf=t.fence_reached;claim(2);send({0x02000001});idle(100);assert(t.fence_reached==oldf&&owner);
 assert(read(4)==pub*4);assert(read(0x10)==t.read_ptr*4);send({4});complete_fence(4);
 // A CPU write accepted during ownership must remain queued, then occur once.
 claim(2);t.s_axi_awaddr=0x4a000004;t.s_axi_awlen=0;t.s_axi_awburst=0;t.s_axi_awvalid=1;t.s_axi_wvalid=1;t.s_axi_wdata=0xfe000000;t.s_axi_wstrb=15;t.s_axi_wlast=1;t.s_axi_bready=0;
 bool aw=false,w=false;for(int i=0;i<30&&(!aw||!w);i++){auto b=tick();if(b.aw){aw=true;t.s_axi_awvalid=0;}if(b.w){w=true;t.s_axi_wvalid=0;}}assert(aw&&w);
 uint64_t cw=cpu_words;idle(40);assert(cpu_words==cw&&!t.s_axi_bvalid);send({0x02000001,5});
 t.s_axi_bready=1;bool bd=false;for(int i=0;i<100&&!bd;i++)bd=tick().b;assert(bd&&cpu_words==cw+1);t.s_axi_bready=0;write(0,8);complete_fence(5);
 // Abort overrides valid, including final word; no publication or done pulse.
 for(unsigned n:{1u,2u,32u,416u}){uint32_t before=pub;claim(n);if(n>1)send(std::vector<uint32_t>(n-1,0xfe000000));t.geo_valid=1;t.geo_data=0xfe000000;t.geo_abort=1;assert(!tick().word);t.geo_abort=t.geo_valid=0;assert(!owner&&app==before&&pub==before&&!t.geo_done);}
 // Ring-full reservation waits while decoder is held at a real fence barrier.
 t.allow_barrier=0;write(4,std::vector<uint32_t>{0x02000001,6});write(0,8);idle(30);assert(t.read_ptr==pub);
 claim(4095);send(std::vector<uint32_t>(4095,0xfe000000),false);t.geo_words=2;t.geo_request=1;for(int i=0;i<100;i++)assert(!tick().grant);
 t.allow_barrier=1;got=false;for(int i=0;i<1000&&!got;i++)got=tick().grant;assert(got);t.geo_request=0;send({0x02000001,7});complete_fence(7);
 // Random complete batches repeatedly wrap ring; GPU consumes prior work while
 // geometry pauses between words. Data contents checked independently per beat.
 for(unsigned job=0;job<1500;job++){
  unsigned n=2+rnd()%415;std::vector<uint32_t> v(n,0xfe000000);v[n-2]=0x02000001;v[n-1]=100+job;
  if(job%7==0){write(4,std::vector<uint32_t>(1+rnd()%32,0xfe000000));write(0,8);}
  claim(n);send(v);if(job%13==0)complete_fence(100+job);
 }
 complete_fence(1599);assert(overlap>100&&blocked_cycles>=40);
 // Every GPU MMIO register, including reset/publish and upload ports, uses
 // the same interlock. Exercise all sixteen addresses while owned.
 for(unsigned reg=0;reg<16;reg++){
  reset();claim(2);send({0x02000001});
  t.s_axi_awaddr=0x4a000000+4*reg;t.s_axi_awlen=0;t.s_axi_awburst=0;t.s_axi_awvalid=1;
  t.s_axi_wvalid=1;t.s_axi_wdata=reg==0?4:0xfe000000;t.s_axi_wstrb=15;t.s_axi_wlast=1;t.s_axi_bready=0;
  bool aa=false,ww=false;for(int i=0;i<30&&(!aa||!ww);i++){auto b=tick();if(b.aw){aa=true;t.s_axi_awvalid=0;}if(b.w){ww=true;t.s_axi_wvalid=0;}}assert(aa&&ww);
  idle(20);assert(!t.s_axi_bvalid&&owner);send({0xbeef});t.s_axi_bready=1;bool bb=false;
  for(int i=0;i<100&&!bb;i++)bb=tick().b;assert(bb);t.s_axi_bready=0;
 }
 reset();
 // Common fabric reset discards a private partial batch; no ghost publication.
 claim(20);send(std::vector<uint32_t>(11,0xfe000000));reset();claim(2);send({0x02000001,0xcafe});complete_fence(0xcafe);
 std::printf("{\"passed\":true,\"cycles\":%llu,\"cpu_words\":%llu,\"geometry_words\":%llu,\"commits\":%llu,\"aborts\":%llu,\"concurrent_consumer_steps\":%llu,\"cpu_blocked_cycles\":%llu,\"atomic_visibility\":true,\"posted_aw_transition\":true,\"split_aw_w_transition\":true,\"all_gpu_writes_backpressure\":true,\"gpu_mmio_registers_blocked\":16,\"wrap_and_full\":true,\"status_reads\":true,\"fabric_reset\":true}\n",(unsigned long long)cycles,(unsigned long long)cpu_words,(unsigned long long)geometry_words,(unsigned long long)commits,(unsigned long long)aborts,(unsigned long long)overlap,(unsigned long long)blocked_cycles);
}
