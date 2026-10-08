#include "Vtb_ring.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <stdexcept>
static Vtb_ring t;
static uint64_t cycles=0,checked=0,rejected=0,dmawords=0;
static std::array<uint32_t,4096> ref{};
static void require(bool v,const char* what){if(!v)throw std::runtime_error(what);}
static void tick(){t.clk=0;t.eval();t.clk=1;t.eval();++cycles;t.clk=0;t.eval();}
static void wr(unsigned r,uint32_t v){t.reg_wr=1;t.reg_addr=r;t.reg_wdata=v;tick();t.reg_wr=0;t.eval();}
static uint32_t word(unsigned n){return 0xe0000000u|((n&2047u)<<13);}
static void equal(){require(t.append0==t.append1&&t.publish0==t.publish1&&t.read0==t.read1&&t.overflow0==t.overflow1,"DMA option altered CPU ring state");}
static void peek(unsigned a,uint32_t expected,bool both=true){t.peek_addr=a;t.eval();require(t.peek0==expected,"DMA-off ring data mismatch");if(both)require(t.peek1==expected,"DMA-on CPU ring data mismatch");++checked;}
static void drain(){for(unsigned i=0;i<100000;++i){equal();if(t.read0==t.publish0&&!t.busy0&&!t.busy1)return;tick();}throw std::runtime_error("CPU ring failed to drain");}
static void batch(unsigned count,unsigned salt,bool overflow){
 unsigned start=t.append0;
 for(unsigned i=0;i<count;++i){unsigned a=(start+i)&4095;ref[a]=word(salt+i);wr(1,ref[a]);equal();require(t.append0==((a+1)&4095),"append pointer increment");}
 require(t.read0==start&&t.publish0==start,"unpublished words consumed");
 for(unsigned i=0;i<count;++i){unsigned a=(start+i)&4095;peek(a,ref[a]);}
 if(overflow){require(count==4095,"bad full test");unsigned end=t.append0;
  for(unsigned i=0;i<17;++i){wr(1,0xdeadbeef^i);++rejected;equal();require(t.append0==end&&t.overflow0,"full append accepted or overflow missing");}
  for(unsigned i=0;i<count;++i){unsigned a=(start+i)&4095;peek(a,ref[a]);}
 }
 wr(0,8);drain();
}
int main(int argc,char**argv){Verilated::commandArgs(argc,argv);try{
 t.reset_n=0;t.reg_wr=0;t.arready=0;t.rvalid=0;t.rlast=0;
 for(unsigned i=0;i<6;++i)tick();t.reset_n=1;for(unsigned i=0;i<6;++i)tick();
 wr(0,16);equal();require(t.mode0&&t.mode1,"CPU mode not enabled");
 batch(4095,0,true);
 for(unsigned round=0;round<16;++round)batch((round&1)?4095:37+round*11,10000+round*4096,(round&1));
 require(t.overflow0&&t.overflow1,"overflow not sticky");
 wr(0,6);for(unsigned i=0;i<8;++i)tick();equal();
 require(t.append0==0&&t.publish0==0&&t.read0==0&&!t.overflow0&&!t.mode0,"ring reset did not reset ownership");
 // Poison both inactive DMA response inputs. DMA-off CPU writes must still
 // take reg_wdata, independent of response data and ring-full comparator.
 wr(0,16);t.rdata=0xdeadbeef;batch(4095,350000,true);
 wr(0,6);for(unsigned i=0;i<8;++i)tick();wr(0,16);wr(0,32);
 require(t.mode0&&!t.mode1,"DMA-enabled mode switch changed excluded configuration");
 wr(3,0x00123400);wr(7,257);wr(11,1);
 unsigned remaining=0,offset=0;bool finished=false;
 for(unsigned i=0;i<100000;++i){
  t.arready=(i%5)!=0;t.rvalid=remaining&&i%4!=1;t.rlast=remaining==1;t.rdata=word(600000+offset);
  t.eval();bool ar=t.arvalid&&t.arready;bool beat=t.rvalid;
  unsigned len=t.arlen,addr=t.araddr;
  if(ar){require(!remaining,"overlapping DMA burst");require(addr==0x00123400+4*offset,"DMA AR address");require(len<16,"DMA burst too long");}
  tick();
  if(beat){--remaining;++offset;++dmawords;}
  if(ar)remaining=len+1;
  require(t.append0==0&&t.publish0==0&&t.read0==0,"excluded DMA accepted a descriptor");
  if(offset==257&&!remaining&&!t.dma_busy1&&t.read1==t.publish1&&!t.busy1){finished=true;break;}
 }
 require(finished&&dmawords==257,"DMA-enabled transfer failed to complete");
 for(unsigned i=0;i<257;++i){t.peek_addr=i;t.eval();require(t.peek1==word(600000+i),"DMA response data mux changed");++checked;}
 t.final();std::printf("{\"passed\":true,\"cycles\":%llu,\"words_checked\":%llu,\"overflow_rejections\":%llu,\"dma_words\":%llu}\n",(unsigned long long)cycles,(unsigned long long)checked,(unsigned long long)rejected,(unsigned long long)dmawords);return 0;
 }catch(const std::exception&e){std::fprintf(stderr,"FAIL cycle %llu: %s\n",(unsigned long long)cycles,e.what());return 1;}}
