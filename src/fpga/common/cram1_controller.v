// CRAM1 Controller — 32-bit word interface on a single 16-bit PSRAM chip
//
// READ PATHS — both go through the chip's sync-burst engine because
// the CRAM0 history (project note: cram_async_in_sync_bcr) proved
// async ADV#/OE# reads HANG when the chip's BCR is set to sync-burst
// mode (0x641F) at 100 MHz.  Two read entry points share one FSM:
//
//   word_rd   — single 32-bit word (saves, sample loader, AXI cache fill)
//               → internally a 1-word sync burst; result lands on word_q
//   burst_rd  — N consecutive 32-bit words (mixer per-voice prefetch)
//               → assembled words stream out on burst_q / burst_q_valid
//
// WRITE PATH — async two-phase (LO halfword then HI halfword) on
// word_wr.  Writes don't have the same sync-burst requirement; the
// existing async write path works fine in BCR=0x641F mode.
//
// CONFIG — config_en / config_data / config_bank_sel pass through to
// the PHY so a core_top BCR-init FSM can program the chip to sync-burst
// mode at boot.  Until config is written, the chip stays in async POR
// mode; ALL reads (word_rd and burst_rd) hang.  Callers wait on
// bcr_init_done before issuing any read.

`default_nettype none

module cram1_controller #(
    parameter CLOCK_SPEED = 74.25
) (
    input wire clk,
    input wire reset_n,

    // 32-bit word interface — async two-phase (LO/HI halfwords).
    input wire         word_rd,
    input wire         word_wr,
    input wire  [21:0] word_addr,
    input wire  [31:0] word_data,
    input wire  [3:0]  word_wstrb,
    output reg  [31:0] word_q,
    output reg         word_busy,
    output reg         word_q_valid,

    // Sync-burst read interface.  burst_rd is a single-cycle pulse;
    // burst_addr is the 22-bit word base; burst_len is (N words - 1)
    // up to 31 (giving max 32-word burst per pulse).  burst_q_valid
    // pulses once per assembled 32-bit word; burst_busy is HIGH for
    // the duration of the access (holds new commands off).
    input  wire         burst_rd,
    input  wire [21:0]  burst_addr,
    input  wire [4:0]   burst_len,
    output reg  [31:0]  burst_q,
    output reg          burst_q_valid,
    output reg          burst_busy,

    // BCR config write — single-cycle pulse on config_en with the BCR
    // value on config_data and the target die on config_bank_sel.
    // core_top's BCR-init FSM pulses config_en twice (once per die) to
    // program sync-burst mode (0x641F) into both halves of the chip.
    // raw_busy mirrors the PHY's busy directly so the external FSM can
    // edge-detect each chip-side write completing; bcr_init_done rises
    // sticky after the controller observes busy fall (handy for callers
    // that only care about "init finished").
    input  wire         config_en,
    input  wire [15:0]  config_data,
    input  wire         config_bank_sel,
    output wire         raw_busy,
    output reg          bcr_init_done,

    // Physical signals (split DQ — top-level pin mux drives cram_dq
    // with the active controller's output enable).
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

localparam [4:0] ST_IDLE        = 5'd0;
localparam [4:0] ST_WR_LO       = 5'd1;
localparam [4:0] ST_WR_LO_BSY   = 5'd2;
localparam [4:0] ST_WR_LO_WAI   = 5'd3;
localparam [4:0] ST_WR_HI       = 5'd4;
localparam [4:0] ST_WR_HI_BSY   = 5'd5;
localparam [4:0] ST_WR_HI_WAI   = 5'd6;
localparam [4:0] ST_DONE        = 5'd7;
// Sync-burst read path — serves BOTH word_rd (1-word burst) and
// burst_rd (N-word burst).  Mirrors cram0_controller's ST_BURST_*.
localparam [4:0] ST_BURST_START = 5'd14;
localparam [4:0] ST_BURST_LO    = 5'd15;
localparam [4:0] ST_BURST_HI    = 5'd16;
localparam [4:0] ST_BURST_DONE  = 5'd17;
// BCR config write — pass-through to PHY then wait for busy fall.
localparam [4:0] ST_CFG_PULSE   = 5'd18;
localparam [4:0] ST_CFG_BUSY    = 5'd19;
localparam [4:0] ST_CFG_WAI     = 5'd20;

reg [4:0] state;
reg [31:0] latched_data;
reg [21:0] latched_addr;
reg latched_chip_sel;
reg [3:0] latched_wstrb;
reg [15:0] lo_captured;

reg         psram_write_en;
reg         psram_read_en;
reg  [21:0] psram_addr;
reg         psram_bank_sel;
reg  [15:0] psram_data_in;
reg         psram_write_high;
reg         psram_write_low;

// Sync-burst control passthrough.
reg         sync_burst_en_r;
reg  [5:0]  sync_burst_len_r;

// Burst tracking.
reg  [4:0]  burst_words_rem;     // 32-bit words remaining (incl current)
reg  [15:0] burst_lo_half;       // latched low halfword
reg         is_burst_op;         // 0 = word_rd port, 1 = burst_rd port

wire [15:0] psram_data_out;
wire        psram_busy;
wire        psram_read_avail;

assign raw_busy = psram_busy;

/* During config writes, the PHY needs the target die on its bank_sel
 * input combinationally with config_en (both are sampled on the same
 * clock edge inside the PHY).  Mux config_bank_sel in whenever
 * config_en is asserted so the external BCR-init FSM in core_top can
 * just drive {config_en, config_bank_sel} together without needing
 * an extra setup cycle. */
wire phy_bank_sel = config_en ? config_bank_sel : psram_bank_sel;

wire [21:0] addr_lo = {latched_addr[20:0], 1'b0};
wire [21:0] addr_hi = {latched_addr[20:0], 1'b1};

cram1_phy #(
    .CLOCK_SPEED(CLOCK_SPEED)
) phy (
    .clk(clk),
    .bank_sel(phy_bank_sel),
    .addr(psram_addr),
    .write_en(psram_write_en),
    .data_in(psram_data_in),
    .write_high_byte(psram_write_high),
    .write_low_byte(psram_write_low),
    .read_en(psram_read_en),
    .sync_burst_en(sync_burst_en_r),
    .sync_burst_len(sync_burst_len_r),
    .config_en(config_en),
    .config_data(config_data),
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
        state <= ST_IDLE;
        word_busy <= 1'b0;
        word_q <= 32'b0;
        word_q_valid <= 1'b0;
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
        sync_burst_en_r <= 1'b0;
        sync_burst_len_r <= 6'd0;
        burst_words_rem <= 5'd0;
        burst_lo_half <= 16'd0;
        burst_q <= 32'd0;
        burst_q_valid <= 1'b0;
        burst_busy <= 1'b0;
        is_burst_op <= 1'b0;
        bcr_init_done <= 1'b0;
    end else begin
        psram_write_en <= 1'b0;
        psram_read_en <= 1'b0;
        word_q_valid <= 1'b0;
        sync_burst_en_r <= 1'b0;
        burst_q_valid <= 1'b0;

        case (state)
            ST_IDLE: begin
                word_busy <= 1'b0;
                burst_busy <= 1'b0;
                /* BCR config takes top priority — runs once at boot
                 * before any data path activity.  After config, all
                 * three command channels (word_wr / word_rd / burst_rd)
                 * accept commands. */
                if (config_en) begin
                    /* Latch the requested die into psram_bank_sel.  PHY
                     * samples bank_sel in STATE_CONFIG_CRE_SETUP which
                     * happens several cycles AFTER the one-cycle config_en
                     * pulse — so we can't rely on the phy_bank_sel mux
                     * (which only holds config_bank_sel through during the
                     * pulse).  Without this latch, every die-1 BCR write
                     * silently lands on die 0 and die 1 stays in async POR. */
                    psram_bank_sel <= config_bank_sel;
                    state <= ST_CFG_PULSE;
                end else if (burst_rd) begin
                    burst_busy <= 1'b1;
                    is_burst_op <= 1'b1;
                    latched_addr <= burst_addr;
                    latched_chip_sel <= burst_addr[21];
                    burst_words_rem <= burst_len + 5'd1;
                    state <= ST_BURST_START;
                end else if (word_wr) begin
                    word_busy <= 1'b1;
                    latched_data <= word_data;
                    latched_addr <= word_addr;
                    latched_chip_sel <= word_addr[21];
                    latched_wstrb <= word_wstrb;
                    if (word_wstrb[1:0] == 2'b00)
                        state <= ST_WR_HI;
                    else
                        state <= ST_WR_LO;
                end else if (word_rd) begin
                    /* Async word_rd hangs in BCR=0x641F mode.  Route
                     * single-word reads through the sync-burst path
                     * with words_rem=1.  is_burst_op=0 steers the
                     * assembled 32-bit word back to word_q in
                     * ST_BURST_HI. */
                    word_busy <= 1'b1;
                    is_burst_op <= 1'b0;
                    latched_addr <= word_addr;
                    latched_chip_sel <= word_addr[21];
                    burst_words_rem <= 5'd1;
                    state <= ST_BURST_START;
                end
            end

            ST_DONE: begin
                word_busy <= 1'b0;
                burst_busy <= 1'b0;
                state <= ST_IDLE;
            end

            // Write LO
            ST_WR_LO: begin
                psram_bank_sel <= latched_chip_sel;
                psram_addr <= addr_lo;
                psram_data_in <= latched_data[15:0];
                psram_write_low <= latched_wstrb[0];
                psram_write_high <= latched_wstrb[1];
                psram_write_en <= 1'b1;
                state <= ST_WR_LO_BSY;
            end
            ST_WR_LO_BSY: if (psram_busy) state <= ST_WR_LO_WAI;
            ST_WR_LO_WAI: begin
                if (!psram_busy) begin
                    if (latched_wstrb[3:2] == 2'b00)
                        state <= ST_DONE;
                    else
                        state <= ST_WR_HI;
                end
            end

            // Write HI
            ST_WR_HI: begin
                psram_bank_sel <= latched_chip_sel;
                psram_addr <= addr_hi;
                psram_data_in <= latched_data[31:16];
                psram_write_low <= latched_wstrb[2];
                psram_write_high <= latched_wstrb[3];
                psram_write_en <= 1'b1;
                state <= ST_WR_HI_BSY;
            end
            ST_WR_HI_BSY: if (psram_busy) state <= ST_WR_HI_WAI;
            ST_WR_HI_WAI: if (!psram_busy) state <= ST_DONE;

            // ============================================
            // Sync burst read path — serves word_rd (1-word) and
            // burst_rd (N-word).  Each 32-bit word = 2 halfword PHY
            // responses streamed back via psram_read_avail.  We
            // assemble pairs into 32-bit words and pulse the right
            // valid (word_q_valid for word_rd, burst_q_valid for
            // burst_rd, gated on is_burst_op).  After the last word,
            // wait for the PHY's busy to drop before releasing
            // word_busy / burst_busy — protects CE# timing for the
            // next access.  Same pattern as cram0_controller.v.
            // ============================================
            ST_BURST_START: begin
                psram_bank_sel <= latched_chip_sel;
                psram_addr <= {latched_addr[20:0], 1'b0};
                sync_burst_en_r <= 1'b1;
                /* total halfwords = 2 × words; len field = halfwords - 1 */
                sync_burst_len_r <= {burst_words_rem[4:0], 1'b0} - 6'd1;
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
                    if (is_burst_op) begin
                        burst_q <= {psram_data_out, burst_lo_half};
                        burst_q_valid <= 1'b1;
                    end else begin
                        word_q <= {psram_data_out, burst_lo_half};
                        word_q_valid <= 1'b1;
                    end
                    burst_words_rem <= burst_words_rem - 5'd1;
                    if (burst_words_rem == 5'd1) begin
                        state <= ST_BURST_DONE;
                    end else begin
                        state <= ST_BURST_LO;
                    end
                end
            end
            ST_BURST_DONE: begin
                /* Wait for PHY to finish STATE_SYNC_END + CE# release */
                if (!psram_busy) begin
                    state <= ST_DONE;
                end
            end

            // ============================================
            // BCR config write
            //
            // The PHY's CRE/WE# sequence runs entirely off our
            // single-cycle config_en pulse to its input — but we have
            // to wait for the chip-side write to complete (busy rises
            // then falls) before raising bcr_init_done so callers know
            // sync-burst is now safe to issue.
            // ============================================
            ST_CFG_PULSE: begin
                /* config_en already pulsed straight to PHY this cycle.
                 * Wait for PHY's busy to rise (acknowledges the write
                 * was picked up). */
                state <= ST_CFG_BUSY;
            end
            ST_CFG_BUSY: begin
                if (psram_busy) state <= ST_CFG_WAI;
            end
            ST_CFG_WAI: begin
                if (!psram_busy) begin
                    bcr_init_done <= 1'b1;
                    state <= ST_DONE;
                end
            end

            default: state <= ST_IDLE;
        endcase
    end
end

endmodule
