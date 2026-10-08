#include "Vtb.h"
#include "verilated.h"
#include <cstdint>
#include <cstdio>
#include <cstdlib>
static Vtb t;
static uint16_t words[4],current;
static bool valid,word_valid;
static uint64_t cycles,collisions,reset_selects,selects;
static void step() {
    t.clk=0;t.eval();
    const bool select=t.reset_n&&!t.soft_reset&&t.select_req;
    if(!t.reset_n||t.soft_reset)valid=false;
    else if(select)valid=true;
    if(select) {current=words[t.spanprod_idx];word_valid=(t.spanprod_cnt_valid>>t.spanprod_idx)&1;++selects;}
    collisions+=select&&t.spanprod_count_we&&t.spanprod_idx==t.spanprod_count_wa;
    reset_selects+=(!t.reset_n||t.soft_reset)&&t.select_req;
    if(t.spanprod_count_we)words[t.spanprod_count_wa]=t.spanprod_count_wd;
    t.clk=1;t.eval();++cycles;
    uint16_t expected=valid&&word_valid?current:0;
    if(t.baseline_count!=expected||t.candidate_count!=expected||
       t.baseline_nonzero!=(expected!=0)||t.candidate_nonzero!=(expected!=0)) {
        std::fprintf(stderr,"FAIL cycle=%llu count=%u old=%u new=%u nz=%u/%u\n",
            (unsigned long long)cycles,unsigned(expected),unsigned(t.baseline_count),
            unsigned(t.candidate_count),unsigned(t.baseline_nonzero),unsigned(t.candidate_nonzero));std::abort();
    }
    t.clk=0;t.eval();
}
int main(int argc,char**argv) {
    Verilated::commandArgs(argc,argv);t.reset_n=0;step();t.reset_n=1;
    for(unsigned value=0;value<65536;value++)for(unsigned lane=0;lane<4;lane++) {
        t.select_req=0;t.spanprod_count_we=1;t.spanprod_count_wa=lane;t.spanprod_count_wd=value;step();
        t.spanprod_count_we=0;t.select_req=1;
        for(unsigned index=0;index<4;index++)for(unsigned mask=0;mask<16;mask++) {
            t.spanprod_idx=index;t.spanprod_cnt_valid=mask;step();
        }
    }
    uint32_t random=0x581dedad;
    for(unsigned i=0;i<1000000;i++) {
        random^=random<<13;random^=random>>17;random^=random<<5;
        t.reset_n=(random&127)!=0;t.soft_reset=((random>>7)&255)==0;
        t.select_req=(random>>4)&1;t.spanprod_count_we=(random>>3)&1;
        t.spanprod_count_wa=(random>>8)&3;t.spanprod_idx=(random>>10)&3;
        t.spanprod_cnt_valid=(random>>12)&15;
        t.spanprod_count_wd=(i&3)?uint16_t(random>>16):0;step();
    }
    if(!collisions||!reset_selects)std::abort();
    std::printf("PASS count shadow: all65536 values x4 write lanes x4 read lanes x16 masks; 1M mixed cycles; cycles=%llu selects=%llu same_address_write_select=%llu reset_select=%llu\n",
        (unsigned long long)cycles,(unsigned long long)selects,(unsigned long long)collisions,(unsigned long long)reset_selects);
}
