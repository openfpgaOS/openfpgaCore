//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------
//
// gpu_cram1_tex_adapter — lets the GPU texture cache fill its lines from CRAM1
// using the chip's SYNC-BURST engine (BCR 0x641F).  Unlike the CRAM0 async
// path, CRAM1 is dedicated to textures, so it can run sync-burst (the mode that
// hangs CRAM0's shared async/save reads).  A 16-byte line fill = one burst.
//
// gpu_tex_cache issues an AXI4 read burst per miss (arlen = LINE_WORDS-1 = 3,
// line-aligned 26-bit byte address, no rready).  This adapter answers it with a
// single cram1_controller burst_rd of (arlen) words and forwards each assembled
// 32-bit word (burst_q / burst_q_valid) straight back as an R-beat, rlast on the
// last.  gpu_tex_cache needs ZERO edits.
//
// Runs on clk_cpu — same domain as the GPU and the cram1_controller — so NO
// clock crossing.  CRAM1 word address = araddr[23:2] (low 24 bits = the 16 MB
// chip); bit 21 selects the die inside the controller.
//------------------------------------------------------------------------------

`default_nettype none

module gpu_cram1_tex_adapter (
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

    // ---- CRAM1 sync-burst read master (to cram1_controller) ----
    output reg         burst_rd,       // single-cycle request pulse
    output reg  [21:0] burst_addr,     // 22-bit word base
    output reg  [4:0]  burst_len,      // words-1 (max 31)
    input  wire [31:0] burst_q,
    input  wire        burst_q_valid,  // pulses once per assembled 32-bit word
    input  wire        burst_busy      // HIGH for the whole burst
);

    localparam S_IDLE  = 2'd0;
    localparam S_FILL  = 2'd1;  // forward burst_q_valid words as R-beats
    localparam S_DRAIN = 2'd2;  // wait burst_busy low before next burst

    reg [1:0] state;
    reg [4:0] len;              // latched arlen[4:0]
    reg [4:0] beat;            // 0..len

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            state     <= S_IDLE;
            arready   <= 1'b0;
            rvalid    <= 1'b0; rdata <= 32'd0; rlast <= 1'b0;
            burst_rd  <= 1'b0; burst_addr <= 22'd0; burst_len <= 5'd0;
            len       <= 5'd0; beat <= 5'd0;
        end else begin
            arready  <= 1'b0;   // single-cycle accept
            rvalid   <= 1'b0;   // single-cycle beat
            rlast    <= 1'b0;
            burst_rd <= 1'b0;   // single-cycle controller pulse

            case (state)
            S_IDLE: begin
                if (arvalid && !burst_busy) begin
                    burst_addr <= araddr[23:2];   // byte->word, low 24 bits
                    burst_len  <= arlen[4:0];
                    len        <= arlen[4:0];
                    beat       <= 5'd0;
                    burst_rd   <= 1'b1;           // kick the burst
                    arready    <= 1'b1;
                    state      <= S_FILL;
                end
            end

            S_FILL: begin
                if (burst_q_valid) begin
                    rvalid <= 1'b1;
                    rdata  <= burst_q;
                    rlast  <= (beat == len);
                    if (beat == len)
                        state <= S_DRAIN;
                    else
                        beat  <= beat + 5'd1;
                end
            end

            S_DRAIN: begin
                if (!burst_busy)
                    state <= S_IDLE;
            end

            default: state <= S_IDLE;
            endcase
        end
    end

endmodule

`default_nettype wire
