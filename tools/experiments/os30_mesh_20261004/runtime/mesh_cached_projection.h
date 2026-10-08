/* Preserve the original 0x56/0x54 precision policy behind the private mesh
 * protocol. Caller retains original material, stable-slot, LOD, clipping and
 * constant-W eligibility; only dirty slots are supplied in v[]. */
static void gpu_project_clip(float,float,float,int16_t *,int16_t *);
static inline uint32_t gpu_clip_reciprocal(uint32_t);
struct MeshCachedProjection {
    int16_t x,y;
    int32_t s,t,zi,depth;
    uint16_t color;
};
static struct MeshCachedProjection mesh_cached_projection[32];
static void mesh_draw_cached_triangle(const struct gfx_vc_vtx v[3],
        const uint8_t slot[3],unsigned dirty) {
#ifndef OF_PC
    int16_t x[3],y[3];int32_t s[3],t[3],zi[3],depth[3];uint16_t color[3];
    for(unsigned i=0;i<3;i++) {
        struct MeshCachedProjection *cached=&mesh_cached_projection[slot[i]];
        if(dirty&(1u<<i)) {
            gpu_project_clip(v[i].cx,v[i].cy,v[i].cw,&cached->x,&cached->y);
            int32_t wq=(int32_t)lrintf(v[i].cw*(65536.0f*VC_W_PRESCALE));
            cached->zi=gpu_clip_reciprocal(wq<256?256u:(uint32_t)wq);
            cached->s=(int32_t)lrintf(v[i].u*65536.0f);
            cached->t=(int32_t)lrintf(v[i].v*65536.0f);
            float df=v[i].w_inv*1073741824.0f;
            if(df<1.0f)df=1.0f;else if(df>2147483520.0f)df=2147483520.0f;
            cached->depth=v[i].use_exact_depth?v[i].exact_depth:(uint32_t)lrintf(df);
            cached->color=g_vc_rgb_input<0?0xffff:rgba_to_565(v[i].r,v[i].g,v[i].b);
            g_vc_loads++;
        }
        x[i]=cached->x;y[i]=cached->y;s[i]=cached->s;t[i]=cached->t;
        zi[i]=cached->zi;depth[i]=cached->depth;color[i]=cached->color;
    }
    mesh_projected_triangle(x,y,s,t,zi,color,depth,NULL,1);
    g_vc_words+=8;g_vc_tris_cached++;
#else
    (void)v;(void)slot;(void)dirty;
#endif
}
