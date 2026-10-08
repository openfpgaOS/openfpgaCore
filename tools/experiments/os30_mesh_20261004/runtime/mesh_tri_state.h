/* Private 0x4A writer. The mesh rasterizer retains sticky state while omitting
 * both legacy plane-derivation capability bits that gate the SDK writer. */
static void mesh_emit_tri_state(const of_gpu_tri_state_t *st) {
    uint32_t control=((uint32_t)st->flags&255u)
        | (((uint32_t)st->colormap_id&15u)<<8)
        | ((uint32_t)(st->attr_q29?OF_GPU_PARAM_ATTR_PERSP_Q29:OF_GPU_PARAM_ATTR_PERSP)<<12)
        | ((uint32_t)OF_GPU_PARAM_AXIS_X<<16)
        | ((uint32_t)OF_GPU_PARAM_RECORD_U16V16_COUNT16<<20)
        | (((uint32_t)st->z_mode&15u)<<24)
        | ((uint32_t)(st->mirror_s&1u)<<28) | ((uint32_t)(st->mirror_t&1u)<<29)
        | ((uint32_t)(st->cd_combine&1u)<<30) | ((uint32_t)(st->subpix_y&1u)<<31);
    _gpu_span_hdr_valid=0;
    _gpu_cmd_header(0x4a,17);
    uint32_t *w=_gpu_ring_claim();
    *w++=st->fb_base;*w++=st->fb_major_step;*w++=st->fb_minor_step;
    *w++=st->tex_addr;*w++=st->tex_width;*w++=((uint32_t)st->tex_h_mask<<16)|st->tex_w_mask;
    *w++=control;*w++=st->clamp_min[0];*w++=st->clamp_max[0];
    *w++=st->clamp_min[1];*w++=st->clamp_max[1];*w++=st->z_base;
    *w++=st->z_major_step;*w++=st->z_minor_step;
    *w++=((uint32_t)(uint16_t)st->clip_x1<<16)|(uint16_t)st->clip_x0;
    *w++=((uint32_t)(uint16_t)st->clip_y1<<16)|(uint16_t)st->clip_y0;
    *w++=st->const_alpha;
    _gpu_ring_commit(17);
}
