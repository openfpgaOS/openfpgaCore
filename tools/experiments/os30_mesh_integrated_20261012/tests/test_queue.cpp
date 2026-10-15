#include "Vgeometry_mesh_queue.h"
#include "verilated.h"
#include <cstdint>
#include <deque>
#include <vector>
#include <iostream>
#include <stdexcept>
#include <algorithm>
struct Packet {uint32_t token,flags,u,v;std::vector<uint32_t> words;bool committed=false;};
struct Sim {
 Vgeometry_mesh_queue d;uint32_t rng=0x825d33a1,serial=1;uint64_t checks=0,cycles=0,words=0,stalls=0,concurrent=0,packets=0,reservations=0,blocked=0; 
 uint32_t random(){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
 void ok(bool b,const char*s){++checks;if(!b)throw std::runtime_error(std::string(s)+" cycle="+std::to_string(cycles));}
 void eval(){d.clk=0;d.eval();}void edge(){d.clk=1;d.eval();++cycles;d.clk=0;d.eval();}
 void reset(){d.reset=1;d.reserve=d.producer_valid=d.producer_done=d.producer_error=d.marker_valid=d.word_ready=0;eval();edge();edge();d.reset=0;eval();ok(!d.busy&&!d.error&&!d.word_valid,"reset retained queue ownership");}
 Packet make(unsigned n){Packet p{serial++,random(),random(),random(),{},false};for(unsigned i=0;i<n;++i)p.words.push_back(random());return p;}
 void marker(const Packet&p){d.marker_valid=1;d.marker_serial=p.token;d.marker_flags=p.flags;d.marker_uofs=p.u;d.marker_vofs=p.v;}
 void descriptor(const Packet&p){d.reserve_serial=p.token;d.reserve_flags=p.flags;d.reserve_uofs=p.u;d.reserve_vofs=p.v;d.reserve_words=p.words.size();}
 void stream(unsigned total,unsigned mode){
  reset();std::deque<Packet> expected;Packet pending=make(mode==1?416:mode==2?0:29);bool filling=false,draining=false,producer_held=false,consumer_held=false;size_t produced=0,consumed=0;unsigned fill_age=0;uint32_t heldword=0;unsigned offered=0,finished=0;uint64_t deadline=cycles+2000000;
  while(finished<total){ok(cycles<deadline,"random queue traffic deadlock");
   d.reserve=!filling&&offered<total;descriptor(pending);
   d.producer_valid=0;d.producer_done=0;
   if(filling){auto &p=expected.back();if(produced<p.words.size()){d.producer_valid=producer_held||random()%4!=0;d.producer_word=p.words[produced];}else if(fill_age>=20)d.producer_done=1;}
   d.marker_valid=0;if(!draining&&!expected.empty()&&(mode!=3||offered>=3||cycles%997>900))marker(expected.front());
   d.word_ready=draining&&(mode==1?cycles%101>80:random()%4==0);eval();
   ok(!d.error,"legal traffic faulted");if(consumer_held)ok(d.word_valid&&d.word_data==heldword,"stalled consumer word not stable");
   bool reserve=d.reserve&&d.reserve_ready,push=d.producer_valid&&d.producer_ready,commit=d.producer_done,mark=d.marker_valid&&d.marker_ready,pop=d.word_valid&&d.word_ready;
   if(d.reserve&&!d.reserve_ready)++blocked;
   if(d.marker_valid)ok(!d.marker_error,"matching head marker rejected during fill/header");
   if(mark){ok(!expected.empty()&&expected.front().committed,"marker accepted uncommitted packet");ok(d.owner_serial==expected.front().token,"head token not immutable");}
   if(d.word_valid){ok(draining&&!expected.empty(),"output before matching marker");ok(consumed<expected.front().words.size(),"extra output word");ok(d.word_data==expected.front().words[consumed],"payload corruption/order/wrap failure");}
   if(d.word_valid&&!d.word_ready)++stalls;
   consumer_held=d.word_valid&&!d.word_ready;heldword=d.word_data;producer_held=d.producer_valid&&!d.producer_ready;
   if(filling&&draining)++concurrent;if(filling)++fill_age;
   edge();
   if(pop){++consumed;++words;if(consumed==expected.front().words.size()){ok(d.done,"last pop did not signal done");expected.pop_front();draining=false;consumed=0;++finished;++packets;}else ok(!d.done,"early completion");}
   if(mark){if(expected.front().words.empty()){ok(d.done,"zero packet missing done");expected.pop_front();++finished;++packets;}else{draining=true;consumed=0;}}
   if(push)++produced;
   if(commit){ok(filling&&!expected.empty(),"host commit state");expected.back().committed=true;filling=false;produced=0;producer_held=false;}
   if(reserve){expected.push_back(pending);filling=true;produced=0;fill_age=0;++offered;++reservations;
    unsigned n=mode==1?416:mode==2?offered%3:mode==3?40:random()%417;pending=make(n);}
  }
  d.reserve=d.producer_valid=d.producer_done=d.marker_valid=d.word_ready=0;for(unsigned i=0;i<10;i++){eval();edge();}ok(!d.busy&&!d.word_valid&&!d.error,"final queue not empty");
 }
 void reserve_one(const Packet&p){d.reserve=1;descriptor(p);for(unsigned i=0;i<20;i++){eval();if(d.reserve_ready){edge();d.reserve=0;return;}edge();}ok(false,"empty reservation rejected");}
 void fill_one(Packet&p,unsigned sent){unsigned n=0;while(n<sent){d.producer_valid=1;d.producer_word=n<p.words.size()?p.words[n]:0xdead;eval();bool push=d.producer_ready;edge();if(push)++n;ok(!d.error,"fill fault before commit");}d.producer_valid=0;d.producer_done=1;eval();edge();d.producer_done=0;eval();p.committed=true;}
 void faults(){
  reset();auto p=make(20);reserve_one(p);fill_one(p,19);ok(d.error&&!d.word_valid,"short payload commit accepted");reset();
  p=make(4);reserve_one(p);d.producer_error=1;eval();edge();d.producer_error=0;eval();ok(d.error&&!d.word_valid,"producer fault not contained");reset();
  p=make(417);d.reserve=1;descriptor(p);eval();ok(!d.reserve_ready,"oversized count accepted");edge();d.reserve=0;reset();
  p=make(32);reserve_one(p);fill_one(p,32);marker(p);d.marker_serial++;for(unsigned i=0;i<30;i++){eval();ok(!d.marker_ready&&!d.word_valid,"stale marker consumed data");if(d.marker_error)break;edge();}eval();ok(d.marker_error,"stale marker never diagnosed");d.marker_valid=0;reset();
  p=make(416);reserve_one(p);for(unsigned i=0;i<20;i++){d.producer_valid=1;d.producer_word=i;eval();edge();}reset();
  p=make(40);reserve_one(p);fill_one(p,40);marker(p);for(unsigned i=0;i<30;i++){eval();if(d.marker_ready){edge();break;}edge();}d.marker_valid=0;d.word_ready=1;for(unsigned i=0;i<9;i++){eval();edge();}d.word_ready=0;eval();ok(d.word_valid,"reset fixture not mid-drain");reset();
 }
};
int main(){try{Sim s;s.stream(300,0);s.stream(30,1);s.stream(200,2);s.stream(200,3);s.faults();s.stream(30,0);s.ok(s.concurrent>1000,"no concurrent fill/drain coverage");s.ok(s.blocked>1000,"no capacity backpressure coverage");std::cout<<"{\"status\":\"PASS\",\"packets\":"<<s.packets<<",\"checks\":"<<s.checks<<",\"cycles\":"<<s.cycles<<",\"words\":"<<s.words<<",\"stalled_cycles\":"<<s.stalls<<",\"concurrent_fill_drain_cycles\":"<<s.concurrent<<",\"reservation_blocked_cycles\":"<<s.blocked<<"}\n";return 0;}catch(const std::exception&e){std::cerr<<e.what()<<"\n";return 1;}}
