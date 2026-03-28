//
// Verilator Testbench: PSRAM Controller Stack
//
// Instantiates: axi_psram_slave -> psram_backend_model
// Tests the AXI wrapper FSM, address decode (CRAM0/CRAM1/SRAM routing),
// burst reads, single-word reads/writes, and busy handshake patterns.
//

`default_nettype none

module tb_psram (
    input  wire        clk,
    input  wire        reset_n,

    // AXI4 Slave interface
    input  wire        s_axi_arvalid,
    output wire        s_axi_arready,
    input  wire [31:0] s_axi_araddr,
    input  wire [7:0]  s_axi_arlen,

    output wire        s_axi_rvalid,
    output wire [31:0] s_axi_rdata,
    output wire [1:0]  s_axi_rresp,
    output wire        s_axi_rlast,

    input  wire        s_axi_awvalid,
    output wire        s_axi_awready,
    input  wire [31:0] s_axi_awaddr,
    input  wire [7:0]  s_axi_awlen,

    input  wire        s_axi_wvalid,
    output wire        s_axi_wready,
    input  wire [31:0] s_axi_wdata,
    input  wire [3:0]  s_axi_wstrb,
    input  wire        s_axi_wlast,

    output wire        s_axi_bvalid,
    output wire [1:0]  s_axi_bresp,

    output wire        busy
);

// Word interface: axi_psram_slave <-> psram_backend_model
wire        psram_rd;
wire        psram_wr;
wire [25:0] psram_addr;
wire [31:0] psram_wdata;
wire [3:0]  psram_wstrb;
wire [31:0] psram_rdata;
wire        psram_busy;
wire        psram_rdata_valid;

// Burst read interface
wire        psram_burst_rd;
wire [5:0]  psram_burst_len;
wire        psram_burst_rdata_valid;
wire [31:0] psram_burst_rdata;

assign busy = psram_busy;

// AXI PSRAM Slave (real RTL)
axi_psram_slave slave (
    .clk(clk), .reset_n(reset_n),
    .s_axi_arvalid(s_axi_arvalid), .s_axi_arready(s_axi_arready),
    .s_axi_araddr(s_axi_araddr), .s_axi_arlen(s_axi_arlen),
    .s_axi_rvalid(s_axi_rvalid), .s_axi_rready(1'b1),
    .s_axi_rdata(s_axi_rdata), .s_axi_rresp(s_axi_rresp), .s_axi_rlast(s_axi_rlast),
    .s_axi_awvalid(s_axi_awvalid), .s_axi_awready(s_axi_awready),
    .s_axi_awaddr(s_axi_awaddr), .s_axi_awlen(s_axi_awlen),
    .s_axi_wvalid(s_axi_wvalid), .s_axi_wready(s_axi_wready),
    .s_axi_wdata(s_axi_wdata), .s_axi_wstrb(s_axi_wstrb), .s_axi_wlast(s_axi_wlast),
    .s_axi_bvalid(s_axi_bvalid), .s_axi_bready(1'b1), .s_axi_bresp(s_axi_bresp),
    .psram_rd(psram_rd), .psram_wr(psram_wr),
    .psram_addr(psram_addr), .psram_wdata(psram_wdata), .psram_wstrb(psram_wstrb),
    .psram_rdata(psram_rdata), .psram_busy(psram_busy),
    .psram_rdata_valid(psram_rdata_valid),
    .psram_burst_rd(psram_burst_rd), .psram_burst_len(psram_burst_len),
    .psram_burst_rdata_valid(psram_burst_rdata_valid),
    .psram_burst_rdata(psram_burst_rdata)
);

// Behavioral PSRAM backend model
psram_backend_model backend (
    .clk(clk), .reset_n(reset_n),
    .psram_rd(psram_rd), .psram_wr(psram_wr),
    .psram_addr(psram_addr), .psram_wdata(psram_wdata), .psram_wstrb(psram_wstrb),
    .psram_rdata(psram_rdata), .psram_busy(psram_busy),
    .psram_rdata_valid(psram_rdata_valid),
    .psram_burst_rd(psram_burst_rd), .psram_burst_len(psram_burst_len),
    .psram_burst_rdata_valid(psram_burst_rdata_valid),
    .psram_burst_rdata(psram_burst_rdata)
);

endmodule
