//
// Hardware PCM Mixer — 32 voices from CRAM1
//
// Reads signed mono samples (16-bit or 8-bit) from CRAM1 (via shared CDC
// adapter), resamples via 16.16 fixed-point, mixes with per-voice stereo
// 8-bit volume (log curve), writes packed stereo pairs to the audio FIFO.
//
// Voice descriptors stored in dual-port BRAM (2 M10K blocks).
// CPU writes via indirect register interface (select voice, write field).
// Mixer FSM reads sequentially during mix cycle.
//
// Per-voice features:
//   - 8-bit stereo volume with log curve (VOL_L, VOL_R)
//   - Hardware volume ramp (VOL_TARGET + VOL_RATE)
//   - 16.16 fixed-point resampling
//   - Forward and bidirectional (ping-pong) looping with LOOP_START/LOOP_END
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

    // Voice configuration (from axi_periph_slave)
    input wire        voice_wr,
    input wire [3:0]  voice_field,   // 0-10: BRAM fields
    input wire [4:0]  voice_sel,     // selected voice index
    input wire [31:0] voice_wdata,

    // Back-pressure: HIGH when the mixer cannot accept a CPU write
    // (FSM owns port A and deferred latch is full). AXI slave must
    // hold bvalid=0 until this drops.
    output wire       voice_wr_stall,

    // CRAM1 read interface (shared with CPU via arbiter)
    output reg         cram1_rd,
    output reg  [21:0] cram1_addr,
    input  wire [31:0] cram1_rdata,
    input  wire        cram1_busy,
    input  wire        cram1_rdata_valid,

    // Audio FIFO interface
    output reg         sample_wr,
    output reg  [31:0] sample_data,
    input  wire [8:0]  fifo_level,

    // Status
    output wire [4:0]  active_count,

    // Position read-back (latched for voice_sel during mix)
    output reg  [21:0] pos_readback,

    // Voice-end IRQ
    input  wire [31:0] irq_clear,     // W1C from CPU
    input  wire        irq_clear_wr,
    output reg  [31:0] voice_end_pending,
    output wire        voice_end_irq
);

wire mixer_active = reset_n & mixer_enable;
assign voice_end_irq = |voice_end_pending;

// ============================================
// Voice table in dual-port BRAM
// ============================================
// 16-word stride per voice, 32 voices = 512 words (2 M10K blocks)
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
//   Word 11-15: reserved

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

// Dual-port BRAM: port A = CPU writes + mixer writes, port B = mixer reads
reg  [31:0] vtbl_a_data;
reg  [8:0]  vtbl_a_addr;
reg         vtbl_a_wr;
wire [31:0] vtbl_b_data;
reg  [8:0]  vtbl_b_addr;

