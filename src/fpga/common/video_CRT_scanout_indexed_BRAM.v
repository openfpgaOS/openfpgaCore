//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

//
// Video Scanout with Multiple Color Modes and Hardware Palette
// Supports: 8-bit indexed, 4-bit indexed, 2-bit indexed,
//           RGB565, RGB555, RGBA5551
//

`default_nettype none

module video_CRT_scanout_indexed_BRAM #(
    // Analogizer raster machinery: the dedicated 240p/15.75 kHz analog
    // raster + 480p mirror, its private 2048x32 line cache, the dedicated
    // SDRAM line fetch, and the analog leg of the shared output pipeline.
    //
    // PRUNE GATE (docs/os30-quake2-cut-plan.md C2): when 0 the two analog
    // issue points fold (analog_fetch_can_issue and issue_analog_read are
    // constant 0), which kills — transitively, by constant propagation and
    // dangling-register sweep — the analog raster counters/sync cone, the
    // analog tag-pipeline flags, serving_analog and the analog arm of the
    // burst FSM, and analog_line_buffer itself (no writer, dead reader).
    // The LCD path is untouched: it runs through the SAME shared output
    // pipeline (pixel_color is produced in the clk_analog domain) and the
    // shared horizontal-scaler cone simply loses its analog operand muxes.
    // Analog outputs park at their reset values (blank, syncs idle).
    // Set 0 only under EXCLUDE_ANALOGIZER builds (Pocket OS30); the
    // Analogizer-capable OS25 build keeps the default 1.
    parameter HAS_ANALOG_RASTER = 1
) (
    // Video clock domain (12.288 MHz)
    input wire clk_video,
    input wire reset_n,

    // Video timing inputs (active high)
    input wire [9:0] x_count,
    input wire [9:0] y_count,
    input wire line_start,          // Pulses at start of each line (x_count == 0)

    // Pixel output (RGB888)
    output reg [23:0] pixel_color,

    // Analogizer line-doubled output (49.152 MHz domain)
    input wire clk_analog,
    input wire reset_analog_n,
    input wire analog_ce_pix,
    input wire [1:0] analog_scanlines,
    // Analog raster timing select:
    //   2'd0 = 240p/15.75 kHz — dedicated raster for the 15 kHz Analogizer
    //          modes (RGBS/component/composite), independent of the LCD.
    //   2'd1 = 480p/31.5 kHz — slaved to the LCD raster (the pre-existing
    //          behavior, for VGA/scandoubler Analogizer modes).
    //   2'd2 = 480i NTSC — same 15.75 kHz line off the dedicated raster, but
    //          interlaced 525-line frames (262.5-line fields, ~60 Hz), each
    //          field fetching alternate source rows of a 480-tall FB.
    //   2'd3 = 576i PAL — 786-clock line (15.63 kHz) and 625-line interlaced
    //          frames (312.5-line fields, 50.03 Hz), free-running (a 50 Hz
    //          raster cannot phase-lock to the 60 Hz LCD frame).
    input wire [1:0] analog_timing,
    output reg analog_pixel_clk,
    output reg [23:0] analog_pixel_color,
    output reg analog_hblank,
    output reg analog_vblank,
    output reg analog_hsync,
    output reg analog_vsync,

    // Framebuffer base address (25-bit SDRAM byte address >> 1 = 16-bit word address)
    input wire [24:0] fb_base_addr,

    // Color mode (synced from CPU domain)
    input wire [2:0] color_mode,
    input wire [9:0] fb_width,
    input wire [9:0] fb_height,
    input wire [15:0] fb_stride,
    input wire [9:0] out_width,
    input wire [9:0] out_height,

    // Independent framebuffer parameters for the dedicated 240p analog fetch.
    // Normally driven identically to the LCD fb_* above (the analog output
    // mirrors the same source the LCD shows).  For "console on LCD, app on
    // analog" they point the analog path at the app framebuffer while the LCD
    // fb_* above point at the terminal framebuffer, so the two paths read
    // different SDRAM sources simultaneously.  Only consumed by the dedicated
    // 240p path (analog_480p=0); the 480p path mirrors the LCD video directly.
    input wire [24:0] analog_fb_base_addr,
    input wire [2:0]  analog_color_mode,
    input wire [9:0]  analog_fb_width,
    input wire [9:0]  analog_fb_height,
    input wire [15:0] analog_fb_stride,
    input wire [9:0]  analog_out_width,
    input wire [9:0]  analog_out_height,

    // Palette/sysreg clock domain (CPU clock)
    input wire clk_palette,
    input wire reset_palette_n,

    // SDRAM clock domain (66 MHz)
    input wire clk_sdram,

    // SDRAM burst interface
    output reg         burst_rd,
    output reg  [24:0] burst_addr,
    output reg  [10:0] burst_len,
    output wire        burst_32bit,
    input wire  [31:0] burst_data,
    input wire         burst_data_valid,
    input wire         burst_data_done,

    // Palette write interface (clk_palette domain)
    input wire        pal_wr,
    input wire [7:0]  pal_addr,
    input wire [23:0] pal_data,     // RGB888 palette data
    input wire        pal_commit,   // request visible-bank swap at frame start
    output wire       pal_busy      // commit queued until visible at frame start
);

    // Color mode constants (match OF_VIDEO_MODE_* in SDK)
    localparam MODE_8BIT    = 3'd0;  // 8-bit indexed (256 colors)
    localparam MODE_4BIT    = 3'd1;  // 4-bit indexed (16 colors)
    localparam MODE_2BIT    = 3'd2;  // 2-bit indexed (4 colors)
    localparam MODE_RGB565  = 3'd3;  // 16-bit direct color
    localparam MODE_RGB555  = 3'd4;  // 15-bit direct color
    localparam MODE_RGBA5551 = 3'd5; // 15-bit + 1-bit alpha

    // Video timing parameters
    localparam VID_V_SYNC   = 3;
    localparam VID_H_SYNC   = 58;
    localparam [9:0] VID_H_ACTIVE_START = 10'd120;
    localparam [9:0] VID_V_FETCH_BASE   = 10'd17;
    localparam [9:0] VID_V_ACTIVE_BASE  = 10'd18;
    localparam [9:0] VID_V_CENTER_HEIGHT = 10'd240;
    localparam [9:0] ANALOG_H_ACTIVE_END = 10'd760;

    // Analog raster timing select (see the analog_timing port).
    localparam [1:0] ATIMING_240P = 2'd0;
    localparam [1:0] ATIMING_480P = 2'd1;
    localparam [1:0] ATIMING_480I = 2'd2;
    localparam [1:0] ATIMING_576I = 2'd3;
    wire analog_480p       = (analog_timing == ATIMING_480P);
    wire analog_interlaced = analog_timing[1];
    wire analog_pal        = (analog_timing == ATIMING_576I);

    // Frame-boundary latch for the output geometry.  out_width/out_height are
    // driven from the CPU sysreg domain and may update at any point; if every
    // consumer (fetch addressing, LCD scanout, analog replay) read them
    // combinationally they could disagree within a single frame, causing tear
    // or mis-banking.  Capture them once per frame at frame_start (clk_video)
    // into out_width_locked/out_height_locked so all three views share one
    // stable geometry for the whole frame.  A mid-frame mode change is held off
    // until the next frame_start.  See latch block below near frame_start.
    reg [9:0] out_width_locked;
    reg [9:0] out_height_locked;

    wire [9:0] out_width_safe =
        (out_width_locked == 10'd0) ? 10'd320 : out_width_locked;
    wire [9:0] out_height_safe =
        (out_height_locked == 10'd0) ? 10'd240 : out_height_locked;
    wire [9:0] vid_v_center_offset =
        (out_height_safe < VID_V_CENTER_HEIGHT) ?
        ((VID_V_CENTER_HEIGHT - out_height_safe) >> 1) : 10'd0;
    wire [10:0] vid_v_fetch_start =
        {1'b0, VID_V_FETCH_BASE} + {1'b0, vid_v_center_offset};
    wire [10:0] vid_v_active_start =
        {1'b0, VID_V_ACTIVE_BASE} + {1'b0, vid_v_center_offset};
    wire [10:0] vid_h_active_end =
        {1'b0, VID_H_ACTIVE_START} + {1'b0, out_width_safe};
    wire [10:0] vid_v_fetch_end =
        vid_v_fetch_start + {1'b0, out_height_safe};
    wire [10:0] vid_v_active_end =
        vid_v_active_start + {1'b0, out_height_safe};

    // Scanout line cache.  The video side reads the bank selected by the
    // visible output line while the SDRAM side fills that bank from the
    // source framebuffer line selected by the vertical scaler.  This avoids
    // read/write contention on a single dual-clock RAM and keeps dynamic
    // source heights independent from the four-bank output cadence.
    //
    // Max line size: 800 x 16-bit = 400 x 32-bit words.  Four 512-word
    // banks keep one output line per bank and leave enough headroom for
    // common 640x480 and 800x600 16-bit framebuffers.
    reg [31:0] line_buffer [0:2047];
    // Dedicated analog line cache for the 240p path. The 240p analog scan
    // runs at half the LCD line rate, so it cannot share the LCD's 4-bank
    // cache (the LCD races through source rows ~2x faster). This buffer is
    // filled by a dedicated analog fetch (below), keyed to the analog scan.
    reg [31:0] analog_line_buffer [0:2047];
    // Each line cache gets its OWN clean registered read port so both infer as
    // M10K; the selected word is muxed AFTER the registers (select pipelined to
    // match the 1-cycle read latency). Reading both RAMs into one register
    // would defeat RAM inference and explode into flip-flops.
    reg [31:0] lcd_bram_rd_data;
    reg [31:0] analog_bram_rd_data;
    reg        rd_from_analog;
    wire [31:0] bram_rd_data =
        rd_from_analog ? analog_bram_rd_data : lcd_bram_rd_data;
    reg [8:0] write_ptr;
    reg [1:0] write_bank;

    // Palette RAM: two 256-entry banks x 24-bit RGB.
    // Port A writes the staging bank.  Port B reads the active bank for
    // scanout.  PAL_COMMIT swaps the active/staging banks only at frame start
    // so a 256-entry fade upload cannot tear through an active scanline.
    reg [7:0] pal_rd_addr;
    wire [23:0] pal_rd_data;
    reg pal_active_bank;

    reg pal_commit_req_toggle;
    reg pal_commit_ack_toggle;
    reg [1:0] pal_commit_ack_palette_sync;
    reg [1:0] pal_commit_req_video_sync;
    reg pal_active_bank_analog_s1;
    reg pal_active_bank_analog;
    wire pal_write_bank = ~pal_commit_ack_palette_sync[1];
    wire pal_commit_pending_palette =
        (pal_commit_req_toggle != pal_commit_ack_palette_sync[1]);
    wire pal_commit_pending_video =
        (pal_commit_req_video_sync[1] != pal_commit_ack_toggle);
    wire frame_start = line_start && (y_count == 10'd0);
    assign pal_busy = pal_commit_pending_palette;

    // Single frame-boundary latch (clk_video).  All output-geometry consumers
    // derive from out_width_locked/out_height_locked, so a mid-frame change to
    // out_width/out_height only takes effect at the next frame_start.  Reset to
    // the same defaults the safe-wires fall back to (320x240) so the very first
    // frame behaves identically to the previous combinational version.
    always @(posedge clk_video or negedge reset_n) begin
        if (!reset_n) begin
            out_width_locked  <= 10'd320;
            out_height_locked <= 10'd240;
        end else if (frame_start) begin
            out_width_locked  <= out_width;
            out_height_locked <= out_height;
        end
    end

    always @(posedge clk_palette or negedge reset_palette_n) begin
        if (!reset_palette_n) begin
            pal_commit_req_toggle <= 1'b0;
            pal_commit_ack_palette_sync <= 2'b00;
        end else begin
            pal_commit_ack_palette_sync <= {pal_commit_ack_palette_sync[0],
                                            pal_commit_ack_toggle};
            if (pal_commit && !pal_commit_pending_palette)
                pal_commit_req_toggle <= ~pal_commit_req_toggle;
        end
    end

    always @(posedge clk_video or negedge reset_n) begin
        if (!reset_n) begin
            pal_active_bank <= 1'b0;
            pal_commit_ack_toggle <= 1'b0;
            pal_commit_req_video_sync <= 2'b00;
        end else begin
            pal_commit_req_video_sync <= {pal_commit_req_video_sync[0],
                                          pal_commit_req_toggle};
            if (frame_start && pal_commit_pending_video) begin
                pal_active_bank <= ~pal_active_bank;
                pal_commit_ack_toggle <= pal_commit_req_video_sync[1];
            end
        end
    end

    always @(posedge clk_analog or negedge reset_analog_n) begin
        if (!reset_analog_n) begin
            pal_active_bank_analog_s1 <= 1'b0;
            pal_active_bank_analog <= 1'b0;
        end else begin
            pal_active_bank_analog_s1 <= pal_active_bank;
            pal_active_bank_analog <= pal_active_bank_analog_s1;
        end
    end

    altsyncram #(
        .operation_mode("DUAL_PORT"),
        .width_a(24),
        .widthad_a(9),
        .width_b(24),
        .widthad_b(9),
        .numwords_a(512),
        .numwords_b(512),
        .clock_enable_input_a("BYPASS"),
        .clock_enable_input_b("BYPASS"),
        .clock_enable_output_b("BYPASS"),
        .outdata_reg_b("UNREGISTERED"),
        .intended_device_family("Cyclone V"),
        .lpm_type("altsyncram"),
        .power_up_uninitialized("FALSE")
    ) palette_ram (
        .clock0(clk_palette),
        .address_a({pal_write_bank, pal_addr}),
        .data_a(pal_data),
        .wren_a(pal_wr),
        .clock1(clk_analog),
        .address_b({pal_active_bank_analog, pal_rd_addr}),
        .q_b(pal_rd_data),
        .wren_b(1'b0),
        .aclr0(1'b0), .aclr1(1'b0),
        .addressstall_a(1'b0), .addressstall_b(1'b0),
        .byteena_a(1'b1), .byteena_b(1'b1),
        .clocken0(1'b1), .clocken1(1'b1),
        .clocken2(1'b1), .clocken3(1'b1),
        .data_b(24'b0), .eccstatus(),
        .q_a(), .rden_a(1'b0), .rden_b(1'b1)
    );

    // Use 32-bit burst mode
    assign burst_32bit = 1'b1;

    // color_mode and fb_* are driven by axi_periph_slave in clk_sdram.
    wire [2:0] color_mode_sdram = color_mode;
    wire [9:0] fb_width_sdram =
        (fb_width == 10'd0) ? 10'd1 : fb_width;
    wire [15:0] fb_stride_sdram =
        (fb_stride < 16'd2) ? 16'd2 : fb_stride;

    // Same, for the dedicated 240p analog fetch's independent framebuffer.
    wire [2:0] analog_color_mode_sdram = analog_color_mode;
    wire [9:0] analog_fb_width_sdram =
        (analog_fb_width == 10'd0) ? 10'd1 : analog_fb_width;
    wire [15:0] analog_fb_stride_sdram =
        (analog_fb_stride < 16'd2) ? 16'd2 : analog_fb_stride;

    // Sync framebuffer height to the 12.288 MHz timing domain for vertical
    // source-line selection. Pixel decode runs in the 49.152 MHz domain below.
    reg [9:0] fb_height_video_s1, fb_height_video;
    always @(posedge clk_video or negedge reset_n) begin
        if (!reset_n) begin
            fb_height_video_s1 <= 10'd240;
            fb_height_video <= 10'd240;
        end else begin
            fb_height_video_s1 <= fb_height;
            fb_height_video <= fb_height_video_s1;
        end
    end
    wire [9:0] fb_height_video_safe =
        (fb_height_video == 10'd0) ? 10'd1 : fb_height_video;

    // =========================================
    // Video clock domain - Line start detection
    // =========================================
    wire [9:0] fetch_output_line = y_count - vid_v_fetch_start[9:0];
    wire in_vactive = ({1'b0, y_count} >= vid_v_fetch_start) &&
                      ({1'b0, y_count} < vid_v_fetch_end);

    reg fetch_request;
    reg fetch_request_ack_sync1, fetch_request_ack_sync2;
    reg [9:0] fetch_output_line_latched;
    reg [9:0] fetch_src_line_latched;
    reg [9:0] src_y_scan;
    reg [10:0] y_acc;
    wire [10:0] y_sum = y_acc + {1'b0, fb_height_video_safe};
    wire [10:0] out_height_1x = {1'b0, out_height_safe};
    wire [10:0] out_height_2x = {out_height_safe, 1'b0};
    wire [10:0] out_height_3x = out_height_2x + out_height_1x;
    wire [2:0] y_inc =
        (y_sum >= out_height_3x) ? 3'd3 :
        (y_sum >= out_height_2x) ? 3'd2 :
        (y_sum >= out_height_1x) ? 3'd1 : 3'd0;
    wire [10:0] y_acc_next =
        y_sum - ((y_inc == 3'd3) ? out_height_3x :
                 (y_inc == 3'd2) ? out_height_2x :
                 (y_inc == 3'd1) ? out_height_1x : 11'd0);
    wire [9:0] src_y_last = fb_height_video_safe - 10'd1;
    wire [10:0] src_y_next_raw = {1'b0, src_y_scan} + {8'b0, y_inc};
    wire [9:0] src_y_next =
        (src_y_next_raw > {1'b0, src_y_last}) ? src_y_last :
                                                src_y_next_raw[9:0];

    always @(posedge clk_video or negedge reset_n) begin
        if (!reset_n) begin
            fetch_request <= 0;
            fetch_output_line_latched <= 0;
            fetch_src_line_latched <= 0;
            fetch_request_ack_sync1 <= 0;
            fetch_request_ack_sync2 <= 0;
            src_y_scan <= 0;
            y_acc <= 0;
        end else begin
            fetch_request_ack_sync1 <= fetch_request_ack;
            fetch_request_ack_sync2 <= fetch_request_ack_sync1;

            if (fetch_request_ack_sync2)
                fetch_request <= 0;

            if (frame_start) begin
                src_y_scan <= 0;
                y_acc <= 0;
            end

            if (line_start && in_vactive && !fetch_request) begin
                fetch_request <= 1;
                fetch_output_line_latched <= fetch_output_line;
                fetch_src_line_latched <= src_y_scan;
                src_y_scan <= src_y_next;
                y_acc <= y_acc_next;
            end
        end
    end

    // =========================================
    // Shared pixel output (49.152 MHz)
    // =========================================
    // One read port on the line cache is time-multiplexed between the LCD
    // scanout and the Analogizer 31 kHz replay.  This keeps the framebuffer
    // fetch/decode path as the single owner of line storage.
    localparam [1:0] READ_NONE   = 2'd0;
    localparam [1:0] READ_LCD    = 2'd1;
    localparam [1:0] READ_ANALOG = 2'd2;

    function [8:0] packed_word_index;
        input [2:0] mode;
        input [9:0] x;
        begin
            case (mode)
                MODE_8BIT: packed_word_index = {1'b0, x[9:2]};
                MODE_4BIT: packed_word_index = {2'b00, x[9:3]};
                MODE_2BIT: packed_word_index = {3'b000, x[9:4]};
                default:   packed_word_index = x[9:1];
            endcase
        end
    endfunction

    function [23:0] rgb888_half;
        input [23:0] rgb;
        begin
            rgb888_half = {1'b0, rgb[23:17],
                           1'b0, rgb[15:9],
                           1'b0, rgb[7:1]};
        end
    endfunction

    // --- LCD pixel-decode params (clk_analog copies of the LCD fb_*) ---
    // The LCD pixel decode runs in clk_analog (shared output pipeline below),
    // so the LCD framebuffer width/color must be carried into this domain.
    // out_width tracks the frame-locked out_width_safe the LCD vertical/fetch
    // already use, so the horizontal and vertical LCD scalers stay consistent.
    reg [2:0] color_mode_lcd_a_s1, color_mode_lcd_a;
    reg [9:0] fb_width_lcd_a_s1, fb_width_lcd_a;
    reg [9:0] out_width_lcd_a_s1, out_width_lcd_a;

    // --- Analog pixel-decode + fetch params (clk_analog copies of analog_*) ---
    reg [2:0] color_mode_analog_s1, color_mode_analog;
    reg [9:0] fb_width_analog_s1, fb_width_analog;
    reg [9:0] fb_height_analog_s1, fb_height_analog;
    reg [9:0] out_width_analog_s1, out_width_analog;
    reg [9:0] out_height_analog_s1, out_height_analog;
    always @(posedge clk_analog or negedge reset_analog_n) begin
        if (!reset_analog_n) begin
            color_mode_lcd_a_s1 <= MODE_8BIT;
            color_mode_lcd_a <= MODE_8BIT;
            fb_width_lcd_a_s1 <= 10'd320;
            fb_width_lcd_a <= 10'd320;
            out_width_lcd_a_s1 <= 10'd320;
            out_width_lcd_a <= 10'd320;
            color_mode_analog_s1 <= MODE_8BIT;
            color_mode_analog <= MODE_8BIT;
            fb_width_analog_s1 <= 10'd320;
            fb_width_analog <= 10'd320;
            fb_height_analog_s1 <= 10'd240;
            fb_height_analog <= 10'd240;
            out_width_analog_s1 <= 10'd320;
            out_width_analog <= 10'd320;
            out_height_analog_s1 <= 10'd240;
            out_height_analog <= 10'd240;
        end else begin
            // LCD decode params (from the LCD fb_* inputs / frame-locked out).
            color_mode_lcd_a_s1 <= color_mode;
            color_mode_lcd_a <= color_mode_lcd_a_s1;
            fb_width_lcd_a_s1 <= fb_width;
            fb_width_lcd_a <= fb_width_lcd_a_s1;
            out_width_lcd_a_s1 <= out_width_safe;
            out_width_lcd_a <= out_width_lcd_a_s1;
            // Analog decode + fetch params (from the independent analog_* inputs).
            color_mode_analog_s1 <= analog_color_mode;
            color_mode_analog <= color_mode_analog_s1;
            fb_width_analog_s1 <= analog_fb_width;
            fb_width_analog <= fb_width_analog_s1;
            fb_height_analog_s1 <= analog_fb_height;
            fb_height_analog <= fb_height_analog_s1;
            out_width_analog_s1 <= analog_out_width;
            out_width_analog <= out_width_analog_s1;
            out_height_analog_s1 <= analog_out_height;
            out_height_analog <= out_height_analog_s1;
        end
    end
    wire [9:0] fb_width_lcd_a_safe =
        (fb_width_lcd_a == 10'd0) ? 10'd1 : fb_width_lcd_a;
    wire [9:0] out_width_lcd_a_safe =
        (out_width_lcd_a == 10'd0) ? 10'd320 : out_width_lcd_a;
    wire [9:0] fb_width_analog_safe =
        (fb_width_analog == 10'd0) ? 10'd1 : fb_width_analog;
    wire [9:0] fb_height_analog_safe =
        (fb_height_analog == 10'd0) ? 10'd1 : fb_height_analog;
    wire [9:0] out_width_analog_safe =
        (out_width_analog == 10'd0) ? 10'd320 : out_width_analog;
    wire [9:0] out_height_analog_safe =
        (out_height_analog == 10'd0) ? 10'd240 : out_height_analog;
    // Analog active height: fixed 240 lines for the dedicated 240p raster,
    // else follow the LCD output height (480p path).
    wire [9:0] analog_active_h =
        analog_480p ? out_height_analog_safe : 10'd240;
    wire [9:0] analog_v_center_offset =
        (out_height_analog_safe < VID_V_CENTER_HEIGHT) ?
        ((VID_V_CENTER_HEIGHT - out_height_analog_safe) >> 1) : 10'd0;
    // 576i letterbox: the active window stays 240 lines per field (480
    // content lines per frame) inside the ~288 visible lines of a 312/313
    // line PAL field; +28 raster lines past the NTSC base roughly centers
    // the picture (top border ~23 visible lines, bottom ~26).
    wire [10:0] analog_v_active_start =
        {1'b0, VID_V_ACTIVE_BASE} + {1'b0, analog_v_center_offset} +
        (analog_pal ? 11'd28 : 11'd0);

    wire [9:0] lcd_visible_y = y_count - vid_v_active_start[9:0];
    wire [1:0] lcd_line_bank = lcd_visible_y[1:0];
    wire lcd_in_hactive = (x_count >= VID_H_ACTIVE_START) &&
                          ({1'b0, x_count} < vid_h_active_end);
    wire lcd_in_vactive = ({1'b0, y_count} >= vid_v_active_start) &&
                          ({1'b0, y_count} < vid_v_active_end);

    reg [9:0] lcd_src_x_scan;
    reg [10:0] lcd_x_acc;
    // Previous-cycle x_count (clk_analog domain) so the LCD line-buffer read is
    // issued once per clk_vid pixel (now 24.576 MHz) instead of per analog_ce_pix
    // (12.288 MHz). clk_vid:clk_analog is mesochronous 1:2 from one PLL VCO, so
    // x_count is stable for 2 clk_analog cycles and this edge-detect yields a
    // clean 24.576 MHz LCD pixel strobe aligned to x_count.
    reg [9:0] lcd_x_count_prev;
    // LCD horizontal-scaler arithmetic shares one cone with the analog
    // scaler — see "Shared horizontal scaler cone" below the issue_*
    // selects.
    wire [9:0] lcd_src_x_for_read =
        lcd_in_hactive ? lcd_src_x_scan : 10'd0;
    wire [8:0] lcd_line_rd_index =
        lcd_in_hactive ? packed_word_index(color_mode_lcd_a,
                                           lcd_src_x_for_read) : 9'd0;
    wire [10:0] lcd_line_rd_addr = {lcd_line_bank, lcd_line_rd_index};

    localparam [9:0] ANALOG_H_TOTAL_LAST = 10'd779;
    // PAL line: H_TOTAL 786 advanced at the same 12.288 MHz pixel clock =
    // 15.634 kHz / 63.96 us (nominal 15.625 kHz / 64.00 us, +0.06% — inside
    // any PAL TV's tolerance), so no new PLL output is needed.  The active
    // window (120..760 = 640 px = 52.1 us) and H sync (58 px = 4.72 us) are
    // correct for both standards and stay shared.
    localparam [9:0] ANALOG_H_TOTAL_LAST_PAL = 10'd785;
    // 240p frame: H_TOTAL 780 advanced at 12.288 MHz = 15.75 kHz line; 262
    // lines => ~60.1 Hz. (480p keeps following the LCD's y_count / 525 lines.)
    localparam [9:0] ANALOG_V_TOTAL_240  = 10'd262;

    reg [1:0] analog_mux_phase;
    reg [9:0] analog_out_x;
    reg [9:0] analog_line_y;
    reg analog_line_second;
    reg analog_line_start_ce_d;
    reg [9:0] analog_src_x_scan;
    reg [10:0] analog_x_acc;
    // Interlace state. analog_field=0 is the SHORT field (262/312 lines,
    // vsync aligned to line start, shows even source rows); analog_field=1
    // is the LONG field (263/313 lines, vsync delayed by half a line, shows
    // odd source rows). The delayed vsync MUST pair with the long field:
    // delta(f0->f1) = len(f0)+0.5 and delta(f1->f0) = len(f1)-0.5, so only
    // len 262/263 (312/313) makes consecutive vsyncs sit uniformly 262.5
    // (312.5) lines apart — the uniform field period the TV's vertical
    // oscillator holds onto, with the half-line offset that interlaces.
    reg analog_field;
    // 480i lock: one interlaced frame = two LCD frames exactly (525 analog
    // lines of 780 px at 12.288 MHz == 2 x 525 LCD lines of 780 px at
    // 24.576 MHz), so resyncing on every OTHER LCD wrap is a no-op once
    // locked and re-acquires within two frames after reset/mode change.
    reg analog_resync_parity;

    wire [9:0] analog_h_total_last =
        analog_pal ? ANALOG_H_TOTAL_LAST_PAL : ANALOG_H_TOTAL_LAST;
    // Mid-line position for the long field's delayed vsync (H_TOTAL/2).
    wire [9:0] analog_half_line = analog_pal ? 10'd393 : 10'd390;
    // Lines in the CURRENT field, minus one (counter wrap compare value):
    // short field 0 = 262 (312), long field 1 = 263 (313) — see analog_field.
    wire [9:0] analog_v_total_last =
        analog_pal        ? (analog_field ? 10'd312 : 10'd311) :
        analog_interlaced ? (analog_field ? 10'd262 : 10'd261) :
                            (ANALOG_V_TOTAL_240 - 10'd1);

    wire analog_line_start_ce = analog_ce_pix && line_start;
    wire analog_line_start = analog_line_start_ce && !analog_line_start_ce_d;
    // Frame boundary (LCD wrap). In 240p the analog raster free-runs its own
    // V counter and only resyncs here, so it stays phase-locked to the 60 Hz
    // frame without inheriting the LCD's 525-line / 31.5 kHz line cadence.
    wire analog_frame_start = analog_line_start && (y_count == 10'd0);
    // Raster resync policy per timing mode: 240p realigns at every LCD wrap
    // (pre-existing), 480i at every other wrap (see analog_resync_parity),
    // 576i never — 50 Hz cannot lock to the 60 Hz LCD, so it free-runs and
    // accepts tearing against the 60 Hz renderer instead of sync loss.
    wire analog_raster_resync =
        analog_pal        ? 1'b0 :
        analog_interlaced ? (analog_frame_start && analog_resync_parity) :
                            analog_frame_start;
    wire analog_hblank_now = (analog_out_x < VID_H_ACTIVE_START) ||
                             (analog_out_x >= ANALOG_H_ACTIVE_END);
    wire analog_hsync_now = (analog_out_x < VID_H_SYNC);
    wire [10:0] analog_v_active_end =
        analog_v_active_start + {1'b0, analog_active_h};
    wire analog_vblank_now = ({1'b0, analog_line_y} < analog_v_active_start) ||
                             ({1'b0, analog_line_y} >= analog_v_active_end);
    // Vsync: 3 full lines from line start in progressive modes and in the
    // short field; the long field's vsync is delayed by half a line (starts
    // mid-line 0, ends mid-line 3) — the half-line offset a TV requires to
    // deflect this field's lines between the short field's on the CRT face.
    wire analog_vsync_even = (analog_line_y < VID_V_SYNC);
    wire analog_vsync_odd =
        ((analog_line_y == 10'd0) && (analog_out_x >= analog_half_line)) ||
        (analog_line_y == 10'd1) || (analog_line_y == 10'd2) ||
        ((analog_line_y == 10'd3) && (analog_out_x < analog_half_line));
    wire analog_vsync_now = (analog_interlaced && analog_field)
                            ? analog_vsync_odd : analog_vsync_even;
    wire analog_active_now = !analog_hblank_now && !analog_vblank_now;
    wire [9:0] analog_visible_y = analog_line_y - analog_v_active_start[9:0];
    wire [1:0] analog_line_bank = analog_visible_y[1:0];

    // --- Dedicated analog (240p) source fetch ---
    reg        analog_fetch_request;        // clk_analog -> clk_sdram handshake
    reg [9:0]  analog_fetch_line;           // next analog output line to fetch
    reg [9:0]  analog_fetch_src;            // source row for analog_fetch_line
    reg [10:0] analog_fy_acc;               // vertical-scale accumulator
    reg [9:0]  analog_fetch_out_latched;    // visible-line idx (bank) in flight
    reg [9:0]  analog_fetch_src_latched;    // source row in flight
    reg        analog_fetch_ack_sync1, analog_fetch_ack_sync2;

    // Vertical scaler: analog active line -> framebuffer source row. For a
    // 240-line analog raster from a 240-tall FB this is 1:1; from 480 it
    // decimates 2:1. Mirrors the LCD's y_inc accumulator with out = 240.
    wire [10:0] analog_fy_sum  = analog_fy_acc + {1'b0, fb_height_analog_safe};
    wire [10:0] analog_outh_1x = {1'b0, analog_active_h};
    wire [10:0] analog_outh_2x = {analog_active_h, 1'b0};
    wire [10:0] analog_outh_3x = analog_outh_2x + analog_outh_1x;
    wire [2:0]  analog_fy_inc =
        (analog_fy_sum >= analog_outh_3x) ? 3'd3 :
        (analog_fy_sum >= analog_outh_2x) ? 3'd2 :
        (analog_fy_sum >= analog_outh_1x) ? 3'd1 : 3'd0;
    wire [10:0] analog_fy_acc_next =
        analog_fy_sum - ((analog_fy_inc == 3'd3) ? analog_outh_3x :
                         (analog_fy_inc == 3'd2) ? analog_outh_2x :
                         (analog_fy_inc == 3'd1) ? analog_outh_1x : 11'd0);
    wire [9:0]  analog_fsrc_last = fb_height_analog_safe - 10'd1;
    wire [10:0] analog_fsrc_next_raw =
        {1'b0, analog_fetch_src} + {8'b0, analog_fy_inc};
    wire [9:0]  analog_fetch_src_next =
        (analog_fsrc_next_raw > {1'b0, analog_fsrc_last}) ?
            analog_fsrc_last : analog_fsrc_next_raw[9:0];

    // --- Interlace fetch seeding ---
    // Each interlaced field walks the same 240-line active window, but the
    // ODD field must sample the source half a frame-line lower: seed the
    // vertical accumulator with fb_height/2, expressed in the per-field
    // accumulator's 1/active_h units. For the canonical 480-tall FB that is
    // exactly one source row (240 = 1x240 rem 0): even fields fetch rows
    // 0,2,4,.. and odd fields 1,3,5,.. For a 240-tall FB it is half a step
    // (rem 120): both fields show every row — line-doubled 240p content on
    // an interlaced raster, which is the correct degradation.
    wire [10:0] analog_seed_half = {2'b00, fb_height_analog_safe[9:1]};
    wire [1:0]  analog_seed_div =
        (analog_seed_half >= analog_outh_2x) ? 2'd2 :
        (analog_seed_half >= analog_outh_1x) ? 2'd1 : 2'd0;
    // Field about to start at a fetch restart: a natural wrap toggles the
    // field on the same edge (next = ~analog_field); a forced raster resync
    // forces field 0 instead.
    wire analog_seed_field = analog_raster_resync ? 1'b0 : ~analog_field;
    wire analog_fetch_seed_odd = analog_interlaced && analog_seed_field;
    wire [9:0] analog_fetch_seed_src =
        analog_fetch_seed_odd ? {8'b0, analog_seed_div} : 10'd0;
    wire [10:0] analog_fetch_seed_acc =
        analog_fetch_seed_odd
            ? (analog_seed_half -
               ((analog_seed_div == 2'd2) ? analog_outh_2x :
                (analog_seed_div == 2'd1) ? analog_outh_1x : 11'd0))
            : 11'd0;
    // Fetch restart: per LCD frame for 240p/480p (pre-existing), per FIELD
    // for the interlaced rasters — at the raster's own field wrap, and at
    // the forced 480i resync (which resets the raster to field 0). In the
    // locked 480i steady state the resync preempts the natural wrap by one
    // clk_analog cycle, so the OR is what keeps the fetch restarting there.
    // No fetch is in flight at either event: the fetch walker stops at the
    // active end, >=3 (NTSC) / >=25 (PAL) lines before the field wraps.
    wire analog_field_wrap = issue_analog_read &&
        (analog_out_x == analog_h_total_last) &&
        (analog_line_y >= analog_v_total_last);
    wire analog_fetch_restart = analog_interlaced
        ? (analog_field_wrap || analog_raster_resync)
        : analog_frame_start;
    // Issue a fetch when none is in flight, the target line is in the active
    // region, and it stays <=2 lines ahead of the displayed line (so it never
    // overruns the 4-bank cache nor writes the bank being read this line).
    // Runs for BOTH the 240p raster (free-running analog_line_y) and the 480p
    // raster (analog_line_y slaved to y_count): in both the analog path reads
    // its OWN framebuffer (analog_fb_base_addr) so it can differ from the LCD
    // (e.g. Pocket LCD = Terminal: console on LCD, app on the analog DAC).
    // PRUNE GATE choke point 1: constant 0 without the analog raster — no
    // analog fetch is ever requested, so the clk_sdram analog handshake,
    // serving_analog and the analog_line_buffer write port all fold.
    wire analog_fetch_can_issue = (HAS_ANALOG_RASTER != 0) &&
        !analog_fetch_request && !analog_fetch_ack_sync2 &&
        (analog_fetch_line < analog_v_active_end[9:0]) &&
        ({1'b0, analog_fetch_line} <= ({1'b0, analog_line_y} + 11'd2));
    // Analog horizontal-scaler arithmetic shares one cone with the LCD
    // scaler — see "Shared horizontal scaler cone" below.
    wire [9:0] analog_src_x_for_read =
        analog_active_now ? analog_src_x_scan : 10'd0;
    wire [8:0] analog_line_rd_index =
        analog_active_now ? packed_word_index(color_mode_analog,
                                              analog_src_x_for_read) : 9'd0;
    wire [10:0] analog_line_rd_addr =
        {analog_line_bank, analog_line_rd_index};

    // One LCD line-buffer read per new x_count value = one per clk_vid pixel
    // (24.576 MHz). Was analog_ce_pix (12.288 MHz), which matched clk_vid only
    // when clk_vid was 12.288; at 24.576 that produced half the pixels.
    wire issue_lcd_read = (x_count != lcd_x_count_prev);
    // 480p: two analog reads per 12.288 MHz window (phases 1 and 3) ->
    // 24.576 MHz pixel advance -> 31.5 kHz line. 240p: one read (phase 1) ->
    // 12.288 MHz advance -> 15.75 kHz line.
    // PRUNE GATE choke point 2: constant 0 without the analog raster — the
    // issue tag can then never be READ_ANALOG (its bit 1 is provably 0
    // through the whole tag pipeline), rd_from_analog stays 0 so the line
    // cache read mux folds to the LCD bank, and every out_tag==READ_ANALOG
    // consumer — the analog output registers included — sweeps.
    wire issue_analog_read = (HAS_ANALOG_RASTER != 0) &&
        !issue_lcd_read &&
        ((analog_mux_phase == 2'd1) ||
         (analog_480p && (analog_mux_phase == 2'd3)));
    wire [1:0] issue_tag =
        issue_lcd_read ? READ_LCD :
        (issue_analog_read ? READ_ANALOG : READ_NONE);

    // --- Shared horizontal scaler cone (LCD + analog) ---
    // Both horizontal scalers live in this clk_analog domain and update on
    // mutually exclusive cycles by construction (issue_analog_read =
    // !issue_lcd_read && ...), so ONE accumulate-compare-subtract-clamp
    // cone serves both, with operand muxes keyed on the issue type.  The
    // analog leg substitutes the constants out_w=640 (so 2x=1280) for the
    // LCD's out_width_lcd_a_safe; with out_w=640 the 3x/4x rungs are
    // unreachable (x_sum < 640 + 1024 < 1920), so the generalized 4-step
    // ladder is exactly the old 2-step analog ladder.  Results commit on
    // the same posedge as before through the existing per-path enables —
    // no latency or cadence change; the vertical scalers are NOT shared
    // (LCD-y is clk_video, analog-fy is clk_analog: cross-domain).
    wire        sh_x_is_analog = issue_analog_read;
    wire [9:0]  sh_fb_width = sh_x_is_analog ? fb_width_analog_safe
                                             : fb_width_lcd_a_safe;
    wire [10:0] sh_x_acc    = sh_x_is_analog ? analog_x_acc : lcd_x_acc;
    wire [9:0]  sh_src_x    = sh_x_is_analog ? analog_src_x_scan
                                             : lcd_src_x_scan;
    wire [9:0]  sh_out_w    = sh_x_is_analog ? 10'd640
                                             : out_width_lcd_a_safe;
    wire [10:0] sh_x_sum     = sh_x_acc + {1'b0, sh_fb_width};
    wire [11:0] sh_x_sum_ext = {1'b0, sh_x_sum};
    wire [11:0] sh_outw_1x = {2'b0, sh_out_w};
    wire [11:0] sh_outw_2x = {1'b0, sh_out_w, 1'b0};
    wire [11:0] sh_outw_3x = sh_outw_2x + sh_outw_1x;
    wire [11:0] sh_outw_4x = {sh_out_w, 2'b00};
    wire [2:0] sh_x_inc =
        (sh_x_sum_ext >= sh_outw_4x) ? 3'd4 :
        (sh_x_sum_ext >= sh_outw_3x) ? 3'd3 :
        (sh_x_sum_ext >= sh_outw_2x) ? 3'd2 :
        (sh_x_sum_ext >= sh_outw_1x) ? 3'd1 : 3'd0;
    wire [11:0] sh_x_acc_next_ext =
        sh_x_sum_ext - ((sh_x_inc == 3'd4) ? sh_outw_4x :
                        (sh_x_inc == 3'd3) ? sh_outw_3x :
                        (sh_x_inc == 3'd2) ? sh_outw_2x :
                        (sh_x_inc == 3'd1) ? sh_outw_1x : 12'd0);
    wire [10:0] sh_x_acc_next = sh_x_acc_next_ext[10:0];
    wire [9:0]  sh_src_x_last = sh_fb_width - 10'd1;
    wire [10:0] sh_src_x_next_raw =
        {1'b0, sh_src_x} + {8'b0, sh_x_inc};
    wire [9:0]  sh_src_x_next =
        (sh_src_x_next_raw > {1'b0, sh_src_x_last}) ?
        sh_src_x_last : sh_src_x_next_raw[9:0];
    wire [10:0] issue_line_addr =
        issue_lcd_read ? lcd_line_rd_addr :
        (issue_analog_read ? analog_line_rd_addr : 11'd0);
    wire [3:0] issue_sub_pixel =
        issue_lcd_read ? lcd_src_x_for_read[3:0] :
        (issue_analog_read ? analog_src_x_for_read[3:0] : 4'd0);
    wire issue_active =
        issue_lcd_read ? (lcd_in_hactive && lcd_in_vactive) :
        (issue_analog_read ? analog_active_now : 1'b0);

    wire [10:0] line_wr_addr = {write_bank, write_ptr};

    (* altera_attribute = "-name AUTO_SHIFT_REGISTER_RECOGNITION OFF" *)
    reg [1:0] read_tag_q1;
    (* altera_attribute = "-name AUTO_SHIFT_REGISTER_RECOGNITION OFF" *)
    reg [3:0] read_sub_pixel_q1;
    (* altera_attribute = "-name AUTO_SHIFT_REGISTER_RECOGNITION OFF" *)
    reg read_active_q1;
    reg read_analog_hblank_q1, read_analog_vblank_q1;
    reg read_analog_hsync_q1, read_analog_vsync_q1;
    reg read_analog_dim_q1;
    reg [2:0] read_color_mode_q1;

    reg [1:0] decode_tag_q;
    reg decode_active_q;
    reg decode_analog_hblank_q, decode_analog_vblank_q;
    reg decode_analog_hsync_q, decode_analog_vsync_q;
    reg decode_analog_dim_q;
    reg [23:0] decode_direct_color_q;
    reg decode_use_direct_q;

    reg [1:0] out_tag_q;
    reg out_active_q;
    reg out_analog_hblank_q, out_analog_vblank_q;
    reg out_analog_hsync_q, out_analog_vsync_q;
    reg out_analog_dim_q;
    reg [23:0] out_direct_color_q;
    reg out_use_direct_q;

    wire [31:0] shifted_8 = bram_rd_data >> {read_sub_pixel_q1[1:0], 3'b0};
    wire [31:0] shifted_4 = bram_rd_data >> {read_sub_pixel_q1[2:0], 2'b0};
    wire [31:0] shifted_2 = bram_rd_data >> {read_sub_pixel_q1[3:0], 1'b0};
    wire [15:0] half_word =
        read_sub_pixel_q1[0] ? bram_rd_data[31:16] : bram_rd_data[15:0];
    wire [23:0] out_rgb =
        out_use_direct_q ? out_direct_color_q : pal_rd_data;
    wire [23:0] out_rgb_dim =
        out_analog_dim_q ? rgb888_half(out_rgb) : out_rgb;

    always @(posedge clk_analog or negedge reset_analog_n) begin
        if (!reset_analog_n) begin
            lcd_bram_rd_data <= 32'b0;
            analog_bram_rd_data <= 32'b0;
            rd_from_analog <= 1'b0;
            pal_rd_addr <= 8'b0;
            pixel_color <= 24'b0;
            analog_pixel_clk <= 1'b0;
            analog_pixel_color <= 24'b0;
            analog_hblank <= 1'b1;
            analog_vblank <= 1'b1;
            analog_hsync <= 1'b0;
            analog_vsync <= 1'b0;
            analog_mux_phase <= 2'd0;
            analog_out_x <= 10'd0;
            analog_line_y <= 10'd0;
            analog_field <= 1'b0;
            analog_resync_parity <= 1'b0;
            analog_line_second <= 1'b0;
            analog_line_start_ce_d <= 1'b0;
            analog_src_x_scan <= 10'd0;
            analog_x_acc <= 11'd0;
            lcd_src_x_scan <= 10'd0;
            lcd_x_acc <= 11'd0;
            lcd_x_count_prev <= 10'd0;
            analog_fetch_request <= 1'b0;
            analog_fetch_line <= 10'd0;
            analog_fetch_src <= 10'd0;
            analog_fy_acc <= 11'd0;
            analog_fetch_out_latched <= 10'd0;
            analog_fetch_src_latched <= 10'd0;
            analog_fetch_ack_sync1 <= 1'b0;
            analog_fetch_ack_sync2 <= 1'b0;
            read_tag_q1 <= READ_NONE;
            read_sub_pixel_q1 <= 4'd0;
            read_active_q1 <= 1'b0;
            read_analog_hblank_q1 <= 1'b1;
            read_analog_vblank_q1 <= 1'b1;
            read_analog_hsync_q1 <= 1'b0;
            read_analog_vsync_q1 <= 1'b0;
            read_analog_dim_q1 <= 1'b0;
            read_color_mode_q1 <= MODE_8BIT;
            decode_tag_q <= READ_NONE;
            decode_active_q <= 1'b0;
            decode_analog_hblank_q <= 1'b1;
            decode_analog_vblank_q <= 1'b1;
            decode_analog_hsync_q <= 1'b0;
            decode_analog_vsync_q <= 1'b0;
            decode_analog_dim_q <= 1'b0;
            decode_direct_color_q <= 24'b0;
            decode_use_direct_q <= 1'b0;
            out_tag_q <= READ_NONE;
            out_active_q <= 1'b0;
            out_analog_hblank_q <= 1'b1;
            out_analog_vblank_q <= 1'b1;
            out_analog_hsync_q <= 1'b0;
            out_analog_vsync_q <= 1'b0;
            out_analog_dim_q <= 1'b0;
            out_direct_color_q <= 24'b0;
            out_use_direct_q <= 1'b0;
        end else begin
            // analog_mux_phase stays live in both configs: it paces the
            // shared read-slot schedule (issue_analog_read derives from it),
            // and folding it without the analog raster saves nothing — the
            // gated issue_analog_read already kills its consumers.
            lcd_x_count_prev <= x_count;
            if (analog_ce_pix)
                analog_mux_phase <= 2'd1;
            else
                analog_mux_phase <= analog_mux_phase + 2'd1;

            if (HAS_ANALOG_RASTER != 0) begin
                analog_pixel_clk <= ~analog_pixel_clk;
                analog_line_start_ce_d <= analog_line_start_ce;

                // Dedicated 240p analog source fetch (handshake to the SDRAM
                // FSM): walk the active lines a couple ahead of the display,
                // requesting one source row at a time into
                // analog_line_buffer.
                analog_fetch_ack_sync1 <= analog_fetch_ack;
                analog_fetch_ack_sync2 <= analog_fetch_ack_sync1;
                if (analog_fetch_ack_sync2)
                    analog_fetch_request <= 1'b0;
                // 480i resync cadence: every other LCD wrap (see
                // analog_resync_parity declaration).
                if (analog_frame_start)
                    analog_resync_parity <= ~analog_resync_parity;
                if (analog_fetch_restart) begin
                    analog_fetch_request <= 1'b0;
                    analog_fetch_line <= analog_v_active_start[9:0];
                    analog_fetch_src  <= analog_fetch_seed_src;
                    analog_fy_acc     <= analog_fetch_seed_acc;
                end else if (analog_fetch_can_issue) begin
                    analog_fetch_request     <= 1'b1;
                    analog_fetch_out_latched <= analog_fetch_line - analog_v_active_start[9:0];
                    analog_fetch_src_latched <= analog_fetch_src;
                    analog_fetch_line <= analog_fetch_line + 10'd1;
                    analog_fetch_src  <= analog_fetch_src_next;
                    analog_fy_acc     <= analog_fy_acc_next;
                end
            end

            // Clean per-RAM registered reads (keeps both inferring as M10K).
            // LCD reads use the shared cache; analog reads (240p AND 480p) use
            // the dedicated cache filled from analog_fb_base_addr. The select is
            // registered to match read latency and muxes the two registered
            // outputs into bram_rd_data (a wire).
            lcd_bram_rd_data    <= line_buffer[issue_line_addr];
            // Gated read keeps the cut explicit: with no writer AND no
            // reader the 2048x32 analog cache (8 M10Ks) cannot survive.
            if (HAS_ANALOG_RASTER != 0)
                analog_bram_rd_data <= analog_line_buffer[issue_line_addr];
            rd_from_analog      <= issue_analog_read;
            read_tag_q1 <= issue_tag;
            read_sub_pixel_q1 <= issue_sub_pixel;
            read_active_q1 <= issue_active;
            read_analog_hblank_q1 <= analog_hblank_now;
            read_analog_vblank_q1 <= analog_vblank_now;
            read_analog_hsync_q1 <= analog_hsync_now;
            read_analog_vsync_q1 <= analog_vsync_now;
            read_analog_dim_q1 <= analog_line_second && analog_scanlines[1];
            // Without the analog raster the non-LCD leg folds to the LCD
            // mode, so the analog CDC param copies lose their last reader.
            read_color_mode_q1 <= issue_lcd_read ? color_mode_lcd_a
                                : ((HAS_ANALOG_RASTER != 0) ? color_mode_analog
                                                            : color_mode_lcd_a);

            if (issue_lcd_read) begin
                if (!lcd_in_hactive) begin
                    lcd_src_x_scan <= 10'd0;
                    lcd_x_acc <= 11'd0;
                end else begin
                    // issue_lcd_read ⇒ shared cone carries LCD operands.
                    lcd_src_x_scan <= sh_src_x_next;
                    lcd_x_acc <= sh_x_acc_next;
                end
            end

            // 480p: restart every LCD line and follow y_count (slaved to the
            // LCD 525-line raster). 240p: restart only at the frame boundary;
            // analog_out_x free-runs 0..779 and analog_line_y is its own
            // 262-line counter, so the analog scan decouples from the LCD line
            // cadence and lands at 15.75 kHz / ~60 Hz. 480i: same free-running
            // raster, resynced at every other LCD wrap (forcing field 0).
            // 576i: fully free-running (analog_raster_resync is constant 0).
            if ((HAS_ANALOG_RASTER != 0) &&
                (analog_480p ? analog_line_start : analog_raster_resync)) begin
                analog_out_x <= 10'd0;
                analog_line_y <= analog_480p ? y_count : 10'd0;
                analog_field <= 1'b0;
                analog_line_second <= 1'b0;
                analog_src_x_scan <= 10'd0;
                analog_x_acc <= 11'd0;
            end else if (issue_analog_read) begin
                if (analog_out_x == analog_h_total_last) begin
                    analog_out_x <= 10'd0;
                    analog_line_second <= ~analog_line_second;
                    analog_src_x_scan <= 10'd0;
                    analog_x_acc <= 11'd0;
                    if (!analog_480p) begin
                        if (analog_line_y >= analog_v_total_last) begin
                            analog_line_y <= 10'd0;
                            analog_field <= analog_interlaced ? ~analog_field
                                                              : 1'b0;
                        end else begin
                            analog_line_y <= analog_line_y + 10'd1;
                        end
                    end
                end else begin
                    analog_out_x <= analog_out_x + 10'd1;
                    if (!analog_active_now) begin
                        analog_src_x_scan <= 10'd0;
                        analog_x_acc <= 11'd0;
                    end else begin
                        // issue_analog_read ⇒ shared cone carries analog
                        // operands.
                        analog_src_x_scan <= sh_src_x_next;
                        analog_x_acc <= sh_x_acc_next;
                    end
                end
            end

            decode_tag_q <= read_tag_q1;
            decode_active_q <= read_active_q1;
            decode_analog_hblank_q <= read_analog_hblank_q1;
            decode_analog_vblank_q <= read_analog_vblank_q1;
            decode_analog_hsync_q <= read_analog_hsync_q1;
            decode_analog_vsync_q <= read_analog_vsync_q1;
            decode_analog_dim_q <= read_analog_dim_q1;
            decode_use_direct_q <= 1'b0;
            case (read_color_mode_q1)
                MODE_8BIT: pal_rd_addr <= shifted_8[7:0];
                MODE_4BIT: pal_rd_addr <= {4'b0, shifted_4[3:0]};
                MODE_2BIT: pal_rd_addr <= {6'b0, shifted_2[1:0]};

                MODE_RGB565: begin
                    decode_use_direct_q <= 1'b1;
                    decode_direct_color_q <= {half_word[15:11], 3'b0,
                                              half_word[10:5], 2'b0,
                                              half_word[4:0], 3'b0};
                end

                MODE_RGB555: begin
                    decode_use_direct_q <= 1'b1;
                    decode_direct_color_q <= {half_word[14:10], 3'b0,
                                              half_word[9:5], 3'b0,
                                              half_word[4:0], 3'b0};
                end

                MODE_RGBA5551: begin
                    decode_use_direct_q <= 1'b1;
                    if (half_word[0])
                        decode_direct_color_q <= {half_word[15:11], 3'b0,
                                                  half_word[10:6], 3'b0,
                                                  half_word[5:1], 3'b0};
                    else
                        decode_direct_color_q <= 24'h000000;
                end

                default: pal_rd_addr <= shifted_8[7:0];
            endcase

            out_tag_q <= decode_tag_q;
            out_active_q <= decode_active_q;
            out_analog_hblank_q <= decode_analog_hblank_q;
            out_analog_vblank_q <= decode_analog_vblank_q;
            out_analog_hsync_q <= decode_analog_hsync_q;
            out_analog_vsync_q <= decode_analog_vsync_q;
            out_analog_dim_q <= decode_analog_dim_q;
            out_direct_color_q <= decode_direct_color_q;
            out_use_direct_q <= decode_use_direct_q;

            if (out_tag_q == READ_LCD)
                pixel_color <= out_active_q ? out_rgb : 24'h000000;

            if ((HAS_ANALOG_RASTER != 0) && (out_tag_q == READ_ANALOG)) begin
                analog_hblank <= out_analog_hblank_q;
                analog_vblank <= out_analog_vblank_q;
                analog_hsync <= out_analog_hsync_q;
                analog_vsync <= out_analog_vsync_q;
                analog_pixel_color <= out_active_q ? out_rgb_dim : 24'h000000;
            end
        end
    end

    // =========================================
    // SDRAM clock domain - Burst read FSM
    // =========================================
    reg fetch_request_sync1, fetch_request_sync2;
    reg fetch_request_ack;
    reg [9:0] fetch_output_line_sdram;
    reg [9:0] fetch_src_line_sdram;
    reg       fetch_line_pending;

    // Dedicated analog fetch request (clk_analog -> clk_sdram), served over the
    // same burst port as the LCD fetch (LCD has priority). serving_analog
    // routes the burst write to analog_line_buffer.
    reg analog_fetch_req_sync1, analog_fetch_req_sync2;
    reg analog_fetch_ack;
    reg [9:0] analog_fetch_out_sdram;
    reg [9:0] analog_fetch_src_sdram;
    reg       analog_fetch_line_pending;
    reg       serving_analog;

    localparam ST_IDLE = 2'd0;
    localparam ST_BURST = 2'd1;
    localparam ST_WAIT = 2'd2;
    localparam ST_CALC = 2'd3;

    reg [1:0] state;

    // Shared burst address/length cone operands (2-cycle dispatch):
    // ST_IDLE picks the pending source (LCD priority first, exactly as
    // before) and latches its operands here; ST_CALC then computes
    // burst_addr/burst_len through ONE shared multiply+add+len cone and
    // asserts burst_rd.  This replaces the two per-branch copies of the
    // stride multiply, base adder and burst_len_for_mode shift cone.
    // The +1 clk_sdram cycle (~15 ns) per line fetch is dispatched
    // against whole-blanking-period slack (line period ~32-64 us).
    reg [24:0] calc_base;
    reg [9:0]  calc_line;
    reg [15:0] calc_stride;
    reg [2:0]  calc_mode;
    reg [9:0]  calc_width;

    function [10:0] burst_len_for_mode;
        input [2:0] mode;
        input [9:0] width;
        begin
            case (mode)
                MODE_8BIT: burst_len_for_mode = ({1'b0, width} + 11'd3) >> 2;
                MODE_4BIT: burst_len_for_mode = ({1'b0, width} + 11'd7) >> 3;
                MODE_2BIT: burst_len_for_mode = ({1'b0, width} + 11'd15) >> 4;
                default:   burst_len_for_mode = ({1'b0, width} + 11'd1) >> 1;
            endcase
        end
    endfunction

    function [24:0] line_offset_for_stride;
        input [9:0] line;
        input [15:0] stride;
        reg [24:0] stride_halfwords;
        begin
            stride_halfwords = {10'b0, stride[15:1]};
            line_offset_for_stride = line * stride_halfwords;
        end
    endfunction

    always @(posedge clk_sdram or negedge reset_n) begin
        if (!reset_n) begin
            state <= ST_IDLE;
            burst_rd <= 0;
            burst_addr <= 0;
            burst_len <= 0;
            write_ptr <= 0;
            write_bank <= 0;
            fetch_request_sync1 <= 0;
            fetch_request_sync2 <= 0;
            fetch_request_ack <= 0;
            fetch_output_line_sdram <= 0;
            fetch_src_line_sdram <= 0;
            fetch_line_pending <= 0;
            analog_fetch_req_sync1 <= 0;
            analog_fetch_req_sync2 <= 0;
            analog_fetch_ack <= 0;
            analog_fetch_out_sdram <= 0;
            analog_fetch_src_sdram <= 0;
            analog_fetch_line_pending <= 0;
            serving_analog <= 0;
            calc_base <= 0;
            calc_line <= 0;
            calc_stride <= 0;
            calc_mode <= 0;
            calc_width <= 0;
        end else begin
            fetch_request_sync1 <= fetch_request;
            fetch_request_sync2 <= fetch_request_sync1;
            burst_rd <= 0;

            if (fetch_request_sync2 && !fetch_request_ack && !fetch_line_pending) begin
                fetch_output_line_sdram <= fetch_output_line_latched;
                fetch_src_line_sdram <= fetch_src_line_latched;
                fetch_line_pending <= 1;
                fetch_request_ack <= 1;
            end else if (!fetch_request_sync2) begin
                fetch_request_ack <= 0;
            end

            // Analog fetch request handshake (mirrors the LCD's above).
            if (HAS_ANALOG_RASTER != 0) begin
                analog_fetch_req_sync1 <= analog_fetch_request;
                analog_fetch_req_sync2 <= analog_fetch_req_sync1;
                if (analog_fetch_req_sync2 && !analog_fetch_ack && !analog_fetch_line_pending) begin
                    analog_fetch_out_sdram <= analog_fetch_out_latched;
                    analog_fetch_src_sdram <= analog_fetch_src_latched;
                    analog_fetch_line_pending <= 1;
                    analog_fetch_ack <= 1;
                end else if (!analog_fetch_req_sync2) begin
                    analog_fetch_ack <= 0;
                end
            end

            case (state)
                ST_IDLE: begin
                    // LCD-priority ordering between the two pending flags
                    // is preserved exactly: LCD wins when both are pending.
                    if (fetch_line_pending) begin
                        calc_base   <= fb_base_addr;
                        calc_line   <= fetch_src_line_sdram;
                        calc_stride <= fb_stride_sdram;
                        calc_mode   <= color_mode_sdram;
                        calc_width  <= fb_width_sdram;
                        write_ptr <= 0;
                        write_bank <= fetch_output_line_sdram[1:0];
                        fetch_line_pending <= 0;
                        serving_analog <= 0;
                        state <= ST_CALC;
                    end else if ((HAS_ANALOG_RASTER != 0)
                                 && analog_fetch_line_pending) begin
                        calc_base   <= analog_fb_base_addr;
                        calc_line   <= analog_fetch_src_sdram;
                        calc_stride <= analog_fb_stride_sdram;
                        calc_mode   <= analog_color_mode_sdram;
                        calc_width  <= analog_fb_width_sdram;
                        write_ptr <= 0;
                        write_bank <= analog_fetch_out_sdram[1:0];
                        analog_fetch_line_pending <= 0;
                        serving_analog <= 1;
                        state <= ST_CALC;
                    end
                end

                // Shared dispatch cycle: one multiply+add+len cone serves
                // both LCD and analog line fetches.  burst_addr/burst_len
                // latch on the same edge burst_rd asserts, as before.
                ST_CALC: begin
                    burst_addr <= calc_base +
                        line_offset_for_stride(calc_line, calc_stride);
                    burst_len <= burst_len_for_mode(calc_mode, calc_width);
                    burst_rd <= 1;
                    state <= ST_BURST;
                end

                ST_BURST: begin
                    if (burst_data_valid) begin
                        if (serving_analog)
                            analog_line_buffer[line_wr_addr] <= burst_data;
                        else
                            line_buffer[line_wr_addr] <= burst_data;
                        write_ptr <= write_ptr + 9'd1;
                    end
                    if (burst_data_done) begin
                        state <= ST_IDLE;
                    end
                end

                ST_WAIT: begin
                    state <= ST_IDLE;
                end

                default: begin
                    state <= ST_IDLE;
                end
            endcase
        end
    end

endmodule
