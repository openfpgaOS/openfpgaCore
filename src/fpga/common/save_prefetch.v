// save_prefetch — APF save-read latency hider.
//
// Problem this fixes: APF asserts bridge_rd at a save-slot address with
// only ~4 clk_74a cycles of capture window for bridge_rd_data, but a
// CRAM1 async read takes ~25–30 cycles.  The current single-word read
// path (bridge_cram1_rd_detect FSM in core_top.v) returns garbage on
// every word, persisting corrupted save data to the SD card.
//
// This module places a 32-word ring BRAM between the bridge and CRAM1.
// The pre-trigger `dataslot_requestread` arrives long before APF starts
// strobing bridge_rd (APF first does an SD-side setup that takes ms),
// so we use it to start sync-burst-fetching the slot's contents into
// BRAM ahead of time.  By the time bridge_rd fires, the requested word
// is already in BRAM, and the combinational port-B read meets APF's
// 4-cycle window.
//
// Bursts are capped at 16 words to stay within one CRAM1 row (avoids
// the word-32 IOB skew bug noted in audio_dma.md).  After the initial
// fill, the prefetcher refills as the bridge drains, sliding-window
// style, so APF can read an arbitrary-length save without ever waiting
// for CRAM1.
//
// Lives on clk_74a (same domain as bridge + psram1_b).  Replaces
// bridge_cram1_rd_detect / cram1_rd_resp_data in core_top.v.  The
// write path (bridge_cram1_wr_detect → psram1_b.word_wr) is untouched.

