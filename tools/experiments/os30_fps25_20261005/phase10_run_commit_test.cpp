#include "Vrun_commit_top.h"
#include "verilated.h"
#include <algorithm>
#include <array>
#include <cfenv>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <random>
#include <stdexcept>
#include <vector>

static void need(bool v,const char *s){if(!v)throw std::runtime_error(s);}
static uint32_t bits(float f){uint32_t u;memcpy(&u,&f,4);return u;}
static float flt(uint32_t u){float f;memcpy(&f,&u,4);return f;}
using Raw=std::array<std::array<uint32_t,8>,32>;
struct Job {
 unsigned count=0;uint32_t flags=0,stale=0,prim=0,env=0;uint16_t uls=0,ult=0;bool linear=false;
 std::array<uint16_t,64> triangles{};Raw raw{};
};
struct Expected {bool bad=false,invalid=false;uint32_t stale=0;unsigned reserved=0,vertices=0;std::vector<uint32_t> prefix;};
static bool quant(uint32_t value,unsigned scale,bool depth,uint32_t &word){
 float q=std::ldexp(flt(value),scale);
 if(depth){if(q<1)q=1;else if(q>2147483520.f)q=2147483520.f;}
 double n=std::nearbyint(double(q));
 if(!std::isfinite(n)||n>2147483647.||n< -2147483648.)return false;
 word=uint32_t(int32_t(n));return true;
}
static bool packet(const Job &j,unsigned slot,std::array<uint32_t,9>&p){
 const auto &r=j.raw[slot];p[0]=0x56000008;p[1]=slot;
 if(!quant(r[0],8,false,p[2])||!quant(r[1],8,false,p[3])||!quant(r[3],8,false,p[4])||!quant(r[4],30,true,p[8]))return false;
 p[5]=p[6]=0;
 if(j.flags&1){
  p[5]=uint32_t((int64_t(int16_t(r[5]))-int64_t(j.uls)*8)*2048+(j.linear?32768:0));
  p[6]=uint32_t((int64_t(int16_t(r[5]>>16))-int64_t(j.ult)*8)*2048+(j.linear?32768:0));
 }
 unsigned source=j.flags>>8&255;uint32_t c=source==255?0xffffff:source==3?j.prim:source==4?r[6]:source==5?j.env:0;
 p[7]=((c&255)>>3)<<11|((c>>8&255)>>2)<<5|((c>>16&255)>>3);return true;
}
static Expected reference(const Job &j){
 Expected e;e.stale=j.stale;unsigned rgb=j.flags>>8&255;
 e.invalid=j.count>64||!(rgb<=6||rgb==255)||(j.flags&0xffff00feu);
 if(e.invalid){e.bad=true;return e;}
 uint32_t mask=j.stale;bool failed=false;
 for(unsigned i=0;i<j.count;i++){
  unsigned ix=j.triangles[i];uint32_t used=0;unsigned dirty=0;
  for(unsigned k=0;k<3;k++){unsigned slot=ix>>(5*k)&31;dirty|=((mask>>slot)&1)<<k;used|=1u<<slot;}
  e.reserved+=2+9*unsigned(__builtin_popcount(dirty));
  for(unsigned k=0;k<3;k++)if(dirty>>k&1){
   if(!failed){std::array<uint32_t,9> p;
    if(packet(j,ix>>(5*k)&31,p)){e.prefix.insert(e.prefix.end(),p.begin(),p.end());e.vertices++;}
    else failed=true;
   }
  }
  if(!failed){e.prefix.push_back(0x54000001);e.prefix.push_back(ix);}
  mask&=~used;
 }
 e.bad=failed;e.stale=failed?j.stale:mask;return e;
}
struct Test {
 Vrun_commit_top d;std::mt19937 rng{0x340972};Raw raw{};Expected expected{};
 std::array<uint32_t,4096> memory{};std::vector<uint32_t> published,staged;
 unsigned writeptr=4080,remaining=0,delay=0,grant_delay=0,reserved=0;bool owned=false,done_pulse=false,valid_held=false,request_held=false;
 uint32_t held_data=0,held_words=0;bool active_job=false;unsigned frame_start_ptr=0;
 uint64_t clocks=0,jobs=0,success=0,errors=0,aborts=0,late_aborts=0,resets=0,ownership_losses=0,words=0,stalls=0,grant_stalls=0,completion_stalls=0,duplicate_loads=0,max_reservation=0;
 void tick(bool random_ready=true){
  d.clk=0;d.geo_owned=owned;d.geo_done=done_pulse;done_pulse=false;
  d.geo_grant=!owned&&d.geo_request&&grant_delay==0&&!d.reset;
  d.geo_ready=owned&&remaining>0&&(!random_ready||rng()%4!=0)&&!d.reset;d.eval();
  // geo_request changes after eval, so settle the combinational grant again.
  d.geo_grant=!owned&&d.geo_request&&grant_delay==0&&!d.reset;d.eval();
  bool raw_read=d.raw_read_valid;unsigned address=d.raw_read_addr;
  bool take=d.geo_valid&&d.geo_ready,claim=d.geo_request&&d.geo_grant,abort=d.geo_abort;
  if(d.reset){owned=false;remaining=delay=0;staged.clear();valid_held=request_held=false;}
  else {
   if(d.geo_request){
    if(request_held)need(d.geo_words==held_words,"reservation length changed while waiting");
    request_held=!d.geo_grant;held_words=d.geo_words;
    if(grant_delay){grant_delay--;grant_stalls++;}
   }else{need(!request_held,"reservation request disappeared");request_held=false;}
   if(d.geo_valid){
    need(owned,"word offered without reservation ownership");
    if(valid_held)need(d.geo_data==held_data,"word changed under backpressure");
    valid_held=!d.geo_ready;held_data=d.geo_data;if(!d.geo_ready)stalls++;
   }else{need(!valid_held,"held output disappeared");valid_held=false;}
   if(claim){
    need(active_job&&!expected.invalid&&!owned&&d.geo_words==expected.reserved,"wrong exact reservation");
    need(d.geo_words>0&&d.geo_words<4096,"invalid reservation capacity");
    reserved=remaining=d.geo_words;max_reservation=std::max(max_reservation,uint64_t(reserved));staged.clear();owned=true;
   }
   if(take){
    need(remaining&&staged.size()<expected.prefix.size(),"extra word after expected prefix");
    if(d.geo_data!=expected.prefix[staged.size()]){
     std::cerr<<"job "<<jobs<<" word "<<staged.size()<<" got "<<std::hex<<d.geo_data<<" expected "<<expected.prefix[staged.size()]<<std::dec<<"\n";
     throw std::runtime_error("ordered word mismatch");
    }
    memory[(writeptr+staged.size())&4095]=d.geo_data;staged.push_back(d.geo_data);remaining--;words++;
    if(!remaining)delay=1+rng()%8;
   }
   if(abort){
    need(owned&&expected.bad&&remaining>0,"invalid reservation abort");
    aborts++;if(!staged.empty())late_aborts++;
    owned=false;remaining=delay=0;staged.clear();
    need(writeptr==frame_start_ptr,"abort advanced published pointer");
   }
   if(owned&&remaining==0&&delay){
    if(!take){completion_stalls++;if(--delay==0){
     need(!expected.bad&&staged==expected.prefix,"publication before complete valid output");
     for(unsigned k=0;k<staged.size();k++)need(memory[(writeptr+k)&4095]==staged[k],"ring memory changed before publish");
     published.insert(published.end(),staged.begin(),staged.end());writeptr=(writeptr+staged.size())&4095;
     owned=false;done_pulse=true;staged.clear();
    }}
   }
  }
  d.clk=1;d.eval();
  if(raw_read){d.raw_read_data=raw[address&31][address>>5];}
  d.eval();d.clk=0;d.eval();clocks++;
 }
 void reset(){d.reset=1;d.start=0;d.list_write=0;tick();tick();d.reset=0;tick();}
 void run(const Job &j,unsigned cancel_after=0,int lose_state=-1,unsigned lose_words=0){
  expected=reference(j);raw=j.raw;frame_start_ptr=writeptr;size_t before=published.size();active_job=false;
  d.start=0;d.list_write=1;d.geo_owned=0;
  for(unsigned i=0;i<64;i++){d.list_write_addr=i;d.list_write_data=j.triangles[i];tick();}
  d.list_write=0;d.live_triangles=j.count;d.draw_flags=j.flags;d.initial_stale=j.stale;d.prim_rgba=j.prim;d.env_rgba=j.env;d.tile_uls=j.uls;d.tile_ult=j.ult;d.filter_linear=j.linear;
  grant_delay=rng()%10;active_job=true;d.start=1;tick();d.start=0;unsigned n=0;bool lost=false;
  while(!d.done){
   need(n++<30000,"committer timed out");
   if(cancel_after&&n==cancel_after){
    need(published.size()==before,"cancel target published too early");reset();active_job=false;resets++;return;
   }
   if(!lost&&owned&&((lose_state>=0&&d.debug_state==unsigned(lose_state))||(lose_words&&staged.size()>=lose_words))){
    need(published.size()==before&&writeptr==frame_start_ptr,"ownership-loss target already published");
    owned=false;remaining=delay=0;staged.clear();valid_held=false;done_pulse=false;
    expected.bad=true;expected.stale=j.stale;lost=true;ownership_losses++;
   }
   d.start=d.busy&&rng()%13==0;tick();
  }
  d.start=0;need(!d.busy&&!owned,"done while busy or ring-owned");
  if(lose_state>=0||lose_words)need(lost,"ownership loss was not injected");
  need(bool(d.unsupported)==expected.bad,"unexpected unsupported status");
  need(d.final_stale==expected.stale,"wrong final stale mask");
  if(expected.bad){
   need(published.size()==before&&writeptr==frame_start_ptr,"failed run published partial data");
   need(d.emitted_words==0&&d.emitted_vertices==0,"failed run retained commit counters");errors++;
  } else {
   need(published.size()-before==expected.reserved,"successful run published wrong length");
   need(d.emitted_words==expected.reserved&&d.emitted_vertices==expected.vertices,"successful counters mismatch");success++;
  }
  jobs++;active_job=false;tick();need(!d.done,"done is not a one-cycle pulse");
 }
};
static Job make(Test &t,unsigned count,uint32_t flags,uint32_t stale,unsigned pattern){
 Job j;j.count=count;j.flags=flags;j.stale=stale;j.prim=t.rng();j.env=t.rng();j.uls=t.rng();j.ult=t.rng();j.linear=t.rng()&1;
 for(unsigned s=0;s<32;s++){
  auto &r=j.raw[s];r[0]=bits(float(int(t.rng()%16385)-8192)/512);r[1]=bits(float(int(t.rng()%16385)-8192)/512);r[2]=bits(0);
  r[3]=bits(float(t.rng()%1024+1)/32);r[4]=bits(1.f/flt(r[3]));r[5]=t.rng();r[6]=t.rng();r[7]=0;
 }
 for(unsigned i=0;i<64;i++){
  unsigned a,b,c;
  if(pattern==0)a=b=c=i%32;
  else if(pattern==1){a=i%32;b=(i+1)%32;c=(i+2)%32;}
  else {a=t.rng()%32;b=t.rng()%32;c=t.rng()%32;}
  j.triangles[i]=a|(b<<5)|(c<<10);
 }
 return j;
}
int main(int argc,char**argv){
 try{
  Verilated::commandArgs(argc,argv);std::fesetround(FE_TONEAREST);Test t;t.reset();
  for(unsigned count=0;count<=64;count++)for(unsigned pattern=0;pattern<3;pattern++)for(unsigned kind=0;kind<4;kind++){
   uint32_t stale=kind==0?0:kind==1?~0u:kind==2?1u<<(count%32):t.rng();
   unsigned selectors[]={0,1,2,3,4,5,6,255};uint32_t flags=(selectors[(count+pattern+kind)%8]<<8)|((count+kind)&1);
   t.run(make(t,count,flags,stale,pattern));
  }
  // Every malformed selector, every reserved flag bit, and all invalid counts.
  for(unsigned source=7;source<255;source++)t.run(make(t,64,(source<<8)|1,~0u,0));
  for(unsigned b=1;b<32;b++)if(b<8||b>=16)t.run(make(t,64,(4<<8)|(1u<<b),~0u,0));
  for(unsigned count=65;count<128;count++)t.run(make(t,count,4<<8,~0u,0));
  // Late arithmetic failures after a long accepted prefix must abort atomically.
  for(unsigned bad_field:{0u,1u,3u,4u})for(unsigned bad_slot=0;bad_slot<32;bad_slot++){
   auto j=make(t,64,0x401,~0u,0);j.raw[bad_slot][bad_field]=0x7fc00000;t.run(j);
   j.raw[bad_slot][bad_field]=0x7f800000;
   t.run(j); // positive infinite depth clamps; positional infinity rejects.
  }
  for(unsigned i=0;i<500;i++)t.run(make(t,t.rng()%65,((i&1)?255:4)<<8|(i&1),t.rng(),2));
  for(unsigned at:{1u,8u,40u,150u,300u,500u})t.run(make(t,64,0x401,~0u,0),at);
  // Independent media/ring reset may remove ownership while keeping this
  // controller alive. It must abort and reset any half-emitted packet locally.
  for(unsigned state=4;state<=11;state++){
   t.run(make(t,64,0x401,~0u,0),0,state);
   t.run(make(t,3,0x401,~0u,1));
  }
  for(unsigned partial:{1u,4u,8u,9u,10u,27u,99u,500u,991u}){
   t.run(make(t,64,0x401,~0u,0),0,-1,partial);
   t.run(make(t,3,0x401,~0u,1));
  }
  t.run(make(t,64,0x401,~0u,0));
  need(t.max_reservation==992,"maximum duplicated-dirty reservation was not covered");
  need(t.late_aborts>100,"insufficient late-abort coverage");
  std::cout<<"{\"passed\":true,\"jobs\":"<<t.jobs<<",\"successful\":"<<t.success<<",\"errors\":"<<t.errors<<",\"aborts\":"<<t.aborts<<",\"late_aborts\":"<<t.late_aborts<<",\"resets\":"<<t.resets<<",\"ownership_losses\":"<<t.ownership_losses<<",\"accepted_words\":"<<t.words<<",\"published_words\":"<<t.published.size()<<",\"backpressure_cycles\":"<<t.stalls<<",\"reservation_wait_cycles\":"<<t.grant_stalls<<",\"completion_wait_cycles\":"<<t.completion_stalls<<",\"max_reserved_words\":"<<t.max_reservation<<",\"cycles\":"<<t.clocks<<"}\n";
 }catch(const std::exception&e){std::cerr<<e.what()<<"\n";return 1;}
}
