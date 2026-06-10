//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

//
// cpu_target_port_per — single-master (per_axi only) AXI4 target port.
//
// PMA-dead-path specialization of cpu_target_port for CRAM0 — this is NOT
// feature gating: every reachable CRAM0 transaction is preserved cycle-for-
// cycle.  The generated VexiiRiscv PMA (src/fpga/vendor/vexriscv/
// generate_vexii.sh, the --region list used by generate_vexii):
//
//   --region base=0,size=8000,main=0,exe=1          (BRAM)
//   --region base=10000000,size=4000000,main=1,exe=1 (SDRAM, cached+exec)
//   --region base=20000000,size=10000000,main=0,exe=0
//   --region base=30000000,size=1000000,main=0,exe=0 (CRAM0)
//   --region base=40000000,size=40000000,main=0,exe=0
//
// marks CRAM0 (0x30000000, 16 MB) main=0, exe=0:
//
//   - exe=0  → the I$ fetch path faults *inside the CPU* before any bus
//     access, so i_axi can never present an AR in 0x30/0x38.  Neither can
//     next-line prefetch slop: the adjacent executable regions (BRAM at
//     0x0-0x8000 and SDRAM at 0x10000000+64MB) do not abut CRAM0.
//   - main=0 → the D$ (LsuL1/mem_axi) never allocates, refills, or writes
//     back lines there, and cbo.* finds no line to operate on.  ALL data
//     accesses to CRAM0 are routed by the LSU down its uncached IO path,
//     which is per_axi.
//
// This matches the "single-master CRAM0" invariant already validated on
// hardware (10/10 testdemo iterations): per_axi is the only master that can
// physically reach this port, so the 3-way read round-robin, the i/mem
// sticky response slots, and the mem branch of the write FSM in the generic
// cpu_target_port synthesize to logic that no generated-CPU access pattern
// can ever exercise.  This module is the same port with those dead paths
// removed; the per_axi read/write/burst behaviour is bit- and cycle-
// identical to cpu_target_port servicing a per-only request stream.
//
// If a future PMA change re-enables cached or executable CRAM0 (main=1 or
// exe=1 on the 0x30000000 region), port_cram0 in cpu_system.v must be
// reverted to the full cpu_target_port.
//
// Note for directed testbenches: a tb that drives i_axi/mem_axi at
// 0x30/0x38 does not model real CPU behaviour (the PMA faults such
// accesses pre-bus) and must target per_axi instead.
//

