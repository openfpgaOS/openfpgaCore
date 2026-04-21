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

// cram1 chip retired in memory-arch v2 — pins are unassigned in the
// qsf so this block is gone from the module port list.

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
reg         bcr_init_done;
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

// Settle counter: hold CPU in reset for a few µs after chip-side BCR
// write completes — chip internalises sync-burst mode after CE#
// deasserts.  511 cycles @ 74.25 MHz ≈ 6.9 µs — cheap insurance.
localparam [8:0] BCR_SETTLE_CYCLES = 9'd511;

// AS1C8M16PL BCR — sync-burst mode at clk_74a.
//
// v2: cram0 runs at clk_74a (74.25 MHz) rather than clk_cpu (100 MHz).
// At the slower clock, sync-burst latency code 4 (5 cycles) is more
// than comfortable and async reads still work alongside it — the
// chip doesn't care whether the adv/oe timing is driven by a 74 MHz
// or 100 MHz controller.
//
// BCR encoding (unchanged from v1):
//   bit 15    = 0   sync burst mode
//   bit 14    = 1   variable initial latency
//   bits 13-11= 100 latency code 4 (5 clocks)
//   bit 10    = 0   WAIT active low
//   bit 9     = 0
//   bit 8     = 1   WAIT asserted one data cycle before delay
//   bit 7     = 0
//   bit 6     = 0
//   bits 5-4  = 01  drive strength 1/2
//   bit 3     = 1   no-wrap burst
//   bits 2-0  = 111 continuous burst
localparam [15:0] BCR_VALUE = 16'h641F;  // sync burst mode

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
            // This holds CPU reset for ~5 µs after the chip-side BCR
            // write completes, giving the chip time to internalise the
            // sync-burst mode change.  Addresses 1/3 intermittent boot
            // failures where first I$ line fetch returns 0x0000000f.
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

// Mux word-interface inputs to the controller: pick the owning side.
wire        mux_word_rd    = cram0_mode_74a ? cpu_cram0_word_rd    : bridge_cram0_rd_pulse;
wire        mux_word_wr    = cram0_mode_74a ? cpu_cram0_word_wr    : bridge_cram0_wr_pulse;
wire [21:0] mux_word_addr  = cram0_mode_74a ? cpu_cram0_word_addr  : bridge_addr[23:2];
wire [31:0] mux_word_wdata = cram0_mode_74a ? cpu_cram0_word_wdata : bridge_wr_data;
wire [3:0]  mux_word_wstrb = cram0_mode_74a ? cpu_cram0_word_wstrb : 4'b1111;

// Controller response fans out to whichever owner asked for it.
wire [31:0] ctrl_word_rdata;
wire        ctrl_word_busy;
wire        ctrl_word_rdata_valid;
assign cpu_cram0_word_rdata       = ctrl_word_rdata;
assign cpu_cram0_word_busy        = ctrl_word_busy;
assign cpu_cram0_word_rdata_valid = ctrl_word_rdata_valid;

// Bridge side of the word interface also reads the controller's rdata
// (used for bridge 0x20xxxxxx read-backs below).
wire [31:0] bridge_cram0_rdata      = ctrl_word_rdata;
wire        bridge_cram0_rdata_valid= ctrl_word_rdata_valid;

// Sync-burst read path — dead-path here because the bridge doesn't
// issue multi-beat reads and the CPU-side cram0_cdc only dispatches
// single-word reads.  Tied off to idle; preserves the cram0_controller
// port list without pulling the sync-burst FSM into the design.
wire        c0_burst_rd    = 1'b0;
wire [5:0]  c0_burst_len   = 6'd0;
wire [31:0] c0_burst_rdata; // unused
wire        c0_burst_rdata_valid; // unused

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
    .cram_clk(),             // PLL / clk_74a drives cram0_clk directly
    .cram_adv_n(cram0_adv_n),
    .cram_cre(cram0_cre),
    .cram_ce0_n(cram0_ce0_n),
    .cram_ce1_n(cram0_ce1_n),
    .cram_oe_n(cram0_oe_n),
    .cram_we_n(cram0_we_n),
    .cram_ub_n(cram0_ub_n),
    .cram_lb_n(cram0_lb_n),
    .burst_rd(c0_burst_rd),
    .burst_len(c0_burst_len),
    .burst_rdata_valid(c0_burst_rdata_valid),
    .burst_rdata(c0_burst_rdata),
    .config_en(bcr_init_config_en),
    .config_data(BCR_VALUE),
    .config_bank_sel(bcr_init_bank_sel),
    .raw_busy(cpu_psram_raw_busy)
);

