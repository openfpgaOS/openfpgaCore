// Full packed SM64 vertex: shared scratch/bank ports, DSP and divider only.
// No local RAM or multiplier. Context64..87 remains read-only.
`default_nettype none
module gpu_mesh_vertex (
    input wire clk, reset_n, cancel, start,
    input wire [7:0] raw_ptr, output_ptr,
    output reg busy, done,
    output reg [7:0] scratch_ra, scratch_rb,
    input wire [31:0] scratch_qa, scratch_qb,
    output reg [7:0] bank_addr,
    output wire bank_we,
    output reg [31:0] bank_wdata,
    input wire [31:0] bank_rdata,
    output reg signed [31:0] mul_a, mul_b,
    input wire signed [63:0] product,
    output wire div_start,
    output wire [63:0] div_numerator,
    output wire [31:0] div_denominator,
    output wire [6:0] div_bits,
    input wire div_busy, div_done,
    input wire [63:0] div_quotient
);
    localparam IDLE=0, INPUT_REQ=1, INPUT0=2, INPUT1=3, INPUT2=4, INPUT3=5,
        MATRIX_REQ=6, MATRIX0=7, MATRIX1=8, MATRIX2=9, MATRIX_SUM=10,
        MATRIX_RESULT=11, MATRIX_WRITE=12, UV0=13, UV1=14, UV_WRITE0=15, UV_WRITE1=16,
        LIGHT_REQ=17, LIGHT0=18, LIGHT1=19, LIGHT2=20, LIGHT_SUM=21, LIGHT_SIGN=22,
        COLOR0=23, COLOR1=24, COLOR_HIGH=25, COLOR_WRITE=26,
        FOG0=27, FOG1=28, FOG2=29, FOG_HIGH=30, FOG_OFFSET=31,
        FOG_SIGN=32, FOG_DIV=33, RGBA_WRITE=34, FINISH=35;
    reg [5:0] state;
    reg [7:0] source, destination;
    reg [1:0] row, channel;
    reg signed [15:0] x,y,z;
    reg [31:0] st, rgba, uv_scale;
    reg [23:0] diffuse, ambient;
    reg lighting, fog;
    reg signed [15:0] fog_mul, fog_offset;
    reg signed [31:0] coefficient1;
    reg signed [31:0] clip_z, clip_w;
    reg signed [63:0] accumulator;
    reg [31:0] result;
    reg [39:0] intensity;
    reg [7:0] diffuse_byte, ambient_byte;
    wire active = reset_n && !cancel;
    wire [7:0] matrix_base = 8'd64 + {4'd0,row,2'b00};
    wire [7:0] next_matrix_base = matrix_base + 8'd4;
    wire [8:0] color_sum = {1'b0,accumulator[37:30]} + {1'b0,ambient_byte};
    wire [7:0] color_result = (|accumulator[63:38]) || color_sum[8] ? 8'd255 : color_sum[7:0];
    wire [31:0] fog_scale = clip_w[31] ? 32'd32767 : 32'd1000;
    wire [63:0] unsigned_light_correction = intensity[31] ? {24'd0,diffuse_byte,32'd0} : 64'd0;
    wire [63:0] unsigned_fog_correction = accumulator[31] ? {fog_scale,32'd0} : 64'd0;
    wire signed [63:0] fog_offset_wide = {{48{fog_offset[15]}},fog_offset};
    // Matrix/light accumulation, unsigned-product correction and fog offsets
    // have disjoint lifetimes. Select operands before a single64-bit adder.
    // Preserve modulo64 arithmetic, including uncommon high-intensity inputs.
    reg [63:0] sum_a,sum_b;
    wire [63:0] wide_sum=sum_a+sum_b;
    always @* begin
        sum_a=accumulator;sum_b=product;
        case(state)
            COLOR1:begin sum_a=product;sum_b=unsigned_light_correction;end
            COLOR_HIGH,FOG_HIGH:sum_b={product[31:0],32'd0};
            FOG2:if($signed(clip_w)<=0)begin sum_a=product;sum_b=unsigned_fog_correction;end
            FOG_OFFSET:sum_b=fog_offset_wide<<<8;
            default:;
        endcase
    end
    wire [31:0] uv_product = {{16{product[31]}},product[31:16]} << 11;
    assign bank_we = active && (state==MATRIX_WRITE || state==UV_WRITE0 ||
                               state==UV_WRITE1 || state==RGBA_WRITE);
    assign div_start = active && state==FOG_SIGN && !accumulator[63] &&
                       accumulator!=0 && !div_busy;
    assign div_numerator = accumulator;
    assign div_denominator = $signed(clip_w)>0 ? clip_w : 32'd256;
    assign div_bits = |accumulator[63:32] ? 7'd64 : 7'd32;

    always @* begin
        case(channel)
            0:begin diffuse_byte=diffuse[7:0];ambient_byte=ambient[7:0];end
            1:begin diffuse_byte=diffuse[15:8];ambient_byte=ambient[15:8];end
            default:begin diffuse_byte=diffuse[23:16];ambient_byte=ambient[23:16];end
        endcase
        scratch_ra=matrix_base+8'd2;scratch_rb=matrix_base+8'd3;
        case(state)
            INPUT_REQ:begin scratch_ra=8'd83;scratch_rb=8'd84;end
            INPUT0:begin scratch_ra=8'd85;scratch_rb=8'd86;end
            INPUT1:begin scratch_ra=8'd87;scratch_rb=8'd87;end
            MATRIX_REQ:begin scratch_ra=matrix_base;scratch_rb=matrix_base+8'd1;end
            MATRIX_WRITE:begin scratch_ra=next_matrix_base;scratch_rb=next_matrix_base+8'd1;end
            LIGHT_REQ:begin scratch_ra=8'd80;scratch_rb=8'd81;end
            LIGHT0,LIGHT1,LIGHT2,LIGHT_SUM:begin scratch_ra=8'd82;scratch_rb=8'd83;end
            default:begin end
        endcase
        bank_addr=source;bank_wdata=result;
        case(state)
            INPUT0:bank_addr=source+8'd1;
            INPUT1:bank_addr=source+8'd2;
            INPUT2,INPUT3:bank_addr=source+8'd3;
            MATRIX_WRITE:bank_addr=destination+{6'd0,row};
            UV_WRITE0:bank_addr=destination+8'd4;
            UV_WRITE1:bank_addr=destination+8'd5;
            RGBA_WRITE:begin bank_addr=destination+8'd6;bank_wdata=rgba;end
            default:begin end
        endcase
        mul_a=0;mul_b=0;
        case(state)
            MATRIX0:begin mul_a=$signed(scratch_qa);mul_b={{16{x[15]}},x};end
            MATRIX1:begin mul_a=coefficient1;mul_b={{16{y[15]}},y};end
            MATRIX2:begin mul_a=$signed(scratch_qa);mul_b={{16{z[15]}},z};end
            UV0:begin mul_a={{16{st[15]}},st[15:0]};mul_b={16'd0,uv_scale[15:0]};end
            UV1:begin mul_a={{16{st[31]}},st[31:16]};mul_b={16'd0,uv_scale[31:16]};end
            LIGHT0:begin mul_a=$signed(scratch_qa);mul_b={{24{rgba[7]}},rgba[7:0]};end
            LIGHT1:begin mul_a=coefficient1;mul_b={{24{rgba[15]}},rgba[15:8]};end
            LIGHT2:begin mul_a=$signed(scratch_qa);mul_b={{24{rgba[23]}},rgba[23:16]};end
            COLOR0:begin mul_a=$signed(intensity[31:0]);mul_b={24'd0,diffuse_byte};end
            COLOR1:begin mul_a={24'd0,intensity[39:32]};mul_b={24'd0,diffuse_byte};end
            FOG0:begin mul_a=clip_z;mul_b={{16{fog_mul[15]}},fog_mul};end
            FOG1:begin
                if($signed(clip_w)>0)begin mul_a={{16{fog_offset[15]}},fog_offset};mul_b=clip_w;end
                else begin mul_a=product[31:0];mul_b=fog_scale;end
            end
            FOG2:begin mul_a=accumulator[63:32];mul_b=fog_scale;end
            default:begin end
        endcase
    end

    always @(posedge clk) begin
        if(!reset_n || cancel)begin
            state<=IDLE;busy<=0;done<=0;source<=0;destination<=0;row<=0;channel<=0;
            x<=0;y<=0;z<=0;st<=0;rgba<=0;uv_scale<=0;diffuse<=0;ambient<=0;
            lighting<=0;fog<=0;fog_mul<=0;fog_offset<=0;coefficient1<=0;
            clip_z<=0;clip_w<=0;accumulator<=0;result<=0;intensity<=0;
        end else begin
            done<=0;
            case(state)
                IDLE:if(start)begin source<=raw_ptr;destination<=output_ptr;row<=0;busy<=1;state<=INPUT_REQ;end
                INPUT_REQ:state<=INPUT0;
                INPUT0:begin x<=bank_rdata[15:0];y<=bank_rdata[31:16];
                    diffuse<=scratch_qa[23:0];ambient<=scratch_qb[23:0];state<=INPUT1;end
                INPUT1:begin z<=bank_rdata[15:0];uv_scale<=scratch_qa;
                    lighting<=scratch_qb[17];fog<=scratch_qb[16];state<=INPUT2;end
                INPUT2:begin st<=bank_rdata;fog_mul<=scratch_qa[15:0];fog_offset<=scratch_qa[31:16];state<=INPUT3;end
                INPUT3:begin rgba<=bank_rdata;state<=MATRIX_REQ;end
                MATRIX_REQ:state<=MATRIX0;
                MATRIX0:begin coefficient1<=scratch_qb;state<=MATRIX1;end
                MATRIX1:begin accumulator<=product;state<=MATRIX2;end
                MATRIX2:begin accumulator<=wide_sum;state<=MATRIX_SUM;end
                MATRIX_SUM:begin accumulator<=wide_sum;state<=MATRIX_RESULT;end
                MATRIX_RESULT:begin result<=accumulator[47:16]+scratch_qb;state<=MATRIX_WRITE;end
                MATRIX_WRITE:begin
                    if(row==2)clip_z<=result;
                    if(row==3)begin clip_w<=result;state<=UV0;end
                    else begin row<=row+2'd1;state<=MATRIX0;end
                end
                UV0:state<=UV1;
                UV1:begin result<=uv_product;state<=UV_WRITE0;end
                UV_WRITE0:begin result<=uv_product;state<=UV_WRITE1;end
                UV_WRITE1:state<=lighting ? LIGHT_REQ : fog ? FOG0 : RGBA_WRITE;
                LIGHT_REQ:state<=LIGHT0;
                LIGHT0:begin coefficient1<=scratch_qb;state<=LIGHT1;end
                LIGHT1:begin accumulator<=product;state<=LIGHT2;end
                LIGHT2:begin accumulator<=wide_sum;state<=LIGHT_SUM;end
                LIGHT_SUM:begin accumulator<=wide_sum;state<=LIGHT_SIGN;end
                LIGHT_SIGN:begin intensity<=accumulator[63] ? 40'd0 : accumulator[39:0];
                    channel<=0;rgba[23:0]<=0;state<=COLOR0;end
                COLOR0:state<=COLOR1;
                COLOR1:begin accumulator<=wide_sum;
                    state<=intensity[39:32]!=0 ? COLOR_HIGH : COLOR_WRITE;end
                COLOR_HIGH:begin accumulator<=wide_sum;state<=COLOR_WRITE;end
                COLOR_WRITE:begin
                    case(channel)
                        0:rgba[7:0]<=color_result;
                        1:rgba[15:8]<=color_result;
                        default:rgba[23:16]<=color_result;
                    endcase
                    if(channel==2)state<=fog ? FOG0 : RGBA_WRITE;
                    else begin channel<=channel+2'd1;state<=COLOR0;end
                end
                FOG0:state<=FOG1;
                FOG1:begin accumulator<=product;state<=FOG2;end
                FOG2:begin
                    accumulator<=wide_sum;
                    state<=$signed(clip_w)>0 ? FOG_SIGN : FOG_HIGH;
                end
                FOG_HIGH:begin accumulator<=wide_sum;state<=FOG_OFFSET;end
                FOG_OFFSET:begin accumulator<=wide_sum;state<=FOG_SIGN;end
                FOG_SIGN:begin
                    if(accumulator[63] || accumulator==0)begin rgba[31:24]<=0;state<=RGBA_WRITE;end
                    else if(!div_busy)state<=FOG_DIV;
                end
                FOG_DIV:if(div_done)begin
                    rgba[31:24]<=|div_quotient[63:8] ? 8'd255 : div_quotient[7:0];state<=RGBA_WRITE;
                end
                RGBA_WRITE:state<=FINISH;
                FINISH:begin busy<=0;done<=1;state<=IDLE;end
                default:begin busy<=0;state<=IDLE;end
            endcase
        end
    end
endmodule
`default_nettype wire
