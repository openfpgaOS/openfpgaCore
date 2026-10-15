// AJ02: sole raw engine, autonomous ordered mesh jobs, head-only CPU repair.
module geometry_async_mesh (
 input wire clk,reset,
 input wire reg_wr,input wire [9:0] reg_addr,input wire [31:0] reg_wdata,input wire [3:0] reg_wstrb,
 output reg [31:0] reg_rdata,
 output wire m_arvalid,input wire m_arready,output wire [31:0] m_araddr,output wire [7:0] m_arlen,
 input wire m_rvalid,output wire m_rready,input wire [31:0] m_rdata,input wire [1:0] m_rresp,input wire m_rlast,
 input wire [7:0] view_read_addr,output wire [31:0] view_read_data,
 input wire view_active,output wire view_block,output reg view_valid,output reg [5:0] view_count,
 output wire raw_start,
 input wire mesh_ctx_valid,output wire mesh_ctx_ready,input wire [4:0] mesh_ctx_index,input wire [31:0] mesh_ctx_data,
 input wire mesh_job_valid,output wire mesh_job_ready,
 input wire [31:0] mesh_job_seq,mesh_job_vertex,mesh_job_vtx_w0,mesh_job_commands,
 input wire [31:0] mesh_job_control,mesh_job_flags,mesh_job_uofs,mesh_job_vofs,
 input wire [31:0] mesh_job_cpu_vertex,mesh_job_cpu_commands,mesh_job_alpha,
 output wire mesh_word_valid,output wire [31:0] mesh_word_data,input wire mesh_word_ready,
 output reg mesh_job_done,output wire mesh_job_fault,input wire mesh_consumer_reset,mesh_bridge_fault
);
 reg [31:0] source_base,aspect,cx,cy,cz,projection_cx,projection_cy,projection_hw,projection_hh;
 reg [23:0] ambient,diffuse;
 reg [15:0] scale_u,scale_v,matrix_valid;
 reg [5:0] count;
 reg lit,active,pending,legacy,completed,success,config_error,aborting,protocol_error,raw_seen,validation_started;
 reg [31:0] descriptor_serial,vtx_w0,command_source,configuration,flags_rgb,uofs,vofs,cpu_vertex,cpu_commands,alpha,completed_serial;
 reg lease_pending,lease_granted,lease_release_pending;
 reg context_open;
 reg [4:0] context_next;
 reg [31:0] lease_target,lease_ticket,granted_ticket,epoch;
 reg repair_pending,repair_end_pending;
 reg [31:0] repair_token,repair_ack;
 reg [5:0] repair_index;
 wire stream_ready,stream_raw_ready,stream_busy,preflight,fallback,stream_done;
 reg output_pending,snapshot_detached;
 reg [31:0] output_serial;
 wire [31:0] serial,owned_mask,used_mask;
 wire [6:0] live_count,checked_count,clip_count,cull_count;
 wire [3:0] reason;
 wire raw_done,unsupported,all_inside;
 wire [1:0] dma_error;
 wire packet_busy,packet_done,packet_error,packet_validated,packet_published,packet_snapshot_valid,fetch_busy,fetch_error;
 wire producer_valid;wire [31:0] producer_word;
 wire [8:0] packet_words;
 wire full_write=reg_wr&&reg_wstrb==4'hf;
 wire acquire_request=full_write&&reg_addr==10'h02d&&reg_wdata==1;
 wire release_request=full_write&&reg_addr==10'h02d&&reg_wdata==2;
 // Before the first word, acquisition wins. An opened bundle must finish
 // its JOB before legacy may overwrite the shared matrix/scalar context.
 wire context_allowed=!output_pending&&!active&&!pending&&!aborting&&!lease_granted&&!stream_busy&&!view_active&&!protocol_error&&!mesh_consumer_reset&&
   (context_open||(!lease_pending&&!acquire_request));
 assign mesh_ctx_ready=context_allowed;
 assign mesh_job_ready=context_allowed&&!mesh_ctx_valid;
 wire ctx_take=mesh_ctx_valid&&mesh_ctx_ready;
 wire ctx_order_good=context_open?(context_next<28&&mesh_ctx_index==context_next):(mesh_ctx_index==0);
 wire job_take=mesh_job_valid&&mesh_job_ready;
 wire legacy_write=full_write&&lease_granted&&!active&&!pending&&!aborting;
 wire old_start=legacy_write&&reg_addr==10'h002&&reg_wdata[0];
 wire task_start=pending&&!view_active&&stream_ready&&!aborting&&!(mesh_consumer_reset&&!legacy);
 assign raw_start=task_start;
 wire matrix_legacy=legacy_write&&reg_addr[9:4]==6'h04;
 wire matrix_we=matrix_legacy||(ctx_take&&ctx_order_good&&mesh_ctx_index<16);
 wire [3:0] matrix_addr=matrix_legacy?reg_addr[3:0]:mesh_ctx_index[3:0];
 wire [31:0] matrix_data=matrix_legacy?reg_wdata:mesh_ctx_data;
 wire consumer_cancel=mesh_consumer_reset;
 wire validate_start=active&&!legacy&&preflight&&live_count!=0&&!repair_pending&&!validation_started&&!packet_busy&&!view_active&&!aborting;
 wire publish_start=output_pending&&packet_validated&&!packet_published&&!repair_pending&&!view_active&&!aborting;
 wire repair_ready=repair_pending&&!repair_end_pending&&!aborting&&!protocol_error&&repair_ack==repair_token&&mesh_word_ready&&!mesh_consumer_reset;
 wire repair_write=full_write&&reg_addr==10'h028;
 assign mesh_word_valid=!mesh_consumer_reset&&!aborting&&(repair_pending?(repair_write&&repair_ready):producer_valid);
 assign mesh_word_data=repair_pending?reg_wdata:producer_word;
 assign mesh_job_fault=protocol_error||mesh_bridge_fault;
 wire zero_done=active&&!legacy&&preflight&&live_count==0&&!repair_pending;
 wire snapshot_release=active&&!legacy&&validation_started&&packet_validated&&packet_snapshot_valid&&!snapshot_detached&&!repair_pending&&!aborting;
 wire task_ack=(active&&legacy&&(preflight||fallback))||zero_done||snapshot_release||
   (repair_pending&&repair_end_pending&&!view_active)||
   (aborting&&fallback&&!fetch_busy);
 wire alias_allowed=(lease_granted&&!active&&!pending)||(repair_pending&&!repair_end_pending&&!aborting);
 assign view_block=!alias_allowed||old_start||task_start||job_take;
 wire matrix_diag=repair_pending&&repair_index<16&&!view_active&&!repair_end_pending;
 wire [7:0] stream_view_addr=matrix_diag?{4'b0,repair_index[3:0]}:view_read_addr;
 wire bad_job=!context_open||context_next!=28||!(&matrix_valid)||mesh_job_seq==0||mesh_job_seq==completed_serial||mesh_job_control[31:10]!=0||mesh_job_control[7]||
  mesh_job_control[6:0]==0||mesh_job_control[6:0]>64||mesh_job_flags[31:19]!=0||mesh_job_alpha>255||
  mesh_job_vtx_w0[7:1]!={1'b0,mesh_job_vtx_w0[17:12]};
 geometry_mesh_stream #(.SOURCE_MIN_ADDRESS(32'h00000000),.SOURCE_END_ADDRESS(33'h001000000)) mesh(
  .clk(clk),.reset(reset),.cancel(aborting),.consumer_reset(consumer_cancel),.matrix_diag_read(matrix_diag),
  .task_valid(task_start),.task_load_only(legacy),.task_ready(stream_ready),.raw_ready(stream_raw_ready),.task_serial(descriptor_serial),
  .task_vtx_w0(legacy?(32'h01000000|({26'b0,count}<<12)|({26'b0,count}<<1)):vtx_w0),
  .task_vtx_w1(source_base),.task_command_count(legacy?7'b0:configuration[6:0]),.task_cull_mode(configuration[9:8]),
  .task_unsupported_features(8'b0),.matrix_we(matrix_we),.matrix_addr(matrix_addr),.matrix_data(matrix_data),
  .aspect(aspect),.coefficient_x(cx),.coefficient_y(cy),.coefficient_z(cz),
  .ambient_rgb(ambient),.diffuse_rgb(diffuse),.lighting_enabled(lit),.scale_u(scale_u),.scale_v(scale_v),
  .m_arvalid(m_arvalid),.m_arready(m_arready),.m_araddr(m_araddr),.m_arlen(m_arlen),
  .m_rvalid(m_rvalid),.m_rready(m_rready),.m_rdata(m_rdata),.m_rresp(m_rresp),.m_rlast(m_rlast),
  .list_addr(6'b0),.list_indices(),.view_addr(stream_view_addr),.view_data(view_read_data),
  .busy(stream_busy),.preflight_valid(preflight),.fallback_valid(fallback),.fallback_reason(reason),.serial(serial),
  .owned_mask(owned_mask),.used_mask(used_mask),.live_count(live_count),.checked_count(checked_count),
  .clip_rejected(clip_count),.cull_rejected(cull_count),.task_ack(task_ack),.ack_serial(serial),.done(stream_done),
  .task_command_source(command_source),.packet_validate(validate_start),.packet_publish(publish_start),
  .flags_rgb(flags_rgb),.u_offset(uofs),.v_offset(vofs),
  .word_valid(producer_valid),.word_ready(mesh_word_ready&&!repair_pending&&!aborting),.word_data(producer_word),
  .packet_busy(packet_busy),.packet_done(packet_done),.packet_error(packet_error),
  .packet_validated(packet_validated),.packet_published(packet_published),.packet_snapshot_valid(packet_snapshot_valid),.packet_words(packet_words),
  .fetch_busy(fetch_busy),.fetch_error(fetch_error),.raw_complete(raw_done),.raw_unsupported(unsupported),.raw_all_inside(all_inside),.raw_dma_error(dma_error));
 reg [31:0] repair_data;
 always @*begin
  repair_data=0;
  if(repair_index<16)repair_data=view_read_data;
  else case(repair_index)
   16:repair_data=aspect;17:repair_data=cx;18:repair_data=cy;19:repair_data=cz;
   20:repair_data={8'b0,ambient};21:repair_data={8'b0,diffuse};22:repair_data={scale_v,scale_u};23:repair_data={31'b0,lit};
   24:repair_data=projection_cx;25:repair_data=projection_cy;26:repair_data=projection_hw;27:repair_data=projection_hh;
   28:repair_data=cpu_vertex;29:repair_data=cpu_commands;30:repair_data=vtx_w0;31:repair_data=configuration;
   32:repair_data=flags_rgb;33:repair_data=uofs;34:repair_data=vofs;35:repair_data=alpha;
   36:repair_data=source_base;37:repair_data=command_source;
   default:;
  endcase
  reg_rdata=0;
  case(reg_addr)
   10'h000:reg_rdata=32'h4c4f4405;
   10'h001:reg_rdata={23'b0,&matrix_valid,config_error,dma_error,all_inside,unsupported,success,completed,active};
   10'h020:reg_rdata=32'h414a3032;
   10'h021:reg_rdata={25'b0,repair_ready,success,lease_granted,lease_pending,mesh_job_fault,repair_pending,active&&!legacy};
   10'h022:reg_rdata=descriptor_serial;10'h023:reg_rdata=completed_serial;
   10'h024:reg_rdata={23'b0,success,4'b0,(packet_error?4'd12:reason)};
   10'h025:reg_rdata=repair_token;10'h026:reg_rdata={26'b0,repair_index};10'h027:reg_rdata=repair_data;
   10'h02b:reg_rdata=lease_target;10'h02e:reg_rdata=epoch;10'h02f:reg_rdata=lease_granted?granted_ticket:0;
   default:;
  endcase
 end
 always @(posedge clk)begin
  if(reset)begin
   source_base<=0;aspect<=32'h3f800000;cx<=0;cy<=0;cz<=0;ambient<=0;diffuse<=0;scale_u<=0;scale_v<=0;matrix_valid<=0;count<=0;lit<=0;
   projection_cx<=0;projection_cy<=0;projection_hw<=0;projection_hh<=0;
   output_pending<=0;snapshot_detached<=0;output_serial<=0;
   active<=0;pending<=0;legacy<=0;completed<=0;success<=0;config_error<=0;aborting<=0;protocol_error<=0;raw_seen<=0;validation_started<=0;
   descriptor_serial<=0;vtx_w0<=0;command_source<=0;configuration<=0;flags_rgb<=0;uofs<=0;vofs<=0;cpu_vertex<=0;cpu_commands<=0;alpha<=0;completed_serial<=0;
   context_open<=0;context_next<=0;lease_pending<=0;lease_granted<=0;lease_release_pending<=0;lease_target<=0;lease_ticket<=0;granted_ticket<=0;epoch<=0;
   repair_pending<=0;repair_end_pending<=0;repair_token<=0;repair_ack<=0;repair_index<=0;
   view_valid<=0;view_count<=0;mesh_job_done<=0;
  end else begin
   mesh_job_done<=0;
   if(task_start)pending<=0;
   if(validate_start)validation_started<=1;
   if(snapshot_release)snapshot_detached<=1;
   if(raw_done)begin completed<=1;success<=!unsupported&&dma_error==0;raw_seen<=1;view_valid<=!unsupported&&dma_error==0;view_count<=legacy?count:vtx_w0[17:12];end
   if(active&&!legacy&&!aborting&&!repair_pending&&(fallback||(packet_error&&!packet_published)))begin
    repair_pending<=1;repair_end_pending<=0;repair_token<=repair_token+1'b1;repair_ack<=repair_token;repair_index<=0;
   end
   if(packet_error&&packet_published)protocol_error<=1;
   // Raw completion after snapshot detaches only the raw controller. The
   // immutable output has its own lifetime and may overlap a CPU legacy LOAD.
   if(stream_done)begin
    active<=0;pending<=0;aborting<=0;repair_pending<=0;repair_end_pending<=0;
    if(!legacy&&!snapshot_detached&&!aborting&&!mesh_consumer_reset)begin
     completed_serial<=output_serial;mesh_job_done<=1;output_pending<=0;validation_started<=0;
    end
    if(!raw_seen)completed<=1;
   end
   if(output_pending&&packet_done&&packet_published&&!packet_error&&!mesh_consumer_reset)begin
    completed_serial<=output_serial;mesh_job_done<=1;output_pending<=0;validation_started<=0;
    // Do not alter active/legacy/completed/success/view: a newer CPU LOAD may own them.
   end
   if(lease_pending&&!context_open&&!active&&!pending&&stream_raw_ready&&!view_active&&!aborting&&!protocol_error&&!mesh_bridge_fault&&!mesh_consumer_reset)begin
    lease_granted<=1;lease_pending<=0;granted_ticket<=lease_ticket;
   end
   if(lease_release_pending&&!active&&!pending&&!view_active&&stream_raw_ready)begin lease_granted<=0;lease_release_pending<=0;end
   if(mesh_bridge_fault)protocol_error<=1;
   if(reg_wr&&reg_wstrb!=4'hf)protocol_error<=1;
   if(full_write)begin
    case(reg_addr)
     10'h026:if(repair_pending&&reg_wdata<=37)repair_index<=reg_wdata[5:0];else protocol_error<=1;
     10'h028:if(!repair_ready)protocol_error<=1;
     10'h029:if(repair_pending)repair_ack<=reg_wdata;else protocol_error<=1;
     10'h02a:if(reg_wdata==1&&repair_pending&&repair_ack==repair_token&&!repair_end_pending)repair_end_pending<=1;else protocol_error<=1;
     10'h02b:if(!lease_pending&&!lease_granted)lease_target<=reg_wdata;else protocol_error<=1;
     10'h02c:if(!lease_pending&&!lease_granted)lease_ticket<=reg_wdata;else protocol_error<=1;
     10'h02d:begin
      if(reg_wdata==1&&!lease_pending&&!lease_granted&&lease_ticket!=0)lease_pending<=1;
      else if(reg_wdata==2&&lease_granted&&granted_ticket==lease_ticket&&!active&&!pending)lease_release_pending<=1;
      else protocol_error<=1;
     end
     default:if((reg_addr<=12||reg_addr[9:4]==6'h04)&&!legacy_write)protocol_error<=1;
    endcase
   end
   if(ctx_take&&!ctx_order_good)protocol_error<=1;
   if(ctx_take&&ctx_order_good)begin
    context_open<=1;context_next<=mesh_ctx_index+1'b1;
    if(mesh_ctx_index<16)matrix_valid[mesh_ctx_index[3:0]]<=1;
    else case(mesh_ctx_index)
     16:aspect<=mesh_ctx_data;17:cx<=mesh_ctx_data;18:cy<=mesh_ctx_data;19:cz<=mesh_ctx_data;
     20:ambient<=mesh_ctx_data[23:0];21:diffuse<=mesh_ctx_data[23:0];
     22:begin scale_u<=mesh_ctx_data[15:0];scale_v<=mesh_ctx_data[31:16];end
     23:begin lit<=mesh_ctx_data[0];if(mesh_ctx_data>1)protocol_error<=1;end
     24:projection_cx<=mesh_ctx_data;25:projection_cy<=mesh_ctx_data;26:projection_hw<=mesh_ctx_data;27:projection_hh<=mesh_ctx_data;
     default:protocol_error<=1;
    endcase
   end
   if(legacy_write)begin
    if(matrix_legacy)matrix_valid[reg_addr[3:0]]<=1;
    case(reg_addr)
     2:if(reg_wdata[1])begin completed<=0;success<=0;config_error<=0;end
     3:source_base<=reg_wdata;4:count<=reg_wdata[5:0];5:aspect<=reg_wdata;6:cx<=reg_wdata;7:cy<=reg_wdata;8:cz<=reg_wdata;
     9:ambient<=reg_wdata[23:0];10:diffuse<=reg_wdata[23:0];11:begin scale_u<=reg_wdata[15:0];scale_v<=reg_wdata[31:16];end
     12:lit<=reg_wdata[0];default:;
    endcase
   end
   if(job_take||old_start)begin
    legacy<=old_start;completed<=0;success<=0;raw_seen<=0;view_valid<=0;view_count<=0;validation_started<=0;
    repair_pending<=0;repair_end_pending<=0;aborting<=0;config_error<=!(&matrix_valid);
    if(job_take)begin
     context_open<=0;context_next<=0;output_pending<=1;snapshot_detached<=0;output_serial<=mesh_job_seq;
     descriptor_serial<=mesh_job_seq;source_base<=mesh_job_vertex;vtx_w0<=mesh_job_vtx_w0;command_source<=mesh_job_commands;
     configuration<=mesh_job_control;flags_rgb<=mesh_job_flags;uofs<=mesh_job_uofs;vofs<=mesh_job_vofs;
     cpu_vertex<=mesh_job_cpu_vertex;cpu_commands<=mesh_job_cpu_commands;alpha<=mesh_job_alpha;
    end
    if(job_take&&bad_job)begin protocol_error<=1;active<=0;pending<=0;output_pending<=0;end
    else if(old_start&&!(&matrix_valid))begin completed<=1;active<=0;pending<=0;end
    else begin active<=1;pending<=1;end
   end
   if(mesh_consumer_reset)begin
    epoch<=epoch+1'b1;repair_token<=repair_token+1'b1;protocol_error<=0;repair_pending<=0;repair_end_pending<=0;
    completed_serial<=0;lease_pending<=0;context_open<=0;context_next<=0;output_pending<=0;snapshot_detached<=0;validation_started<=0;
    if(active&&!legacy)begin aborting<=1;pending<=0;end
    // A granted legacy lease belongs to CPU raw DMA, not the GPU consumer.
   end
   if(aborting&&!stream_busy&&!pending)begin active<=0;aborting<=0;end
  end
 end
`ifndef SYNTHESIS
 always @(posedge clk)if(!reset&&!mesh_consumer_reset)begin
  if(lease_granted&&context_open)$fatal(1,"AJ02 lease interrupted context");
  if(lease_granted&&active&&!legacy)$fatal(1,"AJ02 lease interrupted async raw owner");
  if(lease_granted&&output_pending&&!snapshot_detached)$fatal(1,"AJ02 lease before immutable snapshot");
  if(snapshot_detached&&mesh.packet_raw_read_active)$fatal(1,"AJ02 raw read after detachment");
 end
`endif
endmodule
