//
// Behavioral SRAM Chip Model for Verilator Testing
//
// Models a 128K x 16-bit async SRAM (Analogue Pocket external SRAM).
// No CAS latency - data appears after oe_n goes low (registered for Verilator).
// Writes captured while we_n is low.
//
// Verilator-friendly: separate dq_in/dq_out ports (no inout).
//

`default_nettype none

module sram_chip_model (
    input  wire        clk,
    input  wire [16:0] sram_a,
    input  wire [15:0] sram_dq_in,   // data from controller (writes)
    output reg  [15:0] sram_dq_out,  // data to controller (reads)
    input  wire        sram_oe_n,
    input  wire        sram_we_n,
    input  wire        sram_ub_n,
    input  wire        sram_lb_n
);

// 128K x 16-bit = 256KB
reg [15:0] mem [0:131071];

integer i;
initial begin
    for (i = 0; i < 131072; i = i + 1)
        mem[i] = 16'hDEAD;
end

// Write: capture data while we_n is low (idempotent - data stable during pulse)
always @(posedge clk) begin
    if (!sram_we_n) begin
        if (!sram_lb_n) mem[sram_a][7:0]  <= sram_dq_in[7:0];
        if (!sram_ub_n) mem[sram_a][15:8] <= sram_dq_in[15:8];
    end
end

// Read: output data when oe_n is low (1 cycle registered for Verilator)
always @(posedge clk) begin
    if (!sram_oe_n)
        sram_dq_out <= mem[sram_a];
    else
        sram_dq_out <= 16'h0000;
end

// Verification helper: read a 32-bit word at a halfword-pair address
function [31:0] read_word;
    input [15:0] word_addr;
    begin
        read_word = {mem[{word_addr, 1'b1}], mem[{word_addr, 1'b0}]};
    end
endfunction

endmodule
