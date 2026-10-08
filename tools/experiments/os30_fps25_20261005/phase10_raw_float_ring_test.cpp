#include "Vtb_raw_float_ring.h"
#include "verilated.h"
#include <cstdint>
#include <deque>
#include <iostream>
#include <stdexcept>
#include <vector>

static uint32_t rng=0x37a59261;
static uint32_t random32(){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
static void require(bool ok,const char* message){if(!ok)throw std::runtime_error(message);}
struct Test {
 Vtb_raw_float_ring d;
 uint64_t cycles=0,accepted=0,consumed=0,aborted=0,input_count=0,blocked=0,commits=0;
 uint16_t append=0,published=0,read=0;
 std::vector<uint32_t> pending;
 std::deque<uint32_t> visible,input_expected;
 bool read_primed=false;
 void evaluate(){d.clk=0;d.eval();}
 bool step(bool consume=false){
  evaluate();
  if(consume){
   require(read_primed,"consumer must prime synchronous memory");
   require(!visible.empty(),"unpublished output became visible");
   require(d.output_data==visible.front(),"output word mismatch/order violation");
   visible.pop_front();read=(read+1)&4095;d.output_rdptr=read;d.output_addr=read;
   consumed++;read_primed=false;
   evaluate();
  }
  // A first word written/published on this edge is not yet a valid
  // synchronous RAM read (the retained parser also has a prime cycle).
  bool output_read_primed=!visible.empty();
  bool active=d.reset_n&&d.enable&&!d.soft_reset&&!d.ring_reset;
  bool control=d.reset_n&&!d.soft_reset&&!d.ring_reset;
  if(active&&d.cmd_valid&&d.cmd_ready){
   require(!input_expected.empty(),"input escaped publication");
   require(d.cmd_data==input_expected.front(),"input synchronous read mismatch");
   input_expected.pop_front();input_count++;
  }
  bool take=active&&d.result_valid&&d.result_ready;
  if(active&&d.result_valid&&!d.result_ready)blocked++;
  if(control&&d.result_abort){pending.clear();append=published;aborted++;}
  else if(control){
   if(take){pending.push_back(d.result_data);append=(append+1)&4095;accepted++;}
   if(d.result_commit){for(auto x:pending)visible.push_back(x);pending.clear();published=append;commits++;}
  }
  d.clk=1;d.eval();cycles++;
  if(!d.reset_n||d.soft_reset||d.ring_reset){
   append=published=read=0;visible.clear();pending.clear();input_expected.clear();
   d.output_rdptr=0;d.output_addr=0;read_primed=false;
  }else{
   require(d.output_wrptr==published,"private transaction was published early");
   require(((published-read)&4095)==visible.size(),"output pointer accounting mismatch");
   require(((append-read)&4095)==visible.size()+pending.size(),"append accounting mismatch");
   require(visible.size()+pending.size()<=63,"queue overflow");
   read_primed=output_read_primed;
  }
  d.clk=0;d.eval();
  return take;
 }
 void init(){
  d.enable=1;d.reset_n=0;d.soft_reset=0;d.ring_reset=0;d.cpu_write=0;
  d.input_wrptr=0;d.cmd_ready=0;d.result_valid=0;d.result_commit=0;
  d.result_abort=0;d.processor_busy=0;d.output_rdptr=0;d.output_addr=0;
  step();step();d.reset_n=1;step();
 }
 bool canconsume(){return read_primed&&!visible.empty();}
 void emit(uint32_t word,bool commit,bool runconsumer=true){
  d.result_valid=1;d.result_data=word;
  for(unsigned timeout=0;;timeout++){
   require(timeout<100000,"output deadlock");evaluate();
   bool consume=runconsumer&&canconsume()&&random32()%4!=0;
   // Consumer advancement is presented before the same edge. A formerly
   // full FIFO can accept on that edge; decide the final-word commit using
   // the same handshake, and do not send that word a second time.
   bool ready=d.result_ready||consume;
   d.result_commit=commit&&ready;
   bool took=step(consume);
   if(took)break;
  }
  d.result_valid=0;d.result_commit=0;
 }
 void drain(){
  require(pending.empty(),"cannot drain unpublished transaction");
  d.result_valid=d.result_commit=d.result_abort=0;
  while(!visible.empty())step(canconsume());
  step();
 }
};
int main(int argc,char**argv){
 Verilated::commandArgs(argc,argv);
 try{
  Test t;t.init();
  // No result word can become visible until transaction commit. 29 words
  // covers three complete legacy0x56 packets and one0x54 triangle.
  for(unsigned i=0;i<29;i++)t.emit(0xa0000000+i,false,false);
  require(t.visible.empty()&&t.d.output_wrptr==0,"triangle leaked before commit");
  for(unsigned i=0;i<30;i++)t.step();
  t.d.result_commit=1;t.step();t.d.result_commit=0;t.drain();
  // Completion can arrive one cycle after the final data handshake, while
  // the GPU/worker has just become disabled. Neither pulse may be lost.
  for(unsigned i=0;i<29;i++)t.emit(0xaa000000+i,false,false);
  t.d.enable=0;t.d.result_commit=1;t.step();t.d.result_commit=0;t.step();
  t.d.enable=1;t.drain();
  for(unsigned i=0;i<28;i++)t.emit(0xab000000+i,false,false);
  t.d.enable=0;t.d.result_abort=1;t.step();t.d.result_abort=0;t.step();
  t.d.enable=1;t.drain();
  // Abort wins even when a would-be final word and commit are simultaneous.
  for(unsigned i=0;i<28;i++)t.emit(0xb0000000+i,false,false);
  t.d.result_valid=1;t.d.result_data=0xdeadbeef;t.d.result_abort=1;t.d.result_commit=1;
  t.step();t.d.result_valid=t.d.result_abort=t.d.result_commit=0;
  require(t.pending.empty()&&t.visible.empty(),"abort failed atomic rollback");
  // A complete old queue drains while a private next triangle is assembling.
  for(unsigned i=0;i<63;i++)t.emit(0xc0000000+i,true,false);
  require(!t.d.result_ready,"full queue asserted ready");
  for(unsigned i=0;i<29;i++)t.emit(0xd0000000+i,false);
  t.d.result_commit=1;t.step();t.d.result_commit=0;t.drain();
  // Streaming legacy payloads larger than the output FIFO cannot deadlock.
  for(unsigned i=0;i<8191;i++)t.emit(0xe0000000+i,true);
  t.drain();
  // Input publication, pointer wrap and synchronous one-word-per-cycle reads.
  uint16_t in_append=0;
  for(unsigned batch=0;batch<160;batch++){
   std::vector<uint32_t> values;
   t.d.cmd_ready=0;
   unsigned count=1+random32()%83;
   for(unsigned i=0;i<count;i++){
    uint32_t w=random32();values.push_back(w);t.d.cpu_write=1;t.d.cpu_address=in_append;t.d.cpu_data=w;
    t.step();in_append=(in_append+1)&4095;
   }
   t.d.cpu_write=0;t.step();
   require(!t.d.cmd_valid,"unpublished CPU input escaped");
   for(auto w:values)t.input_expected.push_back(w);
   t.d.input_wrptr=in_append;t.step();
   while(!t.input_expected.empty()){
    t.d.cmd_ready=(random32()%4!=0);t.step();
   }
   t.d.cmd_ready=0;t.step();
  }
  // Random boundaries, both commit styles, stalls and repeated wraparound.
  for(unsigned job=0;job<14000;job++){
   unsigned count=random32()%30;
   bool abort=job%13==0;
   bool legacy=job%7==0;
   if(legacy)count=100+random32()%100;
   for(unsigned i=0;i<count;i++){
    bool commit=legacy||(!abort&&i+1==count&&(job&1));
    t.emit(random32(),commit);
    if(random32()%7==0){t.d.enable=0;t.step();t.d.enable=1;t.step(t.canconsume());}
   }
   t.d.result_abort=abort&&!legacy;t.d.result_commit=!legacy&&!t.d.result_abort&&(!(job&1)||count==0);
   t.step(t.canconsume()&&random32()%2);t.d.result_abort=t.d.result_commit=0;
   if(job%17==0)t.drain();
  }
  t.drain();
  // Reset cancels partial transactions and resets both output pointers.
  for(unsigned kind=0;kind<3;kind++){
   uint16_t tail=t.d.input_wrptr;
   t.d.cmd_ready=0;
   for(unsigned i=0;i<8;i++){
    uint32_t word=0x12340000+kind*8+i;t.d.cpu_write=1;t.d.cpu_address=tail;t.d.cpu_data=word;
    t.step();tail=(tail+1)&4095;t.input_expected.push_back(word);
   }
   t.d.cpu_write=0;t.step();t.d.input_wrptr=tail;t.step();
   require(t.d.cmd_valid,"reset test needs unread published input");
   for(unsigned i=0;i<3;i++)t.emit(0x7a000000+i,true,false);
   for(unsigned i=0;i<17;i++)t.emit(random32(),false,false);
   if(kind==0)t.d.reset_n=0;else if(kind==1)t.d.soft_reset=1;else t.d.ring_reset=1;
   t.step();t.d.reset_n=1;t.d.soft_reset=t.d.ring_reset=0;
   if(kind!=1)t.d.input_wrptr=0;
   t.step();
   require(t.d.input_rdptr==(kind==1?t.d.input_wrptr:0),"reset input pointer mismatch");
   t.emit(0xf0000000+kind,true);t.drain();
  }
  require(!t.d.busy,"transport did not become idle");
  std::cout<<"PASS cycles="<<t.cycles<<" accepted="<<t.accepted<<" consumed="<<t.consumed
   <<" commits="<<t.commits<<" aborts="<<t.aborted<<" input="<<t.input_count<<" blocked="<<t.blocked<<"\n";
  return 0;
 }catch(const std::exception&e){std::cerr<<"FAIL "<<e.what()<<"\n";return 1;}
}
