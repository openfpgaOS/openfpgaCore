//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

//
// cram0_cdc.v — AXI4 slave (clk_cpu) → word-interface (clk_bridge)
//
// Cross-clock bridge between a CPU-side AXI4 master (running in
// clk_cpu, typically 100 MHz) and the CRAM0 controller's word-level
// read/write interface (running in clk_bridge, the APF bridge clock
// at 74.25 MHz).
//
// Architecture: a req/ack handshake ping-pongs across both domains.
// One transaction (one 32-bit word) at a time.  No async FIFO — a
// 3-stage flop chain per toggle direction is plenty for the cold-path
// memcpy traffic this block carries (CPU ↔ CRAM0 is explicit save /
// load staging, not hot audio or frame traffic).
//
// Request flow:
//   1. AXI AR/AW accepted → latch address / wdata / wstrb / is_write.
//   2. Flip req_toggle_cpu.
//   3. req_toggle_cpu crosses to clk_bridge via a 3-flop synchroniser;
//      the latched data buses cross via 2-flop chains.  This is safe
//      because data is held stable by the CPU FSM for the entire round
//      trip and the bridge FSM gates on the toggle edge only AFTER the
//      synchroniser chains for that transaction have settled — no
//      ordering race is possible.
//   4. Bridge FSM on toggle edge pulses b_word_rd or b_word_wr with
//      the synced fields.
//   5. Bridge FSM waits for the controller:
//        - writes → busy rises then falls (saw-busy gate)
//        - reads  → b_word_rdata_valid pulse (latch b_word_rdata)
//   6. Flip resp_toggle_bridge on completion.
//   7. resp_toggle_bridge crosses back to clk_cpu via a 3-flop
//      synchroniser; the latched rdata crosses via a 2-flop chain.
//   8. CPU FSM on edge detect emits the AXI R or B response.
//
// Worst-case round trip: a write takes ~8-10 clk_bridge cycles
// (2 three-flop synchroniser hops + controller latency).  Reads add
// the controller's own read latency.  This is fine: the CPU touches
// CRAM0 only during explicit load/save staging.
//
// Multi-beat bursts dispatch each beat as a separate handshake; no
// beat pipelining.  Simplest possible implementation.
//

