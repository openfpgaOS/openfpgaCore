#include "Vwidth_proof.h"
#include "verilated.h"
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <random>
static Vwidth_proof dut;
static uint64_t vectors;
static void test(uint32_t w,uint32_t o,uint32_t r,bool t) {
    dut.write_ptr=w;dut.output_rdptr=o;dut.input_rdptr=r;dut.take_input=t;dut.eval();vectors++;
    if(dut.mismatch_free || dut.mismatch_space || dut.mismatch_addr) {
        std::fprintf(stderr,"w=%08x o=%08x r=%08x take=%d: free=%llx space=%llx addr=%llx\n",w,o,r,t,
            (unsigned long long)dut.mismatch_free,(unsigned long long)dut.mismatch_space,(unsigned long long)dut.mismatch_addr);std::exit(1);
    }
}
int main(int argc,char**argv) {
    Verilated::commandArgs(argc,argv);std::mt19937 rng(0x0a3032);
    // Exhaust all pointer combinations and consume states through six bits.
    for(unsigned w=0;w<64;w++)for(unsigned o=0;o<64;o++)for(unsigned r=0;r<64;r++)
        for(unsigned t=0;t<2;t++)test(w,o,r,t);
    // Full/empty, adjacent capacities, and pointer wrap at every possible bit.
    for(unsigned q=1;q<=32;q++)for(unsigned j=0;j<512;j++) {
        uint32_t o=rng(),last=uint32_t((uint64_t(1)<<q)-1);
        for(int delta=-2;delta<=2;delta++)for(unsigned t=0;t<2;t++) {
            uint32_t used=last+delta;
            test(o+used,o,0xffffffffu,t);test(o+used,o,uint32_t((uint64_t(1)<<q)-1),t);
        }
    }
    for(unsigned n=0;n<1000000;n++)test(rng(),rng(),rng(),rng()&1);
    std::printf("PASS %llu pointer/consume vectors across 35 ADDR_BITS/QUEUE_BITS configurations, including widths1/32, underflow, exact capacity and wrap\n",(unsigned long long)vectors);
}
