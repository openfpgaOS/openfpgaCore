//
// User core top-level (openfpgaOS)
//
// VexiiRiscv CPU with AXI4 bus architecture, 32-voice PCM mixer.
// Instantiated by the real top-level: apf_top
//

`default_nettype none

module core_top (

//
// physical connections
//

///////////////////////////////////////////////////
// clock inputs 74.25mhz. not phase aligned, so treat these domains as asynchronous

input   wire            clk_74a, // mainclk1
input   wire            clk_74b, // mainclk1

///////////////////////////////////////////////////
// cartridge interface
// switches between 3.3v and 5v mechanically
// output enable for multibit translators controlled by pic32

// GBA AD[15:8]
inout   wire    [7:0]   cart_tran_bank2,
output  wire            cart_tran_bank2_dir,

// GBA AD[7:0]
inout   wire    [7:0]   cart_tran_bank3,
output  wire            cart_tran_bank3_dir,

// GBA A[23:16]
inout   wire    [7:0]   cart_tran_bank1,
output  wire            cart_tran_bank1_dir,

// GBA [7] PHI#
// GBA [6] WR#
// GBA [5] RD#
// GBA [4] CS1#/CS#
//     [3:0] unwired
inout   wire    [7:4]   cart_tran_bank0,
output  wire            cart_tran_bank0_dir,

// GBA CS2#/RES#
inout   wire            cart_tran_pin30,
output  wire            cart_tran_pin30_dir,
output  wire            cart_pin30_pwroff_reset,

// GBA IRQ/DRQ
inout   wire            cart_tran_pin31,
output  wire            cart_tran_pin31_dir,

// infrared
input   wire            port_ir_rx,
output  wire            port_ir_tx,
output  wire            port_ir_rx_disable,

// GBA link port
inout   wire            port_tran_si,
output  wire            port_tran_si_dir,
inout   wire            port_tran_so,
output  wire            port_tran_so_dir,
inout   wire            port_tran_sck,
output  wire            port_tran_sck_dir,
inout   wire            port_tran_sd,
output  wire            port_tran_sd_dir,

///////////////////////////////////////////////////
// cellular psram 0 and 1, two chips (64mbit x2 dual die per chip)

output  wire    [21:16] cram0_a,
inout   wire    [15:0]  cram0_dq,
input   wire            cram0_wait,
output  wire            cram0_clk,
output  wire            cram0_adv_n,
output  wire            cram0_cre,
output  wire            cram0_ce0_n,
output  wire            cram0_ce1_n,
output  wire            cram0_oe_n,
output  wire            cram0_we_n,
output  wire            cram0_ub_n,
output  wire            cram0_lb_n,

// cram1 revived as a dedicated GPU sync-burst texture memory (Phase 2).
output  wire    [21:16] cram1_a,
inout   wire    [15:0]  cram1_dq,
input   wire            cram1_wait,
output  wire            cram1_clk,
output  wire            cram1_adv_n,
output  wire            cram1_cre,
output  wire            cram1_ce0_n,
output  wire            cram1_ce1_n,
output  wire            cram1_oe_n,
output  wire            cram1_we_n,
output  wire            cram1_ub_n,
output  wire            cram1_lb_n,

///////////////////////////////////////////////////
// sdram, 512mbit 16bit

output  wire    [12:0]  dram_a,
output  wire    [1:0]   dram_ba,
inout   wire    [15:0]  dram_dq,
output  wire    [1:0]   dram_dqm,
output  wire            dram_clk,
output  wire            dram_cke,
output  wire            dram_ras_n,
output  wire            dram_cas_n,
output  wire            dram_we_n,

///////////////////////////////////////////////////
// sram, 1mbit 16bit

output  wire    [16:0]  sram_a,
inout   wire    [15:0]  sram_dq,
output  wire            sram_oe_n,
output  wire            sram_we_n,
output  wire            sram_ub_n,
output  wire            sram_lb_n,

///////////////////////////////////////////////////
// vblank driven by dock for sync in a certain mode

input   wire            vblank,

///////////////////////////////////////////////////
// i/o to 6515D breakout usb uart

output  wire            dbg_tx,
input   wire            dbg_rx,

///////////////////////////////////////////////////
// i/o pads near jtag connector user can solder to

output  wire            user1,
input   wire            user2,

///////////////////////////////////////////////////
// RFU internal i2c bus

inout   wire            aux_sda,
output  wire            aux_scl,

///////////////////////////////////////////////////
// RFU, do not use
output  wire            vpll_feed,


//
// logical connections
//

///////////////////////////////////////////////////
// video, audio output to scaler
output  wire    [23:0]  video_rgb,
output  wire            video_rgb_clock,
output  wire            video_rgb_clock_90,
output  wire            video_de,
output  wire            video_skip,
output  wire            video_vs,
output  wire            video_hs,

output  wire            audio_mclk,
input   wire            audio_adc,
output  wire            audio_dac,
output  wire            audio_lrck,

///////////////////////////////////////////////////
// bridge bus connection
// synchronous to clk_74a
output  wire            bridge_endian_little,
input   wire    [31:0]  bridge_addr,
input   wire            bridge_rd,
output  reg     [31:0]  bridge_rd_data,
input   wire            bridge_wr,
input   wire    [31:0]  bridge_wr_data,

///////////////////////////////////////////////////
// controller data
input   wire    [31:0]  cont1_key,
input   wire    [31:0]  cont2_key,
input   wire    [31:0]  cont3_key,
input   wire    [31:0]  cont4_key,
input   wire    [31:0]  cont1_joy,
input   wire    [31:0]  cont2_joy,
input   wire    [31:0]  cont3_joy,
input   wire    [31:0]  cont4_joy,
input   wire    [15:0]  cont1_trig,
input   wire    [15:0]  cont2_trig,
input   wire    [15:0]  cont3_trig,
input   wire    [15:0]  cont4_trig

);

// not using the IR port, so turn off both the LED, and
// disable the receive circuit to save power
assign port_ir_tx = 0;
assign port_ir_rx_disable = 1;

// bridge endianness
assign bridge_endian_little = 1;

// ============================================================
// Analogizer adapter (optional, directly controls cart port)
// ============================================================

// Pocket Menu settings
reg [31:0] analogizer_settings = 32'd0;
reg [31:0] signed_hoff = 32'd0;
reg [31:0] signed_voff = 32'd0;

// App ID from instance JSON memory_writes (bridge 0xF7000010)
reg [31:0] app_id_74a;
reg [31:0] app_id_sync1, app_id_sync2;
always @(posedge clk_cpu) begin
    app_id_sync1 <= app_id_74a;
    app_id_sync2 <= app_id_sync1;
end

reg [31:0] analogizer_settings_cpu_s1, analogizer_settings_cpu;
reg [31:0] signed_hoff_cpu_s1, signed_hoff_cpu;
reg [31:0] signed_voff_cpu_s1, signed_voff_cpu;
reg [31:0] analogizer_settings_core_s1, analogizer_settings_core;
reg [31:0] signed_hoff_core_s1, signed_hoff_core;
reg [31:0] signed_voff_core_s1, signed_voff_core;
reg [31:0] analogizer_settings_vid_s1, analogizer_settings_vid;

always @(posedge clk_cpu) begin
  analogizer_settings_cpu_s1 <= analogizer_settings;
  analogizer_settings_cpu    <= analogizer_settings_cpu_s1;
  signed_hoff_cpu_s1         <= signed_hoff;
  signed_hoff_cpu            <= signed_hoff_cpu_s1;
  signed_voff_cpu_s1         <= signed_voff;
  signed_voff_cpu            <= signed_voff_cpu_s1;
end

always @(posedge clk_core_49152) begin
  analogizer_settings_core_s1 <= analogizer_settings;
  analogizer_settings_core    <= analogizer_settings_core_s1;
  signed_hoff_core_s1         <= signed_hoff;
  signed_hoff_core            <= signed_hoff_core_s1;
  signed_voff_core_s1         <= signed_voff;
  signed_voff_core            <= signed_voff_core_s1;
end

always @(posedge clk_vid) begin
  analogizer_settings_vid_s1 <= analogizer_settings;
  analogizer_settings_vid    <= analogizer_settings_vid_s1;
end

wire [2:0]  analogizer_cpu_wr_toggle;
wire [31:0] analogizer_cpu_wr_settings;
wire [31:0] analogizer_cpu_wr_hoffset;
wire [31:0] analogizer_cpu_wr_voffset;

reg [2:0]  analogizer_cpu_wr_toggle_s1;
reg [2:0]  analogizer_cpu_wr_toggle_s2;
reg [2:0]  analogizer_cpu_wr_toggle_s3;
reg [31:0] analogizer_cpu_wr_settings_s1, analogizer_cpu_wr_settings_s2;
reg [31:0] analogizer_cpu_wr_hoffset_s1, analogizer_cpu_wr_hoffset_s2;
reg [31:0] analogizer_cpu_wr_voffset_s1, analogizer_cpu_wr_voffset_s2;

always @(posedge clk_74a) begin
  analogizer_cpu_wr_toggle_s1 <= analogizer_cpu_wr_toggle;
  analogizer_cpu_wr_toggle_s2 <= analogizer_cpu_wr_toggle_s1;
  analogizer_cpu_wr_toggle_s3 <= analogizer_cpu_wr_toggle_s2;
  analogizer_cpu_wr_settings_s1 <= analogizer_cpu_wr_settings;
  analogizer_cpu_wr_settings_s2 <= analogizer_cpu_wr_settings_s1;
  analogizer_cpu_wr_hoffset_s1 <= analogizer_cpu_wr_hoffset;
  analogizer_cpu_wr_hoffset_s2 <= analogizer_cpu_wr_hoffset_s1;
  analogizer_cpu_wr_voffset_s1 <= analogizer_cpu_wr_voffset;
  analogizer_cpu_wr_voffset_s2 <= analogizer_cpu_wr_voffset_s1;
end

wire [2:0] analogizer_cpu_wr_event =
  analogizer_cpu_wr_toggle_s2 ^ analogizer_cpu_wr_toggle_s3;

wire [4:0] snac_cont_type = analogizer_settings[4:0];
wire [3:0] snac_cont_assignment = analogizer_settings[9:6];
wire       analogizer_ena_cpu  = analogizer_settings_cpu[15];
wire       analogizer_ena_core = analogizer_settings_core[15];
wire       analogizer_ena_vid  = analogizer_settings_vid[15];
wire [3:0] analogizer_video_type_core = analogizer_settings_core[13:10];
wire [3:0] analogizer_video_type_vid  = analogizer_settings_vid[13:10];
wire       analogizer_15khz_core = (analogizer_video_type_core[2:0] <= 3'd4);
wire       analogizer_15khz_vid  = (analogizer_video_type_vid[2:0] <= 3'd4);
// "Analogizer Timing" menu field, bits 19:18 of the settings register:
// 00=240p (default, pre-existing), 01=480i NTSC, 10=576i PAL.  Only the
// 15 kHz modes consume it; RGBHV/scandoubler stays on the 480p raster.
wire [1:0] analogizer_timing_sel_vid = analogizer_settings_vid[19:18];
// Scanout analog raster timing select (ATIMING_* in the scanout module):
// 0=240p, 1=480p, 2=480i, 3=576i.
wire [1:0] analog_timing_vid =
    (!analogizer_ena_vid || !analogizer_15khz_vid) ? 2'd1 :
    (analogizer_timing_sel_vid == 2'd1)            ? 2'd2 :
    (analogizer_timing_sel_vid == 2'd2)            ? 2'd3 : 2'd0;



    // "Pocket LCD" interact variable, bits 17:16 of the analogizer settings
    // register: 00=On (normal), 01=Off (blank the Pocket LCD), 10=Terminal
    // (firmware shows the OS console on the LCD; handled in software).  Off is
    // gated on the Analogizer being enabled so it can never blank the only
    // visible output -- the analog DAC keeps showing the app via its own fetch.
    // (The old bit-13 "Pocket OFF" kill-switch is intentionally NOT used here.)
    wire [1:0] pocket_lcd_mode_vid = analogizer_settings_vid[17:16];
    wire pocket_blank_screen =
        (pocket_lcd_mode_vid == 2'b01) && analogizer_ena_vid;

    wire [23:0] video_rgb_core;
    // Blank ONLY the active pixels (vidout_de). vidout_rgb carries the APF
    // scaler-slot word on the RGB bus during blanking; zeroing it there
    // destroys the slot advertisement, the Pocket scaler never locks, and the
    // LCD goes black with host-side screenshots hanging -- and since the
    // Pocket LCD choice persists across loads, one persisted "Off" bricks the
    // LCD on every boot regardless of later menu changes. Gating on vidout_de
    // keeps the scaler locked; Off just blacks the visible picture.
    assign video_rgb_core = (pocket_blank_screen && vidout_de) ? 24'h000000
                                                               : vidout_rgb;

    // Controller inputs: SNAC mux removed — firmware handles SNAC→Pocket mapping.
    // APF Pocket controllers pass through directly to the CPU.
    // When SNAC is active, firmware reads the SNAC shifter and writes
    // parsed button data into its own input state (of_input_states[]).
    wire [31:0] p1_controls = cont1_key;
    wire [31:0] p1_joypad   = cont1_joy;
    wire [15:0] p1_trigger  = cont1_trig;
    wire [31:0] p2_controls = cont2_key;
    wire [31:0] p2_joypad   = cont2_joy;
    wire [15:0] p2_trigger  = cont2_trig;

    reg [2:0] fx;
    always @(posedge clk_core_49152) begin
        case (analogizer_video_type_core)
            4'd5, 4'd13:    begin fx <= 3'd0; end //SC  0%     1 SC 25%
            4'd6, 4'd14:    begin fx <= 3'd2; end //SC  50%    3 SC 75%
            4'd7, 4'd15:    begin fx <= 3'd4; end //hq2x
            default:        begin fx <= 3'd0; end
        endcase
    end


// Video Y/C Encoder settings
// Follows the Mike Simone Y/C encoder settings:
// https://github.com/MikeS11/MiSTerFPGA_YC_Encoder
// SET PAL and NTSC TIMING and pass through status bits. ** YC must be enabled in the qsf file **
wire [39:0] CHROMA_PHASE_INC;
wire [26:0] COLORBURST_RANGE;
wire [4:0] CHROMA_ADD;
wire [4:0] CHROMA_MULT;
wire PALFLAG;

parameter NTSC_REF = 3.579545;   
parameter PAL_REF = 4.43361875;

// Parameters to be modifed
parameter CLK_VIDEO_NTSC = 49.152;
parameter CLK_VIDEO_PAL  = 49.152;

localparam [39:0] NTSC_PHASE_INC = 40'd80073066196;  // round(3.579545 * 2**40 / 49.152)
localparam [39:0] PAL_PHASE_INC =  40'd99178372574;  // round(4.43361875 * 2**40 / 49.152)

assign CHROMA_PHASE_INC = ((analogizer_video_type_core == 4'h4)|| (analogizer_video_type_core == 4'hC)) ? PAL_PHASE_INC : NTSC_PHASE_INC;
assign PALFLAG = (analogizer_video_type_core == 4'h4) || (analogizer_video_type_core == 4'hC);


// Fixed 12.288 MHz pixel enable for the 49.152 MHz Analogizer helper clock.
// cen[0] is the pixel-rate pulse; cen[1] is half-rate.
wire analog_ce_pix, analog_ce_half;

`ifndef INCLUDE_ANALOGIZER
// Both consumers are gone (the analogizer instance and the scanout's
// analog read slots, pruned by HAS_ANALOG_RASTER(0)), so the divider goes
// too.  Constant 0 = "pixel enable never fires", which the pruned scanout
// ignores entirely.
assign {analog_ce_half, analog_ce_pix} = 2'b00;
`else
frac_cen #(.W(2)) pixel_cen
(
    .clk(clk_core_49152),
    .n(10'd1),
    .m(10'd4),
    .cen({analog_ce_half, analog_ce_pix})
);
`endif

// The generated source is progressive on this non-interlaced timing.  A
// field-alternating resync would inject every-other-frame offsets, so feed
// the deterministic raw syncs directly.
wire HSync = crt_hs;
wire VSync = crt_vs;

wire crt_csync;
wire crt_blankn;

assign crt_csync = ~(HSync ^ VSync);
assign crt_blankn   = ~(crt_hblank | crt_vblank);

wire [23:0] analogizer_scan_rgb;
wire        analogizer_scan_hblank;
wire        analogizer_scan_vblank;
wire        analogizer_scan_hsync;
wire        analogizer_scan_vsync;
wire        analogizer_scan_blankn = ~(analogizer_scan_hblank | analogizer_scan_vblank);
wire        analogizer_scan_csync = ~(analogizer_scan_hsync ^ analogizer_scan_vsync);

