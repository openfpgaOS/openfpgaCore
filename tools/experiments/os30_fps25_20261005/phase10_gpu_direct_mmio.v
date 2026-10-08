// Private GDR1 geometry frontend. No result DMA: CPU fallback alone reads raw
// slots. LOAD/PRECHECK/COMMIT own their inputs until completion. A completed
// PRECHECK reserves its raw slots/list until COMMIT or explicit CANCEL.
// Only fabric reset may cancel AXI traffic; CPU-only reset must leave it live.
module geometry_direct_mmio (
 input wire clk,reset,reg_wr,input wire [9:0] reg_addr,
 input wire [31:0] reg_wdata,input wire [3:0] reg_wstrb,output reg [31:0] reg_rdata,
 output wire m_arvalid,input wire m_arready,output wire [31:0] m_araddr,
 output wire [7:0] m_arlen,input wire m_rvalid,output wire m_rready,
 input wire [31:0] m_rdata,input wire [1:0] m_rresp,input wire m_rlast,
 output wire geo_request,output wire [12:0] geo_words,input wire geo_grant,
 output wire geo_valid,output wire [31:0] geo_data,input wire geo_ready,
 output wire geo_abort,input wire geo_owned,geo_done
);
 localparam IDLE=0,LOAD=1,PRECHECK=2,COMMIT=3;
 reg [1:0] operation;
 wire active=operation!=IDLE;
 reg pending,completed,success,unsupported,config_error;
 reg [1:0] read_error;
 reg [2:0] reason;
 reg [31:0] source,aspect,cx,cy,cz;
 reg [5:0] count;reg [4:0] dest;
 reg [23:0] ambient,diffuse;
 reg [15:0] scale_u,scale_v,matrix_valid;
 reg lighting;
 reg [31:0] command_source,draw_flags,initial_stale,prim,env;
 reg [15:0] tile_uls,tile_ult;reg linear_filter;
 reg [6:0] budget;reg [1:0] cull_mode;
 wire write_idle=reg_wr&&reg_wstrb==4'hf&&!active;
 wire control=write_idle&&reg_addr==10'h002;
 wire matrix_we=write_idle&&!pending&&reg_addr[9:4]==6'h04;
 wire load_request=control&&reg_wdata[4:0]==5'd1;
 wire pre_request=control&&reg_wdata[4:0]==5'd4;
 wire commit_request=control&&reg_wdata[4:0]==5'd8;
 wire load_start=load_request&&!pending&&(&matrix_valid);
 wire pre_start=pre_request&&!pending;
 wire commit_start=commit_request&&pending;
 wire raw_busy,raw_done,raw_bad,all_inside;
 wire [1:0] raw_error;wire [31:0] raw_valid,raw_data;
 wire r_dma_start,p_dma_start,dma_busy,dma_done,dma_word_valid;
 wire [31:0] r_dma_base,p_dma_base,dma_word_data;
 wire [7:0] r_dma_words,p_dma_words,dma_word_index;
 wire [1:0] dma_error;
 wire check_valid,check_ready,result_valid,result_ready,result_bad,result_fallback;
 wire [14:0] check_indices;wire [1:0] check_cull,result_reject;
 wire [2:0] result_reason;
 wire [5:0] list_addr;wire [14:0] list_indices;
 wire packet_access,packet_write,packet_read_valid;
 wire [3:0] packet_write_addr,packet_read_addr;
 wire [31:0] packet_write_data,packet_read_data;
 wire pre_busy,pre_done,pre_fallback,pre_invalid;wire [1:0] pre_error;
 wire [6:0] accepted_commands,live_triangles,clip_rejected,cull_rejected,accepted_triangles;
 wire commit_busy,commit_done,commit_bad,commit_raw_read;
 wire [7:0] commit_raw_addr,emitted_vertices;
 wire [12:0] emitted_words;wire [31:0] final_stale;
 // LOAD and PRECHECK cannot overlap. Keep the accepted operation as DMA
 // response owner through the final word and done pulse; context writes and
 // a new operation remain blocked until that operation has fully drained.
 wire pre_owns_dma=operation==PRECHECK||pre_start;
 raw_vtx_dma #(.VARIABLE_WORDS(1)) reader (
  .clk(clk),.reset(reset),.start(r_dma_start||p_dma_start),
  .job_base(pre_owns_dma?p_dma_base:r_dma_base),.job_count(6'd0),
  .job_word_count(pre_owns_dma?p_dma_words:r_dma_words),
  .busy(dma_busy),.done(dma_done),.error(dma_error),
  .m_arvalid(m_arvalid),.m_arready(m_arready),.m_araddr(m_araddr),.m_arlen(m_arlen),
  .m_rvalid(m_rvalid),.m_rready(m_rready),.m_rdata(m_rdata),.m_rresp(m_rresp),.m_rlast(m_rlast),
  .word_valid(dma_word_valid),.word_ready(1'b1),.word_index(dma_word_index),.word_data(dma_word_data));
 geometry_raw_frontend #(.CAPTURE_CONTEXT(0),.ENABLE_INVALIDATE(1),.EXTERNAL_DMA(1)) raw (
  .clk(clk),.reset(reset),.matrix_we(matrix_we),.matrix_addr(reg_addr[3:0]),.matrix_data(reg_wdata),
  .slot_invalidate(write_idle&&!pending&&reg_addr==10'h010),.invalidate_mask(reg_wdata),
  .start(load_start),.job_base(source),.count(count),.dest(dest),.aspect(aspect),
  .coefficient_x(cx),.coefficient_y(cy),.coefficient_z(cz),.ambient_rgb(ambient),.diffuse_rgb(diffuse),
  .lighting_enabled(lighting),.scale_u(scale_u),.scale_v(scale_v),
  .m_arvalid(),.m_arready(1'b0),.m_araddr(),.m_arlen(),
  .m_rvalid(1'b0),.m_rready(),.m_rdata(32'b0),.m_rresp(2'b0),.m_rlast(1'b0),
  .dma_request_start(r_dma_start),.dma_request_base(r_dma_base),.dma_request_words(r_dma_words),
  .dma_response_busy(dma_busy&&!pre_owns_dma),.dma_response_done(dma_done&&!pre_owns_dma),.dma_response_error(dma_error),
  .dma_response_word_valid(dma_word_valid&&!pre_owns_dma),.dma_response_word_index(dma_word_index),.dma_response_word_data(dma_word_data),
  .output_addr(commit_raw_read?commit_raw_addr:reg_addr[7:0]),.output_data(raw_data),
  .busy(raw_busy),.done(raw_done),.unsupported(raw_bad),.dma_error(raw_error),.all_inside(all_inside),
  .cycles(),.compute_cycles(),.valid_slots(raw_valid),.owned_slots(32'b0),
  .tri_valid(check_valid),.tri_ready(check_ready),.tri_indices(check_indices),.tri_cull_mode(check_cull),
  .tri_out_valid(result_valid),.tri_out_ready(result_ready),.tri_reject_class(result_reject),
  .tri_clip_or(),.tri_cross_bits(),.tri_unsupported(result_bad),.tri_fallback(result_fallback),.tri_reason(result_reason),
  .fma_request_valid(),.fma_request_a(),.fma_request_b(),.fma_request_c(),.fma_request_multiply(),
  .fma_response_valid(1'b0),.fma_response_data(32'b0),.fma_response_unsupported(1'b0));
 geometry_run_dma #(.EXTERNAL_DMA(1)) run (
  .clk(clk),.reset(reset),.start(pre_start),.command_base(command_source),.command_count(budget),.cull_mode(cull_mode),
  .m_arvalid(),.m_arready(1'b0),.m_araddr(),.m_arlen(),
  .m_rvalid(1'b0),.m_rready(),.m_rdata(32'b0),.m_rresp(2'b0),.m_rlast(1'b0),
  .dma_request_start(p_dma_start),.dma_request_base(p_dma_base),.dma_request_words(p_dma_words),
  .dma_response_busy(dma_busy&&pre_owns_dma),.dma_response_done(dma_done&&pre_owns_dma),.dma_response_error(dma_error),
  .dma_response_word_valid(dma_word_valid&&pre_owns_dma),.dma_response_word_index(dma_word_index),.dma_response_word_data(dma_word_data),
  .check_valid(check_valid),.check_ready(check_ready),.check_indices(check_indices),.check_cull_mode(check_cull),
  .result_valid(result_valid),.result_ready(result_ready),.result_reject_class(result_reject),.result_fallback(result_fallback),
  .list_addr(list_addr),.list_indices(list_indices),.packet_access(packet_access),.packet_write(packet_write),
  .packet_write_addr(packet_write_addr),.packet_write_data(packet_write_data),.packet_read_valid(packet_read_valid),
  .packet_read_addr(packet_read_addr),.packet_read_data(packet_read_data),
  .busy(pre_busy),.done(pre_done),.fallback(pre_fallback),.invalid(pre_invalid),.dma_error(pre_error),
  .prefix_end(),.accepted_commands(accepted_commands),.live_triangles(live_triangles),.checked_triangles(),
  .clip_rejected(clip_rejected),.cull_rejected(cull_rejected),.accepted_triangles(accepted_triangles));
 geometry_run_commit commit (
  .clk(clk),.reset(reset),.start(commit_start),.live_triangles(live_triangles),
  .draw_flags(draw_flags),.initial_stale(initial_stale),.prim_rgba(prim),.env_rgba(env),
  .tile_uls(tile_uls),.tile_ult(tile_ult),.filter_linear(linear_filter),
  .list_addr(list_addr),.list_indices(list_indices),
  .packet_access(packet_access),.packet_write(packet_write),.packet_write_addr(packet_write_addr),
  .packet_write_data(packet_write_data),.packet_read_valid(packet_read_valid),.packet_read_addr(packet_read_addr),.packet_read_data(packet_read_data),
  .raw_read_valid(commit_raw_read),.raw_read_addr(commit_raw_addr),.raw_read_data(raw_data),
  .geo_request(geo_request),.geo_words(geo_words),.geo_grant(geo_grant),.geo_valid(geo_valid),.geo_data(geo_data),
  .geo_ready(geo_ready),.geo_abort(geo_abort),.geo_owned(geo_owned),.geo_done(geo_done),
  .busy(commit_busy),.done(commit_done),.unsupported(commit_bad),.final_stale(final_stale),
  .emitted_words(emitted_words),.emitted_vertices(emitted_vertices));
 always @*begin
  reg_rdata=0;
  if(reg_addr[9:8]==2'b01)begin
   if(!active&&raw_valid[reg_addr[4:0]])reg_rdata=raw_data;
  end else case(reg_addr)
   10'h000:reg_rdata=32'h47445201;
   10'h001:reg_rdata={23'b0,&matrix_valid,config_error,read_error,all_inside,unsupported,success,completed,active};
   10'h003:reg_rdata=source;
   10'h004:reg_rdata={19'b0,dest,2'b0,count};
   10'h013:reg_rdata={25'b0,accepted_commands};10'h014:reg_rdata={25'b0,live_triangles};
   10'h015:reg_rdata={29'b0,reason};
   10'h016:reg_rdata={8'b0,1'b0,accepted_triangles,1'b0,cull_rejected,1'b0,clip_rejected};
   10'h017:reg_rdata=final_stale;
   10'h01e:reg_rdata={19'b0,emitted_words};10'h01f:reg_rdata={24'b0,emitted_vertices};
   default:reg_rdata=0;
  endcase
 end
 always @(posedge clk)begin
  if(reset)begin
   operation<=IDLE;pending<=0;completed<=0;success<=0;unsupported<=0;config_error<=0;read_error<=0;reason<=0;
   source<=0;aspect<=32'h3f800000;cx<=0;cy<=0;cz<=0;
   count<=0;dest<=0;ambient<=0;diffuse<=0;scale_u<=0;scale_v<=0;matrix_valid<=0;lighting<=0;
   command_source<=0;draw_flags<=0;initial_stale<=0;prim<=0;env<=0;tile_uls<=0;tile_ult<=0;linear_filter<=0;
   budget<=64;cull_mode<=0;
  end else begin
   if(operation==LOAD&&raw_done)begin
    operation<=IDLE;completed<=1;success<=!raw_bad&&raw_error==0;unsupported<=raw_bad;read_error<=raw_error;
    reason<=raw_error==1?3'd6:raw_error!=0?3'd5:raw_bad?3'd4:3'd0;
   end
   if(operation==PRECHECK&&result_valid&&result_ready&&result_fallback)reason<=result_reason;
   if(operation==PRECHECK&&pre_done)begin
    operation<=IDLE;completed<=1;success<=!pre_invalid&&pre_error==0;read_error<=pre_error;
    unsupported<=pre_invalid;pending<=live_triangles!=0;
    if(pre_error!=0)reason<=pre_error==1?3'd6:3'd5;
    else if(pre_invalid)reason<=6;
   end
   if(operation==COMMIT&&commit_done)begin
    operation<=IDLE;completed<=1;success<=!commit_bad;unsupported<=commit_bad;pending<=0;
    if(commit_bad)reason<=4;
   end
   if(write_idle)begin
    if(matrix_we)matrix_valid[reg_addr[3:0]]<=1'b1;
    if(control)begin
     if(reg_wdata==2)begin completed<=0;config_error<=0;end
     else if(reg_wdata==16)begin pending<=0;completed<=1;success<=1;unsupported<=0;config_error<=0;read_error<=0;reason<=0;end
     else begin
      completed<=0;success<=0;unsupported<=0;config_error<=0;read_error<=0;reason<=0;
      if(load_start)operation<=LOAD;
      else if(pre_start)operation<=PRECHECK;
      else if(commit_start)operation<=COMMIT;
      else begin completed<=1;config_error<=1;reason<=6;end
     end
    end else if(!pending)begin
     case(reg_addr)
      10'h003:source<=reg_wdata;
      10'h004:begin count<=reg_wdata[5:0];dest<=reg_wdata[12:8];end
      10'h005:aspect<=reg_wdata;
      10'h006:cx<=reg_wdata;10'h007:cy<=reg_wdata;10'h008:cz<=reg_wdata;
      10'h009:ambient<=reg_wdata[23:0];10'h00a:diffuse<=reg_wdata[23:0];
      10'h00b:begin scale_u<=reg_wdata[15:0];scale_v<=reg_wdata[31:16];end
      10'h00c:lighting<=reg_wdata[0];
      10'h011:command_source<=reg_wdata;
      10'h012:begin budget<=reg_wdata[14:8];cull_mode<=reg_wdata[1:0];end
     endcase
    end
    // DRAW is intentionally writable while a PRECHECK result is reserved.
    case(reg_addr)
     10'h018:draw_flags<=reg_wdata;
     10'h019:initial_stale<=reg_wdata;
     10'h01a:prim<=reg_wdata;10'h01b:env<=reg_wdata;
     10'h01c:begin tile_uls<=reg_wdata[15:0];tile_ult<=reg_wdata[31:16];end
     10'h01d:linear_filter<=reg_wdata[0];
    endcase
   end
  end
 end
endmodule
