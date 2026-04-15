//
// User core top-level (openfpgaOS)
//
// VexiiRiscv CPU with AXI4 bus architecture, 48-voice PCM mixer.
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
reg [31:0] analogizer_settings;

// App ID from instance JSON memory_writes (bridge 0xF7000010)
reg [31:0] app_id_74a;
reg [31:0] app_id_sync1, app_id_sync2;
always @(posedge clk_cpu) begin
    app_id_sync1 <= app_id_74a;
    app_id_sync2 <= app_id_sync1;
end

reg       analogizer_ena;
reg [3:0] analogizer_video_type;
reg [4:0] snac_cont_type /* synthesis keep */;
reg [3:0] snac_cont_assignment /* synthesis keep */;

always @(*) begin
  snac_cont_type        = analogizer_settings[4:0];
  snac_cont_assignment  = analogizer_settings[9:6];
  analogizer_video_type = analogizer_settings[13:10];
  analogizer_ena        = analogizer_settings[15];
end 



    wire pocket_blank_screen = analogizer_settings[13] && analogizer_ena;

    wire [23:0] video_rgb_core;
    assign video_rgb_core = (pocket_blank_screen) ? 24'h000000: vidout_rgb;

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

    reg [2:0] fx /* synthesis preserve */;
    always @(posedge clk_core_49152) begin
        case (analogizer_video_type)
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

localparam [39:0] NTSC_PHASE_INC = 40'd80073066196;  //print(round(3.579545 * 2**40 / 49.152)) 
localparam [39:0] PAL_PHASE_INC =  40'd99178372574; //print(round(4.43361875 * 2**40 / 49.152)) 

assign CHROMA_PHASE_INC = ((analogizer_video_type == 4'h4)|| (analogizer_video_type == 4'hC)) ? PAL_PHASE_INC : NTSC_PHASE_INC; 
assign PALFLAG = (analogizer_video_type == 4'h4) || (analogizer_video_type == 4'hC); 


// H/V offset
reg [31:0] signed_hoff;
reg [31:0] signed_voff;

wire [5:0]	hoffset = signed_hoff[5:0];
wire  [4:0]	voffset = signed_voff[4:0];
wire video_ce_pix, half_ce_pix;

