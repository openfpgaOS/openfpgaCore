`default_nettype none
module tb_contention(
input wire  clk,
input wire  reset_n,
input wire  mixer_enable,
input wire  voice_wr,
output wire  voice_wr_ready,
input wire [3:0] voice_field,
input wire [4:0] voice_sel,
input wire [4:0] voice_sel_rd,
input wire [31:0] voice_wdata,
output wire  sample_wr,
output wire [31:0] sample_data,
input wire [9:0] fifo_level,
output wire [21:0] pos_readback,
output wire [31:0] voice_end_pending,
output wire [31:0] voice_active_mask,
input wire  m0_arvalid,
output wire  m0_arready,
input wire [31:0] m0_araddr,
input wire [7:0] m0_arlen,
output wire  m0_rvalid,
output wire [31:0] m0_rdata,
output wire [1:0] m0_rresp,
output wire  m0_rlast,
input wire  m0_awvalid,
output wire  m0_awready,
input wire [31:0] m0_awaddr,
input wire [7:0] m0_awlen,
input wire  m0_wvalid,
output wire  m0_wready,
input wire [31:0] m0_wdata,
input wire [3:0] m0_wstrb,
input wire  m0_wlast,
output wire  m0_bvalid,
output wire [1:0] m0_bresp,
input wire  m1_arvalid,
output wire  m1_arready,
input wire [31:0] m1_araddr,
input wire [7:0] m1_arlen,
output wire  m1_rvalid,
output wire [31:0] m1_rdata,
output wire [1:0] m1_rresp,
output wire  m1_rlast,
input wire  m1_rready,
input wire  m1_awvalid,
output wire  m1_awready,
input wire [31:0] m1_awaddr,
input wire [7:0] m1_awlen,
input wire  m1_wvalid,
output wire  m1_wready,
input wire [31:0] m1_wdata,
input wire [3:0] m1_wstrb,
input wire  m1_wlast,
output wire  m1_bvalid,
output wire [1:0] m1_bresp,
output wire [1:0] dbg_arb_state,
output wire  dbg_cpu_pending,
output wire [1:0] dbg_grant,
input wire cache_enable,
input wire [15:0] mem_latency,
input wire [7:0] mem_beat_gap,
input wire periodic_stalls,
output wire audio_ar_fire,
output wire audio_r_fire,
output wire [7:0] audio_arlen,
output wire source_ar_fire,
output wire source_r_fire,
output wire [4:0] fetch_voice,
output wire fetch_cacheable
);
wire  m3_arvalid;
wire  m3_arready;
wire [31:0] m3_araddr;
wire [7:0] m3_arlen;
wire  m3_rvalid;
wire [31:0] m3_rdata;
wire [1:0] m3_rresp;
wire  m3_rlast;
wire  m3_rready;
wire  s_arvalid;
wire  s_arready;
wire [31:0] s_araddr;
wire [7:0] s_arlen;
wire  s_rvalid;
wire  s_rready;
wire [31:0] s_rdata;
wire [1:0] s_rresp;
wire  s_rlast;
wire  s_awvalid;
wire  s_awready;
wire [31:0] s_awaddr;
wire [7:0] s_awlen;
wire  s_wvalid;
wire  s_wready;
wire [31:0] s_wdata;
wire [3:0] s_wstrb;
wire  s_wlast;
wire  s_bvalid;
wire [1:0] s_bresp;
wire  s_wcont;
wire  source_arvalid;
wire  source_arready;
wire [31:0] source_araddr;
wire [7:0] source_arlen;
wire  source_rvalid;
wire [31:0] source_rdata;
wire [1:0] source_rresp;
wire  source_rlast;
wire  source_rready;
audio_mixer mixer(
.clk(clk),
.reset_n(reset_n),
.mixer_enable(mixer_enable),
.voice_wr(voice_wr),
.voice_wr_ready(voice_wr_ready),
.voice_field(voice_field),
.voice_sel(voice_sel),
.voice_sel_rd(voice_sel_rd),
.voice_wdata(voice_wdata),
.master_vol(8'd255),
.group_vol_0(8'd255),
.group_vol_1(8'd255),
.group_vol_2(8'd255),
.group_vol_3(8'd255),
.voice_group_packed(64'd0),
.m_arvalid(source_arvalid),
.m_arready(source_arready),
.m_araddr(source_araddr),
.m_arlen(source_arlen),
.m_rvalid(source_rvalid),
.m_rdata(source_rdata),
.m_rresp(source_rresp),
.m_rlast(source_rlast),
.m_rready(source_rready),
.sample_wr(sample_wr),
.sample_data(sample_data),
.fifo_level(fifo_level),
.pos_readback(pos_readback),
.irq_clear_wr(1'b0),
.irq_clear(32'd0),
.voice_end_pending(voice_end_pending),
.voice_end_irq(),
.voice_active_mask(voice_active_mask),
.fetch_voice(fetch_voice),
.fetch_cacheable(fetch_cacheable)
);

audio_sample_cache cache(
.clk(clk),
.reset_n(reset_n),
.cache_enable(cache_enable && mixer_enable),
.request_cacheable(fetch_cacheable),
.request_voice(fetch_voice),
.invalidate_valid(voice_wr),
.invalidate_voice(voice_sel),
.s_arvalid(source_arvalid),
.s_arready(source_arready),
.s_araddr(source_araddr),
.s_arlen(source_arlen),
.s_rvalid(source_rvalid),
.s_rready(source_rready),
.s_rdata(source_rdata),
.s_rresp(source_rresp),
.s_rlast(source_rlast),
.m_arvalid(m3_arvalid),
.m_arready(m3_arready),
.m_araddr(m3_araddr),
.m_arlen(m3_arlen),
.m_rvalid(m3_rvalid),
.m_rready(m3_rready),
.m_rdata(m3_rdata),
.m_rresp(m3_rresp),
.m_rlast(m3_rlast)
);

assign source_ar_fire = source_arvalid && source_arready;
assign source_r_fire = source_rvalid && source_rready;

axi_sdram_arbiter #(.GPU_WRITE_QUEUE_DEPTH(16),.BUFFERED_SLAVE(0)) arb(
.clk(clk),
.reset_n(reset_n),
.m0_arvalid(m0_arvalid),
.m0_arready(m0_arready),
.m0_araddr(m0_araddr),
.m0_arlen(m0_arlen),
.m0_rvalid(m0_rvalid),
.m0_rdata(m0_rdata),
.m0_rresp(m0_rresp),
.m0_rlast(m0_rlast),
.m0_awvalid(m0_awvalid),
.m0_awready(m0_awready),
.m0_awaddr(m0_awaddr),
.m0_awlen(m0_awlen),
.m0_wvalid(m0_wvalid),
.m0_wready(m0_wready),
.m0_wdata(m0_wdata),
.m0_wstrb(m0_wstrb),
.m0_wlast(m0_wlast),
.m0_bvalid(m0_bvalid),
.m0_bresp(m0_bresp),
.m1_arvalid(m1_arvalid),
.m1_arready(m1_arready),
.m1_araddr(m1_araddr),
.m1_arlen(m1_arlen),
.m1_rvalid(m1_rvalid),
.m1_rdata(m1_rdata),
.m1_rresp(m1_rresp),
.m1_rlast(m1_rlast),
.m1_rready(m1_rready),
.m1_awvalid(m1_awvalid),
.m1_awready(m1_awready),
.m1_awaddr(m1_awaddr),
.m1_awlen(m1_awlen),
.m1_wvalid(m1_wvalid),
.m1_wready(m1_wready),
.m1_wdata(m1_wdata),
.m1_wstrb(m1_wstrb),
.m1_wlast(m1_wlast),
.m1_bvalid(m1_bvalid),
.m1_bresp(m1_bresp),
.m2_arvalid('0),
.m2_arready(),
.m2_araddr('0),
.m2_arlen('0),
.m2_rvalid(),
.m2_rdata(),
.m2_rresp(),
.m2_rlast(),
.m2_rready('0),
.m2_awvalid('0),
.m2_awready(),
.m2_awaddr('0),
.m2_awlen('0),
.m2_wvalid('0),
.m2_wready(),
.m2_wdata('0),
.m2_wstrb('0),
.m2_wlast('0),
.m2_bvalid(),
.m2_bresp(),
.m3_arvalid(m3_arvalid),
.m3_arready(m3_arready),
.m3_araddr(m3_araddr),
.m3_arlen(m3_arlen),
.m3_rvalid(m3_rvalid),
.m3_rdata(m3_rdata),
.m3_rresp(m3_rresp),
.m3_rlast(m3_rlast),
.m3_rready(m3_rready),
.s_arvalid(s_arvalid),
.s_arready(s_arready),
.s_araddr(s_araddr),
.s_arlen(s_arlen),
.s_rvalid(s_rvalid),
.s_rready(s_rready),
.s_rdata(s_rdata),
.s_rresp(s_rresp),
.s_rlast(s_rlast),
.s_awvalid(s_awvalid),
.s_awready(s_awready),
.s_awaddr(s_awaddr),
.s_awlen(s_awlen),
.s_wvalid(s_wvalid),
.s_wready(s_wready),
.s_wdata(s_wdata),
.s_wstrb(s_wstrb),
.s_wlast(s_wlast),
.s_bvalid(s_bvalid),
.s_bresp(s_bresp),
.s_wcont(s_wcont),
.dbg_arb_state(dbg_arb_state),
.dbg_cpu_pending(dbg_cpu_pending),
.dbg_grant(dbg_grant)
);
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

endmodule
`default_nettype wire
