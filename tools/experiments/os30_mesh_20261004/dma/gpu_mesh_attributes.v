// ATTR52: projected seven-word scratch vertex to raster bank attributes.
// Uses existing synchronous scratch read ports and registered shared DSP.
`default_nettype none
module gpu_mesh_attributes (
    input wire clk, reset_n, cancel, start,
    input wire [7:0] src_ptr, dst_ptr, combine_reg,
    output reg busy, done,
    output reg [7:0] scratch_ra, scratch_rb,
    input wire [31:0] scratch_qa, scratch_qb,
    output wire [7:0] bank_addr,
    output wire bank_we,
    output reg [31:0] bank_wdata,
    output reg signed [31:0] mul_a, mul_b,
    input wire signed [63:0] product
);
    localparam IDLE=0, REQUEST=1, CAP0=2, CAP1=3, CAP2=4, CAP3=5,
        WRITE=6, FINISH=7;
    reg [2:0] state;
    reg [7:0] source,destination,combine_address;
    reg [3:0] index;
    reg [31:0] zi,depth,s_product,t_product;
    reg [15:0] color_c,color_d;
    reg combine;
    assign bank_addr=destination+{4'd0,index};
    assign bank_we=reset_n&&!cancel&&state==WRITE;
    always @* begin
        scratch_ra=source+8'd1;scratch_rb=source+8'd3;
        case(state)
            CAP0:begin scratch_ra=source+8'd2;scratch_rb=source+8'd4;end
            CAP1:begin scratch_ra=source+8'd5;scratch_rb=source+8'd6;end
            CAP2:begin scratch_ra=combine_address;scratch_rb=combine_address;end
            default:begin end
        endcase
        mul_a=0;mul_b=0;
        if(state==CAP0)begin mul_a=$signed(scratch_qa);mul_b=$signed(scratch_qb);end
        if(state==CAP1)begin mul_a=$signed(scratch_qa);mul_b=$signed(zi);end
        case(index)
            0:bank_wdata=s_product;
            1:bank_wdata=t_product;
            2:bank_wdata=zi;
            3:bank_wdata={10'd0,color_c[10:5],16'd0};
            4:bank_wdata={11'd0,color_c[15:11],16'd0};
            5:bank_wdata={11'd0,color_c[4:0],16'd0};
            6:bank_wdata=depth;
            7:bank_wdata={11'd0,color_d[15:11],16'd0};
            8:bank_wdata={10'd0,color_d[10:5],16'd0};
            9:bank_wdata={11'd0,color_d[4:0],16'd0};
            default:bank_wdata=0;
        endcase
    end
    always @(posedge clk) begin
        if(!reset_n||cancel)begin
            state<=IDLE;busy<=0;done<=0;source<=0;destination<=0;combine_address<=0;
            index<=0;zi<=0;depth<=0;s_product<=0;t_product<=0;color_c<=0;color_d<=0;combine<=0;
        end else begin
            done<=0;
            case(state)
                IDLE:if(start)begin
                    source<=src_ptr;destination<=dst_ptr;combine_address<=combine_reg;
                    busy<=1;state<=REQUEST;
                end
                REQUEST:state<=CAP0;
                CAP0:begin zi<=scratch_qb;state<=CAP1;end
                CAP1:begin s_product<=product[47:16];depth<=scratch_qb;state<=CAP2;end
                CAP2:begin t_product<=product[47:16];color_c<=scratch_qa[15:0];color_d<=scratch_qb[15:0];state<=CAP3;end
                CAP3:begin combine<=scratch_qa!=0;index<=0;state<=WRITE;end
                WRITE:if(index==(combine?4'd9:4'd6))state<=FINISH;else index<=index+4'd1;
                FINISH:begin busy<=0;done<=1;state<=IDLE;end
                default:begin busy<=0;state<=IDLE;end
            endcase
        end
    end
endmodule
`default_nettype wire
