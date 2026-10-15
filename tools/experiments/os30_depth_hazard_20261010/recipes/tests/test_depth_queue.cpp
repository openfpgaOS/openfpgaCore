#include "Vdepth_queue.h"
#include "verilated.h"
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <deque>
#include <random>

struct Sim {
    Vdepth_queue d;
    std::mt19937 rng{0x713842dd};
    std::deque<uint32_t> fifo;
    unsigned rd=0,wr=0;
    uint64_t cycles=0,checks=0,pushes=0,pops=0,simultaneous=0,wraps=0;
    uint64_t bypasses=0,same_word=0,same_line=0,aliases=0,guards=0,resets=0;
    uint64_t pop_pending=0,full=0,ring_resets=0;
    static unsigned tag(uint32_t a) {
        const uint32_t line=a>>6;
        return (line^(line>>6)^(line>>12)^(line>>18))&63;
    }
    void require(bool p,const char* s) {
        ++checks;if(!p){std::fprintf(stderr,"FAIL cycle=%llu size=%zu %s\n",(unsigned long long)cycles,fifo.size(),s);std::exit(1);}
    }
    void probe(uint32_t a,unsigned guard=0) {
        d.read_addr=a; d.z_flush_valid=guard&1;d.z_src_pending_valid=(guard>>1)&1;
        d.wc_busy=(guard>>2)&1;d.fbwq_req_valid=(guard>>3)&1;d.fbwq_stage_valid=(guard>>4)&1;
        d.clk=0;d.eval();
        bool conflict=false,exact=false,word=false;
        for(auto w:fifo){conflict|=tag(w)==tag(a);exact|=(w>>6)==(a>>6);word|=w==a;}
        require(bool(d.safe)==(!guard&&!conflict),"safe differs from independent logical FIFO");
        require(!exact||!d.safe,"same cache line falsely clear");
        if(!guard&&!conflict)++bypasses;
        if(word)++same_word;else if(exact)++same_line;
        if(!guard&&conflict&&!exact)++aliases;
        if(guard)++guards;
    }
    void tick(bool push,bool pop,uint32_t address,bool swap=false,bool soft=false,bool hard=false,bool ring=false) {
        require(!push||fifo.size()<16,"illegal registered-full enqueue");
        require(!pop||!fifo.empty(),"illegal empty dequeue");
        d.reset_n=!hard;d.soft_reset=soft;d.ring_reset=ring;
        d.fbwq_wr_ptr=wr;d.fbwq_rd_ptr=rd;
        d.fbwq_swap_now=push&&swap;d.fbwq_stage_drain_now=push&&!swap;
        d.fbwq_req_addr=swap?address:(rng()&0x3fffffc);
        d.fbwq_stage_addr=swap?(rng()&0x3fffffc):address;
        d.fbwq_pop_now=pop;d.clk=0;d.eval();
        if(pop&&d.pending_valid&&d.pending_index==rd)++pop_pending;
        d.clk=1;d.eval();++cycles;
        if(soft||hard){fifo.clear();wr=rd=0;++resets;}
        else {
            if(pop){fifo.pop_front();rd=(rd+1)&15;++pops;if(!rd)++wraps;}
            if(push){fifo.push_back(address);wr=(wr+1)&15;++pushes;if(!wr)++wraps;}
            if(push&&pop)++simultaneous;
            if(fifo.size()==16)++full;
        }
        if(ring)++ring_resets;
        d.fbwq_swap_now=0;d.fbwq_stage_drain_now=0;d.fbwq_pop_now=0;
        d.soft_reset=0;d.reset_n=1;d.ring_reset=0;
    }
};
int main(int argc,char**argv) {
    Verilated::commandArgs(argc,argv);Sim s;
    s.tick(false,false,0,false,false,true);
    // Directed pending-only entry, pending commit/pop collision, and hash alias.
    s.tick(true,false,0x1040);s.probe(0x1040);s.probe(0x107c);s.probe(0x2000);
    // Toggle line bits0 and6 together: same XOR tag, different physical line.
    s.probe(0x1040^(1u<<6)^(1u<<12));
    s.tick(false,true,0);s.probe(0x1040);
    // Full queue, repeated wrap, alternating enqueue sources and legal simultaneous events.
    for(unsigned round=0;round<20;round++) {
        while(s.fifo.size()<16)s.tick(true,false,0x10000+uint32_t(s.fifo.size())*64,round&1);
        s.probe(0x103fc);s.tick(false,true,0);
        for(unsigned i=0;i<80;i++) {s.tick(true,true,0x20000+i*64,i&1);s.probe(0x20000+i*64);}
        while(!s.fifo.empty())s.tick(false,true,0);
    }
    for(unsigned n=0;n<150000;n++) {
        uint32_t a=(s.rng()&0x3fffffc);
        if(!s.fifo.empty()&&(n%3))a=s.fifo[s.rng()%s.fifo.size()]^(n&1?0:0x3c);
        s.probe(a);
        if(n%13==0)for(unsigned bit=0;bit<5;bit++)s.probe(a,1u<<bit);
        // Popped/on-deck addresses differ, arbitrary payload masks are irrelevant
        // to this conservative tag tracker; cache test verifies the byte effects.
        bool push=s.fifo.size()<16&&(s.rng()%3!=0);
        bool pop=!s.fifo.empty()&&(s.rng()%3!=0);
        bool reset=n%997==0;
        s.tick(push,pop,s.rng()&0x3fffffc,s.rng()&1,reset&&(n&1),reset&&!(n&1),n%337==0);
        // ring_reset is deliberately not wired into extracted tracker: actual
        // ring reset does not discard the framebuffer write queue.
    }
    while(!s.fifo.empty())s.tick(false,true,0);
    s.probe(0);
    s.require(s.pop_pending&&s.aliases&&s.full&&s.simultaneous&&s.wraps&&s.guards&&s.bypasses,"required coverage");
    std::printf("{\"pass\":true,\"cycles\":%llu,\"checks\":%llu,\"pushes\":%llu,\"pops\":%llu,\"simultaneous_push_pop\":%llu,\"pointer_wraps\":%llu,\"safe_queries\":%llu,\"same_word_queries\":%llu,\"same_line_queries\":%llu,\"conservative_hash_aliases\":%llu,\"holding_guard_queries\":%llu,\"pending_commit_pop\":%llu,\"full_queue_cycles\":%llu,\"resets\":%llu,\"ring_resets_preserved\":%llu}\n",
        (unsigned long long)s.cycles,(unsigned long long)s.checks,(unsigned long long)s.pushes,(unsigned long long)s.pops,(unsigned long long)s.simultaneous,(unsigned long long)s.wraps,(unsigned long long)s.bypasses,(unsigned long long)s.same_word,(unsigned long long)s.same_line,(unsigned long long)s.aliases,(unsigned long long)s.guards,(unsigned long long)s.pop_pending,(unsigned long long)s.full,(unsigned long long)s.resets,(unsigned long long)s.ring_resets);
}
