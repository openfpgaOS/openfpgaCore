// PROJ47: current RAW-worker projection using the engine DSP and divider.
// Input scratch vertex: clipx/y/z/w,S,T,RGBA. Output: xy,S,T,zi,depth,
// alpha[23:16]|RGB565,0. Viewport49=cx,50=cyQ4,51=halfwidth,52=halfheightQ4.
// PROJA51 also consumes material flags53, D54/C55 RGB565 and UV offsets60/61.
`default_nettype none
module gpu_mesh_project(
    input wire clk,reset_n,cancel,start,
    input wire material_mode,
    input wire [7:0] src_ptr,dst_ptr,
    output reg busy,done,
    output reg [7:0] scratch_ra,scratch_rb,
    input wire [31:0] scratch_qa,scratch_qb,
    output wire scratch_we,
    output wire [7:0] scratch_wa,
    output reg [31:0] scratch_wd,
    output reg signed [31:0] mul_a,mul_b,
    input wire signed [63:0] product,
    output wire div_start,
    output wire [31:0] div_den,
    input wire div_done,
    input wire [31:0] quotient
);
localparam IDLE=0,READ_REQ=1,READ_CAP=2,DIV_REQ=3,DIV_WAIT=4,
    RATIO_X=5,RATIO_Y=6,RATIO_Y_CAP=7,SCALE_X=8,CAP_X=9,SAT_X=10,
    SCALE_Y=11,CAP_Y=12,SAT_Y=13,WRITE=14,FINISH=15;
reg [3:0] state;
reg [2:0] read_index,write_index;
reg [7:0] src,dst;
reg signed [31:0] x,y,w_or_depth,s,t,center_x,center_y,half_x,half_y;
reg [23:0] color;
reg [31:0] color_d;
reg material,constant_c;
// Any signed64 product shifted by12 fits signed52. Adding a signed32
// viewport center shifted by4 needs at most one further bit. Keep all53
// bits until saturation; no viewport or vertex-range assumptions are needed.
reg signed [52:0] projected;
reg signed [15:0] sx,sy;
wire [31:0] zi={6'd0,w_or_depth[31:6]};
wire y_axis=state==SCALE_Y||state==CAP_Y||state==SAT_Y;
wire signed [52:0] center_term=y_axis?$signed({{21{center_y[31]}},center_y})
    :$signed({{17{center_x[31]}},center_x,4'd0});
wire signed [52:0] product_term=y_axis?$signed({{5{product[63]}},product[63:16]})
    :$signed({product[63],product[63:12]});
// Select the shifted product before one carry chain. The Y path subtracts;
// it does not require a separate wide negation followed by an addition.
wire [52:0] projected_sum=center_term+(product_term^{53{y_axis}})+y_axis;
wire [31:0] uv_sum=(read_index==6?s:t)+scratch_qb;
assign div_start=state==DIV_REQ&&reset_n&&!cancel;
assign div_den=w_or_depth<32'sd256 ? 32'd256:w_or_depth;
assign scratch_we=state==WRITE&&reset_n&&!cancel;
assign scratch_wa=dst+{5'd0,write_index};
function signed [15:0] sat16;
    input signed [52:0] value;
    begin
        if(value>53'sd32767)sat16=16'sh7fff;
        else if(value < -53'sd32768)sat16=16'sh8000;
        else sat16=value[15:0];
    end
endfunction
always @* begin
    scratch_ra=src;scratch_rb=src+8'd1;
    case(read_index)
        1:begin scratch_ra=src+8'd3;scratch_rb=src+8'd4;end
        2:begin scratch_ra=src+8'd5;scratch_rb=src+8'd6;end
        3:begin scratch_ra=49;scratch_rb=50;end
        4:begin scratch_ra=51;scratch_rb=52;end
        5:begin scratch_ra=53;scratch_rb=54;end
        6:begin scratch_ra=55;scratch_rb=60;end
        7:begin scratch_ra=53;scratch_rb=61;end
        default:;
    endcase
    mul_a=0;mul_b=0;
    case(state)
        RATIO_X:begin mul_a=x;mul_b=zi;end
        RATIO_Y:begin mul_a=y;mul_b=zi;end
        SCALE_X:begin mul_a=half_x;mul_b=x;end
        SCALE_Y:begin mul_a=half_y;mul_b=y;end
        default:;
    endcase
    case(write_index)
        0:scratch_wd={sy,sx};
        1:scratch_wd=s;
        2:scratch_wd=t;
        3:scratch_wd=zi;
        4:scratch_wd=w_or_depth;
        5:scratch_wd={8'd0,color};
        default:scratch_wd=color_d;
    endcase
end
always @(posedge clk)begin
    if(!reset_n||cancel)begin state<=IDLE;busy<=0;done<=0;end
    else begin
        done<=0;
        case(state)
            IDLE:if(start)begin
                src<=src_ptr;dst<=dst_ptr;read_index<=0;busy<=1;state<=READ_REQ;
                material<=material_mode;color_d<=0;constant_c<=0;
            end
            READ_REQ:state<=READ_CAP;
            READ_CAP:begin
                case(read_index)
                    0:begin x<=scratch_qa;y<=scratch_qb;end
                    1:begin w_or_depth<=scratch_qa;s<=scratch_qb;end
                    2:begin t<=scratch_qa;color<={scratch_qb[31:24],scratch_qb[7:3],scratch_qb[15:10],scratch_qb[23:19]};end
                    3:begin center_x<=scratch_qa;center_y<=scratch_qb;end
                    4:begin half_x<=scratch_qa;half_y<=scratch_qb;end
                    5:begin
                        constant_c<=scratch_qa[16];
                        color_d<=scratch_qa[17]?{16'd0,color[15:0]}:{16'd0,scratch_qb[15:0]};
                    end
                    6:begin
                        s<=uv_sum;
                        if(constant_c)color[15:0]<=scratch_qa[15:0];
                    end
                    7:t<=uv_sum;
                    default:;
                endcase
                if(read_index==(material?7:4))state<=DIV_REQ;
                else begin read_index<=read_index+1'b1;state<=READ_REQ;end
            end
            DIV_REQ:state<=DIV_WAIT;
            DIV_WAIT:if(div_done)begin w_or_depth<=quotient;state<=RATIO_X;end
            RATIO_X:state<=RATIO_Y;
            RATIO_Y:begin x<=product[47:16];state<=RATIO_Y_CAP;end
            RATIO_Y_CAP:begin y<=product[47:16];state<=SCALE_X;end
            SCALE_X:state<=CAP_X;
            CAP_X:begin projected<=projected_sum;state<=SAT_X;end
            SAT_X:begin sx<=sat16(projected);state<=SCALE_Y;end
            SCALE_Y:state<=CAP_Y;
            CAP_Y:begin projected<=projected_sum;state<=SAT_Y;end
            SAT_Y:begin sy<=sat16(projected);write_index<=0;state<=WRITE;end
            WRITE:if(write_index==6)state<=FINISH;else write_index<=write_index+1'b1;
            FINISH:begin busy<=0;done<=1;state<=IDLE;end
            default:begin busy<=0;state<=IDLE;end
        endcase
    end
end
endmodule
`default_nettype wire
