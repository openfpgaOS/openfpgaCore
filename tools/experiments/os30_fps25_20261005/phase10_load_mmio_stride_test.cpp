#include "Vtb_axi_periph.h"
#include "verilated.h"
#include <cassert>
#include <cstdio>
#include <cstdint>
#include <cstring>
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
static bool allow_aw=true,allow_w=true,wr_active=false,b_pending=false,inject_b_error=false,hold_final_b=false,b_held=false;
static uint32_t wr_addr=0,wr_left=0,b_error=0,write_bursts=0,write_words=0,job_written=0,job_want=0;
static std::unordered_map<uint32_t,uint32_t> memory,output;
struct Beat {bool ar,aw,w,b,r;uint32_t data;bool last;};
static Beat tick(){
 t.clk=0;
 t.m_arready=allow_dma&&!rd_active&&(rnd()%4!=0);
 t.m_rvalid=rd_active&&allow_dma&&(rd_held||rnd()%3!=0);
 t.m_rdata=rd_active?memory.at(rd_addr):0;t.m_rresp=rd_error;t.m_rlast=rd_left==1;
 t.m_awready=allow_aw&&!wr_active&&!b_pending&&(rnd()%4!=0);
 t.m_wready=allow_w&&wr_active&&(rnd()%3!=0);
 t.m_bvalid=b_pending&&!(hold_final_b&&job_written==job_want)&&(b_held||rnd()%5!=0);
 t.m_bresp=b_error;t.eval();
 Beat b{bool(t.s_axi_arvalid&&t.s_axi_arready),bool(t.s_axi_awvalid&&t.s_axi_awready),bool(t.s_axi_wvalid&&t.s_axi_wready),bool(t.s_axi_bvalid&&t.s_axi_bready),bool(t.s_axi_rvalid&&t.s_axi_rready),t.s_axi_rdata,bool(t.s_axi_rlast)};
 if(b.b)assert(t.s_axi_bresp==0);if(b.r)assert(t.s_axi_rresp==0);
 bool ar=t.m_arvalid&&t.m_arready,r=t.m_rvalid&&t.m_rready,aw=t.m_awvalid&&t.m_awready,w=t.m_wvalid&&t.m_wready,bresp=t.m_bvalid&&t.m_bready;
 uint32_t addr=t.m_araddr,len=t.m_arlen,wa=t.m_awaddr,wl=t.m_awlen,wd=t.m_wdata,ws=t.m_wstrb;bool last=t.m_wlast;
 rd_held=t.m_rvalid&&!t.m_rready;b_held=t.m_bvalid&&!t.m_bready;
 // Mirrors the retained arbiter's no-BREADY contract: every legal B is accepted.
 if(t.m_bvalid)assert(t.m_bready);
 t.clk=1;t.eval();ticks++;
 if(ar){assert(!rd_active);assert((addr&4095)+4*(len+1)<=4096);rd_active=true;rd_addr=addr;rd_left=len+1;rd_error=inject_error?2:0;inject_error=false;bursts++;}
 if(r){assert(rd_active);words++;rd_addr+=4;if(!--rd_left){rd_active=false;rd_held=false;}}
 if(aw){assert(!wr_active&&!b_pending&&!rd_active);assert((wa&63)==0);assert(wl==8);assert((wa&4095)+4*(wl+1)<=4096);wr_active=true;wr_addr=wa;wr_left=wl+1;write_bursts++;}
 if(w){assert(wr_active&&ws==15);assert(last==(wr_left==1));output[wr_addr]=wd;wr_addr+=4;write_words++;job_written++;if(!--wr_left){wr_active=false;b_pending=true;b_error=inject_b_error?2:0;inject_b_error=false;}}
 if(bresp){assert(b_pending);b_pending=false;b_held=false;}
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
  t.s_axi_rready=rnd()%4!=0;Beat b=tick();if(b.r){assert(b.last==(v.size()+1==unsigned(n)));v.push_back(b.data);}
 }
 assert(v.size()==unsigned(n));t.s_axi_rready=0;return v;
}
static uint32_t reg(uint32_t off){return rd(0x4b000000+off)[0];}
static void put(uint32_t off,uint32_t v){wr(0x4b000000+off,v);}
struct Job {uint32_t h[27];std::vector<uint32_t> data;};
static std::vector<Job> jobs;
static uint32_t result_base=0x13fff000;
static void configure(const Job&j,unsigned dest){
 wr(0x4b000100,std::vector<uint32_t>(j.h+3,j.h+19));
 put(0xc,j.h[1]);put(0x10,j.h[0]|dest<<8);
 put(0x14,j.h[2]);put(0x18,j.h[19]);put(0x1c,j.h[20]);put(0x20,j.h[21]);
 put(0x24,j.h[22]);put(0x28,j.h[23]);put(0x2c,j.h[25]|j.h[26]<<16);put(0x30,j.h[24]);put(0x40,result_base);
 memory.clear();output.clear();job_written=0;job_want=9*j.h[0];
 for(unsigned v=0;v<j.h[0];v++)for(unsigned k=0;k<6;k++)memory[j.h[1]+24*v+4*k]=j.data[v*15+k];
 output[result_base-4]=0xcafebabe;output[result_base+64*j.h[0]]=0x12345678;
 for(unsigned v=0;v<j.h[0];v++)for(unsigned k=9;k<16;k++)output[result_base+64*v+4*k]=0xa5000000^(v*16+k);
}
static void wait_done(){
 bool done=false;for(int k=0;k<20000&&!done;k++)done=(reg(4)&2)!=0;assert(done);
}
static uint32_t f32(int16_t x){float f=x;uint32_t b;std::memcpy(&b,&f,4);return b;}
static void fabric_reset(){
 t.reset_n=0;t.geometry_fabric_reset_n=0;rd_active=rd_held=wr_active=b_pending=b_held=false;
 for(int i=0;i<10;i++)tick();t.reset_n=1;t.geometry_fabric_reset_n=1;
 allow_dma=allow_aw=allow_w=true;hold_final_b=false;
 for(int i=0;i<10;i++)tick();
}
int main(int argc,char**argv){
 Verilated::commandArgs(argc,argv);assert(argc==2);
 std::ifstream f(argv[1],std::ios::binary);std::vector<char>b((std::istreambuf_iterator<char>(f)),{});
 assert(b.size()%4==0);const uint32_t*p=reinterpret_cast<const uint32_t*>(b.data());unsigned nj=*p++;assert(nj==20);
 for(unsigned i=0;i<nj;i++){Job j;for(auto&x:j.h)x=*p++;j.data.assign(p,p+15*j.h[0]);p+=15*j.h[0];jobs.push_back(j);}
 assert(reinterpret_cast<const char*>(p)==b.data()+b.size());
 // Match retained100MHz top: no fabric reset pulse on startup. Only the
 // three-cycle initialized POR must establish the new block's defaults.
 t.mixer_ready=1;t.reset_n=1;t.geometry_fabric_reset_n=1;
 for(int i=0;i<20;i++)tick();assert(reg(0x14)==0x3f800000);
 assert(reg(0)==0x4c4f4403);assert(reg(4)==0);assert(reg(0x400)==0);
 put(8,1);assert((reg(4)&0x86)==0x82);assert(bursts==0);put(8,2);
 unsigned checked=0,vertex_count=0,dma_checked=0;
 for(unsigned ji=0;ji<jobs.size();ji++){
  const auto&j=jobs[ji];unsigned n=j.h[0],dest=(ji*7)%(33-n);configure(j,dest);
  assert(reg(4)&0x100);assert(!(reg(4)&4));
  wr(0x4b00000c,0,3);wr(0x4b00000c,0,0);assert(reg(0xc)==j.h[1]);
  if(ji==0){
   allow_dma=false;allow_aw=false;hold_final_b=true;
   put(8,1);assert(reg(4)&1);
   put(0xc,0);put(0x10,0);put(0x14,0);put(0x18,0);put(0x1c,0);put(0x20,0);
   put(0x24,0);put(0x28,0);put(0x2c,0);put(0x30,0);put(0x40,0);
   wr(0x4b000100,std::vector<uint32_t>(16,0));put(8,3);
   assert(reg(0xc)==j.h[1]);assert(reg(0x10)==(n|dest<<8));assert(reg(0x40)==result_base);assert(reg(0x400)==0);
   allow_dma=true;
   for(int k=0;k<10000&&!t.m_awvalid;k++)tick();assert(t.m_awvalid);
   assert(reg(4)&1);put(0xc,0);put(0x10,0);put(0x40,0);put(8,3);
   assert(reg(0xc)==j.h[1]);assert(reg(0x10)==(n|dest<<8));assert(reg(0x40)==result_base);
   allow_aw=true;
   for(int k=0;k<10000&&!(b_pending&&job_written==job_want);k++)tick();
   assert(b_pending&&job_written==job_want);assert((reg(4)&7)==1);
   assert(reg(0x400)==0);put(0x10,0);put(8,1);assert(reg(0x10)==(n|dest<<8));
   hold_final_b=false;
  }else put(8,1);
  wait_done();uint32_t status=reg(4);assert((status&0x6ef)==6);
  assert(reg(0x34)==0&&reg(0x38)==0&&reg(0x3c)==0);
  assert(job_written==9*n);assert(output.at(result_base-4)==0xcafebabe);assert(output.at(result_base+64*n)==0x12345678);
  for(unsigned v=0;v<n;v++)for(unsigned k=0;k<9;k++){
   const uint32_t*e=&j.data[v*15+6];
   uint32_t want=k<5?e[k]:k==5?f32(int16_t(e[5])):k==6?f32(int16_t(e[5]>>16)):k==7?e[6]:e[7]&255;
   uint32_t mask=k==7&&!j.data[v*15+14]?0xff000000:0xffffffff;
   uint32_t got=output.at(result_base+64*v+4*k);
   if((got&mask)!=(want&mask)){std::fprintf(stderr,"DMA job%u vertex%u word%u got%08x want%08x\n",ji,v,k,got,want);return 1;}dma_checked++;
  }
  for(unsigned row=0;row<8;row++){
   auto values=rd(0x4b000400+128*row,32);
   for(unsigned v=0;v<32;v++){
    bool inside=v>=dest&&v<dest+n;
    uint32_t want=inside?j.data[(v-dest)*15+6+row]:0;
    uint32_t mask=inside&&row==6&&!j.data[(v-dest)*15+14]?0xff000000:0xffffffff;
    if((values[v]&mask)!=(want&mask)){std::fprintf(stderr,"debug job%u row%u slot%u got%08x want%08x\n",ji,row,v,values[v],want);return 1;}checked++;
   }
  }
  // Editing the next descriptor invalidates previous debug ownership.
  put(0x10,n|dest<<8);assert(!(reg(4)&4));assert(reg(0x400+dest*4)==0);
  for(unsigned v=0;v<n;v++)for(unsigned k=9;k<16;k++)assert(output.at(result_base+64*v+4*k)==(0xa5000000^(v*16+k)));
  vertex_count+=n;
 }
 unsigned range_jobs=0,range_vertices=0;
 // All528 valid count/dest combinations, covering each of the32 destination
 // slots. Repeat native vertices under one exact matrix/light context.
 for(unsigned n=1;n<=32;n++)for(unsigned dest=0;dest+n<=32;dest++){
  Job j=jobs[0];j.h[0]=n;j.h[1]=0x10000fc0;j.data.clear();
  for(unsigned v=0;v<n;v++){
   unsigned source=v%jobs[0].h[0];
   j.data.insert(j.data.end(),jobs[0].data.begin()+15*source,jobs[0].data.begin()+15*(source+1));
  }
  configure(j,dest);put(8,1);wait_done();assert((reg(4)&0x6ef)==6);
  assert(job_written==9*n);assert(output.at(result_base-4)==0xcafebabe);assert(output.at(result_base+64*n)==0x12345678);
  for(unsigned v=0;v<n;v++){
   const uint32_t*e=&j.data[v*15+6];
   for(unsigned k=0;k<9;k++){
    uint32_t want=k<5?e[k]:k==5?f32(int16_t(e[5])):k==6?f32(int16_t(e[5]>>16)):k==7?e[6]:e[7]&255;
    uint32_t mask=k==7&&!j.data[v*15+14]?0xff000000:0xffffffff;
    assert((output.at(result_base+64*v+4*k)&mask)==(want&mask));
   }
   for(unsigned k=9;k<16;k++)assert(output.at(result_base+64*v+4*k)==(0xa5000000^(v*16+k)));
  }
  range_jobs++;range_vertices+=n;
 }
 std::fprintf(stderr,"STRIDE64_RANGE jobs=%u vertices=%u padding_untouched=yes\n",range_jobs,range_vertices);
 unsigned aw_before=write_bursts;
 configure(jobs[0],0);put(0x10,0);put(8,1);wait_done();assert((reg(4)&0x2e)==0x2a);assert(reg(0x400)==0);assert(write_bursts==aw_before);
 configure(jobs[0],0);inject_error=true;put(8,1);wait_done();assert(!(reg(4)&4));assert((reg(4)&0x60)==0x40);assert(write_bursts==aw_before);
 configure(jobs[0],0);memory[jobs[0].h[1]]=0x7fc00000;put(8,1);wait_done();assert((reg(4)&0xc)==8);assert(write_bursts==aw_before);
 // Output descriptor validation runs before the first AW; old memory untouched.
 configure(jobs[0],0);put(0x40,result_base+4);put(8,1);wait_done();assert((reg(4)&0x606)==0x202);assert(write_bursts==aw_before);assert(output.size()==2+7*jobs[0].h[0]);
 configure(jobs[0],0);inject_b_error=true;put(8,1);wait_done();assert((reg(4)&0x606)==0x402);assert(reg(0x400)==0);
 configure(jobs[0],0);put(8,1);wait_done();assert((reg(4)&6)==6);
 // CPU/peripheral reset cannot cancel or mutate an outstanding M2 job.
 configure(jobs[0],0);allow_dma=false;put(8,1);assert(reg(4)&1);
 t.reset_n=0;for(int k=0;k<10;k++)tick();t.reset_n=1;
 for(int k=0;k<10;k++)tick();assert(reg(4)&1);assert(reg(0xc)==jobs[0].h[1]);
 allow_dma=true;wait_done();assert((reg(4)&6)==6);
 // Hold a debug transaction across final B with twelve relative timings.
 // Its value is sampled by the peripheral read latch, so either busy-zero
 // or the exact requested row is valid; the last writer row is never valid.
 unsigned crossing_reads=0;
 for(int delay=-5;delay<=6;delay++){
  configure(jobs[0],0);hold_final_b=true;put(8,1);
  for(int k=0;k<10000&&!(b_pending&&job_written==job_want);k++)tick();
  assert(b_pending&&job_written==job_want);
  if(delay<0){hold_final_b=false;for(int i=0;i<-delay;i++)tick();}
  t.s_axi_araddr=0x4b000400;t.s_axi_arlen=0;t.s_axi_arvalid=1;t.s_axi_rready=1;
  bool ar=false,seen=false;uint32_t value=0;
  for(int k=0;k<1000&&!seen;k++){
   if(k==delay)hold_final_b=false;
   Beat beat=tick();if(beat.ar){ar=true;t.s_axi_arvalid=0;}
   if(beat.r){assert(ar);value=beat.data;seen=true;}
  }
  assert(seen);assert(value==0||value==jobs[0].data[6]);crossing_reads++;
  t.s_axi_rready=0;hold_final_b=false;wait_done();
 }
 // Fabric reset discards both sides of outstanding transactions.
 allow_dma=false;put(8,1);assert(reg(4)&1);fabric_reset();assert(reg(4)==0);
 configure(jobs[0],0);allow_w=false;put(8,1);
 for(int k=0;k<10000&&!wr_active;k++)tick();assert(wr_active);fabric_reset();assert(reg(4)==0);
 configure(jobs[0],0);put(8,1);wait_done();assert((reg(4)&6)==6);
 assert(rd(0x4a000010)[0]==0x44444444);assert(rd(0x49000000)[0]==0x125);
 std::printf("{\"passed\":true,\"native_jobs\":20,\"native_vertices\":%u,\"debug_words_checked\":%u,\"dma_words_checked\":%u,\"completion_crossing_reads\":%u,\"read_bursts\":%u,\"write_bursts\":%u,\"cycles\":%llu,\"busy_write_ignored\":true,\"partial_write_ignored\":true,\"final_B_ownership\":true,\"compute_errors_suppress_writes\":true,\"invalid_result_pointer\":true,\"BRESP_failure\":true,\"reset_recovery\":true}\n",vertex_count,checked,dma_checked,crossing_reads,bursts,write_bursts,(unsigned long long)ticks);
}
