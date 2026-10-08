// Simulation-only reference extraction. These ports are never fitted.
assign tap_header_valid = gpu.state == gpu.S_DECODE;
assign tap_header = {gpu.cmd_type,11'd0,gpu.cmd_payload_words};
assign tap_payload_valid = gpu.state == gpu.S_PAY_DATA;
assign tap_payload = gpu.ring_rd_data;
assign tap_planes_valid = gpu.state == gpu.S_TRI_DERIVE && gpu.dstate == gpu.DRV_DONE;
assign tap_rgb = gpu.cmd_is_draw_vert_tri_rgb || gpu.cmd_is_draw_indexed_tri
    || gpu.cmd_is_draw_xform_tri_rgb || gpu.cmd_is_draw_clip_tri;
assign tap_plane_words = gpu.spanprod_cd_combine ? 6'd33 : 6'd24;
assign tap_planes[0*32+:32] = {gpu.tri_v0_y,gpu.tri_v0_x};
assign tap_planes[1*32+:32] = {gpu.tri_v1_y,gpu.tri_v1_x};
assign tap_planes[2*32+:32] = {gpu.tri_v2_y,gpu.tri_v2_x};
// Remaining slices are generated in build_planes.py from the documented ABI.
