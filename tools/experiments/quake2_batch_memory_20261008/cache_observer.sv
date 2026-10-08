// Passive selected-cache observations. Included only by the private testbench.
// All functional cache/GPU/controller sources and connections stay unchanged.
`ifdef INCLUDE_GPU_RENDER_CACHE
wire [31:0] audit_real_hits, audit_real_misses, audit_real_writebacks;
reg [63:0] audit_cache [0:63];
reg [63:0] audit_flush_start [0:7], audit_flush_cycles [0:7];
reg [63:0] audit_flush_command [0:31];
reg [63:0] audit_read_bursts [0:16], audit_write_bursts [0:16];
reg [63:0] audit_hit_region [0:2], audit_miss_region [0:2];
reg [2:0] audit_flush_owner;
reg audit_last_flush_done;
integer audit_i;
initial begin
    for(audit_i=0;audit_i<64;audit_i=audit_i+1) audit_cache[audit_i]=0;
    for(audit_i=0;audit_i<8;audit_i=audit_i+1) begin audit_flush_start[audit_i]=0;audit_flush_cycles[audit_i]=0;end
    for(audit_i=0;audit_i<32;audit_i=audit_i+1) audit_flush_command[audit_i]=0;
    for(audit_i=0;audit_i<17;audit_i=audit_i+1) begin audit_read_bursts[audit_i]=0;audit_write_bursts[audit_i]=0;end
    for(audit_i=0;audit_i<3;audit_i=audit_i+1) begin audit_hit_region[audit_i]=0;audit_miss_region[audit_i]=0;end
    audit_flush_owner=0;audit_last_flush_done=0;
end
function automatic [2:0] audit_pop4(input [3:0] v);
    audit_pop4={2'b0,v[0]}+{2'b0,v[1]}+{2'b0,v[2]}+{2'b0,v[3]};
endfunction
// Region 0 is Q2's three framebuffer allocations. Region 1 uses the active
// 0x4A depth allocation. Early initialization writes may classify as other.
function automatic [1:0] audit_region(input [25:0] a);
    if(a<26'h0300000) audit_region=0;
    else if(a>=gpu.spanprod_z_base[25:0] && a<gpu.spanprod_z_base[25:0]+26'd153600) audit_region=1;
    else audit_region=2;
endfunction
wire [2:0] audit_cause={gpu_cache_tex_flush_pending,gpu_cache_idle_flush,
                       gpu_cache_barrier_req && gpu_cache_has_lines};
// The qualified selected instance has CACHE_ALL=1 and registered read lookup.
wire audit_write_hot=gpu_render_cache.state==gpu_render_cache.IDLE &&
    !gpu_render_cache.flush_req && gpu_s_wr_awvalid &&
    !(!gpu_cache_has_lines && (gpu_cache_clear_bypass || gpu_s_wr_awlen>=7)) &&
    (gpu_render_cache.hot0_hit || gpu_render_cache.hot1_hit);
wire audit_read_hot=gpu_render_cache.state==gpu_render_cache.R_ACCEPT &&
    (gpu_render_cache.hot0_hit || gpu_render_cache.hot1_hit);
wire audit_tag_hit=gpu_render_cache.state==gpu_render_cache.LOOK && gpu_render_cache.hit;
wire audit_miss=gpu_render_cache.state==gpu_render_cache.LOOK && !gpu_render_cache.hit;
wire audit_hit=audit_write_hot || audit_read_hot || audit_tag_hit;
wire [25:0] audit_hit_addr=audit_write_hot?gpu_s_wr_awaddr[25:0]:gpu_render_cache.addr;
always @(posedge clk) begin
    if(reset_n && critical_enable) begin
        audit_cache[0]<=audit_cache[0]+1;
        if(gpu_cache_busy) audit_cache[1]<=audit_cache[1]+1;
        if(gpu_cache_has_lines) audit_cache[2]<=audit_cache[2]+1;
        if(gpu.ring_empty && gpu.state==gpu.S_IDLE) audit_cache[3]<=audit_cache[3]+1;
        if(gpu.ring_empty && gpu.state!=gpu.S_IDLE) audit_cache[4]<=audit_cache[4]+1;
        if(audit_hit) begin
            audit_cache[5]<=audit_cache[5]+1;
            audit_hit_region[audit_region(audit_hit_addr)]<=audit_hit_region[audit_region(audit_hit_addr)]+1;
            if(audit_write_hot || (audit_tag_hit && !gpu_render_cache.read_op)) audit_cache[6]<=audit_cache[6]+1;
            else audit_cache[7]<=audit_cache[7]+1;
        end
        if(audit_write_hot) audit_cache[8]<=audit_cache[8]+1;
        if(audit_read_hot) audit_cache[9]<=audit_cache[9]+1;
        if(audit_tag_hit) audit_cache[10]<=audit_cache[10]+1;
        if(audit_miss) begin
            audit_cache[11]<=audit_cache[11]+1;
            audit_miss_region[audit_region(gpu_render_cache.addr)]<=audit_miss_region[audit_region(gpu_render_cache.addr)]+1;
            if(gpu_render_cache.read_op) audit_cache[12]<=audit_cache[12]+1;
            else audit_cache[13]<=audit_cache[13]+1;
            if(!gpu_render_cache.selected_meta[0]) audit_cache[14]<=audit_cache[14]+1;
            else if(gpu_render_cache.selected_meta[1]) audit_cache[15]<=audit_cache[15]+1;
            else audit_cache[16]<=audit_cache[16]+1;
        end
        if(gpu_render_cache.state==gpu_render_cache.MAINT) begin
            if(!gpu_render_cache.walk_meta[0]) audit_cache[17]<=audit_cache[17]+1;
            else if(gpu_render_cache.walk_meta[1]) audit_cache[18]<=audit_cache[18]+1;
            else audit_cache[19]<=audit_cache[19]+1;
        end
        if(gpu_render_cache.state==gpu_render_cache.IDLE && gpu_render_cache.flush_req) begin
            audit_cache[20]<=audit_cache[20]+1;
            audit_flush_start[audit_cause]<=audit_flush_start[audit_cause]+1;
            audit_flush_owner<=audit_cause;
            if(audit_cause[0]) audit_flush_command[gpu.cmd_class]<=audit_flush_command[gpu.cmd_class]+1;
        end
        if(gpu_render_cache.maintenance) begin
            audit_cache[21]<=audit_cache[21]+1;
            audit_flush_cycles[audit_flush_owner]<=audit_flush_cycles[audit_flush_owner]+1;
        end
        if(gpu_cache_flush_done && !audit_last_flush_done) audit_cache[22]<=audit_cache[22]+1;
        if(gpu_render_cache.state==gpu_render_cache.FILL_AR && gpu_rd_arvalid && gpu_rd_arready) begin
            if(gpu_render_cache.fill_new) audit_cache[23]<=audit_cache[23]+1;
            else audit_cache[24]<=audit_cache[24]+1;
        end
        if(gpu_render_cache.fill_blocked && gpu_render_cache.state==gpu_render_cache.FILL_AR) audit_cache[25]<=audit_cache[25]+1;
        if(gpu_render_cache.wb_full && (gpu_render_cache.state==gpu_render_cache.WB_AW || gpu_render_cache.state==gpu_render_cache.BY_AW)) audit_cache[26]<=audit_cache[26]+1;
        if(gpu_s_rd_arvalid && gpu_s_rd_arready) begin
            audit_cache[27]<=audit_cache[27]+1;
            audit_cache[28]<=audit_cache[28]+({56'd0,gpu_s_rd_arlen}+1)*4;
        end
        if(gpu_s_rd_rvalid) audit_cache[29]<=audit_cache[29]+4;
        if(gpu_s_wr_awvalid && gpu_s_wr_awready) begin
            audit_cache[30]<=audit_cache[30]+1;
            audit_cache[31]<=audit_cache[31]+({56'd0,gpu_s_wr_awlen}+1)*4;
        end
        if(gpu_s_wr_wvalid && gpu_s_wr_wready) begin
            audit_cache[32]<=audit_cache[32]+1;
            audit_cache[33]<=audit_cache[33]+audit_pop4(gpu_s_wr_wstrb);
        end
        if(gpu_rd_arvalid && gpu_rd_arready) begin
            audit_cache[34]<=audit_cache[34]+1;
            audit_cache[35]<=audit_cache[35]+({56'd0,gpu_rd_arlen}+1)*4;
            audit_read_bursts[gpu_rd_arlen>=15?16:gpu_rd_arlen+1]<=audit_read_bursts[gpu_rd_arlen>=15?16:gpu_rd_arlen+1]+1;
        end
        if(gpu_rd_rvalid) audit_cache[36]<=audit_cache[36]+4;
        if(gpu_wr_awvalid && gpu_wr_awready) begin
            audit_cache[37]<=audit_cache[37]+1;
            audit_cache[38]<=audit_cache[38]+({56'd0,gpu_wr_awlen}+1)*4;
            audit_write_bursts[gpu_wr_awlen>=15?16:gpu_wr_awlen+1]<=audit_write_bursts[gpu_wr_awlen>=15?16:gpu_wr_awlen+1]+1;
            if(gpu_render_cache.state==gpu_render_cache.WB_AW) begin
                if(gpu_render_cache.maintenance) audit_cache[39]<=audit_cache[39]+1;
                else audit_cache[40]<=audit_cache[40]+1;
            end else audit_cache[41]<=audit_cache[41]+1;
        end
        if(gpu_wr_wvalid && gpu_wr_wready) begin
            audit_cache[42]<=audit_cache[42]+1;
            audit_cache[43]<=audit_cache[43]+audit_pop4(gpu_wr_wstrb);
            if(gpu_render_cache.state==gpu_render_cache.WB_DATA) begin
                if(gpu_render_cache.maintenance) begin
                    audit_cache[44]<=audit_cache[44]+4;
                    audit_cache[45]<=audit_cache[45]+audit_pop4(gpu_wr_wstrb);
                end else begin
                    audit_cache[46]<=audit_cache[46]+4;
                    audit_cache[47]<=audit_cache[47]+audit_pop4(gpu_wr_wstrb);
                end
            end else begin
                audit_cache[48]<=audit_cache[48]+4;
                audit_cache[49]<=audit_cache[49]+audit_pop4(gpu_wr_wstrb);
            end
        end
        if(gpu_rd_arvalid && !gpu_rd_arready) audit_cache[50]<=audit_cache[50]+1;
        if(gpu_wr_awvalid && !gpu_wr_awready) audit_cache[51]<=audit_cache[51]+1;
        if(gpu_wr_wvalid && !gpu_wr_wready) audit_cache[52]<=audit_cache[52]+1;
        if(gpu_s_rd_arvalid && !gpu_s_rd_arready) audit_cache[53]<=audit_cache[53]+1;
        if(gpu_s_wr_awvalid && !gpu_s_wr_awready) audit_cache[54]<=audit_cache[54]+1;
        if(gpu_s_wr_wvalid && !gpu_s_wr_wready) audit_cache[55]<=audit_cache[55]+1;
        if(gpu_render_cache.state==gpu_render_cache.FILL_R && !gpu_rd_rvalid) audit_cache[56]<=audit_cache[56]+1;
        if(gpu_render_cache.state==gpu_render_cache.WCLEAR) audit_cache[57]<=audit_cache[57]+1;
        if(gpu_render_cache.state==gpu_render_cache.LOOK || gpu_render_cache.state==gpu_render_cache.LOOK_WAIT || gpu_render_cache.state==gpu_render_cache.R_ACCEPT) audit_cache[58]<=audit_cache[58]+1;
        if(gpu_cache_barrier_req && !gpu_cache_barrier_done) audit_cache[59]<=audit_cache[59]+1;
        if(gpu_cache_idle_flush) audit_cache[60]<=audit_cache[60]+1;
        if(gpu_cache_tex_flush_pending) audit_cache[61]<=audit_cache[61]+1;
        if(gpu_render_cache.state==gpu_render_cache.R_WORD && gpu_render_cache.word_valid!=4'hf) audit_cache[62]<=audit_cache[62]+1;
        if(gpu_render_cache.state==gpu_render_cache.IDLE && !gpu_s_rd_arvalid && !gpu_s_wr_awvalid && !gpu_render_cache.flush_req) audit_cache[63]<=audit_cache[63]+1;
    end
    audit_last_flush_done<=gpu_cache_flush_done;
end
final begin
    $display("CACHE_REAL hits %0d misses %0d writeback_chunks %0d",audit_real_hits,audit_real_misses,audit_real_writebacks);
    for(audit_i=0;audit_i<64;audit_i=audit_i+1) $display("CACHE_AUDIT %0d %0d",audit_i,audit_cache[audit_i]);
    for(audit_i=0;audit_i<8;audit_i=audit_i+1) $display("CACHE_FLUSH %0d %0d %0d",audit_i,audit_flush_start[audit_i],audit_flush_cycles[audit_i]);
    for(audit_i=0;audit_i<32;audit_i=audit_i+1) if(audit_flush_command[audit_i]) $display("CACHE_BARRIER %0d %0d",audit_i,audit_flush_command[audit_i]);
    for(audit_i=0;audit_i<17;audit_i=audit_i+1) $display("CACHE_BURST %0d %0d %0d",audit_i,audit_read_bursts[audit_i],audit_write_bursts[audit_i]);
    for(audit_i=0;audit_i<3;audit_i=audit_i+1) $display("CACHE_REGION %0d %0d %0d",audit_i,audit_hit_region[audit_i],audit_miss_region[audit_i]);
end
`endif
