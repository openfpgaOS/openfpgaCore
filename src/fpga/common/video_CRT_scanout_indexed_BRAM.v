//
// Video Scanout with Multiple Color Modes and Hardware Palette
// Supports: 8-bit indexed, 4-bit indexed, 2-bit indexed,
//           RGB565, RGB555, RGBA5551
//

`default_nettype none

module video_CRT_scanout_indexed_BRAM (
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
    localparam VID_V_BPORCH = 15;
    localparam VID_V_FPORCH = 4;
    localparam VID_H_SYNC   = 58;
    localparam VID_H_BPORCH = 62;
    localparam VID_H_FPORCH = 20;
    localparam [9:0] VID_H_ACTIVE_START = 10'd120;
    localparam [9:0] VID_V_FETCH_START  = 10'd17;
    localparam [9:0] VID_V_ACTIVE_START = 10'd18;
    localparam [9:0] ANALOG_H_ACTIVE_END = 10'd760;
    localparam [9:0] ANALOG_V_ACTIVE_END = 10'd258;

    wire [9:0] out_width_safe =
        (out_width == 10'd0) ? 10'd640 : out_width;
    wire [9:0] out_height_safe =
        (out_height == 10'd0) ? 10'd240 : out_height;
    wire [10:0] vid_h_active_end =
        {1'b0, VID_H_ACTIVE_START} + {1'b0, out_width_safe};
    wire [10:0] vid_v_fetch_end =
        {1'b0, VID_V_FETCH_START} + {1'b0, out_height_safe};
    wire [10:0] vid_v_active_end =
        {1'b0, VID_V_ACTIVE_START} + {1'b0, out_height_safe};

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
    reg [31:0] bram_rd_data;
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
    wire [9:0] fetch_output_line = y_count - VID_V_FETCH_START;
    wire in_vactive = (y_count >= VID_V_FETCH_START) &&
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

    reg [2:0] color_mode_analog_s1, color_mode_analog;
    reg [9:0] fb_width_analog_s1, fb_width_analog;
    reg [9:0] out_width_analog_s1, out_width_analog;
    always @(posedge clk_analog or negedge reset_analog_n) begin
        if (!reset_analog_n) begin
            color_mode_analog_s1 <= MODE_8BIT;
            color_mode_analog <= MODE_8BIT;
            fb_width_analog_s1 <= 10'd320;
            fb_width_analog <= 10'd320;
            out_width_analog_s1 <= 10'd640;
            out_width_analog <= 10'd640;
        end else begin
            color_mode_analog_s1 <= color_mode;
            color_mode_analog <= color_mode_analog_s1;
            fb_width_analog_s1 <= fb_width;
            fb_width_analog <= fb_width_analog_s1;
            out_width_analog_s1 <= out_width_safe;
            out_width_analog <= out_width_analog_s1;
        end
    end
    wire [9:0] fb_width_analog_safe =
        (fb_width_analog == 10'd0) ? 10'd1 : fb_width_analog;
    wire [9:0] out_width_analog_safe =
        (out_width_analog == 10'd0) ? 10'd640 : out_width_analog;

    wire [9:0] lcd_visible_y = y_count - VID_V_ACTIVE_START;
    wire [1:0] lcd_line_bank = lcd_visible_y[1:0];
    wire lcd_in_hactive = (x_count >= VID_H_ACTIVE_START) &&
                          ({1'b0, x_count} < vid_h_active_end);
    wire lcd_in_vactive = (y_count >= VID_V_ACTIVE_START) &&
                          ({1'b0, y_count} < vid_v_active_end);

    reg [9:0] lcd_src_x_scan;
    reg [10:0] lcd_x_acc;
    wire [10:0] lcd_x_sum = lcd_x_acc + {1'b0, fb_width_analog_safe};
    wire [11:0] lcd_x_sum_ext = {1'b0, lcd_x_sum};
    wire [11:0] out_width_1x = {2'b0, out_width_analog_safe};
    wire [11:0] out_width_2x = {1'b0, out_width_analog_safe, 1'b0};
    wire [11:0] out_width_3x = out_width_2x + out_width_1x;
    wire [11:0] out_width_4x = {out_width_analog_safe, 2'b00};
    wire [2:0] lcd_x_inc =
        (lcd_x_sum_ext >= out_width_4x) ? 3'd4 :
        (lcd_x_sum_ext >= out_width_3x) ? 3'd3 :
        (lcd_x_sum_ext >= out_width_2x) ? 3'd2 :
        (lcd_x_sum_ext >= out_width_1x) ? 3'd1 : 3'd0;
    wire [11:0] lcd_x_acc_next_ext =
        lcd_x_sum_ext - ((lcd_x_inc == 3'd4) ? out_width_4x :
                         (lcd_x_inc == 3'd3) ? out_width_3x :
                         (lcd_x_inc == 3'd2) ? out_width_2x :
                         (lcd_x_inc == 3'd1) ? out_width_1x : 12'd0);
    wire [10:0] lcd_x_acc_next = lcd_x_acc_next_ext[10:0];
    wire [9:0] lcd_src_x_last = fb_width_analog_safe - 10'd1;
    wire [10:0] lcd_src_x_next_raw =
        {1'b0, lcd_src_x_scan} + {8'b0, lcd_x_inc};
    wire [9:0] lcd_src_x_next =
        (lcd_src_x_next_raw > {1'b0, lcd_src_x_last}) ?
        lcd_src_x_last : lcd_src_x_next_raw[9:0];
    wire [9:0] lcd_src_x_for_read =
        lcd_in_hactive ? lcd_src_x_scan : 10'd0;
    wire [8:0] lcd_line_rd_index =
        lcd_in_hactive ? packed_word_index(color_mode_analog,
                                           lcd_src_x_for_read) : 9'd0;
    wire [10:0] lcd_line_rd_addr = {lcd_line_bank, lcd_line_rd_index};

    localparam [9:0] ANALOG_H_TOTAL_LAST = 10'd779;

    reg [1:0] analog_mux_phase;
    reg [9:0] analog_out_x;
    reg [9:0] analog_line_y;
    reg analog_line_second;
    reg analog_line_start_ce_d;
    reg [9:0] analog_src_x_scan;
    reg [10:0] analog_x_acc;

    wire analog_line_start_ce = analog_ce_pix && line_start;
    wire analog_line_start = analog_line_start_ce && !analog_line_start_ce_d;
    wire analog_hblank_now = (analog_out_x < VID_H_ACTIVE_START) ||
                             (analog_out_x >= ANALOG_H_ACTIVE_END);
    wire analog_hsync_now = (analog_out_x < VID_H_SYNC);
    wire analog_vblank_now = (analog_line_y < VID_V_ACTIVE_START) ||
                             (analog_line_y >= ANALOG_V_ACTIVE_END);
    wire analog_vsync_now = (analog_line_y < VID_V_SYNC);
    wire analog_active_now = !analog_hblank_now && !analog_vblank_now;
    wire [9:0] analog_visible_y = analog_line_y - VID_V_ACTIVE_START;
    wire [1:0] analog_line_bank = analog_visible_y[1:0];
    wire [10:0] analog_x_sum =
        analog_x_acc + {1'b0, fb_width_analog_safe};
    wire [1:0] analog_x_inc =
        (analog_x_sum >= 11'd1280) ? 2'd2 :
        (analog_x_sum >= 11'd640)  ? 2'd1 : 2'd0;
    wire [10:0] analog_x_acc_next =
        analog_x_sum - ((analog_x_inc == 2'd2) ? 11'd1280 :
                        (analog_x_inc == 2'd1) ? 11'd640 : 11'd0);
    wire [9:0] analog_src_x_last = fb_width_analog_safe - 10'd1;
    wire [10:0] analog_src_x_next_raw =
        {1'b0, analog_src_x_scan} + {9'b0, analog_x_inc};
    wire [9:0] analog_src_x_next =
        (analog_src_x_next_raw > {1'b0, analog_src_x_last}) ?
        analog_src_x_last : analog_src_x_next_raw[9:0];
    wire [9:0] analog_src_x_for_read =
        analog_active_now ? analog_src_x_scan : 10'd0;
    wire [8:0] analog_line_rd_index =
        analog_active_now ? packed_word_index(color_mode_analog,
                                              analog_src_x_for_read) : 9'd0;
    wire [10:0] analog_line_rd_addr =
        {analog_line_bank, analog_line_rd_index};

    wire issue_lcd_read = analog_ce_pix;
    wire issue_analog_read =
        !issue_lcd_read &&
        ((analog_mux_phase == 2'd1) || (analog_mux_phase == 2'd3));
    wire [1:0] issue_tag =
        issue_lcd_read ? READ_LCD :
        (issue_analog_read ? READ_ANALOG : READ_NONE);
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
            bram_rd_data <= 32'b0;
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
            analog_line_second <= 1'b0;
            analog_line_start_ce_d <= 1'b0;
            analog_src_x_scan <= 10'd0;
            analog_x_acc <= 11'd0;
            lcd_src_x_scan <= 10'd0;
            lcd_x_acc <= 11'd0;
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
            analog_pixel_clk <= ~analog_pixel_clk;
            analog_line_start_ce_d <= analog_line_start_ce;
            if (analog_ce_pix)
                analog_mux_phase <= 2'd1;
            else
                analog_mux_phase <= analog_mux_phase + 2'd1;

            bram_rd_data <= line_buffer[issue_line_addr];
            read_tag_q1 <= issue_tag;
            read_sub_pixel_q1 <= issue_sub_pixel;
            read_active_q1 <= issue_active;
            read_analog_hblank_q1 <= analog_hblank_now;
            read_analog_vblank_q1 <= analog_vblank_now;
            read_analog_hsync_q1 <= analog_hsync_now;
            read_analog_vsync_q1 <= analog_vsync_now;
            read_analog_dim_q1 <= analog_line_second && analog_scanlines[1];
            read_color_mode_q1 <= color_mode_analog;

            if (issue_lcd_read) begin
                if (!lcd_in_hactive) begin
                    lcd_src_x_scan <= 10'd0;
                    lcd_x_acc <= 11'd0;
                end else begin
                    lcd_src_x_scan <= lcd_src_x_next;
                    lcd_x_acc <= lcd_x_acc_next;
                end
            end

            if (analog_line_start) begin
                analog_out_x <= 10'd0;
                analog_line_y <= y_count;
                analog_line_second <= 1'b0;
                analog_src_x_scan <= 10'd0;
                analog_x_acc <= 11'd0;
            end else if (issue_analog_read) begin
                if (analog_out_x == ANALOG_H_TOTAL_LAST) begin
                    analog_out_x <= 10'd0;
                    analog_line_second <= ~analog_line_second;
                    analog_src_x_scan <= 10'd0;
                    analog_x_acc <= 11'd0;
                end else begin
                    analog_out_x <= analog_out_x + 10'd1;
                    if (!analog_active_now) begin
                        analog_src_x_scan <= 10'd0;
                        analog_x_acc <= 11'd0;
                    end else begin
                        analog_src_x_scan <= analog_src_x_next;
                        analog_x_acc <= analog_x_acc_next;
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

            if (out_tag_q == READ_ANALOG) begin
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

    localparam ST_IDLE = 2'd0;
    localparam ST_BURST = 2'd1;
    localparam ST_WAIT = 2'd2;

    reg [1:0] state;

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

            case (state)
                ST_IDLE: begin
                    if (fetch_line_pending) begin
                        burst_addr <= fb_base_addr +
                            line_offset_for_stride(fetch_src_line_sdram,
                                                   fb_stride_sdram);
                        burst_len <= burst_len_for_mode(color_mode_sdram,
                                                        fb_width_sdram);
                        burst_rd <= 1;
                        write_ptr <= 0;
                        write_bank <= fetch_output_line_sdram[1:0];
                        fetch_line_pending <= 0;
                        state <= ST_BURST;
                    end
                end

                ST_BURST: begin
                    if (burst_data_valid) begin
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
