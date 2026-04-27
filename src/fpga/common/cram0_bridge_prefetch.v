`default_nettype none

module cram0_bridge_prefetch (
    input  wire        clk,
    input  wire        reset_n,

    input  wire        start,
    input  wire [31:0] start_bridge_addr,
    input  wire [31:0] start_length,
    input  wire        bridge_owner,

    input  wire        bridge_rd_pulse,
    input  wire [31:0] bridge_addr,
    output wire        bridge_hit,
    output wire [31:0] bridge_rd_data,

    output wire        active,
    output wire        busy,

    output reg         burst_rd,
    output reg  [21:0] burst_addr,
    output reg  [5:0]  burst_len,
    input  wire [31:0] burst_rdata,
    input  wire        burst_rdata_valid,
    input  wire        ctrl_busy
);

localparam [6:0] PREFETCH_DEPTH = 7'd64;
localparam [6:0] BURST_WORDS    = 7'd16;

reg [31:0] buffer [0:63];

reg        active_r;
reg        fetching;
reg        fetch_seen_busy;
reg [21:0] expected_word;
reg [21:0] next_word;
reg [21:0] end_word;
reg [5:0]  rd_ptr;
reg [5:0]  wr_ptr;
reg [6:0]  fill_count;

wire [21:0] bridge_word = bridge_addr[23:2];
wire [21:0] start_word = start_bridge_addr[23:2];
wire [21:0] start_len_words =
    start_length[23:2] + {{21{1'b0}}, |start_length[1:0]};

wire [6:0]  free_words = PREFETCH_DEPTH - fill_count;
wire [21:0] remaining_words = end_word - next_word;
wire [6:0]  fetch_words =
    (remaining_words > {15'd0, BURST_WORDS}) ? BURST_WORDS
                                             : {1'b0, remaining_words[5:0]};

wire can_fetch = active_r
               && bridge_owner
               && !fetching
               && !ctrl_busy
               && (remaining_words != 22'd0)
               && (fetch_words != 7'd0)
               && (free_words >= fetch_words);

assign active = active_r;
assign busy = fetching | burst_rd;
assign bridge_hit = active_r
                  && (bridge_addr[31:24] == 8'h20)
                  && (bridge_word == expected_word)
                  && (fill_count != 7'd0);
assign bridge_rd_data = buffer[rd_ptr];

wire do_consume = bridge_rd_pulse && bridge_hit;
wire do_fill = fetching && burst_rdata_valid;
wire consume_last = do_consume && ((expected_word + 22'd1) >= end_word);

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        active_r <= 1'b0;
        fetching <= 1'b0;
        fetch_seen_busy <= 1'b0;
        expected_word <= 22'd0;
        next_word <= 22'd0;
        end_word <= 22'd0;
        rd_ptr <= 6'd0;
        wr_ptr <= 6'd0;
        fill_count <= 7'd0;
        burst_rd <= 1'b0;
        burst_addr <= 22'd0;
        burst_len <= 6'd0;
    end else begin
        burst_rd <= 1'b0;

        if (start) begin
            active_r <= (start_bridge_addr[31:24] == 8'h20)
                     && (start_len_words != 22'd0);
            fetching <= 1'b0;
            fetch_seen_busy <= 1'b0;
            expected_word <= start_word;
            next_word <= start_word;
            end_word <= start_word + start_len_words;
            rd_ptr <= 6'd0;
            wr_ptr <= 6'd0;
            fill_count <= 7'd0;
        end else begin
            if (do_fill) begin
                buffer[wr_ptr] <= burst_rdata;
                wr_ptr <= wr_ptr + 6'd1;
            end

            if (do_consume) begin
                rd_ptr <= rd_ptr + 6'd1;
                expected_word <= expected_word + 22'd1;
            end

            case ({do_fill, do_consume})
            2'b10: fill_count <= fill_count + 7'd1;
            2'b01: fill_count <= fill_count - 7'd1;
            default: fill_count <= fill_count;
            endcase

            if (consume_last)
                active_r <= 1'b0;

            if (can_fetch) begin
                burst_rd <= 1'b1;
                burst_addr <= next_word;
                burst_len <= fetch_words[5:0] - 6'd1;
                next_word <= next_word + {16'd0, fetch_words[5:0]};
                fetching <= 1'b1;
                fetch_seen_busy <= 1'b0;
            end else if (fetching) begin
                if (ctrl_busy)
                    fetch_seen_busy <= 1'b1;
                else if (fetch_seen_busy)
                    fetching <= 1'b0;
            end
        end
    end
end

endmodule
