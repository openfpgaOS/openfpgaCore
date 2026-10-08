// Private mesh ISA PLANE sequencer. Shares the engine's existing 32x32 DSP.
// All operands are frozen by ownership of scratch/bank while busy. Read ports
// are synchronous; explicit request/capture states avoid asynchronous RF muxes.
`default_nettype none
module gpu_mesh_plane #(parameter COMPACT_LAYOUT=0, parameter BATCH_ONLY=0) (
    input wire clk, reset_n, cancel, start,
    input wire batch,
    input wire [7:0] ptr0, ptr1, ptr2_reg,
    output reg busy, done,
    output reg [7:0] scratch_ra, scratch_rb,
    input wire [31:0] scratch_qa, scratch_qb,
    output reg scratch_we,
    output reg [7:0] scratch_wa,
    output reg [31:0] scratch_wd,
    output reg [7:0] bank_addr,
    input wire [31:0] bank_rdata,
    output reg signed [31:0] mul_a, mul_b,
    input wire signed [63:0] product,
    output wire out_valid,
    output wire [31:0] out_data,
    input wire out_ready
);

function [7:0] context_address;
    input [7:0] address;
    begin
        if(!COMPACT_LAYOUT)context_address=address;
        else if(address<=7)context_address=8'd194+address;
        else if(address<=26)context_address=8'd119+address;
        else if(address>=50&&address<=65)context_address=8'd96+address;
        else if(address>=80&&address<=93)context_address=8'd82+address;
        else if(address>=100&&address<=116)context_address=8'd76+address;
        else context_address=8'd193; // legacy link register240
    end
endfunction

localparam IDLE=0, CTX_REQ=1, CTX_CAP=2, ATTR_REQ=3, ATTR_CAP=4,
    DELTA0=5, DELTA1=6, NUM0=7, NUM1=8, NUM2=9,
    SCALE0=10, SCALE1=11, SCALE2=12, SCALE3=13, SCALE4=14,
    ORG0=15, ORG1=16, ORG2=17, ORG3=18, ORG4=19,
    WRITE_DU=20, WRITE_DV=21, WRITE_ORG=22, FINISH=23;
reg [4:0] state;
reg [2:0] context_index;
reg [1:0] attribute_index;
reg batch_mode,context_valid;
wire batching=BATCH_ONLY||batch_mode;
reg [3:0] plane_index,plane_count;
reg [7:0] p0,p1,p2,p2reg;
reg signed [16:0] dx1,dx2,dy1,dy2;
reg signed [15:0] x0,y0;
reg [30:0] reciprocal;
reg subpixel,small_scale,negative,doing_dv;
reg signed [31:0] a0,a1,a2,du,dv,origin;
reg signed [32:0] scaled;
reg signed [63:0] acc,numerator;
wire output_state=state==WRITE_DU||state==WRITE_DV||state==WRITE_ORG;
wire [31:0] output_word=state==WRITE_DU?origin:state==WRITE_DV?du:dv;
assign out_valid=reset_n&&!cancel&&batching&&output_state;
assign out_data=(plane_index<3||plane_index==6)?output_word:{{8{output_word[23]}},output_word[23:0]};

// NUM2 and SCALE2 are mutually exclusive uses of the same wide arithmetic
// lane. Keep the carry-in explicit so synthesis builds one add/sub chain.
wire wide_subtract=(state==NUM2);
wire signed [63:0] wide_rhs=wide_subtract?product:(product>>>24);
wire [63:0] wide_result=acc+(wide_rhs^{64{wide_subtract}})+wide_subtract;

// The anchor subtraction and pixel-center bias use the same two32-bit
// add/sub chains in different cycles. All three results wrap exactly as the
// previous assignment to origin[31:0]; discarded carry bits are intentional.
wire origin_subtract=(state==ORG3);
wire signed [31:0] origin_a=origin_subtract?a0:origin;
wire signed [31:0] origin_b=origin_subtract?$signed(acc[31:0]):(du>>>1);
wire signed [31:0] origin_c=origin_subtract?$signed(acc[63:32]):(dv>>>1);
wire [31:0] origin_partial=origin_a+(origin_b^{32{origin_subtract}})+origin_subtract;
wire [31:0] origin_result=origin_partial+(origin_c^{32{origin_subtract}})+origin_subtract;

function signed [31:0] sat33;
    input signed [32:0] value;
    begin
        if(value[32]!=value[31])sat33=value[32]?32'sh80000000:32'sh7fffffff;
        else sat33=value[31:0];
    end
endfunction
function signed [32:0] clamp_sign;
    input signed [63:0] value;
    begin
        if(value>64'sd2147483648)clamp_sign=33'sd2147483648;
        else if(value < -64'sd2147483648)clamp_sign=-33'sd2147483648;
        else clamp_sign=value[32:0];
    end
endfunction

always @* begin
    scratch_ra=0;scratch_rb=0;scratch_we=0;scratch_wa=0;scratch_wd=0;
    bank_addr=attribute_index==0?p0:attribute_index==1?p1:p2;
    case(context_index)
        0:begin scratch_ra=p2reg;scratch_rb=12;end
        1:begin scratch_ra=100;scratch_rb=101;end
        2:begin scratch_ra=102;scratch_rb=103;end
        3:begin scratch_ra=106;scratch_rb=107;end
        4:begin scratch_ra=108;scratch_rb=21;end
        default:begin scratch_ra=22;scratch_rb=0;end
    endcase
    mul_a=0;mul_b=0;
    case(state)
        NUM0:begin mul_a=doing_dv?a2:a1;mul_b=doing_dv?dx1:dy2;end
        NUM1:begin mul_a=doing_dv?a1:a2;mul_b=doing_dv?dx2:dy1;end
        SCALE0:begin mul_a=numerator>>>24;mul_b={1'b0,reciprocal};end
        SCALE1:begin mul_a={8'd0,numerator[23:0]};mul_b={1'b0,reciprocal};end
        ORG0:begin mul_a=du;mul_b=x0;end
        ORG1:begin mul_a=dv;mul_b=y0;end
        WRITE_DU:begin scratch_we=!batching;scratch_wa=114;scratch_wd=du;end
        WRITE_DV:begin scratch_we=!batching;scratch_wa=115;scratch_wd=dv;end
        WRITE_ORG:begin scratch_we=!batching;scratch_wa=116;scratch_wd=origin;end
        default:;
    endcase
    if(context_index!=0)scratch_ra=context_address(scratch_ra);
    scratch_rb=context_address(scratch_rb);
    scratch_wa=context_address(scratch_wa);
    if(!reset_n||cancel)scratch_we=0;
end

always @(posedge clk) begin
    if(!reset_n||cancel)begin state<=IDLE;busy<=0;done<=0;end
    else begin
        done<=0;
        case(state)
            IDLE:if(start)begin
                p0<=ptr0;p1<=ptr1;p2reg<=ptr2_reg;
                p2<=ptr0+8'd20;batch_mode<=batch;plane_index<=0;context_valid<=0;
                context_index<=0;busy<=1;state<=CTX_REQ;
            end
            CTX_REQ:state<=CTX_CAP;
            CTX_CAP:begin
                case(context_index)
                    0:begin
                        if(batching)plane_count<=scratch_qa[3:0];
                        else p2<=scratch_qa[7:0];
                        subpixel<=scratch_qb[0];
                    end
                    1:begin dx1<=scratch_qa[16:0];dx2<=scratch_qb[16:0];end
                    2:begin dy1<=scratch_qa[16:0];dy2<=scratch_qb[16:0];end
                    3:begin reciprocal<=scratch_qa[30:0];small_scale<=scratch_qb[0];end
                    4:begin negative<=scratch_qa[0];x0<=scratch_qb[15:0];end
                    default:y0<=scratch_qa[15:0];
                endcase
                if(context_index==0)begin attribute_index<=0;state<=ATTR_REQ;end
                else if(context_index==5)begin context_valid<=1;state<=NUM0;end
                else begin context_index<=context_index+1'b1;state<=CTX_REQ;end
            end
            ATTR_REQ:state<=ATTR_CAP;
            ATTR_CAP:begin
                case(attribute_index)
                    0:a0<=bank_rdata;
                    1:a1<=bank_rdata;
                    default:a2<=bank_rdata;
                endcase
                if(attribute_index==2)state<=DELTA0;
                else begin attribute_index<=attribute_index+1'b1;state<=ATTR_REQ;end
            end
            DELTA0:begin
                // No independent delta lifetime: these attribute registers
                // become saturated differences before any multiplier reads.
                a1<=sat33($signed({a1[31],a1})-$signed({a0[31],a0}));
                a2<=sat33($signed({a2[31],a2})-$signed({a0[31],a0}));
                state<=DELTA1;
            end
            DELTA1:begin
                if(a1==0&&a2==0)begin
                    // A constant plane is independent of determinant scale,
                    // normalization and anchor. No further context/DSP reads.
                    du<=0;dv<=0;origin<=a0;state<=WRITE_DU;
                end else begin
                    doing_dv<=0;
                    if(batching&&context_valid)state<=NUM0;
                    else begin context_index<=1;state<=CTX_REQ;end
                end
            end
            NUM0:state<=NUM1;
            NUM1:begin acc<=product;state<=NUM2;end
            NUM2:begin numerator<=wide_result<<((!doing_dv||subpixel)?4:0);state<=SCALE0;end
            SCALE0:state<=SCALE1;
            SCALE1:begin acc<=product;state<=SCALE2;end
            SCALE2:begin acc<=wide_result;state<=SCALE3;end
            SCALE3:begin scaled<=clamp_sign(small_scale?(acc>>>7):(acc>>>20));state<=SCALE4;end
            SCALE4:begin
                if(!doing_dv)begin du<=sat33(negative?-scaled:scaled);doing_dv<=1;state<=NUM0;end
                else begin dv<=sat33(negative?-scaled:scaled);state<=ORG0;end
            end
            ORG0:state<=ORG1;
            ORG1:begin acc[31:0]<=product>>>4;state<=ORG2;end
            ORG2:begin acc[63:32]<=subpixel?(product>>>4):product;state<=ORG3;end
            ORG3:begin origin<=origin_result;state<=ORG4;end
            ORG4:begin origin<=origin_result;state<=WRITE_DU;end
            WRITE_DU:if(!batching||out_ready)state<=WRITE_DV;
            WRITE_DV:if(!batching||out_ready)state<=WRITE_ORG;
            WRITE_ORG:if(!batching||out_ready)state<=FINISH;
            FINISH:begin
                if(batching&&plane_index+1'b1<plane_count)begin
                    p0<=p0+1'b1;p1<=p1+1'b1;p2<=p2+1'b1;
                    plane_index<=plane_index+1'b1;attribute_index<=0;state<=ATTR_REQ;
                end else begin busy<=0;done<=1;state<=IDLE;end
            end
            default:begin busy<=0;state<=IDLE;end
        endcase
    end
end
endmodule
`default_nettype wire
