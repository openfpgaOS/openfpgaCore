/* Included after the immutable mesh capture and triangle decoder. */
static int gfx_mesh_lazy_capture(size_t count,size_t first,const Vtx *vertices,float aspect) {
    if(!gfx_gpu_mesh_enabled() || !mesh_shadow_vertices)return 0;
    /* mesh_shadow_vertices guarantees an immutable original source, integral
     * positions and supported shader state for precisely this current load. */
    uint32_t mask=count==32?0xffffffffu:((1u<<count)-1u)<<first;
    geometry_batch_mask=0;
    uint32_t release=geometry_valid&mask;
    geometry_valid&=~mask;geometry_pending&=~mask;
    for(unsigned i=first;release;i++) if(release&(1u<<i)) {
        struct GeometryContext *context=&geometry_contexts[geometry_vertices[i].context];
        release&=~context->owners;context->owners&=~mask;
    }
    gfx_mesh_lazy_save(count,first,vertices,aspect);
    mesh_lazy_fixed=(mesh_shadow_vertex_state[57]&1u)!=0;
    if(mesh_lazy_fixed) {
        memcpy(mesh_lazy_matrix,mesh_shadow_vertex_state,8*sizeof(int32_t));
        memcpy(mesh_lazy_matrix+8,mesh_shadow_vertex_state+12,4*sizeof(int32_t));
    }
    return 1;
}

/* The offload caller must first try the entire command through
 * gfx_mesh_try_run(cmd). Only its ordinary CPU fallback reaches this hook.
 * State commands do not flush the saved vertex snapshot. */
static void gfx_mesh_lazy_fallback(const Gfx *cmd) {
    if(!mesh_lazy_pending)return;
    unsigned slot[6],count=gfx_geometry_decode(cmd,slot);
    uint32_t mask=0;
    for(unsigned i=0;i<3*count;i++)if(slot[i]<32)mask|=1u<<slot[i];
    gfx_mesh_lazy_materialize(mask);
}
