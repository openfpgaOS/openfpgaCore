// Experimental 0x59 geometry batches versus independently transformed 0x56
// inputs. Exercise exact framebuffer/depth, culling and malformed drains.
static const int16_t GEO_V[9] = {-200,-160,0, 200,-160,100, 0,180,200};
static int32_t GEO_M[20] = {
    16777216, 8388608, 0, 32,
    0, 16777216, 4194304, -64,
    0, 0, 16777216, 600*256,
    0,0,0,0, 0,0,0,0
};
static int32_t geometry_coord(int vertex, int row) {
    int64_t sum=0;
    for(int k=0;k<3;k++) sum += int64_t(GEO_M[row*4+k])*GEO_V[vertex*3+k];
    return int32_t((sum >> 16) + GEO_M[row*4+3]);
}
static void geometry_load(unsigned first, unsigned count) {
    ring_cmd(0x59, 5*count);
    for(unsigned k=first;k<first+count;k++) {
        ring_write(k);
        ring_write(uint16_t(GEO_V[k*3]) | (uint32_t(uint16_t(GEO_V[k*3+1]))<<16));
        ring_write(uint16_t(GEO_V[k*3+2]) | (uint32_t(XFT_RGB[k])<<16));
        ring_write(uint32_t(XFT_S[k])); ring_write(uint32_t(XFT_T[k]));
    }
}
static void geometry_oracle() {
    for(int k=0;k<3;k++) {
        int32_t w=geometry_coord(k,2);
        emit_load_vert_clip_raw(20+k,geometry_coord(k,0),geometry_coord(k,1),w,
            XFT_S[k],XFT_T[k],XFT_RGB[k],uint32_t((uint64_t(1)<<38)/uint32_t(w)));
    }
}
static void test_geometry() {
    if(!(mmio_read(REG_STATUS)&(1u<<10))) check_fail("geometry.capability","missing");
    else check_pass("geometry.capability");
    if(mmio_read(REG_STATUS)&(1u<<11)) {
        gpu_init();
        // A command much larger than the worker output FIFO, delivered with
        // pauses inside its payload. This exercises the rasterizer's wait
        // state and proves a following fence cannot finish prematurely.
        ring_cmd(0x7f,1537);gpu_kick();
        for(unsigned i=0;i<1537;i++) {
            ring_write(0xface0000u+i);
            if(i%37==0) {gpu_kick();tick(19);}
        }
        if(!submit_and_wait())check_fail("worker.payload_pause","fence timeout");
        else check_pass("worker.payload_pause");
        // Published fence header without its token must keep busy asserted
        // without reusing the previous ring word as a token.
        uint32_t old=tb->fence_reached;
        ring_cmd(0x02,1);gpu_kick();tick(71);
        if(tb->fence_reached!=old || !(mmio_read(REG_STATUS)&1))
            check_fail("worker.fence_wait","premature completion");
        else check_pass("worker.fence_wait");
        uint32_t token=next_fence_token++;ring_write(token);gpu_kick();
        if(wait_fence(token))check_pass("worker.fence_resume");
        else check_fail("worker.fence_resume","timeout");
    }
    for(unsigned varying_w=0;varying_w<2;varying_w++)
    for(unsigned reverse=0;reverse<2;reverse++) for(unsigned cull=0;cull<4;cull++) {
        GEO_M[10]=varying_w?16777216:0;
        xform_preload();
        const uint32_t za=0x00200000,zb=0x00240000;
        sdram_fill(za,640*200,0); sdram_fill(zb,640*200,0);
        emit_set_object_state_raw(GEO_M,XFT_XC,XFT_YC,XFT_XS,XFT_YS,256,3);
        // Missing surface state must drain the entire batch without writes.
        geometry_load(0,3);
        auto p=make_xform_surface(FB_BASE_BYTE);
        p.z_mode=3;p.z_base=za;p.z_major_step=640;p.z_minor_step=2;
        emit_set_tri_state_raw(p,0,320,0,200);
        for(unsigned n: {0u,1u,4u,6u,9u,14u,16u}) {
            ring_cmd(0x59,n);for(unsigned k=0;k<n;k++)ring_write(0xdeadbeef);
        }
        if(reverse) { geometry_load(0,1);geometry_load(1,2); }
        else geometry_load(0,3);
        unsigned i1=reverse?2:1, i2=reverse?1:2;
        ring_cmd(0x5b,1);ring_write((i1<<5)|(i2<<10)|(cull<<15));

        p.fb_base=FB_ALT_BASE_BYTE;p.z_base=zb;
        emit_set_tri_state_raw(p,0,320,0,200);
        geometry_oracle();
        // This triangle has negative screen determinant before reversal.
        bool rejected=(cull==3)||(cull==1&&!reverse)||(cull==2&&reverse);
        if(!rejected) emit_draw_indexed_tri_raw(20|((20+i1)<<5)|((20+i2)<<10));
        if(!submit_and_wait()) {check_fail("geometry.fence","timeout");return;}
        std::string name="geometry."+std::to_string(varying_w)+"."
            +std::to_string(reverse)+"."+std::to_string(cull);
        if(rejected) {
            xform_all_blank((name+".fb").c_str(),FB_BASE_BYTE,0,320,0,200);
            xform_all_blank((name+".z").c_str(),za,0,320,0,200,0);
        } else {
            xform_ab_match((name+".fb").c_str(),FB_BASE_BYTE,FB_ALT_BASE_BYTE,0,320,0,200,500);
            xform_ab_match((name+".z").c_str(),za,zb,0,320,0,200,500,0);
        }
    }
}