wire        analogizer_cart_clk = analogizer_15khz_core ? clk_core_12288 : clk_vid;
// Source the Analogizer from the scanout's dedicated analog fetch (its OWN
// framebuffer) in 15 kHz modes (always) and in Pocket LCD = Terminal (so the
// app stays on the analog DAC while the LCD shows the OS console). Normal RGBHV
// keeps using the LCD video stream (vidout_rgb + crt_* sync), unchanged. The
// 480p analog raster is bit-identical to the LCD raster (H_TOTAL 780, H sync 58,
// active 120..760, V active base 18, 525 lines), so the analog DAC sees the same
// timing it does today -- only the pixels come from the app fetch.
wire        pocket_lcd_terminal_core = (analogizer_settings_core[17:16] == 2'b10);
wire        use_analog_dedicated = analogizer_15khz_core || pocket_lcd_terminal_core;
// RGB: switch to the dedicated analog fetch (its own framebuffer = the app) for
// 15 kHz and for Pocket LCD = Terminal.  SYNC/blank: keep the ORIGINAL gating --
// 15 kHz uses the scan raster's 240p sync, but RGBHV (normal AND Terminal) keeps
// the LCD-raster sync (crt_*/HSync/VSync, clk_vid).  The 480p analog raster is
// bit-identical to the LCD raster, so the app RGB lands in the same active
// region; using the proven clk_vid sync avoids a clk_analog->clk_vid sync CDC
// that an external VGA monitor will not lock to.
wire [23:0] analogizer_src_rgb =
    use_analog_dedicated ? analogizer_scan_rgb : vidout_rgb;
wire        analogizer_src_hblank =
    analogizer_15khz_core ? analogizer_scan_hblank : crt_hblank;
wire        analogizer_src_vblank =
    analogizer_15khz_core ? analogizer_scan_vblank : crt_vblank;
wire        analogizer_src_blankn =
    analogizer_15khz_core ? analogizer_scan_blankn : crt_blankn;
wire        analogizer_src_hsync =
    analogizer_15khz_core ? analogizer_scan_hsync : HSync;
wire        analogizer_src_vsync =
    analogizer_15khz_core ? analogizer_scan_vsync : VSync;
wire        analogizer_src_csync =
    analogizer_15khz_core ? analogizer_scan_csync : crt_csync;

// ============================================================
// SNAC GPIO / UART cart pin mux
// CPU-controlled via snac_enable from axi_periph_slave.
// When snac_enable=1: cart pins driven by CPU SNAC shifter/GPIO
// When snac_enable=0: bank0=UART TX, pin31=UART RX (high-Z input)
// ============================================================
wire [7:0] snac_pin_out;
wire [7:0] snac_pin_dir;
wire [7:0] snac_pin_in;
wire       snac_enable;
wire       snac_configured_cpu = (analogizer_settings_cpu[4:0] != 5'd0);
wire       adapter_fixed_rate_cpu = analogizer_ena_cpu || snac_configured_cpu;

// Map SNAC GPIO bits to physical cart pin names:
//   [0]=OUT1/bank1[6], [1]=OUT2/bank1[7],
//   [2]=IO3/bank0[4],  [3]=IN7/bank0[5],
//   [4]=bank0[6],      [5]=IN4/bank0[7],
//   [6]=IO5/pin30,     [7]=IO6/pin31

// SNAC pin readback: cart pin inputs fed back to periph_slave
assign snac_pin_in = {
    cart_tran_pin31,        // [7] IO6/pin31
    cart_tran_pin30,        // [6] IO5/pin30
    cart_tran_bank0[7],     // [5] IN4
    cart_tran_bank0[6],     // [4]
    cart_tran_bank0[5],     // [3] IN7
    cart_tran_bank0[4],     // [2] IO3
    1'b0,                   // [1] OUT2 readback (output-only, no read)
    1'b0                    // [0] OUT1 readback (output-only, no read)
};

// Cart pin assignments: SNAC / Analogizer GPIO vs UART mode.
// The adapter pins must leave UART mode as soon as Analogizer video or a
// SNAC type is configured. Firmware enables SNAC later, after it has written
// initial GPIO values, so using only snac_enable leaves a boot-time UART
// window on the physical adapter pins.
wire analogizer_snac_configured_core = (analogizer_settings_core[4:0] != 5'd0);
wire analogizer_cart_preclaim = analogizer_ena_core || analogizer_snac_configured_core;
wire cart_gpio_mode = snac_enable || analogizer_cart_preclaim;

// bank0[7:4]: SNAC GPIO outputs, high-Z while the adapter is preclaimed, or
// UART TX replicated when the cart port is in console mode.
wire snac_bank0_drive = snac_enable &&
    (snac_pin_dir[5] | snac_pin_dir[4] | snac_pin_dir[3] | snac_pin_dir[2]);
assign cart_tran_bank0     = snac_enable ?
    (snac_bank0_drive ? {snac_pin_out[5], snac_pin_out[4], snac_pin_out[3], snac_pin_out[2]} : 4'bZ) :
    (analogizer_cart_preclaim ? 4'bZ :
     {uart_tx_serial, uart_tx_serial, uart_tx_serial, uart_tx_serial});
assign cart_tran_bank0_dir = snac_enable ? snac_bank0_drive :
    (analogizer_cart_preclaim ? 1'b0 : 1'b1);

// pin31: SNAC GPIO or UART RX (high-Z input)
assign cart_tran_pin31     = snac_enable ?
    (snac_pin_dir[7] ? snac_pin_out[7] : 1'bZ) :
    1'bZ;
assign cart_tran_pin31_dir = snac_enable ? snac_pin_dir[7] : 1'b0;

// pin30: always SNAC data when active. The pwroff/reset sideband is also
// asserted for Analogizer video/SNAC preclaim so the cart port is in GPIO use.
assign cart_tran_pin30     = (snac_enable && snac_pin_dir[6]) ? snac_pin_out[6] : 1'bZ;
assign cart_tran_pin30_dir = snac_enable ? snac_pin_dir[6] : 1'b0;
assign cart_pin30_pwroff_reset = cart_gpio_mode ? 1'b1 : 1'b0;

// UART RX gating: when the cart port is owned by Analogizer/SNAC, feed
// idle-high to UART RX to prevent adapter traffic from becoming console input.
wire uart_rx_serial = cart_gpio_mode ? 1'b1 : cart_tran_pin31;

`ifndef INCLUDE_ANALOGIZER
// Targets without analogizer (Pocket OS30): prune the analog-video encoder +
// SNAC GPIO entirely.  The cartridge video pins (bank1-3) are tied to high-Z
// with their direction bits forced to input (0); the SNAC pins on
// bank0/pin30/pin31 already fall back to the snac_enable=0 UART wiring above
// (snac_enable is driven 0 by the periph's no-analogizer branch).  No
// undriven/multidriven nets result.  INCLUDE_ANALOGIZER (OS25) instantiates
// the full openFPGA_Pocket_Analogizer below.
assign cart_tran_bank1     = 8'hZZ;
assign cart_tran_bank1_dir = 1'b0;
assign cart_tran_bank2     = 8'hZZ;
assign cart_tran_bank2_dir = 1'b0;
assign cart_tran_bank3     = 8'hZZ;
assign cart_tran_bank3_dir = 1'b0;
`else
// Analogizer: video output only — SNAC pins now driven by CPU through above mux
openFPGA_Pocket_Analogizer #(
    .MASTER_CLK_FREQ(49_152_000),
    // Full encoder set: YPbPr (component) and Y/C (S-Video NTSC + PAL-60)
    // re-enabled 2026-06-12 — the historic cut (~700-900 ALM estimate) was
    // made against a >94% ALM design; measuring against today's ~93% OS25.
    // The Y/C support inputs the module actually takes (CHROMA_PHASE_INC,
    // PALFLAG) are driven above with 49.152 MHz-domain NTSC/PAL constants.
    // If ALM/WNS regress past the OS25 reference (-0.526 on mp_ram
    // general[0]), revert to (0,0) or fund with a donor cut.
    .EN_YPBPR(1),
    .EN_YC(1)
) analogizer (
    .i_clk(clk_core_49152),
    .i_rst(~reset_n_analog),
    .i_ena(analogizer_ena_core),
    // Native 15 kHz modes use the scanout's analog raster and a 12.288 MHz DAC
    // clock. RGBHV modes keep the proven 640x480 carrier and 24.576 MHz DAC
    // clock, so this restores the extra encoders without disturbing VGA output.
    .video_clk(analogizer_cart_clk),
    .analog_video_type(analogizer_video_type_core),
    .R(analogizer_src_rgb[23:16]),
    .G(analogizer_src_rgb[15:8]),
    .B(analogizer_src_rgb[7:0]),
    .Hblank(analogizer_src_hblank),
    .Vblank(analogizer_src_vblank),
    .BLANKn(analogizer_src_blankn),
    .Hsync(analogizer_src_hsync),
    .Vsync(analogizer_src_vsync),
    .Csync(analogizer_src_csync),
    .ce_pix(analog_ce_pix),
    .scandoubler(1'b0),
    .fx(fx),
    .CHROMA_PHASE_INC(CHROMA_PHASE_INC),
    .PALFLAG(PALFLAG),
    // SNAC cart pin pass-through from CPU GPIO
    .snac_bank0_out({snac_pin_out[5], snac_pin_out[4], snac_pin_out[3], snac_pin_out[2]}),
    .snac_bank0_dir(snac_pin_dir[5] | snac_pin_dir[4] | snac_pin_dir[3] | snac_pin_dir[2]),
    .snac_bank1_76_out({snac_pin_out[1], snac_pin_out[0]}),
    .snac_enable(snac_enable),
    .snac_pin30_out(snac_pin_out[6]),
    .snac_pin30_dir(snac_pin_dir[6]),
    .snac_pin31_out(snac_pin_out[7]),
    .snac_pin31_dir(snac_pin_dir[7]),
    // Cartridge port (video output on bank1-3, SNAC on bank0/pin30/pin31)
    .cart_tran_bank2(cart_tran_bank2),
    .cart_tran_bank2_dir(cart_tran_bank2_dir),
    .cart_tran_bank3(cart_tran_bank3),
    .cart_tran_bank3_dir(cart_tran_bank3_dir),
    .cart_tran_bank1(cart_tran_bank1),
    .cart_tran_bank1_dir(cart_tran_bank1_dir)
);
`endif // INCLUDE_ANALOGIZER

// Link port directions
assign port_tran_si = 1'bz;
assign port_tran_si_dir = 1'b0;
assign port_tran_so = link_so_oe ? link_so_out : 1'bz;
assign port_tran_so_dir = link_so_oe;
assign port_tran_sck = link_sck_oe ? link_sck_out : 1'bz;
assign port_tran_sck_dir = link_sck_oe;
assign port_tran_sd = link_sd_oe ? link_sd_out : 1'bz;
assign port_tran_sd_dir = link_sd_oe;
assign link_si_i = port_tran_si;
assign link_sck_i = port_tran_sck;
assign link_sd_i = port_tran_sd;

// ============================================================
// CRAM0 BCR sanity reset (runs in clk_74a)
// ============================================================
// Both CRAM0 dies (CE0 and CE1) get the AS1C8M16PL BCR written via
// CRE on every boot, guarding against a previous bitstream having
// left the chip in a different mode (async page vs sync burst).
//
// v2: FSM moves to clk_74a along with the cram0_controller.  The
// CPU reset is still gated by bcr_init_done, synchronised into
// clk_cpu via the existing synch_3 chain.
//
// Dual-die safety: never assert a second config_en pulse until raw_busy
// has risen and fallen again.  The PHY never asserts both CE# in the
// same state (cram0_phy.sv STATE_CONFIG_CRE_SETUP), so die 0 → die 1
// handoff cannot create bus contention.
// ============================================================
wire        cpu_psram_raw_busy;
reg [3:0]   bcr_init_state;
reg         bcr_init_config_en;
reg         bcr_init_bank_sel;
(* keep, syn_keep, maxfan = 160 *) reg bcr_init_done;
reg [8:0]   bcr_settle_cnt;

localparam [3:0] BCR_ST_WAIT_PLL    = 4'd0;
localparam [3:0] BCR_ST_PULSE_DIE0  = 4'd1;
localparam [3:0] BCR_ST_BUSY_DIE0   = 4'd2;
localparam [3:0] BCR_ST_IDLE_DIE0   = 4'd3;
localparam [3:0] BCR_ST_PULSE_DIE1  = 4'd4;
localparam [3:0] BCR_ST_BUSY_DIE1   = 4'd5;
localparam [3:0] BCR_ST_IDLE_DIE1   = 4'd6;
localparam [3:0] BCR_ST_SETTLE      = 4'd7;  // Post-config settle before release
localparam [3:0] BCR_ST_DONE        = 4'd8;

// Settle counter: hold CPU in reset for a few us after chip-side BCR
// write completes. 511 cycles @ 74.25 MHz is about 6.9 us.
localparam [8:0] BCR_SETTLE_CYCLES = 9'd511;

// AS1C8M16PL BCR: async page mode at clk_74a.
//
// The firmware/bridge CPU alias uses the async single-word path. Hardware
// testing showed the 0x30000000 CPU alias returning the CRAM fill pattern
// while the chip was configured for sync-burst mode, even though the BCR
// write itself completed. Keep the part in async page mode until the sync
// burst path owns all CRAM0 accesses that happen after BCR init.
//
// BCR encoding:
//   bit 15    = 1   async page mode
//   bit 14    = 0   fixed initial latency
//   bits 13-11= 011 latency code
//   bit 10    = 1   WAIT active high
//   bit 9     = 0
//   bit 8     = 1   WAIT asserted one data cycle before delay
//   bit 7     = 0
//   bit 6     = 0
//   bits 5-4  = 01  drive strength 1/2
//   bit 3     = 1   no-wrap burst
//   bits 2-0  = 111 continuous burst
localparam [15:0] BCR_VALUE = 16'h9D1F;  // async page mode

initial begin
    bcr_init_state     = BCR_ST_WAIT_PLL;
    bcr_init_config_en = 1'b0;
    bcr_init_bank_sel  = 1'b0;
    bcr_init_done      = 1'b0;
    bcr_settle_cnt     = 9'd0;
end

// PLL-locked sync into clk_74a (the new cram0 clock domain).
reg [2:0] pll_ram_locked_74a_sync;
always @(posedge clk_74a)
    pll_ram_locked_74a_sync <= {pll_ram_locked_74a_sync[1:0], pll_ram_locked};
wire pll_ram_locked_74a = pll_ram_locked_74a_sync[2];

always @(posedge clk_74a) begin
    bcr_init_config_en <= 1'b0;  // single-cycle pulse default
    case (bcr_init_state)
        BCR_ST_WAIT_PLL:
            if (pll_ram_locked_74a)
                bcr_init_state <= BCR_ST_PULSE_DIE0;

        BCR_ST_PULSE_DIE0: begin
            bcr_init_bank_sel  <= 1'b0;
            bcr_init_config_en <= 1'b1;
            bcr_init_state     <= BCR_ST_BUSY_DIE0;
        end
        BCR_ST_BUSY_DIE0:
            // Wait for the wrapper's raw_busy to rise — confirms the
            // driver picked up config_en and is sequencing CRE/WE#.
            if (cpu_psram_raw_busy)
                bcr_init_state <= BCR_ST_IDLE_DIE0;
        BCR_ST_IDLE_DIE0:
            // Wait for raw_busy to fall — driver finished the chip-side
            // CRE/WE# sequence. Now safe to issue the next config.
            if (!cpu_psram_raw_busy)
                bcr_init_state <= BCR_ST_PULSE_DIE1;

        BCR_ST_PULSE_DIE1: begin
            bcr_init_bank_sel  <= 1'b1;
            bcr_init_config_en <= 1'b1;
            bcr_init_state     <= BCR_ST_BUSY_DIE1;
        end
        BCR_ST_BUSY_DIE1:
            if (cpu_psram_raw_busy)
                bcr_init_state <= BCR_ST_IDLE_DIE1;
        BCR_ST_IDLE_DIE1:
            if (!cpu_psram_raw_busy) begin
                bcr_settle_cnt <= BCR_SETTLE_CYCLES;
                bcr_init_state <= BCR_ST_SETTLE;
            end

        BCR_ST_SETTLE: begin
            // Count down settle cycles before releasing bcr_init_done.
            // This holds CPU reset briefly after the chip-side BCR write
            // completes, giving the chip time to internalise the mode
            // change before firmware touches CRAM0.
            if (bcr_settle_cnt == 9'd0) begin
                bcr_init_done  <= 1'b1;
                bcr_init_state <= BCR_ST_DONE;
            end else begin
                bcr_settle_cnt <= bcr_settle_cnt - 9'd1;
            end
        end

        BCR_ST_DONE:
            ; // sticky
    endcase
end

// ============================================================
// CRAM0 ownership mux + controller (clk_74a domain)
// ============================================================
// Two clients want to drive the cram0 word-interface:
//   1. The APF bridge, via the "bridge-native" word-rd/wr signals
//      (bridge_addr + bridge_wr_data land in clk_74a).
//   2. The CPU, via cram0_cdc which converts a clk_cpu AXI slave
//      into the same word-interface in clk_74a.
// Time-sliced by the axi_periph_slave's cram0_mode register (0 =
// bridge, 1 = CPU).  Firmware is responsible for ensuring the
// inactive side has no in-flight op when flipping the bit — see
// docs/memory-arch-v2.md §4 for the protocol.
// ============================================================

// cram0_mode from axi_periph_slave (clk_cpu domain) synced to clk_74a.
wire       cram0_mode_cpu;          // from axi_periph_slave
reg [2:0]  cram0_mode_74a_sync;
always @(posedge clk_74a)
    cram0_mode_74a_sync <= {cram0_mode_74a_sync[1:0], cram0_mode_cpu};
wire cram0_mode_74a = cram0_mode_74a_sync[2];

// CPU-side word-interface (clk_74a, driven by cram0_cdc)
wire        cpu_cram0_word_rd;
wire        cpu_cram0_word_wr;
wire [21:0] cpu_cram0_word_addr;
wire [31:0] cpu_cram0_word_wdata;
wire [3:0]  cpu_cram0_word_wstrb;
wire [31:0] cpu_cram0_word_rdata;
wire        cpu_cram0_word_busy;
wire        cpu_cram0_word_rdata_valid;

// Bridge-native word-interface (clk_74a).  Bridge writes/reads whose
// top byte == 0x20 land here; the bridge_addr/bridge_wr_data/bridge_rd
// pins are already clk_74a — no CDC needed.  We just repackage them
// into a word-rd/wr pulse with an edge detector for bridge_rd.
//
// Address encoding: bridge_addr is a byte address into the CRAM0 window.
// cram0_controller expects a 22-bit word address = byte_addr[23:2].
// APF bridge targets CRAM0 at 0x20000000 in its own address space
// (OF_TARGET_CRAM0_BRIDGE).  The CPU-side alias 0x30000000 is a
// different thing — that's the CPU's MMU view, which lands on
// cram0_cdc.  Don't confuse the two.
wire bridge_cram0_range = (bridge_addr[31:24] == 8'h20);
reg  bridge_rd_prev_74a;
always @(posedge clk_74a) begin
    bridge_rd_prev_74a <= bridge_rd && bridge_cram0_range;
end
wire bridge_cram0_rd_pulse = bridge_cram0_range && bridge_rd && !bridge_rd_prev_74a;
wire bridge_cram0_wr_pulse = bridge_cram0_range && bridge_wr;

// APF bridge writes land in clk_74a already, but the bridge side has no
// ready/backpressure signal and CRAM0 has turnaround latency.  Queue one
// safe 4 KB data-slot burst while firmware waits for WR_IDLE before flipping
// CRAM0 to CPU ownership.
wire        bridge_wr_fifo_empty;
wire        bridge_wr_fifo_full;
wire [10:0] bridge_wr_fifo_count;
wire [53:0] bridge_wr_fifo_dout;
reg         bridge_wr_inflight = 1'b0;
reg         bridge_wr_seen_busy = 1'b0;
reg         bridge_wr_overrun = 1'b0;

wire        bridge_wr_busy = (bridge_wr_fifo_count != 11'd0) | bridge_wr_inflight;
wire        bridge_wr_issue = !cram0_mode_74a &&
                              !bridge_wr_fifo_empty &&
                              !bridge_wr_inflight &&
                              !ctrl_word_busy;
wire        bridge_wr_accept = !cram0_mode_74a &&
                               bridge_cram0_wr_pulse &&
                               (!bridge_wr_fifo_full || bridge_wr_issue);
wire        bridge_wr_overrun_pulse = !cram0_mode_74a &&
                                      bridge_cram0_wr_pulse &&
                                      bridge_wr_fifo_full &&
                                      !bridge_wr_issue;
wire [21:0] bridge_wr_fifo_addr = bridge_wr_fifo_dout[53:32];
wire [31:0] bridge_wr_fifo_data = bridge_wr_fifo_dout[31:0];

bridge_cram0_write_fifo #(
    .WIDTH(54),
    .DEPTH(1024),
    .ADDR_WIDTH(10)
) bridge_cram0_write_fifo_inst (
    .clk(clk_74a),
    .reset(!pll_ram_locked_74a),
    .push(bridge_wr_accept),
    .din({bridge_addr[23:2], bridge_wr_data}),
    .pop(bridge_wr_issue),
    .dout(bridge_wr_fifo_dout),
    .empty(bridge_wr_fifo_empty),
    .full(bridge_wr_fifo_full),
    .count(bridge_wr_fifo_count)
);

always @(posedge clk_74a or negedge pll_ram_locked_74a) begin
    if (!pll_ram_locked_74a) begin
        bridge_wr_inflight <= 1'b0;
        bridge_wr_seen_busy <= 1'b0;
        bridge_wr_overrun <= 1'b0;
    end
    else begin
        if (bridge_wr_overrun_pulse)
            bridge_wr_overrun <= 1'b1;

        if (bridge_wr_issue) begin
            bridge_wr_inflight <= 1'b1;
            bridge_wr_seen_busy <= 1'b0;
        end else if (bridge_wr_inflight) begin
            if (ctrl_word_busy)
                bridge_wr_seen_busy <= 1'b1;
            else if (bridge_wr_seen_busy)
                bridge_wr_inflight <= 1'b0;
        end
    end
end

wire        bridge_cram0_direct_rd_pulse = bridge_cram0_rd_pulse &&
                                           !bridge_wr_busy;

// Mux word-interface inputs to the controller: pick the owning side.
wire [31:0] ctrl_word_rdata;
wire        ctrl_word_busy;
wire        ctrl_word_rdata_valid;

// Mux word-interface inputs to the controller: pick the owning side.
wire        mux_word_rd    = cram0_mode_74a ? cpu_cram0_word_rd    : bridge_cram0_direct_rd_pulse;
wire        mux_word_wr    = cram0_mode_74a ? cpu_cram0_word_wr    : bridge_wr_issue;
wire [21:0] mux_word_addr  = cram0_mode_74a       ? cpu_cram0_word_addr
                            : bridge_wr_issue       ? bridge_wr_fifo_addr
                                                    : bridge_addr[23:2];
wire [31:0] mux_word_wdata = cram0_mode_74a ? cpu_cram0_word_wdata : bridge_wr_fifo_data;
wire [3:0]  mux_word_wstrb = cram0_mode_74a ? cpu_cram0_word_wstrb : 4'b1111;

// Controller response fans out to whichever owner asked for it.
assign cpu_cram0_word_rdata       = ctrl_word_rdata;
assign cpu_cram0_word_busy        = ctrl_word_busy;
assign cpu_cram0_word_rdata_valid = ctrl_word_rdata_valid;

// Bridge side of the word interface also reads the controller's rdata
// (used for bridge 0x20xxxxxx read-backs below).
wire [31:0] bridge_cram0_rdata      = ctrl_word_rdata;
wire        bridge_cram0_rdata_valid= ctrl_word_rdata_valid;

// ============================================================
// CRAM1 — dedicated GPU sync-burst texture memory (Phase 2).
//
// A separate physical chip from CRAM0 (saves), so textures NEVER touch the
// latency-sensitive bridge/save path.  Runs on clk_cpu in BCR 0x641F sync-burst
// mode (the mode that hangs CRAM0's shared async reads, but is exactly right for
// a read-mostly dedicated texture chip).  Two masters into the one controller:
//   - GPU texture fills  -> burst_rd via gpu_cram1_tex_adapter (priority).
//   - CPU texture upload -> word_wr from gpu_core's MMIO upload regs.
// No CDC: GPU, the CPU-upload regs and the controller are all clk_cpu.
// ============================================================

// GPU texture-cache AXI fill master (gpu_core CRAM0-tex port) -> burst adapter.
wire        gpu_tex_arvalid;
wire        gpu_tex_arready;
wire [31:0] gpu_tex_araddr;
wire [7:0]  gpu_tex_arlen;
wire        gpu_tex_rvalid;
wire [31:0] gpu_tex_rdata;
wire        gpu_tex_rlast;

// CPU texture upload (gpu_core MMIO regs) -> controller word_wr.  Declared
// for BOTH variants — gpu_core always wires these; on OS25 (CRAM1 gated out)
// cram1_up_busy is tied idle in the `else branch below.  These connect to the
// gpu_core's generic gpu_tex_mem_up_* upload port (CRAM1 is Pocket's
// fast-texture chip — the mapping lives entirely in this Pocket-only block).
wire        cram1_up_wr;
wire [21:0] cram1_up_addr;
wire [31:0] cram1_up_wdata;
wire        cram1_up_busy;

`ifdef INCLUDE_TEX_MEM
// CRAM1 controller burst-read interface (adapter <-> controller)
wire        c1_burst_rd;
wire [21:0] c1_burst_addr;
wire [4:0]  c1_burst_len;
wire [31:0] c1_burst_q;
wire        c1_burst_q_valid;
wire        c1_burst_busy;

// CRAM1 controller physical-pin nets (active-low; DQ tristated at top below)
wire [21:16] c1a_a;
wire [15:0]  c1a_dq_out;
wire         c1a_dq_oe;
wire         c1a_adv_n, c1a_cre, c1a_ce0_n, c1a_ce1_n, c1a_oe_n, c1a_we_n, c1a_ub_n, c1a_lb_n;

// CRAM1 BCR-init FSM control
reg          cram1_bcr_config_en;
reg          cram1_bcr_bank_sel;
wire         cram1_a_raw_busy;
wire         cram1_a_bcr_done;

// pll_ram_locked synced into clk_cpu — reset for the CRAM1 controller + BCR FSM
reg [2:0]    pll_ram_locked_c1_sync;
always @(posedge clk_cpu)
    pll_ram_locked_c1_sync <= {pll_ram_locked_c1_sync[1:0], pll_ram_locked};
wire         cram1_reset_n = pll_ram_locked_c1_sync[2];

cram1_controller #(.CLOCK_SPEED(100.0)) psram1 (
    .clk(clk_cpu), .reset_n(cram1_reset_n),
    // word interface — CPU texture upload (word_wr); word_rd unused
    .word_rd(1'b0),
    .word_wr(cram1_up_wr),
    .word_addr(cram1_up_addr), .word_data(cram1_up_wdata), .word_wstrb(4'b1111),
    .word_q(), .word_busy(cram1_up_busy), .word_q_valid(),
    // sync-burst read — GPU texture fills
    .burst_rd(c1_burst_rd), .burst_addr(c1_burst_addr), .burst_len(c1_burst_len),
    .burst_q(c1_burst_q), .burst_q_valid(c1_burst_q_valid), .burst_busy(c1_burst_busy),
    // BCR config (sync burst 0x641F)
    .config_en(cram1_bcr_config_en), .config_data(16'h641F),
    .config_bank_sel(cram1_bcr_bank_sel),
    .raw_busy(cram1_a_raw_busy), .bcr_init_done(cram1_a_bcr_done),
    // physical pins (cram_clk unused — chip clock is the pin driven below)
    .cram_a(c1a_a), .cram_dq_out(c1a_dq_out), .cram_dq_oe(c1a_dq_oe),
    .cram_dq_in(cram1_dq), .cram_wait(cram1_wait), .cram_clk(),
    .cram_adv_n(c1a_adv_n), .cram_cre(c1a_cre),
    .cram_ce0_n(c1a_ce0_n), .cram_ce1_n(c1a_ce1_n),
    .cram_oe_n(c1a_oe_n), .cram_we_n(c1a_we_n),
    .cram_ub_n(c1a_ub_n), .cram_lb_n(c1a_lb_n)
);

gpu_cram1_tex_adapter cram1_tex_adapter (
    .clk(clk_cpu), .reset_n(cram1_reset_n),
    .arvalid(gpu_tex_arvalid), .arready(gpu_tex_arready),
    .araddr(gpu_tex_araddr), .arlen(gpu_tex_arlen),
    .rvalid(gpu_tex_rvalid), .rdata(gpu_tex_rdata), .rlast(gpu_tex_rlast),
    .burst_rd(c1_burst_rd), .burst_addr(c1_burst_addr), .burst_len(c1_burst_len),
    .burst_q(c1_burst_q), .burst_q_valid(c1_burst_q_valid), .burst_busy(c1_burst_busy)
);

// CRAM1 BCR-init FSM: program sync-burst mode (0x641F) into both dies at boot.
// Pulses config_en per die, edge-detecting raw_busy for the inter-die handoff.
reg [3:0] cram1_bcr_state;
localparam [3:0] C1_BCR_WAIT_PLL=4'd0, C1_BCR_PULSE_DIE0=4'd1, C1_BCR_BUSY_DIE0=4'd2,
                 C1_BCR_IDLE_DIE0=4'd3, C1_BCR_PULSE_DIE1=4'd4, C1_BCR_BUSY_DIE1=4'd5,
                 C1_BCR_IDLE_DIE1=4'd6, C1_BCR_DONE=4'd7;
initial begin
    cram1_bcr_state = C1_BCR_WAIT_PLL; cram1_bcr_config_en = 1'b0; cram1_bcr_bank_sel = 1'b0;
end
always @(posedge clk_cpu) begin
    cram1_bcr_config_en <= 1'b0;   // single-cycle pulse default
    case (cram1_bcr_state)
        C1_BCR_WAIT_PLL:   if (cram1_reset_n) cram1_bcr_state <= C1_BCR_PULSE_DIE0;
        C1_BCR_PULSE_DIE0: begin cram1_bcr_bank_sel<=1'b0; cram1_bcr_config_en<=1'b1; cram1_bcr_state<=C1_BCR_BUSY_DIE0; end
        C1_BCR_BUSY_DIE0:  if (cram1_a_raw_busy)  cram1_bcr_state <= C1_BCR_IDLE_DIE0;
        C1_BCR_IDLE_DIE0:  if (!cram1_a_raw_busy) cram1_bcr_state <= C1_BCR_PULSE_DIE1;
        C1_BCR_PULSE_DIE1: begin cram1_bcr_bank_sel<=1'b1; cram1_bcr_config_en<=1'b1; cram1_bcr_state<=C1_BCR_BUSY_DIE1; end
        C1_BCR_BUSY_DIE1:  if (cram1_a_raw_busy)  cram1_bcr_state <= C1_BCR_IDLE_DIE1;
        C1_BCR_IDLE_DIE1:  if (!cram1_a_raw_busy) cram1_bcr_state <= C1_BCR_DONE;
        C1_BCR_DONE: ;  // sticky
    endcase
end

// CRAM1 pin fan-out (DQ tristated at top level; chip clock = clk_cpu).
assign cram1_a     = c1a_a;
assign cram1_dq    = c1a_dq_oe ? c1a_dq_out : 16'hZZZZ;
assign cram1_clk   = clk_cpu;
assign cram1_adv_n = c1a_adv_n;
assign cram1_cre   = c1a_cre;
assign cram1_ce0_n = c1a_ce0_n;
assign cram1_ce1_n = c1a_ce1_n;
assign cram1_oe_n  = c1a_oe_n;
assign cram1_we_n  = c1a_we_n;
assign cram1_ub_n  = c1a_ub_n;
assign cram1_lb_n  = c1a_lb_n;
`else
// ── OS25: CRAM1 disabled — controller/phy/adapter/BCR-FSM gated out above ──
// The gpu_core CRAM1 fill + upload ports stay idle (firmware never asserts
// the route-enable because caps->tex_fast_size is 0 here), and the CRAM1
// chip is held deselected/quiescent so the unused part is inert.
assign gpu_tex_arready = 1'b0;
assign gpu_tex_rvalid  = 1'b0;
assign gpu_tex_rdata   = 32'd0;
assign gpu_tex_rlast   = 1'b0;
assign cram1_up_busy   = 1'b0;
assign cram1_a     = 6'd0;
assign cram1_dq    = 16'hZZZZ;
assign cram1_clk   = 1'b0;
assign cram1_adv_n = 1'b1;
assign cram1_cre   = 1'b0;
assign cram1_ce0_n = 1'b1;
assign cram1_ce1_n = 1'b1;
assign cram1_oe_n  = 1'b1;
assign cram1_we_n  = 1'b1;
assign cram1_ub_n  = 1'b1;
assign cram1_lb_n  = 1'b1;
`endif // INCLUDE_TEX_MEM

// ============================================================
// PSRAM Controller for CRAM0 (16 MB, bridge scratch — clk_74a)
// ============================================================
cram0_controller #(
    .CLOCK_SPEED(74.25)
) psram0 (
    .clk(clk_74a),
    .reset_n(pll_ram_locked_74a),
    .word_rd(mux_word_rd),
    .word_wr(mux_word_wr),
    .word_addr(mux_word_addr),
    .word_data(mux_word_wdata),
    .word_wstrb(mux_word_wstrb),
    .word_q(ctrl_word_rdata),
    .word_busy(ctrl_word_busy),
    .word_q_valid(ctrl_word_rdata_valid),
    .cram_a(cram0_a),
    .cram_dq(cram0_dq),
    .cram_wait(cram0_wait),
    .cram_clk(),             // unused: cram0_clk pin driven from clk_74a at top level (see assign at end of file)
    .cram_adv_n(cram0_adv_n),
    .cram_cre(cram0_cre),
    .cram_ce0_n(cram0_ce0_n),
    .cram_ce1_n(cram0_ce1_n),
    .cram_oe_n(cram0_oe_n),
    .cram_we_n(cram0_we_n),
    .cram_ub_n(cram0_ub_n),
    .cram_lb_n(cram0_lb_n),
    .burst_rd(1'b0),
    .burst_len(6'd0),
    .burst_rdata_valid(),
    .burst_rdata(),
    .config_en(bcr_init_config_en),
    .config_data(BCR_VALUE),
    .config_bank_sel(bcr_init_bank_sel),
    .raw_busy(cpu_psram_raw_busy)
);

// ============================================================
// CRAM0 CDC - CPU-side AXI slave (clk_cpu) → word-iface (clk_74a)
// ============================================================
cram0_cdc cpu_cram0_axi (
    .clk_cpu        (clk_cpu),
    .reset_n_cpu    (reset_n_cpu_cram),

    .s_axi_arvalid  (cpu_cram0_bus_arvalid),
    .s_axi_arready  (cpu_cram0_bus_arready),
    .s_axi_araddr   (cpu_cram0_bus_araddr),
    .s_axi_arlen    (cpu_cram0_bus_arlen),
    .s_axi_rvalid   (cpu_cram0_bus_rvalid),
    .s_axi_rready   (cpu_cram0_bus_rready),
    .s_axi_rdata    (cpu_cram0_bus_rdata),
    .s_axi_rresp    (cpu_cram0_bus_rresp),
    .s_axi_rlast    (cpu_cram0_bus_rlast),
    .s_axi_awvalid  (cpu_cram0_bus_awvalid),
    .s_axi_awready  (cpu_cram0_bus_awready),
    .s_axi_awaddr   (cpu_cram0_bus_awaddr),
    .s_axi_awlen    (cpu_cram0_bus_awlen),
    .s_axi_wvalid   (cpu_cram0_bus_wvalid),
    .s_axi_wready   (cpu_cram0_bus_wready),
    .s_axi_wdata    (cpu_cram0_bus_wdata),
    .s_axi_wstrb    (cpu_cram0_bus_wstrb),
    .s_axi_wlast    (cpu_cram0_bus_wlast),
    .s_axi_bvalid   (cpu_cram0_bus_bvalid),
    .s_axi_bready   (1'b1),
    .s_axi_bresp    (cpu_cram0_bus_bresp),

    .clk_bridge     (clk_74a),
    .reset_n_bridge (pll_ram_locked_74a),
    .b_word_rd      (cpu_cram0_word_rd),
    .b_word_wr      (cpu_cram0_word_wr),
    .b_word_addr    (cpu_cram0_word_addr),
    .b_word_wdata   (cpu_cram0_word_wdata),
    .b_word_wstrb   (cpu_cram0_word_wstrb),
    .b_word_rdata   (cpu_cram0_word_rdata),
    .b_word_busy    (cpu_cram0_word_busy),
    .b_word_rdata_valid (cpu_cram0_word_rdata_valid)
);

// SRAM controller (256 KB) - tristate handled at top level
wire [16:0] sram_a_w;
wire [15:0] sram_dq_out;
wire [15:0] sram_dq_in;
wire        sram_dq_oe;
wire        sram_oe_n_w, sram_we_n_w, sram_ub_n_w, sram_lb_n_w;

wire        sram_word_rd;
wire        sram_word_wr;
wire        sram_word_rd_half;
wire        sram_word_rd_hi;
wire [21:0] sram_word_addr;
wire [31:0] sram_word_wdata;
wire [3:0]  sram_word_wstrb;
wire [31:0] sram_word_rdata;
wire        sram_word_busy;
wire        sram_word_rdata_valid;

assign sram_dq    = sram_dq_oe ? sram_dq_out : 16'hZZZZ;
assign sram_dq_in = sram_dq;
assign sram_a     = sram_a_w;
assign sram_oe_n  = sram_oe_n_w;
assign sram_we_n  = sram_we_n_w;
assign sram_ub_n  = sram_ub_n_w;
assign sram_lb_n  = sram_lb_n_w;

`ifndef INCLUDE_TRANSLUC
// The GPU translucency LUT is this controller's ONLY client, and without
// INCLUDE_TRANSLUC gpu_core holds its SRAM word port permanently idle — so
// the controller itself is dropped (docs/os30-quake2-cut-plan.md C5).
// The physical pins MUST be parked explicitly: left undriven, Quartus
// grounds them, and sram_we_n=0 would hold the chip in a continuous write.
// All control strobes deasserted (active-low high), DQ bus high-Z.
assign sram_a_w    = 17'd0;
assign sram_dq_out = 16'd0;
assign sram_dq_oe  = 1'b0;   // sram_dq -> 16'hZZZZ at the top-level mux
assign sram_oe_n_w = 1'b1;
assign sram_we_n_w = 1'b1;
assign sram_ub_n_w = 1'b1;
assign sram_lb_n_w = 1'b1;
// GPU-side word interface: never busy, never returns data (the port is
// constant-idle in gpu_core under the same define).
assign sram_word_rdata       = 32'd0;
assign sram_word_busy        = 1'b0;
assign sram_word_rdata_valid = 1'b0;
`else
sram_controller #(
    .WAIT_CYCLES(6)
) sram0 (
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
    .word_q_valid(sram_word_rdata_valid),
    .sram_a(sram_a_w),
    .sram_dq_out(sram_dq_out),
    .sram_dq_in(sram_dq_in),
    .sram_dq_oe(sram_dq_oe),
    .sram_oe_n(sram_oe_n_w),
    .sram_we_n(sram_we_n_w),
    .sram_ub_n(sram_ub_n_w),
    .sram_lb_n(sram_lb_n_w)
);
`endif

// ============================================================
// UART (2 Mbaud, 8N1): DevKey/Cartridge service interface.
// CLKS_PER_BIT = clk_cpu / 2_000_000.  clk_cpu is set in
// mf_pllram_133.v output_clock_frequency0; any change there MUST
// update this divisor, or the host will see a baud-rate mismatch
// and silently drop every byte.
//   100 MHz -> 50  (current)
//    90 MHz -> 45
// ============================================================
wire        uart_tx_serial;
wire        uart_tx_active;
wire        uart_tx_done;
wire        uart_tx_dv;
wire [7:0]  uart_tx_byte;
wire        uart_rx_dv;
wire [7:0]  uart_rx_byte;

uart_tx #(.CLKS_PER_BIT(50)) uart_tx_inst (
    .i_Clock(clk_cpu),
    .i_Tx_DV(uart_tx_dv),
    .i_Tx_Byte(uart_tx_byte),
    .o_Tx_Active(uart_tx_active),
    .o_Tx_Serial(uart_tx_serial),
    .o_Tx_Done(uart_tx_done)
);

uart_rx #(.CLKS_PER_BIT(50)) uart_rx_inst (
    .i_Clock(clk_cpu),
    .i_Rx_Serial(uart_rx_serial),  // Gated: idle-high when SNAC active
    .o_Rx_DV(uart_rx_dv),
    .o_Rx_Byte(uart_rx_byte)
);

// UART TX also appears on dbg_tx (direct 1.8V service pin).
assign dbg_tx = uart_tx_serial;

assign user1 = 1'bZ;
assign aux_scl = 1'bZ;
assign vpll_feed = 1'bZ;

// ============================================================
// SDRAM word interface signals (to io_sdram)
// ============================================================
reg             ram1_word_rd;
reg             ram1_word_wr;
reg     [23:0]  ram1_word_addr;
reg     [31:0]  ram1_word_data;
reg     [3:0]   ram1_word_wstrb;
reg     [31:0]  ram1_word_data_next;
reg     [3:0]   ram1_word_wstrb_next;
reg     [3:0]   ram1_word_burst_len;
reg     [3:0]   ram1_word_burst_wr_len;
wire            ram1_word_wr_data_next;
wire            ram1_word_wr_done;
wire    [31:0]  ram1_word_q;
wire            ram1_word_busy;
wire            ram1_word_q_valid;

// axi_sdram_slave word-level outputs (held signals, need pulse conversion)
wire            sdram_slave_rd;
wire            sdram_slave_wr;
wire    [23:0]  sdram_slave_addr;
wire    [31:0]  sdram_slave_wdata;
wire    [3:0]   sdram_slave_wstrb;
wire    [3:0]   sdram_slave_burst_len;
wire    [3:0]   sdram_slave_burst_wr_len;
// io_sdram word_wr_data_next → axi_sdram_slave sdram_wr_data_next
// Same clock domain (clk_cpu = clk_ram_controller), direct wire
wire            sdram_slave_wr_data_next = ram1_word_wr_data_next;
wire            sdram_slave_wr_done      = ram1_word_wr_done;
wire    [31:0]  sdram_slave_next_wdata;
wire    [3:0]   sdram_slave_next_wstrb;
wire    [31:0]  sdram_slave_preload_wdata;
wire    [3:0]   sdram_slave_preload_wstrb;

// ============================================================
// CPU AXI4 master buses
// ============================================================

// CPU AXI4 master → axi_sdram_slave (via arbiter)
wire        cpu_m_sdram_arvalid;
wire        cpu_m_sdram_arready;
wire [31:0] cpu_m_sdram_araddr;
wire [7:0]  cpu_m_sdram_arlen;
wire        cpu_m_sdram_rvalid;
wire        cpu_m_sdram_rready;
wire [31:0] cpu_m_sdram_rdata;
wire [1:0]  cpu_m_sdram_rresp;
wire        cpu_m_sdram_rlast;
wire        cpu_m_sdram_awvalid;
wire        cpu_m_sdram_awready;
wire [31:0] cpu_m_sdram_awaddr;
wire [7:0]  cpu_m_sdram_awlen;
wire        cpu_m_sdram_wvalid;
wire        cpu_m_sdram_wready;
wire [31:0] cpu_m_sdram_wdata;
wire [3:0]  cpu_m_sdram_wstrb;
wire        cpu_m_sdram_wlast;
wire        cpu_m_sdram_bvalid;
wire [1:0]  cpu_m_sdram_bresp;

// CPU AXI4 master → cram0_cdc (clk_cpu slave side of the CDC).
// Crosses into clk_74a inside cram0_cdc and drives the controller
// via the mux block above when cram0_mode_74a=1.
wire        cpu_m_cram0_arvalid;
wire        cpu_m_cram0_arready;
wire [31:0] cpu_m_cram0_araddr;
wire [7:0]  cpu_m_cram0_arlen;
wire        cpu_m_cram0_rvalid;
wire        cpu_m_cram0_rready;
wire [31:0] cpu_m_cram0_rdata;
wire [1:0]  cpu_m_cram0_rresp;
wire        cpu_m_cram0_rlast;
wire        cpu_m_cram0_awvalid;
wire        cpu_m_cram0_awready;
wire [31:0] cpu_m_cram0_awaddr;
wire [7:0]  cpu_m_cram0_awlen;
wire        cpu_m_cram0_wvalid;
wire        cpu_m_cram0_wready;
wire [31:0] cpu_m_cram0_wdata;
wire [3:0]  cpu_m_cram0_wstrb;
wire        cpu_m_cram0_wlast;
wire        cpu_m_cram0_bvalid;
wire [1:0]  cpu_m_cram0_bresp;

// SRAM CPU path removed - GPU has exclusive direct access (Z-buffer).
// CRAM1 has no CPU AXI target port (the chip is GPU-private texture memory).

// Audio FIFO status — HW mixer drives the FIFO directly (v2).
wire [9:0]  audio_fifo_level;
wire        audio_fifo_full;

// Link MMIO register interface
wire        link_reg_wr;
wire        link_reg_rd;
wire [4:0]  link_reg_addr;
wire [31:0] link_reg_wdata;
wire [31:0] link_reg_rdata;

// Link physical interface
wire        link_si_i;
wire        link_sck_i;
wire        link_sd_i;
wire        link_so_out;
wire        link_so_oe;
wire        link_sck_out;
wire        link_sck_oe;
wire        link_sd_out;
wire        link_sd_oe;

// CPU AXI4 master → axi_periph_slave (local peripherals)
wire        cpu_m_local_arvalid;
wire        cpu_m_local_arready;
wire [31:0] cpu_m_local_araddr;
wire [7:0]  cpu_m_local_arlen;
wire        cpu_m_local_rvalid;
wire        cpu_m_local_rready;
wire [31:0] cpu_m_local_rdata;
wire [1:0]  cpu_m_local_rresp;
wire        cpu_m_local_rlast;
wire        cpu_m_local_awvalid;
wire        cpu_m_local_awready;
wire [31:0] cpu_m_local_awaddr;
wire [7:0]  cpu_m_local_awlen;
wire [1:0]  cpu_m_local_awburst;
wire        cpu_m_local_wvalid;
wire        cpu_m_local_wready;
wire [31:0] cpu_m_local_wdata;
wire [3:0]  cpu_m_local_wstrb;
wire        cpu_m_local_wlast;
wire        cpu_m_local_bvalid;
wire [1:0]  cpu_m_local_bresp;

// Optional top-level CPU AXI pipeline cuts.  The CPU wrapper already
// contains slices close to VexiiRiscv; defining OF_PIPE_CPU_AXI_TOP adds
// another protocol-preserving cut at the core_top boundary so the fitter can
// place memory/peripheral logic independently.  Default builds stay direct.
wire        cpu_sdram_bus_arvalid;
wire        cpu_sdram_bus_arready;
wire [31:0] cpu_sdram_bus_araddr;
wire [7:0]  cpu_sdram_bus_arlen;
wire        cpu_sdram_bus_rvalid;
wire        cpu_sdram_bus_rready;
wire [31:0] cpu_sdram_bus_rdata;
wire [1:0]  cpu_sdram_bus_rresp;
wire        cpu_sdram_bus_rlast;
wire        cpu_sdram_bus_awvalid;
wire        cpu_sdram_bus_awready;
wire [31:0] cpu_sdram_bus_awaddr;
wire [7:0]  cpu_sdram_bus_awlen;
wire        cpu_sdram_bus_wvalid;
wire        cpu_sdram_bus_wready;
wire [31:0] cpu_sdram_bus_wdata;
wire [3:0]  cpu_sdram_bus_wstrb;
wire        cpu_sdram_bus_wlast;
wire        cpu_sdram_bus_bvalid;
wire [1:0]  cpu_sdram_bus_bresp;

wire        cpu_cram0_bus_arvalid;
wire        cpu_cram0_bus_arready;
wire [31:0] cpu_cram0_bus_araddr;
wire [7:0]  cpu_cram0_bus_arlen;
wire        cpu_cram0_bus_rvalid;
wire        cpu_cram0_bus_rready;
wire [31:0] cpu_cram0_bus_rdata;
wire [1:0]  cpu_cram0_bus_rresp;
wire        cpu_cram0_bus_rlast;
wire        cpu_cram0_bus_awvalid;
wire        cpu_cram0_bus_awready;
wire [31:0] cpu_cram0_bus_awaddr;
wire [7:0]  cpu_cram0_bus_awlen;
wire        cpu_cram0_bus_wvalid;
wire        cpu_cram0_bus_wready;
wire [31:0] cpu_cram0_bus_wdata;
wire [3:0]  cpu_cram0_bus_wstrb;
wire        cpu_cram0_bus_wlast;
wire        cpu_cram0_bus_bvalid;
wire [1:0]  cpu_cram0_bus_bresp;

wire        cpu_local_bus_arvalid;
wire        cpu_local_bus_arready;
wire [31:0] cpu_local_bus_araddr;
wire [7:0]  cpu_local_bus_arlen;
wire        cpu_local_bus_rvalid;
wire        cpu_local_bus_rready;
wire [31:0] cpu_local_bus_rdata;
wire [1:0]  cpu_local_bus_rresp;
wire        cpu_local_bus_rlast;
wire        cpu_local_bus_awvalid;
wire        cpu_local_bus_awready;
wire [31:0] cpu_local_bus_awaddr;
wire [7:0]  cpu_local_bus_awlen;
wire [1:0]  cpu_local_bus_awburst;
wire        cpu_local_bus_wvalid;
wire        cpu_local_bus_wready;
wire [31:0] cpu_local_bus_wdata;
wire [3:0]  cpu_local_bus_wstrb;
wire        cpu_local_bus_wlast;
wire        cpu_local_bus_bvalid;
wire [1:0]  cpu_local_bus_bresp;

`ifdef OF_PIPE_CPU_AXI_TOP
axi_register_slice #(.W(40)) cpu_sdram_ar_top_slice (
    .clk(clk_cpu), .reset_n(reset_n_cpu_core),
    .s_valid(cpu_m_sdram_arvalid), .s_ready(cpu_m_sdram_arready),
    .s_payload({cpu_m_sdram_araddr, cpu_m_sdram_arlen}),
    .m_valid(cpu_sdram_bus_arvalid), .m_ready(cpu_sdram_bus_arready),
    .m_payload({cpu_sdram_bus_araddr, cpu_sdram_bus_arlen})
);
axi_register_slice #(.W(35)) cpu_sdram_r_top_slice (
    .clk(clk_cpu), .reset_n(reset_n_cpu_core),
    .s_valid(cpu_sdram_bus_rvalid), .s_ready(cpu_sdram_bus_rready),
    .s_payload({cpu_sdram_bus_rdata, cpu_sdram_bus_rresp, cpu_sdram_bus_rlast}),
    .m_valid(cpu_m_sdram_rvalid), .m_ready(cpu_m_sdram_rready),
    .m_payload({cpu_m_sdram_rdata, cpu_m_sdram_rresp, cpu_m_sdram_rlast})
);
axi_register_slice #(.W(40)) cpu_sdram_aw_top_slice (
    .clk(clk_cpu), .reset_n(reset_n_cpu_core),
    .s_valid(cpu_m_sdram_awvalid), .s_ready(cpu_m_sdram_awready),
    .s_payload({cpu_m_sdram_awaddr, cpu_m_sdram_awlen}),
    .m_valid(cpu_sdram_bus_awvalid), .m_ready(cpu_sdram_bus_awready),
    .m_payload({cpu_sdram_bus_awaddr, cpu_sdram_bus_awlen})
);
axi_register_slice #(.W(37)) cpu_sdram_w_top_slice (
    .clk(clk_cpu), .reset_n(reset_n_cpu_core),
    .s_valid(cpu_m_sdram_wvalid), .s_ready(cpu_m_sdram_wready),
    .s_payload({cpu_m_sdram_wdata, cpu_m_sdram_wstrb, cpu_m_sdram_wlast}),
    .m_valid(cpu_sdram_bus_wvalid), .m_ready(cpu_sdram_bus_wready),
    .m_payload({cpu_sdram_bus_wdata, cpu_sdram_bus_wstrb, cpu_sdram_bus_wlast})
);
axi_register_slice #(.W(2)) cpu_sdram_b_top_slice (
    .clk(clk_cpu), .reset_n(reset_n_cpu_core),
    .s_valid(cpu_sdram_bus_bvalid), .s_ready(),
    .s_payload(cpu_sdram_bus_bresp),
    .m_valid(cpu_m_sdram_bvalid), .m_ready(1'b1),
    .m_payload(cpu_m_sdram_bresp)
);

axi_register_slice #(.W(40)) cpu_cram0_ar_top_slice (
    .clk(clk_cpu), .reset_n(reset_n_cpu_cram),
    .s_valid(cpu_m_cram0_arvalid), .s_ready(cpu_m_cram0_arready),
    .s_payload({cpu_m_cram0_araddr, cpu_m_cram0_arlen}),
    .m_valid(cpu_cram0_bus_arvalid), .m_ready(cpu_cram0_bus_arready),
    .m_payload({cpu_cram0_bus_araddr, cpu_cram0_bus_arlen})
);
axi_register_slice #(.W(35)) cpu_cram0_r_top_slice (
    .clk(clk_cpu), .reset_n(reset_n_cpu_cram),
    .s_valid(cpu_cram0_bus_rvalid), .s_ready(cpu_cram0_bus_rready),
    .s_payload({cpu_cram0_bus_rdata, cpu_cram0_bus_rresp, cpu_cram0_bus_rlast}),
    .m_valid(cpu_m_cram0_rvalid), .m_ready(cpu_m_cram0_rready),
    .m_payload({cpu_m_cram0_rdata, cpu_m_cram0_rresp, cpu_m_cram0_rlast})
);
axi_register_slice #(.W(40)) cpu_cram0_aw_top_slice (
    .clk(clk_cpu), .reset_n(reset_n_cpu_cram),
    .s_valid(cpu_m_cram0_awvalid), .s_ready(cpu_m_cram0_awready),
    .s_payload({cpu_m_cram0_awaddr, cpu_m_cram0_awlen}),
    .m_valid(cpu_cram0_bus_awvalid), .m_ready(cpu_cram0_bus_awready),
    .m_payload({cpu_cram0_bus_awaddr, cpu_cram0_bus_awlen})
);
axi_register_slice #(.W(37)) cpu_cram0_w_top_slice (
    .clk(clk_cpu), .reset_n(reset_n_cpu_cram),
    .s_valid(cpu_m_cram0_wvalid), .s_ready(cpu_m_cram0_wready),
    .s_payload({cpu_m_cram0_wdata, cpu_m_cram0_wstrb, cpu_m_cram0_wlast}),
    .m_valid(cpu_cram0_bus_wvalid), .m_ready(cpu_cram0_bus_wready),
    .m_payload({cpu_cram0_bus_wdata, cpu_cram0_bus_wstrb, cpu_cram0_bus_wlast})
);
axi_register_slice #(.W(2)) cpu_cram0_b_top_slice (
    .clk(clk_cpu), .reset_n(reset_n_cpu_cram),
    .s_valid(cpu_cram0_bus_bvalid), .s_ready(),
    .s_payload(cpu_cram0_bus_bresp),
    .m_valid(cpu_m_cram0_bvalid), .m_ready(1'b1),
    .m_payload(cpu_m_cram0_bresp)
);

axi_register_slice #(.W(40)) cpu_local_ar_top_slice (
    .clk(clk_cpu), .reset_n(reset_n_cpu_core),
    .s_valid(cpu_m_local_arvalid), .s_ready(cpu_m_local_arready),
    .s_payload({cpu_m_local_araddr, cpu_m_local_arlen}),
    .m_valid(cpu_local_bus_arvalid), .m_ready(cpu_local_bus_arready),
    .m_payload({cpu_local_bus_araddr, cpu_local_bus_arlen})
);
axi_register_slice #(.W(35)) cpu_local_r_top_slice (
    .clk(clk_cpu), .reset_n(reset_n_cpu_core),
    .s_valid(cpu_local_bus_rvalid), .s_ready(cpu_local_bus_rready),
    .s_payload({cpu_local_bus_rdata, cpu_local_bus_rresp, cpu_local_bus_rlast}),
    .m_valid(cpu_m_local_rvalid), .m_ready(cpu_m_local_rready),
    .m_payload({cpu_m_local_rdata, cpu_m_local_rresp, cpu_m_local_rlast})
);
axi_register_slice #(.W(42)) cpu_local_aw_top_slice (
    .clk(clk_cpu), .reset_n(reset_n_cpu_core),
    .s_valid(cpu_m_local_awvalid), .s_ready(cpu_m_local_awready),
    .s_payload({cpu_m_local_awaddr, cpu_m_local_awlen, cpu_m_local_awburst}),
    .m_valid(cpu_local_bus_awvalid), .m_ready(cpu_local_bus_awready),
    .m_payload({cpu_local_bus_awaddr, cpu_local_bus_awlen, cpu_local_bus_awburst})
);
axi_register_slice #(.W(37)) cpu_local_w_top_slice (
    .clk(clk_cpu), .reset_n(reset_n_cpu_core),
    .s_valid(cpu_m_local_wvalid), .s_ready(cpu_m_local_wready),
    .s_payload({cpu_m_local_wdata, cpu_m_local_wstrb, cpu_m_local_wlast}),
    .m_valid(cpu_local_bus_wvalid), .m_ready(cpu_local_bus_wready),
    .m_payload({cpu_local_bus_wdata, cpu_local_bus_wstrb, cpu_local_bus_wlast})
);
axi_register_slice #(.W(2)) cpu_local_b_top_slice (
    .clk(clk_cpu), .reset_n(reset_n_cpu_core),
    .s_valid(cpu_local_bus_bvalid), .s_ready(),
    .s_payload(cpu_local_bus_bresp),
    .m_valid(cpu_m_local_bvalid), .m_ready(1'b1),
    .m_payload(cpu_m_local_bresp)
);
`else
assign cpu_sdram_bus_arvalid = cpu_m_sdram_arvalid;
assign cpu_m_sdram_arready   = cpu_sdram_bus_arready;
assign cpu_sdram_bus_araddr  = cpu_m_sdram_araddr;
assign cpu_sdram_bus_arlen   = cpu_m_sdram_arlen;
assign cpu_m_sdram_rvalid    = cpu_sdram_bus_rvalid;
assign cpu_sdram_bus_rready  = cpu_m_sdram_rready;
assign cpu_m_sdram_rdata     = cpu_sdram_bus_rdata;
assign cpu_m_sdram_rresp     = cpu_sdram_bus_rresp;
assign cpu_m_sdram_rlast     = cpu_sdram_bus_rlast;
assign cpu_sdram_bus_awvalid = cpu_m_sdram_awvalid;
assign cpu_m_sdram_awready   = cpu_sdram_bus_awready;
assign cpu_sdram_bus_awaddr  = cpu_m_sdram_awaddr;
assign cpu_sdram_bus_awlen   = cpu_m_sdram_awlen;
assign cpu_sdram_bus_wvalid  = cpu_m_sdram_wvalid;
assign cpu_m_sdram_wready    = cpu_sdram_bus_wready;
assign cpu_sdram_bus_wdata   = cpu_m_sdram_wdata;
assign cpu_sdram_bus_wstrb   = cpu_m_sdram_wstrb;
assign cpu_sdram_bus_wlast   = cpu_m_sdram_wlast;
assign cpu_m_sdram_bvalid    = cpu_sdram_bus_bvalid;
assign cpu_m_sdram_bresp     = cpu_sdram_bus_bresp;

assign cpu_cram0_bus_arvalid = cpu_m_cram0_arvalid;
assign cpu_m_cram0_arready   = cpu_cram0_bus_arready;
assign cpu_cram0_bus_araddr  = cpu_m_cram0_araddr;
assign cpu_cram0_bus_arlen   = cpu_m_cram0_arlen;
assign cpu_m_cram0_rvalid    = cpu_cram0_bus_rvalid;
assign cpu_cram0_bus_rready  = cpu_m_cram0_rready;
assign cpu_m_cram0_rdata     = cpu_cram0_bus_rdata;
assign cpu_m_cram0_rresp     = cpu_cram0_bus_rresp;
assign cpu_m_cram0_rlast     = cpu_cram0_bus_rlast;
assign cpu_cram0_bus_awvalid = cpu_m_cram0_awvalid;
assign cpu_m_cram0_awready   = cpu_cram0_bus_awready;
assign cpu_cram0_bus_awaddr  = cpu_m_cram0_awaddr;
assign cpu_cram0_bus_awlen   = cpu_m_cram0_awlen;
assign cpu_cram0_bus_wvalid  = cpu_m_cram0_wvalid;
assign cpu_m_cram0_wready    = cpu_cram0_bus_wready;
assign cpu_cram0_bus_wdata   = cpu_m_cram0_wdata;
assign cpu_cram0_bus_wstrb   = cpu_m_cram0_wstrb;
assign cpu_cram0_bus_wlast   = cpu_m_cram0_wlast;
assign cpu_m_cram0_bvalid    = cpu_cram0_bus_bvalid;
assign cpu_m_cram0_bresp     = cpu_cram0_bus_bresp;

assign cpu_local_bus_arvalid = cpu_m_local_arvalid;
assign cpu_m_local_arready   = cpu_local_bus_arready;
assign cpu_local_bus_araddr  = cpu_m_local_araddr;
assign cpu_local_bus_arlen   = cpu_m_local_arlen;
assign cpu_m_local_rvalid    = cpu_local_bus_rvalid;
assign cpu_local_bus_rready  = cpu_m_local_rready;
assign cpu_m_local_rdata     = cpu_local_bus_rdata;
assign cpu_m_local_rresp     = cpu_local_bus_rresp;
assign cpu_m_local_rlast     = cpu_local_bus_rlast;
assign cpu_local_bus_awvalid = cpu_m_local_awvalid;
assign cpu_m_local_awready   = cpu_local_bus_awready;
assign cpu_local_bus_awaddr  = cpu_m_local_awaddr;
assign cpu_local_bus_awlen   = cpu_m_local_awlen;
assign cpu_local_bus_awburst = cpu_m_local_awburst;
assign cpu_local_bus_wvalid  = cpu_m_local_wvalid;
assign cpu_m_local_wready    = cpu_local_bus_wready;
assign cpu_local_bus_wdata   = cpu_m_local_wdata;
assign cpu_local_bus_wstrb   = cpu_m_local_wstrb;
assign cpu_local_bus_wlast   = cpu_m_local_wlast;
assign cpu_m_local_bvalid    = cpu_local_bus_bvalid;
assign cpu_m_local_bresp     = cpu_local_bus_bresp;
`endif

// AXI4 arbiter output → axi_sdram_slave (direct, no pipeline)
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
wire        arb_s_wcont;   // arbiter → slave: GPU write bursts are continuous (native-streaming OK)

// Bridge -> SDRAM AXI4 write master REMOVED.  The direct SD->SDRAM load path
// corrupted the loaded image on silicon (sparse single-bit flip) and is
// permanently disabled in firmware — the legacy CRAM0 bounce + CPU copy is the
// only SDRAM-destination path.  The dead bridge_to_sdram module and its arbiter
// M2 AW/W leg are gone; arbiter M2 AW/W is tied idle below.
// (The CRAM0-leg overrun is observable via bridge_wr_overrun -> brg_cram0_drop_overrun,
// surfaced to firmware in the DS_BRIDGE_WCNT debug word; no separate alias needed.)

// ============================================================
// Bridge read data mux (registered — one cycle after bridge_rd)
// ============================================================
always @(posedge clk_74a) begin
    casex(bridge_addr)
        default: begin
            bridge_rd_data <= 0;
        end
        32'h20xxxxxx: begin
            // CRAM0 bridge window. Automatic nonvolatile save-on-exit
            // reads this address range after firmware hands ownership
            // back to the bridge.
            if (bridge_cram0_rdata_valid)
                bridge_rd_data <= bridge_cram0_rdata;
        end

        32'hF7000000: begin
        //the byte order is inverted because the bridge_endian_little = 1
        bridge_rd_data <= {analogizer_settings[7:0],analogizer_settings[15:8],analogizer_settings[23:16],analogizer_settings[31:24]};
        end
        32'hF7000004: begin
        //the byte order is inverted because the bridge_endian_little = 1
        bridge_rd_data <= {signed_hoff[7:0],signed_hoff[15:8],signed_hoff[23:16],signed_hoff[31:24]}; //signed_hoff;
        end
        32'hF7000008: begin
        //the byte order is inverted because the bridge_endian_little = 1
        bridge_rd_data <= {signed_voff[7:0],signed_voff[15:8],signed_voff[23:16],signed_voff[31:24]}; //signed_voff;
        end

        32'hF8xxxxxx: begin
            bridge_rd_data <= cmd_bridge_rd_data;
        end
    endcase
end

// Interact variable writes (SNAC adapter type from APF menu)
//
// No analogizer (docs/os30-quake2-cut-plan.md C2): the analogizer
// settings/hoff/voff writes are dropped — menu and CPU writes still
// complete on the bridge/MMIO side, they just no longer land anywhere.
// The three source registers then hold their reset value forever, so the
// whole downstream cone folds: the 3-domain settings CDC copies, the
// ena/video-type/preclaim decode, fx and the chroma constants, AND
// pocket_blank_screen — which is exactly right, because with no analog
// output the LCD is the only display and "Pocket LCD = Off" must never
// blank it.  The cart port likewise stays in UART-console mode
// unconditionally.  app_id is NOT analogizer-specific (periph .app_id)
// and keeps its write arm in both configs.
always @(posedge clk_74a) begin
    if (bridge_wr) begin
        casex (bridge_addr)
`ifdef INCLUDE_ANALOGIZER
        32'hF7000000: analogizer_settings <= {
        bridge_wr_data[7:0],
        bridge_wr_data[15:8],
        bridge_wr_data[23:16],
        bridge_wr_data[31:24]};

        32'hF7000004: signed_hoff <= {
        bridge_wr_data[7:0],
        bridge_wr_data[15:8],
        bridge_wr_data[23:16],
        bridge_wr_data[31:24]};

        32'hF7000008: signed_voff <= {
        bridge_wr_data[7:0],
        bridge_wr_data[15:8],
        bridge_wr_data[23:16],
        bridge_wr_data[31:24]};
