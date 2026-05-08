//
// vrr_controller.v
//
// Variable Refresh Rate controller — fully RTL.
//
// Replaces the CPU's vrr_update() loop in firmware/os/targets/pocket/
// video.c.  Measures the render period T (cycles between successive
// frame swap kicks — gpu_swap_req from CMD_FLIP, or sysreg writes to
// FB_SWAP_CTRL bit 0) and looks up a (V_TOTAL, swap_hold) pair so the
// scaler stays inside the supported 42-60 Hz scanout band.
//
// Implementation note: avoids any multiplier (the previous Q24
// reciprocal version cost ~1.5 ns of slack on the clk_cpu path even
// when output-pipelined).  Instead the algorithm uses a piecewise-
// constant lookup: each main render-time band is split into 4 cycle
// thresholds chosen so the corresponding scanout V_TOTAL stays in
// [262, 375].  V_TOTAL granularity within a band is ~3 Hz, well
// inside vsync jitter and below human perception.
//
//   T (ms)     render fps   V_TOTAL  scanout    swap_hold   notes
//   < 16.6     > 60         262      60 Hz      0           CPU caps via wait_flip
//   16.6-18.4  54-60        285      ~55 Hz     0           N=1 adaptive
//   18.4-20.2  49-54        310      ~50 Hz     0
//   20.2-22.2  45-49        340      ~46 Hz     0
//   22.2-24    42-45        375      42 Hz      0
//   24-33      30-42        262      60 Hz      1           lock to 30 fps
//   33-36.7    27-30        285      ~55 Hz     1           N=2 (T/2 in band)
//   36.7-40.4  24-27        310      ~50 Hz     1
//   40.4-44.4  22-24        340      ~46 Hz     1
//   44.4-48    20-22        375      42 Hz      1
//   48-55      18-20        285      ~55 Hz     2           N=3 (T/3 in band)
//   55-60.6    16-18        310      ~50 Hz     2
//   60.6-66.6  15-16        340      ~46 Hz     2
//   66.6-72    13-15        375      42 Hz      2
//   > 72       < 13         375      42 Hz      2           floor
//
// Bypass: when analogizer_enabled is asserted, the controller leaves
// v_total_o at its previous value (slave mux picks a fixed NTSC/PAL value
// instead) and forces swap_hold_o = 0 so Analogizer/SNAC adapter output is not
// disrupted by adaptive refresh changes.
//
// Domain: clk_cpu — same as the rest of axi_periph_slave's swap
// logic.  gpu_swap_req is already in clk_cpu out of gpu_core; the
// sysreg path is in clk_cpu by construction.  No CDC inside.
//

