//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * Audio mixer Verilator testbench (audio_mixer.v v3).
 *
 * Wraps audio_mixer with a minimal SDRAM read responder so the FSM can
 * complete its tap fetches without hanging.  The C++ harness writes
 * voice/control/group/master MMIO via the same {voice_wr, voice_field,
 * voice_sel, voice_wdata} interface that axi_periph_slave drives in
 * the real design — flat MMIO addressing happens one level above (in
 * the slave) so the unit-under-test sees the same per-write inputs.
 *
 * Coverage (see tb_audio_mixer_main.cpp):
 *   1. flat_mmio_independence    — voice 0 + voice 31 writes don't cross-talk
 *   2. group_composition         — vol_target × group_vol × master_vol math
 *   3. master_mute               — master=0 silences the output stream
 *   3b. master_fade_tracks_settled_voice — a settled voice re-converges when
 *                                  master_vol / group_vol later change (guards
 *                                  any settled-ramp fast-path against a stuck
 *                                  volume during a master/group fade)
 *   4. voice_end_irq             — one-shot voices raise voice_end_pending
 *   5. explicit_lr_targets       — hard-left/hard-right targets reach output
 */

`timescale 1ns/1ps
`default_nettype none

module tb_audio_mixer (
    input  wire        clk,
    input wire mixer_enable,
    input wire [9:0] external_fifo_level,
    input wire stall_ar,
    output wire [31:0] address_probe,
    output wire ar_handshake,
    output wire [7:0] burst_probe,
    output reg [31:0] read_requests,
    output reg [31:0] read_beats,
    input  wire        reset_n,

    /* MMIO write port — same shape as audio_mixer's expected inputs */
    input  wire        voice_wr,
    output wire        voice_wr_ready,
    input  wire        stall_sdram,
    input  wire [15:0] sdram_latency,
    input  wire        mister_output_enable,
    input  wire        mister_clk_is90,
    output wire [15:0] mister_audio_l,
    output wire [15:0] mister_audio_r,
    output wire        read_pending,
    input  wire [3:0]  voice_field,
    input  wire [4:0]  voice_sel,
    input  wire [4:0]  voice_sel_rd,
    input  wire [31:0] voice_wdata,

    /* Group / master MMIO state */
    input  wire  [7:0] master_vol,
    input  wire  [7:0] group_vol_0,
    input  wire  [7:0] group_vol_1,
    input  wire  [7:0] group_vol_2,
    input  wire  [7:0] group_vol_3,
    input  wire [63:0] voice_group_packed,

    /* IRQ clear */
    input  wire        irq_clear_wr,
    input  wire [31:0] irq_clear,

    /* Outputs visible to the harness */
    output wire        sample_wr,
    output wire [31:0] sample_data,
    output wire [21:0] pos_readback,
    output wire [31:0] voice_end_pending,
    output wire [31:0] voice_active_mask,

    /* Highest SDRAM byte address the mixer has fetched (inclusive of
     * burst beats).  Lets the stream-mode tests assert that a starved
     * stream voice NEVER fetches past the published write pointer —
     * the literal stale-replay bug.  Cleared by reset. */
    output reg  [31:0] max_araddr_seen
);

    /* ---------- AXI4 read master <-> SDRAM stub ---------- */
    wire        m_arvalid;
    wire        m_arready;
    wire [31:0] m_araddr;
    wire [7:0]  m_arlen;
    wire        m_rvalid;
    wire [31:0] m_rdata;
    wire [1:0]  m_rresp;
    wire        m_rlast;
    wire        m_rready;

    /* Tiny SDRAM stub.  It supports the audio mixer's single-beat reads
     * and ARLEN=1 two-beat bursts so interpolation tests can run through
     * both mono even/odd sample positions. */
    /* verilator public_module */
    reg [31:0] sdram_mem [0:16383] /*verilator public_flat_rw*/;

    reg        rd_pending;
    reg [31:0] rd_data;
    reg [13:0] rd_addr;
    reg [7:0]  rd_remaining;
    reg [15:0] rd_delay;
    assign m_arready = !rd_pending && !stall_ar;
    assign address_probe=m_araddr;
    assign ar_handshake=m_arvalid && m_arready;
    assign burst_probe=m_arlen;
    assign m_rvalid  = rd_pending && !stall_sdram && (rd_delay == 0);
    assign read_pending = rd_pending;
    assign m_rdata   = rd_data;
    assign m_rresp   = 2'b00;
    assign m_rlast   = (rd_remaining == 8'd0);

    always @(posedge clk) begin
      if(!reset_n) begin read_requests<=0;read_beats<=0;end
      else begin
        if(m_arvalid && m_arready)read_requests<=read_requests+1;
        if(m_rvalid && m_rready)read_beats<=read_beats+1;
      end
    end
    integer si;
    initial begin
        for (si = 0; si < 16384; si = si + 1) sdram_mem[si] = 32'd0;
    end

    always @(posedge clk) begin
        if (!reset_n) begin
            max_araddr_seen <= 32'd0;
        end else if (source_arvalid && source_arready) begin
            if (source_araddr + {22'd0, source_arlen, 2'b00} > max_araddr_seen)
                max_araddr_seen <= source_araddr + {22'd0, source_arlen, 2'b00};
        end
    end

    always @(posedge clk) begin
        if (!reset_n) begin
            rd_pending <= 1'b0;
            rd_data    <= 32'd0;
            rd_delay   <= 16'd0;
        end else begin
            if (rd_delay != 0)
                rd_delay <= rd_delay - 16'd1;
            if (m_arvalid && m_arready) begin
                /* Word-aligned address; treat lower bits as a 1 KB ring */
                rd_addr      <= m_araddr[15:2];
                rd_data      <= sdram_mem[m_araddr[15:2]];
                rd_remaining <= m_arlen;
                rd_pending   <= 1'b1;
                rd_delay     <= sdram_latency;
            end else if (m_rvalid && m_rready) begin
                if (rd_remaining != 8'd0) begin
                    rd_addr      <= rd_addr + 14'd1;
                    rd_data      <= sdram_mem[rd_addr + 14'd1];
                    rd_remaining <= rd_remaining - 8'd1;
                end else begin
                    rd_pending <= 1'b0;
                end
            end
        end
    end

    wire [9:0] mister_fifo_level;
    mister_audio_output mister_output (
        .clk(clk), .reset_n(reset_n), .mixer_enable(mister_output_enable),
        .clk_is90(mister_clk_is90), .sample_wr(sample_wr), .sample_data(sample_data),
        .fifo_level(mister_fifo_level), .fifo_full(),
        .audio_l(mister_audio_l), .audio_r(mister_audio_r)
    );


    wire source_arvalid,source_arready,source_rvalid,source_rlast,source_rready;
    wire[31:0]source_araddr,source_rdata;
    wire[7:0]source_arlen;
    wire[1:0]source_rresp;
    wire[4:0]fetch_voice;
    wire fetch_cacheable;
    audio_sample_cache cache (
        .clk(clk),.reset_n(reset_n),.cache_enable(mixer_enable),
        .request_voice(fetch_voice),.request_cacheable(fetch_cacheable),
        .invalidate_valid(voice_wr),.invalidate_voice(voice_sel),
        .s_arvalid(source_arvalid),.s_arready(source_arready),.s_araddr(source_araddr),.s_arlen(source_arlen),
        .s_rvalid(source_rvalid),.s_rready(source_rready),.s_rdata(source_rdata),.s_rresp(source_rresp),.s_rlast(source_rlast),
        .m_arvalid(m_arvalid),.m_arready(m_arready),.m_araddr(m_araddr),.m_arlen(m_arlen),
        .m_rvalid(m_rvalid),.m_rready(m_rready),.m_rdata(m_rdata),.m_rresp(m_rresp),.m_rlast(m_rlast));
    /* ---------- DUT ---------- */
    audio_mixer dut (
        .clk                (clk),
        .reset_n            (reset_n),
        .mixer_enable       (mixer_enable),
        .voice_wr           (voice_wr),
        .voice_wr_ready     (voice_wr_ready),
        .voice_field        (voice_field),
        .voice_sel          (voice_sel),
        .voice_sel_rd       (voice_sel_rd),
        .voice_wdata        (voice_wdata),
        .master_vol         (master_vol),
        .group_vol_0        (group_vol_0),
        .group_vol_1        (group_vol_1),
        .group_vol_2        (group_vol_2),
        .group_vol_3        (group_vol_3),
        .voice_group_packed (voice_group_packed),
        .m_arvalid          (source_arvalid),
        .m_arready          (source_arready),
        .m_araddr           (source_araddr),
        .m_arlen            (source_arlen),
        .m_rvalid           (source_rvalid),
        .m_rdata            (source_rdata),
        .m_rresp            (source_rresp),
        .m_rlast            (source_rlast),
        .m_rready           (source_rready),
        .sample_wr          (sample_wr),
        .sample_data        (sample_data),
        .fifo_level         (mister_output_enable ? mister_fifo_level : external_fifo_level),
        .pos_readback       (pos_readback),
        .irq_clear_wr       (irq_clear_wr),
        .irq_clear          (irq_clear),
        .voice_end_pending  (voice_end_pending),
        .voice_end_irq      (),
        .voice_active_mask  (voice_active_mask),
        .fetch_voice(fetch_voice),.fetch_cacheable(fetch_cacheable)
    );

endmodule

`default_nettype wire
