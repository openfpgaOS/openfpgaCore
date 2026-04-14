//
// AXI4 Slave for CRAM0 — async single-word reads + same-cycle W
// capture writes.  CRAM0 operates in async page mode (BCR 0x9D1F);
// sync-burst is not used.
//
// Read path (async, single word per AXI beat):
//     S_IDLE → S_RD_CMD → S_RD_DAT → (loop for next beat) → S_IDLE
//   Each AXI beat issues one psram_rd pulse to cram0_controller and
//   waits for psram_rdata_valid.  R channel uses hold-until-rready
//   with a 1-entry skid buffer so master back-pressure doesn't drop
//   beats.
//
// Write path (single-word per PSRAM access):
//     S_IDLE → S_WR_NEXT → S_WR_CMD → S_WR_WAIT → (loop) → S_WR_RESP → S_IDLE
//   - Same-cycle W capture in S_IDLE if both awvalid+wvalid are
//     presented together (bundled AW+W master), else S_WR_NEXT waits
//     for the next W beat and captures on the cycle wvalid is seen.
//   - S_WR_RESP holds s_axi_bvalid until the master asserts bready.
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

    // PSRAM single-word interface — all reads and writes flow through
    // this path.  To re-enable burst later, restore the psram_burst_*
    // ports, the S_RD_BURST / S_RD_STREAM states, and route S_IDLE →
    // S_RD_BURST on `s_axi_arvalid` (see cram0_controller.v for the
    // controller-side burst FSM that is still in place).
    output reg         psram_rd,
    output reg         psram_wr,
    output reg  [25:0] psram_addr,
    output reg  [31:0] psram_wdata,
    output reg  [3:0]  psram_wstrb,
    input  wire [31:0] psram_rdata,
    input  wire        psram_busy,
    input  wire        psram_rdata_valid
);

wire reset = ~reset_n;

// =================================================================
// FSM states
// =================================================================
localparam S_IDLE     = 3'd0;
localparam S_WR_NEXT  = 3'd1;   // wait for next W beat, same-cycle capture
localparam S_WR_CMD   = 3'd2;   // issue psram_wr to the controller
localparam S_WR_WAIT  = 3'd3;   // wait for psram_busy to return low
localparam S_WR_RESP  = 3'd4;   // hold bvalid until bready
localparam S_RD_CMD   = 3'd5;   // async single-word read command
localparam S_RD_DAT   = 3'd6;   // async single-word read data

reg [2:0] state;

// =================================================================
// Transaction tracking
// =================================================================
reg [7:0]  burst_len;       // AxLEN captured at grant
reg [7:0]  beat_count;      // AXI beats delivered / accepted so far
reg [31:0] addr_r;          // running address (byte addr, word-aligned)
reg        cmd_issued;      // psram command accepted flag
reg        psram_started;   // psram_busy has been observed high
reg        wlast_seen;      // master asserted wlast on current burst

wire beat_is_last_internal = (beat_count == burst_len);

