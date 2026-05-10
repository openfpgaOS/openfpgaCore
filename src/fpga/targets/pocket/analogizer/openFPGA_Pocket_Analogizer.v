//This module encapsulates all Analogizer adapter signals
// Original work by @RndMnkIII. 
// Date: 05/2024 
// Releases: 
// 1.0 Initial RGBS output mode
// 1.1 Added SOG modes: RGsB, YPbPt
// 1.2 Added Mike Simon Y/C module, Scandoubler SVGA Mist module.     

// *** Analogizer R.2 adapter ***
// * WHEN SOG SWITCH IS IN ON POSITION, OUTPUTS CSYNC ON G CHANNEL
// # WHEN YPbPr VIDEO OUTPUT IS SELECTED, Y->G, Pr->R, Pb->B
//Pin mappings:                                               VGA CONNECTOR                                                                                          USB3 TYPE A FEMALE CONNECTOR (SNAC)
//                        ______________________________________________________________________________________________________________________________________________________________________________________________________                             
//                       /                              VS  HS          R#  G*# B#                                                                  1      2       3       4      5       6       7       8       9              \
//                       |                              |   |           |   |   |                                                                 VBUS   D-      D+      GND     RX-     RX+     GND_D   TX-     TX+             |
//FUNCTION:              |                              |   |           |   |   |                                                                 +5V    OUT1    OUT2    GND     IO3     IN4     IO5     IO6     IN7             |
//                       |  A                           |   |           |   |   |                                                                          ^       ^              ^       |       ^       ^       |              |
//                       |  N             SOG           |   |           |   |   |                                                                          |       |              V       V       V       V       V              |
//                       |  A           -------         |   |           |   |   |                                                                                                                                                |                              
//                       |  O    OFF   |   S   |--GND   |   |         +------------+                                                                                                                                             |
//                       |  L          |   W   |        |   |   SYNC  |            |                                                                                                                                             |            
//  PIN DIR:             |  G          |   I   +--------------------->|            |---------------------------------------------------------------------------------------------------------+                                   |
//  ^ OUTPUT             |  I          |   T   |        |   |         |  RGB DAC   |                                                                                                         |                                   |
//  V INPUT              |  Z          |   C   |        |   |         |            |===================================================================++                                    |                                   |
//                       |  E    ON ===|   H   |--------+   |         +------------+                                                                   ||                                    |                                   |
//                       |  R           -------         |   |            ||  |   | /BLANK                                                              ||                                    |                                   |         
//                       |                              |   +--------+   ||  |   +------------------------------------------------------------------+  ||                                    |                                   |                                  |
//                       |  R                           +------+     |   ||  +===============================++                                     |  ||                                    |                                   |
//                       |  2                                  |     |   ||                                  ||                                     |  ||                                    |                                   |
//                       |     CONF.B        IO5V       ---    |     |   \\================================  \\================================     |  \\================================   VID               IO3^  IO6^         |  
//                       |     CONF.A   IN4  ---  IN7   IO3V   VS    HS    R0    R1    R2    R3    R4    R5    G0    G1    G2    G3    G4    G5   /BLK   B0    B1    B2    B3    B4    B5   CLK  OUT1   OUT2  IO5^  IO6V         |  
//                       |      __3.3V__ |___ | __ |_____|_____|_____|_____|_____|_____|_____|_____|_____|_____|_____|_____|_____|_____|_____|_____|_____|_____|_____|_____|_____|_____|_____|_____|_____|_____|_____^__GND__    |                                
//POCKET                 |     /         V    V    V     V     ^     ^     ^     ^     ^     ^     ^     ^     ^     ^     ^     ^     ^     ^     ^     ^     ^     ^     ^     ^     ^     ^     ^     ^     ^     V       \   | 
//CARTRIDGE PIN #:       \____|     1    2    3    4     5     6     7     8     9    10    11    12    13    14    15    16    17    18    19    20    21    22    23    24    25    26    27    28    29    30    31   32  |___/
//                             \_________|____|____|_____|_____|_____|_____|_____|_____|_____|_____|_____|_____|_____|_____|_____|_____|_____|_____|_____|_____|_____|_____|_____|_____|_____|_____|_____|_____|_____|_______/
//Pocket Pin Name:                       |    |    |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     | 
//cart_tran_bank0[7] --------------------+    |    |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     | 
//cart_tran_bank0[6] -------------------------+    |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |
//cart_tran_bank0[5] ------------------------------+     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |
//cart_tran_bank0[4] ------------------------------------+     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |
//cart_tran_bank3[0] ------------------------------------------+     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     | 
//cart_tran_bank3[1] ------------------------------------------------+     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     
//cart_tran_bank3[2] ------------------------------------------------------+     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |
//cart_tran_bank3[3] ------------------------------------------------------------+     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |
//cart_tran_bank3[4] ------------------------------------------------------------------+     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |
//cart_tran_bank3[5] ------------------------------------------------------------------------+     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |
//cart_tran_bank3[6] ------------------------------------------------------------------------------+     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |
//cart_tran_bank3[7] ------------------------------------------------------------------------------------+     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |
//------------------                                                                                           |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |
//cart_tran_bank2[0] ------------------------------------------------------------------------------------------+     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     
//cart_tran_bank2[1] ------------------------------------------------------------------------------------------------+     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |
//cart_tran_bank2[2] ------------------------------------------------------------------------------------------------------+     |     |     |     |     |     |     |     |     |     |     |     |     |     |     |
//cart_tran_bank2[3] ------------------------------------------------------------------------------------------------------------+     |     |     |     |     |     |     |     |     |     |     |     |     |     |
//cart_tran_bank2[4] ------------------------------------------------------------------------------------------------------------------+     |     |     |     |     |     |     |     |     |     |     |     |     |
//cart_tran_bank2[5] ------------------------------------------------------------------------------------------------------------------------+     |     |     |     |     |     |     |     |     |     |     |     |
//cart_tran_bank2[6] ------------------------------------------------------------------------------------------------------------------------------+     |     |     |     |     |     |     |     |     |     |     |
//cart_tran_bank2[7] ------------------------------------------------------------------------------------------------------------------------------------+     |     |     |     |     |     |     |     |     |     |                                   
//------------------                                                                                                                                           |     |     |     |     |     |     |     |     |     |
//cart_tran_bank1[0] ------------------------------------------------------------------------------------------------------------------------------------------+     |     |     |     |     |     |     |     |     |
//cart_tran_bank1[1] ------------------------------------------------------------------------------------------------------------------------------------------------+     |     |     |     |     |     |     |     |
//cart_tran_bank1[2] ------------------------------------------------------------------------------------------------------------------------------------------------------+     |     |     |     |     |     |     |
//cart_tran_bank1[3] ------------------------------------------------------------------------------------------------------------------------------------------------------------+     |     |     |     |     |     |
//cart_tran_bank1[4] ------------------------------------------------------------------------------------------------------------------------------------------------------------------+     |     |     |     |     |
//cart_tran_bank1[5] ------------------------------------------------------------------------------------------------------------------------------------------------------------------------+     |     |     |     |
//cart_tran_bank1[6] ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+     |     |     |
//cart_tran_bank1[7] ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+     |     |
//cart_tran_pin30    ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+     | 
//cart_tran_pin31    ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+
`default_nettype none
`timescale 1ns / 1ps

