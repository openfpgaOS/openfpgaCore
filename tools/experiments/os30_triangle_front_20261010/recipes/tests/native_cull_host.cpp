#include "Vtb_system.h"
#include "Vtb_system___024root.h"
#include "Vtb_system_tb_system.h"
#include <verilated.h>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <vector>
#include <stdexcept>
static Vtb_system*tb;static uint64_t cycles=0,accum74=0;static bool state74=false;
static void half(bool high){const uint64_t deadline=Verilated::time()+5555;while(tb->eventsPending()&&tb->nextTimeSlot()<=deadline){const uint64_t next=tb->nextTimeSlot();Verilated::timeInc(next-Verilated::time());tb->eval();}Verilated::timeInc(deadline-Verilated::time());tb->clk_cpu=high;accum74+=74;while(accum74>=90){accum74-=90;state74=!state74;}tb->clk_74a=state74;tb->eval();}
static void tick(){half(false);half(true);cycles++;}
static void require(bool v,const char*m){if(!v)throw std::runtime_error(m);}
static uint32_t get(uint32_t a){auto&m=tb->rootp->tb_system->__PVT__sdram_chip__DOT__mem;uint32_t i=(a&0x3ffffff)/2;return m[i]|uint32_t(m[i+1])<<16;}
static void put(uint32_t a,uint32_t v){require(!tb->cpu_run,"live host write");auto&m=tb->rootp->tb_system->__PVT__sdram_chip__DOT__mem;uint32_t i=(a&0x3ffffff)/2;m[i]=v&65535;m[i+1]=v>>16;}
int main(int argc,char**argv)try{
 require(argc==5,"host kernel.bin output.bin result.json count");Verilated::commandArgs(argc,argv);setvbuf(stdout,nullptr,_IOLBF,0);const unsigned count=std::stoul(argv[4]);tb=new Vtb_system;
 tb->clk_cpu=0;tb->clk_74a=0;tb->reset_n=0;tb->cpu_run=0;tb->vsync=0;tb->bd_gpu_wr=0;tb->bd_geo_wr=0;tb->bd_cram0_wr=0;tb->bd_sdram_we=0;tb->inj_burst_rd=0;tb->inj_audio_valid=0;
 for(int i=0;i<10;i++)tick();auto&s=*tb->rootp->tb_system;
 std::ifstream file(argv[1],std::ios::binary);require(bool(file),"kernel file");std::vector<uint8_t>code(std::istreambuf_iterator<char>(file),{});code.resize((code.size()+3)&~3u);
 for(size_t i=0;i<code.size();i+=4)put(0x10400000+i,code[i]|uint32_t(code[i+1])<<8|uint32_t(code[i+2])<<16|uint32_t(code[i+3])<<24);
 for(unsigned i=0;i<12;i++)put(0x1007f000+i*4,0);
 s.periph__DOT__ram__DOT__mem[0]=0x104002b7;s.periph__DOT__ram__DOT__mem[1]=0x00028067;
 tb->reset_n=1;for(int i=0;i<30000;i++)tick();require(tb->obs_bcr_ready&&!tb->obs_cram_errors,"memory init");
 half(false);tb->cpu_run=1;tb->eval();uint32_t status=0,prior=~0u,lastpc=0;
 for(uint64_t k=0;k<30000000;k++){
  half(false);if(tb->obs_retire)lastpc=tb->obs_retire_pc;
  half(true);cycles++;require(!tb->obs_cram_errors,"paired protocol error");
  if((k&255)==0){const uint32_t stage=get(0x1007f008);if(stage!=prior){std::printf("completed %u cycle %llu\n",stage,(unsigned long long)cycles);prior=stage;}status=get(0x1007f000);if(status)break;}
 }
 const bool pass=status==0x600dcafe&&get(0x1007f008)==count;
 std::ofstream output(argv[2],std::ios::binary);if(pass)for(unsigned i=0;i<count*11;i++){uint32_t v=get(0x10a00000+i*4);output.write(reinterpret_cast<char*>(&v),4);}
 std::ofstream report(argv[3]);report<<"{\"pass\":"<<(pass?"true":"false")<<",\"vectors\":"<<count<<",\"status\":"<<status<<",\"cycles\":"<<cycles<<",\"last_pc\":"<<lastpc<<",\"mcause\":"<<get(0x1007f018)<<",\"mepc\":"<<get(0x1007f01c)<<",\"mtval\":"<<get(0x1007f020)<<"}\n";
 tb->final();delete tb;return pass?0:1;
}catch(const std::exception&e){std::fprintf(stderr,"ERROR %s\n",e.what());return 2;}
