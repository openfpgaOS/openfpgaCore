// Private component: native 24-byte Vtx input only, one outstanding AXI read.
// Partial output is uncommitted until done && error==0. reset is fabric reset:
// the connected AXI responder MUST reset with it; no local cancellation port.
`default_nettype none
module raw_vtx_dma #(
    parameter VARIABLE_WORDS = 0,
    parameter [31:0] MIN_ADDRESS = 32'h10000000,
    parameter [32:0] END_ADDRESS = 33'h014000000
) (
    input wire clk, reset,
    input wire start,
    input wire [31:0] job_base,
    input wire [5:0] job_count,
    input wire [7:0] job_word_count,
    output wire busy,
    output reg done,
    output reg [1:0] error, // 1 invalid descriptor, 2 response, 3 RLAST protocol
    output wire m_arvalid,
    input wire m_arready,
    output wire [31:0] m_araddr,
    output wire [7:0] m_arlen,
    input wire m_rvalid,
    output wire m_rready,
    input wire [31:0] m_rdata,
    input wire [1:0] m_rresp,
    input wire m_rlast,
    output reg word_valid,
    input wire word_ready,
    output reg [7:0] word_index,
    output reg [31:0] word_data
);
    localparam IDLE=2'd0, ADDRESS=2'd1, RESPONSE=2'd2, FINISH=2'd3;
    reg [1:0] state;
    reg [31:0] address;
    reg [8:0] remaining, index;
    reg [4:0] burst_words, beats_left;
    wire [10:0] boundary_words = 11'd1024 - {1'b0,address[11:2]};
    wire [4:0] max_words = remaining>9'd16 ? 5'd16 : remaining[4:0];
    wire [4:0] next_words = boundary_words<{6'd0,max_words}
        ? boundary_words[4:0] : max_words;
    wire [9:0] job_bytes = VARIABLE_WORDS ? {job_word_count,2'b0}
        : ({4'd0,job_count}<<4)+({4'd0,job_count}<<3);
    wire [8:0] job_words = VARIABLE_WORDS ? {1'b0,job_word_count}
        : ({3'd0,job_count}<<2)+({3'd0,job_count}<<1);
    wire [32:0] job_end = {1'b0,job_base}+{23'd0,job_bytes};
    wire invalid_count = VARIABLE_WORDS ? (job_word_count==0 || job_word_count>192)
        : (job_count==0 || job_count>32);
    wire invalid_job = invalid_count || job_base[1:0]!=0
        || job_base<MIN_ADDRESS || {1'b0,job_base}>=END_ADDRESS
        || job_end>END_ADDRESS || job_end[32];
    assign busy=state!=IDLE;
    assign m_arvalid=state==ADDRESS;
    assign m_araddr=address;
    assign m_arlen={3'd0,next_words}-8'd1;
    // On error, drain AXI even if the consumer stalls a prior valid word.
    // A prior word still obeys ready/valid stability until consumed.
    assign m_rready=state==RESPONSE &&
        (error!=0 || (m_rvalid && m_rresp!=0) || !word_valid || word_ready);
    wire take_r=m_rvalid && m_rready;
    wire expected_last=beats_left==1;
    wire last_bad=m_rlast!=expected_last;
    always @(posedge clk) begin
        if(reset) begin
            state<=IDLE;address<=0;remaining<=0;index<=0;
            burst_words<=0;beats_left<=0;done<=0;error<=0;
            word_valid<=0;word_index<=0;word_data<=0;
        end else begin
            done<=0;
            if(word_valid && word_ready)word_valid<=0;
            case(state)
                IDLE: if(start) begin
                    error<=0;index<=0;
                    if(invalid_job)begin error<=1;done<=1;end
                    else begin address<=job_base;remaining<=job_words;state<=ADDRESS;end
                end
                ADDRESS: if(m_arready) begin
                    burst_words<=next_words;beats_left<=next_words;state<=RESPONSE;
                end
                RESPONSE: if(take_r) begin
                    if(error==0 && m_rresp==0 && !last_bad)begin
                        word_valid<=1;word_data<=m_rdata;word_index<=index[7:0];
                    end
                    if(error==0 && m_rresp!=0)error<=2;
                    if(last_bad)error<=3;
                    // If RLAST is late, remain in drain mode until the responder
                    // explicitly closes this accepted burst. Never issue AR.
                    if(beats_left!=0)beats_left<=beats_left-1'b1;
                    index<=index+1'b1;
                    if(m_rlast)begin
                        if(error!=0 || m_rresp!=0 || last_bad || remaining=={4'd0,burst_words})state<=FINISH;
                        else begin
                            address<=address+{25'd0,burst_words,2'b00};
                            remaining<=remaining-{4'd0,burst_words};state<=ADDRESS;
                        end
                    end
                end
                FINISH: if(!word_valid || word_ready)begin state<=IDLE;done<=1;end
                default:state<=IDLE;
            endcase
        end
    end
endmodule
`default_nettype wire
