#include "Vgpu_mesh_attributes.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <cstdio>
#include <stdexcept>
static void require(bool value,const char *why){if(!value)throw std::runtime_error(why);}
struct Sim {
    Vgpu_mesh_attributes d;
    std::array<uint32_t,256> scratch,bank;
    unsigned writes=0;
    void tick(){
        d.clk=0;d.eval();uint32_t qa=scratch[d.scratch_ra],qb=scratch[d.scratch_rb];
        int64_t product=int64_t(int32_t(d.mul_a))*int64_t(int32_t(d.mul_b));
        if(d.bank_we){bank[d.bank_addr]=d.bank_wdata;++writes;}
        d.clk=1;d.eval();d.scratch_qa=qa;d.scratch_qb=qb;d.product=uint64_t(product);d.eval();
    }
    void reset(){d.reset_n=0;d.cancel=0;d.start=0;tick();tick();d.reset_n=1;tick();writes=0;}
};
int main(int argc,char **argv){Verilated::commandArgs(argc,argv);try{
    require(argc==2,"vectors argument");FILE*f=fopen(argv[1],"rb");require(f,"vectors open");
    Sim s;uint32_t row[21];uint64_t count=0,cycles=0,words=0,aborts=0;
    while(fread(row,4,21,f)==21){
        s.reset();for(unsigned i=0;i<256;++i){s.scratch[i]=0x71000000+i;s.bank[i]=0xba000000+i;}
        unsigned src=row[18],dst=row[19],cr=row[20];
        for(unsigned i=0;i<7;++i)s.scratch[(src+i)&255]=row[i];s.scratch[cr]=row[7];
        auto original=s.scratch;auto expected=s.bank;unsigned n=row[7]?10:7;
        for(unsigned i=0;i<n;++i)expected[(dst+i)&255]=row[8+i];
        s.d.src_ptr=src;s.d.dst_ptr=dst;s.d.combine_reg=cr;s.d.start=1;s.tick();s.d.start=0;
        unsigned c=0;while(!s.d.done&&c<100){s.tick();++c;}
        require(s.d.done&&!s.d.busy,"operation timeout");require(c==(row[7]?16:13),"cycles differ");
        if(s.bank!=expected){for(unsigned i=0;i<256;++i)if(s.bank[i]!=expected[i])fprintf(stderr,"case%llu bank%u got%08x want%08x\n",(unsigned long long)count,i,s.bank[i],expected[i]);throw std::runtime_error("attribute mismatch");}
        require(s.scratch==original,"scratch mutated");require(s.writes==n,"wrong write count");
        cycles+=c;words+=n;++count;
        if(count%257==0){
            s.reset();s.d.start=1;s.tick();s.d.start=0;
            unsigned delay=(count/257)%18;while(delay--)s.tick();auto before=s.bank;
            s.d.cancel=1;s.d.clk=0;s.d.eval();require(!s.d.bank_we,"cancel leaked write");s.tick();s.d.cancel=0;
            for(unsigned i=0;i<20;++i)s.tick();require(!s.d.busy&&!s.d.done&&s.bank==before,"cancel did not retire");++aborts;
        }
    }
    require(feof(f),"truncated vectors");fclose(f);
    printf("{\"pass\":true,\"vertices\":%llu,\"cycles\":%llu,\"writes\":%llu,\"cancellations\":%llu}\n",(unsigned long long)count,(unsigned long long)cycles,(unsigned long long)words,(unsigned long long)aborts);
}catch(const std::exception&e){fprintf(stderr,"FAIL %s\n",e.what());return 1;}return 0;}
