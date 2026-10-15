#include "Vtb_async_combined.h"
#include "verilated.h"
#include <cstdint>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <vector>
#include <deque>
static uint32_t bits(float x){uint32_t u;memcpy(&u,&x,4);return u;}
struct Sim{
 Vtb_async_combined d;uint64_t cycles=0,checks=0,reads=0,fullInput=0,fullOutput=0;uint32_t rng=531;unsigned producer=0;
 const uint32_t base=0x1000,cb=0xf000,db=0x20000;std::vector<uint32_t> mem,cmd,desc,pushed,received;std::deque<uint32_t> pendingWords;
 bool pending=false,consume=false,blockAR=false,blockR=false;uint32_t address=0;unsigned left=0;
 uint32_t random(){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
 void ok(bool x,const char*s){checks++;if(!x)throw std::runtime_error(std::string(s)+" cycle="+std::to_string(cycles));}
 unsigned outputUsed(){return(d.write_pointer-d.read_pointer)&4095;}
 unsigned inputUsed(){return(producer-d.consumed)&4095;}
 void tick(){
  d.clk=0;d.m_arready=!pending&&!blockAR&&(random()%4!=0);
  if(!d.m_rvalid&&pending&&!blockR&&random()%3){d.m_rvalid=1;d.m_rdata=address>=db?desc.at((address-db)/4):address>=cb?cmd.at((address-cb)/4):mem.at((address-base)/4);d.m_rlast=left==1;d.m_rresp=0;}
  d.eval();unsigned used=outputUsed();if(!d.reset&&!d.consumer_reset)ok(used<512,"output overflow");fullInput+=inputUsed()==4095;fullOutput+=used==511;
  bool pop=consume&&used&&!d.consumer_reset&&(random()%4!=0);
  if(d.push){pushed.push_back(d.pushed_word);pendingWords.push_back(d.pushed_word);}
  bool ar=d.m_arvalid&&d.m_arready,r=d.m_rvalid&&d.m_rready;auto a=d.m_araddr;unsigned len=d.m_arlen+1;
  if(ar)ok(!pending,"multiple AXI readers");
  d.clk=1;d.eval();cycles++;
  if(pop){ok(!pendingWords.empty()&&d.read_data==pendingWords.front(),"actual FIFO data order");received.push_back(d.read_data);pendingWords.pop_front();d.read_pointer=(d.read_pointer+1)&4095;}
  if(r){address+=4;if(!--left)pending=false;d.m_rvalid=0;}
  if(ar){pending=true;address=a;left=len;reads++;}
  d.clk=0;d.eval();
 }
 template<class F>void wait(F f,const char*message,unsigned limit=100000){for(unsigned n=0;!f();n++){tick();if(n>limit)throw std::runtime_error(std::string(message)+" cycle="+std::to_string(cycles)+" status="+std::to_string(st()));}}
 void wr(unsigned a,uint32_t v){d.reg_wr=1;d.reg_addr=a;d.reg_wdata=v;d.reg_wstrb=15;tick();d.reg_wr=0;d.eval();}
 uint32_t rd(unsigned a){d.reg_addr=a;d.eval();return d.reg_rdata;}
 uint32_t st(){return rd(0x21);}
 void reset(){d.reset=1;d.enable=1;d.upload_we=0;d.published=d.read_pointer=0;d.reg_wr=0;d.view_active=0;d.consumer_reset=0;d.m_rvalid=0;pending=false;producer=0;pendingWords.clear();pushed.clear();received.clear();consume=false;for(int n=0;n<7;n++)tick();d.reset=0;}
 void append(uint32_t w){ok(inputUsed()<4095,"CPU overwrote full ingress");d.upload_we=1;d.upload_addr=producer;d.upload_data=w;tick();d.upload_we=0;producer=(producer+1)&4095;}
 void publish(){tick();d.published=producer;tick();}
 void context(float xscale=1){float m[16]={xscale,0,0,0,0,1,0,0,0,0,1,1,0,0,0,2};append(0x6300001d);append(0);for(auto v:m)append(bits(v));append(bits(1));for(int i=17;i<22;i++)append(0);append(0xffffffff);append(0);for(int i=24;i<28;i++)append(100+i);}
 void vertices(bool partial=false){mem.clear();float xyz[3][3]={{-.25f,-.25f,-1},{.25f,-.25f,0},{0,.5f,2}};if(partial)xyz[0][0]=-4;for(auto&v:xyz){for(float a:v)mem.push_back(bits(a));mem.push_back(0);mem.push_back(0x22110000);mem.push_back(0x4433);}cmd={0x05000204,0};}
 void job(unsigned serial){uint32_t w[]={0x6400000b,serial,base,0x01003006,cb,1,0xabcd,0,0,0x12345000,0x54321000,255};for(auto a:w)append(a);}
 void group(unsigned serial,unsigned count){desc.clear();for(unsigned i=0;i<count;i++){std::vector<uint32_t>a={base,0x01003006,cb,1,0x12345000+i*72,0x54321000+i*8};desc.insert(desc.end(),a.begin(),a.end());}uint32_t h[]={0x65000007,serial,db,count,0xabcd,0,0,255};for(auto a:h)append(a);}
 void ordinary(unsigned n,uint32_t seed){for(unsigned i=0;i<n;i++)append(seed);}
 void drain(){consume=true;wait([&]{return !d.busy&&outputUsed()==0;},"complete stream drain");ok(pendingWords.empty(),"residual output");}
 uint32_t materialize(unsigned row,unsigned slot){ok(!d.view_block,"materialize blocked");d.view_active=1;d.view_read_addr=row*32+slot;tick();auto v=d.view_read_data;d.view_active=0;d.eval();return v;}
 void releaseBeforeProgress(){materialize(0,0);wr(0x2d,2);wait([&]{return !(st()&16);},"release before dependent progress");}
 void consumerReset(){d.consumer_reset=1;d.read_pointer=0;pendingWords.clear();tick();d.consumer_reset=0;d.eval();}
};
int main(){try{Sim s;unsigned cases=0;
 // Both ingress and output full during repair retain a live head insertion path.
 s.vertices(true);s.reset();s.context();s.job(1);s.publish();s.wait([&]{return s.st()&2;},"repair missing");
 s.ordinary(4095,0x4a000000);s.publish();s.ok(s.inputUsed()==4095,"ingress not full");s.wr(0x29,s.rd(0x25));
 for(unsigned i=0;i<511;i++){s.ok(s.st()&64,"initial repair credit");s.wr(0x28,0x70000000+i);}s.ok(!(s.st()&64),"output not full");
 s.wr(0x2b,99);s.wr(0x2c,80);s.wr(0x2d,1);for(int i=0;i<37;i++){s.tick();s.ok(!(s.st()&16),"priority interrupted repair");}
 s.consume=true;for(unsigned i=511;i<1300;i++){s.wait([&]{return s.st()&64;},"head repair deadlock");s.wr(0x28,0x70000000+i);}s.wr(0x2a,1);
 s.wait([&]{return s.st()&16;},"lease after repair");s.releaseBeforeProgress();s.drain();s.ok(s.rd(0x23)==1&&s.pushed.size()==5395,"repair completion/order");
 for(unsigned i=0;i<1300;i++)s.ok(s.pushed[i]==0x70000000+i,"repair prefix");for(unsigned i=0;i<4095;i++)s.ok(s.pushed[1300+i]==0x4a000000,"ordinary tail");cases++;
 // Two immutable queued jobs: priority stops at job1, while job2 stays in ingress.
 // A real legacy LOAD overwrites all shared transform/scalar context. Job2's
 // full saved context must restore its own xscale=2 after materialize+release.
 s.vertices();s.reset();s.ordinary(500,0x4a000000);s.context();s.job(1);s.context(2);s.job(2);s.publish();
 s.wait([&]{return s.outputUsed()==511;},"job1 output stall missing");s.wr(0x2b,2);s.wr(0x2c,90);s.wr(0x2d,1);
 s.wait([&]{return s.st()&16;},"snapshot priority while output full");
 s.ok(s.rd(0x23)==0&&s.pushed.size()==511&&s.outputUsed()==511,"priority still waited output drain");
 float legacy[16]={3,0,0,0,0,2,0,0,0,0,1,1,0,0,0,2};for(unsigned i=0;i<16;i++)s.wr(0x40+i,bits(legacy[i]));
 s.wr(5,bits(.5));s.wr(6,bits(.25));s.wr(7,bits(.5));s.wr(8,bits(.75));s.wr(9,0x112233);s.wr(10,0x778899);s.wr(11,0x12345678);s.wr(12,0);s.wr(3,s.base);s.wr(4,3);s.wr(2,1);s.wait([&]{return !(s.rd(1)&1);},"legacy LOAD");
 s.ok(s.materialize(0,0)!=bits(-.25f),"legacy overwrite not exercised");unsigned tail=4095-s.inputUsed();s.ordinary(tail,0x4b000000);s.publish();s.ok(s.inputUsed()==4095,"full ingress progress case absent");
 s.consume=true;s.wait([&]{return s.rd(0x23)==1;},"old snapshot did not drain during retained lease");for(int i=0;i<40;i++){s.tick();s.ok(s.rd(0x23)==1&&s.d.busy,"queued job escaped retained lease");}
 s.releaseBeforeProgress();s.drain();s.ok(s.rd(0x23)==2&&s.pushed.size()==558+tail,"queued job2 did not finish");
 s.ok(s.pushed[500]==0x56000008&&s.pushed[502]==uint32_t(-64),"job1 payload");s.ok(s.pushed[529]==0x56000008&&s.pushed[531]==uint32_t(-128),"job2 lost immutable restored context");s.ok(!s.d.fault&&!(s.st()&4),"priority restore fault");cases++;
 // Strict front framing rejects dirty fragments, missing JOB and missing context.
 for(unsigned v=0;v<5;v++){
  s.reset();s.vertices();
  if(v==0){s.append(0x63000002);s.append(0);s.append(0);}
  else if(v==1){s.append(0x6300001d);s.append(1);for(int i=0;i<28;i++)s.append(0);}
  else if(v==2){s.context();s.append(0x4a000000);}
  else if(v==3)s.job(1);
  else{s.context();s.context();}
  s.publish();s.wait([&]{return s.d.fault;},"malformed full-context contract not rejected");s.ok(s.st()&4,"front fault not visible in wrapper");cases++;
 }
 // Reset in an opened context cancels the pending acquire and allows a new lease.
 s.reset();s.vertices();s.append(0x6300001d);s.append(0);for(unsigned i=0;i<8;i++)s.append(i==0?bits(1):0);s.publish();for(int i=0;i<30;i++)s.tick();s.wr(0x2b,4);s.wr(0x2c,91);s.wr(0x2d,1);s.ok(!(s.st()&16),"partial context grant");s.consumerReset();s.ok(!(s.st()&24),"partial context reset retained pending lease");
 s.wr(0x2b,4);s.wr(0x2c,92);s.wr(0x2d,1);s.wait([&]{return s.st()&16;},"post-reset independent priority acquire");s.wr(0x2d,2);s.wait([&]{return !(s.st()&16);},"release reset lease");s.context();s.job(1);s.publish();s.drain();s.ok(s.rd(0x23)==1,"fresh context/job after reset");cases++;
 // One real GROUP in ingress spans three immutable members. With both
 // queues full, CPU raw priority may preempt after snapshot1; descriptors and
 // common context survive its writes, then member2/3 resume ahead of the tail.
 s.vertices();s.reset();s.ordinary(500,0x4a000000);s.context();s.group(1,3);s.publish();
 s.wait([&]{return s.outputUsed()==511;},"group output stall missing");s.wr(0x2b,3);s.wr(0x2c,900);s.wr(0x2d,1);s.wait([&]{return s.st()&16;},"group snapshot priority");
 for(unsigned i=0;i<16;i++)s.wr(0x40+i,bits(legacy[i]));s.wr(5,bits(.5));s.wr(6,bits(.25));s.wr(7,bits(.5));s.wr(8,bits(.75));s.wr(9,0x112233);s.wr(10,0x778899);s.wr(11,0x12345678);s.wr(12,0);s.wr(3,s.base);s.wr(4,3);s.wr(2,1);s.wait([&]{return !(s.rd(1)&1);},"group priority raw LOAD");
 s.ok(s.materialize(0,0)!=bits(-.25f),"group legacy poison absent");s.ordinary(4095-s.inputUsed(),0x4b000000);s.publish();s.consume=true;s.wait([&]{return s.rd(0x23)==1;},"group first snapshot output");
 for(unsigned i=0;i<40;i++){s.tick();s.ok(s.rd(0x23)==1&&s.d.busy,"group escaped retained lease");}s.releaseBeforeProgress();s.drain();
 s.ok(s.rd(0x23)==3&&s.pushed.size()==500+3*29+4095,"group completion/tail count");for(unsigned m=0;m<3;m++){s.ok(s.pushed[500+m*29]==0x56000008&&s.pushed[502+m*29]==uint32_t(-64),"group restored payload");}cases++;
 // Streaming repair cannot deadlock behind full ingress, even for a second
 // GROUP member after a CPU legacy handoff. No ordinary tail passes either.
 s.vertices(true);s.reset();s.context();s.group(1,2);s.publish();s.wait([&]{return s.st()&2;},"group first repair");s.ordinary(4095,0x4a000000);s.publish();
 s.wr(0x29,s.rd(0x25));for(unsigned i=0;i<511;i++)s.wr(0x28,0x73000000+i);s.ok(!(s.st()&64),"group repair output full");
 s.wr(0x2b,2);s.wr(0x2c,901);s.wr(0x2d,1);s.consume=true;for(unsigned i=511;i<800;i++){s.wait([&]{return s.st()&64;},"group repair progress");s.wr(0x28,0x73000000+i);}s.wr(0x2a,1);s.wait([&]{return s.st()&16;},"group repair priority");
 s.releaseBeforeProgress();s.wait([&]{return s.st()&2;},"group second repair");s.wr(0x29,s.rd(0x25));for(unsigned i=0;i<900;i++){s.wait([&]{return s.st()&64;},"group second repair credit");s.wr(0x28,0x74000000+i);}s.wr(0x2a,1);s.drain();
 s.ok(s.rd(0x23)==2&&s.pushed.size()==1700+4095,"group repair completion count");for(unsigned i=0;i<800;i++)s.ok(s.pushed[i]==0x73000000+i,"group repair first order");for(unsigned i=0;i<900;i++)s.ok(s.pushed[800+i]==0x74000000+i,"group repair second order");cases++;
 for(unsigned v=0;v<2;v++){s.reset();s.vertices();if(v==0)s.group(1,2);else{s.context();s.append(0x65000008);}s.publish();s.wait([&]{return s.d.fault;},"bad GROUP framing accepted");s.ok(s.st()&4,"GROUP front fault not visible");cases++;}
 std::cout<<"{\"status\":\"PASS\",\"cases\":"<<cases<<",\"checks\":"<<s.checks<<",\"cycles\":"<<s.cycles<<",\"AXI_bursts\":"<<s.reads<<",\"ingress_full_cycles\":"<<s.fullInput<<",\"output_full_cycles\":"<<s.fullOutput<<"}\n";return 0;
}catch(const std::exception&e){std::cerr<<e.what()<<"\n";return 1;}}
