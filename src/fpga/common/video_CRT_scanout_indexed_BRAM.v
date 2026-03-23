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

    // Framebuffer base address (25-bit SDRAM byte address >> 1 = 16-bit word address)
    input wire [24:0] fb_base_addr,

    // Color mode (synced from CPU domain)
    input wire [2:0] color_mode,

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

    // Palette write interface (directly from CPU, active on any clock edge)
    input wire        pal_wr,
    input wire [7:0]  pal_addr,
    input wire [23:0] pal_data      // RGB888 palette data
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
    localparam VID_V_ACTIVE = 240;
    localparam VID_H_SYNC   = 58;
    localparam VID_H_BPORCH = 62;
    localparam VID_H_FPORCH = 20;
    localparam VID_H_ACTIVE = 640;

    // Line buffer: stores one line of pixel data
    // Max size: RGB565 mode = 320 x 16-bit = 160 x 32-bit words
    // 8-bit mode = 320 x 8-bit = 80 x 32-bit words
    reg [31:0] line_buffer [0:159];
    reg [31:0] bram_rd_data;
    reg [7:0] write_ptr;

    // Palette RAM: 256 entries x 24-bit RGB
    reg [23:0] palette [0:255];

    // Use 32-bit burst mode
    assign burst_32bit = 1'b1;

    // Sync color_mode to SDRAM domain
    reg [2:0] color_mode_sdram_s1, color_mode_sdram;
    always @(posedge clk_sdram) begin
        color_mode_sdram_s1 <= color_mode;
        color_mode_sdram <= color_mode_sdram_s1;
    end

    // =========================================
    // Palette write
    // =========================================
    always @(posedge clk_sdram) begin
        if (pal_wr)
            palette[pal_addr] <= pal_data;
    end

    // =========================================
    // Video clock domain - Line start detection
    // =========================================
    wire [9:0] fetch_line = y_count - VID_V_SYNC - VID_V_BPORCH + 1;
    wire in_vactive = (y_count >= (VID_V_SYNC + VID_V_BPORCH - 1)) &&
                      (y_count < (VID_V_SYNC + VID_V_BPORCH + VID_V_ACTIVE - 1));

    reg fetch_request;
    reg fetch_request_ack_sync1, fetch_request_ack_sync2;
    reg [8:0] fetch_line_latched;

    always @(posedge clk_video or negedge reset_n) begin
        if (!reset_n) begin
            fetch_request <= 0;
            fetch_line_latched <= 0;
            fetch_request_ack_sync1 <= 0;
            fetch_request_ack_sync2 <= 0;
        end else begin
            fetch_request_ack_sync1 <= fetch_request_ack;
            fetch_request_ack_sync2 <= fetch_request_ack_sync1;

            if (fetch_request_ack_sync2)
                fetch_request <= 0;

            if (line_start && in_vactive && !fetch_request) begin
                fetch_request <= 1;
                fetch_line_latched <= fetch_line[9:0];
            end
        end
    end

    // =========================================
    // Video clock domain - Pixel output (multi-mode)
    // =========================================
    wire [9:0] visible_x = x_count - VID_H_SYNC - VID_H_BPORCH;
    wire in_hactive = (x_count >= (VID_H_SYNC + VID_H_BPORCH)) &&
                      (x_count < (VID_H_SYNC + VID_H_BPORCH + VID_H_ACTIVE));
    wire in_vactive_display = (y_count >= (VID_V_SYNC + VID_V_BPORCH)) &&
                              (y_count < (VID_V_SYNC + VID_V_BPORCH + VID_V_ACTIVE));

    // Pipeline stage registers
    reg [3:0] sub_pixel_q;  // 4 bits needed for 2-bit mode (16 pixels/word)
    reg hactive_q1, hactive_q2;
    reg vactive_q1, vactive_q2;
    reg [7:0] palette_index;
    reg [23:0] direct_color;
    reg use_direct;

    // Barrel-shift pixel extraction (combinational, fed into STAGE 2 registers)
    wire [31:0] shifted_8 = bram_rd_data >> {sub_pixel_q[1:0], 3'b0};  // byte select
    wire [31:0] shifted_4 = bram_rd_data >> {sub_pixel_q[2:0], 2'b0};  // nibble select
    wire [31:0] shifted_2 = bram_rd_data >> {sub_pixel_q[3:0], 1'b0};  // 2-bit select
    wire [15:0] half_word = sub_pixel_q[0] ? bram_rd_data[31:16] : bram_rd_data[15:0];

    always @(posedge clk_video) begin
        // STAGE 1: BRAM address + read
        // visible_x counts 0..639 (2x horizontal), so real pixel = visible_x / 2
        // Word index = real_pixel / pixels_per_word
        case (color_mode)
            MODE_8BIT:    bram_rd_data <= line_buffer[visible_x[9:3]];   // 4 pixels per word, idx = visible_x/8
            MODE_4BIT:    bram_rd_data <= line_buffer[visible_x[9:4]];   // 8 pixels per word, idx = visible_x/16
            MODE_2BIT:    bram_rd_data <= line_buffer[visible_x[9:5]];   // 16 pixels per word, idx = visible_x/32
            default:      bram_rd_data <= line_buffer[visible_x[9:2]];   // 2 pixels per word, idx = visible_x/4
        endcase

        // Latch sub-pixel position (used by STAGE 2 on next clock)
        sub_pixel_q <= visible_x[4:1];
        hactive_q1 <= in_hactive;
        vactive_q1 <= in_vactive_display;

        // STAGE 2: Pixel decode (uses latched sub_pixel_q matching bram_rd_data)
        // Barrel-shift wires (defined above) replace large case-mux trees.
        use_direct <= 0;
        case (color_mode)
            MODE_8BIT: palette_index <= shifted_8[7:0];
            MODE_4BIT: palette_index <= {4'b0, shifted_4[3:0]};
            MODE_2BIT: palette_index <= {6'b0, shifted_2[1:0]};

            MODE_RGB565: begin
                use_direct <= 1;
                direct_color <= {half_word[15:11], 3'b0, half_word[10:5], 2'b0, half_word[4:0], 3'b0};
            end

            MODE_RGB555: begin
                use_direct <= 1;
                direct_color <= {half_word[14:10], 3'b0, half_word[9:5], 3'b0, half_word[4:0], 3'b0};
            end

            MODE_RGBA5551: begin
                use_direct <= 1;
                if (half_word[0])
                    direct_color <= {half_word[15:11], 3'b0, half_word[10:6], 3'b0, half_word[5:1], 3'b0};
                else
                    direct_color <= 24'h000000;
            end

            default: palette_index <= shifted_8[7:0];
        endcase

        hactive_q2 <= hactive_q1;
        vactive_q2 <= vactive_q1;

        // STAGE 3: Output (palette lookup or direct)
        if (hactive_q2 && vactive_q2) begin
            if (use_direct)
                pixel_color <= direct_color;
            else
                pixel_color <= palette[palette_index];
        end else begin
            pixel_color <= 24'h000000;
        end
    end

    // =========================================
    // SDRAM clock domain - Burst read FSM
    // =========================================
    reg fetch_request_sync1, fetch_request_sync2;
    reg fetch_request_ack;
    reg [8:0] fetch_line_sdram;

    localparam ST_IDLE = 2'd0;
    localparam ST_BURST = 2'd1;
    localparam ST_WAIT = 2'd2;

    reg [1:0] state;

    // Burst length depends on color mode:
    //   8-bit:  80 words (320 bytes / 4)
    //   4-bit:  40 words (160 bytes / 4)
    //   2-bit:  20 words (80 bytes / 4)
    //   16bpp: 160 words (640 bytes / 4)
    reg [10:0] mode_burst_len;
    reg [24:0] mode_line_offset;

    // Line offset: registered to break combinational path in SDRAM domain.
    // Computed when fetch_request arrives (ST_IDLE → ST_BURST transition).
    // Uses shift+add for multiply: line*160, line*80, line*40, line*320.
    always @(posedge clk_sdram) begin
        case (color_mode_sdram)
            MODE_8BIT: begin mode_burst_len <= 11'd80;  mode_line_offset <= {fetch_line_latched, 7'b0} + {fetch_line_latched, 5'b0}; end
            MODE_4BIT: begin mode_burst_len <= 11'd40;  mode_line_offset <= {fetch_line_latched, 6'b0} + {fetch_line_latched, 4'b0}; end
            MODE_2BIT: begin mode_burst_len <= 11'd20;  mode_line_offset <= {fetch_line_latched, 5'b0} + {fetch_line_latched, 3'b0}; end
            default:   begin mode_burst_len <= 11'd160; mode_line_offset <= {fetch_line_latched, 8'b0} + {fetch_line_latched, 6'b0}; end
        endcase
    end

    always @(posedge clk_sdram or negedge reset_n) begin
        if (!reset_n) begin
            state <= ST_IDLE;
            burst_rd <= 0;
            burst_addr <= 0;
            burst_len <= 0;
            write_ptr <= 0;
            fetch_request_sync1 <= 0;
            fetch_request_sync2 <= 0;
            fetch_request_ack <= 0;
            fetch_line_sdram <= 0;
        end else begin
            fetch_request_sync1 <= fetch_request;
            fetch_request_sync2 <= fetch_request_sync1;
            burst_rd <= 0;

            case (state)
                ST_IDLE: begin
                    fetch_request_ack <= 0;
                    if (fetch_request_sync2 && !fetch_request_ack) begin
                        fetch_line_sdram <= fetch_line_latched;
                        burst_addr <= fb_base_addr + mode_line_offset;
                        burst_len <= mode_burst_len;
                        burst_rd <= 1;
                        write_ptr <= 0;
                        state <= ST_BURST;
                    end
                end

                ST_BURST: begin
                    if (burst_data_valid) begin
                        line_buffer[write_ptr] <= burst_data;
                        write_ptr <= write_ptr + 1;
                    end
                    if (burst_data_done) begin
                        fetch_request_ack <= 1;
                        state <= ST_WAIT;
                    end
                end

                ST_WAIT: begin
                    if (!fetch_request_sync2) begin
                        fetch_request_ack <= 0;
                        state <= ST_IDLE;
                    end
                end
            endcase
        end
    end

endmodule
