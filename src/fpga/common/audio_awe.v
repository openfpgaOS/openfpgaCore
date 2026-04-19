//
// AWE — Audio coprocessor for openfpgaOS (Phase 1 skeleton)
//
// Phase 1 scope: CPU-facing register interface validated end-to-end.
// AWE owns 48 voice-state slots, a 16-channel bank, and a small set
// of globals.  On AWE_NOTE_ON it emits a fixed sequence of writes to
// the mixer's voice table using values pre-composed by the CPU and
// stored in the voice state RAM.  No control-rate processing yet
// (no ramp FSM, no LFO, no mod matrix) — all of that lands in Phases
// 2-5 per the design doc.
//
// Voice-state RAM layout (32 words × 4 B = 128 B per voice).  Phase 1
// only reads words 0-11 in the NOTE_ON FSM; words 12-31 are reserved
// for the per-voice ramp / LFO / mod-matrix state populated in later
// phases.  CPU writes any word via the windowed register interface.
//
// FSM emits in this exact order — CTRL last so the voice doesn't
// activate until ADDR / LEN / RATE / VOL / LOOP / FILTER are all set.
//
// VOL_TARGET + VOL_RATE matter even though the SDK passes a static
// VOL_LR: when vol_rate == 0 the mixer "snaps" vol_lr to vol_target
// every sample period, so without these the static VOL_LR is silently
// pulled to whatever was in vol_target from the slot's previous tenant
// (typically 0).  AWE writes VOL_TARGET = VOL_LR and VOL_RATE = 0 so
// the snap is a no-op and the voice plays at the requested level.
//
//   W0:  BASE          — sample CRAM1 word address    → mixer field 0
//   W1:  LEN           — sample count                 → mixer field 1
//   W2:  RATE          — Q16.16 playback rate         → mixer field 2
//   W3:  POS_INT       — initial sample position (0)  → mixer field 4
//   W4:  VOL_LR        — {vol_r[7:0], vol_l[7:0]}     → mixer field 6
//   W5:  VOL_TARGET    — same packing as VOL_LR       → mixer field 9
//   W6:  VOL_RATE      — ramp step (0 = instant)      → mixer field 10
//   W7:  LOOP_END      — sample count                 → mixer field 7
//   W8:  LOOP_START    — sample count                 → mixer field 8
//   W9:  FILTER_FC     — Q0.16 cutoff                 → mixer field 11
//   W10: FILTER_Q      — {enable[8], q[7:0]}          → mixer field 12
//   W11: CTRL          — {dir,bidi,fmt16,loop,active} → mixer field 3 (LAST)
//   W12..W31: reserved — Phase 2-5 ramp/LFO/MM state
//
// Address from axi_awe_slave: {voice[5:0], word[4:0]} → 11 bits.
//
// Channel bank: 16 channels × 4 words × 32 bits in distributed regs.
// Phase 1 stores CC writes but does not recompose live voices; that
// kicks in once the tick scheduler exists (Phase 2+).
//
// active_mask: bit per voice, set when NOTE_ON for that voice strobes
// (and that voice's CTRL word has active=1), cleared by NOTE_OFF or
// VOICE_STOP, or by the mixer's voice_end_irq once that's wired in.
//

