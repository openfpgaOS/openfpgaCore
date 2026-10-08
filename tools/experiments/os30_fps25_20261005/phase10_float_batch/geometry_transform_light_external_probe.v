// Protocol checker wrapper: export the FMA in mode1, retain it inside in mode0.
module geometry_transform_light_external_probe #(parameter EXTERNAL_FMA=1)(
 input wire clk,reset,
 input wire matrix_we,input wire [3:0] matrix_addr,input wire [31:0] matrix_data,
 input wire position_we,input wire [6:0] position_addr,input wire [31:0] position_data,
 input wire normal_we,input wire [4:0] normal_addr,input wire [23:0] normal_data,
 input wire start,input wire [5:0] count,input wire [4:0] dest,input wire [31:0] aspect,
 input wire [31:0] coefficient_x,coefficient_y,coefficient_z,
 input wire [23:0] ambient_rgb,diffuse_rgb,input wire lighting_enabled,
 input wire [7:0] output_addr,output wire [31:0] output_data,output wire [23:0] output_rgb,
 output wire busy,done,unsupported,output wire [31:0] cycles,
 output wire [31:0] transform_cycles,lighting_cycles,
 input wire scratch_read_valid,input wire [6:0] scratch_read_addr,
 output wire [31:0] scratch_read_data,
 input wire scratch_write,input wire [6:0] scratch_write_addr,input wire [31:0] scratch_write_data,
 output reg [15:0] operation_count,output reg owner_collision
);
 wire fma_request_valid,fma_request_multiply,fma_response_valid,fma_response_unsupported;
 wire [31:0] fma_request_a,fma_request_b,fma_request_c,fma_response_data;
 wire attribute_read_valid,attribute_rgb_write;
 wire [4:0] attribute_read_addr,attribute_rgb_addr;
 wire [23:0] attribute_read_data=0;
 wire [1:0] attribute_rgb_channel;
 wire [7:0] attribute_rgb_data;
 geometry_transform_light #(.EXTERNAL_FMA(EXTERNAL_FMA)) dut(.*);
 GeometryFmaProbe fma(.clk(clk),.reset(reset),.io_valid(fma_request_valid),
  .io_a(fma_request_a),.io_b(fma_request_b),.io_c(fma_request_c),
  .io_multiplyOnly(fma_request_multiply),.io_result(fma_response_data),
  .io_resultValid(fma_response_valid),.io_unsupported(fma_response_unsupported));
 always @(posedge clk)begin
  if(reset)begin operation_count<=0;owner_collision<=0;end
  else if(start&&!busy)begin operation_count<=0;owner_collision<=0;end
  else begin
   if(fma_request_valid)operation_count<=operation_count+1'b1;
   if(dut.t_valid&&dut.l_valid)owner_collision<=1;
  end
 end
endmodule
