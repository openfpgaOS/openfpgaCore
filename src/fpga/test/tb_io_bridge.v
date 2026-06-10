//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

//
// tb_io_bridge.v — thin pad wrapper around the REAL io_bridge_peripheral.
//
// The C++ harness (tb_io_bridge_main.cpp) plays the Aristotle side of
// the APF PMP bridge bus: it drives phy_spiclk / phy_spiss / phy_spimosi
// / phy_spimiso through pad muxes (host_oe high = host owns the bus,
// low = host released; the peripheral drives the pads itself during
// read-data turnaround).  clk is the core's clk_74a.  Both clocks run
// at the same 74.25 MHz but the harness sweeps their relative phase in
// sub-ns steps, which is exactly the hardware situation: the two
// clocks are plesiochronous-in-phase, frequency-locked, with a
// power-up-latched offset.
//
// The resolved pad values are exported (pad_*) so the harness can
// receive the peripheral's read-data transmission.
//

`default_nettype none

module tb_io_bridge (
    input  wire        clk,            // core clk_74a
    input  wire        reset_n,
    input  wire        endian_little,

    // host (Aristotle) side
    input  wire        host_oe,        // 1 = host drives spiclk/mosi/miso
    input  wire        host_spiclk,
    input  wire        host_mosi,
    input  wire        host_miso,
    input  wire        host_ss,

    // resolved pad observation (for the read path)
    output wire        pad_spiclk,
    output wire        pad_spimosi,
    output wire        pad_spimiso,

    // word-level (PMP) side, observed by the checker
    output wire [31:0] pmp_addr,
    output wire        pmp_addr_valid,
    output wire        pmp_rd,
    input  wire [31:0] pmp_rd_data,
    output wire        pmp_wr,
    output wire [31:0] pmp_wr_data
);

    // pad resolution: host drives when host_oe=1, otherwise the
    // peripheral's tristate outputs win (it drives Z when idle).
    wire spiclk_pad, spimosi_pad, spimiso_pad;
    assign spiclk_pad  = host_oe ? host_spiclk : 1'bz;
    assign spimosi_pad = host_oe ? host_mosi   : 1'bz;
    assign spimiso_pad = host_oe ? host_miso   : 1'bz;

    assign pad_spiclk  = spiclk_pad;
    assign pad_spimosi = spimosi_pad;
    assign pad_spimiso = spimiso_pad;

    io_bridge_peripheral dut (
        .clk            ( clk ),
        .reset_n        ( reset_n ),

        .endian_little  ( endian_little ),

        .pmp_addr       ( pmp_addr ),
        .pmp_addr_valid ( pmp_addr_valid ),
        .pmp_rd         ( pmp_rd ),
        .pmp_rd_data    ( pmp_rd_data ),
        .pmp_wr         ( pmp_wr ),
        .pmp_wr_data    ( pmp_wr_data ),

        .phy_spimosi    ( spimosi_pad ),
        .phy_spimiso    ( spimiso_pad ),
        .phy_spiclk     ( spiclk_pad ),
        .phy_spiss      ( host_ss )
    );

endmodule

`default_nettype wire
