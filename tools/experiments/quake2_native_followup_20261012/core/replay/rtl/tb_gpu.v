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
    parameter CPU_FAIR_THRESHOLD = 8,
    parameter CRAM_REFRESH_EXTRA = 0,
    parameter INCLUDE_PERSPECTIVE = 1,
    parameter INCLUDE_DEPTH = 1,
    parameter INCLUDE_PARAM_SPAN_LIST = 1,
    parameter INCLUDE_CPU_RING = 0,
    parameter INCLUDE_COMMAND_DMA = 1,
    parameter GPU_WRITE_COMBINE_FAST_FLUSH = 0,
    parameter GPU_WRITE_COMBINE_Z = 0,
    parameter GPU_WRITE_GATHER = 0,
    parameter GPU_MASKED_WRITE_BURSTS = 0,
    parameter GPU_WRITE_COMBINE_BURST_HASH = 0,
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
    parameter GPU_PSS_RECIP_CACHE    = 1,
    parameter GPU_STREAM_PIPE        = 1,
    parameter GPU_WRITE_COMBINE      = 1,
    parameter GPU_WRITE_COMBINE_PIPE = 0,
    parameter GPU_EW_PARALLEL_DIVS   = 1,
    parameter GPU_TEX_CACHE_SET_BITS = 10,
    parameter INCLUDE_COMPACT_SPAN   = 1,
    parameter INCLUDE_COLUMN_LIST    = 1,
    parameter INCLUDE_DIRECT_COLOR   = 0,
    parameter INCLUDE_XFORM_RGB      = 0,
    parameter INCLUDE_CLIP_TRI       = 1,
    parameter INCLUDE_GPU_XFORM_MAC  = 1,   // matrix MAC (0x50 matrix/0x51/0x52/0x53/0x57)
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
    input wire critical_enable,
    input  wire        clk,
    input  wire        reset_n,

    // MMIO register interface (driven from C++)
    input  wire        reg_wr,
    input  wire [3:0]  reg_addr,
    input  wire [31:0] reg_wdata,
    output wire [31:0] reg_rdata,

    // Passive observations only; no new behavior in synthesized production RTL.
    output wire obs_cpu_valid, obs_cpu_ready, obs_cpu_rvalid, obs_cpu_rlast,
    output wire obs_audio_valid, obs_audio_ready, obs_audio_rvalid, obs_audio_rlast,
    output wire obs_scan_req, obs_scan_done,
    output wire [1:0] obs_arb_state, obs_grant,
    output wire obs_gpu_arvalid, obs_gpu_arready, obs_gpu_awvalid, obs_gpu_awready,
    output wire obs_gpu_wvalid, obs_gpu_wready,
    output wire obs_tex_arvalid,obs_tex_arready,obs_tex_rvalid,obs_tex_rlast,
    output wire [31:0] obs_tex_araddr,obs_tex_rdata,
    output wire [7:0] obs_tex_arlen,
    output wire obs_c1_bcr_done,obs_c1_route,
    output wire [15:0] obs_c1_errors,
    output wire obs_mesh_arvalid,obs_mesh_arready,obs_mesh_rvalid,obs_mesh_rlast,
    output wire [31:0] obs_mesh_araddr,obs_mesh_rdata,
    output wire [7:0] obs_mesh_arlen,
    output wire obs_worker_empty,obs_worker_output_stall,obs_worker_active,
    output wire [6:0] obs_worker_state,
    // Status outputs
    output wire        busy,
    output wire [31:0] fence_reached,
    output wire [3:0] obs_fbss,
    output wire [5:0]  dbg_state,
    output wire [5:0]  dbg_setup_step,
    output wire [31:0] dbg_aux,
    output wire [31:0] dbg_frag,
    output reg  [31:0] dbg_aw_count,
    output reg  [31:0] dbg_aw_burst_count,
    output reg  [7:0]  dbg_aw_max_len,

    // Reciprocal reuse diagnostics; testbench only, no hardware ports.
    output reg [31:0] dbg_pss_requests,
    output reg [31:0] dbg_pss_cache_hits,
    output reg [63:0] dbg_pss_digest,

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
// Independent integer model of the original LUT + Newton-Raphson arithmetic.
// Check every consumed reciprocal, including speculative endpoints that a
// shorter schedule may finish before span retirement cancels them.
function [31:0] reference_recip;
    input [31:0] denominator;
    input q29;
    input bias;
    reg [31:0] normalized;
    reg [15:0] lut;
    reg signed [31:0] initial_value, correction;
    reg signed [63:0] product;
    reg [63:0] scaled;
    integer leading;
    begin
        leading = 0;
        normalized = denominator;
        while (!normalized[31] && leading < 31) begin
            normalized = normalized << 1;
            leading = leading + 1;
        end
        lut = 16777216 / (1024 + {22'd0, normalized[30:21]});
        scaled = {48'd0, lut} << (leading + (q29 ? 4 : 0));
        initial_value = 32'(scaled >> 13);
        product = $signed(denominator) * initial_value;
        correction = 32'h20000 - 32'(product >>> (q29 ? 20 : 16));
        product = initial_value * correction;
        reference_recip = product[47:16] + ((!q29 && bias) ? 32'd1 : 32'd0);
    end
endfunction
always @(posedge clk) begin
    if (!reset_n) begin
        dbg_pss_requests <= 0;
        dbg_pss_cache_hits <= 0;
        dbg_pss_digest <= 64'hcbf29ce484222325;
    end else if (!gpu.soft_reset && gpu.state == gpu.S_FRAG_PIPE
              && gpu.persp_active) begin
        if (gpu.persp_pss == gpu.PSS_TOP8) begin
            dbg_pss_requests <= dbg_pss_requests + 1;
            if (gpu.sp_persp_q29_mode && gpu.pss_recip_cache_hit)
                dbg_pss_cache_hits <= dbg_pss_cache_hits + 1;
        end
        if (gpu.persp_pss == gpu.PSS_MUL) begin
            if (gpu.recip_q16_r !== reference_recip(gpu.persp_zinv_abs_r,
                      gpu.sp_persp_q29_mode,
                      gpu.sp_zinv_step_zero && gpu.sp_zinv != 32'h10000))
                $fatal(1, "Reciprocal arithmetic mismatch");
            if (gpu.persp_pass == gpu.PSS_PASS_ANCHOR)
                dbg_pss_digest <= (dbg_pss_digest
                                ^ {gpu.persp_zinv_abs_r, gpu.recip_q16_r})
                                * 64'h100000001b3;
            if ($test$plusargs("gpu_pss_trace"))
                $display("RECIP %d %08x %08x %d %d %d", dbg_pss_requests,
                         gpu.persp_zinv_abs_r, gpu.recip_q16_r,
                         gpu.sp_persp_q29_mode, gpu.persp_pass, gpu.sp_count);
        end
    end
end
// Credit conservation is checked at every clock, including backend stalls
// and reset recovery. Reservations include the DSP/pending-lookup slots.
generate if (GPU_STREAM_PIPE) begin : stream_checks
    always @(posedge clk) if (reset_n && !gpu.soft_reset) begin
        if (gpu.tex_stream.req_reserved > 4 || gpu.tex_stream.res_reserved > 4)
            $fatal(1, "Texture queue reservation overflow");
        if ({1'b0, gpu.tex_stream.req_reserved}
            != ({1'b0, gpu.tex_stream.req_count} + {3'd0, gpu.p0_valid}))
            $fatal(1, "Texture request credit mismatch");
        if ({1'b0, gpu.tex_stream.res_reserved}
            != ({1'b0, gpu.tex_stream.res_count} + {3'd0, gpu.tex_stream.lookup_pending}))
            $fatal(1, "Texture response credit mismatch");
        if ((gpu.tex_stream.req_push && gpu.tex_stream.req_count == 4)
            || (gpu.tex_stream.req_pop && gpu.tex_stream.req_count == 0)
            || (gpu.tex_stream.res_push && gpu.tex_stream.res_count == 4)
            || (gpu.stream_result_pop && gpu.tex_stream.res_count == 0))
            $fatal(1, "Texture queue overflow/underflow");
    end
end endgenerate

// These timing changes must preserve the values at the original consume
// edge, including stalls and concurrent depth-window fills/invalidation.
`ifdef INCLUDE_Z_PREFETCH
always @(posedge clk) if (reset_n && !gpu.soft_reset
    && gpu.state == gpu.S_FRAG_PIPE && gpu.zw_prefetch_armed && !gpu.p0a_valid) begin
    if (gpu.stream_pending || gpu.p0_valid || gpu.p1_valid || gpu.p2_valid
        || gpu.p2b_valid || gpu.p3_valid)
        $fatal(1, "Depth prefetch opportunity contains older fragments");
    if (gpu.sp_z_test_enable && gpu.p3_z_addr !== gpu.sp_z_addr)
        $fatal(1, "Depth prefetch address differs from the first source pixel");
end
`endif
`ifdef INCLUDE_GPU_CLAMP_FLAGS_PIPE
always @(posedge clk) if (reset_n && !gpu.soft_reset && gpu.state == gpu.S_SPANPROD_EMIT) begin
    if (gpu.spanprod_clamp0_min_nz !== (gpu.spanprod_clamp0_min != 0)
        || gpu.spanprod_clamp0_max_nz !== (gpu.spanprod_clamp0_max != 0)
        || gpu.spanprod_clamp1_min_nz !== (gpu.spanprod_clamp1_min != 0)
        || gpu.spanprod_clamp1_max_nz !== (gpu.spanprod_clamp1_max != 0))
        $fatal(1, "Clamp flag not ready at span emission");
end
`endif
`ifdef INCLUDE_Z_COMPARE_PARALLEL
`ifdef INCLUDE_Z_WINDOW_PREDECODE
always @(posedge clk) if (reset_n && !gpu.soft_reset && gpu.p2b_valid
                        && gpu.state == gpu.S_FRAG_PIPE && !gpu.fp_pipe_stall) begin
    if (GPU_Z_READ_WINDOW > 1 && gpu.p2b_zf_window_half !==
        (gpu.p2b_z_addr[1] ? gpu.p2b_zf_window_word[31:16] : gpu.p2b_zf_window_word[15:0]))
        $fatal(1, "Predecoded depth address does not match the live window");
end
`endif
`endif

wire c1_arvalid,c1_arready,c1_rvalid,c1_rlast,c1_up_wr,c1_up_busy;
wire [31:0] c1_araddr,c1_rdata,c1_up_data;
wire [7:0] c1_arlen;
wire [21:0] c1_up_addr;
wire tex1_arvalid,tex1_arready,tex1_rvalid,tex1_rlast;
wire [31:0] tex1_araddr,tex1_rdata;
wire [7:0] tex1_arlen;
geometry_cram1_read_arbiter mesh_texture_arbiter(
    .clk(clk),.reset_n(reset_n),.s0_reset(gpu.soft_reset),
    .s0_arvalid(tex1_arvalid && obs_c1_bcr_done),.s0_arready(tex1_arready),
    .s0_araddr(tex1_araddr),.s0_arlen(tex1_arlen),
    .s0_rvalid(tex1_rvalid),.s0_rdata(tex1_rdata),.s0_rlast(tex1_rlast),
    .s1_arvalid(obs_mesh_arvalid && obs_c1_bcr_done),.s1_arready(obs_mesh_arready),
    .s1_araddr(obs_mesh_araddr),.s1_arlen(obs_mesh_arlen),
    .s1_rvalid(obs_mesh_rvalid),.s1_rdata(obs_mesh_rdata),.s1_rlast(obs_mesh_rlast),
    .m_arvalid(c1_arvalid),.m_arready(c1_arready),.m_araddr(c1_araddr),.m_arlen(c1_arlen),
    .m_rvalid(c1_rvalid),.m_rdata(c1_rdata),.m_rlast(c1_rlast)
);
texture_memory #(.REFRESH_EXTRA_CYCLES(CRAM_REFRESH_EXTRA)) texture_memory(
    .clk(clk),.reset_n(reset_n),.arvalid(c1_arvalid),.arready(c1_arready),
    .araddr(c1_araddr),.arlen(c1_arlen),.rvalid(c1_rvalid),.rdata(c1_rdata),.rlast(c1_rlast),
    .up_wr(c1_up_wr),.up_addr(c1_up_addr),.up_data(c1_up_data),.up_busy(c1_up_busy),
    .bcr_done(obs_c1_bcr_done),.errors(obs_c1_errors)
);
assign obs_fbss=gpu.fbss;
assign obs_worker_state=gpu.g_geometry_worker.worker.state;
assign obs_worker_empty=gpu.g_geometry_worker.worker.input_empty && obs_worker_state==0;
assign obs_worker_output_stall=(obs_worker_state==1 && gpu.g_geometry_worker.worker.free_words<gpu.g_geometry_worker.worker.needed)
    || (obs_worker_state==2 && gpu.g_geometry_worker.worker.mode==0 && gpu.g_geometry_worker.worker.free_words==0)
    || (obs_worker_state==57 && gpu.g_geometry_worker.worker.free_words<7)
    || (obs_worker_state==55 && gpu.g_geometry_worker.worker.native_tri!=gpu.g_geometry_worker.worker.native_triangles
        && gpu.g_geometry_worker.worker.free_words<2);
assign obs_worker_active=!obs_worker_empty && !obs_worker_output_stall;
assign obs_c1_route=gpu.gpu_tex_mem_route;
assign obs_tex_arvalid=gpu.tex_axi_arvalid;
assign obs_tex_arready=gpu.tex_axi_arready;
assign obs_tex_araddr=gpu.tex_axi_araddr;
assign obs_tex_arlen=gpu.tex_axi_arlen;
assign obs_tex_rvalid=gpu.tex_axi_rvalid;
assign obs_tex_rdata=gpu.tex_axi_rdata;
assign obs_tex_rlast=gpu.tex_axi_rlast;

gpu_core #(
    .INCLUDE_CPU_RING(INCLUDE_CPU_RING),
    .INCLUDE_COMMAND_DMA(INCLUDE_COMMAND_DMA),
    .GPU_WRITE_COMBINE_FAST_FLUSH(GPU_WRITE_COMBINE_FAST_FLUSH),
    .GPU_WRITE_COMBINE_Z(GPU_WRITE_COMBINE_Z),
    .GPU_WRITE_GATHER(GPU_WRITE_GATHER),
    .GPU_MASKED_WRITE_BURSTS(GPU_MASKED_WRITE_BURSTS),
    .GPU_WRITE_COMBINE_BURST_HASH(GPU_WRITE_COMBINE_BURST_HASH),
    .INCLUDE_PARAM_TRI(INCLUDE_PARAM_TRI),
    .INCLUDE_VERT_TRI(INCLUDE_VERT_TRI),
    .INCLUDE_PARAM_TRI_RECS(INCLUDE_PARAM_TRI_RECS),
    .GPU_Z_READ_WINDOW(GPU_Z_READ_WINDOW),
    .GPU_CB_READ_WINDOW(GPU_CB_READ_WINDOW),
    .GPU_PSS_RECIP_CACHE(GPU_PSS_RECIP_CACHE),
    .GPU_STREAM_PIPE(GPU_STREAM_PIPE),
    .GPU_WRITE_COMBINE_PIPE(GPU_WRITE_COMBINE_PIPE),
    .GPU_WRITE_COMBINE(GPU_WRITE_COMBINE),
    .GPU_EW_PARALLEL_DIVS(GPU_EW_PARALLEL_DIVS),
    .GPU_TEX_CACHE_SET_BITS(GPU_TEX_CACHE_SET_BITS),
    .INCLUDE_COMPACT_SPAN(INCLUDE_COMPACT_SPAN),
    .INCLUDE_COLUMN_LIST(INCLUDE_COLUMN_LIST),
    .INCLUDE_DIRECT_COLOR(INCLUDE_DIRECT_COLOR),
    .INCLUDE_XFORM_RGB(INCLUDE_XFORM_RGB),
        .INCLUDE_GPU_XFORM_MAC(INCLUDE_GPU_XFORM_MAC),
    .INCLUDE_CLIP_TRI(INCLUDE_CLIP_TRI),
    .INCLUDE_VTX_CACHE(INCLUDE_VTX_CACHE),
    .INCLUDE_GPU_LIGHT(INCLUDE_GPU_LIGHT),
    .INCLUDE_PALETTE(INCLUDE_PALETTE),
    .INCLUDE_PERSPECTIVE(INCLUDE_PERSPECTIVE),
    .INCLUDE_DEPTH(INCLUDE_DEPTH),
    .INCLUDE_PARAM_SPAN_LIST(INCLUDE_PARAM_SPAN_LIST),
    .INCLUDE_COMBINE(INCLUDE_COMBINE),
    .INCLUDE_PARAM_SPAN_Q29(INCLUDE_PARAM_SPAN_Q29),
    .INCLUDE_TEX_MEM(INCLUDE_TEX_MEM)
) gpu (
    .clk(clk),
    .reset_n(reset_n),
    .gpu_enable(1'b1),
    .mesh_reset_n(reset_n),
    .mesh_arvalid(obs_mesh_arvalid),.mesh_arready(obs_mesh_arready),
    .mesh_araddr(obs_mesh_araddr),.mesh_arlen(obs_mesh_arlen),
    .mesh_rvalid(obs_mesh_rvalid),.mesh_rdata(obs_mesh_rdata),.mesh_rlast(obs_mesh_rlast),
    .gpu_tex_mem_arvalid(tex1_arvalid),.gpu_tex_mem_arready(tex1_arready),
    .gpu_tex_mem_araddr(tex1_araddr),.gpu_tex_mem_arlen(tex1_arlen),
    .gpu_tex_mem_rvalid(tex1_rvalid),.gpu_tex_mem_rdata(tex1_rdata),.gpu_tex_mem_rlast(tex1_rlast),
    .gpu_tex_mem_up_wr(c1_up_wr),.gpu_tex_mem_up_addr(c1_up_addr),
    .gpu_tex_mem_up_wdata(c1_up_data),.gpu_tex_mem_up_busy(c1_up_busy),
`ifdef INCLUDE_GPU_RENDER_CACHE
    // AXI4 read
    .m_rd_arvalid(gpu_s_rd_arvalid),
    .m_rd_arready(gpu_s_rd_arready),
    .m_rd_araddr(gpu_s_rd_araddr),
    .m_rd_arlen(gpu_s_rd_arlen),
    .m_rd_rvalid(gpu_s_rd_rvalid),
    .m_rd_rdata(gpu_s_rd_rdata),
    .m_rd_rlast(gpu_s_rd_rlast),
    // AXI4 write
    .m_wr_awvalid(gpu_s_wr_awvalid),
    .m_wr_awready(gpu_s_wr_awready),
    .m_wr_awaddr(gpu_s_wr_awaddr),
    .m_wr_awlen(gpu_s_wr_awlen),
    .m_wr_wvalid(gpu_s_wr_wvalid),
    .m_wr_wready(gpu_s_wr_wready),
    .m_wr_wdata(gpu_s_wr_wdata),
    .m_wr_wstrb(gpu_s_wr_wstrb),
    .m_wr_wlast(gpu_s_wr_wlast),
    .m_wr_bvalid(gpu_s_wr_bvalid),
`else
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
`endif
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
`ifdef INCLUDE_GPU_RENDER_CACHE
    .memory_barrier_done(gpu_cache_barrier_done),
    .memory_barrier_req(gpu_cache_barrier_req),
    .cache_clear_bypass(gpu_cache_clear_bypass),
    .cache_soft_reset(gpu_cache_soft_reset),
    .cache_tex_flush(gpu_cache_tex_flush),
`endif
    // MMIO
    .reg_wr(reg_wr),
    .reg_addr(reg_addr),
    .reg_wdata(reg_wdata),
`ifdef INCLUDE_GPU_RENDER_CACHE
    .reg_rdata(gpu_reg_rdata_core),
    .busy(gpu_busy_core),
`else
    .reg_rdata(reg_rdata),
    // Status
    .busy(busy),
`endif
    .fence_reached(fence_reached),
    .dbg_state(),
    .dbg_setup_step(),
    .dbg_aux(),
    .dbg_frag()
);

// Render cache (INCLUDE_GPU_RENDER_CACHE): same wiring as core_top.v, so the
// acceptance suite (and its slow-write runs) exercises GPU + cache together.
`ifdef INCLUDE_GPU_RENDER_CACHE
wire        gpu_s_rd_arvalid, gpu_s_rd_arready, gpu_s_rd_rvalid, gpu_s_rd_rlast;
wire [31:0] gpu_s_rd_araddr, gpu_s_rd_rdata;
wire [7:0]  gpu_s_rd_arlen;
wire        gpu_s_wr_awvalid, gpu_s_wr_awready, gpu_s_wr_wvalid, gpu_s_wr_wready;
wire        gpu_s_wr_wlast, gpu_s_wr_bvalid;
wire [31:0] gpu_s_wr_awaddr, gpu_s_wr_wdata;
wire [7:0]  gpu_s_wr_awlen;
wire [3:0]  gpu_s_wr_wstrb;
wire        gpu_busy_core;
wire [31:0] gpu_reg_rdata_core;
wire        gpu_cache_barrier_done, gpu_cache_barrier_req, gpu_cache_clear_bypass;
wire        gpu_cache_soft_reset, gpu_cache_tex_flush;
wire        gpu_cache_busy, gpu_cache_has_lines, gpu_cache_flush_done, gpu_cache_protocol_error;
wire [25:0] gpu_cache_m_araddr, gpu_cache_m_awaddr;
reg         gpu_cache_idle_flush;
reg         gpu_cache_tex_flush_pending;
always @(posedge clk) begin
    if (!reset_n || gpu_cache_soft_reset) begin
        gpu_cache_idle_flush <= 1'b0;
        gpu_cache_tex_flush_pending <= 1'b0;
    end else begin
        if (gpu_cache_flush_done) gpu_cache_idle_flush <= 1'b0;
        else if (!gpu_busy_core && gpu_cache_has_lines) gpu_cache_idle_flush <= 1'b1;
        if (gpu_cache_flush_done) gpu_cache_tex_flush_pending <= 1'b0;
        else if (gpu_cache_tex_flush && gpu_cache_has_lines) gpu_cache_tex_flush_pending <= 1'b1;
    end
end
assign gpu_cache_barrier_done = gpu_cache_flush_done || (!gpu_cache_has_lines && !gpu_cache_busy);
assign busy = gpu_busy_core || gpu_cache_busy || gpu_cache_has_lines || gpu_cache_idle_flush;
assign reg_rdata = gpu_reg_rdata_core | ((reg_addr == 4'd5 && busy) ? 32'd1 : 32'd0);
assign gpu_rd_araddr = {6'd0, gpu_cache_m_araddr};
assign gpu_wr_awaddr = {6'd0, gpu_cache_m_awaddr};
gpu_color_depth_cache #(
    .ADDR_W(26), .CACHE_ALL(1), .WAYS(2), .SET_BITS(8), .WORD_BITS(4),
    .REGISTER_READ_LOOKUP(1), .REGISTER_FILL_HAZARD(1), .SPECULATE_WRITE_HOT0(1)
) gpu_render_cache (
    .clk(clk), .reset_n(reset_n && !gpu_cache_soft_reset), .bus_reset_n(reset_n),
    .range0_lo(26'd0), .range0_hi(26'd0), .range1_lo(26'd0), .range1_hi(26'd0),
    .write_no_allocate(gpu_cache_clear_bypass),
    .flush_req((gpu_cache_barrier_req && gpu_cache_has_lines) || gpu_cache_idle_flush
               || gpu_cache_tex_flush_pending),
    .flush_done(gpu_cache_flush_done), .busy(gpu_cache_busy), .has_lines(gpu_cache_has_lines),
    .s_arvalid(gpu_s_rd_arvalid), .s_arready(gpu_s_rd_arready), .s_araddr(gpu_s_rd_araddr[25:0]),
    .s_arlen(gpu_s_rd_arlen), .s_rvalid(gpu_s_rd_rvalid), .s_rdata(gpu_s_rd_rdata), .s_rlast(gpu_s_rd_rlast),
    .s_awvalid(gpu_s_wr_awvalid), .s_awready(gpu_s_wr_awready), .s_awaddr(gpu_s_wr_awaddr[25:0]),
    .s_awlen(gpu_s_wr_awlen), .s_wvalid(gpu_s_wr_wvalid), .s_wready(gpu_s_wr_wready),
    .s_wdata(gpu_s_wr_wdata), .s_wstrb(gpu_s_wr_wstrb), .s_wlast(gpu_s_wr_wlast), .s_bvalid(gpu_s_wr_bvalid),
    .m_arvalid(gpu_rd_arvalid), .m_arready(gpu_rd_arready), .m_araddr(gpu_cache_m_araddr),
    .m_arlen(gpu_rd_arlen), .m_rvalid(gpu_rd_rvalid), .m_rdata(gpu_rd_rdata), .m_rlast(gpu_rd_rlast),
    .m_awvalid(gpu_wr_awvalid), .m_awready(gpu_wr_awready), .m_awaddr(gpu_cache_m_awaddr),
    .m_awlen(gpu_wr_awlen), .m_wvalid(gpu_wr_wvalid), .m_wready(gpu_wr_wready),
    .m_wdata(gpu_wr_wdata), .m_wstrb(gpu_wr_wstrb), .m_wlast(gpu_wr_wlast), .m_bvalid(gpu_wr_bvalid),
    .hits(), .misses(), .writebacks(), .protocol_error(gpu_cache_protocol_error)
);
always @(posedge clk)
    if (reset_n && gpu_cache_protocol_error) begin
        $display("FAIL render cache protocol error");
        $fatal(1, "render cache protocol error");
    end
`endif

// Production debug outputs are tied off to save resources. Observe the actual
// state in the testbench so timeout diagnostics can identify a stalled stage.
assign dbg_state = gpu.state;
assign dbg_setup_step = {2'd0, gpu.spanprod_calc_step};
assign dbg_aux = {gpu.sp_count, gpu.p0a_valid, gpu.p0_valid, gpu.p1_valid,
                  gpu.p2_valid, gpu.p2b_valid, gpu.p3_valid, gpu.fbss,
                  gpu.fp_pipe_stall, gpu.p1_tex_ready, gpu.tex_resp_valid,
                  gpu.cmap_pending_valid, gpu.cmap_resp_valid_b, gpu.cmap_req_ready_b};
assign dbg_frag = {20'd0, gpu.tex_cache.state, gpu.tex_cache.pipe_b_live,
                   gpu.tex_cache.held_valid_b, gpu.tex_cache.pipe_valid_b,
                   gpu.tex_cache.pipe_hit_b, gpu.tex_cache.pipe_miss_b,
                   gpu.src_done, gpu.spanprod_active,
                   gpu.p1_flags[0], gpu.p2b_flags[0]};

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
// Real Pocket memory backend. Read traffic is a repeatable stress
// schedule, not a replacement for CPU instruction execution or the audio mixer.
integer traffic=0;
initial if ($value$plusargs("memory_traffic=%d", traffic)) begin end
reg [31:0] now_cycle;
reg ag1_valid, ag3_valid, scan_req, scan_active;
wire ag1_ready, ag3_ready, scan_done;
reg [31:0] next_cpu, next_audio, next_scan;
always @(posedge clk) begin
    if (!reset_n) begin
        now_cycle<=0; ag1_valid<=0; ag3_valid<=0; scan_req<=0; scan_active<=0;
        next_cpu<=30000; next_audio<=30000; next_scan<=30000;
    end else begin
        now_cycle<=now_cycle+1; scan_req<=0;
        if(traffic>=2 && !ag1_valid && now_cycle>=next_cpu) ag1_valid<=1;
        if(ag1_valid && ag1_ready) begin ag1_valid<=0; next_cpu<=now_cycle+97; end
        if(traffic>=1 && !ag3_valid && now_cycle>=next_audio) ag3_valid<=1;
        if(ag3_valid && ag3_ready) begin ag3_valid<=0; next_audio<=now_cycle+2048; end
        if(traffic>=1 && !scan_active && now_cycle>=next_scan) begin
            scan_req<=1; scan_active<=1; next_scan<=now_cycle+6361;
        end
        if(scan_done) scan_active<=0;
    end
end
assign obs_cpu_valid=ag1_valid;
assign obs_cpu_ready=ag1_ready;
assign obs_audio_valid=ag3_valid;
assign obs_audio_ready=ag3_ready;
assign obs_scan_req=scan_req;
assign obs_scan_done=scan_done;
assign obs_gpu_arvalid=gpu_rd_arvalid;
assign obs_gpu_arready=gpu_rd_arready;
assign obs_gpu_awvalid=gpu_wr_awvalid;
assign obs_gpu_awready=gpu_wr_awready;
assign obs_gpu_wvalid=gpu_wr_wvalid;
assign obs_gpu_wready=gpu_wr_wready;
tb_sdram_cont #(.CPU_FAIR_THRESHOLD(CPU_FAIR_THRESHOLD)) memory (
    .clk(clk),
    .reset_n(reset_n),
    .m0_arvalid(gpu_rd_arvalid),
    .m0_arready(gpu_rd_arready),
    .m0_araddr({6'd0,gpu_rd_araddr[25:0]}),
    .m0_arlen(gpu_rd_arlen),
    .m0_rvalid(gpu_rd_rvalid),
    .m0_rdata(gpu_rd_rdata),
    .m0_rlast(gpu_rd_rlast),
    .m0_awvalid(gpu_wr_awvalid),
    .m0_awready(gpu_wr_awready),
    .m0_awaddr({6'd0,gpu_wr_awaddr[25:0]}),
    .m0_awlen(gpu_wr_awlen),
    .m0_wvalid(gpu_wr_wvalid),
    .m0_wready(gpu_wr_wready),
    .m0_wdata(gpu_wr_wdata),
    .m0_wstrb(gpu_wr_wstrb),
    .m0_wlast(gpu_wr_wlast),
    .m0_bvalid(gpu_wr_bvalid),
    .m1_arvalid(ag1_valid),
    .m1_arready(ag1_ready),
    .m1_araddr(32'h02400000),
    .m1_arlen(8'd15),
    .m1_rvalid(obs_cpu_rvalid),
    .m1_rdata(),
    .m1_rlast(obs_cpu_rlast),
    .m1_rready(1'b1),
    .m1_awvalid(1'b0),
    .m1_awready(),
    .m1_awaddr(32'd0),
    .m1_awlen(8'd0),
    .m1_wvalid(1'b0),
    .m1_wready(),
    .m1_wdata(32'd0),
    .m1_wstrb(4'd0),
    .m1_wlast(1'b0),
    .m1_bvalid(),
    .m2_arvalid(1'b0),
    .m2_arready(),
    .m2_araddr(32'h02800000),
    .m2_arlen(8'd15),
    .m2_rvalid(),
    .m2_rdata(),
    .m2_rlast(),
    .m2_rready(1'b1),
    .m2_awvalid(1'b0),
    .m2_awready(),
    .m2_awaddr(32'd0),
    .m2_awlen(8'd0),
    .m2_wvalid(1'b0),
    .m2_wready(),
    .m2_wdata(32'd0),
    .m2_wstrb(4'd0),
    .m2_wlast(1'b0),
    .m2_bvalid(),
    .m3_arvalid(ag3_valid),
    .m3_arready(ag3_ready),
    .m3_araddr(32'h02c00000),
    .m3_arlen(8'd15),
    .m3_rvalid(obs_audio_rvalid),
    .m3_rdata(),
    .m3_rlast(obs_audio_rlast),
    .m3_rready(1'b1),
    .dbg_arb_state(obs_arb_state),
    .dbg_grant(obs_grant),
    .inj_burst_rd(scan_req),
    .inj_burst_addr(25'h1000000),
    .inj_burst_len(11'd160),
    .inj_burst_data_done(scan_done),
    .bd_we(bd_we),
    .bd_addr({4'd0,bd_addr[19:0]}),
    .bd_wdata(bd_wdata),
    .bd_rd_addr({4'd0,bd_rd_addr[19:0]}),
    .bd_rd_data(bd_rd_data)
);
reg [31:0] rd_count;
assign dbg_rd_txn_count_o=rd_count;
assign dbg_rd_last_latency_o=0;
always @(posedge clk) begin
    if(!reset_n) begin
        rd_count<=0; dbg_aw_count<=0; dbg_aw_burst_count<=0; dbg_aw_max_len<=0;
        dbg_w_beat_cycles<=0; dbg_w_busy_cycles<=0; dbg_rd_busy_cycles<=0;
        dbg_rw_overlap_cycles<=0;
    end else begin
        if(gpu_rd_arvalid && gpu_rd_arready) rd_count<=rd_count+1;
        if(gpu_wr_awvalid && gpu_wr_awready) begin
            dbg_aw_count<=dbg_aw_count+1;
            if(gpu_wr_awlen!=0) dbg_aw_burst_count<=dbg_aw_burst_count+1;
            if(gpu_wr_awlen>dbg_aw_max_len) dbg_aw_max_len<=gpu_wr_awlen;
        end
        if(gpu_wr_wvalid && gpu_wr_wready) dbg_w_beat_cycles<=dbg_w_beat_cycles+1;
    end
end

// ---- sm64_bottleneck_20260929 counters (co-sim only) ----
reg [63:0] bn_total, bn_fp_run, bn_fp_stall, bn_fp_tex, bn_fp_fbss, bn_fp_z, bn_fp_wbuf, bn_fp_infl, bn_fp_other;
reg [63:0] bn_fp_bubble, bn_px, bn_zfill, bn_rd_ar, bn_rd_first;
reg [63:0] bn_state [0:63];
reg [63:0] bn_fbss [0:15];
reg [63:0] bn_cst [0:31];
reg [3:0] bn_fbss_q;
reg bn_wait_first;
integer bn_i;
initial begin
    bn_total=0; bn_fp_run=0; bn_fp_stall=0; bn_fp_tex=0; bn_fp_fbss=0; bn_fp_z=0; bn_fp_wbuf=0; bn_fp_infl=0;
    bn_fp_other=0; bn_fp_bubble=0; bn_px=0; bn_zfill=0; bn_rd_ar=0; bn_rd_first=0; bn_fbss_q=0; bn_wait_first=0;
    for (bn_i=0; bn_i<64; bn_i=bn_i+1) bn_state[bn_i]=0;
    for (bn_i=0; bn_i<16; bn_i=bn_i+1) bn_fbss[bn_i]=0;
    for (bn_i=0; bn_i<32; bn_i=bn_i+1) bn_cst[bn_i]=0;
end
always @(posedge clk) if (reset_n && busy) begin
    bn_total <= bn_total + 1;
    bn_state[gpu.state] <= bn_state[gpu.state] + 1;
    bn_fbss[gpu.fbss] <= bn_fbss[gpu.fbss] + 1;
    bn_cst[gpu_render_cache.state] <= bn_cst[gpu_render_cache.state] + 1;
    bn_fbss_q <= gpu.fbss;
    if (gpu.fbss == 4'd5 && bn_fbss_q != 4'd5) bn_zfill <= bn_zfill + 1;
    if (gpu.state == 6'd6) begin
        if (gpu.fp_pipe_stall) begin
            bn_fp_stall <= bn_fp_stall + 1;
            if (gpu.p1_valid && !gpu.p1_response_ready) bn_fp_tex <= bn_fp_tex + 1;
            else if (gpu.fbss != 4'd0) bn_fp_fbss <= bn_fp_fbss + 1;
            else if (gpu.p3_valid && !gpu.p3_discard && gpu.p3_z_test) bn_fp_z <= bn_fp_z + 1;
            else if (gpu.fb_write_buffer_stall) bn_fp_wbuf <= bn_fp_wbuf + 1;
            else if (gpu.m_wr_inflight_near_full) bn_fp_infl <= bn_fp_infl + 1;
            else bn_fp_other <= bn_fp_other + 1;
        end else begin
            bn_fp_run <= bn_fp_run + 1;
            if (!gpu.p0_valid) bn_fp_bubble <= bn_fp_bubble + 1;
        end
        if (!gpu.fp_pipe_stall && gpu.p3_valid && !gpu.p3_discard) bn_px <= bn_px + 1;
    end
    if (gpu.m_rd_arvalid && gpu.m_rd_arready) begin bn_rd_ar <= bn_rd_ar + 1; bn_wait_first <= 1; end
    else if (gpu.m_rd_rvalid) bn_wait_first <= 0;
    if (bn_wait_first && !gpu.m_rd_rvalid) bn_rd_first <= bn_rd_first + 1;
end
final begin
    $display("BN total %0d", bn_total);
    for (bn_i=0; bn_i<64; bn_i=bn_i+1) if (bn_state[bn_i] != 0) $display("BN state %0d %0d", bn_i, bn_state[bn_i]);
    for (bn_i=0; bn_i<16; bn_i=bn_i+1) if (bn_fbss[bn_i] != 0) $display("BN fbss %0d %0d", bn_i, bn_fbss[bn_i]);
    for (bn_i=0; bn_i<32; bn_i=bn_i+1) if (bn_cst[bn_i] != 0) $display("BN cstate %0d %0d", bn_i, bn_cst[bn_i]);
    $display("BN fp run %0d bubble %0d px %0d stall %0d tex %0d fbss %0d z %0d wbuf %0d infl %0d other %0d",
             bn_fp_run, bn_fp_bubble, bn_px, bn_fp_stall, bn_fp_tex, bn_fp_fbss, bn_fp_z, bn_fp_wbuf, bn_fp_infl, bn_fp_other);
    $display("BN mem zfills %0d rd_ar %0d rd_first_wait %0d cache_hits %0d cache_misses %0d cache_wb %0d",
             bn_zfill, bn_rd_ar, bn_rd_first, 0, 0, 0);
end

// Exclusive counters gated after initialization. No GPU behavior is changed.
reg [63:0] crit_main[0:63],crit_drv[0:63],crit_pss[0:63];
reg [63:0] crit_frag[0:15],crit_span_len[0:15];
reg [63:0] crit_drain[0:15];
integer ci;
initial begin
 for(ci=0;ci<64;ci=ci+1)begin crit_main[ci]=0;crit_drv[ci]=0;crit_pss[ci]=0;end
 for(ci=0;ci<16;ci=ci+1)begin crit_frag[ci]=0;crit_span_len[ci]=0;end
 for(ci=0;ci<16;ci=ci+1)crit_drain[ci]=0;
end
wire crit_zero_uv=gpu.spanprod_pl_origin[1]==0&&gpu.spanprod_pl_du[1]==0&&gpu.spanprod_pl_dv[1]==0
 &&gpu.spanprod_pl_origin[2]==0&&gpu.spanprod_pl_du[2]==0&&gpu.spanprod_pl_dv[2]==0;
reg [3:0] crit_block;
always @* begin
 crit_block=0;
 if(gpu.fp_pipe_stall)begin
  if(gpu.fbss==gpu.FBSS_ZTEST_R_WAIT)crit_block=1;
  else if(gpu.fbss==gpu.FBSS_ZTEST_ACC_EVAL)crit_block=2;
  else if(gpu.fbss!=gpu.FBSS_IDLE)crit_block=3;
  else if(gpu.p3_valid&&!gpu.p3_discard&&gpu.p3_z_test)begin
   if(!gpu.fb_read_barrier_clear)crit_block=4;
   else if(gpu.zw_fill_busy)crit_block=5;
   else crit_block=6;
  end else if(gpu.fb_write_buffer_stall)crit_block=7;
  else if(gpu.p1_valid&&!gpu.p1_response_ready)crit_block=8;
  else if(gpu.cmap_pipe_wait)crit_block=9;
  else crit_block=10;
 end else if(gpu.p2b_valid)crit_block=11;
 else if(gpu.src_done)crit_block=12;
 else if(gpu.persp_active)crit_block=13;
 else crit_block=14;
end
always @(posedge clk)if(reset_n&&critical_enable)begin
 crit_main[gpu.state]<=crit_main[gpu.state]+1;
 if(gpu.state==gpu.S_TRI_DERIVE)crit_drv[gpu.dstate]<=crit_drv[gpu.dstate]+1;
 if(gpu.state==gpu.S_FRAG_PIPE)begin
  crit_frag[crit_block]<=crit_frag[crit_block]+1;
  crit_pss[gpu.persp_pss]<=crit_pss[gpu.persp_pss]+1;
  if(crit_zero_uv&&gpu.persp_active)crit_drain[9]<=crit_drain[9]+1;
  if(crit_zero_uv&&gpu.persp_issue_stall&&!gpu.fp_pipe_stall&&!gpu.src_done)crit_drain[10]<=crit_drain[10]+1;
  if(gpu.persp_issue_stall&&!gpu.fp_pipe_stall&&!gpu.src_done)crit_drain[11]<=crit_drain[11]+1;
  if(gpu.src_done)begin
   crit_drain[0]<=crit_drain[0]+1;
   if(gpu.fp_pipe_stall)crit_drain[1]<=crit_drain[1]+1;
   if(gpu.fbss==gpu.FBSS_ZTEST_R_WAIT)crit_drain[2]<=crit_drain[2]+1;
   if(gpu.p3_valid&&!gpu.p3_discard&&gpu.p3_z_test&&gpu.fbss==gpu.FBSS_IDLE&&!gpu.fb_read_barrier_clear)crit_drain[3]<=crit_drain[3]+1;
   if(gpu.persp_active)crit_drain[4]<=crit_drain[4]+1;
   if(gpu.p1_valid||gpu.p2_valid||gpu.p2b_valid||gpu.p3_valid)crit_drain[5]<=crit_drain[5]+1;
  end
 end
 if(gpu.state==gpu.S_SPANPROD_EMIT)begin
  if(crit_zero_uv&&gpu.spanprod_attr_persp)crit_drain[8]<=crit_drain[8]+1;
  if(gpu.spanprod_cur_count<16)crit_span_len[gpu.spanprod_cur_count]<=crit_span_len[gpu.spanprod_cur_count]+1;
  else crit_span_len[15]<=crit_span_len[15]+1;
 end
end
final begin
 for(ci=0;ci<64;ci=ci+1)$display("CRITICAL_MAIN %0d %0d",ci,crit_main[ci]);
 for(ci=0;ci<64;ci=ci+1)$display("CRITICAL_DRV %0d %0d",ci,crit_drv[ci]);
 for(ci=0;ci<64;ci=ci+1)$display("CRITICAL_PSS %0d %0d",ci,crit_pss[ci]);
 for(ci=0;ci<16;ci=ci+1)$display("CRITICAL_FRAG %0d %0d",ci,crit_frag[ci]);
 for(ci=0;ci<16;ci=ci+1)$display("CRITICAL_SPAN %0d %0d",ci,crit_span_len[ci]);
 for(ci=0;ci<16;ci=ci+1)$display("CRITICAL_DRAIN %0d %0d",ci,crit_drain[ci]);
end


wire audit_handoff = gpu.state==gpu.S_FRAG_PIPE && gpu.span_la==gpu.LA_READY
 &&gpu.span_la_eligible&&gpu.src_done&&!gpu.p0a_valid&&!gpu.p0_valid
 &&!gpu.persp_active&&!gpu.persp_swap_pending&&gpu.persp_pss==gpu.PSS_IDLE;
// These live fields can be consumed by the older tail after the handoff.
// PERSP is source-only and may have been cleared by the constant-Z shortcut.
wire [511:0] audit_constants={gpu.sp_truecolor,gpu.sp_rgb,gpu.sp_blend,
 gpu.sp_const_alpha,gpu.sp_a6,gpu.cb_inv_a6,gpu.sp_cd_combine,
 gpu.sp_flags&4'b1011,gpu.sp_tex_width,gpu.sp_z_test_enable,
 gpu.sp_z_write_enable,gpu.sp_z_step,gpu.sp_zc_step,gpu.sp_zinv_step,
 gpu.spanprod_attr0_step_pre,gpu.spanprod_attr1_step_pre,
 gpu.sp_tex_w_mask,gpu.sp_tex_h_mask,gpu.sp_mirror_s,gpu.sp_mirror_t,
 gpu.sp_s_clamp_min,gpu.sp_s_clamp_max,gpu.sp_t_clamp_min,gpu.sp_t_clamp_max};
reg audit_previous_handoff=0;
reg [511:0] audit_old_constants;
reg [1:0] audit_old_index;
reg audit_old_chunk;
reg [2:0] audit_old_count;
reg [31:0] audit_step0,audit_step1;
reg audit_previous_owned=0;
reg [63:0] audit_counts[0:15];
integer ai;
initial for(ai=0;ai<16;ai=ai+1)audit_counts[ai]=0;
always @(posedge clk)begin
 if(!reset_n||gpu.soft_reset)begin
  audit_previous_handoff<=0;audit_previous_owned<=0;
 end else begin
  if(audit_previous_handoff)begin
   if(gpu.state!=gpu.S_FRAG_PIPE)$fatal(1,"handoff left response collector");
   if(audit_old_constants!==audit_constants)$fatal(1,"handoff changed tail constants");
   if(audit_old_chunk)begin
    if(gpu.spanprod_idx!=0||gpu.spanprod_record_count!=audit_old_count
       ||gpu.spanprod_records_left!=audit_old_count||gpu.tri_fill_idx!=0||gpu.span_la_chunk)
      $fatal(1,"chunk handoff was not atomic");
   end else if(gpu.spanprod_idx!=audit_old_index+2'd1)$fatal(1,"handoff index did not advance once");
   if(gpu.src_done)$fatal(1,"handoff failed to restart source");
  end
  // Prefetch may rewrite these current-PSS operands, but only identically.
  if(audit_previous_owned&&gpu.state==gpu.S_FRAG_PIPE&&gpu.span_la_owned)begin
   if(audit_step0!==gpu.spanprod_attr0_step_pre||audit_step1!==gpu.spanprod_attr1_step_pre)
    $fatal(1,"lookahead changed live PSS plane steps");
  end
  audit_previous_handoff<=audit_handoff;
  audit_previous_owned<=gpu.state==gpu.S_FRAG_PIPE&&gpu.span_la_owned;
  audit_step0<=gpu.spanprod_attr0_step_pre;audit_step1<=gpu.spanprod_attr1_step_pre;
  if(audit_handoff)begin
   audit_old_constants<=audit_constants;audit_old_index<=gpu.spanprod_idx;
   audit_old_chunk<=gpu.span_la_chunk;audit_old_count<=gpu.tri_fill_idx;
   if(!gpu.tex_route||gpu.spanprod_direct_affine||(!gpu.span_la_chunk&&gpu.spanprod_idx>=gpu.spanprod_last_idx))
    $fatal(1,"handoff crossed excluded command boundary");
   if(gpu.sp_z_write_enable&&!gpu.sp_z_test_enable)$fatal(1,"write-only depth handed off");
   audit_counts[0]<=audit_counts[0]+1;
   if(gpu.stream_pending)audit_counts[1]<=audit_counts[1]+1;
   if(gpu.tex_stream.req_count!=0)audit_counts[2]<=audit_counts[2]+1;
   if(gpu.tex_stream.lookup_pending)audit_counts[3]<=audit_counts[3]+1;
   if(gpu.tex_stream.res_count!=0)audit_counts[4]<=audit_counts[4]+1;
   if(gpu.p1_valid||gpu.p2_valid||gpu.p2b_valid||gpu.p3_valid)audit_counts[5]<=audit_counts[5]+1;
   if(gpu.fbss!=gpu.FBSS_IDLE)audit_counts[6]<=audit_counts[6]+1;
   if(gpu.zw_fill_busy)audit_counts[7]<=audit_counts[7]+1;
   if(gpu.blend_arvalid)audit_counts[8]<=audit_counts[8]+1;
   if(gpu.sp_blend)audit_counts[9]<=audit_counts[9]+1;
   if(gpu.z_src_pending_valid)audit_counts[10]<=audit_counts[10]+1;
   if(gpu.spanprod_cur_count==1)audit_counts[11]<=audit_counts[11]+1;
   if(gpu.spanprod_span_axis)audit_counts[12]<=audit_counts[12]+1;
  end
  if(gpu.state==gpu.S_FRAG_PIPE&&gpu.span_la==gpu.LA_SETUP&&!gpu.spanprod_cur_nonzero)
   audit_counts[13]<=audit_counts[13]+1;
  if(gpu.state==gpu.S_SPANPROD_EMIT&&!gpu.tex_route)audit_counts[14]<=audit_counts[14]+1;
  if(gpu.state==gpu.S_SPANPROD_EMIT&&gpu.spanprod_z_write&&!gpu.spanprod_z_test)
   audit_counts[15]<=audit_counts[15]+1;
 end
end
final for(ai=0;ai<16;ai=ai+1)$display("SPAN_AUDIT %0d %0d",ai,audit_counts[ai]);

// Independent FIFO records each walker handshake, then compares the exact
// record later installed by serial EMIT or overlapping source handoff.
reg [47:0] chunk_records[0:31];
reg [4:0] chunk_wr=0,chunk_rd=0;
reg [63:0] chunk_accepted=0,chunk_emitted=0;
reg [63:0] chunk_counts[0:7];
reg chunk_previous_owned=0,chunk_previous_commit=0;
reg [1:0] chunk_previous_index;
reg [2:0] chunk_previous_visible_count;
wire chunk_take=gpu.tri_rec_valid&&gpu.tri_rec_ready;
wire chunk_emit=gpu.cmd_is_tri_walker&&
 ((gpu.state==gpu.S_SPANPROD_EMIT&&gpu.spanprod_cur_nonzero)||audit_handoff);
integer ch;
initial for(ch=0;ch<8;ch=ch+1)chunk_counts[ch]=0;
always @(posedge clk)begin
 if(!reset_n||gpu.soft_reset)begin
  chunk_wr<=0;chunk_rd<=0;chunk_accepted<=0;chunk_emitted<=0;
  chunk_previous_owned<=0;chunk_previous_commit<=0;
 end else begin
  if(chunk_previous_owned&&!chunk_previous_commit)begin
   if(gpu.spanprod_idx!=chunk_previous_index
      ||gpu.spanprod_record_count!=chunk_previous_visible_count)
    $fatal(1,"pending chunk overwrote visible index/count");
  end
  chunk_previous_owned<=gpu.state==gpu.S_FRAG_PIPE&&gpu.span_la_chunk;
  chunk_previous_commit<=audit_handoff;
  chunk_previous_index<=gpu.spanprod_idx;
  chunk_previous_visible_count<=gpu.spanprod_record_count;
  if(gpu.state==gpu.S_FRAG_PIPE&&gpu.span_la_chunk)begin
   if(gpu.tri_fill_idx>4)$fatal(1,"pending chunk overflow");
   if(gpu.span_la==gpu.LA_FILL&&gpu.tri_fill_idx>=4)$fatal(1,"accepted after full chunk");
   if(gpu.span_la==gpu.LA_READY&&(gpu.tri_fill_idx==0||gpu.tri_fill_idx>4))
    $fatal(1,"empty/invalid chunk became ready");
  end
  if(chunk_take)begin
   if(chunk_accepted-chunk_emitted>=31)$fatal(1,"audit FIFO overflow");
   chunk_records[chunk_wr]<={gpu.tri_rec_u,gpu.tri_rec_v,gpu.tri_rec_count};
   chunk_wr<=chunk_wr+1'b1;chunk_accepted<=chunk_accepted+1;
   if(gpu.state==gpu.S_FRAG_PIPE)chunk_counts[6]<=chunk_counts[6]+1;
  end
  if(chunk_emit)begin
   if(chunk_accepted==chunk_emitted)$fatal(1,"span emitted without walker record");
   if(chunk_records[chunk_rd]!=={gpu.spanprod_cur_u,gpu.spanprod_cur_v,gpu.spanprod_cur_count})
    $fatal(1,"span record lost/reordered across chunk");
   chunk_rd<=chunk_rd+1'b1;chunk_emitted<=chunk_emitted+1;
  end
  if(audit_handoff&&gpu.span_la_chunk)begin
   chunk_counts[0]<=chunk_counts[0]+1;
   chunk_counts[gpu.tri_fill_idx]<=chunk_counts[gpu.tri_fill_idx]+1;
   if(gpu.tri_walker_done)chunk_counts[7]<=chunk_counts[7]+1;
  end
  if(gpu.state==gpu.S_FRAG_PIPE&&gpu.span_la==gpu.LA_FILL
     &&gpu.tri_walker_done&&gpu.tri_fill_idx==0)chunk_counts[5]<=chunk_counts[5]+1;
 end
end
final begin
 if(chunk_accepted!=chunk_emitted)$fatal(1,"final walker records stranded");
 $display("CHUNK_RECORDS accepted=%0d emitted=%0d",chunk_accepted,chunk_emitted);
 for(ch=0;ch<8;ch=ch+1)$display("CHUNK_AUDIT %0d %0d",ch,chunk_counts[ch]);
end

// Every field omitted by resume must retain the initial EMIT value. Separate
// bitwise assertions identify an omitted field rather than only a pixel mismatch.
reg resume_after_emit=0;
reg [63:0] resume_checks=0,resume_affine=0,resume_persp=0,resume_combine=0;
reg [$bits(gpu.cb_inv_a6)-1:0] resume_initial_cb_inv_a6;
reg [$bits(gpu.sp_B_step)-1:0] resume_initial_sp_B_step;
reg [$bits(gpu.sp_Db_step)-1:0] resume_initial_sp_Db_step;
reg [$bits(gpu.sp_Dg_step)-1:0] resume_initial_sp_Dg_step;
reg [$bits(gpu.sp_Dr_step)-1:0] resume_initial_sp_Dr_step;
reg [$bits(gpu.sp_R_step)-1:0] resume_initial_sp_R_step;
reg [$bits(gpu.sp_a6)-1:0] resume_initial_sp_a6;
reg [$bits(gpu.sp_blend)-1:0] resume_initial_sp_blend;
reg [$bits(gpu.sp_cd_combine)-1:0] resume_initial_sp_cd_combine;
reg [$bits(gpu.sp_clamp_enable)-1:0] resume_initial_sp_clamp_enable;
reg [$bits(gpu.sp_colormap_id)-1:0] resume_initial_sp_colormap_id;
reg [$bits(gpu.sp_const_alpha)-1:0] resume_initial_sp_const_alpha;
reg [$bits(gpu.sp_fastpath)-1:0] resume_initial_sp_fastpath;
reg [$bits(gpu.sp_light_step)-1:0] resume_initial_sp_light_step;
reg [$bits(gpu.sp_persp_q29_mode)-1:0] resume_initial_sp_persp_q29_mode;
reg [$bits(gpu.sp_q29_z_enable)-1:0] resume_initial_sp_q29_z_enable;
reg [$bits(gpu.sp_rgb)-1:0] resume_initial_sp_rgb;
reg [$bits(gpu.sp_s_max_mirrored)-1:0] resume_initial_sp_s_max_mirrored;
reg [$bits(gpu.sp_s_min_mirrored)-1:0] resume_initial_sp_s_min_mirrored;
reg [$bits(gpu.sp_t_max_mirrored)-1:0] resume_initial_sp_t_max_mirrored;
reg [$bits(gpu.sp_t_min_mirrored)-1:0] resume_initial_sp_t_min_mirrored;
reg [$bits(gpu.sp_tex_addr)-1:0] resume_initial_sp_tex_addr;
reg [$bits(gpu.sp_tex_h_octave)-1:0] resume_initial_sp_tex_h_octave;
reg [$bits(gpu.sp_tex_w_octave)-1:0] resume_initial_sp_tex_w_octave;
reg [$bits(gpu.sp_tex_width)-1:0] resume_initial_sp_tex_width;
reg [$bits(gpu.sp_truecolor)-1:0] resume_initial_sp_truecolor;
reg [$bits(gpu.sp_z_step)-1:0] resume_initial_sp_z_step;
reg [$bits(gpu.sp_z_test_enable)-1:0] resume_initial_sp_z_test_enable;
reg [$bits(gpu.sp_z_value_step)-1:0] resume_initial_sp_z_value_step;
reg [$bits(gpu.sp_z_write_enable)-1:0] resume_initial_sp_z_write_enable;
reg [$bits(gpu.sp_zc_step)-1:0] resume_initial_sp_zc_step;
reg [$bits(gpu.sp_zinv_step)-1:0] resume_initial_sp_zinv_step;
reg [$bits(gpu.sp_zinv_step_zero)-1:0] resume_initial_sp_zinv_step_zero;
always @(posedge clk)begin
 if(!reset_n||gpu.soft_reset)resume_after_emit<=0;
 else begin
  resume_after_emit<=gpu.state==gpu.S_SPANPROD_EMIT&&gpu.spanprod_cur_nonzero;
  if(resume_after_emit)resume_initial_cb_inv_a6<=gpu.cb_inv_a6;
  if(resume_after_emit)resume_initial_sp_B_step<=gpu.sp_B_step;
  if(resume_after_emit)resume_initial_sp_Db_step<=gpu.sp_Db_step;
  if(resume_after_emit)resume_initial_sp_Dg_step<=gpu.sp_Dg_step;
  if(resume_after_emit)resume_initial_sp_Dr_step<=gpu.sp_Dr_step;
  if(resume_after_emit)resume_initial_sp_R_step<=gpu.sp_R_step;
  if(resume_after_emit)resume_initial_sp_a6<=gpu.sp_a6;
  if(resume_after_emit)resume_initial_sp_blend<=gpu.sp_blend;
  if(resume_after_emit)resume_initial_sp_cd_combine<=gpu.sp_cd_combine;
  if(resume_after_emit)resume_initial_sp_clamp_enable<=gpu.sp_clamp_enable;
  if(resume_after_emit)resume_initial_sp_colormap_id<=gpu.sp_colormap_id;
  if(resume_after_emit)resume_initial_sp_const_alpha<=gpu.sp_const_alpha;
  if(resume_after_emit)resume_initial_sp_fastpath<=gpu.sp_fastpath;
  if(resume_after_emit)resume_initial_sp_light_step<=gpu.sp_light_step;
  if(resume_after_emit)resume_initial_sp_persp_q29_mode<=gpu.sp_persp_q29_mode;
  if(resume_after_emit)resume_initial_sp_q29_z_enable<=gpu.sp_q29_z_enable;
  if(resume_after_emit)resume_initial_sp_rgb<=gpu.sp_rgb;
  if(resume_after_emit)resume_initial_sp_s_max_mirrored<=gpu.sp_s_max_mirrored;
  if(resume_after_emit)resume_initial_sp_s_min_mirrored<=gpu.sp_s_min_mirrored;
  if(resume_after_emit)resume_initial_sp_t_max_mirrored<=gpu.sp_t_max_mirrored;
  if(resume_after_emit)resume_initial_sp_t_min_mirrored<=gpu.sp_t_min_mirrored;
  if(resume_after_emit)resume_initial_sp_tex_addr<=gpu.sp_tex_addr;
  if(resume_after_emit)resume_initial_sp_tex_h_octave<=gpu.sp_tex_h_octave;
  if(resume_after_emit)resume_initial_sp_tex_w_octave<=gpu.sp_tex_w_octave;
  if(resume_after_emit)resume_initial_sp_tex_width<=gpu.sp_tex_width;
  if(resume_after_emit)resume_initial_sp_truecolor<=gpu.sp_truecolor;
  if(resume_after_emit)resume_initial_sp_z_step<=gpu.sp_z_step;
  if(resume_after_emit)resume_initial_sp_z_test_enable<=gpu.sp_z_test_enable;
  if(resume_after_emit)resume_initial_sp_z_value_step<=gpu.sp_z_value_step;
  if(resume_after_emit)resume_initial_sp_z_write_enable<=gpu.sp_z_write_enable;
  if(resume_after_emit)resume_initial_sp_zc_step<=gpu.sp_zc_step;
  if(resume_after_emit)resume_initial_sp_zinv_step<=gpu.sp_zinv_step;
  if(resume_after_emit)resume_initial_sp_zinv_step_zero<=gpu.sp_zinv_step_zero;
  if(audit_handoff)begin
   resume_checks<=resume_checks+1;
   if(gpu.spanprod_attr_persp)resume_persp<=resume_persp+1;
   else resume_affine<=resume_affine+1;
   if(gpu.sp_cd_combine)resume_combine<=resume_combine+1;
   if(gpu.cb_inv_a6!==resume_initial_cb_inv_a6)$fatal(1,"resume changed constant cb_inv_a6");
   if(gpu.sp_B_step!==resume_initial_sp_B_step)$fatal(1,"resume changed constant sp_B_step");
   if(gpu.sp_Db_step!==resume_initial_sp_Db_step)$fatal(1,"resume changed constant sp_Db_step");
   if(gpu.sp_Dg_step!==resume_initial_sp_Dg_step)$fatal(1,"resume changed constant sp_Dg_step");
   if(gpu.sp_Dr_step!==resume_initial_sp_Dr_step)$fatal(1,"resume changed constant sp_Dr_step");
   if(gpu.sp_R_step!==resume_initial_sp_R_step)$fatal(1,"resume changed constant sp_R_step");
   if(gpu.sp_a6!==resume_initial_sp_a6)$fatal(1,"resume changed constant sp_a6");
   if(gpu.sp_blend!==resume_initial_sp_blend)$fatal(1,"resume changed constant sp_blend");
   if(gpu.sp_cd_combine!==resume_initial_sp_cd_combine)$fatal(1,"resume changed constant sp_cd_combine");
   if(gpu.sp_clamp_enable!==resume_initial_sp_clamp_enable)$fatal(1,"resume changed constant sp_clamp_enable");
   if(gpu.sp_colormap_id!==resume_initial_sp_colormap_id)$fatal(1,"resume changed constant sp_colormap_id");
   if(gpu.sp_const_alpha!==resume_initial_sp_const_alpha)$fatal(1,"resume changed constant sp_const_alpha");
   if(gpu.sp_fastpath!==resume_initial_sp_fastpath)$fatal(1,"resume changed constant sp_fastpath");
   if(gpu.sp_light_step!==resume_initial_sp_light_step)$fatal(1,"resume changed constant sp_light_step");
   if(gpu.sp_persp_q29_mode!==resume_initial_sp_persp_q29_mode)$fatal(1,"resume changed constant sp_persp_q29_mode");
   if(gpu.sp_q29_z_enable!==resume_initial_sp_q29_z_enable)$fatal(1,"resume changed constant sp_q29_z_enable");
   if(gpu.sp_rgb!==resume_initial_sp_rgb)$fatal(1,"resume changed constant sp_rgb");
   if(gpu.sp_s_max_mirrored!==resume_initial_sp_s_max_mirrored)$fatal(1,"resume changed constant sp_s_max_mirrored");
   if(gpu.sp_s_min_mirrored!==resume_initial_sp_s_min_mirrored)$fatal(1,"resume changed constant sp_s_min_mirrored");
   if(gpu.sp_t_max_mirrored!==resume_initial_sp_t_max_mirrored)$fatal(1,"resume changed constant sp_t_max_mirrored");
   if(gpu.sp_t_min_mirrored!==resume_initial_sp_t_min_mirrored)$fatal(1,"resume changed constant sp_t_min_mirrored");
   if(gpu.sp_tex_addr!==resume_initial_sp_tex_addr)$fatal(1,"resume changed constant sp_tex_addr");
   if(gpu.sp_tex_h_octave!==resume_initial_sp_tex_h_octave)$fatal(1,"resume changed constant sp_tex_h_octave");
   if(gpu.sp_tex_w_octave!==resume_initial_sp_tex_w_octave)$fatal(1,"resume changed constant sp_tex_w_octave");
   if(gpu.sp_tex_width!==resume_initial_sp_tex_width)$fatal(1,"resume changed constant sp_tex_width");
   if(gpu.sp_truecolor!==resume_initial_sp_truecolor)$fatal(1,"resume changed constant sp_truecolor");
   if(gpu.sp_z_step!==resume_initial_sp_z_step)$fatal(1,"resume changed constant sp_z_step");
   if(gpu.sp_z_test_enable!==resume_initial_sp_z_test_enable)$fatal(1,"resume changed constant sp_z_test_enable");
   if(gpu.sp_z_value_step!==resume_initial_sp_z_value_step)$fatal(1,"resume changed constant sp_z_value_step");
   if(gpu.sp_z_write_enable!==resume_initial_sp_z_write_enable)$fatal(1,"resume changed constant sp_z_write_enable");
   if(gpu.sp_zc_step!==resume_initial_sp_zc_step)$fatal(1,"resume changed constant sp_zc_step");
   if(gpu.sp_zinv_step!==resume_initial_sp_zinv_step)$fatal(1,"resume changed constant sp_zinv_step");
   if(gpu.sp_zinv_step_zero!==resume_initial_sp_zinv_step_zero)$fatal(1,"resume changed constant sp_zinv_step_zero");
  end
 end
end
final $display("RESUME_AUDIT checks=%0d affine=%0d perspective=%0d combine=%0d",resume_checks,resume_affine,resume_persp,resume_combine);

// Independent queue records are captured at original req/stage producer ports.
// They do not read the packed storage and never alter GPU state.
localparam REQUEST_ADDR_W=$bits(gpu.fbwq_req_addr);
reg [REQUEST_ADDR_W+35:0] queue_reference[0:15];
reg [15:0] queue_reference_valid=0;
reg queue_check_w=0,queue_check_aw=0,queue_reset_check=0;
reg [35:0] queue_expected_w;
reg [31:0] queue_expected_aw;
reg [63:0] queue_pushes=0,queue_reads=0,queue_aw_reads=0,queue_ahead=0;
reg [63:0] queue_partial=0,queue_w_stalls=0,queue_aw_stalls=0,queue_resets=0;
reg queue_stalled_w=0,queue_stalled_aw=0;
reg [36:0] queue_held_w;
reg [39:0] queue_held_aw;
integer qi;
always @(posedge clk) begin
 if(!reset_n||gpu.soft_reset)begin
  queue_reference_valid<=0;queue_check_w<=0;queue_check_aw<=0;
  queue_stalled_w<=0;queue_stalled_aw<=0;queue_reset_check<=1;
  queue_resets<=queue_resets+1;
 end else begin
  if(queue_reset_check&&(gpu.fbwq_count!=0||gpu.m_wr_wvalid||gpu.m_wr_awvalid))
   $fatal(1,"write queue reset ownership not empty");
  queue_reset_check<=0;
  if(queue_check_w&&{gpu.m_wr_wstrb,gpu.m_wr_wdata}!==queue_expected_w)
   $fatal(1,"packed write queue data/byte-strobe differs from producer");
  if(queue_check_aw&&gpu.m_wr_awaddr!==queue_expected_aw)
   $fatal(1,"common AW queue read differs from producer");
  if(queue_stalled_w&&(!gpu.m_wr_wvalid||{gpu.m_wr_wlast,gpu.m_wr_wstrb,gpu.m_wr_wdata}!==queue_held_w))
   $fatal(1,"W changed under backpressure");
  if(queue_stalled_aw&&(!gpu.m_wr_awvalid||{gpu.m_wr_awlen,gpu.m_wr_awaddr}!==queue_held_aw))
   $fatal(1,"AW changed under backpressure");
  queue_stalled_w<=gpu.m_wr_wvalid&&!gpu.m_wr_wready;
  queue_stalled_aw<=gpu.m_wr_awvalid&&!gpu.m_wr_awready;
  queue_held_w<={gpu.m_wr_wlast,gpu.m_wr_wstrb,gpu.m_wr_wdata};
  queue_held_aw<={gpu.m_wr_awlen,gpu.m_wr_awaddr};
  if(gpu.m_wr_wvalid&&!gpu.m_wr_wready)queue_w_stalls<=queue_w_stalls+1;
  if(gpu.m_wr_awvalid&&!gpu.m_wr_awready)queue_aw_stalls<=queue_aw_stalls+1;
  queue_check_w<=gpu.fbwq_start_now||gpu.fbwq_continue_now||gpu.fbwq_w_chain_start;
  queue_check_aw<=gpu.fbwq_start_now||gpu.fbwq_aw_ahead_issue;
  if(gpu.fbwq_start_now||gpu.fbwq_continue_now||gpu.fbwq_w_chain_start)begin
   if(!queue_reference_valid[gpu.fbwq_rd_ptr])$fatal(1,"queue read unwritten/reset slot");
   queue_expected_w<=queue_reference[gpu.fbwq_rd_ptr][35:0];
   queue_reference_valid[gpu.fbwq_rd_ptr]<=0;
   queue_reads<=queue_reads+1;
   if(queue_reference[gpu.fbwq_rd_ptr][35:32]!=15)queue_partial<=queue_partial+1;
  end
  if(gpu.fbwq_start_now||gpu.fbwq_aw_ahead_issue)begin
   if(!queue_reference_valid[gpu.fbwq_rd_ptr])$fatal(1,"AW read unwritten/reset slot");
   queue_expected_aw<={{(32-REQUEST_ADDR_W){1'b0}},queue_reference[gpu.fbwq_rd_ptr][REQUEST_ADDR_W+35:36]};
   queue_aw_reads<=queue_aw_reads+1;
   if(gpu.fbwq_aw_ahead_issue)queue_ahead<=queue_ahead+1;
  end
  if(gpu.fbwq_swap_now||gpu.fbwq_stage_drain_now)begin
   if(queue_reference_valid[gpu.fbwq_wr_ptr])$fatal(1,"queue overwrote owned slot");
   if(gpu.fbwq_swap_now)
    queue_reference[gpu.fbwq_wr_ptr]<={gpu.fbwq_req_addr,gpu.fbwq_req_strb,gpu.fbwq_req_data};
   else queue_reference[gpu.fbwq_wr_ptr]<={gpu.fbwq_stage_addr,gpu.fbwq_stage_strb,gpu.fbwq_stage_data};
   queue_reference_valid[gpu.fbwq_wr_ptr]<=1;
   queue_pushes<=queue_pushes+1;
  end
 end
end
final $display("QUEUE_AUDIT pushes=%0d reads=%0d aw=%0d ahead=%0d partial=%0d w_stall=%0d aw_stall=%0d resets=%0d",queue_pushes,queue_reads,queue_aw_reads,queue_ahead,queue_partial,queue_w_stalls,queue_aw_stalls,queue_resets);

// Snapshot the producer registers BEFORE the active edge. Inspect the selected
// request AFTER nonblocking assignments, on the inactive edge, avoiding races
// with the main FSM's blocking combinational temporaries.
reg request_pre_live=0,request_pre_valid=0,request_pre_can_push=0;
reg [REQUEST_ADDR_W+35:0] request_pre_payload;
reg [REQUEST_ADDR_W+35:0] request_sources[0:3];
reg [63:0] request_accepts=0,request_stalls=0,request_unowned=0;
reg [63:0] request_source_count[0:3];
integer request_i;
function [3:0] request_clear_mask(input [1:0] lane,input [15:0] remaining);
 integer n;
 begin
  n=4-lane;if(remaining<n)n=remaining;
  request_clear_mask=((16'h1<<n)-1)<<lane;
 end
endfunction
initial for(request_i=0;request_i<4;request_i=request_i+1)request_source_count[request_i]=0;
always @(posedge clk)begin
 request_pre_live<=reset_n&&!gpu.soft_reset;
 request_pre_valid<=gpu.fbwq_req_valid;
 request_pre_can_push<=gpu.fbwq_can_push;
 request_pre_payload<={gpu.fbwq_req_addr,gpu.fbwq_req_strb,gpu.fbwq_req_data};
 request_sources[0]<={gpu.z_flush_addr,gpu.z_flush_strb,gpu.z_flush_data};
 request_sources[1]<={gpu.z_acc_addr,gpu.z_acc_mask,gpu.z_acc_data};
 request_sources[2]<={gpu.fb_acc_addr,gpu.fb_acc_mask,gpu.fb_acc_data};
 request_sources[3]<={gpu.cr_addr[REQUEST_ADDR_W-1:2],2'b00,request_clear_mask(gpu.cr_addr[1:0],gpu.cr_w_remaining),
                      gpu.cr_color,gpu.cr_color,gpu.cr_color,gpu.cr_color};
end
always @(negedge clk)begin
 if(request_pre_live)begin
  if(request_pre_valid&&!request_pre_can_push)begin
   if(!gpu.fbwq_req_valid||{gpu.fbwq_req_addr,gpu.fbwq_req_strb,gpu.fbwq_req_data}!==request_pre_payload)
    $fatal(1,"owned request payload changed while stalled");
   request_stalls<=request_stalls+1;
  end
  if(gpu.main_fsm.fbwq_push_req)begin
   if(!request_pre_can_push)$fatal(1,"request producer accepted into an owned slot");
   if(!gpu.fbwq_req_valid||{gpu.fbwq_req_addr,gpu.fbwq_req_strb,gpu.fbwq_req_data}!==request_sources[gpu.fbwq_push_source])
    $fatal(1,"new request differs from selected pre-edge producer src=%0d valid=%0d can=%0d actual=%h expected=%h state=%0d",gpu.fbwq_push_source,gpu.fbwq_req_valid,request_pre_can_push,{gpu.fbwq_req_addr,gpu.fbwq_req_strb,gpu.fbwq_req_data},request_sources[gpu.fbwq_push_source],gpu.state);
   request_accepts<=request_accepts+1;
   request_source_count[gpu.fbwq_push_source]<=request_source_count[gpu.fbwq_push_source]+1;
  end else if(request_pre_can_push)begin
   if(gpu.fbwq_req_valid)$fatal(1,"unpublished payload acquired ownership");
   request_unowned<=request_unowned+1;
  end
 end
end
final $display("REQUEST_AUDIT accepts=%0d stalls=%0d unowned=%0d zflush=%0d zacc=%0d fbacc=%0d clear=%0d",
 request_accepts,request_stalls,request_unowned,request_source_count[0],request_source_count[1],request_source_count[2],request_source_count[3]);

reg xy_ready=0,xy_check_dsp=0;
reg [127:0] xy_expected_dsp;
reg [63:0] xy_prepares=0,xy_uses=0;
reg signed [15:0] xy_sx0,xy_sx1,xy_sx2,xy_sy0,xy_sy1,xy_sy2;
reg signed [16:0] xy_e1x,xy_e2x,xy_e1y,xy_e2y;
always @(posedge clk) begin
 if(!reset_n||gpu.soft_reset)begin xy_ready<=0;xy_check_dsp<=0;end
 else begin
  if(xy_check_dsp&&{gpu.dsp_a,gpu.dsp_b,gpu.dsp2_a,gpu.dsp2_b}!==xy_expected_dsp)
   $fatal(1,"registered XY changed determinant DSP launch");
  xy_check_dsp<=0;
  if(gpu.state==gpu.S_TRI_DERIVE&&gpu.dstate==gpu.DRV_PROD_L0)begin
   if(xy_ready)$fatal(1,"XY precompute overwritten before determinant");
   xy_ready<=1;xy_prepares<=xy_prepares+1;
  end
  if(gpu.state==gpu.S_TRI_DERIVE&&gpu.dstate==gpu.DRV_DELTA)begin
   if(!xy_ready)$fatal(1,"determinant consumed XY before precompute");
   xy_sx0=gpu.dvx[gpu.dv_ord[0]];xy_sx1=gpu.dvx[gpu.dv_ord[1]];xy_sx2=gpu.dvx[gpu.dv_ord[2]];
   xy_sy0=gpu.dvy[gpu.dv_ord[0]];xy_sy1=gpu.dvy[gpu.dv_ord[1]];xy_sy2=gpu.dvy[gpu.dv_ord[2]];
   xy_e1x={xy_sx1[15],xy_sx1}-{xy_sx0[15],xy_sx0};
   xy_e2x={xy_sx2[15],xy_sx2}-{xy_sx0[15],xy_sx0};
   xy_e1y={xy_sy1[15],xy_sy1}-{xy_sy0[15],xy_sy0};
   xy_e2y={xy_sy2[15],xy_sy2}-{xy_sy0[15],xy_sy0};
   if({gpu.dd1x,gpu.dd2x,gpu.dd1y,gpu.dd2y,gpu.dd_x0,gpu.dd_y0}!==
      {xy_e1x,xy_e2x,xy_e1y,xy_e2y,xy_sx0,xy_sy0})
    $fatal(1,"early XY differs from original live DELTA expression");
   xy_expected_dsp<={{{15{xy_e1x[16]}},xy_e1x},{{15{xy_e2y[16]}},xy_e2y},
                     {{15{xy_e2x[16]}},xy_e2x},{{15{xy_e1y[16]}},xy_e1y}};
   xy_ready<=0;xy_check_dsp<=1;xy_uses<=xy_uses+1;
  end
 end
end
reg zpre_live=0,zpre_fold=0,zpre_read=0,zpre_valid=0;
reg [REQUEST_ADDR_W-1:0] zpre_old,zpre_p2b,zpre_p3,zpre_pending,zexpected;
reg [63:0] zowned_checks=0,zfold_checks=0,zread_checks=0,zpending_checks=0,zunowned=0;
always @(posedge clk)begin
 zpre_live<=reset_n&&!gpu.soft_reset;
 zpre_valid<=gpu.z_acc_valid;
 zpre_old<=gpu.z_acc_addr;zpre_p2b<=gpu.p2b_z_word_addr_w;
 zpre_p3<={gpu.p3_z_addr[REQUEST_ADDR_W-1:2],2'b0};
 zpre_pending<=gpu.z_src_pending_addr;
 zpre_fold<=gpu.state==gpu.S_FRAG_PIPE&&!gpu.fp_pipe_stall&&
   gpu.p2b_zf_fold&&gpu.p2b_zf_pass&&gpu.p2b_z_write&&!gpu.p2b_zf_acc_hit;
 zpre_read<=gpu.state==gpu.S_FRAG_PIPE&&gpu.fbss==gpu.FBSS_ZTEST_ACC_EVAL&&
   gpu.p3_z_value>=gpu.ztest_acc_old_half&&gpu.p3_z_write&&gpu.ztest_acc_from_read;
end
always @(negedge clk)if(zpre_live)begin
 zexpected=zpre_old;
 if(zpre_fold)begin
  if(zpre_valid)$fatal(1,"window fold acquired a different owned Z address");
  zexpected=zpre_p2b;zfold_checks<=zfold_checks+1;
 end
 if(zpre_read)begin zexpected=zpre_p3;zread_checks<=zread_checks+1;end
 if(gpu.main_fsm.z_src_pending_applied)begin zexpected=zpre_pending;zpending_checks<=zpending_checks+1;end
 if(gpu.z_acc_valid)begin
  if(!zpre_valid&&!zpre_fold&&!zpre_read&&!gpu.main_fsm.z_src_pending_applied)
   $fatal(1,"Z address became owned without a legacy producer");
  if(gpu.z_acc_addr!==zexpected)$fatal(1,"owned Z address differs from legacy ordered writers");
  zowned_checks<=zowned_checks+1;
 end
 if(!zpre_valid)zunowned<=zunowned+1;
end
final $display("PATHS_AUDIT xy_prepares=%0d xy_uses=%0d zowned=%0d zfold=%0d zread=%0d zpending=%0d zunowned=%0d",
 xy_prepares,xy_uses,zowned_checks,zfold_checks,zread_checks,zpending_checks,zunowned);

reg [31:0] attribute_reference[0:2];
always @*begin
    case (gpu.dv_attr)
        3'd0: begin attribute_reference[0] = gpu.dv_szi[0]; attribute_reference[1] = gpu.dv_szi[1]; attribute_reference[2] = gpu.dv_szi[2]; end
        3'd1: begin attribute_reference[0] = gpu.dv_tzi[0]; attribute_reference[1] = gpu.dv_tzi[1]; attribute_reference[2] = gpu.dv_tzi[2]; end
        3'd2: begin attribute_reference[0] = gpu.vt_zi[0]; attribute_reference[1] = gpu.vt_zi[1]; attribute_reference[2] = gpu.vt_zi[2]; end
        // attr 4 = RED, attr 5 = BLUE (truecolor RGB; 5-bit -> Q*.16).  Only
        // reached when cmd_is_draw_vert_tri_rgb extends the derive loop past 3.
        3'd4: begin
            attribute_reference[0] = {11'd0, gpu.vt_rrow[0], 16'd0};
            attribute_reference[1] = {11'd0, gpu.vt_rrow[1], 16'd0};
            attribute_reference[2] = {11'd0, gpu.vt_rrow[2], 16'd0};
        end
        3'd5: begin
            attribute_reference[0] = {11'd0, gpu.vt_brow[0], 16'd0};
            attribute_reference[1] = {11'd0, gpu.vt_brow[1], 16'd0};
            attribute_reference[2] = {11'd0, gpu.vt_brow[2], 16'd0};
        end
        3'd6: begin      // attr 6 = decoupled depth (0x4E), full 32-bit
            attribute_reference[0] = gpu.vt_depth[0];
            attribute_reference[1] = gpu.vt_depth[1];
            attribute_reference[2] = gpu.vt_depth[2];
        end
        4'd7: begin      // attr 7 = D-red (combine), 5-bit -> Q*.16
            attribute_reference[0] = {11'd0, gpu.vt_Drrow[0], 16'd0};
            attribute_reference[1] = {11'd0, gpu.vt_Drrow[1], 16'd0};
            attribute_reference[2] = {11'd0, gpu.vt_Drrow[2], 16'd0};
        end
        4'd8: begin      // attr 8 = D-green (combine), 6-bit -> Q*.16
            attribute_reference[0] = {10'd0, gpu.vt_Dgrow[0], 16'd0};
            attribute_reference[1] = {10'd0, gpu.vt_Dgrow[1], 16'd0};
            attribute_reference[2] = {10'd0, gpu.vt_Dgrow[2], 16'd0};
        end
        4'd9: begin      // attr 9 = D-blue (combine), 5-bit -> Q*.16
            attribute_reference[0] = {11'd0, gpu.vt_Dbrow[0], 16'd0};
            attribute_reference[1] = {11'd0, gpu.vt_Dbrow[1], 16'd0};
            attribute_reference[2] = {11'd0, gpu.vt_Dbrow[2], 16'd0};
        end
        default: begin   // attr 3 = light (= green for RGB, in gpu.vt_lrow)
            attribute_reference[0] = {10'd0, gpu.vt_lrow[0], 16'd0};
            attribute_reference[1] = {10'd0, gpu.vt_lrow[1], 16'd0};
            attribute_reference[2] = {10'd0, gpu.vt_lrow[2], 16'd0};
        end
    endcase
end

reg clamp_owned=0,clamp_capture_check=0,clamp_result_check=0;
reg [31:0] clamp_old_zi,clamp_old_abs;
reg [29:0] clamp_expected_limit;
reg clamp_expected_result;
reg [63:0] clamp_captures=0,clamp_checks=0,clamp_q29=0,clamp_legacy=0;
reg [63:0] clamp_shrink_true=0,clamp_shrink_false=0,clamp_negative_new=0,clamp_zero_threshold=0;
reg [31:0] clamp_live_abs;
reg clamp_reference_shrink,clamp_reference_cross;
reg attr_owned=0;
reg [63:0] attr_owned_cycles=0,attr_captures=0,attr_origins=0;
reg [9:0] attr_seen=0;
integer attr_k;
always @(posedge clk)begin
 if(!reset_n||gpu.soft_reset)begin
  clamp_owned<=0;clamp_capture_check<=0;clamp_result_check<=0;attr_owned<=0;
 end else begin
  if(clamp_capture_check&&gpu.pss_zinv_shrink_limit_r!==clamp_expected_limit)
   $fatal(1,"clamp captured threshold differs from legacy unsigned abs");
  if(clamp_result_check&&gpu.pss_zinv_clamp_r!==clamp_expected_result)
   $fatal(1,"clamp output differs from legacy owned comparison");
  clamp_capture_check<=0;clamp_result_check<=0;
  if(!gpu.persp_active)clamp_owned<=0;
  if(gpu.state==gpu.S_FRAG_PIPE&&gpu.persp_active&&gpu.persp_pss==gpu.PSS_ADV)begin
   clamp_old_zi<=gpu.sp_zinv;
   clamp_old_abs<=gpu.sp_zinv[31]?-gpu.sp_zinv:gpu.sp_zinv;
   clamp_expected_limit<=((gpu.sp_zinv[31]?-gpu.sp_zinv:gpu.sp_zinv)>>2)-32'd1;
   clamp_owned<=1;clamp_capture_check<=1;clamp_captures<=clamp_captures+1;
  end
  if(gpu.state==gpu.S_FRAG_PIPE&&gpu.persp_active&&gpu.persp_pss==gpu.PSS_ADV_CLAMP)begin
   if(!clamp_owned)$fatal(1,"clamp used reset/unowned threshold");
   clamp_live_abs=gpu.sp_zinv[31]?-gpu.sp_zinv:gpu.sp_zinv;
   clamp_reference_shrink=clamp_live_abs<(clamp_old_abs>>2);
   clamp_reference_cross=(gpu.sp_zinv[31]^clamp_old_zi[31])&&(clamp_old_zi!=0)&&(gpu.sp_zinv!=0);
   if(gpu.pss_zinv_shrunk!==clamp_reference_shrink)$fatal(1,"live shrink differs");
   if(gpu.pss_zinv_prev_negative!==clamp_old_zi[31]||gpu.pss_zinv_prev_nonzero!==(clamp_old_zi!=0))
    $fatal(1,"clamp old-sign capture differs");
   clamp_expected_result<=clamp_reference_cross||(gpu.pss_use_q29?(clamp_live_abs==0):clamp_reference_shrink);
   clamp_result_check<=1;clamp_checks<=clamp_checks+1;
   if(gpu.pss_use_q29)clamp_q29<=clamp_q29+1;else clamp_legacy<=clamp_legacy+1;
   if(clamp_reference_shrink)clamp_shrink_true<=clamp_shrink_true+1;else clamp_shrink_false<=clamp_shrink_false+1;
   if(gpu.sp_zinv[31])clamp_negative_new<=clamp_negative_new+1;
   if((clamp_old_abs>>2)==0)clamp_zero_threshold<=clamp_zero_threshold+1;
  end
  if(gpu.state!=gpu.S_TRI_DERIVE||gpu.dstate==gpu.DRV_SORT_A)attr_owned<=0;
  if(gpu.state==gpu.S_TRI_DERIVE&&gpu.dstate==gpu.DRV_RDET&&gpu.rdet_cnt==1)attr_owned<=1;
  if(gpu.state==gpu.S_TRI_DERIVE&&attr_owned)begin
   if(gpu.dv_attr>9||gpu.dv_attr_onehot!==(10'b1<<gpu.dv_attr))$fatal(1,"owned attribute onehot/counter mismatch");
   for(attr_k=0;attr_k<3;attr_k=attr_k+1)
    if(gpu.deriv_attr[attr_k]!==attribute_reference[attr_k])$fatal(1,"owned literal attribute mux differs");
   attr_owned_cycles<=attr_owned_cycles+1;attr_seen<=attr_seen|gpu.dv_attr_onehot;
  end
  if(gpu.state==gpu.S_TRI_DERIVE&&gpu.dstate==gpu.DRV_DA_SEL)begin
   if(!attr_owned)$fatal(1,"attribute captured without initialization");
   if(gpu.deriv_a0!==attribute_reference[gpu.dv_ord[0]]||gpu.deriv_a1!==attribute_reference[gpu.dv_ord[1]]||gpu.deriv_a2!==attribute_reference[gpu.dv_ord[2]])
    $fatal(1,"sorted owned attribute value differs");
   attr_captures<=attr_captures+1;
  end
  if(gpu.state==gpu.S_TRI_DERIVE&&(gpu.dstate==gpu.DRV_ORG_CAP||gpu.dstate==gpu.DRV_ORG_FORM))begin
   if(!attr_owned)$fatal(1,"attribute origin used before ownership");attr_origins<=attr_origins+1;
  end
 end
end
final $display("CLAMP_ATTRIBUTE_AUDIT captures=%0d clamps=%0d q29=%0d legacy=%0d shrink_true=%0d shrink_false=%0d negative_new=%0d zero_threshold=%0d attr_cycles=%0d attr_captures=%0d attr_origins=%0d attr_seen=%0d",
 clamp_captures,clamp_checks,clamp_q29,clamp_legacy,clamp_shrink_true,clamp_shrink_false,clamp_negative_new,clamp_zero_threshold,attr_owned_cycles,attr_captures,attr_origins,attr_seen);

// Literal original four assignment sites collapsed only for this independent
// reference. It reads pre-edge original inputs, not the new write-data wire.
reg[31:0] recip_original_next,recip_expected;
reg recip_original_we,recip_checked=0;
reg[5:0] recip_shamt;
reg[44:0] recip_funnel;
reg signed[63:0] recip_shifted;
always @*begin
 recip_original_next=gpu.recip_q16_r;
 recip_original_we=0;recip_shamt=0;recip_funnel=0;recip_shifted=0;
 if(reset_n&&!gpu.soft_reset&&gpu.state==gpu.S_FRAG_PIPE&&gpu.persp_active)begin
case (gpu.persp_pss)
 gpu.PSS_TOP8: if(gpu.pss_use_q29 && gpu.pss_recip_cache_hit) begin
  recip_original_we=1; recip_original_next=$signed(gpu.pss_recip_cache_value);
 end
 gpu.PSS_RECIP_SHIFT: begin
  recip_original_we=1;
                    recip_shamt  = gpu.pss_use_q29 ? gpu.pss_q29_recip_shift
                                                     : {1'b0, gpu.persp_clz};
                    recip_funnel = {29'd0, gpu.recip_rd_data} << recip_shamt;
                    recip_original_next = $signed(recip_funnel[44:13]);
 end
 gpu.PSS_NR_CAPTURE:begin
  recip_original_we=1;
                    if (gpu.pss_use_q29) begin
                        recip_shifted = gpu.dsp_p >>> 16;
                        recip_original_next = recip_shifted[31:0];
                    end else begin
                        recip_original_next = gpu.dsp_p[47:16]
                                     + ((gpu.sp_zinv_step_zero
                                      && gpu.sp_zinv != 32'sh00010000) ? 32'sd1
                                                                   : 32'sd0);
                    end
 end
 default:begin end
endcase
 end
end
reg[63:0] recip_cycles=0,recip_holds=0,recip_seed=0,recip_nr=0,recip_hits=0;
reg[63:0] recip_bias=0,recip_q29=0,recip_consumers=0,recip_clamps=0,recip_resets=0;
always @(posedge clk)begin
 recip_expected<=recip_original_next;
 recip_checked<=1;
 recip_cycles<=recip_cycles+1;
 if(recip_original_we !== (reset_n&&!gpu.soft_reset&&gpu.state==gpu.S_FRAG_PIPE&&gpu.persp_active&&gpu.recip_q16_write_enable))
  $fatal(1,"original reciprocal write enable differs");
 if(!reset_n||gpu.soft_reset)recip_resets<=recip_resets+1;
 if(!recip_original_we)recip_holds<=recip_holds+1;
 else begin
  if(gpu.persp_pss==gpu.PSS_RECIP_SHIFT)recip_seed<=recip_seed+1;
  if(gpu.persp_pss==gpu.PSS_NR_CAPTURE)begin
   recip_nr<=recip_nr+1;
   if(!gpu.pss_use_q29&&gpu.sp_zinv_step_zero&&gpu.sp_zinv!=32'sh10000)recip_bias<=recip_bias+1;
  end
  if(gpu.persp_pss==gpu.PSS_TOP8)recip_hits<=recip_hits+1;
  if(gpu.pss_use_q29)recip_q29<=recip_q29+1;
 end
 if(reset_n&&!gpu.soft_reset&&gpu.state==gpu.S_FRAG_PIPE&&gpu.persp_active)begin
  if(gpu.persp_pss==gpu.PSS_NR_MUL_X||gpu.persp_pss==gpu.PSS_NR_MUL_Y||gpu.persp_pss==gpu.PSS_MUL||gpu.persp_pss==gpu.PSS_FINAL)
   recip_consumers<=recip_consumers+1;
  if(gpu.pss_zinv_clamp_r)recip_clamps<=recip_clamps+1;
 end
end
always @(negedge clk)if(recip_checked&&gpu.recip_q16_r!==recip_expected)
 $fatal(1,"original reciprocal next-state differs after edge");
final $display("RECIP_WRITE_AUDIT cycles=%0d holds=%0d seed=%0d nr=%0d hits=%0d bias=%0d q29=%0d consumers=%0d clamps=%0d resets=%0d",
 recip_cycles,recip_holds,recip_seed,recip_nr,recip_hits,recip_bias,recip_q29,recip_consumers,recip_clamps,recip_resets);

reg owner_checked=0;
reg[63:0] owner_cycles=0,owner_live_la=0,owner_ready=0,owner_handoffs=0;
reg[63:0] owner_hard_resets=0,owner_soft_resets=0,owner_flush=0,owner_pss_writes=0;
always @(posedge clk)begin
 owner_checked<=1;
 owner_cycles<=owner_cycles+1;
 if(!reset_n)owner_hard_resets<=owner_hard_resets+1;
 else begin
  if(gpu.soft_reset)owner_soft_resets<=owner_soft_resets+1;
  if(gpu.span_la!=gpu.LA_IDLE)begin
   if(!gpu.cmd_is_tri_walker)$fatal(1,"lookahead active without original tri command owner");
   owner_live_la<=owner_live_la+1;
  end
  if(gpu.span_la==gpu.LA_READY)owner_ready<=owner_ready+1;
  if(gpu.state==gpu.S_FRAG_PIPE&&gpu.span_la==gpu.LA_READY&&gpu.span_la_eligible
     &&gpu.src_done&&!gpu.p0a_valid&&!gpu.p0_valid&&!gpu.persp_active
     &&!gpu.persp_swap_pending&&gpu.persp_pss==gpu.PSS_IDLE)begin
   if(!gpu.cmd_is_tri_walker)$fatal(1,"new handoff differs from original owner gate");
   owner_handoffs<=owner_handoffs+1;
  end
  if(gpu.state==gpu.S_FB_FLUSH)owner_flush<=owner_flush+1;
  if(gpu.state==gpu.S_FRAG_PIPE&&gpu.persp_active&&gpu.pss_dsp_a_write)
   owner_pss_writes<=owner_pss_writes+1;
 end
end

final $display("CONTROL_OWNER_AUDIT cycles=%0d live_la=%0d ready=%0d handoffs=%0d hard_resets=%0d soft_resets=%0d flush=%0d pss_writes=%0d",
 owner_cycles,owner_live_la,owner_ready,owner_handoffs,owner_hard_resets,owner_soft_resets,owner_flush,owner_pss_writes);


reg[63:0] finality_cycles=0,finality_true=0,finality_ahead=0,finality_gather=0;
wire finality_reference=(gpu.fbwq_start_burst_words==4'd8)||
 (gpu.fbwq_count>{1'b0,gpu.fbwq_start_burst_words});
always @(posedge clk)if(reset_n&&!gpu.soft_reset)begin
 if(gpu.fbwq_head_chain_final!==finality_reference)$fatal(1,"live queue finality differs");
 if(gpu.fbwq_count>16)$fatal(1,"queue count outside capacity");
 finality_cycles<=finality_cycles+1;
 if(finality_reference)finality_true<=finality_true+1;
 if(gpu.fbwq_aw_ahead_issue)finality_ahead<=finality_ahead+1;
 if(gpu.fbwq_gather_prefix)finality_gather<=finality_gather+1;
end
final $display("FINALITY_AUDIT cycles=%0d final=%0d ahead=%0d gather=%0d",
 finality_cycles,finality_true,finality_ahead,finality_gather);

reg step_checked=0;
reg[63:0] step_cycles=0,step_advances=0,step_swaps=0,step_src_done=0;
always @(posedge clk)begin
 step_checked<=1;step_cycles<=step_cycles+1;
 if(reset_n && !gpu.soft_reset && gpu.state==gpu.S_FRAG_PIPE)begin
  if(gpu.span_step_advance_early && !gpu.src_done)step_advances<=step_advances+1;
  if(gpu.persp_swap_pending)step_swaps<=step_swaps+1;
  if(gpu.src_done)step_src_done<=step_src_done+1;
 end
end

final $display("SPAN_STEP_AUDIT cycles=%0d advances=%0d swaps=%0d src_done=%0d",step_cycles,step_advances,step_swaps,step_src_done);

reg [63:0] endpoint_blend_checks=0;
wire [31:0] endpoint_blend_mask={{8{gpu.fb_acc_mask[3]}},{8{gpu.fb_acc_mask[2]}},{8{gpu.fb_acc_mask[1]}},{8{gpu.fb_acc_mask[0]}}};
wire [31:0] endpoint_blend_old_word=gpu.cbw_p2_acc_same_w
 ? ((gpu.cbw_p2_word_w & ~endpoint_blend_mask) | (gpu.fb_acc_data & endpoint_blend_mask))
 : gpu.cbw_p2_word_w;
wire [15:0] endpoint_blend_old=gpu.p2_fb_addr[1] ? endpoint_blend_old_word[31:16] : endpoint_blend_old_word[15:0];
always @(posedge clk)if(reset_n&&!gpu.soft_reset)begin
 if(gpu.cbw_p2_dst_w!==endpoint_blend_old)$fatal(1,"blend halfword overlay differs");
 endpoint_blend_checks<=endpoint_blend_checks+1;
end
final $display("ENDPOINT_BLEND_AUDIT checks=%0d",endpoint_blend_checks);

endmodule
`default_nettype wire
