//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

//
// openfpgaOS — MiSTer core top (the `emu` module instantiated by sys_top).
//
// This is the MiSTer counterpart of pocket/core_top.v: the same
// platform-neutral internals (cpu_system, axi_periph_slave,
// axi_sdram_arbiter/slave, io_sdram, scanout, audio_mixer, gpu_core)
// with the Pocket-specific glue replaced:
//
//   APF bridge + CRAM staging  →  hps_bridge.v (ioctl boot.rom DMA +
//                                 disk-image sector engine on arbiter M2)
//   Pocket LCD + Analogizer    →  VGA_*/CE_PIXEL into the framework
//                                 scaler (fixed 60 Hz raster; the
//                                 dedicated 15 kHz analog raster is a
//                                 post-v1 item)
//   I2S audio_output           →  parallel AUDIO_L/R (sys_top samples)
//   APF cont1/2 inputs         →  hps_io joysticks remapped in
//                                 hps_bridge (OF_BTN bit order)
//   External async SRAM        →  sram_bram.v (32 KB M10K, GPU LUT)
//
// Clocking: one PLL — clk_cpu = clk_ram_controller = 100 MHz,
// clk_ram_chip = 100 MHz phase-shifted (SDRAM CK), clk_vid = 24.576 MHz.
// hps_io runs with clk_sys = clk_cpu so the bridge needs no CDC.
//

