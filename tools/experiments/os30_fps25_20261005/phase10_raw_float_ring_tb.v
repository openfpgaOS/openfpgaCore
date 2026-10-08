`default_nettype none
module tb_raw_float_ring (
    input wire clk, reset_n, enable, soft_reset, ring_reset,
    input wire cpu_write,
    input wire [11:0] cpu_address, input_wrptr,
    input wire [31:0] cpu_data,
    output wire [11:0] input_rdptr,
    output wire cmd_valid,
    output wire [31:0] cmd_data,
    input wire cmd_ready,
    input wire result_valid,
    input wire [31:0] result_data,
    output wire result_ready,
    input wire result_commit, result_abort, processor_busy,
    input wire [11:0] output_rdptr, output_addr,
    output wire [31:0] output_data,
    output wire [11:0] output_wrptr,
    output wire busy
);
reg [31:0] input_memory [0:4095];
reg [31:0] input_data;
wire [11:0] input_addr;
always @(posedge clk) begin
    input_data <= input_memory[input_addr];
    if (cpu_write) input_memory[cpu_address] <= cpu_data;
end
geometry_raw_float_ring ring (
    .clk(clk), .reset_n(reset_n), .enable(enable),
    .soft_reset(soft_reset), .ring_reset(ring_reset),
    .input_wrptr(input_wrptr), .input_rdptr(input_rdptr),
    .input_addr(input_addr), .input_data(input_data),
    .cmd_valid(cmd_valid), .cmd_data(cmd_data), .cmd_ready(cmd_ready),
    .result_valid(result_valid), .result_data(result_data),
    .result_ready(result_ready), .result_commit(result_commit),
    .result_abort(result_abort), .processor_busy(processor_busy),
    .output_rdptr(output_rdptr), .output_addr(output_addr),
    .output_data(output_data), .output_wrptr(output_wrptr), .busy(busy)
);
endmodule
`default_nettype wire
