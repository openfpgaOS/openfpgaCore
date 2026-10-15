#include "Vgeometry_worker_tb.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <climits>
#include <cstdio>
#include <cstdlib>
#include <random>
#include <vector>
static Vgeometry_worker_tb tb;
static std::mt19937 rng(0x307064);
static std::array<uint32_t,32> positions;
static uint32_t loaded;
static std::vector<uint32_t> psram(1<<22);
static bool mem_pending,force_ar_hold,force_r_hold;
static unsigned mem_addr,mem_beat,mem_delay;
static uint64_t bursts,beats,paused_beats;
static void tick() {
    tb.clk=0;
    tb.mesh_arready=!force_ar_hold && !mem_pending && rng()%3;
    tb.mesh_rvalid=!force_r_hold && mem_pending && !mem_delay && rng()%4;
    tb.mesh_rlast=tb.mesh_rvalid && mem_beat==7;
    tb.mesh_rdata=mem_pending?psram[(mem_addr>>2)+mem_beat]:0;
    tb.eval();
    bool accept=tb.mesh_arvalid && tb.mesh_arready;
    unsigned addr=tb.mesh_araddr,len=tb.mesh_arlen;
    bool valid=tb.mesh_rvalid,last=tb.mesh_rlast;
    tb.clk=1;tb.eval();tb.clk=0;tb.eval();
    if(!tb.mesh_reset_n) {mem_pending=false;return;}
    if(mem_delay)mem_delay--;
    if(valid) {beats++;if(!tb.enable)paused_beats++;mem_beat++;if(last)mem_pending=false;}
    if(accept) {
        if((addr&31) || addr>=0x1000000 || len!=7 || mem_pending)std::abort();
        mem_pending=true;mem_addr=addr;mem_beat=0;mem_delay=2+rng()%6;bursts++;
    }
}
static void reset() {
    loaded=0;force_ar_hold=force_r_hold=false;
    tb.host_write=tb.host_publish=tb.consume=tb.soft_reset=tb.ring_reset=0;
    tb.enable=1;tb.mesh_reset_n=0;tb.reset_n=0;tick();tick();tb.mesh_reset_n=1;tb.reset_n=1;tick();
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
static void native(std::vector<uint32_t>& in,std::vector<uint32_t>& out,unsigned batch,int32_t native_clip=256) {
    unsigned oldbase=0x1014,newbase=0x203c,stream=0x300c,count=64;
    unsigned cull=batch%3;
    std::array<int32_t,24> coeff{};
    std::array<uint16_t,256> palette;
    for(auto &p:palette)p=rng();
    for(unsigned i=0;i<count;i++) {psram[(oldbase>>2)+i]=rng();psram[(newbase>>2)+i]=rng();}
    for(unsigned r=0;r<3;r++) {
        for(unsigned c=0;c<6;c++)coeff[r*8+c]=int32_t(rng()%0x6000001)-0x3000000;
        coeff[r*8+6]=r==2?int32_t(10000+rng()%200000):int32_t(rng()%10000)-5000;
    }
    // Explicit zero and negative coefficient coverage.
    coeff[0]=0;coeff[1]=-0x1234567;
    auto oldproj=std::array<int32_t,5>{xc,yc,xs,ys,nc};
    xc=150+batch%20;yc=1920;xs=130;ys=1700;nc=native_clip;
    unsigned cursor=stream>>2;
    for(unsigned mesh=0;mesh<2;mesh++) {
        unsigned nv=1+rng()%32,nt=1+rng()%64;
        psram[cursor++]=nv|(nt<<8);
        for(unsigned i=0;i<nv;i++) {
            unsigned source=rng()%count;uint32_t u=rng(),v=rng();
            psram[cursor++]=source;psram[cursor++]=u;psram[cursor++]=v;
            uint32_t a=psram[(oldbase>>2)+source],b=psram[(newbase>>2)+source];
            int32_t cam[3];
            for(unsigned r=0;r<3;r++) {
                int64_t sum=0;
                for(unsigned c=0;c<6;c++)sum+=int64_t(coeff[r*8+c])*((c<3?a:b)>>((c%3)*8)&255);
                sum=int64_t(uint64_t(sum)<<16)>>16;
                cam[r]=int32_t((sum>>16)+coeff[r*8+6]);
            }
            projected(out,i,palette[b>>24],cam[0],cam[1],cam[2],u,v,true);
        }
        for(unsigned i=0;i<nt;i++) {
            unsigned a=rng()%nv,b=rng()%nv,c=rng()%nv;
            uint32_t word=a|(b<<5)|(c<<10);psram[cursor++]=word;word|=cull<<15;
            int64_t x0=int16_t(positions[a]),y0=int16_t(positions[a]>>16);
            int64_t x1=int16_t(positions[b]),y1=int16_t(positions[b]>>16);
            int64_t x2=int16_t(positions[c]),y2=int16_t(positions[c]>>16);
            int64_t det=(x1-x0)*(y2-y0)-(x2-x0)*(y1-y0);
            if(!cull || (det!=0 && !(cull==1 && det<0) && !(cull==2 && det>0))) {
                out.push_back(0x54000001);out.push_back(word);
            }
        }
    }
    in.push_back(0x600000a4);
    for(auto v:{oldbase,newbase,stream,2u,cull,count,cursor*4})in.push_back(v);
    for(auto v:coeff)in.push_back(v);
    for(auto v:{xc,yc,xs,ys,nc})in.push_back(v);
    for(unsigned i=0;i<256;i+=2)in.push_back(palette[i]|(uint32_t(palette[i+1])<<16));
    xc=oldproj[0];yc=oldproj[1];xs=oldproj[2];ys=oldproj[3];nc=oldproj[4];
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
static void surface(std::vector<uint32_t>& in,std::vector<uint32_t>& out) {
    in.push_back(0x4a000011);out.push_back(0x4a000011);
    for(unsigned i=0;i<17;i++){in.push_back(0);out.push_back(0);}
}
static void malformed_tests() {
    // Metadata errors must never issue a physical read.
    for(unsigned test=0;test<12;test++) {
        reset();std::vector<uint32_t> in,out,junk;surface(in,out);native(in,junk,test);
        unsigned p=19;
        switch(test) {
        case 0:in[p]=0x1000000;break;
        case 1:in[p]=3;break;
        case 2:in[p+1]=0xfffffc;break;
        case 3:in[p+2]|=1;break;
        case 4:in[p+3]=0;break;
        case 5:in[p+3]=4097;break;
        case 6:in[p+4]=3;break;
        case 7:in[p+5]=0;break;
        case 8:in[p+5]=4097;break;
        case 9:in[p+6]=in[p+2];break;
        case 10:in[p+35]=0;break;
        case 11:in[p+35]=0xffffffff;break;
        }
        auto before=bursts;run(in,out);
        if(bursts!=before){std::fprintf(stderr,"invalid metadata read memory test%u\n",test);std::abort();}
    }
    // Bounds reject a complete malformed mesh before any vertex expansion.
    for(unsigned test=0;test<7;test++) {
        reset();std::vector<uint32_t> in,out,junk;surface(in,out);native(in,junk,test);
        unsigned p=19,base=in[p+2]>>2;
        switch(test) {
        case 0:psram[base]=0;break;
        case 1:psram[base]=33|(1<<8);break;
        case 2:psram[base]=3|(65<<8);break;
        case 3:psram[base]|=0x1000000;break;
        case 4:in[p+6]=in[p+2]+4;break;
        case 5:psram[base+1]=in[p+5];break;
        case 6:psram[base+1]=0xffffffff;break;
        }
        auto before=bursts;run(in,out);
        if(bursts>before+1){std::fprintf(stderr,"malformed mesh over-read%u\n",test);std::abort();}
    }
}
static void descriptor_width_tests() {
    unsigned rejected=0; uint64_t cycles=0;
    // Every discarded high bit and alignment bit must remain sticky-invalid
    // through the rest of the descriptor, including otherwise legal fields.
    for (unsigned field=0;field<7;field++) {
        unsigned first=field==3 || field==5 ? 13 : field==4 ? 2 : field==6 ? 24 : 24;
        for (unsigned bit=first;bit<32;bit++) {
            reset(); std::vector<uint32_t> in,out,junk; surface(in,out); native(in,junk,rejected);
            in[19+field] |= uint32_t(1)<<bit;
            auto before=bursts; cycles+=run(in,out);
            if (bursts!=before) {std::fprintf(stderr,"discarded descriptor bit accepted field%u bit%u\n",field,bit);std::abort();}
            // A new complete descriptor must clear the sticky rejection.
            in.clear();out.clear();native(in,out,rejected);cycles+=run(in,out);rejected++;
        }
    }
    for (unsigned field : {0u,1u,2u,6u}) for (unsigned bit=0;bit<2;bit++) {
        reset();std::vector<uint32_t> in,out,junk;surface(in,out);native(in,junk,rejected);
        in[19+field] |= uint32_t(1)<<bit;auto before=bursts;cycles+=run(in,out);
        if(bursts!=before)std::abort();rejected++;
    }
    unsigned accepted=0;
    for (unsigned test=0;test<10;test++) {
        reset();std::vector<uint32_t> in,out;surface(in,out);native(in,out,test);
        unsigned p=19;
        if(test==0)in[p+5]=4096;
        if(test==1)in[p+3]=4096; // Exact end stops the job after its two blocks.
        if(test==2)in[p+6]=0x1000000;
        if(test==3 || test==4) {
            unsigned field=test-3,from=in[p+field]>>2,to=(0x1000000-4*in[p+5])>>2;
            for(unsigned i=0;i<in[p+5];i++)psram[to+i]=psram[from+i];
            in[p+field]=to*4;
        }
        if(test==5 || test==6) {
            unsigned from=in[p+2]>>2,words=(in[p+6]-in[p+2])/4,to=(1u<<22)-words;
            for(unsigned i=0;i<words;i++)psram[to+i]=psram[from+i];
            in[p+2]=to*4;in[p+6]=0x1000000;
            if(test==6)in[p+3]=4096; // Stream must reach bit24, never wrap.
        }
        if(test==7)in[p+5]=4096,in[p+3]=4096;
        if(test==8)in[p+6]=0x1000000,in[p+5]=4096;
        if(test==9) {
            // Highest legal source index4095 must not be truncated to12bits.
            unsigned count=in[p+5],offset=4096-count;
            for(unsigned field : {0u,1u}) {
                unsigned base=in[p+field]>>2;
                for(unsigned i=0;i<count;i++)psram[base+offset+i]=psram[base+i];
            }
            unsigned cursor=in[p+2]>>2;
            for(unsigned mesh=0;mesh<2;mesh++) {
                unsigned header=psram[cursor++],nv=header&255,nt=header>>8;
                for(unsigned i=0;i<nv;i++)psram[cursor+i*3]+=offset;
                cursor+=nv*3+nt;
            }
            in[p+5]=4096;
        }
        cycles+=run(in,out);accepted++;
    }
    std::printf("PASS descriptor width: rejected%u high/alignment bits, immediate valid recovery, accepted%u count/address/end boundaries (%llu cycles)\n",rejected,accepted,(unsigned long long)cycles);
}
static void abort_test(unsigned reset_kind,int beat) {
    reset();std::vector<uint32_t> in,out;surface(in,out);native(in,out,23);
    force_ar_hold=beat<0;
    size_t sent=0;unsigned unpublished=0;uint64_t cycles=0;
    while(true) {
        if(++cycles>50000)std::abort();
        tb.host_write=tb.host_publish=tb.consume=0;tb.enable=1;
        bool room=((tb.host_ptr+1)&63)!=tb.in_read;
        if(unpublished && (!room || unpublished>=13 || sent==in.size())) {
            tb.host_publish=1;unpublished=0;
        }else if(sent<in.size() && room){tb.host_write=1;tb.host_data=in[sent++];unpublished++;}
        // Ordinary state payloads can drain before native expansion begins.
        if(tb.read_ptr!=tb.out_write)tb.consume=1;
        tick();
        if((beat<0 && tb.mesh_arvalid) || (beat>=0 && mem_pending && mem_beat>=unsigned(beat)))break;
    }
    if(sent!=in.size() || unpublished)std::abort();
    tb.host_write=tb.host_publish=tb.consume=0;
    force_r_hold=true;
    if(reset_kind==0)tb.soft_reset=1;
    if(reset_kind==1)tb.ring_reset=1;
    if(reset_kind==2)tb.reset_n=0;
    tick();tick();
    tb.soft_reset=tb.ring_reset=0;tb.reset_n=1;
    force_ar_hold=force_r_hold=false;
    // Disable compute while the accepted burst completes: transport must drain.
    tb.enable=0;
    for(unsigned n=0;n<1000 && (mem_pending||tb.mesh_arvalid);n++)tick();
    if(mem_pending||tb.mesh_arvalid)std::abort();
    tb.enable=1;
    for(unsigned n=0;n<10;n++)tick();
    if(tb.busy || tb.read_ptr!=tb.out_write) {
        std::fprintf(stderr,"reset kind%u beat%d failed\n",reset_kind,beat);std::abort();
    }
    // Subsequent descriptor must not see discarded stale cache lines.
    in.clear();out.clear();surface(in,out);native(in,out,24);run(in,out);
}
static int64_t rne(int64_t value,unsigned bits) {
    int64_t floor=value>>bits;
    uint64_t frac=uint64_t(value)&((uint64_t(1)<<bits)-1),half=uint64_t(1)<<(bits-1);
    return floor+(frac>half || (frac==half && (floor&1)));
}
static std::array<int32_t,16> particle_context;
static void particle_bind(std::vector<uint32_t>& in) {
    in.push_back(0x61000010);
    for(auto w:particle_context)in.push_back(uint32_t(w));
}
static void particles(std::vector<uint32_t>& in,std::vector<uint32_t>& out,unsigned n,bool valid=true) {
    in.push_back(0x62000000+n*5);
    for(unsigned i=0;i<n;i++) {
        int32_t x=int32_t(rng()%100000001)-50000000;
        int32_t y=int32_t(rng()%70000001)-35000000;
        int32_t z=1048576+int32_t(rng()%267386881);
        int32_t scale=65536+int32_t(rng()%1100000);
        uint32_t rgba=(rng()&0xffff)|((i==0?0:i==1?255:rng()%256)<<16);
        for(auto w:{uint32_t(x),uint32_t(y),uint32_t(z),uint32_t(scale),rgba})in.push_back(w);
        unsigned alpha=(rgba>>16)&255;
        if(!valid || !alpha)continue;
        out.push_back(0x63000001);out.push_back(alpha);
        uint32_t depth=uint64_t(1ull<<46)/uint32_t(z);
        int16_t minx=32767,maxx=-32768,miny=32767,maxy=-32768;
        for(unsigned slot=0;slot<3;slot++) {
            int32_t px=x,py=y;
            if(slot) {px+=rne(int64_t(particle_context[slot==1?0:3])*scale,16);
                      py+=rne(int64_t(particle_context[slot==1?1:4])*scale,16);}
            int32_t rx=int32_t((int64_t(px)*depth)>>26),ry=int32_t((int64_t(py)*depth)>>26);
            int16_t sx=sat(rne(int64_t(rx)*particle_context[8]+(int64_t(particle_context[6])<<20),32));
            int16_t sy=sat(rne((int64_t(particle_context[7])<<20)-int64_t(ry)*particle_context[9],32));
            minx=std::min(minx,sx);maxx=std::max(maxx,sx);miny=std::min(miny,sy);maxy=std::max(maxy,sy);
            positions[slot]=uint16_t(sx)|(uint32_t(uint16_t(sy))<<16);loaded|=1u<<slot;
            for(auto w:{0x5c000006u,((rgba&0xffff)<<16)|slot,positions[slot],0x01000000u,
                uint32_t(particle_context[slot==1?12:11]),uint32_t(particle_context[slot==2?14:13]),depth})out.push_back(w);
        }
        if(maxx<0 || minx>=320*16 || maxy<0 || miny>=240*16)continue;
        unsigned cull=particle_context[15];
        int64_t x0=int16_t(positions[0]),y0=int16_t(positions[0]>>16);
        int64_t x1=int16_t(positions[1]),y1=int16_t(positions[1]>>16);
        int64_t x2=int16_t(positions[2]),y2=int16_t(positions[2]>>16);
        int64_t det=(x1-x0)*(y2-y0)-(x2-x0)*(y1-y0);
        if(cull && (det==0 || (cull==1 && det<0) || (cull==2 && det>0)))continue;
        out.push_back(0x54000001);out.push_back((1u<<5)|(2u<<10)|(cull<<15));
    }
}
static void particle_tests() {
    uint64_t cycles=0,words=0;
    for(unsigned group=0;group<80;group++) {
        reset();std::vector<uint32_t> in,out;
        surface(in,out);in[15]=out[15]=320u<<16;in[16]=out[16]=240u<<16;
        particle_context={0,98304,0,98304,0,0,160*65536,120*65536,160*65536,160*65536,
            16*65536,32768,557056,32768,557056,int32_t(group%3)};
        // Fractional viewport/basis and both rounding signs are deliberate.
        particle_context[0]=int32_t(rng()%201)-100;
        particle_context[4]=int32_t(rng()%201)-100;
        particle_context[6]+=int32_t(rng()%65536)-32768;
        particle_context[7]+=int32_t(rng()%65536)-32768;
        particle_bind(in);
        for(unsigned job=0;job<8;job++) {
            // Native descriptors overwrite their shared staging but never the
            // independently retained billboard context in RAM.
            if(TEST_NATIVE && job==4)native(in,out,group);
            particles(in,out,1+rng()%64);
        }
        cycles+=run(in,out);words+=out.size();
    }
    for(unsigned bad=0;bad<6;bad++) {
        reset();std::vector<uint32_t> in,out;
        surface(in,out);particle_context={0,98304,0,98304,0,0,160*65536,120*65536,
            160*65536,160*65536,16*65536,32768,557056,32768,557056,0};
        if(bad==0)particle_context[2]=1;
        if(bad==1)particle_context[5]=-1;
        if(bad==2)particle_context[10]=0;
        if(bad==3)particle_context[15]=3;
        if(bad==4)particle_context[12]=128*65536;
        if(bad==5)particle_context[11]=-1;
        particle_bind(in);particles(in,out,32,false);run(in,out);
    }
    std::printf("PASS particles640 mixed jobs, %llu exact output words, six malformed contexts, RNE/bbox/alpha/cull and preserved native context (%llu cycles)\n",(unsigned long long)words,(unsigned long long)cycles);
}



static void particle_bad_records() {
    for(unsigned bad=0;bad<8;bad++) {
        reset();std::vector<uint32_t> in,out;surface(in,out);
        particle_context={0,98304,0,98304,0,0,160*65536,120*65536,160*65536,160*65536,
            4*65536,32768,557056,32768,557056,0};
        std::array<uint32_t,5> record={0,0,64u*65536,65536,0x0080ffff};
        if(bad==0)record[3]=0;
        if(bad==1)record[3]=0xffffffff;
        if(bad==2)record[2]=16*65536-1;
        if(bad==3)record[2]=4096*65536+1;
        if(bad==4)record[4]|=0x01000000;
        if(bad==5)record[4]&=0x0000ffff;
        if(bad==6)particle_context[10]=128*65536;
        if(bad==7)record[2]=0xffffffff;
        particle_bind(in);in.push_back(0x62000005);for(auto w:record)in.push_back(w);
        run(in,out);
    }
    // Overflow after a valid first vertex cannot publish an indexed triangle.
    reset();std::vector<uint32_t> in,out;surface(in,out);
    particle_context={INT32_MAX,98304,0,98304,0,0,160*65536,120*65536,
        160*65536,160*65536,4*65536,32768,557056,32768,557056,0};
    particle_bind(in);
    for(auto w:{0x62000005u,0u,0u,64u*65536,uint32_t(INT32_MAX),0x0080ffffu})in.push_back(w);
    for(auto w:{0x63000001u,128u,0x5c000006u,0xffff0000u,(1920u<<16)|2560u,
        0x01000000u,32768u,32768u,0x01000000u})out.push_back(w);
    run(in,out);
    std::puts("PASS particle invalid record8/overflow1: no indexed triangle or hang");
}
static void particle_abort_tests() {
    for(unsigned reset_kind=0;reset_kind<3;reset_kind++)for(unsigned phase:{10u,25u,69u,74u,77u,79u}) {
        reset();std::vector<uint32_t> in,out;
        surface(in,out);in[15]=out[15]=320u<<16;in[16]=out[16]=240u<<16;
        particle_context={0,98304,0,98304,0,0,160*65536,120*65536,160*65536,160*65536,
            4*65536,32768,557056,32768,557056,0};
        particle_bind(in);
        for(auto w:{0x62000005u,0u,0u,64u*65536,65536u,0x0080ffffu})in.push_back(w);
        size_t sent=0;unsigned unpublished=0;uint64_t cycles=0;
        while(true) {
            if(++cycles>50000){std::fprintf(stderr,"particle reset phase%u timeout\n",phase);std::abort();}
            tb.host_write=tb.host_publish=tb.consume=0;tb.enable=1;
            bool room=((tb.host_ptr+1)&63)!=tb.in_read;
            if(unpublished && (!room || unpublished>=13 || sent==in.size())) {
                tb.host_publish=1;unpublished=0;
            }else if(sent<in.size() && room){tb.host_write=1;tb.host_data=in[sent++];unpublished++;}
            if(tb.read_ptr!=tb.out_write)tb.consume=1;
            tick();
            if(sent==in.size() && !unpublished && tb.worker_phase==phase)break;
        }
        tb.host_write=tb.host_publish=tb.consume=0;
        if(reset_kind==0)tb.soft_reset=1;
        if(reset_kind==1)tb.ring_reset=1;
        if(reset_kind==2)tb.reset_n=0;
        tick();tb.soft_reset=tb.ring_reset=0;tb.reset_n=1;tick();tick();
        if(tb.busy || tb.read_ptr!=tb.out_write){std::fprintf(stderr,"particle reset stuck kind%u phase%u\n",reset_kind,phase);std::abort();}
        // Reset invalidates the private context even if material is rebound.
        in.clear();out.clear();surface(in,out);particles(in,out,32,false);run(in,out);
        // A fresh context restores operation after every interrupted phase.
        in.clear();out.clear();surface(in,out);in[15]=out[15]=320u<<16;in[16]=out[16]=240u<<16;
        particle_bind(in);particles(in,out,64);run(in,out);
    }
    std::puts("PASS particle reset18 at divide/emit/alpha/basis/space/triangle; stale context rejects; fresh rebind recovers");
}

static void nearclip_boundaries() {
    uint64_t cycles=0, vertices=0;
    const std::array<int32_t,11> planes={INT32_MIN,INT32_MIN+1,-0x40000000,-257,-1,0,1,255,256,INT32_MAX-1,INT32_MAX};
    for(unsigned p=0;p<planes.size();p++) {
        reset(); std::vector<uint32_t> in,out; surface(in,out);
        nc=planes[p]; xc=160; yc=1920; xs=130; ys=1700;
        std::vector<int32_t> depths={INT32_MIN,INT32_MIN+1,-257,-1,0,1,255,256,257,INT32_MAX-1,INT32_MAX,nc};
        if(nc>INT32_MIN) depths.push_back(nc-1);
        if(nc<INT32_MAX) depths.push_back(nc+1);
        for(int32_t w:depths) {
            matrix.fill(0);matrix[3]=65535;matrix[7]=-65536;matrix[11]=w;
            object(in);raw(in,out,3);vertices+=3;
            uint32_t slot=rng()&31,rgb=rng()&65535,u=rng(),v=rng(),depth=rng();
            int32_t x=int32_t(rng()),y=int32_t(rng());
            for(uint32_t z:{0x56000008u,slot,uint32_t(x),uint32_t(y),uint32_t(w),u,v,rgb,depth})in.push_back(z);
            projected(out,slot,rgb,x,y,w,u,v,false,depth);vertices++;
        }
        if(TEST_NATIVE) {
            std::vector<uint32_t> native_expected;
            int32_t native_plane=planes[(p+5)%planes.size()];
            native(in,native_expected,p,native_plane);
            // The native descriptor ABI rejects nonpositive near planes.
            if(native_plane>0) out.insert(out.end(),native_expected.begin(),native_expected.end());
        }
        // Native projection must not replace the saved legacy near plane.
        raw(in,out,3);vertices+=3;
        cycles+=run(in,out);
    }
    std::printf("PASS signed near-plane boundaries: %zu planes, %llu explicit legacy vertices, %llu cycles\n",
                planes.size(),(unsigned long long)vertices,(unsigned long long)cycles);
}

int main(int argc,char**argv) {
    Verilated::commandArgs(argc,argv);
    if(TEST_PARTICLES) { particle_tests();particle_bad_records();particle_abort_tests(); }
    reset();uint64_t cycles=0;size_t words=0;
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
        if(TEST_NATIVE) native(in,out,batch);
        // The old object matrix and viewport must survive the native job.
        raw(in,out,3);
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
    if(TEST_NATIVE) {
        malformed_tests(); descriptor_width_tests();
        for(unsigned kind=0;kind<3;kind++)for(int beat: {-1,0,3,7})abort_test(kind,beat);
    }
    if(TEST_NATIVE) std::printf("PASS malformed19, reset12 (request-held/before-data/mid/last; soft/ring/media), postreset reuse\n");
    std::printf("PASS: 1920 mixed vertex batches, 7680 winding tests, %zu exact output words, wrap/backpressure/enable/reset (%llu cycles)\n",
        words,(unsigned long long)cycles);
    if(TEST_NATIVE) std::printf("Native jobs120, meshlets240, burst reads%llu, returnedbeats%llu, beatswhilepaused%llu\n",(unsigned long long)bursts,(unsigned long long)beats,(unsigned long long)paused_beats);nearclip_boundaries();tb.final();
}
