// Exact prepared-edge setup. Uses the engine's existing DSP and divider.
`default_nettype none
module gpu_mesh_edges #(parameter COMPACT_LAYOUT=0)(
    input wire clk,reset_n,cancel,start,
    output reg busy,done,
    output reg [7:0] scratch_ra,scratch_rb,
    input wire [31:0] scratch_qa,scratch_qb,
    output reg scratch_we,
    output reg [7:0] scratch_wa,
    output reg [31:0] scratch_wd,
    output reg signed [31:0] mul_a,mul_b,
    input wire signed [63:0] product,
    output wire div_start,
    output wire [31:0] div_num,
    output wire [15:0] div_divisor,
    input wire div_done,
    input wire [31:0] quotient
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

localparam IDLE=0,CTX_REQ=1,CTX_CAP=2,SCAN=3,CLIP=4,RANGE=5,
    SLOPE_SET=6,DIV_REQUEST=7,DIV_WAIT=8,SLOPE_COMMIT=9,
    PRE_SET=10,PRE_MUL=11,PRE_CAP=12,PRE_APPLY=13,WRITE=14,FINISH=15;
reg [3:0] state;
reg [2:0] context_index;
reg [1:0] edge_index,pre_index;
reg [3:0] write_index;
reg signed [15:0] x0,y0,x1,y1,x2,y2,cy0,cy1;
reg signed [15:0] first,middle,last;
reg subpixel,long_left,bottom;
reg signed [32:0] slope0,slope1,slope2,pre_slope;
reg signed [16:0] dx,dy,pre_distance;
reg [31:0] quotient_r;
reg signed [31:0] offset_r,long_seed,short_seed,bottom_seed;
wire [15:0] abs_dx=dx[16]?-dx[15:0]:dx[15:0];
assign div_num=subpixel?{abs_dx,16'd0}:{4'd0,abs_dx,12'd0};
assign div_divisor=dy[15:0];
assign div_start=state==DIV_REQUEST && dy!=0 && reset_n && !cancel;
wire signed [32:0] signed_quotient=dx[16]?-$signed({1'b0,quotient_r}):$signed({1'b0,quotient_r});
wire signed [16:0] selected_y=pre_index==2?{middle[15],middle}:{first[15],first};
wire signed [15:0] selected_vertex_y=(pre_index==2||(pre_index==1&&bottom))?y1:y0;
wire signed [31:0] selected_vertex_x=(pre_index==2||(pre_index==1&&bottom))
    ?$signed({{4{x1[15]}},x1,12'd0}):$signed({{4{x0[15]}},x0,12'd0});
wire signed [31:0] shifted_product=subpixel?(product>>>4):product;
wire [31:0] correction={pre_distance[3:0],28'd0};
// Select the endpoints first so all three edges share one x subtractor and
// one y subtractor, with the original signed17 result width unchanged.
wire signed [16:0] slope_x_high=edge_index==1?{x1[15],x1}:{x2[15],x2};
wire signed [16:0] slope_x_low=edge_index==2?{x1[15],x1}:{x0[15],x0};
wire signed [16:0] slope_y_high=edge_index==1?{y1[15],y1}:{y2[15],y2};
wire signed [16:0] slope_y_low=edge_index==2?{y1[15],y1}:{y0[15],y0};
// Q4 scanline center +8 is concatenation, not a second arithmetic lane.
wire signed [16:0] pre_origin=subpixel?{selected_y[12:0],4'b1000}:selected_y;
wire signed [16:0] pre_vertex_y={selected_vertex_y[15],selected_vertex_y};

always @* begin
    case(context_index)
        0:begin scratch_ra=21;scratch_rb=22;end
        1:begin scratch_ra=23;scratch_rb=24;end
        2:begin scratch_ra=25;scratch_rb=26;end
        3:begin scratch_ra=16;scratch_rb=12;end
        default:begin scratch_ra=104;scratch_rb=105;end
    endcase
    mul_a=pre_slope[31:0];mul_b=pre_distance;
    scratch_we=state==WRITE && reset_n && !cancel;
    scratch_wa=0;scratch_wd=0;
    case(write_index)
        0:begin scratch_wa=80;scratch_wd=slope0[31:0];end
        1:begin scratch_wa=82;scratch_wd=slope1[31:0];end
        2:begin scratch_wa=84;scratch_wd=slope2[31:0];end
        3:begin scratch_wa=86;scratch_wd={{16{first[15]}},first};end
        4:begin scratch_wa=87;scratch_wd={{16{middle[15]}},middle};end
        5:begin scratch_wa=88;scratch_wd={{16{last[15]}},last};end
        6:begin scratch_wa=89;scratch_wd={31'd0,long_left};end
        7:begin scratch_wa=90;scratch_wd={31'd0,bottom};end
        8:begin scratch_wa=91;scratch_wd=long_left?long_seed:short_seed;end
        9:begin scratch_wa=92;scratch_wd=long_left?short_seed:long_seed;end
        default:begin scratch_wa=93;scratch_wd=bottom_seed;end
    endcase
    scratch_ra=context_address(scratch_ra);
    scratch_rb=context_address(scratch_rb);
    scratch_wa=context_address(scratch_wa);
end

always @(posedge clk) begin
    if(!reset_n||cancel)begin state<=IDLE;busy<=0;done<=0;end
    else begin
        done<=0;
        case(state)
            IDLE:if(start)begin
                busy<=1;context_index<=0;state<=CTX_REQ;
                slope0<=0;slope1<=0;slope2<=0;
                long_seed<=0;short_seed<=0;bottom_seed<=0;bottom<=0;
            end
            CTX_REQ:state<=CTX_CAP;
            CTX_CAP:begin
                case(context_index)
                    0:begin x0<=scratch_qa[15:0];y0<=scratch_qb[15:0];end
                    1:begin x1<=scratch_qa[15:0];y1<=scratch_qb[15:0];end
                    2:begin x2<=scratch_qa[15:0];y2<=scratch_qb[15:0];end
                    3:begin cy0<=scratch_qa[15:0];cy1<=scratch_qa[31:16];subpixel<=scratch_qb[0];end
                    default:long_left<=!scratch_qb[31] && (|scratch_qa || |scratch_qb);
                endcase
                if(context_index==4)state<=SCAN;
                else begin context_index<=context_index+1'b1;state<=CTX_REQ;end
            end
            SCAN:begin
                first<=subpixel?(($signed({y0[15],y0})+17'sd7)>>>4):y0;
                middle<=subpixel?(($signed({y1[15],y1})+17'sd7)>>>4):y1;
                last<=subpixel?(($signed({y2[15],y2})+17'sd7)>>>4):y2;
                state<=CLIP;
            end
            CLIP:begin
                if(first<cy0)first<=cy0;
                if(last>cy1)last<=cy1;
                state<=RANGE;
            end
            RANGE:begin
                bottom<=first>=middle;
                if(first>=last)begin write_index<=0;state<=WRITE;end
                else begin edge_index<=0;state<=SLOPE_SET;end
            end
            SLOPE_SET:begin
                dx<=slope_x_high-slope_x_low;
                dy<=slope_y_high-slope_y_low;
                state<=DIV_REQUEST;
            end
            DIV_REQUEST:if(dy==0)begin quotient_r<=0;state<=SLOPE_COMMIT;end
                        else state<=DIV_WAIT;
            DIV_WAIT:if(div_done)begin quotient_r<=quotient;state<=SLOPE_COMMIT;end
            SLOPE_COMMIT:begin
                case(edge_index)
                    0:slope0<=dy==0?33'd0:signed_quotient;
                    1:slope1<=dy==0?33'd0:signed_quotient;
                    default:slope2<=dy==0?33'd0:signed_quotient;
                endcase
                if(edge_index==2)begin pre_index<=0;state<=PRE_SET;end
                else begin edge_index<=edge_index+1'b1;state<=SLOPE_SET;end
            end
            PRE_SET:begin
                pre_slope<=pre_index==0?slope0:pre_index==2?slope2:bottom?slope2:slope1;
                pre_distance<=pre_origin-pre_vertex_y;
                state<=PRE_MUL;
            end
            PRE_MUL:state<=PRE_CAP;
            PRE_CAP:begin
                // A sign mismatch identifies precisely the missing signed33
                // correction. Only after this fractional shift may bit32 go.
                if(subpixel && pre_slope[32]!=pre_slope[31])
                    offset_r<=pre_slope[31]?(shifted_product+correction):(shifted_product-correction);
                else offset_r<=shifted_product;
                state<=PRE_APPLY;
            end
            PRE_APPLY:begin
                case(pre_index)
                    0:long_seed<=selected_vertex_x+offset_r;
                    1:short_seed<=selected_vertex_x+offset_r;
                    default:bottom_seed<=selected_vertex_x+offset_r;
                endcase
                if(pre_index==2 || (pre_index==1&&(!subpixel||bottom)))begin
                    if(pre_index!=2)bottom_seed<=$signed({{4{x1[15]}},x1,12'd0});
                    write_index<=0;state<=WRITE;
                end else begin pre_index<=pre_index+1'b1;state<=PRE_SET;end
            end
            WRITE:if(write_index==10)state<=FINISH;else write_index<=write_index+1'b1;
            FINISH:begin busy<=0;done<=1;state<=IDLE;end
            default:begin busy<=0;state<=IDLE;end
        endcase
    end
end
endmodule
`default_nettype wire
