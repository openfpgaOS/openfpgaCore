//
// DMA Engine — SDRAM memory-to-memory copy and fill
//
// Bypasses CPU D-cache: reads/writes SDRAM directly via AXI M1 port
// on the SDRAM arbiter. Uses 16-word burst reads (64 bytes, one cache
// line); writes are single-beat (SDRAM controller limitation).
//
// Modes:
//   Copy: reads from src, writes to dst
//   Fill: writes fill value to dst (no reads)
//
// 0 M10K — 16-word register buffer only.
//

`default_nettype none

module dma_engine (
    input wire clk,
    input wire reset_n,

    // Control interface (from axi_periph_slave)
    input wire [31:0] src_addr,
    input wire [31:0] dst_addr,
    input wire [31:0] length,
    input wire        start,
    input wire        fill_mode,
    output wire       busy,

    // AXI4 Master (to SDRAM arbiter M1)
    output reg         m_arvalid,
    input  wire        m_arready,
    output reg  [31:0] m_araddr,
    output reg  [7:0]  m_arlen,

    input  wire        m_rvalid,
    input  wire [31:0] m_rdata,
    input  wire [1:0]  m_rresp,
    input  wire        m_rlast,

    output reg         m_awvalid,
    input  wire        m_awready,
    output reg  [31:0] m_awaddr,
    output reg  [7:0]  m_awlen,

    output reg         m_wvalid,
    input  wire        m_wready,
    output reg  [31:0] m_wdata,
    output reg  [3:0]  m_wstrb,
    output reg         m_wlast,

    input  wire        m_bvalid,
    input  wire [1:0]  m_bresp
);

wire reset = ~reset_n;

localparam S_IDLE     = 3'd0;
localparam S_CALC     = 3'd1;
localparam S_RD_ADDR  = 3'd2;
localparam S_RD_DATA  = 3'd3;
localparam S_WR_ADDR  = 3'd4;
localparam S_WR_DATA  = 3'd5;
localparam S_WR_RESP  = 3'd6;

localparam BURST_MAX  = 16;  // 16 words = 64 bytes per block

reg [2:0] state;
reg [31:0] cur_src, cur_dst, remaining;
reg        is_fill;

// 16-word buffer
reg [31:0] dbuf[0:15];
reg [4:0]  burst_len;  // 0-15 (actual beats = burst_len + 1)
reg [4:0]  beat_idx;

assign busy = (state != S_IDLE);

always @(posedge clk or posedge reset) begin
    if (reset) begin
        state <= S_IDLE;
        m_arvalid <= 0;
        m_awvalid <= 0;
        m_wvalid <= 0;
        m_wlast <= 0;
        m_wstrb <= 0;
        m_araddr <= 0;
        m_arlen <= 0;
        m_awaddr <= 0;
        m_awlen <= 0;
        m_wdata <= 0;
        cur_src <= 0;
        cur_dst <= 0;
        remaining <= 0;
        is_fill <= 0;
        burst_len <= 0;
        beat_idx <= 0;
    end else begin

        case (state)

        S_IDLE: begin
            m_arvalid <= 0;
            m_awvalid <= 0;
            m_wvalid <= 0;
            if (start && length != 0) begin
                cur_src <= src_addr;
                cur_dst <= dst_addr;
                remaining <= length;
                is_fill <= fill_mode;
                if (fill_mode) begin
                    dbuf[0]  <= src_addr; dbuf[1]  <= src_addr;
                    dbuf[2]  <= src_addr; dbuf[3]  <= src_addr;
                    dbuf[4]  <= src_addr; dbuf[5]  <= src_addr;
                    dbuf[6]  <= src_addr; dbuf[7]  <= src_addr;
                    dbuf[8]  <= src_addr; dbuf[9]  <= src_addr;
                    dbuf[10] <= src_addr; dbuf[11] <= src_addr;
                    dbuf[12] <= src_addr; dbuf[13] <= src_addr;
                    dbuf[14] <= src_addr; dbuf[15] <= src_addr;
                end
                state <= S_CALC;
            end
        end

        S_CALC: begin
            // burst length: min(16, remaining_words) - 1
            if (remaining[31:6] != 0)  // >= 64 bytes = 16 words
                burst_len <= 5'd15;
            else
                burst_len <= remaining[5:2] - 5'd1;
            beat_idx <= 0;
            state <= is_fill ? S_WR_ADDR : S_RD_ADDR;
        end

        // ---- Read ----
        S_RD_ADDR: begin
            m_arvalid <= 1;
            m_araddr <= cur_src;
            m_arlen <= {3'b0, burst_len};
            if (m_arready) begin
                m_arvalid <= 0;
                state <= S_RD_DATA;
            end
        end

        S_RD_DATA: begin
            if (m_rvalid) begin
                dbuf[beat_idx[3:0]] <= m_rdata;
                beat_idx <= beat_idx + 1;
                if (m_rlast) begin
                    beat_idx <= 0;
                    state <= S_WR_ADDR;
                end
            end
        end

        // ---- Write ----
        S_WR_ADDR: begin
            m_awvalid <= 1;
            m_awaddr <= cur_dst;
            m_awlen <= {3'b0, burst_len};
            if (m_awready) begin
                m_awvalid <= 0;
                m_wvalid <= 1;
                m_wdata <= dbuf[0];
                m_wstrb <= 4'hF;
                m_wlast <= (burst_len == 0);
                beat_idx <= 0;
                state <= S_WR_DATA;
            end
        end

        S_WR_DATA: begin
            m_wvalid <= 1;
            m_wdata <= dbuf[beat_idx[3:0]];
            m_wstrb <= 4'hF;
            m_wlast <= (beat_idx == burst_len);
            if (m_wready) begin
                if (beat_idx == burst_len) begin
                    m_wvalid <= 0;
                    m_wlast <= 0;
                    state <= S_WR_RESP;
                end else begin
                    beat_idx <= beat_idx + 1;
                end
            end
        end

        S_WR_RESP: begin
            if (m_bvalid) begin
                cur_src <= cur_src + {24'b0, burst_len + 5'd1, 2'b00};
                cur_dst <= cur_dst + {24'b0, burst_len + 5'd1, 2'b00};
                remaining <= remaining - {24'b0, burst_len + 5'd1, 2'b00};
                if (remaining <= {24'b0, burst_len + 5'd1, 2'b00})
                    state <= S_IDLE;
                else
                    state <= S_CALC;
            end
        end

        default: state <= S_IDLE;

        endcase
    end
end

endmodule