`default_nettype none

module save_prefetch #(
    parameter NUM_SLOTS = 10,
    parameter RING_DEPTH_LOG2 = 5,           // 32-word ring (1 M10K)
    parameter BURST_WORDS    = 16,           // cap per design constraint
    parameter REFILL_THRESHOLD = 16          // refill when free >= this
) (
    input  wire         clk,
    input  wire         reset_n,

    // APF bridge (clk_74a)
    input  wire         bridge_rd,
    input  wire [31:0]  bridge_addr,
    output reg  [31:0]  bridge_rd_data,

    // APF dataslot pre-trigger
    input  wire         dataslot_requestread,
    input  wire [15:0]  dataslot_requestread_id,

    // Per-slot CRAM1 base address table — CPU writes via MMIO at boot.
    // Word address in CRAM1 (22 bits, right-justified).  Flat packed
    // bus, slot N at bits [N*22+21:N*22].  axi_periph_slave's writer
    // packs these the same way; Verilator handles flat ports more
    // robustly than unpacked arrays at module boundaries.
    input  wire [NUM_SLOTS*22-1:0] slot_base_addr_flat,

    // Burst-read interface to cram1_controller (psram1_b)
    output reg          burst_rd,
    output reg  [21:0]  burst_addr,
    output reg  [4:0]   burst_len,
    input  wire [31:0]  burst_q,
    input  wire         burst_q_valid,
    input  wire         burst_busy
);

localparam RING_DEPTH = 1 << RING_DEPTH_LOG2;  // 32
localparam BURST_LEN_M1 = BURST_WORDS - 1;     // 15

// =====================================================
// Ring BRAM (true dual-port, 32-bit wide).
// Port A: prefetch writes from the burst stream.
// Port B: bridge reads — UNREGISTERED so bridge_rd_data is
// combinationally valid in the bridge_rd cycle.
// =====================================================
reg  [31:0] bram [0:RING_DEPTH-1];

// =====================================================
// Counters.
//   consumed_count : total words served to the bridge since slot start.
//   fetched_count  : total words written into BRAM since slot start.
//   depth          = fetched_count - consumed_count  (words ready)
//   space          = RING_DEPTH - depth              (words burst can fill)
//
// Width = 22 (matches CRAM1 word-address width).  Counters never wrap
// inside a single slot read (CRAM1 is 4M words; even the largest save
// is < 256K words).  The lower 5 bits index BRAM; the full counter
// gives the absolute slot offset for expected_word_addr math.
//
// (Earlier sizing CTR_BITS=6 wrapped consumed_count at word 64 and
// caused the address compare to start missing — see git history.)
// =====================================================
localparam CTR_BITS = 22;

reg [CTR_BITS-1:0] consumed_count;
reg [CTR_BITS-1:0] fetched_count;

wire [CTR_BITS-1:0] depth = fetched_count - consumed_count;
wire [CTR_BITS-1:0] space = RING_DEPTH[CTR_BITS-1:0] - depth;

wire [RING_DEPTH_LOG2-1:0] bram_wr_idx = fetched_count[RING_DEPTH_LOG2-1:0];
wire [RING_DEPTH_LOG2-1:0] bram_rd_idx = consumed_count[RING_DEPTH_LOG2-1:0];

// Combinational port-B read; q_b is always whatever's at the current
// read pointer.  When consumed_count advances, q_b updates within the
// same cycle (no register), so the very next bridge_rd cycle sees the
// next word.
wire [31:0] bram_q_b = bram[bram_rd_idx];

// =====================================================
// Slot state.
//   slot_start_word : absolute CRAM1 word address of consumed_count=0.
//   armed           : a slot has been requested; we have a base addr.
// expected_addr    = slot_start_word + consumed_count is the word
// address the next bridge_rd must match.  Mismatch ⇒ APF moved (rare;
// reset and refetch).
// =====================================================
reg [21:0] slot_start_word;
reg        armed;

wire [21:0] expected_word_addr = slot_start_word + consumed_count;

wire        bridge_in_save_range = (bridge_addr[31:24] == 8'h30);
wire [21:0] bridge_word_addr     = bridge_addr[23:2];
wire        bridge_addr_match    = bridge_in_save_range
                                 && (bridge_word_addr == expected_word_addr);

// Slot-id range check (only IDs < NUM_SLOTS have a base entry).
wire        slot_id_in_range = (dataslot_requestread_id < NUM_SLOTS);

// =====================================================
// Burst tracking.
//   burst_inflight   : a burst is in flight (issued, not yet drained).
//   burst_saw_busy   : controller's burst_busy went HIGH at least once
//                      since we issued — guards against treating the
//                      pre-busy LOW window as "burst already done"
//                      (the cram1_controller takes a few cycles to
//                      assert burst_busy after seeing burst_rd).
//   burst_words_left : remaining 32-bit words from the in-flight burst.
// =====================================================
reg        burst_inflight;
reg        burst_saw_busy;
reg [4:0]  burst_words_left;

// =====================================================
// bridge_rd_data — combinational from BRAM port B when the bridge_rd
// address matches the head of our ring.  When the address doesn't
// match, drive 0 so the upstream registered mux in core_top.v doesn't
// latch stale data.
// =====================================================
always @* begin
    if (bridge_addr_match)
        bridge_rd_data = bram_q_b;
    else
        bridge_rd_data = 32'h0000_0000;
end

// =====================================================
// Main state machine.  Single always block — the protocol is simple
// enough that an explicit FSM with named states would just add lines.
//
// NOTE: the ring BRAM is intentionally NOT reset.  Quartus refuses to
// infer M10K when the array has a reset assignment, and the contents
// are don't-care — `armed` gates serve until the first burst has
// landed in BRAM, so initial values don't reach bridge_rd_data.
// =====================================================
always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        consumed_count   <= {CTR_BITS{1'b0}};
        fetched_count    <= {CTR_BITS{1'b0}};
        slot_start_word  <= 22'd0;
        armed            <= 1'b0;
        burst_rd         <= 1'b0;
        burst_addr       <= 22'd0;
        burst_len        <= 5'd0;
        burst_inflight   <= 1'b0;
        burst_saw_busy   <= 1'b0;
        burst_words_left <= 5'd0;
    end else begin
        // Default: clear single-cycle pulse.
        burst_rd <= 1'b0;

        // -----------------------------------------------------------
        // 1) New slot trigger (highest priority — overrides anything).
        //    APF hands us slot_id; we look up the base addr and reset
        //    pointers so the next burst starts at slot_start_word.
        // -----------------------------------------------------------
        if (dataslot_requestread && slot_id_in_range) begin
            // Slice slot N's 22-bit base out of the packed bus.
            slot_start_word  <= slot_base_addr_flat[dataslot_requestread_id[3:0]*22 +: 22];
            consumed_count   <= {CTR_BITS{1'b0}};
            fetched_count    <= {CTR_BITS{1'b0}};
            armed            <= 1'b1;
            // Drop any in-flight burst — its data is stale (wrong slot).
            // The burst itself will still drain on cram1_controller's
            // side; we just stop capturing into BRAM.
            burst_inflight   <= 1'b0;
            burst_saw_busy   <= 1'b0;
            burst_words_left <= 5'd0;
        end else begin

            // -------------------------------------------------------
            // 2) Bridge read consume / address-match check.
            //    Hit  → advance read pointer (one word served).
            //    Miss → APF jumped (different slot, replay, etc.):
            //           re-anchor at the new bridge_addr so the next
            //           prefetch starts there.  This first read still
            //           returns 0 (combinational mux above), which is
            //           the documented "first word stale" edge case.
            // -------------------------------------------------------
            if (bridge_rd && bridge_in_save_range) begin
                if (bridge_addr_match && depth != {CTR_BITS{1'b0}}) begin
                    consumed_count <= consumed_count + 1'b1;
                end else if (!bridge_addr_match) begin
                    slot_start_word  <= bridge_word_addr;
                    consumed_count   <= {CTR_BITS{1'b0}};
                    fetched_count    <= {CTR_BITS{1'b0}};
                    armed            <= 1'b1;
                    burst_inflight   <= 1'b0;
                    burst_saw_busy   <= 1'b0;
                    burst_words_left <= 5'd0;
                end
                // bridge_addr_match && depth==0 — underrun.  Hold
                // pointers; APF gets bridge_rd_data=0.  The refill
                // logic below will issue a burst; APF should retry
                // (or the upstream caller treats 0 as "not ready").
            end

            // -------------------------------------------------------
            // 3) Burst data capture.
            //    Stream burst_q_valid pulses into BRAM at write_ptr.
            //    burst_inflight drops when burst_busy falls (controller
            //    side has fully drained, including the SYNC_END phase).
            // -------------------------------------------------------
            if (burst_inflight) begin
                if (burst_q_valid && burst_words_left != 5'd0) begin
                    bram[bram_wr_idx] <= burst_q;
                    fetched_count <= fetched_count + 1'b1;
                    burst_words_left <= burst_words_left - 5'd1;
                end
                if (burst_busy)
                    burst_saw_busy <= 1'b1;
                // Burst is fully drained when the controller has been
                // busy at least once and is now idle again.  Without
                // burst_saw_busy, the !burst_busy edge BEFORE the
                // controller picks up burst_rd would falsely complete.
                if (burst_saw_busy && !burst_busy) begin
                    burst_inflight <= 1'b0;
                    burst_saw_busy <= 1'b0;
                end
            end

            // -------------------------------------------------------
            // 4) Burst issue.
            //    Issue a new burst when:
            //      - we're armed (slot is set up),
            //      - no burst already in flight,
            //      - controller side fully idle (burst_busy LOW) — a
            //        burst_rd pulse issued while the controller is
            //        still draining the previous burst gets silently
            //        dropped at ST_IDLE in cram1_controller.  This
            //        matters when dataslot_requestread fires mid-burst
            //        (slot change): we drop our internal burst_inflight
            //        but the controller is still busy for ~80 cycles.
            //      - free space ≥ REFILL_THRESHOLD (so we don't churn
            //        small bursts).
            // -------------------------------------------------------
            if (armed && !burst_inflight && !burst_busy && !burst_rd
                && (space >= REFILL_THRESHOLD[CTR_BITS-1:0]))
            begin
                burst_rd         <= 1'b1;
                burst_addr       <= slot_start_word + fetched_count;
                burst_len        <= BURST_LEN_M1[4:0];
                burst_inflight   <= 1'b1;
                burst_words_left <= BURST_WORDS[4:0];
            end
        end
    end
end

endmodule
