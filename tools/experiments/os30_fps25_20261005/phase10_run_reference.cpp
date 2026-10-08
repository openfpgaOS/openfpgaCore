// Private reusable LOAD/RUN reference. qsim IEEE FMA is intentional here;
// native os30 truncated-product FMA is separately validated in raw-load RTL.
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <vector>
static float f(uint32_t x){float y;memcpy(&y,&x,4);return y;}
static uint32_t u(float x){uint32_t y;memcpy(&y,&x,4);return y;}
static void need(bool b,const char*s){if(!b)throw std::runtime_error(s);}
struct Vertex {float x,y,z,w,inv,tu,tv;uint32_t rgba;unsigned clip;bool attributes,geometry;};
struct RawRunReference {
 uint32_t matrix[17]={},light[22]={},control[3]={};Vertex vertices[32]={};
 uint32_t frame=0,source=0,cull=0,owned=0;bool pending=false,has_draw=false;
 std::vector<uint32_t> commands;uint32_t draw[8]={};
 uint64_t loads=0,load_vertices=0,load_float_checks=0,load_rgb_checks=0,load_uv_checks=0;
 uint64_t runs=0,full_runs=0,subset_fallbacks=0,original_fallbacks=0,packet_words=0,draws=0,rejected=0;
 uint64_t gameplay_runs=0,gameplay_draws=0,gameplay_words=0;
 unsigned reasons[7]={};
 void load(const std::vector<uint32_t>&p){
  unsigned n=p[2],first=p[3];need(n&&first+n<=32&&p.size()==4+n*15,"LOAD size");
  for(unsigned v=0;v<n;v++){
   auto raw=p.data()+4+v*15;auto actual=raw+6;Vertex a={};float out[4];
   for(unsigned j=0;j<4;j++){
    float q=f(raw[1])*f(matrix[4+j]);q=fmaf(f(raw[0]),f(matrix[j]),q);q+=f(matrix[12+j]);q=fmaf(f(raw[2]),f(matrix[8+j]),q);if(!j)q*=f(matrix[16]);out[j]=q;
    need(u(q)==actual[j],"LOAD transform mismatch");load_float_checks++;
   }
   a.x=out[0];a.y=out[1];a.z=out[2];a.w=out[3];a.inv=1.f/a.w;need(u(a.inv)==actual[4],"LOAD reciprocal mismatch");load_float_checks++;
   a.clip=(a.x<-a.w?32:0)|(a.x>a.w?16:0)|(a.y<-a.w?8:0)|(a.y>a.w?4:0)|(a.z<-a.w?2:0)|(a.z>a.w?1:0);
   need(a.clip==(actual[8]&255),"LOAD clip mismatch");
   auto bytes=(const uint8_t*)raw;int16_t tu,tv;memcpy(&tu,bytes+14,2);memcpy(&tv,bytes+16,2);
   a.tu=int16_t((int(tu)*int(control[2]&65535))>>16);a.tv=int16_t((int(tv)*int(control[2]>>16))>>16);
   bool lit=control[0]&0x20000,texgen=lit&&(control[0]&0x40000);
   if(!texgen){need(u(a.tu)==actual[5]&&u(a.tv)==actual[6],"LOAD UV mismatch");load_uv_checks+=2;}
   uint32_t rgb=bytes[18]|(bytes[19]<<8)|(bytes[20]<<16);
   if(lit&&light[21]>=1&&light[21]<=3){
    auto lb=(const uint8_t*)light;unsigned amb=light[21]-1;int color[3]={lb[12*amb],lb[12*amb+1],lb[12*amb+2]};
    for(unsigned l=0;l<amb;l++){
     float dot=(int8_t)bytes[19]*f(light[10+3*l]);dot=fmaf((int8_t)bytes[18],f(light[9+3*l]),dot);dot=fmaf((int8_t)bytes[20],f(light[11+3*l]),dot);dot*=f(0x3c010204);
     if(dot>0)for(unsigned k=0;k<3;k++)color[k]=(int)fmaf(float(lb[12*l+k]),dot,float(color[k]));
    }
    rgb=0;for(unsigned k=0;k<3;k++)rgb|=uint32_t(std::clamp(color[k],0,255))<<(8*k);
   }
   need(rgb==(actual[7]&0xffffff),"LOAD RGB mismatch");load_rgb_checks++;
   a.rgba=rgb|(uint32_t(bytes[21])<<24);
   if(!(control[0]&0x10000))need(a.rgba==actual[7],"LOAD nonfog alpha mismatch");
   a.attributes=!texgen&&(!lit||light[21]==2);
   a.geometry=std::isfinite(a.x)&&std::isfinite(a.y)&&std::isfinite(a.z)&&std::isnormal(a.w)&&std::isnormal(a.inv);
   vertices[first+v]=a;
  }
  loads++;load_vertices+=n;
 }
 bool reject(unsigned i,unsigned j,unsigned k)const{
  auto&a=vertices[i];auto&b=vertices[j];auto&c=vertices[k];
  if(a.clip&b.clip&c.clip)return true;
  if(!cull)return false;
  float bx=b.x*b.inv,by=b.y*b.inv;
  float dx1=fmaf(a.x,a.inv,-bx),dy1=fmaf(a.y,a.inv,-by);
  float dx2=fmaf(c.x,c.inv,-bx),dy2=fmaf(c.y,c.inv,-by);
  float lhs=dx1*dy2,rhs=dy1*dx2,cross=lhs-rhs;
  if((a.w<0)^(b.w<0)^(c.w<0))cross=-cross;
  return cull==0x600 || (cull==0x200&&cross<=0) || (cull==0x400&&cross>=0);
 }
 uint32_t color(unsigned slot)const{
  unsigned source=draw[1]>>24;
  return source==255?0xffffff:source==4?vertices[slot].rgba:source==3?draw[3]:source==5?draw[4]:0;
 }
 unsigned alpha()const{
  unsigned source=draw[1]>>20&15;
  return source==8?255:source==4?255:source==3?draw[3]>>24:source==5?draw[4]>>24:0;
 }
 std::array<uint32_t,7> prepare(unsigned slot)const{
  auto&v=vertices[slot];bool textured=draw[1]&(2u<<8);
  float tu=textured?fmaf(v.tu-f(draw[5]),0.03125f,f(draw[7])):0.f;
  float tv=textured?fmaf(v.tv-f(draw[6]),0.03125f,f(draw[7])):0.f;
  uint32_t rgb=color(slot);unsigned r=rgb&255,g=rgb>>8&255,b=rgb>>16&255;
  float depth=std::clamp(v.inv*1073741824.f,1.f,2147483520.f);
  auto quant=[](float q){return uint32_t(int32_t(lrintf(q)));};
  return {quant(v.x*256.f),quant(v.y*256.f),quant(v.w*256.f),quant(tu*65536.f),quant(tv*65536.f),(r>>3)<<11|(g>>2)<<5|(b>>3),quant(depth)};
 }
 // Returns original CPU-format load/draw words. No prepared input is consumed.
 void finish(const std::vector<uint32_t>&reference){
  need(pending&&reference[0]==source,"END request mismatch");
  uint32_t expected_end=reference[1],expected_words=reference[2],expected_triangles=reference[3],expected_alpha=reference[4];
  need(reference.size()==5+expected_words,"reference size");
  std::vector<uint32_t> output;uint32_t stale=has_draw?draw[2]:0;unsigned accepted=0,seen=0,reason=0,live_count=0,drop_count=0;
  for(unsigned ci=0;ci<commands.size()/2;ci++){
   uint32_t w0=commands[ci*2],w1=commands[ci*2+1];unsigned op=w0>>24,n=op==5?1:op==6?2:0;if(!n||seen+n>64)break;
   unsigned slots[2][3],local_reason=0;bool drops[2]={};uint32_t w[2]={w0,w1};unsigned live=0;
   for(unsigned t=0;t<n;t++){
    for(unsigned k=0;k<3;k++){
     unsigned slot=(w[t]>>(16-8*k)&255)/2;slots[t][k]=slot;
     if(slot>=32||!(owned&(1u<<slot))||!vertices[slot].geometry)local_reason=1;
    }
    if(local_reason)break;
    unsigned a=slots[t][0],b=slots[t][1],c=slots[t][2];auto&va=vertices[a];auto&vb=vertices[b];auto&vc=vertices[c];
    if((drops[t]=reject(a,b,c)))continue;
    if(va.clip|vb.clip|vc.clip){local_reason=2;break;}
    auto good=[](float x){return u(x)-1u<0x46fffdffu;};
    if(!good(va.w)||!good(vb.w)||!good(vc.w)||(u(va.w)==u(vb.w)&&u(va.w)==u(vc.w))){local_reason=3;break;}
    if(!va.attributes||!vb.attributes||!vc.attributes){local_reason=4;break;}
    live++;
   }
   if(local_reason){reason=local_reason;break;}
   if(live){
    unsigned flags=draw[1],rgb=flags>>24,al=flags>>20&15;
    if(!has_draw||!((flags>>8)&255)||rgb==7||al==7||(al==4&&!(flags&(1u<<16)))){reason=5;break;}
   }
   for(unsigned t=0;t<n;t++){
    if(drops[t]){drop_count++;continue;}
    unsigned dirty=0;for(unsigned k=0;k<3;k++)dirty|=((stale>>slots[t][k])&1)<<k;
    for(unsigned k=0;k<3;k++)if(dirty&(1u<<k)){
     auto packet=prepare(slots[t][k]);output.push_back(0x56000008);output.push_back(slots[t][k]);output.insert(output.end(),packet.begin(),packet.end());
    }
    unsigned word=0;for(unsigned k=0;k<3;k++){word|=slots[t][k]<<(5*k);stale&=~(1u<<slots[t][k]);}
    output.push_back(0x54000001);output.push_back(word);live_count++;
   }
   seen+=n;accepted++;
  }
  bool subset=reason==1||reason==4;
  need(output.size()<=expected_words,"worker emitted more than F11 prefix");
  for(unsigned i=0;i<output.size();i++)if(output[i]!=reference[5+i]){
   std::cerr<<"frame "<<frame<<" run "<<runs<<" source "<<std::hex<<source<<" word "<<std::dec<<i<<" expected "<<std::hex<<reference[5+i]<<" actual "<<output[i]<<std::dec<<"\n";
   throw std::runtime_error("ordered packet mismatch");
  }
  if(!subset){
   uint32_t end=source+8*(accepted+(reason!=0));
   if(end!=expected_end){std::cerr<<"frame "<<frame<<" source "<<std::hex<<source<<" expectedEnd "<<expected_end<<" actualEnd "<<end<<std::dec<<" reason "<<reason<<"\n";throw std::runtime_error("fallback/consumption mismatch");}
   need(output.size()==expected_words&&live_count==expected_triangles,"complete prefix length mismatch");full_runs++;
  }else subset_fallbacks++;
  if(live_count)need(alpha()==expected_alpha,"surface alpha mismatch");
  if(reason){reasons[reason]++;if(!subset)original_fallbacks++;}
  runs++;draws+=live_count;rejected+=drop_count;packet_words+=output.size();
  if(frame>=360&&frame<=720){gameplay_runs++;gameplay_draws+=live_count;gameplay_words+=output.size();}
  pending=false;has_draw=false;
 }
};
int main(int argc,char**argv){
 if(argc!=2)return 2;std::ifstream in(argv[1],std::ios::binary);auto read=[&](){uint32_t x;in.read((char*)&x,4);if(!in)throw std::runtime_error("short input");return x;};
 RawRunReference r;try{
  unsigned events=read();for(unsigned e=0;e<events;e++){
   unsigned type=read(),n=read();std::vector<uint32_t> p(n);for(auto&v:p)v=read();
   if(type>=1&&type<=3){unsigned size=type==1?17:type==2?22:3;need(n==size,"context size");memcpy(type==1?r.matrix:type==2?r.light:r.control,p.data(),size*4);}
   else if(type==4)r.load(p);
   else if(type==5){need(!r.pending&&n>=4,"overlapping RUN");r.frame=p[0];r.source=p[1];r.cull=p[2];r.owned=p[3];r.commands.assign(p.begin()+4,p.end());r.pending=true;r.has_draw=false;}
   else if(type==6){need(r.pending&&n==8&&p[0]==r.source,"DRAW mismatch");memcpy(r.draw,p.data(),32);r.has_draw=true;}
   else if(type==7)r.finish(p);
   else throw std::runtime_error("unknown event");
  }
  need(!r.pending,"unterminated RUN");
  std::cout<<"{\"passed\":true,\"loads\":"<<r.loads<<",\"load_vertices\":"<<r.load_vertices<<",\"load_float_checks\":"<<r.load_float_checks<<",\"load_RGB_checks\":"<<r.load_rgb_checks<<",\"load_UV_checks\":"<<r.load_uv_checks<<",\"runs\":"<<r.runs<<",\"fully_compared_runs\":"<<r.full_runs<<",\"subset_fallback_runs\":"<<r.subset_fallbacks<<",\"original_fallback_runs\":"<<r.original_fallbacks<<",\"ordered_packet_words\":"<<r.packet_words<<",\"draws\":"<<r.draws<<",\"rejected_triangles\":"<<r.rejected<<",\"gameplay_runs\":"<<r.gameplay_runs<<",\"gameplay_draws\":"<<r.gameplay_draws<<",\"gameplay_packet_words\":"<<r.gameplay_words<<",\"fallback_reasons\":[";
  for(unsigned i=0;i<7;i++)std::cout<<(i?",":"")<<r.reasons[i];std::cout<<"]}\n";
 }catch(const std::exception&e){std::cerr<<e.what()<<"\n";return 1;}
}
