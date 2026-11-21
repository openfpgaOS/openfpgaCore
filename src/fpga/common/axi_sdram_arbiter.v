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
//   M2 = bridge file DMA (Pocket: APF writes only, AR tied off;
//                         MiSTer: hps_bridge sector engine, read + write)
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
// Bridge fairness counter (2026-08-26): the same idiom one level down.
// M2 (hps_bridge / APF bridge) sits at the bottom of the normal priority
// chain with NOTHING bounding its wait: a master mix that keeps a request
// pending on every ST_IDLE pass starves M2 indefinitely.  Proven in sim
// with the production hps_bridge (tb_sdram_lock90: a saturating CPU froze
// the boot.rom ioctl stream at 2/74020 words, S_BOOT_AW held forever,
// ioctl_wait backpressuring MiSTer main) — the mechanism behind a stock-
// DE10 "boot.rom NEVER ARRIVED" diag report.  Any non-M2 grant while M2
// is pending bumps brg_deficit; at BRG_FAIR_THRESHOLD the bridge is
// granted ahead of the normal arms (still below the CPU guard, whose own
// deficit an M2 grant bumps, so the two guards alternate rather than
// starve each other; audio overshoot is bounded by its sparseness and the
// counter saturates rather than wraps).
//
// Single outstanding transaction at the SDRAM slave -- grants one master at a
// time, holds until read completes (R.rlast) or write completes (B.bvalid).
// M0 AW/W handshakes are decoupled from that grant by the posted queue.
//
// GPU_COMMIT_B (Pocket os25 INCLUDE_SDRAM_WRITE_PIPE, with axi_sdram_slave
// EARLY_WRITE_ACK=1): the slave's B for a GPU queue write then means
// "released into io_sdram's in-order queue", not "committed", so it only ends
// the grant and pops the queue exactly as before.  The GPU's B is generated
// from the slave's s_bcommit pulses instead, through a small in-order FIFO of
// popped groups, so m0_bvalid keeps its "pixels are in SDRAM" meaning for
// fences, flips and the blend/z read barrier.
//
`default_nettype none

module axi_sdram_arbiter #(
    parameter [3:0] CPU_FAIR_THRESHOLD   = 4'd8, // Force CPU grant after this many non-CPU grants
    parameter [3:0] BRG_FAIR_THRESHOLD   = 4'd12,// Force bridge grant after this many non-bridge grants
    parameter [3:0] GPU_WRITE_READ_BUDGET = 4'd4, // Max GPU reads while posted writes wait
    parameter BUFFERED_SLAVE = 1'b0, // Address queues may accept again before completion
    parameter GPU_WRITE_QUEUE_DEPTH = 8, // Power of two, at least one 8-word burst
    parameter GPU_COMMIT_B = 0, // 1: m0_bvalid from s_bcommit (see header)
    // 1 (Pocket os25 INCLUDE_SDRAM_MASKED_BURSTS): partial-strobe GPU queue
    // entries join the coalesced run like full ones (see gwq_fs0 below).
    parameter GPU_WQ_MASKED_MERGE = 0,
    // 1 (Pocket os25 INCLUDE_SDRAM_WHOLE_BURSTS): a GPU write run is drained
    // only once its AW's final beat is queued (see gpu_wq_head_ready).
    parameter GPU_WQ_WHOLE_BURST = 0
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

    // Master 2: bridge file DMA.  Write channel used by both targets;
    // the read channel exists for MiSTer's hps_bridge (disk-image sector
    // writeback reads SDRAM → sd_buff).  Pocket ties AR off.
    input  wire        m2_arvalid,
    output wire        m2_arready,
    input  wire [31:0] m2_araddr,
    input  wire [7:0]  m2_arlen,
    output wire        m2_rvalid,
    output wire [31:0] m2_rdata,
    output wire [1:0]  m2_rresp,
    output wire        m2_rlast,
    input  wire        m2_rready,
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

    // Master 3: Audio Mixer (read-only)
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
    // GPU_COMMIT_B: slave pulse, one per early-released write as it commits,
    // oldest first.  Ignored when GPU_COMMIT_B == 0.
    input  wire        s_bcommit,
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
reg [1:0] grant;  // 0=GPU, 1=CPU, 2=Bridge, 3=AudioMix
reg       active_wr_gpuq;
reg [2:0] active_gpuq_awlen;   // beats-1 of the GPU write burst currently draining (0..7)
reg [2:0] gpuq_w_idx;          // beat index within that burst
reg       gpuq_w_done;         // final W accepted; hold idle until B

// Fairness: deficit counter prevents GPU (and technically Audio, but
// it's below CPU anyway) from starving the CPU.  Increments each time
// a non-CPU master wins while the CPU has a pending request.  When it
// reaches the threshold, the CPU is granted unconditionally.  Resets
// when the CPU gets a grant or has no pending request.
reg [3:0] gpu_deficit;
wire cpu_pending = m1_arvalid | m1_awvalid;

// Bridge (M2) fairness — see the header block.  Bumps on any non-M2 grant
// while M2 is pending; resets on an M2 grant or when nothing is pending.
// Saturating (not wrapping): a pathological streak of higher-priority
// grants past the threshold must keep the guard TRIPPED, not un-trip it.
reg [3:0] brg_deficit;
wire brg_pending = m2_arvalid | m2_awvalid;
wire [3:0] brg_deficit_inc = (brg_deficit == 4'hF) ? 4'hF : brg_deficit + 4'd1;

// Audio fairness is provided by the fixed 2nd-priority audio grant below (the
// unconditional `else if (audio_pending)` arm), which bounds AudioMix latency
// to one in-flight transaction — no deficit counter needed.
wire audio_pending = m3_arvalid;

// Posted M0 write queue.  Entries are single 32-bit word writes; AWLEN>0
// GPU bursts are split into consecutive queue entries and one B is returned
// when the entry carrying WLAST commits.
//
// Eight entries hold one complete GPU burst. os30 uses sixteen so the next
// burst can queue while the current burst commits, reducing gaps during
// clears and cache writebacks. Slots still retire on the downstream B;
// capacity does not change the burst limit or completion semantics.
localparam GPU_WQ_DEPTH = GPU_WRITE_QUEUE_DEPTH;
localparam GPU_WQ_HIGH_WATERMARK = GPU_WQ_DEPTH - 2;
localparam GPU_WQ_PTR_W = $clog2(GPU_WQ_DEPTH);

reg [31:0] gpu_wq_addr [0:GPU_WQ_DEPTH-1];
reg [31:0] gpu_wq_data [0:GPU_WQ_DEPTH-1];
reg [3:0]  gpu_wq_strb [0:GPU_WQ_DEPTH-1];
reg        gpu_wq_last [0:GPU_WQ_DEPTH-1];
reg [GPU_WQ_PTR_W-1:0] gpu_wq_rd_ptr;
reg [GPU_WQ_PTR_W-1:0] gpu_wq_wr_ptr;
reg [GPU_WQ_PTR_W:0] gpu_wq_count;

reg        m0w_active;
reg [31:0] m0w_addr;
reg [7:0]  m0w_beats_left;
reg        m0_bvalid_q;
reg [3:0]  gpu_reads_since_write;
// GPU_WQ_WHOLE_BURST: AWs whose WLAST beat is in the queue and not yet drained.
// Entries are FIFO, so a non-zero count means the head entry's AW is
// complete.  Bounded by the queue depth.
reg [GPU_WQ_PTR_W:0] gpu_wq_awdone;
reg                  gpu_wq_awdone_nz;   // registered (gpu_wq_awdone != 0)

wire grant_m0 = (grant == 2'd0);
wire grant_m1 = (grant == 2'd1);
wire grant_m2 = (grant == 2'd2);
wire grant_m3 = (grant == 2'd3);
wire active_rd = (arb_state == ST_RD);
wire active_wr = (arb_state == ST_WR);

wire gpu_wq_empty = (gpu_wq_count == 0);
wire gpu_wq_full  = (gpu_wq_count == GPU_WQ_DEPTH);
wire [GPU_WQ_PTR_W:0] gpu_wq_free = GPU_WQ_DEPTH - gpu_wq_count;
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
// GPU_WQ_MASKED_MERGE=1 drops the full-strobe requirement: every beat carries
// its own byte enables through the native path (slave W0/W1 capture and
// continuation pulls carry wstrb; io_sdram drives DQM per halfword from them
// in ST_WRITE_2/3), so a masked beat costs DQM only, never a read-modify-write
// and never its own SDRAM transaction.  Contiguity is still by construction.
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
wire gwq_f0 = GPU_WQ_MASKED_MERGE || (gpu_wq_strb[gwq_p0] == 4'hF);
wire gwq_f1 = GPU_WQ_MASKED_MERGE || (gpu_wq_strb[gwq_p1] == 4'hF);
wire gwq_f2 = GPU_WQ_MASKED_MERGE || (gpu_wq_strb[gwq_p2] == 4'hF);
wire gwq_f3 = GPU_WQ_MASKED_MERGE || (gpu_wq_strb[gwq_p3] == 4'hF);
wire gwq_f4 = GPU_WQ_MASKED_MERGE || (gpu_wq_strb[gwq_p4] == 4'hF);
wire gwq_f5 = GPU_WQ_MASKED_MERGE || (gpu_wq_strb[gwq_p5] == 4'hF);
wire gwq_f6 = GPU_WQ_MASKED_MERGE || (gpu_wq_strb[gwq_p6] == 4'hF);
wire gwq_f7 = GPU_WQ_MASKED_MERGE || (gpu_wq_strb[gwq_p7] == 4'hF);
wire gwq_fs0 = gwq_f0;
wire gwq_c1 = gwq_fs0 && (gpu_wq_count >= 4'd2) && !gpu_wq_last[gwq_p0] && gwq_f1;
wire gwq_c2 = gwq_c1  && (gpu_wq_count >= 4'd3) && !gpu_wq_last[gwq_p1] && gwq_f2;
wire gwq_c3 = gwq_c2  && (gpu_wq_count >= 4'd4) && !gpu_wq_last[gwq_p2] && gwq_f3;
wire gwq_c4 = gwq_c3  && (gpu_wq_count >= 4'd5) && !gpu_wq_last[gwq_p3] && gwq_f4;
wire gwq_c5 = gwq_c4  && (gpu_wq_count >= 4'd6) && !gpu_wq_last[gwq_p4] && gwq_f5;
wire gwq_c6 = gwq_c5  && (gpu_wq_count >= 4'd7) && !gpu_wq_last[gwq_p5] && gwq_f6;
wire gwq_c7 = gwq_c6  && (gpu_wq_count >= 4'd8) && !gpu_wq_last[gwq_p6] && gwq_f7;
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
wire [GPU_WQ_PTR_W-1:0] active_gpuq_last_ptr = gpu_wq_rd_ptr + active_gpuq_awlen;
// GPU_COMMIT_B commit FIFO: one entry per popped group (in pop order):
// {carries the AW's WLAST, refused (SLVERR: no SDRAM write, so no commit
// will come)}.  The head retires on a commit pulse (or at once if refused);
// commits that arrive while a refused head is retiring are counted, never
// dropped.  Released groups outstanding at the slave are at most two (one
// executing in io_sdram, one queued), refused ones retire immediately, and a
// new GPU write is only granted with at most two entries present, so four
// entries cannot overflow.  All of this is swept when GPU_COMMIT_B == 0.
reg  [3:0] gcb_last;
reg  [3:0] gcb_err;
reg  [1:0] gcb_rd, gcb_wr;
reg  [2:0] gcb_count;
reg  [2:0] gcb_commits;
wire [2:0] gcb_commits_avail = gcb_commits + (s_bcommit ? 3'd1 : 3'd0);
wire       gcb_head_err = gcb_err[gcb_rd];
wire       gcb_retire = GPU_COMMIT_B && (gcb_count != 3'd0) &&
                        (gcb_head_err || (gcb_commits_avail != 3'd0));
wire [2:0] gcb_count_next = gcb_count + (gpu_wq_pop ? 3'd1 : 3'd0)
                                      - (gcb_retire ? 3'd1 : 3'd0);
// Room for another GPU write group: registered (from the next count) so the
// grant decision only sees one more flop input.
reg        gcb_room_r;
wire       gcb_room = !GPU_COMMIT_B || gcb_room_r;
wire [GPU_WQ_PTR_W:0] gpu_wq_awdone_next = gpu_wq_awdone
                     + ((m0_w_post_fire && m0_w_post_last) ? 1'b1 : 1'b0)
                     - ((gpu_wq_pop && gpu_wq_last[active_gpuq_last_ptr]) ? 1'b1 : 1'b0);
// Legacy: drain as soon as two entries (or a lone final beat) are queued, so
// an 8-beat AW streaming in at one beat per cycle is cut into 2 + 6.
// GPU_WQ_WHOLE_BURST=1: wait until the head AW's final beat is queued (a
// registered count).  The GPU's W beats for an accepted AW are continuous and
// the AW was only accepted with room for all of them, so the wait is at most
// AWLEN cycles; meanwhile reads and other masters are granted as usual.
// Unless masked beats merge, a run cannot grow past a partial-strobe entry, so
// a partial head, or a partial second entry, drains at once (4-bit compares on
// the first two entries, next to the legacy head WLAST test).
wire       gpu_wq_head_ready = !gpu_wq_empty && gcb_room
                            && (GPU_WQ_WHOLE_BURST
                                ? (gpu_wq_awdone_nz || gpu_wq_full ||
                                   (!GPU_WQ_MASKED_MERGE &&
                                    ((gpu_wq_strb[gwq_p0] != 4'hF) ||
                                     ((gpu_wq_count >= 4'd2) &&
                                      (gpu_wq_strb[gwq_p1] != 4'hF)))))
                                : (gpu_wq_last[gpu_wq_rd_ptr] ||
                                   (gpu_wq_count >= 4'd2)));
// Combinational (not registered) so the burst length reflects the queue at the
// exact grant cycle — a registered value lags the count by one cycle and would
// drain singles on the cycle count first reaches 2.  The scan is only
// strb/last/count comparisons (no 32-bit address compare — contiguity is
// guaranteed by construction), and it feeds the active_gpuq_awlen flop, not the
// grant-priority mux, so it does not lengthen the AR-priority decision.
wire [2:0] gpu_wq_drain_awlen = gpu_wq_run;
wire [GPU_WQ_PTR_W-1:0] active_gpuq_w_ptr    = gpu_wq_rd_ptr + gpuq_w_idx;
wire       active_gpuq_wlast = (gpuq_w_idx == active_gpuq_awlen);
// REVERTED 2026-07-24 (was: pop-on-W-accept early freeing, the bandwidth-7
// perf change).  On hardware, Doom wedged twice with the GPU frozen on
// write-drain terms that never cleared (S_EXECUTE fence stuck at
// m_wr_inflight!=0 with everything drained; S_FRAG_PIPE shift stuck at
// near-full) — the only change in that accounting family was this early
// freeing, which makes the count depend on exact per-beat W-handshake
// semantics across the wcont fast path.  No sim harness models the real
// arbiter+slave+io_sdram chain under GPU load (tb_gpu_wrpath is rotten:
// pre-doorbell protocol), so the interleave was never pinned.  DO NOT
// re-land the early-free without first resurrecting tb_gpu_wrpath and
// adding a count/B-per-AW invariant soak there.
wire [3:0] gpu_wq_pop_count = gpu_wq_pop ? ({1'b0, active_gpuq_awlen} + 4'd1) : 4'd0;

wire       gpu_wq_high     = (gpu_wq_count >= GPU_WQ_HIGH_WATERMARK);
wire [3:0] gpu_wq_read_budget_limit = gpu_wq_high ? 4'd1 : GPU_WRITE_READ_BUDGET;
wire       gpu_wq_read_budget_spent = (gpu_reads_since_write >= gpu_wq_read_budget_limit);
wire       gpu_wq_should_drain = gpu_wq_head_ready &&
                                 (!m0_arvalid || gpu_wq_read_budget_spent);

// Completion guards: on the cycle bvalid/rlast fires, the slave returns to
// S_IDLE while the arbiter is still registered in ST_WR/ST_RD.
wire rd_completing = active_rd && s_rvalid && s_rlast;
wire wr_completing = active_wr && s_bvalid;

// The legacy single-transaction slave drops READY after one address. A
// clock bridge can remain ready, so explicitly retire each address handshake
// until this grant completes. Also block an upstream next AR on RLAST: it
// must not see READY while the completion guard suppresses downstream VALID.
reg ar_sent, aw_sent;
always @(posedge clk or posedge reset) begin
    if (reset) begin
        ar_sent <= 1'b0;
        aw_sent <= 1'b0;
    end else if (!BUFFERED_SLAVE || arb_state == ST_IDLE) begin
        ar_sent <= 1'b0;
        aw_sent <= 1'b0;
    end else begin
        if (s_arvalid && s_arready) ar_sent <= 1'b1;
        if (s_awvalid && s_awready) aw_sent <= 1'b1;
    end
end
wire ar_ready_once = s_arready && (!BUFFERED_SLAVE || (!ar_sent && !rd_completing));
wire aw_ready_once = s_awready && (!BUFFERED_SLAVE || (!aw_sent && !wr_completing));

// Grant arbitration, M0 posted-write receiver, and M0 B response generation.
always @(posedge clk or posedge reset) begin
    if (reset) begin
        arb_state <= ST_IDLE;
        grant <= 2'd0;
        active_wr_gpuq <= 1'b0;
        active_gpuq_awlen <= 3'd0;
        gpuq_w_idx <= 3'd0;
        gpuq_w_done <= 1'b0;
        gpu_deficit <= 4'd0;
        brg_deficit <= 4'd0;
        gpu_wq_rd_ptr <= {GPU_WQ_PTR_W{1'b0}};
        gpu_wq_wr_ptr <= {GPU_WQ_PTR_W{1'b0}};
        gpu_wq_count <= 0;
        m0w_active <= 1'b0;
        m0w_addr <= 32'd0;
        m0w_beats_left <= 8'd0;
        m0_bvalid_q <= 1'b0;
        gpu_reads_since_write <= 4'd0;
        gcb_rd <= 2'd0;
        gcb_wr <= 2'd0;
        gcb_count <= 3'd0;
        gcb_room_r <= 1'b1;
        gcb_commits <= 3'd0;
        gpu_wq_awdone <= 0;
        gpu_wq_awdone_nz <= 1'b0;
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
            if (!GPU_COMMIT_B && gpu_wq_last[active_gpuq_last_ptr])
                m0_bvalid_q <= 1'b1;
            gpu_wq_rd_ptr <= gpu_wq_rd_ptr + active_gpuq_awlen + 3'd1;
        end

        if (GPU_COMMIT_B) begin
            if (gpu_wq_pop) begin
                gcb_last[gcb_wr] <= gpu_wq_last[active_gpuq_last_ptr];
                gcb_err[gcb_wr]  <= (s_bresp != 2'b00);
                gcb_wr <= gcb_wr + 2'd1;
            end
            if (gcb_retire) begin
                if (gcb_last[gcb_rd])
                    m0_bvalid_q <= 1'b1;
                gcb_rd <= gcb_rd + 2'd1;
            end
            gcb_count <= gcb_count_next;
            gcb_room_r <= (gcb_count_next <= 3'd2);
            gcb_commits <= gcb_commits_avail
                         - ((gcb_retire && !gcb_head_err) ? 3'd1 : 3'd0);
        end

        gpu_wq_count <= gpu_wq_count
                      + (m0_w_post_fire ? 4'd1 : 4'd0)
                      - gpu_wq_pop_count;
        if (GPU_WQ_WHOLE_BURST) begin
            gpu_wq_awdone <= gpu_wq_awdone_next;
            gpu_wq_awdone_nz <= (gpu_wq_awdone_next != 0);
        end

        if (active_wr && active_wr_gpuq && s_wvalid && s_wready && !active_gpuq_wlast)
            gpuq_w_idx <= gpuq_w_idx + 3'd1;
        if (active_wr && active_wr_gpuq && s_wvalid && s_wready && active_gpuq_wlast)
            gpuq_w_done <= 1'b1;

        if (gpu_wq_empty)
            gpu_reads_since_write <= 4'd0;

        case (arb_state)
        ST_IDLE: begin
            if (cpu_pending && gpu_deficit >= CPU_FAIR_THRESHOLD) begin
                grant <= 2'd1;
                active_wr_gpuq <= 1'b0;
                gpu_deficit <= 4'd0;
                arb_state <= m1_arvalid ? ST_RD : ST_WR;
                if (brg_pending) brg_deficit <= brg_deficit_inc;
            end else if (brg_pending && brg_deficit >= BRG_FAIR_THRESHOLD) begin
                // Bridge-starvation guard (see header).  Below the CPU
                // guard: an M2 grant bumps gpu_deficit, so a saturating
                // CPU still gets service every CPU_FAIR_THRESHOLD grants
                // while the bridge is bounded at BRG_FAIR_THRESHOLD —
                // the two guards alternate instead of starving each other.
                grant <= 2'd2;
                active_wr_gpuq <= 1'b0;
                brg_deficit <= 4'd0;
                arb_state <= m2_awvalid ? ST_WR : ST_RD;
                if (cpu_pending) gpu_deficit <= gpu_deficit + 4'd1;
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
                arb_state <= ST_RD;
                if (cpu_pending) gpu_deficit <= gpu_deficit + 4'd1;
                if (brg_pending) brg_deficit <= brg_deficit_inc;
            end else if (m0_arvalid && !gpu_wq_should_drain) begin
                grant <= 2'd0;
                active_wr_gpuq <= 1'b0;
                arb_state <= ST_RD;
                if (!gpu_wq_empty)
                    gpu_reads_since_write <= gpu_reads_since_write + 4'd1;
                else
                    gpu_reads_since_write <= 4'd0;
                if (cpu_pending) gpu_deficit <= gpu_deficit + 4'd1;
                if (brg_pending) brg_deficit <= brg_deficit_inc;
            end else if (gpu_wq_head_ready) begin
                grant <= 2'd0;
                active_wr_gpuq <= 1'b1;
                active_gpuq_awlen <= gpu_wq_drain_awlen;
                gpuq_w_idx <= 3'd0;
                gpuq_w_done <= 1'b0;
                arb_state <= ST_WR;
                gpu_reads_since_write <= 4'd0;
                if (cpu_pending) gpu_deficit <= gpu_deficit + 4'd1;
                if (brg_pending) brg_deficit <= brg_deficit_inc;
            end else if (m0_arvalid) begin
                grant <= 2'd0;
                active_wr_gpuq <= 1'b0;
                arb_state <= ST_RD;
                gpu_reads_since_write <= 4'd0;
                if (cpu_pending) gpu_deficit <= gpu_deficit + 4'd1;
                if (brg_pending) brg_deficit <= brg_deficit_inc;
            end else if (m1_arvalid) begin
                grant <= 2'd1;
                active_wr_gpuq <= 1'b0;
                arb_state <= ST_RD;
                gpu_deficit <= 4'd0;
                if (brg_pending) brg_deficit <= brg_deficit_inc;
            end else if (m1_awvalid) begin
                grant <= 2'd1;
                active_wr_gpuq <= 1'b0;
                arb_state <= ST_WR;
                gpu_deficit <= 4'd0;
                if (brg_pending) brg_deficit <= brg_deficit_inc;
            end else if (m2_awvalid || m2_arvalid) begin
                // Bridge AW preferred over AR within the arm; the bridge
                // FSM is single-transaction so both are never pending at
                // once in practice.
                grant <= 2'd2;
                active_wr_gpuq <= 1'b0;
                brg_deficit <= 4'd0;
                arb_state <= m2_awvalid ? ST_WR : ST_RD;
                if (cpu_pending) gpu_deficit <= gpu_deficit + 4'd1;
            end else if (m3_arvalid) begin
                grant <= 2'd3;
                active_wr_gpuq <= 1'b0;
                arb_state <= ST_RD;
                if (brg_pending) brg_deficit <= brg_deficit_inc;
            end else begin
                gpu_deficit <= 4'd0;
                brg_deficit <= 4'd0;
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
assign s_arvalid = (active_rd && !rd_completing && (!BUFFERED_SLAVE || !ar_sent)) ? (grant_m0 ? m0_arvalid :
                                                     grant_m1 ? m1_arvalid :
                                                     grant_m2 ? m2_arvalid :
                                                                m3_arvalid) : 1'b0;
assign s_araddr  = grant_m0 ? m0_araddr  :
                   grant_m1 ? m1_araddr  :
                   grant_m2 ? m2_araddr  : m3_araddr;
assign s_arlen   = grant_m0 ? m0_arlen   :
                   grant_m1 ? m1_arlen   :
                   grant_m2 ? m2_arlen   : m3_arlen;

// AW/W channel.  Posted M0 writes drain as native SDRAM bursts: the GPU's
// contiguous full-strobe framebuffer AW (up to 8 beats) is coalesced into a
// single slave burst (s_awlen = run length), amortizing the per-grant AW /
// arbitration / SDRAM command overhead over the whole span.
assign s_awvalid = (active_wr && !wr_completing && (!BUFFERED_SLAVE || !aw_sent))
                 ? (active_wr_gpuq ? 1'b1 :
                    grant_m1       ? m1_awvalid :
                    grant_m2       ? m2_awvalid : 1'b0)
                 : 1'b0;
assign s_awaddr  = active_wr_gpuq ? gpu_wq_addr[gpu_wq_rd_ptr] :
                   grant_m1       ? m1_awaddr : m2_awaddr;
assign s_awlen   = active_wr_gpuq ? {5'b0, active_gpuq_awlen} :
                   grant_m1       ? m1_awlen : m2_awlen;

assign s_wvalid  = (active_wr && !wr_completing)
                 ? (active_wr_gpuq ? !gpuq_w_done :
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

assign m0_arready = (active_rd && grant_m0) ? ar_ready_once : 1'b0;
assign m1_arready = (active_rd && grant_m1) ? ar_ready_once : 1'b0;
assign m2_arready = (active_rd && grant_m2) ? ar_ready_once : 1'b0;
assign m3_arready = (active_rd && grant_m3) ? ar_ready_once : 1'b0;

assign m0_rvalid = (active_rd && grant_m0) ? s_rvalid : 1'b0;
assign m1_rvalid = (active_rd && grant_m1) ? s_rvalid : 1'b0;
assign m2_rvalid = (active_rd && grant_m2) ? s_rvalid : 1'b0;
assign m3_rvalid = (active_rd && grant_m3) ? s_rvalid : 1'b0;

// Honor each read master's RREADY.  M1 (CPU), M2 (bridge) and M3 (AudioMix)
// can back-pressure; M0 (GPU) has no RREADY because its read consumer (tex
// cache / DMA) captures every beat the cycle m_rvalid lands, so it is
// contractually always ready and tied to 1'b1.
assign s_rready = active_rd ? (grant_m1 ? m1_rready :
                               grant_m2 ? m2_rready :
                               grant_m3 ? m3_rready : 1'b1) : 1'b1;

assign m0_rdata  = s_rdata;
assign m1_rdata  = s_rdata;
assign m2_rdata  = s_rdata;
assign m3_rdata  = s_rdata;
assign m0_rresp  = s_rresp;
assign m1_rresp  = s_rresp;
assign m2_rresp  = s_rresp;
assign m3_rresp  = s_rresp;
assign m0_rlast  = s_rlast;
assign m1_rlast  = s_rlast;
assign m2_rlast  = s_rlast;
assign m3_rlast  = s_rlast;

assign m0_awready = !m0w_active && m0_aw_has_space;
assign m1_awready = (active_wr && grant_m1 && !active_wr_gpuq) ? aw_ready_once : 1'b0;
assign m2_awready = (active_wr && grant_m2 && !active_wr_gpuq) ? aw_ready_once : 1'b0;

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
