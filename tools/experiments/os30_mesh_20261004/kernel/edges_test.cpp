#include "Vedges_tb.h"
#include "verilated.h"
#include <cstdio>
#include <cstdint>
#include <stdexcept>

int main(int argc,char**argv){
    try{
        Verilated::commandArgs(argc,argv);if(argc!=2)return 2;
        FILE*f=fopen(argv[1],"rb");if(!f)return 2;
        Vedges_tb dut;
        auto tick=[&](){dut.clk=0;dut.eval();dut.clk=1;dut.eval();};
        dut.reset_n=0;dut.start=0;dut.cancel=0;dut.host_we=0;tick();tick();dut.reset_n=1;tick();
        auto write=[&](unsigned addr,uint32_t data){dut.host_we=1;dut.host_addr=addr;dut.host_data=data;tick();dut.host_we=0;};
        unsigned count=0,prepared=0,max_cycles=0;uint64_t cycles=0;
        uint32_t record[21];const unsigned addresses[]={21,22,23,24,25,26,16,12,104,105};
        while(fread(record,4,21,f)==21){
            for(unsigned i=0;i<10;i++)write(addresses[i],record[i]);
            dut.clipx=record[10];dut.start=1;tick();dut.start=0;
            unsigned elapsed=0;while(!dut.done&&elapsed<250){tick();elapsed++;}
            if(elapsed==250||dut.empty==record[11])throw std::runtime_error("edge timeout or empty mismatch");
            if(record[11])for(unsigned i=0;i<9;i++)if(dut.packet[i]!=record[12+i]){
                fprintf(stderr,"case%u word%u got%08x expected%08x\n",count,i,dut.packet[i],record[12+i]);
                throw std::runtime_error("edge mismatch");
            }
            prepared+=record[11];cycles+=elapsed;max_cycles=elapsed>max_cycles?elapsed:max_cycles;
            count++;tick();
        }
        fclose(f);
        printf("PASS cases=%u prepared=%u cycles=%llu max_cycles=%u\n",count,prepared,(unsigned long long)cycles,max_cycles);
    }catch(const std::exception&e){fprintf(stderr,"FAIL %s\n",e.what());return 1;}
}
