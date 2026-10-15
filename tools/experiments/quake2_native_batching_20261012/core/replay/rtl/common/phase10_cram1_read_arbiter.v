// Private capacity/performance probe. Two always-ready readers share CRAM1's
// existing adapter. Fabric reset must reset adapter/controller and both readers.
// Clients MUST hold AR until ready and accept every R beat without backpressure.
// A grant is locked before the adapter's delayed ARREADY can acknowledge it.
`default_nettype none
module geometry_cram1_read_arbiter (
 input wire clk,reset_n,
 // Texture-client reset may cancel only its unaccepted request.
 input wire s0_reset,
 input wire s0_arvalid,output wire s0_arready,input wire [31:0] s0_araddr,
 input wire [7:0] s0_arlen,output wire s0_rvalid,output wire [31:0] s0_rdata,output wire s0_rlast,
 input wire s1_arvalid,output wire s1_arready,input wire [31:0] s1_araddr,
 input wire [7:0] s1_arlen,output wire s1_rvalid,output wire [31:0] s1_rdata,output wire s1_rlast,
 output wire m_arvalid,input wire m_arready,output wire [31:0] m_araddr,
 output wire [7:0] m_arlen,input wire m_rvalid,input wire [31:0] m_rdata,input wire m_rlast
);
 localparam IDLE=2'd0,ADDRESS=2'd1,RESPONSE=2'd2;
 reg [1:0] state;
 reg owner,prefer_one,discard_s0;
 wire s0_valid=s0_arvalid && !s0_reset;
 wire selected=state==IDLE ? (s1_arvalid && (!s0_valid || prefer_one)) : owner;
 assign m_arvalid=state!=RESPONSE && (selected ? s1_arvalid : s0_valid);
 assign m_araddr=selected ? s1_araddr : s0_araddr;
 assign m_arlen=selected ? s1_arlen : s0_arlen;
 assign s0_arready=state!=RESPONSE && !selected && m_arready && !s0_reset;
 assign s1_arready=state!=RESPONSE && selected && m_arready;
 assign s0_rvalid=state==RESPONSE && !owner && m_rvalid && !discard_s0 && !s0_reset;
 assign s1_rvalid=state==RESPONSE && owner && m_rvalid;
 assign s0_rdata=m_rdata;
 assign s1_rdata=m_rdata;
 assign s0_rlast=s0_rvalid && m_rlast;
 assign s1_rlast=s1_rvalid && m_rlast;
 always @(posedge clk or negedge reset_n)begin
  if(!reset_n)begin state<=IDLE;owner<=0;prefer_one<=0;discard_s0<=0;end
  else begin
   if(state!=IDLE && !owner && s0_reset)discard_s0<=1;
   case(state)
   IDLE:if(m_arvalid)begin
    owner<=selected;discard_s0<=0;
    if(m_arready)begin state<=RESPONSE;prefer_one<=!selected;end
    else state<=ADDRESS;
   end
   ADDRESS:begin
    // Adapter ARREADY acknowledges its previous-cycle capture. Even if the
    // texture client reset meanwhile, the accepted physical burst must drain.
    if(m_arready)begin state<=RESPONSE;prefer_one<=!owner;end
    else if(!owner && s0_reset)state<=IDLE;
   end
   RESPONSE:if(m_rvalid && m_rlast)state<=IDLE;
   default:state<=IDLE;
  endcase
  end
 end
endmodule
`default_nettype wire
