// =============================================================================
// tb_audio.v — Verilator harness for audio_mixer + audio_awe + audio_output
// =============================================================================
// Simulates the full audio pipeline at the cycle level.  Replaces the CRAM1
// path with a simple behavioral burst-server (constant 4-cycle latency, 1 word
// per cycle) so we can stress the mixer FSM without modeling PSRAM timing.
//
// The C++ harness pokes the AWE coprocessor via cpu_* strobes (note_on,
// note_off, channel CC writes, global effect writes) and captures sample_wr
// pulses from the mixer.  Underrun is signalled when the FIFO would read
// while empty.
// =============================================================================

`default_nettype none
`timescale 1ns / 1ps

module tb_audio (
    input  wire        clk,
    input  wire        reset_n,

    // ── AWE control inputs (poked from C++) ────────────────────────────────
    input  wire        cpu_voice_state_wr,
    input  wire [10:0] cpu_voice_state_addr,
    input  wire [31:0] cpu_voice_state_wdata,

    input  wire        cpu_chan_wr,
    input  wire [5:0]  cpu_chan_addr,
    input  wire [31:0] cpu_chan_wdata,

    input  wire        cpu_mm_wr,
    input  wire [5:0]  cpu_mm_voice,
    input  wire [2:0]  cpu_mm_field,
    input  wire signed [15:0] cpu_mm_wdata,

    input  wire        cpu_global_wr,
    input  wire [3:0]  cpu_global_addr,
    input  wire [31:0] cpu_global_wdata,

    input  wire        cpu_note_on,
    input  wire [5:0]  cpu_note_on_voice,
    input  wire        cpu_note_off,
    input  wire [5:0]  cpu_note_off_voice,
    input  wire        cpu_voice_stop,
    input  wire [5:0]  cpu_voice_stop_voice,

    input  wire        cpu_ramp1_trig,
    input  wire [5:0]  cpu_ramp1_voice,
    input  wire [3:0]  cpu_ramp1_stage,
    input  wire [31:0] cpu_ramp1_rate,

    // ── Direct mixer voice writes (CPU path, not used here but wired) ──────
    input  wire        cpu_mix_voice_wr,
    input  wire [3:0]  cpu_mix_voice_field,
    input  wire [5:0]  cpu_mix_voice_sel,
    input  wire [31:0] cpu_mix_voice_wdata,

    // ── Audio output sample stream (captured by C++) ───────────────────────
    output wire        sample_wr,
    output wire [31:0] sample_data,
    output wire [9:0]  fifo_level,
    output wire [5:0]  active_count,
    output reg         fifo_underrun,        // pulses when "DAC" would starve
    output reg  [31:0] underrun_count,

    // ── Status / debug ─────────────────────────────────────────────────────
    output wire        cram1_burst_rd_obs,
    output wire        cram1_burst_q_valid_obs,
    output wire        cram1_burst_busy_obs,
    output wire [21:0] cram1_burst_addr_obs,
    output wire [31:0] awe_active_mask,
    output wire [31:0] awe_tick_count,

    // ── Output-level / continuity counters (sample by sample) ──────────────
    // Updated on each sample_wr pulse.  Differential = |sample - previous|
    // for both channels; spikes above a threshold count as discontinuities.
    output reg  signed [15:0] last_left,
    output reg  signed [15:0] last_right,
    output reg  [15:0] peak_left_abs,    // running max |left|
    output reg  [15:0] peak_right_abs,   // running max |right|
    output reg  [15:0] max_delta_l,      // largest |left[n] - left[n-1]|
    output reg  [15:0] max_delta_r,      // largest |right[n] - right[n-1]|
    output reg  [31:0] discontinuity_count,  // count of |delta| > THRESHOLD
    output reg  [63:0] sum_abs_left,     // for RMS-ish average level
    output reg  [63:0] sum_abs_right,
    output reg  [31:0] samples_count
);

// Threshold for "discontinuity" — half of full scale.  A real click is a
// jump of >50% of range in one sample period.  Lower than this is normal
// signal motion at 48 kHz for any reasonable musical content.
localparam [15:0] DISCONTINUITY_THRESHOLD = 16'd16384;

