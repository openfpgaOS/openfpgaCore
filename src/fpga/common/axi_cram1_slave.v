//
// AXI4 Slave for CRAM1 — with both single-word and burst-read paths.
//
// Single-word reads (arlen=0) keep going through the legacy
// psram_rd / psram_rdata / psram_rdata_valid word interface.
//
// Multi-beat reads (arlen>0) — used by the CPU's L1 D$ for cache
// line fills — go through the controller's burst_rd port: one
// sync-burst command returns up to 32 consecutive 32-bit words
// with burst_q_valid pulsing once per word.  Without this path a
// 16-beat line fill is 16 × ~20-cycle single-word reads = ~320 cyc;
// with it, one ~30-cycle burst returns all 16 words (~10× faster).
//
// Writes remain single-word — CRAM1 writes are rare and small.
//

`default_nettype none

module axi_cram1_slave (
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

    // PSRAM word interface (single-word reads/writes, to CRAM1 controller)
    output reg         psram_rd,
    output reg         psram_wr,
    output reg  [25:0] psram_addr,
    output reg  [31:0] psram_wdata,
    output reg  [3:0]  psram_wstrb,
    input  wire [31:0] psram_rdata,
    input  wire        psram_busy,
    input  wire        psram_rdata_valid,

    // PSRAM burst-read interface (multi-beat AXI reads get routed here)
    output reg         psram_burst_rd,
    output reg  [21:0] psram_burst_addr,
    output reg  [4:0]  psram_burst_len,
    input  wire [31:0] psram_burst_q,
    input  wire        psram_burst_q_valid,
    input  wire        psram_burst_busy
);

wire reset = ~reset_n;

// FSM states
localparam S_IDLE       = 4'd0;
localparam S_RD_CMD     = 4'd1;  // single-word read command
localparam S_RD_DAT     = 4'd2;  // single-word read data
localparam S_WR_CMD     = 4'd3;
localparam S_WR_WAIT    = 4'd4;
localparam S_WR_NEXT    = 4'd5;
localparam S_BURST_ISSUE= 4'd6;  // multi-beat read: hold burst_rd until busy
localparam S_BURST_RECV = 4'd7;  // multi-beat read: stream burst_q→R channel

reg [3:0] state;

// Transaction tracking
reg [7:0]  burst_len;
reg [7:0]  beat_count;
reg [31:0] addr_r;
reg        cmd_issued;
reg        psram_started;   // busy was seen after issuing command
reg [7:0]  issue_wait;      // Timeout counter for missed commands

// 1-entry R skid buffer
reg [31:0] rskid_data;
reg        rskid_last;
reg        rskid_valid;

wire beat_is_last = (beat_count == burst_len);

always @(posedge clk or posedge reset) begin
    if (reset) begin
        state <= S_IDLE;
        burst_len <= 0;
        beat_count <= 0;
        addr_r <= 0;
        cmd_issued <= 0;
        psram_started <= 0;
        issue_wait <= 0;

        s_axi_arready <= 0;
        s_axi_rvalid <= 0;
        s_axi_rdata <= 0;
        s_axi_rresp <= 0;
        s_axi_rlast <= 0;
        s_axi_awready <= 0;
        s_axi_wready <= 0;
        s_axi_bvalid <= 0;
        s_axi_bresp <= 0;

        psram_rd <= 0;
        psram_wr <= 0;
        psram_addr <= 0;
        psram_wdata <= 0;
        psram_wstrb <= 0;

        psram_burst_rd   <= 0;
        psram_burst_addr <= 0;
        psram_burst_len  <= 0;

        rskid_data  <= 0;
        rskid_last  <= 0;
        rskid_valid <= 0;
    end else begin
        // Defaults.
        s_axi_arready <= 0;
        s_axi_awready <= 0;
        s_axi_wready <= 0;
        psram_rd <= 0;
        psram_wr <= 0;

        // AXI drop-on-handshake for R / B channels.
        if (s_axi_rvalid && s_axi_rready) begin
            if (rskid_valid) begin
                s_axi_rdata  <= rskid_data;
                s_axi_rlast  <= rskid_last;
                rskid_valid  <= 1'b0;
            end else begin
                s_axi_rvalid <= 1'b0;
            end
        end
        if (s_axi_bvalid && s_axi_bready) s_axi_bvalid <= 1'b0;

        case (state)

        S_IDLE: begin
            cmd_issued <= 0;
            psram_started <= 0;
            issue_wait <= 0;
            if (s_axi_arvalid && !s_axi_rvalid && !rskid_valid) begin
                s_axi_arready <= 1;
                addr_r <= s_axi_araddr;
                burst_len <= s_axi_arlen;
                beat_count <= 0;
                // Multi-beat reads (cache line fills, etc.) go through
                // the controller's burst port.  Single-word reads keep
                // the legacy path.  burst_len max is 5-bit (31 = 32-word
                // burst), which covers every reasonable AXI arlen we
                // care about (VexiiRiscv line fill = arlen 15).
                if (s_axi_arlen != 8'd0 && s_axi_arlen <= 8'd31) begin
                    psram_burst_addr <= s_axi_araddr[23:2];
                    psram_burst_len  <= s_axi_arlen[4:0];
                    psram_burst_rd   <= 1'b1;
                    state <= S_BURST_ISSUE;
                end else begin
                    state <= S_RD_CMD;
                end
            end else if (s_axi_awvalid) begin
                s_axi_awready <= 1;
                addr_r <= s_axi_awaddr;
                burst_len <= s_axi_awlen;
                beat_count <= 0;
                if (s_axi_wvalid) begin
                    s_axi_wready <= 1;
                    psram_wdata <= s_axi_wdata;
                    psram_wstrb <= s_axi_wstrb;
                    state <= S_WR_CMD;
                end else begin
                    state <= S_WR_NEXT;
                end
            end
        end

        // ============================================
        // Single-word read path (AXI arlen == 0)
        // ============================================
        S_RD_CMD: begin
            if (!cmd_issued) begin
                if (!psram_busy) begin
                    psram_rd <= 1;
                    psram_addr <= addr_r[27:2];
                    cmd_issued <= 1;
                    psram_started <= 0;
                    issue_wait <= 0;
                end
            end else begin
                if (!psram_started) begin
                    if (psram_busy) begin
                        psram_started <= 1;
                        issue_wait <= 0;
                    end else begin
                        issue_wait <= issue_wait + 1;
                        if (&issue_wait) begin
                            cmd_issued <= 0;
                            issue_wait <= 0;
                        end
                    end
                end
                if (psram_rdata_valid) begin
                    state <= S_RD_DAT;
                end
            end
        end

        S_RD_DAT: begin
            if (!s_axi_rvalid ||
                (s_axi_rvalid && s_axi_rready && !rskid_valid)) begin
                s_axi_rvalid <= 1;
                s_axi_rdata  <= psram_rdata;
                s_axi_rresp  <= 2'b00;
                s_axi_rlast  <= beat_is_last;
            end else if (!rskid_valid) begin
                rskid_valid <= 1'b1;
                rskid_data  <= psram_rdata;
                rskid_last  <= beat_is_last;
            end
            beat_count <= beat_count + 1;
            cmd_issued <= 0;
            psram_started <= 0;
            if (beat_is_last) begin
                state <= S_IDLE;
            end else begin
                addr_r <= addr_r + 32'd4;
                state <= S_RD_CMD;
            end
        end

        // ============================================
        // Burst read path (AXI arlen > 0)
        //
        // Uses the controller's burst_rd port: one sync-burst command
        // returns arlen+1 consecutive 32-bit words, pulsing burst_q_valid
        // once per word.  We mirror the saw-busy gate pattern from the
        // old cram1_burst_mmio (hold burst_rd until burst_busy rises,
        // then drop it) so the controller reliably latches the request
        // even if it is briefly servicing a word_rd / word_wr.
        // ============================================
        S_BURST_ISSUE: begin
            psram_burst_rd <= 1'b1;
            if (psram_burst_busy) begin
                psram_burst_rd <= 1'b0;
                state <= S_BURST_RECV;
            end
        end

        S_BURST_RECV: begin
            psram_burst_rd <= 1'b0;
            if (psram_burst_q_valid) begin
                if (!s_axi_rvalid ||
                    (s_axi_rvalid && s_axi_rready && !rskid_valid)) begin
                    s_axi_rvalid <= 1'b1;
                    s_axi_rdata  <= psram_burst_q;
                    s_axi_rresp  <= 2'b00;
                    s_axi_rlast  <= beat_is_last;
                end else if (!rskid_valid) begin
                    rskid_valid <= 1'b1;
                    rskid_data  <= psram_burst_q;
                    rskid_last  <= beat_is_last;
                end
                beat_count <= beat_count + 1;
                if (beat_is_last) begin
                    state <= S_IDLE;
                end
            end
        end

        // ============================================
        // Write path (single word per PSRAM access)
        // ============================================
        S_WR_CMD: begin
            if (!cmd_issued) begin
                if (!psram_busy) begin
                    psram_wr <= 1;
                    psram_addr <= addr_r[27:2];
                    cmd_issued <= 1;
                    psram_started <= 0;
                    issue_wait <= 0;
                end
            end else begin
                if (!psram_started && psram_busy) begin
                    psram_started <= 1;
                    issue_wait <= 0;
                end else if (!psram_started) begin
                    issue_wait <= issue_wait + 1;
                    if (&issue_wait) begin
                        cmd_issued <= 0;
                        issue_wait <= 0;
                    end
                end else if (psram_started && !psram_busy) begin
                    state <= S_WR_WAIT;
                end
            end
        end

        S_WR_WAIT: begin
            beat_count <= beat_count + 1;
            cmd_issued <= 0;
            psram_started <= 0;
            if (beat_is_last) begin
                s_axi_bvalid <= 1;
                s_axi_bresp <= 2'b00;
                state <= S_IDLE;
            end else begin
                addr_r <= addr_r + 32'd4;
                state <= S_WR_NEXT;
            end
        end

        S_WR_NEXT: begin
            if (s_axi_wvalid) begin
                s_axi_wready <= 1;
                psram_wdata <= s_axi_wdata;
                psram_wstrb <= s_axi_wstrb;
                state <= S_WR_CMD;
            end
        end

        default: state <= S_IDLE;
        endcase
    end
end

endmodule
