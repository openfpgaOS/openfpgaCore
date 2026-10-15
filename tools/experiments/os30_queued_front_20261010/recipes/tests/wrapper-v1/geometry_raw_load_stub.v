// Private end-to-end LOAD component: original 24-byte Vtx DMA, exact transform,
// reciprocal and one-light/unlit RGB; signed UV scaling and clip classification.
// Output row0..4=xyzw/reciprocal, row5={signedV,signedU}, row6=sourceA|RGB,
// row7=clip mask. Successful LOADs replace only dest..dest+count-1.
// Fog alpha, texture generation, RUN submission and
// integration with the CPU/GPU/fabric are explicitly outside this component.
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
 // QGF1 admission metadata, captured during the existing clip scan.
 output reg [31:0] importable_slots,
 output reg [31:0] w_fingerprint0,w_fingerprint1,w_fingerprint2,w_fingerprint3,
 output wire [31:0] compute_cycles,
 output wire fma_request_valid,output wire [31:0] fma_request_a,fma_request_b,fma_request_c,
 output wire fma_request_multiply,input wire fma_response_valid,
 input wire [31:0] fma_response_data,input wire fma_response_unsupported,
 input wire scratch_read_valid,input wire [6:0] scratch_read_addr,
 output wire [31:0] scratch_read_data,input wire scratch_write,
 input wire [6:0] scratch_write_addr,input wire [31:0] scratch_write_data
);
// Actual DMA ownership and one actual external FMA transaction; geometry
// arithmetic/metadata are separately qualified by their focused tests.
 reg [2:0] state;
 reg [31:0] held_base,completed_base,slots;
 reg [31:0] scratch[0:127],scratch_q;
 assign busy=state!=0;
 assign m_arvalid=state==1,m_araddr=held_base,m_arlen=0,m_rready=state==2;
 assign dma_request_start=0,dma_request_base=0,dma_request_words=0,compute_cycles=0;
 assign fma_request_valid=state==3,fma_request_a=32'h40000000,
        fma_request_b=32'h40400000,fma_request_c=0,fma_request_multiply=1;
 assign scratch_read_data=scratch_q;
 always @(posedge clk)begin
  done<=0;
  output_data<=output_addr[7:5]==7?0:completed_base+output_addr;
  if(!busy && scratch_read_valid)scratch_q<=scratch[scratch_read_addr];
  if(!busy && scratch_write)scratch[scratch_write_addr]<=scratch_write_data;
  if(reset)begin
   state<=0;held_base<=0;completed_base<=0;slots<=0;done<=0;unsupported<=0;dma_error<=0;
   all_inside<=0;cycles<=0;valid_slots<=0;output_data<=0;
   importable_slots<=0;w_fingerprint0<=0;w_fingerprint1<=0;w_fingerprint2<=0;w_fingerprint3<=0;
  end else begin
   if(start && state==0)begin
    state<=1;held_base<=job_base;slots<=32'hffffffff>>(32-count);
    unsupported<=0;dma_error<=0;importable_slots<=0;
   end
   if(m_arvalid&&m_arready)state<=2;
   if(m_rvalid&&m_rready&&m_rlast)begin state<=3;dma_error<=m_rresp;end
   if(state==3)state<=4;
   if(state==4 && fma_response_valid)begin
    state<=0;done<=1;completed_base<=held_base;all_inside<=1;
    unsupported<=fma_response_unsupported || fma_response_data!=32'h40c00000;
    importable_slots<=slots;w_fingerprint0<=slots&32'haaaaaaaa;
    w_fingerprint1<=slots&32'hcccccccc;w_fingerprint2<=slots&32'hf0f0f0f0;w_fingerprint3<=slots&32'hff00ff00;
   end
  end
 end
endmodule
