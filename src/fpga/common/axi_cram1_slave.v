//
// AXI4 Slave for CRAM1 (single-word async only).
//
// CRAM1 sits behind cpu_psram1_cdc and is accessed at clk_74a from a CPU
// domain at clk_cpu. There's no sync burst data path wired through the CDC,
// so all reads are single-word async commands.  Writes are also single-word.
//
// External AXI4 interface; outputs the same psram_controller word-level
// signals as the legacy axi_psram_slave (psram_rd / psram_wr / psram_addr /
// psram_wdata / psram_wstrb / psram_rdata / psram_busy / psram_rdata_valid).
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
    end else begin
        // Defaults
        s_axi_arready <= 0;
        s_axi_awready <= 0;
        s_axi_wready <= 0;
        s_axi_rvalid <= 0;
        s_axi_bvalid <= 0;
        psram_rd <= 0;
        psram_wr <= 0;
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

        default: state <= S_IDLE;

        endcase
    end
end

endmodule
