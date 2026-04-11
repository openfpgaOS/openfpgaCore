//
// VexiiRiscv CPU System — target-parallel AXI4 router (Unlock 1).
//
// Two CPU AXI4 master inputs (mem_axi cached, p_axi uncached) fan out
// to three downstream slaves (SDRAM / PSRAM / LOCAL).  Each target is
// handled by an independent `cpu_target_port` instance that has its
// own read and write sub-FSMs, so up to SIX transactions can be in
// flight simultaneously (one read + one write per target).
//
// Concretely, the following can all run in parallel:
//   - mem_axi read  → SDRAM   }
//   - mem_axi write → PSRAM   }  6 independent sub-FSMs
//   - p_axi  read   → LOCAL   }
//   - mem_axi write → SDRAM   }  (same master, same target — blocked
//                             }   on per-target read FSM contention)
//
// Per-target arbitration: mem_axi has priority over p_axi with a
// round-robin tie-break (last_grant_*_mem flag inside cpu_target_port).
// Same as the legacy serialized FSM's behaviour.
//
// Address decode → target:
//    0x10000000–0x13FFFFFF, 0x50000000–0x53FFFFFF → SDRAM
//    0x30000000–0x3FFFFFFF                        → PSRAM (routed to
//                                                   axi_psram_slave's
//                                                   internal per-target
//                                                   sub-slaves)
//    everything else                              → LOCAL
//
// Master-facing aggregation: each *_contrib signal from the three
// target ports is OR'd together.  Since a master's arvalid/awvalid is
// routed to at most one target at a time (address decode is a 3-way
// mux), only one target port ever drives a given master's ready or
// response signal high.
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

    // ---- PSRAM AXI4 master ---------------------------------------
    output wire        m_psram_arvalid,
    input  wire        m_psram_arready,
    output wire [31:0] m_psram_araddr,
    output wire [7:0]  m_psram_arlen,

    input  wire        m_psram_rvalid,
    input  wire [31:0] m_psram_rdata,
    input  wire [1:0]  m_psram_rresp,
    input  wire        m_psram_rlast,

    output wire        m_psram_awvalid,
    input  wire        m_psram_awready,
    output wire [31:0] m_psram_awaddr,
    output wire [7:0]  m_psram_awlen,

    output wire        m_psram_wvalid,
    input  wire        m_psram_wready,
    output wire [31:0] m_psram_wdata,
    output wire [3:0]  m_psram_wstrb,
    output wire        m_psram_wlast,

    input  wire        m_psram_bvalid,
    input  wire [1:0]  m_psram_bresp,

    // ---- Local peripheral AXI4 master ----------------------------
    output wire        m_local_arvalid,
    input  wire        m_local_arready,
    output wire [31:0] m_local_araddr,
    output wire [7:0]  m_local_arlen,

    input  wire        m_local_rvalid,
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
// CPU AXI4 master ports (from OpenFpgaVexii top)
// mem_axi : cached path, has 2-bit id (lsuL1 source bit + 1)
// p_axi   : uncached LSU IO, no id
// ============================================================

// mem_axi
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

// p_axi
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

