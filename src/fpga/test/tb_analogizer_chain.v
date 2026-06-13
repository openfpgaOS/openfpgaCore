//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

//
// Combined scanout + Analogizer -> cart-pins testbench (640x480 VGA).
//
// Wires the real video_CRT_scanout_indexed_BRAM + the real
// openFPGA_Pocket_Analogizer exactly as core_top now does: the Analogizer is
// fed the 480p LCD video directly (the scanout's pixel_color + crt_* sync), the
// scandoubler is BYPASSED (scandoubler=0, since the LCD is already 480p/31 kHz),
// and the cart-pin DAC clock is video_clk = clk_vid.  The scanout's dedicated
// analog path is dormant (analog_timing=480p, analog_* outputs unconnected → pruned).
// The encoders are gated (EN_YPBPR/EN_YC=0).  Observes the cart pins: do
// hsync/vsync + the DAC clock TOGGLE, and is RGB present during active?
//
`default_nettype none

module tb_analogizer_chain (
    input  wire clk,            // 49.152 MHz (i_clk / clk_analog / clk_sdram)
    input  wire reset_n,
    output wire [7:0] cart_bank1,
    output wire [7:0] cart_bank2,
    output wire [7:0] cart_bank3
);
    // ----- clocks -----
    reg clk_vid;                // clk/2 = 24.576 MHz (LCD + sd_video_clk)
    always @(posedge clk or negedge reset_n)
        if (!reset_n) clk_vid <= 1'b0; else clk_vid <= ~clk_vid;

    reg [1:0] c12; reg clk_12288;   // clk/4 = 12.288 MHz (video_clk)
    always @(posedge clk or negedge reset_n)
        if (!reset_n) begin c12 <= 2'd0; clk_12288 <= 1'b0; end
        else begin c12 <= c12 + 2'd1; if (c12 == 2'd1) clk_12288 <= ~clk_12288; end

    reg [1:0] ce_cnt; reg analog_ce_pix;
    always @(posedge clk or negedge reset_n)
        if (!reset_n) begin ce_cnt <= 2'd0; analog_ce_pix <= 1'b0; end
        else begin ce_cnt <= ce_cnt + 2'd1; analog_ce_pix <= (ce_cnt == 2'd0); end

    // ----- 480p LCD raster (H_TOTAL 780, V_TOTAL 525) in clk_vid -----
    localparam [9:0] H_TOTAL = 10'd780;
    localparam [9:0] V_TOTAL = 10'd525;
    reg [9:0] x_count, y_count;
    reg line_start;
    always @(posedge clk_vid or negedge reset_n) begin
        if (!reset_n) begin x_count<=0; y_count<=0; line_start<=0; end
        else begin
            line_start <= (x_count == 10'd0);
            x_count <= x_count + 10'd1;
            if (x_count == H_TOTAL-10'd1) begin
                x_count <= 10'd0;
                y_count <= (y_count >= V_TOTAL-10'd1) ? 10'd0 : y_count + 10'd1;
            end
        end
    end

    // ----- scanout (640x480 RGB565, VGA => analog_timing=480p) -----
    wire        burst_rd; wire [24:0] burst_addr; wire [10:0] burst_len; wire burst_32bit;
    reg  [31:0] burst_data; reg burst_data_valid, burst_data_done;
    wire [23:0] pixel_color; wire pal_busy;
    // Dedicated analog-fetch outputs. This is the Pocket LCD = Terminal RGBHV
    // path: core_top feeds the Analogizer THESE (the app via the analog fetch)
    // instead of the LCD video, so the analog keeps the app while the LCD shows
    // the console. Verifies the 480p analogizer_scan_* -> Analogizer -> cart-pin
    // chain (clocking/timing), which the LCD-video path never exercised.
    wire [23:0] a_rgb; wire a_hb, a_vb, a_hs, a_vs;
    wire a_blankn = ~(a_hb | a_vb);
    wire a_csync  = ~(a_hs ^ a_vs);

    video_CRT_scanout_indexed_BRAM scanout (
        .clk_video(clk_vid), .reset_n(reset_n),
        .x_count(x_count), .y_count(y_count), .line_start(line_start),
        .pixel_color(pixel_color),
        .clk_analog(clk), .reset_analog_n(reset_n), .analog_ce_pix(analog_ce_pix),
        .analog_scanlines(2'd0), .analog_timing(2'd1),
        .analog_pixel_clk(), .analog_pixel_color(a_rgb),
        .analog_hblank(a_hb), .analog_vblank(a_vb),
        .analog_hsync(a_hs), .analog_vsync(a_vs),
        .fb_base_addr(25'd0), .color_mode(3'd3), .fb_width(10'd640),
        .fb_height(10'd480), .fb_stride(16'd1280),
        .out_width(10'd640), .out_height(10'd480),
        // Analog fb params mirror the LCD (this chain runs the 480p timing).
        .analog_fb_base_addr(25'd0), .analog_color_mode(3'd3),
        .analog_fb_width(10'd640), .analog_fb_height(10'd480),
        .analog_fb_stride(16'd1280),
        .analog_out_width(10'd640), .analog_out_height(10'd480),
        .clk_palette(clk), .reset_palette_n(reset_n),
        .clk_sdram(clk), .burst_rd(burst_rd), .burst_addr(burst_addr),
        .burst_len(burst_len), .burst_32bit(burst_32bit), .burst_data(burst_data),
        .burst_data_valid(burst_data_valid), .burst_data_done(burst_data_done),
        .pal_wr(1'b0), .pal_addr(8'd0), .pal_data(24'd0), .pal_commit(1'b0), .pal_busy(pal_busy)
    );

    // address-aware SDRAM burst model (RGB565 = src row in every pixel)
    reg [10:0] brem; reg bact; reg [15:0] bsrc;
    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin brem<=0; bact<=0; bsrc<=0; burst_data<=0; burst_data_valid<=0; burst_data_done<=0; end
        else begin
            burst_data_valid<=0; burst_data_done<=0;
            if (burst_rd) begin bact<=1; brem<=burst_len; bsrc<=burst_addr/25'd640; end
            else if (bact) begin
                burst_data_valid<=1; burst_data<={bsrc,bsrc};
                if (brem<=11'd1) begin burst_data_done<=1; bact<=0; brem<=0; end
                else brem<=brem-11'd1;
            end
        end
    end

    // ----- Analogizer wired exactly as core_top: fed the 480p LCD video
    // (pixel_color + crt_* sync), scandoubler BYPASSED, DAC clock = video_clk =
    // clk_vid.  crt-style sync derived from the 480p raster (x_count/y_count). -----
    localparam [9:0] H_SYNC = 10'd58, H_BP = 10'd60, H_ACT = 10'd640;
    localparam [9:0] V_SYNC = 10'd3,  V_BP = 10'd30, V_ACT = 10'd480;
    wire crt_hs     = (x_count < H_SYNC);
    wire crt_vs     = (y_count < V_SYNC);
    wire crt_hblank = (x_count < (H_SYNC+H_BP)) || (x_count >= (H_SYNC+H_BP+H_ACT));
    wire crt_vblank = (y_count < (V_SYNC+V_BP)) || (y_count >= (V_SYNC+V_BP+V_ACT));
    wire crt_csync  = ~(crt_hs ^ crt_vs);
    wire crt_blankn = ~(crt_hblank | crt_vblank);
    wire b1d, b2d, b3d;

    openFPGA_Pocket_Analogizer #(
        .MASTER_CLK_FREQ(49_152_000), .EN_YPBPR(0), .EN_YC(0)
    ) analogizer (
        .i_clk(clk), .i_rst(~reset_n), .i_ena(1'b1),
        .video_clk(clk_vid),
        .analog_video_type(4'h5),                 // VGA (scandoubler bypassed)
        // RGBHV Terminal as core_top wires it: dedicated-fetch RGB (the app)
        // with the LCD-raster sync (crt_*, clk_vid). Verifies the app RGB lands
        // in the crt active region (RGB-present check) with the proven sync.
        .R(a_rgb[23:16]), .G(a_rgb[15:8]), .B(a_rgb[7:0]),
        .Hblank(crt_hblank), .Vblank(crt_vblank),
        .BLANKn(crt_blankn),
        .Hsync(crt_hs), .Vsync(crt_vs),
        .Csync(crt_csync),
        .ce_pix(analog_ce_pix), .scandoubler(1'b0), .fx(3'd0),
        .CHROMA_PHASE_INC(40'd0), .PALFLAG(1'b0),
        .snac_bank0_out(4'd0), .snac_bank0_dir(1'b0),
        .snac_bank1_76_out(2'd0), .snac_enable(1'b0),
        .snac_pin30_out(1'b0), .snac_pin30_dir(1'b0),
        .snac_pin31_out(1'b0), .snac_pin31_dir(1'b0),
        .cart_tran_bank2(cart_bank2), .cart_tran_bank2_dir(b2d),
        .cart_tran_bank3(cart_bank3), .cart_tran_bank3_dir(b3d),
        .cart_tran_bank1(cart_bank1), .cart_tran_bank1_dir(b1d)
    );

    wire _unused = &{1'b0, burst_addr, burst_32bit, pal_busy,
                     b1d, b2d, b3d, clk_12288, 1'b0};
endmodule

// ---- off-path encoder stubs (not exercised by the VGA/scandoubler path) ----
module vga_out (
    input wire clk, input wire ypbpr_en, input wire csync, input wire de,
    input wire [23:0] din, output wire [23:0] dout,
    output wire csync_o, output wire de_o
);
    assign dout = 24'd0; assign csync_o = 1'b0; assign de_o = 1'b0;
    wire _u = &{1'b0, clk, ypbpr_en, csync, de, din, 1'b0};
endmodule

module yc_out (
    input wire clk, input wire [39:0] PHASE_INC, input wire PAL_EN,
    input wire hsync, input wire vsync, input wire csync,
    input wire [23:0] din, output wire [23:0] dout,
    output wire hsync_o, output wire vsync_o, output wire csync_o
);
    assign dout = 24'd0; assign hsync_o = 1'b0; assign vsync_o = 1'b0; assign csync_o = 1'b0;
    wire _u = &{1'b0, clk, PHASE_INC, PAL_EN, hsync, vsync, csync, din, 1'b0};
endmodule
