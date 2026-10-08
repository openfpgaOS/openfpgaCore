// Private scalar reciprocal probe. Correctly rounded RNE for normal inputs
// whose reciprocal is normal. Exceptional/subnormal cases raise unsupported.
module geometry_float_recip(
 input wire clk,reset,input wire in_valid,output wire in_ready,
 input wire [31:0] denominator,
 output reg out_valid,input wire out_ready,
 output reg [31:0] reciprocal,output reg unsupported
);
reg [1:0] state;
reg sign;
reg signed [9:0] exponent;
reg [23:0] divisor,remainder,quotient;
reg [4:0] iteration;
wire [24:0] trial={remainder,1'b0};
wire [24:0] difference=trial-{1'b0,divisor};
wire subtract=!difference[24];
wire [23:0] next_remainder=subtract?difference[23:0]:trial[23:0];
wire [23:0] next_quotient={quotient[22:0],subtract};
wire [24:0] twice_rem={remainder,1'b0};
wire round_up=twice_rem>{1'b0,divisor} || (twice_rem=={1'b0,divisor} && quotient[0]);
wire [24:0] rounded={1'b0,quotient}+round_up;
wire signed [9:0] rounded_exponent=exponent+$signed({9'b0,rounded[24]});
assign in_ready=state==0 && (!out_valid || out_ready);
always @(posedge clk)begin
 if(reset)begin state<=0;out_valid<=0;unsupported<=0;end
 else begin
  if(out_valid && out_ready)out_valid<=0;
  if(in_valid && in_ready)begin
   sign<=denominator[31];exponent<=10'sd253-$signed({2'b0,denominator[30:23]});
   unsupported<=0;
   if(denominator[30:23]==0 || denominator[30:23]==255)begin
    reciprocal<=0;unsupported<=1;out_valid<=1;
   end else if(denominator[22:0]==0)begin
    reciprocal<={denominator[31],8'd254-denominator[30:23],23'b0};
    unsupported<=denominator[30:23]==254;out_valid<=1;
   end else begin
    divisor<={1'b1,denominator[22:0]};remainder<=24'h800000;
    quotient<=0;iteration<=0;state<=1;
   end
  end
  if(state==1)begin
   remainder<=next_remainder;quotient<=next_quotient;
   if(iteration==23)state<=2;else iteration<=iteration+1;
  end
  if(state==2)begin
   reciprocal<={sign,rounded_exponent[7:0],rounded[22:0]};
   unsupported<=rounded_exponent<1 || rounded_exponent>254;
   out_valid<=1;state<=0;
  end
 end
end
endmodule
