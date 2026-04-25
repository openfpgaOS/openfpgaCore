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
    input wire         axi_rlast,

    // Debug — exposed in GPU_STATUS for hang diagnosis
    output wire [2:0]  dbg_state,
    output wire        dbg_pipe_valid
);

assign dbg_state      = state;
assign dbg_pipe_valid = pipe_valid;

//   addr[3:0]   = byte offset within 16-byte line
//   addr[13:4]  = set index (10 bits, 1024 sets)
//   addr[25:14] = tag (12 bits)
localparam SET_BITS   = 10;
localparam SETS       = 1024;
localparam TAG_BITS   = 12;
localparam LINE_WORDS = 4;

// ---- Storage ----
// Valid bits were previously `reg [SETS-1:0] valid` — 1024 flip-flops
// with a 10-level fabric mux from `valid[addr_set]` into `rd_valid`.
// That mux plus the carry out of the upstream tex-addr adder put
// `tx_mul_q[0] → rd_valid` at -0.764 ns on the 100 MHz domain.
// Moved to an M10K-backed 1024×1 RAM: one block, hardware-native read
// path, and Quartus can reclaim ~256 ALMs the FF array was using.
// Reset/flush walk through all 1024 sets via the new S_INIT state.
(* ramstyle = "M10K" *) reg valid_mem [0:SETS-1];

(* ramstyle = "M10K" *) reg [TAG_BITS-1:0] tag_mem [0:SETS-1];
(* ramstyle = "M10K" *) reg [31:0] data_mem [0:SETS*LINE_WORDS-1];

// ---- FSM ----
// S_INIT       : walk sets 0..SETS-1 writing valid_mem=0 (entered on reset
//                and on flush; M10K can't be bulk-cleared in one cycle)
// S_PIPE       : normal pipelined operation, 1 req/cycle accepted on hits
// S_FILL_AR    : issue AXI AR for missed line
// S_FILL_DATA  : burst fill in progress
// S_FILL_OUT   : emit response for the originally-missed addr, return to S_PIPE
localparam S_PIPE      = 3'd0;
localparam S_FILL_AR   = 3'd1;
localparam S_FILL_DATA = 3'd2;
localparam S_FILL_OUT  = 3'd3;
localparam S_INIT      = 3'd4;

reg [2:0] state;
reg [SET_BITS-1:0] init_counter;

// Pending flush: `flush` is a 1-cycle CPU-generated pulse (from MMIO
// GPU_TEX_FLUSH).  It arrives any cycle regardless of our FSM state.
// If flush fires while we're in S_FILL_AR / S_FILL_DATA / S_FILL_OUT,
// we can't immediately walk-clear valid_mem (we'd lose the in-flight
// AXI burst).  Latch it and handle it on the next transition back to
// S_PIPE.  Cleared when the flush actually kicks off the S_INIT walk.
reg flush_pending;

// ---- Stage 2 register: the request whose RAM read result is in rd_* now ----
reg         pipe_valid;
reg  [25:0] pipe_addr;
reg         pipe_wide;

wire [TAG_BITS-1:0] pipe_tag  = pipe_addr[25:14];
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
//
// Also ready in S_FILL_OUT: while we're holding a fill response, a new
// request from the consumer is the implicit ack that the response was
// captured, and we can immediately roll the new request into pipe_addr
// (it'll resolve as a hit on the freshly-filled line, or trigger a new
// fill, on the next S_PIPE cycle).
assign req_ready = ((state == S_PIPE) && !pipe_miss && !flush)
                || ((state == S_FILL_OUT) && !flush);

// ---- Combinational response (hot path) ----
// Hit responses are emitted combinationally: 1-cycle req→resp latency.
// Miss/fill responses are emitted via fill_resp_valid which is now HELD
// (not pulsed) until the consumer captures it (signalled by it issuing
// a new req_valid). This is required because the consumer's pipeline can
// be stalled by an unrelated downstream condition (e.g. FB write flush
// in fbss != IDLE) on the exact cycle a fill completes — pulsing the
// response for one cycle would silently drop it and deadlock the pipe.
reg fill_resp_valid;

assign resp_valid = pipe_hit || fill_resp_valid;

// ---- Pipelined RAM read (latched only when consumer's req is accepted) ----
// The RAM reads from the address the consumer is presenting; the result
// lands in rd_* on the next clock, by which time req_addr has been
// latched into pipe_addr.  Stage 1 = address presentation; stage 2 = read
// use.
//
// Critical: gate the latch on req_valid && req_ready.  Without the gate,
// rd_* track req_addr every cycle.  When the consumer's pipeline stalls
// (e.g., FBSS in mid-write), pipe_addr is held but the consumer's
// req_addr can drift to the NEXT pixel (because the GPU's p0 has already
// advanced).  rd_* would re-latch from the new address while pipe_addr
// still names the old one, so byte_from_rd would mis-select bytes.
// Symptom on tb_gpu_floor_span: cache hits returned glitched bytes from
// the wrong line whenever the FBSS sub-FSM stalled the pipe between
// accept and capture.  Gating on accept holds rd_* stable while
// pipe_addr is stable.
wire [SET_BITS-1:0] addr_set  = req_addr[13:4];
wire [1:0]          addr_word = req_addr[3:2];

