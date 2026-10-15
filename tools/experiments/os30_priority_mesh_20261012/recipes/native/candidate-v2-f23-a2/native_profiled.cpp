#include <sstream>
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
#include "cache_snapshot.hpp"
#include "Vtb_system_cpu_system.h"
#include "stall_observer.hpp"
#include "first_word_observer_v2.hpp"
#include "/home/alberto/Repos/openfpgaCore/docs/measurements/os30-psram32-20261009/priority-mesh-v1/validation/observer-v1/gpu_observer.hpp"

static Vtb_system *tb;
static uint64_t cycles=0, accum74=0;
static bool state74=false;
static bool cached_psram=false;
static void half(bool high) {
    // Run every delayed pin/capture event before the next fabric half-edge.
    const uint64_t deadline=Verilated::time()+5555;
    while(tb->eventsPending() && tb->nextTimeSlot()<=deadline) {
        const uint64_t next=tb->nextTimeSlot();
        Verilated::timeInc(next-Verilated::time());tb->eval();
    }
    Verilated::timeInc(deadline-Verilated::time());
    tb->clk_cpu=high;
    accum74+=74;
    while(accum74>=90){accum74-=90;state74=!state74;}
    tb->clk_74a=state74;tb->eval();
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
    auto &s=*tb->rootp->tb_system;
    if(cached_psram && (addr&0xbffe0000u)==0x13fa0000u) {
        uint32_t i=(addr&0x1ffffffu)/4;
        auto &lo=s.paired__DOT__chip0__DOT__mem;
        auto &hi=s.paired__DOT__chip1__DOT__mem;
        return (lo.exists(i)?lo.at(i):0)|uint32_t(hi.exists(i)?hi.at(i):0)<<16;
    }
    auto &m=s.__PVT__sdram_chip__DOT__mem;
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
    require(s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__engine__DOT__frontend__DOT__raw__DOT__state==0,"alias accepted while LOAD active");
    std::vector<uint32_t> v;
    for(uint32_t i=0;i<=len;++i) {
        uint32_t off=(a&1023)+i*4,slot=off/32,row=off/4%8,value=0;
        if(!(a&3)&&(a&1023)+4*(len+1)<=1024&&s.__PVT__geometry_view_valid&&slot<s.__PVT__geometry_view_count) {
            if(row<4)value=s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__engine__DOT__frontend__DOT__raw__DOT__compute__DOT__acc[row*32+slot];
            else if(row==4)value=s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__engine__DOT__frontend__DOT__raw__DOT__compute__DOT__g_context_bram__DOT__context_memory[32+slot];
            else value=s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__engine__DOT__frontend__DOT__raw__DOT__attributes[(row==5?32:row==6?0:64)+slot];
        }
        v.push_back(value);
    }
    return v;
}
struct Burst { uint32_t address,len,id,beat=0;std::vector<uint32_t> alias; };
struct Reader {
    std::string name; std::deque<Burst> q;
    uint64_t bursts=0,beats=0,errors=0,outstanding_cycles=0,ar_wait=0,overlay_bursts=0;
    void ar(uint32_t a,uint32_t len,uint32_t id) { q.push_back({a,len,id});++bursts;if((a&0xbffe0000u)==0x13fa0000u)++overlay_bursts; }
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
    cached_psram=cfg.count("cached_psram")&&n("cached_psram");
    tb=new Vtb_system;tb->clk_cpu=0;tb->clk_74a=0;tb->reset_n=0;tb->cpu_run=0;tb->vsync=0;
    tb->bd_gpu_wr=0;tb->bd_gpu_addr=0;tb->bd_gpu_data=0;
    tb->bd_geo_wr=0;tb->bd_geo_addr=0;tb->bd_geo_data=0;
    tb->bd_cram0_wr=0;tb->bd_cram0_addr=0;tb->bd_cram0_wdata=0;
    tb->bd_sdram_we=0;tb->bd_sdram_word_addr=0;tb->bd_sdram_wdata=0;tb->bd_sdram_rd_word_addr=0;
    tb->inj_burst_rd=0;tb->inj_burst_addr=0;tb->inj_burst_len=0;
    tb->inj_audio_valid=0;
    for(int i=0;i<10;++i)tick();
    auto &s=*tb->rootp->tb_system;
    auto sd=readfile(cfg.at("snapshot")+"/sdram.bin");require(sd.size()==64*1024*1024,"SDRAM size");
    // Equal pre-release preparation in legacy and direct-import models.
    uint32_t clear_offset=n("cleared_bufs_address")-0x10000000;
    uint32_t probe_offset=n("import_probe_address")-0x10000000;
    require(clear_offset<sd.size() && sd[clear_offset]==3,"cleared_bufs preimage");
    require(probe_offset+4<=sd.size() && le32(&sd[probe_offset])==2,"import probe preimage");
    for(unsigned i=0;i<153600;++i)
        require(sd[0x00200000+i]==0,"third color buffer not initialized");
    sd[clear_offset]=7;
    for(unsigned i=0;i<4;++i)sd[probe_offset+i]=0;
    uint32_t front_probe_offset=n("front_probe_address")-0x10000000;
    require(front_probe_offset+4<=sd.size() && (le32(&sd[front_probe_offset])==0 || le32(&sd[front_probe_offset])==2),"front probe preimage");
    uint32_t front_before=le32(&sd[front_probe_offset]);
    for(unsigned i=0;i<4;++i)sd[front_probe_offset+i]=0;
    uint32_t mesh_probe_offset=n("mesh_probe_address")-0x10000000;
    require(mesh_probe_offset+4<=sd.size() && (le32(&sd[mesh_probe_offset])==0 || le32(&sd[mesh_probe_offset])==2),"mesh probe preimage");
    uint32_t mesh_before=le32(&sd[mesh_probe_offset]);
    for(unsigned i=0;i<4;++i)sd[mesh_probe_offset+i]=0;
    uint32_t async_probe_offset=n("async_probe_address")-0x10000000;
    require(async_probe_offset+4<=sd.size() && (le32(&sd[async_probe_offset])==0 || le32(&sd[async_probe_offset])==2),"async probe preimage");
    const uint32_t async_before=le32(&sd[async_probe_offset]);
    for(unsigned i=0;i<4;++i)sd[async_probe_offset+i]=0;
    overlay(0x10000000,sd);
    std::ofstream ap(out+"/prepared-async.json");
    ap<<"{\"probe_address\":"<<n("async_probe_address")<<",\"probe_before\":"<<async_before<<",\"probe_after\":0}\n";
    ap.close();
    std::ofstream mp(out+"/prepared-mesh.json");
    mp<<"{\"probe_address\":"<<n("mesh_probe_address")<<",\"probe_before\":"<<mesh_before<<",\"probe_after\":0}\n";
    mp.close();
    std::ofstream prep(out+"/prepared-memory.json");
    prep<<"{\"cleared_bufs_address\":"<<n("cleared_bufs_address")
        <<",\"cleared_bufs_before\":3,\"cleared_bufs_after\":7,\"import_probe_address\":"
        <<n("import_probe_address")<<",\"probe_before\":2,\"probe_after\":0,"
          "\"third_buffer_zero_bytes_checked\":153600}\n";
    prep.close();
    std::ofstream fp(out+"/prepared-front.json");
    fp<<"{\"probe_address\":"<<n("front_probe_address")<<",\"probe_before\":"<<front_before<<",\"probe_after\":0}\n";
    fp.close();
    auto br=readfile(cfg.at("snapshot")+"/bram.bin");require(br.size()==32768,"BRAM size");
    for(size_t i=0;i<br.size()/4;++i)s.periph__DOT__ram__DOT__mem[i]=le32(&br[4*i]);
    auto cr=readfile(cfg.at("snapshot")+"/cram1.bin");require(cr.size()==16*1024*1024,"CRAM size");
    // Paired chips each hold one halfword at the same word address.
    // Skip zero entries: the checked pin model reads absent sparse keys as0.
    for(size_t i=0;i<cr.size()/4;++i) {
        uint32_t v=le32(&cr[4*i]);
        if(v&65535)s.paired__DOT__chip0__DOT__mem.atWrite(i)=v&65535;
        if(v>>16)s.paired__DOT__chip1__DOT__mem.atWrite(i)=v>>16;
    }
    if(cached_psram)for(unsigned off=0;off<0x20000;off+=4) {
        uint32_t v=le32(&sd[0x3fa0000+off]),i=(0x1fa0000+off)/4;
        if(v&65535)s.paired__DOT__chip0__DOT__mem.atWrite(i)=v&65535;
        if(v>>16)s.paired__DOT__chip1__DOT__mem.atWrite(i)=v>>16;
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
    require(s.__PVT__gpu__DOT__g_async_mesh__DOT__front__DOT__state==0,"AJ02 initializer state");
    require(s.__PVT__gpu__DOT__command_wrptr==s.__PVT__gpu__DOT__ring_rdptr,"AJ02 output initializer not empty");
    s.__PVT__gpu__DOT__upload_rdptr=n("ring_word_pointer");

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
    std::ofstream async_events(out+"/async-events.tsv"), async_words(out+"/worker-commands.bin",std::ios::binary), async_accepts(out+"/worker-words.tsv"), async_sources(out+"/job-sources.tsv");
    require(bool(async_events)&&bool(async_words)&&bool(async_accepts),"AJ02 trace files");
    async_accepts<<"cycle\tphase\toutput_pointer\tword\tfront_state\tserial\trepair\n";
    async_sources<<"cycle\tphase\tserial\tkind\taddress\tindex\tword\n";
    std::ofstream async_stores(out+"/snapshot-stores.tsv");
    async_stores<<"cycle\tphase\twriter\taddress\tword\n";
    std::string async_last;
    std::map<unsigned,std::map<unsigned,uint64_t>> async_overlap;
    async_events<<"cycle\tphase\tactive\tpending\tlegacy\taborting\tprotocol_error\tcompleted\tsuccess\toutput_pending\tsnapshot_detached\toutput_serial\tlease_pending\tlease_granted\tlease_release_pending\tlease_target\tlease_ticket\tgranted_ticket\tcontext_open\tcontext_next\trepair_pending\trepair_end_pending\trepair_token\trepair_ack\tepoch\tdescriptor_serial\tcompleted_serial\tsource_base\tvtx_w0\tcommand_source\tconfiguration\tflags_rgb\tuofs\tvofs\tcpu_vertex\tcpu_commands\talpha\towned_mask\tused_mask\tlive_count\tchecked_count\tclip_count\tcull_count\tstream_busy\tstream_ready\tstream_raw_ready\tfallback\tpacket_done\tpacket_error\tfetch_error\tjob_take\tctx_take\told_start\tmatrix_we\tmatrix_addr\tmatrix_data\tfront_context_pending\tfront_state\tfront_fault\tgpu_state\traw_state\tcull_owner\tctx_index\tctx_data\tjob_done\tview_active\traw_start\tpacket_snapshot_valid\tpacket_store_we\tpacket_store_addr\tpacket_store_wdata\tpacket_store_rdata\tpacket_raw_read_active\tpacket_state\tpacket_mask\tpacket_count\tsurvivor_accepted_result\tsurvivor_result_keep\tsurvivor_live_count\tsurvivor_check_indices\tjob_seq\tjob_vertex\tjob_vtx_w0\tjob_commands\tjob_control\tjob_flags\tjob_uofs\tjob_vofs\tjob_cpu_vertex\tjob_cpu_commands\tjob_alpha\n";

    std::ofstream vtx_calls(out+"/vtx-calls.tsv");
    vtx_calls<<"cycle\tphase\ta0\ta1\ta2\ts1\tra\n";

    std::map<unsigned,std::map<uint32_t,uint64_t>> phase_pcs,phase_gaps;
    os30_stalls::Observer stalls(cached_psram);
    os30_first_word::Observer firstword(cached_psram);
    gpu_bottleneck::Observer gpu_profile;
    gpu_profile.add_event(0x104d7480u,"gfx_run");
    gpu_profile.add_event(0x104d7580u,"gfx_end_frame");
    gpu_profile.add_event(0x104e8a40u,"sm64_perf_render_begin");
    gpu_profile.add_event(0x104e8c80u,"sm64_perf_render_end");
    gpu_profile.add_event(0x104e8b40u,"sm64_perf_step_begin");
    gpu_profile.add_event(0x104e9080u,"sm64_perf_step_end");
    gpu_profile.add_event(0x104e0540u,"gfx_gpu_present");
    gpu_profile.add_event(0x104d94c0u,"gpu_end_frame");
    gpu_profile.add_event(0x104413c0u,"game_loop_one_iteration");
    gpu_profile.add_event(0x104a7540u,"create_next_audio_buffer");
    std::ofstream boundaries(out+"/boundaries.tsv");
    for(uint64_t c=0;c<limit;++c) {
        ran=c+1;
        // Align synthetic traffic to measured renderer entry, so differing
        // cache-initializer latency cannot change the comparison's phase.
        tb->vsync=(start!=UINT64_MAX&&(c-start)%1500000==0);
        if(start!=UINT64_MAX&&c>=next_scan&&!scan) {
                scanbase=n("scan_base_halfword");
                tb->inj_burst_addr=scanbase;tb->inj_burst_len=n("scan_words");tb->inj_burst_rd=1;
                scan=true;scanbeat=0;++scanbursts;next_scan+=n("scan_period");
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
            require(!s.__PVT__geometry_load__DOT__async_mesh__DOT__active&&!s.__PVT__geometry_load__DOT__async_mesh__DOT__pending&&!s.__PVT__geometry_load__DOT__async_mesh__DOT__aborting&&!s.__PVT__geometry_load__DOT__async_mesh__DOT__repair_pending&&!s.__PVT__geometry_load__DOT__async_mesh__DOT__output_pending,"AJ02 unpublished job at frame boundary");
            require(!s.__PVT__geometry_load__DOT__async_mesh__DOT__protocol_error&&!s.__PVT__mesh_bridge_fault,"AJ02 protocol fault at frame boundary");
            if(entries==n("stop_entries")) {
                dump_cpu_arena(tb,cached_psram,out+"/surface-arena",n("surface_base"),n("surface_bytes"));
                dump_cpu_arena(tb,false,out+"/async-hint-stats",288701936u,16);
                dump_cpu_arena(tb,false,out+"/import-stats",n("import_stats_address"),32);
                dump_cpu_arena(tb,false,out+"/front-stats",n("front_stats_address"),64);
                dump_cpu_arena(tb,false,out+"/mesh-stats",n("mesh_stats_address"),n("mesh_stats_bytes"));
                dump_cpu_arena(tb,false,out+"/async-stats",n("async_stats_address"),n("async_stats_bytes"));
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
        if(audio_taken) {ra.ar(0x12c00000,15,0);next_audio+=1843;}
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
            if(start!=UINT64_MAX&&finished==UINT64_MAX) {++retired;++pcs[pc];gaps[pc]+=c-last_retire;++phase_pcs[entries][pc];phase_gaps[entries][pc]+=c-last_retire;}
            last_retire=c;
        }
        if(start!=UINT64_MAX&&finished==UINT64_MAX) {freeze+=tb->obs_freeze;hazard+=tb->obs_hazard;busy+=tb->obs_gpu_busy;}
        if(tb->obs_upload) {put32(uploads,tb->obs_upload_word);++uploadwords;}
        if(tb->obs_gpu_mmio)mmio<<c<<" GPU "<<uint32_t(tb->obs_gpu_mmio_addr)*4<<" "<<tb->obs_gpu_mmio_data<<"\n";
        if(tb->obs_geo_mmio)mmio<<c<<" GEO "<<uint32_t(tb->obs_geo_mmio_addr)*4<<" "<<tb->obs_geo_mmio_data<<"\n";
        stalls.sample(c,start==UINT64_MAX?0:entries,*tb,tb->vlSymsp->TOP__tb_system__cpu_sys__cpu);
        firstword.sample(c,start==UINT64_MAX?0:entries,*tb,tb->vlSymsp->TOP__tb_system,tb->vlSymsp->TOP__tb_system__cpu_sys,tb->vlSymsp->TOP__tb_system__cpu_sys__cpu);
        gpu_profile.sample(c,start==UINT64_MAX?0:entries,*tb,tb->vlSymsp->TOP__tb_system,tb->vlSymsp->TOP__tb_system__cpu_sys__cpu);
        if(tb->obs_retire&&tb->obs_retire_pc==273483328u) {
            auto &rf=*tb->vlSymsp->TOP__tb_system__cpu_sys__cpu.integer_RegFilePlugin_logic_regfile_fpga;
            vtx_calls<<c<<"\t"<<entries<<"\t"<<rf.asMem_ram[10]<<"\t"<<rf.asMem_ram[11]<<"\t"<<rf.asMem_ram[12]<<"\t"<<rf.asMem_ram[9]<<"\t"<<rf.asMem_ram[1]<<"\n";
        }
        // Pure passive AJ02 sampling immediately before this active edge.
        std::ostringstream async_row;
        async_row<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__active)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__pending)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__legacy)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__aborting)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__protocol_error)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__completed)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__success)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__output_pending)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__snapshot_detached)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__output_serial)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__lease_pending)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__lease_granted)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__lease_release_pending)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__lease_target)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__lease_ticket)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__granted_ticket)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__context_open)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__context_next)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__repair_pending)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__repair_end_pending)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__repair_token)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__repair_ack)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__epoch)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__descriptor_serial)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__completed_serial)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__source_base)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__vtx_w0)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__command_source)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__configuration)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__flags_rgb)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__uofs)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__vofs)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__cpu_vertex)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__cpu_commands)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__alpha)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__owned_mask)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__used_mask)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__live_count)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__checked_count)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__clip_count)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__cull_count)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__stream_busy)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__stream_ready)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__stream_raw_ready)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__fallback)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__packet_done)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__packet_error)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__fetch_error)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__job_take)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__ctx_take)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__old_start)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__matrix_we)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__matrix_addr)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__matrix_data)<<"\t"<<uint32_t(s.__PVT__gpu__DOT__g_async_mesh__DOT__front__DOT__context_pending)<<"\t"<<uint32_t(s.__PVT__gpu__DOT__g_async_mesh__DOT__front__DOT__state)<<"\t"<<uint32_t(s.__PVT__mesh_bridge_fault)<<"\t"<<uint32_t(s.__PVT__gpu__DOT__state)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__engine__DOT__frontend__DOT__raw__DOT__state)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__engine__DOT__frontend__DOT__cull_owner)<<"\t"<<uint32_t(s.__PVT__gpu__DOT__g_async_mesh__DOT__front__DOT__context_index)<<"\t"<<uint32_t(s.__PVT__gpu__DOT__g_async_mesh__DOT__data)<<"\t"<<uint32_t(s.__PVT__mesh_job_done)<<"\t"<<uint32_t(s.__PVT__alias_shim__DOT__active)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__raw_start)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__packet_snapshot_valid)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__packet_store_we)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__packet_store_addr)<<"\t"<<uint32_t((s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__packet__DOT__half_index==0?(s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__packet__DOT__x&65535u):s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__packet__DOT__half_index==1?(s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__packet__DOT__x>>16):s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__packet__DOT__half_index==2?(s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__packet__DOT__y&65535u):s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__packet__DOT__half_index==3?(s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__packet__DOT__y>>16):s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__packet__DOT__half_index==4?(s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__packet__DOT__w&65535u):s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__packet__DOT__half_index==5?(s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__packet__DOT__w>>16):s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__packet__DOT__half_index==6?(s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__packet__DOT__u&65535u):s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__packet__DOT__half_index==7?(s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__packet__DOT__u>>16):s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__packet__DOT__half_index==8?(s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__packet__DOT__v&65535u):s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__packet__DOT__half_index==9?(s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__packet__DOT__v>>16):s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__packet__DOT__half_index==10?s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__packet__DOT__rgb:s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__packet__DOT__half_index==11?(s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__packet__DOT__depth&65535u):s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__packet__DOT__half_index==12?(s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__packet__DOT__depth>>16):0u))<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__engine__DOT__ctrl__DOT__list_q)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__packet_raw_read_active)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__packet__DOT__state)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__packet__DOT__mask)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__packet__DOT__count)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__engine__DOT__ctrl__DOT__accepted_result)<<"\t"<<uint32_t((s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__engine__DOT__result_reject_class==0))<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__live_count)<<"\t"<<uint32_t(s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__engine__DOT__check_indices)<<"\t"<<uint32_t(s.__PVT__mesh_job_seq)<<"\t"<<uint32_t(s.__PVT__mesh_job_vertex)<<"\t"<<uint32_t(s.__PVT__mesh_job_vtx_w0)<<"\t"<<uint32_t(s.__PVT__mesh_job_commands)<<"\t"<<uint32_t(s.__PVT__mesh_job_control)<<"\t"<<uint32_t(s.__PVT__mesh_job_flags)<<"\t"<<uint32_t(s.__PVT__mesh_job_uofs)<<"\t"<<uint32_t(s.__PVT__mesh_job_vofs)<<"\t"<<uint32_t(s.__PVT__mesh_job_cpu_vertex)<<"\t"<<uint32_t(s.__PVT__mesh_job_cpu_commands)<<"\t"<<uint32_t(s.__PVT__mesh_job_alpha);
        if(async_row.str()!=async_last) {async_events<<c<<"\t"<<entries<<"\t"<<async_row.str()<<"\n";async_last=async_row.str();}
        if(s.__PVT__geometry_load__DOT__async_mesh__DOT__job_take) {
            for(unsigned kind=0;kind<2;++kind) {const uint32_t a=kind?s.__PVT__mesh_job_commands:s.__PVT__mesh_job_vertex;const unsigned count=kind?(s.__PVT__mesh_job_control&127)*2:((s.__PVT__mesh_job_vtx_w0>>12)&63)*6;
                require((a&3)==0&&a+count*4<=0x02000000,"AJ02 source bounds");
                for(unsigned i=0;i<count;++i) {const uint32_t at=(a/4)+i;auto &lo=s.paired__DOT__chip0__DOT__mem;auto &hi=s.paired__DOT__chip1__DOT__mem;const uint32_t w=(lo.exists(at)?lo.at(at):0)|uint32_t(hi.exists(at)?hi.at(at):0)<<16;
                    async_sources<<c<<"\t"<<entries<<"\t"<<s.__PVT__mesh_job_seq<<"\t"<<kind<<"\t"<<a<<"\t"<<i<<"\t"<<w<<"\n";
                }
            }
        }
        const bool async_emit=s.__PVT__gpu__DOT__g_async_mesh__DOT__front__DOT__emit;
        const unsigned async_pointer=s.__PVT__gpu__DOT__command_wrptr;
        const unsigned async_state=s.__PVT__gpu__DOT__g_async_mesh__DOT__front__DOT__state,async_serial=(s.__PVT__geometry_load__DOT__async_mesh__DOT__output_pending?s.__PVT__geometry_load__DOT__async_mesh__DOT__output_serial:s.__PVT__geometry_load__DOT__async_mesh__DOT__descriptor_serial),async_repair=s.__PVT__geometry_load__DOT__async_mesh__DOT__repair_pending;
        if(start!=UINT64_MAX) {
            const unsigned mask=(tb->obs_retire?1:0)|(tb->obs_gpu_busy?2:0)|(s.__PVT__geometry_load__DOT__async_mesh__DOT__active?4:0)|(s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__engine__DOT__frontend__DOT__raw__DOT__state?8:0)|(s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__engine__DOT__frontend__DOT__cull_owner?16:0)|(async_emit?32:0)|(s.__PVT__geometry_load__DOT__async_mesh__DOT__repair_pending?64:0)|(s.__PVT__gpu__DOT__state==6?128:0)|(s.__PVT__gpu__DOT__g_async_mesh__DOT__front__DOT__used==511?256:0)|(s.__PVT__geometry_load__DOT__async_mesh__DOT__lease_granted?512:0)|(s.__PVT__geometry_load__DOT__async_mesh__DOT__lease_pending?1024:0)|(s.__PVT__geometry_load__DOT__async_mesh__DOT__snapshot_detached?2048:0)|(s.__PVT__geometry_load__DOT__async_mesh__DOT__output_pending&&s.__PVT__geometry_load__DOT__async_mesh__DOT__lease_granted?4096:0);
            ++async_overlap[entries][mask];
        }
        const bool snapshot_packet_write=s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__packet_store_we;
        const bool snapshot_survivor_write=s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__engine__DOT__ctrl__DOT__accepted_result&&(s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__engine__DOT__result_reject_class==0);
        require(!(snapshot_packet_write&&snapshot_survivor_write),"snapshot double writer");
        const unsigned snapshot_writer=snapshot_survivor_write?2:(snapshot_packet_write?1:0);
        const unsigned snapshot_address=snapshot_survivor_write?(s.__PVT__geometry_load__DOT__async_mesh__DOT__live_count&63u):s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__packet_store_addr;
        half(true);++cycles;tb->inj_burst_rd=0;if(audio_taken)tb->inj_audio_valid=0;
        if(async_emit) {const uint32_t w=s.__PVT__gpu__DOT__g_async_mesh__DOT__front__DOT__queue[async_pointer&511];put32(async_words,w);async_accepts<<c<<"\t"<<entries<<"\t"<<async_pointer<<"\t"<<w<<"\t"<<async_state<<"\t"<<async_serial<<"\t"<<async_repair<<"\n";}
        if(snapshot_writer) {require(snapshot_address<512,"snapshot RAM bounds");async_stores<<c<<"\t"<<entries<<"\t"<<snapshot_writer<<"\t"<<snapshot_address<<"\t"<<s.__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__engine__DOT__ctrl__DOT__survivors[snapshot_address]<<"\n";}

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
    bool comma=false;for(Reader *r:{&ri,&rm,&rp,&r1,&ra}) {if(comma)summary<<",\n";comma=true;summary<<"\""<<r->name<<"\":{\"bursts\":"<<r->bursts<<",\"beats\":"<<r->beats<<",\"errors\":"<<r->errors<<",\"outstanding_cycles\":"<<r->outstanding_cycles<<",\"ar_wait_cycles\":"<<r->ar_wait<<",\"overlay_bursts\":"<<r->overlay_bursts<<",\"pending\":"<<r->q.size()<<"}";}
    summary<<"\n}}\n";std::printf("Native replay status=%d cycles=%llu PC=%08x\n",status,(unsigned long long)ran,lastpc);
    stalls.write(out+"/stalls");firstword.write(out+"/first-word");
    gpu_profile.write(out+"/gpu-profile");
    for(auto &phase:phase_pcs) {
        std::ofstream p(out+"/pc-phase-"+std::to_string(phase.first)+".csv");
        p<<"pc,retired,cycles_since_previous_retirement\n";
        for(auto entry:phase.second)p<<entry.first<<","<<entry.second<<","<<phase_gaps[phase.first][entry.first]<<"\n";
    }
    std::ofstream overlap(out+"/async-overlap.tsv");overlap<<"phase\tmask\tcycles\n";
    for(const auto &p:async_overlap)for(const auto &m:p.second)overlap<<p.first<<"\t"<<m.first<<"\t"<<m.second<<"\n";
    tb->final();delete tb;return status;
}catch(const std::exception &e){std::fprintf(stderr,"FATAL %s\n",e.what());return 10;}
