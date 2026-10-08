// Read-only CPU cacheline view of a native raw LOAD result. The existing
// cpu_target_port has already serialized transactions and restored AXI IDs.
// Other addresses pass unchanged to SDRAM. B is an unbackpressured response,
// matching the existing CPU-target/SDRAM-arbiter interface.
module geometry_bram_alias #(
 parameter [31:0] BASE=32'h13f80000
)(
 input wire clk,reset,cpu_reset,
 input wire s_arvalid,output wire s_arready,input wire [31:0] s_araddr,input wire [7:0] s_arlen,
 output wire s_rvalid,input wire s_rready,output wire [31:0] s_rdata,output wire [1:0] s_rresp,output wire s_rlast,
 input wire s_awvalid,output wire s_awready,input wire [31:0] s_awaddr,input wire [7:0] s_awlen,
 input wire s_wvalid,output wire s_wready,input wire [31:0] s_wdata,input wire [3:0] s_wstrb,input wire s_wlast,
 output wire s_bvalid,output wire [1:0] s_bresp,
 output wire m_arvalid,input wire m_arready,output wire [31:0] m_araddr,output wire [7:0] m_arlen,
 input wire m_rvalid,output wire m_rready,input wire [31:0] m_rdata,input wire [1:0] m_rresp,input wire m_rlast,
 output wire m_awvalid,input wire m_awready,output wire [31:0] m_awaddr,output wire [7:0] m_awlen,
 output wire m_wvalid,input wire m_wready,output wire [31:0] m_wdata,output wire [3:0] m_wstrb,output wire m_wlast,
 input wire m_bvalid,input wire [1:0] m_bresp,
 output wire [7:0] view_read_addr,input wire [31:0] view_read_data,
 output wire view_active,input wire view_block,input wire view_valid,input wire [5:0] view_count
);
 // Cached0x13f80000 and the usual uncached0x53f80000 map to one view.
 wire alias_ar=(s_araddr&32'hbffffc00)==(BASE&32'hbffffc00);
 wire alias_aw=(s_awaddr&32'hbffffc00)==(BASE&32'hbffffc00);
 reg active,read_error,held_valid,read_pending,read_zero;
 reg [5:0] held_count;
 reg [7:0] word_address;
 reg [8:0] words,issued,sent;
 reg [1:0] fifo_count;
 reg [31:0] fifo0,fifo1;
 localparam WRITE_IDLE=2'd0,WRITE_NORMAL=2'd1,WRITE_REJECT=2'd2,WRITE_RESPONSE=2'd3;
 reg [1:0] write_state;
 reg [8:0] write_left;
 wire take_r=active && fifo_count!=0 && s_rready;
 wire [2:0] reserved={1'b0,fifo_count}+{2'b0,read_pending};
 wire issue=active && issued<words && reserved<(take_r?3:2);
 wire take_ar=s_arvalid && s_arready && alias_ar;
 wire take_aw=s_awvalid && s_awready && alias_aw;
 wire write_forward=write_state==WRITE_NORMAL ||
       (write_state==WRITE_IDLE && s_awvalid && !alias_aw);
 wire [9:0] end_word={2'b0,s_araddr[9:2]}+{2'b0,s_arlen}+10'd1;
 assign view_active=active;
 assign view_read_addr={word_address[2:0],word_address[7:3]};
 assign s_arready=alias_ar?(!active&&!view_block):(!active&&m_arready);
 assign m_arvalid=s_arvalid&&!alias_ar&&!active;
 assign m_araddr=s_araddr;assign m_arlen=s_arlen;
 assign s_rvalid=active?fifo_count!=0:m_rvalid;
 assign s_rdata=active?fifo0:m_rdata;
 assign s_rresp=active?(read_error?2'b10:2'b00):m_rresp;
 assign s_rlast=active?(sent+1'b1==words):m_rlast;
 assign m_rready=!active&&s_rready;
 assign s_awready=write_state==WRITE_IDLE && (alias_aw || m_awready);
 assign m_awvalid=s_awvalid&&!alias_aw&&write_state==WRITE_IDLE;
 assign m_awaddr=s_awaddr;assign m_awlen=s_awlen;
 assign s_wready=write_state==WRITE_REJECT?1'b1:write_forward?m_wready:1'b0;
 assign m_wvalid=s_wvalid&&write_forward;assign m_wdata=s_wdata;
 assign m_wstrb=s_wstrb;assign m_wlast=s_wlast;
 assign s_bvalid=write_state==WRITE_RESPONSE || m_bvalid;
 assign s_bresp=write_state==WRITE_RESPONSE?2'b10:m_bresp;
 always @(posedge clk)begin
  if(reset||cpu_reset)begin
   active<=0;read_error<=0;held_valid<=0;held_count<=0;word_address<=0;
   words<=0;issued<=0;sent<=0;fifo_count<=0;fifo0<=0;fifo1<=0;
   read_pending<=0;read_zero<=0;
  end else begin
   read_pending<=issue;
   if(issue)begin
    read_zero<=read_error || !held_valid || {1'b0,word_address[7:3]}>=held_count;
    word_address<=word_address+1'b1;issued<=issued+1'b1;
   end
   case({read_pending,take_r})
    2'b10:begin
     if(fifo_count==0)fifo0<=read_zero?32'b0:view_read_data;
     else fifo1<=read_zero?32'b0:view_read_data;
     fifo_count<=fifo_count+1'b1;
    end
    2'b01:begin fifo0<=fifo1;fifo_count<=fifo_count-1'b1;end
    2'b11:begin
     if(fifo_count==1)fifo0<=read_zero?32'b0:view_read_data;
     else begin fifo0<=fifo1;fifo1<=read_zero?32'b0:view_read_data;end
    end
   endcase
   if(take_ar)begin
    active<=1;read_error<=s_araddr[1:0]!=0 || end_word>256;
    held_valid<=view_valid;held_count<=view_count;word_address<=s_araddr[9:2];
    words<={1'b0,s_arlen}+9'd1;issued<=0;sent<=0;fifo_count<=0;read_pending<=0;
   end
   if(take_r)begin
    sent<=sent+1'b1;
    if(sent+1'b1==words)active<=0;
   end
  end
 end
 always @(posedge clk)begin
  if(reset)begin write_state<=WRITE_IDLE;write_left<=0;end
  // A local rejection owns no external transaction and can be discarded.
  // An accepted normal SDRAM write must retain its existing fabric lifetime.
  else if(cpu_reset && write_state!=WRITE_NORMAL)begin write_state<=WRITE_IDLE;write_left<=0;end
  else begin
   if(write_state==WRITE_RESPONSE)write_state<=WRITE_IDLE;
   if(s_awvalid && s_awready)begin
    write_state<=alias_aw?WRITE_REJECT:WRITE_NORMAL;
    write_left<={1'b0,s_awlen}+9'd1;
   end
   if(write_state==WRITE_NORMAL && m_bvalid)write_state<=WRITE_IDLE;
   if(write_state==WRITE_REJECT && s_wvalid)begin
    write_left<=write_left-1'b1;
    if(write_left==1)write_state<=WRITE_RESPONSE;
   end
  end
 end
endmodule
