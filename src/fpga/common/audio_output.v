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

    // CPU write interface (SFX samples)
    input  wire        sample_wr,     // Write strobe (one clk_sys cycle)
    input  wire [31:0] sample_data,   // {left[15:0], right[15:0]}
    output wire [8:0]  fifo_level,    // Write-side fill level
    output wire        fifo_full,

    // OPL3 hardware audio input (from opl3_wrapper, clk_audio domain)
    input  wire signed [15:0] opl_audio_in_l,
    input  wire signed [15:0] opl_audio_in_r,
    input  wire               opl_toggle_in,   // toggles on each new OPL sample

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
    dcfifo_audio.lpm_numwords  = 512,
    dcfifo_audio.lpm_showahead = "OFF",
    dcfifo_audio.lpm_type      = "dcfifo",
    dcfifo_audio.lpm_width     = 32,
    dcfifo_audio.lpm_widthu    = 9,
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
    audio_pop <= 0;
    if (mclk_div > 0) begin
        mclk_div <= mclk_div - 8'd1;
    end else begin
        mclk_div  <= 8'hFF;
        audio_pop <= 1;
    end
end

// ============================================
// SCLK generation (3.072 MHz = MCLK / 4)
// ============================================
reg [1:0] sclk_div;
wire      audgen_sclk = sclk_div[1] /* synthesis keep */;

always @(posedge clk_audio) begin
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
    if (audio_pop) begin
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
// OPL3 sample-rate conversion: ~49,749 Hz → 48,000 Hz
// ============================================
// OPL3 produces stereo at 12,288,000/247 ≈ 49,749 Hz.  I2S consumes at
// 48,000 Hz.  A small FIFO absorbs jitter; a consumer-driven SRC pops
// 1 or 2 samples per output tick and linearly interpolates between the
// two most recent samples (A, B) using a 2.16 fixed-point phase.
//
// Resample step: 12,288,000 * 65,536 / (247 * 48,000) = 67,924 (0.16 FP).
localparam [16:0] OPL_RESAMPLE_STEP = 17'd67924;  // 1.036× in 1.16 FP

// --- Producer: OPL → 4-entry stereo FIFO (owns opl_wr_ptr, opl_tog_prev) ---
reg [31:0] opl_fifo [0:3];   // packed {L[15:0], R[15:0]}
reg [2:0]  opl_wr_ptr = 3'd0;
reg [2:0]  opl_rd_ptr = 3'd0;  // declared here, driven only by consumer block
wire       opl_fifo_full = (opl_wr_ptr[1:0] == opl_rd_ptr[1:0]) &&
                            (opl_wr_ptr[2]   != opl_rd_ptr[2]);
reg        opl_tog_prev = 1'b0;

always @(posedge clk_audio) begin
    if (!reset_n) begin
        opl_wr_ptr   <= 3'd0;
        opl_tog_prev <= 1'b0;
    end else begin
        opl_tog_prev <= opl_toggle_in;
        if (opl_toggle_in != opl_tog_prev && !opl_fifo_full) begin
            opl_fifo[opl_wr_ptr[1:0]] <= {opl_audio_in_l, opl_audio_in_r};
            opl_wr_ptr <= opl_wr_ptr + 3'd1;
        end
    end
end

// --- Consumer: phase-driven SRC (owns opl_rd_ptr, opl_phase, A/B) ---
reg signed [15:0] opl_a_l = 16'sh0, opl_a_r = 16'sh0;
reg signed [15:0] opl_b_l = 16'sh0, opl_b_r = 16'sh0;
reg        [17:0] opl_phase = 18'd0;   // 2.16 fixed-point

wire [2:0] opl_fifo_avail = opl_wr_ptr - opl_rd_ptr;

