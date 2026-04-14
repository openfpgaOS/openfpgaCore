// CRAM1 Controller — 32-bit word interface on a single 16-bit PSRAM chip.
//
// Modes supported (chip stays in POR-default async page mode 0x9D1F —
// no BCR write):
//   - Async single-word write via word_wr.
//   - Async single-word read via word_rd.
//   - Multi-word burst read via burst_rd / burst_addr / burst_len —
//     implemented internally as N back-to-back async word reads.  Same
//     external contract as a sync-burst interface (one burst_q_valid
//     pulse per 32-bit word, burst_busy held throughout) but no
//     dependency on cram_clk timing or BCR state.
//
// Why async-only: sync-burst BCR (0x641F) was tried (commit bfd1ed0)
// and broke CRAM1 reads on real hardware — cram1_clk runs from a
// different clock than psram1_a (cpu/mixer) and the SDC false-paths
// the I/O.  Sync burst would require a phase-shifted PLL output for
// cram1_clk plus input/output_delay constraints mirroring CRAM0.
// The one burst consumer today (save_prefetch) is fine with N async
// reads: 16-word burst at 74.25 MHz takes ~5.4 µs vs. APF's ms-scale
// dataslot_requestread → first bridge_rd window — 200× headroom.
//
// Physical-layer protocol lives in cram1_phy.sv.