`default_nettype none

module vrr_controller (
    input  wire        clk,
    input  wire        reset_n,

    // Frame-rate measurement triggers (single-cycle pulses).
    input  wire        gpu_swap_req,
    input  wire        sysreg_swap_kick,

    // Bypass for fixed-rate Analogizer/SNAC adapter output.
    input  wire        analogizer_enabled,

    // Computed scanout timing.
    output reg  [9:0]  v_total_o,
    output reg  [3:0]  swap_hold_o
);

// -------------------------------------------------------------------
// Cycle thresholds (100 MHz clock).
//
// Boundaries are chosen so that within each adaptive-N band the
// scanout period T/N falls in [16.6, 24]ms (= 60-42 Hz).  See header
// comment for the resulting (V_TOTAL, swap_hold) lookup.
// -------------------------------------------------------------------
localparam [25:0] C_16_6_MS = 26'd1_666_666;   //  60 Hz boundary (cap)
localparam [25:0] C_18_3_MS = 26'd1_833_333;   //  ~55 Hz
localparam [25:0] C_20_2_MS = 26'd2_022_222;   //  ~50 Hz
localparam [25:0] C_22_2_MS = 26'd2_222_222;   //  ~45 Hz
localparam [25:0] C_24_MS   = 26'd2_400_000;   //  42 Hz boundary

localparam [25:0] C_33_MS   = 26'd3_333_333;   //  T/2 → 60 Hz boundary
localparam [25:0] C_36_6_MS = 26'd3_666_666;   //  T/2 → ~55 Hz
localparam [25:0] C_40_4_MS = 26'd4_044_444;   //  T/2 → ~50 Hz
localparam [25:0] C_44_4_MS = 26'd4_444_444;   //  T/2 → ~45 Hz
localparam [25:0] C_48_MS   = 26'd4_800_000;   //  T/2 → 42 Hz boundary

localparam [25:0] C_55_MS   = 26'd5_500_000;   //  T/3 → ~55 Hz
localparam [25:0] C_60_6_MS = 26'd6_066_666;   //  T/3 → ~50 Hz
localparam [25:0] C_66_6_MS = 26'd6_666_666;   //  T/3 → ~45 Hz
localparam [25:0] C_72_MS   = 26'd7_200_000;   //  T/3 → 42 Hz boundary

// V_TOTAL line counts for the discrete scanout rates we use.
// (cycles_per_line at 100 MHz = 100_000_000 / 15720 ≈ 6361)
localparam [9:0] VT_60HZ = 10'd262;            //  16.6 ms / line × 262
localparam [9:0] VT_55HZ = 10'd285;            //  ~18.2 ms / 285 lines
localparam [9:0] VT_50HZ = 10'd310;            //  ~19.7 ms / 310 lines
localparam [9:0] VT_45HZ = 10'd340;            //  ~21.6 ms / 340 lines
localparam [9:0] VT_42HZ = 10'd375;            //  ~23.8 ms / 375 lines

// -------------------------------------------------------------------
// Render-period counter — saturating.
// -------------------------------------------------------------------
reg [25:0] cycles_since_swap;
wire       cycles_saturated = &cycles_since_swap;

wire swap_kick = gpu_swap_req | sysreg_swap_kick;

// -------------------------------------------------------------------
// Bucket lookup (combinational priority encoder).  Quartus reduces
// 26-bit constant comparators to ~3 LUT levels each, and the priority
// chain merges to ~3-4 levels total — well inside a 10 ns budget.
// -------------------------------------------------------------------
reg [9:0] vt_next;
reg [3:0] hold_next;

always @(*) begin
    if      (cycles_since_swap < C_16_6_MS) begin vt_next = VT_60HZ; hold_next = 4'd0; end
    else if (cycles_since_swap < C_18_3_MS) begin vt_next = VT_55HZ; hold_next = 4'd0; end
    else if (cycles_since_swap < C_20_2_MS) begin vt_next = VT_50HZ; hold_next = 4'd0; end
    else if (cycles_since_swap < C_22_2_MS) begin vt_next = VT_45HZ; hold_next = 4'd0; end
    else if (cycles_since_swap < C_24_MS  ) begin vt_next = VT_42HZ; hold_next = 4'd0; end
    else if (cycles_since_swap < C_33_MS  ) begin vt_next = VT_60HZ; hold_next = 4'd1; end
    else if (cycles_since_swap < C_36_6_MS) begin vt_next = VT_55HZ; hold_next = 4'd1; end
    else if (cycles_since_swap < C_40_4_MS) begin vt_next = VT_50HZ; hold_next = 4'd1; end
    else if (cycles_since_swap < C_44_4_MS) begin vt_next = VT_45HZ; hold_next = 4'd1; end
    else if (cycles_since_swap < C_48_MS  ) begin vt_next = VT_42HZ; hold_next = 4'd1; end
    else if (cycles_since_swap < C_55_MS  ) begin vt_next = VT_55HZ; hold_next = 4'd2; end
    else if (cycles_since_swap < C_60_6_MS) begin vt_next = VT_50HZ; hold_next = 4'd2; end
    else if (cycles_since_swap < C_66_6_MS) begin vt_next = VT_45HZ; hold_next = 4'd2; end
    else if (cycles_since_swap < C_72_MS  ) begin vt_next = VT_42HZ; hold_next = 4'd2; end
    else                                    begin vt_next = VT_42HZ; hold_next = 4'd2; end
end

// -------------------------------------------------------------------
// Sequential update.
// -------------------------------------------------------------------
always @(posedge clk) begin
    if (!reset_n) begin
        cycles_since_swap <= 26'd0;
        v_total_o         <= VT_60HZ;
        swap_hold_o       <= 4'd0;
    end
    else begin
        if (!cycles_saturated)
            cycles_since_swap <= cycles_since_swap + 26'd1;

        if (swap_kick) begin
            cycles_since_swap <= 26'd0;
            if (analogizer_enabled) begin
                // Slave mux picks a fixed V_TOTAL when the adapter is active;
                // we just freeze our output and force swap_hold=0.
                swap_hold_o <= 4'd0;
            end else begin
                v_total_o   <= vt_next;
                // hold>0 creates a positive feedback loop with the
                // kernel's of_video_acquire_next wait for
                // fb_swap_pending=0: every extra vsync the slave holds
                // adds ~16-24 ms to the CPU's perceived frame time,
                // which vrr_controller then measures as "even slower"
                // and bumps hold higher.  Once hold>=1 the loop is
                // self-sustaining; Duke3D got stuck at 12 fps after
                // 4585ba7 (post-revert of CMD_FLIP 3-phase) because
                // of this — the GPU was no longer the bottleneck but
                // hold stayed at 2.
                //
                // Force hold=0 always.  V_TOTAL still adapts to render
                // time (262/285/310/340/375 lines for 60/55/50/45/42 Hz)
                // so a slow CPU sees a matching scanout rate; the panel
                // just shows the same buffer for multiple vsyncs at the
                // slowest rate, which is what the panel does naturally
                // when fb_swap_pending=0.
                swap_hold_o <= 4'd0;
            end
        end
    end
end

endmodule

`default_nettype wire
