//
// AXI4 Slave Wrapper for SDRAM (io_sdram word interface)
//
// Converts AXI4 transactions to the io_sdram word-level protocol:
//   word_rd/wr pulse → accepted → busy → rdata_valid (reads)
//
// Features:
//   - Burst reads: ARLEN → word_burst_len (0=1 word, 7=8 words)
//   - Single and burst writes: each W beat → word_wr
//   - Optional serialized write-burst execution: keep the AXI side as one
//     AWLEN>0 transaction, but issue one conservative io_sdram word write per
//     beat.  This isolates hardware from the SDRAM burst-continuation path
//     while preserving lower upstream AXI/arbiter transaction volume.
//   - Single outstanding transaction
//   - 0 M10K (pure register/LUT)
//

`default_nettype none

module axi_sdram_slave #(
    parameter SERIALIZE_WRITE_BURSTS = 1'b0,
    // Largest AXI AWLEN that may execute as a native io_sdram burst when
    // SERIALIZE_WRITE_BURSTS is clear.  AWLEN=1 is the GPU framebuffer
    // coalescer's 2-word command.  Longer CPU writebacks can be kept on the
    // conservative serialized path at the Pocket top level.
    parameter [7:0] MAX_NATIVE_WRITE_BURST_LEN = 8'd15
) (
    input wire clk,
    input wire reset_n,

    // AXI4 Slave interface
    // AR channel (read address)
    input  wire        s_axi_arvalid,
    output reg         s_axi_arready,
    input  wire [31:0] s_axi_araddr,
    input  wire [7:0]  s_axi_arlen,

    // R channel (read data)
    output reg         s_axi_rvalid,
    input  wire        s_axi_rready,
    output reg  [31:0] s_axi_rdata,
    output reg  [1:0]  s_axi_rresp,
    output reg         s_axi_rlast,

    // AW channel (write address)
    input  wire        s_axi_awvalid,
    output reg         s_axi_awready,
    input  wire [31:0] s_axi_awaddr,
    input  wire [7:0]  s_axi_awlen,

    // W channel (write data)
    input  wire        s_axi_wvalid,
    output reg         s_axi_wready,
    input  wire [31:0] s_axi_wdata,
    input  wire [3:0]  s_axi_wstrb,
    input  wire        s_axi_wlast,

    // B channel (write response)
    output reg         s_axi_bvalid,
    input  wire        s_axi_bready,
    output reg  [1:0]  s_axi_bresp,

    // SDRAM word interface (directly to arbiter in core_top.v)
    output reg         sdram_rd,
    output reg         sdram_wr,
    output reg  [23:0] sdram_addr,
    output reg  [31:0] sdram_wdata,
    output reg  [3:0]  sdram_wstrb,
    output reg  [3:0]  sdram_burst_len,
    output reg  [3:0]  sdram_burst_wr_len,
    input  wire [31:0] sdram_rdata,
    input  wire        sdram_busy,
    input  wire        sdram_accepted,
    input  wire        sdram_rdata_valid,
    input  wire        sdram_wr_data_next, // io_sdram needs next word for burst write
    input  wire        sdram_wr_done,      // pulse: slave-issued write committed in io_sdram
    output wire [31:0] sdram_next_wdata,   // Active continuation word for io_sdram burst writes
    output wire [3:0]  sdram_next_wstrb,
    output wire [31:0] sdram_preload_wdata, // Beat 1 already captured before native 2-word issue
    output wire [3:0]  sdram_preload_wstrb
);

wire reset = ~reset_n;

// FSM states
localparam S_IDLE      = 4'd0;
localparam S_RD_CMD    = 4'd1;  // Issue word_rd, wait for accepted
localparam S_RD_DAT    = 4'd2;  // Wait for rdata_valid, send R beats
localparam S_WR_CMD    = 4'd3;  // Issue word_wr, wait for accepted
localparam S_WR_DON    = 4'd4;  // Wait for write completion (!busy)
localparam S_WR_NEXT   = 4'd5;  // Accept next W beat (single-word writes)
localparam S_WR_BURST  = 4'd6;  // Streaming: io_sdram pulls data via wr_data_next
localparam S_WR_NATIVE_W0 = 4'd7; // Capture beat 0 on a real W handshake
localparam S_WR_NATIVE_W1 = 4'd8; // Capture beat 1 before issuing a 2-beat write

reg [3:0] state;

// Transaction tracking
reg [7:0]  burst_len;    // ARLEN/AWLEN
reg [7:0]  beat_count;   // Beats completed
reg [31:0] addr_r;       // Current address (advances per beat)
reg        cmd_issued;   // word_rd/wr issued, waiting for accepted
reg [31:0] next_wdata;   // Pre-staged next W beat data (burst write)
reg [3:0]  next_wstrb;
reg [31:0] active_next_wdata; // Beat selected by the last sdram_wr_data_next pull
reg [3:0]  active_next_wstrb;
reg        wready_given; // Next W beat already accepted
reg        burst_preloaded; // 2-beat write has next_wdata before command issue
reg        wr_op_done_seen; // sdram_wr_done pulse latched after our write was accepted; preferred over !sdram_busy because the busy signal stays high across unrelated io_sdram ops (scanout burst_rd, autorefresh) and starves single-word writes

// Stable continuation export for io_sdram burst write forwarding.  This is
// latched on the pull pulse, so longer native bursts can accept/pre-stage the
// following W beat without changing the word io_sdram is about to capture.
assign sdram_next_wdata = active_next_wdata;
assign sdram_next_wstrb = active_next_wstrb;
assign sdram_preload_wdata = next_wdata;
assign sdram_preload_wstrb = next_wstrb;
reg        started;      // accepted seen, waiting for completion

// 3-entry response pipeline (primary R slot + 2 skid entries) so
// back-pressure from the master doesn't drop sdram_rdata_valid pulses.
// cpu_target_port's registered response slot can transiently lower
// rready mid-burst, and in the 3-master topology one master's slow
// rready holds m_rready low for every beat until it drains.  SDRAM
// streams at 1 beat/cycle, so a K-cycle master stall fills at most
// K-1 downstream buffers.  3 buffers → tolerant of up to 2 cycles of
// back-pressure before we'd have to drop, which is wider than anything
// observed on VexiiRiscv L1 refills.
//
// Invariant: entries are always contiguous from the slot toward skid2
// (rskid2_valid implies rskid_valid implies s_axi_rvalid).  Pushes fill
// the first empty position; drains shift toward the slot.
reg [31:0] rskid_data,  rskid2_data;
reg        rskid_last,  rskid2_last;
reg        rskid_valid, rskid2_valid;

wire beat_is_last = (beat_count == burst_len);
wire write_burst_serialized = SERIALIZE_WRITE_BURSTS ||
                              (burst_len > MAX_NATIVE_WRITE_BURST_LEN);
wire idle_aw_serialized = SERIALIZE_WRITE_BURSTS ||
                          (s_axi_awlen > MAX_NATIVE_WRITE_BURST_LEN);
wire idle_aw_native_burst2 = !idle_aw_serialized && (s_axi_awlen == 8'd1);

always @(posedge clk or posedge reset) begin
    if (reset) begin
        state <= S_IDLE;
        burst_len <= 0;
        beat_count <= 0;
        addr_r <= 0;
        cmd_issued <= 0;
        started <= 0;

        s_axi_arready <= 0;
        s_axi_rvalid <= 0;
        s_axi_rdata <= 0;
        s_axi_rresp <= 0;
        s_axi_rlast <= 0;
        s_axi_awready <= 0;
        s_axi_wready <= 0;
        s_axi_bvalid <= 0;
        s_axi_bresp <= 0;

        sdram_rd <= 0;
        sdram_wr <= 0;
        sdram_addr <= 0;
        sdram_wdata <= 0;
        sdram_wstrb <= 0;
        sdram_burst_len <= 0;
        sdram_burst_wr_len <= 0;
        next_wdata <= 0;
        next_wstrb <= 0;
        active_next_wdata <= 0;
        active_next_wstrb <= 0;
        wready_given <= 0;
        burst_preloaded <= 0;
        wr_op_done_seen <= 0;

        rskid_data   <= 0;
        rskid_last   <= 0;
        rskid_valid  <= 0;
        rskid2_data  <= 0;
        rskid2_last  <= 0;
        rskid2_valid <= 0;
    end else begin
        // Defaults: deassert single-cycle signals.  rvalid and bvalid
        // are NOT in this list — they are "hold until *ready" signals
        // and are explicitly dropped on their respective handshakes
        // below.
        s_axi_arready <= 0;
        s_axi_awready <= 0;
        s_axi_wready <= 0;
        sdram_rd <= 0;
        sdram_wr <= 0;
        sdram_burst_len <= 0;
        sdram_burst_wr_len <= 0;

        // AXI handshake drain-and-shift for R: on every accepted beat,
        // shift the skid chain one position toward the slot.  A push
        // below may override one of the positions on the same cycle
        // (non-blocking last-wins), which is how we handle concurrent
        // drain+push without breaking the contiguous-valid invariant.
        if (s_axi_rvalid && s_axi_rready) begin
            if (rskid_valid) begin
                s_axi_rdata <= rskid_data;
                s_axi_rlast <= rskid_last;
                // s_axi_rvalid stays 1 — skid promoted into slot.
            end else begin
                s_axi_rvalid <= 1'b0;
            end
            rskid_data   <= rskid2_data;
            rskid_last   <= rskid2_last;
            rskid_valid  <= rskid2_valid;
            rskid2_valid <= 1'b0;
        end
        if (s_axi_bvalid && s_axi_bready) s_axi_bvalid <= 1'b0;

        case (state)

        S_IDLE: begin
            cmd_issued <= 0;
            started <= 0;
            // Reads have priority over writes.  Accept a new AR as soon
            // as the skid chain is empty.  AR/AW fairness was attempted
            // (alternation, wire-factored, starvation timer) and each
            // variant pushed setup TNS from -31 to -130..-300 — the
            // slave's S_IDLE has a tight combinational path through
            // the early-issue `!sdram_busy` arms that fitter cannot
            // close at 100 MHz with extra inputs on the AR predicate.
            // Stage 2a (FBSS B-wait removal) gave 2x measured fps
            // without touching this path; if read-vs-write contention
            // remains the bottleneck, Stage 2b (multi-beat AW bursts)
            // amortises per-grant cost without modifying the priority.
            if (s_axi_arvalid && !rskid_valid) begin
                s_axi_arready <= 1;
                addr_r <= s_axi_araddr;
                burst_len <= s_axi_arlen;
                beat_count <= 0;
                // Early command issue: if SDRAM idle, start read 1 cycle sooner
                if (!sdram_busy) begin
                    sdram_rd <= 1;
                    sdram_addr <= s_axi_araddr[25:2];
                    sdram_burst_len <= s_axi_arlen[3:0];
                    cmd_issued <= 1;
                end
                state <= S_RD_CMD;
            end else if (s_axi_awvalid) begin
                s_axi_awready <= 1;
                addr_r <= s_axi_awaddr;
                burst_len <= s_axi_awlen;
                beat_count <= 0;
                burst_preloaded <= 1'b0;
                if (idle_aw_native_burst2) begin
                    // Native 2-beat writes need a stricter W handshake than
                    // the legacy early-capture path below.  WREADY is a
                    // register, so sampling beat 1 immediately after sampling
                    // beat 0 duplicates beat 0 on a normal master.  Wait for
                    // two observed WVALID&WREADY handshakes first, then start
                    // io_sdram with both beats preloaded.
                    state <= S_WR_NATIVE_W0;
                end else if (s_axi_wvalid) begin
                    // Also accept W if valid (common case: AW and W arrive
                    // together).  This legacy early-capture path is safe for
                    // single/serialized writes because the first data beat is
                    // held stable until the registered WREADY handshake lands.
                    s_axi_wready <= 1;
                    sdram_wdata <= s_axi_wdata;
                    sdram_wstrb <= s_axi_wstrb;
                    // Early command issue for writes too
                    if (!sdram_busy) begin
                        sdram_wr <= 1;
                        sdram_addr <= s_axi_awaddr[25:2];
                        sdram_burst_wr_len <= (idle_aw_serialized && (s_axi_awlen != 8'd0))
                                            ? 4'd0 : s_axi_awlen[3:0];
                        cmd_issued <= 1;
                    end
                    state <= S_WR_CMD;
                end else begin
                    state <= S_WR_NEXT;
                end
            end
        end

        // ============================================
        // Read path
        // ============================================
        S_RD_CMD: begin
            // Issue read to SDRAM, hold until accepted
            if (!cmd_issued) begin
                if (!sdram_busy) begin
                    sdram_rd <= 1;
                    sdram_addr <= addr_r[25:2];
                    sdram_burst_len <= burst_len[3:0];
                    cmd_issued <= 1;
                    started <= 0;
                end
            end else begin
                // Hold read request until arbiter accepts
                sdram_rd <= 1;
                sdram_addr <= addr_r[25:2];
                sdram_burst_len <= burst_len[3:0];
                if (sdram_accepted) begin
                    started <= 1;
                    state <= S_RD_DAT;
                end
            end
        end

        S_RD_DAT: begin
            // Gate with started to prevent capturing peripheral data
            // before our command was accepted.  An incoming
            // sdram_rdata_valid pulse is placed at the earliest empty
            // position in the drain-shifted pipeline (slot / skid /
            // skid2).  When the handshake block above shifts on the
            // same cycle, the shifted post-drain state drives the
            // target selection so the invariant stays contiguous.
            if (started && sdram_rdata_valid) begin
                if (s_axi_rvalid && s_axi_rready) begin
                    // Drain-overlap path: post-shift state is
                    // (S'=rskid_valid, K'=rskid2_valid, K2'=0).
                    if (!rskid_valid) begin
                        // Slot will be empty after shift → push to slot.
                        s_axi_rvalid <= 1'b1;
                        s_axi_rdata  <= sdram_rdata;
                        s_axi_rresp  <= 2'b00;
                        s_axi_rlast  <= beat_is_last;
                    end else if (!rskid2_valid) begin
                        // Skid will be empty after shift → push to skid.
                        rskid_valid <= 1'b1;
                        rskid_data  <= sdram_rdata;
                        rskid_last  <= beat_is_last;
                    end else begin
                        // Full chain shifting — skid2 vacates → push there.
                        rskid2_valid <= 1'b1;
                        rskid2_data  <= sdram_rdata;
                        rskid2_last  <= beat_is_last;
                    end
                end else begin
                    // No drain this cycle: push into first empty slot.
                    if (!s_axi_rvalid) begin
                        s_axi_rvalid <= 1'b1;
                        s_axi_rdata  <= sdram_rdata;
                        s_axi_rresp  <= 2'b00;
                        s_axi_rlast  <= beat_is_last;
                    end else if (!rskid_valid) begin
                        rskid_valid <= 1'b1;
                        rskid_data  <= sdram_rdata;
                        rskid_last  <= beat_is_last;
                    end else if (!rskid2_valid) begin
                        rskid2_valid <= 1'b1;
                        rskid2_data  <= sdram_rdata;
                        rskid2_last  <= beat_is_last;
                    end
                    // else: all three full — drop (invariant broken by
                    // pathological back-pressure).  beat_count still
                    // advances so the FSM clears — master will hang
                    // waiting on the lost beat, which is observable at
                    // the bus and forces the backpressure assumption
                    // to be revisited.
                end
                beat_count <= beat_count + 1;
                if (beat_is_last) begin
                    cmd_issued <= 0;
                    started    <= 0;
                    state      <= S_IDLE;
                end
            end
        end

        // ============================================
        // Write path
        // ============================================
        S_WR_CMD: begin
            // Issue write to SDRAM, hold until accepted
            if (!cmd_issued) begin
                if (!sdram_busy) begin
                    sdram_wr <= 1;
                    sdram_addr <= addr_r[25:2];
                    sdram_burst_wr_len <= (write_burst_serialized && (burst_len != 8'd0))
                                        ? 4'd0 : burst_len[3:0];
                    cmd_issued <= 1;
                    started <= 0;
                end
            end else begin
                sdram_wr <= 1;
                sdram_addr <= addr_r[25:2];
                sdram_burst_wr_len <= (write_burst_serialized && (burst_len != 8'd0))
                                    ? 4'd0 : burst_len[3:0];
                if (sdram_accepted) begin
                    started <= 1;
                    // Reset wr_op_done_seen for BOTH paths.
                    // The saw-busy gate alone left S_WR_DON polling
                    // !sdram_busy across unrelated io_sdram activity (scanout
                    // burst_rd, autorefresh, mixer reads), throttling
                    // single-word FB writes to ~8 fps.  word_wr_done is a
                    // 1-cycle pulse from io_sdram tied to ST_WRITE_4→ST_IDLE
                    // for THIS write — robust against back-to-back ops.
                    wr_op_done_seen <= 0;
                    if (burst_len == 0 || write_burst_serialized)
                        state <= S_WR_DON;   // Single/serialized word: wait for done pulse
                    else begin
                        wready_given <= burst_preloaded;
                        wr_op_done_seen <= 0;
                        state <= S_WR_BURST;  // Burst: stream data
                    end
                end
            end
        end

        S_WR_BURST: begin
            /* Streaming burst write mirrors the read/cache-fill side:
             * the SDRAM controller owns cadence and raises a one-cycle
             * pull pulse, then this slave selects that exact beat into a
             * stable register for the controller to capture later.  The
             * pre-stage slot lets AXI W handshakes happen before or on
             * the pull without changing the active continuation word. */
            if (sdram_wr_done) wr_op_done_seen <= 1;
            if (started && (wr_op_done_seen || sdram_wr_done)) begin
                cmd_issued <= 0;
                started <= 0;
                wready_given <= 0;
                burst_preloaded <= 1'b0;
                wr_op_done_seen <= 0;
                s_axi_bvalid <= 1;
                s_axi_bresp <= 2'b00;
                state <= S_IDLE;
            end else begin
                /* Select the pulled beat into active_next_* on io_sdram's
                 * pull.  io_sdram captures burst_wr_direct_* a few cycles
                 * later, while next_wdata can already accept a following
                 * AXI beat for longer native bursts. */
                if (sdram_wr_data_next) begin
                    beat_count <= beat_count + 1;
                    if (wready_given) begin
                        active_next_wdata <= next_wdata;
                        active_next_wstrb <= next_wstrb;
                        sdram_wdata  <= next_wdata;
                        sdram_wstrb  <= next_wstrb;
                        if (s_axi_wvalid) begin
                            next_wdata   <= s_axi_wdata;
                            next_wstrb   <= s_axi_wstrb;
                            wready_given <= 1'b1;
                            s_axi_wready <= 1'b1;
                        end else begin
                            wready_given <= 1'b0;
                        end
                    end else if (s_axi_wvalid) begin
                        active_next_wdata <= s_axi_wdata;
                        active_next_wstrb <= s_axi_wstrb;
                        sdram_wdata  <= s_axi_wdata;
                        sdram_wstrb  <= s_axi_wstrb;
                        s_axi_wready <= 1'b1;
                    end
                    /* else: genuine underrun — no pre-staged beat ready.
                     * sdram_wdata keeps its prior value and io_sdram
                     * would write stale.  Should be unreachable in
                     * practice with 1 beat of buffering versus the
                     * ~3-cycle pull→capture window. */
                end else if (s_axi_wvalid && !wready_given) begin
                    /* Pre-stage: capture W beat into next_wdata the cycle it
                     * arrives, decoupled from io_sdram's pull rate. */
                    next_wdata   <= s_axi_wdata;
                    next_wstrb   <= s_axi_wstrb;
                    wready_given <= 1'b1;
                    s_axi_wready <= 1'b1;
                end
            end
        end

        S_WR_DON: begin
            // Wait for single-word write completion (burst_len == 0).
            // Original saw-busy gate observed busy=HIGH then exited on !busy
            // to avoid false-complete in the dispatch→busy gap.  But
            // sdram_busy is shared with scanout burst_rd / autorefresh /
            // burstwr, so it stays HIGH across unrelated ops and the
            // slave starves under load (~8 fps in Duke3D, fbss=FLUSH_W_RSP
            // wedge).  Use sdram_wr_done pulse instead: it fires exactly
            // when io_sdram's ST_WRITE_4→ST_IDLE for THIS write, immune
            // to subsequent contention.
            if (sdram_wr_done) wr_op_done_seen <= 1;
            if (started && (wr_op_done_seen || sdram_wr_done)) begin
                cmd_issued <= 0;
                started <= 0;
                wr_op_done_seen <= 0;
                if (write_burst_serialized && (beat_count != burst_len)) begin
                    // AXI burst accepted as one transaction, executed as
                    // independent io_sdram word writes.  Hold B until the
                    // final beat commits.
                    beat_count <= beat_count + 8'd1;
                    addr_r <= addr_r + 32'd4;
                    cmd_issued <= 0;
                    started <= 0;
                    wr_op_done_seen <= 0;
                    state <= S_WR_NEXT;
                end else begin
                    burst_preloaded <= 1'b0;
                    s_axi_bvalid <= 1;
                    s_axi_bresp <= 2'b00;
                    state <= S_IDLE;
                end
            end
        end

        S_WR_NATIVE_W0: begin
            // Strict native-2 W receiver.  Set WREADY, then sample only when
            // WREADY was already visible to the master for this clock edge.
            s_axi_wready <= 1'b1;
            if (s_axi_wvalid && s_axi_wready) begin
                sdram_wdata <= s_axi_wdata;
                sdram_wstrb <= s_axi_wstrb;
                state <= S_WR_NATIVE_W1;
            end
        end

        S_WR_NATIVE_W1: begin
            // Capture beat 1 on its own handshake, then issue one native
            // io_sdram two-word write.
            s_axi_wready <= 1'b1;
            if (s_axi_wvalid && s_axi_wready) begin
                next_wdata <= s_axi_wdata;
                next_wstrb <= s_axi_wstrb;
                wready_given <= 1'b1;
                burst_preloaded <= 1'b1;
                state <= S_WR_CMD;
            end
        end

        S_WR_NEXT: begin
            // Accept next W beat (single-word fallback path)
            s_axi_wready <= 1'b1;
            if (s_axi_wvalid) begin
                sdram_wdata <= s_axi_wdata;
                sdram_wstrb <= s_axi_wstrb;
                state <= S_WR_CMD;
            end
        end

        default: state <= S_IDLE;

        endcase
    end
end

endmodule
