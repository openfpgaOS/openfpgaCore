//
// AXI4 PSRAM Slave Router
//
// Thin wrapper around three target-specific slaves:
//   axi_cram0_slave  — CRAM0  (0x30/0x38)
//   axi_cram1_slave  — CRAM1  (0x31/0x39, via cpu_psram1_cdc)
//   axi_sram_slave   — SRAM   (0x3A)
//
// External interface matches the legacy single-FSM axi_psram_slave so the
// surrounding cpu_system / core_top.v wiring is unchanged.
//
// Routing strategy (kept deliberately simple because cpu_system upstream
// serializes one PSRAM transaction at a time, so only one sub-slave is ever
// non-idle):
//
//   * AR / AW are gated per target (only the matching sub-slave sees the
//     valid signal).  Without this each sub-slave would attempt to claim
//     every transaction.
//   * W beats are passed through to all sub-slaves unchanged.  Sub-slaves
//     only enter their write path on AW handshake, so an idle sub-slave
//     ignores wvalid; only the one that just claimed the AW will react.
//   * R-ready / B-ready are passed through unchanged for the same reason.
//   * arready / awready / wready / rvalid / bvalid from the sub-slaves are
//     OR'd back to the master (only one is non-zero at a time).
//   * rdata / rresp / rlast / bresp are muxed by the latched active target.
//   * The shared psram_rd / psram_wr / psram_addr / psram_wdata / psram_wstrb
//     outputs are driven by whichever sub-slave is currently pulsing rd/wr;
//     OR for the rd/wr pulses themselves and a small mux for addr/wdata/wstrb
//     selected by those pulses.
//
// Sync burst (psram_burst_*) is currently disabled at all sub-slaves; the
// burst signals are tied off here.  When CRAM0 burst is re-enabled the
// burst signals from axi_cram0_slave will be wired through.
//

