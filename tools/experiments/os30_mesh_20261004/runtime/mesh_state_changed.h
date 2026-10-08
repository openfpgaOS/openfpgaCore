/* A queued mesh references the same texture storage as ordinary triangles.
 * Mark only the submitted address: an untextured white-texel job must not
 * acquire the currently bound texture slot. gpu_tex_slot_begin then performs
 * its existing finish-before-reuse check for a third upload in this frame. */
static void mesh_renderer_state_changed(uint32_t submitted_texaddr) {
    if(g_cur_tex[0] && g_cur_tex[0]->addr==submitted_texaddr)
        g_cur_tex[0]->slot_epoch[g_cur_tex[0]->cur]=g_tex_epoch;
    g_st_cache_valid=0;
}
