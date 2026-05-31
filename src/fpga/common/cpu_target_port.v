//
// cpu_target_port — single-target AXI4 port with concurrent read/write sub-FSMs.
//
// Instantiated once per downstream target (SDRAM / CRAM0 / CRAM1 /
// SRAM / LOCAL) by cpu_system.v.  Owns the AXI4 master interface to
// one slave and independently arbitrates traffic from THREE CPU
// masters:
//
//   - i_axi  : L1 I$ refill bus (read-only, no AW/W/B)
//   - mem_axi: L1 D$ refill + writeback + cbo.* bus (full R/W)
//   - per_axi: uncached LSU IO bus (full R/W)
//
// Design points:
//   - Read and write sub-FSMs are independent.  A read can be in flight
//     to this target while a write is also in flight — BUT the underlying
//     slave is a single FSM that can only service one transaction at a
//     time, so within the port we still hold R/W mutually exclusive.
//   - Read arbitration is 3-way (i / mem / per) with a last-grant round
//     robin so no master can starve.  Write arbitration is 2-way (mem
//     vs per) with the same round-robin tie-break.
//   - When a master's AR/AW does NOT target this port (address decode
//     mismatch), the *_select inputs are low and the sub-FSM ignores
//     that master's request entirely.  The top level OR's contributions
//     from all target ports together, so only one port ever drives a
//     given master's *ready pulse.
//   - W-channel race: do NOT capture a new W beat from the CPU while a
//     previously captured beat is still sitting in the m_wvalid register
//     waiting for m_wready.
//
// rd_active encoding:
//   2'd0 = i   (instruction fetch)
//   2'd1 = mem (D$ cached)
//   2'd2 = per (uncached IO)
//

