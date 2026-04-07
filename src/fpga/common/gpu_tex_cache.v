//
// GPU Texture Cache -- 16 KB, Direct-Mapped, 2-Stage Pipelined
//
// 1024 sets x 16 bytes/line = 16,384 bytes.
// Single tag + data RAM. Pipelined for 1 request/cycle on hits.
//
// Pipeline:
//   Stage 1 (req in):  RAM tag/data read at req_addr's set (combinational
//                      address into M10K, registered output 1 cycle later).
//                      Latch req_addr/req_wide into stage 2 register.
//   Stage 2 (resp out):rd_tag / rd_data hold the RAM result for the request
//                      that was at stage 1 last cycle (i.e. now at stage 2).
//                      Combinational hit detect, register resp_valid/resp_data.
//
// Latency: 1 cycle from req_valid+req_ready to resp_valid (cache hit).
// Throughput: 1 request/cycle on hits.
//
// On a miss the pipeline stalls (req_ready deasserted combinationally), runs
// the existing AXI burst fill, then emits resp_valid for the missed address
// and resumes pipe operation. The consumer is responsible for holding
// req_valid until req_ready is observed high — exactly the same handshake
// the prior single-request FSM required.
//

`default_nettype none

module gpu_tex_cache (
    input wire clk,
    input wire reset_n,
    input wire flush,

    input wire         req_valid,
    output wire        req_ready,
    input wire  [25:0] req_addr,
    input wire         req_wide,

    output wire        resp_valid,
    output wire [15:0] resp_data,

    output reg         axi_arvalid,
    input wire         axi_arready,
    output reg  [31:0] axi_araddr,
    output reg  [7:0]  axi_arlen,
    input wire         axi_rvalid,
    input wire  [31:0] axi_rdata,
    input wire         axi_rlast
);

//   addr[3:0]   = byte offset within 16-byte line
//   addr[13:4]  = set index (10 bits, 1024 sets)
//   addr[25:14] = tag (12 bits)
localparam SET_BITS   = 10;
localparam SETS       = 1024;
localparam TAG_BITS   = 12;
localparam LINE_WORDS = 4;

// ---- Storage ----
reg [SETS-1:0] valid;

(* ramstyle = "M10K" *) reg [TAG_BITS-1:0] tag_mem [0:SETS-1];
(* ramstyle = "M10K" *) reg [31:0] data_mem [0:SETS*LINE_WORDS-1];

// ---- FSM ----
// S_PIPE       : normal pipelined operation, 1 req/cycle accepted on hits
// S_FILL_AR    : issue AXI AR for missed line
// S_FILL_DATA  : burst fill in progress
// S_FILL_OUT   : emit response for the originally-missed addr, return to S_PIPE
localparam S_PIPE      = 3'd0;
localparam S_FILL_AR   = 3'd1;
localparam S_FILL_DATA = 3'd2;
localparam S_FILL_OUT  = 3'd3;

reg [2:0] state;

// ---- Stage 2 register: the request whose RAM read result is in rd_* now ----
reg         pipe_valid;
reg  [25:0] pipe_addr;
reg         pipe_wide;

wire [TAG_BITS-1:0] pipe_tag  = pipe_addr[25:14];
wire [SET_BITS-1:0] pipe_set  = pipe_addr[13:4];
wire [1:0]          pipe_byte = pipe_addr[1:0];

// ---- RAM read result (1-cycle latency) ----
reg [TAG_BITS-1:0] rd_tag;
reg                rd_valid;
reg [31:0]         rd_data;

// Combinational hit / miss for the request currently at stage 2.
// Only valid in S_PIPE — other states are filling and don't see pipe_valid.
wire pipe_hit  = (state == S_PIPE) && pipe_valid &&  rd_valid && (rd_tag == pipe_tag);
wire pipe_miss = (state == S_PIPE) && pipe_valid && !(rd_valid && (rd_tag == pipe_tag));

// Backpressure: ready unless a miss is being handled this cycle, the FSM
// is busy filling, or flush is asserted. The condition is purely
// combinational — the consumer must check req_ready in the same cycle it
// asserts req_valid.
assign req_ready = (state == S_PIPE) && !pipe_miss && !flush;

// ---- Combinational response (hot path) ----
// Hit responses are emitted combinationally: 1-cycle req→resp latency.
// Miss/fill responses are emitted via fill_resp_valid (1-cycle registered
// pulse) — the resp_data assign below picks the fill_target_word for that
// cycle because pipe_hit is false (state != S_PIPE during S_FILL_OUT).
reg fill_resp_valid;

assign resp_valid = pipe_hit || fill_resp_valid;

// ---- Continuous RAM read (driven by req_addr, NOT pipe_addr) ----
// The RAM reads whatever the consumer is presenting on req_addr this cycle;
// the result lands in rd_* next cycle, by which time req_addr will have been
// latched into pipe_addr. Stage 1 = address presentation; stage 2 = read use.
wire [SET_BITS-1:0] addr_set  = req_addr[13:4];
wire [1:0]          addr_word = req_addr[3:2];

always @(posedge clk) begin
    rd_tag   <= tag_mem[addr_set];
    rd_valid <= valid[addr_set];
    rd_data  <= data_mem[{addr_set, addr_word}];
end

// ---- Byte/halfword extraction from RAM hit (uses pipe_byte) ----
wire [7:0] byte_from_rd =
    (pipe_byte == 2'd0) ? rd_data[7:0]   :
    (pipe_byte == 2'd1) ? rd_data[15:8]  :
    (pipe_byte == 2'd2) ? rd_data[23:16] : rd_data[31:24];
wire [15:0] hw_from_rd = pipe_byte[1] ? rd_data[31:16] : rd_data[15:0];

// ---- Miss state (latched copy of the missed request for the fill flow) ----
reg [25:0] lat_addr;
reg        lat_wide;

wire [TAG_BITS-1:0] lat_tag  = lat_addr[25:14];
wire [SET_BITS-1:0] lat_set  = lat_addr[13:4];
wire [1:0]          lat_word = lat_addr[3:2];
wire [1:0]          lat_byte = lat_addr[1:0];

reg [1:0]  fill_beat;
reg [31:0] fill_target_word;

wire [7:0] byte_from_fill =
    (lat_byte == 2'd0) ? fill_target_word[7:0]   :
    (lat_byte == 2'd1) ? fill_target_word[15:8]  :
    (lat_byte == 2'd2) ? fill_target_word[23:16] : fill_target_word[31:24];
wire [15:0] hw_from_fill = lat_byte[1] ? fill_target_word[31:16] : fill_target_word[15:0];

// Combinational resp_data: pick hit-path or fill-path. resp_valid gates this,
// so when neither pipe_hit nor fill_resp_valid is high the value is "don't
// care" and the consumer ignores it.
assign resp_data = pipe_hit
    ? (pipe_wide ? hw_from_rd  : {8'b0, byte_from_rd})
    : (lat_wide  ? hw_from_fill : {8'b0, byte_from_fill});

// ---- Main FSM ----
always @(posedge clk) begin
    if (!reset_n) begin
        state       <= S_PIPE;
        pipe_valid  <= 0;
        pipe_addr   <= 0;
        pipe_wide   <= 0;
        fill_resp_valid <= 0;
        axi_arvalid <= 0;
        axi_araddr  <= 0;
        axi_arlen   <= 0;
        fill_beat   <= 0;
        fill_target_word <= 0;
        valid       <= {SETS{1'b0}};
        lat_addr    <= 0;
        lat_wide    <= 0;
    end else begin
        // Defaults
        fill_resp_valid <= 0;
        if (flush) valid <= {SETS{1'b0}};

        case (state)
        S_PIPE: begin
            // pipe_valid is HELD (no default clear). Hit path is fully
            // combinational (resp_valid wire above). Holding pipe_valid
            // ensures the response remains valid for the consumer to capture
            // even if the consumer is stalled by some downstream condition
            // (e.g. FB write flush) and can't issue a new request immediately.

            // Miss handling clears pipe_valid as part of entering the fill.
            if (pipe_miss) begin
                axi_arvalid <= 1;
                axi_araddr  <= {6'b0, pipe_addr[25:4], 4'b0};
                axi_arlen   <= LINE_WORDS[7:0] - 8'd1;
                fill_beat   <= 0;
                state       <= S_FILL_AR;
                lat_addr    <= pipe_addr;
                lat_wide    <= pipe_wide;
                pipe_valid  <= 0;  // explicit clear on miss
            end else if (req_valid && req_ready) begin
                // Accept a new request — overwrites the held pipe state.
                pipe_valid <= 1;
                pipe_addr  <= req_addr;
                pipe_wide  <= req_wide;
            end
            // else: hold pipe_valid (and pipe_addr) so resp_valid stays high
            // for any consumer that hasn't yet captured it.
        end

        S_FILL_AR: begin
            if (axi_arready) begin
                axi_arvalid <= 0;
                state       <= S_FILL_DATA;
            end
        end

        S_FILL_DATA: begin
            if (axi_rvalid) begin
                data_mem[{lat_set, fill_beat}] <= axi_rdata;
                if (fill_beat == lat_word) fill_target_word <= axi_rdata;
                fill_beat <= fill_beat + 2'd1;
                if (axi_rlast) begin
                    tag_mem[lat_set] <= lat_tag;
                    valid[lat_set]   <= 1'b1;
                    state <= S_FILL_OUT;
                end
            end
        end

        S_FILL_OUT: begin
            // 1-cycle pulse — combinational resp_data assign picks fill_*_word
            // since pipe_hit is false (state != S_PIPE this cycle).
            fill_resp_valid <= 1;
            state           <= S_PIPE;
        end

        default: state <= S_PIPE;
        endcase
    end
end

endmodule