// ─────────────────────────────────────────────────────────────────────────────
// AWE → mixer voice-write bus (priority mux: AWE > CPU)
// ─────────────────────────────────────────────────────────────────────────────
wire        awe_mix_voice_wr;
wire [3:0]  awe_mix_voice_field;
wire [5:0]  awe_mix_voice_sel;
wire [31:0] awe_mix_voice_wdata;

wire        mix_voice_wr    = awe_mix_voice_wr | cpu_mix_voice_wr;
wire [3:0]  mix_voice_field = awe_mix_voice_wr ? awe_mix_voice_field : cpu_mix_voice_field;
wire [5:0]  mix_voice_sel   = awe_mix_voice_wr ? awe_mix_voice_sel   : cpu_mix_voice_sel;
wire [31:0] mix_voice_wdata = awe_mix_voice_wr ? awe_mix_voice_wdata : cpu_mix_voice_wdata;

// AWE-driven globals (effects)
wire [7:0]  reverb_wet_level;
wire [7:0]  reverb_feedback;
wire [7:0]  chorus_wet_level;
wire [15:0] chorus_lfo_rate;
wire [7:0]  chorus_lfo_depth;

// ─────────────────────────────────────────────────────────────────────────────
// Behavioral CRAM1 burst model
// ─────────────────────────────────────────────────────────────────────────────
// Mixer issues `cram1_burst_rd` (1 cycle pulse) with addr + len (5'd7 = 8 words).
// We respond:
//   - assert cram1_burst_busy on the cycle after the request
//   - 4-cycle initial latency, then 1 word/cycle for `len+1` words
//   - cram1_burst_q_valid pulses each delivered word
//   - synthetic data: encode the linear address as a 16-bit triangle wave
//     (low/high halves of each 32-bit word) so the mixer reads non-zero data
wire        cram1_burst_rd;
wire [21:0] cram1_burst_addr;
wire [4:0]  cram1_burst_len;
reg  [31:0] cram1_burst_q;
reg         cram1_burst_q_valid;
reg         cram1_burst_busy;

reg  [3:0]  cram_lat_cnt;
reg  [4:0]  cram_word_cnt;
reg  [21:0] cram_cur_addr;
reg  [4:0]  cram_cur_len;

assign cram1_burst_rd_obs       = cram1_burst_rd;
assign cram1_burst_q_valid_obs  = cram1_burst_q_valid;
assign cram1_burst_busy_obs     = cram1_burst_busy;
assign cram1_burst_addr_obs     = cram1_burst_addr;

