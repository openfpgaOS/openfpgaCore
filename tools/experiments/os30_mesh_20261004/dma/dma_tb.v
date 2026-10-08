`default_nettype none
module dma_tb (
    input wire clk, reset_n, abort_i,
    input wire desc_valid,
    output wire desc_ready,
    input wire desc_bank,
    input wire [31:0] desc_addr,
    input wire [7:0] desc_dst,
    input wire [8:0] desc_words,
    input wire desc_commit,
    output wire [1:0] bank_owned, bank_ready, bank_error,
    input wire [1:0] bank_release,
    output wire fetch_busy,
    output wire wr_en, wr_bank,
    output wire [7:0] wr_index,
    output wire [31:0] wr_data,
    input wire through_arbiter,
    input wire [2:0] background,
    output wire arvalid,
    input wire arready,
    output wire [31:0] araddr,
    output wire [7:0] arlen,
    input wire rvalid,
    output wire rready,
    input wire [31:0] rdata,
    input wire [1:0] rresp,
    input wire rlast,
    output wire [1:0] dbg_grant,
    output wire [1:0] dbg_state,
    output wire [2:0] bg_granted,
    output wire dma_ar_wait
);
    wire d_arvalid, d_arready, d_rvalid, d_rready, d_rlast;
    wire [31:0] d_araddr, d_rdata;
    wire [7:0] d_arlen;
    wire [1:0] d_rresp;
    assign dma_ar_wait = d_arvalid;
    gpu_mesh_dma dut (
        .clk(clk), .reset_n(reset_n), .abort(abort_i),
        .desc_valid(desc_valid), .desc_ready(desc_ready), .desc_bank(desc_bank),
        .desc_addr(desc_addr), .desc_dst(desc_dst), .desc_words(desc_words),
        .desc_commit(desc_commit), .bank_owned(bank_owned), .bank_ready(bank_ready),
        .bank_error(bank_error), .bank_release(bank_release), .fetch_busy(fetch_busy),
        .wr_en(wr_en), .wr_bank(wr_bank), .wr_index(wr_index), .wr_data(wr_data),
        .m_arvalid(d_arvalid), .m_arready(d_arready), .m_araddr(d_araddr), .m_arlen(d_arlen),
        .m_rvalid(d_rvalid), .m_rready(d_rready), .m_rdata(d_rdata),
        .m_rresp(d_rresp), .m_rlast(d_rlast)
    );
    wire a_arvalid, a_rready, a_m2arready, a_m2rvalid, a_m2rlast;
    wire [31:0] a_araddr, a_m2rdata;
    wire [7:0] a_arlen;
    wire [1:0] a_m2rresp;
    reg [2:0] bg_busy;
    wire [2:0] bg_rvalid, bg_rlast;
    wire [2:0] bg_request = background & ~bg_busy & {3{through_arbiter}};
    always @(posedge clk) begin
        if (!reset_n) bg_busy <= 0;
        else bg_busy <= (bg_busy | (bg_request & bg_granted)) & ~(bg_rvalid & bg_rlast);
    end
    assign arvalid = through_arbiter ? a_arvalid : d_arvalid;
    assign araddr = through_arbiter ? a_araddr : d_araddr;
    assign arlen = through_arbiter ? a_arlen : d_arlen;
    assign rready = through_arbiter ? a_rready : d_rready;
    assign d_arready = through_arbiter ? a_m2arready : arready;
    assign d_rvalid = through_arbiter ? a_m2rvalid : rvalid;
    assign d_rdata = through_arbiter ? a_m2rdata : rdata;
    assign d_rresp = through_arbiter ? a_m2rresp : rresp;
    assign d_rlast = through_arbiter ? a_m2rlast : rlast;
    axi_sdram_arbiter #(.GPU_WRITE_QUEUE_DEPTH(16)) arb (
        .clk(clk), .reset_n(reset_n),
        .m0_arvalid(bg_request[0]), .m0_arready(bg_granted[0]),
        .m0_araddr(32'h10000000), .m0_arlen(8'd7),
        .m0_rvalid(bg_rvalid[0]), .m0_rdata(), .m0_rresp(), .m0_rlast(bg_rlast[0]),
        .m0_awvalid(1'b0), .m0_awready(), .m0_awaddr(32'd0), .m0_awlen(8'd0),
        .m0_wvalid(1'b0), .m0_wready(), .m0_wdata(32'd0), .m0_wstrb(4'd0),
        .m0_wlast(1'b0), .m0_bvalid(), .m0_bresp(),
        .m1_arvalid(bg_request[1]), .m1_arready(bg_granted[1]),
        .m1_araddr(32'h20000000), .m1_arlen(8'd7),
        .m1_rvalid(bg_rvalid[1]), .m1_rready(1'b1), .m1_rdata(), .m1_rresp(), .m1_rlast(bg_rlast[1]),
        .m1_awvalid(1'b0), .m1_awready(), .m1_awaddr(32'd0), .m1_awlen(8'd0),
        .m1_wvalid(1'b0), .m1_wready(), .m1_wdata(32'd0), .m1_wstrb(4'd0),
        .m1_wlast(1'b0), .m1_bvalid(), .m1_bresp(),
        .m2_arvalid(d_arvalid && through_arbiter), .m2_arready(a_m2arready),
        .m2_araddr(d_araddr), .m2_arlen(d_arlen),
        .m2_rvalid(a_m2rvalid), .m2_rready(d_rready), .m2_rdata(a_m2rdata),
        .m2_rresp(a_m2rresp), .m2_rlast(a_m2rlast),
        .m2_awvalid(1'b0), .m2_awready(), .m2_awaddr(32'd0), .m2_awlen(8'd0),
        .m2_wvalid(1'b0), .m2_wready(), .m2_wdata(32'd0), .m2_wstrb(4'd0),
        .m2_wlast(1'b0), .m2_bvalid(), .m2_bresp(),
        .m3_arvalid(bg_request[2]), .m3_arready(bg_granted[2]),
        .m3_araddr(32'h30000000), .m3_arlen(8'd0),
        .m3_rvalid(bg_rvalid[2]), .m3_rready(1'b1), .m3_rdata(), .m3_rresp(), .m3_rlast(bg_rlast[2]),
        .s_arvalid(a_arvalid), .s_arready(arready && through_arbiter),
        .s_araddr(a_araddr), .s_arlen(a_arlen),
        .s_rvalid(rvalid && through_arbiter), .s_rready(a_rready),
        .s_rdata(rdata), .s_rresp(rresp), .s_rlast(rlast),
        .s_awvalid(), .s_awready(1'b0), .s_awaddr(), .s_awlen(),
        .s_wvalid(), .s_wready(1'b0), .s_wdata(), .s_wstrb(), .s_wlast(),
        .s_bvalid(1'b0), .s_bresp(2'b00), .s_wcont(),
        .dbg_arb_state(dbg_state), .dbg_cpu_pending(), .dbg_grant(dbg_grant)
    );
endmodule
`default_nettype wire
