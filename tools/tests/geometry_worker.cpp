#include "Vgeometry_worker_tb.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <random>
#include <vector>
static Vgeometry_worker_tb tb;
static std::mt19937 rng(0x307064);
static std::array<uint32_t,32> positions;
static uint32_t loaded;
static void tick() {tb.clk=0;tb.eval();tb.clk=1;tb.eval();tb.clk=0;tb.eval();}
static void reset() {
    loaded=0;
    tb.host_write=tb.host_publish=tb.consume=tb.soft_reset=tb.ring_reset=0;
    tb.enable=1;tb.reset_n=0;tick();tick();tb.reset_n=1;tick();
}
static int16_t sat(int64_t n) {return n < -32768 ? -32768 : n > 32767 ? 32767 : n;}
static std::array<int32_t,12> matrix;
static int32_t xc=160,yc=1920,xs=160,ys=1920,nc=256;
static void object(std::vector<uint32_t>& input) {
    input.push_back(0x5000001a);
    for(auto x:matrix)input.push_back(x);
    for(int i=12;i<20;i++)input.push_back(0);
    for(auto x:{xc,yc,xs,ys,nc,3})input.push_back(x);
}
static void projected(std::vector<uint32_t>& out, unsigned slot, unsigned rgb,
                      int32_t x,int32_t y,int32_t w,uint32_t u,uint32_t v,
                      bool raw,uint32_t explicit_depth=0) {
    uint32_t den=w<nc?uint32_t(nc):uint32_t(w);
    uint32_t q=den?uint32_t((uint64_t(1)<<(raw?38:32))/den):0xffffffffu;
    uint32_t zi=raw?q>>6:q;
    int32_t rx=int32_t((int64_t(x)*int32_t(zi))>>16);
    int32_t ry=int32_t((int64_t(y)*int32_t(zi))>>16);
    int16_t sx=sat(int64_t(xc)*16+((int64_t(xs)*rx)>>12));
    int16_t sy=sat(int64_t(yc)-((int64_t(ys)*ry)>>16));
    positions[slot&31]=uint32_t(uint16_t(sx))|(uint32_t(uint16_t(sy))<<16);
    loaded|=1u<<(slot&31);
    for(uint32_t z:{0x5c000006u,(rgb<<16)|(slot&31),uint32_t(uint16_t(sx))|(uint32_t(uint16_t(sy))<<16),
                    zi,u,v,raw?q:explicit_depth})out.push_back(z);
}
static void raw(std::vector<uint32_t>& in,std::vector<uint32_t>& out,unsigned count) {
    in.push_back(0x59000000u+5*count);
    for(unsigned i=0;i<count;i++) {
        unsigned slot=rng()&31,rgb=rng()&65535;
        int16_t x=int16_t(rng()),y=int16_t(rng()),z=int16_t(rng());
        uint32_t u=rng(),v=rng();
        for(uint32_t a:{slot,uint32_t(uint16_t(x))|(uint32_t(uint16_t(y))<<16),
                         uint32_t(uint16_t(z))|(rgb<<16),u,v})in.push_back(a);
        int32_t cam[3];
        for(int r=0;r<3;r++)cam[r]=int32_t(((int64_t(matrix[4*r])*x
            +int64_t(matrix[4*r+1])*y+int64_t(matrix[4*r+2])*z)>>16)+matrix[4*r+3]);
        projected(out,slot,rgb,cam[0],cam[1],cam[2],u,v,true);
    }
}
static void clip(std::vector<uint32_t>& in,std::vector<uint32_t>& out) {
    uint32_t slot=rng()&31,rgb=rng()&65535,u=rng(),v=rng(),depth=rng();
    int32_t x=int32_t(rng()),y=int32_t(rng()),w=int32_t(rng());
    for(uint32_t a:{0x56000008u,slot,uint32_t(x),uint32_t(y),uint32_t(w),u,v,rgb,depth})in.push_back(a);
    projected(out,slot,rgb,x,y,w,u,v,false,depth);
}
static void triangle(std::vector<uint32_t>& in,std::vector<uint32_t>& out,unsigned cull) {
    std::vector<unsigned> slots;
    for(unsigned i=0;i<32;i++)if(loaded&(1u<<i))slots.push_back(i);
    unsigned a=slots[rng()%slots.size()],b=slots[rng()%slots.size()],c=slots[rng()%slots.size()];
    uint32_t word=a|(b<<5)|(c<<10)|(cull<<15);
    in.push_back(0x5b000001);in.push_back(word);
    int64_t x0=int16_t(positions[a]),y0=int16_t(positions[a]>>16);
    int64_t x1=int16_t(positions[b]),y1=int16_t(positions[b]>>16);
    int64_t x2=int16_t(positions[c]),y2=int16_t(positions[c]>>16);
    int64_t det=(x1-x0)*(y2-y0)-(x2-x0)*(y1-y0);
    bool reject=cull && (det==0 || cull==3 || (cull==1 && det<0) || (cull==2 && det>0));
    if(!reject) {out.push_back(0x54000001);out.push_back(word);}
}
static uint64_t run(const std::vector<uint32_t>& input,const std::vector<uint32_t>& expected) {
    size_t sent=0,got=0;uint64_t cycles=0;unsigned unpublished=0;bool read_ready=false;
    while(sent<input.size() || got<expected.size() || tb.busy || tb.read_ptr!=tb.out_write) {
        if(++cycles>3000000) {std::fprintf(stderr,"timeout sent=%zu/%zu got=%zu/%zu busy=%d in=%u host=%u read=%u write=%u\n",
            sent,input.size(),got,expected.size(),tb.busy,tb.in_read,tb.host_ptr,tb.read_ptr,tb.out_write);std::exit(1);}
        tb.host_write=tb.host_publish=tb.consume=0;
        // Random clock-enable holds, upload pauses and long raster stalls.
        tb.enable=(rng()%9)!=0;
        bool room=((tb.host_ptr+1)&63)!=tb.in_read;
        if(unpublished && (unpublished>=13 || !room || sent==input.size() || rng()%7==0)) {
            tb.host_publish=1;unpublished=0;
        } else if(sent<input.size() && room && rng()%5) {
            tb.host_write=1;tb.host_data=input[sent++];unpublished++;
        }
        if(read_ready && tb.read_ptr!=tb.out_write && (cycles%1300)>300 && rng()%4) {
            if(got>=expected.size() || tb.data!=expected[got]) {
                std::fprintf(stderr,"word %zu: got %08x expected %08x at cycle %llu\n",got,tb.data,
                    got<expected.size()?expected[got]:0,(unsigned long long)cycles);std::exit(1);
            }
            got++;tb.consume=1;
        }
        read_ready=((tb.out_write-tb.read_ptr)&63)>tb.consume;
        tick();
    }
    tb.host_write=tb.host_publish=tb.consume=0;tb.enable=1;tick();
    return cycles;
}
int main(int argc,char**argv) {
    Verilated::commandArgs(argc,argv);reset();uint64_t cycles=0;size_t words=0;
    for(unsigned batch=0;batch<120;batch++) {
        std::vector<uint32_t> in,out;
        // Arm the same sticky surface state the GPU requires for cache loads.
        in.push_back(0x4a000011);out.push_back(0x4a000011);
        for(unsigned i=0;i<17;i++) {in.push_back(0);out.push_back(0);}
        for(auto& x:matrix)x=int32_t(rng());
        // Include both normal projection and denominator clamps/zero.
        nc=batch%17==0?0:256;
        object(in);
        for(unsigned j=0;j<16;j++) {
            if(j%3==0)clip(in,out);else raw(in,out,1+rng()%32);
            for(unsigned cull=0;cull<4;cull++)triangle(in,out,cull);
            // Mixed ordinary commands, fences and invalid geometry counts.
            unsigned n=j%9;uint32_t op=j%4==0?0x59:j%4==1?0x56:0x33;
            if((op==0x59 && n==5)||(op==0x56 && n==8))n=4;
            in.push_back((op<<24)|n);out.push_back((op<<24)|n);
            for(unsigned k=0;k<n;k++) {uint32_t v=rng();in.push_back(v);out.push_back(v);}
        }
        cycles+=run(in,out);words+=out.size();
        if(batch%10==9) {
            // Abort a published command under output backpressure, then reuse.
            tb.enable=0;
            for(unsigned k=0;k<10;k++) {tb.host_write=1;tb.host_data=0x33000000;tick();}
            tb.host_write=0;tb.host_publish=1;tick();tb.host_publish=0;
            tb.soft_reset=1;tick();tb.soft_reset=0;tb.enable=1;tick();
            if(tb.busy || tb.read_ptr!=tb.out_write) {std::fprintf(stderr,"soft reset failed\n");return 1;}
            reset();
        }
    }
    std::printf("PASS: 1920 mixed vertex batches, 7680 winding tests, %zu exact output words, wrap/backpressure/enable/reset (%llu cycles)\n",
        words,(unsigned long long)cycles);tb.final();
}
