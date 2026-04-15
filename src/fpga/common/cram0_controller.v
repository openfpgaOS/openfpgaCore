// CRAM0 Controller wrapper for VexRiscv CPU
// Provides 32-bit word interface using two 16-bit PSRAM accesses
// Uses the cram1_phy module (CRAM0/CRAM1 share the AS1C8M16PL phy).

`default_nettype none

module cram0_controller #(
    parameter CLOCK_SPEED = 100.0  // MHz - matches CPU / SDRAM / mp_ram PLL
) (
    input wire clk,
    input wire reset_n,

    // CPU 32-bit word interface
    input wire         word_rd,
    input wire         word_wr,
    input wire  [21:0] word_addr,   // 22-bit word address (4MB word = 16MB byte addressable)
    input wire  [31:0] word_data,
    input wire  [3:0]  word_wstrb,  // Byte enables: [0]=byte0, [1]=byte1, [2]=byte2, [3]=byte3
    output reg  [31:0] word_q,
    output reg         word_busy,
    output reg         word_q_valid,  // Pulses when read data is valid

    // PSRAM physical signals
    output wire [21:16] cram_a,
    inout  wire [15:0]  cram_dq,
    input  wire         cram_wait,
    output wire         cram_clk,
    output wire         cram_adv_n,
    output wire         cram_cre,
    output wire         cram_ce0_n,
    output wire         cram_ce1_n,
    output wire         cram_oe_n,
    output wire         cram_we_n,
    output wire         cram_ub_n,
    output wire         cram_lb_n,

    // BCR config write — single-cycle pulse on config_en with the
    // BCR value on config_data and the target die on config_bank_sel.
    // The wrapper drives the underlying psram_cram0_drv's CRE / WE# /
    // address-on-DQ sequence and holds bank_sel = config_bank_sel for
    // the duration of the chip-side config write. Used by core_top's
    // BCR-init FSM at boot to write the AS1C8M16PL POR-default BCR
    // (0x9D1F = async page mode) into both CRAM0 dies. Defends
    // against the failure mode where a previous bitstream left the
    // chip in sync-burst mode and a subsequent async-mode bitstream
    // can't talk to it until the user power-cycles.
    input  wire         config_en,
    input  wire  [15:0] config_data,
    input  wire         config_bank_sel,

    // Sync burst read interface (active only with PSRAM_BURST_ENABLE)
    input  wire         burst_rd,       // single-cycle pulse
    input  wire  [5:0]  burst_len,      // 32-bit words minus 1
    output reg          burst_rdata_valid,
    output reg   [31:0] burst_rdata,

    // Raw psram_cram0_drv busy — surfaced so core_top's BCR-init
    // FSM can wait for the chip-side config-write to drain without
    // confusing it with word_busy (which gates only async word
    // transactions).
    output wire         raw_busy
);

// State machine for 32-bit to 16-bit conversion
localparam [3:0] ST_IDLE      = 4'd0;
localparam [3:0] ST_LO_START  = 4'd1;
localparam [3:0] ST_LO_BUSY   = 4'd2;  // Wait for busy to go high
localparam [3:0] ST_LO_WAIT   = 4'd3;  // Wait for busy to go low
localparam [3:0] ST_HI_START  = 4'd4;
localparam [3:0] ST_HI_BUSY   = 4'd5;  // Wait for busy to go high
localparam [3:0] ST_HI_WAIT   = 4'd6;  // Wait for busy to go low
localparam [3:0] ST_DONE      = 4'd7;
localparam [3:0] ST_BURST_START = 4'd8;   // Issue sync_burst_en to driver
localparam [3:0] ST_BURST_LO   = 4'd9;   // Wait for low halfword read_avail
localparam [3:0] ST_BURST_HI   = 4'd10;  // Wait for high halfword, assemble word

reg [3:0] state;
reg is_write;
reg [31:0] latched_data;
reg [21:0] latched_addr;
reg latched_chip_sel;
reg [3:0] latched_wstrb;

// Burst read tracking
reg [5:0]  burst_words_rem;    // 32-bit words remaining (including current)
reg [15:0] burst_lo_half;      // latched low halfword during burst

// Signals to psram module
reg psram_write_en;
reg psram_read_en;
reg [21:0] psram_addr;
reg [15:0] psram_data_in;
wire [15:0] psram_data_out;
wire psram_busy;
assign raw_busy = psram_busy;
wire psram_read_avail;
reg psram_bank_sel;
reg psram_write_high_byte;
reg psram_write_low_byte;

// Sync burst control (driven by burst FSM states)
reg sync_burst_en_r;
reg [5:0] sync_burst_len_r;

// ============================================================
// BCR config_in_progress handshake
// ============================================================
// When core_top pulses config_en, latch the requested bank_sel and
// hold it through the entire chip-side config write. The driver
// reads bank_sel only during STATE_CONFIG_CRE_SETUP, several cycles
// after config_en goes low, so we MUST hold the value until the
// driver's busy has risen and fallen again.
//
// Sequence:
//   - config_en pulses → latch config_bank_sel, set in_progress=1
//   - driver enters STATE_CONFIG_CRE_WAIT, busy goes 1 (config_saw_busy)
//   - driver finishes (STATE_CONFIG_HOLD_END → STATE_NONE), busy goes 0
//   - in_progress drops, normal psram_bank_sel resumes
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
        if (psram_busy)
            config_saw_busy <= 1'b1;
        else if (config_saw_busy)
            config_in_progress <= 1'b0;  // busy rose then fell → done
    end
end

// Instantiate the 16-bit PSRAM controller (CRAM0-only fork — see
// psram_cram0_drv.sv. The original `psram` module in psram.sv is
// now exclusively for the CRAM1 / save-data path.)
psram_cram0_drv #(
    .CLOCK_SPEED(CLOCK_SPEED),
    .MAX_ACCESS_TIME_FROM_ADV(80)  // ns — 70 ns chip spec + 10 ns margin at 100 MHz
) psram_inst (
    .clk(clk),

    // bank_sel: during a BCR config write, force the latched value
    // so the wrapper's normal psram_bank_sel (which tracks the most
    // recent async word_addr) doesn't accidentally re-target the
    // wrong die mid-config-write.
    .bank_sel(config_in_progress ? config_bank_sel_latched : psram_bank_sel),
    .addr(psram_addr),

    .write_en(psram_write_en),
    .data_in(psram_data_in),
    .write_high_byte(psram_write_high_byte),
    .write_low_byte(psram_write_low_byte),

    .read_en(psram_read_en),

    // Sync burst inputs — driven by burst FSM when PSRAM_BURST_ENABLE
    // is defined, otherwise tied off (async two-phase path only).
    .sync_burst_en(sync_burst_en_r),
    .sync_burst_len(sync_burst_len_r),

    // BCR config — wired straight through. The driver handles the
    // CRE / WE# / address-on-DQ sequence on the next STATE_NONE.
    .config_en(config_en),
    .config_data(config_data),

    .read_avail(psram_read_avail),
    .data_out(psram_data_out),

    .busy(psram_busy),

    // Physical signals
    .cram_a(cram_a),
    .cram_dq(cram_dq),
    .cram_wait(cram_wait),
    .cram_clk(cram_clk),
    .cram_adv_n(cram_adv_n),
    .cram_cre(cram_cre),
    .cram_ce0_n(cram_ce0_n),
    .cram_ce1_n(cram_ce1_n),
    .cram_oe_n(cram_oe_n),
    .cram_we_n(cram_we_n),
    .cram_ub_n(cram_ub_n),
    .cram_lb_n(cram_lb_n)
);

// Convert 22-bit word address to two 22-bit halfword addresses
// word_addr[21] selects chip (bank_sel)
// word_addr[20:0] * 2 = halfword address
wire [21:0] addr_lo = {word_addr[20:0], 1'b0};  // Low 16-bit half
wire [21:0] addr_hi = {word_addr[20:0], 1'b1};  // High 16-bit half
wire [21:0] latched_addr_lo = {latched_addr[20:0], 1'b0};
wire [21:0] latched_addr_hi = {latched_addr[20:0], 1'b1};

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        state <= ST_IDLE;
        word_busy <= 1'b0;
        word_q <= 32'b0;
        word_q_valid <= 1'b0;
        is_write <= 1'b0;
        latched_data <= 32'b0;
        latched_addr <= 22'b0;
        latched_chip_sel <= 1'b0;
        latched_wstrb <= 4'b1111;
        psram_write_en <= 1'b0;
        psram_read_en <= 1'b0;
        psram_addr <= 22'b0;
        psram_data_in <= 16'b0;
        psram_bank_sel <= 1'b0;
        psram_write_high_byte <= 1'b1;
        psram_write_low_byte <= 1'b1;
        sync_burst_en_r <= 1'b0;
        sync_burst_len_r <= 6'd0;
        burst_words_rem <= 6'd0;
        burst_lo_half <= 16'd0;
        burst_rdata_valid <= 1'b0;
        burst_rdata <= 32'b0;
    end else begin
        // Default: clear single-cycle signals
        psram_write_en <= 1'b0;
        psram_read_en <= 1'b0;
        word_q_valid <= 1'b0;
        sync_burst_en_r <= 1'b0;
        burst_rdata_valid <= 1'b0;

        case (state)
            ST_IDLE: begin
                word_busy <= 1'b0;

                if (burst_rd) begin
                    // Sync burst read: each 32-bit word = 2 halfwords
                    word_busy <= 1'b1;
                    latched_addr <= word_addr;
                    latched_chip_sel <= word_addr[21];
                    burst_words_rem <= burst_len + 6'd1;
                    state <= ST_BURST_START;
                end else if (word_wr || word_rd) begin
                    word_busy <= 1'b1;
                    is_write <= word_wr;
                    latched_data <= word_data;
                    latched_addr <= word_addr;
                    latched_chip_sel <= word_addr[21];
                    latched_wstrb <= word_wr ? word_wstrb : 4'b1111;
                    // Skip low half if no bytes enabled there
                    if (word_wr && word_wstrb[1:0] == 2'b00)
                        state <= ST_HI_START;
                    else
                        state <= ST_LO_START;
                end
            end

            ST_LO_START: begin
                // Start access to low 16 bits
                psram_bank_sel <= latched_chip_sel;
                psram_addr <= latched_addr_lo;
                psram_data_in <= latched_data[15:0];
                psram_write_low_byte <= latched_wstrb[0];
                psram_write_high_byte <= latched_wstrb[1];

                if (is_write)
                    psram_write_en <= 1'b1;
                else
                    psram_read_en <= 1'b1;

                state <= ST_LO_BUSY;
            end

            ST_LO_BUSY: begin
                // Wait for psram to acknowledge (busy goes high)
                if (psram_busy) begin
                    state <= ST_LO_WAIT;
                end
            end

            ST_LO_WAIT: begin
                // Wait for low access to complete (busy goes low)
                if (!psram_busy) begin
                    if (!is_write) begin
                        word_q[15:0] <= psram_data_out;
                    end
                    // Skip high half if no bytes enabled there (write only)
                    if (is_write && latched_wstrb[3:2] == 2'b00)
                        state <= ST_DONE;
                    else
                        state <= ST_HI_START;
                end
            end

            ST_HI_START: begin
                // Start access to high 16 bits
                psram_bank_sel <= latched_chip_sel;
                psram_addr <= latched_addr_hi;
                psram_data_in <= latched_data[31:16];
                psram_write_low_byte <= latched_wstrb[2];
                psram_write_high_byte <= latched_wstrb[3];

                if (is_write)
                    psram_write_en <= 1'b1;
                else
                    psram_read_en <= 1'b1;

                state <= ST_HI_BUSY;
            end

            ST_HI_BUSY: begin
                // Wait for psram to acknowledge (busy goes high)
                if (psram_busy) begin
                    state <= ST_HI_WAIT;
                end
            end

            ST_HI_WAIT: begin
                // Wait for high access to complete (busy goes low)
                if (!psram_busy) begin
                    if (!is_write) begin
                        word_q[31:16] <= psram_data_out;
                    end
                    state <= ST_DONE;
                end
            end

            ST_DONE: begin
                word_busy <= 1'b0;
                if (!is_write) begin
                    word_q_valid <= 1'b1;  // Pulse valid on read completion
                end
                state <= ST_IDLE;
            end

            // ============================================
            // Sync burst read path (PSRAM_BURST_ENABLE)
            // ============================================
            ST_BURST_START: begin
                // Issue sync burst to driver: 2 halfwords per 32-bit word
                psram_bank_sel <= latched_chip_sel;
                psram_addr <= {latched_addr[20:0], 1'b0};  // halfword base
                sync_burst_en_r <= 1'b1;
                // Driver's sync_burst_len = (total halfwords) - 1.
                // For N 32-bit words we need 2N halfwords, so len = 2N-1.
                // Previous formula {burst_words_rem[4:0], 1'b1} is 2N+1
                // (off by +2 halfwords / +1 word) — leaves the driver
                // streaming 1 extra word past what psram_cram0.v consumes,
                // which corrupts CE# timing for the next transaction and
                // shows up as a failure on test_cram0_256k's single-word
                // uncached read path.
                sync_burst_len_r <= {burst_words_rem[4:0], 1'b0} - 6'd1;
                state <= ST_BURST_LO;
            end

            ST_BURST_LO: begin
                // Wait for low halfword from sync burst stream
                if (psram_read_avail) begin
                    burst_lo_half <= psram_data_out;
                    state <= ST_BURST_HI;
                end
            end

            ST_BURST_HI: begin
                // Wait for high halfword, assemble 32-bit word
                if (psram_read_avail) begin
                    burst_rdata <= {psram_data_out, burst_lo_half};
                    burst_rdata_valid <= 1'b1;
                    burst_words_rem <= burst_words_rem - 6'd1;
                    if (burst_words_rem == 6'd1) begin
                        // Last word delivered
                        word_busy <= 1'b0;
                        state <= ST_IDLE;
                    end else begin
                        state <= ST_BURST_LO;
                    end
                end
            end

            default: state <= ST_IDLE;
        endcase
    end
end

endmodule
