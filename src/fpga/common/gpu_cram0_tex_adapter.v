//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------
//
// gpu_cram0_tex_adapter — lets the GPU texture cache fill its lines from CRAM0.
//
// gpu_tex_cache issues an AXI4 read burst per cache-line miss (arlen=LINE_WORDS-1
// = 3, a line-aligned 26-bit byte address, no rready — it accepts every beat).
// This adapter answers that burst by performing LINE_WORDS sequential ASYNC
// single-word reads on the CRAM0 controller's word interface and re-presenting
// them as 4 R-beats (rlast on the last).  gpu_tex_cache needs ZERO edits.
//
// We DO NOT use the controller's sync-burst path: that requires BCR=0x641F sync
// mode which breaks ALL async CRAM0 reads system-wide (project_cram_async_in_sync_bcr).
// Each line word is an independent async transaction (~6-7 clk_cpu cycles).
//
// Runs on clk_cpu — same domain as both the GPU (core_top gpu_core) and the
// re-clocked CRAM0 controller — so NO clock crossing.  The word request is muxed
// against the bridge/CPU path by a downstream arbiter (word_rd held until
// word_accept; saw-busy gate before each completion, feedback_burst_saw_busy_gate).
//
// Address: araddr is a line-aligned 26-bit BYTE address whose low 24 bits index
// the CRAM0 chip (16 MB).  CRAM0 word address = araddr[23:2] + beat.  The texture
// region's placement (HIGH window) is handled upstream by the GPU tex decode +
// firmware upload, so araddr already lands in the CRAM0 texture range.
//------------------------------------------------------------------------------

`default_nettype none

module gpu_cram0_tex_adapter (
    input  wire        clk,            // clk_cpu
    input  wire        reset_n,

    // ---- AXI4 read SLAVE (from gpu_tex_cache fill master) ----
    input  wire        arvalid,
    output reg         arready,
    input  wire [31:0] araddr,         // line-aligned byte address
    input  wire [7:0]  arlen,          // beats-1 (=3 for a 4-word line)
    output reg         rvalid,
    output reg  [31:0] rdata,
    output reg         rlast,
    // (gpu_tex_cache has no rready — it captures every rvalid beat)

    // ---- CRAM0 word request (to the controller arbiter) ----
    output reg         word_rd,        // request (held until word_accept)
    output reg  [21:0] word_addr,
    input  wire        word_accept,    // arbiter forwarded our word_rd this cycle
    input  wire [31:0] word_q,
    input  wire        word_busy,
    input  wire        word_q_valid
);

    localparam S_IDLE  = 2'd0;
    localparam S_REQ   = 2'd1;  // assert word_rd, wait for arbiter accept
    localparam S_WAIT  = 2'd2;  // wait this op's word_q_valid (saw-busy gated)
    localparam S_DRAIN = 2'd3;  // wait word_busy low before next word

    reg [1:0]  state;
    reg [21:0] base;            // CRAM0 word address of beat 0
    reg [7:0]  len;             // latched arlen
    reg [7:0]  beat;            // 0..len
    reg        busy_seen;

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            state     <= S_IDLE;
            arready   <= 1'b0;
            rvalid    <= 1'b0; rdata <= 32'd0; rlast <= 1'b0;
            word_rd   <= 1'b0; word_addr <= 22'd0;
            base      <= 22'd0; len <= 8'd0; beat <= 8'd0; busy_seen <= 1'b0;
        end else begin
            arready <= 1'b0;   // single-cycle accept
            rvalid  <= 1'b0;   // single-cycle beat
            rlast   <= 1'b0;

            case (state)
            S_IDLE: begin
                word_rd <= 1'b0;
                if (arvalid) begin
                    base    <= araddr[23:2];   // byte->word, low 24 bits = CRAM0
                    len     <= arlen;
                    beat    <= 8'd0;
                    arready <= 1'b1;
                    state   <= S_REQ;
                end
            end

            S_REQ: begin
                word_rd   <= 1'b1;
                word_addr <= base + {14'b0, beat};
                busy_seen <= 1'b0;
                if (word_accept) begin
                    word_rd <= 1'b0;
                    state   <= S_WAIT;
                end
            end

            S_WAIT: begin
                // saw-busy gate: only this op's word_q_valid counts.
                if (word_busy)
                    busy_seen <= 1'b1;
                if ((busy_seen || word_busy) && word_q_valid) begin
                    rvalid <= 1'b1;
                    rdata  <= word_q;          // direct capture — no stale latch
                    rlast  <= (beat == len);
                    state  <= S_DRAIN;
                end
            end

            S_DRAIN: begin
                // let the controller finish (busy low) before the next word.
                if (!word_busy) begin
                    if (beat == len)
                        state <= S_IDLE;
                    else begin
                        beat  <= beat + 8'd1;
                        state <= S_REQ;
                    end
                end
            end

            default: state <= S_IDLE;
            endcase
        end
    end

endmodule

`default_nettype wire
