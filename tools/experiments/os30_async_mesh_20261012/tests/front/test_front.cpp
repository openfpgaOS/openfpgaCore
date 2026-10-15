#include "Vtb_front.h"
#include <verilated.h>
#include <array>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <deque>
#include <vector>

static uint64_t checks=0, cycles=0;
static uint32_t rng=0x164c927a;
static uint32_t random_word(){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
static void check(bool ok,const char* what){++checks;if(!ok){std::fprintf(stderr,"cycle%llu: %s\n",(unsigned long long)cycles,what);std::abort();}}
struct Job {std::array<uint32_t,11> fields;std::array<uint32_t,28> context;std::vector<uint32_t> words;};
struct Fixture {
 Vtb_front t;
 unsigned producer=0;
 void low(){t.clk=0;t.eval();}
 void tick(){low();t.clk=1;t.eval();++cycles;t.clk=0;t.eval();}
 void reset(){
  t.enable=1;t.reset_n=0;t.soft_reset=0;t.ring_reset=0;t.upload_we=0;t.published=0;
  t.read_pointer=0;t.ctx_ready=0;t.job_ready=0;t.word_valid=0;t.job_done=0;t.job_fault=0;
  tick();tick();t.reset_n=1;tick();producer=0;
  check(!t.fault&&!t.busy&&t.consumed==0&&t.write_pointer==0,"reset state");
 }
 void append(uint32_t word){t.upload_we=1;t.upload_addr=producer;t.upload_data=word;tick();t.upload_we=0;producer=(producer+1)&4095;}
 void publish(){
  // The GPU publishes its registered pointer on a separate MMIO edge after
  // the append. The front observes that pointer on the following edge.
  tick();t.published=producer;tick();
 }
};

static void ordered_stream(){
 Fixture f;f.reset();auto&t=f.t;
 std::vector<std::vector<uint32_t>> packets;
 std::vector<uint32_t> expected;
 std::vector<std::pair<unsigned,uint32_t>> contexts;
 std::vector<Job> jobs;
 std::array<uint32_t,28> current{};
 auto context=[&](unsigned base,unsigned n){
  std::vector<uint32_t> p={0x63000000u+n+1,base};
  for(unsigned i=0;i<n;++i){uint32_t w=random_word();p.push_back(w);current[base+i]=w;contexts.emplace_back(base+i,w);}
  packets.push_back(p);
 };
 context(0,28);
 for(unsigned i=0;i<400;++i){
  unsigned n=(i%7==0)?130:1+i%17;
  std::vector<uint32_t> ordinary={0x4a000000u+n};
  for(unsigned k=0;k<n;++k)ordinary.push_back(random_word());
  packets.push_back(ordinary);expected.insert(expected.end(),ordinary.begin(),ordinary.end());
  if(i%3==0){unsigned base=random_word()%28;context(base,1+random_word()%(28-base));}
  Job j;
  for(unsigned k=0;k<11;++k)j.fields[k]=0x12000000u+(i<<12)+k;
  j.fields[0]=i+1;j.context=current;
  // Long outputs model a streamed clipping repair larger than the FIFO.
  unsigned words=i%29==0?1900:(i%13==0?0:25+i%391);
  for(unsigned k=0;k<words;++k)j.words.push_back(0x84000000u+(i<<12)+k);
  std::vector<uint32_t> p={0x6400000bu};p.insert(p.end(),j.fields.begin(),j.fields.end());packets.push_back(p);
  expected.insert(expected.end(),j.words.begin(),j.words.end());jobs.push_back(j);
 }
 size_t packet=0,offset=0,ctx=0,job=0,pushed=0,read=0;
 std::array<uint32_t,28> observed{};
 const Job*active=nullptr;size_t job_word=0;unsigned delay=0;bool finish=false;
 uint64_t full_cycles=0,held_words=0,context_stalls=0,disabled=0;
 unsigned max_used=0,input_wraps=0,output_wraps=0;
 for(unsigned c=0;c<2000000;++c){
  t.upload_we=0;t.enable=(c%997>=23);disabled+=!t.enable;
  t.ctx_ready=(random_word()%5)!=0;t.job_ready=!active&&!finish&&(random_word()%4)!=0;
  t.word_valid=active&&delay==0&&job_word<active->words.size();
  t.word_data=t.word_valid?active->words[job_word]:0;
  t.job_done=finish;
  bool uploaded=false,last_word=false;
  if(packet<packets.size()&&((f.producer-t.consumed)&4095)<4095&&(random_word()%5)!=0){
   t.upload_we=1;t.upload_addr=f.producer;t.upload_data=packets[packet][offset];uploaded=true;
   last_word=offset+1==packets[packet].size();
  }
  f.low();
  unsigned used=(t.write_pointer-t.read_pointer)&4095;
  check(used<512,"bounded FIFO occupancy");if(used>max_used)max_used=used;
  full_cycles+=used==511;held_words+=t.word_valid&&!t.word_ready;context_stalls+=t.ctx_valid&&!t.ctx_ready;
  bool consume=used&&((c%4000)<3000)&&(random_word()%4)!=0;
  if(t.ctx_valid&&t.ctx_ready){
   check(ctx<contexts.size(),"unexpected context write");
   check(t.ctx_index==contexts[ctx].first&&t.ctx_data==contexts[ctx].second,"ordered context exact");
   observed[t.ctx_index]=t.ctx_data;++ctx;
  }
  if(t.job_valid&&t.job_ready){
   check(job<jobs.size()&&!active,"unexpected job");
   std::array<uint32_t,11> fields={t.j0,t.j1,t.j2,t.j3,t.j4,t.j5,t.j6,t.j7,t.j8,t.j9,t.j10};
   check(fields==jobs[job].fields,"job fields exact");check(observed==jobs[job].context,"job context exact");
   active=&jobs[job++];job_word=0;delay=7+(random_word()%29);
  }
  bool accepted=t.word_valid&&t.word_ready;
  if(t.push){check(pushed<expected.size()&&t.pushed_word==expected[pushed],"FIFO insertion preserves full stream order");++pushed;}
  f.tick();check(!t.fault,"unexpected front fault");
  if(consume){check(read<expected.size()&&t.read_data==expected[read],"actual FIFO read exact");++read;
   unsigned old=t.read_pointer;t.read_pointer=(old+1)&4095;output_wraps+=old==4095;}
  if(uploaded){unsigned old=f.producer;f.producer=(old+1)&4095;input_wraps+=old==4095;
   ++offset;if(last_word){t.published=f.producer;++packet;offset=0;}}
  if(finish)finish=false;
  if(active){
   if(delay)--delay;
   if(accepted)++job_word;
   if(!delay&&job_word==active->words.size()){active=nullptr;finish=true;}
  }
  if(packet==packets.size()&&read==expected.size()&&!active&&!finish&&!t.busy)break;
 }
 check(read==expected.size()&&pushed==expected.size(),"stream fully drained");
 check(ctx==contexts.size()&&job==jobs.size(),"all jobs/context consumed");
 check(full_cycles>1000&&held_words>1000&&context_stalls>100&&disabled>100,"backpressure/disable coverage");
 check(input_wraps>=3&&output_wraps>=10,"logical ring wrap coverage");
 std::printf("ordered_stream jobs=%zu words=%zu max_used=%u full_cycles=%llu stalled_words=%llu input_wraps=%u output_wraps=%u\n",jobs.size(),expected.size(),max_used,(unsigned long long)full_cycles,(unsigned long long)held_words,input_wraps,output_wraps);
}

static void faults_and_reset(){
 Fixture f;
 for(auto packet:std::vector<std::vector<uint32_t>>{{0x63000001u},{0x6300001eu},{0x63000002u,28,0},{0x63000003u,27,1,2},{0x6400000au}}){
  f.reset();for(auto w:packet)f.append(w);f.publish();
  for(unsigned i=0;i<30&&!f.t.fault;++i)f.tick();check(f.t.fault,"malformed job/context must fault");
  f.t.enable=0;for(unsigned i=0;i<5;++i)f.tick();check(f.t.fault,"fault sticky while disabled");
 }
 f.reset();f.append(0x6400000bu);for(unsigned i=0;i<11;++i)f.append(i+1);f.publish();
 for(unsigned i=0;i<30&&!f.t.job_valid;++i)f.tick();check(f.t.job_valid,"job held before acceptance");
 f.t.job_fault=1;f.tick();check(f.t.fault,"wrapper fault in REQUEST propagated");
 f.reset();f.append(0x6400000bu);for(unsigned i=0;i<11;++i)f.append(i+1);f.publish();
 f.t.job_ready=1;for(unsigned i=0;i<30&&!f.t.word_ready;++i)f.tick();check(f.t.word_ready,"stream entered");
 f.t.job_ready=0;f.t.word_valid=1;f.t.word_data=0x12345678;f.tick();check(f.t.write_pointer==1,"stream word stored");
 f.t.enable=0;f.t.job_fault=1;f.tick();check(f.t.fault,"wrapper fault while disabled preserved");
 // Ring reset drops both partial output and ingress, then a new stream starts clean.
 f.t.ring_reset=1;f.t.word_valid=0;f.t.job_fault=0;f.t.published=0;f.tick();
 f.t.ring_reset=0;f.t.enable=1;f.producer=0;f.tick();
 check(!f.t.fault&&!f.t.busy&&f.t.write_pointer==0&&f.t.consumed==0,"coupled reset clears partial stream");
 f.append(0x4a000001);f.append(0xabcdef12);f.publish();
 for(unsigned i=0;i<20;++i)f.tick();check(f.t.write_pointer==2&&!f.t.fault,"post-reset ordinary stream");
 f.t.soft_reset=1;f.tick();f.t.soft_reset=0;f.tick();
 check(f.t.consumed==f.t.published&&f.t.write_pointer==0&&!f.t.fault,"soft reset skips published input and clears output");
}
int main(int argc,char**argv){Verilated::commandArgs(argc,argv);ordered_stream();faults_and_reset();std::printf("PASS checks=%llu cycles=%llu\n",(unsigned long long)checks,(unsigned long long)cycles);}
