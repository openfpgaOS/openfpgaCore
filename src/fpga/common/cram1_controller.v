// CRAM1 Controller — 32-bit word interface on a single 16-bit PSRAM chip.
//
// Modes supported on the same chip (BCR set to sync burst, 0x641F):
//   - Async single-word writes (unchanged from pre-burst design).
//   - Async single-word reads via word_rd — the chip still responds to
//     async ADV#/OE# edges even when BCR selects sync burst mode, same
//     way CRAM0 relies on it.  Latency just matches the BCR latency
//     code (4) instead of the chip's async access time.
//   - Sync burst reads via burst_rd/burst_len — streams halfword pairs
//     from the phy and reassembles 32-bit words with one burst_q_valid
//     pulse per word.  Capped at 16 words by the caller (row-boundary
//     safety, see audio_dma.md risks / word-32 IOB skew bug).
//
// BCR init runs once at reset: writes 0x641F (sync burst, latency code
// 4, WAIT during delay, continuous burst — same value PocketQuake's
// CRAM0 uses in shipping code and what cram1_phy's SYNC_LATENCY=4
// expects).  Both dies (CE0# and CE1#) are configured; word_rd /
// word_wr / burst_rd are gated on bcr_init_done.
//
// Physical-layer protocol lives in cram1_phy.sv.

