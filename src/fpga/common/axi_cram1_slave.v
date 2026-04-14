//
// AXI4 Slave for CRAM1 (single-word async only).
//
// This slave runs on clk_cpu and talks to the cpu-side CRAM1 port
// (psram1_a in core_top), which is the CPU/mixer controller running
// on clk_cpu.  CRAM1 stays in async page mode (POR-default BCR
// 0x9D1F — no BCR write), so all reads and writes are single-word
// async commands.  The save_prefetch module sits on a separate
// burst_rd interface into the same controller.
//
// Word-level downstream signals: psram_rd / psram_wr / psram_addr /
// psram_wdata / psram_wstrb / psram_rdata / psram_busy /
// psram_rdata_valid.
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

    // PSRAM word interface (single-word reads/writes, to CRAM1 controller via mux)
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

// FSM states (single-word path only — no burst)
localparam S_IDLE     = 4'd0;
localparam S_RD_CMD   = 4'd1;
localparam S_RD_DAT   = 4'd2;
localparam S_WR_CMD   = 4'd3;
localparam S_WR_WAIT  = 4'd4;
localparam S_WR_NEXT  = 4'd5;

reg [3:0] state;

// Transaction tracking
reg [7:0]  burst_len;
reg [7:0]  beat_count;
reg [31:0] addr_r;
reg        cmd_issued;
reg        psram_started;   // busy was seen after issuing command
reg [7:0]  issue_wait;      // Timeout counter for missed commands

// 1-entry R skid buffer — catches the rare case where a new psram
// read completion lands while the previous beat's rvalid is still
// held waiting for the master's rready.  Without this, any
// master-side stall drops beats silently (same class of bug that
// axi_sdram_slave had before the hold-until-rready rewrite).
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

        rskid_data  <= 0;
        rskid_last  <= 0;
        rskid_valid <= 0;
    end else begin
        // Defaults.  rvalid / bvalid are NOT in this list — they are
        // hold-until-ready signals and are dropped explicitly by the
        // handshake block below.
        s_axi_arready <= 0;
        s_axi_awready <= 0;
        s_axi_wready <= 0;
        psram_rd <= 0;
        psram_wr <= 0;

        // AXI drop-on-handshake for R / B channels (proper AXI hold).
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

        S_IDLE: begin
            cmd_issued <= 0;
            psram_started <= 0;
            issue_wait <= 0;
            // Don't accept a new AR while the previous burst's last
            // beat is still draining through the R slot or skid —
            // otherwise we'd smash in-flight rdata.
            if (s_axi_arvalid && !s_axi_rvalid && !rskid_valid) begin
                s_axi_arready <= 1;
                addr_r <= s_axi_araddr;
                burst_len <= s_axi_arlen;
                beat_count <= 0;
                state <= S_RD_CMD;
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
        // Read path — single word
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
            // Route the new beat into either the primary R slot (if
            // empty OR just drained this cycle by the handshake-drop
            // block above) or the 1-entry skid.
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
            // else: both full → master is *very* slow; beat is lost.
            // Shouldn't happen in practice — the master's 1-entry
            // slot drains in 1-2 cycles, and CRAM1 single-word reads
            // are 8+ cycles apart.
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
