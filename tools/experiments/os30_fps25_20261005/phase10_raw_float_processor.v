// Private in-ring float cull/prepare subset. The CPU still performs native
// LOAD and checks the numerical/material contract before publishing commands.
// 60: six raw words, slot[17:13], clip[23:18] in header.
// 61: five material words (flags, prim, env, packed tile, linear flag).
// 62: one word (three 5-bit indices, two cull bits).
// Other commands stream unchanged. Raw triangles are atomically published
// using result_commit; abort retracts only their unpublished output. A violated
// guard halts with fault, never silently drops a triangle. Reset is required.
module geometry_raw_float_processor (
 input wire clk,reset,
 input wire cmd_valid,input wire [31:0] cmd_data,output wire cmd_ready,
 output wire result_valid,output wire [31:0] result_data,input wire result_ready,
 output wire result_commit,output wire result_abort,output reg fault,
 output wire processor_busy
);
 localparam HEADER=0,PASS=1,RAW=2,CONTEXT=3,TRI=4,
            CULL_START=6,CULL_WAIT=7,NEXT_SLOT=8,PACK_START=9,PACK_WAIT=10,
            DRAW_HEADER=11,DRAW_INDEX=12,COMMIT=13,ABORT=14,HALT=15;
 reg [3:0] state;
 assign processor_busy=state!=HEADER;
 reg [12:0] remaining;
 reg [2:0] load_word;
 reg [4:0] load_slot;
 reg load_good;
 reg [31:0] valid_slots,projected_stale;
 reg [8:0] flags;
 reg [31:0] prim,env,tile;
 reg linear_filter,context_valid,context_bad;
 reg [14:0] indices;
 reg [1:0] mode,corner;
 reg [2:0] dirty;
 wire private_header=cmd_data[31:24]==8'h60 || cmd_data[31:24]==8'h61 || cmd_data[31:24]==8'h62;
 wire legacy_header=state==HEADER && !private_header;
 wire forwarding=(state==HEADER && !private_header)||state==PASS;
 assign cmd_ready=(state==HEADER)?(private_header?1'b1:result_ready):
                  state==PASS?result_ready:(state==RAW||state==CONTEXT||state==TRI);
 wire take_cmd=cmd_valid&&cmd_ready;
 wire packet_valid,packet_busy,packet_done,packet_bad;
 wire [31:0] packet_data;
 wire packet_raw_read,packet_write,packet_read;
 wire [7:0] packet_raw_addr;
 wire [3:0] packet_write_addr,packet_read_addr;
 wire [31:0] packet_write_data;
 assign result_valid=forwarding?cmd_valid:state==PACK_WAIT?packet_valid:
                     state==DRAW_HEADER||state==DRAW_INDEX;
 assign result_data=forwarding?cmd_data:state==PACK_WAIT?packet_data:
                    state==DRAW_HEADER?32'h54000001:{17'b0,indices};
 assign result_commit=(forwarding&&cmd_valid&&result_ready)||state==COMMIT;
 assign result_abort=state==ABORT;
 wire [4:0] current_slot=corner==0?indices[4:0]:corner==1?indices[9:5]:indices[14:10];
 wire [31:0] projected_clear_mask=(32'b1<<indices[4:0])|
       (32'b1<<indices[9:5])|(32'b1<<indices[14:10]);
 wire cull_ready,cull_out,cull_bad;
 wire [1:0] reject_class;
 wire [5:0] clip_or;
 wire cull_vertex_read,cull_scratch_read,cull_scratch_write;
 wire [4:0] cull_vertex_slot;
 wire [1:0] cull_vertex_field;
 wire [2:0] cull_scratch_addr,cull_scratch_write_addr;
 wire [31:0] cull_scratch_write_data;
 wire fma_valid,fma_mul,fma_result_valid,fma_bad;
 wire [31:0] fma_a,fma_b,fma_c,fma_result;
 wire [31:0] raw_q,scratch_q,scratch_port_q;
 wire [2:0] raw_load_row=load_word<2?load_word:load_word+1'b1;
 wire [31:0] coordinate_magnitude={1'b0,cmd_data[30:0]};
 wire coordinate_good=coordinate_magnitude==0 ||
       (coordinate_magnitude>=32'h3b800000 && coordinate_magnitude<=32'h46fffdff);
 wire w_good=cmd_data>=32'h37800000 && cmd_data<=32'h46fffdff;
 wire inverse_good=cmd_data>=32'h38000000 && cmd_data<32'h48000000;
 wire raw_word_good=load_word<2?coordinate_good:load_word==2?w_good:load_word==3?inverse_good:1'b1;
 wire material_good=(cmd_data[15:8]<=6||cmd_data[15:8]==255) && (cmd_data&32'hffff00fe)==0;
 wire [31:0] packet_flags={flags[8:1],24'b0}|32'h00800100|(flags[0]?32'h00000200:32'b0);

 // One true-dual-port RAM (two M10Ks): raw rows0/1/3/4/5/6; unused row2 is
 // scratch. Cull may read raw and scratch together; its scratch reads and
 // writes never overlap. Formatter uses the same nine scratch words later.
 wire raw_read=cull_vertex_read||packet_raw_read;
 wire [7:0] raw_addr=cull_vertex_read?
       {cull_vertex_field==2?3'd4:{1'b0,cull_vertex_field},cull_vertex_slot}:packet_raw_addr;
 wire raw_write=state==RAW&&take_cmd;
 wire [7:0] raw_write_addr={raw_load_row,load_slot};
 wire [31:0] raw_write_data=cmd_data;
 wire scratch_read=cull_scratch_read||packet_read;
 wire [7:0] scratch_addr={3'd2,1'b0,(cull_scratch_read?{1'b0,cull_scratch_addr}:packet_read_addr)};
 wire scratch_write=cull_scratch_write||packet_write;
 wire [7:0] scratch_write_addr={3'd2,1'b0,(cull_scratch_write?{1'b0,cull_scratch_write_addr}:packet_write_addr)};
 wire [31:0] scratch_write_data=cull_scratch_write?cull_scratch_write_data:packet_write_data;
 // INTEGER_V writes the final UV word while prefetching packet word0.
 // Raw reads are idle then, so borrow portA rather than require a third port.
 wire scratch_borrow=scratch_read&&scratch_write;
 wire port_a_read=raw_read||scratch_borrow;
 wire [7:0] port_a_read_addr=scratch_borrow?scratch_addr:raw_addr;
 wire port_b_read=scratch_read&&!scratch_write;
 reg scratch_from_a;
 assign scratch_q=scratch_from_a?raw_q:scratch_port_q;
 always @(posedge clk)begin
  if(reset)scratch_from_a<=0;
  else if(scratch_read)scratch_from_a<=scratch_borrow;
 end
`ifdef VERILATOR
 reg [31:0] memory[0:255];
 reg [31:0] raw_model_q,scratch_model_q;
 assign raw_q=raw_model_q;assign scratch_port_q=scratch_model_q;
 always @(posedge clk)begin
  if(port_a_read)raw_model_q<=memory[port_a_read_addr];
  if(raw_write)memory[raw_write_addr]<=raw_write_data;
  if(port_b_read)scratch_model_q<=memory[scratch_addr];
  if(scratch_write)memory[scratch_write_addr]<=scratch_write_data;
 end
`else
 altsyncram #(.operation_mode("BIDIR_DUAL_PORT"),.intended_device_family("Cyclone V"),
  .ram_block_type("M10K"),.width_a(32),.widthad_a(8),.numwords_a(256),
  .width_b(32),.widthad_b(8),.numwords_b(256),
  .outdata_reg_a("UNREGISTERED"),.address_reg_b("CLOCK0"),
  .indata_reg_b("CLOCK0"),.wrcontrol_wraddress_reg_b("CLOCK0"),
  .outdata_reg_b("UNREGISTERED"),.read_during_write_mode_mixed_ports("DONT_CARE"),
  .power_up_uninitialized("TRUE")) raw_memory(
  .clock0(clk),.clock1(1'b1),.clocken0(1'b1),.clocken1(1'b1),.clocken2(1'b1),.clocken3(1'b1),
  .aclr0(1'b0),.aclr1(1'b0),.addressstall_a(1'b0),.addressstall_b(1'b0),
  .address_a(raw_write?raw_write_addr:port_a_read_addr),.data_a(raw_write_data),.wren_a(raw_write),
  .rden_a(port_a_read),.q_a(raw_q),.byteena_a(1'b1),
  .address_b(scratch_write?scratch_write_addr:scratch_addr),.data_b(scratch_write_data),.wren_b(scratch_write),
  .rden_b(port_b_read),.q_b(scratch_port_q),.byteena_b(1'b1));
`endif
 GeometryFmaProbe arithmetic(.clk(clk),.reset(reset),.io_valid(fma_valid),
  .io_a(fma_a),.io_b(fma_b),.io_c(fma_c),.io_multiplyOnly(fma_mul),
  .io_result(fma_result),.io_resultValid(fma_result_valid),.io_unsupported(fma_bad));
 geometry_float_cull_mem cull(.clk(clk),.reset(reset),
  .in_valid(state==CULL_START),.in_ready(cull_ready),.vertex_indices(indices),
  .clip_bits(18'b0),.negative_w(3'b0),.cull_mode(mode),
  .out_valid(cull_out),.out_ready(state==CULL_WAIT),.reject_class(reject_class),
  .clip_or(clip_or),.cross_bits(),.unsupported(cull_bad),
  .vertex_read_valid(cull_vertex_read),.vertex_read_index(cull_vertex_slot),
  .vertex_read_field(cull_vertex_field),.vertex_data(raw_q),
  .scratch_read_valid(cull_scratch_read),.scratch_read_index(cull_scratch_addr),.scratch_data(scratch_q),
  .scratch_write(cull_scratch_write),.scratch_write_index(cull_scratch_write_addr),.scratch_write_data(cull_scratch_write_data),
  .fma_valid(fma_valid),.fma_a(fma_a),.fma_b(fma_b),.fma_c(fma_c),.fma_multiply_only(fma_mul),
  .fma_result_valid(fma_result_valid),.fma_result(fma_result),.fma_unsupported(fma_bad));
 geometry_vertex_packet #(.CAPTURE_CONTEXT(0),.EXTERNAL_PACKET(1),.INTEGER_UV(1)) packet(
  .clk(clk),.reset(reset||state==ABORT),.start(state==PACK_START),.slot(current_slot),
  .draw_flags(packet_flags),.prim_rgba(prim),.env_rgba(env),.u_offset(32'b0),.v_offset(32'b0),.uv_bias(32'b0),
  .tile_uls(tile[15:0]),.tile_ult(tile[31:16]),.filter_linear(linear_filter),
  .raw_read_valid(packet_raw_read),.raw_read_addr(packet_raw_addr),.raw_read_data(raw_q),
  .fma_request_valid(),.fma_request_a(),.fma_request_b(),.fma_request_c(),.fma_request_multiply(),
  .fma_response_valid(1'b0),.fma_response_data(32'b0),.fma_response_unsupported(1'b0),
  .word_valid(packet_valid),.word_ready(state==PACK_WAIT&&result_ready),.word_data(packet_data),
  .busy(packet_busy),.done(packet_done),.unsupported(packet_bad),.cycles(),.surface_alpha(),
  .packet_write(packet_write),.packet_write_addr(packet_write_addr),.packet_write_data(packet_write_data),
  .packet_read_valid(packet_read),.packet_read_addr(packet_read_addr),.packet_read_data(scratch_q));
 always @(posedge clk)begin
  if(reset)begin
   state<=HEADER;remaining<=0;load_word<=0;load_slot<=0;load_good<=0;
   valid_slots<=0;projected_stale<=32'hffffffff;flags<=0;prim<=0;env<=0;tile<=0;
   linear_filter<=0;context_valid<=0;context_bad<=0;indices<=0;mode<=0;corner<=0;dirty<=0;fault<=0;
  end else begin
   case(state)
    HEADER:if(take_cmd)begin
     remaining<=cmd_data[12:0];load_word<=0;
     case(cmd_data[31:24])
      8'h60:if(cmd_data[12:0]==6)begin
       state<=RAW;load_slot<=cmd_data[17:13];
       load_good<=cmd_data[23:18]==0;valid_slots[cmd_data[17:13]]<=0;
       projected_stale[cmd_data[17:13]]<=1;
      end else state<=ABORT;
      8'h61:if(cmd_data[12:0]==5 && cmd_data[23:13]==0)begin state<=CONTEXT;context_valid<=0;projected_stale<=32'hffffffff;end else state<=ABORT;
      8'h62:if(cmd_data[12:0]==1 && cmd_data[23:13]==0)state<=TRI;else state<=ABORT;
      default:begin
       if(cmd_data[31:24]==8'h56||cmd_data[31:24]==8'h53||cmd_data[31:24]==8'h50)projected_stale<=32'hffffffff;
       if(cmd_data[12:0]!=0)state<=PASS;
      end
     endcase
    end
    PASS:if(take_cmd)begin remaining<=remaining-1'b1;if(remaining==1)state<=HEADER;end
    RAW:if(take_cmd)begin
     load_good<=load_good&&raw_word_good;
     if(load_word==5)begin valid_slots[load_slot]<=load_good&&raw_word_good;state<=HEADER;end
     else load_word<=load_word+1'b1;
    end
    CONTEXT:if(take_cmd)begin
     case(load_word)0:begin flags<={cmd_data[15:8],cmd_data[0]};context_bad<=!material_good;end
       1:prim<=cmd_data;2:env<=cmd_data;3:tile<=cmd_data;default:linear_filter<=cmd_data[0];endcase
     if(load_word==4)begin
      if(cmd_data[31:1]!=0 || context_bad)state<=ABORT;
      else begin context_valid<=1;state<=HEADER;end
     end else load_word<=load_word+1'b1;
    end
    TRI:if(take_cmd)begin
     indices<=cmd_data[14:0];mode<=cmd_data[16:15];corner<=0;
     dirty<={projected_stale[cmd_data[14:10]],projected_stale[cmd_data[9:5]],projected_stale[cmd_data[4:0]]};
     if(cmd_data[31:17]!=0 || !context_valid ||
        !valid_slots[cmd_data[4:0]] || !valid_slots[cmd_data[9:5]] || !valid_slots[cmd_data[14:10]])state<=ABORT;
     // valid_slots can be set only by a complete upload with clip==0.
     else state<=CULL_START;
    end
    CULL_START:if(cull_ready)state<=CULL_WAIT;
    CULL_WAIT:if(cull_out)begin
     if(cull_bad || clip_or!=0)state<=ABORT;
     else if(reject_class!=0)state<=COMMIT;
     else state<=NEXT_SLOT;
    end
    NEXT_SLOT:if(corner==3)state<=DRAW_HEADER;
              else if(dirty[corner])state<=PACK_START;
              else corner<=corner+1'b1;
    PACK_START:state<=PACK_WAIT;
    PACK_WAIT:if(packet_done)begin
     if(packet_bad)state<=ABORT;
     else begin corner<=corner+1'b1;state<=NEXT_SLOT;end
    end
    DRAW_HEADER:if(result_ready)state<=DRAW_INDEX;
    DRAW_INDEX:if(result_ready)begin
     projected_stale<=projected_stale&~projected_clear_mask;state<=COMMIT;
    end
    COMMIT:state<=HEADER;
    ABORT:begin fault<=1;state<=HALT;end
    HALT:;
    default:state<=ABORT;
   endcase
  end
 end
endmodule
