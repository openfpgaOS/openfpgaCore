//
// AXI4 Slave for CRAM0 — sync-burst reads for multi-beat AXI bursts,
// async single-word reads for single-beat AR, and per-beat writes.
// Not shared with axi_cram1_slave by design — the two CRAM paths
// stay architecturally independent.
//
// Read path:
//   Multi-beat (ARLEN > 0):
//     S_IDLE → S_RD_BRST_CMD → S_RD_BRST_DAT → (stream) → S_IDLE
//     Issues ONE psram_burst_rd pulse with burst_len = ARLEN, then
//     accepts one R beat per psram_burst_rdata_valid pulse until
//     beat_is_last.  Uses the saw-busy completion gate (observe
//     psram_busy HIGH first, then wait for valid pulses).
//   Single-beat (ARLEN == 0):
//     S_IDLE → S_RD_CMD → S_RD_DAT → S_IDLE
//     Issues one psram_rd pulse, waits for psram_rdata_valid.  Kept
//     as the fast single-word path (no sync-burst preamble).
//
//   Both read paths share the 1-entry skid buffer so master
//   back-pressure doesn't drop beats.
//
// Write path (unpipelined):
//     S_IDLE → [S_WR_NEXT] → S_WR_CMD → S_WR_WAIT → ...
//   - Same-cycle W capture in S_IDLE when AW+W arrive bundled.
//   - Otherwise S_WR_NEXT accepts one W beat per round trip.
//   - No pre-capture of the next beat — each beat takes the full
//     psram_wr round trip.  Pipelining was tried and caused
//     testdemo CRAM0 corruption; revert left write path simple.
//   - S_WR_RESP holds s_axi_bvalid until the master asserts bready.
//
// Both paths carry a short issue_wait timeout so a dropped psram
// request pulse (rare) re-issues the command instead of hanging.
//

