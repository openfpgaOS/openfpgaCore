#include "Vtb_triangle_preflight.h"
#include <verilated.h>
#include <array>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <vector>
struct Case {std::array<uint32_t,14>in;std::array<uint32_t,11>out;};
struct Test {
 Vtb_triangle_preflight d;uint64_t cycles=0,checks=0,fpchecks=0;unsigned ops=0;bool bad=false,observe=false;const Case*cur=nullptr;
 void check(bool ok,const char*what){checks++;if(!ok)throw std::runtime_error(std::string(what)+" atcycle="+std::to_string(cycles)+" op="+std::to_string(ops));}
 void tick(){d.clk=0;d.eval();check(!d.memory_collision,"scratch read/write collision");
  if(observe&&d.fma_result_valid){check(ops<9,"unsolicited arithmetic result");if(!bad&&!d.fma_unsupported){fpchecks++;if(d.fma_result!=cur->out[ops]){std::cerr<<std::hex<<"got "<<d.fma_result<<" expected "<<cur->out[ops]<<std::dec<<"\n";check(false,"current CPU FP result mismatch");}}bad|=d.fma_unsupported;ops++;}
  d.clk=1;d.eval();cycles++;
 }
 void reset(){observe=false;d.reset=1;d.reg_wr=0;d.raw_we=0;d.alias_active=0;d.copy_busy=0;d.conflict=0;d.generation=7;d.source_valid=1;d.producer_active=0;d.source_count=3;d.reg_wstrb=15;for(int i=0;i<9;i++)tick();d.reset=0;tick();check(!d.busy&&!d.reserve&&!d.port_owner,"reset owner");}
 void wr(unsigned a,uint32_t v,unsigned strb=15){d.reg_wr=1;d.reg_addr=a;d.reg_wdata=v;d.reg_wstrb=strb;tick();d.reg_wr=0;d.reg_wstrb=15;}
 uint32_t rd(unsigned a){d.reg_addr=a;d.clk=0;d.eval();return d.reg_rdata;}
 void raw(unsigned a,uint32_t v){d.raw_we=1;d.raw_write_addr=a;d.raw_write_data=v;tick();d.raw_we=0;}
 void fill(const Case&c){for(unsigned j=0;j<3;j++){raw(j,c.in[j*3]);raw(32+j,c.in[j*3+1]);raw(128+j,c.in[j*3+2]);raw(96+j,(0x3f800000u+j*0x800000u)|((c.in[9]>>j&1)<<31));raw(224+j,c.in[10+j]);}}
 void setup(unsigned n,unsigned mode){wr(0x14,2);wr(0x12,7);wr(0x13,n|(mode<<8));}
 void start(){wr(0x14,1);}
 void wait(){for(unsigned n=0;d.busy;n++){check(n<10000,"preflight timeout");tick();}check(rd(0x14)&2,"missing done");}
 void stable(){std::array<uint32_t,6>a;for(int i=0;i<6;i++)a[i]=rd(0x14+i);for(int n=0;n<11;n++){tick();for(int i=0;i<6;i++)check(a[i]==rd(0x14+i),"unstable completed result");}}
};
int main(int argc,char**argv)try{
 Verilated::commandArgs(argc,argv);if(argc!=3)return 2;std::ifstream a(argv[1],std::ios::binary),b(argv[2],std::ios::binary);std::vector<Case>v;
 for(Case c;a.read(reinterpret_cast<char*>(c.in.data()),56);){if(!b.read(reinterpret_cast<char*>(c.out.data()),44))return 3;v.push_back(c);}
 Test t;t.reset();t.check(t.rd(0x11)==0x54465031,"capability");unsigned supported=0,fallback=0,arithmetic=0;
 for(unsigned index=0;index<v.size();index++){
  auto&c=v[index];t.fill(c);t.setup(1,c.in[13]);t.wr(0x200,0|(1<<5)|(2<<10));t.cur=&c;t.ops=0;t.bad=false;t.observe=true;t.start();t.wait();t.observe=false;
  bool math=!(c.in[10]&c.in[11]&c.in[12])&&(c.in[13]==1||c.in[13]==2);t.check(t.ops==(math?9:0),"operation count");arithmetic+=math;
  bool fb=t.bad||(c.out[9]==0&&((c.in[10]|c.in[11]|c.in[12])||c.in[9]));
  t.check(t.rd(0x14)==(2u|(fb?4u:0u)),"status mismatch");t.check(t.rd(0x15)==(!fb&&!c.out[9]),"keep mismatch");t.check(t.rd(0x16)==0,"unexpected high keep");t.check(t.rd(0x17)==unsigned(fb)&&t.rd(0x18)==0,"fallback mismatch");t.check(t.rd(0x19)==unsigned(!fb&&c.out[9]==1),"clip count mismatch");t.stable();if(fb)fallback++;else supported++;
 }
 // Directed full64 boundary, malformed descriptors, source/config contracts,
 // accepted alias/copy drain, mutation rejection and reset at every FSM state.
 Case basic={{0x3f000000,0x3f000000,0x3f800000,0,0,0x3f000000,0xbf000000,0x3f000000,0x3e800000,0,0,0,0,0},{}};
 t.reset();t.fill(basic);t.setup(64,0);for(unsigned i=0;i<64;i++)t.wr(0x200+i,0|(1<<5)|(2<<10));t.start();t.wait();t.check(t.rd(0x15)==0xffffffff&&t.rd(0x16)==0xffffffff,"64bit keep");
 t.setup(64,0);t.wr(0x23f,0x80000000);t.start();t.wait();t.check(t.rd(0x18)==0x80000000&&t.rd(0x16)==0x7fffffff&&(t.rd(0x14)&8),"malformed63");
 for(unsigned config:{0u,65u,128u,1025u}){t.setup(1,0);t.wr(0x13,config);t.start();t.check(!t.d.busy&&t.rd(0x14)==14,"invalidconfiguration");}
 t.reset();t.fill(basic);t.setup(1,0);t.start();t.wait();t.check(t.rd(0x17)==1&&(t.rd(0x14)&8),"unwritten descriptor");
 for(unsigned fail=0;fail<4;fail++){t.reset();t.fill(basic);t.setup(1,0);t.wr(0x200,0|(1<<5)|(2<<10));if(fail==0)t.wr(0x12,8);if(fail==1)t.d.source_valid=0;if(fail==2)t.d.producer_active=1;if(fail==3)t.d.source_count=0;t.start();t.check(!t.d.busy&&t.rd(0x14)==14,"invalidsource");}
 t.reset();t.fill(basic);t.setup(1,0);t.wr(0x200,0|(1<<5)|(2<<10));t.d.alias_active=1;t.d.copy_busy=1;t.start();for(int i=0;i<13;i++){t.tick();t.check(t.d.busy&&t.d.reserve&&!t.d.port_owner,"alias drain ownership");}t.d.alias_active=0;for(int i=0;i<7;i++){t.tick();t.check(!t.d.port_owner,"copy drain ownership");}t.d.copy_busy=0;t.wait();t.check(t.rd(0x15)==1,"drained result");
 for(unsigned state=1;state<=8;state++){t.reset();t.fill(basic);t.setup(1,1);t.wr(0x200,0|(1<<5)|(2<<10));t.start();for(unsigned i=0;t.d.state!=state;i++){t.check(i<500,"unreachable state");t.tick();}t.reset();for(int i=0;i<20;i++)t.tick();t.check(t.rd(0x14)==0&&t.rd(0x15)==0&&t.rd(0x17)==0,"reset residual result");}
 t.reset();t.fill(basic);t.setup(1,0);t.wr(0x200,0|(1<<5)|(2<<10));t.d.alias_active=1;t.start();t.wr(0x200,0xffffffff);t.wr(0x12,99);t.wr(0x14,2);t.d.conflict=1;t.tick();t.d.conflict=0;t.d.alias_active=0;t.wait();t.check(t.rd(0x15)==1&&(t.rd(0x14)&8)&&t.rd(0x12)==7,"mutation changed active work");
 t.reset();t.wr(0x13,1,7);t.check(t.rd(0x14)==8&&t.rd(0x13)==0,"partialwrite accepted");
 std::cout<<"{\"pass\":true,\"native_vectors\":"<<v.size()<<",\"arithmetic_triangles\":"<<arithmetic<<",\"supported\":"<<supported<<",\"fallback\":"<<fallback<<",\"exact_FP_stage_checks\":"<<t.fpchecks<<",\"checks\":"<<t.checks<<",\"cycles\":"<<t.cycles<<"}\n";return 0;
}catch(const std::exception&e){std::cerr<<"FAIL "<<e.what()<<"\n";return 1;}
