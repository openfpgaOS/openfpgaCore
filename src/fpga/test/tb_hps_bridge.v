//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

//
// tb_hps_bridge.v — MiSTer hps_bridge integration testbench.
//
// Instantiates the real datapath the bridge sees on hardware:
//
//   hps_bridge  →  axi_sdram_arbiter (M2)  →  axi_sdram_slave
//                                               └─ pulse adapter ─ sdram_fast_model
//
// The C++ harness (tb_hps_bridge_main.cpp) plays two roles:
//   - the HPS side of hps_io: ioctl boot.rom streaming and the
//     sd_rd/sd_wr/sd_ack/sd_buff sector protocol against a host-memory
//     "disk image"
//   - the firmware side of axi_periph_slave: drives the level
//     target_dataslot_* strobes and checks the ack/done/err handshake
//
// A spare AXI master on the arbiter's M1 (CPU) port lets the harness
// preload and verify SDRAM contents word-by-word.
//

`default_nettype none

module tb_hps_bridge (
    input  wire        clk,
    input  wire        reset_n,

    // ── hps_io model (driven by C++) ────────────────────────────────
    input  wire        ioctl_download,
    input  wire [15:0] ioctl_index,
    input  wire        ioctl_wr,
    input  wire [15:0] ioctl_dout,
    output wire        ioctl_wait,

    // Three virtual disks (VDNUM=3 shapes).  img_readonly/img_size are
    // scalar per the hps_io contract: valid only for the active
    // img_mounted[n] pulse bit.
    input  wire [2:0]  img_mounted,
    input  wire        img_readonly,
    input  wire [63:0] img_size,

    output wire [31:0] sd_lba0,
    output wire [31:0] sd_lba1,
    output wire [31:0] sd_lba2,
    output wire [2:0]  sd_rd,
    output wire [2:0]  sd_wr,
    input  wire [2:0]  sd_ack,
    input  wire [7:0]  sd_buff_addr,
    input  wire [15:0] sd_buff_dout,
    output wire [15:0] sd_buff_din,
    input  wire        sd_buff_wr,

    // ── periph-side dataslot interface (driven by C++) ──────────────
    input  wire        ds_read,
    input  wire        ds_write,
    input  wire        ds_openfile,
    input  wire        ds_getfile,
    input  wire [15:0] ds_id,
    input  wire [31:0] ds_offset,
    input  wire [31:0] ds_bridgeaddr,
    input  wire [31:0] ds_length,
    output wire        ds_ack,
    output wire        ds_done,
    output wire [2:0]  ds_err,
    output wire        wr_idle,

    output wire [31:0] hps_status,
    output wire [63:0] hps_img_size,
    output wire [63:0] hps_img1_size,
    output wire [63:0] hps_img2_size,
    output wire [31:0] hps_boot_len,
    output wire [31:0] hps_ini_len,
    output wire [31:0] hps_elf_len,
    output wire        boot_loaded,

    // ── M1 verification master (raw AXI from C++) ───────────────────
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
    input  wire        m1_wlast,
    output wire        m1_bvalid
);

// ── hps_bridge under test ───────────────────────────────────────────
wire        brg_awvalid, brg_awready;
wire [31:0] brg_awaddr;
wire [7:0]  brg_awlen;
wire        brg_wvalid, brg_wready;
wire [31:0] brg_wdata;
wire [3:0]  brg_wstrb;
wire        brg_wlast;
wire        brg_bvalid;
wire [1:0]  brg_bresp;
wire        brg_arvalid, brg_arready;
wire [31:0] brg_araddr;
wire [7:0]  brg_arlen;
wire        brg_rvalid;
wire [31:0] brg_rdata;
wire [1:0]  brg_rresp;
wire        brg_rlast;
wire        brg_rready;

hps_bridge #(
    .BOOT_STAGE_ADDR(32'h0330_0000),
    // Short watchdog so timeout tests run in simulation time.
    .SD_TIMEOUT(28'd20_000)
) dut (
    .clk(clk),
    .reset_n(reset_n),
    .ioctl_download(ioctl_download),
    .ioctl_index(ioctl_index),
    .ioctl_wr(ioctl_wr),
    .ioctl_dout(ioctl_dout),
    .ioctl_wait(ioctl_wait),
    .img_mounted(img_mounted),
    .img_readonly(img_readonly),
    .img_size(img_size),
    .sd_lba0(sd_lba0),
    .sd_lba1(sd_lba1),
    .sd_lba2(sd_lba2),
    .sd_rd(sd_rd),
    .sd_wr(sd_wr),
    .sd_ack(sd_ack),
    .sd_buff_addr(sd_buff_addr),
    .sd_buff_dout(sd_buff_dout),
    .sd_buff_din(sd_buff_din),
    .sd_buff_wr(sd_buff_wr),
    .timestamp(33'd0),
    .joystick_0(32'd0),
    .joystick_1(32'd0),
    .joystick_l_analog_0(16'd0),
    .joystick_r_analog_0(16'd0),
    .joystick_l_analog_1(16'd0),
    .joystick_r_analog_1(16'd0),
    .target_dataslot_read(ds_read),
    .target_dataslot_write(ds_write),
    .target_dataslot_openfile(ds_openfile),
    .target_dataslot_getfile(ds_getfile),
    .target_dataslot_id(ds_id),
    .target_dataslot_slotoffset(ds_offset),
    .target_dataslot_bridgeaddr(ds_bridgeaddr),
    .target_dataslot_length(ds_length),
    .target_dataslot_ack(ds_ack),
    .target_dataslot_done(ds_done),
    .target_dataslot_err(ds_err),
    .bridge_wr_idle(wr_idle),
    .hps_status(hps_status),
    .hps_img_size(hps_img_size),
    .hps_img1_size(hps_img1_size),
    .hps_img2_size(hps_img2_size),
    .hps_boot_len(hps_boot_len),
    .hps_ini_len(hps_ini_len),
    .hps_elf_len(hps_elf_len),
    .boot_rom_loaded(boot_loaded),
    .cont1_key(), .cont1_joy(), .cont1_trig(),
    .cont2_key(), .cont2_joy(), .cont2_trig(),
    .rtc_epoch_seconds(), .rtc_valid(),
    .m_awvalid(brg_awvalid), .m_awready(brg_awready),
    .m_awaddr(brg_awaddr),   .m_awlen(brg_awlen),
    .m_wvalid(brg_wvalid),   .m_wready(brg_wready),
    .m_wdata(brg_wdata),     .m_wstrb(brg_wstrb),
    .m_wlast(brg_wlast),
    .m_bvalid(brg_bvalid),   .m_bresp(brg_bresp),
    .m_arvalid(brg_arvalid), .m_arready(brg_arready),
    .m_araddr(brg_araddr),   .m_arlen(brg_arlen),
    .m_rvalid(brg_rvalid),   .m_rdata(brg_rdata),
    .m_rresp(brg_rresp),     .m_rlast(brg_rlast),
    .m_rready(brg_rready)
);

// ── arbiter: M0/M3 idle, M1 = harness, M2 = bridge ──────────────────
wire        arb_arvalid, arb_arready;
wire [31:0] arb_araddr;
wire [7:0]  arb_arlen;
wire        arb_rvalid, arb_rready, arb_rlast;
wire [31:0] arb_rdata;
wire [1:0]  arb_rresp;
wire        arb_awvalid, arb_awready;
wire [31:0] arb_awaddr;
wire [7:0]  arb_awlen;
wire        arb_wvalid, arb_wready, arb_wlast;
wire [31:0] arb_wdata;
wire [3:0]  arb_wstrb;
wire        arb_bvalid;
wire [1:0]  arb_bresp;
wire        arb_wcont;

axi_sdram_arbiter arb (
    .clk(clk),
    .reset_n(1'b1),
    .m0_arvalid(1'b0), .m0_arready(),
    .m0_araddr(32'd0), .m0_arlen(8'd0),
    .m0_rvalid(), .m0_rdata(), .m0_rresp(), .m0_rlast(),
    .m0_awvalid(1'b0), .m0_awready(),
    .m0_awaddr(32'd0), .m0_awlen(8'd0),
    .m0_wvalid(1'b0), .m0_wready(),
    .m0_wdata(32'd0), .m0_wstrb(4'd0), .m0_wlast(1'b0),
    .m0_bvalid(), .m0_bresp(),
    .m1_arvalid(m1_arvalid), .m1_arready(m1_arready),
    .m1_araddr(m1_araddr),   .m1_arlen(m1_arlen),
    .m1_rvalid(m1_rvalid),   .m1_rdata(m1_rdata),
    .m1_rresp(),             .m1_rlast(m1_rlast),
    .m1_rready(m1_rready),
    .m1_awvalid(m1_awvalid), .m1_awready(m1_awready),
    .m1_awaddr(m1_awaddr),   .m1_awlen(m1_awlen),
    .m1_wvalid(m1_wvalid),   .m1_wready(m1_wready),
    .m1_wdata(m1_wdata),     .m1_wstrb(4'hF),
    .m1_wlast(m1_wlast),
    .m1_bvalid(m1_bvalid),   .m1_bresp(),
    .m2_arvalid(brg_arvalid), .m2_arready(brg_arready),
    .m2_araddr(brg_araddr),   .m2_arlen(brg_arlen),
    .m2_rvalid(brg_rvalid),   .m2_rdata(brg_rdata),
    .m2_rresp(brg_rresp),     .m2_rlast(brg_rlast),
    .m2_rready(brg_rready),
    .m2_awvalid(brg_awvalid), .m2_awready(brg_awready),
    .m2_awaddr(brg_awaddr),   .m2_awlen(brg_awlen),
    .m2_wvalid(brg_wvalid),   .m2_wready(brg_wready),
    .m2_wdata(brg_wdata),     .m2_wstrb(brg_wstrb),
    .m2_wlast(brg_wlast),
    .m2_bvalid(brg_bvalid),   .m2_bresp(brg_bresp),
    .m3_arvalid(1'b0), .m3_arready(),
    .m3_araddr(32'd0), .m3_arlen(8'd0),
    .m3_rvalid(), .m3_rdata(), .m3_rresp(), .m3_rlast(),
    .m3_rready(1'b1),
    .s_arvalid(arb_arvalid), .s_arready(arb_arready),
    .s_araddr(arb_araddr),   .s_arlen(arb_arlen),
    .s_rvalid(arb_rvalid),   .s_rready(arb_rready),
    .s_rdata(arb_rdata),
    .s_rresp(arb_rresp),     .s_rlast(arb_rlast),
    .s_awvalid(arb_awvalid), .s_awready(arb_awready),
    .s_awaddr(arb_awaddr),   .s_awlen(arb_awlen),
    .s_wvalid(arb_wvalid),   .s_wready(arb_wready),
    .s_wdata(arb_wdata),     .s_wstrb(arb_wstrb),
    .s_wlast(arb_wlast),
    .s_bvalid(arb_bvalid),   .s_bresp(arb_bresp),
    .s_wcont(arb_wcont),
    .dbg_arb_state(),
    .dbg_cpu_pending(),
    .dbg_grant()
);

// ── slave + pulse adapter + fast model (tb_system wiring) ───────────
wire        sdram_rd, sdram_wr;
wire [23:0] sdram_addr;
wire [31:0] sdram_wdata;
wire [3:0]  sdram_wstrb;
wire [3:0]  sdram_burst_len;
wire [3:0]  sdram_burst_wr_len;
wire [31:0] sdram_next_wdata;
wire [3:0]  sdram_next_wstrb;
wire [31:0] sdram_preload_wdata;
wire [3:0]  sdram_preload_wstrb;

wire [31:0] word_q_sd;
wire        word_busy_sd;
wire        word_q_valid_sd;
wire        word_wr_data_next_sd;
wire        word_wr_done_sd;

reg         word_rd_sd, word_wr_sd;
reg  [23:0] word_addr_sd;
reg  [31:0] word_data_sd;
reg  [3:0]  word_wstrb_sd;
reg  [31:0] word_data_next_sd;
reg  [3:0]  word_wstrb_next_sd;
reg  [3:0]  word_burst_len_sd;
reg  [3:0]  word_burst_wr_len_sd;
reg         accepted_r;
reg         cmd_forwarded;

axi_sdram_slave #(
    .SERIALIZE_WRITE_BURSTS(1'b0),
    .MAX_NATIVE_WRITE_BURST_LEN(8'd15)
) slave (
    .clk(clk),
    .reset_n(1'b1),
    .s_axi_arvalid(arb_arvalid), .s_axi_arready(arb_arready),
    .s_axi_araddr(arb_araddr),   .s_axi_arlen(arb_arlen),
    .s_axi_rvalid(arb_rvalid),   .s_axi_rready(arb_rready),
    .s_axi_rdata(arb_rdata),     .s_axi_rresp(arb_rresp),
    .s_axi_rlast(arb_rlast),
    .s_axi_awvalid(arb_awvalid), .s_axi_awready(arb_awready),
    .s_axi_awaddr(arb_awaddr),   .s_axi_awlen(arb_awlen),
    .s_axi_wvalid(arb_wvalid),   .s_axi_wready(arb_wready),
    .s_axi_wdata(arb_wdata),     .s_axi_wstrb(arb_wstrb),
    .s_axi_wlast(arb_wlast),
    .s_axi_bvalid(arb_bvalid),   .s_axi_bready(1'b1),
    .s_axi_bresp(arb_bresp),
    .s_axi_wcont(arb_wcont),
    .sdram_rd(sdram_rd),
    .sdram_wr(sdram_wr),
    .sdram_addr(sdram_addr),
    .sdram_wdata(sdram_wdata),
    .sdram_wstrb(sdram_wstrb),
    .sdram_burst_len(sdram_burst_len),
    .sdram_burst_wr_len(sdram_burst_wr_len),
    .sdram_rdata(word_q_sd),
    .sdram_busy(word_busy_sd),
    .sdram_accepted(accepted_r),
    .sdram_rdata_valid(word_q_valid_sd),
    .sdram_wr_data_next(word_wr_data_next_sd),
    .sdram_wr_done(word_wr_done_sd),
    .sdram_next_wdata(sdram_next_wdata),
    .sdram_next_wstrb(sdram_next_wstrb),
    .sdram_preload_wdata(sdram_preload_wdata),
    .sdram_preload_wstrb(sdram_preload_wstrb)
);

always @(posedge clk) begin
    word_rd_sd <= 0;
    word_wr_sd <= 0;
    word_burst_len_sd <= 4'd0;
    word_burst_wr_len_sd <= 4'd0;
    accepted_r <= 0;

    if (!sdram_rd && !sdram_wr)
        cmd_forwarded <= 0;

    if (!word_busy_sd && !cmd_forwarded && (sdram_rd || sdram_wr)) begin
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
end

sdram_fast_model sdram_mem (
    .clk(clk),
    .reset_n(reset_n),
    .word_rd(word_rd_sd),
    .word_wr(word_wr_sd),
    .word_addr(word_addr_sd),
    .word_data(word_data_sd),
    .word_wstrb(word_wstrb_sd),
    .word_data_next(word_data_next_sd),
    .word_wstrb_next(word_wstrb_next_sd),
    .word_burst_len(word_burst_len_sd),
    .word_burst_wr_len(word_burst_wr_len_sd),
    .word_q(word_q_sd),
    .word_busy(word_busy_sd),
    .word_q_valid(word_q_valid_sd),
    .word_wr_data_next(word_wr_data_next_sd),
    .word_wr_done(word_wr_done_sd),
    .burst_wr_direct_data(sdram_next_wdata),
    .burst_wr_direct_strb(sdram_next_wstrb)
);

endmodule
