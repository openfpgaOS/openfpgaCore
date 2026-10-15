#include "Vgeometry_mesh_packet.h"
#include "verilated.h"
#include <array>
#include <vector>
#include <random>
#include <cmath>
#include <cstring>
#include <cfenv>
#include <stdexcept>
#include <iostream>
static uint32_t bits(float f){uint32_t n;std::memcpy(&n,&f,4);return n;}
static float number(uint32_t n){float f;std::memcpy(&f,&n,4);return f;}
static void require(bool ok,const char *what){if(!ok)throw std::runtime_error(what);}
struct Sim {
 Vgeometry_mesh_packet t;
 std::array<uint32_t,256> ram{};
 std::array<uint16_t,64> list{};
 std::vector<uint32_t> output;
 uint64_t cycles=0,stalls=0;
 std::mt19937 rng{728191};
 bool stalled=false;uint32_t held=0;
 void tick(){
  t.clk=0;t.eval();
  auto next_view=ram[t.view_addr];auto next_list=list[t.list_addr];
  if(!t.reset){
   if(stalled)require(t.word_valid&&t.word_data==held,"packet changed while stalled");
   stalled=t.word_valid&&!t.word_ready;held=t.word_data;
   if(stalled)++stalls;
   if(t.word_valid&&t.word_ready)output.push_back(t.word_data);
  }else stalled=false;
  t.clk=1;t.eval();t.view_data=next_view;t.list_indices=next_list;t.eval();++cycles;
 }
 Sim(){t.reset=1;tick();tick();t.reset=0;tick();}
 void run(uint32_t mask,unsigned n,uint32_t flags,uint32_t uo,uint32_t vo,bool error){
  require(!t.busy,"start busy");output.clear();
  t.used_mask=mask;t.live_count=n;t.flags_rgb=flags;t.u_offset=uo;t.v_offset=vo;t.start=1;
  t.word_ready=0;tick();t.start=0;
  // Producer context is deliberately overwritten while the task owns its copy.
  t.used_mask=~mask;t.live_count=127;t.flags_rgb=~flags;t.u_offset=~uo;t.v_offset=~vo;
  unsigned budget=50000;
  while(!t.done&&budget--){t.word_ready=(rng()%7)<4;tick();}
  require(t.done,"timeout");require(bool(t.error)==error,"wrong completion status");
  require(!t.busy,"busy after done");
  if(error)require(output.empty(),"fallback published partial commands");
  tick();
 }
};
static uint32_t fixed(uint32_t b,bool depth){
 double v=number(b);
 if(depth){v=std::max(std::ldexp(1.,-30),std::min(v,double(number(0x3fffffff))));}
 return uint32_t(int64_t(std::nearbyint(std::ldexp(v,depth?30:8))));
}
static std::vector<uint32_t> reference(const Sim&s,uint32_t mask,unsigned count,uint32_t f,uint32_t uo,uint32_t vo){
 std::vector<uint32_t> out;
 for(unsigned i=0;i<32;i++)if(mask&(1u<<i)){
  auto uv=s.ram[5*32+i],color=s.ram[6*32+i];
  auto u=int64_t(int16_t(uv))-uo+((f&(1u<<17))?16:0);
  auto v=int64_t(int16_t(uv>>16))-vo+((f&(1u<<17))?16:0);
  uint32_t rgb=(f&(1u<<18))?(((color&255)>>3)<<11)|((((color>>8)&255)>>2)<<5)|(((color>>16)&255)>>3):(f&65535);
  std::vector<uint32_t> p={0x56000008,i,fixed(s.ram[i],false),fixed(s.ram[32+i],false),fixed(s.ram[96+i],false),
   (f&(1u<<16))?uint32_t(u*2048):0,(f&(1u<<16))?uint32_t(v*2048):0,rgb,fixed(s.ram[128+i],true)};
  out.insert(out.end(),p.begin(),p.end());
 }
 for(unsigned j=0;j<count;j++){out.push_back(0x54000001);out.push_back(s.list[j]);}
 return out;
}
int main(){try{
 std::fesetround(FE_TONEAREST);Sim s;unsigned cases=0,words=0;
 for(unsigned round=0;round<1200;round++){
  unsigned n=1+s.rng()%64;uint32_t mask=0;
  for(unsigned j=0;j<n;j++){
   unsigned a=s.rng()%32,b=s.rng()%32,c=s.rng()%32;
   s.list[j]=a|(b<<5)|(c<<10);mask|=(1u<<a)|(1u<<b)|(1u<<c);
  }
  for(unsigned i=0;i<32;i++){
   // Binary-exact fractions include halfway cases for signed RNE.
   s.ram[i]=bits(float(int(s.rng()%2000001)-1000000)/512);
   s.ram[32+i]=bits(float(int(s.rng()%2000001)-1000000)/512);
   s.ram[64+i]=s.rng(); // Z is unused in this packet format.
   float w=float(1+s.rng()%32000)/2;
   s.ram[96+i]=bits(w);s.ram[128+i]=bits(1.f/w);
   s.ram[160+i]=s.rng();s.ram[192+i]=s.rng();s.ram[224+i]=0;
  }
  uint32_t f=s.rng()&0x7ffff,uo=(s.rng()%65536)*8,vo=(s.rng()%65536)*8;
  auto expected=reference(s,mask,n,f,uo,vo);s.run(mask,n,f,uo,vo,false);
  require(s.output==expected,"CPU numeric oracle mismatch");words+=s.output.size();++cases;
 }
 // Failure at the LAST vertex must not publish earlier valid vertices.
 s.list[0]=0|(1<<5)|(31<<10);uint32_t mask=0x80000003;
 const std::vector<std::pair<unsigned,uint32_t>> bad={
  {0,0x7fc00001},{0,0x7f800000},{0,1},{0,0x4b800000},
  {1,0xff800000},{1,0x80000001},{3,0},{3,0xbf800000},{3,0x46fffe00},
  {4,0},{4,0x80000000},{4,0xbf800000},{4,1},{4,0x7f800000},{7,1}};
 for(auto [row,val]:bad){auto old=s.ram[row*32+31];s.ram[row*32+31]=val;s.run(mask,1,0,0,0,true);s.ram[row*32+31]=old;++cases;}
 for(auto [f,uo,vo]:std::vector<std::array<uint32_t,3>>{{0x80000,0,0},{0x10000,1,0},{0x10000,0,7},{0x10000,524288,0},{0x10000,0,524288}}){s.run(mask,1,f,uo,vo,true);++cases;}
 s.run(mask,0,0,0,0,true);s.run(0,1,0,0,0,true);s.run(mask,65,0,0,0,true);cases+=3;
 s.run(mask&~2u,1,0,0,0,true);s.run(mask|4u,1,0,0,0,true);cases+=2;
 s.run(0,0,0,0,0,false);require(s.output.empty(),"empty task emitted words");++cases;
 // Untextured mode ignores arbitrary UV offsets by contract.
 auto expected=reference(s,mask,1,0x4567,0xffffffff,0x80000000);
 s.run(mask,1,0x4567,0xffffffff,0x80000000,false);require(s.output==expected,"untextured UV contract");++cases;
 std::cout<<"{\"status\":\"PASS\",\"cases\":"<<cases<<",\"words_checked\":"<<words<<",\"stalled_cycles\":"<<s.stalls<<",\"cycles\":"<<s.cycles<<"}\n";
 }catch(const std::exception&e){std::cerr<<e.what()<<"\n";return 1;}}
