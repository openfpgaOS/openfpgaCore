#include "Vgeometry_raw_float_processor.h"
#include "Vgeometry_raw_float_processor___024root.h"
#include "verilated.h"
#include <boost/multiprecision/cpp_bin_float.hpp>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <deque>
#include <fstream>
#include <iostream>
#include <random>
#include <stdexcept>
#include <vector>
using Q=boost::multiprecision::number<boost::multiprecision::cpp_bin_float<100>>;
using Vertex=std::array<uint32_t,8>;
static float f(uint32_t x){float v;memcpy(&v,&x,4);return v;}
static uint32_t u(float x){uint32_t v;memcpy(&v,&x,4);return v;}
static void need(bool v,const char*s){if(!v)throw std::runtime_error(s);}
static uint32_t fm(uint32_t a,uint32_t b,uint32_t c){Q p=Q(f(a))*Q(f(b));if(p!=0){int e;Q m=frexp(abs(p),&e);p=(p<0?-1:1)*ldexp(floor(ldexp(m,26)),e-26);}return u(static_cast<float>(p+Q(f(c))));}
static uint32_t mul(uint32_t a,uint32_t b){return u(f(a)*f(b));}
static bool good(const Vertex&v){auto xy=[](uint32_t b){b&=0x7fffffff;return !b||(b>=0x3b800000&&b<=0x46fffdff);};return xy(v[0])&&xy(v[1])&&v[3]>=0x37800000&&v[3]<=0x46fffdff&&v[4]>=0x38000000&&v[4]<0x48000000&&v[7]==0;}
static bool rejected(const Vertex&a,const Vertex&b,const Vertex&c,unsigned mode){
 if(mode==0)return false;if(mode==3)return true;
 uint32_t x=mul(b[0],b[4]),y=mul(b[1],b[4]);
 uint32_t ax=fm(a[0],a[4],x^0x80000000u),ay=fm(a[1],a[4],y^0x80000000u);
 uint32_t cx=fm(c[0],c[4],x^0x80000000u),cy=fm(c[1],c[4],y^0x80000000u);
 uint32_t p=mul(ax,cy),q=mul(ay,cx),cross=fm(0x3f800000,p,q^0x80000000u);
 return (cross&0x7fffffff)==0||(mode==1?bool(cross>>31):!bool(cross>>31));
}
struct Stream {
 std::vector<uint32_t> input;std::deque<std::vector<uint32_t>> expected;
 std::array<Vertex,32> slots{};uint32_t stale=0xffffffff;unsigned flags=0xff00,prim=0,env=0,tile=0,linear=0;
 unsigned triangles=0,rejects=0,raws=0,legacy_words=0;
 void context(unsigned rgb,bool tex,uint32_t p,uint32_t e,uint32_t t,bool l){flags=(rgb<<8)|tex;prim=p;env=e;tile=t;linear=l;input.insert(input.end(),{0x61000005,flags,prim,env,tile,linear});stale=0xffffffff;}
 void raw(unsigned slot,const Vertex&v){need(good(v),"bad test raw");slots[slot]=v;input.push_back(0x60000006|(slot<<13));for(unsigned row:{0,1,3,4,5,6})input.push_back(v[row]);stale|=1u<<slot;raws++;}
 void packet(std::vector<uint32_t>&out,unsigned slot){const auto&v=slots[slot];unsigned rgb=flags>>8;uint32_t color=rgb==255?0xffffff:rgb==4?v[6]:rgb==3?prim:rgb==5?env:0;
  auto fixed=[](uint32_t b,unsigned scale){return uint32_t(int32_t(std::nearbyint(std::ldexp(double(f(b)),scale))));};
  int64_t U=flags&1?(int64_t(int16_t(v[5]))-int64_t(tile&65535)*8)*2048+(linear?32768:0):0;
  int64_t V=flags&1?(int64_t(int16_t(v[5]>>16))-int64_t(tile>>16)*8)*2048+(linear?32768:0):0;
  uint32_t depth=uint32_t(std::nearbyint(std::clamp(std::ldexp(double(f(v[4])),30),1.0,2147483520.0)));
  uint32_t c=((color&0xf8)<<8)|((color>>5)&0x7e0)|((color>>19)&31);
  for(uint32_t w:{0x56000008u,slot,fixed(v[0],8),fixed(v[1],8),fixed(v[3],8),uint32_t(U),uint32_t(V),c,depth})out.push_back(w);
 }
 void tri(unsigned a,unsigned b,unsigned c,unsigned mode){unsigned ids=a|(b<<5)|(c<<10);input.insert(input.end(),{0x62000001,ids|(mode<<15)});triangles++;std::vector<uint32_t> out;
  if(rejected(slots[a],slots[b],slots[c],mode))rejects++;
  else{for(auto s:{a,b,c})if(stale&(1u<<s))packet(out,s);out.push_back(0x54000001);out.push_back(ids);stale&=~((1u<<a)|(1u<<b)|(1u<<c));}expected.push_back(out);
 }
 void legacy(unsigned op,const std::vector<uint32_t>&words){uint32_t header=(op<<24)|words.size();input.push_back(header);expected.push_back({header});legacy_words++;
  for(auto w:words){input.push_back(w);expected.push_back({w});legacy_words++;}if(op==0x56||op==0x53||op==0x50)stale=0xffffffff;
 }
};
static Vgeometry_raw_float_processor d;static std::mt19937 rng(0x7206);static uint64_t cycles=0,stalls=0,words=0,commits=0,aborts=0;
static void reset(){d.reset=1;d.cmd_valid=0;d.result_ready=0;for(unsigned i=0;i<4;i++){d.clk=0;d.eval();d.clk=1;d.eval();cycles++;}d.reset=0;d.clk=0;d.eval();}
static void run(Stream&s,bool fault_expected=false,bool corrupt_late=false){
 unsigned pos=0;std::vector<uint32_t> staged;bool held=false;uint32_t held_word=0;bool corrupted=false,sending=false;unsigned old_aborts=aborts;
 for(unsigned n=0;n<4000000;n++){
  if(!sending&&pos<s.input.size()&&rng()%7!=0)sending=true;
  d.clk=0;d.cmd_valid=sending;d.cmd_data=pos<s.input.size()?s.input[pos]:0;d.result_ready=rng()%4!=0;d.eval();
  auto*r=d.rootp;
  need(!(r->geometry_raw_float_processor__DOT__raw_read&&r->geometry_raw_float_processor__DOT__raw_write),"raw port read/write collision");
  need(!(r->geometry_raw_float_processor__DOT__scratch_read&&r->geometry_raw_float_processor__DOT__scratch_write&&
         (r->geometry_raw_float_processor__DOT__raw_read||r->geometry_raw_float_processor__DOT__raw_write)),"third RAM port required");
  bool in=d.cmd_valid&&d.cmd_ready,out=d.result_valid&&d.result_ready,commit=d.result_commit,abort=d.result_abort;
  if(held)need(d.result_valid&&d.result_data==held_word,"unstable output under backpressure");
  held=d.result_valid&&!d.result_ready;held_word=d.result_data;if(held)stalls++;
  if(out){words++;staged.push_back(d.result_data);if(!fault_expected){need(!s.expected.empty(),"unsolicited output");need(staged.size()<=s.expected.front().size()&&staged.back()==s.expected.front()[staged.size()-1],"packet/order mismatch");}}
  if(abort){staged.clear();aborts++;need(fault_expected,"unexpected abort");}
  else if(commit){need(!s.expected.empty(),"unsolicited commit");need(staged==s.expected.front(),"early/incorrect atomic commit");s.expected.pop_front();staged.clear();commits++;}
  d.clk=1;d.eval();cycles++;if(in){pos++;sending=false;}
  if(corrupt_late&&!corrupted&&staged.size()==9){
   d.rootp->geometry_raw_float_processor__DOT__memory[3*32+1]=0x7f800000;corrupted=true;
  }
  if(d.fault){need(fault_expected&&aborts>old_aborts&&staged.empty(),"fault without rollback");for(unsigned k=0;k<8;k++){d.clk=0;d.eval();need(!d.cmd_ready&&!d.result_valid,"fault did not halt");d.clk=1;d.eval();}return;}
  if(!fault_expected&&pos==s.input.size()&&s.expected.empty()&&staged.empty()&&!d.result_valid)return;
 }
 throw std::runtime_error("processor timeout");
}
int main(int argc,char**argv){Verilated::commandArgs(argc,argv);if(argc!=2)return 2;unsigned native_good=0;try{
 std::vector<Vertex> native;std::ifstream in(argv[1],std::ios::binary);auto rd=[&](){uint32_t x;in.read((char*)&x,4);need(bool(in),"native file");return x;};need(rd()==20,"native jobs");
 for(unsigned j=0;j<20;j++){unsigned n=rd();for(unsigned k=0;k<26;k++)rd();for(unsigned v=0;v<n;v++){for(unsigned k=0;k<6;k++)rd();Vertex x;for(auto&q:x)q=rd();rd();if(good(x)){native.push_back(x);native_good++;}}}
 Stream s;s.context(255,false,0,0,0,false);
 for(unsigned base=0;base<native.size();base+=32){
  for(unsigned i=0;i<32;i++)s.raw(i,native[(base+i)%native.size()]);
  // Every guarded native vertex is used by an uncullable triangle, not merely
  // uploaded into a slot that random indices might never consume.
  for(unsigned i=0;i<32;i++)s.tri(i,(i+1)%32,(i+2)%32,0);
  for(unsigned i=0;i<1000;i++)s.tri(rng()%32,rng()%32,rng()%32,i%4);
 }
 // Independent guard extrema, adjacent-ULP cancellation and signed zeros.
 // The processor contract is broader than consistent x/w in a real scene;
 // these safely exercise the worst bounded arithmetic inputs as well.
 const uint32_t xy_edges[]={0,0x80000000,0x3b800000,0xbb800000,0x3b800001,0xbb800001,0x46fffdff,0xc6fffdff};
 const uint32_t wi_edges[]={0x38000000,0x38000001,0x3f800000,0x47ffffff};
 for(unsigned batch=0;batch<4;batch++){
  for(unsigned i=0;i<32;i++){Vertex v{};v[0]=xy_edges[i%8];v[1]=xy_edges[(i/4+batch)%8];v[3]=batch&1?0x46fffdff:0x37800000;v[4]=wi_edges[(i/8+batch)%4];v[5]=rng();v[6]=rng();s.raw(i,v);}
  for(unsigned i=0;i<512;i++)s.tri(rng()%32,rng()%32,rng()%32,i%4);
 }
 // Exhaust all low-eight-bit coordinate increments at the minimum supported
 // exponent, with permuted low inverse bits. This directly stresses the
 // 2^-48 centered-difference grid and tiny normal cross-product cancellation.
 for(unsigned k=0;k<256;k++){
  for(unsigned i=0;i<3;i++){Vertex v{};v[0]=0x3b800000|((k+i)&255);v[1]=0x3b800000|((37*k+3*i)&255);v[3]=u(32);v[4]=0x38000000|((73*k+5*i)&255);v[6]=0xffffffff;s.raw(i,v);}
  for(unsigned mode=0;mode<4;mode++)s.tri(0,1,2,mode);
  s.tri(2,1,0,1);s.tri(2,1,0,2);
 }
 const unsigned colors[]={0,1,2,3,4,5,6,255};
 for(unsigned batch=0;batch<120;batch++){
  s.context(colors[batch%8],batch&1,rng(),rng(),rng(),batch%3==0);
  for(unsigned slot=0;slot<32;slot++){Vertex v{};v[0]=u(float(int(rng()%511)-255)/16);v[1]=u(float(int(rng()%511)-255)/16);v[3]=u(32.f+float(slot)/8);v[4]=u(1.f/f(v[3]));v[5]=rng();v[6]=rng();s.raw(slot,v);}
  for(unsigned t=0;t<48;t++)s.tri(t%9==0?0:rng()%32,t%9==0?0:rng()%32,rng()%32,t%4);
  s.legacy(batch%3==0?0x56:batch%3==1?0x53:0x50,{0,0,0,0,0,0,0,0});
  s.tri(0,1,2,0);s.legacy(0x4a,std::vector<uint32_t>(127,batch));s.tri(0,1,2,0);
 }
 reset();run(s);unsigned triangles=s.triangles,rejects=s.rejects,raws=s.raws,legacy=s.legacy_words;
 for(unsigned bad=0;bad<8;bad++){reset();Stream x;x.input={bad==0?0x60000005u:bad==1?0x61000004u:bad==2?0x62000002u:0x62000001u};if(bad>=3)x.input.push_back(bad==3?0x80000000:0);run(x,true);}
 const std::array<std::pair<unsigned,uint32_t>,14> bad_fields={{{0,0x3b7fffff},{1,0xbb7fffff},{0,0x46fffe00},{1,0x7fc00000},{0,0xff800000},{2,0x377fffff},{2,0x46fffe00},{2,0xbf800000},{2,0x7f800000},{3,0x37ffffff},{3,0x48000000},{3,0x7f800000},{3,0x80000000},{6,1}}};
 for(auto [field,bits]:bad_fields){reset();Stream x;x.context(255,false,0,0,0,false);std::array<uint32_t,6> raw={u(1),u(2),u(32),u(1.f/32),0,0xffffffff};uint32_t header=0x60000006;if(field==6)header|=bits<<18;else raw[field]=bits;x.input.push_back(header);x.input.insert(x.input.end(),raw.begin(),raw.end());x.input.insert(x.input.end(),{0x62000001,0});run(x,true);}
 for(auto bad_flags:{0x700u,0x800u,0xfe00u,0x10000u,0xff02u}){reset();Stream x;x.input={0x61000005,bad_flags,0,0,0,0};run(x,true);}
 reset();Stream bad_filter;bad_filter.input={0x61000005,0xff00,0,0,0,2};run(bad_filter,true);
 reset();Stream late;late.context(255,false,0,0,0,false);Vertex v{};v[0]=u(1);v[1]=u(2);v[3]=u(32);v[4]=u(1.f/32);late.raw(0,v);v[0]=u(2);late.raw(1,v);v[0]=u(3);late.raw(2,v);late.tri(0,1,2,0);run(late,true,true);
 reset();Stream recover;recover.legacy(0x4a,{1,2,3});run(recover);
 std::cout<<"{\"passed\":true,\"native_guarded_vertices\":"<<native_good<<",\"raw_uploads\":"<<raws<<",\"triangles\":"<<triangles<<",\"rejections\":"<<rejects<<",\"legacy_words\":"<<legacy<<",\"output_words\":"<<words<<",\"commits\":"<<commits<<",\"fault_rollbacks\":"<<aborts<<",\"backpressure_cycles\":"<<stalls<<",\"cycles\":"<<cycles<<"}\n";
 }catch(const std::exception&e){std::cerr<<e.what()<<" cycles="<<cycles<<"\n";return 1;}}
