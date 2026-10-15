// Isolated bounded mesh-task controller. Owns one immutable VTX + TRI span.
// No triangle is published before the ENTIRE span has passed preflight.
// The external command source, raw vertices and task context remain owned
// until a matching ACK after output copy, or a matching whole-task repair ACK.
// cancel stops new work but drains an accepted LOAD/cull before cancellation.
module geometry_mesh_task_ctrl(
 input wire clk,reset,cancel,
 input wire task_valid,output wire task_ready,
 input wire [31:0] task_serial,task_vtx_w0,task_vtx_w1,
 input wire [6:0] task_command_count,input wire [1:0] task_cull_mode,
 input wire [7:0] task_unsupported_features,
 input wire command_valid,output wire command_ready,
 input wire [31:0] command_w0,command_w1,
 output wire load_start,output reg [31:0] load_base,
 output reg [5:0] load_count,output reg [4:0] load_dest,
 input wire load_busy,load_done,load_unsupported,input wire [1:0] load_dma_error,
 output wire check_valid,input wire check_ready,output wire [14:0] check_indices,
 output reg [1:0] check_cull_mode,
 input wire result_valid,output wire result_ready,
 input wire [1:0] result_reject_class,input wire result_fallback,input wire [2:0] result_reason,
 input wire [5:0] list_addr,output wire [14:0] list_indices,
 output wire busy,preflight_valid,fallback_valid,
 output reg [3:0] fallback_reason,output reg [31:0] serial,
 output reg [31:0] owned_mask,used_mask,
 output reg [6:0] live_count,checked_count,clip_rejected,cull_rejected,
 input wire task_ack,input wire [31:0] ack_serial,
 output reg done
);
 localparam IDLE=0,LOAD_ARM=1,LOAD_WAIT=2,FETCH=3,CHECK=4,WAIT_RESULT=5,
            ACCEPTED=6,FALLBACK=7;
 reg [2:0] state;
 reg canceled,second,has_second;
 reg [6:0] requested,commands_seen,triangles_seen;
 reg [14:0] first_indices,second_indices;
 (* ramstyle="M10K, no_rw_check" *) reg [31:0] survivors[0:127];
 reg [31:0] list_q;
 wire [7:0] offered_count=task_vtx_w0[19:12];
 wire [6:0] offered_end=task_vtx_w0[7:1];
 wire bad_load_shape=task_vtx_w0[31:24]!=8'h01 || offered_count==0 || offered_count>32 ||
                     offered_end>32 || {1'b0,offered_end} < offered_count;
 wire [4:0] offered_dest=offered_end[4:0]-offered_count[4:0];
 wire one=command_w0[31:24]==8'h05;
 wire two=command_w0[31:24]==8'h06;
 wire valid0=!(|{command_w0[23:22],command_w0[15:14],command_w0[7:6]});
 wire valid1=!(|{command_w1[23:22],command_w1[15:14],command_w1[7:6]});
 wire [14:0] decoded0={command_w0[5:1],command_w0[13:9],command_w0[21:17]};
 wire [14:0] decoded1={command_w1[5:1],command_w1[13:9],command_w1[21:17]};
 wire owned0=owned_mask[decoded0[4:0]] && owned_mask[decoded0[9:5]] && owned_mask[decoded0[14:10]];
 wire owned1=owned_mask[decoded1[4:0]] && owned_mask[decoded1[9:5]] && owned_mask[decoded1[14:10]];
 wire result_keep=result_reject_class==0;
 wire accepted_result=state==WAIT_RESULT&&result_valid&&!result_fallback&&!cancel&&!canceled;
 assign task_ready=state==IDLE&&!cancel;
 assign busy=state!=IDLE;
 assign load_start=state==LOAD_ARM&&!load_busy&&!cancel&&!canceled;
 assign command_ready=state==FETCH&&!cancel&&!canceled;
 assign check_valid=state==CHECK&&!cancel&&!canceled;
 assign check_indices=second?second_indices:first_indices;
 assign result_ready=state==WAIT_RESULT;
 assign preflight_valid=state==ACCEPTED&&!cancel&&!canceled;
 assign fallback_valid=state==FALLBACK;
 assign list_indices=list_q[14:0];
 always @(posedge clk)begin
  list_q<=survivors[{1'b0,list_addr}];
  if(accepted_result&&result_keep)
   survivors[{1'b0,live_count[5:0]}]<={17'b0,check_indices};
  if(reset)begin
   state<=IDLE;canceled<=0;second<=0;has_second<=0;requested<=0;commands_seen<=0;triangles_seen<=0;
   first_indices<=0;second_indices<=0;load_base<=0;load_count<=0;load_dest<=0;check_cull_mode<=0;
   fallback_reason<=0;serial<=0;owned_mask<=0;used_mask<=0;live_count<=0;checked_count<=0;
   clip_rejected<=0;cull_rejected<=0;done<=0;
  end else begin
   done<=0;
   if(cancel&&busy)canceled<=1;
   case(state)
    IDLE:if(task_valid&&task_ready)begin
     serial<=task_serial;load_base<=task_vtx_w1;load_count<=offered_count[5:0];load_dest<=offered_dest;
     requested<=task_command_count;check_cull_mode<=task_cull_mode;commands_seen<=0;triangles_seen<=0;
     live_count<=0;checked_count<=0;clip_rejected<=0;cull_rejected<=0;used_mask<=0;
     owned_mask<=(32'hffffffff>>(32-offered_count))<<offered_dest;
     canceled<=0;fallback_reason<=0;second<=0;has_second<=0;
     if(bad_load_shape || task_command_count==0 || task_command_count>64)begin fallback_reason<=1;state<=FALLBACK;end
     else if(|task_unsupported_features)begin fallback_reason<=2;state<=FALLBACK;end
     else state<=LOAD_ARM;
    end
    LOAD_ARM:if(cancel||canceled)begin fallback_reason<=14;state<=FALLBACK;end
             else if(!load_busy)state<=LOAD_WAIT;
    LOAD_WAIT:if(load_done)begin
     if(cancel||canceled)begin fallback_reason<=14;state<=FALLBACK;end
     else if(load_dma_error!=0)begin fallback_reason<=3;state<=FALLBACK;end
     else if(load_unsupported)begin fallback_reason<=4;state<=FALLBACK;end
     else state<=FETCH;
    end
    FETCH:if(cancel||canceled)begin fallback_reason<=14;state<=FALLBACK;end
    else if(command_valid&&command_ready)begin
     if((!one&&!two)||!valid0||(two&&!valid1))begin fallback_reason<=5;state<=FALLBACK;end
     else if(!owned0||(two&&!owned1))begin fallback_reason<=6;state<=FALLBACK;end
     else if({1'b0,triangles_seen}+8'd1+{7'b0,two}>64)begin fallback_reason<=7;state<=FALLBACK;end
     else begin
      first_indices<=decoded0;second_indices<=decoded1;has_second<=two;second<=0;
      triangles_seen<=triangles_seen+7'd1+{6'b0,two};state<=CHECK;
     end
    end
    CHECK:if(cancel||canceled)begin fallback_reason<=14;state<=FALLBACK;end
          else if(check_ready)state<=WAIT_RESULT;
    WAIT_RESULT:if(result_valid)begin
     checked_count<=checked_count+1'b1;
     if(cancel||canceled)begin fallback_reason<=14;state<=FALLBACK;end
     else if(result_fallback)begin
      fallback_reason<=result_reason==2?4'd8:result_reason==3?4'd9:result_reason==4?4'd10:4'd11;
      state<=FALLBACK;
     end else begin
      if(result_keep)begin
       live_count<=live_count+1'b1;
       used_mask<=used_mask|(32'b1<<check_indices[4:0])|(32'b1<<check_indices[9:5])|(32'b1<<check_indices[14:10]);
      end
      if(result_reject_class==1)clip_rejected<=clip_rejected+1'b1;
      if(result_reject_class==2)cull_rejected<=cull_rejected+1'b1;
      if(has_second&&!second)begin second<=1;state<=CHECK;end
      else begin
       commands_seen<=commands_seen+1'b1;
       if(commands_seen+7'd1==requested)state<=ACCEPTED;else state<=FETCH;
      end
     end
    end
    ACCEPTED:if(cancel||canceled)begin fallback_reason<=14;state<=FALLBACK;end
     else if(task_ack&&ack_serial==serial)begin state<=IDLE;done<=1;end
    FALLBACK:if(task_ack&&ack_serial==serial)begin state<=IDLE;done<=1;end
    default:state<=IDLE;
   endcase
  end
 end
endmodule
