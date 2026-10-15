#include "Vgpu_color_depth_cache.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <deque>
#include <map>
#include <random>
#include <vector>

struct Word { uint32_t address, data; unsigned mask; };
struct Write { std::vector<Word> words; uint64_t due; };
struct Read { uint32_t address; unsigned left; uint64_t due; };
struct Expected { uint32_t data; bool last; };
struct Sim {
    Vgpu_color_depth_cache d;
    std::mt19937 rng{0x814dae29};
    std::map<uint32_t,uint32_t> memory, golden;
    std::deque<Write> posted;
    std::deque<Expected> expected;
    std::vector<Word> collecting;
    Read read{};
    uint32_t down_address=0, up_address=0;
    unsigned down_left=0, up_left=0;
    uint64_t cycle=0, checks=0, reads=0, writes=0, downstream_reads=0;
    uint64_t ar_while_posted=0, same_line_posted_ar=0, posted_same_line_wait=0;
    uint64_t priority_checks=0, blocked_write_cycles=0, partial_writes=0, flushes=0;
    uint64_t cache_reset_pending=0, delay_override=0;
    bool random_stalls=true;
    bool faw=false,fw=false,far=false,fb=false;
    uint64_t ar_priority_cycles=0, concurrent_cases=0, preferred_cases=0, fallback_cases=0;
    static bool prefer(uint32_t a,unsigned alen,uint32_t w,unsigned wlen) {
        return !(a&3)&&!(w&3)&&((a&63)/4+alen<16)&&((w&63)/4+wlen<16)&&(a/64!=w/64);
    }
    void require(bool yes,const char *what) {
        ++checks;if(!yes){std::fprintf(stderr,"FAIL cycle=%llu %s\n",(unsigned long long)cycle,what);std::exit(1);}
    }
    static uint32_t initial(uint32_t a){ return (a*0x9e3779b9u)^0x81654321u; }
    uint32_t get(std::map<uint32_t,uint32_t>& m,uint32_t a) {
        auto it=m.find(a);return it==m.end()?initial(a):it->second;
    }
    void merge(std::map<uint32_t,uint32_t>& m,const Word& w) {
        uint32_t value=get(m,w.address);
        for(unsigned b=0;b<4;b++) if(w.mask&(1u<<b)) value=(value&~(255u<<(8*b)))|(w.data&(255u<<(8*b)));
        m[w.address]=value;
    }
    bool same_posted(uint32_t a) {
        for(const auto& w:posted)for(const auto& b:w.words)if((b.address>>6)==(a>>6))return true;
        return false;
    }
    void tick() {
        d.clk=0; d.m_bvalid=0;d.m_rvalid=0;d.m_rlast=0;
        // Downstream write visibility occurs only with its in-order B. Reads
        // may overtake other posted writes, exactly the hazard being tested.
        if(!posted.empty()&&posted.front().due<=cycle) {
            for(const auto& w:posted.front().words)merge(memory,w);
            posted.pop_front();d.m_bvalid=1;
        }
        d.m_awready=!down_left && (!random_stalls || (rng()%4)!=0);
        d.m_wready=down_left && (!random_stalls || (rng()%5)>1);
        d.m_arready=!read.left && (!random_stalls || (rng()%4)!=0);
        if(read.left && read.due<=cycle && (!random_stalls || (rng()%4)!=0)) {
            d.m_rvalid=1;d.m_rdata=get(memory,read.address);d.m_rlast=read.left==1;
        }
        d.eval();
        faw=d.s_awvalid&&d.s_awready;fw=d.s_wvalid&&d.s_wready;far=d.s_arvalid&&d.s_arready;fb=d.s_bvalid;
        require(!d.protocol_error,"cache protocol_error");
        if(d.s_awvalid && d.s_arvalid) {
            require(!(d.s_awready&&d.s_arready),"dual AW/AR acceptance");
            if(prefer(d.s_araddr,d.s_arlen,d.s_awaddr,d.s_awlen)) {
                ++ar_priority_cycles;require(!d.s_awready,"guarded disjoint read lost priority");
            } else {++priority_checks;require(!d.s_arready,"unguarded read passed pending AW");}
        }
        if(d.s_wvalid && !d.s_wready) ++blocked_write_cycles;
        if(d.s_arvalid && d.s_arready) {
            require(expected.empty(),"overlapping upstream reads");
            for(unsigned i=0;i<=d.s_arlen;i++)expected.push_back({get(golden,d.s_araddr+4*i),i==d.s_arlen});
            ++reads;
            if(!posted.empty())++ar_while_posted;
            if(same_posted(d.s_araddr))++same_line_posted_ar;
        }
        if(d.s_rvalid) {
            require(!expected.empty(),"unexpected upstream response");
            auto e=expected.front();expected.pop_front();
            if(d.s_rdata!=e.data) {
                std::fprintf(stderr,"read got=%08x expected=%08x\n",d.s_rdata,e.data);
                require(false,"read observed stale/mis-masked word");
            }
            require(bool(d.s_rlast)==e.last,"upstream RLAST");
        }
        if(d.s_awvalid && d.s_awready) {
            require(!up_left,"overlapping upstream AW");up_address=d.s_awaddr;up_left=d.s_awlen+1;++writes;
        }
        if(d.s_wvalid && d.s_wready) {
            require(up_left,"upstream W without AW");
            require(bool(d.s_wlast)==(up_left==1),"upstream WLAST");
            if(d.s_wstrb!=15)++partial_writes;
            merge(golden,{up_address,d.s_wdata,unsigned(d.s_wstrb)});up_address+=4;--up_left;
        }
        if(d.m_arvalid && d.m_arready) {
            require(!read.left,"overlapping downstream read");
            require(!same_posted(d.m_araddr),"read passed uncommitted same-line downstream write");
            read={d.m_araddr,unsigned(d.m_arlen)+1,cycle+2+(rng()%11)};++downstream_reads;
        }
        if(d.s_arvalid && same_posted(d.s_araddr) && !d.m_arvalid)++posted_same_line_wait;
        if(d.m_awvalid && d.m_awready) {
            require(!down_left,"overlapping downstream AW");down_address=d.m_awaddr;down_left=d.m_awlen+1;collecting.clear();
        }
        if(d.m_wvalid && d.m_wready) {
            require(down_left,"downstream W without AW");
            require(bool(d.m_wlast)==(down_left==1),"downstream WLAST");
            collecting.push_back({down_address,d.m_wdata,unsigned(d.m_wstrb)});down_address+=4;
            if(!--down_left)posted.push_back({collecting,cycle+(delay_override?delay_override:30+rng()%140)});
        }
        if(d.m_rvalid) {read.address+=4;--read.left;read.due=cycle+1;}
        d.clk=1;d.eval();++cycle;
        require(cycle<30000000,"test timeout");
    }
    template<class F> void until(F pred,const char* why) {
        for(unsigned n=0;n<1000000;n++){if(pred())return;tick();}require(false,why);
    }
    void init() {
        d.reset_n=0;d.bus_reset_n=0;d.flush_req=0;d.write_no_allocate=0;
        d.s_arvalid=0;d.s_awvalid=0;d.s_wvalid=0;
        for(unsigned n=0;n<3;n++)tick();
        d.reset_n=1;d.bus_reset_n=1;until([&]{return d.s_awready;},"cache init");
    }
    void write(uint32_t address,unsigned count,unsigned salt,bool hold_read=false,uint32_t read_address=0,unsigned read_count=1) {
        d.s_awaddr=address;d.s_awlen=count-1;d.s_awvalid=1;
        if(hold_read){d.s_araddr=read_address;d.s_arlen=read_count-1;d.s_arvalid=1;}
        for(;;){tick();if(faw)break;}
        d.s_awvalid=0;
        for(unsigned i=0;i<count;i++) {
            if(random_stalls)for(unsigned n=rng()%4;n;n--)tick();
            d.s_wvalid=1;d.s_wdata=(salt+i*0x7f15aa23u)^rng();
            d.s_wstrb=((salt+i)%7==0)?0:((salt+i)%5==0?15:1u<<((salt+i)&3));
            d.s_wlast=i+1==count;
            for(;;){tick();if(fw)break;}
            d.s_wvalid=0;
        }
        until([&]{return d.s_bvalid;},"upstream B");tick();
        if(hold_read) {
            // B and AR may coincide; preserve AR until its handshake rather
            // than relying on the registered B indication as an AR event.
            while(reads==read_start_marker){d.clk=0;d.eval();tick();}
            d.s_arvalid=0;until([&]{return expected.empty();},"overlap response");
        }
    }
    uint64_t read_start_marker=0;
    void read_words(uint32_t address,unsigned count=1) {
        d.s_araddr=address;d.s_arlen=count-1;d.s_arvalid=1;
        for(;;){tick();if(far)break;}
        d.s_arvalid=0;until([&]{return expected.empty();},"read completion");
    }
    void concurrent(uint32_t wa,unsigned wn,uint32_t ra,unsigned rn,unsigned salt) {
        ++concurrent_cases;
        bool rp=prefer(ra,rn-1,wa,wn-1);
        if(rp)++preferred_cases;else++fallback_cases;
        d.s_awaddr=wa;d.s_awlen=wn-1;d.s_awvalid=1;
        d.s_araddr=ra;d.s_arlen=rn-1;d.s_arvalid=1;d.s_wvalid=0;
        bool aw=false,ar=false,b=false;unsigned wi=0,gap=3;
        for(unsigned n=0;n<1000000;n++) {
            if(aw&&wi<wn&&!d.s_wvalid) {
                if(gap)--gap;
                else {d.s_wvalid=1;d.s_wdata=rng()^salt;d.s_wstrb=(salt+wi)%16;d.s_wlast=wi+1==wn;}
            }
            tick();
            if(far) {require(!ar,"duplicate concurrent AR");if(!aw)require(rp,"read first without guarded independence");
                ar=true;d.s_arvalid=0;d.s_araddr=0x3fffffc;d.s_arlen=255;}
            if(faw) {require(!aw,"duplicate concurrent AW");if(!ar)require(!rp,"write first despite guarded read");
                aw=true;d.s_awvalid=0;d.s_awaddr=0x3ffff00;d.s_awlen=127;}
            if(fw) {++wi;d.s_wvalid=0;gap=rng()%6;}
            b|=fb;
            if(aw&&ar&&b&&wi==wn&&expected.empty())return;
        }
        require(false,"concurrent transaction timeout");
    }
    void guard_only(uint32_t wa,unsigned wl,uint32_t ra,unsigned rl) {
        // Unsupported alignment is checked combinationally and never issued.
        d.s_awaddr=wa;d.s_awlen=wl;d.s_awvalid=1;
        d.s_araddr=ra;d.s_arlen=rl;d.s_arvalid=1;d.clk=0;d.eval();
        require(!prefer(ra,rl,wa,wl),"invalid guard-only vector");
        require(!d.s_arready&&d.s_awready,"conservative guard did not preserve AW");
        d.s_awvalid=d.s_arvalid=0;d.eval();
    }
    void flush() {
        ++flushes;d.flush_req=1;
        until([&]{return d.flush_done;},"flush completion");
        require(posted.empty()&&!down_left,"flush_done before downstream completion");
        for(const auto& [a,v]:golden)require(get(memory,a)==v,"flush memory mismatch");
        d.flush_req=0;tick();until([&]{return d.s_awready;},"flush return idle");
    }
};

