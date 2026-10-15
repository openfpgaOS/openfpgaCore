// Private end-to-end LOAD component: original 24-byte Vtx DMA, exact transform,
// reciprocal and one-light/unlit RGB; signed UV scaling and clip classification.
// Output row0..4=xyzw/reciprocal, row5={signedV,signedU}, row6=sourceA|RGB,
// row7=clip mask. Successful LOADs replace only dest..dest+count-1.
// Fog alpha, texture generation, RUN submission and
// integration with the CPU/GPU/fabric are explicitly outside this component.
module geometry_raw_load #(parameter EXTERNAL_FMA=0,parameter CAPTURE_CONTEXT=1,parameter ENABLE_INVALIDATE=0,parameter EXTERNAL_DMA=0,
 parameter [31:0] SOURCE_MIN_ADDRESS=32'h10000000,
 parameter [32:0] SOURCE_END_ADDRESS=33'h014000000) (
 input wire clk,reset,
 input wire slot_invalidate,input wire [31:0] invalidate_mask,
 input wire matrix_we,input wire [3:0] matrix_addr,input wire [31:0] matrix_data,
 input wire start,input wire [31:0] job_base,input wire [5:0] count,input wire [4:0] dest,
 input wire [31:0] aspect,coefficient_x,coefficient_y,coefficient_z,
 input wire [23:0] ambient_rgb,diffuse_rgb,input wire lighting_enabled,
 input wire [15:0] scale_u,scale_v,
 output wire m_arvalid,input wire m_arready,output wire [31:0] m_araddr,
 output wire [7:0] m_arlen,input wire m_rvalid,output wire m_rready,
 input wire [31:0] m_rdata,input wire [1:0] m_rresp,input wire m_rlast,
 // EXTERNAL_DMA: one parent-owned reader, held for this complete operation.
 output wire dma_request_start,output wire [31:0] dma_request_base,
 output wire [7:0] dma_request_words,
 input wire dma_response_busy,dma_response_done,input wire [1:0] dma_response_error,
 input wire dma_response_word_valid,input wire [7:0] dma_response_word_index,
 input wire [31:0] dma_response_word_data,
 input wire [7:0] output_addr,output reg [31:0] output_data,
 output wire busy,output reg done,unsupported,output reg [1:0] dma_error,
 output reg all_inside,output reg [31:0] cycles,
 output reg [31:0] valid_slots,
 output wire [31:0] compute_cycles,
 output wire fma_request_valid,output wire [31:0] fma_request_a,fma_request_b,fma_request_c,
 output wire fma_request_multiply,input wire fma_response_valid,
 input wire [31:0] fma_response_data,input wire fma_response_unsupported,
 input wire scratch_read_valid,input wire [6:0] scratch_read_addr,
 output wire [31:0] scratch_read_data,input wire scratch_write,
 input wire [6:0] scratch_write_addr,input wire [31:0] scratch_write_data
);
 localparam IDLE=3'd0,FETCH=3'd1,COMPUTE=3'd2,SCAN=3'd3,INVALID=3'd4;
 reg [2:0] state;
 assign busy=state!=IDLE;
 wire valid_range=count!=0 && count<=32 && {1'b0,dest}+count<=32;
 wire accept=start && !busy && valid_range;
 wire [31:0] incoming_mask=(32'hffffffff >> (32-count)) << dest;
 reg [5:0] n;
 reg [4:0] job_dest;
 reg [31:0] job_mask;
 reg [31:0] held_job_aspect,held_cx,held_cy,held_cz;
 reg [23:0] held_amb,held_dif;
 reg held_lit;
 reg [15:0] held_su,held_sv;
 // CAPTURE_CONTEXT=0: caller must hold all context inputs until done.
 wire [31:0] job_aspect=CAPTURE_CONTEXT?held_job_aspect:aspect;
 wire [31:0] cx=CAPTURE_CONTEXT?held_cx:coefficient_x;
 wire [31:0] cy=CAPTURE_CONTEXT?held_cy:coefficient_y;
 wire [31:0] cz=CAPTURE_CONTEXT?held_cz:coefficient_z;
 wire [23:0] amb=CAPTURE_CONTEXT?held_amb:ambient_rgb;
 wire [23:0] dif=CAPTURE_CONTEXT?held_dif:diffuse_rgb;
 wire lit=CAPTURE_CONTEXT?held_lit:lighting_enabled;
 wire [15:0] su=CAPTURE_CONTEXT?held_su:scale_u;
 wire [15:0] sv=CAPTURE_CONTEXT?held_sv:scale_v;
 wire fetch_busy,fetch_done;
 wire [1:0] fetch_error;
 wire word_valid;
 wire [7:0] word_index;
 wire [31:0] word_data;
 assign dma_request_start=accept;
 assign dma_request_base=job_base;
 assign dma_request_words=({2'b0,count}<<2)+({2'b0,count}<<1);
 generate if(EXTERNAL_DMA)begin:g_external_dma
  assign fetch_busy=dma_response_busy;assign fetch_done=dma_response_done;
  assign fetch_error=dma_response_error;assign word_valid=dma_response_word_valid;
  assign word_index=dma_response_word_index;assign word_data=dma_response_word_data;
  assign m_arvalid=1'b0;assign m_araddr=32'b0;assign m_arlen=8'b0;assign m_rready=1'b0;
 end else begin:g_internal_dma
 raw_vtx_dma #(.MIN_ADDRESS(SOURCE_MIN_ADDRESS),.END_ADDRESS(SOURCE_END_ADDRESS)) dma(.clk(clk),.reset(reset),.start(accept),.job_base(job_base),.job_count(count),.job_word_count(8'd0),
  .busy(fetch_busy),.done(fetch_done),.error(fetch_error),
  .m_arvalid(m_arvalid),.m_arready(m_arready),.m_araddr(m_araddr),.m_arlen(m_arlen),
  .m_rvalid(m_rvalid),.m_rready(m_rready),.m_rdata(m_rdata),.m_rresp(m_rresp),.m_rlast(m_rlast),
  .word_valid(word_valid),.word_ready(1'b1),.word_index(word_index),.word_data(word_data));
 end endgenerate
 reg [2:0] raw_word;
 reg [4:0] raw_vertex;
 wire [4:0] global_raw_vertex=job_dest+raw_vertex;
 reg [15:0] source_u,normal_xy;
 // Rows0/1/2 hold RGBA, UV and clip flags. Packed source normal/RGB bytes
 // become final RGB only after all three lighting normal passes have drained.
 // Byte writes preserve alpha and the other color channels without an RMW.
 wire [31:0] attribute_q;
 wire attribute_read_valid,attribute_rgb_write;
 wire [4:0] attribute_read_addr,attribute_rgb_addr;
 wire [1:0] attribute_rgb_channel;
 wire [7:0] attribute_rgb_data;
 wire [1:0] output_attribute_row=output_addr[7:5]==5?2'd1:output_addr[7:5]==7?2'd2:2'd0;
 wire [6:0] attribute_read_address=attribute_read_valid?{2'b0,attribute_read_addr}:{output_attribute_row,output_addr[4:0]};
 reg [6:0] attribute_write_address;
 reg [31:0] attribute_write_data;
 reg [3:0] attribute_write_bytes;
 reg [2:0] output_row;
 wire signed [32:0] u_product=$signed(source_u)*$signed({1'b0,su});
 wire signed [32:0] v_product=$signed(word_data[15:0])*$signed({1'b0,sv});
 wire calculate=state==FETCH && fetch_done && fetch_error==0;
 wire compute_busy,compute_done,compute_unsupported;
 wire [31:0] compute_data,compute_position_data;
 wire [23:0] compute_rgb;
 wire [31:0] unused_transform_cycles,unused_lighting_cycles;
 reg [1:0] scan_row,read_row;
 reg [4:0] scan_vertex,read_vertex;
 reg scan_draining,read_valid;
 wire scan_issue=state==SCAN && !scan_draining;
 wire [4:0] global_scan_vertex=job_dest+scan_vertex;
 wire [1:0] scan_coordinate=scan_row-2'd1;
 wire [7:0] compute_addr=state==SCAN?{1'b0,scan_coordinate,global_scan_vertex}:output_addr;
 geometry_transform_light #(.CAPTURE_CONTEXT(0),.EXTERNAL_FMA(EXTERNAL_FMA),.EXTERNAL_ATTRIBUTES(1)) compute(.clk(clk),.reset(reset),
  .matrix_we(matrix_we&&!busy),.matrix_addr(matrix_addr),.matrix_data(matrix_data),
  .position_we(word_valid && raw_word<3),
  .position_addr({1'b0,raw_vertex,1'b0}+{2'b0,raw_vertex}+{4'b0,raw_word}),.position_data(word_data),
  .normal_we(word_valid && raw_word==5),.normal_addr(global_raw_vertex),.normal_data({word_data[7:0],normal_xy}),
  .start(calculate),.count(n),.dest(job_dest),.aspect(job_aspect),
  .coefficient_x(cx),.coefficient_y(cy),.coefficient_z(cz),.ambient_rgb(amb),.diffuse_rgb(dif),.lighting_enabled(lit),
  .output_addr(compute_addr),.output_data(compute_data),.output_position_data(compute_position_data),.output_rgb(compute_rgb),
  .busy(compute_busy),.done(compute_done),.unsupported(compute_unsupported),.cycles(compute_cycles),
  .transform_cycles(unused_transform_cycles),.lighting_cycles(unused_lighting_cycles),
  .fma_request_valid(fma_request_valid),.fma_request_a(fma_request_a),.fma_request_b(fma_request_b),
  .fma_request_c(fma_request_c),.fma_request_multiply(fma_request_multiply),
  .fma_response_valid(fma_response_valid),.fma_response_data(fma_response_data),.fma_response_unsupported(fma_response_unsupported),
  .scratch_read_valid(scratch_read_valid&&!busy),.scratch_read_addr(scratch_read_addr),.scratch_read_data(scratch_read_data),
  .scratch_write(scratch_write&&!busy),.scratch_write_addr(scratch_write_addr),.scratch_write_data(scratch_write_data),
  .attribute_read_valid(attribute_read_valid),.attribute_read_addr(attribute_read_addr),.attribute_read_data(attribute_q[23:0]),
  .attribute_rgb_write(attribute_rgb_write),.attribute_rgb_addr(attribute_rgb_addr),
  .attribute_rgb_channel(attribute_rgb_channel),.attribute_rgb_data(attribute_rgb_data));
 function float_lt;
  input [31:0] a,b;
  begin
   if(a[30:0]==0 && b[30:0]==0)float_lt=0;
   else if(a[31]!=b[31])float_lt=a[31];
   else float_lt=a[31]?(a[30:0]>b[30:0]):(a[30:0]<b[30:0]);
  end
 endfunction
 // W first, then X/Y/Z: one shared comparison pair and one held float.
 // Every vertex still needs four synchronous reads; no extra scan cycles.
 reg [31:0] clip_w;
 reg [3:0] clip_xy;
 wire [31:0] neg_w={~clip_w[31],clip_w[30:0]};
 wire [1:0] clip_pair={float_lt(compute_position_data,neg_w),float_lt(clip_w,compute_position_data)};
 wire [5:0] classification={clip_xy,clip_pair};
 always @*begin
  case(output_row)
   5,6,7:output_data=attribute_q;
   default:output_data=compute_data;
  endcase
 end
 always @*begin
  attribute_write_address=0;attribute_write_data=0;attribute_write_bytes=0;
  if(word_valid && raw_word==4)begin
   attribute_write_address={2'd1,global_raw_vertex};
   attribute_write_data={v_product[31:16],u_product[31:16]};attribute_write_bytes=4'b1111;
  end else if(word_valid && raw_word==5)begin
   attribute_write_address={2'd0,global_raw_vertex};
   attribute_write_data={word_data[15:8],word_data[7:0],normal_xy};attribute_write_bytes=4'b1111;
  end else if(attribute_rgb_write)begin
   attribute_write_address={2'd0,attribute_rgb_addr};
   attribute_write_data={4{attribute_rgb_data}};attribute_write_bytes=4'b0001<<attribute_rgb_channel;
  end else if(state==SCAN && read_valid && read_row==3)begin
   attribute_write_address={2'd2,job_dest+read_vertex};
   attribute_write_data={26'b0,classification};attribute_write_bytes=4'b1111;
  end
 end
`ifdef VERILATOR
 // Same one-cycle synchronous read and byte enables as the primitive below.
 // Same-address read/write is unspecified in hardware; normal operands have
 // already drained before color writes, and LOAD output is invalid until done.
 reg [31:0] attributes[0:127];
 reg [31:0] attribute_model_q;
 assign attribute_q=attribute_model_q;
 integer byte_index;
 always @(posedge clk)begin
  attribute_model_q<=attributes[attribute_read_address];
  for(byte_index=0;byte_index<4;byte_index=byte_index+1)
   if(attribute_write_bytes[byte_index])
    attributes[attribute_write_address][8*byte_index+:8]<=attribute_write_data[8*byte_index+:8];
 end
`else
 // Explicit byte-enable instance prevents Quartus from splitting the four
 // byte slices into four small M10Ks. One 128x32 M10K holds all attributes.
 altsyncram #(.operation_mode("DUAL_PORT"),.intended_device_family("Cyclone V"),
  .ram_block_type("M10K"),.width_a(32),.widthad_a(7),.numwords_a(128),
  .width_b(32),.widthad_b(7),.numwords_b(128),.byte_size(8),.width_byteena_a(4),
  .address_reg_b("CLOCK0"),.rdcontrol_reg_b("CLOCK0"),.outdata_reg_b("UNREGISTERED"),
  .read_during_write_mode_mixed_ports("DONT_CARE"),.power_up_uninitialized("TRUE")) attribute_memory(
  .clock0(clk),.clocken0(1'b1),.clocken1(1'b1),.clocken2(1'b1),.clocken3(1'b1),
  .aclr0(1'b0),.aclr1(1'b0),.address_a(attribute_write_address),
  .data_a(attribute_write_data),.wren_a(|attribute_write_bytes),.byteena_a(attribute_write_bytes),
  .address_b(attribute_read_address),.rden_b(1'b1),.q_b(attribute_q),
  .addressstall_a(1'b0),.addressstall_b(1'b0),.wren_b(1'b0),.rden_a(1'b1),
  .data_b(32'b0),.byteena_b(1'b1),.clock1(1'b1));
`endif
 always @(posedge clk)begin
  output_row<=output_addr[7:5];
  if(word_valid)begin
   if(raw_word==3)source_u<=word_data[31:16];
   if(raw_word==4)normal_xy<=word_data[31:16];
  end
  if(reset)begin
   state<=IDLE;done<=0;unsupported<=0;dma_error<=0;cycles<=0;all_inside<=0;
   n<=0;job_dest<=0;job_mask<=0;valid_slots<=0;held_job_aspect<=0;held_cx<=0;held_cy<=0;held_cz<=0;held_amb<=0;held_dif<=0;held_lit<=0;held_su<=0;held_sv<=0;
   raw_word<=0;raw_vertex<=0;source_u<=0;normal_xy<=0;
   scan_row<=0;read_row<=0;scan_vertex<=0;read_vertex<=0;scan_draining<=0;read_valid<=0;clip_w<=0;clip_xy<=0;
  end else begin
   done<=0;read_valid<=scan_issue;read_row<=scan_row;read_vertex<=scan_vertex;
   if(busy)cycles<=cycles+1'b1;
   if(word_valid)begin
    if(raw_word==5)begin raw_word<=0;raw_vertex<=raw_vertex+1'b1;end
    else raw_word<=raw_word+1'b1;
   end
   if(ENABLE_INVALIDATE && state==IDLE && slot_invalidate && !start)
    valid_slots<=valid_slots&~invalidate_mask;
   case(state)
    IDLE:if(start)begin
     if(!valid_range)begin state<=INVALID;unsupported<=1;dma_error<=1;all_inside<=0;cycles<=0;end
     else begin
     state<=FETCH;done<=0;unsupported<=0;dma_error<=0;cycles<=0;all_inside<=1;
     n<=count;job_dest<=dest;job_mask<=incoming_mask;valid_slots<=valid_slots & ~incoming_mask;held_job_aspect<=aspect;held_cx<=coefficient_x;held_cy<=coefficient_y;held_cz<=coefficient_z;
     held_amb<=ambient_rgb;held_dif<=diffuse_rgb;held_lit<=lighting_enabled;held_su<=scale_u;held_sv<=scale_v;
     raw_word<=0;raw_vertex<=0;
     end
    end
    INVALID:begin state<=IDLE;done<=1;end
    FETCH:if(fetch_done)begin
     if(fetch_error!=0)begin dma_error<=fetch_error;unsupported<=1;all_inside<=0;done<=1;state<=IDLE;end
     else state<=COMPUTE;
    end
    COMPUTE:if(compute_done)begin
     if(compute_unsupported)begin unsupported<=1;all_inside<=0;done<=1;state<=IDLE;end
     else begin state<=SCAN;scan_row<=0;scan_vertex<=0;scan_draining<=0;end
    end
    SCAN:begin
     if(scan_issue)begin
      scan_row<=scan_row+1'b1;
      if(scan_row==3)begin
       if({1'b0,scan_vertex}+1'b1==n)scan_draining<=1;
       else scan_vertex<=scan_vertex+1'b1;
      end
     end
     if(read_valid)begin
      case(read_row)
       0:clip_w<=compute_position_data;
       1:clip_xy[3:2]<=clip_pair;
       2:clip_xy[1:0]<=clip_pair;
       3:begin
        if(classification!=0)all_inside<=0;
        if({1'b0,read_vertex}+1'b1==n)begin state<=IDLE;done<=1;valid_slots<=valid_slots|job_mask;end
       end
      endcase
     end
    end
   endcase
  end
 end
`ifdef GEOMETRY_SCAN_CHECKS
 always @(posedge clk) if(!reset && state==SCAN && read_valid) begin
  assert(!compute_busy);
  assert(compute_position_data==compute_data);
 end
`endif
endmodule
