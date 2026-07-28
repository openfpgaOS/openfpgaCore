//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

//
// GPU Texture Cache -- Direct-Mapped, 2-Stage Pipelined, Dual Port
//
// SET_BITS parameterizes the size: 2^SET_BITS sets x 16 bytes/line.
// Default SET_BITS=10 = 16 KB (the Pocket geometry, unchanged); MiSTer
// passes 11 = 32 KB (no CRAM1 fast-tex chip there, so every texel and
// cmap read is SDRAM-backed through this cache).
// M10K sizing caveat: the dual-read data RAM is REPLICATED by the fitter
// (one full copy per read port), so block cost grows at 2x the logical
// bits — 10=~36 blocks, 11=~70, 12=~140 (the 64 KB point overflowed the
// MiSTer A6 alongside a 256 KB D$: 632/553 blocks).
// Single tag + data RAM, inferred as M10K with TDP read access (Cyclone V).
//
// Two consumer ports:
//   Port A (req_valid / req_ready / req_addr / req_wide /
//          resp_valid / resp_data)
//          — historically the texture-fetch port.  Drives the AXI fill
//          machine when port A misses.
//   Port B (req_valid_b ... resp_data_b)
//          — the live colormap/palookup read client.  gpu_core drives
//          this port with the cmap read path so palookup tables live in
//          SDRAM and are cache-served instead of consuming a dedicated
//          16 KB cmap_bram.
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

module gpu_tex_cache #(
    parameter SET_BITS = 10   // 2^SET_BITS sets x 16 B/line; 10 = 16 KB
) (
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

    // Port B — active cmap/palookup read port (driven by gpu_core).
    input  wire         req_valid_b,
    output wire         req_ready_b,
    input  wire  [25:0] req_addr_b,
    input  wire         req_wide_b,

    output wire         resp_valid_b,
    output wire  [15:0] resp_data_b,

    // Port B response-queue handshake.  Port B holds up to TWO accepted,
    // unconsumed responses (a 1-deep "held" skid in front of the pipe
    // slot) so a new request can be accepted while the previous response
    // is still being presented to a stalled consumer.  resp_valid_b /
    // resp_data_b always present the OLDEST unconsumed response;
    // resp_pop_b is pulsed by the consumer on the cycle it captures that
    // response (the fragment pipe's p2b→p3 shift).  resp_flush_b drops
    // all port-B response bookkeeping (GPU soft reset mid-span).
    input  wire         resp_pop_b,
    input  wire         resp_flush_b,

    // AXI fill (single shared)
    output reg          axi_arvalid,
    input wire          axi_arready,
    output reg  [31:0]  axi_araddr,
    output reg  [7:0]   axi_arlen,
    input wire          axi_rvalid,
    input wire  [31:0]  axi_rdata,
    input wire          axi_rlast
);

//   addr[3:0]           = byte offset within 16-byte line
//   addr[TAG_LO-1:4]    = set index (SET_BITS bits)
//   addr[25:TAG_LO]     = tag
localparam SETS       = 1 << SET_BITS;
localparam TAG_LO     = 4 + SET_BITS;
localparam TAG_BITS   = 26 - TAG_LO;
localparam LINE_WORDS = 4;

// ---- Storage ----
// Inferred M10K, dual-port read (port A + port B).  Cyclone V infers
// TDP altsyncram from the two-always-block pattern below: each port has
// its own (clk, addr, [we], dout) triplet; the synthesizer recognises
// the pattern and emits a TDP block with the same M10K count as the
// prior SDP layout.  data_mem and tagv_mem keep their write side on
// port A only (only the fill machine writes; reads via either port).
//
// Valid is packed into the tag word so the 1024x1 valid array does not
// consume a whole M10K on its own.  Layout: {valid, tag[TAG_BITS-1:0]}.
(* ramstyle = "M10K" *) reg [TAG_BITS:0]    tagv_mem  [0:SETS-1];
(* ramstyle = "M10K" *) reg [31:0]          data_mem  [0:SETS*LINE_WORDS-1];

// ---- FSM ----
// S_INIT       : walk sets 0..SETS-1 writing tagv_mem.valid=0 (entered on reset
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

reg [2:0] state /*verilator public_flat_rd*/;
reg [SET_BITS-1:0] init_counter;

// Keep separate state-decode copies for the hot ready/prime paths.  The
// fitter report shows the synthesized state-derived cache enable as a
// very high-fanout route-heavy net; splitting the decode users gives
// Quartus legal duplication points without changing cache timing.
(* keep, syn_keep, maxfan = 64 *) wire state_pipe_ready     = (state == S_PIPE);
(* keep, syn_keep, maxfan = 64 *) wire state_fillout_ready  = (state == S_FILL_OUT);
(* keep, syn_keep, maxfan = 64 *) wire state_fillout_prime  = (state == S_FILL_OUT);

