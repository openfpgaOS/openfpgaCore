//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

// BRAM-backed replacement for the Pocket's external async SRAM
// (sram_controller.v).  The GPU's only SRAM client is the 32 KB
// translucency LUT, so a DE10-Nano M10K block RAM serves the same
// word interface: busy while an access is in flight, word_q_valid
// pulses once with read data, half reads land in the matching half
// of word_q ([15:0] for lo, [31:16] for hi) exactly like the chip
// controller.  Writes honor per-byte strobes.
//
// Four 8-bit lanes, each with a single full-width write port, so
// Quartus infers clean byte-enabled M10K (no FF fallback).

`default_nettype none

module sram_bram #(
    parameter WORDS = 8192              // 8192 x 32 = 32 KB
) (
    input  wire        clk,
    input  wire        reset_n,

    input  wire        word_rd,
    input  wire        word_wr,
    input  wire        word_rd_half,
    input  wire        word_rd_hi,
    input  wire [21:0] word_addr,
    input  wire [31:0] word_data,
    input  wire [3:0]  word_wstrb,
    output reg  [31:0] word_q,
    output reg         word_busy,
    output reg         word_q_valid
);

localparam AW = $clog2(WORDS);

reg [7:0] lane0 [0:WORDS-1];
reg [7:0] lane1 [0:WORDS-1];
reg [7:0] lane2 [0:WORDS-1];
reg [7:0] lane3 [0:WORDS-1];

localparam ST_IDLE   = 2'd0;
localparam ST_SAMPLE = 2'd1;
localparam ST_DONE   = 2'd2;

reg [1:0] st;
reg       lat_rd_half, lat_rd_hi;

wire [AW-1:0] addr = word_addr[AW-1:0];
reg  [7:0] q0, q1, q2, q3;

always @(posedge clk) begin
    // Write port (one full-width write per lane, gated per strobe).
    if (st == ST_IDLE && word_wr) begin
        if (word_wstrb[0]) lane0[addr] <= word_data[7:0];
        if (word_wstrb[1]) lane1[addr] <= word_data[15:8];
        if (word_wstrb[2]) lane2[addr] <= word_data[23:16];
        if (word_wstrb[3]) lane3[addr] <= word_data[31:24];
    end

    // Read port (synchronous; address valid in the accept cycle).
    q0 <= lane0[addr];
    q1 <= lane1[addr];
    q2 <= lane2[addr];
    q3 <= lane3[addr];
end

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        st <= ST_IDLE;
        word_busy <= 1'b0;
        word_q <= 32'd0;
        word_q_valid <= 1'b0;
        lat_rd_half <= 1'b0;
        lat_rd_hi <= 1'b0;
    end else begin
        word_q_valid <= 1'b0;

        case (st)
        ST_IDLE: begin
            word_busy <= 1'b0;
            if (word_wr) begin
                word_busy <= 1'b1;
                st <= ST_DONE;
            end else if (word_rd) begin
                word_busy <= 1'b1;
                lat_rd_half <= word_rd_half;
                lat_rd_hi <= word_rd_hi;
                st <= ST_SAMPLE;
            end
        end

        ST_SAMPLE: begin
            // q* now hold the addressed word.
            if (lat_rd_half && lat_rd_hi)
                word_q <= {q3, q2, 16'd0};
            else if (lat_rd_half)
                word_q <= {16'd0, q1, q0};
            else
                word_q <= {q3, q2, q1, q0};
            word_q_valid <= 1'b1;
            st <= ST_DONE;
        end

        ST_DONE: begin
            word_busy <= 1'b0;
            st <= ST_IDLE;
        end

        default: st <= ST_IDLE;
        endcase
    end
end

endmodule
