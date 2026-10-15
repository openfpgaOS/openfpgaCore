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
 const uint32_t base=0x1000,cb=0xf000;std::vector<uint32_t> mem,cmd,pushed,received;std::deque<uint32_t> pendingWords;
 bool pending=false,consume=false,blockAR=false,blockR=false;uint32_t address=0;unsigned left=0;
 uint32_t random(){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
 void ok(bool x,const char*s){checks++;if(!x)throw std::runtime_error(std::string(s)+" cycle="+std::to_string(cycles));}
 unsigned outputUsed(){return(d.write_pointer-d.read_pointer)&4095;}
 unsigned inputUsed(){return(producer-d.consumed)&4095;}
 void tick(){
  d.clk=0;d.m_arready=!pending&&!blockAR&&(random()%4!=0);
  if(!d.m_rvalid&&pending&&!blockR&&random()%3){d.m_rvalid=1;d.m_rdata=address>=cb?cmd.at((address-cb)/4):mem.at((address-base)/4);d.m_rlast=left==1;d.m_rresp=0;}
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
 void context(){float m[16]={1,0,0,0,0,1,0,0,0,0,1,1,0,0,0,2};append(0x6300001d);append(0);for(auto v:m)append(bits(v));append(bits(1));for(int i=17;i<22;i++)append(0);append(0xffffffff);append(0);for(int i=24;i<28;i++)append(100+i);}
 void vertices(bool partial=false){mem.clear();float xyz[3][3]={{-.25f,-.25f,-1},{.25f,-.25f,0},{0,.5f,2}};if(partial)xyz[0][0]=-4;for(auto&v:xyz){for(float a:v)mem.push_back(bits(a));mem.push_back(0);mem.push_back(0x22110000);mem.push_back(0x4433);}cmd={0x05000204,0};}
 void job(unsigned serial){uint32_t w[]={0x6400000b,serial,base,0x01003006,cb,1,0xabcd,0,0,0x12345000,0x54321000,255};for(auto a:w)append(a);}
 void ordinary(unsigned n,uint32_t seed){for(unsigned i=0;i<n;i++)append(seed);}
 void drain(){consume=true;wait([&]{return !d.busy&&outputUsed()==0;},"complete stream drain");ok(pendingWords.empty(),"residual output");}
 void consumerReset(){d.consumer_reset=1;d.read_pointer=0;pendingWords.clear();tick();d.consumer_reset=0;d.eval();}
};
int main(){try{Sim s;unsigned cases=0;
 // Both rings full while the head asks for CPU repair: head insertion must make progress.
 s.vertices(true);s.reset();s.context();s.job(1);s.publish();s.wait([&]{return s.st()&2;},"repair missing");
 s.ordinary(4095,0x4a000000);s.publish();s.ok(s.inputUsed()==4095&&s.d.busy,"ingress not fully blocked behind repair");s.wr(0x29,s.rd(0x25));
 for(unsigned i=0;i<511;i++){s.ok(s.st()&64,"repair lost initial output credit");s.wr(0x28,0x70000000+i);}
 s.ok(s.outputUsed()==511&&!(s.st()&64),"output did not backpressure repair");for(int i=0;i<37;i++)s.tick();
 s.consume=true;for(unsigned i=511;i<1300;i++){s.wait([&]{return s.st()&64;},"repair credit deadlock");s.wr(0x28,0x70000000+i);}
 s.wr(0x2a,1);s.drain();s.ok(s.rd(0x23)==1&&s.pushed.size()==5395,"repaired stream length/completion");
 for(unsigned i=0;i<1300;i++)s.ok(s.pushed[i]==0x70000000+i,"repair prefix order");for(unsigned i=0;i<4095;i++)s.ok(s.pushed[1300+i]==0x4a000000,"queued ordinary tail order");s.ok(!s.d.fault&&!(s.st()&4),"repair caused fault");cases++;
 // A legacy lease waits generated packet copy, not downstream raster consumption.
 s.vertices();s.reset();s.context();s.ordinary(500,0x4a000000);s.job(2);s.publish();
 s.wait([&]{return s.outputUsed()==511;},"autonomous output not stalled");s.wr(0x2b,2);s.wr(0x2c,88);s.wr(0x2d,1);for(int i=0;i<30;i++){s.tick();s.ok(!(s.st()&16),"lease granted before copied output");}
 s.consume=true;s.wait([&]{return s.st()&16;},"lease failed to grant");s.consume=false;s.ok(s.outputUsed()>0,"lease accidentally required raster drain");s.ok(s.rd(0x23)==2&&s.pushed.size()==529,"packet incomplete on lease");
 s.wr(3,s.base);s.wr(4,3);s.wr(2,1);s.ordinary(50,0x4b000000);s.publish();s.consume=true;s.wait([&]{return !(s.rd(1)&1);},"legacy LOAD under ordinary ring");s.drain();s.ok(s.pushed.size()==579&&!s.d.fault,"ordinary ring blocked by legacy lease");s.wr(0x2d,2);s.wait([&]{return !(s.st()&16);},"lease release");cases++;
 // Coupled consumer reset discards a partial repair and changes its token.
 s.vertices(true);s.context();s.job(3);s.publish();s.wait([&]{return s.st()&2;},"reset repair missing");auto old=s.rd(0x25);s.wr(0x29,old);s.consume=false;for(unsigned i=0;i<15;i++)s.wr(0x28,0x80000000+i);
 s.consumerReset();s.wait([&]{return !(s.rd(1)&1);},"repair reset raw drain");s.ok(s.outputUsed()==0&&!s.d.busy&&s.rd(0x25)!=old,"coupled reset retained output/token");
 s.job(4);s.publish();s.wait([&]{return s.st()&2;},"fresh repair missing");s.wr(0x29,old);s.wr(0x2a,1);s.tick();s.ok(s.d.fault&&(s.st()&4),"stale END did not fault entire front");cases++;
 std::cout<<"{\"status\":\"PASS\",\"cases\":"<<cases<<",\"checks\":"<<s.checks<<",\"cycles\":"<<s.cycles<<",\"AXI_bursts\":"<<s.reads<<",\"ingress_full_cycles\":"<<s.fullInput<<",\"output_full_cycles\":"<<s.fullOutput<<"}\n";return 0;
}catch(const std::exception&e){std::cerr<<e.what()<<"\n";return 1;}}