`default_nettype none

module audio_awe (
    input  wire        clk,
    input  wire        reset_n,

    // Voice-state write window (from axi_awe_slave)
    input  wire        cpu_voice_state_wr,
    input  wire [10:0] cpu_voice_state_addr,    // {voice[5:0], word[4:0]}
    input  wire [31:0] cpu_voice_state_wdata,

    // Channel bank writes
    input  wire        cpu_chan_wr,
    input  wire [5:0]  cpu_chan_addr,           // {channel[3:0], word[1:0]}
    input  wire [31:0] cpu_chan_wdata,

    // Mod-matrix scale writes (Phase 4).  CPU writes one 16-bit signed
    // scale at a time via AWE_MM_VOICE / AWE_MM_FIELD / AWE_MM_DATA.
    // `field` matches awe_mm_t member order: 0=lfo0_pitch, 1=lfo0_filter,
    // 2=lfo1_pitch, 3=ramp1_pitch, 4=ramp1_filt.
    input  wire        cpu_mm_wr,
    input  wire [5:0]  cpu_mm_voice,
    input  wire [2:0]  cpu_mm_field,
    input  wire signed [15:0] cpu_mm_wdata,

    // Globals
    input  wire        cpu_global_wr,
    input  wire [3:0]  cpu_global_addr,
    input  wire [31:0] cpu_global_wdata,

    // Strobes (1-cycle pulses)
    input  wire        cpu_note_on,
    input  wire [5:0]  cpu_note_on_voice,
    input  wire        cpu_note_off,
    input  wire [5:0]  cpu_note_off_voice,
    input  wire        cpu_voice_stop,
    input  wire [5:0]  cpu_voice_stop_voice,

    // Phase 5c — Ramp1 (mod env) trigger.  CPU writes the rate to
    // AWE_RAMP1_RATE first, then writes {voice, stage} to
    // AWE_RAMP1_TRIGGER which pulses cpu_ramp1_trig.  Stage encoding
    // matches ENV_*: 2=ATTACK (resets level=0), 6=RELEASE (keep level),
    // 7=DONE (level=0).
    input  wire        cpu_ramp1_trig,
    input  wire [5:0]  cpu_ramp1_voice,
    input  wire [3:0]  cpu_ramp1_stage,
    input  wire [31:0] cpu_ramp1_rate,

    // Output to mixer (priority over CPU-direct periph writes —
    // mux lives in core_top alongside the existing periph_slave drive).
    // The two internal drivers (NOTE_ON burst FSM, VOL_COMPOSE sub-FSM)
    // write into *_fsm_* regs; the module-level assigns mux them onto
    // the external output.  NOTE_ON wins if both assert the same cycle
    // (rare: a tick coincident with an app note-on).
    output wire        awe_mix_voice_wr,
    output wire [3:0]  awe_mix_voice_field,
    output wire [5:0]  awe_mix_voice_sel,
    output wire [31:0] awe_mix_voice_wdata,

    // Voice-active bitmap (for SW voice-stealing readback)
    output reg  [31:0] active_mask,

    // Phase 2 sequencer: tick_count increments once per 1 kHz tick
    // pulse and is exposed via AWE_TICK_COUNT (0x14C) so the SDK can
    // confirm the scheduler is running.  Rolls over naturally every
    // ~50 days at 1 kHz.
    output reg  [31:0] tick_count,

    // Phase 6a/6b — global reverb + chorus bus controls.
    output reg  [7:0]  reverb_wet_level,
    output reg  [7:0]  reverb_feedback,
    output reg  [7:0]  chorus_wet_level,
    output reg  [15:0] chorus_lfo_rate,
    output reg  [7:0]  chorus_lfo_depth
);

// =========================================================
// Voice-state RAM (5 M10Ks: 48 voices × 32 words × 32 bits)
// =========================================================
// Port A: CPU writes (cpu_voice_state_wr)
// Port B: NOTE_ON FSM reads (single-port read; word stream)
//
// altsyncram doesn't support a true read-while-write on the same
// address from both ports without `BIDIR_DUAL_PORT`, but we never
// alias: CPU writes happen only when the FSM is idle (the HAL writes
// the state, then strobes NOTE_ON).  DUAL_PORT (port A write, port B
// read) is the right config.

reg  [10:0] vstate_a_addr;
reg  [31:0] vstate_a_data;
reg         vstate_a_wr;

wire [31:0] vstate_b_q;
// Port B is shared: NOTE_ON burst FSM drives during its 24-cycle
// emission, the sequencer sub-FSMs drive during tick walks.  A mux
// at the altsyncram instantiation gives NOTE_ON priority (it only
// fires during app note-on, 1 kHz of tick activity is 99% idle).
reg  [10:0] noteon_vstate_addr;
reg  [10:0] seq_vstate_addr;
reg  [31:0] seq_vstate_data;
reg         seq_vstate_wr;
reg         noteon_port_busy;  // high during NOTE_ON FSM's read burst

// Mixer-write outputs: two internal drivers merged via a module-level
// priority mux further down.  NOTE_ON wins when both fire same cycle.
reg        noteon_mix_wr;
reg [3:0]  noteon_mix_field;
reg [5:0]  noteon_mix_sel;
reg [31:0] noteon_mix_wdata;
reg        vol_mix_wr;
reg [3:0]  vol_mix_field;
reg [5:0]  vol_mix_sel;
reg [31:0] vol_mix_wdata;
reg        pitch_mix_wr;       // Phase 5: PITCH_COMPOSE mixer emit
reg [3:0]  pitch_mix_field;
reg [5:0]  pitch_mix_sel;
reg [31:0] pitch_mix_wdata;
reg        filt_mix_wr;        // Phase 5b: FILTER_COMPOSE mixer emit
reg [3:0]  filt_mix_field;
reg [5:0]  filt_mix_sel;
reg [31:0] filt_mix_wdata;
reg        send_mix_wr;        // Phase 6b: SEND_COMPOSE mixer emit
reg [3:0]  send_mix_field;
reg [5:0]  send_mix_sel;
reg [31:0] send_mix_wdata;

wire [10:0] vstate_b_addr = noteon_port_busy ? noteon_vstate_addr : seq_vstate_addr;
wire [31:0] vstate_b_data = noteon_port_busy ? 32'b0              : seq_vstate_data;
wire        vstate_b_wr   = noteon_port_busy ? 1'b0               : seq_vstate_wr;

assign awe_mix_voice_wr    = noteon_mix_wr | vol_mix_wr | pitch_mix_wr | filt_mix_wr | send_mix_wr;
assign awe_mix_voice_field = noteon_mix_wr ? noteon_mix_field :
                             vol_mix_wr    ? vol_mix_field    :
                             pitch_mix_wr  ? pitch_mix_field  :
                             filt_mix_wr   ? filt_mix_field   :
                                             send_mix_field;
assign awe_mix_voice_sel   = noteon_mix_wr ? noteon_mix_sel :
                             vol_mix_wr    ? vol_mix_sel    :
                             pitch_mix_wr  ? pitch_mix_sel  :
                             filt_mix_wr   ? filt_mix_sel   :
                                             send_mix_sel;
assign awe_mix_voice_wdata = noteon_mix_wr ? noteon_mix_wdata :
                             vol_mix_wr    ? vol_mix_wdata    :
                             pitch_mix_wr  ? pitch_mix_wdata  :
                             filt_mix_wr   ? filt_mix_wdata   :
                                             send_mix_wdata;

// Port B needs write capability in Phase 3 (stage sub-FSMs update
// level/stage fields from the tick sequencer).  Switched from
// DUAL_PORT to BIDIR_DUAL_PORT; NOTE_ON FSM still only reads on it
// but now the sequencer's sub-FSMs can also write back.
altsyncram #(
    .operation_mode("BIDIR_DUAL_PORT"),
    .width_a(32),
    .widthad_a(11),
    .numwords_a(2048),       // shrinking to 1024 traded -3 M10Ks for +424 ALMs;
    .width_b(32),            // not worth it on Cyclone V — keep oversized.
    .widthad_b(11),
    .numwords_b(2048),
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
) voice_state_ram (
    .clock0(clk),
    .address_a(vstate_a_addr),
    .data_a(vstate_a_data),
    .wren_a(vstate_a_wr),
    .q_a(),
    .rden_a(1'b0),
    .clock1(clk),
    .address_b(vstate_b_addr),
    .data_b(vstate_b_data),
    .wren_b(vstate_b_wr),
    .q_b(vstate_b_q),
    .rden_b(1'b1),
    .aclr0(1'b0), .aclr1(1'b0),
    .addressstall_a(1'b0), .addressstall_b(1'b0),
    .byteena_a(1'b1), .byteena_b(1'b1),
    .clocken0(1'b1), .clocken1(1'b1),
    .clocken2(1'b1), .clocken3(1'b1),
    .eccstatus()
);

// Port-A driver: pass CPU writes straight through.  The address goes
// in the lower 11 bits as {voice[5:0], word[4:0]}; depth is padded to
// 2048 so the M10K fitter has slack.
always @(posedge clk) begin
    vstate_a_wr   <= cpu_voice_state_wr;
    vstate_a_addr <= cpu_voice_state_addr;
    vstate_a_data <= cpu_voice_state_wdata;
end

// =========================================================
// Cents → mult LUT (Phase 5)
// =========================================================
// 1200 × 16-bit ROM initialized from cents_to_mult.mif.  For cents
// in [0, 1199] the stored value is (2^(cents/1200) - 1) × 65536 in
// Q16; callers rebuild the full multiplier as 0x10000 + LUT[cents]
// and then shift by `octaves` to cover the full SF2 pitch range.
// Callers must guarantee the input has been mod-1200'd by the
// octave-reduce step — no saturation slots anymore.
reg  [10:0] c2m_addr;
wire [15:0] c2m_q;

altsyncram #(
    .operation_mode("ROM"),
    .width_a(16),
    .widthad_a(11),
    .numwords_a(1200),
    .outdata_reg_a("UNREGISTERED"),
    .init_file("cents_to_mult.mif"),
    .intended_device_family("Cyclone V"),
    .lpm_type("altsyncram"),
    .clock_enable_input_a("BYPASS"),
    .clock_enable_output_a("BYPASS"),
    .address_aclr_a("NONE"),
    .outdata_aclr_a("NONE")
) cents_to_mult_rom (
    .clock0(clk),
    .address_a(c2m_addr),
    .q_a(c2m_q),
    .clocken0(1'b1)
);

// =========================================================
// Shared octave-shift mux (Phase 8 ALM hunt)
// =========================================================
// PITCH_COMPOSE (stage 9) and FILTER_COMPOSE (stage 10) both reconstruct
// a Q16.16 multiplier as (1.0 + frac) << octaves.  These stages run
// sequentially in the sequencer (stage 9 retires before stage 10
// starts), so they can share a single combinational shift block instead
// of each instantiating its own 8-way 32-bit mux.  ~80 ALMs saved.
reg  [15:0]      shift_in_frac;
reg signed [3:0] shift_in_octaves;
wire [31:0]      shift_out_q16;
wire [31:0]      shift_base = {16'b0, shift_in_frac} + 32'h0001_0000;
assign shift_out_q16 =
    (shift_in_octaves == 4'sd3)  ? (shift_base << 3) :
    (shift_in_octaves == 4'sd2)  ? (shift_base << 2) :
    (shift_in_octaves == 4'sd1)  ? (shift_base << 1) :
    (shift_in_octaves == 4'sd0)  ?  shift_base       :
    (shift_in_octaves == -4'sd1) ? (shift_base >> 1) :
    (shift_in_octaves == -4'sd2) ? (shift_base >> 2) :
    (shift_in_octaves == -4'sd3) ? (shift_base >> 3) :
                                    32'h0001_0000;

// =========================================================
// Channel bank (16 ch × 4 words × 32 bits = 64 words)
// =========================================================
// M10K-backed DUAL_PORT: port A absorbs CPU writes, port B serves
// sub-FSM reads during VOL_COMPOSE / PITCH_COMPOSE / FILTER_COMPOSE.
// Sync-read — readers latch the address one cycle before consuming
// the data on chan_b_q.  Frees ~100 ALMs versus the old distributed
// register file and lets Quartus place the 2 KB block in one M10K.

reg  [5:0]  chan_b_addr;
wire [31:0] chan_b_q;

altsyncram #(
    .operation_mode("DUAL_PORT"),
    .width_a(32),
    .widthad_a(6),
    .numwords_a(64),
    .width_b(32),
    .widthad_b(6),
    .numwords_b(64),
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
) chan_bank_ram (
    .clock0(clk),
    .address_a(cpu_chan_addr),
    .data_a(cpu_chan_wdata),
    .wren_a(cpu_chan_wr),
    .address_b(chan_b_addr),
    .q_b(chan_b_q),
    .clocken0(1'b1)
);

// =========================================================
// Mod-matrix scales (Phase 4) — per-voice sidecar register file
// =========================================================
// 48 voices × 5 signed 16-bit scales = 3,840 bits.  Synthesizes to
// distributed RAM / MLAB — async read makes them available in one
// cycle during MM_PITCH_ACCUM / MM_FILTER_ACCUM.  CPU writes one
// scale at a time via AWE_MM_WRITE: {field[2:0], voice[5:0]} address
// and signed 16-bit data.  `field` index matches awe_mm_t member
// order.
// Each 48×16 array becomes its own tiny altsyncram DUAL_PORT block
// (M10K-backed, sync read).  Port A takes the one-at-a-time CPU
// scale writes, port B serves the MM_*_ACCUM sub-FSMs.  Sync read
// adds one wait cycle to the pitch/filter FSMs but saves the 3 × 32:1
// muxes the distributed LUT-RAM variant required.  Each 32-entry
// slice is exactly one MLAB (32 × 20 native shape).
//
// mm_ramp1_pitch / mm_ramp1_filt deferred to Phase 5c — the HAL still
// writes fields 3 and 4 for ABI stability, but the writes fall on the
// floor until RAMP1_STEP goes live.
reg  [4:0]       mm_s_a_addr;
reg  [15:0]      mm_s_a_wdata;
reg              mm_s_lfo0_p_wr;
reg              mm_s_lfo0_f_wr;
reg              mm_s_lfo1_p_wr;
reg              mm_s_ramp1_p_wr;   // Phase 5c
reg              mm_s_ramp1_f_wr;   // Phase 5c
reg  [4:0]       mm_s_b_addr;
wire signed [15:0] mm_s_lfo0_p_q;
wire signed [15:0] mm_s_lfo0_f_q;
wire signed [15:0] mm_s_lfo1_p_q;
wire signed [15:0] mm_s_ramp1_p_q;   // Phase 5c
wire signed [15:0] mm_s_ramp1_f_q;   // Phase 5c

always @(posedge clk) begin
    mm_s_lfo0_p_wr  <= 1'b0;
    mm_s_lfo0_f_wr  <= 1'b0;
    mm_s_lfo1_p_wr  <= 1'b0;
    mm_s_ramp1_p_wr <= 1'b0;
    mm_s_ramp1_f_wr <= 1'b0;
    if (cpu_mm_wr) begin
        mm_s_a_addr  <= cpu_mm_voice[4:0];
        mm_s_a_wdata <= cpu_mm_wdata;
        case (cpu_mm_field)
            3'd0: mm_s_lfo0_p_wr  <= 1'b1;
            3'd1: mm_s_lfo0_f_wr  <= 1'b1;
            3'd2: mm_s_lfo1_p_wr  <= 1'b1;
            3'd3: mm_s_ramp1_p_wr <= 1'b1;   // Phase 5c
            3'd4: mm_s_ramp1_f_wr <= 1'b1;   // Phase 5c
            default: ;
        endcase
    end
end

// Phase 5c — Ramp1 trigger writer.  Port-A writes to ramp1_meta_ram
// and ramp1_level_ram from the cpu_ramp1_trig pulse.  Stage 4's
// RAMP1_STEP owns port A as well, so trigger and FSM are arbitrated
// here: CPU trigger wins (pulses are rare; FSM retries next tick).
// FSM-side writes are driven by ramp1_*_wr_fsm in the sub-FSM block.
reg  [31:0] ramp1_meta_a_wdata_fsm, ramp1_level_a_wdata_fsm;
reg  [4:0]  ramp1_a_addr_fsm;
reg         ramp1_meta_wr_fsm, ramp1_level_wr_fsm;
always @(*) begin
    if (cpu_ramp1_trig) begin
        ramp1_a_addr        = cpu_ramp1_voice[4:0];
        ramp1_meta_a_wdata  = {cpu_ramp1_stage, cpu_ramp1_rate[27:0]};
        ramp1_meta_wr       = 1'b1;
        // ATTACK resets level to 0; DONE clears to 0; RELEASE keeps
        // current level so the fade continues from where it was.
        ramp1_level_a_wdata = 32'd0;
        ramp1_level_wr      = (cpu_ramp1_stage == 4'd2) ||
                              (cpu_ramp1_stage == 4'd7);
    end else begin
        ramp1_a_addr        = ramp1_a_addr_fsm;
        ramp1_meta_a_wdata  = ramp1_meta_a_wdata_fsm;
        ramp1_meta_wr       = ramp1_meta_wr_fsm;
        ramp1_level_a_wdata = ramp1_level_a_wdata_fsm;
        ramp1_level_wr      = ramp1_level_wr_fsm;
    end
end

altsyncram #(
    .operation_mode("DUAL_PORT"),
    .width_a(16), .widthad_a(5), .numwords_a(32),
    .width_b(16), .widthad_b(5), .numwords_b(32),
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
) mm_lfo0_pitch_ram (
    .clock0(clk),
    .address_a(mm_s_a_addr),
    .data_a(mm_s_a_wdata),
    .wren_a(mm_s_lfo0_p_wr),
    .address_b(mm_s_b_addr),
    .q_b(mm_s_lfo0_p_q),
    .clocken0(1'b1)
);
altsyncram #(
    .operation_mode("DUAL_PORT"),
    .width_a(16), .widthad_a(5), .numwords_a(32),
    .width_b(16), .widthad_b(5), .numwords_b(32),
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
) mm_lfo0_filter_ram (
    .clock0(clk),
    .address_a(mm_s_a_addr),
    .data_a(mm_s_a_wdata),
    .wren_a(mm_s_lfo0_f_wr),
    .address_b(mm_s_b_addr),
    .q_b(mm_s_lfo0_f_q),
    .clocken0(1'b1)
);
altsyncram #(
    .operation_mode("DUAL_PORT"),
    .width_a(16), .widthad_a(5), .numwords_a(32),
    .width_b(16), .widthad_b(5), .numwords_b(32),
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
) mm_lfo1_pitch_ram (
    .clock0(clk),
    .address_a(mm_s_a_addr),
    .data_a(mm_s_a_wdata),
    .wren_a(mm_s_lfo1_p_wr),
    .address_b(mm_s_b_addr),
    .q_b(mm_s_lfo1_p_q),
    .clocken0(1'b1)
);

// Phase 5c: ramp1 → pitch / filter scales (each 32 × 16 bits, 1 MLAB).
altsyncram #(
    .operation_mode("DUAL_PORT"),
    .width_a(16), .widthad_a(5), .numwords_a(32),
    .width_b(16), .widthad_b(5), .numwords_b(32),
    .outdata_reg_a("UNREGISTERED"), .outdata_reg_b("UNREGISTERED"),
    .address_reg_b("CLOCK0"),
    .read_during_write_mode_mixed_ports("OLD_DATA"),
    .rdcontrol_reg_b("CLOCK0"),
    .intended_device_family("Cyclone V"), .lpm_type("altsyncram"),
    .clock_enable_input_a("BYPASS"),  .clock_enable_input_b("BYPASS"),
    .clock_enable_output_a("BYPASS"), .clock_enable_output_b("BYPASS"),
    .power_up_uninitialized("FALSE")
) mm_ramp1_pitch_ram (
    .clock0(clk),
    .address_a(mm_s_a_addr), .data_a(mm_s_a_wdata),
    .wren_a(mm_s_ramp1_p_wr),
    .address_b(mm_s_b_addr), .q_b(mm_s_ramp1_p_q),
    .clocken0(1'b1)
);
altsyncram #(
    .operation_mode("DUAL_PORT"),
    .width_a(16), .widthad_a(5), .numwords_a(32),
    .width_b(16), .widthad_b(5), .numwords_b(32),
    .outdata_reg_a("UNREGISTERED"), .outdata_reg_b("UNREGISTERED"),
    .address_reg_b("CLOCK0"),
    .read_during_write_mode_mixed_ports("OLD_DATA"),
    .rdcontrol_reg_b("CLOCK0"),
    .intended_device_family("Cyclone V"), .lpm_type("altsyncram"),
    .clock_enable_input_a("BYPASS"),  .clock_enable_input_b("BYPASS"),
    .clock_enable_output_a("BYPASS"), .clock_enable_output_b("BYPASS"),
    .power_up_uninitialized("FALSE")
) mm_ramp1_filter_ram (
    .clock0(clk),
    .address_a(mm_s_a_addr), .data_a(mm_s_a_wdata),
    .wren_a(mm_s_ramp1_f_wr),
    .address_b(mm_s_b_addr), .q_b(mm_s_ramp1_f_q),
    .clocken0(1'b1)
);

// =========================================================
// Phase 5c — Ramp1 (mod env) state
// =========================================================
// 32 voices × {stage[3:0], rate[27:0]} in ramp1_meta_ram (1 M10K) and
// 32 × level[31:0] in ramp1_level_ram (1 M10K).  CPU triggers via
// AWE_RAMP1_RATE (latch) + AWE_RAMP1_TRIGGER (commit).  RAMP1_STEP at
// sequencer stage 4 walks each voice and advances the level toward
// 0x10000 (ATTACK) or toward 0 (RELEASE).  Simplified A/S/R model —
// no DAHDSR detail.  Suits the ~80 % of SF2 mod envs that just want
// "fade in to peak, then fade out on note-off".
reg  [4:0]  ramp1_a_addr;
reg  [31:0] ramp1_meta_a_wdata, ramp1_level_a_wdata;
reg         ramp1_meta_wr, ramp1_level_wr;
reg  [4:0]  ramp1_b_addr;
wire [31:0] ramp1_meta_q;
wire [31:0] ramp1_level_q;

altsyncram #(
    .operation_mode("DUAL_PORT"),
    .width_a(32), .widthad_a(5), .numwords_a(32),
    .width_b(32), .widthad_b(5), .numwords_b(32),
    .outdata_reg_a("UNREGISTERED"), .outdata_reg_b("UNREGISTERED"),
    .address_reg_b("CLOCK0"),
    .read_during_write_mode_mixed_ports("OLD_DATA"),
    .rdcontrol_reg_b("CLOCK0"),
    .intended_device_family("Cyclone V"), .lpm_type("altsyncram"),
    .clock_enable_input_a("BYPASS"),  .clock_enable_input_b("BYPASS"),
    .clock_enable_output_a("BYPASS"), .clock_enable_output_b("BYPASS"),
    .power_up_uninitialized("FALSE")
) ramp1_meta_ram (
    .clock0(clk),
    .address_a(ramp1_a_addr), .data_a(ramp1_meta_a_wdata),
    .wren_a(ramp1_meta_wr),
    .address_b(ramp1_b_addr), .q_b(ramp1_meta_q),
    .clocken0(1'b1)
);
altsyncram #(
    .operation_mode("DUAL_PORT"),
    .width_a(32), .widthad_a(5), .numwords_a(32),
    .width_b(32), .widthad_b(5), .numwords_b(32),
    .outdata_reg_a("UNREGISTERED"), .outdata_reg_b("UNREGISTERED"),
    .address_reg_b("CLOCK0"),
    .read_during_write_mode_mixed_ports("OLD_DATA"),
    .rdcontrol_reg_b("CLOCK0"),
    .intended_device_family("Cyclone V"), .lpm_type("altsyncram"),
    .clock_enable_input_a("BYPASS"),  .clock_enable_input_b("BYPASS"),
    .clock_enable_output_a("BYPASS"), .clock_enable_output_b("BYPASS"),
    .power_up_uninitialized("FALSE")
) ramp1_level_ram (
    .clock0(clk),
    .address_a(ramp1_a_addr), .data_a(ramp1_level_a_wdata),
    .wren_a(ramp1_level_wr),
    .address_b(ramp1_b_addr), .q_b(ramp1_level_q),
    .clocken0(1'b1)
);

// =========================================================
// Globals
// =========================================================

reg [7:0]  master_vol;       // 0..255
reg [11:0] bend_range_cents;
// reverb_preset / chorus_preset / interp_default removed in Phase 8 ALM
// hunt — they were assigned but never read by any compose stage.

reg hw_envelope_enable;  // Phase 3b: live reg, fabric divides eliminated

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        master_vol         <= 8'd255;
        bend_range_cents   <= 12'd200;     // ±2 semitones default
        hw_envelope_enable <= 1'b0;
        reverb_wet_level   <= 8'd0;     // bypass at boot
        reverb_feedback    <= 8'd0;
        chorus_wet_level   <= 8'd0;
        chorus_lfo_rate    <= 16'd0;
        chorus_lfo_depth   <= 8'd0;
    end else if (cpu_global_wr) begin
        case (cpu_global_addr)
            4'd0: master_vol         <= cpu_global_wdata[7:0];
            4'd1: bend_range_cents   <= cpu_global_wdata[11:0];
            4'd5: hw_envelope_enable <= cpu_global_wdata[0];
            4'd6: reverb_wet_level   <= cpu_global_wdata[7:0];
            4'd7: reverb_feedback    <= cpu_global_wdata[7:0];
            4'd8: chorus_wet_level   <= cpu_global_wdata[7:0];
            4'd9: chorus_lfo_rate    <= cpu_global_wdata[15:0];
            4'd10: chorus_lfo_depth  <= cpu_global_wdata[7:0];
            default: ;
        endcase
    end
end

// =========================================================
// Stage sequencer skeleton (Phase 2)
// =========================================================
// 1 kHz tick derived from clk_cpu (100 MHz) via a /100000 prescaler.
// On each tick_pulse the sequencer walks voice=0..47 × stage=0..17,
// advancing the (voice, stage) counter one step per cycle.  Phase 2
// has every stage wired as a NOP — the point is to prove the cadence
// is right and the fabric fits before Phases 3-5 drop real per-stage
// logic into the case statement below.
//
// Cost per tick: 48 × 18 = 864 cycles = 0.86 % of the 100 k-cycle tick
// period.  Plenty of headroom even once the stages stop being NOPs;
// the design doc's estimate was ~2.6 % at 3 cycles per stage.
//
// tick_count is a free-running 32-bit counter the SDK can sample via
// AWE_TICK_COUNT to confirm the scheduler is running at the right rate.

localparam [16:0] SEQ_TICK_RELOAD = 17'd99_999;  // 100 MHz / 100000 = 1 kHz
localparam [4:0]  SEQ_STAGE_LAST  = 5'd17;
localparam [5:0]  SEQ_VOICE_LAST  = 6'd31;

// Voice-state word offsets — must match hal/awe.c's VW_* + Phase-3 extensions.
localparam [4:0] VW_RATE           = 5'd2;   // BASE_RATE (Q16.16) — Phase 5 reads
localparam [4:0] VW_MIDI_CH_BV_PAN = 5'd5;   // {pan_base[15:0], voice_base_vol[7:0], midi_ch[7:0]}
localparam [4:0] VW_RAMP0_LEVEL    = 5'd12;  // Q16.16, 0..0x10000
localparam [4:0] VW_RAMP0_RATE     = 5'd13;  // Q16.16 current segment rate
localparam [4:0] VW_RAMP0_TARGET   = 5'd14;  // Q16.16 current segment target
localparam [4:0] VW_RAMP0_TIMER    = 5'd15;  // ticks remaining (delay/hold)
localparam [4:0] VW_RAMP0_STAGE    = 5'd16;  // {_, noff_pending, stage[3:0]}
localparam [4:0] VW_RAMP0_ATTACK_R = 5'd17;  // baked attack_rate
localparam [4:0] VW_RAMP0_HOLD_T   = 5'd18;  // baked hold_ticks
localparam [4:0] VW_RAMP0_DECAY_R  = 5'd19;  // baked decay_rate
localparam [4:0] VW_RAMP0_SUSTAIN  = 5'd20;  // baked sustain_level
localparam [4:0] VW_RAMP0_RELEASE_T= 5'd21;  // baked release_ticks

// Phase 4 — LFOs + mod-matrix outputs.
// LFO state: 16-bit phase (wraps at 0x10000), 16-bit delay_rem (ticks
// remaining before LFO starts advancing), 16-bit rate (phase incr per
// tick), 3-bit waveform (only TRIANGLE = 0 wired in this build),
// signed 32-bit output cache that stage 7/8 read without recomputing.
localparam [4:0] VW_LFO0_STATE     = 5'd22;  // {phase[15:0], delay_rem[15:0]}
localparam [4:0] VW_LFO0_CONFIG    = 5'd23;  // {13'b0, wave[2:0], rate[15:0]}
localparam [4:0] VW_LFO0_OUTPUT    = 5'd24;  // signed s32 cached output
localparam [4:0] VW_LFO1_STATE     = 5'd25;
localparam [4:0] VW_LFO1_CONFIG    = 5'd26;
localparam [4:0] VW_LFO1_OUTPUT    = 5'd27;
localparam [4:0] VW_PITCH_OFS      = 5'd28;  // signed cents — stage 7 writes
localparam [4:0] VW_FILTER_OFS     = 5'd29;  // signed cents — stage 8 writes
localparam [4:0] VW_INITIAL_FC     = 5'd30;  // raw SF2 fc in cents — FILTER_COMPOSE reads

// Envelope stages (match env_stage_t in of_smp_voice.h)
localparam [3:0] ENV_OFF     = 4'd0;
localparam [3:0] ENV_DELAY   = 4'd1;
localparam [3:0] ENV_ATTACK  = 4'd2;
localparam [3:0] ENV_HOLD    = 4'd3;
localparam [3:0] ENV_DECAY   = 4'd4;
localparam [3:0] ENV_SUSTAIN = 4'd5;
localparam [3:0] ENV_RELEASE = 4'd6;
localparam [3:0] ENV_DONE    = 4'd7;
localparam       ENV_FLOOR   = 32'h0000_0100;

reg [16:0] seq_prescaler;
wire       tick_pulse = (seq_prescaler == 17'd0);

// Sequencer walk: seq_running drops after stage 17 of voice 47.  Stage
// advance is controlled by stage_done — 1-cycle for NOP stages, multi
// cycle when a sub-FSM is active.
reg        seq_running;
reg [5:0]  seq_voice;
reg [4:0]  seq_stage;

// Sub-FSM state.  Each multi-cycle stage has its own state machine
// that the outer sequencer dispatches to and waits on.
reg [4:0]  ramp0_sub;    // RAMP0_STEP_A sub-FSM (stage 3), up to state 28
reg [4:0]  vol_sub;      // VOL_COMPOSE  sub-FSM (stage 15)
reg [4:0]  noff_sub;     // NOTE_OFF_SERVICE sub-FSM (stage 1)
reg [4:0]  lfo_sub;      // Shared LFO_STEP sub-FSM (stages 5, 6) — the
                         // seq_stage bit picks which LFO slot to touch
reg [4:0]  mm_p_sub;     // MM_PITCH_ACCUM sub-FSM (stage 7)
reg [4:0]  mm_f_sub;     // MM_FILTER_ACCUM sub-FSM (stage 8)
reg [4:0]  pitch_sub;    // PITCH_COMPOSE sub-FSM (stage 9)
reg [4:0]  filt_sub;     // FILTER_COMPOSE sub-FSM (stage 10)
reg [4:0]  send_sub;     // SEND_COMPOSE sub-FSM (stage 16)
reg [4:0]  ramp1_sub;    // Phase 5c — RAMP1_STEP sub-FSM (stage 4)

localparam [4:0] SUB_IDLE = 5'd0;

// Per-voice release-pending bitmap — set by cpu_note_off when
// HW_ENVELOPE is enabled; stage 1 drains it by kicking off a RELEASE
// segment and clears the bit.
reg [31:0] noff_pending;

/* Pulse from RAMP0_STEP when a voice's volume envelope reaches
 * ENV_DONE.  Consumed by the active_mask always block so the SDK's
 * voice-stealer can reclaim slots only after the voice has TRULY
 * retired (release-ramp completed), not the moment cpu_note_off
 * fires.  Without this, hw_envelope=1 voices get their active_mask
 * bit cleared by cpu_note_off → SDK reuses the slot mid-release →
 * audible cut-off + click. */
reg        ramp0_done_pulse;
reg [4:0]  ramp0_done_voice;

// Change-detect cache for VOL_COMPOSE — only emit mixer writes when
// the composed vol_l/r differs from the prior tick's value for this
// voice.  Keeps the mixer write port mostly idle at steady state.
// Prev-vol change-detect.  Attempted MLAB ramstyle twice; both times
// it perturbed placement enough to push *unrelated* paths (GPU sprite
// engine, CPU LsuL1) over the edge.  Keep as plain registers and let
// Quartus pick.
reg [7:0] prev_vol_l [0:31];
reg [7:0] prev_vol_r [0:31];
// PITCH/FILTER change-detect attempted; cost was 1,300+ ALMs
// vs ~30 K writes/sec saved (0.03 % of clock cycles).  Mixer writes
// are single-cycle and queued, so the redundant traffic is free.
// Reverted in favour of always-emit; keep this comment as a hint
// that this trade-off has already been measured.

// Working registers populated during sub-FSM execution.
reg [31:0] ramp_level, ramp_rate, ramp_target, ramp_timer;
reg [3:0]  ramp_stage_new;
reg [3:0]  ramp_stage_cur;
reg        ramp_write_state;  // 1 = write W16 (stage), then optional W12/14/13/15

// Phase 3c pipeline: pre-register the level±rate sums and threshold
// bounds in sub-state 5'd10, then compare/decide/write in 5'd28.
// Breaks the ramp_rate → add → compare → state-mux → ramp_level
// critical-path chain into two shorter cycles.
reg [31:0] ramp_add_lvl;
reg [31:0] ramp_sub_lvl;
reg [31:0] ramp_dec_bound;
reg [31:0] ramp_rel_bound;

// Phase 4 — LFO sub-FSM working registers (shared lfo0/lfo1 scratch).
reg [15:0] lfo_phase;       // captured from vstate W22/W25
reg [15:0] lfo_rate;        // captured from W23/W26
reg [15:0] lfo_delay_rem;   // captured from W22/W25 upper half
reg [2:0]  lfo_wave;        // captured from W23/W26 (TRIANGLE only for now)
reg signed [31:0] lfo_output;  // freshly computed waveform value

// Phase 4 — mod matrix working registers.
// Narrow widths keep each multiply at 20×16 → one DSP, and the add
// chain registered per cycle so the whole pipe closes 100 MHz.
reg signed [19:0] mm_lfo0_out;      // captured from W24, range ±0x20000
reg signed [19:0] mm_lfo1_out;      // captured from W27
reg signed [19:0] mm_ramp1_out;     // Phase 5 fills it from VW_RAMP1
// Raw s36 products (s20 × s16).  Shifted by 16 and rewidened to s32
// when latched into the accumulator.  Phase 4 ships with the ramp1
// sources hard-zero (Phase 5 wires RAMP1_STEP), so the multiplies
// with ramp1_pitch / ramp1_filt are elided in fabric.
reg signed [35:0] mm_mul0_raw;    // lfo0_out × lfo0_pitch
reg signed [35:0] mm_mul1_raw;    // lfo1_out × lfo1_pitch (shared for filter lfo0×lfo0_filter)

// Scale-lookup pipeline registers.  seq_voice → 48:1 mux → s_lfoN_*
// is one cycle (FF→mux→FF), then s_lfoN_* → DSP input → mm_mulN_raw
// is the next cycle (FF→DSP→FF).  Splitting this hop keeps each
// FF-to-FF window shorter than 10 ns.
reg signed [15:0] s_lfo0_pitch;
reg signed [15:0] s_lfo1_pitch;
reg signed [15:0] s_lfo0_filter;
// Phase 5c — ramp1 mod-matrix scales.
reg signed [15:0] s_ramp1_pitch;
reg signed [15:0] s_ramp1_filter;
reg signed [35:0] mm_mul2_raw;     // mm_ramp1_out × s_ramp1_*

// Phase 5 — PITCH_COMPOSE working regs.
reg [31:0]        pitch_base_rate;   // W2 BASE_RATE snapshot
reg signed [15:0] pitch_bend_s16;    // chan_bank bend (signed 14-bit, sign-ext)
reg signed [31:0] pitch_ofs_s32;     // W28 PITCH_OFS capture
reg signed [31:0] pitch_bend_prod;   // bend × bend_range_cents (s16 × s12)
reg signed [31:0] pitch_cents_sum;   // running sum for octave-reduce
reg signed [3:0]  pitch_octaves;     // accumulated octave shift
reg [31:0]        pitch_mult_q16;    // 0x10000 + mult_frac, shifted by octaves
reg signed [47:0] pitch_final_rate;  // BASE_RATE × mult >> 16

// Phase 5b — FILTER_COMPOSE working regs.  Same LUT/octave pipeline
// as PITCH_COMPOSE but scales the raw SF2 FC cents (INITIAL_FC +
// FILTER_OFS + CH_BRIGHTNESS) into a Q0.16 mixer cutoff via the
// HAL formula:  cutoff = (2^(c/1200) × 65536 × 22) >> 16.
reg signed [31:0] filt_cents_sum;
reg signed [3:0]  filt_octaves;
reg signed [31:0] filt_bright_prod;   // (brightness-64) × SCALE
reg [31:0]        filt_mult_q16;
reg [47:0]        filt_cutoff_prod;   // mult × 22 — needs 32+5 bits
reg               filt_bypass;         // cents >= 13500 → disable filter
reg signed [31:0] mm_pitch_acc;     // final accumulator, written to W28
reg signed [31:0] mm_filter_acc;    // final accumulator, written to W29

// NOTE_OFF_SERVICE release-rate multiplier:
//   (level 0..0x10000) × (inv_release_ticks 0..0x10000) → 34-bit product.
//   Computed inline in state 5'd4 and captured into ramp_rate; shifted
//   right by 16 to collapse the Q16 reciprocal.  17×17 fits one 18×18 DSP.
wire [33:0] noff_rel_prod = {17'h0, ramp_level[16:0]}
                          * {17'h0, vstate_b_q[16:0]};

reg [7:0]  cmp_env_vol;
reg [7:0]  cmp_voice_base_vol;
reg [15:0] cmp_pan_base;
reg [7:0]  cmp_midi_ch;
// Phase 3b: CPU pre-combines vol×expr/127 and (pan-64)*500/63 so
// the fabric reads scalar factors instead of re-dividing every tick.
reg [7:0]  cmp_ch_vol_combined;
reg signed [15:0] cmp_midi_pan;
// Pan-split pipeline registers (see vol_sub 5'd11 → 5'd17).
reg [16:0] pan_scaled;      // cmp_vol × (500 ± pan), max 127500 fits u17
reg        pan_dir;         // 0: reciprocal result → vol_r; 1: → vol_l
reg [7:0]  cmp_pan_side;    // the side of the pan split that gets vol as-is
reg [15:0] cmp_vol;        // scratch during compose — never > 255, u16 keeps DSP ≤ 27×8
reg signed [15:0] cmp_pan;
reg [7:0]  cmp_vol_l, cmp_vol_r;

// stage_done: pulse (1 cycle) from whichever state machine is active.
// Drives the outer sequencer's (voice, stage) advance.
reg stage_done;

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        seq_prescaler <= SEQ_TICK_RELOAD;
        seq_running   <= 1'b0;
        seq_voice     <= 6'd0;
        seq_stage     <= 5'd0;
        tick_count    <= 32'd0;
        ramp0_sub     <= SUB_IDLE;
        ramp1_sub     <= SUB_IDLE;
        vol_sub       <= SUB_IDLE;
        noff_sub      <= SUB_IDLE;
        lfo_sub       <= SUB_IDLE;
        mm_p_sub      <= SUB_IDLE;
        mm_f_sub      <= SUB_IDLE;
        pitch_sub     <= SUB_IDLE;
        filt_sub      <= SUB_IDLE;
        send_sub      <= SUB_IDLE;
        ramp1_meta_wr_fsm  <= 1'b0;
        ramp1_level_wr_fsm <= 1'b0;
        noff_pending  <= 32'd0;
        ramp0_done_pulse <= 1'b0;
        ramp0_done_voice <= 5'd0;
        stage_done    <= 1'b0;
        seq_vstate_wr   <= 1'b0;
        seq_vstate_addr <= 11'd0;
        seq_vstate_data <= 32'd0;
        vol_mix_wr       <= 1'b0;
        vol_mix_field    <= 4'd0;
        vol_mix_sel      <= 6'd0;
        vol_mix_wdata    <= 32'd0;
        pitch_mix_wr     <= 1'b0;
        pitch_mix_field  <= 4'd0;
        pitch_mix_sel    <= 6'd0;
        pitch_mix_wdata  <= 32'd0;
        filt_mix_wr      <= 1'b0;
        filt_mix_field   <= 4'd0;
        filt_mix_sel     <= 6'd0;
        filt_mix_wdata   <= 32'd0;
        send_mix_wr      <= 1'b0;
        send_mix_field   <= 4'd0;
        send_mix_sel     <= 6'd0;
        send_mix_wdata   <= 32'd0;
    end else begin
        // Defaults each cycle; overrides below.
        stage_done    <= 1'b0;
        seq_vstate_wr <= 1'b0;
        vol_mix_wr    <= 1'b0;
        pitch_mix_wr  <= 1'b0;
        filt_mix_wr   <= 1'b0;
        send_mix_wr   <= 1'b0;
        ramp0_done_pulse <= 1'b0;  // 1-cycle pulse — see ENV_DONE in RAMP0_STEP

        // Prescaler (Phase 2 unchanged).
        if (seq_prescaler == 17'd0)
            seq_prescaler <= SEQ_TICK_RELOAD;
        else
            seq_prescaler <= seq_prescaler - 17'd1;

        // CPU note_off strobe → set release-pending bit (only matters
        // when HW owns envelope; the SW path clears it by skipping
        // stage 1 in that mode).
        if (cpu_note_off && hw_envelope_enable)
            noff_pending[cpu_note_off_voice] <= 1'b1;

        // -------- Tick entry --------
        if (tick_pulse) begin
            seq_running <= 1'b1;
            seq_voice   <= 6'd0;
            seq_stage   <= 5'd0;
            tick_count  <= tick_count + 32'd1;
            ramp0_sub   <= SUB_IDLE;
            ramp1_sub   <= SUB_IDLE;
            vol_sub     <= SUB_IDLE;
            noff_sub    <= SUB_IDLE;
            lfo_sub     <= SUB_IDLE;
            mm_p_sub    <= SUB_IDLE;
            mm_f_sub    <= SUB_IDLE;
            pitch_sub   <= SUB_IDLE;
            filt_sub    <= SUB_IDLE;
            send_sub    <= SUB_IDLE;
        end

        // -------- NOTE_OFF_SERVICE (stage 1) sub-FSM --------
        //
        // Fires when seq_stage==1 and noff_pending[seq_voice]==1.  Reads
        // current level + release_ticks, computes release_rate via
        // integer divide, writes back {stage=RELEASE, rate=release_rate,
        // target=0}.  No-ops when noff_pending bit is clear.
        if (noff_sub == SUB_IDLE &&
            seq_running && seq_stage == 5'd1 &&
            hw_envelope_enable && noff_pending[seq_voice]) begin
            noff_sub      <= 5'd1;
            seq_vstate_addr <= {seq_voice, VW_RAMP0_LEVEL};   // read level
        end else begin
            case (noff_sub)
                5'd1: begin noff_sub <= 5'd2; end  // addr phase 1 latency
                5'd2: begin
                    ramp_level    <= vstate_b_q;
                    seq_vstate_addr <= {seq_voice, VW_RAMP0_RELEASE_T};
                    noff_sub      <= 5'd3;
                end
                5'd3: begin noff_sub <= 5'd4; end
                5'd4: begin
                    // Phase 3c: CPU pre-bakes inv_release = 0x10000 /
                    // release_ticks at voice-load time.  Fabric turns
                    // the per-NOTE_OFF divide into a 17×16 multiply:
                    //   rate = (level × inv_release) >> 16
                    // matching SW's `level / release_ticks` to within
                    // the inv's Q16 quantization (<1 unit for musical
                    // release_ticks).  Single DSP, FF→DSP→FF.
                    ramp_rate   <= {15'd0, noff_rel_prod[32:16]};
                    ramp_target <= 32'd0;
                    noff_sub    <= 5'd5;
                end
                5'd5: begin
                    // Write RELEASE stage + clamp rate to >= 1.
                    seq_vstate_wr   <= 1'b1;
                    seq_vstate_addr <= {seq_voice, VW_RAMP0_RATE};
                    seq_vstate_data <=(ramp_rate == 32'd0) ? 32'd1 : ramp_rate;
                    noff_sub      <= 5'd6;
                end
                5'd6: begin
                    seq_vstate_wr   <= 1'b1;
                    seq_vstate_addr <= {seq_voice, VW_RAMP0_TARGET};
                    seq_vstate_data <=32'd0;
                    noff_sub      <= 5'd7;
                end
                5'd7: begin
                    seq_vstate_wr   <= 1'b1;
                    seq_vstate_addr <= {seq_voice, VW_RAMP0_STAGE};
                    seq_vstate_data <={28'd0, ENV_RELEASE};
                    noff_pending[seq_voice] <= 1'b0;
                    noff_sub      <= 5'd8;
                end
                5'd8: begin
                    noff_sub   <= SUB_IDLE;
                    stage_done <= 1'b1;
                end
                default: ;
            endcase
        end

        // NOP fast-path for stage 1 when no release pending.
        if (seq_running && seq_stage == 5'd1 && noff_sub == SUB_IDLE &&
            !(hw_envelope_enable && noff_pending[seq_voice]))
            stage_done <= 1'b1;

        // -------- RAMP0_STEP_A (stage 3) sub-FSM --------
        //
        // Reads stage → dispatches per env_advance: DELAY/HOLD tick the
        // timer; ATTACK/DECAY/RELEASE advance level; transitions load
        // baked segment parameters.  Gated by hw_envelope_enable.
        if (ramp0_sub == SUB_IDLE &&
            seq_running && seq_stage == 5'd3 && hw_envelope_enable) begin
            ramp0_sub     <= 5'd1;
            seq_vstate_addr <= {seq_voice, VW_RAMP0_STAGE};
        end else begin
            case (ramp0_sub)
                5'd1: ramp0_sub <= 5'd2;  // stage read latency
                5'd2: begin
                    ramp_stage_cur <= vstate_b_q[3:0];
                    ramp_stage_new <= vstate_b_q[3:0];
                    // Branch on stage: OFF/DONE/SUSTAIN skip everything.
                    if (vstate_b_q[3:0] == ENV_OFF ||
                        vstate_b_q[3:0] == ENV_DONE ||
                        vstate_b_q[3:0] == ENV_SUSTAIN) begin
                        ramp0_sub  <= SUB_IDLE;
                        stage_done <= 1'b1;
                    end else if (vstate_b_q[3:0] == ENV_DELAY ||
                                 vstate_b_q[3:0] == ENV_HOLD) begin
                        // Timer stages: read timer next.
                        seq_vstate_addr <= {seq_voice, VW_RAMP0_TIMER};
                        ramp0_sub     <= 5'd3;
                    end else begin
                        // ATTACK/DECAY/RELEASE: read level + rate + target.
                        seq_vstate_addr <= {seq_voice, VW_RAMP0_LEVEL};
                        ramp0_sub     <= 5'd5;
                    end
                end

                // --- Timer-stage path (DELAY / HOLD) ---
                5'd3: ramp0_sub <= 5'd4;
                5'd4: begin
                    ramp_timer <= vstate_b_q - 32'd1;
                    if (vstate_b_q <= 32'd1) begin
                        // Timer expired — transition.  DELAY → ATTACK,
                        // HOLD → DECAY.  Write new stage + preload rate
                        // + target from baked params (read next).
                        seq_vstate_addr <= (ramp_stage_cur == ENV_DELAY)
                            ? {seq_voice, VW_RAMP0_ATTACK_R}
                            : {seq_voice, VW_RAMP0_DECAY_R};
                        ramp_stage_new <= (ramp_stage_cur == ENV_DELAY)
                            ? ENV_ATTACK : ENV_DECAY;
                        ramp0_sub <= 5'd14;  // branch to transition finalize
                    end else begin
                        // Timer still counting: write back decremented value.
                        seq_vstate_wr   <= 1'b1;
                        seq_vstate_addr <= {seq_voice, VW_RAMP0_TIMER};
                        seq_vstate_data <=vstate_b_q - 32'd1;
                        ramp0_sub     <= 5'd13;  // done
                    end
                end

                // --- Level-stage path (ATTACK / DECAY / RELEASE) ---
                5'd5: ramp0_sub <= 5'd6;
                5'd6: begin
                    ramp_level    <= vstate_b_q;
                    seq_vstate_addr <= {seq_voice, VW_RAMP0_RATE};
                    ramp0_sub     <= 5'd7;
                end
                5'd7: ramp0_sub <= 5'd8;
                5'd8: begin
                    ramp_rate     <= vstate_b_q;
                    seq_vstate_addr <= {seq_voice, VW_RAMP0_TARGET};
                    ramp0_sub     <= 5'd9;
                end
                5'd9: ramp0_sub <= 5'd10;
                5'd10: begin
                    // Cycle A of the RAMP0 step: capture TARGET and
                    // pre-compute all level±rate and threshold+rate
                    // adders in parallel (FF-to-FF paths).  No
                    // comparison or state transition yet — that moves
                    // to 5'd28 to break the critical path.
                    ramp_target    <= vstate_b_q;
                    ramp_add_lvl   <= ramp_level + ramp_rate;
                    ramp_sub_lvl   <= ramp_level - ramp_rate;
                    ramp_dec_bound <= vstate_b_q + ramp_rate;
                    ramp_rel_bound <= ENV_FLOOR + ramp_rate;
                    ramp0_sub      <= 5'd28;
                end

                // Cycle B of the RAMP0 step: compare registered sums
                // against registered bounds, decide transition, write
                // back.  Fan-in is just FF→compare→mux→FF.
                5'd28: begin
                    if (ramp_stage_cur == ENV_ATTACK) begin
                        if (ramp_add_lvl >= 32'h0001_0000) begin
                            ramp_level     <= 32'h0001_0000;
                            // ATTACK→HOLD or →DECAY, need hold_ticks.
                            seq_vstate_addr<= {seq_voice, VW_RAMP0_HOLD_T};
                            ramp0_sub      <= 5'd11;
                        end else begin
                            ramp_level <= ramp_add_lvl;
                            ramp0_sub  <= 5'd15;
                        end
                    end else if (ramp_stage_cur == ENV_DECAY) begin
                        if (ramp_level <= ramp_dec_bound) begin
                            ramp_level     <= ramp_target;  // sustain floor
                            ramp_stage_new <= ENV_SUSTAIN;
                            ramp_rate      <= 32'd0;
                            ramp0_sub      <= 5'd12;
                        end else begin
                            ramp_level <= ramp_sub_lvl;
                            ramp0_sub  <= 5'd15;
                        end
                    end else begin  // RELEASE
                        if (ramp_level <= ramp_rel_bound) begin
                            ramp_level     <= 32'd0;
                            ramp_stage_new <= ENV_DONE;
                            ramp_rate      <= 32'd0;
                            ramp0_sub      <= 5'd12;
                            /* Voice fully retired — pulse the
                             * active_mask clear handler so the SDK's
                             * voice-stealer can reclaim this slot. */
                            ramp0_done_pulse <= 1'b1;
                            ramp0_done_voice <= seq_voice;
                        end else begin
                            ramp_level <= ramp_sub_lvl;
                            ramp0_sub  <= 5'd15;
                        end
                    end
                end

                5'd11: ramp0_sub <= 5'd16;  // HOLD_T read latency → branch
                5'd16: begin
                    if (vstate_b_q > 32'd0) begin
                        // Enter HOLD: write timer=hold_ticks, stage=HOLD, rate=0.
                        seq_vstate_wr   <= 1'b1;
                        seq_vstate_addr <= {seq_voice, VW_RAMP0_TIMER};
                        seq_vstate_data <=vstate_b_q;
                        ramp_stage_new<= ENV_HOLD;
                        ramp_rate     <= 32'd0;
                        ramp0_sub     <= 5'd17;
                    end else begin
                        // Skip HOLD → DECAY: load decay_rate + sustain_level.
                        seq_vstate_addr <= {seq_voice, VW_RAMP0_DECAY_R};
                        ramp_stage_new<= ENV_DECAY;
                        ramp0_sub     <= 5'd14;
                    end
                end
                5'd17: begin  // write stage=HOLD, level already at 0x10000
                    seq_vstate_wr   <= 1'b1;
                    seq_vstate_addr <= {seq_voice, VW_RAMP0_RATE};
                    seq_vstate_data <=32'd0;
                    ramp0_sub     <= 5'd18;
                end
                5'd18: begin
                    seq_vstate_wr   <= 1'b1;
                    seq_vstate_addr <= {seq_voice, VW_RAMP0_LEVEL};
                    seq_vstate_data <=32'h0001_0000;
                    ramp0_sub     <= 5'd19;
                end
                5'd19: begin
                    seq_vstate_wr   <= 1'b1;
                    seq_vstate_addr <= {seq_voice, VW_RAMP0_STAGE};
                    seq_vstate_data <={28'd0, ramp_stage_new};
                    ramp0_sub     <= 5'd13;
                end

                // ATTACK→DECAY or HOLD→DECAY transition: need decay_rate
                // and sustain_level, then write back.
                5'd14: ramp0_sub <= 5'd20;  // DECAY_R read latency
                5'd20: begin
                    ramp_rate     <= vstate_b_q;
                    seq_vstate_addr <= {seq_voice, VW_RAMP0_SUSTAIN};
                    ramp0_sub     <= 5'd21;
                end
                5'd21: ramp0_sub <= 5'd22;
                5'd22: begin
                    ramp_target <= vstate_b_q;
                    // Write all fields (rate, target, level=0x10000 if from ATTACK, stage).
                    seq_vstate_wr   <= 1'b1;
                    seq_vstate_addr <= {seq_voice, VW_RAMP0_RATE};
                    seq_vstate_data <=ramp_rate;
                    ramp0_sub     <= 5'd23;
                end
                5'd23: begin
                    seq_vstate_wr   <= 1'b1;
                    seq_vstate_addr <= {seq_voice, VW_RAMP0_TARGET};
                    seq_vstate_data <=ramp_target;
                    ramp0_sub     <= 5'd24;
                end
                5'd24: begin
                    seq_vstate_wr   <= 1'b1;
                    seq_vstate_addr <= {seq_voice, VW_RAMP0_LEVEL};
                    seq_vstate_data <=32'h0001_0000;  // always enter DECAY from peak
                    ramp0_sub     <= 5'd25;
                end
                5'd25: begin
                    seq_vstate_wr   <= 1'b1;
                    seq_vstate_addr <= {seq_voice, VW_RAMP0_STAGE};
                    seq_vstate_data <={28'd0, ramp_stage_new};
                    ramp0_sub     <= 5'd13;
                end

                // 5'd12: DECAY→SUSTAIN or RELEASE→DONE with level/rate/stage write-back.
                5'd12: begin
                    seq_vstate_wr   <= 1'b1;
                    seq_vstate_addr <= {seq_voice, VW_RAMP0_LEVEL};
                    seq_vstate_data <=ramp_level;
                    ramp0_sub     <= 5'd26;
                end
                5'd26: begin
                    seq_vstate_wr   <= 1'b1;
                    seq_vstate_addr <= {seq_voice, VW_RAMP0_RATE};
                    seq_vstate_data <=ramp_rate;
                    ramp0_sub     <= 5'd27;
                end
                5'd27: begin
                    seq_vstate_wr   <= 1'b1;
                    seq_vstate_addr <= {seq_voice, VW_RAMP0_STAGE};
                    seq_vstate_data <={28'd0, ramp_stage_new};
                    ramp0_sub     <= 5'd13;
                end

                // 5'd15: write level only (ATTACK/DECAY/RELEASE ongoing).
                5'd15: begin
                    seq_vstate_wr   <= 1'b1;
                    seq_vstate_addr <= {seq_voice, VW_RAMP0_LEVEL};
                    seq_vstate_data <=ramp_level;
                    ramp0_sub     <= 5'd13;
                end

                // 5'd13: common done path.
                5'd13: begin
                    ramp0_sub  <= SUB_IDLE;
                    stage_done <= 1'b1;
                end
                default: ;
            endcase
        end

        // -------- VOL_COMPOSE (stage 15) sub-FSM --------
        //
        // Phase 3c: fabric compose re-enabled with narrow widths.
        // cmp_vol is u16 (never > 255), pan_scaled u17, reciprocal
        // constant u17 — every intermediate multiply now fits a single
        // 27×27 DSP and the pan-split is split across two cycles.
        if (vol_sub == SUB_IDLE &&
            seq_running && seq_stage == 5'd15 && hw_envelope_enable) begin
            vol_sub         <= 5'd30;
            seq_vstate_addr <= {seq_voice, VW_RAMP0_STAGE};
        end else begin
            case (vol_sub)
                5'd30: vol_sub <= 5'd31;
                5'd31: begin
                    if (vstate_b_q[3:0] == ENV_OFF ||
                        vstate_b_q[3:0] == ENV_DONE) begin
                        vol_sub    <= SUB_IDLE;
                        stage_done <= 1'b1;
                    end else begin
                        seq_vstate_addr <= {seq_voice, VW_RAMP0_LEVEL};
                        vol_sub         <= 5'd1;
                    end
                end
                5'd1: vol_sub <= 5'd2;
                5'd2: begin
                    // Clamp level → env_vol (0..255).
                    if ($signed(vstate_b_q) <= 0)
                        cmp_env_vol <= 8'd0;
                    else if (vstate_b_q >= 32'h0001_0000)
                        cmp_env_vol <= 8'd255;
                    else
                        cmp_env_vol <= vstate_b_q[15:8];
                    seq_vstate_addr <= {seq_voice, VW_MIDI_CH_BV_PAN};
                    vol_sub       <= 5'd3;
                end
                5'd3: vol_sub <= 5'd4;
                5'd4: begin
                    // W5 packs {pan_base[15:0], voice_base_vol[7:0], midi_ch[7:0]}
                    // per hal/awe.c's vstate_write for voice identity.
                    cmp_midi_ch        <= vstate_b_q[7:0];
                    cmp_voice_base_vol <= vstate_b_q[15:8];
                    cmp_pan_base       <= vstate_b_q[31:16];
                    // Latch channel-bank read address (W0 of this ch).
                    // M10K sync read lands on chan_b_q next cycle.
                    chan_b_addr        <= {vstate_b_q[3:0], 2'd0};
                    vol_sub <= 5'd20;
                end
                5'd20: vol_sub <= 5'd5;  // M10K read latency
                5'd5: begin
                    // Phase 3b: channel bank W0 now carries CPU-precomputed
                    // factors so the fabric dodges /127 and /63 entirely.
                    //   [31:24] = ch_vol_combined  = (vol × expr) / 127
                    //   [23: 8] = midi_pan_scaled  = ((pan-64) × 500) / 63 (signed)
                    //   [ 7: 0] = sustain
                    cmp_ch_vol_combined <= chan_b_q[31:24];
                    cmp_midi_pan        <= $signed(chan_b_q[23:8]);
                    vol_sub <= 5'd6;
                end
                5'd6: begin
                    // vol = (env_vol × voice_base_vol) >> 8
                    cmp_vol <= (cmp_env_vol * cmp_voice_base_vol) >> 8;
                    vol_sub <= 5'd7;
                end
                5'd7: begin
                    // vol = (vol × ch_vol_combined) >> 7  — no /127 in fabric
                    cmp_vol <= (cmp_vol * cmp_ch_vol_combined) >> 7;
                    vol_sub <= 5'd8;
                end
                5'd8: begin
                    cmp_vol <= (cmp_vol * master_vol) >> 8;
                    vol_sub <= 5'd9;
                end
                5'd9: begin
                    if (cmp_vol > 16'd255) cmp_vol <= 16'd255;
                    // pan = pan_base + midi_pan_scaled (CPU-precomputed, signed)
                    cmp_pan <= $signed(cmp_pan_base) + cmp_midi_pan;
                    vol_sub <= 5'd10;
                end
                5'd10: begin
                    if (cmp_pan < -16'sd500) cmp_pan <= -16'sd500;
                    if (cmp_pan >  16'sd500) cmp_pan <=  16'sd500;
                    vol_sub <= 5'd11;
                end
                5'd11: begin
                    // Pan-split reciprocal multiply: (N × 67109) >> 25 == N/500
                    // over the full input range.  Pipelined across two
                    // cycles (first the u8×u10 scale, then the ×67109
                    // reciprocal) so each cycle carries only ONE DSP
                    // multiply and the 100 MHz clock has headroom.
                    //   stage 11: compute cmp_vol × (500 ± pan) → u18 in pan_scaled
                    //   stage 12: (pan_scaled × 67109) >> 25 → u8 vol_l or vol_r
                    if ($signed(cmp_pan) <= 16'sd0) begin
                        pan_scaled <= cmp_vol[7:0] * (17'sd500 + cmp_pan);
                        pan_dir    <= 1'b0;   // 0 = result goes to vol_r
                    end else begin
                        pan_scaled <= cmp_vol[7:0] * (17'sd500 - cmp_pan);
                        pan_dir    <= 1'b1;   // 1 = result goes to vol_l
                    end
                    cmp_pan_side <= cmp_vol[7:0];   // the un-scaled side
                    vol_sub <= 5'd17;
                end
                5'd17: begin
                    // Second multiply: pan_scaled × 67109 >> 25.
                    // pan_scaled is u18, constant 17-bit → single DSP.
                    if (pan_dir == 1'b0) begin
                        cmp_vol_l <= cmp_pan_side;
                        cmp_vol_r <= (pan_scaled * 17'd67109) >> 25;
                    end else begin
                        cmp_vol_l <= (pan_scaled * 17'd67109) >> 25;
                        cmp_vol_r <= cmp_pan_side;
                    end
                    vol_sub <= 5'd12;
                end
                5'd12: begin
                    // Change-detect.  If either changed, emit mixer write.
                    if (cmp_vol_l != prev_vol_l[seq_voice] ||
                        cmp_vol_r != prev_vol_r[seq_voice]) begin
                        prev_vol_l[seq_voice] <= cmp_vol_l;
                        prev_vol_r[seq_voice] <= cmp_vol_r;
                        // Emit VOL_LR, VOL_TARGET (=LR), VOL_RATE=0.
                        vol_mix_wr    <= 1'b1;
                        vol_mix_sel   <= seq_voice;
                        vol_mix_field <= 5'd6;  // VOL_LR
                        vol_mix_wdata <= {16'd0, cmp_vol_r, cmp_vol_l};
                        vol_sub       <= 5'd13;
                    end else begin
                        vol_sub    <= SUB_IDLE;
                        stage_done <= 1'b1;
                    end
                end
                5'd13: begin
                    vol_mix_wr    <= 1'b1;
                    vol_mix_sel   <= seq_voice;
                    vol_mix_field <= 5'd9;  // VOL_TARGET
                    vol_mix_wdata <= {16'd0, cmp_vol_r, cmp_vol_l};
                    vol_sub       <= 5'd14;
                end
                5'd14: begin
                    vol_mix_wr    <= 1'b1;
                    vol_mix_sel   <= seq_voice;
                    vol_mix_field <= 5'd10;  // VOL_RATE
                    vol_mix_wdata <= 32'd0;
                    vol_sub       <= 5'd15;
                end
                5'd15: begin
                    vol_sub    <= SUB_IDLE;
                    stage_done <= 1'b1;
                end
                default: ;
            endcase
        end

        // -------- RAMP1_STEP (stage 4) sub-FSM — Phase 5c --------
        //
        // Simplified attack-sustain-release mod envelope.  Reads
        // {stage, rate} from ramp1_meta_ram and current level from
        // ramp1_level_ram, advances level toward 0x10000 (ATTACK) or
        // toward 0 (RELEASE), writes back, and captures the level
        // into mm_ramp1_out for stages 7/8 to consume.  Sub-states
        // mirror the sequencing pattern of RAMP0_STEP_A but compressed
        // for the simpler model — DAHDSR detail is RAMP0's job.
        if (ramp1_sub == SUB_IDLE &&
            seq_running && seq_stage == 5'd4 && hw_envelope_enable) begin
            ramp1_sub    <= 5'd1;
            ramp1_b_addr <= seq_voice[4:0];
        end else begin
            case (ramp1_sub)
                5'd1: ramp1_sub <= 5'd2;   // BRAM read latency
                5'd2: begin
                    // ramp1_meta_q = {stage[31:28], rate[27:0]}
                    // ramp1_level_q = current level Q16.16, range 0..0x10000.
                    // mm_ramp1_out is signed [19:0] with the same Q16 scale
                    // as mm_lfo0_out so the downstream s20×s16 multiply
                    // yields cents in the upper 20 bits of the 36-bit
                    // product (matching the LFO contribution's units).
                    mm_ramp1_out <= {3'd0, ramp1_level_q[16:0]};
                    case (ramp1_meta_q[31:28])
                        4'd2: begin   // ENV_ATTACK
                            ramp1_a_addr_fsm <= seq_voice[4:0];
                            if (ramp1_level_q + {4'd0, ramp1_meta_q[27:0]}
                                >= 32'h0001_0000) begin
                                // Reached peak — pin to 1.0, transition
                                // to SUSTAIN.
                                ramp1_level_a_wdata_fsm <= 32'h0001_0000;
                                ramp1_meta_a_wdata_fsm  <=
                                    {4'd5, ramp1_meta_q[27:0]};  // ENV_SUSTAIN
                                ramp1_level_wr_fsm <= 1'b1;
                                ramp1_meta_wr_fsm  <= 1'b1;
                                mm_ramp1_out <= 20'sh10000;  // 1.0 in Q16
                            end else begin
                                ramp1_level_a_wdata_fsm <=
                                    ramp1_level_q + {4'd0, ramp1_meta_q[27:0]};
                                ramp1_level_wr_fsm <= 1'b1;
                            end
                        end
                        4'd6: begin   // ENV_RELEASE
                            ramp1_a_addr_fsm <= seq_voice[4:0];
                            if (ramp1_level_q <= {4'd0, ramp1_meta_q[27:0]} ||
                                ramp1_level_q <= ENV_FLOOR) begin
                                ramp1_level_a_wdata_fsm <= 32'd0;
                                ramp1_meta_a_wdata_fsm  <=
                                    {4'd7, ramp1_meta_q[27:0]};  // ENV_DONE
                                ramp1_level_wr_fsm <= 1'b1;
                                ramp1_meta_wr_fsm  <= 1'b1;
                                mm_ramp1_out <= 20'sd0;
                            end else begin
                                ramp1_level_a_wdata_fsm <=
                                    ramp1_level_q - {4'd0, ramp1_meta_q[27:0]};
                                ramp1_level_wr_fsm <= 1'b1;
                            end
                        end
                        4'd5: begin   // ENV_SUSTAIN — hold at peak
                            mm_ramp1_out <= 20'sh10000;
                        end
                        default: begin   // OFF / DONE — output silent
                            mm_ramp1_out <= 20'sd0;
                        end
                    endcase
                    ramp1_sub <= 5'd3;
                end
                5'd3: begin
                    ramp1_meta_wr_fsm  <= 1'b0;
                    ramp1_level_wr_fsm <= 1'b0;
                    ramp1_sub  <= SUB_IDLE;
                    stage_done <= 1'b1;
                end
                default: ;
            endcase
        end

        // -------- LFO_STEP (stages 5, 6) shared sub-FSM --------
        //
        // Stage 5 touches LFO0 slot (VW_LFO0_*), stage 6 touches LFO1
        // (VW_LFO1_*).  The sub-FSM is the same; `is_lfo1` selects
        // which word triplet is addressed.  Delay_rem > 0 → decrement,
        // emit 0 output.  Otherwise advance phase, compute triangle,
        // write phase+output back.  Triangle waveform only (SF2 mod /
        // vib LFOs default to triangle on these cores; other waveform
        // codes fall through to triangle in this build).
        if (lfo_sub == SUB_IDLE &&
            seq_running && (seq_stage == 5'd5 || seq_stage == 5'd6) &&
            hw_envelope_enable) begin
            // Kick off: read STATE (phase + delay_rem).
            lfo_sub <= 5'd1;
            seq_vstate_addr <= {seq_voice,
                (seq_stage == 5'd5) ? VW_LFO0_STATE : VW_LFO1_STATE};
        end else begin
            case (lfo_sub)
                5'd1: lfo_sub <= 5'd2;  // RAM read latency
                5'd2: begin
                    // Capture phase + delay_rem; request CONFIG read.
                    lfo_phase     <= vstate_b_q[15:0];
                    lfo_delay_rem <= vstate_b_q[31:16];
                    seq_vstate_addr <= {seq_voice,
                        (seq_stage == 5'd5) ? VW_LFO0_CONFIG : VW_LFO1_CONFIG};
                    lfo_sub <= 5'd3;
                end
                5'd3: lfo_sub <= 5'd4;
                5'd4: begin
                    // Capture rate + wave.  Always-registered to keep
                    // the compute path FF→DSP→FF.
                    lfo_rate <= vstate_b_q[15:0];
                    lfo_wave <= vstate_b_q[18:16];
                    lfo_sub  <= 5'd5;
                end
                5'd5: begin
                    // Decide: still in delay, or advancing?
                    if (lfo_delay_rem != 16'd0) begin
                        // Write STATE back with decremented delay_rem
                        // and phase unchanged.
                        seq_vstate_wr   <= 1'b1;
                        seq_vstate_addr <= {seq_voice,
                            (seq_stage == 5'd5) ? VW_LFO0_STATE : VW_LFO1_STATE};
                        seq_vstate_data <= {lfo_delay_rem - 16'd1, lfo_phase};
                        lfo_output      <= 32'sd0;     // delayed = silent
                        lfo_sub         <= 5'd7;
                    end else begin
                        // Advance phase; compute triangle from new phase.
                        lfo_phase <= lfo_phase + lfo_rate;
                        lfo_sub   <= 5'd6;
                    end
                end
                5'd6: begin
                    // Compute triangle waveform from the just-advanced
                    // lfo_phase.  Match of_smp_voice.c:triangle_wave().
                    //   phase<0x4000 : out = phase<<2          (0..0x10000)
                    //   phase<0xC000 : out = 0x20000-(phase<<2) (0x10000..-0x10000)
                    //   else         : out = (phase<<2)-0x40000 (-0x10000..0)
                    if (lfo_phase < 16'h4000)
                        lfo_output <= {14'd0, lfo_phase, 2'b00};
                    else if (lfo_phase < 16'hC000)
                        lfo_output <= 32'sd131072 - {14'd0, lfo_phase, 2'b00};
                    else
                        lfo_output <= {14'd0, lfo_phase, 2'b00} - 32'sd262144;
                    // Write STATE back (new phase, delay_rem stays 0).
                    seq_vstate_wr   <= 1'b1;
                    seq_vstate_addr <= {seq_voice,
                        (seq_stage == 5'd5) ? VW_LFO0_STATE : VW_LFO1_STATE};
                    seq_vstate_data <= {16'd0, lfo_phase};
                    lfo_sub <= 5'd7;
                end
                5'd7: begin
                    // Write OUTPUT word.
                    seq_vstate_wr   <= 1'b1;
                    seq_vstate_addr <= {seq_voice,
                        (seq_stage == 5'd5) ? VW_LFO0_OUTPUT : VW_LFO1_OUTPUT};
                    seq_vstate_data <= lfo_output;
                    lfo_sub <= 5'd8;
                end
                5'd8: begin
                    lfo_sub    <= SUB_IDLE;
                    stage_done <= 1'b1;
                end
                default: ;
            endcase
        end

        // -------- MM_PITCH_ACCUM (stage 7) sub-FSM --------
        //
        //   PITCH_OFS = Σ (source_i × scale_i) >>> 16
        // with sources = {LFO0_OUT, LFO1_OUT, RAMP1_OUT}.  One multiply
        // per cycle, registered; the >>> 16 shift is free because we
        // slice the product directly.  Each multiply is s20 × s16 →
        // one DSP; the accumulator path is a single 32-bit add per
        // cycle.  Both keep the FF→DSP→FF / FF→+→FF hops short.
        if (mm_p_sub == SUB_IDLE &&
            seq_running && seq_stage == 5'd7 && hw_envelope_enable) begin
            mm_p_sub        <= 5'd1;
            seq_vstate_addr <= {seq_voice, VW_LFO0_OUTPUT};
        end else begin
            case (mm_p_sub)
                5'd1: mm_p_sub <= 5'd2;
                5'd2: begin
                    mm_lfo0_out     <= vstate_b_q[19:0];
                    seq_vstate_addr <= {seq_voice, VW_LFO1_OUTPUT};
                    mm_p_sub <= 5'd3;
                end
                5'd3: begin
                    // Latch mm_scale read address now — the M10Ks need
                    // one cycle to surface the q outputs.
                    mm_s_b_addr <= seq_voice[4:0];
                    mm_p_sub    <= 5'd4;
                end
                5'd4: begin
                    mm_lfo1_out  <= vstate_b_q[19:0];
                    // mm_ramp1_out is set by RAMP1_STEP at stage 4 —
                    // already valid for this voice when stage 7 runs.
                    // Capture scales from the compacted M10K outputs.
                    // Pre-register before the DSP input so the FF→DSP
                    // →FF hop in 5'd5 stays under 10 ns.
                    s_lfo0_pitch   <= mm_s_lfo0_p_q;
                    s_lfo1_pitch   <= mm_s_lfo1_p_q;
                    s_lfo0_filter  <= mm_s_lfo0_f_q;
                    s_ramp1_pitch  <= mm_s_ramp1_p_q;   // Phase 5c
                    s_ramp1_filter <= mm_s_ramp1_f_q;   // Phase 5c
                    mm_p_sub       <= 5'd5;
                end
                5'd5: begin
                    // Three parallel multiplies (LFO0+LFO1+RAMP1 into pitch).
                    // Operands are all FFs (FF→DSP→FF path only).
                    mm_mul0_raw <= mm_lfo0_out  * s_lfo0_pitch;
                    mm_mul1_raw <= mm_lfo1_out  * s_lfo1_pitch;
                    mm_mul2_raw <= mm_ramp1_out * s_ramp1_pitch;
                    mm_p_sub    <= 5'd6;
                end
                5'd6: begin
                    /* Sign-extend 36-bit signed product slices [35:16].
                     * Verilog default treats `[35:16]` as UNSIGNED and
                     * zero-extends, flipping every negative LFO/RAMP1
                     * cents contribution to a large positive value.
                     * On instruments with mod-LFO vibrato (electric
                     * guitar, brass, strings) this turns the smooth
                     * ±cents oscillation into one-sided spikes that
                     * sound clipped/wrong.  Manual sign-ext restores
                     * the negative half cycles. */
                    mm_pitch_acc <= {{16{mm_mul0_raw[35]}}, mm_mul0_raw[35:16]}
                                  + {{16{mm_mul2_raw[35]}}, mm_mul2_raw[35:16]};
                    mm_p_sub <= 5'd7;
                end
                5'd7: begin
                    mm_pitch_acc <= mm_pitch_acc +
                                    {{16{mm_mul1_raw[35]}}, mm_mul1_raw[35:16]};
                    mm_p_sub <= 5'd8;
                end
                5'd8: begin
                    seq_vstate_wr   <= 1'b1;
                    seq_vstate_addr <= {seq_voice, VW_PITCH_OFS};
                    seq_vstate_data <= mm_pitch_acc;
                    mm_p_sub <= 5'd9;
                end
                5'd9: begin
                    mm_p_sub   <= SUB_IDLE;
                    stage_done <= 1'b1;
                end
                default: ;
            endcase
        end

        // -------- MM_FILTER_ACCUM (stage 8) sub-FSM --------
        //
        //   FILTER_OFS = LFO0_OUT × lfo0_filter + RAMP1_OUT × ramp1_filt
        // (CC74 brightness fold-in is Phase 5's job — it reads the
        // channel-bank brightness field directly during FILTER_COMPOSE.)
        // mm_lfo0_out and mm_ramp1_out are shared with the pitch FSM;
        // stage 7 runs strictly before stage 8 so there's no aliasing.
        if (mm_f_sub == SUB_IDLE &&
            seq_running && seq_stage == 5'd8 && hw_envelope_enable) begin
            mm_f_sub <= 5'd1;
        end else begin
            case (mm_f_sub)
                5'd1: begin
                    // Phase 5c: parallel multiplies for LFO0×lfo0_filter
                    // and RAMP1×ramp1_filter.  s_* are pre-registered
                    // (stage 7) and mm_ramp1_out is set by RAMP1_STEP
                    // at stage 4, so this cycle is clean FF→DSP→FF.
                    mm_mul1_raw <= mm_lfo0_out  * s_lfo0_filter;
                    mm_mul2_raw <= mm_ramp1_out * s_ramp1_filter;
                    mm_f_sub <= 5'd2;
                end
                5'd2: begin
                    /* Same sign-ext as MM_PITCH_ACCUM — slice [35:16]
                     * of a 36-bit signed product is UNSIGNED in
                     * Verilog without the manual replication. */
                    mm_filter_acc <= {{16{mm_mul1_raw[35]}}, mm_mul1_raw[35:16]}
                                   + {{16{mm_mul2_raw[35]}}, mm_mul2_raw[35:16]};
                    mm_f_sub <= 5'd3;
                end
                5'd3: begin
                    seq_vstate_wr   <= 1'b1;
                    seq_vstate_addr <= {seq_voice, VW_FILTER_OFS};
                    seq_vstate_data <= mm_filter_acc;
                    mm_f_sub <= 5'd4;
                end
                5'd4: begin
                    mm_f_sub   <= SUB_IDLE;
                    stage_done <= 1'b1;
                end
                default: ;
            endcase
        end

        // -------- PITCH_COMPOSE (stage 9) sub-FSM --------
        //
        //   final_rate = BASE_RATE × 2^(((PITCH_OFS + CH_BEND_CENTS)) / 1200)
        //   CH_BEND_CENTS = (bend_s14 × bend_range_cents) >> 13
        //
        // Pipeline: pull midi_ch first (vstate W5), use it to address
        // chan_bank W1 for the signed 14-bit bend, then fold into the
        // mod-matrix PITCH_OFS from W28.  Octave-reduce the sum into
        // [0, 1199], look up 2^(c/1200)-1 in the cents_to_mult ROM,
        // rebuild the Q16 multiplier, barrel-shift for whole octaves,
        // multiply BASE_RATE by the result and emit to mixer field 2.
        if (pitch_sub == SUB_IDLE &&
            seq_running && seq_stage == 5'd9 && hw_envelope_enable) begin
            pitch_sub       <= 5'd16;  // Phase 5d entry — read midi_ch
            seq_vstate_addr <= {seq_voice, VW_MIDI_CH_BV_PAN};
        end else begin
            case (pitch_sub)
                // --- CH_BEND read path ---
                5'd16: pitch_sub <= 5'd17;        // vstate W5 read latency
                5'd17: begin
                    // vstate_b_q = {pan_base[15:0], voice_base_vol[7:0], midi_ch[7:0]}
                    chan_b_addr <= {vstate_b_q[3:0], 2'd1};  // chan_bank W1 (bend)
                    seq_vstate_addr <= {seq_voice, VW_RATE};
                    pitch_sub <= 5'd18;
                end
                5'd18: pitch_sub <= 5'd1;         // W2 + chan W1 read latency
                5'd1: begin
                    pitch_base_rate <= vstate_b_q;
                    // chan_b_q[31:16] = bend (14-bit signed in low bits of 16)
                    pitch_bend_s16  <= $signed(chan_b_q[31:16]);
                    seq_vstate_addr <= {seq_voice, VW_PITCH_OFS};
                    pitch_sub <= 5'd2;
                end
                5'd2: begin
                    // Start the bend × bend_range_cents multiply while
                    // the PITCH_OFS read is in flight.  One cycle, one
                    // DSP (s16 × s12 → s28).
                    pitch_bend_prod <= pitch_bend_s16 *
                                       $signed({1'b0, bend_range_cents});
                    pitch_sub <= 5'd3;
                end
                5'd3: begin
                    pitch_ofs_s32 <= $signed(vstate_b_q);
                    pitch_sub     <= 5'd4;
                end
                5'd4: begin
                    // CH_BEND contributes (bend × range) >> 13 cents
                    // (bend_s14 scaled so 8192 = full-range, i.e. 1
                    // bend_range_cents).  Summed with mod-matrix
                    // PITCH_OFS to form the total cents offset.
                    pitch_cents_sum <= pitch_ofs_s32 + (pitch_bend_prod >>> 13);
                    pitch_octaves   <= 4'sd0;
                    pitch_sub       <= 5'd5;
                end
                // Octave reduce: up to 4 iterations (covers ±4800
                // cents = ±4 octaves — plenty for SF2 + typical bends).
                // Each iteration is FF→compare→add→FF.
                5'd5, 5'd6, 5'd7, 5'd8: begin
                    if (pitch_cents_sum >= 32'sd1200) begin
                        pitch_cents_sum <= pitch_cents_sum - 32'sd1200;
                        pitch_octaves   <= pitch_octaves + 4'sd1;
                    end else if (pitch_cents_sum < 32'sd0) begin
                        pitch_cents_sum <= pitch_cents_sum + 32'sd1200;
                        pitch_octaves   <= pitch_octaves - 4'sd1;
                    end
                    pitch_sub <= pitch_sub + 5'd1;
                end
                // After 5'd8 we know cents ∈ [0, 1199].  Latch LUT addr.
                5'd9: begin
                    c2m_addr  <= pitch_cents_sum[10:0];
                    pitch_sub <= 5'd10;
                end
                5'd10: pitch_sub <= 5'd11;       // ROM read latency
                5'd11: begin
                    // Drive the shared octave-shift mux now; result lands
                    // in shift_out_q16 combinationally next cycle.
                    shift_in_frac    <= c2m_q;
                    shift_in_octaves <= pitch_octaves;
                    pitch_sub <= 5'd12;
                end
                5'd12: begin
                    // Apply octave shift via shared mux (see shift_out_q16
                    // in the cents-to-mult section).
                    pitch_mult_q16 <= shift_out_q16;
                    pitch_sub <= 5'd13;
                end
                5'd13: begin
                    // final_rate = BASE_RATE × mult >> 16.  BASE_RATE is
                    // u32 Q16.16, mult_q16 is u32 Q16.16 → product u48.
                    // Result fits u32 after >>> 16 as long as the final
                    // rate stays below 2^16 samples/tick which every
                    // SF2 voice respects.  Register into pitch_final_rate
                    // to break the multiply+shift into its own cycle.
                    pitch_final_rate <= pitch_base_rate * pitch_mult_q16;
                    pitch_sub <= 5'd14;
                end
                5'd14: begin
                    pitch_mix_wr    <= 1'b1;
                    pitch_mix_sel   <= seq_voice;
                    pitch_mix_field <= 4'd2;  // mixer RATE
                    pitch_mix_wdata <= pitch_final_rate[47:16];
                    pitch_sub <= 5'd15;
                end
                5'd15: begin
                    pitch_sub  <= SUB_IDLE;
                    stage_done <= 1'b1;
                end
                default: ;
            endcase
        end

        // -------- FILTER_COMPOSE (stage 10) sub-FSM --------
        //
        //   total_cents = INITIAL_FC + FILTER_OFS + (CC74−64) × 15
        //   mixer_cutoff_Q0_16 = (2^(total_cents/1200) × 65536 × 22) >> 16
        // CC74 brightness maps 0..127 → ±945 cents of cutoff delta
        // (scale 15, matching `compose_filter_cutoff` in hal/awe.c well
        // enough at musical depth).  Filter bypass triggers at
        // AWE_FILTER_BYPASS (13500); we emit 65535 so the mixer SVF
        // lets signal pass wide-open.  FILTER_Q is handled by the
        // one-shot HAL write at NOTE_ON; Phase 5 does not re-emit it.
        if (filt_sub == SUB_IDLE &&
            seq_running && seq_stage == 5'd10 && hw_envelope_enable) begin
            filt_sub        <= 5'd1;
            seq_vstate_addr <= {seq_voice, VW_MIDI_CH_BV_PAN};
        end else begin
            case (filt_sub)
                5'd1: filt_sub <= 5'd2;        // W5 read latency
                5'd2: begin
                    // midi_ch in vstate_b_q[7:0] — address chan_bank W2.
                    chan_b_addr     <= {vstate_b_q[3:0], 2'd2};
                    seq_vstate_addr <= {seq_voice, VW_INITIAL_FC};
                    filt_sub <= 5'd3;
                end
                5'd3: filt_sub <= 5'd4;        // both reads in flight
                5'd4: begin
                    // chan_b_q[31:24] = brightness (0..127).  Centre on
                    // 64 and register the s16 product with 15 cents per
                    // unit — fits one DSP.
                    filt_bright_prod <= ($signed({8'h0, chan_b_q[31:24]}) - 16'sd64)
                                        * 16'sd15;
                    filt_cents_sum   <= $signed(vstate_b_q);       // INITIAL_FC
                    seq_vstate_addr  <= {seq_voice, VW_FILTER_OFS};
                    filt_sub <= 5'd5;
                end
                5'd5: filt_sub <= 5'd6;        // W29 read latency
                5'd6: begin
                    filt_cents_sum <= filt_cents_sum +
                                      $signed(vstate_b_q) +
                                      filt_bright_prod;
                    filt_octaves   <= 4'sd0;
                    filt_sub       <= 5'd7;
                end
                5'd7: begin
                    // Bypass: SF2 zones with initial_fc >= 13500 mean
                    // "no filter" — emit 0xFFFF so the SVF passes the
                    // signal unchanged.  Clamp minimum too so the LUT
                    // address stays positive after octave-reduce.
                    if (filt_cents_sum >= 32'sd13500) begin
                        filt_bypass <= 1'b1;
                        filt_sub    <= 5'd14;         // jump to emit
                    end else begin
                        filt_bypass <= 1'b0;
                        if (filt_cents_sum < 32'sd1500)
                            filt_cents_sum <= 32'sd1500;
                        filt_sub <= 5'd8;
                    end
                end
                // Octave reduce — up to 10 octaves (fc range 1500..13500
                // cents = ~10 octaves).  4 iterations cover the typical
                // musical cutoff range; extreme outliers clip.
                5'd8, 5'd9, 5'd10, 5'd11: begin
                    if (filt_cents_sum >= 32'sd1200) begin
                        filt_cents_sum <= filt_cents_sum - 32'sd1200;
                        filt_octaves   <= filt_octaves + 4'sd1;
                    end else if (filt_cents_sum < 32'sd0) begin
                        filt_cents_sum <= filt_cents_sum + 32'sd1200;
                        filt_octaves   <= filt_octaves - 4'sd1;
                    end
                    filt_sub <= filt_sub + 5'd1;
                end
                5'd12: begin
                    c2m_addr <= filt_cents_sum[10:0];
                    filt_sub <= 5'd13;
                end
                5'd13: begin
                    // Drive the shared octave-shift mux now; result is
                    // sampled in 5'd15 below.
                    shift_in_frac    <= c2m_q;
                    shift_in_octaves <= filt_octaves;
                    filt_sub         <= 5'd15;
                end
                5'd15: begin
                    // Shared octave-shift result, then cutoff = (mult × 22) >> 16.
                    filt_mult_q16 <= shift_out_q16;
                    filt_sub <= 5'd16;
                end
                5'd16: begin
                    // cutoff = mult × 22 (shift not yet; register product).
                    filt_cutoff_prod <= filt_mult_q16 * 32'd22;
                    filt_sub <= 5'd14;
                end
                5'd14: begin
                    // Filter writes go out every tick — they're sparse
                    // enough (filter usually static unless LFO/env
                    // active) that the mixer doesn't notice the
                    // redundant traffic.  Saved ~150 ALMs by not
                    // adding a prev_cutoff array here.
                    filt_mix_wr    <= 1'b1;
                    filt_mix_sel   <= seq_voice;
                    filt_mix_field <= 4'd11;  // mixer FILTER_FC
                    filt_mix_wdata <= filt_bypass ? 32'hFFFF :
                        (filt_cutoff_prod[47:16] > 32'd65535 ? 32'hFFFF
                                                             : filt_cutoff_prod[47:16]);
                    filt_sub <= 5'd17;
                end
                5'd17: begin
                    filt_sub   <= SUB_IDLE;
                    stage_done <= 1'b1;
                end
                default: ;
            endcase
        end

        // -------- SEND_COMPOSE (stage 16) sub-FSM --------
        //
        // Read voice's midi_ch (W5), look up chan_bank W3 which packs
        // {chorus_send[31:24], reverb_send[23:16], _[15:0]}, then
        // emit two mixer writes (field 13 REVERB_SEND, field 14
        // CHORUS_SEND) at the per-voice send level.  No change-detect
        // here either — sends rarely change at tick rate, so always
        // emit costs little.
        if (send_sub == SUB_IDLE &&
            seq_running && seq_stage == 5'd16 && hw_envelope_enable) begin
            send_sub        <= 5'd1;
            seq_vstate_addr <= {seq_voice, VW_MIDI_CH_BV_PAN};
        end else begin
            case (send_sub)
                5'd1: send_sub <= 5'd2;        // W5 read latency
                5'd2: begin
                    chan_b_addr <= {vstate_b_q[3:0], 2'd3};   // chan_bank W3
                    send_sub    <= 5'd3;
                end
                5'd3: send_sub <= 5'd4;        // chan_bank read latency
                5'd4: begin
                    send_mix_wr    <= 1'b1;
                    send_mix_sel   <= seq_voice;
                    send_mix_field <= 4'd13;   // mixer REVERB_SEND
                    send_mix_wdata <= {24'd0, chan_b_q[23:16]};  // reverb_send byte
                    send_sub <= 5'd5;
                end
                5'd5: begin
                    send_mix_wr    <= 1'b1;
                    send_mix_sel   <= seq_voice;
                    send_mix_field <= 4'd14;   // mixer CHORUS_SEND
                    send_mix_wdata <= {24'd0, chan_b_q[31:24]};  // chorus_send byte
                    send_sub <= 5'd6;
                end
                5'd6: begin
                    send_sub   <= SUB_IDLE;
                    stage_done <= 1'b1;
                end
                default: ;
            endcase
        end

        // -------- HW_ENVELOPE-off fast-paths --------
        // Stages that gate on HW envelope advance still need to
        // retire every tick even when HW is off so the sequencer can
        // walk through them.  Same pattern as stage 3 / stage 15.
        if (seq_running && seq_stage == 5'd15 && vol_sub == SUB_IDLE &&
            !hw_envelope_enable)
            stage_done <= 1'b1;
        if (seq_running && seq_stage == 5'd3 && ramp0_sub == SUB_IDLE &&
            !hw_envelope_enable)
            stage_done <= 1'b1;
        if (seq_running && (seq_stage == 5'd5 || seq_stage == 5'd6) &&
            lfo_sub == SUB_IDLE && !hw_envelope_enable)
            stage_done <= 1'b1;
        if (seq_running && seq_stage == 5'd7 && mm_p_sub == SUB_IDLE &&
            !hw_envelope_enable)
            stage_done <= 1'b1;
        if (seq_running && seq_stage == 5'd8 && mm_f_sub == SUB_IDLE &&
            !hw_envelope_enable)
            stage_done <= 1'b1;
        if (seq_running && seq_stage == 5'd9 && pitch_sub == SUB_IDLE &&
            !hw_envelope_enable)
            stage_done <= 1'b1;
        if (seq_running && seq_stage == 5'd10 && filt_sub == SUB_IDLE &&
            !hw_envelope_enable)
            stage_done <= 1'b1;
        if (seq_running && seq_stage == 5'd16 && send_sub == SUB_IDLE &&
            !hw_envelope_enable)
            stage_done <= 1'b1;
        if (seq_running && seq_stage == 5'd4 && ramp1_sub == SUB_IDLE &&
            !hw_envelope_enable)
            stage_done <= 1'b1;

        // -------- NOP fast-path for remaining stages --------
        if (seq_running &&
            seq_stage != 5'd1  && seq_stage != 5'd3 &&
            seq_stage != 5'd4  &&
            seq_stage != 5'd5  && seq_stage != 5'd6 &&
            seq_stage != 5'd7  && seq_stage != 5'd8 &&
            seq_stage != 5'd9  && seq_stage != 5'd10 &&
            seq_stage != 5'd15 && seq_stage != 5'd16)
            stage_done <= 1'b1;

        // -------- Outer sequencer advance --------
        if (seq_running && stage_done) begin
            if (seq_stage == SEQ_STAGE_LAST) begin
                seq_stage <= 5'd0;
                if (seq_voice == SEQ_VOICE_LAST) begin
                    seq_voice   <= 6'd0;
                    seq_running <= 1'b0;
                end else begin
                    seq_voice <= seq_voice + 6'd1;
                end
            end else begin
                seq_stage <= seq_stage + 5'd1;
            end
        end
    end
end

// =========================================================
// NOTE_ON FSM
// =========================================================
// Reads voice state words W0..W8 sequentially and emits them as
// mixer voice writes via mix_voice_wr/sel/field/wdata.  Field IDs
// follow the mapping in the header comment.
//
// Pipelined: address asserted in S_READ, q_b valid one cycle later
// in S_EMIT.  Each field takes 2 cycles, total 18 cycles for 9
// fields.  No bus arbitration required — the priority mux in core_top
// holds CPU-direct writes off while awe_mix_voice_wr is high.

localparam S_IDLE     = 3'd0;
localparam S_READ     = 3'd1;
localparam S_EMIT     = 3'd2;
localparam S_DONE     = 3'd3;

reg  [2:0] fsm_state;
reg  [4:0] fsm_word_idx;        // 0..8 stepping through W0..W8 (5 bits to match RAM word index width)
reg  [5:0] fsm_voice;
reg  [3:0] fsm_field;           // mixer field id derived from word idx

// Word-idx → mixer field id (matches header comment).
function [3:0] word_to_field(input [4:0] widx);
    case (widx)
        5'd0:  word_to_field = 4'd0;   // BASE
        5'd1:  word_to_field = 4'd1;   // LEN
        5'd2:  word_to_field = 4'd2;   // RATE
        5'd3:  word_to_field = 4'd4;   // POS_INT
        5'd4:  word_to_field = 4'd6;   // VOL_LR
        5'd5:  word_to_field = 4'd9;   // VOL_TARGET (must match VOL_LR)
        5'd6:  word_to_field = 4'd10;  // VOL_RATE   (0 = instant snap)
        5'd7:  word_to_field = 4'd7;   // LOOP_END
        5'd8:  word_to_field = 4'd8;   // LOOP_START
        5'd9:  word_to_field = 4'd11;  // FILTER_FC
        5'd10: word_to_field = 4'd12;  // FILTER_Q
        5'd11: word_to_field = 4'd3;   // CTRL — written LAST to atomically
                                       // activate the voice once everything
                                       // else is programmed.
        default: word_to_field = 4'd15;
    endcase
endfunction

localparam [4:0] FSM_LAST_WORD = 5'd11;

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        fsm_state          <= S_IDLE;
        fsm_word_idx       <= 0;
        fsm_voice          <= 0;
        fsm_field          <= 0;
        noteon_vstate_addr <= 11'd0;
        noteon_port_busy   <= 1'b0;
        noteon_mix_wr      <= 1'b0;
        noteon_mix_field   <= 4'd0;
        noteon_mix_sel     <= 6'd0;
        noteon_mix_wdata   <= 32'd0;
        active_mask        <= 32'd0;
    end else begin
        // Default: deassert mixer write strobe each cycle.
        noteon_mix_wr <= 1'b0;

        // NOTE_OFF / VOICE_STOP: clear the voice's bit in active_mask.
        //
        //   hw_envelope OFF: cpu_note_off is treated as a hard stop
        //     (no fabric envelope), so clear the bit immediately.
        //   hw_envelope ON:  cpu_note_off only triggers RAMP0 RELEASE
        //     (via noff_pending below); the voice keeps playing while
        //     the release ramps down.  Don't clear active_mask here —
        //     wait for ramp0_done_pulse from RAMP0_STEP when ENV_DONE
        //     is reached.
        if (cpu_note_off && !hw_envelope_enable)
            active_mask[cpu_note_off_voice] <= 1'b0;
        if (cpu_voice_stop)
            active_mask[cpu_voice_stop_voice] <= 1'b0;
        if (ramp0_done_pulse)
            active_mask[ramp0_done_voice] <= 1'b0;

        case (fsm_state)
            S_IDLE: begin
                if (cpu_note_on) begin
                    fsm_voice          <= cpu_note_on_voice;
                    fsm_word_idx       <= 0;
                    noteon_vstate_addr <= {cpu_note_on_voice, 5'd0};
                    noteon_port_busy   <= 1'b1;
                    fsm_state          <= S_READ;
                end
            end

            // Read latency is 1 cycle (q_b is registered inside the
            // M10K when outdata_reg_b="UNREGISTERED" the address-to-q
            // delay is still 1 cycle of clk-to-out for sync RAM).
            S_READ: begin
                fsm_state <= S_EMIT;
                fsm_field <= word_to_field(fsm_word_idx);
            end

            S_EMIT: begin
                noteon_mix_wr    <= 1'b1;
                noteon_mix_field <= fsm_field;
                noteon_mix_sel   <= fsm_voice;
                noteon_mix_wdata <= vstate_b_q;

                if (fsm_word_idx == FSM_LAST_WORD) begin
                    fsm_state <= S_DONE;
                end else begin
                    fsm_word_idx       <= fsm_word_idx + 5'd1;
                    noteon_vstate_addr <= {fsm_voice, fsm_word_idx + 5'd1};
                    fsm_state          <= S_READ;
                end
            end

            S_DONE: begin
                // Mark voice active.  Phase 2+ will fold this into the
                // CTRL word's active bit emitted to the mixer; for
                // now active_mask is a SW-visible side channel.
                active_mask[fsm_voice] <= 1'b1;
                noteon_port_busy       <= 1'b0;  // release port B for sequencer
                fsm_state              <= S_IDLE;
            end

            default: fsm_state <= S_IDLE;
        endcase
    end
end

endmodule

`default_nettype wire
