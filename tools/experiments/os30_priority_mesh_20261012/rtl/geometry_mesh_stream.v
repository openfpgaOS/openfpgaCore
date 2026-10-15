// Isolated end-to-end source/command DMA + geometry + packet prototype.
// One physical AXI read master has sequential vertex-LOAD then command-fetch
// ownership. An early geometry exception cancels and DRAINS command prefetch
// before exposing fallback or accepting task ACK. Original source/context and
// command bytes must remain immutable until the matching task ACK.
// Consumer/fabric reset must reset the AXI responder and packet destination.
// VALIDATE runs a complete no-output pass; publication waits the matching ring marker.
// Ordinary cancel after publication is ignored. consumer_reset discards the packet
// together with its GPU consumer, while the raw master and command fetch drain.
module geometry_mesh_stream #(
 parameter [31:0] SOURCE_MIN_ADDRESS=32'h10000000,
 parameter [32:0] SOURCE_END_ADDRESS=33'h014000000
)(
 input wire clk,reset,cancel,consumer_reset,
 input wire matrix_diag_read,
 input wire task_valid,task_load_only,output wire task_ready,output wire raw_ready,
 input wire [31:0] task_serial,task_vtx_w0,task_vtx_w1,
 input wire [6:0] task_command_count,input wire [1:0] task_cull_mode,
 input wire [7:0] task_unsupported_features,
 input wire matrix_we,input wire [3:0] matrix_addr,input wire [31:0] matrix_data,
 input wire [31:0] aspect,coefficient_x,coefficient_y,coefficient_z,
 input wire [23:0] ambient_rgb,diffuse_rgb,input wire lighting_enabled,
 input wire [15:0] scale_u,scale_v,
 output wire m_arvalid,input wire m_arready,output wire [31:0] m_araddr,
 output wire [7:0] m_arlen,input wire m_rvalid,output wire m_rready,
 input wire [31:0] m_rdata,input wire [1:0] m_rresp,input wire m_rlast,
 input wire [5:0] list_addr,output wire [14:0] list_indices,
 input wire [7:0] view_addr,output wire [31:0] view_data,
 output wire busy,preflight_valid,fallback_valid,
 output wire [3:0] fallback_reason,output wire [31:0] serial,
 output wire [31:0] owned_mask,used_mask,
 output wire [6:0] live_count,checked_count,clip_rejected,cull_rejected,
 input wire task_ack,input wire [31:0] ack_serial,output wire done,
 input wire [31:0] task_command_source,
 input wire packet_validate,packet_publish,input wire [31:0] flags_rgb,u_offset,v_offset,
 output wire packet_validated,packet_published,packet_snapshot_valid,output wire [8:0] packet_words,
 output wire raw_complete,raw_unsupported,raw_all_inside,output wire [1:0] raw_dma_error,
 output wire word_valid,input wire word_ready,output wire [31:0] word_data,
 output wire packet_busy,packet_done,packet_error,
 output wire fetch_busy,fetch_error
);
 wire engine_ready,engine_busy,engine_preflight,engine_fallback,engine_done;
 wire [3:0] engine_reason;
 wire command_valid,command_ready;wire [31:0] command_w0,command_w1;
 wire raw_arvalid,raw_rready;wire [31:0] raw_araddr;wire [7:0] raw_arlen;
 wire fetch_arvalid,fetch_rready,fetch_done;wire [31:0] fetch_araddr;wire [7:0] fetch_arlen;
 reg fetch_started,validation_started,publication_started,packet_complete,engine_load_only;
 wire packet_store_we,packet_raw_read_active;
 wire [8:0] packet_store_addr;
 wire [15:0] packet_store_wdata,packet_store_rdata;
 reg [31:0] command_source;reg [6:0] command_count;
 wire fetch_start=engine_busy&&command_ready&&!fetch_started&&!cancel;
 wire packet_take=packet_validate&&preflight_valid&&!validation_started&&!cancel&&!consumer_reset;
 wire publish_take=packet_publish&&packet_validated&&!publication_started&&!consumer_reset;
 // Validation snapshots all converted vertices into the survivor M10K.
 // Publication reads only that store, so LOAD-only tasks may reuse raw/FMA.
 wire packet_owned=packet_take||packet_raw_read_active;
 wire engine_cancel=((cancel||consumer_reset)&&!engine_load_only)||(fetch_started&&fetch_error);
 assign packet_published=publication_started;
 wire safe_ack=task_ack&&!fetch_busy&&(engine_load_only||!packet_busy||packet_snapshot_valid);
 wire packet_reset=reset||consumer_reset||
   ((cancel||safe_ack)&&!packet_snapshot_valid);
 assign raw_ready=engine_ready&&!fetch_busy&&!cancel;
 assign task_ready=raw_ready&&(!packet_busy||task_load_only);
 assign busy=engine_busy||fetch_busy||packet_busy;
 assign preflight_valid=engine_preflight&&!fetch_busy&&!fetch_error;
 assign fallback_valid=engine_fallback&&!fetch_busy&&(engine_load_only||!packet_busy);
 assign fallback_reason=fetch_started&&fetch_error?4'd3:engine_reason;
 assign done=engine_done;
 assign m_arvalid=fetch_started?fetch_arvalid:raw_arvalid;
 assign m_araddr=fetch_started?fetch_araddr:raw_araddr;
 assign m_arlen=fetch_started?fetch_arlen:raw_arlen;
 assign m_rready=fetch_started?fetch_rready:raw_rready;
 wire [7:0] packet_view_addr;wire [5:0] packet_list_addr;
 always @(posedge clk)begin
  if(reset)begin fetch_started<=0;validation_started<=0;publication_started<=0;packet_complete<=0;engine_load_only<=0;command_source<=0;command_count<=0;end
  else begin
   if(task_valid&&task_ready)begin
    fetch_started<=0;engine_load_only<=task_load_only;
    if(!task_load_only)begin validation_started<=0;publication_started<=0;packet_complete<=0;end
    command_source<=task_command_source;command_count<=task_command_count;
   end
   if(fetch_start)fetch_started<=1;
   if(packet_take)validation_started<=1;
   if(publish_take)publication_started<=1;
   if(consumer_reset)begin validation_started<=0;publication_started<=0;packet_complete<=0;end
   if(packet_done)packet_complete<=1;
   if(engine_done)fetch_started<=0;
  end
 end
 geometry_mesh_command_fetch #(.SOURCE_MIN_ADDRESS(SOURCE_MIN_ADDRESS),.SOURCE_END_ADDRESS(SOURCE_END_ADDRESS)) fetch(.clk(clk),.reset(reset),.start(fetch_start),
  .cancel(engine_cancel||engine_fallback),.source(command_source),.count(command_count),
  .busy(fetch_busy),.done(fetch_done),.error(fetch_error),
  .command_valid(command_valid),.command_ready(command_ready),.command_w0(command_w0),.command_w1(command_w1),
  .m_arvalid(fetch_arvalid),.m_arready(m_arready&&fetch_started),.m_araddr(fetch_araddr),.m_arlen(fetch_arlen),
  .m_rvalid(m_rvalid&&fetch_started),.m_rready(fetch_rready),.m_rdata(m_rdata),.m_rresp(m_rresp),.m_rlast(m_rlast));
 geometry_mesh_task_engine #(.SOURCE_MIN_ADDRESS(SOURCE_MIN_ADDRESS),.SOURCE_END_ADDRESS(SOURCE_END_ADDRESS)) engine(
  .clk(clk),
  .reset(reset),
  .cancel(engine_cancel),.matrix_diag_read(matrix_diag_read&&!packet_owned),
  .task_valid(task_valid&&task_ready),
  .task_load_only(task_load_only),
  .task_ready(engine_ready),
  .task_serial(task_serial),
  .task_vtx_w0(task_vtx_w0),
  .task_vtx_w1(task_vtx_w1),
  .task_command_count(task_command_count),
  .task_cull_mode(task_cull_mode),
  .task_unsupported_features(task_unsupported_features),
  .command_valid(command_valid),
  .command_ready(command_ready),
  .command_w0(command_w0),
  .command_w1(command_w1),
  .matrix_we(matrix_we),
  .matrix_addr(matrix_addr),
  .matrix_data(matrix_data),
  .aspect(aspect),
  .coefficient_x(coefficient_x),
  .coefficient_y(coefficient_y),
  .coefficient_z(coefficient_z),
  .ambient_rgb(ambient_rgb),
  .diffuse_rgb(diffuse_rgb),
  .lighting_enabled(lighting_enabled),
  .scale_u(scale_u),
  .scale_v(scale_v),
  .m_arvalid(raw_arvalid),
  .m_arready(m_arready&&!fetch_started),
  .m_araddr(raw_araddr),
  .m_arlen(raw_arlen),
  .m_rvalid(m_rvalid&&!fetch_started),
  .m_rready(raw_rready),
  .m_rdata(m_rdata),
  .m_rresp(m_rresp),
  .m_rlast(m_rlast),
  .list_addr(packet_owned?packet_list_addr:list_addr),
  .list_indices(list_indices),
  .snapshot_access(packet_busy),.snapshot_we(packet_store_we),.snapshot_addr(packet_store_addr),
  .snapshot_wdata(packet_store_wdata),.snapshot_rdata(packet_store_rdata),
  .view_addr(packet_owned?packet_view_addr:view_addr),
  .view_data(view_data),
  .busy(engine_busy),
  .preflight_valid(engine_preflight),
  .fallback_valid(engine_fallback),
  .fallback_reason(engine_reason),
  .serial(serial),
  .owned_mask(owned_mask),
  .used_mask(used_mask),
  .live_count(live_count),
  .checked_count(checked_count),
  .clip_rejected(clip_rejected),
  .cull_rejected(cull_rejected),
  .task_ack(safe_ack),
  .ack_serial(ack_serial),
  .done(engine_done),.raw_complete(raw_complete),.raw_unsupported(raw_unsupported),
  .raw_all_inside(raw_all_inside),.raw_dma_error(raw_dma_error));
 geometry_mesh_packet packet(.clk(clk),.reset(packet_reset),.start(packet_take),
  .publish(publish_take),.validated(packet_validated),.snapshot_valid(packet_snapshot_valid),
  .raw_read_active(packet_raw_read_active),.store_addr(packet_store_addr),.store_we(packet_store_we),
  .store_wdata(packet_store_wdata),.store_rdata(packet_store_rdata),.payload_words(packet_words),
  .used_mask(used_mask),.flags_rgb(flags_rgb),.u_offset(u_offset),.v_offset(v_offset),.live_count(live_count),
  .view_addr(packet_view_addr),.view_data(view_data),.list_addr(packet_list_addr),.list_indices(list_indices),
  .word_valid(word_valid),.word_ready(word_ready),.word_data(word_data),
  .busy(packet_busy),.done(packet_done),.error(packet_error));
endmodule
