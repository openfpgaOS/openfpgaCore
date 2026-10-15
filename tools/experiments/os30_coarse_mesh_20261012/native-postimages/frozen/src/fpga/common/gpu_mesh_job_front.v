// AJ03 ordered context/job expansion. One producer, one ordinary output FIFO.
`default_nettype none
module gpu_mesh_job_front #(parameter ADDR_BITS=12, parameter QUEUE_BITS=9)(
 input wire clk,reset_n,enable,soft_reset,ring_reset,
 input wire [ADDR_BITS-1:0] input_wrptr,
 output reg [ADDR_BITS-1:0] input_rdptr,
 output wire [ADDR_BITS-1:0] input_addr,input wire [31:0] input_data,
 input wire [ADDR_BITS-1:0] output_rdptr,output_addr,
 output reg [31:0] output_data,output reg [ADDR_BITS-1:0] output_wrptr,
 output wire busy, output reg fault,
 output wire mesh_ctx_valid,input wire mesh_ctx_ready,
 output wire [4:0] mesh_ctx_index,output wire [31:0] mesh_ctx_data,
 output wire mesh_job_valid,input wire mesh_job_ready,
 output reg [31:0] mesh_job_seq,mesh_job_vertex,mesh_job_vtx_w0,mesh_job_commands,
 output reg [31:0] mesh_job_control,mesh_job_flags,mesh_job_uofs,mesh_job_vofs,
 output reg [31:0] mesh_job_cpu_vertex,mesh_job_cpu_commands,mesh_job_alpha,
 input wire mesh_word_valid,input wire [31:0] mesh_word_data,output wire mesh_word_ready,
 input wire mesh_job_done,mesh_job_fault
);
 localparam HEADER=0,PASS_HEADER=1,COPY=2,CTX_BASE=3,CTX_DATA=4,FIELDS=5,REQUEST=6,STREAM=7,FAULT=8;
 reg [3:0] state,field_index;
 reg [31:0] header;
 reg [12:0] remaining;
 reg [4:0] context_index;
 reg context_pending,group_fields;
 (* ramstyle="M10K, no_rw_check" *) reg [31:0] queue[0:(1<<QUEUE_BITS)-1];
 wire input_empty=input_rdptr==input_wrptr;
 wire [ADDR_BITS-1:0] used=output_wrptr-output_rdptr;
 wire space=used<((1<<QUEUE_BITS)-1);
 wire running=reset_n&&!soft_reset&&!ring_reset&&enable&&!fault;
 wire take=running&&!input_empty&&(state==HEADER||state==FIELDS||state==CTX_BASE||
   (state==COPY&&space)||(state==CTX_DATA&&mesh_ctx_ready));
 assign input_addr=input_rdptr+{{(ADDR_BITS-1){1'b0}},take};
 assign busy=!input_empty||state!=HEADER||fault;
 assign mesh_ctx_valid=running&&state==CTX_DATA&&!input_empty;
 assign mesh_ctx_index=context_index;
 assign mesh_ctx_data=input_data;
 assign mesh_job_valid=running&&state==REQUEST;
 assign mesh_word_ready=running&&state==STREAM&&space;
 wire emit=running&&space&&(state==PASS_HEADER||(state==COPY&&!input_empty)||
   (state==STREAM&&mesh_word_valid));
 wire [31:0] emit_data=state==PASS_HEADER?header:state==STREAM?mesh_word_data:input_data;
 // Passive stream taps are exactly FIFO write handshakes.
 wire output_push=emit;
 wire [31:0] output_word=emit_data;
 always @(posedge clk)begin
  output_data<=queue[output_addr[QUEUE_BITS-1:0]];
  if(emit)queue[output_wrptr[QUEUE_BITS-1:0]]<=emit_data;
 end
 always @(posedge clk)begin
  if(!reset_n||soft_reset||ring_reset)begin
   input_rdptr<=(reset_n&&soft_reset&&!ring_reset)?input_wrptr:0;
   output_wrptr<=0;context_pending<=0;group_fields<=0;state<=HEADER;field_index<=0;header<=0;remaining<=0;context_index<=0;fault<=0;
   mesh_job_seq<=0;mesh_job_vertex<=0;mesh_job_vtx_w0<=0;mesh_job_commands<=0;
   mesh_job_control<=0;mesh_job_flags<=0;mesh_job_uofs<=0;mesh_job_vofs<=0;
   mesh_job_cpu_vertex<=0;mesh_job_cpu_commands<=0;mesh_job_alpha<=0;
  end else begin
   if(emit)output_wrptr<=output_wrptr+1'b1;
   if(take)input_rdptr<=input_rdptr+1'b1;
   if(state==STREAM&&mesh_job_fault)begin state<=FAULT;fault<=1;end
   else if(state==STREAM&&mesh_job_done)state<=HEADER;
   else if(running)case(state)
    HEADER:if(take)begin
     header<=input_data;remaining<=input_data[12:0];field_index<=0;
     if(context_pending&&input_data[31:24]!=8'h64&&input_data[31:24]!=8'h65)begin state<=FAULT;fault<=1;end
     else if(input_data[31:24]==8'h63)begin
      if(input_data!=32'h6300001d)begin state<=FAULT;fault<=1;end
      else begin state<=CTX_BASE;context_pending<=1;end
     end else if(input_data[31:24]==8'h64)begin
      if(input_data!=32'h6400000b||!context_pending)begin state<=FAULT;fault<=1;end else begin state<=FIELDS;context_pending<=0;group_fields<=0;end
     end else if(input_data[31:24]==8'h65)begin
      if(input_data!=32'h65000007||!context_pending)begin state<=FAULT;fault<=1;end
      else begin state<=FIELDS;context_pending<=0;group_fields<=1;mesh_job_control<=32'h80000000;mesh_job_commands<=0;mesh_job_cpu_vertex<=0;mesh_job_cpu_commands<=0;end
     end else state<=PASS_HEADER;
    end
    PASS_HEADER:if(emit)state<=remaining==0?HEADER:COPY;
    COPY:if(take)begin remaining<=remaining-1'b1;if(remaining==1)state<=HEADER;end
    CTX_BASE:if(take)begin
     if(input_data!=0)begin state<=FAULT;fault<=1;end
     else begin context_index<=input_data[4:0];remaining<=remaining-1'b1;state<=CTX_DATA;end
    end
    CTX_DATA:if(take)begin context_index<=context_index+1'b1;remaining<=remaining-1'b1;if(remaining==1)state<=HEADER;end
    FIELDS:if(take)begin
     if(group_fields)case(field_index)
      0:mesh_job_seq<=input_data;1:mesh_job_vertex<=input_data;2:mesh_job_vtx_w0<=input_data;
      3:mesh_job_flags<=input_data;4:mesh_job_uofs<=input_data;5:mesh_job_vofs<=input_data;6:mesh_job_alpha<=input_data;
     endcase
     else case(field_index)
      0:mesh_job_seq<=input_data;1:mesh_job_vertex<=input_data;2:mesh_job_vtx_w0<=input_data;
      3:mesh_job_commands<=input_data;4:mesh_job_control<=input_data;5:mesh_job_flags<=input_data;
      6:mesh_job_uofs<=input_data;7:mesh_job_vofs<=input_data;8:mesh_job_cpu_vertex<=input_data;
      9:mesh_job_cpu_commands<=input_data;10:mesh_job_alpha<=input_data;
     endcase
     field_index<=field_index+1'b1;if(field_index==(group_fields?6:10))state<=REQUEST;
     // Bit31 is an internal GROUP tag, never legal in an ordinary JOB.
     if(!group_fields&&field_index==4&&input_data[31])begin state<=FAULT;fault<=1;end
    end
    REQUEST:if(mesh_job_fault)begin fault<=1;state<=FAULT;end
     else if(mesh_job_ready)state<=mesh_job_done?HEADER:STREAM;
    STREAM:;
    default:begin fault<=1;state<=FAULT;end
   endcase
  end
 end
`ifndef SYNTHESIS
 always @(posedge clk)if(running)begin
  if(used>=(1<<QUEUE_BITS))$fatal(1,"AJ01 output overflow");
  if(state==STREAM&&mesh_job_done&&mesh_word_valid)$fatal(1,"AJ01 done with word");
 end
`endif
endmodule
`default_nettype wire