`default_nettype none

module cram1_controller #(
    parameter CLOCK_SPEED = 100.0
) (
    input wire clk,
    input wire reset_n,

    // 32-bit word interface (async single-word)
    input wire         word_rd,
    input wire         word_wr,
    input wire  [21:0] word_addr,
    input wire  [31:0] word_data,
    input wire  [3:0]  word_wstrb,
    output reg  [31:0] word_q,
    output reg         word_busy,
    output reg         word_q_valid,

    // Burst read interface — N back-to-back async reads internally.
    //   burst_rd       : 1-cycle pulse to start a burst.
    //   burst_addr     : starting 32-bit word address (bit 21 = die).
    //   burst_len      : words minus 1 (0 = 1 word, 15 = 16 words).
    //                    5-bit field; callers cap at 15 to stay within
    //                    a CRAM1 row (avoid the word-32 IOB skew bug
    //                    noted in audio_dma.md).
    //   burst_q_valid  : 1-cycle pulse per 32-bit word delivered.
    //   burst_busy     : HIGH from accept until the last word fires.
    input  wire         burst_rd,
    input  wire  [21:0] burst_addr,
    input  wire  [4:0]  burst_len,
    output reg   [31:0] burst_q,
    output reg          burst_q_valid,
    output reg          burst_busy,

    // Held HIGH after reset.  Kept as an output port for compatibility
    // with the previous (BCR-writing) version of this controller —
    // core_top.v consumers can stay wired without conditional logic.
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

localparam [3:0] ST_IDLE       = 4'd0;
localparam [3:0] ST_WR_LO      = 4'd1;
localparam [3:0] ST_WR_LO_BSY  = 4'd2;
localparam [3:0] ST_WR_LO_WAI  = 4'd3;
localparam [3:0] ST_WR_HI      = 4'd4;
localparam [3:0] ST_WR_HI_BSY  = 4'd5;
localparam [3:0] ST_WR_HI_WAI  = 4'd6;
localparam [3:0] ST_RD_LO      = 4'd7;
localparam [3:0] ST_RD_LO_BSY  = 4'd8;
localparam [3:0] ST_RD_LO_WAI  = 4'd9;
localparam [3:0] ST_RD_HI      = 4'd10;
localparam [3:0] ST_RD_HI_BSY  = 4'd11;
localparam [3:0] ST_RD_HI_WAI  = 4'd12;
localparam [3:0] ST_DONE       = 4'd13;

reg [3:0]  state;
reg [31:0] latched_data;
reg [21:0] latched_addr;
reg        latched_chip_sel;
reg [3:0]  latched_wstrb;
reg [15:0] lo_captured;

// Burst tracking — when is_burst is set, the read path's completion
// (ST_RD_HI_WAI) emits via burst_q / burst_q_valid instead of word_q /
// word_q_valid, decrements burst_words_rem, and either loops back to
// ST_RD_LO with the next word's address or exits to ST_IDLE.
reg        is_burst;
reg [4:0]  burst_words_rem;

reg         psram_write_en;
reg         psram_read_en;
reg  [21:0] psram_addr;
reg         psram_bank_sel;
reg  [15:0] psram_data_in;
reg         psram_write_high;
reg         psram_write_low;

wire [15:0] psram_data_out;
wire        psram_busy;

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
    // Sync burst + BCR config tied off — async-only operation.
    .sync_burst_en(1'b0),
    .sync_burst_len(6'd0),
    .config_en(1'b0),
    .config_data(16'd0),
    .read_avail(),
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
        state <= ST_IDLE;
        bcr_init_done <= 1'b1;       // no BCR init needed; tie HIGH
        word_busy <= 1'b0;
        word_q <= 32'b0;
        word_q_valid <= 1'b0;
        burst_q <= 32'b0;
        burst_q_valid <= 1'b0;
        burst_busy <= 1'b0;
        is_burst <= 1'b0;
        burst_words_rem <= 5'd0;
        latched_data <= 32'b0;
        latched_addr <= 22'b0;
        latched_chip_sel <= 1'b0;
        latched_wstrb <= 4'b1111;
        lo_captured <= 16'b0;
        psram_write_en <= 1'b0;
        psram_read_en <= 1'b0;
        psram_addr <= 22'b0;
        psram_bank_sel <= 1'b0;
        psram_data_in <= 16'b0;
        psram_write_high <= 1'b1;
        psram_write_low <= 1'b1;
    end else begin
        // Pulsed outputs default to LOW each cycle.
        word_q_valid  <= 1'b0;
        burst_q_valid <= 1'b0;

        case (state)
            // =====================================================
            // Idle — accept word_wr / word_rd / burst_rd.  burst_rd
            // shares the read FSM via the is_burst flag below.
            // =====================================================
            ST_IDLE: begin
                word_busy  <= 1'b0;
                burst_busy <= 1'b0;
                is_burst   <= 1'b0;
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
                    is_burst   <= 1'b1;
                    latched_addr     <= burst_addr;
                    latched_chip_sel <= burst_addr[21];
                    burst_words_rem  <= burst_len + 5'd1;
                    state <= ST_RD_LO;
                end
            end

            // Reached after every word_rd / word_wr / burst_rd
            // transaction.  Drops both *_busy flags one cycle after
            // the final *_q_valid pulse so external callers can latch
            // the last word before observing busy fall (race safety).
            ST_DONE: begin
                word_busy  <= 1'b0;
                burst_busy <= 1'b0;
                is_burst   <= 1'b0;
                state <= ST_IDLE;
            end

            // =====================================================
            // Async writes
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
            // Async reads — shared by word_rd (is_burst=0) and
            // burst_rd (is_burst=1).  ST_RD_HI_WAI dispatches output
            // to word_q or burst_q based on is_burst, advances to the
            // next word in a burst, or completes.
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
                if (is_burst) begin
                    burst_q       <= {psram_data_out, lo_captured};
                    burst_q_valid <= 1'b1;
                    if (burst_words_rem == 5'd1) begin
                        // Last word delivered — keep burst_busy HIGH
                        // this cycle (caller may be sampling burst_q
                        // on the burst_q_valid pulse), drop it in
                        // ST_DONE next cycle.
                        state <= ST_DONE;
                    end else begin
                        burst_words_rem <= burst_words_rem - 5'd1;
                        // Bump latched_addr to the next 32-bit word
                        // and loop back through the LO/HI sequence.
                        latched_addr <= latched_addr + 22'd1;
                        state <= ST_RD_LO;
                    end
                end else begin
                    word_q       <= {psram_data_out, lo_captured};
                    word_q_valid <= 1'b1;
                    state <= ST_DONE;
                end
            end

            default: state <= ST_IDLE;
        endcase
    end
end

endmodule
