// Deterministic CPU/GPU transfers around the production audio mixer.
// The FIFO consumer models 48 kHz after a 256-sample startup prefill; this
// is a memory-service test, not a model of Pocket's physical audio CDC.
#include "Vtb_sdram_cont.h"
#include <verilated.h>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <vector>
#include <algorithm>
#include <cstring>

static Vtb_sdram_cont d;
static uint64_t cycles, start_cycle, phase, first_ar, acts, pres;
static unsigned level, underruns, outputs, consumed;
static unsigned mhz=100;
static bool running, playing;
static std::vector<uint32_t> samples;
static std::vector<unsigned> latencies, full_latencies, output_gaps;
static bool audio_waiting;
static uint64_t request_start, last_output;
static void die(const char *s){ std::fprintf(stderr,"FAIL %s cycle=%llu\n",s,(unsigned long long)cycles); std::exit(1); }
static void low(){d.clk=0;d.fifo_level=level;d.eval();}
static void edge(){
 bool wr=d.sample_wr; uint32_t sample=d.sample_data;
 if(running){
  if(d.m3_arvalid && !audio_waiting){audio_waiting=true;request_start=cycles;}
  if(d.m3_arvalid && d.m3_arready)first_ar=cycles;
  if(d.m3_rvalid && d.m3_rready && d.m3_rlast){
   latencies.push_back(cycles-first_ar);
   full_latencies.push_back(cycles-request_start);audio_waiting=false;
  }
  if(d.memory_command==11)acts++;
  if(d.memory_command==10)pres++;
 }
 d.clk=1;d.eval();cycles++;
 if(running){
  if(wr){if(last_output)output_gaps.push_back(cycles-last_output);last_output=cycles;if(level>=1023)die("FIFO overflow");level++;samples.push_back(sample);outputs++;}
  if(!playing && level>=256){playing=true;phase=0;}
  if(playing){phase+=48000;if(phase>=mhz*1000000ull){phase-=mhz*1000000ull;consumed++;if(level)level--;else underruns++;}}
  if(d.protocol_errors)die("SDRAM protocol");
 }
}
static void tick(){low();edge();}
static uint32_t pattern(unsigned a){return (a*2654435761u)^0xb17d8065u;}
static void preload(unsigned addr,unsigned value){d.bd_word_addr=addr/4;d.bd_wdata=value;d.bd_we=1;tick();d.bd_we=0;}
static void mmio(unsigned v,unsigned f,unsigned x){
 d.voice_sel=v;d.voice_field=f;d.voice_wdata=x;d.voice_wr=1;
 for(unsigned n=0;;n++){low();bool ready=d.voice_wr_ready;edge();if(ready)break;if(n>100000)die("voice MMIO timeout");}
 d.voice_wr=0;tick();
}
struct Master {
 unsigned id,job=0,stage=0,beat=0; bool aw=false,wd=false;
 unsigned readbase,writebase,total=100000;
 uint64_t finished=0;
 unsigned count()const{return id==0?8:16;}
 unsigned address(bool write)const{return (write?writebase:readbase)+((job*count()*4)%65536);}
 void drive(){
  bool active=job<total;
  bool ar=active&&stage==0,w=active&&stage==2;
  if(id==0){
   d.m0_arvalid=ar;d.m0_araddr=address(false);d.m0_arlen=count()-1;
   d.m0_awvalid=w&&!aw;d.m0_awaddr=address(true);d.m0_awlen=count()-1;
   d.m0_wvalid=w&&!wd;d.m0_wdata=pattern(address(true)+beat*4);d.m0_wstrb=15;d.m0_wlast=beat==count()-1;
  }else{
   d.m1_arvalid=ar;d.m1_araddr=address(false);d.m1_arlen=count()-1;d.m1_rready=1;
   d.m1_awvalid=w&&!aw;d.m1_awaddr=address(true);d.m1_awlen=count()-1;
   d.m1_wvalid=w&&!wd;d.m1_wdata=pattern(address(true)+beat*4);d.m1_wstrb=15;d.m1_wlast=beat==count()-1;
  }
 }
 void sample(){
  if(job==total)return;
  bool ar=id==0?d.m0_arvalid&&d.m0_arready:d.m1_arvalid&&d.m1_arready;
  bool rv=id==0?d.m0_rvalid:d.m1_rvalid;
  bool rl=id==0?d.m0_rlast:d.m1_rlast;
  unsigned rd=id==0?d.m0_rdata:d.m1_rdata;
  bool ah=id==0?d.m0_awvalid&&d.m0_awready:d.m1_awvalid&&d.m1_awready;
  bool wh=id==0?d.m0_wvalid&&d.m0_wready:d.m1_wvalid&&d.m1_wready;
  bool b=id==0?d.m0_bvalid:d.m1_bvalid;
  if(ar){stage=1;beat=0;}
  if(rv){if(rd!=pattern(address(false)+beat*4))die("read data");if(rl!=(beat==count()-1))die("read last");beat++;if(rl){stage=2;beat=0;aw=wd=false;}}
  if(ah)aw=true;
  if(wh){beat++;if(beat==count())wd=true;}
  if(b){if(!aw||!wd)die("early write response");job++;stage=0;beat=0;aw=wd=false;if(job==total)finished=cycles-start_cycle;}
 }
};
int main(int argc,char **argv){
 Verilated::commandArgs(argc,argv);
 if(argc<3)die("usage: bench output.raw voices [MHz] [quiet]");
 unsigned voices=std::strtoul(argv[2],nullptr,0);if(voices<1||voices>32)die("voice count");
 if(argc>3)mhz=std::strtoul(argv[3],nullptr,0);
 const char *mode=argc>4?argv[4]:"stress";
 bool quiet=std::strcmp(mode,"quiet")==0;
 bool cpu_only=std::strcmp(mode,"cpu")==0;
 if(!quiet && !cpu_only && std::strcmp(mode,"stress"))die("mode");
 d.reset_n=0;for(int n=0;n<20;n++)tick();d.reset_n=1;for(int n=0;n<11000;n++)tick();
 for(unsigned base:{0x00400000u,0x01000000u})for(unsigned a=0;a<65536;a+=4)preload(base+a,pattern(base+a));
 for(unsigned v=0;v<voices;v++){
  for(unsigned a=0;a<4096;a+=4){
   int l=int((a/2+v*17)%1000)-500,r=int((a/2+v*29+1)%1000)-500;
   preload(0x02000000+v*4096+a,(uint16_t)l|((unsigned)(uint16_t)r<<16));
  }
  unsigned stereo=v%3==0,len=stereo?1024:2048;
  mmio(v,0,0x02000000+v*4096);mmio(v,1,len);
  static const unsigned rates[]={0x4000,0x6000,0xc000,0x10000,0x14000};
  mmio(v,2,rates[v%5]);mmio(v,8,0);mmio(v,7,len);
  mmio(v,9,0x6060);mmio(v,10,0);mmio(v,6,0);mmio(v,3,5|(stereo?2:0));
 }
 Master gpu{0},cpu{1};gpu.readbase=0x01000000;gpu.writebase=0x03000000;
 cpu.readbase=0x00400000;cpu.writebase=0x00800000;
 if(quiet)gpu.total=cpu.total=0;
 if(cpu_only){gpu.total=0;cpu.writebase=0x03000000;}
 d.mixer_enable=1;running=true;start_cycle=cycles;
 bool scan_pending=false;unsigned scan_row=0;uint64_t next_scan=cycles;
 while(outputs<8192 || gpu.job<gpu.total || cpu.job<cpu.total){
  if(cycles-start_cycle>60000000){std::fprintf(stderr,"outputs=%u level=%u jobs=%u/%u stages=%u/%u beats=%u/%u voice=%u rd=%u/%u busy=%u arb=%u/%u\n",outputs,level,gpu.job,cpu.job,gpu.stage,cpu.stage,gpu.beat,cpu.beat,d.sample_wr,d.m3_arvalid,d.m3_arready,d.dbg_busy,d.dbg_arb_state,d.dbg_grant);die("simulation timeout");}
  gpu.drive();cpu.drive();
  if(!quiet && !scan_pending && cycles>=next_scan){scan_pending=true;d.inj_burst_rd=1;d.inj_burst_addr=(0x03000000+scan_row*320)/2;d.inj_burst_len=80;scan_row=(scan_row+1)%(cpu_only?240:200);next_scan+=mhz*1000000ull/(cpu_only?14400:12000);}
  low();gpu.sample();cpu.sample();
  bool scan_done=d.inj_burst_data_done;
  edge();d.inj_burst_rd=0;if(scan_done)scan_pending=false;
 }
 d.mixer_enable=0;running=false;d.m0_arvalid=d.m0_awvalid=d.m0_wvalid=0;d.m1_arvalid=d.m1_awvalid=d.m1_wvalid=0;
 for(unsigned n=0;n<1000;n++)tick();
 if(d.protocol_errors)die("SDRAM protocol during drain");
 if(!quiet)for(unsigned base:cpu_only?std::vector<unsigned>{0x03000000u}:std::vector<unsigned>{0x00800000u,0x03000000u})for(unsigned a=0;a<65536;a+=4){d.bd_word_addr=(base+a)/4;low();if(d.bd_rdata!=pattern(base+a))die("write data");}
 FILE *f=std::fopen(argv[1],"wb");if(!f)die("output file");if(std::fwrite(samples.data(),4,8192,f)!=8192||std::fclose(f))die("output write");
 std::sort(latencies.begin(),latencies.end());
 std::sort(full_latencies.begin(),full_latencies.end());
 std::sort(output_gaps.begin(),output_gaps.end());
 std::printf("SERVICE cpu_only=%u full_audio_p99=%u full_audio_max=%u output_gap_p99=%u output_gap_max=%u\n",cpu_only,full_latencies[full_latencies.size()*99/100],full_latencies.back(),output_gaps[output_gaps.size()*99/100],output_gaps.back());
 std::printf("PASS voices=%u MHz=%u quiet=%u cycles=%llu gpu_cycles=%llu cpu_cycles=%llu samples=%u consumed=%u underruns=%u audio_p99=%u audio_max=%u acts=%llu pres=%llu protocol_errors=%u\n",voices,mhz,quiet,(unsigned long long)(cycles-start_cycle),(unsigned long long)gpu.finished,(unsigned long long)cpu.finished,outputs,consumed,underruns,latencies[latencies.size()*99/100],latencies.back(),(unsigned long long)acts,(unsigned long long)pres,d.protocol_errors);
 return underruns?1:0;
}
