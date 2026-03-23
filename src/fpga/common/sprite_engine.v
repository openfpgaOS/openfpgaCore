//
// Hardware Sprite Engine for openfpgaOS
//
// 64 sprites, 8x8 pixels, 4bpp, per-sprite flip/palette/enable.
// Renders into a double-buffered line buffer during hblank (clk_cpu domain).
// Video readout from line buffer on clk_video domain.
//
// Sprite Attribute Table (SAT) entry format (written as 3 registers):
//   Word 0 (X/Y):    [24:16] = Y (0-255), [8:0] = X (0-511, wraps)
//   Word 1 (Attr):   [15] = enable, [14] = vflip, [13] = hflip,
//                    [12] = size (0=8x8, 1=reserved), [11:8] = palette,
//                    [7:0] = tile_id
//
// Sprite character data: same 4bpp format as tile engine.
// 256 tiles x 8 rows x 32-bit = 2048 words in BRAM.
//
// Rendering priority: sprite 0 = highest, sprite 63 = lowest.
// Rendered back-to-front so higher-priority sprites overwrite lower ones.
//

`default_nettype none

module sprite_engine (
    input wire clk_video,         // 12.288 MHz (video scanout)
    input wire clk_cpu,           // 100 MHz (rendering + CPU writes)
    input wire reset_n,

    // Scanout position (clk_video domain)
    input wire [9:0] visible_x,   // 0-639 (each logical pixel doubled)
    input wire active_video,      // high during visible region

    // Line timing (clk_video domain, CDC'd internally)
    input wire hblank,            // high during horizontal blanking
    input wire vblank,            // high during vertical blanking
    input wire [8:0] visible_y,   // current scanline (0-239)

    // Output pixel (clk_video domain)
    output wire [23:0] sprite_rgb,
    output wire sprite_opaque,

    // Control (clk_cpu domain)
    input wire sprite_enable,

    // SAT write port (clk_cpu domain)
    input wire sat_wr,
    input wire [5:0] sat_idx,     // sprite index (0-63)
    input wire [1:0] sat_field,   // 0=X/Y, 1=attr
    input wire [31:0] sat_wdata,

    // Sprite character data write port (clk_cpu domain)
    input wire sprchar_wr,
    input wire [10:0] sprchar_waddr,
    input wire [31:0] sprchar_wdata,

    // Palette (clk_cpu domain, shared)
    input wire pal_wr,
    input wire [7:0] pal_addr,
    input wire [23:0] pal_data
);

// ============================================
// Sprite Attribute Table (64 sprites, register-based)
// ============================================
reg [8:0]  sat_x    [0:63];  // X position (0-511)
reg [8:0]  sat_y    [0:63];  // Y position (0-255, 9-bit for off-screen)
reg [7:0]  sat_tile [0:63];  // tile_id
reg [3:0]  sat_pal  [0:63];  // palette
reg        sat_hf   [0:63];  // hflip
reg        sat_vf   [0:63];  // vflip
reg        sat_en   [0:63];  // enable

integer k;
always @(posedge clk_cpu) begin
    if (!reset_n) begin
        for (k = 0; k < 64; k = k + 1) begin
            sat_x[k]    <= 9'd0;
            sat_y[k]    <= 9'd0;
            sat_tile[k] <= 8'd0;
            sat_pal[k]  <= 4'd0;
            sat_hf[k]   <= 1'b0;
            sat_vf[k]   <= 1'b0;
            sat_en[k]   <= 1'b0;
        end
    end else if (sat_wr) begin
        case (sat_field)
            2'd0: begin  // X/Y
                sat_x[sat_idx] <= sat_wdata[8:0];
                sat_y[sat_idx] <= sat_wdata[24:16];
            end
            2'd1: begin  // Attributes
                sat_tile[sat_idx] <= sat_wdata[7:0];
                sat_pal[sat_idx]  <= sat_wdata[11:8];
                sat_hf[sat_idx]   <= sat_wdata[13];
                sat_vf[sat_idx]   <= sat_wdata[14];
                sat_en[sat_idx]   <= sat_wdata[15];
            end
            default: ;
        endcase
    end
end

// ============================================
// Sprite character data BRAM (2048 x 32-bit, dual-port)
//   Port A: CPU write (clk_cpu)
//   Port B: Renderer read (clk_cpu)
// ============================================
(* ramstyle = "M10K" *)
reg [31:0] sprchar [0:2047];

always @(posedge clk_cpu) begin
    if (sprchar_wr)
        sprchar[sprchar_waddr] <= sprchar_wdata;
end

reg [10:0] chr_rd_addr_r;
reg [31:0] chr_rd_data_r;
always @(posedge clk_cpu) begin
    chr_rd_data_r <= sprchar[chr_rd_addr_r];
end

// ============================================
// Palette RAM (duplicated, ALM-based for combinatorial read)
// ============================================
(* ramstyle = "logic" *)
reg [23:0] palette [0:255];

always @(posedge clk_cpu) begin
    if (pal_wr)
        palette[pal_addr] <= pal_data;
end

// ============================================
// Double-buffered sprite line buffer
// Written by renderer (clk_cpu), read by video (clk_video).
// 320 x 8-bit palette indices per buffer, 2 buffers = 640 entries.
// ============================================
// Buffer 0 = addresses 0-511, Buffer 1 = addresses 512-1023
// Only 0-319 and 512-831 used (320 pixels per line)
(* ramstyle = "M10K" *)
reg [7:0] linebuf [0:1023];

// Write port (clk_cpu, renderer)
reg [9:0] lb_waddr;
reg [7:0] lb_wdata;
reg       lb_wr;

always @(posedge clk_cpu) begin
    if (lb_wr)
        linebuf[lb_waddr] <= lb_wdata;
end

// Read port (clk_video)
reg [9:0] lb_raddr;
reg [7:0] lb_rdata;
always @(posedge clk_video) begin
    lb_rdata <= linebuf[lb_raddr];
end

// Buffer select: toggles each line
reg lb_draw_sel;   // 0 or 1, selects which half renderer writes to
reg lb_disp_sel;   // opposite of lb_draw_sel, for video readout

// ============================================
// CDC: hblank/vblank/visible_y into clk_cpu domain
// ============================================
reg [2:0] hblank_sync;
reg [2:0] vblank_sync;
reg [8:0] vis_y_s1, vis_y_s2;
reg       en_s1, en_s2;

always @(posedge clk_cpu) begin
    hblank_sync <= {hblank_sync[1:0], hblank};
    vblank_sync <= {vblank_sync[1:0], vblank};
    vis_y_s1    <= visible_y;
    vis_y_s2    <= vis_y_s1;
    en_s1       <= sprite_enable;
    en_s2       <= en_s1;
end

wire hblank_rise = hblank_sync[1] && !hblank_sync[2];
wire vblank_rise = vblank_sync[1] && !vblank_sync[2];

// ============================================
// Renderer FSM (clk_cpu domain)
//
// Triggered on hblank rising edge.
// 1) Clear the draw line buffer
// 2) Scan sprites 63 -> 0 (back to front for priority)
// 3) For each sprite intersecting this scanline:
//    a) Read chr data row
//    b) Write 8 pixels to line buffer
// ============================================
localparam R_IDLE     = 3'd0;
localparam R_CLEAR    = 3'd1;
localparam R_SCAN     = 3'd2;
localparam R_CHR_RD   = 3'd3;
localparam R_PIXELS   = 3'd4;
localparam R_DONE     = 3'd5;

reg [2:0] r_state;
reg [8:0] r_scanline;     // scanline being rendered (next visible line)
reg [5:0] r_spr_idx;      // current sprite index (63 down to 0)
reg [8:0] r_clear_x;      // clear counter (0-319)
reg [2:0] r_px_count;     // pixel counter within sprite (0-7)
reg [31:0] r_chr_data;    // latched chr data
reg [8:0]  r_spr_x;       // latched sprite X
reg [3:0]  r_spr_pal;     // latched sprite palette
reg        r_spr_hflip;   // latched hflip
reg        r_chr_ready;   // chr data is valid

// Combinatorial helpers for sprite rendering
reg [2:0]  r_row_calc;    // computed sprite row
reg [2:0]  r_px_sel;      // pixel select (with hflip)
reg [3:0]  r_nibble;      // extracted nibble
reg [8:0]  r_screen_x;    // screen X for current pixel

always @(*) begin
    // Compute row within sprite (for R_SCAN state)
    r_row_calc = r_scanline[2:0] - sat_y[r_spr_idx][2:0];
    if (sat_vf[r_spr_idx])
        r_row_calc = 3'd7 - r_row_calc;

    // Pixel extraction (for R_PIXELS state)
    r_px_sel = r_spr_hflip ? (3'd7 - r_px_count) : r_px_count;
    case (r_px_sel)
        3'd0: r_nibble = r_chr_data[3:0];
        3'd1: r_nibble = r_chr_data[7:4];
        3'd2: r_nibble = r_chr_data[11:8];
        3'd3: r_nibble = r_chr_data[15:12];
        3'd4: r_nibble = r_chr_data[19:16];
        3'd5: r_nibble = r_chr_data[23:20];
        3'd6: r_nibble = r_chr_data[27:24];
        3'd7: r_nibble = r_chr_data[31:28];
    endcase
    r_screen_x = r_spr_x + {6'd0, r_px_count};
end

always @(posedge clk_cpu or negedge reset_n) begin
    if (!reset_n) begin
        r_state     <= R_IDLE;
        lb_wr       <= 1'b0;
        lb_draw_sel <= 1'b0;
        lb_disp_sel <= 1'b1;
        r_chr_ready <= 1'b0;
    end else begin
        lb_wr <= 1'b0;  // default: no write

        case (r_state)
        R_IDLE: begin
            if (vblank_rise) begin
                // Reset buffer select at start of frame
                lb_draw_sel <= 1'b0;
                lb_disp_sel <= 1'b1;
            end

            if (hblank_rise && en_s2) begin
                // Start rendering for the upcoming scanline
                // vis_y_s2 is the line that just ended; next line = vis_y_s2 + 1
                // But during vblank, we skip rendering
                if (!vblank_sync[2] && vis_y_s2 < 9'd240) begin
                    r_scanline <= vis_y_s2;
                    r_state    <= R_CLEAR;
                    r_clear_x  <= 9'd0;
                end
            end
        end

        R_CLEAR: begin
            // Clear draw line buffer (320 pixels)
            lb_wr    <= 1'b1;
            lb_waddr <= {lb_draw_sel, r_clear_x};
            lb_wdata <= 8'd0;
            r_clear_x <= r_clear_x + 9'd1;
            if (r_clear_x == 9'd319) begin
                r_state   <= R_SCAN;
                r_spr_idx <= 6'd63;  // start from lowest priority
            end
        end

        R_SCAN: begin
            // Check if sprite r_spr_idx intersects r_scanline
            if (sat_en[r_spr_idx]) begin
                // Y intersection check (8x8 sprite)
                if (r_scanline >= sat_y[r_spr_idx] &&
                    r_scanline < sat_y[r_spr_idx] + 9'd8) begin
                    // Sprite intersects! Fetch chr data
                    r_spr_x     <= sat_x[r_spr_idx];
                    r_spr_pal   <= sat_pal[r_spr_idx];
                    r_spr_hflip <= sat_hf[r_spr_idx];

                    // Compute row within sprite (uses combinatorial r_row_calc)
                    chr_rd_addr_r <= {sat_tile[r_spr_idx], r_row_calc};
                    r_chr_ready <= 1'b0;
                    r_state     <= R_CHR_RD;
                end else begin
                    // No intersection, next sprite
                    if (r_spr_idx == 6'd0)
                        r_state <= R_DONE;
                    else
                        r_spr_idx <= r_spr_idx - 6'd1;
                end
            end else begin
                // Sprite disabled, next
                if (r_spr_idx == 6'd0)
                    r_state <= R_DONE;
                else
                    r_spr_idx <= r_spr_idx - 6'd1;
            end
        end

        R_CHR_RD: begin
            // Wait 1 cycle for BRAM read latency
            if (!r_chr_ready) begin
                r_chr_ready <= 1'b1;
            end else begin
                r_chr_data <= chr_rd_data_r;
                r_px_count <= 3'd0;
                r_state    <= R_PIXELS;
            end
        end

        R_PIXELS: begin
            // Write pixel to line buffer (uses combinatorial r_nibble/r_screen_x)
            if (r_nibble != 4'd0 && r_screen_x < 9'd320) begin
                lb_wr    <= 1'b1;
                lb_waddr <= {lb_draw_sel, r_screen_x};
                lb_wdata <= {r_spr_pal, r_nibble};
            end

            if (r_px_count == 3'd7) begin
                // Done with this sprite, move to next
                if (r_spr_idx == 6'd0)
                    r_state <= R_DONE;
                else begin
                    r_spr_idx <= r_spr_idx - 6'd1;
                    r_state   <= R_SCAN;
                end
            end else begin
                r_px_count <= r_px_count + 3'd1;
            end
        end

        R_DONE: begin
            // Swap line buffers
            lb_draw_sel <= ~lb_draw_sel;
            lb_disp_sel <= ~lb_disp_sel;
            r_state     <= R_IDLE;
        end

        default: r_state <= R_IDLE;
        endcase
    end
end

// ============================================
// Video readout (clk_video domain)
// ============================================
// CDC the display buffer select
reg disp_sel_s1, disp_sel_s2;
always @(posedge clk_video) begin
    disp_sel_s1 <= lb_disp_sel;
    disp_sel_s2 <= disp_sel_s1;
end

// CDC sprite enable
reg spr_en_vs1, spr_en_vs2;
always @(posedge clk_video) begin
    spr_en_vs1 <= sprite_enable;
    spr_en_vs2 <= spr_en_vs1;
end

// Read from display line buffer
always @(posedge clk_video) begin
    if (active_video && spr_en_vs2)
        lb_raddr <= {disp_sel_s2, visible_x[9:1]};
    else
        lb_raddr <= 10'd0;
end

// Palette lookup (combinatorial from ALM-based palette)
assign sprite_rgb    = (lb_rdata != 8'd0) ? palette[lb_rdata] : 24'd0;
assign sprite_opaque = (lb_rdata != 8'd0) && active_video && spr_en_vs2;

endmodule
