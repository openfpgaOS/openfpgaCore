//
// Testbench: CRAM1 CDC + Bridge Collision
//
// Tests the cpu_psram1_cdc under concurrent bridge reads.
// Reproduces the File I/O assertion 17 hang:
//   1. CPU issues rapid reads from CRAM1 (simulating memcpy from I/O cache)
//   2. Bridge reads CRAM1 intermittently (simulating Pocket save-state polling)
//   3. Verify all CPU reads complete without permanent stall
//

`default_nettype none
`timescale 1ns / 1ps

module tb_cram1_cdc;

// ============================================================
// Clocks: CPU at 100 MHz, bridge at 74.25 MHz
// ============================================================
reg clk_cpu = 0;
reg clk_74a = 0;

always #5.0  clk_cpu = ~clk_cpu;   // 100 MHz
always #6.734 clk_74a = ~clk_74a;  // 74.25 MHz

reg reset_n = 0;
initial begin
    #100;
    reset_n = 1;
end

// ============================================================
// PSRAM1 word model (behavioral, clk_74a domain)
// ============================================================
wire        psram1_rd;
wire        psram1_wr;
wire [21:0] psram1_addr;
wire [31:0] psram1_wdata;
wire [3:0]  psram1_wstrb;
wire [31:0] psram1_rdata;
wire        psram1_busy;
wire        psram1_rdata_valid;