always @(posedge clk) begin
    if (req_valid && req_ready) begin
        rd_tag   <= tag_mem[addr_set];
        rd_valid <= valid_mem[addr_set];
        rd_data  <= data_mem[{addr_set, addr_word}];
    end
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
        state       <= S_INIT;
        init_counter <= 0;
        pipe_valid  <= 0;
        pipe_addr   <= 0;
        pipe_wide   <= 0;
        fill_resp_valid <= 0;
        axi_arvalid <= 0;
        axi_araddr  <= 0;
        axi_arlen   <= 0;
        fill_beat   <= 0;
        fill_target_word <= 0;
        lat_addr    <= 0;
        lat_wide    <= 0;
        flush_pending <= 1'b0;
    end else begin
        // Latch any flush pulse regardless of state.  Cleared when we
        // enter S_INIT below.
        if (flush)
            flush_pending <= 1'b1;

        case (state)
        // ----------------------------------------------------------------
        // S_INIT: walk through 1024 sets writing valid_mem[i] <= 0.
        // Entered on reset and on flush (flush can't bulk-clear an M10K
        // the way the old FF array allowed). req_ready is already false
        // in S_INIT because it only asserts in S_PIPE and S_FILL_OUT.
        // 1024 cycles ≈ 10 µs at 100 MHz — negligible at boot/flush.
        // ----------------------------------------------------------------
        S_INIT: begin
            valid_mem[init_counter] <= 1'b0;
            if (init_counter == {SET_BITS{1'b1}}) begin
                state <= S_PIPE;
                flush_pending <= 1'b0;  // walk-clear done, clear pending
            end
            init_counter <= init_counter + 1'b1;
        end

        S_PIPE: begin
            // pipe_valid is HELD (no default clear). Hit path is fully
            // combinational (resp_valid wire above). Holding pipe_valid
            // ensures the response remains valid for the consumer to capture
            // even if the consumer is stalled by some downstream condition
            // (e.g. FB write flush) and can't issue a new request immediately.

            // Priority order matters here.  Original code had `flush ||
            // flush_pending` first, which would silently drop an in-flight
            // miss request (consumer just issued, pipe_valid=1, miss
            // detected this cycle): the flush branch yanks pipe_valid <= 0
            // and transitions to S_INIT before the miss-fill path runs.
            // Consumer's fragment pipeline is left waiting on a
            // tex_resp_valid that never comes — fp_pipe_stall stays high,
            // p1_valid holds, the span never retires, the fence after it
            // never advances, of_gpu_finish() spins forever.  Symptom on
            // hardware: BUILD/Duke3D loadtile() flushed the cache between
            // frames without first fencing the prior frame's spans, the
            // GPU froze on the next of_gpu_finish().
            //
            // Fix: pipe_miss takes priority over flush.  An in-flight miss
            // runs through fill normally; flush_pending stays sticky and
            // fires on the trip back through S_PIPE (or via the new
            // S_FILL_OUT fast-exit added below).
            if (pipe_miss) begin
                axi_arvalid <= 1;
                axi_araddr  <= {6'b0, pipe_addr[25:4], 4'b0};
                axi_arlen   <= LINE_WORDS[7:0] - 8'd1;
                fill_beat   <= 0;
                state       <= S_FILL_AR;
                lat_addr    <= pipe_addr;
                lat_wide    <= pipe_wide;
                pipe_valid  <= 0;  // explicit clear on miss
            end else if (flush || flush_pending) begin
                // Safe to flush now: pipe_miss=0 means either pipe_valid=0
                // (no in-flight request) or pipe_hit=1 (response is being
                // served combinationally this cycle — consumer's shift
                // captures it on the same posedge as our state change).
                state <= S_INIT;
                init_counter <= 0;
                fill_resp_valid <= 0;
                pipe_valid <= 0;
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
                    tag_mem[lat_set]     <= lat_tag;
                    valid_mem[lat_set]   <= 1'b1;
                    fill_resp_valid      <= 1;
                    state                <= S_FILL_OUT;
                end
            end
        end

        S_FILL_OUT: begin
            // Hold fill_resp_valid + fill_target_word until the consumer
            // issues a new request (the implicit ack). The combinational
            // resp_data path picks fill_*_word here because pipe_hit is
            // false (state != S_PIPE while we're in S_FILL_OUT).
            //
            // While we wait, the RAM read of req_addr's set is happening
            // continuously in the unconditional always block at the top
            // of the file — by the time we transition to S_PIPE on the
            // cycle the consumer accepts, rd_tag/rd_data are already the
            // values for the new req_addr.
            //
            // Flush fast-exit: if a flush is pending, transition straight
            // to S_INIT instead of waiting for the consumer's next
            // request.  fill_resp_valid was high entering this state, so
            // the consumer's fragment shift captured the response on its
            // !fp_pipe_stall path the same cycle.  Routing through S_PIPE
            // first (the original path) would race the flush_pending
            // branch and drop the consumer's NEW request.  Bypass S_PIPE.
            if (flush || flush_pending) begin
                state <= S_INIT;
                init_counter <= 0;
                fill_resp_valid <= 0;
            end else if (req_valid && req_ready) begin
                pipe_valid      <= 1;
                pipe_addr       <= req_addr;
                pipe_wide       <= req_wide;
                fill_resp_valid <= 0;
                state           <= S_PIPE;
            end
        end

        default: state <= S_PIPE;
        endcase
    end
end

endmodule
