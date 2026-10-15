// Bounded executable LOAD + original TRI1/TRI2 + exact cull task engine.
// Original command fetching and GPU packet conversion are separate ready/valid
// consumers. The retained raw view and survivors cannot be overwritten before
// the owner acknowledges completed output copy or ordered whole-task repair.
// Scalar context is latched at task acceptance. Matrix writes are accepted
// only while task_ready and task_valid==0; sixteen initialized words are needed.
// reset is FABRIC reset, shared with AXI responder. cancel never resets DMA.
module geometry_mesh_task_engine #(parameter CONTEXT_SHADOW=0,
 parameter [31:0] SOURCE_MIN_ADDRESS=32'h10000000,
 parameter [32:0] SOURCE_END_ADDRESS=33'h014000000
)(
 input wire clk,reset,cancel,
 input wire matrix_diag_read,
 input wire shadow_we,input wire [4:0] shadow_addr,input wire [31:0] shadow_data,
 input wire task_valid,task_load_only,output wire task_ready,
 input wire [31:0] task_serial,task_vtx_w0,task_vtx_w1,
 input wire [6:0] task_command_count,input wire [1:0] task_cull_mode,
 input wire [7:0] task_unsupported_features,
 input wire command_valid,output wire command_ready,
 input wire [31:0] command_w0,command_w1,
 input wire matrix_we,input wire [3:0] matrix_addr,input wire [31:0] matrix_data,
 input wire [31:0] aspect,coefficient_x,coefficient_y,coefficient_z,
 input wire [23:0] ambient_rgb,diffuse_rgb,input wire lighting_enabled,
 input wire [15:0] scale_u,scale_v,
 output wire m_arvalid,input wire m_arready,output wire [31:0] m_araddr,
 output wire [7:0] m_arlen,input wire m_rvalid,output wire m_rready,
 input wire [31:0] m_rdata,input wire [1:0] m_rresp,input wire m_rlast,
 input wire [5:0] list_addr,output wire [14:0] list_indices,
 input wire snapshot_access,snapshot_we,input wire [8:0] snapshot_addr,
 input wire [15:0] snapshot_wdata,output wire [15:0] snapshot_rdata,
 input wire [7:0] view_addr,output wire [31:0] view_data,
 output wire busy,preflight_valid,fallback_valid,
 output wire [3:0] fallback_reason,output wire [31:0] serial,
 output wire [31:0] owned_mask,used_mask,
 output wire [6:0] live_count,checked_count,clip_rejected,cull_rejected,
 input wire task_ack,input wire [31:0] ack_serial,output wire done,
 output wire raw_complete,raw_unsupported,raw_all_inside,output wire [1:0] raw_dma_error
);
 wire c_task_ready;
 assign task_ready=c_task_ready&&!matrix_we;
 wire take_task=task_valid&&task_ready;
 reg [15:0] matrix_valid;
 reg [31:0] held_aspect,held_cx,held_cy,held_cz;
 reg [23:0] held_ambient,held_diffuse;
 reg held_lit;
 reg [15:0] held_su,held_sv;
 wire load_start,raw_busy,load_done,load_unsupported;
 wire [31:0] load_base;
 wire [5:0] load_count;
 wire [4:0] load_dest;
 wire [1:0] load_dma_error;
 assign raw_complete=load_done;assign raw_unsupported=load_unsupported;assign raw_dma_error=load_dma_error;
 wire check_valid,check_ready,result_valid,result_ready,result_fallback;
 wire [14:0] check_indices;
 wire [1:0] check_cull_mode,result_reject_class;
 wire [2:0] result_reason;
 always @(posedge clk)begin
  if(reset)begin
   matrix_valid<=0;held_aspect<=0;held_cx<=0;held_cy<=0;held_cz<=0;
   held_ambient<=0;held_diffuse<=0;held_lit<=0;held_su<=0;held_sv<=0;
  end else begin
   if(matrix_we&&c_task_ready&&!task_valid)matrix_valid[matrix_addr]<=1;
   if(take_task)begin
    held_aspect<=aspect;held_cx<=coefficient_x;held_cy<=coefficient_y;held_cz<=coefficient_z;
    held_ambient<=ambient_rgb;held_diffuse<=diffuse_rgb;held_lit<=lighting_enabled;held_su<=scale_u;held_sv<=scale_v;
   end
  end
 end
 geometry_mesh_task_ctrl ctrl(
  .clk(clk),.reset(reset),.cancel(cancel),.task_valid(task_valid&&!matrix_we),.task_ready(c_task_ready),
  .task_load_only(task_load_only),.task_serial(task_serial),.task_vtx_w0(task_vtx_w0),.task_vtx_w1(task_vtx_w1),
  .task_command_count(task_command_count),.task_cull_mode(task_cull_mode),
  .task_unsupported_features(task_unsupported_features|(matrix_valid==16'hffff ? 8'h00 : 8'h80)),
  .command_valid(command_valid),.command_ready(command_ready),.command_w0(command_w0),.command_w1(command_w1),
  .load_start(load_start),.load_base(load_base),.load_count(load_count),.load_dest(load_dest),
  .load_busy(raw_busy),.load_done(load_done),.load_unsupported(load_unsupported),.load_dma_error(load_dma_error),
  .check_valid(check_valid),.check_ready(check_ready),.check_indices(check_indices),.check_cull_mode(check_cull_mode),
  .result_valid(result_valid),.result_ready(result_ready),.result_reject_class(result_reject_class),
  .result_fallback(result_fallback),.result_reason(result_reason),
  .list_addr(list_addr),.list_indices(list_indices),
  .snapshot_access(snapshot_access),.snapshot_we(snapshot_we),.snapshot_addr(snapshot_addr),
  .snapshot_wdata(snapshot_wdata),.snapshot_rdata(snapshot_rdata),.busy(busy),.preflight_valid(preflight_valid),
  .fallback_valid(fallback_valid),.fallback_reason(fallback_reason),.serial(serial),.owned_mask(owned_mask),.used_mask(used_mask),
  .live_count(live_count),.checked_count(checked_count),.clip_rejected(clip_rejected),.cull_rejected(cull_rejected),
  .task_ack(task_ack),.ack_serial(ack_serial),.done(done));
 geometry_mesh_raw_frontend #(.CONTEXT_SHADOW(CONTEXT_SHADOW),.CAPTURE_CONTEXT(0),.USE_OWNERSHIP(1),
  .SOURCE_MIN_ADDRESS(SOURCE_MIN_ADDRESS),.SOURCE_END_ADDRESS(SOURCE_END_ADDRESS)) frontend(
  .clk(clk),.reset(reset),.matrix_diag_read(matrix_diag_read),
  .shadow_we(shadow_we&&c_task_ready&&!task_valid),.shadow_addr(shadow_addr),.shadow_data(shadow_data),.slot_invalidate(1'b0),.invalidate_mask(32'b0),
  .matrix_we(matrix_we&&c_task_ready&&!task_valid),.matrix_addr(matrix_addr),.matrix_data(matrix_data),
  .start(load_start),.job_base(load_base),.count(load_count),.dest(load_dest),
  .aspect(held_aspect),.coefficient_x(held_cx),.coefficient_y(held_cy),.coefficient_z(held_cz),
  .ambient_rgb(held_ambient),.diffuse_rgb(held_diffuse),.lighting_enabled(held_lit),.scale_u(held_su),.scale_v(held_sv),
  .m_arvalid(m_arvalid),.m_arready(m_arready),.m_araddr(m_araddr),.m_arlen(m_arlen),
  .m_rvalid(m_rvalid),.m_rready(m_rready),.m_rdata(m_rdata),.m_rresp(m_rresp),.m_rlast(m_rlast),
  .dma_request_start(),.dma_request_base(),.dma_request_words(),
  .dma_response_busy(1'b0),.dma_response_done(1'b0),.dma_response_error(2'b0),
  .dma_response_word_valid(1'b0),.dma_response_word_index(8'b0),.dma_response_word_data(32'b0),
  .output_addr(view_addr),.output_data(view_data),.busy(raw_busy),.done(load_done),.unsupported(load_unsupported),
  .dma_error(load_dma_error),.all_inside(raw_all_inside),.cycles(),.compute_cycles(),.valid_slots(),
  .tri_valid(check_valid),.tri_ready(check_ready),.tri_indices(check_indices),.owned_slots(owned_mask),
  .tri_cull_mode(check_cull_mode),.tri_out_valid(result_valid),.tri_out_ready(result_ready),
  .tri_reject_class(result_reject_class),.tri_clip_or(),.tri_cross_bits(),.tri_unsupported(),
  .tri_fallback(result_fallback),.tri_reason(result_reason),
  .fma_request_valid(),.fma_request_a(),.fma_request_b(),.fma_request_c(),.fma_request_multiply(),
  .fma_response_valid(1'b0),.fma_response_data(32'b0),.fma_response_unsupported(1'b0));
endmodule
