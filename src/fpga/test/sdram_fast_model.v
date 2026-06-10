//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

//
// sdram_fast_model.v — behavioural SDRAM model for tb_system.v.
//
// Not timing-accurate; designed for functional boot bring-up.  Presents
// the same word-level interface that io_sdram exports, so the exact
// axi_sdram_slave + pulse-adapter stack used on hardware drops in
// unchanged.  Fixed 2-cycle latency on reads; writes complete in a
// single cycle with busy asserted for 1 cycle so the sdram_wr_data_next
// pulse aligns with a familiar FSM shape.
//
// Memory: 64 MB backing (16 Mword × 32-bit).  Word-addressed (byte
// offset = word_addr × 4).
//

`default_nettype none

module sdram_fast_model (
    input  wire        clk,
    input  wire        reset_n,

    // io_sdram-style word interface (mirror of the axi_sdram_slave pulse
    // adapter).  word_rd/word_wr are single-cycle pulses asserted when
    // !word_busy; word_addr is a 24-bit WORD address (bits [25:2] of the
    // CPU byte address).
    input  wire        word_rd,
    input  wire        word_wr,
    input  wire [23:0] word_addr,
    input  wire [31:0] word_data,
    input  wire [3:0]  word_wstrb,
    input  wire [3:0]  word_burst_len,      // N+1 word burst
    input  wire [3:0]  word_burst_wr_len,

    // Beat-1 preload for native burst writes (latched by the pulse adapter
    // from sdram_preload_* at command forward, exactly like io_sdram's
    // word_data_next capture)
    input  wire [31:0] word_data_next,
    input  wire [3:0]  word_wstrb_next,

    output reg  [31:0] word_q,
    output reg         word_busy,
    output reg         word_q_valid,
    output reg         word_wr_data_next,
    output reg         word_wr_done,        // Pulse: write op committed (matches io_sdram ST_WRITE_4→IDLE)

    // Continuation bus for pulled beats (axi_sdram_slave's active_next_*,
    // i.e. the same sdram_next_wdata/strb wires io_sdram consumes)
    input  wire [31:0] burst_wr_direct_data,
    input  wire [3:0]  burst_wr_direct_strb
);

// Full backing matching physical hardware: 64 MB / 4 = 16 Mword plus a wrap-around mask.
// The CPU boots out of 0x10320000+, touches framebuffers, stacks and
// heap all within the 64 MB of SDRAM.
localparam MEM_WORDS = 16 * 1024 * 1024;
localparam MEM_MASK  = MEM_WORDS - 1;
reg [31:0] mem [0:MEM_WORDS-1] /*verilator public_flat_rw*/;

// Masked access — every reference below uses (addr & MEM_MASK).
// The harness preloads using the same mask.

// ============================================================
// FSM
// ============================================================
localparam S_IDLE     = 4'd0;
localparam S_RD_LAT1  = 4'd1;
localparam S_RD_LAT2  = 4'd2;
localparam S_RD_BEAT  = 4'd3;
localparam S_WR_BEAT  = 4'd4;
localparam S_WR_LATCH = 4'd5;

reg [3:0] state;
reg [23:0] addr_r;
reg [3:0]  burst_r;     // N (0-based — N+1 beats total)
reg [3:0]  beat_r;      // beats completed

// Native burst-write rolling preload (mirror of io_sdram's scheme):
// beat 0 rides word_data, beat 1 rides word_data_next, beats 2+ are
// pulled — pull asserted while writing beat N, slave registers the
// selected beat one cycle later, capture lands on beat N+1's write
// edge into the slot freed by the shift in between.
reg [31:0] wdata_cur, wdata_nxt;
reg [3:0]  wstrb_cur, wstrb_nxt;
reg        pull_pend;   // pull issued, capture still outstanding

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        state             <= S_IDLE;
        addr_r            <= 24'd0;
        burst_r           <= 4'd0;
        beat_r            <= 4'd0;
        word_q            <= 32'd0;
        word_busy         <= 1'b0;
        word_q_valid      <= 1'b0;
        word_wr_data_next <= 1'b0;
        word_wr_done      <= 1'b0;
        wdata_cur         <= 32'd0;
        wdata_nxt         <= 32'd0;
        wstrb_cur         <= 4'd0;
        wstrb_nxt         <= 4'd0;
        pull_pend         <= 1'b0;
    end else begin
        // Default: pulses deassert each cycle
        word_q_valid      <= 1'b0;
        word_wr_data_next <= 1'b0;
        word_wr_done      <= 1'b0;

        case (state)

        S_IDLE: begin
            word_busy <= 1'b0;
            if (word_rd) begin
                addr_r    <= word_addr;
                burst_r   <= word_burst_len;
                beat_r    <= 4'd0;
                word_busy <= 1'b1;
                state     <= S_RD_LAT1;
            end else if (word_wr) begin
                addr_r    <= word_addr;
                burst_r   <= word_burst_wr_len;
                beat_r    <= 4'd0;
                word_busy <= 1'b1;
                // Capture beats 0 and 1 at command time (io_sdram does the
                // same with word_data_captured / word_data_next_captured).
                wdata_cur <= word_data;
                wstrb_cur <= word_wstrb;
                wdata_nxt <= word_data_next;
                wstrb_nxt <= word_wstrb_next;
                pull_pend <= 1'b0;
                state     <= S_WR_BEAT;
            end
        end

        S_RD_LAT1: begin
            word_busy <= 1'b1;
            state     <= S_RD_LAT2;
        end

        S_RD_LAT2: begin
            word_busy <= 1'b1;
            // Emit beat 0
            word_q       <= mem[(addr_r + beat_r) & MEM_MASK];
            word_q_valid <= 1'b1;
            beat_r       <= beat_r + 4'd1;
            if (burst_r == 4'd0) begin
                word_busy <= 1'b0;
                state     <= S_IDLE;
            end else begin
                state <= S_RD_BEAT;
            end
        end

        S_RD_BEAT: begin
            word_busy    <= 1'b1;
            word_q       <= mem[(addr_r + beat_r) & MEM_MASK];
            word_q_valid <= 1'b1;
            if (beat_r == burst_r) begin
                // This was the last beat pushed out
                word_busy <= 1'b0;
                state     <= S_IDLE;
            end else begin
                beat_r <= beat_r + 4'd1;
            end
        end

        S_WR_BEAT: begin
            word_busy <= 1'b1;
            // Write the current beat from the rolling-preload registers.
            if (wstrb_cur[0]) mem[(addr_r + beat_r) & MEM_MASK][ 7: 0] <= wdata_cur[ 7: 0];
            if (wstrb_cur[1]) mem[(addr_r + beat_r) & MEM_MASK][15: 8] <= wdata_cur[15: 8];
            if (wstrb_cur[2]) mem[(addr_r + beat_r) & MEM_MASK][23:16] <= wdata_cur[23:16];
            if (wstrb_cur[3]) mem[(addr_r + beat_r) & MEM_MASK][31:24] <= wdata_cur[31:24];

            // Capture the beat requested by the PREVIOUS S_WR_BEAT's pull:
            // the slave registered it on burst_wr_direct_* one cycle ago.
            if (pull_pend) begin
                wdata_nxt <= burst_wr_direct_data;
                wstrb_nxt <= burst_wr_direct_strb;
            end

            if (beat_r == burst_r) begin
                // Last beat — drop busy next cycle to let the slave see
                // the falling edge and emit the B response.
                word_busy    <= 1'b0;
                word_wr_done <= 1'b1;  // Pulse: matches io_sdram ST_WRITE_4→IDLE
                state        <= S_IDLE;
            end else begin
                // Pull beat N+2 if it exists (the next beat is already in
                // the preload slot).  N-2 pulls total for an N-beat burst.
                if ({1'b0, beat_r} + 5'd2 <= {1'b0, burst_r}) begin
                    word_wr_data_next <= 1'b1;
                    pull_pend         <= 1'b1;
                end else begin
                    pull_pend         <= 1'b0;
                end
                beat_r <= beat_r + 4'd1;
                state  <= S_WR_LATCH;
            end
        end

        S_WR_LATCH: begin
            word_busy <= 1'b1;
            // Shift preload -> current between beats (io_sdram's
            // ST_WRITE_3 shift).  The outstanding pull's data is captured
            // on the NEXT S_WR_BEAT edge, after the slave's registered
            // response has settled.
            wdata_cur <= wdata_nxt;
            wstrb_cur <= wstrb_nxt;
            state <= S_WR_BEAT;
        end

        default: state <= S_IDLE;

        endcase
    end
end

endmodule