`endif

        32'hF7000010: app_id_74a <= {
        bridge_wr_data[7:0],
        bridge_wr_data[15:8],
        bridge_wr_data[23:16],
        bridge_wr_data[31:24]};
        endcase
    end

`ifdef INCLUDE_ANALOGIZER
    if (analogizer_cpu_wr_event[0])
        analogizer_settings <= analogizer_cpu_wr_settings_s2;
    if (analogizer_cpu_wr_event[1])
        signed_hoff <= analogizer_cpu_wr_hoffset_s2;
    if (analogizer_cpu_wr_event[2])
        signed_voff <= analogizer_cpu_wr_voffset_s2;
`endif
end

// bridge_wr_idle: true when both bridge write targets have drained.
// The CRAM0 side must be idle before firmware flips CRAM0 ownership; the
// SDRAM side must be idle before the data-slot command is acknowledged.
// Conservative: reports busy whenever either path has work in flight.
reg  bridge_wr_seen_74a;
always @(posedge clk_74a) begin
    if (bridge_cram0_wr_pulse || bridge_cram0_rd_pulse)
        bridge_wr_seen_74a <= 1'b1;
    else if (!ctrl_word_busy && !bridge_wr && !bridge_rd)
        bridge_wr_seen_74a <= 1'b0;
end

// NOTE: bridge_wr_overrun is intentionally NOT part of this busy term.  It is
// a sticky error latch (cleared only by reset); ORing it here turned a
// transient CRAM0 write-FIFO overflow into a permanent wedge of the data-slot
// completion handshake (bridge_active_74a stuck high -> bridge_wr_idle never
// asserts).  Overrun is surfaced as an observable diagnostic instead.
wire bridge_active_74a = bridge_wr_seen_74a
                       | ctrl_word_busy
                       | bridge_wr_busy
                       | bridge_wr_issue
                       | bridge_cram0_wr_pulse
                       | bridge_cram0_rd_pulse;

// Synchronise drain flag back to clk_cpu for the DS_DONE waiter.
reg [2:0] bridge_active_cpu_sync;
always @(posedge clk_cpu)
    bridge_active_cpu_sync <= {bridge_active_cpu_sync[1:0], bridge_active_74a};
wire bridge_cram0_idle_cpu = ~bridge_active_cpu_sync[2];
// SDRAM-leg idle term dropped with bridge_to_sdram — the only bridge write
// destination is now CRAM0, so CRAM0 idle alone gates the DS DONE waiter.
wire bridge_wr_idle = bridge_cram0_idle_cpu;

// Bridge DMA active tracking
reg bridge_dma_active;
reg cpu_ds_read_prev, cpu_ds_write_prev, cpu_ds_open_prev, cpu_ds_getfile_prev;
reg [2:0] ds_done_ram_sync;
reg [9:0] ds_done_quiet_count;
reg       ds_done_quiet_reached;
reg [7:0] ds_done_blanking;
localparam [9:0] DS_DONE_QUIET_CYCLES = 10'd1023;
localparam [7:0] DS_DONE_BLANKING_CYCLES = 8'd128;
wire cpu_ds_read_start = cpu_target_dataslot_read && !cpu_ds_read_prev;
wire cpu_ds_write_start = cpu_target_dataslot_write && !cpu_ds_write_prev;
wire cpu_ds_open_start = cpu_target_dataslot_openfile && !cpu_ds_open_prev;
wire cpu_ds_getfile_start = cpu_target_dataslot_getfile && !cpu_ds_getfile_prev;
wire cpu_ds_start = cpu_ds_read_start || cpu_ds_write_start || cpu_ds_open_start || cpu_ds_getfile_start;
wire ds_done_blanking_active = (ds_done_blanking != 8'd0);
wire target_dataslot_done_safe = ds_done_ram_sync[2] && ds_done_quiet_reached;
always @(posedge clk_ram_controller) begin
    cpu_ds_read_prev <= cpu_target_dataslot_read;
    cpu_ds_write_prev <= cpu_target_dataslot_write;
    cpu_ds_open_prev <= cpu_target_dataslot_openfile;
    cpu_ds_getfile_prev <= cpu_target_dataslot_getfile;

    if (!reset_n_apf) begin
        bridge_dma_active <= 1'b0;
        ds_done_ram_sync <= 3'b000;
        ds_done_quiet_count <= 10'd0;
        ds_done_quiet_reached <= 1'b0;
        ds_done_blanking <= 8'd0;
    end else begin
        ds_done_ram_sync <= {ds_done_ram_sync[1:0], target_dataslot_done};

        if (ds_done_blanking != 8'd0)
            ds_done_blanking <= ds_done_blanking - 8'd1;

        if (cpu_ds_start) begin
            ds_done_quiet_count <= 10'd0;
            ds_done_quiet_reached <= 1'b0;
            ds_done_blanking <= DS_DONE_BLANKING_CYCLES;

            if (cpu_ds_read_start || cpu_ds_write_start)
                bridge_dma_active <= 1'b1;
        end else if (!ds_done_blanking_active) begin
            if (ds_done_ram_sync[2]) begin
                if (bridge_wr_idle) begin
                    if (!ds_done_quiet_reached) begin
                        ds_done_quiet_count <= ds_done_quiet_count + 10'd1;
                        if (ds_done_quiet_count == DS_DONE_QUIET_CYCLES - 10'd1)
                            ds_done_quiet_reached <= 1'b1;
                    end
                end else begin
                    ds_done_quiet_count <= 10'd0;
                    ds_done_quiet_reached <= 1'b0;
                end
            end else begin
                ds_done_quiet_count <= 10'd0;
                ds_done_quiet_reached <= 1'b0;
            end

            if (bridge_dma_active && target_dataslot_done_safe)
                bridge_dma_active <= 1'b0;
        end
    end
end

// Bridge SDRAM reads are not used.  Bridge writes target either SDRAM
// through bridge_to_sdram or CRAM0 through the ownership-mux block above.

//
// host/target command handler
//
    wire            reset_n_apf;
    wire    [31:0]  cmd_bridge_rd_data;

    wire reset_n = reset_n_apf & bcr_init_done;

    // clk_cpu-domain reset replicas.  reset_n asserts asynchronously
    // from the bridge/BCR gate, but deasserts synchronously and on
    // separate local branches so fitter placement is not constrained by
    // one monolithic reset tree feeding CPU, GPU, audio, link and CRAM.
    reg [1:0] reset_cpu_core_sync;
    reg [1:0] reset_cpu_media_sync;
    reg [1:0] reset_cpu_cram_sync;
    always @(posedge clk_cpu or negedge reset_n) begin
        if (~reset_n) begin
            reset_cpu_core_sync  <= 2'b00;
            reset_cpu_media_sync <= 2'b00;
            reset_cpu_cram_sync  <= 2'b00;
        end else begin
            reset_cpu_core_sync  <= {reset_cpu_core_sync[0],  1'b1};
            reset_cpu_media_sync <= {reset_cpu_media_sync[0], 1'b1};
            reset_cpu_cram_sync  <= {reset_cpu_cram_sync[0],  1'b1};
        end
    end
    wire reset_n_cpu_core  = reset_cpu_core_sync[1];
    wire reset_n_cpu_media = reset_cpu_media_sync[1];
    wire reset_n_cpu_cram  = reset_cpu_cram_sync[1];

    // Synchronize reset deassertion to clk_vid domain
    reg [1:0] reset_vid_sync;
    wire reset_n_vid = reset_vid_sync[1];
    always @(posedge clk_vid or negedge reset_n) begin
        if (~reset_n)
            reset_vid_sync <= 2'b0;
        else
            reset_vid_sync <= {reset_vid_sync[0], 1'b1};
    end

    // Synchronize reset deassertion to the 49.152 MHz Analogizer helper
    // domain used by the shared scanout/line-doubler timing.
    reg [1:0] reset_analog_sync;
    wire reset_n_analog = reset_analog_sync[1];
    always @(posedge clk_core_49152 or negedge reset_n) begin
        if (~reset_n)
            reset_analog_sync <= 2'b0;
        else
            reset_analog_sync <= {reset_analog_sync[0], 1'b1};
    end

    wire            bcr_init_done_s;
    synch_3 sync_bcr(bcr_init_done, bcr_init_done_s, clk_74a);
    wire            status_boot_done  = bcr_init_done_s;
    wire            status_setup_done = bcr_init_done_s;
    wire            status_running    = reset_n;

    wire            dataslot_requestread;
    wire    [15:0]  dataslot_requestread_id;
    wire            dataslot_requestread_ack = 1;
    wire            dataslot_requestread_ok = 1;

    wire            dataslot_requestwrite;
    wire    [15:0]  dataslot_requestwrite_id;
    wire    [31:0]  dataslot_requestwrite_size;
    wire            dataslot_requestwrite_ack = 1;
    wire            dataslot_requestwrite_ok = 1;

    wire            dataslot_update;
    wire    [15:0]  dataslot_update_id;
    wire    [31:0]  dataslot_update_size;

    wire            dataslot_allcomplete;

    wire     [31:0] rtc_epoch_seconds;
    wire     [31:0] rtc_date_bcd;
    wire     [31:0] rtc_time_bcd;
    wire            rtc_valid;

    wire            savestate_supported;
    wire    [31:0]  savestate_addr;
    wire    [31:0]  savestate_size;
    wire    [31:0]  savestate_maxloadsize;

    wire            savestate_start;
    wire            savestate_start_ack;
    wire            savestate_start_busy;
    wire            savestate_start_ok;
    wire            savestate_start_err;

    wire            savestate_load;
    wire            savestate_load_ack;
    wire            savestate_load_busy;
    wire            savestate_load_ok;
    wire            savestate_load_err;

    wire            osnotify_inmenu;

    // CPU-side signals (in clk_ram_controller domain)
    wire            cpu_target_dataslot_read;
    wire            cpu_target_dataslot_write;
    wire            cpu_target_dataslot_openfile;
    wire            cpu_target_dataslot_getfile;
    wire    [15:0]  cpu_target_dataslot_id;
    wire    [31:0]  cpu_target_dataslot_slotoffset;
    wire    [31:0]  cpu_target_dataslot_bridgeaddr;
    wire    [31:0]  cpu_target_dataslot_length;
    wire    [31:0]  cpu_target_buffer_param_struct;
    wire    [31:0]  cpu_target_buffer_resp_struct;

    // Bridge-side signals (in clk_74a domain)
    wire            target_dataslot_ack;
    wire            target_dataslot_done;
    wire    [2:0]   target_dataslot_err;

    wire            target_dataslot_read;
    wire            target_dataslot_write;
    wire            target_dataslot_openfile;
    wire            target_dataslot_getfile;

    synch_3 sync_ds_read(cpu_target_dataslot_read, target_dataslot_read, clk_74a);
    synch_3 sync_ds_write(cpu_target_dataslot_write, target_dataslot_write, clk_74a);
    synch_3 sync_ds_openfile(cpu_target_dataslot_openfile, target_dataslot_openfile, clk_74a);
    synch_3 sync_ds_getfile(cpu_target_dataslot_getfile, target_dataslot_getfile, clk_74a);

    reg [15:0]  cpu_ds_id_sync1, cpu_ds_id_sync2;
    reg [31:0]  cpu_ds_slotoffset_sync1, cpu_ds_slotoffset_sync2;
    reg [31:0]  cpu_ds_bridgeaddr_sync1, cpu_ds_bridgeaddr_sync2;
    reg [31:0]  cpu_ds_length_sync1, cpu_ds_length_sync2;
    reg [31:0]  cpu_ds_param_sync1, cpu_ds_param_sync2;
    reg [31:0]  cpu_ds_resp_sync1, cpu_ds_resp_sync2;

    reg target_dataslot_read_1, target_dataslot_write_1, target_dataslot_openfile_1;
    reg [15:0]  target_dataslot_id;
    reg [31:0]  target_dataslot_slotoffset;
    reg [31:0]  target_dataslot_bridgeaddr;
    reg [31:0]  target_dataslot_length;
    reg [31:0]  target_buffer_param_struct;
    reg [31:0]  target_buffer_resp_struct;

    always @(posedge clk_74a) begin
        cpu_ds_id_sync1 <= cpu_target_dataslot_id;
        cpu_ds_id_sync2 <= cpu_ds_id_sync1;
        cpu_ds_slotoffset_sync1 <= cpu_target_dataslot_slotoffset;
        cpu_ds_slotoffset_sync2 <= cpu_ds_slotoffset_sync1;
        cpu_ds_bridgeaddr_sync1 <= cpu_target_dataslot_bridgeaddr;
        cpu_ds_bridgeaddr_sync2 <= cpu_ds_bridgeaddr_sync1;
        cpu_ds_length_sync1 <= cpu_target_dataslot_length;
        cpu_ds_length_sync2 <= cpu_ds_length_sync1;
        cpu_ds_param_sync1 <= cpu_target_buffer_param_struct;
        cpu_ds_param_sync2 <= cpu_ds_param_sync1;
        cpu_ds_resp_sync1 <= cpu_target_buffer_resp_struct;
        cpu_ds_resp_sync2 <= cpu_ds_resp_sync1;

        target_dataslot_read_1 <= target_dataslot_read;
        target_dataslot_write_1 <= target_dataslot_write;
        target_dataslot_openfile_1 <= target_dataslot_openfile;

        if ((target_dataslot_read && !target_dataslot_read_1) ||
            (target_dataslot_write && !target_dataslot_write_1) ||
            (target_dataslot_openfile && !target_dataslot_openfile_1)) begin
            target_dataslot_id <= cpu_ds_id_sync2;
            target_dataslot_slotoffset <= cpu_ds_slotoffset_sync2;
            target_dataslot_bridgeaddr <= cpu_ds_bridgeaddr_sync2;
            target_dataslot_length <= cpu_ds_length_sync2;
            target_buffer_param_struct <= cpu_ds_param_sync2;
            target_buffer_resp_struct <= cpu_ds_resp_sync2;
        end
    end

// ============================================================
// F2i: per-DS-command bridge word counters (clk_74a)
// ============================================================
// Cold-start bridge-corruption forensics: count every bridge word the
// APF host pushes at us during one data-slot command, on BOTH write
// legs, BEFORE any FIFO/mux drop.  Firmware compares the count against
// length/4: a short count means words never made it out of the SPI RX
// capture (RX-side drop); an exact count with bad content means the
// words arrived already corrupted.
//   [14:0]  CRAM0-leg words   (bridge_cram0_wr_pulse, saturates 0x7FFF)
//   [15]    sticky (F6): CRAM0-range bridge write arrived while the CPU
//           owned the CRAM0 mux — the write was silently dropped by the
//           bridge_wr_accept gate above
//   [30:16] RESERVED, reads 0 — was the SDRAM-leg word count (bridge_to_sdram
//           detect_wr), removed with the dead direct-SDRAM write path
//   [31]    sticky: CRAM0 bridge write FIFO overrun drop this command
// All four fields clear on the DS command issue pulse (same edge that
// latches target_dataslot_* into the bridge handler below), so after
// DONE the register describes exactly the command that just ran.
reg         target_dataslot_getfile_cnt_1 = 1'b0;
reg [14:0]  brg_wcnt_cram0 = 15'd0;
reg [14:0]  brg_wcnt_sdram = 15'd0;
reg         brg_cram0_drop_cpu_owned = 1'b0;
reg         brg_cram0_drop_overrun   = 1'b0;

wire ds_cmd_pulse_74a =
    (target_dataslot_read     && !target_dataslot_read_1)     ||
    (target_dataslot_write    && !target_dataslot_write_1)    ||
    (target_dataslot_openfile && !target_dataslot_openfile_1) ||
    (target_dataslot_getfile  && !target_dataslot_getfile_cnt_1);

always @(posedge clk_74a) begin
    target_dataslot_getfile_cnt_1 <= target_dataslot_getfile;
    if (ds_cmd_pulse_74a) begin
        brg_wcnt_cram0 <= 15'd0;
        brg_wcnt_sdram <= 15'd0;
        brg_cram0_drop_cpu_owned <= 1'b0;
        brg_cram0_drop_overrun   <= 1'b0;
    end else begin
        if (bridge_cram0_wr_pulse && brg_wcnt_cram0 != 15'h7FFF)
            brg_wcnt_cram0 <= brg_wcnt_cram0 + 15'd1;
        // SDRAM-leg counter removed with bridge_to_sdram; brg_wcnt_sdram stays 0.
        // F6 sticky: a CRAM0-range bridge write while cram0_mode_74a=1
        // (CPU owns the mux) is dropped by the bridge_wr_accept gate.
        if (bridge_cram0_wr_pulse && cram0_mode_74a)
            brg_cram0_drop_cpu_owned <= 1'b1;
        if (bridge_wr_overrun_pulse)
            brg_cram0_drop_overrun <= 1'b1;
    end
end

// Read by axi_periph_slave (clk_cpu) through a plain 2FF-per-bit sync:
// quasi-static use only — firmware samples it AFTER DS_STATUS reports
// DONE + WR_IDLE, when the counters have stopped moving, so per-bit
// synchronizer skew cannot tear a live value.
wire [31:0] bridge_dbg_wcnt_74a = {brg_cram0_drop_overrun,   brg_wcnt_sdram,
                                   brg_cram0_drop_cpu_owned, brg_wcnt_cram0};

    reg     [9:0]   datatable_addr;
    wire    [31:0]  datatable_q;
    reg             datatable_wren;
    reg     [31:0]  datatable_data;

// Nonvolatile size updates share port A of the APF datatable BRAM.
// APF automatic-load writes already hit the datatable directly through
// port B, so only CPU-side SAVE_DT_SIZE commits need to be serialized here.
// Keep this event-driven; a mirrored save_sizes[0:9] register table costs a
// large mux/register bank in ALMs.
reg        dt_update_pending = 1'b0;
reg [9:0]  dt_update_addr = 10'd0;
reg [31:0] dt_update_data = 32'd0;

localparam [9:0] PRESAVE_SIZE_WORD = 10'd17;
localparam [9:0] SAVE0_SIZE_WORD   = 10'd19;
localparam [3:0] PRESAVE_DT_SLOT   = 4'hF;

wire bridge_datatable_write =
    bridge_wr && (bridge_addr[31:24] == 8'hF8) && (bridge_addr[15:12] == 4'h2);

// Entry 8 is one pre-save nonvolatile slot (SDK Shared Config or Duke
// Settings). Saves are entries 9..18. Each entry is two datatable words:
// file id, size. Pre-save size is word 17; save0 is word 19; save9 is word 37.

// CPU write: individual slot update via periph slave CDC
wire [3:0]  cpu_save_dt_slot;
wire [31:0] cpu_save_dt_size;
wire        cpu_save_dt_commit;
// Entry-resolved commit address (SAVE_DT_WORD, HW_FEATURES bit 24): the OS
// scans the datatable for the entry whose id word matches the data slot and
// supplies entry*2+1 directly, because the legacy fixed mapping below is
// only valid for layouts where every declared slot loaded a file (the
// Pocket compacts the table to loaded slots — Diablo's optional slots shift
// every save entry, landing legacy commits on the WRONG entry: ini flushed
// at the save archive's size, saves never created).  word_mode arms on a
// SAVE_DT_WORD write and clears on a SAVE_DT_SLOT write, so an old os.bin
// (which never writes 0xC8) drives the legacy path bit-identically.
wire [9:0]  cpu_save_dt_word;
wire        cpu_save_dt_word_mode;

// CDC: synchronize the commit toggle from clk_ram_controller to clk_74a.
// A single-cycle pulse can be missed between 100 MHz and 74.25 MHz; the
// toggle makes each SAVE_DT_SIZE write phase-independent.
reg [2:0] save_dt_commit_sync = 3'b000;
always @(posedge clk_74a)
    save_dt_commit_sync <= {save_dt_commit_sync[1:0], cpu_save_dt_commit};

wire save_dt_commit_event = save_dt_commit_sync[1] ^ save_dt_commit_sync[2];

// Latch slot/size/word in clk_ram_controller domain (stable when commit
// arrives — the OS writes the selector registers strictly before the
// SAVE_DT_SIZE commit write, so all of these are settled long before the
// commit toggle crosses).
reg [3:0]  save_dt_slot_latch;
reg [31:0] save_dt_size_latch;
reg [9:0]  save_dt_word_latch;
reg        save_dt_word_mode_latch;
always @(posedge clk_ram_controller) begin
    save_dt_slot_latch <= cpu_save_dt_slot;
    save_dt_size_latch <= cpu_save_dt_size;
    save_dt_word_latch <= cpu_save_dt_word;
    save_dt_word_mode_latch <= cpu_save_dt_word_mode;
end

// CDC the latched values (they're stable for many cycles before commit rises)
reg [3:0]  save_dt_slot_sync;
reg [31:0] save_dt_size_sync;
reg [9:0]  save_dt_word_sync;
reg        save_dt_word_mode_sync;
always @(posedge clk_74a) begin
    save_dt_slot_sync <= save_dt_slot_latch;
    save_dt_size_sync <= save_dt_size_latch;
    save_dt_word_sync <= save_dt_word_latch;
    save_dt_word_mode_sync <= save_dt_word_mode_latch;
end

// Word mode: the OS supplied the exact datatable word address (entry*2+1,
// resolved by id scan) — no range gate beyond the 10-bit address itself;
// the legacy mapping keeps its original slot validity check unchanged.
wire save_dt_commit_valid =
    save_dt_commit_event &&
    (save_dt_word_mode_sync
        || (save_dt_slot_sync == PRESAVE_DT_SLOT)
        || (save_dt_slot_sync < 4'd10));
wire [9:0] save_dt_commit_addr =
    save_dt_word_mode_sync
        ? save_dt_word_sync
        : (save_dt_slot_sync == PRESAVE_DT_SLOT)
            ? PRESAVE_SIZE_WORD
            : SAVE0_SIZE_WORD + {5'd0, save_dt_slot_sync, 1'b0};

// Datatable slot size query: CDC from CPU (clk_ram_controller) → clk_74a → back
wire [9:0]  cpu_dt_query_addr;
wire        cpu_dt_query_toggle;
wire [31:0] cpu_dt_query_data;
wire        cpu_dt_query_valid;

// CDC: toggle-based request from CPU to clk_74a
reg [2:0] dt_toggle_sync = 3'b000;
always @(posedge clk_74a)
    dt_toggle_sync <= {dt_toggle_sync[1:0], cpu_dt_query_toggle};
wire dt_req_rise = dt_toggle_sync[1] ^ dt_toggle_sync[2];  // any change = new request

// Latch query address (stable before req rises)
reg [9:0] dt_addr_latch;
always @(posedge clk_ram_controller)
    dt_addr_latch <= cpu_dt_query_addr;
reg [9:0] dt_addr_sync;
always @(posedge clk_74a)
    dt_addr_sync <= dt_addr_latch;

// Query state machine: pause cycling FSM, read datatable, resume
reg        dt_reading = 0;
reg [31:0] dt_result = 0;
reg        dt_result_toggle = 1;  // init opposite of dt_query_toggle (0) so valid starts low
reg [1:0]  dt_read_cnt = 0;

// CDC: result + toggle back to CPU domain
reg [31:0] dt_result_sync1, dt_result_sync2;
reg        dt_rtoggle_sync1, dt_rtoggle_sync2;
always @(posedge clk_ram_controller) begin
    dt_result_sync1 <= dt_result;
    dt_result_sync2 <= dt_result_sync1;
    dt_rtoggle_sync1 <= dt_result_toggle;
    dt_rtoggle_sync2 <= dt_rtoggle_sync1;
end
assign cpu_dt_query_data = dt_result_sync2;
assign cpu_dt_query_valid = (dt_rtoggle_sync2 == cpu_dt_query_toggle);

// Datatable port-A arbiter: CPU reads have priority, then APF port-B writes
// hold this side idle, then queued CPU size updates are written.
always @(posedge clk_74a) begin
    datatable_wren <= 0;

    if (dt_req_rise) begin
        // CPU requested a datatable read.
        dt_reading <= 1;
        dt_read_cnt <= 0;
        datatable_addr <= dt_addr_sync;
    end else if (dt_reading) begin
        dt_read_cnt <= dt_read_cnt + 1;
        if (dt_read_cnt == 2'd1) begin
            // BRAM output valid after 1 cycle — capture result + toggle
            dt_result <= datatable_q;
            dt_result_toggle <= dt_toggle_sync[2];
            dt_reading <= 0;
        end
    end else if (bridge_datatable_write) begin
        // Avoid racing APF's bridge-port datatable writes at startup/load.
    end else if (dt_update_pending) begin
        datatable_wren <= 1;
        datatable_addr <= dt_update_addr;
        datatable_data <= dt_update_data;
        dt_update_pending <= 0;
    end

    if (save_dt_commit_valid) begin
        dt_update_pending <= 1;
        dt_update_addr <= save_dt_commit_addr;
        dt_update_data <= save_dt_size_sync;
    end
end

// Shutdown handshake CDC
wire shutdown_pending_74a;
wire shutdown_ack_cpu;
wire shutdown_pending_cpu;
wire shutdown_ack_74a;

synch_3 sync_shutdown_pending(shutdown_pending_74a, shutdown_pending_cpu, clk_ram_controller);

// Wait for the CPU to acknowledge shutdown so firmware can hand CRAM0
// back to the bridge before Pocket nonvolatile save writeback.  The
// bridge command FSM still has its own timeout fallback if the CPU is
// wedged and never acknowledges.
wire shutdown_ack_combined = shutdown_ack_cpu;
synch_3 sync_shutdown_ack(shutdown_ack_combined, shutdown_ack_74a, clk_74a);

core_bridge_cmd icb (

    .clk                ( clk_74a ),
    .reset_n            ( reset_n_apf ),
    .shutdown_pending   ( shutdown_pending_74a ),
    .shutdown_ack_s     ( shutdown_ack_74a ),

    .bridge_endian_little   ( bridge_endian_little ),
    .bridge_addr            ( bridge_addr ),
    .bridge_rd              ( bridge_rd ),
    .bridge_rd_data         ( cmd_bridge_rd_data ),
    .bridge_wr              ( bridge_wr ),
    .bridge_wr_data         ( bridge_wr_data ),

    .status_boot_done       ( status_boot_done ),
    .status_setup_done      ( status_setup_done ),
    .status_running         ( status_running ),

    .dataslot_requestread       ( dataslot_requestread ),
    .dataslot_requestread_id    ( dataslot_requestread_id ),
    .dataslot_requestread_ack   ( dataslot_requestread_ack ),
    .dataslot_requestread_ok    ( dataslot_requestread_ok ),

    .dataslot_requestwrite      ( dataslot_requestwrite ),
    .dataslot_requestwrite_id   ( dataslot_requestwrite_id ),
    .dataslot_requestwrite_size ( dataslot_requestwrite_size ),
    .dataslot_requestwrite_ack  ( dataslot_requestwrite_ack ),
    .dataslot_requestwrite_ok   ( dataslot_requestwrite_ok ),

    .dataslot_update            ( dataslot_update ),
    .dataslot_update_id         ( dataslot_update_id ),
    .dataslot_update_size       ( dataslot_update_size ),

    .dataslot_allcomplete   ( dataslot_allcomplete ),

    .rtc_epoch_seconds      ( rtc_epoch_seconds ),
    .rtc_date_bcd           ( rtc_date_bcd ),
    .rtc_time_bcd           ( rtc_time_bcd ),
    .rtc_valid              ( rtc_valid ),

    .savestate_supported    ( savestate_supported ),
    .savestate_addr         ( savestate_addr ),
    .savestate_size         ( savestate_size ),
    .savestate_maxloadsize  ( savestate_maxloadsize ),

    .savestate_start        ( savestate_start ),
    .savestate_start_ack    ( savestate_start_ack ),
    .savestate_start_busy   ( savestate_start_busy ),
    .savestate_start_ok     ( savestate_start_ok ),
    .savestate_start_err    ( savestate_start_err ),

    .savestate_load         ( savestate_load ),
    .savestate_load_ack     ( savestate_load_ack ),
    .savestate_load_busy    ( savestate_load_busy ),
    .savestate_load_ok      ( savestate_load_ok ),
    .savestate_load_err     ( savestate_load_err ),

    .osnotify_inmenu        ( osnotify_inmenu ),

    .target_dataslot_read       ( target_dataslot_read ),
    .target_dataslot_write      ( target_dataslot_write ),
    .target_dataslot_getfile    ( target_dataslot_getfile ),
    .target_dataslot_openfile   ( target_dataslot_openfile ),

    .target_dataslot_ack        ( target_dataslot_ack ),
    .target_dataslot_done       ( target_dataslot_done ),
    .target_dataslot_err        ( target_dataslot_err ),

    .target_dataslot_id         ( target_dataslot_id ),
    .target_dataslot_slotoffset ( target_dataslot_slotoffset ),
    .target_dataslot_bridgeaddr ( target_dataslot_bridgeaddr ),
    .target_dataslot_length     ( target_dataslot_length ),

    .target_buffer_param_struct ( target_buffer_param_struct ),
    .target_buffer_resp_struct  ( target_buffer_resp_struct ),

    .datatable_addr         ( datatable_addr ),
    .datatable_wren         ( datatable_wren ),
    .datatable_data         ( datatable_data ),
    .datatable_q            ( datatable_q )

);



////////////////////////////////////////////////////////////////////////////////////////



// video generation
assign video_rgb_clock = clk_vid;
assign video_rgb_clock_90 = clk_vid_90deg;
assign video_rgb = video_rgb_core;
assign video_de = vidout_de;
assign video_skip = vidout_skip;
assign video_vs = vidout_vs;
assign video_hs = vidout_hs;

    reg [9:0]   x_count;
    reg [9:0]   y_count;

    reg [23:0]  vidout_rgb;
    reg         vidout_de;
    reg         vidout_skip;
    reg         vidout_vs;
    reg         vidout_hs;
    wire        early_vblank_safe_vid;

    // Terminal moved to software — no hardware VRAM interface

    // Display mode and framebuffer address from CPU
    // display_mode removed — terminal rendering in software
    wire [2:0] color_mode;
    wire [24:0] fb_display_addr;
    wire [9:0] fb_width;
    wire [9:0] fb_height;
    wire [15:0] fb_stride;
    // Independent analog-output framebuffer (Pocket LCD = Terminal). Base
    // follows the app's current display buffer; geometry is held separately so
    // the analog keeps showing the app while the LCD shows the terminal.
    wire [24:0] analog_src_fb_addr;
    wire [2:0]  analog_src_color_mode;
    wire [9:0]  analog_src_fb_width;
    wire [9:0]  analog_src_fb_height;
    wire [15:0] analog_src_fb_stride;
    wire [2:0] video_scaler_slot_cpu;

    // VRR: firmware-written V_TOTAL
    wire [9:0] vrr_v_total_cpu;
    // CDC vrr_v_total (clk_cpu → clk_vid) with toggle handshake
    // Prevents bit-tearing on multi-bit bus crossing
    reg        vrr_toggle_cpu;       // toggles in clk_cpu on each write
    reg [9:0]  vrr_vt_hold;         // stable copy latched in clk_cpu
    reg [2:0]  vrr_toggle_sync;     // 3-stage synchronizer in clk_vid
    reg [9:0]  vrr_vt_sync2;        // output in clk_vid domain
    reg [2:0]  video_scaler_slot_s1;
    reg [2:0]  video_scaler_slot_vid;

    always @(posedge clk_cpu or negedge reset_n_cpu_core) begin
        if (~reset_n_cpu_core) begin
            vrr_toggle_cpu <= 1'b0;
            vrr_vt_hold <= CRT_V_TOTAL_DEFAULT;
        end else if (vrr_vt_hold != vrr_v_total_cpu) begin
            vrr_vt_hold <= vrr_v_total_cpu;
            vrr_toggle_cpu <= ~vrr_toggle_cpu;
        end
    end

    always @(posedge clk_vid or negedge reset_n_vid) begin
        if (~reset_n_vid) begin
            vrr_toggle_sync <= 3'b0;
            vrr_vt_sync2 <= CRT_V_TOTAL_DEFAULT;
            video_scaler_slot_s1 <= 3'd0;
            video_scaler_slot_vid <= 3'd0;
        end else begin
            vrr_toggle_sync <= {vrr_toggle_sync[1:0], vrr_toggle_cpu};
            if (vrr_toggle_sync[2] != vrr_toggle_sync[1])
                vrr_vt_sync2 <= vrr_vt_hold;  // stable — toggle edge means new value ready
            video_scaler_slot_s1 <= video_scaler_slot_cpu;
            video_scaler_slot_vid <= video_scaler_slot_s1;
        end
    end

    // Pocket scaler slots advertised in dist/core/video.json. Slot 0 is the
    // standard 320x240 output used for boot/terminal/fallback.
    localparam CRT_V_SYNC   = 3;
    localparam CRT_V_BPORCH = 15;
    localparam CRT_V_FPORCH = 4;
    localparam CRT_H_SYNC   = 58;
    localparam CRT_H_BPORCH = 62;
    localparam CRT_H_FPORCH = 20;
    localparam CRT_H_TOTAL  = CRT_H_SYNC + CRT_H_BPORCH + 640 + CRT_H_FPORCH;
    localparam [9:0] CRT_V_ACTIVE_BASE = CRT_V_SYNC + CRT_V_BPORCH;
    localparam [9:0] CRT_V_CENTER_HEIGHT = 10'd240;
    localparam CRT_V_TOTAL_DEFAULT = 10'd525; // ~60.0 Hz at 24.576 MHz (old CRT_V_ACTIVE_BASE+240+CRT_V_FPORCH=262 was ~60 Hz at 12.288, = ~120 Hz at 24.576). Default sets frame LENGTH only; active region is crt_v_active_start/end.

    function [9:0] scaler_slot_width;
        input [2:0] slot;
        begin
            case (slot)
                3'd0: scaler_slot_width = 10'd320;
                3'd1: scaler_slot_width = 10'd320;
                3'd2: scaler_slot_width = 10'd320;
                3'd3: scaler_slot_width = 10'd320;
                3'd4: scaler_slot_width = 10'd320;
                3'd5: scaler_slot_width = 10'd400;
                3'd6: scaler_slot_width = 10'd256;
                3'd7: scaler_slot_width = 10'd640;   /* 640x480 (full-res) */
                default: scaler_slot_width = 10'd320;
            endcase
        end
    endfunction

    function [9:0] scaler_slot_height;
        input [2:0] slot;
        begin
            case (slot)
                3'd0: scaler_slot_height = 10'd240;
                3'd1: scaler_slot_height = 10'd200;
                3'd2: scaler_slot_height = 10'd224;
                3'd3: scaler_slot_height = 10'd256;
                3'd4: scaler_slot_height = 10'd288;
                3'd5: scaler_slot_height = 10'd300;
                3'd6: scaler_slot_height = 10'd240;
                3'd7: scaler_slot_height = 10'd480;   /* 640x480 (full-res); V_TOTAL floors to 18+480=498 -> ~31.6 Hz at the 12.288 MHz video clock */
                default: scaler_slot_height = 10'd240;
            endcase
        end
    endfunction

    // RGBHV Analogizer output mirrors the 640x480 carrier, so force the raw
    // CRT stream full-size only for RGBHV modes. Native 15 kHz modes use the
    // dedicated scanout analog raster and leave the LCD path in its app mode.
    wire analogizer_full_raster_vid = analogizer_ena_vid && !analogizer_15khz_vid;
    wire [2:0] crt_scaler_slot_vid =
        analogizer_full_raster_vid ? 3'd7 : video_scaler_slot_vid;
    wire [9:0] crt_h_active =
        analogizer_full_raster_vid ? 10'd640 :
                                     scaler_slot_width(video_scaler_slot_vid);
    wire [9:0] crt_v_active =
        analogizer_full_raster_vid ? 10'd480 :
                                     scaler_slot_height(video_scaler_slot_vid);
    wire [9:0] crt_v_center_offset =
        (crt_v_active < CRT_V_CENTER_HEIGHT) ?
        ((CRT_V_CENTER_HEIGHT - crt_v_active) >> 1) : 10'd0;
    wire [9:0] crt_h_active_end =
        CRT_H_SYNC + CRT_H_BPORCH + crt_h_active;
    wire [9:0] crt_v_active_start =
        CRT_V_ACTIVE_BASE + crt_v_center_offset;
    wire [9:0] crt_v_active_end =
        crt_v_active_start + crt_v_active;
    wire [9:0] crt_v_total_min =
        crt_v_active_end;
    reg [9:0] crt_v_total;
    reg crt_hs, crt_vs, crt_de;
    reg crt_hblank, crt_vblank;

    // VRR: dynamic V_TOTAL from CPU register. Normal LCD timing can use
    // the experimental V_TOTAL=257 mode for ~61.30 Hz with the current
    // clock. Physical scaler modes with taller active regions clamp the
    // request up so active video is never truncated.
    wire [9:0] vrr_vt_clamped = (vrr_vt_sync2 == 10'd0)  ? CRT_V_TOTAL_DEFAULT :
                                (vrr_vt_sync2 < 10'd514) ? 10'd514 :
                                (vrr_vt_sync2 > 10'd750) ? 10'd750 :
                                                           vrr_vt_sync2;
    // 640x480 RGBHV (full-raster) feeds an external analog display, which wants
    // fixed in-spec timing, and the Pocket's own scaler only locks 47-61 Hz.
    // VRR's floor (514) at 640x480 = 24.576M/(780*514) = 61.3 Hz, just over the
    // ceiling -> intermittent black LCD. Pin full-raster to V_TOTAL=525 (60.0 Hz,
    // and 525 > active-end 498 so no truncation). VRR is unchanged for the
    // normal LCD slots and the 15 kHz/SCART path.
    wire [9:0] vrr_vt_safe =
        analogizer_full_raster_vid ? CRT_V_TOTAL_DEFAULT :
        (vrr_vt_clamped < crt_v_total_min) ? crt_v_total_min : vrr_vt_clamped;

    // VexiiRiscv CPU system — AXI4 bus routing
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
        // CRAM0 AXI4 master interface
        .m_cram0_arvalid(cpu_m_cram0_arvalid),
        .m_cram0_arready(cpu_m_cram0_arready),
        .m_cram0_araddr (cpu_m_cram0_araddr),
        .m_cram0_arlen  (cpu_m_cram0_arlen),
        .m_cram0_rvalid (cpu_m_cram0_rvalid),
        .m_cram0_rready (cpu_m_cram0_rready),
        .m_cram0_rdata  (cpu_m_cram0_rdata),
        .m_cram0_rresp  (cpu_m_cram0_rresp),
        .m_cram0_rlast  (cpu_m_cram0_rlast),
        .m_cram0_awvalid(cpu_m_cram0_awvalid),
        .m_cram0_awready(cpu_m_cram0_awready),
        .m_cram0_awaddr (cpu_m_cram0_awaddr),
        .m_cram0_awlen  (cpu_m_cram0_awlen),
        .m_cram0_wvalid (cpu_m_cram0_wvalid),
        .m_cram0_wready (cpu_m_cram0_wready),
        .m_cram0_wdata  (cpu_m_cram0_wdata),
        .m_cram0_wstrb  (cpu_m_cram0_wstrb),
        .m_cram0_wlast  (cpu_m_cram0_wlast),
        .m_cram0_bvalid (cpu_m_cram0_bvalid),
        .m_cram0_bresp  (cpu_m_cram0_bresp),
        // CRAM1 + SRAM target ports retired in v2 (chip gone, SRAM is
        // GPU-private).  cpu_system.v no longer exposes those ports.
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
        .int_m_external(int_m_external_sync),
        .int_m_timer(int_m_timer_sync)
    );

    // AXI4 peripheral slave
    //
    // Every feature param is set from its INCLUDE_* build macro via the
    // `ifdef INCLUDE_X 1 `else 0 `endif pattern, so each variant's caps bits
    // are a direct echo of its include-list (Makefile VARIANT_DEFS).  Each
    // INCLUDE_* MUST match the gpu's same-named param below.
    axi_periph_slave #(
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
        .INCLUDE_XFORM_RGB(`ifdef INCLUDE_XFORM 1 `else 0 `endif),
        // GPU texel*C+D combiner cap (bit 27, OF_HW_GPU_COMBINE), additive —
        // advertised only when the build lists INCLUDE_COMBINE (gated AND with
        // truecolor inside axi_periph_slave).
        .INCLUDE_COMBINE(`ifdef INCLUDE_COMBINE 1 `else 0 `endif),
        // Q29 caps bit 18: advertised only when the build keeps the module (os30 clears it).
        .INCLUDE_PARAM_SPAN_Q29(`ifdef INCLUDE_PARAM_SPAN_Q29 1 `else 0 `endif)
    ) periph (
        .clk(clk_cpu),
        .reset_n(reset_n_cpu_core),
        // AXI4 slave interface
        .s_axi_arvalid(cpu_local_bus_arvalid),
        .s_axi_arready(cpu_local_bus_arready),
        .s_axi_araddr(cpu_local_bus_araddr),
        .s_axi_arlen(cpu_local_bus_arlen),
        .s_axi_rvalid(cpu_local_bus_rvalid),
        .s_axi_rready(cpu_local_bus_rready),
        .s_axi_rdata(cpu_local_bus_rdata),
        .s_axi_rresp(cpu_local_bus_rresp),
        .s_axi_rlast(cpu_local_bus_rlast),
        .s_axi_awvalid(cpu_local_bus_awvalid),
        .s_axi_awready(cpu_local_bus_awready),
        .s_axi_awaddr(cpu_local_bus_awaddr),
        .s_axi_awlen(cpu_local_bus_awlen),
        .s_axi_awburst(cpu_local_bus_awburst),
        .s_axi_wvalid(cpu_local_bus_wvalid),
        .s_axi_wready(cpu_local_bus_wready),
        .s_axi_wdata(cpu_local_bus_wdata),
        .s_axi_wstrb(cpu_local_bus_wstrb),
        .s_axi_wlast(cpu_local_bus_wlast),
        .s_axi_bvalid(cpu_local_bus_bvalid),
        .s_axi_bready(1'b1),
        .s_axi_bresp(cpu_local_bus_bresp),
        // CDC inputs
        .dataslot_allcomplete(dataslot_allcomplete && bridge_wr_idle),
        .vsync(vidout_vs),
        .early_vblank(early_vblank_safe_vid),
        .os_inmenu(osnotify_inmenu),
        .cont1_key(p1_controls),
        .cont1_joy(p1_joypad),
        .cont1_trig(p1_trigger),
        .cont2_key(p2_controls),
        .cont2_joy(p2_joypad),
        .cont2_trig(p2_trigger),
`ifndef INCLUDE_4PLAYER
        // No 4-player (every Pocket variant): tie players 3-4 to 0 so the
        // periph's cont3/4 sync registers + input-scan/rdata mux arms
        // constant-fold away.  Players 1-2 (cont1/2) retained.
        .cont3_key(32'd0),
        .cont3_joy(32'd0),
        .cont3_trig(16'd0),
	    .cont4_key(32'd0),
	    .cont4_joy(32'd0),
	    .cont4_trig(16'd0),
`else
        .cont3_key(cont3_key),
        .cont3_joy(cont3_joy),
        .cont3_trig(cont3_trig),
	    .cont4_key(cont4_key),
	    .cont4_joy(cont4_joy),
	    .cont4_trig(cont4_trig),
`endif
        .rtc_epoch_seconds(rtc_epoch_seconds),
        .rtc_valid(rtc_valid),
        .bridge_wr_idle(bridge_wr_idle),
        // F2i diagnostic counters (clk_74a, quasi-static after DONE)
        .bridge_dbg_wcnt(bridge_dbg_wcnt_74a),
	    .target_dataslot_ack(target_dataslot_ack),
        .target_dataslot_done(target_dataslot_done_safe),
        .target_dataslot_err(target_dataslot_err),
        // HPS status block (REGION_HPS) — MiSTer-only, reads as zeros here
        .hps_status(32'd0),
        .hps_img_size(64'd0),
        .hps_boot_len(32'd0),
        .hps_img1_size(64'd0),
        .hps_img2_size(64'd0),
        // Terminal moved to software — no hardware VRAM
        // Display control
        // display_mode removed — terminal rendering in software
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
        // Target dataslot interface
        .target_dataslot_read(cpu_target_dataslot_read),
        .target_dataslot_write(cpu_target_dataslot_write),
        .target_dataslot_openfile(cpu_target_dataslot_openfile),
        .target_dataslot_getfile(cpu_target_dataslot_getfile),
        .target_dataslot_id(cpu_target_dataslot_id),
        .target_dataslot_slotoffset(cpu_target_dataslot_slotoffset),
        .target_dataslot_bridgeaddr(cpu_target_dataslot_bridgeaddr),
        .target_dataslot_length(cpu_target_dataslot_length),
        .target_buffer_param_struct(cpu_target_buffer_param_struct),
        .target_buffer_resp_struct(cpu_target_buffer_resp_struct),
        // Audio FIFO status (read-only; HW mixer drives the FIFO directly)
        .audio_fifo_level(audio_fifo_level),
        .audio_fifo_full(audio_fifo_full),
        // CPU PCM sample push (AUDIO_PCM_SAMPLE @ 0x4C + 0x04).  Always
        // present; consumed by audio_output only without INCLUDE_HW_MIXER (SW mixer).
        .audio_sample_wr        (periph_audio_sample_wr),
        .audio_sample_data      (periph_audio_sample_data),
        // Hardware mixer MMIO ↔ audio_mixer (flat addressing, 0x4B000000)
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
        // CRAM0 ownership mode (0 = bridge, 1 = CPU)
        .cram0_mode            (cram0_mode_cpu),
        // Link MMIO interface
        .link_reg_wr(link_reg_wr),
        .link_reg_rd(link_reg_rd),
        .link_reg_addr(link_reg_addr),
        .link_reg_wdata(link_reg_wdata),
        .link_reg_rdata(link_reg_rdata),
        // UART
        .uart_tx_dv(uart_tx_dv),
        .uart_tx_byte(uart_tx_byte),
        .uart_tx_active(uart_tx_active),
        .uart_rx_dv(uart_rx_dv),
        .uart_rx_byte(uart_rx_byte),
        .save_dt_slot(cpu_save_dt_slot),
        .save_dt_size(cpu_save_dt_size),
        .save_dt_word(cpu_save_dt_word),
        .save_dt_word_mode(cpu_save_dt_word_mode),
        .save_dt_commit(cpu_save_dt_commit),
        .app_id(app_id_sync2),
        .analogizer_settings(analogizer_settings_cpu),
        .analogizer_hoffset(signed_hoff_cpu),
        .analogizer_voffset(signed_voff_cpu),
        .analogizer_cpu_wr_toggle(analogizer_cpu_wr_toggle),
        .analogizer_cpu_wr_settings(analogizer_cpu_wr_settings),
        .analogizer_cpu_wr_hoffset(analogizer_cpu_wr_hoffset),
        .analogizer_cpu_wr_voffset(analogizer_cpu_wr_voffset),
        // Shutdown handshake
        .shutdown_pending(shutdown_pending_cpu),
        .shutdown_ack(shutdown_ack_cpu),
        .timer_irq(timer_irq),
        .uart_rx_irq(),
        .link_irq(link_irq),
        .ext_irq(ext_irq),
        // Datatable slot size query
        .dt_query_addr(cpu_dt_query_addr),
        .dt_query_toggle(cpu_dt_query_toggle),
        .dt_query_data(cpu_dt_query_data),
        .dt_query_valid(cpu_dt_query_valid),
        .vrr_v_total(vrr_v_total_cpu),
        .analogizer_enabled(adapter_fixed_rate_cpu),
        // SNAC shifter / GPIO
        .snac_pin_out(snac_pin_out),
        .snac_pin_dir(snac_pin_dir),
        .snac_pin_in(snac_pin_in),
        .snac_enable(snac_enable),
        // GPU register interface
        .gpu_reg_wr(gpu_reg_wr),
        .gpu_reg_addr(gpu_reg_addr),
        .gpu_reg_wdata(gpu_reg_wdata),
        .gpu_reg_rdata(gpu_reg_rdata),
        // CMD_FLIP side-port from gpu_core
        .gpu_swap_req(gpu_swap_req),
        .gpu_swap_idx(gpu_swap_idx),
        // CMD_FLIP backpressure → gpu_core stalls until vsync clears
        .fb_swap_pending_o(slave_swap_pending)
    );

    // DMA engine removed — apps use CPU memcpy instead

    // Slave → io_sdram pulse adapter
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

    // bridge_to_sdram (the direct SD->SDRAM bridge write master) REMOVED — it
    // corrupted the loaded image on silicon and the only SDRAM-dest path is now
    // the firmware CRAM0 bounce + CPU copy.  Arbiter M2 AW/W is tied idle below.

    // ============================================================
    // AXI4 SDRAM arbiter (GPU, CPU, APF bridge, Audio)
    // ============================================================
    // GPU exposes separate AR (gpu_rd_*) and AW/W (gpu_wr_*) channels
    // but they are never active simultaneously — the GPU FSM serialises
    // its own reads and writes.  We drop both into the arbiter's M0
    // master: only one channel fires at a time, the other is idle.
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
        .m1_arvalid(cpu_sdram_bus_arvalid), .m1_arready(cpu_sdram_bus_arready),
        .m1_araddr(cpu_sdram_bus_araddr),   .m1_arlen(cpu_sdram_bus_arlen),
        .m1_rvalid(cpu_sdram_bus_rvalid),   .m1_rdata(cpu_sdram_bus_rdata),
        .m1_rresp(cpu_sdram_bus_rresp),     .m1_rlast(cpu_sdram_bus_rlast),
        .m1_rready(cpu_sdram_bus_rready),
        .m1_awvalid(cpu_sdram_bus_awvalid), .m1_awready(cpu_sdram_bus_awready),
        .m1_awaddr(cpu_sdram_bus_awaddr),   .m1_awlen(cpu_sdram_bus_awlen),
        .m1_wvalid(cpu_sdram_bus_wvalid),   .m1_wready(cpu_sdram_bus_wready),
        .m1_wdata(cpu_sdram_bus_wdata),     .m1_wstrb(cpu_sdram_bus_wstrb),
        .m1_wlast(cpu_sdram_bus_wlast),
        .m1_bvalid(cpu_sdram_bus_bvalid),   .m1_bresp(cpu_sdram_bus_bresp),
        // M2: tied idle on Pocket — the APF bridge no longer writes SDRAM
        // through the arbiter (bridge_to_sdram removed; SDRAM-dest goes via the
        // firmware CRAM0 bounce).  M2 read channel is MiSTer-only (hps_bridge
        // sector writeback); the Pocket bridge never reads SDRAM through it.
        .m2_arvalid(1'b0), .m2_arready(),
        .m2_araddr(32'd0), .m2_arlen(8'd0),
        .m2_rvalid(), .m2_rdata(), .m2_rresp(), .m2_rlast(),
        .m2_rready(1'b1),
        .m2_awvalid(1'b0), .m2_awready(),
        .m2_awaddr(32'd0), .m2_awlen(8'd0),
        .m2_wvalid(1'b0),  .m2_wready(),
        .m2_wdata(32'd0),  .m2_wstrb(4'd0),
        .m2_wlast(1'b0),
        .m2_bvalid(),      .m2_bresp(),
        // M3: Audio Mixer (read-only, lowest priority) — per-voice
        // sample fetches from SDRAM.
        .m3_arvalid(mixer_arvalid),   .m3_arready(mixer_arready),
        .m3_araddr(mixer_araddr),     .m3_arlen(mixer_arlen),
        .m3_rvalid(mixer_rvalid),     .m3_rdata(mixer_rdata),
        .m3_rresp(mixer_rresp),       .m3_rlast(mixer_rlast),
        .m3_rready(mixer_rready),
        // Slave output (to axi_sdram_slave)
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

    // AXI4 SDRAM slave (must stay alive during reset for APF save flush & data load)
    // NOTE: the SDRAM slave sits behind an arbiter (arb_s_*) that fans in
    // the CPU master alongside the GPU / bridge masters, so its rready is
    // the arbiter-local signal, not cpu_m_sdram_rready directly. The
    // arbiter itself already provides back-pressure to each master; for
    // now leave this tied to 1'b1 (matches pre-burst behaviour).
    axi_sdram_slave #(
        // Native streaming write bursts are gated per-master by s_axi_wcont
        // (driven from the arbiter's s_wcont): the GPU's queue-fed framebuffer
        // bursts (continuous WVALID, underrun-immune) stream natively up to 8
        // beats.  With SERIALIZE_WRITE_BURSTS=0 and AWLEN<=15, a CPU cache-line
        // writeback (!wcont) takes the slave's buffered path (S_WR_FILL ->
        // single native io_sdram burst), not a serialized per-beat path.  MAX
        // cap stays at 15 (the io_sdram/AXI 4-bit ARLEN/AWLEN limit).
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

    // CRAM0 is now served by the cram0_cdc instance (declared at the
    // top of the file with the CRAM0 controller block).  No per-chip
    // AXI sub-slave is needed - the CDC *is* the CPU-side AXI slave.
    //
    // CRAM1 has no CPU AXI surface (it is GPU-private texture memory driven
    // by cram1_controller above).  SRAM is GPU-exclusive (Z-buffer/translucency
    // LUT) and has no CPU AXI surface either.

    // Terminal rendering moved to software (firmware renders to framebuffer)

    // Line start signal for video scanout
    reg line_start;
    always @(posedge clk_vid) begin
        line_start <= (x_count == 0);
    end

    // Video scanout from SDRAM framebuffer (8-bit indexed with hardware palette)
    wire [23:0] framebuffer_pixel_color;

    // Palette write signals from CPU
    wire        cpu_pal_wr;
    wire [7:0]  cpu_pal_addr;
    wire [23:0] cpu_pal_data;
    wire        cpu_pal_commit;
    wire        cpu_pal_busy;

    // SDRAM burst interface signals for video scanout
    wire        video_burst_rd;
    wire [24:0] video_burst_addr;
    wire [10:0] video_burst_len;
    wire        video_burst_32bit;
    wire [31:0] video_burst_data;
    wire        video_burst_data_valid;
    wire        video_burst_data_done;
    
    video_CRT_scanout_indexed_BRAM #(
        // No Analogizer ⇒ no consumer for the analog raster: prune the
        // dedicated 240p fetch, the analog leg of the shared output
        // pipeline and the 2048x32 analog line cache (8 M10Ks).  The LCD
        // fetch/decode path is identical in both configs.
`ifdef INCLUDE_ANALOGIZER
        .HAS_ANALOG_RASTER(1)
`else
        .HAS_ANALOG_RASTER(0)
`endif
    ) scanout (
        .clk_video(clk_vid),
        .reset_n(reset_n_vid),
        .x_count(x_count),
        .y_count(y_count),
        .line_start(line_start),
        .pixel_color(framebuffer_pixel_color),
        .clk_analog(clk_core_49152),
        .reset_analog_n(reset_n_analog),
        .analog_ce_pix(analog_ce_pix),
        .analog_scanlines(fx[1:0]),
        // Native 15 kHz Analogizer modes (RGBS/component/composite) get a
        // dedicated analog raster with its own framebuffer fetch — 240p by
        // default, menu-selectable 480i (NTSC) / 576i (PAL) interlace.
        // VGA/RGBHV and Analogizer-off use the 480p path slaved to the LCD
        // raster (analog mirrors vidout_rgb).  The dedicated fetch shares the
        // SDRAM burst port with the LCD fetch, but the LCD has strict priority
        // in the scanout FSM and the video burst outranks the CPU in io_sdram,
        // so it cannot be starved: tb_scanout proves the LCD still meets its
        // 1-line deadline with the 240p fetch interleaved even under ~2.7x
        // worst-case burst latency.  (The earlier black-LCD bug was a stale
        // bitstream, not this fetch.)
        .analog_timing(analog_timing_vid),
        .analog_pixel_clk(),
        .analog_pixel_color(analogizer_scan_rgb),
        .analog_hblank(analogizer_scan_hblank),
        .analog_vblank(analogizer_scan_vblank),
        .analog_hsync(analogizer_scan_hsync),
        .analog_vsync(analogizer_scan_vsync),
        .fb_base_addr(fb_display_addr),
        .color_mode(color_mode),
        .fb_width(fb_width),
        .fb_height(fb_height),
        .fb_stride(fb_stride),
        .out_width(crt_h_active),
        .out_height(crt_v_active),
        // Analog framebuffer params, driven from the dedicated analog-fb
        // register set in axi_periph_slave: base = the app's current display
        // buffer, geometry = the app's geometry held independently of the LCD.
        // In Pocket LCD = On/Off this equals the app the LCD shows; in Terminal
        // the LCD switches to the console while the analog keeps the app.
        .analog_fb_base_addr(analog_src_fb_addr),
        .analog_color_mode(analog_src_color_mode),
        .analog_fb_width(analog_src_fb_width),
        .analog_fb_height(analog_src_fb_height),
        .analog_fb_stride(analog_src_fb_stride),
        // Output geometry = the LCD output size (crt_h/v_active), NOT the app's
        // native FB size. The analog vertical scaler then upscales the native
        // app (e.g. 320x240) to fill the 480p frame, exactly like the LCD/vidout
        // path -- otherwise the app paints at native height into a 480-line
        // raster and the rest of the frame is black.
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

    assign early_vblank_safe_vid = (y_count < crt_v_active_start);

always @(posedge clk_vid or negedge reset_n_vid) begin

    if(~reset_n_vid) begin

        x_count <= 0;
        y_count <= 0;
        crt_v_total <= CRT_V_TOTAL_DEFAULT;

    end else begin
        vidout_de <= 0;
        vidout_skip <= 0;
        vidout_vs <= 0;
        vidout_hs <= 0;

        // x and y counters
        // Refresh the target at line boundaries so a frame that becomes ready
        // during blanking can end in the current frame instead of waiting one
        // more full refresh.  The >= compare handles late shorter requests.
        if(x_count == 0)
            crt_v_total <= vrr_vt_safe;
        x_count <= x_count + 1'b1;
        if(x_count == CRT_H_TOTAL-1) begin
            x_count <= 0;

            y_count <= y_count + 1'b1;
            if(y_count >= crt_v_total - 1) begin
                y_count <= 0;
                crt_v_total <= vrr_vt_safe;
            end
        end

        // CRT Blank
        crt_hblank <= x_count < (CRT_H_SYNC + CRT_H_BPORCH) ||
                      (x_count >= crt_h_active_end);
        crt_vblank <= y_count < crt_v_active_start ||
                      (y_count >= crt_v_active_end);

        // Generate CRT sync
        // --- Generación de Syncs (Lógica Negativa) ---

        crt_hs <= (x_count >= 0) && (x_count < CRT_H_SYNC);
        crt_vs <= (y_count >= 0) && (y_count < CRT_V_SYNC);


        // Generate Pocket sync
        if(x_count == 0 && y_count == 0) begin
            // sync signal in back porch
            // new frame
            vidout_vs <= 1;
        end

        // we want HS to occur a bit after VS, not on the same cycle
        if(x_count == 3) begin
            // sync signal in back porch
            // new line
            vidout_hs <= 1;
        end

        // APF blanking RGB — template-exact shape (Dock fix, iteration 2).
        // The contract (buscomm): "RGB pixel value should remain all zero
        // when DE is deasserted unless using the frame feature bits or
        // end-of-line bits."  Iteration 1 held the slot command across all
        // of blanking and zeroed only the exact VS cycle (the feature-word
        // parse point); the Dock still rejected — if its feature-word
        // sampler is registered (reads RGB a cycle after it sees VS), the
        // held slot word still lands in feature bits [23:4]
        // reserved-must-be-zero and poisons HDMI generation, while the LCD
        // tolerates it.  So: drive the safe value 24'h0 across ALL of
        // blanking (feature word = progressive, no rescan), and emit the
        // Set-Scaler-Slot end-of-line word ONLY on the official parse
        // cycle — exactly one cycle after DE falls (once per active line;
        // "last command in frame wins" keeps the selection stable).  This
        // is the official template shape, field-proven by third-party
        // multi-slot cores on the Dock.
        vidout_rgb <= 24'h0;
        if (vidout_de
            && !((x_count >= CRT_H_SYNC + CRT_H_BPORCH)
                 && (x_count < crt_h_active_end)
                 && (y_count >= crt_v_active_start)
                 && (y_count < crt_v_active_end)))
            // DE is high this output cycle and falls on the next one: the
            // next RGB word is the end-of-line slot command
            // (Parameter[2:0] = slot at bits [15:13], function 3'b000).
            vidout_rgb <= {8'b0, crt_scaler_slot_vid, 13'b0};

        // generate active video, now accounts for CRT specific timings but making compatible with Analogue Pocket video also
        if(x_count >= CRT_H_SYNC + CRT_H_BPORCH  && x_count < crt_h_active_end) begin

            if(y_count >= crt_v_active_start && y_count < crt_v_active_end) begin
                // data enable. this is the active region of the line
                vidout_de <= 1;

                // All display modes rendered to framebuffer by software
                vidout_rgb <= framebuffer_pixel_color;
            end
        end
    end
end

//
// Link MMIO peripheral
//
`ifdef INCLUDE_LINK
link_lite #(
    .CLK_HZ(100000000),
    .SCK_HZ(256000)
) link0 (
    .clk(clk_cpu),
    .reset_n(reset_n_cpu_media),
    .reg_wr(link_reg_wr),
    .reg_rd(link_reg_rd),
    .reg_addr(link_reg_addr),
    .reg_wdata(link_reg_wdata),
    .reg_rdata(link_reg_rdata),
    .link_si_i(link_si_i),
    .link_so_o(link_so_out),
    .link_so_oe(link_so_oe),
    .link_sck_i(link_sck_i),
    .link_sck_o(link_sck_out),
    .link_sck_oe(link_sck_oe),
    .link_sd_i(link_sd_i),
    .link_sd_o(link_sd_out),
    .link_sd_oe(link_sd_oe),
    .irq(link_irq)
);
`else
assign link_reg_rdata = 32'b0;
assign link_irq = 1'b0;
`endif

//
// Audio output (dcfifo + I2S)
//
// Producer is selected by INCLUDE_HW_MIXER: HW audio_mixer when present
// (OS25/MiSTer), else CPU SW-mixer PCM samples via AUDIO_PCM_SAMPLE (OS30).
// dcfifo bridges clk_cpu → clk_audio (12.288 MHz).
//
wire        timer_irq;
wire        link_irq;
wire        ext_irq;  // Masked combination from axi_periph_slave

// ────────────────────────────────────────────────────────────────────
// IRQ synchronizers — the stock VexiiRiscv PrivilegedPlugin latches
// int_m_timer / int_m_external directly into mip.mtip / mip.meip with
// NO internal synchronizer (see TilelinkVexiiRiscvFiber.bind(ctrl): in
// fiber-land the binding goes through InterruptNode which inserts a
// proper buffer, but we drive the raw Verilog ports).  Our ext_irq is
// a combinational OR of several sources in axi_periph_slave, which can
// glitch when irq_mask writes transition or when source edges land
// near a clock.  A glitch caught by the meip flop turns into a
// spurious external interrupt that corrupts the interrupted instruction
// mid-commit (observed as t0/a1 carrying sp's value and faulting at
// PMA boundaries like 0x8000 / 0x14000000).  Two-flop chain filters
// combinational glitches and breaks the peripheral→CPU timing path.
reg [1:0] ext_irq_sync;
reg [1:0] timer_irq_sync;
always @(posedge clk_cpu) begin
    ext_irq_sync   <= {ext_irq_sync[0],   ext_irq};
    timer_irq_sync <= {timer_irq_sync[0], timer_irq};
end
wire int_m_external_sync = ext_irq_sync[1];
wire int_m_timer_sync    = timer_irq_sync[1];

// ─── HW audio mixer ────────────────────────────────────────────────
// Per-voice sample fetch from SDRAM via M3 on the arbiter; writes
// stereo pairs directly into the audio_output dcfifo.  (audio_dma
// retired in v2 — see the audio_dma retirement note below.)
wire        mixer_arvalid;
wire        mixer_arready;
wire [31:0] mixer_araddr;
wire [7:0]  mixer_arlen;
wire        mixer_rvalid;
wire [31:0] mixer_rdata;
wire [1:0]  mixer_rresp;
wire        mixer_rlast;
wire        mixer_rready;
`ifdef INCLUDE_HW_MIXER
// HW-mixer sample stream into audio_output (absent without INCLUDE_HW_MIXER,
// where audio_output is fed by the periph PCM path instead).
wire        mixer_sample_wr;
wire [31:0] mixer_sample_data;
`endif
wire [31:0] mixer_active_mask;
wire [21:0] mixer_pos_readback;
wire [31:0] mixer_voice_end_pending;

/* 1-cycle register stage between audio_mixer status outputs and
 * axi_periph_slave read mux.  The sysreg read mux in the periph slave
 * is 128 entries wide and already fans in from GPU, timer, controllers
 * and many other IPs; without a boundary register, Quartus was forced
 * to cluster audio_mixer and axi_periph_slave in the same LAB region,
 * which made the FPU critical path in VexiiRiscv worse because the
 * CPU had to be squeezed in too.  Firmware MMIO reads already incur
 * multi-cycle AXI handshake latency, so +1 cycle here is invisible. */
/* pos_readback is NOT boundary-registered: audio_mixer registers its
 * MLAB read internally, and the periph FSM's S_PERIPH_RD_LATCH budget
 * is exactly two cycles after req_addr — one register stage on the
 * select-dependent path, no more. */
reg [31:0] mixer_active_mask_r;
reg [31:0] mixer_voice_end_pending_r;
always @(posedge clk_cpu) begin
    mixer_active_mask_r       <= mixer_active_mask;
    mixer_voice_end_pending_r <= mixer_voice_end_pending;
end

// MMIO-driven voice programming signals (from axi_periph_slave).
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

// CPU PCM sample push (AUDIO_PCM_SAMPLE @ 0x4C + 0x04) from axi_periph_slave.
// Always wired (the periph port is unconditional); only consumed by
// audio_output without INCLUDE_HW_MIXER, where the SW mixer on the CPU
// produces finished stereo samples in place of the HW mixer.
wire        periph_audio_sample_wr;
wire [31:0] periph_audio_sample_data;

`ifndef INCLUDE_HW_MIXER
// ─── HW mixer cut (OS30) ───────────────────────────────────────────
// The HW audio_mixer congested the device (~99% → WNS -1.9).  The CPU
// runs a software mixer against the same MMIO contract and pushes
// finished stereo samples through AUDIO_PCM_SAMPLE.  audio_mixer is not
// instantiated; its periph-facing readbacks and its M3 arbiter port are
// tied off so nothing is left undriven / multidriven.
assign mixer_pos_readback      = 22'd0;
assign mixer_voice_end_pending = 32'd0;
assign mixer_active_mask       = 32'd0;
// M3 arbiter port (read-only mixer master) parked idle.
assign mixer_arvalid           = 1'b0;
assign mixer_araddr            = 32'd0;
assign mixer_arlen             = 8'd0;
assign mixer_rready            = 1'b0;
`else
audio_mixer audio_mixer_inst (
    .clk              (clk_cpu),
    .reset_n          (reset_n_cpu_media),
    .mixer_enable     (mixer_enable_mmio),
    .voice_wr         (mixer_voice_wr_mmio),
    .voice_field      (mixer_voice_field_mmio),
    .voice_sel        (mixer_voice_sel_mmio),
    .voice_sel_rd     (mixer_voice_sel_rd_mmio), // combinational from read addr
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
`endif

// cram1_burst_mmio retired with CRAM1 chip; 0x4E000000 MMIO slot is
// repurposed in v2 as the CRAM0 ownership mode bit (see axi_periph_slave).

// v2 audio path: HW mixer is the sole producer into audio_output.
// audio_dma retired (v1 pre-mixed-ring DMA) and the AUDIO_SAMPLE
// direct-MMIO path retired — both fully deleted from the netlist.
// The previous three-way mux had `audio_sample_data` as its default
// branch, so a stale register latched a constant DC pair into the
// FIFO whenever the mixer wasn't writing (observed: 00b1/0020 stuck
// through an entire run).
audio_output audio_out (
    .clk_sys      (clk_cpu),
    .clk_audio    (clk_core_12288),
    .reset_n      (reset_n_cpu_media),

`ifndef INCLUDE_HW_MIXER
    // SW mixer: CPU pushes finished stereo samples via AUDIO_PCM_SAMPLE.
    .sample_wr    (periph_audio_sample_wr),
    .sample_data  (periph_audio_sample_data),
`else
    // HW mixer is the sole producer into audio_output.
    .sample_wr    (mixer_sample_wr),
    .sample_data  (mixer_sample_data),
`endif
    .fifo_level   (audio_fifo_level),
    .fifo_full    (audio_fifo_full),

    .audio_mclk   (audio_mclk),
    .audio_lrck   (audio_lrck),
    .audio_dac    (audio_dac)
);

// ============================================================
// GPU — 3D Span Rasteriser
// ============================================================
wire        gpu_reg_wr;
wire [3:0]  gpu_reg_addr;
wire [31:0] gpu_reg_wdata;
wire [31:0] gpu_reg_rdata;


// GPU AXI4 read master (M0 on SDRAM arbiter)
wire        gpu_rd_arvalid;
wire        gpu_rd_arready;
wire [31:0] gpu_rd_araddr;
wire [7:0]  gpu_rd_arlen;
wire        gpu_rd_rvalid;
wire [31:0] gpu_rd_rdata;
wire        gpu_rd_rlast;

// GPU AXI4 write channel (GPU M0 on SDRAM arbiter)
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

// GPU SRAM interface: private scratch tables (currently translucency LUT).

// GPU enable (from MMIO GPU_CTRL bit 0, directly in gpu_core)
wire        gpu_busy;
wire [31:0] gpu_fence_reached;
wire        gpu_swap_req;
wire [1:0]  gpu_swap_idx;
wire        slave_swap_pending;  // from axi_periph_slave → stalls gpu_core CMD_FLIP

`ifndef EXCLUDE_GPU
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
    // xform + 0x50/0x51 matrix-MAC + 0x53/0x54 vtx-cache + 0x55/0x57 lighting +
    // 0x4F clip — is one INCLUDE_XFORM.  Off on every shipping variant (CPU
    // geometry); os25 thus sheds its previously-dead 0x50/0x51/0x4F cones.
    .INCLUDE_XFORM_RGB(`ifdef INCLUDE_XFORM 1 `else 0 `endif),
    .INCLUDE_CLIP_TRI(`ifdef INCLUDE_XFORM 1 `else 0 `endif),
    .INCLUDE_VTX_CACHE(`ifdef INCLUDE_XFORM 1 `else 0 `endif),
    .INCLUDE_GPU_LIGHT(`ifdef INCLUDE_XFORM 1 `else 0 `endif),
    .INCLUDE_GPU_XFORM_MAC(`ifdef INCLUDE_XFORM 1 `else 0 `endif),
    // Palette / colormap lane (additive): os25/mister list INCLUDE_PALETTE; os30 omits it.
    .INCLUDE_PALETTE(`ifdef INCLUDE_PALETTE 1 `else 0 `endif),
    // Combiner texel*C+D (additive): os30 lists INCLUDE_COMBINE (Mario head).
    .INCLUDE_COMBINE(`ifdef INCLUDE_COMBINE 1 `else 0 `endif),
    // Q29 param-span dynamic-scale precision (additive): os25/mister keep it
    // (Quake perspective); os30 omits it so the Q29 z-step cone + perspective
    // branches fold — removes the GPU's #1 critical path + frees ~150-220 ALM.
    .INCLUDE_PARAM_SPAN_Q29(`ifdef INCLUDE_PARAM_SPAN_Q29 1 `else 0 `endif),
    // Numeric tuning (not feature gates).  The z read window is 4 on the
    // triangle-heavy variants and degenerates to 1 on OS25 (single-word z fill,
    // sweeps the window logic).  Keyed off INCLUDE_Z_BURST (NOT INCLUDE_TEX_MEM)
    // so OS30/SM64 keeps the 16-byte z-line burst even with the fast-tex chip
    // disabled — SM64 z-tests every fragment.
    // EW_PARALLEL_DIVS: OS30/SM64 uses 1 — three slope dividers run concurrently
    // (~28 beats vs ~87 serial).  The serial walker was the per-triangle setup
    // long pole (vs the ~61-cycle derive), so parallelising it cuts triangle
    // setup ~30%.  Bit-identical quotients (the gpu-acceptance default is 1).
    // OS25 stays 0 (single shared divider — ALM-constrained for its feature set).
    .GPU_Z_READ_WINDOW(`ifdef INCLUDE_Z_BURST 4 `else 1 `endif),
    // Additive: only a build that lists INCLUDE_PARALLEL_DIVS gets the concurrent
    // slope dividers.  Neither pocket variant lists it today (both = 0), which is
    // bit-identical to the old EXCLUDE_PARALLEL_DIVS/DIRECT_COLOR derivation and
    // removes that load-bearing ifdef ordering.
    .GPU_EW_PARALLEL_DIVS(`ifdef INCLUDE_PARALLEL_DIVS 1 `else 0 `endif)
) gpu (
    .clk(clk_cpu),
    .reset_n(reset_n_cpu_media),
    .gpu_enable(1'b1),
    // AXI4 read master (ring fetch + texture)
    .m_rd_arvalid(gpu_rd_arvalid),
    .m_rd_arready(gpu_rd_arready),
    .m_rd_araddr(gpu_rd_araddr),
    .m_rd_arlen(gpu_rd_arlen),
    .m_rd_rvalid(gpu_rd_rvalid),
    .m_rd_rdata(gpu_rd_rdata),
    .m_rd_rlast(gpu_rd_rlast),
    // AXI4 read master — fast texture memory fills (redirected tex cache).
    // Pocket routes these to the CRAM1 controller/adapter (the INCLUDE_TEX_MEM
    // block above); the gpu_tex_* local wires are the Pocket-side mapping.
    .gpu_tex_mem_arvalid(gpu_tex_arvalid),
    .gpu_tex_mem_arready(gpu_tex_arready),
    .gpu_tex_mem_araddr(gpu_tex_araddr),
    .gpu_tex_mem_arlen(gpu_tex_arlen),
    .gpu_tex_mem_rvalid(gpu_tex_rvalid),
    .gpu_tex_mem_rdata(gpu_tex_rdata),
    .gpu_tex_mem_rlast(gpu_tex_rlast),
    // Fast texture upload (MMIO regs -> cram1_controller word_wr)
    .gpu_tex_mem_up_wr(cram1_up_wr),
    .gpu_tex_mem_up_addr(cram1_up_addr),
    .gpu_tex_mem_up_wdata(cram1_up_wdata),
    .gpu_tex_mem_up_busy(cram1_up_busy),
    // AXI4 write master (FB writes + clear)
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
    // MMIO registers
    .reg_wr(gpu_reg_wr),
    .reg_addr(gpu_reg_addr),
    .reg_wdata(gpu_reg_wdata),
    .reg_rdata(gpu_reg_rdata),
    // CMD_FLIP side-port → axi_periph_slave's swap mux
    .gpu_swap_req(gpu_swap_req),
    .gpu_swap_idx(gpu_swap_idx),
    .slave_swap_pending(slave_swap_pending),
    // Status
    .busy(gpu_busy),
    .fence_reached(gpu_fence_reached)
);
`else
assign gpu_rd_arvalid = 1'b0;
assign gpu_rd_araddr  = 32'b0;
assign gpu_rd_arlen   = 8'b0;
assign gpu_tex_arvalid = 1'b0;
assign gpu_tex_araddr  = 32'b0;
assign gpu_tex_arlen   = 8'b0;
assign cram1_up_wr     = 1'b0;
assign cram1_up_addr   = 22'b0;
assign cram1_up_wdata  = 32'b0;
assign gpu_wr_awvalid = 1'b0;
assign gpu_wr_awaddr  = 32'b0;
assign gpu_wr_awlen   = 8'b0;
assign gpu_wr_wvalid  = 1'b0;
assign gpu_wr_wdata   = 32'b0;
assign gpu_wr_wstrb   = 4'b0;
assign gpu_wr_wlast   = 1'b0;
assign gpu_busy       = 1'b0;
assign gpu_fence_reached = 32'b0;
assign gpu_reg_rdata   = 32'b0;
assign gpu_swap_req   = 1'b0;
assign gpu_swap_idx   = 2'b0;
assign sram_word_rd    = 1'b0;
assign sram_word_wr    = 1'b0;
assign sram_word_rd_half = 1'b0;
assign sram_word_rd_hi = 1'b0;
assign sram_word_addr  = 22'b0;
assign sram_word_wdata = 32'b0;
assign sram_word_wstrb = 4'b0;
`endif


///////////////////////////////////////////////


    wire    clk_core_12288;         // 12.288 MHz — audio (256*48kHz) ONLY
    wire    clk_core_49152;
    wire    clk_core_24576;         // 24.576 MHz — Pocket video pixel clock
    wire    clk_core_24576_90deg;   // 24.576 MHz 90° — Pocket video DDR
    wire    clk_vid;                // 24.576 MHz — ~60.0 Hz at V_TOTAL=525
    wire    clk_vid_90deg;
    wire    clk_cpu;
    wire    clk_ram_controller;
    wire    clk_ram_chip;

    wire    pll_core_locked;
    wire    pll_ram_locked;

assign clk_vid = clk_core_24576;
assign clk_vid_90deg = clk_core_24576_90deg;

mf_pllbase mp1 (
    .refclk         ( clk_74a ),
    .rst            ( 0 ),

    .outclk_0       ( clk_core_12288 ),
    .outclk_1       ( ),

    .outclk_2       ( clk_core_49152),
    .outclk_3       ( clk_core_24576 ),
    .outclk_4       ( clk_core_24576_90deg ),

    .locked         ( pll_core_locked )
);

mf_pllram_133 mp_ram (
    .refclk         ( clk_74a ),
    .rst            ( 0 ),
    .outclk_0       ( clk_ram_controller ),
    .outclk_1       ( clk_ram_chip ),
    .outclk_2       ( ),
    .locked         ( pll_ram_locked )
);

assign clk_cpu = clk_ram_controller;

// cram0_clk: chip currently runs in async page mode (BCR_VALUE = 16'h9D1F
// above, burst_rd tied 1'b0 at the controller instance) with the clock pin
// free-running on clk_74a.  (F5, 2026-06: tying the pin LOW for strict async
// operation was tried then reverted — tie-low was suspected in a field
// regression.)  If sync-burst reads ever return, a synchronous BCR mode needs
// a real CK (restore the generated clock + IO delays in core_constraints.sdc).
assign cram0_clk = clk_74a;  // F5 revert (A/B): restore legacy free-running clock

// SDRAM controller
io_sdram #(
    // Open-page tracking layout (see io_sdram.v).  Per-bank open_row[0:3]
    // tracking (4 open rows) lets the FB and z-buffer keep separate open rows
    // when they live in different SDRAM banks, killing the per-pixel
    // FB-write/Z-write row-thrash turnaround.  Enabled on OS30 either via fast
    // texture (INCLUDE_TEX_MEM, e.g. Quake2) OR explicitly via
    // INCLUDE_BANK_ROW_TRACK (SM64, which has TEX_MEM off but still benefits
    // from the FB/Z bank split — the firmware places z in a different bank).
    // OS25 (2.5D span games) degenerates to the single open_bank/open_row
    // — same protocol, lower row-hit rate, sheds the per-bank entries and
    // bank-indexed muxes for device budget.  MiSTer keeps the default (1).
`ifdef INCLUDE_TEX_MEM
    .BANK_ROW_TRACK(1)
`elsif INCLUDE_BANK_ROW_TRACK
    .BANK_ROW_TRACK(1)
`else
    .BANK_ROW_TRACK(0)
`endif
) isr0 (
    .controller_clk ( clk_ram_controller ),
    .chip_clk       ( clk_ram_chip ),
    .clk_90         ( clk_ram_chip ),
    .reset_n        ( 1'b1 ),

    .phy_cke        ( dram_cke ),
    .phy_clk        ( dram_clk ),
    .phy_cas        ( dram_cas_n ),
    .phy_ras        ( dram_ras_n ),
    .phy_we         ( dram_we_n ),
    .phy_ba         ( dram_ba ),
    .phy_a          ( dram_a ),
    .phy_dq         ( dram_dq ),
    .phy_dqm        ( dram_dqm ),

    // Burst interface - video scanout
    .burst_rd           ( video_burst_rd ),
    .burst_addr         ( video_burst_addr ),
    .burst_len          ( video_burst_len ),
    .burst_32bit        ( video_burst_32bit ),
    .burst_data         ( video_burst_data ),
    .burst_data_valid   ( video_burst_data_valid ),
    .burst_data_done    ( video_burst_data_done ),

    // Burst write interface - not used
    .burstwr        ( 1'b0 ),
    .burstwr_addr   ( 25'b0 ),
    .burstwr_ready  ( ),
    .burstwr_strobe ( 1'b0 ),
    .burstwr_data   ( 16'b0 ),
    .burstwr_done   ( 1'b0 ),
    .dbg_io         ( ),

    // Word interface - CPU access via AXI
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
    .word_wr_data_next ( ram1_word_wr_data_next ),
    .word_wr_done ( ram1_word_wr_done ),
    .burst_wr_direct_data ( sdram_slave_next_wdata ),
    .burst_wr_direct_strb ( sdram_slave_next_wstrb )
);



endmodule

module bridge_cram0_write_fifo #(
    parameter WIDTH = 54,
    parameter DEPTH = 1024,
    parameter ADDR_WIDTH = 10
) (
    input  wire                   clk,
    input  wire                   reset,
    input  wire                   push,
    input  wire [WIDTH-1:0]       din,
    input  wire                   pop,
    output wire [WIDTH-1:0]       dout,
    output wire                   empty,
    output wire                   full,
    output wire [ADDR_WIDTH:0]    count
);
    reg [WIDTH-1:0] mem [0:DEPTH-1];
    reg [ADDR_WIDTH-1:0] rd_ptr = {ADDR_WIDTH{1'b0}};
    reg [ADDR_WIDTH-1:0] wr_ptr = {ADDR_WIDTH{1'b0}};
    reg [ADDR_WIDTH:0] used = {(ADDR_WIDTH+1){1'b0}};
    localparam [ADDR_WIDTH:0] DEPTH_COUNT = DEPTH;

    assign empty = (used == {(ADDR_WIDTH+1){1'b0}});
    assign full = (used == DEPTH_COUNT);
    assign count = used;
    assign dout = mem[rd_ptr];

    wire pop_ok = pop && !empty;
    wire push_ok = push && (!full || pop_ok);

    always @(posedge clk) begin
        if (reset) begin
            rd_ptr <= {ADDR_WIDTH{1'b0}};
            wr_ptr <= {ADDR_WIDTH{1'b0}};
            used <= {(ADDR_WIDTH+1){1'b0}};
        end else begin
            if (push_ok) begin
                mem[wr_ptr] <= din;
                wr_ptr <= wr_ptr + {{(ADDR_WIDTH-1){1'b0}}, 1'b1};
            end
            if (pop_ok)
                rd_ptr <= rd_ptr + {{(ADDR_WIDTH-1){1'b0}}, 1'b1};

            case ({push_ok, pop_ok})
            2'b10: used <= used + {{ADDR_WIDTH{1'b0}}, 1'b1};
            2'b01: used <= used - {{ADDR_WIDTH{1'b0}}, 1'b1};
            default: used <= used;
            endcase
        end
    end
endmodule
