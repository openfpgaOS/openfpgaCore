// Ordered expansion of a validated mesh task into ordinary GPU packets.
// The producer owns raw/list memory through its final accepted output word.
// Only published ring work contributes busy; an unpublished task must not
// prevent CPU material preparation from fencing older GPU work.
`default_nettype none
module gpu_mesh_bridge #(
    parameter ADDR_BITS=12, parameter QUEUE_BITS=9,
    parameter RAM_STYLE="M10K"
) (
    input wire clk,reset_n,enable,soft_reset,ring_reset,
    input wire [ADDR_BITS-1:0] input_wrptr,
    output reg [ADDR_BITS-1:0] input_rdptr,
    output wire [ADDR_BITS-1:0] input_addr,
    input wire [31:0] input_data,
    input wire [ADDR_BITS-1:0] output_rdptr,output_addr,
    output reg [31:0] output_data,
    output reg [ADDR_BITS-1:0] output_wrptr,
    output wire busy,
    output wire mesh_marker_valid,
    output reg [31:0] mesh_marker_serial,mesh_marker_flags,
                       mesh_marker_uofs,mesh_marker_vofs,
    input wire mesh_marker_ready,mesh_marker_error,
    input wire mesh_word_valid,input wire [31:0] mesh_word_data,
    output wire mesh_word_ready,
    input wire mesh_packet_done,mesh_packet_error,
    output reg fault
);
localparam HEADER=0,PASS_HEADER=1,COPY=2,FIELDS=3,REQUEST=4,STREAM=5,FAULT=6;
reg [2:0] state;
reg [31:0] header;
reg [12:0] remaining;
reg [1:0] field_index;
(* ramstyle=RAM_STYLE *) reg [31:0] queue[0:(1<<QUEUE_BITS)-1];
wire input_empty=input_rdptr==input_wrptr;
wire [ADDR_BITS-1:0] used=output_wrptr-output_rdptr;
wire space=used<((1<<QUEUE_BITS)-1);
wire running=reset_n&&!soft_reset&&!ring_reset&&enable&&!fault;
wire take=running&&!input_empty&&
    (state==HEADER||state==FIELDS||(state==COPY&&space));
assign input_addr=input_rdptr+{{(ADDR_BITS-1){1'b0}},take};
assign busy=!input_empty||state!=HEADER||fault;
assign mesh_marker_valid=running&&state==REQUEST;
assign mesh_word_ready=running&&state==STREAM&&space;
wire emit=running&&space&&
    (state==PASS_HEADER||(state==COPY&&!input_empty)||
     (state==STREAM&&mesh_word_valid));
wire [31:0] emit_data=state==PASS_HEADER?header:
    state==STREAM?mesh_word_data:input_data;
always @(posedge clk) begin
    output_data<=queue[output_addr[QUEUE_BITS-1:0]];
    if(emit)queue[output_wrptr[QUEUE_BITS-1:0]]<=emit_data;
end
always @(posedge clk) begin
    if(!reset_n||soft_reset||ring_reset)begin
        input_rdptr<=(reset_n&&soft_reset&&!ring_reset)?input_wrptr:0;
        output_wrptr<=0;state<=HEADER;header<=0;remaining<=0;field_index<=0;
        mesh_marker_serial<=0;mesh_marker_flags<=0;
        mesh_marker_uofs<=0;mesh_marker_vofs<=0;fault<=0;
    end else if(state==STREAM&&mesh_packet_error)begin
        fault<=1;state<=FAULT;
    end else if(state==STREAM&&mesh_packet_done)begin
        // Completion is a pulse from an independent producer. Capture it
        // even if software disables the consumer on this exact cycle.
        state<=HEADER;
    end else if(running)begin
        if(take)input_rdptr<=input_rdptr+1'b1;
        if(emit)output_wrptr<=output_wrptr+1'b1;
        case(state)
            HEADER:if(take)begin
                header<=input_data;remaining<=input_data[12:0];field_index<=0;
                if(input_data[31:24]==8'h62)begin
                    if(input_data!=32'h62000004)begin fault<=1;state<=FAULT;end
                    else state<=FIELDS;
                end else state<=PASS_HEADER;
            end
            PASS_HEADER:if(emit)state<=remaining==0?HEADER:COPY;
            COPY:if(take)begin
                remaining<=remaining-1'b1;
                if(remaining==1)state<=HEADER;
            end
            FIELDS:if(take)begin
                case(field_index)
                    0:mesh_marker_serial<=input_data;
                    1:mesh_marker_flags<=input_data;
                    2:mesh_marker_uofs<=input_data;
                    3:mesh_marker_vofs<=input_data;
                endcase
                field_index<=field_index+1'b1;
                if(field_index==3)state<=REQUEST;
            end
            REQUEST:if(mesh_marker_error)begin fault<=1;state<=FAULT;end
                else if(mesh_marker_ready)state<=mesh_packet_done?HEADER:STREAM;
            STREAM:if(mesh_packet_error)begin fault<=1;state<=FAULT;end
                else if(mesh_packet_done)state<=HEADER;
            default:begin fault<=1;state<=FAULT;end
        endcase
    end
end
`ifndef SYNTHESIS
always @(posedge clk)if(running)begin
    if(used>=(1<<QUEUE_BITS))$fatal(1,"mesh output FIFO overflow");
    if(state==STREAM&&mesh_packet_done&&mesh_word_valid)
        $fatal(1,"mesh producer completion overlaps output");
end
`endif
endmodule
`default_nettype wire
