// Private LOAD + exact cull integration. Original vertex DMA, transform,
// lighting and triangle culling share the baseline FMA and raw memories. This is not
// yet the command-ring/RUN/legacy-packet frontend. Results require valid_slots.
module geometry_mesh_raw_frontend #(parameter CONTEXT_SHADOW=0,parameter EXTERNAL_FMA=0,parameter CAPTURE_CONTEXT=1,parameter USE_OWNERSHIP=0,parameter ENABLE_INVALIDATE=0,parameter EXTERNAL_DMA=0,
 parameter [31:0] SOURCE_MIN_ADDRESS=32'h10000000,
 parameter [32:0] SOURCE_END_ADDRESS=33'h014000000) (
 input wire clk,reset,
 input wire matrix_diag_read,
 input wire shadow_we,input wire [4:0] shadow_addr,input wire [31:0] shadow_data,
 input wire slot_invalidate,input wire [31:0] invalidate_mask,
 input wire matrix_we,input wire [3:0] matrix_addr,input wire [31:0] matrix_data,
 input wire start,input wire [31:0] job_base,input wire [5:0] count,input wire [4:0] dest,
 input wire [31:0] aspect,coefficient_x,coefficient_y,coefficient_z,
 input wire [23:0] ambient_rgb,diffuse_rgb,input wire lighting_enabled,
 input wire [15:0] scale_u,scale_v,
 output wire m_arvalid,input wire m_arready,output wire [31:0] m_araddr,
 output wire [7:0] m_arlen,input wire m_rvalid,output wire m_rready,
 input wire [31:0] m_rdata,input wire [1:0] m_rresp,input wire m_rlast,
 // EXTERNAL_DMA: one parent-owned reader, held for this complete operation.
 output wire dma_request_start,output wire [31:0] dma_request_base,
 output wire [7:0] dma_request_words,
 input wire dma_response_busy,dma_response_done,input wire [1:0] dma_response_error,
 input wire dma_response_word_valid,input wire [7:0] dma_response_word_index,
 input wire [31:0] dma_response_word_data,
 input wire [7:0] output_addr,output wire [31:0] output_data,
 output wire busy,done,unsupported,output wire [1:0] dma_error,
 output wire all_inside,output wire [31:0] cycles,compute_cycles,valid_slots,
 input wire tri_valid,output wire tri_ready,input wire [14:0] tri_indices,
 input wire [31:0] owned_slots,
 input wire [1:0] tri_cull_mode,
 output reg tri_out_valid,input wire tri_out_ready,
 output reg [1:0] tri_reject_class,output reg [5:0] tri_clip_or,
 output reg [31:0] tri_cross_bits,output reg tri_unsupported,tri_fallback,
 output reg [2:0] tri_reason,
 output wire fma_request_valid,output wire [31:0] fma_request_a,fma_request_b,fma_request_c,
 output wire fma_request_multiply,input wire fma_response_valid,
 input wire [31:0] fma_response_data,input wire fma_response_unsupported
);
 localparam IDLE=0,META=1,LAUNCH=2,CULL=3;
 reg [1:0] state;
 reg [14:0] indices;
 reg [1:0] mode;
 reg [17:0] clips;
 reg parity,good_w,equal_w;
 reg [31:0] first_w;
 reg [2:0] meta_index,meta_tag;
 reg meta_draining,meta_read_valid;
 wire raw_busy;
 wire [31:0] usable_slots=USE_OWNERSHIP?(valid_slots&owned_slots):valid_slots;
 wire idle=state==IDLE && !tri_out_valid;
 assign busy=raw_busy || !idle;
 assign tri_ready=idle && !raw_busy && !start;
 wire [4:0] meta_vertex=meta_index[2:1]==0?indices[4:0]:
                      meta_index[2:1]==1?indices[9:5]:indices[14:10];
 wire [7:0] meta_addr={meta_index[0]?3'd3:3'd7,meta_vertex};
 wire c_vertex_valid;
 wire [4:0] c_vertex_index;
 wire [1:0] c_vertex_field;
 wire [7:0] c_vertex_addr={c_vertex_field==2?3'd4:{1'b0,c_vertex_field},c_vertex_index};
 wire [7:0] read_addr=state==META?meta_addr:state==CULL?c_vertex_addr:output_addr;
 wire raw_req,raw_mul,c_req,c_mul;
 wire [31:0] raw_a,raw_b,raw_c,c_a,c_b,c_c;
 wire result_valid,result_bad;
 wire [31:0] result;
 reg [6:0] cull_owner;
 wire c_sread,c_swrite;
 wire [2:0] c_sread_index,c_swrite_index;
 wire [31:0] scratch_data,c_swrite_data;
 wire c_ready,c_out,c_bad;
 wire [1:0] c_reject;
 wire [5:0] c_clip;
 wire [31:0] c_cross;
 geometry_raw_load #(.CONTEXT_SHADOW(CONTEXT_SHADOW),.EXTERNAL_FMA(1),.CAPTURE_CONTEXT(CAPTURE_CONTEXT),.ENABLE_INVALIDATE(ENABLE_INVALIDATE),.EXTERNAL_DMA(EXTERNAL_DMA),
  .SOURCE_MIN_ADDRESS(SOURCE_MIN_ADDRESS),.SOURCE_END_ADDRESS(SOURCE_END_ADDRESS)) raw (
  .clk(clk),.reset(reset),.matrix_diag_read(matrix_diag_read),
  .shadow_we(shadow_we&&idle),.shadow_addr(shadow_addr),.shadow_data(shadow_data),.matrix_we(matrix_we&&idle),.matrix_addr(matrix_addr),.matrix_data(matrix_data),
  .slot_invalidate(slot_invalidate&&idle&&!raw_busy),.invalidate_mask(invalidate_mask),
  .start(start&&idle),.job_base(job_base),.count(count),.dest(dest),
  .aspect(aspect),.coefficient_x(coefficient_x),.coefficient_y(coefficient_y),.coefficient_z(coefficient_z),
  .ambient_rgb(ambient_rgb),.diffuse_rgb(diffuse_rgb),.lighting_enabled(lighting_enabled),.scale_u(scale_u),.scale_v(scale_v),
  .m_arvalid(m_arvalid),.m_arready(m_arready),.m_araddr(m_araddr),.m_arlen(m_arlen),
  .m_rvalid(m_rvalid),.m_rready(m_rready),.m_rdata(m_rdata),.m_rresp(m_rresp),.m_rlast(m_rlast),
  .dma_request_start(dma_request_start),.dma_request_base(dma_request_base),.dma_request_words(dma_request_words),
  .dma_response_busy(dma_response_busy),.dma_response_done(dma_response_done),.dma_response_error(dma_response_error),
  .dma_response_word_valid(dma_response_word_valid),.dma_response_word_index(dma_response_word_index),.dma_response_word_data(dma_response_word_data),
  .output_addr(read_addr),.output_data(output_data),.busy(raw_busy),.done(done),.unsupported(unsupported),
  .dma_error(dma_error),.all_inside(all_inside),.cycles(cycles),.compute_cycles(compute_cycles),.valid_slots(valid_slots),
  .fma_request_valid(raw_req),.fma_request_a(raw_a),.fma_request_b(raw_b),.fma_request_c(raw_c),.fma_request_multiply(raw_mul),
  .fma_response_valid(result_valid&&!cull_owner[6]),.fma_response_data(result),.fma_response_unsupported(result_bad),
  .scratch_read_valid(c_sread),.scratch_read_addr({4'b0,c_sread_index}),.scratch_read_data(scratch_data),
  .scratch_write(c_swrite),.scratch_write_addr({4'b0,c_swrite_index}),.scratch_write_data(c_swrite_data));
 geometry_float_cull_mem cull (
  .clk(clk),.reset(reset),.in_valid(state==LAUNCH),.in_ready(c_ready),
  .vertex_indices(indices),.clip_bits(clips),.negative_w({2'b0,parity}),.cull_mode(mode),
  .out_valid(c_out),.out_ready(state==CULL),.reject_class(c_reject),.clip_or(c_clip),.cross_bits(c_cross),.unsupported(c_bad),
  .vertex_read_valid(c_vertex_valid),.vertex_read_index(c_vertex_index),.vertex_read_field(c_vertex_field),.vertex_data(output_data),
  .scratch_read_valid(c_sread),.scratch_read_index(c_sread_index),.scratch_data(scratch_data),
  .scratch_write(c_swrite),.scratch_write_index(c_swrite_index),.scratch_write_data(c_swrite_data),
  .fma_valid(c_req),.fma_a(c_a),.fma_b(c_b),.fma_c(c_c),.fma_multiply_only(c_mul),
  .fma_result_valid(result_valid&&cull_owner[6]),.fma_result(result),.fma_unsupported(result_bad));
 assign fma_request_valid=raw_req||c_req;
 assign fma_request_a=c_req?c_a:raw_a;assign fma_request_b=c_req?c_b:raw_b;assign fma_request_c=c_req?c_c:raw_c;
 assign fma_request_multiply=c_req?c_mul:raw_mul;
 generate if(EXTERNAL_FMA)begin:g_external
  assign result=fma_response_data;assign result_valid=fma_response_valid;assign result_bad=fma_response_unsupported;
 end else begin:g_internal
 GeometryFmaProbe fma (.clk(clk),.reset(reset),.io_valid(fma_request_valid),
  .io_a(fma_request_a),.io_b(fma_request_b),.io_c(fma_request_c),
  .io_multiplyOnly(fma_request_multiply),.io_result(result),.io_resultValid(result_valid),.io_unsupported(result_bad));
 end endgenerate
 always @(posedge clk)begin
  if(reset)begin
   state<=IDLE;indices<=0;mode<=0;clips<=0;parity<=0;good_w<=0;equal_w<=0;first_w<=0;
   meta_index<=0;meta_tag<=0;meta_draining<=0;meta_read_valid<=0;
   cull_owner<=0;tri_out_valid<=0;tri_reject_class<=0;tri_clip_or<=0;tri_cross_bits<=0;tri_unsupported<=0;tri_fallback<=0;tri_reason<=0;
  end else begin
   cull_owner<={cull_owner[5:0],c_req};
   meta_read_valid<=state==META&&!meta_draining;meta_tag<=meta_index;
   if(tri_out_valid&&tri_out_ready)tri_out_valid<=0;
   case(state)
    IDLE:if(tri_valid&&tri_ready)begin
     indices<=tri_indices;mode<=tri_cull_mode;clips<=0;parity<=0;good_w<=1;equal_w<=1;
     tri_reject_class<=0;tri_clip_or<=0;tri_cross_bits<=0;tri_reason<=0;
     if(!usable_slots[tri_indices[4:0]] || !usable_slots[tri_indices[9:5]] || !usable_slots[tri_indices[14:10]])begin
      tri_out_valid<=1;tri_unsupported<=1;tri_fallback<=1;tri_reason<=1;
     end else begin
      state<=META;meta_index<=0;meta_draining<=0;tri_unsupported<=0;
     end
    end
    META:begin
     if(!meta_draining)begin
      if(meta_index==5)meta_draining<=1;
      else meta_index<=meta_index+1'b1;
     end
     if(meta_read_valid)begin
      if(meta_tag[0])begin
       parity<=parity^(output_data[31]&&|output_data[30:0]);
       if(output_data[31] || output_data[30:23]==0 || output_data>=32'h46fffe00)good_w<=0;
       if(meta_tag==1)first_w<=output_data;
       else if(output_data!=first_w)equal_w<=0;
      end
      else case(meta_tag[2:1])
       0:clips[5:0]<=output_data[5:0];
       1:clips[11:6]<=output_data[5:0];
       2:clips[17:12]<=output_data[5:0];
      endcase
      if(meta_tag==5)state<=LAUNCH;
     end
    end
    LAUNCH:if(c_ready)state<=CULL;
    CULL:if(c_out)begin
     tri_out_valid<=1;tri_reject_class<=c_reject;tri_clip_or<=c_clip;
     tri_cross_bits<=c_cross;tri_unsupported<=c_bad;
     tri_fallback<=c_bad || (c_reject==0 && (c_clip!=0 || !good_w || equal_w));
     tri_reason<=c_bad?3'd4:c_reject!=0?3'd0:c_clip!=0?3'd2:(!good_w||equal_w)?3'd3:3'd0;
     state<=IDLE;
    end
   endcase
  end
 end
endmodule
