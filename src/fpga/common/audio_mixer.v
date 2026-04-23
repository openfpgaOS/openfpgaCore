//
// Hardware PCM Mixer (v2) — 32 voices from SDRAM sample pool
//
// Derived from the original CRAM1-era audio_mixer.v (retired in
// f11811c).  This is a simplified v2 port that fetches from SDRAM
// via a dedicated AXI4 read master instead of CRAM1 burst IO, and
// writes the final stereo pair directly into audio_output's dcfifo
// (audio_dma is retired — removed from core_top and the QSF in v2).
//
// Runs on clk_cpu (100 MHz).  Produces a stereo int16 pair at 48 kHz
// (sample period = 2083 cycles).  With 32 voices × ~30 cycles/voice
// worst-case per SDRAM fetch, steady-state budget ~960 cycles per
// sample — well under the 2083-cycle slot — with plenty of slack
// for worst-case SDRAM arbitration.
//
// What's in this version (phase 1):
//   - 32 voices, mono/stereo 16-bit PCM in the SDRAM sample pool
//   - Forward loop / one-shot (no bidi — deferred)
//   - 2-tap linear interpolation (Q16.16 position)
//   - Per-channel HW volume ramp (target/rate pair)
//   - Voice-end IRQ bitmap (W1C from CPU)
//
// What's missing vs the old mixer (deliberately deferred):
//   - Reverb, chorus, per-voice send levels
//   - Bidi loop (reverse direction)
//   - 8-bit PCM samples
//   - Per-voice sample burst cache
//
// Voice table layout (16-word stride × 32 voices = 512 words = 1 M10K):
//   Word 0:  base_byte[31:0]   — byte offset of sample 0 into SDRAM pool
//   Word 1:  length[21:0]      — total sample count
//   Word 2:  rate_fp16[31:0]   — Q16.16 playback rate (0x10000 = 1.0)
//   Word 3:  ctrl              — {loop[2], stereo[1], active[0]} (fmt=16 always)
//   Word 4:  pos_int[21:0]
//   Word 5:  pos_frac[15:0]
//   Word 6:  vol_lr            — {vol_r[7:0], vol_l[7:0]}  (current, ramped)
//   Word 7:  loop_end[21:0]
//   Word 8:  loop_start[21:0]
//   Word 9:  vol_target        — {tgt_r[7:0], tgt_l[7:0]}
//   Word 10: vol_rate[7:0]     — ramp step size (0 = snap)
//   Word 11-15: reserved
//

