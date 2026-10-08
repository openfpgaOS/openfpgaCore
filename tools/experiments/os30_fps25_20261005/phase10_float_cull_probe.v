// Standalone measurement wrapper; production integration shares one existing
// arithmetic port, so the FMA below must not be duplicated in an integrated GPU.
module geometry_float_cull_probe(
 input wire clk,reset,input wire in_valid,output wire in_ready,
 input wire [287:0] vertex_data,input wire [17:0] clip_bits,
 input wire [2:0] negative_w,input wire [1:0] cull_mode,
 output wire out_valid,input wire out_ready,output wire [1:0] reject_class,
 output wire [5:0] clip_or,output wire [31:0] cross_bits,output wire unsupported
);
 wire fma_valid,fma_multiply_only,fma_result_valid,fma_unsupported;
 wire [31:0] fma_a,fma_b,fma_c,fma_result;
 geometry_float_cull cull(.*);
 GeometryFmaProbe fma(.clk(clk),.reset(reset),.io_valid(fma_valid),
  .io_a(fma_a),.io_b(fma_b),.io_c(fma_c),.io_multiplyOnly(fma_multiply_only),
  .io_resultValid(fma_result_valid),.io_result(fma_result),.io_unsupported(fma_unsupported));
endmodule
