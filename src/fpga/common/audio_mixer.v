//
// Hardware PCM Mixer — 32 voices from CRAM1
//
// Reads 16-bit signed mono samples from CRAM1 (via shared CDC adapter),
// resamples via 16.16 fixed-point, mixes with 4-bit volume scaling,
// writes packed stereo pairs to the audio FIFO.
//
// Voice descriptors stored in dual-port BRAM (1 M10K block).
// CPU writes via indirect register interface (select voice, write field).
// Mixer FSM reads sequentially during mix cycle.
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
    input wire [2:0]  voice_field,   // 0=addr, 1=len, 2=rate, 3=ctrl
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
    output wire [4:0]  active_count
);

wire mixer_active = reset_n & mixer_enable;

// ============================================
// Voice table in dual-port BRAM
// ============================================
// Layout per voice (4 words × 32 bits = 128 bits):
//   Word 0: base_addr[21:0] (CRAM1 word address)
//   Word 1: length[21:0] (sample length in 16-bit halfwords)
//   Word 2: rate[31:0] (16.16 fixed-point) (0.16 fixed-point playback rate)
//   Word 3: {vol[7:4], loop[1], active[0], pos_frac[15:0], pos_int[5:0]}
//            — packed to fit 32 bits
//
// Actually, let's use a simpler layout with more words:
//   Word 0: base_addr[21:0]
//   Word 1: length[21:0]
//   Word 2: rate[31:0] (16.16 fixed-point)
//   Word 3: ctrl: {24'b0, vol[7:4], 2'b0, loop[1], active[0]}
//   Word 4: pos_int[21:0]
//   Word 5: pos_frac[15:0]
// 6 words per voice, 32 voices = 192 words. Fits in one M10K (256 words min).
// Address = voice_index * 8 + field (use 8-word stride for power-of-2 addressing)

localparam VTBL_STRIDE = 8;  // words per voice slot (6 used, 2 padding)
localparam VTBL_ADDR   = 0;
localparam VTBL_LEN    = 1;
localparam VTBL_RATE   = 2;
localparam VTBL_CTRL   = 3;
localparam VTBL_POS_INT  = 4;
localparam VTBL_POS_FRAC = 5;

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
// CPU writes are forwarded from the periph slave.
// Map voice_field to BRAM address:
//   field 0 (addr) → offset 0, field 1 (len) → offset 1,
//   field 2 (rate) → offset 2, field 3 (ctrl) → offset 3
// When ctrl is written with active=1, also clear pos_int and pos_frac.

// Voice active flags — register, not BRAM (avoids write race on deactivation)
reg [31:0] voice_active;

// CPU write state — serviced by FSM
reg        cpu_wr_pending;
reg        cpu_clear_pos;
reg [7:0]  cpu_clear_base;  // voice base addr for pos clear

// Direct BRAM write from CPU (port A shared with FSM)
// CPU writes take priority — FSM only writes during S_WR_POS/S_WR_FRAC
reg        fsm_wr_active;  // FSM is using port A

// ============================================
// Mixer FSM
// ============================================
localparam S_IDLE      = 4'd0;
localparam S_CPU_WR    = 4'd1;   // Service pending CPU write
localparam S_CPU_CLR1  = 4'd2;   // Clear pos_int
localparam S_CPU_CLR2  = 4'd3;   // Clear pos_frac
localparam S_RD_CTRL   = 4'd4;   // Read voice ctrl from BRAM
localparam S_RD_CTRL_W = 4'd5;   // Wait for BRAM read
localparam S_RD_FIELDS = 4'd6;   // Read addr + pos_int
localparam S_RD_FIELDS_W= 4'd7;  // Wait + latch
localparam S_CRAM_REQ  = 4'd8;   // Issue CRAM1 read
localparam S_CRAM_WAIT = 4'd9;   // Wait for CRAM1 data
localparam S_ACCUM     = 4'd10;  // Accumulate sample
localparam S_WR_POS    = 4'd11;  // Write back new position
localparam S_WR_FRAC   = 4'd12;  // Write back new frac
localparam S_OUTPUT    = 4'd13;  // Write to FIFO

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
reg [3:0]  cur_vol;
reg        cur_loop;
reg        cur_active;
reg [21:0] cur_pos_int;
reg [15:0] cur_pos_frac;

