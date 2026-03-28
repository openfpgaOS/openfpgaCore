//
// Behavioral Async FIFO for Verilator CDC Testing
//
// Gray-coded pointer synchronization between clock domains.
// Show-ahead mode: rd_data is valid whenever !rd_empty.
//

`default_nettype none

module async_fifo #(
    parameter WIDTH = 56,
    parameter DEPTH_LOG2 = 4   // 16 entries
)(
    // Write port
    input  wire             wr_clk,
    input  wire             wr_en,
    input  wire [WIDTH-1:0] wr_data,
    output wire             wr_full,

    // Read port (show-ahead)
    input  wire             rd_clk,
    input  wire             rd_en,
    output wire [WIDTH-1:0] rd_data,
    output wire             rd_empty,

    input  wire             aclr
);

localparam DEPTH = 1 << DEPTH_LOG2;

// Memory
reg [WIDTH-1:0] mem [0:DEPTH-1];

// Write-side pointers (binary and gray)
reg [DEPTH_LOG2:0] wr_ptr_bin;
reg [DEPTH_LOG2:0] wr_ptr_gray;
wire [DEPTH_LOG2:0] wr_ptr_bin_next = wr_ptr_bin + (wr_en && !wr_full);

// Read-side pointers (binary and gray)
reg [DEPTH_LOG2:0] rd_ptr_bin;
reg [DEPTH_LOG2:0] rd_ptr_gray;
wire [DEPTH_LOG2:0] rd_ptr_bin_next = rd_ptr_bin + (rd_en && !rd_empty);

// Synchronized pointers (2-stage synchronizers)
reg [DEPTH_LOG2:0] wr_ptr_gray_rd1, wr_ptr_gray_rd2;  // wr→rd sync
reg [DEPTH_LOG2:0] rd_ptr_gray_wr1, rd_ptr_gray_wr2;  // rd→wr sync

// Binary-to-gray conversion
function [DEPTH_LOG2:0] bin2gray;
    input [DEPTH_LOG2:0] b;
    bin2gray = b ^ (b >> 1);
endfunction

// Full and empty detection
assign wr_full = (wr_ptr_gray == {~rd_ptr_gray_wr2[DEPTH_LOG2:DEPTH_LOG2-1],
                                    rd_ptr_gray_wr2[DEPTH_LOG2-2:0]});
assign rd_empty = (rd_ptr_gray == wr_ptr_gray_rd2);

// Show-ahead: output data at current read pointer
assign rd_data = mem[rd_ptr_bin[DEPTH_LOG2-1:0]];

// Write logic
always @(posedge wr_clk or posedge aclr) begin
    if (aclr) begin
        wr_ptr_bin <= 0;
        wr_ptr_gray <= 0;
    end else begin
        if (wr_en && !wr_full) begin
            mem[wr_ptr_bin[DEPTH_LOG2-1:0]] <= wr_data;
            wr_ptr_bin <= wr_ptr_bin_next;
            wr_ptr_gray <= bin2gray(wr_ptr_bin_next);
        end
    end
end

// Read logic
always @(posedge rd_clk or posedge aclr) begin
    if (aclr) begin
        rd_ptr_bin <= 0;
        rd_ptr_gray <= 0;
    end else begin
        if (rd_en && !rd_empty) begin
            rd_ptr_bin <= rd_ptr_bin_next;
            rd_ptr_gray <= bin2gray(rd_ptr_bin_next);
        end
    end
end

// Write→Read synchronizer
always @(posedge rd_clk or posedge aclr) begin
    if (aclr) begin
        wr_ptr_gray_rd1 <= 0;
        wr_ptr_gray_rd2 <= 0;
    end else begin
        wr_ptr_gray_rd1 <= wr_ptr_gray;
        wr_ptr_gray_rd2 <= wr_ptr_gray_rd1;
    end
end

// Read→Write synchronizer
always @(posedge wr_clk or posedge aclr) begin
    if (aclr) begin
        rd_ptr_gray_wr1 <= 0;
        rd_ptr_gray_wr2 <= 0;
    end else begin
        rd_ptr_gray_wr1 <= rd_ptr_gray;
        rd_ptr_gray_wr2 <= rd_ptr_gray_wr1;
    end
end

endmodule
