//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

//
// lsu_axi_shim.v
//
// Native LsuPlugin cmd/rsp bus → AXI4 master shim.  Lifted verbatim
// from the inline shim block in cpu_system.v so it can also be
// instantiated by tb_gpu_chain — gives us the full
//   shim → slices → cpu_target_port → axi_periph_slave
// path under sim without dragging VexiiRiscv in.
//
// Behavior mirrors cpu_system's inline shim 1:1:
//   - 1-deep read tracker.
//   - 4-deep posted-write FIFO with M2 same-address coalescer that
//     fires AW with awburst=FIXED, awlen=N-1 for runs of identical
//     MMIO destinations.
//   - Writes posted: lsu_cmd_ready falls only when the FIFO is full
//     OR a read is in flight; rsp_valid pulses one cycle after each
//     write enters the FIFO.
//   - Read rsp at R-last; write errors are dropped (writes posted).
//
// If cpu_system is later refactored to instantiate this module
// instead of inlining, the shim's behavior must remain bit-identical
// — the testbench (tb_gpu_chain) and the production CPU port both
// rely on the same coalescer details.
//

`default_nettype none

module lsu_axi_shim (
    input  wire        clk,
    input  wire        reset,

    // ============================================================
    // LSU cmd/rsp bus (upstream — from VexiiRiscv or testbench)
    // ============================================================
    input  wire        lsu_cmd_valid,
    output wire        lsu_cmd_ready,
    input  wire        lsu_cmd_write,
    input  wire [31:0] lsu_cmd_addr,
    input  wire [31:0] lsu_cmd_data,
    input  wire [3:0]  lsu_cmd_mask,
    output wire        lsu_rsp_valid,
    output wire        lsu_rsp_error,
    output wire [31:0] lsu_rsp_data,

    // ============================================================
    // AXI4 master (downstream — to slices / cpu_target_port)
    // ============================================================
    output wire        per_arvalid_cpu,
    input  wire        per_arready_cpu,
    output wire [31:0] per_araddr_cpu,
    output wire [7:0]  per_arlen_cpu,

    input  wire        per_rvalid_cpu,
    output wire        per_rready_cpu,
    input  wire [31:0] per_rdata_cpu,
    input  wire [1:0]  per_rresp_cpu,
    input  wire        per_rlast_cpu,

    output wire        per_awvalid_cpu,
    input  wire        per_awready_cpu,
    output wire [31:0] per_awaddr_cpu,
    output wire [7:0]  per_awlen_cpu,
    output wire [1:0]  per_awburst_cpu,

    output wire        per_wvalid_cpu,
    input  wire        per_wready_cpu,
    output wire [31:0] per_wdata_cpu,
    output wire [3:0]  per_wstrb_cpu,
    output wire        per_wlast_cpu,

    input  wire        per_bvalid_cpu,
    output wire        per_bready_cpu,
    input  wire [1:0]  per_bresp_cpu
);

// Read tracker — 1-deep.
reg        lsu_inflight_read;
reg        lsu_ar_sent;
reg [31:0] lsu_rd_addr;

// Write FIFO — 4-deep posted-write queue.
localparam WR_FIFO_DEPTH = 4;
localparam WR_PTR_W      = 2;
localparam WR_CNT_W      = 3;

reg [31:0] wr_addr_mem [0:WR_FIFO_DEPTH-1];
reg [31:0] wr_data_mem [0:WR_FIFO_DEPTH-1];
reg [3:0]  wr_mask_mem [0:WR_FIFO_DEPTH-1];
reg [WR_PTR_W-1:0] wr_head, wr_tail;
reg [WR_CNT_W-1:0] wr_count;
reg                lsu_aw_sent, lsu_w_sent;

wire wr_fifo_full  = (wr_count == WR_FIFO_DEPTH);
wire wr_fifo_empty = (wr_count == 0);

reg [WR_PTR_W-1:0] burst_awlen;
reg [WR_PTR_W-1:0] burst_w_idx;

wire lsu_cmd_can_accept_wr = !lsu_inflight_read && !wr_fifo_full;
wire lsu_cmd_can_accept_rd = !lsu_inflight_read && wr_fifo_empty;
wire lsu_cmd_can_accept    =  lsu_cmd_write ? lsu_cmd_can_accept_wr
                                            : lsu_cmd_can_accept_rd;
wire lsu_cmd_fire = lsu_cmd_valid && lsu_cmd_can_accept;
wire wr_push     = lsu_cmd_fire & lsu_cmd_write;
wire wr_pop      = per_bvalid_cpu & per_bready_cpu;

reg wr_rsp_q;
always @(posedge clk or posedge reset) begin
    if (reset) wr_rsp_q <= 1'b0;
    else       wr_rsp_q <= wr_push;
end

assign lsu_rsp_valid = (per_rvalid_cpu & per_rready_cpu & per_rlast_cpu)
                     | wr_rsp_q;
assign lsu_rsp_data  = per_rdata_cpu;
assign lsu_rsp_error = (per_rvalid_cpu & per_rready_cpu & per_rlast_cpu)
                       ? (per_rresp_cpu != 2'b00)
                       : 1'b0;
assign lsu_cmd_ready = lsu_cmd_can_accept;

// Read FSM — 1-deep.
always @(posedge clk or posedge reset) begin
    if (reset) begin
        lsu_inflight_read <= 1'b0;
        lsu_ar_sent       <= 1'b0;
        lsu_rd_addr       <= 32'b0;
    end else begin
        if (lsu_cmd_fire && !lsu_cmd_write)
            lsu_rd_addr <= lsu_cmd_addr;
        if (per_arvalid_cpu && per_arready_cpu) lsu_ar_sent <= 1'b1;
        if (per_rvalid_cpu  && per_rready_cpu && per_rlast_cpu) begin
            lsu_inflight_read <= 1'b0;
            lsu_ar_sent       <= 1'b0;
        end else if (lsu_cmd_fire && !lsu_cmd_write) begin
            lsu_inflight_read <= 1'b1;
        end
    end
end

// M2 match scan
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

wire [WR_PTR_W-1:0] w_idx     = wr_head + burst_w_idx;
wire [31:0]         w_addr    = wr_addr_mem[wr_head];
wire [31:0]         w_data    = wr_data_mem[w_idx];
wire [3:0]          w_mask    = wr_mask_mem[w_idx];
// Pipeline-race fix: when AW and W handshakes fire on the same
// posedge (first beat of a multi-beat burst), burst_awlen is still
// the OLD value during the cycle — it'll update to burst_awlen_calc
// at the same posedge that the W handshake commits.  Computing
// w_is_last against the OLD burst_awlen makes the shim mark beat 0
// as last on burst transitions (e.g. single-beat → 3-beat) and stop
// driving W after one beat → slave waits forever for the remaining
// beats → cmd_ready stuck low → CR-issue-1 wedge.  Use the value
// that burst_awlen WILL HAVE post-handshake.
wire [WR_PTR_W-1:0] eff_burst_awlen =
    (per_awvalid_cpu && per_awready_cpu) ? burst_awlen_calc : burst_awlen;
wire                w_is_last = (burst_w_idx == eff_burst_awlen);

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

        if (wr_pop) begin
            wr_head     <= wr_head + (burst_awlen + {{(WR_PTR_W-1){1'b0}}, 1'b1});
            lsu_aw_sent <= 1'b0;
            lsu_w_sent  <= 1'b0;
            burst_w_idx <= {WR_PTR_W{1'b0}};
        end

        if (per_awvalid_cpu && per_awready_cpu) begin
            lsu_aw_sent <= 1'b1;
            burst_awlen <= burst_awlen_calc;
        end

        if (per_wvalid_cpu && per_wready_cpu) begin
            if (w_is_last) lsu_w_sent <= 1'b1;
            else           burst_w_idx <= burst_w_idx + {{(WR_PTR_W-1){1'b0}}, 1'b1};
        end

        case ({wr_push, wr_pop})
            2'b10: wr_count <= wr_count + 3'd1;
            2'b01: wr_count <= wr_count - {1'b0, burst_awlen} - 3'd1;
            2'b11: wr_count <= wr_count - {1'b0, burst_awlen};
            default: ;
        endcase
    end
end

// AR channel
assign per_arvalid_cpu = lsu_inflight_read & ~lsu_ar_sent;
assign per_araddr_cpu  = lsu_rd_addr;
assign per_arlen_cpu   = 8'd0;
assign per_rready_cpu  = 1'b1;

// AW channel
assign per_awvalid_cpu = !wr_fifo_empty & ~lsu_aw_sent;
assign per_awaddr_cpu  = w_addr;
assign per_awlen_cpu   = {{(8-WR_PTR_W){1'b0}}, burst_awlen_calc};
assign per_awburst_cpu = (burst_awlen_calc != {WR_PTR_W{1'b0}}) ? 2'b00 : 2'b01;

// W channel
assign per_wvalid_cpu  = !wr_fifo_empty & ~lsu_w_sent;
assign per_wdata_cpu   = w_data;
assign per_wstrb_cpu   = w_mask;
assign per_wlast_cpu   = w_is_last;
assign per_bready_cpu  = 1'b1;

endmodule

`default_nettype wire