`default_nettype none

module cram0_cdc (
    // ============================================================
    // CPU side (clk_cpu): AXI4 slave
    // ============================================================
    input  wire        clk_cpu,
    input  wire        reset_n_cpu,

    input  wire        s_axi_arvalid,
    output reg         s_axi_arready,
    input  wire [31:0] s_axi_araddr,
    input  wire [7:0]  s_axi_arlen,

    output reg         s_axi_rvalid,
    input  wire        s_axi_rready,
    output reg  [31:0] s_axi_rdata,
    output reg  [1:0]  s_axi_rresp,
    output reg         s_axi_rlast,

    input  wire        s_axi_awvalid,
    output reg         s_axi_awready,
    input  wire [31:0] s_axi_awaddr,
    input  wire [7:0]  s_axi_awlen,

    input  wire        s_axi_wvalid,
    output reg         s_axi_wready,
    input  wire [31:0] s_axi_wdata,
    input  wire [3:0]  s_axi_wstrb,
    input  wire        s_axi_wlast,

    output reg         s_axi_bvalid,
    input  wire        s_axi_bready,
    output reg  [1:0]  s_axi_bresp,

    // ============================================================
    // Bridge-clock side (clk_bridge): word interface to cram0_controller
    // ============================================================
    input  wire        clk_bridge,
    input  wire        reset_n_bridge,

    output reg         b_word_rd,
    output reg         b_word_wr,
    output reg  [21:0] b_word_addr,
    output reg  [31:0] b_word_wdata,
    output reg  [3:0]  b_word_wstrb,
    input  wire [31:0] b_word_rdata,
    input  wire        b_word_busy,
    input  wire        b_word_rdata_valid
);

// ============================================================
// CPU-side state machine — captures AXI beats, dispatches via toggle
// ============================================================
localparam C_IDLE    = 3'd0;
localparam C_RD_REQ  = 3'd1;  // waiting for bridge to ack the read beat
localparam C_RD_RESP = 3'd2;  // emit R beat
localparam C_WR_REQ  = 3'd3;  // waiting for W beat then bridge ack
localparam C_WR_WAIT = 3'd4;  // W beat captured, waiting for bridge ack
localparam C_WR_RESP = 3'd5;  // emit B response

reg [2:0]  c_state;
reg [31:0] c_addr;
reg [7:0]  c_len;         // AXI ARLEN / AWLEN captured
reg [7:0]  c_beat;        // beat index (0..c_len)
reg [31:0] c_wdata;
reg [3:0]  c_wstrb;
reg        c_is_write;

// Handshake toggle: flipped when a new beat is dispatched.
reg        req_toggle_cpu;

// Response-toggle synchroniser from clk_bridge, and rdata sampled via
// parallel flop chain.  Data is stable for the entire response-window
// because the bridge FSM latches it before flipping resp_toggle_bridge.
wire        resp_toggle_cpu_s;
wire [31:0] c_rdata_synced;
reg         resp_toggle_prev_cpu;
wire        resp_edge_cpu = (resp_toggle_cpu_s != resp_toggle_prev_cpu);

// beat_is_last — current AXI beat is final.
wire c_beat_is_last = (c_beat == c_len);

always @(posedge clk_cpu or negedge reset_n_cpu) begin
    if (!reset_n_cpu) begin
        c_state              <= C_IDLE;
        c_addr               <= 32'd0;
        c_len                <= 8'd0;
        c_beat               <= 8'd0;
        c_wdata              <= 32'd0;
        c_wstrb              <= 4'd0;
        c_is_write           <= 1'b0;
        req_toggle_cpu       <= 1'b0;
        resp_toggle_prev_cpu <= 1'b0;

        s_axi_arready        <= 1'b0;
        s_axi_awready        <= 1'b0;
        s_axi_wready         <= 1'b0;
        s_axi_rvalid         <= 1'b0;
        s_axi_rdata          <= 32'd0;
        s_axi_rresp          <= 2'b00;
        s_axi_rlast          <= 1'b0;
        s_axi_bvalid         <= 1'b0;
        s_axi_bresp          <= 2'b00;
    end else begin
        // Single-cycle pulse defaults
        s_axi_arready <= 1'b0;
        s_axi_awready <= 1'b0;
        s_axi_wready  <= 1'b0;

        // Drop rvalid / bvalid on AXI handshake
        if (s_axi_rvalid && s_axi_rready) s_axi_rvalid <= 1'b0;
        if (s_axi_bvalid && s_axi_bready) s_axi_bvalid <= 1'b0;

        // Track response toggle for edge detect
        resp_toggle_prev_cpu <= resp_toggle_cpu_s;

        case (c_state)

        C_IDLE: begin
            if (s_axi_arvalid) begin
                // Accept read command.
                s_axi_arready  <= 1'b1;
                c_addr         <= s_axi_araddr;
                c_len          <= s_axi_arlen;
                c_beat         <= 8'd0;
                c_is_write     <= 1'b0;
                req_toggle_cpu <= ~req_toggle_cpu;
                c_state        <= C_RD_REQ;
            end else if (s_axi_awvalid) begin
                s_axi_awready  <= 1'b1;
                c_addr         <= s_axi_awaddr;
                c_len          <= s_axi_awlen;
                c_beat         <= 8'd0;
                c_is_write     <= 1'b1;
                c_state        <= C_WR_REQ;
            end
        end

        C_RD_REQ: begin
            if (resp_edge_cpu) begin
                // Fresh response — emit R beat.
                s_axi_rvalid <= 1'b1;
                s_axi_rdata  <= c_rdata_synced;
                s_axi_rresp  <= 2'b00;
                s_axi_rlast  <= c_beat_is_last;
                c_state      <= C_RD_RESP;
            end
        end

        C_RD_RESP: begin
            // Wait for master to accept the R beat (handled by drop
            // block at top of always).  Then advance or finish.
            if (!s_axi_rvalid) begin
                if (c_beat_is_last) begin
                    c_state <= C_IDLE;
                end else begin
                    c_beat         <= c_beat + 8'd1;
                    c_addr         <= c_addr + 32'd4;
                    req_toggle_cpu <= ~req_toggle_cpu;
                    c_state        <= C_RD_REQ;
                end
            end
        end

        C_WR_REQ: begin
            // Need a W beat before dispatching.
            if (s_axi_wvalid) begin
                s_axi_wready   <= 1'b1;
                c_wdata        <= s_axi_wdata;
                c_wstrb        <= s_axi_wstrb;
                req_toggle_cpu <= ~req_toggle_cpu;
                c_state        <= C_WR_WAIT;
            end
        end

        C_WR_WAIT: begin
            // Wait for bridge to complete the write beat.
            if (resp_edge_cpu) begin
                if (c_beat_is_last) begin
                    s_axi_bvalid <= 1'b1;
                    s_axi_bresp  <= 2'b00;
                    c_state      <= C_WR_RESP;
                end else begin
                    c_beat  <= c_beat + 8'd1;
                    c_addr  <= c_addr + 32'd4;
                    c_state <= C_WR_REQ;
                end
            end
        end

        C_WR_RESP: begin
            if (!s_axi_bvalid) begin
                c_state <= C_IDLE;
            end
        end

        default: c_state <= C_IDLE;

        endcase
    end
end

// ============================================================
// CPU → bridge data synchroniser
// ============================================================
// Request toggle crosses with a 3-stage flop chain in clk_bridge.
// Addr / wdata / wstrb / is_write likewise cross with 2-stage flop
// chains; they are stable in clk_cpu from the moment req_toggle_cpu
// flips until the response edge arrives (≥6 clk_bridge cycles round
// trip), so by the time the bridge FSM samples them on req edge they
// have settled.  2 stages is enough — no data-corruption window is
// possible given the toggle-gated dispatch.
reg [2:0]  req_toggle_bridge_sync;
reg [31:0] b_addr_sync1,  b_addr_sync2;
reg [31:0] b_wdata_sync1, b_wdata_sync2;
reg [3:0]  b_wstrb_sync1, b_wstrb_sync2;
reg        b_iswrite_sync1, b_iswrite_sync2;

always @(posedge clk_bridge or negedge reset_n_bridge) begin
    if (!reset_n_bridge) begin
        req_toggle_bridge_sync <= 3'b0;
        b_addr_sync1    <= 32'd0;
        b_addr_sync2    <= 32'd0;
        b_wdata_sync1   <= 32'd0;
        b_wdata_sync2   <= 32'd0;
        b_wstrb_sync1   <= 4'd0;
        b_wstrb_sync2   <= 4'd0;
        b_iswrite_sync1 <= 1'b0;
        b_iswrite_sync2 <= 1'b0;
    end else begin
        req_toggle_bridge_sync <= {req_toggle_bridge_sync[1:0], req_toggle_cpu};
        b_addr_sync1    <= c_addr;
        b_addr_sync2    <= b_addr_sync1;
        b_wdata_sync1   <= c_wdata;
        b_wdata_sync2   <= b_wdata_sync1;
        b_wstrb_sync1   <= c_wstrb;
        b_wstrb_sync2   <= b_wstrb_sync1;
        b_iswrite_sync1 <= c_is_write;
        b_iswrite_sync2 <= b_iswrite_sync1;
    end
end

// ============================================================
// Bridge-side state machine
// ============================================================
localparam B_IDLE    = 2'd0;
localparam B_WAIT    = 2'd2;  // wait for completion

reg [1:0]  b_state;
reg        b_prev_toggle;
wire       b_req_edge = (req_toggle_bridge_sync[2] != b_prev_toggle);

reg        b_busy_seen;
reg [31:0] b_rdata_hold;
reg        resp_toggle_bridge;
reg        b_op_is_write;  // latched from b_iswrite_sync2 on dispatch
reg        b_req_pending;
reg [31:0] b_pending_addr;
reg [31:0] b_pending_wdata;
reg [3:0]  b_pending_wstrb;
reg        b_pending_iswrite;

always @(posedge clk_bridge or negedge reset_n_bridge) begin
    if (!reset_n_bridge) begin
        b_state            <= B_IDLE;
        b_prev_toggle      <= 1'b0;
        b_word_rd          <= 1'b0;
        b_word_wr          <= 1'b0;
        b_word_addr        <= 22'd0;
        b_word_wdata       <= 32'd0;
        b_word_wstrb       <= 4'd0;
        b_busy_seen        <= 1'b0;
        b_rdata_hold       <= 32'd0;
        resp_toggle_bridge <= 1'b0;
        b_op_is_write      <= 1'b0;
        b_req_pending      <= 1'b0;
        b_pending_addr     <= 32'd0;
        b_pending_wdata    <= 32'd0;
        b_pending_wstrb    <= 4'd0;
        b_pending_iswrite  <= 1'b0;
    end else begin
        // Default: deassert single-cycle pulses
        b_word_rd     <= 1'b0;
        b_word_wr     <= 1'b0;

        if (b_req_edge && !b_req_pending) begin
            b_prev_toggle     <= req_toggle_bridge_sync[2];
            b_req_pending     <= 1'b1;
            b_pending_addr    <= b_addr_sync2;
            b_pending_wdata   <= b_wdata_sync2;
            b_pending_wstrb   <= b_wstrb_sync2;
            b_pending_iswrite <= b_iswrite_sync2;
        end

        case (b_state)

        B_IDLE: begin
            if (b_req_pending && !b_word_busy) begin
                // Convert byte address → word address.
                // 16 MB = 4 M words ⇒ word-addr width = 22 bits, so
                // word_addr = byte_addr[23:2].  Address decode happens
                // upstream, so c_addr is already guaranteed to live in
                // the CRAM0 range.
                b_word_addr   <= b_pending_addr[23:2];
                b_word_wdata  <= b_pending_wdata;
                b_word_wstrb  <= b_pending_wstrb;
                b_op_is_write <= b_pending_iswrite;
                b_req_pending <= 1'b0;
                if (b_pending_iswrite)
                    b_word_wr <= 1'b1;
                else
                    b_word_rd <= 1'b1;
                b_busy_seen <= 1'b0;
                b_state     <= B_WAIT;
            end
        end

        B_WAIT: begin
            // Detect "controller accepted the request": busy rises.
            if (!b_busy_seen && b_word_busy)
                b_busy_seen <= 1'b1;

            if (b_op_is_write) begin
                // Write completion: busy has risen then fallen.
                if (b_busy_seen && !b_word_busy) begin
                    resp_toggle_bridge <= ~resp_toggle_bridge;
                    b_state            <= B_IDLE;
                end
            end else begin
                // Read completion: rdata_valid pulse.
                if (b_word_rdata_valid) begin
                    b_rdata_hold       <= b_word_rdata;
                    resp_toggle_bridge <= ~resp_toggle_bridge;
                    b_state            <= B_IDLE;
                end
            end
        end

        default: b_state <= B_IDLE;

        endcase
    end
end

// ============================================================
// Bridge → CPU response synchroniser
// ============================================================
// Toggle: 3-stage synchroniser in clk_cpu.  rdata: 2-stage flop chain.
// b_rdata_hold is stable from the moment resp_toggle_bridge flips
// (latched one cycle earlier) until the CPU FSM fires another beat
// — the CPU-side FSM samples c_rdata_synced only after observing the
// toggle edge, which itself is ≥3 clk_cpu cycles behind the flip.
reg [2:0]  resp_toggle_cpu_sync;
reg [31:0] c_rdata_sync1, c_rdata_sync2;

always @(posedge clk_cpu or negedge reset_n_cpu) begin
    if (!reset_n_cpu) begin
        resp_toggle_cpu_sync <= 3'b0;
        c_rdata_sync1        <= 32'd0;
        c_rdata_sync2        <= 32'd0;
    end else begin
        resp_toggle_cpu_sync <= {resp_toggle_cpu_sync[1:0], resp_toggle_bridge};
        c_rdata_sync1        <= b_rdata_hold;
        c_rdata_sync2        <= c_rdata_sync1;
    end
end
assign resp_toggle_cpu_s = resp_toggle_cpu_sync[2];
assign c_rdata_synced    = c_rdata_sync2;

endmodule
