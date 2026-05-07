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
    input  wire [31:0] data,
    input  wire        wrreq,
    output wire [31:0] q,
    input  wire        rdreq,
    output wire        rdempty,
    output wire  [9:0] wrusedw,
    output wire        wrfull,
    input  wire        aclr
);
    reg [31:0] mem [0:1023];
    reg [10:0] wr_ptr = 11'd0;  /* MSB = wrap bit */
    reg [10:0] rd_ptr = 11'd0;
    reg [31:0] q_reg  = 32'd0;

    wire [10:0] diff = wr_ptr - rd_ptr;

    assign wrusedw = diff[9:0];
    assign wrfull  = (diff == 11'd1024);
    assign rdempty = (wr_ptr == rd_ptr);
    assign q       = (lpm_showahead == "ON" && !rdempty) ? mem[rd_ptr[9:0]] : q_reg;

    always @(posedge wrclk) begin
        if (aclr) begin
            wr_ptr <= 11'd0;
        end else if (wrreq && !wrfull) begin
            mem[wr_ptr[9:0]] <= data;
            wr_ptr <= wr_ptr + 11'd1;
        end
    end

    always @(posedge rdclk) begin
        if (aclr) begin
            rd_ptr <= 11'd0;
            q_reg  <= 32'd0;
        end else if (rdreq && !rdempty) begin
            q_reg  <= mem[rd_ptr[9:0]];
            rd_ptr <= rd_ptr + 11'd1;
        end
    end
endmodule