`default_nettype none

module axi_psram_slave (
    input wire clk,
    input wire reset_n,

    // ===== AXI4 Slave interface (external — same as legacy) =====
    input  wire        s_axi_arvalid,
    output wire        s_axi_arready,
    input  wire [31:0] s_axi_araddr,
    input  wire [7:0]  s_axi_arlen,

    output wire        s_axi_rvalid,
    input  wire        s_axi_rready,
    output wire [31:0] s_axi_rdata,
    output wire [1:0]  s_axi_rresp,
    output wire        s_axi_rlast,

    input  wire        s_axi_awvalid,
    output wire        s_axi_awready,
    input  wire [31:0] s_axi_awaddr,
    input  wire [7:0]  s_axi_awlen,

    input  wire        s_axi_wvalid,
    output wire        s_axi_wready,
    input  wire [31:0] s_axi_wdata,
    input  wire [3:0]  s_axi_wstrb,
    input  wire        s_axi_wlast,

    output wire        s_axi_bvalid,
    input  wire        s_axi_bready,
    output wire [1:0]  s_axi_bresp,

    // ===== Shared PSRAM word interface (downstream mux in core_top.v) =====
    output wire        psram_rd,
    output wire        psram_wr,
    output wire [25:0] psram_addr,
    output wire [31:0] psram_wdata,
    output wire [3:0]  psram_wstrb,
    input  wire [31:0] psram_rdata,
    input  wire        psram_busy,
    input  wire        psram_rdata_valid,

    // ===== CRAM0 sync burst read interface (unused while burst disabled) =====
    output wire        psram_burst_rd,
    output wire [5:0]  psram_burst_len,
    input  wire        psram_burst_rdata_valid,
    input  wire [31:0] psram_burst_rdata
);

// ============================================================
// Address decode (combinational)
// ============================================================
wire ar_is_cram0 = (s_axi_araddr[27:24] == 4'h0) || (s_axi_araddr[27:24] == 4'h8);
wire ar_is_cram1 = (s_axi_araddr[27:24] == 4'h1) || (s_axi_araddr[27:24] == 4'h9);
wire ar_is_sram  = (s_axi_araddr[27:24] == 4'hA);

wire aw_is_cram0 = (s_axi_awaddr[27:24] == 4'h0) || (s_axi_awaddr[27:24] == 4'h8);
wire aw_is_cram1 = (s_axi_awaddr[27:24] == 4'h1) || (s_axi_awaddr[27:24] == 4'h9);
wire aw_is_sram  = (s_axi_awaddr[27:24] == 4'hA);

// ============================================================
// Sub-slave wires
// ============================================================
wire        c0_arready, c0_awready, c0_wready;
wire        c0_rvalid, c0_rlast, c0_bvalid;
wire [31:0] c0_rdata;
wire [1:0]  c0_rresp, c0_bresp;
wire        c0_psram_rd, c0_psram_wr;
wire [25:0] c0_psram_addr;
wire [31:0] c0_psram_wdata;
wire [3:0]  c0_psram_wstrb;

wire        c1_arready, c1_awready, c1_wready;
wire        c1_rvalid, c1_rlast, c1_bvalid;
wire [31:0] c1_rdata;
wire [1:0]  c1_rresp, c1_bresp;
wire        c1_psram_rd, c1_psram_wr;
wire [25:0] c1_psram_addr;
wire [31:0] c1_psram_wdata;
wire [3:0]  c1_psram_wstrb;

wire        sr_arready, sr_awready, sr_wready;
wire        sr_rvalid, sr_rlast, sr_bvalid;
wire [31:0] sr_rdata;
wire [1:0]  sr_rresp, sr_bresp;
wire        sr_psram_rd, sr_psram_wr;
wire [25:0] sr_psram_addr;
wire [31:0] sr_psram_wdata;
wire [3:0]  sr_psram_wstrb;

// ============================================================
// Master-facing aggregations
// ============================================================
// Because cpu_system upstream serializes one transaction at a time, only
// one sub-slave is ever in a non-idle state.  We can OR all of their valid /
// ready signals and mux response data by whichever one is currently
// asserting valid.  No latched "active target" register needed.

assign s_axi_arready = c0_arready | c1_arready | sr_arready;
assign s_axi_awready = c0_awready | c1_awready | sr_awready;
assign s_axi_wready  = c0_wready  | c1_wready  | sr_wready;

assign s_axi_rvalid  = c0_rvalid  | c1_rvalid  | sr_rvalid;
assign s_axi_rdata   = c0_rvalid  ? c0_rdata
                     : c1_rvalid  ? c1_rdata
                     :              sr_rdata;
assign s_axi_rresp   = c0_rvalid  ? c0_rresp
                     : c1_rvalid  ? c1_rresp
                     :              sr_rresp;
assign s_axi_rlast   = c0_rvalid  ? c0_rlast
                     : c1_rvalid  ? c1_rlast
                     :              sr_rlast;

assign s_axi_bvalid  = c0_bvalid  | c1_bvalid  | sr_bvalid;
assign s_axi_bresp   = c0_bvalid  ? c0_bresp
                     : c1_bvalid  ? c1_bresp
                     :              sr_bresp;

// ============================================================
// Shared PSRAM word interface
// ============================================================
// rd/wr pulses are one-cycle assertions from whichever sub-slave is issuing
// a command.  At most one is high at a time, so OR them.
assign psram_rd = c0_psram_rd | c1_psram_rd | sr_psram_rd;
assign psram_wr = c0_psram_wr | c1_psram_wr | sr_psram_wr;

// addr/wdata/wstrb are registered persistently in each sub-slave; mux them
// using the rd/wr pulse from the same sub-slave so the right values reach
// the backend on the cycle the command is issued.
wire c0_drives = c0_psram_rd | c0_psram_wr;
wire c1_drives = c1_psram_rd | c1_psram_wr;

assign psram_addr  = c0_drives ? c0_psram_addr
                   : c1_drives ? c1_psram_addr
                   :             sr_psram_addr;
assign psram_wdata = c0_drives ? c0_psram_wdata
                   : c1_drives ? c1_psram_wdata
                   :             sr_psram_wdata;
assign psram_wstrb = c0_drives ? c0_psram_wstrb
                   : c1_drives ? c1_psram_wstrb
                   :             sr_psram_wstrb;

// Burst interface — currently unused.
assign psram_burst_rd  = 1'b0;
assign psram_burst_len = 6'b0;

// ============================================================
// Sub-slave instantiations
// ============================================================
//
// All sub-slaves see the same s_axi_araddr / s_axi_awaddr / s_axi_wdata,
// etc.  Only their *_arvalid and *_awvalid are gated by target match;
// everything else is passed through unchanged because cpu_system upstream
// serializes one transaction at a time.

axi_cram0_slave u_cram0 (
    .clk        (clk),
    .reset_n    (reset_n),

    .s_axi_arvalid (s_axi_arvalid & ar_is_cram0),
    .s_axi_arready (c0_arready),
    .s_axi_araddr  (s_axi_araddr),
    .s_axi_arlen   (s_axi_arlen),

    .s_axi_rvalid  (c0_rvalid),
    .s_axi_rready  (s_axi_rready),
    .s_axi_rdata   (c0_rdata),
    .s_axi_rresp   (c0_rresp),
    .s_axi_rlast   (c0_rlast),

    .s_axi_awvalid (s_axi_awvalid & aw_is_cram0),
    .s_axi_awready (c0_awready),
    .s_axi_awaddr  (s_axi_awaddr),
    .s_axi_awlen   (s_axi_awlen),

    .s_axi_wvalid  (s_axi_wvalid),
    .s_axi_wready  (c0_wready),
    .s_axi_wdata   (s_axi_wdata),
    .s_axi_wstrb   (s_axi_wstrb),
    .s_axi_wlast   (s_axi_wlast),

    .s_axi_bvalid  (c0_bvalid),
    .s_axi_bready  (s_axi_bready),
    .s_axi_bresp   (c0_bresp),

    .psram_rd          (c0_psram_rd),
    .psram_wr          (c0_psram_wr),
    .psram_addr        (c0_psram_addr),
    .psram_wdata       (c0_psram_wdata),
    .psram_wstrb       (c0_psram_wstrb),
    .psram_rdata       (psram_rdata),
    .psram_busy        (psram_busy),
    .psram_rdata_valid (psram_rdata_valid)
);

axi_cram1_slave u_cram1 (
    .clk        (clk),
    .reset_n    (reset_n),

    .s_axi_arvalid (s_axi_arvalid & ar_is_cram1),
    .s_axi_arready (c1_arready),
    .s_axi_araddr  (s_axi_araddr),
    .s_axi_arlen   (s_axi_arlen),

    .s_axi_rvalid  (c1_rvalid),
    .s_axi_rready  (s_axi_rready),
    .s_axi_rdata   (c1_rdata),
    .s_axi_rresp   (c1_rresp),
    .s_axi_rlast   (c1_rlast),

    .s_axi_awvalid (s_axi_awvalid & aw_is_cram1),
    .s_axi_awready (c1_awready),
    .s_axi_awaddr  (s_axi_awaddr),
    .s_axi_awlen   (s_axi_awlen),

    .s_axi_wvalid  (s_axi_wvalid),
    .s_axi_wready  (c1_wready),
    .s_axi_wdata   (s_axi_wdata),
    .s_axi_wstrb   (s_axi_wstrb),
    .s_axi_wlast   (s_axi_wlast),

    .s_axi_bvalid  (c1_bvalid),
    .s_axi_bready  (s_axi_bready),
    .s_axi_bresp   (c1_bresp),

    .psram_rd          (c1_psram_rd),
    .psram_wr          (c1_psram_wr),
    .psram_addr        (c1_psram_addr),
    .psram_wdata       (c1_psram_wdata),
    .psram_wstrb       (c1_psram_wstrb),
    .psram_rdata       (psram_rdata),
    .psram_busy        (psram_busy),
    .psram_rdata_valid (psram_rdata_valid)
);

axi_sram_slave u_sram (
    .clk        (clk),
    .reset_n    (reset_n),

    .s_axi_arvalid (s_axi_arvalid & ar_is_sram),
    .s_axi_arready (sr_arready),
    .s_axi_araddr  (s_axi_araddr),
    .s_axi_arlen   (s_axi_arlen),

    .s_axi_rvalid  (sr_rvalid),
    .s_axi_rready  (s_axi_rready),
    .s_axi_rdata   (sr_rdata),
    .s_axi_rresp   (sr_rresp),
    .s_axi_rlast   (sr_rlast),

    .s_axi_awvalid (s_axi_awvalid & aw_is_sram),
    .s_axi_awready (sr_awready),
    .s_axi_awaddr  (s_axi_awaddr),
    .s_axi_awlen   (s_axi_awlen),

    .s_axi_wvalid  (s_axi_wvalid),
    .s_axi_wready  (sr_wready),
    .s_axi_wdata   (s_axi_wdata),
    .s_axi_wstrb   (s_axi_wstrb),
    .s_axi_wlast   (s_axi_wlast),

    .s_axi_bvalid  (sr_bvalid),
    .s_axi_bready  (s_axi_bready),
    .s_axi_bresp   (sr_bresp),

    .psram_rd          (sr_psram_rd),
    .psram_wr          (sr_psram_wr),
    .psram_addr        (sr_psram_addr),
    .psram_wdata       (sr_psram_wdata),
    .psram_wstrb       (sr_psram_wstrb),
    .psram_rdata       (psram_rdata),
    .psram_busy        (psram_busy),
    .psram_rdata_valid (psram_rdata_valid)
);

endmodule
