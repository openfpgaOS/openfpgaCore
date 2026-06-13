//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

//
// Verilator testbench for video_CRT_scanout_indexed_BRAM.v
//
// Exercises the *analog* (Analogizer) output path and checks BOTH:
//   1. timing  — analog line/frame rate (240p 15.75 kHz vs 480p 31.5 kHz), and
//   2. data    — that each analog active line shows the CORRECT framebuffer
//                source row.
//
// Data oracle: the framebuffer is RGB565 and the SDRAM model returns, for
// every pixel of source row R, the 16-bit value R. The RGB565 decode is
// recoverable in C++ so the harness can confirm analog active line A shows
// source row A (for the 320x240, out_height=240 config used here).
//
// Clocking: `clk` = 49.152 MHz (analog + sdram). clk_video (24.576 MHz) is
// clk/2; analog_ce_pix (12.288 MHz) is a 1-in-4 strobe.
//
`default_nettype none

module tb_scanout #(
    // Forwarded verbatim to the DUT so the OS30 lean (analog raster pruned)
    // config can be built straight from the command line with a Verilator
    // -G override, e.g. -GHAS_ANALOG_RASTER=0.  Default matches the DUT's
    // default (analog raster present), so the standard scanout build is
    // unchanged.
    parameter HAS_ANALOG_RASTER = 1
) (
    input  wire clk,
    input  wire reset_n,
    // Analog raster timing select (ATIMING_* in the DUT):
    // 0=240p, 1=480p (slaved to LCD), 2=480i NTSC, 3=576i PAL.
    input  wire [1:0] analog_timing,
    // When 1, point the analog framebuffer at a different base than the LCD
    // (offset so analog source row R reads as R+1000). Proves the analog fetch
    // reads its OWN framebuffer while the LCD reads the LCD framebuffer.
    input  wire analog_split,
    // When 1, drive the analog OUTPUT height to 480 (2x the 240-tall source) so
    // the analog scaler must upscale a native app to fill the 480p frame, like
    // the LCD/vidout path. Without upscaling the app paints at native height and
    // the rest of the frame is black ("blank in Terminal" on RGBHV).
    input  wire analog_upscale,
    // When 1, drive a 480-tall analog framebuffer (the canonical interlace
    // source): each 240-line field must then fetch alternate source rows —
    // even field rows 0,2,4,.. / odd field rows 1,3,5,..
    input  wire analog_tall,

    output wire        analog_hsync,
    output wire        analog_vsync,
    output wire        analog_hblank,
    output wire        analog_vblank,
    output wire        analog_pixel_clk,
    output wire [23:0] analog_pixel_color_o,

    output wire [23:0] pixel_color_o,        // LCD pixel (for LCD deadline check)
    output wire [9:0]  x_count_o,
    output wire [9:0]  y_count_o
);
    // ----- derived clocks -----
    reg clk_video;
    always @(posedge clk or negedge reset_n)
        if (!reset_n) clk_video <= 1'b0; else clk_video <= ~clk_video;

    reg [1:0] ce_cnt;
    reg       analog_ce_pix;
    always @(posedge clk or negedge reset_n)
        if (!reset_n) begin ce_cnt <= 2'd0; analog_ce_pix <= 1'b0; end
        else begin ce_cnt <= ce_cnt + 2'd1; analog_ce_pix <= (ce_cnt == 2'd0); end

    // ----- LCD raster (480p: H_TOTAL 780, V_TOTAL 525) -----
    localparam [9:0] H_TOTAL = 10'd780;
    localparam [9:0] V_TOTAL = 10'd525;
    reg [9:0] x_count, y_count;
    reg       line_start;
    always @(posedge clk_video or negedge reset_n) begin
        if (!reset_n) begin
            x_count <= 10'd0; y_count <= 10'd0; line_start <= 1'b0;
        end else begin
            line_start <= (x_count == 10'd0);
            x_count <= x_count + 10'd1;
            if (x_count == H_TOTAL - 10'd1) begin
                x_count <= 10'd0;
                y_count <= (y_count >= V_TOTAL - 10'd1) ? 10'd0 : y_count + 10'd1;
            end
        end
    end
    assign x_count_o = x_count;
    assign y_count_o = y_count;
    assign pixel_color_o = pixel_color;

    // ----- framebuffer geometry: 320x240 RGB565, base 0, stride 640 bytes -----
    localparam [2:0]  COLOR_MODE = 3'd3;     // MODE_RGB565
    localparam [9:0]  FB_W = 10'd320;
    localparam [9:0]  FB_H = 10'd240;
    localparam [15:0] FB_STRIDE = 16'd640;   // bytes => 320 halfwords/line

    // ----- DUT wiring -----
    wire        burst_rd;
    wire [24:0] burst_addr;
    wire [10:0] burst_len;
    wire        burst_32bit;
    reg  [31:0] burst_data;
    reg         burst_data_valid;
    reg         burst_data_done;
    wire [23:0] pixel_color;
    wire        pal_busy;

    video_CRT_scanout_indexed_BRAM #(
        .HAS_ANALOG_RASTER(HAS_ANALOG_RASTER)
    ) dut (
        .clk_video(clk_video),
        .reset_n(reset_n),
        .x_count(x_count),
        .y_count(y_count),
        .line_start(line_start),
        .pixel_color(pixel_color),

        .clk_analog(clk),
        .reset_analog_n(reset_n),
        .analog_ce_pix(analog_ce_pix),
        .analog_scanlines(2'd0),
        .analog_timing(analog_timing),
        .analog_pixel_clk(analog_pixel_clk),
        .analog_pixel_color(analog_pixel_color_o),
        .analog_hblank(analog_hblank),
        .analog_vblank(analog_vblank),
        .analog_hsync(analog_hsync),
        .analog_vsync(analog_vsync),

        .fb_base_addr(25'd0),
        .color_mode(COLOR_MODE),
        .fb_width(FB_W),
        .fb_height(FB_H),
        .fb_stride(FB_STRIDE),
        .out_width(FB_W),
        .out_height(FB_H),

        // Analog framebuffer: same geometry, base 0 (mirror) or +1000 rows
        // (320000 halfwords = 1000 * 320-halfword stride) when split.
        // analog_tall doubles the source height to 480 rows (interlace).
        .analog_fb_base_addr(analog_split ? 25'd320000 : 25'd0),
        .analog_color_mode(COLOR_MODE),
        .analog_fb_width(FB_W),
        .analog_fb_height(analog_tall ? 10'd480 : FB_H),
        .analog_fb_stride(FB_STRIDE),
        .analog_out_width(analog_upscale ? 10'd640 : FB_W),
        .analog_out_height(analog_upscale ? 10'd480 : FB_H),

        .clk_palette(clk),
        .reset_palette_n(reset_n),

        .clk_sdram(clk),
        .burst_rd(burst_rd),
        .burst_addr(burst_addr),
        .burst_len(burst_len),
        .burst_32bit(burst_32bit),
        .burst_data(burst_data),
        .burst_data_valid(burst_data_valid),
        .burst_data_done(burst_data_done),

        .pal_wr(1'b0),
        .pal_addr(8'd0),
        .pal_data(24'd0),
        .pal_commit(1'b0),
        .pal_busy(pal_busy)
    );

    // ----- address-aware SDRAM burst model WITH realistic latency -----
    // src_line = burst_addr / 320 (stride_halfwords). Every 32-bit word of
    // that line is two RGB565 pixels both equal to src_line, so the decoded
    // output color encodes the source row it actually read (LCD or analog).
    //
    // Unlike the original zero-latency model, this adds:
    //   * ACT_LAT cycles of activation/dispatch latency before the first word
    //     (RAS->CAS + the io_sdram ST_IDLE->ST_REQ_BURST_READ pipeline), and
    //   * a free-running refresh timer that, when a burst is dispatched while a
    //     refresh is due, prepends REFRESH_STALL extra cycles (io_sdram serves
    //     refresh ABOVE the burst in ST_IDLE, but never preempts mid-burst).
    // This lets the harness verify the LCD fetch still meets its 1-line deadline
    // with the dedicated 240p analog fetch interleaved on the same burst port.
    //
    // These values are deliberately brutal (~128-cycle worst-case per burst, vs
    // real SDRAM's ~5-15 cycles, and clk_sdram here is 1:1 with clk_analog where
    // the real controller runs 100 MHz / 2x faster relative to video). The LCD
    // still renders every line: worst case = one in-flight analog burst + the
    // LCD burst = (ACT_LAT+REFRESH_STALL+burst_len)*2 = (64+64+160)*2 = 576 clk,
    // vs the 1560-clk (one LCD line) deadline => 2.7x headroom. This is the
    // regression guard proving the dual-fetch does not starve the LCD.
    localparam [8:0]  ACT_LAT       = 9'd64;   // per-burst activation latency
    localparam [11:0] REFRESH_EVERY = 12'd128; // ~ one refresh window
    localparam [8:0]  REFRESH_STALL = 9'd64;   // extra stall if refresh coincides

    localparam [1:0] M_IDLE = 2'd0, M_ACT = 2'd1, M_STREAM = 2'd2;
    reg [11:0] refresh_cnt;
    reg        refresh_due;
    reg [1:0]  mstate;
    reg [8:0]  act_remain;
    reg [10:0] burst_remain;
    reg [15:0] burst_src;
    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            refresh_cnt <= 12'd0; refresh_due <= 1'b0;
            mstate <= M_IDLE; act_remain <= 9'd0; burst_remain <= 11'd0;
            burst_src <= 16'd0; burst_data <= 32'd0;
            burst_data_valid <= 1'b0; burst_data_done <= 1'b0;
        end else begin
            burst_data_valid <= 1'b0;
            burst_data_done  <= 1'b0;

            // Free-running refresh timer. A newly-due refresh always wins over a
            // same-cycle consume so a refresh tick is never dropped.
            if (refresh_cnt >= REFRESH_EVERY - 12'd1) begin
                refresh_cnt <= 12'd0;
                refresh_due <= 1'b1;
            end else begin
                refresh_cnt <= refresh_cnt + 12'd1;
                if (mstate == M_IDLE && burst_rd)
                    refresh_due <= 1'b0;     // consume on burst dispatch
            end

            case (mstate)
                M_IDLE: begin
                    if (burst_rd) begin
                        burst_remain <= burst_len;
                        burst_src    <= burst_addr / 25'd320;   // source row
                        act_remain   <= ACT_LAT +
                                        (refresh_due ? REFRESH_STALL : 9'd0);
                        mstate       <= M_ACT;
                    end
                end
                M_ACT: begin
                    if (act_remain <= 9'd1) mstate <= M_STREAM;
                    else act_remain <= act_remain - 9'd1;
                end
                M_STREAM: begin
                    burst_data_valid <= 1'b1;
                    burst_data <= {burst_src, burst_src};    // two RGB565 px = row
                    if (burst_remain <= 11'd1) begin
                        burst_data_done <= 1'b1;
                        burst_remain <= 11'd0;
                        mstate <= M_IDLE;
                    end else begin
                        burst_remain <= burst_remain - 11'd1;
                    end
                end
                default: mstate <= M_IDLE;
            endcase
        end
    end

    wire _unused = &{1'b0, burst_addr, burst_32bit, pal_busy, 1'b0};
endmodule
