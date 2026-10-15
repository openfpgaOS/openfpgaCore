#include "Vgeometry_mesh_stream.h"
#include "verilated.h"
#include <cstdint>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <vector>
#include <array>
#include <string>
#include <cmath>
#include <fstream>
static uint32_t bits(float f){uint32_t x;memcpy(&x,&f,4);return x;}
struct Sim {
 Vgeometry_mesh_stream d; uint64_t cycles=0,checks=0,reads=0,beats=0;uint32_t rng=37;
 bool stress_publish=false;
 bool block_command_ar=false,block_command_r=false,extend_command_burst=false;
 uint32_t memory_base=0x10001000,command_base=0x13f00000;std::vector<uint32_t> commands_mem;
 std::vector<uint32_t> packets;bool held=false;uint32_t held_word=0;
 std::vector<uint32_t> mem; uint32_t address=0;unsigned left=0;bool pending=false;bool response_error=false;
 uint32_t random(){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
 void ok(bool p,const std::string &s){checks++;if(!p)throw std::runtime_error(s+" cycle="+std::to_string(cycles));}
 void tick(){
  d.clk=0;if(stress_publish&&d.packet_busy){d.packet_access=random()&1;d.view_addr=random();d.list_addr=random();if(!packets.empty())d.cancel=1;} d.m_arready=!pending&&random()%4!=0&&!(block_command_ar&&d.m_araddr>=command_base);
  if(!d.m_rvalid&&pending&&random()%3!=0&&!(block_command_r&&address>=command_base)){d.m_rvalid=1;d.m_rdata=(address>=command_base?commands_mem.at((address-command_base)/4):mem.at((address-memory_base)/4));d.m_rlast=left==1;d.m_rresp=response_error?2:0;}
  d.word_ready=random()%4!=0;d.eval();
  if(held)ok(d.word_valid&&d.word_data==held_word,"output changed while stalled");
  held=d.word_valid&&!d.word_ready;held_word=d.word_data;
  if(d.word_valid&&d.word_ready)packets.push_back(d.word_data);
  bool ar=d.m_arvalid&&d.m_arready,r=d.m_rvalid&&d.m_rready;
  uint32_t a=d.m_araddr;unsigned n=d.m_arlen+1;
  if(ar){ok(!pending,"overlapping read bursts");ok((a>=memory_base&&uint64_t(a)+4*n<=uint64_t(memory_base)+4*mem.size())||(a>=command_base&&uint64_t(a)+4*n<=uint64_t(command_base)+4*commands_mem.size()),"DMA outside source");}
  d.clk=1;d.eval();cycles++;
  if(r){beats++;address+=4;if(--left==0)pending=false;d.m_rvalid=0;}
  if(ar){pending=true;address=a;left=n+((extend_command_burst&&a>=command_base)?1:0);reads++;}
 }
 void reset(){d.reset=1;d.task_valid=0;d.matrix_we=0;d.task_ack=0;d.cancel=0;d.packet_start=0;d.packet_access=0;d.m_rvalid=0;pending=false;for(int i=0;i<9;i++)tick();d.reset=0;}
 void matrix(bool equal=false){float m[16]={1,0,0,0,0,1,0,0,0,0,1,equal?0.f:1.f,0,0,0,2};for(int i=0;i<16;i++){d.matrix_we=1;d.matrix_addr=i;d.matrix_data=bits(m[i]);tick();}d.matrix_we=0;}
 void vertices(bool partial=false,bool common=false,bool invalid=false){
  mem.clear();float xyz[3][3]={{-.25f,-.25f,-1},{.25f,-.25f,0},{0,.5f,2}};
  if(partial)xyz[0][0]=-4;if(common)for(auto &v:xyz)v[0]=-8;if(invalid)xyz[0][0]=1e-40f;
  for(auto &v:xyz){for(float x:v)mem.push_back(bits(x));mem.push_back(0);mem.push_back(0x22110000);mem.push_back(0x00004433);}
 }
 void start(unsigned serial,unsigned dest=5,unsigned mode=0,unsigned features=0,unsigned commands=1){
  d.eval();for(unsigned n=0;!d.task_ready&&n<8;n++)tick();ok(d.task_ready,"task not ready");d.task_valid=1;d.task_serial=serial;d.task_vtx_w0=0x01000000|(3<<12)|((dest+3)<<1);d.task_vtx_w1=0x10001000;d.task_command_count=commands;d.task_cull_mode=mode;d.task_unsupported_features=features;
  d.aspect=bits(1);d.coefficient_x=d.coefficient_y=d.coefficient_z=0;d.ambient_rgb=d.diffuse_rgb=0;d.lighting_enabled=0;d.scale_u=d.scale_v=65535;tick();d.task_valid=0;
  // All mutable descriptor/scalar inputs may change after acceptance.
  d.aspect=0x7fc00000;d.coefficient_x=0x7fc00000;d.lighting_enabled=1;d.scale_u=d.scale_v=0;d.task_vtx_w1=0;d.task_serial=0;
 }
 void finish(){for(unsigned n=0;!d.preflight_valid&&!d.fallback_valid;n++){tick();if(n>15000)throw std::runtime_error("preflight timeout");}ok(!pending,"completed with AXI outstanding");}
 void publish(unsigned serial){
  packets.clear();std::vector<uint32_t> expected;uint32_t mask=d.used_mask;unsigned count=d.live_count;
  bool shade=serial%2,textured=serial%3==0;uint32_t flags=(shade?1u<<18:0)|(textured?3u<<16:0)|0xabcd;
  for(unsigned slot=0;slot<32;slot++)if(mask>>slot&1){
   uint32_t q[8];for(unsigned row=0;row<8;row++)q[row]=view(row,slot);
   auto f=[](uint32_t x){float v;memcpy(&v,&x,4);return v;};
   auto cv=[&](uint32_t x){return uint32_t(int32_t(std::lrintf(f(x)*256.f)));};
   uint32_t depth=uint32_t(int32_t(std::lrintf(std::fmax(1.f,std::fmin(2147483520.f,f(q[4])*1073741824.f)))));
   uint32_t rgb=shade?((q[6]>>3&31)<<11)|((q[6]>>10&63)<<5)|(q[6]>>19&31):0xabcd;
   int32_t u=textured?(int16_t(q[5])-24+16)*2048:0,v=textured?(int16_t(q[5]>>16)-40+16)*2048:0;
   for(auto word:std::vector<uint32_t>{0x56000008,slot,cv(q[0]),cv(q[1]),cv(q[3]),uint32_t(u),uint32_t(v),rgb,depth})expected.push_back(word);
  }
  for(unsigned i=0;i<count;i++){d.list_addr=i;tick();expected.push_back(0x54000001);expected.push_back(d.list_indices);}
  d.packet_access=1;d.flags_rgb=flags;d.u_offset=24;d.v_offset=40;d.packet_start=1;tick();d.packet_start=stress_publish?1:0;
  // Material inputs are captured, too.
  d.flags_rgb=0xffffffff;d.u_offset=d.v_offset=0xffffffff;
  for(unsigned n=0;!d.packet_done;n++){ok(d.preflight_valid&&d.busy&&!d.task_ready,"task released during packets");tick();if(n>20000)throw std::runtime_error("packet timeout");}
  if(stress_publish){for(unsigned n=0;n<20;n++){tick();ok(!d.fallback_valid&&d.preflight_valid,"late cancel created repair");}d.packet_start=0;d.cancel=0;stress_publish=false;}
  ok(!d.packet_error,"packet conversion rejected engine output");ok(packets==expected,"ordinary GPU packet mismatch");d.packet_access=0;d.eval();
 }
 void ack(unsigned serial){if(d.preflight_valid)publish(serial);else ok(!d.word_valid,"fallback emitted packets");d.task_ack=1;d.ack_serial=serial+1;tick();ok(d.busy,"stale ack released owner");d.ack_serial=serial;tick();d.task_ack=0;ok(d.done&&!d.busy,"matching ack did not release");}
 uint32_t view(unsigned row,unsigned slot){d.view_addr=row*32+slot;tick();return d.view_data;}
 void coords(){float expect[5][3]={{-.25f,.25f,0},{-.25f,-.25f,.5f},{-1,0,2},{1,2,4},{1,.5f,.25f}};for(unsigned row=0;row<5;row++)for(unsigned i=0;i<3;i++)ok(view(row,i+5)==bits(expect[row][i]),"raw coordinate/inverse differs");for(unsigned i=0;i<3;i++){ok(view(5,i+5)==0,"UV differs");ok(view(6,i+5)==0x44332211,"RGBA differs");ok(view(7,i+5)==0,"clip differs");}}
};
int main(){try{Sim s;s.vertices();s.reset();s.matrix();unsigned cases=0;
 auto setup=[&](unsigned id,unsigned n=2){s.commands_mem={0x050a0c0e,0,0x050e0c0a,0};s.d.task_command_source=s.command_base;s.start(id,5,0,0,n);};
 auto wait=[&](auto pred,const char*why){for(unsigned n=0;!pred();n++){s.tick();if(n>10000)throw std::runtime_error(why);}};
 // A cancel during stalled command AR must keep that transaction live until
 // accepted and drained. Even a correct early ACK cannot free the owner.
 s.block_command_ar=true;setup(101);wait([&](){return s.d.m_arvalid&&s.d.m_araddr>=s.command_base;},"command AR missing");uint32_t addr=s.d.m_araddr;
 s.d.cancel=1;s.tick();s.d.cancel=0;s.d.task_ack=1;s.d.ack_serial=101;
 for(unsigned i=0;i<40;i++){s.ok(s.d.m_arvalid&&s.d.m_araddr==addr,"stalled AR canceled/changed");s.ok(!s.d.fallback_valid&&!s.d.task_ready&&s.d.busy,"prefetch owner released early");s.tick();}
 s.d.task_ack=0;s.block_command_ar=false;s.finish();s.ok(s.d.fallback_reason==14&&!s.pending,"stalled cancel did not drain");s.ack(101);cases++;
 // Partial clip is discovered while the *next* command's R burst is delayed.
 s.vertices(true);setup(102);wait([&](){return s.d.m_arvalid&&s.d.m_araddr==s.command_base+8;},"second prefetch missing");s.block_command_r=true;
 wait([&](){return s.pending&&s.address==s.command_base+8;},"second prefetch not accepted");s.d.task_ack=1;s.d.ack_serial=102;
 for(unsigned i=0;i<200;i++){s.tick();s.ok(!s.d.fallback_valid&&!s.d.task_ready&&s.d.busy,"clip exception exposed before read drain");}
 s.d.task_ack=0;s.block_command_r=false;s.finish();s.ok(s.d.fallback_reason==8&&!s.pending,"prefetch clip drain");s.ack(102);cases++;
 // A late RLAST is drained as a malformed command transaction, then routed
 // to whole-task fallback without a geometry publication or next-task start.
 s.vertices();s.extend_command_burst=true;setup(103);s.finish();s.ok(s.d.fallback_reason==3&&s.d.fetch_error&&!s.pending,"late command RLAST not drained");s.ack(103);s.extend_command_burst=false;cases++;
 // Invalid command source fails before emitting any command-side address.
 s.d.task_command_source=s.command_base+4;s.start(104,5,0,0,1);s.finish();s.ok(s.d.fallback_reason==3&&!s.pending,"unaligned command source accepted");s.ack(104);cases++;
 // After failures, new source/load ownership must recover cleanly and publish.
 setup(105);s.finish();s.ok(s.d.preflight_valid&&!s.d.fetch_error&&s.d.live_count==2,"recovery task failed");s.stress_publish=true;s.ack(105);cases++;
 // Same-edge cancel has precedence over first packet publication.
 setup(106);s.finish();s.packets.clear();s.d.packet_start=1;s.d.cancel=1;s.tick();s.d.packet_start=0;s.d.cancel=0;s.finish();s.ok(s.d.fallback_reason==14&&s.packets.empty()&&!s.d.packet_busy,"cancel/start precedence");s.ack(106);cases++;
 std::cout<<"{\"status\":\"PASS\",\"cases\":"<<cases<<",\"checks\":"<<s.checks<<",\"cycles\":"<<s.cycles<<",\"AXI_bursts\":"<<s.reads<<",\"AXI_beats\":"<<s.beats<<"}\n";return 0;
}catch(const std::exception&e){std::cerr<<e.what()<<"\n";return 1;}}