psram_word_model psram1 (
    .clk(clk_74a),
    .reset_n(reset_n),
    .word_rd(psram1_rd),
    .word_wr(psram1_wr),
    .word_addr({4'b0, psram1_addr}),
    .word_wdata(psram1_wdata),
    .word_wstrb(psram1_wstrb),
    .word_rdata(psram1_rdata),
    .word_busy(psram1_busy),
    .word_rdata_valid(psram1_rdata_valid),
    .burst_rd(1'b0),
    .burst_len(6'd0),
    .burst_rdata_valid(),
    .burst_rdata(),
    .direct_rd(1'b0),
    .direct_addr(22'd0),
    .direct_rdata(),
    .direct_rdata_valid(),
    .bd_we(1'b0),
    .bd_addr(22'd0),
    .bd_wdata(32'd0)
);

// ============================================================
// CPU-side CDC interface (clk_cpu domain)
// ============================================================
reg         cpu_rd = 0;
reg         cpu_wr = 0;
reg  [21:0] cpu_addr = 0;
reg  [31:0] cpu_wdata = 0;
reg  [3:0]  cpu_wstrb = 4'hF;
wire [31:0] cpu_rdata;
wire        cpu_busy;
wire        cpu_rdata_valid;

// ============================================================
// Bridge CRAM1 signals (clk_74a domain)
// ============================================================
reg         bridge_cram1_rd_pulse = 0;
reg         bridge_cram1_wr_pulse = 0;
reg         bridge_cram1_wr_pending = 0;
reg         bridge_cram1_rd_pending = 0;
reg  [21:0] bridge_cram1_rd_addr = 0;
reg  [21:0] bridge_cram1_wr_addr = 0;
reg  [31:0] bridge_cram1_wr_data = 0;

wire bridge_cram1_active = bridge_cram1_wr_pending | bridge_cram1_rd_pending;

// Bridge requesting: combinatorial same-cycle detection (must be wire, not reg!)
wire bridge_requesting = bridge_do_read && !bridge_cram1_rd_pending && !bridge_cram1_wr_pending;

// CDC inflight output
wire cdc_psram1_inflight;

// ============================================================
// CDC under test
// ============================================================
wire cdc_psram1_rd;
wire cdc_psram1_wr;
wire [21:0] cdc_psram1_addr;
wire [31:0] cdc_psram1_wdata;
wire [3:0]  cdc_psram1_wstrb;

cpu_psram1_cdc cdc_psram1 (
    .clk_cpu(clk_cpu),
    .cpu_rd(cpu_rd),
    .cpu_wr(cpu_wr),
    .cpu_addr(cpu_addr),
    .cpu_wdata(cpu_wdata),
    .cpu_wstrb(cpu_wstrb),
    .cpu_rdata(cpu_rdata),
    .cpu_busy(cpu_busy),
    .cpu_rdata_valid(cpu_rdata_valid),
    .clk_74a(clk_74a),
    .psram_rd(cdc_psram1_rd),
    .psram_wr(cdc_psram1_wr),
    .psram_addr(cdc_psram1_addr),
    .psram_wdata(cdc_psram1_wdata),
    .psram_wstrb(cdc_psram1_wstrb),
    .psram_rdata(psram1_rdata),
    .psram_busy(psram1_busy),
    .psram_rdata_valid(psram1_rdata_valid_for_cdc),
    .bridge_active(bridge_cram1_active),
    .bridge_requesting(bridge_requesting),
    .cdc_inflight(cdc_psram1_inflight)
);

// ============================================================
// PSRAM1 mux with response ownership tracking
// ============================================================

// Track who owns the current in-flight PSRAM read.
// Set on the cycle psram1_rd fires, locked until rdata_valid.
reg psram1_rd_owner;     // 0=CDC, 1=bridge
reg psram1_rd_inflight;  // a read is being processed

always @(posedge clk_74a) begin
    if (!psram1_rd_inflight && psram1_rd) begin
        // New read accepted by PSRAM — lock owner
        psram1_rd_inflight <= 1'b1;
        psram1_rd_owner <= bridge_cram1_rd_pulse;  // 1 if bridge, 0 if CDC
    end
    if (psram1_rdata_valid)
        psram1_rd_inflight <= 1'b0;  // unlock
end

// Filter rdata_valid to the locked owner
wire psram1_rdata_valid_for_cdc    = psram1_rdata_valid && !psram1_rd_owner;
wire psram1_rdata_valid_for_bridge = psram1_rdata_valid &&  psram1_rd_owner;

// Block ALL new reads while one is inflight — prevents overlapping requests
assign psram1_rd = psram1_rd_inflight ? 1'b0
                 : bridge_cram1_rd_pulse ? 1'b1
                 : bridge_cram1_active   ? 1'b0
                 : cdc_psram1_rd;
assign psram1_wr = bridge_cram1_wr_pulse ? 1'b1
                 : bridge_cram1_active   ? 1'b0
                 : cdc_psram1_wr;
assign psram1_addr = bridge_cram1_wr_pending ? bridge_cram1_wr_addr
                   : bridge_cram1_rd_pending ? bridge_cram1_rd_addr
                   : cdc_psram1_addr;
assign psram1_wdata = bridge_cram1_wr_pending ? bridge_cram1_wr_data
                    : cdc_psram1_wdata;
assign psram1_wstrb = bridge_cram1_wr_pending ? 4'b1111
                    : cdc_psram1_wstrb;

// ============================================================
// Bridge read handler (deferred, same as core_top.v)
// ============================================================
reg bridge_cram1_rd_deferred = 0;
reg bridge_cram1_rd_started = 0;
reg [31:0] cram1_rd_resp_data;
reg bridge_do_read = 0;  // test stimulus

always @(posedge clk_74a) begin
    bridge_cram1_rd_pulse <= 0;

    if (bridge_do_read && !bridge_cram1_rd_pending && !bridge_cram1_wr_pending) begin
        bridge_cram1_rd_pending <= 1;
        bridge_cram1_rd_started <= 0;
        bridge_cram1_rd_addr <= 22'h100;  // fixed test address
        if (!cdc_psram1_inflight && !psram1_busy) begin
            bridge_cram1_rd_pulse <= 1;
            bridge_cram1_rd_deferred <= 0;
        end else begin
            bridge_cram1_rd_deferred <= 1;
        end
        bridge_do_read <= 0;
    end

    if (bridge_cram1_rd_deferred && !cdc_psram1_inflight && !psram1_busy) begin
        bridge_cram1_rd_pulse <= 1;
        bridge_cram1_rd_deferred <= 0;
    end

    if (bridge_cram1_rd_pending && !bridge_cram1_rd_deferred) begin
        if (psram1_rdata_valid_for_bridge) begin
            cram1_rd_resp_data <= psram1_rdata;
            bridge_cram1_rd_pending <= 0;
            bridge_cram1_rd_started <= 0;
        end
    end
end

// ============================================================
// Test stimulus
// ============================================================
integer cpu_reads_completed = 0;
integer cpu_reads_requested = 0;
integer bridge_reads_completed = 0;
integer bridge_reads_injected = 0;
integer watchdog = 0;
reg test_done = 0;

// Pre-fill PSRAM with known data
integer init_i;
initial begin
    #200;  // wait for reset
    @(posedge clk_74a);
    for (init_i = 0; init_i < 8192; init_i = init_i + 1) begin
        @(posedge clk_74a);
        psram1.mem[init_i] = init_i ^ 32'hA5A5A5A5;
    end
    $display("[%0t] PSRAM pre-filled with 8192 words", $time);
end

// CPU read task: issue read, wait for response
task cpu_read_word;
    input [21:0] addr;
    output [31:0] data;
    begin
        @(posedge clk_cpu);
        cpu_rd <= 1;
        cpu_addr <= addr;
        @(posedge clk_cpu);
        cpu_rd <= 0;
        // Wait for response
        while (!cpu_rdata_valid) begin
            @(posedge clk_cpu);
            watchdog = watchdog + 1;
            if (watchdog > 100000) begin
                $display("[%0t] FAIL: CPU read watchdog expired! addr=%h reads_done=%0d",
                         $time, addr, cpu_reads_completed);
                $finish;
            end
        end
        data = cpu_rdata;
        watchdog = 0;
    end
endtask

// Bridge read injection (from clk_74a domain)
always @(posedge clk_74a) begin
    if (reset_n && !test_done) begin
        // Inject bridge read every ~50 clk_74a cycles
        if (!bridge_cram1_rd_pending) begin  // every cycle — absolute worst case
            bridge_do_read <= 1;
            bridge_reads_injected <= bridge_reads_injected + 1;
        end
    end
end

// Track bridge completions
always @(posedge clk_74a)
    if (bridge_cram1_rd_pending && psram1_rdata_valid && !bridge_cram1_rd_deferred)
        bridge_reads_completed <= bridge_reads_completed + 1;

// Main test sequence
reg [31:0] read_data;
reg [31:0] expected;
integer read_i;
integer errors = 0;

initial begin
    $dumpfile("cram1_cdc_test.vcd");
    $dumpvars(0, tb_cram1_cdc);

    // Wait for reset and pre-fill
    #500;

    $display("=== Test 1: 512 rapid CPU reads (simulates 32KB D-cache fill) ===");
    for (read_i = 0; read_i < 512; read_i = read_i + 1) begin
        cpu_read_word(read_i[21:0], read_data);
        expected = read_i ^ 32'hA5A5A5A5;
        if (read_data !== expected) begin
            $display("[%0t] FAIL: addr=%0d got=%h exp=%h",
                     $time, read_i, read_data, expected);
            errors = errors + 1;
        end
        cpu_reads_completed = cpu_reads_completed + 1;
    end
    $display("[%0t] Test 1 complete: %0d reads, %0d errors, %0d bridge reads injected",
             $time, cpu_reads_completed, errors, bridge_reads_injected);

    $display("=== Test 2: 8192 rapid CPU reads (simulates 32KB uncached memcpy) ===");
    cpu_reads_completed = 0;
    errors = 0;
    bridge_reads_injected = 0;
    for (read_i = 0; read_i < 8192; read_i = read_i + 1) begin
        cpu_read_word(read_i[21:0], read_data);
        expected = read_i ^ 32'hA5A5A5A5;
        if (read_data !== expected) begin
            $display("[%0t] FAIL: addr=%0d got=%h exp=%h",
                     $time, read_i, read_data, expected);
            errors = errors + 1;
            if (errors > 10) begin
                $display("Too many errors, stopping");
                $finish;
            end
        end
        cpu_reads_completed = cpu_reads_completed + 1;
    end
    $display("[%0t] Test 2 complete: %0d reads, %0d errors, %0d bridge reads injected",
             $time, cpu_reads_completed, errors, bridge_reads_injected);

    test_done = 1;

    if (errors == 0)
        $display("=== ALL TESTS PASSED ===");
    else
        $display("=== FAILED: %0d errors ===", errors);

    #100;
    $finish;
end

// Global watchdog
initial begin
    #50000000;  // 50ms
    $display("[%0t] GLOBAL WATCHDOG: simulation timed out", $time);
    $display("CPU reads: %0d, Bridge reads: %0d injected / %0d completed",
             cpu_reads_completed, bridge_reads_injected, bridge_reads_completed);
    $finish;
end

endmodule
