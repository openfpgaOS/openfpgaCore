// SPDX-License-Identifier: Apache-2.0
// Replay qsim captures through the configured GPU; compare full memory and fences.
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
#include <algorithm>

#include "Vtb_gpu.h"
#include "Vtb_gpu___024root.h"
#include "verilated.h"

#include "gpuvec.h"
#include <stdexcept>

static Vtb_gpu *tb;
static uint64_t cycles, busy_cycles;
#include "observer.hpp"
#include "texture_observer.hpp"
#include "mesh_observer.hpp"

static void tick(int n = 1) {
    for (int i = 0; i < n; i++) {
        tb->clk = 0; tb->eval();
        observe_tick();
        texture_observe_tick();
        mesh_observe_tick();
        tb->clk = 1; tb->eval();
        cycles++;
        busy_cycles += tb->busy;
    }
}

static inline uint32_t sdram_word(uint32_t widx) {
    uint32_t value;
    memcpy(&value, &tb->rootp->tb_gpu__DOT__memory__DOT__sdram_chip__DOT__mem[(widx & 0xFFFFFF)*2], sizeof value);
    return value;
}
static uint8_t mem_rd8(uint32_t a) {
    a &= GV_SDRAM_MASK;
    return (uint8_t)(sdram_word(a >> 2) >> ((a & 3) * 8));
}
static void mem_wr8(uint32_t a, uint8_t v) {
    a &= GV_SDRAM_MASK;
    auto &half = tb->rootp->tb_gpu__DOT__memory__DOT__sdram_chip__DOT__mem[a >> 1];
    unsigned sh = (a & 1) * 8;
    half = (half & ~(0xffu << sh)) | (uint16_t(v) << sh);
}

static void mmio_write(uint32_t reg, uint32_t val) {
    tb->reg_wr = 1; tb->reg_addr = reg; tb->reg_wdata = val;
    tick();
    tb->reg_wr = 0;
}
static uint32_t mmio_read(uint32_t reg) {
    tb->reg_addr = reg; tb->eval();
    return tb->reg_rdata;
}


static void drain_upload(){
    uint64_t start=cycles;
    while(mmio_read(15)&1u){
        if(cycles-start>1000000)throw std::runtime_error("MMIO upload timeout");
        tick();
    }
}
static void live_mmio(uint32_t off,uint32_t val){
    if(off!=0x08&&off!=0x38&&off!=0x3c)throw std::runtime_error("Unexpected captured MMIO register");
    if(off==0x08){drain_upload();upload_pointer=val&0x3fffff;mmio_write(2,val);}
    else if(off==0x38){
        drain_upload();
        if(tb->busy||texture_inflight)throw std::runtime_error("Captured route change while GPU busy");
        mmio_write(14,val);
    }else{
        drain_upload();
        uint32_t a=upload_pointer*4;
        if(a+4>0x1000000)throw std::runtime_error("Live upload exceeds model safe range");
        uint64_t start=cycles;
        if(texture_inflight)++upload_while_fill;
        mmio_write(15,val);drain_upload();
        uint64_t duration=cycles-start;upload_cycles+=duration;upload_max=std::max(upload_max,duration);++upload_words;
        verify_uploaded_word(a,val);upload_pointer=(upload_pointer+1)&0x3fffff;
    }
}
// Called only while the host is otherwise waiting for a published batch.
// Real MMIO strobes and status readback; no controller backdoor for uploads.
static void concurrency_step(){
    if(!concurrent_upload||concurrent_done)return;
    if(concurrent_word_pending&&!(mmio_read(15)&1u)){
        uint64_t duration=cycles-concurrent_word_start;
        upload_cycles+=duration;upload_max=std::max(upload_max,duration);++upload_words;
        verify_uploaded_word(CONCURRENT_BASE+4*(concurrent_count-1),upload_pattern(concurrent_count-1));
        concurrent_word_pending=false;
        if(concurrent_count==CONCURRENT_WORDS){concurrent_done=true;concurrent_end=cycles;return;}
    }
    if(!concurrent_started){
        if(!texture_inflight)return;
        concurrent_started=true;concurrent_start=cycles;
        mmio_write(2,CONCURRENT_BASE/4);
    }
    if(!concurrent_word_pending){
        if(texture_inflight)++upload_while_fill;
        concurrent_word_start=cycles;
        mmio_write(15,upload_pattern(concurrent_count++));
        concurrent_word_pending=true;
    }
}
static void upload_report(){
    if(concurrent_upload&&(!concurrent_done||!upload_while_fill||!fill_while_upload))
        throw std::runtime_error("Concurrent test did not exercise both overlap directions");
    printf("UPLOAD words=%llu upload_cycles=%llu word_cycles_max=%llu uploads_while_fill=%llu fills_while_upload=%llu concurrent_span=%llu readback_words=%llu\n",
        (unsigned long long)upload_words,(unsigned long long)upload_cycles,(unsigned long long)upload_max,
        (unsigned long long)upload_while_fill,(unsigned long long)fill_while_upload,
        (unsigned long long)(concurrent_end-concurrent_start),(unsigned long long)upload_words);
}

