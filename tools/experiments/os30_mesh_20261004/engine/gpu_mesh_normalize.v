// Normalize the three perspective triples in ten-word attribute records.
// This is exactly the old repeated common shift, with two sequential RAM
// passes instead of rereading/re-writing nine words for every individual bit.
`default_nettype none
module gpu_mesh_normalize (
    input wire clk, reset_n, cancel, start,
    input wire [7:0] base,
    output reg busy, done,
    output wire [7:0] bank_addr,
    input wire [31:0] bank_rdata,
    output wire bank_we,
    output wire [31:0] bank_wdata
);
localparam IDLE=0,READ_REQ=1,READ_CAP=2,SHIFT=3,WRITE_REQ=4,WRITE=5,FINISH=6;
reg [2:0] state;
reg [7:0] origin,offset;
reg [1:0] vertex,attribute;
reg [4:0] shift;
reg [31:0] magnitude;
assign bank_addr=origin+offset;
assign bank_we=reset_n&&!cancel&&state==WRITE;
assign bank_wdata=bank_rdata<<shift;
wire [31:0] signed_magnitude=bank_rdata[31]?~bank_rdata:bank_rdata;
always @(posedge clk) begin
    if(!reset_n||cancel)begin
        state<=IDLE;busy<=0;done<=0;origin<=0;offset<=0;
        vertex<=0;attribute<=0;shift<=0;magnitude<=0;
    end else begin
        done<=0;
        case(state)
        IDLE:if(start)begin
            origin<=base;offset<=0;vertex<=0;attribute<=0;
            shift<=0;magnitude<=0;busy<=1;state<=READ_REQ;
        end
        READ_REQ:state<=READ_CAP;
        READ_CAP:begin
            magnitude<=magnitude|signed_magnitude;
            if(vertex==2&&attribute==2)state<=SHIFT;
            else begin
                if(attribute==2)begin attribute<=0;vertex<=vertex+1'b1;offset<=offset+8'd8;end
                else begin attribute<=attribute+1'b1;offset<=offset+8'd1;end
                state<=READ_REQ;
            end
        end
        SHIFT:begin
            if((|magnitude[31:28])||shift==16)begin
                offset<=0;vertex<=0;attribute<=0;
                state<=shift==0?FINISH:WRITE_REQ;
            end else begin magnitude<=magnitude<<1;shift<=shift+1'b1;end
        end
        WRITE_REQ:state<=WRITE;
        WRITE:begin
            if(vertex==2&&attribute==2)state<=FINISH;
            else begin
                if(attribute==2)begin attribute<=0;vertex<=vertex+1'b1;offset<=offset+8'd8;end
                else begin attribute<=attribute+1'b1;offset<=offset+8'd1;end
                state<=WRITE_REQ;
            end
        end
        FINISH:begin busy<=0;done<=1;state<=IDLE;end
        default:state<=IDLE;
        endcase
    end
end
endmodule
`default_nettype wire
