//
// Behavioral CRAM Chip Model for Verilator Testing
//
// Models a cellular PSRAM (e.g., IS66WVS4M16EBLL) with:
//   - Async single-word write (ADV# address latch, WE# data capture)
//   - Async single-word read (ADV# address latch, OE# data output)
//   - Synchronous burst read (ADV# address latch, WAIT/data streaming)
//   - BCR configuration write (CRE high — parses and validates BCR value)
//   - Two dies selected by CE0#/CE1# (bank_sel)
//
// Uses cram_clk (phase-shifted from controller clk) for sync burst timing,
// matching real hardware where PLL provides cram_clk with phase offset.
//

`default_nettype none

module cram_chip_model #(
    parameter DEFAULT_BURST_LATENCY = 4,  // Default before BCR config
    parameter POWERUP_CYCLES = 50         // Cycles after reset before accepting commands
)(
    input  wire        clk,       // Controller clock (for async ops + edge detect)
    input  wire        cram_clk,  // Phase-shifted clock (for sync burst data output)
    input  wire        reset_n,   // For power-up sequencing

    // Address bus
    input  wire [21:16] cram_a,

    // Split DQ bus
    input  wire [15:0] cram_dq_in,
    output wire [15:0] cram_dq_out,   // Muxed: async read (combo) or burst (registered)
    input  wire        cram_dq_oe,    // 1 = controller driving DQ

    // Control signals
    output reg         cram_wait_out,
    input  wire        cram_adv_n,
    input  wire        cram_cre,
    input  wire        cram_ce0_n,
    input  wire        cram_ce1_n,
    input  wire        cram_oe_n,
    input  wire        cram_we_n,
    input  wire        cram_ub_n,
    input  wire        cram_lb_n,
    input  wire        cram_wr_strobe,  // Pulse: capture current DQ data for burst write

    // Backdoor preload port (test-only, for C++ harness).
    // Writes a 32-bit word as two halfwords (low=bit 0 of halfword
    // addr, high=bit 1) so the host can stream a full word per cycle.
    // bd_word_addr: bit 20 selects bank, bits 19:0 are WORD index
    // within that bank (two halfwords per word).
    input  wire        bd_we,
    input  wire [20:0] bd_word_addr,
    input  wire [31:0] bd_wdata_word,

    // Error reporting
    output reg  [15:0] error_count
);

// Split 32-bit word into two halfwords at 2*word_idx and 2*word_idx+1.
// Word addressing is packed: halfword_idx = {word_idx[18:0], lo_hi}
// within the selected bank.
always @(posedge clk) begin
    if (bd_we) begin
        if (bd_word_addr[20]) begin
            mem1[{bd_word_addr[18:0], 1'b0}] <= bd_wdata_word[15:0];
            mem1[{bd_word_addr[18:0], 1'b1}] <= bd_wdata_word[31:16];
        end else begin
            mem0[{bd_word_addr[18:0], 1'b0}] <= bd_wdata_word[15:0];
            mem0[{bd_word_addr[18:0], 1'b1}] <= bd_wdata_word[31:16];
        end
    end
end

// Memory: 2 banks, 20-bit addressing per bank (1M × 16 = 2MB per bank)
localparam BANK_BITS = 20;
reg [15:0] mem0 [0:(1<<BANK_BITS)-1];
reg [15:0] mem1 [0:(1<<BANK_BITS)-1];

// BCR configuration (parsed from config write)
reg        bcr_sync_mode;       // Bit 15 = 0 → sync burst enabled
reg        bcr_wait_active_high;// Bit 10 = 1 → WAIT active high
reg [2:0]  bcr_latency_code;   // Bits 6:4 → latency code
reg [3:0]  bcr_configured_latency;
reg        bcr_valid;           // At least one valid BCR write received

// Power-up sequencing
reg [7:0] powerup_cnt;
reg       powered_up;

// Edge detection (on controller clk)
reg adv_n_prev;
reg we_n_prev;
wire adv_n_fall = adv_n_prev && !cram_adv_n;
wire we_n_rise  = !we_n_prev && cram_we_n;
wire chip_active = !cram_ce0_n || !cram_ce1_n;
wire bank_sel = !cram_ce1_n;

// FSM (runs on controller clk for address latch and writes)
localparam [2:0] ST_IDLE        = 3'd0;
localparam [2:0] ST_ADDR_LATCH  = 3'd1;
localparam [2:0] ST_WRITE       = 3'd2;
localparam [2:0] ST_READ_ASYNC  = 3'd3;
localparam [2:0] ST_BURST_SETUP = 3'd4;  // Hand off to cram_clk domain
localparam [2:0] ST_BURST_ACTIVE= 3'd5;  // Burst running on cram_clk
localparam [2:0] ST_BURST_WRITE = 3'd6;  // Sync burst write: data in each cycle

reg [2:0] state;
reg [21:0] latched_addr;
reg        latched_bank;
reg        latched_is_write;
reg        latched_is_cre;
reg [15:0] captured_wr_data;
reg        captured_ub, captured_lb;

// Sync burst state (runs on cram_clk)
reg        burst_active;
reg [3:0]  burst_latency_cnt;
reg [BANK_BITS-1:0] burst_addr;
reg        burst_bank;
reg        burst_data_valid;
reg        oe_seen;  // Tracks whether OE# went low during async read
reg        wr_strobe_prev;  // Previous cram_wr_strobe for edge detection

// Output mux: async read is combinational, burst is registered
reg [15:0] burst_dq_out;  // Registered output from cram_clk domain
wire async_read_active = (state == ST_READ_ASYNC) && !cram_oe_n;
wire [15:0] async_dq = latched_bank ? mem1[latched_addr[BANK_BITS-1:0]]
                                     : mem0[latched_addr[BANK_BITS-1:0]];
assign cram_dq_out = async_read_active ? async_dq :
                     burst_active      ? burst_dq_out : 16'h0;

integer i;
initial begin
    state = ST_IDLE;
    burst_dq_out = 16'h0;
    cram_wait_out = 1'b0;
    adv_n_prev = 1'b1;
    we_n_prev = 1'b1;
    latched_addr = 0;
    latched_bank = 0;
    latched_is_write = 0;
    latched_is_cre = 0;
    captured_wr_data = 0;
    captured_ub = 0;
    captured_lb = 0;
    burst_active = 0;
    burst_latency_cnt = 0;
    burst_addr = 0;
    burst_bank = 0;
    burst_data_valid = 0;
    oe_seen = 0;
    wr_strobe_prev = 0;
    error_count = 0;
    powerup_cnt = 0;
    powered_up = 0;
    bcr_sync_mode = 0;
    bcr_wait_active_high = 1;
    bcr_latency_code = 3'd1;
    bcr_configured_latency = DEFAULT_BURST_LATENCY[3:0];
    bcr_valid = 0;
    for (i = 0; i < (1<<BANK_BITS); i = i + 1) begin
        mem0[i] = 16'hDEAD;
        mem1[i] = 16'hDEAD;
    end
end

// ============================================================
// Controller-clock domain: address latch, async write/read, burst setup
// ============================================================
always @(posedge clk) begin
    adv_n_prev <= cram_adv_n;
    we_n_prev <= cram_we_n;

    // Power-up sequencing
    if (!reset_n) begin
        powerup_cnt <= 0;
        powered_up <= 0;
    end else if (!powered_up) begin
        if (powerup_cnt >= POWERUP_CYCLES[7:0])
            powered_up <= 1;
        else
            powerup_cnt <= powerup_cnt + 8'd1;
        // Check for commands before power-up is complete (BCR config is OK)
        if (chip_active && adv_n_fall && !cram_cre) begin
            $display("[%0t] CRAM ERROR: command before power-up stable (%0d/%0d cycles)",
                     $time, powerup_cnt, POWERUP_CYCLES);
            error_count <= error_count + 16'd1;
        end
    end

    case (state)
    ST_IDLE: begin
        if (chip_active && adv_n_fall) begin
            latched_addr <= {cram_a, cram_dq_in};
            latched_bank <= bank_sel;
            latched_is_write <= !cram_we_n;
            latched_is_cre <= cram_cre;
            state <= ST_ADDR_LATCH;
        end
    end

    ST_ADDR_LATCH: begin
        if (cram_adv_n) begin
            if (latched_is_cre) begin
                // BCR configuration write — parse and validate
                // BCR value was on DQ[15:0] during address latch
                bcr_sync_mode <= !latched_addr[15];       // Bit 15: 0=sync
                bcr_wait_active_high <= latched_addr[10];  // Bit 10: WAIT polarity
                bcr_latency_code <= latched_addr[6:4];     // Bits 6:4: latency code
                // Decode latency: code 1→4 cycles, code 2→5, etc.
                // For 105MHz (code 1): 4 cycles initial latency
                case (latched_addr[6:4])
                    3'd1: bcr_configured_latency <= 4'd4;
                    3'd2: bcr_configured_latency <= 4'd5;
                    3'd3: bcr_configured_latency <= 4'd6;
                    default: bcr_configured_latency <= 4'd4;
                endcase
                bcr_valid <= 1;
                // Validate expected BCR value
                if (latched_addr[15:0] != 16'h641F) begin
                    $display("[%0t] CRAM WARNING: unexpected BCR value 0x%04x (expected 0x641F)",
                             $time, latched_addr[15:0]);
                end
                state <= ST_IDLE;
            end else if (latched_is_write) begin
                // Always use async write capture (WE# edge + DQ sampling).
                // Works for both single async writes and sync burst writes
                // because the controller drives WE# and DQ in both cases.
                state <= ST_WRITE;
            end else begin
                if (!cram_oe_n) begin
                    // Sync burst read — hand off to cram_clk domain
                    burst_active <= 1;
                    burst_latency_cnt <= bcr_configured_latency;
                    burst_addr <= latched_addr[BANK_BITS-1:0];
                    burst_bank <= latched_bank;
                    burst_data_valid <= 0;
                    state <= ST_BURST_ACTIVE;
                end else begin
                    state <= ST_READ_ASYNC;
                end
            end
        end
    end

    ST_WRITE: begin
        if (!cram_we_n && cram_dq_oe) begin
            captured_wr_data <= cram_dq_in;
            captured_ub <= !cram_ub_n;
            captured_lb <= !cram_lb_n;
        end
        if (we_n_rise || !chip_active) begin
            if (latched_bank) begin
                if (captured_lb) mem1[latched_addr[BANK_BITS-1:0]][7:0]  <= captured_wr_data[7:0];
                if (captured_ub) mem1[latched_addr[BANK_BITS-1:0]][15:8] <= captured_wr_data[15:8];
            end else begin
                if (captured_lb) mem0[latched_addr[BANK_BITS-1:0]][7:0]  <= captured_wr_data[7:0];
                if (captured_ub) mem0[latched_addr[BANK_BITS-1:0]][15:8] <= captured_wr_data[15:8];
            end
            state <= ST_IDLE;
        end
    end

    ST_READ_ASYNC: begin
        // Output data combinationally when OE# is low (no registered delay)
        // to compensate for the cram_dq_r IOB register in psram.sv.
        // Real CRAM chips also output combinationally after access time.
        if (!cram_oe_n) oe_seen <= 1'b1;
        if (oe_seen && (cram_oe_n || !chip_active)) begin
            oe_seen <= 1'b0;
            state <= ST_IDLE;
        end
        if (!oe_seen && !chip_active)
            state <= ST_IDLE;
    end

    ST_BURST_ACTIVE: begin
        // Burst read data is driven by cram_clk domain
        if (!chip_active) begin
            burst_active <= 0;
            burst_data_valid <= 0;
            burst_dq_out <= 16'h0;
            cram_wait_out <= 1'b0;
            state <= ST_IDLE;
        end
    end

    ST_BURST_WRITE: begin
        // Capture on edges of cram_wr_strobe toggle signal.
        // The toggle changes when new data is on DQ (after NBA propagation).
        if (cram_wr_strobe != wr_strobe_prev) begin
            if (burst_bank)
                mem1[burst_addr] <= cram_dq_in;
            else
                mem0[burst_addr] <= cram_dq_in;
            burst_addr <= burst_addr + 1;
        end
        wr_strobe_prev <= cram_wr_strobe;
        if (!chip_active || cram_we_n) begin
            state <= ST_IDLE;
        end
    end

    default: state <= ST_IDLE;
    endcase
end

// ============================================================
// CRAM-clock domain: sync burst read/write data streaming
// ============================================================
always @(posedge cram_clk) begin
    if (burst_active && !latched_is_write) begin
        // Sync burst READ: output data
        if (burst_latency_cnt > 0) begin
            cram_wait_out <= 1'b1;
            burst_latency_cnt <= burst_latency_cnt - 4'd1;
        end else begin
            cram_wait_out <= 1'b0;
            burst_dq_out <= burst_bank ? mem1[burst_addr] : mem0[burst_addr];
            burst_addr <= burst_addr + 1;
        end
    end
    // (Burst writes are captured in clk domain, not cram_clk)
end

// ============================================================
// Protocol assertions (checked on controller clk)
// ============================================================
always @(posedge clk) begin
    // Assertion: WE# and OE# should not both be low simultaneously
    if (!cram_we_n && !cram_oe_n && chip_active) begin
        $display("[%0t] CRAM ERROR: WE# and OE# both low simultaneously", $time);
        error_count <= error_count + 16'd1;
    end

    // Assertion: sync burst read attempted without BCR configured
    if (state == ST_ADDR_LATCH && cram_adv_n && !latched_is_write && !latched_is_cre
        && !cram_oe_n && !bcr_valid) begin
        $display("[%0t] CRAM ERROR: sync burst read before BCR configuration", $time);
        error_count <= error_count + 16'd1;
    end
end

endmodule
