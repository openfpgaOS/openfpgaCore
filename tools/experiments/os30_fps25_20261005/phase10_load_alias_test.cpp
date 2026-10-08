#include "Vtb_load_alias.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <stdexcept>
#include <unordered_map>
#include <vector>
static Vtb_load_alias t;
static uint32_t rng=0x7194acd3;
static unsigned rnd(){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
static void need(bool c,const char*s){if(!c)throw std::runtime_error(s);}
static uint64_t cycles=0,alias_words=0,stalls=0;
static bool dma_allow=true,dma_active=false,dma_held=false,dma_error_next=false;
static unsigned dma_addr=0,dma_left=0,dma_error=0,dma_bursts=0;
static std::unordered_map<uint32_t,uint32_t> mem;
struct Beat{bool ar,r,aw,w,b;uint32_t data,resp;bool last;};
static bool held=false;static uint32_t held_data,held_resp;static bool held_last;
static Beat tick(){
 t.clk=0;
 t.dma_arready=dma_allow&&!dma_active&&rnd()%4!=0;
 t.dma_rvalid=dma_active&&dma_allow&&(dma_held||rnd()%3!=0);
 t.dma_rdata=dma_active?mem.at(dma_addr):0;t.dma_rresp=dma_error;t.dma_rlast=dma_left==1;t.eval();
 if(held&&!t.reset&&!t.cpu_reset)need(t.s_rvalid&&t.s_rdata==held_data&&t.s_rresp==held_resp&&t.s_rlast==held_last,"R changed under stall");
 held=t.s_rvalid&&!t.s_rready&&!t.reset&&!t.cpu_reset;held_data=t.s_rdata;held_resp=t.s_rresp;held_last=t.s_rlast;stalls+=held;
 Beat b{bool(t.s_arvalid&&t.s_arready),bool(t.s_rvalid&&t.s_rready),bool(t.s_awvalid&&t.s_awready),bool(t.s_wvalid&&t.s_wready),bool(t.s_bvalid),t.s_rdata,t.s_rresp,bool(t.s_rlast)};
 bool ar=t.dma_arvalid&&t.dma_arready,r=t.dma_rvalid&&t.dma_rready;unsigned addr=t.dma_araddr,len=t.dma_arlen;
 if(t.obs_active){need(!t.dma_arvalid,"LOAD started while alias burst owned output");need(!t.m_rready,"alias leaked RREADY to SDRAM");}
 dma_held=t.dma_rvalid&&!t.dma_rready;t.clk=1;t.eval();cycles++;
 if(ar){need(!dma_active,"overlapping source burst");need((addr&4095)+4*(len+1)<=4096,"source crossed4K");dma_addr=addr;dma_left=len+1;dma_active=true;dma_error=dma_error_next?2:0;dma_error_next=false;dma_bursts++;}
 if(r){dma_addr+=4;if(!--dma_left){dma_active=false;dma_held=false;}}
 return b;
}
static uint32_t reg(unsigned off){t.reg_addr=off/4;t.eval();return t.reg_rdata;}
static void put(unsigned off,uint32_t v,unsigned strobe=15){t.reg_addr=off/4;t.reg_wdata=v;t.reg_wstrb=strobe;t.reg_wr=1;tick();t.reg_wr=0;}
static void reset(){t.reset=1;t.cpu_reset=0;t.reg_wr=0;t.s_arvalid=t.s_awvalid=t.s_wvalid=t.s_rready=0;t.m_arready=t.m_rvalid=t.m_awready=t.m_wready=t.m_bvalid=0;dma_active=dma_held=false;held=false;for(int i=0;i<4;i++)tick();t.reset=0;tick();}
static std::vector<uint32_t> read(unsigned address,unsigned n,unsigned resp=0){
 t.s_araddr=address;t.s_arlen=n-1;t.s_arvalid=1;t.s_rready=0;bool accepted=false;
 for(unsigned i=0;i<20000&&!accepted;i++)accepted=tick().ar;need(accepted,"AR timeout");t.s_arvalid=0;
 std::vector<uint32_t> v;
 for(unsigned i=0;i<20000&&v.size()<n;i++){t.s_rready=rnd()%4!=0;auto b=tick();if(b.r){need(b.resp==resp,"RRESP mismatch");need(b.last==(v.size()+1==n),"RLAST mismatch");v.push_back(b.data);alias_words++;}}
 need(v.size()==n,"R timeout");t.s_rready=0;return v;
}
struct Job{uint32_t h[27];std::vector<uint32_t>d;};
static std::vector<Job> jobs;
static void configure(const Job&j){
 for(unsigned i=0;i<16;i++)put(0x100+4*i,j.h[3+i]);
 put(0xc,j.h[1]);put(0x10,j.h[0]);put(0x14,j.h[2]);
 for(unsigned i=0;i<3;i++)put(0x18+4*i,j.h[19+i]);
 put(0x24,j.h[22]);put(0x28,j.h[23]);put(0x2c,j.h[25]|j.h[26]<<16);put(0x30,j.h[24]);
 mem.clear();for(unsigned v=0;v<j.h[0];v++)for(unsigned k=0;k<6;k++)mem[j.h[1]+24*v+4*k]=j.d[v*15+k];
}
static void done(){for(unsigned i=0;i<30000;i++){if(!(reg(4)&1)){need(reg(4)&2,"premature idle before completion");return;}tick();}throw std::runtime_error("LOAD timeout");}
static void check(const Job&j,unsigned mirror=0){
 need((reg(4)&0xef)==6,"LOAD status");
 // Every cacheline, including stale unused second vertices after odd count.
 for(unsigned start=0;start<256;start+=16){auto r=read(0x13f80000+mirror+start*4,16);
  for(unsigned k=0;k<16;k++){unsigned slot=(start+k)/8,row=(start+k)%8;uint32_t want=slot<j.h[0]?j.d[slot*15+6+row]:0;
   uint32_t mask=slot<j.h[0]&&row==6&&!j.d[slot*15+14]?0xff000000:~0u;
   if((r[k]&mask)!=(want&mask)){std::fprintf(stderr,"slot%u row%u got%08x want%08x\n",slot,row,r[k],want);throw std::runtime_error("raw/native mismatch");}
  }
 }
}
static void reject_write(unsigned address,unsigned n){
 t.s_awaddr=address;t.s_awlen=n-1;t.s_awvalid=1;t.s_wvalid=1;t.s_wdata=0xdeadbeef;t.s_wstrb=15;t.s_wlast=n==1;t.m_awready=t.m_wready=1;
 bool aw=false;for(unsigned i=0;i<100&&!aw;i++){t.clk=0;t.eval();need(!t.m_awvalid&&!t.m_wvalid,"alias AW/W escaped");auto b=tick();need(!b.w,"alias W accepted before AW");aw=b.aw;}need(aw,"AW reject timeout");t.s_awvalid=0;
 for(unsigned i=0;i<n;i++){t.s_wlast=i+1==n;auto b=tick();need(b.w&&!t.m_wvalid,"rejected W not drained");}
 t.s_wvalid=0;need(t.s_bvalid&&t.s_bresp==2,"missing write error");tick();need(!t.s_bvalid,"sticky B without BREADY");t.m_awready=t.m_wready=0;
}
int main(int argc,char**argv){Verilated::commandArgs(argc,argv);try{
 need(argc==2,"native fixture argument");std::ifstream f(argv[1],std::ios::binary);auto rd=[&](){uint32_t u;f.read((char*)&u,4);need(bool(f),"native fixture short");return u;};need(rd()==20,"native jobs");
 for(unsigned i=0;i<20;i++){Job j;for(auto&u:j.h)u=rd();for(unsigned k=0;k<15*j.h[0];k++)j.d.push_back(rd());jobs.push_back(j);}
 reset();need(reg(0)==0x4c4f4404&&reg(4)==0,"reset/ID");for(auto x:read(0x13f80000,16))need(x==0,"uninitialized alias leaked");
 put(8,1);need((reg(4)&0x87)==0x82&&dma_bursts==0,"missing matrix accepted");put(8,2);
 unsigned vertices=0,synthetic=0;
 for(unsigned i=0;i<jobs.size();i++){const auto&j=jobs[i];configure(j);put(0xc,0,3);put(8,1);
  if(i==0){dma_allow=false;for(int k=0;k<10;k++)tick();need(reg(4)&1,"busy absent");
   for(auto off:{0xc,0x10,0x14,0x18,0x1c,0x20,0x24,0x28,0x2c,0x30,0x100})put(off,0);put(8,3);
   t.s_araddr=0x13f80000;t.s_arlen=15;t.s_arvalid=1;
   for(int k=0;k<8;k++)need(!tick().ar,"alias accepted during compute");t.s_arvalid=0;
   t.cpu_reset=1;for(int k=0;k<4;k++)tick();t.cpu_reset=0;need(reg(4)&1,"CPU reset aborted DMA");dma_allow=true;
  }
  done();check(j,i&1?0x40000000:0);vertices+=j.h[0];
 }
 for(unsigned n=1;n<=32;n++){Job j=jobs[0];j.h[0]=n;j.h[1]=0x10000fc0;j.d.clear();
  for(unsigned v=0;v<n;v++){unsigned k=v%jobs[0].h[0];j.d.insert(j.d.end(),jobs[0].d.begin()+15*k,jobs[0].d.begin()+15*(k+1));}
  configure(j);put(8,1);done();check(j);synthetic+=n;
 }
 // Keep old output burst stalled while the next complete job is requested.
 configure(jobs[0]);put(8,1);done();
 t.s_araddr=0x13f80000;t.s_arlen=15;t.s_arvalid=1;t.s_rready=0;need(tick().ar,"held AR");t.s_arvalid=0;unsigned before=dma_bursts;
 put(8,1);need((reg(4)&7)==1,"queued start not busy");
 for(unsigned k=0;k<32;k++){tick();need(dma_bursts==before,"start did not wait for old burst");}
 unsigned received=0;for(unsigned k=0;k<200&&received<16;k++){t.s_rready=rnd()%3!=0;auto b=tick();if(b.r){need(b.data==jobs[0].d[(received/8)*15+6+(received%8)],"old output changed while queued");received++;}}
 need(received==16,"held read completion");t.s_rready=0;done();check(jobs[0]);
 // CPU reset discards only its local unfinished cacheline response.
 t.s_araddr=0x13f80000;t.s_arlen=15;t.s_arvalid=1;need(tick().ar,"reset AR");t.s_arvalid=0;for(int i=0;i<5;i++)tick();need(t.obs_active,"local read inactive");
 t.cpu_reset=1;tick();t.cpu_reset=0;tick();need(!t.obs_active&&!t.s_rvalid,"CPU reset did not discard local alias");check(jobs[0]);
 for(auto a:{0x13f80001u,0x13f803fcu}){auto r=read(a,16,2);for(auto x:r)need(x==0,"invalid burst leaked data");}
 reject_write(0x13f80000,16);reject_write(0x53f803fc,1);check(jobs[0]);
 // START wins over an alias AR on the same edge; no speculative burst sees
 // a mixture of the old result and the new LOAD's in-place writes.
 t.s_araddr=0x13f80000;t.s_arlen=15;t.s_arvalid=1;t.reg_wr=1;t.reg_addr=2;t.reg_wdata=1;t.reg_wstrb=15;
 need(!tick().ar,"simultaneous START/AR accepted old read");t.reg_wr=0;t.s_arvalid=0;done();check(jobs[0]);
 // Exact scalar/subline accesses also use the synchronous row selector.
 for(unsigned word=0;word<8*jobs[0].h[0];word++){auto r=read(0x13f80000+4*word,1);unsigned row=word%8,v=word/8;
  uint32_t mask=row==6&&!jobs[0].d[v*15+14]?0xff000000:~0u;need((r[0]&mask)==(jobs[0].d[v*15+6+row]&mask),"scalar alias word");}
 // CPU reset during an already accepted external source burst preserves it.
 put(8,1);for(unsigned i=0;i<1000&&!dma_active;i++)tick();need(dma_active,"accepted source burst missing");dma_allow=false;
 t.cpu_reset=1;for(unsigned i=0;i<4;i++)tick();t.cpu_reset=0;need((reg(4)&1)&&dma_active,"CPU reset lost external source ownership");dma_allow=true;done();check(jobs[0]);
 // A partly-drained rejected CPU write is entirely local and resettable.
 t.s_awaddr=0x13f80000;t.s_awlen=15;t.s_awvalid=1;need(tick().aw,"partial reject AW");t.s_awvalid=0;
 t.s_wvalid=1;t.s_wlast=0;for(unsigned i=0;i<3;i++)need(tick().w,"partial reject W");t.s_wvalid=0;t.cpu_reset=1;tick();t.cpu_reset=0;tick();need(!t.s_bvalid,"abandoned local write B");check(jobs[0]);
 // Normal SDRAM AW and first W must pass on the SAME original cycle.
 t.s_awaddr=0x10500000;t.s_awlen=0;t.s_awvalid=1;t.s_wvalid=1;t.s_wlast=1;t.s_wdata=0x12345678;t.s_wstrb=5;t.m_awready=t.m_wready=1;t.clk=0;t.eval();
 need(t.m_awvalid&&t.m_wvalid&&t.s_awready&&t.s_wready,"normal AW/W gained latency");need(t.m_awaddr==t.s_awaddr&&t.m_wdata==t.s_wdata&&t.m_wstrb==5,"normal AW/W modified");auto bw=tick();need(bw.aw&&bw.w,"normal initial handshake");t.s_awvalid=t.s_wvalid=0;
 t.cpu_reset=1;tick();t.cpu_reset=0;t.m_bvalid=1;t.m_bresp=0;t.clk=0;t.eval();need(t.s_bvalid&&t.s_bresp==0,"normal B hidden after CPU reset");tick();t.m_bvalid=t.m_awready=t.m_wready=0;
 // Normal AR/R passthrough is combinational and untouched, including stalls.
 t.s_araddr=0x10400400;t.s_arlen=15;t.s_arvalid=1;t.m_arready=1;t.clk=0;t.eval();need(t.m_arvalid&&t.s_arready&&t.m_araddr==t.s_araddr&&t.m_arlen==15,"normal AR modified");tick();t.s_arvalid=0;
 for(unsigned k=0;k<16;k++){t.m_rvalid=1;t.m_rdata=k^0xbeefcafe;t.m_rresp=0;t.m_rlast=k==15;t.s_rready=1;t.clk=0;t.eval();need(t.s_rvalid&&t.m_rready&&t.s_rdata==t.m_rdata&&t.s_rlast==t.m_rlast,"normal R modified");tick();}t.m_rvalid=t.m_arready=t.s_rready=0;
 configure(jobs[0]);put(0x10,0);put(8,1);done();need((reg(4)&0xe)==10,"zero count status");for(auto x:read(0x13f80000,16))need(x==0,"failed result exposed");
 configure(jobs[0]);dma_error_next=true;put(8,1);done();need((reg(4)&0x60)==0x40&&!(reg(4)&4),"source error status");
 configure(jobs[0]);mem[jobs[0].h[1]]=0x7fc00000;put(8,1);done();need((reg(4)&0xc)==8,"unsupported status");
 configure(jobs[0]);put(8,1);done();check(jobs[0]);
 configure(jobs[0]);put(0x10,33);put(8,1);done();need((reg(4)&0xe)==10,"count33 status");
 configure(jobs[0]);dma_allow=false;put(8,1);tick();reset();need(reg(4)==0,"fabric reset kept job");dma_allow=true;configure(jobs[0]);put(8,1);done();check(jobs[0]);
 std::printf("{\"passed\":true,\"native_jobs\":20,\"native_vertices\":%u,\"native_independent_RGB\":145,\"synthetic_vertices\":%u,\"counts_1_through_32\":true,\"alias_words_checked\":%llu,\"source_bursts\":%u,\"backpressure_cycles\":%llu,\"cycles\":%llu,\"read_before_start_ownership\":true,\"cpu_reset_preserves_load\":true,\"cpu_reset_discards_local_read\":true,\"write_rejected_and_drained\":true,\"normal_AW_W_AR_R_passthrough\":true,\"range_and_numeric_error_recovery\":true}\n",vertices,synthetic,(unsigned long long)alias_words,dma_bursts,(unsigned long long)stalls,(unsigned long long)cycles);
 }catch(const std::exception&e){std::fprintf(stderr,"FAIL %s cycle%llu\n",e.what(),(unsigned long long)cycles);return 1;}}