altsyncram #(
    .operation_mode("DUAL_PORT"),
    .width_a(32),
    .widthad_a(9),
    .width_b(32),
    .widthad_b(9),
    .numwords_a(512),
    .numwords_b(512),
    .clock_enable_input_a("BYPASS"),
    .clock_enable_input_b("BYPASS"),
    .clock_enable_output_b("BYPASS"),
    .outdata_reg_b("UNREGISTERED"),
    .intended_device_family("Cyclone V"),
    .lpm_type("altsyncram"),
    .power_up_uninitialized("FALSE")
) voice_table (
    .clock0(clk),
    .address_a(vtbl_a_addr),
    .data_a(vtbl_a_data),
    .wren_a(vtbl_a_wr),
    .clock1(clk),
    .address_b(vtbl_b_addr),
    .q_b(vtbl_b_data),
    .wren_b(1'b0),
    .aclr0(1'b0), .aclr1(1'b0),
    .addressstall_a(1'b0), .addressstall_b(1'b0),
    .byteena_a(1'b1), .byteena_b(1'b1),
    .clocken0(1'b1), .clocken1(1'b1),
    .clocken2(1'b1), .clocken3(1'b1),
    .data_b(32'b0), .eccstatus(),
    .q_a(), .rden_a(1'b0), .rden_b(1'b1)
);

// ============================================
// CPU voice write (port A, when FSM not writing)
// ============================================
reg [31:0] voice_active;

reg        cpu_wr_pending;
reg        cpu_clear_pos;
reg [8:0]  cpu_clear_base;

reg        fsm_wr_active;

// CPU write FIFO: absorbs bursts of register writes while the FSM
// owns BRAM port A. 8-deep is enough for of_mixer_play (6 writes)
// + of_mixer_set_loop (3 writes) back-to-back.
localparam WRFIFO_DEPTH = 8;
localparam WRFIFO_BITS  = 3;  // log2(8)

reg [40:0] wrfifo [0:WRFIFO_DEPTH-1];  // {field[3:0], sel[4:0], data[31:0]} = 41 bits
reg [WRFIFO_BITS:0] wrfifo_wr_ptr;     // extra bit for full/empty detection
reg [WRFIFO_BITS:0] wrfifo_rd_ptr;

wire wrfifo_empty = (wrfifo_wr_ptr == wrfifo_rd_ptr);
wire wrfifo_full  = (wrfifo_wr_ptr[WRFIFO_BITS] != wrfifo_rd_ptr[WRFIFO_BITS]) &&
                    (wrfifo_wr_ptr[WRFIFO_BITS-1:0] == wrfifo_rd_ptr[WRFIFO_BITS-1:0]);

// Back-pressure: high when FIFO is full. AXI slave should stall.
// (Currently unused — FIFO depth is sufficient for all bursts.)
assign voice_wr_stall = wrfifo_full;

// ============================================
// Mixer FSM
// ============================================
localparam S_IDLE       = 5'd0;
localparam S_CPU_WR     = 5'd1;
localparam S_CPU_CLR1   = 5'd2;
localparam S_CPU_CLR2   = 5'd3;
localparam S_RD_CTRL    = 5'd4;
localparam S_RD_CTRL_W  = 5'd5;
localparam S_RD_FIELDS  = 5'd6;
localparam S_RD_FIELDS_W= 5'd7;
localparam S_CRAM_REQ   = 5'd8;
localparam S_CRAM_WAIT  = 5'd9;
localparam S_SCALE      = 5'd10;
localparam S_MULTIPLY   = 5'd11;
localparam S_ACCUM      = 5'd17;
localparam S_VOL_RAMP   = 5'd12;
localparam S_WR_POS     = 5'd13;
localparam S_WR_FRAC    = 5'd14;
localparam S_OUTPUT     = 5'd15;
localparam S_WR_DIR     = 5'd16;

reg [4:0]  state;
reg [4:0]  cur_voice;
reg signed [31:0] accum_l, accum_r;
reg [4:0]  voice_cnt;
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

reg        new_dir;
reg        dir_changed;
reg        pos_wrapped;  // set in S_WR_POS when loop/end triggered

// Pipeline registers
reg signed [15:0] pipe_scaled_l;
reg signed [15:0] pipe_scaled_r;

// Position advance
wire [37:0] pos_combined = {cur_pos_int, cur_pos_frac};
wire [37:0] new_pos_fwd = pos_combined + {6'd0, cur_rate};
wire [37:0] new_pos_rev = pos_combined - {6'd0, cur_rate};
wire [37:0] new_pos = cur_dir ? new_pos_rev : new_pos_fwd;
wire [21:0] new_pos_int = new_pos[37:16];
wire [15:0] new_pos_frac = new_pos[15:0];
wire rev_underflow = cur_dir && new_pos[37];

// Sample selection
wire signed [15:0] raw_sample_16 = cur_pos_int[0]
                                  ? $signed(cram1_rdata[31:16])
                                  : $signed(cram1_rdata[15:0]);

wire [1:0] byte_sel = cur_pos_int[1:0];
wire signed [7:0] raw_byte = byte_sel == 2'd0 ? $signed(cram1_rdata[7:0])   :
                              byte_sel == 2'd1 ? $signed(cram1_rdata[15:8])  :
                              byte_sel == 2'd2 ? $signed(cram1_rdata[23:16]) :
                                                 $signed(cram1_rdata[31:24]);
wire signed [15:0] raw_sample_8 = {raw_byte, 8'd0};
wire signed [15:0] raw_sample = cur_fmt16 ? raw_sample_16 : raw_sample_8;

// Log volume LUT: x² >> 8 precomputed (replaces 2 combinational multipliers)
reg [7:0] log_vol_lut [0:255];
integer _i;
initial for (_i = 0; _i < 256; _i = _i + 1)
    log_vol_lut[_i] = (_i * _i) >> 8;

// Registered LUT outputs (read in S_SCALE, used in S_ACCUM)
reg [7:0] log_vol_l, log_vol_r;

// Stereo volume scaling with log curve
wire signed [23:0] prod_l = raw_sample * $signed({1'b0, log_vol_l});
wire signed [23:0] prod_r = raw_sample * $signed({1'b0, log_vol_r});
wire signed [15:0] scaled_l = prod_l[23:8];
wire signed [15:0] scaled_r = prod_r[23:8];

// Mix-down: attenuate to prevent clipping.
// Shift right by 1 (÷2) — headroom for multi-voice mixing.
// Combined with log volume curve (x²/256), 4 voices at vol=180 ≈ full scale.
wire signed [31:0] mix_l = accum_l >>> 1;
wire signed [31:0] mix_r = accum_r >>> 1;

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

// BRAM read pipeline phase counter (needs 4 bits for 11 phases)
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
        vtbl_b_addr <= 0;
        voice_active <= 32'd0;
        cpu_wr_pending <= 0;
        cpu_clear_pos <= 0;
        cpu_clear_base <= 0;
        fsm_wr_active <= 0;
        pos_readback <= 0;
        dir_changed <= 0;
        new_dir <= 0;
        voice_end_pending <= 32'd0;
        wrfifo_wr_ptr <= 0;
        wrfifo_rd_ptr <= 0;
    end else begin
        sample_wr <= 0;
        cram1_rd <= 0;

        // Voice-end IRQ clear (W1C)
        if (irq_clear_wr)
            voice_end_pending <= voice_end_pending & ~irq_clear;

        // Push CPU writes into FIFO when:
        //   - FSM owns port A (can't write directly), OR
        //   - FIFO already has pending entries (preserves write order)
        // Without the second condition, a direct write could jump ahead
        // of older queued writes, corrupting voice setup sequences.
        if (voice_wr && (fsm_wr_active || !wrfifo_empty) && !wrfifo_full) begin
            wrfifo[wrfifo_wr_ptr[WRFIFO_BITS-1:0]] <= {voice_field, voice_sel, voice_wdata};
            wrfifo_wr_ptr <= wrfifo_wr_ptr + 1;
        end

        // CPU writes go directly to BRAM port A only when FSM is idle
        // AND the FIFO is empty (no older writes to drain first).
        if (voice_wr && !fsm_wr_active && wrfifo_empty) begin
            vtbl_a_wr <= 1;
            vtbl_a_addr <= {voice_sel, voice_field};
            vtbl_a_data <= voice_wdata;
            if (voice_field == VTBL_CTRL)
                voice_active[voice_sel] <= voice_wdata[0];
            if (voice_field == VTBL_CTRL && voice_wdata[0] && !voice_active[voice_sel]) begin
                cpu_clear_pos <= 1;
                cpu_clear_base <= {voice_sel, 4'd0};
            end
        end else if (!wrfifo_empty && !fsm_wr_active) begin
            // Drain one FIFO entry to BRAM.
            // Entry format: {field[3:0], sel[4:0], data[31:0]} = 41 bits
            //   [40:37]=field, [36:32]=sel, [31:0]=data
            begin : fifo_pop
                reg [40:0] fe;
                reg [3:0]  ff;
                reg [4:0]  fs;
                reg [31:0] fd;
                fe = wrfifo[wrfifo_rd_ptr[WRFIFO_BITS-1:0]];
                ff = fe[40:37];
                fs = fe[36:32];
                fd = fe[31:0];
                vtbl_a_wr  <= 1;
                vtbl_a_addr <= {fs, ff};
                vtbl_a_data <= fd;
                if (ff == VTBL_CTRL) begin
                    voice_active[fs] <= fd[0];
                    if (fd[0] && !voice_active[fs]) begin
                        cpu_clear_pos  <= 1;
                        cpu_clear_base <= {fs, 4'd0};
                    end
                end
            end
            wrfifo_rd_ptr <= wrfifo_rd_ptr + 1;
        end else if (cpu_clear_pos && !fsm_wr_active) begin
            vtbl_a_wr <= 1;
            vtbl_a_addr <= {cpu_clear_base[8:4], VTBL_POS_INT};
            vtbl_a_data <= 32'd0;
            cpu_clear_pos <= 0;
            cpu_wr_pending <= 1;
        end else if (cpu_wr_pending && !fsm_wr_active) begin
            vtbl_a_wr <= 1;
            vtbl_a_addr <= {cpu_clear_base[8:4], VTBL_POS_FRAC};
            vtbl_a_data <= 32'd0;
            cpu_wr_pending <= 0;
        end else begin
            vtbl_a_wr <= 0;
        end

        if (!mixer_active) begin
            state <= S_IDLE;
        end else begin
            case (state)

            S_IDLE: begin
                fsm_wr_active <= 0;
                if (fifo_level < 9'd480) begin
                    accum_l <= 0;
                    accum_r <= 0;
                    cur_voice <= 0;
                    voice_cnt <= 0;
                    vtbl_b_addr <= {5'd0, VTBL_CTRL};
                    state <= S_RD_CTRL;
                end
            end

            S_RD_CTRL: begin
                state <= S_RD_CTRL_W;
            end

            S_RD_CTRL_W: begin
                cur_loop   <= vtbl_b_data[1];
                cur_fmt16  <= vtbl_b_data[2];
                cur_bidi   <= vtbl_b_data[3];
                cur_dir    <= vtbl_b_data[4];

                if (!voice_active[cur_voice]) begin
                    if (cur_voice == 5'd31)
                        state <= S_OUTPUT;
                    else begin
                        cur_voice <= cur_voice + 1;
                        vtbl_b_addr <= {cur_voice + 5'd1, VTBL_CTRL};
                        state <= S_RD_CTRL;
                    end
                end else begin
                    vtbl_b_addr <= {cur_voice, VTBL_ADDR};
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
                        cur_addr <= vtbl_b_data[21:0];
                        vtbl_b_addr <= {cur_voice, VTBL_LEN};
                        bram_rd_phase <= 1;
                        state <= S_RD_FIELDS;
                    end
                    4'd1: begin  // LEN
                        cur_len <= vtbl_b_data[21:0];
                        vtbl_b_addr <= {cur_voice, VTBL_RATE};
                        bram_rd_phase <= 2;
                        state <= S_RD_FIELDS;
                    end
                    4'd2: begin  // RATE
                        cur_rate <= vtbl_b_data;
                        vtbl_b_addr <= {cur_voice, VTBL_POS_INT};
                        bram_rd_phase <= 3;
                        state <= S_RD_FIELDS;
                    end
                    4'd3: begin  // POS_INT
                        cur_pos_int <= vtbl_b_data[21:0];
                        if (cur_voice == voice_sel)
                            pos_readback <= vtbl_b_data[21:0];
                        vtbl_b_addr <= {cur_voice, VTBL_POS_FRAC};
                        bram_rd_phase <= 4;
                        state <= S_RD_FIELDS;
                    end
                    4'd4: begin  // POS_FRAC
                        cur_pos_frac <= vtbl_b_data[15:0];
                        vtbl_b_addr <= {cur_voice, VTBL_VOL_LR};
                        bram_rd_phase <= 5;
                        state <= S_RD_FIELDS;
                    end
                    4'd5: begin  // VOL_LR
                        cur_vol_l <= vtbl_b_data[7:0];
                        cur_vol_r <= vtbl_b_data[15:8];
                        vtbl_b_addr <= {cur_voice, VTBL_LOOP_END};
                        bram_rd_phase <= 6;
                        state <= S_RD_FIELDS;
                    end
                    4'd6: begin  // LOOP_END
                        cur_loop_end <= vtbl_b_data[21:0];
                        vtbl_b_addr <= {cur_voice, VTBL_LOOP_START};
                        bram_rd_phase <= 7;
                        state <= S_RD_FIELDS;
                    end
                    4'd7: begin  // LOOP_START
                        cur_loop_start <= vtbl_b_data[21:0];
                        vtbl_b_addr <= {cur_voice, VTBL_VOL_TARGET};
                        bram_rd_phase <= 8;
                        state <= S_RD_FIELDS;
                    end
                    4'd8: begin  // VOL_TARGET
                        cur_target_l <= vtbl_b_data[7:0];
                        cur_target_r <= vtbl_b_data[15:8];
                        vtbl_b_addr <= {cur_voice, VTBL_VOL_RATE};
                        bram_rd_phase <= 9;
                        state <= S_RD_FIELDS;
                    end
                    4'd9: begin  // VOL_RATE + end-of-field check
                        cur_vol_rate <= vtbl_b_data[7:0];
                        if (cur_pos_int >= cur_len) begin
                            voice_active[cur_voice] <= 0;
                            voice_end_pending[cur_voice] <= 1;
                            if (cur_voice == 5'd31)
                                state <= S_OUTPUT;
                            else begin
                                cur_voice <= cur_voice + 1;
                                vtbl_b_addr <= {cur_voice + 5'd1, VTBL_CTRL};
                                state <= S_RD_CTRL;
                            end
                        end else
                            state <= S_CRAM_REQ;
                    end
                    default: state <= S_CRAM_REQ;
                endcase
            end

            // ---- CRAM1 read ----
            S_CRAM_REQ: begin
                if (!cram1_busy) begin
                    cram1_rd <= 1;
                    cram1_addr <= cur_fmt16
                        ? cur_addr + {1'b0, cur_pos_int[21:1]}
                        : cur_addr + {2'b0, cur_pos_int[21:2]};
                    state <= S_CRAM_WAIT;
                end
            end

            S_CRAM_WAIT: begin
                if (cram1_rdata_valid)
                    state <= S_SCALE;
            end

            // ---- Pipeline stage 1: log volume LUT lookup ----
            S_SCALE: begin
                log_vol_l <= log_vol_lut[cur_vol_l];
                log_vol_r <= log_vol_lut[cur_vol_r];
                state <= S_MULTIPLY;
            end

            // ---- Pipeline stage 2: register multiply products ----
            S_MULTIPLY: begin
                pipe_scaled_l <= scaled_l;
                pipe_scaled_r <= scaled_r;
                state <= S_ACCUM;
            end

            // ---- Pipeline stage 3: accumulate ----
            S_ACCUM: begin
                accum_l <= accum_l + {{16{pipe_scaled_l[15]}}, pipe_scaled_l};
                accum_r <= accum_r + {{16{pipe_scaled_r[15]}}, pipe_scaled_r};
                voice_cnt <= voice_cnt + 1;
                dir_changed <= 0;
                state <= S_VOL_RAMP;
            end

            // ---- Volume ramp: step VOL_LR toward VOL_TARGET ----
            S_VOL_RAMP: begin
                fsm_wr_active <= 1;
                if (cur_vol_rate == 8'd0) begin
                    // Instant: snap to target
                    if (cur_vol_l != cur_target_l || cur_vol_r != cur_target_r) begin
                        vtbl_a_wr <= 1;
                        vtbl_a_addr <= {cur_voice, VTBL_VOL_LR};
                        vtbl_a_data <= {16'd0, cur_target_r, cur_target_l};
                    end
                end else begin
                    // Ramp: step toward target
                    begin : ramp_block
                        reg [7:0] new_l, new_r;
                        new_l = ramp_step(cur_vol_l, cur_target_l, cur_vol_rate);
                        new_r = ramp_step(cur_vol_r, cur_target_r, cur_vol_rate);
                        if (new_l != cur_vol_l || new_r != cur_vol_r) begin
                            vtbl_a_wr <= 1;
                            vtbl_a_addr <= {cur_voice, VTBL_VOL_LR};
                            vtbl_a_data <= {16'd0, new_r, new_l};
                        end
                    end
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
                        vtbl_a_data <= {10'd0, cur_loop_start};
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
                    // Loop wrap or voice end — reset or skip frac
                    vtbl_a_wr <= cur_loop;  // write 0 if looping, skip if ended
                    vtbl_a_data <= 32'd0;
                end else begin
                    vtbl_a_wr <= 1;
                    vtbl_a_data <= {16'd0, new_pos_frac};
                end

                if (dir_changed)
                    state <= S_WR_DIR;
                else if (cur_voice == 5'd31)
                    state <= S_OUTPUT;
                else begin
                    cur_voice <= cur_voice + 1;
                    vtbl_b_addr <= {cur_voice + 5'd1, VTBL_CTRL};
                    state <= S_RD_CTRL;
                    // Release port A between voices so the CPU write FIFO
                    // can drain during the next voice's read-only states
                    // (S_RD_CTRL through S_ACCUM, ~30 cycles).
                    fsm_wr_active <= 0;
                end
            end

            S_WR_DIR: begin
                vtbl_a_wr <= 1;
                vtbl_a_addr <= {cur_voice, VTBL_CTRL};
                vtbl_a_data <= {27'd0, new_dir, cur_bidi, cur_fmt16, cur_loop, 1'b1};

                if (cur_voice == 5'd31)
                    state <= S_OUTPUT;
                else begin
                    cur_voice <= cur_voice + 1;
                    vtbl_b_addr <= {cur_voice + 5'd1, VTBL_CTRL};
                    state <= S_RD_CTRL;
                    // Release port A between voices (same as S_WR_FRAC).
                    fsm_wr_active <= 0;
                end
            end

            S_OUTPUT: begin
                fsm_wr_active <= 0;
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
