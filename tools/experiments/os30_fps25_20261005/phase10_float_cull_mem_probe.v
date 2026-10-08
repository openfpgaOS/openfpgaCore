// Isolated memory/FMA wrapper. In an integrated frontend both memories and
// the FMA are existing resources; this wrapper measures their combined cost.
module geometry_float_cull_mem_probe(
 input wire clk,reset,input wire in_valid,output wire in_ready,
 input wire vertex_we,input wire [6:0] vertex_write_address,input wire [31:0] vertex_write_data,
 input wire [14:0] vertex_indices,input wire [17:0] clip_bits,
 input wire [2:0] negative_w,input wire [1:0] cull_mode,
 output wire out_valid,input wire out_ready,output wire [1:0] reject_class,
 output wire [5:0] clip_or,output wire [31:0] cross_bits,output wire unsupported,
 output reg [5:0] operation_count,output reg memory_collision
);
 wire vertex_read_valid,scratch_read_valid,scratch_write;
 wire [4:0] vertex_read_index;wire [1:0] vertex_read_field;
 wire [2:0] scratch_read_index,scratch_write_index;wire [31:0] scratch_write_data;
 reg [31:0] vertex_data,scratch_data;
 (* ramstyle="M10K, no_rw_check" *)reg [31:0] vertices[0:95];
 (* ramstyle="M10K, no_rw_check" *)reg [31:0] scratch[0:7];
 wire fma_valid,fma_multiply_only,fma_result_valid,fma_unsupported;
 wire [31:0] fma_a,fma_b,fma_c,fma_result;
 geometry_float_cull_mem cull(.*);
 GeometryFmaProbe fma(.clk(clk),.reset(reset),.io_valid(fma_valid),
  .io_a(fma_a),.io_b(fma_b),.io_c(fma_c),.io_multiplyOnly(fma_multiply_only),
  .io_resultValid(fma_result_valid),.io_result(fma_result),.io_unsupported(fma_unsupported));
 always @(posedge clk)begin
  if(vertex_we&&in_ready)vertices[vertex_write_address]<=vertex_write_data;
  if(vertex_read_valid)vertex_data<=vertices[{vertex_read_field,vertex_read_index}];
  if(scratch_read_valid)scratch_data<=scratch[scratch_read_index];
  if(scratch_write)scratch[scratch_write_index]<=scratch_write_data;
  if(reset)begin operation_count<=0;memory_collision<=0;end
  else if(in_valid&&in_ready)begin operation_count<=0;memory_collision<=0;end
  else begin
   if(fma_valid)operation_count<=operation_count+1'b1;
   if(scratch_write&&scratch_read_valid)memory_collision<=1;
  end
 end
endmodule
