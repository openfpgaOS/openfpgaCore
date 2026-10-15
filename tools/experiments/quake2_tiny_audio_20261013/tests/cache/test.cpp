#include "Vaudio_sample_cache.h"
#include <verilated.h>
#include <array>
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <functional>
#include <utility>
#include <vector>

static void require(bool b,const char *why){if(!b){std::fprintf(stderr,"FAIL: %s\n",why);std::exit(1);}}
static uint32_t rng=0x273594ab;
static uint32_t random32(){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
static uint32_t memory_word(uint32_t address,uint32_t epoch){return (address*0x9e3779b9u)^((address>>11)*0x85ebca6bu)^(epoch*0xa511e9b3u)^0x763917ab;}
struct Beat {uint32_t data;unsigned resp;bool last;};
struct Transaction {uint32_t address;unsigned len;};
struct Bench {
    Vaudio_sample_cache d;
    bool busy=false,offered=false,hold_ar=false,hold_r=false;
    uint32_t rd_address=0,epoch=0,held_araddr=0,held_rdata=0;
    unsigned rd_len=0,rd_index=0,wait=0,held_arlen=0,held_rresp=0;
    bool held_rlast=false;
    uint64_t cycles=0,read_beats=0,requests=0,response_beats=0;
    unsigned max_latency=16;
    bool stalled=true, error_enabled=false;
    uint32_t error_address=0;unsigned error_value=2;
    std::vector<Transaction> transactions;
    std::vector<Beat> response;
    bool ar_accepted=false;
    Bench(){reset();}
    void tick(){
        d.m_arready=!busy && (!stalled || (random32()%5)!=0);
        if(busy && wait==0)offered=true;
        d.m_rvalid=busy&&offered;
        d.m_rdata=memory_word(rd_address+rd_index*4,epoch);
        d.m_rresp=error_enabled && rd_address+rd_index*4==error_address?error_value:0;
        d.m_rlast=rd_index==rd_len;
        d.clk=0;d.eval();
        if(hold_ar)require(d.m_arvalid && d.m_araddr==held_araddr && d.m_arlen==held_arlen,"downstream AR changed under backpressure");
        if(hold_r)require(d.s_rvalid && d.s_rdata==held_rdata && d.s_rresp==held_rresp && d.s_rlast==held_rlast,"upstream R changed under backpressure");
        hold_ar=d.m_arvalid&&!d.m_arready;held_araddr=d.m_araddr;held_arlen=d.m_arlen;
        hold_r=d.s_rvalid&&!d.s_rready;held_rdata=d.s_rdata;held_rresp=d.s_rresp;held_rlast=d.s_rlast;
        bool ar=d.m_arvalid&&d.m_arready, rr=d.m_rvalid&&d.m_rready;
        bool sr=d.s_rvalid&&d.s_rready;
        ar_accepted=d.s_arvalid&&d.s_arready;
        uint32_t aa=d.m_araddr;unsigned al=d.m_arlen;
        if(sr){response.push_back({d.s_rdata,unsigned(d.s_rresp),bool(d.s_rlast)});response_beats++;}
        d.clk=1;d.eval();cycles++;
        if(!d.reset_n){busy=offered=hold_ar=hold_r=false;return;}
        if(ar){require(!busy,"multiple downstream transactions");busy=true;offered=false;rd_address=aa;rd_len=al;rd_index=0;wait=stalled?random32()%(max_latency+1):0;transactions.push_back({aa,al});requests++;}
        else if(rr){read_beats++;offered=false;if(rd_index==rd_len)busy=false;else {rd_index++;wait=stalled?random32()%4:0;}}
        else if(busy&&wait)wait--;
    }
    void reset(){
        d.reset_n=0;d.cache_enable=1;d.request_cacheable=1;d.request_voice=0;
        d.invalidate_valid=0;d.invalidate_voice=0;d.s_arvalid=0;d.s_araddr=0;d.s_arlen=0;d.s_rready=0;
        busy=offered=hold_ar=hold_r=false;
        for(unsigned i=0;i<4;i++)tick();d.reset_n=1;tick();response.clear();transactions.clear();
    }
    void invalidate(unsigned voice){d.invalidate_valid=1;d.invalidate_voice=voice;tick();d.invalidate_valid=0;}
    using Hook=std::function<void(Bench&,unsigned)>;
    std::vector<Beat> request(unsigned voice,uint32_t addr,unsigned len,bool cacheable=true,Hook hook={}){
        response.clear();d.request_voice=voice;d.request_cacheable=cacheable;d.s_araddr=addr;d.s_arlen=len;d.s_arvalid=1;
        bool accepted=false;
        for(unsigned t=0;t<200000;t++){
            d.s_rready=!stalled || random32()%4!=0;
            if(hook)hook(*this,t);
            tick();
            if(ar_accepted){require(!accepted,"duplicate upstream AR");accepted=true;d.s_arvalid=0;}
            if(!response.empty()&&response.back().last){require(accepted,"response before request");require(response.size()==len+1,"incorrect response length");return response;}
        }
        require(false,"request timeout");return{};
    }
    void expect(const std::vector<Beat>& beats,uint32_t address,unsigned expected_epoch,unsigned resp=0){
        for(unsigned i=0;i<beats.size();i++){
            if(beats[i].data!=memory_word(address+i*4,expected_epoch)){
                std::fprintf(stderr,"data at %08x beat%u got%08x want%08x\n",address,i,beats[i].data,memory_word(address+i*4,expected_epoch));
                require(false,"read data mismatch");
            }
            require(beats[i].resp==resp,"RRESP mismatch");
            require(beats[i].last==(i+1==beats.size()),"RLAST mismatch");
        }
    }
};

int main(int argc,char**argv){
    Verilated::commandArgs(argc,argv);Bench b;unsigned checks=0;
    // Every voice has independent storage, including identical addresses.
    for(unsigned v=0;v<32;v++){
        auto before=b.requests;auto x=b.request(v,0x12345004,1);b.expect(x,0x12345004,0);
        require(b.requests==before+1,"first voice miss missing");checks++;
    }
    for(unsigned v=0;v<32;v++){
        auto before=b.requests;auto x=b.request(v,0x12345018,1);b.expect(x,0x12345018,0);
        require(b.requests==before,"per-voice hit missed");checks++;
    }
    // All27 tag bits participate; high-address aliases cannot hit.
    for(unsigned bit=5;bit<32;bit++){
        uint32_t addr=0x12345000u^(1u<<bit);auto before=b.requests;
        b.expect(b.request(7,addr,0),addr,0);require(b.requests==before+1,"full-address tag alias");checks++;
    }
    auto before=b.requests;b.expect(b.request(5,0x1000001c,1),0x1000001c,0);
    require(b.requests==before+2,"cross-line len1 needs fill and tail");
    require(b.transactions.back().len==0,"cross-line tail must bypass one word");checks++;
    before=b.requests;b.expect(b.request(5,0x1000001c,0),0x1000001c,0);
    require(b.requests==before,"cross-line tail evicted the first line");checks++;
    before=b.requests;b.expect(b.request(5,0x10000020,1),0x10000020,0);
    require(b.requests==before+1,"tail bypass unexpectedly allocated second line");checks++;
    before=b.requests;b.expect(b.request(3,0xfffffffcu,255),0xfffffffcu,0);
    require(b.requests==before+33,"long burst/address wrap line count");checks++;
    // Mutation is visible only after the explicit invalidation contract.
    b.expect(b.request(2,0x1000,1),0x1000,0);b.epoch=1;
    before=b.requests;b.expect(b.request(2,0x1000,1),0x1000,0);require(before==b.requests,"unexpected replacement");
    b.invalidate(1);b.expect(b.request(2,0x1000,0),0x1000,0);
    b.invalidate(2);b.expect(b.request(2,0x1000,1),0x1000,1);checks+=3;
    // Every bypass burst retains its exact address/length and new bytes.
    for(unsigned len:{0u,1u,7u,19u,255u})for(unsigned mode=0;mode<3;mode++){
        b.epoch++;b.d.cache_enable=mode!=1;
        uint32_t addr=0x8000301c+(mode==2?1:0);
        size_t tx=b.transactions.size();b.expect(b.request(2,addr,len,mode!=0),addr,b.epoch);
        require(b.transactions.size()==tx+1,"bypass split/coalesced burst");
        require(b.transactions.back().address==addr&&b.transactions.back().len==len,"bypass changed address/length");checks++;
    }
    b.d.cache_enable=1;b.epoch++;b.expect(b.request(2,0x1000,1),0x1000,b.epoch);checks++;
    // Reset invalidates stale RAM; the RAM itself need not be reset.
    b.reset();b.epoch++;before=b.requests;b.expect(b.request(2,0x1000,0),0x1000,b.epoch);
    require(b.requests==before+1,"reset retained stale validity");checks++;
    // Invalidate at every fill beat, including simultaneous final commit.
    for(unsigned offset=0;offset<8;offset++){
        b.reset();b.epoch++;uint64_t start=b.read_beats;bool fired=false;
        auto hook=[&](Bench& x,unsigned){
            x.d.invalidate_valid=0;
            if(!fired && x.busy && x.rd_index==offset && x.wait==0){x.d.invalidate_voice=9;x.d.invalidate_valid=1;fired=true;}
        };
        b.expect(b.request(9,0x409c,1,true,hook),0x409c,b.epoch);
        b.d.invalidate_valid=0;require(fired,"invalidation hook not reached");
        b.epoch++;before=b.requests;b.expect(b.request(9,0x40a0,1),0x40a0,b.epoch);
        require(b.requests==before+1,"invalidated fill committed");checks++;
    }
    // Disable during refill, then re-enable before completion: no valid commit.
    b.reset();bool disabled=false,reenabled=false;
    auto off_hook=[&](Bench& x,unsigned){if(!disabled&&x.busy&&x.rd_index==2){x.d.cache_enable=0;disabled=true;}else if(disabled&&!reenabled&&x.busy&&x.rd_index==4){x.d.cache_enable=1;reenabled=true;}};
    b.expect(b.request(8,0x5080,1,true,off_hook),0x5080,b.epoch);require(disabled&&reenabled,"disable hook missing");
    b.epoch++;before=b.requests;b.expect(b.request(8,0x5080,1),0x5080,b.epoch);require(b.requests==before+1,"disable during fill committed stale line");checks++;
    // Errors are propagated conservatively and never populate valid storage.
    for(unsigned resp:{1u,2u,3u}){
        b.reset();b.error_enabled=true;b.error_address=0x6004;b.error_value=resp;
        b.expect(b.request(1,0x6010,1),0x6010,b.epoch,resp);
        b.error_enabled=false;before=b.requests;b.expect(b.request(1,0x6010,1),0x6010,b.epoch);
        require(b.requests==before+1,"error fill was cached");checks++;
        b.error_enabled=true;b.error_address=0x6004;b.error_value=resp;
        auto x=b.request(1,0x6000,3,false);for(unsigned i=0;i<x.size();i++)require(x[i].resp==(i==1?resp:0),"bypass RRESP changed");
        b.error_enabled=false;checks++;
    }
    // Random request oracle predicts exact downstream line transactions.
    b.reset();std::array<bool,32> valid{};std::array<uint32_t,32> tag{};
    uint64_t fuzz_reads=0;
    for(unsigned test=0;test<12000;test++){
        unsigned voice=random32()%32, len=(random32()%13==0)?random32()%256:random32()%2;
        bool cacheable=random32()%11!=0,enabled=random32()%17!=0;
        if(!enabled)valid.fill(false);
        b.d.cache_enable=enabled;
        if(random32()%7==0){unsigned victim=random32()%32;b.invalidate(victim);valid[victim]=false;}
        uint32_t addr=(random32()%7==0?random32():0x100000+voice*4096+(random32()%64)*4)&~3u;
        std::vector<Transaction> expected;
        if(!enabled||!cacheable)expected.push_back({addr,len});
        else for(unsigned beat=0;beat<=len;beat++){
            if(beat==len && beat>0 && ((addr+(beat-1)*4)&31)==28){
                expected.push_back({addr+beat*4,0});break;
            }
            uint32_t line=(addr+beat*4)&~31u;
            if(!valid[voice]||tag[voice]!=line){expected.push_back({line,7});valid[voice]=true;tag[voice]=line;}
        }
        size_t begin=b.transactions.size();auto x=b.request(voice,addr,len,cacheable);b.expect(x,addr,b.epoch);
        require(b.transactions.size()==begin+expected.size(),"random oracle request count");
        for(unsigned i=0;i<expected.size();i++)require(b.transactions[begin+i].address==expected[i].address&&b.transactions[begin+i].len==expected[i].len,"random oracle transaction mismatch");
        checks++;fuzz_reads+=x.size();
    }
    // Quantify sequential11.025kHz mono tap pairs. The mixer repeats source
    // words at48kHz; compare fetch work with the existing1/2-beat requests.
    b.reset();b.stalled=false;before=b.requests;uint64_t beats_before=b.read_beats;
    unsigned pos=0,frac=0,uncached_requests=0,uncached_beats=0;
    for(unsigned f=0;f<48000;f++){
        unsigned byte=pos*2;unsigned len=(pos&1)?1:0;
        b.expect(b.request(4,0x100000+(byte&~3u),len),0x100000+(byte&~3u),b.epoch);
        uncached_requests++;uncached_beats+=len+1;unsigned x=frac+15053;pos+=x>>16;frac=x&65535;
    }
    std::printf("{\"passed\":true,\"directed_and_random_checks\":%u,\"random_response_beats\":%llu,\"total_cycles\":%llu,\"audio_11k_48k_outputs\":48000,\"uncached_requests\":%u,\"cache_requests\":%llu,\"uncached_beats\":%u,\"cache_beats\":%llu}\n",checks,(unsigned long long)fuzz_reads,(unsigned long long)b.cycles,uncached_requests,(unsigned long long)(b.requests-before),uncached_beats,(unsigned long long)(b.read_beats-beats_before));
}
