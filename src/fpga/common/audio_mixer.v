//
// Hardware PCM Mixer — 32 voices from CRAM1
//
// Reads signed mono samples (16-bit or 8-bit) from CRAM1, resamples via
// 2-tap linear interpolation with 16.16 fixed-point positioning,
// mixes with per-voice stereo 8-bit volume (log curve v²/256), and writes
// packed stereo pairs to the audio FIFO for I2S output.
//
// Runs on clk_cpu (100 MHz).  CRAM1 access shared with CPU via a 2-way
// mux (CPU > mixer priority).  Bridge has its own controller on clk_74a.
//
// Voice descriptors stored in dual-port BRAM (3 M10K blocks).
// CPU writes via indirect register interface (select voice, write field).
// Mixer FSM reads all 32 voices sequentially each sample period.
//
// Per-voice features:
//   - 2-tap linear sample interpolation (cheap, ALM-budget-friendly;
//     cubic Hermite removed in Phase 8 ALM hunt)
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

    // CRAM1 burst-read interface (shared with CPU via arbiter).
    // Mixer uses per-voice 8-word prefetch: cache miss → burst_rd
    // pulse, controller streams 8 words back via burst_q + burst_q_valid,
    // mixer writes them into a per-voice cache RAM.  Sustained voices
    // hit the cache for ~16 sample periods between refills.
    output reg         cram1_burst_rd,
    output reg  [21:0] cram1_burst_addr,
    output reg  [4:0]  cram1_burst_len,    // = 5'd7 → 8 words
    input  wire [31:0] cram1_burst_q,
    input  wire        cram1_burst_q_valid,
    input  wire        cram1_burst_busy,
    input  wire        cram1_busy,         // global "any controller traffic"

    // Audio FIFO interface
    output reg         sample_wr,
    output reg  [31:0] sample_data,
    input  wire [9:0]  fifo_level,

    // Status
    output wire [5:0]  active_count,

    // Position read-back — mux over a per-voice latched array so a
    // CPU SEL switch returns the selected voice's latest position
    // immediately, rather than waiting up to one mixer cycle (~20us)
    // for the FSM to re-scan that voice.
    output wire [21:0] pos_readback,

    // Voice-end IRQ
    input  wire [31:0] irq_clear,     // W1C from CPU
    input  wire        irq_clear_wr,
    output reg  [31:0] voice_end_pending,
    output wire        voice_end_irq,

    // Phase 6a — reverb bus controls.  Global wet/feedback level
    // (0..255 each).  Default zeros = bypass; CPU wires them via new
    // AWE MMIO (AWE_REVERB_LEVEL / AWE_REVERB_FEEDBACK).
    input  wire [7:0] reverb_wet_level,
    input  wire [7:0] reverb_feedback,
    // Phase 6b — chorus bus controls.  level = wet/dry mix (0..255),
    // rate = LFO phase increment per sample (Q16, 0 = no modulation),
    // depth = peak swing of the read-pointer offset in samples.
    input  wire [7:0] chorus_wet_level,
    input  wire [15:0] chorus_lfo_rate,
    input  wire [7:0] chorus_lfo_depth,
    // Phase 6b complete: per-voice send levels.  When voice_field
    // == 13 (REVERB_SEND) / 14 (CHORUS_SEND), voice_wdata[7:0] lands
    // here.  Default 0xFF at reset == "full send" so existing apps
    // that don't drive these still hear reverb/chorus.

    // CRAM1 inhibit — when asserted, the tap-fetch path stops issuing
    // new cram1_rd pulses.  Already-fetched Hermite taps continue to
    // feed the output so voices don't glitch (4 × 16-bit taps per
    // voice ≈ 80 µs of headroom at 48 kHz).  The kernel wraps its
    // bridge→CRAM1→SDRAM file-I/O bounces with this bit to avoid the
    // mixer/CPU arbiter race documented for the Doom port.
    input  wire       cram1_inhibit
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
    .widthad_a(9),
    .width_b(32),
    .widthad_b(9),
    .numwords_a(512),
    .numwords_b(512),
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

reg [31:0] voice_active;

