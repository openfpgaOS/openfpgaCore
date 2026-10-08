// Private bounded RUN precheck. TRI2 is atomic: neither half is committed when
// either half requires software fallback. Only surviving, fully accepted
// commands enter the 64-triangle list. The caller owns immutable command input
// and must not alter the vertex cache until the subsequent COMMIT completes.
// This module does not fetch commands, resolve materials, or publish packets.
module geometry_run_precheck #(parameter COMMAND_BUFFER=0) (
 input wire clk,reset,start,input wire [6:0] command_count,
 input wire [6:0] triangle_budget,
 input wire [1:0] cull_mode,
 input wire command_valid,output wire command_ready,
 input wire [31:0] command_w0,command_w1,
 output wire check_valid,input wire check_ready,output wire [14:0] check_indices,
 output wire [1:0] check_cull_mode,
 input wire result_valid,output wire result_ready,
 input wire [1:0] result_reject_class,input wire result_fallback,
 input wire [5:0] list_addr,output wire [14:0] list_indices,
 // COMMIT can borrow words64..72 for an unpublished vertex packet.
 input wire packet_access,packet_write,input wire [3:0] packet_write_addr,
 input wire [31:0] packet_write_data,input wire packet_read_valid,
 input wire [3:0] packet_read_addr,output wire [31:0] packet_read_data,
 // A fully drained 16-word AXI chunk uses otherwise spare words80..95.
 // Only FETCH owns this port; it cannot overwrite tentative survivors.
 input wire command_memory_access,command_memory_write,
 input wire [3:0] command_memory_addr,input wire [31:0] command_memory_data,
 output wire [31:0] command_memory_q,
 output wire busy,output reg done,fallback,invalid,
 output reg [6:0] accepted_commands,live_triangles,
 output reg [6:0] checked_triangles,
 output reg [6:0] clip_rejected,cull_rejected,accepted_triangles
);
 localparam IDLE=0,FETCH=1,ISSUE=2,WAIT=3,FINISH=4;
 reg [2:0] state;
 reg [6:0] requested,tentative,seen;
 reg [6:0] budget;
 reg [1:0] first_reject;
 reg [1:0] mode;
 reg second,has_second;
 reg [14:0] first_indices,second_indices;
 (* ramstyle="M10K, no_rw_check" *) reg [31:0] survivors[0:127];
 reg [31:0] memory_q;
 assign list_indices=memory_q[14:0];
 assign packet_read_data=memory_q;
 assign command_memory_q=memory_q;
 wire commit_access=packet_access&&!busy;
 wire fetch_access=COMMAND_BUFFER&&command_memory_access&&state==FETCH;
 wire memory_write=fetch_access?command_memory_write:commit_access?packet_write:(state==WAIT&&result_valid&&!result_fallback&&keep);
 wire [6:0] memory_write_addr=fetch_access?{3'b101,command_memory_addr}:commit_access?{3'b100,packet_write_addr}:{1'b0,tentative[5:0]};
 wire [31:0] memory_write_data=fetch_access?command_memory_data:commit_access?packet_write_data:{17'b0,check_indices};
 wire [6:0] memory_read_addr=fetch_access?{3'b101,command_memory_addr}:commit_access?{3'b100,packet_read_addr}:{1'b0,list_addr};
 wire one=command_w0[31:24]==8'h05;
 wire two=command_w0[31:24]==8'h06;
 wire valid0=!(|{command_w0[23:22],command_w0[15:14],command_w0[7:6]});
 wire valid1=!(|{command_w1[23:22],command_w1[15:14],command_w1[7:6]});
 wire [14:0] indices0={command_w0[5:1],command_w0[13:9],command_w0[21:17]};
 wire [14:0] indices1={command_w1[5:1],command_w1[13:9],command_w1[21:17]};
 wire keep=result_reject_class==0;
 wire [6:0] after_triangle=tentative+{6'b0,keep};
 assign busy=state!=IDLE;
 assign command_ready=state==FETCH;
 assign check_valid=state==ISSUE;
 assign check_indices=second?second_indices:first_indices;
 assign check_cull_mode=mode;
 assign result_ready=state==WAIT;
 always @(posedge clk)begin
  if(fetch_access || !commit_access || packet_read_valid)memory_q<=survivors[memory_read_addr];
  if(memory_write)survivors[memory_write_addr]<=memory_write_data;
  if(reset)begin
   state<=IDLE;done<=0;fallback<=0;invalid<=0;accepted_commands<=0;
   live_triangles<=0;checked_triangles<=0;requested<=0;tentative<=0;
   seen<=0;mode<=0;second<=0;has_second<=0;first_indices<=0;second_indices<=0;
   budget<=64;first_reject<=0;clip_rejected<=0;cull_rejected<=0;accepted_triangles<=0;
  end else begin
   done<=0;
   case(state)
    IDLE:if(start)begin
     accepted_commands<=0;live_triangles<=0;checked_triangles<=0;
     clip_rejected<=0;cull_rejected<=0;accepted_triangles<=0;
     tentative<=0;seen<=0;requested<=command_count;mode<=cull_mode;
     budget<=COMMAND_BUFFER?triangle_budget:7'd64;
     fallback<=0;invalid<=0;
     if(command_count==0||command_count>64||
        (COMMAND_BUFFER&&(triangle_budget==0||triangle_budget>64)))begin state<=FINISH;invalid<=1;fallback<=1;end
     else state<=FETCH;
    end
    FETCH:if(command_valid)begin
     if((!one&&!two)||!valid0||(two&&!valid1))begin fallback<=1;state<=FINISH;end
     else if(seen+{6'b0,two}+7'd1>budget)begin
      if(!COMMAND_BUFFER)begin invalid<=1;fallback<=1;end
      state<=FINISH;
     end
     else begin
      first_indices<=indices0;second_indices<=indices1;has_second<=two;second<=0;
      seen<=seen+{6'b0,two}+7'd1;state<=ISSUE;
     end
    end
    ISSUE:if(check_ready)state<=WAIT;
    WAIT:if(result_valid)begin
     checked_triangles<=checked_triangles+1'b1;
     if(result_fallback)begin tentative<=live_triangles;fallback<=1;state<=FINISH;end
     else begin
      tentative<=after_triangle;
      if(has_second&&!second)begin second<=1;first_reject<=result_reject_class;state<=ISSUE;end
      else begin
       live_triangles<=after_triangle;accepted_commands<=accepted_commands+1'b1;
       clip_rejected<=clip_rejected+{6'b0,result_reject_class==1}+{6'b0,has_second&&first_reject==1};
       cull_rejected<=cull_rejected+{6'b0,result_reject_class==2}+{6'b0,has_second&&first_reject==2};
       accepted_triangles<=accepted_triangles+7'd1+{6'b0,has_second};
       if(accepted_commands+1'b1==requested||(COMMAND_BUFFER&&seen==budget))state<=FINISH;
       else state<=FETCH;
      end
     end
    end
    FINISH:begin state<=IDLE;done<=1;end
   endcase
  end
 end
endmodule
