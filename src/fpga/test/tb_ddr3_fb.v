//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------
//
// TB for the MiSTer direct-framebuffer pipeline (ddr3_fb + video_burst_arb).
//
// Models:
//   * SDRAM burst port (io_sdram side of the arbiter): fixed-latency
//     responder returning a deterministic pattern word per byte address.
//   * DDR3 Avalon 64-bit burst-write slave with pseudo-random BUSY,
//     capturing writes into a window buffer (probe-readable from C++).
//   * A fake scanout client that fires bursts through the arbiter's
//     priority port and self-checks the returned pattern (contention).
//   * Palette capture of the FB_PAL_* stream.
//
// Scenario control + all assertions live in tb_ddr3_fb_main.cpp.
//

`default_nettype none

module tb_ddr3_fb (
    input  wire        clk,
    input  wire        reset_n,

    // scenario controls
    input  wire        enable,
    input  wire        crt_vs,
    input  wire        fb_vbl,
    input  wire [24:0] fb_display_addr,
    input  wire [2:0]  color_mode,
    input  wire [9:0]  fb_width,
    input  wire [9:0]  fb_height,
    input  wire [15:0] fb_stride,

    // palette snoop stimulus
    input  wire        pal_wr,
    input  wire [7:0]  pal_addr,
    input  wire [23:0] pal_data,
    input  wire        pal_commit,

    // fake scanout client stimulus / results
    input  wire        vid_kick,
    input  wire [24:0] vid_kick_addr,
    input  wire [10:0] vid_kick_len,
    output reg  [15:0] vid_done_cnt,
    output reg  [15:0] vid_err_cnt,

    // framework FB observation
    output wire        FB_EN,
    output wire [4:0]  FB_FORMAT,
    output wire [11:0] FB_WIDTH,
    output wire [11:0] FB_HEIGHT,
    output wire [31:0] FB_BASE,
    output wire [13:0] FB_STRIDE,

    // DDR3 capture probe (64-bit-word offset within the 16 MB window at
    // 0x2200_0000)
    input  wire [20:0] ddr_probe_idx,
    output wire [63:0] ddr_probe_data,
    output reg  [31:0] ddr_write_cnt,
    output reg  [15:0] ddr_proto_err,   // Avalon protocol violations

    // palette capture probe
    input  wire [7:0]  pal_probe_idx,
    output wire [23:0] pal_probe_data,
    output reg  [15:0] pal_wr_cnt
);

// ---------------------------------------------------------------------------
// deterministic SDRAM contents: 32-bit word at byte address b (b%4==0)
// ---------------------------------------------------------------------------
function [31:0] sdram_word;
    input [31:0] byte_addr;
    begin
        sdram_word = (byte_addr[31:2] * 32'h9E37_79B9) ^ 32'hC0FF_EE00;
    end
endfunction

// ---------------------------------------------------------------------------
// DUT wiring
// ---------------------------------------------------------------------------
wire        vid_rd;
wire [24:0] vid_addr;
wire [10:0] vid_len;
wire        vid_data_valid;
wire        vid_data_done;

wire        dma_req, dma_gnt;
wire [24:0] dma_addr;
wire [10:0] dma_len;
wire        dma_data_valid, dma_data_done;

wire        burst_rd;
wire [24:0] burst_addr;
wire [10:0] burst_len;
wire        burst_32bit;
reg  [31:0] burst_data;
reg         burst_data_valid;
reg         burst_data_done;

video_burst_arb arb (
    .clk(clk), .reset_n(reset_n),
    .vid_rd(vid_rd), .vid_addr(vid_addr), .vid_len(vid_len), .vid_32bit(1'b1),
    .vid_data_valid(vid_data_valid), .vid_data_done(vid_data_done),
    .dma_req(dma_req), .dma_addr(dma_addr), .dma_len(dma_len), .dma_gnt(dma_gnt),
    .dma_data_valid(dma_data_valid), .dma_data_done(dma_data_done),
    .burst_rd(burst_rd), .burst_addr(burst_addr), .burst_len(burst_len),
    .burst_32bit(burst_32bit),
    .burst_data_valid(burst_data_valid), .burst_data_done(burst_data_done)
);

wire        DDRAM_CLK;
wire        DDRAM_RD;
wire [7:0]  DDRAM_BURSTCNT;
wire [28:0] DDRAM_ADDR;
wire [63:0] DDRAM_DIN;
wire [7:0]  DDRAM_BE;
wire        DDRAM_WE;
reg         DDRAM_BUSY;

wire        FB_PAL_CLK;
wire [7:0]  FB_PAL_ADDR;
wire [23:0] FB_PAL_DOUT;
wire        FB_PAL_WR;

ddr3_fb dut (
    .clk(clk), .reset_n(reset_n),
    .enable(enable),
    .clk_vid(clk), .crt_vs(crt_vs),
    .fb_display_addr(fb_display_addr),
    .color_mode(color_mode),
    .fb_width(fb_width), .fb_height(fb_height), .fb_stride(fb_stride),
    .dma_req(dma_req), .dma_gnt(dma_gnt), .dma_addr(dma_addr), .dma_len(dma_len),
    .dma_data(burst_data), .dma_data_valid(dma_data_valid), .dma_data_done(dma_data_done),
    .DDRAM_CLK(DDRAM_CLK), .DDRAM_BUSY(DDRAM_BUSY), .DDRAM_BURSTCNT(DDRAM_BURSTCNT),
    .DDRAM_ADDR(DDRAM_ADDR), .DDRAM_DOUT(64'd0), .DDRAM_DOUT_READY(1'b0),
    .DDRAM_RD(DDRAM_RD), .DDRAM_DIN(DDRAM_DIN), .DDRAM_BE(DDRAM_BE), .DDRAM_WE(DDRAM_WE),
    .FB_EN(FB_EN), .FB_FORMAT(FB_FORMAT), .FB_WIDTH(FB_WIDTH), .FB_HEIGHT(FB_HEIGHT),
    .FB_BASE(FB_BASE), .FB_STRIDE(FB_STRIDE), .FB_VBL(fb_vbl), .FB_FORCE_BLANK(),
    .pal_wr(pal_wr), .pal_addr(pal_addr), .pal_data(pal_data), .pal_commit(pal_commit),
    .FB_PAL_CLK(FB_PAL_CLK), .FB_PAL_ADDR(FB_PAL_ADDR), .FB_PAL_DOUT(FB_PAL_DOUT),
    .FB_PAL_WR(FB_PAL_WR)
);

// ---------------------------------------------------------------------------
// SDRAM burst-port model: latency, then burst_len back-to-back words.
// burst_addr is a halfword address; word i covers byte {addr<<1} + 4*i.
// ---------------------------------------------------------------------------
reg [10:0] sm_left;
reg [31:0] sm_byte;
reg [3:0]  sm_lat;
reg        sm_active;

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        sm_left  <= 11'd0;
        sm_byte  <= 32'd0;
        sm_lat   <= 4'd0;
        sm_active <= 1'b0;
        burst_data <= 32'd0;
        burst_data_valid <= 1'b0;
        burst_data_done  <= 1'b0;
    end else begin
        burst_data_valid <= 1'b0;
        burst_data_done  <= 1'b0;
        if (burst_rd && !sm_active) begin
            sm_active <= 1'b1;
            sm_left <= burst_len;
            sm_byte <= {6'd0, burst_addr, 1'b0};
            sm_lat  <= 4'd8;
        end else if (sm_active) begin
            if (sm_lat != 4'd0) begin
                sm_lat <= sm_lat - 4'd1;
            end else if (sm_left != 11'd0) begin
                burst_data <= sdram_word(sm_byte);
                burst_data_valid <= 1'b1;
                sm_byte <= sm_byte + 32'd4;
                sm_left <= sm_left - 11'd1;
                if (sm_left == 11'd1) begin
                    burst_data_done <= 1'b1;
                    sm_active <= 1'b0;
                end
            end
        end
    end
end

// ---------------------------------------------------------------------------
// DDR3 Avalon slave model: pseudo-random BUSY, capture window at
// 0x2200_0000 (word64 base 29'h0440_0000), 16 MB = 2^21 64-bit words.
// Protocol checks: ADDR/BURSTCNT stable during a burst, word count == BURSTCNT.
// ---------------------------------------------------------------------------
localparam [28:0] WIN_BASE = 29'h0440_0000;

reg [63:0] ddr_mem [0:(1<<21)-1];
reg [15:0] lfsr;
reg [8:0]  av_left;      // words remaining in current burst (0 = idle)
reg [28:0] av_addr;
reg [28:0] av_start;
reg [7:0]  av_cnt;

assign ddr_probe_data = ddr_mem[ddr_probe_idx];

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        lfsr <= 16'hACE1;
        av_left <= 9'd0;
        av_addr <= 29'd0;
        av_start <= 29'd0;
        av_cnt  <= 8'd0;
        ddr_write_cnt <= 32'd0;
        ddr_proto_err <= 16'd0;
        DDRAM_BUSY <= 1'b0;
    end else begin
        lfsr <= {lfsr[14:0], lfsr[15] ^ lfsr[13] ^ lfsr[12] ^ lfsr[10]};
        DDRAM_BUSY <= lfsr[0] & lfsr[3];   // ~25% busy

        if (DDRAM_WE && !DDRAM_BUSY) begin
            if (av_left == 9'd0) begin
                // burst start
                av_start <= DDRAM_ADDR;
                av_cnt   <= DDRAM_BURSTCNT;
                av_addr  <= DDRAM_ADDR + 29'd1;
                av_left  <= {1'b0, DDRAM_BURSTCNT} - 9'd1;
                if (DDRAM_BURSTCNT == 8'd0) ddr_proto_err <= ddr_proto_err + 16'd1;
                if (DDRAM_ADDR >= WIN_BASE && (DDRAM_ADDR - WIN_BASE) < (1<<21))
                    ddr_mem[DDRAM_ADDR - WIN_BASE] <= DDRAM_DIN;
                else
                    ddr_proto_err <= ddr_proto_err + 16'd1;
            end else begin
                // burst continuation: address/burstcount must hold
                if (DDRAM_ADDR != av_start || DDRAM_BURSTCNT != av_cnt)
                    ddr_proto_err <= ddr_proto_err + 16'd1;
                if (av_addr >= WIN_BASE && (av_addr - WIN_BASE) < (1<<21))
                    ddr_mem[av_addr - WIN_BASE] <= DDRAM_DIN;
                av_addr <= av_addr + 29'd1;
                av_left <= av_left - 9'd1;
            end
            ddr_write_cnt <= ddr_write_cnt + 32'd1;
        end
        if (!DDRAM_WE && av_left != 9'd0)
            ddr_proto_err <= ddr_proto_err + 16'd1;  // WE dropped mid-burst
    end
end

// ---------------------------------------------------------------------------
// Fake scanout client: kick -> one burst through the arbiter's priority
// port; self-checks the returned pattern.
// ---------------------------------------------------------------------------
reg        vk_rd;
reg [24:0] vk_addr;
reg [10:0] vk_len;
reg [31:0] vk_byte;
reg        vk_active;

assign vid_rd   = vk_rd;
assign vid_addr = vk_addr;
assign vid_len  = vk_len;

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        vk_rd <= 1'b0;
        vk_addr <= 25'd0;
        vk_len <= 11'd0;
        vk_byte <= 32'd0;
        vk_active <= 1'b0;
        vid_done_cnt <= 16'd0;
        vid_err_cnt <= 16'd0;
    end else begin
        vk_rd <= 1'b0;
        if (vid_kick && !vk_active) begin
            vk_rd <= 1'b1;
            vk_addr <= vid_kick_addr;
            vk_len <= vid_kick_len;
            vk_byte <= {6'd0, vid_kick_addr, 1'b0};
            vk_active <= 1'b1;
        end
        if (vid_data_valid) begin
            if (burst_data != sdram_word(vk_byte))
                vid_err_cnt <= vid_err_cnt + 16'd1;
            vk_byte <= vk_byte + 32'd4;
        end
        if (vid_data_done) begin
            vk_active <= 1'b0;
            vid_done_cnt <= vid_done_cnt + 16'd1;
        end
    end
end

// ---------------------------------------------------------------------------
// Palette capture
// ---------------------------------------------------------------------------
reg [23:0] pal_cap [0:255];
assign pal_probe_data = pal_cap[pal_probe_idx];

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        pal_wr_cnt <= 16'd0;
    end else if (FB_PAL_WR) begin
        pal_cap[FB_PAL_ADDR] <= FB_PAL_DOUT;
        pal_wr_cnt <= pal_wr_cnt + 16'd1;
    end
end

endmodule

`default_nettype wire