`default_nettype none

module audio_mixer (
    input wire clk,
    input wire reset_n,

    // Global enable (from MIXER_CTRL MMIO).
    input wire mixer_enable,

    // Sample pool base address in SDRAM (byte address).  Each voice's
    // base_byte is added to this.  Programmed once at reset to
    // OF_TARGET_SAMPLE_BASE (0x13700000).
    input wire [31:0] sample_pool_base,

    // ------- Voice-table write port (from axi_periph_slave) -------
    // CPU writes voice state one field at a time: set voice_sel + field,
    // drive voice_wdata, pulse voice_wr for one cycle.  Lands directly
    // on vtbl port B; no stall.  voice_sel_rd is the read-side selector
    // for position readback (independent of voice_wr).
    input wire        voice_wr,
    input wire [3:0]  voice_field,
    input wire [4:0]  voice_sel,
    input wire [4:0]  voice_sel_rd,
    input wire [31:0] voice_wdata,

    // ------- AXI4 read master → SDRAM arbiter (M3) --------
    // Read-only, 32-bit data, single-beat bursts (ARLEN=0).
    output reg         m_arvalid,
    input  wire        m_arready,
    output reg  [31:0] m_araddr,
    output reg  [7:0]  m_arlen,
    input  wire        m_rvalid,
    input  wire [31:0] m_rdata,
    input  wire [1:0]  m_rresp,
    input  wire        m_rlast,
    output reg         m_rready,

    // ------- Audio FIFO write (into audio_output's dcfifo) --------
    output reg         sample_wr,
    output reg  [31:0] sample_data,  // {left[15:0], right[15:0]}
    input  wire [9:0]  fifo_level,

    // ------- Status + IRQ ----------------------------------------
    output wire [5:0]  active_count,
    output wire [21:0] pos_readback,
    input  wire        irq_clear_wr,
    input  wire [31:0] irq_clear,
    output reg  [31:0] voice_end_pending,
    output wire        voice_end_irq,

    // ------- Debug / diagnostic outputs ---------------------------
    // last_sample_data  : 32-bit value last pushed into the audio_out
    //                     dcfifo ({left[15:0], right[15:0]}).  Firmware
    //                     reads this to verify non-zero samples are
    //                     actually being produced by the mixer.
    // sample_count      : monotonic counter, increments on every
    //                     sample_wr pulse.  Firmware diffs across a
    //                     window to confirm the mixer is producing at
    //                     ~48 kHz and not stalled.
    output reg  [31:0] last_sample_data,
    output reg  [31:0] sample_count,
    // voice_active_mask : 32-bit bitmap of the shadow `voice_active`
    // register — firmware diagnostic to see WHICH voices the HW thinks
    // are playing (active_count only gives the popcount).
    output wire [31:0] voice_active_mask
);

// ============================================================
// Voice table (BIDIR_DUAL_PORT altsyncram, 1 M10K)
// ============================================================
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

/* =================================================================
 * Voice table — SPLIT INTO TWO MEMORIES (Option A).
 *
 * Attempted BIDIR_DUAL_PORT with one altsyncram repeatedly produced
 * silent port-B write failures on Cyclone V silicon despite multiple
 * parameter tweaks (width_byteena, byte_size, rden_b, q_b handling).
 * The shadow `voice_active` (plain always block) would update but
 * the corresponding CTRL write into the RAM would not — voice stayed
 * permanently skipped at S_CHECK_ACTIVE.  Split by ownership:
 *
 *   vtbl_cpu (DUAL_PORT):  port B = CPU write, port A = FSM read.
 *       Holds ADDR, LEN, RATE, CTRL, LOOP_END, LOOP_START,
 *       VOL_TARGET, VOL_RATE — everything CPU configures at note-on.
 *   vtbl_fsm (SINGLE_PORT): port A = FSM R/W.
 *       Holds POS_INT, POS_FRAC, VOL_LR — the per-sample mutable
 *       state only the FSM touches.
 *
 * Simple-dual-port and single-port are the well-trodden altsyncram
 * configurations on Altera — no inference edge cases.  Both memories
 * are 512 words × 32 bits (addresses = {voice[4:0], field[3:0]} so the
 * existing field indices stay valid across either memory), total cost
 * 2 M10Ks (was 1 previously — acceptable overhead).
 * ================================================================= */
reg  [8:0]  vtbl_a_addr;
reg  [31:0] vtbl_a_data;
reg         vtbl_a_wr;
wire [31:0] vtbl_a_q;     // muxed FSM read output (cpu or fsm memory)

wire [31:0] vtbl_cpu_q;
wire [31:0] vtbl_fsm_q;

/* is_fsm_field = the currently-addressed field belongs to vtbl_fsm.
 * Computed combinationally from the read address; matches the 1-cycle
 * altsyncram latency because the address is stable for that cycle. */
wire is_fsm_field =
    (vtbl_a_addr[3:0] == VTBL_POS_INT)  ||
    (vtbl_a_addr[3:0] == VTBL_POS_FRAC) ||
    (vtbl_a_addr[3:0] == VTBL_VOL_LR);

assign vtbl_a_q = is_fsm_field ? vtbl_fsm_q : vtbl_cpu_q;

/* CPU → vtbl_cpu port B write.  Gate out FSM-owned fields so a stray
 * CPU write to POS_INT/POS_FRAC/VOL_LR doesn't corrupt the FSM memory
 * (firmware doesn't write those today, but cheap insurance). */
wire [8:0]  vtbl_b_addr = {voice_sel, voice_field};
wire [31:0] vtbl_b_data = voice_wdata;
wire        is_cpu_field =
    (voice_field == VTBL_ADDR)       ||
    (voice_field == VTBL_LEN)        ||
    (voice_field == VTBL_RATE)       ||
    (voice_field == VTBL_CTRL)       ||
    (voice_field == VTBL_POS_INT)    ||  /* POS_WR writes VTBL_POS_INT directly */
    (voice_field == VTBL_LOOP_END)   ||
    (voice_field == VTBL_LOOP_START) ||
    (voice_field == VTBL_VOL_TARGET) ||
    (voice_field == VTBL_VOL_RATE);
wire        vtbl_cpu_b_wr = voice_wr && is_cpu_field;

/* Wait — MIX_VOICE_POS_WR writes VTBL_POS_INT (field 4) via the MMIO
 * path, but POS_INT lives in vtbl_fsm.  Route that one specific CPU
 * write to vtbl_fsm's port A, time-multiplexed with the FSM's own
 * writes.  The CPU's POS_WR happens at most once per note-on (rare
 * vs. the FSM's per-sample S_ADVANCE writes), so the race window is
 * tiny.  CPU write wins when both fire same cycle. */
wire        cpu_pos_wr = voice_wr && (voice_field == VTBL_POS_INT);
wire        vtbl_fsm_wren_a;
wire [8:0]  vtbl_fsm_addr_a;
wire [31:0] vtbl_fsm_data_a;
assign vtbl_fsm_wren_a = cpu_pos_wr ? 1'b1 : vtbl_a_wr;
assign vtbl_fsm_addr_a = cpu_pos_wr ? {voice_sel, voice_field} : vtbl_a_addr;
assign vtbl_fsm_data_a = cpu_pos_wr ? voice_wdata : vtbl_a_data;

/* vtbl_cpu — plain reg array with async read + sync write.  Matches
 * the 0-cycle combinational read semantics of altsyncram UNREGISTERED
 * that the FSM's read pipeline was built around (set addr cycle N,
 * capture q in cycle N+1 via NBA).  On Cyclone V, Quartus infers this
 * into MLABs (~26 blocks for 16 Kbits).  Bypasses the altsyncram
 * DUAL_PORT inference that silently dropped port-B writes. */
reg [31:0] vtbl_cpu_mem [0:511];

always @(posedge clk) begin
    if (vtbl_cpu_b_wr)
        vtbl_cpu_mem[vtbl_b_addr] <= vtbl_b_data;
end

assign vtbl_cpu_q = vtbl_cpu_mem[vtbl_a_addr];

/* vtbl_fsm — same plain reg-array pattern as vtbl_cpu so both memories
 * share 0-cycle async-read semantics.  The previous altsyncram SINGLE_PORT
 * UNREGISTERED variant may have added a 1-cycle input-register latency
 * (Quartus' M10K inference forces one), which would put vtbl_fsm reads
 * a cycle behind vtbl_cpu reads and break the FSM's pipeline for
 * POS_INT/POS_FRAC/VOL_LR fields.  Reg array → MLAB inference, matches
 * vtbl_cpu's behaviour exactly. */
reg [31:0] vtbl_fsm_mem [0:511];

always @(posedge clk) begin
    if (vtbl_fsm_wren_a)
        vtbl_fsm_mem[vtbl_fsm_addr_a] <= vtbl_fsm_data_a;
end

assign vtbl_fsm_q = vtbl_fsm_mem[vtbl_a_addr];

// Shadow active bit — updated when CPU writes CTRL (bit 0), or when
// the FSM retires a one-shot voice (voice_end_clear_mask pulses high
// for one cycle with bit `cur_voice` set).  CPU write wins if both
// happen the same cycle.
reg [31:0] voice_active;
reg [31:0] voice_end_clear_mask;
always @(posedge clk) begin
    if (!reset_n)
        voice_active <= 32'd0;
    else begin
        // Start from "clear any bits the FSM flagged this cycle".
        reg [31:0] next_active;
        next_active = voice_active & ~voice_end_clear_mask;
        if (voice_wr && voice_field == VTBL_CTRL)
            next_active[voice_sel] = voice_wdata[0];
        voice_active <= next_active;
    end
end

assign voice_end_irq = |voice_end_pending;

// Popcount for active_count MMIO readback.  Blocking accumulator inside
// an always_comb-style combinational always block, then registered so
// the MMIO read sees a stable value.  (The previous NBA-in-for-loop form
// only retained the LAST set bit's +1, not a real popcount.)
reg [5:0] active_cnt;
integer i;
reg [5:0] active_cnt_comb;
always @* begin
    active_cnt_comb = 6'd0;
    for (i = 0; i < 32; i = i + 1)
        if (voice_active[i]) active_cnt_comb = active_cnt_comb + 6'd1;
end
always @(posedge clk) active_cnt <= active_cnt_comb;
assign active_count = active_cnt;
assign voice_active_mask = voice_active;

// Position readback — latched per-voice for instantaneous CPU reads.
reg [21:0] pos_latch [0:31];
assign pos_readback = pos_latch[voice_sel_rd];

// Voice-end IRQ pending with W1C (write-one-to-clear) support.
reg [31:0] voice_end_set_mask;
always @(posedge clk) begin
    if (!reset_n)
        voice_end_pending <= 32'd0;
    else begin
        voice_end_pending <= (voice_end_pending | voice_end_set_mask)
                           & (irq_clear_wr ? ~irq_clear : 32'hFFFFFFFF);
    end
end

// ============================================================
// Sample extraction / address helpers
// ============================================================
function signed [15:0] extract_sample16;
    input [31:0] word;
    input        low_half;   // 0 = lo word, 1 = hi word
    begin
        extract_sample16 = low_half ? $signed(word[31:16])
                                    : $signed(word[15:0]);
    end
endfunction

// Compute the SDRAM byte address for mono sample index `idx` given the
// voice's precomputed base (sample_pool_base + cur_base_byte) and its
// stereo flag.  Stride: 2 bytes mono, 4 bytes stereo (L+R 16-bit
// interleaved).  The pool-base + voice-base add is hoisted to
// S_RD_BASE_W (once per voice) so this function is a single 32-bit
// adder, keeping cur_pos_int → m_araddr inside the 10 ns budget.
function [31:0] sample_byte_addr;
    input [31:0] vbase;
    input [21:0] idx;
    input        stereo;
    begin
        sample_byte_addr = vbase + (stereo ? {8'd0, idx, 2'b00}    // idx * 4
                                           : {9'd0, idx, 1'b0});   // idx * 2
    end
endfunction

// Word-align an SDRAM byte address to a 4-byte fetch.  The returned
// low bit tells us which 16-bit half inside the fetched word holds
// the sample.  For stereo mono-interleaved, bit 1 of the byte
// address picks L vs R within the 32-bit word.
function [31:0] word_aligned;
    input [31:0] byte_addr;
    begin
        word_aligned = byte_addr & 32'hFFFFFFFC;
    end
endfunction

// ============================================================
// FSM
// ============================================================
localparam S_IDLE           = 5'd0;
localparam S_START_SAMPLE   = 5'd1;   // begin a new stereo-out sample period
localparam S_ADV_COMPUTE    = 5'd2;   // repurposed from unused S_RD_CTRL
localparam S_RD_CTRL_W      = 5'd3;
localparam S_CHECK_ACTIVE   = 5'd4;
localparam S_FETCH_TAP1_NXT_CALC = 5'd5;  // repurposed from unused S_RD_POS
localparam S_RD_POS_W       = 5'd6;
localparam S_RD_BASE        = 5'd7;
localparam S_RD_BASE_W      = 5'd8;
localparam S_RD_RATE        = 5'd9;
localparam S_RD_RATE_W      = 5'd10;
localparam S_RD_LEN         = 5'd11;
localparam S_RD_LEN_W       = 5'd12;
localparam S_RD_VOL         = 5'd13;
localparam S_RD_VOL_W       = 5'd14;
localparam S_FETCH_TAP0_CALC = 5'd29;  // register byte addr (adder cycle)
localparam S_FETCH_TAP0_AR  = 5'd15;    // drive m_araddr from registered addr
localparam S_FETCH_TAP0_R   = 5'd16;
localparam S_FETCH_TAP1_NXT  = 5'd31;   // register nxt (pos+1, wrap, clamp)
localparam S_FETCH_TAP1_CALC = 5'd30;   // register byte addr from nxt
localparam S_FETCH_TAP1_AR  = 5'd17;
localparam S_FETCH_TAP1_R   = 5'd18;
localparam S_LERP_INTERP    = 5'd19;   // stage 1: diff, mult by pos_frac, + tap0
localparam S_LERP_MIX       = 5'd20;   // stage 2: * vol, accumulate
localparam S_ADVANCE        = 5'd21;
localparam S_WR_POS_INT     = 5'd22;
localparam S_WR_POS_FRAC    = 5'd23;
localparam S_NEXT_VOICE     = 5'd24;
localparam S_OUTPUT         = 5'd25;
localparam S_RAMP_STEP      = 5'd26;
localparam S_WR_VOL         = 5'd27;
localparam S_VOICE_END      = 5'd28;

reg [4:0] state;
reg [4:0] cur_voice;
reg [4:0] next_voice;

// Per-voice cached fields for the current pass.
reg [31:0] cur_base_byte;
reg [31:0] voice_base;          // sample_pool_base + cur_base_byte, precomputed
reg [21:0] cur_length;
reg [31:0] cur_rate;
reg [2:0]  cur_ctrl;        // {loop, stereo, active}
reg [21:0] cur_pos_int;
reg [15:0] cur_pos_frac;
reg [7:0]  cur_vol_l, cur_vol_r;
reg [21:0] cur_loop_end, cur_loop_start;
reg [7:0]  cur_vol_tgt_l, cur_vol_tgt_r;
reg [7:0]  cur_vol_rate;

wire cur_active = cur_ctrl[0];
wire cur_stereo = cur_ctrl[1];
wire cur_loop   = cur_ctrl[2];

// Tap fetch state — linear interp needs 2 consecutive samples.
reg signed [15:0] tap0_l, tap0_r;
reg signed [15:0] tap1_l, tap1_r;
reg [31:0] tap_byte_addr;
reg [31:0] tap_araddr;     // registered m_araddr; pipelined from TAP*_CALC
reg [21:0] tap_pos;
reg [21:0] tap_nxt_pos;    // S_FETCH_TAP1_CALC hands this to TAP1_AR
reg [21:0] tap_nxt_raw;    // pos+1 pre-wrap-clamp; pipeline register between
                           //   TAP1_NXT_CALC and TAP1_NXT so the 22-bit
                           //   adder isn't chained with the 2× compares
                           //   and muxes in the same cycle (was -1.7 ns).

// Linear-interp result (per channel).
reg signed [15:0] samp_l, samp_r;

// Pipeline register between S_LERP_INTERP and S_LERP_MIX — holds the
// post-interp, pre-volume sample so the multiplier chain is split
// across two cycles.  Without this the tap1→accum combinational path
// is 2×mult + 3×add = ~25 ns, violating the 100 MHz budget.
reg signed [16:0] lerp_l_reg, lerp_r_reg;

// Pipeline registers between S_ADV_COMPUTE and S_ADVANCE — the raw
// pos/frac advance (two chained 22-bit adders) gets its own cycle so
// the wrap compare + subtract-add + pos_latch M10K write runs alone.
// Previously the full chain blew the 100 MHz budget by 2 ns.
reg [17:0] adv_new_frac_full;
reg [21:0] adv_new_pos_raw;

// Stereo accumulators for the current output sample (s32 to avoid
// overflow across 32 voices).
reg signed [31:0] accum_l, accum_r;

// Volume-ramped new values (written back to VTBL_VOL_LR).
reg [7:0] ramp_new_l, ramp_new_r;
reg       ramp_changed;

function [7:0] ramp_step;
    input [7:0] cur;
    input [7:0] tgt;
    input [7:0] step;
    reg   [8:0] up, dn;
    begin
        up = {1'b0, cur} + {1'b0, step};
        dn = {1'b0, cur} - {1'b0, step};
        if (step == 8'd0)
            ramp_step = tgt;
        else if (cur < tgt)
            ramp_step = (up[8] || up[7:0] >= tgt) ? tgt : up[7:0];
        else if (cur > tgt)
            ramp_step = (dn[8] || dn[7:0] <= tgt) ? tgt : dn[7:0];
        else
            ramp_step = cur;
    end
endfunction

// ============================================================
// FSM body
// ============================================================
always @(posedge clk) begin
    // Defaults each cycle.
    vtbl_a_wr            <= 1'b0;
    voice_end_set_mask   <= 32'd0;
    voice_end_clear_mask <= 32'd0;
    sample_wr            <= 1'b0;

    if (!reset_n) begin
        state            <= S_IDLE;
        cur_voice        <= 5'd0;
        next_voice       <= 5'd0;
        accum_l          <= 32'sd0;
        accum_r          <= 32'sd0;
        m_arvalid        <= 1'b0;
        m_rready         <= 1'b0;
        m_araddr         <= 32'd0;
        m_arlen          <= 8'd0;
        last_sample_data <= 32'd0;
        sample_count     <= 32'd0;
    end else case (state)

    // ---- Idle: wait for FIFO to drop below half, then start new sample ----
    S_IDLE: begin
        if (mixer_enable && fifo_level[9:8] != 2'b11) begin
            // fifo < 768 entries → room for another ~256 samples.  Begin.
            accum_l    <= 32'sd0;
            accum_r    <= 32'sd0;
            cur_voice  <= 5'd0;
            next_voice <= 5'd0;
            state      <= S_START_SAMPLE;
        end
    end

    S_START_SAMPLE: begin
        vtbl_a_addr <= {cur_voice, VTBL_CTRL};
        state       <= S_RD_CTRL_W;
    end

    // Voice-field read pipeline — each field is {S_RD_*, S_RD_*_W}.
    // _W captures vtbl_a_q with one cycle of BRAM latency.
    S_RD_CTRL_W: begin
        cur_ctrl <= vtbl_a_q[2:0];
        state    <= S_CHECK_ACTIVE;
    end

    S_CHECK_ACTIVE: begin
        /* Check the shadow ONLY — in the split-memory v2 the FSM no
         * longer writes VTBL_CTRL (the shadow `voice_active` is the
         * source of truth).  Previously we also AND'd `cur_ctrl[0]`;
         * keeping that would race the RAM read pipeline unnecessarily. */
        if (!voice_active[cur_voice]) begin
            state <= S_NEXT_VOICE;
        end else begin
            vtbl_a_addr <= {cur_voice, VTBL_POS_INT};
            state       <= S_RD_POS_W;
        end
    end

    S_RD_POS_W: begin
        cur_pos_int <= vtbl_a_q[21:0];
        vtbl_a_addr <= {cur_voice, VTBL_POS_FRAC};
        state       <= S_RD_BASE;
    end

    S_RD_BASE: begin
        cur_pos_frac <= vtbl_a_q[15:0];
        vtbl_a_addr  <= {cur_voice, VTBL_ADDR};
        state        <= S_RD_BASE_W;
    end

    S_RD_BASE_W: begin
        cur_base_byte <= vtbl_a_q;
        voice_base    <= sample_pool_base + vtbl_a_q;   // hoist pool+base add
        vtbl_a_addr   <= {cur_voice, VTBL_RATE};
        state         <= S_RD_RATE_W;
    end

    S_RD_RATE_W: begin
        cur_rate    <= vtbl_a_q;
        vtbl_a_addr <= {cur_voice, VTBL_LEN};
        state       <= S_RD_LEN_W;
    end

    S_RD_LEN_W: begin
        cur_length  <= vtbl_a_q[21:0];
        vtbl_a_addr <= {cur_voice, VTBL_VOL_LR};
        state       <= S_RD_VOL_W;
    end

    S_RD_VOL_W: begin
        cur_vol_l <= vtbl_a_q[7:0];
        cur_vol_r <= vtbl_a_q[15:8];
        // Read more control fields for loop/ramp handling.
        vtbl_a_addr <= {cur_voice, VTBL_LOOP_END};
        state       <= S_RD_VOL;
    end

    S_RD_VOL: begin
        cur_loop_end <= vtbl_a_q[21:0];
        vtbl_a_addr  <= {cur_voice, VTBL_LOOP_START};
        state        <= S_FETCH_TAP0_CALC;  // loop_start captured in CALC
    end

    // ---- Fetch tap 0 (sample at pos_int) ----
    // CALC cycle: register byte addr so the 32-bit adder sits alone in
    // its own cycle (cur_pos_int + voice_base + idx_shift).  AR cycle
    // drives m_araddr from the registered addr and asserts arvalid.
    S_FETCH_TAP0_CALC: begin
        cur_loop_start <= vtbl_a_q[21:0];
        if (cur_pos_int >= cur_length) begin
            state <= S_VOICE_END;
        end else begin
            tap_pos       <= cur_pos_int;
            tap_byte_addr <= sample_byte_addr(voice_base, cur_pos_int, cur_stereo);
            tap_araddr    <= word_aligned(sample_byte_addr(voice_base, cur_pos_int, cur_stereo));
            state         <= S_FETCH_TAP0_AR;
        end
    end

    S_FETCH_TAP0_AR: begin
        m_araddr  <= tap_araddr;
        m_arlen   <= 8'd0;
        m_arvalid <= 1'b1;
        state     <= S_FETCH_TAP0_R;
    end

    S_FETCH_TAP0_R: begin
        if (m_arvalid && m_arready) m_arvalid <= 1'b0;
        m_rready <= 1'b1;
        if (m_rvalid) begin
            // Extract taps.  For mono, the 32-bit word holds 2 samples;
            // tap_byte_addr[1] picks the high half.  For stereo, same
            // word holds both L and R — rdata[15:0] = L, rdata[31:16] = R.
            if (cur_stereo) begin
                tap0_l <= $signed(m_rdata[15:0]);
                tap0_r <= $signed(m_rdata[31:16]);
            end else begin
                tap0_l <= extract_sample16(m_rdata, tap_byte_addr[1]);
                tap0_r <= extract_sample16(m_rdata, tap_byte_addr[1]);
            end
            m_rready <= 1'b0;
            state    <= S_FETCH_TAP1_NXT_CALC;
        end
    end

    // ---- Fetch tap 1 (sample at pos_int + 1, or loop_start if wrapping) ----
    // NXT_CALC cycle: register pos+1 raw (isolates the adder).
    // NXT      cycle: wrap + clamp on registered raw, register final nxt.
    // CALC     cycle: register byte addr from nxt + voice_base.
    // AR       cycle: drive the bus from the registered addr.
    // Splitting four ways keeps every arithmetic op + each compare/mux
    // cone on its own cycle — required to close the 100 MHz budget
    // (pos+1 + 2× compare + 2× mux was -1.7 ns as one cycle).
    S_FETCH_TAP1_NXT_CALC: begin
        tap_nxt_raw <= cur_pos_int + 22'd1;
        state       <= S_FETCH_TAP1_NXT;
    end

    S_FETCH_TAP1_NXT: begin : tap1_nxt_blk
        reg [21:0] nxt;
        nxt = tap_nxt_raw;
        if (cur_loop && nxt >= cur_loop_end) nxt = cur_loop_start;
        if (nxt >= cur_length)               nxt = cur_pos_int;   // clamp
        tap_nxt_pos <= nxt;
        tap_pos     <= nxt;
        state       <= S_FETCH_TAP1_CALC;
    end

    S_FETCH_TAP1_CALC: begin
        tap_byte_addr <= sample_byte_addr(voice_base, tap_nxt_pos, cur_stereo);
        tap_araddr    <= word_aligned(sample_byte_addr(voice_base, tap_nxt_pos, cur_stereo));
        state         <= S_FETCH_TAP1_AR;
    end

    S_FETCH_TAP1_AR: begin
        m_araddr  <= tap_araddr;
        m_arlen   <= 8'd0;
        m_arvalid <= 1'b1;
        state     <= S_FETCH_TAP1_R;
    end

    S_FETCH_TAP1_R: begin
        if (m_arvalid && m_arready) m_arvalid <= 1'b0;
        m_rready <= 1'b1;
        if (m_rvalid) begin
            if (cur_stereo) begin
                tap1_l <= $signed(m_rdata[15:0]);
                tap1_r <= $signed(m_rdata[31:16]);
            end else begin
                tap1_l <= extract_sample16(m_rdata, tap_byte_addr[1]);
                tap1_r <= extract_sample16(m_rdata, tap_byte_addr[1]);
            end
            m_rready <= 1'b0;
            state    <= S_LERP_INTERP;
        end
    end

    // ---- Stage 1: linear interp (diff → mult by pos_frac → + tap0) ----
    S_LERP_INTERP: begin : lerp_interp_blk
        reg signed [16:0] diff_l, diff_r;
        reg signed [32:0] delta_l, delta_r;
        begin
            // (tap1 - tap0) * pos_frac[15:0], result scaled by 2^-16.
            diff_l  = $signed({tap1_l[15], tap1_l}) - $signed({tap0_l[15], tap0_l});
            diff_r  = $signed({tap1_r[15], tap1_r}) - $signed({tap0_r[15], tap0_r});
            delta_l = diff_l * $signed({1'b0, cur_pos_frac});
            delta_r = diff_r * $signed({1'b0, cur_pos_frac});
            /* Use arithmetic shift (>>>) so the 33-bit signed `delta`
             * is sign-extended when we slice off the fractional bits.
             * A plain bit-slice `delta_l[31:16]` is UNSIGNED in Verilog,
             * which poisons the whole `+` expression into unsigned and
             * zero-extends `tap0_l` — for any negative tap0 the result
             * is off by 65536 (caught by the mixer probe: voices were
             * active but output was nonsense). */
            lerp_l_reg <= $signed(tap0_l) + (delta_l >>> 16);
            lerp_r_reg <= $signed(tap0_r) + (delta_r >>> 16);
            state      <= S_LERP_MIX;
        end
    end

    // ---- Stage 2: volume multiply + accumulate ----
    S_LERP_MIX: begin : lerp_mix_blk
        reg signed [24:0] scaled_l, scaled_r;
        reg signed [16:0] post_l, post_r;
        begin
            // Apply per-channel volume (8-bit unsigned), shift right 8.
            scaled_l = lerp_l_reg * $signed({1'b0, cur_vol_l});
            scaled_r = lerp_r_reg * $signed({1'b0, cur_vol_r});
            /* Divide by 256 AND saturate to 16-bit signed.  A bare
             * `scaled[23:8]` slice is unsigned, so when lerp is close
             * to ±65535 (17-bit range) and vol ~255, scaled exceeds
             * ±2^23 and bit 23 becomes set on POSITIVE values, making
             * the slice look negative in s16.  Large-signal samples
             * get their sign flipped → audible as clicks.  Arithmetic
             * shift preserves sign; saturation clips to s16. */
            post_l = scaled_l >>> 8;
            post_r = scaled_r >>> 8;
            samp_l = (post_l >  17'sd32767)  ? 16'sh7FFF :
                     (post_l < -17'sd32768)  ? 16'sh8000 :
                                               post_l[15:0];
            samp_r = (post_r >  17'sd32767)  ? 16'sh7FFF :
                     (post_r < -17'sd32768)  ? 16'sh8000 :
                                               post_r[15:0];

            accum_l <= accum_l + $signed({{16{samp_l[15]}}, samp_l});
            accum_r <= accum_r + $signed({{16{samp_r[15]}}, samp_r});
            state   <= S_ADV_COMPUTE;
        end
    end

    // ---- Advance phase: stage 1 (raw position + fraction arithmetic) ----
    // Two chained 22-bit adders (pos_frac + rate_low, pos_int + carry +
    // rate_high) land into pipeline registers; the wrap/clamp/write
    // happens in S_ADVANCE next cycle so the pos_latch M10K input isn't
    // on the same path as the adders.
    S_ADV_COMPUTE: begin : adv_c_blk
        reg [17:0] nf_full;
        reg [21:0] np_raw;
        begin
            nf_full = {1'b0, cur_pos_frac} + cur_rate[15:0];
            np_raw  = cur_pos_int + {18'd0, nf_full[17:16]} + cur_rate[31:16];
            adv_new_frac_full <= nf_full;
            adv_new_pos_raw   <= np_raw;
            state             <= S_ADVANCE;
        end
    end

    // ---- Advance phase: stage 2 (wrap / clamp / commit) ----
    S_ADVANCE: begin : adv_a_blk
        reg [21:0] new_pos;
        reg        voice_ended;
        begin
            new_pos     = adv_new_pos_raw;
            voice_ended = 1'b0;

            if (cur_loop) begin
                if (new_pos >= cur_loop_end) begin
                    /* Single-subtraction wrap.  Full `%` of the loop
                     * length needs 22-bit restoring division, which
                     * Quartus synthesises as a ~70 ns combinational
                     * cone and blows the 100 MHz budget.  For every
                     * realistic playback rate (Q16.16 ≤ ~8×) the
                     * per-sample excess past loop_end is a handful
                     * of samples while loop_end-loop_start is
                     * thousands, so one subtraction always lands
                     * back inside the loop region. */
                    new_pos = new_pos - cur_loop_end + cur_loop_start;
                end
            end else begin
                if (new_pos >= cur_length) begin
                    voice_ended = 1'b1;
                end
            end

            cur_pos_int  <= new_pos;
            cur_pos_frac <= adv_new_frac_full[15:0];
            if (voice_ended) begin
                state <= S_VOICE_END;
            end else begin
                vtbl_a_addr <= {cur_voice, VTBL_POS_INT};
                vtbl_a_data <= {10'd0, new_pos};
                vtbl_a_wr   <= 1'b1;
                pos_latch[cur_voice] <= new_pos;
                state       <= S_WR_POS_FRAC;
            end
        end
    end

    S_WR_POS_FRAC: begin
        vtbl_a_addr <= {cur_voice, VTBL_POS_FRAC};
        vtbl_a_data <= {16'd0, cur_pos_frac};
        vtbl_a_wr   <= 1'b1;
        // Fall through to ramp.
        state       <= S_RAMP_STEP;
    end

    // ---- Volume ramp step (reads target + rate) ----
    S_RAMP_STEP: begin
        vtbl_a_addr <= {cur_voice, VTBL_VOL_TARGET};
        state       <= S_WR_VOL;
    end

    S_WR_VOL: begin : ramp_blk
        reg [7:0] tgt_l, tgt_r;
        reg [7:0] nxt_l, nxt_r;
        begin
            tgt_l = vtbl_a_q[7:0];
            tgt_r = vtbl_a_q[15:8];
            // Second read to grab vol_rate.  Combine with next cycle.
            vtbl_a_addr <= {cur_voice, VTBL_VOL_RATE};
            // Use rate = 1 tentatively; real rate read happens in
            // S_NEXT_VOICE tick below.  Simpler: do the step with
            // cur_vol_rate (captured previously).  For this phase we
            // ignore ramping and just snap vol to target every tick
            // — the SW mixer's ramp was per-sample, here we only
            // step once per 48 kHz tick.
            nxt_l = ramp_step(cur_vol_l, tgt_l, cur_vol_rate);
            nxt_r = ramp_step(cur_vol_r, tgt_r, cur_vol_rate);
            if (nxt_l != cur_vol_l || nxt_r != cur_vol_r) begin
                vtbl_a_addr <= {cur_voice, VTBL_VOL_LR};
                vtbl_a_data <= {16'd0, nxt_r, nxt_l};
                vtbl_a_wr   <= 1'b1;
            end
            state <= S_NEXT_VOICE;
        end
    end

    // ---- Voice-end path (one-shot) ----
    S_VOICE_END: begin
        /* Signal active-bit clear (in the shadow) and set the IRQ bit.
         * No VTBL_CTRL write here: in the split-memory v2, CTRL lives
         * in vtbl_cpu which FSM cannot write.  The shadow clear makes
         * S_CHECK_ACTIVE skip this voice on every subsequent pass until
         * CPU re-arms it with a fresh CTRL write. */
        voice_end_clear_mask[cur_voice] <= 1'b1;
        voice_end_set_mask[cur_voice] <= 1'b1;
        state       <= S_NEXT_VOICE;
    end

    S_NEXT_VOICE: begin
        if (cur_voice == 5'd31) begin
            state <= S_OUTPUT;
        end else begin
            cur_voice <= cur_voice + 5'd1;
            state     <= S_START_SAMPLE;
        end
    end

    // ---- Saturate + push stereo pair to FIFO ----
    S_OUTPUT: begin : out_blk
        reg [15:0] out_l, out_r;
        begin
            /* Mix-down /2 — probe at /8 showed peaks ~8 % FS, /2 gave
             * ~25 % FS.  Keep /2 and fix the real click cause (CDC
             * between clk_audio and audgen_sclk) in audio_output.v. */
            out_l = (accum_l >>> 1 >  32'sd32767)  ? 16'h7FFF :
                    (accum_l >>> 1 < -32'sd32768)  ? 16'h8000 :
                                                     accum_l[16:1];
            out_r = (accum_r >>> 1 >  32'sd32767)  ? 16'h7FFF :
                    (accum_r >>> 1 < -32'sd32768)  ? 16'h8000 :
                                                     accum_r[16:1];
            sample_data <= {out_l, out_r};
            sample_wr   <= 1'b1;
            /* Diagnostic: latch the outgoing sample + bump counter.
             * Firmware reads these via MIX_LAST_SAMPLE/MIX_SAMPLE_COUNT
             * to verify the HW mixer is actually producing non-zero
             * values at 48 kHz before they hit the dcfifo / I2S. */
            last_sample_data <= {out_l, out_r};
            sample_count     <= sample_count + 32'd1;
            state       <= S_IDLE;
        end
    end

    default: state <= S_IDLE;

    endcase
end

endmodule

`default_nettype wire
