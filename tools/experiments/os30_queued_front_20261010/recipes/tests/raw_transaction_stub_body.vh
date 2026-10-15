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
