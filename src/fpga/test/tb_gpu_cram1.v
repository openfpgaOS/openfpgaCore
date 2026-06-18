//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------
//
// Verilator Testbench: GPU Core driving its REAL texture/colormap fills
// through the REAL CRAM1 sync-burst chain (not the SDRAM stub).
//
// This is a faithful clone of tb_gpu.v (same module name `tb_gpu`, same
// external port list, so the existing tb_gpu_acceptance_main.cpp harness
// links against it UNCHANGED) PLUS:
//
//   1. The full CRAM1 chain wired to gpu_core's gpu_tex_mem_* fill master:
//        gpu_core.gpu_tex_mem_* -> gpu_cram1_tex_adapter
//                              -> cram1_controller(#CLOCK_SPEED 100) + cram1_phy
//                              -> cram_chip_model (pin-level behavioral)
//      A BCR-init FSM configures both dies to sync-burst 0x641F before use.
//
//   2. DATA MIRRORING: every backdoor SDRAM write (bd_we/bd_addr/bd_wdata)
//      is ALSO written into the cram_chip_model at the SAME 32-bit word
//      address (the adapter maps GPU byte addr araddr[23:2] == SDRAM word
//      addr).  So the GPU fetches byte-identical data whether reg14 picks the
//      SDRAM route (bit0=0) or the CRAM1 route (bit0=1) -- any palette
//      difference is PURELY the CRAM1 timing path, not the data.
//
//   3. reg14 (GPU_TEX_MEM_ROUTE) is written by the C++ harness (reg_wr addr=14
//      wdata=1) AFTER reset+BCR to route the tex-cache fill master to CRAM1.
//
// IMPORTANT addressing constraint (kept by the harness): every byte address
// the GPU fetches via CRAM1 (textures + colormap) must have CRAM1 word-addr
// bits [21:19] == 0 (byte addr < 0x200000) so the chip-model backdoor
// (bd_word_addr[18:0] + bit20 die) and the controller (burst_addr[19:0] +
// bit21 die) agree 1:1.  TEX_BASE/PALOOKUP_BASE are chosen accordingly.
//
// HW symptom being reproduced: with GPU texture/colormap fetches routed to
// CRAM1, TEXELS render correctly but the per-pixel COLORMAP (palookup) byte
// returns the WRONG palette.  Rendered-but-wrong, NOT a hang.
//

