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

    output reg  [31:0] word_q,
    output reg         word_busy,
    output reg         word_q_valid,
    output reg         word_wr_data_next,

    // Pre-staged next word (driven combinatorially by axi_sdram_slave)
    input  wire [31:0] burst_wr_direct_data,
    input  wire [3:0]  burst_wr_direct_strb
);

// Scaled-down backing: 4 MB / 4 = 1 Mword plus a wrap-around mask.
// The CPU boots out of 0x10320000+, touches framebuffers, stacks and
// heap all within the bottom 4 MB of SDRAM, so wrapping higher
// addresses into this window doesn't collide.  Gives a 2^20 = 1M-word
// array that fits comfortably in a stack-allocated Verilator instance.
localparam MEM_WORDS = 1 * 1024 * 1024;
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
    end else begin
        // Default: pulses deassert each cycle
        word_q_valid      <= 1'b0;
        word_wr_data_next <= 1'b0;

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
                // First beat's data arrives via word_data on the same
                // cycle; latch it in S_WR_BEAT.
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
            // Write the beat that's currently on word_data.  For the
            // first beat (beat_r == 0) the slave placed it on word_data
            // before asserting word_wr; for subsequent beats the slave
            // forwards on word_wr_data_next pulses into word_data.
            if (word_wstrb[0]) mem[(addr_r + beat_r) & MEM_MASK][ 7: 0] <= word_data[ 7: 0];
            if (word_wstrb[1]) mem[(addr_r + beat_r) & MEM_MASK][15: 8] <= word_data[15: 8];
            if (word_wstrb[2]) mem[(addr_r + beat_r) & MEM_MASK][23:16] <= word_data[23:16];
            if (word_wstrb[3]) mem[(addr_r + beat_r) & MEM_MASK][31:24] <= word_data[31:24];

            if (beat_r == burst_r) begin
                // Last beat — drop busy next cycle to let the slave see
                // the falling edge and emit the B response.
                word_busy <= 1'b0;
                state     <= S_IDLE;
            end else begin
                // Pull the next beat from the slave's pre-staged word.
                word_wr_data_next <= 1'b1;
                beat_r            <= beat_r + 4'd1;
                state             <= S_WR_LATCH;
            end
        end

        S_WR_LATCH: begin
            word_busy <= 1'b1;
            // The axi_sdram_slave promoted next_wdata into sdram_wdata
            // on its wr_data_next pulse (one cycle earlier); the pulse
            // adapter in tb_system forwards it into word_data with a
            // 1-cycle delay.  Latch the new value now.
            state <= S_WR_BEAT;
        end

        default: state <= S_IDLE;

        endcase
    end
end

endmodule
