module tb_queued_front (
 input wire clk,reset_n,enable,soft_reset,ring_reset,
 input wire [11:0] input_wrptr,input wire input_we,input wire [11:0] input_write_addr,input wire [31:0] input_write_data,
 output wire [11:0] input_rdptr,
 input wire [11:0] output_rdptr,output_addr,output wire [11:0] output_wrptr,output wire [31:0] output_data,
 output wire busy,
 input wire raw_we,input wire [7:0] raw_write_addr,input wire [31:0] raw_write_data,
 input wire producer_success,producer_valid,producer_busy,input wire [5:0] producer_count,
 input wire claim,input wire [31:0] claim_token,output wire [31:0] generation,claim_result,
 output wire copy_busy,view_error_sticky,view_owner,output wire [1:0] free_banks,
 input wire math_allow,
 input wire queue_reg_wr,input wire [9:0] queue_reg_addr,input wire [31:0] queue_reg_wdata,input wire [3:0] queue_reg_wstrb,
 output wire [31:0] queue_reg_rdata,
 output wire [4:0] state,output wire output_push,output wire [31:0] output_word,
 output wire view_release,view_done,view_fault,output wire [31:0] view_release_token,
 output wire math_grant,math_release,fma_result_valid,output wire [31:0] fma_result,output wire fma_bad
);
 wire reset=!reset_n,consumer_reset=soft_reset||ring_reset;
 wire [11:0] input_addr;reg [31:0] input_data;reg[31:0] ingress[0:4095];
 wire view_req,view_grant,view_error,math_req;
 wire [31:0] view_token,view_data;
 wire [4:0] view_source;wire [7:0] view_addr;
 wire [7:0] raw_addr;reg [31:0] raw_data;reg [31:0] raw[0:255];
 wire fma_valid,fma_multiply;wire [31:0] fma_a,fma_b,fma_c;
 wire sread,swrite;wire [2:0] sread_addr,swrite_addr;
 reg[31:0] scratch[0:7],sdata;wire [31:0] swdata;reg granted;
 assign math_grant=granted&&!reset&&!consumer_reset;
 assign state=dut.state,output_push=dut.output_push,output_word=dut.output_word;
 always @(posedge clk)begin
  input_data<=ingress[input_addr];if(input_we)ingress[input_write_addr]<=input_write_data;
  raw_data<=raw[raw_addr];if(raw_we)raw[raw_write_addr]<=raw_write_data;
  if(sread)sdata<=scratch[sread_addr];if(swrite)scratch[swrite_addr]<=swdata;
  if(reset||consumer_reset)granted<=0;
  else if(math_release)granted<=0;
  else if(math_req&&math_allow)granted<=1;
 end
 geometry_import_view snapshots(
  .clk(clk),.reset(reset),.consumer_reset(consumer_reset),
  .result_success(producer_success),.result_valid(producer_valid),.producer_active(producer_busy),.result_count(producer_count),
  .claim(claim),.claim_token(claim_token),.clear_error(1'b0),.producer_rejected(1'b0),
  .generation(generation),.leased(copy_busy),.error(view_error_sticky),.claim_result(claim_result),.free_banks(free_banks),
  .cpu_view_active(1'b0),.cpu_view_addr(8'b0),.cpu_view_block(),.result_addr(raw_addr),.result_data(raw_data),
  .gpu_req(view_req),.gpu_token(view_token),.gpu_src(view_source),.gpu_addr(view_addr),.gpu_grant(view_grant),.gpu_error(view_error),.gpu_data(view_data),
  .gpu_read_done(view_done),.gpu_release(view_release),.gpu_fault(view_fault),.gpu_release_token(view_release_token),.owner(view_owner)
 );
 gpu_queued_front #(.QUEUE_BITS(11)) dut(
  .clk(clk),.reset_n(reset_n),.enable(enable),.soft_reset(soft_reset),.ring_reset(ring_reset),
  .input_wrptr(input_wrptr),.input_rdptr(input_rdptr),.input_addr(input_addr),.input_data(input_data),
  .output_rdptr(output_rdptr),.output_addr(output_addr),.output_data(output_data),.output_wrptr(output_wrptr),.busy(busy),
  .view_req(view_req),.view_token(view_token),.view_source(view_source),.view_addr(view_addr),.view_done(view_done),
  .view_grant(view_grant),.view_error(view_error),.view_data(view_data),.view_release(view_release),.view_fault(view_fault),.view_release_token(view_release_token),
  .math_req(math_req),.math_grant(math_grant),.math_release(math_release),
  .math_fma_valid(fma_valid),.math_fma_a(fma_a),.math_fma_b(fma_b),.math_fma_c(fma_c),.math_fma_multiply(fma_multiply),
  .math_fma_result_valid(fma_result_valid),.math_fma_result(fma_result),.math_fma_unsupported(fma_bad),
  .math_scratch_read(sread),.math_scratch_write(swrite),.math_scratch_read_addr(sread_addr),.math_scratch_write_addr(swrite_addr),.math_scratch_data(sdata),.math_scratch_write_data(swdata),
  .queue_reg_wr(queue_reg_wr),.queue_reg_addr(queue_reg_addr),.queue_reg_wdata(queue_reg_wdata),.queue_reg_wstrb(queue_reg_wstrb),.queue_reg_rdata(queue_reg_rdata)
 );
 GeometryFmaProbe fma(.clk(clk),.reset(reset||consumer_reset),.io_valid(fma_valid),.io_a(fma_a),.io_b(fma_b),.io_c(fma_c),.io_multiplyOnly(fma_multiply),.io_resultValid(fma_result_valid),.io_result(fma_result),.io_unsupported(fma_bad));
endmodule
