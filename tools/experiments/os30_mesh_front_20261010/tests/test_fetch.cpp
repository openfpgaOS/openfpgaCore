#include "Vgeometry_mesh_command_fetch.h"
#include "verilated.h"
#include <random>
#include <vector>
#include <utility>
#include <stdexcept>
#include <iostream>
static void require(bool b,const char*s){if(!b)throw std::runtime_error(s);}
static uint32_t memory(uint32_t a){return (a*0x9e3779b9u)^(a>>9)^0x06012345u;}
struct Sim {
 Vgeometry_mesh_command_fetch t;
 std::mt19937 rng{913921};
 std::vector<std::pair<uint32_t,uint32_t>> commands;
 bool outstanding=false,hold_ar=false,hold_word=false,hold_response=false;
 uint32_t address=0,ar_held=0,w0=0,w1=0;
 unsigned beat=0,read_beats=2,response_error=0;
 unsigned accepted=0,drained=0,cycles=0,checks=0;
 Sim(){t.reset=1;tick();t.reset=0;tick();}
 void tick(){
  t.clk=0;t.eval();
  if(!t.reset){
   if(hold_ar){require(t.m_arvalid&&t.m_araddr==ar_held,"AXI address changed before acceptance");++checks;}
   if(hold_word&&!t.cancel){require(t.command_valid&&t.command_w0==w0&&t.command_w1==w1,"command changed before acceptance");++checks;}
   hold_ar=t.m_arvalid&&!t.m_arready;ar_held=t.m_araddr;
   hold_word=t.command_valid&&!t.command_ready;w0=t.command_w0;w1=t.command_w1;
   hold_response=t.m_rvalid&&!t.m_rready;
   if(t.m_arvalid&&t.m_arready){require(!outstanding,"overlapping reads");require(t.m_arlen==1,"wrong burst length");address=t.m_araddr;beat=0;outstanding=true;++accepted;}
   if(t.m_rvalid&&t.m_rready){require(outstanding,"read without address");++beat;if(t.m_rlast){outstanding=false;++drained;}}
   if(t.command_valid&&t.command_ready)commands.emplace_back(t.command_w0,t.command_w1);
  }
  t.clk=1;t.eval();++cycles;
 }
 void drive(bool allow_address=true){
  t.m_arready=allow_address&&rng()%3!=0;
  // Hold response fields/VALID under backpressure as required by AXI.
  if(!hold_response){
   t.m_rvalid=outstanding&&(rng()%4!=0);t.m_rdata=memory(address+4*beat);
   t.m_rlast=beat+1==read_beats;t.m_rresp=response_error;
  }
  t.command_ready=rng()%5==0;
 }
 void begin(uint32_t source,unsigned count){
  require(!t.busy&&!outstanding,"begin before previous completion");
  commands.clear();t.source=source;t.count=count;t.start=1;t.cancel=0;drive();tick();t.start=0;
  t.source=~source;t.count=127;
 }
 void finish(){unsigned budget=100000;while(!t.done&&budget--){drive();tick();}require(t.done,"timeout");require(!outstanding,"completion abandoned accepted burst");t.cancel=0;drive();tick();}
};
int main(){try{Sim s;unsigned cases=0,words=0;
 for(unsigned n=1;n<=64;n++){
  auto source=0x10000000u+8*(s.rng()%1024);s.begin(source,n);s.finish();
  require(!s.t.error&&s.commands.size()==n,"normal completion");
  for(unsigned j=0;j<n;j++){require(s.commands[j]==std::make_pair(memory(source+8*j),memory(source+8*j+4)),"wrong native command");words+=2;}++cases;
 }
 for(auto [source,n]:std::vector<std::pair<uint32_t,unsigned>>{{0x10000001,1},{0x0ffffff8,1},{0x13fffff8,2},{0xfffffff8,1},{0x10000000,0},{0x10000000,65}}){s.begin(source,n);s.finish();require(s.t.error&&s.commands.empty(),"bad range accepted");++cases;}
 s.begin(0x13fffff8,1);s.finish();require(!s.t.error,"last valid source rejected");++cases;
 // Cancel with address held, then accept and drain the already offered burst.
 s.begin(0x10000100,4);s.t.m_arready=0;s.tick();s.t.cancel=1;s.tick();s.finish();require(s.commands.empty(),"cancel emitted command");++cases;
 // Cancel after read address acceptance, including first/last response boundary.
 for(unsigned delay=0;delay<7;delay++){
  s.begin(0x10000200,4);while(!s.outstanding){s.drive();s.tick();}
  for(unsigned j=0;j<delay&&!s.t.done;j++){s.drive();s.t.command_ready=0;s.tick();}
  s.t.cancel=1;s.finish();require(s.commands.empty(),"canceled response published");++cases;
 }
 for(unsigned n:{1,2,3,5}){
  s.read_beats=n;s.response_error=n==2?2:0;s.begin(0x10000100,3);s.finish();
  require(s.t.error&&s.commands.empty(),"malformed/error burst accepted");++cases;
 }
 require(s.accepted==s.drained,"read accounting mismatch");
 std::cout<<"{\"status\":\"PASS\",\"cases\":"<<cases<<",\"words_checked\":"<<words<<",\"accepted_reads\":"<<s.accepted<<",\"drained_reads\":"<<s.drained<<",\"held_signal_checks\":"<<s.checks<<",\"cycles\":"<<s.cycles<<"}\n";
 }catch(const std::exception&e){std::cerr<<e.what()<<"\n";return 1;}}
