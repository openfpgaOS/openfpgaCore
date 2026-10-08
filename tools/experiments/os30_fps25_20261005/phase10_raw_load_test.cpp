#ifdef FRONTEND_TEST
#include "Vgeometry_raw_frontend.h"
using Vgeometry_raw_load=Vgeometry_raw_frontend;
#else
#include "Vgeometry_raw_load.h"
#endif
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
int main(int argc,char**argv){
 if(argc!=2)return 2;Verilated::commandArgs(argc,argv);Vgeometry_raw_load d;std::mt19937 rng(0x72617731);
 auto edge=[&](){d.clk=0;d.eval();d.clk=1;d.eval();};
 uint32_t expected_valid=0;Vertex retained[32]={};
 auto reset=[&](){expected_valid=0;d.reset=1;d.start=d.matrix_we=d.m_arready=d.m_rvalid=0;for(int i=0;i<10;i++)edge();d.reset=0;edge();};reset();
 std::ifstream in(argv[1],std::ios::binary);auto read=[&](){uint32_t x;in.read((char*)&x,4);if(!in)throw std::runtime_error("short input");return x;};
 unsigned native_n=read();std::vector<Job> jobs;
 for(unsigned k=0;k<native_n;k++){
  Job j;j.n=read();j.base=read();j.aspect=read();for(auto&x:j.m)x=read();for(auto&x:j.coeff)x=read();j.ambient=read();j.diffuse=read();j.lit=read();j.su=read();j.sv=read();
  for(unsigned v=0;v<j.n;v++){Vertex x;for(auto&a:x.raw)a=read();for(auto&a:x.expected)a=read();x.rgb_check=read();j.vertices.push_back(x);}jobs.push_back(j);
 }
 unsigned tests=0,native_vertices=0,native_rgb=0,synthetic_vertices=0,bursts=0,errors=0,invalids=0,resets=0;uint64_t native_cycles=0,synthetic_cycles=0;

#ifdef FRONTEND_TEST
 unsigned triangles=0,invalid_triangles=0,triangle_cycles=0;
 auto triangle=[&](unsigned a,unsigned b,unsigned c,unsigned mode){
  d.start=d.matrix_we=d.m_rvalid=d.tri_out_ready=0;d.tri_valid=1;
  d.tri_indices=a|(b<<5)|(c<<10);d.tri_cull_mode=mode;d.clk=0;d.eval();
  require(d.tri_ready,"triangle not ready");edge();d.tri_valid=0;
  bool valid=(expected_valid&(1u<<a))&&(expected_valid&(1u<<b))&&(expected_valid&(1u<<c));
  unsigned reject=0,clip_or=0;uint32_t cross=0;bool fallback=!valid;
  if(valid){
   auto x=retained[a].expected,y=retained[b].expected,z=retained[c].expected;
   clip_or=x[7]|y[7]|z[7];
   if(x[7]&y[7]&z[7])reject=1;
   else if(mode==3)reject=2;
   else if(mode){
    auto cx=mul(y[0],y[4]),cy=mul(y[1],y[4]);
    auto dx1=madd(x[0],x[4],cx^0x80000000u),dy1=madd(x[1],x[4],cy^0x80000000u);
    auto dx3=madd(z[0],z[4],cx^0x80000000u),dy3=madd(z[1],z[4],cy^0x80000000u);
    auto p=mul(dx1,dy3),q=mul(dy1,dx3);
    bool parity=(f(x[3])<0)^(f(y[3])<0)^(f(z[3])<0);
    cross=parity?madd(u(1),q,p^0x80000000u):madd(u(1),p,q^0x80000000u);
    reject=((cross&0x7fffffffu)==0||(mode==1?bool(cross>>31):!bool(cross>>31)))?2:0;
   }
  }
  if(valid){
   auto x=retained[a].expected,y=retained[b].expected,z=retained[c].expected;
   auto good=[](uint32_t w){return w-1u<0x46fffdffu;};
   fallback=reject==0&&(clip_or||!good(x[3])||!good(y[3])||!good(z[3])||(x[3]==y[3]&&x[3]==z[3]));
  }
  unsigned elapsed=0;
  while(!d.tri_out_valid&&elapsed<200){
   d.start=1;d.matrix_we=1;d.matrix_data=rng();d.tri_indices=rng();d.tri_cull_mode=rng()%4;
   edge();require(!d.m_arvalid,"LOAD accepted while triangle active");elapsed++;
  }
  require(d.tri_out_valid,"triangle timeout");
  d.start=d.matrix_we=0;
  require(bool(d.tri_unsupported)==!valid,"triangle validity mismatch");
  require(bool(d.tri_fallback)==fallback,"triangle fallback mismatch");
  if(valid){
   if(d.tri_cross_bits!=cross||d.tri_clip_or!=clip_or||d.tri_reject_class!=reject){
    std::cerr<<"triangle "<<triangles<<" slots "<<a<<","<<b<<","<<c<<" mode "<<mode<<" cross actual/expected "<<std::hex<<d.tri_cross_bits<<"/"<<cross<<" clips "<<unsigned(d.tri_clip_or)<<"/"<<clip_or<<" reject "<<unsigned(d.tri_reject_class)<<"/"<<reject<<std::dec<<"\n";
    throw std::runtime_error("integrated cull mismatch");
   }
  }else invalid_triangles++;
  const auto saved_cross=d.tri_cross_bits;const unsigned saved_meta=d.tri_unsupported|(d.tri_clip_or<<1)|(d.tri_reject_class<<7);
  for(unsigned k=0;k<5;k++){
   d.tri_valid=1;d.start=1;edge();require(d.tri_out_valid&&!d.tri_ready&&!d.m_arvalid,"blocked output was not held");
   require(d.tri_cross_bits==saved_cross&&(d.tri_unsupported|(d.tri_clip_or<<1)|(d.tri_reject_class<<7))==saved_meta,"triangle output changed under stall");
  }
  d.start=d.tri_valid=0;d.tri_out_ready=1;edge();d.tri_out_ready=0;
  require(!d.busy&&!d.tri_out_valid,"triangle did not retire");triangles++;triangle_cycles+=elapsed;
 };
 triangle(0,1,2,1);
#endif
 auto run=[&](const Job&j,bool native,int error_burst,int error_beat,bool abort_reset){
  require(!d.busy,"not idle before job");
  for(unsigned i=0;i<16;i++){d.matrix_we=1;d.matrix_addr=i;d.matrix_data=j.m[i];edge();}d.matrix_we=0;
  d.job_base=j.base;d.count=j.n;d.dest=j.dest;d.aspect=j.aspect;d.coefficient_x=j.coeff[0];d.coefficient_y=j.coeff[1];d.coefficient_z=j.coeff[2];d.ambient_rgb=j.ambient;d.diffuse_rgb=j.diffuse;d.lighting_enabled=j.lit;d.scale_u=j.su;d.scale_v=j.sv;
  const bool valid_range=j.n>=1&&j.n<=32&&j.dest+j.n<=32;
  const uint32_t write_mask=valid_range?uint32_t(((uint64_t(1)<<j.n)-1)<<j.dest):0;
  expected_valid&=~write_mask;
  d.start=1;d.m_arready=d.m_rvalid=0;edge();d.start=0;
  bool active=false,rv=false,ar_stall=false,fault_used=false,finished=false;uint32_t address=0,next_address=j.base,stalled_ar=0;unsigned beats=0,beat=0,job_bursts=0,stalled_len=0;
  const uint64_t end=uint64_t(j.base)+j.n*24;const bool invalid=!valid_range||(j.base&3)||j.base<0x10000000u||end>0x14000000u;
  for(unsigned cycle=0;cycle<12000;cycle++){
   d.clk=0;d.start=(rng()%11)==0;d.job_base=rng();d.count=rng()%64;d.dest=rng()%32;
#ifndef HELD_CONTEXT_TEST
   d.aspect=rng();d.coefficient_x=rng();d.coefficient_y=rng();d.coefficient_z=rng();d.ambient_rgb=rng();d.diffuse_rgb=rng();d.lighting_enabled=rng()&1;d.scale_u=rng();d.scale_v=rng();
#endif
   d.matrix_we=(rng()%7)==0;d.matrix_addr=rng()%16;d.matrix_data=rng();d.output_addr=rng()%256;
   d.m_arready=rng()%4!=0;
   if(!rv&&active&&rng()%4!=0){
    rv=true;unsigned index=(address-j.base)/4+beat;require(index<j.n*6,"read outside raw input");d.m_rdata=j.vertices[index/6].raw[index%6];d.m_rlast=beat+1==beats;d.m_rresp=(int(job_bursts)==error_burst&&int(beat)==error_beat)?2:0;
   }
   d.m_rvalid=rv;d.eval();
   if(ar_stall)require(d.m_arvalid&&d.m_araddr==stalled_ar&&d.m_arlen==stalled_len,"AR not stable under stall");
   ar_stall=d.m_arvalid&&!d.m_arready;stalled_ar=d.m_araddr;stalled_len=d.m_arlen;
   bool ar=d.m_arvalid&&d.m_arready,rr=d.m_rvalid&&d.m_rready;
   if(ar){require(!invalid&&!active&&!fault_used,"unexpected request");address=d.m_araddr;beats=d.m_arlen+1;require(address==next_address,"job base changed while active");require(beats&&beats<=16&&(address&4095)+beats*4<=4096&&uint64_t(address)+beats*4<=end,"invalid burst bounds");next_address+=beats*4;beat=0;active=true;job_bursts++;bursts++;}
   if(rr){require(active,"unexpected response");if(d.m_rresp)fault_used=true;beat++;rv=false;if(d.m_rlast)active=false;}
   if(abort_reset&&cycle==9){reset();resets++;finished=true;break;}
   edge();
   if(d.done){
    require(!d.busy&&!active&&!rv,"done before drain or still busy");finished=true;
    if(invalid){require(d.unsupported&&d.dma_error==1&&!d.all_inside,"invalid job did not fail");invalids++;}
    else if(fault_used){require(d.unsupported&&d.dma_error==2&&!d.all_inside,"error job did not fail");errors++;}
    else{
     require(!d.unsupported&&!d.dma_error,"unexpected unsupported job");require(next_address==end,"source not fully read");
     bool inside=true;for(auto&v:j.vertices)inside&=v.expected[7]==0;require(d.all_inside==inside,"all_inside mismatch");
     for(unsigned v=0;v<j.n;v++)retained[j.dest+v]=j.vertices[v];
     expected_valid|=write_mask;
     if(native)native_cycles+=d.cycles;else synthetic_cycles+=d.cycles;
     d.start=d.matrix_we=d.m_rvalid=0;
     for(unsigned v=0;v<32;v++)if(expected_valid&(1u<<v))for(unsigned r=0;r<8;r++){
      d.output_addr=r*32+v;edge();uint32_t actual=d.output_data,expect=retained[v].expected[r];
      uint32_t mask=(r==6&&!retained[v].rgb_check)?0xff000000u:0xffffffffu;
      if((actual&mask)!=(expect&mask)){std::cerr<<"test "<<tests<<" native "<<native<<" vertex "<<v<<" row "<<r<<" expected "<<std::hex<<expect<<" actual "<<actual<<std::dec<<"\n";throw std::runtime_error("result mismatch");}
     }
     
#ifdef FRONTEND_TEST
     for(unsigned mode=0;mode<4;mode++)for(unsigned trial=0;trial<4;trial++){
      unsigned a=(j.dest+rng()%j.n),b=(j.dest+rng()%j.n),c=trial==0?a:(rng()%32);
      triangle(a,b,c,mode);
     }
#endif
     if(native){native_vertices+=j.n;for(auto&v:j.vertices)native_rgb+=v.rgb_check;}else synthetic_vertices+=j.n;
    }require(d.valid_slots==expected_valid,"persistent validity mismatch");break;
   }
  }
  require(finished,"timeout");d.start=d.matrix_we=d.m_rvalid=0;edge();tests++;
 };
 try{
  for(auto&j:jobs)run(j,true,0,0,false);
  for(unsigned n=1;n<=32;n++)for(unsigned pattern=0;pattern<4;pattern++){
   Job j={};j.n=n;j.base=0x10000000u+(pattern==0?0:pattern==1?4092:pattern==2?4088:4);j.aspect=u(1.f+float(pattern)/8);j.lit=pattern&1;j.ambient=rng()&0xffffff;j.diffuse=rng()&0xffffff;j.su=(pattern==0?0:pattern==1?65535:pattern==2?32768:rng()&65535);j.sv=65535-j.su;
   for(unsigned r=0;r<4;r++)for(unsigned c=0;c<4;c++)j.m[r*4+c]=u(c==3?(r==3?1.f:0.f):float(int(rng()%129)-64)/16.f);
   for(auto&c:j.coeff)c=u(float(int(rng()%257)-128)/128.f);
   for(unsigned v=0;v<n;v++){Vertex x={};for(unsigned k=0;k<3;k++)x.raw[k]=u(float(int(rng()%1025)-512)/16.f);x.raw[3]=rng();x.raw[4]=rng();x.raw[5]=rng();expected(j,x);j.vertices.push_back(x);}
   j.dest=(32-n)?rng()%(33-n):0;
   run(j,false,0,0,false);
  }
  for(unsigned dest=0;dest<32;dest++){Job j=jobs[0];j.n=1;j.vertices.resize(1);j.dest=dest;run(j,false,0,0,false);}
  for(unsigned dest:{1u,16u,31u}){Job j=jobs[0];j.n=32;j.dest=dest;run(j,false,0,0,false);}
  Job j=jobs.back();j.base=0x10000100;for(int beat:{0,3,15})run(j,false,1,beat,false);
  j=jobs[1];j.base=0x10000ffc;run(j,false,1,0,false);run(j,false,2,5,false);
  for(uint32_t base:{0u,0x0ffffffcu,0x10000001u,0x13fffffcu,0x14000000u,0xfffffffcu}){j.base=base;run(j,false,0,0,false);}
  j.base=0x10000000;for(unsigned n:{0u,33u,63u}){j.n=n;run(j,false,0,0,false);}
  j=jobs[1];j.base=0x10000ffc;run(j,false,0,0,true);run(j,false,0,0,false);

#ifdef FRONTEND_TEST
  std::cout<<"{\"integrated_cull_passed\":true,\"triangles\":"<<triangles<<",\"invalid_triangles\":"<<invalid_triangles<<",\"triangle_cycles\":"<<triangle_cycles<<"}\n";
#endif
  std::cout<<"{\"passed\":true,\"tests\":"<<tests<<",\"native_jobs\":"<<native_n<<",\"native_vertices\":"<<native_vertices<<",\"native_independent_RGB\":"<<native_rgb<<",\"synthetic_vertices\":"<<synthetic_vertices<<",\"bursts\":"<<bursts<<",\"response_errors_drained\":"<<errors<<",\"invalid_jobs\":"<<invalids<<",\"fabric_resets\":"<<resets<<",\"native_cycles_with_random_AXI_stalls\":"<<native_cycles<<",\"synthetic_cycles\":"<<synthetic_cycles<<"}\n";
 }catch(const std::exception&e){std::cerr<<"test "<<tests<<": "<<e.what()<<"\n";return 1;}
}
