// Ordered depth/blend oracle for early depth reads. Reuse command encoders,
// but calculate expected depth rejection and blending independently.
#define main gpu_acceptance_main
#include "tb_gpu_acceptance_main.cpp"
#undef main

static void ordered_depth_blend() {
    bool coverage[80][80] = {};
    tcbf_draw(160, 0);
    if (!submit_and_wait()) { check_fail("depth_prefetch.coverage", "timeout"); return; }
    int covered = 0;
    for (int y = 0; y < 80; ++y) for (int x = 0; x < 80; ++x) {
        coverage[y][x] = sdram_read_u16_le(FB_BASE_BYTE + y*640 + x*2) == 0xffff;
        covered += coverage[y][x];
    }
    if (covered < 2000) { check_fail("depth_prefetch.coverage", "too few pixels"); return; }

    tcbf_setup();
    const uint32_t zbase = 0x00200000;
    sdram_fill(zbase, 640*80, 0);
    sdram_write_u16_le(TEX_BASE_BYTE + 64, 0);
    struct Pass { int32_t depth; uint16_t rgb; bool blend, write, transparent; };
    const Pass passes[] = {
        {0x10000000, 0xf800, false, true, false},
        {0x00040000, 0x07e0, true, true, false},  // rejected blended overdraw
        {0x20000000, 0x001f, true, true, false},
        {0x20000000, 0xf800, true, false, false}, // equal depth, read-only
        {0x30000000, 0xffff, false, true, true},  // texture discard, no z write
        {0x30000000, 0x001f, false, false, false},
        {0x10000000, 0xffff, false, true, false}, // must still reject
        {0x40000000, 0x07e0, true, true, false},
    };
    int16_t vx[3] = {5*16,75*16,5*16}, vy[3] = {4,4,74};
    int32_t uv[3] = {}, zi[3] = {1<<16,1<<16,1<<16};
    for (const auto &pass : passes) {
        auto p = make_vert_tri_surface();
        p.flags = SPAN_PERSP | (1u<<7) | (pass.blend ? (1u<<1):0) | (pass.transparent ? (1u<<2):0);
        p.fb_base = FB_BASE_BYTE; p.fb_major_step=640; p.fb_minor_step=2;
        p.tex_addr=TEX_BASE_BYTE+(pass.transparent?64:0);
        p.tex_width=1; p.tex_w_mask=p.tex_h_mask=0; p.attr_mode=1;
        p.z_mode=2 | pass.write; p.z_base=zbase; p.z_major_step=640; p.z_minor_step=2;
        p.const_alpha=160;
        uint16_t rgb[3] = {pass.rgb,pass.rgb,pass.rgb};
        int32_t depth[3] = {pass.depth,pass.depth,pass.depth};
        emit_set_tri_state_raw(p,0,320,0,200);
        emit_draw_vert_tri_rgb_raw(vx,vy,uv,uv,zi,rgb,depth);
    }
    if (!submit_and_wait()) { check_fail("depth_prefetch.ordered", "timeout"); return; }
    int bad=0;
    for (int y=0;y<80;++y) for (int x=0;x<80;++x) {
        const uint32_t addr=FB_BASE_BYTE+y*640+x*2;
        uint16_t fb=tcbf_pattern(x,y), z=0;
        if (coverage[y][x]) for (const auto &pass:passes) {
            const uint16_t incoming=z_compress_ref(pass.depth);
            if (pass.transparent || incoming<z) continue;
            const uint16_t color=gouraud_white_ref(pass.rgb>>11,(pass.rgb>>5)&63,pass.rgb&31);
            fb=pass.blend ? blend565_ref(color,fb,40,addr) : color;
            if(pass.write) z=incoming;
        }
        const uint16_t got_fb=sdram_read_u16_le(addr);
        const uint16_t got_z=sdram_read_u16_le(zbase+y*640+x*2);
        if(got_fb!=fb || got_z!=z) {
            if(bad<8) printf("(%d,%d) fb=%04x/%04x z=%04x/%04x cov=%d\n",x,y,got_fb,fb,got_z,z,coverage[y][x]);
            ++bad;
        }
    }
    if(bad) check_fail("depth_prefetch.ordered", "pixel/depth mismatch");
    else check_pass("depth_prefetch.ordered");
}
int main(int argc,char **argv) {
    Verilated::commandArgs(argc,argv); Verilated::traceEverOn(false);
    tb=new Vtb_gpu; trace=nullptr; hard_reset();
    ordered_depth_blend();
    printf("Acceptance Results: %d passed, %d failed\n",pass_count,fail_count);
    tb->final(); delete tb; return fail_count ? 1:0;
}
