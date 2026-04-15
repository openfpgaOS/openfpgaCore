//
// Hardware PCM Mixer — 48 voices from CRAM1
//
// Reads signed mono samples (16-bit or 8-bit) from CRAM1, resamples via
// Hermite (Catmull-Rom) interpolation with 16.16 fixed-point positioning,
// mixes with per-voice stereo 8-bit volume (log curve v²/256), and writes
// packed stereo pairs to the audio FIFO for I2S output.
//
// Runs on clk_cpu (100 MHz).  CRAM1 access shared with CPU via a 2-way
// mux (CPU > mixer priority).  Bridge has its own controller on clk_74a.
//
// Voice descriptors stored in dual-port BRAM (3 M10K blocks).
// CPU writes via indirect register interface (select voice, write field).
// Mixer FSM reads all 48 voices sequentially each sample period.
//
// Per-voice features:
//   - Hermite (Catmull-Rom) 4-tap sample interpolation
//   - 8-bit stereo volume with log curve (VOL_L, VOL_R)
//   - Hardware volume ramp (VOL_TARGET + VOL_RATE)
//   - 16.16 fixed-point resampling
//   - Forward and bidirectional (ping-pong) looping
//   - 16-bit or 8-bit signed sample format
//   - Position read-back and write
//   - Voice-end IRQ (bit per voice, W1C)
//
// Gated by reset_n & mixer_enable — safe during shutdown/saves.
//