module openFPGA_Pocket_Analogizer #(parameter MASTER_CLK_FREQ=50_000_000, parameter LINE_LENGTH) (
	input wire i_clk,
    input wire i_rst,
	input wire i_ena,
	//Video interface
	input wire video_clk,
	input wire [3:0] analog_video_type,
	input wire [7:0] R,
	input wire [7:0] G,
	input wire [7:0] B,
	input wire Hblank,
	input wire Vblank,
	input wire BLANKn,
	input wire Hsync,
	input wire Vsync,
	input wire Csync,
	//Video Y/C Encoder interface
	input wire [39:0] CHROMA_PHASE_INC,
	input wire PALFLAG,
	//Video SVGA Scandoubler interface
	input wire ce_pix,
	input wire scandoubler, //logic for disable/enable the scandoubler
	input wire [2:0] fx, //0 disable, 1 scanlines 25%, 2 scanlines 50%, 3 scanlines 75%, 4 hq2x
	// SNAC cart pin pass-through (driven by CPU SNAC shifter/GPIO)
	input wire [7:4] snac_bank0_out,     // CPU-driven output values for bank0[7:4]
	input wire       snac_bank0_dir,     // 1=output, 0=input (whole nibble)
	input wire [7:6] snac_bank1_76_out,  // CPU-driven output values for bank1[7:6]
	input wire       snac_enable,        // keep OUT1/OUT2 alive even when analog video is off
	input wire       snac_pin30_out,
	input wire       snac_pin30_dir,
	input wire       snac_pin31_out,
	input wire       snac_pin31_dir,
	// Pocket Analogizer IO interface: video output on bank1-3.
	inout   wire    [7:0]   cart_tran_bank2,
	output  wire            cart_tran_bank2_dir,
	inout   wire    [7:0]   cart_tran_bank3,
	output  wire            cart_tran_bank3_dir,
	inout   wire    [7:0]   cart_tran_bank1,
	output  wire            cart_tran_bank1_dir
);
	// SNAC pins are driven by the CPU SNAC shifter/GPIO.
	wire [7:4] CART_BK0_OUT    = snac_bank0_out;
	wire       CART_BK0_DIR    = snac_bank0_dir;
	wire [7:6] CART_BK1_OUT_P76 = snac_bank1_76_out;
	wire       CART_PIN30_OUT  = snac_pin30_out;
	wire       CART_PIN30_DIR  = snac_pin30_dir;
	wire       CART_PIN31_OUT  = snac_pin31_out;
	wire       CART_PIN31_DIR  = snac_pin31_dir;

	//Choose type of analog video type of signal
		reg [5:0] Rout, Gout, Bout;
		reg HsyncOut, VsyncOut, BLANKnOut;
		wire [7:0] Yout, PrOut, PbOut;
		wire [5:0] R_Sd, G_Sd, B_Sd;
		wire Hsync_Sd, Vsync_Sd;
		wire Hblank_Sd, Vblank_Sd;
		wire BLANKn_SD = ~(Hblank_Sd || Vblank_Sd);
		wire scandoubler_mode = (analog_video_type == 4'h5) ||
		                         (analog_video_type == 4'h6) ||
		                         (analog_video_type == 4'h7) ||
		                         (analog_video_type == 4'hD) ||
		                         (analog_video_type == 4'hE) ||
		                         (analog_video_type == 4'hF);

	always @(*) begin
		case(analog_video_type)
			4'h0, 4'h8: begin //RGBS
				Rout = R[7:2]&{6{BLANKn}};
				Gout = G[7:2]&{6{BLANKn}};
				Bout = B[7:2]&{6{BLANKn}};
				HsyncOut = Csync;
				VsyncOut = 1'b1;
				BLANKnOut = BLANKn;
			end
			4'h3, 4'h4, 4'hB, 4'hC: begin// Y/C Modes works for Analogizer R1, R2 Adapters
				Rout = yc_o[23:18];
				Gout = yc_o[15:10];
				Bout = yc_o[7:2];
				HsyncOut = yc_cs;
				VsyncOut = 1'b1;
				BLANKnOut = 1'b1;
			end
			4'h1, 4'h9: begin //RGsB
				Rout = R[7:2]&{6{BLANKn}};
				Gout = G[7:2]&{6{BLANKn}};
				Bout = B[7:2]&{6{BLANKn}};
				HsyncOut = 1'b1;
				VsyncOut = Csync; //to DAC SYNC pin, SWITCH SOG ON
				BLANKnOut = BLANKn;
			end
			4'h2, 4'hA: begin //YPbPr
				Rout = PrOut[7:2];
				Gout = Yout[7:2];
				Bout = PbOut[7:2];
				HsyncOut = 1'b1;
				VsyncOut = YPbPr_sync; //to DAC SYNC pin, SWITCH SOG ON
				BLANKnOut = 1'b1; //ADV7123 needs this
			end
			4'h5, 4'h6, 4'h7, 4'hD, 4'hE, 4'hF: begin //Scandoubler modes
				Rout = R_Sd;
				Gout = G_Sd;
				Bout = B_Sd;
				HsyncOut = Hsync_Sd;
				VsyncOut = Vsync_Sd;
				BLANKnOut = BLANKn_SD;
			end
			default: begin
				Rout = 6'h0;
				Gout = 6'h0;
				Bout = 6'h0;
				HsyncOut = Hsync;
				VsyncOut = 1'b1;
				BLANKnOut = BLANKn;
			end
		endcase
	end


	//First Stage: video fix
	// wire hs_fix,vs_fix;
	// sync_fix sync_v(video_clk, HSync, hs_fix);
	// sync_fix sync_h(video_clk, VSync, vs_fix);

	// reg [DW-1:0] RGB_fix;

	// reg CE_fix,HS_fix,VS_fix,HBL_fix,VBL_fix;
	// always @(posedge video_clk) begin
	// 	reg old_ce;
	// 	old_ce <= ce_pix;
	// 	CE_fix <= 0;
	// 	if(~old_ce & ce_pix) begin
	// 		CE_fix <= 1;
	// 		HS_fix <= hs_fix;
	// 		if(~HS_fix & hs_fix) VS_fix <= vs_fix;

	// 		RGB_fix <= {R,G,B};
	// 		HBL_fix <= HBlank;
	// 		if(HBL_fix & ~HBlank) VBL_fix <= VBlank;
	// 	end
	// end

	// //Csync generation
	// wire CSYNC_fix;
	// csync csync_fix(video_clk, HS_fix, VS_fix, CSYNC_fix);

	wire YPbPr_sync, YPbPr_blank;
	vga_out ybpr_video
	(
		.clk(video_clk),
		.ypbpr_en(1'b1),
		.csync(Csync),
		.de(BLANKn),
		.din({R&{8{BLANKn}},G&{8{BLANKn}},B&{8{BLANKn}}}), //NES specific override, because not zero color data while blanking period.
		.dout({PrOut,Yout,PbOut}),
		.csync_o(YPbPr_sync),
		.de_o(YPbPr_blank)
	);

		wire [23:0] yc_o;
		//wire yc_hs, yc_vs,
		wire yc_cs;
	yc_out yc_out
	(
		.clk(i_clk),
		.PHASE_INC(CHROMA_PHASE_INC),
		.PAL_EN(PALFLAG),
		.hsync(Hsync),
		.vsync(Vsync),
		.csync(Csync),
    	.din({R&{8{BLANKn}},G&{8{BLANKn}},B&{8{BLANKn}}}),
		.dout(yc_o),
		.hsync_o(),
		.vsync_o(),
		.csync_o(yc_cs)
	);

	wire sd_pixel_ena;
	scandoubler #(
		.HCNT_WIDTH(10),
		.COLOR_DEPTH(6),
		.OUT_COLOR_DEPTH(6)
	) sc_video (
		.clk_sys(i_clk),
		.bypass(1'b0),
		.ce_divider(3'd3),
		.pixel_ena(sd_pixel_ena),
		.scanlines(fx[1:0]),
		.hb_in(Hblank),
		.vb_in(Vblank),
		.hs_in(Hsync),
		.vs_in(Vsync),
		.r_in(R[7:2] & {6{BLANKn}}),
		.g_in(G[7:2] & {6{BLANKn}}),
		.b_in(B[7:2] & {6{BLANKn}}),
		.hb_out(Hblank_Sd),
		.vb_out(Vblank_Sd),
		.hs_out(Hsync_Sd),
		.vs_out(Vsync_Sd),
		.r_out(R_Sd),
		.g_out(G_Sd),
		.b_out(B_Sd)
	);

	wire cart_video_clk = scandoubler_mode ? sd_pixel_ena : video_clk;

	// Tri-state buffers for video output cart pins (bank1-3)
	// Bank0, pin30, pin31 now driven by core_top SNAC/UART mux.
	//BK3 — Video: R[5:0], Hsync, Vsync
	assign cart_tran_bank3         = i_rst | ~i_ena ? 8'hzz : {Rout[5:0],HsyncOut,VsyncOut};
	assign cart_tran_bank3_dir     = i_rst | ~i_ena ? 1'b0  : 1'b1;
	//BK2 — Video: B[0], /BLANK, G[5:0]
	assign cart_tran_bank2         = i_rst | ~i_ena ? 8'hzz : {Bout[0],BLANKnOut,Gout[5:0]};
	assign cart_tran_bank2_dir     = i_rst | ~i_ena ? 1'b0  : 1'b1;
	//BK1 — Video: [5:0]=B[5:1]+clk, [7:6]=SNAC OUT1/OUT2.
	// The Pocket exposes one direction control for all of bank1.  SNAC
	// needs OUT1/OUT2 even when analog video is disabled, so drive safe
	// zeros on the video bits in SNAC-only mode.
	wire bank1_drive = i_ena | snac_enable;
	assign cart_tran_bank1         = i_rst | ~bank1_drive ? 8'hzz :
	                                 {snac_enable ? CART_BK1_OUT_P76 : 2'b00,
	                                  i_ena ? {cart_video_clk,Bout[5:1]} : 6'b0};
	assign cart_tran_bank1_dir     = i_rst | ~bank1_drive ? 1'b0  : 1'b1;
endmodule
