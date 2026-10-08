// Private RUN publisher. Count first, reserve an exact unpublished ring range,
// then stream prepared vertices and indexed triangles in original order.
// Abort discards the WHOLE reservation. The caller owns DRAW uniforms and raw
// slots until done; the GPU owns published words until its normal raster fence.
module geometry_run_commit (
 input wire clk,reset,start,input wire [6:0] live_triangles,
 input wire [31:0] draw_flags,initial_stale,prim_rgba,env_rgba,
 input wire [15:0] tile_uls,tile_ult,input wire filter_linear,
 output wire [5:0] list_addr,input wire [14:0] list_indices,
 output wire packet_access,packet_write,output wire [3:0] packet_write_addr,
 output wire [31:0] packet_write_data,output wire packet_read_valid,
 output wire [3:0] packet_read_addr,input wire [31:0] packet_read_data,
 output wire raw_read_valid,output wire [7:0] raw_read_addr,input wire [31:0] raw_read_data,
 output wire geo_request,output wire [12:0] geo_words,input wire geo_grant,
 output wire geo_valid,output wire [31:0] geo_data,input wire geo_ready,
 output wire geo_abort,input wire geo_owned,geo_done,
 output wire busy,output reg done,unsupported,
 output reg [31:0] final_stale,output reg [12:0] emitted_words,
 output reg [7:0] emitted_vertices
);
 localparam IDLE=0,COUNT_READ=1,COUNT_CAPTURE=2,CLAIM=3,LIST_READ=4,
    LIST_CAPTURE=5,CORNER=6,PACKET_START=7,PACKET_WAIT=8,HEADER=9,
    INDEX=10,PUBLISH=11,ABORT=12,FINISH=13;
 reg [3:0] state;
 reg [6:0] count,index;
 reg [1:0] corner;
 reg [14:0] indices;
 reg [2:0] dirty;
 reg [31:0] stale;
 reg [12:0] reserved_words;
 wire [31:0] read_mask=(32'd1<<list_indices[4:0])|(32'd1<<list_indices[9:5])|(32'd1<<list_indices[14:10]);
 wire [31:0] used_mask=(32'd1<<indices[4:0])|(32'd1<<indices[9:5])|(32'd1<<indices[14:10]);
 // Duplicated dirty corners intentionally count before ANY stale-bit clear.
 wire [1:0] dirty_count={1'b0,stale[list_indices[4:0]]}+{1'b0,stale[list_indices[9:5]]}+{1'b0,stale[list_indices[14:10]]};
 wire [5:0] triangle_words=6'd2+{1'b0,dirty_count,3'b0}+{4'b0,dirty_count};
 wire [4:0] slot=corner==0?indices[4:0]:corner==1?indices[9:5]:indices[14:10];
 wire p_busy,p_done,p_bad,p_valid;
 wire [31:0] p_data;
 wire p_packet_write,p_packet_read_valid,p_raw_read_valid;
 wire [31:0] packet_flags={draw_flags[15:8],24'b0}|32'h00800100|
                       (draw_flags[0]?32'h00000200:32'b0);
 wire valid_rgb=draw_flags[15:8]<=8'd6 || draw_flags[15:8]==8'd255;
 // The ring may be reset independently while an M2 DMA requester remains
 // alive. Losing an acquired reservation invalidates the entire RUN. Packet
 // state is local BRAM/arithmetic only, so it can be cancelled independently.
 wire ownership_lost=state>=LIST_READ && state<=PUBLISH && !geo_owned && !geo_done;
 wire packet_reset=reset || ownership_lost || state==ABORT;
 assign busy=state!=IDLE;
 assign list_addr=index[5:0];
 assign packet_access=geo_owned && (state==PACKET_START||state==PACKET_WAIT);
 assign packet_write=geo_owned && p_packet_write;
 assign packet_read_valid=geo_owned && p_packet_read_valid;
 assign raw_read_valid=geo_owned && p_raw_read_valid;
 assign geo_request=state==CLAIM;
 assign geo_words=reserved_words;
 assign geo_valid=geo_owned && (state==HEADER||state==INDEX||(state==PACKET_WAIT&&p_valid));
 assign geo_data=state==HEADER?32'h54000001:state==INDEX?{17'b0,indices}:p_data;
 assign geo_abort=state==ABORT&&geo_owned;
 geometry_vertex_packet #(.CAPTURE_CONTEXT(0),.EXTERNAL_PACKET(1),.INTEGER_UV(1)) packet (
  .clk(clk),.reset(packet_reset),.start(state==PACKET_START),.slot(slot),
  .draw_flags(packet_flags),.prim_rgba(prim_rgba),.env_rgba(env_rgba),
  .u_offset(32'b0),.v_offset(32'b0),.uv_bias(32'b0),
  .tile_uls(tile_uls),.tile_ult(tile_ult),.filter_linear(filter_linear),
  .raw_read_valid(p_raw_read_valid),.raw_read_addr(raw_read_addr),.raw_read_data(raw_read_data),
  .fma_request_valid(),.fma_request_a(),.fma_request_b(),.fma_request_c(),.fma_request_multiply(),
  .fma_response_valid(1'b0),.fma_response_data(32'b0),.fma_response_unsupported(1'b0),
  .word_valid(p_valid),.word_ready(state==PACKET_WAIT&&geo_ready),.word_data(p_data),
  .busy(p_busy),.done(p_done),.unsupported(p_bad),.cycles(),.surface_alpha(),
  .packet_write(p_packet_write),.packet_write_addr(packet_write_addr),.packet_write_data(packet_write_data),
  .packet_read_valid(p_packet_read_valid),.packet_read_addr(packet_read_addr),.packet_read_data(packet_read_data));
 always @(posedge clk)begin
  if(reset)begin
   state<=IDLE;count<=0;index<=0;corner<=0;indices<=0;dirty<=0;stale<=0;reserved_words<=0;
   done<=0;unsupported<=0;final_stale<=0;emitted_words<=0;emitted_vertices<=0;
  end else begin
   done<=0;
   if(geo_valid&&geo_ready)emitted_words<=emitted_words+1'b1;
   if(ownership_lost)begin unsupported<=1;state<=ABORT;end
   else case(state)
    IDLE:if(start)begin
     count<=live_triangles;index<=0;stale<=initial_stale;final_stale<=initial_stale;
     reserved_words<=0;emitted_words<=0;emitted_vertices<=0;unsupported<=0;
     if(live_triangles>64||!valid_rgb||(|draw_flags[31:16])||(|draw_flags[7:1]))begin
      unsupported<=1;state<=FINISH;
     end else if(live_triangles==0)state<=FINISH;
     else state<=COUNT_READ;
    end
    COUNT_READ:state<=COUNT_CAPTURE;
    COUNT_CAPTURE:begin
     reserved_words<=reserved_words+{7'b0,triangle_words};stale<=stale&~read_mask;
     if(index+1'b1==count)begin index<=0;stale<=initial_stale;state<=CLAIM;end
     else begin index<=index+1'b1;state<=COUNT_READ;end
    end
    CLAIM:if(geo_grant)state<=LIST_READ;
    LIST_READ:state<=LIST_CAPTURE;
    LIST_CAPTURE:begin
     indices<=list_indices;dirty<={stale[list_indices[14:10]],stale[list_indices[9:5]],stale[list_indices[4:0]]};
     corner<=0;state<=CORNER;
    end
    CORNER:if(dirty[corner])state<=PACKET_START;
     else if(corner==2)state<=HEADER;else corner<=corner+1'b1;
    PACKET_START:state<=PACKET_WAIT;
    PACKET_WAIT:if(p_done)begin
     if(p_bad)begin unsupported<=1;state<=ABORT;end
     else begin
      emitted_vertices<=emitted_vertices+1'b1;
      if(corner==2)state<=HEADER;else begin corner<=corner+1'b1;state<=CORNER;end
     end
    end
    HEADER:if(geo_ready)state<=INDEX;
    INDEX:if(geo_ready)begin
     stale<=stale&~used_mask;
     if(index+1'b1==count)begin final_stale<=stale&~used_mask;state<=PUBLISH;end
     else begin index<=index+1'b1;state<=LIST_READ;end
    end
    PUBLISH:if(geo_done)state<=FINISH;
    ABORT:if(!geo_owned)begin
     emitted_words<=0;emitted_vertices<=0;final_stale<=initial_stale;state<=FINISH;
    end
    FINISH:begin done<=1;state<=IDLE;end
   endcase
  end
 end
endmodule