`default_nettype none

module audio_mixer (
    input wire clk,
    input wire reset_n,

    // Mixer enable (from register)
    input wire mixer_enable,

    // Voice configuration (from axi_periph_slave).  Writes land directly
    // on vtbl port B every cycle voice_wr pulses — no FIFO, no stall.
    input wire        voice_wr,
    input wire [3:0]  voice_field,   // 0-14: BRAM fields
    input wire [5:0]  voice_sel,     // selected voice index
    input wire [5:0]  voice_sel_rd,  // direct passthrough for position readback
    input wire [31:0] voice_wdata,

    // CRAM1 read interface (shared with CPU via arbiter)
    output reg         cram1_rd,
    output reg  [21:0] cram1_addr,
    input  wire [31:0] cram1_rdata,
    input  wire        cram1_busy,
    input  wire        cram1_rdata_valid,
    input  wire        cram1_rd_accepted,  // mux confirms our read went through

    // Audio FIFO interface
    output reg         sample_wr,
    output reg  [31:0] sample_data,
    input  wire [8:0]  fifo_level,

    // Status
    output wire [5:0]  active_count,

    // Position read-back — mux over a per-voice latched array so a
    // CPU SEL switch returns the selected voice's latest position
    // immediately, rather than waiting up to one mixer cycle (~20us)
    // for the FSM to re-scan that voice.
    output wire [21:0] pos_readback,

    // Voice-end IRQ
    input  wire [47:0] irq_clear,     // W1C from CPU
    input  wire        irq_clear_wr,
    output reg  [47:0] voice_end_pending,
    output wire        voice_end_irq
);

wire mixer_active = reset_n & mixer_enable;
assign voice_end_irq = |voice_end_pending;

// ============================================
// Voice table in dual-port BRAM
// ============================================
// 16-word stride per voice, 48 voices = 768 words (3 M10K blocks)
//
//   Word 0:  base_addr[21:0]
//   Word 1:  length[21:0]
//   Word 2:  rate[31:0]         — 16.16 fixed-point playback rate
//   Word 3:  ctrl               — {dir[4], bidi[3], fmt16[2], loop[1], active[0]}
//   Word 4:  pos_int[21:0]
//   Word 5:  pos_frac[15:0]
//   Word 6:  vol_lr             — {vol_r[7:0], vol_l[7:0]} (current, ramped by HW)
//   Word 7:  loop_end[21:0]
//   Word 8:  loop_start[21:0]
//   Word 9:  vol_target         — {target_r[7:0], target_l[7:0]}
//   Word 10: vol_rate[7:0]      — ramp step size (0=instant)
//   Words 11-15: reserved

localparam VTBL_ADDR       = 4'd0;
localparam VTBL_LEN        = 4'd1;
localparam VTBL_RATE       = 4'd2;
localparam VTBL_CTRL       = 4'd3;
localparam VTBL_POS_INT    = 4'd4;
localparam VTBL_POS_FRAC   = 4'd5;
localparam VTBL_VOL_LR     = 4'd6;
localparam VTBL_LOOP_END   = 4'd7;
localparam VTBL_LOOP_START = 4'd8;
localparam VTBL_VOL_TARGET = 4'd9;
localparam VTBL_VOL_RATE   = 4'd10;

// True dual-port voice table:
//   Port A — FSM owns, reads during field scan and writes during
//            vol-ramp / pos write-back / dir write-back.
//   Port B — CPU owns, writes land directly from voice_wr/sel/field/data.
// Each agent has its own port, so neither needs to arbitrate.
reg  [9:0]  vtbl_a_addr;
reg  [31:0] vtbl_a_data;
reg         vtbl_a_wr;
wire [31:0] vtbl_a_q;

wire [9:0]  vtbl_b_addr = {voice_sel, voice_field};
wire [31:0] vtbl_b_data = voice_wdata;
wire        vtbl_b_wr   = voice_wr;

altsyncram #(
    .operation_mode("BIDIR_DUAL_PORT"),
    .width_a(32),
    .widthad_a(10),
    .width_b(32),
    .widthad_b(10),
    .numwords_a(768),
    .numwords_b(768),
    .clock_enable_input_a("BYPASS"),
    .clock_enable_input_b("BYPASS"),
    .clock_enable_output_a("BYPASS"),
    .clock_enable_output_b("BYPASS"),
    .outdata_reg_a("UNREGISTERED"),
    .outdata_reg_b("UNREGISTERED"),
    .read_during_write_mode_mixed_ports("OLD_DATA"),
    .read_during_write_mode_port_a("NEW_DATA_NO_NBE_READ"),
    .read_during_write_mode_port_b("NEW_DATA_NO_NBE_READ"),
    .intended_device_family("Cyclone V"),
    .lpm_type("altsyncram"),
    .power_up_uninitialized("FALSE")
) voice_table (
    .clock0(clk),
    .address_a(vtbl_a_addr),
    .data_a(vtbl_a_data),
    .wren_a(vtbl_a_wr),
    .q_a(vtbl_a_q),
    .rden_a(1'b1),
    .clock1(clk),
    .address_b(vtbl_b_addr),
    .data_b(vtbl_b_data),
    .wren_b(vtbl_b_wr),
    .q_b(),
    .rden_b(1'b0),
    .aclr0(1'b0), .aclr1(1'b0),
    .addressstall_a(1'b0), .addressstall_b(1'b0),
    .byteena_a(1'b1), .byteena_b(1'b1),
    .clocken0(1'b1), .clocken1(1'b1),
    .clocken2(1'b1), .clocken3(1'b1),
    .eccstatus()
);

reg [47:0] voice_active;

// ============================================
// Mixer FSM
// ============================================
// FSM states — grouped by pipeline stage
//
// Idle / CPU write:
localparam S_IDLE        = 5'd0;
localparam S_CPU_WR      = 5'd1;
localparam S_CPU_CLR1    = 5'd2;
localparam S_CPU_CLR2    = 5'd3;
// Voice field read (BRAM pipeline):
localparam S_RD_CTRL     = 5'd4;
localparam S_RD_CTRL_W   = 5'd5;
localparam S_RD_FIELDS   = 5'd6;
localparam S_RD_FIELDS_W = 5'd7;
// Tap fetch (3-stage pipeline: resolve addr → compute word → cache/CRAM):
localparam S_TAP_RD        = 5'd8;   // issue tap cache BRAM read
localparam S_TAP_CHECK     = 5'd9;   // check cache hit
// Tap-address resolve is split across two cycles so the 24-bit raw add
// and the bounds/wrap mux don't share a 100 MHz cycle:
//   CALC : raw = cur_pos_int ± offset (registered into tap_raw)
//   FETCH: bounds-check + wrap/reflect → tap_fetch_addr
localparam S_TAP_FETCH_CALC = 5'd31; // registered 24-bit raw position
localparam S_TAP_FETCH     = 5'd10; // bounds + wrap → tap_fetch_addr
localparam S_TAP_ADDR      = 5'd11;  // cram_word_addr (registered)
localparam S_TAP_CRAM    = 5'd12;  // cache hit? extract : issue CRAM read
localparam S_TAP_WAIT    = 5'd13;  // wait for CRAM response
localparam S_TAP_NEXT    = 5'd14;  // advance tap index or start Hermite
// Hermite interpolation (6-stage pipeline):
localparam S_HERMITE_0A  = 5'd15;  // coefficient partial sums
localparam S_HERMITE_0B  = 5'd16;  // coefficient combine + shift
localparam S_HERMITE_1   = 5'd17;  // Horner: a*t + b
localparam S_HERMITE_2   = 5'd18;  // Horner: *t + c
localparam S_HERMITE_3   = 5'd19;  // Horner: *t + d
localparam S_HERMITE_4   = 5'd20;  // clamp to 16-bit
// Volume + mix:
localparam S_SCALE       = 5'd21;  // log volume v²/256
localparam S_MULTIPLY    = 5'd22;  // sample × volume (DSP)
localparam S_ACCUM       = 5'd23;  // accumulate into stereo mix
// Volume ramp is split into CALC + WR so the ramp_step / compare /
// vtbl_a_data write-mux chain doesn't all land in one 100 MHz cycle.
// CALC registers the stepped values; WR issues the voice-table write.
localparam S_VOL_RAMP    = 5'd24;  // S_VOL_RAMP_CALC: step toward target
localparam S_VOL_RAMP_WR = 5'd30;  // commit ramped VOL_LR to voice table
localparam S_WR_POS      = 5'd25;  // write back position
localparam S_WR_FRAC     = 5'd26;  // write back fractional pos
localparam S_WR_DIR      = 5'd27;  // write back direction
// Output:
localparam S_OUTPUT      = 5'd28;  // clamp + push to audio FIFO
// Transition after a port-A write, to set the next voice's CTRL
// read address.  The write states can't set vtbl_a_addr themselves
// because they drive it with their own write target; this state
// takes the next cycle to present the read address so altsyncram
// has the usual 2-cycle latency to settle into S_RD_CTRL_W.
localparam S_NEXT_VOICE  = 5'd29;

reg [4:0]  state;
reg [5:0]  cur_voice;
reg signed [31:0] accum_l, accum_r;
reg [5:0]  voice_cnt;
reg [5:0]  active_cnt;
assign active_count = active_cnt;

// Latched voice fields during mix
reg [21:0] cur_addr;
reg [21:0] cur_len;
reg [31:0] cur_rate;
reg        cur_loop;
reg        cur_fmt16;
reg        cur_bidi;
reg        cur_dir;
reg [21:0] cur_pos_int;
reg [15:0] cur_pos_frac;
reg [7:0]  cur_vol_l;
reg [7:0]  cur_vol_r;
reg [21:0] cur_loop_end;
reg [21:0] cur_loop_start;
reg [7:0]  cur_target_l;
reg [7:0]  cur_target_r;
reg [7:0]  cur_vol_rate;

// Registered ramp outputs — broken out so the ramp_step → vtbl_a_data
// chain is split across two cycles (fixes 100 MHz Fmax violation).
reg [7:0]  ramp_new_l;
reg [7:0]  ramp_new_r;

reg        new_dir;
reg        dir_changed;
reg        pos_wrapped;  // set in S_WR_POS when loop/end triggered

// Per-voice position latch — mirrored from BRAM each time the FSM
// reads a voice's POS_INT (once per mixer cycle). Muxed to pos_readback
// so a CPU SEL switch returns the selected voice's latest sample
// position without waiting up to ~20us for the next FSM visit.
reg [21:0] voice_pos_latch [0:47];
assign pos_readback = voice_pos_latch[voice_sel_rd];

// (pipe_scaled removed — volume multiply now registered in S_MULTIPLY)

// Position advance
wire [37:0] pos_combined = {cur_pos_int, cur_pos_frac};
wire [37:0] new_pos_fwd = pos_combined + {6'd0, cur_rate};
wire [37:0] new_pos_rev = pos_combined - {6'd0, cur_rate};
wire [37:0] new_pos = cur_dir ? new_pos_rev : new_pos_fwd;
wire [21:0] new_pos_int = new_pos[37:16];
wire [15:0] new_pos_frac = new_pos[15:0];
wire rev_underflow = cur_dir && new_pos[37];

// ============================================
// 4-point Hermite resampler
// ============================================

// --- CRAM sample extraction (combinational, reused by tap fetch) ---
// Extract a 16-bit signed sample from a 32-bit CRAM word given a
// sample-space position.  For 16-bit: pos[0] selects hi/lo half.
// For 8-bit: pos[1:0] selects one of 4 bytes, shifted to 16-bit.
function signed [15:0] extract_sample;
    input [31:0] word;
    input [21:0] pos;
    input        fmt16;
    reg [1:0] bs;
    begin
        if (fmt16)
            extract_sample = pos[0] ? $signed(word[31:16])
                                    : $signed(word[15:0]);
        else begin
            bs = pos[1:0];
            extract_sample = {(bs == 2'd0 ? $signed(word[7:0])   :
                               bs == 2'd1 ? $signed(word[15:8])  :
                               bs == 2'd2 ? $signed(word[23:16]) :
                                            $signed(word[31:24])), 8'd0};
        end
    end
endfunction

// CRAM word address for a given sample position
function [21:0] cram_word_addr;
    input [21:0] base;
    input [21:0] pos;
    input        fmt16;
    begin
        cram_word_addr = fmt16 ? base + {1'b0, pos[21:1]}
                               : base + {2'b0, pos[21:2]};
    end
endfunction

// --- 4 Hermite taps ---
reg signed [15:0] tap_m1, tap_0, tap_1, tap_2;

// --- Tap cache in BRAM: 48 entries × 88 bits = {valid, tag[22:0], data[63:0]} ---
// Port A: FSM writes (cache update in S_TAP_NEXT, invalidation from CPU writes)
// Port B: FSM reads (issued in S_TAP_RD, data available in S_TAP_CHECK)
reg  [87:0] tcache_a_data;
reg  [5:0]  tcache_a_addr;
reg         tcache_a_wr;
wire [87:0] tcache_b_data;
reg  [5:0]  tcache_b_addr;

altsyncram #(
    .operation_mode("DUAL_PORT"),
    .width_a(88),
    .widthad_a(6),
    .width_b(88),
    .widthad_b(6),
    .numwords_a(48),
    .numwords_b(48),
    .clock_enable_input_a("BYPASS"),
    .clock_enable_input_b("BYPASS"),
    .clock_enable_output_b("BYPASS"),
    .outdata_reg_b("UNREGISTERED"),
    .read_during_write_mode_mixed_ports("OLD_DATA"),
    .intended_device_family("Cyclone V"),
    .lpm_type("altsyncram"),
    .power_up_uninitialized("FALSE")
) tap_cache_ram (
    .clock0(clk),
    .address_a(tcache_a_addr),
    .data_a(tcache_a_data),
    .wren_a(tcache_a_wr),
    .clock1(clk),
    .address_b(tcache_b_addr),
    .q_b(tcache_b_data),
    .wren_b(1'b0),
    .aclr0(1'b0), .aclr1(1'b0),
    .addressstall_a(1'b0), .addressstall_b(1'b0),
    .byteena_a(1'b1), .byteena_b(1'b1),
    .clocken0(1'b1), .clocken1(1'b1),
    .clocken2(1'b1), .clocken3(1'b1),
    .data_b(88'b0), .eccstatus(),
    .q_a(), .rden_a(1'b0), .rden_b(1'b1)
);

// Valid bits stay in registers (48 bits = negligible ALMs) so
// invalidation doesn't need a read-modify-write through the BRAM.
reg [47:0] cache_valid;

// Registered cache read result — latched in S_TAP_RD to break
// the BRAM-output → Add → hit_fwd critical path on clk_74a.
reg [22:0] tcache_rd_tag;
reg [63:0] tcache_rd_data;

// --- Tap fetch state ---
reg [1:0]  fetch_tap_idx;   // which tap are we fetching (0=m1,1=0,2=1,3=2)
reg [2:0]  fetch_count;     // how many taps still to fetch
reg [21:0] fetch_pos;       // resolved address for current tap
reg [21:0] last_cram_word;  // last CRAM word address fetched (for same-word opt)
reg [31:0] last_cram_data;  // last CRAM data read
reg        last_cram_valid; // is last_cram_data usable

// --- Boundary-aware tap address resolver ---
// Given a sample offset relative to cur_pos_int, resolve the actual
// sample address accounting for loop/end boundaries.
// Resolve a tap address for Hermite interpolation.  Offsets are small
// (−1 to +2), so overshoot past boundaries is at most 2 samples —
// simple wrap/reflect/clamp, no division needed.
//
// Two-stage pipeline (see S_TAP_FETCH_CALC / S_TAP_FETCH):
//   stage 1: tap_raw = cur_pos_int ± offset (24-bit signed)
//   stage 2: this function = bounds + wrap/reflect on the registered raw.
function [21:0] resolve_tap_raw;
    input signed [23:0] raw;
    input [21:0] len;
    input [21:0] loop_s, loop_e;
    input        is_loop, is_bidi;
    begin
        if (!is_loop) begin
            // One-shot: clamp to [0, len-1]
            if (raw < 0) resolve_tap_raw = 22'd0;
            else if (raw >= {2'b0, len}) resolve_tap_raw = len - 22'd1;
            else resolve_tap_raw = raw[21:0];
        end else if (!is_bidi) begin
            // Forward loop: wrap within [loop_s, loop_e).
            // Overshoot is at most 2, so one wrap suffices.
            if (raw >= {2'b0, loop_e})
                resolve_tap_raw = loop_s + (raw[21:0] - loop_e);
            else if (raw < {2'b0, loop_s})
                resolve_tap_raw = loop_e - 22'd1 - (loop_s - 22'd1 - raw[21:0]);
            else if (raw < 0) resolve_tap_raw = 22'd0;
            else resolve_tap_raw = raw[21:0];
        end else begin
            // Bidi: reflect at boundaries
            if (raw >= {2'b0, loop_e})
                resolve_tap_raw = loop_e - 22'd1 - (raw[21:0] - loop_e);
            else if (raw < {2'b0, loop_s})
                resolve_tap_raw = loop_s + (loop_s - raw[21:0]);
            else if (raw < 0) resolve_tap_raw = 22'd0;
            else resolve_tap_raw = raw[21:0];
        end
    end
endfunction

// Resolved addresses for all 4 taps
reg [21:0] tap_addrs [0:3];
// Pipeline regs: S_TAP_FETCH_CALC → S_TAP_FETCH → S_TAP_ADDR → S_TAP_CRAM
reg signed [23:0] tap_raw;       // registered raw (pos ± offset)
reg [21:0] tap_fetch_addr;      // resolved sample address
reg [21:0] tap_fetch_word;

// --- Hermite polynomial evaluation (pipelined) ---
// Catmull-Rom: result = ((a*t + b)*t + c)*t + d
// Coefficients computed from taps, evaluation via Horner in 3 stages.
reg signed [17:0] h_a, h_b, h_c, h_d;
reg signed [17:0] h_stage;     // intermediate Horner accumulator
// Pipeline regs for Hermite coefficient split (S_TAP_NEXT → 0A → 0B)
reg signed [18:0] h_p_m1, h_p_0, h_p_1, h_p_2;
reg signed [19:0] h_a_hi, h_a_lo;  // partial sums for h_a
reg signed [19:0] h_b_hi, h_b_lo;  // partial sums for h_b
reg signed [18:0] h_raw;        // pre-clamp Horner result (S_HERMITE_3 → 4)
reg signed [15:0] hermite_sample;

wire signed [16:0] h_t = $signed({1'b0, cur_pos_frac});

// Coefficient computation (done in S_TAP_NEXT after all taps loaded):
//   a = (-x_m1 + 3*x_0 - 3*x_1 + x_2) / 2
//   b = (2*x_m1 - 5*x_0 + 4*x_1 - x_2) / 2
//   c = (-x_m1 + x_1) / 2
//   d = x_0
// Use wide intermediates to avoid overflow (tap values up to ±32767,
// 5*32767 = 163835, needs 18+ bits).

// --- Log volume curve: v²/256 computed inline (cheaper than 256-entry LUT) ---
reg [7:0] log_vol_l, log_vol_r;

// Stereo volume scaling — registered in S_MULTIPLY to enable DSP inference
reg signed [15:0] scaled_l, scaled_r;

// Mix-down: attenuate to prevent clipping.
// Shift right by 2 (÷4) — headroom for 48-voice mixing.
// Combined with log volume curve (x²/256), 6 voices at vol=180 ≈ full scale.
wire signed [31:0] mix_l = accum_l >>> 2;
wire signed [31:0] mix_r = accum_r >>> 2;

// Output clamp
wire [15:0] clamp_l = (mix_l > 32'sd32767)  ? 16'h7FFF :
                      (mix_l < -32'sd32768) ? 16'h8000 : mix_l[15:0];
wire [15:0] clamp_r = (mix_r > 32'sd32767)  ? 16'h7FFF :
                      (mix_r < -32'sd32768) ? 16'h8000 : mix_r[15:0];

// Volume ramp: step one channel toward target (minimized comparators)
function [7:0] ramp_step;
    input [7:0] cur;
    input [7:0] target;
    input [7:0] step;
    reg [8:0] up, dn;  // 9-bit to detect overflow/underflow
    begin
        up = {1'b0, cur} + {1'b0, step};
        dn = {1'b0, cur} - {1'b0, step};
        if (cur < target)
            ramp_step = (up[8] || up[7:0] >= target) ? target : up[7:0];
        else if (cur > target)
            ramp_step = (dn[8] || dn[7:0] <= target) ? target : dn[7:0];
        else
            ramp_step = cur;
    end
endfunction

// BRAM read pipeline phase counter (needs 4 bits for 14 phases)
reg [3:0] bram_rd_phase;

always @(posedge clk) begin
    if (!reset_n) begin
        state <= S_IDLE;
        cur_voice <= 0;
        accum_l <= 0;
        accum_r <= 0;
        sample_wr <= 0;
        sample_data <= 0;
        cram1_rd <= 0;
        cram1_addr <= 0;
        active_cnt <= 0;
        voice_cnt <= 0;
        vtbl_a_wr <= 0;
        vtbl_a_addr <= 0;
        vtbl_a_data <= 0;
        ramp_new_l <= 0;
        ramp_new_r <= 0;
        voice_active <= 48'd0;
        cache_valid <= 48'd0;
        dir_changed <= 0;
        new_dir <= 0;
        voice_end_pending <= 48'd0;
        fetch_tap_idx <= 0;
        tap_raw <= 0;
        fetch_count <= 0;
        last_cram_valid <= 0;
        tcache_a_wr <= 0;
        tcache_a_addr <= 0;
        tcache_a_data <= 0;
        tcache_b_addr <= 0;
        tcache_rd_tag <= 0;
        tcache_rd_data <= 0;
        hermite_sample <= 16'sh0;
    end else begin
        sample_wr <= 0;
        cram1_rd <= 0;
        tcache_a_wr <= 0;
        vtbl_a_wr <= 0;   // default: port A reads; FSM write states override to 1

        // Voice-end IRQ clear (W1C)
        if (irq_clear_wr)
            voice_end_pending <= voice_end_pending & ~irq_clear;

        // Snoop CPU writes (landing on vtbl port B this cycle) so the
        // FSM's shadow state — voice_active bitmap and tap-cache valid
        // bits — stays in sync with the voice table contents.
        if (voice_wr) begin
            if (voice_field == VTBL_CTRL)
                voice_active[voice_sel] <= voice_wdata[0];
            if (voice_field == VTBL_CTRL || voice_field == VTBL_ADDR ||
                voice_field == VTBL_LOOP_START || voice_field == VTBL_LOOP_END)
                cache_valid[voice_sel] <= 1'b0;
        end

        if (!mixer_active) begin
            state <= S_IDLE;
        end else begin
            case (state)

            S_IDLE: begin
                if (fifo_level < 9'd480) begin
                    accum_l <= 0;
                    accum_r <= 0;
                    cur_voice <= 0;
                    voice_cnt <= 0;
                    vtbl_a_addr <= {6'd0, VTBL_CTRL};
                    state <= S_RD_CTRL;
                end
            end

            // S_RD_CTRL is the altsyncram latency wait: the predecessor
            // state set vtbl_a_addr one cycle ago, the internal address
            // register samples the new value at the next posedge, and
            // q_a is valid in S_RD_CTRL_W.
            S_RD_CTRL: begin
                state <= S_RD_CTRL_W;
            end

            S_RD_CTRL_W: begin
                cur_loop   <= vtbl_a_q[1];
                cur_fmt16  <= vtbl_a_q[2];
                cur_bidi   <= vtbl_a_q[3];
                cur_dir    <= vtbl_a_q[4];

                if (!voice_active[cur_voice]) begin
                    if (cur_voice == 6'd47)
                        state <= S_OUTPUT;
                    else begin
                        cur_voice <= cur_voice + 6'd1;
                        vtbl_a_addr <= {cur_voice + 6'd1, VTBL_CTRL};
                        state <= S_RD_CTRL;
                    end
                end else begin
                    vtbl_a_addr <= {cur_voice, VTBL_ADDR};
                    bram_rd_phase <= 0;
                    state <= S_RD_FIELDS;
                end
            end

            S_RD_FIELDS: begin
                state <= S_RD_FIELDS_W;
            end

            S_RD_FIELDS_W: begin
                case (bram_rd_phase)
                    4'd0: begin  // ADDR
                        cur_addr <= vtbl_a_q[21:0];
                        vtbl_a_addr <= {cur_voice, VTBL_LEN};
                        bram_rd_phase <= 1;
                        state <= S_RD_FIELDS;
                    end
                    4'd1: begin  // LEN
                        cur_len <= vtbl_a_q[21:0];
                        vtbl_a_addr <= {cur_voice, VTBL_RATE};
                        bram_rd_phase <= 2;
                        state <= S_RD_FIELDS;
                    end
                    4'd2: begin  // RATE
                        cur_rate <= vtbl_a_q;
                        vtbl_a_addr <= {cur_voice, VTBL_POS_INT};
                        bram_rd_phase <= 3;
                        state <= S_RD_FIELDS;
                    end
                    4'd3: begin  // POS_INT
                        cur_pos_int <= vtbl_a_q[21:0];
                        /* Mirror every voice's position into the per-voice
                         * latch array so pos_readback can mux it out
                         * immediately when the CPU switches voice_sel. */
                        voice_pos_latch[cur_voice] <= vtbl_a_q[21:0];
                        vtbl_a_addr <= {cur_voice, VTBL_POS_FRAC};
                        bram_rd_phase <= 4;
                        state <= S_RD_FIELDS;
                    end
                    4'd4: begin  // POS_FRAC
                        cur_pos_frac <= vtbl_a_q[15:0];
                        vtbl_a_addr <= {cur_voice, VTBL_VOL_LR};
                        bram_rd_phase <= 5;
                        state <= S_RD_FIELDS;
                    end
                    4'd5: begin  // VOL_LR
                        cur_vol_l <= vtbl_a_q[7:0];
                        cur_vol_r <= vtbl_a_q[15:8];
                        vtbl_a_addr <= {cur_voice, VTBL_LOOP_END};
                        bram_rd_phase <= 6;
                        state <= S_RD_FIELDS;
                    end
                    4'd6: begin  // LOOP_END
                        cur_loop_end <= vtbl_a_q[21:0];
                        vtbl_a_addr <= {cur_voice, VTBL_LOOP_START};
                        bram_rd_phase <= 7;
                        state <= S_RD_FIELDS;
                    end
                    4'd7: begin  // LOOP_START
                        cur_loop_start <= vtbl_a_q[21:0];
                        vtbl_a_addr <= {cur_voice, VTBL_VOL_TARGET};
                        bram_rd_phase <= 8;
                        state <= S_RD_FIELDS;
                    end
                    4'd8: begin  // VOL_TARGET
                        cur_target_l <= vtbl_a_q[7:0];
                        cur_target_r <= vtbl_a_q[15:8];
                        vtbl_a_addr <= {cur_voice, VTBL_VOL_RATE};
                        bram_rd_phase <= 9;
                        state <= S_RD_FIELDS;
                    end
                    4'd9: begin  // VOL_RATE + end-of-field check
                        cur_vol_rate <= vtbl_a_q[7:0];
                        if (cur_pos_int >= cur_len) begin
                            voice_active[cur_voice] <= 0;
                            voice_end_pending[cur_voice] <= 1;
                            if (cur_voice == 6'd47)
                                state <= S_OUTPUT;
                            else begin
                                cur_voice <= cur_voice + 6'd1;
                                vtbl_a_addr <= {cur_voice + 6'd1, VTBL_CTRL};
                                state <= S_RD_CTRL;
                            end
                        end else begin
                            // Voice still active — go straight to tap fetch
                            // (filter BRAM reads removed)
                            tcache_b_addr <= cur_voice;
                            state <= S_TAP_RD;
                        end
                    end
                    default: begin
                        tcache_b_addr <= cur_voice;
                        state <= S_TAP_RD;
                    end
                endcase
            end

            // ---- Tap cache BRAM read wait ----
            // tcache_b_addr was set in the previous state; BRAM output
            // (tcache_b_data) is valid this cycle. Register it here so
            // the hit_fwd/hit_rev comparisons in S_TAP_CHECK start from
            // FFs instead of raw BRAM output.
            S_TAP_RD: begin
                tcache_rd_tag  <= tcache_b_data[86:64];
                tcache_rd_data <= tcache_b_data[63:0];
                state <= S_TAP_CHECK;
            end

            // ---- Hermite tap cache check (reads BRAM output) ----
            S_TAP_CHECK: begin
                begin : tap_check_block
                    reg [63:0] cached;
                    reg [22:0] tag;
                    reg [21:0] last_pos;
                    reg        last_dir;
                    reg        hit_same, hit_fwd, hit_rev;
                    cached   = tcache_rd_data;
                    tag      = tcache_rd_tag;
                    last_pos = tag[21:0];
                    last_dir = tag[22];

                    hit_same = cache_valid[cur_voice] &&
                               (cur_pos_int == last_pos) &&
                               (cur_dir == last_dir);
                    hit_fwd  = cache_valid[cur_voice] &&
                               (cur_pos_int == last_pos + 22'd1) &&
                               !cur_dir && !last_dir;
                    hit_rev  = cache_valid[cur_voice] &&
                               (cur_pos_int + 22'd1 == last_pos) &&
                               cur_dir && last_dir;

                    if (hit_same) begin
                        tap_m1 <= $signed(cached[15:0]);
                        tap_0  <= $signed(cached[31:16]);
                        tap_1  <= $signed(cached[47:32]);
                        tap_2  <= $signed(cached[63:48]);
                        state <= S_TAP_NEXT;
                    end else if (hit_fwd) begin
                        tap_m1 <= $signed(cached[31:16]);
                        tap_0  <= $signed(cached[47:32]);
                        tap_1  <= $signed(cached[63:48]);
                        fetch_tap_idx <= 2'd3;
                        fetch_count <= 3'd1;
                        last_cram_valid <= 0;
                        state <= S_TAP_FETCH_CALC;
                    end else if (hit_rev) begin
                        tap_0  <= $signed(cached[15:0]);
                        tap_1  <= $signed(cached[31:16]);
                        tap_2  <= $signed(cached[47:32]);
                        fetch_tap_idx <= 2'd0;
                        fetch_count <= 3'd1;
                        last_cram_valid <= 0;
                        state <= S_TAP_FETCH_CALC;
                    end else begin
                        fetch_tap_idx <= 2'd0;
                        fetch_count <= 3'd4;
                        last_cram_valid <= 0;
                        state <= S_TAP_FETCH_CALC;
                    end
                end
            end

            // ---- Tap address stage 1: compute raw (pos ± offset) ----
            // Registered into tap_raw so the 24-bit add + Decoder
            // (offset mux) don't share a cycle with the bounds/wrap
            // logic in S_TAP_FETCH.
            S_TAP_FETCH_CALC: begin
                begin : tap_raw_block
                    reg signed [2:0] offset;
                    case (fetch_tap_idx)
                        2'd0: offset = -3'sd1;
                        2'd1: offset = 3'sd0;
                        2'd2: offset = 3'sd1;
                        2'd3: offset = 3'sd2;
                    endcase
                    if (cur_bidi && cur_dir)
                        tap_raw <= {2'b0, cur_pos_int} - {{21{offset[2]}}, offset};
                    else
                        tap_raw <= {2'b0, cur_pos_int} + {{21{offset[2]}}, offset};
                end
                state <= S_TAP_FETCH;
            end

            // ---- Tap address stage 2: bounds + wrap/reflect ----
            // Uses registered tap_raw from the previous cycle.  Result
            // is registered into tap_fetch_addr.  cram_word_addr is
            // deferred to S_TAP_ADDR (next cycle).
            S_TAP_FETCH: begin
                begin : tap_fetch_block
                    reg [21:0] addr;
                    addr = resolve_tap_raw(tap_raw, cur_len,
                        cur_loop_start, cur_loop_end, cur_loop, cur_bidi);
                    tap_fetch_addr <= addr;
                    fetch_pos <= addr;
                    tap_addrs[fetch_tap_idx] <= addr;
                end
                state <= S_TAP_ADDR;
            end

            // ---- Compute CRAM word addr (pipeline stage 2) ----
            // tap_fetch_addr is registered; cram_word_addr is one add.
            // Register the result — cache check + issue in S_TAP_WAIT reuse.
            S_TAP_ADDR: begin
                tap_fetch_word <= cram_word_addr(cur_addr, tap_fetch_addr, cur_fmt16);
                state <= S_TAP_CRAM;
            end

            S_TAP_CRAM: begin
                if (last_cram_valid && tap_fetch_word == last_cram_word) begin
                    case (fetch_tap_idx)
                        2'd0: tap_m1 <= extract_sample(last_cram_data, tap_fetch_addr, cur_fmt16);
                        2'd1: tap_0  <= extract_sample(last_cram_data, tap_fetch_addr, cur_fmt16);
                        2'd2: tap_1  <= extract_sample(last_cram_data, tap_fetch_addr, cur_fmt16);
                        2'd3: tap_2  <= extract_sample(last_cram_data, tap_fetch_addr, cur_fmt16);
                    endcase
                    state <= S_TAP_NEXT;
                end else begin
                    cram1_addr <= tap_fetch_word;
                    last_cram_word <= tap_fetch_word;
                    // Assert read request; only advance when mux confirms
                    // the pulse was actually accepted (not suppressed by
                    // a colliding CPU access).
                    if (!cram1_busy) cram1_rd <= 1;
                    if (cram1_rd_accepted) begin
                        cram1_rd <= 0;
                        state <= S_TAP_WAIT;
                    end
                end
            end

            // ---- Wait for CRAM response ----
            S_TAP_WAIT: begin
                if (cram1_rdata_valid) begin
                    last_cram_data <= cram1_rdata;
                    last_cram_valid <= 1;
                    case (fetch_tap_idx)
                        2'd0: tap_m1 <= extract_sample(cram1_rdata, fetch_pos, cur_fmt16);
                        2'd1: tap_0  <= extract_sample(cram1_rdata, fetch_pos, cur_fmt16);
                        2'd2: tap_1  <= extract_sample(cram1_rdata, fetch_pos, cur_fmt16);
                        2'd3: tap_2  <= extract_sample(cram1_rdata, fetch_pos, cur_fmt16);
                    endcase
                    state <= S_TAP_NEXT;
                end
            end

            // ---- Advance to next tap or start Hermite pipeline ----
            S_TAP_NEXT: begin
                fetch_count <= fetch_count - 3'd1;
                if (fetch_count <= 3'd1) begin
                    // All taps loaded — write cache BRAM, sign-extend into
                    // pipeline regs.  Actual coefficient arithmetic moves to
                    // S_HERMITE_0 to meet 100 MHz timing.
                    tcache_a_wr   <= 1;
                    tcache_a_addr <= cur_voice;
                    tcache_a_data <= {1'b0, {cur_dir, cur_pos_int}, tap_2, tap_1, tap_0, tap_m1};
                    cache_valid[cur_voice] <= 1'b1;

                    h_p_m1 <= {{3{tap_m1[15]}}, tap_m1};
                    h_p_0  <= {{3{tap_0[15]}},  tap_0};
                    h_p_1  <= {{3{tap_1[15]}},  tap_1};
                    h_p_2  <= {{3{tap_2[15]}},  tap_2};
                    state  <= S_HERMITE_0A;
                end else begin
                    // More taps to fetch
                    fetch_tap_idx <= fetch_tap_idx + 2'd1;
                    state <= S_TAP_FETCH_CALC;
                end
            end

            // ---- Hermite coefficient computation (2-stage pipeline) ----
            // Catmull-Rom:  a = (-m1 + 3*x0 - 3*x1 + x2)/2
            //               b = (2*m1 - 5*x0 + 4*x1 - x2)/2
            //               c = (-m1 + x1)/2     d = x0
            // Stage 0A: partial sums (~2 adder levels each)
            S_HERMITE_0A: begin
                h_a_hi <= -h_p_m1 + 3*h_p_0;    // -m1 + 3*x0
                h_a_lo <= -3*h_p_1 + h_p_2;      // -3*x1 + x2
                h_b_hi <= 2*h_p_m1 - 5*h_p_0;    // 2*m1 - 5*x0
                h_b_lo <= 4*h_p_1 - h_p_2;       // 4*x1 - x2
                h_c <= (-h_p_m1 + h_p_1) >>> 1;  // simple, fits here
                h_d <= h_p_0[17:0];
                state <= S_HERMITE_0B;
            end

            // Stage 0B: combine partial sums + shift
            S_HERMITE_0B: begin
                h_a <= (h_a_hi + h_a_lo) >>> 1;
                h_b <= (h_b_hi + h_b_lo) >>> 1;
                state <= S_HERMITE_1;
            end

            // ---- Hermite Horner evaluation: 3 pipelined stages ----
            // result = ((a*t + b)*t + c)*t + d

            S_HERMITE_1: begin
                // h_stage = a*t >> 16 + b
                begin : horner_1
                    reg signed [34:0] p;
                    p = h_a * h_t;
                    h_stage <= $signed(p[34:16]) + h_b;
                end
                state <= S_HERMITE_2;
            end

            S_HERMITE_2: begin
                // h_stage = h_stage*t >> 16 + c
                begin : horner_2
                    reg signed [34:0] p;
                    p = h_stage * h_t;
                    h_stage <= $signed(p[34:16]) + h_c;
                end
                state <= S_HERMITE_3;
            end

            S_HERMITE_3: begin
                // h_raw = h_stage*t >> 16 + d (multiply + add only)
                begin : horner_3
                    reg signed [34:0] p;
                    p = h_stage * h_t;
                    h_raw <= $signed(p[34:16]) + {{1{h_d[17]}}, h_d};
                end
                state <= S_HERMITE_4;
            end

            S_HERMITE_4: begin
                // Clamp h_raw to signed 16-bit
                hermite_sample <= (h_raw > 19'sd32767)  ? 16'sh7FFF :
                                  (h_raw < -19'sd32768) ? 16'sh8000 :
                                  h_raw[15:0];
                state <= S_SCALE;
            end


            // ---- Pipeline stage 1: log volume curve v²/256 ----
            S_SCALE: begin
                begin : log_vol_calc
                    reg [15:0] sq_l, sq_r;
                    sq_l = cur_vol_l * cur_vol_l;
                    sq_r = cur_vol_r * cur_vol_r;
                    log_vol_l <= sq_l[15:8];
                    log_vol_r <= sq_r[15:8];
                end
                state <= S_MULTIPLY;
            end

            // ---- Pipeline stage 2: volume multiply (DSP-inferred) ----
            S_MULTIPLY: begin
                begin : vol_mul
                    reg signed [23:0] pl, pr;
                    pl = hermite_sample * $signed({1'b0, log_vol_l});
                    pr = hermite_sample * $signed({1'b0, log_vol_r});
                    scaled_l <= pl[23:8];
                    scaled_r <= pr[23:8];
                end
                state <= S_ACCUM;
            end

            // ---- Pipeline stage 3: accumulate ----
            S_ACCUM: begin
                accum_l <= accum_l + {{16{scaled_l[15]}}, scaled_l};
                accum_r <= accum_r + {{16{scaled_r[15]}}, scaled_r};
                voice_cnt <= voice_cnt + 1;
                dir_changed <= 0;
                state <= S_VOL_RAMP;
            end

            // ---- Volume ramp stage 1: compute stepped values ----
            // Registered into ramp_new_l/r so the ramp_step comparator
            // chain doesn't feed directly into the 32-bit vtbl_a_data
            // write mux (that combined path is the 100 MHz Fmax offender).
            S_VOL_RAMP: begin
                if (cur_vol_rate == 8'd0) begin
                    // Instant: snap to target
                    ramp_new_l <= cur_target_l;
                    ramp_new_r <= cur_target_r;
                end else begin
                    // Ramp: one step toward target
                    ramp_new_l <= ramp_step(cur_vol_l, cur_target_l, cur_vol_rate);
                    ramp_new_r <= ramp_step(cur_vol_r, cur_target_r, cur_vol_rate);
                end
                state <= S_VOL_RAMP_WR;
            end

            // ---- Volume ramp stage 2: write back if changed ----
            // Compare is now register-to-register (short path) and the
            // write mux sees only the already-registered ramp_new_*.
            S_VOL_RAMP_WR: begin
                if (ramp_new_l != cur_vol_l || ramp_new_r != cur_vol_r) begin
                    vtbl_a_wr <= 1;
                    vtbl_a_addr <= {cur_voice, VTBL_VOL_LR};
                    vtbl_a_data <= {16'd0, ramp_new_r, ramp_new_l};
                end
                state <= S_WR_POS;
            end

            // ---- Write back new position ----
            S_WR_POS: begin
                vtbl_a_wr <= 1;
                vtbl_a_addr <= {cur_voice, VTBL_POS_INT};
                pos_wrapped <= 0;

                if (cur_loop && cur_bidi) begin
                    if (!cur_dir && new_pos_int >= cur_loop_end) begin
                        vtbl_a_data <= {10'd0, cur_loop_end - 22'd1};
                        new_dir <= 1;
                        dir_changed <= 1;
                        pos_wrapped <= 1;
                    end else if (cur_dir && (rev_underflow || new_pos_int < cur_loop_start)) begin
                        vtbl_a_data <= {10'd0, cur_loop_start};
                        new_dir <= 0;
                        dir_changed <= 1;
                        pos_wrapped <= 1;
                    end else begin
                        vtbl_a_data <= {10'd0, new_pos_int};
                    end
                end else if (cur_loop && !cur_bidi) begin
                    if (new_pos_int >= cur_loop_end) begin
                        vtbl_a_data <= {10'd0, cur_loop_start + (new_pos_int - cur_loop_end)};
                        pos_wrapped <= 1;
                    end else
                        vtbl_a_data <= {10'd0, new_pos_int};
                end else begin
                    if (new_pos_int >= cur_len) begin
                        voice_active[cur_voice] <= 0;
                        voice_end_pending[cur_voice] <= 1;
                        pos_wrapped <= 1;
                    end
                    vtbl_a_data <= {10'd0, new_pos_int};
                end

                state <= S_WR_FRAC;
            end

            S_WR_FRAC: begin
                vtbl_a_addr <= {cur_voice, VTBL_POS_FRAC};

                if (pos_wrapped) begin
                    vtbl_a_wr <= cur_loop;  // write frac if looping, skip if ended
                    // Preserve fractional position for forward loops (overshoot
                    // carried over in S_WR_POS). Bidi wraps still clamp POS_INT
                    // to the boundary, so reset frac to avoid mismatched state.
                    vtbl_a_data <= (cur_loop && !cur_bidi) ? {16'd0, new_pos_frac} : 32'd0;
                end else begin
                    vtbl_a_wr <= 1;
                    vtbl_a_data <= {16'd0, new_pos_frac};
                end

                if (dir_changed)
                    state <= S_WR_DIR;
                else if (cur_voice == 6'd47)
                    state <= S_OUTPUT;
                else begin
                    cur_voice <= cur_voice + 6'd1;
                    state <= S_NEXT_VOICE;
                end
            end

            S_WR_DIR: begin
                vtbl_a_wr <= 1;
                vtbl_a_addr <= {cur_voice, VTBL_CTRL};
                vtbl_a_data <= {27'd0, new_dir, cur_bidi, cur_fmt16, cur_loop, 1'b1};

                if (cur_voice == 6'd47)
                    state <= S_OUTPUT;
                else begin
                    cur_voice <= cur_voice + 6'd1;
                    state <= S_NEXT_VOICE;
                end
            end

            // Predecessor wrote port A and incremented cur_voice.  Set
            // the read address here so altsyncram's 2-cycle latency lines
            // up with S_RD_CTRL_W.
            S_NEXT_VOICE: begin
                vtbl_a_addr <= {cur_voice, VTBL_CTRL};
                state <= S_RD_CTRL;
            end

            S_OUTPUT: begin
                sample_wr <= 1;
                sample_data <= {clamp_l, clamp_r};  // {Left[15:0], Right[15:0]}
                active_cnt <= voice_cnt;
                state <= S_IDLE;
            end

            default: state <= S_IDLE;
            endcase
        end
    end
end

endmodule