// =================================================================
// Async read pipeline — 1-entry skid so S_RD_DAT can park a beat
// when the master isn't ready yet.
// =================================================================
reg [31:0] skid_data;
reg        skid_last;
reg        skid_valid;

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
        psram_addr       <= 26'b0;
        psram_wdata      <= 32'b0;
        psram_wstrb      <= 4'b0;

        skid_data        <= 32'b0;
        skid_last        <= 1'b0;
        skid_valid       <= 1'b0;
    end else begin
        // -------------------------------------------------------------
        // Single-cycle pulse defaults.  The valid outputs (rvalid,
        // bvalid, wready) are NOT in this list — they are explicitly
        // held / dropped by the handshake handlers below.
        // -------------------------------------------------------------
        s_axi_arready    <= 1'b0;
        s_axi_awready    <= 1'b0;
        psram_rd         <= 1'b0;
        psram_wr         <= 1'b0;

        // -------------------------------------------------------------
        // AXI handshake "drop on accept" drivers.  Once we assert a
        // VALID output it stays high until the master samples its
        // corresponding READY high on the same clock edge.
        // -------------------------------------------------------------
        if (s_axi_rvalid && s_axi_rready) s_axi_rvalid <= 1'b0;
        if (s_axi_bvalid && s_axi_bready) s_axi_bvalid <= 1'b0;
        if (s_axi_wvalid && s_axi_wready) s_axi_wready <= 1'b0;

        case (state)

        // =============================================================
        // S_IDLE — accept AR or AW
        // =============================================================
        S_IDLE: begin
            cmd_issued    <= 1'b0;
            psram_started <= 1'b0;
            wlast_seen    <= 1'b0;

            if (s_axi_arvalid) begin
                s_axi_arready <= 1'b1;
                addr_r        <= s_axi_araddr;
                burst_len     <= s_axi_arlen;
                beat_count    <= 8'b0;
                skid_valid    <= 1'b0;
                state         <= S_RD_CMD;
            end else if (s_axi_awvalid) begin
                s_axi_awready <= 1'b1;
                addr_r        <= s_axi_awaddr;
                burst_len     <= s_axi_awlen;
                beat_count    <= 8'b0;
                // If the master already has W on the bus, capture it
                // on the same cycle as AW — the proven bundled-AW+W
                // pattern.  Otherwise wait for it in S_WR_NEXT.
                if (s_axi_wvalid) begin
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
        // Async single-word read path.  Each AXI beat issues one
        // psram_rd pulse and waits for psram_rdata_valid.  Multi-beat
        // AXI reads loop: S_RD_CMD → S_RD_DAT → S_RD_CMD.
        // =============================================================
        S_RD_CMD: begin
            if (!cmd_issued) begin
                if (!psram_busy) begin
                    psram_rd       <= 1'b1;
                    psram_addr     <= addr_r[27:2];
                    cmd_issued     <= 1'b1;
                    psram_started  <= 1'b0;
                end
            end else begin
                if (!psram_started && psram_busy) begin
                    psram_started <= 1'b1;
                end else if (psram_started && !psram_busy) begin
                    // psram transaction completed — move to data phase
                end
                if (psram_rdata_valid) begin
                    state <= S_RD_DAT;
                end
            end
        end

        S_RD_DAT: begin
            // Only advance when the output slot can accept this beat
            // (hold-until-rready + skid, same pattern as the other
            // hold-style slaves we landed earlier in the session).
            if (!s_axi_rvalid ||
                (s_axi_rvalid && s_axi_rready && !skid_valid)) begin
                s_axi_rvalid <= 1'b1;
                s_axi_rdata  <= psram_rdata;
                s_axi_rresp  <= 2'b00;
                s_axi_rlast  <= beat_is_last_internal;
                beat_count   <= beat_count + 8'd1;
                cmd_issued   <= 1'b0;
                psram_started<= 1'b0;
                if (beat_is_last_internal) begin
                    state <= S_IDLE;
                end else begin
                    addr_r <= addr_r + 32'd4;
                    state  <= S_RD_CMD;
                end
            end else if (!skid_valid) begin
                skid_valid <= 1'b1;
                skid_data  <= psram_rdata;
                skid_last  <= beat_is_last_internal;
                beat_count <= beat_count + 8'd1;
                cmd_issued <= 1'b0;
                psram_started<= 1'b0;
                if (beat_is_last_internal) begin
                    state <= S_IDLE;
                end else begin
                    addr_r <= addr_r + 32'd4;
                    state  <= S_RD_CMD;
                end
            end
            // else: both full — hold (master is draining slowly)
        end

        // =============================================================
        // Write path — same-cycle W capture
        // =============================================================
        S_WR_NEXT: begin
            // Wait for the master to present wvalid for the next beat
            // of a burst (or for a single-beat write where AW arrived
            // before W).  When we see wvalid we handshake immediately,
            // capturing wdata on the same cycle — this matches the
            // proven legacy pattern and avoids the one-cycle-late
            // race that corrupts writes.
            if (s_axi_wvalid) begin
                s_axi_wready <= 1'b1;
                psram_wdata  <= s_axi_wdata;
                psram_wstrb  <= s_axi_wstrb;
                wlast_seen   <= s_axi_wlast;
                state        <= S_WR_CMD;
            end
        end

        S_WR_CMD: begin
            // Issue the single-word write to the PSRAM controller.
            // Deterministic: if the controller doesn't accept the pulse
            // on the first !psram_busy cycle, the bug is in the
            // controller and gets fixed there.
            if (!cmd_issued) begin
                if (!psram_busy) begin
                    psram_wr       <= 1'b1;
                    psram_addr     <= addr_r[27:2];
                    cmd_issued     <= 1'b1;
                    psram_started  <= 1'b0;
                end
            end else begin
                if (!psram_started && psram_busy) begin
                    psram_started <= 1'b1;
                end else if (psram_started && !psram_busy) begin
                    state <= S_WR_WAIT;
                end
            end
        end

        S_WR_WAIT: begin
            // The single-word write completed.  Advance the beat count
            // and either go back for another beat or issue the burst
            // response, respecting wlast_seen as the early-terminate
            // indicator per AXI4.
            beat_count     <= beat_count + 8'd1;
            cmd_issued     <= 1'b0;
            psram_started  <= 1'b0;

            if (beat_is_last_internal || wlast_seen) begin
                // Last beat (either by counter or by master assertion).
                s_axi_bvalid <= 1'b1;
                s_axi_bresp  <= 2'b00;
                state        <= S_WR_RESP;
            end else begin
                addr_r <= addr_r + 32'd4;
                state  <= S_WR_NEXT;
            end
        end

        S_WR_RESP: begin
            // Hold s_axi_bvalid until the master asserts bready.  The
            // top-of-always handshake-drop block clears bvalid the
            // cycle the handshake fires, so detect that transition.
            // Until then we stay here — no new AW accepted.
            if (!s_axi_bvalid) begin
                // bvalid has been dropped (handshake already fired).
                state <= S_IDLE;
            end
        end

        default: state <= S_IDLE;

        endcase
    end
end

endmodule
