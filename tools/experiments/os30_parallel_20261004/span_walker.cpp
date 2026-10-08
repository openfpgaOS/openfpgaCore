#include "Vspan_walker_tb.h"
#include "verilated.h"
#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <vector>

using Span=std::array<uint16_t,3>;
static uint32_t rng=0x614eaf03;
static uint32_t random32() { rng^=rng<<13; rng^=rng>>17; rng^=rng<<5; return rng; }
int main(int argc,char **argv) {
    Verilated::commandArgs(argc,argv);
    Vspan_walker_tb t;
    auto tick=[&](){t.clk=0;t.eval();t.clk=1;t.eval();};
    t.reset_n=0;t.start=0;t.abort_req=0;t.new_start=0;t.load=0;
    tick();tick();t.reset_n=1;tick();
    unsigned prepared_cases=0,aborted_cases=0;
    uint64_t spans=0;
    for (unsigned trial=0;trial<10000;trial++) {
        std::vector<Span> old_out,new_out;
        std::array<uint32_t,9> packet{};
        bool captured=false,old_seen=false,new_seen=false;
        int loading=-1;
        t.subpix=trial&1;
        t.x0=random32();t.y0=random32();t.x1=random32();t.y1=random32();
        t.x2=random32();t.y2=random32();
        if (trial%3==0) {
            auto pos=[](){return int(random32()%18000)-7000;};
            t.x0=pos();t.x1=pos();t.x2=pos();t.y0=pos();t.y1=pos();t.y2=pos();
        }
        // Exact horizontals, tiny edges, maximum signed differences and all
        // six vertex orderings occur alongside unrestricted random endpoints.
        if (trial%17==0)t.y1=t.y0;
        if (trial%29==0)t.y2=uint16_t(t.y1+1);
        if (trial%31==0){t.y0=0x8000;t.y2=0x7fff;}
        int xmin=int(random32()%160)-80,ymin=int(random32()%100)-50;
        t.cx0=xmin;t.cx1=xmin+1+random32()%321;
        t.cy0=ymin;t.cy1=ymin+1+random32()%241;
        t.start=1;tick();t.start=0;
        bool old_stalled=false,new_stalled=false;
        Span old_saved{},new_saved{};
        unsigned cycle;
        for (cycle=0;cycle<12000;cycle++) {
            t.old_ready=(random32()%5)!=0;t.new_ready=(random32()%3)!=0;
            t.clk=0;t.eval();
            Span old_now{t.old_u,t.old_v,t.old_count},new_now{t.new_u,t.new_v,t.new_count};
            if (old_stalled)assert(t.old_valid && old_now==old_saved);
            if (new_stalled)assert(t.new_valid && new_now==new_saved);
            old_stalled=t.old_valid&&!t.old_ready;new_stalled=t.new_valid&&!t.new_ready;
            old_saved=old_now;new_saved=new_now;
            if (t.old_valid&&t.old_ready)old_out.push_back(old_now);
            if (t.new_valid&&t.new_ready)new_out.push_back(new_now);
            old_seen|=t.old_busy;new_seen|=t.new_busy;
            if (t.prepared) {
                assert(!captured);captured=true;prepared_cases++;
                for(unsigned i=0;i<9;i++)packet[i]=t.packet[i];
                loading=0;
            }
            if (loading>=0 && loading<9) {
                t.load=1;t.load_index=loading;t.load_data=packet[loading++];
            } else {
                t.load=0;
                t.new_start=loading==9;
                if(loading==9)loading++;
            }
            t.clk=1;t.eval();
            if(old_seen&&!t.old_busy&&(!captured||(new_seen&&!t.new_busy)))break;
        }
        assert(cycle<12000);
        if (old_out!=new_out) {
            fprintf(stderr,"Mismatch case %u: %zu vs %zu spans\n",trial,old_out.size(),new_out.size());
            return 1;
        }
        spans+=old_out.size();
        t.load=0;t.new_start=0;
        // Abort a prepared walker while its first valid span is held.
        if (captured && !old_out.empty() && trial%37==0) {
            for(unsigned i=0;i<9;i++) {t.load=1;t.load_index=i;t.load_data=packet[i];tick();}
            t.load=0;t.new_ready=0;t.new_start=1;tick();t.new_start=0;
            unsigned timeout=0;while(!t.new_valid&&timeout++<2000)tick();assert(timeout<2000);
            t.abort_req=1;tick();t.abort_req=0;tick();
            assert(!t.new_busy&&!t.new_valid);aborted_cases++;
        }
    }
    printf("PASS cases=10000 prepared=%u spans=%llu aborts=%u\n",prepared_cases,
           (unsigned long long)spans,aborted_cases);
}