// Per-voice "CPU wrote POS_INT" latch.  When CPU calls
// of_mixer_set_position(v, n), it writes VTBL_POS_INT via port B.
// The FSM processes voices in a loop, reading POS_INT early and
// writing an advanced POS_INT back at the end.  If the CPU write
// occurs after the FSM has already read POS_INT for that voice,
// the FSM's port-A write at S_WR_POS clobbers the CPU's seek and
// the voice keeps playing from its old position.
//
// Fix: set pos_wr_pending[v] on every CPU write to POS_INT; the
// FSM's S_WR_POS then skips its write-back for that voice on the
// current pass, letting the CPU's new value stand.  The flag
// clears when the FSM reaches S_WR_POS for that voice, so the next
// pass advances from the freshly-seeked position normally.
reg [31:0] pos_wr_pending;

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
// Phase 6a: two states pipeline the reverb pre-output path.
//   S_REVERB  — q_b just valid; register the 24-bit multiply outputs
//               (wet = tap × wet_level, fb = tap × feedback).
//   S_REVERB2 — shift the registered products and form wet_reg + the
//               clamped new-tap.  S_OUTPUT only has to do a 16-bit
//               add + clamp against mix_l/r.
// State reg was widened to 6 bits to make room; the existing 32
// states below are untouched.
localparam S_REVERB      = 6'd32;
localparam S_REVERB2     = 6'd33;
// Voice field read (BRAM pipeline):
localparam S_RD_CTRL     = 5'd4;
localparam S_RD_CTRL_W   = 5'd5;
localparam S_RD_FIELDS   = 5'd6;
localparam S_RD_FIELDS_W = 5'd7;
// Tap fetch (2-tap linear: resolve addr → compute word → CRAM read):
//   CALC : raw = cur_pos_int ± offset (registered into tap_raw)
//   FETCH: bounds-check + wrap/reflect → tap_fetch_addr
localparam S_TAP_FETCH_CALC = 5'd31;
localparam S_TAP_FETCH     = 5'd10;
localparam S_TAP_ADDR      = 5'd11;
localparam S_TAP_CRAM      = 5'd12;
localparam S_TAP_WAIT      = 5'd13;
localparam S_TAP_NEXT      = 5'd14;
localparam S_PF_WAIT       = 5'd9;   // burst prefetch: wait for 8 burst_q_valid pulses
// Linear interpolation (3-stage pipeline): result = tap_0 + (tap_1 - tap_0) * frac
//   S_LERP1: lerp_diff      <= tap_1 - tap_0  (17-bit subtract registered)
//   S_LERP2: lerp_diff_prod <= lerp_diff * cur_pos_frac  (DSP-mapped multiply)
//   S_LERP3: interp_sample  <= tap_0 + lerp_diff_prod[31:16]  (16-bit add)
// Three stages keep each cycle FF→logic→FF short enough for 100 MHz —
// the 2-stage variant put subtract+multiply in one cycle and was the
// post-hunt -5 ns critical path on clk_cpu.
localparam S_LERP1         = 5'd15;
localparam S_LERP2         = 5'd16;
localparam S_LERP3         = 5'd17;
// Slots 5'd8, 5'd9, 5'd18–5'd20 freed by the cubic-Hermite removal —
// available for future stages without widening the state register.
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

reg [5:0]  state;  // widened from 5-bit for Phase 6a reverb states
reg [4:0]  cur_voice;
reg signed [31:0] accum_l, accum_r;
reg [5:0]  voice_cnt;
reg [4:0]  active_cnt;
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
// reads a voice's POS_INT (once per mixer cycle).  CPU reads the
// selected voice's latest sample position via voice_sel_rd → pos_readback.
//
// Backed by an M10K (DUAL_PORT, sync-read on port B) instead of a
// 32×22 LUT-RAM array.  Trades the 32:1 mux fanout (~150 ALMs) for
// one cycle of read latency on pos_readback, which the CPU's MMIO
// path already absorbs.  Port A: FSM mirror writes; port B: CPU read.
reg  [4:0]  pos_latch_wr_addr;
reg  [21:0] pos_latch_wr_data;
reg         pos_latch_wr_en;

