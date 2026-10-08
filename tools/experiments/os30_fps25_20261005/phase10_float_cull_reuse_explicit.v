// V2 storage reuse: inputs are overwritten only after their phase has issued
// every dependent operation; results return after the final input was read.
// Private exact F11 triangle rejection controller. The external FMA port is
// exclusively reserved from input acceptance to output production. Responses
// must be routed only to this client. No multiplier/DSP is instantiated here.
// vertex_data words0..8 = x1,y1,inv1,x2,y2,inv2,x3,y3,inv3.
// negative_w is the three predicates W<0 (negative zero is NOT negative).
// cull_mode:0 none,1 front,2 back,3 both; reject_class:0 keep,1 clip,2 cull.
// Clip OR is returned to the caller; partial clipping requires further work.
module geometry_float_cull(
 input wire clk,reset,input wire in_valid,output wire in_ready,
 input wire [287:0] vertex_data,input wire [17:0] clip_bits,
 input wire [2:0] negative_w,input wire [1:0] cull_mode,
 output reg out_valid,input wire out_ready,output reg [1:0] reject_class,
 output reg [5:0] clip_or,output reg [31:0] cross_bits,output reg unsupported,
 output wire fma_valid,output reg [31:0] fma_a,fma_b,fma_c,
 output reg fma_multiply_only,input wire fma_result_valid,
 input wire [31:0] fma_result,input wire fma_unsupported
);
 reg busy,bad,parity;
 reg [1:0] mode,phase;
 reg [2:0] issued,received;
 reg [31:0] vertex[0:8];
 wire [2:0] operations=phase==1?3'd4:phase==3?3'd1:3'd2;
 assign in_ready=!busy && (!out_valid || out_ready);
 assign fma_valid=busy && issued<operations;
 always @*begin
  fma_a=0;fma_b=0;fma_c=0;fma_multiply_only=0;
  case(phase)
   0:begin
    fma_a=issued[0]?vertex[4]:vertex[3];fma_b=vertex[5];fma_multiply_only=1;
   end
   1:begin
    case(issued[1:0])
     0:begin fma_a=vertex[0];fma_b=vertex[2];fma_c={~vertex[3][31],vertex[3][30:0]};end
     1:begin fma_a=vertex[1];fma_b=vertex[2];fma_c={~vertex[4][31],vertex[4][30:0]};end
     2:begin fma_a=vertex[6];fma_b=vertex[8];fma_c={~vertex[3][31],vertex[3][30:0]};end
     3:begin fma_a=vertex[7];fma_b=vertex[8];fma_c={~vertex[4][31],vertex[4][30:0]};end
    endcase
   end
   2:begin
    fma_a=issued[0]?vertex[1]:vertex[0];fma_b=issued[0]?vertex[6]:vertex[7];fma_multiply_only=1;
   end
   3:begin
    fma_a=32'h3f800000;fma_b=parity?vertex[1]:vertex[0];
    fma_c=parity?{~vertex[0][31],vertex[0][30:0]}:{~vertex[1][31],vertex[1][30:0]};
   end
  endcase
 end
 wire accept=in_valid && in_ready;
 genvar k;
 generate for(k=0;k<9;k=k+1)begin:slots
  wire replace_result=busy && fma_result_valid &&
   ((phase==0 && ((k==3 && received==0)||(k==4 && received==1))) ||
    (phase==1 && ((k==0 && received==0)||(k==1 && received==1)||(k==6 && received==2)||(k==7 && received==3))) ||
    (phase==2 && ((k==0 && received==0)||(k==1 && received==1))));
  always @(posedge clk)begin
   if(accept)vertex[k]<=vertex_data[k*32+:32];
   else if(replace_result)vertex[k]<=fma_result;
  end
 end endgenerate
 always @(posedge clk)begin
  if(reset)begin busy<=0;out_valid<=0;unsupported<=0;bad<=0;phase<=0;issued<=0;received<=0;end
  else begin
   if(out_valid && out_ready)out_valid<=0;
   if(in_valid && in_ready)begin
    clip_or<=clip_bits[5:0]|clip_bits[11:6]|clip_bits[17:12];
    cross_bits<=0;unsupported<=0;bad<=0;phase<=0;issued<=0;received<=0;
    mode<=cull_mode;parity<=^negative_w;
    if(|(clip_bits[5:0]&clip_bits[11:6]&clip_bits[17:12]))begin reject_class<=1;out_valid<=1;end
    else if(cull_mode==3)begin reject_class<=2;out_valid<=1;end
    else if(cull_mode==0)begin reject_class<=0;out_valid<=1;end
    else busy<=1;
   end
   if(fma_valid)issued<=issued+1'b1;
   if(busy && fma_result_valid)begin
    bad<=bad|fma_unsupported;received<=received+1'b1;
    case(phase)
     3:begin
      cross_bits<=fma_result;unsupported<=bad|fma_unsupported;
      reject_class<=((fma_result[30:0]==0) || (mode==1?fma_result[31]:!fma_result[31]))?2:0;
      busy<=0;out_valid<=1;
     end
    endcase
    if(received+1'b1==operations)begin phase<=phase+1'b1;issued<=0;received<=0;end
   end
  end
 end
endmodule
