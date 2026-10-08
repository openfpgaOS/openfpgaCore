// Private LOD4: completed native LOAD remains in BRAM. CPU copies the
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
 geometry_raw_load #(.CAPTURE_CONTEXT(0),.ENABLE_INVALIDATE(0)) load(
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
   10'h000:reg_rdata=32'h4c4f4404;
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
