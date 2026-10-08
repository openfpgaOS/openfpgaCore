#include "Vgather_tb.h"
#include "verilated.h"
#include <cstdio>
#include <cstdint>
#include <stdexcept>

int main(int argc,char**argv){
    try{
        Verilated::commandArgs(argc,argv);if(argc!=2)return 2;
        FILE*f=fopen(argv[1],"rb");if(!f)return 2;Vgather_tb dut;
        auto tick=[&](){dut.clk=0;dut.eval();
            if(dut.ownership_violation)throw std::runtime_error("gather scratch overwritten");
            dut.clk=1;dut.eval();};
        dut.reset_n=0;dut.start=0;dut.cancel=0;dut.host_we=0;tick();tick();dut.reset_n=1;tick();
        auto write=[&](bool bank,unsigned addr,uint32_t data){dut.host_we=1;dut.host_bank=bank;dut.host_addr=addr;dut.host_data=data;tick();dut.host_we=0;};
        unsigned count=0,max_cycles=0;uint64_t cycles=0;uint32_t record[25];
        while(fread(record,4,25,f)==25){
            const uint32_t ordinal=record[0];uint32_t index=record[1]>>((ordinal&1)*16);
            write(false,ordinal>>1,record[1]);
            for(unsigned i=0;i<3;i++)for(unsigned j=0;j<7;j++)write(true,((index>>(5*i))&31)*7+j,record[2+i*7+j]);
            dut.tri_index=ordinal;dut.start=1;tick();dut.start=0;
            unsigned elapsed=0;while(!dut.done&&elapsed<100){tick();elapsed++;}
            if(elapsed==100)throw std::runtime_error("gather timeout");
            for(unsigned i=0;i<21;i++)if(dut.polygon[i]!=record[2+i]){
                fprintf(stderr,"case%u word%u got%08x expected%08x\n",count,i,dut.polygon[i],record[2+i]);
                throw std::runtime_error("gather vertex mismatch");
            }
            if(dut.clip_or!=record[23]||dut.clip_and!=record[24]){
                fprintf(stderr,"case%u masks OR%x/%x AND%x/%x\n",count,dut.clip_or,record[23],dut.clip_and,record[24]);
                throw std::runtime_error("gather mask mismatch");
            }
            cycles+=elapsed;max_cycles=elapsed>max_cycles?elapsed:max_cycles;count++;tick();
        }fclose(f);
        unsigned aborts=0;
        for(unsigned delay=0;delay<65;delay++){
            dut.start=1;tick();dut.start=0;for(unsigned c=0;c<delay;c++)tick();
            dut.cancel=1;tick();dut.cancel=0;tick();
            if(dut.busy||dut.done)throw std::runtime_error("gather cancel failed");aborts++;
        }
        printf("PASS cases=%u cycles=%llu max_cycles=%u aborts=%u\n",count,(unsigned long long)cycles,max_cycles,aborts);
    }catch(const std::exception&e){fprintf(stderr,"FAIL %s\n",e.what());return 1;}
}
