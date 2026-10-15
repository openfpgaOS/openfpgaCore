// Experimental consumer of a completed, immutable mesh-task raw view.
// The owner must hold the view/list until done and ACK the task afterwards.
// Validates every used vertex AND every list entry before publishing anything.
// Unsupported numeric inputs return error with zero output. Successful validation
// holds until publish; raw/list/context must remain immutable across that wait. GPU material state
// is a separate ordered prerequisite: this unit emits vertex/index words only.
// reset is a fabric/consumer reset: resetting this producer alone during a
// published packet is invalid. No asynchronous per-task cancel after publication.
module geometry_mesh_packet (
 input wire clk,reset,start,publish,
 output wire validated,output reg [8:0] payload_words,
 input wire [31:0] used_mask,flags_rgb,u_offset,v_offset,
 input wire [6:0] live_count,
 output wire [7:0] view_addr,input wire [31:0] view_data,
 output wire [5:0] list_addr,input wire [14:0] list_indices,
 output wire word_valid,input wire word_ready,output reg [31:0] word_data,
 output wire busy,output reg done,error
);
 localparam IDLE=0,SELECT=1,PRIME=2,CAPTURE=3,EMIT_VERTEX=4,
            LIST_PRIME=5,LIST_CAPTURE=6,DRAW_HEADER=7,DRAW_WORD=8,FINISH=9,VALIDATED=10;
 reg [3:0] state;
 reg validation;
 reg [31:0] mask,flags,uo,vo,list_mask;
 reg [6:0] count;
 reg [5:0] slot,job;
 reg [2:0] row;
 reg [3:0] emit_index;
 reg [31:0] x,y,w,u,v,depth;
 reg [15:0] rgb;
 reg [14:0] indices;
 assign busy=state!=IDLE;
 assign validated=state==VALIDATED;
 assign view_addr={row,slot[4:0]};
 assign list_addr=job;
 assign word_valid=!reset&&(state==EMIT_VERTEX||state==DRAW_HEADER||state==DRAW_WORD);
 wire [31:0] converted;
 wire conversion_error;
 geometry_import_convert convert(.value(view_data),.depth_mode(row==4),.result(converted),.error(conversion_error));
 wire signed [31:0] ru={{16{view_data[15]}},view_data[15:0]};
 wire signed [31:0] rv={{16{view_data[31]}},view_data[31:16]};
 wire signed [31:0] tu=ru-$signed(uo)+(flags[17]?32'sd16:32'sd0);
 wire signed [31:0] tv=rv-$signed(vo)+(flags[17]?32'sd16:32'sd0);
 wire bad_vertex=((row==0||row==1||row==3||row==4)&&conversion_error)||
   (row==3&&(view_data<32'h00800000||view_data>=32'h46fffe00))||
   (row==4&&(view_data[31]||view_data[30:23]==0||view_data[30:23]==8'hff))||
   (row==7&&view_data!=0);
 wire [31:0] triangle_mask=(32'b1<<list_indices[4:0])|
   (32'b1<<list_indices[9:5])|(32'b1<<list_indices[14:10]);
 wire bad_context=|flags_rgb[31:19]||live_count>64||
   ((live_count==0)!=(used_mask==0))||
   (flags_rgb[16]&&(u_offset>524280||v_offset>524280||(|u_offset[2:0])||(|v_offset[2:0])));
 always @* begin
  word_data=0;
  if(state==EMIT_VERTEX)case(emit_index)
   0:word_data=32'h56000008;
   1:word_data={27'b0,slot[4:0]};
   2:word_data=x;3:word_data=y;4:word_data=w;
   5:word_data=u;6:word_data=v;7:word_data={16'b0,rgb};8:word_data=depth;
   default:;
  endcase
  else if(state==DRAW_HEADER)word_data=32'h54000001;
  else if(state==DRAW_WORD)word_data={17'b0,indices};
 end
 always @(posedge clk)begin
  done<=0;
  if(reset)begin
   state<=IDLE;validation<=0;mask<=0;flags<=0;uo<=0;vo<=0;list_mask<=0;
   count<=0;slot<=0;job<=0;row<=0;emit_index<=0;
   x<=0;y<=0;w<=0;u<=0;v<=0;depth<=0;rgb<=0;indices<=0;done<=0;error<=0;payload_words<=0;
  end else case(state)
   IDLE:if(start)begin
    // Count during the existing validation walk, avoiding a combinational
    // popcount tree or a second admission scan. No output exists yet.
    payload_words<={1'b0,live_count,1'b0};
    error<=bad_context;
    if(bad_context)state<=FINISH;
    else if(live_count==0)begin count<=0;state<=VALIDATED;end
    else begin
     mask<=used_mask;flags<=flags_rgb;uo<=u_offset;vo<=v_offset;count<=live_count;
     slot<=0;job<=0;list_mask<=0;validation<=1;state<=SELECT;
    end
   end
   SELECT:if(slot==32)begin job<=0;state<=LIST_PRIME;end
    else if(mask[slot[4:0]])begin
     if(validation)payload_words<=payload_words+9'd9;
     row<=0;state<=PRIME;
    end
    else slot<=slot+1'b1;
   PRIME:state<=CAPTURE;
   CAPTURE:if(bad_vertex)begin error<=1;state<=FINISH;end
    else begin
     case(row)
      0:x<=converted;1:y<=converted;3:w<=converted;4:depth<=converted;
      5:begin u<=flags[16]?(tu<<11):0;v<=flags[16]?(tv<<11):0;end
      6:rgb<=flags[18]?{view_data[7:3],view_data[15:10],view_data[23:19]}:flags[15:0];
      default:;
     endcase
     if(row==7)begin
      if(validation)begin slot<=slot+1'b1;state<=SELECT;end
      else begin emit_index<=0;state<=EMIT_VERTEX;end
     end else begin row<=row+1'b1;state<=PRIME;end
    end
   EMIT_VERTEX:if(word_ready)begin
    if(emit_index==8)begin slot<=slot+1'b1;state<=SELECT;end
    else emit_index<=emit_index+1'b1;
   end
   LIST_PRIME:state<=LIST_CAPTURE;
   LIST_CAPTURE:if(validation)begin
    if((triangle_mask&~mask)!=0||({1'b0,job}+7'd1==count&&(list_mask|triangle_mask)!=mask))begin
     error<=1;state<=FINISH;
    end else begin
     list_mask<=list_mask|triangle_mask;
     if({1'b0,job}+7'd1==count)begin validation<=0;slot<=0;state<=VALIDATED;end
     else begin job<=job+1'b1;state<=LIST_PRIME;end
    end
   end else begin indices<=list_indices;state<=DRAW_HEADER;end
   DRAW_HEADER:if(word_ready)state<=DRAW_WORD;
   DRAW_WORD:if(word_ready)begin
    if({1'b0,job}+7'd1==count)state<=FINISH;
    else begin job<=job+1'b1;state<=LIST_PRIME;end
   end
   VALIDATED:if(publish)begin
    if(count==0)state<=FINISH;else begin validation<=0;slot<=0;job<=0;state<=SELECT;end
   end
   FINISH:begin done<=1;state<=IDLE;end
   default:begin error<=1;state<=FINISH;end
  endcase
 end
endmodule
