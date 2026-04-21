/*
 * audio_dma.v — SDRAM → audio_output ring DMA (AXI4 read master).
 *
 * Streams stereo samples from a CPU-programmed ring buffer in SDRAM
 * into the audio_output dcfifo, so the CPU no longer has to push to
 * AUDIO_SAMPLE on the sample-accurate deadline.  CPU fills the ring
 * in bursts from swmixer output (+ cbo.clean to flush each block
 * from L1 to SDRAM); this block keeps the dcfifo topped up continuously.
 *
 * Why SDRAM (not CRAM1):
 *   - CRAM1 is uncached (PMA main=0), so every CPU write to the ring
 *     was a full controller transaction.  A DMA read master on the
 *     same single-ported controller then serialized with those writes
 *     and starved the Doom loader / renderer.
 *   - SDRAM is cached (main=1) and has its own arbiter fan-in, so
 *     audio fetches ride a dedicated AXI master port and never see
 *     the CRAM1 controller.  The ring lives in the cached alias so
 *     writes hit L1 (~1 cyc/word) and a trailing cbo.clean flushes
 *     each 48-sample block down to physical SDRAM in ~3 cache-line
 *     writebacks.
 *
 * Each ring entry is one stereo pair = 32 bits.  Ring length is in
 * pairs / words / AXI beats — firmware sizes it as a multiple of 8 so
 * no burst straddles the wrap point; the FSM drives arlen = 7
 * unconditionally.  ring_base is a byte address into SDRAM.
 *
 * Cost: ~60 ALMs + a handful of registers.
 */

`default_nettype none

module audio_dma (
    input  wire        clk,
    input  wire        reset_n,

    /* Control (from axi_periph_slave MMIO).
     *   enable    — 0 → DMA idle, read_ptr reset; 1 → stream
     *   ring_base — SDRAM byte address of ring[0]
     *   ring_len  — ring length in stereo pairs / words / beats
     *               (must be a multiple of 8; firmware enforces) */
    input  wire        enable,
    input  wire [31:0] ring_base,
    input  wire [13:0] ring_len,

    /* Status readback — lets firmware compute (cpu_write_idx - read_ptr)
     * to gauge how far ahead the producer is of the consumer. */
    output wire [13:0] read_ptr,

    /* AXI4 read master — wires to axi_sdram_arbiter.m4.  We only need
     * the read channels: AR for requests, R for data.  No back-pressure
     * on R (we always sink immediately into the dcfifo, which has room
     * guaranteed by fifo_level < FILL_THRESHOLD before we issue). */
    output reg         m_axi_arvalid,
    input  wire        m_axi_arready,
    output reg  [31:0] m_axi_araddr,
    output wire [7:0]  m_axi_arlen,
    input  wire        m_axi_rvalid,
    input  wire [31:0] m_axi_rdata,
    input  wire        m_axi_rlast,

    /* Audio FIFO write side — drives the dcfifo wrreq/data in
     * audio_output when it's selected by the DMA-vs-MMIO mux. */
    output reg        sample_wr,
    output reg [31:0] sample_data,
    input  wire [9:0] fifo_level
);

/* Burst size: 8 words = 32 bytes = 8 stereo pairs = ~167 µs of audio
 * at 48 kHz.  Small enough to refill frequently; large enough to
 * amortise the SDRAM row-activate overhead. */
localparam [7:0] BURST_BEATS_M1 = 8'd7;    /* AXI arlen = beats-1 */
localparam [4:0] BURST_BEATS    = 5'd8;
/* Only start a new fetch when the FIFO has room for a full burst plus
 * a margin, so we never backpressure the dcfifo mid-burst. */
localparam [9:0] FILL_THRESHOLD = 10'd1008;   /* start fetch when level < 1008 */

assign m_axi_arlen = BURST_BEATS_M1;

localparam [1:0] ST_IDLE  = 2'd0;
localparam [1:0] ST_AR    = 2'd1;
localparam [1:0] ST_R     = 2'd2;

reg [1:0]  state;
reg [13:0] rp;                    /* current read pointer in pairs/words */

assign read_ptr = rp;

wire [13:0] rp_next8 = rp + 14'd8;
wire [13:0] rp_wrap  = rp_next8 - ring_len;
wire        wrap_now = (rp_next8 >= ring_len);

always @(posedge clk) begin
    if (!reset_n || !enable) begin
        state         <= ST_IDLE;
        rp            <= 14'd0;
        m_axi_arvalid <= 1'b0;
        m_axi_araddr  <= 32'd0;
        sample_wr     <= 1'b0;
        sample_data   <= 32'd0;
    end else begin
        sample_wr <= 1'b0;

        case (state)
            ST_IDLE: begin
                m_axi_arvalid <= 1'b0;
                if (fifo_level < FILL_THRESHOLD) begin
                    /* rp is in words; shift left 2 to get byte offset. */
                    m_axi_araddr  <= ring_base + {16'd0, rp, 2'b00};
                    m_axi_arvalid <= 1'b1;
                    state         <= ST_AR;
                end
            end

            ST_AR: begin
                /* Hold arvalid until arbiter + slave accept the AR.
                 * axi_sdram_arbiter single-transaction design means this
                 * wait is bounded by the longest in-flight transfer of
                 * any other master. */
                if (m_axi_arready) begin
                    m_axi_arvalid <= 1'b0;
                    state         <= ST_R;
                end
            end

            ST_R: begin
                if (m_axi_rvalid) begin
                    sample_wr   <= 1'b1;
                    sample_data <= m_axi_rdata;
                    if (m_axi_rlast) begin
                        /* Last beat — advance rp by 8, wrap at ring_len
                         * (ring_len is a multiple of 8 so one subtraction
                         * is sufficient). */
                        rp    <= wrap_now ? rp_wrap : rp_next8;
                        state <= ST_IDLE;
                    end
                end
            end

            default: state <= ST_IDLE;
        endcase
    end
end

endmodule
