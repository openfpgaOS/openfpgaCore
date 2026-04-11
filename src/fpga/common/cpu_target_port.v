//
// cpu_target_port — single-target AXI4 port with concurrent read/write sub-FSMs.
//
// Instantiated once per downstream target (SDRAM / PSRAM / LOCAL) by
// cpu_system.v.  Owns the AXI4 master interface to one slave and
// independently arbitrates read and write traffic from the two CPU
// masters (mem_axi, p_axi).
//
// Design points:
//   - Read and write sub-FSMs are independent.  A read can be in flight
//     to this target while a write is also in flight (AXI spec allows
//     concurrent channels).
//   - Within each sub-FSM, mem_axi has priority over p_axi via a
//     round-robin tie-break (last_grant_mem flag).  Same behaviour as
//     the legacy serialized cpu_system.
//   - When a master's AR/AW does NOT target this port (address decode
//     mismatch), the *_select inputs are low and the sub-FSM ignores
//     that master's request entirely.  The top level OR's contributions
//     from all three target ports together, so only one port ever drives
//     a given master's *ready pulse.
//   - W-channel race: do NOT capture a new W beat from the CPU while a
//     previously captured beat is still sitting in the m_wvalid register
//     waiting for m_wready.  Same fix as the legacy FSM.
//
// Port select:
//   mem_rd_select / per_rd_select are combinational signals from
//   cpu_system.v: high when the corresponding master's arvalid is high
//   AND the address decodes to this target.  We use them as a gate on
//   the FSM's acceptance logic.  Same for mem_wr_select / per_wr_select.
//