OpenFpgaVexii cpu (
    .clk(clk),
    .reset(reset),

    .int_m_timer   (int_m_timer),
    .int_m_software(1'b0),
    .int_m_external(int_m_external),

    .mem_axi_awvalid  (mem_awvalid),
    .mem_axi_awready  (mem_awready),
    .mem_axi_awaddr   (mem_awaddr),
    .mem_axi_awid     (mem_awid),
    .mem_axi_awlen    (mem_awlen),
    .mem_axi_awsize   (mem_awsize),
    .mem_axi_awburst  (mem_awburst),
    .mem_axi_awallStrb(mem_awallStrb),
    .mem_axi_wvalid   (mem_wvalid),
    .mem_axi_wready   (mem_wready),
    .mem_axi_wdata    (mem_wdata),
    .mem_axi_wstrb    (mem_wstrb),
    .mem_axi_wlast    (mem_wlast),
    .mem_axi_bvalid   (mem_bvalid),
    .mem_axi_bready   (mem_bready),
    .mem_axi_bid      (mem_bid),
    .mem_axi_bresp    (mem_bresp),
    .mem_axi_arvalid  (mem_arvalid),
    .mem_axi_arready  (mem_arready),
    .mem_axi_araddr   (mem_araddr),
    .mem_axi_arid     (mem_arid),
    .mem_axi_arlen    (mem_arlen),
    .mem_axi_arsize   (mem_arsize),
    .mem_axi_arburst  (mem_arburst),
    .mem_axi_rvalid   (mem_rvalid),
    .mem_axi_rready   (mem_rready),
    .mem_axi_rdata    (mem_rdata),
    .mem_axi_rid      (mem_rid),
    .mem_axi_rresp    (mem_rresp),
    .mem_axi_rlast    (mem_rlast),

    .p_axi_awvalid    (per_awvalid),
    .p_axi_awready    (per_awready),
    .p_axi_awaddr     (per_awaddr),
    .p_axi_awlen      (per_awlen),
    .p_axi_awsize     (per_awsize),
    .p_axi_awburst    (per_awburst),
    .p_axi_awallStrb  (per_awallStrb),
    .p_axi_wvalid     (per_wvalid),
    .p_axi_wready     (per_wready),
    .p_axi_wdata      (per_wdata),
    .p_axi_wstrb      (per_wstrb),
    .p_axi_wlast      (per_wlast),
    .p_axi_bvalid     (per_bvalid),
    .p_axi_bready     (per_bready),
    .p_axi_bresp      (per_bresp),
    .p_axi_arvalid    (per_arvalid),
    .p_axi_arready    (per_arready),
    .p_axi_araddr     (per_araddr),
    .p_axi_arlen      (per_arlen),
    .p_axi_arsize     (per_arsize),
    .p_axi_arburst    (per_arburst),
    .p_axi_rvalid     (per_rvalid),
    .p_axi_rready     (per_rready),
    .p_axi_rdata      (per_rdata),
    .p_axi_rresp      (per_rresp),
    .p_axi_rlast      (per_rlast)
);

// ============================================================
// Address decode → target select
// ============================================================
function [1:0] decode_target;
    input [31:0] addr;
    begin
        if (addr[31:26] == 6'b000100 || addr[31:26] == 6'b010100)
            decode_target = 2'd0;   // SDRAM
        else if (addr[31:27] == 5'b00110 || addr[31:27] == 5'b00111)
            decode_target = 2'd1;   // PSRAM
        else
            decode_target = 2'd2;   // LOCAL
    end
endfunction

wire [1:0] mem_ar_target = decode_target(mem_araddr);
wire [1:0] mem_aw_target = decode_target(mem_awaddr);
wire [1:0] per_ar_target = decode_target(per_araddr);
wire [1:0] per_aw_target = decode_target(per_awaddr);

// Per-target per-master busy signals (see cpu_target_port.v)
wire sdram_mem_rd_busy, sdram_mem_wr_busy, sdram_per_rd_busy, sdram_per_wr_busy;
wire psram_mem_rd_busy, psram_mem_wr_busy, psram_per_rd_busy, psram_per_wr_busy;
wire local_mem_rd_busy, local_mem_wr_busy, local_per_rd_busy, local_per_wr_busy;

// Per-(master, direction) serialization — a master can only have ONE
// outstanding read (and one outstanding write) across all target ports,
// so responses for the same ID class always return in the order they
// were issued.  Combined with within-port R/W mutual exclusion in
// cpu_target_port.v, this means at most one transaction is live on any
// given slave while still allowing up to 3 concurrent transactions across
// the three target ports (SDRAM / PSRAM / LOCAL).
wire global_mem_rd_busy = sdram_mem_rd_busy | psram_mem_rd_busy | local_mem_rd_busy;
wire global_mem_wr_busy = sdram_mem_wr_busy | psram_mem_wr_busy | local_mem_wr_busy;
wire global_per_rd_busy = sdram_per_rd_busy | psram_per_rd_busy | local_per_rd_busy;
wire global_per_wr_busy = sdram_per_wr_busy | psram_per_wr_busy | local_per_wr_busy;

wire mem_ar_is_sdram = mem_arvalid && (mem_ar_target == 2'd0) && !global_mem_rd_busy;
wire mem_ar_is_psram = mem_arvalid && (mem_ar_target == 2'd1) && !global_mem_rd_busy;
wire mem_ar_is_local = mem_arvalid && (mem_ar_target == 2'd2) && !global_mem_rd_busy;
wire mem_aw_is_sdram = mem_awvalid && (mem_aw_target == 2'd0) && !global_mem_wr_busy;
wire mem_aw_is_psram = mem_awvalid && (mem_aw_target == 2'd1) && !global_mem_wr_busy;
wire mem_aw_is_local = mem_awvalid && (mem_aw_target == 2'd2) && !global_mem_wr_busy;

wire per_ar_is_sdram = per_arvalid && (per_ar_target == 2'd0) && !global_per_rd_busy;
wire per_ar_is_psram = per_arvalid && (per_ar_target == 2'd1) && !global_per_rd_busy;
wire per_ar_is_local = per_arvalid && (per_ar_target == 2'd2) && !global_per_rd_busy;
wire per_aw_is_sdram = per_awvalid && (per_aw_target == 2'd0) && !global_per_wr_busy;
wire per_aw_is_psram = per_awvalid && (per_aw_target == 2'd1) && !global_per_wr_busy;
wire per_aw_is_local = per_awvalid && (per_aw_target == 2'd2) && !global_per_wr_busy;

// ============================================================
// Per-target contribution wires
// ============================================================
// Each target port drives these; at most one is high per master per
// signal because the selection signals are mutually exclusive.

// SDRAM port contributions
wire        sdram_mem_arready, sdram_per_arready;
wire        sdram_mem_rvalid;  wire [31:0] sdram_mem_rdata;
wire [1:0]  sdram_mem_rid;     wire [1:0]  sdram_mem_rresp;
wire        sdram_mem_rlast;
wire        sdram_per_rvalid;  wire [31:0] sdram_per_rdata;
wire [1:0]  sdram_per_rresp;   wire        sdram_per_rlast;
wire        sdram_mem_awready, sdram_per_awready;
wire        sdram_mem_wready,  sdram_per_wready;
wire        sdram_mem_bvalid;  wire [1:0]  sdram_mem_bid;
wire [1:0]  sdram_mem_bresp;
wire        sdram_per_bvalid;  wire [1:0]  sdram_per_bresp;

// PSRAM port contributions
wire        psram_mem_arready, psram_per_arready;
wire        psram_mem_rvalid;  wire [31:0] psram_mem_rdata;
wire [1:0]  psram_mem_rid;     wire [1:0]  psram_mem_rresp;
wire        psram_mem_rlast;
wire        psram_per_rvalid;  wire [31:0] psram_per_rdata;
wire [1:0]  psram_per_rresp;   wire        psram_per_rlast;
wire        psram_mem_awready, psram_per_awready;
wire        psram_mem_wready,  psram_per_wready;
wire        psram_mem_bvalid;  wire [1:0]  psram_mem_bid;
wire [1:0]  psram_mem_bresp;
wire        psram_per_bvalid;  wire [1:0]  psram_per_bresp;

// LOCAL port contributions
wire        local_mem_arready, local_per_arready;
wire        local_mem_rvalid;  wire [31:0] local_mem_rdata;
wire [1:0]  local_mem_rid;     wire [1:0]  local_mem_rresp;
wire        local_mem_rlast;
wire        local_per_rvalid;  wire [31:0] local_per_rdata;
wire [1:0]  local_per_rresp;   wire        local_per_rlast;
wire        local_mem_awready, local_per_awready;
wire        local_mem_wready,  local_per_wready;
wire        local_mem_bvalid;  wire [1:0]  local_mem_bid;
wire [1:0]  local_mem_bresp;
wire        local_per_bvalid;  wire [1:0]  local_per_bresp;

// ============================================================
// SDRAM target port
// ============================================================
cpu_target_port port_sdram (
    .clk    (clk),
    .reset_n(reset_n),

    .mem_rd_select(mem_ar_is_sdram),
    .per_rd_select(per_ar_is_sdram),
    .mem_wr_select(mem_aw_is_sdram),
    .per_wr_select(per_aw_is_sdram),

    // mem side
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
    .mem_awlen           (mem_awlen),
    .mem_wvalid          (mem_wvalid),
    .mem_wready_contrib  (sdram_mem_wready),
    .mem_wdata           (mem_wdata),
    .mem_wstrb           (mem_wstrb),
    .mem_wlast           (mem_wlast),
    .mem_bvalid_contrib  (sdram_mem_bvalid),
    .mem_bid_contrib     (sdram_mem_bid),
    .mem_bresp_contrib   (sdram_mem_bresp),
    .mem_bready          (mem_bready),

    // per side
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

    // slave (SDRAM)
    .m_arvalid (m_sdram_arvalid),
    .m_arready (m_sdram_arready),
    .m_araddr  (m_sdram_araddr),
    .m_arlen   (m_sdram_arlen),
    .m_rvalid  (m_sdram_rvalid),
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

    .mem_rd_busy(sdram_mem_rd_busy),
    .mem_wr_busy(sdram_mem_wr_busy),
    .per_rd_busy(sdram_per_rd_busy),
    .per_wr_busy(sdram_per_wr_busy)
);

// ============================================================
// PSRAM target port
// ============================================================
cpu_target_port port_psram (
    .clk    (clk),
    .reset_n(reset_n),

    .mem_rd_select(mem_ar_is_psram),
    .per_rd_select(per_ar_is_psram),
    .mem_wr_select(mem_aw_is_psram),
    .per_wr_select(per_aw_is_psram),

    .mem_arvalid         (mem_arvalid),
    .mem_arready_contrib (psram_mem_arready),
    .mem_araddr          (mem_araddr),
    .mem_arid            (mem_arid),
    .mem_arlen           (mem_arlen),
    .mem_rvalid_contrib  (psram_mem_rvalid),
    .mem_rdata_contrib   (psram_mem_rdata),
    .mem_rid_contrib     (psram_mem_rid),
    .mem_rresp_contrib   (psram_mem_rresp),
    .mem_rlast_contrib   (psram_mem_rlast),
    .mem_rready          (mem_rready),
    .mem_awvalid         (mem_awvalid),
    .mem_awready_contrib (psram_mem_awready),
    .mem_awaddr          (mem_awaddr),
    .mem_awid            (mem_awid),
    .mem_awlen           (mem_awlen),
    .mem_wvalid          (mem_wvalid),
    .mem_wready_contrib  (psram_mem_wready),
    .mem_wdata           (mem_wdata),
    .mem_wstrb           (mem_wstrb),
    .mem_wlast           (mem_wlast),
    .mem_bvalid_contrib  (psram_mem_bvalid),
    .mem_bid_contrib     (psram_mem_bid),
    .mem_bresp_contrib   (psram_mem_bresp),
    .mem_bready          (mem_bready),

    .per_arvalid         (per_arvalid),
    .per_arready_contrib (psram_per_arready),
    .per_araddr          (per_araddr),
    .per_arlen           (per_arlen),
    .per_rvalid_contrib  (psram_per_rvalid),
    .per_rdata_contrib   (psram_per_rdata),
    .per_rresp_contrib   (psram_per_rresp),
    .per_rlast_contrib   (psram_per_rlast),
    .per_rready          (per_rready),
    .per_awvalid         (per_awvalid),
    .per_awready_contrib (psram_per_awready),
    .per_awaddr          (per_awaddr),
    .per_awlen           (per_awlen),
    .per_wvalid          (per_wvalid),
    .per_wready_contrib  (psram_per_wready),
    .per_wdata           (per_wdata),
    .per_wstrb           (per_wstrb),
    .per_wlast           (per_wlast),
    .per_bvalid_contrib  (psram_per_bvalid),
    .per_bresp_contrib   (psram_per_bresp),
    .per_bready          (per_bready),

    .m_arvalid (m_psram_arvalid),
    .m_arready (m_psram_arready),
    .m_araddr  (m_psram_araddr),
    .m_arlen   (m_psram_arlen),
    .m_rvalid  (m_psram_rvalid),
    .m_rdata   (m_psram_rdata),
    .m_rresp   (m_psram_rresp),
    .m_rlast   (m_psram_rlast),
    .m_awvalid (m_psram_awvalid),
    .m_awready (m_psram_awready),
    .m_awaddr  (m_psram_awaddr),
    .m_awlen   (m_psram_awlen),
    .m_wvalid  (m_psram_wvalid),
    .m_wready  (m_psram_wready),
    .m_wdata   (m_psram_wdata),
    .m_wstrb   (m_psram_wstrb),
    .m_wlast   (m_psram_wlast),
    .m_bvalid  (m_psram_bvalid),
    .m_bresp   (m_psram_bresp),

    .mem_rd_busy(psram_mem_rd_busy),
    .mem_wr_busy(psram_mem_wr_busy),
    .per_rd_busy(psram_per_rd_busy),
    .per_wr_busy(psram_per_wr_busy)
);

// ============================================================
// LOCAL target port
// ============================================================
cpu_target_port port_local (
    .clk    (clk),
    .reset_n(reset_n),

    .mem_rd_select(mem_ar_is_local),
    .per_rd_select(per_ar_is_local),
    .mem_wr_select(mem_aw_is_local),
    .per_wr_select(per_aw_is_local),

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

    .mem_rd_busy(local_mem_rd_busy),
    .mem_wr_busy(local_mem_wr_busy),
    .per_rd_busy(local_per_rd_busy),
    .per_wr_busy(local_per_wr_busy)
);

// ============================================================
// Master-facing aggregations (OR across target ports)
// ============================================================
// At most one target drives any given signal high because the *_select
// inputs are mutually exclusive per master (address decode picks one
// target).

assign mem_arready = sdram_mem_arready | psram_mem_arready | local_mem_arready;
assign per_arready = sdram_per_arready | psram_per_arready | local_per_arready;
assign mem_awready = sdram_mem_awready | psram_mem_awready | local_mem_awready;
assign per_awready = sdram_per_awready | psram_per_awready | local_per_awready;
assign mem_wready  = sdram_mem_wready  | psram_mem_wready  | local_mem_wready;
assign per_wready  = sdram_per_wready  | psram_per_wready  | local_per_wready;

// mem R channel: mux rdata/rid/rresp/rlast based on which port drives valid
assign mem_rvalid = sdram_mem_rvalid | psram_mem_rvalid | local_mem_rvalid;
assign mem_rdata  = sdram_mem_rvalid ? sdram_mem_rdata
                  : psram_mem_rvalid ? psram_mem_rdata
                  : local_mem_rdata;
assign mem_rid    = sdram_mem_rvalid ? sdram_mem_rid
                  : psram_mem_rvalid ? psram_mem_rid
                  : local_mem_rid;
assign mem_rresp  = sdram_mem_rvalid ? sdram_mem_rresp
                  : psram_mem_rvalid ? psram_mem_rresp
                  : local_mem_rresp;
assign mem_rlast  = sdram_mem_rvalid ? sdram_mem_rlast
                  : psram_mem_rvalid ? psram_mem_rlast
                  : local_mem_rlast;

// per R channel
assign per_rvalid = sdram_per_rvalid | psram_per_rvalid | local_per_rvalid;
assign per_rdata  = sdram_per_rvalid ? sdram_per_rdata
                  : psram_per_rvalid ? psram_per_rdata
                  : local_per_rdata;
assign per_rresp  = sdram_per_rvalid ? sdram_per_rresp
                  : psram_per_rvalid ? psram_per_rresp
                  : local_per_rresp;
assign per_rlast  = sdram_per_rvalid ? sdram_per_rlast
                  : psram_per_rvalid ? psram_per_rlast
                  : local_per_rlast;

// mem B channel
assign mem_bvalid = sdram_mem_bvalid | psram_mem_bvalid | local_mem_bvalid;
assign mem_bid    = sdram_mem_bvalid ? sdram_mem_bid
                  : psram_mem_bvalid ? psram_mem_bid
                  : local_mem_bid;
assign mem_bresp  = sdram_mem_bvalid ? sdram_mem_bresp
                  : psram_mem_bvalid ? psram_mem_bresp
                  : local_mem_bresp;

// per B channel
assign per_bvalid = sdram_per_bvalid | psram_per_bvalid | local_per_bvalid;
assign per_bresp  = sdram_per_bvalid ? sdram_per_bresp
                  : psram_per_bvalid ? psram_per_bresp
                  : local_per_bresp;

endmodule
