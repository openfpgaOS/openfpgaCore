//
// GPU Texture Cache -- 16 KB, Direct-Mapped, 2-Stage Pipelined, Dual Port
//
// 1024 sets x 16 bytes/line = 16,384 bytes.
// Single tag + data RAM, inferred as M10K with TDP read access (Cyclone V).
//
// Two consumer ports:
//   Port A (req_valid / req_ready / req_addr / req_wide /
//          resp_valid / resp_data)
//          — historically the texture-fetch port.  Drives the AXI fill
//          machine when port A misses.
//   Port B (req_valid_b ... resp_data_b)
//          — second read-only client (commit 2 will wire this to the
//          colormap-lookup path so palookup tables can live in SDRAM
//          and be cache-served instead of consuming a dedicated
//          16 KB cmap_bram).  Today gpu_core ties port B inputs to 0
//          and ignores its outputs; existing tests therefore exercise
//          only port A and continue to pass byte-exact.
//
// Per port:
//   Stage 1 (req in):  RAM tag/data read at req_addr's set (combinational
//                      address into M10K, registered output 1 cycle later).
//                      Latch req_addr/req_wide into stage 2 register.
//   Stage 2 (resp out):rd_tag / rd_data hold the RAM result for the request
//                      that was at stage 1 last cycle (i.e. now at stage 2).
//                      Combinational hit detect, register resp_valid/resp_data.
//
// Latency: 1 cycle from req_valid+req_ready to resp_valid (cache hit),
// per port, independent.  Both ports can hit on the same cycle: the M10K
// is configured TDP at 32 b × 256 entries (same M10K count as the prior
// SDP geometry; Cyclone V supports the dual-read pattern at no block
// cost).
//
// Throughput: 1 hit/port/cycle.  Miss handling serializes through one
// shared AXI fill machine — fixed-priority A on tie, B on the next
// quiescent cycle.  During a fill, the *issuing* port is stalled until
// S_FILL_OUT.  The *other* port can continue serving hits via the second
// M10K read port; only its req_ready drops while the FSM is in S_FILL_*
// (uniform across ports — keeps the handshake symmetric and avoids
// races between the second port's mid-fill accept and the fill's
// data_mem write).
//