`default_nettype none

module cram1_controller #(
    parameter CLOCK_SPEED = 100.0
) (
    input wire clk,
    input wire reset_n,

    // 32-bit word interface (async single-word — gated on bcr_init_done)
    input wire         word_rd,
    input wire         word_wr,
    input wire  [21:0] word_addr,
    input wire  [31:0] word_data,
    input wire  [3:0]  word_wstrb,
    output reg  [31:0] word_q,
    output reg         word_busy,
    output reg         word_q_valid,

    // Burst read interface (sync burst — gated on bcr_init_done)
    //   burst_rd        : 1-cycle pulse to start a burst
    //   burst_addr      : starting 32-bit word address (bit 21 = die select)
    //   burst_len       : words minus 1 (0 = 1 word, 15 = 16 words).
    //                     5-bit field even though callers cap at 15;
    //                     leaves room for an eventual 32-word path if
    //                     the word-32 IOB skew bug gets root-caused.
    //   burst_q_valid   : 1-cycle pulse per 32-bit word delivered.
    //   burst_busy      : HIGH from burst_rd accept until the burst
    //                     fully drains (includes phy STATE_SYNC_END).
    input  wire         burst_rd,
    input  wire  [21:0] burst_addr,
    input  wire  [4:0]  burst_len,
    output reg   [31:0] burst_q,
    output reg          burst_q_valid,
    output reg          burst_busy,

    // Set once both dies have been configured; external code can check
    // this (but ST_IDLE gating is also internal, so callers don't have
    // to — they just observe word_busy / burst_busy).
    output reg          bcr_init_done,

    // Physical signals (tristate broken out for pin-level muxing)
    output wire [21:16] cram_a,
    output wire [15:0]  cram_dq_out,
    output wire         cram_dq_oe,
    input  wire [15:0]  cram_dq_in,
    input  wire         cram_wait,
    output wire         cram_clk,
    output wire         cram_adv_n,
    output wire         cram_cre,
    output wire         cram_ce0_n,
    output wire         cram_ce1_n,
    output wire         cram_oe_n,
    output wire         cram_we_n,
    output wire         cram_ub_n,
    output wire         cram_lb_n
);

// BCR value — sync burst, fixed latency code 4, continuous burst.
// Matches PocketQuake's CRAM0 value (shipping code, AS1C8M16PL family).
// cram1_phy.sv SYNC_LATENCY=4 and its WAIT polarity decode
// (HIGH=invalid, LOW=valid during STATE_SYNC_DATA) both line up with
// this value.  See core_top.v discussion of the prior 6-hour debug
// when a CRAM0 bitstream had the chip in the wrong BCR state.
localparam [15:0] BCR_VALUE = 16'h641F;

localparam [4:0] ST_BCR_START_0  = 5'd0;
localparam [4:0] ST_BCR_BSY_0    = 5'd1;
localparam [4:0] ST_BCR_WAI_0    = 5'd2;
localparam [4:0] ST_BCR_START_1  = 5'd3;
localparam [4:0] ST_BCR_BSY_1    = 5'd4;
localparam [4:0] ST_BCR_WAI_1    = 5'd5;
localparam [4:0] ST_IDLE         = 5'd6;
localparam [4:0] ST_WR_LO        = 5'd7;
localparam [4:0] ST_WR_LO_BSY    = 5'd8;
localparam [4:0] ST_WR_LO_WAI    = 5'd9;
localparam [4:0] ST_WR_HI        = 5'd10;
localparam [4:0] ST_WR_HI_BSY    = 5'd11;
localparam [4:0] ST_WR_HI_WAI    = 5'd12;
localparam [4:0] ST_RD_LO        = 5'd13;
localparam [4:0] ST_RD_LO_BSY    = 5'd14;
localparam [4:0] ST_RD_LO_WAI    = 5'd15;
localparam [4:0] ST_RD_HI        = 5'd16;
localparam [4:0] ST_RD_HI_BSY    = 5'd17;
localparam [4:0] ST_RD_HI_WAI    = 5'd18;
localparam [4:0] ST_DONE         = 5'd19;
localparam [4:0] ST_BURST_START  = 5'd20;
localparam [4:0] ST_BURST_LO     = 5'd21;
localparam [4:0] ST_BURST_HI     = 5'd22;
localparam [4:0] ST_BURST_DRAIN  = 5'd23;

reg [4:0]  state;
reg [31:0] latched_data;
reg [21:0] latched_addr;
reg        latched_chip_sel;
reg [3:0]  latched_wstrb;
reg [15:0] lo_captured;

reg [4:0]  burst_words_rem;  // words still to deliver, including the one in flight
reg [15:0] burst_lo_half;

reg         psram_write_en;
reg         psram_read_en;
reg  [21:0] psram_addr;
reg         psram_bank_sel;
reg  [15:0] psram_data_in;
reg         psram_write_high;
reg         psram_write_low;

reg         psram_sync_burst_en;
reg  [5:0]  psram_sync_burst_len;
reg         psram_config_en;
reg  [15:0] psram_config_data;

wire [15:0] psram_data_out;
wire        psram_busy;
wire        psram_read_avail;

wire [21:0] addr_lo = {latched_addr[20:0], 1'b0};
wire [21:0] addr_hi = {latched_addr[20:0], 1'b1};

cram1_phy #(
    .CLOCK_SPEED(CLOCK_SPEED)
) phy (
    .clk(clk),
    .bank_sel(psram_bank_sel),
    .addr(psram_addr),
    .write_en(psram_write_en),
    .data_in(psram_data_in),
    .write_high_byte(psram_write_high),
    .write_low_byte(psram_write_low),
    .read_en(psram_read_en),
    .sync_burst_en(psram_sync_burst_en),
    .sync_burst_len(psram_sync_burst_len),
    .config_en(psram_config_en),
    .config_data(psram_config_data),
    .read_avail(psram_read_avail),
    .data_out(psram_data_out),
    .busy(psram_busy),
    .cram_a(cram_a),
    .cram_dq_out(cram_dq_out),
    .cram_dq_oe(cram_dq_oe),
    .cram_dq_in(cram_dq_in),
    .cram_wait(cram_wait),
    .cram_clk(cram_clk),
    .cram_adv_n(cram_adv_n),
    .cram_cre(cram_cre),
    .cram_ce0_n(cram_ce0_n),
    .cram_ce1_n(cram_ce1_n),
    .cram_oe_n(cram_oe_n),
    .cram_we_n(cram_we_n),
    .cram_ub_n(cram_ub_n),
    .cram_lb_n(cram_lb_n),
    .dbg_wait_seen(),
    .dbg_wait_cycles(),
    .dbg_burst_count(),
    .dbg_stale_count()
);

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        state <= ST_BCR_START_0;
        bcr_init_done <= 1'b0;
        // Present word_busy / burst_busy HIGH while BCR init is
        // running.  Callers check these to gate new requests; if they
        // were LOW during BCR init, a racing word_rd / word_wr /
        // burst_rd pulse would be silently dropped (the controller is
        // not in ST_IDLE, so accept logic never fires) and the caller
        // would deadlock waiting for a transaction that never started.
        word_busy <= 1'b1;
        burst_busy <= 1'b1;
        word_q <= 32'b0;
        word_q_valid <= 1'b0;
        burst_q <= 32'b0;
        burst_q_valid <= 1'b0;
        latched_data <= 32'b0;
        latched_addr <= 22'b0;
        latched_chip_sel <= 1'b0;
        latched_wstrb <= 4'b1111;
        lo_captured <= 16'b0;
        burst_words_rem <= 5'd0;
        burst_lo_half <= 16'b0;
        psram_write_en <= 1'b0;
        psram_read_en <= 1'b0;
        psram_addr <= 22'b0;
        psram_bank_sel <= 1'b0;
        psram_data_in <= 16'b0;
        psram_write_high <= 1'b1;
        psram_write_low <= 1'b1;
        psram_sync_burst_en <= 1'b0;
        psram_sync_burst_len <= 6'd0;
        psram_config_en <= 1'b0;
        psram_config_data <= 16'b0;
    end else begin
        // Defaults
        //   - Async read/write enables: LEVEL signals held until phy goes
        //     busy (pulse-miss race noted in the original file — each BSY
        //     state deasserts after observing busy rise).
        //   - config_en / sync_burst_en: single-cycle pulses.
        //   - word_q_valid / burst_q_valid: single-cycle pulses.
        word_q_valid        <= 1'b0;
        burst_q_valid       <= 1'b0;
        psram_sync_burst_en <= 1'b0;
        psram_config_en     <= 1'b0;

        case (state)
            // =====================================================
            // BCR init — runs once at reset, before anything else.
            // Writes BCR to die 0 then die 1.  Pin mux in core_top
            // decides which controller instance actually reaches the
            // shared physical chip; the other's BCR writes are harmless
            // (outputs muxed away, but internal FSM still advances so
            // bcr_init_done eventually rises).
            // =====================================================
            ST_BCR_START_0: begin
                psram_bank_sel    <= 1'b0;
                psram_config_data <= BCR_VALUE;
                psram_config_en   <= 1'b1;  // 1-cycle pulse
                state <= ST_BCR_BSY_0;
            end
            ST_BCR_BSY_0: if (psram_busy) state <= ST_BCR_WAI_0;
            ST_BCR_WAI_0: if (!psram_busy) state <= ST_BCR_START_1;

            ST_BCR_START_1: begin
                psram_bank_sel    <= 1'b1;
                psram_config_data <= BCR_VALUE;
                psram_config_en   <= 1'b1;
                state <= ST_BCR_BSY_1;
            end
            ST_BCR_BSY_1: if (psram_busy) state <= ST_BCR_WAI_1;
            ST_BCR_WAI_1: if (!psram_busy) begin
                bcr_init_done <= 1'b1;
                state <= ST_IDLE;
            end

            // =====================================================
            // Idle — dispatch incoming word/burst requests.
            // =====================================================
            ST_IDLE: begin
                word_busy  <= 1'b0;
                burst_busy <= 1'b0;
                if (word_wr) begin
                    word_busy <= 1'b1;
                    latched_data     <= word_data;
                    latched_addr     <= word_addr;
                    latched_chip_sel <= word_addr[21];
                    latched_wstrb    <= word_wstrb;
                    if (word_wstrb[1:0] == 2'b00)
                        state <= ST_WR_HI;
                    else
                        state <= ST_WR_LO;
                end else if (word_rd) begin
                    word_busy <= 1'b1;
                    latched_addr     <= word_addr;
                    latched_chip_sel <= word_addr[21];
                    state <= ST_RD_LO;
                end else if (burst_rd) begin
                    burst_busy <= 1'b1;
                    latched_addr     <= burst_addr;
                    latched_chip_sel <= burst_addr[21];
                    // words_rem = N = burst_len + 1
                    burst_words_rem  <= burst_len + 5'd1;
                    state <= ST_BURST_START;
                end
            end

            ST_DONE: begin
                word_busy <= 1'b0;
                state <= ST_IDLE;
            end

            // =====================================================
            // Async writes (unchanged)
            // =====================================================
            ST_WR_LO: begin
                psram_bank_sel   <= latched_chip_sel;
                psram_addr       <= addr_lo;
                psram_data_in    <= latched_data[15:0];
                psram_write_low  <= latched_wstrb[0];
                psram_write_high <= latched_wstrb[1];
                psram_write_en   <= 1'b1;
                state <= ST_WR_LO_BSY;
            end
            ST_WR_LO_BSY: if (psram_busy) begin
                psram_write_en <= 1'b0;
                state <= ST_WR_LO_WAI;
            end
            ST_WR_LO_WAI: if (!psram_busy) begin
                if (latched_wstrb[3:2] == 2'b00) state <= ST_DONE;
                else                             state <= ST_WR_HI;
            end

            ST_WR_HI: begin
                psram_bank_sel   <= latched_chip_sel;
                psram_addr       <= addr_hi;
                psram_data_in    <= latched_data[31:16];
                psram_write_low  <= latched_wstrb[2];
                psram_write_high <= latched_wstrb[3];
                psram_write_en   <= 1'b1;
                state <= ST_WR_HI_BSY;
            end
            ST_WR_HI_BSY: if (psram_busy) begin
                psram_write_en <= 1'b0;
                state <= ST_WR_HI_WAI;
            end
            ST_WR_HI_WAI: if (!psram_busy) state <= ST_DONE;

            // =====================================================
            // Async single-word reads (unchanged)
            // Still use the phy's async read_en path even though the
            // chip is BCR-configured for sync burst; cram0_controller
            // has the same setup and it works in shipping hardware.
            // =====================================================
            ST_RD_LO: begin
                psram_bank_sel <= latched_chip_sel;
                psram_addr     <= addr_lo;
                psram_read_en  <= 1'b1;
                state <= ST_RD_LO_BSY;
            end
            ST_RD_LO_BSY: if (psram_busy) begin
                psram_read_en <= 1'b0;
                state <= ST_RD_LO_WAI;
            end
            ST_RD_LO_WAI: if (!psram_busy) begin
                lo_captured <= psram_data_out;
                state <= ST_RD_HI;
            end

            ST_RD_HI: begin
                psram_bank_sel <= latched_chip_sel;
                psram_addr     <= addr_hi;
                psram_read_en  <= 1'b1;
                state <= ST_RD_HI_BSY;
            end
            ST_RD_HI_BSY: if (psram_busy) begin
                psram_read_en <= 1'b0;
                state <= ST_RD_HI_WAI;
            end
            ST_RD_HI_WAI: if (!psram_busy) begin
                word_q <= {psram_data_out, lo_captured};
                word_q_valid <= 1'b1;
                state <= ST_DONE;
            end

            // =====================================================
            // Sync burst reads.
            //   halfwords = 2 * words
            //   sync_burst_len = halfwords - 1 = 2*burst_len + 1
            // Which is {burst_len[4:0], 1'b1}.
            // =====================================================
            ST_BURST_START: begin
                psram_bank_sel       <= latched_chip_sel;
                psram_addr           <= {latched_addr[20:0], 1'b0};
                psram_sync_burst_len <= {burst_words_rem[4:0], 1'b0} - 6'd1;
                psram_sync_burst_en  <= 1'b1;  // 1-cycle pulse
                state <= ST_BURST_LO;
            end

            ST_BURST_LO: begin
                if (psram_read_avail) begin
                    burst_lo_half <= psram_data_out;
                    state <= ST_BURST_HI;
                end
            end

            ST_BURST_HI: begin
                if (psram_read_avail) begin
                    burst_q       <= {psram_data_out, burst_lo_half};
                    burst_q_valid <= 1'b1;
                    burst_words_rem <= burst_words_rem - 5'd1;
                    if (burst_words_rem == 5'd1)
                        state <= ST_BURST_DRAIN;
                    else
                        state <= ST_BURST_LO;
                end
            end

            // Wait for phy to finish STATE_SYNC_END (busy falls) before
            // releasing burst_busy — prevents a back-to-back burst_rd
            // from landing while the phy still has CE# asserted.
            ST_BURST_DRAIN: if (!psram_busy) begin
                burst_busy <= 1'b0;
                state <= ST_IDLE;
            end

            default: state <= ST_IDLE;
        endcase
    end
end

endmodule
