// Private native renderer/game/audio interval; stop at an original gfx entry.
// All snapshot/guest initialization still ends before cpu_run.
#define private public
#include "Vtb_system.h"
#include "Vtb_system__Syms.h"
#undef private
#include "Vtb_system___024root.h"
#include "Vtb_system_tb_system.h"
#include "Vtb_system_VexiiRiscv.h"
#include "Vtb_system_RegFileMem.h"
#include "Vtb_system_RegFileMem_1.h"
#include <verilated.h>
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <map>
#include <deque>
#include <string>
#include <vector>
#include <stdexcept>

static Vtb_system *tb;
static uint64_t cycles=0, accum74=0;
static bool state74=false;
static void half(bool high) {
    tb->clk_cpu=high;
    accum74+=74;
    while(accum74>=100) { accum74-=100; state74=!state74; }
    tb->clk_74a=state74; tb->eval();
}
static void tick() { half(false); half(true); ++cycles; }
static std::vector<uint8_t> readfile(const std::string &p) {
    std::ifstream f(p,std::ios::binary);
    if(!f) throw std::runtime_error("open "+p);
    return std::vector<uint8_t>(std::istreambuf_iterator<char>(f),{});
}
static uint32_t le32(const uint8_t *p) { return p[0]|uint32_t(p[1])<<8|uint32_t(p[2])<<16|uint32_t(p[3])<<24; }
static void put32(std::ofstream &f,uint32_t v) {
    char b[4]={char(v),char(v>>8),char(v>>16),char(v>>24)};f.write(b,4);
}
static void require(bool ok,const std::string &s) { if(!ok) throw std::runtime_error(s); }
static void overlay(uint32_t addr,const std::vector<uint8_t> &v) {
    require(!tb->cpu_run,"live host write");
    require(addr>=0x10000000&&uint64_t(addr)+v.size()<=0x14000000,"overlay range");
    auto &m=tb->rootp->tb_system->__PVT__sdram_chip__DOT__mem;
    for(size_t i=0;i<v.size();++i) {
        uint32_t off=addr-0x10000000+i, shift=(off&1)*8;
        m[off/2]=(m[off/2]&~(255<<shift))|uint16_t(v[i])<<shift;
    }
}
static uint32_t peek(uint32_t addr) {
    auto &m=tb->rootp->tb_system->__PVT__sdram_chip__DOT__mem;
    uint32_t i=(addr&0x3ffffff)/2;
    return m[i]|uint32_t(m[i+1])<<16;
}
static void dump_regs(const std::string &path,uint32_t pc) {
    auto &cpu=tb->vlSymsp->TOP__tb_system__cpu_sys__cpu;
    std::ofstream regs(path,std::ios::binary);
    for(int i=0;i<32;++i)put32(regs,i?cpu.integer_RegFilePlugin_logic_regfile_fpga->asMem_ram[i]:0);
    for(int i=0;i<32;++i)put32(regs,cpu.float_RegFilePlugin_logic_regfile_fpga->asMem_ram[i]);
    put32(regs,pc);
    put32(regs,(cpu.__PVT__FpuCsrPlugin_api_rm<<5)|(cpu.__PVT__FpuCsrPlugin_api_flags_NV<<4)|(cpu.__PVT__FpuCsrPlugin_api_flags_DZ<<3)|(cpu.__PVT__FpuCsrPlugin_api_flags_OF<<2)|(cpu.__PVT__FpuCsrPlugin_api_flags_UF<<1)|cpu.__PVT__FpuCsrPlugin_api_flags_NX);
}
static void dump_frame(const std::string &path,uint32_t color,uint32_t depth,uint32_t bytes) {
    std::ofstream f(path,std::ios::binary);
    for(uint32_t address:{color,depth})for(uint32_t off=0;off<bytes;off+=4)put32(f,peek(address+off));
}
static bool alias_address(uint32_t a) {return (a&0xbffffc00u)==0x13f80000u;}
static std::vector<uint32_t> alias_values(uint32_t a,uint32_t len) {
    auto &s=*tb->rootp->tb_system;
    require(s.__PVT__geometry_load__DOT__load__DOT__state==0,"alias accepted while LOAD active");
    std::vector<uint32_t> v;
    for(uint32_t i=0;i<=len;++i) {
        uint32_t off=(a&1023)+i*4,slot=off/32,row=off/4%8,value=0;
        if(!(a&3)&&(a&1023)+4*(len+1)<=1024&&s.__PVT__geometry_view_valid&&slot<s.__PVT__geometry_view_count) {
            if(row<4)value=s.__PVT__geometry_load__DOT__load__DOT__compute__DOT__acc[row*32+slot];
            else if(row==4)value=s.__PVT__geometry_load__DOT__load__DOT__compute__DOT__g_context_bram__DOT__context_memory[32+slot];
            else value=s.__PVT__geometry_load__DOT__load__DOT__attributes[(row==5?32:row==6?0:64)+slot];
        }
        v.push_back(value);
    }
    return v;
}
struct Burst { uint32_t address,len,id,beat=0;std::vector<uint32_t> alias; };
struct Reader {
    std::string name; std::deque<Burst> q;
    uint64_t bursts=0,beats=0,errors=0,outstanding_cycles=0,ar_wait=0;
    void ar(uint32_t a,uint32_t len,uint32_t id) { q.push_back({a,len,id});++bursts; }
    void r(uint32_t data,uint32_t id,bool last,uint64_t c) {
        ++beats;
        if(q.empty()) { ++errors;std::printf("%s orphan c=%llu\n",name.c_str(),(unsigned long long)c);return; }
        auto &b=q.front();uint32_t a=b.address+4*b.beat;
        uint32_t physical=a&~0x40000000u;
        bool memory=physical>=0x10000000&&physical<0x14000000;
        // The SDRAM bridge returns the containing 32-bit word; byte selection
        // belongs to the CPU. AR low bits do not shift the bus data lanes.
        uint32_t expected=memory?peek(a&~3u):data;
        if(alias_address(a)) {require(b.alias.size()==b.len+1,"unbound alias oracle "+name);expected=b.alias.at(b.beat);}
        if(id!=b.id || last!=(b.beat==b.len) || data!=expected) {
            if(errors++<8) std::printf("%s mismatch c=%llu addr=%08x beat=%u/%u data=%08x expect=%08x id=%u/%u last=%u\n",name.c_str(),(unsigned long long)c,a,b.beat,b.len,data,expected,id,b.id,last);
        }
        ++b.beat;if(last||b.beat>b.len) q.pop_front();
    }
};

