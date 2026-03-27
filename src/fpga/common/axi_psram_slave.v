//
// AXI4 Slave Wrapper for PSRAM (psram_controller word interface)
//
// Converts AXI4 transactions to the psram_controller word-level protocol.
// CRAM reads use sync burst mode (1 hardware burst per AXI read).
// SRAM reads fall back to single-word operations (no burst support).
// Writes always use single-word async operations (PSRAM has no burst write).
//
// Address mapping: psram_addr[25:22] carries addr[27:24] for target decode:
//   0x0/0x8 → CRAM0, 0x1/0x9 → CRAM1, 0xA → SRAM
//

`default_nettype none

module axi_psram_slave (
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

    // PSRAM word interface (single-word reads/writes, to psram_controller via mux)
    output reg         psram_rd,
    output reg         psram_wr,
    output reg  [25:0] psram_addr,
    output reg  [31:0] psram_wdata,
    output reg  [3:0]  psram_wstrb,
    input  wire [31:0] psram_rdata,
    input  wire        psram_busy,
    input  wire        psram_rdata_valid,

    // PSRAM sync burst read interface (to psram_controller)
    output reg         psram_burst_rd,
    output reg  [5:0]  psram_burst_len,
    input  wire        psram_burst_rdata_valid,
    input  wire [31:0] psram_burst_rdata
);

wire reset = ~reset_n;

// FSM states
localparam S_IDLE       = 4'd0;
localparam S_RD_BURST   = 4'd1;  // Issue burst_rd for CRAM targets
localparam S_RD_STREAM  = 4'd2;  // Stream burst data to AXI R channel
localparam S_RD_CMD     = 4'd3;  // Single-word read for SRAM fallback
localparam S_WR_CMD     = 4'd4;  // Issue word_wr, wait for busy
localparam S_WR_WAIT    = 4'd5;  // Wait for !busy (write done)
localparam S_WR_NEXT    = 4'd6;  // Accept next W beat
localparam S_RD_DAT     = 4'd7;  // Present single-word read data on R channel

reg [3:0] state;

// Transaction tracking
reg [7:0]  burst_len;
reg [7:0]  beat_count;
reg [31:0] addr_r;
reg        cmd_issued;
reg        psram_started;   // busy was seen after issuing command
reg [7:0]  issue_wait;      // Timeout counter for missed commands
reg        is_sram_target;  // Latched: current read targets SRAM (no burst)

wire beat_is_last = (beat_count == burst_len);

// SRAM detection: addr[27:24] == 0xA (no burst support)
wire addr_is_sram  = (s_axi_araddr[27:24] == 4'hA);

always @(posedge clk or posedge reset) begin
    if (reset) begin
        state <= S_IDLE;
        burst_len <= 0;
        beat_count <= 0;
        addr_r <= 0;
        cmd_issued <= 0;
        psram_started <= 0;
        issue_wait <= 0;
        is_sram_target <= 0;

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
        // Defaults
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
                is_sram_target <= addr_is_sram;
                // SRAM: single-word async reads; CRAM0/CRAM1: sync burst reads
                state <= addr_is_sram ? S_RD_CMD : S_RD_BURST;
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
        // Read path — sync burst (CRAM targets only)
        // ============================================
        S_RD_BURST: begin
            if (!cmd_issued) begin
                if (!psram_busy) begin
                    psram_burst_rd <= 1;
                    psram_addr <= addr_r[27:2];
                    psram_burst_len <= burst_len[5:0];
                    cmd_issued <= 1;
                    state <= S_RD_STREAM;
                end
            end
        end

        S_RD_STREAM: begin
            if (s_axi_rvalid && s_axi_rready) begin
                beat_count <= beat_count + 1;
                if (beat_is_last) begin
                    cmd_issued <= 0;
                    state <= S_IDLE;
                end else if (psram_burst_rdata_valid) begin
                    s_axi_rvalid <= 1;
                    s_axi_rdata <= psram_burst_rdata;
                    s_axi_rresp <= 2'b00;
                    s_axi_rlast <= ((beat_count + 8'd1) == burst_len);
                end
            end else if (s_axi_rvalid) begin
                s_axi_rvalid <= 1;
            end else if (psram_burst_rdata_valid) begin
                s_axi_rvalid <= 1;
                s_axi_rdata <= psram_burst_rdata;
                s_axi_rresp <= 2'b00;
                s_axi_rlast <= beat_is_last;
            end
        end

        // ============================================
        // Read path — single word (SRAM fallback)
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

        // ============================================
        default: state <= S_IDLE;

        endcase
    end
end

endmodule
