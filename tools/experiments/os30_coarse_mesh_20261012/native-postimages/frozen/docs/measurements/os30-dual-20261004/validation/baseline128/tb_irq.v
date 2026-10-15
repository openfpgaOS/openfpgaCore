//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

//
// tb_system_sdram.v — tb_system with the REAL MiSTer SDRAM back-end.
//
// Same top-level as tb_system.v (module name kept: tb_system) except the
// idealized sdram_fast_model is replaced by the real chain:
//     axi_sdram_slave → pulse adapter → io_sdram (mister copy, DQ-split)
//         → sdram_model_full (cycle-accurate chip, refresh-enforced)
// plus a scanout burst_rd injection port at io_sdram (the MiSTer video
// scanout enters BELOW the AXI fabric on hardware) and observation taps
// for a C++ R-beat data checker.  Purpose: reproduce the MiSTer
// kernel-layout boot lottery (suspected i-fetch corruption/hang under
// concurrent scanout) in simulation.
//
// Brings up:
//   - OpenFpgaVexii CPU            (clk_cpu)
//   - cpu_system 3-master router   (clk_cpu)
//   - axi_sdram_arbiter + slave    (clk_cpu)
//   - sdram_fast_model             (clk_cpu)
//   - cram0_cdc (s_axi → word-if)  (clk_cpu ↔ clk_74a)
//   - cram0_sim_model              (clk_74a)
//   - axi_periph_slave             (clk_cpu)
//       └── altsyncram (BRAM, 32KB) via altsyncram stub (bram_model.v)
//
// The mux from core_top.v between bridge-native and CPU-native word
// interfaces is replicated below; the bridge side exposes a backdoor so
// the C++ harness can preload os.bin into CRAM0 before reset is
// released.  Once reset_n deasserts, the mux tracks cram0_mode exactly
// as it does on hardware.
//
// Outputs surfaced to the C++ harness:
//   uart_tx_dv    — 1-cycle pulse when axi_periph_slave accepts a byte
//   uart_tx_byte  — the accepted byte
//   dbg_pc        — CPU fetch program counter hoist (best-effort)
//

`default_nettype none

`timescale 1ns/1ps
module tb_system (

    input wire cpu_run,
    input wire inj_audio_valid,
    output wire obs_audio_ready, obs_audio_rvalid, obs_audio_rlast,
    output wire [31:0] obs_audio_rdata,
    input wire bd_gpu_wr, input wire [3:0] bd_gpu_addr,
    input wire [31:0] bd_gpu_data,
    input wire bd_geo_wr, input wire [9:0] bd_geo_addr,
    input wire [31:0] bd_geo_data,
    output wire [31:0] obs_gpu_rdata,
    output wire obs_gpu_busy, obs_bcr_ready,
    output wire [31:0] obs_fence,
    output wire obs_retire, output wire [31:0] obs_retire_pc, obs_retire_uop,
    output wire obs_freeze, obs_hazard,
    output wire obs_upload, output wire [31:0] obs_upload_word,
    output wire obs_gpu_mmio, output wire [3:0] obs_gpu_mmio_addr,
    output wire [31:0] obs_gpu_mmio_data,
    output wire obs_geo_mmio, output wire [9:0] obs_geo_mmio_addr,
    output wire [31:0] obs_geo_mmio_data,
    output wire [15:0] obs_cram_errors,

output wire dbg_test_reset,
output wire[31:0]dbg_native_uploads,dbg_native_loads,dbg_native_alias,dbg_native_stalls,dbg_native_texture,dbg_native_resets,dbg_native_geo_wait,
    input  wire        clk_cpu,
    input  wire        clk_74a,
    input  wire        reset_n,
    // Vsync — driven by C++ harness as a periodic 1-cycle pulse so the
    // periph slave's fb_swap_pending actually clears the way it does
    // on real hardware.
    input  wire        vsync,

    // UART TX observation — driven by axi_periph_slave
    output wire        uart_tx_dv,
    output wire [7:0]  uart_tx_byte,

    // CRAM0 backdoor (bridge-native side of the mux).  The harness uses
    // this to preload os.bin before releasing reset_n.
    input  wire        bd_cram0_wr,
    input  wire [21:0] bd_cram0_addr,
    input  wire [31:0] bd_cram0_wdata,

    // SDRAM chip-model backdoor: clocked preload writes + combinational
    // live peek (the C++ checker compares every delivered R beat against
    // the model's current content through this port).
    input  wire        bd_sdram_we,
    input  wire [23:0] bd_sdram_word_addr,
    input  wire [31:0] bd_sdram_wdata,
    input  wire [23:0] bd_sdram_rd_word_addr,
    output wire [31:0] bd_sdram_rd_data,

    // Scanout burst injection — drives io_sdram's burst_rd port at the
    // real video cadence (C++-driven).  burst_addr is a HALFWORD address,
    // burst_len counts 32-bit words (burst_32bit=1).
    input  wire        inj_burst_rd,
    input  wire [24:0] inj_burst_addr,
    input  wire [10:0] inj_burst_len,
    output wire [31:0] inj_burst_data,
    output wire        inj_burst_data_valid,
    output wire        inj_burst_data_done,

    // M1 (CPU→arbiter) AXI observation taps for the R-beat checker
    output wire        dbg_m1_arvalid,
    output wire        dbg_m1_arready,
    output wire [31:0] dbg_m1_araddr,
    output wire [7:0]  dbg_m1_arlen,
    output wire        dbg_m1_rvalid,
    output wire        dbg_m1_rready,
    output wire [31:0] dbg_m1_rdata,
    output wire        dbg_m1_rlast,
    output wire        dbg_m1_awvalid,
    output wire        dbg_m1_awready,
    output wire [31:0] dbg_m1_awaddr,

    // Chip-model write-beat events (harness written-address bitmap)
    output wire        dbg_model_wr_evt,
    output wire [24:0] dbg_model_wr_hw_addr,
    output wire [1:0]  dbg_model_wr_dqm,

    // CPU-boundary per-master taps (i/mem/per as the CPU sees them) —
    // catch response mis-routing that is invisible at M1 (data valid for
    // the address presented downstream but delivered to the wrong master
    // or wrong refill slot).
    output wire        dbg_i_arvalid,
    output wire        dbg_i_arready,
    output wire [31:0] dbg_i_araddr,
    output wire        dbg_i_arid,
    output wire [7:0]  dbg_i_arlen,
    output wire        dbg_i_rvalid,
    output wire        dbg_i_rready,
    output wire [31:0] dbg_i_rdata,
    output wire        dbg_i_rid,
    output wire        dbg_i_rlast,
    output wire        dbg_mem_arvalid,
    output wire        dbg_mem_arready,
    output wire [31:0] dbg_mem_araddr,
    output wire [1:0]  dbg_mem_arid,
    output wire [7:0]  dbg_mem_arlen,
    output wire        dbg_mem_rvalid,
    output wire        dbg_mem_rready,
    output wire [31:0] dbg_mem_rdata,
    output wire [1:0]  dbg_mem_rid,
    output wire        dbg_mem_rlast,
    output wire        dbg_per_arvalid,
    output wire        dbg_per_arready,
    output wire [31:0] dbg_per_araddr,
    output wire [7:0]  dbg_per_arlen,
    output wire        dbg_per_rvalid,
    output wire        dbg_per_rready,
    output wire [31:0] dbg_per_rdata,
    output wire        dbg_per_rlast,
    output wire        dbg_slave_rd,
    output wire        dbg_slave_wr,

    // Arbiter→slave write-channel taps + chip write-beat data (ordered
    // write-stream conformance checking in the harness)
    output wire        dbg_aw_valid,
    output wire        dbg_aw_ready,
    output wire [31:0] dbg_aw_addr,
    output wire [7:0]  dbg_aw_len,
    output wire        dbg_w_valid,
    output wire        dbg_w_ready,
    output wire [31:0] dbg_w_data,
    output wire [3:0]  dbg_w_strb,
    output wire        dbg_w_last,
    output wire        dbg_w_cont,
    output wire [15:0] dbg_model_wr_data,

    // CPU debug hoists
    output wire [31:0] dbg_periph_araddr,
    output wire        dbg_periph_arvalid,
    output wire [31:0] dbg_sdram_araddr,
    output wire        dbg_sdram_arvalid,
    output wire [31:0] dbg_cram0_araddr,
    output wire        dbg_cram0_arvalid,
    output wire [31:0] dbg_periph_awaddr,
    output wire        dbg_periph_awvalid,
    output wire [31:0] dbg_sdram_awaddr,
    output wire        dbg_sdram_awvalid,
    output wire [2:0]  dbg_periph_state,
    output wire        dbg_cram0_mode,
    output wire [3:0]  dbg_sdram_slave_state,
    output wire [3:0]  dbg_sdram_model_state,
    output wire        dbg_sdram_model_busy,
    output wire        dbg_arb_grant_cpu,
    output wire [1:0]  dbg_arb_state,
    output wire        dbg_cmd_forwarded,
    output wire        dbg_accepted,
    output wire        dbg_word_rd_sd,
    output wire        dbg_word_wr_sd,
    output wire [23:0] dbg_word_addr_sd,
    output wire [3:0]  dbg_word_burst_len_sd,
    output wire        dbg_word_q_valid_sd
);

// ============================================================
// CPU master → cpu_system busses
// ============================================================
wire        cpu_sdram_arvalid, cpu_sdram_arready;
wire [31:0] cpu_sdram_araddr;
wire [7:0]  cpu_sdram_arlen;
wire        cpu_sdram_rvalid,  cpu_sdram_rready;
wire [31:0] cpu_sdram_rdata;
wire [1:0]  cpu_sdram_rresp;
wire        cpu_sdram_rlast;
wire        cpu_sdram_awvalid, cpu_sdram_awready;
wire [31:0] cpu_sdram_awaddr;
wire [7:0]  cpu_sdram_awlen;
wire        cpu_sdram_wvalid,  cpu_sdram_wready;
wire [31:0] cpu_sdram_wdata;
wire [3:0]  cpu_sdram_wstrb;
wire        cpu_sdram_wlast;
wire        cpu_sdram_bvalid;
wire [1:0]  cpu_sdram_bresp;

wire        cpu_cram0_arvalid, cpu_cram0_arready;
wire [31:0] cpu_cram0_araddr;
wire [7:0]  cpu_cram0_arlen;
wire        cpu_cram0_rvalid,  cpu_cram0_rready;
wire [31:0] cpu_cram0_rdata;
wire [1:0]  cpu_cram0_rresp;
wire        cpu_cram0_rlast;
wire        cpu_cram0_awvalid, cpu_cram0_awready;
wire [31:0] cpu_cram0_awaddr;
wire [7:0]  cpu_cram0_awlen;
wire        cpu_cram0_wvalid,  cpu_cram0_wready;
wire [31:0] cpu_cram0_wdata;
wire [3:0]  cpu_cram0_wstrb;
wire        cpu_cram0_wlast;
wire        cpu_cram0_bvalid;
wire [1:0]  cpu_cram0_bresp;

