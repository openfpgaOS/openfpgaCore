// Audio DMA Engine
//
// Reads stereo samples from an SDRAM ring buffer and pushes them into
// the audio hardware FIFO automatically. The CPU writes samples to SDRAM
// (cached, fast) and updates the write pointer — zero IO bus usage.
//
// Ring buffer format: 32-bit words, each = {right[15:0], left[15:0]}
// Ring buffer lives in SDRAM at a configurable base address.
//
// Control registers (active-low reset):
//   ring_base  - SDRAM byte address of ring buffer start
//   ring_size  - Number of 32-bit entries (must be power of 2)
//   ring_wptr  - Write pointer (updated by CPU, entries not bytes)
//   enable     - Start/stop DMA
//
// The DMA reads when: enabled && (rptr != wptr) && FIFO not full.
// Reads are single-beat (one word at a time) to avoid hogging the bus.

`default_nettype none

module audio_dma (
    input wire clk,
    input wire reset_n,

    // Control interface (from system registers)
    input  wire        enable,
    input  wire [31:0] ring_base,     // SDRAM byte address of ring start
    input  wire [12:0] ring_size_log, // log2(ring_size), e.g. 13 = 8192 entries
    input  wire [12:0] ring_wptr,     // CPU write pointer (entry index)
    output reg  [12:0] ring_rptr,     // Hardware read pointer (entry index)

    // Audio FIFO status (from audio_output, clk_sys domain)
    input  wire [11:0] fifo_level,
    input  wire        fifo_full,

    // Audio FIFO write (to audio_output)
    output reg         sample_wr,
    output reg  [31:0] sample_data,

    // Word-level SDRAM read master (to word_sdram_arbiter M0)
    output reg         m_rd,
    output reg  [23:0] m_addr,
    output wire [3:0]  m_burst_len,
    input  wire [31:0] m_rdata,
    input  wire        m_busy,
    input  wire        m_accepted,
    input  wire        m_rdata_valid
);

// Single-beat reads
assign m_burst_len = 4'd0;

// Ring pointer mask
wire [12:0] ring_mask = (13'd1 << ring_size_log) - 13'd1;

// DMA has data to read?
wire ring_has_data = (ring_rptr != ring_wptr);

// FIFO has space? Keep a margin to avoid overflow during latency
wire fifo_has_space = (fifo_level < 12'd3840) && !fifo_full;

// FSM
localparam S_IDLE = 2'd0;
localparam S_CMD  = 2'd1;  // Hold rd until accepted
localparam S_DATA = 2'd2;  // Wait for rdata_valid

reg [1:0] state;
reg       cmd_accepted;

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        state <= S_IDLE;
        ring_rptr <= 13'd0;
        m_rd <= 1'b0;
        m_addr <= 24'd0;
        sample_wr <= 1'b0;
        sample_data <= 32'd0;
        cmd_accepted <= 1'b0;
    end else begin
        sample_wr <= 1'b0;

        case (state)
        S_IDLE: begin
            m_rd <= 1'b0;
            cmd_accepted <= 1'b0;
            if (enable && ring_has_data && fifo_has_space) begin
                m_rd <= 1'b1;
                m_addr <= ring_base[25:2] + {11'd0, ring_rptr};
                state <= S_CMD;
            end
        end

        S_CMD: begin
            m_rd <= 1'b1;  // Hold until accepted
            if (m_accepted) begin
                m_rd <= 1'b0;
                cmd_accepted <= 1'b1;
                state <= S_DATA;
            end
        end

        S_DATA: begin
            if (m_rdata_valid) begin
                sample_wr <= 1'b1;
                sample_data <= m_rdata;
                ring_rptr <= (ring_rptr + 13'd1) & ring_mask;
                state <= S_IDLE;
            end
        end

        default: state <= S_IDLE;
        endcase
    end
end

endmodule
