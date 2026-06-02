//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * Verilator-friendly behavioural stub for Altera's `dcfifo` megafunction.
 *
 * Matches the interface used by audio_output.v:
 *   - 32-bit data, 1024-deep
 *   - separate wrclk / rdclk domains
 *   - wrusedw[9:0] fill level on the write side
 *   - rdempty / wrfull flags
 *   - synchronous reset via aclr (active high)
 *
 * The stub is a plain single-port RAM-backed ring with two pointers.
 * No CDC synchroniser chain: it's deliberately simpler than the real
 * megafunction so tests stay deterministic — good enough to exercise
 * the sample-write → I2S drain path in audio_output.v.
 */

`default_nettype none

module dcfifo #(
    parameter intended_device_family = "Cyclone V",
    parameter lpm_numwords           = 1024,
    parameter lpm_showahead          = "OFF",
    parameter lpm_type               = "dcfifo",
    parameter lpm_width              = 32,
    parameter lpm_widthu             = 10,
    parameter overflow_checking      = "ON",
    parameter underflow_checking     = "ON",
    parameter rdsync_delaypipe       = 5,
    parameter wrsync_delaypipe       = 5,
    parameter use_eab                = "ON"
) (
    input  wire        wrclk,
    input  wire        rdclk,
    input  wire [lpm_width-1:0] data,
    input  wire        wrreq,
    output wire [lpm_width-1:0] q,
    input  wire        rdreq,
    output wire        rdempty,
    output wire [lpm_widthu-1:0] wrusedw,
    output wire        wrfull,
    input  wire        aclr
);
    reg [lpm_width-1:0] mem [0:lpm_numwords-1];
    reg [lpm_widthu:0] wr_ptr = {(lpm_widthu+1){1'b0}};  /* MSB = wrap bit */
    reg [lpm_widthu:0] rd_ptr = {(lpm_widthu+1){1'b0}};
    reg [lpm_width-1:0] q_reg  = {lpm_width{1'b0}};

    localparam [lpm_widthu:0] DEPTH_COUNT = lpm_numwords;
    wire [lpm_widthu:0] diff = wr_ptr - rd_ptr;

    assign wrusedw = diff[lpm_widthu-1:0];
    assign wrfull  = (diff == DEPTH_COUNT);
    assign rdempty = (wr_ptr == rd_ptr);
    assign q       = (lpm_showahead == "ON" && !rdempty) ? mem[rd_ptr[lpm_widthu-1:0]] : q_reg;

    always @(posedge wrclk) begin
        if (aclr) begin
            wr_ptr <= {(lpm_widthu+1){1'b0}};
        end else if (wrreq && !wrfull) begin
            mem[wr_ptr[lpm_widthu-1:0]] <= data;
            wr_ptr <= wr_ptr + {{lpm_widthu{1'b0}}, 1'b1};
        end
    end

    always @(posedge rdclk) begin
        if (aclr) begin
            rd_ptr <= {(lpm_widthu+1){1'b0}};
            q_reg  <= {lpm_width{1'b0}};
        end else if (rdreq && !rdempty) begin
            q_reg  <= mem[rd_ptr[lpm_widthu-1:0]];
            rd_ptr <= rd_ptr + {{lpm_widthu{1'b0}}, 1'b1};
        end
    end
endmodule
