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

// EN_YPBPR / EN_YC gate the analog *output encoders*.  They default to 1 so
// every existing consumer keeps full functionality; a resource-constrained
// core that only outputs RGBS/RGsB/VGA (scandoubler) can set them to 0 to drop
// the YPbPr (vga_out, ~230-400 ALMs) and Y/C composite (yc_out, ~400-490 ALMs)
// encoders entirely.  When 0 the corresponding mode-mux inputs are tied off, so
// selecting a YPbPr/Y-C video_type just outputs black instead of the encoder.
module openFPGA_Pocket_Analogizer #(
	parameter MASTER_CLK_FREQ=50_000_000,
	parameter EN_YPBPR=1,
	parameter EN_YC=1
) (
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
	//Video SVGA Scandoubler interface (internal scandoubler re-instated)
	input wire ce_pix,
	input wire scandoubler, //logic for disable/enable the scandoubler
	input wire [2:0] fx, //0 disable, 1 scanlines 25%, 2 50%, 3 75%, 4 hq2x
	//Video Y/C Encoder interface
	input wire [39:0] CHROMA_PHASE_INC,
	input wire PALFLAG,
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
	// RndMnkIII Analogizer/SNAC physical pinout.  The OS-side raw shifter
	// or optional PSX poller drives these pass-through pins.
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
	generate if (EN_YPBPR) begin : g_ypbpr
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
	end else begin : g_no_ypbpr
		assign {PrOut,Yout,PbOut} = 24'd0;
		assign YPbPr_sync  = 1'b0;
		assign YPbPr_blank = 1'b0;
	end endgenerate

		wire [23:0] yc_o;
		//wire yc_hs, yc_vs,
		wire yc_cs;
	generate if (EN_YC) begin : g_yc
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
	end else begin : g_no_yc
		assign yc_o  = 24'd0;
		assign yc_cs = 1'b0;
	end endgenerate

	// Internal scandoubler (upstream Analogizer): when `scandoubler`=1 it
	// line-doubles a 240p source to 480p; when `scandoubler`=0 it BYPASSES
	// (passes the input straight through), for a source that is already at the
	// output resolution (openfpgaOS drives the Analogizer from the 640x480/31 kHz
	// LCD video, so it bypasses).  pixel_ena is left UNCONNECTED and the DAC
	// clock at the cart pin is the real PLL clock video_clk (see cart_video_clk
	// below) — NOT a logic-derived enable.  Routing a logic enable onto the DAC
	// clock pin is off the clock network with the wrong duty cycle: it sims fine
	// but gives a dark VGA on hardware.
	scandoubler #(
		.HCNT_WIDTH(10),
		.COLOR_DEPTH(6),
		.OUT_COLOR_DEPTH(6)
	) sc_video (
		.clk_sys(i_clk),
		.bypass(~scandoubler),
		.ce_divider(3'd3),
		.pixel_ena(),
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

	// DAC clock = video_clk (a real PLL clock), exactly as the upstream
	// Analogizer (cart_tran_bank1 = {.., video_clk, ..}).  core_top supplies
	// video_clk = clk_vid (24.576 MHz) = the scandoubled 480p output rate, so
	// this PLL clock samples the i_clk-domain scandoubler output 1:1 and is
	// already SDC-constrained.  scandoubler_mode no longer gates the pin.
	wire cart_video_clk = video_clk;

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
