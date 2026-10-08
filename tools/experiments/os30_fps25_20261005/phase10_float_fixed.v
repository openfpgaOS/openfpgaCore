// Private shared float-to-fixed conversion for GPU clip/UV/depth payloads.
// RNE and RTZ are supported. Depth mode exactly applies the legacy clamps
// after multiplication by2^30, then converts with RNE. Input/output are
// pipelined without stalls; clients must reserve downstream capacity.
module geometry_float_fixed(
 input wire clk,reset,input wire in_valid,input wire [31:0] value,
 input wire [5:0] scale,input wire truncate,input wire depth_mode,
 output reg out_valid,output reg [31:0] fixed_value,output reg unsupported
);
reg valid0,sign0,truncate0,depth0,nan0,inf0;
reg [23:0] mantissa0;
reg signed [9:0] exponent0;
reg clamp_low0,clamp_high0;
reg [31:0] magnitude;
reg [31:0] result;
reg bad;
reg [4:0] shift;
reg [23:0] guard_mask;
reg increment;
always @* begin
 magnitude=0;result=0;bad=nan0||inf0;shift=0;guard_mask=0;increment=0;
 if(exponent0>30)begin
  magnitude=32'h7fffffff;bad=1;
  if(sign0)begin
   magnitude=32'h80000000;
   if(exponent0==31 && mantissa0==24'h800000)bad=0;
  end
 end else if(exponent0>=23)begin
  magnitude={8'b0,mantissa0}<<(exponent0-23);
 end else if(exponent0>=-1)begin
  shift=23-exponent0;
  magnitude={8'b0,mantissa0}>>shift;
  guard_mask=24'b1<<(shift-1);
  increment=!truncate0 && (mantissa0&guard_mask)!=0
   && ((mantissa0&(guard_mask-1'b1))!=0 || magnitude[0]);
  magnitude=magnitude+increment;
 end
 result=sign0 ? -magnitude : magnitude;
 if(nan0)result=32'h7fffffff;
 if(depth0)begin
  if(clamp_low0)begin result=1;bad=nan0;end
  else if(clamp_high0)begin result=32'h7fffff80;bad=nan0;end
 end
end
always @(posedge clk)begin
 if(reset)begin valid0<=0;out_valid<=0;end
 else begin
  valid0<=in_valid;out_valid<=valid0;
  sign0<=value[31];truncate0<=truncate && !depth_mode;depth0<=depth_mode;
  mantissa0<={value[30:23]!=0,value[22:0]};
  exponent0<=$signed({2'b0,value[30:23]==0 ? 8'd1 : value[30:23]})-10'sd127
             +$signed({4'b0,depth_mode ? 6'd30 : scale});
  nan0<=value[30:23]==255 && value[22:0]!=0;
  inf0<=value[30:23]==255 && value[22:0]==0;
  clamp_low0<=value[31] || value[30:0]<31'h30800000;
  clamp_high0<=!value[31] && value[30:0]>31'h3fffffff;
  fixed_value<=result;unsupported<=bad;
 end
end
endmodule
