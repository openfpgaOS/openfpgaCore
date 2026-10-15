module tb_front(
 input wire clk,reset_n,enable,soft_reset,ring_reset,
 input wire upload_we,input wire [11:0] upload_addr,input wire [31:0] upload_data,
 input wire [11:0] published,
 output wire [11:0] consumed,
 input wire [11:0] read_pointer,
 output wire [11:0] write_pointer,output wire [31:0] read_data,
 output wire busy,fault,push,output wire [31:0] pushed_word,
 output wire ctx_valid,input wire ctx_ready,output wire[4:0]ctx_index,output wire[31:0]ctx_data,
 output wire job_valid,input wire job_ready,
 output wire[31:0]j0,j1,j2,j3,j4,j5,j6,j7,j8,j9,j10,
 input wire word_valid,input wire[31:0]word_data,output wire word_ready,
 input wire job_done,job_fault
);
 reg[31:0] ingress[0:4095];
 wire[11:0] address;reg[31:0] data;
 always @(posedge clk)begin
  if(upload_we)ingress[upload_addr]<=upload_data;
  data<=ingress[address];
 end
 gpu_mesh_job_front #(.ADDR_BITS(12),.QUEUE_BITS(9)) dut(
  .clk(clk),.reset_n(reset_n),.enable(enable),.soft_reset(soft_reset),.ring_reset(ring_reset),
  .input_wrptr(published),.input_rdptr(consumed),.input_addr(address),.input_data(data),
  .output_rdptr(read_pointer),.output_addr(read_pointer),.output_data(read_data),.output_wrptr(write_pointer),
  .busy(busy),.fault(fault),
  .mesh_ctx_valid(ctx_valid),.mesh_ctx_ready(ctx_ready),.mesh_ctx_index(ctx_index),.mesh_ctx_data(ctx_data),
  .mesh_job_valid(job_valid),.mesh_job_ready(job_ready),
  .mesh_job_seq(j0),.mesh_job_vertex(j1),.mesh_job_vtx_w0(j2),.mesh_job_commands(j3),
  .mesh_job_control(j4),.mesh_job_flags(j5),.mesh_job_uofs(j6),.mesh_job_vofs(j7),
  .mesh_job_cpu_vertex(j8),.mesh_job_cpu_commands(j9),.mesh_job_alpha(j10),
  .mesh_word_valid(word_valid),.mesh_word_data(word_data),.mesh_word_ready(word_ready),
  .mesh_job_done(job_done),.mesh_job_fault(job_fault)
 );
 assign push=dut.output_push;assign pushed_word=dut.output_word;
endmodule
