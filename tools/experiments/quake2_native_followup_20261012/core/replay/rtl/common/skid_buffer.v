//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------
//
// skid_buffer — full-throughput valid/ready register slice (2-slot skid).
//
// Registers BOTH the forward (payload + valid) and the backward (ready) paths
// of a single AXI-style handshake channel, so a long combinational
// valid/ready/data route through the fabric is split into two timing-isolated
// halves with NO loss of throughput: in steady state it accepts and emits one
// beat per cycle.  An AXI4 interface is registered by instantiating five of
// these — AW / W / AR carry payload downstream, B / R carry payload upstream —
// each with its channel's payload packed into {data}.
//
// Latency: 1 cycle.  Capacity: 2 beats (output slot + overflow "skid" slot),
// which is exactly what lets the slice tolerate a one-cycle ready bubble
// without ever stalling the upstream when the downstream is keeping up.
//
// Contract (standard AXI): payload/valid stable until accepted; s_ready and
// m_valid are not combinationally coupled across the slice (the whole point).
//
`default_nettype none

module skid_buffer #(
    parameter WIDTH = 32
) (
    input  wire             clk,
    input  wire             reset_n,

    // Upstream port (this slice is the SLAVE: it accepts s_valid when s_ready).
    input  wire             s_valid,
    output wire             s_ready,
    input  wire [WIDTH-1:0] s_data,

    // Downstream port (this slice is the MASTER: it drives m_valid, waits m_ready).
    output wire             m_valid,
    input  wire             m_ready,
    output wire [WIDTH-1:0] m_data
);

    reg              valid_o;          // output slot occupied (drives m_valid)
    reg [WIDTH-1:0]  data_o;
    reg              valid_s;          // overflow "skid" slot occupied
    reg [WIDTH-1:0]  data_s;

    // Accept a new upstream beat whenever the overflow slot is free.  When it
    // fills, s_ready drops and back-pressures upstream — registered, so the
    // upstream sees a clean registered ready, not a long combinational route.
    assign s_ready = !valid_s;
    assign m_valid = valid_o;
    assign m_data  = data_o;

    always @(posedge clk) begin
        if (!reset_n) begin
            valid_o <= 1'b0;
            valid_s <= 1'b0;
        end else if (m_ready || !valid_o) begin
            // Output slot is draining or empty: refill it from the skid slot
            // if one is held, otherwise straight from the upstream beat.
            if (valid_s) begin
                data_o  <= data_s;
                valid_o <= 1'b1;
                valid_s <= 1'b0;
            end else begin
                data_o  <= s_data;
                valid_o <= s_valid;
            end
        end else if (s_valid && s_ready) begin
            // Output slot is full and stalled (valid_o && !m_ready): capture the
            // one incoming beat into the skid slot so upstream isn't blocked
            // by the bubble.  s_ready then deasserts until the slot drains.
            data_s  <= s_data;
            valid_s <= 1'b1;
        end
    end

endmodule

`default_nettype wire
