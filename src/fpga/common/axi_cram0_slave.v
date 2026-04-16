//
// AXI4 Slave for CRAM0 — async single-word reads + same-cycle W capture
// writes.  One PSRAM access per AXI beat; multi-beat AXI bursts loop
// through the FSM.  Matches the axi_cram1_slave layout.
//
// Read path:
//     S_IDLE → S_RD_CMD → S_RD_DAT → (loop for next beat) → S_IDLE
//   Each AXI beat issues one psram_rd pulse to the underlying
//   psram_cram0 wrapper and waits for psram_rdata_valid.  The R
//   channel uses hold-until-rready with a 1-entry skid buffer so
//   master back-pressure doesn't drop beats.
//
// Write path:
//     S_IDLE → [S_WR_NEXT] → S_WR_CMD → S_WR_WAIT → (loop) → S_WR_RESP → S_IDLE
//   - Same-cycle W capture in S_IDLE when AW+W arrive bundled,
//     otherwise S_WR_NEXT waits for the next W beat and captures on
//     the cycle wvalid is seen.
//   - S_WR_RESP holds s_axi_bvalid until the master asserts bready.
//
// Both paths carry a short issue_wait timeout so a dropped psram
// request pulse (rare) re-issues the command instead of hanging.
// Symmetric with axi_cram1_slave.
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
    input  wire        psram_rdata_valid
);

wire reset = ~reset_n;

// =================================================================
// FSM states
// =================================================================
localparam S_IDLE     = 3'd0;
localparam S_RD_CMD   = 3'd1;
localparam S_RD_DAT   = 3'd2;
localparam S_WR_NEXT  = 3'd3;
localparam S_WR_CMD   = 3'd4;
localparam S_WR_WAIT  = 3'd5;
localparam S_WR_RESP  = 3'd6;

reg [2:0] state;

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

// 1-entry R skid buffer — catches the rare case where a new psram
// read completion lands while the previous beat's rvalid is still
// held waiting for the master's rready.
reg [31:0] rskid_data;
reg        rskid_last;
reg        rskid_valid;

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
        psram_addr       <= 26'b0;
        psram_wdata      <= 32'b0;
        psram_wstrb      <= 4'b0;

        rskid_data       <= 32'b0;
        rskid_last       <= 1'b0;
        rskid_valid      <= 1'b0;
    end else begin
        // Single-cycle pulse defaults.  rvalid / bvalid are NOT in
        // this list — they are hold-until-ready signals and are
        // dropped explicitly by the handshake block below.
        s_axi_arready    <= 1'b0;
        s_axi_awready    <= 1'b0;
        s_axi_wready     <= 1'b0;
        psram_rd         <= 1'b0;
        psram_wr         <= 1'b0;

        // AXI "drop on accept" drivers for R / B channels.  On R,
        // promote the skid into the slot on the same cycle if one
        // is parked.
        if (s_axi_rvalid && s_axi_rready) begin
            if (rskid_valid) begin
                s_axi_rdata  <= rskid_data;
                s_axi_rlast  <= rskid_last;
                rskid_valid  <= 1'b0;
                // rvalid stays high — skid promoted into slot.
            end else begin
                s_axi_rvalid <= 1'b0;
            end
        end
        if (s_axi_bvalid && s_axi_bready) s_axi_bvalid <= 1'b0;

        case (state)

        // =============================================================
        // S_IDLE — accept AR or AW
        // =============================================================
        S_IDLE: begin
            cmd_issued    <= 1'b0;
            psram_started <= 1'b0;
            wlast_seen    <= 1'b0;
            issue_wait    <= 8'b0;

            // Don't accept a new AR while the previous burst's last
            // beat is still draining through the R slot or skid.
            if (s_axi_arvalid && !s_axi_rvalid && !rskid_valid) begin
                s_axi_arready <= 1'b1;
                addr_r        <= s_axi_araddr;
                burst_len     <= s_axi_arlen;
                beat_count    <= 8'b0;
                state         <= S_RD_CMD;
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
            // Advance only when the output slot can accept this beat
            // (hold-until-rready + 1-entry skid).
            if (!s_axi_rvalid ||
                (s_axi_rvalid && s_axi_rready && !rskid_valid)) begin
                s_axi_rvalid  <= 1'b1;
                s_axi_rdata   <= psram_rdata;
                s_axi_rresp   <= 2'b00;
                s_axi_rlast   <= beat_is_last;
                beat_count    <= beat_count + 8'd1;
                cmd_issued    <= 1'b0;
                psram_started <= 1'b0;
                if (beat_is_last) begin
                    state <= S_IDLE;
                end else begin
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
                if (beat_is_last) begin
                    state <= S_IDLE;
                end else begin
                    addr_r <= addr_r + 32'd4;
                    state  <= S_RD_CMD;
                end
            end
            // else: both full — hold (master is draining slowly).
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
