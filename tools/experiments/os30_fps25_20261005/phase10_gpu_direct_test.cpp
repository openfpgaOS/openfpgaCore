#include "Vgeometry_direct_mmio.h"
#include "verilated.h"
#include <boost/multiprecision/cpp_bin_float.hpp>
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iostream>
#include <random>
#include <stdexcept>
#include <vector>
#include <unordered_map>
#include <array>
using Q=boost::multiprecision::number<boost::multiprecision::cpp_bin_float<100>>;
static float f(uint32_t x){float y;memcpy(&y,&x,4);return y;}
static uint32_t u(float x){uint32_t y;memcpy(&y,&x,4);return y;}
static uint32_t madd(uint32_t ai,uint32_t bi,uint32_t ci){
 Q p=Q(f(ai))*Q(f(bi));if(p!=0){int e;Q m=frexp(abs(p),&e);Q mag=ldexp(floor(ldexp(m,26)),e-26);p=p<0?-mag:mag;}
 return u(static_cast<float>(p+Q(f(ci))));
}
static uint32_t mul(uint32_t a,uint32_t b){return u(f(a)*f(b));}
static void require(bool b,const char*s){if(!b)throw std::runtime_error(s);}
static unsigned clip(const uint32_t*q){float x=f(q[0]),y=f(q[1]),z=f(q[2]),w=f(q[3]);return (x<-w?32:0)|(x>w?16:0)|(y<-w?8:0)|(y>w?4:0)|(z<-w?2:0)|(z>w?1:0);}
struct Vertex {uint32_t raw[6],expected[8],rgb_check;};
struct Job {uint32_t n,base,aspect,m[16],coeff[3],ambient,diffuse,lit,su,sv;std::vector<Vertex> vertices;unsigned dest=0;};
static void expected(Job&j,Vertex&v){
 for(unsigned r=0;r<4;r++){
  auto t=mul(v.raw[1],j.m[4+r]);t=madd(v.raw[0],j.m[r],t);t=madd(u(1),j.m[12+r],t);t=madd(v.raw[2],j.m[8+r],t);if(!r)t=mul(t,j.aspect);v.expected[r]=t;
 }
 v.expected[4]=u(1.f/f(v.expected[3]));
 int tc_u=(int16_t)(v.raw[3]>>16),tc_v=(int16_t)v.raw[4];
 v.expected[5]=(uint16_t)((tc_u*int(j.su))>>16)|(uint32_t(uint16_t((tc_v*int(j.sv))>>16))<<16);
 uint32_t normal=(v.raw[4]>>16)|((v.raw[5]&255)<<16),rgb=normal;
 if(j.lit){
  auto dot=mul(u((int8_t)(normal>>8)),j.coeff[1]);dot=madd(j.coeff[0],u((int8_t)normal),dot);dot=madd(j.coeff[2],u((int8_t)(normal>>16)),dot);dot=mul(dot,0x3c010204);rgb=0;
  for(unsigned k=0;k<3;k++){int a=j.ambient>>(8*k)&255,b=j.diffuse>>(8*k)&255;int c=f(dot)>0?(int)f(madd(u(b),dot,u(a))):a;rgb|=uint32_t(std::clamp(c,0,255))<<(8*k);}
 }
 v.expected[6]=rgb|((v.raw[5]&0xff00)<<16);v.expected[7]=clip(v.expected);v.rgb_check=1;
}
static Vgeometry_direct_mmio d;
static std::mt19937 rng(0x67646972);
static std::unordered_map<uint32_t,uint32_t> memory;
static std::vector<uint32_t> staging,published;
static bool axi_active=false,ring_owned=false,held_response=false,inject_error=false,burst_error=false;
static uint32_t axi_address=0;static unsigned axi_left=0,ring_left=0;
static uint64_t ticks=0;static unsigned bursts=0,commits=0,aborts=0;
static void tick(){
 d.clk=0;d.m_arready=!axi_active&&rng()%4!=0;
 d.m_rvalid=axi_active&&(held_response||rng()%4!=0);
 d.m_rdata=axi_active?memory.at(axi_address):0;d.m_rlast=axi_left==1;d.m_rresp=burst_error?2:0;
 d.geo_owned=ring_owned;d.geo_grant=d.geo_request&&!ring_owned&&rng()%4!=0;
 d.geo_ready=ring_owned&&rng()%4!=0;d.eval();
 bool ar=d.m_arvalid&&d.m_arready,rr=d.m_rvalid&&d.m_rready;
 uint32_t address=d.m_araddr;unsigned len=d.m_arlen+1;
 bool grant=d.geo_request&&d.geo_grant,wr=d.geo_valid&&d.geo_ready,abort=d.geo_abort;
 uint32_t w=d.geo_data;unsigned reserve=d.geo_words;
 held_response=d.m_rvalid&&!d.m_rready;
 d.clk=1;d.eval();ticks++;d.geo_done=0;
 if(ar){require(!axi_active,"AXI overlap");require(len<=16&&(address&4095)+len*4<=4096,"AXI bounds");axi_active=true;axi_address=address;axi_left=len;bursts++;burst_error=inject_error;inject_error=false;}
 if(rr){require(axi_active,"unexpected R");axi_address+=4;if(!--axi_left)axi_active=false;}
 if(grant){require(!ring_owned&&reserve>0&&reserve<=4095,"invalid ring claim");ring_owned=true;ring_left=reserve;staging.clear();}
 if(abort){require(ring_owned&&!wr,"abort ownership");staging.clear();ring_owned=false;aborts++;}
 if(wr){require(ring_owned&&ring_left,"ring overrun");staging.push_back(w);if(!--ring_left){published.insert(published.end(),staging.begin(),staging.end());staging.clear();ring_owned=false;d.geo_done=1;commits++;}}
}
static void put(unsigned byte,uint32_t data,unsigned strobe=15){d.reg_addr=byte/4;d.reg_wdata=data;d.reg_wstrb=strobe;d.reg_wr=1;tick();d.reg_wr=0;tick();}
static uint32_t get(unsigned byte){d.reg_addr=byte/4;tick();tick();d.clk=0;d.eval();return d.reg_rdata;}
static uint32_t wait_done(){for(unsigned i=0;i<30000;i++){uint32_t s=get(4);if(s&2){require(!(s&1)&&!axi_active&&!ring_owned,"completion before ownership release");return s;}}throw std::runtime_error("MMIO timeout");}
static Vertex slots[32];static uint32_t valid=0;
static void configure(Job&j){
 for(unsigned i=0;i<16;i++)put(0x100+i*4,j.m[i]);
 put(0xc,j.base);put(0x10,j.n|(j.dest<<8));put(0x14,j.aspect);
 for(unsigned i=0;i<3;i++)put(0x18+i*4,j.coeff[i]);
 put(0x24,j.ambient);put(0x28,j.diffuse);put(0x2c,j.su|(j.sv<<16));put(0x30,j.lit);
 for(unsigned i=0;i<j.n;i++)for(unsigned k=0;k<6;k++)memory[j.base+i*24+k*4]=j.vertices[i].raw[k];
}
static std::array<unsigned,2> classify(unsigned ids,unsigned mode){
 unsigned a=ids&31,b=(ids>>5)&31,c=ids>>10;
 if(!(valid&(1u<<a))||!(valid&(1u<<b))||!(valid&(1u<<c)))return {0,1};
 auto x=slots[a].expected,y=slots[b].expected,z=slots[c].expected;
 if(x[7]&y[7]&z[7])return {1,0};if(mode==3)return {2,0};
 if(mode){
  auto cx=mul(y[0],y[4]),cy=mul(y[1],y[4]);
  auto dx1=madd(x[0],x[4],cx^0x80000000u),dy1=madd(x[1],x[4],cy^0x80000000u);
  auto dx3=madd(z[0],z[4],cx^0x80000000u),dy3=madd(z[1],z[4],cy^0x80000000u);
  auto p=mul(dx1,dy3),q=mul(dy1,dx3);bool parity=(f(x[3])<0)^(f(y[3])<0)^(f(z[3])<0);
  auto cross=parity?madd(u(1),q,p^0x80000000u):madd(u(1),p,q^0x80000000u);
  if((cross&0x7fffffffu)==0||(mode==1?bool(cross>>31):!bool(cross>>31)))return {2,0};
 }
 if(x[7]|y[7]|z[7])return {0,2};
 auto good=[](uint32_t w){return w-1u<0x46fffdffu;};
 if(!good(x[3])||!good(y[3])||!good(z[3])||(x[3]==y[3]&&x[3]==z[3]))return {0,3};
 return {0,0};
}
static unsigned decode(uint32_t w){return ((w>>17)&31)|(((w>>9)&31)<<5)|(((w>>1)&31)<<10);}
static void packet(std::vector<uint32_t>&out,unsigned slot,unsigned flags,uint32_t prim,uint32_t env,unsigned tu,unsigned tv,bool linear){
 const auto*q=slots[slot].expected;unsigned rgb=flags>>8;
 uint32_t color=rgb==255?0xffffff:rgb==4?q[6]:rgb==3?prim:rgb==5?env:0;
 auto fixed=[](uint32_t bits,unsigned shift){return uint32_t(int32_t(std::nearbyint(std::ldexp(double(f(bits)),shift))));};
 int64_t U=flags&1?(int64_t(int16_t(q[5]))-int64_t(tu)*8)*2048+(linear?32768:0):0;
 int64_t V=flags&1?(int64_t(int16_t(q[5]>>16))-int64_t(tv)*8)*2048+(linear?32768:0):0;
 uint32_t depth=uint32_t(std::nearbyint(std::clamp(std::ldexp(double(f(q[4])),30),1.0,2147483520.0)));
 uint32_t c=((color&0xf8)<<8)|((color>>5)&0x7e0)|((color>>19)&31);
 for(uint32_t w:{0x56000008u,slot,fixed(q[0],8),fixed(q[1],8),fixed(q[3],8),uint32_t(U),uint32_t(V),c,depth})out.push_back(w);
}
int main(int argc,char**argv){
 Verilated::commandArgs(argc,argv);if(argc!=2)return 2;
 unsigned loaded=0,readbacks=0,runs=0,draws=0,independent_rgb=0;
 try{
  std::ifstream in(argv[1],std::ios::binary);auto read=[&](){uint32_t x;in.read((char*)&x,4);require(bool(in),"native input");return x;};
  unsigned nj=read();require(nj==20,"native count");std::vector<Job> jobs;
  for(unsigned k=0;k<nj;k++){
   Job j;j.n=read();j.base=read();j.aspect=read();for(auto&x:j.m)x=read();for(auto&x:j.coeff)x=read();j.ambient=read();j.diffuse=read();j.lit=read();j.su=read();j.sv=read();
   for(unsigned v=0;v<j.n;v++){Vertex x;for(auto&a:x.raw)a=read();for(auto&a:x.expected)a=read();x.rgb_check=read();j.vertices.push_back(x);}j.dest=k%(33-j.n);jobs.push_back(j);
  }
  for(unsigned k=0;k<100;k++){
   Job j{};j.n=k==0?32:3+rng()%30;j.dest=k%(33-j.n);j.base=0x12000fe8;j.aspect=u(1);j.m[0]=j.m[5]=j.m[10]=j.m[11]=u(1);j.m[15]=u(10);j.su=65535;j.sv=k%2?65535:32768;
   for(unsigned v=0;v<j.n;v++){Vertex x{};x.raw[0]=u(float(int(rng()%64)-32)/16);x.raw[1]=u(float(int(rng()%64)-32)/16);x.raw[2]=u(float(v)/16);x.raw[3]=(rng()&65535)<<16;x.raw[4]=rng();x.raw[5]=rng()&65535;expected(j,x);j.vertices.push_back(x);}jobs.push_back(j);
  }
  d.reset=1;d.reg_wr=d.m_rvalid=d.geo_done=0;for(unsigned i=0;i<5;i++)tick();d.reset=0;tick();
  require(get(0)==0x47445201,"GDR ID");put(8,1);require((wait_done()&0x86)==0x82,"unconfigured LOAD");
  for(unsigned ji=0;ji<jobs.size();ji++){
   auto&j=jobs[ji];configure(j);put(0xc,0,3);require(get(0xc)==j.base,"partial descriptor write");put(8,1);
   put(0x100,0);put(0xc,0);require(get(0xc)==j.base,"busy context mutation");
   require((wait_done()&0xef)==6,"native LOAD status");
   for(unsigned v=0;v<j.n;v++){slots[j.dest+v]=j.vertices[v];valid|=1u<<(j.dest+v);loaded++;if(ji<20)independent_rgb+=j.vertices[v].rgb_check;}
   for(unsigned s=0;s<32;s++)for(unsigned row=0;row<8;row++){
    uint32_t got=get(0x400+row*128+s*4),want=valid&(1u<<s)?slots[s].expected[row]:0;
    uint32_t mask=(valid&(1u<<s))&&row==6&&!slots[s].rgb_check?0xff000000:0xffffffff;
    require((got&mask)==(want&mask),"persistent raw readback mismatch");readbacks++;
   }
   for(unsigned iteration=0;iteration<4;iteration++){
    unsigned mode=iteration,budget=1+rng()%64;uint32_t address=0x13000fe8;
    std::vector<std::array<uint32_t,2>> commands(64);
    auto iw=[&]()->uint32_t{return ((j.dest+rng()%j.n)*2<<16)|((j.dest+rng()%j.n)*2<<8)|((j.dest+rng()%j.n)*2);};
    for(unsigned c=0;c<64;c++){commands[c]={(rng()%3==0?0x06000000u:0x05000000u)|iw(),iw()};if(c==63)commands[c][0]=0xdf000000;memory[address+c*8]=commands[c][0];memory[address+c*8+4]=commands[c][1];}
    unsigned accepted=0,ntotal=0,clips=0,culls=0,reason=0;std::vector<unsigned> survivors;
    for(auto c:commands){
     unsigned op=c[0]>>24,n=op==6?2:1;if(op!=5&&op!=6)break;if(ntotal+n>budget)break;
     unsigned old=survivors.size(),cl=0,cu=0;
     for(unsigned t=0;t<n;t++){unsigned ids=decode(c[t]);auto r=classify(ids,mode);if(r[1]){reason=r[1];break;}if(!r[0])survivors.push_back(ids);else if(r[0]==1)cl++;else cu++;}
     if(reason){survivors.resize(old);break;}accepted++;ntotal+=n;clips+=cl;culls+=cu;if(ntotal==budget)break;
    }
    put(0x44,address);put(0x48,(budget<<8)|mode);put(8,4);require((wait_done()&0xef)==6,"PRECHECK status");
    require(get(0x4c)==accepted&&get(0x50)==survivors.size(),"PRECHECK prefix mismatch");
    require(get(0x54)==reason,"PRECHECK reason mismatch");require(get(0x58)==(clips|(culls<<8)|(ntotal<<16)),"PRECHECK diagnostics");runs++;
    unsigned selector=ji<20?255:iteration==0?4:iteration==1?3:iteration==2?5:0;
    unsigned flags=(selector<<8)|(ji&1),tu=rng()&65535,tv=rng()&65535;bool linear=ji%3==0;
    uint32_t stale=rng(),prim=rng(),env=rng(),final=stale;std::vector<uint32_t> want;
    unsigned vertices=0;
    for(unsigned ids:survivors){unsigned a[3]={ids&31,(ids>>5)&31,ids>>10};for(unsigned slot:a)if(final&(1u<<slot)){packet(want,slot,flags,prim,env,tu,tv,linear);vertices++;}want.push_back(0x54000001);want.push_back(ids);for(unsigned slot:a)final&=~(1u<<slot);}
    if(!survivors.empty()){
     // Reserved raw slots/context must survive attempted CPU mutation.
     put(0x40,0xffffffff);put(0xc,0);require(get(0xc)==j.base,"reserved context changed");
     put(0x60,flags);put(0x64,stale);put(0x68,prim);put(0x6c,env);put(0x70,tu|(tv<<16));put(0x74,linear);
     auto old=published.size();put(8,8);require((wait_done()&0xef)==6,"COMMIT status");
     std::vector<uint32_t> got(published.begin()+old,published.end());
     if(got!=want){for(unsigned i=0;i<std::min(got.size(),want.size());i++)if(got[i]!=want[i]){std::cerr<<"word"<<i<<" got "<<std::hex<<got[i]<<" want "<<want[i]<<std::dec<<"\n";break;}throw std::runtime_error("ordered native packet mismatch");}
     require(get(0x5c)==final&&get(0x78)==want.size()&&get(0x7c)==vertices,"COMMIT accounting");
     draws+=survivors.size();
    }else put(8,16);
   }
  }
  // Invalidate is independent of persistent raw RAM contents.
  put(0x40,0xffffffff);require(get(0x400)==0,"invalidated raw exposed");
  memory[0x13000000]=0x05000204;memory[0x13000004]=0;for(unsigned i=2;i<16;i++)memory[0x13000000+i*4]=0;
  put(0x44,0x13000000);put(0x48,64<<8);put(8,4);wait_done();require(get(0x4c)==0&&get(0x54)==1,"CPU slot ownership ignored");put(8,16);
  configure(jobs[0]);inject_error=true;put(8,1);require((wait_done()&0x64)==0x40,"raw read error not drained");
  // Alternate reader owners after successful, rejected and failed transfers.
  // The shared reader must not deliver a previous RUN word to the next LOAD,
  // or let either operation complete while an AXI response is outstanding.
  configure(jobs[0]);put(8,1);require((wait_done()&0xef)==6,"LOAD recovery after DMA error");
  uint32_t recovered_x=get(0x400);
  put(0x44,0x13000000);put(0x48,(64<<8)|3);inject_error=true;put(8,4);
  require((wait_done()&0x64)==0x40,"RUN read error not drained");
  require(get(0x400)==recovered_x,"RUN failure mutated LOAD result");put(8,16);
  configure(jobs[0]);put(8,1);require((wait_done()&0xef)==6,"LOAD recovery after RUN error");
  unsigned rejected_bursts=bursts;
  put(0xc,0x14000000);put(8,1);require((wait_done()&0x6c)==0x28,"invalid LOAD descriptor");
  require(bursts==rejected_bursts,"invalid LOAD issued AXI");
  configure(jobs[0]);put(0x10,0);put(8,1);require((wait_done()&0x6c)==0x28,"empty LOAD range");
  require(bursts==rejected_bursts,"empty LOAD issued AXI");
  configure(jobs[0]);put(8,1);require((wait_done()&0xef)==6,"LOAD recovery after rejection");
  rejected_bursts=bursts;put(0x44,0x13000004);put(0x48,64<<8);put(8,4);
  require((wait_done()&0x64)==0x20,"invalid RUN descriptor");
  require(bursts==rejected_bursts,"invalid RUN issued AXI");put(8,16);
  put(0x44,0x13000000);put(0x48,1<<8|3);put(8,4);
  require((wait_done()&0xef)==6&&get(0x50)==0,"RUN recovery after rejected descriptor");put(8,16);
  require(!axi_active&&!ring_owned,"final ownership leak");
  std::cout<<"{\"passed\":true,\"load_jobs\":"<<jobs.size()<<",\"vertices\":"<<loaded<<",\"native_vertices\":230,\"independent_native_RGB\":"<<independent_rgb<<",\"raw_readbacks\":"<<readbacks<<",\"RUNs\":"<<runs<<",\"draws\":"<<draws<<",\"commits\":"<<commits<<",\"published_words\":"<<published.size()<<",\"AXI_bursts\":"<<bursts<<",\"cycles\":"<<ticks<<"}\n";
 }catch(const std::exception&e){std::cerr<<"afterLOADs="<<loaded<<" RUNs="<<runs<<": "<<e.what()<<"\n";return 1;}
}
