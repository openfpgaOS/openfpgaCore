// Private os30 raw-float experiment: ordered ring transport only.
// The arithmetic/parser owns command boundaries. Legacy words commit as they
// transfer; a raw triangle's at-most-29 output words commit together. A
// 64-word queue can always finish that transaction after older work drains.
`default_nettype none
module geometry_raw_float_ring #(
    parameter ADDR_BITS = 12,
    parameter QUEUE_BITS = 6
) (
    input wire clk, reset_n, enable, soft_reset, ring_reset,
    input wire [ADDR_BITS-1:0] input_wrptr,
    output reg [ADDR_BITS-1:0] input_rdptr,
    output wire [ADDR_BITS-1:0] input_addr,
    input wire [31:0] input_data,
    output wire cmd_valid,
    output wire [31:0] cmd_data,
    input wire cmd_ready,
    input wire result_valid,
    input wire [31:0] result_data,
    output wire result_ready,
    input wire result_commit, result_abort,
    input wire processor_busy,
    input wire [ADDR_BITS-1:0] output_rdptr, output_addr,
    output reg [31:0] output_data,
    output reg [ADDR_BITS-1:0] output_wrptr,
    output wire busy
);
localparam [ADDR_BITS:0] CAPACITY = (1 << QUEUE_BITS)-1;
reg [ADDR_BITS-1:0] append_ptr;
(* ramstyle = "M10K, no_rw_check" *) reg [31:0] queue [0:(1 << QUEUE_BITS)-1];
wire active = enable && reset_n && !soft_reset && !ring_reset;
wire [ADDR_BITS-1:0] used_words = append_ptr - output_rdptr;
wire [ADDR_BITS:0] free_words = CAPACITY - {1'b0, used_words};
assign cmd_valid = active && input_rdptr != input_wrptr;
assign cmd_data = input_data;
wire take_input = cmd_valid && cmd_ready;
// External ring BRAM is synchronous. Look ahead on consumption, exactly as
// the retained worker does, so adjacent input words can transfer every cycle.
assign input_addr = input_rdptr + {{(ADDR_BITS-1){1'b0}}, take_input};
assign result_ready = active && !result_abort && free_words != 0;
wire take_result = result_valid && result_ready;
wire [ADDR_BITS-1:0] next_append = append_ptr + {{(ADDR_BITS-1){1'b0}}, take_result};
assign busy = processor_busy || input_rdptr != input_wrptr
            || append_ptr != output_wrptr || output_wrptr != output_rdptr;

always @(posedge clk) begin
    output_data <= queue[output_addr[QUEUE_BITS-1:0]];
    if (take_result)
        queue[append_ptr[QUEUE_BITS-1:0]] <= result_data;
    if (!reset_n || soft_reset || ring_reset) begin
        input_rdptr <= reset_n && soft_reset && !ring_reset ? input_wrptr : 0;
        append_ptr <= 0;
        output_wrptr <= 0;
    end else begin
        if (take_input)
            input_rdptr <= input_rdptr + 1'b1;
        // Commit/abort are control acknowledgements for words already
        // accepted. Honour them while disabled too: an enable transition
        // after the last word must not lose the processor's completion.
        // A commit accompanying a data word must qualify that word's actual
        // valid/ready handshake; a delayed commit may pulse independently.
        if (result_abort) begin
            // Never rewind already-visible legacy words or earlier triangles.
            append_ptr <= output_wrptr;
        end else begin
            if (take_result)
                append_ptr <= next_append;
            if (result_commit)
                output_wrptr <= next_append;
        end
    end
end
endmodule
`default_nettype wire
