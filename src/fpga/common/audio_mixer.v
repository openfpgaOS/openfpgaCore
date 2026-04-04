//
// Hardware PCM Mixer — 32 voices from CRAM1
//
// Reads signed mono samples (16-bit or 8-bit) from CRAM1 (via shared CDC
// adapter), resamples via 16.16 fixed-point, mixes with per-voice stereo
// 8-bit volume, writes packed stereo pairs to the audio FIFO.
//
// Voice descriptors stored in dual-port BRAM (1 M10K block).
// CPU writes via indirect register interface (select voice, write field).
// Mixer FSM reads sequentially during mix cycle.
//
// Per-voice features:
//   - 8-bit stereo volume (VOL_L, VOL_R)
//   - Forward and bidirectional (ping-pong) looping
//   - Configurable loop end point (LOOP_END)
//   - 16-bit or 8-bit signed sample format
//   - Position read-back and write for CPU
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
    input wire [2:0]  voice_field,   // 0=addr, 1=len, 2=rate, 3=ctrl, 4=pos_int, 5=pos_frac, 6=vol_lr, 7=loop_end
    input wire [4:0]  voice_sel,     // selected voice index
    input wire [31:0] voice_wdata,

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
    output reg  [21:0] pos_readback
);

wire mixer_active = reset_n & mixer_enable;

// ============================================
// Voice table in dual-port BRAM
// ============================================
// 8-word stride per voice, 32 voices = 256 words (1 M10K block)
//
//   Word 0: base_addr[21:0]    — CRAM1 word address
//   Word 1: length[21:0]       — sample length in format-specific units
//   Word 2: rate[31:0]         — 16.16 fixed-point playback rate
//   Word 3: ctrl               — {24'b0, dir[4], 3'b0, bidi[3], fmt16[2], loop[1], active[0]}
//   Word 4: pos_int[21:0]      — integer part of playback position
//   Word 5: pos_frac[15:0]     — fractional part
//   Word 6: vol_lr             — {16'b0, vol_r[7:0], vol_l[7:0]}
//   Word 7: loop_end[21:0]     — loop end point (default = length)

localparam VTBL_STRIDE   = 8;
localparam VTBL_ADDR     = 0;
localparam VTBL_LEN      = 1;
localparam VTBL_RATE     = 2;
localparam VTBL_CTRL     = 3;
localparam VTBL_POS_INT  = 4;
localparam VTBL_POS_FRAC = 5;
localparam VTBL_VOL_LR   = 6;
localparam VTBL_LOOP_END = 7;

// Dual-port BRAM: port A = CPU writes + mixer writes, port B = mixer reads
reg  [31:0] vtbl_a_data;
reg  [7:0]  vtbl_a_addr;
reg         vtbl_a_wr;
wire [31:0] vtbl_b_data;
reg  [7:0]  vtbl_b_addr;

altsyncram #(
    .operation_mode("DUAL_PORT"),
    .width_a(32),
    .widthad_a(8),
    .width_b(32),
    .widthad_b(8),
    .numwords_a(256),
    .numwords_b(256),
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
// Voice active flags — register, not BRAM (avoids write race on deactivation)
reg [31:0] voice_active;

// CPU write state — serviced by FSM
reg        cpu_wr_pending;
reg        cpu_clear_pos;
reg [7:0]  cpu_clear_base;
reg        cpu_init_loopend; // queue default LOOP_END write
reg [21:0] cpu_init_len;     // cached LEN for LOOP_END default

reg        fsm_wr_active;

// ============================================
// Mixer FSM
// ============================================
localparam S_IDLE      = 4'd0;
localparam S_CPU_WR    = 4'd1;
localparam S_CPU_CLR1  = 4'd2;
localparam S_CPU_CLR2  = 4'd3;
localparam S_RD_CTRL   = 4'd4;
localparam S_RD_CTRL_W = 4'd5;
localparam S_RD_FIELDS = 4'd6;
localparam S_RD_FIELDS_W= 4'd7;
localparam S_CRAM_REQ  = 4'd8;
localparam S_CRAM_WAIT = 4'd9;
localparam S_ACCUM     = 4'd10;
localparam S_WR_POS    = 4'd11;
localparam S_WR_FRAC   = 4'd12;
localparam S_OUTPUT    = 4'd13;
localparam S_WR_DIR    = 4'd14;  // write back direction change for bidi

