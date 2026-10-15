// Host-only texel validation and render-only residency idealization.
static inline uint32_t sdram_word(uint32_t widx);
static bool fast_tex=false;

static bool tagged_tex=false,concurrent_upload=false;
static std::vector<uint32_t> resident_words(0x1000000/4,0xdeaddead);
static std::vector<uint8_t> resident_known(0x1000000/4,0);
static uint32_t mesh_padding_seed=0xdeaddead;
static uint32_t upload_pointer=0;
static uint64_t upload_words=0,upload_cycles=0,upload_max=0;
static uint64_t upload_while_fill=0,fill_while_upload=0;
static uint64_t concurrent_start=0,concurrent_end=0;
static unsigned concurrent_count=0;
static bool concurrent_started=false,concurrent_done=false,concurrent_word_pending=false;
static uint64_t concurrent_word_start=0;
static const unsigned CONCURRENT_WORDS=256;
static const uint32_t CONCURRENT_BASE=0x1f0000;
static uint32_t upload_pattern(unsigned i){return 0x57ea1600u^(i*0x1020101u);}
static uint32_t cram_word(uint32_t a){
    if((a&3)||a+4>0x1000000)throw std::runtime_error("CRAM model range/alignment");
    auto &mem=(a&0x800000)?tb->rootp->tb_gpu__DOT__texture_memory__DOT__chip__DOT__mem1:tb->rootp->tb_gpu__DOT__texture_memory__DOT__chip__DOT__mem0;
    uint32_t h=(a&0x7fffff)>>1;
    return uint32_t(mem[h])|(uint32_t(mem[h+1])<<16);
}
static void cram_preload_word(uint32_t a,uint32_t w){
    if((a&3)||a+4>0x1000000)throw std::runtime_error("CRAM preload range/alignment");
    auto &mem=(a&0x800000)?tb->rootp->tb_gpu__DOT__texture_memory__DOT__chip__DOT__mem1:tb->rootp->tb_gpu__DOT__texture_memory__DOT__chip__DOT__mem0;
    uint32_t h=(a&0x7fffff)>>1;
    mem[h]=w&65535;mem[h+1]=w>>16;resident_words[a>>2]=w;resident_known[a>>2]=1;
}
static void verify_uploaded_word(uint32_t a,uint32_t w){
    if(cram_word(a)!=w)throw std::runtime_error("GPU MMIO upload readback mismatch");
    resident_words[a>>2]=w;resident_known[a>>2]=1;
}

static uint64_t texture_fills=0,texture_beats=0,texture_wait=0,texture_latency=0;
static uint64_t preload_batches=0,preload_bytes=0;
static bool texture_inflight=false;
static uint64_t texture_accepted=0;
static unsigned texture_beat=0;
static uint32_t expected_texture[4];
#include "texture_map.hpp"
static uint32_t original_texture_address(uint32_t a) {
    if(!fast_tex)return a&GV_SDRAM_MASK;
    for(auto p:texture_pages)if(a>=p.fast&&a<p.fast+16384)return p.original+(a-p.fast);
    throw std::runtime_error("Unmapped fast texture fetch");
}
static void texture_observe_tick() {
    if(!tb->reset_n)return;
    if(tb->obs_c1_errors)throw std::runtime_error("CRAM1 timing/protocol error");
    if(tb->obs_tex_rvalid) {
        if(!texture_inflight || texture_beat>=4)throw std::runtime_error("Unexpected texture response");
        if(tb->obs_tex_rdata!=expected_texture[texture_beat]) {
            fprintf(stderr,"texel mismatch beat%u got%08x expected%08x\n",texture_beat,tb->obs_tex_rdata,expected_texture[texture_beat]);
            throw std::runtime_error("Texture response differs from original SDRAM bytes");
        }
        if(bool(tb->obs_tex_rlast)!=(texture_beat==3))throw std::runtime_error("Texture burst length mismatch");
        ++texture_beat;
        if(obs_enabled)++texture_beats;
        if(tb->obs_tex_rlast) {
            texture_inflight=false;
            if(obs_enabled)texture_latency+=cycles-texture_accepted;
        }
    }
    if(tb->obs_tex_arvalid&&tb->obs_tex_arready) {
        if(texture_inflight || tb->obs_tex_arlen!=3)throw std::runtime_error("Invalid texture request");
        auto a=tagged_tex?(tb->obs_tex_araddr&0x3ffffff):original_texture_address(tb->obs_tex_araddr);
        if(tb->obs_c1_route&&tb->rootp->tb_gpu__DOT__gpu__DOT__gpu_tex_mem_up_inflight)++fill_while_upload;
        if(a&15)throw std::runtime_error("Unaligned texture line");
        if(tagged_tex&&tb->obs_c1_route){
            if(a+16>0x1000000)throw std::runtime_error("Tagged texture read exceeds model safe range");
            for(unsigned k=0;k<4;++k)expected_texture[k]=resident_words[(a>>2)+k];
        }else for(unsigned k=0;k<4;++k)expected_texture[k]=sdram_word((a>>2)+k);
        texture_accepted=cycles;texture_beat=0;texture_inflight=true;
        if(obs_enabled)++texture_fills;
    }
    if(obs_enabled&&tb->obs_tex_arvalid&&!tb->obs_tex_arready)++texture_wait;
}
static void preload_texture_pages() {
    if(!fast_tex||tagged_tex)return;
    throw std::runtime_error("Q2 replay requires tagged texture snapshots");
    if(texture_inflight)throw std::runtime_error("Texture preload with outstanding fill");
    // Captures apply MEM1 snapshots before each CMDS batch. Preserve that order.
    // No modeled upload cycles: this tests rendering with resident textures only.
    for(auto p:texture_pages) {
        if(p.fast+16384>0x1000000)throw std::runtime_error("Model die0 safe address limit exceeded");
        for(unsigned off=0;off<16384;off+=4) {
            auto w=sdram_word((p.original+off)>>2);
            auto h=(p.fast+off)>>1;
            tb->rootp->tb_gpu__DOT__texture_memory__DOT__chip__DOT__mem0[h]=w&65535;
            tb->rootp->tb_gpu__DOT__texture_memory__DOT__chip__DOT__mem0[h+1]=w>>16;
        }
        preload_bytes+=16384;
    }
    ++preload_batches;
}
static void texture_report() {
    printf("TEXTURE fills=%llu beats=%llu request_wait=%llu service_sum=%llu preloads=%llu host_preload_bytes=%llu cram_errors=%u\n",
        (unsigned long long)texture_fills,(unsigned long long)texture_beats,
        (unsigned long long)texture_wait,(unsigned long long)texture_latency,
        (unsigned long long)preload_batches,(unsigned long long)preload_bytes,tb->obs_c1_errors);
}
