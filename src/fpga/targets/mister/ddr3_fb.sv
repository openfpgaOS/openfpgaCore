//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------
//
// MiSTer direct-framebuffer pipeline (target-local; no shared-RTL changes).
//
// Copies the currently DISPLAYED SDRAM framebuffer into a 2-slot ring in
// HPS DDR3 once per output frame, then hands the completed slot to the
// framework scaler via the MISTER_FB interface (FB_BASE is latched by
// ascal at output-VS falling edge, so slot flips are tear-free by
// construction).  The 256-entry hardware palette is mirrored from the
// periph-slave write stream and forwarded to ascal's core palette
// (pal2) during vertical blanking.
//
// Why copy-from-SDRAM instead of a BRAM render target: the OS triple-
// buffers at three fixed SDRAM addresses and the GPU renders to
// CPU-computed byte addresses across all three, so a single BRAM cannot
// shadow the render target without deep GPU/firmware surgery.  Snapshotting
// the *displayed* buffer (stable for >= 1 frame by the triple-buffer
// contract, copy takes <1 ms) is correct by construction and still
// delivers the wins: native-resolution single-pass ascal scaling
// (instead of scanout-DDA-to-640x480 then ascal rescale), tear-free
// mailbox pacing, and the legacy VGA path kept alive as fallback.
//
// Mode support: 8bpp indexed (palette), RGB565, RGB555 — gated by
// fb_supported below; anything else (2/4bpp, RGBA5551 whose alpha sits
// in bit0, not ascal's 1555 layout) drops FB_EN so the framework falls
// back to the always-running VGA/scaler-input path.  Stride must be a
// multiple of 8 bytes so lines land 64-bit-aligned in DDR3.
//
// Clocking: everything here runs on clk (= clk_ram_controller = clk_cpu,
// 100 MHz).  DDRAM_CLK and FB_PAL_CLK are that same clock.  crt_vs
// (clk_vid) and FB_VBL (clk_vid) are 2FF-synchronized in.
//
// DDR3 map: slots at SLOT0/SLOT1 (default 0x2200_0000/0x2280_0000),
// above the framework ascal triple buffer which ends at 0x217F_FFFF.
//

