// Private four-stage RTZ+unsigned-byte clamp used only by lighting. This is
// not a general float-to-fixed replacement. Accepted values give the same
// byte as signed32 RTZ conversion followed by clamp[0,255]. Unsupported flags
// retain the general converter's NaN/Inf/signed32-overflow fallback contract.
module geometry_color_rtz (
 input wire clk,reset,in_valid,input wire [31:0] value,
 output reg out_valid,output reg [31:0] fixed_value,output reg unsupported
);
 reg valid0,valid1,valid2;
 reg [7:0] significand0,shifted1,color2;
 reg [2:0] shift0;
 reg low0,high0,bad0,low1,high1,bad1,bad2;
 always @(posedge clk)begin
  if(reset)begin valid0<=0;valid1<=0;valid2<=0;out_valid<=0;end
  else begin
   valid0<=in_valid;valid1<=valid0;valid2<=valid1;out_valid<=valid2;
   significand0<={1'b1,value[22:16]};shift0<=3'd6-value[25:23];
   low0<=value[31] || value[30:23]<127;
   high0<=!value[31] && value[30:23]>=135;
   bad0<=value[30:23]>=158 && !(value[31] && value[30:0]==31'h4f000000);
   shifted1<=significand0>>shift0;low1<=low0;high1<=high0;bad1<=bad0;
   color2<=low1?8'd0:high1?8'd255:shifted1;bad2<=bad1;
   fixed_value<={24'd0,color2};unsupported<=bad2;
  end
 end
endmodule
