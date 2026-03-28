//
// Verilator Testbench: Full RTL PSRAM/SRAM Controller Stack
//
// Instantiates the real RTL chain:
//   axi_psram_slave → target_mux → psram_controller_test (×2) → cram_chip_model (×2)
//                                → sram_controller           → sram_chip_model
//
// Target mux replicated from core_top.v (simplified: no bridge activity).
// BCR init FSM configures both CRAM chips for sync burst mode before tests begin.
//

`default_nettype none

module tb_psram_full (
    input  wire        clk,
    input  wire        cram_clk,   // Phase-shifted clock for CRAM chips (from C++)
    input  wire        clk_bridge, // Bridge clock domain (~74MHz, async to clk)
    input  wire        reset_n,

    // Bridge stimulus (driven from C++ on clk_bridge domain)
    input  wire        bridge_wr,
    input  wire        bridge_rd,
    input  wire [31:0] bridge_addr,
    input  wire [31:0] bridge_wr_data,
    output reg  [31:0] bridge_rd_data,   // CRAM1 read response (bridge domain)
    output reg         bridge_rd_ready,  // Pulses when bridge_rd_data is valid

    // AXI4 Slave interface
    input  wire        s_axi_arvalid,
    output wire        s_axi_arready,
    input  wire [31:0] s_axi_araddr,
    input  wire [7:0]  s_axi_arlen,

    output wire        s_axi_rvalid,
    output wire [31:0] s_axi_rdata,
    output wire [1:0]  s_axi_rresp,
    output wire        s_axi_rlast,

    input  wire        s_axi_awvalid,
    output wire        s_axi_awready,
    input  wire [31:0] s_axi_awaddr,
    input  wire [7:0]  s_axi_awlen,

    input  wire        s_axi_wvalid,
    output wire        s_axi_wready,
    input  wire [31:0] s_axi_wdata,
    input  wire [3:0]  s_axi_wstrb,
    input  wire        s_axi_wlast,

    output wire        s_axi_bvalid,
    output wire [1:0]  s_axi_bresp,

    output wire        init_done,
    output wire        busy,
    output wire [15:0] assertion_errors  // Bus contention + protocol error count
);

// ============================================================
// axi_psram_slave → word-level signals
// ============================================================
wire        cpu_psram_rd;
wire        cpu_psram_wr;
wire [25:0] cpu_psram_addr;
wire [31:0] cpu_psram_wdata;
wire [3:0]  cpu_psram_wstrb;
wire [31:0] cpu_psram_rdata;
wire        cpu_psram_busy;
wire        cpu_psram_rdata_valid;

wire        cpu_psram_burst_rd;
wire [5:0]  cpu_psram_burst_len;
wire        cpu_psram_burst_rdata_valid;
wire [31:0] cpu_psram_burst_rdata;

wire        cpu_psram_burst_wr;
wire [5:0]  cpu_psram_burst_wr_len;
wire [31:0] cpu_psram_burst_wdata;
wire [3:0]  cpu_psram_burst_wstrb;
wire        cpu_psram_burst_wdata_next;

axi_psram_slave slave (
    .clk(clk), .reset_n(reset_n),
    .s_axi_arvalid(s_axi_arvalid), .s_axi_arready(s_axi_arready),
    .s_axi_araddr(s_axi_araddr), .s_axi_arlen(s_axi_arlen),
    .s_axi_rvalid(s_axi_rvalid), .s_axi_rready(1'b1),
    .s_axi_rdata(s_axi_rdata), .s_axi_rresp(s_axi_rresp), .s_axi_rlast(s_axi_rlast),
    .s_axi_awvalid(s_axi_awvalid), .s_axi_awready(s_axi_awready),
    .s_axi_awaddr(s_axi_awaddr), .s_axi_awlen(s_axi_awlen),
    .s_axi_wvalid(s_axi_wvalid), .s_axi_wready(s_axi_wready),
    .s_axi_wdata(s_axi_wdata), .s_axi_wstrb(s_axi_wstrb), .s_axi_wlast(s_axi_wlast),
    .s_axi_bvalid(s_axi_bvalid), .s_axi_bready(1'b1), .s_axi_bresp(s_axi_bresp),
    .psram_rd(cpu_psram_rd), .psram_wr(cpu_psram_wr),
    .psram_addr(cpu_psram_addr), .psram_wdata(cpu_psram_wdata), .psram_wstrb(cpu_psram_wstrb),
    .psram_rdata(cpu_psram_rdata), .psram_busy(cpu_psram_busy),
    .psram_rdata_valid(cpu_psram_rdata_valid),
    .psram_burst_rd(cpu_psram_burst_rd), .psram_burst_len(cpu_psram_burst_len),
    .psram_burst_rdata_valid(cpu_psram_burst_rdata_valid),
    .psram_burst_rdata(cpu_psram_burst_rdata),
    .psram_burst_wr(cpu_psram_burst_wr), .psram_burst_wr_len(cpu_psram_burst_wr_len),
    .psram_burst_wdata(cpu_psram_burst_wdata), .psram_burst_wstrb(cpu_psram_burst_wstrb),
    .psram_burst_wdata_next(cpu_psram_burst_wdata_next)
);

// ============================================================
// Address decode (from core_top.v)
// ============================================================
wire [3:0] mem_target_sel = cpu_psram_addr[25:22];
wire cpu_psram_sel_cram0 = (mem_target_sel == 4'h0) || (mem_target_sel == 4'h8);
wire cpu_psram_sel_cram1 = (mem_target_sel == 4'h1) || (mem_target_sel == 4'h9);
wire cpu_psram_sel_sram  = (mem_target_sel == 4'hA);

// ============================================================
// Bridge write path: clk_bridge → async_fifo → clk drain FSM → CRAM0 mux
// ============================================================
wire        bridge_cram0_wr_detect = bridge_wr && (bridge_addr[31:24] == 8'h20);

// Async FIFO: bridge clock → controller clock
wire        cram0_wr_fifo_full;
wire        cram0_wr_fifo_empty;
wire [55:0] cram0_wr_fifo_q;
wire        cram0_wr_fifo_drain;

async_fifo #(.WIDTH(56), .DEPTH_LOG2(4)) cram0_wr_fifo (
    .wr_clk(clk_bridge),
    .wr_en(bridge_cram0_wr_detect && !cram0_wr_fifo_full),
    .wr_data({bridge_addr[23:2], 2'b00, bridge_wr_data}),
    .wr_full(cram0_wr_fifo_full),
    .rd_clk(clk),
    .rd_en(cram0_wr_fifo_drain),
    .rd_data(cram0_wr_fifo_q),
    .rd_empty(cram0_wr_fifo_empty),
    .aclr(!reset_n)
);

// Drain FSM (clk domain) — mirrors core_top.v
reg        cram0_wr_pending;
reg        cram0_wr_started;
reg [21:0] cram0_wr_addr_r;
reg [31:0] cram0_wr_data_r;

initial begin
    cram0_wr_pending = 0;
    cram0_wr_started = 0;
end

assign cram0_wr_fifo_drain = !cram0_wr_fifo_empty && !cram0_wr_pending && bcr_init_done;

always @(posedge clk) begin
    if (!reset_n) begin
        cram0_wr_pending <= 0;
        cram0_wr_started <= 0;
    end else if (!cram0_wr_pending) begin
        if (!cram0_wr_fifo_empty && bcr_init_done) begin
            cram0_wr_addr_r <= cram0_wr_fifo_q[55:34];
            cram0_wr_data_r <= cram0_wr_fifo_q[31:0];
            cram0_wr_pending <= 1;
            cram0_wr_started <= 0;
        end
    end else begin
        if (!cram0_wr_started && psram0_busy) begin
            cram0_wr_started <= 1;
        end else if (cram0_wr_started && !psram0_busy) begin
            cram0_wr_pending <= 0;
            cram0_wr_started <= 0;
        end
    end
end

wire cram0_bridge_wr_active = cram0_wr_pending | !cram0_wr_fifo_empty;

// ============================================================
// CRAM0 controller (with bridge priority mux)
// ============================================================
wire        cpu_cram0_rd = cpu_psram_rd & cpu_psram_sel_cram0;
wire        cpu_cram0_wr = cpu_psram_wr & cpu_psram_sel_cram0;
wire        cpu_cram0_burst_rd = cpu_psram_burst_rd & cpu_psram_sel_cram0;

// Bridge has priority: block CPU when bridge active
wire        psram0_word_rd = cram0_bridge_wr_active ? 1'b0 : cpu_cram0_rd;
wire        psram0_word_wr = cram0_wr_pending ? 1'b1 : (cram0_bridge_wr_active ? 1'b0 : cpu_cram0_wr);
wire [21:0] psram0_mux_addr = cram0_wr_pending ? cram0_wr_addr_r : cpu_psram_addr[21:0];
wire [31:0] psram0_mux_wdata = cram0_wr_pending ? cram0_wr_data_r : cpu_psram_wdata;
wire [3:0]  psram0_mux_wstrb = cram0_wr_pending ? 4'b1111 : cpu_psram_wstrb;
wire        psram0_burst_rd_sig = cram0_bridge_wr_active ? 1'b0 : cpu_cram0_burst_rd;
wire        cpu_cram0_burst_wr = cpu_psram_burst_wr & cpu_psram_sel_cram0;
wire        psram0_burst_wr_sig = cram0_bridge_wr_active ? 1'b0 : cpu_cram0_burst_wr;

wire [31:0] psram0_rdata;
wire        psram0_busy;
wire        psram0_rdata_valid;
wire        psram0_burst_rdata_valid;
wire [31:0] psram0_burst_rdata;
wire        psram0_raw_busy;

// CRAM0 physical signals (split DQ)
wire [21:16] cram0_a;
wire [15:0]  cram0_ctrl_dq_out;
wire         cram0_ctrl_dq_oe;
wire [15:0]  cram0_chip_dq_out;
wire         cram0_wait;
wire         cram0_adv_n, cram0_cre;
wire         cram0_ce0_n, cram0_ce1_n;
wire         cram0_oe_n, cram0_we_n, cram0_ub_n, cram0_lb_n;

// Resolved DQ: controller drives when oe=1, chip drives otherwise
wire [15:0] cram0_dq_to_ctrl = cram0_ctrl_dq_oe ? cram0_ctrl_dq_out : cram0_chip_dq_out;

psram_controller_test #(.CLOCK_SPEED(48.0)) psram0 (
    .clk(clk), .reset_n(1'b1),
    .word_rd(psram0_word_rd), .word_wr(psram0_word_wr),
    .word_addr(psram0_mux_addr), .word_data(psram0_mux_wdata), .word_wstrb(psram0_mux_wstrb),
    .word_q(psram0_rdata), .word_busy(psram0_busy), .word_q_valid(psram0_rdata_valid),
    .cram_a(cram0_a),
    .cram_dq_in(cram0_dq_to_ctrl),
    .cram_dq_out(cram0_ctrl_dq_out),
    .cram_dq_oe(cram0_ctrl_dq_oe),
    .cram_wait(cram0_wait),
    .cram_clk(),
    .cram_adv_n(cram0_adv_n), .cram_cre(cram0_cre),
    .cram_ce0_n(cram0_ce0_n), .cram_ce1_n(cram0_ce1_n),
    .cram_oe_n(cram0_oe_n), .cram_we_n(cram0_we_n),
    .cram_ub_n(cram0_ub_n), .cram_lb_n(cram0_lb_n),
    .config_en(psram0_config_en), .config_data(bcr_config_data),
    .config_bank_sel(bcr_config_bank),
    .burst_rd(psram0_burst_rd_sig), .burst_len(cpu_psram_burst_len),
    .burst_rdata_valid(psram0_burst_rdata_valid), .burst_rdata(psram0_burst_rdata),
    .burst_wr(psram0_burst_wr_sig), .burst_wr_len(cpu_psram_burst_wr_len),
    .burst_wdata(cpu_psram_burst_wdata), .burst_wstrb(cpu_psram_burst_wstrb),
    .burst_wdata_next(psram0_burst_wdata_next),
    .burst_wr_strobe(cram0_wr_strobe),
    .raw_busy(psram0_raw_busy),
    .dbg_wait_seen(), .dbg_wait_cycles(), .dbg_burst_count(), .dbg_stale_count()
);

wire psram0_burst_wdata_next;
wire        cram0_wr_strobe;

wire [15:0] cram0_errors;
cram_chip_model cram0_chip (
    .clk(clk), .cram_clk(cram_clk), .reset_n(reset_n),
    .cram_a(cram0_a),
    .cram_dq_in(cram0_ctrl_dq_oe ? cram0_ctrl_dq_out : 16'h0),
    .cram_dq_out(cram0_chip_dq_out),
    .cram_dq_oe(cram0_ctrl_dq_oe),
    .cram_wait_out(cram0_wait),
    .cram_adv_n(cram0_adv_n), .cram_cre(cram0_cre),
    .cram_ce0_n(cram0_ce0_n), .cram_ce1_n(cram0_ce1_n),
    .cram_oe_n(cram0_oe_n), .cram_we_n(cram0_we_n),
    .cram_ub_n(cram0_ub_n), .cram_lb_n(cram0_lb_n),
    .cram_wr_strobe(cram0_wr_strobe),
    .error_count(cram0_errors)
);

// ============================================================
// CRAM1 bridge read path (request FIFO + drain FSM + response FIFO)
// ============================================================

// Bridge read detect: addr[31:24] == 0x30 → CRAM1 read
wire bridge_cram1_rd_detect = bridge_rd && (bridge_addr[31:24] == 8'h30);

// Request FIFO: bridge_clk → clk (22-bit word address)
wire        cram1_rd_req_empty;
wire [21:0] cram1_rd_req_q;
wire        cram1_rd_req_drain;

async_fifo #(.WIDTH(22), .DEPTH_LOG2(4)) cram1_rd_req_fifo (
    .wr_clk(clk_bridge),
    .wr_en(bridge_cram1_rd_detect),
    .wr_data(bridge_addr[23:2]),
    .wr_full(),
    .rd_clk(clk),
    .rd_en(cram1_rd_req_drain),
    .rd_data(cram1_rd_req_q),
    .rd_empty(cram1_rd_req_empty),
    .aclr(!reset_n)
);

// Read drain FSM (clk domain)
reg        cram1_rd_pending;
reg        cram1_rd_started;
reg [21:0] cram1_rd_addr_r;
reg        cram1_rd_resp_push;
reg [31:0] cram1_rd_resp_wdata;

initial begin
    cram1_rd_pending = 0;
    cram1_rd_started = 0;
    cram1_rd_resp_push = 0;
end

assign cram1_rd_req_drain = !cram1_rd_req_empty && !cram1_rd_pending;

always @(posedge clk) begin
    cram1_rd_resp_push <= 0;
    if (!reset_n) begin
        cram1_rd_pending <= 0;
        cram1_rd_started <= 0;
    end else if (!cram1_rd_pending) begin
        if (!cram1_rd_req_empty) begin
            cram1_rd_addr_r <= cram1_rd_req_q;
            cram1_rd_pending <= 1;
            cram1_rd_started <= 0;
        end
    end else begin
        if (!cram1_rd_started && psram1_busy)
            cram1_rd_started <= 1;
        if (psram1_rdata_valid) begin
            cram1_rd_resp_wdata <= psram1_rdata;
            cram1_rd_resp_push <= 1;
            cram1_rd_pending <= 0;
            cram1_rd_started <= 0;
        end
    end
end

// Response FIFO: clk → bridge_clk (32-bit data)
wire        cram1_rd_resp_empty;
wire [31:0] cram1_rd_resp_q;

reg cram1_rd_resp_pop;

async_fifo #(.WIDTH(32), .DEPTH_LOG2(2)) cram1_rd_resp_fifo (
    .wr_clk(clk),
    .wr_en(cram1_rd_resp_push),
    .wr_data(cram1_rd_resp_wdata),
    .wr_full(),
    .rd_clk(clk_bridge),
    .rd_en(cram1_rd_resp_pop),
    .rd_data(cram1_rd_resp_q),
    .rd_empty(cram1_rd_resp_empty),
    .aclr(!reset_n)
);

// Bridge-side response capture (bridge_clk domain)
// Capture data, pulse bridge_rd_ready, then pop on the next cycle.
reg cram1_resp_captured;  // 1 = data captured, waiting for pop

initial begin
    bridge_rd_data = 0;
    bridge_rd_ready = 0;
    cram1_rd_resp_pop = 0;
    cram1_resp_captured = 0;
end

always @(posedge clk_bridge) begin
    bridge_rd_ready <= 0;
    cram1_rd_resp_pop <= 0;

    if (cram1_resp_captured) begin
        // Pop the FIFO entry we captured last cycle
        cram1_rd_resp_pop <= 1;
        cram1_resp_captured <= 0;
    end else if (!cram1_rd_resp_empty) begin
        // Capture data from FIFO (show-ahead: data valid when !empty)
        bridge_rd_data <= cram1_rd_resp_q;
        bridge_rd_ready <= 1;
        cram1_resp_captured <= 1;
    end
end

wire bridge_cram1_rd_active = cram1_rd_pending | !cram1_rd_req_empty;

// ============================================================
// CRAM1 controller (with bridge read priority)
// ============================================================
wire        cpu_cram1_rd = cpu_psram_rd & cpu_psram_sel_cram1;
wire        cpu_cram1_wr = cpu_psram_wr & cpu_psram_sel_cram1;
wire        cpu_cram1_burst_rd = cpu_psram_burst_rd & cpu_psram_sel_cram1;

// Bridge read has priority over CPU
wire        psram1_word_rd = cram1_rd_pending ? 1'b1 :
                             bridge_cram1_rd_active ? 1'b0 : cpu_cram1_rd;
wire        psram1_word_wr = bridge_cram1_rd_active ? 1'b0 : cpu_cram1_wr;
wire [21:0] psram1_mux_addr = cram1_rd_pending ? cram1_rd_addr_r : cpu_psram_addr[21:0];
wire        psram1_burst_rd_sig = bridge_cram1_rd_active ? 1'b0 : cpu_cram1_burst_rd;
wire        cpu_cram1_burst_wr = cpu_psram_burst_wr & cpu_psram_sel_cram1;
wire        psram1_burst_wr_sig = bridge_cram1_rd_active ? 1'b0 : cpu_cram1_burst_wr;

wire [31:0] psram1_rdata;
wire        psram1_busy;
wire        psram1_rdata_valid;
wire        psram1_burst_rdata_valid;
wire [31:0] psram1_burst_rdata;
wire        psram1_raw_busy;

wire [21:16] cram1_a;
wire [15:0]  cram1_ctrl_dq_out;
wire         cram1_ctrl_dq_oe;
wire [15:0]  cram1_chip_dq_out;
wire         cram1_wait;
wire         cram1_adv_n, cram1_cre;
wire         cram1_ce0_n, cram1_ce1_n;
wire         cram1_oe_n, cram1_we_n, cram1_ub_n, cram1_lb_n;

wire [15:0] cram1_dq_to_ctrl = cram1_ctrl_dq_oe ? cram1_ctrl_dq_out : cram1_chip_dq_out;

psram_controller_test #(.CLOCK_SPEED(48.0)) psram1 (
    .clk(clk), .reset_n(1'b1),
    .word_rd(psram1_word_rd), .word_wr(psram1_word_wr),
    .word_addr(psram1_mux_addr), .word_data(cpu_psram_wdata), .word_wstrb(cpu_psram_wstrb),
    .word_q(psram1_rdata), .word_busy(psram1_busy), .word_q_valid(psram1_rdata_valid),
    .cram_a(cram1_a),
    .cram_dq_in(cram1_dq_to_ctrl),
    .cram_dq_out(cram1_ctrl_dq_out),
    .cram_dq_oe(cram1_ctrl_dq_oe),
    .cram_wait(cram1_wait),
    .cram_clk(),
    .cram_adv_n(cram1_adv_n), .cram_cre(cram1_cre),
    .cram_ce0_n(cram1_ce0_n), .cram_ce1_n(cram1_ce1_n),
    .cram_oe_n(cram1_oe_n), .cram_we_n(cram1_we_n),
    .cram_ub_n(cram1_ub_n), .cram_lb_n(cram1_lb_n),
    .config_en(psram1_config_en), .config_data(bcr_config_data),
    .config_bank_sel(bcr_config_bank),
    .burst_rd(psram1_burst_rd_sig), .burst_len(cpu_psram_burst_len),
    .burst_rdata_valid(psram1_burst_rdata_valid), .burst_rdata(psram1_burst_rdata),
    .burst_wr(psram1_burst_wr_sig), .burst_wr_len(cpu_psram_burst_wr_len),
    .burst_wdata(cpu_psram_burst_wdata), .burst_wstrb(cpu_psram_burst_wstrb),
    .burst_wdata_next(psram1_burst_wdata_next),
    .burst_wr_strobe(cram1_wr_strobe),
    .raw_busy(psram1_raw_busy),
    .dbg_wait_seen(), .dbg_wait_cycles(), .dbg_burst_count(), .dbg_stale_count()
);

wire psram1_burst_wdata_next;
wire cram1_wr_strobe;

wire [15:0] cram1_errors;
cram_chip_model cram1_chip (
    .clk(clk), .cram_clk(cram_clk),
    .cram_a(cram1_a),
    .cram_dq_in(cram1_ctrl_dq_oe ? cram1_ctrl_dq_out : 16'h0),
    .cram_dq_out(cram1_chip_dq_out),
    .cram_dq_oe(cram1_ctrl_dq_oe),
    .cram_wait_out(cram1_wait),
    .cram_adv_n(cram1_adv_n), .cram_cre(cram1_cre),
    .cram_ce0_n(cram1_ce0_n), .cram_ce1_n(cram1_ce1_n),
    .cram_oe_n(cram1_oe_n), .cram_we_n(cram1_we_n),
    .cram_ub_n(cram1_ub_n), .cram_lb_n(cram1_lb_n),
    .cram_wr_strobe(cram1_wr_strobe),
    .error_count(cram1_errors)
);

// ============================================================
// SRAM controller (sram_controller → sram_chip_model)
// ============================================================
wire        sram_word_rd = cpu_psram_rd & cpu_psram_sel_sram;
wire        sram_word_wr = cpu_psram_wr & cpu_psram_sel_sram;
wire [31:0] sram_rdata;
wire        sram_busy;
wire        sram_rdata_valid;

wire [16:0] sram_a;
wire [15:0] sram_dq_out_ctrl;
wire [15:0] sram_dq_in_ctrl;
wire        sram_dq_oe;
wire        sram_oe_n, sram_we_n, sram_ub_n, sram_lb_n;

sram_controller #(.WAIT_CYCLES(6)) sram0 (
    .clk(clk), .reset_n(reset_n),
    .word_rd(sram_word_rd), .word_wr(sram_word_wr),
    .word_addr(cpu_psram_addr[21:0]), .word_data(cpu_psram_wdata), .word_wstrb(cpu_psram_wstrb),
    .word_q(sram_rdata), .word_busy(sram_busy), .word_q_valid(sram_rdata_valid),
    .sram_a(sram_a), .sram_dq_out(sram_dq_out_ctrl), .sram_dq_in(sram_dq_in_ctrl),
    .sram_dq_oe(sram_dq_oe),
    .sram_oe_n(sram_oe_n), .sram_we_n(sram_we_n),
    .sram_ub_n(sram_ub_n), .sram_lb_n(sram_lb_n)
);

sram_chip_model sram0_chip (
    .clk(clk),
    .sram_a(sram_a),
    .sram_dq_in(sram_dq_out_ctrl),
    .sram_dq_out(sram_dq_in_ctrl),
    .sram_oe_n(sram_oe_n), .sram_we_n(sram_we_n),
    .sram_ub_n(sram_ub_n), .sram_lb_n(sram_lb_n)
);

// ============================================================
// Response mux back to axi_psram_slave (from core_top.v)
// ============================================================

// Read data mux
assign cpu_psram_rdata = cpu_psram_sel_cram1 ? psram1_rdata :
                         cpu_psram_sel_sram  ? sram_rdata :
                                               psram0_rdata;

assign cpu_psram_rdata_valid = cpu_psram_sel_cram1 ? psram1_rdata_valid :
                               cpu_psram_sel_sram  ? sram_rdata_valid :
                                                     psram0_rdata_valid;

// Busy mux with latch (from core_top.v)
reg [1:0] psram_busy_sel;
always @(posedge clk)
    if (!cpu_psram_busy)
        psram_busy_sel <= cpu_psram_sel_cram1 ? 2'd1 :
                          cpu_psram_sel_sram  ? 2'd2 : 2'd0;

assign cpu_psram_busy = (psram_busy_sel == 2'd1) ? (bridge_cram1_rd_active | psram1_busy) :
                        (psram_busy_sel == 2'd2) ? sram_busy :
                                                   (cram0_bridge_wr_active | psram0_busy);

// Burst read data mux (SRAM has no burst support)
assign cpu_psram_burst_rdata_valid = cpu_psram_sel_cram1 ? psram1_burst_rdata_valid
                                                         : psram0_burst_rdata_valid;
assign cpu_psram_burst_rdata = cpu_psram_sel_cram1 ? psram1_burst_rdata
                                                    : psram0_burst_rdata;

// Burst write data next mux
assign cpu_psram_burst_wdata_next = cpu_psram_sel_cram1 ? psram1_burst_wdata_next
                                                        : psram0_burst_wdata_next;

assign busy = cpu_psram_busy;

// ============================================================
// Bus contention assertions
// ============================================================
reg [15:0] bus_errors;
initial bus_errors = 0;

always @(posedge clk) begin
    // SRAM: controller and chip should not conflict
    // sram_dq_oe=1 means controller drives (write), oe_n=0 means chip drives (read)
    if (sram_dq_oe && !sram_oe_n) begin
        $display("[%0t] BUS ERROR: SRAM controller driving DQ while OE# active", $time);
        bus_errors <= bus_errors + 16'd1;
    end

    // CRAM0: WE# and OE# should not both be low (checked in cram_chip_model)
    // CRAM0: ADV# should not be asserted without CE#
    if (!cram0_adv_n && cram0_ce0_n && cram0_ce1_n) begin
        $display("[%0t] BUS ERROR: CRAM0 ADV# low without CE#", $time);
        bus_errors <= bus_errors + 16'd1;
    end
    if (!cram1_adv_n && cram1_ce0_n && cram1_ce1_n) begin
        $display("[%0t] BUS ERROR: CRAM1 ADV# low without CE#", $time);
        bus_errors <= bus_errors + 16'd1;
    end
end

assign assertion_errors = bus_errors + cram0_errors + cram1_errors;

// ============================================================
// BCR Init FSM (from core_top.v — configures both CRAM0 and CRAM1)
// ============================================================
reg         bcr_init_done;
reg [2:0]   bcr_state;
reg         bcr_config_en;
reg [15:0]  bcr_config_data;
reg         bcr_config_bank;
reg         bcr_target_cram1;

localparam [15:0] BCR_VALUE = 16'h641F;

localparam BCR_ST_IDLE      = 3'd0;
localparam BCR_ST_CFG_CE0   = 3'd2;
localparam BCR_ST_WAIT_CE0  = 3'd3;
localparam BCR_ST_CFG_CE1   = 3'd4;
localparam BCR_ST_WAIT_CE1  = 3'd5;
localparam BCR_ST_DONE      = 3'd6;

wire bcr_raw_busy = bcr_target_cram1 ? psram1_raw_busy : psram0_raw_busy;
wire psram0_config_en = bcr_config_en && !bcr_target_cram1;
wire psram1_config_en = bcr_config_en &&  bcr_target_cram1;

assign init_done = bcr_init_done;

initial begin
    bcr_init_done = 0;
    bcr_state = BCR_ST_IDLE;
    bcr_config_en = 0;
    bcr_config_data = 0;
    bcr_config_bank = 0;
    bcr_target_cram1 = 0;
end

always @(posedge clk) begin
    bcr_config_en <= 0;

    if (!reset_n) begin
        bcr_init_done <= 0;
        bcr_state <= BCR_ST_IDLE;
        bcr_target_cram1 <= 0;
    end else begin
        case (bcr_state)
            BCR_ST_IDLE: begin
                bcr_target_cram1 <= 0;
                bcr_state <= BCR_ST_CFG_CE0;
            end

            BCR_ST_CFG_CE0: begin
                if (!bcr_raw_busy) begin
                    bcr_config_en <= 1;
                    bcr_config_data <= BCR_VALUE;
                    bcr_config_bank <= 0;
                    bcr_state <= BCR_ST_WAIT_CE0;
                end
            end

            BCR_ST_WAIT_CE0: begin
                if (!bcr_raw_busy && !bcr_config_en) begin
                    bcr_state <= BCR_ST_CFG_CE1;
                end
            end

            BCR_ST_CFG_CE1: begin
                if (!bcr_raw_busy) begin
                    bcr_config_en <= 1;
                    bcr_config_data <= BCR_VALUE;
                    bcr_config_bank <= 1;
                    bcr_state <= BCR_ST_WAIT_CE1;
                end
            end

            BCR_ST_WAIT_CE1: begin
                if (!bcr_raw_busy && !bcr_config_en) begin
                    if (!bcr_target_cram1) begin
                        bcr_target_cram1 <= 1;
                        bcr_state <= BCR_ST_CFG_CE0;
                    end else begin
                        bcr_state <= BCR_ST_DONE;
                    end
                end
            end

            BCR_ST_DONE: begin
                bcr_init_done <= 1;
            end
        endcase
    end
end

endmodule
