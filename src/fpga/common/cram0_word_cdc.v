//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------
//
// cram0_word_cdc — word-interface clock-domain crossing for CRAM0.
//
// Lets the cram0_controller run on clk_cpu (100 MHz) while the existing
// ownership mux / bridge burst-FIFO / CPU cram0_cdc glue stays on clk_74a
// (74.25 MHz) unchanged.  Sits at the controller's word-interface boundary:
//
//   clk_74a mux  ── w_* request ──►  [this CDC]  ── c_* ──►  cram0_controller
//                ◄── w_rdata/busy ──             ◄── c_rdata/busy ── (clk_cpu)
//
// Toggle handshake (NO FIFO, NO M10K) — one outstanding word op at a time,
// which is all the controller does anyway (single-FSM, serializes ops).
// Mirrors the proven cram0_cdc.v pattern: a 1-bit request/response toggle
// crossed with 3-stage flop chains, and the multi-bit payload / rdata crossed
// with 2-stage chains that are guaranteed stable across the round trip because
// the toggle gates the sample.
//
// The controller's word protocol: pulse word_rd/word_wr -> word_busy rises ->
// (read) word_q_valid pulses with word_q -> word_busy falls = done.  We use a
// saw-busy gate (wait for c_busy HIGH before looking for completion) so a
// request->busy dispatch gap can't false-complete (feedback_burst_saw_busy_gate).
//------------------------------------------------------------------------------

