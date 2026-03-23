//
// Text Terminal Display for 320x240 resolution
// 40 columns x 30 rows, 8x8 pixel font
// Character VRAM at 0x20000000 (1200 bytes)
// Color VRAM at 0x20000800 (1200 bytes) -- [7:4]=bg, [3:0]=fg
//

`default_nettype none

module text_terminal (
    input wire clk,           // Video clock (12.288 MHz)
    input wire clk_cpu,       // CPU clock (74.25 MHz)
    input wire reset_n,

    // Video interface
    input wire [9:0] pixel_x,
    input wire [9:0] pixel_y,
    output reg [23:0] pixel_color,

    // CPU memory interface (directly exposed for memory mapping)
    input wire        mem_valid,
    input wire [31:0] mem_addr,
    input wire [31:0] mem_wdata,
    input wire [3:0]  mem_wstrb,
    output reg [31:0] mem_rdata,
    output reg        mem_ready
);

// Terminal dimensions (320x240 with 8x8 font = 40x30 characters)
localparam TERM_COLS = 40;
localparam TERM_ROWS = 30;
localparam TERM_SIZE = TERM_COLS * TERM_ROWS;  // 1200 characters

// Character dimensions
localparam CHAR_WIDTH = 8;
localparam CHAR_HEIGHT = 8;

wire [9:0] fetch_x = pixel_x;

// Calculate character position from pixel coordinates
wire [5:0] char_col = fetch_x[8:3];  // fetch_x / 8 (max 39 for 40 cols)
wire [4:0] char_row = pixel_y[7:3];  // pixel_y / 8 (max 29 for 30 rows)
wire [2:0] pixel_col = fetch_x[2:0]; // fetch_x % 8
wire [2:0] pixel_row = pixel_y[2:0]; // pixel_y % 8

// Calculate VRAM address for current character
// char_index = char_row * 40 + char_col
wire [10:0] char_index = ({char_row, 5'b0} + {char_row, 3'b0} + char_col);
wire [8:0] vram_word_addr = char_index[10:2];  // Divide by 4 for 32-bit words
wire [1:0]  vram_byte_sel = char_index[1:0];    // Which byte in the word

// CPU address decoding
// 0x20000000..0x200004FF = character VRAM (1200 bytes)
// 0x20000800..0x20000CFF = color VRAM (1200 bytes)
wire cpu_in_range = (mem_addr[31:13] == 19'h10000);  // 0x20000000 range
wire cpu_is_color = mem_addr[11];                      // bit 11 = color RAM select
wire cpu_addr_char = cpu_in_range && !cpu_is_color;
wire cpu_addr_color = cpu_in_range && cpu_is_color;
wire [10:0] cpu_word_addr = mem_addr[12:2];
wire [8:0]  cpu_color_word_addr = mem_addr[10:2];      // Within color RAM

// ======================================================================
// Character VRAM (dual-port block RAM)
// Port A: Video read, Port B: CPU read/write
// ======================================================================
wire [31:0] vram_video_data;
wire [31:0] vram_cpu_data;

altsyncram #(
    .operation_mode("BIDIR_DUAL_PORT"),
    .width_a(32),
    .widthad_a(9),
    .numwords_a(300),
    .width_b(32),
    .widthad_b(9),
    .numwords_b(300),
    .width_byteena_b(4),
    .lpm_type("altsyncram"),
    .outdata_reg_a("UNREGISTERED"),
    .outdata_reg_b("UNREGISTERED"),
    .init_file("../../common/vram_init.mif"),
    .intended_device_family("Cyclone V"),
    .read_during_write_mode_port_a("NEW_DATA_NO_NBE_READ"),
    .read_during_write_mode_port_b("NEW_DATA_NO_NBE_READ")
) vram (
    .clock0(clk),
    .address_a(vram_word_addr),
    .data_a(32'b0),
    .wren_a(1'b0),
    .q_a(vram_video_data),

    .clock1(clk_cpu),
    .address_b(cpu_word_addr[8:0]),
    .data_b(mem_wdata),
    .wren_b(mem_valid && !mem_ready && cpu_addr_char && |mem_wstrb),
    .byteena_b(mem_wstrb),
    .q_b(vram_cpu_data),

    .aclr0(1'b0), .aclr1(1'b0),
    .addressstall_a(1'b0), .addressstall_b(1'b0),
    .byteena_a(1'b1),
    .clocken0(1'b1), .clocken1(1'b1), .clocken2(1'b1), .clocken3(1'b1),
    .eccstatus(), .rden_a(1'b1), .rden_b(1'b1)
);

// ======================================================================
// Color VRAM (dual-port block RAM)
// Same layout as character VRAM, 1 byte per cell: [7:4]=bg, [3:0]=fg
// Initialized to 0x0F (black background, white foreground)
// ======================================================================
wire [31:0] color_video_data;
wire [31:0] color_cpu_data;

altsyncram #(
    .operation_mode("BIDIR_DUAL_PORT"),
    .width_a(32),
    .widthad_a(9),
    .numwords_a(300),
    .width_b(32),
    .widthad_b(9),
    .numwords_b(300),
    .width_byteena_b(4),
    .lpm_type("altsyncram"),
    .outdata_reg_a("UNREGISTERED"),
    .outdata_reg_b("UNREGISTERED"),
    .init_file("../../common/color_init.mif"),
    .intended_device_family("Cyclone V"),
    .read_during_write_mode_port_a("NEW_DATA_NO_NBE_READ"),
    .read_during_write_mode_port_b("NEW_DATA_NO_NBE_READ")
) color_ram (
    .clock0(clk),
    .address_a(vram_word_addr),
    .data_a(32'b0),
    .wren_a(1'b0),
    .q_a(color_video_data),

    .clock1(clk_cpu),
    .address_b(cpu_color_word_addr),
    .data_b(mem_wdata),
    .wren_b(mem_valid && !mem_ready && cpu_addr_color && |mem_wstrb),
    .byteena_b(mem_wstrb),
    .q_b(color_cpu_data),

    .aclr0(1'b0), .aclr1(1'b0),
    .addressstall_a(1'b0), .addressstall_b(1'b0),
    .byteena_a(1'b1),
    .clocken0(1'b1), .clocken1(1'b1), .clocken2(1'b1), .clocken3(1'b1),
    .eccstatus(), .rden_a(1'b1), .rden_b(1'b1)
);

// ======================================================================
// 16-color palette (VGA/CGA-style)
// ======================================================================
reg [23:0] palette [0:15];
initial begin
    palette[ 0] = 24'h000000;  // Black
    palette[ 1] = 24'h0000AA;  // Dark Blue
    palette[ 2] = 24'h00AA00;  // Dark Green
    palette[ 3] = 24'h00AAAA;  // Dark Cyan
    palette[ 4] = 24'hAA0000;  // Dark Red
    palette[ 5] = 24'hAA00AA;  // Dark Magenta
    palette[ 6] = 24'hAA5500;  // Brown
    palette[ 7] = 24'hAAAAAA;  // Light Gray
    palette[ 8] = 24'h555555;  // Dark Gray
    palette[ 9] = 24'h5555FF;  // Blue
    palette[10] = 24'h55FF55;  // Green
    palette[11] = 24'h55FFFF;  // Cyan
    palette[12] = 24'hFF5555;  // Red
    palette[13] = 24'hFF55FF;  // Magenta
    palette[14] = 24'hFFFF55;  // Yellow
    palette[15] = 24'hFFFFFF;  // White
end

// ======================================================================
// Pipeline stage 1: VRAM + Color RAM read latency
// ======================================================================
reg [1:0] vram_byte_sel_d1;
reg [2:0] pixel_col_d1;
reg [2:0] pixel_row_d1;
reg [9:0] pixel_x_d1;
reg [9:0] pixel_y_d1;

always @(posedge clk) begin
    vram_byte_sel_d1 <= vram_byte_sel;
    pixel_col_d1 <= pixel_col;
    pixel_row_d1 <= pixel_row;
    pixel_x_d1 <= pixel_x;
    pixel_y_d1 <= pixel_y;
end

// Select character and color bytes from their respective words
reg [7:0] current_char;
reg [7:0] current_color;
always @(*) begin
    case (vram_byte_sel_d1)
        2'd0: begin current_char = vram_video_data[ 7: 0]; current_color = color_video_data[ 7: 0]; end
        2'd1: begin current_char = vram_video_data[15: 8]; current_color = color_video_data[15: 8]; end
        2'd2: begin current_char = vram_video_data[23:16]; current_color = color_video_data[23:16]; end
        2'd3: begin current_char = vram_video_data[31:24]; current_color = color_video_data[31:24]; end
    endcase
end

wire [3:0] fg_index = current_color[3:0];
wire [3:0] bg_index = current_color[7:4];

// ======================================================================
// Font ROM (2048 bytes: 256 characters x 8 rows, CP437)
// ======================================================================
wire [10:0] font_addr;
wire [7:0] font_data;

assign font_addr = {current_char, pixel_row_d1};

altsyncram #(
    .operation_mode("ROM"),
    .width_a(8),
    .widthad_a(11),
    .numwords_a(2048),
    .lpm_type("altsyncram"),
    .outdata_reg_a("UNREGISTERED"),
    .init_file("../../common/font_rom.mif"),
    .intended_device_family("Cyclone V")
) font_rom (
    .clock0(clk),
    .address_a(font_addr),
    .q_a(font_data),
    .aclr0(1'b0), .aclr1(1'b0),
    .address_b(1'b0),
    .addressstall_a(1'b0), .addressstall_b(1'b0),
    .byteena_a(1'b1), .byteena_b(1'b1),
    .clock1(1'b1),
    .clocken0(1'b1), .clocken1(1'b1), .clocken2(1'b1), .clocken3(1'b1),
    .data_a({8{1'b0}}), .data_b({8{1'b0}}),
    .eccstatus(), .q_b(),
    .rden_a(1'b1), .rden_b(1'b0),
    .wren_a(1'b0), .wren_b(1'b0)
);

// ======================================================================
// Pipeline stage 2: Font ROM read latency
// ======================================================================
reg [2:0] pixel_col_d2;
reg [9:0] pixel_x_d2;
reg [9:0] pixel_y_d2;
reg [3:0] fg_index_d2;
reg [3:0] bg_index_d2;

always @(posedge clk) begin
    pixel_col_d2 <= pixel_col_d1;
    pixel_x_d2 <= pixel_x_d1;
    pixel_y_d2 <= pixel_y_d1;
    fg_index_d2 <= fg_index;
    bg_index_d2 <= bg_index;
end

// Visible area check
wire in_visible_area = (pixel_x_d2 < 320) && (pixel_y_d2 < 240);

// Get pixel value (MSB first)
wire pixel_on = font_data[7 - pixel_col_d2];

// Generate pixel color from palette
always @(*) begin
    if (in_visible_area && pixel_on)
        pixel_color = palette[fg_index_d2];
    else if (in_visible_area)
        pixel_color = palette[bg_index_d2];
    else
        pixel_color = 24'h000000;
end

// ======================================================================
// CPU memory interface
// Handles both character VRAM and color VRAM reads and writes.
// ======================================================================
reg mem_pending;
reg mem_color_pending;

always @(posedge clk_cpu) begin
    mem_ready <= 0;

    if (!reset_n) begin
        mem_pending <= 0;
        mem_color_pending <= 0;
    end else if (mem_valid && !mem_ready && !mem_pending) begin
        mem_pending <= 1;
        mem_color_pending <= cpu_is_color;
    end else if (mem_pending) begin
        mem_ready <= 1;
        mem_pending <= 0;

        if (cpu_in_range)
            mem_rdata <= mem_color_pending ? color_cpu_data : vram_cpu_data;
        else
            mem_rdata <= 32'h0;
    end
end

endmodule
