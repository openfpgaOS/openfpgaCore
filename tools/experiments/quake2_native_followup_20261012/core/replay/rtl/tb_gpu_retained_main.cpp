// Compare retained 2D or os30 triangle workloads before/after GPU cuts. Reuse the
// byte-exact acceptance oracles, and report cycles separately for each case.
#define main gpu_acceptance_main
#include "tb_gpu_acceptance_main.cpp"
#undef main

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    Verilated::traceEverOn(false);
    tb = new Vtb_gpu;
    trace = nullptr;
    hard_reset();
#define RUN_TEST(name) do { \
    const uint64_t start = sim_time; \
    name(); \
    printf("CYCLES %s %llu\n", #name, \
           (unsigned long long)((sim_time - start) / 2)); \
} while (0)
    RUN_TEST(test_fence_no_writes);
    RUN_TEST(test_multi_fence_in_order);
    RUN_TEST(test_fence_after_clear_drains);
    RUN_TEST(test_fence_token_high_bits);
    RUN_TEST(test_set_fb_two_bases);
    RUN_TEST(test_set_fb_unusual_stride);
    RUN_TEST(test_clear_color_replication);
    RUN_TEST(test_clear_drains_framebuffer_writes);
    RUN_TEST(test_clear_no_op_when_flag_clear);
    RUN_TEST(test_clear_does_not_touch_rows_200_to_239);
    RUN_TEST(test_clear_rect_edge_lanes);
    RUN_TEST(test_clear_rect_zero_dimensions);
#ifdef GPU_TEST_TRUECOLOR
    RUN_TEST(test_texture_mirror_s);
    RUN_TEST(test_texture_mirror_t);
    RUN_TEST(test_vert_tri_sliver_renders_in_range);
    RUN_TEST(test_vert_tri_sticky_semantics);
    RUN_TEST(test_vert_tri_wrong_size_noop);
    RUN_TEST(test_lean_sticky_state_contract);
    RUN_TEST(test_disabled_vertex_q29_override);
    RUN_TEST(test_vert_tri_rgb_pack_depth);
    RUN_TEST(test_vert_tri_rgb_pack_depth_subpix_y);
    RUN_TEST(test_small_triangle_depth_plane);
    RUN_TEST(test_vert_tri_fractional_plane_anchor);
    RUN_TEST(test_truecolor_blend_same_pixel);
    RUN_TEST(test_truecolor_clamp_mirror);
    RUN_TEST(test_truecolor_blend);
    RUN_TEST(test_truecolor_blend_full);
    RUN_TEST(test_truecolor_blend_overlap);
    RUN_TEST(test_truecolor_blend_abutting);
    RUN_TEST(test_vert_tri_subpixel_y);
    RUN_TEST(test_clip_tri_drains_when_pruned);
    RUN_TEST(test_xform_matrix_cmds_drain_no_mac);
    RUN_TEST(test_load_vert_clip_wrong_size_drains);
    RUN_TEST(test_vtx_cache_distant_texture_precision);
    RUN_TEST(test_vtx_cache_clip_depth_override);
    RUN_TEST(test_vtx_cache_slot_reuse_and_wrap);
#else
    RUN_TEST(test_param_span_list_affine_rows);
    RUN_TEST(test_param_span_list_affine_columns);
    RUN_TEST(test_param_span_list_affine_clamp);
    RUN_TEST(test_param_span_list_zero_counts_skip);
    RUN_TEST(test_param_span_list_streams_many_records);
    RUN_TEST(test_param_span_list_colormap_skip_zero);
    RUN_TEST(test_param_q48_multichunk_distinct_column_drop_repro);
    RUN_TEST(test_portb_starve_distinct_cache_line_wall);
    RUN_TEST(test_param_span_translucency);
#endif
    RUN_TEST(test_framebuffer_writes_burst_full_words);
    RUN_TEST(test_flip_no_writes_pulses_immediately);
#undef RUN_TEST
    printf("Acceptance Results: %d passed, %d failed\n", pass_count, fail_count);
    delete tb;
    return fail_count ? 1 : 0;
}
