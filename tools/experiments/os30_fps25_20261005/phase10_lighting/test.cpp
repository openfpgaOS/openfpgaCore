#include "Vlighting_test_top.h"
#include "verilated.h"
#include <algorithm>
#include <boost/multiprecision/cpp_bin_float.hpp>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iostream>
#include <random>
#include <vector>
struct Vec {unsigned index;uint32_t normal,cx,cy,cz,ambient,diffuse,rgb,intensity;};
static float f(uint32_t x){float v;memcpy(&v,&x,4);return v;}
static uint32_t u(float x){uint32_t v;memcpy(&v,&x,4);return v;}
static uint32_t mac(uint32_t ai,uint32_t bi,uint32_t ci){using Q=boost::multiprecision::number<boost::multiprecision::cpp_bin_float<100>>;Q p=Q(f(ai))*Q(f(bi));if(p!=0){int e;Q m=frexp(abs(p),&e);Q mag=ldexp(floor(ldexp(m,26)),e-26);p=p<0?-mag:mag;}return u(static_cast<float>(p+Q(f(ci))));}
static uint32_t oracle(Vec v){float nx=int8_t(v.normal),ny=int8_t(v.normal>>8),nz=int8_t(v.normal>>16);uint32_t a=u(ny*f(v.cy));a=mac(v.cx,u(nx),a);a=mac(v.cz,u(nz),a);float intensity=f(a)*f(0x3c010204);uint32_t rgb=0;for(unsigned c=0;c<3;c++){int amb=v.ambient>>(8*c)&255,dif=v.diffuse>>(8*c)&255;int ch=amb;if(intensity>0)ch=int(f(mac(u(float(dif)),u(intensity),u(float(amb)))));ch=std::max(0,std::min(255,ch));rgb|=uint32_t(ch)<<(8*c);}return rgb;}
int main(int argc,char**argv){Verilated::commandArgs(argc,argv);if(argc!=2)return 2;Vlighting_test_top d;d.clk=0;d.reset=1;d.start=0;d.normal_we=0;auto tick=[&](){d.clk=0;d.eval();d.clk=1;d.eval();};for(int i=0;i<4;i++)tick();d.reset=0;
 std::ifstream in(argv[1]);std::vector<Vec> corpus;Vec v;while(in>>std::hex>>v.index>>v.normal>>v.cx>>v.cy>>v.cz>>v.ambient>>v.diffuse>>v.rgb>>v.intensity)corpus.push_back(v);if(corpus.size()!=1227)return 3;
 unsigned jobs=0,vertices=0,bad=0,unsupported=0,oracle_bad=0;unsigned cyc[33]={0};
 auto job=[&](const std::vector<Vec>&batch){unsigned n=batch.size();if(d.busy)return false;d.coefficient_x=batch[0].cx;d.coefficient_y=batch[0].cy;d.coefficient_z=batch[0].cz;d.ambient_rgb=batch[0].ambient;d.diffuse_rgb=batch[0].diffuse;
  for(unsigned i=0;i<n;i++){d.normal_we=1;d.normal_addr=i;d.normal_word=batch[i].normal;tick();}d.normal_we=0;d.count=n;d.start=1;tick();d.start=0;unsigned wait=0;while(!d.done&&++wait<1000)tick();if(!d.done||d.write_count!=3*n){std::cerr<<"timeout/write count n="<<n<<" writes="<<d.write_count<<"\n";return false;}cyc[n]=d.cycles;if(d.unsupported)unsupported++;
  for(unsigned i=0;i<n;i++){d.output_addr=i;tick();vertices++;if(d.output_rgb!=batch[i].rgb){if(bad++<16)std::cerr<<"mismatch job="<<jobs<<" n="<<n<<" idx="<<i<<" source="<<batch[i].index<<" got="<<std::hex<<d.output_rgb<<" expected="<<batch[i].rgb<<std::dec<<"\n";}}jobs++;return true;};
 for(auto& x:corpus){if(oracle(x)!=x.rgb){oracle_bad++;}if(!job({x}))return 4;}
 // Native common-context groups test independently varying vertex normals.
 for(unsigned start=0;start<203;){unsigned end=start+1;while(end<203&&end-start<32&&corpus[end].cx==corpus[start].cx&&corpus[end].cy==corpus[start].cy&&corpus[end].cz==corpus[start].cz&&corpus[end].ambient==corpus[start].ambient&&corpus[end].diffuse==corpus[start].diffuse)end++;if(!job(std::vector<Vec>(corpus.begin()+start,corpus.begin()+end)))return 5;start=end;}
 std::mt19937 rng(0x20261006);
 for(unsigned n=1;n<=32;n++)for(unsigned trial=0;trial<8;trial++){
  Vec ctx=corpus[203+rng()%1024];std::vector<Vec>b;
  for(unsigned i=0;i<n;i++){Vec x=ctx;x.normal=rng()&0xffffff;x.rgb=oracle(x);b.push_back(x);}if(!job(b))return 6;
 }
 // Invalidcount has no writes and signals unsupported, then normal job resumes.
 for(unsigned n:{0u,33u,63u}){d.count=n;d.start=1;tick();d.start=0;if(!d.done||!d.unsupported||d.busy||d.write_count)return 7;tick();}
 if(!job({corpus[0]}))return 8;
 std::cout<<"{\"jobs\":"<<jobs<<",\"vertices\":"<<vertices<<",\"native_cpu_vectors\":"<<corpus.size()<<",\"mismatches\":"<<bad<<",\"oracle_mismatches_vs_CPU\":"<<oracle_bad<<",\"unsupported_jobs\":"<<unsupported<<",\"cycles_by_count\":{";
 for(unsigned n=1;n<=32;n++)std::cout<<(n>1?",":"")<<"\""<<n<<"\":"<<cyc[n];std::cout<<"}}\n";return bad||unsupported||oracle_bad;
}