`default_nettype none

module axi_cram0_slave (
    input wire clk,
    input wire reset_n,

    // AXI4 Slave interface
    input  wire        s_axi_arvalid,
    output reg         s_axi_arready,
    input  wire [31:0] s_axi_araddr,
    input  wire [7:0]  s_axi_arlen,

    output reg         s_axi_rvalid,
    input  wire        s_axi_rready,
    output reg  [31:0] s_axi_rdata,
    output reg  [1:0]  s_axi_rresp,
    output reg         s_axi_rlast,

    input  wire        s_axi_awvalid,
    output reg         s_axi_awready,
    input  wire [31:0] s_axi_awaddr,
    input  wire [7:0]  s_axi_awlen,

    input  wire        s_axi_wvalid,
    output reg         s_axi_wready,
    input  wire [31:0] s_axi_wdata,
    input  wire [3:0]  s_axi_wstrb,
    input  wire        s_axi_wlast,

    output reg         s_axi_bvalid,
    input  wire        s_axi_bready,
    output reg  [1:0]  s_axi_bresp,

    // PSRAM single-word interface (to psram_cram0 via per-target mux)
    output reg         psram_rd,
    output reg         psram_wr,
    output reg  [25:0] psram_addr,
    output reg  [31:0] psram_wdata,
    output reg  [3:0]  psram_wstrb,
    input  wire [31:0] psram_rdata,
    input  wire        psram_busy,
    input  wire        psram_rdata_valid,

    // PSRAM sync-burst read interface — used for ARLEN > 0.
    // psram_burst_len follows the controller's convention: N 32-bit
    // words - 1 (matches AXI ARLEN directly).
    output reg         psram_burst_rd,
    output reg  [5:0]  psram_burst_len,
    input  wire [31:0] psram_burst_rdata,
    input  wire        psram_burst_rdata_valid
);

wire reset = ~reset_n;

// =================================================================
// FSM states
// =================================================================
localparam S_IDLE        = 4'd0;
localparam S_RD_CMD      = 4'd1;
localparam S_RD_DAT      = 4'd2;
localparam S_RD_BRST_CMD = 4'd3;
localparam S_RD_BRST_DAT = 4'd4;
localparam S_WR_NEXT     = 4'd5;
localparam S_WR_CMD      = 4'd6;
localparam S_WR_WAIT     = 4'd7;
localparam S_WR_RESP     = 4'd8;

reg [3:0] state;

// =================================================================
// Transaction tracking
// =================================================================
reg [7:0]  burst_len;       // AxLEN captured at grant
reg [7:0]  beat_count;      // AXI beats delivered / accepted so far
reg [31:0] addr_r;          // running address (byte addr, word-aligned)
reg        cmd_issued;      // psram command pulse raised
reg        psram_started;   // psram_busy has been observed high
reg        wlast_seen;      // master asserted wlast on current burst
reg [7:0]  issue_wait;      // timeout counter for a missed command pulse

// 3-entry R response pipeline (primary slot + 2 skid entries).  The
// sync-burst controller emits one 32-bit beat every ~2 cycles and
// cpu_target_port's registered response slot can transiently lower
// m_rready mid-burst, so buffering one beat isn't enough under
// 3-master contention.  3 buffers absorb up to 4 cycles of back-
// pressure before we'd have to drop — wider than observed stalls.
//
// Invariant: entries are contiguous from the slot toward skid2
// (rskid2_valid implies rskid_valid implies s_axi_rvalid).
reg [31:0] rskid_data,  rskid2_data;
reg        rskid_last,  rskid2_last;
reg        rskid_valid, rskid2_valid;

wire beat_is_last = (beat_count == burst_len);

// =================================================================
// Main FSM
// =================================================================
always @(posedge clk or posedge reset) begin
    if (reset) begin
        state            <= S_IDLE;
        burst_len        <= 8'b0;
        beat_count       <= 8'b0;
        addr_r           <= 32'b0;
        cmd_issued       <= 1'b0;
        psram_started    <= 1'b0;
        wlast_seen       <= 1'b0;
        issue_wait       <= 8'b0;

        s_axi_arready    <= 1'b0;
        s_axi_awready    <= 1'b0;
        s_axi_wready     <= 1'b0;
        s_axi_rvalid     <= 1'b0;
        s_axi_rdata      <= 32'b0;
        s_axi_rresp      <= 2'b0;
        s_axi_rlast      <= 1'b0;
        s_axi_bvalid     <= 1'b0;
        s_axi_bresp      <= 2'b0;

        psram_rd         <= 1'b0;
        psram_wr         <= 1'b0;
        psram_burst_rd   <= 1'b0;
        psram_burst_len  <= 6'b0;
        psram_addr       <= 26'b0;
        psram_wdata      <= 32'b0;
        psram_wstrb      <= 4'b0;

        rskid_data       <= 32'b0;
        rskid_last       <= 1'b0;
        rskid_valid      <= 1'b0;
        rskid2_data      <= 32'b0;
        rskid2_last      <= 1'b0;
        rskid2_valid     <= 1'b0;
    end else begin
        // Single-cycle pulse defaults.  rvalid / bvalid are NOT in
        // this list — they are hold-until-ready signals and are
        // dropped explicitly by the handshake block below.
        s_axi_arready    <= 1'b0;
        s_axi_awready    <= 1'b0;
        s_axi_wready     <= 1'b0;
        psram_rd         <= 1'b0;
        psram_wr         <= 1'b0;
        psram_burst_rd   <= 1'b0;

        // AXI R drain: on every accepted beat, shift the skid chain
        // one position toward the slot.  A push below may override
        // one of the positions on the same cycle (non-blocking last-
        // wins) to handle concurrent drain+push while keeping the
        // contiguous-valid invariant.
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

        // =============================================================
        // S_IDLE — accept AR or AW
        // =============================================================
        S_IDLE: begin
            cmd_issued     <= 1'b0;
            psram_started  <= 1'b0;
            wlast_seen     <= 1'b0;
            issue_wait     <= 8'b0;

            // Accept a new AR as soon as both skid entries are free.
            // The held last beat of the previous burst can still be
            // in the slot; the sync-burst startup takes 5+ cycles
            // before the new burst's first beat lands, by which time
            // the master will have drained the previous beat (and the
            // 3-entry pipeline protects any reordering).  This pairs
            // with cpu_target_port's last-beat pre-grant to close the
            // inter-burst gap.
            if (s_axi_arvalid && !rskid_valid && !rskid2_valid) begin
                s_axi_arready <= 1'b1;
                addr_r        <= s_axi_araddr;
                burst_len     <= s_axi_arlen;
                beat_count    <= 8'b0;
                // All reads go through the async single-word path
                // (S_RD_CMD → S_RD_DAT, one psram_rd pulse per AXI
                // beat).  Sync burst produced deterministic musl
                // mallocng corruption after Seek Large; see BCR
                // comment in core_top.v for the investigation
                // summary.  Pairs with BCR=0x9D1F in core_top.v.
                state <= S_RD_CMD;
            end else if (s_axi_awvalid) begin
                s_axi_awready <= 1'b1;
                addr_r        <= s_axi_awaddr;
                burst_len     <= s_axi_awlen;
                beat_count    <= 8'b0;
                if (s_axi_wvalid) begin
                    // Bundled AW+W — capture on the same cycle.
                    s_axi_wready <= 1'b1;
                    psram_wdata  <= s_axi_wdata;
                    psram_wstrb  <= s_axi_wstrb;
                    wlast_seen   <= s_axi_wlast;
                    state        <= S_WR_CMD;
                end else begin
                    state        <= S_WR_NEXT;
                end
            end
        end

        // =============================================================
        // Read path — async single word per AXI beat
        // =============================================================
        S_RD_CMD: begin
            if (!cmd_issued) begin
                if (!psram_busy) begin
                    psram_rd      <= 1'b1;
                    psram_addr    <= addr_r[27:2];
                    cmd_issued    <= 1'b1;
                    psram_started <= 1'b0;
                    issue_wait    <= 8'b0;
                end
            end else begin
                if (!psram_started) begin
                    if (psram_busy) begin
                        psram_started <= 1'b1;
                        issue_wait    <= 8'b0;
                    end else begin
                        issue_wait <= issue_wait + 8'd1;
                        if (&issue_wait) begin
                            // Controller missed the pulse — re-issue.
                            cmd_issued <= 1'b0;
                            issue_wait <= 8'b0;
                        end
                    end
                end
                if (psram_rdata_valid) begin
                    state <= S_RD_DAT;
                end
            end
        end

        S_RD_DAT: begin
            // Dead code while BCR=0x641F (all reads use sync-burst), but
            // kept in case the BCR ever flips back to async page mode.
            // Place a freshly-read word at the earliest empty position
            // in the drain-shifted pipeline; hold if full.
            if (s_axi_rvalid && s_axi_rready) begin
                if (!rskid_valid) begin
                    s_axi_rvalid  <= 1'b1;
                    s_axi_rdata   <= psram_rdata;
                    s_axi_rresp   <= 2'b00;
                    s_axi_rlast   <= beat_is_last;
                end else if (!rskid2_valid) begin
                    rskid_valid <= 1'b1;
                    rskid_data  <= psram_rdata;
                    rskid_last  <= beat_is_last;
                end else begin
                    rskid2_valid <= 1'b1;
                    rskid2_data  <= psram_rdata;
                    rskid2_last  <= beat_is_last;
                end
                beat_count    <= beat_count + 8'd1;
                cmd_issued    <= 1'b0;
                psram_started <= 1'b0;
                if (beat_is_last) state <= S_IDLE;
                else begin
                    addr_r <= addr_r + 32'd4;
                    state  <= S_RD_CMD;
                end
            end else if (!s_axi_rvalid) begin
                s_axi_rvalid  <= 1'b1;
                s_axi_rdata   <= psram_rdata;
                s_axi_rresp   <= 2'b00;
                s_axi_rlast   <= beat_is_last;
                beat_count    <= beat_count + 8'd1;
                cmd_issued    <= 1'b0;
                psram_started <= 1'b0;
                if (beat_is_last) state <= S_IDLE;
                else begin
                    addr_r <= addr_r + 32'd4;
                    state  <= S_RD_CMD;
                end
            end else if (!rskid_valid) begin
                rskid_valid   <= 1'b1;
                rskid_data    <= psram_rdata;
                rskid_last    <= beat_is_last;
                beat_count    <= beat_count + 8'd1;
                cmd_issued    <= 1'b0;
                psram_started <= 1'b0;
                if (beat_is_last) state <= S_IDLE;
                else begin
                    addr_r <= addr_r + 32'd4;
                    state  <= S_RD_CMD;
                end
            end else if (!rskid2_valid) begin
                rskid2_valid  <= 1'b1;
                rskid2_data   <= psram_rdata;
                rskid2_last   <= beat_is_last;
                beat_count    <= beat_count + 8'd1;
                cmd_issued    <= 1'b0;
                psram_started <= 1'b0;
                if (beat_is_last) state <= S_IDLE;
                else begin
                    addr_r <= addr_r + 32'd4;
                    state  <= S_RD_CMD;
                end
            end
            // else: all three full — hold (master is draining very slowly).
        end

        // =============================================================
        // Sync-burst read path — one psram_burst_rd pulse delivers
        // the entire AXI burst via streamed psram_burst_rdata_valid
        // pulses from the CRAM0 controller.  Saw-busy gate defends
        // against false-completion during the request-to-busy
        // window (per the CRAM burst controller's completion
        // signaling semantics).
        // =============================================================
        S_RD_BRST_CMD: begin
            if (!cmd_issued) begin
                if (!psram_busy) begin
                    psram_burst_rd  <= 1'b1;
                    psram_addr      <= addr_r[27:2];
                    psram_burst_len <= burst_len[5:0];
                    cmd_issued      <= 1'b1;
                    psram_started   <= 1'b0;
                    issue_wait      <= 8'b0;
                end
            end else begin
                if (!psram_started) begin
                    if (psram_busy) begin
                        psram_started <= 1'b1;
                        issue_wait    <= 8'b0;
                        state         <= S_RD_BRST_DAT;
                    end else begin
                        issue_wait <= issue_wait + 8'd1;
                        if (&issue_wait) begin
                            // Controller missed the pulse — re-issue.
                            cmd_issued <= 1'b0;
                            issue_wait <= 8'b0;
                        end
                    end
                end
            end
        end

        S_RD_BRST_DAT: begin
            if (psram_burst_rdata_valid) begin
                // Place the streamed beat at the earliest empty
                // position of the drain-shifted pipeline.  With 3
                // entries and the controller emitting ~1 beat / 2
                // cycles, we tolerate ≥4 cycles of continuous master
                // backpressure.  The "all-full" path remains a drop
                // to preserve FSM progress, but it should now be
                // observably unreachable under healthy bus traffic.
                if (s_axi_rvalid && s_axi_rready) begin
                    if (!rskid_valid) begin
                        s_axi_rvalid <= 1'b1;
                        s_axi_rdata  <= psram_burst_rdata;
                        s_axi_rresp  <= 2'b00;
                        s_axi_rlast  <= beat_is_last;
                    end else if (!rskid2_valid) begin
                        rskid_valid <= 1'b1;
                        rskid_data  <= psram_burst_rdata;
                        rskid_last  <= beat_is_last;
                    end else begin
                        rskid2_valid <= 1'b1;
                        rskid2_data  <= psram_burst_rdata;
                        rskid2_last  <= beat_is_last;
                    end
                end else begin
                    if (!s_axi_rvalid) begin
                        s_axi_rvalid <= 1'b1;
                        s_axi_rdata  <= psram_burst_rdata;
                        s_axi_rresp  <= 2'b00;
                        s_axi_rlast  <= beat_is_last;
                    end else if (!rskid_valid) begin
                        rskid_valid <= 1'b1;
                        rskid_data  <= psram_burst_rdata;
                        rskid_last  <= beat_is_last;
                    end else if (!rskid2_valid) begin
                        rskid2_valid <= 1'b1;
                        rskid2_data  <= psram_burst_rdata;
                        rskid2_last  <= beat_is_last;
                    end
                    // else: all three full — drop; master will hang
                    // visibly rather than silently corrupting data.
                end
                beat_count <= beat_count + 8'd1;
                if (beat_is_last) begin
                    cmd_issued    <= 1'b0;
                    psram_started <= 1'b0;
                    state         <= S_IDLE;
                end
            end
        end

        // =============================================================
        // Write path — same-cycle W capture, one psram access per beat
        // =============================================================
        S_WR_NEXT: begin
            if (s_axi_wvalid) begin
                s_axi_wready <= 1'b1;
                psram_wdata  <= s_axi_wdata;
                psram_wstrb  <= s_axi_wstrb;
                wlast_seen   <= s_axi_wlast;
                state        <= S_WR_CMD;
            end
        end

        S_WR_CMD: begin
            if (!cmd_issued) begin
                if (!psram_busy) begin
                    psram_wr      <= 1'b1;
                    psram_addr    <= addr_r[27:2];
                    cmd_issued    <= 1'b1;
                    psram_started <= 1'b0;
                    issue_wait    <= 8'b0;
                end
            end else begin
                if (!psram_started && psram_busy) begin
                    psram_started <= 1'b1;
                    issue_wait    <= 8'b0;
                end else if (!psram_started) begin
                    issue_wait <= issue_wait + 8'd1;
                    if (&issue_wait) begin
                        // Controller missed the pulse — re-issue.
                        cmd_issued <= 1'b0;
                        issue_wait <= 8'b0;
                    end
                end else if (psram_started && !psram_busy) begin
                    state <= S_WR_WAIT;
                end
            end
        end

        S_WR_WAIT: begin
            beat_count    <= beat_count + 8'd1;
            cmd_issued    <= 1'b0;
            psram_started <= 1'b0;

            if (beat_is_last || wlast_seen) begin
                s_axi_bvalid <= 1'b1;
                s_axi_bresp  <= 2'b00;
                state        <= S_WR_RESP;
            end else begin
                addr_r <= addr_r + 32'd4;
                state  <= S_WR_NEXT;
            end
        end

        S_WR_RESP: begin
            // Hold bvalid until the master asserts bready.  The
            // top-of-always handshake block clears bvalid on the
            // transfer cycle — detect that edge to return to IDLE.
            if (!s_axi_bvalid) begin
                state <= S_IDLE;
            end
        end

        default: state <= S_IDLE;

        endcase
    end
end

endmodule
