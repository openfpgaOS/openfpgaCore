#include "Vproject_tb.h"
#include "verilated.h"
#include <cstdio>
#include <cstdint>
#include <stdexcept>

int main(int argc,char**argv){
    try{
        Verilated::commandArgs(argc,argv);if(argc!=2)return 2;
        FILE*f=fopen(argv[1],"rb");if(!f)return 2;Vproject_tb dut;
        auto tick=[&](){dut.clk=0;dut.eval();
            if(dut.ownership_violation)throw std::runtime_error("projection scratch overwritten");
            dut.clk=1;dut.eval();};
        dut.reset_n=0;dut.start=0;dut.cancel=0;dut.host_we=0;tick();tick();dut.reset_n=1;tick();
        auto write=[&](unsigned addr,uint32_t data){dut.host_we=1;dut.host_addr=addr;dut.host_data=data;tick();dut.host_we=0;};
        unsigned count=0,max_cycles=0;uint64_t cycles=0;uint32_t record[18];
        while(fread(record,4,18,f)==18){
            for(unsigned i=0;i<7;i++)write(64+i,record[i]);
            for(unsigned i=0;i<4;i++)write(49+i,record[7+i]);
            dut.start=1;tick();dut.start=0;
            unsigned elapsed=0;while(!dut.done&&elapsed<150){tick();elapsed++;}
            if(elapsed==150)throw std::runtime_error("projection timeout");
            for(unsigned i=0;i<7;i++)if(dut.output_words[i]!=record[11+i]){
                fprintf(stderr,"case%u word%u got%08x expected%08x\n",count,i,dut.output_words[i],record[11+i]);
                throw std::runtime_error("projection mismatch");
            }
            cycles+=elapsed;max_cycles=elapsed>max_cycles?elapsed:max_cycles;count++;tick();
        }fclose(f);
        unsigned aborts=0;
        for(unsigned delay=0;delay<75;delay++){
            dut.start=1;tick();dut.start=0;for(unsigned c=0;c<delay;c++)tick();
            dut.cancel=1;tick();dut.cancel=0;tick();
            if(dut.busy||dut.done)throw std::runtime_error("projection cancel failed");aborts++;
        }
        printf("PASS cases=%u cycles=%llu max_cycles=%u aborts=%u\n",count,(unsigned long long)cycles,max_cycles,aborts);
    }catch(const std::exception&e){fprintf(stderr,"FAIL %s\n",e.what());return 1;}
}