altsyncram #(
    .operation_mode("DUAL_PORT"),
    .width_a(22), .widthad_a(5), .numwords_a(32),
    .width_b(22), .widthad_b(5), .numwords_b(32),
    .outdata_reg_a("UNREGISTERED"), .outdata_reg_b("UNREGISTERED"),
    .address_reg_b("CLOCK0"),
    .read_during_write_mode_mixed_ports("OLD_DATA"),
    .rdcontrol_reg_b("CLOCK0"),
    .intended_device_family("Cyclone V"), .lpm_type("altsyncram"),
    .clock_enable_input_a("BYPASS"),  .clock_enable_input_b("BYPASS"),
    .clock_enable_output_a("BYPASS"), .clock_enable_output_b("BYPASS"),
    .power_up_uninitialized("FALSE")
) voice_pos_latch_ram (
    .clock0(clk),
    .address_a(pos_latch_wr_addr),
    .data_a(pos_latch_wr_data),
    .wren_a(pos_latch_wr_en),
    .address_b(voice_sel_rd[4:0]),
    .q_b(pos_readback),
    .clocken0(1'b1)
);

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

// --- 2 linear-interp taps ---
// tap_0 = sample at cur_pos_int, tap_1 = sample at next position
// (cur_pos_int + 1 forward, cur_pos_int - 1 in bidi reverse).
reg signed [15:0] tap_0, tap_1;

// --- Tap fetch state (linear: 2 taps per voice) ---
reg        fetch_tap_idx;   // which tap are we fetching (0 or 1)
reg [1:0]  fetch_count;     // how many taps still to fetch (1 or 2)
reg [21:0] fetch_pos;       // resolved address for current tap

// --- Per-voice 8-word prefetch cache --------------------------------
//
// 32 voices × 8 words × 32 bits = 8 Kb = 1 M10K (BIDIR_DUAL_PORT
// inferred via the standard altsyncram pattern).  Address layout:
// {voice[4:0], idx[2:0]}.  Port A: prefetch FSM writes 8 consecutive
// words on each refill.  Port B: tap-fetch reads on demand.
//
// Per-voice metadata, packed for compactness:
//   cache_base   — 32×22-bit MLAB.  Asynchronous read keeps the
//                  hit/miss check + cache_b_addr generation
//                  combinational from cur_voice, so the M10K read
//                  for a hit kicks off in S_TAP_ADDR (via the
//                  combinational cache_b_addr below) and lands at
//                  cache_b_q one cycle later for S_TAP_WAIT.
//   cache_valid  — single 32-bit bitmap.  cheaper than a 32-entry
//                  reg array and lets bit-set/clear stay one-hot.
//
// Invalidated on voice activation (note-on edge) so a re-used slot
// can't read stale samples from the previous tenant.
//
// Cache hit = cache_valid_bm[v] && tap_word[21:3] == cache_base[v][21:3].
// (Windows are 8-word aligned, see comment at cur_cache_base_hi.)
// Miss kicks a burst_rd at the aligned address; controller streams 8
// words back over ~16 cycles via burst_q_valid pulses.
(* ramstyle = "MLAB" *) reg [21:0] cache_base [0:31];
reg [31:0] cache_valid_bm;

(* ramstyle = "M10K" *) reg [31:0] mix_cache [0:255];
reg [31:0] cache_b_q;

/* Port A write: combinationally driven by the burst stream — every
 * cram1_burst_q_valid pulse writes one word into the per-voice cache
 * window at addr {pf_voice, pf_idx}.  pf_idx advances per pulse in
 * the FSM's S_PF_WAIT case so consecutive pulses fill consecutive
 * cache slots. */
wire [7:0]  cache_a_addr = {pf_voice, pf_idx};
wire [31:0] cache_a_data = cram1_burst_q;
wire        cache_a_we   = cram1_burst_q_valid;

/* Combinational lookup — the SAME cycle that tap_fetch_word becomes
 * valid (S_TAP_ADDR's posedge end), the cache window's start (cache_base
 * via MLAB async read) is also visible, so cache_b_addr settles within
 * the cycle.  M10K port-B reads the addr presented at the posedge,
 * data lands in cache_b_q at the NEXT posedge — perfectly aligned with
 * the FSM transitioning S_TAP_CRAM (cycle X) → S_TAP_WAIT (cycle X+1).
 *
 * Timing trick: cache windows are 8-word aligned (cache_base[2:0] == 0
 * by construction in the miss handler), so the hit check reduces to a
 * 19-bit equality and cache_off3 is just tap_fetch_word[2:0].  Without
 * the alignment the path included a 22-bit (tap - base) subtractor +
 * comparator that pushed the cur_voice → cache_base critical path past
 * 12 ns.  Pre-aligning the burst start may fetch up to 7 words before
 * the actual tap; PSRAM bursts are 8 cycles either way so it costs
 * nothing in CRAM bandwidth. */
wire [18:0] cur_cache_base_hi = cache_base[cur_voice][21:3];
wire        cache_hit         = cache_valid_bm[cur_voice]
                              && (tap_fetch_word[21:3] == cur_cache_base_hi);
wire [2:0]  cache_off3        = tap_fetch_word[2:0];
wire [7:0]  cache_b_addr      = {cur_voice, cache_off3};

always @(posedge clk) begin
    if (cache_a_we) mix_cache[cache_a_addr] <= cache_a_data;
    cache_b_q <= mix_cache[cache_b_addr];
end

// Prefetch FSM working regs.
reg [4:0]  pf_voice;          // voice the prefetch is for
reg [2:0]  pf_idx;            // 0..7 word index within burst
reg [21:0] pf_base_word;      // burst base address (= cache_base[pf_voice])

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

// Pipeline regs: S_TAP_FETCH_CALC → S_TAP_FETCH → S_TAP_ADDR → S_TAP_CRAM
reg signed [23:0] tap_raw;       // registered raw (pos ± offset)
reg [21:0] tap_fetch_addr;       // resolved sample address
reg [21:0] tap_fetch_word;

// --- Linear interp pipeline ---
// result = tap_0 + ((tap_1 - tap_0) * frac) >> 16
//   S_LERP1: lerp_diff      <= tap_1 - tap_0  (s17)
//   S_LERP2: lerp_diff_prod <= lerp_diff * cur_pos_frac  (s17 × u16 → s33, DSP)
//   S_LERP3: interp_sample  <= tap_0 + lerp_diff_prod[31:16]
// Linear output stays bounded by [tap_0, tap_1] so no clamp needed.
reg signed [16:0] lerp_diff;
reg signed [32:0] lerp_diff_prod;
reg signed [15:0] interp_sample;

// --- Log volume curve: v²/256 computed inline (cheaper than 256-entry LUT) ---
reg [7:0] log_vol_l, log_vol_r;

// Stereo volume scaling — registered in S_MULTIPLY to enable DSP inference
reg signed [15:0] scaled_l, scaled_r;

// Mix-down: attenuate to prevent clipping.
// Shift right by 2 (÷4) — headroom for 48-voice mixing.
// Combined with log volume curve (x²/256), 6 voices at vol=180 ≈ full scale.
wire signed [31:0] mix_l = accum_l >>> 2;
wire signed [31:0] mix_r = accum_r >>> 2;

// ================================================================
// Phase 6a — global reverb delay bus
// ================================================================
// 1024-sample × 16-bit mono delay line backed by altsyncram (≈ 21 ms
// at 48 kHz, 2 M10K blocks).  Each sample output reads the oldest
// tap, writes a new tap = mono(mix) + feedback × tap_old, then
// mixes tap_old back into both output channels at reverb_wet_level.
// DUAL_PORT: port A writes, port B reads; a one-cycle head state
// (S_REVERB) captures the port-B read before S_OUTPUT consumes it.
reg  [9:0]          rev_w_ptr;
reg  signed [15:0]  rev_tap;              // latched old tap (port B q)
reg  [9:0]          rev_rd_addr;
reg  [9:0]          rev_wr_addr;
reg  signed [15:0]  rev_wr_data;
reg                 rev_wr_en;
wire signed [15:0]  rev_q;

// Phase 6a pipeline registers — split the 16×8 multiply from the
// add+clamp so each cycle stays within 10 ns at 100 MHz.
//   S_REVERB  : rev_*_prod_reg <= rev_q × coefficient
//   S_REVERB2 : rev_wet_reg / rev_new_reg <= shifted + clamped product
//   S_OUTPUT  : sample_data <= clamp(mix_l/r + rev_wet_reg)
reg  signed [23:0]  rev_wet_prod_reg;
reg  signed [23:0]  rev_fb_prod_reg;
reg  signed [15:0]  rev_mono_in_reg;
reg  signed [15:0]  rev_wet_reg;
reg  signed [15:0]  rev_new_reg;

altsyncram #(
    .operation_mode("DUAL_PORT"),
    .width_a(16), .widthad_a(10), .numwords_a(1024),
    .width_b(16), .widthad_b(10), .numwords_b(1024),
    .outdata_reg_a("UNREGISTERED"),
    .outdata_reg_b("UNREGISTERED"),
    .address_reg_b("CLOCK0"),
    .read_during_write_mode_mixed_ports("OLD_DATA"),
    .rdcontrol_reg_b("CLOCK0"),
    .intended_device_family("Cyclone V"),
    .lpm_type("altsyncram"),
    .clock_enable_input_a("BYPASS"),
    .clock_enable_input_b("BYPASS"),
    .clock_enable_output_a("BYPASS"),
    .clock_enable_output_b("BYPASS"),
    .power_up_uninitialized("FALSE")
) reverb_delay (
    .clock0(clk),
    // Port A: writes (DUAL_PORT convention).
    .address_a(rev_wr_addr),
    .data_a(rev_wr_data),
    .wren_a(rev_wr_en),
    // Port B: reads — sync address register feeds q_b next cycle.
    .address_b(rev_rd_addr),
    .q_b(rev_q),
    .clocken0(1'b1)
);

// Per-voice-send accumulators feed the delay-line inputs (Phase 6b
// complete).  Shift down to compensate for 32-voice headroom (~÷4)
// and clamp to 16-bit before writing.
wire signed [31:0] rev_mono_in_s32 = reverb_in_acc >>> 2;
wire signed [15:0] rev_mono_in     =
    (rev_mono_in_s32 > 32'sd32767)  ? 16'h7FFF :
    (rev_mono_in_s32 < -32'sd32768) ? 16'h8000 : rev_mono_in_s32[15:0];

wire signed [31:0] chor_in_s32 = chorus_in_acc >>> 2;
wire signed [15:0] chor_in     =
    (chor_in_s32 > 32'sd32767)  ? 16'h7FFF :
    (chor_in_s32 < -32'sd32768) ? 16'h8000 : chor_in_s32[15:0];

// S_REVERB latches rev_q into rev_tap AND registers the derived wet
// and new-tap values.  The combinational pipe reads rev_q directly
// (fresh from altsyncram) so no nonblocking-assign hazard.
wire signed [23:0] rev_fb_prod   = rev_q * $signed({1'b0, reverb_feedback});
wire signed [15:0] rev_fb_scaled = rev_fb_prod[23:8];
wire signed [16:0] rev_new_s17   = rev_mono_in + rev_fb_scaled;
wire signed [15:0] rev_new_clp   =
    (rev_new_s17 >  17'sd32767)  ? 16'h7FFF :
    (rev_new_s17 < -17'sd32768)  ? 16'h8000 : rev_new_s17[15:0];

wire signed [23:0] rev_wet_prod  = rev_q * $signed({1'b0, reverb_wet_level});
wire signed [15:0] rev_wet       = rev_wet_prod[23:8];

// ================================================================
// Phase 6b — global chorus delay bus
// ================================================================
// 512-sample × 16-bit mono delay (~10.6 ms at 48 kHz, 1 M10K).  An
// LFO-modulated read pointer wobbles inside ±chorus_lfo_depth
// samples around the current write position, producing the classic
// chorus "shimmer".  No feedback loop — chorus shouldn't self-
// oscillate.  Same DUAL_PORT pattern as reverb_delay.
reg  [8:0]          chor_w_ptr;
reg  signed [15:0]  chor_tap;
reg  [8:0]          chor_rd_addr;
reg  [8:0]          chor_wr_addr;
reg  signed [15:0]  chor_wr_data;
reg                 chor_wr_en;
wire signed [15:0]  chor_q;

// LFO state — 16-bit phase accumulator, increment per sample.  Top
// bit picks the direction; the rest forms a triangle wave that
// scales chorus_lfo_depth.
reg  [15:0]         chor_lfo_phase;

// Triangle: phase[15] = 0 → ramp up (+),  phase[15] = 1 → ramp down.
// Use signed cast on the absolute value; max swing = ±127 samples.
wire signed [15:0]  chor_lfo_swing =
    chor_lfo_phase[15] ? -$signed({1'b0, chor_lfo_phase[14:7]})
                       :  $signed({1'b0, chor_lfo_phase[14:7]});
wire signed [15:0]  chor_offset_samples =
    (chor_lfo_swing * $signed({1'b0, chorus_lfo_depth})) >>> 7;
// Read pointer = write pointer minus delay center plus modulated swing.
// Delay center = numwords/2 = 256 to keep room for ± modulation.
wire [8:0]          chor_rd_ptr_next =
    chor_w_ptr - 9'd256 + chor_offset_samples[8:0];

altsyncram #(
    .operation_mode("DUAL_PORT"),
    .width_a(16), .widthad_a(9), .numwords_a(512),
    .width_b(16), .widthad_b(9), .numwords_b(512),
    .outdata_reg_a("UNREGISTERED"),
    .outdata_reg_b("UNREGISTERED"),
    .address_reg_b("CLOCK0"),
    .read_during_write_mode_mixed_ports("OLD_DATA"),
    .rdcontrol_reg_b("CLOCK0"),
    .intended_device_family("Cyclone V"),
    .lpm_type("altsyncram"),
    .clock_enable_input_a("BYPASS"),
    .clock_enable_input_b("BYPASS"),
    .clock_enable_output_a("BYPASS"),
    .clock_enable_output_b("BYPASS"),
    .power_up_uninitialized("FALSE")
) chorus_delay (
    .clock0(clk),
    .address_a(chor_wr_addr),
    .data_a(chor_wr_data),
    .wren_a(chor_wr_en),
    .address_b(chor_rd_addr),
    .q_b(chor_q),
    .clocken0(1'b1)
);

// Chorus has no feedback — write data is just the dry mono input.
wire signed [23:0] chor_wet_prod = chor_q * $signed({1'b0, chorus_wet_level});
wire signed [15:0] chor_wet      = chor_wet_prod[23:8];

reg  signed [23:0]  chor_wet_prod_reg;   // S_REVERB stage A captures
reg  signed [15:0]  chor_wet_reg;        // S_REVERB2 stage B applies

// =====================================================================
// Per-voice send levels (Phase 6b complete)
// =====================================================================
// SF2 reverb_send / chorus_send per voice — drives how much of each
// voice's sample contributes to the wet bus that feeds the delay
// lines.  Default 0xFF at reset means existing apps that don't set
// these still get full reverb/chorus contribution from every voice.
//
// AWE's SEND_COMPOSE stage (or app code) writes them via mixer
// fields 13 (REVERB_SEND) and 14 (CHORUS_SEND).
reg [7:0]  voice_reverb_send [0:31];
reg [7:0]  voice_chorus_send [0:31];

// Wet-bus accumulators — running sum per sample of voice_sample ×
// voice_send during the S_ACCUM/S_VOL_RAMP/S_VOL_RAMP_WR scan.
// The full chain (sample_sum → mult → shift → add → acc) is too long
// for one cycle at 100 MHz, so it's split:
//   S_ACCUM        : mono_voice_reg <= scaled_l + scaled_r;
//                    captured_send  <= voice_send[cur_voice]
//   S_VOL_RAMP     : prod_reg <= mono_voice_reg × captured_send
//   S_VOL_RAMP_WR  : acc <= acc + prod_reg[24:8]    (>>> 8)
reg signed [31:0]  reverb_in_acc;
reg signed [31:0]  chorus_in_acc;
reg signed [16:0]  mono_voice_reg;       // scaled_l + scaled_r (s17)
reg [7:0]          voice_rev_send_reg;
reg [7:0]          voice_chr_send_reg;
reg signed [24:0]  reverb_prod_reg;      // s17 × u8 → s25
reg signed [24:0]  chorus_prod_reg;

integer pv_init;
integer i;             // generic loop var for cache reset / invalidation
initial begin
    for (pv_init = 0; pv_init < 32; pv_init = pv_init + 1) begin
        voice_reverb_send[pv_init] = 8'hFF;
        voice_chorus_send[pv_init] = 8'hFF;
    end
end

// Output clamp — combine dry + reverb wet + chorus wet, clamp.
wire signed [31:0] out_l_s32 = mix_l + rev_wet_reg + chor_wet_reg;
wire signed [31:0] out_r_s32 = mix_r + rev_wet_reg + chor_wet_reg;
wire [15:0] clamp_l = (out_l_s32 > 32'sd32767)  ? 16'h7FFF :
                      (out_l_s32 < -32'sd32768) ? 16'h8000 : out_l_s32[15:0];
wire [15:0] clamp_r = (out_r_s32 > 32'sd32767)  ? 16'h7FFF :
                      (out_r_s32 < -32'sd32768) ? 16'h8000 : out_r_s32[15:0];

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
        cram1_burst_rd   <= 0;
        cram1_burst_addr <= 0;
        cram1_burst_len  <= 0;
        pf_voice  <= 5'd0;
        pf_idx    <= 3'd0;
        pf_base_word <= 22'd0;
        active_cnt <= 0;
        voice_cnt <= 0;
        vtbl_a_wr <= 0;
        vtbl_a_addr <= 0;
        vtbl_a_data <= 0;
        ramp_new_l <= 0;
        ramp_new_r <= 0;
        voice_active <= 32'd0;
        pos_wr_pending <= 32'd0;
        dir_changed <= 0;
        new_dir <= 0;
        voice_end_pending <= 32'd0;
        rev_w_ptr   <= 10'd0;
        rev_rd_addr <= 10'd0;
        rev_wr_addr <= 10'd0;
        rev_wr_data <= 16'd0;
        rev_wr_en   <= 1'b0;
        rev_tap          <= 16'd0;
        rev_wet_prod_reg <= 24'd0;
        rev_fb_prod_reg  <= 24'd0;
        rev_mono_in_reg  <= 16'd0;
        rev_wet_reg      <= 16'd0;
        rev_new_reg      <= 16'd0;
        chor_w_ptr       <= 9'd0;
        chor_rd_addr     <= 9'd0;
        chor_wr_addr     <= 9'd0;
        chor_wr_data     <= 16'd0;
        chor_wr_en       <= 1'b0;
        chor_tap         <= 16'd0;
        chor_lfo_phase   <= 16'd0;
        chor_wet_prod_reg<= 24'd0;
        chor_wet_reg     <= 16'd0;
        reverb_in_acc    <= 32'd0;
        chorus_in_acc    <= 32'd0;
        mono_voice_reg     <= 17'sd0;
        voice_rev_send_reg <= 8'd0;
        voice_chr_send_reg <= 8'd0;
        reverb_prod_reg    <= 25'sd0;
        chorus_prod_reg    <= 25'sd0;
        fetch_tap_idx <= 0;
        tap_raw <= 0;
        fetch_count <= 0;
        cache_valid_bm <= 32'd0;
        for (i = 0; i < 32; i = i + 1)
            cache_base[i]  <= 22'd0;
        lerp_diff      <= 17'sd0;
        lerp_diff_prod <= 33'sd0;
        interp_sample  <= 16'sh0;
        pos_latch_wr_en   <= 1'b0;
        pos_latch_wr_addr <= 5'd0;
        pos_latch_wr_data <= 22'd0;
    end else begin
        sample_wr <= 0;
        cram1_burst_rd <= 0;       // single-cycle pulse — overridden in S_TAP_CRAM miss
        vtbl_a_wr <= 0;   // default: port A reads; FSM write states override to 1
        pos_latch_wr_en <= 1'b0;  // 1-cycle write strobe; FSM overrides
        rev_wr_en  <= 0;   // delay-line write pulses only in S_OUTPUT
        chor_wr_en <= 0;

        // Voice-end IRQ clear (W1C)
        if (irq_clear_wr)
            voice_end_pending <= voice_end_pending & ~irq_clear;

        // Snoop CPU writes (landing on vtbl port B this cycle) so the
        // FSM's shadow state — voice_active bitmap and tap-cache valid
        // bits — stays in sync with the voice table contents.
        if (voice_wr) begin
            if (voice_field == VTBL_CTRL) begin
                /* Cache invalidate on note-on edge (rising active bit).
                 * Without this, a re-used voice slot would read whatever
                 * the previous tenant left in mix_cache → wrong samples
                 * for the first ~16 output ticks until the cache base
                 * comparison forced a refill. */
                if (voice_wdata[0] && !voice_active[voice_sel])
                    cache_valid_bm[voice_sel] <= 1'b0;
                voice_active[voice_sel] <= voice_wdata[0];
            end
            if (voice_field == VTBL_POS_INT) begin
                /* CPU repositioning a voice (loop-jump, scrub) — drop
                 * the cache so we re-fetch from the new pos. */
                cache_valid_bm[voice_sel] <= 1'b0;
                pos_wr_pending[voice_sel] <= 1'b1;
            end
            if (voice_field == 4'd13)
                voice_reverb_send[voice_sel] <= voice_wdata[7:0];
            if (voice_field == 4'd14)
                voice_chorus_send[voice_sel] <= voice_wdata[7:0];
        end

        if (!mixer_active) begin
            state <= S_IDLE;
        end else begin
            case (state)

            S_IDLE: begin
                if (fifo_level < 10'd960) begin
                    accum_l <= 0;
                    accum_r <= 0;
                    reverb_in_acc <= 0;
                    chorus_in_acc <= 0;
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
                    if (cur_voice == 5'd31)
                        state <= S_REVERB;
                    else begin
                        cur_voice <= cur_voice + 5'd1;
                        vtbl_a_addr <= {cur_voice + 5'd1, VTBL_CTRL};
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
                        /* Mirror every voice's position into the M10K
                         * latch so the CPU's pos_readback returns the
                         * latest sample position when SEL changes. */
                        pos_latch_wr_addr <= cur_voice;
                        pos_latch_wr_data <= vtbl_a_q[21:0];
                        pos_latch_wr_en   <= 1'b1;
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
                            if (cur_voice == 5'd31)
                                state <= S_REVERB;
                            else begin
                                cur_voice <= cur_voice + 5'd1;
                                vtbl_a_addr <= {cur_voice + 5'd1, VTBL_CTRL};
                                state <= S_RD_CTRL;
                            end
                        end else begin
                            // Voice still active — start the 2-tap fetch
                            // for linear interpolation.  No cache: per-voice
                            // re-fetch is cheap enough for the 32-voice scan.
                            fetch_tap_idx   <= 1'd0;
                            fetch_count     <= 2'd2;
                            state           <= S_TAP_FETCH_CALC;
                        end
                    end
                    default: begin
                        fetch_tap_idx   <= 1'd0;
                        fetch_count     <= 2'd2;
                        state           <= S_TAP_FETCH_CALC;
                    end
                endcase
            end

            // ---- Tap address stage 1: compute raw (pos ± offset) ----
            // Linear interp uses 2 taps: offset 0 (tap_0) and offset 1
            // (tap_1).  In bidi-reverse mode tap_1 lives at pos-1.
            // Registered into tap_raw so the 22-bit add + offset mux
            // don't share a cycle with the bounds/wrap logic in S_TAP_FETCH.
            S_TAP_FETCH_CALC: begin
                begin : tap_raw_block
                    // Linear interp: offset is 0 or +1; bidi-reverse
                    // subtracts.  Single-bit offset, no sign extension.
                    reg [21:0] off22;
                    off22 = {21'd0, fetch_tap_idx};
                    if (cur_bidi && cur_dir)
                        tap_raw <= $signed({2'b0, cur_pos_int}) -
                                   $signed({2'b0, off22});
                    else
                        tap_raw <= $signed({2'b0, cur_pos_int}) +
                                   $signed({2'b0, off22});
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
                end
                state <= S_TAP_ADDR;
            end

            // ---- Compute CRAM word addr (pipeline stage 2) ----
            // tap_fetch_addr is registered; cram_word_addr is one add.
            S_TAP_ADDR: begin
                tap_fetch_word <= cram_word_addr(cur_addr, tap_fetch_addr, cur_fmt16);
                state <= S_TAP_CRAM;
            end

            // ---- Cache lookup -----------------------------------------
            // cache_hit (combinational, computed from cur_voice +
            // tap_fetch_word + cache_base/cache_valid_bm) is already
            // settled at the S_TAP_CRAM cycle, AND cache_b_addr (also
            // combinational) is presented to the M10K port-B address
            // pin THIS cycle, so cache_b_q lands at S_TAP_WAIT one
            // cycle later — exactly the latency built into the FSM.
            // hit  → S_TAP_WAIT consumes cache_b_q
            // miss → kick burst_rd at tap_fetch_word, wait for fill
            S_TAP_CRAM: begin
                if (cache_hit) begin
                    state <= S_TAP_WAIT;
                end else if (!cram1_busy && !cram1_inhibit) begin
                    /* Miss — anchor cache_base at the 8-word boundary
                     * containing tap_word.  Forcing low 3 bits to 0
                     * lets the hit check be a 19-bit equality (see
                     * comments at cur_cache_base_hi).  cache_b_addr
                     * uses tap_fetch_word[2:0] directly so the voice's
                     * tap lands at the correct slot in the cache. */
                    cache_base[cur_voice] <= {tap_fetch_word[21:3], 3'b000};
                    cache_valid_bm[cur_voice] <= 1'b0;   // stale until burst lands
                    pf_voice            <= cur_voice;
                    pf_idx              <= 3'd0;
                    pf_base_word        <= {tap_fetch_word[21:3], 3'b000};
                    cram1_burst_addr    <= {tap_fetch_word[21:3], 3'b000};
                    cram1_burst_len     <= 5'd7;       // 8 words
                    cram1_burst_rd      <= 1'b1;       // single-cycle pulse
                    state <= S_PF_WAIT;
                end
                /* else (cram1_busy / inhibit): hold here, retry next cycle */
            end

            // ---- Tap extract from cache (1-cycle M10K read latency) ---
            S_TAP_WAIT: begin
                /* cache_b_q now reflects mix_cache[cache_b_addr] from
                 * the previous cycle (the M10K's registered output).
                 * Because cache_b_addr is combinational from
                 * cur_voice + tap_fetch_word + cache_base, it was stable
                 * during the S_TAP_CRAM cycle and the M10K's read fired
                 * at that posedge.  Result is here now. */
                if (fetch_tap_idx == 1'd0)
                    tap_0 <= extract_sample(cache_b_q, fetch_pos, cur_fmt16);
                else
                    tap_1 <= extract_sample(cache_b_q, fetch_pos, cur_fmt16);
                state <= S_TAP_NEXT;
            end

            // ---- Prefetch: wait for 8 burst_q_valid pulses -----------
            // The Port-A write happens combinationally each cycle that
            // cram1_burst_q_valid is high (driven by the always block
            // that maintains cache_a_we below).  We just count pulses.
            S_PF_WAIT: begin
                cram1_burst_rd <= 1'b0;   // pulse already issued
                if (cram1_burst_q_valid) begin
                    if (pf_idx == 3'd7) begin
                        cache_valid_bm[pf_voice] <= 1'b1;
                        state <= S_TAP_CRAM;   // retry — will hit now
                    end else begin
                        pf_idx <= pf_idx + 3'd1;
                    end
                end
            end

            // ---- Advance to next tap or start linear interp ----
            S_TAP_NEXT: begin
                fetch_count <= fetch_count - 2'd1;
                if (fetch_count <= 2'd1) begin
                    // Both taps loaded — start linear interp pipeline.
                    state <= S_LERP1;
                end else begin
                    // More taps to fetch
                    fetch_tap_idx <= fetch_tap_idx + 1'd1;
                    state <= S_TAP_FETCH_CALC;
                end
            end

            // ---- Linear interp stage 1: tap_1 - tap_0 (s17 register) ----
            // Split out from the multiply so subtract-then-multiply isn't
            // a single 100 MHz cycle.  The 2-stage version was the
            // post-cleanup -5 ns critical path on clk_cpu.
            S_LERP1: begin
                lerp_diff <= $signed({tap_1[15], tap_1}) -
                             $signed({tap_0[15], tap_0});
                state <= S_LERP2;
            end

            // ---- Linear interp stage 2: diff × frac (DSP-inferred) ----
            // s17 × u16 → s33 product.  Maps to one DSP block, so the
            // multiply alone is a clean FF→DSP→FF cycle.
            S_LERP2: begin
                lerp_diff_prod <= lerp_diff *
                                  $signed({1'b0, cur_pos_frac});
                state <= S_LERP3;
            end

            // ---- Linear interp stage 3: tap_0 + diff_prod[31:16] ----
            // Final 16-bit add lands the interpolated sample.  Linear
            // output is bounded by [tap_0, tap_1] so no clamp.
            S_LERP3: begin
                interp_sample <= tap_0 + lerp_diff_prod[31:16];
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
                    pl = interp_sample * $signed({1'b0, log_vol_l});
                    pr = interp_sample * $signed({1'b0, log_vol_r});
                    scaled_l <= pl[23:8];
                    scaled_r <= pr[23:8];
                end
                state <= S_ACCUM;
            end

            // ---- Pipeline stage 3: accumulate ----
            S_ACCUM: begin
                accum_l <= accum_l + {{16{scaled_l[15]}}, scaled_l};
                accum_r <= accum_r + {{16{scaled_r[15]}}, scaled_r};
                // Phase 6b: stage A of the wet-bus pipe — register
                // the mono mix and capture this voice's send levels.
                // Multiply happens in S_VOL_RAMP, accumulate in
                // S_VOL_RAMP_WR.  Splitting it keeps each cycle's
                // FF→logic→FF window short enough for 100 MHz.
                mono_voice_reg     <= scaled_l + scaled_r;        // s16 + s16 → s17
                voice_rev_send_reg <= voice_reverb_send[cur_voice];
                voice_chr_send_reg <= voice_chorus_send[cur_voice];
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
                // Phase 6b stage B of the wet-bus pipe: register the
                // mono × send products (one DSP each).  Accumulate
                // happens in S_VOL_RAMP_WR.
                reverb_prod_reg <= mono_voice_reg * $signed({1'b0, voice_rev_send_reg});
                chorus_prod_reg <= mono_voice_reg * $signed({1'b0, voice_chr_send_reg});
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
                // Phase 6b stage C: shift product down by 8 (compensate
                // for u8 send scale) and add to the wet accumulator.
                // Sign-extend s17 product slice into the s32 acc.
                reverb_in_acc <= reverb_in_acc +
                                 {{15{reverb_prod_reg[24]}}, reverb_prod_reg[24:8]};
                chorus_in_acc <= chorus_in_acc +
                                 {{15{chorus_prod_reg[24]}}, chorus_prod_reg[24:8]};
                state <= S_WR_POS;
            end

            // ---- Write back new position ----
            S_WR_POS: begin
                // If the CPU just wrote POS_INT for this voice (via
                // port B), skip our write-back on port A so the
                // CPU's seek value stands.  The CPU's write went
                // directly into vtbl; the next pass through this
                // voice will read that fresh value and advance from
                // there normally.  The flag stays set through
                // S_WR_FRAC so that stage also skips — it clears at
                // the end of S_WR_FRAC.
                vtbl_a_wr <= !pos_wr_pending[cur_voice];
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
                        /* Only mark the voice ended if we're NOT
                         * also skipping the write-back due to a
                         * just-arrived CPU seek — a CPU seek that
                         * resets POS to 0 while the FSM sees the
                         * stale end-of-sample pos would otherwise
                         * prematurely stop the voice. */
                        if (!pos_wr_pending[cur_voice]) begin
                            voice_active[cur_voice] <= 0;
                            voice_end_pending[cur_voice] <= 1;
                        end
                        pos_wrapped <= 1;
                    end
                    vtbl_a_data <= {10'd0, new_pos_int};
                end

                state <= S_WR_FRAC;
            end

            S_WR_FRAC: begin
                vtbl_a_addr <= {cur_voice, VTBL_POS_FRAC};

                // Clear the CPU-seek-pending latch now that we've
                // passed the POS_INT write stage.  If the flag was
                // set, skip the FRAC write-back too so the CPU's
                // seek isn't contaminated by a partial FSM update.
                if (pos_wr_pending[cur_voice])
                    pos_wr_pending[cur_voice] <= 1'b0;

                if (pos_wr_pending[cur_voice]) begin
                    vtbl_a_wr <= 1'b0;
                end else if (pos_wrapped) begin
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
                else if (cur_voice == 5'd31)
                    state <= S_REVERB;
                else begin
                    cur_voice <= cur_voice + 5'd1;
                    state <= S_NEXT_VOICE;
                end
            end

            S_WR_DIR: begin
                vtbl_a_wr <= 1;
                vtbl_a_addr <= {cur_voice, VTBL_CTRL};
                vtbl_a_data <= {27'd0, new_dir, cur_bidi, cur_fmt16, cur_loop, 1'b1};

                if (cur_voice == 5'd31)
                    state <= S_REVERB;
                else begin
                    cur_voice <= cur_voice + 5'd1;
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

            S_REVERB: begin
                // Pipe stage A: rev_q just valid from the M10K.  Kick
                // off the two 16×8 multiplies into pipeline regs, and
                // latch the current mono input for use in stage B.
                rev_tap          <= rev_q;
                rev_wet_prod_reg <= rev_q * $signed({1'b0, reverb_wet_level});
                rev_fb_prod_reg  <= rev_q * $signed({1'b0, reverb_feedback});
                rev_mono_in_reg  <= rev_mono_in;
                // Chorus runs in parallel: its q_b is also valid this
                // cycle (we addressed it from the previous S_OUTPUT).
                chor_tap          <= chor_q;
                chor_wet_prod_reg <= chor_q * $signed({1'b0, chorus_wet_level});
                state             <= S_REVERB2;
            end

            S_REVERB2: begin
                // Pipe stage B: products are registered; shift them
                // down by 8 and clamp to form the 16-bit wet mix and
                // new-tap values that S_OUTPUT consumes.  The saw
                // chain here is short (1 add + 1 clamp per reg).
                rev_wet_reg <= rev_wet_prod_reg[23:8];
                rev_new_reg <= ((rev_mono_in_reg + rev_fb_prod_reg[23:8]) >  17'sd32767) ? 16'h7FFF :
                               ((rev_mono_in_reg + rev_fb_prod_reg[23:8]) < -17'sd32768) ? 16'h8000 :
                               (rev_mono_in_reg + rev_fb_prod_reg[23:8]);
                chor_wet_reg <= chor_wet_prod_reg[23:8];
                state        <= S_OUTPUT;
            end

            S_OUTPUT: begin
                sample_wr <= 1;
                sample_data <= {clamp_l, clamp_r};  // {Left[15:0], Right[15:0]}
                active_cnt <= voice_cnt;
                // Commit the pipelined new reverb tap into the delay
                // line and advance the circular pointer; pre-address
                // the next read so NEXT sample's S_REVERB has q_b valid.
                rev_wr_addr <= rev_w_ptr;
                rev_wr_data <= rev_new_reg;
                rev_wr_en   <= 1'b1;
                rev_w_ptr   <= rev_w_ptr + 10'd1;
                rev_rd_addr <= rev_w_ptr + 10'd1;
                // Chorus delay: write dry mono in, advance pointers,
                // step the LFO phase by chorus_lfo_rate.  Read pointer
                // is the LFO-modulated offset around the new write head.
                chor_wr_addr   <= chor_w_ptr;
                chor_wr_data   <= chor_in;        // dedicated per-voice chorus send accumulator
                chor_wr_en     <= 1'b1;
                chor_w_ptr     <= chor_w_ptr + 9'd1;
                chor_rd_addr   <= chor_rd_ptr_next;
                chor_lfo_phase <= chor_lfo_phase + chorus_lfo_rate;
                state <= S_IDLE;
            end

            default: state <= S_IDLE;
            endcase
        end
    end
end

endmodule
