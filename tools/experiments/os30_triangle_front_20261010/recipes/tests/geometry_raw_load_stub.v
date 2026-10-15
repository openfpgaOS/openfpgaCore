// Test-only stub uses the exact producer interface. It does not validate geometry arithmetic.
module geometry_raw_load #(parameter EXTERNAL_FMA=0,parameter CAPTURE_CONTEXT=1,parameter ENABLE_INVALIDATE=0,parameter EXTERNAL_DMA=0,
 parameter [31:0] SOURCE_MIN_ADDRESS=32'h10000000,
 parameter [32:0] SOURCE_END_ADDRESS=33'h014000000) (
 input wire clk,reset,
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
 input wire [7:0] output_addr,output reg [31:0] output_data,
 output wire busy,output reg done,unsupported,output reg [1:0] dma_error,
 output reg all_inside,output reg [31:0] cycles,
 output reg [31:0] valid_slots,
 output wire [31:0] compute_cycles,
 output wire fma_request_valid,output wire [31:0] fma_request_a,fma_request_b,fma_request_c,
 output wire fma_request_multiply,input wire fma_response_valid,
 input wire [31:0] fma_response_data,input wire fma_response_unsupported,
 input wire scratch_read_valid,input wire [6:0] scratch_read_addr,
 output wire [31:0] scratch_read_data,input wire scratch_write,
 input wire [6:0] scratch_write_addr,input wire [31:0] scratch_write_data
);
 // Test-only transaction producer: arithmetic is covered independently.
 // Accepted AR remains owned until an explicit RLAST, including consumer reset.
 reg [1:0] state;
 reg [31:0] held_base, completed_base;
 assign busy=state!=0;
 assign m_arvalid=state==1;
 assign m_araddr=held_base;
 assign m_arlen=0;
 assign m_rready=state==2;
 assign dma_request_start=0,dma_request_base=0,dma_request_words=0;
 assign compute_cycles=0,fma_request_valid=0,fma_request_a=0,fma_request_b=0,fma_request_c=0,fma_request_multiply=0;
 assign scratch_read_data=0;
 always @(posedge clk) begin
  done<=0;
  // Clip rows are zero; other rows are a recognizable generation pattern.
  output_data<=output_addr[7:5]==7?32'b0:completed_base+output_addr;
  if(reset) begin
   state<=0;held_base<=0;completed_base<=0;done<=0;unsupported<=0;dma_error<=0;
   all_inside<=0;cycles<=0;valid_slots<=0;output_data<=0;
  end else begin
   if(start && state==0)begin state<=1;held_base<=job_base;unsupported<=0;dma_error<=0;end
   if(m_arvalid&&m_arready)state<=2;
   if(m_rvalid&&m_rready&&m_rlast)begin
    state<=0;done<=1;dma_error<=m_rresp;completed_base<=held_base;all_inside<=1;
   end
  end
 end
endmodule
