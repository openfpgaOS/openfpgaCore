//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

// gpu_edge_walker.v — triangle edge walker for CMD_DRAW_PARAM_TRI.
//
// Walks one triangle's edges and emits {u, v, count} span records in the
// same format gpu_core's param-span producer consumes (spanprod_u/v/count).
// The attribute planes, lighting, clamps and z behaviour all come from the
// param-span header words; this module only generates the per-scanline
// records the CPU used to compute.
//
// Vertex format: x signed Q12.4 subpixel, y signed integer scanline.
// Fill convention: ceil on both edges, left-closed right-open, clipped to
// [clip_x0, clip_x1) x [clip_y0, clip_y1).
//
// Datapath: y-sort, winding cross product (pipelined 27x16 DSP shared with
// the clip presteps), per-edge Q16.16 slope via a 28-cycle serial restoring
// divider, two-half DDA with a registered emit cone and a valid/ready
// record handshake.
//
// ~110 cycles of setup per triangle, 3 cycles per scanline — always ahead
// of the span producer's per-record DSP plane evaluation and pixel fill.

module gpu_edge_walker (
    input  wire        clk,
    input  wire        reset_n,

    // Pulse: drop any in-flight walk and return to idle.  Wired to the
    // GPU's soft_reset so a mid-command abort cannot leave the walker
    // wedged in S_WAIT holding a record nobody will consume.
    input  wire        abort,

    // Command load: pulse start with vertices + clip rect held stable.
    input  wire        start,
    input  wire signed [15:0] v0_x,   // Q12.4
    input  wire signed [15:0] v0_y,   // integer scanline
    input  wire signed [15:0] v1_x,
    input  wire signed [15:0] v1_y,
    input  wire signed [15:0] v2_x,
    input  wire signed [15:0] v2_y,
    input  wire signed [15:0] clip_x0,  // inclusive
    input  wire signed [15:0] clip_x1,  // exclusive
    input  wire signed [15:0] clip_y0,  // inclusive
    input  wire signed [15:0] clip_y1,  // exclusive

    output reg         busy,

    // Record stream: one span per surviving scanline.
    output reg         rec_valid,
    output reg  signed [15:0] rec_u,
    output reg  signed [15:0] rec_v,
    output reg  [15:0] rec_count,
    input  wire        rec_ready
);

// ----------------------------------------------------------------
// FSM
// ----------------------------------------------------------------
localparam S_IDLE        = 5'd0;
localparam S_SORT_A      = 5'd1;   // compare-swap (v0,v1)
localparam S_SORT_B      = 5'd2;   // compare-swap (v1,v2)
localparam S_SORT_C      = 5'd3;   // compare-swap (v0,v1)
localparam S_XPROD_A     = 5'd4;   // launch dx01*dy02
localparam S_XPROD_W1    = 5'd5;   // DSP pipeline latency
localparam S_XPROD_B     = 5'd6;   // register product 0, launch dx02*dy01
localparam S_XPROD_W2    = 5'd7;   // DSP pipeline latency
localparam S_XPROD_C     = 5'd8;   // register product 1
localparam S_XPROD_D     = 5'd9;   // winding sign from registered products
localparam S_DIV_INIT    = 5'd10;  // abs operands for current edge
localparam S_DIV_RUN     = 5'd11;  // serial restoring divide
localparam S_DIV_DONE    = 5'd12;  // sign fixup, store slope
localparam S_PRESTEP_LL  = 5'd13;  // launch slope_long * dy_clip
localparam S_PRESTEP_W1  = 5'd14;  // DSP pipeline latency
localparam S_PRESTEP_LC  = 5'd15;  // register long product, launch short
localparam S_PRESTEP_W2  = 5'd16;  // DSP pipeline latency
localparam S_PRESTEP_SC  = 5'd17;  // register short product
localparam S_PRESTEP_CM  = 5'd18;  // commit xl/xr from registered products
localparam S_WALK_INIT   = 5'd19;  // one stage before first emit cone
localparam S_EMIT_A      = 5'd20;  // register ceil+clamp edges
localparam S_EMIT_B      = 5'd21;  // width subtract/compare, raise valid
localparam S_WAIT        = 5'd22;  // hold for rec_ready
localparam S_STEP        = 5'd23;  // DDA advance, mid-vertex swap
localparam S_DONE        = 5'd24;

reg [4:0] state;

// ----------------------------------------------------------------
// Sorted vertices (y-ascending after S_SORT_C)
// ----------------------------------------------------------------
reg signed [15:0] x0, x1, x2;
reg signed [15:0] y0, y1, y2;

