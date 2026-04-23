//
// AXI4 SDRAM Arbiter — 4 Masters, Fixed Priority (v2).
//
// Routes 4 AXI4 masters to 1 AXI4 slave (axi_sdram_slave):
//   M0 = GPU      (span reads + framebuffer writes, merged upstream)
//   M1 = CPU      (i + d + p merged through cpu_target_port)
//   M2 = retired  (was AudioDMA; tied off at core_top — HW mixer now
//                  drives audio directly).  Slot kept for arbiter ABI
//                  stability; a future pass can drop it to 3 masters.
//   M3 = AudioMix (audio_mixer per-voice sample fetch, read-only)
//
// Fixed priority: GPU > CPU > (M2 unused) > AudioMix.
//
// CPU fairness counter (unchanged): any GPU/audio grant while the CPU
// has a pending request increments a deficit; once it reaches
// CPU_FAIR_THRESHOLD the CPU is granted unconditionally.  Audio is
// BELOW CPU in priority, so it never contributes to the deficit —
// matching the old 5-master layout where audio also sat below CPU.
//
// Single outstanding transaction — grants one master at a time, holds
// until read completes (R.rlast) or write completes (B.bvalid).
//
// v2 changes vs the 5-master arbiter:
//   - Dropped M0/M1/M3 (legacy GPU-read + GPU-write + bridge) — GPU now
//     exposes a single merged AXI master, bridge no longer touches SDRAM.
//   - Renumbered: old M2 (CPU) → M1, old M4 (audio) → M2.
//

