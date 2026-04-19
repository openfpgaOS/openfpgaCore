/*
 * bridge_to_cram1.v -- APF bridge → CRAM1 access bridge with CDC
 *
 * The Pocket bridge (PHDP/host) issues writes/reads on bridge_addr /
 * bridge_wr_data strobed by bridge_wr / bridge_rd in the clk_74a
 * domain.  This module sniffs accesses whose address lands in the
 * CRAM1 range (default 0x30xx_xxxx) and forwards them to a single
 * CRAM1 controller running in the clk_cpu domain — eliminating the
 * dual-controller / pin-mux architecture that needed cram1_clk to
 * follow whichever controller was active.
 *
 * Architecture:
 *   1. clk_74a side: detect bridge_wr/rd in CRAM1 range; pack
 *      {is_write, addr[21:0], wdata[31:0]} into a 56-bit FIFO entry
 *      and push.
 *   2. Request dcfifo (16 entries, 56 bits) bridges clk_74a → clk_cpu.
 *   3. clk_cpu side: pop entries, drive req_rd/req_wr/req_addr/req_wdata
 *      to the shared CRAM1 controller (arbitrated against CPU + mixer
 *      in core_top.v).  Capture req_rdata when valid.
 *   4. Response dcfifo (4 entries, 32 bits) bridges clk_cpu → clk_74a.
 *   5. clk_74a side: drain response FIFO, hold latest in bridge_rd_data
 *      so the host's bridge_rd port returns the most recent read.
 *
 * The OS-visible "writes drained" status (bridge_wr_idle) is
 * synthesised from "request FIFO empty AND no in-flight bridge op" so
 * the file-IO completion poll path keeps working unchanged.
 */

