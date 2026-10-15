module tb_async_combined(
 input wire clk,reset,enable,consumer_reset,
 input wire upload_we,input wire [11:0] upload_addr,input wire [31:0] upload_data,input wire [11:0] published,
 output wire [11:0] consumed,input wire [11:0] read_pointer,output wire [11:0] write_pointer,output wire [31:0] read_data,
 output wire busy,fault,push,output wire [31:0] pushed_word,
 input wire reg_wr,input wire [9:0] reg_addr,input wire [31:0] reg_wdata,input wire [3:0] reg_wstrb,output wire [31:0] reg_rdata,
 output wire m_arvalid,input wire m_arready,output wire [31:0] m_araddr,output wire [7:0] m_arlen,
 input wire m_rvalid,output wire m_rready,input wire [31:0] m_rdata,input wire [1:0] m_rresp,input wire m_rlast,
 input wire [7:0] view_read_addr,output wire [31:0] view_read_data,input wire view_active,output wire view_block,view_valid,output wire [5:0] view_count
);
 reg [31:0] ingress[0:4095];wire [11:0] address;reg [31:0] data;
 always @(posedge clk)begin if(upload_we)ingress[upload_addr]<=upload_data;data<=ingress[address];end
 wire cv,cr,jv,jr,wv,wr,jd,jf;wire [4:0] ci;wire [31:0] cd,wd,j0,j1,j2,j3,j4,j5,j6,j7,j8,j9,j10;
 gpu_mesh_job_front front(.clk(clk),.reset_n(!reset),.enable(enable),.soft_reset(consumer_reset),.ring_reset(1'b0),
 .input_wrptr(published),.input_rdptr(consumed),.input_addr(address),.input_data(data),
 .output_rdptr(read_pointer),.output_addr(read_pointer),.output_data(read_data),.output_wrptr(write_pointer),.busy(busy),.fault(fault),
 .mesh_ctx_valid(cv),.mesh_ctx_ready(cr),.mesh_ctx_index(ci),.mesh_ctx_data(cd),.mesh_job_valid(jv),.mesh_job_ready(jr),
 .mesh_job_seq(j0),.mesh_job_vertex(j1),.mesh_job_vtx_w0(j2),.mesh_job_commands(j3),.mesh_job_control(j4),.mesh_job_flags(j5),.mesh_job_uofs(j6),.mesh_job_vofs(j7),.mesh_job_cpu_vertex(j8),.mesh_job_cpu_commands(j9),.mesh_job_alpha(j10),
 .mesh_word_valid(wv),.mesh_word_data(wd),.mesh_word_ready(wr),.mesh_job_done(jd),.mesh_job_fault(jf));
 geometry_load_alias geometry_load(.clk(clk),.reset(reset),.reg_wr(reg_wr),.reg_addr(reg_addr),.reg_wdata(reg_wdata),.reg_wstrb(reg_wstrb),.reg_rdata(reg_rdata),
 .m_arvalid(m_arvalid),.m_arready(m_arready),.m_araddr(m_araddr),.m_arlen(m_arlen),.m_rvalid(m_rvalid),.m_rready(m_rready),.m_rdata(m_rdata),.m_rresp(m_rresp),.m_rlast(m_rlast),
 .view_read_addr(view_read_addr),.view_read_data(view_read_data),.view_active(view_active),.view_block(view_block),.view_valid(view_valid),.view_count(view_count),
 .mesh_ctx_valid(cv),.mesh_ctx_ready(cr),.mesh_ctx_index(ci),.mesh_ctx_data(cd),.mesh_job_valid(jv),.mesh_job_ready(jr),
 .mesh_job_seq(j0),.mesh_job_vertex(j1),.mesh_job_vtx_w0(j2),.mesh_job_commands(j3),.mesh_job_control(j4),.mesh_job_flags(j5),.mesh_job_uofs(j6),.mesh_job_vofs(j7),.mesh_job_cpu_vertex(j8),.mesh_job_cpu_commands(j9),.mesh_job_alpha(j10),
 .mesh_word_valid(wv),.mesh_word_data(wd),.mesh_word_ready(wr),.mesh_job_done(jd),.mesh_job_fault(jf),.mesh_consumer_reset(consumer_reset),.mesh_bridge_fault(fault));
 assign push=front.output_push;assign pushed_word=front.output_word;
endmodule
