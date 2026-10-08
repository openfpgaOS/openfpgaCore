// Private idle-drain regression. Existing independent pixel/barrier oracles
// plus unfenced clear ownership at the public busy signal.
#define main retained_acceptance_main
#include "tb_gpu_acceptance_main.cpp"
#undef main

static void unfenced_clear() {
    gpu_init();
    sdram_fill(FB_BASE_BYTE,256,SENTINEL_BYTE);
    cmd_set_fb(FB_BASE_BYTE,320);
    cmd_clear_rect(FB_BASE_BYTE+3,93,1,320,0x5a);
    gpu_kick();
    bool entered=false,finished=false;
    for(unsigned n=0;n<100000;n++) {
        entered|=tb->busy;
        if(entered&&!tb->busy){finished=true;break;}
        tick();
    }
    if(!finished){check_fail("unfenced_clear_busy","timeout");return;}
    unsigned early=0;
    for(unsigned i=0;i<256;i++) {
        unsigned expected=i>=3&&i<96?0x5a:SENTINEL_BYTE;
        early+=sdram_read_byte(FB_BASE_BYTE+i)!=expected;
    }
    printf("OWNERSHIP first_idle_wrong_bytes=%u tail_holes=%llu premature_flush=%llu\n",
           early,(unsigned long long)observed_busy_holes,
           (unsigned long long)observed_premature_flushes);
    // Both versions must still complete the original fence correctly.
    if(!submit_and_wait())check_fail("unfenced_clear_final_fence","timeout");
    else {
        bool exact=true;
        for(unsigned i=0;i<256;i++)exact&=sdram_read_byte(FB_BASE_BYTE+i)==
            (i>=3&&i<96?0x5a:SENTINEL_BYTE);
        if(exact)check_pass("unfenced_clear_final_fence");
        else check_fail("unfenced_clear_final_fence","pixels");
    }
}

static void unfenced_triangle() {
    gpu_init();
    sdram_fill(FB_BASE_BYTE,640*12,SENTINEL_BYTE);
    sdram_write_u16_le(TEX_BASE_BYTE,0x7bef);
    auto p=make_vert_tri_surface();
    p.flags=SPAN_PERSP|(1u<<7);p.z_mode=0;
    p.fb_base=FB_BASE_BYTE;p.fb_major_step=640;p.fb_minor_step=2;
    p.tex_width=1;p.tex_w_mask=p.tex_h_mask=0;
    const int16_t x[3]={0,32*16,0},y[3]={0,0,8};
    const int32_t st[3]={},zi[3]={65536,65536,65536};
    const uint8_t light[3]={63,63,63};
    emit_set_tri_state_raw(p,0,320,0,200);
    emit_draw_vert_tri_raw(x,y,st,st,zi,light);
    gpu_kick();
    bool entered=false,finished=false;
    for(unsigned n=0;n<100000;n++) {
        entered|=tb->busy;
        if(entered&&!tb->busy){finished=true;break;}
        tick();
    }
    if(!finished){check_fail("unfenced_triangle_busy","timeout");return;}
    std::vector<uint8_t> first(640*12);
    for(unsigned i=0;i<first.size();i++)first[i]=sdram_read_byte(FB_BASE_BYTE+i);
    if(!submit_and_wait()){check_fail("unfenced_triangle_fence","timeout");return;}
    unsigned changed=0,pixels=0;
    for(unsigned i=0;i<first.size();i++)changed+=first[i]!=sdram_read_byte(FB_BASE_BYTE+i);
    for(unsigned yy=0;yy<12;yy++)for(unsigned xx=0;xx<320;xx++)
        pixels+=sdram_read_u16_le(FB_BASE_BYTE+yy*640+xx*2)==0x7bef;
    printf("TRIANGLE first_idle_changed_after_fence=%u pixels=%u\n",changed,pixels);
    if(pixels>80&&pixels<160)check_pass("unfenced_triangle_final_pixels");
    else check_fail("unfenced_triangle_final_pixels","coverage");
}

int main(int argc,char**argv) {
    Verilated::commandArgs(argc,argv);Verilated::traceEverOn(false);
    tb=new Vtb_gpu;trace=nullptr;
    unfenced_clear();unfenced_triangle();
    test_fence_no_writes();test_multi_fence_in_order();
    test_fence_after_clear_drains();test_fence_token_high_bits();
    test_clear_rect_edge_lanes();test_clear_rect_zero_dimensions();
    test_truecolor_blend_same_pixel();test_truecolor_blend_overlap();
    test_vert_tri_rgb_pack_depth();test_lean_sticky_state_contract();
    test_flip_no_writes_pulses_immediately();
    printf("DRAIN counters tail_holes=%llu premature_flush=%llu idle_flush=%llu\n",
      (unsigned long long)observed_busy_holes,
      (unsigned long long)observed_premature_flushes,
      (unsigned long long)observed_idle_flushes);
    printf("Acceptance Results: %d passed, %d failed\n",pass_count,fail_count);
    delete tb;return fail_count?1:0;
}
