#include "Vgeometry_run_precheck.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <iostream>
#include <random>
#include <stdexcept>
#include <vector>
static void need(bool b,const char*s){if(!b)throw std::runtime_error(s);}
static unsigned decode(uint32_t w){return ((w>>17)&31)|(((w>>9)&31)<<5)|(((w>>1)&31)<<10);}
static bool badword(uint32_t w){return w&0xc0c0c0;}
static unsigned response(unsigned indices){
 unsigned a=indices&31,b=indices>>5&31,c=indices>>10;
 // Independent geometry response model: 3=fallback, 1/2=culled, 0=keep.
 unsigned key=a*17+b*13+c*7;
 return key%19==0?3:key%3;
}
int main(int argc,char**argv){
 Verilated::commandArgs(argc,argv);Vgeometry_run_precheck d;std::mt19937 rng(0x72756e31);
 auto edge=[&](){d.clk=0;d.eval();d.clk=1;d.eval();};
 d.reset=1;d.start=d.command_valid=d.check_ready=d.result_valid=0;edge();edge();d.reset=0;edge();
 unsigned jobs=0,checks=0,retained=0,atomic_rollbacks=0;
 try{for(unsigned trial=0;trial<4096;trial++){
  unsigned n=trial<67?trial:1+rng()%64;
  std::vector<std::array<uint32_t,2>> commands;
  for(unsigned i=0;i<n;i++){
   auto word=[&](){return ((rng()%32)*2<<16)|((rng()%32)*2<<8)|((rng()%32)*2);};
   uint32_t op=(rng()%3==0?6:5);
   uint32_t a=word(),b=word();if(trial%17==0&&i==n/2)a|=0x400000;
   if(trial%23==0&&i==n/2)op=0xfa;
   commands.push_back({(op<<24)|a,b});
  }
  unsigned accepted=0,seen=0,expected_checks=0;bool fallback=false,invalid=false;
  std::vector<unsigned> survivors;
  if(n==0||n>64)fallback=invalid=true;
  else for(auto cmd:commands){
   unsigned op=cmd[0]>>24,nt=op==6?2:1;
   if((op!=5&&op!=6)||badword(cmd[0])||(nt==2&&badword(cmd[1]))){fallback=true;break;}
   if(seen+nt>64){fallback=invalid=true;break;}seen+=nt;
   unsigned old=survivors.size();bool fail=false;
   for(unsigned t=0;t<nt;t++){
    unsigned indices=decode(cmd[t]),r=response(indices);expected_checks++;
    if(r==3){fail=true;break;}if(r==0)survivors.push_back(indices);
   }
   if(fail){if(survivors.size()!=old)atomic_rollbacks++;survivors.resize(old);fallback=true;break;}
   accepted++;
  }
  d.command_count=n;d.cull_mode=trial%4;d.start=1;edge();d.start=0;
  unsigned ci=0,actual_checks=0,delay=0,pending_indices=0;bool pending=false,rv=false,cv=false,finished=false;
  for(unsigned cycle=0;cycle<10000;cycle++){
   d.clk=0;
   if(!cv&&ci<commands.size()&&rng()%3){cv=true;d.command_w0=commands[ci][0];d.command_w1=commands[ci][1];}
   if(pending&&!rv){if(delay)delay--;else rv=true;}
   d.command_valid=cv;d.check_ready=!pending&&rng()%3;d.result_valid=rv;
   unsigned r=response(pending_indices);d.result_fallback=r==3;d.result_reject_class=r==3?0:r;
   d.start=rng()%13==0;d.command_count=rng()%128;d.cull_mode=rng()%4;d.eval();
   bool cr=d.command_valid&&d.command_ready,ck=d.check_valid&&d.check_ready,rr=d.result_valid&&d.result_ready;
   if(cr){ci++;cv=false;}
   if(ck){need(!pending,"overlapping check");pending=true;pending_indices=d.check_indices;delay=rng()%11;actual_checks++;need(d.check_cull_mode==trial%4,"busy mode changed");}
   if(rr){pending=false;rv=false;}
   edge();
   if(d.done){finished=true;break;}
  }
  need(finished&&!d.busy&&!pending,"completion/drain failure");
  need(d.accepted_commands==accepted&&d.live_triangles==survivors.size(),"atomic prefix count mismatch");
  need(bool(d.fallback)==fallback&&bool(d.invalid)==invalid,"fallback classification mismatch");
  need(actual_checks==expected_checks&&d.checked_triangles==expected_checks,"check count mismatch");
  d.start=d.command_valid=d.result_valid=0;
  for(unsigned i=0;i<survivors.size();i++){d.list_addr=i;edge();need(d.list_indices==survivors[i],"survivor sequence mismatch");}
  d.packet_access=1;uint32_t scratch[9];
  for(unsigned i=0;i<9;i++){scratch[i]=rng();d.packet_write=1;d.packet_write_addr=i;d.packet_write_data=scratch[i];edge();}d.packet_write=0;
  for(unsigned i=0;i<9;i++){
   d.packet_read_valid=1;d.packet_read_addr=i;edge();need(d.packet_read_data==scratch[i],"packet staging mismatch");
   d.packet_read_valid=0;d.packet_read_addr=rng()%16;edge();need(d.packet_read_data==scratch[i],"packet staging read did not hold");
  }
  d.packet_access=0;
  for(unsigned i=0;i<survivors.size();i++){d.list_addr=i;edge();need(d.list_indices==survivors[i],"packet staging corrupted survivor list");}
  checks+=actual_checks;retained+=survivors.size();jobs++;edge();
 }
 std::cout<<"{\"passed\":true,\"jobs\":"<<jobs<<",\"triangle_checks\":"<<checks<<",\"survivors\":"<<retained<<",\"TRI2_atomic_rollbacks\":"<<atomic_rollbacks<<"}\n";
 }catch(const std::exception&e){std::cerr<<"job "<<jobs<<": "<<e.what()<<"\n";return 1;}
}
