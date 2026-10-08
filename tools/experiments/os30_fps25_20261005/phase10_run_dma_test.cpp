#include "Vgeometry_run_dma.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <iostream>
#include <random>
#include <stdexcept>
#include <vector>
static void need(bool b,const char*s){if(!b)throw std::runtime_error(s);}
static unsigned decode(uint32_t w){return ((w>>17)&31)|(((w>>9)&31)<<5)|(((w>>1)&31)<<10);}
static unsigned response(unsigned i,unsigned mode){
 if(mode==0)return 0;
 unsigned k=(i&31)*17+(i>>5&31)*13+(i>>10)*7;
 return k%19==0?3:k%3;
}
int main(int argc,char**argv){
 Verilated::commandArgs(argc,argv);Vgeometry_run_dma d;std::mt19937 rng(0x72646d61);
 auto edge=[&](){d.clk=0;d.eval();d.clk=1;d.eval();};
 d.reset=1;d.start=d.m_arready=d.m_rvalid=d.check_ready=d.result_valid=0;
 d.packet_access=d.packet_write=d.packet_read_valid=0;edge();edge();d.reset=0;edge();
 unsigned jobs=0,bursts=0,words=0,triangles=0,atomic=0,faults=0;
 try {for(unsigned t=0;t<3000;t++){
  unsigned budget=t<67?t:1+rng()%64,mode=t%3==0?0:1;
  uint32_t base=t%29==0?0x13ffffc0u:0x11000000u+(t%31)*8;
  if(t%53==0)base|=4;if(t%59==0)base=0x14000000;
  bool invalid=budget==0||budget>64||(base&7)||base<0x10000000||base>=0x14000000;
  bool fault=!invalid&&t%11==0;
  unsigned fault_chunk=t%4,chunk_no=0;
  std::vector<std::array<uint32_t,2>> cmds(64);
  for(unsigned i=0;i<64;i++){
   auto iw=[&](){return ((rng()%32)*2<<16)|((rng()%32)*2<<8)|((rng()%32)*2);};
   unsigned op=t%3==0?5:(rng()%3==0?6:5);
   cmds[i]={(op<<24)|iw(),iw()};
   if(t%7==0&&i==t%32)cmds[i][0]=0xfa000000;
   if(t%17==0&&i==t%32)cmds[i][0]|=0x400000;
  }
  unsigned accepted=0,seen=0,expected_checks=0,clips=0,culls=0,expected_error=0;
  bool fallback=invalid,expected_invalid=invalid;
  std::vector<unsigned> survivors;
  if(invalid)expected_error=1;
  else for(unsigned i=0;i<budget;i++){
   if(base+uint64_t(i)*8>=0x14000000ull){expected_error=1;expected_invalid=fallback=true;break;}
   if(fault&&i/8==fault_chunk){expected_error=2;expected_invalid=fallback=true;break;}
   auto c=cmds[i];unsigned op=c[0]>>24,nt=op==6?2:1;
   if((op!=5&&op!=6)||(c[0]&0xc0c0c0)||(nt==2&&(c[1]&0xc0c0c0))){fallback=true;break;}
   if(seen+nt>budget)break;
   unsigned old=survivors.size(),nc=0,nu=0;bool fail=false;
   for(unsigned k=0;k<nt;k++){
    unsigned ix=decode(c[k]),r=response(ix,mode);expected_checks++;
    if(r==3){fail=true;break;}if(r==0)survivors.push_back(ix);else if(r==1)nc++;else nu++;
   }
   if(fail){if(survivors.size()!=old)atomic++;survivors.resize(old);fallback=true;break;}
   accepted++;seen+=nt;clips+=nc;culls+=nu;if(seen==budget)break;
  }
  d.command_base=base;d.command_count=budget;d.cull_mode=t%4;d.start=1;edge();d.start=0;
  bool active=false,rv=false,pending=false,done=false;
  unsigned beat=0,len=0,pending_index=0,delay=0,actual_checks=0;
  uint32_t address=0;bool burst_fault=false;
  for(unsigned cy=0;cy<20000;cy++){
   d.clk=0;d.m_arready=!active&&(rng()%4!=0);
   d.m_rvalid=active&&(rng()%4!=0);d.m_rlast=active&&beat+1==len;
   unsigned off=active?(address-base)/4+beat:0;
   d.m_rdata=off<128?cmds[off/2][off%2]:0;
   d.m_rresp=burst_fault&&beat==len/2?2:0;
   if(pending&&!rv){if(delay)delay--;else rv=true;}
   d.check_ready=!pending&&(rng()%4!=0);d.result_valid=rv;
   unsigned r=response(pending_index,mode);d.result_reject_class=r==3?0:r;d.result_fallback=r==3;
   // Attacking descriptor pins while busy must not alter owned work.
   d.start=rng()%37==0;d.command_base=rng();d.command_count=rng()%128;d.cull_mode=rng()%4;d.eval();
   bool ar=d.m_arvalid&&d.m_arready,rd=d.m_rvalid&&d.m_rready;
   bool ck=d.check_valid&&d.check_ready,rr=d.result_valid&&d.result_ready;
   if(ar){
    need(!active,"overlapping AXI burst");address=d.m_araddr;len=d.m_arlen+1;beat=0;active=true;bursts++;
    need(len<=16&&((address&4095)+len*4<=4096),"AXI burst boundary");
    need(address>=base&&uint64_t(address)+len*4<=0x14000000ull,"DMA aperture");
    // The fixture sources are at most256-byte offsets, so no chunk splits
    // occur in this suite. Boundary splitting is separately rawDMA-tested.
    burst_fault=fault&&chunk_no==fault_chunk;chunk_no++;
   }
   if(rd){words++;if(++beat==len)active=false;}
   if(ck){need(!active,"arithmetic holds accepted AXI burst");need(!pending,"overlapping geometry request");pending=true;pending_index=d.check_indices;delay=5+rng()%31;actual_checks++;need(d.check_cull_mode==t%4,"owned cull mode changed");}
   if(rr){pending=false;rv=false;}
   edge();if(d.done){done=true;break;}
  }
  need(done&&!d.busy&&!active&&!pending,"job did not drain");
  if(d.accepted_commands!=accepted||d.live_triangles!=survivors.size()||d.dma_error!=expected_error){
   std::cerr<<"counts "<<unsigned(d.accepted_commands)<<"/"<<accepted<<" live "<<unsigned(d.live_triangles)<<"/"<<survivors.size()<<" error "<<unsigned(d.dma_error)<<"/"<<expected_error<<"\n";
  }
  need(d.accepted_commands==accepted&&d.live_triangles==survivors.size(),"committed prefix mismatch");
  need(d.prefix_end==base+accepted*8,"restart pointer mismatch");
  need(d.dma_error==expected_error,"DMA error classification");
  need(bool(d.fallback)==fallback&&bool(d.invalid)==expected_invalid,"fallback classification");
  need(d.clip_rejected==clips&&d.cull_rejected==culls&&d.accepted_triangles==seen,"committed diagnostics mismatch");
  need(actual_checks==expected_checks&&d.checked_triangles==expected_checks,"geometry check count");
  if(expected_error==2)faults++;
  d.start=d.m_rvalid=d.result_valid=0;
  for(unsigned i=0;i<survivors.size();i++){d.list_addr=i;edge();need(d.list_indices==survivors[i],"survivor buffer corruption");}
  d.packet_access=1;uint32_t scratch[9];
  for(unsigned i=0;i<9;i++){scratch[i]=rng();d.packet_write=1;d.packet_write_addr=i;d.packet_write_data=scratch[i];edge();}d.packet_write=0;
  for(unsigned i=0;i<9;i++){d.packet_read_valid=1;d.packet_read_addr=i;edge();need(d.packet_read_data==scratch[i],"packet RAM mismatch");}
  d.packet_access=d.packet_read_valid=0;
  for(unsigned i=0;i<survivors.size();i++){d.list_addr=i;edge();need(d.list_indices==survivors[i],"packet overwrote survivors");}
  triangles+=actual_checks;jobs++;edge();
 }
 std::cout<<"{\"passed\":true,\"jobs\":"<<jobs<<",\"bursts\":"<<bursts<<",\"words\":"<<words<<",\"triangles\":"<<triangles<<",\"atomic_rollbacks\":"<<atomic<<",\"read_errors\":"<<faults<<"}\n";
 }catch(const std::exception&e){std::cerr<<"job "<<jobs<<": "<<e.what()<<"\n";return 1;}
}
