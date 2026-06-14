//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------
//
// FINAL CONVERGENT EXPERIMENT — exercise the gpu_core fbwq AW-ahead OVERLAP
// path to a BYTE-EXACT verdict by backpressuring the framebuffer-WRITE W
// channel EXCLUSIVELY, while keeping the GPU's command-fetch / texture READ
// path uncontended (so doorbell-DMA + fences never starve -> no INVALID runs).
//
// Stack (REAL, same as tb_gpu_wrpath):
//   gpu_core fbwq  -> axi_sdram_arbiter M0  -> axi_sdram_slave
//                  -> pulse adapter (byte-identical to core_top.v)
//                  -> io_sdram_test (== targets/mister/io_sdram.v)
//                  -> sdram_model_full (10-bit column; ring + tex + FB)
//
// HOW THE W CHANNEL IS BACKPRESSURED *EXCLUSIVELY*:
//   The arbiter gates the GPU W channel by gpu_wq fullness:
//       m0_wready = m0w_active && !gpu_wq_full     (axi_sdram_arbiter.v:542)
//   gpu_wq drains when the slave returns B for a GPU write burst
//   (gpu_wq_pop = active_wr && active_wr_gpuq && s_bvalid).  So to make
//   m_wr_wready deassert mid-burst WITHOUT touching the read path, we slow
//   WRITE retirement only: the test-side pulse adapter delays forwarding
//   *write* word-ops to io_sdram by a deterministic stall, while READ
//   word-ops forward immediately.  Write bursts complete more slowly ->
//   gpu_wq fills -> m0_wready deasserts mid-burst -> the GPU's fbwq sees a
//   genuine W stall with burst_remaining==0 held high -> fbwq_aw_ahead_issue
//   fires.  Reads (command-fetch + texture) are never delayed, so the
//   doorbell-DMA keeps up and fences complete.  NO scanout burst injection,
//   NO read-competing aggressors -> command-fetch is never starved.
//
//   The stall is injected purely in the TEST harness pulse adapter (this
//   file); production RTL and the generated io_sdram_test.v are untouched.
//
// AW-ahead PATH PROOF: this file taps the internal fbwq AW-ahead wires
// hierarchically and exports per-cycle event counters so the C++ harness can
// assert the path actually FIRED (a clean result is meaningless otherwise).
//
// TEST-ONLY, ADDITIVE.  No production RTL is modified.
//
`default_nettype none

module tb_gpu_wstall #(
    parameter GPU_HAS_VERT_TRI       = 1,
    parameter GPU_HAS_PARAM_TRI_RECS = 1,
    parameter GPU_Z_READ_WINDOW      = 4,
    parameter GPU_EW_PARALLEL_DIVS   = 1,
    parameter GPU_HAS_COMPACT_SPAN   = 1,
    parameter GPU_HAS_COLUMN_LIST    = 1,
    parameter BANK_ROW_TRACK         = 1
) (
    input  wire        clk,
    input  wire        reset_n,

    // MMIO (driven by C++ harness, mirrors tb_gpu)
    input  wire        reg_wr,
    input  wire [3:0]  reg_addr,
    input  wire [31:0] reg_wdata,
    output wire [31:0] reg_rdata,

    // GPU status
    output wire        busy,
    output wire [31:0] fence_reached,
    output wire [5:0]  dbg_state,

    // ---- W-channel-exclusive write-stall control (from C++) ----
    // wstall_en      : master enable for the write-completion stall
    // wstall_depth   : number of cycles to hold OFF each *write* word-op
    //                  forward (0 = no stall; reads never stalled)
    // wstall_phase   : skip the stall on the first 'phase' write-ops after a
    //                  read, to shift the stall onset relative to burst
    //                  boundaries (alignment sweep)
    input  wire        wstall_en,
    input  wire [7:0]  wstall_depth,
    input  wire [7:0]  wstall_phase,

    // ---- focused trace window (test debug) ----
    input  wire        trace_en,

    // ---- SDRAM backdoor (preload ring/textures; verify FB) ----
    input  wire        bd_we,
    input  wire [23:0] bd_addr,    // word address
    input  wire [31:0] bd_wdata,
    input  wire [23:0] bd_rd_addr, // word address
    output wire [31:0] bd_rd_data,

    // ---- AW-ahead path proof taps (registered counters) ----
    output reg  [31:0] cnt_aw_ahead_issue,    // fbwq_aw_ahead_issue fired
    output reg  [31:0] cnt_w_chain_start,     // fbwq_w_chain_start  fired
    output reg  [31:0] cnt_w_chain_noW,       // chain-start via the !m_wr_wvalid branch
    output reg  [31:0] cnt_wstall_applied,    // write word-ops actually held off
    output reg  [31:0] cnt_wready_low_burst0, // cycles m_wr_wvalid&!m_wr_wready w/ burst_remaining==0
    output reg  [31:0] cnt_gpu_wq_full,       // cycles arbiter gpu_wq_count==8
    output wire [4:0]  dbg_gpu_wq_count,
    output wire [4:0]  dbg_fbwq_count,
    output wire [3:0]  dbg_fbwq_burst_remaining,
    output wire        dbg_aw_ahead_valid
);

// ============================================================
// gpu_core master channels
// ============================================================
wire        gpu_rd_arvalid, gpu_rd_arready;
wire [31:0] gpu_rd_araddr;
wire [7:0]  gpu_rd_arlen;
wire        gpu_rd_rvalid;
wire [31:0] gpu_rd_rdata;
wire        gpu_rd_rlast;

wire        gpu_wr_awvalid, gpu_wr_awready;
wire [31:0] gpu_wr_awaddr;
wire [7:0]  gpu_wr_awlen;
wire        gpu_wr_wvalid, gpu_wr_wready;
wire [31:0] gpu_wr_wdata;
wire [3:0]  gpu_wr_wstrb;
wire        gpu_wr_wlast;
wire        gpu_wr_bvalid;

// SRAM scratch
wire        gpu_sram_rd, gpu_sram_wr, gpu_sram_rd_half, gpu_sram_rd_hi;
wire [21:0] gpu_sram_addr;
wire [31:0] gpu_sram_wdata;
wire [3:0]  gpu_sram_wstrb;
reg  [31:0] gpu_sram_rdata;
reg         gpu_sram_busy;
reg         gpu_sram_rdata_valid;

wire        gpu_swap_req;
wire [1:0]  gpu_swap_idx;

// ============================================================
// gpu_core
// ============================================================
gpu_core #(
    .GPU_HAS_VERT_TRI(GPU_HAS_VERT_TRI),
    .GPU_HAS_PARAM_TRI_RECS(GPU_HAS_PARAM_TRI_RECS),
    .GPU_Z_READ_WINDOW(GPU_Z_READ_WINDOW),
    .GPU_EW_PARALLEL_DIVS(GPU_EW_PARALLEL_DIVS),
    .GPU_HAS_COMPACT_SPAN(GPU_HAS_COMPACT_SPAN),
    .GPU_HAS_COLUMN_LIST(GPU_HAS_COLUMN_LIST)
) gpu (
    .clk(clk), .reset_n(reset_n), .gpu_enable(1'b1),
    .m_rd_arvalid(gpu_rd_arvalid), .m_rd_arready(gpu_rd_arready),
    .m_rd_araddr(gpu_rd_araddr),   .m_rd_arlen(gpu_rd_arlen),
    .m_rd_rvalid(gpu_rd_rvalid),   .m_rd_rdata(gpu_rd_rdata), .m_rd_rlast(gpu_rd_rlast),
    .m_wr_awvalid(gpu_wr_awvalid), .m_wr_awready(gpu_wr_awready),
    .m_wr_awaddr(gpu_wr_awaddr),   .m_wr_awlen(gpu_wr_awlen),
    .m_wr_wvalid(gpu_wr_wvalid),   .m_wr_wready(gpu_wr_wready),
    .m_wr_wdata(gpu_wr_wdata),     .m_wr_wstrb(gpu_wr_wstrb), .m_wr_wlast(gpu_wr_wlast),
    .m_wr_bvalid(gpu_wr_bvalid),
    .sram_rd(gpu_sram_rd), .sram_wr(gpu_sram_wr),
    .sram_rd_half(gpu_sram_rd_half), .sram_rd_hi(gpu_sram_rd_hi),
    .sram_addr(gpu_sram_addr), .sram_wdata(gpu_sram_wdata), .sram_wstrb(gpu_sram_wstrb),
    .sram_rdata(gpu_sram_rdata), .sram_busy(gpu_sram_busy), .sram_rdata_valid(gpu_sram_rdata_valid),
    .gpu_swap_req(gpu_swap_req), .gpu_swap_idx(gpu_swap_idx),
    .slave_swap_pending(1'b0),
    .reg_wr(reg_wr), .reg_addr(reg_addr), .reg_wdata(reg_wdata), .reg_rdata(reg_rdata),
    .busy(busy), .fence_reached(fence_reached),
    .dbg_state(dbg_state), .dbg_setup_step(), .dbg_aux(), .dbg_frag()
);

// ============================================================
// SRAM model (word-level GPU scratch) — 1-cycle latency M10K stub.
// ============================================================
reg [31:0] sram_mem [0:65535];
always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        gpu_sram_busy <= 0; gpu_sram_rdata_valid <= 0; gpu_sram_rdata <= 0;
    end else begin
        gpu_sram_rdata_valid <= 0;
        gpu_sram_busy <= 0;
        if (gpu_sram_wr) begin
            if (gpu_sram_wstrb[0]) sram_mem[gpu_sram_addr[15:0]][7:0]   <= gpu_sram_wdata[7:0];
            if (gpu_sram_wstrb[1]) sram_mem[gpu_sram_addr[15:0]][15:8]  <= gpu_sram_wdata[15:8];
            if (gpu_sram_wstrb[2]) sram_mem[gpu_sram_addr[15:0]][23:16] <= gpu_sram_wdata[23:16];
            if (gpu_sram_wstrb[3]) sram_mem[gpu_sram_addr[15:0]][31:24] <= gpu_sram_wdata[31:24];
        end
        if (gpu_sram_rd) begin
            gpu_sram_rdata <= sram_mem[gpu_sram_addr[15:0]];
            gpu_sram_rdata_valid <= 1;
        end
    end
end

// ============================================================
// Arbiter <-> Slave wires
// ============================================================
wire        arb_s_arvalid, arb_s_arready;
wire [31:0] arb_s_araddr;
wire [7:0]  arb_s_arlen;
wire        arb_s_rvalid,  arb_s_rready;
wire [31:0] arb_s_rdata;
wire [1:0]  arb_s_rresp;
wire        arb_s_rlast;
wire        arb_s_awvalid, arb_s_awready;
wire [31:0] arb_s_awaddr;
wire [7:0]  arb_s_awlen;
wire        arb_s_wvalid,  arb_s_wready;
wire [31:0] arb_s_wdata;
wire [3:0]  arb_s_wstrb;
wire        arb_s_wlast;
wire        arb_s_bvalid;
wire [1:0]  arb_s_bresp;
wire        arb_s_wcont;

// Slave <-> io_sdram word interface
wire        sdram_slave_rd, sdram_slave_wr;
wire [23:0] sdram_slave_addr;
wire [31:0] sdram_slave_wdata;
wire [3:0]  sdram_slave_wstrb;
wire [3:0]  sdram_slave_burst_len;
wire [3:0]  sdram_slave_burst_wr_len;
wire        sdram_slave_wr_data_next;
wire        sdram_slave_wr_done;
wire [31:0] sdram_slave_next_wdata;
wire [3:0]  sdram_slave_next_wstrb;
wire [31:0] sdram_slave_preload_wdata;
wire [3:0]  sdram_slave_preload_wstrb;

reg         ram1_word_rd, ram1_word_wr;
reg  [23:0] ram1_word_addr;
reg  [31:0] ram1_word_data;
reg  [3:0]  ram1_word_wstrb;
reg  [31:0] ram1_word_data_next;
reg  [3:0]  ram1_word_wstrb_next;
reg  [3:0]  ram1_word_burst_len;
reg  [3:0]  ram1_word_burst_wr_len;
wire [31:0] ram1_word_q;
wire        ram1_word_busy;
wire        ram1_word_q_valid;

wire        phy_cke, phy_clk, phy_cas, phy_ras, phy_we;
wire [1:0]  phy_ba;
wire [12:0] phy_a;
wire [1:0]  phy_dqm;
wire [15:0] ctrl_dq_out;
wire        ctrl_dq_oe;
wire [15:0] model_dq_out;
wire        model_dq_oe;
wire [15:0] dq_to_ctrl = model_dq_oe ? model_dq_out : 16'h0;

// ============================================================
// Slave -> io_sdram pulse adapter (byte-identical to core_top.v) WITH a
// TEST-ONLY W-channel-exclusive write-completion stall.
//
// Production behaviour: forward a slave word-op into io_sdram whenever
//   (!ram1_word_busy && !sdram_cmd_forwarded && (slave_rd || slave_wr)).
//
// Stall injection (test only): when wstall_en and the pending op is a WRITE,
// hold OFF the forward for wstall_depth cycles (after a wstall_phase grace).
// READS are NEVER held off.  This delays write-burst retirement -> gpu_wq
// fills -> the GPU W channel backpressures mid-burst, with the command-fetch
// read path completely unaffected.
// ============================================================
reg sdram_accepted_r;
reg sdram_cmd_forwarded;

// Deterministic PERIODIC duty-cycle write-stall.  A free-running counter
// repeats a period of WSTALL_PERIOD cycles: during the first wstall_depth
// cycles of each period, WRITE word-op forwards are BLOCKED; the rest of the
// period they flow.  During the blocked window the slave cannot retire GPU
// write bursts, so the arbiter gpu_wq FILLS and m_wr_wready deasserts; a
// burst whose last beat lands in the window then sits with burst_remaining==0
// and wready low -> fbwq_aw_ahead_issue fires.  During the open window the
// queue drains and multi-beat bursts re-form -> the next blocked window again
// catches burst boundaries.  This produces the genuine bursty MiSTer-style
// W-backpressure WITHOUT perpetual jam (which would collapse bursts to length
// 1) and WITHOUT touching READS (command-fetch unaffected).  wstall_phase
// offsets the period so the blocked window aligns to different burst phases.
localparam [9:0] WSTALL_PERIOD = 10'd64;
reg [9:0] wstall_cyc;        // free-running position in the period

wire op_is_write = sdram_slave_wr;
wire op_is_read  = sdram_slave_rd;
// Blocked while the period position is below wstall_depth.
wire in_block_window = wstall_en && (wstall_depth != 8'd0)
                     && (wstall_cyc < {2'b0, wstall_depth});
wire stall_active = in_block_window && op_is_write;

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        ram1_word_rd <= 0; ram1_word_wr <= 0;
        ram1_word_addr <= 0; ram1_word_data <= 0; ram1_word_wstrb <= 0;
        ram1_word_data_next <= 0; ram1_word_wstrb_next <= 0;
        ram1_word_burst_len <= 0; ram1_word_burst_wr_len <= 0;
        sdram_accepted_r <= 0; sdram_cmd_forwarded <= 0;
        wstall_cyc <= ({2'b0, wstall_phase} % WSTALL_PERIOD);  // phase offset
        cnt_wstall_applied <= 0;
    end else begin
        ram1_word_rd <= 0; ram1_word_wr <= 0;
        ram1_word_burst_len <= 4'd0; ram1_word_burst_wr_len <= 4'd0;
        sdram_accepted_r <= 0;
        wstall_cyc <= (wstall_cyc + 10'd1 >= WSTALL_PERIOD) ? 10'd0 : (wstall_cyc + 10'd1);
        // Count cycles where a pending write is actually being held off.
        if (stall_active && !sdram_cmd_forwarded)
            cnt_wstall_applied <= cnt_wstall_applied + 1;

        if (!sdram_slave_rd && !sdram_slave_wr)
            sdram_cmd_forwarded <= 0;

        if (!ram1_word_busy && !sdram_cmd_forwarded &&
            (sdram_slave_rd || sdram_slave_wr) && !stall_active) begin
            ram1_word_rd <= sdram_slave_rd;
            ram1_word_wr <= sdram_slave_wr;
            ram1_word_addr <= sdram_slave_addr;
            ram1_word_data <= sdram_slave_wdata;
            ram1_word_wstrb <= sdram_slave_wstrb;
            ram1_word_data_next <= sdram_slave_preload_wdata;
            ram1_word_wstrb_next <= sdram_slave_preload_wstrb;
            ram1_word_burst_len <= sdram_slave_burst_len;
            ram1_word_burst_wr_len <= sdram_slave_burst_wr_len;
            sdram_accepted_r <= 1;
            sdram_cmd_forwarded <= 1;
        end
    end
end

// ============================================================
// Arbiter — M0 = gpu_core only (rd -> AR, wr -> AW/W).  M1/M2/M3 idle so the
// READ path is uncontended (command-fetch never starves).  The W channel is
// backpressured purely by the write-completion stall above.
// ============================================================
axi_sdram_arbiter sdram_arb (
    .clk(clk), .reset_n(reset_n),

    .m0_arvalid(gpu_rd_arvalid), .m0_arready(gpu_rd_arready),
    .m0_araddr(gpu_rd_araddr),   .m0_arlen(gpu_rd_arlen),
    .m0_rvalid(gpu_rd_rvalid),   .m0_rdata(gpu_rd_rdata),
    .m0_rresp(),                 .m0_rlast(gpu_rd_rlast),
    .m0_awvalid(gpu_wr_awvalid), .m0_awready(gpu_wr_awready),
    .m0_awaddr(gpu_wr_awaddr),   .m0_awlen(gpu_wr_awlen),
    .m0_wvalid(gpu_wr_wvalid),   .m0_wready(gpu_wr_wready),
    .m0_wdata(gpu_wr_wdata),     .m0_wstrb(gpu_wr_wstrb),
    .m0_wlast(gpu_wr_wlast),
    .m0_bvalid(gpu_wr_bvalid),   .m0_bresp(),

    .m1_arvalid(1'b0), .m1_arready(),
    .m1_araddr(32'b0), .m1_arlen(8'b0),
    .m1_rvalid(),      .m1_rdata(),
    .m1_rresp(),       .m1_rlast(),
    .m1_rready(1'b1),
    .m1_awvalid(1'b0), .m1_awready(),
    .m1_awaddr(32'b0), .m1_awlen(8'b0),
    .m1_wvalid(1'b0),  .m1_wready(),
    .m1_wdata(32'b0),  .m1_wstrb(4'b0),
    .m1_wlast(1'b0),
    .m1_bvalid(),      .m1_bresp(),

    .m2_arvalid(1'b0), .m2_arready(),
    .m2_araddr(32'b0), .m2_arlen(8'b0),
    .m2_rvalid(),      .m2_rdata(),
    .m2_rresp(),       .m2_rlast(),
    .m2_rready(1'b1),
    .m2_awvalid(1'b0), .m2_awready(),
    .m2_awaddr(32'b0), .m2_awlen(8'b0),
    .m2_wvalid(1'b0),  .m2_wready(),
    .m2_wdata(32'b0),  .m2_wstrb(4'b0),
    .m2_wlast(1'b0),
    .m2_bvalid(),      .m2_bresp(),

    .m3_arvalid(1'b0), .m3_arready(),
    .m3_araddr(32'b0), .m3_arlen(8'b0),
    .m3_rvalid(),      .m3_rdata(),
    .m3_rresp(),       .m3_rlast(),
    .m3_rready(1'b1),

    .s_arvalid(arb_s_arvalid), .s_arready(arb_s_arready),
    .s_araddr(arb_s_araddr),   .s_arlen(arb_s_arlen),
    .s_rvalid(arb_s_rvalid),   .s_rready(arb_s_rready),
    .s_rdata(arb_s_rdata),
    .s_rresp(arb_s_rresp),     .s_rlast(arb_s_rlast),
    .s_awvalid(arb_s_awvalid), .s_awready(arb_s_awready),
    .s_awaddr(arb_s_awaddr),   .s_awlen(arb_s_awlen),
    .s_wvalid(arb_s_wvalid),   .s_wready(arb_s_wready),
    .s_wdata(arb_s_wdata),     .s_wstrb(arb_s_wstrb),
    .s_wlast(arb_s_wlast),
    .s_bvalid(arb_s_bvalid),   .s_bresp(arb_s_bresp),
    .s_wcont(arb_s_wcont),

    .dbg_arb_state(),
    .dbg_cpu_pending(),
    .dbg_grant()
);

// ============================================================
// Slave
// ============================================================
axi_sdram_slave #(
    .SERIALIZE_WRITE_BURSTS(1'b0),
    .MAX_NATIVE_WRITE_BURST_LEN(8'd15)
) slave (
    .clk(clk), .reset_n(reset_n),
    .s_axi_arvalid(arb_s_arvalid), .s_axi_arready(arb_s_arready),
    .s_axi_araddr(arb_s_araddr),   .s_axi_arlen(arb_s_arlen),
    .s_axi_rvalid(arb_s_rvalid),   .s_axi_rready(arb_s_rready),
    .s_axi_rdata(arb_s_rdata),     .s_axi_rresp(arb_s_rresp), .s_axi_rlast(arb_s_rlast),
    .s_axi_awvalid(arb_s_awvalid), .s_axi_awready(arb_s_awready),
    .s_axi_awaddr(arb_s_awaddr),   .s_axi_awlen(arb_s_awlen),
    .s_axi_wvalid(arb_s_wvalid),   .s_axi_wready(arb_s_wready),
    .s_axi_wdata(arb_s_wdata),     .s_axi_wstrb(arb_s_wstrb), .s_axi_wlast(arb_s_wlast),
    .s_axi_bvalid(arb_s_bvalid),   .s_axi_bready(1'b1),       .s_axi_bresp(arb_s_bresp),
    .s_axi_wcont(arb_s_wcont),
    .sdram_rd(sdram_slave_rd), .sdram_wr(sdram_slave_wr),
    .sdram_addr(sdram_slave_addr), .sdram_wdata(sdram_slave_wdata), .sdram_wstrb(sdram_slave_wstrb),
    .sdram_burst_len(sdram_slave_burst_len), .sdram_burst_wr_len(sdram_slave_burst_wr_len),
    .sdram_rdata(ram1_word_q), .sdram_busy(ram1_word_busy),
    .sdram_accepted(sdram_accepted_r), .sdram_rdata_valid(ram1_word_q_valid),
    .sdram_wr_data_next(sdram_slave_wr_data_next),
    .sdram_wr_done(sdram_slave_wr_done),
    .sdram_next_wdata(sdram_slave_next_wdata),
    .sdram_next_wstrb(sdram_slave_next_wstrb),
    .sdram_preload_wdata(sdram_slave_preload_wdata),
    .sdram_preload_wstrb(sdram_slave_preload_wstrb)
);

// ============================================================
// io_sdram (MiSTer controller; split-DQ test variant).  No scanout burst.
// ============================================================
io_sdram #(
    .BANK_ROW_TRACK(BANK_ROW_TRACK)
) sdram_ctrl (
    .controller_clk(clk), .chip_clk(clk), .clk_90(clk), .reset_n(reset_n),
    .phy_cke(phy_cke), .phy_clk(phy_clk),
    .phy_cas(phy_cas), .phy_ras(phy_ras), .phy_we(phy_we),
    .phy_ba(phy_ba), .phy_a(phy_a),
    .phy_dq_in(dq_to_ctrl),
    .phy_dq_out_port(ctrl_dq_out),
    .phy_dq_oe_port(ctrl_dq_oe),
    .phy_dqm(phy_dqm),
    .burst_rd(1'b0), .burst_addr(25'b0), .burst_len(11'b0), .burst_32bit(1'b1),
    .burst_data(), .burst_data_valid(), .burst_data_done(),
    .burstwr(1'b0), .burstwr_addr(25'b0), .burstwr_ready(),
    .burstwr_strobe(1'b0), .burstwr_data(16'b0), .burstwr_done(1'b0),
    .word_rd(ram1_word_rd), .word_wr(ram1_word_wr),
    .word_addr(ram1_word_addr), .word_data(ram1_word_data), .word_wstrb(ram1_word_wstrb),
    .word_data_next(ram1_word_data_next), .word_wstrb_next(ram1_word_wstrb_next),
    .word_burst_len(ram1_word_burst_len), .word_burst_wr_len(ram1_word_burst_wr_len),
    .word_q(ram1_word_q), .word_busy(ram1_word_busy), .word_q_valid(ram1_word_q_valid),
    .word_wr_data_next(sdram_slave_wr_data_next),
    .word_wr_done(sdram_slave_wr_done),
    .burst_wr_direct_data(sdram_slave_next_wdata),
    .burst_wr_direct_strb(sdram_slave_next_wstrb),
    .dbg_io()
);

// ============================================================
// SDRAM model (full 10-bit column) — holds ring + textures + FB.
// ============================================================
sdram_model_full sdram_chip (
    .clk(phy_clk), .cke(phy_cke), .cs_n(1'b0),
    .ras_n(phy_ras), .cas_n(phy_cas), .we_n(phy_we),
    .ba(phy_ba), .a(phy_a),
    .dq_in(ctrl_dq_out),
    .dq_out(model_dq_out), .dq_oe(model_dq_oe),
    .dqm(phy_dqm),
    .bd_we(bd_we), .bd_word_addr(bd_addr), .bd_wdata(bd_wdata),
    .bd_rd_word_addr(bd_rd_addr), .bd_rd_data(bd_rd_data)
);

// ============================================================
// AW-ahead PATH PROOF — tap internal fbwq wires hierarchically and count
// the events that prove the path FIRED.  Read-only references; no drive.
// ============================================================
assign dbg_gpu_wq_count          = sdram_arb.gpu_wq_count;
assign dbg_fbwq_count            = gpu.fbwq_count;
assign dbg_fbwq_burst_remaining  = gpu.fbwq_burst_remaining;
assign dbg_aw_ahead_valid        = gpu.fbwq_aw_ahead_valid;

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        cnt_aw_ahead_issue    <= 0;
        cnt_w_chain_start     <= 0;
        cnt_w_chain_noW       <= 0;
        cnt_wready_low_burst0 <= 0;
        cnt_gpu_wq_full       <= 0;
    end else begin
        if (gpu.fbwq_aw_ahead_issue) cnt_aw_ahead_issue <= cnt_aw_ahead_issue + 1;
        if (gpu.fbwq_w_chain_start)  cnt_w_chain_start  <= cnt_w_chain_start  + 1;
        if (gpu.fbwq_w_chain_start && !gpu.m_wr_wvalid)
            cnt_w_chain_noW <= cnt_w_chain_noW + 1;
        // genuine W backpressure at the last beat (the AW-ahead trigger window)
        if (gpu.m_wr_wvalid && !gpu.m_wr_wready && (gpu.fbwq_burst_remaining == 4'd0))
            cnt_wready_low_burst0 <= cnt_wready_low_burst0 + 1;
        if (sdram_arb.gpu_wq_count == 4'd8)
            cnt_gpu_wq_full <= cnt_gpu_wq_full + 1;
    end
end

// Focused per-cycle trace of the AW-ahead handshake (bounded by trace_en in C++).
always @(posedge clk) begin
    if (reset_n && trace_en) begin
        if (gpu.fbwq_aw_ahead_issue)
            $display("[%0t] AW_AHEAD_ISSUE  wq=%0d fbwq_cnt=%0d br=%0d start_words=%0d head_final=%b link[rd]=%b link[rd+1]=%b head_addr=%08x rdp=%0d wrp=%0d",
                     $time, sdram_arb.gpu_wq_count, gpu.fbwq_count, gpu.fbwq_burst_remaining,
                     gpu.fbwq_start_burst_words, gpu.fbwq_head_chain_final,
                     gpu.fbwq_link_next[gpu.fbwq_rd_ptr], gpu.fbwq_link_next[gpu.fbwq_rd_ptr_1],
                     gpu.fbwq_addr[gpu.fbwq_rd_ptr], gpu.fbwq_rd_ptr, gpu.fbwq_wr_ptr);
        if (gpu.fbwq_w_chain_start)
            $display("[%0t] W_CHAIN_START   wq=%0d fbwq_cnt=%0d br=%0d ahead_words=%0d retiring=%b !wvalid=%b",
                     $time, sdram_arb.gpu_wq_count, gpu.fbwq_count, gpu.fbwq_burst_remaining,
                     gpu.fbwq_aw_ahead_words, gpu.fbwq_w_tail_retiring, !gpu.m_wr_wvalid);
    end
end

endmodule

`default_nettype wire
