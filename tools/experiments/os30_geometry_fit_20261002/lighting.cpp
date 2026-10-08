// Focused footprint-screen check: RGB565 lighting versus CPU-calculated colors.
// This validates the existing fixed-point command, not SM64's float lighting.
#define main gpu_acceptance_main
#include "tb_gpu_acceptance_main.cpp"
#undef main

static uint16_t light_reference(const int32_t n[3], const int32_t l[3],
                                uint16_t diffuse, uint16_t ambient) {
    int64_t dot = 0;
    for (int k = 0; k < 3; ++k) dot += int64_t(n[k]) * l[k];
    dot = std::max<int64_t>(0, std::min<int64_t>(65536, dot >> 16));
    const unsigned masks[] = {31, 63, 31}, shifts[] = {11, 5, 0};
    uint16_t rgb = 0;
    for (int k = 0; k < 3; ++k) {
        unsigned value = ((ambient >> shifts[k]) & masks[k])
                       + ((dot * ((diffuse >> shifts[k]) & masks[k])) >> 16);
        rgb |= std::min(value, masks[k]) << shifts[k];
    }
    return rgb;
}

static void lighting_case(unsigned which) {
    xform_preload();
    const int32_t directions[][3] = {
        {0, 0, 65536}, {16384, -32768, 65536}, {-65536, 32768, 16384}
    };
    const int32_t normals[][3] = {
        {0, 0, -65536}, {0, 0, 32768}, {0, 0, 131072},
        {-65536, 32768, 0}, {16384, 65536, 32768}, {65536, -32768, 65536},
        {65536, 0, 0}, {-65536, 0, 0}, {32768, 32768, 65536}
    };
    const uint16_t diffuse[] = {0xffff, 0xa53c, 0xef3b};
    const uint16_t ambient[] = {0x0000, 0x3295, 0x94d2};
    const int32_t *l = directions[which];
    emit_identity_object_state(0, 0, 0);
    ring_cmd(0x55, 6);
    for (int k = 0; k < 3; ++k) ring_write(uint32_t(l[k]));
    ring_write(diffuse[which]); ring_write(ambient[which]); ring_write(1);
    const uint32_t za = 0x00200000, zb = 0x00240000;
    sdram_fill(za, 640 * 200, 0); sdram_fill(zb, 640 * 200, 0);

    auto p = make_xform_surface(FB_BASE_BYTE);
    p.z_mode = 3; p.z_base = za; p.z_major_step = 640; p.z_minor_step = 2;
    emit_set_tri_state_raw(p, 0, 320, 0, 200);
    for (int k = 0; k < 3; ++k) {
        ring_cmd(0x57, 9); ring_write(k);
        for (int j = 0; j < 3; ++j) ring_write(uint32_t(XFT_CLIP_V[k*3+j]));
        for (int j = 0; j < 3; ++j) ring_write(uint32_t(normals[which*3+k][j]));
        ring_write(uint32_t(XFT_S[k])); ring_write(uint32_t(XFT_T[k]));
    }
    emit_draw_indexed_tri_raw(0 | (1 << 5) | (2 << 10));

    p.fb_base = FB_ALT_BASE_BYTE; p.z_base = zb;
    emit_set_tri_state_raw(p, 0, 320, 0, 200);
    for (int k = 0; k < 3; ++k)
        emit_load_vert_raw(0x53, 20+k, XFT_CLIP_V[k*3], XFT_CLIP_V[k*3+1],
            XFT_CLIP_V[k*3+2], XFT_S[k], XFT_T[k],
            light_reference(normals[which*3+k], l, diffuse[which], ambient[which]));
    emit_draw_indexed_tri_raw(20 | (21 << 5) | (22 << 10));
    if (!submit_and_wait()) { check_fail("geometry.light", "timeout"); return; }
    const std::string name = "geometry.light." + std::to_string(which);
    xform_ab_match((name+".fb").c_str(), FB_BASE_BYTE, FB_ALT_BASE_BYTE,
                   0, 320, 0, 200, 500);
    xform_ab_match((name+".z").c_str(), za, zb, 0, 320, 0, 200, 500, 0);
}

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv); Verilated::traceEverOn(false);
    tb = new Vtb_gpu; trace = nullptr; hard_reset();
    for (unsigned i = 0; i < 3; ++i) lighting_case(i);
    printf("Acceptance Results: %d passed, %d failed\n", pass_count, fail_count);
    tb->final(); delete tb; return fail_count ? 1 : 0;
}