`default_nettype none

module gpu_tex_cache (
    input wire clk,
    input wire reset_n,
    input wire flush,

    // Port A — texture fetch (existing interface; gpu_core wiring
    // unchanged from the SDP-era cache).
    input  wire         req_valid,
    output wire         req_ready,
    input  wire  [25:0] req_addr,
    input  wire         req_wide,

    output wire         resp_valid,
    output wire  [15:0] resp_data,

    // Port B — second read-only client (cmap read path in commit 2).
    input  wire         req_valid_b,
    output wire         req_ready_b,
    input  wire  [25:0] req_addr_b,
    input  wire         req_wide_b,

    output wire         resp_valid_b,
    output wire  [15:0] resp_data_b,

    // AXI fill (single shared)
    output reg          axi_arvalid,
    input wire          axi_arready,
    output reg  [31:0]  axi_araddr,
    output reg  [7:0]   axi_arlen,
    input wire          axi_rvalid,
    input wire  [31:0]  axi_rdata,
    input wire          axi_rlast,

    // Debug — exposed in GPU_STATUS for hang diagnosis
    output wire [2:0]  dbg_state,
    output wire        dbg_pipe_valid
);

assign dbg_state      = state;
assign dbg_pipe_valid = pipe_valid_a;

//   addr[3:0]   = byte offset within 16-byte line
//   addr[13:4]  = set index (10 bits, 1024 sets)
//   addr[25:14] = tag (12 bits)
localparam SET_BITS   = 10;
localparam SETS       = 1024;
localparam TAG_BITS   = 12;
localparam LINE_WORDS = 4;

// ---- Storage ----
// Inferred M10K, dual-port read (port A + port B).  Cyclone V infers
// TDP altsyncram from the two-always-block pattern below: each port has
// its own (clk, addr, [we], dout) triplet; the synthesizer recognises
// the pattern and emits a TDP block with the same M10K count as the
// prior SDP layout.  data_mem and tag_mem keep their write side on
// port A only (only the fill machine writes; reads via either port).
(* ramstyle = "M10K" *) reg                 valid_mem [0:SETS-1];
(* ramstyle = "M10K" *) reg [TAG_BITS-1:0]  tag_mem   [0:SETS-1];
(* ramstyle = "M10K" *) reg [31:0]          data_mem  [0:SETS*LINE_WORDS-1];

// ---- FSM ----
// S_INIT       : walk sets 0..SETS-1 writing valid_mem=0 (entered on reset
//                and on flush; M10K can't be bulk-cleared in one cycle)
// S_PIPE       : normal pipelined operation, 1 req/cycle accepted on hits
//                (per port, independently)
// S_FILL_AR    : issue AXI AR for missed line (lat_port selects which
//                port's miss is being serviced)
// S_FILL_DATA  : burst fill in progress
// S_FILL_OUT   : 1-cycle hold after axi_rlast to let the requesting
//                consumer's PRE-edge of the next clock observe
//                resp_valid_x=fill_resp_valid_x; transitions
//                unconditionally to S_PIPE the cycle after — the OLD
//                "wait for new req as ack" pattern deadlocks at
//                end-of-span when the consumer's req_valid_x goes low
//                and never returns (the last fragment's response gets
//                pinned in S_FILL_OUT forever).  By exiting on a fixed
//                1-cycle delay and re-priming pipe_*_x / rd_*_x for the
//                just-filled line, the response continues to be
//                visible as a pipe_hit_x in S_PIPE so a stalled
//                consumer can capture whenever it's ready.
localparam S_PIPE      = 3'd0;
localparam S_FILL_AR   = 3'd1;
localparam S_FILL_DATA = 3'd2;
localparam S_FILL_OUT  = 3'd3;
localparam S_INIT      = 3'd4;

reg [2:0] state;
reg [SET_BITS-1:0] init_counter;

// Pending flush: see prior (SDP) version for the full rationale.  TL;DR:
// flush is a 1-cycle pulse that can arrive any cycle; we latch and apply
// it at the next safe transition.
reg flush_pending;

// ---- Stage 2 registers — one set per port ----
reg         pipe_valid_a;
reg  [25:0] pipe_addr_a;
reg         pipe_wide_a;

reg         pipe_valid_b;
reg  [25:0] pipe_addr_b;
reg         pipe_wide_b;

wire [TAG_BITS-1:0] pipe_tag_a  = pipe_addr_a[25:14];
wire [1:0]          pipe_byte_a = pipe_addr_a[1:0];

wire [TAG_BITS-1:0] pipe_tag_b  = pipe_addr_b[25:14];
wire [1:0]          pipe_byte_b = pipe_addr_b[1:0];

// ---- RAM read result registers — one set per port ----
reg [TAG_BITS-1:0] rd_tag_a;
reg                rd_valid_a;
reg [31:0]         rd_data_a;

reg [TAG_BITS-1:0] rd_tag_b;
reg                rd_valid_b;
reg [31:0]         rd_data_b;

// ---- Combinational hit / miss per port ----
// State-independent: a hit on port X depends only on its own pipe_valid_x
// and the latched rd_*_x — those are registered before the fill starts,
// so even while the FSM is busy filling for the other port, this port's
// already-resolved response stays available.  Required for the dual-port
// pipeline: when port A misses and goes to fill, port B's pipe_hit_b
// must remain visible to its consumer (else resp_valid_b drops, the
// fragment-pipeline's cmap_pipe_wait stays asserted, and we deadlock
// even though port B's data is sitting in rd_data_b ready to deliver).
//
// The fill-trigger logic that consumes pipe_miss_x is gated on
// (state == S_PIPE) inside the S_PIPE arm of the FSM below — that's the
// only place we *act* on pipe_miss; the wires themselves are pure
// combinational facts about the latched request.
wire pipe_hit_a  = pipe_valid_a &&  rd_valid_a && (rd_tag_a == pipe_tag_a);
wire pipe_miss_a = pipe_valid_a && !(rd_valid_a && (rd_tag_a == pipe_tag_a));

wire pipe_hit_b  = pipe_valid_b &&  rd_valid_b && (rd_tag_b == pipe_tag_b);
wire pipe_miss_b = pipe_valid_b && !(rd_valid_b && (rd_tag_b == pipe_tag_b));

// lat_port: 0 = A, 1 = B.  Captured on the cycle a miss enters S_FILL_AR
// so the fill response is routed to the correct port.
reg lat_port;

// ---- Backpressure ----
// Per-port req_ready: ready in S_PIPE when no miss and no flush;
// ALSO ready in S_FILL_OUT when serving this port — that lets the
// consumer's first post-fill request roll into S_PIPE without an
// extra latch cycle (the M10K read fires on accept and rd_*_x
// updates correctly even though the FSM transitions S_FILL_OUT →
// S_PIPE simultaneously).
assign req_ready   = ((state == S_PIPE) && !pipe_miss_a && !flush)
                  || ((state == S_FILL_OUT) && (lat_port == 1'b0) && !flush);
assign req_ready_b = ((state == S_PIPE) && !pipe_miss_b && !flush)
                  || ((state == S_FILL_OUT) && (lat_port == 1'b1) && !flush);

// ---- Combinational responses (hot path) ----
// Pipe-hit OR S_FILL_OUT held response.  fill_resp_valid_x is set at
// axi_rlast and held through the 1-cycle S_FILL_OUT window so the
// stalled consumer can still observe the response if it didn't shift
// at the exact landing cycle.
reg fill_resp_valid_a, fill_resp_valid_b;
assign resp_valid   = pipe_hit_a || fill_resp_valid_a;
assign resp_valid_b = pipe_hit_b || fill_resp_valid_b;

// ---- Pipelined RAM read — gated per-port on accept ----
// Same gate-on-accept rationale as the SDP-era cache (without it, rd_*
// drift to the next pixel's address while pipe_addr is still the prior
// pixel — produces glitched bytes when the consumer is mid-stall).
wire [SET_BITS-1:0] addr_set_a  = req_addr[13:4];
wire [1:0]          addr_word_a = req_addr[3:2];

wire [SET_BITS-1:0] addr_set_b  = req_addr_b[13:4];
wire [1:0]          addr_word_b = req_addr_b[3:2];

// Single-writer rule: each rd_*_x register is written by exactly
// one always block (this one), so Quartus is happy with M10K SDP
// inference and there's no multi-driver synthesis error.  Two cases:
//
//   1. req_valid_x && req_ready_x — new request accepted on this
//      port: latch the M10K read of (addr_set_x, addr_word_x).
//      Standard SDP read-with-enable pattern.
//
//   2. else, exiting S_FILL_OUT on the no-ack path for this port:
//      no consumer accept this cycle, so prime rd_*_x with the
//      just-filled line (lat_tag + fill_target_word).  Mirrors
//      the FSM's pipe_*_x <= lat_*_x update on the same posedge,
//      so resp_valid_x stays alive in S_PIPE next cycle as a
//      pipe_hit_x — exactly the timing the original consumer code
//      expects, no extra cycle of stall.
wire prime_a = (state == S_FILL_OUT) && (lat_port == 1'b0)
            && !flush && !flush_pending
            && !(req_valid && req_ready);
wire prime_b = (state == S_FILL_OUT) && (lat_port == 1'b1)
            && !flush && !flush_pending
            && !(req_valid_b && req_ready_b);

always @(posedge clk) begin
    if (req_valid && req_ready) begin
        rd_tag_a   <= tag_mem[addr_set_a];
        rd_valid_a <= valid_mem[addr_set_a];
        rd_data_a  <= data_mem[{addr_set_a, addr_word_a}];
    end else if (prime_a) begin
        rd_tag_a   <= lat_tag;
        rd_valid_a <= 1'b1;
        rd_data_a  <= fill_target_word;
    end
end

always @(posedge clk) begin
    if (req_valid_b && req_ready_b) begin
        rd_tag_b   <= tag_mem[addr_set_b];
        rd_valid_b <= valid_mem[addr_set_b];
        rd_data_b  <= data_mem[{addr_set_b, addr_word_b}];
    end else if (prime_b) begin
        rd_tag_b   <= lat_tag;
        rd_valid_b <= 1'b1;
        rd_data_b  <= fill_target_word;
    end
end

// ---- Byte/halfword extraction per port ----
wire [7:0] byte_from_rd_a =
    (pipe_byte_a == 2'd0) ? rd_data_a[7:0]   :
    (pipe_byte_a == 2'd1) ? rd_data_a[15:8]  :
    (pipe_byte_a == 2'd2) ? rd_data_a[23:16] : rd_data_a[31:24];
wire [15:0] hw_from_rd_a = pipe_byte_a[1] ? rd_data_a[31:16] : rd_data_a[15:0];

wire [7:0] byte_from_rd_b =
    (pipe_byte_b == 2'd0) ? rd_data_b[7:0]   :
    (pipe_byte_b == 2'd1) ? rd_data_b[15:8]  :
    (pipe_byte_b == 2'd2) ? rd_data_b[23:16] : rd_data_b[31:24];
wire [15:0] hw_from_rd_b = pipe_byte_b[1] ? rd_data_b[31:16] : rd_data_b[15:0];

// ---- Miss state (latched copy of the missed request, single shared) ----
// lat_port selects which port owns the in-flight fill.  At axi_rlast the
// fill machine writes lat_addr / lat_wide back into the requesting port's
// pipe_*_x and rd_*_x, then transitions to S_PIPE; the response is then
// served as a normal pipe_hit_x and resp_data_x is computed from rd_*_x.
reg [25:0] lat_addr;
reg        lat_wide;

wire [TAG_BITS-1:0] lat_tag  = lat_addr[25:14];
wire [SET_BITS-1:0] lat_set  = lat_addr[13:4];
wire [1:0]          lat_word = lat_addr[3:2];

reg [1:0]  fill_beat;
reg [31:0] fill_target_word;

// ---- Combinational resp_data per port ----
// Hit-path uses rd_*; S_FILL_OUT path uses fill_target_word (the word
// captured at lat_word offset during the burst).
wire [7:0] byte_from_fill =
    (lat_addr[1:0] == 2'd0) ? fill_target_word[7:0]   :
    (lat_addr[1:0] == 2'd1) ? fill_target_word[15:8]  :
    (lat_addr[1:0] == 2'd2) ? fill_target_word[23:16] : fill_target_word[31:24];
wire [15:0] hw_from_fill = lat_addr[1] ? fill_target_word[31:16] : fill_target_word[15:0];

assign resp_data   = pipe_hit_a
    ? (pipe_wide_a ? hw_from_rd_a : {8'b0, byte_from_rd_a})
    : (lat_wide    ? hw_from_fill : {8'b0, byte_from_fill});
assign resp_data_b = pipe_hit_b
    ? (pipe_wide_b ? hw_from_rd_b : {8'b0, byte_from_rd_b})
    : (lat_wide    ? hw_from_fill : {8'b0, byte_from_fill});

// ---- Main FSM ----
always @(posedge clk) begin
    if (!reset_n) begin
        state       <= S_INIT;
        init_counter <= 0;
        pipe_valid_a <= 0;
        pipe_addr_a  <= 0;
        pipe_wide_a  <= 0;
        pipe_valid_b <= 0;
        pipe_addr_b  <= 0;
        pipe_wide_b  <= 0;
        fill_resp_valid_a <= 0;
        fill_resp_valid_b <= 0;
        axi_arvalid <= 0;
        axi_araddr  <= 0;
        axi_arlen   <= 0;
        fill_beat   <= 0;
        fill_target_word <= 0;
        lat_addr    <= 0;
        lat_wide    <= 0;
        lat_port    <= 0;
        flush_pending <= 1'b0;
    end else begin
        // Latch any flush pulse regardless of state.  Cleared when we
        // enter S_INIT below.
        if (flush)
            flush_pending <= 1'b1;

        case (state)
        // ----------------------------------------------------------------
        // S_INIT: walk through 1024 sets writing valid_mem[i] <= 0.
        // Entered on reset and on flush.  Both ports' req_ready are 0
        // because they only assert in S_PIPE / S_FILL_OUT.
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
            // pipe_valid_x is HELD (no default clear).  Hit path is
            // fully combinational (resp_valid_x wires above).  Holding
            // the per-port pipe_valid ensures the response remains
            // valid for a stalled consumer to capture.

            // Miss arbitration — fixed priority A on tie.  Rationale:
            //   * Texture reads issue earlier in the fragment pipeline
            //     (p1) than the cmap reads that will eventually hit
            //     port B (p2/p2b).  Servicing A first keeps pipeline
            //     forward progress — B's miss waits at most one fill
            //     duration (~5 cycles) before its turn.
            //   * Starvation analysis: both ports stream from
            //     monotonically-increasing addresses within a span,
            //     so back-to-back A-miss / B-miss alternation requires
            //     simultaneous cold-line entries on both axes — rare
            //     enough that strict priority is observably as fair
            //     as round-robin in the cmap-cache sim.  If a future
            //     trace shows starvation, swap to alternating
            //     (lat_port_round_robin) — same M10K, ~10 ALMs.
            //
            // Same flush priority story as the SDP version: an
            // in-flight miss must run to completion before flush
            // walk-clears valid_mem; otherwise the consumer waits
            // forever on a tex_resp_valid that gets dropped mid-fill.
            if (pipe_miss_a) begin
                axi_arvalid <= 1;
                axi_araddr  <= {6'b0, pipe_addr_a[25:4], 4'b0};
                axi_arlen   <= LINE_WORDS[7:0] - 8'd1;
                fill_beat   <= 0;
                state       <= S_FILL_AR;
                lat_addr    <= pipe_addr_a;
                lat_wide    <= pipe_wide_a;
                lat_port    <= 1'b0;       // serving A
                pipe_valid_a <= 0;          // explicit clear on miss
            end else if (pipe_miss_b) begin
                axi_arvalid <= 1;
                axi_araddr  <= {6'b0, pipe_addr_b[25:4], 4'b0};
                axi_arlen   <= LINE_WORDS[7:0] - 8'd1;
                fill_beat   <= 0;
                state       <= S_FILL_AR;
                lat_addr    <= pipe_addr_b;
                lat_wide    <= pipe_wide_b;
                lat_port    <= 1'b1;       // serving B
                pipe_valid_b <= 0;          // explicit clear on miss
            end else if (flush || flush_pending) begin
                // Safe to flush now: no pending miss on either port
                // means either pipe_valid_x=0 (no in-flight request)
                // or pipe_hit_x=1 (response is being served
                // combinationally this cycle — consumer's shift
                // captures it on the same posedge as our state
                // change).
                state <= S_INIT;
                init_counter <= 0;
                pipe_valid_a <= 0;
                pipe_valid_b <= 0;
            end else begin
                // Both ports independently accept new requests on hit
                // cycles.  A and B are unrelated for accept purposes —
                // both gates check their own req_valid_x && req_ready_x.
                if (req_valid && req_ready) begin
                    pipe_valid_a <= 1;
                    pipe_addr_a  <= req_addr;
                    pipe_wide_a  <= req_wide;
                end
                if (req_valid_b && req_ready_b) begin
                    pipe_valid_b <= 1;
                    pipe_addr_b  <= req_addr_b;
                    pipe_wide_b  <= req_wide_b;
                end
            end
            // else: hold pipe_valid_x (and pipe_addr_x) so resp_valid_x
            // stays high for any consumer that hasn't yet captured it.
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
                    if (lat_port == 1'b0) fill_resp_valid_a <= 1;
                    else                  fill_resp_valid_b <= 1;
                    state                <= S_FILL_OUT;
                end
            end
        end

        S_FILL_OUT: begin
            // Auto-transition to S_PIPE after a 1-cycle hold so end-of-
            // span doesn't deadlock waiting for an ack-via-new-req.  In
            // the same cycle, prime the requesting port's stage-2 regs
            // so resp_valid_x continues to be served as a pipe_hit_x in
            // S_PIPE — a stalled consumer that didn't capture the held
            // fill_resp_valid_x in time still sees the response.
            //
            // If the consumer DID issue a new req this cycle (the
            // common case during steady-state pipelined operation):
            // req_ready_x is high (S_FILL_OUT && lat_port == X), so the
            // M10K-read latch fires and updates rd_*_x to the new
            // addr's data.  pipe_*_x captures the new req's address.
            // The held fill_resp_valid_x for the prior addr was already
            // observed at PRE-edge of this cycle, so the consumer's
            // shift captured it; the latch's new rd_*_x then becomes
            // the basis of next cycle's pipe_hit_x.
            //
            // If no new req: the prime sets pipe_*_x to lat_addr and
            // rd_*_x to the just-filled line's word containing the
            // requested byte.  Next cycle's pipe_hit_x carries the
            // response forward in S_PIPE.
            if (flush || flush_pending) begin
                state <= S_INIT;
                init_counter <= 0;
                fill_resp_valid_a <= 0;
                fill_resp_valid_b <= 0;
                pipe_valid_a <= 0;
                pipe_valid_b <= 0;
            end else begin
                // Whether the consumer issued a new req this cycle or
                // not, drive pipe_*_x for the requesting port.  In the
                // ack-via-new-req case (req_valid && req_ready), pipe_*_x
                // tracks the new addr and rd_*_x is updated by the
                // separate latch always block on the same posedge.  In
                // the no-ack case, pipe_*_x re-points to lat_addr and
                // rd_*_x is primed with the just-filled line so the
                // response continues to be served as a pipe_hit_x in
                // S_PIPE.
                // pipe_*_x is a single-writer reg (this FSM block);
                // rd_*_x is updated by the M10K-read latch above using
                // the prime_a / prime_b combinational signals which
                // mirror the conditions here.  Single posedge T sees
                // both pipe_*_x and rd_*_x land at their correct
                // POST-edge values.
                if (lat_port == 1'b0) begin
                    fill_resp_valid_a <= 0;
                    if (req_valid && req_ready) begin
                        pipe_valid_a <= 1;
                        pipe_addr_a  <= req_addr;
                        pipe_wide_a  <= req_wide;
                    end else begin
                        pipe_valid_a <= 1;
                        pipe_addr_a  <= lat_addr;
                        pipe_wide_a  <= lat_wide;
                    end
                end else begin
                    fill_resp_valid_b <= 0;
                    if (req_valid_b && req_ready_b) begin
                        pipe_valid_b <= 1;
                        pipe_addr_b  <= req_addr_b;
                        pipe_wide_b  <= req_wide_b;
                    end else begin
                        pipe_valid_b <= 1;
                        pipe_addr_b  <= lat_addr;
                        pipe_wide_b  <= lat_wide;
                    end
                end
                state <= S_PIPE;
            end
        end

        default: state <= S_PIPE;
        endcase
    end
end

endmodule
