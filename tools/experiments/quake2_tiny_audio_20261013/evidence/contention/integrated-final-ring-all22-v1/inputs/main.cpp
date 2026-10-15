#include "Vtb_contention.h"
#include <verilated.h>
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <deque>
#include <memory>
#include <vector>

static void require(bool ok, const char *why) {
    if (!ok) { std::fprintf(stderr,"FAIL: %s\n",why); std::exit(1); }
}
struct Result {
    uint64_t cycles=0, produced=0, consumed=0, underruns=0;
    uint64_t reads=0, beats=0, source_reads=0, source_beats=0;
    uint64_t stream_reads=0, stream_beats=0, stream_source_reads=0;
    uint64_t gpu_r=0, gpu_w=0, cpu_r=0, cpu_w=0;
    unsigned minimum=1024, initial_level=0, final_level=0;
    std::vector<uint32_t> pcm;
};
struct Bench {
    std::unique_ptr<Vtb_contention> d = std::make_unique<Vtb_contention>();
    std::deque<uint32_t> fifo;
    Result r;
    uint64_t phase=0, cycles=0;
    unsigned hz, gpu_wleft=0, cpu_wleft=0;
    bool gpu_rd=false,cpu_rd=false,gpu_aw=false,cpu_aw=false,cpu_next_write=false;
    bool load=false,consume=false,measure=false;
    Bench(unsigned mhz,unsigned latency,bool stalls):hz(mhz*1000000) {
        d->reset_n=0; d->mixer_enable=0; d->voice_wr=0; d->cache_enable=0;
        d->voice_field=0;d->voice_sel=0;d->voice_sel_rd=0;d->voice_wdata=0;
        d->mem_latency=latency;d->mem_beat_gap=1;d->periodic_stalls=stalls;
        d->m0_araddr=0x100000;d->m0_arlen=7;
        d->m0_awaddr=0x200000;d->m0_awlen=7;d->m0_wdata=0x12345678;d->m0_wstrb=15;
        d->m1_araddr=0x300000;d->m1_arlen=7;d->m1_rready=1;
        d->m1_awaddr=0x400000;d->m1_awlen=0;d->m1_wdata=0xABCDEF01;d->m1_wstrb=15;
        for (int i=0;i<10;i++) tick();
        d->reset_n=1;
        for (int i=0;i<10;i++) tick();
    }
    void tick() {
        d->m0_arvalid=load&&!gpu_rd;
        d->m1_arvalid=load&&!cpu_rd&&!cpu_next_write;
        d->m0_awvalid=load&&!gpu_aw;
        d->m1_awvalid=load&&!cpu_aw&&cpu_next_write;
        d->m0_wvalid=gpu_wleft!=0;d->m0_wlast=gpu_wleft==1;
        d->m1_wvalid=cpu_wleft!=0;d->m1_wlast=cpu_wleft==1;
        d->fifo_level=fifo.size();d->clk=0;d->eval();
        bool a0=d->m0_arvalid&&d->m0_arready,a1=d->m1_arvalid&&d->m1_arready;
        bool ar=d->audio_ar_fire,br=d->audio_r_fire;
        bool sr=d->source_ar_fire,sb=d->source_r_fire;
        bool stream=d->fetch_voice==31;
        if (sr&&stream) require(!d->fetch_cacheable,"stream became cacheable");
        if (ar&&stream) require(d->audio_arlen<=1,"stream used speculative line fill");
        bool aw0=d->m0_awvalid&&d->m0_awready,aw1=d->m1_awvalid&&d->m1_awready;
        bool w0=d->m0_wvalid&&d->m0_wready,w1=d->m1_wvalid&&d->m1_wready;
        bool b0=d->m0_bvalid,b1=d->m1_bvalid;
        bool r0=d->m0_rvalid,r1=d->m1_rvalid&&d->m1_rready;
        bool end0=r0&&d->m0_rlast,end1=r1&&d->m1_rlast;
        bool sw=d->sample_wr;uint32_t data=d->sample_data;
        d->clk=1;d->eval();++cycles;
        if (!d->reset_n) return;
        if (a0) gpu_rd=true;if (a1) cpu_rd=true;
        if (end0) gpu_rd=false;if (end1) {cpu_rd=false;cpu_next_write=true;}
        if (aw0) {gpu_aw=true;gpu_wleft=8;}
        if (aw1) {cpu_aw=true;cpu_wleft=1;}
        if (w0) --gpu_wleft;if (w1) --cpu_wleft;
        if (b0) gpu_aw=false;if (b1) {cpu_aw=false;cpu_next_write=false;}
        if (sw) {
            require(fifo.size()<1024,"audio FIFO overflow");
            fifo.push_back(data);
            r.pcm.push_back(data);
        }
        bool pop=false,under=false;
        if (consume) {
            phase+=48000;
            if (phase>=hz) {
                phase-=hz;pop=true;
                if (fifo.empty()) under=true;else fifo.pop_front();
            }
        }
        if (measure) {
            ++r.cycles;r.produced+=sw;r.consumed+=pop;r.underruns+=under;
            r.reads+=ar;r.beats+=br;r.source_reads+=sr;r.source_beats+=sb;
            r.stream_reads+=ar&&stream;r.stream_beats+=br&&stream;
            r.stream_source_reads+=sr&&stream;r.gpu_r+=r0;r.gpu_w+=w0;
            r.cpu_r+=r1;r.cpu_w+=w1;r.minimum=std::min(r.minimum,unsigned(fifo.size()));
        }
    }
    void write(unsigned v,unsigned f,uint32_t value) {
        d->voice_sel=v;d->voice_field=f;d->voice_wdata=value;
        unsigned limit=10000;
        while (!d->voice_wr_ready&&--limit) tick();
        require(limit!=0,"MMIO never ready");
        d->voice_wr=1;tick();d->voice_wr=0;tick();
    }
    void program(unsigned voices, unsigned workload) {
        for(unsigned v=0;v<32;v++) write(v,3,0);
        for(unsigned j=0;j<voices;j++) {
            unsigned v=(j==voices-1)?31:j;
            const bool stream=v==31, stereo=stream||workload==1;
            // Quake2-like case: native-rate mono SFX and live stereo voice31.
            // Adversarial case: all stereo, uneven short loops and up to2x
            // pitch create frequent discontinuities and line replacements.
            unsigned end=stream?65536:workload==1?(17+2*(v%7)):1024;
            unsigned rate=stream?60211:workload==1?(v%3==0?131071:65536):workload==2?30105:(v&1?30105:15053);
            unsigned start=stream||workload!=1?0:1+v%3;
            write(v,0,v*4096);write(v,1,end);write(v,2,rate);
            write(v,4,0);write(v,5,0);write(v,6,0);
            write(v,7,end);write(v,8,start);
            // Low adversarial volumes avoid saturation hiding PCM differences.
            write(v,9,workload==1?0x070b:0xc0ff);write(v,10,stream?4:8);
            write(v,11,stream?32768:0);write(v,3,stream?15:(stereo?7:5));
        }
        for (unsigned i=0;i<300;i++) tick();
        d->mixer_enable=voices!=0;
    }
    Result run(unsigned voices, unsigned workload, bool busy, bool cache) {
        program(voices,workload);load=busy;d->cache_enable=cache;
        if(voices) {
            uint64_t end=cycles+10000000;
            while (fifo.size()<700&&cycles<end) tick();
            require(fifo.size()>=700,"audio prefill exceeded ten million cycles");
        }
        consume=voices!=0;measure=true;r.initial_level=fifo.size();
        const uint64_t duration=voices?uint64_t(hz)*4/10:hz/10;
        for (uint64_t i=0;i<duration;i++) tick();
        r.final_level=fifo.size();
        // Conservation includes prefill and missing consumer samples.
        require(r.final_level==r.initial_level+r.produced-r.consumed+r.underruns,
                "FIFO accounting mismatch");
        return r;
    }
};

