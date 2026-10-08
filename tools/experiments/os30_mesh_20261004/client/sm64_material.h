/* Concrete hardware-job material snapshot, parallel to diagnostic captures.
 * Status0 is supported. All other jobs retain the ordered original renderer.
 * No loaded vertex or triangle is inspected to prepare a warm job. */
enum { MESH_MATERIAL_OK=0, MESH_MATERIAL_BACKEND=1,
       MESH_MATERIAL_LOD=2, MESH_MATERIAL_SHADE_XLU=3,
       MESH_MATERIAL_FOG_ALPHA=4 };
uint32_t mesh_material_status[MESH_SHADOW_JOBS];
uint32_t mesh_material_state[MESH_SHADOW_JOBS][64];
/* The renderer invalidates triangle_mesh_material_ready whenever it rebuilds
 * its draw material. LOAD fog is independent of that draw state, and the
 * shader and guard acceptance are copied afresh for every submitted job. */
static uint32_t mesh_material_template[64];
static unsigned mesh_material_template_status;
static bool mesh_material_template_load_fog,mesh_material_template_textured;
static unsigned mesh_build_material_template(struct ColorCombiner *comb,bool fog,
        const uint32_t vertex_state[64],uint32_t out[64]) {
    int rgb_in=-1,alpha_in=-1;
    int eligible=gfx_gpu_mesh_select(&rgb_in,&alpha_in);
    if(!eligible)return MESH_MATERIAL_BACKEND;
    unsigned rgb_mux=rgb_in<0?0:comb->shader_input_mapping[0][rgb_in];
    unsigned alpha_mux=alpha_in<0?0:comb->shader_input_mapping[1][alpha_in];
    if((rgb_in>=0&&rgb_mux==CC_LOD)||(alpha_in>=0&&alpha_mux==CC_LOD))
        return MESH_MATERIAL_LOD;
    int shade_alpha=alpha_in>=0&&alpha_mux==CC_SHADE&&!fog;
    if(shade_alpha&&(eligible&4))return MESH_MATERIAL_SHADE_XLU;
    if(shade_alpha&&(vertex_state[22]&G_FOG))
        return MESH_MATERIAL_FOG_ALPHA;
    mesh_material_template_textured=(eligible&2)!=0;
    /* Snapshot overwrites24..46; initialize only the optional material tail. */
    memset(out+47,0,17*4);memcpy(out,vertex_state,24*4);
    uint8_t opacity=255;
    if(alpha_in>=0&&alpha_mux!=CC_SHADE) {
        struct RGBA tmp;
        opacity=gfx_vc_resolve(alpha_mux,NULL,NULL,&tmp)->a;
    }
    gfx_gpu_mesh_snapshot(out,(eligible&2)!=0,opacity);
    unsigned cull=((rsp.geometry_mode&G_CULL_FRONT)?1u:0u)
                 |((rsp.geometry_mode&G_CULL_BACK)?2u:0u);
    out[47]=3|(cull<<8)|(shade_alpha?(1u<<18):0)|((vertex_state[57]&1u)<<19);
    if(rgb_in<0||rgb_mux!=CC_SHADE) {
        uint16_t rgb=65535;
        if(rgb_in>=0) {
            struct RGBA tmp;
            const struct RGBA *c=gfx_vc_resolve(rgb_mux,NULL,NULL,&tmp);
            rgb=((uint16_t)(c->r>>3)<<11)|((uint16_t)(c->g>>2)<<5)|(c->b>>3);
        }
        out[47]|=1u<<16;out[51]=rgb;
    }
    if(eligible&2) {
        int bias=(rdp.other_mode_h&(3U<<G_MDSFT_TEXTFILT))!=G_TF_POINT;
        out[48]=-rdp.texture_tile.uls*16384+(bias?32768:0);
        out[49]=-rdp.texture_tile.ult*16384+(bias?32768:0);
    } else {
        /* Existing truecolor renderer uses s=t=0 for untextured jobs. */
        out[21]=0;
    }
    return MESH_MATERIAL_OK;
}
static unsigned mesh_capture_material_state(const uint32_t vertex_state[64],uint32_t out[64]) {
    bool fog,alpha,texture;
    struct ColorCombiner *comb=gfx_prepare_triangle_material(&fog,&alpha,&texture);
    const bool load_fog=(vertex_state[22]&G_FOG)!=0;
    if(!triangle_mesh_material_ready||mesh_material_template_load_fog!=load_fog) {
        mesh_material_template_status=mesh_build_material_template(comb,fog,
            vertex_state,mesh_material_template);
        mesh_material_template_load_fog=load_fog;
        triangle_mesh_material_ready=true;
    }
    if(mesh_material_template_status!=MESH_MATERIAL_OK)
        return mesh_material_template_status;
    memcpy(out,vertex_state,24*4);
    memcpy(out+24,mesh_material_template+24,40*4);
    if(!mesh_material_template_textured)out[21]=0;
    out[47]=(out[47]&~(1u<<19))|((vertex_state[57]&1u)<<19);
    return MESH_MATERIAL_OK;
}
static unsigned mesh_capture_material(const struct MeshOwnedJob *job,uint32_t out[64]) {
    return mesh_capture_material_state(job->state,out);
}
