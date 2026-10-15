#include "Vgeometry_load_alias.h"
#include <verilated.h>
#include <array>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <vector>
struct Test{
 Vgeometry_load_alias v;uint64_t cycles=0,checks=0;std::vector<uint32_t>results;
 void check(bool b,const char*m){++checks;if(!b)throw std::runtime_error(std::string(m)+" at "+std::to_string(cycles));}
 void settle(){v.clk=0;v.eval();}
 void tick(){settle();if(v.math_fma_result_valid){check(v.math_grant,"result escaped ownership");results.push_back(v.math_fma_result);}v.clk=1;v.eval();++cycles;settle();}
 uint32_t rd(unsigned r){v.reg_addr=r;settle();return v.reg_rdata;}
 void wr(unsigned r,uint32_t x,unsigned s=15){v.reg_addr=r;v.reg_wdata=x;v.reg_wstrb=s;v.reg_wr=1;tick();v.reg_wr=0;settle();}
 void reset(){v.reset=1;v.import_reset=0;v.import_req=0;v.import_read_done=0;v.import_release=0;v.import_fault=0;v.math_req=0;v.math_release=0;v.math_fma_valid=0;v.math_scratch_read=0;v.math_scratch_write=0;v.view_active=0;v.reg_wr=0;v.m_arready=0;v.m_rvalid=0;tick();v.reset=0;for(int i=0;i<10;i++)tick();results.clear();check(!v.math_grant&&!v.m_arvalid&&rd(0x1b)==0,"reset publication");for(unsigned i=0;i<16;i++)wr(0x40+i,0x3f800000);wr(3,0x1200);wr(4,4);}
 void own(){v.math_req=1;for(unsigned n=0;!v.math_grant;n++){check(n<20,"math grant timeout");tick();}v.math_req=0;settle();}
 void release(){v.math_release=1;tick();v.math_release=0;settle();check(!v.math_grant,"math release after drain");}
 void issue(uint32_t a,uint32_t b,uint32_t c=0,bool multiply=true){check(v.math_grant,"request has grant");v.math_fma_a=a;v.math_fma_b=b;v.math_fma_c=c;v.math_fma_multiply=multiply;v.math_fma_valid=1;tick();v.math_fma_valid=0;settle();}
 void accept(){for(unsigned n=0;!v.m_arvalid;n++){check(n<40,"raw pending never starts");tick();}check(v.m_araddr==0x1200,"queued raw context survives");v.m_arready=1;tick();v.m_arready=0;check(v.m_rready,"accepted raw owns response");}
 void complete(unsigned error=0){v.m_rvalid=1;v.m_rlast=1;v.m_rresp=error;tick();v.m_rvalid=0;v.m_rlast=0;v.m_rresp=0;for(unsigned n=0;rd(1)&1;n++){check(n<30,"raw FMA never completed");tick();}check((rd(1)&6)==(error?2u:6u),"raw consumed wrong-owner FMA result");}
};
int main(int argc,char**argv)try{
 Verilated::commandArgs(argc,argv);Test t;t.reset();
 t.check(t.rd(0x1a)==0x51474631&&t.rd(0x11)==0,"QGF capability only; TFP absent");
 // GEO forwarding retains data/strobes; only the repair window reads it.
 t.v.queue_reg_rdata=0xabcddcba;t.v.reg_wr=1;t.v.reg_addr=0x24;t.v.reg_wdata=7;t.v.reg_wstrb=3;t.settle();
 t.check(t.v.queue_reg_wr&&t.v.queue_reg_addr==0x24&&t.v.queue_reg_wdata==7&&t.v.queue_reg_wstrb==3&&t.v.reg_rdata==0xabcddcba,"repair MMIO forwarding");t.v.reg_wr=0;t.check(t.rd(0x19)==0,"repair read isolation");
 // Accepted raw START has priority over a simultaneous queued math request.
 t.v.math_req=1;t.wr(2,1);t.check(!t.v.math_grant&&t.rd(0x1b)==0,"raw START priority/invalidation");t.accept();
 for(unsigned i=0;i<13;i++){t.tick();t.check(!t.v.math_grant&&t.v.m_rready,"no math grant during accepted DMA");}
 t.complete();t.check(t.rd(0xe)==1&&t.rd(0x1b)==15,"successful generation publishes metadata");t.own();t.check(t.rd(0x1c)==10&&t.rd(0x1d)==12,"metadata bitplane routing");
 // Real FMA request/result ordering, pipelined outstanding tracking and scratch.
 t.issue(0x3f800000,0x40000000);t.issue(0x40000000,0x40000000);t.issue(0x40400000,0x40000000);
 for(unsigned i=0;i<12;i++)t.tick();t.check(t.results==std::vector<uint32_t>({0x40000000,0x40800000,0x40c00000}),"queued FMA ordered results");
 for(unsigned i=0;i<6;i++){t.v.math_scratch_write=1;t.v.math_scratch_write_addr=i;t.v.math_scratch_write_data=0x12121200+i;t.tick();}t.v.math_scratch_write=0;
 for(unsigned i=0;i<6;i++){t.v.math_scratch_read=1;t.v.math_scratch_read_addr=i;t.tick();t.check(t.v.math_scratch_data==0x12121200+i,"shared scratch exact synchronous read");}t.v.math_scratch_read=0;
 // START accepted while math owns is queued, rather than lost/rejected.
 t.wr(2,1);t.check((t.rd(1)&1)&&!t.v.m_arvalid&&t.rd(0x1b)==0,"raw START queued behind cull");
 for(unsigned i=0;i<5;i++){t.tick();t.check(!t.v.m_arvalid,"raw cannot steal math owner");}t.release();t.accept();t.complete();t.check(t.rd(0xe)==2,"queued raw finishes correct generation");
 // Consumer reset at each pipeline age must drain old FMA before raw begins;
 // the raw stub verifies its result is 2*3, not a cancelled cull result.
 for(unsigned age=0;age<9;age++){
  t.reset();t.own();t.issue(0x40800000,0x40a00000);for(unsigned i=0;i<age;i++)t.tick();
  t.v.import_reset=1;t.wr(2,1);t.check(!t.v.math_grant,"consumer reset hides queued result/grant");t.v.import_reset=0;
  t.accept();t.complete();t.check(t.rd(0xe)==1&&t.rd(0x1b)==15,"cancelled FMA cannot contaminate next raw LOAD");
 }
 // Accepted raw DMA, its FMA, and metadata survive consumer-only resets.
 for(unsigned age=0;age<10;age++){
  t.reset();t.wr(2,1);t.accept();t.v.import_reset=1;
  for(unsigned i=0;i<5;i++){t.tick();t.check(t.v.m_rready&&!t.v.math_grant,"consumer reset preserves accepted raw DMA");}
  t.v.import_reset=0;t.v.m_rvalid=1;t.v.m_rlast=1;t.tick();t.v.m_rvalid=0;t.v.m_rlast=0;
  for(unsigned i=0;i<age;i++)t.tick();t.v.import_reset=1;t.tick();t.v.import_reset=0;
  for(unsigned n=0;t.rd(1)&1;n++){t.check(n<30,"raw compute survives consumer reset");t.tick();}
  t.check((t.rd(1)&6)==6&&t.rd(0xe)==1&&t.rd(0x1b)==15,"raw FMA completion survives consumer reset");
 }
 // CLEAR and failed DMA cannot authorize stale result metadata.
 t.wr(2,2);t.check(t.rd(0x1b)==0,"CLEAR disables old metadata");t.wr(2,1);t.accept();t.complete(2);t.check(t.rd(0x1b)==0&&t.rd(0xe)==1,"failed LOAD masks stale/partial metadata");
 // Fabric reset is the only reset that discards all producers and owners.
 t.own();t.issue(0x3f800000,0x40000000);t.reset();for(unsigned i=0;i<12;i++)t.tick();t.check(t.results.empty()&&t.rd(0xe)==0&&!t.v.math_grant,"fabric reset clears all FMA owner state");
 std::cout<<"{\"pass\":true,\"checks\":"<<t.checks<<",\"cycles\":"<<t.cycles<<",\"scope\":\"Actual QGF phase10, BIM2 and shared FMA; transaction producer uses one actual FMA operation\"}\n";return 0;
}catch(const std::exception&e){std::cerr<<"FAIL "<<e.what()<<"\n";return 1;}