`default_nettype none

module cpu_target_port (
    input wire clk,
    input wire reset_n,

    // ============================================================
    // Selection signals (combinational from cpu_system top)
    // ============================================================
    // High when the corresponding master's incoming transaction targets
    // this port (address decoded AND global-busy permits).  The sub-FSM
    // gates its accept logic on these.
    input wire i_rd_select,
    input wire mem_rd_select,
    input wire per_rd_select,
    input wire mem_wr_select,
    input wire per_wr_select,

    // ============================================================
    // CPU i_axi master — read-only, 1-bit ID from VexiiRiscv
    // ============================================================
    input  wire        i_arvalid,
    output wire        i_arready_contrib,
    input  wire [31:0] i_araddr,
    input  wire [0:0]  i_arid,
    input  wire [7:0]  i_arlen,

    output wire        i_rvalid_contrib,
    output wire [31:0] i_rdata_contrib,
    output wire [0:0]  i_rid_contrib,
    output wire [1:0]  i_rresp_contrib,
    output wire        i_rlast_contrib,
    input  wire        i_rready,

    // ============================================================
    // CPU mem_axi master (D$ bus) — 2-bit ID
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
    input  wire [1:0]  mem_awburst,

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

    // ============================================================
    // Global busy outputs — used by cpu_system.v to cross-serialize
    // (master, direction) so responses for a given master return in
    // order even though each target port has its own FSM.
    // ============================================================
    output wire        i_rd_busy,
    output wire        mem_rd_busy,
    output wire        mem_wr_busy,
    output wire        per_rd_busy,
    output wire        per_wr_busy
);

wire reset = ~reset_n;

// ============================================================
// Read sub-FSM — 3-way arbitrated (i / mem / per)
// ============================================================
localparam [1:0] RD_IDLE = 2'd0;
localparam [1:0] RD_AR   = 2'd1;
localparam [1:0] RD_R    = 2'd2;

// rd_active owner encoding
localparam [1:0] OWN_I   = 2'd0;
localparam [1:0] OWN_MEM = 2'd1;
localparam [1:0] OWN_PER = 2'd2;

reg [1:0] rd_state;
reg [1:0] rd_active;          // OWN_I / OWN_MEM / OWN_PER
reg [1:0] rd_active_id;       // mem master's AXI ID (unused otherwise)
reg       rd_active_iid;      // i   master's 1-bit ID
reg [7:0] rd_burst_len;
reg [7:0] rd_burst_count;
reg [1:0] last_grant_rd;      // round-robin: index of last-granted master

// Registered response slots per master (hold-until-ready).
reg        i_rvalid_r;
reg [31:0] i_rdata_r;
reg        i_rid_r;
reg [1:0]  i_rresp_r;
reg        i_rlast_r;
reg        mem_rvalid_r;
reg [31:0] mem_rdata_r;
reg [1:0]  mem_rid_r;
reg [1:0]  mem_rresp_r;
reg        mem_rlast_r;
reg        per_rvalid_r;
reg [31:0] per_rdata_r;
reg [1:0]  per_rresp_r;
reg        per_rlast_r;

// Round-robin grant: starting from (last_grant_rd + 1), scan cyclically
// for the first master whose *_rd_select is high.
// Flattened to simple priority with the last-grant tie-break because
// it's only 3 masters.
wire [2:0] rd_req = { per_rd_select, mem_rd_select, i_rd_select };
wire any_rd = |rd_req;

reg [1:0] rd_next_owner;
always @(*) begin
    rd_next_owner = OWN_I; // default harmless
    case (last_grant_rd)
    OWN_I:   if      (mem_rd_select) rd_next_owner = OWN_MEM;
             else if (per_rd_select) rd_next_owner = OWN_PER;
             else                    rd_next_owner = OWN_I;
    OWN_MEM: if      (per_rd_select) rd_next_owner = OWN_PER;
             else if (i_rd_select)   rd_next_owner = OWN_I;
             else                    rd_next_owner = OWN_MEM;
    OWN_PER: if      (i_rd_select)   rd_next_owner = OWN_I;
             else if (mem_rd_select) rd_next_owner = OWN_MEM;
             else                    rd_next_owner = OWN_PER;
    default: rd_next_owner = OWN_I;
    endcase
end

// Serialization between rd and wr sub-FSMs on this port.  The
// downstream slave is single-FSM so only one direction can be in-
// flight through it at a time.  We intentionally do NOT gate on the
// registered response slots (mem_bvalid_r, i/mem/per_rvalid_r) — a
// pending response is a master-side handshake that doesn't occupy
// the slave, and cpu_system's global_*_busy already prevents a new
// same-master transaction from overwriting a sticky response slot.
wire no_wr_in_flight = (wr_state == WR_IDLE);
wire no_rd_in_flight = (rd_state == RD_IDLE);
wire rd_can_start    = (rd_state == RD_IDLE) && no_wr_in_flight && any_rd;

wire rd_grant_i   = rd_can_start && (rd_next_owner == OWN_I)   && i_rd_select;
wire rd_grant_mem = rd_can_start && (rd_next_owner == OWN_MEM) && mem_rd_select;
wire rd_grant_per = rd_can_start && (rd_next_owner == OWN_PER) && per_rd_select;

wire rd_beat_is_last = (rd_burst_count == rd_burst_len);

// --- Zero-gap pre-grant ----------------------------------------------------
// When the last beat of a burst is accepted in RD_R, a pending next request
// is normally held until the FSM returns to RD_IDLE (1 dead cycle).  If the
// arbiter would grant a new master next cycle anyway, skip that dead cycle
// by asserting m_arvalid directly from the last-beat cycle.  Safe because:
//   - wr_can_start requires (rd_state == RD_IDLE), which is never true
//     during RD_R, so writes can't collide.
//   - Back-pressure chain (port slot → m_rready → slave skid) still
//     protects against overflow when the new burst's first beats arrive
//     before the old last beat drains from the master's registered slot.
wire rd_last_accept    = (rd_state == RD_R) && m_rvalid && m_rready && rd_beat_is_last;
wire rd_pregrant_avail = no_wr_in_flight && any_rd;
wire rd_pregrant_i   = rd_last_accept && rd_pregrant_avail && (rd_next_owner == OWN_I)   && i_rd_select;
wire rd_pregrant_mem = rd_last_accept && rd_pregrant_avail && (rd_next_owner == OWN_MEM) && mem_rd_select;
wire rd_pregrant_per = rd_last_accept && rd_pregrant_avail && (rd_next_owner == OWN_PER) && per_rd_select;

always @(posedge clk or posedge reset) begin
    if (reset) begin
        rd_state       <= RD_IDLE;
        rd_active      <= OWN_I;
        rd_active_id   <= 2'b0;
        rd_active_iid  <= 1'b0;
        rd_burst_len   <= 8'b0;
        rd_burst_count <= 8'b0;
        last_grant_rd  <= OWN_PER;     // so next grant prefers i
        m_arvalid      <= 1'b0;
        m_araddr       <= 32'b0;
        m_arlen        <= 8'b0;
        i_rvalid_r     <= 1'b0;
        i_rdata_r      <= 32'b0;
        i_rid_r        <= 1'b0;
        i_rresp_r      <= 2'b0;
        i_rlast_r      <= 1'b0;
        mem_rvalid_r   <= 1'b0;
        mem_rdata_r    <= 32'b0;
        mem_rid_r      <= 2'b0;
        mem_rresp_r    <= 2'b0;
        mem_rlast_r    <= 1'b0;
        per_rvalid_r   <= 1'b0;
        per_rdata_r    <= 32'b0;
        per_rresp_r    <= 2'b0;
        per_rlast_r    <= 1'b0;
    end else begin
        // Clear registered beats as the master drains them.
        if (i_rready)   i_rvalid_r   <= 1'b0;
        if (mem_rready) mem_rvalid_r <= 1'b0;
        if (per_rready) per_rvalid_r <= 1'b0;

        case (rd_state)
        RD_IDLE: begin
            m_arvalid <= 1'b0;
            if (rd_grant_i) begin
                rd_active      <= OWN_I;
                rd_active_iid  <= i_arid;
                rd_burst_len   <= i_arlen;
                rd_burst_count <= 8'b0;
                m_arvalid      <= 1'b1;
                m_araddr       <= i_araddr;
                m_arlen        <= i_arlen;
                last_grant_rd  <= OWN_I;
                rd_state       <= RD_AR;
            end else if (rd_grant_mem) begin
                rd_active      <= OWN_MEM;
                rd_active_id   <= mem_arid;
                rd_burst_len   <= mem_arlen;
                rd_burst_count <= 8'b0;
                m_arvalid      <= 1'b1;
                m_araddr       <= mem_araddr;
                m_arlen        <= mem_arlen;
                last_grant_rd  <= OWN_MEM;
                rd_state       <= RD_AR;
            end else if (rd_grant_per) begin
                rd_active      <= OWN_PER;
                rd_burst_len   <= per_arlen;
                rd_burst_count <= 8'b0;
                m_arvalid      <= 1'b1;
                m_araddr       <= per_araddr;
                m_arlen        <= per_arlen;
                last_grant_rd  <= OWN_PER;
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
            // Only accept a beat when the owning master's registered slot
            // is empty (or emptying via its own rready this cycle — the
            // m_rready combinational drives accordingly).
            if (m_rvalid && m_rready) begin
                case (rd_active)
                OWN_I: begin
                    i_rvalid_r <= 1'b1;
                    i_rdata_r  <= m_rdata;
                    i_rid_r    <= rd_active_iid;
                    i_rresp_r  <= m_rresp;
                    i_rlast_r  <= rd_beat_is_last;
                end
                OWN_MEM: begin
                    mem_rvalid_r <= 1'b1;
                    mem_rdata_r  <= m_rdata;
                    mem_rid_r    <= rd_active_id;
                    mem_rresp_r  <= m_rresp;
                    mem_rlast_r  <= rd_beat_is_last;
                end
                default: begin
                    per_rvalid_r <= 1'b1;
                    per_rdata_r  <= m_rdata;
                    per_rresp_r  <= m_rresp;
                    per_rlast_r  <= rd_beat_is_last;
                end
                endcase
                rd_burst_count <= rd_burst_count + 8'd1;
                if (rd_beat_is_last) begin
                    // Pre-grant path: skip the RD_IDLE dead cycle when a
                    // next owner is already requesting.  Last assignment
                    // wins for rd_burst_count / rd_burst_len — the new
                    // burst's values below override the increment above.
                    if (rd_pregrant_i) begin
                        rd_active      <= OWN_I;
                        rd_active_iid  <= i_arid;
                        rd_burst_len   <= i_arlen;
                        rd_burst_count <= 8'b0;
                        m_arvalid      <= 1'b1;
                        m_araddr       <= i_araddr;
                        m_arlen        <= i_arlen;
                        last_grant_rd  <= OWN_I;
                        rd_state       <= RD_AR;
                    end else if (rd_pregrant_mem) begin
                        rd_active      <= OWN_MEM;
                        rd_active_id   <= mem_arid;
                        rd_burst_len   <= mem_arlen;
                        rd_burst_count <= 8'b0;
                        m_arvalid      <= 1'b1;
                        m_araddr       <= mem_araddr;
                        m_arlen        <= mem_arlen;
                        last_grant_rd  <= OWN_MEM;
                        rd_state       <= RD_AR;
                    end else if (rd_pregrant_per) begin
                        rd_active      <= OWN_PER;
                        rd_burst_len   <= per_arlen;
                        rd_burst_count <= 8'b0;
                        m_arvalid      <= 1'b1;
                        m_araddr       <= per_araddr;
                        m_arlen        <= per_arlen;
                        last_grant_rd  <= OWN_PER;
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

// Single-cycle arready pulses — combinational from grant.  Also fires on
// the zero-gap pre-grant path so the granted master sees its AR consumed.
wire rd_i_arready_pulse   = rd_grant_i   || rd_pregrant_i;
wire rd_mem_arready_pulse = rd_grant_mem || rd_pregrant_mem;
wire rd_per_arready_pulse = rd_grant_per || rd_pregrant_per;

// ============================================================
// Write sub-FSM — 2-way (mem / per); i_axi never writes.
// ============================================================
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

reg wr_mem_wready_pulse;
reg wr_per_wready_pulse;

reg       mem_bvalid_r;
reg [1:0] mem_bid_r;
reg [1:0] mem_bresp_r;
reg       per_bvalid_r;
reg [1:0] per_bresp_r;

wire wr_beat_is_last = (wr_burst_count == wr_burst_len);

always @(posedge clk or posedge reset) begin
    if (reset) begin
        wr_state           <= WR_IDLE;
        wr_active_is_mem   <= 1'b0;
        wr_active_id       <= 2'b0;
        wr_burst_len       <= 8'b0;
        wr_burst_count     <= 8'b0;
        last_grant_wr_mem  <= 1'b0;
        m_awvalid          <= 1'b0;
        m_awaddr           <= 32'b0;
        m_awlen            <= 8'b0;
        m_awburst          <= 2'b01;
        m_wvalid           <= 1'b0;
        m_wdata            <= 32'b0;
        m_wstrb            <= 4'b0;
        m_wlast            <= 1'b0;
        wr_mem_wready_pulse <= 1'b0;
        wr_per_wready_pulse <= 1'b0;
        mem_bvalid_r <= 1'b0;
        mem_bid_r    <= 2'b0;
        mem_bresp_r  <= 2'b0;
        per_bvalid_r <= 1'b0;
        per_bresp_r  <= 2'b0;
    end else begin
        wr_mem_wready_pulse <= 1'b0;
        wr_per_wready_pulse <= 1'b0;
        if (mem_bready) mem_bvalid_r <= 1'b0;
        if (per_bready) per_bvalid_r <= 1'b0;

        case (wr_state)
        // WR_IDLE bundles W into the same cycle as AW when the master
        // already has wvalid up — for single-beat MMIO writes (the
        // common case for the mixer's per-note register writes)
        // this skips the WR_AW intermediate state, saving 1 cycle per
        // write at the master-side handshake.  WR_AW handles the
        // unbundled case (W arrives later) by dropping awready and
        // falling through to WR_W to pull W normally.
        WR_IDLE: begin
            m_awvalid <= 1'b0;
            m_wvalid  <= 1'b0;
            if (wr_grant_mem) begin
                wr_active_is_mem  <= 1'b1;
                wr_active_id      <= mem_awid;
                wr_burst_len      <= mem_awlen;
                wr_burst_count    <= 8'b0;
                m_awvalid         <= 1'b1;
                m_awaddr          <= mem_awaddr;
                m_awlen           <= mem_awlen;
                m_awburst         <= mem_awburst;
                last_grant_wr_mem <= 1'b1;
                wr_state          <= WR_AW;
                if (mem_wvalid) begin
                    m_wvalid <= 1'b1;
                    m_wdata  <= mem_wdata;
                    m_wstrb  <= mem_wstrb;
                    m_wlast  <= mem_wlast;
                    wr_mem_wready_pulse <= 1'b1;
                end
            end else if (wr_grant_per) begin
                wr_active_is_mem  <= 1'b0;
                wr_active_id      <= 2'b0;
                wr_burst_len      <= per_awlen;
                wr_burst_count    <= 8'b0;
                m_awvalid         <= 1'b1;
                m_awaddr          <= per_awaddr;
                m_awlen           <= per_awlen;
                m_awburst         <= per_awburst;
                last_grant_wr_mem <= 1'b0;
                wr_state          <= WR_AW;
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
// Master-facing contribution outputs (OR'd at top level)
// ============================================================
assign i_arready_contrib   = rd_i_arready_pulse;
assign mem_arready_contrib = rd_mem_arready_pulse;
assign per_arready_contrib = rd_per_arready_pulse;
assign mem_awready_contrib = wr_mem_awready_pulse;
assign per_awready_contrib = wr_per_awready_pulse;
assign mem_wready_contrib  = wr_mem_wready_pulse;
assign per_wready_contrib  = wr_per_wready_pulse;

// Read responses (sticky until master's *_rready is asserted)
assign i_rvalid_contrib   = i_rvalid_r;
assign i_rdata_contrib    = i_rvalid_r ? i_rdata_r : 32'b0;
assign i_rid_contrib      = i_rvalid_r ? i_rid_r   : 1'b0;
assign i_rresp_contrib    = i_rvalid_r ? i_rresp_r : 2'b0;
assign i_rlast_contrib    = i_rvalid_r ? i_rlast_r : 1'b0;

assign mem_rvalid_contrib = mem_rvalid_r;
assign mem_rdata_contrib  = mem_rvalid_r ? mem_rdata_r : 32'b0;
assign mem_rid_contrib    = mem_rvalid_r ? mem_rid_r   : 2'b0;
assign mem_rresp_contrib  = mem_rvalid_r ? mem_rresp_r : 2'b0;
assign mem_rlast_contrib  = mem_rvalid_r ? mem_rlast_r : 1'b0;

assign per_rvalid_contrib = per_rvalid_r;
assign per_rdata_contrib  = per_rvalid_r ? per_rdata_r : 32'b0;
assign per_rresp_contrib  = per_rvalid_r ? per_rresp_r : 2'b0;
assign per_rlast_contrib  = per_rvalid_r ? per_rlast_r : 1'b0;

// Write responses (sticky until master's *_bready)
assign mem_bvalid_contrib = mem_bvalid_r;
assign mem_bid_contrib    = mem_bvalid_r ? mem_bid_r   : 2'b0;
assign mem_bresp_contrib  = mem_bvalid_r ? mem_bresp_r : 2'b0;

assign per_bvalid_contrib = per_bvalid_r;
assign per_bresp_contrib  = per_bvalid_r ? per_bresp_r : 2'b0;

// ============================================================
// Slave-side R-channel back-pressure
// ============================================================
// Hold m_rready low when the owning master's registered slot is full
// and won't be drained this cycle — the slave keeps m_rvalid high until
// we catch up, no beat is lost.
assign m_rready = (rd_state == RD_R)
                  ? ((rd_active == OWN_I)
                        ? (!i_rvalid_r   || i_rready)
                        : (rd_active == OWN_MEM)
                              ? (!mem_rvalid_r || mem_rready)
                              : (!per_rvalid_r || per_rready))
                  : 1'b1;

// ============================================================
// Global busy outputs (per master, per direction)
// ============================================================
assign i_rd_busy   = ((rd_state != RD_IDLE) && (rd_active == OWN_I))   || i_rvalid_r;
assign mem_rd_busy = ((rd_state != RD_IDLE) && (rd_active == OWN_MEM)) || mem_rvalid_r;
assign per_rd_busy = ((rd_state != RD_IDLE) && (rd_active == OWN_PER)) || per_rvalid_r;
assign mem_wr_busy = ((wr_state != WR_IDLE) &&  wr_active_is_mem) || mem_bvalid_r;
assign per_wr_busy = ((wr_state != WR_IDLE) && !wr_active_is_mem) || per_bvalid_r;

endmodule