jtframe_frac_cen #(.W(2)) pixel_cen
(
    .clk(clk_core_49152),
    .n(10'd1),
    .m(10'd4),
    .cen({video_ce_pix,half_ce_pix})
);
wire HSync,VSync;
jtframe_resync jtframe_resync
(
    .clk(clk_core_49152),
    .pxl_cen(video_ce_pix),
    .hs_in(crt_hs),
    .vs_in(crt_vs),
    .LVBL(crt_vblank),
    .LHBL(crt_hblank),
    .hoffset(hoffset), //5bits signed
    .voffset(voffset), //5bits signed
    .hs_out(HSync),
    .vs_out(VSync)
);

wire crt_csync;
wire crt_blankn;

assign crt_csync = ~(HSync ^ VSync);
assign crt_blankn   = ~(crt_hblank | crt_vblank);

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

// Cart pin assignments: SNAC vs UART mode
// bank0[7:4]: SNAC GPIO outputs or UART TX (replicated)
assign cart_tran_bank0     = snac_enable ?
    {snac_pin_out[5], snac_pin_out[4], snac_pin_out[3], snac_pin_out[2]} :
    {uart_tx_serial, uart_tx_serial, uart_tx_serial, uart_tx_serial};
assign cart_tran_bank0_dir = snac_enable ?
    (snac_pin_dir[5] | snac_pin_dir[4] | snac_pin_dir[3] | snac_pin_dir[2]) :
    1'b1;  // UART TX is always output

// pin31: SNAC GPIO or UART RX (high-Z input)
assign cart_tran_pin31     = snac_enable ?
    (snac_pin_dir[7] ? snac_pin_out[7] : 1'bZ) :
    1'bZ;
assign cart_tran_pin31_dir = snac_enable ? snac_pin_dir[7] : 1'b0;

// pin30: always SNAC (not shared with UART)
assign cart_tran_pin30     = snac_pin_dir[6] ? snac_pin_out[6] : 1'bZ;
assign cart_tran_pin30_dir = snac_enable ? snac_pin_dir[6] : 1'b0;
assign cart_pin30_pwroff_reset = snac_enable ? 1'b1 : 1'b0;

// UART RX gating: when SNAC active, feed idle-high to UART RX to prevent garbage
wire uart_rx_serial = snac_enable ? 1'b1 : cart_tran_pin31;

// Analogizer: video output only — SNAC pins now driven by CPU through above mux
openFPGA_Pocket_Analogizer #(
    .MASTER_CLK_FREQ(49_152_000),
    .LINE_LENGTH(640)
) analogizer (
    .i_clk(clk_core_49152),
    .i_rst(~reset_n),
    .i_ena(analogizer_ena),
    // Video interface
    .video_clk(clk_vid),
    .analog_video_type(analogizer_video_type),
    .R(vidout_rgb[23:16]),
    .G(vidout_rgb[15:8]),
    .B(vidout_rgb[7:0]),
    .Hblank(crt_hblank),
    .Vblank(crt_vblank),
    .BLANKn(crt_blankn),
    .Hsync(HSync),
    .Vsync(VSync),
    .Csync(crt_csync),
    .CHROMA_PHASE_INC(CHROMA_PHASE_INC),
    .PALFLAG(PALFLAG),
    .ce_pix(1'b1),
    .scandoubler(1'b1),
    .fx(fx),
    // SNAC cart pin pass-through from CPU GPIO
    .snac_bank0_out({snac_pin_out[5], snac_pin_out[4], snac_pin_out[3], snac_pin_out[2]}),
    .snac_bank0_dir(snac_pin_dir[5] | snac_pin_dir[4] | snac_pin_dir[3] | snac_pin_dir[2]),
    .snac_bank1_76_out({snac_pin_out[1], snac_pin_out[0]}),
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
    .cart_tran_bank1_dir(cart_tran_bank1_dir),
    // Debug
    .DBG_TX(),
    .o_stb()
);

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
// CRAM0 BCR sanity reset
// ============================================================
// Both CRAM0 dies (CE0 and CE1) get the AS1C8M16PL POR-default
// BCR (0x9D1F = async page mode, fixed-latency code 3, WAIT
// active high, drive strength 1/2, no-wrap continuous burst)
// written via CRE on every boot.
//
// Why this exists: BCR state lives on the chip, NOT in the FPGA.
// FPGA reconfig (e.g., quartus_pgm reload) does NOT reset the
// chip's BCR. If a previous bitstream ever wrote BCR to a
// different mode (e.g., sync burst at 0x641F) and then a
// subsequent bitstream that expects async mode is loaded, the
// async-mode controller can't talk to a sync-mode chip and the
// CPU's first instruction fetch from CRAM0 wedges silently. The
// only way out without this defensive write is a true Pocket
// power-cycle (Vcc removed). 2026-04-08 spent ~6 hours debugging
// exactly this failure mode after an earlier sync-burst experiment
// left the chip in 0x641F mode.
//
// The FSM runs once after PLL lock and gates bcr_init_done (which
// in turn gates the CPU reset deassertion at line 1222) until both
// dies have been written. ~20 cycles total at 100 MHz = 200 ns of
// boot delay. Negligible.
//
// CE# safety: the FSM never asserts a second config_en pulse until
// it has observed cpu_psram_raw_busy fall back to 0, which means
// the driver has reached STATE_CONFIG_HOLD_END (lines 446-447 of
// cram0_phy.sv) and BOTH CE0# and CE1# are deasserted again.
// The driver itself never asserts both CE# in the same state — see
// STATE_CONFIG_CRE_SETUP, lines 421-424. Therefore the die 0 → die 1
// handoff cannot create bus contention.
// ============================================================
wire        cpu_psram_raw_busy;
reg [2:0]   bcr_init_state;
reg         bcr_init_config_en;
reg         bcr_init_bank_sel;
reg         bcr_init_done;

localparam [2:0] BCR_ST_WAIT_PLL    = 3'd0;
localparam [2:0] BCR_ST_PULSE_DIE0  = 3'd1;
localparam [2:0] BCR_ST_BUSY_DIE0   = 3'd2;
localparam [2:0] BCR_ST_IDLE_DIE0   = 3'd3;
localparam [2:0] BCR_ST_PULSE_DIE1  = 3'd4;
localparam [2:0] BCR_ST_BUSY_DIE1   = 3'd5;
localparam [2:0] BCR_ST_IDLE_DIE1   = 3'd6;
localparam [2:0] BCR_ST_DONE        = 3'd7;

// AS1C8M16PL POR-default BCR value:
//   bit 15    = 1   async page mode (NOT sync burst)
//   bit 14    = 0   fixed initial latency
//   bits 13-11= 011 latency code 3 (4 clocks)
//   bit 10    = 1   WAIT active high (matches cram0_phy expectations)
//   bit 9     = 1   reserved-as-1
//   bit 8     = 0   WAIT asserted during delay
//   bit 7     = 1   reserved-as-1
//   bit 6     = 0   reserved
//   bits 5-4  = 01  drive strength 1/2
//   bit 3     = 1   no-wrap burst (don't care in async)
//   bits 2-0  = 111 continuous burst
localparam [15:0] BCR_VALUE = 16'h9D1F;  // async page mode

initial begin
    bcr_init_state     = BCR_ST_WAIT_PLL;
    bcr_init_config_en = 1'b0;
    bcr_init_bank_sel  = 1'b0;
    bcr_init_done      = 1'b0;
end

always @(posedge clk_ram_controller) begin
    bcr_init_config_en <= 1'b0;  // single-cycle pulse default
    case (bcr_init_state)
        BCR_ST_WAIT_PLL:
            if (pll_ram_locked)
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
                bcr_init_done  <= 1'b1;
                bcr_init_state <= BCR_ST_DONE;
            end

        BCR_ST_DONE:
            ; // sticky
    endcase
end

// ============================================================
// PSRAM Controller for CRAM0 (16MB, app cached data)
// ============================================================
// CRAM0: PocketDoom's proven controller — async two-phase, with
// the BCR sanity reset above ensuring the chip is in async mode
// regardless of what mode the previous bitstream left it in.
cram0_controller #(
    .CLOCK_SPEED(100.0)
) psram0 (
    .clk(clk_ram_controller),
    .reset_n(1'b1),
    .word_rd(c0_psram_rd),
    .word_wr(c0_psram_wr),
    .word_addr(c0_psram_addr[21:0]),
    .word_data(c0_psram_wdata),
    .word_wstrb(c0_psram_wstrb),
    .word_q(c0_psram_rdata),
    .word_busy(c0_psram_busy),
    .word_q_valid(c0_psram_rdata_valid),
    .cram_a(cram0_a),
    .cram_dq(cram0_dq),
    .cram_wait(cram0_wait),
    .cram_clk(),             // PLL drives cram0_clk directly
    .cram_adv_n(cram0_adv_n),
    .cram_cre(cram0_cre),
    .cram_ce0_n(cram0_ce0_n),
    .cram_ce1_n(cram0_ce1_n),
    .cram_oe_n(cram0_oe_n),
    .cram_we_n(cram0_we_n),
    .cram_ub_n(cram0_ub_n),
    .cram_lb_n(cram0_lb_n),
    // Sync burst read — dormant.  The burst FSM inside
    // cram0_controller is kept available for future re-enablement;
    // axi_cram0_slave currently has no burst path, so burst_rd stays
    // tied low here.  To re-enable: restore the burst ports on
    // axi_cram0_slave (see commit history) and drive burst_rd /
    // burst_len from the slave instead of these constants.
    .burst_rd(1'b0),
    .burst_len(6'd0),
    .burst_rdata_valid(),
    .burst_rdata(),
    // BCR config write (driven by the BCR_ST_* FSM above)
    .config_en(bcr_init_config_en),
    .config_data(BCR_VALUE),
    .config_bank_sel(bcr_init_bank_sel),
    .raw_busy(cpu_psram_raw_busy)
);

// ============================================================
// PSRAM Controller for CRAM1 (16MB, save data only — clk_74a domain)
// ============================================================
wire        psram1_rd;
wire        psram1_wr;
wire [21:0] psram1_addr;
wire [31:0] psram1_wdata;
wire [3:0]  psram1_wstrb;
wire [31:0] psram1_rdata;
wire        psram1_busy;
wire        psram1_rdata_valid;

// CRAM1 reset: sync pll_ram_locked into clk_cpu
reg [2:0] pll_ram_locked_cpu_sync;
always @(posedge clk_cpu)
    pll_ram_locked_cpu_sync <= {pll_ram_locked_cpu_sync[1:0], pll_ram_locked};
wire psram1_reset_n = pll_ram_locked_cpu_sync[2];

// ============================================================
// CRAM1: dual-controller pin mux.  No CDC on any data path.
//   Controller A (clk_cpu, 100 MHz): CPU + mixer (hot path)
//   Controller B (clk_74a, 74.25 MHz): bridge DMA (cold path)
// Pin mux selects B when bridge_cram1_active, A otherwise.
// One dead cycle on each transition (registered mux).
// ============================================================

// Sync bridge_active into clk_cpu so controller A knows to idle.
reg [2:0] brg_active_cpu_sync;
always @(posedge clk_cpu)
    brg_active_cpu_sync <= {brg_active_cpu_sync[1:0], bridge_cram1_active};
wire brg_active_cpu = brg_active_cpu_sync[2];

// Physical signals from each controller (active-low directly from cram1_controller)
wire [21:16] c1a_a, c1b_a;
wire [15:0]  c1a_dq_out, c1b_dq_out;
wire         c1a_dq_oe,  c1b_dq_oe;
wire         c1a_adv_n, c1b_adv_n;
wire         c1a_cre,   c1b_cre;
wire         c1a_ce0_n, c1b_ce0_n;
wire         c1a_ce1_n, c1b_ce1_n;
wire         c1a_oe_n,  c1b_oe_n;
wire         c1a_we_n,  c1b_we_n;
wire         c1a_ub_n,  c1b_ub_n;
wire         c1a_lb_n,  c1b_lb_n;

// BCR-init done flags from each controller instance.  Both controllers
// run the same BCR write sequence internally at reset; whichever holds
// the pin mux at the moment actually reaches the chip.  At reset the
// mux defaults to A, so A's BCR write is the one that sticks.  B's
// BCR state machine still progresses so its gating (bcr_init_done)
// releases at roughly the same time.
wire        psram1_a_bcr_done;
wire        psram1_b_bcr_done;

// CRAM1 burst is currently unused on both controllers — the bridge
// uses the simple word_rd FSM (bridge_cram1_rd_detect, below) and
// the CPU/mixer side never bursts.  The burst FSM inside
// cram1_controller is left in place for possible future use; both
// instantiations tie burst inputs to 0.

// Controller A: CPU + mixer (clk_cpu)
cram1_controller #(.CLOCK_SPEED(100)) psram1_a (
    .clk(clk_cpu), .reset_n(psram1_reset_n),
    .word_rd(psram1_rd),
    .word_wr(psram1_wr),
    .word_addr(psram1_addr), .word_data(psram1_wdata),
    .word_wstrb(psram1_wstrb),
    .word_q(psram1_rdata), .word_busy(psram1_busy),
    .word_q_valid(psram1_rdata_valid),
    .burst_rd(1'b0),
    .burst_addr(22'd0),
    .burst_len(5'd0),
    .burst_q(),
    .burst_q_valid(),
    .burst_busy(),
    .bcr_init_done(psram1_a_bcr_done),
    .cram_a(c1a_a), .cram_dq_out(c1a_dq_out), .cram_dq_oe(c1a_dq_oe),
    .cram_dq_in(cram1_dq), .cram_wait(cram1_wait), .cram_clk(),
    .cram_adv_n(c1a_adv_n), .cram_cre(c1a_cre),
    .cram_ce0_n(c1a_ce0_n), .cram_ce1_n(c1a_ce1_n),
    .cram_oe_n(c1a_oe_n), .cram_we_n(c1a_we_n),
    .cram_ub_n(c1a_ub_n), .cram_lb_n(c1a_lb_n)
);

// Controller B: bridge (clk_74a).  Burst inputs tied off — bridge
// reads use the simple bridge_cram1_rd_detect FSM below.
cram1_controller #(.CLOCK_SPEED(74.25)) psram1_b (
    .clk(clk_74a), .reset_n(psram1_reset_n),
    .word_rd(brg_psram_rd), .word_wr(brg_psram_wr),
    .word_addr(brg_psram_addr), .word_data(brg_psram_wdata),
    .word_wstrb(4'b1111),
    .word_q(brg_psram_rdata), .word_busy(brg_psram_word_busy),
    .word_q_valid(brg_psram_rdata_valid),
    .burst_rd(1'b0),
    .burst_addr(22'd0),
    .burst_len(5'd0),
    .burst_q(),
    .burst_q_valid(),
    .burst_busy(),
    .bcr_init_done(psram1_b_bcr_done),
    .cram_a(c1b_a), .cram_dq_out(c1b_dq_out), .cram_dq_oe(c1b_dq_oe),
    .cram_dq_in(cram1_dq), .cram_wait(cram1_wait), .cram_clk(),
    .cram_adv_n(c1b_adv_n), .cram_cre(c1b_cre),
    .cram_ce0_n(c1b_ce0_n), .cram_ce1_n(c1b_ce1_n),
    .cram_oe_n(c1b_oe_n), .cram_we_n(c1b_we_n),
    .cram_ub_n(c1b_ub_n), .cram_lb_n(c1b_lb_n)
);

// Pin mux with safe transitions: never switch while controller A is busy.
// Flipping mid-transaction would cut A's pins and leave the CRAM1 chip
// in an inconsistent state (half-completed write, stale data, etc.).
//   - A→B: wait for A to be idle (psram1_busy == 0) before switching
//   - B→A: bridge is on clk_74a; when brg_active_cpu drops, the bridge
//          FSM has already completed (pending flags cleared), safe.
reg cram1_pin_sel;  // 0=A (cpu/mixer), 1=B (bridge)
always @(posedge clk_cpu) begin
    if (!reset_n) begin
        cram1_pin_sel <= 1'b0;
    end else begin
        if (!cram1_pin_sel && brg_active_cpu && !psram1_busy)
            cram1_pin_sel <= 1'b1;
        else if (cram1_pin_sel && !brg_active_cpu)
            cram1_pin_sel <= 1'b0;
    end
end

assign cram1_a     = cram1_pin_sel ? c1b_a     : c1a_a;
assign cram1_dq    = (cram1_pin_sel ? c1b_dq_oe : c1a_dq_oe)
                   ? (cram1_pin_sel ? c1b_dq_out : c1a_dq_out)
                   : 16'hZZZZ;
assign cram1_adv_n = cram1_pin_sel ? c1b_adv_n : c1a_adv_n;
assign cram1_cre   = cram1_pin_sel ? c1b_cre   : c1a_cre;
assign cram1_ce0_n = cram1_pin_sel ? c1b_ce0_n : c1a_ce0_n;
assign cram1_ce1_n = cram1_pin_sel ? c1b_ce1_n : c1a_ce1_n;
assign cram1_oe_n  = cram1_pin_sel ? c1b_oe_n  : c1a_oe_n;
assign cram1_we_n  = cram1_pin_sel ? c1b_we_n  : c1a_we_n;
assign cram1_ub_n  = cram1_pin_sel ? c1b_ub_n  : c1a_ub_n;
assign cram1_lb_n  = cram1_pin_sel ? c1b_lb_n  : c1a_lb_n;

// SRAM controller (256 KB) - tristate handled at top level
wire [16:0] sram_a_w;
wire [15:0] sram_dq_out;
wire [15:0] sram_dq_in;
wire        sram_dq_oe;
wire        sram_oe_n_w, sram_we_n_w, sram_ub_n_w, sram_lb_n_w;

wire        sram_word_rd;
wire        sram_word_wr;
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

sram_controller #(
    .WAIT_CYCLES(6)
) sram0 (
    .clk(clk_ram_controller),
    .reset_n(1'b1),
    .word_rd(sram_word_rd),
    .word_wr(sram_word_wr),
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

// ============================================================
// UART (2 Mbaud, 8N1) — DevKey/Cartridge debug interface
// CLKS_PER_BIT = clk_cpu / 2_000_000.  clk_cpu is set in
// mf_pllram_133.v output_clock_frequency0; any change there MUST
// update this divisor, or the host will see a baud-rate mismatch
// and silently drop every byte.
//   100 MHz → 50  ← current
//    90 MHz → 45
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

// UART TX also on dbg_tx (direct 1.8V debug pin)
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
reg     [3:0]   ram1_word_burst_len;
reg     [3:0]   ram1_word_burst_wr_len;
wire            ram1_word_wr_data_next;
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
wire    [31:0]  sdram_slave_next_wdata;
wire    [3:0]   sdram_slave_next_wstrb;

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

// CPU AXI4 master → axi_psram_slave
// Per-chip AXI4 master wires between cpu_system and the three standalone
// sub-slaves (CRAM0, CRAM1, SRAM).  Concurrent fabrics — no shared bus.
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

wire        cpu_m_cram1_arvalid;
wire        cpu_m_cram1_arready;
wire [31:0] cpu_m_cram1_araddr;
wire [7:0]  cpu_m_cram1_arlen;
wire        cpu_m_cram1_rvalid;
wire        cpu_m_cram1_rready;
wire [31:0] cpu_m_cram1_rdata;
wire [1:0]  cpu_m_cram1_rresp;
wire        cpu_m_cram1_rlast;
wire        cpu_m_cram1_awvalid;
wire        cpu_m_cram1_awready;
wire [31:0] cpu_m_cram1_awaddr;
wire [7:0]  cpu_m_cram1_awlen;
wire        cpu_m_cram1_wvalid;
wire        cpu_m_cram1_wready;
wire [31:0] cpu_m_cram1_wdata;
wire [3:0]  cpu_m_cram1_wstrb;
wire        cpu_m_cram1_wlast;
wire        cpu_m_cram1_bvalid;
wire [1:0]  cpu_m_cram1_bresp;

// SRAM stub: responds to any CPU access with SLVERR (prevents AXI hang).
// Tracks outstanding AR/AW transactions and returns one response each.
wire cpu_m_sram_stub_arvalid;   // from cpu_system (unused output)
wire cpu_m_sram_stub_awvalid;
reg  cpu_m_sram_stub_rvalid;
reg  cpu_m_sram_stub_bvalid;
always @(posedge clk_cpu) begin
    if (!reset_n) begin
        cpu_m_sram_stub_rvalid <= 0;
        cpu_m_sram_stub_bvalid <= 0;
    end else begin
        cpu_m_sram_stub_rvalid <= cpu_m_sram_stub_arvalid;
        cpu_m_sram_stub_bvalid <= cpu_m_sram_stub_awvalid;
    end
end

// axi_psram_slave exposes per-chip backend wires — CRAM0, CRAM1, and
// SRAM are three independent physical backends (two separate PSRAM
// chips + on-chip M10K) so they can service transactions in parallel.
// No shared bus, no psram_target_sel latch.
wire        c0_psram_rd;
wire        c0_psram_wr;
wire [25:0] c0_psram_addr;
wire [31:0] c0_psram_wdata;
wire [3:0]  c0_psram_wstrb;
wire [31:0] c0_psram_rdata;
wire        c0_psram_busy;
wire        c0_psram_rdata_valid;

wire        c1_psram_rd;
wire        c1_psram_wr;
wire [25:0] c1_psram_addr;
wire [31:0] c1_psram_wdata;
wire [3:0]  c1_psram_wstrb;
wire [31:0] c1_psram_rdata;
wire        c1_psram_busy;
wire        c1_psram_rdata_valid;

// SRAM CPU path removed — GPU has exclusive direct access (Z-buffer).

// Muxed PSRAM signals (bridge or CPU)
// psram_mux_* removed — per-chip backends wire directly to each sub-slave

// Audio output interface
wire        audio_sample_wr;
wire [31:0] audio_sample_data;
wire [8:0]  audio_fifo_level;
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
wire        cpu_m_local_wvalid;
wire        cpu_m_local_wready;
wire [31:0] cpu_m_local_wdata;
wire [3:0]  cpu_m_local_wstrb;
wire        cpu_m_local_wlast;
wire        cpu_m_local_bvalid;
wire [1:0]  cpu_m_local_bresp;

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

// Bridge SDRAM path fully removed — all bridge DMA goes through CRAM1.

// ============================================================
// Bridge read data mux (registered — one cycle after bridge_rd)
// ============================================================
always @(posedge clk_74a) begin
    casex(bridge_addr)
        default: begin
            bridge_rd_data <= 0;
        end
        32'h30xxxxxx: begin
            // CRAM1 bridge read — single-word async path through
            // psram1_b.  cram1_rd_resp_data holds the most recently
            // completed bridge_rd response (one cycle late vs APF's
            // capture window — see comment in the FSM below).
            bridge_rd_data <= cram1_rd_resp_data;
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
always @(posedge clk_74a) begin
    if (bridge_wr) begin
        casex (bridge_addr)
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

        32'hF7000010: app_id_74a <= {
        bridge_wr_data[7:0],
        bridge_wr_data[15:8],
        bridge_wr_data[23:16],
        bridge_wr_data[31:24]};
        endcase
    end
end

// Bridge SDRAM write path removed — OS loads via CRAM1 bounce.
// bridge_wr_idle: true when no CRAM1 bridge op is in flight.
wire bridge_wr_idle = !bridge_cram1_active;

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

// Bridge SDRAM read path fully removed — bridge never reads SDRAM.

// ============================================================
// Bridge CRAM1: native clk_74a (no CDC)
//
// Two paths into psram1_b, both via the simple word interface:
//   - Writes: bridge_cram1_wr_detect → word_wr
//   - Reads:  bridge_cram1_rd_detect → word_rd → cram1_rd_resp_data
//
// The pin-level mux on the CRAM1 chip switches between psram1_a
// (CPU + mixer on clk_cpu) and psram1_b (bridge on clk_74a) when
// bridge_cram1_active rises (bridge has a read or write pending).
// ============================================================
wire bridge_cram1_wr_detect = bridge_wr && (bridge_addr[31:24] == 8'h30);

// Edge-detect the read so we accept exactly one read per bridge_rd
// pulse (APF can hold bridge_rd HIGH for several cycles).
reg prev_bridge_rd_for_cram1;
always @(posedge clk_74a)
    prev_bridge_rd_for_cram1 <= bridge_rd && (bridge_addr[31:24] == 8'h30);
wire bridge_cram1_rd_detect = !prev_bridge_rd_for_cram1 && bridge_rd && (bridge_addr[31:24] == 8'h30);

// Bridge command interface (clk_74a, directly to controller B).
reg        brg_psram_rd;
reg        brg_psram_wr;
reg [21:0] brg_psram_addr;
reg [31:0] brg_psram_wdata;
wire        brg_psram_word_busy;
wire [31:0] brg_psram_rdata;
wire        brg_psram_rdata_valid;
reg [31:0] cram1_rd_resp_data;

// brg_psram_busy mirrors the controller's word_busy.  No burst path
// exists on psram1_b in this revision, so we don't need to OR in a
// burst-busy term — leaving it word-only keeps the wr FSM clear of
// the bug where save_prefetch's burst_busy would pin this HIGH and
// stall every dataslot operation through the done-tracking quiet
// counter (bridge_wr_idle below feeds it).
wire        brg_psram_busy = brg_psram_word_busy;

// Bridge CRAM1 command FSM (clk_74a).
// Accept-then-issue pattern: latch addr/data immediately, defer the
// psram command pulse until controller B is idle.  This prevents the
// 1-cycle race where the detect fires while word_busy is dropping but
// the controller hasn't reached ST_IDLE yet.
//
// Reads and writes share the latched-addr register and are mutually
// exclusive — APF never interleaves them within a single dataslot
// transaction.
reg        bridge_cram1_wr_pending;
reg        bridge_cram1_wr_issued;   // command pulse sent to controller
reg        bridge_cram1_wr_started;  // controller went busy
reg        bridge_cram1_rd_pending;
reg        bridge_cram1_rd_issued;
reg [21:0] brg_lat_addr;
reg [31:0] brg_lat_data;

always @(posedge clk_74a) begin
    brg_psram_rd <= 0;
    brg_psram_wr <= 0;

    // Accept write — latch immediately, issue later when idle
    if (bridge_cram1_wr_detect && !bridge_cram1_wr_pending && !bridge_cram1_rd_pending) begin
        bridge_cram1_wr_pending <= 1;
        bridge_cram1_wr_issued  <= 0;
        bridge_cram1_wr_started <= 0;
        brg_lat_addr  <= bridge_addr[23:2];
        brg_lat_data  <= bridge_wr_data;
    end

    // Issue deferred write when controller is idle
    if (bridge_cram1_wr_pending && !bridge_cram1_wr_issued && !brg_psram_busy) begin
        brg_psram_wr   <= 1;
        brg_psram_addr <= brg_lat_addr;
        brg_psram_wdata <= brg_lat_data;
        bridge_cram1_wr_issued <= 1;
    end

    // Write completion: busy HIGH → LOW
    if (bridge_cram1_wr_pending && bridge_cram1_wr_issued) begin
        if (!bridge_cram1_wr_started && brg_psram_busy)
            bridge_cram1_wr_started <= 1;
        else if (bridge_cram1_wr_started && !brg_psram_busy) begin
            bridge_cram1_wr_pending <= 0;
            bridge_cram1_wr_issued  <= 0;
            bridge_cram1_wr_started <= 0;
        end
    end

    // Accept read — latch immediately, issue later when idle.  This
    // is the simple single-word path — APF's ~4-cycle bridge_rd
    // capture window vs ~25-30 cycle CRAM1 access means the first
    // read for any given address will return whatever was previously
    // latched in cram1_rd_resp_data.  Acceptable for app/file load
    // (host sees stale word, retries via the higher-level protocol);
    // not great for save flushing (would write garbage to SD).
    // save_prefetch existed to fix the save-flush case but had a
    // global side-effect; removed for now.
    if (bridge_cram1_rd_detect && !bridge_cram1_rd_pending && !bridge_cram1_wr_pending) begin
        bridge_cram1_rd_pending <= 1;
        bridge_cram1_rd_issued  <= 0;
        brg_lat_addr <= bridge_addr[23:2];
    end

    // Issue deferred read when controller is idle
    if (bridge_cram1_rd_pending && !bridge_cram1_rd_issued && !brg_psram_busy) begin
        brg_psram_rd   <= 1;
        brg_psram_addr <= brg_lat_addr;
        bridge_cram1_rd_issued <= 1;
    end

    // Read completion
    if (bridge_cram1_rd_pending && bridge_cram1_rd_issued && brg_psram_rdata_valid) begin
        cram1_rd_resp_data <= brg_psram_rdata;
        bridge_cram1_rd_pending <= 0;
        bridge_cram1_rd_issued  <= 0;
    end
end

wire bridge_cram1_active = bridge_cram1_wr_pending | bridge_cram1_rd_pending;

// ============================================================
// CRAM1 mux (clk_cpu) — CPU + mixer only, no bridge
// ============================================================

// Controller busy IS the inflight indicator — no separate tracker needed.
// Block new rd/wr commands whenever the controller is busy.  The controller's
// ST_IDLE only samples word_rd/word_wr; any pulse issued while busy would be
// dropped, causing phantom "inflight" state.
//
// Owner is captured when the rd pulse was issued.  rdata_valid later uses
// the captured owner to route the response to CPU or mixer.
reg psram1_rd_owner;   // 0=CPU, 1=mixer

// Mixer read acceptance — asserted on the cycle the mux actually passes
// the mixer's cram1_mix_rd pulse through to the controller.  Used by the
// mixer to know its read was not suppressed by a CPU collision.
wire cram1_mix_rd_accepted = psram1_rd & ~c1_psram_rd;

always @(posedge clk_cpu) begin
    if (psram1_rd)
        psram1_rd_owner <= c1_psram_rd ? 1'b0 : 1'b1;
end

wire psram1_rdata_valid_for_cpu   = psram1_rdata_valid && !psram1_rd_owner;
wire psram1_rdata_valid_for_mixer = psram1_rdata_valid &&  psram1_rd_owner;

// CPU AXI slave feedback — busy when controller busy OR bridge wants pins
// (gating new ops so controller A drains naturally for safe pin handover).
assign c1_psram_busy        = psram1_busy | brg_active_cpu;
assign c1_psram_rdata       = psram1_rdata;
assign c1_psram_rdata_valid = psram1_rdata_valid_for_cpu;

// Write: CPU only (mixer doesn't write).  Block when controller busy or
// when bridge is requesting the pins.
assign psram1_wr = psram1_busy     ? 1'b0
                 : brg_active_cpu  ? 1'b0
                 : c1_psram_wr;

// Read: block when controller busy, a write is being issued, or bridge
// wants the pins.  CPU > mixer priority.
assign psram1_rd = psram1_wr       ? 1'b0
                 : psram1_busy     ? 1'b0
                 : brg_active_cpu  ? 1'b0
                 : c1_psram_rd     ? 1'b1
                 : cram1_mix_rd;

assign psram1_addr  = (c1_psram_rd | c1_psram_wr) ? c1_psram_addr[21:0] : cram1_mix_addr;
assign psram1_wdata = c1_psram_wdata;
assign psram1_wstrb = c1_psram_wstrb;

// Bridge SDRAM read 4-stage sync removed — no bridge SDRAM reads.

// Bridge CRAM0 + SRAM write paths removed.
// OS now bounces all bridge DMA through CRAM1, then CPU copies to CRAM0.
// SRAM bridge was never used by firmware.
wire cram_bridge_active = 1'b0;

// SRAM: GPU-exclusive direct access (Z-buffer). No CPU path, no mux.
assign sram_word_rd    = gpu_sram_rd;
assign sram_word_wr    = gpu_sram_wr;
assign sram_word_addr  = gpu_sram_addr;
assign sram_word_wdata = gpu_sram_wdata;
assign sram_word_wstrb = gpu_sram_wstrb;



//
// host/target command handler
//
    wire            reset_n_apf;
    wire    [31:0]  cmd_bridge_rd_data;

    wire reset_n = reset_n_apf & bcr_init_done;

    // Synchronize reset deassertion to clk_vid domain
    reg [1:0] reset_vid_sync;
    wire reset_n_vid = reset_vid_sync[1];
    always @(posedge clk_vid or negedge reset_n) begin
        if (~reset_n)
            reset_vid_sync <= 2'b0;
        else
            reset_vid_sync <= {reset_vid_sync[0], 1'b1};
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

    reg     [9:0]   datatable_addr;
    wire    [31:0]  datatable_q;
    reg             datatable_wren;
    reg     [31:0]  datatable_data;

// Per-slot save sizes: 10-entry array, updated from two sources:
//   1. Chip32 via pmpw to bridge 0xF0000000 (sets default for all slots)
//   2. CPU via SAVE_DT_SLOT/SAVE_DT_SIZE registers (sets individual slots)
// The cycling FSM continuously writes these to the datatable.
reg [31:0] save_sizes [0:9];
integer si;
initial for (si = 0; si < 10; si = si + 1) save_sizes[si] = 32'h00000000;

// CPU write: individual slot update via periph slave CDC
wire [3:0]  cpu_save_dt_slot;
wire [31:0] cpu_save_dt_size;
wire        cpu_save_dt_commit;

// CDC: synchronize the commit pulse from clk_ram_controller to clk_74a
reg [2:0] save_dt_commit_sync;
always @(posedge clk_74a)
    save_dt_commit_sync <= {save_dt_commit_sync[1:0], cpu_save_dt_commit};

wire save_dt_commit_rise = save_dt_commit_sync[1] & ~save_dt_commit_sync[2];

// Latch slot/size in clk_ram_controller domain (stable when commit arrives)
reg [3:0]  save_dt_slot_latch;
reg [31:0] save_dt_size_latch;
always @(posedge clk_ram_controller) begin
    save_dt_slot_latch <= cpu_save_dt_slot;
    save_dt_size_latch <= cpu_save_dt_size;
end

// CDC the latched values (they're stable for many cycles before commit rises)
reg [3:0]  save_dt_slot_sync;
reg [31:0] save_dt_size_sync;
always @(posedge clk_74a) begin
    save_dt_slot_sync <= save_dt_slot_latch;
    save_dt_size_sync <= save_dt_size_latch;
end

// Single driver for save_sizes: Chip32 or CPU updates
//   Bridge 0xF0000000:        set ALL slots (bulk default)
//   Bridge 0xF0000010..0x34:  set individual slot (0xF0000010 + slot*4)
//   CPU SAVE_DT_SLOT/SIZE:    set individual slot (via CDC)
always @(posedge clk_74a) begin
    if (bridge_wr && bridge_addr == 32'hF0000000)
        for (si = 0; si < 10; si = si + 1)
            save_sizes[si] <= bridge_wr_data;
    else if (bridge_wr && bridge_addr[31:8] == 24'hF00000
             && bridge_addr[7:2] >= 6'd4 && bridge_addr[7:2] < 6'd14)
        save_sizes[bridge_addr[7:2] - 6'd4] <= bridge_wr_data;
    else if (save_dt_commit_rise && save_dt_slot_sync < 4'd10)
        save_sizes[save_dt_slot_sync] <= save_dt_size_sync;
end

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

// Cycling FSM: continuously write per-slot sizes to datatable,
// pausing briefly for CPU datatable reads.
reg [3:0] save_dt_idx;
always @(posedge clk_74a) begin
    if (dt_req_rise) begin
        // CPU requested a datatable read — pause cycling, set read address
        dt_reading <= 1;
        dt_read_cnt <= 0;
        datatable_wren <= 0;
        datatable_addr <= dt_addr_sync;
    end else if (dt_reading) begin
        datatable_wren <= 0;
        dt_read_cnt <= dt_read_cnt + 1;
        if (dt_read_cnt == 2'd1) begin
            // BRAM output valid after 1 cycle — capture result + toggle
            dt_result <= datatable_q;
            dt_result_toggle <= dt_toggle_sync[2];
            dt_reading <= 0;
        end
    end else begin
        // Normal cycling: write save sizes
        datatable_wren <= 1;
        datatable_addr <= 10'd15 + {6'd0, save_dt_idx[3:0]} * 10'd2;
        datatable_data <= save_sizes[save_dt_idx];
        save_dt_idx <= (save_dt_idx == 4'd9) ? 4'd0 : save_dt_idx + 4'd1;
    end
end

// Shutdown handshake CDC
wire shutdown_pending_74a;
wire shutdown_ack_cpu;
wire shutdown_pending_cpu;
wire shutdown_ack_74a;

synch_3 sync_shutdown_pending(shutdown_pending_74a, shutdown_pending_cpu, clk_ram_controller);

// Auto-ack shutdown: OR the CPU's ack with shutdown_pending itself.
// This gives the bridge immediate acknowledgment so it never times out
// and hard-resets the core. The CPU can still flush saves via the
// SYS_SHUTDOWN register, but the bridge won't wait for it.
wire shutdown_ack_combined = shutdown_ack_cpu | shutdown_pending_cpu;
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

    reg [15:0]  frame_count;

    reg [9:0]   x_count;
    reg [9:0]   y_count;

    reg [23:0]  vidout_rgb;
    reg         vidout_de, vidout_de_1;
    reg         vidout_skip;
    reg         vidout_vs;
    reg         vidout_hs, vidout_hs_1;

    // Terminal moved to software — no hardware VRAM interface

    // Display mode and framebuffer address from CPU
    // display_mode removed — terminal rendering in software
    wire [2:0] color_mode;
    wire [24:0] fb_display_addr;

    // VRR: firmware-written V_TOTAL
    wire [9:0] vrr_v_total_cpu;
    // CDC vrr_v_total (clk_cpu → clk_vid) with toggle handshake
    // Prevents bit-tearing on multi-bit bus crossing
    reg        vrr_toggle_cpu;       // toggles in clk_cpu on each write
    reg [9:0]  vrr_vt_hold;         // stable copy latched in clk_cpu
    reg [2:0]  vrr_toggle_sync;     // 3-stage synchronizer in clk_vid
    reg [9:0]  vrr_vt_sync2;        // output in clk_vid domain

    always @(posedge clk_cpu or negedge reset_n) begin
        if (~reset_n) begin
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
        end else begin
            vrr_toggle_sync <= {vrr_toggle_sync[1:0], vrr_toggle_cpu};
            if (vrr_toggle_sync[2] != vrr_toggle_sync[1])
                vrr_vt_sync2 <= vrr_vt_hold;  // stable — toggle edge means new value ready
        end
    end

    // VexiiRiscv CPU system — AXI4 bus routing
    cpu_system cpu (
        .clk(clk_cpu),
        .reset_n(reset_n),
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
        // CRAM1 AXI4 master interface
        .m_cram1_arvalid(cpu_m_cram1_arvalid),
        .m_cram1_arready(cpu_m_cram1_arready),
        .m_cram1_araddr (cpu_m_cram1_araddr),
        .m_cram1_arlen  (cpu_m_cram1_arlen),
        .m_cram1_rvalid (cpu_m_cram1_rvalid),
        .m_cram1_rready (cpu_m_cram1_rready),
        .m_cram1_rdata  (cpu_m_cram1_rdata),
        .m_cram1_rresp  (cpu_m_cram1_rresp),
        .m_cram1_rlast  (cpu_m_cram1_rlast),
        .m_cram1_awvalid(cpu_m_cram1_awvalid),
        .m_cram1_awready(cpu_m_cram1_awready),
        .m_cram1_awaddr (cpu_m_cram1_awaddr),
        .m_cram1_awlen  (cpu_m_cram1_awlen),
        .m_cram1_wvalid (cpu_m_cram1_wvalid),
        .m_cram1_wready (cpu_m_cram1_wready),
        .m_cram1_wdata  (cpu_m_cram1_wdata),
        .m_cram1_wstrb  (cpu_m_cram1_wstrb),
        .m_cram1_wlast  (cpu_m_cram1_wlast),
        .m_cram1_bvalid (cpu_m_cram1_bvalid),
        .m_cram1_bresp  (cpu_m_cram1_bresp),
        // SRAM port: GPU-exclusive.  CPU accesses get immediate SLVERR
        // (prevents AXI deadlock — old tie-off accepted requests but
        // never responded, hanging the CPU).
        .m_sram_arvalid(cpu_m_sram_stub_arvalid),
        .m_sram_arready(1'b1),
        .m_sram_araddr (),
        .m_sram_arlen  (),
        .m_sram_rvalid (cpu_m_sram_stub_rvalid),
        .m_sram_rready (),
        .m_sram_rdata  (32'hDEADDEAD),
        .m_sram_rresp  (2'b10),      // SLVERR
        .m_sram_rlast  (1'b1),
        .m_sram_awvalid(cpu_m_sram_stub_awvalid),
        .m_sram_awready(1'b1),
        .m_sram_awaddr (),
        .m_sram_awlen  (),
        .m_sram_wvalid (),
        .m_sram_wready (1'b1),
        .m_sram_wdata  (),
        .m_sram_wstrb  (),
        .m_sram_wlast  (),
        .m_sram_bvalid (cpu_m_sram_stub_bvalid),
        .m_sram_bresp  (2'b10),      // SLVERR
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
        .m_local_wvalid(cpu_m_local_wvalid),
        .m_local_wready(cpu_m_local_wready),
        .m_local_wdata(cpu_m_local_wdata),
        .m_local_wstrb(cpu_m_local_wstrb),
        .m_local_wlast(cpu_m_local_wlast),
        .m_local_bvalid(cpu_m_local_bvalid),
        .m_local_bresp(cpu_m_local_bresp),
        .int_m_external(ext_irq),
        .int_m_timer(timer_irq)
    );

    // AXI4 peripheral slave
    axi_periph_slave periph (
        .clk(clk_cpu),
        .reset_n(reset_n),
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
        .s_axi_wvalid(cpu_m_local_wvalid),
        .s_axi_wready(cpu_m_local_wready),
        .s_axi_wdata(cpu_m_local_wdata),
        .s_axi_wstrb(cpu_m_local_wstrb),
        .s_axi_wlast(cpu_m_local_wlast),
        .s_axi_bvalid(cpu_m_local_bvalid),
        .s_axi_bready(1'b1),
        .s_axi_bresp(cpu_m_local_bresp),
        // CDC inputs
        .dataslot_allcomplete(dataslot_allcomplete && bridge_wr_idle),
        .vsync(vidout_vs),
        .cont1_key(p1_controls),
        .cont1_joy(p1_joypad),
        .cont1_trig(p1_trigger),
        .cont2_key(p2_controls),
        .cont2_joy(p2_joypad),
        .cont2_trig(p2_trigger),
        .bridge_wr_idle(bridge_wr_idle),
        .target_dataslot_ack(target_dataslot_ack),
        .target_dataslot_done(target_dataslot_done_safe),
        .target_dataslot_err(target_dataslot_err),
        // Terminal moved to software — no hardware VRAM
        // Display control
        // display_mode removed — terminal rendering in software
        .color_mode(color_mode),
        .fb_display_addr(fb_display_addr),
        // Palette write interface
        .pal_wr(cpu_pal_wr),
        .pal_addr(cpu_pal_addr),
        .pal_data(cpu_pal_data),
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
        // Audio output interface
        .audio_sample_wr(audio_sample_wr),
        .audio_sample_data(audio_sample_data),
        .audio_fifo_level(audio_fifo_level),
        .audio_fifo_full(audio_fifo_full),
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
        .save_dt_commit(cpu_save_dt_commit),
        .app_id(app_id_sync2),
        // Shutdown handshake
        .shutdown_pending(shutdown_pending_cpu),
        .shutdown_ack(shutdown_ack_cpu),
        .mix_voice_wr(mix_voice_wr),
        .mix_voice_field(mix_voice_field),
        .mix_voice_sel(mix_voice_sel),
        .mix_voice_wdata(mix_voice_wdata),
        .mix_enable(mix_enable),
        .mix_active_count(mix_active_count),
        .mix_voice_pos(mix_voice_pos),
        .mix_irq_clear_wr(mix_irq_clear_wr),
        .mix_irq_clear_data(mix_irq_clear_data),
        .mix_irq_pending(mix_irq_pending),
        .timer_irq(timer_irq),
        .uart_rx_irq(uart_rx_irq),
        .link_irq(link_irq),
        .mix_voice_end_irq(mix_voice_end_irq),
        .ext_irq(ext_irq),
        // Datatable slot size query
        .dt_query_addr(cpu_dt_query_addr),
        .dt_query_toggle(cpu_dt_query_toggle),
        .dt_query_data(cpu_dt_query_data),
        .dt_query_valid(cpu_dt_query_valid),
        .vrr_v_total(vrr_v_total_cpu),
        // SNAC shifter / GPIO
        .snac_pin_out(snac_pin_out),
        .snac_pin_dir(snac_pin_dir),
        .snac_pin_in(snac_pin_in),
        .snac_enable(snac_enable),
        // GPU register interface
        .gpu_reg_wr(gpu_reg_wr),
        .gpu_reg_addr(gpu_reg_addr),
        .gpu_reg_wdata(gpu_reg_wdata),
        .gpu_reg_rdata(gpu_reg_rdata)
    );

    // DMA engine removed — apps use CPU memcpy instead

    // Slave → io_sdram pulse adapter
    reg sdram_accepted_r;
    reg sdram_cmd_forwarded;
    reg wr_data_fwd_d1;
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
            ram1_word_burst_len <= sdram_slave_burst_len;
            ram1_word_burst_wr_len <= sdram_slave_burst_wr_len;
            sdram_accepted_r <= 1;
            sdram_cmd_forwarded <= 1;
        end

        // Burst write data forwarding: delay by 1 cycle after slave updates
        wr_data_fwd_d1 <= ram1_word_wr_data_next;
        if (wr_data_fwd_d1) begin
            ram1_word_data <= sdram_slave_wdata;
            ram1_word_wstrb <= sdram_slave_wstrb;
        end
    end

    // axi_bridge_master removed — all bridge DMA goes through CRAM1 now.

    // AXI4 SDRAM arbiter (must stay alive during reset for APF save flush & data load)
    axi_sdram_arbiter sdram_arb (
        .clk(clk_cpu),
        .reset_n(1'b1),
        // M0: GPU read master (ring fetch + texture cache fills)
        .m0_arvalid(gpu_rd_arvalid), .m0_arready(gpu_rd_arready),
        .m0_araddr(gpu_rd_araddr),   .m0_arlen(gpu_rd_arlen),
        .m0_rvalid(gpu_rd_rvalid),   .m0_rdata(gpu_rd_rdata),
        .m0_rresp(),                 .m0_rlast(gpu_rd_rlast),
        .m0_awvalid(1'b0), .m0_awready(),
        .m0_awaddr(32'b0),  .m0_awlen(8'b0),
        .m0_wvalid(1'b0),  .m0_wready(),
        .m0_wdata(32'b0),   .m0_wstrb(4'b0),
        .m0_wlast(1'b0),
        .m0_bvalid(),       .m0_bresp(),
        // M1: GPU write master (framebuffer writes + clear DMA)
        .m1_arvalid(1'b0), .m1_arready(),
        .m1_araddr(32'b0),  .m1_arlen(8'b0),
        .m1_rvalid(),       .m1_rdata(),
        .m1_rresp(),        .m1_rlast(),
        .m1_awvalid(gpu_wr_awvalid), .m1_awready(gpu_wr_awready),
        .m1_awaddr(gpu_wr_awaddr),   .m1_awlen(gpu_wr_awlen),
        .m1_wvalid(gpu_wr_wvalid),   .m1_wready(gpu_wr_wready),
        .m1_wdata(gpu_wr_wdata),     .m1_wstrb(gpu_wr_wstrb),
        .m1_wlast(gpu_wr_wlast),
        .m1_bvalid(gpu_wr_bvalid),   .m1_bresp(),
        // M2: CPU
        .m2_arvalid(cpu_m_sdram_arvalid), .m2_arready(cpu_m_sdram_arready),
        .m2_araddr(cpu_m_sdram_araddr),   .m2_arlen(cpu_m_sdram_arlen),
        .m2_rvalid(cpu_m_sdram_rvalid),   .m2_rdata(cpu_m_sdram_rdata),
        .m2_rresp(cpu_m_sdram_rresp),     .m2_rlast(cpu_m_sdram_rlast),
        .m2_rready(cpu_m_sdram_rready),
        .m2_awvalid(cpu_m_sdram_awvalid), .m2_awready(cpu_m_sdram_awready),
        .m2_awaddr(cpu_m_sdram_awaddr),   .m2_awlen(cpu_m_sdram_awlen),
        .m2_wvalid(cpu_m_sdram_wvalid),   .m2_wready(cpu_m_sdram_wready),
        .m2_wdata(cpu_m_sdram_wdata),     .m2_wstrb(cpu_m_sdram_wstrb),
        .m2_wlast(cpu_m_sdram_wlast),
        .m2_bvalid(cpu_m_sdram_bvalid),   .m2_bresp(cpu_m_sdram_bresp),
        // M3: Bridge (lowest priority)
        // M3: Bridge (removed — tied off)
        .m3_arvalid(1'b0), .m3_arready(),
        .m3_araddr(32'b0),  .m3_arlen(8'b0),
        .m3_rvalid(),       .m3_rdata(),
        .m3_rresp(),        .m3_rlast(),
        .m3_awvalid(1'b0), .m3_awready(),
        .m3_awaddr(32'b0),  .m3_awlen(8'b0),
        .m3_wvalid(1'b0),  .m3_wready(),
        .m3_wdata(32'b0),   .m3_wstrb(4'b0),
        .m3_wlast(1'b0),
        .m3_bvalid(),       .m3_bresp(),
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
        .s_bvalid(arb_s_bvalid),   .s_bresp(arb_s_bresp)
    );

    // AXI4 SDRAM slave (must stay alive during reset for APF save flush & data load)
    // NOTE: the SDRAM slave sits behind an arbiter (arb_s_*) that fans in
    // the CPU master alongside the GPU / bridge masters, so its rready is
    // the arbiter-local signal, not cpu_m_sdram_rready directly. The
    // arbiter itself already provides back-pressure to each master; for
    // now leave this tied to 1'b1 (matches pre-burst behaviour).
    axi_sdram_slave sdram_axi_slave (
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
        .sdram_next_wdata(sdram_slave_next_wdata),
        .sdram_next_wstrb(sdram_slave_next_wstrb)
    );

    // Per-chip AXI4 sub-slaves — directly instantiated (no shared
    // axi_psram_slave router).  cpu_system gives each chip its own
    // AXI master port, so CRAM0, CRAM1, and SRAM service transactions
    // concurrently on their independent physical backends.

    axi_cram0_slave cpu_cram0_axi (
        .clk(clk_cpu),
        .reset_n(reset_n),
        .s_axi_arvalid(cpu_m_cram0_arvalid),
        .s_axi_arready(cpu_m_cram0_arready),
        .s_axi_araddr(cpu_m_cram0_araddr),
        .s_axi_arlen(cpu_m_cram0_arlen),
        .s_axi_rvalid(cpu_m_cram0_rvalid),
        .s_axi_rready(cpu_m_cram0_rready),
        .s_axi_rdata(cpu_m_cram0_rdata),
        .s_axi_rresp(cpu_m_cram0_rresp),
        .s_axi_rlast(cpu_m_cram0_rlast),
        .s_axi_awvalid(cpu_m_cram0_awvalid),
        .s_axi_awready(cpu_m_cram0_awready),
        .s_axi_awaddr(cpu_m_cram0_awaddr),
        .s_axi_awlen(cpu_m_cram0_awlen),
        .s_axi_wvalid(cpu_m_cram0_wvalid),
        .s_axi_wready(cpu_m_cram0_wready),
        .s_axi_wdata(cpu_m_cram0_wdata),
        .s_axi_wstrb(cpu_m_cram0_wstrb),
        .s_axi_wlast(cpu_m_cram0_wlast),
        .s_axi_bvalid(cpu_m_cram0_bvalid),
        .s_axi_bready(1'b1),
        .s_axi_bresp(cpu_m_cram0_bresp),
        .psram_rd          (c0_psram_rd),
        .psram_wr          (c0_psram_wr),
        .psram_addr        (c0_psram_addr),
        .psram_wdata       (c0_psram_wdata),
        .psram_wstrb       (c0_psram_wstrb),
        .psram_rdata       (c0_psram_rdata),
        .psram_busy        (c0_psram_busy),
        .psram_rdata_valid (c0_psram_rdata_valid)
    );

    axi_cram1_slave cpu_cram1_axi (
        .clk(clk_cpu),
        .reset_n(reset_n),
        .s_axi_arvalid(cpu_m_cram1_arvalid),
        .s_axi_arready(cpu_m_cram1_arready),
        .s_axi_araddr(cpu_m_cram1_araddr),
        .s_axi_arlen(cpu_m_cram1_arlen),
        .s_axi_rvalid(cpu_m_cram1_rvalid),
        .s_axi_rready(cpu_m_cram1_rready),
        .s_axi_rdata(cpu_m_cram1_rdata),
        .s_axi_rresp(cpu_m_cram1_rresp),
        .s_axi_rlast(cpu_m_cram1_rlast),
        .s_axi_awvalid(cpu_m_cram1_awvalid),
        .s_axi_awready(cpu_m_cram1_awready),
        .s_axi_awaddr(cpu_m_cram1_awaddr),
        .s_axi_awlen(cpu_m_cram1_awlen),
        .s_axi_wvalid(cpu_m_cram1_wvalid),
        .s_axi_wready(cpu_m_cram1_wready),
        .s_axi_wdata(cpu_m_cram1_wdata),
        .s_axi_wstrb(cpu_m_cram1_wstrb),
        .s_axi_wlast(cpu_m_cram1_wlast),
        .s_axi_bvalid(cpu_m_cram1_bvalid),
        .s_axi_bready(1'b1),
        .s_axi_bresp(cpu_m_cram1_bresp),
        .psram_rd          (c1_psram_rd),
        .psram_wr          (c1_psram_wr),
        .psram_addr        (c1_psram_addr),
        .psram_wdata       (c1_psram_wdata),
        .psram_wstrb       (c1_psram_wstrb),
        .psram_rdata       (c1_psram_rdata),
        .psram_busy        (c1_psram_busy),
        .psram_rdata_valid (c1_psram_rdata_valid)
    );

    // axi_sram_slave removed — SRAM is GPU-exclusive (Z-buffer).

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

    // SDRAM burst interface signals for video scanout
    wire        video_burst_rd;
    wire [24:0] video_burst_addr;
    wire [10:0] video_burst_len;
    wire        video_burst_32bit;
    wire [31:0] video_burst_data;
    wire        video_burst_data_valid;
    wire        video_burst_data_done;
    
    video_CRT_scanout_indexed_BRAM  scanout (
        .clk_video(clk_vid),
        .reset_n(reset_n_vid),
        .x_count(x_count),
        .y_count(y_count),
        .line_start(line_start),
        .pixel_color(framebuffer_pixel_color),
        .fb_base_addr(fb_display_addr),
        .color_mode(color_mode),
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
        .pal_data(cpu_pal_data)
    );


        // ---  CRT 15.7kHz / VRR Parameters ---
    localparam CRT_V_SYNC   = 3;
    localparam CRT_V_BPORCH = 15;
    localparam CRT_V_FPORCH = 4;
    localparam CRT_V_ACTIVE = 240;
    localparam CRT_V_TOTAL_DEFAULT = CRT_V_SYNC + CRT_V_BPORCH + CRT_V_ACTIVE + CRT_V_FPORCH; // 262
    localparam CRT_H_TOTAL  = CRT_H_SYNC + CRT_H_BPORCH + CRT_H_ACTIVE + CRT_H_FPORCH;
    localparam CRT_H_SYNC   = 58;
    localparam CRT_H_BPORCH = 62;
    localparam CRT_H_FPORCH = 20;
    localparam CRT_H_ACTIVE = 640;
    reg crt_hs, crt_vs, crt_de;
    reg crt_hblank, crt_vblank;

    // VRR: dynamic V_TOTAL, latched at frame boundary from CPU register.
    // Pocket scaler accepts 42-60 Hz → V_TOTAL range [262, 375].
    // Video clock: 12.5738 MHz / 780 H_TOTAL = 16120 lines/sec.
    // 16120/262 = 61.5 Hz, 16120/375 = 43.0 Hz.
    reg [9:0] crt_v_total;
    wire [9:0] vrr_vt_safe = (vrr_vt_sync2 < 10'd262) ? 10'd262 :
                              (vrr_vt_sync2 > 10'd375) ? 10'd375 :
                              (vrr_vt_sync2 == 10'd0)  ? 10'd262 : vrr_vt_sync2;

    wire [9:0]  visible_x = x_count - CRT_H_SYNC - CRT_H_BPORCH;
    wire [9:0]  visible_y = y_count - CRT_V_SYNC - CRT_V_BPORCH;

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

        vidout_hs_1 <= vidout_hs;
        vidout_de_1 <= vidout_de;

        // x and y counters
        x_count <= x_count + 1'b1;
        if(x_count == CRT_H_TOTAL-1) begin
            x_count <= 0;

            y_count <= y_count + 1'b1;
            if(y_count == crt_v_total - 1) begin
                y_count <= 0;
                crt_v_total <= vrr_vt_safe; // latch new V_TOTAL at frame boundary
            end
        end

        // CRT Blank
        crt_hblank <= x_count < (CRT_H_SYNC + CRT_H_BPORCH) || (x_count >= CRT_H_SYNC + CRT_H_BPORCH + CRT_H_ACTIVE);
        crt_vblank <= y_count < (CRT_V_SYNC + CRT_V_BPORCH) || (y_count >= CRT_V_SYNC + CRT_V_BPORCH + CRT_V_ACTIVE);

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

        // inactive screen areas are black
        vidout_rgb <= 24'h0;

        // generate active video, now accounts for CRT specific timings but making compatible with Analogue Pocket video also
        if(x_count >= CRT_H_SYNC + CRT_H_BPORCH  && x_count < CRT_H_SYNC + CRT_H_BPORCH + CRT_H_ACTIVE) begin

            if(y_count >= CRT_V_SYNC + CRT_V_BPORCH && y_count < CRT_V_SYNC + CRT_V_BPORCH + CRT_V_ACTIVE) begin
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
`ifndef EXCLUDE_LINK
link_lite #(
    .CLK_HZ(100000000),
    .SCK_HZ(256000)
) link0 (
    .clk(clk_cpu),
    .reset_n(reset_n),
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
// Hardware mixer
wire        mix_enable;
wire        mix_voice_wr;
wire [3:0]  mix_voice_field;
wire [5:0]  mix_voice_sel;
wire [31:0] mix_voice_wdata;
wire [5:0]  mix_active_count;
wire [21:0] mix_voice_pos;
wire        mix_irq_clear_wr;
wire [47:0] mix_irq_clear_data;
wire [47:0] mix_irq_pending;
wire        mix_voice_end_irq;
wire        mix_sample_wr;
wire [31:0] mix_sample_data;
wire        mix_cram1_rd;
wire [21:0] mix_cram1_addr;
wire        timer_irq;
wire        uart_rx_irq;
wire        link_irq;
wire        ext_irq;  // Masked combination from axi_periph_slave

// audio_output: dcfifo bridges clk_cpu → clk_audio (12.288 MHz).
// Mixer and audio_output both run on clk_cpu now — no CDC on the
// sample push path.
audio_output audio_out (
    .clk_sys      (clk_cpu),
    .clk_audio    (clk_core_12288),
    .reset_n      (reset_n),

    .sample_wr    (mix_sample_wr),
    .sample_data  (mix_sample_data),
    .fifo_level   (audio_fifo_level),
    .fifo_full    (audio_fifo_full),

    .audio_mclk   (audio_mclk),
    .audio_lrck   (audio_lrck),
    .audio_dac    (audio_dac)
);

// audio_mixer runs directly on clk_cpu — no CDC wrapper needed.
// CPU voice config, CRAM1 data, and audio output are all same-domain.
wire        cram1_mix_rd;
wire [21:0] cram1_mix_addr;

`ifndef EXCLUDE_MIXER
audio_mixer mixer_inst (
    .clk                (clk_cpu),
    .reset_n            (reset_n),

    .mixer_enable       (mix_enable),

    .voice_wr           (mix_voice_wr),
    .voice_field        (mix_voice_field),
    .voice_sel          (mix_voice_sel),
    .voice_sel_rd       (mix_voice_sel),
    .voice_wdata        (mix_voice_wdata),

    .cram1_rd           (cram1_mix_rd),
    .cram1_addr         (cram1_mix_addr),
    .cram1_rdata        (psram1_rdata),
    .cram1_busy         (psram1_busy | brg_active_cpu | c1_psram_rd | c1_psram_wr),
    .cram1_rdata_valid  (psram1_rdata_valid_for_mixer),
    .cram1_rd_accepted  (cram1_mix_rd_accepted),

    .sample_wr          (mix_sample_wr),
    .sample_data        (mix_sample_data),
    .fifo_level         (audio_fifo_level),

    .active_count       (mix_active_count),
    .pos_readback       (mix_voice_pos),

    .irq_clear          (mix_irq_clear_data),
    .irq_clear_wr       (mix_irq_clear_wr),
    .voice_end_pending  (mix_irq_pending),
    .voice_end_irq      (mix_voice_end_irq)
);
assign mix_cram1_rd   = cram1_mix_rd;
assign mix_cram1_addr = cram1_mix_addr;
`else
assign mix_cram1_rd = 1'b0;
assign mix_cram1_addr = 22'b0;
assign mix_sample_wr = 1'b0;
assign mix_sample_data = 32'b0;
assign mix_active_count = 6'b0;
assign mix_voice_pos = 22'b0;
assign mix_irq_pending = 48'b0;
assign mix_voice_end_irq = 1'b0;
`endif

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

// GPU AXI4 write master (M1 on SDRAM arbiter)
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

// GPU SRAM interface (Z-buffer)
wire        gpu_sram_rd;
wire        gpu_sram_wr;
wire [21:0] gpu_sram_addr;
wire [31:0] gpu_sram_wdata;
wire [3:0]  gpu_sram_wstrb;

// GPU enable (from MMIO GPU_CTRL bit 0, directly in gpu_core)
wire        gpu_busy;
wire [31:0] gpu_fence_reached;
wire [31:0] gpu_stat_pixels;
wire [31:0] gpu_stat_spans;

`ifndef EXCLUDE_GPU
gpu_core gpu (
    .clk(clk_cpu),
    .reset_n(reset_n),
    .gpu_enable(1'b1),
    // AXI4 read master (ring fetch + texture)
    .m_rd_arvalid(gpu_rd_arvalid),
    .m_rd_arready(gpu_rd_arready),
    .m_rd_araddr(gpu_rd_araddr),
    .m_rd_arlen(gpu_rd_arlen),
    .m_rd_rvalid(gpu_rd_rvalid),
    .m_rd_rdata(gpu_rd_rdata),
    .m_rd_rlast(gpu_rd_rlast),
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
    // SRAM interface (Z-buffer)
    .sram_rd(gpu_sram_rd),
    .sram_wr(gpu_sram_wr),
    .sram_addr(gpu_sram_addr),
    .sram_wdata(gpu_sram_wdata),
    .sram_wstrb(gpu_sram_wstrb),
    .sram_rdata(sram_word_rdata),
    .sram_busy(sram_word_busy),
    .sram_rdata_valid(sram_word_rdata_valid),
    // MMIO registers
    .reg_wr(gpu_reg_wr),
    .reg_addr(gpu_reg_addr),
    .reg_wdata(gpu_reg_wdata),
    .reg_rdata(gpu_reg_rdata),
    // Status
    .busy(gpu_busy),
    .fence_reached(gpu_fence_reached),
    .stat_pixels(gpu_stat_pixels),
    .stat_spans(gpu_stat_spans)
);
`else
assign gpu_rd_arvalid = 1'b0;
assign gpu_rd_araddr  = 32'b0;
assign gpu_rd_arlen   = 8'b0;
assign gpu_wr_awvalid = 1'b0;
assign gpu_wr_awaddr  = 32'b0;
assign gpu_wr_awlen   = 8'b0;
assign gpu_wr_wvalid  = 1'b0;
assign gpu_wr_wdata   = 32'b0;
assign gpu_wr_wstrb   = 4'b0;
assign gpu_wr_wlast   = 1'b0;
assign gpu_sram_rd    = 1'b0;
assign gpu_sram_wr    = 1'b0;
assign gpu_sram_addr  = 22'b0;
assign gpu_sram_wdata = 32'b0;
assign gpu_sram_wstrb = 4'b0;
assign gpu_busy       = 1'b0;
assign gpu_fence_reached = 32'b0;
assign gpu_stat_pixels = 32'b0;
assign gpu_stat_spans  = 32'b0;
assign gpu_reg_rdata   = 32'b0;
`endif


///////////////////////////////////////////////


    wire    clk_core_12288;         // 12.288 MHz — audio (48kHz-friendly)
    wire    clk_core_49152;
    wire    clk_vid;                // 12.5738 MHz — video (~61.5 Hz at V_TOTAL=262)
    wire    clk_vid_90deg;
    wire    clk_cpu;
    wire    clk_ram_controller;
    wire    clk_ram_chip;

    wire    pll_core_locked;
    wire    pll_ram_locked;
    wire    pll_locked_all = pll_core_locked & pll_ram_locked;
    wire    pll_core_locked_s;
synch_3 s01(pll_locked_all, pll_core_locked_s, clk_74a);

mf_pllbase mp1 (
    .refclk         ( clk_74a ),
    .rst            ( 0 ),

    .outclk_0       ( clk_core_12288 ),
    .outclk_1       ( ),  // 12.288 MHz 90° — unused (video moved to clk_vid)

    .outclk_2       ( clk_core_49152),
    .outclk_3       ( clk_vid ),
    .outclk_4       ( clk_vid_90deg ),

    .locked         ( pll_core_locked )
);

    wire    clk_cram;

mf_pllram_133 mp_ram (
    .refclk         ( clk_74a ),
    .rst            ( 0 ),
    .outclk_0       ( clk_ram_controller ),
    .outclk_1       ( clk_ram_chip ),
    .outclk_2       ( clk_cram ),
    .locked         ( pll_ram_locked )
);

assign clk_cpu = clk_ram_controller;

// Drive CRAM clock pins from PLL outputs (the controller's cram_clk
// output stays low; the chip clock is generated here).
//
// CRAM0: clk_cram (100 MHz, 5500 ps phase shift).  The phase shift
// tunes the DQ sampling window for the Pocket's board traces and
// was kept even after CRAM0 was reverted from sync-burst to async
// page mode — async reads still latch DQ relative to cram_clk on
// the AS1C8M16PL.
//
// CRAM1: clk_74a (74.25 MHz, no phase shift).  Attempting to switch
// CRAM1 to clk_cram as a "future-proofing" change broke ELF loads
// on real hardware — the chip doesn't fully ignore cram_clk in
// async mode, and the phase shift tuned for CRAM0 is wrong for
// CRAM1's routing.  Any future move to sync burst on CRAM1 will
// require lab measurement of its actual Tco before changing this.
assign cram0_clk = clk_cram;
assign cram1_clk = clk_74a;

// SDRAM controller
io_sdram isr0 (
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

    // Word interface - CPU access via AXI
    .word_rd    ( ram1_word_rd ),
    .word_wr    ( ram1_word_wr ),
    .word_addr  ( ram1_word_addr ),
    .word_data  ( ram1_word_data ),
    .word_wstrb ( ram1_word_wstrb ),
    .word_burst_len ( ram1_word_burst_len ),
    .word_burst_wr_len ( ram1_word_burst_wr_len ),
    .word_q     ( ram1_word_q ),
    .word_busy  ( ram1_word_busy ),
    .word_q_valid ( ram1_word_q_valid ),
    .word_wr_data_next ( ram1_word_wr_data_next ),
    .burst_wr_direct_data ( sdram_slave_wdata ),
    .burst_wr_direct_strb ( sdram_slave_wstrb )
);



endmodule
