//
// DMA Engine — SDRAM memory-to-memory copy and fill
//
// Bypasses CPU D-cache: reads/writes SDRAM directly via word-level M1 port
// on the SDRAM arbiter. Uses 16-word burst reads (64 bytes, one cache
// line); writes use burst mode via wr_data_next streaming.
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

    // Word-level SDRAM master (to word_sdram_arbiter M1)
    output reg         m_rd,
    output reg         m_wr,
    output reg  [23:0] m_addr,
    output reg  [31:0] m_wdata,
    output wire [3:0]  m_wstrb,
    output reg  [3:0]  m_burst_len,
    output reg  [3:0]  m_burst_wr_len,
    input  wire [31:0] m_rdata,
    input  wire        m_busy,
    input  wire        m_accepted,
    input  wire        m_rdata_valid,
    input  wire        m_wr_data_next
);

wire reset = ~reset_n;

localparam S_IDLE     = 3'd0;
localparam S_CALC     = 3'd1;
localparam S_RD_CMD   = 3'd2;  // Hold rd until accepted, then stream data
localparam S_RD_DATA  = 3'd3;  // Collect burst read data
localparam S_WR_CMD   = 3'd4;  // Hold wr until accepted
localparam S_WR_BURST = 3'd5;  // Stream write data via wr_data_next
localparam S_WR_DONE  = 3'd6;  // Wait for !busy after write

localparam BURST_MAX  = 16;  // 16 words = 64 bytes per block

reg [2:0] state;
reg [31:0] cur_src, cur_dst, remaining;
reg        is_fill;

// 16-word buffer
reg [31:0] dbuf[0:15];
reg [4:0]  burst_len_calc;  // 0-15 (actual beats = burst_len + 1)
reg [4:0]  beat_idx;
reg        wr_started;

assign busy = (state != S_IDLE);
assign m_wstrb = 4'hF;

always @(posedge clk or posedge reset) begin
    if (reset) begin
        state <= S_IDLE;
        m_rd <= 0;
        m_wr <= 0;
        m_addr <= 0;
        m_wdata <= 0;
        m_burst_len <= 0;
        m_burst_wr_len <= 0;
        cur_src <= 0;
        cur_dst <= 0;
        remaining <= 0;
        is_fill <= 0;
        burst_len_calc <= 0;
        beat_idx <= 0;
        wr_started <= 0;
    end else begin

        case (state)

        S_IDLE: begin
            m_rd <= 0;
            m_wr <= 0;
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
            if (remaining[31:6] != 0)
                burst_len_calc <= 5'd15;
            else
                burst_len_calc <= remaining[5:2] - 5'd1;
            beat_idx <= 0;
            state <= is_fill ? S_WR_CMD : S_RD_CMD;
        end

        // ---- Read: hold rd until accepted ----
        S_RD_CMD: begin
            m_rd <= 1;
            m_addr <= cur_src[25:2];
            m_burst_len <= burst_len_calc[3:0];
            if (m_accepted) begin
                m_rd <= 0;
                state <= S_RD_DATA;
            end
        end

        S_RD_DATA: begin
            m_rd <= 0;
            if (m_rdata_valid) begin
                dbuf[beat_idx[3:0]] <= m_rdata;
                beat_idx <= beat_idx + 1;
                if (beat_idx == burst_len_calc) begin
                    beat_idx <= 0;
                    state <= S_WR_CMD;
                end
            end
        end

        // ---- Write: hold wr until accepted, then stream ----
        S_WR_CMD: begin
            m_wr <= 1;
            m_addr <= cur_dst[25:2];
            m_wdata <= dbuf[0];
            m_burst_wr_len <= burst_len_calc[3:0];
            beat_idx <= 0;
            wr_started <= 0;
            if (m_accepted) begin
                m_wr <= 0;
                if (burst_len_calc == 0)
                    state <= S_WR_DONE;
                else
                    state <= S_WR_BURST;
            end
        end

        S_WR_BURST: begin
            m_wr <= 0;
            // When io_sdram requests next word, provide it
            if (m_wr_data_next) begin
                beat_idx <= beat_idx + 1;
                m_wdata <= dbuf[beat_idx[3:0] + 4'd1];
            end
            if (m_busy) wr_started <= 1;
            if (wr_started && !m_busy) begin
                state <= S_WR_DONE;
            end
        end

        S_WR_DONE: begin
            // Wait for single write or clean up after burst
            if (!m_busy) begin
                cur_src <= cur_src + {24'b0, burst_len_calc + 5'd1, 2'b00};
                cur_dst <= cur_dst + {24'b0, burst_len_calc + 5'd1, 2'b00};
                remaining <= remaining - {24'b0, burst_len_calc + 5'd1, 2'b00};
                if (remaining <= {24'b0, burst_len_calc + 5'd1, 2'b00})
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
