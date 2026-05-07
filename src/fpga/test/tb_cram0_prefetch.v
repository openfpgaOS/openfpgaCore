`default_nettype none

module tb_cram0_prefetch (
    input  wire        clk,
    input  wire        reset_n,

    input  wire        start,
    input  wire [31:0] start_bridge_addr,
    input  wire [31:0] start_length,
    input  wire        bridge_owner,

    input  wire        bridge_rd_pulse,
    input  wire [31:0] bridge_addr,
    output wire        bridge_hit,
    output wire [31:0] bridge_rd_data,

    output wire        active,
    output wire        busy,
    output wire        ready,

    output wire        burst_rd,
    output wire [21:0] burst_addr,
    output wire [5:0]  burst_len,
    input  wire [31:0] burst_rdata,
    input  wire        burst_rdata_valid,
    input  wire        ctrl_busy
);

cram0_bridge_prefetch dut (
    .clk               (clk),
    .reset_n           (reset_n),
    .start             (start),
    .start_bridge_addr (start_bridge_addr),
    .start_length      (start_length),
    .bridge_owner      (bridge_owner),
    .bridge_rd_pulse   (bridge_rd_pulse),
    .bridge_addr       (bridge_addr),
    .bridge_hit        (bridge_hit),
    .bridge_rd_data    (bridge_rd_data),
    .active            (active),
    .busy              (busy),
    .ready             (ready),
    .burst_rd          (burst_rd),
    .burst_addr        (burst_addr),
    .burst_len         (burst_len),
    .burst_rdata       (burst_rdata),
    .burst_rdata_valid (burst_rdata_valid),
    .ctrl_busy         (ctrl_busy)
);

endmodule
