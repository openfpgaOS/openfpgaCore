// Private throughput probe: actual F11 xyzw transform DAG, batched over 1..32
// vertices. Matrix/position/output RAMs are local, independently owned by a job.
// No DMA, reciprocal, lighting, culling or GPU integration is claimed.
module geometry_float_batch (
 input wire clk, reset,
 input wire matrix_we,input wire [3:0] matrix_addr,input wire [31:0] matrix_data,
 input wire position_we,input wire [6:0] position_addr,input wire [31:0] position_data,
 input wire start,input wire [5:0] count,input wire [31:0] aspect,
 input wire [6:0] output_addr,output reg [31:0] output_data,
 output reg busy,done,unsupported,
 output reg [31:0] cycles
);
 (* ramstyle="M10K, no_rw_check" *) reg [31:0] matrix[0:15];
 (* ramstyle="M10K, no_rw_check" *) reg [31:0] positions[0:95];
 (* ramstyle="M10K, no_rw_check" *) reg [31:0] acc[0:127];
 reg [2:0] phase;
 reg [1:0] row;
 reg [4:0] vertex;
 reg [5:0] n;
 reg [31:0] job_aspect;
 reg drain;
 reg read_valid;
 reg [2:0] read_phase;
 reg [6:0] read_tag;
 reg [31:0] pos_q,coef_q,acc_q;
 reg [6:0] tags[0:6];
 reg [4:0] outstanding;
 wire issuing=busy && !drain;
 wire [1:0] axis=phase==0?2'd1:phase==3?2'd2:2'd0;
 wire [6:0] pos_index={vertex,1'b0}+vertex+axis;
 wire [1:0] coefficient_column=phase==0?2'd1:phase==1?2'd0:phase==2?2'd3:2'd2;
 wire [3:0] coefficient_index={coefficient_column,row};
 wire [6:0] tag={row,vertex};
 wire [31:0] fma_a=read_phase==2?coef_q:read_phase==4?acc_q:pos_q;
 wire [31:0] fma_b=read_phase==2?32'h3f800000:read_phase==4?job_aspect:coef_q;
 wire [31:0] fma_c=(read_phase==0||read_phase==4)?32'd0:acc_q;
 wire fma_mul=read_phase==0||read_phase==4;
 wire [31:0] result;
 wire result_valid,result_unsupported;
 GeometryFmaProbe fma(.clk(clk),.reset(reset),.io_valid(read_valid),
  .io_a(fma_a),.io_b(fma_b),.io_c(fma_c),.io_multiplyOnly(fma_mul),
  .io_result(result),.io_resultValid(result_valid),.io_unsupported(result_unsupported));
 integer i;
 always @(posedge clk) begin
  if(!busy) begin
   if(matrix_we)matrix[matrix_addr]<=matrix_data;
   if(position_we && position_addr<96)positions[position_addr]<=position_data;
  end
  if(issuing) begin
   pos_q<=positions[pos_index];coef_q<=matrix[coefficient_index];acc_q<=acc[tag];
  end
  output_data<=acc[output_addr];
  if(result_valid && busy)acc[tags[6]]<=result;
  if(reset) begin
   busy<=0;done<=0;unsupported<=0;cycles<=0;drain<=0;read_valid<=0;
   outstanding<=0;phase<=0;row<=0;vertex<=0;n<=0;job_aspect<=0;
   read_tag<=0;read_phase<=0;
   for(i=0;i<7;i=i+1)tags[i]<=0;
  end else begin
   done<=0;
   read_valid<=issuing;
   read_phase<=phase;read_tag<=tag;
   tags[0]<=read_tag;
   for(i=1;i<7;i=i+1)tags[i]<=tags[i-1];
   case({read_valid,result_valid && busy})
    2'b10:outstanding<=outstanding+1'b1;
    2'b01:outstanding<=outstanding-1'b1;
    default:outstanding<=outstanding;
   endcase
   if(busy) begin
    cycles<=cycles+1'b1;
    if(result_valid && result_unsupported)unsupported<=1;
    if(issuing) begin
     if({1'b0,vertex}+1'b1==n)begin
      vertex<=0;
      if(row==3 || phase==4)begin row<=0;drain<=1;end
      else row<=row+1'b1;
     end else vertex<=vertex+1'b1;
    end
    if(drain && outstanding==0 && !read_valid && !result_valid)begin
     if(phase==4)begin busy<=0;done<=1;end
     else begin phase<=phase+1'b1;drain<=0;end
    end
   end else if(start)begin
    if(count==0 || count>32)begin done<=1;unsupported<=1;end
    else begin
     busy<=1;done<=0;unsupported<=0;cycles<=0;phase<=0;row<=0;vertex<=0;
     n<=count;job_aspect<=aspect;drain<=0;outstanding<=0;
    end
   end
  end
 end
endmodule
