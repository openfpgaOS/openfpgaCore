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
// Vertex format: x signed Q12.4, y integer or Q12.4 (subpix_y).
// Q12.4 triangles sample pixel centers; legacy integer-Y commands sample
// corners. Both use left-closed right-open coverage, clipped to
// [clip_x0, clip_x1) x [clip_y0, clip_y1).
//
// Datapath: y-sort, winding cross product (pipelined DSP shared with
// the clip presteps), per-edge Q16.16 slope dividers (28/32-beat serial
// restoring — see EW_PARALLEL_DIVS below for the one-vs-three layout),
// two-half DDA with a registered emit cone and a valid/ready record
// handshake.
//
// Setup is ~52 cycles per triangle with the parallel dividers (~110 with
// the single shared one), 3 cycles per scanline — always ahead of the span
// producer's per-record DSP plane evaluation and pixel fill.

module gpu_edge_walker #(
    // ----------------------------------------------------------------
    // Per-target area/speed knob for the slope-divide stage.
    //   1 (default) — three PARALLEL 28-beat restoring dividers, one per
    //       edge, resolving concurrently (~52 cy of setup per triangle:
    //       operand loads and slope fixups sequence one edge per cycle
    //       through shared subtract/abs and negate/scale cones).
    //   0 — the original SINGLE shared divider iterated over the three
    //       edges by edge_sel (~110 cy of setup per triangle).  Both
    //       implementations run the same restoring algorithm at the same
    //       widths, so the quotients — and therefore every emitted span
    //       record — are bit-identical; only setup latency differs, and
    //       the rec_valid/rec_ready handshake absorbs that.
    // The selection is a constant-folded prune gate (same style as
    // gpu_core's GPU_HAS_VERT_TRI / GPU_Z_READ_WINDOW gates): the
    // unselected implementation's registers lose every writer and reader,
    // so Quartus sweeps them — config 0 sheds the two extra dividers'
    // registers and subtract/compare chains.
    parameter EW_PARALLEL_DIVS = 1
) (
    input  wire        clk,
    input  wire        reset_n,

    // Pulse: drop any in-flight walk and return to idle.  Wired to the
    // GPU's soft_reset so a mid-command abort cannot leave the walker
    // wedged in S_WAIT holding a record nobody will consume.
    input  wire        abort,

    // Command load: pulse start with vertices + clip rect held stable.
    input  wire        start,
    // subpix_y = 0 (legacy): v*_y are INTEGER scanlines, byte-exact behaviour.
    // subpix_y = 1: v*_y are Q12.4 subpixel (same as x), so triangle vertices
    // are not snapped to whole scanlines. Integer-Y commands keep their
    // original corner-sampled coverage; 3D callers explicitly opt in.
    input  wire        subpix_y,
    input  wire signed [15:0] v0_x,   // Q12.4
    input  wire signed [15:0] v0_y,   // integer scanline (subpix_y=0) / Q12.4 (subpix_y=1)
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
localparam S_DIV_INIT    = 5'd10;  // divider operand setup (see EW_PARALLEL_DIVS)
localparam S_DIV_RUN     = 5'd11;  // serial restoring divide beats
localparam S_DIV_DONE    = 5'd12;  // sign fixup, store slope(s)
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
localparam S_PRESTEP_MIDW = 5'd25; // subpix: DSP latency for slope_bot*dy_clip_mid
localparam S_PRESTEP_MIDC = 5'd26; // subpix: capture bot_mid_off (>>4 -> Q16.16)

reg [4:0] state;

// ----------------------------------------------------------------
// Sorted vertices (y-ascending after S_SORT_C)
// ----------------------------------------------------------------
reg signed [15:0] x0, x1, x2;
reg signed [15:0] y0, y1, y2;

// ----------------------------------------------------------------
// Shared multiplier (winding cross product + clip presteps).
// Endpoint differences need 17 signed bits, including dy=65535. Slopes
// need 33 signed bits for (abs(dxQ4)<<16)/dyQ4. Do not saturate the
// prestep slope: even a very short edge can straddle a pixel center, and
// its fractional prestep must still land at the correct intersection.
// mul_p_pipe keeps the DSP output register and the existing launch/capture
// latency. Winding compares the complete signed 33-bit cross products.
// ----------------------------------------------------------------
reg  signed [32:0] mul_a;
reg  signed [16:0] mul_b;
(* multstyle = "dsp" *) reg  signed [49:0] mul_p_pipe;
reg  signed [49:0] mul_p_r;
reg  signed [49:0] xprod0_r;
wire winding_positive = $signed(xprod0_r[32:0]) > $signed(mul_p_r[32:0]);

// ----------------------------------------------------------------
// Slope dividers retain all 16 fractional bits. Integer Y uses dxQ4<<12
// divided by dy in 28 beats; subpixel Y uses dxQ4<<16 divided by dyQ4
// in 32 beats. Both start with dxQ4<<16 in a 32-bit dividend/quotient
// shift register. The 28-beat path consumes the high 28 dividend bits;
// its four unconsumed low bits are zero, preserving the legacy quotient.
// The subpixel path consumes all 32 bits, avoiding the previous loss of
// four quotient bits before edge accumulation.
// A signed-16 endpoint pair can span 65535 units. Preserve all 16 divisor
// bits and the 17-bit trial remainder; truncating dy to 13 bits wrapped
// edges at 512 pixels in Q4-Y mode, before scissor clipping could help.
//
// EW_PARALLEL_DIVS == 1: three dividers, one per edge (0 = long (02),
// 1 = top (01), 2 = bot (12)), operands loaded in S_DIV_INIT, three
// instances iterating on the shared beat counter.  A degenerate edge
// (dy == 0) sets its skip flag and the slope is forced to 0 at
// S_DIV_DONE — same result the shared divider's early-out produced.
//
// EW_PARALLEL_DIVS == 0: the single shared divider, sequenced over the
// edges by edge_sel (S_DIV_DONE loops back to S_DIV_INIT). Operands load
// directly in S_DIV_INIT; the first S_DIV_RUN beat checks for dy=0 and
// preserves the original setup latency without duplicate operand registers.
//
// Both register sets are declared unconditionally; the prune gates in
// the FSM mean only the selected set ever has a writer or a reader, so
// the other is swept at synthesis.
// ----------------------------------------------------------------
// Parallel-divider set (EW_PARALLEL_DIVS == 1)
reg  [31:0] div_dq0,       div_dq1,       div_dq2;  // fused dividend/quotient
reg  [15:0] div_divisor0,  div_divisor1,  div_divisor2;
reg  [16:0] div_rem0,      div_rem1,      div_rem2;
reg  [5:0]  div_cnt;
reg         div_neg0,      div_neg1,      div_neg2;
reg         div_skip0,     div_skip1,     div_skip2;

wire [16:0] div_try0  = {div_rem0[15:0], div_dq0[31]};
wire [17:0] div_sub0  = {1'b0, div_try0} - {2'b0, div_divisor0};
wire        div_ge0   = !div_sub0[17];
wire [16:0] div_next0 = div_ge0 ? div_sub0[16:0] : div_try0;

wire [16:0] div_try1  = {div_rem1[15:0], div_dq1[31]};
wire [17:0] div_sub1  = {1'b0, div_try1} - {2'b0, div_divisor1};
wire        div_ge1   = !div_sub1[17];
wire [16:0] div_next1 = div_ge1 ? div_sub1[16:0] : div_try1;

wire [16:0] div_try2  = {div_rem2[15:0], div_dq2[31]};
wire [17:0] div_sub2  = {1'b0, div_try2} - {2'b0, div_divisor2};
wire        div_ge2   = !div_sub2[17];
wire [16:0] div_next2 = div_ge2 ? div_sub2[16:0] : div_try2;

// Edge slot counter — used by BOTH configs: the shared-divider config
// iterates whole divides over it; the parallel config sequences its
// S_DIV_INIT operand loads and S_DIV_DONE slope fixups over it (one
// edge per cycle through one shared cone each, instead of three
// parallel cones committing in a single cycle).
reg  [1:0]  edge_sel;            // 0 = long (02), 1 = top (01), 2 = bot (12)

// Winding and divider setup are mutually exclusive. Share one subtractor
// pair across both, selecting endpoints before subtraction. The winding
// products need (dx01,dy02), then (dx02,dy01); divisions select 02/01/12.
wire eop_winding = (state == S_XPROD_A) || (state == S_XPROD_B);
wire eop_x_mid = eop_winding ? (state == S_XPROD_A) : (edge_sel == 2'd1);
wire eop_y_mid = eop_winding ? (state == S_XPROD_B) : (edge_sel == 2'd1);
wire eop_from_mid = !eop_winding && (edge_sel == 2'd2);
wire signed [15:0] eop_xa = eop_x_mid ? x1 : x2;
wire signed [15:0] eop_ya = eop_y_mid ? y1 : y2;
wire signed [15:0] eop_xb = eop_from_mid ? x1 : x0;
wire signed [15:0] eop_yb = eop_from_mid ? y1 : y0;
wire signed [16:0] eop_dx = {eop_xa[15], eop_xa} - {eop_xb[15], eop_xb};
wire signed [16:0] eop_dy = {eop_ya[15], eop_ya} - {eop_yb[15], eop_yb};
wire        [15:0] eop_abs_dx = eop_dx[16] ? -eop_dx[15:0] : eop_dx[15:0];
// Sorting guarantees ya >= yb for all three edges. The complete unsigned
// difference fits 16 bits; a conditional dy negate would only duplicate
// logic for a sign that can never occur after S_SORT_C.
wire        [15:0] eop_abs_dy = eop_dy[15:0];
wire               eop_neg    = eop_dx[16];
wire               eop_skip   = (eop_dy == 17'sd0);

// S_DIV_DONE slope fixup, sequenced over edge_sel (parallel config):
// ONE shared conditional-negate + scale cone.  Slot i writes the
// identical value the old single-cycle commit produced, i cycles
// later; the first slope consumer is S_PRESTEP_LL, entered only after
// slot 2.
wire        [31:0] fix_dq   = (edge_sel == 2'd0) ? div_dq0
                            : (edge_sel == 2'd1) ? div_dq1   : div_dq2;
wire               fix_skip = (edge_sel == 2'd0) ? div_skip0
                            : (edge_sel == 2'd1) ? div_skip1 : div_skip2;
wire               fix_neg  = (edge_sel == 2'd0) ? div_neg0
                            : (edge_sel == 2'd1) ? div_neg1  : div_neg2;
wire        [32:0] fix_scaled = {1'b0, fix_dq};
wire        [32:0] fix_slope  = fix_skip ? 33'd0
                              : fix_neg  ? -fix_scaled : fix_scaled;

// Shared-divider set (EW_PARALLEL_DIVS == 0)
reg  [31:0] div_dq;              // fused dividend/quotient
reg  [15:0] div_divisor;
reg  [16:0] div_rem;
reg         div_neg;

wire [16:0] div_try  = {div_rem[15:0], div_dq[31]};
// The subtraction's borrow supplies the comparison too. The extra bit
// preserves unsigned 17-bit trial values above 65535.
wire [17:0] div_sub  = {1'b0, div_try} - {2'b0, div_divisor};
wire        div_ge   = !div_sub[17];
wire [16:0] div_next = div_ge ? div_sub[16:0] : div_try;

// Slopes have 16 fractional bits and a sign bit above the full quotient.
reg signed [32:0] slope_long;
reg signed [32:0] slope_top;
reg signed [32:0] slope_bot;
reg               long_left;     // long edge is the left edge

// ----------------------------------------------------------------
// Walk state
// ----------------------------------------------------------------
reg signed [31:0] xl, xr;        // Q16.16 DDA accumulators
reg signed [31:0] bot_mid_off;   // subpix: prestep of the bottom edge from v1 to
                                 // scanline y_mid (Q16.16); 0 for integer Y / no swap
reg signed [15:0] y_cur;
reg signed [15:0] y_start, y_mid, y_end;
reg               in_bottom_half;

// Sign-extended Q16.16 vertex positions for DDA loads.
wire signed [31:0] x0_q16 = {{4{x0[15]}}, x0, 12'd0};
wire signed [31:0] x1_q16 = {{4{x1[15]}}, x1, 12'd0};

// Subpixel triangles sample pixel centers: ceil(edge_x - 0.5). Legacy
// integer-Y commands retain their original corner-sampled coverage.
// Only the carry out of the fractional half is used. Express it directly
// so each pixel-boundary conversion needs a 16-bit increment, not an
// otherwise discarded 32-bit bias addition. Exact ties remain unchanged.
wire ceil_inc_l = subpix_y ? (xl[15] && |xl[14:0]) : |xl[15:0];
wire ceil_inc_r = subpix_y ? (xr[15] && |xr[14:0]) : |xr[15:0];
wire signed [15:0] ceil_xl = xl[31:16] + {15'd0, ceil_inc_l};
wire signed [15:0] ceil_xr = xr[31:16] + {15'd0, ceil_inc_r};
wire signed [15:0] span_u0 = (ceil_xl < clip_x0) ? clip_x0 : ceil_xl;
wire signed [15:0] span_u1 = (ceil_xr > clip_x1) ? clip_x1 : ceil_xr;

// S_EMIT_A registers the ceil+clamp results so the width subtract and
// the record mux are not in the same cycle as the ceil increments.
reg signed [15:0] span_u0_r, span_u1_r;
wire signed [16:0] span_w = {span_u1_r[15], span_u1_r}
                          - {span_u0_r[15], span_u0_r};

// Integer scanline of each sorted vertex.  subpix_y=0: y* are already integer
// scanlines (identity).  subpix_y=1: y* are Q12.4, and the top-left fill rule
// covers centers in [y_top,y_bot): ceil(y - 0.5) = (yQ12.4+7)>>4.
// Extend before adding so vertices at the signed Q12.4 limits do not wrap.
wire signed [15:0] y0_scan = subpix_y ? (($signed({y0[15],y0}) + 17'sd7) >>> 4) : y0;
wire signed [15:0] y1_scan = subpix_y ? (($signed({y1[15],y1}) + 17'sd7) >>> 4) : y1;
wire signed [15:0] y2_scan = subpix_y ? (($signed({y2[15],y2}) + 17'sd7) >>> 4) : y2;

// Prestep distance from the vertex to the first walked scanline.
// subpix_y=0: integer line count (y_start - y_vertex), exactly as before.
// subpix_y=1: Q12.4 distance ((y_start<<4)+8 - y_vertexQ12.4); the prestep multiply
// product is then >>4 back to a Q16.16 x offset (see S_PRESTEP_LC capture).
wire signed [16:0] dy_clip_long = subpix_y
    ? (($signed({y_start[15], y_start}) <<< 4) + 17'sd8 - $signed({y0[15], y0}))
    : ($signed({y_start[15], y_start}) - $signed({y0[15], y0}));
wire signed [16:0] dy_clip_bot = subpix_y
    ? (($signed({y_start[15], y_start}) <<< 4) + 17'sd8 - $signed({y1[15], y1}))
    : ($signed({y_start[15], y_start}) - $signed({y1[15], y1}));
// subpix: Q12.4 distance from the bottom-edge start vertex v1 to scanline y_mid,
// used to prestep the bottom edge when it is re-seeded at the mid-vertex swap.
// Only consumed in the subpix && !in_bottom_half branch (y1 is Q12.4 there).
wire signed [16:0] dy_clip_mid = ($signed({y_mid[15], y_mid}) <<< 4)
                               + 17'sd8 - $signed({y1[15], y1});

// ----------------------------------------------------------------
// Shared DDA accumulator adders: xl and xr each get exactly ONE
// 32-bit adder, operands muxed by state (the S_PRESTEP_CM seed and
// the two S_STEP arms are mutually exclusive cycles).  xl and xr
// update in the same cycle, so they do NOT share a single adder.
// Values and commit cycles are identical to the per-arm adds this
// replaces — pure structural sharing; the operand mux now sits ahead
// of each adder (reg -> mux -> add -> reg).
// Dropping a slope's top sign bit here preserves the modulo-2^32 sum.
// Every visited edge intersection lies between its Q12.4 X endpoints,
// which fit Q16.16. Slopes requiring the extra bit span less than one
// scanline; only their full-width fractional prestep is consumed.
// ----------------------------------------------------------------
`ifdef INCLUDE_EW_STEP_PREDECODE
// EMIT_A and EMIT_B separate every y_cur update from S_STEP, including
// zero-width rows. Compute the next-row decisions during those existing
// cycles so the 16-bit compare does not precede the 32-bit DDA adders.
reg step_swap_r, step_end_r;
always @(posedge clk) begin
    step_swap_r <= !in_bottom_half && (y_cur + 16'sd1 >= y_mid);
    step_end_r <= (y_cur + 16'sd1 >= y_end);
end
wire step_swap = step_swap_r;
wire step_end = step_end_r;
`else
wire step_swap = !in_bottom_half && (y_cur + 16'sd1 >= y_mid);
`endif

// The three prestep launches share the multiplier's slope input mux.
wire signed [32:0] ew_prestep_slope_sel =
      (state == S_PRESTEP_LL) ? slope_long
    : (state == S_PRESTEP_LC) ? (in_bottom_half ? slope_bot : slope_top)
    :                           slope_bot;   // S_PRESTEP_SC mid-prestep

reg signed [31:0] xl_base, xl_off, xr_base, xr_off;
always @* begin
    if (state == S_PRESTEP_CM) begin
        if (long_left) begin
            xl_base = x0_q16;
            xl_off  = xprod0_r[31:0];
            xr_base = in_bottom_half ? x1_q16 : x0_q16;
            xr_off  = mul_p_r[31:0];
        end else begin
            xr_base = x0_q16;
            xr_off  = xprod0_r[31:0];
            xl_base = in_bottom_half ? x1_q16 : x0_q16;
            xl_off  = mul_p_r[31:0];
        end
    end else begin  // S_STEP is the only other state committing xl/xr
        if (long_left) begin
            xl_base = xl;
            xl_off  = slope_long[31:0];
            xr_base = step_swap ? x1_q16 : xr;
            xr_off  = step_swap ? bot_mid_off
                                : (in_bottom_half ? slope_bot[31:0] : slope_top[31:0]);
        end else begin
            xr_base = xr;
            xr_off  = slope_long[31:0];
            xl_base = step_swap ? x1_q16 : xl;
            xl_off  = step_swap ? bot_mid_off
                                : (in_bottom_half ? slope_bot[31:0] : slope_top[31:0]);
        end
    end
end
wire signed [31:0] xl_sum = xl_base + xl_off;
wire signed [31:0] xr_sum = xr_base + xr_off;

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
                mul_a <= eop_dx;
                mul_b <= eop_dy;
                state <= S_XPROD_W1;
            end
            S_XPROD_W1: state <= S_XPROD_B;
            S_XPROD_B: begin
                xprod0_r <= mul_p_pipe;
                mul_a <= eop_dx;
                mul_b <= eop_dy;
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
                long_left <= winding_positive;
                edge_sel  <= 2'd0;   // both configs sequence on edge_sel
                state     <= S_DIV_INIT;
                // Degenerate (all on one line) resolves via dy=0 slopes
                // and the y_start >= y_end check in S_PRESTEP_LL.
            end

            // ---------------- per-edge slope divide ----------------
            // PRUNE GATE: EW_PARALLEL_DIVS is an elaboration constant, so
            // each branch below survives in exactly one config and the
            // other config's divider registers are swept.
            S_DIV_INIT: if (EW_PARALLEL_DIVS != 0) begin
                // Load abs operands ONE EDGE PER CYCLE through the shared
                // eop_* subtract/abs cone (edge_sel: 0 = long, 1 = top,
                // 2 = bot).  dy==0 → degenerate edge; its skip flag forces
                // a 0 slope at S_DIV_DONE (same result the old
                // shared-divider early-out produced for that edge).  A
                // skipped slot still loads its divider registers — the
                // garbage quotient is never consumed (skip wins the fixup
                // mux), exactly as that slot's stale registers were never
                // consumed before.
                case (edge_sel)
                    2'd0: begin
                        div_skip0    <= eop_skip;
                        div_dq0      <= {eop_abs_dx, 16'd0};
                        div_divisor0 <= eop_abs_dy;
                        div_neg0     <= eop_neg;
                        div_rem0     <= 17'd0;
                    end
                    2'd1: begin
                        div_skip1    <= eop_skip;
                        div_dq1      <= {eop_abs_dx, 16'd0};
                        div_divisor1 <= eop_abs_dy;
                        div_neg1     <= eop_neg;
                        div_rem1     <= 17'd0;
                    end
                    default: begin
                        div_skip2    <= eop_skip;
                        div_dq2      <= {eop_abs_dx, 16'd0};
                        div_divisor2 <= eop_abs_dy;
                        div_neg2     <= eop_neg;
                        div_rem2     <= 17'd0;
                    end
                endcase
                if (edge_sel == 2'd2) begin
                    edge_sel <= 2'd0;    // re-arm for the S_DIV_DONE slots
                    div_cnt  <= subpix_y ? 6'd32 : 6'd28;   // 28..1 iterate
                    state    <= S_DIV_RUN;
                end else begin
                    edge_sel <= edge_sel + 2'd1;
                end
            end else begin
                // Load the same abs operands directly, as in the parallel
                // configuration. Sorted vertices remain stable throughout
                // setup. Keep the following preparation beat so divider and
                // record timing remain identical to the registered-delta form.
                state <= S_DIV_RUN;
                div_cnt <= subpix_y ? 6'd33 : 6'd29;
                div_dq  <= {eop_abs_dx, 16'd0};
                div_rem <= 17'd0;
                div_divisor <= eop_abs_dy;
                div_neg <= eop_neg;
            end

            S_DIV_RUN: if (EW_PARALLEL_DIVS != 0) begin
                div_rem0 <= div_next0;
                div_dq0  <= {div_dq0[30:0], div_ge0};
                div_rem1 <= div_next1;
                div_dq1  <= {div_dq1[30:0], div_ge1};
                div_rem2 <= div_next2;
                div_dq2  <= {div_dq2[30:0], div_ge2};
                if (div_cnt == 5'd1)
                    state <= S_DIV_DONE;
                else
                    div_cnt <= div_cnt - 5'd1;
            end else begin
                if (div_cnt == (subpix_y ? 6'd33 : 6'd29)) begin
                    // Original preparation beat. dy==0 forces a zero slope
                    // and skips division; other edges begin on the next beat.
                    if (div_divisor == 16'd0) begin
                        div_dq <= 32'd0;
                        state  <= S_DIV_DONE;
                    end else begin
                        div_cnt <= div_cnt - 5'd1;
                    end
                end else begin
                    div_rem <= div_next;
                    div_dq  <= {div_dq[30:0], div_ge};
                    if (div_cnt == 5'd1)
                        state <= S_DIV_DONE;
                    else
                        div_cnt <= div_cnt - 5'd1;
                end
            end

            S_DIV_DONE: if (EW_PARALLEL_DIVS != 0) begin
                // The completed quotient is Q16.16 for both Y formats.
                // Subpixel triangles use four extra divide beats to retain
                // the low slope bits that matter near shared pixel centers.
                // ONE EDGE PER CYCLE through the shared fix_* negate/scale
                // cone (edge_sel was re-armed to 0 on S_DIV_RUN entry);
                // slopes are first read in S_PRESTEP_LL, entered only
                // after slot 2 committed.
                case (edge_sel)
                    2'd0:    slope_long <= fix_slope;
                    2'd1:    slope_top  <= fix_slope;
                    default: slope_bot  <= fix_slope;
                endcase
                if (edge_sel == 2'd2) begin
                    // Clip the walk range before prestep (scanline bounds).
                    y_start <= (y0_scan < clip_y0) ? clip_y0 : y0_scan;
                    y_mid   <= y1_scan;
                    y_end   <= (y2_scan > clip_y1) ? clip_y1 : y2_scan;
                    state   <= S_PRESTEP_LL;
                end else begin
                    edge_sel <= edge_sel + 2'd1;
                end
            end else begin
                case (edge_sel)
                    2'd0: slope_long <= div_neg ? -{1'b0, div_dq}
                                                :  {1'b0, div_dq};
                    2'd1: slope_top  <= div_neg ? -{1'b0, div_dq}
                                                :  {1'b0, div_dq};
                    default: slope_bot <= div_neg ? -{1'b0, div_dq}
                                                  :  {1'b0, div_dq};
                endcase
                if (edge_sel == 2'd2) begin
                    // Clip the walk range before prestep (scanline bounds).
                    y_start <= (y0_scan < clip_y0) ? clip_y0 : y0_scan;
                    y_mid   <= y1_scan;
                    y_end   <= (y2_scan > clip_y1) ? clip_y1 : y2_scan;
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
                    mul_a <= ew_prestep_slope_sel;
                    mul_b <= dy_clip_long;
                    in_bottom_half <= (y_start >= y_mid);
                    state <= S_PRESTEP_W1;
                end
            end
            S_PRESTEP_W1: state <= S_PRESTEP_LC;
            S_PRESTEP_LC: begin
                // subpix_y=1: dy_clip was Q12.4, so the product is 16x the
                // Q16.16 x offset — shift back.  subpix_y=0: already Q16.16.
                xprod0_r <= subpix_y ? (mul_p_pipe >>> 4) : mul_p_pipe;  // long-edge prestep offset, Q16.16
                // Short edge: top half steps from v0 with slope_top;
                // bottom half steps from v1 with slope_bot.
                mul_a <= ew_prestep_slope_sel;
                mul_b <= in_bottom_half ? dy_clip_bot : dy_clip_long;
                state <= S_PRESTEP_W2;
            end
            S_PRESTEP_W2: state <= S_PRESTEP_SC;
            S_PRESTEP_SC: begin
                mul_p_r <= subpix_y ? (mul_p_pipe >>> 4) : mul_p_pipe;   // short-edge prestep offset
                // subpix only: prestep the bottom edge from v1 to scanline y_mid
                // (the mid-vertex swap re-seeds it there).  Integer Y / a walk
                // that already starts in the bottom half need no correction, so
                // bot_mid_off stays 0 and the swap re-seeds to exactly x1_q16.
                if (subpix_y && !in_bottom_half) begin
                    mul_a <= ew_prestep_slope_sel;
                    mul_b <= dy_clip_mid;
                    state <= S_PRESTEP_MIDW;
                end else begin
                    bot_mid_off <= 32'sd0;
                    state <= S_PRESTEP_CM;
                end
            end
            S_PRESTEP_MIDW: state <= S_PRESTEP_MIDC;
            S_PRESTEP_MIDC: begin
                bot_mid_off <= mul_p_pipe >>> 4;   // slope_bot*((y_mid<<4)+8 - y1) >> 4
                state <= S_PRESTEP_CM;
            end
            S_PRESTEP_CM: begin
                // Seed the DDA through the shared adders (operand muxes
                // select the prestep arm while state == S_PRESTEP_CM).
                xl    <= xl_sum;
                xr    <= xr_sum;
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
`ifdef INCLUDE_EW_STEP_PREDECODE
                if (step_end) begin
`else
                if (y_cur + 16'sd1 >= y_end) begin
`endif
                    state <= S_DONE;
                end else begin
                    y_cur <= y_cur + 16'sd1;
                    // Mid-vertex (step_swap): swap the short edge. Long
                    // edge DDA continues; the bottom edge starts AT v1 —
                    // its vertex sits on scanline y_mid (integer y), so
                    // the edge evaluates to exactly x1 there. The first
                    // slope_bot step lands on y_mid+1, matching the
                    // clip-prestep entry (x1 + sb*(y_start-y1)) and
                    // keeping a shared edge identical whether a neighbour
                    // walks it as long or bottom-short (crack-free
                    // adjacency).  bot_mid_off prelaces the bottom edge
                    // from v1 to scanline y_mid (subpix); it is 0 for
                    // integer Y so the swap re-seed is the exact original
                    // x1_q16 (byte-exact, crack-free).  Both the swap and
                    // plain-step arms commit through the shared xl/xr
                    // adders (operand muxes select on step_swap /
                    // long_left / in_bottom_half).
                    if (step_swap)
                        in_bottom_half <= 1'b1;
                    xl <= xl_sum;
                    xr <= xr_sum;
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
