/*
 * Audio pipeline Verilator testbench.
 *
 * Exercises audio_output.v: push stereo samples into the dcfifo on the
 * CPU clock, observe that the I2S serializer drains them on the audio
 * clock at 48 kHz.  Validates:
 *
 *   - fifo_level rises as samples are pushed
 *   - fifo_full asserts when the FIFO is full
 *   - stereo sample packing reaches the I2S staging registers unchanged
 *   - audio_mclk passthrough
 *   - audio_lrck toggles at half the SCLK rate, 48 kHz
 *   - audio_dac serialises 16-bit MSB-first stereo words
 *   - underrun hold + decay path does not lock up
 *
 * The dcfifo is modelled by test/dcfifo_stub.v (see that file for the
 * simplified behaviour).  The real Cyclone-V dcfifo has CDC synchroniser
 * pipelines we don't model — harmless for this functional test.
 */

`timescale 1ns/1ps
`default_nettype none

module tb_audio_pipeline(
    input  wire        clk_sys,
    input  wire        clk_audio,
    input  wire        reset_n,
    input  wire        sample_wr,
    input  wire [31:0] sample_data,
    output wire  [9:0] fifo_level,
    output wire        fifo_full,
    output wire        audio_mclk,
    output wire        audio_lrck,
    output wire        audio_dac
);

    /* DUT */
    audio_output dut (
        .clk_sys     (clk_sys),
        .clk_audio   (clk_audio),
        .reset_n     (reset_n),
        .sample_wr   (sample_wr),
        .sample_data (sample_data),
        .fifo_level  (fifo_level),
        .fifo_full   (fifo_full),
        .audio_mclk  (audio_mclk),
        .audio_lrck  (audio_lrck),
        .audio_dac   (audio_dac)
    );

endmodule