`default_nettype none

module axi_sdram_arbiter #(
    parameter [3:0] CPU_FAIR_THRESHOLD = 4'd8  // Force CPU grant after this many non-CPU grants
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

    // Master 2: Audio DMA (read-only, third priority)
    input  wire        m2_arvalid,
    output wire        m2_arready,
    input  wire [31:0] m2_araddr,
    input  wire [7:0]  m2_arlen,
    output wire        m2_rvalid,
    output wire [31:0] m2_rdata,
    output wire [1:0]  m2_rresp,
    output wire        m2_rlast,

    // Master 3: Audio Mixer (read-only, lowest priority)
    input  wire        m3_arvalid,
    output wire        m3_arready,
    input  wire [31:0] m3_araddr,
    input  wire [7:0]  m3_arlen,
    output wire        m3_rvalid,
    output wire [31:0] m3_rdata,
    output wire [1:0]  m3_rresp,
    output wire        m3_rlast,

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
    input  wire [1:0]  s_bresp
);

wire reset = ~reset_n;

// Arbiter states
localparam ST_IDLE = 2'd0;
localparam ST_RD   = 2'd1;  // Read transaction active (AR→R)
localparam ST_WR   = 2'd2;  // Write transaction active (AW→W→B)

reg [1:0] arb_state;
reg [1:0] grant;  // 0=GPU, 1=CPU, 2=AudioDMA, 3=AudioMix

// Fairness: deficit counter prevents GPU (and technically Audio, but
// it's below CPU anyway) from starving the CPU.  Increments each time
// a non-CPU master wins while the CPU has a pending request.  When it
// reaches the threshold, the CPU is granted unconditionally.  Resets
// when the CPU gets a grant or has no pending request.
reg [3:0] gpu_deficit;
wire cpu_pending = m1_arvalid | m1_awvalid;

// Grant arbitration — registered for timing
always @(posedge clk or posedge reset) begin
    if (reset) begin
        arb_state   <= ST_IDLE;
        grant       <= 2'd0;
        gpu_deficit <= 0;
    end else begin
        case (arb_state)
        ST_IDLE: begin
            // Fairness override: force a CPU grant after too many
            // non-CPU grants while CPU was waiting.
            if (cpu_pending && gpu_deficit >= CPU_FAIR_THRESHOLD) begin
                grant <= 2'd1;
                gpu_deficit <= 0;
                if (m1_arvalid)
                    arb_state <= ST_RD;
                else
                    arb_state <= ST_WR;
            end
            // Priority: GPU (M0) > CPU (M1) > Audio (M2)
            else if (m0_arvalid) begin
                grant <= 2'd0;
                arb_state <= ST_RD;
                if (cpu_pending) gpu_deficit <= gpu_deficit + 4'd1;
            end else if (m0_awvalid) begin
                grant <= 2'd0;
                arb_state <= ST_WR;
                if (cpu_pending) gpu_deficit <= gpu_deficit + 4'd1;
            end else if (m1_arvalid) begin
                grant <= 2'd1;
                arb_state <= ST_RD;
                gpu_deficit <= 0;
            end else if (m1_awvalid) begin
                grant <= 2'd1;
                arb_state <= ST_WR;
                gpu_deficit <= 0;
            end else if (m2_arvalid) begin
                /* Audio DMA gets whatever's left after everyone else has
                 * had their turn.  Never touches gpu_deficit — this
                 * master is below CPU so it cannot starve the CPU. */
                grant <= 2'd2;
                arb_state <= ST_RD;
            end else if (m3_arvalid) begin
                /* Audio Mixer — same rationale as M2, still below CPU. */
                grant <= 2'd3;
                arb_state <= ST_RD;
            end else begin
                // No requests pending — reset deficit
                gpu_deficit <= 0;
            end
        end

        ST_RD: begin
            // Release grant when last R beat is transferred
            if (s_rvalid && s_rlast)
                arb_state <= ST_IDLE;
        end

        ST_WR: begin
            // Release grant when B response is transferred
            if (s_bvalid)
                arb_state <= ST_IDLE;
        end

        default: arb_state <= ST_IDLE;
        endcase
    end
end

// ============================================
// Master → Slave channel mux (combinational)
// ============================================
wire grant_m0 = (grant == 2'd0);
wire grant_m1 = (grant == 2'd1);
wire grant_m2 = (grant == 2'd2);
wire grant_m3 = (grant == 2'd3);
wire active_rd = (arb_state == ST_RD);
wire active_wr = (arb_state == ST_WR);

// Completion guards: on the cycle bvalid/rlast fires, the slave
// returns to S_IDLE while the arbiter is still in ST_WR/ST_RD
// (registered transition).  Without masking, the slave would see
// the next master request through the mux and accept it before the
// arbiter returns to ST_IDLE — causing a duplicate transaction.
wire rd_completing = active_rd && s_rvalid && s_rlast;
wire wr_completing = active_wr && s_bvalid;

// AR channel — masked on rlast.  M2 (audio DMA) and M3 (audio mixer)
// are read-only; routed here.
assign s_arvalid = (active_rd && !rd_completing) ? (grant_m0 ? m0_arvalid :
                                                     grant_m1 ? m1_arvalid :
                                                     grant_m2 ? m2_arvalid :
                                                                m3_arvalid) : 1'b0;
assign s_araddr  = grant_m0 ? m0_araddr  :
                   grant_m1 ? m1_araddr  :
                   grant_m2 ? m2_araddr  : m3_araddr;
assign s_arlen   = grant_m0 ? m0_arlen   :
                   grant_m1 ? m1_arlen   :
                   grant_m2 ? m2_arlen   : m3_arlen;

// AW channel — masked on bvalid.  M2 never writes.
assign s_awvalid = (active_wr && !wr_completing) ? (grant_m0 ? m0_awvalid :
                                                                m1_awvalid) : 1'b0;
assign s_awaddr  = grant_m0 ? m0_awaddr  : m1_awaddr;
assign s_awlen   = grant_m0 ? m0_awlen   : m1_awlen;

// W channel — masked on bvalid
assign s_wvalid  = (active_wr && !wr_completing) ? (grant_m0 ? m0_wvalid :
                                                                m1_wvalid) : 1'b0;
assign s_wdata   = grant_m0 ? m0_wdata  : m1_wdata;
assign s_wstrb   = grant_m0 ? m0_wstrb  : m1_wstrb;
assign s_wlast   = grant_m0 ? m0_wlast  : m1_wlast;

// ============================================
// Slave → Master channel demux (combinational)
// ============================================

// AR ready — only to granted master during read
assign m0_arready = (active_rd && grant_m0) ? s_arready : 1'b0;
assign m1_arready = (active_rd && grant_m1) ? s_arready : 1'b0;
assign m2_arready = (active_rd && grant_m2) ? s_arready : 1'b0;
assign m3_arready = (active_rd && grant_m3) ? s_arready : 1'b0;

// R channel — only to granted master during read
assign m0_rvalid = (active_rd && grant_m0) ? s_rvalid : 1'b0;
assign m1_rvalid = (active_rd && grant_m1) ? s_rvalid : 1'b0;
assign m2_rvalid = (active_rd && grant_m2) ? s_rvalid : 1'b0;
assign m3_rvalid = (active_rd && grant_m3) ? s_rvalid : 1'b0;

// Upstream rready — mux from granted master.  GPU (m0), audio DMA (m2),
// and audio mixer (m3) don't back-pressure so they default to 1; CPU
// (m1) drives m1_rready via cpu_target_port's registered response slot.
assign s_rready = active_rd ? (grant_m1 ? m1_rready : 1'b1) : 1'b1;

assign m0_rdata  = s_rdata;  // Broadcast data (only valid matters)
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

// AW ready — only to granted master during write (M2 never writes)
assign m0_awready = (active_wr && grant_m0) ? s_awready : 1'b0;
assign m1_awready = (active_wr && grant_m1) ? s_awready : 1'b0;

// W ready — only to granted master during write
assign m0_wready = (active_wr && grant_m0) ? s_wready : 1'b0;
assign m1_wready = (active_wr && grant_m1) ? s_wready : 1'b0;

// B channel — only to granted master during write
assign m0_bvalid = (active_wr && grant_m0) ? s_bvalid : 1'b0;
assign m1_bvalid = (active_wr && grant_m1) ? s_bvalid : 1'b0;
assign m0_bresp  = s_bresp;
assign m1_bresp  = s_bresp;

endmodule
