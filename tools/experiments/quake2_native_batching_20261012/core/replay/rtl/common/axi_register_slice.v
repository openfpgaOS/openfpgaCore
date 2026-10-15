//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

// AXI4 register slice — 1-cycle pipeline stage with full throughput.
//
// Drop-in for any valid/ready handshake.  Inserts a register on both
// the payload and the valid/ready bits, breaking the combinational
// path between upstream and downstream sides.  Throughput: 1
// transfer/cycle (no stalls under steady traffic).  Latency: +1
// cycle.  Storage: 2 slots so an upstream-stall doesn't bubble
// through, and a downstream-stall doesn't bubble back.
//
// Used to break placement-critical AXI nets between VexiiRiscv's
// master ports and the cpu_target_port fan-out (Phase 2 of the
// timing-closure plan).  Without these slices, the CPU's pins must
// stay close to the SDRAM/CRAM/peripheral ports physically; with
// slices, each side reaches a register inside its own placement
// region and the long crossing is registered-to-registered, which
// the placer can route freely.
//
// AXI ordering: this is a single in-order channel — the slice
// preserves order.  Use one instance per AXI channel (AR, AW, W, R,
// B); the channels are independent on the protocol so each gets its
// own slice.
//
// Implementation: classic 2-slot skid.  out_data is the head of the
// queue (visible downstream); skid_data is a 1-slot overflow buffer
// used only when downstream is stalled and a new upstream beat
// arrives.  Both registers are written from the upstream payload;
// downstream only sees out_data.

`default_nettype none

module axi_register_slice #(
    parameter W = 32,
    // FIFO_OUT=1: same 2-entry, full-throughput behaviour (s_ready while at
    // most one beat is held, m_valid while any is), but each beat is written
    // once from s_payload into one of two slots and m_payload is selected by
    // a read pointer.  m_ready then clocks two pointer/count flops instead of
    // reloading W payload flops (the skid form's head enable), taking the
    // downstream grant cone off a high-fanout enable.  Costs a W-bit 2:1 mux
    // on m_payload and saves the one on the head's input.
    parameter FIFO_OUT = 0
) (
    input  wire         clk,
    input  wire         reset_n,

    // Upstream (master side) — drives valid + payload, samples ready.
    input  wire         s_valid,
    output wire         s_ready,
    input  wire [W-1:0] s_payload,

    // Downstream (slave side) — sees valid + payload, drives ready.
    output wire         m_valid,
    input  wire         m_ready,
    output wire [W-1:0] m_payload
);

generate if (FIFO_OUT) begin : g_fifo
    reg [W-1:0] slot0, slot1;
    reg         wr_sel, rd_sel;
    reg [1:0]   count;

    assign s_ready   = (count != 2'd2);
    assign m_valid   = (count != 2'd0);
    assign m_payload = rd_sel ? slot1 : slot0;

    wire upstream_xfer   = s_valid && (count != 2'd2);
    wire downstream_xfer = (count != 2'd0) && m_ready;

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            slot0  <= {W{1'b0}};
            slot1  <= {W{1'b0}};
            wr_sel <= 1'b0;
            rd_sel <= 1'b0;
            count  <= 2'd0;
        end else begin
            if (upstream_xfer) begin
                if (wr_sel) slot1 <= s_payload;
                else        slot0 <= s_payload;
                wr_sel <= ~wr_sel;
            end
            if (downstream_xfer) rd_sel <= ~rd_sel;
            count <= count + {1'b0, upstream_xfer} - {1'b0, downstream_xfer};
        end
    end
end else begin : g_skid
    reg         out_valid;
    reg [W-1:0] out_data;
    reg         skid_valid;
    reg [W-1:0] skid_data;

    // Upstream can accept a beat whenever the skid slot is empty —
    // the head slot might be full, but we still have room in the
    // overflow.  This is what makes the slice full-throughput: a
    // back-to-back upstream stream with momentary downstream stalls
    // doesn't bubble.
    assign s_ready   = !skid_valid;
    assign m_valid   = out_valid;
    assign m_payload = out_data;

    wire upstream_xfer   = s_valid && s_ready;
    wire downstream_xfer = m_valid && m_ready;

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            out_valid  <= 1'b0;
            out_data   <= {W{1'b0}};
            skid_valid <= 1'b0;
            skid_data  <= {W{1'b0}};
        end else begin
            // Output side: drain skid into head when downstream
            // takes the head this cycle.
            if (downstream_xfer) begin
                if (skid_valid) begin
                    out_data   <= skid_data;
                    out_valid  <= 1'b1;
                    skid_valid <= 1'b0;
                end else begin
                    out_valid <= 1'b0;
                end
            end

            // Input side: stage incoming beat.  Lands in the head if
            // the head is empty (or about to drain this cycle); else
            // in the skid slot.
            if (upstream_xfer) begin
                if (!out_valid || downstream_xfer) begin
                    out_data  <= s_payload;
                    out_valid <= 1'b1;
                end else begin
                    skid_data  <= s_payload;
                    skid_valid <= 1'b1;
                end
            end
        end
    end
end endgenerate

endmodule

`default_nettype wire