`default_nettype none

module cpu_target_port (
    input wire clk,
    input wire reset_n,

    // ============================================================
    // Selection signals (combinational from cpu_system top)
    // ============================================================
    // High when the corresponding master's incoming transaction targets
    // this port (address decoded).  Filter for the sub-FSM accept logic.
    input wire mem_rd_select,
    input wire per_rd_select,
    input wire mem_wr_select,
    input wire per_wr_select,

    // ============================================================
    // CPU mem_axi master (passthroughs; only consumed when selected)
    // ============================================================
    input  wire        mem_arvalid,
    output wire        mem_arready_contrib,
    input  wire [31:0] mem_araddr,
    input  wire [1:0]  mem_arid,
    input  wire [7:0]  mem_arlen,

    output wire        mem_rvalid_contrib,
    output wire [31:0] mem_rdata_contrib,
    output wire [1:0]  mem_rid_contrib,
    output wire [1:0]  mem_rresp_contrib,
    output wire        mem_rlast_contrib,
    input  wire        mem_rready,

    input  wire        mem_awvalid,
    output wire        mem_awready_contrib,
    input  wire [31:0] mem_awaddr,
    input  wire [1:0]  mem_awid,
    input  wire [7:0]  mem_awlen,

    input  wire        mem_wvalid,
    output wire        mem_wready_contrib,
    input  wire [31:0] mem_wdata,
    input  wire [3:0]  mem_wstrb,
    input  wire        mem_wlast,

    output wire        mem_bvalid_contrib,
    output wire [1:0]  mem_bid_contrib,
    output wire [1:0]  mem_bresp_contrib,
    input  wire        mem_bready,

    // ============================================================
    // CPU p_axi master (no AXI ID)
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

    output reg         m_wvalid,
    input  wire        m_wready,
    output reg  [31:0] m_wdata,
    output reg  [3:0]  m_wstrb,
    output reg         m_wlast,

    input  wire        m_bvalid,
    input  wire [1:0]  m_bresp,

    // ============================================================
    // Global busy outputs — used by cpu_system.v to cross-serialize
    // (master, direction) so responses for a given master return in
    // order even though each port has its own FSM.  AXI4 allows
    // out-of-order responses only for DIFFERENT IDs; VexiiRiscv's
    // fetchL1/lsuL1 reuse a small ID space so we play it safe by
    // serializing per (master, direction) across all target ports.
    // ============================================================
    output wire        mem_rd_busy,
    output wire        mem_wr_busy,
    output wire        per_rd_busy,
    output wire        per_wr_busy
);

wire reset = ~reset_n;

// ============================================================
// Read sub-FSM
// ============================================================
// States:
//   RD_IDLE → RD_AR  (waiting for m_arready)
//   RD_AR   → RD_R   (arready handshake complete; waiting for beats)
//   RD_R    → RD_IDLE (last beat received)
//
localparam [1:0] RD_IDLE = 2'd0;
localparam [1:0] RD_AR   = 2'd1;
localparam [1:0] RD_R    = 2'd2;

reg [1:0] rd_state;
reg       rd_active_is_mem;   // 1 = mem owns this read, 0 = per
reg [1:0] rd_active_id;       // mem's AXI ID (unused for per)
reg [7:0] rd_burst_len;
reg [7:0] rd_burst_count;
reg       last_grant_rd_mem;

// Registered response signals (match the legacy cpu_system semantics:
// slaves pulse m_rvalid for 1 cycle, we latch the beat and hold it until
// the master asserts mem_rready / per_rready).
reg        mem_rvalid_r;
reg [31:0] mem_rdata_r;
reg [1:0]  mem_rid_r;
reg [1:0]  mem_rresp_r;
reg        mem_rlast_r;
reg        per_rvalid_r;
reg [31:0] per_rdata_r;
reg [1:0]  per_rresp_r;
reg        per_rlast_r;

// Accept new read when idle.  Within a port, the R and W sub-FSMs are
// mutually exclusive — the underlying slave (axi_sdram_slave /
// axi_cram1_slave) is a single FSM that can only service one transaction
// at a time, so we can't have both rd and wr in flight on the same port.
wire no_wr_in_flight   = (wr_state == WR_IDLE) && !mem_bvalid_r && !per_bvalid_r;
wire no_rd_in_flight   = (rd_state == RD_IDLE) && !mem_rvalid_r && !per_rvalid_r;
wire rd_can_start      = (rd_state == RD_IDLE) && no_wr_in_flight;
wire rd_grant_mem      = rd_can_start && mem_rd_select &&
                         (!per_rd_select || !last_grant_rd_mem);
wire rd_grant_per      = rd_can_start && per_rd_select && !rd_grant_mem;

// Single-cycle arready pulses back to the master — combinational from
// the grant condition, deasserted the cycle after the handshake.
wire rd_mem_arready_pulse = rd_grant_mem;
wire rd_per_arready_pulse = rd_grant_per;

wire rd_beat_is_last = (rd_burst_count == rd_burst_len);

always @(posedge clk or posedge reset) begin
    if (reset) begin
        rd_state         <= RD_IDLE;
        rd_active_is_mem <= 1'b0;
        rd_active_id     <= 2'b0;
        rd_burst_len     <= 8'b0;
        rd_burst_count   <= 8'b0;
        last_grant_rd_mem<= 1'b0;
        m_arvalid        <= 1'b0;
        m_araddr         <= 32'b0;
        m_arlen          <= 8'b0;
        mem_rvalid_r     <= 1'b0;
        mem_rdata_r      <= 32'b0;
        mem_rid_r        <= 2'b0;
        mem_rresp_r      <= 2'b0;
        mem_rlast_r      <= 1'b0;
        per_rvalid_r     <= 1'b0;
        per_rdata_r      <= 32'b0;
        per_rresp_r      <= 2'b0;
        per_rlast_r      <= 1'b0;
    end else begin
        // Default: deassert registered response when master accepts it
        if (mem_rready) mem_rvalid_r <= 1'b0;
        if (per_rready) per_rvalid_r <= 1'b0;

        case (rd_state)
        RD_IDLE: begin
            m_arvalid <= 1'b0;
            if (rd_grant_mem) begin
                rd_active_is_mem <= 1'b1;
                rd_active_id     <= mem_arid;
                rd_burst_len     <= mem_arlen;
                rd_burst_count   <= 8'b0;
                m_arvalid        <= 1'b1;
                m_araddr         <= mem_araddr;
                m_arlen          <= mem_arlen;
                last_grant_rd_mem<= 1'b1;
                rd_state         <= RD_AR;
            end else if (rd_grant_per) begin
                rd_active_is_mem <= 1'b0;
                rd_active_id     <= 2'b0;
                rd_burst_len     <= per_arlen;
                rd_burst_count   <= 8'b0;
                m_arvalid        <= 1'b1;
                m_araddr         <= per_araddr;
                m_arlen          <= per_arlen;
                last_grant_rd_mem<= 1'b0;
                rd_state         <= RD_AR;
            end
        end

        RD_AR: begin
            if (m_arready) begin
                m_arvalid <= 1'b0;
                rd_state  <= RD_R;
            end
        end

        RD_R: begin
            // Only accept a new beat when our per-master registered
            // response slot is actually free.  If it's still holding
            // data the master hasn't consumed yet, leave m_rready low
            // (via the combinational assignment below) so the slave
            // holds its rvalid until we catch up — no beats are dropped.
            if (m_rvalid && m_rready) begin
                if (rd_active_is_mem) begin
                    mem_rvalid_r <= 1'b1;
                    mem_rdata_r  <= m_rdata;
                    mem_rid_r    <= rd_active_id;
                    mem_rresp_r  <= m_rresp;
                    mem_rlast_r  <= rd_beat_is_last;
                end else begin
                    per_rvalid_r <= 1'b1;
                    per_rdata_r  <= m_rdata;
                    per_rresp_r  <= m_rresp;
                    per_rlast_r  <= rd_beat_is_last;
                end
                rd_burst_count <= rd_burst_count + 8'd1;
                if (rd_beat_is_last)
                    rd_state <= RD_IDLE;
            end
        end

        default: rd_state <= RD_IDLE;
        endcase
    end
end

// ============================================================
// Write sub-FSM
// ============================================================
// States:
//   WR_IDLE → WR_AW  (waiting for m_awready)
//   WR_AW   → WR_W   (awready handshake complete; forwarding W beats)
//   WR_W    → WR_B   (last beat sent; waiting for bvalid)
//   WR_B    → WR_IDLE (bvalid handshake)
//
localparam [1:0] WR_IDLE = 2'd0;
localparam [1:0] WR_AW   = 2'd1;
localparam [1:0] WR_W    = 2'd2;
localparam [1:0] WR_B    = 2'd3;

reg [1:0] wr_state;
reg       wr_active_is_mem;
reg [1:0] wr_active_id;
reg [7:0] wr_burst_len;
reg [7:0] wr_burst_count;
reg       last_grant_wr_mem;

wire wr_can_start = (wr_state == WR_IDLE) && no_rd_in_flight;
wire wr_grant_mem = wr_can_start && mem_wr_select &&
                    (!per_wr_select || !last_grant_wr_mem);
wire wr_grant_per = wr_can_start && per_wr_select && !wr_grant_mem;

wire wr_mem_awready_pulse = wr_grant_mem;
wire wr_per_awready_pulse = wr_grant_per;

// W beat capture: only while in WR_W, only when an empty slot is
// available (no slave-side wvalid still pending acceptance).
reg wr_mem_wready_pulse;
reg wr_per_wready_pulse;

// Registered B-channel responses (same sticky semantics as R channel)
reg       mem_bvalid_r;
reg [1:0] mem_bid_r;
reg [1:0] mem_bresp_r;
reg       per_bvalid_r;
reg [1:0] per_bresp_r;

wire wr_beat_is_last = (wr_burst_count == wr_burst_len);

always @(posedge clk or posedge reset) begin
    if (reset) begin
        wr_state         <= WR_IDLE;
        wr_active_is_mem <= 1'b0;
        wr_active_id     <= 2'b0;
        wr_burst_len     <= 8'b0;
        wr_burst_count   <= 8'b0;
        last_grant_wr_mem<= 1'b0;
        m_awvalid        <= 1'b0;
        m_awaddr         <= 32'b0;
        m_awlen          <= 8'b0;
        m_wvalid         <= 1'b0;
        m_wdata          <= 32'b0;
        m_wstrb          <= 4'b0;
        m_wlast          <= 1'b0;
        wr_mem_wready_pulse <= 1'b0;
        wr_per_wready_pulse <= 1'b0;
        mem_bvalid_r <= 1'b0;
        mem_bid_r    <= 2'b0;
        mem_bresp_r  <= 2'b0;
        per_bvalid_r <= 1'b0;
        per_bresp_r  <= 2'b0;
    end else begin
        // Default: single-cycle pulses clear themselves
        wr_mem_wready_pulse <= 1'b0;
        wr_per_wready_pulse <= 1'b0;
        // Deassert registered B response when master accepts
        if (mem_bready) mem_bvalid_r <= 1'b0;
        if (per_bready) per_bvalid_r <= 1'b0;

        case (wr_state)
        WR_IDLE: begin
            m_awvalid <= 1'b0;
            m_wvalid  <= 1'b0;
            if (wr_grant_mem) begin
                wr_active_is_mem <= 1'b1;
                wr_active_id     <= mem_awid;
                wr_burst_len     <= mem_awlen;
                wr_burst_count   <= 8'b0;
                m_awvalid        <= 1'b1;
                m_awaddr         <= mem_awaddr;
                m_awlen          <= mem_awlen;
                last_grant_wr_mem<= 1'b1;
                wr_state         <= WR_AW;
            end else if (wr_grant_per) begin
                wr_active_is_mem <= 1'b0;
                wr_active_id     <= 2'b0;
                wr_burst_len     <= per_awlen;
                wr_burst_count   <= 8'b0;
                m_awvalid        <= 1'b1;
                m_awaddr         <= per_awaddr;
                m_awlen          <= per_awlen;
                last_grant_wr_mem<= 1'b0;
                wr_state         <= WR_AW;
            end
        end

        WR_AW: begin
            if (m_awready) begin
                m_awvalid <= 1'b0;
                wr_state  <= WR_W;
            end
        end

        WR_W: begin
            // Capture next W beat from whichever master owns us, but
            // ONLY if we don't already have a captured beat sitting
            // in m_wvalid waiting for slave acceptance (W-channel race
            // fix from the legacy serialized FSM).
            if (wr_active_is_mem) begin
                if (mem_wvalid && !wr_mem_wready_pulse && !m_wvalid) begin
                    wr_mem_wready_pulse <= 1'b1;
                    m_wvalid <= 1'b1;
                    m_wdata  <= mem_wdata;
                    m_wstrb  <= mem_wstrb;
                    m_wlast  <= mem_wlast;
                end
            end else begin
                if (per_wvalid && !wr_per_wready_pulse && !m_wvalid) begin
                    wr_per_wready_pulse <= 1'b1;
                    m_wvalid <= 1'b1;
                    m_wdata  <= per_wdata;
                    m_wstrb  <= per_wstrb;
                    m_wlast  <= per_wlast;
                end
            end

            // Slave-side beat handshake
            if (m_wready && m_wvalid) begin
                m_wvalid <= 1'b0;
                wr_burst_count <= wr_burst_count + 8'd1;
                if (wr_beat_is_last)
                    wr_state <= WR_B;
            end
        end

        WR_B: begin
            if (m_bvalid) begin
                if (wr_active_is_mem) begin
                    mem_bvalid_r <= 1'b1;
                    mem_bid_r    <= wr_active_id;
                    mem_bresp_r  <= m_bresp;
                end else begin
                    per_bvalid_r <= 1'b1;
                    per_bresp_r  <= m_bresp;
                end
                wr_state <= WR_IDLE;
            end
        end

        default: wr_state <= WR_IDLE;
        endcase
    end
end

// ============================================================
// Master-facing contribution outputs
// ============================================================
// These are OR'd at the top level across all three target ports.
// At most one port ever drives any given signal high because the
// *_select inputs are mutually exclusive across ports (address decode).

assign mem_arready_contrib = rd_mem_arready_pulse;
assign per_arready_contrib = rd_per_arready_pulse;
assign mem_awready_contrib = wr_mem_awready_pulse;
assign per_awready_contrib = wr_per_awready_pulse;
assign mem_wready_contrib  = wr_mem_wready_pulse;
assign per_wready_contrib  = wr_per_wready_pulse;

// Read response: registered (sticky until mem_rready / per_rready seen).
// The _r values are only non-zero when this port owns the current beat,
// so OR-ing across target ports at the top level is safe.
assign mem_rvalid_contrib = mem_rvalid_r;
assign mem_rdata_contrib  = mem_rvalid_r ? mem_rdata_r : 32'b0;
assign mem_rid_contrib    = mem_rvalid_r ? mem_rid_r   : 2'b0;
assign mem_rresp_contrib  = mem_rvalid_r ? mem_rresp_r : 2'b0;
assign mem_rlast_contrib  = mem_rvalid_r ? mem_rlast_r : 1'b0;

assign per_rvalid_contrib = per_rvalid_r;
assign per_rdata_contrib  = per_rvalid_r ? per_rdata_r : 32'b0;
assign per_rresp_contrib  = per_rvalid_r ? per_rresp_r : 2'b0;
assign per_rlast_contrib  = per_rvalid_r ? per_rlast_r : 1'b0;

// Write response: registered (sticky until mem_bready / per_bready seen).
assign mem_bvalid_contrib = mem_bvalid_r;
assign mem_bid_contrib    = mem_bvalid_r ? mem_bid_r   : 2'b0;
assign mem_bresp_contrib  = mem_bvalid_r ? mem_bresp_r : 2'b0;

assign per_bvalid_contrib = per_bvalid_r;
assign per_bresp_contrib  = per_bvalid_r ? per_bresp_r : 2'b0;

// ============================================================
// Slave-side R channel back-pressure
// ============================================================
// Drive m_rready true when either we're not currently servicing a read
// (RD_IDLE / RD_AR — the slave should never be driving m_rvalid anyway),
// or we're in RD_R and the owning master's registered response slot is
// empty or will be freed this cycle by the master's own ready pulse.
//
// Without this the slave's rvalid pulses during a fast sync burst were
// overwriting mem_rvalid_r while the CPU was still holding the previous
// beat, silently dropping data and wedging the L1 refill FSM.
assign m_rready = (rd_state == RD_R)
                  ? (rd_active_is_mem
                        ? (!mem_rvalid_r || mem_rready)
                        : (!per_rvalid_r || per_rready))
                  : 1'b1;

// ============================================================
// Global busy outputs
// ============================================================
// A master's read is "in flight" on this port if:
//   - the read sub-FSM is past IDLE and owned by that master, OR
//   - the port still holds a pending registered response for that master
//
// Both conditions matter: the response-drain window (mem_rvalid_r still
// sticky) must keep the port "busy" for ordering purposes, otherwise a
// new read on the same master could be granted to a different port
// while the previous response is still being delivered.

assign mem_rd_busy = ((rd_state != RD_IDLE) &&  rd_active_is_mem) || mem_rvalid_r;
assign per_rd_busy = ((rd_state != RD_IDLE) && !rd_active_is_mem) || per_rvalid_r;
assign mem_wr_busy = ((wr_state != WR_IDLE) &&  wr_active_is_mem) || mem_bvalid_r;
assign per_wr_busy = ((wr_state != WR_IDLE) && !wr_active_is_mem) || per_bvalid_r;

endmodule
