//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

//
// Verilator Testbench: GPU Core
//
// Instantiates: gpu_core -> gpu_tex_cache (internal)
// Provides:
//   - Simplified SDRAM model (flat memory, AXI4 read+write)
//   - SRAM model (word-level, for Z-buffer)
//   - MMIO register interface (from C++ harness)
//   - Framebuffer readback port (from C++ harness)
//

`default_nettype none

module tb_gpu #(
    // Forwarded verbatim to gpu_core so per-variant configs can be built
    // straight from the command line with Verilator -G overrides, e.g.
    //   verilator ... -GINCLUDE_PARAM_TRI_RECS=0 -GGPU_Z_READ_WINDOW=1
    // Defaults match gpu_core's defaults (everything on), so the standard
    // gpu / gpu-persp / gpu-acceptance builds are unchanged.
    parameter INCLUDE_PARAM_TRI      = 1,
    parameter INCLUDE_VERT_TRI       = 1,
    parameter INCLUDE_PARAM_TRI_RECS = 1,
    parameter GPU_Z_READ_WINDOW      = 4,
    parameter GPU_CB_READ_WINDOW     = 4,
    parameter GPU_EW_PARALLEL_DIVS   = 1,
    parameter INCLUDE_COMPACT_SPAN   = 1,
    parameter INCLUDE_COLUMN_LIST    = 1,
    parameter INCLUDE_DIRECT_COLOR   = 0,
    parameter INCLUDE_XFORM_RGB      = 0,
    parameter INCLUDE_CLIP_TRI       = 1,
    parameter INCLUDE_VTX_CACHE      = 0,
    parameter INCLUDE_GPU_LIGHT      = 0,
    parameter INCLUDE_PALETTE        = 1,
    parameter INCLUDE_COMBINE        = 1,
    parameter INCLUDE_PARAM_SPAN_Q29 = 1,
    // Fast texture memory fold.  Default 1 = gpu_core's default (matches every
    // pre-existing acceptance config).  The tb has no fast-tex model: the
    // gpu_tex_mem_* fill-master inputs are tied idle below, exactly like the
    // MiSTer emu.sv instantiation, so -GINCLUDE_TEX_MEM=0 builds the exact
    // shipped MiSTer fold (gpu-acceptance-mister-exact).
    parameter INCLUDE_TEX_MEM        = 1
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

    // Write-channel occupancy counters (test-only, ADDITIVE). Let the C++
    // harness separate write-channel-busy cycles from write-channel-idle
    // cycles to attribute triangle SETUP (write-idle) vs pixel-write time.
    //   dbg_w_beat_cycles  : cycles a W data beat actually transferred
    //   dbg_w_busy_cycles  : cycles ANY write txn occupied the channel
    //                        (AW accepted / W beats / B / commit-delay pending)
    output reg  [31:0] dbg_w_beat_cycles,
    output reg  [31:0] dbg_w_busy_cycles,

    // Read-channel occupancy (test-only). A read txn (z-read / tex fill / ring)
    // holds the SDRAM read port. rd_busy = cycles rd_active is set. Combined
    // with w_busy this gives TOTAL SDRAM (single-port) occupancy: the only time
    // a *second* triangle could do useful work is when SDRAM is idle.
    output reg  [31:0] dbg_rd_busy_cycles,

    // Cycles BOTH a read txn and a write txn occupied their ports at once.
    // On the real single-port io_sdram these would serialize, so the true
    // single-port busy = rd_busy + wr_busy - rw_overlap (union, not sum).
    output reg  [31:0] dbg_rw_overlap_cycles,

    // Read-latency diagnostics (ADDITIVE, test-only). Surface the
    // configurable SDRAM read responder's last-picked initial latency and
    // the transaction count so the C++ harness can confirm the high /
    // variable latency mode is actually engaged.
    output wire [9:0]  dbg_rd_last_latency_o,
    output wire        dbg_wr_awvalid,
    output wire        dbg_wr_wvalid,
    output wire        dbg_wr_awready,
    output wire        dbg_wr_wready,
    output wire        dbg_wr_wlast,
    output wire        dbg_wr_bvalid,
    output wire [7:0]  dbg_wr_awlen,
    output wire [31:0] dbg_rd_txn_count_o,

    // CMD_FLIP side-port (observed by C++ harness for the drain test)
    output wire        gpu_swap_req,
    output wire [1:0]  gpu_swap_idx,

    // External diag inputs — driven by C++ harness for the drain test
    input  wire        slave_swap_pending,

    // SDRAM backdoor write (preload textures, ring buffer, etc.)
    input  wire        bd_we,
    input  wire [23:0] bd_addr,    // word address
    input  wire [31:0] bd_wdata,

    // SDRAM backdoor read (verify framebuffer)
    input  wire [23:0] bd_rd_addr,
    output wire [31:0] bd_rd_data
);

// ============================================================
// GPU AXI4 Read Master signals
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
assign dbg_wr_awvalid = gpu_wr_awvalid;
assign dbg_wr_awready = gpu_wr_awready;
assign dbg_wr_wready  = gpu_wr_wready;
assign dbg_wr_wlast   = gpu_wr_wlast;
assign dbg_wr_bvalid  = gpu_wr_bvalid;
assign dbg_wr_awlen   = gpu_wr_awlen;
wire        gpu_wr_awready;
wire [31:0] gpu_wr_awaddr;
wire [7:0]  gpu_wr_awlen;
wire        gpu_wr_wvalid;
wire        gpu_wr_wready;
assign dbg_wr_wvalid = gpu_wr_wvalid;
wire [31:0] gpu_wr_wdata;
wire [3:0]  gpu_wr_wstrb;
wire        gpu_wr_wlast;
wire        gpu_wr_bvalid;

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
    .GPU_CB_READ_WINDOW(GPU_CB_READ_WINDOW),
    .GPU_EW_PARALLEL_DIVS(GPU_EW_PARALLEL_DIVS),
    .INCLUDE_COMPACT_SPAN(INCLUDE_COMPACT_SPAN),
    .INCLUDE_COLUMN_LIST(INCLUDE_COLUMN_LIST),
    .INCLUDE_DIRECT_COLOR(INCLUDE_DIRECT_COLOR),
    .INCLUDE_XFORM_RGB(INCLUDE_XFORM_RGB),
    .INCLUDE_CLIP_TRI(INCLUDE_CLIP_TRI),
    .INCLUDE_VTX_CACHE(INCLUDE_VTX_CACHE),
    .INCLUDE_GPU_LIGHT(INCLUDE_GPU_LIGHT),
    .INCLUDE_PALETTE(INCLUDE_PALETTE),
    .INCLUDE_COMBINE(INCLUDE_COMBINE),
    .INCLUDE_PARAM_SPAN_Q29(INCLUDE_PARAM_SPAN_Q29),
    .INCLUDE_TEX_MEM(INCLUDE_TEX_MEM)
) gpu (
    .clk(clk),
    .reset_n(reset_n),
    .gpu_enable(1'b1),
    // Fast texture memory: no tb model — fill-master inputs tied idle,
    // exactly like MiSTer's emu.sv (INCLUDE_TEX_MEM=0) instantiation.
    // Previously left unconnected (Verilator floats inputs to 0), so the
    // explicit ties are behaviour-identical for every existing config.
    .gpu_tex_mem_arready(1'b0),
    .gpu_tex_mem_rvalid(1'b0),
    .gpu_tex_mem_rdata(32'b0),
    .gpu_tex_mem_rlast(1'b0),
    .gpu_tex_mem_up_busy(1'b0),
    // AXI4 read
    .m_rd_arvalid(gpu_rd_arvalid),
    .m_rd_arready(gpu_rd_arready),
    .m_rd_araddr(gpu_rd_araddr),
    .m_rd_arlen(gpu_rd_arlen),
    .m_rd_rvalid(gpu_rd_rvalid),
    .m_rd_rdata(gpu_rd_rdata),
    .m_rd_rlast(gpu_rd_rlast),
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
    // External backpressure input (only the drain test drives it from
    // the C++ harness).
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
// Combined read+write AXI4 responder with 2-cycle read latency.

reg [31:0] sdram_mem [0:1048575];  // 1M words = 4MB

// Backdoor write
always @(posedge clk) begin
    if (bd_we)
        sdram_mem[bd_addr[19:0]] <= bd_wdata;
end

// Backdoor read
assign bd_rd_data = sdram_mem[bd_rd_addr[19:0]];

// ---- AXI4 Read responder ----
//
// ADDITIVE (test-only) variable / high-latency mode.  By default this is
// byte-identical to the historical fixed 2-cycle, single-outstanding,
// non-backpressuring stub (gpu_rd_arready stays low while a read is
// active — matching the real single-outstanding axi_sdram_slave).  Two
// runtime plusargs (read at reset, via $value$plusargs) let the C++
// harness crank up the *initial* latency without touching the AXI
// contract so the existing 279/0 baseline is preserved when neither
// plusarg is present:
//
//   +gpu_rd_latency=N        : fixed initial latency of N cycles per
//                              transaction (default 2).  Back-to-back
//                              beats inside a burst still deliver at
//                              1/cycle (matches the real slave's
//                              line-fill behaviour).
//   +gpu_rd_latency_var=1    : deterministic pseudo-random initial
//                              latency in [LAT_VAR_MIN .. LAT_VAR_MAX],
//                              derived from an internal LFSR (NO real
//                              RNG — Verilator scripts forbid it).  The
//                              per-transaction value is published on
//                              dbg_rd_last_latency for the harness.
//
// The single-outstanding contract is UNCHANGED in every mode: arready
// is low while rd_active, exactly one AR is in flight, beats are in
// order, rlast on the final beat.  The ONLY thing the modes change is
// how many bubble cycles precede the first beat.
localparam [9:0] LAT_VAR_MIN = 10'd8;
localparam [9:0] LAT_VAR_MAX = 10'd60;

reg        rd_active;
reg [23:0] rd_addr;
reg [7:0]  rd_beats_left;
reg [9:0]  rd_delay;       // 10 bits so the texlat boost (up to ~1023) fits

// Latency configuration latched once at reset from plusargs.
reg [9:0]  cfg_rd_latency;     // fixed initial latency
reg        cfg_rd_latency_var; // variable (LFSR) mode select
reg [15:0] lat_lfsr;           // deterministic pseudo-random source

// SUSPECT-1 escalation (step 5): an EXTRA, port-A-region-selective latency
// boost.  Reads whose AR byte address falls in [cfg_texlat_lo,cfg_texlat_hi]
// (the texture region) get cfg_texlat additional bubble cycles.  This keeps
// the shared fill FSM parked in S_FILL_* on PORT-A (texture) fills far
// longer than port-B (colormap) fills, the strongest possible starvation
// pressure on port B's req_ready_b (which is LOW in every S_FILL_* state for
// the other port).  Off by default (cfg_texlat=0).
reg [9:0]  cfg_texlat;
reg [31:0] cfg_texlat_lo;      // inclusive byte address
reg [31:0] cfg_texlat_hi;      // inclusive byte address

// Diagnostics for the C++ harness.
reg [9:0]  dbg_rd_last_latency;
reg [31:0] dbg_rd_txn_count;
assign dbg_rd_last_latency_o = dbg_rd_last_latency;
assign dbg_rd_txn_count_o    = dbg_rd_txn_count;

// Pick the initial latency for the transaction now being accepted.
// Pure function of cfg + the current LFSR state (so it is deterministic
// and reproducible run-to-run).
wire [9:0] lat_span      = LAT_VAR_MAX - LAT_VAR_MIN + 10'd1;   // 53
wire [9:0] lat_var_pick  = LAT_VAR_MIN + (lat_lfsr[9:0] % lat_span);
wire [9:0] lat_base      = cfg_rd_latency_var ? lat_var_pick : cfg_rd_latency;
// Region-selective boost for the texture (port-A) region.
wire       ar_in_texregion = (gpu_rd_araddr >= cfg_texlat_lo)
                          && (gpu_rd_araddr <= cfg_texlat_hi);
wire [9:0] lat_pick      = (cfg_texlat != 10'd0 && ar_in_texregion)
                         ? (lat_base + cfg_texlat)
                         : lat_base;

assign gpu_rd_arready = !rd_active;
assign gpu_rd_rvalid  = rd_active && (rd_delay == 0);
assign gpu_rd_rdata   = sdram_mem[rd_addr[19:0]];
assign gpu_rd_rlast   = rd_active && (rd_delay == 0) && (rd_beats_left == 0);

// Plusarg latch + LFSR seed (one-time, at reset).
integer plusarg_lat;
integer plusarg_var;
integer plusarg_texlat;
integer plusarg_texlo;
integer plusarg_texhi;
initial begin
    cfg_rd_latency     = 10'd2;   // historical default
    cfg_rd_latency_var = 1'b0;
    lat_lfsr           = 16'hACE1;
    dbg_rd_last_latency = 10'd2;
    dbg_rd_txn_count    = 32'd0;
    cfg_texlat         = 10'd0;
    cfg_texlat_lo      = 32'h0004_0000;  // default = TEX_BASE_BYTE
    cfg_texlat_hi      = 32'h0007_FFFF;  // default = up to FB_BASE_BYTE-1
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
        dbg_rd_busy_cycles <= 0;
        // LFSR / diag keep their initial-block values across reset so a
        // single seed produces the same deterministic sequence.
    end else begin
        // Read-port occupancy: AR being accepted this cycle, or a read txn
        // already in flight (waiting on initial latency or streaming beats).
        if ((!rd_active && gpu_rd_arvalid) || rd_active)
            dbg_rd_busy_cycles <= dbg_rd_busy_cycles + 32'd1;
        if (!rd_active && gpu_rd_arvalid) begin
            rd_active     <= 1;
            rd_addr       <= gpu_rd_araddr[25:2];
            rd_beats_left <= gpu_rd_arlen;
            rd_delay      <= lat_pick;   // configurable initial latency
            dbg_rd_last_latency <= lat_pick;
            dbg_rd_txn_count    <= dbg_rd_txn_count + 32'd1;
            // Advance the LFSR once per accepted transaction (16-bit
            // maximal-length xnor taps 16,14,13,11) so the next txn's
            // pick differs deterministically.
            lat_lfsr <= {lat_lfsr[14:0],
                         lat_lfsr[15] ^~ lat_lfsr[13] ^~ lat_lfsr[12] ^~ lat_lfsr[10]};
        end else if (rd_active) begin
            if (rd_delay > 0) begin
                rd_delay <= rd_delay - 10'd1;
            end else begin
                // Beat delivered
                if (rd_beats_left == 0) begin
                    rd_active <= 0;
                end else begin
                    rd_addr       <= rd_addr + 1;
                    rd_beats_left <= rd_beats_left - 1;
                    rd_delay      <= 0;  // back-to-back beats
                end
            end
        end
    end
end

// ---- AXI4 Write responder ----
//
// ADDITIVE (test-only) write-commit latency knob `+gpu_wr_latency=N`
// (default 0).  At N==0 the model is BYTE-IDENTICAL to the historical
// stub: each accepted W beat commits to sdram_mem the same cycle, and B
// raises the cycle after WLAST.  At N!=0 the per-beat byte-strobed writes
// are STAGED in registers and applied to sdram_mem (and B raised) only
// after N cycles of post-WLAST hold — exposing in-flight z-RAW that the
// instant-commit stub masks.  The single-outstanding AXI contract is
// UNCHANGED in every mode: awready is low while a burst is active OR a
// delayed commit is pending, exactly one AW in flight, wready high during
// the burst, one B per WLAST.  Up to 8-beat bursts (fbwq cap) are staged;
// size to 16 for headroom.
reg        wr_aw_active;
reg [23:0] wr_addr;
reg        wr_b_pending;

// Delayed-commit staging (only used when cfg_wr_latency != 0).
reg [9:0]  cfg_wr_latency;          // post-WLAST commit hold, in cycles
reg        wr_commit_pending;       // a staged burst is waiting to commit
reg [9:0]  wr_commit_delay;         // countdown to commit
reg [4:0]  wr_stage_n;              // # staged beats (0..16)
reg [19:0] wr_stage_addr [0:15];    // word address per staged beat
reg [31:0] wr_stage_data [0:15];    // wdata per staged beat
reg [3:0]  wr_stage_strb [0:15];    // wstrb per staged beat
integer    wr_apply_i;
integer    plusarg_wrlat;

initial begin
    cfg_wr_latency = 10'd0;          // historical default: same-cycle commit
    plusarg_wrlat  = 0;
    if ($value$plusargs("gpu_wr_latency=%d", plusarg_wrlat)) begin
        if (plusarg_wrlat < 0)    plusarg_wrlat = 0;
        if (plusarg_wrlat > 1023) plusarg_wrlat = 1023;
        cfg_wr_latency = plusarg_wrlat[9:0];
    end
end

// awready stays low while a burst is active OR a delayed commit is pending,
// so at most one AW is outstanding (matches the real single-outstanding
// slave).  wr_commit_pending is constant 0 in latency-0 mode, so this term
// reduces to the historical `!wr_aw_active && !wr_b_pending`.
assign gpu_wr_awready = !wr_aw_active && !wr_b_pending && !wr_commit_pending;
assign gpu_wr_wready  = wr_aw_active;
assign gpu_wr_bvalid  = wr_b_pending;

always @(posedge clk) begin
    if (!reset_n) begin
        wr_aw_active <= 0;
        wr_b_pending <= 0;
        dbg_aw_count <= 0;
        dbg_aw_burst_count <= 0;
        dbg_aw_max_len <= 0;
        wr_commit_pending <= 0;
        wr_commit_delay   <= 0;
        wr_stage_n        <= 0;
        dbg_w_beat_cycles <= 0;
        dbg_w_busy_cycles <= 0;
        dbg_rw_overlap_cycles <= 0;
    end else begin : wr_occ_blk
        reg wbusy_now;
        wbusy_now = (gpu_wr_awvalid && gpu_wr_awready)
                 || wr_aw_active || wr_b_pending || wr_commit_pending;
        // Write-channel occupancy accounting (sampled each cycle).
        // beat: a W data word is actually moving this cycle.
        if (wr_aw_active && gpu_wr_wvalid && gpu_wr_wready)
            dbg_w_beat_cycles <= dbg_w_beat_cycles + 32'd1;
        // busy: any phase of a write transaction occupies the channel — an AW
        // is being accepted, a burst is mid-flight, a response is pending, or a
        // delayed commit is draining. Everything else is write-channel IDLE.
        if (wbusy_now)
            dbg_w_busy_cycles <= dbg_w_busy_cycles + 32'd1;
        // Overlap: a read txn is in flight at the same time as a write txn.
        // (gpu_rd_arvalid acceptance OR rd_active mirrors the rd_busy term.)
        if (wbusy_now && ((!rd_active && gpu_rd_arvalid) || rd_active))
            dbg_rw_overlap_cycles <= dbg_rw_overlap_cycles + 32'd1;

        // Accept AW
        if (!wr_aw_active && !wr_b_pending && !wr_commit_pending && gpu_wr_awvalid) begin
            wr_aw_active <= 1;
            wr_addr      <= gpu_wr_awaddr[25:2];
            wr_stage_n   <= 0;
            dbg_aw_count <= dbg_aw_count + 32'd1;
            if (gpu_wr_awlen != 8'd0)
                dbg_aw_burst_count <= dbg_aw_burst_count + 32'd1;
            if (gpu_wr_awlen > dbg_aw_max_len)
                dbg_aw_max_len <= gpu_wr_awlen;
        end

        // Accept W beats
        if (wr_aw_active && gpu_wr_wvalid) begin
            if (cfg_wr_latency == 10'd0) begin
                // Same-cycle commit (historical, byte-identical path).
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
            end else begin
                // Delayed commit: stage this beat; commit the whole burst
                // after wr_commit_delay cycles past WLAST.
                wr_stage_addr[wr_stage_n[3:0]] <= wr_addr[19:0];
                wr_stage_data[wr_stage_n[3:0]] <= gpu_wr_wdata;
                wr_stage_strb[wr_stage_n[3:0]] <= gpu_wr_wstrb;
                wr_stage_n <= wr_stage_n + 5'd1;

                if (gpu_wr_wlast) begin
                    wr_aw_active      <= 0;
                    wr_commit_pending <= 1;
                    wr_commit_delay   <= cfg_wr_latency;
                end else begin
                    wr_addr <= wr_addr + 1;
                end
            end
        end

        // Delayed-commit countdown + apply (latency mode only).
        if (wr_commit_pending) begin
            if (wr_commit_delay != 10'd0) begin
                wr_commit_delay <= wr_commit_delay - 10'd1;
            end else begin
                for (wr_apply_i = 0; wr_apply_i < 16; wr_apply_i = wr_apply_i + 1) begin
                    if (wr_apply_i < wr_stage_n) begin
                        if (wr_stage_strb[wr_apply_i][0])
                            sdram_mem[wr_stage_addr[wr_apply_i]][7:0]   <= wr_stage_data[wr_apply_i][7:0];
                        if (wr_stage_strb[wr_apply_i][1])
                            sdram_mem[wr_stage_addr[wr_apply_i]][15:8]  <= wr_stage_data[wr_apply_i][15:8];
                        if (wr_stage_strb[wr_apply_i][2])
                            sdram_mem[wr_stage_addr[wr_apply_i]][23:16] <= wr_stage_data[wr_apply_i][23:16];
                        if (wr_stage_strb[wr_apply_i][3])
                            sdram_mem[wr_stage_addr[wr_apply_i]][31:24] <= wr_stage_data[wr_apply_i][31:24];
                    end
                end
                wr_commit_pending <= 0;
                wr_b_pending      <= 1;
            end
        end

        // B response consumed (single cycle)
        if (wr_b_pending)
            wr_b_pending <= 0;
    end
end

endmodule
