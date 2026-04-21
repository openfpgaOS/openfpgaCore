//
// AXI4 SDRAM Arbiter — 5 Masters, Fixed Priority
//
// Routes 5 AXI4 masters to 1 AXI4 slave (axi_sdram_slave).
// Fixed priority: M0 (Span) > M1 (GPU W/DMA) > M3 (Bridge) > M2 (CPU) > M4 (Audio).
//
// M4 (audio_dma) is *lowest* priority.  The DMA ring is sized for
// ~85 ms of buffered audio, so even long gaps in bus availability
// are absorbed without glitching.  Putting audio above CPU choked
// Doom: every audio burst cost the CPU an arbitration round-trip,
// and audio's 6000 bursts/sec × ~30 cycles compounded into a
// measurable rendering slowdown even though the raw bandwidth was
// <1 %.  Lowest priority gives audio scraps only — which is plenty —
// and keeps the CPU first in line for its own fetches.
//
// Bridge (M3) gets higher priority than CPU so the APF bridge can
// read back nonvolatile save data from SDRAM at shutdown/flush without
// being starved.  Bridge is idle during normal operation so this has
// zero performance impact on the CPU.
//
// CPU fairness: any GPU/bridge grant while CPU has a pending request
// increments a deficit counter; once it reaches CPU_FAIR_THRESHOLD
// the CPU is granted unconditionally.  M4 is below CPU so it never
// contributes to the deficit.
//
// Single outstanding transaction — grants one master at a time, holds
// until read completes (R.rlast) or write completes (B.bvalid).
//
// Pure register/LUT — 0 M10K.
//