// Synthetic sample generator: 16-bit triangle wave of period 256 samples,
// independent for each "voice base" so each voice has a recognisable signal
function [15:0] synth_sample;
    input [21:0] sample_addr;  // word address
    reg [7:0] phase;
    begin
        phase = sample_addr[7:0];
        if (phase < 128)
            synth_sample = {1'b0, phase, 7'd0};       // ramp up to ~+16k
        else
            synth_sample = -({1'b0, phase - 8'd128, 7'd0});  // ramp down
    end
endfunction

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        cram_lat_cnt        <= 0;
        cram_word_cnt       <= 0;
        cram_cur_addr       <= 0;
        cram_cur_len        <= 0;
        cram1_burst_q       <= 0;
        cram1_burst_q_valid <= 0;
        cram1_burst_busy    <= 0;
    end else begin
        cram1_burst_q_valid <= 0;
        if (cram1_burst_rd && !cram1_burst_busy) begin
            cram_cur_addr    <= cram1_burst_addr;
            cram_cur_len     <= cram1_burst_len;
            cram_lat_cnt     <= 4'd4;     // 4-cycle initial latency
            cram_word_cnt    <= 0;
            cram1_burst_busy <= 1;
        end else if (cram1_burst_busy) begin
            if (cram_lat_cnt > 0) begin
                cram_lat_cnt <= cram_lat_cnt - 4'd1;
            end else begin
                /* Stream one word per cycle.  Pack two synth samples per
                 * 32-bit word so the mixer's 16-bit fmt reads valid data. */
                cram1_burst_q       <= {synth_sample(cram_cur_addr + cram_word_cnt + 22'd1),
                                        synth_sample(cram_cur_addr + cram_word_cnt)};
                cram1_burst_q_valid <= 1;
                if (cram_word_cnt == cram_cur_len) begin
                    cram1_burst_busy <= 0;
                    cram_word_cnt    <= 0;
                end else begin
                    cram_word_cnt <= cram_word_cnt + 5'd1;
                end
            end
        end
    end
end

// ─────────────────────────────────────────────────────────────────────────────
// Per-sample output continuity + level capture
// ─────────────────────────────────────────────────────────────────────────────
wire signed [15:0] cur_left  = sample_data[15:0];
wire signed [15:0] cur_right = sample_data[31:16];
wire signed [16:0] dl_s17 = $signed({cur_left[15],  cur_left})  - $signed({last_left[15],  last_left});
wire signed [16:0] dr_s17 = $signed({cur_right[15], cur_right}) - $signed({last_right[15], last_right});
wire [15:0] dl_abs = (dl_s17 < 0) ? -dl_s17[15:0] : dl_s17[15:0];
wire [15:0] dr_abs = (dr_s17 < 0) ? -dr_s17[15:0] : dr_s17[15:0];
wire [15:0] cl_abs = cur_left[15]  ? -cur_left  : cur_left;
wire [15:0] cr_abs = cur_right[15] ? -cur_right : cur_right;

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        last_left           <= 16'sd0;
        last_right          <= 16'sd0;
        peak_left_abs       <= 16'd0;
        peak_right_abs      <= 16'd0;
        max_delta_l         <= 16'd0;
        max_delta_r         <= 16'd0;
        discontinuity_count <= 32'd0;
        sum_abs_left        <= 64'd0;
        sum_abs_right       <= 64'd0;
        samples_count       <= 32'd0;
    end else if (sample_wr) begin
        last_left  <= cur_left;
        last_right <= cur_right;
        if (cl_abs > peak_left_abs)  peak_left_abs  <= cl_abs;
        if (cr_abs > peak_right_abs) peak_right_abs <= cr_abs;
        if (samples_count != 0) begin
            if (dl_abs > max_delta_l) max_delta_l <= dl_abs;
            if (dr_abs > max_delta_r) max_delta_r <= dr_abs;
            if (dl_abs > DISCONTINUITY_THRESHOLD ||
                dr_abs > DISCONTINUITY_THRESHOLD)
                discontinuity_count <= discontinuity_count + 32'd1;
        end
        sum_abs_left  <= sum_abs_left  + {48'd0, cl_abs};
        sum_abs_right <= sum_abs_right + {48'd0, cr_abs};
        samples_count <= samples_count + 32'd1;
    end
end

// ─────────────────────────────────────────────────────────────────────────────
// Behavioural FIFO model: drains every 2083 cycles (= 100 MHz / 48 kHz).
// Tracks fifo_level so the mixer knows when to start a new sample.  Fires
// underrun if the DAC would pop while empty.
// ─────────────────────────────────────────────────────────────────────────────
reg [9:0]  fifo_count;
reg [11:0] pop_div;       // 0..2082, fires DAC pop when reaches 0
assign fifo_level = fifo_count;

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        fifo_count     <= 10'd0;
        pop_div        <= 12'd0;
        fifo_underrun  <= 1'b0;
        underrun_count <= 32'd0;
    end else begin
        fifo_underrun <= 1'b0;
        if (pop_div == 12'd2082) begin
            pop_div <= 12'd0;
            if (fifo_count == 10'd0) begin
                fifo_underrun  <= 1'b1;
                underrun_count <= underrun_count + 32'd1;
                /* don't decrement past zero */
            end else begin
                /* simultaneous push+pop: net change 0 */
                if (sample_wr)
                    fifo_count <= fifo_count;       // +1 -1
                else
                    fifo_count <= fifo_count - 10'd1;
            end
        end else begin
            pop_div <= pop_div + 12'd1;
            if (sample_wr)
                fifo_count <= (fifo_count == 10'd1023) ? 10'd1023
                                                       : fifo_count + 10'd1;
        end
    end
end

// ─────────────────────────────────────────────────────────────────────────────
// audio_awe coprocessor
// ─────────────────────────────────────────────────────────────────────────────
audio_awe u_awe (
    .clk(clk),
    .reset_n(reset_n),

    .cpu_voice_state_wr(cpu_voice_state_wr),
    .cpu_voice_state_addr(cpu_voice_state_addr),
    .cpu_voice_state_wdata(cpu_voice_state_wdata),

    .cpu_chan_wr(cpu_chan_wr),
    .cpu_chan_addr(cpu_chan_addr),
    .cpu_chan_wdata(cpu_chan_wdata),

    .cpu_mm_wr(cpu_mm_wr),
    .cpu_mm_voice(cpu_mm_voice),
    .cpu_mm_field(cpu_mm_field),
    .cpu_mm_wdata(cpu_mm_wdata),

    .cpu_global_wr(cpu_global_wr),
    .cpu_global_addr(cpu_global_addr),
    .cpu_global_wdata(cpu_global_wdata),

    .cpu_note_on(cpu_note_on),
    .cpu_note_on_voice(cpu_note_on_voice),
    .cpu_note_off(cpu_note_off),
    .cpu_note_off_voice(cpu_note_off_voice),
    .cpu_voice_stop(cpu_voice_stop),
    .cpu_voice_stop_voice(cpu_voice_stop_voice),

    .cpu_ramp1_trig(cpu_ramp1_trig),
    .cpu_ramp1_voice(cpu_ramp1_voice),
    .cpu_ramp1_stage(cpu_ramp1_stage),
    .cpu_ramp1_rate(cpu_ramp1_rate),

    .awe_mix_voice_wr(awe_mix_voice_wr),
    .awe_mix_voice_field(awe_mix_voice_field),
    .awe_mix_voice_sel(awe_mix_voice_sel),
    .awe_mix_voice_wdata(awe_mix_voice_wdata),

    .active_mask(awe_active_mask),
    .tick_count(awe_tick_count),

    .reverb_wet_level(reverb_wet_level),
    .reverb_feedback(reverb_feedback),
    .chorus_wet_level(chorus_wet_level),
    .chorus_lfo_rate(chorus_lfo_rate),
    .chorus_lfo_depth(chorus_lfo_depth)
);

// ─────────────────────────────────────────────────────────────────────────────
// audio_mixer
// ─────────────────────────────────────────────────────────────────────────────
audio_mixer u_mixer (
    .clk(clk),
    .reset_n(reset_n),
    .mixer_enable(1'b1),

    .voice_wr(mix_voice_wr),
    .voice_field(mix_voice_field),
    .voice_sel(mix_voice_sel),
    .voice_sel_rd(mix_voice_sel),
    .voice_wdata(mix_voice_wdata),

    .cram1_burst_rd(cram1_burst_rd),
    .cram1_burst_addr(cram1_burst_addr),
    .cram1_burst_len(cram1_burst_len),
    .cram1_burst_q(cram1_burst_q),
    .cram1_burst_q_valid(cram1_burst_q_valid),
    .cram1_burst_busy(cram1_burst_busy),
    .cram1_busy(cram1_burst_busy),

    .sample_wr(sample_wr),
    .sample_data(sample_data),
    .fifo_level(fifo_level),
    .active_count(active_count),

    .pos_readback(),

    .irq_clear(32'd0),
    .irq_clear_wr(1'b0),
    .voice_end_pending(),
    .voice_end_irq(),

    .reverb_wet_level(reverb_wet_level),
    .reverb_feedback(reverb_feedback),
    .chorus_wet_level(chorus_wet_level),
    .chorus_lfo_rate(chorus_lfo_rate),
    .chorus_lfo_depth(chorus_lfo_depth),

    .cram1_inhibit(1'b0)
);

endmodule
