//
// Testbench: CRAM1 concurrent CPU + mixer stress test
//
// Reproduces the test_mixer_stress → test_psram_memory hang:
//   1. "Mixer" reads CRAM1 continuously at high rate (31 voices × 4 taps)
//   2. "CPU" writes and reads CRAM1 sequentially (cram1 16K test pattern)
//   3. Uses the real cram1_controllercontroller + cram_chip_model
//   4. Implements the mux from core_top.v (CPU > mixer priority, busy blocking)
//   5. Checks for:
//      - Hangs (no activity for N cycles)
//      - Data corruption (CPU read != last CPU write)
//
// Goal: reproduce in simulation so we can iterate on the fix without
// resynthesizing.
//

`default_nettype none
`timescale 1ns / 1ps

module tb_cram1_stress;

// 100 MHz clock (clk_cpu domain — controller + mixer + CPU all here)
reg clk = 0;
always #5.0 clk = ~clk;

reg reset_n = 0;
initial begin
    #200;
    reset_n = 1;
end

// ============================================================
// Command sources
// ============================================================
// CPU (master 0): AXI4 master issuing single-word writes/reads
reg         axi_arvalid = 0;
wire        axi_arready;
reg  [31:0] axi_araddr = 0;
wire        axi_rvalid;
reg         axi_rready = 1;
wire [31:0] axi_rdata;
wire [1:0]  axi_rresp;
wire        axi_rlast;

reg         axi_awvalid = 0;
wire        axi_awready;
reg  [31:0] axi_awaddr = 0;
reg         axi_wvalid = 0;
wire        axi_wready;
reg  [31:0] axi_wdata = 0;
reg         axi_wlast = 1;
wire        axi_bvalid;
wire [1:0]  axi_bresp;

// Signals from AXI slave to our mux
wire        cpu_rd;
wire        cpu_wr;
wire [25:0] cpu_addr26;
wire [31:0] cpu_wdata_slave;
wire [3:0]  cpu_wstrb;

wire [21:0] cpu_addr = cpu_addr26[21:0];

// Mixer (master 1): continuous reads at pseudo-random addresses
reg         mix_rd = 0;
reg  [21:0] mix_addr = 0;

