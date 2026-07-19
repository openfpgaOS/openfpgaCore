//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

//
// hps_mouse — MiSTer framework mouse → APF input-hub slot 3 (cont4_*).
//
// Consumes hps_io's PS/2-style mouse stream (same clk_sys = clk_cpu domain,
// so no CDC here; axi_periph_slave 2FF-syncs every cont bus internally) and
// encodes it per the unified mouse contract:
//
//   cont4_key [31:28] = 5 (OF_INPUT_TYPE_MOUSE) once a packet has been seen
//   cont4_key [15:0]  = report counter, +1 per packet
//   cont4_joy [31:16] = buttons {ext[11:8], M, R, L}
//   cont4_joy [15:0]  = X accumulator (signed, positive = right)
//   cont4_trig[15:0]  = Y accumulator (signed, positive = DOWN)
//
// X/Y are free-running wrapping accumulators (NOT per-report deltas like the
// Pocket dock): firmware computes delta = (int16_t)(now - prev), which is
// lossless under pure polling and self-heals a torn read — an accumulator
// sample is absolute, so a stale value only defers motion to the next poll.
// Do not saturate them; the differencing requires 16-bit wraparound.
//
// hps_io packet contract: ps2_mouse[24] toggles once per packet;
// [7:0] = PS/2 flags (bit0=L, bit1=R, bit2=M, bit4=X sign, bit5=Y sign,
// bit6=X overflow, bit7=Y overflow), [15:8] = dx low byte, [23:16] = dy low
// byte; ps2_mouse_ext[11:8] = extra buttons ([7:0] wheel is ignored — the
// of_mouse_state_t ABI is frozen).  PS/2 dy is positive-UP, so it is negated
// into the positive-DOWN slot convention.  On an overflow flag the magnitude
// byte has wrapped; the delta is clamped to ±255 toward the flagged sign.
//
`default_nettype none

module hps_mouse (
    input  wire        clk,
    input  wire        reset_n,

    // ── hps_io mouse stream ─────────────────────────────────────────
    input  wire [24:0] ps2_mouse,
    input  wire [15:0] ps2_mouse_ext,

    // ── APF cont4 (input-hub slot 3) ────────────────────────────────
    output wire [31:0] cont4_key,
    output wire [31:0] cont4_joy,
    output wire [15:0] cont4_trig
);

// 9-bit signed deltas: {sign flag, low byte}; overflow clamps to ±255.
wire [8:0] dx = ps2_mouse[6] ? {ps2_mouse[4], ps2_mouse[4] ? 8'h01 : 8'hFF}
                             : {ps2_mouse[4], ps2_mouse[15:8]};
wire [8:0] dy = ps2_mouse[7] ? {ps2_mouse[5], ps2_mouse[5] ? 8'h01 : 8'hFF}
                             : {ps2_mouse[5], ps2_mouse[23:16]};

reg        stb_prev;
reg        seen;
reg [15:0] report_cnt;
reg [15:0] buttons;
reg [15:0] acc_x;
reg [15:0] acc_y;

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        stb_prev   <= 1'b0;
        seen       <= 1'b0;
        report_cnt <= 16'd0;
        buttons    <= 16'd0;
        acc_x      <= 16'd0;
        acc_y      <= 16'd0;
    end else begin
        stb_prev <= ps2_mouse[24];
        if (ps2_mouse[24] != stb_prev) begin
            seen       <= 1'b1;
            report_cnt <= report_cnt + 16'd1;
            buttons    <= {9'd0, ps2_mouse_ext[11:8],
                           ps2_mouse[2], ps2_mouse[1], ps2_mouse[0]};
            acc_x      <= acc_x + {{7{dx[8]}}, dx};
            acc_y      <= acc_y - {{7{dy[8]}}, dy};
        end
    end
end

assign cont4_key  = {seen ? 4'h5 : 4'h0, 12'h000, report_cnt};
assign cont4_joy  = {buttons, acc_x};
assign cont4_trig = acc_y;

endmodule
