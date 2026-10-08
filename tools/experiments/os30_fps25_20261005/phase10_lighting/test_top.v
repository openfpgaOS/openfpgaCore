module lighting_test_top(
 input wire clk,reset,start,input wire [5:0] count,
 input wire normal_we,input wire [4:0] normal_addr,input wire [23:0] normal_word,
 input wire [31:0] coefficient_x,coefficient_y,coefficient_z,
 input wire [23:0] ambient_rgb,diffuse_rgb,
 input wire [4:0] output_addr,output reg [23:0] output_rgb,
 output wire busy,done,unsupported,output wire [31:0] cycles,
 output reg [15:0] write_count
);
 reg [23:0] normals[0:31],rgb[0:31];reg [31:0] scratch[0:31];
 wire work_read_valid;wire [4:0] work_read_vertex;reg [23:0] normal_data;reg [31:0] scratch_data;
 wire scratch_write;wire[4:0]scratch_write_vertex;wire[31:0]scratch_write_data;
 wire rgb_write;wire[4:0]rgb_write_vertex;wire[1:0]rgb_write_channel;wire[7:0]rgb_write_data;
 wire fma_valid,fma_multiply_only,fma_result_valid,fma_unsupported;wire[31:0]fma_a,fma_b,fma_c,fma_result;
 wire fixed_valid,fixed_result_valid,fixed_unsupported;wire[31:0]fixed_value,fixed_result;
 geometry_lighting_batch light(.*);
 GeometryFmaProbe fma(.clk(clk),.reset(reset),.io_valid(fma_valid),.io_a(fma_a),.io_b(fma_b),.io_c(fma_c),.io_multiplyOnly(fma_multiply_only),.io_result(fma_result),.io_resultValid(fma_result_valid),.io_unsupported(fma_unsupported));
 geometry_float_fixed_pipe fixed_unit(.clk(clk),.reset(reset),.in_valid(fixed_valid),.value(fixed_value),.scale(6'd0),.truncate(1'b1),.depth_mode(1'b0),.out_valid(fixed_result_valid),.fixed_value(fixed_result),.unsupported(fixed_unsupported));
 always @(posedge clk)begin
  if(normal_we&&!busy)normals[normal_addr]<=normal_word;
  if(work_read_valid)begin normal_data<=normals[work_read_vertex];scratch_data<=scratch[work_read_vertex];end
  if(scratch_write)scratch[scratch_write_vertex]<=scratch_write_data;
  if(rgb_write)begin
   case(rgb_write_channel)
    0:rgb[rgb_write_vertex][7:0]<=rgb_write_data;
    1:rgb[rgb_write_vertex][15:8]<=rgb_write_data;
    2:rgb[rgb_write_vertex][23:16]<=rgb_write_data;
    default:rgb[rgb_write_vertex]<=24'hbadbad;
   endcase
  end
  output_rgb<=rgb[output_addr];
  if(reset||start)write_count<=0;else if(rgb_write)write_count<=write_count+1'b1;
 end
endmodule
