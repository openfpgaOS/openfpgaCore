`ifdef INCLUDE_GPU_MESH_FRONT
// Experimental MTF1 wrapper. The legacy LOD5 path and mesh tasks share ONE
// physical raw LOAD/FMA engine. No task owns GPU_BUSY before its ring marker.
module geometry_load_alias (
 input wire clk,reset,
 input wire reg_wr,input wire [9:0] reg_addr,
 input wire [31:0] reg_wdata,input wire [3:0] reg_wstrb,
 output reg [31:0] reg_rdata,
 output wire m_arvalid,input wire m_arready,output wire [31:0] m_araddr,
 output wire [7:0] m_arlen,input wire m_rvalid,output wire m_rready,
 input wire [31:0] m_rdata,input wire [1:0] m_rresp,input wire m_rlast,
 input wire [7:0] view_read_addr,output wire [31:0] view_read_data,
 input wire view_active,output wire view_block,output reg view_valid,
 output reg [5:0] view_count,
 input wire mesh_marker_valid,
 input wire [31:0] mesh_marker_serial,mesh_marker_flags,mesh_marker_uofs,mesh_marker_vofs,
 output wire mesh_marker_ready,mesh_marker_error,
 output wire mesh_word_valid,output wire [31:0] mesh_word_data,input wire mesh_word_ready,
 output wire mesh_packet_done,mesh_packet_error,
 input wire mesh_consumer_reset,mesh_bridge_fault
);
 reg [31:0] source_base,aspect,cx,cy,cz;
 reg [5:0] count;
 reg [23:0] ambient,diffuse;
 reg [15:0] scale_u,scale_v,matrix_valid;
 reg lit,active,pending,completed,success,config_error;
 reg legacy,aborting,protocol_error,raw_seen,copy_done,validation_pending,arm_pending;
 reg [31:0] descriptor_serial,vtx_w0,command_source,configuration,flags_rgb,uofs,vofs,completed_serial;
 wire stream_ready,stream_busy,preflight,fallback,stream_done;
 wire [31:0] serial,owned_mask,used_mask;
 wire [6:0] live_count,checked_count,clip_count,cull_count;
 wire [3:0] reason;
 wire raw_done,unsupported,all_inside;wire [1:0] dma_error;
 wire packet_busy,packet_done,packet_error,packet_validated,packet_published,fetch_busy,fetch_error;
 wire full_write=reg_wr&&reg_wstrb==4'hf;
 wire write_idle=full_write&&!active&&!pending;
 wire old_start=write_idle&&reg_addr==10'h002&&reg_wdata[0];
 wire mesh_start=write_idle&&reg_addr==10'h026&&reg_wdata==1;
 wire task_start=pending&&!view_active&&stream_ready;
 wire raw_start=task_start; // retained passive alias-start assertion name
 wire matrix_we=write_idle&&reg_addr[9:4]==6'h04;
 wire ack_request=full_write&&reg_addr==10'h026&&reg_wdata==2&&!legacy;
 wire cancel_request=full_write&&reg_addr==10'h026&&reg_wdata==4&&!legacy;
 wire validate_request=full_write&&reg_addr==10'h026&&reg_wdata==8&&!legacy;
 wire arm_request=full_write&&reg_addr==10'h026&&reg_wdata==16&&!legacy;
 wire validate_start=validation_pending&&!view_active&&preflight&&!packet_busy&&!packet_published;
 wire consumer_cancel=mesh_consumer_reset&&active&&!legacy;
 wire producer_valid,producer_ready;
 wire [31:0] producer_word;
 wire queue_reserve_ready,queue_busy,queue_filling,queue_fault;
 wire queue_marker_ready,queue_marker_error;
 wire [31:0] queue_serial;
 wire [31:0] queue_fill_serial;
 wire [8:0] packet_words;
 wire queue_reserve=arm_pending&&packet_validated&&!packet_published&&queue_reserve_ready&&!consumer_cancel;
 // Reservation latches metadata independently; copying waits an accepted alias.
 wire raw_copy_start=arm_pending&&packet_validated&&!packet_published&&queue_filling&&
   queue_fill_serial==serial&&!view_active&&!consumer_cancel;
 wire raw_copy_done=packet_done&&packet_published;
 wire pending_marker_match=active&&!legacy&&arm_pending&&packet_validated&&
   mesh_marker_serial==serial&&mesh_marker_flags==flags_rgb&&mesh_marker_uofs==uofs&&mesh_marker_vofs==vofs;
 // A legitimate marker can arrive while ARM is waiting to reserve the now-free
 // queue. This is flow control, not a malformed token.
 assign mesh_marker_ready=queue_marker_ready;
 assign mesh_marker_error=queue_marker_error&&!(queue_reserve_ready&&pending_marker_match);
 assign mesh_packet_error=queue_fault;
 geometry_mesh_queue packet_queue(.clk(clk),.reset(reset||mesh_consumer_reset),
  .reserve(queue_reserve),.reserve_ready(queue_reserve_ready),
  .reserve_serial(serial),.reserve_flags(flags_rgb),.reserve_uofs(uofs),.reserve_vofs(vofs),
  .reserve_words(packet_words),.producer_serial(queue_fill_serial),
  .producer_valid(producer_valid),.producer_word(producer_word),.producer_ready(producer_ready),
  .producer_done(raw_copy_done),.producer_error(packet_error&&packet_published),
  .marker_valid(mesh_marker_valid),.marker_serial(mesh_marker_serial),.marker_flags(mesh_marker_flags),
  .marker_uofs(mesh_marker_uofs),.marker_vofs(mesh_marker_vofs),
  .marker_ready(queue_marker_ready),.marker_error(queue_marker_error),
  .word_valid(mesh_word_valid),.word_data(mesh_word_data),.word_ready(mesh_word_ready),
  .done(mesh_packet_done),.error(queue_fault),.busy(queue_busy),.filling(queue_filling),.owner_serial(queue_serial));
 wire task_ack=(legacy&&(preflight||fallback))||
   (ack_request&&(preflight||fallback)&&!packet_published&&!arm_pending)||
   (packet_done&&packet_published&&!packet_error)||
   (aborting&&fallback&&!fetch_busy);
 // Alias requests are blocked only while the sole view port is actually owned.
 // Accepted bursts always drain before a new LOAD or validation takes that port.
 assign view_block=pending||old_start||mesh_start||validation_pending||validate_request||
   (active&&!(preflight||fallback))||(packet_busy&&!packet_validated)||
   (arm_pending&&queue_filling&&!packet_published);
 geometry_mesh_stream #(.SOURCE_MIN_ADDRESS(32'h00000000),.SOURCE_END_ADDRESS(33'h001000000)) mesh(
  .clk(clk),.reset(reset),.cancel((cancel_request&&!arm_pending)||aborting),.consumer_reset(consumer_cancel),
  .task_valid(task_start),.task_load_only(legacy),.task_ready(stream_ready),
  .task_serial(descriptor_serial),
  .task_vtx_w0(legacy?(32'h01000000|({26'b0,count}<<12)|({26'b0,count}<<1)):vtx_w0),
  .task_vtx_w1(source_base),.task_command_count(legacy?7'b0:configuration[6:0]),
  .task_cull_mode(configuration[9:8]),
  .task_unsupported_features(legacy?8'b0:(configuration[23:16]|(vtx_w0[7:1]!={1'b0,vtx_w0[17:12]}?8'h01:8'h00))),
  .matrix_we(matrix_we),.matrix_addr(reg_addr[3:0]),.matrix_data(reg_wdata),
  .aspect(aspect),.coefficient_x(cx),.coefficient_y(cy),.coefficient_z(cz),
  .ambient_rgb(ambient),.diffuse_rgb(diffuse),.lighting_enabled(lit),.scale_u(scale_u),.scale_v(scale_v),
  .m_arvalid(m_arvalid),.m_arready(m_arready),.m_araddr(m_araddr),.m_arlen(m_arlen),
  .m_rvalid(m_rvalid),.m_rready(m_rready),.m_rdata(m_rdata),.m_rresp(m_rresp),.m_rlast(m_rlast),
  .list_addr(6'b0),.list_indices(),.view_addr(view_read_addr),.view_data(view_read_data),
  .busy(stream_busy),.preflight_valid(preflight),.fallback_valid(fallback),.fallback_reason(reason),.serial(serial),
  .owned_mask(owned_mask),.used_mask(used_mask),.live_count(live_count),.checked_count(checked_count),
  .clip_rejected(clip_count),.cull_rejected(cull_count),.task_ack(task_ack),.ack_serial(serial),.done(stream_done),
  .task_command_source(command_source),.packet_validate(validate_start),.packet_publish(raw_copy_start),
  .flags_rgb(flags_rgb),.u_offset(uofs),.v_offset(vofs),
  .word_valid(producer_valid),.word_ready(producer_ready),.word_data(producer_word),
  .packet_busy(packet_busy),.packet_done(packet_done),.packet_error(packet_error),
  .packet_validated(packet_validated),.packet_published(packet_published),.packet_words(packet_words),
  .fetch_busy(fetch_busy),.fetch_error(fetch_error),
  .raw_complete(raw_done),.raw_unsupported(unsupported),.raw_all_inside(all_inside),.raw_dma_error(dma_error));
 always @* begin
  reg_rdata=0;
  case(reg_addr)
   10'h000:reg_rdata=32'h4c4f4405;
   10'h001:reg_rdata={23'b0,&matrix_valid,config_error,dma_error,all_inside,unsupported,success,completed,active};
   10'h020:reg_rdata=32'h4d544632;
   10'h021:reg_rdata={11'b0,copy_done,reason,raw_seen,live_count,packet_published,packet_error,packet_validated,
     success,protocol_error,fallback,preflight,active&&!legacy};
   10'h022:reg_rdata=descriptor_serial;
   10'h023:reg_rdata=vtx_w0;
   10'h024:reg_rdata=command_source;
   10'h025:reg_rdata=configuration;
   10'h027:reg_rdata=used_mask;
   10'h028:reg_rdata=owned_mask;
   10'h029:reg_rdata={25'b0,clip_count};
   10'h02a:reg_rdata={25'b0,cull_count};
   10'h02b:reg_rdata=flags_rgb;
   10'h02c:reg_rdata=uofs;
   10'h02d:reg_rdata=vofs;
   10'h02e:reg_rdata={25'b0,checked_count};
   10'h02f:reg_rdata=completed_serial;
   default:;
  endcase
 end
 always @(posedge clk) begin
  if(reset)begin
   source_base<=0;aspect<=32'h3f800000;cx<=0;cy<=0;cz<=0;count<=0;
   ambient<=0;diffuse<=0;scale_u<=0;scale_v<=0;matrix_valid<=0;lit<=0;
   active<=0;pending<=0;completed<=0;success<=0;config_error<=0;
   view_valid<=0;view_count<=0;legacy<=0;aborting<=0;protocol_error<=0;raw_seen<=0;copy_done<=0;validation_pending<=0;arm_pending<=0;
   descriptor_serial<=0;vtx_w0<=0;command_source<=0;configuration<=0;flags_rgb<=0;uofs<=0;vofs<=0;completed_serial<=0;
  end else begin
   if(task_start)pending<=0;
   if(validate_start)validation_pending<=0;
   if(raw_done)begin
    completed<=1;success<=!unsupported&&dma_error==0;raw_seen<=1;
    view_valid<=!unsupported&&dma_error==0;view_count<=legacy?count:vtx_w0[17:12];
   end
   if(consumer_cancel)begin aborting<=1;validation_pending<=0;arm_pending<=0;end
   if(stream_done)begin
    active<=0;pending<=0;aborting<=0;validation_pending<=0;arm_pending<=0;
    completed_serial<=serial;
    if(!raw_seen)completed<=1;
   end
   if(raw_copy_done)copy_done<=1;
   if(mesh_marker_error||mesh_bridge_fault||mesh_packet_error)protocol_error<=1;
   if(full_write)begin
    if(reg_addr==10'h026)begin
     case(reg_wdata)
      1:if(active||pending)protocol_error<=1;
      2:if(!active||legacy||!(preflight||fallback)||packet_published||arm_pending)protocol_error<=1;
      4:if(!active||legacy||packet_published||arm_pending)protocol_error<=1;
      8:if(!active||legacy||!preflight||packet_busy||validation_pending||packet_published)protocol_error<=1;
        else validation_pending<=1;
      16:if(!active||legacy||!preflight||!packet_validated||packet_published||arm_pending)protocol_error<=1;
        else arm_pending<=1;
      default:protocol_error<=1;
     endcase
    end
    if((reg_addr==10'h02b||reg_addr==10'h02c||reg_addr==10'h02d)&&
       (!active||(!legacy&&preflight&&!packet_busy&&!validation_pending)))begin
     case(reg_addr)
      10'h02b:flags_rgb<=reg_wdata;
      10'h02c:uofs<=reg_wdata;
      10'h02d:vofs<=reg_wdata;
     endcase
    end
    else if((reg_addr==10'h02b||reg_addr==10'h02c||reg_addr==10'h02d))protocol_error<=1;
   end
   if(write_idle)begin
    if(matrix_we)matrix_valid[reg_addr[3:0]]<=1;
    case(reg_addr)
     10'h002:if(reg_wdata[1])begin completed<=0;success<=0;config_error<=0;protocol_error<=0;end
     10'h003:source_base<=reg_wdata;
     10'h004:count<=reg_wdata[5:0];
     10'h005:aspect<=reg_wdata;
     10'h006:cx<=reg_wdata;
     10'h007:cy<=reg_wdata;
     10'h008:cz<=reg_wdata;
     10'h009:ambient<=reg_wdata[23:0];
     10'h00a:diffuse<=reg_wdata[23:0];
     10'h00b:begin scale_u<=reg_wdata[15:0];scale_v<=reg_wdata[31:16];end
     10'h00c:lit<=reg_wdata[0];
     10'h022:descriptor_serial<=reg_wdata;
     10'h023:vtx_w0<=reg_wdata;
     10'h024:command_source<=reg_wdata;
     10'h025:configuration<=reg_wdata;
     default:;
    endcase
   end
   if(old_start||mesh_start)begin
    completed<=0;success<=0;raw_seen<=0;copy_done<=0;validation_pending<=0;arm_pending<=0;aborting<=0;
    view_valid<=0;view_count<=0;legacy<=old_start;
    config_error<=!(&matrix_valid);
    // Mesh shape/context errors are returned by full preflight before LOAD.
    // Legacy lacks a fallback status, so reject a missing matrix synchronously.
    if(old_start&&!(&matrix_valid))begin completed<=1;active<=0;pending<=0;end
    else begin active<=1;pending<=1;end
   end
  end
 end
endmodule
`else
// Private LOD5: completed native LOAD remains in BRAM. CPU copies the
// read-only, cacheable 32-byte raw view into its ordinary LoadedVertex slots.
// All hardware jobs use dest0. No result DMA, persistent ownership or counters.
module geometry_load_alias (
 input wire clk,reset,
 input wire reg_wr,input wire [9:0] reg_addr,
 input wire [31:0] reg_wdata,input wire [3:0] reg_wstrb,
 output reg [31:0] reg_rdata,
 output wire m_arvalid,input wire m_arready,output wire [31:0] m_araddr,
 output wire [7:0] m_arlen,input wire m_rvalid,output wire m_rready,
 input wire [31:0] m_rdata,input wire [1:0] m_rresp,input wire m_rlast,
 input wire [7:0] view_read_addr,output wire [31:0] view_read_data,
 input wire view_active,output wire view_block,output reg view_valid,
 output reg [5:0] view_count
);
 reg [31:0] source_base,aspect,cx,cy,cz;
 reg [5:0] count;
 reg [23:0] ambient,diffuse;
 reg [15:0] scale_u,scale_v,matrix_valid;
 reg lit,active,pending,completed,success,config_error;
 wire raw_busy,raw_done,unsupported,all_inside;
 wire [1:0] dma_error;
 wire write_idle=reg_wr && reg_wstrb==4'hf && !active;
 wire start_request=write_idle && reg_addr==10'h002 && reg_wdata[0];
 wire raw_start=pending && !view_active && !raw_busy;
 wire matrix_we=write_idle && reg_addr[9:4]==6'h04;
 // Give START priority over accepting a new alias burst on the same edge.
 assign view_block=active || start_request;
 geometry_raw_load #(.CAPTURE_CONTEXT(0),.ENABLE_INVALIDATE(0),
  .SOURCE_MIN_ADDRESS(32'h00000000),.SOURCE_END_ADDRESS(33'h001000000)) load(
  .clk(clk),.reset(reset),.slot_invalidate(1'b0),.invalidate_mask(32'b0),
  .matrix_we(matrix_we),.matrix_addr(reg_addr[3:0]),.matrix_data(reg_wdata),
  .start(raw_start),.job_base(source_base),.count(count),.dest(5'd0),
  .aspect(aspect),.coefficient_x(cx),.coefficient_y(cy),.coefficient_z(cz),
  .ambient_rgb(ambient),.diffuse_rgb(diffuse),.lighting_enabled(lit),
  .scale_u(scale_u),.scale_v(scale_v),
  .m_arvalid(m_arvalid),.m_arready(m_arready),.m_araddr(m_araddr),.m_arlen(m_arlen),
  .m_rvalid(m_rvalid),.m_rready(m_rready),.m_rdata(m_rdata),.m_rresp(m_rresp),.m_rlast(m_rlast),
  .dma_request_start(),.dma_request_base(),.dma_request_words(),
  .dma_response_busy(1'b0),.dma_response_done(1'b0),.dma_response_error(2'b0),
  .dma_response_word_valid(1'b0),.dma_response_word_index(8'b0),.dma_response_word_data(32'b0),
  .output_addr(view_read_addr),.output_data(view_read_data),
  .busy(raw_busy),.done(raw_done),.unsupported(unsupported),.dma_error(dma_error),
  .all_inside(all_inside),.cycles(),.valid_slots(),.compute_cycles(),
  .fma_request_valid(),.fma_request_a(),.fma_request_b(),.fma_request_c(),.fma_request_multiply(),
  .fma_response_valid(1'b0),.fma_response_data(32'b0),.fma_response_unsupported(1'b0),
  .scratch_read_valid(1'b0),.scratch_read_addr(7'b0),.scratch_read_data(),
  .scratch_write(1'b0),.scratch_write_addr(7'b0),.scratch_write_data(32'b0));
 always @*begin
  reg_rdata=0;
  case(reg_addr)
   10'h000:reg_rdata=32'h4c4f4405;
   10'h001:reg_rdata={23'b0,&matrix_valid,config_error,dma_error,all_inside,unsupported,success,completed,active};
   default:reg_rdata=0;
  endcase
 end
 always @(posedge clk)begin
  if(reset)begin
   source_base<=0;aspect<=32'h3f800000;cx<=0;cy<=0;cz<=0;count<=0;
   ambient<=0;diffuse<=0;scale_u<=0;scale_v<=0;matrix_valid<=0;lit<=0;
   active<=0;pending<=0;completed<=0;success<=0;config_error<=0;
   view_valid<=0;view_count<=0;
  end else begin
   if(raw_start)pending<=0;
   if(raw_done)begin
    active<=0;completed<=1;success<=!unsupported && dma_error==0;
    view_valid<=!unsupported && dma_error==0;view_count<=count;
   end
   if(write_idle)begin
    if(matrix_we)matrix_valid[reg_addr[3:0]]<=1'b1;
    case(reg_addr)
     10'h002:begin
      if(reg_wdata[1])begin completed<=0;success<=0;config_error<=0;end
      if(reg_wdata[0])begin
       completed<=!(&matrix_valid);success<=0;config_error<=!(&matrix_valid);
       if(&matrix_valid)begin active<=1;pending<=1;end
      end
     end
     10'h003:source_base<=reg_wdata;
     10'h004:count<=reg_wdata[5:0];
     10'h005:aspect<=reg_wdata;
     10'h006:cx<=reg_wdata;
     10'h007:cy<=reg_wdata;
     10'h008:cz<=reg_wdata;
     10'h009:ambient<=reg_wdata[23:0];
     10'h00a:diffuse<=reg_wdata[23:0];
     10'h00b:begin scale_u<=reg_wdata[15:0];scale_v<=reg_wdata[31:16];end
     10'h00c:lit<=reg_wdata[0];
    endcase
   end
  end
 end
endmodule
`endif
