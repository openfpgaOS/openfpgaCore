#include "Vaxi_sdram_clock_bridge.h"
#include <verilated.h>
#include <array>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <deque>
#include <random>

// Transport-level test: independent AW/W, return traffic, long stalls,
// FIFO wraparound, coincident/offset edges, and reset while all FIFOs fill.
struct Channel { bool active=false; uint64_t data=0, sent=0, received=0;
                 std::deque<uint64_t> expected; };
static Vaxi_sdram_clock_bridge d;
static std::array<Channel,5> ch;
static std::mt19937 rng(0x831661);
static void fail(const char *why, int channel, unsigned t) {
    std::fprintf(stderr,"FAIL %s channel=%d time=%u\n",why,channel,t); std::exit(1);
}
static void drive(unsigned t, bool draining) {
    for (int i=0;i<5;i++) {
        auto &c=ch[i];
        if (!c.active && !draining && rng()%4!=0) {
            c.active=true; c.data=(uint64_t(rng())<<32)|rng();
            c.data &= (uint64_t(1) << (i<2?40:i==2?37:i==3?35:2))-1;
        }
    }
    d.s_arvalid=ch[0].active; d.s_araddr=ch[0].data; d.s_arlen=ch[0].data>>32;
    d.s_awvalid=ch[1].active; d.s_awaddr=ch[1].data; d.s_awlen=ch[1].data>>32;
    d.s_wvalid=ch[2].active; d.s_wdata=ch[2].data; d.s_wstrb=ch[2].data>>32; d.s_wlast=ch[2].data>>36;
    d.m_rvalid=ch[3].active; d.m_rdata=ch[3].data; d.m_rresp=ch[3].data>>32; d.m_rlast=ch[3].data>>34;
    d.m_bvalid=ch[4].active; d.m_bresp=ch[4].data;
    bool ready[5];
    for(int i=0;i<5;i++) ready[i]=draining || ((t/127)%7!=i && rng()%3!=0);
    d.m_arready=ready[0]; d.m_awready=ready[1]; d.m_wready=ready[2]; d.s_rready=ready[3]; d.s_bready=ready[4];
    d.eval();
}
static void step(unsigned t, unsigned shalf, unsigned mhalf, unsigned offset, bool drain) {
    const bool sc=(t/shalf)&1, mc=((t+offset)/mhalf)&1;
    const bool sr=sc&&!d.s_clk, mr=mc&&!d.m_clk;
    drive(t,drain);
    bool sent[]={sr&&d.s_arvalid&&d.s_arready, sr&&d.s_awvalid&&d.s_awready, sr&&d.s_wvalid&&d.s_wready,
                 mr&&d.m_rvalid&&d.m_rready, mr&&d.m_bvalid&&d.m_bready};
    bool got[]={mr&&d.m_arvalid&&d.m_arready, mr&&d.m_awvalid&&d.m_awready, mr&&d.m_wvalid&&d.m_wready,
                sr&&d.s_rvalid&&d.s_rready, sr&&d.s_bvalid&&d.s_bready};
    uint64_t data[]={uint64_t(d.m_araddr)|(uint64_t(d.m_arlen)<<32),uint64_t(d.m_awaddr)|(uint64_t(d.m_awlen)<<32),
        uint64_t(d.m_wdata)|(uint64_t(d.m_wstrb)<<32)|(uint64_t(d.m_wlast)<<36),
        uint64_t(d.s_rdata)|(uint64_t(d.s_rresp)<<32)|(uint64_t(d.s_rlast)<<34),d.s_bresp};
    for(int i=0;i<5;i++) {
        auto &c=ch[i];
        if (got[i]) {
            if(c.expected.empty()) fail("unexpected beat",i,t);
            if(c.expected.front()!=data[i]) fail("data/order mismatch",i,t);
            c.expected.pop_front(); c.received++;
        }
        if(sent[i]) {c.expected.push_back(c.data);c.sent++;c.active=false;}
    }
    d.s_clk=sc; d.m_clk=mc; d.eval();
}
int main(int argc,char **argv) {
    Verilated::commandArgs(argc,argv);
    uint64_t total=0;
    for(auto clocks: {std::array<unsigned,3>{6,3,0}, {6,3,1}, {6,3,2}, {5,3,1}, {3,7,2}}) {
        d.reset_n=0; d.s_clk=0; d.m_clk=0; d.eval();
        ch={};
        for(unsigned t=0;t<200000;t++) {
            if(t==80 || t==100080) d.reset_n=1;
            if(t==100000) {d.reset_n=0;d.eval();ch={};}
            step(t,clocks[0],clocks[1],clocks[2],!d.reset_n || t>=190000);
        }
        for(int i=0;i<5;i++) {
            if(ch[i].active || !ch[i].expected.empty() || ch[i].received<1000) fail("incomplete/vacuous drain",i,200000);
            total+=ch[i].received;
        }
    }
    std::printf("PASS clock bridge: %llu checked beats after mid-traffic resets, five clock/phase cases\n",(unsigned long long)total);
}