`default_nettype none

module tb_gpu #(
    parameter INCLUDE_PARAM_TRI      = 1,
    parameter INCLUDE_VERT_TRI       = 1,
    parameter INCLUDE_PARAM_TRI_RECS = 1,
    parameter GPU_Z_READ_WINDOW      = 4,
    parameter GPU_EW_PARALLEL_DIVS   = 1,
    parameter INCLUDE_COMPACT_SPAN   = 1,
    parameter INCLUDE_COLUMN_LIST    = 1
) (
    input  wire        clk,
    input  wire        reset_n,

    // MMIO register interface (driven from C++)
    input  wire        reg_wr,
    input  wire [3:0]  reg_addr,
    input  wire [31:0] reg_wdata,
    output wire [31:0] reg_rdata,

    // Status outputs
    output wire        busy,
    output wire [31:0] fence_reached,
    output wire [5:0]  dbg_state,
    output wire [5:0]  dbg_setup_step,
    output wire [31:0] dbg_aux,
    output wire [31:0] dbg_frag,
    output reg  [31:0] dbg_aw_count,
    output reg  [31:0] dbg_aw_burst_count,
    output reg  [7:0]  dbg_aw_max_len,

    output wire [9:0]  dbg_rd_last_latency_o,
    output wire [31:0] dbg_rd_txn_count_o,

    output wire        gpu_swap_req,
    output wire [1:0]  gpu_swap_idx,

    input  wire        slave_swap_pending,

    // SDRAM backdoor write (preload textures, ring buffer, etc.)
    input  wire        bd_we,
    input  wire [23:0] bd_addr,    // word address
    input  wire [31:0] bd_wdata,
    // When high during a bd_we, the write lands in SDRAM ONLY (NOT mirrored
    // into the CRAM1 chip).  The harness uses it to POISON the SDRAM colormap
    // copy while leaving CRAM1 correct: if the CRAM1-route render is still
    // byte-exact, the colormap data provably came from CRAM1, not SDRAM.
    input  wire        bd_no_mirror,

    // SDRAM backdoor read (verify framebuffer)
    input  wire [23:0] bd_rd_addr,
    output wire [31:0] bd_rd_data,

    // ----------------------------------------------------------------
    // CRAM1 repro observability (ADDITIVE, test-only).  Surfaced so the
    // C++ harness can confirm the CRAM1 route is actually engaged and so a
    // VCD / printf can capture cycle-level evidence at a failing pixel.
    // ----------------------------------------------------------------
    output wire        cram1_route_active,   // gpu.gpu_tex_mem_route (reg14 bit0)
    output wire        cram1_bcr_done,
    output wire [31:0] cram1_fill_count,     // # of CRAM1 line fills serviced
    output wire [31:0] cram1_chip_errors,    // chip-model internal error count
    output wire [31:0] cram1_skid_push_count,// # of port-B skid stale-push events
    output wire [31:0] cram1_b_stall_cycles, // cycles port B held off (req_valid_b & !req_ready_b)
    output wire [31:0] cram1_b_accept_count  // # of port-B requests accepted by the cache
);

// ============================================================
// GPU AXI4 Read Master signals (SDRAM path)
// ============================================================
wire        gpu_rd_arvalid;
wire        gpu_rd_arready;
wire [31:0] gpu_rd_araddr;
wire [7:0]  gpu_rd_arlen;
wire        gpu_rd_rvalid;
wire [31:0] gpu_rd_rdata;
wire        gpu_rd_rlast;

// ============================================================
// GPU AXI4 Write Master signals
// ============================================================
wire        gpu_wr_awvalid;
wire        gpu_wr_awready;
wire [31:0] gpu_wr_awaddr;
wire [7:0]  gpu_wr_awlen;
wire        gpu_wr_wvalid;
wire        gpu_wr_wready;
wire [31:0] gpu_wr_wdata;
wire [3:0]  gpu_wr_wstrb;
wire        gpu_wr_wlast;
wire        gpu_wr_bvalid;

// ============================================================
// GPU fast-texture redirect master (gpu_tex_mem_*) — wired to CRAM1 chain
// ============================================================
wire        gpu_tex_mem_arvalid;
wire        gpu_tex_mem_arready;
wire [31:0] gpu_tex_mem_araddr;
wire [7:0]  gpu_tex_mem_arlen;
wire        gpu_tex_mem_rvalid;
wire [31:0] gpu_tex_mem_rdata;
wire        gpu_tex_mem_rlast;

// CRAM1 MMIO upload port (unused here — backdoor preload instead). Tie-off.
wire        gpu_tex_mem_up_wr;
wire [21:0] gpu_tex_mem_up_addr;
wire [31:0] gpu_tex_mem_up_wdata;

// ============================================================
// GPU SRAM scratch signals
// ============================================================
wire        gpu_sram_rd;
wire        gpu_sram_wr;
wire        gpu_sram_rd_half;
wire        gpu_sram_rd_hi;
wire [21:0] gpu_sram_addr;
wire [31:0] gpu_sram_wdata;
wire [3:0]  gpu_sram_wstrb;
wire [31:0] gpu_sram_rdata;
wire        gpu_sram_busy;
wire        gpu_sram_rdata_valid;

// ============================================================
// GPU Core
// ============================================================
gpu_core #(
    .INCLUDE_PARAM_TRI(INCLUDE_PARAM_TRI),
    .INCLUDE_VERT_TRI(INCLUDE_VERT_TRI),
    .INCLUDE_PARAM_TRI_RECS(INCLUDE_PARAM_TRI_RECS),
    .GPU_Z_READ_WINDOW(GPU_Z_READ_WINDOW),
    .GPU_EW_PARALLEL_DIVS(GPU_EW_PARALLEL_DIVS),
    .INCLUDE_COMPACT_SPAN(INCLUDE_COMPACT_SPAN),
    .INCLUDE_COLUMN_LIST(INCLUDE_COLUMN_LIST),
    .INCLUDE_TEX_MEM(1)
) gpu (
    .clk(clk),
    .reset_n(reset_n),
    .gpu_enable(1'b1),
    // AXI4 read (SDRAM)
    .m_rd_arvalid(gpu_rd_arvalid),
    .m_rd_arready(gpu_rd_arready),
    .m_rd_araddr(gpu_rd_araddr),
    .m_rd_arlen(gpu_rd_arlen),
    .m_rd_rvalid(gpu_rd_rvalid),
    .m_rd_rdata(gpu_rd_rdata),
    .m_rd_rlast(gpu_rd_rlast),
    // CRAM0/CRAM1 texture redirect master
    .gpu_tex_mem_arvalid(gpu_tex_mem_arvalid),
    .gpu_tex_mem_arready(gpu_tex_mem_arready),
    .gpu_tex_mem_araddr(gpu_tex_mem_araddr),
    .gpu_tex_mem_arlen(gpu_tex_mem_arlen),
    .gpu_tex_mem_rvalid(gpu_tex_mem_rvalid),
    .gpu_tex_mem_rdata(gpu_tex_mem_rdata),
    .gpu_tex_mem_rlast(gpu_tex_mem_rlast),
    // CRAM1 MMIO upload (unused — tie busy low)
    .gpu_tex_mem_up_wr(gpu_tex_mem_up_wr),
    .gpu_tex_mem_up_addr(gpu_tex_mem_up_addr),
    .gpu_tex_mem_up_wdata(gpu_tex_mem_up_wdata),
    .gpu_tex_mem_up_busy(1'b0),
    // AXI4 write
    .m_wr_awvalid(gpu_wr_awvalid),
    .m_wr_awready(gpu_wr_awready),
    .m_wr_awaddr(gpu_wr_awaddr),
    .m_wr_awlen(gpu_wr_awlen),
    .m_wr_wvalid(gpu_wr_wvalid),
    .m_wr_wready(gpu_wr_wready),
    .m_wr_wdata(gpu_wr_wdata),
    .m_wr_wstrb(gpu_wr_wstrb),
    .m_wr_wlast(gpu_wr_wlast),
    .m_wr_bvalid(gpu_wr_bvalid),
    // SRAM scratch
    .sram_rd(gpu_sram_rd),
    .sram_wr(gpu_sram_wr),
    .sram_rd_half(gpu_sram_rd_half),
    .sram_rd_hi(gpu_sram_rd_hi),
    .sram_addr(gpu_sram_addr),
    .sram_wdata(gpu_sram_wdata),
    .sram_wstrb(gpu_sram_wstrb),
    .sram_rdata(gpu_sram_rdata),
    .sram_busy(gpu_sram_busy),
    .sram_rdata_valid(gpu_sram_rdata_valid),
    // CMD_FLIP side-port
    .gpu_swap_req(gpu_swap_req),
    .gpu_swap_idx(gpu_swap_idx),
    .slave_swap_pending(slave_swap_pending),
    // MMIO
    .reg_wr(reg_wr),
    .reg_addr(reg_addr),
    .reg_wdata(reg_wdata),
    .reg_rdata(reg_rdata),
    // Status
    .busy(busy),
    .fence_reached(fence_reached),
    .dbg_state(dbg_state),
    .dbg_setup_step(dbg_setup_step),
    .dbg_aux(dbg_aux),
    .dbg_frag(dbg_frag)
);

// ============================================================
// CRAM1 CHAIN: adapter -> controller(+phy) -> chip model
// ============================================================
// adapter <-> controller burst
wire        burst_rd; wire [21:0] burst_addr; wire [4:0] burst_len;
wire [31:0] burst_q; wire burst_q_valid, burst_busy;

// controller <-> chip pins
wire [21:16] cram_a;
wire [15:0]  cram_ctrl_dq_out; wire cram_ctrl_dq_oe; wire [15:0] cram_chip_dq_out;
wire [15:0]  cram_dq_to_ctrl = cram_ctrl_dq_oe ? cram_ctrl_dq_out : cram_chip_dq_out;
wire cram_wait, cram_clk, cram_adv_n, cram_cre, cram_ce0_n, cram_ce1_n, cram_oe_n, cram_we_n, cram_ub_n, cram_lb_n;

// CPU word path into the controller — unused (texture is backdoor-loaded). Tie off.
wire [31:0] cword_q; wire cword_busy, cword_q_valid;

// BCR config
reg  config_en = 0, config_bank_sel = 0;
wire raw_busy, bcr_init_done;

// chip backdoor (mirrored from SDRAM backdoor)
reg         cbd_we = 0; reg [20:0] cbd_word_addr = 0; reg [31:0] cbd_wdata_word = 0;
wire [15:0] cram_errors;

gpu_cram1_tex_adapter adapter (
    .clk(clk), .reset_n(reset_n),
    .arvalid(gpu_tex_mem_arvalid), .arready(gpu_tex_mem_arready),
    .araddr(gpu_tex_mem_araddr), .arlen(gpu_tex_mem_arlen),
    .rvalid(gpu_tex_mem_rvalid), .rdata(gpu_tex_mem_rdata), .rlast(gpu_tex_mem_rlast),
    .burst_rd(burst_rd), .burst_addr(burst_addr), .burst_len(burst_len),
    .burst_q(burst_q), .burst_q_valid(burst_q_valid), .burst_busy(burst_busy)
);

cram1_controller #(.CLOCK_SPEED(100.0)) ctrl (
    .clk(clk), .reset_n(reset_n),
    .word_rd(1'b0), .word_wr(1'b0), .word_addr(22'd0),
    .word_data(32'd0), .word_wstrb(4'hF),
    .word_q(cword_q), .word_busy(cword_busy), .word_q_valid(cword_q_valid),
    .burst_rd(burst_rd), .burst_addr(burst_addr), .burst_len(burst_len),
    .burst_q(burst_q), .burst_q_valid(burst_q_valid), .burst_busy(burst_busy),
    .config_en(config_en), .config_data(16'h641F), .config_bank_sel(config_bank_sel),
    .raw_busy(raw_busy), .bcr_init_done(bcr_init_done),
    .cram_a(cram_a), .cram_dq_out(cram_ctrl_dq_out), .cram_dq_oe(cram_ctrl_dq_oe),
    .cram_dq_in(cram_dq_to_ctrl), .cram_wait(cram_wait), .cram_clk(cram_clk),
    .cram_adv_n(cram_adv_n), .cram_cre(cram_cre),
    .cram_ce0_n(cram_ce0_n), .cram_ce1_n(cram_ce1_n),
    .cram_oe_n(cram_oe_n), .cram_we_n(cram_we_n),
    .cram_ub_n(cram_ub_n), .cram_lb_n(cram_lb_n)
);

cram_chip_model #(.POWERUP_CYCLES(8'd4)) chip (
    .clk(clk), .cram_clk(clk), .reset_n(reset_n),
    .cram_a(cram_a),
    .cram_dq_in(cram_ctrl_dq_oe ? cram_ctrl_dq_out : 16'h0),
    .cram_dq_out(cram_chip_dq_out), .cram_dq_oe(cram_ctrl_dq_oe),
    .cram_wait_out(cram_wait),
    .cram_adv_n(cram_adv_n), .cram_cre(cram_cre),
    .cram_ce0_n(cram_ce0_n), .cram_ce1_n(cram_ce1_n),
    .cram_oe_n(cram_oe_n), .cram_we_n(cram_we_n),
    .cram_ub_n(cram_ub_n), .cram_lb_n(cram_lb_n),
    .cram_wr_strobe(1'b0),
    .bd_we(cbd_we), .bd_word_addr(cbd_word_addr), .bd_wdata_word(cbd_wdata_word),
    .error_count(cram_errors)
);

// ---- BCR-init FSM: pulse config_en per die (sync burst 0x641F) ----
reg [3:0] bcr_st = 0; integer warm = 0;
localparam B_WAIT=0, B_P0=1, B_B0=2, B_I0=3, B_P1=4, B_B1=5, B_I1=6, B_DONE=7;
always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin bcr_st<=B_WAIT; config_en<=0; config_bank_sel<=0; warm<=0; end
    else begin
        config_en <= 1'b0;
        case (bcr_st)
        B_WAIT: if (warm < 20) warm <= warm + 1; else bcr_st <= B_P0;
        B_P0:   begin config_bank_sel<=1'b0; config_en<=1'b1; bcr_st<=B_B0; end
        B_B0:   if (raw_busy) bcr_st<=B_I0;
        B_I0:   if (!raw_busy) bcr_st<=B_P1;
        B_P1:   begin config_bank_sel<=1'b1; config_en<=1'b1; bcr_st<=B_B1; end
        B_B1:   if (raw_busy) bcr_st<=B_I1;
        B_I1:   if (!raw_busy) bcr_st<=B_DONE;
        B_DONE: ;
        endcase
    end
end

assign cram1_route_active = gpu.gpu_tex_mem_route;
assign cram1_bcr_done     = bcr_init_done;
assign cram1_chip_errors  = {16'd0, cram_errors};

// CRAM1 fill counter: count adapter S_FILL entries (each = one line fill).
reg [1:0] adp_st_p = 0;
reg [31:0] fill_cnt = 0;
always @(posedge clk) begin
    if (!reset_n) begin fill_cnt <= 0; adp_st_p <= 0; end
    else begin
        if (adapter.state == 2'd1 && adp_st_p != 2'd1) fill_cnt <= fill_cnt + 32'd1;
        adp_st_p <= adapter.state;
    end
end
assign cram1_fill_count = fill_cnt;

// ============================================================
// DECISIVE skid stale-push observability (ADDITIVE, test-only).
// Taps the REAL gpu_tex_cache port-B handshake to detect the exact event
// described at gpu_tex_cache.v L425-431: an accept_b that lands WITHOUT a
// same-cycle resp_pop_b while pipe_b_live held a response -> the displaced
// response is pushed into the held skid.  If that response was ALREADY
// consumed (the prior pixel's byte), the skid now carries a STALE byte that
// corrupts the NEXT port-B read -> the wrong/shifted palette.
//
// We separate two sub-cases:
//   * push_total    : every accept_b that moves pipe_b_live into held skid
//                     without a same-cycle pop (the structural skid push).
//   * push_stale    : that push where the displaced response was already
//                     consumed (resp_pop_b was low AND the response had been
//                     popped previously) -> the genuine corruption trigger.
// Because the cache only ever holds <=1 live + <=1 skid, the corruption
// trigger is precisely: accept_b & !resp_pop_b & pipe_b_live, i.e. the
// pipe slot still holds an UNPOPPED response when a new accept lands.  That
// is the same condition the cache uses at L427.  We count it.
wire tc_accept_b      = gpu.tex_cache.req_valid_b && gpu.tex_cache.req_ready_b;
wire tc_resp_pop_b    = gpu.tex_cache.resp_pop_b;
wire tc_pipe_b_live   = gpu.tex_cache.pipe_b_live;
wire tc_held_valid_b  = gpu.tex_cache.held_valid_b;
wire tc_skid_push     = tc_accept_b && tc_pipe_b_live
                        && !(tc_resp_pop_b && !tc_held_valid_b);

// Starvation-pressure indicator: port B presents a request (req_valid_b) but
// the cache refuses it (req_ready_b low) -- i.e. port B is being held off,
// the precondition for a pop/accept decoupling.  And accept_b count proves
// port B requests really flow through the contended fill machine.
wire tc_b_stalled = gpu.tex_cache.req_valid_b && !gpu.tex_cache.req_ready_b;

reg [31:0] skid_push_cnt = 0;
reg [31:0] b_stall_cyc   = 0;
reg [31:0] b_accept_cnt  = 0;
always @(posedge clk) begin
    if (!reset_n) begin
        skid_push_cnt <= 0; b_stall_cyc <= 0; b_accept_cnt <= 0;
    end else begin
        if (tc_skid_push) skid_push_cnt <= skid_push_cnt + 32'd1;
        if (tc_b_stalled) b_stall_cyc   <= b_stall_cyc   + 32'd1;
        if (tc_accept_b)  b_accept_cnt  <= b_accept_cnt  + 32'd1;
    end
end

assign cram1_skid_push_count = skid_push_cnt;
assign cram1_b_stall_cycles  = b_stall_cyc;
assign cram1_b_accept_count  = b_accept_cnt;

// ============================================================
// SRAM Model (word-level, GPU private scratch)
// ============================================================
reg [31:0] sram_mem [0:65535];
reg        sram_busy_r;
reg        sram_rvalid_r;
reg [1:0]  sram_delay;
reg        sram_op_read;
reg        sram_half_r;
reg        sram_hi_r;
reg [15:0] sram_addr_r;
reg [31:0] sram_wdata_r;
reg [3:0]  sram_wstrb_r;

assign gpu_sram_busy = sram_busy_r;
assign gpu_sram_rdata_valid = sram_rvalid_r;
assign gpu_sram_rdata = sram_half_r
                       ? (sram_hi_r
                          ? {sram_mem[sram_addr_r][31:16], 16'd0}
                          : {16'd0, sram_mem[sram_addr_r][15:0]})
                       : sram_mem[sram_addr_r];

always @(posedge clk) begin
    if (!reset_n) begin
        sram_busy_r   <= 1'b0;
        sram_rvalid_r <= 1'b0;
        sram_delay    <= 2'd0;
        sram_op_read  <= 1'b0;
        sram_half_r    <= 1'b0;
        sram_hi_r      <= 1'b0;
        sram_addr_r   <= 16'd0;
        sram_wdata_r  <= 32'd0;
        sram_wstrb_r  <= 4'd0;
    end else begin
        sram_rvalid_r <= 1'b0;
        if (!sram_busy_r && (gpu_sram_rd || gpu_sram_wr)) begin
            sram_busy_r  <= 1'b1;
            sram_delay   <= 2'd2;
            sram_op_read <= gpu_sram_rd;
            sram_half_r   <= gpu_sram_rd && gpu_sram_rd_half;
            sram_hi_r     <= gpu_sram_rd_hi;
            sram_addr_r  <= gpu_sram_addr[15:0];
            sram_wdata_r <= gpu_sram_wdata;
            sram_wstrb_r <= gpu_sram_wstrb;
        end else if (sram_busy_r) begin
            if (sram_delay != 2'd0) begin
                sram_delay <= sram_delay - 2'd1;
            end else begin
                if (sram_op_read) begin
                    sram_rvalid_r <= 1'b1;
                end else begin
                    if (sram_wstrb_r[0]) sram_mem[sram_addr_r][7:0]   <= sram_wdata_r[7:0];
                    if (sram_wstrb_r[1]) sram_mem[sram_addr_r][15:8]  <= sram_wdata_r[15:8];
                    if (sram_wstrb_r[2]) sram_mem[sram_addr_r][23:16] <= sram_wdata_r[23:16];
                    if (sram_wstrb_r[3]) sram_mem[sram_addr_r][31:24] <= sram_wdata_r[31:24];
                end
                sram_busy_r <= 1'b0;
            end
        end
    end
end

// ============================================================
// Simplified SDRAM Model (flat 1M word = 4MB, fast)
// ============================================================
reg [31:0] sdram_mem [0:1048575];  // 1M words = 4MB

// Backdoor write — ALSO mirror into the CRAM1 chip model at the SAME 32-bit
// word address so the GPU fetches byte-identical data on either route.
// bd_addr is the 32-bit SDRAM word address; the adapter requests CRAM1 word
// addr = gpu_tex_mem_araddr[23:2] which == that SDRAM word address for the
// (low) regions used by the harness.  cbd_word_addr[20]=die, [18:0]=word —
// agrees with the adapter/controller because the harness keeps every CRAM1
// byte address < 0x200000 (word bits [21:19]==0).
always @(posedge clk) begin
    if (bd_we) begin
        sdram_mem[bd_addr[19:0]] <= bd_wdata;
        cbd_we         <= !bd_no_mirror;          // poison = SDRAM-only write
        cbd_word_addr  <= {2'b0, bd_addr[18:0]};  // die 0; word index
        cbd_wdata_word <= bd_wdata;
    end else begin
        cbd_we <= 1'b0;
    end
end

// Backdoor read
assign bd_rd_data = sdram_mem[bd_rd_addr[19:0]];

// ---- AXI4 Read responder (SDRAM path; unchanged from tb_gpu.v) ----
localparam [9:0] LAT_VAR_MIN = 10'd8;
localparam [9:0] LAT_VAR_MAX = 10'd60;

reg        rd_active;
reg [23:0] rd_addr;
reg [7:0]  rd_beats_left;
reg [9:0]  rd_delay;

reg [9:0]  cfg_rd_latency;
reg        cfg_rd_latency_var;
reg [15:0] lat_lfsr;

reg [9:0]  cfg_texlat;
reg [31:0] cfg_texlat_lo;
reg [31:0] cfg_texlat_hi;

reg [9:0]  dbg_rd_last_latency;
reg [31:0] dbg_rd_txn_count;
assign dbg_rd_last_latency_o = dbg_rd_last_latency;
assign dbg_rd_txn_count_o    = dbg_rd_txn_count;

wire [9:0] lat_span      = LAT_VAR_MAX - LAT_VAR_MIN + 10'd1;
wire [9:0] lat_var_pick  = LAT_VAR_MIN + (lat_lfsr[9:0] % lat_span);
wire [9:0] lat_base      = cfg_rd_latency_var ? lat_var_pick : cfg_rd_latency;
wire       ar_in_texregion = (gpu_rd_araddr >= cfg_texlat_lo)
                          && (gpu_rd_araddr <= cfg_texlat_hi);
wire [9:0] lat_pick      = (cfg_texlat != 10'd0 && ar_in_texregion)
                         ? (lat_base + cfg_texlat)
                         : lat_base;

assign gpu_rd_arready = !rd_active;
assign gpu_rd_rvalid  = rd_active && (rd_delay == 0);
assign gpu_rd_rdata   = sdram_mem[rd_addr[19:0]];
assign gpu_rd_rlast   = rd_active && (rd_delay == 0) && (rd_beats_left == 0);

integer plusarg_lat;
integer plusarg_var;
integer plusarg_texlat;
integer plusarg_texlo;
integer plusarg_texhi;
initial begin
    cfg_rd_latency     = 10'd2;
    cfg_rd_latency_var = 1'b0;
    lat_lfsr           = 16'hACE1;
    dbg_rd_last_latency = 10'd2;
    dbg_rd_txn_count    = 32'd0;
    cfg_texlat         = 10'd0;
    cfg_texlat_lo      = 32'h0004_0000;
    cfg_texlat_hi      = 32'h0007_FFFF;
    plusarg_lat = 0;
    plusarg_var = 0;
    plusarg_texlat = 0;
    plusarg_texlo = 0;
    plusarg_texhi = 0;
    if ($value$plusargs("gpu_rd_latency=%d", plusarg_lat)) begin
        if (plusarg_lat < 0)    plusarg_lat = 0;
        if (plusarg_lat > 1023) plusarg_lat = 1023;
        cfg_rd_latency = plusarg_lat[9:0];
    end
    if ($value$plusargs("gpu_rd_latency_var=%d", plusarg_var)) begin
        cfg_rd_latency_var = (plusarg_var != 0);
    end
    if ($value$plusargs("gpu_rd_texlat=%d", plusarg_texlat)) begin
        if (plusarg_texlat < 0)    plusarg_texlat = 0;
        if (plusarg_texlat > 1023) plusarg_texlat = 1023;
        cfg_texlat = plusarg_texlat[9:0];
    end
    if ($value$plusargs("gpu_rd_texlat_lo=%d", plusarg_texlo))
        cfg_texlat_lo = plusarg_texlo[31:0];
    if ($value$plusargs("gpu_rd_texlat_hi=%d", plusarg_texhi))
        cfg_texlat_hi = plusarg_texhi[31:0];
end

always @(posedge clk) begin
    if (!reset_n) begin
        rd_active <= 0;
        rd_delay <= 0;
    end else begin
        if (!rd_active && gpu_rd_arvalid) begin
            rd_active     <= 1;
            rd_addr       <= gpu_rd_araddr[25:2];
            rd_beats_left <= gpu_rd_arlen;
            rd_delay      <= lat_pick;
            dbg_rd_last_latency <= lat_pick;
            dbg_rd_txn_count    <= dbg_rd_txn_count + 32'd1;
            lat_lfsr <= {lat_lfsr[14:0],
                         lat_lfsr[15] ^~ lat_lfsr[13] ^~ lat_lfsr[12] ^~ lat_lfsr[10]};
        end else if (rd_active) begin
            if (rd_delay > 0) begin
                rd_delay <= rd_delay - 10'd1;
            end else begin
                if (rd_beats_left == 0) begin
                    rd_active <= 0;
                end else begin
                    rd_addr       <= rd_addr + 1;
                    rd_beats_left <= rd_beats_left - 1;
                    rd_delay      <= 0;
                end
            end
        end
    end
end

// ---- AXI4 Write responder ----
reg        wr_aw_active;
reg [23:0] wr_addr;
reg        wr_b_pending;

assign gpu_wr_awready = !wr_aw_active && !wr_b_pending;
assign gpu_wr_wready  = wr_aw_active;
assign gpu_wr_bvalid  = wr_b_pending;

always @(posedge clk) begin
    if (!reset_n) begin
        wr_aw_active <= 0;
        wr_b_pending <= 0;
        dbg_aw_count <= 0;
        dbg_aw_burst_count <= 0;
        dbg_aw_max_len <= 0;
    end else begin
        if (!wr_aw_active && !wr_b_pending && gpu_wr_awvalid) begin
            wr_aw_active <= 1;
            wr_addr      <= gpu_wr_awaddr[25:2];
            dbg_aw_count <= dbg_aw_count + 32'd1;
            if (gpu_wr_awlen != 8'd0)
                dbg_aw_burst_count <= dbg_aw_burst_count + 32'd1;
            if (gpu_wr_awlen > dbg_aw_max_len)
                dbg_aw_max_len <= gpu_wr_awlen;
        end

        if (wr_aw_active && gpu_wr_wvalid) begin
            if (gpu_wr_wstrb[0]) sdram_mem[wr_addr[19:0]][7:0]   <= gpu_wr_wdata[7:0];
            if (gpu_wr_wstrb[1]) sdram_mem[wr_addr[19:0]][15:8]  <= gpu_wr_wdata[15:8];
            if (gpu_wr_wstrb[2]) sdram_mem[wr_addr[19:0]][23:16] <= gpu_wr_wdata[23:16];
            if (gpu_wr_wstrb[3]) sdram_mem[wr_addr[19:0]][31:24] <= gpu_wr_wdata[31:24];

            if (gpu_wr_wlast) begin
                wr_aw_active <= 0;
                wr_b_pending <= 1;
            end else begin
                wr_addr <= wr_addr + 1;
            end
        end

        if (wr_b_pending)
            wr_b_pending <= 0;
    end
end

endmodule

`default_nettype wire
