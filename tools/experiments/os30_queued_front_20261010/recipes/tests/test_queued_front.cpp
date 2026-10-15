#include "Vtb_queued_front.h"
#include <verilated.h>
#include <algorithm>
#include <array>
#include <cfenv>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <vector>
struct Case{std::array<uint32_t,14>in;std::array<uint32_t,11>out;};
using Raw=std::array<uint32_t,256>;
static float fl(uint32_t w){float f;std::memcpy(&f,&w,4);return f;}
static uint32_t quant(uint32_t w,int scale){return uint32_t(int32_t(std::nearbyint(std::ldexp(double(fl(w)),scale))));}
struct Context{unsigned first=0,mode=0;uint32_t u=0,v=0,flags=0x4567;};
static uint32_t job(unsigned a,unsigned b,unsigned c){return a|(b<<5)|(c<<10);}
static std::vector<uint32_t> run_words(uint32_t token,const Context&c,const std::vector<uint32_t>&jobs){std::vector<uint32_t>o={0x60000000u+uint32_t(5+jobs.size()),token,c.first|(c.mode<<5)|(255u<<8)|(uint32_t(jobs.size())<<16),c.u,c.v,c.flags};o.insert(o.end(),jobs.begin(),jobs.end());return o;}
static std::vector<uint32_t> expected(const Raw&r,const Context&c,const std::vector<uint32_t>&jobs,uint64_t keep){
 std::vector<uint32_t>o;uint32_t used=0;
 for(unsigned j=0;j<jobs.size();j++)if((keep>>j)&1)for(unsigned k=0;k<3;k++)used|=1u<<((jobs[j]>>(5*k))&31);
 for(unsigned i=0;i<32;i++)if((used>>i)&1){
  uint32_t uv=r[160+i];int32_t u=int16_t(uv),v=int16_t(uv>>16);uint32_t tu=0,tv=0;
  if(c.flags&0x10000){tu=uint32_t(u-int32_t(c.u)+((c.flags&0x20000)?16:0))*2048u;tv=uint32_t(v-int32_t(c.v)+((c.flags&0x20000)?16:0))*2048u;}
  uint32_t rgba=r[192+i],rgb=c.flags&0xffff;if(c.flags&0x40000)rgb=((rgba>>3)&31)<<11|((rgba>>10)&63)<<5|((rgba>>19)&31);
  double d=std::ldexp(double(fl(r[128+i])),30);d=std::max(1.0,std::min(2147483520.0,d));
  std::vector<uint32_t>p={0x56000008,c.first+i,quant(r[i],8),quant(r[32+i],8),quant(r[96+i],8),tu,tv,rgb,uint32_t(std::nearbyint(d))};o.insert(o.end(),p.begin(),p.end());
 }
 for(unsigned j=0;j<jobs.size();j++)if((keep>>j)&1){o.push_back(0x54000001);uint32_t x=0;for(unsigned k=0;k<3;k++)x|=((((jobs[j]>>(k*5))&31)+c.first)<<(k*5));o.push_back(x);}return o;
}
struct Test{
 Vtb_queued_front d;uint64_t cycles=0,checks=0,fpchecks=0;unsigned repairs=0,runs=0,releases=0,ops=0;bool bad=false,watch=false;const Case*oracle=nullptr;
 std::vector<uint32_t>published,consumed;
 void check(bool b,const char*m){++checks;if(!b)throw std::runtime_error(std::string(m)+" cycle="+std::to_string(cycles)+" state="+std::to_string(d.state)+" input="+std::to_string(d.input_rdptr)+"/"+std::to_string(d.input_wrptr)+" output="+std::to_string(d.output_rdptr)+"/"+std::to_string(d.output_wrptr));}
 void settle(){d.clk=0;d.eval();}
 void tick(){settle();if(d.output_push)published.push_back(d.output_word);if(d.view_release)++releases;
  if(watch&&d.fma_result_valid){check(ops<9,"extra FMA result");if(!bad&&!d.fma_bad){++fpchecks;check(d.fma_result==oracle->out[ops],"actual CPU arithmetic stage");}bad|=d.fma_bad;++ops;}
  d.clk=1;d.eval();++cycles;settle();}
 void reset(){watch=false;d.reset_n=0;d.enable=1;d.soft_reset=0;d.ring_reset=0;d.input_wrptr=0;d.output_rdptr=0;d.input_we=0;d.raw_we=0;d.claim=0;d.producer_success=0;d.producer_valid=1;d.producer_busy=0;d.producer_count=32;d.math_allow=1;d.queue_reg_wr=0;tick();d.reset_n=1;for(int i=0;i<10;i++)tick();published.clear();consumed.clear();releases=0;check(d.free_banks==3&&!d.view_owner&&!d.math_grant,"reset owners");}
 void soft(){watch=false;d.soft_reset=1;d.output_rdptr=0;tick();d.soft_reset=0;for(unsigned i=0;i<10;i++)tick();published.clear();consumed.clear();releases=0;check(d.free_banks==3&&!d.view_owner&&!d.math_grant,"consumer reset owners");}
 void wr(unsigned a,uint32_t x,unsigned strobe=15){d.queue_reg_addr=a;d.queue_reg_wdata=x;d.queue_reg_wstrb=strobe;d.queue_reg_wr=1;tick();d.queue_reg_wr=0;settle();}
 uint32_t rd(unsigned a){d.queue_reg_addr=a;settle();return d.queue_reg_rdata;}
 void raw(const Raw&r){for(unsigned i=0;i<256;i++){d.raw_we=1;d.raw_write_addr=i;d.raw_write_data=r[i];tick();}d.raw_we=0;}
 uint32_t snapshot(const Raw&r){raw(r);d.producer_success=1;tick();d.producer_success=0;d.claim_token=d.generation;d.claim=1;tick();d.claim=0;check(d.claim_result&1,"snapshot accepted");for(unsigned i=0;d.copy_busy;i++){check(i<270,"snapshot copy drains");tick();}return d.generation;}
 void enqueue(const std::vector<uint32_t>&words){uint32_t w=d.input_wrptr;for(uint32_t x:words){check(((w+1)&4095)!=d.input_rdptr,"test ingress full");d.input_we=1;d.input_write_addr=w;d.input_write_data=x;tick();w=(w+1)&4095;}d.input_we=0;
  // Data upload and doorbell are separate hardware writes. Refresh the
  // synchronous head read after its possible same-address RAM write.
  tick();d.input_wrptr=w;settle();}
 void drain_one(){if(d.output_rdptr==d.output_wrptr)return;d.output_addr=d.output_rdptr;tick();uint32_t x=d.output_data;consumed.push_back(x);d.output_rdptr=(d.output_rdptr+1)&4095;tick();}
 void wait(bool drain=true){for(unsigned i=0;d.busy || (drain&&d.output_rdptr!=d.output_wrptr);i++){check(i<50000,"worker/drain timeout");if(rd(0x20)&2)throw std::runtime_error("unexpected repair");if(drain&&d.output_rdptr!=d.output_wrptr&&(i%3))drain_one();else tick();}for(int i=0;i<3;i++)tick();if(drain)check(consumed==published,"output FIFO matches write handshake");}
 void repair(const Case&c,bool malformed=false){
  check(rd(0x20)&2,"repair pending");
  // Release is a registered pulse consumed by the arbiter on the next edge.
  if(d.math_grant){check(d.math_release,"repair requests FMA release");tick();}
  check(!d.math_grant,"repair releases FMA owner");uint32_t serial=rd(0x21);check(rd(0x22)==job(0,1,2),"repair saved indices");check((rd(0x23)&3)==c.in[13],"repair saved cull mode");
  for(unsigned k=0;k<9;k++){wr(0x24,k);tick();check(rd(0x25)==c.in[k],"immutable repair operand mapping");}
  if(malformed){wr(0x2e,serial-1);wr(0x2d,3);check(rd(0x20)&2,"stale ACK must preserve pending");wr(0x24,9);check(rd(0x20)&2,"invalid operand keeps request");wr(0x2e,serial);wr(0x2d,3,7);check(rd(0x20)&2,"partial ACK keeps request");}
  wr(0x2e,serial);wr(0x2d,1u|(unsigned(c.out[9]==0)<<1));check(!(rd(0x20)&2),"matching repair accepted");++repairs;
 }
};
static Raw basic(){Raw r{};for(unsigned i=0;i<32;i++){r[i]=i%2?0x3f000000:0xbf000000;r[32+i]=i%3?0x3e800000:0xbf000000;r[96+i]=0x3f800000+(i%3)*0x800000;r[128+i]=0x3f800000-(i%3)*0x800000;r[160+i]=uint32_t(uint16_t(int(i)*47-300))|(uint32_t(uint16_t(500-int(i)*23))<<16);r[192+i]=0xff7f3f1f+i*7919;}return r;}
int main(int argc,char**argv)try{
 Verilated::commandArgs(argc,argv);std::fesetround(FE_TONEAREST);if(argc!=3)return 2;
 std::ifstream in(argv[1],std::ios::binary),out(argv[2],std::ios::binary);std::vector<Case>cases;for(Case c;in.read((char*)c.in.data(),56);){if(!out.read((char*)c.out.data(),44))return 3;cases.push_back(c);}
 Test t;t.reset();Raw r=basic();Context c;c.first=3;c.flags=0x70000|0x55aa;c.u=8;c.v=524280;auto token=t.snapshot(r);
 // Ordinary headers/payloads pass through unchanged, even payloads whose high
 // byte resembles a private opcode. Two runs reuse one generation until0x61.
 std::vector<uint32_t>jobs={job(0,1,2),job(3,1,2),job(3,3,4)};
 std::vector<uint32_t>before={0x50000002,0x60000001,0x61abcdef},between={0x55000001,0xff};
 t.enqueue(before);t.enqueue(run_words(token,c,jobs));t.enqueue(between);t.enqueue(run_words(token,c,{job(4,2,0)}));t.enqueue({0x61000001,token,0x42000000});
 // Raw contents and generation may change after copying, before conversion.
 Raw overwritten{};t.raw(overwritten);t.d.producer_success=1;t.tick();t.d.producer_success=0;t.wait();
 auto want=before;auto a=expected(r,c,jobs,7);want.insert(want.end(),a.begin(),a.end());want.insert(want.end(),between.begin(),between.end());a=expected(r,c,{job(4,2,0)},1);want.insert(want.end(),a.begin(),a.end());want.push_back(0x42000000);
 t.check(t.published==want,"ordered pass-through/run/second run/release output");t.check(t.releases==1&&t.d.free_banks==3&&!t.d.view_error_sticky,"one explicit release after last generation use");
 // Geometry ownership ends once values are queued, with raster consumption
 // completely stopped. Reuse the freed bank before reading any queued value.
 t.reset();token=t.snapshot(r);Context independent;t.enqueue(run_words(token,independent,{job(0,1,2)}));t.enqueue({0x61000001,token});t.wait(false);
 t.check(t.d.output_rdptr==0&&t.d.output_wrptr==29&&t.d.free_banks==3&&t.releases==1,"release before any raster consumption");
 t.snapshot(overwritten);t.wait();t.check(t.consumed==expected(r,independent,{job(0,1,2)},1),"reused snapshot bank cannot change queued values");
 // Stalled consumer fills the511 usable words. No0x61/release can overtake a
 // blocked value copy; draining later must retain exact commands across wrap.
 t.reset();token=t.snapshot(r);Context plain;std::vector<uint32_t>many;for(unsigned i=0;i<64;i++)many.push_back(job(i%32,(i+1)%32,(i+2)%32));
 std::vector<uint32_t>pass(400,0x12345678);pass.insert(pass.begin(),0x4a000190);t.enqueue(pass);t.enqueue(run_words(token,plain,many));t.enqueue({0x61000001,token});
 for(unsigned i=0;i<5000;i++)t.tick();t.check(((t.d.output_wrptr-t.d.output_rdptr)&4095)==511,"output capacity bounded");t.check(t.releases==0&&t.d.free_banks!=3&&t.d.view_owner,"snapshot held while value output blocked");
 t.wait();want=pass;a=expected(r,plain,many,~uint64_t(0));want.insert(want.end(),a.begin(),a.end());t.check(t.published==want&&t.d.free_banks==3,"pressure preserves exact output and frees snapshot");
 // Repeated long pass-through packets force both ingress and output pointer
 // wrap, with no hidden private-opcode decode inside payloads.
 t.published.clear();t.consumed.clear();want.clear();for(unsigned batch=0;batch<12;batch++){std::vector<uint32_t>p={0x4e000190};for(unsigned i=0;i<400;i++)p.push_back(0x60000000u+batch*400+i);t.enqueue(p);t.wait();want.insert(want.end(),p.begin(),p.end());}t.check(t.published==want,"ring/FIFO pointer wrap");
 // Current CPU oracle through actual snapshot synchronous port/cull pipeline.
 unsigned arithmetic_cases=0,natural_repairs=0;
 for(const auto&entry:cases){
  t.reset();r=basic();for(unsigned j=0;j<3;j++){r[j]=entry.in[j*3];r[32+j]=entry.in[j*3+1];r[128+j]=entry.in[j*3+2];r[96+j]=0x46fff800+j*0x200;}
  token=t.snapshot(r);c={};c.mode=entry.in[13];t.oracle=&entry;t.ops=0;t.bad=false;t.watch=true;t.d.math_allow=0;t.enqueue(run_words(token,c,{job(0,1,2)}));
  if(c.mode==1||c.mode==2){for(unsigned n=0;t.d.state!=9;n++){t.check(n<40,"math request expected");t.tick();}for(unsigned i=0;i<19;i++){t.tick();t.check(!t.d.math_grant&&!t.d.output_push,"delayed math grant does not publish");}}
  t.d.math_allow=1;for(unsigned n=0;t.d.busy;n++){
   t.check(n<3000,"oracle run timeout");if(t.rd(0x20)&2){t.check(t.published.empty()&&t.bad,"repair before any run output");t.repair(entry);++natural_repairs;}else t.tick();
  }t.watch=false;t.wait();t.check(t.ops==((c.mode==1||c.mode==2)?9u:0u),"nine-op/no-cull count");a=expected(r,c,{job(0,1,2)},entry.out[9]==0);t.check(t.published==a,"native CPU keep and exact converted payload");t.check(t.d.free_banks!=3,"run finish alone retains generation");t.enqueue({0x61000001,token});t.wait();t.check(t.d.free_banks==3,"explicit generation release");++arithmetic_cases;
 }
 t.check(natural_repairs>0,"natural late arithmetic exceptions covered");
 // A late repair on job2 publishes neither the already-accepted job1 nor any
 // vertex. Bad/stale ACKs recover without mutating the immutable request.
 const Case*repair_case=nullptr;for(const auto&entry:cases)if(entry.out[0]&&std::any_of(entry.out.begin(),entry.out.begin()+9,[](uint32_t w){return(w&0x7fffffff)&&!(w&0x7f800000);}))repair_case=&entry;
 t.check(repair_case!=nullptr,"repair boundary oracle available");auto exception=*repair_case;
 t.reset();r=basic();for(unsigned j=0;j<3;j++){r[j]=exception.in[3*j];r[32+j]=exception.in[3*j+1];r[128+j]=exception.in[3*j+2];}
 token=t.snapshot(r);c={};c.mode=exception.in[13];t.enqueue(run_words(token,c,{job(3,4,5),job(0,1,2)}));
 for(unsigned n=0;!(t.rd(0x20)&2);n++){t.check(n<500,"late repair pending");t.tick();}t.check(t.published.empty(),"whole-batch atomicity before arithmetic repair");uint32_t old_serial=t.rd(0x21);t.repair(exception,true);t.wait();t.check(t.rd(0x20)&4,"bad ACK sticky error recorded");
 // Consumer reset separates serial epochs. A pre-reset ACK cannot satisfy a
 // new repair even when both repairs are the first in their respective runs.
 t.soft();token=t.snapshot(r);t.enqueue(run_words(token,c,{job(0,1,2)}));for(unsigned n=0;!(t.rd(0x20)&2);n++){t.check(n<500,"post-reset repair pending");t.tick();}
 t.check(t.rd(0x21)!=old_serial,"repair serial reset epoch");t.wr(0x2e,old_serial);t.wr(0x2d,3);t.check(t.rd(0x20)&2,"pre-reset ACK rejected");t.repair(exception);t.wait();
 // Invalid headers/tokens halt until reset, and leak no draw values.
 for(unsigned failure=0;failure<3;failure++){
  t.reset();token=t.snapshot(basic());auto w=run_words(token,Context{}, {job(0,1,2)});if(failure==0)w[0]=0x60000005;if(failure==1)w[1]=token+17;if(failure==2)w.back()|=0x80000000;t.enqueue(w);
  for(unsigned n=0;t.d.state!=25;n++){t.check(n<150,"malformed input halts");t.tick();}t.check(t.published.empty()&&(t.rd(0x20)&4),"malformed input emits no commands");t.soft();t.enqueue({0x42000000});t.wait();t.check(t.published==std::vector<uint32_t>{0x42000000},"soft reset recovers parser");
 }
 std::cout<<"{\"pass\":true,\"checks\":"<<t.checks<<",\"cycles\":"<<t.cycles<<",\"current_cpu_vectors\":"<<arithmetic_cases<<",\"exact_FP_stage_checks\":"<<t.fpchecks<<",\"natural_arithmetic_repairs\":"<<natural_repairs<<",\"total_repairs\":"<<t.repairs<<"}\n";return 0;
}catch(const std::exception&e){std::cerr<<"FAIL "<<e.what()<<"\n";return 1;}