int main(int argc,char **argv) try {
    require(argc==4,"usage: native_renderer CONFIG OUTPUT MAX_CYCLES");
    Verilated::commandArgs(argc,argv);setvbuf(stdout,nullptr,_IOLBF,0);
    std::map<std::string,std::string> cfg;std::ifstream config(argv[1]);std::string line;
    while(std::getline(config,line)) { auto p=line.find('='); if(p!=std::string::npos) cfg[line.substr(0,p)]=line.substr(p+1); }
    auto n=[&](const char *key)->uint32_t {return std::stoul(cfg.at(key),nullptr,0);};
    std::string out=argv[2];uint64_t limit=std::stoull(argv[3]);
    tb=new Vtb_system;tb->clk_cpu=0;tb->clk_74a=0;tb->reset_n=0;tb->cpu_run=0;tb->vsync=0;
    tb->bd_gpu_wr=0;tb->bd_gpu_addr=0;tb->bd_gpu_data=0;
    tb->bd_geo_wr=0;tb->bd_geo_addr=0;tb->bd_geo_data=0;
    tb->bd_cram0_wr=0;tb->bd_cram0_addr=0;tb->bd_cram0_wdata=0;
    tb->bd_sdram_we=0;tb->bd_sdram_word_addr=0;tb->bd_sdram_wdata=0;tb->bd_sdram_rd_word_addr=0;
    tb->inj_burst_rd=0;tb->inj_burst_addr=0;tb->inj_burst_len=0;
    tb->inj_audio_valid=0;
    for(int i=0;i<10;++i)tick();
    auto &s=*tb->rootp->tb_system;
    auto sd=readfile(cfg.at("snapshot")+"/sdram.bin");require(sd.size()==64*1024*1024,"SDRAM size");overlay(0x10000000,sd);
    auto br=readfile(cfg.at("snapshot")+"/bram.bin");require(br.size()==32768,"BRAM size");
    for(size_t i=0;i<br.size()/4;++i)s.periph__DOT__ram__DOT__mem[i]=le32(&br[4*i]);
    auto cr=readfile(cfg.at("snapshot")+"/cram1.bin");require(cr.size()==16*1024*1024,"CRAM size");
    for(size_t i=0;i<cr.size()/2;++i) {
        uint16_t v=cr[2*i]|uint16_t(cr[2*i+1])<<8;
        if(i<4194304)s.__PVT__chip__DOT__mem0[i]=v;else s.__PVT__chip__DOT__mem1[i-4194304]=v;
    }
    for(uint32_t i=0;i<n("segments");++i) {
        std::string k="segment"+std::to_string(i);
        overlay(std::stoul(cfg.at(k+"_address"),nullptr,0),readfile(cfg.at(k+"_path")));
    }
    // Private two-instruction BRAM reset trampoline, excluded from timing.
    uint32_t entry=n("entry_pc"),upper=(entry+0x800)&0xfffff000,lower=entry-upper;
    s.periph__DOT__ram__DOT__mem[0]=upper|(5<<7)|0x37;
    s.periph__DOT__ram__DOT__mem[1]=((lower&0xfff)<<20)|(5<<15)|0x67;
    tb->reset_n=1;
    for(uint32_t i=0;i<30000;++i)tick();
    require(tb->obs_bcr_ready,"BCR init");require(!tb->obs_cram_errors,"CRAM init errors");
    auto init=readfile(cfg.at("snapshot")+"/gpu-init-writes.bin");require(init.size()%8==0,"GPU init pairs");
    for(size_t i=0;i<init.size();i+=8) {
        tb->bd_gpu_addr=le32(&init[i])>>2;tb->bd_gpu_data=le32(&init[i+4]);tb->bd_gpu_wr=1;tick();
        tb->bd_gpu_wr=0;tick();
    }
    for(uint32_t i=0;i<100000 && (tb->obs_gpu_busy||tb->obs_fence!=n("last_fence"));++i)tick();
    require(!tb->obs_gpu_busy&&tb->obs_fence==n("last_fence"),"GPU initial fence drain");
    s.__PVT__gpu__DOT__ring_wr_addr=n("ring_word_pointer");s.__PVT__gpu__DOT__ring_wrptr=n("ring_word_pointer");
    s.__PVT__gpu__DOT__ring_rd_addr=n("ring_word_pointer");s.__PVT__gpu__DOT__ring_rdptr=n("ring_word_pointer");
    s.__PVT__periph__DOT__fb_display_idx=n("display_index");s.__PVT__periph__DOT__fb_ready_idx=n("display_index");
    s.__PVT__periph__DOT__fb_swap_pending=0;s.__PVT__periph__DOT__fb_swap_consumed_this_frame=0;
    s.__PVT__periph__DOT__color_mode_reg=n("color_mode");
    half(false);tb->cpu_run=1;tb->eval();
    std::ofstream uploads(out+"/commands.bin",std::ios::binary),mmio(out+"/mmio.tsv"),summary(out+"/result.json"),hist(out+"/pc.csv");
    require(bool(uploads)&&bool(mmio)&&bool(summary)&&bool(hist),"output files");
    Reader ri{"I"},rm{"MEM"},rp{"PER"},r1{"M1"},ra{"AUDIO"};
    struct HW {uint32_t a;uint16_t d;uint8_t mask;};
    std::deque<Burst> aw;std::deque<HW> writes;uint64_t write_errors=0,write_beats=0;
    bool scan=false;uint32_t scanbase=0,scanbeat=0;uint64_t scanerrors=0,scanbursts=0;
    uint64_t next_scan=30000,next_audio=30000;
    uint64_t start=UINT64_MAX,finished=UINT64_MAX,complete=UINT64_MAX,last_retire=0,retired=0,freeze=0,hazard=0,busy=0,uploadwords=0;
    uint32_t lastpc=0;std::map<uint32_t,uint64_t> pcs,gaps;
    uint64_t ran=0;std::string uart;int status=1;
    unsigned entries=0;
    std::ofstream boundaries(out+"/boundaries.tsv");
    for(uint64_t c=0;c<limit;++c) {
        ran=c+1;
        // Align synthetic traffic to measured renderer entry, so differing
        // cache-initializer latency cannot change the comparison's phase.
        tb->vsync=(start!=UINT64_MAX&&(c-start)%1666667==0);
        if(start!=UINT64_MAX&&c>=next_scan&&!scan) {
                scanbase=n("scan_base_halfword");
                tb->inj_burst_addr=scanbase;tb->inj_burst_len=n("scan_words");tb->inj_burst_rd=1;
                scan=true;scanbeat=0;++scanbursts;next_scan=c+n("scan_period");
        }
        if(start!=UINT64_MAX&&c>=next_audio&&!tb->inj_audio_valid)tb->inj_audio_valid=1;
        half(false);
        // Observe the next original call before its first instruction writes
        // architectural state. Do not count unclocked bus handshakes at stop.
        if(tb->obs_retire&&tb->obs_retire_pc==n("gfx_entry_pc")&&start!=UINT64_MAX) {
            ++entries;
            dump_regs(out+"/entry-"+std::to_string(entries)+".bin",tb->obs_retire_pc);
            std::string key="boundary"+std::to_string(entries-1)+"_color_address";
            dump_frame(out+"/frame-"+std::to_string(entries-1)+".bin",std::stoul(cfg.at(key),nullptr,0),n("depth_address"),n("framebuffer_bytes"));
            // Preserve every actual buffer for diagnosis if real presentation
            // timing changes the free-buffer choice from the reference.
            for(unsigned index=0;index<3;++index)
                dump_frame(out+"/frame-"+std::to_string(entries-1)+"-buffer"+std::to_string(index)+".bin",0x10000000+index*0x100000,n("depth_address"),n("framebuffer_bytes"));
            boundaries<<entries<<" "<<c<<" "<<retired<<" "<<uploadwords<<" "<<tb->obs_fence<<" "<<uint32_t(tb->obs_gpu_busy)<<"\n";
            require(!tb->obs_gpu_busy,"GPU not drained at subsequent gfx entry");
            if(entries==n("stop_entries")) {
                finished=complete=c;
                uint64_t errors=ri.errors+rm.errors+rp.errors+r1.errors+ra.errors+write_errors+scanerrors+tb->obs_cram_errors;
                status=errors||!writes.empty()||!aw.empty()||tb->obs_fence!=n("expected_fence")?3:0;
                break;
            }
        }
        // AXI accepts and architectural commits are sampled BEFORE the active edge.
#define TRACK(R,P,ID,RID) \
        if(tb->P##_arvalid&&tb->P##_arready)R.ar(tb->P##_araddr,tb->P##_arlen,ID); \
        if(tb->P##_rvalid&&tb->P##_rready)R.r(tb->P##_rdata,RID,tb->P##_rlast,c); \
        if(!R.q.empty())++R.outstanding_cycles; \
        if(tb->P##_arvalid&&!tb->P##_arready)++R.ar_wait;
        TRACK(ri,dbg_i,tb->dbg_i_arid,tb->dbg_i_rid)
        TRACK(rm,dbg_mem,tb->dbg_mem_arid,tb->dbg_mem_rid)
        TRACK(rp,dbg_per,0,0)
        if(tb->dbg_m1_arvalid&&tb->dbg_m1_arready) {
            r1.ar(tb->dbg_m1_araddr,tb->dbg_m1_arlen,0);
            if(alias_address(tb->dbg_m1_araddr)) {
                auto v=alias_values(tb->dbg_m1_araddr,tb->dbg_m1_arlen);r1.q.back().alias=v;
                bool found=false;
                for(auto *r:{&ri,&rm,&rp})for(auto &b:r->q)
                    if(b.address==tb->dbg_m1_araddr&&b.len==tb->dbg_m1_arlen&&b.alias.empty()) {require(!found,"duplicate alias pending");b.alias=v;found=true;}
                require(found,"alias M1 without CPU request");
            }
        }
        if(tb->dbg_m1_rvalid&&tb->dbg_m1_rready)r1.r(tb->dbg_m1_rdata,0,tb->dbg_m1_rlast,c);
        if(!r1.q.empty())++r1.outstanding_cycles;
        if(tb->dbg_m1_arvalid&&!tb->dbg_m1_arready)++r1.ar_wait;
#undef TRACK
        bool audio_taken=tb->inj_audio_valid&&tb->obs_audio_ready;
        if(audio_taken) {ra.ar(0x12c00000,15,0);next_audio=c+2048;}
        if(tb->obs_audio_rvalid)ra.r(tb->obs_audio_rdata,0,tb->obs_audio_rlast,c);
        if(tb->dbg_aw_valid&&tb->dbg_aw_ready)aw.push_back({tb->dbg_aw_addr,tb->dbg_aw_len,0});
        if(tb->dbg_w_valid&&tb->dbg_w_ready) {
            ++write_beats;
            if(aw.empty())++write_errors;
            // sdram_slave uses AW[25:2], with WSTRB selecting byte lanes.
            // A halfword CPU store may retain address bit1 in AW.
            else {auto &a=aw.front();uint32_t h=((a.address+4*a.beat)&0x3fffffc)/2;
                writes.push_back({h,uint16_t(tb->dbg_w_data),uint8_t(~tb->dbg_w_strb&3)});
                writes.push_back({h+1,uint16_t(tb->dbg_w_data>>16),uint8_t((~tb->dbg_w_strb>>2)&3)});
                if(bool(tb->dbg_w_last)!=(a.beat==a.len))++write_errors;
                if(++a.beat>a.len)aw.pop_front();
            }
        }
        if(tb->obs_retire) {
            uint32_t pc=tb->obs_retire_pc;lastpc=pc;
            if(start==UINT64_MAX&&pc==n("gfx_entry_pc")) {
                start=c;next_scan=c+30000;next_audio=c+30000;
                entries=1;boundaries<<entries<<" "<<c<<" 0 0 "<<tb->obs_fence<<" "<<uint32_t(tb->obs_gpu_busy)<<"\n";
                std::printf("GFX_ENTRY c=%llu\n",(unsigned long long)c);
                auto &cpu=tb->vlSymsp->TOP__tb_system__cpu_sys__cpu;
                std::ofstream regs(out+"/entry-regs.bin",std::ios::binary);
                for(int i=0;i<32;++i)put32(regs,i?cpu.integer_RegFilePlugin_logic_regfile_fpga->asMem_ram[i]:0);
                for(int i=0;i<32;++i)put32(regs,cpu.float_RegFilePlugin_logic_regfile_fpga->asMem_ram[i]);
                put32(regs,pc);
                put32(regs,(cpu.__PVT__FpuCsrPlugin_api_rm<<5)|(cpu.__PVT__FpuCsrPlugin_api_flags_NV<<4)|(cpu.__PVT__FpuCsrPlugin_api_flags_DZ<<3)|(cpu.__PVT__FpuCsrPlugin_api_flags_OF<<2)|(cpu.__PVT__FpuCsrPlugin_api_flags_UF<<1)|cpu.__PVT__FpuCsrPlugin_api_flags_NX);
            }
            if(start!=UINT64_MAX&&finished==UINT64_MAX) {++retired;++pcs[pc];gaps[pc]+=c-last_retire;}
            last_retire=c;
        }
        if(start!=UINT64_MAX&&finished==UINT64_MAX) {freeze+=tb->obs_freeze;hazard+=tb->obs_hazard;busy+=tb->obs_gpu_busy;}
        if(tb->obs_upload) {put32(uploads,tb->obs_upload_word);++uploadwords;}
        if(tb->obs_gpu_mmio)mmio<<c<<" GPU "<<uint32_t(tb->obs_gpu_mmio_addr)*4<<" "<<tb->obs_gpu_mmio_data<<"\n";
        if(tb->obs_geo_mmio)mmio<<c<<" GEO "<<uint32_t(tb->obs_geo_mmio_addr)*4<<" "<<tb->obs_geo_mmio_data<<"\n";
        half(true);++cycles;tb->inj_burst_rd=0;if(audio_taken)tb->inj_audio_valid=0;
        if(tb->dbg_model_wr_evt) {
            if(writes.empty())++write_errors;
            else {auto e=writes.front();writes.pop_front();uint32_t mask=((e.mask&1)?0:255)|((e.mask&2)?0:65280);
                if(e.a!=tb->dbg_model_wr_hw_addr||e.mask!=tb->dbg_model_wr_dqm||((e.d^tb->dbg_model_wr_data)&mask)) {
                    if(write_errors++<8)std::printf("WRITE mismatch c=%llu addr=%x/%x data=%x/%x mask=%u/%u\n",(unsigned long long)c,e.a,tb->dbg_model_wr_hw_addr,e.d,tb->dbg_model_wr_data,e.mask,tb->dbg_model_wr_dqm);
                }
            }
        }
        if(tb->inj_burst_data_valid) {
            if(!scan||tb->inj_burst_data!=peek(0x10000000+(scanbase/2+scanbeat)*4))++scanerrors;
            ++scanbeat;
        }
        if(tb->inj_burst_data_done) {if(!scan||scanbeat!=n("scan_words"))++scanerrors;scan=false;}
        if(tb->uart_tx_dv) {char ch=tb->uart_tx_byte;uart+=ch;std::putchar(ch);}
        uint64_t errors=ri.errors+rm.errors+rp.errors+r1.errors+ra.errors+write_errors+scanerrors+tb->obs_cram_errors;
        if(c%500000==0)std::printf("c=%llu pc=%08x retired=%llu uploads=%llu fence=%u errors=%llu\n",(unsigned long long)c,lastpc,(unsigned long long)retired,(unsigned long long)uploadwords,tb->obs_fence,(unsigned long long)errors);
        if(errors>30) {status=4;break;}
        if(uart.find("NATIVE_RENDER_FAIL")!=std::string::npos&&uart.find('\n')!=std::string::npos){status=5;break;}
        if(c-last_retire>500000) {status=6;break;}
    }
    std::ofstream frame(out+"/frame.bin",std::ios::binary);
    for(auto spec:{std::make_pair(n("framebuffer_address"),n("framebuffer_bytes")),std::make_pair(n("depth_address"),n("depth_bytes"))})
        for(uint32_t off=0;off<spec.second;off+=4)put32(frame,peek(spec.first+off));
    hist<<"pc,retired,cycles_since_previous_retirement\n";
    for(auto p:pcs)hist<<p.first<<","<<p.second<<","<<gaps[p.first]<<"\n";
    summary<<"{\n\"status\":"<<status<<",\n\"cycles\":"<<ran<<",\n\"entry_cycle\":"<<start<<",\n\"finished_cycle\":"<<finished<<",\n\"complete_cycle\":"<<complete<<",\n\"retired\":"<<retired<<",\n\"freeze_cycles\":"<<freeze<<",\n\"hazard_cycles\":"<<hazard<<",\n\"gpu_busy_cycles\":"<<busy<<",\n\"upload_words\":"<<uploadwords<<",\n\"write_beats\":"<<write_beats<<",\n\"write_errors\":"<<write_errors<<",\n\"scan_bursts\":"<<scanbursts<<",\n\"scan_errors\":"<<scanerrors<<",\n\"cram_errors\":"<<tb->obs_cram_errors<<",\n\"fence\":"<<tb->obs_fence<<",\n\"readers\":{\n";
    bool comma=false;for(Reader *r:{&ri,&rm,&rp,&r1,&ra}) {if(comma)summary<<",\n";comma=true;summary<<"\""<<r->name<<"\":{\"bursts\":"<<r->bursts<<",\"beats\":"<<r->beats<<",\"errors\":"<<r->errors<<",\"outstanding_cycles\":"<<r->outstanding_cycles<<",\"ar_wait_cycles\":"<<r->ar_wait<<",\"pending\":"<<r->q.size()<<"}";}
    summary<<"\n}}\n";std::printf("Native replay status=%d cycles=%llu PC=%08x\n",status,(unsigned long long)ran,lastpc);
    tb->final();delete tb;return status;
}catch(const std::exception &e){std::fprintf(stderr,"FATAL %s\n",e.what());return 10;}