`default_nettype none

module axi_sdram_arbiter #(
    parameter [3:0] CPU_FAIR_THRESHOLD = 4'd8  // Force CPU grant after this many non-CPU grants
) (
    input wire clk,
    input wire reset_n,

    // Master 0: Span Rasterizer
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

    // Master 1: DMA Clear/Blit
    input  wire        m1_arvalid,
    output wire        m1_arready,
    input  wire [31:0] m1_araddr,
    input  wire [7:0]  m1_arlen,
    output wire        m1_rvalid,
    output wire [31:0] m1_rdata,
    output wire [1:0]  m1_rresp,
    output wire        m1_rlast,
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

    // Master 2: CPU
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

    // Master 3: Bridge
    input  wire        m3_arvalid,
    output wire        m3_arready,
    input  wire [31:0] m3_araddr,
    input  wire [7:0]  m3_arlen,
    output wire        m3_rvalid,
    output wire [31:0] m3_rdata,
    output wire [1:0]  m3_rresp,
    output wire        m3_rlast,
    input  wire        m3_awvalid,
    output wire        m3_awready,
    input  wire [31:0] m3_awaddr,
    input  wire [7:0]  m3_awlen,
    input  wire        m3_wvalid,
    output wire        m3_wready,
    input  wire [31:0] m3_wdata,
    input  wire [3:0]  m3_wstrb,
    input  wire        m3_wlast,
    output wire        m3_bvalid,
    output wire [1:0]  m3_bresp,

    // Master 4: Audio DMA (read-only, highest priority)
    input  wire        m4_arvalid,
    output wire        m4_arready,
    input  wire [31:0] m4_araddr,
    input  wire [7:0]  m4_arlen,
    output wire        m4_rvalid,
    output wire [31:0] m4_rdata,
    output wire [1:0]  m4_rresp,
    output wire        m4_rlast,

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
reg [2:0] grant;  // 0=M0(Span), 1=M1(DMA), 2=M2(CPU), 3=M3(Bridge), 4=M4(Audio)

// Fairness: deficit counter prevents higher-priority masters from
// starving the CPU.  Increments each time any non-CPU master
// (M0/M1/M3/M4) wins while the CPU has a pending request.  When it
// reaches the threshold, the CPU is granted unconditionally.
// Resets when the CPU gets a grant or has no pending request.
reg [3:0] gpu_deficit;
wire cpu_pending = m2_arvalid | m2_awvalid;

// Grant arbitration — registered for timing
always @(posedge clk or posedge reset) begin
    if (reset) begin
        arb_state <= ST_IDLE;
        grant <= 3'd0;
        gpu_deficit <= 0;
    end else begin
        case (arb_state)
        ST_IDLE: begin
            // Fairness override: if a non-CPU master has won too many
            // times while CPU was waiting, force a CPU grant.
            if (cpu_pending && gpu_deficit >= CPU_FAIR_THRESHOLD) begin
                grant <= 3'd2;
                gpu_deficit <= 0;
                if (m2_arvalid)
                    arb_state <= ST_RD;
                else
                    arb_state <= ST_WR;
            end
            // Priority: M0 > M1 > M3 > M2 (CPU) > M4 (Audio)
            else if (m0_arvalid) begin
                grant <= 3'd0;
                arb_state <= ST_RD;
                if (cpu_pending) gpu_deficit <= gpu_deficit + 4'd1;
            end else if (m0_awvalid) begin
                grant <= 3'd0;
                arb_state <= ST_WR;
                if (cpu_pending) gpu_deficit <= gpu_deficit + 4'd1;
            end else if (m1_arvalid) begin
                grant <= 3'd1;
                arb_state <= ST_RD;
                if (cpu_pending) gpu_deficit <= gpu_deficit + 4'd1;
            end else if (m1_awvalid) begin
                grant <= 3'd1;
                arb_state <= ST_WR;
                if (cpu_pending) gpu_deficit <= gpu_deficit + 4'd1;
            end else if (m3_arvalid) begin
                grant <= 3'd3;
                arb_state <= ST_RD;
                if (cpu_pending) gpu_deficit <= gpu_deficit + 4'd1;
            end else if (m3_awvalid) begin
                grant <= 3'd3;
                arb_state <= ST_WR;
                if (cpu_pending) gpu_deficit <= gpu_deficit + 4'd1;
            end else if (m2_arvalid) begin
                grant <= 3'd2;
                arb_state <= ST_RD;
                gpu_deficit <= 0;
            end else if (m2_awvalid) begin
                grant <= 3'd2;
                arb_state <= ST_WR;
                gpu_deficit <= 0;
            end else if (m4_arvalid) begin
                /* Audio gets whatever's left after everyone else has
                 * had their turn.  Never touches gpu_deficit — this
                 * master is below CPU so it cannot starve the CPU. */
                grant <= 3'd4;
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
wire grant_m0 = (grant == 3'd0);
wire grant_m1 = (grant == 3'd1);
wire grant_m2 = (grant == 3'd2);
wire grant_m3 = (grant == 3'd3);
wire grant_m4 = (grant == 3'd4);
wire active_rd = (arb_state == ST_RD);
wire active_wr = (arb_state == ST_WR);
wire active = active_rd | active_wr;

// Completion guards: on the cycle bvalid/rlast fires, the slave returns to
// S_IDLE while the arbiter is still in ST_WR/ST_RD (registered transition).
// Without masking, the slave would see the next master request through the
// mux and accept it before the arbiter returns to ST_IDLE — causing a
// duplicate transaction the arbiter never tracks.
wire rd_completing = active_rd && s_rvalid && s_rlast;
wire wr_completing = active_wr && s_bvalid;

// AR channel — masked on rlast to prevent slave from accepting a new read.
// M4 is read-only; default to its signals through the same mux fabric.
assign s_arvalid = (active_rd && !rd_completing) ? (grant_m0 ? m0_arvalid :
                                                     grant_m1 ? m1_arvalid :
                                                     grant_m2 ? m2_arvalid :
                                                     grant_m3 ? m3_arvalid :
                                                                m4_arvalid) : 1'b0;
assign s_araddr  = grant_m0 ? m0_araddr  : grant_m1 ? m1_araddr  :
                   grant_m2 ? m2_araddr  : grant_m3 ? m3_araddr  : m4_araddr;
assign s_arlen   = grant_m0 ? m0_arlen   : grant_m1 ? m1_arlen   :
                   grant_m2 ? m2_arlen   : grant_m3 ? m3_arlen   : m4_arlen;

// AW channel — masked on bvalid.  M4 never writes.
assign s_awvalid = (active_wr && !wr_completing) ? (grant_m0 ? m0_awvalid :
                                                     grant_m1 ? m1_awvalid :
                                                     grant_m2 ? m2_awvalid :
                                                                m3_awvalid) : 1'b0;
assign s_awaddr  = grant_m0 ? m0_awaddr  : grant_m1 ? m1_awaddr  :
                   grant_m2 ? m2_awaddr  : m3_awaddr;
assign s_awlen   = grant_m0 ? m0_awlen   : grant_m1 ? m1_awlen   :
                   grant_m2 ? m2_awlen   : m3_awlen;

// W channel — masked on bvalid (same as AW)
assign s_wvalid = (active_wr && !wr_completing) ? (grant_m0 ? m0_wvalid :
                                                    grant_m1 ? m1_wvalid :
                                                    grant_m2 ? m2_wvalid :
                                                               m3_wvalid) : 1'b0;
assign s_wdata  = grant_m0 ? m0_wdata  : grant_m1 ? m1_wdata  :
                  grant_m2 ? m2_wdata  : m3_wdata;
assign s_wstrb  = grant_m0 ? m0_wstrb  : grant_m1 ? m1_wstrb  :
                  grant_m2 ? m2_wstrb  : m3_wstrb;
assign s_wlast  = grant_m0 ? m0_wlast  : grant_m1 ? m1_wlast  :
                  grant_m2 ? m2_wlast  : m3_wlast;

// ============================================
// Slave → Master channel demux (combinational)
// ============================================

// AR ready — only to granted master during read
assign m0_arready = (active_rd && grant_m0) ? s_arready : 1'b0;
assign m1_arready = (active_rd && grant_m1) ? s_arready : 1'b0;
assign m2_arready = (active_rd && grant_m2) ? s_arready : 1'b0;
assign m3_arready = (active_rd && grant_m3) ? s_arready : 1'b0;
assign m4_arready = (active_rd && grant_m4) ? s_arready : 1'b0;

// R channel — only to granted master during read
assign m0_rvalid = (active_rd && grant_m0) ? s_rvalid : 1'b0;
assign m1_rvalid = (active_rd && grant_m1) ? s_rvalid : 1'b0;
assign m2_rvalid = (active_rd && grant_m2) ? s_rvalid : 1'b0;
assign m3_rvalid = (active_rd && grant_m3) ? s_rvalid : 1'b0;
assign m4_rvalid = (active_rd && grant_m4) ? s_rvalid : 1'b0;

// Upstream rready — mux from granted master.  GPU (m0), DMA (m1),
// bridge (m3), and audio (m4) don't back-pressure so they default to 1.
// CPU (m2) drives m2_rready via cpu_target_port's registered response
// slot — this is what makes sync-burst back-pressure propagate all the
// way from the cpu down to the SDRAM controller.
assign s_rready = active_rd ? (grant_m2 ? m2_rready : 1'b1) : 1'b1;
assign m0_rdata  = s_rdata;  // Broadcast data (only valid matters)
assign m1_rdata  = s_rdata;
assign m2_rdata  = s_rdata;
assign m3_rdata  = s_rdata;
assign m4_rdata  = s_rdata;
assign m0_rresp  = s_rresp;
assign m1_rresp  = s_rresp;
assign m2_rresp  = s_rresp;
assign m3_rresp  = s_rresp;
assign m4_rresp  = s_rresp;
assign m0_rlast  = s_rlast;
assign m1_rlast  = s_rlast;
assign m2_rlast  = s_rlast;
assign m3_rlast  = s_rlast;
assign m4_rlast  = s_rlast;

// AW ready — only to granted master during write (M4 never writes).
assign m0_awready = (active_wr && grant_m0) ? s_awready : 1'b0;
assign m1_awready = (active_wr && grant_m1) ? s_awready : 1'b0;
assign m2_awready = (active_wr && grant_m2) ? s_awready : 1'b0;
assign m3_awready = (active_wr && grant_m3) ? s_awready : 1'b0;

// W ready — only to granted master during write
assign m0_wready = (active_wr && grant_m0) ? s_wready : 1'b0;
assign m1_wready = (active_wr && grant_m1) ? s_wready : 1'b0;
assign m2_wready = (active_wr && grant_m2) ? s_wready : 1'b0;
assign m3_wready = (active_wr && grant_m3) ? s_wready : 1'b0;

// B channel — only to granted master during write
assign m0_bvalid = (active_wr && grant_m0) ? s_bvalid : 1'b0;
assign m1_bvalid = (active_wr && grant_m1) ? s_bvalid : 1'b0;
assign m2_bvalid = (active_wr && grant_m2) ? s_bvalid : 1'b0;
assign m3_bvalid = (active_wr && grant_m3) ? s_bvalid : 1'b0;
assign m0_bresp  = s_bresp;
assign m1_bresp  = s_bresp;
assign m2_bresp  = s_bresp;
assign m3_bresp  = s_bresp;

endmodule
