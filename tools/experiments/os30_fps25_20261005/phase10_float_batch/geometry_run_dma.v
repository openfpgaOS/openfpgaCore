// Private original-Gfx reader + atomic RUN precheck. The single shared M10K
// holds survivors0..63, unpublished packet64..79, and command chunk80..95.
// AXI bursts are completely drained BEFORE arithmetic; a slow triangle can
// never monopolize the memory arbiter by holding an accepted R burst open.
// Fabric reset must reset the connected AXI responder too.
module geometry_run_dma #(parameter EXTERNAL_DMA=0) (
 input wire clk,reset,start,input wire [31:0] command_base,
 input wire [6:0] command_count,input wire [1:0] cull_mode,
 output wire m_arvalid,input wire m_arready,output wire [31:0] m_araddr,
 output wire [7:0] m_arlen,input wire m_rvalid,output wire m_rready,
 input wire [31:0] m_rdata,input wire [1:0] m_rresp,input wire m_rlast,
 // EXTERNAL_DMA: one parent-owned reader, held for this complete operation.
 output wire dma_request_start,output wire [31:0] dma_request_base,
 output wire [7:0] dma_request_words,
 input wire dma_response_busy,dma_response_done,input wire [1:0] dma_response_error,
 input wire dma_response_word_valid,input wire [7:0] dma_response_word_index,
 input wire [31:0] dma_response_word_data,
 output wire check_valid,input wire check_ready,output wire [14:0] check_indices,
 output wire [1:0] check_cull_mode,input wire result_valid,output wire result_ready,
 input wire [1:0] result_reject_class,input wire result_fallback,
 input wire [5:0] list_addr,output wire [14:0] list_indices,
 input wire packet_access,packet_write,input wire [3:0] packet_write_addr,
 input wire [31:0] packet_write_data,input wire packet_read_valid,
 input wire [3:0] packet_read_addr,output wire [31:0] packet_read_data,
 output wire busy,output reg done,fallback,invalid,output reg [1:0] dma_error,
 output reg [31:0] prefix_end,output wire [6:0] accepted_commands,live_triangles,
 output wire [6:0] checked_triangles,clip_rejected,cull_rejected,accepted_triangles
);
 localparam IDLE=0,ARM=1,NEED=2,DMA_ARM=3,DMA_WAIT=4,READ0=5,READ1=6,SEND=7,FAIL=8,FINISH=9;
 reg [3:0] state;
 reg [31:0] cursor,w0;
 reg [6:0] requested,issued;
 reg [1:0] mode;
 reg [4:0] chunk_words,read_index;
 wire [6:0] remaining_commands=requested-issued;
 wire [7:0] requested_words=remaining_commands>=8?8'd16:{remaining_commands,1'b0};
 wire [32:0] bytes_available=33'h014000000-{1'b0,cursor};
 wire [7:0] bounded_words=bytes_available<33'd64 ? {2'b0,bytes_available[7:2]} : requested_words;
 wire [7:0] transfer_words=bounded_words<requested_words?bounded_words:requested_words;
 wire d_busy,d_done,d_word;
 wire [1:0] d_error;
 wire [7:0] d_index;
 wire [31:0] d_data,command_q;
 wire p_busy,p_done,p_fallback,p_invalid,p_command_ready;
 wire [6:0] p_accepted,p_live,p_checked;
 assign busy=state!=IDLE;
 assign accepted_commands=p_accepted;
 assign live_triangles=p_live;
 assign checked_triangles=p_checked;
 wire memory_access=state==DMA_WAIT || state==READ0 || state==READ1 || state==SEND;
 wire [3:0] memory_addr=state==DMA_WAIT?d_index[3:0]:
      state==READ0?read_index[3:0]:read_index[3:0]+4'd1;
 assign dma_request_start=state==DMA_ARM;
 assign dma_request_base=cursor;
 assign dma_request_words=transfer_words;
 generate if(EXTERNAL_DMA)begin:g_external_dma
  assign d_busy=dma_response_busy;assign d_done=dma_response_done;
  assign d_error=dma_response_error;assign d_word=dma_response_word_valid;
  assign d_index=dma_response_word_index;assign d_data=dma_response_word_data;
  assign m_arvalid=1'b0;assign m_araddr=32'b0;assign m_arlen=8'b0;assign m_rready=1'b0;
 end else begin:g_internal_dma
 raw_vtx_dma #(.VARIABLE_WORDS(1)) fetch (
  .clk(clk),.reset(reset),.start(state==DMA_ARM),.job_base(cursor),.job_count(6'd0),.job_word_count(transfer_words),
  .busy(d_busy),.done(d_done),.error(d_error),
  .m_arvalid(m_arvalid),.m_arready(m_arready),.m_araddr(m_araddr),.m_arlen(m_arlen),
  .m_rvalid(m_rvalid),.m_rready(m_rready),.m_rdata(m_rdata),.m_rresp(m_rresp),.m_rlast(m_rlast),
  .word_valid(d_word),.word_ready(1'b1),.word_index(d_index),.word_data(d_data));
 end endgenerate
 geometry_run_precheck #(.COMMAND_BUFFER(1)) precheck (
  .clk(clk),.reset(reset),.start(state==ARM),.command_count(requested),.triangle_budget(requested),.cull_mode(mode),
  .command_valid(state==SEND||state==FAIL),.command_ready(p_command_ready),
  .command_w0(state==FAIL?32'b0:w0),.command_w1(command_q),
  .check_valid(check_valid),.check_ready(check_ready),.check_indices(check_indices),.check_cull_mode(check_cull_mode),
  .result_valid(result_valid),.result_ready(result_ready),.result_reject_class(result_reject_class),.result_fallback(result_fallback),
  .list_addr(list_addr),.list_indices(list_indices),.packet_access(packet_access&&!busy),.packet_write(packet_write),
  .packet_write_addr(packet_write_addr),.packet_write_data(packet_write_data),.packet_read_valid(packet_read_valid),
  .packet_read_addr(packet_read_addr),.packet_read_data(packet_read_data),
  .command_memory_access(memory_access),.command_memory_write(state==DMA_WAIT&&d_word),
  .command_memory_addr(memory_addr),.command_memory_data(d_data),.command_memory_q(command_q),
  .busy(p_busy),.done(p_done),.fallback(p_fallback),.invalid(p_invalid),
  .accepted_commands(p_accepted),.live_triangles(p_live),.checked_triangles(p_checked),
  .clip_rejected(clip_rejected),.cull_rejected(cull_rejected),.accepted_triangles(accepted_triangles));
 always @(posedge clk)begin
  if(reset)begin
   state<=IDLE;cursor<=0;w0<=0;requested<=0;issued<=0;mode<=0;chunk_words<=0;read_index<=0;
   done<=0;fallback<=0;invalid<=0;dma_error<=0;prefix_end<=0;
  end else begin
   done<=0;
   case(state)
    IDLE:if(start)begin
     requested<=command_count;issued<=0;mode<=cull_mode;cursor<=command_base;prefix_end<=command_base;
     chunk_words<=0;read_index<=0;fallback<=0;invalid<=0;dma_error<=0;
     // Invalid requests still start precheck with count0 to reset its public
     // result counters; they generate no AXI request and complete explicitly.
     if(command_count==0||command_count>64||command_base[2:0]!=0||
        command_base<32'h10000000||command_base>=32'h14000000)begin
      requested<=0;invalid<=1;dma_error<=1;
     end
     state<=ARM;
    end
    ARM:state<=NEED;
    NEED:if(p_done)begin
     fallback<=p_fallback;invalid<=invalid||p_invalid;
     // cursor includes a consumed unsupported command; only the committed
     // original-command count defines the caller's exact restart boundary.
     prefix_end<=prefix_end+{22'b0,p_accepted,3'b0};state<=FINISH;
    end else if(p_command_ready)begin
     if(read_index>=chunk_words)begin
      if(cursor>=32'h14000000 || transfer_words<2)begin dma_error<=1;state<=FAIL;end
      else begin chunk_words<=transfer_words[4:0];read_index<=0;state<=DMA_ARM;end
     end else state<=READ0;
    end
    DMA_ARM:state<=DMA_WAIT;
    DMA_WAIT:if(d_done)begin
     if(d_error!=0)begin dma_error<=d_error;state<=FAIL;end
     else state<=READ0;
    end
    READ0:state<=READ1;
    READ1:begin w0<=command_q;state<=SEND;end
    SEND:if(p_command_ready)begin
     read_index<=read_index+5'd2;issued<=issued+1'b1;cursor<=cursor+32'd8;state<=NEED;
    end
    FAIL:if(p_command_ready)begin invalid<=1;state<=NEED;end
    FINISH:begin done<=1;state<=IDLE;end
   endcase
  end
 end
endmodule
