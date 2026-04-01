//
// User core top-level (openfpgaOS)
//
// VexiiRiscv CPU with AXI4 bus architecture, OPL2 sound synthesis.
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

    //switch between Analogizer SNAC and Pocket Controls for P1-P4 (P3,P4 when uses PCEngine Multitap)
    wire [15:0] p1_btn, p2_btn;
    wire [31:0] p1_joy, p2_joy;

    reg [31:0] p1_controls, p2_controls;
    reg [31:0] p1_joypad, p2_joypad;
    reg [15:0] p1_trigger, p2_trigger;

    always @(posedge clk_74a) begin
        if((snac_cont_type == 5'h0) || (analogizer_ena == 1'b0)) begin //SNAC is disabled
            p1_controls <= cont1_key;
            p1_joypad   <= cont1_joy;
            p1_trigger  <= cont1_trig;
            p2_controls <= cont2_key;
            p2_joypad   <= cont2_joy;
            p2_trigger  <= cont2_trig;
        end
        else begin
        case(snac_cont_assignment[1:0])
        2'h0: begin  //SNAC P1 -> Pocket P1
            p1_controls <= p1_btn;
            p1_joypad   <= p1_joy;
            p1_trigger  <= 15'h00;

            p2_controls <= cont2_key;
            p2_joypad   <= cont2_joy;
            p2_trigger  <= cont2_trig;
            end
        2'h1: begin  //SNAC P1 -> Pocket P2
            p1_controls <= cont1_key;
            p1_joypad   <= cont1_joy;
            p1_trigger  <= cont1_trig;

            p2_controls <= p1_btn;
            p2_joypad   <= p2_joy;
            p2_trigger  <= 15'h00;
            end
        2'h2: begin //SNAC P1 -> Pocket P1, SNAC P2 -> Pocket P2
            p1_controls <= p1_btn;
            p1_joypad <= p1_joy;
            p1_trigger <= 15'h00;
            p2_controls <= p2_btn;
            p2_joypad <= p2_joy;
            p2_trigger <= 15'h00;
            end
        2'h3: begin //SNAC P1 -> Pocket P2, SNAC P2 -> Pocket P1
            p1_controls <= p2_btn;
            p1_joypad <= p2_joy;
            p1_trigger <= 15'h00;
            p2_controls <= p1_btn;
            p2_joypad <= p1_joy;
            p2_trigger <= 15'h00;
            end
        default: begin 
            p1_controls <= cont1_key;
            p1_joypad   <= cont1_joy;
            p1_trigger  <= cont1_trig;

            p2_controls <= cont2_key;
            p2_joypad   <= cont2_joy;
            p2_trigger  <= cont2_trig;
            end
        endcase
        end
    end

    wire [15:0] p1_btn_CK, p2_btn_CK;
    wire [31:0] p1_joy_CK, p2_joy_CK;
    synch_3 #(
    .WIDTH(16)
    ) p1b_s (
        p1_btn_CK,
        p1_btn,
        clk_74a
    );

    synch_3 #(
        .WIDTH(16)
    ) p2b_s (
        p2_btn_CK,
        p2_btn,
        clk_74a
    );

    synch_3 #(
    .WIDTH(32)
    ) p3b_s (
        p1_joy_CK,
        p1_joy,
        clk_74a
    );
        
    synch_3 #(
        .WIDTH(32)
    ) p4b_s (
        p2_joy_CK,
        p2_joy,
        clk_74a
    );

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

// Analogizer/UART cart pin mux: UART owns bank0 + pin31 when Analogizer is disabled
wire [7:4] ana_cart_bank0;
wire       ana_cart_bank0_dir;
wire       ana_cart_pin31;
wire       ana_cart_pin31_dir;

// UART always owns cart pins (Analogizer disabled for debug builds)
// TODO: add runtime switch when Analogizer + UART coexistence is needed
assign cart_tran_bank0     = {uart_tx_serial, uart_tx_serial, uart_tx_serial, uart_tx_serial};
assign cart_tran_bank0_dir = 1'b1;   // output for UART TX
assign cart_tran_pin31     = 1'bZ;    // input for UART RX
assign cart_tran_pin31_dir = 1'b0;   // input

