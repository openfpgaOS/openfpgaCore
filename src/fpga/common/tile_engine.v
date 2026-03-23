//
// Hardware Tile Engine for openfpgaOS
//
// 64x32 tilemap, 256 tiles (8x8 pixels, 4bpp), 16 sub-palettes.
// Real-time 2-stage pipeline: tilemap read -> chr read -> pixel output.
// Shares the framebuffer's 256-entry palette via duplicated palette RAM.
//
// Tilemap entry format (16-bit):
//   [15]    = vflip
//   [14]    = hflip
//   [13:10] = palette (selects 16-color sub-palette)
//   [9:8]   = reserved
//   [7:0]   = tile_id (0-255)
//
// Tile character data format (32-bit per row):
//   8 pixels x 4bpp, pixel 0 in bits [3:0], pixel 7 in bits [31:28]
//   Address = tile_id * 8 + row (0-7)
//
// Output: 24-bit RGB from shared palette, with opaque flag.
// Pipeline latency: 1 logical pixel (2 CRT clocks).
//

`default_nettype none

module tile_engine (
    input wire clk_video,         // 12.288 MHz (video scanout)
    input wire clk_cpu,           // 100 MHz (CPU / register writes)
    input wire reset_n,

    // Scanout position (clk_video domain)
    input wire [9:0] visible_x,   // 0-639 (each logical pixel doubled)
    input wire [8:0] visible_y,   // 0-239
    input wire active_video,      // high during visible region

    // Output pixel (clk_video domain)
    output reg [23:0] tile_rgb,   // 24-bit RGB from palette
    output reg tile_opaque,       // high when tile pixel is non-transparent

    // Control registers (clk_cpu domain, CDC handled internally)
    input wire tile_enable,
    input wire tile_priority,     // 0 = behind framebuffer, 1 = over framebuffer
    input wire [8:0] scroll_x,   // horizontal scroll (0-511, wraps tilemap)
    input wire [7:0] scroll_y,   // vertical scroll (0-255, wraps tilemap)

    // CPU tilemap write port (clk_cpu domain)
    input wire tilemap_wr,
    input wire [10:0] tilemap_waddr,  // 64*32 = 2048 entries
    input wire [15:0] tilemap_wdata,

    // CPU tile character data write port (clk_cpu domain)
    input wire tilechar_wr,
    input wire [10:0] tilechar_waddr, // 256 tiles * 8 rows = 2048 words
    input wire [31:0] tilechar_wdata,

    // Palette write (clk_cpu domain, shared with framebuffer)
    input wire pal_wr,
    input wire [7:0] pal_addr,
    input wire [23:0] pal_data
);

// ============================================
// CDC: Control registers (CPU -> video domain)
// These change between frames; 2FF sync is sufficient.
// ============================================
reg en_s1, en_s2;
reg [8:0] sx_s1, sx_s2;
reg [7:0] sy_s1, sy_s2;

always @(posedge clk_video) begin
    en_s1  <= tile_enable;    en_s2  <= en_s1;
    sx_s1  <= scroll_x;       sx_s2  <= sx_s1;
    sy_s1  <= scroll_y;       sy_s2  <= sy_s1;
end

// ============================================
// Tilemap BRAM (2048 x 16-bit, true dual-port dual-clock)
//   Port A: CPU write (clk_cpu)
//   Port B: Video read (clk_video)
// ============================================
(* ramstyle = "M10K" *)
reg [15:0] tilemap [0:2047];

always @(posedge clk_cpu) begin
    if (tilemap_wr)
        tilemap[tilemap_waddr] <= tilemap_wdata;
end

reg [10:0] tm_rd_addr;
reg [15:0] tm_rd_data;
always @(posedge clk_video) begin
    tm_rd_data <= tilemap[tm_rd_addr];
end

// ============================================
// Tile character data BRAM (2048 x 32-bit, true dual-port dual-clock)
//   Port A: CPU write (clk_cpu)
//   Port B: Video read (clk_video)
// ============================================
(* ramstyle = "M10K" *)
reg [31:0] tilechar [0:2047];

always @(posedge clk_cpu) begin
    if (tilechar_wr)
        tilechar[tilechar_waddr] <= tilechar_wdata;
end

reg [10:0] chr_rd_addr;
reg [31:0] chr_rd_data;
always @(posedge clk_video) begin
    chr_rd_data <= tilechar[chr_rd_addr];
end

// ============================================
// Palette RAM (256 x 24-bit)
// Duplicated from framebuffer palette. Written on clk_cpu,
// read combinatorially on clk_video for zero-latency lookup.
// Uses ALM-based distributed RAM (~200 ALMs).
// ============================================
(* ramstyle = "logic" *)
reg [23:0] palette [0:255];

always @(posedge clk_cpu) begin
    if (pal_wr)
        palette[pal_addr] <= pal_data;
end

// ============================================
// Scrolled coordinates (combinatorial)
// ============================================
wire [8:0] pixel_x   = visible_x[9:1];          // logical pixel (0-319)
wire [8:0] scroll_px  = pixel_x + sx_s2;          // scrolled X (wraps at 512)
wire [7:0] scroll_py  = visible_y[7:0] + sy_s2;   // scrolled Y (wraps at 256)

wire [5:0] tile_x  = scroll_px[8:3];   // tile column (0-63)
wire [4:0] tile_y  = scroll_py[7:3];   // tile row (0-31)
wire [2:0] fine_x  = scroll_px[2:0];   // pixel within tile (0-7)
wire [2:0] fine_y  = scroll_py[2:0];   // row within tile (0-7)

// ============================================
// Pipeline registers
// ============================================
reg [2:0]  s1_fine_x;       // latched fine_x (with hflip applied)
reg [3:0]  s1_palette;      // latched sub-palette index
reg        s1_valid;         // stage 1 produced valid data

// ============================================
// Nibble extraction (combinatorial from chr data)
// ============================================
reg [3:0] chr_nibble;
always @(*) begin
    case (s1_fine_x)
        3'd0: chr_nibble = chr_rd_data[3:0];
        3'd1: chr_nibble = chr_rd_data[7:4];
        3'd2: chr_nibble = chr_rd_data[11:8];
        3'd3: chr_nibble = chr_rd_data[15:12];
        3'd4: chr_nibble = chr_rd_data[19:16];
        3'd5: chr_nibble = chr_rd_data[23:20];
        3'd6: chr_nibble = chr_rd_data[27:24];
        3'd7: chr_nibble = chr_rd_data[31:28];
    endcase
end

// Palette index for current tile pixel
wire [7:0] tile_pal_idx = {s1_palette, chr_nibble};

// ============================================
// 2-stage pipeline
//
// Even CRT clock (visible_x[0]==0):
//   - Issue tilemap read for current logical pixel
//   - Extract pixel from chr data (from previous odd cycle)
//   - Palette lookup and output
//
// Odd CRT clock (visible_x[0]==1):
//   - Tilemap data ready -> issue chr data read
//   - Hold previous output (natural pixel doubling)
// ============================================
always @(posedge clk_video or negedge reset_n) begin
    if (!reset_n) begin
        tile_rgb    <= 24'd0;
        tile_opaque <= 1'b0;
        s1_valid    <= 1'b0;
        s1_fine_x   <= 3'd0;
        s1_palette  <= 4'd0;
        tm_rd_addr  <= 11'd0;
        chr_rd_addr <= 11'd0;
    end else if (active_video && en_s2) begin
        if (!visible_x[0]) begin
            // ===== EVEN CRT CLOCK =====
            // Stage 0: Issue tilemap read for current logical pixel
            tm_rd_addr <= {tile_y, tile_x};

            // Stage 2: Extract pixel from chr data (ready from previous odd cycle)
            if (s1_valid) begin
                tile_opaque <= (chr_nibble != 4'd0);
                tile_rgb    <= (chr_nibble != 4'd0) ? palette[tile_pal_idx] : 24'd0;
            end else begin
                tile_rgb    <= 24'd0;
                tile_opaque <= 1'b0;
            end
        end else begin
            // ===== ODD CRT CLOCK =====
            // Stage 1: Tilemap data ready, parse entry and issue chr read
            s1_fine_x  <= tm_rd_data[14] ? (3'd7 - fine_x) : fine_x;   // hflip
            s1_palette <= tm_rd_data[13:10];
            s1_valid   <= 1'b1;

            // Chr address: tile_id[7:0] * 8 + row (with vflip)
            chr_rd_addr <= {tm_rd_data[7:0],
                            tm_rd_data[15] ? (3'd7 - fine_y) : fine_y};

            // Output held from even clock (pixel doubling)
        end
    end else begin
        tile_rgb    <= 24'd0;
        tile_opaque <= 1'b0;
        s1_valid    <= 1'b0;
    end
end

endmodule
