// GATHER50: indexed seven-word vertices -> local triangle and clip masks.
// No RAM or DSP is instantiated; uses the engine's scratch and active bank.
`default_nettype none
module gpu_mesh_gather(
    input wire clk,reset_n,cancel,start,
    input wire [5:0] tri_index,
    input wire [7:0] dst_ptr,
    input wire [7:0] mask_reg,
    output reg busy,done,
    output wire [7:0] scratch_ra,scratch_rb,
    input wire [31:0] scratch_qa,scratch_qb,
    output wire scratch_we,
    output wire [7:0] scratch_wa,
    output wire [31:0] scratch_wd,
    output wire [7:0] bank_addr,
    input wire [31:0] bank_rdata
);
localparam IDLE=0,INDEX_REQ=1,INDEX_CAP=2,VERTEX=3,W_REQ=4,W_CAP=5,
    WORD_REQ=6,WORD_CAP=7,MASK=8,WRITE_OR=9,WRITE_AND=10,FINISH=11;
reg [3:0] state;
reg [5:0] index;
reg [7:0] destination,source,mask_destination;
reg [14:0] triangle;
reg [1:0] vertex_index;
reg [2:0] word_index;
reg signed [31:0] w;
reg signed [32:0] minus_w;
reg [5:0] vertex_mask,mask_or,mask_and;
reg range_fault;
wire outside_positive=$signed(bank_rdata)>w;
wire outside_negative=$signed({bank_rdata[31],bank_rdata})<minus_w;
wire outside_range=bank_rdata[31:29]!=3'd0 && bank_rdata[31:29]!=3'd7 && bank_rdata!=32'h20000000;
assign scratch_ra={3'd0,index[5:1]};
assign scratch_rb=0;
assign scratch_we=(state==WORD_CAP||state==WRITE_OR||state==WRITE_AND)&&reset_n&&!cancel;
assign scratch_wa=state==WRITE_OR?mask_destination:state==WRITE_AND?mask_destination+8'd1:destination;
assign scratch_wd=state==WRITE_OR?{range_fault,25'd0,mask_or}:state==WRITE_AND?{26'd0,mask_and}:bank_rdata;
assign bank_addr=source+(state==W_REQ||state==W_CAP?8'd3:{5'd0,word_index});
always @(posedge clk)begin
    if(!reset_n||cancel)begin state<=IDLE;busy<=0;done<=0;end
    else begin
        done<=0;
        case(state)
            IDLE:if(start)begin
                index<=tri_index;destination<=dst_ptr;vertex_index<=0;
                mask_or<=0;mask_and<=63;range_fault<=0;mask_destination<=mask_reg;busy<=1;state<=INDEX_REQ;
            end
            INDEX_REQ:state<=INDEX_CAP;
            INDEX_CAP:begin triangle<=index[0]?scratch_qa[30:16]:scratch_qa[14:0];state<=VERTEX;end
            VERTEX:begin
                source<={triangle[4:0],3'd0}-{3'd0,triangle[4:0]};
                word_index<=0;vertex_mask<=0;state<=W_REQ;
            end
            W_REQ:state<=W_CAP;
            W_CAP:begin w<=bank_rdata;minus_w<=-$signed({bank_rdata[31],bank_rdata});state<=WORD_REQ;end
            WORD_REQ:state<=WORD_CAP;
            WORD_CAP:begin
                if(word_index<4&&outside_range)range_fault<=1;
                case(word_index)
                    0:begin vertex_mask[4]<=outside_positive;vertex_mask[5]<=outside_negative;end
                    1:begin vertex_mask[2]<=outside_positive;vertex_mask[3]<=outside_negative;end
                    2:begin vertex_mask[0]<=outside_positive;vertex_mask[1]<=outside_negative;end
                    default:;
                endcase
                destination<=destination+1'b1;
                if(word_index==6)state<=MASK;
                else begin word_index<=word_index+1'b1;state<=WORD_REQ;end
            end
            MASK:begin
                mask_or<=mask_or|vertex_mask;mask_and<=mask_and&vertex_mask;
                triangle<={5'd0,triangle[14:5]};
                if(vertex_index==2)state<=WRITE_OR;
                else begin vertex_index<=vertex_index+1'b1;state<=VERTEX;end
            end
            WRITE_OR:state<=WRITE_AND;
            WRITE_AND:state<=FINISH;
            FINISH:begin busy<=0;done<=1;state<=IDLE;end
            default:begin busy<=0;state<=IDLE;end
        endcase
    end
end
endmodule
`default_nettype wire
