//
// audio_mixer_cdc — Clock-domain crossing wrapper around audio_mixer.
//
// The audio mixer itself runs in clk_74a so it shares a domain with the
// CRAM1 controller (no CDC on the high-traffic tap fetch path) and with
// audio_output's clk_sys (no CDC on the sample-output path).  The only
// cross-domain work is the low-frequency CPU control interface: voice
// parameter writes, IRQ clear, mixer_enable, and status/IRQ readback.
//
// This wrapper:
//   - Exposes the original audio_mixer port layout on the clk_cpu side
//     so core_top.v can wire axi_periph_slave's mix_* signals unchanged
//   - Exposes a native clk_74a interface for CRAM1 data and the audio
//     sample output
//   - Handles all CDC with a mix of toggle handshakes (for writes /
//     pulses) and 2-FF synchronizers (for level status signals)
//
// Timing budget:
//   - Voice writes are infrequent (one per note-on event) — the toggle
//     handshake round trip is ~8 cycles clk_cpu + 8 cycles clk_74a.
//     Back-pressure via voice_wr_stall prevents overrun.
//   - Status signals are polled by the CPU on demand — 2-FF syncs have
//     acceptable staleness (tens of ns).
//

`default_nettype none

module audio_mixer_cdc (
    // ===== Clocks and reset =====
    input  wire        clk_cpu,
    input  wire        clk_74a,
    input  wire        reset_n,     // async assert, sync deassert handled internally

    // ===== CPU-side (clk_cpu): voice control =====
    input  wire        mixer_enable,
    input  wire        voice_wr,
    input  wire [3:0]  voice_field,
    input  wire [4:0]  voice_sel,
    input  wire [31:0] voice_wdata,
    output wire        voice_wr_stall,

    // ===== CPU-side (clk_cpu): IRQ =====
    input  wire [31:0] irq_clear,
    input  wire        irq_clear_wr,
    output wire [31:0] voice_end_pending,
    output wire        voice_end_irq,

    // ===== CPU-side (clk_cpu): status =====
    output wire [4:0]  active_count,
    output wire [21:0] pos_readback,

    // ===== clk_74a-side: CRAM1 data =====
    output wire        cram1_rd,
    output wire [21:0] cram1_addr,
    input  wire [31:0] cram1_rdata,
    input  wire        cram1_busy,
    input  wire        cram1_rdata_valid,

    // ===== clk_74a-side: audio output =====
    output wire        sample_wr,
    output wire [31:0] sample_data,
    input  wire [8:0]  fifo_level
);

// ============================================================
// Reset synchronization
// ============================================================
// Async assertion, sync deassertion into clk_74a so audio_mixer comes
// out of reset cleanly without a metastable FSM state.
reg [1:0] reset_n_74a_sync;
always @(posedge clk_74a or negedge reset_n) begin
    if (!reset_n) reset_n_74a_sync <= 2'b00;
    else          reset_n_74a_sync <= {reset_n_74a_sync[0], 1'b1};
end
wire reset_n_74a = reset_n_74a_sync[1];

// ============================================================
// mixer_enable: level sync clk_cpu -> clk_74a
// ============================================================
reg [1:0] mixer_enable_74a_sync;
always @(posedge clk_74a or negedge reset_n_74a) begin
    if (!reset_n_74a) mixer_enable_74a_sync <= 2'b00;
    else              mixer_enable_74a_sync <= {mixer_enable_74a_sync[0], mixer_enable};
end
wire mixer_enable_74a = mixer_enable_74a_sync[1];

// ============================================================
// Voice write CDC (clk_cpu -> clk_74a)
// ============================================================
// Toggle handshake: CPU latches {field, sel, wdata} and flips a toggle
// bit.  clk_74a sync detects the toggle edge and pulses voice_wr_74a.
// Back-pressure: vwr_cdc_busy is asserted until clk_74a acknowledges
// the toggle — combined with the mixer's own stall, it's fed back as
// voice_wr_stall so the CPU-side AXI slave holds bvalid until safe.

reg        vwr_req_toggle_cpu;
reg [3:0]  vwr_field_cpu_lat;
reg [4:0]  vwr_sel_cpu_lat;
reg [31:0] vwr_wdata_cpu_lat;

always @(posedge clk_cpu or negedge reset_n) begin
    if (!reset_n) begin
        vwr_req_toggle_cpu <= 1'b0;
        vwr_field_cpu_lat  <= 4'd0;
        vwr_sel_cpu_lat    <= 5'd0;
        vwr_wdata_cpu_lat  <= 32'd0;
    end else if (voice_wr) begin
        vwr_req_toggle_cpu <= ~vwr_req_toggle_cpu;
        vwr_field_cpu_lat  <= voice_field;
        vwr_sel_cpu_lat    <= voice_sel;
        vwr_wdata_cpu_lat  <= voice_wdata;
    end
end

// Sync toggle into clk_74a, detect edge
reg [2:0] vwr_req_toggle_74a_sync;
always @(posedge clk_74a or negedge reset_n_74a) begin
    if (!reset_n_74a) vwr_req_toggle_74a_sync <= 3'b000;
    else              vwr_req_toggle_74a_sync <= {vwr_req_toggle_74a_sync[1:0], vwr_req_toggle_cpu};
end
wire vwr_req_edge_74a = vwr_req_toggle_74a_sync[2] ^ vwr_req_toggle_74a_sync[1];

// Acknowledge by flipping an ack toggle when we consume the request
reg vwr_ack_toggle_74a;
always @(posedge clk_74a or negedge reset_n_74a) begin
    if (!reset_n_74a)       vwr_ack_toggle_74a <= 1'b0;
    else if (vwr_req_edge_74a) vwr_ack_toggle_74a <= vwr_req_toggle_74a_sync[2];
end

// Sync ack back to clk_cpu
reg [2:0] vwr_ack_toggle_cpu_sync;
always @(posedge clk_cpu or negedge reset_n) begin
    if (!reset_n) vwr_ack_toggle_cpu_sync <= 3'b000;
    else          vwr_ack_toggle_cpu_sync <= {vwr_ack_toggle_cpu_sync[1:0], vwr_ack_toggle_74a};
end

wire vwr_cdc_busy = (vwr_req_toggle_cpu != vwr_ack_toggle_cpu_sync[2]);

// Signals into audio_mixer (clk_74a domain).
//
// voice_sel is passed THROUGH from the clk_cpu input (not via the
// vwr_sel_cpu_lat latch) because it drives the per-voice position-
// readback mux inside audio_mixer.v.  The readback path is a pure
// combinational lookup keyed by voice_sel, so a CPU write to MIX_VOICE_SEL
// (which does NOT pulse voice_wr in axi_periph_slave.v) must still
// reach the mixer's selector immediately — otherwise the next
// MIX_VOICE_POS read returns whichever voice was last selected via a
// voice_wr write instead of the freshly-selected one.  audio_mixer.v
// already handles voice_sel as a slow/stable cross-domain signal; it
// only CDCs the voice_wr pulse itself.  Keeping the latched field and
// wdata is still fine for the write path — those only need to be
// stable when voice_wr_sync fires.
wire        mixer_voice_wr    = vwr_req_edge_74a;
wire [3:0]  mixer_voice_field = vwr_field_cpu_lat;  // stable by time of edge sync
wire [4:0]  mixer_voice_sel   = voice_sel;          // direct passthrough (see comment)
wire [31:0] mixer_voice_wdata = vwr_wdata_cpu_lat;

// ============================================================
// IRQ clear CDC (clk_cpu -> clk_74a)
// ============================================================
// Same pattern as voice writes: toggle + data latch.
reg        icw_req_toggle_cpu;
reg [31:0] icw_data_cpu_lat;

always @(posedge clk_cpu or negedge reset_n) begin
    if (!reset_n) begin
        icw_req_toggle_cpu <= 1'b0;
        icw_data_cpu_lat   <= 32'd0;
    end else if (irq_clear_wr) begin
        icw_req_toggle_cpu <= ~icw_req_toggle_cpu;
        icw_data_cpu_lat   <= irq_clear;
    end
end

reg [2:0] icw_req_toggle_74a_sync;
always @(posedge clk_74a or negedge reset_n_74a) begin
    if (!reset_n_74a) icw_req_toggle_74a_sync <= 3'b000;
    else              icw_req_toggle_74a_sync <= {icw_req_toggle_74a_sync[1:0], icw_req_toggle_cpu};
end
wire icw_req_edge_74a = icw_req_toggle_74a_sync[2] ^ icw_req_toggle_74a_sync[1];

wire        mixer_irq_clear_wr   = icw_req_edge_74a;
wire [31:0] mixer_irq_clear_data = icw_data_cpu_lat;

// ============================================================
// Status signals: clk_74a -> clk_cpu
// ============================================================
// active_count (5b), pos_readback (22b), voice_end_pending (32b):
// 2-FF sync per bit.  These are polled by CPU on demand; brief
// incoherent mid-update values resolve on the next poll.

wire        mixer_voice_wr_stall;
wire [4:0]  mixer_active_count;
wire [21:0] mixer_pos_readback;
wire [31:0] mixer_voice_end_pending;
wire        mixer_voice_end_irq;

reg [1:0]  stall_cpu_sync;
reg [9:0]  active_cpu_sync; // {sync2[4:0], sync1[4:0]}
reg [43:0] pos_cpu_sync;    // {sync2[21:0], sync1[21:0]}
reg [63:0] vep_cpu_sync;    // {sync2[31:0], sync1[31:0]}
reg [1:0]  vei_cpu_sync;

always @(posedge clk_cpu or negedge reset_n) begin
    if (!reset_n) begin
        stall_cpu_sync  <= 2'b00;
        active_cpu_sync <= 10'b0;
        pos_cpu_sync    <= 44'b0;
        vep_cpu_sync    <= 64'b0;
        vei_cpu_sync    <= 2'b00;
    end else begin
        stall_cpu_sync  <= {stall_cpu_sync[0], mixer_voice_wr_stall};
        active_cpu_sync <= {active_cpu_sync[4:0], mixer_active_count};
        pos_cpu_sync    <= {pos_cpu_sync[21:0], mixer_pos_readback};
        vep_cpu_sync    <= {vep_cpu_sync[31:0], mixer_voice_end_pending};
        vei_cpu_sync    <= {vei_cpu_sync[0], mixer_voice_end_irq};
    end
end

assign voice_wr_stall    = vwr_cdc_busy | stall_cpu_sync[1];
assign active_count      = active_cpu_sync[9:5];
assign pos_readback      = pos_cpu_sync[43:22];
assign voice_end_pending = vep_cpu_sync[63:32];
assign voice_end_irq     = vei_cpu_sync[1];

// ============================================================
// audio_mixer instance (clk_74a domain)
// ============================================================
audio_mixer mixer_inst (
    .clk                (clk_74a),
    .reset_n            (reset_n_74a),

    .mixer_enable       (mixer_enable_74a),

    .voice_wr           (mixer_voice_wr),
    .voice_field        (mixer_voice_field),
    .voice_sel          (mixer_voice_sel),
    .voice_wdata        (mixer_voice_wdata),
    .voice_wr_stall     (mixer_voice_wr_stall),

    .cram1_rd           (cram1_rd),
    .cram1_addr         (cram1_addr),
    .cram1_rdata        (cram1_rdata),
    .cram1_busy         (cram1_busy),
    .cram1_rdata_valid  (cram1_rdata_valid),

    .sample_wr          (sample_wr),
    .sample_data        (sample_data),
    .fifo_level         (fifo_level),

    .active_count       (mixer_active_count),
    .pos_readback       (mixer_pos_readback),

    .irq_clear          (mixer_irq_clear_data),
    .irq_clear_wr       (mixer_irq_clear_wr),
    .voice_end_pending  (mixer_voice_end_pending),
    .voice_end_irq      (mixer_voice_end_irq)
);

endmodule
