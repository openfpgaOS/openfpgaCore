// Private unified transform/light scheduler; unchanged LOD5 arithmetic/ABI.
module geometry_transform_light #(parameter CAPTURE_CONTEXT=1,parameter EXTERNAL_FMA=0,
 parameter EXTERNAL_ATTRIBUTES=0,parameter CONTEXT_BRAM=1) (
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

 // One scheduler owns all FMA requests: phases0..4 transform,5..8 dot/scale,
 //9..11 RGB. Dependent phases drain; RGB phases stream using channel tags.
 // Reciprocal operates independently after the W-first transform row completes.
 (* ramstyle="M10K, no_rw_check" *) reg [31:0] positions[0:95];
 (* ramstyle="M10K, no_rw_check" *) reg [31:0] acc[0:127];
 (* ramstyle="MLAB, no_rw_check" *) reg [31:0] w_queue[0:31];
 reg [3:0] phase,read_phase;
 reg [1:0] row;
 reg [4:0] vertex,job_dest;
 reg [5:0] n;
 reg [31:0] job_aspect,held_cx,held_cy,held_cz;
 reg [23:0] held_ambient,held_diffuse;
 reg light_enabled,drain,arithmetic_done,read_valid,encoded_valid;
 reg [6:0] read_tag,tags[0:7];
 reg [4:0] outstanding;
 reg [6:0] fixed_tags[0:3];
 reg [3:0] fixed_outstanding;
 reg [31:0] pos_q,acc_q;
 wire [31:0] coef_q,inv_q;
 reg output_inverse;
 reg [31:0] encoded_a,encoded_b,encoded_c;
 reg encoded_multiply;
 reg [31:0] t_cycles,l_cycles;
 assign transform_cycles=t_cycles;
 assign lighting_cycles=l_cycles;
 wire [31:0] cx=CAPTURE_CONTEXT?held_cx:coefficient_x;
 wire [31:0] cy=CAPTURE_CONTEXT?held_cy:coefficient_y;
 wire [31:0] cz=CAPTURE_CONTEXT?held_cz:coefficient_z;
 wire [23:0] amb=CAPTURE_CONTEXT?held_ambient:ambient_rgb;
 wire [23:0] dif=CAPTURE_CONTEXT?held_diffuse:diffuse_rgb;
 wire accept=start&&!busy&&count!=0&&count<=32&&{1'b0,dest}+count<=32;
 wire issuing=busy&&!drain&&!arithmetic_done;
 wire transforming=phase<5;
 wire [1:0] axis=phase==0?2'd1:phase==3?2'd2:2'd0;
 wire [6:0] pos_index={1'b0,vertex,1'b0}+{2'b0,vertex}+{5'b0,axis};
 wire [1:0] coefficient_column=phase==0?2'd1:phase==1?2'd0:phase==2?2'd3:2'd2;
 wire [3:0] coefficient_index={coefficient_column,row};
 wire [1:0] channel=phase-4'd9;
 wire [6:0] issue_tag={transforming?row:channel,vertex};
 wire [6:0] returned_tag=tags[7];
 wire [4:0] returned_global=job_dest+returned_tag[4:0];
 wire [6:0] returned_acc={returned_tag[6:5],returned_global};
 wire result_valid,result_unsupported;
 wire [31:0] result;
 assign fma_request_valid=encoded_valid&&busy;
 assign fma_request_a=encoded_a;
 assign fma_request_b=encoded_b;
 assign fma_request_c=encoded_c;
 assign fma_request_multiply=encoded_multiply;
 generate if(EXTERNAL_FMA)begin:g_external
  assign result=fma_response_data;
  assign result_valid=fma_response_valid;
  assign result_unsupported=fma_response_unsupported;
 end else begin:g_internal
  GeometryFmaProbe shared_fma(.clk(clk),.reset(reset),.io_valid(fma_request_valid),
   .io_a(encoded_a),.io_b(encoded_b),.io_c(encoded_c),.io_multiplyOnly(encoded_multiply),
   .io_result(result),.io_resultValid(result_valid),.io_unsupported(result_unsupported));
 end endgenerate
 wire light_scratch_write=busy&&result_valid&&phase>=5&&phase<=8;
 wire [31:0] light_scratch_data=phase==8&&result[31]?32'd0:result;
 wire external_scratch_read=!busy&&!start&&scratch_read_valid;
 wire external_scratch_write=!busy&&!start&&scratch_write;
 wire position_writing=light_scratch_write||external_scratch_write||(!busy&&position_we);
 wire [6:0] position_write_addr=light_scratch_write?{2'b0,returned_tag[4:0]}:
   external_scratch_write?scratch_write_addr:position_addr;
 wire [31:0] position_write_data=light_scratch_write?light_scratch_data:
   external_scratch_write?scratch_write_data:position_data;
 wire [6:0] position_read_addr=external_scratch_read?scratch_read_addr:
   transforming?pos_index:{2'b0,vertex};
 assign scratch_read_data=pos_q;
 assign output_data=output_inverse?inv_q:acc_q;
 wire fixed_valid=busy&&result_valid&&phase>=9;
 wire fixed_result_valid,fixed_unsupported;
 wire [31:0] fixed_result;
 geometry_color_rtz fixed_converter(.clk(clk),.reset(reset),.in_valid(fixed_valid),
  .value(result),.out_valid(fixed_result_valid),.fixed_value(fixed_result),.unsupported(fixed_unsupported));
 wire rgb_write=busy&&fixed_result_valid;
 wire [4:0] rgb_write_vertex=fixed_tags[3][4:0];
 wire [1:0] rgb_write_channel=fixed_tags[3][6:5];
 wire [7:0] rgb_write_data=fixed_result[31]?8'd0:(|fixed_result[30:8])?8'd255:fixed_result[7:0];
 assign attribute_read_valid=issuing&&!transforming;
 assign attribute_read_addr=job_dest+vertex;
 assign attribute_rgb_write=rgb_write;
 assign attribute_rgb_addr=job_dest+rgb_write_vertex;
 assign attribute_rgb_channel=rgb_write_channel;
 assign attribute_rgb_data=rgb_write_data;
 wire [23:0] normal_q;
 wire initialize_rgb=!busy&&normal_we;
 wire [4:0] rgb_address=initialize_rgb?normal_addr:job_dest+rgb_write_vertex;
 generate if(EXTERNAL_ATTRIBUTES)begin:g_external_attributes
  assign normal_q=attribute_read_data;
  assign output_rgb=attribute_read_data;
 end else begin:g_internal_attributes
  (* ramstyle="MLAB, no_rw_check" *) reg [23:0] normals[0:31];
  (* ramstyle="MLAB, no_rw_check" *) reg [7:0] red[0:31],green[0:31],blue[0:31];
  reg [23:0] normal_data_q,rgb_q;
  assign normal_q=normal_data_q;assign output_rgb=rgb_q;
  always @(posedge clk)begin
   if(initialize_rgb)normals[normal_addr]<=normal_data;
   if(attribute_read_valid)normal_data_q<=normals[attribute_read_addr];
   if(initialize_rgb||(rgb_write&&rgb_write_channel==0))red[rgb_address]<=initialize_rgb?normal_data[7:0]:rgb_write_data;
   if(initialize_rgb||(rgb_write&&rgb_write_channel==1))green[rgb_address]<=initialize_rgb?normal_data[15:8]:rgb_write_data;
   if(initialize_rgb||(rgb_write&&rgb_write_channel==2))blue[rgb_address]<=initialize_rgb?normal_data[23:16]:rgb_write_data;
   rgb_q<={blue[output_addr[4:0]],green[output_addr[4:0]],red[output_addr[4:0]]};
  end
 end endgenerate
 function [31:0] unsigned_byte_float;
  input [7:0] v;
  begin
   casez(v)
    8'b1???????:unsigned_byte_float={1'b0,8'd134,v[6:0],16'd0};
    8'b01??????:unsigned_byte_float={1'b0,8'd133,v[5:0],17'd0};
    8'b001?????:unsigned_byte_float={1'b0,8'd132,v[4:0],18'd0};
    8'b0001????:unsigned_byte_float={1'b0,8'd131,v[3:0],19'd0};
    8'b00001???:unsigned_byte_float={1'b0,8'd130,v[2:0],20'd0};
    8'b000001??:unsigned_byte_float={1'b0,8'd129,v[1:0],21'd0};
    8'b0000001?:unsigned_byte_float={1'b0,8'd128,v[0],22'd0};
    8'b00000001:unsigned_byte_float=32'h3f800000;
    default:unsigned_byte_float=0;
   endcase
  end
 endfunction
 reg [7:0] amb_byte,dif_byte,selected_byte;
 wire selected_sign=read_phase>=5&&read_phase<=7&&selected_byte[7];
 wire [7:0] selected_magnitude=selected_sign?-selected_byte:selected_byte;
 wire [31:0] magnitude_float=unsigned_byte_float(selected_magnitude);
 wire [31:0] selected_float={selected_sign,magnitude_float[30:0]};
 reg [31:0] decoded_a,decoded_b,decoded_c;
 reg decoded_multiply;
 always @*begin
  case(read_phase)
   9:begin amb_byte=amb[7:0];dif_byte=dif[7:0];end
   10:begin amb_byte=amb[15:8];dif_byte=dif[15:8];end
   default:begin amb_byte=amb[23:16];dif_byte=dif[23:16];end
  endcase
  case(read_phase)
   5:selected_byte=normal_q[15:8];
   6:selected_byte=normal_q[7:0];
   7:selected_byte=normal_q[23:16];
   default:selected_byte=dif_byte;
  endcase
  decoded_a=0;decoded_b=0;decoded_c=0;decoded_multiply=0;
  case(read_phase)
   0:begin decoded_a=pos_q;decoded_b=coef_q;decoded_multiply=1;end
   1,3:begin decoded_a=pos_q;decoded_b=coef_q;decoded_c=acc_q;end
   2:begin decoded_a=coef_q;decoded_b=32'h3f800000;decoded_c=acc_q;end
   4:begin decoded_a=acc_q;decoded_b=job_aspect;decoded_multiply=1;end
   5:begin decoded_a=selected_float;decoded_b=cy;decoded_multiply=1;end
   6:begin decoded_a=cx;decoded_b=selected_float;decoded_c=pos_q;end
   7:begin decoded_a=cz;decoded_b=selected_float;decoded_c=pos_q;end
   8:begin decoded_a=pos_q;decoded_b=32'h3c010204;decoded_multiply=1;end
   default:begin decoded_a=selected_float;decoded_b=pos_q;decoded_c=unsigned_byte_float(amb_byte);end
  endcase
 end
 reg [5:0] w_written,recip_index,recip_completed;
 reg [1:0] recip_state;
 reg [31:0] w_q;
 reg [4:0] recip_tag;
 wire [4:0] global_recip=job_dest+recip_index[4:0];
 wire recip_in_ready,recip_out_valid,recip_unsupported;
 wire [31:0] reciprocal;
 geometry_float_recip2 recip(.clk(clk),.reset(reset),
  .in_valid(busy&&recip_state==1),.in_ready(recip_in_ready),.denominator(w_q),
  .out_valid(recip_out_valid),.out_ready(1'b1),.reciprocal(reciprocal),.unsupported(recip_unsupported));
 wire returning_w=busy&&result_valid&&phase==3&&returned_tag[6:5]==3;
 // Matrix is read only during transform issue; inverse results are visible
 // only after whole-job done. Matrix writes are idle-only, inverse writes busy.
 // One SDP M10K can therefore serve both with unchanged one-cycle read latency.
 generate if(CONTEXT_BRAM)begin:g_context_bram
  (* ramstyle="M10K, no_rw_check" *) reg [31:0] context_memory[0:63];
  reg [31:0] context_q;
  wire inverse_write=busy&&recip_out_valid;
  wire context_write=inverse_write||(!busy&&matrix_we);
  wire [5:0] write_addr=inverse_write?{1'b1,recip_tag}:{2'b0,matrix_addr};
  wire [31:0] write_data=inverse_write?reciprocal:matrix_data;
  wire [5:0] read_addr=issuing&&transforming?{2'b0,coefficient_index}:{1'b1,output_addr[4:0]};
  assign coef_q=context_q;assign inv_q=context_q;
  always @(posedge clk)begin
   if(context_write)context_memory[write_addr]<=write_data;
   context_q<=context_memory[read_addr];
  end
 end else begin:g_context_mlab
  (* ramstyle="MLAB, no_rw_check" *) reg [31:0] matrix[0:15];
  (* ramstyle="MLAB, no_rw_check" *) reg [31:0] inverses[0:31];
  reg [31:0] coef_data,inv_data;
  assign coef_q=coef_data;assign inv_q=inv_data;
  always @(posedge clk)begin
   if(!busy&&matrix_we)matrix[matrix_addr]<=matrix_data;
   if(issuing&&transforming)coef_data<=matrix[coefficient_index];
   if(busy&&recip_out_valid)inverses[recip_tag]<=reciprocal;
   inv_data<=inverses[output_addr[4:0]];
  end
 end endgenerate

 always @(posedge clk)begin
  if(position_writing&&position_write_addr<96)positions[position_write_addr]<=position_write_data;
  if(issuing||external_scratch_read)pos_q<=positions[position_read_addr];
  acc_q<=acc[busy?{row,job_dest+vertex}:output_addr[6:0]];
  output_inverse<=output_addr[7];
  if(result_valid&&busy&&transforming)acc[returned_acc]<=result;
  if(returning_w)w_queue[returned_global]<=result;
  if(busy&&recip_state==0&&recip_index<w_written)w_q<=w_queue[global_recip];
 end
 integer i;
 always @(posedge clk)begin
  if(reset)begin
   busy<=0;done<=0;unsupported<=0;cycles<=0;t_cycles<=0;l_cycles<=0;
   phase<=0;read_phase<=0;row<=0;vertex<=0;n<=0;job_dest<=0;job_aspect<=0;
   drain<=0;arithmetic_done<=0;read_valid<=0;encoded_valid<=0;read_tag<=0;
   light_enabled<=0;outstanding<=0;fixed_outstanding<=0;
   held_cx<=0;held_cy<=0;held_cz<=0;held_ambient<=0;held_diffuse<=0;
   w_written<=0;recip_index<=0;recip_completed<=0;recip_state<=0;recip_tag<=0;
   for(i=0;i<8;i=i+1)tags[i]<=0;
   for(i=0;i<4;i=i+1)fixed_tags[i]<=0;
  end else begin
   done<=0;read_valid<=issuing;read_phase<=phase;read_tag<=issue_tag;
   encoded_valid<=read_valid&&busy;
   encoded_a<=decoded_a;encoded_b<=decoded_b;encoded_c<=decoded_c;encoded_multiply<=decoded_multiply;
   tags[0]<=read_tag;
   for(i=1;i<8;i=i+1)tags[i]<=tags[i-1];
   fixed_tags[0]<=returned_tag;
   for(i=1;i<4;i=i+1)fixed_tags[i]<=fixed_tags[i-1];
   case({fma_request_valid,result_valid&&busy})
    2'b10:outstanding<=outstanding+1'b1;
    2'b01:outstanding<=outstanding-1'b1;
    default:outstanding<=outstanding;
   endcase
   case({fixed_valid,fixed_result_valid&&busy})
    2'b10:fixed_outstanding<=fixed_outstanding+1'b1;
    2'b01:fixed_outstanding<=fixed_outstanding-1'b1;
    default:fixed_outstanding<=fixed_outstanding;
   endcase
   if(busy)begin
    cycles<=cycles+1'b1;
    if(transforming||recip_completed!=n)t_cycles<=t_cycles+1'b1;
    if(!transforming&&!arithmetic_done)l_cycles<=l_cycles+1'b1;
    if(result_valid&&result_unsupported)unsupported<=1;
    if(fixed_result_valid&&fixed_unsupported)unsupported<=1;
    if(returning_w)w_written<=w_written+1'b1;
    if(recip_state==0&&recip_index<w_written)recip_state<=1;
    if(recip_state==1&&recip_in_ready)begin
     recip_tag<=global_recip;recip_index<=recip_index+1'b1;recip_state<=2;
    end
    if(recip_out_valid)begin
     recip_completed<=recip_completed+1'b1;recip_state<=0;
     if(recip_unsupported)unsupported<=1;
    end
    if(issuing)begin
     if({1'b0,vertex}+1'b1==n)begin
      vertex<=0;
      if(transforming)begin
       if((phase==3&&row==2)||(phase!=3&&row==3)||phase==4)begin row<=0;drain<=1;end
       else row<=row+1'b1;
      end else if(phase==9||phase==10)phase<=phase+1'b1;
      else drain<=1;
     end else vertex<=vertex+1'b1;
    end
    if(drain&&outstanding==0&&!read_valid&&!fma_request_valid&&!result_valid&&
       fixed_outstanding==0&&!fixed_result_valid&&!arithmetic_done)begin
     if(phase==11||(phase==4&&!light_enabled))arithmetic_done<=1;
     else begin phase<=phase+1'b1;row<=phase==2?2'd3:2'd0;drain<=0;end
    end
    if(arithmetic_done&&recip_completed==n)begin busy<=0;done<=1;end
   end else if(start)begin
    if(!accept)begin done<=1;unsupported<=1;end
    else begin
     busy<=1;unsupported<=0;cycles<=0;t_cycles<=0;l_cycles<=0;
     phase<=0;row<=0;vertex<=0;n<=count;job_dest<=dest;job_aspect<=aspect;
     drain<=0;arithmetic_done<=0;outstanding<=0;fixed_outstanding<=0;light_enabled<=lighting_enabled;
     if(CAPTURE_CONTEXT)begin held_cx<=coefficient_x;held_cy<=coefficient_y;held_cz<=coefficient_z;
      held_ambient<=ambient_rgb;held_diffuse<=diffuse_rgb;end
     w_written<=0;recip_index<=0;recip_completed<=0;recip_state<=0;
    end
   end
  end
 end
endmodule