reg [3:0]  state;
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

// Direction change flag (set in S_WR_POS for bidi, written in S_WR_DIR)
reg        new_dir;
reg        dir_changed;

// Position advance: {pos_int[21:0], pos_frac[15:0]} ± rate[31:0]
wire [37:0] pos_combined = {cur_pos_int, cur_pos_frac};
wire [37:0] new_pos_fwd = pos_combined + {6'd0, cur_rate};
wire [37:0] new_pos_rev = pos_combined - {6'd0, cur_rate};
wire [37:0] new_pos = cur_dir ? new_pos_rev : new_pos_fwd;
wire [21:0] new_pos_int = new_pos[37:16];
wire [15:0] new_pos_frac = new_pos[15:0];

// Reverse underflow detection (position went negative)
wire rev_underflow = cur_dir && new_pos[37]; // sign bit of subtraction

// Sample selection: 16-bit or 8-bit format
wire signed [15:0] raw_sample_16 = cur_pos_int[0]
                                  ? $signed(cram1_rdata[31:16])
                                  : $signed(cram1_rdata[15:0]);

wire [1:0] byte_sel = cur_pos_int[1:0];
wire signed [7:0] raw_byte = byte_sel == 2'd0 ? $signed(cram1_rdata[7:0])   :
                              byte_sel == 2'd1 ? $signed(cram1_rdata[15:8])  :
                              byte_sel == 2'd2 ? $signed(cram1_rdata[23:16]) :
                                                 $signed(cram1_rdata[31:24]);
wire signed [15:0] raw_sample_8 = {raw_byte, 8'd0}; // scale to 16-bit range

wire signed [15:0] raw_sample = cur_fmt16 ? raw_sample_16 : raw_sample_8;

// Stereo volume scaling: 16-bit sample × 8-bit volume → 24-bit, take [23:8]
wire signed [23:0] prod_l = raw_sample * $signed({1'b0, cur_vol_l});
wire signed [23:0] prod_r = raw_sample * $signed({1'b0, cur_vol_r});
wire signed [15:0] scaled_l = prod_l[23:8];
wire signed [15:0] scaled_r = prod_r[23:8];

// Output clamp
wire [15:0] clamp_l = (accum_l > 32'sd32767)  ? 16'h7FFF :
                      (accum_l < -32'sd32768) ? 16'h8000 : accum_l[15:0];
wire [15:0] clamp_r = (accum_r > 32'sd32767)  ? 16'h7FFF :
                      (accum_r < -32'sd32768) ? 16'h8000 : accum_r[15:0];

// BRAM read pipeline phase counter
reg [2:0] bram_rd_phase;

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
        cpu_init_loopend <= 0;
        cpu_init_len <= 0;
        fsm_wr_active <= 0;
        pos_readback <= 0;
        dir_changed <= 0;
        new_dir <= 0;
    end else begin
        sample_wr <= 0;
        cram1_rd <= 0;

        // CPU writes go directly to BRAM port A (when FSM isn't using it)
        if (voice_wr && !fsm_wr_active) begin
            vtbl_a_wr <= 1;
            vtbl_a_addr <= {voice_sel, voice_field};
            vtbl_a_data <= voice_wdata;
            // If writing ctrl, update active flag register
            if (voice_field == 3'd3) begin
                voice_active[voice_sel] <= voice_wdata[0];
            end
            // If writing ctrl with active=1, queue position clear
            if (voice_field == 3'd3 && voice_wdata[0]) begin
                cpu_clear_pos <= 1;
                cpu_clear_base <= {voice_sel, 3'd0};
            end
            // If writing LEN, also set LOOP_END = LEN as default
            if (voice_field == 3'd1) begin
                cpu_init_loopend <= 1;
                cpu_init_len <= voice_wdata[21:0];
            end
        end else if (cpu_clear_pos && !fsm_wr_active) begin
            // Clear pos_int
            vtbl_a_wr <= 1;
            vtbl_a_addr <= {cpu_clear_base[7:3], VTBL_POS_INT[2:0]};
            vtbl_a_data <= 32'd0;
            cpu_clear_pos <= 0;
            cpu_wr_pending <= 1;
        end else if (cpu_wr_pending && !fsm_wr_active) begin
            // Clear pos_frac
            vtbl_a_wr <= 1;
            vtbl_a_addr <= {cpu_clear_base[7:3], VTBL_POS_FRAC[2:0]};
            vtbl_a_data <= 32'd0;
            cpu_wr_pending <= 0;
        end else if (cpu_init_loopend && !fsm_wr_active) begin
            // Set LOOP_END = LEN as default
            vtbl_a_wr <= 1;
            vtbl_a_addr <= {voice_sel, VTBL_LOOP_END[2:0]};
            vtbl_a_data <= {10'd0, cpu_init_len};
            cpu_init_loopend <= 0;
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
                    // Start mix cycle
                    accum_l <= 0;
                    accum_r <= 0;
                    cur_voice <= 0;
                    voice_cnt <= 0;
                    vtbl_b_addr <= {5'd0, VTBL_CTRL[2:0]};
                    state <= S_RD_CTRL;
                end
            end

            // ---- Read voice ctrl ----
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
                        vtbl_b_addr <= {cur_voice + 5'd1, VTBL_CTRL[2:0]};
                        state <= S_RD_CTRL;
                    end
                end else begin
                    vtbl_b_addr <= {cur_voice, VTBL_ADDR[2:0]};
                    bram_rd_phase <= 0;
                    state <= S_RD_FIELDS;
                end
            end

            // ---- Read remaining fields ----
            S_RD_FIELDS: begin
                state <= S_RD_FIELDS_W;
            end

            S_RD_FIELDS_W: begin
                case (bram_rd_phase)
                    3'd0: begin  // ADDR
                        cur_addr <= vtbl_b_data[21:0];
                        vtbl_b_addr <= {cur_voice, VTBL_LEN[2:0]};
                        bram_rd_phase <= 1;
                        state <= S_RD_FIELDS;
                    end
                    3'd1: begin  // LEN
                        cur_len <= vtbl_b_data[21:0];
                        vtbl_b_addr <= {cur_voice, VTBL_RATE[2:0]};
                        bram_rd_phase <= 2;
                        state <= S_RD_FIELDS;
                    end
                    3'd2: begin  // RATE
                        cur_rate <= vtbl_b_data;
                        vtbl_b_addr <= {cur_voice, VTBL_POS_INT[2:0]};
                        bram_rd_phase <= 3;
                        state <= S_RD_FIELDS;
                    end
                    3'd3: begin  // POS_INT
                        cur_pos_int <= vtbl_b_data[21:0];
                        // Latch position for CPU read-back
                        if (cur_voice == voice_sel)
                            pos_readback <= vtbl_b_data[21:0];
                        vtbl_b_addr <= {cur_voice, VTBL_POS_FRAC[2:0]};
                        bram_rd_phase <= 4;
                        state <= S_RD_FIELDS;
                    end
                    3'd4: begin  // POS_FRAC
                        cur_pos_frac <= vtbl_b_data[15:0];
                        vtbl_b_addr <= {cur_voice, VTBL_VOL_LR[2:0]};
                        bram_rd_phase <= 5;
                        state <= S_RD_FIELDS;
                    end
                    3'd5: begin  // VOL_LR
                        cur_vol_l <= vtbl_b_data[7:0];
                        cur_vol_r <= vtbl_b_data[15:8];
                        vtbl_b_addr <= {cur_voice, VTBL_LOOP_END[2:0]};
                        bram_rd_phase <= 6;
                        state <= S_RD_FIELDS;
                    end
                    3'd6: begin  // LOOP_END
                        cur_loop_end <= vtbl_b_data[21:0];
                        // Check if position past end
                        if (cur_pos_int >= cur_len) begin
                            voice_active[cur_voice] <= 0;
                            if (cur_voice == 5'd31)
                                state <= S_OUTPUT;
                            else begin
                                cur_voice <= cur_voice + 1;
                                vtbl_b_addr <= {cur_voice + 5'd1, VTBL_CTRL[2:0]};
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
                    // 16-bit: 2 samples/word, 8-bit: 4 samples/word
                    cram1_addr <= cur_fmt16
                        ? cur_addr + {1'b0, cur_pos_int[21:1]}
                        : cur_addr + {2'b0, cur_pos_int[21:2]};
                    state <= S_CRAM_WAIT;
                end
            end

            S_CRAM_WAIT: begin
                if (cram1_rdata_valid) begin
                    state <= S_ACCUM;
                end
            end

            // ---- Accumulate with stereo volume ----
            S_ACCUM: begin
                accum_l <= accum_l + {{16{scaled_l[15]}}, scaled_l};
                accum_r <= accum_r + {{16{scaled_r[15]}}, scaled_r};
                voice_cnt <= voice_cnt + 1;
                dir_changed <= 0;
                state <= S_WR_POS;
            end

            // ---- Write back new position ----
            S_WR_POS: begin
                fsm_wr_active <= 1;
                vtbl_a_wr <= 1;
                vtbl_a_addr <= {cur_voice, VTBL_POS_INT[2:0]};

                if (cur_loop && cur_bidi) begin
                    // Bidirectional loop
                    if (!cur_dir && new_pos_int >= cur_loop_end) begin
                        // Forward hit end → clamp to end, flip to reverse
                        vtbl_a_data <= {10'd0, cur_loop_end - 22'd1};
                        new_dir <= 1;
                        dir_changed <= 1;
                    end else if (rev_underflow) begin
                        // Reverse underflow → clamp to 0, flip to forward
                        vtbl_a_data <= 32'd0;
                        new_dir <= 0;
                        dir_changed <= 1;
                    end else begin
                        vtbl_a_data <= {10'd0, new_pos_int};
                    end
                end else if (cur_loop && !cur_bidi) begin
                    // Forward loop
                    if (new_pos_int >= cur_loop_end) begin
                        vtbl_a_data <= 32'd0;  // restart from beginning
                    end else begin
                        vtbl_a_data <= {10'd0, new_pos_int};
                    end
                end else begin
                    // No loop
                    if (new_pos_int >= cur_len) begin
                        voice_active[cur_voice] <= 0;
                    end
                    vtbl_a_data <= {10'd0, new_pos_int};
                end

                state <= S_WR_FRAC;
            end

            S_WR_FRAC: begin
                vtbl_a_addr <= {cur_voice, VTBL_POS_FRAC[2:0]};

                if (cur_loop && cur_bidi && ((!cur_dir && new_pos_int >= cur_loop_end) || rev_underflow)) begin
                    // Bidi direction change — reset frac
                    vtbl_a_wr <= 1;
                    vtbl_a_data <= 32'd0;
                end else if (cur_loop && !cur_bidi && new_pos_int >= cur_loop_end) begin
                    // Forward loop wrap — reset frac
                    vtbl_a_wr <= 1;
                    vtbl_a_data <= 32'd0;
                end else if (!cur_loop && new_pos_int >= cur_len) begin
                    // Voice ended — no frac write needed
                    vtbl_a_wr <= 0;
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
                    vtbl_b_addr <= {cur_voice + 5'd1, VTBL_CTRL[2:0]};
                    state <= S_RD_CTRL;
                end
            end

            // ---- Write back direction change for bidi ----
            S_WR_DIR: begin
                vtbl_a_wr <= 1;
                vtbl_a_addr <= {cur_voice, VTBL_CTRL[2:0]};
                // Preserve existing CTRL bits, update DIR (bit 4)
                vtbl_a_data <= {27'd0, new_dir, 3'd0, cur_bidi, cur_fmt16, cur_loop, 1'b1};

                if (cur_voice == 5'd31)
                    state <= S_OUTPUT;
                else begin
                    cur_voice <= cur_voice + 1;
                    vtbl_b_addr <= {cur_voice + 5'd1, VTBL_CTRL[2:0]};
                    state <= S_RD_CTRL;
                end
            end

            // ---- Output mixed sample ----
            S_OUTPUT: begin
                fsm_wr_active <= 0;
                sample_wr <= 1;
                sample_data <= {clamp_r, clamp_l};
                active_cnt <= voice_cnt;
                state <= S_IDLE;
            end

            default: state <= S_IDLE;
            endcase
        end
    end
end

endmodule
