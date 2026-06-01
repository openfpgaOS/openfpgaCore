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

module tb_scanout (
    input  wire clk,
    input  wire reset_n,
    input  wire analog_480p,

    output wire        analog_hsync,
    output wire        analog_vsync,
    output wire        analog_hblank,
    output wire        analog_vblank,
    output wire        analog_pixel_clk,
    output wire [23:0] analog_pixel_color_o,

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

    video_CRT_scanout_indexed_BRAM dut (
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
        .analog_480p(analog_480p),
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

    // ----- address-aware SDRAM burst model -----
    // src_line = burst_addr / 320 (stride_halfwords). Every 32-bit word of
    // that line is two RGB565 pixels both equal to src_line, so the analog
    // output color encodes the source row it actually read.
    reg [10:0] burst_remain;
    reg        burst_active;
    reg [15:0] burst_src;
    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            burst_remain <= 11'd0; burst_active <= 1'b0; burst_src <= 16'd0;
            burst_data <= 32'd0; burst_data_valid <= 1'b0; burst_data_done <= 1'b0;
        end else begin
            burst_data_valid <= 1'b0;
            burst_data_done  <= 1'b0;
            if (burst_rd) begin
                burst_active <= 1'b1;
                burst_remain <= burst_len;
                burst_src    <= burst_addr / 25'd320;   // source row number
            end else if (burst_active) begin
                burst_data_valid <= 1'b1;
                burst_data <= {burst_src, burst_src};    // two RGB565 px = src row
                if (burst_remain <= 11'd1) begin
                    burst_data_done <= 1'b1;
                    burst_active <= 1'b0;
                    burst_remain <= 11'd0;
                end else begin
                    burst_remain <= burst_remain - 11'd1;
                end
            end
        end
    end

    wire _unused = &{1'b0, burst_addr, burst_32bit, pixel_color, pal_busy, 1'b0};
endmodule
