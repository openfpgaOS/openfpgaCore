#include "Vdma_tb.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>

static void require(bool ok, const char *why) { if (!ok) throw std::runtime_error(why); }
static uint32_t data_at(uint32_t addr) {
    uint32_t v = addr ^ 0x7a419b3du;
    v ^= v >> 16; v *= 0x85ebca6bu; v ^= v >> 13;
    return v;
}
struct Write { unsigned bank, index; uint32_t data; };
struct Sim {
    Vdma_tb d;
    std::mt19937 rng{0x4d32444d};
    std::array<std::array<uint32_t,256>,2> ram{};
    std::vector<Write> writes;
    uint64_t cycles=0, bursts=0, beats=0, jobs=0, error_jobs=0, aborts=0, resets=0;
    std::array<uint64_t,4> grants{};
    unsigned cpu_gap=0, mesh_gap=0, max_cpu_gap=0, max_mesh_gap=0;
    bool active=false, held=false, previous_ar_stall=false;
    uint32_t read_addr=0, stalled_addr=0;
    unsigned read_beat=0, expected_beats=0, actual_beats=0, stalled_len=0;
    int next_fault=0, fault=0, error_at=0, ar_hold=0, r_hold=0;

    Sim() { d.clk=0; d.reset_n=0; d.abort_i=0; d.desc_valid=0; d.desc_bank=0;
        d.desc_addr=0; d.desc_dst=0; d.desc_words=0; d.desc_commit=0;
        d.bank_release=0; d.through_arbiter=0; d.background=0;
        d.arready=0; d.rvalid=0; d.rdata=0; d.rresp=0; d.rlast=0;
        d.eval(); reset(); }
    void reset() {
        d.reset_n=0; d.abort_i=0; d.desc_valid=0; d.bank_release=0;
        active=held=previous_ar_stall=false; next_fault=0; ar_hold=r_hold=0;
        tick(); tick(); d.reset_n=1; tick(); ++resets;
        require(!d.bank_owned && !d.bank_ready && !d.fetch_busy, "reset did not clear ownership");
    }
    bool tick() {
        d.arready = !active && ar_hold==0 && (rng()%4 != 0);
        if (ar_hold>0) --ar_hold;
        if (!held && active && r_hold==0 && rng()%3 != 0) {
            held=true; d.rdata=data_at(read_addr+4*read_beat);
            d.rlast=(read_beat+1==actual_beats);
            d.rresp=(fault==1 && int(read_beat)==error_at) ? 2 : 0;
        }
        if (r_hold>0) --r_hold;
        d.rvalid=held;
        d.clk=0; d.eval();
        const bool df=d.desc_valid && d.desc_ready;
        const bool af=d.arvalid && d.arready;
        const bool rf=d.rvalid && d.rready;
        const uint32_t aa=d.araddr;
        const unsigned al=d.arlen;
        const bool last=d.rlast;
        if (d.reset_n) {
            if (previous_ar_stall && !(d.arvalid && aa==stalled_addr && al==stalled_len)) {
                std::fprintf(stderr,"AR failure cycle=%llu jobs=%llu aborts=%llu integrated=%u old=%08x/%u new=%08x/%u valid=%u busy=%u reset=%u\n",
                    (unsigned long long)cycles,(unsigned long long)jobs,(unsigned long long)aborts,
                    d.through_arbiter,stalled_addr,stalled_len,aa,al,d.arvalid,d.fetch_busy,d.reset_n);
                require(false,"AR changed/withdrew while stalled (including abort)");
            }
            previous_ar_stall=d.arvalid && !d.arready;
            stalled_addr=aa; stalled_len=al;
            if (d.wr_en) {
                require(!d.abort_i, "write occurred during abort");
                require((d.bank_owned & (1u<<d.wr_bank))!=0, "write without bank ownership");
                require((d.bank_ready & (1u<<d.wr_bank))==0, "write overwrote processing bank");
                require(rf && d.rresp==0, "write without successful R handshake");
                ram[d.wr_bank][d.wr_index]=d.wr_data;
                writes.push_back({unsigned(d.wr_bank),unsigned(d.wr_index),d.wr_data});
            }
            if (af) {
                require(!active, "more than one outstanding read");
                require((aa&3)==0 && al<8 && (aa&31)+4*(al+1)<=32,
                        "burst unaligned, over eight beats, or crosses 32-byte boundary");
                ++bursts;
                const unsigned g=d.through_arbiter ? d.dbg_grant : 2;
                if (d.through_arbiter) ++grants[g];
                if (d.through_arbiter && (d.background&2)) {
                    if (g==1) cpu_gap=0; else ++cpu_gap;
                    if (cpu_gap>max_cpu_gap) max_cpu_gap=cpu_gap;
                    require(cpu_gap<=9, "CPU starvation guard exceeded bound");
                }
                if (d.through_arbiter && d.dma_ar_wait) {
                    if (g==2) mesh_gap=0; else ++mesh_gap;
                    if (mesh_gap>max_mesh_gap) max_mesh_gap=mesh_gap;
                    require(mesh_gap<=14, "M2 starvation guard exceeded bound");
                }
                active=true; read_addr=aa; read_beat=0; expected_beats=al+1;
                fault=next_fault; next_fault=0;
                actual_beats=expected_beats;
                if (fault==2) actual_beats=1;
                if (fault==3) ++actual_beats;
                error_at=rng()%expected_beats;
            }
            if (rf) { require(active, "unsolicited read response"); ++beats;
                held=false; ++read_beat; if(last) active=false; }
        } else { active=held=previous_ar_stall=false; }
        if (!d.dma_ar_wait) mesh_gap=0;
        d.clk=1; d.eval(); d.clk=0; d.eval(); ++cycles;
        return df;
    }
    void until_idle(unsigned bound=30000) {
        while (d.fetch_busy && bound--) tick();
        require(!d.fetch_busy, "fetch timeout");
    }
    void submit(unsigned bank,uint32_t addr,unsigned dst,unsigned words,bool commit) {
        d.desc_bank=bank; d.desc_addr=addr; d.desc_dst=dst;
        d.desc_words=words; d.desc_commit=commit; d.desc_valid=1;
        unsigned bound=30000; bool accepted=false;
        while (!accepted && bound--) accepted=tick();
        require(accepted,"descriptor acceptance timeout"); d.desc_valid=0;
    }
    void release(unsigned mask) {
        d.bank_release=mask; tick(); d.bank_release=0;
        require(!(d.bank_owned&mask),"ready bank release failed");
    }
    void normal(unsigned bank,uint32_t addr,unsigned dst,unsigned words,bool commit) {
        const size_t start=writes.size(); submit(bank,addr,dst,words,commit);
        // An accidental early release must not free an in-progress bank.
        if (d.fetch_busy) { d.bank_release=1u<<bank; tick(); d.bank_release=0;
            require(d.bank_owned&(1u<<bank),"early release freed filling bank"); }
        until_idle();
        require(!d.bank_error,"unexpected DMA error");
        require(bool(d.bank_ready&(1u<<bank))==commit,"commit visibility mismatch");
        require(writes.size()-start==words,"incorrect number of bank writes");
        for(unsigned i=0;i<words;++i) {
            const auto &w=writes[start+i];
            require(w.bank==bank && w.index==dst+i && w.data==data_at(addr+4*i),
                    "bank write address/data mismatch");
            require(ram[bank][dst+i]==data_at(addr+4*i),"bank contents mismatch");
        }
        if(commit) ++jobs;
    }
    void check_ready_stall(unsigned bank) {
        d.desc_bank=bank; d.desc_addr=0x44550000; d.desc_words=1;
        d.desc_dst=0; d.desc_commit=1; d.desc_valid=1;
        const auto w=writes.size();
        for(int i=0;i<13;++i) require(!tick(),"descriptor overwrote ready/processing bank");
        d.desc_valid=0; require(writes.size()==w,"ready bank changed while held");
    }
};
int main(int argc,char **argv) {
    Verilated::commandArgs(argc,argv);
    try {
        Sim s;
        // Full range and segment ownership, with the other bank held in use.
        for(unsigned job=0;job<1500;++job) {
            const unsigned bank=job&1;
            const unsigned words=1+s.rng()%256;
            const unsigned dst=s.rng()%(257-words);
            const uint32_t addr=0x01000000+(s.rng()%0x10000)*4;
            s.normal(bank,addr,dst,words,true);
            s.check_ready_stall(bank);
            if(job>0) s.release(1u<<(bank^1));
        }
        s.release(3);
        for(unsigned job=0;job<400;++job) {
            const unsigned b=job&1;
            s.normal(b,0x01230ffcu+job*1024,0,128,false);
            s.normal(b,0x02234004u+job*1024,128,64,false);
            s.normal(b,0x03234010u+job*1024,192,64,true);
            s.release(1u<<b);
        }
        // Address range endpoint and burst boundary corner cases.
        s.normal(0,0xfffffc00u,0,256,true); s.release(1);
        const struct {uint32_t a;unsigned dst,n;} invalid[] = {
            {0x1001,0,4},{0x1000,0,0},{0x1000,0,257},{0x1000,255,2},
            {0xfffffffc,0,2},{0x1000,250,511}
        };
        for(const auto &v:invalid) {
            const auto old=s.bursts; s.submit(0,v.a,v.dst,v.n,true); s.until_idle();
            require(s.d.bank_ready==1 && s.d.bank_error==1,"invalid descriptor not quarantined");
            require(s.bursts==old,"invalid descriptor issued external read");
            s.check_ready_stall(0); s.release(1); ++s.error_jobs;
        }
        // Bus error, short burst, and missing/late RLAST must invalidate rather
        // than publish a partial bank; all responses drain before its reuse.
        for(unsigned i=0;i<300;++i) {
            s.next_fault=1+i%3;
            s.submit(i&1,0x03456000,16,64,true); s.until_idle();
            require(s.d.bank_error==(1u<<(i&1)),"read error not reported");
            require(!s.active && !s.held,"error did not drain outstanding burst");
            s.check_ready_stall(i&1); s.release(1u<<(i&1)); ++s.error_jobs;
            s.normal(i&1,0x04567000+i*16,0,8,true); s.release(1u<<(i&1));
        }
        // Soft abort before AR, during a stalled AR, during R, between bursts,
        // and with both ready banks. No stale response can touch a fresh bank.
        for(unsigned i=0;i<600;++i) {
            s.ar_hold=(i%3==0)?15:0; s.r_hold=(i%3==1)?12:0;
            s.submit(i&1,0x01000000+i*1024,0,256,true);
            for(unsigned j=0;j<i%31;++j) s.tick();
            const auto old=s.writes.size(); s.d.abort_i=1; s.tick(); s.d.abort_i=0;
            s.until_idle(); ++s.aborts;
            require(s.writes.size()==old,"cancelled read wrote bank");
            require(!s.d.bank_owned && !s.d.bank_ready && !s.d.bank_error,
                    "abort did not invalidate ownership");
            s.normal(i&1,0x05456004+i*32,0,13,true); s.release(1u<<(i&1));
        }
        for(unsigned i=0;i<100;++i) {
            s.submit(i&1,0x01000000,0,256,true);
            for(unsigned j=0;j<i%29;++j) s.tick();
            s.reset(); s.normal(0,0x02345000,0,8,true); s.release(1);
        }
        // Both processing and completed-prefetch ownership are cancelled even
        // when no read is outstanding; a held abort also blocks fresh jobs.
        s.normal(0,0x02000000,0,8,true);
        s.normal(1,0x03000000,0,8,true);
        s.d.abort_i=1;
        for(unsigned i=0;i<8;++i) { s.tick(); require(!s.d.desc_ready,"held abort accepted job"); }
        s.d.abort_i=0; s.tick(); ++s.aborts;
        require(!s.d.bank_owned && !s.d.bank_ready,"idle abort retained a ready bank");
        // Interleave segments in both uncommitted banks. Publication order is
        // intentionally the scheduler's responsibility, not inferred by DMA.
        s.normal(0,0x01000000,0,128,false);
        s.normal(1,0x02000000,0,128,false);
        s.normal(1,0x02100000,128,128,true);
        s.normal(0,0x01100000,128,128,true);
        s.release(3);
        // Real production arbiter under sustained GPU+CPU and sparse or
        // continuous audio. Checks transaction fairness, not physical deadlines.
        s.reset(); s.d.through_arbiter=1;
        for(unsigned job=0;job<120;++job) {
            s.d.background=job<60?3:7;
            s.normal(job&1,0x01000000+job*1024,0,256,true);
            s.release(1u<<(job&1));
        }
        require(s.grants[0]>0 && s.grants[1]>0 && s.grants[3]>0,
                "background masters were not exercised");
        std::printf("{\"pass\":true,\"jobs\":%llu,\"error_jobs\":%llu,\"aborts\":%llu,"
                    "\"shared_resets\":%llu,\"bursts\":%llu,\"beats\":%llu,"
                    "\"cycles\":%llu,\"max_cpu_other_grants\":%u,"
                    "\"max_mesh_other_grants\":%u,\"arbiter_grants\":[%llu,%llu,%llu,%llu]}\n",
                    (unsigned long long)s.jobs,(unsigned long long)s.error_jobs,
                    (unsigned long long)s.aborts,(unsigned long long)s.resets,
                    (unsigned long long)s.bursts,(unsigned long long)s.beats,
                    (unsigned long long)s.cycles,s.max_cpu_gap,s.max_mesh_gap,
                    (unsigned long long)s.grants[0],(unsigned long long)s.grants[1],
                    (unsigned long long)s.grants[2],(unsigned long long)s.grants[3]);
        return 0;
    } catch (const std::exception &e) { std::fprintf(stderr,"FAIL: %s\n",e.what()); return 1; }
}
