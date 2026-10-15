// Private unified transform/light scheduler; unchanged LOD5 arithmetic/ABI.
module geometry_transform_light #(parameter CAPTURE_CONTEXT=1,parameter EXTERNAL_FMA=0,
 parameter EXTERNAL_ATTRIBUTES=0,parameter CONTEXT_BRAM=1) (
 input wire clk,reset,
 input wire matrix_we,input wire [3:0] matrix_addr,input wire [31:0] matrix_data,
 input wire position_we,input wire [6:0] position_addr,input wire [31:0] position_data,
 input wire normal_we,input wire [4:0] normal_addr,input wire [23:0] normal_data,
 input wire start,input wire [5:0] count,input wire [4:0] dest,input wire [31:0] aspect,
 input wire [31:0] coefficient_x,coefficient_y,coefficient_z,
 input wire [23:0] ambient_rgb,diffuse_rgb,input wire lighting_enabled,
 input wire [7:0] output_addr,output wire [31:0] output_data,output wire [31:0] output_position_data,output wire [23:0] output_rgb,
 output reg busy,done,unsupported,output reg [31:0] cycles,
 output wire [31:0] transform_cycles,lighting_cycles,
 output wire fma_request_valid,output wire [31:0] fma_request_a,
 output wire [31:0] fma_request_b,fma_request_c,output wire fma_request_multiply,
 input wire fma_response_valid,input wire [31:0] fma_response_data,
 input wire fma_response_unsupported,
 input wire scratch_read_valid,input wire [6:0] scratch_read_addr,
 output wire [31:0] scratch_read_data,
 input wire scratch_write,input wire [6:0] scratch_write_addr,
 input wire [31:0] scratch_write_data,
 // Optional shared attribute RAM: normal reads are one cycle, RGB writes use
 // global vertex addresses. All normals are consumed before the first RGB
 // write, so packed source normals and resulting RGB may share one word.
 output wire attribute_read_valid,output wire [4:0] attribute_read_addr,
 input wire [23:0] attribute_read_data,
 output wire attribute_rgb_write,output wire [4:0] attribute_rgb_addr,
 output wire [1:0] attribute_rgb_channel,output wire [7:0] attribute_rgb_data
);

 reg [31:0] words[0:255],q;reg test_unsupported;reg [1:0] latency;
 assign output_data=q,output_position_data=q,output_rgb=0;
 assign transform_cycles=0,lighting_cycles=0;
 assign fma_request_valid=0,fma_request_a=0,fma_request_b=0,fma_request_c=0,fma_request_multiply=0;
 assign scratch_read_data=0;
 assign attribute_read_valid=0,attribute_read_addr=0,attribute_rgb_write=0,attribute_rgb_addr=0,attribute_rgb_channel=0,attribute_rgb_data=0;
 always @(posedge clk)begin
  q<=words[output_addr];done<=0;
  if(reset)begin busy<=0;unsupported<=0;latency<=0;cycles<=0;end
  else if(start)begin busy<=1;unsupported<=0;latency<=2;end
  else if(busy)begin
   if(latency==0)begin busy<=0;done<=1;unsupported<=test_unsupported;end
   else latency<=latency-1'b1;
  end
 end
endmodule
