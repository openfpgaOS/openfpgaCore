// SETUP56: compact triangle stable-Y sort, deltas and both winding signs.
// Uses the engine's existing synchronous scratch ports and registered DSP.
`default_nettype none
module gpu_mesh_setup (
    input wire clk, reset_n, cancel, start,
    output reg busy, done,
    output wire [7:0] scratch_ra, scratch_rb,
    input wire [31:0] scratch_qa, scratch_qb,
    output wire scratch_we,
    output reg [7:0] scratch_wa,
    output reg [31:0] scratch_wd,
    output reg signed [31:0] mul_a, mul_b,
    input wire signed [63:0] product
);
    localparam IDLE=0, REQUEST=1, CAP0=2, CAP1=3, DELTAS=4,
        MUL0=5, MUL1=6, DET=7, SORT0=8, SORT1=9, SORT2=10,
        SORTED_DELTAS=11, WRITE=12, FINISH=13;
    reg [3:0] state;
    reg [31:0] xy0,xy1,xy2;
    reg [1:0] ptr0,ptr1,ptr2;
    reg parity;
    reg signed [16:0] dx1,dx2,dy1,dy2;
    reg signed [33:0] first_product;
    reg signed [34:0] determinant;
    reg [4:0] index;
    wire signed [15:0] x0=xy0[15:0],y0=xy0[31:16];
    wire signed [15:0] x1=xy1[15:0],y1=xy1[31:16];
    wire signed [15:0] x2=xy2[15:0],y2=xy2[31:16];
    wire signed [34:0] subtract_a=state==DET?product[34:0]:35'd0;
    wire signed [34:0] subtract_b=state==DET?{first_product[33],first_product}:determinant;
    wire signed [34:0] difference=subtract_a-subtract_b;
    assign scratch_ra=state==CAP0?8'd249:8'd235;
    assign scratch_rb=8'd242;
    assign scratch_we=reset_n&&!cancel&&state==WRITE;
    function [31:0] input_pointer;
        input [1:0] slot;
        begin case(slot)
            0:input_pointer=32'd235;
            1:input_pointer=32'd242;
            default:input_pointer=32'd249;
        endcase end
    endfunction
    always @* begin
        mul_a=0;mul_b=0;
        if(state==MUL0)begin mul_a={{15{dx1[16]}},dx1};mul_b={{15{dy2[16]}},dy2};end
        if(state==MUL1)begin mul_a={{15{dx2[16]}},dx2};mul_b={{15{dy1[16]}},dy1};end
        scratch_wa=0;scratch_wd=0;
        case(index)
            // Store the original winding before reusing the narrow determinant
            // register for the sorted sign. No 64-bit subtractor is required.
            0:begin scratch_wa=199;scratch_wd=determinant[31:0];end
            1:begin scratch_wa=200;scratch_wd={{29{determinant[34]}},determinant[34:32]};end
            2:begin scratch_wa=137;scratch_wd=input_pointer(ptr0);end
            3:begin scratch_wa=138;scratch_wd=input_pointer(ptr1);end
            4:begin scratch_wa=139;scratch_wd=input_pointer(ptr2);end
            5:begin scratch_wa=140;scratch_wd={{16{x0[15]}},x0};end
            6:begin scratch_wa=141;scratch_wd={{16{y0[15]}},y0};end
            7:begin scratch_wa=142;scratch_wd={{16{x1[15]}},x1};end
            8:begin scratch_wa=143;scratch_wd={{16{y1[15]}},y1};end
            9:begin scratch_wa=144;scratch_wd={{16{x2[15]}},x2};end
            10:begin scratch_wa=145;scratch_wd={{16{y2[15]}},y2};end
            11:begin scratch_wa=176;scratch_wd={{15{dx1[16]}},dx1};end
            12:begin scratch_wa=177;scratch_wd={{15{dx2[16]}},dx2};end
            13:begin scratch_wa=178;scratch_wd={{15{dy1[16]}},dy1};end
            14:begin scratch_wa=179;scratch_wd={{15{dy2[16]}},dy2};end
            15:begin scratch_wa=180;scratch_wd=determinant[31:0];end
            16:begin scratch_wa=181;scratch_wd={{29{determinant[34]}},determinant[34:32]};end
            default:begin end
        endcase
    end
    always @(posedge clk)begin
        if(!reset_n||cancel)begin state<=IDLE;busy<=0;done<=0;end
        else begin
            done<=0;
            case(state)
                IDLE:if(start)begin busy<=1;parity<=0;ptr0<=0;ptr1<=1;ptr2<=2;state<=REQUEST;end
                REQUEST:state<=CAP0;
                CAP0:begin xy0<=scratch_qa;xy1<=scratch_qb;state<=CAP1;end
                CAP1:begin xy2<=scratch_qa;state<=DELTAS;end
                DELTAS:begin
                    dx1<=$signed({x1[15],x1})-$signed({x0[15],x0});
                    dx2<=$signed({x2[15],x2})-$signed({x0[15],x0});
                    dy1<=$signed({y1[15],y1})-$signed({y0[15],y0});
                    dy2<=$signed({y2[15],y2})-$signed({y0[15],y0});
                    state<=MUL0;
                end
                MUL0:state<=MUL1;
                MUL1:begin first_product<=product[33:0];state<=DET;end
                DET:begin determinant<=difference;state<=SORT0;end
                SORT0:begin
                    if(y1<y0)begin xy0<=xy1;xy1<=xy0;ptr0<=ptr1;ptr1<=ptr0;parity<=!parity;end
                    state<=SORT1;
                end
                SORT1:begin
                    if(y2<y1)begin xy1<=xy2;xy2<=xy1;ptr1<=ptr2;ptr2<=ptr1;parity<=!parity;end
                    state<=SORT2;
                end
                SORT2:begin
                    if(y1<y0)begin xy0<=xy1;xy1<=xy0;ptr0<=ptr1;ptr1<=ptr0;parity<=!parity;end
                    state<=SORTED_DELTAS;
                end
                SORTED_DELTAS:begin
                    dx1<=$signed({x1[15],x1})-$signed({x0[15],x0});
                    dx2<=$signed({x2[15],x2})-$signed({x0[15],x0});
                    dy1<=$signed({y1[15],y1})-$signed({y0[15],y0});
                    dy2<=$signed({y2[15],y2})-$signed({y0[15],y0});
                    index<=0;state<=WRITE;
                end
                WRITE:begin
                    if(index==1&&!parity)determinant<=difference;
                    if(index==16)state<=FINISH;else index<=index+1'b1;
                end
                FINISH:begin busy<=0;done<=1;state<=IDLE;end
                default:begin busy<=0;state<=IDLE;end
            endcase
        end
    end
endmodule
`default_nettype wire
