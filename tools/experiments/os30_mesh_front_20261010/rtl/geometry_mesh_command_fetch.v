// Small bounded original-command reader: two 32-bit registers, no extra BRAM.
// Begin only after vertex LOAD has drained the shared read master. The next
// command may prefetch while the mesh engine culls the previous one. Source
// addresses are bound PSRAM addresses, NOT original CPU virtual pointers.
// cancel drains an accepted read burst; reset must reset the AXI fabric too.
module geometry_mesh_command_fetch #(
 parameter [31:0] SOURCE_MIN_ADDRESS=32'h10000000,
 parameter [32:0] SOURCE_END_ADDRESS=33'h014000000
)(
 input wire clk,reset,start,cancel,
 input wire [31:0] source,input wire [6:0] count,
 output wire busy,output reg done,error,
 output wire command_valid,input wire command_ready,
 output reg [31:0] command_w0,command_w1,
 output wire m_arvalid,input wire m_arready,output wire [31:0] m_araddr,
 output wire [7:0] m_arlen,input wire m_rvalid,output wire m_rready,
 input wire [31:0] m_rdata,input wire [1:0] m_rresp,input wire m_rlast
);
 localparam IDLE=0,ADDRESS=1,READ=2,OFFER=3,FINISH=4;
 reg [2:0] state;
 reg [31:0] address;
 reg [6:0] remaining;
 reg canceled,second,extra;
 wire [32:0] source_end={1'b0,source}+{23'b0,count,3'b0};
 wire bad_source=(|source[2:0])||source<SOURCE_MIN_ADDRESS||source_end>SOURCE_END_ADDRESS||count==0||count>64;
 assign busy=state!=IDLE;
 // Once asserted, AXI ARVALID remains asserted until acceptance, even cancel.
 assign m_arvalid=state==ADDRESS&&!reset;
 assign m_araddr=address;
 assign m_arlen=8'd1;
 assign m_rready=state==READ&&!reset;
 assign command_valid=state==OFFER&&!cancel&&!canceled&&!error&&!reset;
 always @(posedge clk)begin
  done<=0;
  if(reset)begin
   state<=IDLE;address<=0;remaining<=0;canceled<=0;second<=0;extra<=0;
   command_w0<=0;command_w1<=0;done<=0;error<=0;
  end else begin
   if(cancel&&busy)canceled<=1;
   case(state)
    IDLE:if(start&&!cancel)begin
     error<=bad_source;canceled<=0;address<=source;remaining<=count;
     second<=0;extra<=0;
     if(bad_source)state<=FINISH;else state<=ADDRESS;
    end
    ADDRESS:if(m_arready)begin second<=0;extra<=0;state<=READ;end
    READ:if(m_rvalid)begin
     if(!second)command_w0<=m_rdata;
     else if(!extra)command_w1<=m_rdata;
     if(m_rresp!=0||m_rlast!=second||extra)error<=1;
     if(m_rlast)begin
      if(cancel||canceled||error||m_rresp!=0||!second||extra)state<=FINISH;
      else state<=OFFER;
     end else begin
      if(second)extra<=1;
      second<=1;
     end
    end
    OFFER:if(cancel||canceled)state<=FINISH;
     else if(command_ready)begin
      if(remaining==1)state<=FINISH;
      else begin remaining<=remaining-1'b1;address<=address+8;state<=ADDRESS;end
     end
    FINISH:begin done<=1;state<=IDLE;end
    default:begin error<=1;state<=FINISH;end
   endcase
  end
 end
endmodule
