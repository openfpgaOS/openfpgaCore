// Hybrid, deterministic-workload simulation. GPU + Pocket SDRAM are RTL;
// CPU time is qsim's cache/instruction estimate, never a hardware FPS claim.
#include <algorithm>
#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <string>
#include "Vtb_gpu.h"
#include "Vtb_gpu___024root.h"
#include "verilated.h"
extern "C" {
#include "env.h"
#include "cache.h"
#include "coupled.h"
}

static Vtb_gpu *tb;
static qenv *env;
static uint8_t *original_memory;
static bool active, initializing, cpu_rendering;
static uint64_t cycles, cpu_origin, next_vblank, vblanks, presents;
static uint64_t rd_polls, fence_polls, publications, upload_words;
static uint32_t ring_offset, last_fence, pending_token, last_submitted, depth_address;
static int display_idx, pending_idx = -1;
static FILE *events, *counters, *timeline, *publish_log;
static std::string folder;
extern "C" { uint32_t coupled_render_pc; }
struct Frame { uint64_t seq=0, submit=0, finish=0, present=0; uint32_t tick=0, idx=0; };
static std::map<uint32_t,Frame> frames;
enum { C_TOTAL,C_STARVE,C_BLOCK,C_VERTEX,C_CULL,C_OTHER,C_RASTER,C_FRAGMENT,
       C_FRAGMENT_STALL,C_OVERLAP,C_FRAGMENT_OVERLAP,C_GPU_BUSY,C_OUT_NONEMPTY,
       C_IN_NONEMPTY,C_CPU_RENDER,C_UPLOAD_WORD_SUM,C_OUTPUT_WORD_SUM,
       C_PRODUCTIVE,C_IDLE_EMPTY,C_PAYLOAD_EMPTY,C_WRITE,C_TEX_WAIT,C_TEX_INFLIGHT,C_COUNT };
static const char *names[C_COUNT]={"total","input_starved","output_blocked","vertex_math","cull_math",
    "worker_other","raster_active","fragment","fragment_stall","math_raster_overlap",
    "math_fragment_overlap","gpu_busy","output_nonempty","input_nonempty","cpu_render",
    "upload_words_sum","output_words_sum","fragment_productive_advance","idle_empty",
    "payload_empty","write_beats","texture_request_wait","texture_inflight"};
