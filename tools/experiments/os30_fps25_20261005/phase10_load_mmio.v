// Private LOAD-only MMIO resource experiment. Full-word writes only.
// No result DMA writer: software may read the synchronous result window only
// after successful completion. Configuration and matrix writes while busy
// are ignored. Reset must reset the SDRAM arbiter/responders simultaneously.
module geometry_load_mmio (
 input wire clk,reset,
 input wire reg_wr,input wire [9:0] reg_addr,
 input wire [31:0] reg_wdata,input wire [3:0] reg_wstrb,
 output reg [31:0] reg_rdata,
 output wire m_arvalid,input wire m_arready,output wire [31:0] m_araddr,
 output wire [7:0] m_arlen,input wire m_rvalid,output wire m_rready,
 input wire [31:0] m_rdata,input wire [1:0] m_rresp,input wire m_rlast
);
 reg [31:0] source_base,aspect,cx,cy,cz;
 reg [5:0] count;
 reg [4:0] dest;
 reg [23:0] ambient,diffuse;
 reg [15:0] scale_u,scale_v,matrix_valid;
 reg lit,completed,success,config_error;
 wire busy,raw_done,unsupported,all_inside;
 wire [1:0] dma_error;
 wire [31:0] cycles,compute_cycles,valid_slots,raw_result;
 wire write_idle=reg_wr && reg_wstrb==4'hf && !busy;
 wire start_request=write_idle && reg_addr==10'h002 && reg_wdata[0];
 wire start_fire=start_request && (&matrix_valid);
 wire matrix_we=write_idle && reg_addr[9:4]==6'h04;
 geometry_raw_load load(
  .clk(clk),.reset(reset),
  .matrix_we(matrix_we),.matrix_addr(reg_addr[3:0]),.matrix_data(reg_wdata),
  .start(start_fire),.job_base(source_base),.count(count),.dest(dest),
  .aspect(aspect),.coefficient_x(cx),.coefficient_y(cy),.coefficient_z(cz),
  .ambient_rgb(ambient),.diffuse_rgb(diffuse),.lighting_enabled(lit),
  .scale_u(scale_u),.scale_v(scale_v),
  .m_arvalid(m_arvalid),.m_arready(m_arready),.m_araddr(m_araddr),.m_arlen(m_arlen),
  .m_rvalid(m_rvalid),.m_rready(m_rready),.m_rdata(m_rdata),.m_rresp(m_rresp),.m_rlast(m_rlast),
  .output_addr(reg_addr[7:0]),.output_data(raw_result),
  .busy(busy),.done(raw_done),.unsupported(unsupported),.dma_error(dma_error),
  .all_inside(all_inside),.cycles(cycles),.valid_slots(valid_slots),.compute_cycles(compute_cycles),
  .fma_request_valid(),.fma_request_a(),.fma_request_b(),.fma_request_c(),.fma_request_multiply(),
  .fma_response_valid(1'b0),.fma_response_data(32'b0),.fma_response_unsupported(1'b0),
  .scratch_read_valid(1'b0),.scratch_read_addr(7'b0),.scratch_read_data(),
  .scratch_write(1'b0),.scratch_write_addr(7'b0),.scratch_write_data(32'b0));
 always @*begin
  reg_rdata=0;
  if(reg_addr[9:8]==2'b01)begin
   if(!busy && success && valid_slots[reg_addr[4:0]])reg_rdata=raw_result;
  end else case(reg_addr)
   10'h000:reg_rdata=32'h4c4f4401; // LOD, private interface version1
   10'h001:reg_rdata={23'b0,&matrix_valid,config_error,dma_error,all_inside,unsupported,success,completed,busy};
   10'h003:reg_rdata=source_base;
   10'h004:reg_rdata={19'b0,dest,2'b0,count};
   10'h005:reg_rdata=aspect;
   10'h006:reg_rdata=cx;
   10'h007:reg_rdata=cy;
   10'h008:reg_rdata=cz;
   10'h009:reg_rdata={8'b0,ambient};
   10'h00a:reg_rdata={8'b0,diffuse};
   10'h00b:reg_rdata={scale_v,scale_u};
   10'h00c:reg_rdata={31'b0,lit};
   10'h00d:reg_rdata=cycles;
   10'h00e:reg_rdata=compute_cycles;
   10'h00f:reg_rdata=valid_slots;
   default:reg_rdata=0;
  endcase
 end
 always @(posedge clk)begin
  if(reset)begin
   source_base<=0;aspect<=32'h3f800000;cx<=0;cy<=0;cz<=0;
   count<=0;dest<=0;ambient<=0;diffuse<=0;scale_u<=0;scale_v<=0;
   lit<=0;completed<=0;success<=0;config_error<=0;matrix_valid<=0;
  end else begin
   if(raw_done)begin completed<=1;success<=!unsupported && dma_error==0;end
   if(write_idle)begin
    if(matrix_we)matrix_valid[reg_addr[3:0]]<=1'b1;
    case(reg_addr)
     10'h002:begin
      if(reg_wdata[1])begin completed<=0;config_error<=0;end
      if(reg_wdata[0])begin
       completed<=!(&matrix_valid);success<=0;config_error<=!(&matrix_valid);
      end
     end
     10'h003:source_base<=reg_wdata;
     10'h004:begin count<=reg_wdata[5:0];dest<=reg_wdata[12:8];end
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

