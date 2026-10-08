#include "Vtb_axi_periph.h"
#include "verilated.h"
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <vector>
static Vtb_axi_periph t;
static uint64_t cycles=0,words=0,fences=0;
struct Beat{bool ar,aw,w,b,r;uint32_t data;};
static Beat tick(){
 t.clk=0;t.eval();
 Beat b{bool(t.s_axi_arvalid&&t.s_axi_arready),bool(t.s_axi_awvalid&&t.s_axi_awready),
        bool(t.s_axi_wvalid&&t.s_axi_wready),bool(t.s_axi_bvalid&&t.s_axi_bready),
        bool(t.s_axi_rvalid&&t.s_axi_rready),t.s_axi_rdata};
 if(t.reset_n&&t.dbg_gpu_reg_wr&&t.dbg_gpu_reg_addr==1)words++;
 t.clk=1;t.eval();cycles++;
 if(b.b)assert(t.s_axi_bresp==0);if(b.r)assert(t.s_axi_rresp==0);
 return b;
}
static void idle(unsigned n){while(n--)tick();}
static void write(uint32_t off,const std::vector<uint32_t>&v){
 assert(!v.empty()&&v.size()<=256);
 t.s_axi_awaddr=0x4a000000+off;t.s_axi_awlen=v.size()-1;t.s_axi_awburst=0;t.s_axi_awvalid=1;t.s_axi_bready=0;
 bool aw=false;unsigned n=0;
 for(unsigned k=0;k<100000&&(!aw||n<v.size());k++){
  t.s_axi_wvalid=n<v.size();t.s_axi_wdata=v[n<v.size()?n:0];t.s_axi_wstrb=15;t.s_axi_wlast=n+1==v.size();
  auto b=tick();if(b.aw){aw=true;t.s_axi_awvalid=0;}if(b.w)n++;
 }
 assert(aw&&n==v.size());t.s_axi_wvalid=0;t.s_axi_bready=1;
 bool done=false;for(unsigned k=0;k<100000&&!done;k++)done=tick().b;assert(done);t.s_axi_bready=0;
}
static void write(uint32_t off,uint32_t v){write(off,std::vector<uint32_t>{v});}
static uint32_t read(uint32_t off){
 t.s_axi_araddr=0x4a000000+off;t.s_axi_arlen=0;t.s_axi_arvalid=1;t.s_axi_rready=0;
 bool ar=false;for(unsigned k=0;k<10000&&!ar;k++)ar=tick().ar;assert(ar);
 t.s_axi_arvalid=0;t.s_axi_rready=1;
 for(unsigned k=0;k<10000;k++){auto b=tick();if(b.r){t.s_axi_rready=0;return b.data;}}
 assert(false);return 0;
}
static void fence(uint32_t id){
 for(unsigned k=0;k<1000000&&t.fence_reached!=id;k++)tick();
 assert(t.fence_reached==id);fences++;
 for(unsigned k=0;k<10000&&(read(0x14)&1);k++)tick();
 assert(!(read(0x14)&1));
}
static void push(const std::vector<uint32_t>&v){
 for(size_t pos=0;pos<v.size();){
  size_t count=std::min<size_t>(128,v.size()-pos);
  for(unsigned k=0;;k++){
   assert(k<10000);unsigned used=((read(4)/4)-(read(0x10)/4))&4095;
   if(4095-used>=count)break;idle(10);
  }
  write(4,std::vector<uint32_t>(v.begin()+pos,v.begin()+pos+count));write(0,8);pos+=count;
 }
}
static void reset(){
 t.reset_n=0;t.s_axi_awvalid=t.s_axi_wvalid=t.s_axi_arvalid=t.s_axi_bready=t.s_axi_rready=0;
 t.gpu_enable=1;t.allow_barrier=1;idle(5);t.reset_n=1;idle(5);write(0,0x10);
 uint32_t status=read(0x14);assert((status&0x3000)==0x1000);assert(!(status&0x800));
}
int main(int argc,char**argv){
 Verilated::commandArgs(argc,argv);t.mixer_ready=1;reset();
 // First publication after idle/reset is the critical synchronous RAM case.
 for(unsigned i=1;i<=80;i++){
  idle(30);push({0xfe000000});idle(25);push({0x02000001,i});fence(i);
 }
 // Complete headers can be published before their legacy payload arrives.
 push({0x02000001});idle(100);assert(t.fence_reached==80);
 push({81});fence(81);
 // A legacy command longer than the entire input ring streams through the
 // 64-word output FIFO; payload boundaries may cross any CPU publication.
 std::vector<uint32_t> huge{0xff001fff};
 for(unsigned i=0;i<8191;i++)huge.push_back(0x02000001u^i);
 huge.push_back(0x02000001);huge.push_back(82);push(huge);fence(82);
 // Producer and consumer pointers wrap independently.
 for(unsigned batch=0;batch<50;batch++){
  std::vector<uint32_t> v(511,0xfe000000);v.push_back(0x02000001);v.push_back(100+batch);
  push(v);fence(100+batch);
 }
 // Enable may pause a partly consumed legacy command and resume safely.
 push({0xff000009,1,2});idle(40);t.gpu_enable=0;idle(80);
 assert(t.fence_reached==149);t.gpu_enable=1;
 push({3,4,5,6,7,8,9,0x02000001,150});fence(150);
 // Soft reset flushes worker/output pointers even while old work is queued
 // behind a real GPU fence barrier. New headers must begin at output zero.
 t.allow_barrier=0;push({0x02000001,151,0xff00000a,1,2,3});idle(100);
 write(0,2);idle(10);t.allow_barrier=1;push({0x02000001,152});fence(152);
 // Malformed private command faults before a following fence can escape.
 push({0x62000001,0});idle(300);assert(read(0x14)&0x2000);
 push({0x02000001,153});idle(100);assert(t.fence_reached!=153);
 write(0,2);idle(10);assert(!(read(0x14)&0x2000));push({0x02000001,154});fence(154);
 // Ring reset and fabric reset each recover coherent input/output pointers.
 push({0x62000001,0});idle(300);assert(read(0x14)&0x2000);
 write(0,4);idle(10);write(0,0x10);push({0x02000001,155});fence(155);
 reset();push({0x02000001,156});fence(156);
 std::printf("{\"passed\":true,\"scope\":\"Actual AXI peripheral, raw worker transport and retained GPU parser/fence; no raster or numeric qualification\",\"cycles\":%llu,\"words\":%llu,\"fences\":%llu,\"empty_publication\":true,\"long_legacy_stream\":true,\"partial_payload_pause\":true,\"fault_containment\":true,\"soft_ring_fabric_reset\":true}\n",(unsigned long long)cycles,(unsigned long long)words,(unsigned long long)fences);
}
