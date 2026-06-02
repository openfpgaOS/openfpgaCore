//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

//
// AXI4 SDRAM Arbiter — 3 Masters, Fixed Priority.
//
// Routes 3 AXI4 masters to 1 AXI4 slave (axi_sdram_slave):
//   M0 = GPU      (span reads + framebuffer writes, merged upstream)
//   M1 = CPU      (i + d + p merged through cpu_target_port)
//   M2 = APF bridge file DMA (write-only)
//   M3 = AudioMix (audio_mixer per-voice sample fetch, read-only)
//
// Priority: CPU-starvation guard, then AudioMix, then GPU > CPU > Bridge.
// (AudioMix was historically lowest; it is now promoted to just under the CPU
//  guard because it is latency-critical and tiny — see the ST_IDLE comment.)
//
// M0 writes are posted into an ordered local queue before they reach the
// shared SDRAM slave.  B responses still wait for the corresponding queued
// write to commit, so GPU fences/flips keep their "pixels are in SDRAM"
// meaning.  The queue lets texture reads continue while framebuffer writes
// are pending; a bounded read budget and high-water mark keep writes from
// starving.
//
// CPU fairness counter: any GPU/audio grant while the CPU has a pending
// request increments a deficit; once it reaches CPU_FAIR_THRESHOLD the CPU
// is granted unconditionally.  Audio now sits ABOVE GPU/CPU but still under
// this guard, and an audio grant while the CPU is pending DOES bump the
// deficit, so the guard can preempt a sustained audio burst (no starvation).
//
// Single outstanding transaction at the SDRAM slave -- grants one master at a
// time, holds until read completes (R.rlast) or write completes (B.bvalid).
// M0 AW/W handshakes are decoupled from that grant by the posted queue.
//
`default_nettype none

module axi_sdram_arbiter #(
    parameter [3:0] CPU_FAIR_THRESHOLD   = 4'd8, // Force CPU grant after this many non-CPU grants
    parameter [3:0] AUDIO_FAIR_THRESHOLD = 4'd4, // Force AudioMix grant after this many non-audio grants
    parameter [3:0] GPU_WRITE_READ_BUDGET = 4'd4 // Max GPU reads while posted writes wait
) (
    input wire clk,
    input wire reset_n,

    // Master 0: GPU (merged read + write)
    input  wire        m0_arvalid,
    output wire        m0_arready,
    input  wire [31:0] m0_araddr,
    input  wire [7:0]  m0_arlen,
    output wire        m0_rvalid,
    output wire [31:0] m0_rdata,
    output wire [1:0]  m0_rresp,
    output wire        m0_rlast,
    input  wire        m0_awvalid,
    output wire        m0_awready,
    input  wire [31:0] m0_awaddr,
    input  wire [7:0]  m0_awlen,
    input  wire        m0_wvalid,
    output wire        m0_wready,
    input  wire [31:0] m0_wdata,
    input  wire [3:0]  m0_wstrb,
    input  wire        m0_wlast,
    output wire        m0_bvalid,
    output wire [1:0]  m0_bresp,

    // Master 1: CPU
    input  wire        m1_arvalid,
    output wire        m1_arready,
    input  wire [31:0] m1_araddr,
    input  wire [7:0]  m1_arlen,
    output wire        m1_rvalid,
    output wire [31:0] m1_rdata,
    output wire [1:0]  m1_rresp,
    output wire        m1_rlast,
    input  wire        m1_rready,
    input  wire        m1_awvalid,
    output wire        m1_awready,
    input  wire [31:0] m1_awaddr,
    input  wire [7:0]  m1_awlen,
    input  wire        m1_wvalid,
    output wire        m1_wready,
    input  wire [31:0] m1_wdata,
    input  wire [3:0]  m1_wstrb,
    input  wire        m1_wlast,
    output wire        m1_bvalid,
    output wire [1:0]  m1_bresp,

    // Master 2: APF bridge file DMA (write-only)
    input  wire        m2_awvalid,
    output wire        m2_awready,
    input  wire [31:0] m2_awaddr,
    input  wire [7:0]  m2_awlen,
    input  wire        m2_wvalid,
    output wire        m2_wready,
    input  wire [31:0] m2_wdata,
    input  wire [3:0]  m2_wstrb,
    input  wire        m2_wlast,
    output wire        m2_bvalid,
    output wire [1:0]  m2_bresp,

    // Master 3: Audio Mixer (read-only, lowest priority)
    input  wire        m3_arvalid,
    output wire        m3_arready,
    input  wire [31:0] m3_araddr,
    input  wire [7:0]  m3_arlen,
    output wire        m3_rvalid,
    output wire [31:0] m3_rdata,
    output wire [1:0]  m3_rresp,
    output wire        m3_rlast,
    input  wire        m3_rready,

    // Slave port (to axi_sdram_slave)
    output wire        s_arvalid,
    input  wire        s_arready,
    output wire [31:0] s_araddr,
    output wire [7:0]  s_arlen,
    input  wire        s_rvalid,
    output wire        s_rready,
    input  wire [31:0] s_rdata,
    input  wire [1:0]  s_rresp,
    input  wire        s_rlast,
    output wire        s_awvalid,
    input  wire        s_awready,
    output wire [31:0] s_awaddr,
    output wire [7:0]  s_awlen,
    output wire        s_wvalid,
    input  wire        s_wready,
    output wire [31:0] s_wdata,
    output wire [3:0]  s_wstrb,
    output wire        s_wlast,
    input  wire        s_bvalid,
    input  wire [1:0]  s_bresp,
    // Asserted while the active write's W data is supplied continuously by the
    // arbiter (the GPU posted-write queue) and so cannot underrun.  The slave
    // uses this to allow native streaming bursts only for the GPU; CPU/bridge
    // writes (which can stall WVALID) are kept on the serialized path.
    output wire        s_wcont,

    // Diagnostic observability for simulation harnesses.
    //   dbg_arb_state: 0=IDLE, 1=RD, 2=WR
    //   dbg_cpu_pending: m1_arvalid|m1_awvalid (CPU has a request out)
    //   dbg_grant: 0=GPU(M0), 1=CPU(M1), 2=Bridge(M2), 3=AudioMix(M3)
    output wire [1:0]  dbg_arb_state,
    output wire        dbg_cpu_pending,
    output wire [1:0]  dbg_grant
);

wire reset = ~reset_n;

// Arbiter states
localparam ST_IDLE = 2'd0;
localparam ST_RD   = 2'd1;  // Read transaction active (AR→R)
localparam ST_WR   = 2'd2;  // Write transaction active (AW→W→B)

reg [1:0] arb_state;
reg [1:0] grant;  // 0=GPU, 1=CPU, 3=AudioMix
reg       active_wr_gpuq;
reg [2:0] active_gpuq_awlen;   // beats-1 of the GPU write burst currently draining (0..7)
reg [2:0] gpuq_w_idx;          // beat index within that burst

// Fairness: deficit counter prevents GPU (and technically Audio, but
// it's below CPU anyway) from starving the CPU.  Increments each time
// a non-CPU master wins while the CPU has a pending request.  When it
// reaches the threshold, the CPU is granted unconditionally.  Resets
// when the CPU gets a grant or has no pending request.
reg [3:0] gpu_deficit;
wire cpu_pending = m1_arvalid | m1_awvalid;

// Audio fairness: bounds the worst-case wait the AudioMix master can
// experience.  Without this, GPU + CPU traffic can starve the audio
// FSM during heavy frame rendering, draining the output dcfifo (1024
// entries / ~21 ms) and producing framerate-correlated clicks.
reg [3:0] audio_deficit;
wire audio_pending = m3_arvalid;

// Posted M0 write queue.  Entries are single 32-bit word writes; AWLEN>0
// GPU bursts are split into consecutive queue entries and one B is returned
// when the entry carrying WLAST commits.
//
// Keep this deliberately compact.  The GPU emits up to 8-beat contiguous
// framebuffer write bursts; an 8-entry queue holds one full burst and gives
// useful read/write decoupling without spending the routing/ALM budget of a
// larger register FIFO in the already dense 100 MHz SDRAM domain.
localparam GPU_WQ_DEPTH = 8;
localparam GPU_WQ_HIGH_WATERMARK = 6;
localparam GPU_WQ_PTR_W = 3;

reg [31:0] gpu_wq_addr [0:GPU_WQ_DEPTH-1];
reg [31:0] gpu_wq_data [0:GPU_WQ_DEPTH-1];
reg [3:0]  gpu_wq_strb [0:GPU_WQ_DEPTH-1];
reg        gpu_wq_last [0:GPU_WQ_DEPTH-1];
reg [GPU_WQ_PTR_W-1:0] gpu_wq_rd_ptr;
reg [GPU_WQ_PTR_W-1:0] gpu_wq_wr_ptr;
reg [3:0]  gpu_wq_count;

reg        m0w_active;
reg [31:0] m0w_addr;
reg [7:0]  m0w_beats_left;
reg        m0_bvalid_q;
reg [3:0]  gpu_reads_since_write;

wire grant_m0 = (grant == 2'd0);
wire grant_m1 = (grant == 2'd1);
wire grant_m2 = (grant == 2'd2);
wire grant_m3 = (grant == 2'd3);
wire active_rd = (arb_state == ST_RD);
wire active_wr = (arb_state == ST_WR);

wire gpu_wq_empty = (gpu_wq_count == 5'd0);
wire gpu_wq_full  = (gpu_wq_count == 4'd8);
wire [4:0] gpu_wq_free = 5'd8 - {1'b0, gpu_wq_count};
wire [4:0] m0_aw_beats = {1'b0, m0_awlen[3:0]} + 5'd1;
wire       m0_aw_supported = (m0_awlen[7:3] == 5'd0);
wire       m0_aw_has_space = m0_aw_supported && (gpu_wq_free >= m0_aw_beats);
wire       m0_aw_post_fire = m0_awvalid && m0_awready;
wire       m0_w_post_fire  = m0_wvalid && m0_wready;
wire       m0_w_post_last  = (m0w_beats_left == 8'd0) || m0_wlast;
wire       gpu_wq_pop      = active_wr && active_wr_gpuq && s_bvalid;
// GPU write-burst run-length coalescing.
// A GPU AW burst is split into consecutive queue entries whose addresses are
// base, base+4, ... BY CONSTRUCTION (m0w_addr increments +4 per posted beat),
// and the final beat carries WLAST.  So one full AW = the run of entries from
// rd_ptr up to and INCLUDING the first WLAST entry.  We coalesce that whole run
// into a single native SDRAM burst (one AW + one B), instead of the previous
// fixed 2-beat merge.  No inter-entry address compare is needed (contiguity is
// guaranteed by construction); we only require each entry be full-strobe so the
// native burst path stays on its tested full-word path, and stop at the first
// WLAST so exactly one B is returned per GPU AW.
wire [GPU_WQ_PTR_W-1:0] gwq_p0 = gpu_wq_rd_ptr;
wire [GPU_WQ_PTR_W-1:0] gwq_p1 = gpu_wq_rd_ptr + 3'd1;
wire [GPU_WQ_PTR_W-1:0] gwq_p2 = gpu_wq_rd_ptr + 3'd2;
wire [GPU_WQ_PTR_W-1:0] gwq_p3 = gpu_wq_rd_ptr + 3'd3;
wire [GPU_WQ_PTR_W-1:0] gwq_p4 = gpu_wq_rd_ptr + 3'd4;
wire [GPU_WQ_PTR_W-1:0] gwq_p5 = gpu_wq_rd_ptr + 3'd5;
wire [GPU_WQ_PTR_W-1:0] gwq_p6 = gpu_wq_rd_ptr + 3'd6;
wire [GPU_WQ_PTR_W-1:0] gwq_p7 = gpu_wq_rd_ptr + 3'd7;
// chainK: entries 0..K are all present and full-strobe, with no WLAST strictly
// before K — i.e. entries 0..K-1 are non-last (so the run never crosses an AW
// boundary; it stops AT the first WLAST entry, including it).
wire gwq_fs0 = (gpu_wq_strb[gwq_p0] == 4'hF);
wire gwq_c1 = gwq_fs0 && (gpu_wq_count >= 4'd2) && !gpu_wq_last[gwq_p0] && (gpu_wq_strb[gwq_p1] == 4'hF);
wire gwq_c2 = gwq_c1  && (gpu_wq_count >= 4'd3) && !gpu_wq_last[gwq_p1] && (gpu_wq_strb[gwq_p2] == 4'hF);
wire gwq_c3 = gwq_c2  && (gpu_wq_count >= 4'd4) && !gpu_wq_last[gwq_p2] && (gpu_wq_strb[gwq_p3] == 4'hF);
wire gwq_c4 = gwq_c3  && (gpu_wq_count >= 4'd5) && !gpu_wq_last[gwq_p3] && (gpu_wq_strb[gwq_p4] == 4'hF);
wire gwq_c5 = gwq_c4  && (gpu_wq_count >= 4'd6) && !gpu_wq_last[gwq_p4] && (gpu_wq_strb[gwq_p5] == 4'hF);
wire gwq_c6 = gwq_c5  && (gpu_wq_count >= 4'd7) && !gpu_wq_last[gwq_p5] && (gpu_wq_strb[gwq_p6] == 4'hF);
wire gwq_c7 = gwq_c6  && (gpu_wq_count >= 4'd8) && !gpu_wq_last[gwq_p6] && (gpu_wq_strb[gwq_p7] == 4'hF);
// awlen = length-1 of the contiguous full-strobe run currently present from
// rd_ptr (capped at the first WLAST entry).  This coalesces opportunistically
// like the old 2-beat path — it does NOT wait for the AW's WLAST to be queued,
// so a partially-streamed AW still drains efficiently rather than as singles.
// B is gated separately on the drained group containing the WLAST entry, so a
// burst that stops mid-AW correctly returns no B until the final beat commits.
wire [2:0] gpu_wq_run =
      gwq_c7 ? 3'd7 :
      gwq_c6 ? 3'd6 :
      gwq_c5 ? 3'd5 :
      gwq_c4 ? 3'd4 :
      gwq_c3 ? 3'd3 :
      gwq_c2 ? 3'd2 :
      gwq_c1 ? 3'd1 : 3'd0;
wire       gpu_wq_head_ready = !gpu_wq_empty
                            && (gpu_wq_last[gpu_wq_rd_ptr] ||
                                (gpu_wq_count >= 4'd2));
// Combinational (not registered) so the burst length reflects the queue at the
// exact grant cycle — a registered value lags the count by one cycle and would
// drain singles on the cycle count first reaches 2.  The scan is only
// strb/last/count comparisons (no 32-bit address compare — contiguity is
// guaranteed by construction), and it feeds the active_gpuq_awlen flop, not the
// grant-priority mux, so it does not lengthen the AR-priority decision.
wire [2:0] gpu_wq_drain_awlen = gpu_wq_run;
wire [GPU_WQ_PTR_W-1:0] active_gpuq_w_ptr    = gpu_wq_rd_ptr + gpuq_w_idx;
wire [GPU_WQ_PTR_W-1:0] active_gpuq_last_ptr = gpu_wq_rd_ptr + active_gpuq_awlen;
wire       active_gpuq_wlast = (gpuq_w_idx == active_gpuq_awlen);
wire [3:0] gpu_wq_pop_count = gpu_wq_pop ? ({1'b0, active_gpuq_awlen} + 4'd1) : 4'd0;
wire       gpu_wq_high     = (gpu_wq_count >= GPU_WQ_HIGH_WATERMARK[3:0]);
wire [3:0] gpu_wq_read_budget_limit = gpu_wq_high ? 4'd1 : GPU_WRITE_READ_BUDGET;
wire       gpu_wq_read_budget_spent = (gpu_reads_since_write >= gpu_wq_read_budget_limit);
wire       gpu_wq_should_drain = gpu_wq_head_ready &&
                                 (!m0_arvalid || gpu_wq_read_budget_spent);

// Completion guards: on the cycle bvalid/rlast fires, the slave returns to
// S_IDLE while the arbiter is still registered in ST_WR/ST_RD.
wire rd_completing = active_rd && s_rvalid && s_rlast;
wire wr_completing = active_wr && s_bvalid;

// Grant arbitration, M0 posted-write receiver, and M0 B response generation.
always @(posedge clk or posedge reset) begin
    if (reset) begin
        arb_state <= ST_IDLE;
        grant <= 2'd0;
        active_wr_gpuq <= 1'b0;
        active_gpuq_awlen <= 3'd0;
        gpuq_w_idx <= 3'd0;
        gpu_deficit <= 4'd0;
        audio_deficit <= 4'd0;
        gpu_wq_rd_ptr <= {GPU_WQ_PTR_W{1'b0}};
        gpu_wq_wr_ptr <= {GPU_WQ_PTR_W{1'b0}};
        gpu_wq_count <= 4'd0;
        m0w_active <= 1'b0;
        m0w_addr <= 32'd0;
        m0w_beats_left <= 8'd0;
        m0_bvalid_q <= 1'b0;
        gpu_reads_since_write <= 4'd0;
    end else begin
        m0_bvalid_q <= 1'b0;

        if (m0_aw_post_fire) begin
            m0w_active <= 1'b1;
            m0w_addr <= m0_awaddr;
            m0w_beats_left <= m0_awlen;
        end

        if (m0_w_post_fire) begin
            gpu_wq_addr[gpu_wq_wr_ptr] <= m0w_addr;
            gpu_wq_data[gpu_wq_wr_ptr] <= m0_wdata;
            gpu_wq_strb[gpu_wq_wr_ptr] <= m0_wstrb;
            gpu_wq_last[gpu_wq_wr_ptr] <= m0_w_post_last;
            gpu_wq_wr_ptr <= gpu_wq_wr_ptr + 1'b1;
            if (m0_w_post_last) begin
                m0w_active <= 1'b0;
            end else begin
                m0w_addr <= m0w_addr + 32'd4;
                m0w_beats_left <= m0w_beats_left - 8'd1;
            end
        end

        if (gpu_wq_pop) begin
            if (gpu_wq_last[active_gpuq_last_ptr])
                m0_bvalid_q <= 1'b1;
            gpu_wq_rd_ptr <= gpu_wq_rd_ptr + active_gpuq_awlen + 3'd1;
        end

        gpu_wq_count <= gpu_wq_count
                      + (m0_w_post_fire ? 4'd1 : 4'd0)
                      - gpu_wq_pop_count;

        if (active_wr && active_wr_gpuq && s_wvalid && s_wready && !active_gpuq_wlast)
            gpuq_w_idx <= gpuq_w_idx + 3'd1;

        if (gpu_wq_empty)
            gpu_reads_since_write <= 4'd0;

        case (arb_state)
        ST_IDLE: begin
            if (cpu_pending && gpu_deficit >= CPU_FAIR_THRESHOLD) begin
                grant <= 2'd1;
                active_wr_gpuq <= 1'b0;
                gpu_deficit <= 4'd0;
                if (audio_pending) audio_deficit <= audio_deficit + 4'd1;
                arb_state <= m1_arvalid ? ST_RD : ST_WR;
            end else if (audio_pending) begin
                // AudioMix promoted to 2nd-highest (just under the CPU-starvation
                // guard, ABOVE GPU/CPU-normal/Bridge). It is a tiny (~192 KB/s) but
                // hard-real-time per-output-sample voice fetch; with the native
                // 640x480 scanout reading the framebuffer at ~4x the old rate,
                // leaving audio at the bottom let its fetches stall behind GPU/CPU
                // long enough to stretch the output FIFO (audible pitch flutter).
                // Granting it whenever pending bounds its latency to one in-flight
                // transaction plus the bounded video line-burst above it in
                // io_sdram. Audio demand is sparse/short, so GPU/CPU barely notice;
                // the cpu_pending->gpu_deficit bump keeps the CPU guard able to
                // preempt a pathological audio burst, so no master starves.
                grant <= 2'd3;
                active_wr_gpuq <= 1'b0;
                audio_deficit <= 4'd0;
                arb_state <= ST_RD;
                if (cpu_pending) gpu_deficit <= gpu_deficit + 4'd1;
            end else if (m0_arvalid && !gpu_wq_should_drain) begin
                grant <= 2'd0;
                active_wr_gpuq <= 1'b0;
                arb_state <= ST_RD;
                if (!gpu_wq_empty)
                    gpu_reads_since_write <= gpu_reads_since_write + 4'd1;
                else
                    gpu_reads_since_write <= 4'd0;
                if (cpu_pending) gpu_deficit <= gpu_deficit + 4'd1;
                if (audio_pending) audio_deficit <= audio_deficit + 4'd1;
            end else if (gpu_wq_head_ready) begin
                grant <= 2'd0;
                active_wr_gpuq <= 1'b1;
                active_gpuq_awlen <= gpu_wq_drain_awlen;
                gpuq_w_idx <= 3'd0;
                arb_state <= ST_WR;
                gpu_reads_since_write <= 4'd0;
                if (cpu_pending) gpu_deficit <= gpu_deficit + 4'd1;
                if (audio_pending) audio_deficit <= audio_deficit + 4'd1;
            end else if (m0_arvalid) begin
                grant <= 2'd0;
                active_wr_gpuq <= 1'b0;
                arb_state <= ST_RD;
                gpu_reads_since_write <= 4'd0;
                if (cpu_pending) gpu_deficit <= gpu_deficit + 4'd1;
                if (audio_pending) audio_deficit <= audio_deficit + 4'd1;
            end else if (m1_arvalid) begin
                grant <= 2'd1;
                active_wr_gpuq <= 1'b0;
                arb_state <= ST_RD;
                gpu_deficit <= 4'd0;
                if (audio_pending) audio_deficit <= audio_deficit + 4'd1;
            end else if (m1_awvalid) begin
                grant <= 2'd1;
                active_wr_gpuq <= 1'b0;
                arb_state <= ST_WR;
                gpu_deficit <= 4'd0;
                if (audio_pending) audio_deficit <= audio_deficit + 4'd1;
            end else if (m2_awvalid) begin
                grant <= 2'd2;
                active_wr_gpuq <= 1'b0;
                arb_state <= ST_WR;
                if (cpu_pending) gpu_deficit <= gpu_deficit + 4'd1;
                if (audio_pending) audio_deficit <= audio_deficit + 4'd1;
            end else if (m3_arvalid) begin
                grant <= 2'd3;
                active_wr_gpuq <= 1'b0;
                arb_state <= ST_RD;
                audio_deficit <= 4'd0;
            end else begin
                gpu_deficit <= 4'd0;
                audio_deficit <= 4'd0;
            end
        end

        ST_RD: begin
            if (s_rvalid && s_rready && s_rlast)
                arb_state <= ST_IDLE;
        end

        ST_WR: begin
            if (s_bvalid) begin
                arb_state <= ST_IDLE;
                active_wr_gpuq <= 1'b0;
            end
        end

        default: begin
            arb_state <= ST_IDLE;
            active_wr_gpuq <= 1'b0;
        end
        endcase
    end
end

// ============================================
// Master -> Slave channel mux (combinational)
// ============================================

// AR channel.
assign s_arvalid = (active_rd && !rd_completing) ? (grant_m0 ? m0_arvalid :
                                                     grant_m1 ? m1_arvalid :
                                                                m3_arvalid) : 1'b0;
assign s_araddr  = grant_m0 ? m0_araddr  :
                   grant_m1 ? m1_araddr  : m3_araddr;
assign s_arlen   = grant_m0 ? m0_arlen   :
                   grant_m1 ? m1_arlen   : m3_arlen;

// AW/W channel.  Posted M0 writes drain as native SDRAM bursts: the GPU's
// contiguous full-strobe framebuffer AW (up to 8 beats) is coalesced into a
// single slave burst (s_awlen = run length), amortizing the per-grant AW /
// arbitration / SDRAM command overhead over the whole span.
assign s_awvalid = (active_wr && !wr_completing)
                 ? (active_wr_gpuq ? 1'b1 :
                    grant_m1       ? m1_awvalid :
                    grant_m2       ? m2_awvalid : 1'b0)
                 : 1'b0;
assign s_awaddr  = active_wr_gpuq ? gpu_wq_addr[gpu_wq_rd_ptr] :
                   grant_m1       ? m1_awaddr : m2_awaddr;
assign s_awlen   = active_wr_gpuq ? {5'b0, active_gpuq_awlen} :
                   grant_m1       ? m1_awlen : m2_awlen;

assign s_wvalid  = (active_wr && !wr_completing)
                 ? (active_wr_gpuq ? 1'b1 :
                    grant_m1       ? m1_wvalid :
                    grant_m2       ? m2_wvalid : 1'b0)
                 : 1'b0;
assign s_wdata   = active_wr_gpuq ? gpu_wq_data[active_gpuq_w_ptr] :
                   grant_m1       ? m1_wdata : m2_wdata;
assign s_wstrb   = active_wr_gpuq ? gpu_wq_strb[active_gpuq_w_ptr] :
                   grant_m1       ? m1_wstrb : m2_wstrb;
assign s_wlast   = active_wr_gpuq ? active_gpuq_wlast :
                   grant_m1       ? m1_wlast : m2_wlast;

// ============================================
// Slave -> Master channel demux (combinational)
// ============================================

assign m0_arready = (active_rd && grant_m0) ? s_arready : 1'b0;
assign m1_arready = (active_rd && grant_m1) ? s_arready : 1'b0;
assign m3_arready = (active_rd && grant_m3) ? s_arready : 1'b0;

assign m0_rvalid = (active_rd && grant_m0) ? s_rvalid : 1'b0;
assign m1_rvalid = (active_rd && grant_m1) ? s_rvalid : 1'b0;
assign m3_rvalid = (active_rd && grant_m3) ? s_rvalid : 1'b0;

// Honor each read master's RREADY.  M1 (CPU) and M3 (AudioMix) can back-
// pressure; M0 (GPU) has no RREADY because its read consumer (tex cache / DMA)
// captures every beat the cycle m_rvalid lands, so it is contractually always
// ready and tied to 1'b1.
assign s_rready = active_rd ? (grant_m1 ? m1_rready :
                               grant_m3 ? m3_rready : 1'b1) : 1'b1;

assign m0_rdata  = s_rdata;
assign m1_rdata  = s_rdata;
assign m3_rdata  = s_rdata;
assign m0_rresp  = s_rresp;
assign m1_rresp  = s_rresp;
assign m3_rresp  = s_rresp;
assign m0_rlast  = s_rlast;
assign m1_rlast  = s_rlast;
assign m3_rlast  = s_rlast;

assign m0_awready = !m0w_active && m0_aw_has_space;
assign m1_awready = (active_wr && grant_m1 && !active_wr_gpuq) ? s_awready : 1'b0;
assign m2_awready = (active_wr && grant_m2 && !active_wr_gpuq) ? s_awready : 1'b0;

assign m0_wready = m0w_active && !gpu_wq_full;
assign m1_wready = (active_wr && grant_m1 && !active_wr_gpuq) ? s_wready : 1'b0;
assign m2_wready = (active_wr && grant_m2 && !active_wr_gpuq) ? s_wready : 1'b0;

assign m0_bvalid = m0_bvalid_q;
assign m1_bvalid = (active_wr && grant_m1 && !active_wr_gpuq) ? s_bvalid : 1'b0;
assign m2_bvalid = (active_wr && grant_m2 && !active_wr_gpuq) ? s_bvalid : 1'b0;
assign m0_bresp  = 2'b00;
assign m1_bresp  = s_bresp;
assign m2_bresp  = s_bresp;

// The GPU posted-write queue supplies W beats continuously (s_wvalid held high
// from the queue), so its bursts can never underrun → safe for native slave
// streaming.  CPU/bridge writes pass through and may stall, so wcont=0 keeps
// them on the serialized path.
assign s_wcont = active_wr_gpuq;

// Diagnostic taps
assign dbg_arb_state   = arb_state;
assign dbg_cpu_pending = cpu_pending;
assign dbg_grant       = grant;

endmodule