static bool wait_idle(uint64_t timeout) {
    int quiet = 0;
    for (uint64_t t = 0; t < timeout; t++) {
        concurrency_step();
        tick();
        if (!concurrent_word_pending && !tb->busy && (mmio_read(5) & 0x8) == 0) {
            if (++quiet >= 64) return true;
        } else {
            quiet = 0;
        }
    }
    return false;
}

static void hard_reset() {
    delete tb; tb = new Vtb_gpu;
    tb->critical_enable=0;
    tb->reset_n = 0; tb->reg_wr = 0; tb->bd_we = 0; tb->slave_swap_pending = 0;
    tick(20);
    tb->reset_n = 1;
    tick(30000);
    mmio_write(0, 4);               // ring reset
    mmio_write(14,fast_tex?1:0);     // same one-cycle slot; enable was a no-op
    mmio_write(12, GV_DEFAULT_PALOOKUP_BASE);
    mmio_write(0, 16);              // select CPU ring transport
    if ((mmio_read(5) & 0x100) == 0) {
        fprintf(stderr, "CPU ring mode not selected\n");
        exit(2);
    }
    mmio_write(10, 1);              // tex flush
    tick(2000);                     // tex cache + recip cache init walks
    if(!tb->obs_c1_bcr_done || bool(tb->obs_c1_route)!=fast_tex)throw std::runtime_error("CRAM1 init/route failed");
}