`default_nettype none

module cpu_target_port_per (
    input wire clk,
    input wire reset_n,

    // High when per_axi's incoming transaction targets this port
    // (address decoded AND global-busy permits), from cpu_system top.
    input wire per_rd_select,
    input wire per_wr_select,

    // ============================================================
    // CPU p_axi master (uncached LSU/IO) — no AXI ID
    // ============================================================
    input  wire        per_arvalid,
    output wire        per_arready_contrib,
    input  wire [31:0] per_araddr,
    input  wire [7:0]  per_arlen,

    output wire        per_rvalid_contrib,
    output wire [31:0] per_rdata_contrib,
    output wire [1:0]  per_rresp_contrib,
    output wire        per_rlast_contrib,
    input  wire        per_rready,

    input  wire        per_awvalid,
    output wire        per_awready_contrib,
    input  wire [31:0] per_awaddr,
    input  wire [7:0]  per_awlen,
    input  wire [1:0]  per_awburst,

    input  wire        per_wvalid,
    output wire        per_wready_contrib,
    input  wire [31:0] per_wdata,
    input  wire [3:0]  per_wstrb,
    input  wire        per_wlast,

    output wire        per_bvalid_contrib,
    output wire [1:0]  per_bresp_contrib,
    input  wire        per_bready,

    // ============================================================
    // Slave AXI4 master interface (to the downstream target)
    // ============================================================
    output reg         m_arvalid,
    input  wire        m_arready,
    output reg  [31:0] m_araddr,
    output reg  [7:0]  m_arlen,

    input  wire        m_rvalid,
    output wire        m_rready,
    input  wire [31:0] m_rdata,
    input  wire [1:0]  m_rresp,
    input  wire        m_rlast,

    output reg         m_awvalid,
    input  wire        m_awready,
    output reg  [31:0] m_awaddr,
    output reg  [7:0]  m_awlen,
    output reg  [1:0]  m_awburst,

    output reg         m_wvalid,
    input  wire        m_wready,
    output reg  [31:0] m_wdata,
    output reg  [3:0]  m_wstrb,
    output reg         m_wlast,

    input  wire        m_bvalid,
    input  wire [1:0]  m_bresp,

    // Global busy outputs — used by cpu_system.v to cross-serialize
    // per_axi transactions across target ports.
    output wire        per_rd_busy,
    output wire        per_wr_busy
);

wire reset = ~reset_n;

// ============================================================
// Read sub-FSM — single master, no arbitration
// ============================================================
localparam [1:0] RD_IDLE = 2'd0;
localparam [1:0] RD_AR   = 2'd1;
localparam [1:0] RD_R    = 2'd2;

reg [1:0] rd_state;
reg [7:0] rd_burst_len;
reg [7:0] rd_burst_count;

// Registered response slot (hold-until-ready).
reg        per_rvalid_r;
reg [31:0] per_rdata_r;
reg [1:0]  per_rresp_r;
reg        per_rlast_r;

// Serialization between rd and wr sub-FSMs on this port: the downstream
// slave is single-FSM so only one direction can be in flight at a time.
// As in cpu_target_port, the registered response slot does NOT gate a new
// start — a pending response is a master-side handshake that doesn't
// occupy the slave, and cpu_system's global_per_*_busy already prevents a
// new per transaction from overwriting the sticky slot.
wire no_wr_in_flight = (wr_state == WR_IDLE);
wire no_rd_in_flight = (rd_state == RD_IDLE);

wire rd_grant = (rd_state == RD_IDLE) && no_wr_in_flight && per_rd_select;

wire rd_beat_is_last = (rd_burst_count == rd_burst_len);

// Zero-gap pre-grant (same shape as cpu_target_port): when the last beat
// of a burst is accepted in RD_R and per is already requesting again,
// skip the RD_IDLE dead cycle and issue the next AR directly.
wire rd_last_accept = (rd_state == RD_R) && m_rvalid && m_rready && rd_beat_is_last;
wire rd_pregrant    = rd_last_accept && no_wr_in_flight && per_rd_select;

always @(posedge clk or posedge reset) begin
    if (reset) begin
        rd_state       <= RD_IDLE;
        rd_burst_len   <= 8'b0;
        rd_burst_count <= 8'b0;
        m_arvalid      <= 1'b0;
        m_araddr       <= 32'b0;
        m_arlen        <= 8'b0;
        per_rvalid_r   <= 1'b0;
        per_rdata_r    <= 32'b0;
        per_rresp_r    <= 2'b0;
        per_rlast_r    <= 1'b0;
    end else begin
        // Clear the registered beat as the master drains it.
        if (per_rready) per_rvalid_r <= 1'b0;

        case (rd_state)
        RD_IDLE: begin
            m_arvalid <= 1'b0;
            if (rd_grant) begin
                rd_burst_len   <= per_arlen;
                rd_burst_count <= 8'b0;
                m_arvalid      <= 1'b1;
                m_araddr       <= per_araddr;
                m_arlen        <= per_arlen;
                rd_state       <= RD_AR;
            end
        end

        RD_AR: begin
            if (m_arready) begin
                m_arvalid <= 1'b0;
                rd_state  <= RD_R;
            end
        end

        RD_R: begin
            // Only accept a beat when the registered slot is empty (or
            // emptying via per_rready this cycle — m_rready below).
            if (m_rvalid && m_rready) begin
                per_rvalid_r <= 1'b1;
                per_rdata_r  <= m_rdata;
                per_rresp_r  <= m_rresp;
                per_rlast_r  <= rd_beat_is_last;
                rd_burst_count <= rd_burst_count + 8'd1;
                if (rd_beat_is_last) begin
                    if (rd_pregrant) begin
                        // Last assignment wins for rd_burst_count/len —
                        // the new burst's values override the increment.
                        rd_burst_len   <= per_arlen;
                        rd_burst_count <= 8'b0;
                        m_arvalid      <= 1'b1;
                        m_araddr       <= per_araddr;
                        m_arlen        <= per_arlen;
                        rd_state       <= RD_AR;
                    end else begin
                        rd_state <= RD_IDLE;
                    end
                end
            end
        end

        default: rd_state <= RD_IDLE;
        endcase
    end
end

// Single-cycle arready pulse — combinational from grant / pre-grant.
wire rd_per_arready_pulse = rd_grant || rd_pregrant;

// ============================================================
// Write sub-FSM — single master (per); i_axi never writes and the
// mem branch is PMA-dead (see header).
// ============================================================
localparam [1:0] WR_IDLE = 2'd0;
localparam [1:0] WR_AW   = 2'd1;
localparam [1:0] WR_W    = 2'd2;
localparam [1:0] WR_B    = 2'd3;

reg [1:0] wr_state;
reg [7:0] wr_burst_len;
reg [7:0] wr_burst_count;

wire wr_grant = (wr_state == WR_IDLE) && no_rd_in_flight && per_wr_select;

wire wr_per_awready_pulse = wr_grant;

reg wr_per_wready_pulse;

reg       per_bvalid_r;
reg [1:0] per_bresp_r;

wire wr_beat_is_last = (wr_burst_count == wr_burst_len);

always @(posedge clk or posedge reset) begin
    if (reset) begin
        wr_state           <= WR_IDLE;
        wr_burst_len       <= 8'b0;
        wr_burst_count     <= 8'b0;
        m_awvalid          <= 1'b0;
        m_awaddr           <= 32'b0;
        m_awlen            <= 8'b0;
        m_awburst          <= 2'b01;
        m_wvalid           <= 1'b0;
        m_wdata            <= 32'b0;
        m_wstrb            <= 4'b0;
        m_wlast            <= 1'b0;
        wr_per_wready_pulse <= 1'b0;
        per_bvalid_r <= 1'b0;
        per_bresp_r  <= 2'b0;
    end else begin
        wr_per_wready_pulse <= 1'b0;
        if (per_bready) per_bvalid_r <= 1'b0;

        case (wr_state)
        // WR_IDLE bundles W into the same cycle as AW when the master
        // already has wvalid up — single-beat MMIO writes skip the
        // WR_AW intermediate state, saving 1 cycle per write.  WR_AW
        // handles the unbundled case (W arrives later).
        WR_IDLE: begin
            m_awvalid <= 1'b0;
            m_wvalid  <= 1'b0;
            if (wr_grant) begin
                wr_burst_len   <= per_awlen;
                wr_burst_count <= 8'b0;
                m_awvalid      <= 1'b1;
                m_awaddr       <= per_awaddr;
                m_awlen        <= per_awlen;
                m_awburst      <= per_awburst;
                wr_state       <= WR_AW;
                if (per_wvalid) begin
                    m_wvalid <= 1'b1;
                    m_wdata  <= per_wdata;
                    m_wstrb  <= per_wstrb;
                    m_wlast  <= per_wlast;
                    wr_per_wready_pulse <= 1'b1;
                end
            end
        end

        // WR_AW dual-purpose: handles AW handshake AND (when bundled
        // from WR_IDLE) the W handshake too.  If m_awready+m_wready
        // both fire here, advance to WR_B directly.
        WR_AW: begin
            if (m_awready) m_awvalid <= 1'b0;

            if (m_wready && m_wvalid) begin
                m_wvalid <= 1'b0;
                wr_burst_count <= wr_burst_count + 8'd1;
                if (wr_beat_is_last) begin
                    if (m_awready || !m_awvalid)
                        wr_state <= WR_B;
                    // else: AW not done yet, stay in WR_AW until awready
                end else begin
                    wr_state <= WR_W;
                end
            end else if (m_awready && !m_wvalid) begin
                wr_state <= WR_W;
            end
        end

        WR_W: begin
            if (per_wvalid && !wr_per_wready_pulse && !m_wvalid) begin
                wr_per_wready_pulse <= 1'b1;
                m_wvalid <= 1'b1;
                m_wdata  <= per_wdata;
                m_wstrb  <= per_wstrb;
                m_wlast  <= per_wlast;
            end

            if (m_wready && m_wvalid) begin
                m_wvalid <= 1'b0;
                wr_burst_count <= wr_burst_count + 8'd1;
                if (wr_beat_is_last)
                    wr_state <= WR_B;
            end
        end

        WR_B: begin
            if (m_bvalid) begin
                per_bvalid_r <= 1'b1;
                per_bresp_r  <= m_bresp;
                wr_state <= WR_IDLE;
            end
        end

        default: wr_state <= WR_IDLE;
        endcase
    end
end

// ============================================================
// Master-facing contribution outputs (OR'd at top level)
// ============================================================
assign per_arready_contrib = rd_per_arready_pulse;
assign per_awready_contrib = wr_per_awready_pulse;
assign per_wready_contrib  = wr_per_wready_pulse;

// Read response (sticky until per_rready)
assign per_rvalid_contrib = per_rvalid_r;
assign per_rdata_contrib  = per_rvalid_r ? per_rdata_r : 32'b0;
assign per_rresp_contrib  = per_rvalid_r ? per_rresp_r : 2'b0;
assign per_rlast_contrib  = per_rvalid_r ? per_rlast_r : 1'b0;

// Write response (sticky until per_bready)
assign per_bvalid_contrib = per_bvalid_r;
assign per_bresp_contrib  = per_bvalid_r ? per_bresp_r : 2'b0;

// ============================================================
// Slave-side R-channel back-pressure
// ============================================================
// Hold m_rready low when the registered slot is full and won't be
// drained this cycle — the slave keeps m_rvalid high, no beat is lost.
assign m_rready = (rd_state == RD_R) ? (!per_rvalid_r || per_rready) : 1'b1;

// ============================================================
// Global busy outputs
// ============================================================
assign per_rd_busy = (rd_state != RD_IDLE) || per_rvalid_r;
assign per_wr_busy = (wr_state != WR_IDLE) || per_bvalid_r;

endmodule
