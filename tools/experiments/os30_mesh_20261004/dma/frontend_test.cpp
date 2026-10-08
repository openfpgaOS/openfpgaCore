#include "Vgpu_mesh_frontend.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <cstdio>
#include <random>
#include <stdexcept>
#include <vector>
#ifndef FRONTEND_CODE_PAGES
#define FRONTEND_CODE_PAGES 3
#endif
static constexpr unsigned code_words=FRONTEND_CODE_PAGES*256;
static void check(bool ok,const char *why){if(!ok)throw std::runtime_error(why);}
static uint32_t value(uint32_t a){a^=0x5896ac21u;a*=0x85ebca6bu;return a^(a>>13);}
static uint32_t ins(unsigned op,unsigned d=0,unsigned a=0,unsigned b=0){return op<<24|d<<16|a<<8|b;}
static uint32_t li(unsigned d,unsigned n){return ins(1,d)|(n&65535);}
static std::vector<uint32_t> kernel(bool plus){
    std::vector<uint32_t> p={ins(24,0,62),ins(24,0,63),li(0,0),li(1,256),ins(22,2,0,0)};
    if(plus)p.push_back(ins(13,2,2,1));
    p.push_back(ins(23,2,0,0));p.push_back(ins(24,0,2,0));
    p.push_back(ins(13,0,0,1));p.push_back(ins(27,0,0,1));
    p.push_back(ins(26,2)|4);p.push_back(0);return p;
}
struct Sim {
    Vgpu_mesh_frontend d;
    std::mt19937 rng{0x6630646d};
    std::array<uint32_t,4096> ring{};
    std::vector<uint32_t> input,expected,received;
    std::vector<uint32_t> memory;
    uint32_t memory_base=0;
    size_t supplied=0;
    unsigned wp=0,rp=0,publish=0,consumer_stage=0;
    uint32_t in_q=0;
    bool have_publish=false,active=false,held=false,stalled=false;
    uint32_t addr=0,stalled_addr=0;
    unsigned n=0,beat=0,stalled_len=0;
    unsigned ar_hold=0,r_hold=0,consume_hold=0;
    bool inject_error=false,read_error=false,verify=true;
    uint64_t cycles=0,reads=0,overlap=0,full_cycles=0,aborts=0;
    Sim(){d.clk=0;d.reset_n=0;d.bus_reset_n=0;d.enable=1;d.soft_reset=0;d.ring_reset=0;
        d.input_wrptr=0;d.input_data=0;d.output_rdptr=0;d.output_addr=0;
        d.m_arready=0;d.m_rvalid=0;d.m_rdata=0;d.m_rresp=0;d.m_rlast=0;
        d.eval();tick(false);tick(false);d.reset_n=1;d.bus_reset_n=1;tick(false);}
    void clear_stream(){input.clear();expected.clear();received.clear();supplied=0;
        wp=rp=publish=0;have_publish=false;consumer_stage=0;in_q=0;
        d.input_wrptr=0;d.output_rdptr=0;d.output_addr=0;ring.fill(0);}
    void upload(bool plus){auto p=kernel(plus);input.push_back(0x63000000|unsigned(p.size()+1));
        input.push_back(0);input.insert(input.end(),p.begin(),p.end());}
    void upload_raw(unsigned start,const std::vector<uint32_t>&code){
        input.push_back(0x63000000|unsigned(code.size()+1));input.push_back(start);
        input.insert(input.end(),code.begin(),code.end());}
    void job(unsigned j,bool plus){uint32_t va=0x01000004+j*4096,ia=0x0200000c+j*4096,sa=0x03000014+j*4096;
        const uint32_t words[]={0x62000007,va,128,ia,64,sa,64,0};
        input.insert(input.end(),std::begin(words),std::end(words));
        expected.push_back(32);expected.push_back(64);
        for(unsigned i=0;i<128;++i)expected.push_back(value(va+i*4)+plus);
        for(unsigned i=0;i<64;++i)expected.push_back(value(sa+i*4)+plus);
        for(unsigned i=0;i<64;++i)expected.push_back(value(ia+i*4)+plus);}
    void job_custom(unsigned j,unsigned entry,const std::vector<uint32_t>&out){
        const auto n=expected.size();job(j,false);input.back()=entry;
        expected.resize(n);expected.insert(expected.end(),out.begin(),out.end());}
    void packet(unsigned words,unsigned tag){uint32_t h=0x42000000|words;
        input.push_back(h);expected.push_back(h);
        for(unsigned i=0;i<words;++i){input.push_back(tag+i);expected.push_back(tag+i);}}
    void tick(bool feed=true){
        if(feed && d.reset_n && !d.soft_reset && !d.ring_reset){
            if(have_publish){d.input_wrptr=publish;have_publish=false;}
            unsigned free=4095-((wp-d.input_rdptr)&4095);
            if(supplied<input.size()&&free&&rng()%3){unsigned take=1+rng()%31;if(take>free)take=free;
                while(take--&&supplied<input.size()){ring[wp]=input[supplied++];wp=(wp+1)&4095;}
                publish=wp;have_publish=true;}
        }
        d.input_data=in_q;
        d.m_arready=!active&&!ar_hold&&rng()%4;
        if(ar_hold)--ar_hold;
        if(!held&&active&&!r_hold&&rng()%3){held=true;uint32_t source=addr+4*beat;
            d.m_rdata=source>=memory_base&&(source-memory_base)/4<memory.size() ?
                memory[(source-memory_base)/4] : value(source);
            d.m_rlast=beat+1==n;d.m_rresp=read_error&&beat==0?2:0;}
        if(r_hold)--r_hold;
        d.m_rvalid=held;d.output_addr=rp;d.output_rdptr=rp;
        d.clk=0;d.eval();
        if(d.bus_reset_n){
            if(stalled)check(d.m_arvalid&&d.m_araddr==stalled_addr&&d.m_arlen==stalled_len,
                             "GPU reset/abort withdrew stalled AXI request");
            stalled=d.m_arvalid&&!d.m_arready;stalled_addr=d.m_araddr;stalled_len=d.m_arlen;
        }
        bool af=d.m_arvalid&&d.m_arready,rf=d.m_rvalid&&d.m_rready;
        uint32_t aa=d.m_araddr;unsigned al=d.m_arlen;
        bool last=d.m_rlast;
        unsigned nextin=d.input_addr;
        bool consume=false;
        if(d.reset_n&&!d.soft_reset&&!d.ring_reset){
            if(d.dbg_engine_busy&&d.dbg_dma_busy)++overlap;
            if(((d.output_wrptr-rp)&4095)==256)++full_cycles;
            if(consume_hold)--consume_hold;
            else if(consumer_stage&&rp!=d.output_wrptr&&rng()%4){consume=true;}
            if(!consumer_stage&&rp!=d.output_wrptr)consumer_stage=1;
            if(consume){uint32_t got=d.output_data;
                if(verify){if(received.size()>=expected.size()||got!=expected[received.size()]){
                    std::fprintf(stderr,"word=%zu got=%08x expected=%08x cycles=%llu fault=%u\n",
                        received.size(),got,received.size()<expected.size()?expected[received.size()]:0,
                        (unsigned long long)cycles,d.fault);check(false,"output packet order/data mismatch");}}
                received.push_back(got);rp=(rp+1)&4095;consumer_stage=0;
            }
        }
        d.clk=1;d.eval();d.clk=0;d.eval();in_q=ring[nextin];
        if(!d.bus_reset_n){active=held=stalled=false;}
        else{
            if(af){check(!active,"multiple external transactions");active=true;addr=aa;n=al+1;beat=0;
                read_error=inject_error;inject_error=false;++reads;}
            if(rf){check(active,"unsolicited response");held=false;++beat;if(last)active=false;}
        }
        ++cycles;
    }
    void drain(unsigned limit=3000000){
        while(limit--){tick();check(!d.fault,"unexpected frontend fault");
            if(supplied==input.size()&&!have_publish&&d.input_rdptr==wp&&!d.busy&&
               rp==d.output_wrptr&&!active)return;}
        check(false,"frontend drain timed out");
    }
};
int main(int argc,char **argv){Verilated::commandArgs(argc,argv);try{
    Sim s;s.consume_hold=20000;s.upload(false);
    for(unsigned j=0;j<80;++j){s.job(j,false);if(j%7==6)s.packet(513,0x05000000+j*1024);}
    s.upload(true);
    for(unsigned j=80;j<120;++j){s.job(j,true);if(j%11==10)s.packet(19,0x06000000+j*1024);}
    s.packet(0,0);s.drain();check(s.received==s.expected,"final output differs");
    check(s.overlap>10000,"DMA/geometry overlap absent");check(s.full_cycles>100,"FIFO backpressure absent");
    auto checked_words=s.received.size();
    // Program pages are separate physical RAM blocks. Execute through both
    // boundaries, from the upper page, and at the final legal instruction.
    s.clear_stream();s.d.ring_reset=1;s.tick(false);s.d.ring_reset=0;
    s.upload_raw(0,std::vector<uint32_t>(code_words,0));
    std::vector<unsigned> boundaries={255u,code_words-3};
    if(code_words>512){boundaries.push_back(511);boundaries.push_back(512);}
    for(unsigned start: boundaries){
        unsigned v=1234+start;s.upload_raw(start,{li(0,v),ins(24,0,0,0),0});
        s.job_custom(start,start,{v});
    }
    s.upload_raw(code_words-1,{0});s.job_custom(code_words-1,code_words-1,{});s.drain();
    checked_words+=s.received.size();
    // Adjacent jobs have independent bank metadata, including odd packed
    // triangle counts and the largest legal 64 triangles in 32 DMA words.
    s.clear_stream();s.d.ring_reset=1;s.tick(false);s.d.ring_reset=0;
    s.upload_raw(0,{ins(24,0,62),ins(24,0,63),0});
    for(unsigned j=0;j<40;++j){
        unsigned vertices=1+j%32;
        unsigned triangles=j%4==0?64:j%4==1?1:j%4==2?5:0;
        unsigned indexwords=triangles?((triangles+1)/2):1+j%64;
        const uint32_t w[]={0x62000007,0x01000004+j*4096,vertices*4,
            0x0200000c+j*4096,(triangles<<16)|indexwords,0x03000014+j*4096,64,0};
        s.input.insert(s.input.end(),std::begin(w),std::end(w));
        s.expected.push_back(vertices);s.expected.push_back(triangles?triangles:indexwords);
    }
    s.drain();checked_words+=s.received.size();
    // Cancel while AR waits, R waits, execution emits, and both banks are owned.
    // GPU/media reset does not reset the memory fabric: pending R must drain.
    for(unsigned i=0;i<90;++i){
        s.clear_stream();s.verify=false;s.d.ring_reset=1;s.tick(false);s.d.ring_reset=0;
        s.upload(false);s.job(i,false);s.job(i+1,false);
        if(i%3==0)s.ar_hold=120;
        if(i%3==1)s.r_hold=160;
        unsigned wait=70+i*7;while(wait--)s.tick();
        if(i%3==0)s.d.reset_n=0;else if(i%3==1)s.d.soft_reset=1;else s.d.ring_reset=1;
        s.tick(false);s.tick(false);++s.aborts;
        s.d.reset_n=1;s.d.soft_reset=0;s.d.ring_reset=0;
        s.clear_stream();s.d.ring_reset=1;s.tick(false);s.d.ring_reset=0;
        unsigned bound=10000;while(s.d.dbg_dma_busy&&bound--)s.tick(false);
        check(!s.d.dbg_dma_busy&&!s.d.dbg_bank_owned,"cancel failed to drain/release banks");
        s.verify=true;s.upload(false);s.job(i,false);s.drain();checked_words+=s.received.size();
    }
    // Malformed packet, invalid segment, and external bus error fail-stop.
    for(unsigned mode=0;mode<10;++mode){s.clear_stream();s.d.ring_reset=1;s.tick(false);s.d.ring_reset=0;
        s.verify=false;s.upload(false);s.job(mode,false);
        if(mode==0)s.input[s.input.size()-8]=0x62000006;
        if(mode==1)s.input[s.input.size()-6]=129;
        if(mode==2)s.inject_error=true;
        if(mode==3)s.input.back()=code_words;
        if(mode==4)s.upload_raw(code_words-1,{0,0});
        if(mode==5)s.upload_raw(code_words,{0});
        if(mode==6)s.upload_raw(0,std::vector<uint32_t>(code_words+1,0));
        if(mode==7)s.input[s.input.size()-4]=(65u<<16)|33;
        if(mode==8)s.input[s.input.size()-4]=(5u<<16)|2;
        if(mode==9)s.input[s.input.size()-4]=(1u<<16);
        unsigned bound=20000;while(!s.d.fault&&bound--)s.tick();
        check(s.d.fault==(mode==2?2:1),"fault classification missing");
        for(unsigned i=0;i<100;++i)s.tick();check(s.d.busy,"fault must block normal retirement");
    }
    std::printf("{\"pass\":true,\"output_words_checked\":%zu,\"cycles\":%llu,\"read_bursts\":%llu,"
        "\"dma_engine_overlap_cycles\":%llu,\"fifo_full_cycles\":%llu,\"cancellations\":%llu,\"fault_cases\":10,\"program_boundary_jobs\":%zu,\"code_words\":%u,\"metadata_jobs\":40}\n",
        checked_words,(unsigned long long)s.cycles,(unsigned long long)s.reads,
        (unsigned long long)s.overlap,(unsigned long long)s.full_cycles,(unsigned long long)s.aborts,boundaries.size()+1,code_words);
    return 0;
}catch(const std::exception&e){std::fprintf(stderr,"FAIL: %s\n",e.what());return 1;}}
