// Single outstanding slave. Deterministic data is address-sensitive; input
// latency and inter-beat gaps are explicit test assumptions, not measured RAM.
reg rd_busy, wr_busy, b_pending;
reg [31:0] rd_addr;
reg [7:0] rd_left, wr_left;
reg [15:0] rd_wait, wr_wait;
reg [12:0] clock_count;
wire blocked = periodic_stalls &&
               ((clock_count < 13'd160) || (clock_count[9:0] < 10'd10));
assign s_arready = !rd_busy && !wr_busy && !b_pending && !blocked;
assign s_awready = !rd_busy && !wr_busy && !b_pending && !blocked;
assign s_rvalid = rd_busy && (rd_wait == 0) && !blocked;
wire [15:0] sample_lo = (rd_addr[16:1] * 16'd37) ^ 16'h2397;
wire [15:0] sample_hi = ((rd_addr[16:1] + 16'd1) * 16'd37) ^ 16'h2397;
assign s_rdata = {sample_hi,sample_lo};
assign s_rresp = 2'd0;
assign s_rlast = rd_left == 0;
assign s_wready = wr_busy && (wr_wait == 0) && !blocked;
assign s_bvalid = b_pending;
assign s_bresp = 2'd0;
assign audio_ar_fire = m3_arvalid && m3_arready;
assign audio_arlen = m3_arlen;
assign audio_r_fire = m3_rvalid && m3_rready;
always @(posedge clk) begin
    if (!reset_n) begin
        rd_busy <= 0; wr_busy <= 0; b_pending <= 0;
        rd_addr <= 0; rd_left <= 0; wr_left <= 0;
        rd_wait <= 0; wr_wait <= 0; clock_count <= 0;
    end else begin
        clock_count <= clock_count + 13'd1;
        b_pending <= 0;
        if (s_arvalid && s_arready) begin
            rd_busy <= 1; rd_addr <= s_araddr; rd_left <= s_arlen;
            rd_wait <= mem_latency;
        end else if (rd_busy) begin
            if (rd_wait != 0) rd_wait <= rd_wait - 16'd1;
            if (s_rvalid && s_rready) begin
                if (rd_left == 0) rd_busy <= 0;
                else begin
                    rd_addr <= rd_addr + 32'd4; rd_left <= rd_left - 8'd1;
                    rd_wait <= {8'd0, mem_beat_gap};
                end
            end
        end
        if (s_awvalid && s_awready) begin
            wr_busy <= 1; wr_left <= s_awlen; wr_wait <= mem_latency;
        end else if (wr_busy) begin
            if (wr_wait != 0) wr_wait <= wr_wait - 16'd1;
            if (s_wvalid && s_wready) begin
                if (wr_left == 0) begin wr_busy <= 0; b_pending <= 1; end
                else begin wr_left <= wr_left - 8'd1; wr_wait <= {8'd0, mem_beat_gap}; end
            end
        end
    end
end
