//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * bridge_to_sdram.v -- APF bridge -> SDRAM write path
 *
 * APF data-slot reads appear as bridge writes in clk_74a.  This module
 * captures writes to the SDRAM bridge window and replays them as AXI4
 * single-beat writes in the SDRAM/CPU clock domain.
 */

`default_nettype none

module bridge_to_sdram #(
    // Sized to hold one full APF data-slot burst (one 4 KB / 1024-word chunk)
    // so a transient drain stall cannot drop write data.  Mirrors the CRAM0
    // bridge write FIFO depth.
    parameter FIFO_WORDS = 1024,
    parameter FIFO_WIDTHU = 10
) (
    input  wire        clk_bridge,
    input  wire        reset_n,
    input  wire [31:0] bridge_addr,
    input  wire        bridge_wr,
    input  wire [31:0] bridge_wr_data,

    output wire        idle,
    output wire        fifo_full,
    output reg         overrun,
    // Diagnostic tap (clk_bridge): one-cycle pulse for every bridge word
    // that decodes into the SDRAM window, BEFORE any FIFO-full drop.
    // core_top counts these per data-slot command (F2i bridge word
    // counters) so firmware can tell "words never arrived" (short count
    // = RX-side drop) from "words arrived corrupted" (exact count).
    output wire        detect_wr_o,

    input  wire        clk_axi,
    // Reset for the clk_axi AXI write FSM.  Held at 1'b1 (config-init only) by
    // core_top so the FSM is symmetric with the never-reset arbiter/SDRAM
    // slave it drives: it never asynchronously drops m_awvalid/m_wvalid mid-
    // handshake (which would wedge the live slave on a warm reset), and it has
    // no async-deassert metastability window against clk_axi.
    input  wire        reset_n_axi,
    output reg         m_awvalid,
    input  wire        m_awready,
    output reg  [31:0] m_awaddr,
    output reg  [7:0]  m_awlen,
    output reg         m_wvalid,
    input  wire        m_wready,
    output reg  [31:0] m_wdata,
    output reg  [3:0]  m_wstrb,
    output reg         m_wlast,
    input  wire        m_bvalid
);

wire detect_wr = bridge_wr && (bridge_addr[31:26] == 6'b000000);
assign detect_wr_o = detect_wr;

reg [63:0] fifo_in;
reg        fifo_wrreq;

always @(posedge clk_bridge or negedge reset_n) begin
    if (!reset_n) begin
        fifo_wrreq <= 1'b0;
        fifo_in <= 64'd0;
        overrun <= 1'b0;
    end else begin
        fifo_wrreq <= 1'b0;
        if (detect_wr) begin
            if (!fifo_full) begin
                fifo_in <= {bridge_addr, bridge_wr_data};
                fifo_wrreq <= 1'b1;
            end else begin
                overrun <= 1'b1;
            end
        end
    end
end

wire [63:0] fifo_out;
wire        fifo_empty;
wire [FIFO_WIDTHU-1:0] fifo_usedw_unused;
reg         fifo_rdreq;

dcfifo bridge_sdram_fifo (
    .wrclk   (clk_bridge),
    .rdclk   (clk_axi),
    .data    (fifo_in),
    .wrreq   (fifo_wrreq),
    .q       (fifo_out),
    .rdreq   (fifo_rdreq),
    .rdempty (fifo_empty),
    .wrusedw (fifo_usedw_unused),
    .wrfull  (fifo_full),
    .aclr    (~reset_n)
);
defparam bridge_sdram_fifo.intended_device_family = "Cyclone V",
    bridge_sdram_fifo.lpm_numwords        = FIFO_WORDS,
    bridge_sdram_fifo.lpm_showahead       = "ON",
    bridge_sdram_fifo.lpm_type            = "dcfifo",
    bridge_sdram_fifo.lpm_width           = 64,
    bridge_sdram_fifo.lpm_widthu          = FIFO_WIDTHU,
    bridge_sdram_fifo.overflow_checking   = "ON",
    bridge_sdram_fifo.underflow_checking  = "ON",
    bridge_sdram_fifo.rdsync_delaypipe    = 4,
    bridge_sdram_fifo.wrsync_delaypipe    = 4,
    bridge_sdram_fifo.use_eab             = "ON";

localparam ST_IDLE = 2'd0;
localparam ST_AW   = 2'd1;
localparam ST_B    = 2'd3;

reg [1:0] state;

// `idle` reflects ONLY drain state (FIFO empty + FSM idle + no AXI request
// outstanding).  It must NOT depend on the sticky `overrun` flag: gating
// completion on a never-cleared error latch turned a transient FIFO overflow
// into a permanent, session-long wedge of the APF data-slot handshake.
// `overrun` is now a pure diagnostic, surfaced to firmware by core_top.
assign idle = fifo_empty && (state == ST_IDLE) &&
              !m_awvalid && !m_wvalid;

always @(posedge clk_axi or negedge reset_n_axi) begin
    if (!reset_n_axi) begin
        state <= ST_IDLE;
        fifo_rdreq <= 1'b0;
        m_awvalid <= 1'b0;
        m_awaddr <= 32'd0;
        m_awlen <= 8'd0;
        m_wvalid <= 1'b0;
        m_wdata <= 32'd0;
        m_wstrb <= 4'h0;
        m_wlast <= 1'b0;
    end else begin
        fifo_rdreq <= 1'b0;

        case (state)
        ST_IDLE: begin
            if (!fifo_empty) begin
                m_awaddr <= fifo_out[63:32];
                m_wdata <= fifo_out[31:0];
                m_awlen <= 8'd0;
                m_wstrb <= 4'hF;
                m_wlast <= 1'b1;
                // Present AW and W together so the SDRAM slave takes its
                // combined S_IDLE fast path (AW+W in one go), skipping the
                // extra S_WR_NEXT cycle per word.
                m_awvalid <= 1'b1;
                m_wvalid <= 1'b1;
                state <= ST_AW;
            end
        end

        // AW+W outstanding.  Drop each channel independently on its own
        // ready; advance once both have handshaked.
        ST_AW: begin
            if (m_awready) m_awvalid <= 1'b0;
            if (m_wready) begin
                m_wvalid <= 1'b0;
                m_wlast <= 1'b0;
            end
            if ((m_awready || !m_awvalid) && (m_wready || !m_wvalid))
                state <= ST_B;
        end

        ST_B: begin
            if (m_bvalid) begin
                fifo_rdreq <= 1'b1;
                state <= ST_IDLE;
            end
        end

        default: state <= ST_IDLE;
        endcase
    end
end

endmodule

`default_nettype wire
