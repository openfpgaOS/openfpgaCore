//
// Word-Level SDRAM Arbiter — 4 Masters, Fixed Priority
//
// Replaces axi_sdram_arbiter + axi_sdram_slave + pulse adapter.
// All masters use word-level protocol. Single outstanding transaction.
//
// Fixed priority: M0 (Audio DMA) > M1 (DMA) > M3 (Bridge) > M2 (CPU).
//
// Handles pulse adaptation: masters hold rd/wr until accepted.
// Arbiter converts to single-cycle pulse for io_sdram.
//
// Pure register/LUT — 0 M10K.
//

`default_nettype none

module word_sdram_arbiter (
    input wire clk,
    input wire reset_n,

    // Master 0: Audio DMA (highest priority, read-only)
    input  wire        m0_rd,
    input  wire [23:0] m0_addr,
    input  wire [3:0]  m0_burst_len,
    output wire [31:0] m0_rdata,
    output wire        m0_busy,
    output wire        m0_accepted,
    output wire        m0_rdata_valid,

    // Master 1: DMA engine
    input  wire        m1_rd,
    input  wire        m1_wr,
    input  wire [23:0] m1_addr,
    input  wire [31:0] m1_wdata,
    input  wire [3:0]  m1_wstrb,
    input  wire [3:0]  m1_burst_len,
    input  wire [3:0]  m1_burst_wr_len,
    output wire [31:0] m1_rdata,
    output wire        m1_busy,
    output wire        m1_accepted,
    output wire        m1_rdata_valid,
    output wire        m1_wr_data_next,

    // Master 2: CPU
    input  wire        m2_rd,
    input  wire        m2_wr,
    input  wire [23:0] m2_addr,
    input  wire [31:0] m2_wdata,
    input  wire [3:0]  m2_wstrb,
    input  wire [3:0]  m2_burst_len,
    input  wire [3:0]  m2_burst_wr_len,
    output wire [31:0] m2_rdata,
    output wire        m2_busy,
    output wire        m2_accepted,
    output wire        m2_rdata_valid,
    output wire        m2_wr_data_next,

    // Master 3: Bridge (lowest priority)
    input  wire        m3_rd,
    input  wire        m3_wr,
    input  wire [23:0] m3_addr,
    input  wire [31:0] m3_wdata,
    input  wire [3:0]  m3_wstrb,
    input  wire [3:0]  m3_burst_len,
    input  wire [3:0]  m3_burst_wr_len,
    output wire [31:0] m3_rdata,
    output wire        m3_busy,
    output wire        m3_accepted,
    output wire        m3_rdata_valid,
    output wire        m3_wr_data_next,

    // io_sdram word interface
    output reg         sdram_rd,
    output reg         sdram_wr,
    output reg  [23:0] sdram_addr,
    output reg  [31:0] sdram_wdata,
    output reg  [3:0]  sdram_wstrb,
    output reg  [3:0]  sdram_burst_len,
    output reg  [3:0]  sdram_burst_wr_len,
    input  wire [31:0] sdram_rdata,
    input  wire        sdram_busy,
    input  wire        sdram_rdata_valid,
    input  wire        sdram_wr_data_next,

    // Direct combinational data path for burst writes (bypasses registered sdram_wdata)
    // Connect to io_sdram's burst_wr_direct_data for zero-latency data forwarding
    output wire [31:0] sdram_wdata_direct,
    output wire [3:0]  sdram_wstrb_direct
);

wire reset = ~reset_n;

// ============================================
// Arbiter state
// ============================================
localparam ST_IDLE   = 2'd0;
localparam ST_ACTIVE = 2'd1;

reg [1:0] arb_state;
reg [1:0] grant;  // 0=M0, 1=M1, 2=M2, 3=M3
reg       cmd_forwarded;  // Pulse already sent to io_sdram
reg       io_busy_seen;   // io_sdram busy was seen (transaction started)
reg       wr_data_fwd_d1; // 1-cycle delay for burst write data forwarding

// Request detection
wire m0_req = m0_rd;
wire m1_req = m1_rd | m1_wr;
wire m2_req = m2_rd | m2_wr;
wire m3_req = m3_rd | m3_wr;

// Muxed master signals based on grant
wire        mux_rd   = (grant == 0) ? m0_rd   : (grant == 1) ? m1_rd   : (grant == 2) ? m2_rd   : m3_rd;
wire        mux_wr   = (grant == 0) ? 1'b0    : (grant == 1) ? m1_wr   : (grant == 2) ? m2_wr   : m3_wr;
wire [23:0] mux_addr = (grant == 0) ? m0_addr : (grant == 1) ? m1_addr : (grant == 2) ? m2_addr : m3_addr;
wire [31:0] mux_wdata= (grant == 1) ? m1_wdata: (grant == 2) ? m2_wdata: m3_wdata;
wire [3:0]  mux_wstrb= (grant == 1) ? m1_wstrb: (grant == 2) ? m2_wstrb: m3_wstrb;
wire [3:0]  mux_blen = (grant == 0) ? m0_burst_len : (grant == 1) ? m1_burst_len : (grant == 2) ? m2_burst_len : m3_burst_len;
wire [3:0]  mux_bwlen= (grant == 1) ? m1_burst_wr_len : (grant == 2) ? m2_burst_wr_len : m3_burst_wr_len;

// ============================================
// Grant arbitration + pulse adapter
// ============================================
always @(posedge clk or posedge reset) begin
    if (reset) begin
        arb_state <= ST_IDLE;
        grant <= 0;
        cmd_forwarded <= 0;
        io_busy_seen <= 0;
        wr_data_fwd_d1 <= 0;
        sdram_rd <= 0;
        sdram_wr <= 0;
        sdram_addr <= 0;
        sdram_wdata <= 0;
        sdram_wstrb <= 0;
        sdram_burst_len <= 0;
        sdram_burst_wr_len <= 0;
    end else begin
        // Default: deassert pulses
        sdram_rd <= 0;
        sdram_wr <= 0;
        sdram_burst_len <= 0;
        sdram_burst_wr_len <= 0;

        case (arb_state)

        ST_IDLE: begin
            cmd_forwarded <= 0;
            io_busy_seen <= 0;
            // Fixed priority: M0 > M1 > M3 > M2
            if (m0_req) begin
                grant <= 2'd0;
                arb_state <= ST_ACTIVE;
            end else if (m1_req) begin
                grant <= 2'd1;
                arb_state <= ST_ACTIVE;
            end else if (m3_req) begin
                grant <= 2'd3;
                arb_state <= ST_ACTIVE;
            end else if (m2_req) begin
                grant <= 2'd2;
                arb_state <= ST_ACTIVE;
            end
        end

        ST_ACTIVE: begin
            // Pulse adapter: forward rd/wr to io_sdram when !busy and not yet forwarded
            if (!cmd_forwarded && !sdram_busy && (mux_rd || mux_wr)) begin
                sdram_rd <= mux_rd;
                sdram_wr <= mux_wr;
                sdram_addr <= mux_addr;
                sdram_wdata <= mux_wdata;
                sdram_wstrb <= mux_wstrb;
                sdram_burst_len <= mux_blen;
                sdram_burst_wr_len <= mux_bwlen;
                cmd_forwarded <= 1;
            end

            // Track io_sdram activity
            if (sdram_busy) io_busy_seen <= 1;

            // Burst write data forwarding: update registered wdata when
            // io_sdram requests next word (for ram1_word_data path).
            // The burst_wr_direct path uses sdram_wdata_direct (combinational).
            if (sdram_wr_data_next) begin
                sdram_wdata <= mux_wdata;
                sdram_wstrb <= mux_wstrb;
            end

            // Transaction complete: io_sdram went busy then returned to idle
            // Master has also deasserted rd/wr (processed the accepted)
            if (cmd_forwarded && io_busy_seen && !sdram_busy) begin
                cmd_forwarded <= 0;
                io_busy_seen <= 0;
                // Check if same master has another request immediately
                if (mux_rd || mux_wr) begin
                    // Stay granted — new transaction from same master
                end else begin
                    arb_state <= ST_IDLE;
                end
            end
        end

        default: arb_state <= ST_IDLE;

        endcase
    end
end

// ============================================
// Response routing
// ============================================
wire grant_m0 = (grant == 2'd0);
wire grant_m1 = (grant == 2'd1);
wire grant_m2 = (grant == 2'd2);
wire grant_m3 = (grant == 2'd3);
wire active = (arb_state == ST_ACTIVE);

// accepted: registered pulse (same cycle as cmd_forwarded transitions 0→1)
reg accepted_r;
always @(posedge clk or posedge reset) begin
    if (reset)
        accepted_r <= 0;
    else
        accepted_r <= active && !cmd_forwarded && !sdram_busy && (mux_rd || mux_wr);
end

// Read data: broadcast to all, only valid matters
assign m0_rdata = sdram_rdata;
assign m1_rdata = sdram_rdata;
assign m2_rdata = sdram_rdata;
assign m3_rdata = sdram_rdata;

// busy: non-granted masters always see busy=1
assign m0_busy = (active && grant_m0) ? sdram_busy : 1'b1;
assign m1_busy = (active && grant_m1) ? sdram_busy : 1'b1;
assign m2_busy = (active && grant_m2) ? sdram_busy : 1'b1;
assign m3_busy = (active && grant_m3) ? sdram_busy : 1'b1;

// accepted: only to granted master
assign m0_accepted = (active && grant_m0) ? accepted_r : 1'b0;
assign m1_accepted = (active && grant_m1) ? accepted_r : 1'b0;
assign m2_accepted = (active && grant_m2) ? accepted_r : 1'b0;
assign m3_accepted = (active && grant_m3) ? accepted_r : 1'b0;

// rdata_valid: only to granted master
assign m0_rdata_valid = (active && grant_m0) ? sdram_rdata_valid : 1'b0;
assign m1_rdata_valid = (active && grant_m1) ? sdram_rdata_valid : 1'b0;
assign m2_rdata_valid = (active && grant_m2) ? sdram_rdata_valid : 1'b0;
assign m3_rdata_valid = (active && grant_m3) ? sdram_rdata_valid : 1'b0;

// wr_data_next: only to granted master
assign m1_wr_data_next = (active && grant_m1) ? sdram_wr_data_next : 1'b0;
assign m2_wr_data_next = (active && grant_m2) ? sdram_wr_data_next : 1'b0;
assign m3_wr_data_next = (active && grant_m3) ? sdram_wr_data_next : 1'b0;

// Direct combinational data path: io_sdram reads this for burst_wr_direct_data
// Uses current master's wdata (no registration delay)
assign sdram_wdata_direct = active ? mux_wdata : sdram_wdata;
assign sdram_wstrb_direct = active ? mux_wstrb : sdram_wstrb;

endmodule