always @(posedge clk_audio) begin
    if (!reset_n) begin
        opl_a_l    <= 16'sh0;  opl_a_r <= 16'sh0;
        opl_b_l    <= 16'sh0;  opl_b_r <= 16'sh0;
        opl_phase  <= 18'd0;
        opl_rd_ptr <= 3'd0;
    end else if (audio_pop) begin
        begin : opl_src_advance
            reg [17:0] new_phase;
            reg [1:0]  wanted, actual;

            new_phase = opl_phase + {1'b0, OPL_RESAMPLE_STEP};
            // Cap wanted at 2 — handles sustained underrun where debt > 2
            wanted = (new_phase[17:16] > 2'd2) ? 2'd2 : new_phase[17:16];

            // Clamp to available FIFO entries (full 3-bit compare)
            actual = ({1'b0, wanted} <= opl_fifo_avail) ? wanted :
                     opl_fifo_avail[1:0];

            if (actual == 2'd1) begin
                opl_a_l <= opl_b_l;
                opl_a_r <= opl_b_r;
                opl_b_l <= $signed(opl_fifo[opl_rd_ptr[1:0]][31:16]);
                opl_b_r <= $signed(opl_fifo[opl_rd_ptr[1:0]][15:0]);
                opl_rd_ptr <= opl_rd_ptr + 3'd1;
            end else if (actual == 2'd2) begin
                begin : double_pop
                    reg [2:0] rd1, rd2;
                    rd1 = opl_rd_ptr;
                    rd2 = opl_rd_ptr + 3'd1;
                    opl_a_l <= $signed(opl_fifo[rd1[1:0]][31:16]);
                    opl_a_r <= $signed(opl_fifo[rd1[1:0]][15:0]);
                    opl_b_l <= $signed(opl_fifo[rd2[1:0]][31:16]);
                    opl_b_r <= $signed(opl_fifo[rd2[1:0]][15:0]);
                    opl_rd_ptr <= opl_rd_ptr + 3'd2;
                end
            end

            // Subtract consumed intervals; keep fractional remainder + debt.
            // Only advance phase when we actually consumed samples —
            // otherwise the accumulator runs away and the LERP fraction
            // wraps, producing distortion.
            if (actual > 0)
                opl_phase <= new_phase - {actual, 16'd0};
        end
    end
end

// Linear interpolation between A and B using fractional phase [15:0]
wire [15:0]        opl_frac   = opl_phase[15:0];
wire signed [16:0] opl_diff_l = {opl_b_l[15], opl_b_l} - {opl_a_l[15], opl_a_l};
wire signed [16:0] opl_diff_r = {opl_b_r[15], opl_b_r} - {opl_a_r[15], opl_a_r};
wire signed [16:0] opl_frac_s = $signed({1'b0, opl_frac});
wire signed [33:0] opl_prod_l = opl_diff_l * opl_frac_s;
wire signed [33:0] opl_prod_r = opl_diff_r * opl_frac_s;
wire signed [16:0] opl_off_l  = $signed(opl_prod_l[32:16]);
wire signed [16:0] opl_off_r  = $signed(opl_prod_r[32:16]);
wire signed [16:0] opl_wide_l = {opl_a_l[15], opl_a_l} + opl_off_l;
wire signed [16:0] opl_wide_r = {opl_a_r[15], opl_a_r} + opl_off_r;
wire signed [15:0] opl_lerp_l = opl_wide_l[15:0];
wire signed [15:0] opl_lerp_r = opl_wide_r[15:0];

// ============================================
// Final mix: PCM (sfx) + OPL3 stereo
// ============================================
// No OPL pre-boost.  OPL at >>>3 (0.125×) enters the mix directly.
// Net: PCM = 0.5 × 1.25 post = 0.625×.  OPL = 0.125 × 1.25 post = 0.156×.
wire signed [17:0] mix_l = {{2{sfx_l[15]}}, sfx_l} + {{2{opl_lerp_l[15]}}, opl_lerp_l};
wire signed [17:0] mix_r = {{2{sfx_r[15]}}, sfx_r} + {{2{opl_lerp_r[15]}}, opl_lerp_r};

// 1.25× post-mix boost (x + x>>>2) — into 19-bit before soft sat.
wire signed [18:0] out_l = {mix_l[17], mix_l} + {{3{mix_l[17]}}, mix_l[17:2]};
wire signed [18:0] out_r = {mix_r[17], mix_r} + {{3{mix_r[17]}}, mix_r[17:2]};
// Soft saturation: linear below 75% threshold, 2:1 compression above.
function [15:0] soft_sat;
    input signed [18:0] x;
    reg signed [18:0] y;
    begin
        if (x > 19'sd24576)
            y = 19'sd24576 + ((x - 19'sd24576) >>> 1);
        else if (x < -19'sd24576)
            y = -19'sd24576 + ((x + 19'sd24576) >>> 1);
        else
            y = x;
        soft_sat = (y > 19'sd32767)  ? 16'h7FFF :
                   (y < -19'sd32768) ? 16'h8000 :
                   y[15:0];
    end
endfunction

wire [15:0] mix_clamp_l = soft_sat(out_l);
wire [15:0] mix_clamp_r = soft_sat(out_r);

// Latch mixer output on audio_pop (48 kHz) for stable I2S serialization.
reg [15:0] active_l = 16'h0;
reg [15:0] active_r = 16'h0;
always @(posedge clk_audio) begin
    if (audio_pop) begin
        active_l <= mix_clamp_l;
        active_r <= mix_clamp_r;
    end
end

reg [31:0] audgen_sampshift;
reg [4:0]  audgen_lrck_cnt;
reg        audgen_lrck;
reg        audgen_dac;

always @(negedge audgen_sclk) begin
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

assign audio_lrck = audgen_lrck;
assign audio_dac  = audgen_dac;

endmodule
