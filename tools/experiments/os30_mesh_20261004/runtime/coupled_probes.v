// Simulation-only observations, never part of the fitted core.
assign overlap_raster_active = gpu.state != gpu.S_IDLE
    && !((gpu.state == gpu.S_PAY_DATA || gpu.state == gpu.S_PAY_WAIT) && gpu.ring_empty);
assign overlap_fragment = gpu.state == gpu.S_FRAG_PIPE;
assign overlap_fragment_stall = overlap_fragment && gpu.fp_pipe_stall;
assign overlap_upload_words = gpu.ring_wrptr - gpu.upload_rdptr;
assign overlap_write_take = gpu_wr_wvalid && gpu_wr_wready;
assign overlap_output_words = gpu.command_wrptr - gpu.ring_rdptr;
// Categories are exclusive. Full engine activity includes memory and output
// instructions; explicit independent engine/DMA counters avoid calling it math.
assign overlap_worker_kind = mesh_output_blocked ? 1
    : mesh_engine_active ? 2
    : (!mesh_dma_active && gpu.g_geometry_worker.worker.input_empty) ? 0 : 4;
