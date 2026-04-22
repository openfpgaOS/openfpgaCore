//
// Hardware PCM Mixer (v2) — 32 voices from SDRAM sample pool
//
// Derived from the original CRAM1-era audio_mixer.v (retired in
// f11811c).  This is a simplified v2 port that fetches from SDRAM
// via a dedicated AXI4 read master instead of CRAM1 burst IO, and
// writes the final stereo pair directly into audio_output's dcfifo
// (audio_dma is retired).
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
    output wire        voice_end_irq
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

reg  [8:0]  vtbl_a_addr;
reg  [31:0] vtbl_a_data;
reg         vtbl_a_wr;
wire [31:0] vtbl_a_q;

wire [8:0]  vtbl_b_addr = {voice_sel, voice_field};
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

// Popcount for active_count MMIO readback.
reg [5:0] active_cnt;
integer i;
always @(posedge clk) begin
    active_cnt <= 6'd0;
    for (i = 0; i < 32; i = i + 1)
        if (voice_active[i]) active_cnt <= active_cnt + 6'd1;
end
assign active_count = active_cnt;

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

// Compute the SDRAM byte address for mono sample index `idx` given a
// voice's base_byte and its stereo flag.  Stride: 2 bytes mono, 4
// bytes stereo (L+R 16-bit interleaved).
function [31:0] sample_byte_addr;
    input [31:0] base_byte;
    input [21:0] idx;
    input        stereo;
    begin
        sample_byte_addr = sample_pool_base + base_byte
                         + (stereo ? {8'd0, idx, 2'b00}    // idx * 4
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
localparam S_RD_CTRL        = 5'd2;
localparam S_RD_CTRL_W      = 5'd3;
localparam S_CHECK_ACTIVE   = 5'd4;
localparam S_RD_POS         = 5'd5;
localparam S_RD_POS_W       = 5'd6;
localparam S_RD_BASE        = 5'd7;
localparam S_RD_BASE_W      = 5'd8;
localparam S_RD_RATE        = 5'd9;
localparam S_RD_RATE_W      = 5'd10;
localparam S_RD_LEN         = 5'd11;
localparam S_RD_LEN_W       = 5'd12;
localparam S_RD_VOL         = 5'd13;
localparam S_RD_VOL_W       = 5'd14;
localparam S_FETCH_TAP0_AR  = 5'd15;
localparam S_FETCH_TAP0_R   = 5'd16;
localparam S_FETCH_TAP1_AR  = 5'd17;
localparam S_FETCH_TAP1_R   = 5'd18;
localparam S_LERP_MIX       = 5'd19;
localparam S_ADVANCE        = 5'd20;
localparam S_WR_POS_INT     = 5'd21;
localparam S_WR_POS_FRAC    = 5'd22;
localparam S_NEXT_VOICE     = 5'd23;
localparam S_OUTPUT         = 5'd24;
localparam S_RAMP_STEP      = 5'd25;
localparam S_WR_VOL         = 5'd26;
localparam S_VOICE_END      = 5'd27;

reg [4:0] state;
reg [4:0] cur_voice;
reg [4:0] next_voice;

// Per-voice cached fields for the current pass.
reg [31:0] cur_base_byte;
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
reg [21:0] tap_pos;

// Linear-interp result (per channel).
reg signed [15:0] samp_l, samp_r;

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
        if (!voice_active[cur_voice] || !cur_ctrl[0]) begin
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
        state        <= S_FETCH_TAP0_AR;  // we'll capture loop_start next cycle
    end

    // ---- Fetch tap 0 (sample at pos_int) ----
    S_FETCH_TAP0_AR: begin
        cur_loop_start <= vtbl_a_q[21:0];
        // Defensive: if pos_int has run off the end, kill the voice now.
        if (cur_pos_int >= cur_length) begin
            state <= S_VOICE_END;
        end else begin
            tap_pos       <= cur_pos_int;
            tap_byte_addr <= sample_byte_addr(cur_base_byte, cur_pos_int, cur_stereo);
            m_araddr      <= word_aligned(sample_byte_addr(cur_base_byte, cur_pos_int, cur_stereo));
            m_arlen       <= 8'd0;    // single beat
            m_arvalid     <= 1'b1;
            state         <= S_FETCH_TAP0_R;
        end
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
            state    <= S_FETCH_TAP1_AR;
        end
    end

    // ---- Fetch tap 1 (sample at pos_int + 1, or loop_start if wrapping) ----
    S_FETCH_TAP1_AR: begin : tap1_ar_blk
        reg [21:0] nxt;
        nxt = cur_pos_int + 22'd1;
        if (cur_loop && nxt >= cur_loop_end) nxt = cur_loop_start;
        if (nxt >= cur_length)               nxt = cur_pos_int;   // clamp
        tap_pos       <= nxt;
        tap_byte_addr <= sample_byte_addr(cur_base_byte, nxt, cur_stereo);
        m_araddr      <= word_aligned(sample_byte_addr(cur_base_byte, nxt, cur_stereo));
        m_arlen       <= 8'd0;
        m_arvalid     <= 1'b1;
        state         <= S_FETCH_TAP1_R;
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
            state    <= S_LERP_MIX;
        end
    end

    // ---- Linear interp + volume multiply + accumulate ----
    S_LERP_MIX: begin : lerp_mix_blk
        reg signed [16:0] diff_l, diff_r;
        reg signed [32:0] delta_l, delta_r;
        reg signed [16:0] lerp_l, lerp_r;
        reg signed [24:0] scaled_l, scaled_r;
        begin
            // (tap1 - tap0) * pos_frac[15:0], result scaled by 2^-16.
            diff_l  = $signed({tap1_l[15], tap1_l}) - $signed({tap0_l[15], tap0_l});
            diff_r  = $signed({tap1_r[15], tap1_r}) - $signed({tap0_r[15], tap0_r});
            delta_l = diff_l * $signed({1'b0, cur_pos_frac});
            delta_r = diff_r * $signed({1'b0, cur_pos_frac});
            lerp_l  = $signed(tap0_l) + delta_l[31:16];
            lerp_r  = $signed(tap0_r) + delta_r[31:16];

            // Apply per-channel volume (8-bit unsigned), shift right 8.
            scaled_l = lerp_l * $signed({1'b0, cur_vol_l});
            scaled_r = lerp_r * $signed({1'b0, cur_vol_r});
            samp_l   = scaled_l[23:8];
            samp_r   = scaled_r[23:8];

            accum_l <= accum_l + $signed({{16{samp_l[15]}}, samp_l});
            accum_r <= accum_r + $signed({{16{samp_r[15]}}, samp_r});
            state   <= S_ADVANCE;
        end
    end

    // ---- Advance phase ----
    S_ADVANCE: begin : adv_blk
        reg [17:0] new_frac_full;
        reg [21:0] new_pos;
        reg        voice_ended;
        begin
            new_frac_full = {1'b0, cur_pos_frac} + cur_rate[15:0];
            new_pos       = cur_pos_int + {18'd0, new_frac_full[17:16]} + cur_rate[31:16];
            voice_ended   = 1'b0;

            if (cur_loop) begin
                if (new_pos >= cur_loop_end) begin
                    // Wrap into loop region.
                    new_pos = cur_loop_start
                            + ((new_pos - cur_loop_end) % (cur_loop_end - cur_loop_start));
                end
            end else begin
                if (new_pos >= cur_length) begin
                    voice_ended = 1'b1;
                end
            end

            cur_pos_int  <= new_pos;
            cur_pos_frac <= new_frac_full[15:0];
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
        // Signal active-bit clear and IRQ set for this voice; actual
        // voice_active update happens in the shadow always block.
        voice_end_clear_mask[cur_voice] <= 1'b1;
        vtbl_a_addr <= {cur_voice, VTBL_CTRL};
        vtbl_a_data <= 32'd0;
        vtbl_a_wr   <= 1'b1;
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
            // Mix-down /8 like the SW mixer — gives polyphony headroom.
            // audio_output re-boosts.
            out_l = (accum_l >>> 3 >  32'sd32767)  ? 16'h7FFF :
                    (accum_l >>> 3 < -32'sd32768)  ? 16'h8000 :
                                                     accum_l[18:3];
            out_r = (accum_r >>> 3 >  32'sd32767)  ? 16'h7FFF :
                    (accum_r >>> 3 < -32'sd32768)  ? 16'h8000 :
                                                     accum_r[18:3];
            sample_data <= {out_l, out_r};
            sample_wr   <= 1'b1;
            state       <= S_IDLE;
        end
    end

    default: state <= S_IDLE;

    endcase
end

endmodule

`default_nettype wire