// ============================================================
// CRAM0 CDC — CPU-side AXI slave (clk_cpu) → word-iface (clk_74a)
// ============================================================
cram0_cdc cpu_cram0_axi (
    .clk_cpu        (clk_cpu),
    .reset_n_cpu    (reset_n),

    .s_axi_arvalid  (cpu_m_cram0_arvalid),
    .s_axi_arready  (cpu_m_cram0_arready),
    .s_axi_araddr   (cpu_m_cram0_araddr),
    .s_axi_arlen    (cpu_m_cram0_arlen),
    .s_axi_rvalid   (cpu_m_cram0_rvalid),
    .s_axi_rready   (cpu_m_cram0_rready),
    .s_axi_rdata    (cpu_m_cram0_rdata),
    .s_axi_rresp    (cpu_m_cram0_rresp),
    .s_axi_rlast    (cpu_m_cram0_rlast),
    .s_axi_awvalid  (cpu_m_cram0_awvalid),
    .s_axi_awready  (cpu_m_cram0_awready),
    .s_axi_awaddr   (cpu_m_cram0_awaddr),
    .s_axi_awlen    (cpu_m_cram0_awlen),
    .s_axi_wvalid   (cpu_m_cram0_wvalid),
    .s_axi_wready   (cpu_m_cram0_wready),
    .s_axi_wdata    (cpu_m_cram0_wdata),
    .s_axi_wstrb    (cpu_m_cram0_wstrb),
    .s_axi_wlast    (cpu_m_cram0_wlast),
    .s_axi_bvalid   (cpu_m_cram0_bvalid),
    .s_axi_bready   (1'b1),
    .s_axi_bresp    (cpu_m_cram0_bresp),

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

// SRAM CPU path removed — GPU has exclusive direct access (Z-buffer).
// CRAM1 removed — memory arch v2 retires the chip.

// Audio output interface
wire        audio_sample_wr;
wire [31:0] audio_sample_data;
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
        32'h20xxxxxx: begin
            // CRAM0 bridge read — controller runs natively in clk_74a
            // so the rdata latch is same-cycle (controller returns data
            // on a word_q_valid pulse; we capture it into bridge_rd_data).
            // When firmware has set cram0_mode = BRIDGE, this is the
            // path that serves APF dataslot readbacks.
            // Match 0x20xxxxxx — the CRAM0 bridge-space address is
            // OF_TARGET_CRAM0_BRIDGE (CPU-side is 0x30xxxxxx, different).
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

// Bridge SDRAM write path removed — bridge only touches CRAM0 now
// and firmware synchronises mode flips explicitly.
// bridge_wr_idle: true when no bridge-to-cram0 operation is in flight.
// Conservative: reports busy whenever the controller is busy or a
// bridge request is mid-handshake.
reg  bridge_wr_seen_74a;
always @(posedge clk_74a) begin
    if (bridge_cram0_wr_pulse || bridge_cram0_rd_pulse)
        bridge_wr_seen_74a <= 1'b1;
    else if (!ctrl_word_busy && !bridge_wr && !bridge_rd)
        bridge_wr_seen_74a <= 1'b0;
end

wire bridge_active_74a = bridge_wr_seen_74a
                       | ctrl_word_busy
                       | bridge_cram0_wr_pulse
                       | bridge_cram0_rd_pulse;

// Synchronise drain flag back to clk_cpu for the DS_DONE waiter.
reg [2:0] bridge_active_cpu_sync;
always @(posedge clk_cpu)
    bridge_active_cpu_sync <= {bridge_active_cpu_sync[1:0], bridge_active_74a};
wire bridge_wr_idle = ~bridge_active_cpu_sync[2];

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

// Bridge SDRAM read path fully removed — bridge never reads SDRAM in v2.
// Bridge CRAM0 access lives in the ownership-mux block above.

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
        // Audio DMA control (SDRAM ring → audio_output)
        .audio_dma_base(audio_dma_base),
        .audio_dma_len(audio_dma_len),
        .audio_dma_enable(audio_dma_enable),
        .audio_dma_read_ptr(audio_dma_read_ptr),
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
        .save_dt_commit(cpu_save_dt_commit),
        .app_id(app_id_sync2),
        // Shutdown handshake
        .shutdown_pending(shutdown_pending_cpu),
        .shutdown_ack(shutdown_ack_cpu),
        .timer_irq(timer_irq),
        .uart_rx_irq(uart_rx_irq),
        .link_irq(link_irq),
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

    // axi_bridge_master removed — bridge writes no longer touch SDRAM.
    // Bridge SDRAM path retired with v2: saves/loads bounce through
    // CRAM0 (bridge-native clock), CPU then memcpys into SDRAM.

    // ============================================================
    // AXI4 SDRAM arbiter (3 masters in v2: GPU, CPU, Audio)
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
        // M2: Audio DMA (read-only, lowest priority).  The ring
        // carries ~85 ms of buffered audio, so audio can absorb long
        // arbitration gaps without glitching.
        .m2_arvalid(audio_m_arvalid), .m2_arready(audio_m_arready),
        .m2_araddr(audio_m_araddr),   .m2_arlen(audio_m_arlen),
        .m2_rvalid(audio_m_rvalid),   .m2_rdata(audio_m_rdata),
        .m2_rresp(),                  .m2_rlast(audio_m_rlast),
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

    // CRAM0 is now served by the cram0_cdc instance (declared at the
    // top of the file with the CRAM0 controller block).  No per-chip
    // AXI sub-slave is needed — the CDC *is* the CPU-side AXI slave.
    //
    // CRAM1 axi slave and pin fan-out retired with the chip.  SRAM is
    // GPU-exclusive (Z-buffer) and has no CPU AXI surface.

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
// Hardware mixer retired — CPU writes stereo samples directly via
// AUDIO_BASE MMIO (axi_periph_slave drives audio_sample_wr/data).
// dcfifo bridges clk_cpu → clk_audio (12.288 MHz).
//
wire        timer_irq;
wire        uart_rx_irq;
wire        link_irq;
wire        ext_irq;  // Masked combination from axi_periph_slave

// Audio DMA (SDRAM → audio_output).  MMIO-programmed base/len/enable
// from axi_periph_slave; reads via a dedicated AXI master port (M4)
// on the SDRAM arbiter so it never contends with the CPU or bridge on
// CRAM1.  Output muxed into audio_output below so direct CPU writes
// to AUDIO_SAMPLE still work when DMA is disabled.
wire        dma_sample_wr;
wire [31:0] dma_sample_data;
wire [13:0] audio_dma_read_ptr;
wire [31:0] audio_dma_base;
wire [13:0] audio_dma_len;
wire        audio_dma_enable;

wire        audio_m_arvalid;
wire        audio_m_arready;
wire [31:0] audio_m_araddr;
wire [7:0]  audio_m_arlen;
wire        audio_m_rvalid;
wire [31:0] audio_m_rdata;
wire        audio_m_rlast;

audio_dma audio_dma_inst (
    .clk           (clk_cpu),
    .reset_n       (reset_n),
    .enable        (audio_dma_enable),
    .ring_base     (audio_dma_base),
    .ring_len      (audio_dma_len),
    .read_ptr      (audio_dma_read_ptr),
    .m_axi_arvalid (audio_m_arvalid),
    .m_axi_arready (audio_m_arready),
    .m_axi_araddr  (audio_m_araddr),
    .m_axi_arlen   (audio_m_arlen),
    .m_axi_rvalid  (audio_m_rvalid),
    .m_axi_rdata   (audio_m_rdata),
    .m_axi_rlast   (audio_m_rlast),
    .sample_wr     (dma_sample_wr),
    .sample_data   (dma_sample_data),
    .fifo_level    (audio_fifo_level)
);

// cram1_burst_mmio retired with CRAM1 chip; 0x4E000000 MMIO slot is
// repurposed in v2 as the CRAM0 ownership mode bit (see axi_periph_slave).

// MMIO write and DMA are mutually exclusive — firmware uses one or the
// other.  Simple OR/mux keeps either path working.  If both fire on the
// same cycle (shouldn't happen in practice), DMA wins.
wire        out_sample_wr   = dma_sample_wr | audio_sample_wr;
wire [31:0] out_sample_data = dma_sample_wr ? dma_sample_data : audio_sample_data;

audio_output audio_out (
    .clk_sys      (clk_cpu),
    .clk_audio    (clk_core_12288),
    .reset_n      (reset_n),

    .sample_wr    (out_sample_wr),
    .sample_data  (out_sample_data),
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

// Bridge → SDRAM write path retired in v2 (bridge no longer touches
// SDRAM; loads/saves bounce through CRAM0 and CPU memcpys the bytes).

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

// Drive CRAM0 clock pin directly from clk_74a.  The controller now
// runs in the clk_74a domain (v2 memory architecture), so the chip
// CK and the controller FSM share the same clock tree with zero
// cross-domain skew.  PocketQuake ran the chip at 74 MHz reliably
// for years with this exact arrangement.
//
// CRAM1 retired in v2 — pin unassigned in the qsf.
assign cram0_clk = clk_74a;

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