openFPGA_Pocket_Analogizer #(
    .MASTER_CLK_FREQ(49_152_000),
    .LINE_LENGTH(640)
) analogizer (
    .i_clk(clk_core_49152), //currently 50MHz
    .i_rst(~reset_n),
    .i_ena(analogizer_ena),
    // Video interface (active but directly from our pipeline)
    .video_clk(clk_core_12288), ////currently 12.25MHz
    .analog_video_type(analogizer_video_type),       // 0 RGBS
    .R(vidout_rgb[23:16]),
    .G(vidout_rgb[15:8]),
    .B(vidout_rgb[7:0]),
    .Hblank(crt_hblank),
    .Vblank(crt_vblank),
    .BLANKn(crt_blankn),
    .Hsync(HSync),
    .Vsync(VSync),
    .Csync(crt_csync ),
    // Y/C encoder (unused)
    .CHROMA_PHASE_INC(CHROMA_PHASE_INC),
    .PALFLAG(PALFLAG),
    // Scandoubler (unused)
    .ce_pix(1'b1),
    .scandoubler(1'b1),
    .fx(fx), //0 disable, 1 scanlines 25%, 2 scanlines 50%, 3 scanlines 75%, 4 hq2x
    // SNAC controller interface
    .conf_AB(snac_cont_type >= 5'd16),  //0 conf. A(default), 1 conf. B (see graph above)
    .game_cont_type(snac_cont_type),
    .p1_btn_state(p1_btn_CK),
    .p1_joy_state(p1_joy_CK),
    .p2_btn_state(p2_btn_CK),
    .p2_joy_state(p2_joy_CK),
    .p3_btn_state(),
    .p4_btn_state(),
    // Rumble (unused)
    .i_VIB_SW1(2'b0),
    .i_VIB_DAT1(8'h0),
    .i_VIB_SW2(2'b0),
    .i_VIB_DAT2(8'h0),
    // Status
    .busy(),
    // Cartridge port (directly driven by Analogizer)
    .cart_tran_bank2(cart_tran_bank2),
    .cart_tran_bank2_dir(cart_tran_bank2_dir),
    .cart_tran_bank3(cart_tran_bank3),
    .cart_tran_bank3_dir(cart_tran_bank3_dir),
    .cart_tran_bank1(cart_tran_bank1),
    .cart_tran_bank1_dir(cart_tran_bank1_dir),
    .cart_tran_bank0(ana_cart_bank0),
    .cart_tran_bank0_dir(ana_cart_bank0_dir),
    .cart_tran_pin30(cart_tran_pin30),
    .cart_tran_pin30_dir(cart_tran_pin30_dir),
    .cart_pin30_pwroff_reset(cart_pin30_pwroff_reset),
    .cart_tran_pin31(ana_cart_pin31),
    .cart_tran_pin31_dir(ana_cart_pin31_dir),
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
// BCR Initialization FSM
// Configures both CRAM chips for synchronous burst mode before CPU starts.
// BCR value 0x641F: sync mode, latency code 4 (≥105MHz), continuous burst.
// With psram_controller_32, both chips are configured in lockstep (2 phases: CE0, CE1).
// ============================================================
reg         bcr_init_done;
reg [2:0]   bcr_state;
reg         bcr_config_en;
reg [15:0]  bcr_config_data;
reg         bcr_config_bank;

wire        psram32_raw_busy;

localparam BCR_VALUE = 16'h641F;

localparam BCR_ST_IDLE      = 3'd0;
localparam BCR_ST_WAIT_LOCK = 3'd1;
localparam BCR_ST_CFG_CE0   = 3'd2;
localparam BCR_ST_WAIT_CE0  = 3'd3;
localparam BCR_ST_CFG_CE1   = 3'd4;
localparam BCR_ST_WAIT_CE1  = 3'd5;
localparam BCR_ST_DONE      = 3'd6;

initial begin
    bcr_init_done = 0;
    bcr_state = BCR_ST_IDLE;
    bcr_config_en = 0;
    bcr_config_data = 0;
    bcr_config_bank = 0;
end

always @(posedge clk_ram_controller) begin
    bcr_config_en <= 0;  // single-cycle pulse

    case (bcr_state)
        BCR_ST_IDLE: begin
            bcr_state <= BCR_ST_WAIT_LOCK;
        end

        BCR_ST_WAIT_LOCK: begin
            if (pll_ram_locked)
                bcr_state <= BCR_ST_CFG_CE0;
        end

        BCR_ST_CFG_CE0: begin
            if (!psram32_raw_busy) begin
                bcr_config_en <= 1;
                bcr_config_data <= BCR_VALUE;
                bcr_config_bank <= 0;  // CE0 on both chips simultaneously
                bcr_state <= BCR_ST_WAIT_CE0;
            end
        end

        BCR_ST_WAIT_CE0: begin
            if (!psram32_raw_busy && !bcr_config_en)
                bcr_state <= BCR_ST_CFG_CE1;
        end

        BCR_ST_CFG_CE1: begin
            if (!psram32_raw_busy) begin
                bcr_config_en <= 1;
                bcr_config_data <= BCR_VALUE;
                bcr_config_bank <= 1;  // CE1 on both chips simultaneously
                bcr_state <= BCR_ST_WAIT_CE1;
            end
        end

        BCR_ST_WAIT_CE1: begin
            if (!psram32_raw_busy && !bcr_config_en)
                bcr_state <= BCR_ST_DONE;
        end

        BCR_ST_DONE: begin
            bcr_init_done <= 1;
        end
    endcase
end

// ============================================================
// 32-bit Dual PSRAM Controller (CRAM0 + CRAM1 in lockstep)
// CRAM0 = bits [15:0], CRAM1 = bits [31:16]
// ============================================================
wire        psram32_burst_rd;
wire [5:0]  psram32_burst_len;
wire        psram32_burst_rdata_valid;
wire [31:0] psram32_burst_rdata;

psram_controller_32 #(
    .CLOCK_SPEED(100.0)
) psram32 (
    .clk(clk_ram_controller),
    .reset_n(1'b1),
    .word_rd(psram_mux_rd),
    .word_wr(psram_mux_wr),
    .word_addr(psram_mux_addr),
    .word_data(psram_mux_wdata),
    .word_wstrb(psram_mux_wstrb),
    .word_q(psram_mux_rdata),
    .word_busy(psram_mux_busy),
    .word_q_valid(psram_mux_rdata_valid),
    // CRAM0 (low 16 bits)
    .cram0_a(cram0_a),
    .cram0_dq(cram0_dq),
    .cram0_wait(cram0_wait),
    .cram0_clk(),    // PLL drives cram0_clk directly
    .cram0_adv_n(cram0_adv_n),
    .cram0_cre(cram0_cre),
    .cram0_ce0_n(cram0_ce0_n),
    .cram0_ce1_n(cram0_ce1_n),
    .cram0_oe_n(cram0_oe_n),
    .cram0_we_n(cram0_we_n),
    .cram0_ub_n(cram0_ub_n),
    .cram0_lb_n(cram0_lb_n),
    // CRAM1 (high 16 bits)
    .cram1_a(cram1_a),
    .cram1_dq(cram1_dq),
    .cram1_wait(cram1_wait),
    .cram1_clk(),    // PLL drives cram1_clk directly
    .cram1_adv_n(cram1_adv_n),
    .cram1_cre(cram1_cre),
    .cram1_ce0_n(cram1_ce0_n),
    .cram1_ce1_n(cram1_ce1_n),
    .cram1_oe_n(cram1_oe_n),
    .cram1_we_n(cram1_we_n),
    .cram1_ub_n(cram1_ub_n),
    .cram1_lb_n(cram1_lb_n),
    // Config
    .config_en(bcr_config_en),
    .config_data(bcr_config_data),
    .config_bank_sel(bcr_config_bank),
    // Burst
    .burst_rd(psram32_burst_rd),
    .burst_len(psram32_burst_len),
    .burst_rdata_valid(psram32_burst_rdata_valid),
    .burst_rdata(psram32_burst_rdata),
    .raw_busy(psram32_raw_busy),
    .dbg_wait_seen(),
    .dbg_wait_cycles(),
    .dbg_burst_count(),
    .dbg_stale_count()
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
// CLKS_PER_BIT = 90 MHz / 2000000 = 45
// ============================================================
wire        uart_tx_serial;
wire        uart_tx_active;
wire        uart_tx_done;
wire        uart_tx_dv;
wire [7:0]  uart_tx_byte;
wire        uart_rx_dv;
wire [7:0]  uart_rx_byte;

uart_tx #(.CLKS_PER_BIT(45)) uart_tx_inst (
    .i_Clock(clk_cpu),
    .i_Tx_DV(uart_tx_dv),
    .i_Tx_Byte(uart_tx_byte),
    .o_Tx_Active(uart_tx_active),
    .o_Tx_Serial(uart_tx_serial),
    .o_Tx_Done(uart_tx_done)
);

uart_rx #(.CLKS_PER_BIT(45)) uart_rx_inst (
    .i_Clock(clk_cpu),
    .i_Rx_Serial(cart_tran_pin31),  // DevKey Pin 31 = UART RX
    .o_Rx_DV(uart_rx_dv),
    .o_Rx_Byte(uart_rx_byte)
);

// UART TX also on dbg_tx (direct 1.8V debug pin)
assign dbg_tx = uart_tx_serial;

assign user1 = 1'bZ;
assign aux_scl = 1'bZ;
assign vpll_feed = 1'bZ;

// ============================================================
// SDRAM word interface signals (to io_sdram, from word_sdram_arbiter)
// ============================================================
wire            ram1_word_rd;
wire            ram1_word_wr;
wire    [23:0]  ram1_word_addr;
wire    [31:0]  ram1_word_data;
wire    [3:0]   ram1_word_wstrb;
wire    [3:0]   ram1_word_burst_len;
wire    [3:0]   ram1_word_burst_wr_len;
wire            ram1_word_wr_data_next;
wire    [31:0]  ram1_word_q;
wire            ram1_word_busy;
wire            ram1_word_q_valid;

// ============================================================
// CPU AXI4 master buses
// ============================================================

// CPU word-level SDRAM master (to word_sdram_arbiter M2)
wire        cpu_m_sdram_rd;
wire        cpu_m_sdram_wr;
wire [23:0] cpu_m_sdram_addr;
wire [31:0] cpu_m_sdram_wdata;
wire [3:0]  cpu_m_sdram_wstrb;
wire [3:0]  cpu_m_sdram_burst_len;
wire [3:0]  cpu_m_sdram_burst_wr_len;
wire [31:0] cpu_m_sdram_rdata;
wire        cpu_m_sdram_busy;
wire        cpu_m_sdram_accepted;
wire        cpu_m_sdram_rdata_valid;
wire        cpu_m_sdram_wr_data_next;

// CPU word-level PSRAM master (direct from cpu_system to psram_controller)
// cpu_psram_addr[25:22] carries addr[27:24] for CRAM0/CRAM1/SRAM decode
wire        cpu_psram_rd;
wire        cpu_psram_wr;
wire [25:0] cpu_psram_addr;
wire [31:0] cpu_psram_wdata;
wire [3:0]  cpu_psram_wstrb;
wire [31:0] cpu_psram_rdata;
wire        cpu_psram_busy;
wire        cpu_psram_rdata_valid;
wire        cpu_psram_burst_rd;
wire [5:0]  cpu_psram_burst_len;
wire        cpu_psram_burst_rdata_valid;
wire [31:0] cpu_psram_burst_rdata;


// Address decode: cpu_psram_addr[25:22] = original addr[27:24]
// CRAM0 and CRAM1 are now a single 32-bit-wide memory (psram_controller_32)
wire [3:0] mem_target_sel = cpu_psram_addr[25:22];
wire cpu_psram_sel_cram = (mem_target_sel == 4'h0) || (mem_target_sel == 4'h8)
                        || (mem_target_sel == 4'h1) || (mem_target_sel == 4'h9);
wire cpu_psram_sel_sram = (mem_target_sel == 4'hA);

// Muxed PSRAM signals (bridge or CPU)
wire        psram_mux_rd;
wire        psram_mux_wr;
wire [21:0] psram_mux_addr;
wire [31:0] psram_mux_wdata;
wire [3:0]  psram_mux_wstrb;
wire [31:0] psram_mux_rdata;
wire        psram_mux_busy;
wire        psram_mux_rdata_valid;

// Audio output interface
wire        audio_sample_wr;
wire [31:0] audio_sample_data;
wire [11:0] audio_fifo_level;
wire        audio_fifo_full;

// OPL3 (YMF262) hardware interface
wire        opl_write_req;
wire [1:0]  opl_write_addr;
wire [7:0]  opl_write_data;
wire        opl_ack;
wire signed [15:0] opl_audio_out;
wire               opl_sample_toggle;

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
// CPU word-level local master (to periph_slave)
wire        cpu_m_local_rd;
wire        cpu_m_local_wr;
wire [31:0] cpu_m_local_addr;
wire [31:0] cpu_m_local_wdata;
wire [3:0]  cpu_m_local_wstrb;
wire [7:0]  cpu_m_local_burst_len;
wire [31:0] cpu_m_local_rdata;
wire        cpu_m_local_rdata_valid;
wire        cpu_m_local_rdata_last;
wire        cpu_m_local_wr_done;
wire        cpu_m_local_busy;

// AXI4 arbiter output → axi_sdram_slave (direct, no pipeline)
// (AXI arbiter slave wires removed — word_sdram_arbiter drives io_sdram directly)

// Bridge AXI4 master (from axi_bridge_master to axi_sdram_arbiter)
// Bridge word-level SDRAM master (to word_sdram_arbiter M3)
wire        bridge_m_rd;
wire        bridge_m_wr;
wire [23:0] bridge_m_addr;
wire [31:0] bridge_m_wdata;
wire [3:0]  bridge_m_wstrb;
wire [3:0]  bridge_m_burst_len;
wire [3:0]  bridge_m_burst_wr_len;
wire [31:0] bridge_m_rdata;
wire        bridge_m_busy;
wire        bridge_m_accepted;
wire        bridge_m_rdata_valid;
wire        bridge_m_wr_data_next;
wire        bridge_m_idle;
wire        bridge_m_wr_idle;
wire [31:0] bridge_axi_rd_data;
wire        bridge_axi_rd_done;


// ============================================================
// Bridge read data mux
// ============================================================
always @(*) begin
    casex(bridge_addr)
        default: begin
            bridge_rd_data <= 0;
        end
        32'b000000xx_xxxxxxxx_xxxxxxxx_xxxxxxxx: begin
            bridge_rd_data <= bridge_rd_data_captured;
        end
        32'h30xxxxxx: begin
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

// ============================================================
// Bridge SDRAM Write CDC: dcfifo (clk_74a -> clk_ram_controller)
// ============================================================
localparam integer BRIDGE_WR_SKID_DEPTH = 4;
wire        bridge_sdram_wr = bridge_wr && (bridge_addr[31:26] == 6'b000000);

wire        bridge_wr_fifo_wrreq;
wire        bridge_wr_fifo_full;
wire [55:0] bridge_wr_fifo_wdata;
wire        bridge_wr_fifo_drain;
wire        bridge_wr_fifo_empty;
wire [55:0] bridge_wr_fifo_q;
reg [55:0]  bridge_wr_skid_data [0:BRIDGE_WR_SKID_DEPTH-1];
reg [1:0]   bridge_wr_skid_wrptr;
reg [1:0]   bridge_wr_skid_rdptr;
reg [2:0]   bridge_wr_skid_count;
wire        bridge_wr_skid_empty = (bridge_wr_skid_count == 0);
wire        bridge_wr_skid_nonempty_74a = !bridge_wr_skid_empty;
wire        bridge_wr_skid_pop = !bridge_wr_skid_empty && !bridge_wr_fifo_full;
wire [55:0] bridge_wr_skid_head =
            (bridge_wr_skid_rdptr == 2'd0) ? bridge_wr_skid_data[0] :
            (bridge_wr_skid_rdptr == 2'd1) ? bridge_wr_skid_data[1] :
            (bridge_wr_skid_rdptr == 2'd2) ? bridge_wr_skid_data[2] :
                                             bridge_wr_skid_data[3];
wire        bridge_wr_skid_push = bridge_sdram_wr;
wire        bridge_wr_skid_has_space = (bridge_wr_skid_count != 3'd4);
wire        bridge_wr_skid_push_ok = bridge_wr_skid_push &&
                                     (bridge_wr_skid_has_space || bridge_wr_skid_pop);
assign bridge_wr_fifo_wrreq = bridge_wr_skid_pop;
assign bridge_wr_fifo_wdata = bridge_wr_skid_head;

always @(posedge clk_74a) begin
    if (!reset_n_apf) begin
        bridge_wr_skid_wrptr <= 2'd0;
        bridge_wr_skid_rdptr <= 2'd0;
        bridge_wr_skid_count <= 3'd0;
    end else begin
        if (bridge_wr_skid_pop) begin
            bridge_wr_skid_rdptr <= bridge_wr_skid_rdptr + 2'd1;
        end

        if (bridge_wr_skid_push_ok) begin
            case (bridge_wr_skid_wrptr)
                2'd0: bridge_wr_skid_data[0] <= {bridge_addr[25:2], bridge_wr_data[31:0]};
                2'd1: bridge_wr_skid_data[1] <= {bridge_addr[25:2], bridge_wr_data[31:0]};
                2'd2: bridge_wr_skid_data[2] <= {bridge_addr[25:2], bridge_wr_data[31:0]};
                default: bridge_wr_skid_data[3] <= {bridge_addr[25:2], bridge_wr_data[31:0]};
            endcase
            bridge_wr_skid_wrptr <= bridge_wr_skid_wrptr + 2'd1;
        end

        case ({bridge_wr_skid_push_ok, bridge_wr_skid_pop})
            2'b10: bridge_wr_skid_count <= bridge_wr_skid_count + 3'd1;
            2'b01: bridge_wr_skid_count <= bridge_wr_skid_count - 3'd1;
            default: ;
        endcase
    end
end

dcfifo bridge_wr_fifo (
    .wrclk   (clk_74a),
    .wrreq   (bridge_wr_fifo_wrreq),
    .data    (bridge_wr_fifo_wdata),
    .wrfull  (bridge_wr_fifo_full),
    .rdclk   (clk_ram_controller),
    .rdreq   (bridge_wr_fifo_drain),
    .q       (bridge_wr_fifo_q),
    .rdempty (bridge_wr_fifo_empty),
    .aclr    (1'b0),
    .wrusedw (),
    .wrempty (),
    .rdfull  (),
    .rdusedw ()
);
defparam bridge_wr_fifo.intended_device_family = "Cyclone V",
    bridge_wr_fifo.lpm_numwords  = 512,
    bridge_wr_fifo.lpm_showahead = "ON",
    bridge_wr_fifo.lpm_type      = "dcfifo",
    bridge_wr_fifo.lpm_width     = 56,
    bridge_wr_fifo.lpm_widthu    = 9,
    bridge_wr_fifo.overflow_checking  = "ON",
    bridge_wr_fifo.underflow_checking = "ON",
    bridge_wr_fifo.rdsync_delaypipe   = 5,
    bridge_wr_fifo.wrsync_delaypipe   = 5,
    bridge_wr_fifo.use_eab       = "ON";

// Synchronize skid-queue nonempty flag into RAM clock domain
reg [2:0] bridge_wr_skid_nonempty_sync;
always @(posedge clk_ram_controller) begin
    bridge_wr_skid_nonempty_sync <= {bridge_wr_skid_nonempty_sync[1:0], bridge_wr_skid_nonempty_74a};
end
wire bridge_wr_skid_nonempty = bridge_wr_skid_nonempty_sync[2];

wire bridge_wr_idle = !bridge_wr_skid_nonempty && bridge_m_wr_idle;

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

// Bridge SDRAM read handshake CDC
reg [31:0] bridge_addr_captured;
reg bridge_sdram_rd;
reg [31:0] bridge_addr_ram_clk;
reg bridge_rd_done;
reg bridge_rd_done_sync1, bridge_rd_done_sync2;
reg [31:0] bridge_rd_data_captured;

always @(posedge clk_74a) begin
    bridge_rd_done_sync1 <= bridge_rd_done;
    bridge_rd_done_sync2 <= bridge_rd_done_sync1;

    if (bridge_rd_done_sync2) bridge_sdram_rd <= 0;

    // SDRAM read (0x00-0x03) — single-word handshake
    if (!bridge_sdram_rd && bridge_rd && bridge_addr[31:26] == 6'b000000) begin
        bridge_sdram_rd <= 1;
        bridge_addr_captured <= bridge_addr;
    end
end

// CRAM1 bridge read: With unified psram_controller_32, CRAM1 reads
// (bridge addr 0x30xxxxxx) go through the same controller as CRAM0.
// We use a request/response FIFO pair just like before, but the drain
// FSM reads from the unified psram_controller_32 via the mux.
wire        cram1_rd_req_fifo_full;

reg         bridge_cram1_rd;
reg  [31:0] bridge_cram1_rd_captured;
wire        cram1_rd_resp_empty;
wire [31:0] cram1_rd_resp_q;
reg         cram1_rd_resp_pop;

always @(posedge clk_74a) begin
    cram1_rd_resp_pop <= 0;

    if (!bridge_cram1_rd && bridge_rd && (bridge_addr[31:24] == 8'h30)) begin
        bridge_cram1_rd <= 1;
    end

    if (bridge_cram1_rd && !cram1_rd_resp_empty) begin
        bridge_cram1_rd_captured <= cram1_rd_resp_q;
        cram1_rd_resp_pop <= 1;
        bridge_cram1_rd <= 0;
    end
end

wire cram1_rd_req_push = !bridge_cram1_rd && bridge_rd
                        && (bridge_addr[31:24] == 8'h30)
                        && !cram1_rd_req_fifo_full;
wire [21:0] cram1_rd_req_data = bridge_addr[23:2];

// CRAM1 bridge read FIFOs (request + response) — kept for bridge compatibility.
// The drain FSM uses the unified psram_controller_32 via psram_mux signals.
wire        cram1_rd_req_fifo_empty;
wire [21:0] cram1_rd_req_fifo_q;
wire        cram1_rd_req_drain;

dcfifo cram1_rd_req_fifo (
    .wrclk   (clk_74a),
    .wrreq   (cram1_rd_req_push),
    .data    (cram1_rd_req_data),
    .wrfull  (cram1_rd_req_fifo_full),
    .rdclk   (clk_ram_controller),
    .rdreq   (cram1_rd_req_drain),
    .q       (cram1_rd_req_fifo_q),
    .rdempty (cram1_rd_req_fifo_empty),
    .aclr    (1'b0)
);
defparam cram1_rd_req_fifo.intended_device_family = "Cyclone V",
    cram1_rd_req_fifo.lpm_numwords  = 64,
    cram1_rd_req_fifo.lpm_showahead = "ON",
    cram1_rd_req_fifo.lpm_type      = "dcfifo",
    cram1_rd_req_fifo.lpm_width     = 22,
    cram1_rd_req_fifo.lpm_widthu    = 6,
    cram1_rd_req_fifo.overflow_checking  = "ON",
    cram1_rd_req_fifo.underflow_checking = "ON",
    cram1_rd_req_fifo.rdsync_delaypipe   = 5,
    cram1_rd_req_fifo.wrsync_delaypipe   = 5,
    cram1_rd_req_fifo.use_eab       = "ON";

// CRAM1 read drain FSM — reads from unified controller
reg        cram1_rd_pending;
reg        cram1_rd_started;
reg [21:0] cram1_rd_addr_r;
reg        cram1_rd_resp_push;
reg [31:0] cram1_rd_resp_wdata;

assign cram1_rd_req_drain = !cram1_rd_req_fifo_empty && !cram1_rd_pending;

always @(posedge clk_ram_controller) begin
    cram1_rd_resp_push <= 0;

    if (!cram1_rd_pending) begin
        if (!cram1_rd_req_fifo_empty) begin
            cram1_rd_addr_r <= cram1_rd_req_fifo_q;
            cram1_rd_pending <= 1;
            cram1_rd_started <= 0;
        end
    end else begin
        if (!cram1_rd_started && psram_mux_busy)
            cram1_rd_started <= 1;
        if (psram_mux_rdata_valid) begin
            cram1_rd_resp_wdata <= psram_mux_rdata;
            cram1_rd_resp_push <= 1;
            cram1_rd_pending <= 0;
            cram1_rd_started <= 0;
        end
    end
end

dcfifo cram1_rd_resp_fifo (
    .wrclk   (clk_ram_controller),
    .wrreq   (cram1_rd_resp_push),
    .data    (cram1_rd_resp_wdata),
    .rdclk   (clk_74a),
    .rdreq   (cram1_rd_resp_pop),
    .q       (cram1_rd_resp_q),
    .rdempty (cram1_rd_resp_empty),
    .aclr    (1'b0),
    .wrfull  (), .wrempty (), .rdfull (), .rdusedw (), .wrusedw ()
);
defparam cram1_rd_resp_fifo.intended_device_family = "Cyclone V",
    cram1_rd_resp_fifo.lpm_numwords  = 4,
    cram1_rd_resp_fifo.lpm_showahead = "ON",
    cram1_rd_resp_fifo.lpm_type      = "dcfifo",
    cram1_rd_resp_fifo.lpm_width     = 32,
    cram1_rd_resp_fifo.lpm_widthu    = 2,
    cram1_rd_resp_fifo.overflow_checking  = "ON",
    cram1_rd_resp_fifo.underflow_checking = "ON",
    cram1_rd_resp_fifo.rdsync_delaypipe   = 5,
    cram1_rd_resp_fifo.wrsync_delaypipe   = 5,
    cram1_rd_resp_fifo.use_eab       = "OFF";

wire [31:0] cram1_rd_resp_data = bridge_cram1_rd_captured;

// 4-stage synchronizer for SDRAM bridge reads
reg bridge_rd_sync1, bridge_rd_sync2, bridge_rd_sync3, bridge_rd_sync4;
reg [31:0] bridge_addr_sync1, bridge_addr_sync2;

always @(posedge clk_ram_controller) begin
    bridge_rd_sync1 <= bridge_sdram_rd;
    bridge_rd_sync2 <= bridge_rd_sync1;
    bridge_rd_sync3 <= bridge_rd_sync2;
    bridge_rd_sync4 <= bridge_rd_sync3;

    if (bridge_rd_sync2 && !bridge_rd_sync3) begin
        bridge_addr_sync1 <= bridge_addr_captured;
    end
    bridge_addr_sync2 <= bridge_addr_sync1;

    if (bridge_rd_sync3 && !bridge_rd_sync4) begin
        bridge_addr_ram_clk <= bridge_addr_sync2;
    end

    // SDRAM read complete → capture data
    if (bridge_axi_rd_done) begin
        bridge_rd_data_captured <= bridge_axi_rd_data;
        bridge_rd_done <= 1;
    end
    if (!bridge_rd_sync1) begin
        bridge_rd_done <= 0;
    end
end

// ============================================================
// Bridge CRAM0 Write Path: dcfifo (clk_74a -> clk_ram_controller)
// Same FIFO-based pattern as CRAM1 for bulk dataslot loads.
// ============================================================
// Accept bridge writes to both CRAM0 (0x20) and CRAM1 (0x30) regions —
// with psram_controller_32, both go to the unified controller
wire bridge_cram0_wr_detect = bridge_wr && ((bridge_addr[31:24] == 8'h20)
                                         || (bridge_addr[31:24] == 8'h30));

// Skid buffer (4-entry) in clk_74a domain
localparam integer CRAM0_WR_SKID_DEPTH = 4;
reg [55:0] cram0_wr_skid_data [0:CRAM0_WR_SKID_DEPTH-1];
reg [1:0]  cram0_wr_skid_wrptr;
reg [1:0]  cram0_wr_skid_rdptr;
reg [2:0]  cram0_wr_skid_count;
wire       cram0_wr_skid_empty = (cram0_wr_skid_count == 0);
wire       cram0_wr_skid_nonempty_74a = !cram0_wr_skid_empty;
wire       cram0_wr_skid_pop = !cram0_wr_skid_empty && !cram0_wr_fifo_full;
wire [55:0] cram0_wr_skid_head =
            (cram0_wr_skid_rdptr == 2'd0) ? cram0_wr_skid_data[0] :
            (cram0_wr_skid_rdptr == 2'd1) ? cram0_wr_skid_data[1] :
            (cram0_wr_skid_rdptr == 2'd2) ? cram0_wr_skid_data[2] :
                                             cram0_wr_skid_data[3];
wire       cram0_wr_skid_push = bridge_cram0_wr_detect;
wire       cram0_wr_skid_has_space = (cram0_wr_skid_count != 3'd4);
wire       cram0_wr_skid_push_ok = cram0_wr_skid_push &&
                                   (cram0_wr_skid_has_space || cram0_wr_skid_pop);

wire       cram0_wr_fifo_full;
wire       cram0_wr_fifo_empty;
wire [55:0] cram0_wr_fifo_q;

always @(posedge clk_74a) begin
    if (cram0_wr_skid_pop)
        cram0_wr_skid_rdptr <= cram0_wr_skid_rdptr + 2'd1;

    if (cram0_wr_skid_push_ok) begin
        case (cram0_wr_skid_wrptr)
            2'd0: cram0_wr_skid_data[0] <= {bridge_addr[23:2], 2'b00, bridge_wr_data[31:0]};
            2'd1: cram0_wr_skid_data[1] <= {bridge_addr[23:2], 2'b00, bridge_wr_data[31:0]};
            2'd2: cram0_wr_skid_data[2] <= {bridge_addr[23:2], 2'b00, bridge_wr_data[31:0]};
            default: cram0_wr_skid_data[3] <= {bridge_addr[23:2], 2'b00, bridge_wr_data[31:0]};
        endcase
        cram0_wr_skid_wrptr <= cram0_wr_skid_wrptr + 2'd1;
    end

    case ({cram0_wr_skid_push_ok, cram0_wr_skid_pop})
        2'b10: cram0_wr_skid_count <= cram0_wr_skid_count + 3'd1;
        2'b01: cram0_wr_skid_count <= cram0_wr_skid_count - 3'd1;
        default: ;
    endcase
end

// Async FIFO: clk_74a -> clk_ram_controller (512 entries, 56-bit)
wire cram0_wr_fifo_drain;

dcfifo cram0_wr_fifo (
    .wrclk   (clk_74a),
    .wrreq   (cram0_wr_skid_pop),
    .data    (cram0_wr_skid_head),
    .wrfull  (cram0_wr_fifo_full),
    .rdclk   (clk_ram_controller),
    .rdreq   (cram0_wr_fifo_drain),
    .q       (cram0_wr_fifo_q),
    .rdempty (cram0_wr_fifo_empty),
    .aclr    (1'b0),
    .wrusedw (), .wrempty (), .rdfull (), .rdusedw ()
);
defparam cram0_wr_fifo.intended_device_family = "Cyclone V",
    cram0_wr_fifo.lpm_numwords  = 512,
    cram0_wr_fifo.lpm_showahead = "ON",
    cram0_wr_fifo.lpm_type      = "dcfifo",
    cram0_wr_fifo.lpm_width     = 56,
    cram0_wr_fifo.lpm_widthu    = 9,
    cram0_wr_fifo.overflow_checking  = "ON",
    cram0_wr_fifo.underflow_checking = "ON",
    cram0_wr_fifo.rdsync_delaypipe   = 5,
    cram0_wr_fifo.wrsync_delaypipe   = 5,
    cram0_wr_fifo.use_eab       = "ON";

// Synchronize skid-queue nonempty flag into RAM clock domain
reg [2:0] cram0_wr_skid_nonempty_sync;
always @(posedge clk_ram_controller) begin
    cram0_wr_skid_nonempty_sync <= {cram0_wr_skid_nonempty_sync[1:0], cram0_wr_skid_nonempty_74a};
end
wire cram0_wr_skid_nonempty = cram0_wr_skid_nonempty_sync[2];

// CRAM0 write drain FSM
reg        cram0_wr_pending;
reg        cram0_wr_started;
reg [21:0] cram0_wr_addr_r;
reg [31:0] cram0_wr_data_r;

assign cram0_wr_fifo_drain = !cram0_wr_fifo_empty && !cram0_wr_pending && bcr_init_done;

always @(posedge clk_ram_controller) begin
    if (!cram0_wr_pending) begin
        if (!cram0_wr_fifo_empty && bcr_init_done) begin
            cram0_wr_addr_r <= cram0_wr_fifo_q[55:34];
            cram0_wr_data_r <= cram0_wr_fifo_q[31:0];
            cram0_wr_pending <= 1;
            cram0_wr_started <= 0;
        end
    end else begin
        if (!cram0_wr_started && psram_mux_busy) begin
            cram0_wr_started <= 1;
        end else if (cram0_wr_started && !psram_mux_busy) begin
            cram0_wr_pending <= 0;
            cram0_wr_started <= 0;
        end
    end
end

wire cram0_bridge_wr_active = cram0_wr_pending | !cram0_wr_fifo_empty | cram0_wr_skid_nonempty;
wire cram_bridge_active = cram0_bridge_wr_active | cram1_rd_pending;

// ============================================================
// Bridge SRAM Write Path: dcfifo (clk_74a -> clk_ram_controller)
// Enables file reads to bounce through SRAM (256KB, separate bus)
// to avoid SDRAM/PSRAM contention during bridge DMA.
// Bridge address 0x3Axxxxxx → SRAM.
// ============================================================
wire bridge_sram_wr_detect = bridge_wr && (bridge_addr[31:24] == 8'h3A);

// Skid buffer (4-entry) in clk_74a domain
localparam integer SRAM_WR_SKID_DEPTH = 4;
reg [55:0] sram_wr_skid_data [0:SRAM_WR_SKID_DEPTH-1];
reg [1:0]  sram_wr_skid_wrptr;
reg [1:0]  sram_wr_skid_rdptr;
reg [2:0]  sram_wr_skid_count;
wire       sram_wr_skid_empty = (sram_wr_skid_count == 0);
wire       sram_wr_skid_pop = !sram_wr_skid_empty && !sram_wr_fifo_full;
wire [55:0] sram_wr_skid_head =
            (sram_wr_skid_rdptr == 2'd0) ? sram_wr_skid_data[0] :
            (sram_wr_skid_rdptr == 2'd1) ? sram_wr_skid_data[1] :
            (sram_wr_skid_rdptr == 2'd2) ? sram_wr_skid_data[2] :
                                             sram_wr_skid_data[3];
wire       sram_wr_skid_push = bridge_sram_wr_detect;
wire       sram_wr_skid_has_space = (sram_wr_skid_count != 3'd4);
wire       sram_wr_skid_push_ok = sram_wr_skid_push &&
                                  (sram_wr_skid_has_space || sram_wr_skid_pop);

wire       sram_wr_fifo_full;
wire       sram_wr_fifo_empty;
wire [55:0] sram_wr_fifo_q;

always @(posedge clk_74a) begin
    if (sram_wr_skid_pop)
        sram_wr_skid_rdptr <= sram_wr_skid_rdptr + 2'd1;

    if (sram_wr_skid_push_ok) begin
        case (sram_wr_skid_wrptr)
            2'd0: sram_wr_skid_data[0] <= {bridge_addr[23:2], 2'b00, bridge_wr_data[31:0]};
            2'd1: sram_wr_skid_data[1] <= {bridge_addr[23:2], 2'b00, bridge_wr_data[31:0]};
            2'd2: sram_wr_skid_data[2] <= {bridge_addr[23:2], 2'b00, bridge_wr_data[31:0]};
            default: sram_wr_skid_data[3] <= {bridge_addr[23:2], 2'b00, bridge_wr_data[31:0]};
        endcase
        sram_wr_skid_wrptr <= sram_wr_skid_wrptr + 2'd1;
    end

    case ({sram_wr_skid_push_ok, sram_wr_skid_pop})
        2'b10: sram_wr_skid_count <= sram_wr_skid_count + 3'd1;
        2'b01: sram_wr_skid_count <= sram_wr_skid_count - 3'd1;
        default: ;
    endcase
end

// Async FIFO: clk_74a -> clk_ram_controller
wire sram_wr_fifo_drain;

dcfifo sram_wr_fifo (
    .wrclk   (clk_74a),
    .wrreq   (sram_wr_skid_pop),
    .data    (sram_wr_skid_head),
    .wrfull  (sram_wr_fifo_full),
    .rdclk   (clk_ram_controller),
    .rdreq   (sram_wr_fifo_drain),
    .q       (sram_wr_fifo_q),
    .rdempty (sram_wr_fifo_empty),
    .aclr    (1'b0),
    .wrusedw (), .wrempty (), .rdfull (), .rdusedw ()
);
defparam sram_wr_fifo.intended_device_family = "Cyclone V",
    sram_wr_fifo.lpm_numwords  = 512,
    sram_wr_fifo.lpm_showahead = "ON",
    sram_wr_fifo.lpm_type      = "dcfifo",
    sram_wr_fifo.lpm_width     = 56,
    sram_wr_fifo.lpm_widthu    = 9,
    sram_wr_fifo.overflow_checking  = "ON",
    sram_wr_fifo.underflow_checking = "ON",
    sram_wr_fifo.rdsync_delaypipe   = 5,
    sram_wr_fifo.wrsync_delaypipe   = 5,
    sram_wr_fifo.use_eab       = "ON";

// SRAM write drain FSM
reg        sram_wr_pending;
reg        sram_wr_started;
reg [21:0] sram_wr_addr_r;
reg [31:0] sram_wr_data_r;

initial begin
    sram_wr_pending = 0;
    sram_wr_started = 0;
end

assign sram_wr_fifo_drain = !sram_wr_fifo_empty && !sram_wr_pending;

always @(posedge clk_ram_controller) begin
    if (!sram_wr_pending) begin
        if (!sram_wr_fifo_empty) begin
            sram_wr_addr_r <= sram_wr_fifo_q[55:34];
            sram_wr_data_r <= sram_wr_fifo_q[31:0];
            sram_wr_pending <= 1;
            sram_wr_started <= 0;
        end
    end else begin
        if (!sram_wr_started && sram_word_busy)
            sram_wr_started <= 1;
        else if (sram_wr_started && !sram_word_busy) begin
            sram_wr_pending <= 0;
            sram_wr_started <= 0;
        end
    end
end

wire sram_bridge_wr_active = sram_wr_pending | !sram_wr_fifo_empty;

// CRAM mux: Bridge FIFO drain has priority, then CRAM1 bridge reads, then CPU
wire cpu_cram_rd = cpu_psram_rd & cpu_psram_sel_cram;
wire cpu_cram_wr = cpu_psram_wr & cpu_psram_sel_cram;
wire cpu_cram_burst_rd = cpu_psram_burst_rd & cpu_psram_sel_cram;

assign psram_mux_rd = (cram1_rd_pending && !cram0_wr_pending) ? 1'b1 :
                      cram_bridge_active ? 1'b0 : cpu_cram_rd;
assign psram_mux_wr = cram0_wr_pending ? 1'b1 : (cram_bridge_active ? 1'b0 : cpu_cram_wr);
assign psram_mux_addr = cram0_wr_pending ? cram0_wr_addr_r :
                         cram1_rd_pending ? cram1_rd_addr_r :
                         cpu_psram_addr[21:0];
assign psram_mux_wdata = cram0_wr_pending ? cram0_wr_data_r : cpu_psram_wdata;
assign psram_mux_wstrb = cram0_wr_pending ? 4'b1111 : cpu_psram_wstrb;

// CRAM burst read routing (disabled when bridge active)
assign psram32_burst_rd  = cram_bridge_active ? 1'b0 : cpu_cram_burst_rd;
assign psram32_burst_len = cpu_psram_burst_len;


// SRAM mux: bridge write drain has priority, CPU when bridge idle
assign sram_word_rd = sram_bridge_wr_active ? 1'b0 : (cpu_psram_rd & cpu_psram_sel_sram);
assign sram_word_wr = sram_wr_pending ? 1'b1 : (sram_bridge_wr_active ? 1'b0 : (cpu_psram_wr & cpu_psram_sel_sram));
assign sram_word_addr = sram_wr_pending ? sram_wr_addr_r : cpu_psram_addr[21:0];
assign sram_word_wdata = sram_wr_pending ? sram_wr_data_r : cpu_psram_wdata;
assign sram_word_wstrb = sram_wr_pending ? 4'b1111 : cpu_psram_wstrb;

// Read data / busy / valid mux back to axi_psram_slave
// With psram_controller_32, CRAM0+CRAM1 are unified — no separate mux needed
assign cpu_psram_rdata = cpu_psram_sel_sram ? sram_word_rdata : psram_mux_rdata;

// Per-target busy mux: CRAM (unified) or SRAM
reg psram_busy_sel_sram;  // 0=cram, 1=sram
always @(posedge clk_ram_controller)
    if (!cpu_psram_busy)
        psram_busy_sel_sram <= cpu_psram_sel_sram;

assign cpu_psram_busy = psram_busy_sel_sram ? sram_word_busy :
                                              (cram_bridge_active | psram_mux_busy);

assign cpu_psram_rdata_valid = cpu_psram_sel_sram ? sram_word_rdata_valid :
                                                    psram_mux_rdata_valid;

// Burst read data / valid mux — single CRAM controller
assign cpu_psram_burst_rdata_valid = psram32_burst_rdata_valid;
assign cpu_psram_burst_rdata = psram32_burst_rdata;



//
// host/target command handler
//
    wire            reset_n_apf;
    wire    [31:0]  cmd_bridge_rd_data;

    wire reset_n = reset_n_apf & bcr_init_done;

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

// Cycling FSM: continuously write per-slot sizes to datatable
reg [3:0] save_dt_idx;
always @(posedge clk_74a) begin
    datatable_wren <= 1;
    datatable_addr <= 10'd15 + {6'd0, save_dt_idx[3:0]} * 10'd2;
    datatable_data <= save_sizes[save_dt_idx];
    save_dt_idx <= (save_dt_idx == 4'd9) ? 4'd0 : save_dt_idx + 4'd1;
end

// Shutdown handshake CDC
wire shutdown_pending_74a;
wire shutdown_ack_cpu;
wire shutdown_pending_cpu;
wire shutdown_ack_74a;

synch_3 sync_shutdown_pending(shutdown_pending_74a, shutdown_pending_cpu, clk_ram_controller);
synch_3 sync_shutdown_ack(shutdown_ack_cpu, shutdown_ack_74a, clk_74a);

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
assign video_rgb_clock = clk_core_12288;
assign video_rgb_clock_90 = clk_core_12288_90deg;
assign video_rgb = video_rgb_core;
assign video_de = vidout_de;
assign video_skip = vidout_skip;
assign video_vs = vidout_vs;
assign video_hs = vidout_hs;

    localparam  VID_V_BPORCH = 'd16;
    localparam  VID_V_ACTIVE = 'd240;
    localparam  VID_V_TOTAL = 'd512;
    localparam  VID_H_BPORCH = 'd40;
    localparam  VID_H_ACTIVE = 'd320;
    localparam  VID_H_TOTAL = 'd400;

    reg [15:0]  frame_count;

    reg [9:0]   x_count;
    reg [9:0]   y_count;

    reg [23:0]  vidout_rgb;
    reg         vidout_de, vidout_de_1;
    reg         vidout_skip;
    reg         vidout_vs;
    reg         vidout_hs, vidout_hs_1;

    // CPU to terminal interface signals
    wire        term_mem_valid;
    wire [31:0] term_mem_addr;
    wire [31:0] term_mem_wdata;
    wire [3:0]  term_mem_wstrb;
    wire [31:0] term_mem_rdata;
    wire        term_mem_ready;

    // Display mode and framebuffer address from CPU
    wire [1:0] display_mode;
    wire [2:0] color_mode;
    wire [24:0] fb_display_addr;

    // Stub wires for axi_periph_slave tile/sprite register ports (no-ops).
    wire        tile_enable_w;
    wire        tile_priority_w;
    wire [8:0]  tile_scroll_x_w;
    wire [7:0]  tile_scroll_y_w;
    wire        tilemap_wr_w;
    wire [10:0] tilemap_waddr_w;
    wire [15:0] tilemap_wdata_w;
    wire        tilechar_wr_w;
    wire [10:0] tilechar_waddr_w;
    wire [31:0] tilechar_wdata_w;
    wire        sprite_enable_w;
    wire        sat_wr_w;
    wire [5:0]  sat_idx_w;
    wire [1:0]  sat_field_w;
    wire [31:0] sat_wdata_w;
    wire        sprchar_wr_w;
    wire [10:0] sprchar_waddr_w;
    wire [31:0] sprchar_wdata_w;

    // VexiiRiscv CPU system — word-level bus routing
    cpu_system cpu (
        .clk(clk_cpu),
        .reset_n(reset_n),
        // SDRAM word-level master interface
        .m_sdram_rd(cpu_m_sdram_rd),
        .m_sdram_wr(cpu_m_sdram_wr),
        .m_sdram_addr(cpu_m_sdram_addr),
        .m_sdram_wdata(cpu_m_sdram_wdata),
        .m_sdram_wstrb(cpu_m_sdram_wstrb),
        .m_sdram_burst_len(cpu_m_sdram_burst_len),
        .m_sdram_burst_wr_len(cpu_m_sdram_burst_wr_len),
        .m_sdram_rdata(cpu_m_sdram_rdata),
        .m_sdram_busy(cpu_m_sdram_busy),
        .m_sdram_accepted(cpu_m_sdram_accepted),
        .m_sdram_rdata_valid(cpu_m_sdram_rdata_valid),
        .m_sdram_wr_data_next(cpu_m_sdram_wr_data_next),
        // PSRAM word-level master interface
        .m_psram_rd(cpu_psram_rd),
        .m_psram_wr(cpu_psram_wr),
        .m_psram_addr(cpu_psram_addr),
        .m_psram_wdata(cpu_psram_wdata),
        .m_psram_wstrb(cpu_psram_wstrb),
        .m_psram_rdata(cpu_psram_rdata),
        .m_psram_busy(cpu_psram_busy),
        .m_psram_rdata_valid(cpu_psram_rdata_valid),
        .m_psram_burst_rd(cpu_psram_burst_rd),
        .m_psram_burst_len(cpu_psram_burst_len),
        .m_psram_burst_rdata_valid(cpu_psram_burst_rdata_valid),
        .m_psram_burst_rdata(cpu_psram_burst_rdata),
        // Local peripheral word-level master interface
        .m_local_rd(cpu_m_local_rd),
        .m_local_wr(cpu_m_local_wr),
        .m_local_addr(cpu_m_local_addr),
        .m_local_wdata(cpu_m_local_wdata),
        .m_local_wstrb(cpu_m_local_wstrb),
        .m_local_burst_len(cpu_m_local_burst_len),
        .m_local_rdata(cpu_m_local_rdata),
        .m_local_rdata_valid(cpu_m_local_rdata_valid),
        .m_local_rdata_last(cpu_m_local_rdata_last),
        .m_local_wr_done(cpu_m_local_wr_done),
        .m_local_busy(cpu_m_local_busy)
    );

    // Peripheral slave (word-level)
    periph_slave periph (
        .clk(clk_cpu),
        .reset_n(reset_n),
        // Word-level slave interface
        .req_rd(cpu_m_local_rd),
        .req_wr(cpu_m_local_wr),
        .req_addr_in(cpu_m_local_addr),
        .req_wdata_in(cpu_m_local_wdata),
        .req_wstrb_in(cpu_m_local_wstrb),
        .req_burst_len_in(cpu_m_local_burst_len),
        .rsp_rdata(cpu_m_local_rdata),
        .rsp_rdata_valid(cpu_m_local_rdata_valid),
        .rsp_rdata_last(cpu_m_local_rdata_last),
        .rsp_wr_done(cpu_m_local_wr_done),
        .rsp_busy(cpu_m_local_busy),
        // CDC inputs
        .dataslot_allcomplete(dataslot_allcomplete && bridge_wr_idle),
        .vsync(vidout_vs),
        .cont1_key(p1_controls),
        .cont1_joy(p1_joypad),
        .cont1_trig(p1_trigger),
        .cont2_key(p2_controls),
        .cont2_joy(p2_joypad),
        .cont2_trig(p2_trigger),
        .target_dataslot_ack(target_dataslot_ack),
        .target_dataslot_done(target_dataslot_done_safe),
        .target_dataslot_err(target_dataslot_err),
        // Terminal interface
        .term_mem_valid(term_mem_valid),
        .term_mem_addr(term_mem_addr),
        .term_mem_wdata(term_mem_wdata),
        .term_mem_wstrb(term_mem_wstrb),
        .term_mem_rdata(term_mem_rdata),
        .term_mem_ready(term_mem_ready),
        // Display control
        .display_mode(display_mode),
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
        // OPM hardware interface
        .opl_write_req(opl_write_req),
        .opl_write_addr(opl_write_addr),
        .opl_write_data(opl_write_data),
        .opl_ack(opl_ack),
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
        // Tile engine
        .tile_enable(tile_enable_w),
        .tile_priority(tile_priority_w),
        .tile_scroll_x(tile_scroll_x_w),
        .tile_scroll_y(tile_scroll_y_w),
        .tilemap_wr(tilemap_wr_w),
        .tilemap_waddr(tilemap_waddr_w),
        .tilemap_wdata(tilemap_wdata_w),
        .tilechar_wr(tilechar_wr_w),
        .tilechar_waddr(tilechar_waddr_w),
        .tilechar_wdata(tilechar_wdata_w),
        // Sprite engine
        .sprite_enable(sprite_enable_w),
        .sat_wr(sat_wr_w),
        .sat_idx(sat_idx_w),
        .sat_field(sat_field_w),
        .sat_wdata(sat_wdata_w),
        .sprchar_wr(sprchar_wr_w),
        .sprchar_waddr(sprchar_waddr_w),
        .sprchar_wdata(sprchar_wdata_w),
        // Shutdown handshake
        .shutdown_pending(shutdown_pending_cpu),
        .shutdown_ack(shutdown_ack_cpu),
        // DMA engine
        .dma_src(dma_src_w),
        .dma_dst(dma_dst_w),
        .dma_len(dma_len_w),
        .dma_start(dma_start_w),
        .dma_fill_mode(dma_fill_mode_w),
        .dma_busy(dma_busy_w),
        .adma_enable(adma_enable),
        .adma_ring_base(adma_ring_base),
        .adma_ring_size_log(adma_ring_size_log),
        .adma_ring_wptr(adma_ring_wptr),
        .adma_ring_rptr(adma_ring_rptr)
    );

    // DMA engine — memory-to-memory copy/fill via word_sdram_arbiter M1
    wire [31:0] dma_src_w, dma_dst_w, dma_len_w;
    wire        dma_start_w, dma_fill_mode_w, dma_busy_w;
    wire        dma_m_rd, dma_m_wr;
    wire [23:0] dma_m_addr;
    wire [31:0] dma_m_wdata;
    wire [3:0]  dma_m_wstrb;
    wire [3:0]  dma_m_burst_len, dma_m_burst_wr_len;
    wire [31:0] dma_m_rdata;
    wire        dma_m_busy, dma_m_accepted, dma_m_rdata_valid, dma_m_wr_data_next;

    dma_engine dma_inst (
        .clk(clk_cpu),
        .reset_n(reset_n),
        .src_addr(dma_src_w),
        .dst_addr(dma_dst_w),
        .length(dma_len_w),
        .start(dma_start_w),
        .fill_mode(dma_fill_mode_w),
        .busy(dma_busy_w),
        .m_rd(dma_m_rd),             .m_wr(dma_m_wr),
        .m_addr(dma_m_addr),         .m_wdata(dma_m_wdata),
        .m_wstrb(dma_m_wstrb),
        .m_burst_len(dma_m_burst_len), .m_burst_wr_len(dma_m_burst_wr_len),
        .m_rdata(dma_m_rdata),       .m_busy(dma_m_busy),
        .m_accepted(dma_m_accepted), .m_rdata_valid(dma_m_rdata_valid),
        .m_wr_data_next(dma_m_wr_data_next)
    );

    // Bridge master (must stay alive during reset for APF save flush & data load)
    bridge_master bridge_m (
        .clk(clk_cpu),
        .reset_n(1'b1),
        .fifo_q(bridge_wr_fifo_q),
        .fifo_empty(bridge_wr_fifo_empty),
        .fifo_rdreq(bridge_wr_fifo_drain),
        .bridge_rd_req(1'b0),
        .bridge_rd_addr(bridge_addr_ram_clk[25:2]),
        .bridge_rd_data(bridge_axi_rd_data),
        .bridge_rd_done(bridge_axi_rd_done),
        .m_rd(bridge_m_rd),             .m_wr(bridge_m_wr),
        .m_addr(bridge_m_addr),         .m_wdata(bridge_m_wdata),
        .m_wstrb(bridge_m_wstrb),
        .m_burst_len(bridge_m_burst_len), .m_burst_wr_len(bridge_m_burst_wr_len),
        .m_rdata(bridge_m_rdata),       .m_busy(bridge_m_busy),
        .m_accepted(bridge_m_accepted), .m_rdata_valid(bridge_m_rdata_valid),
        .idle(bridge_m_idle),
        .wr_idle(bridge_m_wr_idle)
    );

    // Word-level SDRAM arbiter (must stay alive during reset for APF save flush)
    // Replaces: axi_sdram_arbiter + axi_sdram_slave + pulse adapter
    wire [31:0] arb_sdram_wdata;
    wire [31:0] arb_sdram_wdata_direct;  // Combinational path for burst writes
    wire [3:0]  arb_sdram_wstrb_direct;
    word_sdram_arbiter sdram_arb (
        .clk(clk_cpu),
        .reset_n(1'b1),
        // M0: Audio DMA (highest priority, read-only)
        .m0_rd(adma_m_rd),         .m0_addr(adma_m_addr),
        .m0_burst_len(adma_m_burst_len),
        .m0_rdata(adma_m_rdata),   .m0_busy(adma_m_busy),
        .m0_accepted(adma_m_accepted), .m0_rdata_valid(adma_m_rdata_valid),
        // M1: DMA engine
        .m1_rd(dma_m_rd),           .m1_wr(dma_m_wr),
        .m1_addr(dma_m_addr),       .m1_wdata(dma_m_wdata),
        .m1_wstrb(dma_m_wstrb),
        .m1_burst_len(dma_m_burst_len), .m1_burst_wr_len(dma_m_burst_wr_len),
        .m1_rdata(dma_m_rdata),     .m1_busy(dma_m_busy),
        .m1_accepted(dma_m_accepted), .m1_rdata_valid(dma_m_rdata_valid),
        .m1_wr_data_next(dma_m_wr_data_next),
        // M2: CPU
        .m2_rd(cpu_m_sdram_rd),      .m2_wr(cpu_m_sdram_wr),
        .m2_addr(cpu_m_sdram_addr),  .m2_wdata(cpu_m_sdram_wdata),
        .m2_wstrb(cpu_m_sdram_wstrb),
        .m2_burst_len(cpu_m_sdram_burst_len), .m2_burst_wr_len(cpu_m_sdram_burst_wr_len),
        .m2_rdata(cpu_m_sdram_rdata), .m2_busy(cpu_m_sdram_busy),
        .m2_accepted(cpu_m_sdram_accepted), .m2_rdata_valid(cpu_m_sdram_rdata_valid),
        .m2_wr_data_next(cpu_m_sdram_wr_data_next),
        // M3: Bridge (lowest priority)
        .m3_rd(bridge_m_rd),         .m3_wr(bridge_m_wr),
        .m3_addr(bridge_m_addr),     .m3_wdata(bridge_m_wdata),
        .m3_wstrb(bridge_m_wstrb),
        .m3_burst_len(bridge_m_burst_len), .m3_burst_wr_len(bridge_m_burst_wr_len),
        .m3_rdata(bridge_m_rdata),   .m3_busy(bridge_m_busy),
        .m3_accepted(bridge_m_accepted), .m3_rdata_valid(bridge_m_rdata_valid),
        .m3_wr_data_next(bridge_m_wr_data_next),
        // io_sdram word interface
        .sdram_rd(ram1_word_rd),     .sdram_wr(ram1_word_wr),
        .sdram_addr(ram1_word_addr), .sdram_wdata(arb_sdram_wdata),
        .sdram_wstrb(ram1_word_wstrb),
        .sdram_burst_len(ram1_word_burst_len),
        .sdram_burst_wr_len(ram1_word_burst_wr_len),
        .sdram_rdata(ram1_word_q),   .sdram_busy(ram1_word_busy),
        .sdram_rdata_valid(ram1_word_q_valid),
        .sdram_wr_data_next(ram1_word_wr_data_next),
        .sdram_wdata_direct(arb_sdram_wdata_direct),
        .sdram_wstrb_direct(arb_sdram_wstrb_direct)
    );
    assign ram1_word_data = arb_sdram_wdata;

    // PSRAM: cpu_system connects directly to psram_controller (no AXI slave wrapper)

    // Terminal display (40x30 characters, 320x240 pixels)
    wire [23:0] terminal_pixel_color;
    wire        terminal_pixel_opaque;

    text_terminal terminal (
        .clk(clk_core_12288),
        .clk_cpu(clk_cpu),
        .reset_n(reset_n),
        .pixel_x({1'b0, visible_x[9:1]}),  // Halve doubled-X CRT resolution back to 320
        .pixel_y(visible_y),
        .pixel_color(terminal_pixel_color),
        .pixel_opaque(terminal_pixel_opaque),
        .mem_valid(term_mem_valid),
        .mem_addr(term_mem_addr),
        .mem_wdata(term_mem_wdata),
        .mem_wstrb(term_mem_wstrb),
        .mem_rdata(term_mem_rdata),
        .mem_ready(term_mem_ready)
    );

    // Line start signal for video scanout
    reg line_start;
    always @(posedge clk_core_12288) begin
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
        .clk_video(clk_core_12288),
        .reset_n(reset_n),
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


        // ---  CRT 15.7kHz / 60Hz Parameters ---
    localparam CRT_V_TOTAL  = CRT_V_SYNC + CRT_V_BPORCH + CRT_V_ACTIVE + CRT_V_FPORCH;
    localparam CRT_V_SYNC   = 3;
    localparam CRT_V_BPORCH = 15; //15;
    localparam CRT_V_FPORCH = 4; //4;
    localparam CRT_V_ACTIVE = 240;
    localparam CRT_H_TOTAL  = CRT_H_SYNC + CRT_H_BPORCH + CRT_H_ACTIVE + CRT_H_FPORCH;
    localparam CRT_H_SYNC   = 58;
    localparam CRT_H_BPORCH = 62;
    localparam CRT_H_FPORCH = 20;
    localparam CRT_H_ACTIVE = 640;
    reg crt_hs, crt_vs, crt_de;
    reg crt_hblank, crt_vblank;

    wire [9:0]  visible_x = x_count - CRT_H_SYNC - CRT_H_BPORCH;
    wire [9:0]  visible_y = y_count - CRT_V_SYNC - CRT_V_BPORCH;

always @(posedge clk_core_12288 or negedge reset_n) begin

    if(~reset_n) begin

        x_count <= 0;
        y_count <= 0;

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
            if(y_count == CRT_V_TOTAL-1) begin
                y_count <= 0;
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

                // Display mode: 0=terminal, 1=framebuffer, 2=overlay (white text over FB)
                if (display_mode == 2'd0)
                    vidout_rgb <= terminal_pixel_color;
                else if (display_mode == 2'd2 && terminal_pixel_color == 24'hFFFFFF)
                    vidout_rgb <= 24'hFFFFFF;
                else
                    vidout_rgb <= framebuffer_pixel_color;
            end
        end
    end
end

//
// Link MMIO peripheral
//
link_mmio #(
    .CLK_HZ(100000000),
    .SCK_HZ(256000),
    .POLL_HZ(3000),
    .FIFO_DEPTH(256)
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
    .link_sd_oe(link_sd_oe)
);

//
// OPL3 hardware synthesizer (Greg Taylor's opl3_fpga)
//
opl3_wrapper opl3 (
    .clk            (clk_cpu),
    .clk_opl        (clk_core_12288),
    .reset_n        (reset_n),
    .opl_write_req  (opl_write_req),
    .opl_write_addr (opl_write_addr),
    .opl_write_data (opl_write_data),
    .opl_ack            (opl_ack),
    .opl_audio_out      (opl_audio_out),
    .opl_sample_toggle  (opl_sample_toggle)
);

//
// Audio output (dcfifo + I2S) with OPL3 mixing
//
// Audio DMA control signals
wire        adma_enable;
wire [31:0] adma_ring_base;
wire [12:0] adma_ring_size_log;
wire [12:0] adma_ring_wptr;
wire [12:0] adma_ring_rptr;
wire        adma_sample_wr;
wire [31:0] adma_sample_data;
wire        adma_m_rd;
wire [23:0] adma_m_addr;
wire [3:0]  adma_m_burst_len;
wire [31:0] adma_m_rdata;
wire        adma_m_busy, adma_m_accepted, adma_m_rdata_valid;

wire        audio_mux_wr = adma_enable ? adma_sample_wr : audio_sample_wr;
wire [31:0] audio_mux_data = adma_enable ? adma_sample_data : audio_sample_data;

audio_output audio_out (
    .clk_sys      (clk_cpu),
    .clk_audio    (clk_core_12288),
    .reset_n      (reset_n),

    .sample_wr    (audio_mux_wr),
    .sample_data  (audio_mux_data),
    .fifo_level   (audio_fifo_level),
    .fifo_full    (audio_fifo_full),

    .opl_audio_in     (opl_audio_out),
    .opl_toggle_in    (opl_sample_toggle),

    .audio_mclk   (audio_mclk),
    .audio_lrck   (audio_lrck),
    .audio_dac    (audio_dac)
);

audio_dma adma (
    .clk(clk_cpu), .reset_n(reset_n),
    .enable(adma_enable),
    .ring_base(adma_ring_base),
    .ring_size_log(adma_ring_size_log),
    .ring_wptr(adma_ring_wptr),
    .ring_rptr(adma_ring_rptr),
    .fifo_level(audio_fifo_level),
    .fifo_full(audio_fifo_full),
    .sample_wr(adma_sample_wr),
    .sample_data(adma_sample_data),
    .m_rd(adma_m_rd),           .m_addr(adma_m_addr),
    .m_burst_len(adma_m_burst_len),
    .m_rdata(adma_m_rdata),     .m_busy(adma_m_busy),
    .m_accepted(adma_m_accepted), .m_rdata_valid(adma_m_rdata_valid)
);


///////////////////////////////////////////////


    wire    clk_core_12288;
    wire    clk_core_12288_90deg;
    wire    clk_core_49152;
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
    .outclk_1       ( clk_core_12288_90deg ),

    .outclk_2       ( clk_core_49152),
    .outclk_3       ( ),
    .outclk_4       ( ),

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

// Drive CRAM CLK pins from 105MHz PLL output after BCR init completes.
// PLL clock always on (PocketQuake confirmed: BCR config works with clock running)
assign cram0_clk = clk_cram;
assign cram1_clk = clk_cram;

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
    .burst_wr_direct_data ( arb_sdram_wdata_direct ),
    .burst_wr_direct_strb ( arb_sdram_wstrb_direct )
);



endmodule
