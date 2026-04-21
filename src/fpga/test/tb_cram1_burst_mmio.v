/*
 * tb_cram1_burst_mmio.v — Verilator testbench for cram1_burst_mmio.v
 *
 * The real cram1_controller's burst path has a known sim issue
 * (burst_busy never asserts under the chip_model stimulus), so this
 * testbench stubs the controller with a small FSM that drives the
 * expected saw-busy behaviour: burst_busy rises on the cycle after
 * burst_rd is sampled high, streams 8 beats of sequential word data
 * via burst_q_valid, then drops busy.  That lets us exercise the
 * cram1_burst_mmio FSM + FIFO in isolation.
 */

`default_nettype none

module tb_cram1_burst_mmio (
    input  wire clk,
    input  wire reset_n,

    /* MMIO interface (driven from the C++ harness) */
    input  wire        mmio_addr_wr_pulse,
    input  wire [21:0] mmio_addr_wdata,
    input  wire        mmio_data_rd_pulse,
    output wire        mmio_busy,
    output wire [31:0] mmio_data_q,

    /* Observability of the DUT→stub handshake (for the harness). */
    output wire        dut_burst_rd,
    output wire [21:0] dut_burst_addr,
    output wire [4:0]  dut_burst_len
);

wire        burst_rd;
wire [21:0] burst_addr;
wire [4:0]  burst_len;

reg  [31:0] burst_q;
reg         burst_q_valid;
reg         burst_busy;

assign dut_burst_rd   = burst_rd;
assign dut_burst_addr = burst_addr;
assign dut_burst_len  = burst_len;

cram1_burst_mmio dut (
    .clk                (clk),
    .reset_n            (reset_n),
    .mmio_addr_wr_pulse (mmio_addr_wr_pulse),
    .mmio_addr_wdata    (mmio_addr_wdata),
    .mmio_data_rd_pulse (mmio_data_rd_pulse),
    .mmio_busy          (mmio_busy),
    .mmio_data_q        (mmio_data_q),
    .burst_rd           (burst_rd),
    .burst_addr         (burst_addr),
    .burst_len          (burst_len),
    .burst_q            (burst_q),
    .burst_q_valid      (burst_q_valid),
    .burst_busy         (burst_busy)
);

/* Fake CRAM1 controller stub.
 *
 *   STUB_IDLE    : waiting for burst_rd level-high
 *   STUB_BUSY_N  : busy=HIGH for a couple of cycles to emulate the
 *                  row-activate delay (N cycles before first word).
 *   STUB_STREAM  : drive burst_q + burst_q_valid for N+1 beats, where
 *                  burst_q is (burst_addr + i) so the harness can verify
 *                  the buffer captured the right sequence.
 *   STUB_DRAIN   : busy stays high for 1 extra cycle after the last
 *                  word, then drops. */
localparam [1:0] STUB_IDLE   = 2'd0;
localparam [1:0] STUB_BUSY_N = 2'd1;
localparam [1:0] STUB_STREAM = 2'd2;
localparam [1:0] STUB_DRAIN  = 2'd3;

reg [1:0]  stub_state;
reg [2:0]  stub_delay;      /* row-activate cycle counter */
reg [4:0]  stub_count;      /* words delivered so far */
reg [21:0] stub_addr_reg;
reg [4:0]  stub_len_reg;

always @(posedge clk) begin
    if (!reset_n) begin
        stub_state    <= STUB_IDLE;
        stub_delay    <= 3'd0;
        stub_count    <= 5'd0;
        burst_q       <= 32'd0;
        burst_q_valid <= 1'b0;
        burst_busy    <= 1'b0;
        stub_addr_reg <= 22'd0;
        stub_len_reg  <= 5'd0;
    end else begin
        burst_q_valid <= 1'b0;

        case (stub_state)
            STUB_IDLE: begin
                burst_busy <= 1'b0;
                if (burst_rd) begin
                    /* Latch the request and raise busy next cycle. */
                    stub_addr_reg <= burst_addr;
                    stub_len_reg  <= burst_len;
                    stub_delay    <= 3'd3;   /* 3-cycle row-activate */
                    stub_count    <= 5'd0;
                    burst_busy    <= 1'b1;
                    stub_state    <= STUB_BUSY_N;
                end
            end

            STUB_BUSY_N: begin
                burst_busy <= 1'b1;
                if (stub_delay == 3'd0) begin
                    stub_state <= STUB_STREAM;
                end else begin
                    stub_delay <= stub_delay - 3'd1;
                end
            end

            STUB_STREAM: begin
                burst_busy    <= 1'b1;
                burst_q       <= {10'd0, stub_addr_reg} + {27'd0, stub_count};
                burst_q_valid <= 1'b1;
                if (stub_count == stub_len_reg) begin
                    stub_state <= STUB_DRAIN;
                end else begin
                    stub_count <= stub_count + 5'd1;
                end
            end

            STUB_DRAIN: begin
                /* Keep busy HIGH one extra cycle after the last beat
                 * so the DUT sees busy dropping AFTER the last data
                 * word, not on the same cycle. */
                burst_busy <= 1'b0;
                stub_state <= STUB_IDLE;
            end

            default: stub_state <= STUB_IDLE;
        endcase
    end
end

endmodule