module emu
(
	`include "sys/emu_ports.vh"
);

///////// Default values for ports not used in this core /////////

assign ADC_BUS  = 'Z;
assign USER_OUT = '1;
assign {UART_RTS, UART_TXD, UART_DTR} = 0;
assign {SD_SCK, SD_MOSI, SD_CS} = 'Z;
// DDRAM is driven by the ddr3_fb direct-framebuffer pipeline (see the
// video section below) — frames are copied from SDRAM into a 2-slot DDR3
// ring that the framework scaler reads via the MISTER_FB interface.

assign VGA_SL = 0;
assign VGA_F1 = 0;
assign VGA_SCALER  = 0;
assign VGA_DISABLE = 0;
assign HDMI_FREEZE = 0;
assign HDMI_BLACKOUT = 0;
assign HDMI_BOB_DEINT = 0;

assign AUDIO_S = 1;        // signed samples from the mixer
assign AUDIO_MIX = 0;

assign LED_POWER = 0;
assign BUTTONS = 0;

wire [1:0] ar = status[122:121];
assign VIDEO_ARX = (!ar) ? 13'd4 : {11'd0, ar - 1'd1};
assign VIDEO_ARY = (!ar) ? 13'd3 : 13'd0;

///////////////////////  CONF_STR  ///////////////////////////////

`include "build_id.v"
localparam CONF_STR = {
	"OpenfpgaOS;;",
	// Sn index = hps_io disk/bit number = DS_SLOT_ID index in firmware:
	// 0 = family/legacy image, 1 = per-instance image, 2 = borrow slot.
	// NOTE: an H0<opt> menu-mask hide was tried here to declutter the OSD but it
	// WEDGED the framework on HW (stuck "loading boot.vhd", no render) — reverted
	// to the plain full menu.  Do NOT re-add H-flags without a verified syntax.
	// These entries must stay: the .mgl drives the mounts (S0/S1/S2) and the
	// F-loads (F1 ini / F2 elf) by index.
	"S0,VHDIMG,Family Data;",
	"S1,VHDIMG,Instance;",
	"S2,VHDIMG,Extra Data;",
	"F1,INI,Load Instance;",
	"F2,ELF,Load App;",
	"-;",
	"O[122:121],Aspect ratio,Original,Full Screen,[ARC1],[ARC2];",
	// Direct FB = frames DMA'd to DDR3 and scaled natively by ascal
	// (single-pass, tear-free).  Original = the legacy scanout->VGA input
	// path, kept alive as a fallback; unsupported color modes fall back
	// automatically regardless of this setting.
	"O[1],Video Output,Direct FB,Original;",
	"-;",
	"R[0],Reset and close OSD;",
	// Button order is the input ABI: bits 4+ of joystick_0 land 1:1 on
	// OF_BTN_{A,B,X,Y,L1,R1,L2,R2,L3,R3,SELECT,START}.  Do not reorder
	// without updating hps_bridge.v's key_remap + firmware input.c.
	"J1,A,B,X,Y,L,R,L2,R2,L3,R3,Select,Start;",
	"V,v",`BUILD_DATE
};

///////////////////////   CLOCKS   ///////////////////////////////

wire clk_cpu;
wire clk_ram_controller = clk_cpu;
wire clk_ram_chip;
wire clk_vid;
wire pll_sys_locked, pll_vid_locked;
wire pll_locked = pll_sys_locked & pll_vid_locked;

// Two PLLs: 100 MHz pair is integer-mode, 24.576 MHz needs its own
// fractional VCO (no legal shared VCO with 100 MHz — see pll_sys.v).
pll_sys pll (
	.refclk   (CLK_50M),
	.rst      (0),
	.outclk_0 (clk_cpu),
	.outclk_1 (clk_ram_chip),
	.locked   (pll_sys_locked)
);

pll_vid pllv (
	.refclk   (CLK_50M),
	.rst      (0),
	.outclk_0 (clk_vid),
	.locked   (pll_vid_locked)
);

// Auto-reset on image mount: mounting (or changing) a vhd in the OSD
// pulses img_mounted[n]; restarting the core makes the OS re-read the
// mounted set and launch the newly selected instance — no manual reset
// needed.  img_size!=0 distinguishes mount from eject (eject alone does
// not reset; the follow-up mount does).  The pulse is stretched well past
// the reset synchronizer depth.  Note the framework re-announces
// remembered mounts right after core load, so one benign extra reset
// fires at startup (standard for MiSTer cores that reset on image load).
reg [7:0] mount_reset_cnt = 8'd0;
always @(posedge clk_cpu) begin
	if ((|img_mounted) && img_size != 64'd0)
		mount_reset_cnt <= 8'hFF;
	else if (mount_reset_cnt != 8'd0)
		mount_reset_cnt <= mount_reset_cnt - 8'd1;
end
wire mount_reset = (mount_reset_cnt != 8'd0);

// Reset the SoC when a NEW instance ini is F-loaded, so switching games
// works even mid-session (not only when the MGL happens to re-mount the
// family vhd).  hps_status[10] = ini_loaded (from hps_bridge) rises on each
// completed F-load.  hps_bridge is tied .reset_n(1'b1) — never reset — so the
// ini staging, ini_loaded, and the boot.rom staging all SURVIVE this reset;
// the rebooted OS re-reads the newly-picked ini from staging and launches it.
// Same one-shot stretch pattern as mount_reset (clk_cpu domain; ini_loaded is
// already clk_cpu, so no CDC).
reg  [7:0] ini_reset_cnt = 8'd0;
reg        ini_loaded_d  = 1'b0;
always @(posedge clk_cpu) begin
	ini_loaded_d <= hps_status[10];
	if (hps_status[10] && !ini_loaded_d)
		ini_reset_cnt <= 8'hFF;
	else if (ini_reset_cnt != 8'd0)
		ini_reset_cnt <= ini_reset_cnt - 8'd1;
end
wire ini_reset = (ini_reset_cnt != 8'd0);

wire reset_n = ~RESET & ~status[0] & ~mount_reset & ~ini_reset & pll_locked;

// clk_cpu-domain reset replicas (same split as core_top.v so fitter
// placement is not constrained by one monolithic reset tree).
reg [1:0] reset_cpu_core_sync;
reg [1:0] reset_cpu_media_sync;
always @(posedge clk_cpu or negedge reset_n) begin
	if (~reset_n) begin
		reset_cpu_core_sync  <= 2'b00;
		reset_cpu_media_sync <= 2'b00;
	end else begin
		reset_cpu_core_sync  <= {reset_cpu_core_sync[0],  1'b1};
		reset_cpu_media_sync <= {reset_cpu_media_sync[0], 1'b1};
	end
end
wire reset_n_cpu_core  = reset_cpu_core_sync[1];
wire reset_n_cpu_media = reset_cpu_media_sync[1];

reg [1:0] reset_vid_sync;
wire reset_n_vid = reset_vid_sync[1];
always @(posedge clk_vid or negedge reset_n) begin
	if (~reset_n)
		reset_vid_sync <= 2'b0;
	else
		reset_vid_sync <= {reset_vid_sync[0], 1'b1};
end

///////////////////////   HPS IO   ///////////////////////////////

wire [127:0] status;
wire  [1:0]  buttons;

wire         ioctl_download;
wire [15:0]  ioctl_index;
wire         ioctl_wr;
wire [26:0]  ioctl_addr;
wire [15:0]  ioctl_dout;
wire         ioctl_wait;

// Three virtual disks (VDNUM=3): S0 = family/legacy, S1 = instance,
// S2 = borrow.  img_readonly/img_size stay scalar per the hps_io
// contract (valid only for the active img_mounted[n] pulse bit);
// hps_bridge latches them per disk.
wire [2:0]   img_mounted;
wire         img_readonly;
wire [63:0]  img_size;

wire [31:0]  sd_lba[3];
// One 512-byte block per sd_rd/sd_wr — the sector engine's contract.
// Tied explicitly so the HPS can never stream multi-block transfers
// that would alias over the 256-word sector buffer.
wire [5:0]   sd_blk_cnt[3];
assign sd_blk_cnt[0] = 6'd0;
assign sd_blk_cnt[1] = 6'd0;
assign sd_blk_cnt[2] = 6'd0;
wire [2:0]   sd_rd;
wire [2:0]   sd_wr;
wire [2:0]   sd_ack;
wire [12:0]  sd_buff_addr;
wire [15:0]  sd_buff_dout;
wire [15:0]  sd_buff_din[3];
wire         sd_buff_wr;
// The engine serializes one op at a time through one shared sector
// buffer, so a single registered din stream feeds every array element;
// hps_io picks by the acked disk (sdn_ack) and Quartus dedups the fanout.
wire [15:0]  sd_buff_din_brg;
assign sd_buff_din[0] = sd_buff_din_brg;
assign sd_buff_din[1] = sd_buff_din_brg;
assign sd_buff_din[2] = sd_buff_din_brg;

wire [31:0]  joystick_0, joystick_1;
wire [15:0]  joystick_l_analog_0, joystick_r_analog_0;
wire [15:0]  joystick_l_analog_1, joystick_r_analog_1;

wire [24:0]  ps2_mouse;
wire [15:0]  ps2_mouse_ext;

wire [32:0]  timestamp;

hps_io #(.CONF_STR(CONF_STR), .WIDE(1), .VDNUM(3), .BLKSZ(2)) hps_io (
	.clk_sys             (clk_cpu),
	.HPS_BUS             (HPS_BUS),
	.EXT_BUS             (),
	.gamma_bus           (),

	.buttons             (buttons),
	.status              (status),
	.status_menumask     (16'h0000),   // no H-hidden CONF_STR entries — keep the full menu
	                                    // (an H0 hide wedged ARM Main: "loading boot.vhd", see CONF_STR note)

	.ioctl_download      (ioctl_download),
	.ioctl_index         (ioctl_index),
	.ioctl_wr            (ioctl_wr),
	.ioctl_addr          (ioctl_addr),
	.ioctl_dout          (ioctl_dout),
	.ioctl_wait          (ioctl_wait),

	.img_mounted         (img_mounted),
	.img_readonly        (img_readonly),
	.img_size            (img_size),

	.sd_lba              (sd_lba),
	.sd_blk_cnt          (sd_blk_cnt),
	.sd_rd               (sd_rd),
	.sd_wr               (sd_wr),
	.sd_ack              (sd_ack),
	.sd_buff_addr        (sd_buff_addr),
	.sd_buff_dout        (sd_buff_dout),
	.sd_buff_din         (sd_buff_din),
	.sd_buff_wr          (sd_buff_wr),

	.joystick_0          (joystick_0),
	.joystick_1          (joystick_1),
	.joystick_l_analog_0 (joystick_l_analog_0),
	.joystick_r_analog_0 (joystick_r_analog_0),
	.joystick_l_analog_1 (joystick_l_analog_1),
	.joystick_r_analog_1 (joystick_r_analog_1),

	.ps2_mouse           (ps2_mouse),
	.ps2_mouse_ext       (ps2_mouse_ext),

	.TIMESTAMP           (timestamp)
);

// Framework mouse → input-hub slot 3 (periph cont4_*).  Same clk_cpu
// domain as hps_io; the periph 2FF-syncs the cont buses internally.
wire [31:0] mouse_controls, mouse_joypad;
wire [15:0] mouse_trigger;

hps_mouse mouse (
	.clk      (clk_cpu),
	// Never reset — hps_io isn't reset by the core's warm resets either,
	// so ps2_mouse[24] keeps toggling across them; clearing stb_prev would
	// desync the toggle tracker (one phantom packet).  The accumulators
	// are free-running by contract (firmware re-baselines its differencing
	// on restart) and "seen" persisting is correct: the mouse is still there.
	.reset_n  (1'b1),

	.ps2_mouse     (ps2_mouse),
	.ps2_mouse_ext (ps2_mouse_ext),

	.cont4_key  (mouse_controls),
	.cont4_joy  (mouse_joypad),
	.cont4_trig (mouse_trigger)
);

///////////////////////  HPS BRIDGE  /////////////////////////////

// Periph-side data-slot interface (all clk_cpu — no CDC on MiSTer).
wire        cpu_target_dataslot_read;
wire        cpu_target_dataslot_write;
wire        cpu_target_dataslot_openfile;
wire        cpu_target_dataslot_getfile;
wire [15:0] cpu_target_dataslot_id;
wire [31:0] cpu_target_dataslot_slotoffset;
wire [31:0] cpu_target_dataslot_bridgeaddr;
wire [31:0] cpu_target_dataslot_length;
wire        target_dataslot_ack;
wire        target_dataslot_done;
wire [2:0]  target_dataslot_err;
wire        bridge_wr_idle;

wire [31:0] hps_status;
wire [63:0] hps_img_size;
wire [63:0] hps_img1_size;
wire [63:0] hps_img2_size;
wire [31:0] hps_boot_len;
wire [31:0] hps_ini_len;
wire [31:0] hps_elf_len;
wire        boot_rom_loaded;

wire [31:0] p1_controls, p1_joypad;
wire [15:0] p1_trigger;
wire [31:0] p2_controls, p2_joypad;
wire [15:0] p2_trigger;
wire [31:0] rtc_epoch_seconds;
wire        rtc_valid;

// hps_bridge AXI master → arbiter M2
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
	// SDRAM byte offset of the staging arena (OF_TARGET_CRAM0_BRIDGE)
	.BOOT_STAGE_ADDR(32'h0330_0000)
) bridge (
	.clk      (clk_cpu),
	// Never reset — same rationale as the Pocket's bridge_to_sdram
	// reset_n_axi(1'b1): the arbiter/slave it talks to are never-reset,
	// so an OSD/status[0] warm reset must not abandon an AXI burst
	// mid-handshake (that would wedge the whole SDRAM path).  The FSM
	// self-drains: periph strobes drop on reset and S_DONE/watchdog
	// return it to idle.  This also keeps boot_rom_loaded latched across
	// warm resets — the HPS only re-sends boot.rom on core load.
	.reset_n  (1'b1),

	.ioctl_download (ioctl_download),
	.ioctl_index    (ioctl_index),
	.ioctl_wr       (ioctl_wr),
	.ioctl_dout     (ioctl_dout),
	.ioctl_wait     (ioctl_wait),

	.img_mounted    (img_mounted),
	.img_readonly   (img_readonly),
	.img_size       (img_size),

	.sd_lba0        (sd_lba[0]),
	.sd_lba1        (sd_lba[1]),
	.sd_lba2        (sd_lba[2]),
	.sd_rd          (sd_rd),
	.sd_wr          (sd_wr),
	.sd_ack         (sd_ack),
	.sd_buff_addr   (sd_buff_addr[7:0]),
	.sd_buff_dout   (sd_buff_dout),
	.sd_buff_din    (sd_buff_din_brg),
	.sd_buff_wr     (sd_buff_wr),

	.timestamp      (timestamp),
	.joystick_0     (joystick_0),
	.joystick_1     (joystick_1),
	.joystick_l_analog_0 (joystick_l_analog_0),
	.joystick_r_analog_0 (joystick_r_analog_0),
	.joystick_l_analog_1 (joystick_l_analog_1),
	.joystick_r_analog_1 (joystick_r_analog_1),

	.target_dataslot_read       (cpu_target_dataslot_read),
	.target_dataslot_write      (cpu_target_dataslot_write),
	.target_dataslot_openfile   (cpu_target_dataslot_openfile),
	.target_dataslot_getfile    (cpu_target_dataslot_getfile),
	.target_dataslot_id         (cpu_target_dataslot_id),
	.target_dataslot_slotoffset (cpu_target_dataslot_slotoffset),
	.target_dataslot_bridgeaddr (cpu_target_dataslot_bridgeaddr),
	.target_dataslot_length     (cpu_target_dataslot_length),
	.target_dataslot_ack        (target_dataslot_ack),
	.target_dataslot_done       (target_dataslot_done),
	.target_dataslot_err        (target_dataslot_err),
	.bridge_wr_idle             (bridge_wr_idle),

	.hps_status      (hps_status),
	.hps_img_size    (hps_img_size),
	.hps_img1_size   (hps_img1_size),
	.hps_img2_size   (hps_img2_size),
	.hps_boot_len    (hps_boot_len),
	.hps_ini_len     (hps_ini_len),
	.hps_elf_len     (hps_elf_len),
	.boot_rom_loaded (boot_rom_loaded),

	.cont1_key  (p1_controls),
	.cont1_joy  (p1_joypad),
	.cont1_trig (p1_trigger),
	.cont2_key  (p2_controls),
	.cont2_joy  (p2_joypad),
	.cont2_trig (p2_trigger),
	.rtc_epoch_seconds (rtc_epoch_seconds),
	.rtc_valid         (rtc_valid),

	.m_awvalid (brg_awvalid), .m_awready (brg_awready),
	.m_awaddr  (brg_awaddr),  .m_awlen   (brg_awlen),
	.m_wvalid  (brg_wvalid),  .m_wready  (brg_wready),
	.m_wdata   (brg_wdata),   .m_wstrb   (brg_wstrb),
	.m_wlast   (brg_wlast),
	.m_bvalid  (brg_bvalid),  .m_bresp   (brg_bresp),
	.m_arvalid (brg_arvalid), .m_arready (brg_arready),
	.m_araddr  (brg_araddr),  .m_arlen   (brg_arlen),
	.m_rvalid  (brg_rvalid),  .m_rdata   (brg_rdata),
	.m_rresp   (brg_rresp),   .m_rlast   (brg_rlast),
	.m_rready  (brg_rready)
);

assign LED_USER = ioctl_download;
assign LED_DISK = {1'b0, |sd_rd | |sd_wr | |sd_ack};

///////////////////////  CPU SYSTEM  /////////////////////////////

// CPU master buses (direct, no top-level register slices — the SE
// device has far more routing headroom than the Pocket's CEBA4).
wire        cpu_m_sdram_arvalid, cpu_m_sdram_arready;
wire [31:0] cpu_m_sdram_araddr;
wire [7:0]  cpu_m_sdram_arlen;
wire        cpu_m_sdram_rvalid, cpu_m_sdram_rready;
wire [31:0] cpu_m_sdram_rdata;
wire [1:0]  cpu_m_sdram_rresp;
wire        cpu_m_sdram_rlast;
wire        cpu_m_sdram_awvalid, cpu_m_sdram_awready;
wire [31:0] cpu_m_sdram_awaddr;
wire [7:0]  cpu_m_sdram_awlen;
wire        cpu_m_sdram_wvalid, cpu_m_sdram_wready;
wire [31:0] cpu_m_sdram_wdata;
wire [3:0]  cpu_m_sdram_wstrb;
wire        cpu_m_sdram_wlast;
wire        cpu_m_sdram_bvalid;
wire [1:0]  cpu_m_sdram_bresp;

wire        cpu_m_local_arvalid, cpu_m_local_arready;
wire [31:0] cpu_m_local_araddr;
wire [7:0]  cpu_m_local_arlen;
wire        cpu_m_local_rvalid, cpu_m_local_rready;
wire [31:0] cpu_m_local_rdata;
wire [1:0]  cpu_m_local_rresp;
wire        cpu_m_local_rlast;
wire        cpu_m_local_awvalid, cpu_m_local_awready;
wire [31:0] cpu_m_local_awaddr;
wire [7:0]  cpu_m_local_awlen;
wire [1:0]  cpu_m_local_awburst;
wire        cpu_m_local_wvalid, cpu_m_local_wready;
wire [31:0] cpu_m_local_wdata;
wire [3:0]  cpu_m_local_wstrb;
wire        cpu_m_local_wlast;
wire        cpu_m_local_bvalid;
wire [1:0]  cpu_m_local_bresp;

wire        timer_irq;
wire        uart_rx_irq;
wire        ext_irq;

// Two-flop IRQ synchronizers (see core_top.v rationale: VexiiRiscv
// latches meip/mtip with no internal synchronizer).
reg [1:0] ext_irq_sync;
reg [1:0] timer_irq_sync;
always @(posedge clk_cpu) begin
	ext_irq_sync   <= {ext_irq_sync[0],   ext_irq};
	timer_irq_sync <= {timer_irq_sync[0], timer_irq};
end

cpu_system cpu (
	.clk(clk_cpu),
	.reset_n(reset_n_cpu_core),
	// SDRAM AXI4 master interface
	.m_sdram_arvalid(cpu_m_sdram_arvalid),
	.m_sdram_arready(cpu_m_sdram_arready),
	.m_sdram_araddr(cpu_m_sdram_araddr),
	.m_sdram_arlen(cpu_m_sdram_arlen),
	.m_sdram_rvalid(cpu_m_sdram_rvalid),
	.m_sdram_rready(cpu_m_sdram_rready),
	.m_sdram_rdata(cpu_m_sdram_rdata),
	.m_sdram_rresp(cpu_m_sdram_rresp),
	.m_sdram_rlast(cpu_m_sdram_rlast),
	.m_sdram_awvalid(cpu_m_sdram_awvalid),
	.m_sdram_awready(cpu_m_sdram_awready),
	.m_sdram_awaddr(cpu_m_sdram_awaddr),
	.m_sdram_awlen(cpu_m_sdram_awlen),
	.m_sdram_wvalid(cpu_m_sdram_wvalid),
	.m_sdram_wready(cpu_m_sdram_wready),
	.m_sdram_wdata(cpu_m_sdram_wdata),
	.m_sdram_wstrb(cpu_m_sdram_wstrb),
	.m_sdram_wlast(cpu_m_sdram_wlast),
	.m_sdram_bvalid(cpu_m_sdram_bvalid),
	.m_sdram_bresp(cpu_m_sdram_bresp),
	// CRAM0 AXI4 master — unbacked on MiSTer (the staging arena lives in
	// SDRAM and firmware never touches 0x30000000).  Tied off: an access
	// would stall, which is the desired loud failure for a firmware bug.
	.m_cram0_arvalid(),
	.m_cram0_arready(1'b0),
	.m_cram0_araddr (),
	.m_cram0_arlen  (),
	.m_cram0_rvalid (1'b0),
	.m_cram0_rready (),
	.m_cram0_rdata  (32'd0),
	.m_cram0_rresp  (2'd0),
	.m_cram0_rlast  (1'b0),
	.m_cram0_awvalid(),
	.m_cram0_awready(1'b0),
	.m_cram0_awaddr (),
	.m_cram0_awlen  (),
	.m_cram0_wvalid (),
	.m_cram0_wready (1'b0),
	.m_cram0_wdata  (),
	.m_cram0_wstrb  (),
	.m_cram0_wlast  (),
	.m_cram0_bvalid (1'b0),
	.m_cram0_bresp  (2'd0),
	// Local peripheral AXI4 master interface
	.m_local_arvalid(cpu_m_local_arvalid),
	.m_local_arready(cpu_m_local_arready),
	.m_local_araddr(cpu_m_local_araddr),
	.m_local_arlen(cpu_m_local_arlen),
	.m_local_rvalid(cpu_m_local_rvalid),
	.m_local_rready(cpu_m_local_rready),
	.m_local_rdata(cpu_m_local_rdata),
	.m_local_rresp(cpu_m_local_rresp),
	.m_local_rlast(cpu_m_local_rlast),
	.m_local_awvalid(cpu_m_local_awvalid),
	.m_local_awready(cpu_m_local_awready),
	.m_local_awaddr(cpu_m_local_awaddr),
	.m_local_awlen(cpu_m_local_awlen),
	.m_local_awburst(cpu_m_local_awburst),
	.m_local_wvalid(cpu_m_local_wvalid),
	.m_local_wready(cpu_m_local_wready),
	.m_local_wdata(cpu_m_local_wdata),
	.m_local_wstrb(cpu_m_local_wstrb),
	.m_local_wlast(cpu_m_local_wlast),
	.m_local_bvalid(cpu_m_local_bvalid),
	.m_local_bresp(cpu_m_local_bresp),
	.int_m_external(ext_irq_sync[1]),
	.int_m_timer(timer_irq_sync[1])
);

///////////////////////  VIDEO REGISTERS  ////////////////////////

wire [2:0]  color_mode;
wire [24:0] fb_display_addr;
wire [9:0]  fb_width;
wire [9:0]  fb_height;
wire [15:0] fb_stride;
wire [24:0] analog_src_fb_addr;
wire [2:0]  analog_src_color_mode;
wire [9:0]  analog_src_fb_width;
wire [9:0]  analog_src_fb_height;
wire [15:0] analog_src_fb_stride;
wire [2:0]  video_scaler_slot_cpu;
wire [9:0]  vrr_v_total_cpu;

// Scaler-slot CDC clk_cpu → clk_vid (quasi-static).
reg [2:0] video_scaler_slot_s1, video_scaler_slot_vid;
always @(posedge clk_vid or negedge reset_n_vid) begin
	if (~reset_n_vid) begin
		video_scaler_slot_s1  <= 3'd0;
		video_scaler_slot_vid <= 3'd0;
	end else begin
		video_scaler_slot_s1  <= video_scaler_slot_cpu;
		video_scaler_slot_vid <= video_scaler_slot_s1;
	end
end

///////////////////////  PERIPH SLAVE  ///////////////////////////

wire        cpu_pal_wr;
wire [7:0]  cpu_pal_addr;
wire [23:0] cpu_pal_data;
wire        cpu_pal_commit;
wire        cpu_pal_busy;

// Mixer sample pacing.  audio_mixer paces itself against the Pocket's I2S
// dcfifo fill level (it starts a new sample whenever level < 768, and the
// FIFO drains at 48 kHz).  MiSTer has no FIFO — sys_top samples AUDIO_L/R
// directly — so a hardwired level of 0 lets the mixer free-run at FSM speed
// (heard as SFX/music playing far too fast).  Emulate the backpressure with
// a CREDIT pacer: a 48 kHz phase-accumulator strobe grants one sample
// credit (small saturating pool so the mixer can catch up after SDRAM
// stalls, like the real FIFO's slack); each produced sample consumes one.
// The emulated level reads "room" only while a credit is available — and
// mixer_sample_wr is folded in COMBINATIONALLY so the cycle a sample
// completes cannot double-start against a credit whose decrement is still
// one flop away (the documented one-per-tick off-by-one, 2026-07-02).
// All-register and boot-inert: with mixer_enable=0 the mixer never starts
// regardless of this logic.  The tick is the CARRY OUT of a 32-bit
// accumulator sampled every cycle, so rate = K / 2^32 * 100 MHz and
// K = round(48000 / 100e6 * 2^32) = 2061584 → 47.99999 kHz.  (The first
// deployment used the 2^33-scaled K from an older half-rate strobe design
// and paced the mixer at 96 kHz — music/SFX at exactly double speed.)
reg  [32:0] audio_pace_acc /* synthesis preserve */;
reg  [2:0]  audio_credits;
wire        audio_pace_tick = audio_pace_acc[32];
always @(posedge clk_cpu) begin
	audio_pace_acc <= {1'b0, audio_pace_acc[31:0]} + 33'd2061584;
	case ({audio_pace_tick && (audio_credits != 3'd7),
	       mixer_sample_wr && (audio_credits != 3'd0)})
		2'b10:   audio_credits <= audio_credits + 3'd1;
		2'b01:   audio_credits <= audio_credits - 3'd1;
		default: ;   // both or neither: net zero
	endcase
end
wire audio_have_credit = (audio_credits != 3'd0) &&
                         !((audio_credits == 3'd1) && mixer_sample_wr);
// Raw pacing wires for the MIXER only; level>=768 ([9:8]==2'b11) = hold.
wire [9:0]  audio_fifo_level = audio_have_credit ? 10'd0 : 10'd1023;
wire        audio_fifo_full  = ~audio_have_credit;
// REGISTERED copies for the periph slave's MMIO readback mux — the raw
// wires reach deep into audio_mixer placement and must stay out of the
// periph's combinational rdata cone (see 2026-07-02 campaign notes).
reg  [9:0]  audio_fifo_level_mmio;
reg         audio_fifo_full_mmio;
always @(posedge clk_cpu) begin
	audio_fifo_level_mmio <= audio_fifo_level;
	audio_fifo_full_mmio  <= audio_fifo_full;
end

wire        mixer_enable_mmio;
wire        mixer_voice_wr_mmio;
wire [4:0]  mixer_voice_sel_mmio;
wire [3:0]  mixer_voice_field_mmio;
wire [31:0] mixer_voice_wdata_mmio;
wire        mixer_irq_clear_wr_mmio;
wire [31:0] mixer_irq_clear_mmio;
wire [7:0]  mixer_master_vol_mmio;
wire [7:0]  mixer_group_vol_0_mmio;
wire [7:0]  mixer_group_vol_1_mmio;
wire [7:0]  mixer_group_vol_2_mmio;
wire [7:0]  mixer_group_vol_3_mmio;
wire [63:0] mixer_voice_group_packed_mmio;
wire [4:0]  mixer_voice_sel_rd_mmio;

wire        gpu_reg_wr;
wire [3:0]  gpu_reg_addr;
wire [31:0] gpu_reg_wdata;
wire [31:0] gpu_reg_rdata;
wire        gpu_swap_req;
wire [1:0]  gpu_swap_idx;
wire        slave_swap_pending;

wire        link_irq = 1'b0;
wire [31:0] link_reg_rdata = 32'd0;

// Mixer status boundary registers (see core_top.v timing rationale).
wire [31:0] mixer_active_mask;
wire [21:0] mixer_pos_readback;
wire [31:0] mixer_voice_end_pending;
// pos_readback is NOT boundary-registered: audio_mixer registers its
// MLAB read internally, and the periph FSM's S_PERIPH_RD_LATCH budget
// is exactly two cycles after req_addr — one register stage on the
// select-dependent path, no more.
reg [31:0] mixer_active_mask_r;
reg [31:0] mixer_voice_end_pending_r;
always @(posedge clk_cpu) begin
	mixer_active_mask_r       <= mixer_active_mask;
	mixer_voice_end_pending_r <= mixer_voice_end_pending;
end

wire vidout_vs_w;
wire early_vblank_safe_vid;

axi_periph_slave #(
	// MiSTer feature set: every INCLUDE_* param echoes its build macro
	// (Makefile INCLUDE_DEFS).  No Analogizer (Pocket-only HW) and no fast
	// texture memory (no CRAM1 chip) — those two macros are NOT in the
	// MiSTer include list, so INCLUDE_ANALOGIZER / INCLUDE_TEX_MEM resolve
	// to 0.  This fixes the old HAS_CRAM1(1) vs tex_fast_size=0 inconsistency.
	.INCLUDE_ANALOGIZER(`ifdef INCLUDE_ANALOGIZER 1 `else 0 `endif),
	.INCLUDE_LINK(`ifdef INCLUDE_LINK 1 `else 0 `endif),
	.INCLUDE_HW_MIXER(`ifdef INCLUDE_HW_MIXER 1 `else 0 `endif),
	.INCLUDE_PARAM_TRI(`ifdef INCLUDE_PARAM_TRI 1 `else 0 `endif),
	.INCLUDE_VERT_TRI(`ifdef INCLUDE_VERT_TRI 1 `else 0 `endif),
	.INCLUDE_PARAM_TRI_RECS(`ifdef INCLUDE_PARAM_TRI_RECS 1 `else 0 `endif),
	.INCLUDE_COLUMN_LIST(`ifdef INCLUDE_COLUMN_LIST 1 `else 0 `endif),
	.INCLUDE_COMPACT_SPAN(`ifdef INCLUDE_COMPACT_SPAN 1 `else 0 `endif),
	.INCLUDE_TEX_MEM(`ifdef INCLUDE_TEX_MEM 1 `else 0 `endif)
) periph (
	.clk(clk_cpu),
	.reset_n(reset_n_cpu_core),
	// AXI4 slave interface
	.s_axi_arvalid(cpu_m_local_arvalid),
	.s_axi_arready(cpu_m_local_arready),
	.s_axi_araddr(cpu_m_local_araddr),
	.s_axi_arlen(cpu_m_local_arlen),
	.s_axi_rvalid(cpu_m_local_rvalid),
	.s_axi_rready(cpu_m_local_rready),
	.s_axi_rdata(cpu_m_local_rdata),
	.s_axi_rresp(cpu_m_local_rresp),
	.s_axi_rlast(cpu_m_local_rlast),
	.s_axi_awvalid(cpu_m_local_awvalid),
	.s_axi_awready(cpu_m_local_awready),
	.s_axi_awaddr(cpu_m_local_awaddr),
	.s_axi_awlen(cpu_m_local_awlen),
	.s_axi_awburst(cpu_m_local_awburst),
	.s_axi_wvalid(cpu_m_local_wvalid),
	.s_axi_wready(cpu_m_local_wready),
	.s_axi_wdata(cpu_m_local_wdata),
	.s_axi_wstrb(cpu_m_local_wstrb),
	.s_axi_wlast(cpu_m_local_wlast),
	.s_axi_bvalid(cpu_m_local_bvalid),
	.s_axi_bready(1'b1),
	.s_axi_bresp(cpu_m_local_bresp),
	// Status inputs
	.dataslot_allcomplete(boot_rom_loaded && bridge_wr_idle),
	.vsync(vidout_vs_w),
	.early_vblank(early_vblank_safe_vid),
	.os_inmenu(OSD_STATUS),
	.cont1_key(p1_controls),
	.cont1_joy(p1_joypad),
	.cont1_trig(p1_trigger),
	.cont2_key(p2_controls),
	.cont2_joy(p2_joypad),
	.cont2_trig(p2_trigger),
	.cont3_key(32'd0),
	.cont3_joy(32'd0),
	.cont3_trig(16'd0),
	.cont4_key(mouse_controls),
	.cont4_joy(mouse_joypad),
	.cont4_trig(mouse_trigger),
	.rtc_epoch_seconds(rtc_epoch_seconds),
	.rtc_valid(rtc_valid),
	.bridge_wr_idle(bridge_wr_idle),
	// F2i bridge word counters are a Pocket APF-bridge diagnostic; the
	// MiSTer HPS path has no equivalent tap — the register reads 0.
	.bridge_dbg_wcnt(32'd0),
	.target_dataslot_ack(target_dataslot_ack),
	.target_dataslot_done(target_dataslot_done),
	.target_dataslot_err(target_dataslot_err),
	// HPS status block (REGION_HPS)
	.hps_status(hps_status),
	.hps_img_size(hps_img_size),
	.hps_boot_len(hps_boot_len),
	.hps_img1_size(hps_img1_size),
	.hps_img2_size(hps_img2_size),
	.hps_ini_len(hps_ini_len),
	.hps_elf_len(hps_elf_len),
	// Display control
	.color_mode(color_mode),
	.fb_display_addr(fb_display_addr),
	.fb_width(fb_width),
	.fb_height(fb_height),
	.fb_stride(fb_stride),
	.analog_fb_addr(analog_src_fb_addr),
	.analog_color_mode(analog_src_color_mode),
	.analog_fb_width(analog_src_fb_width),
	.analog_fb_height(analog_src_fb_height),
	.analog_fb_stride(analog_src_fb_stride),
	.video_scaler_slot(video_scaler_slot_cpu),
	// Palette write interface
	.pal_wr(cpu_pal_wr),
	.pal_addr(cpu_pal_addr),
	.pal_data(cpu_pal_data),
	.pal_commit(cpu_pal_commit),
	.pal_busy(cpu_pal_busy),
	// Target dataslot interface → hps_bridge (no CDC)
	.target_dataslot_read(cpu_target_dataslot_read),
	.target_dataslot_write(cpu_target_dataslot_write),
	.target_dataslot_openfile(cpu_target_dataslot_openfile),
	.target_dataslot_getfile(cpu_target_dataslot_getfile),
	.target_dataslot_id(cpu_target_dataslot_id),
	.target_dataslot_slotoffset(cpu_target_dataslot_slotoffset),
	.target_dataslot_bridgeaddr(cpu_target_dataslot_bridgeaddr),
	.target_dataslot_length(cpu_target_dataslot_length),
	.target_buffer_param_struct(),
	.target_buffer_resp_struct(),
	// Audio FIFO status (no FIFO on MiSTer — mixer paces internally)
	.audio_fifo_level(audio_fifo_level_mmio),
	.audio_fifo_full(audio_fifo_full_mmio),
	// Hardware mixer MMIO
	.mix_enable             (mixer_enable_mmio),
	.mix_voice_wr           (mixer_voice_wr_mmio),
	.mix_voice_sel          (mixer_voice_sel_mmio),
	.mix_voice_field        (mixer_voice_field_mmio),
	.mix_voice_wdata        (mixer_voice_wdata_mmio),
	.mix_irq_clear_wr       (mixer_irq_clear_wr_mmio),
	.mix_irq_clear          (mixer_irq_clear_mmio),
	.mix_master_vol         (mixer_master_vol_mmio),
	.mix_group_vol_0        (mixer_group_vol_0_mmio),
	.mix_group_vol_1        (mixer_group_vol_1_mmio),
	.mix_group_vol_2        (mixer_group_vol_2_mmio),
	.mix_group_vol_3        (mixer_group_vol_3_mmio),
	.mix_voice_group_packed (mixer_voice_group_packed_mmio),
	.mix_voice_sel_rd       (mixer_voice_sel_rd_mmio),
	.mix_active_mask        (mixer_active_mask_r),
	.mix_pos_readback       (mixer_pos_readback),  // already registered inside audio_mixer (MLAB read flop) — a 2nd boundary register here overruns the periph FSM's 2-cycle latch budget and returns the PREVIOUS transaction's voice
	.mix_voice_end_pending  (mixer_voice_end_pending_r),
	// CRAM0 ownership mode — no CRAM on MiSTer; register is inert
	.cram0_mode             (),
	// Link MMIO — no link port on MiSTer
	.link_reg_wr(),
	.link_reg_rd(),
	.link_reg_addr(),
	.link_reg_wdata(),
	.link_reg_rdata(link_reg_rdata),
	// UART — no DevKey serial on MiSTer v1
	.uart_tx_dv(),
	.uart_tx_byte(),
	.uart_tx_active(1'b0),
	.uart_rx_dv(1'b0),
	.uart_rx_byte(8'd0),
	// Save datatable — no APF datatable; sizes live in the FAT image
	.save_dt_slot(),
	.save_dt_size(),
	.save_dt_commit(),
	.app_id(32'd0),
	// Analogizer — not present
	.analogizer_settings(32'd0),
	.analogizer_hoffset(32'd0),
	.analogizer_voffset(32'd0),
	.analogizer_cpu_wr_toggle(),
	.analogizer_cpu_wr_settings(),
	.analogizer_cpu_wr_hoffset(),
	.analogizer_cpu_wr_voffset(),
	// Shutdown handshake — MiSTer cores exit by hard reset
	.shutdown_pending(1'b0),
	.shutdown_ack(),
	.timer_irq(timer_irq),
	.uart_rx_irq(uart_rx_irq),
	.link_irq(link_irq),
	.ext_irq(ext_irq),
	// Datatable query — answered instantly with zeros (firmware
	// synthesizes the datatable view in software on MiSTer)
	.dt_query_addr(),
	.dt_query_toggle(),
	.dt_query_data(32'd0),
	.dt_query_valid(1'b1),
	.vrr_v_total(vrr_v_total_cpu),
	.analogizer_enabled(1'b0),
	// SNAC — not present
	.snac_pin_out(),
	.snac_pin_dir(),
	.snac_pin_in(8'd0),
	.snac_enable(),
	// GPU register interface
	.gpu_reg_wr(gpu_reg_wr),
	.gpu_reg_addr(gpu_reg_addr),
	.gpu_reg_wdata(gpu_reg_wdata),
	.gpu_reg_rdata(gpu_reg_rdata),
	.gpu_swap_req(gpu_swap_req),
	.gpu_swap_idx(gpu_swap_idx),
	.fb_swap_pending_o(slave_swap_pending)
);

///////////////////////  SDRAM PATH  /////////////////////////////

// Arbiter ↔ slave bus
wire        arb_s_arvalid, arb_s_arready;
wire [31:0] arb_s_araddr;
wire [7:0]  arb_s_arlen;
wire        arb_s_rvalid, arb_s_rlast, arb_s_rready;
wire [31:0] arb_s_rdata;
wire [1:0]  arb_s_rresp;
wire        arb_s_awvalid, arb_s_awready;
wire [31:0] arb_s_awaddr;
wire [7:0]  arb_s_awlen;
wire        arb_s_wvalid, arb_s_wready, arb_s_wlast;
wire [31:0] arb_s_wdata;
wire [3:0]  arb_s_wstrb;
wire        arb_s_bvalid;
wire [1:0]  arb_s_bresp;
wire        arb_s_wcont;

// GPU AXI master
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

// Mixer AXI master
wire        mixer_arvalid, mixer_arready;
wire [31:0] mixer_araddr;
wire [7:0]  mixer_arlen;
wire        mixer_rvalid;
wire [31:0] mixer_rdata;
wire [1:0]  mixer_rresp;
wire        mixer_rlast;
wire        mixer_rready;

axi_sdram_arbiter sdram_arb (
	.clk(clk_cpu),
	.reset_n(1'b1),
	// M0: GPU (merged read + write)
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
	// M1: CPU
	.m1_arvalid(cpu_m_sdram_arvalid), .m1_arready(cpu_m_sdram_arready),
	.m1_araddr(cpu_m_sdram_araddr),   .m1_arlen(cpu_m_sdram_arlen),
	.m1_rvalid(cpu_m_sdram_rvalid),   .m1_rdata(cpu_m_sdram_rdata),
	.m1_rresp(cpu_m_sdram_rresp),     .m1_rlast(cpu_m_sdram_rlast),
	.m1_rready(cpu_m_sdram_rready),
	.m1_awvalid(cpu_m_sdram_awvalid), .m1_awready(cpu_m_sdram_awready),
	.m1_awaddr(cpu_m_sdram_awaddr),   .m1_awlen(cpu_m_sdram_awlen),
	.m1_wvalid(cpu_m_sdram_wvalid),   .m1_wready(cpu_m_sdram_wready),
	.m1_wdata(cpu_m_sdram_wdata),     .m1_wstrb(cpu_m_sdram_wstrb),
	.m1_wlast(cpu_m_sdram_wlast),
	.m1_bvalid(cpu_m_sdram_bvalid),   .m1_bresp(cpu_m_sdram_bresp),
	// M2: hps_bridge (boot.rom DMA + sector engine, read + write)
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
	// M3: Audio Mixer
	.m3_arvalid(mixer_arvalid),   .m3_arready(mixer_arready),
	.m3_araddr(mixer_araddr),     .m3_arlen(mixer_arlen),
	.m3_rvalid(mixer_rvalid),     .m3_rdata(mixer_rdata),
	.m3_rresp(mixer_rresp),       .m3_rlast(mixer_rlast),
	.m3_rready(mixer_rready),
	// Slave output
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

// Slave → io_sdram word interface
wire        sdram_slave_rd, sdram_slave_wr;
wire [23:0] sdram_slave_addr;
wire [31:0] sdram_slave_wdata;
wire [3:0]  sdram_slave_wstrb;
wire [3:0]  sdram_slave_burst_len;
wire [3:0]  sdram_slave_burst_wr_len;
wire [31:0] sdram_slave_next_wdata;
wire [3:0]  sdram_slave_next_wstrb;
wire [31:0] sdram_slave_preload_wdata;
wire [3:0]  sdram_slave_preload_wstrb;
wire        sdram_slave_wr_data_next;
wire        sdram_slave_wr_done;

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

axi_sdram_slave #(
	.SERIALIZE_WRITE_BURSTS(1'b0),
	.MAX_NATIVE_WRITE_BURST_LEN(8'd15)
) sdram_axi_slave (
	.clk(clk_cpu),
	.reset_n(1'b1),
	.s_axi_arvalid(arb_s_arvalid),
	.s_axi_arready(arb_s_arready),
	.s_axi_araddr(arb_s_araddr),
	.s_axi_arlen(arb_s_arlen),
	.s_axi_rvalid(arb_s_rvalid),
	.s_axi_rready(arb_s_rready),
	.s_axi_rdata(arb_s_rdata),
	.s_axi_rresp(arb_s_rresp),
	.s_axi_rlast(arb_s_rlast),
	.s_axi_awvalid(arb_s_awvalid),
	.s_axi_awready(arb_s_awready),
	.s_axi_awaddr(arb_s_awaddr),
	.s_axi_awlen(arb_s_awlen),
	.s_axi_wvalid(arb_s_wvalid),
	.s_axi_wready(arb_s_wready),
	.s_axi_wdata(arb_s_wdata),
	.s_axi_wstrb(arb_s_wstrb),
	.s_axi_wlast(arb_s_wlast),
	.s_axi_bvalid(arb_s_bvalid),
	.s_axi_bready(1'b1),
	.s_axi_bresp(arb_s_bresp),
	.s_axi_wcont(arb_s_wcont),
	.sdram_rd(sdram_slave_rd),
	.sdram_wr(sdram_slave_wr),
	.sdram_addr(sdram_slave_addr),
	.sdram_wdata(sdram_slave_wdata),
	.sdram_wstrb(sdram_slave_wstrb),
	.sdram_burst_len(sdram_slave_burst_len),
	.sdram_burst_wr_len(sdram_slave_burst_wr_len),
	.sdram_rdata(ram1_word_q),
	.sdram_busy(ram1_word_busy),
	.sdram_accepted(sdram_accepted_r),
	.sdram_rdata_valid(ram1_word_q_valid),
	.sdram_wr_data_next(sdram_slave_wr_data_next),
	.sdram_wr_done(sdram_slave_wr_done),
	.sdram_next_wdata(sdram_slave_next_wdata),
	.sdram_next_wstrb(sdram_slave_next_wstrb),
	.sdram_preload_wdata(sdram_slave_preload_wdata),
	.sdram_preload_wstrb(sdram_slave_preload_wstrb)
);

// Slave → io_sdram pulse adapter (verbatim from core_top.v)
reg sdram_accepted_r;
reg sdram_cmd_forwarded;
always @(posedge clk_ram_controller) begin
	ram1_word_rd <= 0;
	ram1_word_wr <= 0;
	ram1_word_burst_len <= 4'd0;
	ram1_word_burst_wr_len <= 4'd0;
	sdram_accepted_r <= 0;

	if (!sdram_slave_rd && !sdram_slave_wr)
		sdram_cmd_forwarded <= 0;

	if (!ram1_word_busy && !sdram_cmd_forwarded &&
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

///////////////////////  SCANOUT + RASTER  ///////////////////////

reg [9:0]  x_count;
reg [9:0]  y_count;
reg [23:0] vidout_rgb;
reg        vidout_de;
reg        vidout_vs;
reg        vidout_hs;
reg        crt_hs, crt_vs;

assign vidout_vs_w = vidout_vs;

localparam CRT_V_SYNC   = 3;
localparam CRT_V_BPORCH = 15;
localparam CRT_H_SYNC   = 58;
localparam CRT_H_BPORCH = 62;
localparam CRT_H_FPORCH = 20;
localparam CRT_H_TOTAL  = CRT_H_SYNC + CRT_H_BPORCH + 640 + CRT_H_FPORCH;
localparam [9:0] CRT_V_ACTIVE_BASE = CRT_V_SYNC + CRT_V_BPORCH;
localparam [9:0] CRT_V_CENTER_HEIGHT = 10'd240;
// Fixed 60.0 Hz — ascal wants a stable input frame rate, so the
// firmware VRR register is accepted but ignored on MiSTer v1.
localparam [9:0] CRT_V_TOTAL_60HZ = 10'd525;

function [9:0] scaler_slot_width;
	input [2:0] slot;
	begin
		case (slot)
			3'd5: scaler_slot_width = 10'd400;
			3'd6: scaler_slot_width = 10'd256;
			3'd7: scaler_slot_width = 10'd640;
			default: scaler_slot_width = 10'd320;
		endcase
	end
endfunction

function [9:0] scaler_slot_height;
	input [2:0] slot;
	begin
		case (slot)
			3'd1: scaler_slot_height = 10'd200;
			3'd2: scaler_slot_height = 10'd224;
			3'd3: scaler_slot_height = 10'd256;
			3'd4: scaler_slot_height = 10'd288;
			3'd5: scaler_slot_height = 10'd300;
			3'd7: scaler_slot_height = 10'd480;
			default: scaler_slot_height = 10'd240;
		endcase
	end
endfunction

// v1: pin the raster to the FULL 640x480 active window for every mode and
// let the scanout DDA upscale the app framebuffer to fill it (the exact
// path the Pocket uses for Analogizer RGBHV, and the tb_scanout UPSCALE
// test).  Hardware observation 2026-06-12: with the slot-sized windows
// (e.g. Doom/Quake at 320x200) the ascal INPUT stage is fine — OSD
// screenshots capture the game correctly — but the HDMI output stays
// BLANK; the terminal and Diablo at the full 640x480 window are solid on
// the same chain.  Pinning the window makes the framework see a constant
// 640x480@60 input regardless of app mode, sidestepping whatever the
// ARM-side mode filter dislikes about a 200-line active region on a
// 31.5 kHz raster.  The slot functions above are kept for the future
// native-window revival (single-pass ascal scale is crisper than
// DDA-then-ascal); video_scaler_slot_vid keeps arriving from the periph.
wire [9:0] crt_h_active = 10'd640;
wire [9:0] crt_v_active = 10'd480;
// Lint: slot-sized windows parked until the framework blank is
// root-caused; keep the decode functions referenced but folded.
wire [9:0] crt_unused_slot_dims =
	scaler_slot_width(video_scaler_slot_vid) ^
	scaler_slot_height(video_scaler_slot_vid);
wire crt_unused_slot_dims_nc = &{1'b0, crt_unused_slot_dims};
wire [9:0] crt_v_center_offset =
	(crt_v_active < CRT_V_CENTER_HEIGHT) ?
	((CRT_V_CENTER_HEIGHT - crt_v_active) >> 1) : 10'd0;
wire [9:0] crt_h_active_end  = CRT_H_SYNC + CRT_H_BPORCH + crt_h_active;
wire [9:0] crt_v_active_start = CRT_V_ACTIVE_BASE + crt_v_center_offset;
wire [9:0] crt_v_active_end   = crt_v_active_start + crt_v_active;

assign early_vblank_safe_vid = (y_count < crt_v_active_start);

wire [23:0] framebuffer_pixel_color;
reg line_start;
always @(posedge clk_vid) begin
	line_start <= (x_count == 0);
end

// SDRAM burst interface for video scanout (client V of the burst arbiter)
wire        video_burst_rd;
wire [24:0] video_burst_addr;
wire [10:0] video_burst_len;
wire        video_burst_32bit;
wire [31:0] video_burst_data;      // shared read-data bus from io_sdram
wire        video_burst_data_valid;
wire        video_burst_data_done;

// Frame-DMA client (client D) + arbiter->io_sdram side
wire        fbdma_req, fbdma_gnt;
wire [24:0] fbdma_addr;
wire [10:0] fbdma_len;
wire        fbdma_data_valid, fbdma_data_done;
wire        arb_burst_rd;
wire [24:0] arb_burst_addr;
wire [10:0] arb_burst_len;
wire        arb_burst_32bit;
wire        isr_burst_data_valid;
wire        isr_burst_data_done;

video_burst_arb vburst_arb (
	.clk(clk_ram_controller),
	.reset_n(reset_n_cpu_core),
	.vid_rd(video_burst_rd),
	.vid_addr(video_burst_addr),
	.vid_len(video_burst_len),
	.vid_32bit(video_burst_32bit),
	.vid_data_valid(video_burst_data_valid),
	.vid_data_done(video_burst_data_done),
	.dma_req(fbdma_req),
	.dma_addr(fbdma_addr),
	.dma_len(fbdma_len),
	.dma_gnt(fbdma_gnt),
	.dma_data_valid(fbdma_data_valid),
	.dma_data_done(fbdma_data_done),
	.burst_rd(arb_burst_rd),
	.burst_addr(arb_burst_addr),
	.burst_len(arb_burst_len),
	.burst_32bit(arb_burst_32bit),
	.burst_data_valid(isr_burst_data_valid),
	.burst_data_done(isr_burst_data_done)
);

video_CRT_scanout_indexed_BRAM scanout (
	.clk_video(clk_vid),
	.reset_n(reset_n_vid),
	.x_count(x_count),
	.y_count(y_count),
	.line_start(line_start),
	.pixel_color(framebuffer_pixel_color),
	// Dedicated 15 kHz analog raster unused on MiSTer v1 — the path is
	// parked in 480p mode (no fetches) and its outputs are ignored.
	.clk_analog(clk_vid),
	.reset_analog_n(reset_n_vid),
	.analog_ce_pix(),
	.analog_scanlines(2'b00),
	.analog_timing(2'd1),    // 480p (analog raster dormant on MiSTer)
	.analog_pixel_clk(),
	.analog_pixel_color(),
	.analog_hblank(),
	.analog_vblank(),
	.analog_hsync(),
	.analog_vsync(),
	.fb_base_addr(fb_display_addr),
	.color_mode(color_mode),
	.fb_width(fb_width),
	.fb_height(fb_height),
	.fb_stride(fb_stride),
	.out_width(crt_h_active),
	.out_height(crt_v_active),
	.analog_fb_base_addr(analog_src_fb_addr),
	.analog_color_mode(analog_src_color_mode),
	.analog_fb_width(analog_src_fb_width),
	.analog_fb_height(analog_src_fb_height),
	.analog_fb_stride(analog_src_fb_stride),
	.analog_out_height(crt_v_active),
	.clk_palette(clk_cpu),
	.reset_palette_n(reset_n_cpu_core),
	.clk_sdram(clk_ram_controller),
	.burst_rd(video_burst_rd),
	.burst_addr(video_burst_addr),
	.burst_len(video_burst_len),
	.burst_32bit(video_burst_32bit),
	.burst_data(video_burst_data),
	.burst_data_valid(video_burst_data_valid),
	.burst_data_done(video_burst_data_done),
	.pal_wr(cpu_pal_wr),
	.pal_addr(cpu_pal_addr),
	.pal_data(cpu_pal_data),
	.pal_commit(cpu_pal_commit),
	.pal_busy(cpu_pal_busy)
);

// ---- Direct-framebuffer pipeline (DDR3) --------------------------------
// Copies the displayed SDRAM frame into a 2-slot DDR3 ring each frame and
// exposes it through the framework's MISTER_FB interface; the palette
// stream from the periph slave is mirrored into ascal's core palette.
// The legacy scanout->VGA path above keeps running as the fallback (OSD
// "Video Output: Original", or automatically for unsupported modes).
wire fb_direct_enable = ~status[1];

ddr3_fb fbpipe (
	.clk(clk_ram_controller),
	.reset_n(reset_n_cpu_core),
	.enable(fb_direct_enable),
	.clk_vid(clk_vid),
	.crt_vs(crt_vs),
	.fb_display_addr(fb_display_addr),
	.color_mode(color_mode),
	.fb_width(fb_width),
	.fb_height(fb_height),
	.fb_stride(fb_stride),
	.dma_req(fbdma_req),
	.dma_gnt(fbdma_gnt),
	.dma_addr(fbdma_addr),
	.dma_len(fbdma_len),
	.dma_data(video_burst_data),
	.dma_data_valid(fbdma_data_valid),
	.dma_data_done(fbdma_data_done),
	.DDRAM_CLK(DDRAM_CLK),
	.DDRAM_BUSY(DDRAM_BUSY),
	.DDRAM_BURSTCNT(DDRAM_BURSTCNT),
	.DDRAM_ADDR(DDRAM_ADDR),
	.DDRAM_DOUT(DDRAM_DOUT),
	.DDRAM_DOUT_READY(DDRAM_DOUT_READY),
	.DDRAM_RD(DDRAM_RD),
	.DDRAM_DIN(DDRAM_DIN),
	.DDRAM_BE(DDRAM_BE),
	.DDRAM_WE(DDRAM_WE),
`ifdef MISTER_FB
	.FB_EN(FB_EN),
	.FB_FORMAT(FB_FORMAT),
	.FB_WIDTH(FB_WIDTH),
	.FB_HEIGHT(FB_HEIGHT),
	.FB_BASE(FB_BASE),
	.FB_STRIDE(FB_STRIDE),
	.FB_VBL(FB_VBL),
	.FB_FORCE_BLANK(FB_FORCE_BLANK),
`ifdef MISTER_FB_PALETTE
	.FB_PAL_CLK(FB_PAL_CLK),
	.FB_PAL_ADDR(FB_PAL_ADDR),
	.FB_PAL_DOUT(FB_PAL_DOUT),
	.FB_PAL_WR(FB_PAL_WR),
`endif
`else
	// MISTER_FB not defined in this build: pipeline outputs dangle and
	// the palette streamer times off the input vsync instead of FB_VBL.
	.FB_VBL(crt_vs),
`endif
	.pal_wr(cpu_pal_wr),
	.pal_addr(cpu_pal_addr),
	.pal_data(cpu_pal_data),
	.pal_commit(cpu_pal_commit)
);

always @(posedge clk_vid or negedge reset_n_vid) begin
	if (~reset_n_vid) begin
		x_count <= 0;
		y_count <= 0;
		vidout_rgb <= 24'd0;
		vidout_de <= 0;
		vidout_vs <= 0;
		vidout_hs <= 0;
		crt_hs <= 0;
		crt_vs <= 0;
	end else begin
		vidout_de <= 0;
		vidout_vs <= 0;
		vidout_hs <= 0;
		vidout_rgb <= 24'd0;

		x_count <= x_count + 1'b1;
		if (x_count == CRT_H_TOTAL-1) begin
			x_count <= 0;
			y_count <= y_count + 1'b1;
			if (y_count >= CRT_V_TOTAL_60HZ - 1)
				y_count <= 0;
		end

		// Full-width syncs for the framework scaler (active high; the
		// Pocket's vidout_hs/vs are 1-cycle APF markers — those are kept
		// only as the periph's frame tick below).
		crt_hs <= (x_count < CRT_H_SYNC);
		crt_vs <= (y_count < CRT_V_SYNC);

		if (x_count == 0 && y_count == 0)
			vidout_vs <= 1;
		if (x_count == 3)
			vidout_hs <= 1;

		if (x_count >= CRT_H_SYNC + CRT_H_BPORCH && x_count < crt_h_active_end) begin
			if (y_count >= crt_v_active_start && y_count < crt_v_active_end) begin
				vidout_de <= 1;
				vidout_rgb <= framebuffer_pixel_color;
			end
		end
	end
end

assign CLK_VIDEO = clk_vid;
assign CE_PIXEL  = 1'b1;
assign VGA_R  = vidout_rgb[23:16];
assign VGA_G  = vidout_rgb[15:8];
assign VGA_B  = vidout_rgb[7:0];
assign VGA_DE = vidout_de;
assign VGA_HS = crt_hs;
assign VGA_VS = crt_vs;

///////////////////////  AUDIO  //////////////////////////////////

wire        mixer_sample_wr;
wire [31:0] mixer_sample_data;

audio_mixer audio_mixer_inst (
	.clk              (clk_cpu),
	.reset_n          (reset_n_cpu_media),
	.mixer_enable     (mixer_enable_mmio),
	.voice_wr         (mixer_voice_wr_mmio),
	.voice_field      (mixer_voice_field_mmio),
	.voice_sel        (mixer_voice_sel_mmio),
	.voice_sel_rd     (mixer_voice_sel_rd_mmio),
	.voice_wdata      (mixer_voice_wdata_mmio),
	.master_vol       (mixer_master_vol_mmio),
	.group_vol_0      (mixer_group_vol_0_mmio),
	.group_vol_1      (mixer_group_vol_1_mmio),
	.group_vol_2      (mixer_group_vol_2_mmio),
	.group_vol_3      (mixer_group_vol_3_mmio),
	.voice_group_packed(mixer_voice_group_packed_mmio),
	.m_arvalid        (mixer_arvalid),
	.m_arready        (mixer_arready),
	.m_araddr         (mixer_araddr),
	.m_arlen          (mixer_arlen),
	.m_rvalid         (mixer_rvalid),
	.m_rdata          (mixer_rdata),
	.m_rresp          (mixer_rresp),
	.m_rlast          (mixer_rlast),
	.m_rready         (mixer_rready),
	.sample_wr        (mixer_sample_wr),
	.sample_data      (mixer_sample_data),
	.fifo_level       (audio_fifo_level),
	.pos_readback     (mixer_pos_readback),
	.irq_clear_wr     (mixer_irq_clear_wr_mmio),
	.irq_clear        (mixer_irq_clear_mmio),
	.voice_end_pending(mixer_voice_end_pending),
	.voice_end_irq    (),
	.voice_active_mask(mixer_active_mask)
);

// Latch the 48 kHz stereo pair for sys_top's audio chain (it handles
// the clock crossing; AUDIO_S=1 marks the samples signed).
reg [15:0] audio_l_reg, audio_r_reg;
always @(posedge clk_cpu) begin
	if (mixer_sample_wr) begin
		audio_l_reg <= mixer_sample_data[31:16];
		audio_r_reg <= mixer_sample_data[15:0];
	end
end
assign AUDIO_L = audio_l_reg;
assign AUDIO_R = audio_r_reg;

///////////////////////  GPU  ////////////////////////////////////

// GPU-private LUT scratch (BRAM-backed; external SRAM on the Pocket)
wire        sram_word_rd, sram_word_wr;
wire        sram_word_rd_half, sram_word_rd_hi;
wire [21:0] sram_word_addr;
wire [31:0] sram_word_wdata;
wire [3:0]  sram_word_wstrb;
wire [31:0] sram_word_rdata;
wire        sram_word_busy;
wire        sram_word_rdata_valid;

sram_bram gpu_lut_sram (
	.clk(clk_ram_controller),
	.reset_n(1'b1),
	.word_rd(sram_word_rd),
	.word_wr(sram_word_wr),
	.word_rd_half(sram_word_rd_half),
	.word_rd_hi(sram_word_rd_hi),
	.word_addr(sram_word_addr),
	.word_data(sram_word_wdata),
	.word_wstrb(sram_word_wstrb),
	.word_q(sram_word_rdata),
	.word_busy(sram_word_busy),
	.word_q_valid(sram_word_rdata_valid)
);

gpu_core #(
	// MiSTer feature set: triangles + 2.5D fastpaths all IN; fast texture
	// memory OUT (no CRAM1 chip).  Each INCLUDE_* echoes its build macro and
	// MUST match the periph's same-named param above.
	.INCLUDE_PARAM_TRI(`ifdef INCLUDE_PARAM_TRI 1 `else 0 `endif),
	.INCLUDE_VERT_TRI(`ifdef INCLUDE_VERT_TRI 1 `else 0 `endif),
	.INCLUDE_PARAM_TRI_RECS(`ifdef INCLUDE_PARAM_TRI_RECS 1 `else 0 `endif),
	.INCLUDE_COMPACT_SPAN(`ifdef INCLUDE_COMPACT_SPAN 1 `else 0 `endif),
	.INCLUDE_COLUMN_LIST(`ifdef INCLUDE_COLUMN_LIST 1 `else 0 `endif),
	.INCLUDE_TEX_MEM(`ifdef INCLUDE_TEX_MEM 1 `else 0 `endif),
	// 32 KB tex/cmap cache (2048 sets x 16 B) vs the Pocket's 16 KB
	// default: with no CRAM1 every texel + cmap read is SDRAM-backed
	// through this cache.  Size-only knob — protocol and wiring unchanged.
	// M10K cost is ~70 blocks (the dual-read data RAM is REPLICATED, one
	// copy per read port, so every extra SET_BIT doubles TWO copies);
	// 12 = 64 KB costs ~140 blocks and only fits if the D$ stays 128 KB —
	// 11 keeps ~119 blocks free for a future BRAM framebuffer.
	.GPU_TEX_CACHE_SET_BITS(11)
) gpu (
	.clk(clk_cpu),
	.reset_n(reset_n_cpu_media),
	.gpu_enable(1'b1),
	.m_rd_arvalid(gpu_rd_arvalid),
	.m_rd_arready(gpu_rd_arready),
	.m_rd_araddr(gpu_rd_araddr),
	.m_rd_arlen(gpu_rd_arlen),
	.m_rd_rvalid(gpu_rd_rvalid),
	.m_rd_rdata(gpu_rd_rdata),
	.m_rd_rlast(gpu_rd_rlast),
	// Fast texture memory not present on MiSTer (INCLUDE_TEX_MEM 0): tie the
	// fill-master inputs idle and leave the outputs unconnected.  The
	// upload-reg ports are unused (no fast-tex controller).
	.gpu_tex_mem_arready(1'b0),
	.gpu_tex_mem_rvalid(1'b0),
	.gpu_tex_mem_rdata(32'b0),
	.gpu_tex_mem_rlast(1'b0),
	.gpu_tex_mem_up_busy(1'b0),
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
	.sram_rd(sram_word_rd),
	.sram_wr(sram_word_wr),
	.sram_rd_half(sram_word_rd_half),
	.sram_rd_hi(sram_word_rd_hi),
	.sram_addr(sram_word_addr),
	.sram_wdata(sram_word_wdata),
	.sram_wstrb(sram_word_wstrb),
	.sram_rdata(sram_word_rdata),
	.sram_busy(sram_word_busy),
	.sram_rdata_valid(sram_word_rdata_valid),
	.reg_wr(gpu_reg_wr),
	.reg_addr(gpu_reg_addr),
	.reg_wdata(gpu_reg_wdata),
	.reg_rdata(gpu_reg_rdata),
	.gpu_swap_req(gpu_swap_req),
	.gpu_swap_idx(gpu_swap_idx),
	.slave_swap_pending(slave_swap_pending),
	.busy(),
	.fence_reached()
);

///////////////////////  SDRAM PHY  //////////////////////////////

// The MiSTer SDRAM module shares the Pocket's chip family/pinout; the
// only extra pin is nCS, tied active (io_sdram idles with NOP commands).
assign SDRAM_nCS = 1'b0;

// SDRAM CK: DDIO-forwarded inverted controller clock — the standard MiSTer
// core scheme (see any MiSTer-devel sdram.v).  The clock leaves through the
// same IOB structure as the FAST_OUTPUT_REGISTER-packed data/control pins,
// so clock-vs-data pin delay is matched by construction and the chip samples
// half a period (5 ns) after the IOB launch edge.  Replaces forwarding the
// raw PLL outclk_1 (6750 ps — the POCKET board's tuned phase) out as a data
// signal, which needed per-board phase tuning the DDIO scheme does not.
// Validated on HW 2026-07-02 (boot, word/burst read+write, memtest-clean
// module).  clk_ram_chip is now unused here.
// (The late-2026-07-02 phase-sweep experiments that temporarily bypassed
// this with raw PLL forwarding were retroactively invalidated: those
// black boots were the kernel-layout boot lottery, not the clock scheme.
// This DDIO structure is what the deployed, user-validated fits carry.)
altddio_out #(
	.extend_oe_disable("OFF"),
	.intended_device_family("Cyclone V"),
	.invert_output("OFF"),
	.lpm_hint("UNUSED"),
	.lpm_type("altddio_out"),
	.oe_reg("UNREGISTERED"),
	.power_up_high("OFF"),
	.width(1)
) sdramclk_ddr (
	.datain_h(1'b0),
	.datain_l(1'b1),
	.outclock(clk_ram_controller),
	.dataout(SDRAM_CLK),
	.aclr(1'b0),
	.aset(1'b0),
	.oe(1'b1),
	.oe_out(),
	.outclocken(1'b1),
	.sclr(1'b0),
	.sset(1'b0)
);

wire unused_phy_clk;

io_sdram isr0 (
	.controller_clk ( clk_ram_controller ),
	.chip_clk       ( clk_ram_chip ),
	.clk_90         ( clk_ram_chip ),
	.reset_n        ( 1'b1 ),

	.phy_cke        ( SDRAM_CKE ),
	.phy_clk        ( unused_phy_clk ),
	.phy_cas        ( SDRAM_nCAS ),
	.phy_ras        ( SDRAM_nRAS ),
	.phy_we         ( SDRAM_nWE ),
	.phy_ba         ( SDRAM_BA ),
	.phy_a          ( SDRAM_A ),
	.phy_dq         ( SDRAM_DQ ),
	.phy_dqm        ( {SDRAM_DQMH, SDRAM_DQML} ),

	// Burst interface — video scanout + frame DMA via video_burst_arb
	.burst_rd           ( arb_burst_rd ),
	.burst_addr         ( arb_burst_addr ),
	.burst_len          ( arb_burst_len ),
	.burst_32bit        ( arb_burst_32bit ),
	.burst_data         ( video_burst_data ),
	.burst_data_valid   ( isr_burst_data_valid ),
	.burst_data_done    ( isr_burst_data_done ),

	// Burst write interface — not used
	.burstwr        ( 1'b0 ),
	.burstwr_addr   ( 25'b0 ),
	.burstwr_ready  ( ),
	.burstwr_strobe ( 1'b0 ),
	.burstwr_data   ( 16'b0 ),
	.burstwr_done   ( 1'b0 ),
	.dbg_io         ( ),

	// Word interface — CPU access via AXI
	.word_rd    ( ram1_word_rd ),
	.word_wr    ( ram1_word_wr ),
	.word_addr  ( ram1_word_addr ),
	.word_data  ( ram1_word_data ),
	.word_wstrb ( ram1_word_wstrb ),
	.word_data_next ( ram1_word_data_next ),
	.word_wstrb_next ( ram1_word_wstrb_next ),
	.word_burst_len ( ram1_word_burst_len ),
	.word_burst_wr_len ( ram1_word_burst_wr_len ),
	.word_q     ( ram1_word_q ),
	.word_busy  ( ram1_word_busy ),
	.word_q_valid ( ram1_word_q_valid ),
	.word_wr_data_next ( sdram_slave_wr_data_next ),
	.word_wr_done ( sdram_slave_wr_done ),
	.burst_wr_direct_data ( sdram_slave_next_wdata ),
	.burst_wr_direct_strb ( sdram_slave_next_wstrb )
);

endmodule
