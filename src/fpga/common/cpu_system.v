//
// VexiiRiscv CPU System — 3-master × 3-target AXI4 router (v2).
//
// Three CPU AXI4 master inputs fan out to three downstream slaves.
// Each target is handled by an independent `cpu_target_port` with its
// own read and write sub-FSMs, so many transactions can overlap as
// long as they hit distinct target ports:
//
//   i_axi   (L1 I$ refills, read-only)    ───┐
//   mem_axi (L1 D$ + cbo.*)                  │
//   p_axi   (uncached LSU IO)                ├──→  3 target ports
//                                            │     (SDRAM / CRAM0 /
//                                            │      LOCAL)
//                                            │
//
// Address decode → target:
//    0x10000000–0x13FFFFFF, 0x50000000–0x53FFFFFF → SDRAM
//    0x30000000 / 0x38000000                      → CRAM0
//    everything else                              → LOCAL
//
// v2 changes vs the previous iteration:
//   - CRAM1 target port removed: the CRAM1 chip is retired.
//   - SRAM target port removed: SRAM is GPU-private and off the fabric.
//

`default_nettype none

module cpu_system (
    input wire clk,
    input wire reset_n,

    // ---- SDRAM AXI4 master ---------------------------------------
    output wire        m_sdram_arvalid,
    input  wire        m_sdram_arready,
    output wire [31:0] m_sdram_araddr,
    output wire [7:0]  m_sdram_arlen,

    input  wire        m_sdram_rvalid,
    output wire        m_sdram_rready,
    input  wire [31:0] m_sdram_rdata,
    input  wire [1:0]  m_sdram_rresp,
    input  wire        m_sdram_rlast,

    output wire        m_sdram_awvalid,
    input  wire        m_sdram_awready,
    output wire [31:0] m_sdram_awaddr,
    output wire [7:0]  m_sdram_awlen,

    output wire        m_sdram_wvalid,
    input  wire        m_sdram_wready,
    output wire [31:0] m_sdram_wdata,
    output wire [3:0]  m_sdram_wstrb,
    output wire        m_sdram_wlast,

    input  wire        m_sdram_bvalid,
    input  wire [1:0]  m_sdram_bresp,

    // ---- CRAM0 AXI4 master ---------------------------------------
    output wire        m_cram0_arvalid,
    input  wire        m_cram0_arready,
    output wire [31:0] m_cram0_araddr,
    output wire [7:0]  m_cram0_arlen,
    input  wire        m_cram0_rvalid,
    output wire        m_cram0_rready,
    input  wire [31:0] m_cram0_rdata,
    input  wire [1:0]  m_cram0_rresp,
    input  wire        m_cram0_rlast,
    output wire        m_cram0_awvalid,
    input  wire        m_cram0_awready,
    output wire [31:0] m_cram0_awaddr,
    output wire [7:0]  m_cram0_awlen,
    output wire        m_cram0_wvalid,
    input  wire        m_cram0_wready,
    output wire [31:0] m_cram0_wdata,
    output wire [3:0]  m_cram0_wstrb,
    output wire        m_cram0_wlast,
    input  wire        m_cram0_bvalid,
    input  wire [1:0]  m_cram0_bresp,

    // ---- Local peripheral AXI4 master ----------------------------
    output wire        m_local_arvalid,
    input  wire        m_local_arready,
    output wire [31:0] m_local_araddr,
    output wire [7:0]  m_local_arlen,

    input  wire        m_local_rvalid,
    output wire        m_local_rready,
    input  wire [31:0] m_local_rdata,
    input  wire [1:0]  m_local_rresp,
    input  wire        m_local_rlast,

    output wire        m_local_awvalid,
    input  wire        m_local_awready,
    output wire [31:0] m_local_awaddr,
    output wire [7:0]  m_local_awlen,

    output wire        m_local_wvalid,
    input  wire        m_local_wready,
    output wire [31:0] m_local_wdata,
    output wire [3:0]  m_local_wstrb,
    output wire        m_local_wlast,

    input  wire        m_local_bvalid,
    input  wire [1:0]  m_local_bresp,

    // ---- Interrupts ---------------------------------------------
    input  wire        int_m_external,
    input  wire        int_m_timer
);

wire reset = ~reset_n;

// ============================================================
// CPU bus signals — maps the stock VexiiRiscv's three bus interfaces
// onto our internal three-master router.
//   i_axi   : FetchL1Axi4Plugin (read-only, I$ refills, 1-bit id)
//   mem_axi : LsuL1Axi4Plugin   (full R/W, D$ refills/writebacks + cbo.*)
//   p_axi   : LsuPlugin native cmd/rsp converted to single-beat AXI4
//             (uncached LSU IO — MMIO, framebuffer writes, etc.)
// ============================================================

// i_axi
wire        i_arvalid;  wire        i_arready;
wire [31:0] i_araddr;
wire [0:0]  i_arid;
wire [7:0]  i_arlen;
wire [2:0]  i_arsize;   wire [1:0]  i_arburst;
wire        i_rvalid;   wire        i_rready;
wire [31:0] i_rdata;
wire [0:0]  i_rid;      wire [1:0]  i_rresp;
wire        i_rlast;

// i_axi has AW/W/B ports on the CPU top but VexiiRiscv ties them off
// internally (read-only bridge), so the cpu_system side only connects R.
wire        i_awvalid_tie;
wire        i_wvalid_tie;
wire [0:0]  i_bid_tie;
wire [1:0]  i_bresp_tie;

// mem_axi (D$ cached)
wire        mem_arvalid;  wire        mem_arready;
wire [31:0] mem_araddr;
wire [1:0]  mem_arid;
wire [7:0]  mem_arlen;
wire [2:0]  mem_arsize;   wire [1:0]  mem_arburst;
wire        mem_rvalid;   wire        mem_rready;
wire [31:0] mem_rdata;
wire [1:0]  mem_rid;      wire [1:0]  mem_rresp;
wire        mem_rlast;

wire        mem_awvalid;  wire        mem_awready;
wire [31:0] mem_awaddr;
wire [1:0]  mem_awid;
wire [7:0]  mem_awlen;
wire [2:0]  mem_awsize;   wire [1:0]  mem_awburst;
wire        mem_awallStrb;
wire        mem_wvalid;   wire        mem_wready;
wire [31:0] mem_wdata;    wire [3:0]  mem_wstrb;
wire        mem_wlast;
wire        mem_bvalid;   wire        mem_bready;
wire [1:0]  mem_bid;      wire [1:0]  mem_bresp;

// p_axi (uncached IO)
wire        per_arvalid;  wire        per_arready;
wire [31:0] per_araddr;
wire [7:0]  per_arlen;
wire [2:0]  per_arsize;   wire [1:0]  per_arburst;
wire        per_rvalid;   wire        per_rready;
wire [31:0] per_rdata;    wire [1:0]  per_rresp;
wire        per_rlast;

wire        per_awvalid;  wire        per_awready;
wire [31:0] per_awaddr;
wire [7:0]  per_awlen;
wire [2:0]  per_awsize;   wire [1:0]  per_awburst;
wire        per_awallStrb;
wire        per_wvalid;   wire        per_wready;
wire [31:0] per_wdata;    wire [3:0]  per_wstrb;
wire        per_wlast;
wire        per_bvalid;   wire        per_bready;
wire [1:0]  per_bresp;

// ============================================================
// Native LsuPlugin cmd/rsp bus → per_* AXI4 shim
//
// VexiiRiscv (stock Generate) exposes the uncached LSU path as a
// non-AXI cmd/rsp bus.  We convert it to single-beat AXI4 here so
// downstream cpu_target_ports keep their uniform interface.  The
// CPU stalls on rsp, so one outstanding transaction max.
// ============================================================
wire        lsu_cmd_valid;
wire        lsu_cmd_ready;
wire        lsu_cmd_write;
wire [31:0] lsu_cmd_addr;
wire [31:0] lsu_cmd_data;
wire [1:0]  lsu_cmd_size;   // AXI size: 0=1B, 1=2B, 2=4B
wire [3:0]  lsu_cmd_mask;
wire        lsu_rsp_valid;
wire        lsu_rsp_error;
wire [31:0] lsu_rsp_data;

// Single-transaction tracker — one native cmd ↔ one AXI transaction.
reg lsu_inflight_read;    // AR accepted, waiting for R
reg lsu_inflight_write;   // AW+W accepted, waiting for B
reg lsu_ar_sent;
reg lsu_aw_sent, lsu_w_sent;

// The native LSU cmd payload is only guaranteed stable until cmd_ready.
// Hold a private copy while the downstream AXI target accepts AW/W/AR.
reg        lsu_req_write;
reg [31:0] lsu_req_addr;
reg [31:0] lsu_req_data;
reg [1:0]  lsu_req_size;
reg [3:0]  lsu_req_mask;

wire lsu_cmd_can_accept = !lsu_inflight_read && !lsu_inflight_write;
wire lsu_cmd_fire = lsu_cmd_valid && lsu_cmd_can_accept;

always @(posedge clk or posedge reset) begin
    if (reset) begin
        lsu_inflight_read  <= 1'b0;
        lsu_inflight_write <= 1'b0;
        lsu_ar_sent        <= 1'b0;
        lsu_aw_sent        <= 1'b0;
        lsu_w_sent         <= 1'b0;
        lsu_req_write      <= 1'b0;
        lsu_req_addr       <= 32'b0;
        lsu_req_data       <= 32'b0;
        lsu_req_size       <= 2'b0;
        lsu_req_mask       <= 4'b0;
    end else begin
        if (lsu_cmd_fire) begin
            lsu_req_write <= lsu_cmd_write;
            lsu_req_addr  <= lsu_cmd_addr;
            lsu_req_data  <= lsu_cmd_data;
            lsu_req_size  <= lsu_cmd_size;
            lsu_req_mask  <= lsu_cmd_mask;
        end

        // Read issue
        if (per_arvalid && per_arready) lsu_ar_sent <= 1'b1;
        if (per_rvalid  && per_rready && per_rlast) begin
            lsu_inflight_read <= 1'b0;
            lsu_ar_sent       <= 1'b0;
        end else if (lsu_cmd_fire && !lsu_cmd_write) begin
            lsu_inflight_read <= 1'b1;
        end

        // Write issue
        if (per_awvalid && per_awready) lsu_aw_sent <= 1'b1;
        if (per_wvalid  && per_wready)  lsu_w_sent  <= 1'b1;
        if (per_bvalid  && per_bready) begin
            lsu_inflight_write <= 1'b0;
            lsu_aw_sent        <= 1'b0;
            lsu_w_sent         <= 1'b0;
        end else if (lsu_cmd_fire && lsu_cmd_write) begin
            lsu_inflight_write <= 1'b1;
        end
    end
end

// AR channel — drive while we have a read in flight but AR not yet accepted.
assign per_arvalid = lsu_inflight_read & ~lsu_ar_sent;
assign per_araddr  = lsu_req_addr;
assign per_arlen   = 8'd0;           // single beat
assign per_arsize  = {1'b0, lsu_req_size};
assign per_arburst = 2'b01;          // INCR
assign per_rready  = 1'b1;           // always accept the single R beat

// AW/W channels — single-beat write.
assign per_awvalid    = lsu_inflight_write & ~lsu_aw_sent;
assign per_awaddr     = lsu_req_addr;
assign per_awlen      = 8'd0;
assign per_awsize     = {1'b0, lsu_req_size};
assign per_awburst    = 2'b01;
assign per_awallStrb  = &lsu_req_mask;
assign per_wvalid     = lsu_inflight_write & ~lsu_w_sent;
assign per_wdata      = lsu_req_data;
assign per_wstrb      = lsu_req_mask;
assign per_wlast      = 1'b1;
assign per_bready     = 1'b1;

// Accept exactly one native command, then hold its payload until response.
assign lsu_cmd_ready = lsu_cmd_can_accept;

// Return the rsp beat when R or B lands.
assign lsu_rsp_valid = (per_rvalid & per_rready & per_rlast)
                     | (per_bvalid & per_bready);
assign lsu_rsp_data  = per_rdata;
assign lsu_rsp_error = lsu_req_write ? (per_bresp != 2'b00)
                                     : (per_rresp != 2'b00);

// i_axi AW/W/B tie-offs retained for backwards-compatible placeholders.
assign i_awvalid_tie = 1'b0;
assign i_wvalid_tie  = 1'b0;
assign i_bid_tie     = 1'b0;
assign i_bresp_tie   = 2'b00;

VexiiRiscv cpu (
    .clk  (clk),
    .reset(reset),

    // Privileged time counter — tie to zero (we don't use rdtime from the CSR).
    .PrivilegedPlugin_logic_rdtime(64'd0),

    .PrivilegedPlugin_logic_harts_0_int_m_timer   (int_m_timer),
    .PrivilegedPlugin_logic_harts_0_int_m_software(1'b0),
    .PrivilegedPlugin_logic_harts_0_int_m_external(int_m_external),

    // FetchL1Axi4 (I-cache refills, read-only)
    .FetchL1Axi4Plugin_logic_axi_ar_valid        (i_arvalid),
    .FetchL1Axi4Plugin_logic_axi_ar_ready        (i_arready),
    .FetchL1Axi4Plugin_logic_axi_ar_payload_addr (i_araddr),
    .FetchL1Axi4Plugin_logic_axi_ar_payload_id   (i_arid),
    .FetchL1Axi4Plugin_logic_axi_ar_payload_len  (i_arlen),
    .FetchL1Axi4Plugin_logic_axi_ar_payload_size (i_arsize),
    .FetchL1Axi4Plugin_logic_axi_ar_payload_burst(i_arburst),
    .FetchL1Axi4Plugin_logic_axi_ar_payload_cache(),
    .FetchL1Axi4Plugin_logic_axi_ar_payload_prot (),
    .FetchL1Axi4Plugin_logic_axi_r_valid         (i_rvalid),
    .FetchL1Axi4Plugin_logic_axi_r_ready         (i_rready),
    .FetchL1Axi4Plugin_logic_axi_r_payload_data  (i_rdata),
    .FetchL1Axi4Plugin_logic_axi_r_payload_id    (i_rid),
    .FetchL1Axi4Plugin_logic_axi_r_payload_resp  (i_rresp),
    .FetchL1Axi4Plugin_logic_axi_r_payload_last  (i_rlast),

    // LsuL1Axi4 (D-cache refills/writebacks + cbo.*)
    .LsuL1Axi4Plugin_logic_axi_aw_valid        (mem_awvalid),
    .LsuL1Axi4Plugin_logic_axi_aw_ready        (mem_awready),
    .LsuL1Axi4Plugin_logic_axi_aw_payload_addr (mem_awaddr),
    .LsuL1Axi4Plugin_logic_axi_aw_payload_id   (mem_awid),
    .LsuL1Axi4Plugin_logic_axi_aw_payload_len  (mem_awlen),
    .LsuL1Axi4Plugin_logic_axi_aw_payload_size (mem_awsize),
    .LsuL1Axi4Plugin_logic_axi_aw_payload_burst(mem_awburst),
    .LsuL1Axi4Plugin_logic_axi_aw_payload_cache(),
    .LsuL1Axi4Plugin_logic_axi_aw_payload_prot (),
    .LsuL1Axi4Plugin_logic_axi_w_valid         (mem_wvalid),
    .LsuL1Axi4Plugin_logic_axi_w_ready         (mem_wready),
    .LsuL1Axi4Plugin_logic_axi_w_payload_data  (mem_wdata),
    .LsuL1Axi4Plugin_logic_axi_w_payload_strb  (mem_wstrb),
    .LsuL1Axi4Plugin_logic_axi_w_payload_last  (mem_wlast),
    .LsuL1Axi4Plugin_logic_axi_b_valid         (mem_bvalid),
    .LsuL1Axi4Plugin_logic_axi_b_ready         (mem_bready),
    .LsuL1Axi4Plugin_logic_axi_b_payload_id    (mem_bid),
    .LsuL1Axi4Plugin_logic_axi_b_payload_resp  (mem_bresp),
    .LsuL1Axi4Plugin_logic_axi_ar_valid        (mem_arvalid),
    .LsuL1Axi4Plugin_logic_axi_ar_ready        (mem_arready),
    .LsuL1Axi4Plugin_logic_axi_ar_payload_addr (mem_araddr),
    .LsuL1Axi4Plugin_logic_axi_ar_payload_id   (mem_arid),
    .LsuL1Axi4Plugin_logic_axi_ar_payload_len  (mem_arlen),
    .LsuL1Axi4Plugin_logic_axi_ar_payload_size (mem_arsize),
    .LsuL1Axi4Plugin_logic_axi_ar_payload_burst(mem_arburst),
    .LsuL1Axi4Plugin_logic_axi_ar_payload_cache(),
    .LsuL1Axi4Plugin_logic_axi_ar_payload_prot (),
    .LsuL1Axi4Plugin_logic_axi_r_valid         (mem_rvalid),
    .LsuL1Axi4Plugin_logic_axi_r_ready         (mem_rready),
    .LsuL1Axi4Plugin_logic_axi_r_payload_data  (mem_rdata),
    .LsuL1Axi4Plugin_logic_axi_r_payload_id    (mem_rid),
    .LsuL1Axi4Plugin_logic_axi_r_payload_resp  (mem_rresp),
    .LsuL1Axi4Plugin_logic_axi_r_payload_last  (mem_rlast),

    // LsuPlugin native IO bus (goes through the shim above to per_* AXI)
    .LsuPlugin_logic_bus_cmd_valid           (lsu_cmd_valid),
    .LsuPlugin_logic_bus_cmd_ready           (lsu_cmd_ready),
    .LsuPlugin_logic_bus_cmd_payload_write   (lsu_cmd_write),
    .LsuPlugin_logic_bus_cmd_payload_address (lsu_cmd_addr),
    .LsuPlugin_logic_bus_cmd_payload_data    (lsu_cmd_data),
    .LsuPlugin_logic_bus_cmd_payload_size    (lsu_cmd_size),
    .LsuPlugin_logic_bus_cmd_payload_mask    (lsu_cmd_mask),
    .LsuPlugin_logic_bus_cmd_payload_io      (),
    .LsuPlugin_logic_bus_cmd_payload_fromHart(),
    .LsuPlugin_logic_bus_cmd_payload_uopId   (),
    .LsuPlugin_logic_bus_rsp_valid           (lsu_rsp_valid),
    .LsuPlugin_logic_bus_rsp_payload_error   (lsu_rsp_error),
    .LsuPlugin_logic_bus_rsp_payload_data    (lsu_rsp_data)
);

// ============================================================
// Address decode → target select
// ============================================================
// Target IDs (3 total).
//   0 — SDRAM   (0x10xxxxxx cached, 0x50xxxxxx uncached alias)
//   1 — CRAM0   (0x30xxxxxx, 0x38xxxxxx alias)
//   2 — LOCAL   (BRAM, peripherals, term FB, etc.)
function [2:0] decode_target;
    input [31:0] addr;
    begin
        if (addr[31:26] == 6'b000100 || addr[31:26] == 6'b010100)
            decode_target = 3'd0;   // SDRAM
        else if (addr[31:24] == 8'h30 || addr[31:24] == 8'h38)
            decode_target = 3'd1;   // CRAM0
        else
            decode_target = 3'd2;   // LOCAL
    end
endfunction

wire [2:0] i_ar_target   = decode_target(i_araddr);
wire [2:0] mem_ar_target = decode_target(mem_araddr);
wire [2:0] mem_aw_target = decode_target(mem_awaddr);
wire [2:0] per_ar_target = decode_target(per_araddr);
wire [2:0] per_aw_target = decode_target(per_awaddr);

// Per-(master, target) busy signals (see cpu_target_port.v)
wire sdram_i_rd_busy, sdram_mem_rd_busy, sdram_mem_wr_busy, sdram_per_rd_busy, sdram_per_wr_busy;
wire cram0_i_rd_busy, cram0_mem_rd_busy, cram0_mem_wr_busy, cram0_per_rd_busy, cram0_per_wr_busy;
wire local_i_rd_busy, local_mem_rd_busy, local_mem_wr_busy, local_per_rd_busy, local_per_wr_busy;

// Per-(master, direction) global serialization — keeps responses in-order
// per master even though they may come back from different target ports.
wire global_i_rd_busy   = sdram_i_rd_busy   | cram0_i_rd_busy   | local_i_rd_busy;
wire global_mem_rd_busy = sdram_mem_rd_busy | cram0_mem_rd_busy | local_mem_rd_busy;
wire global_mem_wr_busy = sdram_mem_wr_busy | cram0_mem_wr_busy | local_mem_wr_busy;
wire global_per_rd_busy = sdram_per_rd_busy | cram0_per_rd_busy | local_per_rd_busy;
wire global_per_wr_busy = sdram_per_wr_busy | cram0_per_wr_busy | local_per_wr_busy;

wire i_ar_is_sdram = i_arvalid && (i_ar_target == 3'd0) && !global_i_rd_busy;
wire i_ar_is_cram0 = i_arvalid && (i_ar_target == 3'd1) && !global_i_rd_busy;
wire i_ar_is_local = i_arvalid && (i_ar_target == 3'd2) && !global_i_rd_busy;

wire mem_ar_is_sdram = mem_arvalid && (mem_ar_target == 3'd0) && !global_mem_rd_busy;
wire mem_ar_is_cram0 = mem_arvalid && (mem_ar_target == 3'd1) && !global_mem_rd_busy;
wire mem_ar_is_local = mem_arvalid && (mem_ar_target == 3'd2) && !global_mem_rd_busy;
wire mem_aw_is_sdram = mem_awvalid && (mem_aw_target == 3'd0) && !global_mem_wr_busy;
wire mem_aw_is_cram0 = mem_awvalid && (mem_aw_target == 3'd1) && !global_mem_wr_busy;
wire mem_aw_is_local = mem_awvalid && (mem_aw_target == 3'd2) && !global_mem_wr_busy;

wire per_ar_is_sdram = per_arvalid && (per_ar_target == 3'd0) && !global_per_rd_busy;
wire per_ar_is_cram0 = per_arvalid && (per_ar_target == 3'd1) && !global_per_rd_busy;
wire per_ar_is_local = per_arvalid && (per_ar_target == 3'd2) && !global_per_rd_busy;
wire per_aw_is_sdram = per_awvalid && (per_aw_target == 3'd0) && !global_per_wr_busy;
wire per_aw_is_cram0 = per_awvalid && (per_aw_target == 3'd1) && !global_per_wr_busy;
wire per_aw_is_local = per_awvalid && (per_aw_target == 3'd2) && !global_per_wr_busy;

// ============================================================
// Per-target contribution wires
// ============================================================
// SDRAM
wire        sdram_i_arready,   sdram_mem_arready,   sdram_per_arready;
wire        sdram_i_rvalid;    wire [31:0] sdram_i_rdata;   wire [0:0] sdram_i_rid;   wire [1:0] sdram_i_rresp;   wire sdram_i_rlast;
wire        sdram_mem_rvalid;  wire [31:0] sdram_mem_rdata; wire [1:0] sdram_mem_rid; wire [1:0] sdram_mem_rresp; wire sdram_mem_rlast;
wire        sdram_per_rvalid;  wire [31:0] sdram_per_rdata; wire [1:0] sdram_per_rresp; wire sdram_per_rlast;
wire        sdram_mem_awready, sdram_per_awready;
wire        sdram_mem_wready,  sdram_per_wready;
wire        sdram_mem_bvalid;  wire [1:0]  sdram_mem_bid;   wire [1:0] sdram_mem_bresp;
wire        sdram_per_bvalid;  wire [1:0]  sdram_per_bresp;

// CRAM0
wire        cram0_i_arready,   cram0_mem_arready,   cram0_per_arready;
wire        cram0_i_rvalid;    wire [31:0] cram0_i_rdata;   wire [0:0] cram0_i_rid;   wire [1:0] cram0_i_rresp;   wire cram0_i_rlast;
wire        cram0_mem_rvalid;  wire [31:0] cram0_mem_rdata; wire [1:0] cram0_mem_rid; wire [1:0] cram0_mem_rresp; wire cram0_mem_rlast;
wire        cram0_per_rvalid;  wire [31:0] cram0_per_rdata; wire [1:0] cram0_per_rresp; wire cram0_per_rlast;
wire        cram0_mem_awready, cram0_per_awready;
wire        cram0_mem_wready,  cram0_per_wready;
wire        cram0_mem_bvalid;  wire [1:0]  cram0_mem_bid;   wire [1:0] cram0_mem_bresp;
wire        cram0_per_bvalid;  wire [1:0]  cram0_per_bresp;

// LOCAL
wire        local_i_arready,   local_mem_arready,   local_per_arready;
wire        local_i_rvalid;    wire [31:0] local_i_rdata;   wire [0:0] local_i_rid;   wire [1:0] local_i_rresp;   wire local_i_rlast;
wire        local_mem_rvalid;  wire [31:0] local_mem_rdata; wire [1:0] local_mem_rid; wire [1:0] local_mem_rresp; wire local_mem_rlast;
wire        local_per_rvalid;  wire [31:0] local_per_rdata; wire [1:0] local_per_rresp; wire local_per_rlast;
wire        local_mem_awready, local_per_awready;
wire        local_mem_wready,  local_per_wready;
wire        local_mem_bvalid;  wire [1:0]  local_mem_bid;   wire [1:0] local_mem_bresp;
wire        local_per_bvalid;  wire [1:0]  local_per_bresp;

// ============================================================
// SDRAM target port
// ============================================================
cpu_target_port port_sdram (
    .clk    (clk),
    .reset_n(reset_n),

    .i_rd_select  (i_ar_is_sdram),
    .mem_rd_select(mem_ar_is_sdram),
    .per_rd_select(per_ar_is_sdram),
    .mem_wr_select(mem_aw_is_sdram),
    .per_wr_select(per_aw_is_sdram),

    .i_arvalid         (i_arvalid),
    .i_arready_contrib (sdram_i_arready),
    .i_araddr          (i_araddr),
    .i_arid            (i_arid),
    .i_arlen           (i_arlen),
    .i_rvalid_contrib  (sdram_i_rvalid),
    .i_rdata_contrib   (sdram_i_rdata),
    .i_rid_contrib     (sdram_i_rid),
    .i_rresp_contrib   (sdram_i_rresp),
    .i_rlast_contrib   (sdram_i_rlast),
    .i_rready          (i_rready),

    .mem_arvalid         (mem_arvalid),
    .mem_arready_contrib (sdram_mem_arready),
    .mem_araddr          (mem_araddr),
    .mem_arid            (mem_arid),
    .mem_arlen           (mem_arlen),
    .mem_rvalid_contrib  (sdram_mem_rvalid),
    .mem_rdata_contrib   (sdram_mem_rdata),
    .mem_rid_contrib     (sdram_mem_rid),
    .mem_rresp_contrib   (sdram_mem_rresp),
    .mem_rlast_contrib   (sdram_mem_rlast),
    .mem_rready          (mem_rready),
    .mem_awvalid         (mem_awvalid),
    .mem_awready_contrib (sdram_mem_awready),
    .mem_awaddr          (mem_awaddr),
    .mem_awid            (mem_awid),
    .mem_awlen            (mem_awlen),
    .mem_wvalid          (mem_wvalid),
    .mem_wready_contrib  (sdram_mem_wready),
    .mem_wdata           (mem_wdata),
    .mem_wstrb           (mem_wstrb),
    .mem_wlast           (mem_wlast),
    .mem_bvalid_contrib  (sdram_mem_bvalid),
    .mem_bid_contrib     (sdram_mem_bid),
    .mem_bresp_contrib   (sdram_mem_bresp),
    .mem_bready          (mem_bready),

    .per_arvalid         (per_arvalid),
    .per_arready_contrib (sdram_per_arready),
    .per_araddr          (per_araddr),
    .per_arlen           (per_arlen),
    .per_rvalid_contrib  (sdram_per_rvalid),
    .per_rdata_contrib   (sdram_per_rdata),
    .per_rresp_contrib   (sdram_per_rresp),
    .per_rlast_contrib   (sdram_per_rlast),
    .per_rready          (per_rready),
    .per_awvalid         (per_awvalid),
    .per_awready_contrib (sdram_per_awready),
    .per_awaddr          (per_awaddr),
    .per_awlen           (per_awlen),
    .per_wvalid          (per_wvalid),
    .per_wready_contrib  (sdram_per_wready),
    .per_wdata           (per_wdata),
    .per_wstrb           (per_wstrb),
    .per_wlast           (per_wlast),
    .per_bvalid_contrib  (sdram_per_bvalid),
    .per_bresp_contrib   (sdram_per_bresp),
    .per_bready          (per_bready),

    .m_arvalid (m_sdram_arvalid),
    .m_arready (m_sdram_arready),
    .m_araddr  (m_sdram_araddr),
    .m_arlen   (m_sdram_arlen),
    .m_rvalid  (m_sdram_rvalid),
    .m_rready  (m_sdram_rready),
    .m_rdata   (m_sdram_rdata),
    .m_rresp   (m_sdram_rresp),
    .m_rlast   (m_sdram_rlast),
    .m_awvalid (m_sdram_awvalid),
    .m_awready (m_sdram_awready),
    .m_awaddr  (m_sdram_awaddr),
    .m_awlen   (m_sdram_awlen),
    .m_wvalid  (m_sdram_wvalid),
    .m_wready  (m_sdram_wready),
    .m_wdata   (m_sdram_wdata),
    .m_wstrb   (m_sdram_wstrb),
    .m_wlast   (m_sdram_wlast),
    .m_bvalid  (m_sdram_bvalid),
    .m_bresp   (m_sdram_bresp),

    .i_rd_busy  (sdram_i_rd_busy),
    .mem_rd_busy(sdram_mem_rd_busy),
    .mem_wr_busy(sdram_mem_wr_busy),
    .per_rd_busy(sdram_per_rd_busy),
    .per_wr_busy(sdram_per_wr_busy)
);

// ============================================================
// CRAM0 target port
// ============================================================
cpu_target_port port_cram0 (
    .clk    (clk),
    .reset_n(reset_n),

    .i_rd_select  (i_ar_is_cram0),
    .mem_rd_select(mem_ar_is_cram0),
    .per_rd_select(per_ar_is_cram0),
    .mem_wr_select(mem_aw_is_cram0),
    .per_wr_select(per_aw_is_cram0),

    .i_arvalid         (i_arvalid),
    .i_arready_contrib (cram0_i_arready),
    .i_araddr          (i_araddr),
    .i_arid            (i_arid),
    .i_arlen           (i_arlen),
    .i_rvalid_contrib  (cram0_i_rvalid),
    .i_rdata_contrib   (cram0_i_rdata),
    .i_rid_contrib     (cram0_i_rid),
    .i_rresp_contrib   (cram0_i_rresp),
    .i_rlast_contrib   (cram0_i_rlast),
    .i_rready          (i_rready),

    .mem_arvalid         (mem_arvalid),
    .mem_arready_contrib (cram0_mem_arready),
    .mem_araddr          (mem_araddr),
    .mem_arid            (mem_arid),
    .mem_arlen           (mem_arlen),
    .mem_rvalid_contrib  (cram0_mem_rvalid),
    .mem_rdata_contrib   (cram0_mem_rdata),
    .mem_rid_contrib     (cram0_mem_rid),
    .mem_rresp_contrib   (cram0_mem_rresp),
    .mem_rlast_contrib   (cram0_mem_rlast),
    .mem_rready          (mem_rready),
    .mem_awvalid         (mem_awvalid),
    .mem_awready_contrib (cram0_mem_awready),
    .mem_awaddr          (mem_awaddr),
    .mem_awid            (mem_awid),
    .mem_awlen           (mem_awlen),
    .mem_wvalid          (mem_wvalid),
    .mem_wready_contrib  (cram0_mem_wready),
    .mem_wdata           (mem_wdata),
    .mem_wstrb           (mem_wstrb),
    .mem_wlast           (mem_wlast),
    .mem_bvalid_contrib  (cram0_mem_bvalid),
    .mem_bid_contrib     (cram0_mem_bid),
    .mem_bresp_contrib   (cram0_mem_bresp),
    .mem_bready          (mem_bready),

    .per_arvalid         (per_arvalid),
    .per_arready_contrib (cram0_per_arready),
    .per_araddr          (per_araddr),
    .per_arlen           (per_arlen),
    .per_rvalid_contrib  (cram0_per_rvalid),
    .per_rdata_contrib   (cram0_per_rdata),
    .per_rresp_contrib   (cram0_per_rresp),
    .per_rlast_contrib   (cram0_per_rlast),
    .per_rready          (per_rready),
    .per_awvalid         (per_awvalid),
    .per_awready_contrib (cram0_per_awready),
    .per_awaddr          (per_awaddr),
    .per_awlen           (per_awlen),
    .per_wvalid          (per_wvalid),
    .per_wready_contrib  (cram0_per_wready),
    .per_wdata           (per_wdata),
    .per_wstrb           (per_wstrb),
    .per_wlast           (per_wlast),
    .per_bvalid_contrib  (cram0_per_bvalid),
    .per_bresp_contrib   (cram0_per_bresp),
    .per_bready          (per_bready),

    .m_arvalid (m_cram0_arvalid),
    .m_arready (m_cram0_arready),
    .m_araddr  (m_cram0_araddr),
    .m_arlen   (m_cram0_arlen),
    .m_rvalid  (m_cram0_rvalid),
    .m_rready  (m_cram0_rready),
    .m_rdata   (m_cram0_rdata),
    .m_rresp   (m_cram0_rresp),
    .m_rlast   (m_cram0_rlast),
    .m_awvalid (m_cram0_awvalid),
    .m_awready (m_cram0_awready),
    .m_awaddr  (m_cram0_awaddr),
    .m_awlen   (m_cram0_awlen),
    .m_wvalid  (m_cram0_wvalid),
    .m_wready  (m_cram0_wready),
    .m_wdata   (m_cram0_wdata),
    .m_wstrb   (m_cram0_wstrb),
    .m_wlast   (m_cram0_wlast),
    .m_bvalid  (m_cram0_bvalid),
    .m_bresp   (m_cram0_bresp),

    .i_rd_busy  (cram0_i_rd_busy),
    .mem_rd_busy(cram0_mem_rd_busy),
    .mem_wr_busy(cram0_mem_wr_busy),
    .per_rd_busy(cram0_per_rd_busy),
    .per_wr_busy(cram0_per_wr_busy)
);

// ============================================================
// LOCAL target port
// ============================================================
cpu_target_port port_local (
    .clk    (clk),
    .reset_n(reset_n),

    .i_rd_select  (i_ar_is_local),
    .mem_rd_select(mem_ar_is_local),
    .per_rd_select(per_ar_is_local),
    .mem_wr_select(mem_aw_is_local),
    .per_wr_select(per_aw_is_local),

    .i_arvalid         (i_arvalid),
    .i_arready_contrib (local_i_arready),
    .i_araddr          (i_araddr),
    .i_arid            (i_arid),
    .i_arlen           (i_arlen),
    .i_rvalid_contrib  (local_i_rvalid),
    .i_rdata_contrib   (local_i_rdata),
    .i_rid_contrib     (local_i_rid),
    .i_rresp_contrib   (local_i_rresp),
    .i_rlast_contrib   (local_i_rlast),
    .i_rready          (i_rready),

    .mem_arvalid         (mem_arvalid),
    .mem_arready_contrib (local_mem_arready),
    .mem_araddr          (mem_araddr),
    .mem_arid            (mem_arid),
    .mem_arlen           (mem_arlen),
    .mem_rvalid_contrib  (local_mem_rvalid),
    .mem_rdata_contrib   (local_mem_rdata),
    .mem_rid_contrib     (local_mem_rid),
    .mem_rresp_contrib   (local_mem_rresp),
    .mem_rlast_contrib   (local_mem_rlast),
    .mem_rready          (mem_rready),
    .mem_awvalid         (mem_awvalid),
    .mem_awready_contrib (local_mem_awready),
    .mem_awaddr          (mem_awaddr),
    .mem_awid            (mem_awid),
    .mem_awlen           (mem_awlen),
    .mem_wvalid          (mem_wvalid),
    .mem_wready_contrib  (local_mem_wready),
    .mem_wdata           (mem_wdata),
    .mem_wstrb           (mem_wstrb),
    .mem_wlast           (mem_wlast),
    .mem_bvalid_contrib  (local_mem_bvalid),
    .mem_bid_contrib     (local_mem_bid),
    .mem_bresp_contrib   (local_mem_bresp),
    .mem_bready          (mem_bready),

    .per_arvalid         (per_arvalid),
    .per_arready_contrib (local_per_arready),
    .per_araddr          (per_araddr),
    .per_arlen           (per_arlen),
    .per_rvalid_contrib  (local_per_rvalid),
    .per_rdata_contrib   (local_per_rdata),
    .per_rresp_contrib   (local_per_rresp),
    .per_rlast_contrib   (local_per_rlast),
    .per_rready          (per_rready),
    .per_awvalid         (per_awvalid),
    .per_awready_contrib (local_per_awready),
    .per_awaddr          (per_awaddr),
    .per_awlen           (per_awlen),
    .per_wvalid          (per_wvalid),
    .per_wready_contrib  (local_per_wready),
    .per_wdata           (per_wdata),
    .per_wstrb           (per_wstrb),
    .per_wlast           (per_wlast),
    .per_bvalid_contrib  (local_per_bvalid),
    .per_bresp_contrib   (local_per_bresp),
    .per_bready          (per_bready),

    .m_arvalid (m_local_arvalid),
    .m_arready (m_local_arready),
    .m_araddr  (m_local_araddr),
    .m_arlen   (m_local_arlen),
    .m_rvalid  (m_local_rvalid),
    .m_rready  (m_local_rready),
    .m_rdata   (m_local_rdata),
    .m_rresp   (m_local_rresp),
    .m_rlast   (m_local_rlast),
    .m_awvalid (m_local_awvalid),
    .m_awready (m_local_awready),
    .m_awaddr  (m_local_awaddr),
    .m_awlen   (m_local_awlen),
    .m_wvalid  (m_local_wvalid),
    .m_wready  (m_local_wready),
    .m_wdata   (m_local_wdata),
    .m_wstrb   (m_local_wstrb),
    .m_wlast   (m_local_wlast),
    .m_bvalid  (m_local_bvalid),
    .m_bresp   (m_local_bresp),

    .i_rd_busy  (local_i_rd_busy),
    .mem_rd_busy(local_mem_rd_busy),
    .mem_wr_busy(local_mem_wr_busy),
    .per_rd_busy(local_per_rd_busy),
    .per_wr_busy(local_per_wr_busy)
);

// ============================================================
// Master-facing aggregations (OR across target ports)
// ============================================================
// At most one target drives any given signal high because the *_select
// inputs are mutually exclusive per master (address decode picks exactly
// one target).

assign i_arready   = sdram_i_arready   | cram0_i_arready   | local_i_arready;
assign mem_arready = sdram_mem_arready | cram0_mem_arready | local_mem_arready;
assign per_arready = sdram_per_arready | cram0_per_arready | local_per_arready;
assign mem_awready = sdram_mem_awready | cram0_mem_awready | local_mem_awready;
assign per_awready = sdram_per_awready | cram0_per_awready | local_per_awready;
assign mem_wready  = sdram_mem_wready  | cram0_mem_wready  | local_mem_wready;
assign per_wready  = sdram_per_wready  | cram0_per_wready  | local_per_wready;

// i R channel
assign i_rvalid = sdram_i_rvalid | cram0_i_rvalid | local_i_rvalid;
assign i_rdata  = sdram_i_rvalid ? sdram_i_rdata
                : cram0_i_rvalid ? cram0_i_rdata
                : local_i_rdata;
assign i_rid    = sdram_i_rvalid ? sdram_i_rid
                : cram0_i_rvalid ? cram0_i_rid
                : local_i_rid;
assign i_rresp  = sdram_i_rvalid ? sdram_i_rresp
                : cram0_i_rvalid ? cram0_i_rresp
                : local_i_rresp;
assign i_rlast  = sdram_i_rvalid ? sdram_i_rlast
                : cram0_i_rvalid ? cram0_i_rlast
                : local_i_rlast;

// mem R channel
assign mem_rvalid = sdram_mem_rvalid | cram0_mem_rvalid | local_mem_rvalid;
assign mem_rdata  = sdram_mem_rvalid ? sdram_mem_rdata
                  : cram0_mem_rvalid ? cram0_mem_rdata
                  : local_mem_rdata;
assign mem_rid    = sdram_mem_rvalid ? sdram_mem_rid
                  : cram0_mem_rvalid ? cram0_mem_rid
                  : local_mem_rid;
assign mem_rresp  = sdram_mem_rvalid ? sdram_mem_rresp
                  : cram0_mem_rvalid ? cram0_mem_rresp
                  : local_mem_rresp;
assign mem_rlast  = sdram_mem_rvalid ? sdram_mem_rlast
                  : cram0_mem_rvalid ? cram0_mem_rlast
                  : local_mem_rlast;

// per R channel
assign per_rvalid = sdram_per_rvalid | cram0_per_rvalid | local_per_rvalid;
assign per_rdata  = sdram_per_rvalid ? sdram_per_rdata
                  : cram0_per_rvalid ? cram0_per_rdata
                  : local_per_rdata;
assign per_rresp  = sdram_per_rvalid ? sdram_per_rresp
                  : cram0_per_rvalid ? cram0_per_rresp
                  : local_per_rresp;
assign per_rlast  = sdram_per_rvalid ? sdram_per_rlast
                  : cram0_per_rvalid ? cram0_per_rlast
                  : local_per_rlast;

// mem B channel
assign mem_bvalid = sdram_mem_bvalid | cram0_mem_bvalid | local_mem_bvalid;
assign mem_bid    = sdram_mem_bvalid ? sdram_mem_bid
                  : cram0_mem_bvalid ? cram0_mem_bid
                  : local_mem_bid;
assign mem_bresp  = sdram_mem_bvalid ? sdram_mem_bresp
                  : cram0_mem_bvalid ? cram0_mem_bresp
                  : local_mem_bresp;

// per B channel
assign per_bvalid = sdram_per_bvalid | cram0_per_bvalid | local_per_bvalid;
assign per_bresp  = sdram_per_bvalid ? sdram_per_bresp
                  : cram0_per_bvalid ? cram0_per_bresp
                  : local_per_bresp;

endmodule
