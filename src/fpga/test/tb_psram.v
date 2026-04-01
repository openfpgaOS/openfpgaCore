//
// Verilator Testbench: PSRAM Controller Stack (word-level interface)
//
// Instantiates: psram_backend_model
// Tests word-level interface (matching cpu_system outputs):
// burst reads, single-word reads/writes, busy handshake patterns,
// address decode (CRAM0/CRAM1/SRAM routing), byte strobes.
//

`default_nettype none

module tb_psram (
    input  wire        clk,
    input  wire        reset_n,

    // Word-level interface (matching cpu_system outputs)
    input  wire        m_rd,
    input  wire        m_wr,
    input  wire [25:0] m_addr,           // byte_addr[27:2]
    input  wire [31:0] m_wdata,
    input  wire [3:0]  m_wstrb,
    input  wire        m_burst_rd,
    input  wire [5:0]  m_burst_len,
    output wire [31:0] m_rdata,
    output wire        m_busy,
    output wire        m_rdata_valid,
    output wire        m_burst_rdata_valid,
    output wire [31:0] m_burst_rdata,

    // Burst write interface
    input  wire        m_burst_wr,
    input  wire [5:0]  m_burst_wr_len,
    input  wire [31:0] m_burst_wdata,
    input  wire [3:0]  m_burst_wstrb,
    output wire        m_burst_wdata_next,

    output wire        busy
);

assign busy = m_busy;

// Behavioral PSRAM backend model — directly wired
psram_backend_model backend (
    .clk(clk), .reset_n(reset_n),
    .psram_rd(m_rd), .psram_wr(m_wr),
    .psram_addr(m_addr), .psram_wdata(m_wdata), .psram_wstrb(m_wstrb),
    .psram_rdata(m_rdata), .psram_busy(m_busy),
    .psram_rdata_valid(m_rdata_valid),
    .psram_burst_rd(m_burst_rd), .psram_burst_len(m_burst_len),
    .psram_burst_rdata_valid(m_burst_rdata_valid),
    .psram_burst_rdata(m_burst_rdata),
    .psram_burst_wr(m_burst_wr), .psram_burst_wr_len(m_burst_wr_len),
    .psram_burst_wdata(m_burst_wdata), .psram_burst_wstrb(m_burst_wstrb),
    .psram_burst_wdata_next(m_burst_wdata_next)
);

endmodule
