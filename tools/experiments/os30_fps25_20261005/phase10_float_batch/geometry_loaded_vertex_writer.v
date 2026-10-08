// Private completed-LOAD writer. Output is packed36-byte LoadedVertex records;
// source rows belong to this client until done. One accepted AXI burst is fully
// written and its B response retired before another AW is issued. Partial
// results are uncommitted on error. reset is global fabric reset only.
`default_nettype none
module geometry_loaded_vertex_writer #(
 parameter [31:0] MIN_ADDRESS=32'h10000000,
 parameter [32:0] END_ADDRESS=33'h014000000
)(
 input wire clk,reset,start,input wire [31:0] output_base,
 input wire [5:0] count,input wire [4:0] dest,
 output wire raw_read_valid,output wire [7:0] raw_read_addr,input wire [31:0] raw_read_data,
 output wire m_awvalid,input wire m_awready,output wire [31:0] m_awaddr,output wire [7:0] m_awlen,
 output wire m_wvalid,input wire m_wready,output wire [31:0] m_wdata,
 output wire [3:0] m_wstrb,output wire m_wlast,
 input wire m_bvalid,output wire m_bready,input wire [1:0] m_bresp,
 output wire busy,output reg done,output reg [1:0] error,output reg [31:0] cycles
);
 localparam IDLE=2'd0,ADDRESS=2'd1,DATA=2'd2,RESPONSE=2'd3;
 reg [1:0] state;
 reg [31:0] address;
 reg [8:0] remaining;
 reg [4:0] job_dest,vertex,burst_words,issued,sent;
 reg [3:0] field,read_field;
 reg read_pending;
 reg [1:0] fifo_count;
 reg [31:0] fifo0,fifo1;
 wire [11:0] job_bytes=({6'd0,count}<<5)+({6'd0,count}<<2);
 wire [32:0] job_end={1'b0,output_base}+{21'd0,job_bytes};
 wire invalid=count==0 || count>32 || {1'b0,dest}+count>32 || output_base[5:0]!=0
   || output_base<MIN_ADDRESS || {1'b0,output_base}>=END_ADDRESS || job_end>END_ADDRESS || job_end[32];
 wire [4:0] next_words=remaining>16?5'd16:remaining[4:0];
 assign busy=state!=IDLE;
 assign m_awvalid=state==ADDRESS;
 assign m_awaddr=address;
 assign m_awlen={3'b0,next_words}-8'd1;
 // All nonfinal bursts have16words. A64byte aligned base therefore makes
 // every AW64byte aligned, and no burst can cross a4KB boundary.
 assign m_wvalid=state==DATA && fifo_count!=0;
 assign m_wdata=fifo0;assign m_wstrb=4'b1111;
 assign m_wlast=sent+1'b1==burst_words;
 assign m_bready=state==RESPONSE;
 wire take_w=m_wvalid && m_wready;
 // Reserve space for the one synchronous read already in flight. Two skid
 // entries absorb sudden W backpressure and still sustain one word/cycle.
 wire [2:0] reserved={1'b0,fifo_count}+{2'b0,read_pending};
 assign raw_read_valid=state==DATA && issued<burst_words && reserved<(take_w?3:2);
 wire [2:0] raw_row=field<5?field[2:0]:field<7?3'd5:field==7?3'd6:3'd7;
 wire [4:0] raw_slot=job_dest+vertex;
 assign raw_read_addr={raw_row,raw_slot};
 function [31:0] signed_short_float;
  input [15:0] value;
  reg [15:0] magnitude;
  begin
   magnitude=value[15]?-value:value;
   casez(magnitude)
    16'b1???????????????:signed_short_float={value[15],8'd142,magnitude[14:0],8'd0};
    16'b01??????????????:signed_short_float={value[15],8'd141,magnitude[13:0],9'd0};
    16'b001?????????????:signed_short_float={value[15],8'd140,magnitude[12:0],10'd0};
    16'b0001????????????:signed_short_float={value[15],8'd139,magnitude[11:0],11'd0};
    16'b00001???????????:signed_short_float={value[15],8'd138,magnitude[10:0],12'd0};
    16'b000001??????????:signed_short_float={value[15],8'd137,magnitude[9:0],13'd0};
    16'b0000001?????????:signed_short_float={value[15],8'd136,magnitude[8:0],14'd0};
    16'b00000001????????:signed_short_float={value[15],8'd135,magnitude[7:0],15'd0};
    16'b000000001???????:signed_short_float={value[15],8'd134,magnitude[6:0],16'd0};
    16'b0000000001??????:signed_short_float={value[15],8'd133,magnitude[5:0],17'd0};
    16'b00000000001?????:signed_short_float={value[15],8'd132,magnitude[4:0],18'd0};
    16'b000000000001????:signed_short_float={value[15],8'd131,magnitude[3:0],19'd0};
    16'b0000000000001???:signed_short_float={value[15],8'd130,magnitude[2:0],20'd0};
    16'b00000000000001??:signed_short_float={value[15],8'd129,magnitude[1:0],21'd0};
    16'b000000000000001?:signed_short_float={value[15],8'd128,magnitude[0],22'd0};
    16'b0000000000000001:signed_short_float={value[15],8'd127,23'd0};
    default:signed_short_float=0;
   endcase
  end
 endfunction
 wire [15:0] selected_uv=read_field==6?raw_read_data[31:16]:raw_read_data[15:0];
 wire [31:0] converted_uv=signed_short_float(selected_uv);
 wire [31:0] loaded_word=read_field==5 || read_field==6?converted_uv:
   read_field==8?{24'b0,raw_read_data[7:0]}:raw_read_data;
 always @(posedge clk)begin
  if(reset)begin
   state<=IDLE;done<=0;error<=0;cycles<=0;address<=0;remaining<=0;job_dest<=0;
   vertex<=0;field<=0;read_field<=0;read_pending<=0;fifo_count<=0;fifo0<=0;fifo1<=0;
   burst_words<=0;issued<=0;sent<=0;
  end else begin
   done<=0;read_pending<=raw_read_valid;read_field<=field;
   if(busy)cycles<=cycles+1'b1;
   if(raw_read_valid)begin
    issued<=issued+1'b1;
    if(field==8)begin field<=0;vertex<=vertex+1'b1;end
    else field<=field+1'b1;
   end
   case({read_pending,take_w})
    2'b10:begin
     if(fifo_count==0)fifo0<=loaded_word;else fifo1<=loaded_word;
     fifo_count<=fifo_count+1'b1;
    end
    2'b01:begin fifo0<=fifo1;fifo_count<=fifo_count-1'b1;end
    2'b11:begin
     if(fifo_count==1)fifo0<=loaded_word;
     else begin fifo0<=fifo1;fifo1<=loaded_word;end
    end
   endcase
   case(state)
    IDLE:if(start)begin
     error<=0;cycles<=0;
     if(invalid)begin done<=1;error<=1;end
     else begin
      state<=ADDRESS;address<=output_base;remaining<=({3'd0,count}<<3)+{3'd0,count};
      job_dest<=dest;vertex<=0;field<=0;fifo_count<=0;read_pending<=0;
     end
    end
    ADDRESS:if(m_awready)begin
     state<=DATA;burst_words<=next_words;issued<=0;sent<=0;
    end
    DATA:if(take_w)begin
     sent<=sent+1'b1;
     if(m_wlast)state<=RESPONSE;
    end
    RESPONSE:if(m_bvalid)begin
     if(m_bresp!=0)begin error<=2;done<=1;state<=IDLE;end
     else if(remaining=={4'd0,burst_words})begin done<=1;state<=IDLE;end
     else begin address<=address+{25'd0,burst_words,2'b0};remaining<=remaining-{4'd0,burst_words};state<=ADDRESS;end
    end
   endcase
  end
 end
endmodule
`default_nettype wire
