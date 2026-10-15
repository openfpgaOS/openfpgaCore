module tb_triangle_preflight (
 input wire clk,reset,reg_wr,input wire [9:0]reg_addr,
 input wire [31:0]reg_wdata,input wire[3:0]reg_wstrb,output wire[31:0]reg_rdata,
 input wire[31:0]generation,input wire source_valid,producer_active,
 input wire[5:0]source_count,input wire alias_active,copy_busy,conflict,
 input wire raw_we,input wire[7:0]raw_write_addr,input wire[31:0]raw_write_data,
 output wire busy,reserve,port_owner,output wire[3:0]state,
 output wire fma_valid,fma_result_valid,fma_unsupported,output wire[31:0]fma_result,
 output wire memory_collision
);
 wire[7:0]raw_addr;reg[31:0]raw_data;
 wire scratch_read,scratch_write;wire[6:0]scratch_read_addr,scratch_write_addr;
 reg[31:0]scratch_data;wire[31:0]scratch_write_data;
 wire[31:0]fma_a,fma_b,fma_c;wire fma_multiply_only;
 reg[31:0]raw[0:255],scratch[0:127];
 assign state=dut.state;
 assign memory_collision=scratch_read&&scratch_write&&scratch_read_addr==scratch_write_addr;
 always @(posedge clk)begin
  raw_data<=raw[raw_addr];
  if(raw_we)raw[raw_write_addr]<=raw_write_data;
  if(scratch_read)scratch_data<=scratch[scratch_read_addr];
  if(scratch_write)scratch[scratch_write_addr]<=scratch_write_data;
 end
 geometry_triangle_preflight dut(.*);
 GeometryFmaProbe fma(.clk(clk),.reset(reset),.io_valid(fma_valid),.io_a(fma_a),.io_b(fma_b),.io_c(fma_c),
  .io_multiplyOnly(fma_multiply_only),.io_resultValid(fma_result_valid),.io_result(fma_result),.io_unsupported(fma_unsupported));
endmodule
