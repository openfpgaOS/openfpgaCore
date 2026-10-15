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
  d.clk=0;d.m_arready=!pending&&!blockAR&&(random()%4!=0);d.mesh_word_ready=!forceStall&&(random()%4!=0);
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
 void reset(){d.reset=1;d.reg_wr=0;d.view_active=0;d.mesh_marker_valid=0;d.mesh_consumer_reset=0;d.mesh_bridge_fault=0;d.m_rvalid=0;pending=false;for(int n=0;n<7;n++)tick();d.reset=0;}
 void context(){float m[16]={1,0,0,0,0,1,0,0,0,0,1,1,0,0,0,2};for(unsigned n=0;n<16;n++)wr(0x40+n,bits(m[n]));wr(3,base);wr(4,3);wr(5,bits(1));wr(6,0);wr(7,0);wr(8,0);wr(9,0);wr(10,0);wr(11,0xffffffff);wr(12,0);}
 void vertices(bool partial=false){mem.clear();float xyz[3][3]={{-.25f,-.25f,-1},{.25f,-.25f,0},{0,.5f,2}};if(partial)xyz[0][0]=-4;for(auto&v:xyz){for(float a:v)mem.push_back(bits(a));mem.push_back(0);mem.push_back(0x22110000);mem.push_back(0x4433);}cmd={0x05000204,0};}
 void start(unsigned serial){words.clear();wr(0x22,serial);wr(0x23,0x01003006);wr(0x24,cb);wr(0x25,1);wr(0x26,1);}
 void preflight(){wait([&]{return st()&6;},"preflight timeout");ok(!pending,"terminal preflight has bus owner");}
 void idle(){wait([&]{return !(rd(1)&1);},"owner idle timeout");}
 uint32_t view(unsigned row,unsigned slot){ok(!d.view_block,"alias incorrectly blocked");d.view_active=1;d.view_read_addr=row*32+slot;tick();auto v=d.view_read_data;d.view_active=0;d.eval();return v;}
 void rawcheck(){float exp[5][3]={{-.25f,.25f,0},{-.25f,-.25f,.5f},{-1,0,2},{1,2,4},{1,.5f,.25f}};for(unsigned r=0;r<5;r++)for(unsigned i=0;i<3;i++)ok(view(r,i)==bits(exp[r][i]),"legacy or mesh raw output changed");ok(d.view_valid&&d.view_count==3,"raw view validity/count");}
 void validate(){wr(0x2b,0xabcd);wr(0x2c,0);wr(0x2d,0);wr(0x26,8);wait([&]{return st()&(32|64);},"validation timeout");ok((st()&32)&&!(st()&64)&&words.empty(),"validation failed or emitted words");}
 void arm(){wr(0x26,16);}
 void marker(unsigned serial){d.mesh_marker_serial=serial;d.mesh_marker_flags=0xabcd;d.mesh_marker_uofs=0;d.mesh_marker_vofs=0;d.mesh_marker_valid=1;d.eval();wait([&]{ok(!d.mesh_marker_error,"valid marker rejected");return d.mesh_marker_ready;},"marker wait timeout");tick();d.mesh_marker_valid=0;d.eval();}
 void drain(){wait([&]{return d.mesh_packet_done;},"packet queue drain timeout");ok(!d.mesh_word_valid,"done still publishes");tick();}
 void consumerReset(){d.mesh_consumer_reset=1;tick();d.mesh_consumer_reset=0;d.eval();}
};
int main(){try{Sim s;s.vertices();s.reset();s.context();unsigned cases=0;
 s.ok(s.rd(0x20)==0x4d544632,"MTF2 capability");
 s.wr(2,1);s.idle();s.ok((s.rd(1)&6)==6,"legacy completion failed");s.rawcheck();cases++;
 // Accepted aliases drain before raw LOAD and before numeric validation.
 s.d.view_active=1;s.start(11);for(unsigned n=0;n<20;n++){s.tick();s.ok(!s.d.m_arvalid&&s.d.view_block,"LOAD bypassed alias owner");}s.d.view_active=0;s.preflight();s.ok((s.st()&0x8013)==0x8013,"accepted mesh status");s.rawcheck();
 s.d.view_active=1;s.wr(0x2b,0xabcd);s.wr(0x2c,0);s.wr(0x2d,0);s.wr(0x26,8);
 for(unsigned n=0;n<20;n++){s.tick();s.ok(s.d.view_block&&!(s.st()&32)&&s.words.empty(),"validation bypassed alias");}s.d.view_active=0;s.wait([&]{return s.st()&32;},"validate alias drain timeout");
 for(unsigned n=0;n<50;n++){s.tick();s.ok(s.words.empty()&&!s.d.view_block&&(s.st()&1),"unpublished validated hold");}s.rawcheck();
 // ARM reserves its own metadata, but copying and a matching early marker wait
 // for the old alias burst. FILLING is legal marker backpressure, not an error.
 s.d.view_active=1;s.d.view_read_addr=0;s.arm();s.d.mesh_marker_serial=11;s.d.mesh_marker_flags=0xabcd;s.d.mesh_marker_uofs=s.d.mesh_marker_vofs=0;s.d.mesh_marker_valid=1;
 for(unsigned n=0;n<30;n++){s.tick();s.ok(!s.d.mesh_marker_ready&&!s.d.mesh_marker_error&&s.d.view_block&&s.words.empty(),"ARM or marker bypassed alias drain");s.ok(s.d.view_read_data==bits(-.25f),"copy stole active alias");}
 s.d.view_active=0;s.wait([&]{s.ok(!s.d.mesh_marker_error,"filling marker fault");return s.d.mesh_marker_ready;},"fill did not commit");s.tick();s.d.mesh_marker_valid=0;s.d.eval();s.drain();s.idle();
 s.ok(s.words.size()==29&&s.words[0]==0x56000008&&s.words[27]==0x54000001&&s.words[28]==0x820,"packet output shape");s.ok((s.st()&(1<<20))&&s.rd(0x2f)==11,"raw-copy completion serial");s.rawcheck();cases++;
 // Raw owner releases before any marker. Next LOAD/cull/validation overwrites
 // raw values and descriptors while the prepared packet remains immutable.
 s.start(18);s.preflight();s.validate();s.arm();s.idle();s.ok(s.words.empty(),"private prefill leaked words");
 s.mem[0]=bits(-.5f);s.start(19);s.preflight();s.validate();s.arm();
 s.idle();s.ok(s.words.empty(),"second queued packet leaked before marker");
 s.marker(18);s.drain();s.idle();s.ok(s.words.size()==29&&s.words[2]==uint32_t(-64),"queued old vertex changed after next LOAD");
 s.marker(19);s.drain();s.ok(s.words.size()==58&&s.words[31]==uint32_t(-128),"pending ARM or next packet failed");s.vertices();cases++;
 // Fifteen short packets occupy 505 words after the head descriptor is cached.
 // A sixteenth ARM must wait for credits; draining the oldest packet admits it
 // while preserving every older queued task across producer/consumer wrap.
 for(unsigned n=0;n<15;n++){
  s.mem[0]=bits(-float(n+1)/64);s.start(100+n);s.preflight();s.validate();s.arm();s.idle();
  s.ok(s.words.empty(),"queued packet published without marker");
 }
 s.mem[0]=bits(-16.0f/64);s.start(115);s.preflight();s.validate();s.arm();
 for(unsigned n=0;n<40;n++){s.tick();s.ok((s.st()&1)&&s.words.empty(),"full queue failed to retain ARM");}
 s.marker(100);s.drain();s.idle();
 for(unsigned n=1;n<16;n++){s.marker(100+n);s.drain();}
 s.ok(s.words.size()==16*29,"multi-packet word count");
 for(unsigned n=0;n<16;n++)s.ok(s.words[n*29+2]==uint32_t(-int(4*(n+1))),"queued source/token order changed");
 s.vertices();cases++;
 // A newer task's marker cannot bypass an older READY head even when there is
 // credit for its ARM. Reservation may backpressure for a cycle; then the
 // out-of-order token must fault, with no ordinary word accepted.
 s.start(120);s.preflight();s.validate();s.arm();s.idle();
 for(unsigned n=0;n<16;n++)s.tick();
 s.start(121);s.preflight();s.validate();s.arm();
 s.d.mesh_marker_serial=121;s.d.mesh_marker_flags=0xabcd;
 s.d.mesh_marker_uofs=s.d.mesh_marker_vofs=0;s.d.mesh_marker_valid=1;s.d.eval();
 s.wait([&]{s.ok(!s.d.mesh_marker_ready&&s.words.empty(),"new marker bypassed old queued head");return s.d.mesh_marker_error;},"out-of-order marker hung",10);
 s.tick();s.d.mesh_marker_valid=0;s.consumerReset();s.idle();s.wr(2,2);cases++;
 // Geometry fallback and numeric rejection preserve canonical repair data.
 s.vertices(true);s.start(12);s.preflight();s.ok((s.st()&0x15)==0x15&&((s.st()>>16)&15)==8,"geometry fallback/raw success");s.wr(0x26,2);s.idle();s.ok(s.d.view_valid&&!s.d.view_block,"fallback ACK destroyed alias");cases++;
 s.vertices();s.start(13);s.preflight();s.wr(0x2b,0x80000000);s.wr(0x26,8);s.wait([&]{return s.st()&64;},"invalid-context validation timeout");s.ok(s.words.empty(),"invalid validation published");s.wr(0x26,2);s.idle();s.rawcheck();cases++;
 // Stale marker is explicit even after its raw owner already released.
 s.start(14);s.preflight();s.validate();s.arm();s.idle();s.d.mesh_marker_serial=13;s.d.mesh_marker_flags=0xabcd;s.d.mesh_marker_uofs=s.d.mesh_marker_vofs=0;s.d.mesh_marker_valid=1;s.d.eval();s.wait([&]{s.ok(!s.d.mesh_marker_ready,"stale marker accepted");return s.d.mesh_marker_error;},"stale marker escaped header validation");s.tick();s.d.mesh_marker_valid=0;s.ok(s.st()&8,"marker protocol fault absent");s.consumerReset();s.idle();s.ok(s.words.empty(),"bad marker publication");s.wr(2,2);cases++;
 // Current mesh cancellation drains a stalled raw address/response.
 s.blockAR=true;s.start(15);s.wait([&]{return s.d.m_arvalid;},"raw AR missing");auto addr=s.d.m_araddr;s.consumerReset();for(unsigned n=0;n<25;n++){s.tick();s.ok(s.d.m_arvalid&&s.d.m_araddr==addr&&(s.rd(1)&1),"reset canceled stalled raw AR");}s.blockAR=false;s.idle();s.ok(!s.pending,"reset released DMA before drain");cases++;
 // GPU reset discards an older prepared queue while an unrelated legacy raw
 // DMA continues. The queue reset must never reset the shared AXI producer.
 s.start(20);s.preflight();s.validate();s.arm();s.idle();s.blockR=true;s.wr(2,1);s.wait([&]{return s.pending;},"legacy read missing");s.consumerReset();for(unsigned n=0;n<20;n++){s.tick();s.ok(s.rd(1)&1,"GPU reset aborted legacy LOAD");}s.blockR=false;s.idle();s.ok((s.rd(1)&6)==6,"legacy reset completion");s.rawcheck();cases++;
 // Reset during private prefill and after a published prefix are both safe.
 s.start(16);s.preflight();s.validate();s.arm();s.wait([&]{return s.st()&128;},"copy start missing");s.consumerReset();s.idle();s.ok(s.words.empty()&&!s.pending,"prefill reset leaked packet");cases++;
 s.start(21);s.preflight();s.validate();s.arm();s.idle();s.marker(21);s.wait([&]{return !s.words.empty();},"publication missing");s.forceStall=true;s.consumerReset();s.idle();s.ok(!s.d.mesh_word_valid&&!s.pending,"consumer reset left packet owner");s.forceStall=false;cases++;
 s.start(17);s.preflight();s.validate();s.arm();s.idle();s.marker(17);s.drain();s.ok(s.words.size()==29,"post-reset publication failed");s.rawcheck();cases++;
 std::cout<<"{\"status\":\"PASS\",\"cases\":"<<cases<<",\"checks\":"<<s.checks<<",\"cycles\":"<<s.cycles<<",\"AXI_bursts\":"<<s.reads<<",\"AXI_beats\":"<<s.beats<<"}\n";return 0;
}catch(const std::exception&e){std::cerr<<e.what()<<"\n";return 1;}}
