// SPDX-License-Identifier: Apache-2.0
// Buffer mixer completions and present one stereo pair per 48 kHz tick.
// The mixer can run ahead by eight samples (~167 us) to cover SDRAM stalls.
`default_nettype none

module mister_audio_output (
    input wire clk,
    input wire reset_n,
    input wire mixer_enable,
    input wire clk_is90,
    input wire sample_wr,
    input wire [31:0] sample_data,
    output wire [9:0] fifo_level,
    output wire fifo_full,
    output reg [15:0] audio_l,
    output reg [15:0] audio_r
);

reg [32:0] pace_acc /* synthesis preserve */;
wire pace_tick = pace_acc[32];
reg playing;
wire [4:0] count;
wire empty;
wire [31:0] head;
wire pop = pace_tick && !empty && (playing || count >= 5'd8);

// Stop requesting samples at eight queued pairs. The mixer may already be
// starting one more pass when sample_wr increments count, so leave headroom.
assign fifo_full = (count >= 5'd8);
assign fifo_level = fifo_full ? 10'd1023 : 10'd0;

// Keep this small fall-through FIFO in registers. Its MLAB write-address
// hold path can fail after unrelated placement changes; registers preserve
// the same read latency without that memory hold constraint.
sync_fifo #(.WIDTH(32), .DEPTH(16), .ADDR_WIDTH(4), .RAMSTYLE("logic")) samples (
    .clk(clk), .reset(!reset_n), .clear(!mixer_enable),
    .push(sample_wr), .din(sample_data), .pop(pop), .dout(head),
    .empty(empty), .full(), .count(count)
);

always @(posedge clk) begin
    if (!reset_n || !mixer_enable) begin
        pace_acc <= 33'd0;
        playing <= 1'b0;
        audio_l <= 16'd0;
        audio_r <= 16'd0;
    end else begin
        // round(48000 / f_cpu * 2^32), including the runtime 90 MHz fallback.
        pace_acc <= {1'b0, pace_acc[31:0]} +
                    (clk_is90 ? 33'd2290649 : 33'd2061584);
        if (pace_tick) begin
            playing <= pop;
            if (pop) begin
                audio_l <= head[31:16];
                audio_r <= head[15:0];
            end
            // A longer underrun holds the last pair and refills before
            // restarting. Never compress catch-up samples into a DAC period.
        end
    end
end

endmodule
`default_nettype wire
