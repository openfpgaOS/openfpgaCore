//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------
//
// cram0_arb — 2-client per-transaction arbiter for the CRAM0 controller's single
// word interface.  Lets the GPU texture adapter share CRAM0 with the existing
// CPU/bridge path WITHOUT modifying cram0_word_cdc and WITHOUT gating either out
// (feedback_no_gating).  Round-robin so neither starves.  All clk_cpu.
//
//   client 0 = cram0_word_cdc (CPU/bridge):  PULSE request (c0_rd/c0_wr one cyc),
//              served by a gated busy-back (c0_busy held from pulse until done) so
//              the CDC's existing pulse+saw-busy FSM needs no change.
//   client 1 = gpu_cram0_tex_adapter:         HELD request (c1_rd) + c1_accept pulse.
//
// The controller serializes one op at a time, so only the current owner ever
// inspects ctrl_busy/ctrl_q_valid — we route those by owner; the other client's
// busy/valid read 0 until it is granted.  Saw-busy gate (observe ctrl_busy HIGH
// before completing) protects the dispatch->busy gap (feedback_burst_saw_busy_gate).
//------------------------------------------------------------------------------

`default_nettype none

module cram0_arb (
    input  wire        clk,
    input  wire        reset_n,

    // ---- client 0: CPU/bridge (cram0_word_cdc) — pulse request, busy-back ----
    input  wire        c0_rd,
    input  wire        c0_wr,
    input  wire [21:0] c0_addr,
    input  wire [31:0] c0_wdata,
    input  wire [3:0]  c0_wstrb,
    output wire        c0_busy,
    output wire [31:0] c0_rdata,
    output wire        c0_rdata_valid,

    // ---- client 1: GPU tex adapter — held request, accept ----
    input  wire        c1_rd,
    input  wire [21:0] c1_addr,
    output reg         c1_accept,
    output wire        c1_busy,
    output wire [31:0] c1_q,
    output wire        c1_q_valid,

    // ---- CRAM0 controller word interface ----
    output reg         word_rd,
    output reg         word_wr,
    output reg  [21:0] word_addr,
    output reg  [31:0] word_wdata,
    output reg  [3:0]  word_wstrb,
    input  wire [31:0] ctrl_q,
    input  wire        ctrl_busy,
    input  wire        ctrl_q_valid
);
    localparam OWN_NONE = 2'd0;
    localparam OWN_C0   = 2'd1;
    localparam OWN_C1   = 2'd2;

    // client 0 request latch (its c0_rd/c0_wr is a single-cycle pulse)
    reg        p0;            // CDC op pending or in flight
    reg        p0_wr;
    reg [21:0] p0_addr;
    reg [31:0] p0_wdata;
    reg [3:0]  p0_wstrb;

    reg [1:0]  owner;
    reg        busy_seen;
    reg        rr;            // round-robin: 0 -> prefer c0, 1 -> prefer c1

    // gated responses
    assign c0_busy        = p0;                                  // pulse..done
    assign c0_rdata       = ctrl_q;
    assign c0_rdata_valid = (owner == OWN_C0) ? ctrl_q_valid : 1'b0;
    assign c1_busy        = (owner == OWN_C1) ? ctrl_busy   : 1'b0;
    assign c1_q           = ctrl_q;
    assign c1_q_valid     = (owner == OWN_C1) ? ctrl_q_valid : 1'b0;

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            p0 <= 1'b0; p0_wr <= 1'b0; p0_addr <= 22'd0; p0_wdata <= 32'd0; p0_wstrb <= 4'd0;
            owner <= OWN_NONE; busy_seen <= 1'b0; rr <= 1'b0; c1_accept <= 1'b0;
            word_rd <= 1'b0; word_wr <= 1'b0; word_addr <= 22'd0; word_wdata <= 32'd0; word_wstrb <= 4'd0;
        end else begin
            word_rd   <= 1'b0;   // single-cycle controller dispatch pulses
            word_wr   <= 1'b0;
            c1_accept <= 1'b0;

            // latch the CDC's single-cycle request pulse
            if ((c0_rd || c0_wr) && !p0) begin
                p0      <= 1'b1;
                p0_wr   <= c0_wr;
                p0_addr <= c0_addr;
                p0_wdata<= c0_wdata;
                p0_wstrb<= c0_wstrb;
            end

            case (owner)
            OWN_NONE: if (!ctrl_busy) begin
                // round-robin grant; never starve either client
                if (p0 && (rr == 1'b0 || !c1_rd)) begin
                    word_rd    <= ~p0_wr;
                    word_wr    <=  p0_wr;
                    word_addr  <=  p0_addr;
                    word_wdata <=  p0_wdata;
                    word_wstrb <=  p0_wstrb;
                    owner      <= OWN_C0;
                    busy_seen  <= 1'b0;
                    rr         <= 1'b1;
                end else if (c1_rd && (rr == 1'b1 || !p0)) begin
                    word_rd    <= 1'b1;     // GPU tex is read-only
                    word_addr  <= c1_addr;
                    word_wstrb <= 4'b1111;
                    c1_accept  <= 1'b1;
                    owner      <= OWN_C1;
                    busy_seen  <= 1'b0;
                    rr         <= 1'b0;
                end
            end

            default: begin  // OWN_C0 / OWN_C1: run the op to completion
                if (ctrl_busy)
                    busy_seen <= 1'b1;
                else if (busy_seen) begin
                    if (owner == OWN_C0) p0 <= 1'b0;  // release CDC's gated busy
                    owner     <= OWN_NONE;
                    busy_seen <= 1'b0;
                end
            end
            endcase
        end
    end

endmodule

`default_nettype wire
