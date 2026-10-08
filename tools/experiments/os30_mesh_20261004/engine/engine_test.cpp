#include "Vgpu_mesh_engine.h"
#include "verilated.h"
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <vector>
#include <stdexcept>
#include <array>
static Vgpu_mesh_engine dut;
static std::array<uint32_t,256> bank;
static uint32_t random_state=0x1534ace1;
static unsigned ticks;
static uint32_t rnd(){random_state^=random_state<<13;random_state^=random_state>>17;random_state^=random_state<<5;return random_state;}
static void tick(){
    dut.clk=0;dut.eval();
    const uint32_t q=bank[dut.bank_addr];
    if(dut.bank_we)bank[dut.bank_addr]=dut.bank_wdata;
    dut.clk=1;dut.eval();dut.bank_rdata=q;dut.eval();++ticks;
}
static void require(bool v,const char *s){if(!v)throw std::runtime_error(s);}
static void reset(){dut.reset_n=0;dut.start=0;dut.cancel=0;dut.code_we=0;dut.scratch_we=0;dut.out_ready=0;tick();tick();dut.reset_n=1;tick();}
static void upload(const std::vector<uint32_t>& code){
    require(!dut.busy,"upload busy");
    for(unsigned i=0;i<code.size();++i){dut.code_we=1;dut.code_addr=i;dut.code_data=code[i];tick();}
    dut.code_we=0;
}
static unsigned execute(const std::vector<uint32_t>& expected,unsigned limit,bool stalls){
    dut.entry=0;dut.start=1;tick();dut.start=0;
    unsigned n=0,idle=0;bool held=false;uint32_t held_word=0;
    for(unsigned cycle=0;cycle<limit;++cycle){
        dut.out_ready=!stalls || (rnd()%5!=0);dut.clk=0;dut.eval();
        if(held)require(dut.out_valid && dut.out_data==held_word,"stalled output changed");
        held=dut.out_valid&&!dut.out_ready;held_word=dut.out_data;
        if(held)++idle;
        if(dut.out_valid&&dut.out_ready){
            if(n>=expected.size()||dut.out_data!=expected[n]){
                fprintf(stderr,"word %u got %08x expected %08x at cycle %u\n",n,dut.out_data,n<expected.size()?expected[n]:0,cycle);
                throw std::runtime_error("arithmetic mismatch");
            }++n;
        }
        tick();
        if(dut.done){require(!dut.fault,"engine fault");require(n==expected.size(),"output truncated");return dut.cycles-idle;}
    }throw std::runtime_error("execution timeout");
}
int main(int argc,char **argv){
    try{
        if(argc!=2)return 2;
        FILE*f=fopen(argv[1],"rb");require(f,"vectors open");
        reset();unsigned count=0;uint64_t words=0;
        for(;;){
            uint32_t h[4];if(fread(h,4,4,f)!=4)break;
            std::vector<uint32_t> code(h[0]),expected(h[1]);
            require(fread(code.data(),4,code.size(),f)==code.size(),"code read");
            require(fread(expected.data(),4,expected.size(),f)==expected.size(),"expected read");
            reset();bank.fill(0);upload(code);
            unsigned measured=execute(expected,h[2]+10000,true);
            if(measured!=h[2]){fprintf(stderr,"case%u cycles%u expected%u\n",count,measured,h[2]);throw std::runtime_error("cycle model mismatch");}
            words+=expected.size();++count;
        }fclose(f);
        // Cancellation at every phase of a program with DIV, RAM write, OUT.
        const std::vector<uint32_t> cancellation={0x0100007f,0x01010003,0x21020001,0x17020000,0x18000200,0};
        unsigned aborts=0;
        for(unsigned delay=0;delay<90;++delay){
            reset();upload(cancellation);dut.start=1;tick();dut.start=0;dut.out_ready=0;
            for(unsigned i=0;i<delay;++i)tick();
            dut.cancel=1;dut.clk=0;dut.eval();require(!dut.out_valid&&!dut.bank_we,"abort leaks output/write");tick();dut.cancel=0;tick();
            require(!dut.busy&&!dut.done,"abort did not cancel");
            upload({0x01001234,0x18000000,0});execute({0x1234},30,true);++aborts;
        }
        reset();upload({0xff000000});dut.start=1;tick();dut.start=0;
        while(!dut.done)tick();require(dut.fault==1,"illegal opcode accepted");
        printf("PASS %u programs, %llu exact output words, %u cancellation phases, illegal-op trap; cycle counts exact\n",count,(unsigned long long)words,aborts);
    }catch(const std::exception&e){fprintf(stderr,"FAIL %s\n",e.what());return 1;}return 0;
}
