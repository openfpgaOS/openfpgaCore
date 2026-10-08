// Private pipelined shared float-to-fixed converter. Identical arithmetic
// contract to geometry_float_fixed; stages isolate shift, rounding and sign.
module geometry_float_fixed_pipe(
 input wire clk,reset,input wire in_valid,input wire [31:0] value,
 input wire [5:0] scale,input wire truncate,input wire depth_mode,
 output reg out_valid,output reg [31:0] fixed_value,output reg unsupported
);
reg valid0,valid1,valid2;
reg sign0,truncate0,depth0,nan0,inf0,low0,high0;
reg [23:0] mantissa0;
reg signed [9:0] exponent0;
reg [31:0] magnitude1,magnitude2;
reg increment1,sign1,sign2,depth1,depth2,nan1,nan2,bad1,bad2,low1,low2,high1,high2;
reg [31:0] magnitude;
reg bad,increment;
reg [4:0] right_shift;
reg [2:0] left_shift;
reg [55:0] shifted;
always @*begin
 magnitude=0;bad=nan0||inf0;increment=0;right_shift=0;left_shift=0;shifted=0;
 if(exponent0>30)begin
  magnitude=32'h7fffffff;bad=1;
  if(sign0)begin magnitude=32'h80000000;if(exponent0==31 && mantissa0==24'h800000)bad=0;end
 end else if(exponent0>=23)begin
  left_shift=exponent0-23;magnitude={8'b0,mantissa0}<<left_shift;
 end else if(exponent0>=-1)begin
  right_shift=23-exponent0;shifted={mantissa0,32'b0}>>right_shift;
  magnitude={8'b0,shifted[55:32]};
  increment=!truncate0 && shifted[31] && (|shifted[30:0] || shifted[32]);
 end
end
always @(posedge clk)begin
 if(reset)begin valid0<=0;valid1<=0;valid2<=0;out_valid<=0;end
 else begin
  valid0<=in_valid;valid1<=valid0;valid2<=valid1;out_valid<=valid2;
  sign0<=value[31];truncate0<=truncate&&!depth_mode;depth0<=depth_mode;
  mantissa0<={value[30:23]!=0,value[22:0]};
  exponent0<=$signed({2'b0,value[30:23]==0 ? 8'd1 : value[30:23]})-10'sd127
             +$signed({4'b0,depth_mode ? 6'd30 : scale});
  nan0<=value[30:23]==255 && value[22:0]!=0;inf0<=value[30:23]==255 && value[22:0]==0;
  low0<=value[31] || value[30:0]<31'h30800000;high0<=!value[31] && value[30:0]>31'h3fffffff;
  magnitude1<=magnitude;increment1<=increment;sign1<=sign0;depth1<=depth0;nan1<=nan0;bad1<=bad;low1<=low0;high1<=high0;
  magnitude2<=magnitude1+increment1;sign2<=sign1;depth2<=depth1;nan2<=nan1;bad2<=bad1;low2<=low1;high2<=high1;
  fixed_value<=sign2 ? -magnitude2 : magnitude2;unsupported<=bad2;
  if(nan2)fixed_value<=32'h7fffffff;
  if(depth2)begin
   if(low2)begin fixed_value<=1;unsupported<=nan2;end
   else if(high2)begin fixed_value<=32'h7fffff80;unsupported<=nan2;end
  end
 end
end
endmodule
