//
// Audio output module for openfpgaOS
// - Dual-clock FIFO bridges CPU clock to audio clock domain
// - I2S serializer outputs 48 kHz 16-bit stereo
// - Based on openfpga-litex audio.sv and sound_i2s.sv patterns
//

`default_nettype none

module audio_output (
    input  wire        clk_sys,       // CPU clock (FIFO write side)
    input  wire        clk_audio,     // 12.288 MHz (FIFO read side, audio master clock)
    input  wire        reset_n,

    // CPU write interface (PCM samples from hardware mixer)
    input  wire        sample_wr,     // Write strobe (one clk_sys cycle)
    input  wire [31:0] sample_data,   // {left[15:0], right[15:0]}
    output wire [9:0]  fifo_level,    // Write-side fill level (0..1024)
    output wire        fifo_full,

    // I2S output
    output wire        audio_mclk,    // 12.288 MHz passthrough
    output wire        audio_lrck,    // Left/right clock (48 kHz)
    output wire        audio_dac      // Serial data
);

// ============================================
// MCLK passthrough
// ============================================
assign audio_mclk = clk_audio;

// ============================================
// Dual-clock FIFO (clk_sys -> clk_audio)
// ============================================
wire [15:0] fifo_l;
wire [15:0] fifo_r;
wire        fifo_empty;

dcfifo dcfifo_audio (
    .wrclk   (clk_sys),
    .rdclk   (clk_audio),

    .data    (sample_data),
    .wrreq   (sample_wr),

    .q       ({fifo_l, fifo_r}),
    .rdreq   (audio_pop && !fifo_empty),

    .rdempty (fifo_empty),
    .wrusedw (fifo_level),
    .wrfull  (fifo_full),

    .aclr    (~reset_n)
);
defparam dcfifo_audio.intended_device_family = "Cyclone V",
    dcfifo_audio.lpm_numwords  = 1024,
    dcfifo_audio.lpm_showahead = "ON",
    dcfifo_audio.lpm_type      = "dcfifo",
    dcfifo_audio.lpm_width     = 32,
    dcfifo_audio.lpm_widthu    = 10,
    dcfifo_audio.overflow_checking  = "ON",
    dcfifo_audio.underflow_checking = "ON",
    dcfifo_audio.rdsync_delaypipe   = 5,
    dcfifo_audio.wrsync_delaypipe   = 5,
    dcfifo_audio.use_eab       = "ON";

// ============================================
// 48 kHz sample pop (12.288 MHz / 256 = 48 kHz)
// ============================================
reg [7:0] mclk_div = 8'hFF;
reg       audio_pop = 0;

always @(posedge clk_audio) begin
    if (!reset_n) begin
        mclk_div  <= 8'hFF;
        audio_pop <= 1'b0;
    end else begin
        audio_pop <= 1'b0;
        if (mclk_div > 0) begin
            mclk_div <= mclk_div - 8'd1;
        end else begin
            mclk_div  <= 8'hFF;
            audio_pop <= 1'b1;
        end
    end
end

// ============================================
// SCLK generation (3.072 MHz = MCLK / 4)
// ============================================
reg [1:0] sclk_div;
wire      audgen_sclk = sclk_div[1] /* synthesis keep */;

always @(posedge clk_audio) begin
    if (!reset_n)
        sclk_div <= 2'd0;
    else
        sclk_div <= sclk_div + 2'd1;
end

// ============================================
// I2S serializer (16-bit signed stereo)
// ============================================
// Data format: 32 bits per channel (16 data + 16 dummy), MSB first
// LRCK toggles every 32 SCLK cycles

// Hold last valid sample on FIFO underrun, then ramp to zero.
// Immediate drop to silence causes a hard discontinuity = audible pop.
// Gentle decay (>>>8 ≈ 0.4% per sample) minimises slope discontinuity.
reg signed [15:0] hold_l = 16'sh0;
reg signed [15:0] hold_r = 16'sh0;

always @(posedge clk_audio) begin
    if (!reset_n) begin
        hold_l <= 16'sh0;
        hold_r <= 16'sh0;
    end else if (audio_pop) begin
        if (!fifo_empty) begin
            hold_l <= $signed(fifo_l);
            hold_r <= $signed(fifo_r);
        end else begin
            hold_l <= hold_l - (hold_l >>> 8);
            hold_r <= hold_r - (hold_r >>> 8);
        end
    end
end

wire signed [15:0] sfx_l = fifo_empty ? hold_l : $signed(fifo_l);
wire signed [15:0] sfx_r = fifo_empty ? hold_r : $signed(fifo_r);

// ============================================
// Pass-through to DAC.  Mixer output is already 16-bit hard-saturated
// in audio_mixer.v (S_OUTPUT clamp at ±32767), so no further headroom
// or knee is required.  An earlier 1.25× post-boost + 2:1 soft-knee
// compressor lived here to compensate for the old /2 mixer mixdown;
// it caused audible 2:1 compression artifacts on busy/transient mixes
// ("crackle on busy passages") because the boost pushed in-range
// mixer samples past the 24576 knee.  Now that the mixer mixes down
// /8 (sufficient headroom for multi-voice peaks), no post-boost is
// needed and a clean passthrough is the right answer.
// ============================================
wire [15:0] mix_clamp_l = sfx_l;
wire [15:0] mix_clamp_r = sfx_r;

// Latch mixer output on audio_pop (48 kHz) for stable I2S serialization.
reg [15:0] active_l = 16'h0;
reg [15:0] active_r = 16'h0;
always @(posedge clk_audio) begin
    if (!reset_n) begin
        active_l <= 16'h0;
        active_r <= 16'h0;
    end else if (audio_pop) begin
        active_l <= mix_clamp_l;
        active_r <= mix_clamp_r;
    end
end

reg [31:0] audgen_sampshift;
reg [4:0]  audgen_lrck_cnt;
reg        audgen_lrck;
reg        audgen_dac;
wire       audgen_sclk_fall = (sclk_div == 2'b11);

always @(posedge clk_audio) begin
    if (!reset_n) begin
        audgen_sampshift <= 32'd0;
        audgen_lrck_cnt  <= 5'd0;
        audgen_lrck      <= 1'b0;
        audgen_dac       <= 1'b0;
    end else if (audgen_sclk_fall) begin
        // Output next bit
        audgen_dac <= audgen_sampshift[31];

        // 48 kHz * 64 bits = 3.072 MHz
        audgen_lrck_cnt <= audgen_lrck_cnt + 5'd1;
        if (audgen_lrck_cnt == 5'd31) begin
            // Switch channels
            audgen_lrck <= ~audgen_lrck;

            // Reload sample data at start of left channel
            if (~audgen_lrck) begin
                audgen_sampshift <= {active_l, active_r};
            end
        end else if (audgen_lrck_cnt < 5'd16) begin
            // Shift out 16 active bits per channel
            audgen_sampshift <= {audgen_sampshift[30:0], 1'b0};
        end
    end
end

assign audio_lrck = audgen_lrck;
assign audio_dac  = audgen_dac;

endmodule