wire        cpu_local_arvalid, cpu_local_arready;
wire [31:0] cpu_local_araddr;
wire [7:0]  cpu_local_arlen;
wire        cpu_local_rvalid,  cpu_local_rready;
wire [31:0] cpu_local_rdata;
wire [1:0]  cpu_local_rresp;
wire        cpu_local_rlast;
wire        cpu_local_awvalid, cpu_local_awready;
wire [31:0] cpu_local_awaddr;
wire [7:0]  cpu_local_awlen;
wire [1:0]  cpu_local_awburst;
wire        cpu_local_wvalid,  cpu_local_wready;
wire [31:0] cpu_local_wdata;
wire [3:0]  cpu_local_wstrb;
wire        cpu_local_wlast;
wire        cpu_local_bvalid;
wire [1:0]  cpu_local_bresp;

// ============================================================
// cpu_system — 3-master CPU router
// ============================================================
wire test_timer_irq;
cpu_system cpu_sys (
    .clk    (clk_cpu),
    .reset_n(fixture_cpu_reset_n),

    // SDRAM master
    .m_sdram_arvalid(cpu_sdram_arvalid), .m_sdram_arready(cpu_sdram_arready),
    .m_sdram_araddr (cpu_sdram_araddr),  .m_sdram_arlen  (cpu_sdram_arlen),
    .m_sdram_rvalid (cpu_sdram_rvalid),  .m_sdram_rready (cpu_sdram_rready),
    .m_sdram_rdata  (cpu_sdram_rdata),   .m_sdram_rresp  (cpu_sdram_rresp),
    .m_sdram_rlast  (cpu_sdram_rlast),
    .m_sdram_awvalid(cpu_sdram_awvalid), .m_sdram_awready(cpu_sdram_awready),
    .m_sdram_awaddr (cpu_sdram_awaddr),  .m_sdram_awlen  (cpu_sdram_awlen),
    .m_sdram_wvalid (cpu_sdram_wvalid),  .m_sdram_wready (cpu_sdram_wready),
    .m_sdram_wdata  (cpu_sdram_wdata),   .m_sdram_wstrb  (cpu_sdram_wstrb),
    .m_sdram_wlast  (cpu_sdram_wlast),
    .m_sdram_bvalid (cpu_sdram_bvalid),  .m_sdram_bresp  (cpu_sdram_bresp),

    // CRAM0 master
    .m_cram0_arvalid(cpu_cram0_arvalid), .m_cram0_arready(cpu_cram0_arready),
    .m_cram0_araddr (cpu_cram0_araddr),  .m_cram0_arlen  (cpu_cram0_arlen),
    .m_cram0_rvalid (cpu_cram0_rvalid),  .m_cram0_rready (cpu_cram0_rready),
    .m_cram0_rdata  (cpu_cram0_rdata),   .m_cram0_rresp  (cpu_cram0_rresp),
    .m_cram0_rlast  (cpu_cram0_rlast),
    .m_cram0_awvalid(cpu_cram0_awvalid), .m_cram0_awready(cpu_cram0_awready),
    .m_cram0_awaddr (cpu_cram0_awaddr),  .m_cram0_awlen  (cpu_cram0_awlen),
    .m_cram0_wvalid (cpu_cram0_wvalid),  .m_cram0_wready (cpu_cram0_wready),
    .m_cram0_wdata  (cpu_cram0_wdata),   .m_cram0_wstrb  (cpu_cram0_wstrb),
    .m_cram0_wlast  (cpu_cram0_wlast),
    .m_cram0_bvalid (cpu_cram0_bvalid),  .m_cram0_bresp  (cpu_cram0_bresp),

    // Local master
    .m_local_arvalid(cpu_local_arvalid), .m_local_arready(cpu_local_arready),
    .m_local_araddr (cpu_local_araddr),  .m_local_arlen  (cpu_local_arlen),
    .m_local_rvalid (cpu_local_rvalid),  .m_local_rready (cpu_local_rready),
    .m_local_rdata  (cpu_local_rdata),   .m_local_rresp  (cpu_local_rresp),
    .m_local_rlast  (cpu_local_rlast),
    .m_local_awvalid(cpu_local_awvalid), .m_local_awready(cpu_local_awready),
    .m_local_awaddr (cpu_local_awaddr),  .m_local_awlen  (cpu_local_awlen),
    .m_local_awburst(cpu_local_awburst),
    .m_local_wvalid (cpu_local_wvalid),  .m_local_wready (cpu_local_wready),
    .m_local_wdata  (cpu_local_wdata),   .m_local_wstrb  (cpu_local_wstrb),
    .m_local_wlast  (cpu_local_wlast),
    .m_local_bvalid (cpu_local_bvalid),  .m_local_bresp  (cpu_local_bresp),

    .int_m_external(1'b0),
    .int_m_timer   (test_timer_irq)
);

// ============================================================
// SDRAM arbiter + slave + sim model
// ============================================================
// GPU and audio masters are tied off: only the CPU port goes live.
wire        arb_arvalid, arb_arready;
wire [31:0] arb_araddr;
wire [7:0]  arb_arlen;
wire        arb_rvalid,  arb_rready;
wire [31:0] arb_rdata;
wire [1:0]  arb_rresp;
wire        arb_rlast;
wire        arb_awvalid, arb_awready;
wire [31:0] arb_awaddr;
wire [7:0]  arb_awlen;
wire        arb_wvalid,  arb_wready;
wire [31:0] arb_wdata;
wire [3:0]  arb_wstrb;
wire        arb_wlast;
wire        arb_bvalid;
wire [1:0]  arb_bresp;
wire        arb_wcont;

// ─── GPU bus signals (live: gpu_core instance below) ───
wire        gpu_rd_arvalid, gpu_rd_arready;
wire [31:0] gpu_rd_araddr;
wire [7:0]  gpu_rd_arlen;
wire        gpu_rd_rvalid;
wire [31:0] gpu_rd_rdata;
wire [1:0]  gpu_rd_rresp;
wire        gpu_rd_rlast;
wire        gpu_wr_awvalid, gpu_wr_awready;
wire [31:0] gpu_wr_awaddr;
wire [7:0]  gpu_wr_awlen;
wire        gpu_wr_wvalid,  gpu_wr_wready;
wire [31:0] gpu_wr_wdata;
wire [3:0]  gpu_wr_wstrb;
wire        gpu_wr_wlast;
wire        gpu_wr_bvalid;
wire [1:0]  gpu_wr_bresp;
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
wire        gpu_swap_req;
wire [1:0]  gpu_swap_idx;
wire        slave_swap_pending;
wire [31:0] gpu_reg_rdata;
wire        gpu_reg_wr;
wire [3:0]  gpu_reg_addr;
wire [31:0] gpu_reg_wdata;
wire        bridge_awready_unused;
wire        bridge_wready_unused;
wire        bridge_bvalid_unused;
wire [1:0]  bridge_bresp_unused;


// Exact LOD5 alias shim; fixed synthetic completed result rows.
wire alias_normal_arvalid;
wire alias_normal_arready;
wire [31:0] alias_normal_araddr;
wire [7:0] alias_normal_arlen;
wire alias_normal_rvalid;
wire alias_normal_rready;
wire [31:0] alias_normal_rdata;
wire [1:0] alias_normal_rresp;
wire alias_normal_rlast;
wire alias_normal_awvalid;
wire alias_normal_awready;
wire [31:0] alias_normal_awaddr;
wire [7:0] alias_normal_awlen;
wire alias_normal_wvalid;
wire alias_normal_wready;
wire [31:0] alias_normal_wdata;
wire [3:0] alias_normal_wstrb;
wire alias_normal_wlast;
wire alias_normal_bvalid;
wire [1:0] alias_normal_bresp;
wire[7:0]alias_view_addr;wire[31:0]alias_view_data;


// Reset-held initialization is excluded from measurement.
wire fixture_cpu_reset_n=reset_n&&cpu_run;
assign dbg_test_reset=!fixture_cpu_reset_n;
wire alias_hold=1'b0;
wire alias_raw_rvalid;
assign cpu_sdram_rvalid=alias_raw_rvalid;
assign dbg_native_uploads=native_upload_words;
assign dbg_native_loads=native_load_bursts;
assign dbg_native_alias=native_alias_beats;
assign dbg_native_stalls=native_alias_stalls;
assign dbg_native_texture=0;
assign dbg_native_resets=0;
assign dbg_native_geo_wait=0;
wire gpu_mmio_wr=cpu_run?gpu_reg_wr:bd_gpu_wr;
wire [3:0] gpu_mmio_addr=cpu_run?gpu_reg_addr:bd_gpu_addr;
wire [31:0] gpu_mmio_data=cpu_run?gpu_reg_wdata:bd_gpu_data;
wire geo_mmio_wr=cpu_run?geometry_reg_wr:bd_geo_wr;
wire [9:0] geo_mmio_addr=cpu_run?geometry_reg_addr:bd_geo_addr;
wire [31:0] geo_mmio_data=cpu_run?geometry_reg_wdata:bd_geo_data;
assign obs_gpu_rdata=gpu_reg_rdata;
assign obs_bcr_ready=paired.init_done;
assign obs_cram_errors=cram_errors;
assign obs_retire=cpu_sys.cpu.WhiteboxerPlugin_logic_commits_ports_0_valid;
assign obs_retire_pc=cpu_sys.cpu.WhiteboxerPlugin_logic_commits_ports_0_pc;
assign obs_retire_uop=cpu_sys.cpu.WhiteboxerPlugin_logic_commits_ports_0_uop;
assign obs_freeze=cpu_sys.cpu._zz_WhiteboxerPlugin_logic_perf_executeFreezedCounter;
assign obs_hazard=cpu_sys.cpu._zz_WhiteboxerPlugin_logic_perf_dispatchHazardsCounter;
assign obs_upload=gpu.cpu_ring_write;
assign obs_upload_word=gpu_mmio_data;
assign obs_gpu_mmio=gpu_reg_wr&&cpu_run;
assign obs_gpu_mmio_addr=gpu_reg_addr;
assign obs_gpu_mmio_data=gpu_reg_wdata;
assign obs_geo_mmio=geometry_reg_wr&&cpu_run;
assign obs_geo_mmio_addr=geometry_reg_addr;
assign obs_geo_mmio_data=geometry_reg_wdata;
always @(posedge clk_cpu) if(cpu_run && (bd_gpu_wr || bd_geo_wr))
    $fatal(1,"Host MMIO initialization after native CPU release");
wire geometry_reg_wr;wire[9:0]geometry_reg_addr;wire[31:0]geometry_reg_wdata,geometry_reg_rdata;wire[3:0]geometry_reg_wstrb;
wire geometry_arvalid,geometry_arready,geometry_rvalid,geometry_rready,geometry_rlast;
wire[31:0]geometry_araddr,geometry_rdata;wire[7:0]geometry_arlen;
wire geometry_view_active,geometry_view_block,geometry_view_valid;wire[5:0]geometry_view_count;
reg[2:0] geometry_startup=0;
always@(posedge clk_cpu)geometry_startup<={geometry_startup[1:0],1'b1};
wire geometry_reset=!geometry_startup[2]||!reset_n;
`ifdef INCLUDE_GPU_ASYNC_MESH
wire mesh_ctx_valid,mesh_ctx_ready;wire[4:0]mesh_ctx_index;wire[31:0]mesh_ctx_data;
wire mesh_job_valid,mesh_job_ready;
wire[31:0]mesh_job_seq,mesh_job_vertex,mesh_job_vtx_w0,mesh_job_commands;
wire[31:0]mesh_job_control,mesh_job_flags,mesh_job_uofs,mesh_job_vofs;
wire[31:0]mesh_job_cpu_vertex,mesh_job_cpu_commands,mesh_job_alpha;
wire mesh_word_valid,mesh_word_ready,mesh_job_done,mesh_job_fault;
wire[31:0]mesh_word_data;wire mesh_consumer_reset,mesh_bridge_fault;
`elsif INCLUDE_GPU_MESH_FRONT
wire mesh_marker_valid,mesh_marker_ready,mesh_marker_error;
wire [31:0] mesh_marker_serial,mesh_marker_flags,mesh_marker_uofs,mesh_marker_vofs;
wire mesh_word_valid,mesh_word_ready,mesh_packet_done,mesh_packet_error;
wire [31:0] mesh_word_data;
wire mesh_consumer_reset,mesh_bridge_fault;
`endif
geometry_load_alias geometry_load(
`ifdef INCLUDE_GPU_ASYNC_MESH
 .mesh_ctx_valid(mesh_ctx_valid),
 .mesh_ctx_ready(mesh_ctx_ready),
 .mesh_ctx_index(mesh_ctx_index),
 .mesh_ctx_data(mesh_ctx_data),
 .mesh_job_valid(mesh_job_valid),
 .mesh_job_ready(mesh_job_ready),
 .mesh_job_seq(mesh_job_seq),
 .mesh_job_vertex(mesh_job_vertex),
 .mesh_job_vtx_w0(mesh_job_vtx_w0),
 .mesh_job_commands(mesh_job_commands),
 .mesh_job_control(mesh_job_control),
 .mesh_job_flags(mesh_job_flags),
 .mesh_job_uofs(mesh_job_uofs),
 .mesh_job_vofs(mesh_job_vofs),
 .mesh_job_cpu_vertex(mesh_job_cpu_vertex),
 .mesh_job_cpu_commands(mesh_job_cpu_commands),
 .mesh_job_alpha(mesh_job_alpha),
 .mesh_word_valid(mesh_word_valid),
 .mesh_word_data(mesh_word_data),
 .mesh_word_ready(mesh_word_ready),
 .mesh_job_done(mesh_job_done),
 .mesh_job_fault(mesh_job_fault),
 .mesh_consumer_reset(mesh_consumer_reset),
 .mesh_bridge_fault(mesh_bridge_fault),
`elsif INCLUDE_GPU_MESH_FRONT
 .mesh_marker_valid(mesh_marker_valid),.mesh_marker_serial(mesh_marker_serial),
 .mesh_marker_flags(mesh_marker_flags),.mesh_marker_uofs(mesh_marker_uofs),
 .mesh_marker_vofs(mesh_marker_vofs),.mesh_marker_ready(mesh_marker_ready),
 .mesh_marker_error(mesh_marker_error),.mesh_word_valid(mesh_word_valid),
 .mesh_word_data(mesh_word_data),.mesh_word_ready(mesh_word_ready),
 .mesh_packet_done(mesh_packet_done),.mesh_packet_error(mesh_packet_error),
 .mesh_consumer_reset(mesh_consumer_reset),.mesh_bridge_fault(mesh_bridge_fault),
`endif
 .clk(clk_cpu),.reset(geometry_reset),
 .reg_wr(geo_mmio_wr),.reg_addr(geo_mmio_addr),.reg_wdata(geo_mmio_data),.reg_wstrb(cpu_run?geometry_reg_wstrb:4'hf),.reg_rdata(geometry_reg_rdata),
 .m_arvalid(geometry_arvalid),.m_arready(geometry_arready),.m_araddr(geometry_araddr),.m_arlen(geometry_arlen),
 .m_rvalid(geometry_rvalid),.m_rready(geometry_rready),.m_rdata(geometry_rdata),.m_rresp(2'b00),.m_rlast(geometry_rlast),
 .view_read_addr(alias_view_addr),.view_read_data(alias_view_data),.view_active(geometry_view_active),.view_block(geometry_view_block),.view_valid(geometry_view_valid),.view_count(geometry_view_count));
wire word_wr, word_busy; wire[21:0]word_addr; wire[31:0]word_data; wire[4:0]word_len;
wire[15:0]cram_errors={14'd0,paired.error,paired.protocol_error};
wire[3:0]bcr_st=paired.init_done?4'd7:4'd0;localparam B_DONE=7;
generate begin:paired
 wire clk=clk_cpu; localparam real CLOCK_PERIOD=11.110;
 localparam real DELAY0=7.6,DELAY1=1.8,WAIT0=1.8,WAIT1=7.6;
 localparam integer REFRESH0=4,REFRESH1=2;
 localparam real CAPTURE_PHASE=3.913,CLKOUT_DELAY=5.0666,OUTPUT_PIN_DELAY=3.0666,INPUT_PIN_DELAY=2.1714;
 reg capture_clk=0;
 always @(posedge clk) #(CAPTURE_PHASE) capture_clk=1;
 always @(negedge clk) #(CAPTURE_PHASE) capture_clk=0;
 wire cmd_valid,cmd_ready,cmd_write,w_valid,w_ready,r_valid,r_ready,r_last,done,error,busy,init_done,protocol_error;
 wire [22:0] cmd_addr;wire[4:0]cmd_len;wire[31:0]w_data,r_data;wire[3:0]w_strb;
 wire [31:0] cram_dq_out,cram_dq_in;
 wire [11:0] cram_a;wire [1:0] cram_dq_oe,cram_wait,cram_clk;
 wire [1:0] cram_adv_n,cram_cre,cram_ce0_n,cram_ce1_n,cram_oe_n,cram_we_n,cram_ub_n,cram_lb_n;
 wire stuck_wait=0;

 psram32_controller #(.POWERUP_CYCLES(20)) ctrl(.*);
 psram32_axi_adapter adapter(.clk(clk),.reset_n(reset_n),.cpu_reset(!fixture_cpu_reset_n),
 .s_axi_arvalid(cpu_cram0_arvalid),.s_axi_arready(cpu_cram0_arready),.s_axi_araddr(cpu_cram0_araddr),.s_axi_arlen(cpu_cram0_arlen),
 .s_axi_rvalid(cpu_cram0_rvalid),.s_axi_rready(cpu_cram0_rready),.s_axi_rdata(cpu_cram0_rdata),.s_axi_rresp(cpu_cram0_rresp),.s_axi_rlast(cpu_cram0_rlast),
 .s_axi_awvalid(cpu_cram0_awvalid),.s_axi_awready(cpu_cram0_awready),.s_axi_awaddr(cpu_cram0_awaddr),.s_axi_awlen(cpu_cram0_awlen),
 .s_axi_wvalid(cpu_cram0_wvalid),.s_axi_wready(cpu_cram0_wready),.s_axi_wdata(cpu_cram0_wdata),.s_axi_wstrb(cpu_cram0_wstrb),.s_axi_wlast(cpu_cram0_wlast),
 .s_axi_bvalid(cpu_cram0_bvalid),.s_axi_bready(1'b1),.s_axi_bresp(cpu_cram0_bresp),
 .tex_arvalid(arvalid_m),.tex_arready(arready_m),.tex_araddr(araddr_m),.tex_arlen(arlen_m),.tex_rvalid(rvalid_m),.tex_rdata(rdata_m),.tex_rlast(rlast_m),
 .upload_wr(word_wr),.upload_addr(word_addr),.upload_data(word_data),.upload_busy(word_busy),.upload_len(word_len),.upload_reset(!fixture_cpu_reset_n),
 .cmd_valid(cmd_valid),.cmd_ready(cmd_ready),.cmd_write(cmd_write),.cmd_addr(cmd_addr),.cmd_len(cmd_len),
 .w_valid(w_valid),.w_ready(w_ready),.w_data(w_data),.w_strb(w_strb),.r_valid(r_valid),.r_ready(r_ready),.r_data(r_data),.r_last(r_last),
 .done(done),.error(error),.protocol_error(protocol_error));
 `include "psram_pins.vh"
end endgenerate

wire tex_arvalid,tex_arready,tex_rvalid,tex_rlast;
wire[31:0]tex_araddr,tex_rdata;wire[7:0]tex_arlen;
wire arvalid_m,arready_m,rvalid_m,rlast_m;wire[31:0]araddr_m,rdata_m;wire[7:0]arlen_m;
geometry_cram1_read_arbiter cram_arbiter(
 .clk(clk_cpu),.reset_n(reset_n),.s0_reset(!reset_n),
 .s0_arvalid(tex_arvalid),.s0_arready(tex_arready),.s0_araddr(tex_araddr),.s0_arlen(tex_arlen),.s0_rvalid(tex_rvalid),.s0_rdata(tex_rdata),.s0_rlast(tex_rlast),
 .s1_arvalid(geometry_arvalid&&bcr_st==B_DONE),.s1_arready(geometry_arready),.s1_araddr(geometry_araddr),.s1_arlen(geometry_arlen),.s1_rvalid(geometry_rvalid),.s1_rdata(geometry_rdata),.s1_rlast(geometry_rlast),
 .m_arvalid(arvalid_m),.m_arready(arready_m),.m_araddr(araddr_m),.m_arlen(arlen_m),.m_rvalid(rvalid_m),.m_rdata(rdata_m),.m_rlast(rlast_m));
integer native_upload_words=0,native_load_bursts=0,native_alias_beats=0,native_alias_stalls=0;
always@(posedge clk_cpu)if(reset_n)begin
 if(word_wr)native_upload_words<=native_upload_words+1;
 if(geometry_arvalid&&geometry_arready)native_load_bursts<=native_load_bursts+1;
 if(alias_shim.s_rvalid&&alias_shim.s_rready&&alias_shim.view_active)native_alias_beats<=native_alias_beats+1;
 if(alias_shim.s_rvalid&&!alias_shim.s_rready&&alias_shim.view_active)native_alias_stalls<=native_alias_stalls+1;
 if(cram_errors)$fatal(1,"physical CRAM timing/error");
 if(geometry_load.raw_start&&geometry_view_active)$fatal(1,"LOAD overwrote active view");
 if(geometry_reg_wr&&geometry_reg_addr==2&&geometry_reg_wdata[0]&&(word_busy||gpu.gpu_tex_mem_up_inflight))$fatal(1,"LOAD before source upload drained");
 if(geometry_rvalid&&!geometry_rready)$fatal(1,"CRAM response cannot be backpressured");
end
geometry_bram_alias alias_shim(.clk(clk_cpu),.reset(!reset_n),.cpu_reset(!fixture_cpu_reset_n),
.s_arvalid(cpu_sdram_arvalid),.m_arvalid(alias_normal_arvalid),
.s_arready(cpu_sdram_arready),.m_arready(alias_normal_arready),
.s_araddr(cpu_sdram_araddr),.m_araddr(alias_normal_araddr),
.s_arlen(cpu_sdram_arlen),.m_arlen(alias_normal_arlen),
.s_rvalid(alias_raw_rvalid),.m_rvalid(alias_normal_rvalid),
.s_rready(cpu_sdram_rready&&!alias_hold),.m_rready(alias_normal_rready),
.s_rdata(cpu_sdram_rdata),.m_rdata(alias_normal_rdata),
.s_rresp(cpu_sdram_rresp),.m_rresp(alias_normal_rresp),
.s_rlast(cpu_sdram_rlast),.m_rlast(alias_normal_rlast),
.s_awvalid(cpu_sdram_awvalid),.m_awvalid(alias_normal_awvalid),
.s_awready(cpu_sdram_awready),.m_awready(alias_normal_awready),
.s_awaddr(cpu_sdram_awaddr),.m_awaddr(alias_normal_awaddr),
.s_awlen(cpu_sdram_awlen),.m_awlen(alias_normal_awlen),
.s_wvalid(cpu_sdram_wvalid),.m_wvalid(alias_normal_wvalid),
.s_wready(cpu_sdram_wready),.m_wready(alias_normal_wready),
.s_wdata(cpu_sdram_wdata),.m_wdata(alias_normal_wdata),
.s_wstrb(cpu_sdram_wstrb),.m_wstrb(alias_normal_wstrb),
.s_wlast(cpu_sdram_wlast),.m_wlast(alias_normal_wlast),
.s_bvalid(cpu_sdram_bvalid),.m_bvalid(alias_normal_bvalid),
.s_bresp(cpu_sdram_bresp),.m_bresp(alias_normal_bresp),
.view_read_addr(alias_view_addr),.view_read_data(alias_view_data),.view_active(geometry_view_active),.view_block(geometry_view_block),.view_valid(geometry_view_valid),.view_count(geometry_view_count));

axi_sdram_arbiter #(
        .GPU_WRITE_QUEUE_DEPTH(`ifdef INCLUDE_GPU_WQ16 16 `else 8 `endif),
`ifdef INCLUDE_OS30_SDRAM166
        .BUFFERED_SLAVE(1'b1)
`else
        .BUFFERED_SLAVE(1'b0)
`endif
    ) sdram_arb (
    .clk(clk_cpu), .reset_n(reset_n),

    // M0 — GPU (live; reads + writes both go here)
    .m0_arvalid(gpu_rd_arvalid), .m0_arready(gpu_rd_arready),
    .m0_araddr (gpu_rd_araddr),  .m0_arlen  (gpu_rd_arlen),
    .m0_rvalid (gpu_rd_rvalid),  .m0_rdata  (gpu_rd_rdata),
    .m0_rresp  (gpu_rd_rresp),   .m0_rlast  (gpu_rd_rlast),
    .m0_awvalid(gpu_wr_awvalid), .m0_awready(gpu_wr_awready),
    .m0_awaddr (gpu_wr_awaddr),  .m0_awlen  (gpu_wr_awlen),
    .m0_wvalid (gpu_wr_wvalid),  .m0_wready (gpu_wr_wready),
    .m0_wdata  (gpu_wr_wdata),   .m0_wstrb  (gpu_wr_wstrb),
    .m0_wlast  (gpu_wr_wlast),
    .m0_bvalid (gpu_wr_bvalid),  .m0_bresp  (gpu_wr_bresp),

    // M1 — CPU (live)
    .m1_arvalid(alias_normal_arvalid), .m1_arready(alias_normal_arready),
    .m1_araddr (alias_normal_araddr),  .m1_arlen  (alias_normal_arlen),
    .m1_rvalid (alias_normal_rvalid),  .m1_rdata  (alias_normal_rdata),
    .m1_rresp  (alias_normal_rresp),   .m1_rlast  (alias_normal_rlast),
    .m1_rready (alias_normal_rready),
    .m1_awvalid(alias_normal_awvalid), .m1_awready(alias_normal_awready),
    .m1_awaddr (alias_normal_awaddr),  .m1_awlen  (alias_normal_awlen),
    .m1_wvalid (alias_normal_wvalid),  .m1_wready (alias_normal_wready),
    .m1_wdata  (alias_normal_wdata),   .m1_wstrb  (alias_normal_wstrb),
    .m1_wlast  (alias_normal_wlast),
    .m1_bvalid (alias_normal_bvalid),  .m1_bresp  (alias_normal_bresp),

    // M2 — bridge DMA unused in this system test
    .m2_arvalid(1'b0), .m2_arready(),
    .m2_araddr (32'd0), .m2_arlen(8'd0),
    .m2_rvalid (), .m2_rdata(), .m2_rresp(), .m2_rlast(),
    .m2_rready (1'b1),
    .m2_awvalid(1'b0), .m2_awready(bridge_awready_unused),
    .m2_awaddr (32'd0), .m2_awlen(8'd0),
    .m2_wvalid (1'b0), .m2_wready(bridge_wready_unused),
    .m2_wdata  (32'd0), .m2_wstrb(4'd0),
    .m2_wlast  (1'b0),
    .m2_bvalid (bridge_bvalid_unused), .m2_bresp(bridge_bresp_unused),

    // Captured synthetic audio read pressure, not native audio execution.
    .m3_arvalid(inj_audio_valid), .m3_arready(obs_audio_ready),
    .m3_araddr(32'h02c00000), .m3_arlen(8'd15),
    .m3_rvalid(obs_audio_rvalid), .m3_rdata(obs_audio_rdata),
    .m3_rlast(obs_audio_rlast), .m3_rready(1'b1),
    // Slave side
    .s_arvalid(arb_arvalid), .s_arready(arb_arready),
    .s_araddr (arb_araddr),  .s_arlen  (arb_arlen),
    .s_rvalid (arb_rvalid),  .s_rready (arb_rready),
    .s_rdata  (arb_rdata),   .s_rresp  (arb_rresp),
    .s_rlast  (arb_rlast),
    .s_awvalid(arb_awvalid), .s_awready(arb_awready),
    .s_awaddr (arb_awaddr),  .s_awlen  (arb_awlen),
    .s_wvalid (arb_wvalid),  .s_wready (arb_wready),
    .s_wdata  (arb_wdata),   .s_wstrb  (arb_wstrb),
    .s_wlast  (arb_wlast),
    .s_bvalid (arb_bvalid),  .s_bresp  (arb_bresp),
    .s_wcont  (arb_wcont),

    // Debug taps kept for the harness.
    .dbg_arb_state(tb_arb_state_dbg),
    .dbg_cpu_pending(tb_arb_cpu_pending_dbg),
    .dbg_grant(tb_arb_grant_dbg)
);

wire [1:0] tb_arb_state_dbg;
wire       tb_arb_cpu_pending_dbg;
wire [1:0] tb_arb_grant_dbg;

// ─── axi_sdram_slave + pulse adapter ───
wire        sdram_rd, sdram_wr;
wire [23:0] sdram_addr;
wire [31:0] sdram_wdata;
wire [3:0]  sdram_wstrb;
wire [3:0]  sdram_burst_len;
wire [3:0]  sdram_burst_wr_len;
wire        sdram_wr_data_next;
wire [31:0] sdram_next_wdata;
wire [3:0]  sdram_next_wstrb;
wire [31:0] sdram_preload_wdata;
wire [3:0]  sdram_preload_wstrb;

// Pulse adapter (mirror of core_top.v lines 1713–1746)
reg         cmd_forwarded;
reg         accepted_r;
reg         word_rd_sd;
reg         word_wr_sd;
reg  [23:0] word_addr_sd;
reg  [31:0] word_data_sd;
reg  [3:0]  word_wstrb_sd;
reg  [31:0] word_data_next_sd;
reg  [3:0]  word_wstrb_next_sd;
reg  [3:0]  word_burst_len_sd;
reg  [3:0]  word_burst_wr_len_sd;
wire [31:0] word_q_sd;
wire word_busy_sd, word_queue_pending_sd;
wire        word_q_valid_sd;
wire        word_wr_data_next_sd;
wire        word_wr_done_sd;

always @(posedge clk_cpu or negedge reset_n) begin
    if (!reset_n) begin
        word_rd_sd <= 0; word_wr_sd <= 0;
        word_addr_sd <= 0; word_data_sd <= 0; word_wstrb_sd <= 0;
        word_data_next_sd <= 0; word_wstrb_next_sd <= 0;
        word_burst_len_sd <= 0; word_burst_wr_len_sd <= 0;
        cmd_forwarded <= 0; accepted_r <= 0;
    end else begin
        word_rd_sd <= 0; word_wr_sd <= 0;
        word_burst_len_sd <= 0; word_burst_wr_len_sd <= 0;
        accepted_r <= 0;

        if (!sdram_rd && !sdram_wr)
            cmd_forwarded <= 0;

        if (!word_queue_pending_sd && !cmd_forwarded && (sdram_rd || sdram_wr)) begin
            word_rd_sd <= sdram_rd;
            word_wr_sd <= sdram_wr;
            word_addr_sd <= sdram_addr;
            word_data_sd <= sdram_wdata;
            word_wstrb_sd <= sdram_wstrb;
            word_data_next_sd <= sdram_preload_wdata;
            word_wstrb_next_sd <= sdram_preload_wstrb;
            word_burst_len_sd <= sdram_burst_len;
            word_burst_wr_len_sd <= sdram_burst_wr_len;
            accepted_r <= 1;
            cmd_forwarded <= 1;
        end
        // (The old wr_data_fwd_d1 refresh of word_data_sd is gone: native
        // burst continuation beats now flow over the direct
        // sdram_next_wdata/strb bus, matching io_sdram on hardware.)
    end
end

axi_sdram_slave sdram_axi_slave (
    .clk(clk_cpu), .reset_n(reset_n),
    .s_axi_arvalid(arb_arvalid), .s_axi_arready(arb_arready),
    .s_axi_araddr (arb_araddr),  .s_axi_arlen  (arb_arlen),
    .s_axi_rvalid (arb_rvalid),  .s_axi_rready (arb_rready),
    .s_axi_rdata  (arb_rdata),   .s_axi_rresp  (arb_rresp),
    .s_axi_rlast  (arb_rlast),
    .s_axi_awvalid(arb_awvalid), .s_axi_awready(arb_awready),
    .s_axi_awaddr (arb_awaddr),  .s_axi_awlen  (arb_awlen),
    .s_axi_wvalid (arb_wvalid),  .s_axi_wready (arb_wready),
    .s_axi_wdata  (arb_wdata),   .s_axi_wstrb  (arb_wstrb),
    .s_axi_wlast  (arb_wlast),
    .s_axi_bvalid (arb_bvalid),  .s_axi_bready (1'b1),
    .s_axi_bresp  (arb_bresp),
    .s_axi_wcont  (arb_wcont),
    .sdram_rd          (sdram_rd),
    .sdram_wr          (sdram_wr),
    .sdram_addr        (sdram_addr),
    .sdram_wdata       (sdram_wdata),
    .sdram_wstrb       (sdram_wstrb),
    .sdram_burst_len   (sdram_burst_len),
    .sdram_burst_wr_len(sdram_burst_wr_len),
    .sdram_rdata       (word_q_sd),
    .sdram_busy        (word_queue_pending_sd),
    .sdram_accepted    (accepted_r),
    .sdram_rdata_valid (word_q_valid_sd),
    .sdram_wr_data_next(word_wr_data_next_sd),
    .sdram_wr_done     (word_wr_done_sd),
    .sdram_next_wdata  (sdram_next_wdata),
    .sdram_next_wstrb  (sdram_next_wstrb),
    .sdram_preload_wdata(sdram_preload_wdata),
    .sdram_preload_wstrb(sdram_preload_wstrb)
);

// ============================================================
// Real MiSTer SDRAM back-end (replaces sdram_fast_model): the slave's
// word ops cross the real io_sdram FSM — bank/row management, CAS
// pipeline, refresh, and scanout burst arbitration — against a
// cycle-accurate chip model, exactly as on hardware.
// ============================================================
wire        phy_cke, phy_clk, phy_cas, phy_ras, phy_we;
wire [1:0]  phy_ba;
wire [12:0] phy_a;
wire [1:0]  phy_dqm;
wire        phy_ncs;    // constant 0 in 1T builds; live under INCLUDE_SDRAM_2T
wire [15:0] ctrl_dq_out;
wire        ctrl_dq_oe;     // unused (split-DQ shim drives port directly)
wire [15:0] model_dq_out;
wire        model_dq_oe;
wire [15:0] dq_to_ctrl; assign #4.0 dq_to_ctrl = model_dq_oe ? model_dq_out : 16'h0;
wire [7:0]  dbg_io_sd;

assign phy_ncs = 1'b0;
reg sdram_capture_clk=0,sdram_chip_clk=0;
always @(posedge clk_cpu) #0.25 sdram_capture_clk=1;
always @(negedge clk_cpu) #0.25 sdram_capture_clk=0;
always @(posedge clk_cpu) #9.2 sdram_chip_clk=1;
always @(negedge clk_cpu) #9.2 sdram_chip_clk=0;
io_sdram #(.BANK_ROW_TRACK(1),.REFRESH_INTERVAL(660)) sdram_ctrl (
    .controller_clk(clk_cpu), .capture_clk(sdram_capture_clk), .chip_clk(sdram_chip_clk), .clk_90(clk_cpu),
    .reset_n(reset_n),
    .phy_cke(phy_cke), .phy_clk(phy_clk),
    .phy_cas(phy_cas), .phy_ras(phy_ras), .phy_we(phy_we),
    .phy_ba(phy_ba), .phy_a(phy_a),
    .phy_dq_in(dq_to_ctrl),
    .phy_dq_out_port(ctrl_dq_out),
    .phy_dq_oe_port(ctrl_dq_oe),
    .phy_dqm(phy_dqm),

    .burst_rd(inj_burst_rd), .burst_addr(inj_burst_addr),
    .burst_len(inj_burst_len), .burst_32bit(1'b1),
    .burst_data(inj_burst_data), .burst_data_valid(inj_burst_data_valid),
    .burst_data_done(inj_burst_data_done),
    .burstwr(1'b0), .burstwr_addr(25'b0), .burstwr_ready(),
    .burstwr_strobe(1'b0), .burstwr_data(16'b0), .burstwr_done(1'b0),
    .word_rd(word_rd_sd), .word_wr(word_wr_sd),
    .word_addr(word_addr_sd), .word_data(word_data_sd), .word_wstrb(word_wstrb_sd),
    .word_data_next(word_data_next_sd), .word_wstrb_next(word_wstrb_next_sd),
    .word_burst_len(word_burst_len_sd), .word_burst_wr_len(word_burst_wr_len_sd),
    .word_q(word_q_sd), .word_busy(word_busy_sd), .word_q_valid(word_q_valid_sd), .word_queue_pending(word_queue_pending_sd),
    .word_wr_data_next(word_wr_data_next_sd),
    .word_wr_done(word_wr_done_sd),
    .burst_wr_direct_data(sdram_next_wdata),
    .burst_wr_direct_strb(sdram_next_wstrb),
    .dbg_io(dbg_io_sd)
);

wire [1:0] pin_ba,pin_dqm;wire[12:0]pin_a;wire[15:0]pin_dq;
wire pin_cke,pin_ras,pin_cas,pin_we;
assign #4.0 {pin_ba,pin_dqm,pin_a,pin_dq,pin_cke,pin_ras,pin_cas,pin_we}={phy_ba,phy_dqm,phy_a,ctrl_dq_out,phy_cke,phy_ras,phy_cas,phy_we};
sdram_model_full sdram_chip (
    .clk(phy_clk), .cke(pin_cke), .cs_n(phy_ncs),
    .ras_n(pin_ras), .cas_n(pin_cas), .we_n(pin_we),
    .ba(pin_ba), .a(pin_a),
    .dq_in(pin_dq),
    .dq_out(model_dq_out), .dq_oe(model_dq_oe),
    .dqm(pin_dqm),
    .bd_we(bd_sdram_we), .bd_word_addr(bd_sdram_word_addr),
    .bd_wdata(bd_sdram_wdata),
    .bd_rd_word_addr(bd_sdram_rd_word_addr), .bd_rd_data(bd_sdram_rd_data),
    .wr_evt(dbg_model_wr_evt), .wr_evt_hw_addr(dbg_model_wr_hw_addr),
    .wr_evt_dqm(dbg_model_wr_dqm), .wr_evt_data(dbg_model_wr_data)
);

assign dbg_aw_valid = arb_awvalid;
assign dbg_aw_ready = arb_awready;
assign dbg_aw_addr  = arb_awaddr;
assign dbg_aw_len   = arb_awlen;
assign dbg_w_valid  = arb_wvalid;
assign dbg_w_ready  = arb_wready;
assign dbg_w_data   = arb_wdata;
assign dbg_w_strb   = arb_wstrb;
assign dbg_w_last   = arb_wlast;
assign dbg_w_cont   = arb_wcont;

// M1 observation taps (CPU master port into the SDRAM arbiter)
assign dbg_m1_arvalid = cpu_sdram_arvalid;
assign dbg_m1_arready = cpu_sdram_arready;
assign dbg_m1_araddr  = cpu_sdram_araddr;
assign dbg_m1_arlen   = cpu_sdram_arlen;
assign dbg_m1_rvalid  = cpu_sdram_rvalid;
assign dbg_m1_rready  = cpu_sdram_rready;
assign dbg_m1_rdata   = cpu_sdram_rdata;
assign dbg_m1_rlast   = cpu_sdram_rlast;
assign dbg_m1_awvalid = cpu_sdram_awvalid;
assign dbg_m1_awready = cpu_sdram_awready;
assign dbg_m1_awaddr  = cpu_sdram_awaddr;

// ============================================================
// CRAM0 — CDC from clk_cpu to clk_74a, then word interface mux to
//         either the backdoor (bridge-native) or the CDC output
//         depending on cram0_mode (driven by axi_periph_slave).
// ============================================================
wire cram0_mode_cpu; assign dbg_cram0_mode=cram0_mode_cpu;
// // axi_periph_slave — LOCAL target (BRAM 32KB + MMIO + UART)
// ============================================================
// Tie-offs for peripheral inputs we don't exercise.
wire target_dataslot_ack   = 1'b0;
wire target_dataslot_done  = 1'b0;
wire [2:0] target_dataslot_err = 3'b0;
wire [31:0] link_reg_rdata = 32'b0;
wire [9:0] audio_fifo_level = 10'b0;
wire audio_fifo_full = 1'b0;
wire uart_tx_active = 1'b0;   // UART always idle in sim → TX FIFO always ready
wire uart_rx_dv = 1'b0;
wire [7:0] uart_rx_byte = 8'b0;
wire shutdown_pending = 1'b0;
wire [31:0] dt_query_data = 32'b0;
wire dt_query_valid = 1'b0;
wire [7:0] snac_pin_in = 8'b0;
wire link_irq = 1'b0;
wire bridge_wr_idle = 1'b1;
wire dataslot_allcomplete = 1'b1;
wire [31:0] cont1_key = 32'b0;
wire [31:0] cont1_joy = 32'b0;
wire [15:0] cont1_trig = 16'b0;
wire [31:0] cont2_key = 32'b0;
wire [31:0] cont2_joy = 32'b0;
wire [15:0] cont2_trig = 16'b0;
wire [31:0] app_id = 32'b0;

axi_periph_slave #(
        // CPU/RAM clock frequency advertisement (CLK_FREQ_HZ @ 0xD4) —
        // must track the mp_ram PLL selection above.  Firmware reads it at
        // boot (fallback: compile-time OF_TARGET_CPU_FREQ_HZ when 0), so
        // ONE os.bin serves 100, 96 and 90 MHz bitstreams alike.
        .CLK_HZ(`ifdef INCLUDE_OS30_SDRAM166 32'd83_000_000 `elsif INCLUDE_CLK90 32'd90_000_000 `elsif INCLUDE_CLK96 32'd96_000_000 `else 32'd100_000_000 `endif),
        // ANALOGIZER (HW_FEATURES bit 3): the SNAC instances inside
        // axi_periph_slave.v gate on this; clear → they constant-fold away.
        .INCLUDE_ANALOGIZER(`ifdef INCLUDE_ANALOGIZER 1 `else 0 `endif),
        // LINK (HW_FEATURES bit 2).  No Pocket game uses the link cable, so
        // neither variant includes it; FEAT_LINK also keys on the macro.
        .INCLUDE_LINK(`ifdef INCLUDE_LINK 1 `else 0 `endif),
        // Mixer backend bit (HW_FEATURES bit 1, OF_HW_MIXER_HW).  The HW
        // audio_mixer.v instance below is cut when INCLUDE_HW_MIXER is absent
        // (OS30), so advertise 0 there and the OS picks its CPU software mixer
        // at boot.  Bit 0 (OF_HW_MIXER, "mixer available") stays SET in both
        // cases.  OS25/MiSTer keep the HW mixer → INCLUDE_HW_MIXER 1.
        .INCLUDE_HW_MIXER(`ifdef INCLUDE_HW_MIXER 1 `else 0 `endif),
        // Triangle extras.  OS25 includes none of them (only Quake1 emits 0x49,
        // and it falls back to SW alias rendering); OS30 (Quake2) includes
        // PARAM_TRI/VERT_TRI/PARAM_TRI_RECS but NOT column/compact (Quake2
        // never emits those); MiSTer includes all.  span-group / column apps
        // (Quake1/Doom/gpudemo) self-gate via the SDK and stay on OS25.
        .INCLUDE_PARAM_TRI(`ifdef INCLUDE_PARAM_TRI 1 `else 0 `endif),
        .INCLUDE_VERT_TRI(`ifdef INCLUDE_VERT_TRI 1 `else 0 `endif),
        .INCLUDE_PARAM_TRI_RECS(`ifdef INCLUDE_PARAM_TRI_RECS 1 `else 0 `endif),
        .INCLUDE_COLUMN_LIST(`ifdef INCLUDE_COLUMN_LIST 1 `else 0 `endif),
        .INCLUDE_COMPACT_SPAN(`ifdef INCLUDE_COMPACT_SPAN 1 `else 0 `endif),
        // Fast texture memory advertisement (HW_FEATURES bit 25).  The CRAM1
        // controller/phy below is gated on the same INCLUDE_TEX_MEM macro, so
        // OS25 (no macro) advertises 0 → caps->tex_fast_size 0 → SDRAM textures.
        .INCLUDE_TEX_MEM(`ifdef INCLUDE_TEX_MEM 1 `else 0 `endif),
        // Truecolor cap (bit 10) with the same dependency pull-up as gpu_core
        // (combine / xform imply truecolor) so the caps word tracks what is built.
        .INCLUDE_DIRECT_COLOR(`ifdef INCLUDE_DIRECT_COLOR 1 `elsif INCLUDE_COMBINE 1 `elsif INCLUDE_XFORM 1 `else 0 `endif),
        // XFORM bundle cap (bit 26): tracks the gpu_core transform front-end.
        // 0x51/0x52/0x53 all need the matrix MAC, so a MAC-less build clears it
        // (bit 29 still advertises the 0x56/0x54 clip-space cache).
        .INCLUDE_XFORM_RGB(`ifdef INCLUDE_GPU_SM64_GEOMETRY 0 `else `ifdef INCLUDE_XFORM `ifdef EXCLUDE_GPU_XFORM_MAC 0 `else 1 `endif `else 0 `endif `endif),
        .INCLUDE_GPU_CLIP_LOAD(`ifdef INCLUDE_XFORM 1 `else 0 `endif),
        .INCLUDE_GPU_LIGHT(`ifdef INCLUDE_XFORM `ifdef EXCLUDE_GPU_LIGHT 0 `else 1 `endif `else 0 `endif),
        // GPU texel*C+D combiner cap (bit 27, OF_HW_GPU_COMBINE), additive —
        // advertised only when the build lists INCLUDE_COMBINE (gated AND with
        // truecolor inside axi_periph_slave).
        .INCLUDE_COMBINE(`ifdef INCLUDE_COMBINE 1 `else 0 `endif),
        // Q29 caps bit 18: advertised only when the build keeps the module (os30 clears it).
        .INCLUDE_PARAM_SPAN_Q29(`ifdef INCLUDE_PARAM_SPAN_Q29 1 `else 0 `endif),
        .INCLUDE_PERSPECTIVE(`ifdef EXCLUDE_GPU_PERSPECTIVE 0 `else 1 `endif),
        .INCLUDE_DEPTH(`ifdef EXCLUDE_GPU_DEPTH 0 `else 1 `endif),
        .INCLUDE_PARAM_SPAN_LIST(`ifdef EXCLUDE_GPU_PARAM_SPAN_LIST 0 `else 1 `endif)
    ) periph (
.geometry_reg_wr(geometry_reg_wr),.geometry_reg_addr(geometry_reg_addr),.geometry_reg_wdata(geometry_reg_wdata),.geometry_reg_wstrb(geometry_reg_wstrb),.geometry_reg_rdata(geometry_reg_rdata),
    .mix_voice_ready(1'b1),
    .clk    (clk_cpu),
    .reset_n(reset_n),

    .s_axi_arvalid(cpu_local_arvalid), .s_axi_arready(cpu_local_arready),
    .s_axi_araddr (cpu_local_araddr),  .s_axi_arlen  (cpu_local_arlen),
    .s_axi_rvalid (cpu_local_rvalid),  .s_axi_rready (cpu_local_rready),
    .s_axi_rdata  (cpu_local_rdata),   .s_axi_rresp  (cpu_local_rresp),
    .s_axi_rlast  (cpu_local_rlast),
    .s_axi_awvalid(cpu_local_awvalid), .s_axi_awready(cpu_local_awready),
    .s_axi_awaddr (cpu_local_awaddr),  .s_axi_awlen  (cpu_local_awlen),
    .s_axi_awburst(cpu_local_awburst),
    .s_axi_wvalid (cpu_local_wvalid),  .s_axi_wready (cpu_local_wready),
    .s_axi_wdata  (cpu_local_wdata),   .s_axi_wstrb  (cpu_local_wstrb),
    .s_axi_wlast  (cpu_local_wlast),
    .s_axi_bvalid (cpu_local_bvalid),  .s_axi_bready (1'b1),
    .s_axi_bresp  (cpu_local_bresp),

    .dataslot_allcomplete(dataslot_allcomplete),
    .vsync               (vsync),
    .early_vblank        (1'b0),
    .os_inmenu           (1'b0),
    .cont1_key           (cont1_key),
    .cont1_joy           (cont1_joy),
    .cont1_trig          (cont1_trig),
    .cont2_key           (cont2_key),
    .cont2_joy           (cont2_joy),
    .cont2_trig          (cont2_trig),
    .cont3_key           (32'b0),
    .cont3_joy           (32'b0),
    .cont3_trig          (16'b0),
    .cont4_key           (32'b0),
    .cont4_joy           (32'b0),
    .cont4_trig          (16'b0),
    .target_dataslot_ack (target_dataslot_ack),
    .target_dataslot_done(target_dataslot_done),
    .target_dataslot_err (target_dataslot_err),
    .hps_status          (32'd0),
    .hps_img_size        (64'd0),
    .hps_boot_len        (32'd0),
    .hps_img1_size       (64'd0),
    .hps_img2_size       (64'd0),
    .bridge_wr_idle      (bridge_wr_idle),
    .bridge_dbg_wcnt     (32'd0),

    .color_mode     (),
    .fb_display_addr(),
    .fb_width       (),
    .fb_height      (),
    .fb_stride      (),

    .pal_wr  (), .pal_addr(), .pal_data(), .pal_commit(), .pal_busy(1'b0),

    .target_dataslot_read      (),
    .target_dataslot_write     (),
    .target_dataslot_openfile  (),
    .target_dataslot_getfile   (),
    .target_dataslot_id        (),
    .target_dataslot_slotoffset(),
    .target_dataslot_bridgeaddr(),
    .target_dataslot_length    (),
    .target_buffer_param_struct(),
    .target_buffer_resp_struct (),

    .audio_fifo_level (audio_fifo_level),
    .audio_fifo_full  (audio_fifo_full),

    .cram0_mode(cram0_mode_cpu),

    .link_reg_wr   (),
    .link_reg_rd   (),
    .link_reg_addr (),
    .link_reg_wdata(),
    .link_reg_rdata(link_reg_rdata),

    .save_dt_slot   (),
    .save_dt_size   (),
    .save_dt_commit (),

    .uart_tx_dv    (uart_tx_dv),
    .uart_tx_byte  (uart_tx_byte),
    .uart_tx_active(uart_tx_active),
    .uart_rx_dv    (uart_rx_dv),
    .uart_rx_byte  (uart_rx_byte),

    .app_id         (app_id),
    .analogizer_settings(32'b0),
    .analogizer_hoffset (32'b0),
    .analogizer_voffset (32'b0),
    .analogizer_cpu_wr_toggle(),
    .analogizer_cpu_wr_settings(),
    .analogizer_cpu_wr_hoffset(),
    .analogizer_cpu_wr_voffset(),
    .shutdown_pending(shutdown_pending),
    .shutdown_ack   (),

    .timer_irq  (test_timer_irq),
    .uart_rx_irq(),

    .link_irq        (link_irq),
    .ext_irq         (),

    .dt_query_addr  (),
    .dt_query_toggle(),
    .dt_query_data  (dt_query_data),
    .dt_query_valid (dt_query_valid),

    .vrr_v_total(),
    .analogizer_enabled(1'b0),

    .snac_pin_out(),
    .snac_pin_dir(),
    .snac_pin_in (snac_pin_in),
    .snac_enable (),

    .gpu_reg_wr   (gpu_reg_wr),
    .gpu_reg_addr (gpu_reg_addr),
    .gpu_reg_wdata(gpu_reg_wdata),
    .gpu_reg_rdata(gpu_reg_rdata),
    // CMD_FLIP side-port from gpu_core
    .gpu_swap_req (gpu_swap_req),
    .gpu_swap_idx (gpu_swap_idx),
    .fb_swap_pending_o (slave_swap_pending)
);

// ============================================================
// gpu_core — full GPU instance (matches core_top.v wiring)
// ============================================================
gpu_core #(
    // Every feature param echoes its INCLUDE_* build macro (variant include
    // list).  OS25 includes NO triangle command (0x49/0x4A-0x4B/0x4D all
    // absent → the shared edge walker constant-folds away) but keeps the 2.5D
    // fastpaths (0x48 compact span groups + 0x4C column lists + translucency).
    // OS30 (Quake2) is the inverse: triangles + fast tex IN, compact/column
    // OUT (Quake2 never emits them).  MiSTer includes all but analogizer +
    // fast tex.  Each INCLUDE_* MUST match the periph's same-named param above.
    .INCLUDE_PARAM_TRI(`ifdef INCLUDE_PARAM_TRI 1 `else 0 `endif),
    .INCLUDE_VERT_TRI(`ifdef INCLUDE_VERT_TRI 1 `else 0 `endif),
    .INCLUDE_PARAM_TRI_RECS(`ifdef INCLUDE_PARAM_TRI_RECS 1 `else 0 `endif),
    .INCLUDE_COMPACT_SPAN(`ifdef INCLUDE_COMPACT_SPAN 1 `else 0 `endif),
    .INCLUDE_COLUMN_LIST(`ifdef INCLUDE_COLUMN_LIST 1 `else 0 `endif),
    .INCLUDE_TEX_MEM(`ifdef INCLUDE_TEX_MEM 1 `else 0 `endif),
    // Dependency pull-up (resolver): combine / xform front-end imply the
    // truecolor RGB565 datapath, so a build can list INCLUDE_COMBINE or
    // INCLUDE_XFORM alone and get truecolor automatically.  See docs/MODULES.md.
    .INCLUDE_DIRECT_COLOR(`ifdef INCLUDE_DIRECT_COLOR 1 `elsif INCLUDE_COMBINE 1 `elsif INCLUDE_XFORM 1 `else 0 `endif),
    // XFORM module (coarse bundle): the whole GPU transform front-end — 0x52
    // xform + 0x50/0x51 matrix-MAC + 0x53/0x54/0x56 vtx-cache + 0x55/0x57
    // lighting + 0x4F clip — is one INCLUDE_XFORM, refined by the EXCLUDE_*
    // opt-outs below.  os30 ships it as of 2026-08-04 (minus lighting/clip);
    // os25 stays off and thus sheds its previously-dead 0x50/0x51/0x4F cones.
    .INCLUDE_XFORM_RGB(`ifdef INCLUDE_XFORM 1 `else 0 `endif),
    // Fine-grained opt-outs inside the XFORM bundle, so a variant can take
    // the vertex cache (0x53 LOAD_VERTS / 0x54 DRAW_INDEXED_TRI, the
    // transform-once/draw-many win) WITHOUT paying for the parts it does
    // not use.  The 0x53 decode gates on VTX_CACHE && XFORM_RGB only -- it
    // does NOT require the matrix MAC -- so a host that already does its
    // own model/view math (os30) can drop the MAC and its xf_M M10K and
    // keep only reciprocal/projection + the cache.
    .INCLUDE_CLIP_TRI(`ifdef INCLUDE_XFORM `ifdef EXCLUDE_CLIP_TRI 0 `else 1 `endif `else 0 `endif),
    .INCLUDE_VTX_CACHE(`ifdef INCLUDE_XFORM 1 `else 0 `endif),
    .INCLUDE_GPU_LIGHT(`ifdef INCLUDE_XFORM `ifdef EXCLUDE_GPU_LIGHT 0 `else 1 `endif `else 0 `endif),
    .INCLUDE_GPU_XFORM_MAC(`ifdef INCLUDE_XFORM `ifdef EXCLUDE_GPU_XFORM_MAC 0 `else 1 `endif `else 0 `endif),
    // Palette / colormap lane (additive): os25/mister list INCLUDE_PALETTE; os30 omits it.
    .INCLUDE_PALETTE(`ifdef INCLUDE_PALETTE 1 `else 0 `endif),
    .INCLUDE_PERSPECTIVE(`ifdef EXCLUDE_GPU_PERSPECTIVE 0 `else 1 `endif),
    .INCLUDE_DEPTH(`ifdef EXCLUDE_GPU_DEPTH 0 `else 1 `endif),
    .INCLUDE_PARAM_SPAN_LIST(`ifdef EXCLUDE_GPU_PARAM_SPAN_LIST 0 `else 1 `endif),
    // Combiner texel*C+D (additive): os30 lists INCLUDE_COMBINE (Mario head).
    .INCLUDE_COMBINE(`ifdef INCLUDE_COMBINE 1 `else 0 `endif),
    // Q29 param-span dynamic-scale precision (additive): os25/mister keep it
    // (Quake perspective); os30 omits it so the Q29 z-step cone + perspective
    // branches fold — removes the GPU's #1 critical path + frees ~150-220 ALM.
    .INCLUDE_PARAM_SPAN_Q29(`ifdef INCLUDE_PARAM_SPAN_Q29 1 `else 0 `endif),
    // Optional CPU command uploads reuse the ring RAM. Status advertises this
    // transport; DMA-free variants require applications that select it.
    .INCLUDE_CPU_RING(`ifdef INCLUDE_CPU_RING 1 `else 0 `endif),
    .INCLUDE_COMMAND_DMA(`ifdef EXCLUDE_GPU_COMMAND_DMA 0 `else 1 `endif),
    // Gather adjacent masked framebuffer words before requesting SDRAM.
    // Keep these independently selectable from the triangle/depth features.
    .GPU_WRITE_COMBINE_FAST_FLUSH(`ifdef INCLUDE_GPU_WRITE_BURSTS 1 `else 0 `endif),
    .GPU_WRITE_GATHER(`ifdef INCLUDE_GPU_WRITE_BURSTS 1 `else 0 `endif),
    .GPU_MASKED_WRITE_BURSTS(`ifdef INCLUDE_GPU_WRITE_BURSTS 1 `else 0 `endif),
    // Numeric tuning (not feature gates).  The z read window is 4 on the
    // triangle-heavy variants and degenerates to 1 on OS25 (single-word z fill,
    // sweeps the window logic).  Keyed off INCLUDE_Z_BURST (NOT INCLUDE_TEX_MEM)
    // so OS30/SM64 keeps the 16-byte z-line burst even with the fast-tex chip
    // disabled — SM64 z-tests every fragment.
    // EW_PARALLEL_DIVS: 1 runs three slope dividers concurrently (~28 beats vs
    // ~87 serial), cutting triangle setup ~30% with bit-identical quotients.
    // BOTH pocket variants currently ship 0 — no variant defines
    // INCLUDE_PARALLEL_DIVS (os30 lists it among its cuts) — on ALM budget.
    // INCLUDE_Z_WINDOW16/8 widen it to a 64/32-byte line (SM64 castle: fewer
    // depth-line refills; pairs with INCLUDE_GPU_SELECTIVE_READ_WAIT).
    .GPU_Z_READ_WINDOW(`ifdef INCLUDE_Z_WINDOW16 16 `elsif INCLUDE_Z_WINDOW8 8 `elsif INCLUDE_Z_BURST 4 `else 1 `endif),
    // Truecolor-blend dst read window: 4-word default; INCLUDE_CB_WINDOW2
    // halves it for ALM-pressed variants; INCLUDE_CB_WINDOW1 compiles the
    // window out (legacy per-pixel blend reads — measurement/fallback).
    // Byte-identical behavior at every size.
    .GPU_CB_READ_WINDOW(`ifdef INCLUDE_CB_WINDOW1 1 `else `ifdef INCLUDE_CB_WINDOW2 2 `else 4 `endif `endif),
    // Additive: only a build that lists INCLUDE_PARALLEL_DIVS gets the concurrent
    // slope dividers.  Neither pocket variant lists it today (both = 0), which is
    // bit-identical to the old EXCLUDE_PARALLEL_DIVS/DIRECT_COLOR derivation and
    // removes that load-bearing ifdef ordering.
    .GPU_EW_PARALLEL_DIVS(`ifdef INCLUDE_PARALLEL_DIVS 1 `else 0 `endif),
    // The render cache gathers and merges framebuffer/depth writes itself;
    // the internal write combiner in front of it only costs time and RAM.
    .GPU_WRITE_COMBINE(`ifdef INCLUDE_GPU_RENDER_CACHE 0 `else 1 `endif)
) gpu (
`ifdef INCLUDE_GPU_ASYNC_MESH
 .mesh_ctx_valid(mesh_ctx_valid),
 .mesh_ctx_ready(mesh_ctx_ready),
 .mesh_ctx_index(mesh_ctx_index),
 .mesh_ctx_data(mesh_ctx_data),
 .mesh_job_valid(mesh_job_valid),
 .mesh_job_ready(mesh_job_ready),
 .mesh_job_seq(mesh_job_seq),
 .mesh_job_vertex(mesh_job_vertex),
 .mesh_job_vtx_w0(mesh_job_vtx_w0),
 .mesh_job_commands(mesh_job_commands),
 .mesh_job_control(mesh_job_control),
 .mesh_job_flags(mesh_job_flags),
 .mesh_job_uofs(mesh_job_uofs),
 .mesh_job_vofs(mesh_job_vofs),
 .mesh_job_cpu_vertex(mesh_job_cpu_vertex),
 .mesh_job_cpu_commands(mesh_job_cpu_commands),
 .mesh_job_alpha(mesh_job_alpha),
 .mesh_word_valid(mesh_word_valid),
 .mesh_word_data(mesh_word_data),
 .mesh_word_ready(mesh_word_ready),
 .mesh_job_done(mesh_job_done),
 .mesh_job_fault(mesh_job_fault),
 .mesh_consumer_reset(mesh_consumer_reset),
 .mesh_bridge_fault(mesh_bridge_fault),
`elsif INCLUDE_GPU_MESH_FRONT
 .mesh_marker_valid(mesh_marker_valid),.mesh_marker_serial(mesh_marker_serial),
 .mesh_marker_flags(mesh_marker_flags),.mesh_marker_uofs(mesh_marker_uofs),
 .mesh_marker_vofs(mesh_marker_vofs),.mesh_marker_ready(mesh_marker_ready),
 .mesh_marker_error(mesh_marker_error),.mesh_word_valid(mesh_word_valid),
 .mesh_word_data(mesh_word_data),.mesh_word_ready(mesh_word_ready),
 .mesh_packet_done(mesh_packet_done),.mesh_packet_error(mesh_packet_error),
 .mesh_consumer_reset(mesh_consumer_reset),.mesh_bridge_fault(mesh_bridge_fault),
`endif
.gpu_tex_mem_arvalid(tex_arvalid),.gpu_tex_mem_arready(tex_arready),.gpu_tex_mem_araddr(tex_araddr),.gpu_tex_mem_arlen(tex_arlen),.gpu_tex_mem_rvalid(tex_rvalid),.gpu_tex_mem_rdata(tex_rdata),.gpu_tex_mem_rlast(tex_rlast),
.gpu_tex_mem_up_wr(word_wr),.gpu_tex_mem_up_addr(word_addr),.gpu_tex_mem_up_wdata(word_data),.gpu_tex_mem_up_busy(word_busy),
.gpu_tex_mem_up_len(word_len),.gpu_tex_mem_up_fault(paired.error||paired.protocol_error),
    .clk        (clk_cpu),
    .reset_n    (reset_n),
    .gpu_enable (1'b1),
    // AXI4 read master (ring fetch + texture)
    .m_rd_arvalid(gpu_s_rd_arvalid), .m_rd_arready(gpu_s_rd_arready),
    .m_rd_araddr (gpu_s_rd_araddr),  .m_rd_arlen  (gpu_s_rd_arlen),
    .m_rd_rvalid (gpu_s_rd_rvalid),  .m_rd_rdata  (gpu_s_rd_rdata),
    .m_rd_rlast  (gpu_s_rd_rlast),
    // AXI4 write master (FB writes + clear)
    .m_wr_awvalid(gpu_s_wr_awvalid), .m_wr_awready(gpu_s_wr_awready),
    .m_wr_awaddr (gpu_s_wr_awaddr),  .m_wr_awlen  (gpu_s_wr_awlen),
    .m_wr_wvalid (gpu_s_wr_wvalid),  .m_wr_wready (gpu_s_wr_wready),
    .m_wr_wdata  (gpu_s_wr_wdata),   .m_wr_wstrb  (gpu_s_wr_wstrb),
    .m_wr_wlast  (gpu_s_wr_wlast),
    .m_wr_bvalid (gpu_s_wr_bvalid),
    // SRAM scratch
    .sram_rd     (gpu_sram_rd),
    .sram_wr     (gpu_sram_wr),
    .sram_rd_half(gpu_sram_rd_half),
    .sram_rd_hi  (gpu_sram_rd_hi),
    .sram_addr   (gpu_sram_addr),
    .sram_wdata  (gpu_sram_wdata),
    .sram_wstrb  (gpu_sram_wstrb),
    .sram_rdata  (gpu_sram_rdata),
    .sram_busy   (gpu_sram_busy),
    .sram_rdata_valid(gpu_sram_rdata_valid),
    // MMIO from periph slave
    .reg_wr      (gpu_mmio_wr),
    .reg_addr    (gpu_mmio_addr),
    .reg_wdata   (gpu_mmio_data),
    .reg_rdata   (gpu_reg_rdata_core),
    // CMD_FLIP side-port → periph slave
    .gpu_swap_req(gpu_swap_req),
    .gpu_swap_idx(gpu_swap_idx),
    .slave_swap_pending(slave_swap_pending),
    // Status
    .busy        (gpu_busy_core),
    .fence_reached(obs_fence),
    .memory_barrier_done(gpu_cache_barrier_done),
    .memory_barrier_req(gpu_cache_barrier_req),
    .cache_clear_bypass(gpu_cache_clear_bypass),
    .cache_soft_reset(gpu_cache_soft_reset),
    .cache_tex_flush(gpu_cache_tex_flush)
);

// ============================================================
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
always @(posedge clk_cpu) begin
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
assign obs_gpu_busy = gpu_busy_core || gpu_cache_busy || gpu_cache_has_lines || gpu_cache_idle_flush;
assign gpu_reg_rdata = gpu_reg_rdata_core | ((gpu_mmio_addr == 4'd5 && obs_gpu_busy) ? 32'd1 : 32'd0);
assign gpu_rd_araddr = {6'd0, gpu_cache_m_araddr};
assign gpu_wr_awaddr = {6'd0, gpu_cache_m_awaddr};
gpu_color_depth_cache #(
    .ADDR_W(26), .CACHE_ALL(1), .WAYS(2), .SET_BITS(8), .WORD_BITS(4), .REGISTER_READ_LOOKUP(1), .REGISTER_FILL_HAZARD(1), .SPECULATE_WRITE_HOT0(1)
) gpu_render_cache (
    .clk(clk_cpu), .reset_n(reset_n && !gpu_cache_soft_reset), .bus_reset_n(reset_n),
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
always @(posedge clk_cpu)
    if (reset_n && gpu_cache_protocol_error) begin
        $display("FAIL render cache protocol error");
        $fatal(1, "render cache protocol error");
    end

// SRAM Model (word-level GPU scratch)
// ============================================================
reg [31:0] gpu_sram_mem [0:65535];
reg        gpu_sram_busy_r;
reg        gpu_sram_rvalid_r;
reg [1:0]  gpu_sram_delay;
reg        gpu_sram_op_read;
reg        gpu_sram_half_r;
reg        gpu_sram_hi_r;
reg [15:0] gpu_sram_addr_r;
reg [31:0] gpu_sram_wdata_r;
reg [3:0]  gpu_sram_wstrb_r;

assign gpu_sram_busy = gpu_sram_busy_r;
assign gpu_sram_rdata_valid = gpu_sram_rvalid_r;
assign gpu_sram_rdata = gpu_sram_half_r
                      ? (gpu_sram_hi_r
                         ? {gpu_sram_mem[gpu_sram_addr_r][31:16], 16'd0}
                         : {16'd0, gpu_sram_mem[gpu_sram_addr_r][15:0]})
                      : gpu_sram_mem[gpu_sram_addr_r];

always @(posedge clk_cpu) begin
    if (!reset_n) begin
        gpu_sram_busy_r   <= 1'b0;
        gpu_sram_rvalid_r <= 1'b0;
        gpu_sram_delay    <= 2'd0;
        gpu_sram_op_read  <= 1'b0;
        gpu_sram_half_r   <= 1'b0;
        gpu_sram_hi_r     <= 1'b0;
        gpu_sram_addr_r   <= 16'd0;
        gpu_sram_wdata_r  <= 32'd0;
        gpu_sram_wstrb_r  <= 4'd0;
    end else begin
        gpu_sram_rvalid_r <= 1'b0;
        if (!gpu_sram_busy_r && (gpu_sram_rd || gpu_sram_wr)) begin
            gpu_sram_busy_r  <= 1'b1;
            gpu_sram_delay   <= 2'd2;
            gpu_sram_op_read <= gpu_sram_rd;
            gpu_sram_half_r  <= gpu_sram_rd && gpu_sram_rd_half;
            gpu_sram_hi_r    <= gpu_sram_rd_hi;
            gpu_sram_addr_r  <= gpu_sram_addr[15:0];
            gpu_sram_wdata_r <= gpu_sram_wdata;
            gpu_sram_wstrb_r <= gpu_sram_wstrb;
        end else if (gpu_sram_busy_r) begin
            if (gpu_sram_delay != 2'd0) begin
                gpu_sram_delay <= gpu_sram_delay - 2'd1;
            end else begin
                if (gpu_sram_op_read) begin
                    gpu_sram_rvalid_r <= 1'b1;
                end else begin
                    if (gpu_sram_wstrb_r[0]) gpu_sram_mem[gpu_sram_addr_r][7:0]   <= gpu_sram_wdata_r[7:0];
                    if (gpu_sram_wstrb_r[1]) gpu_sram_mem[gpu_sram_addr_r][15:8]  <= gpu_sram_wdata_r[15:8];
                    if (gpu_sram_wstrb_r[2]) gpu_sram_mem[gpu_sram_addr_r][23:16] <= gpu_sram_wdata_r[23:16];
                    if (gpu_sram_wstrb_r[3]) gpu_sram_mem[gpu_sram_addr_r][31:24] <= gpu_sram_wdata_r[31:24];
                end
                gpu_sram_busy_r <= 1'b0;
            end
        end
    end
end

// ============================================================
// Debug hoists for the C++ harness
// ============================================================
assign dbg_periph_araddr  = cpu_local_araddr;
assign dbg_periph_arvalid = cpu_local_arvalid;
assign dbg_sdram_araddr   = cpu_sdram_araddr;
assign dbg_sdram_arvalid  = cpu_sdram_arvalid;
assign dbg_cram0_araddr   = cpu_cram0_araddr;
assign dbg_cram0_arvalid  = cpu_cram0_arvalid;
assign dbg_periph_awaddr  = cpu_local_awaddr;
assign dbg_periph_awvalid = cpu_local_awvalid;
assign dbg_sdram_awaddr   = cpu_sdram_awaddr;
assign dbg_sdram_awvalid  = cpu_sdram_awvalid;
assign dbg_periph_state   = periph.state;
assign dbg_sdram_slave_state = sdram_axi_slave.state;
assign dbg_sdram_model_state = dbg_io_sd[3:0];
assign dbg_sdram_model_busy  = word_busy_sd;
assign dbg_arb_grant_cpu     = (sdram_arb.grant == 2'd1);
assign dbg_arb_state         = sdram_arb.arb_state;
assign dbg_cmd_forwarded     = cmd_forwarded;
assign dbg_accepted          = accepted_r;
assign dbg_word_rd_sd        = word_rd_sd;
assign dbg_word_wr_sd        = word_wr_sd;
assign dbg_word_addr_sd      = word_addr_sd;
assign dbg_word_burst_len_sd = word_burst_len_sd;
assign dbg_word_q_valid_sd   = word_q_valid_sd;

// CPU-boundary taps (hierarchical refs into cpu_system internals)
assign dbg_i_arvalid   = cpu_sys.i_arvalid_cpu;
assign dbg_i_arready   = cpu_sys.i_arready_cpu;
assign dbg_i_araddr    = cpu_sys.i_araddr_cpu;
assign dbg_i_arid      = cpu_sys.i_arid_cpu;
assign dbg_i_arlen     = cpu_sys.i_arlen_cpu;
assign dbg_i_rvalid    = cpu_sys.i_rvalid_cpu;
assign dbg_i_rready    = cpu_sys.i_rready_cpu;
assign dbg_i_rdata     = cpu_sys.i_rdata_cpu;
assign dbg_i_rid       = cpu_sys.i_rid_cpu;
assign dbg_i_rlast     = cpu_sys.i_rlast_cpu;
assign dbg_mem_arvalid = cpu_sys.mem_arvalid_cpu;
assign dbg_mem_arready = cpu_sys.mem_arready_cpu;
assign dbg_mem_araddr  = cpu_sys.mem_araddr_cpu;
assign dbg_mem_arid    = cpu_sys.mem_arid_cpu;
assign dbg_mem_arlen   = cpu_sys.mem_arlen_cpu;
assign dbg_mem_rvalid  = cpu_sys.mem_rvalid_cpu;
assign dbg_mem_rready  = cpu_sys.mem_rready_cpu;
assign dbg_mem_rdata   = cpu_sys.mem_rdata_cpu;
assign dbg_mem_rid     = cpu_sys.mem_rid_cpu;
assign dbg_mem_rlast   = cpu_sys.mem_rlast_cpu;
assign dbg_per_arvalid = cpu_sys.per_arvalid_cpu;
assign dbg_per_arready = cpu_sys.per_arready_cpu;
assign dbg_per_araddr  = cpu_sys.per_araddr_cpu;
assign dbg_per_arlen   = cpu_sys.per_arlen_cpu;
assign dbg_per_rvalid  = cpu_sys.per_rvalid_cpu;
assign dbg_per_rready  = cpu_sys.per_rready_cpu;
assign dbg_per_rdata   = cpu_sys.per_rdata_cpu;
assign dbg_per_rlast   = cpu_sys.per_rlast_cpu;
assign dbg_slave_rd    = sdram_rd;
assign dbg_slave_wr    = sdram_wr;

endmodule
