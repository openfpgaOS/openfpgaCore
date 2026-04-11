//
// AXI4 Slave for CRAM0 — single-word async reads + writes (stage 1).
//
// The burst read path (S_RD_BURST / S_RD_STREAM) is ready but currently
// disabled: hardware tests with sync burst enabled on CRAM0 hang at the
// loader stage for reasons we can't pin down from RTL alone (Verilator
// tests of this same slave all pass, so the functional model is sound).
// Need on-hardware debugging (SignalTap on the burst data path, or a
// logic-analyzer trace of cram0_dq during the first burst read) before
// re-enabling.
//
// Until then, reads go through the single-word async path.  The burst
// datapath + interface are kept wired so the re-enable is a one-line
// change (S_RD_CMD → S_RD_BURST at the S_IDLE transition).
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

    // PSRAM single-word interface (used for writes only)
    output reg         psram_rd,      // kept for the per-target mux; tied low here
    output reg         psram_wr,
    output reg  [25:0] psram_addr,
    output reg  [31:0] psram_wdata,
    output reg  [3:0]  psram_wstrb,
    input  wire [31:0] psram_rdata,
    input  wire        psram_busy,
    input  wire        psram_rdata_valid,

    // PSRAM sync burst read interface (CRAM0 only)
    output reg         psram_burst_rd,
    output reg  [5:0]  psram_burst_len,
    input  wire [31:0] psram_burst_rdata,
    input  wire        psram_burst_rdata_valid
);

wire reset = ~reset_n;

// FSM states
localparam S_IDLE       = 4'd0;
localparam S_RD_BURST   = 4'd1;  // Issue burst_rd pulse (DISABLED — see header)
localparam S_RD_STREAM  = 4'd2;  // Stream burst data onto the AXI R channel
localparam S_WR_CMD     = 4'd3;  // Issue single-word write, wait for busy
localparam S_WR_WAIT    = 4'd4;  // Wait for !busy (write done)
localparam S_WR_NEXT    = 4'd5;  // Accept next W beat
localparam S_RD_CMD     = 4'd6;  // Single-word async read (active path)
localparam S_RD_DAT     = 4'd7;  // Present single-word read data on R channel

reg [3:0] state;

// Transaction tracking
reg [7:0]  burst_len;
reg [7:0]  beat_count;
reg [31:0] addr_r;
reg        cmd_issued;
reg        psram_started;
reg [7:0]  issue_wait;

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
        psram_burst_rd <= 0;
        psram_burst_len <= 0;
    end else begin
        // Defaults (single-cycle pulses clear themselves)
        s_axi_arready <= 0;
        s_axi_awready <= 0;
        s_axi_wready <= 0;
        s_axi_rvalid <= 0;
        s_axi_bvalid <= 0;
        psram_rd <= 0;
        psram_wr <= 0;
        psram_burst_rd <= 0;

        case (state)

        S_IDLE: begin
            cmd_issued <= 0;
            psram_started <= 0;
            issue_wait <= 0;
            if (s_axi_arvalid) begin
                s_axi_arready <= 1;
                addr_r <= s_axi_araddr;
                burst_len <= s_axi_arlen;
                beat_count <= 0;
                state <= S_RD_CMD;   // was S_RD_BURST; see header
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
        // Read path — sync burst (one hardware burst per AXI read)
        // ============================================
        S_RD_BURST: begin
            if (!cmd_issued) begin
                if (!psram_busy) begin
                    psram_burst_rd  <= 1;
                    psram_addr      <= addr_r[27:2];
                    psram_burst_len <= burst_len[5:0];
                    cmd_issued      <= 1;
                    state           <= S_RD_STREAM;
                end
            end
        end

        S_RD_STREAM: begin
            // Accept incoming burst data, forward to the AXI R channel.
            // Simple sliding-window acknowledge: assert rvalid the cycle the
            // data arrives (or keep it asserted until rready on back-pressure).
            if (s_axi_rvalid && s_axi_rready) begin
                beat_count <= beat_count + 1;
                if (beat_is_last) begin
                    cmd_issued <= 0;
                    state <= S_IDLE;
                end else if (psram_burst_rdata_valid) begin
                    s_axi_rvalid <= 1;
                    s_axi_rdata  <= psram_burst_rdata;
                    s_axi_rresp  <= 2'b00;
                    s_axi_rlast  <= ((beat_count + 8'd1) == burst_len);
                end
            end else if (s_axi_rvalid) begin
                s_axi_rvalid <= 1;
            end else if (psram_burst_rdata_valid) begin
                s_axi_rvalid <= 1;
                s_axi_rdata  <= psram_burst_rdata;
                s_axi_rresp  <= 2'b00;
                s_axi_rlast  <= beat_is_last;
            end
        end

        // ============================================
        // Read path — single-word async (active while burst disabled)
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
            s_axi_rvalid <= 1;
            s_axi_rdata <= psram_rdata;
            s_axi_rresp <= 2'b00;
            s_axi_rlast <= beat_is_last;
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
        // Write path (single word per PSRAM access, no burst writes)
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
