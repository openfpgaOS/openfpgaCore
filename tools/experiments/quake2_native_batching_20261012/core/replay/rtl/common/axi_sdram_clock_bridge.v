// SPDX-License-Identifier: Apache-2.0
// Buffered AXI subset used between the Pocket arbiter and SDRAM slave.
// Addresses are aligned 32-bit INCR bursts with no IDs, as on the arbiter.
// Both sides must reset together. Reset assertion is asynchronous; each
// domain releases locally. The bridge stays alive across CPU soft resets,
// so accepted writes and their B responses cannot disappear during a save.
//
// IMPORTANT: wcont must be cleared at the downstream slave. An upstream
// continuous stream can acquire bubbles across this bridge. The slave's
// existing 16-word buffer makes native writes underrun-proof again.
`default_nettype none
module axi_sdram_clock_bridge (
    input wire s_clk, m_clk, reset_n,
    input wire s_arvalid, output wire s_arready,
    input wire [31:0] s_araddr, input wire [7:0] s_arlen,
    output wire s_rvalid, input wire s_rready,
    output wire [31:0] s_rdata, output wire [1:0] s_rresp,
    output wire s_rlast,
    input wire s_awvalid, output wire s_awready,
    input wire [31:0] s_awaddr, input wire [7:0] s_awlen,
    input wire s_wvalid, output wire s_wready,
    input wire [31:0] s_wdata, input wire [3:0] s_wstrb,
    input wire s_wlast,
    output wire s_bvalid, input wire s_bready, output wire [1:0] s_bresp,
    output wire m_arvalid, input wire m_arready,
    output wire [31:0] m_araddr, output wire [7:0] m_arlen,
    input wire m_rvalid, output wire m_rready,
    input wire [31:0] m_rdata, input wire [1:0] m_rresp,
    input wire m_rlast,
    output wire m_awvalid, input wire m_awready,
    output wire [31:0] m_awaddr, output wire [7:0] m_awlen,
    output wire m_wvalid, input wire m_wready,
    output wire [31:0] m_wdata, output wire [3:0] m_wstrb,
    output wire m_wlast,
    input wire m_bvalid, output wire m_bready, input wire [1:0] m_bresp
);
    sdram_cdc_fifo #(.WIDTH(40), .ADDR_BITS(2)) ar (
        .wclk(s_clk), .rclk(m_clk), .reset_n(reset_n),
        .wvalid(s_arvalid), .wready(s_arready), .wdata({s_arlen, s_araddr}),
        .rvalid(m_arvalid), .rready(m_arready), .rdata({m_arlen, m_araddr}));
    sdram_cdc_fifo #(.WIDTH(40), .ADDR_BITS(2)) aw (
        .wclk(s_clk), .rclk(m_clk), .reset_n(reset_n),
        .wvalid(s_awvalid), .wready(s_awready), .wdata({s_awlen, s_awaddr}),
        .rvalid(m_awvalid), .rready(m_awready), .rdata({m_awlen, m_awaddr}));
    sdram_cdc_fifo #(.WIDTH(37), .ADDR_BITS(3)) w (
        .wclk(s_clk), .rclk(m_clk), .reset_n(reset_n),
        .wvalid(s_wvalid), .wready(s_wready), .wdata({s_wlast, s_wstrb, s_wdata}),
        .rvalid(m_wvalid), .rready(m_wready), .rdata({m_wlast, m_wstrb, m_wdata}));
    sdram_cdc_fifo #(.WIDTH(35), .ADDR_BITS(3)) r (
        .wclk(m_clk), .rclk(s_clk), .reset_n(reset_n),
        .wvalid(m_rvalid), .wready(m_rready), .wdata({m_rlast, m_rresp, m_rdata}),
        .rvalid(s_rvalid), .rready(s_rready), .rdata({s_rlast, s_rresp, s_rdata}));
    sdram_cdc_fifo #(.WIDTH(2), .ADDR_BITS(2)) b (
        .wclk(m_clk), .rclk(s_clk), .reset_n(reset_n),
        .wvalid(m_bvalid), .wready(m_bready), .wdata(m_bresp),
        .rvalid(s_bvalid), .rready(s_bready), .rdata(s_bresp));
endmodule

// Small first-word-fall-through dual-clock FIFO. Only Gray pointers cross
// through synchronizers. Data remains in its slot until the read pointer
// has returned to the writer, so the multi-bit payload is never sampled
// while that slot is being written. No RAM reset, preserving MLAB inference.
// In the 83/166 experiment both clocks are PLL-related and ALL these paths
// remain timed by STA; do not add an asynchronous clock-group exception.
module sdram_cdc_fifo #(
    parameter WIDTH = 32,
    parameter ADDR_BITS = 3
) (
    input wire wclk, rclk, reset_n,
    input wire wvalid, output wire wready, input wire [WIDTH-1:0] wdata,
    output wire rvalid, input wire rready, output wire [WIDTH-1:0] rdata
);
    localparam DEPTH = 1 << ADDR_BITS;
    (* ramstyle = "MLAB, no_rw_check" *) reg [WIDTH-1:0] mem [0:DEPTH-1];
    reg [ADDR_BITS:0] wbin, rbin, wgray, rgray;
    (* preserve *) reg [ADDR_BITS:0] rq1_wgray, rq2_wgray;
    (* preserve *) reg [ADDR_BITS:0] wq1_rgray, wq2_rgray;
    (* preserve *) reg [1:0] wrst, rrst;
    reg full, empty;
    wire push = wvalid && wready;
    wire pop = rvalid && rready;
    wire [ADDR_BITS:0] wnext = wbin + {{ADDR_BITS{1'b0}}, push};
    wire [ADDR_BITS:0] rnext = rbin + {{ADDR_BITS{1'b0}}, pop};
    wire [ADDR_BITS:0] wgray_next = (wnext >> 1) ^ wnext;
    wire [ADDR_BITS:0] rgray_next = (rnext >> 1) ^ rnext;
    assign wready = wrst[1] && !full;
    assign rvalid = rrst[1] && !empty;
    assign rdata = mem[rbin[ADDR_BITS-1:0]];
    always @(posedge wclk or negedge reset_n)
        if (!reset_n) wrst <= 0; else wrst <= {wrst[0], 1'b1};
    always @(posedge rclk or negedge reset_n)
        if (!reset_n) rrst <= 0; else rrst <= {rrst[0], 1'b1};
    always @(posedge wclk) if (push) mem[wbin[ADDR_BITS-1:0]] <= wdata;
    always @(posedge wclk or negedge reset_n) begin
        if (!reset_n) begin
            wbin <= 0; wgray <= 0; full <= 0;
            wq1_rgray <= 0; wq2_rgray <= 0;
        end else if (!wrst[1]) begin
            wbin <= 0; wgray <= 0; full <= 0;
            wq1_rgray <= 0; wq2_rgray <= 0;
        end else begin
            wq1_rgray <= rgray; wq2_rgray <= wq1_rgray;
            wbin <= wnext; wgray <= wgray_next;
            full <= (wgray_next == {~wq2_rgray[ADDR_BITS:ADDR_BITS-1],
                                    wq2_rgray[ADDR_BITS-2:0]});
        end
    end
    always @(posedge rclk or negedge reset_n) begin
        if (!reset_n) begin
            rbin <= 0; rgray <= 0; empty <= 1;
            rq1_wgray <= 0; rq2_wgray <= 0;
        end else if (!rrst[1]) begin
            rbin <= 0; rgray <= 0; empty <= 1;
            rq1_wgray <= 0; rq2_wgray <= 0;
        end else begin
            rq1_wgray <= wgray; rq2_wgray <= rq1_wgray;
            rbin <= rnext; rgray <= rgray_next;
            empty <= (rgray_next == rq2_wgray);
        end
    end
endmodule
`default_nettype wire
