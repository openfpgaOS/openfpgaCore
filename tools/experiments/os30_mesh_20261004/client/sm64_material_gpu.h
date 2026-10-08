/* Private, non-emitting bridge to the renderer's concrete sticky state.
 * Included in gfx_gpu.c after mesh_build_tri_state. SDK stays untouched. */
int gfx_gpu_mesh_select(int *rgb, int *alpha) {
    if (!g_truecolor || !g_cur_shader) return 0;
    const struct CCFeatures *cc=&g_cur_shader->cc;
    int textured=cc->used_textures[0] && g_cur_tex[0] && g_cur_tex[0]->ci8;
    int a,b,d;
    if (g_decal || cc_blend_texel_alpha(cc) ||
        (textured && classify_combine_cd(cc,&a,&b,&d))) return 0;
    *rgb=cc_rgb_has_input(cc)?cc_cycle_input(cc,0):-1;
    *alpha=cc->opt_alpha?cc_cycle_input(cc,1):-1;
    return 1 | (textured?2:0) | (cc->opt_alpha && !g_z_write?4:0);
}
void gfx_gpu_mesh_snapshot(uint32_t out[64], int textured, uint8_t alpha) {
    of_gpu_tri_state_t st;
    mesh_build_tri_state(textured,0,1,alpha,0,&st);
    uint32_t control=((uint32_t)st.flags&255u)
        | (((uint32_t)st.colormap_id&15u)<<8)
        | ((uint32_t)(st.attr_q29?OF_GPU_PARAM_ATTR_PERSP_Q29:OF_GPU_PARAM_ATTR_PERSP)<<12)
        | ((uint32_t)OF_GPU_PARAM_AXIS_X<<16)
        | ((uint32_t)OF_GPU_PARAM_RECORD_U16V16_COUNT16<<20)
        | (((uint32_t)st.z_mode&15u)<<24)
        | ((uint32_t)(st.mirror_s&1u)<<28) | ((uint32_t)(st.mirror_t&1u)<<29)
        | ((uint32_t)(st.cd_combine&1u)<<30) | ((uint32_t)(st.subpix_y&1u)<<31);
    uint32_t *w=out+24;
    *w++=st.fb_base;*w++=st.fb_major_step;*w++=st.fb_minor_step;
    *w++=st.tex_addr;*w++=st.tex_width;*w++=((uint32_t)st.tex_h_mask<<16)|st.tex_w_mask;
    *w++=control;*w++=st.clamp_min[0];*w++=st.clamp_max[0];
    *w++=st.clamp_min[1];*w++=st.clamp_max[1];*w++=st.z_base;
    *w++=st.z_major_step;*w++=st.z_minor_step;
    *w++=((uint32_t)(uint16_t)st.clip_x1<<16)|(uint16_t)st.clip_x0;
    *w++=((uint32_t)(uint16_t)st.clip_y1<<16)|(uint16_t)st.clip_y0;
    *w++=st.const_alpha;
    out[41]=(int32_t)lrintf(g_cx);out[42]=(int32_t)lrintf(g_cy*16.0f);
    out[43]=(int32_t)lrintf(g_hw);out[44]=(int32_t)lrintf(g_hh*16.0f);
    out[45]=out[38];out[46]=out[39];
}
