//
// VexiiRiscv CPU System — 3-master × 3-target AXI4 router.
//
// Three CPU AXI4 master inputs fan out to three downstream slaves.
// Each target is handled by an independent `cpu_target_port` with its
// own read and write sub-FSMs, so many transactions can overlap as
// long as they hit distinct target ports:
//
//   i_axi   (L1 I$ refills, read-only)    ───┐
//   mem_axi (L1 D$ + cbo.*)                  │
//   p_axi   (uncached LSU IO)                ├──→  4 target ports
//                                            │     (SDRAM / PSRAM /
//                                            │      LOCAL)
//                                            │
//
// Address decode → target:
//    0x10000000–0x13FFFFFF, 0x50000000–0x53FFFFFF → SDRAM
//    0x30000000 / 0x38000000 / 0x31000000         → PSRAM
//    everything else                              → LOCAL
// SRAM target port removed: SRAM is GPU-private and off the fabric.
//

`default_nettype none

module cpu_system (
    input wire clk,
    input wire reset_n,

    // ---- SDRAM AXI4 master ---------------------------------------
    output wire        m_sdram_arvalid,
    input  wire        m_sdram_arready,
    output wire [31:0] m_sdram_araddr,
    output wire [7:0]  m_sdram_arlen,

    input  wire        m_sdram_rvalid,
    output wire        m_sdram_rready,
    input  wire [31:0] m_sdram_rdata,
    input  wire [1:0]  m_sdram_rresp,
    input  wire        m_sdram_rlast,

    output wire        m_sdram_awvalid,
    input  wire        m_sdram_awready,
    output wire [31:0] m_sdram_awaddr,
    output wire [7:0]  m_sdram_awlen,

    output wire        m_sdram_wvalid,
    input  wire        m_sdram_wready,
    output wire [31:0] m_sdram_wdata,
    output wire [3:0]  m_sdram_wstrb,
    output wire        m_sdram_wlast,

    input  wire        m_sdram_bvalid,
    input  wire [1:0]  m_sdram_bresp,

    // ---- PSRAM AXI4 master (CRAM0 + CRAM1) -----------------------
    output wire        m_cram0_arvalid,
    input  wire        m_cram0_arready,
    output wire [31:0] m_cram0_araddr,
    output wire [7:0]  m_cram0_arlen,
    input  wire        m_cram0_rvalid,
    output wire        m_cram0_rready,
    input  wire [31:0] m_cram0_rdata,
    input  wire [1:0]  m_cram0_rresp,
    input  wire        m_cram0_rlast,
    output wire        m_cram0_awvalid,
    input  wire        m_cram0_awready,
    output wire [31:0] m_cram0_awaddr,
    output wire [7:0]  m_cram0_awlen,
    output wire        m_cram0_wvalid,
    input  wire        m_cram0_wready,
    output wire [31:0] m_cram0_wdata,
    output wire [3:0]  m_cram0_wstrb,
    output wire        m_cram0_wlast,
    input  wire        m_cram0_bvalid,
    input  wire [1:0]  m_cram0_bresp,

    // ---- Local peripheral AXI4 master ----------------------------
    output wire        m_local_arvalid,
    input  wire        m_local_arready,
    output wire [31:0] m_local_araddr,
    output wire [7:0]  m_local_arlen,

    input  wire        m_local_rvalid,
    output wire        m_local_rready,
    input  wire [31:0] m_local_rdata,
    input  wire [1:0]  m_local_rresp,
    input  wire        m_local_rlast,

    output wire        m_local_awvalid,
    input  wire        m_local_awready,
    output wire [31:0] m_local_awaddr,
    output wire [7:0]  m_local_awlen,
    output wire [1:0]  m_local_awburst,

    output wire        m_local_wvalid,
    input  wire        m_local_wready,
    output wire [31:0] m_local_wdata,
    output wire [3:0]  m_local_wstrb,
    output wire        m_local_wlast,

    input  wire        m_local_bvalid,
    input  wire [1:0]  m_local_bresp,

    // ---- Interrupts ---------------------------------------------
    input  wire        int_m_external,
    input  wire        int_m_timer
);

wire reset = ~reset_n;

// ============================================================
// CPU bus signals — maps the stock VexiiRiscv's three bus interfaces
// onto our internal three-master router.
//   i_axi   : FetchL1Axi4Plugin (read-only, I$ refills, 1-bit id)
//   mem_axi : LsuL1Axi4Plugin   (full R/W, D$ refills/writebacks + cbo.*)
//   p_axi   : LsuPlugin native cmd/rsp converted to single-beat AXI4
//             (uncached LSU IO — MMIO, framebuffer writes, etc.)
// ============================================================

// ============================================================
// Phase 2 — AXI register slices on each VexiiRiscv master.
//
// VexiiRiscv's pins were the dominant placement-pressure source on
// the 100 MHz CPU domain: each master fans out to all target
// cpu_target_ports (sdram, cram0, local), so without a slice the
// fitter has to keep VexiiRiscv physically close to whichever target
// owns the worst route — stretching VexiiRiscv across a wide region
// and exposing internal feedback paths (exe3→fetch1, FPU→I-cache RE)
// to long routing.  See openfpgaOS/docs/cr-* timing analysis.
//
// With the slice, VexiiRiscv's pins reach a register inside its own
// placement region and the long crossings to target ports become
// register-to-register transfers the placer can route freely.
// VexiiRiscv contracts; internal cones get short routes.
//
// Latency cost: +1 cycle each direction per AXI handshake.
// Cached inner loops are unaffected (no AXI traffic).  D$ refills
// pay +2 cycles amortised over an 8-beat burst.  Uncached LSU
// (per_axi) pays +2 per single-beat MMIO — recovered by the
// multi-outstanding LSU shim (M1) and burst-mode ring writes (M2).
//
// One slice per AXI channel.  The cpu-side wires (`*_cpu`) connect
// to VexiiRiscv directly; slice outputs drive the existing wire
// names that the cpu_target_ports already use, so target-port
// instantiations are unchanged.
// ============================================================

// i_axi
wire        i_arvalid;  wire        i_arready;
wire [31:0] i_araddr;
wire [0:0]  i_arid;
wire [7:0]  i_arlen;
wire        i_rvalid;   wire        i_rready;
wire [31:0] i_rdata;
wire [0:0]  i_rid;      wire [1:0]  i_rresp;
wire        i_rlast;

// i_axi cpu-side (between VexiiRiscv and slice)
wire        i_arvalid_cpu, i_arready_cpu;
wire [31:0] i_araddr_cpu;
wire [0:0]  i_arid_cpu;
wire [7:0]  i_arlen_cpu;
wire        i_rvalid_cpu, i_rready_cpu;
wire [31:0] i_rdata_cpu;
wire [0:0]  i_rid_cpu;
wire [1:0]  i_rresp_cpu;
wire        i_rlast_cpu;

// i_axi has AW/W/B ports on the CPU top but VexiiRiscv ties them off
// internally (read-only bridge), so the cpu_system side only connects R.
wire        i_awvalid_tie;
wire        i_wvalid_tie;
wire [0:0]  i_bid_tie;
wire [1:0]  i_bresp_tie;

// mem_axi (D$ cached) — post-slice, drives target ports
wire        mem_arvalid;  wire        mem_arready;
wire [31:0] mem_araddr;
wire [1:0]  mem_arid;
wire [7:0]  mem_arlen;
wire        mem_rvalid;   wire        mem_rready;
wire [31:0] mem_rdata;
wire [1:0]  mem_rid;      wire [1:0]  mem_rresp;
wire        mem_rlast;

wire        mem_awvalid;  wire        mem_awready;
wire [31:0] mem_awaddr;
wire [1:0]  mem_awid;
wire [7:0]  mem_awlen;
wire [1:0]  mem_awburst;
wire        mem_awallStrb;
wire        mem_wvalid;   wire        mem_wready;
wire [31:0] mem_wdata;    wire [3:0]  mem_wstrb;
wire        mem_wlast;
wire        mem_bvalid;   wire        mem_bready;
wire [1:0]  mem_bid;      wire [1:0]  mem_bresp;

// mem_axi cpu-side (between VexiiRiscv and slice)
wire        mem_arvalid_cpu, mem_arready_cpu;
wire [31:0] mem_araddr_cpu;
wire [1:0]  mem_arid_cpu;
wire [7:0]  mem_arlen_cpu;
wire        mem_rvalid_cpu, mem_rready_cpu;
wire [31:0] mem_rdata_cpu;
wire [1:0]  mem_rid_cpu;
wire [1:0]  mem_rresp_cpu;
wire        mem_rlast_cpu;
wire        mem_awvalid_cpu, mem_awready_cpu;
wire [31:0] mem_awaddr_cpu;
wire [1:0]  mem_awid_cpu;
wire [7:0]  mem_awlen_cpu;
wire [1:0]  mem_awburst_cpu;
// mem_awallStrb is unused by cpu_target_port (no slave reads it) so it
// doesn't need a slice — declared on the post-slice side only, tied to
// 0 since VexiiRiscv doesn't expose it.  Same for per_awallStrb below.
wire        mem_wvalid_cpu, mem_wready_cpu;
wire [31:0] mem_wdata_cpu;
wire [3:0]  mem_wstrb_cpu;
wire        mem_wlast_cpu;
wire        mem_bvalid_cpu, mem_bready_cpu;
wire [1:0]  mem_bid_cpu;
wire [1:0]  mem_bresp_cpu;

// p_axi (uncached IO) — post-slice, drives target ports.
// NB: per_axi is sourced by the LSU shim further down (it converts
// VexiiRiscv's native LsuPlugin cmd/rsp bus into single-beat AXI4),
// not directly by VexiiRiscv.  The slice still sits between the shim
// output and the target-port fan-out — the LSU shim's output is what
// fans out to all target ports, and that fan-out is the
// placement-pressure source we need to break.
wire        per_arvalid;  wire        per_arready;
wire [31:0] per_araddr;
wire [7:0]  per_arlen;
wire        per_rvalid;   wire        per_rready;
wire [31:0] per_rdata;    wire [1:0]  per_rresp;
wire        per_rlast;

wire        per_awvalid;  wire        per_awready;
wire [31:0] per_awaddr;
wire [7:0]  per_awlen;
wire [1:0]  per_awburst;
wire        per_awallStrb;
wire        per_wvalid;   wire        per_wready;
wire [31:0] per_wdata;    wire [3:0]  per_wstrb;
wire        per_wlast;
wire        per_bvalid;   wire        per_bready;
wire [1:0]  per_bresp;

// per_axi cpu-side (between LSU shim and slice)
wire        per_arvalid_cpu, per_arready_cpu;
wire [31:0] per_araddr_cpu;
wire [7:0]  per_arlen_cpu;
wire        per_rvalid_cpu, per_rready_cpu;
wire [31:0] per_rdata_cpu;
wire [1:0]  per_rresp_cpu;
wire        per_rlast_cpu;
wire        per_awvalid_cpu, per_awready_cpu;
wire [31:0] per_awaddr_cpu;
wire [7:0]  per_awlen_cpu;
wire [1:0]  per_awburst_cpu;
wire        per_wvalid_cpu, per_wready_cpu;
wire [31:0] per_wdata_cpu;
wire [3:0]  per_wstrb_cpu;
wire        per_wlast_cpu;
wire        per_bvalid_cpu, per_bready_cpu;
wire [1:0]  per_bresp_cpu;

// ============================================================
// Native LsuPlugin cmd/rsp bus → per_* AXI4 shim (M1: posted writes)
//
// VexiiRiscv (stock Generate) exposes the uncached LSU path as a
// non-AXI cmd/rsp bus.  We convert it to single-beat AXI4 here so
// downstream cpu_target_ports keep their uniform interface.
//
// Reads stay 1-deep: VexiiRiscv's commit stage stalls on rsp, so
// pipelining reads in the shim doesn't help.
//
// Writes are posted with a 4-deep FIFO: cmd_ready fires the cycle
// the write enters the FIFO, and rsp_valid pulses one cycle later
// — the CPU sees a fast write rsp and pipelines into the next
// store while the actual AW/W still travels through the slices to
// the target.  This is the key win after Phase 2 added +2 cycles
// of round-trip latency on per_axi.
//
// Ordering: a read can only be accepted when the write FIFO is
// empty (drains all pending writes first).  Avoids RAW hazards
// to MMIO regions where a write must complete before its side
// effect is observable.
//
// Trade-off: bus errors on writes (per_bresp != OK) are dropped.
// Acceptable for MMIO/UART — none of our targets generate write
// faults.  Reads still surface rresp via lsu_rsp_error.
// ============================================================
wire        lsu_cmd_valid;
wire        lsu_cmd_ready;
wire        lsu_cmd_write;
wire [31:0] lsu_cmd_addr;
wire [31:0] lsu_cmd_data;
wire [3:0]  lsu_cmd_mask;
wire        lsu_rsp_valid;
wire        lsu_rsp_error;
wire [31:0] lsu_rsp_data;

// Read tracker — 1-deep.
reg        lsu_inflight_read;
reg        lsu_ar_sent;
reg [31:0] lsu_rd_addr;

// Write FIFO — 4-deep posted-write queue.
localparam WR_FIFO_DEPTH = 4;
localparam WR_PTR_W      = 2;  // log2(WR_FIFO_DEPTH)
localparam WR_CNT_W      = 3;  // log2(WR_FIFO_DEPTH+1)

reg [31:0] wr_addr_mem [0:WR_FIFO_DEPTH-1];
reg [31:0] wr_data_mem [0:WR_FIFO_DEPTH-1];
reg [3:0]  wr_mask_mem [0:WR_FIFO_DEPTH-1];
reg [WR_PTR_W-1:0] wr_head, wr_tail;
reg [WR_CNT_W-1:0] wr_count;
reg                lsu_aw_sent, lsu_w_sent;  // per-head AW/W issue tracking

wire wr_fifo_full  = (wr_count == WR_FIFO_DEPTH);
wire wr_fifo_empty = (wr_count == 0);

// M2 coalescer state.  When the FIFO holds N consecutive same-address
// same-mask entries at the head, fire one AW with awlen=N-1
// and awburst=FIXED, pump N W-beats from FIFO[head..head+N-1], then
// pop all N entries on B.  burst_awlen_calc is combinational from the
// current FIFO contents; burst_awlen is the latched value at AW
// handshake (used for W-phase wlast detection).  This still helps
// repeated writes to one MMIO register, such as LUT uploads.
reg [WR_PTR_W-1:0] burst_awlen;
reg [WR_PTR_W-1:0] burst_w_idx;

// Accept logic.  A write enters the FIFO whenever it has room AND
// no read is in flight (read drained first; writes don't pass reads
// because the CPU's LSU stalls on read rsp anyway).  A read fires
// when no writes are pending and no other read is outstanding.
wire lsu_cmd_can_accept_wr = !lsu_inflight_read && !wr_fifo_full;
wire lsu_cmd_can_accept_rd = !lsu_inflight_read && wr_fifo_empty;
wire lsu_cmd_can_accept    =  lsu_cmd_write ? lsu_cmd_can_accept_wr
                                            : lsu_cmd_can_accept_rd;
wire lsu_cmd_fire = lsu_cmd_valid && lsu_cmd_can_accept;
wire wr_push     = lsu_cmd_fire & lsu_cmd_write;
wire wr_pop      = per_bvalid_cpu & per_bready_cpu;

// Posted-write rsp: pulse one cycle after the FIFO push so the CPU
// retires the store and pipelines the next.  Read rsp is when R-last lands.
reg wr_rsp_q;
always @(posedge clk or posedge reset) begin
    if (reset) wr_rsp_q <= 1'b0;
    else       wr_rsp_q <= wr_push;
end

assign lsu_rsp_valid = (per_rvalid_cpu & per_rready_cpu & per_rlast_cpu)
                     | wr_rsp_q;
assign lsu_rsp_data  = per_rdata_cpu;
// Surface read errors only; write errors are dropped (writes posted).
assign lsu_rsp_error = (per_rvalid_cpu & per_rready_cpu & per_rlast_cpu)
                       ? (per_rresp_cpu != 2'b00)
                       : 1'b0;
assign lsu_cmd_ready = lsu_cmd_can_accept;

// Read FSM — 1-deep, identical behavior to the previous shim.
always @(posedge clk or posedge reset) begin
    if (reset) begin
        lsu_inflight_read <= 1'b0;
        lsu_ar_sent       <= 1'b0;
        lsu_rd_addr       <= 32'b0;
    end else begin
        if (lsu_cmd_fire && !lsu_cmd_write) begin
            lsu_rd_addr <= lsu_cmd_addr;
        end
        if (per_arvalid_cpu && per_arready_cpu) lsu_ar_sent <= 1'b1;
        if (per_rvalid_cpu  && per_rready_cpu && per_rlast_cpu) begin
            lsu_inflight_read <= 1'b0;
            lsu_ar_sent       <= 1'b0;
        end else if (lsu_cmd_fire && !lsu_cmd_write) begin
            lsu_inflight_read <= 1'b1;
        end
    end
end

// M2 match scan: how many consecutive head entries share addr+mask?
// burst_awlen_calc is N-1 (0=single beat, 3=4-beat burst).  Combinational
// — at the AW handshake posedge, both the slice and our burst_awlen
// register sample the same value, so they stay consistent through the
// W phase.
wire [WR_PTR_W-1:0] head1 = wr_head + {{(WR_PTR_W-1){1'b0}}, 1'b1};
wire [WR_PTR_W-1:0] head2 = wr_head + 2'd2;
wire [WR_PTR_W-1:0] head3 = wr_head + 2'd3;
wire match01 = (wr_count >= 3'd2)
            && (wr_addr_mem[wr_head] == wr_addr_mem[head1])
            && (wr_mask_mem[wr_head] == wr_mask_mem[head1]);
wire match02 = match01 && (wr_count >= 3'd3)
            && (wr_addr_mem[wr_head] == wr_addr_mem[head2])
            && (wr_mask_mem[wr_head] == wr_mask_mem[head2]);
wire match03 = match02 && (wr_count >= 3'd4)
            && (wr_addr_mem[wr_head] == wr_addr_mem[head3])
            && (wr_mask_mem[wr_head] == wr_mask_mem[head3]);

wire [WR_PTR_W-1:0] burst_awlen_calc = match03 ? 2'd3
                                     : match02 ? 2'd2
                                     : match01 ? 2'd1
                                     : 2'd0;

// W-beat data routing: head + w_idx for both single-beat and burst.
wire [WR_PTR_W-1:0] w_idx     = wr_head + burst_w_idx;
wire [31:0]         w_addr    = wr_addr_mem[wr_head];   // FIXED: same every beat
wire [31:0]         w_data    = wr_data_mem[w_idx];
wire [3:0]          w_mask    = wr_mask_mem[w_idx];
// Pipeline-race fix (cr-gpu-and-tri-wedges issue 1): burst_awlen
// updates at the same posedge the AW handshake commits.  Computing
// w_is_last against the OLD burst_awlen on that cycle marks beat 0
// of a multi-beat burst as last when the previous burst was a
// single-beat (the bind_texture-followed-by-draw_triangles wedge
// shape — see also tb_gpu_chain test_lsu_bind_then_draw).  Use
// burst_awlen_calc when the AW handshake is firing this cycle.
wire [WR_PTR_W-1:0] eff_burst_awlen =
    (per_awvalid_cpu && per_awready_cpu) ? burst_awlen_calc : burst_awlen;
wire                w_is_last = (burst_w_idx == eff_burst_awlen);

// Write FIFO push/pop and burst tracking.
integer wi;
always @(posedge clk or posedge reset) begin
    if (reset) begin
        wr_head     <= {WR_PTR_W{1'b0}};
        wr_tail     <= {WR_PTR_W{1'b0}};
        wr_count    <= {WR_CNT_W{1'b0}};
        lsu_aw_sent <= 1'b0;
        lsu_w_sent  <= 1'b0;
        burst_awlen <= {WR_PTR_W{1'b0}};
        burst_w_idx <= {WR_PTR_W{1'b0}};
        for (wi = 0; wi < WR_FIFO_DEPTH; wi = wi + 1) begin
            wr_addr_mem[wi] <= 32'b0;
            wr_data_mem[wi] <= 32'b0;
            wr_mask_mem[wi] <= 4'b0;
        end
    end else begin
        if (wr_push) begin
            wr_addr_mem[wr_tail] <= lsu_cmd_addr;
            wr_data_mem[wr_tail] <= lsu_cmd_data;
            wr_mask_mem[wr_tail] <= lsu_cmd_mask;
            wr_tail              <= wr_tail + {{(WR_PTR_W-1){1'b0}}, 1'b1};
        end

        // B retires the entire burst — pop burst_awlen+1 entries, reset
        // per-burst state so the next AW handshake re-latches.
        if (wr_pop) begin
            wr_head     <= wr_head + (burst_awlen + {{(WR_PTR_W-1){1'b0}}, 1'b1});
            lsu_aw_sent <= 1'b0;
            lsu_w_sent  <= 1'b0;
            burst_w_idx <= {WR_PTR_W{1'b0}};
        end

        // AW handshake: latch awlen for the W phase + mark sent.
        if (per_awvalid_cpu && per_awready_cpu) begin
            lsu_aw_sent <= 1'b1;
            burst_awlen <= burst_awlen_calc;
        end

        // W beat: advance w_idx, set w_sent on wlast.
        if (per_wvalid_cpu && per_wready_cpu) begin
            if (w_is_last) lsu_w_sent <= 1'b1;
            else           burst_w_idx <= burst_w_idx + {{(WR_PTR_W-1){1'b0}}, 1'b1};
        end

        // FIFO count delta accounts for posted push and burst-sized pop.
        case ({wr_push, wr_pop})
            2'b10: wr_count <= wr_count + 3'd1;
            2'b01: wr_count <= wr_count - {1'b0, burst_awlen} - 3'd1;
            2'b11: wr_count <= wr_count - {1'b0, burst_awlen};
            default: ;
        endcase
    end
end

// AR channel — drive while we have a read in flight but AR not yet accepted.
assign per_arvalid_cpu = lsu_inflight_read & ~lsu_ar_sent;
assign per_araddr_cpu  = lsu_rd_addr;
assign per_arlen_cpu   = 8'd0;
assign per_rready_cpu  = 1'b1;

// AW channel.  awlen + awburst are combinational from the current
// match scan — the slice samples them at the handshake posedge and
// our burst_awlen register latches the same value at the same posedge.
// awburst=FIXED when coalescing keeps req_addr pinned across beats;
// awburst=INCR for single beats so
// non-burst-aware slaves keep their existing behavior.
assign per_awvalid_cpu = !wr_fifo_empty & ~lsu_aw_sent;
assign per_awaddr_cpu  = w_addr;
assign per_awlen_cpu   = {{(8-WR_PTR_W){1'b0}}, burst_awlen_calc};
assign per_awburst_cpu = (burst_awlen_calc != {WR_PTR_W{1'b0}}) ? 2'b00 : 2'b01;
// awallStrb is a side-band hint not consumed by cpu_target_port —
// drive the post-slice wire directly; no slice needed.
assign per_awallStrb   = &w_mask;
assign mem_awallStrb   = 1'b0;

// W channel — beat data from FIFO[head + w_idx]; wlast on the final beat.
assign per_wvalid_cpu  = !wr_fifo_empty & ~lsu_w_sent;
assign per_wdata_cpu   = w_data;
assign per_wstrb_cpu   = w_mask;
assign per_wlast_cpu   = w_is_last;
assign per_bready_cpu  = 1'b1;

// i_axi AW/W/B tie-offs retained for backwards-compatible placeholders.
assign i_awvalid_tie = 1'b0;
assign i_wvalid_tie  = 1'b0;
assign i_bid_tie     = 1'b0;
assign i_bresp_tie   = 2'b00;

VexiiRiscv cpu (
    .clk  (clk),
    .reset(reset),

    // Privileged time counter — tie to zero (we don't use rdtime from the CSR).
    .PrivilegedPlugin_logic_rdtime(64'd0),

    .PrivilegedPlugin_logic_harts_0_int_m_timer   (int_m_timer),
    .PrivilegedPlugin_logic_harts_0_int_m_software(1'b0),
    .PrivilegedPlugin_logic_harts_0_int_m_external(int_m_external),

    // FetchL1Axi4 (I-cache refills, read-only).  All channels routed
    // through axi_register_slice instances below — VexiiRiscv connects
    // to the *_cpu wires; the slice outputs drive i_* (post-slice).
    .FetchL1Axi4Plugin_logic_axi_ar_valid        (i_arvalid_cpu),
    .FetchL1Axi4Plugin_logic_axi_ar_ready        (i_arready_cpu),
    .FetchL1Axi4Plugin_logic_axi_ar_payload_addr (i_araddr_cpu),
    .FetchL1Axi4Plugin_logic_axi_ar_payload_id   (i_arid_cpu),
    .FetchL1Axi4Plugin_logic_axi_ar_payload_len  (i_arlen_cpu),
    .FetchL1Axi4Plugin_logic_axi_ar_payload_size (),
    .FetchL1Axi4Plugin_logic_axi_ar_payload_burst(),
    .FetchL1Axi4Plugin_logic_axi_ar_payload_cache(),
    .FetchL1Axi4Plugin_logic_axi_ar_payload_prot (),
    .FetchL1Axi4Plugin_logic_axi_r_valid         (i_rvalid_cpu),
    .FetchL1Axi4Plugin_logic_axi_r_ready         (i_rready_cpu),
    .FetchL1Axi4Plugin_logic_axi_r_payload_data  (i_rdata_cpu),
    .FetchL1Axi4Plugin_logic_axi_r_payload_id    (i_rid_cpu),
    .FetchL1Axi4Plugin_logic_axi_r_payload_resp  (i_rresp_cpu),
    .FetchL1Axi4Plugin_logic_axi_r_payload_last  (i_rlast_cpu),

    // LsuL1Axi4 (D-cache refills/writebacks + cbo.*) — sliced.
    .LsuL1Axi4Plugin_logic_axi_aw_valid        (mem_awvalid_cpu),
    .LsuL1Axi4Plugin_logic_axi_aw_ready        (mem_awready_cpu),
    .LsuL1Axi4Plugin_logic_axi_aw_payload_addr (mem_awaddr_cpu),
    .LsuL1Axi4Plugin_logic_axi_aw_payload_id   (mem_awid_cpu),
    .LsuL1Axi4Plugin_logic_axi_aw_payload_len  (mem_awlen_cpu),
    .LsuL1Axi4Plugin_logic_axi_aw_payload_size (),
    .LsuL1Axi4Plugin_logic_axi_aw_payload_burst(mem_awburst_cpu),
    .LsuL1Axi4Plugin_logic_axi_aw_payload_cache(),
    .LsuL1Axi4Plugin_logic_axi_aw_payload_prot (),
    .LsuL1Axi4Plugin_logic_axi_w_valid         (mem_wvalid_cpu),
    .LsuL1Axi4Plugin_logic_axi_w_ready         (mem_wready_cpu),
    .LsuL1Axi4Plugin_logic_axi_w_payload_data  (mem_wdata_cpu),
    .LsuL1Axi4Plugin_logic_axi_w_payload_strb  (mem_wstrb_cpu),
    .LsuL1Axi4Plugin_logic_axi_w_payload_last  (mem_wlast_cpu),
    .LsuL1Axi4Plugin_logic_axi_b_valid         (mem_bvalid_cpu),
    .LsuL1Axi4Plugin_logic_axi_b_ready         (mem_bready_cpu),
    .LsuL1Axi4Plugin_logic_axi_b_payload_id    (mem_bid_cpu),
    .LsuL1Axi4Plugin_logic_axi_b_payload_resp  (mem_bresp_cpu),
    .LsuL1Axi4Plugin_logic_axi_ar_valid        (mem_arvalid_cpu),
    .LsuL1Axi4Plugin_logic_axi_ar_ready        (mem_arready_cpu),
    .LsuL1Axi4Plugin_logic_axi_ar_payload_addr (mem_araddr_cpu),
    .LsuL1Axi4Plugin_logic_axi_ar_payload_id   (mem_arid_cpu),
    .LsuL1Axi4Plugin_logic_axi_ar_payload_len  (mem_arlen_cpu),
    .LsuL1Axi4Plugin_logic_axi_ar_payload_size (),
    .LsuL1Axi4Plugin_logic_axi_ar_payload_burst(),
    .LsuL1Axi4Plugin_logic_axi_ar_payload_cache(),
    .LsuL1Axi4Plugin_logic_axi_ar_payload_prot (),
    .LsuL1Axi4Plugin_logic_axi_r_valid         (mem_rvalid_cpu),
    .LsuL1Axi4Plugin_logic_axi_r_ready         (mem_rready_cpu),
    .LsuL1Axi4Plugin_logic_axi_r_payload_data  (mem_rdata_cpu),
    .LsuL1Axi4Plugin_logic_axi_r_payload_id    (mem_rid_cpu),
    .LsuL1Axi4Plugin_logic_axi_r_payload_resp  (mem_rresp_cpu),
    .LsuL1Axi4Plugin_logic_axi_r_payload_last  (mem_rlast_cpu),

    // LsuPlugin native IO bus (goes through the shim above to per_* AXI)
    .LsuPlugin_logic_bus_cmd_valid           (lsu_cmd_valid),
    .LsuPlugin_logic_bus_cmd_ready           (lsu_cmd_ready),
    .LsuPlugin_logic_bus_cmd_payload_write   (lsu_cmd_write),
    .LsuPlugin_logic_bus_cmd_payload_address (lsu_cmd_addr),
    .LsuPlugin_logic_bus_cmd_payload_data    (lsu_cmd_data),
    .LsuPlugin_logic_bus_cmd_payload_size    (),
    .LsuPlugin_logic_bus_cmd_payload_mask    (lsu_cmd_mask),
    .LsuPlugin_logic_bus_cmd_payload_io      (),
    .LsuPlugin_logic_bus_cmd_payload_fromHart(),
    .LsuPlugin_logic_bus_cmd_payload_uopId   (),
    .LsuPlugin_logic_bus_rsp_valid           (lsu_rsp_valid),
    .LsuPlugin_logic_bus_rsp_payload_error   (lsu_rsp_error),
    .LsuPlugin_logic_bus_rsp_payload_data    (lsu_rsp_data)
);

// ============================================================
// AXI register slices (Phase 2)
//
// One slice per AXI channel.  Each bundles the channel's payload
// signals into a single packed bus that the slice carries unchanged
// across a 1-cycle register stage.  Throughput unchanged; latency
// +1 cycle each direction; placement freedom for VexiiRiscv.
// ============================================================

// ---- i_axi (read-only) ----
//   AR payload = {araddr[31:0], arid[0], arlen[7:0]} = 41 bits
axi_register_slice #(.W(41)) i_ar_slice (
    .clk(clk), .reset_n(reset_n),
    .s_valid (i_arvalid_cpu),  .s_ready (i_arready_cpu),
    .s_payload({i_araddr_cpu, i_arid_cpu, i_arlen_cpu}),
    .m_valid (i_arvalid),      .m_ready (i_arready),
    .m_payload({i_araddr,     i_arid,     i_arlen})
);
//   R payload = {rdata[31:0], rid[0], rresp[1:0], rlast} = 36 bits
axi_register_slice #(.W(36)) i_r_slice (
    .clk(clk), .reset_n(reset_n),
    .s_valid (i_rvalid),       .s_ready (i_rready),
    .s_payload({i_rdata,     i_rid,     i_rresp,     i_rlast}),
    .m_valid (i_rvalid_cpu),   .m_ready (i_rready_cpu),
    .m_payload({i_rdata_cpu, i_rid_cpu, i_rresp_cpu, i_rlast_cpu})
);

// ---- mem_axi (D$ R/W) ----
//   AR = {araddr[31:0], arid[1:0], arlen[7:0]} = 42 bits
axi_register_slice #(.W(42)) mem_ar_slice (
    .clk(clk), .reset_n(reset_n),
    .s_valid (mem_arvalid_cpu),.s_ready (mem_arready_cpu),
    .s_payload({mem_araddr_cpu, mem_arid_cpu, mem_arlen_cpu}),
    .m_valid (mem_arvalid),    .m_ready (mem_arready),
    .m_payload({mem_araddr,     mem_arid,     mem_arlen})
);
//   R = {rdata[31:0], rid[1:0], rresp[1:0], rlast} = 37 bits
axi_register_slice #(.W(37)) mem_r_slice (
    .clk(clk), .reset_n(reset_n),
    .s_valid (mem_rvalid),     .s_ready (mem_rready),
    .s_payload({mem_rdata,     mem_rid,     mem_rresp,     mem_rlast}),
    .m_valid (mem_rvalid_cpu), .m_ready (mem_rready_cpu),
    .m_payload({mem_rdata_cpu, mem_rid_cpu, mem_rresp_cpu, mem_rlast_cpu})
);
//   AW = {awaddr[31:0], awid[1:0], awlen[7:0], awburst[1:0]} = 44 bits
axi_register_slice #(.W(44)) mem_aw_slice (
    .clk(clk), .reset_n(reset_n),
    .s_valid (mem_awvalid_cpu),.s_ready (mem_awready_cpu),
    .s_payload({mem_awaddr_cpu, mem_awid_cpu, mem_awlen_cpu, mem_awburst_cpu}),
    .m_valid (mem_awvalid),    .m_ready (mem_awready),
    .m_payload({mem_awaddr,     mem_awid,     mem_awlen,     mem_awburst})
);
//   W = {wdata[31:0], wstrb[3:0], wlast} = 37 bits
axi_register_slice #(.W(37)) mem_w_slice (
    .clk(clk), .reset_n(reset_n),
    .s_valid (mem_wvalid_cpu), .s_ready (mem_wready_cpu),
    .s_payload({mem_wdata_cpu, mem_wstrb_cpu, mem_wlast_cpu}),
    .m_valid (mem_wvalid),     .m_ready (mem_wready),
    .m_payload({mem_wdata,     mem_wstrb,     mem_wlast})
);
//   B = {bid[1:0], bresp[1:0]} = 4 bits
axi_register_slice #(.W(4))  mem_b_slice (
    .clk(clk), .reset_n(reset_n),
    .s_valid (mem_bvalid),     .s_ready (mem_bready),
    .s_payload({mem_bid,     mem_bresp}),
    .m_valid (mem_bvalid_cpu), .m_ready (mem_bready_cpu),
    .m_payload({mem_bid_cpu, mem_bresp_cpu})
);

// ---- per_axi (uncached LSU IO) ----
//   AR = {araddr[31:0], arlen[7:0]} = 40 bits
axi_register_slice #(.W(40)) per_ar_slice (
    .clk(clk), .reset_n(reset_n),
    .s_valid (per_arvalid_cpu),.s_ready (per_arready_cpu),
    .s_payload({per_araddr_cpu, per_arlen_cpu}),
    .m_valid (per_arvalid),    .m_ready (per_arready),
    .m_payload({per_araddr,     per_arlen})
);
//   R = {rdata[31:0], rresp[1:0], rlast} = 35 bits
axi_register_slice #(.W(35)) per_r_slice (
    .clk(clk), .reset_n(reset_n),
    .s_valid (per_rvalid),     .s_ready (per_rready),
    .s_payload({per_rdata,     per_rresp,     per_rlast}),
    .m_valid (per_rvalid_cpu), .m_ready (per_rready_cpu),
    .m_payload({per_rdata_cpu, per_rresp_cpu, per_rlast_cpu})
);
//   AW = {awaddr[31:0], awlen[7:0], awburst[1:0]} = 42 bits
axi_register_slice #(.W(42)) per_aw_slice (
    .clk(clk), .reset_n(reset_n),
    .s_valid (per_awvalid_cpu),.s_ready (per_awready_cpu),
    .s_payload({per_awaddr_cpu, per_awlen_cpu, per_awburst_cpu}),
    .m_valid (per_awvalid),    .m_ready (per_awready),
    .m_payload({per_awaddr,     per_awlen,     per_awburst})
);
//   W = {wdata[31:0], wstrb[3:0], wlast} = 37 bits
axi_register_slice #(.W(37)) per_w_slice (
    .clk(clk), .reset_n(reset_n),
    .s_valid (per_wvalid_cpu), .s_ready (per_wready_cpu),
    .s_payload({per_wdata_cpu, per_wstrb_cpu, per_wlast_cpu}),
    .m_valid (per_wvalid),     .m_ready (per_wready),
    .m_payload({per_wdata,     per_wstrb,     per_wlast})
);
//   B = {bresp[1:0]} = 2 bits
axi_register_slice #(.W(2))  per_b_slice (
    .clk(clk), .reset_n(reset_n),
    .s_valid (per_bvalid),     .s_ready (per_bready),
    .s_payload(per_bresp),
    .m_valid (per_bvalid_cpu), .m_ready (per_bready_cpu),
    .m_payload(per_bresp_cpu)
);

// ============================================================
// Address decode → target select
// ============================================================
// Target IDs.
//   0 — SDRAM   (0x10xxxxxx cached, 0x50xxxxxx uncached alias)
//   1 — PSRAM   (0x30/0x38 CRAM0 staging, 0x31 CRAM1 executable)
//   2 — LOCAL   (BRAM, peripherals, term FB, etc.)
function [2:0] decode_target;
    input [31:0] addr;
    begin
        if (addr[31:26] == 6'b000100 || addr[31:26] == 6'b010100)
            decode_target = 3'd0;   // SDRAM
        else if (addr[31:24] == 8'h30 || addr[31:24] == 8'h31 || addr[31:24] == 8'h38)
            decode_target = 3'd1;   // PSRAM
        else
            decode_target = 3'd2;   // LOCAL
    end
endfunction

wire [2:0] i_ar_target   = decode_target(i_araddr);
wire [2:0] mem_ar_target = decode_target(mem_araddr);
wire [2:0] mem_aw_target = decode_target(mem_awaddr);
wire [2:0] per_ar_target = decode_target(per_araddr);
wire [2:0] per_aw_target = decode_target(per_awaddr);

// Per-(master, target) busy signals (see cpu_target_port.v)
wire sdram_i_rd_busy, sdram_mem_rd_busy, sdram_mem_wr_busy, sdram_per_rd_busy, sdram_per_wr_busy;
wire cram0_i_rd_busy, cram0_mem_rd_busy, cram0_mem_wr_busy, cram0_per_rd_busy, cram0_per_wr_busy;
wire local_i_rd_busy, local_mem_rd_busy, local_mem_wr_busy, local_per_rd_busy, local_per_wr_busy;

// Per-(master, direction) global serialization — keeps responses in-order
// per master even though they may come back from different target ports.
wire global_i_rd_busy   = sdram_i_rd_busy   | cram0_i_rd_busy   | local_i_rd_busy;
wire global_mem_rd_busy = sdram_mem_rd_busy | cram0_mem_rd_busy | local_mem_rd_busy;
wire global_mem_wr_busy = sdram_mem_wr_busy | cram0_mem_wr_busy | local_mem_wr_busy;
wire global_per_rd_busy = sdram_per_rd_busy | cram0_per_rd_busy | local_per_rd_busy;
wire global_per_wr_busy = sdram_per_wr_busy | cram0_per_wr_busy | local_per_wr_busy;

wire i_ar_is_sdram = i_arvalid && (i_ar_target == 3'd0) && !global_i_rd_busy;
wire i_ar_is_cram0 = i_arvalid && (i_ar_target == 3'd1) && !global_i_rd_busy;
wire i_ar_is_local = i_arvalid && (i_ar_target == 3'd2) && !global_i_rd_busy;

wire mem_ar_is_sdram = mem_arvalid && (mem_ar_target == 3'd0) && !global_mem_rd_busy;
wire mem_ar_is_cram0 = mem_arvalid && (mem_ar_target == 3'd1) && !global_mem_rd_busy;
wire mem_ar_is_local = mem_arvalid && (mem_ar_target == 3'd2) && !global_mem_rd_busy;
wire mem_aw_is_sdram = mem_awvalid && (mem_aw_target == 3'd0) && !global_mem_wr_busy;
wire mem_aw_is_cram0 = mem_awvalid && (mem_aw_target == 3'd1) && !global_mem_wr_busy;
wire mem_aw_is_local = mem_awvalid && (mem_aw_target == 3'd2) && !global_mem_wr_busy;

wire per_ar_is_sdram = per_arvalid && (per_ar_target == 3'd0) && !global_per_rd_busy;
wire per_ar_is_cram0 = per_arvalid && (per_ar_target == 3'd1) && !global_per_rd_busy;
wire per_ar_is_local = per_arvalid && (per_ar_target == 3'd2) && !global_per_rd_busy;
wire per_aw_is_sdram = per_awvalid && (per_aw_target == 3'd0) && !global_per_wr_busy;
wire per_aw_is_cram0 = per_awvalid && (per_aw_target == 3'd1) && !global_per_wr_busy;
wire per_aw_is_local = per_awvalid && (per_aw_target == 3'd2) && !global_per_wr_busy;

// ============================================================
// Per-target contribution wires
// ============================================================
// SDRAM
wire        sdram_i_arready,   sdram_mem_arready,   sdram_per_arready;
wire        sdram_i_rvalid;    wire [31:0] sdram_i_rdata;   wire [0:0] sdram_i_rid;   wire [1:0] sdram_i_rresp;   wire sdram_i_rlast;
wire        sdram_mem_rvalid;  wire [31:0] sdram_mem_rdata; wire [1:0] sdram_mem_rid; wire [1:0] sdram_mem_rresp; wire sdram_mem_rlast;
wire        sdram_per_rvalid;  wire [31:0] sdram_per_rdata; wire [1:0] sdram_per_rresp; wire sdram_per_rlast;
wire        sdram_mem_awready, sdram_per_awready;
wire        sdram_mem_wready,  sdram_per_wready;
wire        sdram_mem_bvalid;  wire [1:0]  sdram_mem_bid;   wire [1:0] sdram_mem_bresp;
wire        sdram_per_bvalid;  wire [1:0]  sdram_per_bresp;

// PSRAM target port.  core_top demuxes 0x30/0x38 to CRAM0 and 0x31 to CRAM1.
wire        cram0_i_arready,   cram0_mem_arready,   cram0_per_arready;
wire        cram0_i_rvalid;    wire [31:0] cram0_i_rdata;   wire [0:0] cram0_i_rid;   wire [1:0] cram0_i_rresp;   wire cram0_i_rlast;
wire        cram0_mem_rvalid;  wire [31:0] cram0_mem_rdata; wire [1:0] cram0_mem_rid; wire [1:0] cram0_mem_rresp; wire cram0_mem_rlast;
wire        cram0_per_rvalid;  wire [31:0] cram0_per_rdata; wire [1:0] cram0_per_rresp; wire cram0_per_rlast;
wire        cram0_mem_awready, cram0_per_awready;
wire        cram0_mem_wready,  cram0_per_wready;
wire        cram0_mem_bvalid;  wire [1:0]  cram0_mem_bid;   wire [1:0] cram0_mem_bresp;
wire        cram0_per_bvalid;  wire [1:0]  cram0_per_bresp;

// LOCAL
wire        local_i_arready,   local_mem_arready,   local_per_arready;
wire        local_i_rvalid;    wire [31:0] local_i_rdata;   wire [0:0] local_i_rid;   wire [1:0] local_i_rresp;   wire local_i_rlast;
wire        local_mem_rvalid;  wire [31:0] local_mem_rdata; wire [1:0] local_mem_rid; wire [1:0] local_mem_rresp; wire local_mem_rlast;
wire        local_per_rvalid;  wire [31:0] local_per_rdata; wire [1:0] local_per_rresp; wire local_per_rlast;
wire        local_mem_awready, local_per_awready;
wire        local_mem_wready,  local_per_wready;
wire        local_mem_bvalid;  wire [1:0]  local_mem_bid;   wire [1:0] local_mem_bresp;
wire        local_per_bvalid;  wire [1:0]  local_per_bresp;

// ============================================================
// SDRAM target port
// ============================================================
cpu_target_port port_sdram (
    .clk    (clk),
    .reset_n(reset_n),

    .i_rd_select  (i_ar_is_sdram),
    .mem_rd_select(mem_ar_is_sdram),
    .per_rd_select(per_ar_is_sdram),
    .mem_wr_select(mem_aw_is_sdram),
    .per_wr_select(per_aw_is_sdram),

    .i_arvalid         (i_arvalid),
    .i_arready_contrib (sdram_i_arready),
    .i_araddr          (i_araddr),
    .i_arid            (i_arid),
    .i_arlen           (i_arlen),
    .i_rvalid_contrib  (sdram_i_rvalid),
    .i_rdata_contrib   (sdram_i_rdata),
    .i_rid_contrib     (sdram_i_rid),
    .i_rresp_contrib   (sdram_i_rresp),
    .i_rlast_contrib   (sdram_i_rlast),
    .i_rready          (i_rready),

    .mem_arvalid         (mem_arvalid),
    .mem_arready_contrib (sdram_mem_arready),
    .mem_araddr          (mem_araddr),
    .mem_arid            (mem_arid),
    .mem_arlen           (mem_arlen),
    .mem_rvalid_contrib  (sdram_mem_rvalid),
    .mem_rdata_contrib   (sdram_mem_rdata),
    .mem_rid_contrib     (sdram_mem_rid),
    .mem_rresp_contrib   (sdram_mem_rresp),
    .mem_rlast_contrib   (sdram_mem_rlast),
    .mem_rready          (mem_rready),
    .mem_awvalid         (mem_awvalid),
    .mem_awready_contrib (sdram_mem_awready),
    .mem_awaddr          (mem_awaddr),
    .mem_awid            (mem_awid),
    .mem_awlen            (mem_awlen),
    .mem_awburst         (mem_awburst),
    .mem_wvalid          (mem_wvalid),
    .mem_wready_contrib  (sdram_mem_wready),
    .mem_wdata           (mem_wdata),
    .mem_wstrb           (mem_wstrb),
    .mem_wlast           (mem_wlast),
    .mem_bvalid_contrib  (sdram_mem_bvalid),
    .mem_bid_contrib     (sdram_mem_bid),
    .mem_bresp_contrib   (sdram_mem_bresp),
    .mem_bready          (mem_bready),

    .per_arvalid         (per_arvalid),
    .per_arready_contrib (sdram_per_arready),
    .per_araddr          (per_araddr),
    .per_arlen           (per_arlen),
    .per_rvalid_contrib  (sdram_per_rvalid),
    .per_rdata_contrib   (sdram_per_rdata),
    .per_rresp_contrib   (sdram_per_rresp),
    .per_rlast_contrib   (sdram_per_rlast),
    .per_rready          (per_rready),
    .per_awvalid         (per_awvalid),
    .per_awready_contrib (sdram_per_awready),
    .per_awaddr          (per_awaddr),
    .per_awlen           (per_awlen),
    .per_awburst         (per_awburst),
    .per_wvalid          (per_wvalid),
    .per_wready_contrib  (sdram_per_wready),
    .per_wdata           (per_wdata),
    .per_wstrb           (per_wstrb),
    .per_wlast           (per_wlast),
    .per_bvalid_contrib  (sdram_per_bvalid),
    .per_bresp_contrib   (sdram_per_bresp),
    .per_bready          (per_bready),

    .m_arvalid (m_sdram_arvalid),
    .m_arready (m_sdram_arready),
    .m_araddr  (m_sdram_araddr),
    .m_arlen   (m_sdram_arlen),
    .m_rvalid  (m_sdram_rvalid),
    .m_rready  (m_sdram_rready),
    .m_rdata   (m_sdram_rdata),
    .m_rresp   (m_sdram_rresp),
    .m_rlast   (m_sdram_rlast),
    .m_awvalid (m_sdram_awvalid),
    .m_awready (m_sdram_awready),
    .m_awaddr  (m_sdram_awaddr),
    .m_awlen   (m_sdram_awlen),
    .m_awburst (),                  // SDRAM slave doesn't consume awburst (INCR-only)
    .m_wvalid  (m_sdram_wvalid),
    .m_wready  (m_sdram_wready),
    .m_wdata   (m_sdram_wdata),
    .m_wstrb   (m_sdram_wstrb),
    .m_wlast   (m_sdram_wlast),
    .m_bvalid  (m_sdram_bvalid),
    .m_bresp   (m_sdram_bresp),

    .i_rd_busy  (sdram_i_rd_busy),
    .mem_rd_busy(sdram_mem_rd_busy),
    .mem_wr_busy(sdram_mem_wr_busy),
    .per_rd_busy(sdram_per_rd_busy),
    .per_wr_busy(sdram_per_wr_busy)
);

// ============================================================
// PSRAM target port (CRAM0 + CRAM1)
// ============================================================
cpu_target_port port_cram0 (
    .clk    (clk),
    .reset_n(reset_n),

    .i_rd_select  (i_ar_is_cram0),
    .mem_rd_select(mem_ar_is_cram0),
    .per_rd_select(per_ar_is_cram0),
    .mem_wr_select(mem_aw_is_cram0),
    .per_wr_select(per_aw_is_cram0),

    .i_arvalid         (i_arvalid),
    .i_arready_contrib (cram0_i_arready),
    .i_araddr          (i_araddr),
    .i_arid            (i_arid),
    .i_arlen           (i_arlen),
    .i_rvalid_contrib  (cram0_i_rvalid),
    .i_rdata_contrib   (cram0_i_rdata),
    .i_rid_contrib     (cram0_i_rid),
    .i_rresp_contrib   (cram0_i_rresp),
    .i_rlast_contrib   (cram0_i_rlast),
    .i_rready          (i_rready),

    .mem_arvalid         (mem_arvalid),
    .mem_arready_contrib (cram0_mem_arready),
    .mem_araddr          (mem_araddr),
    .mem_arid            (mem_arid),
    .mem_arlen           (mem_arlen),
    .mem_rvalid_contrib  (cram0_mem_rvalid),
    .mem_rdata_contrib   (cram0_mem_rdata),
    .mem_rid_contrib     (cram0_mem_rid),
    .mem_rresp_contrib   (cram0_mem_rresp),
    .mem_rlast_contrib   (cram0_mem_rlast),
    .mem_rready          (mem_rready),
    .mem_awvalid         (mem_awvalid),
    .mem_awready_contrib (cram0_mem_awready),
    .mem_awaddr          (mem_awaddr),
    .mem_awid            (mem_awid),
    .mem_awlen           (mem_awlen),
    .mem_awburst         (mem_awburst),
    .mem_wvalid          (mem_wvalid),
    .mem_wready_contrib  (cram0_mem_wready),
    .mem_wdata           (mem_wdata),
    .mem_wstrb           (mem_wstrb),
    .mem_wlast           (mem_wlast),
    .mem_bvalid_contrib  (cram0_mem_bvalid),
    .mem_bid_contrib     (cram0_mem_bid),
    .mem_bresp_contrib   (cram0_mem_bresp),
    .mem_bready          (mem_bready),

    .per_arvalid         (per_arvalid),
    .per_arready_contrib (cram0_per_arready),
    .per_araddr          (per_araddr),
    .per_arlen           (per_arlen),
    .per_rvalid_contrib  (cram0_per_rvalid),
    .per_rdata_contrib   (cram0_per_rdata),
    .per_rresp_contrib   (cram0_per_rresp),
    .per_rlast_contrib   (cram0_per_rlast),
    .per_rready          (per_rready),
    .per_awvalid         (per_awvalid),
    .per_awready_contrib (cram0_per_awready),
    .per_awaddr          (per_awaddr),
    .per_awlen           (per_awlen),
    .per_awburst         (per_awburst),
    .per_wvalid          (per_wvalid),
    .per_wready_contrib  (cram0_per_wready),
    .per_wdata           (per_wdata),
    .per_wstrb           (per_wstrb),
    .per_wlast           (per_wlast),
    .per_bvalid_contrib  (cram0_per_bvalid),
    .per_bresp_contrib   (cram0_per_bresp),
    .per_bready          (per_bready),

    .m_arvalid (m_cram0_arvalid),
    .m_arready (m_cram0_arready),
    .m_araddr  (m_cram0_araddr),
    .m_arlen   (m_cram0_arlen),
    .m_rvalid  (m_cram0_rvalid),
    .m_rready  (m_cram0_rready),
    .m_rdata   (m_cram0_rdata),
    .m_rresp   (m_cram0_rresp),
    .m_rlast   (m_cram0_rlast),
    .m_awvalid (m_cram0_awvalid),
    .m_awready (m_cram0_awready),
    .m_awaddr  (m_cram0_awaddr),
    .m_awlen   (m_cram0_awlen),
    .m_awburst (),                  // PSRAM slaves consume INCR order only
    .m_wvalid  (m_cram0_wvalid),
    .m_wready  (m_cram0_wready),
    .m_wdata   (m_cram0_wdata),
    .m_wstrb   (m_cram0_wstrb),
    .m_wlast   (m_cram0_wlast),
    .m_bvalid  (m_cram0_bvalid),
    .m_bresp   (m_cram0_bresp),

    .i_rd_busy  (cram0_i_rd_busy),
    .mem_rd_busy(cram0_mem_rd_busy),
    .mem_wr_busy(cram0_mem_wr_busy),
    .per_rd_busy(cram0_per_rd_busy),
    .per_wr_busy(cram0_per_wr_busy)
);

// ============================================================
// LOCAL target port
// ============================================================
cpu_target_port port_local (
    .clk    (clk),
    .reset_n(reset_n),

    .i_rd_select  (i_ar_is_local),
    .mem_rd_select(mem_ar_is_local),
    .per_rd_select(per_ar_is_local),
    .mem_wr_select(mem_aw_is_local),
    .per_wr_select(per_aw_is_local),

    .i_arvalid         (i_arvalid),
    .i_arready_contrib (local_i_arready),
    .i_araddr          (i_araddr),
    .i_arid            (i_arid),
    .i_arlen           (i_arlen),
    .i_rvalid_contrib  (local_i_rvalid),
    .i_rdata_contrib   (local_i_rdata),
    .i_rid_contrib     (local_i_rid),
    .i_rresp_contrib   (local_i_rresp),
    .i_rlast_contrib   (local_i_rlast),
    .i_rready          (i_rready),

    .mem_arvalid         (mem_arvalid),
    .mem_arready_contrib (local_mem_arready),
    .mem_araddr          (mem_araddr),
    .mem_arid            (mem_arid),
    .mem_arlen           (mem_arlen),
    .mem_rvalid_contrib  (local_mem_rvalid),
    .mem_rdata_contrib   (local_mem_rdata),
    .mem_rid_contrib     (local_mem_rid),
    .mem_rresp_contrib   (local_mem_rresp),
    .mem_rlast_contrib   (local_mem_rlast),
    .mem_rready          (mem_rready),
    .mem_awvalid         (mem_awvalid),
    .mem_awready_contrib (local_mem_awready),
    .mem_awaddr          (mem_awaddr),
    .mem_awid            (mem_awid),
    .mem_awlen           (mem_awlen),
    .mem_awburst         (mem_awburst),
    .mem_wvalid          (mem_wvalid),
    .mem_wready_contrib  (local_mem_wready),
    .mem_wdata           (mem_wdata),
    .mem_wstrb           (mem_wstrb),
    .mem_wlast           (mem_wlast),
    .mem_bvalid_contrib  (local_mem_bvalid),
    .mem_bid_contrib     (local_mem_bid),
    .mem_bresp_contrib   (local_mem_bresp),
    .mem_bready          (mem_bready),

    .per_arvalid         (per_arvalid),
    .per_arready_contrib (local_per_arready),
    .per_araddr          (per_araddr),
    .per_arlen           (per_arlen),
    .per_rvalid_contrib  (local_per_rvalid),
    .per_rdata_contrib   (local_per_rdata),
    .per_rresp_contrib   (local_per_rresp),
    .per_rlast_contrib   (local_per_rlast),
    .per_rready          (per_rready),
    .per_awvalid         (per_awvalid),
    .per_awready_contrib (local_per_awready),
    .per_awaddr          (per_awaddr),
    .per_awlen           (per_awlen),
    .per_awburst         (per_awburst),
    .per_wvalid          (per_wvalid),
    .per_wready_contrib  (local_per_wready),
    .per_wdata           (per_wdata),
    .per_wstrb           (per_wstrb),
    .per_wlast           (per_wlast),
    .per_bvalid_contrib  (local_per_bvalid),
    .per_bresp_contrib   (local_per_bresp),
    .per_bready          (per_bready),

    .m_arvalid (m_local_arvalid),
    .m_arready (m_local_arready),
    .m_araddr  (m_local_araddr),
    .m_arlen   (m_local_arlen),
    .m_rvalid  (m_local_rvalid),
    .m_rready  (m_local_rready),
    .m_rdata   (m_local_rdata),
    .m_rresp   (m_local_rresp),
    .m_rlast   (m_local_rlast),
    .m_awvalid (m_local_awvalid),
    .m_awready (m_local_awready),
    .m_awaddr  (m_local_awaddr),
    .m_awlen   (m_local_awlen),
    .m_awburst (m_local_awburst),    // forwarded to axi_periph_slave at the top level
    .m_wvalid  (m_local_wvalid),
    .m_wready  (m_local_wready),
    .m_wdata   (m_local_wdata),
    .m_wstrb   (m_local_wstrb),
    .m_wlast   (m_local_wlast),
    .m_bvalid  (m_local_bvalid),
    .m_bresp   (m_local_bresp),

    .i_rd_busy  (local_i_rd_busy),
    .mem_rd_busy(local_mem_rd_busy),
    .mem_wr_busy(local_mem_wr_busy),
    .per_rd_busy(local_per_rd_busy),
    .per_wr_busy(local_per_wr_busy)
);

// ============================================================
// Master-facing aggregations (OR across target ports)
// ============================================================
// At most one target drives any given signal high because the *_select
// inputs are mutually exclusive per master (address decode picks exactly
// one target).

assign i_arready   = sdram_i_arready   | cram0_i_arready   | local_i_arready;
assign mem_arready = sdram_mem_arready | cram0_mem_arready | local_mem_arready;
assign per_arready = sdram_per_arready | cram0_per_arready | local_per_arready;
assign mem_awready = sdram_mem_awready | cram0_mem_awready | local_mem_awready;
assign per_awready = sdram_per_awready | cram0_per_awready | local_per_awready;
assign mem_wready  = sdram_mem_wready  | cram0_mem_wready  | local_mem_wready;
assign per_wready  = sdram_per_wready  | cram0_per_wready  | local_per_wready;

// i R channel
assign i_rvalid = sdram_i_rvalid | cram0_i_rvalid | local_i_rvalid;
assign i_rdata  = sdram_i_rvalid ? sdram_i_rdata
                : cram0_i_rvalid ? cram0_i_rdata
                : local_i_rdata;
assign i_rid    = sdram_i_rvalid ? sdram_i_rid
                : cram0_i_rvalid ? cram0_i_rid
                : local_i_rid;
assign i_rresp  = sdram_i_rvalid ? sdram_i_rresp
                : cram0_i_rvalid ? cram0_i_rresp
                : local_i_rresp;
assign i_rlast  = sdram_i_rvalid ? sdram_i_rlast
                : cram0_i_rvalid ? cram0_i_rlast
                : local_i_rlast;

// mem R channel
assign mem_rvalid = sdram_mem_rvalid | cram0_mem_rvalid | local_mem_rvalid;
assign mem_rdata  = sdram_mem_rvalid ? sdram_mem_rdata
                  : cram0_mem_rvalid ? cram0_mem_rdata
                  : local_mem_rdata;
assign mem_rid    = sdram_mem_rvalid ? sdram_mem_rid
                  : cram0_mem_rvalid ? cram0_mem_rid
                  : local_mem_rid;
assign mem_rresp  = sdram_mem_rvalid ? sdram_mem_rresp
                  : cram0_mem_rvalid ? cram0_mem_rresp
                  : local_mem_rresp;
assign mem_rlast  = sdram_mem_rvalid ? sdram_mem_rlast
                  : cram0_mem_rvalid ? cram0_mem_rlast
                  : local_mem_rlast;

// per R channel
assign per_rvalid = sdram_per_rvalid | cram0_per_rvalid | local_per_rvalid;
assign per_rdata  = sdram_per_rvalid ? sdram_per_rdata
                  : cram0_per_rvalid ? cram0_per_rdata
                  : local_per_rdata;
assign per_rresp  = sdram_per_rvalid ? sdram_per_rresp
                  : cram0_per_rvalid ? cram0_per_rresp
                  : local_per_rresp;
assign per_rlast  = sdram_per_rvalid ? sdram_per_rlast
                  : cram0_per_rvalid ? cram0_per_rlast
                  : local_per_rlast;

// mem B channel
assign mem_bvalid = sdram_mem_bvalid | cram0_mem_bvalid | local_mem_bvalid;
assign mem_bid    = sdram_mem_bvalid ? sdram_mem_bid
                  : cram0_mem_bvalid ? cram0_mem_bid
                  : local_mem_bid;
assign mem_bresp  = sdram_mem_bvalid ? sdram_mem_bresp
                  : cram0_mem_bvalid ? cram0_mem_bresp
                  : local_mem_bresp;

// per B channel
assign per_bvalid = sdram_per_bvalid | cram0_per_bvalid | local_per_bvalid;
assign per_bresp  = sdram_per_bvalid ? sdram_per_bresp
                  : cram0_per_bvalid ? cram0_per_bresp
                  : local_per_bresp;

endmodule