int main(int argc,char**argv) {
    Verilated::commandArgs(argc,argv);Sim s;s.init();
    // Empty cache bypass posts a burst, then same-line early-restart must
    // merge partial masks only after downstream visibility. Cross64 boundary.
    s.delay_override=500;
    s.write(0x103c,8,1);s.read_words(0x103c,8);
    s.write(0x2000,8,3);s.read_words(0x8000,4);s.read_words(0x2000,8);
    s.delay_override=0;
    // AW has strict priority, and every accepted write beat is committed to
    // cache RAM before the parked overlapping read may enter.
    for(unsigned i=0;i<70;i++){
        s.read_start_marker=s.reads;
        s.write(0x3040+(i%8)*4,1+(i%7),i+4,true,0x3040,16);
    }
    // Distinct-line reads may precede unaccepted writes. Pending AW stays
    // stable while AR read lookup/fill executes; inactive ports are poisoned
    // immediately after acceptance to verify selected-request capture.
    for(unsigned i=0;i<500;i++) {
        uint32_t wa=0x70000+(i%5)*0x4000+(i%8)*4;
        uint32_t ra=0x70000+((i+2)%5)*0x4000+((i+3)%8)*4;
        s.concurrent(wa,1+i%8,ra,1+(i+2)%8,i+101);
        s.read_words(wa,1+i%8);
    }
    // Complete line ranges, not just starting words, control arbitration.
    s.concurrent(0xa003c,2,0xa1000,4,999); // write crosses a line
    s.concurrent(0xa2000,2,0xa303c,2,998); // read crosses a line
    s.concurrent(0xa4000,256,0xa6000,1,997); // maximum AWLEN
    s.concurrent(0xa8000,1,0xaa000,256,996); // maximum ARLEN
    s.concurrent(0xac03c,8,0xac040,8,995); // crossing overlap
    s.concurrent(0xae000,8,0xae000,16,994); // same line masks
    s.guard_only(0xb0001,0,0xb1000,0);
    s.guard_only(0xb0000,0,0xb1002,0);
    // Congruent lines force real dirty evictions; random masked bursts and
    // read windows cover both hot ways, partial valid words and refill merge.
    for(unsigned i=0;i<2400;i++){
        uint32_t a=0x10000+(s.rng()%7)*0x4000+(s.rng()%16)*4;
        unsigned count=1+s.rng()%8;
        s.write(a,count,i+11);
        if((i%3)!=0)s.read_words(a,count);
        if((i%7)==0)s.read_words(0x10000+(s.rng()%7)*0x4000,16);
        if((i%101)==0)s.flush();
    }
    s.flush();
    // Producer-local reset invalidates cache contents but must retain
    // downstream posted tracking. The bus itself is not reset.
    s.delay_override=4000;s.write(0x50000,8,17);
    s.require(!s.posted.empty(),"reset scenario did not post a write");
    s.d.reset_n=0;s.tick();s.d.reset_n=1;++s.cache_reset_pending;
    s.until([&]{return s.d.s_awready;},"local reset init");
    s.read_words(0x50000,8);s.delay_override=0;s.flush();
    s.require(s.ar_while_posted>0,"no upstream read overlapped posted write");
    s.require(s.same_line_posted_ar>0,"no same-line posted hazard exercised");
    s.require(s.priority_checks>0&&s.blocked_write_cycles>0&&s.partial_writes>0,"missing directed coverage");
    s.require(s.preferred_cases==500&&s.fallback_cases==6&&s.ar_priority_cycles>0,"priority coverage");
    std::printf("{\"pass\":true,\"cycles\":%llu,\"checks\":%llu,\"reads\":%llu,\"writes\":%llu,\"downstream_reads\":%llu,\"ar_while_posted\":%llu,\"same_line_posted_ar\":%llu,\"aw_priority_cycles\":%llu,\"write_stall_cycles\":%llu,\"partial_writes\":%llu,\"flushes\":%llu,\"local_reset_with_posted_write\":%llu,\"guarded_ar_priority_cycles\":%llu,\"concurrent_cases\":%llu,\"read_first_cases\":%llu,\"write_first_cases\":%llu}\n",
      (unsigned long long)s.cycle,(unsigned long long)s.checks,(unsigned long long)s.reads,(unsigned long long)s.writes,(unsigned long long)s.downstream_reads,(unsigned long long)s.ar_while_posted,(unsigned long long)s.same_line_posted_ar,(unsigned long long)s.priority_checks,(unsigned long long)s.blocked_write_cycles,(unsigned long long)s.partial_writes,(unsigned long long)s.flushes,(unsigned long long)s.cache_reset_pending,(unsigned long long)s.ar_priority_cycles,(unsigned long long)s.concurrent_cases,(unsigned long long)s.preferred_cases,(unsigned long long)s.fallback_cases);
}
