// Compact exact-F11 cull controller. Vertex and six-word scratch storage are
// supplied by the caller; reads have one-cycle synchronous latency. Reserved
// scratch words0/1 hold center/products,2/3 hold vertex1differences,4/5 vertex3.
// The exclusive FMA port has the frozen primitive's6cycle result latency.
// Vertex fields:0X,1Y,2reciprocalW. negative_w holds exact W<0 predicates.
module geometry_float_cull_mem(
 input wire clk,reset,input wire in_valid,output wire in_ready,
 input wire [14:0] vertex_indices,input wire [17:0] clip_bits,
 input wire [2:0] negative_w,input wire [1:0] cull_mode,
 output reg out_valid,input wire out_ready,output reg [1:0] reject_class,
 output reg [5:0] clip_or,output reg [31:0] cross_bits,output reg unsupported,
 output reg vertex_read_valid,output reg [4:0] vertex_read_index,
 output reg [1:0] vertex_read_field,input wire [31:0] vertex_data,
 output reg scratch_read_valid,output reg [2:0] scratch_read_index,
 input wire [31:0] scratch_data,output wire scratch_write,
 output wire [2:0] scratch_write_index,output wire [31:0] scratch_write_data,
 output wire fma_valid,output reg [31:0] fma_a,fma_b,fma_c,
 output reg fma_multiply_only,input wire fma_result_valid,
 input wire [31:0] fma_result,input wire fma_unsupported
);
 localparam NONE=0,VERTEX_HOLD=1,VERTEX_MUL=2,VERTEX_MAC=3,
            SCRATCH_HOLD=4,SCRATCH_MUL=5,SCRATCH_ADD=6;
 reg busy,bad,parity;
 reg [14:0] indices;
 reg [1:0] mode;
 reg [4:0] pc;
 reg [31:0] held_operand;
 reg [2:0] request_kind,read_kind,request_target,read_target;
 reg [2:0] tags[0:6];
 reg [2:0] outstanding;
 assign in_ready=!busy && (!out_valid || out_ready);
 assign fma_valid=busy && (read_kind==VERTEX_MUL || read_kind==VERTEX_MAC
                                       || read_kind==SCRATCH_MUL || read_kind==SCRATCH_ADD);
 assign scratch_write=busy&&fma_result_valid&&tags[6]!=7;
 assign scratch_write_index=tags[6];
 assign scratch_write_data=fma_result;
 always @*begin
  vertex_read_valid=0;vertex_read_index=0;vertex_read_field=0;
  scratch_read_valid=0;scratch_read_index=0;request_kind=NONE;request_target=0;
  if(busy)case(pc)
   0,5,9:begin
    vertex_read_valid=1;vertex_read_field=2;request_kind=VERTEX_HOLD;
    vertex_read_index=pc==0?indices[9:5]:pc==5?indices[4:0]:indices[14:10];
   end
   2,3:begin
    vertex_read_valid=1;vertex_read_index=indices[9:5];vertex_read_field=pc==2?0:1;
    request_kind=VERTEX_MUL;request_target=pc==2?0:1;
   end
   7,8,11,12:begin
    vertex_read_valid=1;vertex_read_index=pc<9?indices[4:0]:indices[14:10];
    vertex_read_field=(pc==7||pc==11)?0:1;
    scratch_read_valid=1;scratch_read_index=(pc==7||pc==11)?0:1;
    request_kind=VERTEX_MAC;
    case(pc)7:request_target=2;8:request_target=3;11:request_target=4;default:request_target=5;endcase
   end
   14,17,21:begin
    scratch_read_valid=1;request_kind=SCRATCH_HOLD;
    scratch_read_index=pc==14?5:pc==17?4:parity?0:1;
   end
   16,19:begin
    scratch_read_valid=1;scratch_read_index=pc==16?2:3;
    request_kind=SCRATCH_MUL;request_target=pc==16?0:1;
   end
   23:begin
    scratch_read_valid=1;scratch_read_index=parity?1:0;
    request_kind=SCRATCH_ADD;request_target=7;
   end
   default:;
  endcase
  fma_a=0;fma_b=held_operand;fma_c=0;fma_multiply_only=0;
  case(read_kind)
   VERTEX_MUL:begin fma_a=vertex_data;fma_multiply_only=1;end
   VERTEX_MAC:begin fma_a=vertex_data;fma_c={~scratch_data[31],scratch_data[30:0]};end
   SCRATCH_MUL:begin fma_a=scratch_data;fma_multiply_only=1;end
   SCRATCH_ADD:begin fma_a=32'h3f800000;fma_b=scratch_data;fma_c={~held_operand[31],held_operand[30:0]};end
   default:;
  endcase
 end
 integer i;
 always @(posedge clk)begin
  if(reset)begin
   busy<=0;out_valid<=0;bad<=0;unsupported<=0;pc<=0;outstanding<=0;
   read_kind<=NONE;read_target<=0;
   for(i=0;i<7;i=i+1)tags[i]<=0;
  end else begin
   read_kind<=request_kind;read_target<=request_target;
   tags[0]<=read_target;for(i=1;i<7;i=i+1)tags[i]<=tags[i-1];
   if(out_valid&&out_ready)out_valid<=0;
   if(busy&&read_kind==VERTEX_HOLD)held_operand<=vertex_data;
   if(busy&&read_kind==SCRATCH_HOLD)held_operand<=scratch_data;
   case({fma_valid,busy&&fma_result_valid})
    2'b10:outstanding<=outstanding+1'b1;
    2'b01:outstanding<=outstanding-1'b1;
    default:;
   endcase
   if(busy)begin
    case(pc)
     4,13,20:if(outstanding==0&&!fma_valid)pc<=pc+1'b1;
     24:;
     default:pc<=pc+1'b1;
    endcase
    if(fma_result_valid)begin
     bad<=bad|fma_unsupported;
     if(tags[6]==7)begin
      cross_bits<=fma_result;unsupported<=bad|fma_unsupported;
      reject_class<=((fma_result[30:0]==0)||(mode==1?fma_result[31]:!fma_result[31]))?2:0;
      busy<=0;out_valid<=1;
     end
    end
   end else if(in_valid&&in_ready)begin
    indices<=vertex_indices;mode<=cull_mode;parity<=^negative_w;
    clip_or<=clip_bits[5:0]|clip_bits[11:6]|clip_bits[17:12];
    cross_bits<=0;bad<=0;unsupported<=0;pc<=0;outstanding<=0;read_kind<=NONE;
    if(|(clip_bits[5:0]&clip_bits[11:6]&clip_bits[17:12]))begin reject_class<=1;out_valid<=1;end
    else if(cull_mode==3)begin reject_class<=2;out_valid<=1;end
    else if(cull_mode==0)begin reject_class<=0;out_valid<=1;end
    else busy<=1;
   end
  end
 end
endmodule
