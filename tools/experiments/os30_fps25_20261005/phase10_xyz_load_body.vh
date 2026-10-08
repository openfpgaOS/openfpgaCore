 // LOD7 specialization: packed XYZ only, dest0; no hardware UV/RGBA.
 // Position RAM is dead once transform completes. SCAN reuses its existing
 // scratch write port for clip masks, then alias row7 reads the same port.
 localparam IDLE=3'd0,FETCH=3'd1,COMPUTE=3'd2,SCAN=3'd3,INVALID=3'd4;
 reg [2:0] state;
 reg [5:0] n;
 reg [31:0] held_aspect;
 wire [31:0] job_aspect=CAPTURE_CONTEXT?held_aspect:aspect;
 assign busy=state!=IDLE;
 wire valid_range=count!=0 && count<=32 && dest==0;
 wire accept=start && !busy && valid_range;
 wire [7:0] words=({2'b0,count}<<1)+{2'b0,count};
 wire [31:0] incoming_mask=32'hffffffff >> (32-count);
 reg [31:0] job_mask;
 wire fetch_busy,fetch_done;
 wire [1:0] fetch_error;
 wire word_valid;
 wire [7:0] word_index;
 wire [31:0] word_data;
 assign dma_request_start=accept;
 assign dma_request_base=job_base;
 assign dma_request_words=words;
 generate if(EXTERNAL_DMA)begin:g_external_dma
  assign fetch_busy=dma_response_busy;assign fetch_done=dma_response_done;
  assign fetch_error=dma_response_error;assign word_valid=dma_response_word_valid;
  assign word_index=dma_response_word_index;assign word_data=dma_response_word_data;
  assign m_arvalid=1'b0;assign m_araddr=32'b0;assign m_arlen=8'b0;assign m_rready=1'b0;
 end else begin:g_internal_dma
 raw_vtx_dma #(.VARIABLE_WORDS(1),.MIN_ADDRESS(SOURCE_MIN_ADDRESS),.END_ADDRESS(SOURCE_END_ADDRESS)) dma(
  .clk(clk),.reset(reset),.start(accept),.job_base(job_base),.job_count(count),.job_word_count(words),
  .busy(fetch_busy),.done(fetch_done),.error(fetch_error),
  .m_arvalid(m_arvalid),.m_arready(m_arready),.m_araddr(m_araddr),.m_arlen(m_arlen),
  .m_rvalid(m_rvalid),.m_rready(m_rready),.m_rdata(m_rdata),.m_rresp(m_rresp),.m_rlast(m_rlast),
  .word_valid(word_valid),.word_ready(1'b1),.word_index(word_index),.word_data(word_data));
 end endgenerate
 wire calculate=state==FETCH && fetch_done && fetch_error==0;
 wire compute_busy,compute_done,compute_unsupported;
 wire [31:0] compute_data,clip_data;
 reg [1:0] scan_row,read_row;
 reg [4:0] scan_vertex,read_vertex;
 reg scan_draining,read_valid;
 reg [2:0] output_row;
 reg [31:0] clip_w;
 reg [3:0] clip_xy;
 wire scan_issue=state==SCAN && !scan_draining;
 wire [1:0] scan_coordinate=scan_row-2'd1;
 wire [7:0] compute_addr=state==SCAN?{1'b0,scan_coordinate,scan_vertex}:output_addr;
 wire clip_write=state==SCAN && read_valid && read_row==3;
 wire clip_read=state==IDLE && !start && output_addr[7:5]==7;
 wire [31:0] neg_w={~clip_w[31],clip_w[30:0]};
 function float_lt;
  input [31:0] a,b;
  begin
   if(a[30:0]==0 && b[30:0]==0)float_lt=0;
   else if(a[31]!=b[31])float_lt=a[31];
   else float_lt=a[31]?(a[30:0]>b[30:0]):(a[30:0]<b[30:0]);
  end
 endfunction
 wire [1:0] clip_pair={float_lt(compute_data,neg_w),float_lt(clip_w,compute_data)};
 wire [5:0] classification={clip_xy,clip_pair};
 // Direct use of the existing exact batch eliminates the now-unused parent
 // lighting/completion wrapper. Native FMA and reciprocal primitives are intact.
 geometry_float_batch_recip #(.EXTERNAL_FMA(EXTERNAL_FMA)) compute(
  .clk(clk),.reset(reset),.matrix_we(matrix_we&&!busy),.matrix_addr(matrix_addr),.matrix_data(matrix_data),
  .position_we(word_valid),.position_addr(word_index[6:0]),.position_data(word_data),
  .start(calculate),.count(n),.dest(5'd0),.aspect(job_aspect),.output_addr(compute_addr),.output_data(compute_data),
  .busy(compute_busy),.done(compute_done),.unsupported(compute_unsupported),.cycles(compute_cycles),.transform_finished(),
  .fma_request_valid(fma_request_valid),.fma_request_a(fma_request_a),.fma_request_b(fma_request_b),
  .fma_request_c(fma_request_c),.fma_request_multiply(fma_request_multiply),
  .fma_response_valid(fma_response_valid),.fma_response_data(fma_response_data),.fma_response_unsupported(fma_response_unsupported),
  .scratch_read_valid(clip_read),.scratch_read_addr({2'b0,output_addr[4:0]}),.scratch_read_data(clip_data),
  .scratch_write(clip_write),.scratch_write_addr({2'b0,read_vertex}),.scratch_write_data({26'b0,classification}));
 // The generic raw interface is retained for this isolated wrapper only;
 // external scratch clients are not part of LOD7's ABI.
 assign scratch_read_data=32'b0;
 always @*begin
  case(output_row)
   5,6:output_data=32'b0;
   7:output_data={26'b0,clip_data[5:0]};
   default:output_data=compute_data;
  endcase
 end
 always @(posedge clk)begin
  output_row<=output_addr[7:5];
  if(reset)begin
   state<=IDLE;done<=0;unsupported<=0;dma_error<=0;cycles<=0;all_inside<=0;
   n<=0;held_aspect<=0;job_mask<=0;valid_slots<=0;
   scan_row<=0;read_row<=0;scan_vertex<=0;read_vertex<=0;scan_draining<=0;read_valid<=0;
   clip_w<=0;clip_xy<=0;
  end else begin
   done<=0;read_valid<=scan_issue;read_row<=scan_row;read_vertex<=scan_vertex;
   if(busy)cycles<=cycles+1'b1;
   if(ENABLE_INVALIDATE && state==IDLE && slot_invalidate && !start)
    valid_slots<=valid_slots&~invalidate_mask;
   case(state)
    IDLE:if(start)begin
     if(!valid_range)begin state<=INVALID;unsupported<=1;dma_error<=1;all_inside<=0;cycles<=0;valid_slots<=0;end
     else begin
      state<=FETCH;done<=0;unsupported<=0;dma_error<=0;cycles<=0;all_inside<=1;
      n<=count;held_aspect<=aspect;job_mask<=incoming_mask;valid_slots<=0;
     end
    end
    INVALID:begin state<=IDLE;done<=1;end
    FETCH:if(fetch_done)begin
     if(fetch_error!=0)begin dma_error<=fetch_error;unsupported<=1;all_inside<=0;done<=1;state<=IDLE;end
     else state<=COMPUTE;
    end
    COMPUTE:if(compute_done)begin
     if(compute_unsupported)begin unsupported<=1;all_inside<=0;done<=1;state<=IDLE;end
     else begin state<=SCAN;scan_row<=0;scan_vertex<=0;scan_draining<=0;end
    end
    SCAN:begin
     if(scan_issue)begin
      scan_row<=scan_row+1'b1;
      if(scan_row==3)begin
       if({1'b0,scan_vertex}+1'b1==n)scan_draining<=1;
       else scan_vertex<=scan_vertex+1'b1;
      end
     end
     if(read_valid)begin
      case(read_row)
       0:clip_w<=compute_data;
       1:clip_xy[3:2]<=clip_pair;
       2:clip_xy[1:0]<=clip_pair;
       3:begin
        if(classification!=0)all_inside<=0;
        if({1'b0,read_vertex}+1'b1==n)begin state<=IDLE;done<=1;valid_slots<=job_mask;end
       end
      endcase
     end
    end
   endcase
  end
 end
endmodule