`default_nettype none

module bridge_to_cram1 #(
    parameter [7:0] CRAM1_TAG = 8'h30  // bridge_addr[31:24] match
) (
    // Bridge side (clk_74a)
    input  wire        clk_bridge,
    input  wire        reset_n,
    input  wire [31:0] bridge_addr,
    input  wire        bridge_wr,
    input  wire        bridge_rd,
    input  wire [31:0] bridge_wr_data,
    output reg  [31:0] bridge_rd_data,
    output wire        bridge_active,    // any bridge CRAM1 op outstanding
    output wire        bridge_wr_idle,   // == !bridge_active

    // Controller A side (clk_cpu)
    input  wire        clk_cpu,
    output reg         req_rd,
    output reg         req_wr,
    output reg  [21:0] req_addr,
    output reg  [31:0] req_wdata,
    input  wire        req_busy,
    input  wire [31:0] req_rdata,
    input  wire        req_rdata_valid
);

// ============================================================
// clk_74a side -- detect bridge writes/reads, push to req FIFO
// ============================================================
wire detect_wr = bridge_wr && (bridge_addr[31:24] == CRAM1_TAG);

// Edge-detect read so we accept exactly one read per bridge_rd pulse
// (APF can hold bridge_rd HIGH for several cycles).
reg prev_bridge_rd_in_range;
always @(posedge clk_bridge) begin
    if (!reset_n)
        prev_bridge_rd_in_range <= 1'b0;
    else
        prev_bridge_rd_in_range <= bridge_rd && (bridge_addr[31:24] == CRAM1_TAG);
end
wire detect_rd = !prev_bridge_rd_in_range && bridge_rd && (bridge_addr[31:24] == CRAM1_TAG);

// Request FIFO entry: {is_write, addr[21:0], wdata[31:0]} = 55 bits, padded to 56.
reg [55:0] req_fifo_in;
reg        req_fifo_wrreq;
wire       req_fifo_full;

always @(posedge clk_bridge) begin
    if (!reset_n) begin
        req_fifo_wrreq <= 1'b0;
        req_fifo_in    <= 56'b0;
    end else begin
        req_fifo_wrreq <= 1'b0;
        if (detect_wr && !req_fifo_full) begin
            req_fifo_in    <= {1'b0, 1'b1, bridge_addr[23:2], bridge_wr_data};
            req_fifo_wrreq <= 1'b1;
        end else if (detect_rd && !req_fifo_full) begin
            req_fifo_in    <= {1'b0, 1'b0, bridge_addr[23:2], 32'b0};
            req_fifo_wrreq <= 1'b1;
        end
    end
end

// Async request FIFO (clk_74a → clk_cpu)
wire [55:0] req_fifo_out;
wire        req_fifo_empty;     // clk_cpu side
wire        req_fifo_wrempty;   // clk_74a side (used in bridge_active)
reg         req_fifo_rdreq;

dcfifo bridge_cram1_req_fifo (
    .wrclk   (clk_bridge),
    .rdclk   (clk_cpu),
    .data    (req_fifo_in),
    .wrreq   (req_fifo_wrreq),
    .q       (req_fifo_out),
    .rdreq   (req_fifo_rdreq),
    .rdempty (req_fifo_empty),
    .wrempty (req_fifo_wrempty),
    .wrfull  (req_fifo_full),
    .aclr    (~reset_n)
);
defparam bridge_cram1_req_fifo.intended_device_family = "Cyclone V",
    bridge_cram1_req_fifo.lpm_numwords        = 16,
    bridge_cram1_req_fifo.lpm_showahead       = "ON",
    bridge_cram1_req_fifo.lpm_type            = "dcfifo",
    bridge_cram1_req_fifo.lpm_width           = 56,
    bridge_cram1_req_fifo.lpm_widthu          = 4,
    bridge_cram1_req_fifo.overflow_checking   = "ON",
    bridge_cram1_req_fifo.underflow_checking  = "ON",
    bridge_cram1_req_fifo.rdsync_delaypipe    = 4,
    bridge_cram1_req_fifo.wrsync_delaypipe    = 4,
    bridge_cram1_req_fifo.use_eab             = "ON";

// ============================================================
// clk_cpu side -- consume requests, drive controller A
// ============================================================
localparam ST_IDLE      = 2'd0;
localparam ST_ISSUE     = 2'd1;
localparam ST_WAIT      = 2'd2;

reg [1:0]  cpu_state;
reg        cpu_is_write;       // latched from popped req
reg [21:0] cpu_lat_addr;
reg [31:0] cpu_lat_wdata;
reg        cpu_in_flight;      // 1 from issue until completion (reads: rdata_valid; writes: req_busy fall)
reg        cpu_busy_seen;      // for write completion: req_busy went HIGH at least once

always @(posedge clk_cpu) begin
    if (!reset_n) begin
        cpu_state      <= ST_IDLE;
        cpu_is_write   <= 1'b0;
        cpu_lat_addr   <= 22'b0;
        cpu_lat_wdata  <= 32'b0;
        cpu_in_flight  <= 1'b0;
        cpu_busy_seen  <= 1'b0;
        req_fifo_rdreq <= 1'b0;
        req_rd         <= 1'b0;
        req_wr         <= 1'b0;
        req_addr       <= 22'b0;
        req_wdata      <= 32'b0;
    end else begin
        req_fifo_rdreq <= 1'b0;
        req_rd         <= 1'b0;
        req_wr         <= 1'b0;

        case (cpu_state)
            ST_IDLE: begin
                if (!req_fifo_empty && !req_busy) begin
                    /* showahead FIFO: q already presents head entry */
                    cpu_is_write   <= req_fifo_out[54];
                    cpu_lat_addr   <= req_fifo_out[53:32];
                    cpu_lat_wdata  <= req_fifo_out[31:0];
                    req_fifo_rdreq <= 1'b1;
                    cpu_in_flight  <= 1'b1;
                    cpu_busy_seen  <= 1'b0;
                    cpu_state      <= ST_ISSUE;
                end
            end
            ST_ISSUE: begin
                /* Pulse the controller's word_rd / word_wr command. */
                req_addr  <= cpu_lat_addr;
                req_wdata <= cpu_lat_wdata;
                if (cpu_is_write) req_wr <= 1'b1;
                else              req_rd <= 1'b1;
                cpu_state <= ST_WAIT;
            end
            ST_WAIT: begin
                if (cpu_is_write) begin
                    /* Write completion: edge-detect req_busy (HIGH then LOW). */
                    if (!cpu_busy_seen && req_busy)
                        cpu_busy_seen <= 1'b1;
                    else if (cpu_busy_seen && !req_busy) begin
                        cpu_in_flight <= 1'b0;
                        cpu_state     <= ST_IDLE;
                    end
                end else begin
                    /* Read completion: wait for rdata_valid pulse. */
                    if (req_rdata_valid) begin
                        cpu_in_flight <= 1'b0;
                        cpu_state     <= ST_IDLE;
                    end
                end
            end
            default: cpu_state <= ST_IDLE;
        endcase
    end
end

// ============================================================
// Response FIFO (clk_cpu → clk_74a) -- only used for reads
// ============================================================
reg         resp_fifo_wrreq;
reg  [31:0] resp_fifo_in;
wire        resp_fifo_full;
wire [31:0] resp_fifo_out;
wire        resp_fifo_empty;
reg         resp_fifo_rdreq;

always @(posedge clk_cpu) begin
    if (!reset_n) begin
        resp_fifo_wrreq <= 1'b0;
        resp_fifo_in    <= 32'b0;
    end else begin
        resp_fifo_wrreq <= 1'b0;
        if (cpu_state == ST_WAIT && !cpu_is_write && req_rdata_valid && !resp_fifo_full) begin
            resp_fifo_in    <= req_rdata;
            resp_fifo_wrreq <= 1'b1;
        end
    end
end

dcfifo bridge_cram1_resp_fifo (
    .wrclk   (clk_cpu),
    .rdclk   (clk_bridge),
    .data    (resp_fifo_in),
    .wrreq   (resp_fifo_wrreq),
    .q       (resp_fifo_out),
    .rdreq   (resp_fifo_rdreq),
    .rdempty (resp_fifo_empty),
    .wrfull  (resp_fifo_full),
    .aclr    (~reset_n)
);
defparam bridge_cram1_resp_fifo.intended_device_family = "Cyclone V",
    bridge_cram1_resp_fifo.lpm_numwords        = 4,
    bridge_cram1_resp_fifo.lpm_showahead       = "ON",
    bridge_cram1_resp_fifo.lpm_type            = "dcfifo",
    bridge_cram1_resp_fifo.lpm_width           = 32,
    bridge_cram1_resp_fifo.lpm_widthu          = 2,
    bridge_cram1_resp_fifo.overflow_checking   = "ON",
    bridge_cram1_resp_fifo.underflow_checking  = "ON",
    bridge_cram1_resp_fifo.rdsync_delaypipe    = 4,
    bridge_cram1_resp_fifo.wrsync_delaypipe    = 4,
    bridge_cram1_resp_fifo.use_eab             = "ON";

// ============================================================
// clk_74a side -- drain response FIFO, expose latest read data
// ============================================================
always @(posedge clk_bridge) begin
    if (!reset_n) begin
        bridge_rd_data  <= 32'b0;
        resp_fifo_rdreq <= 1'b0;
    end else begin
        resp_fifo_rdreq <= 1'b0;
        if (!resp_fifo_empty) begin
            bridge_rd_data  <= resp_fifo_out;
            resp_fifo_rdreq <= 1'b1;
        end
    end
end

// ============================================================
// Status flags for OS-visible DS_STATUS bits
// ============================================================
// bridge_active = anything outstanding anywhere in the pipeline.
// Two synchronisers from clk_cpu → clk_bridge: cpu_in_flight + the
// FIFO empties.  Conservative: report active until everything drains.
reg [2:0] cpu_in_flight_sync;
always @(posedge clk_bridge) begin
    if (!reset_n)
        cpu_in_flight_sync <= 3'b000;
    else
        cpu_in_flight_sync <= {cpu_in_flight_sync[1:0], cpu_in_flight};
end

// Uses write-side empty flag (req_fifo_wrempty) so this stays in
// clk_74a domain — req_fifo_empty is the clk_cpu side flag and
// using it here would cross domains untimed.  wrempty is conservative
// (it can be slightly stale, but that just defers "drained" — never
// signals false-empty).
assign bridge_active = !req_fifo_wrempty | cpu_in_flight_sync[2] | !resp_fifo_empty
                     | detect_wr | detect_rd;
assign bridge_wr_idle = !bridge_active;

endmodule
