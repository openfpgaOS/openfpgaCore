#include "Vtb_axi_periph.h"
#include "verilated.h"
#include <cassert>
#include <cstdio>
#include <cstdint>
#include <fstream>
#include <iterator>
#include <vector>
#include <unordered_map>
static Vtb_axi_periph t;
static uint32_t rng=0x632be59b;
static uint32_t rnd(){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
static uint64_t ticks=0;
static bool allow_dma=true,rd_active=false,rd_held=false,inject_error=false;
static uint32_t rd_addr=0,rd_left=0,rd_error=0,bursts=0,words=0;
static std::unordered_map<uint32_t,uint32_t> memory;
struct Beat {bool ar,aw,w,b,r;uint32_t data;bool last;};
static Beat tick(){
 t.clk=0;
 t.m_arready=allow_dma&&!rd_active&&(rnd()%4!=0);
 t.m_rvalid=rd_active&&allow_dma&&(rd_held||rnd()%3!=0);
 t.m_rdata=rd_active?memory.at(rd_addr):0;
 t.m_rresp=rd_error;t.m_rlast=rd_left==1;t.eval();
 Beat b{bool(t.s_axi_arvalid&&t.s_axi_arready),bool(t.s_axi_awvalid&&t.s_axi_awready),bool(t.s_axi_wvalid&&t.s_axi_wready),bool(t.s_axi_bvalid&&t.s_axi_bready),bool(t.s_axi_rvalid&&t.s_axi_rready),t.s_axi_rdata,bool(t.s_axi_rlast)};
 if(b.b)assert(t.s_axi_bresp==0);if(b.r)assert(t.s_axi_rresp==0);
 bool ar=t.m_arvalid&&t.m_arready,r=t.m_rvalid&&t.m_rready;
 uint32_t addr=t.m_araddr,len=t.m_arlen;
 rd_held=t.m_rvalid&&!t.m_rready;
 t.clk=1;t.eval();ticks++;
 if(ar){assert(!rd_active);assert((addr&4095)+4*(len+1)<=4096);rd_active=true;rd_addr=addr;rd_left=len+1;rd_error=inject_error?2:0;inject_error=false;bursts++;}
 if(r){assert(rd_active);words++;rd_addr+=4;if(!--rd_left){rd_active=false;rd_held=false;}}
 return b;
}
static void wr(uint32_t a,const std::vector<uint32_t>&v,uint32_t strb=15,bool fixed=false){
 t.s_axi_awaddr=a;t.s_axi_awlen=v.size()-1;t.s_axi_awburst=fixed?0:1;t.s_axi_awvalid=1;
 t.s_axi_bready=0;size_t n=0;bool aw=false;
 for(int k=0;k<10000 && (!aw||n<v.size());k++){
  t.s_axi_wvalid=n<v.size();t.s_axi_wdata=v[n<v.size()?n:0];t.s_axi_wstrb=strb;t.s_axi_wlast=n+1==v.size();
  Beat b=tick();if(b.aw){aw=true;t.s_axi_awvalid=0;}if(b.w)n++;
 }
 assert(aw&&n==v.size());t.s_axi_wvalid=0;t.s_axi_bready=1;bool done=false;
 for(int k=0;k<10000&&!done;k++)done=tick().b;
 assert(done);t.s_axi_bready=0;
}
static void wr(uint32_t a,uint32_t d,uint32_t s=15){wr(a,std::vector<uint32_t>{d},s);}
static std::vector<uint32_t> rd(uint32_t a,int n=1){
 t.s_axi_araddr=a;t.s_axi_arlen=n-1;t.s_axi_arvalid=1;t.s_axi_rready=0;
 bool ar=false;for(int k=0;k<10000&&!ar;k++)ar=tick().ar;assert(ar);t.s_axi_arvalid=0;
 std::vector<uint32_t> v;
 for(int k=0;k<100000&&v.size()<unsigned(n);k++){
  t.s_axi_rready=rnd()%4!=0;Beat b=tick();
  if(b.r){assert(b.last==(v.size()+1==unsigned(n)));v.push_back(b.data);}
 }
 assert(v.size()==unsigned(n));t.s_axi_rready=0;return v;
}
static uint32_t reg(uint32_t off){return rd(0x4b000000+off)[0];}
static void put(uint32_t off,uint32_t v){wr(0x4b000000+off,v);}
struct Job {uint32_t h[27];std::vector<uint32_t> data;};
static std::vector<Job> jobs;
static uint32_t expected[8][32],masks[8][32],valid=0;
static void configure(const Job&j,unsigned dest){
 wr(0x4b000100,std::vector<uint32_t>(j.h+3,j.h+19));
 put(0xc,j.h[1]);put(0x10,j.h[0]|dest<<8);
 put(0x14,j.h[2]);put(0x18,j.h[19]);put(0x1c,j.h[20]);put(0x20,j.h[21]);
 put(0x24,j.h[22]);put(0x28,j.h[23]);put(0x2c,j.h[25]|j.h[26]<<16);put(0x30,j.h[24]);
 memory.clear();for(unsigned v=0;v<j.h[0];v++)for(unsigned k=0;k<6;k++)memory[j.h[1]+24*v+4*k]=j.data[v*15+k];
}
static void wait_done(){
 bool done=false;for(int k=0;k<20000&&!done;k++)done=(reg(4)&2)!=0;assert(done);
}
int main(int argc,char**argv){
 Verilated::commandArgs(argc,argv);assert(argc==2);
 std::ifstream f(argv[1],std::ios::binary);std::vector<char>b((std::istreambuf_iterator<char>(f)),{});
 assert(b.size()%4==0);const uint32_t*p=reinterpret_cast<const uint32_t*>(b.data());unsigned nj=*p++;assert(nj==20);
 for(unsigned i=0;i<nj;i++){Job j;for(auto&x:j.h)x=*p++;j.data.assign(p,p+15*j.h[0]);p+=15*j.h[0];jobs.push_back(j);}
 assert(reinterpret_cast<const char*>(p)==b.data()+b.size());
 t.reset_n=0;t.mixer_ready=1;
 for(int i=0;i<10;i++)tick();t.reset_n=1;for(int i=0;i<10;i++)tick();
 assert(reg(0)==0x4c4f4401);assert(reg(4)==0);assert(reg(0x400)==0);
 put(8,1);assert((reg(4)&0x86)==0x82);assert(bursts==0);put(8,2);
 unsigned checked=0,vertex_count=0;
 for(unsigned ji=0;ji<jobs.size();ji++){
  const auto&j=jobs[ji];unsigned n=j.h[0],dest=(ji*7)%(33-n);configure(j,dest);
  assert(reg(4)&0x100);
  // Partial and zero-byte writes never alter descriptors.
  wr(0x4b00000c,0,3);wr(0x4b00000c,0,0);assert(reg(0xc)==j.h[1]);
  if(ji==0){
   allow_dma=false;put(8,1);assert(reg(4)&1);
   put(0xc,0);put(0x10,0);put(0x14,0);put(0x18,0);put(0x1c,0);put(0x20,0);
   put(0x24,0);put(0x28,0);put(0x2c,0);put(0x30,0);
   wr(0x4b000100,std::vector<uint32_t>(16,0));put(8,3);
   assert(reg(0xc)==j.h[1]);assert(reg(0x10)==(n|dest<<8));assert(reg(0x400)==0);
   allow_dma=true;
  }else put(8,1);
  wait_done();uint32_t status=reg(4);assert((status&0xef)==6);
  for(unsigned v=0;v<n;v++){
   valid|=1u<<(dest+v);
   for(unsigned row=0;row<8;row++){
    expected[row][dest+v]=j.data[v*15+6+row];
    masks[row][dest+v]=(row==6&&!j.data[v*15+14])?0xff000000:0xffffffff;
   }
  }
  assert(reg(0x3c)==valid);
  // One burst traverses each complete RAM row with randomized CPU R stalls.
  for(unsigned row=0;row<8;row++){
   auto values=rd(0x4b000400+128*row,32);
   for(unsigned v=0;v<32;v++){
    uint32_t want=(valid>>v&1)?expected[row][v]:0,mask=(valid>>v&1)?masks[row][v]:0xffffffff;
    if((values[v]&mask)!=(want&mask)){std::fprintf(stderr,"job%u row%u slot%u got%08x want%08x mask%08x\n",ji,row,v,values[v],want,mask);return 1;}checked++;
   }
  }
  vertex_count+=n;
 }
 // Invalid range, then AXI response error: neither can publish success.
 configure(jobs[0],0);put(0x10,0);put(8,1);wait_done();assert((reg(4)&0x2e)==0x2a);assert(reg(0x400)==0);
 configure(jobs[0],0);inject_error=true;put(8,1);wait_done();assert(!(reg(4)&4));assert((reg(4)&0x60)==0x40);assert(reg(0x400)==0);
 // Recovery after failure, then fabric reset while a request is held.
 configure(jobs[0],0);put(8,1);wait_done();assert((reg(4)&6)==6);
 allow_dma=false;put(8,1);assert(reg(4)&1);t.reset_n=0;rd_active=rd_held=false;for(int i=0;i<10;i++)tick();
 t.reset_n=1;allow_dma=true;for(int i=0;i<10;i++)tick();assert(reg(4)==0);assert(reg(0x3c)==0);
 // Existing GPU decode remains distinct and synchronous read staging intact.
 assert(rd(0x4a000010)[0]==0x44444444);assert(rd(0x49000000)[0]==0x125);
 std::printf("{\"passed\":true,\"native_jobs\":20,\"native_vertices\":%u,\"result_words_checked\":%u,\"bursts\":%u,\"words\":%u,\"cycles\":%llu,\"busy_write_ignored\":true,\"partial_write_ignored\":true,\"matrix_burst\":true,\"result_bursts\":true,\"fault_and_reset_recovery\":true}\n",vertex_count,checked,bursts,words,(unsigned long long)ticks);
}