// ----------------------------------------------------------------
// Shared multiplier (winding cross product + clip presteps).
// 27x16 fits one Cyclone V DSP in native 27x27 mode; mul_p_pipe is the
// DSP output register, so the reg->DSP->reg path closes at clk_sys.
// Prestep slope operands saturate to Q10.16 (+/-1024 px/line): any edge
// steeper than that crosses the whole clip rect between two scanlines
// and its spans clip out regardless.
// ----------------------------------------------------------------
reg  signed [26:0] mul_a;
reg  signed [15:0] mul_b;
reg  signed [42:0] mul_p_pipe;
reg  signed [42:0] mul_p_r;
reg  signed [42:0] xprod0_r;

function signed [26:0] slope_sat27;
    input signed [31:0] s;
    begin
        if (s > 32'sh03FFFFFF)
            slope_sat27 = 27'sh3FFFFFF;
        else if (s < -32'sh04000000)
            slope_sat27 = -27'sh4000000;
        else
            slope_sat27 = s[26:0];
    end
endfunction

// ----------------------------------------------------------------
// Shared serial divider:
// slope Q16.16 = (|dx_q12.4| << 12) / |dy| — 28-bit dividend,
// 13-bit divisor, 28 quotient beats (beat 29 loads operands).
// ----------------------------------------------------------------
reg  [1:0]  edge_sel;            // 0 = long (02), 1 = top (01), 2 = bot (12)
reg  [27:0] div_quot;
reg  [27:0] div_dividend;
reg  [12:0] div_divisor;
reg  [13:0] div_rem;
reg  [4:0]  div_cnt;
reg         div_neg;

wire [13:0] div_try  = {div_rem[12:0], div_dividend[27]};
wire        div_ge   = div_try >= {1'b0, div_divisor};
wire [13:0] div_next = div_ge ? (div_try - {1'b0, div_divisor}) : div_try;

// Per-edge dx/dy selection for divider setup.
reg signed [16:0] edge_dx;       // Q12.4, one extra bit for the subtract
reg signed [16:0] edge_dy;

// Slopes in Q16.16.
reg signed [31:0] slope_long;
reg signed [31:0] slope_top;
reg signed [31:0] slope_bot;
reg               long_left;     // long edge is the left edge

// ----------------------------------------------------------------
// Walk state
// ----------------------------------------------------------------
reg signed [31:0] xl, xr;        // Q16.16 DDA accumulators
reg signed [15:0] y_cur;
reg signed [15:0] y_start, y_mid, y_end;
reg               in_bottom_half;

// Sign-extended Q16.16 vertex positions for DDA loads.
wire signed [31:0] x0_q16 = {{4{x0[15]}}, x0, 12'd0};
wire signed [31:0] x1_q16 = {{4{x1[15]}}, x1, 12'd0};

// ceil(Q16.16): top half of the +0xFFFF sum.
wire signed [31:0] ceil_sum_l = xl + 32'sh0000FFFF;
wire signed [31:0] ceil_sum_r = xr + 32'sh0000FFFF;
wire signed [15:0] ceil_xl = ceil_sum_l[31:16];
wire signed [15:0] ceil_xr = ceil_sum_r[31:16];
wire signed [15:0] span_u0 = (ceil_xl < clip_x0) ? clip_x0 : ceil_xl;
wire signed [15:0] span_u1 = (ceil_xr > clip_x1) ? clip_x1 : ceil_xr;

// S_EMIT_A registers the ceil+clamp results so the width subtract and
// the record mux are not in the same cycle as the 32-bit ceil adds.
reg signed [15:0] span_u0_r, span_u1_r;
wire signed [16:0] span_w = {span_u1_r[15], span_u1_r}
                          - {span_u0_r[15], span_u0_r};

wire signed [15:0] dy_clip_long = y_start - y0;
wire signed [15:0] dy_clip_bot  = y_start - y1;

always @(posedge clk) begin
    if (!reset_n || abort) begin
        state     <= S_IDLE;
        busy      <= 1'b0;
        rec_valid <= 1'b0;
    end else begin
        // Free-running DSP output register.
        mul_p_pipe <= mul_a * mul_b;

        case (state)
            S_IDLE: begin
                if (start) begin
                    x0 <= v0_x; y0 <= v0_y;
                    x1 <= v1_x; y1 <= v1_y;
                    x2 <= v2_x; y2 <= v2_y;
                    busy  <= 1'b1;
                    state <= S_SORT_A;
                end
            end

            // ---------------- y-sort: 3 compare-swaps ----------------
            S_SORT_A: begin
                if (y1 < y0) begin
                    x0 <= x1; y0 <= y1; x1 <= x0; y1 <= y0;
                end
                state <= S_SORT_B;
            end
            S_SORT_B: begin
                if (y2 < y1) begin
                    x1 <= x2; y1 <= y2; x2 <= x1; y2 <= y1;
                end
                state <= S_SORT_C;
            end
            S_SORT_C: begin
                if (y1 < y0) begin
                    x0 <= x1; y0 <= y1; x1 <= x0; y1 <= y0;
                end
                state <= S_XPROD_A;
            end

            // ---------------- winding cross product ----------------
            S_XPROD_A: begin
                mul_a <= {{11{x1[15]}}, x1} - {{11{x0[15]}}, x0};
                mul_b <= y2 - y0;
                state <= S_XPROD_W1;
            end
            S_XPROD_W1: state <= S_XPROD_B;
            S_XPROD_B: begin
                xprod0_r <= mul_p_pipe;
                mul_a <= {{11{x2[15]}}, x2} - {{11{x0[15]}}, x0};
                mul_b <= y1 - y0;
                state <= S_XPROD_W2;
            end
            S_XPROD_W2: state <= S_XPROD_C;
            S_XPROD_C: begin
                mul_p_r <= mul_p_pipe;
                state   <= S_XPROD_D;
            end
            S_XPROD_D: begin
                // z = cross(d01, d02) in y-down screen space; z > 0 →
                // mid vertex right of the long edge → long edge is left.
                long_left <= (xprod0_r - mul_p_r > 43'sd0);
                edge_sel  <= 2'd0;
                state     <= S_DIV_INIT;
                // Degenerate (all on one line) resolves via dy=0 slopes
                // and the y_start >= y_end check in S_PRESTEP_LL.
            end

            // ---------------- per-edge slope divide ----------------
            S_DIV_INIT: begin
                case (edge_sel)
                    2'd0: begin
                        edge_dx <= {x2[15], x2} - {x0[15], x0};
                        edge_dy <= {y2[15], y2} - {y0[15], y0};
                    end
                    2'd1: begin
                        edge_dx <= {x1[15], x1} - {x0[15], x0};
                        edge_dy <= {y1[15], y1} - {y0[15], y0};
                    end
                    default: begin
                        edge_dx <= {x2[15], x2} - {x1[15], x1};
                        edge_dy <= {y2[15], y2} - {y1[15], y1};
                    end
                endcase
                state <= S_DIV_RUN;
                div_cnt <= 5'd29;   // beat 29 loads operands, 28..1 iterate
                div_quot <= 28'd0;
                div_rem  <= 14'd0;
                div_dividend <= 28'd0;
                div_divisor  <= 13'd0;
                div_neg <= 1'b0;
            end

            S_DIV_RUN: begin
                if (div_cnt == 5'd29) begin
                    // First beat: load abs operands. dy==0 → degenerate
                    // edge; slope forced to 0 and divide skipped.
                    if (edge_dy == 17'sd0) begin
                        div_quot <= 28'd0;
                        state    <= S_DIV_DONE;
                    end else begin
                        div_dividend <= edge_dx[16]
                                      ? {-edge_dx[15:0], 12'd0}
                                      : { edge_dx[15:0], 12'd0};
                        div_divisor  <= edge_dy[16]
                                      ? -edge_dy[12:0]
                                      :  edge_dy[12:0];
                        div_neg <= edge_dx[16] ^ edge_dy[16];
                        div_rem <= 14'd0;
                        div_cnt <= div_cnt - 5'd1;
                    end
                end else begin
                    div_rem      <= div_next;
                    div_quot     <= {div_quot[26:0], div_ge};
                    div_dividend <= {div_dividend[26:0], 1'b0};
                    if (div_cnt == 5'd1)
                        state <= S_DIV_DONE;
                    else
                        div_cnt <= div_cnt - 5'd1;
                end
            end

            S_DIV_DONE: begin
                case (edge_sel)
                    2'd0: slope_long <= div_neg ? -{4'b0, div_quot}
                                                :  {4'b0, div_quot};
                    2'd1: slope_top  <= div_neg ? -{4'b0, div_quot}
                                                :  {4'b0, div_quot};
                    default: slope_bot <= div_neg ? -{4'b0, div_quot}
                                                  :  {4'b0, div_quot};
                endcase
                if (edge_sel == 2'd2) begin
                    // Clip the walk range before prestep.
                    y_start <= (y0 < clip_y0) ? clip_y0 : y0;
                    y_mid   <= y1;
                    y_end   <= (y2 > clip_y1) ? clip_y1 : y2;
                    state   <= S_PRESTEP_LL;
                end else begin
                    edge_sel <= edge_sel + 2'd1;
                    state    <= S_DIV_INIT;
                end
            end

            // ---------------- clip prestep ----------------
            S_PRESTEP_LL: begin
                if (y_start >= y_end) begin
                    // Fully clipped or degenerate.
                    busy  <= 1'b0;
                    state <= S_IDLE;
                end else begin
                    mul_a <= slope_sat27(slope_long);
                    mul_b <= dy_clip_long;
                    in_bottom_half <= (y_start >= y_mid);
                    state <= S_PRESTEP_W1;
                end
            end
            S_PRESTEP_W1: state <= S_PRESTEP_LC;
            S_PRESTEP_LC: begin
                xprod0_r <= mul_p_pipe;  // long-edge prestep offset, Q16.16
                // Short edge: top half steps from v0 with slope_top;
                // bottom half steps from v1 with slope_bot.
                mul_a <= slope_sat27(in_bottom_half ? slope_bot : slope_top);
                mul_b <= in_bottom_half ? dy_clip_bot : dy_clip_long;
                state <= S_PRESTEP_W2;
            end
            S_PRESTEP_W2: state <= S_PRESTEP_SC;
            S_PRESTEP_SC: begin
                mul_p_r <= mul_p_pipe;   // short-edge prestep offset
                state   <= S_PRESTEP_CM;
            end
            S_PRESTEP_CM: begin
                if (long_left) begin
                    xl <= x0_q16 + xprod0_r[31:0];
                    xr <= (in_bottom_half ? x1_q16 : x0_q16)
                        + mul_p_r[31:0];
                end else begin
                    xr <= x0_q16 + xprod0_r[31:0];
                    xl <= (in_bottom_half ? x1_q16 : x0_q16)
                        + mul_p_r[31:0];
                end
                y_cur <= y_start;
                state <= S_WALK_INIT;
            end

            S_WALK_INIT: begin
                // One registration stage between prestep adds and the
                // first emit's ceil/clip cone.
                state <= S_EMIT_A;
            end

            // ---------------- walk ----------------
            S_EMIT_A: begin
                // Stage 1: register ceil+clamp of both edges.
                span_u0_r <= span_u0;
                span_u1_r <= span_u1;
                state     <= S_EMIT_B;
            end
            S_EMIT_B: begin
                // Stage 2: width from registered edges, raise valid.
                if (span_w > 17'sd0) begin
                    rec_u     <= span_u0_r;
                    rec_v     <= y_cur;
                    rec_count <= span_w[15:0];
                    rec_valid <= 1'b1;
                    state     <= S_WAIT;
                end else begin
                    state <= S_STEP;
                end
            end

            S_WAIT: begin
                if (rec_ready) begin
                    rec_valid <= 1'b0;
                    state     <= S_STEP;
                end
            end

            S_STEP: begin
                if (y_cur + 16'sd1 >= y_end) begin
                    state <= S_DONE;
                end else begin
                    y_cur <= y_cur + 16'sd1;
                    if (!in_bottom_half && (y_cur + 16'sd1 >= y_mid)) begin
                        // Mid-vertex: swap the short edge. Long edge DDA
                        // continues; the bottom edge starts AT v1 — its
                        // vertex sits on scanline y_mid (integer y), so
                        // the edge evaluates to exactly x1 there. The
                        // first slope_bot step lands on y_mid+1, matching
                        // the clip-prestep entry (x1 + sb*(y_start-y1))
                        // and keeping a shared edge identical whether a
                        // neighbour walks it as long or bottom-short
                        // (crack-free adjacency).
                        in_bottom_half <= 1'b1;
                        if (long_left) begin
                            xl <= xl + slope_long;
                            xr <= x1_q16;
                        end else begin
                            xr <= xr + slope_long;
                            xl <= x1_q16;
                        end
                    end else begin
                        if (long_left) begin
                            xl <= xl + slope_long;
                            xr <= xr + (in_bottom_half ? slope_bot : slope_top);
                        end else begin
                            xr <= xr + slope_long;
                            xl <= xl + (in_bottom_half ? slope_bot : slope_top);
                        end
                    end
                    state <= S_EMIT_A;
                end
            end

            S_DONE: begin
                busy  <= 1'b0;
                state <= S_IDLE;
            end

            default: state <= S_IDLE;
        endcase
    end
end

endmodule
