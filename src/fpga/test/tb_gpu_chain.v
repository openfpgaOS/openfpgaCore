//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

//
// tb_gpu_chain.v
//
// Reproduces the cr-gpu-and-tri-wedges issue 1 scenario in sim by
// instantiating the FULL CPU→slave write path:
//
//     C++ harness drives per_axi (post-shim, pre-slice)
//        ↓
//     5x axi_register_slice (AR / R / AW / W / B)
//        ↓
//     cpu_target_port (mem_*_select tied off; only per_* live)
//        ↓
//     axi_periph_slave (peripheral inputs tied off)
//        ↓
//     gpu_reg_wr / gpu_reg_addr / gpu_reg_wdata exposed to harness
//
// The C++ harness pumps the wedge sequence (~23 GPU_RING_DATA writes
// back-to-back, optionally with a read interleaved) and counts
// gpu_reg_wr pulses against writes submitted.  Any drop or stall
// surfaces as a failed test.
//
// The harness can either drive the slices directly (legacy per_axi
// mode) or drive the upstream LSU shim, which covers the posted-write
// FIFO + M2 coalescer path without dragging VexiiRiscv into this
// focused bench.
//

`default_nettype none

module tb_gpu_chain (
    input wire clk,
    input wire reset_n,

    // ============================================================
    // LSU master interface (driven by C++) — drives the upstream
    // side of lsu_axi_shim, mirroring what VexiiRiscv's LsuPlugin
    // bus presents in the production cpu_system instance.  When
    // USE_LSU_PATH=1 (driven from the harness via lsu_path_en),
    // these inputs steer the chain; when 0, the legacy per_axi
    // inputs below drive the slices directly (older test mode).
    // ============================================================
    input  wire        lsu_path_en,
    input  wire        lsu_cmd_valid,
    output wire        lsu_cmd_ready,
    input  wire        lsu_cmd_write,
    input  wire [31:0] lsu_cmd_addr,
    input  wire [31:0] lsu_cmd_data,
    input  wire [1:0]  lsu_cmd_size,
    input  wire [3:0]  lsu_cmd_mask,
    output wire        lsu_rsp_valid,
    output wire        lsu_rsp_error,
    output wire [31:0] lsu_rsp_data,

    // ============================================================
    // Legacy per_axi master interface (driven by C++ — bypass shim)
    // Used only when lsu_path_en=0; otherwise the shim's outputs win.
    // ============================================================
    input  wire        s_per_arvalid,
    output wire        s_per_arready,
    input  wire [31:0] s_per_araddr,
    input  wire [7:0]  s_per_arlen,
    input  wire [2:0]  s_per_arsize,
    input  wire [1:0]  s_per_arburst,

    output wire        s_per_rvalid,
    input  wire        s_per_rready,
    output wire [31:0] s_per_rdata,
    output wire [1:0]  s_per_rresp,
    output wire        s_per_rlast,

    input  wire        s_per_awvalid,
    output wire        s_per_awready,
    input  wire [31:0] s_per_awaddr,
    input  wire [7:0]  s_per_awlen,
    input  wire [2:0]  s_per_awsize,
    input  wire [1:0]  s_per_awburst,

    input  wire        s_per_wvalid,
    output wire        s_per_wready,
    input  wire [31:0] s_per_wdata,
    input  wire [3:0]  s_per_wstrb,
    input  wire        s_per_wlast,

    output wire        s_per_bvalid,
    input  wire        s_per_bready,
    output wire [1:0]  s_per_bresp,

    // ============================================================
    // Observability for the harness.
    // ============================================================
    output wire        dbg_gpu_reg_wr,
    output wire [3:0]  dbg_gpu_reg_addr,
    output wire [31:0] dbg_gpu_reg_wdata,

    // Visibility into the write-side FSM in cpu_target_port to help
    // the harness explain a stall (which state is stuck).
    output wire [1:0]  dbg_wr_state,

    // Slave's wready / awready pulses — useful to cross-check that
    // backpressure isn't sourcing in the slave.
    output wire        dbg_slave_awready,
    output wire        dbg_slave_wready,
    output wire        dbg_slave_bvalid,

    // Shim internals (lsu path debug).
    output wire [2:0]  dbg_shim_count,
    output wire        dbg_shim_aw_sent,
    output wire        dbg_shim_w_sent,
    output wire [1:0]  dbg_shim_burst_awlen,
    output wire        dbg_shim_per_awvalid,
    output wire        dbg_shim_per_wvalid,
    output wire        dbg_shim_b_xfer
);

// ----------------------------------------------------------------
// Slice outputs (cpu side → target_port side).  Names mirror
// cpu_system.v exactly so it's easy to follow.
// ----------------------------------------------------------------
wire        per_arvalid; wire        per_arready;
wire [31:0] per_araddr;  wire [7:0]  per_arlen;

wire        per_rvalid;  wire        per_rready;
wire [31:0] per_rdata;   wire [1:0]  per_rresp;
wire        per_rlast;

wire        per_awvalid; wire        per_awready;
wire [31:0] per_awaddr;  wire [7:0]  per_awlen;
wire [1:0]  per_awburst;

wire        per_wvalid;  wire        per_wready;
wire [31:0] per_wdata;   wire [3:0]  per_wstrb;
wire        per_wlast;

wire        per_bvalid;  wire        per_bready;
wire [1:0]  per_bresp;

// ----------------------------------------------------------------
// LSU shim — drives the slice s_payload side when lsu_path_en=1.
// When lsu_path_en=0, the harness drives the legacy per_axi master
// interface directly (bypasses the shim).  This lets the same
// testbench cover both layers: with the shim (Test C/D) we exercise
// the posted FIFO + M2 coalescer, without it (legacy A/B) we
// isolate the slice / target_port / slave path.
// ----------------------------------------------------------------
wire        shim_arvalid;
wire [31:0] shim_araddr;
wire [7:0]  shim_arlen;
wire        shim_arready;

wire        shim_rready;
wire        shim_rvalid;
wire [31:0] shim_rdata;
wire [1:0]  shim_rresp;
wire        shim_rlast;

wire        shim_awvalid;
wire [31:0] shim_awaddr;
wire [7:0]  shim_awlen;
wire [1:0]  shim_awburst;
wire        shim_awready;

wire        shim_wvalid;
wire [31:0] shim_wdata;
wire [3:0]  shim_wstrb;
wire        shim_wlast;
wire        shim_wready;

wire        shim_bvalid;
wire [1:0]  shim_bresp;
wire        shim_bready;

lsu_axi_shim u_shim (
    .clk(clk), .reset(~reset_n),

    .lsu_cmd_valid(lsu_cmd_valid & lsu_path_en),
    .lsu_cmd_ready(lsu_cmd_ready),
    .lsu_cmd_write(lsu_cmd_write),
    .lsu_cmd_addr (lsu_cmd_addr),
    .lsu_cmd_data (lsu_cmd_data),
    .lsu_cmd_mask (lsu_cmd_mask),
    .lsu_rsp_valid(lsu_rsp_valid),
    .lsu_rsp_error(lsu_rsp_error),
    .lsu_rsp_data (lsu_rsp_data),

    .per_arvalid_cpu(shim_arvalid), .per_arready_cpu(shim_arready),
    .per_araddr_cpu (shim_araddr),  .per_arlen_cpu  (shim_arlen),
    .per_rvalid_cpu (shim_rvalid),  .per_rready_cpu (shim_rready),
    .per_rdata_cpu  (shim_rdata),   .per_rresp_cpu  (shim_rresp),
    .per_rlast_cpu  (shim_rlast),

    .per_awvalid_cpu(shim_awvalid), .per_awready_cpu(shim_awready),
    .per_awaddr_cpu (shim_awaddr),  .per_awlen_cpu  (shim_awlen),
    .per_awburst_cpu(shim_awburst),
    .per_wvalid_cpu (shim_wvalid),  .per_wready_cpu (shim_wready),
    .per_wdata_cpu  (shim_wdata),   .per_wstrb_cpu  (shim_wstrb),
    .per_wlast_cpu  (shim_wlast),
    .per_bvalid_cpu (shim_bvalid),  .per_bready_cpu (shim_bready),
    .per_bresp_cpu  (shim_bresp)
);

// Mux selects between LSU shim (when lsu_path_en=1) and legacy
// per_axi (lsu_path_en=0) for the slice s_payload inputs.
wire        slice_arvalid_in = lsu_path_en ? shim_arvalid : s_per_arvalid;
wire [31:0] slice_araddr_in  = lsu_path_en ? shim_araddr  : s_per_araddr;
wire [7:0]  slice_arlen_in   = lsu_path_en ? shim_arlen   : s_per_arlen;

wire        slice_awvalid_in = lsu_path_en ? shim_awvalid : s_per_awvalid;
wire [31:0] slice_awaddr_in  = lsu_path_en ? shim_awaddr  : s_per_awaddr;
wire [7:0]  slice_awlen_in   = lsu_path_en ? shim_awlen   : s_per_awlen;
wire [1:0]  slice_awburst_in = lsu_path_en ? shim_awburst : s_per_awburst;

wire        slice_wvalid_in  = lsu_path_en ? shim_wvalid  : s_per_wvalid;
wire [31:0] slice_wdata_in   = lsu_path_en ? shim_wdata   : s_per_wdata;
wire [3:0]  slice_wstrb_in   = lsu_path_en ? shim_wstrb   : s_per_wstrb;
wire        slice_wlast_in   = lsu_path_en ? shim_wlast   : s_per_wlast;

wire        slice_rready_in  = lsu_path_en ? shim_rready  : s_per_rready;
wire        slice_bready_in  = lsu_path_en ? shim_bready  : s_per_bready;

// Steer back: slice s_ready outputs go to whichever side is driving.
wire        slice_arready_out;
wire        slice_awready_out;
wire        slice_wready_out;
assign shim_arready  =  lsu_path_en ? slice_arready_out : 1'b0;
assign shim_awready  =  lsu_path_en ? slice_awready_out : 1'b0;
assign shim_wready   =  lsu_path_en ? slice_wready_out  : 1'b0;
assign s_per_arready = !lsu_path_en ? slice_arready_out : 1'b0;
assign s_per_awready = !lsu_path_en ? slice_awready_out : 1'b0;
assign s_per_wready  = !lsu_path_en ? slice_wready_out  : 1'b0;

// AR slice
axi_register_slice #(.W(40)) per_ar_slice (
    .clk(clk), .reset_n(reset_n),
    .s_valid (slice_arvalid_in), .s_ready (slice_arready_out),
    .s_payload({slice_araddr_in, slice_arlen_in}),
    .m_valid (per_arvalid),      .m_ready (per_arready),
    .m_payload({per_araddr,      per_arlen})
);

// R / B slices: m_* is the master-side (downstream of slice).  Both
// the legacy harness (s_per_*) and the LSU shim need to see this
// side, so route through internal wires and mux to whichever consumer
// is active.
wire        r_master_valid;
wire [31:0] r_master_data;
wire [1:0]  r_master_resp;
wire        r_master_last;

axi_register_slice #(.W(35)) per_r_slice (
    .clk(clk), .reset_n(reset_n),
    .s_valid (per_rvalid),      .s_ready (per_rready),
    .s_payload({per_rdata,      per_rresp,      per_rlast}),
    .m_valid (r_master_valid),  .m_ready (slice_rready_in),
    .m_payload({r_master_data,  r_master_resp,  r_master_last})
);

assign s_per_rvalid = !lsu_path_en ? r_master_valid : 1'b0;
assign s_per_rdata  =  r_master_data;
assign s_per_rresp  =  r_master_resp;
assign s_per_rlast  =  r_master_last;
assign shim_rvalid  =  lsu_path_en ? r_master_valid : 1'b0;
assign shim_rdata   =  r_master_data;
assign shim_rresp   =  r_master_resp;
assign shim_rlast   =  r_master_last;

axi_register_slice #(.W(42)) per_aw_slice (
    .clk(clk), .reset_n(reset_n),
    .s_valid (slice_awvalid_in), .s_ready (slice_awready_out),
    .s_payload({slice_awaddr_in, slice_awlen_in, slice_awburst_in}),
    .m_valid (per_awvalid),      .m_ready (per_awready),
    .m_payload({per_awaddr,      per_awlen,      per_awburst})
);

axi_register_slice #(.W(37)) per_w_slice (
    .clk(clk), .reset_n(reset_n),
    .s_valid (slice_wvalid_in), .s_ready (slice_wready_out),
    .s_payload({slice_wdata_in, slice_wstrb_in,  slice_wlast_in}),
    .m_valid (per_wvalid),      .m_ready (per_wready),
    .m_payload({per_wdata,      per_wstrb,      per_wlast})
);

wire        b_master_valid;
wire [1:0]  b_master_resp;

axi_register_slice #(.W(2)) per_b_slice (
    .clk(clk), .reset_n(reset_n),
    .s_valid (per_bvalid),       .s_ready (per_bready),
    .s_payload({per_bresp}),
    .m_valid (b_master_valid),   .m_ready (slice_bready_in),
    .m_payload({b_master_resp})
);

assign s_per_bvalid = !lsu_path_en ? b_master_valid : 1'b0;
assign s_per_bresp  =  b_master_resp;
assign shim_bvalid  =  lsu_path_en ? b_master_valid : 1'b0;
assign shim_bresp   =  b_master_resp;

// ----------------------------------------------------------------
// cpu_target_port — only per_* live.  Mem and i tied off.
// ----------------------------------------------------------------

// Slave-side wires (cpu_target_port → axi_periph_slave).
wire        m_arvalid;   wire        m_arready;
wire [31:0] m_araddr;    wire [7:0]  m_arlen;
wire        m_rvalid;    wire        m_rready;
wire [31:0] m_rdata;     wire [1:0]  m_rresp;  wire m_rlast;

wire        m_awvalid;   wire        m_awready;
wire [31:0] m_awaddr;    wire [7:0]  m_awlen;  wire [1:0] m_awburst;
wire        m_wvalid;    wire        m_wready;
wire [31:0] m_wdata;     wire [3:0]  m_wstrb;  wire m_wlast;

wire        m_bvalid;    wire [1:0]  m_bresp;

// per_*_*_contrib outputs from the port — wire directly to per_axi
// since this is the only target port in the bench.
wire local_per_arready, local_per_rvalid;
wire [31:0] local_per_rdata; wire [1:0] local_per_rresp; wire local_per_rlast;
wire local_per_awready, local_per_wready, local_per_bvalid;
wire [1:0] local_per_bresp;

// Decode: target = local for any per_axi access (the slave covers
// the full 32-bit space in this bench).
wire per_ar_is_local = per_arvalid;   // gated only by valid
wire per_aw_is_local = per_awvalid;

cpu_target_port port_local (
    .clk    (clk),
    .reset_n(reset_n),

    .i_rd_select  (1'b0),
    .mem_rd_select(1'b0),
    .per_rd_select(per_ar_is_local),
    .mem_wr_select(1'b0),
    .per_wr_select(per_aw_is_local),

    // i_axi tied off
    .i_arvalid (1'b0), .i_arready_contrib (),
    .i_araddr  (32'b0), .i_arid (1'b0), .i_arlen (8'b0),
    .i_rvalid_contrib  (),
    .i_rdata_contrib   (), .i_rid_contrib (), .i_rresp_contrib (),
    .i_rlast_contrib   (),
    .i_rready  (1'b0),

    // mem_axi tied off
    .mem_arvalid (1'b0), .mem_arready_contrib (),
    .mem_araddr  (32'b0), .mem_arid (2'b0), .mem_arlen (8'b0),
    .mem_rvalid_contrib  (),
    .mem_rdata_contrib   (), .mem_rid_contrib (), .mem_rresp_contrib (),
    .mem_rlast_contrib   (),
    .mem_rready  (1'b0),
    .mem_awvalid (1'b0), .mem_awready_contrib (),
    .mem_awaddr  (32'b0), .mem_awid (2'b0), .mem_awlen (8'b0),
    .mem_awburst (2'b01),
    .mem_wvalid  (1'b0), .mem_wready_contrib (),
    .mem_wdata   (32'b0), .mem_wstrb (4'b0), .mem_wlast (1'b0),
    .mem_bvalid_contrib  (),
    .mem_bid_contrib     (), .mem_bresp_contrib (),
    .mem_bready  (1'b0),

    // per_axi live
    .per_arvalid         (per_arvalid),
    .per_arready_contrib (local_per_arready),
    .per_araddr          (per_araddr),
    .per_arlen           (per_arlen),
    .per_rvalid_contrib  (local_per_rvalid),
    .per_rdata_contrib   (local_per_rdata),
    .per_rresp_contrib   (local_per_rresp),
    .per_rlast_contrib   (local_per_rlast),
    .per_rready          (per_rready),
    .per_awvalid         (per_awvalid),
    .per_awready_contrib (local_per_awready),
    .per_awaddr          (per_awaddr),
    .per_awlen           (per_awlen),
    .per_awburst         (per_awburst),
    .per_wvalid          (per_wvalid),
    .per_wready_contrib  (local_per_wready),
    .per_wdata           (per_wdata),
    .per_wstrb           (per_wstrb),
    .per_wlast           (per_wlast),
    .per_bvalid_contrib  (local_per_bvalid),
    .per_bresp_contrib   (local_per_bresp),
    .per_bready          (per_bready),

    // To the slave
    .m_arvalid (m_arvalid), .m_arready (m_arready),
    .m_araddr  (m_araddr),  .m_arlen   (m_arlen),
    .m_rvalid  (m_rvalid),  .m_rready  (m_rready),
    .m_rdata   (m_rdata),   .m_rresp   (m_rresp),
    .m_rlast   (m_rlast),
    .m_awvalid (m_awvalid), .m_awready (m_awready),
    .m_awaddr  (m_awaddr),  .m_awlen   (m_awlen),
    .m_awburst (m_awburst),
    .m_wvalid  (m_wvalid),  .m_wready  (m_wready),
    .m_wdata   (m_wdata),   .m_wstrb   (m_wstrb),
    .m_wlast   (m_wlast),
    .m_bvalid  (m_bvalid),  .m_bresp   (m_bresp),

    .i_rd_busy(),  .mem_rd_busy(), .mem_wr_busy(),
    .per_rd_busy(),.per_wr_busy()
);

// Single target ⇒ contribution drives per_*ready / per_rvalid / etc.
assign per_arready = local_per_arready;
assign per_rvalid  = local_per_rvalid;
assign per_rdata   = local_per_rdata;
assign per_rresp   = local_per_rresp;
assign per_rlast   = local_per_rlast;
assign per_awready = local_per_awready;
assign per_wready  = local_per_wready;
assign per_bvalid  = local_per_bvalid;
assign per_bresp   = local_per_bresp;

// ----------------------------------------------------------------
// axi_periph_slave — peripheral inputs tied off, GPU regs exposed.
// ----------------------------------------------------------------
wire        gpu_reg_wr_w;
wire [3:0]  gpu_reg_addr_w;
wire [31:0] gpu_reg_wdata_w;
reg  [31:0] gpu_reg_rdata;
always @(*) begin
    case (gpu_reg_addr_w)
        4'd1:  gpu_reg_rdata = 32'h1111_1111;
        4'd4:  gpu_reg_rdata = 32'h4444_4444;
        4'd5:  gpu_reg_rdata = 32'h5555_5555;
        4'd6:  gpu_reg_rdata = 32'h6666_6666;
        default: gpu_reg_rdata = 32'h0;
    endcase
end

assign dbg_gpu_reg_wr    = gpu_reg_wr_w;
assign dbg_gpu_reg_addr  = gpu_reg_addr_w;
assign dbg_gpu_reg_wdata = gpu_reg_wdata_w;

axi_periph_slave dut (
    .clk    (clk),
    .reset_n(reset_n),

    .s_axi_arvalid(m_arvalid), .s_axi_arready(m_arready),
    .s_axi_araddr (m_araddr),  .s_axi_arlen  (m_arlen),
    .s_axi_rvalid (m_rvalid),  .s_axi_rready (m_rready),
    .s_axi_rdata  (m_rdata),   .s_axi_rresp  (m_rresp),
    .s_axi_rlast  (m_rlast),
    .s_axi_awvalid(m_awvalid), .s_axi_awready(m_awready),
    .s_axi_awaddr (m_awaddr),  .s_axi_awlen  (m_awlen),
    .s_axi_awburst(m_awburst),
    .s_axi_wvalid (m_wvalid),  .s_axi_wready (m_wready),
    .s_axi_wdata  (m_wdata),   .s_axi_wstrb  (m_wstrb),
    .s_axi_wlast  (m_wlast),
    .s_axi_bvalid (m_bvalid),  .s_axi_bready (1'b1),
    .s_axi_bresp  (m_bresp),

    .dataslot_allcomplete(1'b1),
    .vsync               (1'b0),
    .early_vblank        (1'b0),
    .os_inmenu           (1'b0),
    .cont1_key           (32'b0),
    .cont1_joy           (32'b0),
    .cont1_trig          (16'b0),
    .cont2_key           (32'b0),
    .cont2_joy           (32'b0),
    .cont2_trig          (16'b0),
    .cont3_key           (32'b0),
    .cont3_joy           (32'b0),
    .cont3_trig          (16'b0),
    .cont4_key           (32'b0),
    .cont4_joy           (32'b0),
    .cont4_trig          (16'b0),
    .target_dataslot_ack (1'b0),
    .target_dataslot_done(1'b0),
    .target_dataslot_err (3'b0),
    .hps_status          (32'd0),
    .hps_img_size        (64'd0),
    .hps_boot_len        (32'd0),
    .hps_img1_size       (64'd0),
    .hps_img2_size       (64'd0),
    .bridge_wr_idle      (1'b1),
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

    .audio_fifo_level (9'b0),
    .audio_fifo_full  (1'b0),

    .link_reg_wr   (),
    .link_reg_rd   (),
    .link_reg_addr (),
    .link_reg_wdata(),
    .link_reg_rdata(32'b0),

    .save_dt_slot   (),
    .save_dt_size   (),
    .save_dt_commit (),

    .uart_tx_dv    (),
    .uart_tx_byte  (),
    .uart_tx_active(1'b0),
    .uart_rx_dv    (1'b0),
    .uart_rx_byte  (8'b0),

    .app_id         (32'b0),
    .analogizer_settings(32'b0),
    .analogizer_hoffset (32'b0),
    .analogizer_voffset (32'b0),
    .analogizer_cpu_wr_toggle(),
    .analogizer_cpu_wr_settings(),
    .analogizer_cpu_wr_hoffset(),
    .analogizer_cpu_wr_voffset(),
    .shutdown_pending(1'b0),
    .shutdown_ack   (),

    .timer_irq  (),
    .uart_rx_irq(),

    .link_irq        (1'b0),
    .ext_irq         (),

    .dt_query_addr  (),
    .dt_query_toggle(),
    .dt_query_data  (32'b0),
    .dt_query_valid (1'b0),

    .vrr_v_total(),
    .analogizer_enabled(1'b0),

    .snac_pin_out(),
    .snac_pin_dir(),
    .snac_pin_in (8'b0),
    .snac_enable (),

    .gpu_reg_wr   (gpu_reg_wr_w),
    .gpu_reg_addr (gpu_reg_addr_w),
    .gpu_reg_wdata(gpu_reg_wdata_w),
    .gpu_reg_rdata(gpu_reg_rdata),
    .gpu_swap_req (1'b0),
    .gpu_swap_idx (2'b0)
);

// Hoist write-FSM state for harness debug.
assign dbg_wr_state      = port_local.wr_state;
assign dbg_slave_awready = m_awready;
assign dbg_slave_wready  = m_wready;
assign dbg_slave_bvalid  = m_bvalid;

// Shim internals.
assign dbg_shim_count       = u_shim.wr_count;
assign dbg_shim_aw_sent     = u_shim.lsu_aw_sent;
assign dbg_shim_w_sent      = u_shim.lsu_w_sent;
assign dbg_shim_burst_awlen = u_shim.burst_awlen;
assign dbg_shim_per_awvalid = shim_awvalid;
assign dbg_shim_per_wvalid  = shim_wvalid;
assign dbg_shim_b_xfer      = b_master_valid & shim_bready;

endmodule

`default_nettype wire
