// Private arithmetic integration. One shared FMA transforms a batch, then
// lights it while the independent reciprocal engine finishes. No CPU copies,
// DMA, UV/fog, clipping, triangle submission, or frame-rate gain is implied.
// CAPTURE_CONTEXT=0 requires coefficients/colors to remain stable until done;
// geometry_raw_load already owns that snapshot, avoiding duplicate registers.
// EXTERNAL_FMA=1 exports the shared arithmetic port. Responses must correspond
// to this client's requests, with the primitive's fixed six-cycle latency.
// External scratch accesses are accepted only when !busy && !start; they must
// finish before a new LOAD overwrites the local XYZ/scratch storage.
module geometry_transform_light #(parameter CAPTURE_CONTEXT=1,parameter EXTERNAL_FMA=0,
 parameter EXTERNAL_ATTRIBUTES=0) (
 input wire clk,reset,
 input wire matrix_we,input wire [3:0] matrix_addr,input wire [31:0] matrix_data,
 input wire position_we,input wire [6:0] position_addr,input wire [31:0] position_data,
 input wire normal_we,input wire [4:0] normal_addr,input wire [23:0] normal_data,
 input wire start,input wire [5:0] count,input wire [4:0] dest,input wire [31:0] aspect,
 input wire [31:0] coefficient_x,coefficient_y,coefficient_z,
 input wire [23:0] ambient_rgb,diffuse_rgb,input wire lighting_enabled,
 input wire [7:0] output_addr,output wire [31:0] output_data,output wire [23:0] output_rgb,
 output reg busy,done,unsupported,output reg [31:0] cycles,
 output wire [31:0] transform_cycles,lighting_cycles,
 output wire fma_request_valid,output wire [31:0] fma_request_a,
 output wire [31:0] fma_request_b,fma_request_c,output wire fma_request_multiply,
 input wire fma_response_valid,input wire [31:0] fma_response_data,
 input wire fma_response_unsupported,
 input wire scratch_read_valid,input wire [6:0] scratch_read_addr,
 output wire [31:0] scratch_read_data,
 input wire scratch_write,input wire [6:0] scratch_write_addr,
 input wire [31:0] scratch_write_data,
 // Optional shared attribute RAM: normal reads are one cycle, RGB writes use
 // global vertex addresses. All normals are consumed before the first RGB
 // write, so packed source normals and resulting RGB may share one word.
 output wire attribute_read_valid,output wire [4:0] attribute_read_addr,
 input wire [23:0] attribute_read_data,
 output wire attribute_rgb_write,output wire [4:0] attribute_rgb_addr,
 output wire [1:0] attribute_rgb_channel,output wire [7:0] attribute_rgb_data
);
 wire [23:0] normal_q;
 wire [31:0] scratch_q;
 reg light_started,light_complete,transform_complete;
 reg light_enabled;
 reg [5:0] n;
 reg [4:0] held_dest;
 reg [31:0] held_cx,held_cy,held_cz;
 reg [23:0] held_ambient,held_diffuse;
 wire [31:0] cx=CAPTURE_CONTEXT?held_cx:coefficient_x;
 wire [31:0] cy=CAPTURE_CONTEXT?held_cy:coefficient_y;
 wire [31:0] cz=CAPTURE_CONTEXT?held_cz:coefficient_z;
 wire [23:0] ambient=CAPTURE_CONTEXT?held_ambient:ambient_rgb;
 wire [23:0] diffuse=CAPTURE_CONTEXT?held_diffuse:diffuse_rgb;
 wire accept=start && !busy && count!=0 && count<=32 && {2'b0,dest}+{1'b0,count}<=7'd32;
 wire transform_finished,t_busy,t_done,t_unsupported;
 wire light_start=busy && light_enabled && transform_finished && !light_started;
 wire l_busy,l_done,l_unsupported;
 wire t_valid,t_mul,l_valid,l_mul;
 wire [31:0] t_a,t_b,t_c,l_a,l_b,l_c;
 wire arithmetic_valid,arithmetic_unsupported;
 wire [31:0] arithmetic_result;
 reg [6:0] light_owner;
 always @(posedge clk)begin
  if(reset)light_owner<=0;
  else light_owner<={light_owner[5:0],l_valid};
 end
 assign fma_request_valid=t_valid || l_valid;
 assign fma_request_a=l_valid?l_a:t_a;
 assign fma_request_b=l_valid?l_b:t_b;
 assign fma_request_c=l_valid?l_c:t_c;
 assign fma_request_multiply=l_valid?l_mul:t_mul;
 generate if(EXTERNAL_FMA)begin:g_external
  assign arithmetic_result=fma_response_data;
  assign arithmetic_valid=fma_response_valid;
  assign arithmetic_unsupported=fma_response_unsupported;
 end else begin:g_internal
 GeometryFmaProbe shared_fma(.clk(clk),.reset(reset),.io_valid(fma_request_valid),
  .io_a(fma_request_a),.io_b(fma_request_b),.io_c(fma_request_c),
  .io_multiplyOnly(fma_request_multiply),.io_result(arithmetic_result),
  .io_resultValid(arithmetic_valid),.io_unsupported(arithmetic_unsupported));
 end endgenerate
 geometry_float_batch_recip #(.EXTERNAL_FMA(1)) transform(
  .clk(clk),.reset(reset),.matrix_we(matrix_we&&!busy),.matrix_addr(matrix_addr),.matrix_data(matrix_data),
  .position_we(position_we&&!busy),.position_addr(position_addr),.position_data(position_data),
  .start(accept),.count(count),.dest(dest),.aspect(aspect),.output_addr(output_addr),.output_data(output_data),
  .busy(t_busy),.done(t_done),.unsupported(t_unsupported),.cycles(transform_cycles),
  .transform_finished(transform_finished),.fma_request_valid(t_valid),
  .fma_request_a(t_a),.fma_request_b(t_b),.fma_request_c(t_c),.fma_request_multiply(t_mul),
  .fma_response_valid(arithmetic_valid&&!light_owner[6]),.fma_response_data(arithmetic_result),
  .fma_response_unsupported(arithmetic_unsupported),
  .scratch_read_valid(busy?work_read_valid:external_scratch_read),
  .scratch_read_addr(busy?{2'b0,work_read_vertex}:scratch_read_addr),.scratch_read_data(scratch_q),
  .scratch_write(busy?light_scratch_write:external_scratch_write),
  .scratch_write_addr(busy?{2'b0,scratch_write_vertex}:scratch_write_addr),
  .scratch_write_data(busy?light_scratch_data:scratch_write_data));
 wire external_scratch_read=!busy && !start && scratch_read_valid;
 wire external_scratch_write=!busy && !start && scratch_write;
 assign scratch_read_data=scratch_q;
 wire work_read_valid,light_scratch_write,rgb_write;
 wire [4:0] work_read_vertex,scratch_write_vertex,rgb_write_vertex;
 wire [31:0] light_scratch_data;
 wire [1:0] rgb_write_channel;
 wire [7:0] rgb_write_data;
 assign attribute_read_valid=work_read_valid;
 assign attribute_read_addr=held_dest+work_read_vertex;
 assign attribute_rgb_write=rgb_write;
 assign attribute_rgb_addr=held_dest+rgb_write_vertex;
 assign attribute_rgb_channel=rgb_write_channel;
 assign attribute_rgb_data=rgb_write_data;
 wire initialize_rgb=!busy && normal_we;
 wire [4:0] rgb_address=initialize_rgb?normal_addr:held_dest+rgb_write_vertex;
 wire [7:0] red_data=initialize_rgb?normal_data[7:0]:rgb_write_data;
 wire [7:0] green_data=initialize_rgb?normal_data[15:8]:rgb_write_data;
 wire [7:0] blue_data=initialize_rgb?normal_data[23:16]:rgb_write_data;
 wire fixed_valid,fixed_result_valid,fixed_unsupported;
 wire [31:0] fixed_value,fixed_result;
 geometry_lighting_batch #(.CAPTURE_CONTEXT(0)) lighting(.clk(clk),.reset(reset),.start(light_start),.count(n),
  .coefficient_x(cx),.coefficient_y(cy),.coefficient_z(cz),.ambient_rgb(ambient),.diffuse_rgb(diffuse),
  .work_read_valid(work_read_valid),.work_read_vertex(work_read_vertex),
  .normal_data(normal_q),.scratch_data(scratch_q),
  .scratch_write(light_scratch_write),.scratch_write_vertex(scratch_write_vertex),.scratch_write_data(light_scratch_data),
  .rgb_write(rgb_write),.rgb_write_vertex(rgb_write_vertex),.rgb_write_channel(rgb_write_channel),.rgb_write_data(rgb_write_data),
  .fma_valid(l_valid),.fma_a(l_a),.fma_b(l_b),.fma_c(l_c),.fma_multiply_only(l_mul),
  .fma_result_valid(arithmetic_valid&&light_owner[6]),.fma_result(arithmetic_result),.fma_unsupported(arithmetic_unsupported),
  .fixed_valid(fixed_valid),.fixed_value(fixed_value),.fixed_result_valid(fixed_result_valid),
  .fixed_result(fixed_result),.fixed_unsupported(fixed_unsupported),
  .busy(l_busy),.done(l_done),.unsupported(l_unsupported),.cycles(lighting_cycles));
 geometry_color_rtz fixed_converter(.clk(clk),.reset(reset),.in_valid(fixed_valid),
  .value(fixed_value),
  .out_valid(fixed_result_valid),.fixed_value(fixed_result),.unsupported(fixed_unsupported));
 generate if(EXTERNAL_ATTRIBUTES)begin:g_external_attributes
  assign normal_q=attribute_read_data;
  assign output_rgb=attribute_read_data;
 end else begin:g_internal_attributes
  (* ramstyle="MLAB, no_rw_check" *) reg [23:0] normals[0:31];
  (* ramstyle="MLAB, no_rw_check" *) reg [7:0] red[0:31],green[0:31],blue[0:31];
  reg [23:0] normal_data_q,rgb_q;
  assign normal_q=normal_data_q;
  assign output_rgb=rgb_q;
  always @(posedge clk)begin
   if(!busy && normal_we)normals[normal_addr]<=normal_data;
   if(work_read_valid)normal_data_q<=normals[attribute_read_addr];
   if(initialize_rgb || (rgb_write && rgb_write_channel==0))red[rgb_address]<=red_data;
   if(initialize_rgb || (rgb_write && rgb_write_channel==1))green[rgb_address]<=green_data;
   if(initialize_rgb || (rgb_write && rgb_write_channel==2))blue[rgb_address]<=blue_data;
   rgb_q<={blue[output_addr[4:0]],green[output_addr[4:0]],red[output_addr[4:0]]};
  end
 end endgenerate
 always @(posedge clk)begin
  if(reset)begin
   busy<=0;done<=0;unsupported<=0;cycles<=0;
   light_started<=0;light_complete<=0;transform_complete<=0;
   n<=0;held_dest<=0;held_cx<=0;held_cy<=0;held_cz<=0;held_ambient<=0;held_diffuse<=0;light_enabled<=0;
  end else begin
   done<=0;
   if(busy)begin
    cycles<=cycles+1'b1;
    if(light_start)light_started<=1;
    if(t_done)begin transform_complete<=1;if(t_unsupported)unsupported<=1;end
    if(l_done)begin light_complete<=1;if(l_unsupported)unsupported<=1;end
    if(transform_complete && light_complete)begin busy<=0;done<=1;end
   end else if(start)begin
    if(!accept)begin done<=1;unsupported<=1;end
    else begin
     busy<=1;done<=0;unsupported<=0;cycles<=0;light_started<=0;light_complete<=!lighting_enabled;transform_complete<=0;light_enabled<=lighting_enabled;
     n<=count;held_dest<=dest;held_cx<=coefficient_x;held_cy<=coefficient_y;held_cz<=coefficient_z;held_ambient<=ambient_rgb;held_diffuse<=diffuse_rgb;
    end
   end
  end
 end
endmodule
