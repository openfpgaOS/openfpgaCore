//
// sync_fifo.v - small single-clock FIFO
//
// Parameterized, LUT/MLAB-friendly FIFO for control/event paths.  This is
// intentionally simple: first-word fall-through read data, explicit push/pop,
// and a count output for status registers.
//

`default_nettype none

module sync_fifo #(
    parameter WIDTH = 32,
    parameter DEPTH = 16,
    parameter ADDR_WIDTH = 4,
    parameter RAMSTYLE = ""
) (
    input  wire                  clk,
    input  wire                  reset,
    input  wire                  clear,

    input  wire                  push,
    input  wire [WIDTH-1:0]      din,
    input  wire                  pop,
    output wire [WIDTH-1:0]      dout,

    output wire                  empty,
    output wire                  full,
    output reg  [ADDR_WIDTH:0]   count
);

(* ramstyle = RAMSTYLE *) reg [WIDTH-1:0] mem [0:DEPTH-1];
reg [ADDR_WIDTH-1:0] wr_ptr;
reg [ADDR_WIDTH-1:0] rd_ptr;

localparam [ADDR_WIDTH:0] COUNT_ZERO = {ADDR_WIDTH+1{1'b0}};
localparam [ADDR_WIDTH:0] COUNT_ONE  = {{ADDR_WIDTH{1'b0}}, 1'b1};
localparam [ADDR_WIDTH:0] COUNT_DEPTH = DEPTH;

assign empty = (count == COUNT_ZERO);
assign full  = (count == COUNT_DEPTH);
assign dout  = mem[rd_ptr];

wire do_pop  = pop && !empty;
wire do_push = push && (!full || do_pop);

always @(posedge clk) begin
    if (reset || clear) begin
        wr_ptr <= {ADDR_WIDTH{1'b0}};
        rd_ptr <= {ADDR_WIDTH{1'b0}};
        count  <= COUNT_ZERO;
    end else begin
        if (do_push) begin
            mem[wr_ptr] <= din;
            wr_ptr <= wr_ptr + {{ADDR_WIDTH-1{1'b0}}, 1'b1};
        end

        if (do_pop)
            rd_ptr <= rd_ptr + {{ADDR_WIDTH-1{1'b0}}, 1'b1};

        case ({do_push, do_pop})
            2'b10: count <= count + COUNT_ONE;
            2'b01: count <= count - COUNT_ONE;
            default: count <= count;
        endcase
    end
end

endmodule

`default_nettype wire
