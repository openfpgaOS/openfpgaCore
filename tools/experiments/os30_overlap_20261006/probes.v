// Testbench-only observations; retained-A synthesizable sources stay exact.
assign overlap_raster_active = gpu.state != gpu.S_IDLE
    && !((gpu.state == gpu.S_PAY_DATA || gpu.state == gpu.S_PAY_WAIT) && gpu.ring_empty);
assign overlap_fragment = gpu.state == gpu.S_FRAG_PIPE;
assign overlap_fragment_stall = overlap_fragment && gpu.fp_pipe_stall;
// A pipeline advance containing at least one live fragment, not just BUSY.
assign overlap_fragment_productive = overlap_fragment && !gpu.fp_pipe_stall
    && (gpu.p0a_valid || gpu.p0_valid || gpu.p1_valid || gpu.p2_valid || gpu.p2b_valid || gpu.p3_valid);
assign overlap_idle_empty = gpu.state == gpu.S_IDLE && gpu.ring_empty;
assign overlap_payload_empty = (gpu.state == gpu.S_PAY_DATA || gpu.state == gpu.S_PAY_WAIT) && gpu.ring_empty;
assign overlap_upload_words = gpu.ring_wrptr - gpu.upload_rdptr;
assign overlap_output_words = gpu.command_wrptr - gpu.ring_rdptr;
assign overlap_write_take = gpu_wr_wvalid && gpu_wr_wready;
assign overlap_worker_kind = 7; // The retained A bitstream contains no worker.
