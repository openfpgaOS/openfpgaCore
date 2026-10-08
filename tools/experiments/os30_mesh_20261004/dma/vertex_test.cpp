#include "Vgpu_mesh_vertex.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <random>
#include <stdexcept>
#include <vector>
static void ck(bool ok,const char *why){if(!ok)throw std::runtime_error(why);}
struct Sim {
    Vgpu_mesh_vertex d;
    std::array<uint32_t,256> bank{},scratch{};
    std::vector<unsigned> writes;
    uint32_t qa=0,qb=0,bq=0;
    uint64_t product=0,quotient=0,cycles=0,div32=0,div64=0;
    unsigned divide_left=0;bool divide_done=false;
    Sim(){d.clk=0;d.reset_n=0;d.cancel=0;d.start=0;d.raw_ptr=0;d.output_ptr=0;
        tick();tick();d.reset_n=1;tick();}
    void tick(){
        d.scratch_qa=qa;d.scratch_qb=qb;d.bank_rdata=bq;d.product=product;
        d.div_busy=divide_left!=0;d.div_done=divide_done;d.div_quotient=quotient;
        d.clk=0;d.eval();
        auto next_qa=scratch[d.scratch_ra],next_qb=scratch[d.scratch_rb],next_bq=bank[d.bank_addr];
        uint64_t next_product=uint64_t(int64_t(int32_t(d.mul_a))*int64_t(int32_t(d.mul_b)));
        if(d.bank_we){ck(d.reset_n&&!d.cancel,"write during reset/abort");
            bank[d.bank_addr]=d.bank_wdata;writes.push_back(d.bank_addr);}
        bool request=d.div_start;auto num=uint64_t(d.div_numerator);
        auto den=uint32_t(d.div_denominator);unsigned bits=d.div_bits;
        d.clk=1;d.eval();d.clk=0;d.eval();
        qa=next_qa;qb=next_qb;bq=next_bq;product=next_product;++cycles;
        divide_done=false;
        if(!d.reset_n||d.cancel){divide_left=0;quotient=0;}
        else{
            if(divide_left&&!--divide_left)divide_done=true;
            if(request){ck(!divide_left&&!divide_done,"overlapping divider request");
                ck(den>0 && (bits==32||bits==64),"invalid unsigned divide request");
                ck(bits!=32 || num>>32==0,"32-bit division truncates numerator");
                quotient=num/den;divide_left=bits+2;if(bits==32)++div32;else++div64;}
        }
    }
};
int main(int argc,char**argv){Verilated::commandArgs(argc,argv);try{
    ck(argc==2,"need vector file");std::ifstream in(argv[1],std::ios::binary);ck(bool(in),"openvectors");
    Sim s;std::mt19937 rng(0x48465047);uint32_t v[37];unsigned cases=0,aborts=0;
    uint64_t cycles=0,captured_cycles=0,fuzz_cycles=0,mincy=9999,maxcy=0;
    while(in.read(reinterpret_cast<char*>(v),sizeof(v))){
        for(unsigned i=0;i<256;++i){s.bank[i]=rng();s.scratch[i]=rng();}
        unsigned src=v[35],dst=v[36];ck(src<=252&&dst<=249,"vector address invalid");
        for(unsigned i=0;i<4;++i)s.bank[src+i]=v[i];
        for(unsigned i=0;i<24;++i)s.scratch[64+i]=v[4+i];
        auto before=s.bank;s.writes.clear();s.d.raw_ptr=src;s.d.output_ptr=dst;
        s.d.start=1;uint64_t start=s.cycles;s.tick();s.d.start=0;
        unsigned timeout=300;while(!s.d.done&&timeout--)s.tick();
        ck(s.d.done&&!s.d.busy,"vertex timeout");uint64_t used=s.cycles-start;cycles+=used;
        if(cases<32496)captured_cycles+=used;else fuzz_cycles+=used;
        if(used<mincy)mincy=used;if(used>maxcy)maxcy=used;
        ck(s.writes.size()==7,"wrong vertex write count");
        for(unsigned i=0;i<7;++i){
            if(s.bank[dst+i]!=v[28+i]){
                std::fprintf(stderr,"case=%u word=%u got=%08x expected=%08x flags=%08x\n",
                    cases,i,s.bank[dst+i],v[28+i],v[26]);ck(false,"vertex mismatch");}
            ck(s.writes[i]==dst+i,"vertex write order/address mismatch");}
        for(unsigned i=0;i<256;++i)if(i<dst||i>=dst+7)ck(s.bank[i]==before[i],"vertex overwrote another region");
        // Stop during each phase across the randomized run, including divider
        // ownership. Cancellation suppresses all writes from that edge onward.
        if(cases%127==0){s.writes.clear();s.d.start=1;s.tick();s.d.start=0;
            unsigned wait=rng()%unsigned(used);while(wait--)s.tick();
            s.d.cancel=1;size_t nw=s.writes.size();s.tick();s.d.cancel=0;
            for(unsigned i=0;i<4;++i)s.tick();ck(!s.d.busy&&!s.d.done,"abort failed to quiesce vertex");
            ck(s.writes.size()==nw,"aborted vertex wrote after cancellation");++aborts;}
        ++cases;
    }
    ck(in.eof()&&in.gcount()==0,"truncated vectors");
    std::printf("{\"passed\":true,\"vertices\":%u,\"cycles\":%llu,\"captured_cycles\":%llu,"
        "\"fuzz_cycles\":%llu,\"minimum_cycles\":%llu,\"maximum_cycles\":%llu,"
        "\"divide32\":%llu,\"divide64\":%llu,\"aborts\":%u}\n",cases,
        (unsigned long long)cycles,(unsigned long long)captured_cycles,(unsigned long long)fuzz_cycles,
        (unsigned long long)mincy,(unsigned long long)maxcy,(unsigned long long)s.div32,
        (unsigned long long)s.div64,aborts);
    return 0;
}catch(const std::exception&e){std::fprintf(stderr,"FAIL: %s\n",e.what());return 1;}}