// ============================================================
// Real axi_cram1_slave
// ============================================================
axi_cram1_slave slave (
    .clk(clk), .reset_n(reset_n),
    .s_axi_arvalid(axi_arvalid), .s_axi_arready(axi_arready),
    .s_axi_araddr(axi_araddr), .s_axi_arlen(8'd0),
    .s_axi_rvalid(axi_rvalid), .s_axi_rready(axi_rready),
    .s_axi_rdata(axi_rdata), .s_axi_rresp(axi_rresp), .s_axi_rlast(axi_rlast),
    .s_axi_awvalid(axi_awvalid), .s_axi_awready(axi_awready),
    .s_axi_awaddr(axi_awaddr), .s_axi_awlen(8'd0),
    .s_axi_wvalid(axi_wvalid), .s_axi_wready(axi_wready),
    .s_axi_wdata(axi_wdata), .s_axi_wstrb(4'hF), .s_axi_wlast(axi_wlast),
    .s_axi_bvalid(axi_bvalid), .s_axi_bready(1'b1), .s_axi_bresp(axi_bresp),
    .psram_rd(cpu_rd), .psram_wr(cpu_wr),
    .psram_addr(cpu_addr26),
    .psram_wdata(cpu_wdata_slave), .psram_wstrb(cpu_wstrb),
    .psram_rdata(psram1_rdata),
    .psram_busy(psram1_busy),
    .psram_rdata_valid(psram1_rdata_valid_for_cpu)
);

// ============================================================
// Mux — mirrors core_top.v CRAM1 mux
// ============================================================
wire        psram1_rd;
wire        psram1_wr;
wire [21:0] psram1_addr;
wire [31:0] psram1_wdata;
wire [3:0]  psram1_wstrb;
wire [31:0] psram1_rdata;
wire        psram1_busy;
wire        psram1_rdata_valid;

reg psram1_rd_owner;  // 0=CPU, 1=mixer
always @(posedge clk) begin
    if (psram1_rd)
        psram1_rd_owner <= cpu_rd ? 1'b0 : 1'b1;
end

wire psram1_rdata_valid_for_cpu   = psram1_rdata_valid && !psram1_rd_owner;
wire psram1_rdata_valid_for_mixer = psram1_rdata_valid &&  psram1_rd_owner;

// Same logic as core_top.v
assign psram1_wr = psram1_busy ? 1'b0 : cpu_wr;
assign psram1_rd = psram1_wr ? 1'b0
                 : psram1_busy ? 1'b0
                 : cpu_rd ? 1'b1
                 : mix_rd;

assign psram1_addr  = (cpu_rd | cpu_wr) ? cpu_addr : mix_addr;
assign psram1_wdata = cpu_wdata_slave;
assign psram1_wstrb = cpu_wstrb;

// ============================================================
// DUT: cram1_controller(real RTL)
// ============================================================
wire [21:16] cram_a;
wire [15:0]  cram_dq_out;
wire         cram_dq_oe;
wire [15:0]  cram_dq_in;
wire         cram_wait;
wire         cram_adv_n, cram_cre, cram_ce0_n, cram_ce1_n;
wire         cram_oe_n, cram_we_n, cram_ub_n, cram_lb_n;

cram1_controller#(.CLOCK_SPEED(100)) dut (
    .clk(clk), .reset_n(reset_n),
    .word_rd(psram1_rd), .word_wr(psram1_wr),
    .word_addr(psram1_addr), .word_data(psram1_wdata),
    .word_wstrb(psram1_wstrb),
    .word_q(psram1_rdata), .word_busy(psram1_busy),
    .word_q_valid(psram1_rdata_valid),
    .burst_rd(1'b0), .burst_addr(22'd0), .burst_len(5'd0),
    .burst_q(), .burst_q_valid(), .burst_busy(),
    .bcr_init_done(),
    .cram_a(cram_a),
    .cram_dq_out(cram_dq_out), .cram_dq_oe(cram_dq_oe),
    .cram_dq_in(cram_dq_in),
    .cram_wait(cram_wait), .cram_clk(),
    .cram_adv_n(cram_adv_n), .cram_cre(cram_cre),
    .cram_ce0_n(cram_ce0_n), .cram_ce1_n(cram_ce1_n),
    .cram_oe_n(cram_oe_n), .cram_we_n(cram_we_n),
    .cram_ub_n(cram_ub_n), .cram_lb_n(cram_lb_n)
);

// ============================================================
// Chip model
// ============================================================
wire [15:0] error_count;
cram_chip_model chip (
    .clk(clk), .cram_clk(clk), .reset_n(reset_n),
    .cram_a(cram_a),
    .cram_dq_in(cram_dq_out),    // controller drives IN from chip POV
    .cram_dq_out(cram_dq_in),    // chip drives IN from controller POV
    .cram_dq_oe(cram_dq_oe),
    .cram_wait_out(cram_wait),
    .cram_adv_n(cram_adv_n), .cram_cre(cram_cre),
    .cram_ce0_n(cram_ce0_n), .cram_ce1_n(cram_ce1_n),
    .cram_oe_n(cram_oe_n), .cram_we_n(cram_we_n),
    .cram_ub_n(cram_ub_n), .cram_lb_n(cram_lb_n),
    .cram_wr_strobe(1'b0),
    .bd_we(1'b0), .bd_word_addr(21'd0), .bd_wdata_word(32'd0),
    .error_count(error_count)
);

// ============================================================
// AXI master stimulus — simulates CPU writes then reads
// ============================================================
localparam CPU_IDLE   = 3'd0;
localparam CPU_WR     = 3'd1;
localparam CPU_WR_B   = 3'd2;
localparam CPU_RD     = 3'd3;
localparam CPU_RD_R   = 3'd4;
localparam CPU_DONE   = 3'd5;

reg [2:0]  cpu_state = CPU_IDLE;
reg [11:0] cpu_idx = 0;
reg [31:0] cpu_errors = 0;
reg [3:0]  cpu_pass = 0;
reg [31:0] cpu_watchdog = 0;
localparam NUM_PASSES = 4;
localparam TEST_WORDS = 256;

reg [31:0] cycle = 0;
always @(posedge clk) cycle <= cycle + 1;

wire [31:0] expected = ~{20'd0, cpu_idx};

always @(posedge clk) begin
    if (!reset_n) begin
        cpu_state <= CPU_IDLE;
        cpu_idx   <= 0;
        cpu_pass  <= 0;
        cpu_errors <= 0;
        cpu_watchdog <= 0;
        axi_arvalid <= 0;
        axi_awvalid <= 0;
        axi_wvalid  <= 0;
    end else begin
        cpu_watchdog <= cpu_watchdog + 1;
        if (cpu_watchdog > 32'd50000) begin
            $display("[%0t] HANG: pass=%0d state=%0d idx=%0d",
                     $time, cpu_pass, cpu_state, cpu_idx);
            $display("        busy=%b ar=%b aw=%b w=%b r=%b b=%b",
                     psram1_busy, axi_arvalid, axi_awvalid, axi_wvalid,
                     axi_rvalid, axi_bvalid);
            $display("        mix_rd=%b cpu_rd=%b cpu_wr=%b",
                     mix_rd, cpu_rd, cpu_wr);
            $finish;
        end

        case (cpu_state)
        CPU_IDLE: if (cycle > 32'd500) begin
            $display("[%0t] CPU start", $time);
            cpu_state <= CPU_WR;
        end

        CPU_WR: begin
            axi_awvalid <= 1;
            axi_wvalid  <= 1;
            axi_awaddr  <= 32'h39290000 + (cpu_idx << 2);
            axi_wdata   <= expected;
            if (axi_awvalid && axi_awready) axi_awvalid <= 0;
            if (axi_wvalid && axi_wready)   axi_wvalid  <= 0;
            if (!axi_awvalid && !axi_wvalid) begin
                cpu_watchdog <= 0;
                cpu_state <= CPU_WR_B;
            end
        end

        CPU_WR_B: begin
            if (axi_bvalid) begin
                cpu_watchdog <= 0;
                if (cpu_idx == TEST_WORDS - 1) begin
                    cpu_idx <= 0;
                    cpu_state <= CPU_RD;
                    $display("[%0t] pass %0d: writes done", $time, cpu_pass);
                end else begin
                    cpu_idx <= cpu_idx + 1;
                    cpu_state <= CPU_WR;
                end
            end
        end

        CPU_RD: begin
            axi_arvalid <= 1;
            axi_araddr  <= 32'h39290000 + (cpu_idx << 2);
            if (axi_arvalid && axi_arready) begin
                axi_arvalid <= 0;
                cpu_watchdog <= 0;
                cpu_state <= CPU_RD_R;
            end
        end

        CPU_RD_R: begin
            if (axi_rvalid) begin
                if (axi_rdata !== expected) begin
                    cpu_errors <= cpu_errors + 1;
                    if (cpu_errors < 5)
                        $display("[%0t] DATA pass=%0d idx=%0d exp=%08x got=%08x",
                                 $time, cpu_pass, cpu_idx, expected, axi_rdata);
                end
                cpu_watchdog <= 0;
                if (cpu_idx == TEST_WORDS - 1) begin
                    $display("[%0t] pass %0d done, errors=%0d",
                             $time, cpu_pass, cpu_errors);
                    if (cpu_pass == NUM_PASSES - 1) begin
                        cpu_state <= CPU_DONE;
                    end else begin
                        cpu_pass <= cpu_pass + 1;
                        cpu_idx  <= 0;
                        cpu_state <= CPU_WR;
                    end
                end else begin
                    cpu_idx <= cpu_idx + 1;
                    cpu_state <= CPU_RD;
                end
            end
        end

        CPU_DONE: begin
            $display("=== CPU DONE: %0d passes, errors=%0d ===", NUM_PASSES, cpu_errors);
            if (cpu_errors == 0) $display("PASS");
            else                 $display("FAIL");
            $finish;
        end
        endcase
    end
end

// ============================================================
// Mixer stimulus — continuous reads at pseudo-random addresses,
// emulating 31 voices × 4 taps at various sample rates.
// ============================================================
reg [31:0] mix_lfsr = 32'hDEADBEEF;
reg [3:0]  mix_state = 0;

always @(posedge clk) begin
    if (!reset_n) begin
        mix_rd <= 0;
        mix_state <= 0;
    end else begin
        mix_rd <= 0;

        case (mix_state)
        4'd0: begin
            // Request a read (simulates mixer always wanting samples)
            if (!psram1_busy) begin
                mix_lfsr <= {mix_lfsr[30:0], mix_lfsr[31] ^ mix_lfsr[21] ^ mix_lfsr[1] ^ mix_lfsr[0]};
                mix_addr <= 22'h400000 + (mix_lfsr[19:0] & 22'h0FFFFF);
                mix_rd <= 1;
                mix_state <= 4'd1;
            end
        end
        4'd1: begin
            // Wait for response (or accept that it was blocked)
            if (psram1_rdata_valid_for_mixer)
                mix_state <= 4'd0;
            // If mux blocked us (psram1_rd was 0), retry by going back to state 0
            // We can detect this: if enough cycles pass without a response, retry.
        end
        endcase
    end
end

// ============================================================
// Hang watchdog — detect if CPU state machine stalls
// ============================================================
reg [31:0] last_progress_cycle = 0;
reg [2:0]  last_cpu_state = CPU_IDLE;
reg [11:0] last_cpu_idx = 0;

always @(posedge clk) begin
    if (cpu_state != last_cpu_state || cpu_idx != last_cpu_idx) begin
        last_progress_cycle <= cycle;
        last_cpu_state <= cpu_state;
        last_cpu_idx <= cpu_idx;
    end
    if ((cycle - last_progress_cycle) > 32'd10000) begin
        $display("[%0t] HANG: no CPU progress for 10k cycles", $time);
        $display("        state=%0d idx=%0d", cpu_state, cpu_idx);
        $finish;
    end
end

// ============================================================
// Global timeout
// ============================================================
initial begin
    #10000000;  // 10 ms sim time
    $display("[%0t] GLOBAL TIMEOUT: sim ran too long", $time);
    $finish;
end

// VCD dump
initial begin
    $dumpfile("tb_cram1_stress.vcd");
    $dumpvars(0, tb_cram1_stress);
end

endmodule
