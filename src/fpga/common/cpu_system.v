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

    // ---- CRAM0 AXI4 master (PSRAM chip #0, app code/data) --------
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

    // ---- CRAM1 AXI4 master (PSRAM chip #1, saves/audio/files) ----
    output wire        m_cram1_arvalid,
    input  wire        m_cram1_arready,
    output wire [31:0] m_cram1_araddr,
    output wire [7:0]  m_cram1_arlen,
    input  wire        m_cram1_rvalid,
    output wire        m_cram1_rready,
    input  wire [31:0] m_cram1_rdata,
    input  wire [1:0]  m_cram1_rresp,
    input  wire        m_cram1_rlast,
    output wire        m_cram1_awvalid,
    input  wire        m_cram1_awready,
    output wire [31:0] m_cram1_awaddr,
    output wire [7:0]  m_cram1_awlen,
    output wire        m_cram1_wvalid,
    input  wire        m_cram1_wready,
    output wire [31:0] m_cram1_wdata,
    output wire [3:0]  m_cram1_wstrb,
    output wire        m_cram1_wlast,
    input  wire        m_cram1_bvalid,
    input  wire [1:0]  m_cram1_bresp,

    // ---- SRAM AXI4 master (on-chip, ~256KB) ----------------------
    output wire        m_sram_arvalid,
    input  wire        m_sram_arready,
    output wire [31:0] m_sram_araddr,
    output wire [7:0]  m_sram_arlen,
    input  wire        m_sram_rvalid,
    output wire        m_sram_rready,
    input  wire [31:0] m_sram_rdata,
    input  wire [1:0]  m_sram_rresp,
    input  wire        m_sram_rlast,
    output wire        m_sram_awvalid,
    input  wire        m_sram_awready,
    output wire [31:0] m_sram_awaddr,
    output wire [7:0]  m_sram_awlen,
    output wire        m_sram_wvalid,
    input  wire        m_sram_wready,
    output wire [31:0] m_sram_wdata,
    output wire [3:0]  m_sram_wstrb,
    output wire        m_sram_wlast,
    input  wire        m_sram_bvalid,
    input  wire [1:0]  m_sram_bresp,

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
// Target IDs (5 total) — expanded from 3 to split PSRAM into its
// three physical backends (CRAM0, CRAM1, SRAM) so each can run in
// parallel.
//   0 — SDRAM   (0x10xxxxxx cached, 0x50xxxxxx uncached alias)
//   1 — CRAM0   (0x30xxxxxx cached, 0x38xxxxxx uncached, PSRAM chip 0)
//   2 — CRAM1   (0x31xxxxxx,       0x39xxxxxx uncached, PSRAM chip 1)
//   3 — SRAM    (0x3Axxxxxx uncached, on-chip 256KB)
//   4 — LOCAL   (0x00xxxxxx BRAM, 0x40/0x4F peripherals, 0x20 term FB)
function [2:0] decode_target;
    input [31:0] addr;
    begin
        if (addr[31:26] == 6'b000100 || addr[31:26] == 6'b010100)
            decode_target = 3'd0;   // SDRAM
        else if (addr[31:24] == 8'h30 || addr[31:24] == 8'h38)
            decode_target = 3'd1;   // CRAM0
        else if (addr[31:24] == 8'h31 || addr[31:24] == 8'h39)
            decode_target = 3'd2;   // CRAM1
        else if (addr[31:24] == 8'h3A)
            decode_target = 3'd3;   // SRAM
        else
            decode_target = 3'd4;   // LOCAL (BRAM, peripherals, etc.)
    end
endfunction

wire [2:0] mem_ar_target = decode_target(mem_araddr);
wire [2:0] mem_aw_target = decode_target(mem_awaddr);
wire [2:0] per_ar_target = decode_target(per_araddr);
wire [2:0] per_aw_target = decode_target(per_awaddr);

// Per-target per-master busy signals (see cpu_target_port.v)
wire sdram_mem_rd_busy, sdram_mem_wr_busy, sdram_per_rd_busy, sdram_per_wr_busy;
wire cram0_mem_rd_busy, cram0_mem_wr_busy, cram0_per_rd_busy, cram0_per_wr_busy;
wire cram1_mem_rd_busy, cram1_mem_wr_busy, cram1_per_rd_busy, cram1_per_wr_busy;
wire sram_mem_rd_busy,  sram_mem_wr_busy,  sram_per_rd_busy,  sram_per_wr_busy;
wire local_mem_rd_busy, local_mem_wr_busy, local_per_rd_busy, local_per_wr_busy;

// Per-(master, direction) serialization — a master can only have ONE
// outstanding read (and one outstanding write) across all target ports,
// so responses for the same ID class always return in the order they
// were issued.  Combined with within-port R/W mutual exclusion in
// cpu_target_port.v, this means at most one transaction is live on any
// given slave while still allowing up to 5 concurrent transactions
// across the target ports (SDRAM / CRAM0 / CRAM1 / SRAM / LOCAL) —
// each served by its own physical backend.
wire global_mem_rd_busy = sdram_mem_rd_busy | cram0_mem_rd_busy | cram1_mem_rd_busy | sram_mem_rd_busy | local_mem_rd_busy;
wire global_mem_wr_busy = sdram_mem_wr_busy | cram0_mem_wr_busy | cram1_mem_wr_busy | sram_mem_wr_busy | local_mem_wr_busy;
wire global_per_rd_busy = sdram_per_rd_busy | cram0_per_rd_busy | cram1_per_rd_busy | sram_per_rd_busy | local_per_rd_busy;
wire global_per_wr_busy = sdram_per_wr_busy | cram0_per_wr_busy | cram1_per_wr_busy | sram_per_wr_busy | local_per_wr_busy;

wire mem_ar_is_sdram = mem_arvalid && (mem_ar_target == 3'd0) && !global_mem_rd_busy;
wire mem_ar_is_cram0 = mem_arvalid && (mem_ar_target == 3'd1) && !global_mem_rd_busy;
wire mem_ar_is_cram1 = mem_arvalid && (mem_ar_target == 3'd2) && !global_mem_rd_busy;
wire mem_ar_is_sram  = mem_arvalid && (mem_ar_target == 3'd3) && !global_mem_rd_busy;
wire mem_ar_is_local = mem_arvalid && (mem_ar_target == 3'd4) && !global_mem_rd_busy;
wire mem_aw_is_sdram = mem_awvalid && (mem_aw_target == 3'd0) && !global_mem_wr_busy;
wire mem_aw_is_cram0 = mem_awvalid && (mem_aw_target == 3'd1) && !global_mem_wr_busy;
wire mem_aw_is_cram1 = mem_awvalid && (mem_aw_target == 3'd2) && !global_mem_wr_busy;
wire mem_aw_is_sram  = mem_awvalid && (mem_aw_target == 3'd3) && !global_mem_wr_busy;
wire mem_aw_is_local = mem_awvalid && (mem_aw_target == 3'd4) && !global_mem_wr_busy;

wire per_ar_is_sdram = per_arvalid && (per_ar_target == 3'd0) && !global_per_rd_busy;
wire per_ar_is_cram0 = per_arvalid && (per_ar_target == 3'd1) && !global_per_rd_busy;
wire per_ar_is_cram1 = per_arvalid && (per_ar_target == 3'd2) && !global_per_rd_busy;
wire per_ar_is_sram  = per_arvalid && (per_ar_target == 3'd3) && !global_per_rd_busy;
wire per_ar_is_local = per_arvalid && (per_ar_target == 3'd4) && !global_per_rd_busy;
wire per_aw_is_sdram = per_awvalid && (per_aw_target == 3'd0) && !global_per_wr_busy;
wire per_aw_is_cram0 = per_awvalid && (per_aw_target == 3'd1) && !global_per_wr_busy;
wire per_aw_is_cram1 = per_awvalid && (per_aw_target == 3'd2) && !global_per_wr_busy;
wire per_aw_is_sram  = per_awvalid && (per_aw_target == 3'd3) && !global_per_wr_busy;
wire per_aw_is_local = per_awvalid && (per_aw_target == 3'd4) && !global_per_wr_busy;

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

// CRAM0 port contributions
wire        cram0_mem_arready, cram0_per_arready;
wire        cram0_mem_rvalid;  wire [31:0] cram0_mem_rdata;
wire [1:0]  cram0_mem_rid;     wire [1:0]  cram0_mem_rresp;
wire        cram0_mem_rlast;
wire        cram0_per_rvalid;  wire [31:0] cram0_per_rdata;
wire [1:0]  cram0_per_rresp;   wire        cram0_per_rlast;
wire        cram0_mem_awready, cram0_per_awready;
wire        cram0_mem_wready,  cram0_per_wready;
wire        cram0_mem_bvalid;  wire [1:0]  cram0_mem_bid;
wire [1:0]  cram0_mem_bresp;
wire        cram0_per_bvalid;  wire [1:0]  cram0_per_bresp;

// CRAM1 port contributions
wire        cram1_mem_arready, cram1_per_arready;
wire        cram1_mem_rvalid;  wire [31:0] cram1_mem_rdata;
wire [1:0]  cram1_mem_rid;     wire [1:0]  cram1_mem_rresp;
wire        cram1_mem_rlast;
wire        cram1_per_rvalid;  wire [31:0] cram1_per_rdata;
wire [1:0]  cram1_per_rresp;   wire        cram1_per_rlast;
wire        cram1_mem_awready, cram1_per_awready;
wire        cram1_mem_wready,  cram1_per_wready;
wire        cram1_mem_bvalid;  wire [1:0]  cram1_mem_bid;
wire [1:0]  cram1_mem_bresp;
wire        cram1_per_bvalid;  wire [1:0]  cram1_per_bresp;

// SRAM port contributions
wire        sram_mem_arready,  sram_per_arready;
wire        sram_mem_rvalid;   wire [31:0] sram_mem_rdata;
wire [1:0]  sram_mem_rid;      wire [1:0]  sram_mem_rresp;
wire        sram_mem_rlast;
wire        sram_per_rvalid;   wire [31:0] sram_per_rdata;
wire [1:0]  sram_per_rresp;    wire        sram_per_rlast;
wire        sram_mem_awready,  sram_per_awready;
wire        sram_mem_wready,   sram_per_wready;
wire        sram_mem_bvalid;   wire [1:0]  sram_mem_bid;
wire [1:0]  sram_mem_bresp;
wire        sram_per_bvalid;   wire [1:0]  sram_per_bresp;

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

    .mem_rd_busy(sdram_mem_rd_busy),
    .mem_wr_busy(sdram_mem_wr_busy),
    .per_rd_busy(sdram_per_rd_busy),
    .per_wr_busy(sdram_per_wr_busy)
);

// ============================================================
// CRAM0 target port (PSRAM chip #0)
// ============================================================
cpu_target_port port_cram0 (
    .clk    (clk),
    .reset_n(reset_n),

    .mem_rd_select(mem_ar_is_cram0),
    .per_rd_select(per_ar_is_cram0),
    .mem_wr_select(mem_aw_is_cram0),
    .per_wr_select(per_aw_is_cram0),

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

    .mem_rd_busy(cram0_mem_rd_busy),
    .mem_wr_busy(cram0_mem_wr_busy),
    .per_rd_busy(cram0_per_rd_busy),
    .per_wr_busy(cram0_per_wr_busy)
);

// ============================================================
// CRAM1 target port (PSRAM chip #1)
// ============================================================
cpu_target_port port_cram1 (
    .clk    (clk),
    .reset_n(reset_n),

    .mem_rd_select(mem_ar_is_cram1),
    .per_rd_select(per_ar_is_cram1),
    .mem_wr_select(mem_aw_is_cram1),
    .per_wr_select(per_aw_is_cram1),

    .mem_arvalid         (mem_arvalid),
    .mem_arready_contrib (cram1_mem_arready),
    .mem_araddr          (mem_araddr),
    .mem_arid            (mem_arid),
    .mem_arlen           (mem_arlen),
    .mem_rvalid_contrib  (cram1_mem_rvalid),
    .mem_rdata_contrib   (cram1_mem_rdata),
    .mem_rid_contrib     (cram1_mem_rid),
    .mem_rresp_contrib   (cram1_mem_rresp),
    .mem_rlast_contrib   (cram1_mem_rlast),
    .mem_rready          (mem_rready),
    .mem_awvalid         (mem_awvalid),
    .mem_awready_contrib (cram1_mem_awready),
    .mem_awaddr          (mem_awaddr),
    .mem_awid            (mem_awid),
    .mem_awlen           (mem_awlen),
    .mem_wvalid          (mem_wvalid),
    .mem_wready_contrib  (cram1_mem_wready),
    .mem_wdata           (mem_wdata),
    .mem_wstrb           (mem_wstrb),
    .mem_wlast           (mem_wlast),
    .mem_bvalid_contrib  (cram1_mem_bvalid),
    .mem_bid_contrib     (cram1_mem_bid),
    .mem_bresp_contrib   (cram1_mem_bresp),
    .mem_bready          (mem_bready),

    .per_arvalid         (per_arvalid),
    .per_arready_contrib (cram1_per_arready),
    .per_araddr          (per_araddr),
    .per_arlen           (per_arlen),
    .per_rvalid_contrib  (cram1_per_rvalid),
    .per_rdata_contrib   (cram1_per_rdata),
    .per_rresp_contrib   (cram1_per_rresp),
    .per_rlast_contrib   (cram1_per_rlast),
    .per_rready          (per_rready),
    .per_awvalid         (per_awvalid),
    .per_awready_contrib (cram1_per_awready),
    .per_awaddr          (per_awaddr),
    .per_awlen           (per_awlen),
    .per_wvalid          (per_wvalid),
    .per_wready_contrib  (cram1_per_wready),
    .per_wdata           (per_wdata),
    .per_wstrb           (per_wstrb),
    .per_wlast           (per_wlast),
    .per_bvalid_contrib  (cram1_per_bvalid),
    .per_bresp_contrib   (cram1_per_bresp),
    .per_bready          (per_bready),

    .m_arvalid (m_cram1_arvalid),
    .m_arready (m_cram1_arready),
    .m_araddr  (m_cram1_araddr),
    .m_arlen   (m_cram1_arlen),
    .m_rvalid  (m_cram1_rvalid),
    .m_rready  (m_cram1_rready),
    .m_rdata   (m_cram1_rdata),
    .m_rresp   (m_cram1_rresp),
    .m_rlast   (m_cram1_rlast),
    .m_awvalid (m_cram1_awvalid),
    .m_awready (m_cram1_awready),
    .m_awaddr  (m_cram1_awaddr),
    .m_awlen   (m_cram1_awlen),
    .m_wvalid  (m_cram1_wvalid),
    .m_wready  (m_cram1_wready),
    .m_wdata   (m_cram1_wdata),
    .m_wstrb   (m_cram1_wstrb),
    .m_wlast   (m_cram1_wlast),
    .m_bvalid  (m_cram1_bvalid),
    .m_bresp   (m_cram1_bresp),

    .mem_rd_busy(cram1_mem_rd_busy),
    .mem_wr_busy(cram1_mem_wr_busy),
    .per_rd_busy(cram1_per_rd_busy),
    .per_wr_busy(cram1_per_wr_busy)
);

// ============================================================
// SRAM target port (on-chip M10K-backed SRAM, 256 KB)
// ============================================================
cpu_target_port port_sram (
    .clk    (clk),
    .reset_n(reset_n),

    .mem_rd_select(mem_ar_is_sram),
    .per_rd_select(per_ar_is_sram),
    .mem_wr_select(mem_aw_is_sram),
    .per_wr_select(per_aw_is_sram),

    .mem_arvalid         (mem_arvalid),
    .mem_arready_contrib (sram_mem_arready),
    .mem_araddr          (mem_araddr),
    .mem_arid            (mem_arid),
    .mem_arlen           (mem_arlen),
    .mem_rvalid_contrib  (sram_mem_rvalid),
    .mem_rdata_contrib   (sram_mem_rdata),
    .mem_rid_contrib     (sram_mem_rid),
    .mem_rresp_contrib   (sram_mem_rresp),
    .mem_rlast_contrib   (sram_mem_rlast),
    .mem_rready          (mem_rready),
    .mem_awvalid         (mem_awvalid),
    .mem_awready_contrib (sram_mem_awready),
    .mem_awaddr          (mem_awaddr),
    .mem_awid            (mem_awid),
    .mem_awlen           (mem_awlen),
    .mem_wvalid          (mem_wvalid),
    .mem_wready_contrib  (sram_mem_wready),
    .mem_wdata           (mem_wdata),
    .mem_wstrb           (mem_wstrb),
    .mem_wlast           (mem_wlast),
    .mem_bvalid_contrib  (sram_mem_bvalid),
    .mem_bid_contrib     (sram_mem_bid),
    .mem_bresp_contrib   (sram_mem_bresp),
    .mem_bready          (mem_bready),

    .per_arvalid         (per_arvalid),
    .per_arready_contrib (sram_per_arready),
    .per_araddr          (per_araddr),
    .per_arlen           (per_arlen),
    .per_rvalid_contrib  (sram_per_rvalid),
    .per_rdata_contrib   (sram_per_rdata),
    .per_rresp_contrib   (sram_per_rresp),
    .per_rlast_contrib   (sram_per_rlast),
    .per_rready          (per_rready),
    .per_awvalid         (per_awvalid),
    .per_awready_contrib (sram_per_awready),
    .per_awaddr          (per_awaddr),
    .per_awlen           (per_awlen),
    .per_wvalid          (per_wvalid),
    .per_wready_contrib  (sram_per_wready),
    .per_wdata           (per_wdata),
    .per_wstrb           (per_wstrb),
    .per_wlast           (per_wlast),
    .per_bvalid_contrib  (sram_per_bvalid),
    .per_bresp_contrib   (sram_per_bresp),
    .per_bready          (per_bready),

    .m_arvalid (m_sram_arvalid),
    .m_arready (m_sram_arready),
    .m_araddr  (m_sram_araddr),
    .m_arlen   (m_sram_arlen),
    .m_rvalid  (m_sram_rvalid),
    .m_rready  (m_sram_rready),
    .m_rdata   (m_sram_rdata),
    .m_rresp   (m_sram_rresp),
    .m_rlast   (m_sram_rlast),
    .m_awvalid (m_sram_awvalid),
    .m_awready (m_sram_awready),
    .m_awaddr  (m_sram_awaddr),
    .m_awlen   (m_sram_awlen),
    .m_wvalid  (m_sram_wvalid),
    .m_wready  (m_sram_wready),
    .m_wdata   (m_sram_wdata),
    .m_wstrb   (m_sram_wstrb),
    .m_wlast   (m_sram_wlast),
    .m_bvalid  (m_sram_bvalid),
    .m_bresp   (m_sram_bresp),

    .mem_rd_busy(sram_mem_rd_busy),
    .mem_wr_busy(sram_mem_wr_busy),
    .per_rd_busy(sram_per_rd_busy),
    .per_wr_busy(sram_per_wr_busy)
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

assign mem_arready = sdram_mem_arready | cram0_mem_arready | cram1_mem_arready | sram_mem_arready | local_mem_arready;
assign per_arready = sdram_per_arready | cram0_per_arready | cram1_per_arready | sram_per_arready | local_per_arready;
assign mem_awready = sdram_mem_awready | cram0_mem_awready | cram1_mem_awready | sram_mem_awready | local_mem_awready;
assign per_awready = sdram_per_awready | cram0_per_awready | cram1_per_awready | sram_per_awready | local_per_awready;
assign mem_wready  = sdram_mem_wready  | cram0_mem_wready  | cram1_mem_wready  | sram_mem_wready  | local_mem_wready;
assign per_wready  = sdram_per_wready  | cram0_per_wready  | cram1_per_wready  | sram_per_wready  | local_per_wready;

// mem R channel: mux rdata/rid/rresp/rlast based on which port drives valid
assign mem_rvalid = sdram_mem_rvalid | cram0_mem_rvalid | cram1_mem_rvalid | sram_mem_rvalid | local_mem_rvalid;
assign mem_rdata  = sdram_mem_rvalid ? sdram_mem_rdata
                  : cram0_mem_rvalid ? cram0_mem_rdata
                  : cram1_mem_rvalid ? cram1_mem_rdata
                  : sram_mem_rvalid  ? sram_mem_rdata
                  : local_mem_rdata;
assign mem_rid    = sdram_mem_rvalid ? sdram_mem_rid
                  : cram0_mem_rvalid ? cram0_mem_rid
                  : cram1_mem_rvalid ? cram1_mem_rid
                  : sram_mem_rvalid  ? sram_mem_rid
                  : local_mem_rid;
assign mem_rresp  = sdram_mem_rvalid ? sdram_mem_rresp
                  : cram0_mem_rvalid ? cram0_mem_rresp
                  : cram1_mem_rvalid ? cram1_mem_rresp
                  : sram_mem_rvalid  ? sram_mem_rresp
                  : local_mem_rresp;
assign mem_rlast  = sdram_mem_rvalid ? sdram_mem_rlast
                  : cram0_mem_rvalid ? cram0_mem_rlast
                  : cram1_mem_rvalid ? cram1_mem_rlast
                  : sram_mem_rvalid  ? sram_mem_rlast
                  : local_mem_rlast;

// per R channel
assign per_rvalid = sdram_per_rvalid | cram0_per_rvalid | cram1_per_rvalid | sram_per_rvalid | local_per_rvalid;
assign per_rdata  = sdram_per_rvalid ? sdram_per_rdata
                  : cram0_per_rvalid ? cram0_per_rdata
                  : cram1_per_rvalid ? cram1_per_rdata
                  : sram_per_rvalid  ? sram_per_rdata
                  : local_per_rdata;
assign per_rresp  = sdram_per_rvalid ? sdram_per_rresp
                  : cram0_per_rvalid ? cram0_per_rresp
                  : cram1_per_rvalid ? cram1_per_rresp
                  : sram_per_rvalid  ? sram_per_rresp
                  : local_per_rresp;
assign per_rlast  = sdram_per_rvalid ? sdram_per_rlast
                  : cram0_per_rvalid ? cram0_per_rlast
                  : cram1_per_rvalid ? cram1_per_rlast
                  : sram_per_rvalid  ? sram_per_rlast
                  : local_per_rlast;

// mem B channel
assign mem_bvalid = sdram_mem_bvalid | cram0_mem_bvalid | cram1_mem_bvalid | sram_mem_bvalid | local_mem_bvalid;
assign mem_bid    = sdram_mem_bvalid ? sdram_mem_bid
                  : cram0_mem_bvalid ? cram0_mem_bid
                  : cram1_mem_bvalid ? cram1_mem_bid
                  : sram_mem_bvalid  ? sram_mem_bid
                  : local_mem_bid;
assign mem_bresp  = sdram_mem_bvalid ? sdram_mem_bresp
                  : cram0_mem_bvalid ? cram0_mem_bresp
                  : cram1_mem_bvalid ? cram1_mem_bresp
                  : sram_mem_bvalid  ? sram_mem_bresp
                  : local_mem_bresp;

// per B channel
assign per_bvalid = sdram_per_bvalid | cram0_per_bvalid | cram1_per_bvalid | sram_per_bvalid | local_per_bvalid;
assign per_bresp  = sdram_per_bvalid ? sdram_per_bresp
                  : cram0_per_bvalid ? cram0_per_bresp
                  : cram1_per_bvalid ? cram1_per_bresp
                  : sram_per_bvalid  ? sram_per_bresp
                  : local_per_bresp;

endmodule