`default_nettype none

// ---------------------------------------------------------------------------
// Two-client arbiter for the io_sdram video burst port.
//
// Client V (video scanout): fire-and-forget 1-cycle burst_rd pulse with
// addr/len valid on the same edge (the scanout FSM holds them, but we
// latch anyway and replay after any in-flight DMA burst).  Priority.
// Client D (frame DMA): req/gnt handshake; granted only when idle and no
// video request is pending.  Addr/len must be held by the DMA until done.
//
// The io_sdram controller samples burst_addr/len when it DEQUEUES the
// request (it has a 1-deep internal queue), so the controller-facing
// addr/len come from arbiter-owned registers held stable from pulse to
// burst_data_done.  Exactly one burst is in flight at a time.
// ---------------------------------------------------------------------------
module video_burst_arb (
    input  wire        clk,
    input  wire        reset_n,

    // client V — video scanout (priority)
    input  wire        vid_rd,
    input  wire [24:0] vid_addr,
    input  wire [10:0] vid_len,
    input  wire        vid_32bit,
    output wire        vid_data_valid,
    output wire        vid_data_done,

    // client D — frame DMA
    input  wire        dma_req,
    input  wire [24:0] dma_addr,
    input  wire [10:0] dma_len,
    output reg         dma_gnt,
    output wire        dma_data_valid,
    output wire        dma_data_done,

    // controller side (io_sdram burst port)
    output reg         burst_rd,
    output reg  [24:0] burst_addr,
    output reg  [10:0] burst_len,
    output reg         burst_32bit,
    input  wire        burst_data_valid,
    input  wire        burst_data_done
);

localparam OWN_IDLE = 2'd0;
localparam OWN_VID  = 2'd1;
localparam OWN_DMA  = 2'd2;

reg [1:0]  owner;
reg        vid_pend;
reg [24:0] vid_addr_l;
reg [10:0] vid_len_l;
reg        vid_32bit_l;

assign vid_data_valid = burst_data_valid && (owner == OWN_VID);
assign vid_data_done  = burst_data_done  && (owner == OWN_VID);
assign dma_data_valid = burst_data_valid && (owner == OWN_DMA);
assign dma_data_done  = burst_data_done  && (owner == OWN_DMA);

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        owner       <= OWN_IDLE;
        vid_pend    <= 1'b0;
        vid_addr_l  <= 25'd0;
        vid_len_l   <= 11'd0;
        vid_32bit_l <= 1'b0;
        burst_rd    <= 1'b0;
        burst_addr  <= 25'd0;
        burst_len   <= 11'd0;
        burst_32bit <= 1'b1;
        dma_gnt     <= 1'b0;
    end else begin
        burst_rd <= 1'b0;
        dma_gnt  <= 1'b0;

        // Latch every video pulse (scanout never re-pulses until its
        // burst completes, so one pending slot is sufficient).
        if (vid_rd) begin
            vid_pend    <= 1'b1;
            vid_addr_l  <= vid_addr;
            vid_len_l   <= vid_len;
            vid_32bit_l <= vid_32bit;
        end

        case (owner)
        OWN_IDLE: begin
            if (vid_pend || vid_rd) begin
                burst_rd    <= 1'b1;
                burst_addr  <= vid_rd ? vid_addr  : vid_addr_l;
                burst_len   <= vid_rd ? vid_len   : vid_len_l;
                burst_32bit <= vid_rd ? vid_32bit : vid_32bit_l;
                vid_pend    <= 1'b0;
                owner       <= OWN_VID;
            end else if (dma_req) begin
                burst_rd    <= 1'b1;
                burst_addr  <= dma_addr;
                burst_len   <= dma_len;
                burst_32bit <= 1'b1;
                dma_gnt     <= 1'b1;
                owner       <= OWN_DMA;
            end
        end
        default: begin
            if (burst_data_done)
                owner <= OWN_IDLE;
        end
        endcase
    end
end

endmodule

// ---------------------------------------------------------------------------
// Frame DMA + framework-FB control + palette forwarder.
// ---------------------------------------------------------------------------
module ddr3_fb #(
    parameter [31:0] SLOT0_BASE = 32'h2200_0000,  // byte addresses, 8-aligned
    parameter [31:0] SLOT1_BASE = 32'h2280_0000
) (
    input  wire        clk,       // clk_ram_controller (= clk_cpu, 100 MHz)
    input  wire        reset_n,

    input  wire        enable,    // OSD toggle (quasi-static, sync'd here)

    // frame trigger (clk_vid domain)
    input  wire        clk_vid,
    input  wire        crt_vs,

    // framebuffer state snoops (clk domain — periph-slave outputs)
    input  wire [24:0] fb_display_addr,  // SDRAM halfword address
    input  wire [2:0]  color_mode,
    input  wire [9:0]  fb_width,
    input  wire [9:0]  fb_height,
    input  wire [15:0] fb_stride,        // byte stride

    // SDRAM read through video_burst_arb client D
    output reg         dma_req,
    input  wire        dma_gnt,
    output reg  [24:0] dma_addr,
    output reg  [10:0] dma_len,
    input  wire [31:0] dma_data,
    input  wire        dma_data_valid,
    input  wire        dma_data_done,

    // HPS DDR3 (Avalon burst, 64-bit; addr = byte >> 3)
    output wire        DDRAM_CLK,
    input  wire        DDRAM_BUSY,
    output reg  [7:0]  DDRAM_BURSTCNT,
    output reg  [28:0] DDRAM_ADDR,
    input  wire [63:0] DDRAM_DOUT,
    input  wire        DDRAM_DOUT_READY,
    output wire        DDRAM_RD,
    output reg  [63:0] DDRAM_DIN,
    output wire [7:0]  DDRAM_BE,
    output reg         DDRAM_WE,

    // framework FB control (sys_top registers these on clk_sys)
    output reg         FB_EN,
    output reg  [4:0]  FB_FORMAT,
    output reg  [11:0] FB_WIDTH,
    output reg  [11:0] FB_HEIGHT,
    output reg  [31:0] FB_BASE,
    output reg  [13:0] FB_STRIDE,
    input  wire        FB_VBL,    // hdmi vblank, clk_vid domain
    output wire        FB_FORCE_BLANK,

    // palette snoop (clk domain, periph-slave write stream)
    input  wire        pal_wr,
    input  wire [7:0]  pal_addr,
    input  wire [23:0] pal_data,
    input  wire        pal_commit,

    // palette out to ascal pal2 (synchronous write port, our clock)
    output wire        FB_PAL_CLK,
    output reg  [7:0]  FB_PAL_ADDR,
    output reg  [23:0] FB_PAL_DOUT,
    output reg         FB_PAL_WR
);

assign DDRAM_CLK      = clk;
assign DDRAM_RD       = 1'b0;
assign DDRAM_BE       = 8'hFF;
assign FB_FORCE_BLANK = 1'b0;
assign FB_PAL_CLK     = clk;

// ---- mode gate ----------------------------------------------------------
// color_mode encoding (video_CRT_scanout / SYS_COLOR_MODE): 0=8bpp indexed,
// 1=4bpp, 2=2bpp, 3=RGB565, 4=RGB555, 5=RGBA5551.
// FB_FORMAT: [2:0] 011=8bpp(palette) 100=16bpp; [3] 0=565 1=1555.
// RGBA5551 is rejected: its alpha lives in bit0, not ascal's 1555 bit15.
localparam [2:0] MODE_8BIT   = 3'd0;
localparam [2:0] MODE_RGB565 = 3'd3;
localparam [2:0] MODE_RGB555 = 3'd4;

wire mode_ok = (color_mode == MODE_8BIT)
            || (color_mode == MODE_RGB565)
            || (color_mode == MODE_RGB555);
wire fb_supported = mode_ok && (fb_stride[2:0] == 3'b000) && (fb_stride != 16'd0)
                 && (fb_width != 10'd0) && (fb_height != 10'd0);

function [4:0] fb_format_of;
    input [2:0] mode;
    begin
        case (mode)
            MODE_RGB565: fb_format_of = 5'b00100;  // 16bpp 565
            MODE_RGB555: fb_format_of = 5'b01100;  // 16bpp 1555 (x555)
            default:     fb_format_of = 5'b00011;  // 8bpp palette
        endcase
    end
endfunction

// ---- CDC: enable, vsync, vblank ----------------------------------------
reg [1:0] en_sync;
always @(posedge clk) en_sync <= {en_sync[0], enable};
wire en_q = en_sync[1];

reg [2:0] vs_sync;
always @(posedge clk) vs_sync <= {vs_sync[1:0], crt_vs};
wire vs_rising = vs_sync[1] && !vs_sync[2];

reg [2:0] vbl_sync;
always @(posedge clk) vbl_sync <= {vbl_sync[1:0], FB_VBL};
wire vbl_rising = vbl_sync[1] && !vbl_sync[2];

// ---- frame DMA ----------------------------------------------------------
// One copy per input frame: at vsync (+ a settle delay so the periph
// slave's swap-at-vsync has flipped fb_display_addr), snapshot the
// displayed buffer and geometry, copy stride*height bytes linearly into
// the inactive DDR3 slot, then publish base+geometry together.  The
// displayed buffer is stable for >= 1 frame (triple-buffer contract);
// the copy takes <1 ms.
localparam CHUNK_WORDS = 11'd64;       // 32-bit words per SDRAM burst (256 B)

localparam S_IDLE    = 3'd0;
localparam S_SETTLE  = 3'd1;
localparam S_LINE    = 3'd2;
localparam S_FETCH   = 3'd3;
localparam S_WRITE   = 3'd4;
localparam S_PUBLISH = 3'd5;

reg [2:0]  st;
reg [5:0]  settle_cnt;

// latched frame geometry
reg [24:0] src_half;        // SDRAM halfword addr of frame base
reg [2:0]  cp_mode;
reg [9:0]  cp_width, cp_height;
reg [15:0] cp_stride;

reg [24:0] line_half;       // SDRAM halfword addr of current line
reg [9:0]  lines_left;
reg [11:0] line_words_left; // 32-bit words left in current line
reg [28:0] ddr_word;        // DDR3 64-bit-word write pointer

reg        slot;            // slot being WRITTEN this frame
wire [31:0] slot_base = slot ? SLOT1_BASE : SLOT0_BASE;

// chunk buffer: up to 32 x 64-bit (one 256-byte SDRAM burst)
reg [63:0] cbuf [0:31];
reg [5:0]  cw_words;        // 32-bit words captured this chunk
reg [10:0] chunk_words;     // words requested this chunk
reg        chunk_active;    // burst issued, awaiting data_done
reg [31:0] lo_word;
reg [5:0]  wr_idx;          // 64-bit words sent to DDR3 this chunk

// stride/4 in 32-bit words.  FB_STRIDE is 14 bits, so stride < 16384 and
// the quotient fits 12 bits.
wire [11:0] stride_words = cp_stride[13:2];
// stride is a multiple of 8 (fb_supported), so chunk word counts are even
wire [7:0]  chunk_pairs  = chunk_words[8:1];           // 64-bit words/chunk

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        st          <= S_IDLE;
        settle_cnt  <= 6'd0;
        dma_req     <= 1'b0;
        dma_addr    <= 25'd0;
        dma_len     <= 11'd0;
        DDRAM_WE    <= 1'b0;
        DDRAM_ADDR  <= 29'd0;
        DDRAM_BURSTCNT <= 8'd0;
        DDRAM_DIN   <= 64'd0;
        FB_EN       <= 1'b0;
        FB_FORMAT   <= 5'd0;
        FB_WIDTH    <= 12'd0;
        FB_HEIGHT   <= 12'd0;
        FB_BASE     <= SLOT0_BASE;
        FB_STRIDE   <= 14'd0;
        slot        <= 1'b0;
        src_half    <= 25'd0;
        cp_mode     <= 3'd0;
        cp_width    <= 10'd0;
        cp_height   <= 10'd0;
        cp_stride   <= 16'd0;
        line_half   <= 25'd0;
        lines_left  <= 10'd0;
        line_words_left <= 12'd0;
        ddr_word    <= 29'd0;
        cw_words    <= 6'd0;
        chunk_words <= 11'd0;
        chunk_active <= 1'b0;
        lo_word     <= 32'd0;
        wr_idx      <= 6'd0;
    end else begin
        case (st)
        S_IDLE: begin
            DDRAM_WE <= 1'b0;
            if (!en_q || !fb_supported)
                FB_EN <= 1'b0;               // immediate fallback to VGA path
            if (vs_rising && en_q && fb_supported) begin
                settle_cnt <= 6'd32;         // let the slave's swap settle
                st <= S_SETTLE;
            end
        end

        S_SETTLE: begin
            settle_cnt <= settle_cnt - 6'd1;
            if (settle_cnt == 6'd1) begin
                src_half   <= fb_display_addr;
                cp_mode    <= color_mode;
                cp_width   <= fb_width;
                cp_height  <= fb_height;
                cp_stride  <= fb_stride;
                line_half  <= fb_display_addr;
                lines_left <= fb_height;
                ddr_word   <= slot ? SLOT1_BASE[31:3] : SLOT0_BASE[31:3];
                st <= S_LINE;
            end
        end

        S_LINE: begin
            if (lines_left == 10'd0) begin
                st <= S_PUBLISH;
            end else begin
                line_words_left <= stride_words;
                cw_words <= 6'd0;
                chunk_active <= 1'b0;
                st <= S_FETCH;
            end
        end

        S_FETCH: begin
            if (!chunk_active && !dma_req) begin
                // start a chunk (line word counts are even; chunks too)
                chunk_words <= (line_words_left > {2'b00, CHUNK_WORDS[9:0]})
                               ? CHUNK_WORDS : line_words_left[10:0];
                dma_addr <= line_half;
                dma_len  <= (line_words_left > {2'b00, CHUNK_WORDS[9:0]})
                            ? CHUNK_WORDS : line_words_left[10:0];
                dma_req  <= 1'b1;
                chunk_active <= 1'b1;
            end
            if (dma_gnt)
                dma_req <= 1'b0;
            if (dma_data_valid) begin
                if (!cw_words[0])
                    lo_word <= dma_data;
                else
                    cbuf[cw_words[5:1]] <= {dma_data, lo_word};
                cw_words <= cw_words + 6'd1;
            end
            if (dma_data_done) begin
                wr_idx <= 6'd0;
                st <= S_WRITE;
            end
        end

        S_WRITE: begin
            // Avalon burst write: address+burstcount held for the whole
            // burst, one data word accepted per !BUSY cycle.
            if (!DDRAM_WE) begin
                DDRAM_ADDR     <= ddr_word;
                DDRAM_BURSTCNT <= chunk_pairs;
                DDRAM_DIN      <= cbuf[0];
                DDRAM_WE       <= 1'b1;
                wr_idx         <= 6'd1;
            end else if (!DDRAM_BUSY) begin
                if ({1'b0, wr_idx} == {1'b0, chunk_pairs[5:0]}) begin
                    // last 64-bit word of the chunk was accepted this cycle
                    DDRAM_WE <= 1'b0;
                    ddr_word <= ddr_word + {21'd0, chunk_pairs};
                    // chunk covers stride bytes contiguously: advancing by
                    // chunk_words halfwords walks straight into the next
                    // line when the current one completes (stride copy).
                    line_half <= line_half + {14'd0, chunk_words[9:0], 1'b0};
                    line_words_left <= line_words_left - {1'b0, chunk_words};
                    cw_words <= 6'd0;
                    chunk_active <= 1'b0;
                    if (line_words_left == {1'b0, chunk_words}) begin
                        lines_left <= lines_left - 10'd1;
                        st <= S_LINE;
                    end else begin
                        st <= S_FETCH;
                    end
                end else begin
                    DDRAM_DIN <= cbuf[wr_idx[4:0]];
                    wr_idx    <= wr_idx + 6'd1;
                end
            end
        end

        S_PUBLISH: begin
            FB_BASE   <= slot_base;
            FB_FORMAT <= fb_format_of(cp_mode);
            FB_WIDTH  <= {2'b00, cp_width};
            FB_HEIGHT <= {2'b00, cp_height};
            FB_STRIDE <= cp_stride[13:0];
            FB_EN     <= en_q;   // not 1'b1: avoids a 1-cycle enable glitch
                                 // when the OSD toggle flips mid-copy
            slot      <= ~slot;
            st        <= S_IDLE;
        end

        default: st <= S_IDLE;
        endcase
    end
end

// ---- palette mirror + forwarder ----------------------------------------
// The periph slave streams committed-palette writes (pal_wr with resolved
// address) into the scanout's staging bank; we mirror the same stream and,
// on commit, forward all 256 entries to ascal's pal2 during the next
// output vertical blanking (ascal reads pal2 during active video; vblank
// at 1080p60 is ~660 us, the stream takes 2.6 us).
reg [23:0] pal_mem [0:255];
reg [23:0] pal_rd_q;
reg        pal_pending;
reg        pal_streaming;
reg [8:0]  pal_cnt;         // 0..256
reg [7:0]  pal_addr_d;

always @(posedge clk) begin
    if (pal_wr)
        pal_mem[pal_addr] <= pal_data;
    pal_rd_q <= pal_mem[pal_cnt[7:0]];
end

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        pal_pending   <= 1'b0;
        pal_streaming <= 1'b0;
        pal_cnt       <= 9'd0;
        pal_addr_d    <= 8'd0;
        FB_PAL_ADDR   <= 8'd0;
        FB_PAL_DOUT   <= 24'd0;
        FB_PAL_WR     <= 1'b0;
    end else begin
        FB_PAL_WR <= 1'b0;
        if (pal_commit)
            pal_pending <= 1'b1;

        if (!pal_streaming) begin
            if (pal_pending && vbl_rising) begin
                pal_streaming <= 1'b1;
                pal_pending   <= 1'b0;
                pal_cnt       <= 9'd0;
            end
        end else begin
            // pal_rd_q holds entry (pal_cnt-1) this cycle
            pal_addr_d <= pal_cnt[7:0];
            pal_cnt    <= pal_cnt + 9'd1;
            if (pal_cnt != 9'd0) begin
                FB_PAL_ADDR <= pal_addr_d;
                FB_PAL_DOUT <= pal_rd_q;
                FB_PAL_WR   <= 1'b1;
            end
            if (pal_cnt == 9'd256)
                pal_streaming <= 1'b0;
        end
    end
end

endmodule

`default_nettype wire