// Pending flush: flush is a 1-cycle pulse that can arrive any cycle; we
// latch it and apply it at the next safe transition.
reg flush_pending;
wire flush_block = flush || flush_pending;

// ---- Stage 2 registers — one set per port ----
reg pipe_valid_a /*verilator public_flat_rd*/;
reg  [25:0] pipe_addr_a;
reg         pipe_wide_a;

reg pipe_valid_b /*verilator public_flat_rd*/;
reg  [25:0] pipe_addr_b;
reg  [TAG_BITS-1:0] pipe_tag_b_r;
reg  [1:0]          pipe_byte_b_r;
reg         pipe_wide_b;

wire [TAG_BITS-1:0] pipe_tag_a  = pipe_addr_a[25:TAG_LO];
wire [1:0]          pipe_byte_a = pipe_addr_a[1:0];

wire [TAG_BITS-1:0] pipe_tag_b  = pipe_tag_b_r;
wire [1:0]          pipe_byte_b = pipe_byte_b_r;

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
assign req_ready   = (state_pipe_ready && !pipe_miss_a && !flush_block)
                  || (state_fillout_ready && (lat_port == 1'b0) && !flush_block);

// Port B is used for colormap lookups from the fragment pipe.  The
// consumer registers req_valid_b / req_addr_b on its side (a staged
// pending-request slot), so ready feeds only the GPU's stall cone and
// this module's own RAM read-enable — NOT the request address.  That
// lets ready be combinational on cache-internal registers (pipe_hit_b
// is the same compare depth resp_valid_b already exposes to the same
// stall cone) and port B can then accept BACK-TO-BACK on hits:
//
//   * pipe slot empty/consumed (!pipe_b_live): always safe to accept.
//   * pipe slot holds a RESOLVED HIT whose response hasn't been popped:
//     safe iff the held skid is free — the accept pushes the pipe
//     response into the skid, preserving it for the stalled consumer.
//   * pipe slot mid-miss (live && !hit): never accept; the fill machine
//     owns the slot and the miss branch must not race an accept.
//
// In S_FILL_OUT (port-B fill landing) the response is resolved by
// construction (fill_resp_valid_b), so only the skid-free condition
// applies.
reg pipe_b_live /*verilator public_flat_rd*/;    // pipe slot holds an accepted, not-yet-popped request
reg held_valid_b /*verilator public_flat_rd*/;   // skid holds the oldest unconsumed response
reg  [15:0] held_data_b;
assign req_ready_b = !flush_block
    && ( (state_pipe_ready
          && (!pipe_b_live || (pipe_hit_b && !held_valid_b)))
      || (state_fillout_ready && (lat_port == 1'b1)
          && (!pipe_b_live || !held_valid_b)) );
wire accept_b = req_valid_b && req_ready_b;

// ---- Combinational responses (hot path) ----
// Pipe-hit OR S_FILL_OUT held response.  fill_resp_valid_x is set at
// axi_rlast and held through the 1-cycle S_FILL_OUT window so the
// stalled consumer can still observe the response if it didn't shift
// at the exact landing cycle.
reg fill_resp_valid_a, fill_resp_valid_b;
assign resp_valid   = pipe_hit_a || fill_resp_valid_a;
// Port B responses are queue-ordered: the held skid (oldest) first,
// then the live pipe-slot response.  Gating the pipe-slot terms on
// pipe_b_live keeps a STALE pipe hit (request already popped, tags
// still matching) from presenting resp_valid_b for a pixel that has no
// outstanding request — the historical off-by-one byte-lane bug class.
assign resp_valid_b = held_valid_b
                   || (pipe_b_live && (pipe_hit_b || fill_resp_valid_b));

// ---- Pipelined RAM read — gated per-port on accept ----
// Same gate-on-accept rationale as the SDP-era cache (without it, rd_*
// drift to the next pixel's address while pipe_addr is still the prior
// pixel — produces glitched bytes when the consumer is mid-stall).
wire [SET_BITS-1:0] addr_set_a  = req_addr[TAG_LO-1:4];
wire [1:0]          addr_word_a = req_addr[3:2];

wire [SET_BITS-1:0] addr_set_b  = req_addr_b[TAG_LO-1:4];
wire [1:0]          addr_word_b = req_addr_b[3:2];

// Quartus M10K-inference rule: each rd_*_x reg has exactly one source
// (the M10K read), with a single read-address signal and a single
// read-enable.  Two paths feed the read address and enable:
//
//   1. Consumer accept (req_valid_x && req_ready_x): read at
//      (addr_set_x, addr_word_x).
//
//   2. S_FILL_OUT no-ack prime: the FSM has just written
//      tagv_mem[lat_set] <= {valid=1, lat_tag}, and
//      the line's words into data_mem[{lat_set, *}] in the prior
//      cycle (axi_rlast).  Read at (lat_set, lat_word) — the M10K
//      already holds the correct values, so we don't need a non-RAM
//      source.  This keeps the latch a clean SDP read pattern;
//      Quartus infers M10K with no FF blowup.
//
// The previous version used `else if (prime_x) rd_*_x <= lat_tag/...`
// — Quartus sees that as "rd_*_x is sometimes from RAM, sometimes
// from non-RAM logic" and gives up on M10K inference, falling back
// to FFs (Error 276003: "Cannot convert all sets of registers into
// RAM megafunctions").  This version reads from M10K in both cases;
// the address mux is fine in M10K's SDP interface.
wire prime_a = state_fillout_prime && (lat_port == 1'b0)
            && !flush_block
            && !(req_valid && req_ready);
wire prime_b = state_fillout_prime && (lat_port == 1'b1)
            && !flush_block
            && !accept_b;

wire [SET_BITS-1:0] read_set_a  = prime_a ? lat_set  : addr_set_a;
wire [1:0]          read_word_a = prime_a ? lat_word : addr_word_a;
wire                read_en_a   = (req_valid && req_ready) || prime_a;

wire [SET_BITS-1:0] read_set_b  = prime_b ? lat_set  : addr_set_b;
wire [1:0]          read_word_b = prime_b ? lat_word : addr_word_b;
wire                read_en_b   = accept_b || prime_b;

always @(posedge clk) begin
    if (read_en_a) begin
        {rd_valid_a, rd_tag_a} <= tagv_mem[read_set_a];
        rd_data_a  <= data_mem[{read_set_a, read_word_a}];
    end
end

always @(posedge clk) begin
    if (read_en_b) begin
        {rd_valid_b, rd_tag_b} <= tagv_mem[read_set_b];
        rd_data_b  <= data_mem[{read_set_b, read_word_b}];
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

wire [TAG_BITS-1:0] lat_tag  = lat_addr[25:TAG_LO];
wire [SET_BITS-1:0] lat_set  = lat_addr[TAG_LO-1:4];
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
// Pipe-slot response (hit path or fill path) — also the value captured
// into the held skid when a new accept displaces an unconsumed response.
wire [15:0] pipe_resp_data_b = pipe_hit_b
    ? (pipe_wide_b ? hw_from_rd_b : {8'b0, byte_from_rd_b})
    : (lat_wide    ? hw_from_fill : {8'b0, byte_from_fill});
assign resp_data_b = held_valid_b ? held_data_b : pipe_resp_data_b;

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
        pipe_tag_b_r <= 0;
        pipe_byte_b_r <= 0;
        pipe_wide_b  <= 0;
        pipe_b_live  <= 1'b0;
        held_valid_b <= 1'b0;
        held_data_b  <= 16'd0;
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

        // ----------------------------------------------------------------
        // Port B response-queue bookkeeping — runs in EVERY state (a pop
        // can arrive while the FSM is filling for port A, and an accept
        // can land in S_PIPE or S_FILL_OUT).
        //
        //   pop:    consume the oldest unconsumed response — the held
        //           skid if occupied, else the live pipe-slot response.
        //   accept: the pipe slot takes the new request (the accept
        //           blocks in S_PIPE / S_FILL_OUT write pipe_*_b); if the
        //           slot still held an unconsumed response that is NOT
        //           being popped this same cycle, it moves into the skid.
        //
        // Same-cycle pop+accept with an empty skid (the steady-state
        // 1-px/cycle case) consumes the pipe response and re-arms the
        // slot without touching the skid.  req_ready_b guarantees the
        // skid is free whenever an accept needs it.
        // ----------------------------------------------------------------
        if (resp_flush_b) begin
            held_valid_b <= 1'b0;
            pipe_b_live  <= 1'b0;
        end else begin
            if (resp_pop_b) begin
                if (held_valid_b)
                    held_valid_b <= 1'b0;
                else
                    pipe_b_live <= 1'b0;
            end
            if (accept_b) begin
                pipe_b_live <= 1'b1;
                if (pipe_b_live && !(resp_pop_b && !held_valid_b)) begin
                    held_valid_b <= 1'b1;
                    held_data_b  <= pipe_resp_data_b;
                end
            end
        end

        case (state)
        // ----------------------------------------------------------------
        // S_INIT: walk through 1024 sets writing tagv_mem[i].valid <= 0.
        // Entered on reset and on flush.  Both ports' req_ready are 0
        // because they only assert in S_PIPE / S_FILL_OUT.
        // 1024 cycles ≈ 10 µs at 100 MHz — negligible at boot/flush.
        // ----------------------------------------------------------------
        S_INIT: begin
            tagv_mem[init_counter] <= {1'b0, {TAG_BITS{1'b0}}};
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
            //     duration before its turn.
            //
            // Critical: the new-request accept blocks below MUST be
            // outside the miss-priority chain.  The earlier shape — a
            // chained if/elseif with the accepts in the trailing else
            // — silently broke port B on real hardware: A misses
            // dominate (fresh tex line per pixel under sparse access
            // patterns + ~50-100 cycle SDRAM fills), so the FSM
            // bounces between S_PIPE-for-1-cycle → S_FILL_AR → … →
            // S_PIPE-for-1-cycle and the else branch never executes.
            // Result: pipe_addr_b stuck at the very first accepted
            // value, every cmap fragment reads the same byte, screen
            // is uniform colour.  In Verilator the 2-cycle SDRAM stub
            // is fast enough that S_PIPE windows are wide and B
            // updates fine — the bug only manifests in synthesis
            // against real DRAM latency.
            //
            // Splitting accepts out is safe: port A's req_ready gates on
            // pipe_miss_a directly, and port B's ready gates on the live
            // pipe-slot state (a live request that hasn't resolved as a
            // hit blocks accepts), so a miss is resolved before the next
            // B accept can occur.  The miss branch's pipe_valid_x<=0
            // cannot race with an accept on the same port.
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
            end else if (flush_block) begin
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
                pipe_b_live  <= 1'b0;
                held_valid_b <= 1'b0;
            end

            // Per-port new-request accepts — independent of the miss/flush
            // priority block above.  req_ready_x already self-gates on the
            // right combination of state / readiness / flush_block, so this
            // cannot accept while the FSM is mid-fill or about to flush.
            if (req_valid && req_ready) begin
                pipe_valid_a <= 1;
                pipe_addr_a  <= req_addr;
                pipe_wide_a  <= req_wide;
            end
            if (accept_b) begin
                pipe_valid_b <= 1;
                pipe_addr_b  <= req_addr_b;
                pipe_tag_b_r <= req_addr_b[25:TAG_LO];
                pipe_byte_b_r <= req_addr_b[1:0];
                pipe_wide_b  <= req_wide_b;
            end
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
                    tagv_mem[lat_set]    <= {1'b1, lat_tag};
                    if (lat_port == 1'b0) fill_resp_valid_a <= 1;
                    else fill_resp_valid_b <= 1;
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
            // req_ready_x is high when flush_block is clear
            // (S_FILL_OUT && lat_port == X && !flush_block), so the
            // M10K-read latch fires and updates rd_*_x to the new addr's
            // data.  pipe_*_x captures the new req's address.
            // The held fill_resp_valid_x for the prior addr was already
            // observed at PRE-edge of this cycle, so the consumer's
            // shift captured it; the latch's new rd_*_x then becomes
            // the basis of next cycle's pipe_hit_x.
            //
            // If no new req: the prime sets pipe_*_x to lat_addr and
            // rd_*_x to the just-filled line's word containing the
            // requested byte.  Next cycle's pipe_hit_x carries the
            // response forward in S_PIPE.
            if (flush_block) begin
                state <= S_INIT;
                init_counter <= 0;
                fill_resp_valid_a <= 0;
                fill_resp_valid_b <= 0;
                pipe_valid_a <= 0;
                pipe_valid_b <= 0;
                pipe_b_live  <= 1'b0;
                held_valid_b <= 1'b0;
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
                    if (accept_b) begin
                        pipe_valid_b <= 1;
                        pipe_addr_b  <= req_addr_b;
                        pipe_tag_b_r <= req_addr_b[25:TAG_LO];
                        pipe_byte_b_r <= req_addr_b[1:0];
                        pipe_wide_b  <= req_wide_b;
                    end else begin
                        pipe_valid_b <= 1;
                        pipe_addr_b  <= lat_addr;
                        pipe_tag_b_r <= lat_addr[25:TAG_LO];
                        pipe_byte_b_r <= lat_addr[1:0];
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
