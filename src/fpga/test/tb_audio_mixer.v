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
 *   4. voice_end_irq             — one-shot voices raise voice_end_pending
 *   5. explicit_lr_targets       — hard-left/hard-right targets reach output
 */

`timescale 1ns/1ps
`default_nettype none

module tb_audio_mixer (
    input  wire        clk,
    input  wire        reset_n,

    /* MMIO write port — same shape as audio_mixer's expected inputs */
    input  wire        voice_wr,
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
    output wire [31:0] voice_active_mask
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
    reg [31:0] sdram_mem [0:1023] /*verilator public_flat_rw*/;

    reg        rd_pending;
    reg [31:0] rd_data;
    reg [9:0]  rd_addr;
    reg [7:0]  rd_remaining;
    assign m_arready = !rd_pending;
    assign m_rvalid  = rd_pending;
    assign m_rdata   = rd_data;
    assign m_rresp   = 2'b00;
    assign m_rlast   = (rd_remaining == 8'd0);

    integer si;
    initial begin
        for (si = 0; si < 1024; si = si + 1) sdram_mem[si] = 32'd0;
    end

    always @(posedge clk) begin
        if (!reset_n) begin
            rd_pending <= 1'b0;
            rd_data    <= 32'd0;
        end else begin
            if (m_arvalid && m_arready) begin
                /* Word-aligned address; treat lower bits as a 1 KB ring */
                rd_addr      <= m_araddr[11:2];
                rd_data      <= sdram_mem[m_araddr[11:2]];
                rd_remaining <= m_arlen;
                rd_pending   <= 1'b1;
            end else if (m_rvalid && m_rready) begin
                if (rd_remaining != 8'd0) begin
                    rd_addr      <= rd_addr + 10'd1;
                    rd_data      <= sdram_mem[rd_addr + 10'd1];
                    rd_remaining <= rd_remaining - 8'd1;
                end else begin
                    rd_pending <= 1'b0;
                end
            end
        end
    end

    /* ---------- DUT ---------- */
    audio_mixer dut (
        .clk                (clk),
        .reset_n            (reset_n),
        .mixer_enable       (1'b1),
        .voice_wr           (voice_wr),
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
        .m_arvalid          (m_arvalid),
        .m_arready          (m_arready),
        .m_araddr           (m_araddr),
        .m_arlen            (m_arlen),
        .m_rvalid           (m_rvalid),
        .m_rdata            (m_rdata),
        .m_rresp            (m_rresp),
        .m_rlast            (m_rlast),
        .m_rready           (m_rready),
        .sample_wr          (sample_wr),
        .sample_data        (sample_data),
        .fifo_level         (10'd0),       /* always have headroom */
        .active_count       (),
        .pos_readback       (pos_readback),
        .irq_clear_wr       (irq_clear_wr),
        .irq_clear          (irq_clear),
        .voice_end_pending  (voice_end_pending),
        .voice_end_irq      (),
        .last_sample_data   (),
        .sample_count       (),
        .voice_active_mask  (voice_active_mask)
    );

endmodule

`default_nettype wire
