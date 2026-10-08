#ifndef LOAD_OUTPUT_STRIDE
#define LOAD_OUTPUT_STRIDE 36
#endif
// Private coupled RTL observer/driver. This exercises real DMA, not a CPU emulator.
#include <array>
#include <fstream>
#include <cmath>
static uint8_t mem_rd8(uint32_t a);
static void mem_wr8(uint32_t a,uint8_t v);
static void load_need(bool b,const char*s){if(!b)throw std::runtime_error(s);}
static uint32_t load_bits(float f){uint32_t u;memcpy(&u,&f,4);return u;}
struct LoadVertex {std::array<uint32_t,6> raw;std::array<uint32_t,8> expected;uint32_t rgb;};
struct LoadJob {std::array<uint32_t,27> h;std::vector<LoadVertex> vertices;};
static std::vector<LoadJob> load_jobs;
static bool load_active=false;
static unsigned load_mode=0,load_job=0,load_config=0,load_state=0;
static uint64_t load_start_at=150000,load_epoch=0,load_config_at=0,load_started=0,load_busy=0;
static uint64_t load_words=0,load_vertices=0,load_rgb=0;
static std::vector<std::pair<uint16_t,uint32_t>> load_regs;
static constexpr uint32_t LOAD_SRC=0x13e00000,LOAD_OUT=0x13f00000;
struct LoadChannel {
 bool pending=false,inflight=false;uint64_t issued=0,accepted=0;unsigned expected=0,beats=0;
 Latencies queue,service,total;
 void request(bool valid,bool ready,unsigned len){
  if(valid&&!pending){pending=true;issued=cycles;}
  if(valid&&ready){load_need(pending&&!inflight,"LOAD request overlap");queue.values.push_back(cycles-issued);accepted=cycles;expected=len+1;beats=0;pending=false;inflight=true;}
 }
 void done(){load_need(inflight,"LOAD response without request");service.values.push_back(cycles-accepted);total.values.push_back(cycles-issued);inflight=false;}
 void print(const char*n){queue.print(n,"queue");service.print(n,"service");total.print(n,"total");}
};
static LoadChannel load_rd,load_wr;
static Latencies load_latencies,load_withconfig;
static uint32_t load_read_word(uint32_t a){uint32_t r=0;for(unsigned b=0;b<4;b++)r|=uint32_t(mem_rd8(a+b))<<(8*b);return r;}
static void load_options(int argc,char**argv){
 for(int a=1;a<argc;a++){
  if(!strncmp(argv[a],"+load_mode=",11))load_mode=std::strtoul(argv[a]+11,nullptr,0);
  if(!strncmp(argv[a],"+load_start=",12))load_start_at=std::strtoull(argv[a]+12,nullptr,0);
 }
 std::ifstream in(LOAD_NATIVE_PATH,std::ios::binary);auto get=[&](){uint32_t v;in.read((char*)&v,4);load_need(bool(in),"short native LOAD input");return v;};
 unsigned n=get();for(unsigned j=0;j<n;j++){LoadJob job;for(auto&h:job.h)h=get();for(unsigned v=0;v<job.h[0];v++){LoadVertex x;for(auto&q:x.raw)q=get();for(auto&q:x.expected)q=get();x.rgb=get();job.vertices.push_back(x);}load_jobs.push_back(job);}
 load_need(in.peek()==std::ifstream::traits_type::eof(),"extra native LOAD input");
}
static void load_begin(){
 load_epoch=cycles;load_job=load_config=load_state=0;load_active=load_mode!=0;
 for(unsigned j=0;j<load_jobs.size();j++)for(unsigned v=0;v<load_jobs[j].vertices.size();v++)for(unsigned w=0;w<6;w++)for(unsigned b=0;b<4;b++)mem_wr8(LOAD_SRC+j*1024+v*24+w*4+b,load_jobs[j].vertices[v].raw[w]>>(8*b));
}
static bool load_owned(uint32_t a){return (a>=0x03e00000 && a<0x03e00000+load_jobs.size()*1024) || (a>=0x03f00000 && a<0x03f00000+load_jobs.size()*2048);}
static void load_check_range(uint32_t address,uint32_t n){
 uint32_t a=address&GV_SDRAM_MASK;
 load_need(uint64_t(a)+n<=0x03e00000 || a>=0x03f10000,"GPU vector conflicts with private LOAD storage");
}
static void load_make_config(){
 const auto&h=load_jobs[load_job].h;load_regs.clear();
 for(unsigned i=0;i<16;i++)load_regs.emplace_back(0x40+i,h[3+i]);
 load_regs.emplace_back(3,LOAD_SRC+load_job*1024);load_regs.emplace_back(4,h[0]);
 load_regs.emplace_back(5,h[2]);for(unsigned i=0;i<3;i++)load_regs.emplace_back(6+i,h[19+i]);
 load_regs.emplace_back(9,h[22]);load_regs.emplace_back(10,h[23]);load_regs.emplace_back(11,h[25]|(h[26]<<16));load_regs.emplace_back(12,h[24]);
 load_regs.emplace_back(16,LOAD_OUT+load_job*2048);load_regs.emplace_back(2,1);
 load_config=0;load_state=1;load_config_at=cycles;load_busy=0;
}
static void load_drive(){
 tb->load_reg_wr=0;tb->load_reg_addr=1;tb->load_reg_wdata=0;
 if(!load_active||!tb->reset_n||load_job==load_jobs.size())return;
 if(load_state==0 && cycles-load_epoch>=load_start_at && (load_mode==2||tb->busy))load_make_config();
 if(load_state==1){tb->load_reg_wr=1;tb->load_reg_addr=load_regs[load_config].first;tb->load_reg_wdata=load_regs[load_config].second;}
}
static void load_sample(){
 if(!tb->reset_n)return;
 load_rd.request(tb->load_arvalid,tb->load_arready,tb->load_arlen);
 if(tb->load_rvalid&&tb->load_rready){load_need(load_rd.inflight,"LOAD R beat without AR");load_rd.beats++;load_need(bool(tb->load_rlast)==(load_rd.beats==load_rd.expected),"LOAD RLAST mismatch");if(tb->load_rlast)load_rd.done();}
 load_wr.request(tb->load_awvalid,tb->load_awready,tb->load_awlen);
 if(tb->load_wvalid&&tb->load_wready){load_need(load_wr.inflight,"LOAD W without AW");load_wr.beats++;load_words++;}
 if(tb->load_bvalid){load_need(tb->load_bready,"LOAD B not ready");load_need(load_wr.beats==load_wr.expected,"LOAD B before all W");load_wr.done();}
 if(!load_active||load_job==load_jobs.size())return;
 if(load_state)load_busy+=tb->busy;
 if(load_state==1){if(++load_config==load_regs.size()){load_state=2;load_started=cycles;}return;}
 if(load_state!=2)return;
 load_need(cycles-load_started<2000000,"LOAD DMA completion timeout");
 if(!(tb->load_reg_rdata&2))return;
 load_need((tb->load_reg_rdata&0x6)==0x6 && !(tb->load_reg_rdata&0x6e9),"LOAD error status");
 load_need(!load_rd.inflight&&!load_wr.inflight,"LOAD completed with outstanding memory");
 const auto&j=load_jobs[load_job];
 for(unsigned v=0;v<j.vertices.size();v++){
  const auto&a=j.vertices[v];uint32_t words[9];std::copy_n(a.expected.begin(),5,words);
  words[5]=load_bits(float(int16_t(a.expected[5])));words[6]=load_bits(float(int16_t(a.expected[5]>>16)));words[7]=a.expected[6];words[8]=a.expected[7]&255;
  for(unsigned w=0;w<9;w++){
   uint32_t got=load_read_word(LOAD_OUT+load_job*2048+v*LOAD_OUTPUT_STRIDE+w*4);uint32_t mask=(w==7&&!a.rgb)?0xff000000:0xffffffff;
   if((got&mask)!=(words[w]&mask)){fprintf(stderr,"LOAD job%u vertex%u word%u expected%08x got%08x\n",load_job,v,w,words[w],got);throw std::runtime_error("native DMA result mismatch");}
  }
  load_vertices++;load_rgb+=a.rgb;
 }
 uint64_t duration=cycles-load_started,whole=cycles-load_config_at+1;load_latencies.values.push_back(duration);load_withconfig.values.push_back(whole);
 printf("LOAD_JOB index=%u vertices=%u cycles=%llu with_config=%llu gpu_busy=%llu start=%llu end=%llu\n",load_job,j.h[0],(unsigned long long)duration,(unsigned long long)whole,(unsigned long long)load_busy,(unsigned long long)(load_started-load_epoch),(unsigned long long)(cycles-load_epoch));
 load_job++;load_state=0;
}
static void load_report(){
 if(load_active)load_need(load_job==load_jobs.size(),"LOAD jobs incomplete");
 printf("LOAD mode=%u jobs=%u vertices=%llu independent_rgb=%llu words=%llu",load_mode,load_job,(unsigned long long)load_vertices,(unsigned long long)load_rgb,(unsigned long long)load_words);
 load_latencies.print("job","cycles");load_withconfig.print("job","with_config");load_rd.print("load_read");load_wr.print("load_write");printf("\n");
}
