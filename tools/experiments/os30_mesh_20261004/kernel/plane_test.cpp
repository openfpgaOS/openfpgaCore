#include "Vplane_tb.h"
#include "verilated.h"
#include <array>
#include <cassert>
#include <cstdio>
#include <cstdint>
#include <stdexcept>

int main(int argc,char**argv){
    try{
        Verilated::commandArgs(argc,argv);
        if(argc!=2)return 2;
        FILE*f=fopen(argv[1],"rb");if(!f)return 2;
        Vplane_tb dut;
        auto tick=[&](){dut.clk=0;dut.eval();dut.clk=1;dut.eval();};
        dut.reset_n=0;dut.start=0;dut.cancel=0;dut.host_we=0;tick();tick();dut.reset_n=1;tick();
        auto write=[&](bool bank,unsigned addr,uint32_t data){dut.host_we=1;dut.host_bank=bank;dut.host_addr=addr;dut.host_data=data;tick();dut.host_we=0;};
        uint64_t cases=0,cycles=0;unsigned max_cycles=0,aborts=0;
        uint32_t record[16];
        const unsigned addresses[]={100,101,102,103,106,107,108,12,21,22};
        while(fread(record,4,16,f)==16){
            for(unsigned i=0;i<3;i++)write(true,i,record[i]);
            for(unsigned i=0;i<10;i++)write(false,addresses[i],record[3+i]);
            write(false,112,2);
            dut.start=1;tick();dut.start=0;
            unsigned elapsed=0;
            while(!dut.done&&elapsed<200){tick();elapsed++;}
            if(elapsed>=200||dut.out_origin!=record[13]||dut.out_du!=record[14]||dut.out_dv!=record[15]){
                fprintf(stderr,"case%llu cycles%u origin%08x/%08x du%08x/%08x dv%08x/%08x\n",
                        (unsigned long long)cases,elapsed,dut.out_origin,record[13],dut.out_du,record[14],dut.out_dv,record[15]);
                throw std::runtime_error("plane mismatch");
            }
            max_cycles=elapsed>max_cycles?elapsed:max_cycles;cycles+=elapsed;cases++;
            tick();
        }
        fclose(f);
        // Cancellation at every phase, followed immediately by a clean restart.
        for(unsigned delay=0;delay<50;delay++){
            dut.start=1;tick();dut.start=0;
            for(unsigned c=0;c<delay;c++)tick();
            dut.cancel=1;tick();dut.cancel=0;tick();
            assert(!dut.busy&&!dut.done);aborts++;
        }
        printf("PASS cases=%llu cycles=%llu cycles_per_plane=%u aborts=%u\n",
               (unsigned long long)cases,(unsigned long long)cycles,max_cycles,aborts);
    }catch(const std::exception&e){fprintf(stderr,"FAIL %s\n",e.what());return 1;}
}
