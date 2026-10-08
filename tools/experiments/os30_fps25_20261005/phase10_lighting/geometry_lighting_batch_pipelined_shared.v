// Registered shared byte encoders: one normal/diffuse and one ambient.
// One added operand stage preserves throughput and adds5 drain cycles/batch.
// Private one-directional-light controller. No RAM, FMA or converter instances.
// Caller supplies one-cycle synchronous packed-normal and32-word scratch reads.
// Shared FMA latency: valid at inputcycle0 -> result valid atcycle6.
// Shared fixed conversion latency: four stages, scale0, truncate1, depth_mode0.
// Finite RNE lighting only. Unsupported arithmetic is sticky for CPU fallback.
// CAPTURE_CONTEXT=0 requires coefficient/ambient/diffuse inputs to remain
// unchanged from accepted start until busy deasserts. The parent owns them.
module geometry_lighting_batch #(parameter CAPTURE_CONTEXT=1) (
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
 reg [31:0] captured_cx,captured_cy,captured_cz;
 reg [23:0] captured_amb,captured_dif;
 wire [31:0] cx=CAPTURE_CONTEXT?captured_cx:coefficient_x;
 wire [31:0] cy=CAPTURE_CONTEXT?captured_cy:coefficient_y;
 wire [31:0] cz=CAPTURE_CONTEXT?captured_cz:coefficient_z;
 wire [23:0] amb=CAPTURE_CONTEXT?captured_amb:ambient_rgb;
 wire [23:0] dif=CAPTURE_CONTEXT?captured_dif:diffuse_rgb;
 reg [5:0] n;
 reg [4:0] vertex;
 reg [2:0] phase,read_phase;
 reg read_valid,drain;
 reg [4:0] read_vertex;
 reg [7:0] fma_tags[0:7];
 reg encoded_valid;
 reg [31:0] decoded_a,decoded_b,decoded_c;
 reg decoded_multiply_only;
 reg [6:0] fixed_tags[0:3];
 reg [4:0] fma_outstanding;
 reg [3:0] fixed_outstanding;
 wire issuing=busy&&!drain;
 assign work_read_valid=issuing;
 assign work_read_vertex=vertex;
 assign fma_valid=encoded_valid&&busy;
 wire [2:0] return_phase=fma_tags[7][7:5];
 assign scratch_write=busy&&fma_result_valid&&return_phase<4;
 assign scratch_write_vertex=fma_tags[7][4:0];
 assign scratch_write_data=(return_phase==3&&fma_result[31])?32'd0:fma_result;
 assign fixed_valid=busy&&fma_result_valid&&return_phase>=4;
 assign fixed_value=fma_result;
 assign rgb_write=busy&&fixed_result_valid;
 assign rgb_write_vertex=fixed_tags[3][4:0];
 assign rgb_write_channel=fixed_tags[3][6:5];
 assign rgb_write_data=fixed_result[31]?8'd0:(|fixed_result[30:8])?8'd255:fixed_result[7:0];
 reg [7:0] amb_byte,dif_byte,selected_byte;
 wire signed_normal=read_phase<3;
 wire selected_sign=signed_normal&&selected_byte[7];
 wire [7:0] selected_magnitude=selected_sign ? -selected_byte:selected_byte;
 wire [31:0] magnitude_float=unsigned_byte_float(selected_magnitude);
 wire [31:0] selected_float={selected_sign,magnitude_float[30:0]};
 always @*begin
  case(read_phase)
   4:begin amb_byte=amb[7:0];dif_byte=dif[7:0];end
   5:begin amb_byte=amb[15:8];dif_byte=dif[15:8];end
   default:begin amb_byte=amb[23:16];dif_byte=dif[23:16];end
  endcase
  case(read_phase)
   0:selected_byte=normal_data[15:8];
   1:selected_byte=normal_data[7:0];
   2:selected_byte=normal_data[23:16];
   default:selected_byte=dif_byte;
  endcase
  decoded_a=0;decoded_b=0;decoded_c=0;decoded_multiply_only=0;
  case(read_phase)
   0:begin decoded_a=selected_float;decoded_b=cy;decoded_multiply_only=1;end
   1:begin decoded_a=cx;decoded_b=selected_float;decoded_c=scratch_data;end
   2:begin decoded_a=cz;decoded_b=selected_float;decoded_c=scratch_data;end
   3:begin decoded_a=scratch_data;decoded_b=32'h3c010204;decoded_multiply_only=1;end
   default:begin decoded_a=selected_float;decoded_b=scratch_data;decoded_c=unsigned_byte_float(amb_byte);end
  endcase
 end
 integer i;
 always @(posedge clk)begin
  if(reset)begin
   if(CAPTURE_CONTEXT)begin captured_cx<=0;captured_cy<=0;captured_cz<=0;captured_amb<=0;captured_dif<=0;end
   n<=0;vertex<=0;phase<=0;read_phase<=0;
   encoded_valid<=0;read_valid<=0;drain<=0;read_vertex<=0;fma_outstanding<=0;fixed_outstanding<=0;
   busy<=0;done<=0;unsupported<=0;cycles<=0;
   for(i=0;i<8;i=i+1)fma_tags[i]<=0;
   for(i=0;i<4;i=i+1)fixed_tags[i]<=0;
  end else begin
   encoded_valid<=read_valid&&busy;
   fma_a<=decoded_a;fma_b<=decoded_b;fma_c<=decoded_c;fma_multiply_only<=decoded_multiply_only;
   done<=0;read_valid<=issuing;read_phase<=phase;read_vertex<=vertex;
   fma_tags[0]<={read_phase,read_vertex};
   for(i=1;i<8;i=i+1)fma_tags[i]<=fma_tags[i-1];
   fixed_tags[0]<={return_phase[1:0],fma_tags[7][4:0]};
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
    if(drain&&fma_outstanding==0&&!read_valid&&!fma_valid&&!fma_result_valid&&fixed_outstanding==0&&!fixed_result_valid)begin
     if(phase==6)begin busy<=0;done<=1;end
     else begin phase<=phase+1'b1;drain<=0;end
    end
   end else if(start)begin
    if(count==0||count>32)begin done<=1;unsupported<=1;end
    else begin
     if(CAPTURE_CONTEXT)begin
      captured_cx<=coefficient_x;captured_cy<=coefficient_y;captured_cz<=coefficient_z;
      captured_amb<=ambient_rgb;captured_dif<=diffuse_rgb;
     end
     n<=count;vertex<=0;phase<=0;read_phase<=0;read_valid<=0;drain<=0;
     fma_outstanding<=0;fixed_outstanding<=0;busy<=1;done<=0;unsupported<=0;cycles<=0;
    end
   end
  end
 end
endmodule
