#include "Vgeometry_load_alias.h"
#include "Vgeometry_load_alias___024root.h"
#define W(name) d.rootp->geometry_load_alias__DOT__async_mesh__DOT__##name
#include "verilated.h"
#include <cstdint>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <vector>
#include <cmath>
static uint32_t bits(float x){uint32_t u;memcpy(&u,&x,4);return u;}
struct Sim{
 Vgeometry_load_alias d;uint64_t cycles=0,checks=0,reads=0,beats=0;uint32_t rng=51;
 const uint32_t base=0x1000,cb=0xf000,db=0x20000;std::vector<uint32_t> mem,cmd,desc,words;unsigned descriptorFault=0;bool pending=false,blockAR=false,blockR=false,forceStall=false,held=false;uint32_t address=0,heldword=0;unsigned left=0;
 uint32_t random(){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
 void ok(bool x,const char*s){checks++;if(!x)throw std::runtime_error(std::string(s)+" cycle="+std::to_string(cycles));}
 void tick(){
  d.clk=0;d.m_arready=!pending&&!blockAR&&(random()%4!=0);if(!(d.reg_wr&&d.reg_addr==0x28))d.mesh_word_ready=!forceStall&&(random()%4!=0);
  if(!d.m_rvalid&&pending&&!blockR&&random()%3){d.m_rvalid=1;d.m_rdata=address>=db?desc.at((address-db)/4):address>=cb?cmd.at((address-cb)/4):mem.at((address-base)/4);d.m_rlast=left==1||(address>=db&&descriptorFault==2);d.m_rresp=address>=db&&descriptorFault==1?2:0;}
  d.eval();if(held&&!d.mesh_consumer_reset)ok(d.mesh_word_valid&&d.mesh_word_data==heldword,"packet changed under stall");
  held=d.mesh_word_valid&&!d.mesh_word_ready&&!d.mesh_consumer_reset;heldword=d.mesh_word_data;
  if(d.mesh_word_valid&&d.mesh_word_ready)words.push_back(d.mesh_word_data);
  bool ar=d.m_arvalid&&d.m_arready,r=d.m_rvalid&&d.m_rready;auto a=d.m_araddr;unsigned len=d.m_arlen+1;bool last=d.m_rlast;
  if(ar)ok(!pending,"multiple read owners");
  d.clk=1;d.eval();cycles++;
  if(r){beats++;address+=4;if(last){left=0;pending=false;}else if(!--left)pending=false;d.m_rvalid=0;}
  if(ar){pending=true;address=a;left=(a>=db&&descriptorFault==3)?len+1:len;reads++;}
 }
 template<class F>void wait(F f,const char*message,unsigned limit=20000){for(unsigned n=0;!f();n++){tick();if(n>limit)throw std::runtime_error(std::string(message)+" cycle="+std::to_string(cycles)+" legacy="+std::to_string(rd(1))+" status="+std::to_string(st()));}}
 void wr(unsigned a,uint32_t v){d.reg_wr=1;d.reg_addr=a;d.reg_wdata=v;d.reg_wstrb=15;tick();d.reg_wr=0;d.eval();}
 uint32_t rd(unsigned a){d.reg_addr=a;d.eval();return d.reg_rdata;}
 uint32_t st(){return rd(0x21);}
 void reset(){d.reset=1;d.reg_wr=0;d.view_active=0;d.mesh_ctx_valid=0;d.mesh_job_valid=0;d.mesh_consumer_reset=0;d.mesh_bridge_fault=0;d.m_rvalid=0;pending=false;for(int n=0;n<7;n++)tick();d.reset=0;}
 void acquire(unsigned target,unsigned ticket){wr(0x2b,target);wr(0x2c,ticket);wr(0x2d,1);wait([&]{return st()&16;},"legacy acquire");ok(rd(0x2f)==ticket,"lease ticket");}
 void release(){wr(0x2d,2);wait([&]{return !(st()&16);},"legacy release");}
 void ctx(unsigned i,uint32_t v){d.mesh_ctx_index=i;d.mesh_ctx_data=v;d.mesh_ctx_valid=1;d.eval();wait([&]{return d.mesh_ctx_ready;},"context blocked");tick();d.mesh_ctx_valid=0;d.eval();}
 void context(){float m[16]={1,0,0,0,0,1,0,0,0,0,1,1,0,0,0,2};for(unsigned i=0;i<16;i++)ctx(i,bits(m[i]));ctx(16,bits(1));for(unsigned i=17;i<22;i++)ctx(i,0);ctx(22,0xffffffff);ctx(23,0);for(unsigned i=24;i<28;i++)ctx(i,100+i);}
 uint32_t cv(unsigned i){float m[16]={1,0,0,0,0,1,0,0,0,0,1,1,0,0,0,2};if(i<16)return bits(m[i]);if(i==16)return bits(1);if(i==22)return 0xffffffff;return i>=24?100+i:0;}
 void prefix(unsigned lo,unsigned hi){for(unsigned i=lo;i<hi;i++)ctx(i,cv(i));}
 void request(unsigned target,unsigned ticket){wr(0x2b,target);wr(0x2c,ticket);wr(0x2d,1);}
 void vertices(bool partial=false){mem.clear();float xyz[3][3]={{-.25f,-.25f,-1},{.25f,-.25f,0},{0,.5f,2}};if(partial)xyz[0][0]=-4;for(auto&v:xyz){for(float a:v)mem.push_back(bits(a));mem.push_back(0);mem.push_back(0x22110000);mem.push_back(0x4433);}cmd={0x05000204,0};}
 void start(unsigned serial,unsigned count=3,unsigned commands=1,uint32_t flags=0xabcd,uint32_t uoff=0,uint32_t voff=0){words.clear();d.mesh_job_seq=serial;d.mesh_job_vertex=base;d.mesh_job_vtx_w0=0x01000000|(count<<12)|(count<<1);d.mesh_job_commands=cb;d.mesh_job_control=commands;d.mesh_job_flags=flags;d.mesh_job_uofs=uoff;d.mesh_job_vofs=voff;d.mesh_job_cpu_vertex=0x12345000;d.mesh_job_cpu_commands=0x54321000;d.mesh_job_alpha=255;d.mesh_job_valid=1;d.eval();wait([&]{return d.mesh_job_ready;},"job not ready");tick();d.mesh_job_valid=0;d.eval();}
 void group(unsigned seq,unsigned count){desc.clear();for(unsigned i=0;i<count;i++){std::vector<uint32_t>x={base,0x01003006,cb,1,0x12345000+i*72,0x54321000+i*8};desc.insert(desc.end(),x.begin(),x.end());}groupStart(seq,count);}
 void groupStart(unsigned seq,unsigned count){words.clear();d.mesh_job_seq=seq;d.mesh_job_vertex=db;d.mesh_job_vtx_w0=count;d.mesh_job_commands=0;d.mesh_job_control=0x80000000;d.mesh_job_flags=0xabcd;d.mesh_job_uofs=0;d.mesh_job_vofs=0;d.mesh_job_cpu_vertex=0;d.mesh_job_cpu_commands=0;d.mesh_job_alpha=255;d.mesh_job_valid=1;d.eval();wait([&]{return d.mesh_job_ready;},"GROUP not ready");tick();d.mesh_job_valid=0;d.eval();}
 bool restoring(unsigned i){return W(group_state)==5&&W(restore_index)==i;}
 bool descriptorOwned(){return W(mesh__DOT__descriptor_owner);}
 bool detached(){return W(snapshot_detached);}
 void shadowCheck(){for(unsigned i=0;i<28;i++)ok(W(mesh__DOT__engine__DOT__frontend__DOT__raw__DOT__attributes)[96+i]==cv(i),"immutable context shadow poisoned");}
 void idle(){wait([&]{return !(rd(1)&1);},"owner idle");}
 void done(){wait([&]{return d.mesh_job_done;},"job completion");ok(!d.mesh_word_valid,"done with output");tick();}
 uint32_t view(unsigned row,unsigned slot){ok(!d.view_block,"alias blocked");d.view_active=1;d.view_read_addr=row*32+slot;tick();auto v=d.view_read_data;d.view_active=0;d.eval();return v;}
 uint32_t data(unsigned index){wr(0x26,index);tick();return rd(0x27);}
 void consumerReset(){d.mesh_consumer_reset=1;tick();d.mesh_consumer_reset=0;d.eval();}
};
int main(){try{Sim s;unsigned cases=0;
 for(unsigned f=1;f<=3;f++){
  s.reset();s.vertices();s.context();s.descriptorFault=f;s.group(1,2);
  s.wait([&]{return s.st()&4;},"descriptor error not reported");
  s.ok(s.words.empty()&&s.rd(0x23)==0&&!s.pending,"descriptor fault launched raw/output or failed drain");
  s.descriptorFault=0;s.consumerReset();s.acquire(99,900+f);s.release();s.context();s.group(3,1);s.done();
  s.ok(s.words.size()==29&&s.rd(0x23)==3,"descriptor fault/reset recovery");cases++;
 }
 // Cancel each synchronous shadow-read/apply position. No partial restored
 // context may become a member; fresh full context is required after reset.
 for(unsigned index=0;index<28;index++){
  s.reset();s.vertices();s.context();s.group(1,2);s.wait([&]{return s.restoring(index);},"restore position missing");
  s.consumerReset();s.ok(s.words.empty()&&s.rd(0x23)==0,"reset restore published member");
  s.acquire(88,1000+index);s.release();s.context();s.group(5,1);s.done();s.ok(s.words.size()==29&&s.rd(0x23)==5,"restore reset recovery");cases++;
 }
 // Consumer cancellation invalidates repair tokens even inside a group.
 s.reset();s.vertices(true);s.context();s.group(1,2);s.wait([&]{return s.st()&2;},"group repair absent");auto token=s.rd(0x25);
 s.consumerReset();s.idle();s.context();s.group(3,2);s.wait([&]{return s.st()&2;},"new group repair absent");
 s.ok(token!=s.rd(0x25),"group stale token reused");s.wr(0x29,token);s.wr(0x2a,1);s.ok(s.st()&4,"stale group repair END accepted");cases++;
 std::cout<<"{\"status\":\"PASS\",\"cases\":"<<cases<<",\"checks\":"<<s.checks<<",\"cycles\":"<<s.cycles<<",\"AXI_bursts\":"<<s.reads<<"}\n";return 0;
}catch(const std::exception&e){std::cerr<<e.what()<<"\n";return 1;}}