`default_nettype none

module cram0_word_cdc (
    // ---- clk_74a requester side (faces the CRAM0 ownership mux) ----
    input  wire        clk_74a,
    input  wire        reset_n_74a,
    input  wire        w_rd,            // read request pulse (1 cyc)
    input  wire        w_wr,            // write request pulse (1 cyc)
    input  wire [21:0] w_addr,          // 22-bit word address
    input  wire [31:0] w_wdata,
    input  wire [3:0]  w_wstrb,
    output reg         w_busy,          // high from accept until completion
    output reg  [31:0] w_rdata,
    output reg         w_rdata_valid,   // 1-cyc pulse when read data latched

    // ---- clk_cpu controller side ----
    input  wire        clk_cpu,
    input  wire        reset_n_cpu,
    output reg         c_rd,
    output reg         c_wr,
    output reg  [21:0] c_addr,
    output reg  [31:0] c_wdata,
    output reg  [3:0]  c_wstrb,
    input  wire [31:0] c_rdata,
    input  wire        c_busy,
    input  wire        c_rdata_valid
);

    // ====================================================================
    // clk_74a → clk_cpu : request toggle + payload (2FF), accept logic
    // ====================================================================
    reg        req_toggle_74a;
    reg [21:0] q_addr;
    reg [31:0] q_wdata;
    reg [3:0]  q_wstrb;
    reg        q_iswrite;

    // resp toggle synced back from clk_cpu (3FF)
    reg [2:0]  resp_toggle_74a_sync;
    reg        resp_prev_74a;
    wire       resp_edge_74a = (resp_toggle_74a_sync[2] != resp_prev_74a);

    always @(posedge clk_74a or negedge reset_n_74a) begin
        if (!reset_n_74a) begin
            req_toggle_74a       <= 1'b0;
            q_addr <= 22'd0; q_wdata <= 32'd0; q_wstrb <= 4'd0; q_iswrite <= 1'b0;
            w_busy <= 1'b0; w_rdata <= 32'd0; w_rdata_valid <= 1'b0;
            resp_toggle_74a_sync <= 3'b0; resp_prev_74a <= 1'b0;
        end else begin
            w_rdata_valid        <= 1'b0;  // default: single-cycle pulse
            resp_toggle_74a_sync <= {resp_toggle_74a_sync[1:0], resp_toggle_cpu};

            // Accept a new op only when idle (one outstanding at a time).
            if (!w_busy && (w_rd || w_wr)) begin
                q_addr         <= w_addr;
                q_wdata        <= w_wdata;
                q_wstrb        <= w_wstrb;
                q_iswrite      <= w_wr;
                req_toggle_74a <= ~req_toggle_74a;  // launch
                w_busy         <= 1'b1;
            end

            // Completion edge from clk_cpu: latch rdata, drop busy.
            if (resp_edge_74a) begin
                resp_prev_74a <= resp_toggle_74a_sync[2];
                w_rdata       <= rdata_hold_74a;   // 2FF-synced below
                w_rdata_valid <= 1'b1;             // harmless pulse on writes too
                w_busy        <= 1'b0;
            end
        end
    end

    // ====================================================================
    // clk_cpu side : sync req toggle (3FF) + payload (2FF), drive controller
    // ====================================================================
    reg [2:0]  req_toggle_cpu_sync;
    reg        req_prev_cpu;
    wire       req_edge_cpu = (req_toggle_cpu_sync[2] != req_prev_cpu);

    reg [21:0] addr_sync1, addr_sync2;
    reg [31:0] wdata_sync1, wdata_sync2;
    reg [3:0]  wstrb_sync1, wstrb_sync2;
    reg        iswr_sync1,  iswr_sync2;

    reg        resp_toggle_cpu;
    reg [31:0] rdata_hold_cpu;
    reg        c_busy_seen;

    localparam S_IDLE = 2'd0;
    localparam S_WAIT = 2'd1;
    reg [1:0]  c_state;

    always @(posedge clk_cpu or negedge reset_n_cpu) begin
        if (!reset_n_cpu) begin
            req_toggle_cpu_sync <= 3'b0; req_prev_cpu <= 1'b0;
            addr_sync1<=0; addr_sync2<=0; wdata_sync1<=0; wdata_sync2<=0;
            wstrb_sync1<=0; wstrb_sync2<=0; iswr_sync1<=0; iswr_sync2<=0;
            resp_toggle_cpu <= 1'b0; rdata_hold_cpu <= 32'd0;
            c_rd<=1'b0; c_wr<=1'b0; c_addr<=22'd0; c_wdata<=32'd0; c_wstrb<=4'd0;
            c_busy_seen <= 1'b0; c_state <= S_IDLE;
        end else begin
            req_toggle_cpu_sync <= {req_toggle_cpu_sync[1:0], req_toggle_74a};
            addr_sync1<=q_addr;   addr_sync2<=addr_sync1;
            wdata_sync1<=q_wdata; wdata_sync2<=wdata_sync1;
            wstrb_sync1<=q_wstrb; wstrb_sync2<=wstrb_sync1;
            iswr_sync1<=q_iswrite;iswr_sync2<=iswr_sync1;

            c_rd <= 1'b0;  // single-cycle pulses
            c_wr <= 1'b0;

            // capture read data whenever the controller flags it valid for the
            // op in flight (between dispatch and completion)
            if (c_state == S_WAIT && c_rdata_valid)
                rdata_hold_cpu <= c_rdata;

            case (c_state)
            S_IDLE: begin
                if (req_edge_cpu && !c_busy) begin
                    req_prev_cpu <= req_toggle_cpu_sync[2];
                    c_addr  <= addr_sync2;
                    c_wdata <= wdata_sync2;
                    c_wstrb <= wstrb_sync2;
                    c_rd    <= ~iswr_sync2;
                    c_wr    <=  iswr_sync2;
                    c_busy_seen <= 1'b0;
                    c_state <= S_WAIT;
                end
            end
            S_WAIT: begin
                // saw-busy gate: observe busy HIGH before allowing completion,
                // so the dispatch->busy gap can't false-complete.
                if (c_busy)
                    c_busy_seen <= 1'b1;
                else if (c_busy_seen) begin
                    resp_toggle_cpu <= ~resp_toggle_cpu;  // signal done to clk_74a
                    c_state <= S_IDLE;
                end
            end
            default: c_state <= S_IDLE;
            endcase
        end
    end

    // 2FF sync of the held rdata back to clk_74a (stable from resp toggle).
    reg [31:0] rdata_sync1, rdata_hold_74a;
    always @(posedge clk_74a or negedge reset_n_74a) begin
        if (!reset_n_74a) begin
            rdata_sync1 <= 32'd0; rdata_hold_74a <= 32'd0;
        end else begin
            rdata_sync1    <= rdata_hold_cpu;
            rdata_hold_74a <= rdata_sync1;
        end
    end

endmodule

`default_nettype wire
