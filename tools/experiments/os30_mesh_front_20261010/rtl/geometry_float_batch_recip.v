// Private geometry datapath, not a deployable GPU front end. Executes the
// retained CPU's F11 transform DAG and overlaps a correctly rounded reciprocal.
// Position and accumulator storage each have one read port. The small matrix
// lives in distributed RAM so this block needs only two dedicated block RAMs.
// Output rows 0..3 are xyzw; row 4 is reciprocal(w). Results belong to the job
// only after done && !unsupported. Invalid jobs require the software fallback.
module geometry_float_batch_recip #(parameter EXTERNAL_FMA=0) (
 input wire clk, reset,
 input wire matrix_we,input wire [3:0] matrix_addr,input wire [31:0] matrix_data,
 input wire position_we,input wire [6:0] position_addr,input wire [31:0] position_data,
 input wire start,input wire [5:0] count,input wire [4:0] dest,input wire [31:0] aspect,
 input wire [7:0] output_addr,output reg [31:0] output_data,
 output reg busy,done,unsupported,
 output reg [31:0] cycles,
 output wire transform_finished,
 output wire fma_request_valid,output wire [31:0] fma_request_a,
 output wire [31:0] fma_request_b,fma_request_c,output wire fma_request_multiply,
 input wire fma_response_valid,input wire [31:0] fma_response_data,
 input wire fma_response_unsupported,
 // The original XYZ storage is scratch after transform_finished. One-cycle
 // synchronous read; external users must finish before the next start/upload.
 input wire scratch_read_valid,input wire [6:0] scratch_read_addr,
 output wire [31:0] scratch_read_data,
 input wire scratch_write,input wire [6:0] scratch_write_addr,
 input wire [31:0] scratch_write_data
);
 (* ramstyle="M10K, no_rw_check" *) reg [31:0] positions[0:95];
 (* ramstyle="MLAB, no_rw_check" *) reg [31:0] matrix[0:15];
 (* ramstyle="M10K, no_rw_check" *) reg [31:0] acc[0:127];
 (* ramstyle="MLAB, no_rw_check" *) reg [31:0] w_queue[0:31];
 (* ramstyle="MLAB, no_rw_check" *) reg [31:0] inverses[0:31];
 reg [2:0] phase;
 reg [1:0] row;
 reg [4:0] vertex;
 reg [5:0] n;
 reg [4:0] job_dest;
 reg [31:0] job_aspect;
 reg drain,transform_done;
 reg read_valid;
 reg [2:0] read_phase;
 reg [6:0] read_tag;
 reg [31:0] pos_q,coef_q,acc_q,inv_q;
 reg output_inverse;
 reg [6:0] tags[0:6];
 reg [6:0] final_w;
 reg [4:0] outstanding;
 wire issuing=busy && !drain && !transform_done;
 wire [1:0] axis=phase==0?2'd1:phase==3?2'd2:2'd0;
 wire [6:0] pos_index={1'b0,vertex,1'b0}+{2'b0,vertex}+{5'b0,axis};
 wire [1:0] coefficient_column=phase==0?2'd1:phase==1?2'd0:phase==2?2'd3:2'd2;
 wire [3:0] coefficient_index={coefficient_column,row};
 wire scratch_owned=transform_done && !start;
 wire scratch_writing=scratch_owned && scratch_write;
 wire position_writing=scratch_writing || (!busy && position_we && position_addr<96);
 wire [6:0] position_write_addr=scratch_writing?scratch_write_addr:position_addr;
 wire [31:0] position_write_data=scratch_writing?scratch_write_data:position_data;
 wire scratch_reading=scratch_owned && scratch_read_valid;
 wire [6:0] position_read_addr=scratch_reading?scratch_read_addr:pos_index;
 assign scratch_read_data=pos_q;
 wire [4:0] global_vertex=job_dest+vertex;
 wire [4:0] global_recip=job_dest+recip_index[4:0];
 wire [6:0] tag={row,global_vertex};
 wire [31:0] fma_a=read_phase==2?coef_q:read_phase==4?acc_q:pos_q;
 wire [31:0] fma_b=read_phase==2?32'h3f800000:read_phase==4?job_aspect:coef_q;
 wire [31:0] fma_c=(read_phase==0||read_phase==4)?32'd0:acc_q;
 wire fma_mul=read_phase==0||read_phase==4;
 wire [31:0] result;
 wire result_valid,result_unsupported;
 assign transform_finished=transform_done;
 assign fma_request_valid=read_valid;
 assign fma_request_a=fma_a;assign fma_request_b=fma_b;assign fma_request_c=fma_c;
 assign fma_request_multiply=fma_mul;
 generate if(EXTERNAL_FMA)begin:g_external
  assign result=fma_response_data;assign result_valid=fma_response_valid;
  assign result_unsupported=fma_response_unsupported;
 end else begin:g_internal
 GeometryFmaProbe fma(.clk(clk),.reset(reset),.io_valid(read_valid),
  .io_a(fma_a),.io_b(fma_b),.io_c(fma_c),.io_multiplyOnly(fma_mul),
  .io_result(result),.io_resultValid(result_valid),.io_unsupported(result_unsupported));
 end endgenerate

 reg [5:0] w_written,recip_index,recip_completed;
 reg [1:0] recip_state;
 reg [31:0] w_q;
 reg [4:0] recip_tag;
 wire recip_in_ready,recip_out_valid,recip_unsupported;
 wire [31:0] reciprocal;
 geometry_float_recip2 recip(.clk(clk),.reset(reset),
  .in_valid(busy && recip_state==1),.in_ready(recip_in_ready),.denominator(w_q),
  .out_valid(recip_out_valid),.out_ready(1'b1),
  .reciprocal(reciprocal),.unsupported(recip_unsupported));

 // Context writes are accepted only while idle; busy start/configuration
 // attempts are ignored. The input arrays need only simple dual-port storage.
 always @(posedge clk) begin
  if(position_writing && position_write_addr<96)positions[position_write_addr]<=position_write_data;
  if(issuing || scratch_reading)pos_q<=positions[position_read_addr];
 end
 always @(posedge clk) begin
  if(!busy && matrix_we)matrix[matrix_addr]<=matrix_data;
  if(issuing)coef_q<=matrix[coefficient_index];
 end
 always @(posedge clk) begin
  acc_q<=acc[busy?tag:output_addr[6:0]];
  inv_q<=inverses[output_addr[4:0]];
  output_inverse<=output_addr[7];
  if(result_valid && busy)acc[tags[6]]<=result;
  if(result_valid && final_w[6] && busy)w_queue[tags[6][4:0]]<=result;
  if(busy && recip_state==0 && recip_index<w_written)w_q<=w_queue[global_recip];
  if(recip_out_valid && busy)inverses[recip_tag]<=reciprocal;
 end
 always @* output_data=output_inverse?inv_q:acc_q;
 integer i;
 always @(posedge clk) begin
  if(reset) begin
   busy<=0;done<=0;unsupported<=0;cycles<=0;drain<=0;read_valid<=0;
   outstanding<=0;phase<=0;row<=0;vertex<=0;n<=0;job_dest<=0;job_aspect<=0;
   read_tag<=0;read_phase<=0;transform_done<=0;final_w<=0;
   w_written<=0;recip_index<=0;recip_completed<=0;recip_state<=0;recip_tag<=0;
   for(i=0;i<7;i=i+1)tags[i]<=0;
  end else begin
   done<=0;
   read_valid<=issuing;
   read_phase<=phase;read_tag<=tag;
   tags[0]<=read_tag;
   for(i=1;i<7;i=i+1)tags[i]<=tags[i-1];
   final_w<={final_w[5:0],read_valid && read_phase==3 && read_tag[6:5]==3};
   case({read_valid,result_valid && busy})
    2'b10:outstanding<=outstanding+1'b1;
    2'b01:outstanding<=outstanding-1'b1;
    default:outstanding<=outstanding;
   endcase
   if(busy) begin
    cycles<=cycles+1'b1;
    if(result_valid && result_unsupported)unsupported<=1;
    if(result_valid && final_w[6])w_written<=w_written+1'b1;
    if(recip_state==0 && recip_index<w_written)recip_state<=1;
    if(recip_state==1 && recip_in_ready)begin
     recip_tag<=global_recip;recip_index<=recip_index+1'b1;recip_state<=2;
    end
    if(recip_out_valid)begin
     recip_completed<=recip_completed+1'b1;recip_state<=0;
     if(recip_unsupported)unsupported<=1;
    end
    if(issuing) begin
     if({1'b0,vertex}+1'b1==n)begin
      vertex<=0;
      if((phase==3 && row==2) || (phase!=3 && row==3) || phase==4)begin row<=0;drain<=1;end
      else row<=row+1'b1;
     end else vertex<=vertex+1'b1;
    end
    if(drain && outstanding==0 && !read_valid && !result_valid && !transform_done)begin
     if(phase==4)transform_done<=1;
     else begin phase<=phase+1'b1;row<=phase==2?2'd3:2'd0;drain<=0;end
    end
    if(transform_done && recip_completed==n)begin busy<=0;done<=1;end
   end else if(start)begin
    if(count==0 || count>32 || {1'b0,dest}+count>32)begin done<=1;unsupported<=1;end
    else begin
     busy<=1;done<=0;unsupported<=0;cycles<=0;phase<=0;row<=0;vertex<=0;
     n<=count;job_dest<=dest;job_aspect<=aspect;drain<=0;outstanding<=0;transform_done<=0;
     w_written<=0;recip_index<=0;recip_completed<=0;recip_state<=0;
    end
   end
  end
 end
endmodule