static int run_vector(const char *vec_path, const char *out_path) {
    FILE *f = fopen(vec_path, "rb");
    if (!f) { perror(vec_path); return 2; }
    std::vector<uint8_t> buf;
    {
        fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
        buf.resize((size_t)n);
        if (n && fread(buf.data(), 1, (size_t)n, f) != (size_t)n) { fclose(f); return 2; }
        fclose(f);
    }
    FILE *out = fopen(out_path, "wb");
    if (!out) { perror(out_path); return 2; }

    hard_reset();
    // Unuploaded physical words are poison, never silently assumed zero.
    for(uint32_t a=0;a<0x1000000;a+=4)cram_preload_word(a,mesh_padding_seed);
    std::fill(resident_known.begin(),resident_known.end(),0);
    // Zero the whole 64 MB SDRAM model so both sides start identical.
    for (uint32_t i = 0; i < 0x2000000; i++) tb->rootp->tb_gpu__DOT__memory__DOT__sdram_chip__DOT__mem[i] = 0;
    for (uint32_t i = 0; i < 65536; i++) tb->rootp->tb_gpu__DOT__sram_mem[i] = 0;

    const uint64_t start_cycles = cycles, start_busy = busy_cycles;
    obs_enabled = true;tb->critical_enable=1;
    size_t pos = 0;
    bool ended = false;
    auto require = [&](size_t n) {
        if (n > buf.size() - pos) throw std::runtime_error("Truncated GPU vector");
    };
    auto rd32 = [&](void) -> uint32_t {
        require(4);
        uint32_t v = (uint32_t)buf[pos] | ((uint32_t)buf[pos+1] << 8)
                   | ((uint32_t)buf[pos+2] << 16) | ((uint32_t)buf[pos+3] << 24);
        pos += 4; return v;
    };
    int rc = 0;
    uint32_t batches = 0;
    while (pos + 4 <= buf.size()) {
        uint32_t tag = rd32();
        if(tag==GV_TAG('T','E','X','1')){
            if(!tagged_tex)throw std::runtime_error("TEX1 requires +tagged_tex=1");
            uint32_t a=rd32(),n=rd32();
            if((a&3)||(n&3)||uint64_t(a)+n>0x1000000||texture_inflight||tb->busy)
                throw std::runtime_error("Invalid TEX1 residency snapshot");
            require(n);
            for(uint32_t i=0;i<n;i+=4)cram_preload_word(a+i,rd32());
        }else if(tag==GV_TAG('M','M','I','O')){
            if(!tagged_tex)throw std::runtime_error("MMIO tag requires +tagged_tex=1");
            uint32_t off=rd32(),v=rd32();live_mmio(off,v);
        }else if (tag == GV_TAG_MEM) {
            uint32_t addr = rd32(), len = rd32();
            require((size_t(len) + 3) & ~size_t(3));
            for (uint32_t i = 0; i < len; i++) mem_wr8(addr + i, buf[pos + i]);
            pos += (len + 3) & ~3u;
        } else if (tag == GV_TAG_TLUT) {
            require(32768);
            // Upload through the real MMIO window (exercises the SRAM FSM).
            mmio_write(8, 0);
            for (uint32_t i = 0; i < 32768; i += 4) {
                uint32_t w = (uint32_t)buf[pos+i] | ((uint32_t)buf[pos+i+1] << 8)
                           | ((uint32_t)buf[pos+i+2] << 16) | ((uint32_t)buf[pos+i+3] << 24);
                for (int t = 0; t < 1000 && (mmio_read(5) & 8); t++) tick();
                mmio_write(9, w);
            }
            for (int t = 0; t < 1000 && (mmio_read(5) & 8); t++) tick();
            pos += 32768;
        } else if (tag == GV_TAG_PALB) {
            mmio_write(12, rd32());
        } else if (tag == GV_TAG_FLSH) {
            mmio_write(10, 1);
            tick(8);
            if (!wait_idle(1000000)) { fprintf(stderr, "%s: flush idle timeout\n", vec_path); rc = 3; break; }
        } else if (tag == GV_TAG_CMDS) {
            preload_texture_pages();
            uint32_t n = rd32();
            if (n > 4095) throw std::runtime_error("Oversized GPU batch");
            require(size_t(n) * 4);
            uint64_t reserve_start=cycles;
            while((4095u-(((mmio_read(1)-mmio_read(4))&16383u)/4u))<n) {
                if(cycles-reserve_start>50000000ull)throw std::runtime_error("ring reserve timeout");
                tick();
            }
            for (uint32_t i = 0; i < n; i++) mmio_write(1, rd32());
            if ((mmio_read(5) & 0x300) != 0x100) {
                fprintf(stderr, "%s: CPU ring overflow/mode lost\n", vec_path);
                rc = 3; break;
            }
            mmio_write(0, 8);   // publish
            batches++;
            // Maximal-rate producer: the next batch may overlap this one.
        } else if (tag == GV_TAG_DUMP) {
            if(!wait_idle(50000000ull))throw std::runtime_error("final drain timeout");
            uint32_t addr = rd32(), len = rd32();
            for (uint32_t i = 0; i < len; i++) { uint8_t b = mem_rd8(addr + i); fputc(b, out); }
        } else if (tag == GV_TAG_FENC) {
            uint32_t v = tb->fence_reached;
            fwrite(&v, 4, 1, out);
        } else if (tag == GV_TAG_RAND) {
            uint32_t addr = rd32(), len = rd32(); unsigned st = rd32();
            for (uint32_t i = 0; i < len; i++) mem_wr8(addr + i, (uint8_t)gv_rand_next(&st));
        } else if (tag == GV_TAG_HASH) {
            uint64_t h = 0xcbf29ce484222325ull;
            for (uint32_t i = 0; i < 0x1000000; i++) {
                uint32_t w = sdram_word(i);
                for (int b = 0; b < 4; b++) { h ^= (uint8_t)(w >> (8 * b)); h *= 0x100000001b3ull; }
            }
            fwrite(&h, 8, 1, out);
        } else if (tag == GV_TAG_END) {
            ended = true;
            break;
        } else {
            fprintf(stderr, "%s: bad tag %08x at %zu\n", vec_path, tag, pos - 4);
            rc = 2; break;
        }
    }
    if (!ended || pos != buf.size()) rc = 2;
    if (fclose(out) != 0) rc = 2;
    printf("RESULT cycles=%llu busy_cycles=%llu batches=%u\n",
        (unsigned long long)(cycles - start_cycles),
        (unsigned long long)(busy_cycles - start_busy), batches);
    observe_report();
    texture_report();
    mesh_report();
    upload_report();
    tb->final();
    return rc;
}

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    for(int a=1;a<argc;++a)if(!strcmp(argv[a],"+fast_tex=1"))fast_tex=true;
    for(int a=1;a<argc;++a){
        if(!strncmp(argv[a],"+mesh_padding=",14))mesh_padding_seed=strtoul(argv[a]+14,nullptr,0);
        if(!strcmp(argv[a],"+tagged_tex=1"))tagged_tex=true;
        if(!strcmp(argv[a],"+concurrent_upload=1"))concurrent_upload=true;
    }
    if(tagged_tex&&concurrent_upload)throw std::runtime_error("Separate tagged and concurrency tests");
    tb = new Vtb_gpu;
    int worst = 0;
    for (int i = 1; i + 1 < argc; i += 2) {
        if (argv[i][0] == '+') { i--; continue; }
        int rc;
        try { rc = run_vector(argv[i], argv[i + 1]); }
        catch (const std::exception &e) { fprintf(stderr, "%s\n", e.what()); rc = 2; }
        if (rc) fprintf(stderr, "vector %s rc=%d\n", argv[i], rc);
        if (rc > worst) worst = rc;
    }
    fprintf(stderr, "rtl cycles=%llu\n", (unsigned long long)cycles);
    delete tb;
    return worst;
}