static std::array<uint64_t,C_COUNT> all{}, render{}, prev_bin{};
static uint32_t max_upload, max_output;
static uint8_t *memory();
static uint64_t texture_fills, texture_beats, texture_uploads, mmio_reads, mmio_writes;
static bool texture_inflight;
static unsigned texture_beat;
static uint32_t expected_texture[4];
static uint32_t cram_word(uint32_t address) {
    assert(!(address&3) && address+4<=0x200000);
    const auto &m=tb->rootp->tb_gpu__DOT__texture_memory__DOT__chip__DOT__mem0;
    return uint32_t(m[address>>1])|(uint32_t(m[(address>>1)+1])<<16);
}
static void texture_tick() {
    assert(!tb->obs_c1_errors);
    if(tb->obs_tex_rvalid) {
        assert(texture_inflight && texture_beat<4);
        assert(tb->obs_tex_rdata==expected_texture[texture_beat]);
        assert(bool(tb->obs_tex_rlast)==(texture_beat==3));
        ++texture_beat;++texture_beats;
        if(tb->obs_tex_rlast)texture_inflight=false;
    }
    if(tb->obs_tex_arvalid && tb->obs_tex_arready) {
        assert(!texture_inflight && tb->obs_tex_arlen==3);
        uint32_t a=tb->obs_tex_araddr&0x03ffffff;
        assert(!(a&15));
        for(unsigned k=0;k<4;++k) {
            if(tb->obs_c1_route)expected_texture[k]=cram_word(a+k*4);
            else memcpy(&expected_texture[k],memory()+a+k*4,4);
        }
        texture_inflight=true;texture_beat=0;++texture_fills;
    }
}
static uint8_t *memory() {
    return reinterpret_cast<uint8_t *>(&tb->rootp->tb_gpu__DOT__memory__DOT__sdram_chip__DOT__mem[0]);
}
static uint64_t cpu_time() { return env->cpu->model->cyc-cpu_origin; }
static uint32_t reg_read(unsigned r) { tb->reg_addr=r; tb->eval(); return tb->reg_rdata; }
static void event(const char *kind,uint32_t token,int idx) {
    auto f=frames.find(token);
    fprintf(events,"%s,%llu,%llu,%u,%u,%d\n",kind,(unsigned long long)cycles,
        (unsigned long long)(f==frames.end()?env->flips:f->second.seq),
        f==frames.end()?0:f->second.tick,token,idx);
    fflush(events);
}
static void snapshot(const char *kind,uint32_t token) {
    fprintf(counters,"%s,%u,%llu",kind,token,(unsigned long long)cycles);
    for(auto v:all) fprintf(counters,",%llu",(unsigned long long)v);
    for(auto v:render) fprintf(counters,",%llu",(unsigned long long)v);
    fprintf(counters,",%llu,%llu,%llu,%llu,%u,%u\n",(unsigned long long)rd_polls,
        (unsigned long long)fence_polls,(unsigned long long)publications,
        (unsigned long long)upload_words,max_upload,max_output);
    fflush(counters);
}
static void dump_frame(uint32_t token) {
    const auto &f=frames.at(token);
    char path[4096];
    snprintf(path,sizeof path,"%s/frame_%05llu.bin",folder.c_str(),(unsigned long long)f.seq);
    FILE *o=fopen(path,"wb"); assert(o);
    assert(f.idx<3 && depth_address && depth_address+153600<=env->cpu->sdram_size);
    assert(fwrite(memory()+(f.idx<<20),1,153600,o)==153600);
    assert(fwrite(memory()+depth_address,1,153600,o)==153600);
    fclose(o);
}
static void tick() {
    tb->clk=0; tb->eval();
    if(!initializing) {
        texture_tick();
        if(cycles>=next_vblank) {
            vblanks++;
            if(pending_idx>=0) {
                display_idx=pending_idx; pending_idx=-1; presents++;
                frames.at(pending_token).present=cycles;
                event("present",pending_token,display_idx);
            }
            next_vblank=((vblanks+1)*100000000ull+59)/60;
        }
        tb->slave_swap_pending=pending_idx>=0; tb->eval();
        std::array<uint64_t,C_COUNT> d{};
        d[C_TOTAL]=1;
        if(tb->overlap_worker_kind<5) d[C_STARVE+tb->overlap_worker_kind]=1;
        bool math=tb->overlap_worker_kind==2 || tb->overlap_worker_kind==3;
        d[C_RASTER]=tb->overlap_raster_active;
        d[C_FRAGMENT]=tb->overlap_fragment;
        d[C_FRAGMENT_STALL]=tb->overlap_fragment_stall;
        d[C_OVERLAP]=math && tb->overlap_raster_active;
        d[C_FRAGMENT_OVERLAP]=math && tb->overlap_fragment;
        d[C_GPU_BUSY]=tb->busy;
        d[C_OUT_NONEMPTY]=tb->overlap_output_words!=0;
        d[C_IN_NONEMPTY]=tb->overlap_upload_words!=0;
        d[C_CPU_RENDER]=cpu_rendering;
        d[C_UPLOAD_WORD_SUM]=tb->overlap_upload_words;
        d[C_OUTPUT_WORD_SUM]=tb->overlap_output_words;
        d[C_PRODUCTIVE]=tb->overlap_fragment_productive;
        d[C_IDLE_EMPTY]=tb->overlap_idle_empty;
        d[C_PAYLOAD_EMPTY]=tb->overlap_payload_empty;
        d[C_WRITE]=tb->overlap_write_take;
        d[C_TEX_WAIT]=tb->obs_tex_arvalid && !tb->obs_tex_arready;
        d[C_TEX_INFLIGHT]=texture_inflight;
        for(unsigned i=0;i<C_COUNT;i++) {all[i]+=d[i]; if(cpu_rendering)render[i]+=d[i];}
        max_upload=std::max(max_upload,unsigned(tb->overlap_upload_words));
        max_output=std::max(max_output,unsigned(tb->overlap_output_words));
    }
    tb->clk=1; tb->eval(); cycles++;
    if(initializing) return;
    if(cycles%10000==0) {
        fprintf(timeline,"%llu",(unsigned long long)cycles);
        for(unsigned i=0;i<C_COUNT;i++) {
            fprintf(timeline,",%llu",(unsigned long long)(all[i]-prev_bin[i])); prev_bin[i]=all[i];
        }
        fputc('\n',timeline);
    }
    if(tb->gpu_swap_req) {
        assert(pending_idx<0);
        pending_idx=tb->gpu_swap_idx; pending_token=tb->fence_reached;
        assert(pending_idx!=display_idx);
        auto &f=frames.at(pending_token); assert(f.idx==unsigned(pending_idx));
        f.finish=cycles; event("complete",pending_token,pending_idx);
        snapshot("complete",pending_token); dump_frame(pending_token);
    }
    if(tb->fence_reached!=last_fence) {
        last_fence=tb->fence_reached; event("fence",last_fence,pending_idx);
    }
}
static void advance(uint64_t to) {while(cycles<to)tick();}
static void reg_write(unsigned r,uint32_t v) {
    tb->reg_wr=1;tb->reg_addr=r;tb->reg_wdata=v;tick();tb->reg_wr=0;
}
extern "C" int coupled_active() {return active;}
extern "C" void coupled_sync() {
    if(!active)return;
    advance(cpu_time());
    env->hw_display=env->buf_display=display_idx;
    env->buf_ready=pending_idx;env->swap_kicked=pending_idx>=0;
}
extern "C" void coupled_render_enter() {
    if(!active)return;
    coupled_sync();cpu_rendering=true;event("render",0,env->buf_draw);
}
extern "C" void coupled_start(void *p,const char *out) {
    auto E=static_cast<qenv *>(p);
    if(active || !getenv("COUPLED_START_FRAME") || E->flips<strtoull(getenv("COUPLED_START_FRAME"),nullptr,0))return;
    assert(E->cpu->model && !E->lint && E->gpu && !E->vec_nframes && !E->cpu->sdram_uc);
    env=E;folder=out;
    const auto *s=qsim_syms_lookup(&E->syms,"gfx_run");assert(s);coupled_render_pc=s->addr;
    s=qsim_syms_lookup(&E->syms,"g_zbuf");assert(s);
    memcpy(&depth_address,qsim_mem_ptr(E->cpu,s->addr,4),4);depth_address&=0x03ffffff;
    events=fopen((folder+"/events.csv").c_str(),"w");
    counters=fopen((folder+"/counters.csv").c_str(),"w");
    timeline=fopen((folder+"/timeline.csv").c_str(),"w");
    publish_log=fopen((folder+"/publishes.csv").c_str(),"w");
    assert(events && counters && timeline && publish_log);
    fprintf(events,"kind,cycle,frame,game_tick,token,buffer\n");
    fprintf(publish_log,"cycle,frame,words,upload_queued,output_queued\n");
    fprintf(counters,"kind,token,cycle");
    for(auto name:names)fprintf(counters,",all_%s",name);
    for(auto name:names)fprintf(counters,",render_%s",name);
    fprintf(counters,",rdptr_polls,fence_polls,publications,uploaded_words,max_upload,max_output\n");
    fprintf(timeline,"cycle");for(auto name:names)fprintf(timeline,",%s",name);fputc('\n',timeline);
    uint32_t old_rd=gpu_mmio_read(E->gpu,0x10),old_fence=gpu_fence_reached(E->gpu);
    assert(!gpu_queued_words(E->gpu));
    std::string traffic="+memory_traffic="+std::string(getenv("MEMORY_TRAFFIC")?getenv("MEMORY_TRAFFIC"):"1");
    const char *args[]={"os30-f9-overlap",traffic.c_str()};Verilated::commandArgs(2,args);
    initializing=true;tb=new Vtb_gpu;
    tb->reset_n=0;tb->reg_wr=0;tb->bd_we=0;tb->slave_swap_pending=0;
    for(int i=0;i<20;i++)tick();tb->reset_n=1;
    for(int i=0;i<30000;i++)tick();
    reg_write(0,4);reg_write(0,1);reg_write(12,gpu_mmio_read(E->gpu,0x30));
    reg_write(0,16);reg_write(10,1);
    for(int i=0;i<2000;i++)tick();
    reg_write(1,0x02000001);reg_write(1,old_fence);reg_write(0,8);
    for(int i=0;i<1000;i++)tick();
    assert(tb->fence_reached==old_fence && !tb->busy);
    ring_offset=(old_rd-reg_read(4))&16383;
    original_memory=E->cpu->sdram;
    memcpy(memory(),original_memory,E->cpu->sdram_size);
    assert(tb->obs_c1_bcr_done && !tb->obs_c1_errors);
    // Existing fast-texture residency is copied only at the idle handoff.
    // All subsequent uploads traverse the actual GPU/controller MMIO path.
    uint32_t used=gpu_fast_tex_used(E->gpu);assert(used<=0x200000 && !(used&1));
    const uint8_t *tex=gpu_fast_tex_data(E->gpu);
    auto &cm=tb->rootp->tb_gpu__DOT__texture_memory__DOT__chip__DOT__mem0;
    for(unsigned a=0;a<used;a+=2)cm[a>>1]=uint16_t(tex[a])|(uint16_t(tex[a+1])<<8);
    reg_write(2,gpu_fast_tex_addr(E->gpu));reg_write(14,gpu_fast_tex_route(E->gpu));
    assert(reg_read(14)==gpu_fast_tex_route(E->gpu));
    // gpu_t retains original_memory: its functional writes cannot touch RTL RAM.
    E->cpu->sdram=E->scan=memory();
    cpu_origin=E->cpu->model->cyc;display_idx=E->hw_display;last_fence=old_fence;
    cycles=0;next_vblank=(100000000ull+59)/60;
    initializing=false;active=true;snapshot("start",0);
    fprintf(stderr,"coupled: start frame %llu; GPU 100 MHz; traffic %s; fixed game step\n",
        (unsigned long long)E->flips,traffic.c_str());
}
extern "C" uint32_t coupled_read(uint32_t off) {
    ++mmio_reads;
    coupled_sync();uint32_t v=reg_read(off/4);
    if(off==4 || off==16)v=(v+ring_offset)&16383;
    if(off==16)rd_polls++;
    if(off==24)fence_polls++;
    return v;
}
extern "C" void coupled_write(uint32_t off,uint32_t value) {
    ++mmio_writes;
    uint64_t to=cpu_time();if(to>cycles)advance(to-1);else env->cpu->model->cyc++;
    reg_write(off/4,value);
    if(off==4)upload_words++;
    if(off==0x3c)++texture_uploads;
    if(off==0 && (value&8)) {
        publications++;
        fprintf(publish_log,"%llu,%llu,%llu,%u,%u\n",(unsigned long long)cycles,
            (unsigned long long)env->flips,(unsigned long long)upload_words,
            tb->overlap_upload_words,tb->overlap_output_words);
    }
    if((reg_read(5)&0x300)!=0x100) {fprintf(stderr,"RTL ring overflow/lost CPU mode\n");abort();}
}
extern "C" void coupled_submit(uint32_t idx,uint32_t token,uint32_t game_tick) {
    if(!active)return;
    coupled_sync();frames[token]=Frame{env->flips,cycles,0,0,game_tick,idx};last_submitted=token;
    event("submit",token,idx);snapshot("submit",token);cpu_rendering=false;
}
extern "C" int coupled_acquire(int just,uint32_t token) {
    coupled_sync();if(just<0)return env->buf_draw;
    assert((int32_t)(tb->fence_reached-token)>=0);
    for(int i=0;i<3;i++)if(i!=display_idx && i!=pending_idx){env->buf_draw=i;return i;}
    abort();
}
extern "C" void coupled_video_timing(uint32_t addr) {
    coupled_sync();uint32_t words[8]={(uint32_t)vblanks,(uint32_t)presents,0,0,0,0,0,0};
    auto p=qsim_mem_ptr(env->cpu,addr,sizeof words);assert(p);memcpy(p,words,sizeof words);
}
extern "C" void coupled_finish() {
    if(!active)return;
    coupled_sync();snapshot("cpu_stop",last_submitted);
    uint64_t stop=cycles+100000000;
    while((tb->busy || pending_idx>=0 || (int32_t)(tb->fence_reached-last_submitted)<0) && cycles<stop)tick();
    assert(cycles<stop);snapshot("end",last_submitted);
    FILE *o=fopen((folder+"/coupled.json").c_str(),"w");assert(o);
    fprintf(o,"{\"cycles\":%llu,\"cpu_cycles\":%llu,\"frames\":%zu,\"presented\":%llu,"
        "\"clock_hz\":100000000,\"depth_address\":%u,\"fixed_game_step\":true,"
        "\"memory_errors\":0,\"ring_overflow\":false,\"frame_completion_checked\":true,"
        "\"texture_fills\":%llu,\"texture_beats\":%llu,\"texture_uploads\":%llu,"
        "\"mmio_reads\":%llu,\"mmio_writes\":%llu,\"cram1_errors\":%u}\n",
        (unsigned long long)cycles,(unsigned long long)cpu_time(),frames.size(),
        (unsigned long long)presents,depth_address,(unsigned long long)texture_fills,
        (unsigned long long)texture_beats,(unsigned long long)texture_uploads,
        (unsigned long long)mmio_reads,(unsigned long long)mmio_writes,tb->obs_c1_errors);fclose(o);
    for(const auto &kv:frames)assert(kv.second.finish && kv.second.present);
    fflush(events);fflush(timeline);fflush(publish_log);
}
extern "C" void coupled_close() {
    if(!active)return;
    env->cpu->sdram=original_memory;env->scan=original_memory;
    fclose(events);fclose(counters);fclose(timeline);fclose(publish_log);
    tb->final();delete tb;tb=nullptr;active=false;
}
