// Simulation-only observations. Never added to the fitted design.
assign overlap_raster_active = gpu.state != gpu.S_IDLE
    && !((gpu.state == gpu.S_PAY_DATA || gpu.state == gpu.S_PAY_WAIT) && gpu.ring_empty);
assign overlap_fragment = gpu.state == gpu.S_FRAG_PIPE;
assign overlap_fragment_stall = overlap_fragment && gpu.fp_pipe_stall;
assign overlap_upload_words = gpu.ring_wrptr - gpu.upload_rdptr;
assign overlap_write_take = gpu_wr_wvalid && gpu_wr_wready;
assign overlap_output_words = gpu.command_wrptr - gpu.ring_rdptr;
`ifdef INCLUDE_GPU_GEOMETRY_WORKER
// Categories are exclusive: input-starved, output-blocked, vertex arithmetic,
// winding arithmetic, other command/queue work. Arithmetic includes DSP/divider
// latency but excludes input loads, output emission and FIFO waits.
wire ow_empty = gpu.g_geometry_worker.worker.input_empty;
wire [5:0] ow_state = gpu.g_geometry_worker.worker.state;
wire [2:0] ow_mode = gpu.g_geometry_worker.worker.mode;
wire ow_block = (ow_state == 1 && gpu.g_geometry_worker.worker.free_words < gpu.g_geometry_worker.worker.needed)
    || (ow_state == 2 && ow_mode == 0 && gpu.g_geometry_worker.worker.free_words == 0);
wire ow_starve = ow_empty && (ow_state == 0 || ow_state == 2 || ow_state == 3);
assign overlap_worker_kind = ow_block ? 1 : ow_starve ? 0
    : (ow_state >= 4 && ow_state <= 24) ? 2
    : (ow_state >= 27 && ow_state <= 36) ? 3 : 4;
`else
assign overlap_worker_kind = 7;
`endif
