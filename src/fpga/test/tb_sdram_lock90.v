//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------
//
// tb_sdram_lock90 — MiSTer 90 MHz boot-freeze convergence bench.
//
// HW context (2026-08-25, SS1 at 90 MHz + legal VCO): the bootloader's
// uncached SDRAM alias test freezes deterministically-per-boot.rom-content
// while the HPS streams staging DMA, with boot-console scanout + the
// ddr3_fb frame copy both live; the identical bitstream passes when the
// stream timing shifts.  Timing is closed (WNS +0.847) → logic-level
// arbitration/livelock, interleave-dependent.
//
// This bench wires the COMPLETE production SDRAM contention topology —
// one stage more than tb_sdram_cont: the video_burst_arb (ddr3_fb.sv)
// two-client arbiter now sits between the scanout/frame-DMA clients and
// io_sdram's burst port, exactly as in emu.sv:
//
//   M0..M3 AXI  -> axi_sdram_arbiter -> axi_sdram_slave -> (pulse adapter)
//                                                        -> io_sdram word port
//   scanout (V) ---\
//                   video_burst_arb  ------------------> io_sdram burst port
//   frame DMA (D) -/
//
// The C++ driver shapes traffic to the hardware-observed freeze scenario
// (serialized CPU singles vs bridge 8x16-beat drains vs real-time video)
// and issues a LIVENESS verdict per pacing combination.
//
// Additive, test-only.  REFRESH_INTERVAL is a parameter so one source
// serves the 90 MHz config (660) and the 100 MHz control (736) via -G.
//
`default_nettype none

module tb_sdram_lock90 #(
    parameter BANK_ROW_TRACK   = 1,
    // W candidate 3 (INCLUDE_SDRAM_WHOLE_BURSTS): 1 = drain whole GPU AW bursts
    parameter GPU_WQ_WHOLE_BURST = 0,
    // W candidate 2 (INCLUDE_SDRAM_MASKED_BURSTS): 1 = masked GPU queue beats join bursts
    parameter GPU_WQ_MASKED_MERGE = 0,
    // W candidate 1 (INCLUDE_SDRAM_WRITE_PIPE): 1 = early write ack + commit B
    parameter SDRAM_WRITE_PIPE = 0,
    parameter REFRESH_INTERVAL = 10'd736
) (
    input  wire        clk,
    input  wire        reset_n,

    // ---- M0: GPU (merged read + posted write) ----
    input  wire        m0_arvalid,
    output wire        m0_arready,
    input  wire [31:0] m0_araddr,
    input  wire [7:0]  m0_arlen,
    output wire        m0_rvalid,
    output wire [31:0] m0_rdata,
    output wire        m0_rlast,
    input  wire        m0_awvalid,
    output wire        m0_awready,
    input  wire [31:0] m0_awaddr,
    input  wire [7:0]  m0_awlen,
    input  wire        m0_wvalid,
    output wire        m0_wready,
    input  wire [31:0] m0_wdata,
    input  wire [3:0]  m0_wstrb,
    input  wire        m0_wlast,
    output wire        m0_bvalid,

    // ---- M1: CPU (read + write) ----
    input  wire        m1_arvalid,
    output wire        m1_arready,
    input  wire [31:0] m1_araddr,
    input  wire [7:0]  m1_arlen,
    output wire        m1_rvalid,
    output wire [31:0] m1_rdata,
    output wire        m1_rlast,
    input  wire        m1_rready,
    input  wire        m1_awvalid,
    output wire        m1_awready,
    input  wire [31:0] m1_awaddr,
    input  wire [7:0]  m1_awlen,
    input  wire        m1_wvalid,
    output wire        m1_wready,
    input  wire [31:0] m1_wdata,
    input  wire [3:0]  m1_wstrb,
    input  wire        m1_wlast,
    output wire        m1_bvalid,

    // ---- M2: bridge file DMA (read + write) ----
    // m2_src_bridge=0: these external hand-model ports drive arbiter M2.
    // m2_src_bridge=1: the REAL hps_bridge (fed via the ioctl ports below)
    // drives M2 and the external ports are ignored.
    input  wire        m2_src_bridge,
    input  wire        m2_arvalid,
    output wire        m2_arready,
    input  wire [31:0] m2_araddr,
    input  wire [7:0]  m2_arlen,
    output wire        m2_rvalid,
    output wire [31:0] m2_rdata,
    output wire        m2_rlast,
    input  wire        m2_rready,
    input  wire        m2_awvalid,
    output wire        m2_awready,
    input  wire [31:0] m2_awaddr,
    input  wire [7:0]  m2_awlen,
    input  wire        m2_wvalid,
    output wire        m2_wready,
    input  wire [31:0] m2_wdata,
    input  wire [3:0]  m2_wstrb,
    input  wire        m2_wlast,
    output wire        m2_bvalid,

    // ---- Real hps_bridge ioctl feed (boot.rom download model) ----
    input  wire        ioctl_download,
    input  wire [15:0] ioctl_index,
    input  wire        ioctl_wr,
    input  wire [15:0] ioctl_dout,
    output wire        ioctl_wait,
    output wire        boot_rom_loaded,
    output wire [3:0]  dbg_brg_state,

    // ---- M3: Audio mixer (read-only) ----
    input  wire        m3_arvalid,
    output wire        m3_arready,
    input  wire [31:0] m3_araddr,
    input  wire [7:0]  m3_arlen,
    output wire        m3_rvalid,
    output wire [31:0] m3_rdata,
    output wire        m3_rlast,
    input  wire        m3_rready,

    // ---- Video scanout client (video_burst_arb client V, priority) ----
    input  wire        vid_rd,
    input  wire [24:0] vid_addr,
    input  wire [10:0] vid_len,
    output wire        vid_data_valid,
    output wire        vid_data_done,

    // ---- Frame-DMA client (video_burst_arb client D, ddr3_fb-shaped) ----
    input  wire        dma_req,
    input  wire [24:0] dma_addr,
    input  wire [10:0] dma_len,
    output wire        dma_gnt,
    output wire        dma_data_valid,
    output wire        dma_data_done,

    // ---- Diagnostics ----
    output wire [4:0]  dbg_gpu_wq_count,
    output wire        dbg_gpu_wq_full,
    output wire [1:0]  dbg_arb_state,
    output wire [1:0]  dbg_grant,
    output wire [7:0]  dbg_io,
    output wire        dbg_busy,
    output wire [1:0]  dbg_vb_owner,
    output wire        dbg_word_queue_pending,
    output wire [3:0]  dbg_slave_state,
    output wire [7:0]  dbg_wlast_err
);

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

// ============================================================
// Slave <-> io_sdram word interface
// ============================================================
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
wire        ram1_word_queue_pending;
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

assign dbg_busy = ram1_word_busy;
assign dbg_word_queue_pending = ram1_word_queue_pending;

// ============================================================
// Slave -> io_sdram pulse adapter (byte-identical to production)
// ============================================================
reg sdram_accepted_r;
reg sdram_cmd_forwarded;
always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        ram1_word_rd <= 0; ram1_word_wr <= 0;
        ram1_word_addr <= 0; ram1_word_data <= 0; ram1_word_wstrb <= 0;
        ram1_word_data_next <= 0; ram1_word_wstrb_next <= 0;
        ram1_word_burst_len <= 0; ram1_word_burst_wr_len <= 0;
        sdram_accepted_r <= 0; sdram_cmd_forwarded <= 0;
    end else begin
        ram1_word_rd <= 0;
        ram1_word_wr <= 0;
        ram1_word_burst_len <= 4'd0;
        ram1_word_burst_wr_len <= 4'd0;
        sdram_accepted_r <= 0;

        if (!sdram_slave_rd && !sdram_slave_wr)
            sdram_cmd_forwarded <= 0;

        if (!ram1_word_queue_pending && !sdram_cmd_forwarded &&
            (sdram_slave_rd || sdram_slave_wr)) begin
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
// DUT 0: REAL hps_bridge (production, unmodified) — the boot.rom /
// F-load ioctl ingest + sector engine that owns arbiter M2 in emu.sv.
// Fed by the C++ ioctl model; muxed onto M2 when m2_src_bridge=1.
// Production ties reset_n=1 (never reset); the bench resets it per
// combo so wedge trials are independent.
// ============================================================
wire        brg_awvalid, brg_awready;
wire [31:0] brg_awaddr;
wire [7:0]  brg_awlen;
wire        brg_wvalid, brg_wready;
wire [31:0] brg_wdata;
wire [3:0]  brg_wstrb;
wire        brg_wlast;
wire        brg_bvalid;
wire        brg_arvalid, brg_arready;
wire [31:0] brg_araddr;
wire [7:0]  brg_arlen;
wire        brg_rvalid_i, brg_rready;
wire [31:0] brg_rdata_i;
wire        brg_rlast_i;

hps_bridge u_bridge (
    .clk(clk),
    .reset_n(reset_n),

    .ioctl_download(ioctl_download),
    .ioctl_index(ioctl_index),
    .ioctl_wr(ioctl_wr),
    .ioctl_dout(ioctl_dout),
    .ioctl_wait(ioctl_wait),

    .img_mounted(3'b000),
    .img_readonly(1'b0),
    .img_size(64'd0),
    .sd_lba0(), .sd_lba1(), .sd_lba2(),
    .sd_rd(), .sd_wr(),
    .sd_ack(3'b000),
    .sd_buff_addr(8'd0),
    .sd_buff_dout(16'd0),
    .sd_buff_din(),
    .sd_buff_wr(1'b0),

    .timestamp(33'd0),
    .joystick_0(32'd0), .joystick_1(32'd0),
    .joystick_l_analog_0(16'd0), .joystick_r_analog_0(16'd0),
    .joystick_l_analog_1(16'd0), .joystick_r_analog_1(16'd0),

    .target_dataslot_read(1'b0),
    .target_dataslot_write(1'b0),
    .target_dataslot_openfile(1'b0),
    .target_dataslot_getfile(1'b0),
    .target_dataslot_id(16'd0),
    .target_dataslot_slotoffset(32'd0),
    .target_dataslot_bridgeaddr(32'd0),
    .target_dataslot_length(32'd0),
    .target_dataslot_ack(),
    .target_dataslot_done(),
    .target_dataslot_err(),
    .bridge_wr_idle(),
    .pause_req(1'b0),
    .pause_quiet(),

    .hps_status(), .hps_img_size(), .hps_img1_size(), .hps_img2_size(),
    .hps_boot_len(), .hps_ini_len(), .hps_elf_len(),
    .boot_rom_loaded(boot_rom_loaded),

    .cont1_key(), .cont1_joy(), .cont1_trig(),
    .cont2_key(), .cont2_joy(), .cont2_trig(),
    .rtc_epoch_seconds(), .rtc_valid(),

    .m_awvalid(brg_awvalid), .m_awready(brg_awready),
    .m_awaddr(brg_awaddr),   .m_awlen(brg_awlen),
    .m_wvalid(brg_wvalid),   .m_wready(brg_wready),
    .m_wdata(brg_wdata),     .m_wstrb(brg_wstrb),
    .m_wlast(brg_wlast),
    .m_bvalid(brg_bvalid),   .m_bresp(2'b00),
    .m_arvalid(brg_arvalid), .m_arready(brg_arready),
    .m_araddr(brg_araddr),   .m_arlen(brg_arlen),
    .m_rvalid(brg_rvalid_i), .m_rdata(brg_rdata_i),
    .m_rresp(2'b00),         .m_rlast(brg_rlast_i),
    .m_rready(brg_rready)
);

assign dbg_brg_state = u_bridge.state;

// ---- M2 source mux (external hand model vs real bridge) ----
wire        m2_mux_arvalid = m2_src_bridge ? brg_arvalid : m2_arvalid;
wire [31:0] m2_mux_araddr  = m2_src_bridge ? brg_araddr  : m2_araddr;
wire [7:0]  m2_mux_arlen   = m2_src_bridge ? brg_arlen   : m2_arlen;
wire        m2_mux_rready  = m2_src_bridge ? brg_rready  : m2_rready;
wire        m2_mux_awvalid = m2_src_bridge ? brg_awvalid : m2_awvalid;
wire [31:0] m2_mux_awaddr  = m2_src_bridge ? brg_awaddr  : m2_awaddr;
wire [7:0]  m2_mux_awlen   = m2_src_bridge ? brg_awlen   : m2_awlen;
wire        m2_mux_wvalid  = m2_src_bridge ? brg_wvalid  : m2_wvalid;
wire [31:0] m2_mux_wdata   = m2_src_bridge ? brg_wdata   : m2_wdata;
wire [3:0]  m2_mux_wstrb   = m2_src_bridge ? brg_wstrb   : m2_wstrb;
wire        m2_mux_wlast   = m2_src_bridge ? brg_wlast   : m2_wlast;
wire        m2_mux_arready, m2_mux_awready, m2_mux_wready;
wire        m2_mux_rvalid, m2_mux_rlast, m2_mux_bvalid;
wire [31:0] m2_mux_rdata;

assign m2_arready = m2_src_bridge ? 1'b0 : m2_mux_arready;
assign m2_awready = m2_src_bridge ? 1'b0 : m2_mux_awready;
assign m2_wready  = m2_src_bridge ? 1'b0 : m2_mux_wready;
assign m2_rvalid  = m2_src_bridge ? 1'b0 : m2_mux_rvalid;
assign m2_rdata   = m2_mux_rdata;
assign m2_rlast   = m2_src_bridge ? 1'b0 : m2_mux_rlast;
assign m2_bvalid  = m2_src_bridge ? 1'b0 : m2_mux_bvalid;

assign brg_arready  = m2_src_bridge ? m2_mux_arready : 1'b0;
assign brg_awready  = m2_src_bridge ? m2_mux_awready : 1'b0;
assign brg_wready   = m2_src_bridge ? m2_mux_wready  : 1'b0;
assign brg_rvalid_i = m2_src_bridge ? m2_mux_rvalid  : 1'b0;
assign brg_rdata_i  = m2_mux_rdata;
assign brg_rlast_i  = m2_src_bridge ? m2_mux_rlast   : 1'b0;
assign brg_bvalid   = m2_src_bridge ? m2_mux_bvalid  : 1'b0;

// ============================================================
// DUT 1: AXI SDRAM arbiter (production, unmodified)
// ============================================================
wire arb_s_bcommit;
axi_sdram_arbiter #(.GPU_COMMIT_B(SDRAM_WRITE_PIPE), .GPU_WQ_MASKED_MERGE(GPU_WQ_MASKED_MERGE), .GPU_WQ_WHOLE_BURST(GPU_WQ_WHOLE_BURST)) sdram_arb (
    .clk(clk),
    .reset_n(reset_n),

    .m0_arvalid(m0_arvalid), .m0_arready(m0_arready),
    .m0_araddr(m0_araddr),   .m0_arlen(m0_arlen),
    .m0_rvalid(m0_rvalid),   .m0_rdata(m0_rdata),
    .m0_rresp(),             .m0_rlast(m0_rlast),
    .m0_awvalid(m0_awvalid), .m0_awready(m0_awready),
    .m0_awaddr(m0_awaddr),   .m0_awlen(m0_awlen),
    .m0_wvalid(m0_wvalid),   .m0_wready(m0_wready),
    .m0_wdata(m0_wdata),     .m0_wstrb(m0_wstrb),
    .m0_wlast(m0_wlast),
    .m0_bvalid(m0_bvalid),   .m0_bresp(),

    .m1_arvalid(m1_arvalid), .m1_arready(m1_arready),
    .m1_araddr(m1_araddr),   .m1_arlen(m1_arlen),
    .m1_rvalid(m1_rvalid),   .m1_rdata(m1_rdata),
    .m1_rresp(),             .m1_rlast(m1_rlast),
    .m1_rready(m1_rready),
    .m1_awvalid(m1_awvalid), .m1_awready(m1_awready),
    .m1_awaddr(m1_awaddr),   .m1_awlen(m1_awlen),
    .m1_wvalid(m1_wvalid),   .m1_wready(m1_wready),
    .m1_wdata(m1_wdata),     .m1_wstrb(m1_wstrb),
    .m1_wlast(m1_wlast),
    .m1_bvalid(m1_bvalid),   .m1_bresp(),

    .m2_arvalid(m2_mux_arvalid), .m2_arready(m2_mux_arready),
    .m2_araddr(m2_mux_araddr),   .m2_arlen(m2_mux_arlen),
    .m2_rvalid(m2_mux_rvalid),   .m2_rdata(m2_mux_rdata),
    .m2_rresp(),                 .m2_rlast(m2_mux_rlast),
    .m2_rready(m2_mux_rready),
    .m2_awvalid(m2_mux_awvalid), .m2_awready(m2_mux_awready),
    .m2_awaddr(m2_mux_awaddr),   .m2_awlen(m2_mux_awlen),
    .m2_wvalid(m2_mux_wvalid),   .m2_wready(m2_mux_wready),
    .m2_wdata(m2_mux_wdata),     .m2_wstrb(m2_mux_wstrb),
    .m2_wlast(m2_mux_wlast),
    .m2_bvalid(m2_mux_bvalid),   .m2_bresp(),

    .m3_arvalid(m3_arvalid), .m3_arready(m3_arready),
    .m3_araddr(m3_araddr),   .m3_arlen(m3_arlen),
    .m3_rvalid(m3_rvalid),   .m3_rdata(m3_rdata),
    .m3_rresp(),             .m3_rlast(m3_rlast),
    .m3_rready(m3_rready),

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
    .s_bcommit(arb_s_bcommit),

    .dbg_arb_state(dbg_arb_state),
    .dbg_cpu_pending(),
    .dbg_grant(dbg_grant)
);

// ============================================================
// DUT 2: AXI SDRAM slave (production, unmodified)
// ============================================================
axi_sdram_slave #(
    .SERIALIZE_WRITE_BURSTS(1'b0),
    .MAX_NATIVE_WRITE_BURST_LEN(8'd15),
    .EARLY_WRITE_ACK(SDRAM_WRITE_PIPE)
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
    .s_axi_bcommit(arb_s_bcommit),
    .sdram_rd(sdram_slave_rd), .sdram_wr(sdram_slave_wr),
    .sdram_addr(sdram_slave_addr), .sdram_wdata(sdram_slave_wdata), .sdram_wstrb(sdram_slave_wstrb),
    .sdram_burst_len(sdram_slave_burst_len), .sdram_burst_wr_len(sdram_slave_burst_wr_len),
    .sdram_rdata(ram1_word_q), .sdram_busy(ram1_word_queue_pending),
    .sdram_accepted(sdram_accepted_r), .sdram_rdata_valid(ram1_word_q_valid),
    .sdram_wr_data_next(sdram_slave_wr_data_next),
    .sdram_wr_done(sdram_slave_wr_done),
    .sdram_next_wdata(sdram_slave_next_wdata),
    .sdram_next_wstrb(sdram_slave_next_wstrb),
    .sdram_preload_wdata(sdram_slave_preload_wdata),
    .sdram_preload_wstrb(sdram_slave_preload_wstrb)
);

// ============================================================
// DUT 3: video_burst_arb (production, from ddr3_fb.sv) — scanout vs
// frame-DMA onto the single io_sdram burst port, exactly as emu.sv.
// ============================================================
wire        arb_burst_rd;
wire [24:0] arb_burst_addr;
wire [10:0] arb_burst_len;
wire        arb_burst_32bit;
wire        isr_burst_data_valid;
wire        isr_burst_data_done;

video_burst_arb vburst_arb (
    .clk(clk),
    .reset_n(reset_n),
    .vid_rd(vid_rd),
    .vid_addr(vid_addr),
    .vid_len(vid_len),
    .vid_32bit(1'b1),
    .vid_data_valid(vid_data_valid),
    .vid_data_done(vid_data_done),
    .dma_req(dma_req),
    .dma_addr(dma_addr),
    .dma_len(dma_len),
    .dma_gnt(dma_gnt),
    .dma_data_valid(dma_data_valid),
    .dma_data_done(dma_data_done),
    .burst_rd(arb_burst_rd),
    .burst_addr(arb_burst_addr),
    .burst_len(arb_burst_len),
    .burst_32bit(arb_burst_32bit),
    .burst_data_valid(isr_burst_data_valid),
    .burst_data_done(isr_burst_data_done)
);

assign dbg_vb_owner = vburst_arb.owner;
assign dbg_slave_state = slave.state;
assign dbg_wlast_err = slave.wlast_err_count;

// ============================================================
// DUT 4: io_sdram (MiSTer controller; test variant with split DQ)
// ============================================================
io_sdram #(
    .BANK_ROW_TRACK(BANK_ROW_TRACK),
    .WR_DATA_LATCH(SDRAM_WRITE_PIPE),
    .REFRESH_INTERVAL(REFRESH_INTERVAL)
) sdram_ctrl (
    .controller_clk(clk), .chip_clk(clk), .clk_90(clk), .reset_n(reset_n),
    .phy_cke(phy_cke), .phy_clk(phy_clk),
    .phy_cas(phy_cas), .phy_ras(phy_ras), .phy_we(phy_we),
    .phy_ba(phy_ba), .phy_a(phy_a),
    .phy_dq_in(dq_to_ctrl),
    .phy_dq_out_port(ctrl_dq_out),
    .phy_dq_oe_port(ctrl_dq_oe),
    .phy_dqm(phy_dqm),
    .burst_rd(arb_burst_rd), .burst_addr(arb_burst_addr),
    .burst_len(arb_burst_len), .burst_32bit(arb_burst_32bit),
    .burst_data(), .burst_data_valid(isr_burst_data_valid),
    .burst_data_done(isr_burst_data_done),
    .burstwr(1'b0), .burstwr_addr(25'b0), .burstwr_ready(),
    .burstwr_strobe(1'b0), .burstwr_data(16'b0), .burstwr_done(1'b0),
    .word_rd(ram1_word_rd), .word_wr(ram1_word_wr),
    .word_addr(ram1_word_addr), .word_data(ram1_word_data), .word_wstrb(ram1_word_wstrb),
    .word_data_next(ram1_word_data_next), .word_wstrb_next(ram1_word_wstrb_next),
    .word_burst_len(ram1_word_burst_len), .word_burst_wr_len(ram1_word_burst_wr_len),
    .word_q(ram1_word_q), .word_busy(ram1_word_busy),
    .word_queue_pending(ram1_word_queue_pending), .word_q_valid(ram1_word_q_valid),
    .word_wr_data_next(sdram_slave_wr_data_next),
    .word_wr_done(sdram_slave_wr_done),
    .burst_wr_direct_data(sdram_slave_next_wdata),
    .burst_wr_direct_strb(sdram_slave_next_wstrb),
    .dbg_io(dbg_io)
);

// ============================================================
// Behavioral SDRAM model (full-column, refresh-enforced)
// ============================================================
sdram_model_full sdram_chip (
    .clk(phy_clk), .cke(phy_cke), .cs_n(1'b0),
    .ras_n(phy_ras), .cas_n(phy_cas), .we_n(phy_we),
    .ba(phy_ba), .a(phy_a),
    .dq_in(ctrl_dq_out),
    .dq_out(model_dq_out), .dq_oe(model_dq_oe),
    .dqm(phy_dqm),
    .bd_we(1'b0), .bd_word_addr(24'b0), .bd_wdata(32'b0)
);

assign dbg_gpu_wq_count = sdram_arb.gpu_wq_count;
assign dbg_gpu_wq_full  = sdram_arb.gpu_wq_full;

endmodule

`default_nettype wire
