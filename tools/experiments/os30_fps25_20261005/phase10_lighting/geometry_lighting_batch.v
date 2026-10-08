// Private one-directional-light controller. No RAM, FMA or converter instances.
// Caller supplies one-cycle synchronous packed-normal and32-word scratch reads.
// Shared FMA latency: valid at inputcycle0 -> result valid atcycle6.
// Shared fixed conversion latency: four stages, scale0, truncate1, depth_mode0.
// Finite RNE lighting only. Unsupported arithmetic is sticky for CPU fallback.
module geometry_lighting_batch (
 input wire clk,reset,start,input wire [5:0] count,
 input wire [31:0] coefficient_x,coefficient_y,coefficient_z,
 input wire [23:0] ambient_rgb,diffuse_rgb,
 output wire work_read_valid,output wire [4:0] work_read_vertex,
 input wire [23:0] normal_data,input wire [31:0] scratch_data,
 output wire scratch_write,output wire [4:0] scratch_write_vertex,
 output wire [31:0] scratch_write_data,
 output wire rgb_write,output wire [4:0] rgb_write_vertex,
 output wire [1:0] rgb_write_channel,output wire [7:0] rgb_write_data,
 output wire fma_valid,output reg [31:0] fma_a,fma_b,fma_c,
 output reg fma_multiply_only,
 input wire fma_result_valid,input wire [31:0] fma_result,input wire fma_unsupported,
 output wire fixed_valid,output wire [31:0] fixed_value,
 input wire fixed_result_valid,input wire [31:0] fixed_result,input wire fixed_unsupported,
 output reg busy,done,unsupported,output reg [31:0] cycles
);
 function [31:0] unsigned_byte_float;
  input [7:0] v;
  begin
   casez(v)
    8'b1???????:unsigned_byte_float={1'b0,8'd134,v[6:0],16'd0};
    8'b01??????:unsigned_byte_float={1'b0,8'd133,v[5:0],17'd0};
    8'b001?????:unsigned_byte_float={1'b0,8'd132,v[4:0],18'd0};
    8'b0001????:unsigned_byte_float={1'b0,8'd131,v[3:0],19'd0};
    8'b00001???:unsigned_byte_float={1'b0,8'd130,v[2:0],20'd0};
    8'b000001??:unsigned_byte_float={1'b0,8'd129,v[1:0],21'd0};
    8'b0000001?:unsigned_byte_float={1'b0,8'd128,v[0],22'd0};
    8'b00000001:unsigned_byte_float=32'h3f800000;
    default:unsigned_byte_float=0;
   endcase
  end
 endfunction
 function [31:0] signed_byte_float;
  input [7:0] v;
  reg [7:0] mag;reg [31:0] f;
  begin mag=v[7] ? -v:v;f=unsigned_byte_float(mag);signed_byte_float={v[7],f[30:0]};end
 endfunction
 reg [31:0] cx,cy,cz;
 reg [23:0] amb,dif;
 reg [5:0] n;
 reg [4:0] vertex;
 reg [2:0] phase,read_phase;
 reg read_valid,drain;
 reg [4:0] read_vertex;
 reg [7:0] fma_tags[0:6];
 reg [6:0] fixed_tags[0:3];
 reg [4:0] fma_outstanding;
 reg [3:0] fixed_outstanding;
 wire issuing=busy&&!drain;
 assign work_read_valid=issuing;
 assign work_read_vertex=vertex;
 assign fma_valid=read_valid&&busy;
 wire [2:0] return_phase=fma_tags[6][7:5];
 assign scratch_write=busy&&fma_result_valid&&return_phase<4;
 assign scratch_write_vertex=fma_tags[6][4:0];
 assign scratch_write_data=(return_phase==3&&fma_result[31])?32'd0:fma_result;
 assign fixed_valid=busy&&fma_result_valid&&return_phase>=4;
 assign fixed_value=fma_result;
 assign rgb_write=busy&&fixed_result_valid;
 assign rgb_write_vertex=fixed_tags[3][4:0];
 assign rgb_write_channel=fixed_tags[3][6:5];
 assign rgb_write_data=fixed_result[31]?8'd0:(|fixed_result[30:8])?8'd255:fixed_result[7:0];
 reg [7:0] amb_byte,dif_byte;
 always @*begin
  case(read_phase)
   4:begin amb_byte=amb[7:0];dif_byte=dif[7:0];end
   5:begin amb_byte=amb[15:8];dif_byte=dif[15:8];end
   default:begin amb_byte=amb[23:16];dif_byte=dif[23:16];end
  endcase
  fma_a=0;fma_b=0;fma_c=0;fma_multiply_only=0;
  case(read_phase)
   0:begin fma_a=signed_byte_float(normal_data[15:8]);fma_b=cy;fma_multiply_only=1;end
   1:begin fma_a=cx;fma_b=signed_byte_float(normal_data[7:0]);fma_c=scratch_data;end
   2:begin fma_a=cz;fma_b=signed_byte_float(normal_data[23:16]);fma_c=scratch_data;end
   3:begin fma_a=scratch_data;fma_b=32'h3c010204;fma_multiply_only=1;end
   default:begin fma_a=unsigned_byte_float(dif_byte);fma_b=scratch_data;fma_c=unsigned_byte_float(amb_byte);end
  endcase
 end
 integer i;
 always @(posedge clk)begin
  if(reset)begin
   cx<=0;cy<=0;cz<=0;amb<=0;dif<=0;n<=0;vertex<=0;phase<=0;read_phase<=0;
   read_valid<=0;drain<=0;read_vertex<=0;fma_outstanding<=0;fixed_outstanding<=0;
   busy<=0;done<=0;unsupported<=0;cycles<=0;
   for(i=0;i<7;i=i+1)fma_tags[i]<=0;
   for(i=0;i<4;i=i+1)fixed_tags[i]<=0;
  end else begin
   done<=0;read_valid<=issuing;read_phase<=phase;read_vertex<=vertex;
   fma_tags[0]<={read_phase,read_vertex};
   for(i=1;i<7;i=i+1)fma_tags[i]<=fma_tags[i-1];
   fixed_tags[0]<={return_phase[1:0],fma_tags[6][4:0]};
   for(i=1;i<4;i=i+1)fixed_tags[i]<=fixed_tags[i-1];
   case({fma_valid,fma_result_valid&&busy})
    2'b10:fma_outstanding<=fma_outstanding+1'b1;
    2'b01:fma_outstanding<=fma_outstanding-1'b1;
    default:fma_outstanding<=fma_outstanding;
   endcase
   case({fixed_valid,fixed_result_valid&&busy})
    2'b10:fixed_outstanding<=fixed_outstanding+1'b1;
    2'b01:fixed_outstanding<=fixed_outstanding-1'b1;
    default:fixed_outstanding<=fixed_outstanding;
   endcase
   if(busy)begin
    cycles<=cycles+1'b1;
    if(fma_result_valid&&fma_unsupported)unsupported<=1;
    if(fixed_result_valid&&fixed_unsupported)unsupported<=1;
    if(issuing)begin
     if({1'b0,vertex}+6'd1==n)begin
      vertex<=0;
      if(phase==4||phase==5)phase<=phase+1'b1;
      else drain<=1;
     end else vertex<=vertex+1'b1;
    end
    if(drain&&fma_outstanding==0&&!read_valid&&!fma_result_valid&&fixed_outstanding==0&&!fixed_result_valid)begin
     if(phase==6)begin busy<=0;done<=1;end
     else begin phase<=phase+1'b1;drain<=0;end
    end
   end else if(start)begin
    if(count==0||count>32)begin done<=1;unsupported<=1;end
    else begin
     cx<=coefficient_x;cy<=coefficient_y;cz<=coefficient_z;amb<=ambient_rgb;dif<=diffuse_rgb;
     n<=count;vertex<=0;phase<=0;read_phase<=0;read_valid<=0;drain<=0;
     fma_outstanding<=0;fixed_outstanding<=0;busy<=1;done<=0;unsupported<=0;cycles<=0;
    end
   end
  end
 end
endmodule
