// Three scheduled scratch operand pairs share the engine's existing DSP.
`default_nettype none
module gpu_mesh_dot (
    input wire clk, reset_n, cancel, start, matrix_row,
    input wire [7:0] ptr_a, ptr_b, destination,
    output reg busy, done,
    output wire [7:0] scratch_ra, scratch_rb,
    input wire [31:0] scratch_qa, scratch_qb,
    output wire scratch_we,
    output wire [7:0] scratch_wa,
    output wire [31:0] scratch_wd,
    output wire signed [31:0] mul_a, mul_b,
    input wire signed [63:0] product,
    output reg signed [63:0] accumulator
);
localparam IDLE=0,REQUEST=1,M0=2,M1=3,M2=4,SUM=5,RESULT=6,WRITE=7,FINISH=8;
reg [3:0] state;
reg [7:0] base_a,base_b,target;
reg row;
reg [31:0] translation,result;
wire [7:0] offset = state==M0 ? 8'd1 : state==M1 ? 8'd2 : state==M2 ? 8'd3 : 8'd0;
assign scratch_ra=base_a+offset;
assign scratch_rb=base_b+offset;
assign scratch_we=reset_n&&!cancel&&state==WRITE;
assign scratch_wa=target;
assign scratch_wd=result;
assign mul_a=$signed(scratch_qa);
assign mul_b=$signed(scratch_qb);
always @(posedge clk) begin
    if(!reset_n||cancel)begin
        state<=IDLE;busy<=0;done<=0;accumulator<=0;
        base_a<=0;base_b<=0;target<=0;row<=0;translation<=0;result<=0;
    end else begin
        done<=0;
        case(state)
        IDLE:if(start)begin
            base_a<=ptr_a;base_b<=ptr_b;target<=destination;row<=matrix_row;
            state<=REQUEST;busy<=1;
        end
        REQUEST:state<=M0;
        M0:state<=M1;
        M1:begin accumulator<=product;state<=M2;end
        M2:begin accumulator<=accumulator+product;state<=SUM;end
        SUM:begin accumulator<=accumulator+product;translation<=scratch_qa;state<=RESULT;end
        RESULT:begin result<=row ? accumulator[47:16]+translation : accumulator[31:0];state<=WRITE;end
        WRITE:state<=FINISH;
        FINISH:begin busy<=0;done<=1;state<=IDLE;end
        default:state<=IDLE;
        endcase
    end
end
endmodule
`default_nettype wire
