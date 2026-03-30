// 32-bit Dual-PSRAM Controller (lockstep CRAM0 + CRAM1)
//
// Drives two 16-bit PSRAM chips in lockstep with identical address/control
// signals. CRAM0 provides bits [15:0], CRAM1 provides bits [31:16].
// Every access is 32-bit native — no two-phase LO/HI splitting.
//
// Bandwidth: 2x vs single-chip (32 bits per beat instead of 16)
// Capacity:  32MB preserved (16MB address space × 4 bytes/word)

`default_nettype none

module psram_controller_32 #(
    parameter CLOCK_SPEED = 100.0
) (
    input wire clk,
    input wire reset_n,

    // CPU 32-bit word interface
    input wire         word_rd,
    input wire         word_wr,
    input wire  [21:0] word_addr,   // 22-bit word address (bank_sel = addr[21])
    input wire  [31:0] word_data,
    input wire  [3:0]  word_wstrb,
    output reg  [31:0] word_q,
    output reg         word_busy,
    output reg         word_q_valid,

    // CRAM0 physical signals (low 16 bits)
    output wire [21:16] cram0_a,
    inout  wire [15:0]  cram0_dq,
    input  wire         cram0_wait,
    output wire         cram0_clk,
    output wire         cram0_adv_n,
    output wire         cram0_cre,
    output wire         cram0_ce0_n,
    output wire         cram0_ce1_n,
    output wire         cram0_oe_n,
    output wire         cram0_we_n,
    output wire         cram0_ub_n,
    output wire         cram0_lb_n,

    // CRAM1 physical signals (high 16 bits)
    output wire [21:16] cram1_a,
    inout  wire [15:0]  cram1_dq,
    input  wire         cram1_wait,
    output wire         cram1_clk,
    output wire         cram1_adv_n,
    output wire         cram1_cre,
    output wire         cram1_ce0_n,
    output wire         cram1_ce1_n,
    output wire         cram1_oe_n,
    output wire         cram1_we_n,
    output wire         cram1_ub_n,
    output wire         cram1_lb_n,

    // BCR configuration
    input wire          config_en,
    input wire  [15:0]  config_data,
    input wire          config_bank_sel,

    // Sync burst read
    input wire          burst_rd,
    input wire  [5:0]   burst_len,          // 32-bit words minus 1
    output reg          burst_rdata_valid,
    output reg  [31:0]  burst_rdata,

    // Raw busy (OR of both chips, for BCR init FSM)
    output wire         raw_busy,

    // Debug (from CRAM0 instance)
    output wire         dbg_wait_seen,
    output wire [15:0]  dbg_wait_cycles,
    output wire [15:0]  dbg_burst_count,
    output wire [15:0]  dbg_stale_count
);

// ============================================
// State machine — single-phase, no LO/HI split
// ============================================
localparam [2:0] ST_IDLE        = 3'd0;
localparam [2:0] ST_WR_START    = 3'd1;
localparam [2:0] ST_WR_BUSY     = 3'd2;
localparam [2:0] ST_WR_WAIT     = 3'd3;
localparam [2:0] ST_DONE        = 3'd4;
localparam [2:0] ST_BURST_START = 3'd5;
localparam [2:0] ST_BURST_DATA  = 3'd6;
localparam [2:0] ST_BURST_DONE  = 3'd7;

reg [2:0] state;
reg is_write;
reg [31:0] latched_data;
reg [21:0] latched_addr;
reg latched_chip_sel;
reg [3:0] latched_wstrb;
reg [5:0] burst_words_remaining;

// ============================================
// Shared control signals to both psram.sv instances
// ============================================
reg         psram_write_en;
reg         psram_read_en;
reg  [21:0] psram_addr;
reg         psram_bank_sel;
reg         psram_sync_burst_en;
reg  [5:0]  psram_sync_burst_len;

// Per-chip data and byte enables
reg  [15:0] psram_lo_data_in;
reg         psram_lo_write_high;
reg         psram_lo_write_low;

reg  [15:0] psram_hi_data_in;
reg         psram_hi_write_high;
reg         psram_hi_write_low;

// Per-chip outputs
wire [15:0] psram_lo_data_out;
wire        psram_lo_busy;
wire        psram_lo_read_avail;

wire [15:0] psram_hi_data_out;
wire        psram_hi_busy;
wire        psram_hi_read_avail;

// Combined busy: either chip busy = controller busy
assign raw_busy = psram_lo_busy | psram_hi_busy;

// Combined read_avail: both chips ready (AND for safety)
wire both_read_avail = psram_lo_read_avail & psram_hi_read_avail;

// ============================================
// BCR config latching (same as original controller)
// ============================================
reg config_bank_sel_latched;
reg config_in_progress;
reg config_saw_busy;

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        config_bank_sel_latched <= 1'b0;
        config_in_progress <= 1'b0;
        config_saw_busy <= 1'b0;
    end else if (config_en) begin
        config_bank_sel_latched <= config_bank_sel;
        config_in_progress <= 1'b1;
        config_saw_busy <= 1'b0;
    end else if (config_in_progress) begin
        if (raw_busy)
            config_saw_busy <= 1'b1;
        else if (config_saw_busy)
            config_in_progress <= 1'b0;
    end
end

wire psram_bank_sel_mux = config_in_progress ? config_bank_sel_latched : psram_bank_sel;

// ============================================
// CRAM0 — low 16 bits
// ============================================
psram #(
    .CLOCK_SPEED(CLOCK_SPEED)
) psram_lo (
    .clk(clk),
    .bank_sel(psram_bank_sel_mux),
    .addr(psram_addr),
    .write_en(psram_write_en),
    .data_in(psram_lo_data_in),
    .write_high_byte(psram_lo_write_high),
    .write_low_byte(psram_lo_write_low),
    .read_en(psram_read_en),
    .sync_burst_en(psram_sync_burst_en),
    .sync_burst_len(psram_sync_burst_len),
    .config_en(config_en),
    .config_data(config_data),
    .read_avail(psram_lo_read_avail),
    .data_out(psram_lo_data_out),
    .busy(psram_lo_busy),
    .cram_a(cram0_a),
    .cram_dq(cram0_dq),
    .cram_wait(cram0_wait),
    .cram_clk(cram0_clk),
    .cram_adv_n(cram0_adv_n),
    .cram_cre(cram0_cre),
    .cram_ce0_n(cram0_ce0_n),
    .cram_ce1_n(cram0_ce1_n),
    .cram_oe_n(cram0_oe_n),
    .cram_we_n(cram0_we_n),
    .cram_ub_n(cram0_ub_n),
    .cram_lb_n(cram0_lb_n),
    .dbg_wait_seen(dbg_wait_seen),
    .dbg_wait_cycles(dbg_wait_cycles),
    .dbg_burst_count(dbg_burst_count),
    .dbg_stale_count(dbg_stale_count)
);

// ============================================
// CRAM1 — high 16 bits
// ============================================
psram #(
    .CLOCK_SPEED(CLOCK_SPEED)
) psram_hi (
    .clk(clk),
    .bank_sel(psram_bank_sel_mux),
    .addr(psram_addr),
    .write_en(psram_write_en),
    .data_in(psram_hi_data_in),
    .write_high_byte(psram_hi_write_high),
    .write_low_byte(psram_hi_write_low),
    .read_en(psram_read_en),
    .sync_burst_en(psram_sync_burst_en),
    .sync_burst_len(psram_sync_burst_len),
    .config_en(config_en),
    .config_data(config_data),
    .read_avail(psram_hi_read_avail),
    .data_out(psram_hi_data_out),
    .busy(psram_hi_busy),
    .cram_a(cram1_a),
    .cram_dq(cram1_dq),
    .cram_wait(cram1_wait),
    .cram_clk(cram1_clk),
    .cram_adv_n(cram1_adv_n),
    .cram_cre(cram1_cre),
    .cram_ce0_n(cram1_ce0_n),
    .cram_ce1_n(cram1_ce1_n),
    .cram_oe_n(cram1_oe_n),
    .cram_we_n(cram1_we_n),
    .cram_ub_n(cram1_ub_n),
    .cram_lb_n(cram1_lb_n),
    .dbg_wait_seen(),
    .dbg_wait_cycles(),
    .dbg_burst_count(),
    .dbg_stale_count()
);

// ============================================
// Main state machine
// ============================================
always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        state <= ST_IDLE;
        is_write <= 1'b0;
        word_busy <= 1'b0;
        word_q <= 32'b0;
        word_q_valid <= 1'b0;
        latched_data <= 32'b0;
        latched_addr <= 22'b0;
        latched_chip_sel <= 1'b0;
        latched_wstrb <= 4'b1111;
        psram_write_en <= 1'b0;
        psram_read_en <= 1'b0;
        psram_addr <= 22'b0;
        psram_bank_sel <= 1'b0;
        psram_lo_data_in <= 16'b0;
        psram_lo_write_high <= 1'b1;
        psram_lo_write_low <= 1'b1;
        psram_hi_data_in <= 16'b0;
        psram_hi_write_high <= 1'b1;
        psram_hi_write_low <= 1'b1;
        psram_sync_burst_en <= 1'b0;
        psram_sync_burst_len <= 6'b0;
        burst_words_remaining <= 6'b0;
        burst_rdata_valid <= 1'b0;
        burst_rdata <= 32'b0;
    end else begin
        // Clear single-cycle signals
        psram_write_en <= 1'b0;
        psram_read_en <= 1'b0;
        psram_sync_burst_en <= 1'b0;
        word_q_valid <= 1'b0;
        burst_rdata_valid <= 1'b0;

        case (state)
            ST_IDLE: begin
                word_busy <= 1'b0;

                if (burst_rd) begin
                    word_busy <= 1'b1;
                    latched_addr <= word_addr;
                    latched_chip_sel <= word_addr[21];
                    burst_words_remaining <= burst_len;
                    state <= ST_BURST_START;
                end else if (word_wr || word_rd) begin
                    word_busy <= 1'b1;
                    is_write <= word_wr;
                    latched_data <= word_data;
                    latched_addr <= word_addr;
                    latched_chip_sel <= word_addr[21];
                    latched_wstrb <= word_wr ? word_wstrb : 4'b1111;
                    state <= ST_WR_START;
                end
            end

            ST_DONE: begin
                word_busy <= 1'b0;
                state <= ST_IDLE;
            end

            // ============================================
            // Single-phase write/read — both chips simultaneously
            // ============================================
            ST_WR_START: begin
                psram_bank_sel <= latched_chip_sel;
                // Both chips get the same word address (no halfword splitting)
                psram_addr <= latched_addr;

                // CRAM0 (low): data[15:0], wstrb[1:0]
                psram_lo_data_in <= latched_data[15:0];
                psram_lo_write_low <= latched_wstrb[0];
                psram_lo_write_high <= latched_wstrb[1];

                // CRAM1 (high): data[31:16], wstrb[3:2]
                psram_hi_data_in <= latched_data[31:16];
                psram_hi_write_low <= latched_wstrb[2];
                psram_hi_write_high <= latched_wstrb[3];

                if (is_write)
                    psram_write_en <= 1'b1;
                else
                    psram_read_en <= 1'b1;

                state <= ST_WR_BUSY;
            end

            ST_WR_BUSY: begin
                if (raw_busy)
                    state <= ST_WR_WAIT;
            end

            ST_WR_WAIT: begin
                if (!raw_busy) begin
                    if (!is_write) begin
                        word_q <= {psram_hi_data_out, psram_lo_data_out};
                        word_q_valid <= 1'b1;
                    end
                    state <= ST_DONE;
                end
            end

            // ============================================
            // Sync burst read — both chips burst simultaneously
            // Each read_avail pulse delivers 32 bits (16 from each chip)
            // No LO/HI assembly needed.
            // ============================================
            ST_BURST_START: begin
                psram_bank_sel <= latched_chip_sel;
                // Same address to both chips; each delivers 16 bits per beat
                psram_addr <= latched_addr;
                psram_sync_burst_en <= 1'b1;
                // burst_len is in 32-bit words; each chip delivers one 16-bit
                // halfword per beat, so psram.sv burst_len = word burst_len
                psram_sync_burst_len <= burst_words_remaining;
                state <= ST_BURST_DATA;
            end

            ST_BURST_DATA: begin
                if (both_read_avail) begin
                    burst_rdata <= {psram_hi_data_out, psram_lo_data_out};
                    burst_rdata_valid <= 1'b1;
                    if (burst_words_remaining == 6'd0) begin
                        state <= ST_BURST_DONE;
                    end else begin
                        burst_words_remaining <= burst_words_remaining - 6'd1;
                    end
                end
            end

            ST_BURST_DONE: begin
                if (!raw_busy) begin
                    word_busy <= 1'b0;
                    state <= ST_IDLE;
                end
            end

            default: state <= ST_IDLE;
        endcase
    end
end

endmodule