// Position advance
// Position advance: {pos_int[21:0], pos_frac[15:0]} + rate[31:0]
// rate is 16.16 fixed-point: upper 16 = integer step, lower 16 = fractional step
wire [37:0] new_pos = {cur_pos_int, cur_pos_frac} + {6'd0, cur_rate};
wire [21:0] new_pos_int = new_pos[37:16];
wire [15:0] new_pos_frac = new_pos[15:0];

// Sample selection: pick hi or lo 16-bit half based on position LSB
wire signed [15:0] raw_sample = cur_pos_int[0]
                               ? $signed(cram1_rdata[31:16])
                               : $signed(cram1_rdata[15:0]);

// Volume: 0=silent, 15=full. Scale = raw >>> (15 - vol)
wire signed [15:0] scaled_sample = (cur_vol == 4'd0) ? 16'sd0
                                 : raw_sample >>> (4'd15 - cur_vol);

// Output clamp
wire [15:0] clamp_l = (accum_l > 32'sd32767)  ? 16'h7FFF :
                      (accum_l < -32'sd32768) ? 16'h8000 : accum_l[15:0];
wire [15:0] clamp_r = (accum_r > 32'sd32767)  ? 16'h7FFF :
                      (accum_r < -32'sd32768) ? 16'h8000 : accum_r[15:0];

// BRAM read pipeline: address set in cycle N, data available in cycle N+1
reg [2:0] bram_rd_phase;  // which field we're reading

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
        end else if (cpu_clear_pos && !fsm_wr_active) begin
            // Clear pos_int (first cycle)
            vtbl_a_wr <= 1;
            vtbl_a_addr <= {cpu_clear_base[7:3], VTBL_POS_INT[2:0]};
            vtbl_a_data <= 32'd0;
            cpu_clear_pos <= 0;
            cpu_wr_pending <= 1;  // signal to clear pos_frac next
        end else if (cpu_wr_pending && !fsm_wr_active) begin
            // Clear pos_frac (second cycle)
            vtbl_a_wr <= 1;
            vtbl_a_addr <= {cpu_clear_base[7:3], VTBL_POS_FRAC[2:0]};
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
                    // Start mix cycle
                    accum_l <= 0;
                    accum_r <= 0;
                    cur_voice <= 0;
                    voice_cnt <= 0;
                    // Start reading ctrl of voice 0
                    vtbl_b_addr <= {5'd0, VTBL_CTRL[2:0]};
                    state <= S_RD_CTRL;
                end
            end

            // ---- Mix cycle: read voice ctrl ----
            S_RD_CTRL: begin
                // BRAM addr was set last cycle, wait 1 for data
                state <= S_RD_CTRL_W;
            end

            S_RD_CTRL_W: begin
                // Latch ctrl
                cur_active <= vtbl_b_data[0];
                cur_loop   <= vtbl_b_data[1];
                cur_vol    <= vtbl_b_data[7:4];

                if (!voice_active[cur_voice]) begin
                    // Voice inactive — skip
                    if (cur_voice == 5'd31)
                        state <= S_OUTPUT;
                    else begin
                        cur_voice <= cur_voice + 1;
                        vtbl_b_addr <= {cur_voice + 5'd1, VTBL_CTRL[2:0]};
                        state <= S_RD_CTRL;
                    end
                end else begin
                    // Voice active — read addr field
                    vtbl_b_addr <= {cur_voice, VTBL_ADDR[2:0]};
                    bram_rd_phase <= 0;
                    state <= S_RD_FIELDS;
                end
            end

            // ---- Read remaining fields (addr, len, rate, pos_int, pos_frac) ----
            S_RD_FIELDS: begin
                state <= S_RD_FIELDS_W;
            end

            S_RD_FIELDS_W: begin
                case (bram_rd_phase)
                    3'd0: begin
                        cur_addr <= vtbl_b_data[21:0];
                        vtbl_b_addr <= {cur_voice, VTBL_LEN[2:0]};
                        bram_rd_phase <= 1;
                        state <= S_RD_FIELDS;
                    end
                    3'd1: begin
                        cur_len <= vtbl_b_data[21:0];
                        vtbl_b_addr <= {cur_voice, VTBL_RATE[2:0]};
                        bram_rd_phase <= 2;
                        state <= S_RD_FIELDS;
                    end
                    3'd2: begin
                        cur_rate <= vtbl_b_data;
                        vtbl_b_addr <= {cur_voice, VTBL_POS_INT[2:0]};
                        bram_rd_phase <= 3;
                        state <= S_RD_FIELDS;
                    end
                    3'd3: begin
                        cur_pos_int <= vtbl_b_data[21:0];
                        vtbl_b_addr <= {cur_voice, VTBL_POS_FRAC[2:0]};
                        bram_rd_phase <= 4;
                        state <= S_RD_FIELDS;
                    end
                    3'd4: begin
                        cur_pos_frac <= vtbl_b_data[15:0];
                        // Check if position (read in phase 3, now latched) is past end
                        if (cur_pos_int >= cur_len) begin  // cur_pos_int updated last cycle
                            // Deactivate voice — skip read/accumulate
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
                    // 16-bit samples packed 2 per 32-bit word
                    cram1_addr <= cur_addr + {1'b0, cur_pos_int[21:1]};
                    state <= S_CRAM_WAIT;
                end
            end

            S_CRAM_WAIT: begin
                if (cram1_rdata_valid) begin
                    state <= S_ACCUM;
                end
            end

            // ---- Accumulate ----
            S_ACCUM: begin
                accum_l <= accum_l + {{16{scaled_sample[15]}}, scaled_sample};
                accum_r <= accum_r + {{16{scaled_sample[15]}}, scaled_sample};
                voice_cnt <= voice_cnt + 1;
                state <= S_WR_POS;
            end

            // ---- Write back new position ----
            S_WR_POS: begin
                fsm_wr_active <= 1;
                vtbl_a_wr <= 1;
                if (new_pos_int >= cur_len) begin
                    if (cur_loop) begin
                        vtbl_a_addr <= {cur_voice, VTBL_POS_INT[2:0]};
                        vtbl_a_data <= 32'd0;
                    end else begin
                        // Deactivate voice
                        voice_active[cur_voice] <= 0;
                        vtbl_a_addr <= {cur_voice, VTBL_POS_INT[2:0]};
                        vtbl_a_data <= {10'd0, new_pos_int};
                    end
                end else begin
                    vtbl_a_addr <= {cur_voice, VTBL_POS_INT[2:0]};
                    vtbl_a_data <= {10'd0, new_pos_int};
                end
                state <= S_WR_FRAC;
            end

            S_WR_FRAC: begin
                if (new_pos_int >= cur_len && cur_loop) begin
                    vtbl_a_wr <= 1;
                    vtbl_a_addr <= {cur_voice, VTBL_POS_FRAC[2:0]};
                    vtbl_a_data <= 32'd0;
                end else if (new_pos_int < cur_len) begin
                    vtbl_a_wr <= 1;
                    vtbl_a_addr <= {cur_voice, VTBL_POS_FRAC[2:0]};
                    vtbl_a_data <= {16'd0, new_pos_frac};
                end
                // else: voice deactivated, no frac write needed

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
