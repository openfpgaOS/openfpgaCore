// Private single-slot legacy packet formatter. Owns no FMA: the caller routes
// its requests through the existing seven-cycle native rounded FMA. Raw rows
// use a one-cycle synchronous read and remain owned until done. The full nine
// word packet is withheld until all arithmetic succeeds, then held stable
// under output backpressure. Start/configuration while busy are ignored.
module geometry_vertex_packet #(parameter CAPTURE_CONTEXT=1,parameter EXTERNAL_PACKET=0,parameter INTEGER_UV=0) (
 input wire clk,reset,start,input wire [4:0] slot,
 input wire [31:0] draw_flags,prim_rgba,env_rgba,u_offset,v_offset,uv_bias,
 // INTEGER_UV accepts the original uint16 tile origins and filter selector;
 // (signed16UV - tile*8)*2048 + (linear?32768:0) is exactly F11's float path.
 input wire [15:0] tile_uls,tile_ult,input wire filter_linear,
 output wire raw_read_valid,output wire [7:0] raw_read_addr,input wire [31:0] raw_read_data,
 output wire fma_request_valid,output wire [31:0] fma_request_a,fma_request_b,fma_request_c,
 output wire fma_request_multiply,input wire fma_response_valid,
 input wire [31:0] fma_response_data,input wire fma_response_unsupported,
 output wire word_valid,input wire word_ready,output reg [31:0] word_data,
 output reg busy,done,unsupported,output reg [31:0] cycles,output reg [7:0] surface_alpha,
 // Optional single-port staging storage, private indices0..8. Reads are one
 // cycle synchronous and retain data when read_valid is low. Parent owns
 // these words from start through done; partial/error contents never publish.
 output reg packet_write,output reg [3:0] packet_write_addr,output reg [31:0] packet_write_data,
 output wire packet_read_valid,output wire [3:0] packet_read_addr,input wire [31:0] packet_read_data
);
 localparam IDLE=0,FETCH=1,SUBMIT_SUB=2,WAIT_SUB=3,SUBMIT_SCALE=4,WAIT_SCALE=5,DRAIN=6,EMIT=7,INTEGER_U=8,INTEGER_V=9;
 reg [3:0] state;
 reg [4:0] job_slot;
 reg [31:0] held_flags,held_prim,held_env,held_uo,held_vo,held_bias,du,dv;
 wire [31:0] flags=CAPTURE_CONTEXT?held_flags:draw_flags;
 wire [31:0] prim=CAPTURE_CONTEXT?held_prim:prim_rgba;
 wire [31:0] env=CAPTURE_CONTEXT?held_env:env_rgba;
 wire [31:0] uo=CAPTURE_CONTEXT?held_uo:u_offset;
 wire [31:0] vo=CAPTURE_CONTEXT?held_vo:v_offset;
 wire [31:0] bias=CAPTURE_CONTEXT?held_bias:uv_bias;
 reg [15:0] packed_u,packed_v,rgb,held_uls,held_ult;
 reg held_filter;
 wire [15:0] uls=CAPTURE_CONTEXT?held_uls:tile_uls;
 wire [15:0] ult=CAPTURE_CONTEXT?held_ult:tile_ult;
 wire linear_filter=CAPTURE_CONTEXT?held_filter:filter_linear;
 wire signed [20:0] uv_diff=$signed({{5{(state==INTEGER_V?packed_v[15]:packed_u[15])}},(state==INTEGER_V?packed_v:packed_u)})
     -$signed({2'b0,(state==INTEGER_V?ult:uls),3'b0});
 wire [31:0] uv_diff_extended={{11{uv_diff[20]}},uv_diff};
 wire [31:0] integer_uv=(uv_diff_extended<<11)+(linear_filter?32'd32768:32'd0);
 reg [31:0] qx,qy,qw,qu,qv,qd;
 reg [2:0] fetch_row,read_row;
 reg read_valid,axis;
 reg [1:0] responses;
 reg [2:0] fixed_count;
 reg [3:0] emit_word;
 reg [6:0] fma_axis;
 reg [2:0] fixed_tag[0:3];
 wire textured=flags[9];
 wire valid_material=(draw_flags[15:8]!=0) && draw_flags[31:24]!=7 && draw_flags[23:20]!=7
     && !(draw_flags[23:20]==4 && !draw_flags[16]);
 assign raw_read_valid=state==FETCH;
 assign raw_read_addr={fetch_row,job_slot};
 function [31:0] signed_short_float;
  input [15:0] value;
  reg [15:0] mag;reg [4:0] shift;reg [7:0] exponent;reg [30:0] fraction;integer i;
  begin
   mag=value[15]?-value:value;shift=0;
   for(i=0;i<16;i=i+1)if(mag[i])shift=i;
   exponent=8'd127+shift;fraction={mag,15'b0}<<(15-shift);
   signed_short_float=mag==0?32'd0:{value[15],exponent,fraction[29:7]};
  end
 endfunction
 assign fma_request_valid=!INTEGER_UV && (state==SUBMIT_SUB || state==SUBMIT_SCALE);
 assign fma_request_a=INTEGER_UV?32'd0:state==SUBMIT_SUB ? signed_short_float(axis?packed_v:packed_u) : (axis?dv:du);
 assign fma_request_b=INTEGER_UV?32'd0:state==SUBMIT_SUB ? 32'h3f800000 : 32'h3d000000;
 assign fma_request_c=INTEGER_UV?32'd0:state==SUBMIT_SUB ? {(axis?vo[31]:uo[31])^1'b1,(axis?vo[30:0]:uo[30:0])} : bias;
 assign fma_request_multiply=1'b0;
 wire fetching_fixed=read_valid && (read_row==0 || read_row==1 || read_row==3 || read_row==4);
 wire uv_fixed=!INTEGER_UV && state==WAIT_SCALE && fma_response_valid;
 wire fixed_valid=fetching_fixed || uv_fixed;
 wire [31:0] fixed_value=uv_fixed?fma_response_data:raw_read_data;
 wire [5:0] fixed_scale=uv_fixed?6'd16:6'd8;
 wire fixed_depth=fetching_fixed && read_row==4;
 wire [2:0] next_fixed_tag=uv_fixed?(fma_axis[6]?3'd4:3'd3):
         read_row==0?3'd0:read_row==1?3'd1:read_row==3?3'd2:3'd5;
 wire converted_valid,converted_unsupported;
 wire [31:0] converted;
 geometry_float_fixed_pipe fixed_conversion(.clk(clk),.reset(reset),.in_valid(fixed_valid),
  .value(fixed_value),.scale(fixed_scale),.truncate(1'b0),.depth_mode(fixed_depth),
  .out_valid(converted_valid),.fixed_value(converted),.unsupported(converted_unsupported));
 wire [31:0] source_color=flags[31:24]==255?32'h00ffffff:
       flags[31:24]==4?raw_read_data:flags[31:24]==3?prim:flags[31:24]==5?env:32'd0;
 wire arithmetic_ready=state==DRAIN && fixed_count==((textured&&!INTEGER_UV)?6:4) && !unsupported;
 wire packet_ready=(arithmetic_ready && !(INTEGER_UV && textured)) || state==INTEGER_V;
 assign packet_read_valid=EXTERNAL_PACKET && (packet_ready || (state==EMIT && word_ready && emit_word<8));
 assign packet_read_addr=packet_ready?4'd0:emit_word+1'b1;
 assign word_valid=state==EMIT;
 always @*begin
  packet_write=0;packet_write_addr=0;packet_write_data=0;
  if(EXTERNAL_PACKET)begin
   if(state==IDLE && start && valid_material)begin
    packet_write=1;packet_write_addr=0;packet_write_data=32'h56000008;
   end else if(state==FETCH && fetch_row==0)begin
    packet_write=1;packet_write_addr=1;packet_write_data={27'b0,job_slot};
   end else if(state==FETCH && !textured && (fetch_row==1 || fetch_row==2))begin
    packet_write=1;packet_write_addr=fetch_row==1?4'd5:4'd6;packet_write_data=0;
   end else if(state==INTEGER_U || state==INTEGER_V)begin
    packet_write=1;packet_write_addr=state==INTEGER_U?4'd5:4'd6;packet_write_data=integer_uv;
   end else if(busy && converted_valid)begin
    packet_write=1;packet_write_data=converted;
    packet_write_addr=fixed_tag[3]==5?4'd8:{1'b0,fixed_tag[3]}+4'd2;
   end else if(read_valid && read_row==6)begin
    packet_write=1;packet_write_addr=7;packet_write_data={16'b0,source_color[7:3],source_color[15:10],source_color[23:19]};
   end
  end
 end
 always @*begin
  if(EXTERNAL_PACKET)word_data=packet_read_data;
  else case(emit_word)
   0:word_data=32'h56000008;
   1:word_data={27'b0,job_slot};
   2:word_data=qx;
   3:word_data=qy;
   4:word_data=qw;
   5:word_data=qu;
   6:word_data=qv;
   7:word_data={16'b0,rgb};
   default:word_data=qd;
  endcase
 end
 integer k;
 always @(posedge clk)begin
  if(reset)begin
   state<=IDLE;busy<=0;done<=0;unsupported<=0;cycles<=0;surface_alpha<=255;
   job_slot<=0;held_flags<=0;held_prim<=0;held_env<=0;held_uo<=0;held_vo<=0;held_bias<=0;du<=0;dv<=0;
   packed_u<=0;packed_v<=0;rgb<=0;held_uls<=0;held_ult<=0;held_filter<=0;qx<=0;qy<=0;qw<=0;qu<=0;qv<=0;qd<=0;
   fetch_row<=0;read_row<=0;read_valid<=0;axis<=0;responses<=0;fixed_count<=0;emit_word<=0;fma_axis<=0;
   for(k=0;k<4;k=k+1)fixed_tag[k]<=0;
  end else begin
   done<=0;read_valid<=raw_read_valid;read_row<=fetch_row;
   fma_axis<={fma_axis[5:0],axis};fixed_tag[0]<=next_fixed_tag;
   for(k=1;k<4;k=k+1)fixed_tag[k]<=fixed_tag[k-1];
   if(busy)cycles<=cycles+1'b1;
   if(busy && converted_valid)begin
    fixed_count<=fixed_count+1'b1;
    if(converted_unsupported)unsupported<=1;
    case(fixed_tag[3])
     0:qx<=converted;1:qy<=converted;2:qw<=converted;
     3:qu<=converted;4:qv<=converted;5:qd<=converted;
    endcase
   end
   if(!INTEGER_UV && busy && fma_response_valid && fma_response_unsupported)unsupported<=1;
   if(read_valid && read_row==5)begin packed_u<=raw_read_data[15:0];packed_v<=raw_read_data[31:16];end
   if(read_valid && read_row==6)rgb<={source_color[7:3],source_color[15:10],source_color[23:19]};
   case(state)
    IDLE:if(start)begin
     done<=0;cycles<=0;unsupported<=!valid_material;fixed_count<=0;
     if(!valid_material)done<=1;
     else begin
      state<=FETCH;busy<=1;job_slot<=slot;
      if(CAPTURE_CONTEXT)begin held_flags<=draw_flags;held_prim<=prim_rgba;held_env<=env_rgba;
       held_uo<=u_offset;held_vo<=v_offset;held_bias<=uv_bias;held_uls<=tile_uls;held_ult<=tile_ult;held_filter<=filter_linear;end
      fetch_row<=0;qu<=0;qv<=0;
      surface_alpha<=draw_flags[23:20]==8 || draw_flags[23:20]==4?8'd255:
           draw_flags[23:20]==3?prim_rgba[31:24]:draw_flags[23:20]==5?env_rgba[31:24]:8'd0;
     end
    end
    FETCH:begin
     if(fetch_row==6)begin
      if(textured&&!INTEGER_UV)begin state<=SUBMIT_SUB;axis<=0;responses<=0;end
      else state<=DRAIN;
     end else fetch_row<=fetch_row+1'b1;
    end
    SUBMIT_SUB:begin
     if(axis)state<=WAIT_SUB;else axis<=1;
    end
    WAIT_SUB:if(fma_response_valid)begin
     if(fma_axis[6])dv<=fma_response_data;else du<=fma_response_data;
     if(responses==1)begin state<=SUBMIT_SCALE;axis<=0;responses<=0;end
     else responses<=responses+1'b1;
    end
    SUBMIT_SCALE:begin
     if(axis)state<=WAIT_SCALE;else axis<=1;
    end
    WAIT_SCALE:if(fma_response_valid)begin
     if(responses==1)state<=DRAIN;else responses<=responses+1'b1;
    end
    DRAIN:if(fixed_count==((textured&&!INTEGER_UV)?6:4))begin
     if(unsupported)begin state<=IDLE;busy<=0;done<=1;end
     else if(INTEGER_UV && textured)state<=INTEGER_U;
     else begin state<=EMIT;emit_word<=0;end
    end
    INTEGER_U:begin qu<=integer_uv;state<=INTEGER_V;end
    INTEGER_V:begin qv<=integer_uv;state<=EMIT;emit_word<=0;end
    EMIT:if(word_ready)begin
     if(emit_word==8)begin state<=IDLE;busy<=0;done<=1;end
     else emit_word<=emit_word+1'b1;
    end
   endcase
  end
 end
endmodule
