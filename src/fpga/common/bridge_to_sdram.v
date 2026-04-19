/*
 * bridge_to_sdram.v -- APF bridge → SDRAM write path
 *
 * The Pocket bridge (PHDP/host) writes appear on `bridge_addr` /
 * `bridge_wr_data` strobed by `bridge_wr` in the clk_74a domain.
 * This module sniffs writes whose address lands in the SDRAM range
 * (default 0x10xx_xxxx) and re-issues them as AXI4 single-beat writes
 * on the SDRAM arbiter's M3 master port — fully bypassing the CRAM1
 * I/O cache that races with the audio mixer.
 *
 * Architecture:
 *   1. clk_74a side: sniff bridge_wr; if bridge_addr matches the
 *      SDRAM range, pack {addr[27:0], data[31:0], wstrb[3:0]} into
 *      a 64-bit FIFO entry and push.
 *   2. dcfifo (16 entries) bridges clk_74a → clk_cpu.
 *   3. clk_cpu side: pop entries, drive an AXI4 write transaction
 *      (AW + W + B) on the M3 port.  One entry == one beat (no
 *      bursts), since bridge writes are sparse and 32-bit aligned.
 *
 * The FIFO smooths the difference between bridge spurt rate
 * (~8 MB/s peaks) and SDRAM service rate (>100 MB/s).  16 entries
 * is enough to absorb a worst-case PHDP burst with the SDRAM
 * arbiter granting M3 last-priority.
 *
 * No reads supported — file I/O always reads from SDRAM via the
 * CPU AXI master, which goes through the existing CPU SDRAM port.
 */

module bridge_to_sdram #(
    parameter [7:0] SDRAM_TAG = 8'h10  // bridge_addr[31:24] match
) (
    // Bridge side (clk_74a)
    input  wire        clk_bridge,
    input  wire        reset_n,
    input  wire [31:0] bridge_addr,
    input  wire        bridge_wr,
    input  wire [31:0] bridge_wr_data,

    // Optional back-pressure signal to bridge — high when our FIFO
    // would overflow.  Bridge currently has no flow-control hook, so
    // this is exposed for future use.
    output wire        fifo_full,

    // AXI4 master M3 — clk_cpu domain
    input  wire        clk_axi,
    output reg         m_awvalid,
    input  wire        m_awready,
    output reg  [31:0] m_awaddr,
    output reg  [7:0]  m_awlen,
    output reg         m_wvalid,
    input  wire        m_wready,
    output reg  [31:0] m_wdata,
    output reg  [3:0]  m_wstrb,
    output reg         m_wlast,
    input  wire        m_bvalid,
    output wire        m_bready
);

// =====================================================================
// clk_bridge side — capture writes into the FIFO
// =====================================================================
wire detect_wr = bridge_wr && (bridge_addr[31:24] == SDRAM_TAG);

reg [63:0] fifo_in;
reg        fifo_wrreq;
always @(posedge clk_bridge or negedge reset_n) begin
    if (!reset_n) begin
        fifo_wrreq <= 1'b0;
        fifo_in    <= 64'b0;
    end else begin
        fifo_wrreq <= 1'b0;
        if (detect_wr && !fifo_full) begin
            // Layout: {addr[31:0], data[31:0]}.  Strobe is implicit
            // 4'hF (full word write) — bridge always writes 32-bit
            // aligned full words, never byte-enable subsets.
            fifo_in    <= {bridge_addr, bridge_wr_data};
            fifo_wrreq <= 1'b1;
        end
    end
end

// =====================================================================
// Async FIFO (dcfifo) -- 16 entries × 64 bits
// =====================================================================
wire [63:0] fifo_out;
wire        fifo_empty;
reg         fifo_rdreq;

dcfifo bridge_sdram_fifo (
    .wrclk   (clk_bridge),
    .rdclk   (clk_axi),
    .data    (fifo_in),
    .wrreq   (fifo_wrreq),
    .q       (fifo_out),
    .rdreq   (fifo_rdreq),
    .rdempty (fifo_empty),
    .wrfull  (fifo_full),
    .aclr    (~reset_n)
);
defparam bridge_sdram_fifo.intended_device_family = "Cyclone V",
    bridge_sdram_fifo.lpm_numwords        = 16,
    bridge_sdram_fifo.lpm_showahead       = "ON",
    bridge_sdram_fifo.lpm_type            = "dcfifo",
    bridge_sdram_fifo.lpm_width           = 64,
    bridge_sdram_fifo.lpm_widthu          = 4,
    bridge_sdram_fifo.overflow_checking   = "ON",
    bridge_sdram_fifo.underflow_checking  = "ON",
    bridge_sdram_fifo.rdsync_delaypipe    = 4,
    bridge_sdram_fifo.wrsync_delaypipe    = 4,
    bridge_sdram_fifo.use_eab             = "ON";

// =====================================================================
// clk_axi side — drive AXI4 single-beat writes
// =====================================================================
//
// Each FIFO entry becomes one AW + one W + one B handshake.  Stay
// AW-then-W (no overlap) to keep the FSM simple; the SDRAM arbiter
// already overlaps masters internally.
localparam ST_IDLE   = 2'd0;
localparam ST_AW     = 2'd1;
localparam ST_W      = 2'd2;
localparam ST_B      = 2'd3;

reg [1:0] state;

assign m_bready = 1'b1;  // we always accept the write response

always @(posedge clk_axi or negedge reset_n) begin
    if (!reset_n) begin
        state      <= ST_IDLE;
        fifo_rdreq <= 1'b0;
        m_awvalid  <= 1'b0;
        m_awaddr   <= 32'b0;
        m_awlen    <= 8'b0;
        m_wvalid   <= 1'b0;
        m_wdata    <= 32'b0;
        m_wstrb    <= 4'b0;
        m_wlast    <= 1'b0;
    end else begin
        fifo_rdreq <= 1'b0;
        case (state)
            ST_IDLE: begin
                if (!fifo_empty) begin
                    // showahead FIFO: q is valid before rdreq.
                    m_awaddr  <= fifo_out[63:32];
                    m_wdata   <= fifo_out[31:0];
                    m_awlen   <= 8'd0;     // single beat
                    m_wstrb   <= 4'hF;     // full word
                    m_wlast   <= 1'b1;     // single beat = last
                    m_awvalid <= 1'b1;
                    state     <= ST_AW;
                end
            end
            ST_AW: begin
                if (m_awready) begin
                    m_awvalid <= 1'b0;
                    m_wvalid  <= 1'b1;
                    state     <= ST_W;
                end
            end
            ST_W: begin
                if (m_wready) begin
                    m_wvalid <= 1'b0;
                    m_wlast  <= 1'b0;
                    state    <= ST_B;
                end
            end
            ST_B: begin
                if (m_bvalid) begin
                    fifo_rdreq <= 1'b1;   // pop the entry we just consumed
                    state      <= ST_IDLE;
                end
            end
            default: state <= ST_IDLE;
        endcase
    end
end

endmodule
