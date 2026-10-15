// Variable-length immutable packets in one 512-word dual-port RAM.
// Each record is {serial,flags,uofs,vofs,payload_count,payload...}. A producer
// reserves its entire record before writing; only the final exact-length commit
// publishes it. An older committed consumer can drain during a later fill.
module geometry_mesh_queue (
 input wire clk,reset,
 input wire reserve,output wire reserve_ready,
 input wire [31:0] reserve_serial,reserve_flags,reserve_uofs,reserve_vofs,
 input wire [8:0] reserve_words,output wire [31:0] producer_serial,
 input wire producer_valid,input wire [31:0] producer_word,
 output wire producer_ready,input wire producer_done,producer_error,
 input wire marker_valid,input wire [31:0] marker_serial,marker_flags,marker_uofs,marker_vofs,
 output wire marker_ready,marker_error,
 output wire word_valid,output wire [31:0] word_data,input wire word_ready,
 output reg done,output wire error,busy,filling,output wire [31:0] owner_serial
);
 localparam EMPTY=0,HEADER_PRIME=1,READY=2,DRAIN=3,FAULT=4,HEADER_CAPTURE=5;
 reg [2:0] state,header_index;
 reg fault,fill_active;
 reg [31:0] serial,flags,uofs,vofs;
 reg [31:0] fill_serial,fill_flags,fill_uofs,fill_vofs;
 reg [8:0] fill_count;
 reg [2:0] fill_header;
 reg [9:0] write_count,read_count,count;
 // The tenth bit distinguishes full from empty across the physical 512-word
 // wrap. Credits include the whole reserved record; only one fill may exist.
 reg [9:0] write_ptr,committed_ptr,read_ptr,reserved_end;
 wire [9:0] used_words=committed_ptr-read_ptr;
 wire [9:0] free_words=10'd512-used_words;
 wire [9:0] reservation_words={1'b0,reserve_words}+10'd5;
 wire reserve_take=reserve&&reserve_ready;
 assign reserve_ready=!reset&&!fault&&!fill_active&&reserve_words<=416&&
   used_words<=512&&free_words>=reservation_words;
 assign owner_serial=serial;
 assign producer_serial=fill_serial;
 assign busy=fill_active||state!=EMPTY||read_ptr!=committed_ptr;
 assign filling=fill_active;
 assign producer_ready=fill_active&&fill_header==5&&write_count<{1'b0,fill_count}&&!fault&&!reset;
 wire push=producer_valid&&producer_ready;
 wire header_write=fill_active&&fill_header<5&&!fault&&!reset;
 wire commit=fill_active&&producer_done&&!producer_valid&&!producer_error&&
   fill_header==5&&write_count=={1'b0,fill_count}&&write_ptr==reserved_end&&!fault&&!reset;
 wire marker_match=marker_serial==serial&&marker_flags==flags&&marker_uofs==uofs&&marker_vofs==vofs;
 wire filling_match=marker_serial==fill_serial&&marker_flags==fill_flags&&
   marker_uofs==fill_uofs&&marker_vofs==fill_vofs;
 assign marker_ready=marker_valid&&marker_match&&state==READY&&!fault&&!reset;
 // A header in flight is ordinary backpressure. A marker for the sole
 // uncommitted head also waits. Never compare against the previous head's regs.
 assign marker_error=marker_valid&&!reset&&(fault||state==FAULT||state==DRAIN||
   (state==READY&&!marker_match)||
   (state==EMPTY&&read_ptr==committed_ptr&&(!fill_active||!filling_match)));
 assign word_valid=state==DRAIN&&read_count<count&&!fault&&!reset;
 wire pop=word_valid&&word_ready;
 assign error=fault;
 (* ramstyle="M10K, no_rw_check" *) reg [31:0] queue[0:511];
 reg [31:0] data;
 reg [31:0] header_word;
 wire [8:0] read_addr=read_ptr[8:0]+{8'b0,pop};
 assign word_data=data;
 always @*begin
  case(fill_header)
   0:header_word=fill_serial;
   1:header_word=fill_flags;
   2:header_word=fill_uofs;
   3:header_word=fill_vofs;
   default:header_word={23'b0,fill_count};
  endcase
 end
 always @(posedge clk)begin
  data<=queue[read_addr];
  if(header_write||push)queue[write_ptr[8:0]]<=header_write?header_word:producer_word;
  if(reset)begin
   state<=EMPTY;header_index<=0;fault<=0;fill_active<=0;
   serial<=0;flags<=0;uofs<=0;vofs<=0;
   fill_serial<=0;fill_flags<=0;fill_uofs<=0;fill_vofs<=0;fill_count<=0;fill_header<=0;
   write_count<=0;read_count<=0;count<=0;done<=0;
   write_ptr<=0;committed_ptr<=0;read_ptr<=0;reserved_end<=0;
  end else begin
   done<=0;
   if(reserve_take)begin
    fill_active<=1;fill_serial<=reserve_serial;fill_flags<=reserve_flags;
    fill_uofs<=reserve_uofs;fill_vofs<=reserve_vofs;fill_count<=reserve_words;
    fill_header<=0;write_count<=0;write_ptr<=committed_ptr;
    reserved_end<=committed_ptr+reservation_words;
   end
   if(header_write)begin fill_header<=fill_header+1'b1;write_ptr<=write_ptr+1'b1;end
   if(push)begin write_count<=write_count+1'b1;write_ptr<=write_ptr+1'b1;end
   if(commit)begin committed_ptr<=write_ptr;fill_active<=0;end
   // Extra/short payloads never advance committed_ptr. Older records remain
   // intact, but the consumer halts until the coupled GPU/queue reset.
   if((reserve&&!fill_active&&reserve_words>416)||
      (fill_active&&(producer_error||
        (producer_valid&&fill_header==5&&write_count=={1'b0,fill_count})||
        (producer_done&&!commit))))fault<=1;
   if(fault)state<=FAULT;
   else case(state)
    EMPTY:if(read_ptr!=committed_ptr)begin header_index<=0;state<=HEADER_PRIME;end
    HEADER_PRIME:state<=HEADER_CAPTURE;
    HEADER_CAPTURE:begin
     read_ptr<=read_ptr+1'b1;
     case(header_index)
      0:serial<=data;
      1:flags<=data;
      2:uofs<=data;
      3:vofs<=data;
      4:begin
       count<=data[9:0];read_count<=0;
       if(data>416)fault<=1;
      end
      default:fault<=1;
     endcase
     if(header_index==4)state<=READY;
     else begin header_index<=header_index+1'b1;state<=HEADER_PRIME;end
    end
    READY:if(marker_ready)begin
     if(count==0)begin done<=1;state<=EMPTY;end
     else state<=DRAIN;
    end
    DRAIN:if(pop)begin
     read_count<=read_count+1'b1;read_ptr<=read_ptr+1'b1;
     if(read_count+10'd1==count)begin done<=1;state<=EMPTY;end
    end
    FAULT:;
    default:fault<=1;
   endcase
  end
 end
endmodule
