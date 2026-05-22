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

    // Palette write interface (clk_sdram domain)
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
    localparam VID_V_ACTIVE = 240;
    localparam VID_H_SYNC   = 58;
    localparam VID_H_BPORCH = 62;
    localparam VID_H_FPORCH = 20;
    localparam VID_H_ACTIVE = 640;
    localparam [9:0] VID_H_ACTIVE_START = 10'd120;
    localparam [9:0] VID_H_ACTIVE_END   = 10'd760;
    localparam [9:0] VID_V_FETCH_START  = 10'd17;
    localparam [9:0] VID_V_FETCH_END    = 10'd257;
    localparam [9:0] VID_V_ACTIVE_START = 10'd18;
    localparam [9:0] VID_V_ACTIVE_END   = 10'd258;

    // Scanout line cache.  The video side reads the bank selected by the
    // visible line number while the SDRAM side prefetches a few future lines
    // into different banks.  This avoids read/write contention on a single
    // dual-clock RAM and gives SDRAM arbitration jitter room to breathe.
    //
    // Max line size: RGB565 mode = 320 x 16-bit = 160 x 32-bit words.
    localparam [8:0] LAST_ACTIVE_LINE = 9'd239;
    localparam [8:0] PREFETCH_LOOKAHEAD = 9'd2;

    // Four 256-word banks are used even though only 160 words are needed per
    // line.  The power-of-two bank stride keeps the RAM address simple enough
    // for Quartus to infer block RAM reliably.
    reg [31:0] line_buffer [0:1023];
    reg [31:0] bram_rd_data;
    reg [7:0] write_ptr;
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
    reg [1:0] pal_commit_ack_sdram_sync;
    reg [1:0] pal_commit_req_video_sync;
    wire pal_write_bank = ~pal_commit_ack_sdram_sync[1];
    wire pal_commit_pending_sdram =
        (pal_commit_req_toggle != pal_commit_ack_sdram_sync[1]);
    wire pal_commit_pending_video =
        (pal_commit_req_video_sync[1] != pal_commit_ack_toggle);
    wire frame_start = line_start && (y_count == 10'd0);
    assign pal_busy = pal_commit_pending_sdram;

    always @(posedge clk_sdram or negedge reset_n) begin
        if (!reset_n) begin
            pal_commit_req_toggle <= 1'b0;
            pal_commit_ack_sdram_sync <= 2'b00;
        end else begin
            pal_commit_ack_sdram_sync <= {pal_commit_ack_sdram_sync[0],
                                          pal_commit_ack_toggle};
            if (pal_commit && !pal_commit_pending_sdram)
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
        .clock0(clk_sdram),
        .address_a({pal_write_bank, pal_addr}),
        .data_a(pal_data),
        .wren_a(pal_wr),
        .clock1(clk_video),
        .address_b({pal_active_bank, pal_rd_addr}),
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

    // color_mode is driven by axi_periph_slave in the clk_sdram domain.
    wire [2:0] color_mode_sdram = color_mode;

    // Sync color_mode to the pixel pipeline domain; the CPU side shares
    // clk_sdram, but pixel decode runs on clk_video.
    reg [2:0] color_mode_video_s1, color_mode_video;
    always @(posedge clk_video) begin
        color_mode_video_s1 <= color_mode;
        color_mode_video <= color_mode_video_s1;
    end

    // =========================================
    // Video clock domain - Line start detection
    // =========================================
    wire [9:0] fetch_line = y_count - VID_V_FETCH_START;
    wire in_vactive = (y_count >= VID_V_FETCH_START) &&
                      (y_count < VID_V_FETCH_END);

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
                fetch_line_latched <= fetch_line[8:0];
            end
        end
    end

    // =========================================
    // Video clock domain - Pixel output (multi-mode)
    // =========================================
    wire [9:0] visible_x = x_count - VID_H_ACTIVE_START;
    wire [9:0] visible_y = y_count - VID_V_ACTIVE_START;
    wire [1:0] visible_line_bank = visible_y[1:0];
    wire [7:0] line_rd_index =
        (color_mode_video == MODE_8BIT) ? {1'b0, visible_x[9:3]} :
        (color_mode_video == MODE_4BIT) ? {2'b00, visible_x[9:4]} :
        (color_mode_video == MODE_2BIT) ? {3'b000, visible_x[9:5]} :
                                           visible_x[9:2];
    wire in_hactive = (x_count >= VID_H_ACTIVE_START) &&
                      (x_count < VID_H_ACTIVE_END);
    wire in_vactive_display = (y_count >= VID_V_ACTIVE_START) &&
                              (y_count < VID_V_ACTIVE_END);
    wire [7:0] line_rd_index_safe = in_hactive ? line_rd_index : 8'd0;
    wire [9:0] line_rd_addr = {visible_line_bank, line_rd_index_safe};
    wire [9:0] line_wr_addr = {write_bank, write_ptr};

    // Pipeline stage registers
    (* altera_attribute = "-name AUTO_SHIFT_REGISTER_RECOGNITION OFF" *)
    reg [3:0] sub_pixel_q;  // 4 bits needed for 2-bit mode (16 pixels/word)
    (* altera_attribute = "-name AUTO_SHIFT_REGISTER_RECOGNITION OFF" *)
    reg hactive_q1, hactive_q2, hactive_q3;
    (* altera_attribute = "-name AUTO_SHIFT_REGISTER_RECOGNITION OFF" *)
    reg vactive_q1, vactive_q2, vactive_q3;
    reg [23:0] direct_color;
    reg use_direct, use_direct_q;

    // Barrel-shift pixel extraction (combinational, fed into STAGE 2 registers)
    wire [31:0] shifted_8 = bram_rd_data >> {sub_pixel_q[1:0], 3'b0};  // byte select
    wire [31:0] shifted_4 = bram_rd_data >> {sub_pixel_q[2:0], 2'b0};  // nibble select
    wire [31:0] shifted_2 = bram_rd_data >> {sub_pixel_q[3:0], 1'b0};  // 2-bit select
    wire [15:0] half_word = sub_pixel_q[0] ? bram_rd_data[31:16] : bram_rd_data[15:0];

    always @(posedge clk_video) begin
        // STAGE 1: BRAM address + read
        // visible_x counts 0..639 (2x horizontal), so real pixel = visible_x / 2
        // Word index = real_pixel / pixels_per_word
        bram_rd_data <= line_buffer[line_rd_addr];

        // Latch sub-pixel position (used by STAGE 2 on next clock)
        sub_pixel_q <= visible_x[4:1];
        hactive_q1 <= in_hactive;
        vactive_q1 <= in_vactive_display;

        // STAGE 2: Pixel decode — feed palette BRAM address or latch direct color
        use_direct <= 0;
        case (color_mode_video)
            MODE_8BIT: pal_rd_addr <= shifted_8[7:0];
            MODE_4BIT: pal_rd_addr <= {4'b0, shifted_4[3:0]};
            MODE_2BIT: pal_rd_addr <= {6'b0, shifted_2[1:0]};

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

            default: pal_rd_addr <= shifted_8[7:0];
        endcase

        hactive_q2 <= hactive_q1;
        vactive_q2 <= vactive_q1;

        // STAGE 3: Palette BRAM data available (1-cycle read latency)
        use_direct_q <= use_direct;
        hactive_q3 <= hactive_q2;
        vactive_q3 <= vactive_q2;

        // STAGE 4: Output (palette BRAM result or direct)
        if (hactive_q3 && vactive_q3) begin
            if (use_direct_q)
                pixel_color <= direct_color;
            else
                pixel_color <= pal_rd_data;
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
    reg [8:0] last_request_line;
    reg [8:0] next_fetch_line;
    reg [8:0] prefetch_limit_line;
    reg       prefetch_enabled;
    reg       fetch_line_sdram_valid;

    localparam ST_IDLE = 2'd0;
    localparam ST_BURST = 2'd1;
    localparam ST_WAIT = 2'd2;

    reg [1:0] state;

    function [10:0] burst_len_for_mode;
        input [2:0] mode;
        begin
            case (mode)
                MODE_8BIT: burst_len_for_mode = 11'd80;
                MODE_4BIT: burst_len_for_mode = 11'd40;
                MODE_2BIT: burst_len_for_mode = 11'd20;
                default:   burst_len_for_mode = 11'd160;
            endcase
        end
    endfunction

    // Address offsets match the original scanout addressing convention.
    function [24:0] line_offset_for_mode;
        input [8:0] line;
        input [2:0] mode;
        begin
            case (mode)
                MODE_8BIT: line_offset_for_mode = {line, 7'b0} + {line, 5'b0};
                MODE_4BIT: line_offset_for_mode = {line, 6'b0} + {line, 4'b0};
                MODE_2BIT: line_offset_for_mode = {line, 5'b0} + {line, 3'b0};
                default:   line_offset_for_mode = {line, 8'b0} + {line, 6'b0};
            endcase
        end
    endfunction

    function [8:0] prefetch_limit_for_line;
        input [8:0] line;
        reg [10:0] limit;
        begin
            limit = {2'b00, line} + {2'b00, PREFETCH_LOOKAHEAD};
            if (limit > {2'b00, LAST_ACTIVE_LINE})
                prefetch_limit_for_line = LAST_ACTIVE_LINE;
            else
                prefetch_limit_for_line = limit[8:0];
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
            fetch_line_sdram <= 0;
            fetch_line_sdram_valid <= 0;
            last_request_line <= 0;
            next_fetch_line <= 0;
            prefetch_limit_line <= 0;
            prefetch_enabled <= 0;
        end else begin
            fetch_request_sync1 <= fetch_request;
            fetch_request_sync2 <= fetch_request_sync1;
            burst_rd <= 0;

            if (fetch_line_sdram_valid) begin
                if (!prefetch_enabled || (fetch_line_sdram < last_request_line)) begin
                    next_fetch_line <= fetch_line_sdram;
                    prefetch_limit_line <= prefetch_limit_for_line(fetch_line_sdram);
                    prefetch_enabled <= 1;
                end else if (prefetch_limit_for_line(fetch_line_sdram) > prefetch_limit_line) begin
                    prefetch_limit_line <= prefetch_limit_for_line(fetch_line_sdram);
                end

                last_request_line <= fetch_line_sdram;
                fetch_line_sdram_valid <= 0;
            end

            if (fetch_request_sync2 && !fetch_request_ack) begin
                fetch_line_sdram <= fetch_line_latched;
                fetch_line_sdram_valid <= 1;
                fetch_request_ack <= 1;
            end else if (!fetch_request_sync2) begin
                fetch_request_ack <= 0;
            end

            case (state)
                ST_IDLE: begin
                    if (prefetch_enabled && (next_fetch_line <= prefetch_limit_line)) begin
                        burst_addr <= fb_base_addr + line_offset_for_mode(next_fetch_line, color_mode_sdram);
                        burst_len <= burst_len_for_mode(color_mode_sdram);
                        burst_rd <= 1;
                        write_ptr <= 0;
                        write_bank <= next_fetch_line[1:0];
                        state <= ST_BURST;
                    end
                end

                ST_BURST: begin
                    if (burst_data_valid) begin
                        line_buffer[line_wr_addr] <= burst_data;
                        write_ptr <= write_ptr + 8'd1;
                    end
                    if (burst_data_done) begin
                        next_fetch_line <= next_fetch_line + 9'd1;
                        state <= ST_IDLE;
                    end
                end

                ST_WAIT: begin
                    state <= ST_IDLE;
                end
            endcase
        end
    end

endmodule
