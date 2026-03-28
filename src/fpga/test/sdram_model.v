//
// Behavioral SDRAM Model for Verilator Testing
//
// Mimics a 16-bit SDRAM (e.g., IS42S16160G) with:
//   - 4 banks × 8192 rows × 512 columns (16-bit words)
//   - BL=2 (burst length 2, configured via LMR)
//   - CAS latency 3
//   - Timing enforcement with violation detection
//
// Memory is modeled as a flat array (32MB = 16M × 16-bit).
// Provides assertion-like error output for timing violations.
//
// Verilator-friendly: uses separate dq_in/dq_out/dq_oe instead of inout,
// since Verilator cannot properly resolve bidirectional buses between modules.
//

`timescale 1ns/1ps

module sdram_model (
    input  wire        clk,
    input  wire        cke,
    input  wire        cs_n,    // active low (active low active low!)
    input  wire        ras_n,
    input  wire        cas_n,
    input  wire        we_n,
    input  wire [1:0]  ba,
    input  wire [12:0] a,
    input  wire [15:0] dq_in,   // data from controller (writes)
    output wire [15:0] dq_out,  // data to controller (reads) — combinational, like real SDRAM
    output wire        dq_oe,   // output enable (1 = model driving)
    input  wire [1:0]  dqm,

    // Backdoor: direct memory write for firmware preload
    input  wire        bd_we,
    input  wire [23:0] bd_word_addr,  // 24-bit WORD address (not halfword)
    input  wire [31:0] bd_wdata
);

// Commands decoded from {ras_n, cas_n, we_n}
localparam CMD_NOP     = 3'b111;
localparam CMD_ACT     = 3'b011;
localparam CMD_READ    = 3'b101;
localparam CMD_WRITE   = 3'b100;
localparam CMD_PRECHG  = 3'b010;
localparam CMD_AUTOREF = 3'b001;
localparam CMD_LMR     = 3'b000;

wire [2:0] cmd = {ras_n, cas_n, we_n};

// Timing parameters (100 MHz = 10ns period)
// CAS=3 in real hardware, but model uses 2 to compensate for:
// 1) Registered cmd output in io_sdram (adds 1 cycle to cmd→model path)
// 2) phy_dq_latched register in io_sdram (adds 1 cycle to model→capture path)
// Net: model needs CAS-1 stages to align data with enable_dq_read_4.
localparam CAS_LATENCY = 2;
localparam T_RCD       = 2;   // ACT to READ/WRITE
localparam T_RP        = 2;   // Precharge to ACT
localparam T_WR        = 2;   // Write recovery
localparam T_RAS       = 5;   // ACT to PRECHARGE min
localparam T_RC        = 6;   // ACT to ACT (same bank)
localparam T_RFC       = 8;   // Refresh cycle

// Bank state
reg        bank_active [0:3];
reg [12:0] bank_row    [0:3];
reg [31:0] bank_act_time [0:3];  // Cycle of last ACT

// Memory array (16M × 16-bit = 32MB)
reg [15:0] mem [0:16*1024*1024-1];

// CAS latency read pipeline
reg [15:0] rd_pipe_data [0:CAS_LATENCY];
reg        rd_pipe_valid [0:CAS_LATENCY];

// Write data capture
reg        wr_pending;
reg [1:0]  wr_bank;
reg [12:0] wr_row;
reg [9:0]  wr_col;
reg [1:0]  wr_dqm_saved;
reg        wr_beat;  // 0=first, 1=second BL=2 beat

// Burst state
reg        rd_burst_active;
reg [1:0]  rd_bank;
reg [12:0] rd_row;
reg [9:0]  rd_col;
reg        rd_beat;

// Cycle counter
reg [31:0] cycle_count;

// Refresh enforcement
// IS42S16160G: 8192 refreshes per 64ms. At 100MHz = 781 cycles max between refreshes.
// Use a generous margin (1000 cycles) to avoid false positives during init.
localparam T_REF_MAX = 1000;  // Max cycles between auto-refreshes
reg [31:0] last_refresh_cycle;
reg [31:0] refresh_count;
reg        refresh_tracking;  // Don't enforce during init sequence

// Error counter
integer errors;

// Address calculation
function [23:0] flat_addr;
    input [1:0]  bank;
    input [12:0] row;
    input [9:0]  col;
    flat_addr = {bank, row, col[8:0]};
endfunction

integer i;
// Combinational DQ output — mimics real SDRAM's CAS latency output
assign dq_out = rd_pipe_valid[CAS_LATENCY] ? rd_pipe_data[CAS_LATENCY] : 16'h0;
assign dq_oe  = rd_pipe_valid[CAS_LATENCY];

initial begin
    errors = 0;
    cycle_count = 0;
    wr_pending = 0;
    rd_burst_active = 0;
    last_refresh_cycle = 0;
    refresh_count = 0;
    refresh_tracking = 0;
    for (i = 0; i < 4; i = i + 1) begin
        bank_active[i] = 0;
        bank_row[i] = 0;
        bank_act_time[i] = 0;
    end
    for (i = 0; i <= CAS_LATENCY; i = i + 1) begin
        rd_pipe_valid[i] = 0;
        rd_pipe_data[i] = 0;
    end
    // Initialize memory to a known pattern
    for (i = 0; i < 16*1024*1024; i = i + 1)
        mem[i] = 16'hDEAD;
end

always @(posedge clk) begin
    cycle_count <= cycle_count + 1;

    // Refresh enforcement: after first auto-refresh, start tracking interval
    if (refresh_count > 0 && !refresh_tracking)
        refresh_tracking <= 1;
    if (refresh_tracking && (cycle_count - last_refresh_cycle) > T_REF_MAX) begin
        $display("[%0t] SDRAM ERROR: refresh interval exceeded (%0d cycles since last refresh, max %0d)",
                 $time, cycle_count - last_refresh_cycle, T_REF_MAX);
        errors = errors + 1;
        refresh_tracking <= 0;  // Suppress repeated errors
    end

    // Shift read pipeline
    for (i = CAS_LATENCY; i > 0; i = i - 1) begin
        rd_pipe_data[i]  <= rd_pipe_data[i-1];
        rd_pipe_valid[i] <= rd_pipe_valid[i-1];
    end
    rd_pipe_data[0] <= 16'h0;
    rd_pipe_valid[0] <= 0;

    // Handle second BL=2 read beat
    if (rd_burst_active) begin
        rd_pipe_data[0] <= mem[flat_addr(rd_bank, rd_row, rd_col + 10'd1)];
        rd_pipe_valid[0] <= 1;
        rd_burst_active <= 0;
    end

    // Handle write data capture (BL=2)
    // io_sdram drives phy_dq_out with non-blocking assignment, so data appears
    // on ctrl_dq_out ONE cycle after the state that sets it.
    // ST_WRITE_2 sets low half → appears on dq_in next cycle (beat 0 capture)
    // ST_WRITE_3 sets high half → appears on dq_in next cycle (beat 1 capture)
    // wr_beat: 0 = capture first beat, 1 = capture second beat
    if (wr_pending) begin
        if (wr_beat == 0) begin
            // First beat: low 16 bits
            if (!dqm[0]) mem[flat_addr(wr_bank, wr_row, wr_col)][7:0]  <= dq_in[7:0];
            if (!dqm[1]) mem[flat_addr(wr_bank, wr_row, wr_col)][15:8] <= dq_in[15:8];
            wr_beat <= 1;
        end else begin
            // Second beat: high 16 bits
            if (!dqm[0]) mem[flat_addr(wr_bank, wr_row, wr_col + 10'd1)][7:0]  <= dq_in[7:0];
            if (!dqm[1]) mem[flat_addr(wr_bank, wr_row, wr_col + 10'd1)][15:8] <= dq_in[15:8];
            wr_pending <= 0;
        end
    end

    // Command decode
    if (cke && !cs_n) begin
        case (cmd)
        CMD_ACT: begin
            if (bank_active[ba]) begin
                $display("[%0t] SDRAM ERROR: ACT to bank %0d while active (row %0d open)", $time, ba, bank_row[ba]);
                errors = errors + 1;
            end
            if (bank_active[ba] && (cycle_count - bank_act_time[ba]) < T_RC) begin
                $display("[%0t] SDRAM ERROR: tRC violation bank %0d (%0d cycles, need %0d)", $time, ba,
                         cycle_count - bank_act_time[ba], T_RC);
                errors = errors + 1;
            end
            bank_active[ba] <= 1;
            bank_row[ba] <= a;
            bank_act_time[ba] <= cycle_count;
        end

        CMD_READ: begin
            if (!bank_active[ba]) begin
                $display("[%0t] SDRAM ERROR: READ to inactive bank %0d", $time, ba);
                errors = errors + 1;
            end
            if ((cycle_count - bank_act_time[ba]) < T_RCD) begin
                $display("[%0t] SDRAM ERROR: tRCD violation bank %0d (%0d cycles, need %0d)", $time, ba,
                         cycle_count - bank_act_time[ba], T_RCD);
                errors = errors + 1;
            end
            // BL=2: read two consecutive halfwords
            rd_pipe_data[0] <= mem[flat_addr(ba, bank_row[ba], a[9:0])];
            rd_pipe_valid[0] <= 1;
            // Second beat next cycle
            rd_burst_active <= 1;
            rd_bank <= ba;
            rd_row <= bank_row[ba];
            rd_col <= a[9:0];
        end

        CMD_WRITE: begin
            if (!bank_active[ba]) begin
                $display("[%0t] SDRAM ERROR: WRITE to inactive bank %0d", $time, ba);
                errors = errors + 1;
            end
            if ((cycle_count - bank_act_time[ba]) < T_RCD) begin
                $display("[%0t] SDRAM ERROR: tRCD violation bank %0d (%0d cycles, need %0d)", $time, ba,
                         cycle_count - bank_act_time[ba], T_RCD);
                errors = errors + 1;
            end
            // BL=2 write: cmd and first beat data arrive on same posedge
            // (both driven by io_sdram's non-blocking in the previous cycle).
            // Capture first beat (low half) here. Second beat captured by
            // wr_pending handler next cycle.
            wr_pending <= 1;
            wr_beat <= 1;  // Skip beat 0 in handler (already captured here)
            wr_bank <= ba;
            wr_row <= bank_row[ba];
            wr_col <= a[9:0];
            // Capture first BL=2 beat (low halfword)
            if (!dqm[0]) mem[flat_addr(ba, bank_row[ba], a[9:0])][7:0]  <= dq_in[7:0];
            if (!dqm[1]) mem[flat_addr(ba, bank_row[ba], a[9:0])][15:8] <= dq_in[15:8];
        end

        CMD_PRECHG: begin
            if (a[10]) begin
                // Precharge all banks
                for (i = 0; i < 4; i = i + 1)
                    bank_active[i] <= 0;
            end else begin
                bank_active[ba] <= 0;
            end
        end

        CMD_AUTOREF: begin
            // Track refresh timing
            last_refresh_cycle <= cycle_count;
            refresh_count <= refresh_count + 1;
        end

        CMD_LMR: begin
            // Accept mode register set
        end

        CMD_NOP: begin
            // Nothing
        end
        endcase
    end
end

// Verification helper: read a 32-bit word at a word address
function [31:0] read_word;
    input [23:0] word_addr;
    reg [23:0] ha;
    begin
        ha = word_addr << 1;  // Convert word addr to halfword addr
        read_word = {mem[ha + 1], mem[ha]};
    end
endfunction

// Backdoor write port: uses same {bank, row, col[8:0]} flat addressing as the model.
wire [24:0] bd_hw_lo = {bd_word_addr[23:0], 1'b0};
wire [24:0] bd_hw_hi = {bd_word_addr[23:0], 1'b0} + 25'd1;
wire [23:0] bd_flat_lo = flat_addr(bd_hw_lo[24:23], bd_hw_lo[22:10], bd_hw_lo[9:0]);
wire [23:0] bd_flat_hi = flat_addr(bd_hw_hi[24:23], bd_hw_hi[22:10], bd_hw_hi[9:0]);

always @(posedge clk) begin
    if (bd_we) begin
        mem[bd_flat_lo] <= bd_wdata[15:0];
        mem[bd_flat_hi] <= bd_wdata[31:16];
    end
end

// Legacy task (not used by Verilator, kept for reference)
// word_addr is a 24-bit word address → 2 consecutive halfwords
task bd_write32;
    input [23:0] word_addr;
    input [31:0] data;
    reg [23:0] ha;
    begin
        ha = word_addr << 1;
        mem[ha]     = data[15:0];
        mem[ha + 1] = data[31:16];
    end
endtask

endmodule