static size_t compare(const Result& a,const Result& b) {
    size_t n=std::min(a.pcm.size(),b.pcm.size());
    require(n>384,"insufficient PCM sequence");
    if (!std::equal(a.pcm.begin(),a.pcm.begin()+n,b.pcm.begin())) {
        for(size_t i=0;i<n;i++) if(a.pcm[i]!=b.pcm[i]) {
            std::fprintf(stderr,"PCM mismatch at%zu: %08x != %08x\n",i,a.pcm[i],b.pcm[i]);break;
        }
        require(false,"cache or arbitration changed PCM sequence");
    }
    return n;
}
static uint64_t hash(const Result& r) {
    uint64_t h=1469598103934665603ull;
    for(auto v:r.pcm) for(unsigned i=0;i<4;i++) { h^=(v>>(8*i))&255;h*=1099511628211ull; }
    return h;
}
static void print_result(const Result& r,const Result& silent,bool busy) {
    double work=busy?(double(r.gpu_r+r.gpu_w)/r.cycles)/
                          (double(silent.gpu_r+silent.gpu_w)/silent.cycles):0;
    std::printf("{\"cycles\":%llu,\"produced\":%llu,\"consumed\":%llu,\"underruns\":%llu,"
                "\"fifo_min\":%u,\"fifo_final\":%u,\"fifo_initial\":%u,\"fifo_draining\":%s,"
                "\"audio_requests\":%llu,\"audio_beats\":%llu,\"source_requests\":%llu,\"source_beats\":%llu,"
                "\"stream_requests\":%llu,\"stream_beats\":%llu,\"stream_source_requests\":%llu,"
                "\"gpu_read_beats\":%llu,\"gpu_write_beats\":%llu,\"cpu_read_beats\":%llu,\"cpu_write_beats\":%llu,"
                "\"gpu_work_fraction_of_silent\":%.6f,\"pcm_samples\":%zu,\"pcm_fnv64\":\"%016llx\"}",
                (unsigned long long)r.cycles,(unsigned long long)r.produced,(unsigned long long)r.consumed,
                (unsigned long long)r.underruns,r.minimum,r.final_level,r.initial_level,
                r.final_level+2<r.initial_level?"true":"false",
                (unsigned long long)r.reads,(unsigned long long)r.beats,
                (unsigned long long)r.source_reads,(unsigned long long)r.source_beats,
                (unsigned long long)r.stream_reads,(unsigned long long)r.stream_beats,
                (unsigned long long)r.stream_source_reads,(unsigned long long)r.gpu_r,
                (unsigned long long)r.gpu_w,(unsigned long long)r.cpu_r,(unsigned long long)r.cpu_w,
                work,r.pcm.size(),(unsigned long long)hash(r));
}
int main(int argc,char **argv) {
    Verilated::commandArgs(argc,argv);
    require(argc==3||argc==4,"usage: test MHz latency [workload_count]");
    unsigned mhz=std::atoi(argv[1]),latency=std::atoi(argv[2]);
    unsigned workloads=argc==4?std::atoi(argv[3]):2;
    require(workloads>=1&&workloads<=3,"invalid workload_count");
    std::printf("{\"MHz\":%u,\"latency\":%u,\"voices\":32,\"duration_ms\":400,"
                "\"model\":\"real mixer/cache/selected arbiter; saturated synthetic CPU/GPU; bounded memory\",\"rows\":[\n",mhz,latency);
    Result silent=Bench(mhz,latency,true).run(0,0,true,false);
    bool first=true;
    for(unsigned workload=workloads==3?2:0;workload<workloads;workload++) {
        Result reference=Bench(mhz,latency,true).run(32,workload,false,false);
        for(bool cache:{false,true}) for(bool busy:{false,true}) {
            Result result=(!cache&&!busy)?reference:Bench(mhz,latency,true).run(32,workload,busy,cache);
            size_t matched=compare(reference,result);
            if(busy) require(result.gpu_r&&result.gpu_w&&result.cpu_r&&result.cpu_w,"a bus master starved");
            if(!first) std::printf(",\n");first=false;
            std::printf("{\"workload\":\"%s\",\"cache\":%s,\"busy\":%s,\"pcm_samples_compared\":%zu,"
                        "\"pcm_common_sequence_match\":true,\"result\":",workload==2?"q2-all22-plus-stream":workload==1?"stereo-short-loops":"q2-mono-plus-stream",
                        cache?"true":"false",busy?"true":"false",matched);
            print_result(result,silent,busy);std::printf("}");std::fflush(stdout);
        }
    }
    std::printf("\n],\"protocol_and_pcm_checks_passed\":true}\n");
}
