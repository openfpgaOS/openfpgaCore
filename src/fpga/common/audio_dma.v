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
// Reads are single-beat AXI4 (one word at a time) to avoid hogging the bus.

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
    input  wire [10:0] fifo_level,
    input  wire        fifo_full,

    // Audio FIFO write (to audio_output)
    output reg         sample_wr,
    output reg  [31:0] sample_data,

    // AXI4 Read master (to SDRAM arbiter)
    output reg         m_arvalid,
    input  wire        m_arready,
    output reg  [31:0] m_araddr,
    output wire [7:0]  m_arlen,
    output wire [2:0]  m_arsize,
    output wire [1:0]  m_arburst,

    input  wire        m_rvalid,
    output wire        m_rready,
    input  wire [31:0] m_rdata,
    input  wire [1:0]  m_rresp,
    input  wire        m_rlast
);

// Single-beat reads
assign m_arlen = 8'd0;
assign m_arsize = 3'b010;   // 4 bytes
assign m_arburst = 2'b01;   // INCR

// Always ready for read data
assign m_rready = 1'b1;

// Ring pointer mask
wire [12:0] ring_mask = (13'd1 << ring_size_log) - 13'd1;

// DMA has data to read?
wire ring_has_data = (ring_rptr != ring_wptr);

// FIFO has space? Keep a margin to avoid overflow during AXI latency
wire fifo_has_space = (fifo_level < 12'd1920) && !fifo_full;

// FSM
localparam S_IDLE = 2'd0;
localparam S_ADDR = 2'd1;
localparam S_DATA = 2'd2;

reg [1:0] state;

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        state <= S_IDLE;
        ring_rptr <= 13'd0;
        m_arvalid <= 1'b0;
        m_araddr <= 32'd0;
        sample_wr <= 1'b0;
        sample_data <= 32'd0;
    end else begin
        sample_wr <= 1'b0;
        m_arvalid <= 1'b0;

        case (state)
        S_IDLE: begin
            if (enable && ring_has_data && fifo_has_space) begin
                // Issue AXI read for next sample
                m_arvalid <= 1'b1;
                m_araddr <= ring_base + {16'd0, ring_rptr, 2'b00};  // byte address
                state <= S_ADDR;
            end
        end

        S_ADDR: begin
            m_arvalid <= 1'b1;
            if (m_arready) begin
                m_arvalid <= 1'b0;
                state <= S_DATA;
            end
        end

        S_DATA: begin
            if (m_rvalid) begin
                // Push sample to audio FIFO
                sample_wr <= 1'b1;
                sample_data <= m_rdata;
                // Advance read pointer
                ring_rptr <= (ring_rptr + 13'd1) & ring_mask;
                state <= S_IDLE;
            end
        end

        default: state <= S_IDLE;
        endcase
    end
end

endmodule
