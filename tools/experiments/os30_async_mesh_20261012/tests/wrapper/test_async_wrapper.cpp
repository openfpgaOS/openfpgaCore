#include "Vgeometry_load_alias.h"
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
 const uint32_t base=0x1000,cb=0xf000;std::vector<uint32_t> mem,cmd,words;bool pending=false,blockAR=false,blockR=false,forceStall=false,held=false;uint32_t address=0,heldword=0;unsigned left=0;
 uint32_t random(){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
 void ok(bool x,const char*s){checks++;if(!x)throw std::runtime_error(std::string(s)+" cycle="+std::to_string(cycles));}
 void tick(){
  d.clk=0;d.m_arready=!pending&&!blockAR&&(random()%4!=0);if(!(d.reg_wr&&d.reg_addr==0x28))d.mesh_word_ready=!forceStall&&(random()%4!=0);
  if(!d.m_rvalid&&pending&&!blockR&&random()%3){d.m_rvalid=1;d.m_rdata=address>=cb?cmd.at((address-cb)/4):mem.at((address-base)/4);d.m_rlast=left==1;d.m_rresp=0;}
  d.eval();if(held&&!d.mesh_consumer_reset)ok(d.mesh_word_valid&&d.mesh_word_data==heldword,"packet changed under stall");
  held=d.mesh_word_valid&&!d.mesh_word_ready&&!d.mesh_consumer_reset;heldword=d.mesh_word_data;
  if(d.mesh_word_valid&&d.mesh_word_ready)words.push_back(d.mesh_word_data);
  bool ar=d.m_arvalid&&d.m_arready,r=d.m_rvalid&&d.m_rready;auto a=d.m_araddr;unsigned len=d.m_arlen+1;
  if(ar)ok(!pending,"multiple read owners");
  d.clk=1;d.eval();cycles++;
  if(r){beats++;address+=4;if(!--left)pending=false;d.m_rvalid=0;}
  if(ar){pending=true;address=a;left=len;reads++;}
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
 void vertices(bool partial=false){mem.clear();float xyz[3][3]={{-.25f,-.25f,-1},{.25f,-.25f,0},{0,.5f,2}};if(partial)xyz[0][0]=-4;for(auto&v:xyz){for(float a:v)mem.push_back(bits(a));mem.push_back(0);mem.push_back(0x22110000);mem.push_back(0x4433);}cmd={0x05000204,0};}
 void start(unsigned serial){words.clear();d.mesh_job_seq=serial;d.mesh_job_vertex=base;d.mesh_job_vtx_w0=0x01003006;d.mesh_job_commands=cb;d.mesh_job_control=1;d.mesh_job_flags=0xabcd;d.mesh_job_uofs=d.mesh_job_vofs=0;d.mesh_job_cpu_vertex=0x12345000;d.mesh_job_cpu_commands=0x54321000;d.mesh_job_alpha=255;d.mesh_job_valid=1;d.eval();wait([&]{return d.mesh_job_ready;},"job not ready");tick();d.mesh_job_valid=0;d.eval();}
 void idle(){wait([&]{return !(rd(1)&1);},"owner idle");}
 void done(){wait([&]{return d.mesh_job_done;},"job completion");ok(!d.mesh_word_valid,"done with output");tick();}
 uint32_t view(unsigned row,unsigned slot){ok(!d.view_block,"alias blocked");d.view_active=1;d.view_read_addr=row*32+slot;tick();auto v=d.view_read_data;d.view_active=0;d.eval();return v;}
 uint32_t data(unsigned index){wr(0x26,index);tick();return rd(0x27);}
 void consumerReset(){d.mesh_consumer_reset=1;tick();d.mesh_consumer_reset=0;d.eval();}
};
int main(){try{Sim s;s.vertices();s.reset();s.context();unsigned cases=0;
 s.ok(s.rd(0x20)==0x414a3031,"capability");
 s.start(1);s.done();s.ok(s.rd(0x23)==1&&s.words.size()==29,"autonomous packet completion");s.ok(s.words[0]==0x56000008&&s.words[2]==uint32_t(-64)&&s.words[27]==0x54000001,"packet data");cases++;
 // An earlier job must finish output-copy before raw grant, not raster completion.
 s.forceStall=true;s.start(2);s.wait([&]{return s.d.mesh_word_valid;},"output wait");
 s.wr(0x2b,2);s.wr(0x2c,99);s.wr(0x2d,1);for(unsigned i=0;i<40;i++){s.tick();s.ok(!(s.st()&16),"lease stole stalled producer");}
 s.forceStall=false;s.wait([&]{return s.st()&16;},"grant after output");s.ok(s.words.size()==29,"grant before last output word");
 s.wr(3,s.base);s.wr(4,3);s.wr(2,1);s.idle();s.ok((s.rd(1)&6)==6,"legacy raw success");s.ok(s.view(0,0)==bits(-.25f),"legacy raw view");
 s.consumerReset();s.ok((s.st()&16)&&s.rd(0x2f)==99&&s.view(0,0)==bits(-.25f),"reset revoked legacy lease/view");
 s.wr(2,1);s.idle();s.ok(s.view(3,2)==bits(4),"reuse legacy lease");s.d.view_active=1;s.wr(0x2d,2);for(unsigned i=0;i<20;i++){s.tick();s.ok(s.st()&16,"release skipped alias drain");}s.d.view_active=0;s.wait([&]{return !(s.st()&16);},"release drain");cases++;
 // Frozen complete repair inputs, no output prefix, and head-only streaming.
 s.context();s.vertices(true);s.start(3);s.wait([&]{return s.st()&2;},"partial clip repair");s.ok(s.words.empty()&&(s.st()&32),"repair published or lost raw");
 float m[16]={1,0,0,0,0,1,0,0,0,0,1,1,0,0,0,2};for(unsigned i=0;i<16;i++)s.ok(s.data(i)==bits(m[i]),"matrix mirror");
 s.ok(s.data(28)==0x12345000&&s.data(29)==0x54321000&&s.data(24)==124&&s.data(35)==255,"captured repair metadata");
 s.ok(s.view(0,0)==bits(-4),"repair raw view");auto token=s.rd(0x25);s.wr(0x29,token);
 s.forceStall=true;s.tick();s.ok(!(s.st()&64),"repair credit under stall");s.forceStall=false;
 for(unsigned i=0;i<600;i++){s.wait([&]{return s.st()&64;},"repair ready");s.wr(0x28,0x60000000+i);}
 s.ok(s.words.size()==600,"long repair truncation");for(unsigned i=0;i<600;i++)s.ok(s.words[i]==0x60000000+i,"repair data order");
 s.d.view_active=1;s.wr(0x2a,1);for(unsigned i=0;i<20;i++){s.tick();s.ok(s.st()&2,"repair END skipped alias drain");}s.d.view_active=0;s.done();s.ok(s.rd(0x23)==3,"repair completion sequence");cases++;
 // Stale token is rejected after consumer reset and a fresh repair.
 s.start(4);s.wait([&]{return s.st()&2;},"repair before reset");auto stale=s.rd(0x25);s.consumerReset();s.idle();
 s.start(5);s.wait([&]{return s.st()&2;},"repair after reset");s.ok(s.rd(0x25)!=stale,"reset reused repair token");s.wr(0x29,stale);s.wr(0x2a,1);s.ok(s.st()&4,"stale END not rejected");s.consumerReset();s.idle();cases++;
 // Accepted AR survives cancellation until real RLAST; no local DMA reset.
 s.vertices();s.blockAR=true;s.start(6);s.wait([&]{return s.d.m_arvalid;},"AR missing");auto address=s.d.m_araddr;s.consumerReset();for(unsigned i=0;i<20;i++){s.tick();s.ok(s.d.m_arvalid&&s.d.m_araddr==address,"canceled held AR");}s.blockAR=false;s.idle();s.ok(!s.pending,"canceled DMA not drained");cases++;
 s.context();s.start(7);s.done();s.ok(s.words.size()==29&&!(s.st()&4),"recovery packet");cases++;
 // Cancel before the pending descriptor starts must release ownership without DMA.
 auto priorReads=s.reads;s.start(8);s.consumerReset();s.idle();s.ok(s.reads==priorReads&&!s.pending,"pre-LOAD cancel launched DMA");cases++;
 // Failed raw arithmetic returns the complete frozen inputs, never partial output.
 s.vertices();s.mem[0]=0x7fc00000;s.start(9);s.wait([&]{return s.st()&2;},"raw failure repair");s.ok(!(s.st()&32)&&s.words.empty(),"raw failure exposed partial success/output");
 s.ok(s.data(0)==bits(1)&&s.data(28)==0x12345000,"raw failure lost context");s.wr(0x29,s.rd(0x25));s.wr(0x2a,1);s.done();s.ok(s.rd(0x23)==9,"raw failure END completion");cases++;
 // A consumer reset also leaves an in-progress CPU legacy LOAD untouched.
 s.vertices();s.acquire(9,100);s.wr(3,s.base);s.wr(4,3);s.blockR=true;s.wr(2,1);s.wait([&]{return s.pending;},"legacy DMA missing");s.consumerReset();s.ok((s.st()&16)&&s.rd(0x2f)==100,"active legacy reset revoked lease");s.blockR=false;s.idle();s.ok((s.rd(1)&6)==6&&s.view(0,0)==bits(-.25f),"active legacy reset canceled raw result");s.release();cases++;
 // A malformed job with no complete matrix is rejected before raw ownership.
 s.reset();s.start(10);s.ok((s.st()&4)&&!(s.st()&1)&&!s.pending,"missing context accepted job");cases++;
 std::cout<<"{\"status\":\"PASS\",\"cases\":"<<cases<<",\"checks\":"<<s.checks<<",\"cycles\":"<<s.cycles<<",\"AXI_bursts\":"<<s.reads<<"}\n";return 0;
}catch(const std::exception&e){std::cerr<<e.what()<<"\n";return 1;}}
