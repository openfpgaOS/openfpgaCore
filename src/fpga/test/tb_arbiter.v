//
// Verilator testbench for axi_sdram_arbiter.v.
//
// Probes the read-vs-write priority decision the arbiter makes at
// ST_IDLE (lines 187-196 of axi_sdram_arbiter.v): m0_arvalid is
// checked BEFORE m0_awvalid, so a steady GPU read stream can
// prevent the GPU's own write channel from ever being granted —
// the suspected wedge behind the Duke3D fbss=FBSS_FLUSH_W_RSP hang.
//
// The slave port is implemented inside this module as a tiny stub:
// 1-cycle AR/AW accept, N+1 single-cycle R beats, B one cycle after
// W.last.  Keeps the test focused on the arbiter's grant FSM, not
// on the SDRAM controller.
//

`default_nettype none

module tb_arbiter (
    input  wire        clk,
    input  wire        reset_n,

    // Master 0: GPU (read + write)
    input  wire        m0_arvalid,
    output wire        m0_arready,
    input  wire [31:0] m0_araddr,
    input  wire [7:0]  m0_arlen,
    output wire        m0_rvalid,
    output wire [31:0] m0_rdata,
    output wire [1:0]  m0_rresp,
    output wire        m0_rlast,
    input  wire        m0_awvalid,
    output wire        m0_awready,
    input  wire [31:0] m0_awaddr,
    input  wire [7:0]  m0_awlen,
    input  wire        m0_wvalid,
    output wire        m0_wready,
    input  wire [31:0] m0_wdata,
    input  wire [3:0]  m0_wstrb,
    input  wire        m0_wlast,
    output wire        m0_bvalid,
    output wire [1:0]  m0_bresp,

    // Master 1: CPU (read + write)
    input  wire        m1_arvalid,
    output wire        m1_arready,
    input  wire [31:0] m1_araddr,
    input  wire [7:0]  m1_arlen,
    output wire        m1_rvalid,
    output wire [31:0] m1_rdata,
    output wire [1:0]  m1_rresp,
    output wire        m1_rlast,
    input  wire        m1_rready,
    input  wire        m1_awvalid,
    output wire        m1_awready,
    input  wire [31:0] m1_awaddr,
    input  wire [7:0]  m1_awlen,
    input  wire        m1_wvalid,
    output wire        m1_wready,
    input  wire [31:0] m1_wdata,
    input  wire [3:0]  m1_wstrb,
    input  wire        m1_wlast,
    output wire        m1_bvalid,
    output wire [1:0]  m1_bresp,

    // Master 3: Audio Mixer (read-only, lowest priority)
    input  wire        m3_arvalid,
    output wire        m3_arready,
    input  wire [31:0] m3_araddr,
    input  wire [7:0]  m3_arlen,
    output wire        m3_rvalid,
    output wire [31:0] m3_rdata,
    output wire [1:0]  m3_rresp,
    output wire        m3_rlast,

    // Observability — exposed to the C++ harness
    output wire [1:0]  dbg_grant,
    output wire [1:0]  dbg_arb_state,
    output wire        dbg_s_arvalid,
    output wire        dbg_s_arready,
    output wire        dbg_s_awvalid,
    output wire        dbg_s_awready,
    output wire [7:0]  dbg_s_awlen,
    output wire        dbg_s_wvalid,
    output wire        dbg_s_wready,
    output wire        dbg_s_wlast,
    output wire [31:0] dbg_s_wdata,
    output wire        dbg_s_bvalid,
    output wire        dbg_s_rvalid,
    output wire        dbg_s_rlast,
    output wire [4:0]  dbg_gpu_wq_count
);

// ============================================================
// DUT ↔ stub slave wires
// ============================================================
wire        s_arvalid;
wire        s_arready;
wire [31:0] s_araddr;
wire [7:0]  s_arlen;
wire        s_rvalid;
wire        s_rready;
wire [31:0] s_rdata;
wire [1:0]  s_rresp;
wire        s_rlast;
wire        s_awvalid;
wire        s_awready;
wire [31:0] s_awaddr;
wire [7:0]  s_awlen;
wire        s_wvalid;
wire        s_wready;
wire [31:0] s_wdata;
wire [3:0]  s_wstrb;
wire        s_wlast;
wire        s_bvalid;
wire [1:0]  s_bresp;
wire        dut_dbg_cpu_pending;
wire        m2_awready_unused;
wire        m2_wready_unused;
wire        m2_bvalid_unused;
wire [1:0]  m2_bresp_unused;

axi_sdram_arbiter dut (
    .clk        (clk),
    .reset_n    (reset_n),

    .m0_arvalid (m0_arvalid),
    .m0_arready (m0_arready),
    .m0_araddr  (m0_araddr),
    .m0_arlen   (m0_arlen),
    .m0_rvalid  (m0_rvalid),
    .m0_rdata   (m0_rdata),
    .m0_rresp   (m0_rresp),
    .m0_rlast   (m0_rlast),
    .m0_awvalid (m0_awvalid),
    .m0_awready (m0_awready),
    .m0_awaddr  (m0_awaddr),
    .m0_awlen   (m0_awlen),
    .m0_wvalid  (m0_wvalid),
    .m0_wready  (m0_wready),
    .m0_wdata   (m0_wdata),
    .m0_wstrb   (m0_wstrb),
    .m0_wlast   (m0_wlast),
    .m0_bvalid  (m0_bvalid),
    .m0_bresp   (m0_bresp),

    .m1_arvalid (m1_arvalid),
    .m1_arready (m1_arready),
    .m1_araddr  (m1_araddr),
    .m1_arlen   (m1_arlen),
    .m1_rvalid  (m1_rvalid),
    .m1_rdata   (m1_rdata),
    .m1_rresp   (m1_rresp),
    .m1_rlast   (m1_rlast),
    .m1_rready  (m1_rready),
    .m1_awvalid (m1_awvalid),
    .m1_awready (m1_awready),
    .m1_awaddr  (m1_awaddr),
    .m1_awlen   (m1_awlen),
    .m1_wvalid  (m1_wvalid),
    .m1_wready  (m1_wready),
    .m1_wdata   (m1_wdata),
    .m1_wstrb   (m1_wstrb),
    .m1_wlast   (m1_wlast),
    .m1_bvalid  (m1_bvalid),
    .m1_bresp   (m1_bresp),

    .m2_awvalid (1'b0),
    .m2_awready (m2_awready_unused),
    .m2_awaddr  (32'd0),
    .m2_awlen   (8'd0),
    .m2_wvalid  (1'b0),
    .m2_wready  (m2_wready_unused),
    .m2_wdata   (32'd0),
    .m2_wstrb   (4'd0),
    .m2_wlast   (1'b0),
    .m2_bvalid  (m2_bvalid_unused),
    .m2_bresp   (m2_bresp_unused),

    .m3_arvalid (m3_arvalid),
    .m3_arready (m3_arready),
    .m3_araddr  (m3_araddr),
    .m3_arlen   (m3_arlen),
    .m3_rvalid  (m3_rvalid),
    .m3_rdata   (m3_rdata),
    .m3_rresp   (m3_rresp),
    .m3_rlast   (m3_rlast),
    .m3_rready  (1'b1),

    .s_arvalid  (s_arvalid),
    .s_arready  (s_arready),
    .s_araddr   (s_araddr),
    .s_arlen    (s_arlen),
    .s_rvalid   (s_rvalid),
    .s_rready   (s_rready),
    .s_rdata    (s_rdata),
    .s_rresp    (s_rresp),
    .s_rlast    (s_rlast),
    .s_awvalid  (s_awvalid),
    .s_awready  (s_awready),
    .s_awaddr   (s_awaddr),
    .s_awlen    (s_awlen),
    .s_wvalid   (s_wvalid),
    .s_wready   (s_wready),
    .s_wdata    (s_wdata),
    .s_wstrb    (s_wstrb),
    .s_wlast    (s_wlast),
    .s_bvalid   (s_bvalid),
    .s_bresp    (s_bresp),
    .s_wcont    (),

    .dbg_arb_state(dbg_arb_state),
    .dbg_cpu_pending(dut_dbg_cpu_pending),
    .dbg_grant(dbg_grant)
);

// ============================================================
// Stub SDRAM slave — a tiny single-outstanding model.
//
// Accepts AR in 1 cycle, then emits N+1 R beats back-to-back with
// rlast on the last beat.  Accepts AW + W beats, asserts B one
// cycle after the W-last beat.  Read- and write-serving FSMs are
// independent so we don't introduce extra serialization beyond the
// arbiter itself.
// ============================================================

reg [1:0] sl_rd_state;
reg [7:0] sl_rd_remaining;
reg [31:0] sl_rd_data;

localparam SL_RD_IDLE = 2'd0;
localparam SL_RD_RUN  = 2'd1;

assign s_arready = (sl_rd_state == SL_RD_IDLE);
assign s_rvalid  = (sl_rd_state == SL_RD_RUN);
assign s_rdata   = sl_rd_data;
assign s_rresp   = 2'b00;
assign s_rlast   = (sl_rd_state == SL_RD_RUN) && (sl_rd_remaining == 8'd0);

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        sl_rd_state     <= SL_RD_IDLE;
        sl_rd_remaining <= 8'd0;
        sl_rd_data      <= 32'd0;
    end else begin
        case (sl_rd_state)
        SL_RD_IDLE: if (s_arvalid && s_arready) begin
            sl_rd_state     <= SL_RD_RUN;
            sl_rd_remaining <= s_arlen;
            sl_rd_data      <= s_araddr;  // any deterministic payload
        end
        SL_RD_RUN: if (s_rvalid && s_rready) begin
            if (sl_rd_remaining == 8'd0) begin
                sl_rd_state <= SL_RD_IDLE;
            end else begin
                sl_rd_remaining <= sl_rd_remaining - 8'd1;
                sl_rd_data      <= sl_rd_data + 32'd4;
            end
        end
        default: sl_rd_state <= SL_RD_IDLE;
        endcase
    end
end

reg [2:0] sl_wr_state;
reg [7:0] sl_wr_remaining;

localparam SL_WR_IDLE = 3'd0;
localparam SL_WR_DATA = 3'd1;
localparam SL_WR_RESP = 3'd2;

assign s_awready = (sl_wr_state == SL_WR_IDLE);
assign s_wready  = (sl_wr_state == SL_WR_DATA);
assign s_bvalid  = (sl_wr_state == SL_WR_RESP);
assign s_bresp   = 2'b00;

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        sl_wr_state     <= SL_WR_IDLE;
        sl_wr_remaining <= 8'd0;
    end else begin
        case (sl_wr_state)
        SL_WR_IDLE: if (s_awvalid && s_awready) begin
            sl_wr_state     <= SL_WR_DATA;
            sl_wr_remaining <= s_awlen;
        end
        SL_WR_DATA: if (s_wvalid && s_wready) begin
            if (sl_wr_remaining == 8'd0) begin
                sl_wr_state <= SL_WR_RESP;
            end else begin
                sl_wr_remaining <= sl_wr_remaining - 8'd1;
            end
        end
        SL_WR_RESP: begin
            // The arbiter has no bready — bvalid is consumed on the
            // cycle it asserts (matching the real axi_sdram_slave
            // contract).  Drop back to IDLE next cycle.
            sl_wr_state <= SL_WR_IDLE;
        end
        default: sl_wr_state <= SL_WR_IDLE;
        endcase
    end
end

// ============================================================
// Observability
// ============================================================
assign dbg_s_arvalid = s_arvalid;
assign dbg_s_arready = s_arready;
assign dbg_s_awvalid = s_awvalid;
assign dbg_s_awready = s_awready;
assign dbg_s_awlen   = s_awlen;
assign dbg_s_wvalid  = s_wvalid;
assign dbg_s_wready  = s_wready;
assign dbg_s_wlast   = s_wlast;
assign dbg_s_wdata   = s_wdata;
assign dbg_s_bvalid  = s_bvalid;
assign dbg_s_rvalid  = s_rvalid;
assign dbg_s_rlast   = s_rlast;
assign dbg_gpu_wq_count = dut.gpu_wq_count;

endmodule

`default_nettype wire
