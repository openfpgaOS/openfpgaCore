/*
 * cram1_burst_mmio.v — CPU-triggered 8-word CRAM1 burst reader.
 *
 * Purpose:
 *   Restores the amortised-fetch path that the retired audio_mixer.v
 *   used to own (per-voice M10K prefetch of 8 samples).  The SW mixer
 *   in swmixer.c now drives this via MMIO: one write kicks off an
 *   8-word burst from a CRAM1 word address, and eight reads drain the
 *   words into a voice-local BRAM cache.  Turns 8 × ~20-cycle uncached
 *   CRAM1 word reads into ~30 cycles of burst + 8 cycles of register
 *   reads — worth ~10 % CPU back on the 1 kHz ISR under 16-voice load.
 *
 * MMIO contract (decoded by axi_periph_slave, region 0x4E000000):
 *   WR 0x00 BURST_ADDR   — 22-bit CRAM1 word address; write starts burst
 *   RD 0x04 BURST_STATUS — bit 0 = busy (1 while burst in flight)
 *   RD 0x08 BURST_DATA   — returns the next word in the buffer and
 *                          auto-advances.  The FIRST eight reads after
 *                          a burst complete return buf[0..7] in order;
 *                          subsequent reads return buf[7] (stale).
 *
 * Typical CPU sequence:
 *   CRAM1_BURST_ADDR = aligned_word_addr;
 *   while (CRAM1_BURST_STATUS & 1) ;      // wait for busy=0
 *   for (int i = 0; i < 8; i++)
 *       cache[i] = CRAM1_BURST_DATA;
 */

`default_nettype none

module cram1_burst_mmio (
    input  wire clk,
    input  wire reset_n,

    /* MMIO side (driven from axi_periph_slave). */
    input  wire        mmio_addr_wr_pulse,   /* 1-cycle pulse on ADDR write */
    input  wire [21:0] mmio_addr_wdata,
    input  wire        mmio_data_rd_pulse,   /* 1-cycle pulse on DATA read */
    output wire        mmio_busy,
    output wire [31:0] mmio_data_q,

    /* CRAM1 burst interface (wires to cram1_controller.burst_*). */
    output reg         burst_rd,
    output reg  [21:0] burst_addr,
    output reg  [4:0]  burst_len,
    input  wire [31:0] burst_q,
    input  wire        burst_q_valid,
    input  wire        burst_busy
);

localparam [4:0] BURST_LEN_M1 = 5'd7;

localparam [1:0] ST_IDLE  = 2'd0;
localparam [1:0] ST_ISSUE = 2'd1;
localparam [1:0] ST_RECV  = 2'd2;

reg [1:0]  state;
reg [31:0] buf0, buf1, buf2, buf3, buf4, buf5, buf6, buf7;
reg [3:0]  recv_count;   /* 0..8 */
reg [2:0]  read_index;   /* 0..7 */

/* Busy until the burst completes (all 8 words received and controller
 * dropped busy).  OR in `mmio_addr_wr_pulse` so that a CPU doing
 * back-to-back ST/LW observes busy=HIGH on the very cycle the write
 * pulse is seen — without this, the CPU's LW can race ahead of the
 * state update and see busy=0 on the cycle *before* the FSM has
 * latched the new request (classic saw-busy gate issue documented in
 * the CRAM1 controller header). */
assign mmio_busy = (state != ST_IDLE) | mmio_addr_wr_pulse;

/* Combinational mux of the 8 buffer entries based on read_index.
 * Reads past index 7 stick at buf7; the CPU is expected to only read
 * 8 words per burst. */
assign mmio_data_q = (read_index == 3'd0) ? buf0 :
                     (read_index == 3'd1) ? buf1 :
                     (read_index == 3'd2) ? buf2 :
                     (read_index == 3'd3) ? buf3 :
                     (read_index == 3'd4) ? buf4 :
                     (read_index == 3'd5) ? buf5 :
                     (read_index == 3'd6) ? buf6 :
                                            buf7;

always @(posedge clk) begin
    if (!reset_n) begin
        state        <= ST_IDLE;
        burst_rd     <= 1'b0;
        burst_addr   <= 22'd0;
        burst_len    <= 5'd0;
        recv_count   <= 4'd0;
        read_index   <= 3'd0;
        buf0 <= 0; buf1 <= 0; buf2 <= 0; buf3 <= 0;
        buf4 <= 0; buf5 <= 0; buf6 <= 0; buf7 <= 0;
    end else begin
        case (state)
            ST_IDLE: begin
                burst_rd <= 1'b0;
                if (mmio_addr_wr_pulse) begin
                    burst_addr <= mmio_addr_wdata;
                    burst_len  <= BURST_LEN_M1;
                    burst_rd   <= 1'b1;
                    recv_count <= 4'd0;
                    read_index <= 3'd0;
                    state      <= ST_ISSUE;
                end else if (mmio_data_rd_pulse && read_index != 3'd7) begin
                    /* CPU draining buffer — advance read pointer.
                     * We cap at 7 so reads past the burst return buf7,
                     * matching the contract in the header comment. */
                    read_index <= read_index + 3'd1;
                end
            end

            ST_ISSUE: begin
                /* Hold burst_rd HIGH until cram1_controller acknowledges
                 * by raising burst_busy.  Per CRAM1 controller's saw-busy
                 * gate — polling must observe busy=HIGH first to detect
                 * the request-to-busy edge reliably. */
                burst_rd <= 1'b1;
                if (burst_busy) begin
                    burst_rd <= 1'b0;
                    state    <= ST_RECV;
                end
            end

            ST_RECV: begin
                burst_rd <= 1'b0;
                /* Stream 8 words into buf0..buf7 as they arrive. */
                if (burst_q_valid) begin
                    case (recv_count[2:0])
                        3'd0: buf0 <= burst_q;
                        3'd1: buf1 <= burst_q;
                        3'd2: buf2 <= burst_q;
                        3'd3: buf3 <= burst_q;
                        3'd4: buf4 <= burst_q;
                        3'd5: buf5 <= burst_q;
                        3'd6: buf6 <= burst_q;
                        3'd7: buf7 <= burst_q;
                    endcase
                    recv_count <= recv_count + 4'd1;
                end
                /* Done when controller drops busy (all words delivered). */
                if (!burst_busy) begin
                    state <= ST_IDLE;
                end
            end

            default: state <= ST_IDLE;
        endcase
    end
end

endmodule
