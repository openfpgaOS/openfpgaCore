#include "Vtb_metadata.h"
#include <verilated.h>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <random>
#include <stdexcept>
#include <vector>
static float number(uint32_t w){float f;std::memcpy(&f,&w,4);return f;}
static uint32_t bits(float f){uint32_t w;std::memcpy(&w,&f,4);return w;}
static unsigned hash_w(uint32_t w){unsigned h=0;for(unsigned i=0;i<8;i++)h^=(w>>(i*4))&15;return h;}
struct Vertex{uint32_t x,y,z,w;};
struct Test{
 Vtb_metadata d;uint64_t checks=0,cycles=0;unsigned batches=0,triangles=0,accepted=0,collisions=0;
 void check(bool b,const char*m){++checks;if(!b)throw std::runtime_error(std::string(m)+" cycle="+std::to_string(cycles));}
 void tick(){d.clk=0;d.eval();d.clk=1;d.eval();d.clk=0;d.eval();++cycles;}
 void reset(){d.start=0;d.dma_done=0;d.compute_bad=0;d.word_we=0;d.reset=1;tick();d.reset=0;tick();check(!d.importable_slots&&!d.w_fingerprint0&&!d.w_fingerprint1&&!d.w_fingerprint2&&!d.w_fingerprint3,"reset metadata");}
 void word(unsigned a,uint32_t w){d.word_we=1;d.word_addr=a;d.word_data=w;tick();d.word_we=0;}
 unsigned clip(const Vertex&v){float w=number(v.w);unsigned c=0;float p[3]={number(v.x),number(v.y),number(v.z)};for(float x:p)c=(c<<2)|unsigned(x>w)|(unsigned(x< -w)<<1);return c;}
 unsigned hw_hash(unsigned slot){return ((d.w_fingerprint0>>slot)&1)|(((d.w_fingerprint1>>slot)&1)<<1)|(((d.w_fingerprint2>>slot)&1)<<2)|(((d.w_fingerprint3>>slot)&1)<<3);}
 void run(const std::vector<Vertex>&v,unsigned dest=0){
  check(!d.busy,"previous LOAD drained");for(unsigned j=0;j<v.size();j++){word(dest+j,v[j].x);word(32+dest+j,v[j].y);word(64+dest+j,v[j].z);word(96+dest+j,v[j].w);}
  d.count=v.size();d.dest=dest;d.start=1;tick();d.start=0;
  check(d.busy&&!d.importable_slots&&!d.w_fingerprint0&&!d.w_fingerprint1&&!d.w_fingerprint2&&!d.w_fingerprint3,"accepted LOAD clears old metadata");
  d.dma_done=1;tick();d.dma_done=0;for(unsigned n=0;!d.done;n++){check(n<300,"SCAN timeout");tick();}
  check(!d.busy&&!d.unsupported,"successful stub transform/real SCAN");uint32_t expected=0;bool inside=true;
  for(unsigned j=0;j<v.size();j++){
   const auto&a=v[j];unsigned c=clip(a);inside&=c==0;
   bool safe=std::isnormal(number(a.w))&&number(a.w)>0&&number(a.w)<32767.0f&&c==0;
   if(safe)expected|=1u<<(dest+j);
   check(hw_hash(dest+j)==hash_w(a.w),"exact XOR-nibble bitplanes");
   d.output_addr=224+dest+j;tick();tick();check(d.output_data==c,"real clip row vs independent float comparisons");
  }
  check(d.importable_slots==expected,"importable slots including dest/high bit");check(bool(d.all_inside)==inside,"all_inside independent of importability");
  uint32_t occupied=uint32_t(0xffffffffu>>(32-v.size()))<<dest;
  check(!((d.w_fingerprint0|d.w_fingerprint1|d.w_fingerprint2|d.w_fingerprint3)&~occupied),"old fingerprints cleared outside new LOAD");
  // The admission property is checked against actual RTL-produced metadata,
  // including repeated indices and deliberately colliding unequal values.
  for(unsigned a=0;a<v.size();a++)for(unsigned b=0;b<v.size();b++)for(unsigned c=0;c<v.size();c++){
   ++triangles;uint32_t used=(1u<<(dest+a))|(1u<<(dest+b))|(1u<<(dest+c));
   bool diff=hw_hash(dest+a)!=hw_hash(dest+b)||hw_hash(dest+a)!=hw_hash(dest+c);
   bool take=(expected&used)==used&&diff;
   if(take){++accepted;check(!(v[a].w==v[b].w&&v[a].w==v[c].w),"fingerprint falsely admitted all-equal W");check(clip(v[a])==0&&clip(v[b])==0&&clip(v[c])==0,"admitted clipped triangle");}
   else if((expected&used)==used&&!diff&&!(v[a].w==v[b].w&&v[a].w==v[c].w))++collisions;
  }
  ++batches;
 }
};
int main(int argc,char**argv)try{
 Verilated::commandArgs(argc,argv);Test t;t.reset();
 // Zeros, subnormal boundaries, normal range, exact upper bound, signed W,
 // finite extreme and infinity. NaN input is excluded by successful LOAD.
 const uint32_t wb[]={0,0x80000000,1,0x007fffff,0x00800000,0x00800001,0x3f000000,0x3f800000,0x3f800011,0x3f800022,0x40000000,0x46fffdff,0x46fffe00,0x46fffe01,0x7f7fffff,0x7f800000,0xbf800000,0xff800000};
 for(uint32_t w:wb){std::vector<Vertex>v={{0,0,0,w},{0,0,0,0x3f800000},{0,0,0,0x40000000}};t.run(v,29);}
 // Exact clip edges are inside; one representable step beyond each edge is not.
 for(unsigned axis=0;axis<3;axis++)for(unsigned sign=0;sign<2;sign++)for(unsigned step=0;step<3;step++){
  Vertex a{0,0,0,0x3f800000};uint32_t x=0x3f800000+(step==0?-1:step==1?0:1);if(sign)x|=0x80000000;(&a.x)[axis]=x;t.run({a,{0,0,0,0x40000000},{0,0,0,0x40400000}});
 }
 std::mt19937 rng(0x51474631);for(unsigned batch=0;batch<256;batch++){
  unsigned n=1+rng()%32,dest=rng()%(33-n);std::vector<Vertex>v;
  for(unsigned i=0;i<n;i++){
   uint32_t w=0x00800000+rng()%(0x46fffe10-0x00800000);float f=number(w);
   auto coordinate=[&](){unsigned kind=rng()%8;return kind<2?uint32_t(0):bits(f*(float(int(rng()%257)-128)/64.0f));};
   v.push_back({coordinate(),coordinate(),coordinate(),w});
  }t.run(v,dest);
 }
 t.check(t.accepted>0&&t.collisions>0,"admission and conservative collision paths exercised");
 // DMA/transform failure never enters SCAN or exposes valid admission bits.
 for(unsigned mode=0;mode<2;mode++){
  t.d.count=3;t.d.dest=0;t.d.start=1;t.tick();t.d.start=0;t.d.compute_bad=mode;t.tick();t.d.dma_error=mode?0:2;t.d.dma_done=1;t.tick();t.d.dma_done=0;
  for(unsigned n=0;!t.d.done;n++){t.check(n<30,"failed LOAD drains");t.tick();}
  t.check(t.d.unsupported&&!t.d.importable_slots&&!t.d.w_fingerprint0&&!t.d.w_fingerprint1&&!t.d.w_fingerprint2&&!t.d.w_fingerprint3,"failed LOAD cannot admit");t.d.dma_error=0;t.d.compute_bad=0;
 }
 t.reset();std::cout<<"{\"pass\":true,\"batches\":"<<t.batches<<",\"checks\":"<<t.checks<<",\"triangle_admissions_checked\":"<<t.triangles<<",\"accepted\":"<<t.accepted<<",\"conservative_hash_collisions\":"<<t.collisions<<",\"cycles\":"<<t.cycles<<"}\n";return 0;
}catch(const std::exception&e){std::cerr<<"FAIL "<<e.what()<<"\n";return 1;}
