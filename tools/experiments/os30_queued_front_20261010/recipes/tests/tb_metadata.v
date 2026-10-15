// The production raw LOAD/SCAN logic is under test. Only its transform is
// replaced with a synchronous result RAM, so exact boundary floats can be
// injected without depending on transform rounding to generate them.
module tb_metadata (
 input wire clk,reset,start,compute_bad,
 input wire [5:0] count,input wire [4:0] dest,
 input wire dma_done,input wire [1:0] dma_error,
 input wire word_we,input wire [7:0] word_addr,input wire [31:0] word_data,
 input wire [7:0] output_addr,output wire [31:0] output_data,
 output wire busy,done,unsupported,all_inside,
 output wire [31:0] importable_slots,w_fingerprint0,w_fingerprint1,w_fingerprint2,w_fingerprint3
);
 always @(posedge clk)begin
  if(word_we)dut.compute.words[word_addr]<=word_data;
  dut.compute.test_unsupported<=compute_bad;
 end
 geometry_raw_load #(.EXTERNAL_DMA(1)) dut(
  .clk(clk),.reset(reset),.start(start),.count(count),.dest(dest),.job_base(32'h10000000),
  .slot_invalidate(1'b0),.invalidate_mask(32'b0),.matrix_we(1'b0),.matrix_addr(4'b0),.matrix_data(32'b0),
  .aspect(32'h3f800000),.coefficient_x(32'b0),.coefficient_y(32'b0),.coefficient_z(32'b0),
  .ambient_rgb(24'b0),.diffuse_rgb(24'b0),.lighting_enabled(1'b0),.scale_u(16'b0),.scale_v(16'b0),
  .m_arready(1'b0),.m_rvalid(1'b0),.m_rdata(32'b0),.m_rresp(2'b0),.m_rlast(1'b0),
  .dma_response_busy(1'b0),.dma_response_done(dma_done),.dma_response_error(dma_error),
  .dma_response_word_valid(1'b0),.dma_response_word_index(8'b0),.dma_response_word_data(32'b0),
  .output_addr(output_addr),.output_data(output_data),.busy(busy),.done(done),.unsupported(unsupported),
  .all_inside(all_inside),.importable_slots(importable_slots),
  .w_fingerprint0(w_fingerprint0),.w_fingerprint1(w_fingerprint1),.w_fingerprint2(w_fingerprint2),.w_fingerprint3(w_fingerprint3),
  .fma_response_valid(1'b0),.fma_response_data(32'b0),.fma_response_unsupported(1'b0),
  .scratch_read_valid(1'b0),.scratch_read_addr(7'b0),.scratch_write(1'b0),.scratch_write_addr(7'b0),.scratch_write_data(32'b0)
 );
endmodule
