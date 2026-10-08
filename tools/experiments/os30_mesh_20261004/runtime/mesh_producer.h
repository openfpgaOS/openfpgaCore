/* Included after sm64_shadow.h. Whole immutable runs stay in original order;
 * unsupported state and mixed vertex loads return to the ordinary renderer. */
static uint32_t mesh_published_vertices,mesh_published_indices;
static Gfx *gfx_mesh_try_run(Gfx *cmd) {
    if(!gfx_gpu_mesh_enabled()||!mesh_shadow_vertices)return cmd;
    /* Keep original CPU precision/clipping for loads the production raw path
     * rejected. Accepted loads use its identical safe guardband and scissor. */
    if(!(mesh_shadow_vertex_state[57]&1u))return cmd;
    uintptr_t address=(uintptr_t)cmd,end=(uintptr_t)__EH_FRAME_BEGIN__;
    int immutable=address>=(uintptr_t)&_start&&address<end&&sizeof(Gfx)<=end-address;
    const struct MeshRunEntry *run=NULL;
    if(mesh_run_get_compact(&mesh_shadow_cache,address,mesh_shadow_first,
            mesh_shadow_vertices_count,immutable,mesh_shadow_read_command,(void *)cmd,&run))return cmd;
    uint32_t state[64];
    if(mesh_capture_material_state(mesh_shadow_vertex_state,state)!=MESH_MATERIAL_OK)return cmd;
    if(buf_vbo_len)gfx_flush();
    /* Pools only append immutable data. Publish new words once, including the
     * tail of a cache line shared with an earlier immutable allocation. */
    if(mesh_published_vertices!=mesh_shadow_cache.vertex_used) {
        gfx_gpu_mesh_publish_cached(mesh_shadow_cache.vertices+mesh_published_vertices,
            (mesh_shadow_cache.vertex_used-mesh_published_vertices)*16);
        mesh_published_vertices=mesh_shadow_cache.vertex_used;
    }
    if(mesh_published_indices!=mesh_shadow_cache.index_used) {
        gfx_gpu_mesh_publish_cached(mesh_shadow_cache.indices+mesh_published_indices,
            (mesh_shadow_cache.index_used-mesh_published_indices)*4);
        mesh_published_indices=mesh_shadow_cache.index_used;
    }
    if(!gfx_gpu_mesh_submit(mesh_shadow_cache.vertices[mesh_shadow_vertices->offset],
            mesh_shadow_vertices_count,mesh_shadow_cache.indices+run->offset,
            run->triangles,state))return cmd;
    return cmd+run->commands;
}
