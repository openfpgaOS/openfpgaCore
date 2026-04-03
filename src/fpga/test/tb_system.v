//
// Verilator System Testbench: VexiiRiscv CPU + Memory Stack
//
// Full CPU system running real firmware in simulation:
//   VexiiRiscv → cpu_system → { BRAM (I-fetch + local), SDRAM stack }
//
// The C++ harness loads firmware into BRAM via backdoor, releases reset,
// and monitors a result register at a known BRAM address.
//
// Result protocol:
//   BRAM[0x3FF00] = test status (0 = running, 1 = pass, 2 = fail)
//   BRAM[0x3FF04] = test result value
//   BRAM[0x3FF08] = cycle counter (written by firmware)
//

`default_nettype none

module tb_system (
    input wire clk,
    input wire reset_n,

    // Backdoor BRAM access (for firmware loading and result checking)
    input  wire        bd_we,
    input  wire [15:0] bd_addr,
    input  wire [31:0] bd_wdata,
    output wire [31:0] bd_rdata,

    // UART output (from CPU writes to 0x4F000004)
    output wire        uart_tx_valid,
    output wire [7:0]  uart_tx_byte,

    // SDRAM backdoor (for OS binary preload)
    input  wire        sd_bd_we,
    input  wire [23:0] sd_bd_addr,    // 24-bit word address
    input  wire [31:0] sd_bd_wdata,

    // PSRAM/CRAM0 backdoor (for OS binary preload)
    input  wire        ps_bd_we,
    input  wire [23:0] ps_bd_addr,
    input  wire [31:0] ps_bd_wdata,

    // Debug: expose fetch and LSU addresses for PC tracing
    output wire [31:0] dbg_fetch_addr,
    output wire        dbg_fetch_valid,
    output wire [31:0] dbg_lsu_addr,
    output wire        dbg_lsu_valid
);

// ============================================================
// CPU system (VexiiRiscv + address decode + bus routing)
// ============================================================

// SDRAM AXI4 bus
wire        sdram_arvalid, sdram_arready;
wire [31:0] sdram_araddr;
wire [7:0]  sdram_arlen;
wire        sdram_rvalid, sdram_rlast;
wire [31:0] sdram_rdata;
wire [1:0]  sdram_rresp;
wire        sdram_awvalid, sdram_awready;
wire [31:0] sdram_awaddr;
wire [7:0]  sdram_awlen;
wire        sdram_wvalid, sdram_wready, sdram_wlast;
wire [31:0] sdram_wdata;
wire [3:0]  sdram_wstrb;
wire        sdram_bvalid;
wire [1:0]  sdram_bresp;

// PSRAM AXI4 bus (active but not connected to memory for now — just ack)
wire        psram_arvalid, psram_arready;
wire [31:0] psram_araddr;
wire [7:0]  psram_arlen;
wire        psram_rvalid, psram_rlast;
wire [31:0] psram_rdata;
wire [1:0]  psram_rresp;
wire        psram_awvalid, psram_awready;
wire [31:0] psram_awaddr;
wire [7:0]  psram_awlen;
wire        psram_wvalid, psram_wready, psram_wlast;
wire [31:0] psram_wdata;
wire [3:0]  psram_wstrb;
wire        psram_bvalid;
wire [1:0]  psram_bresp;

// PSRAM memory model: 16 MB backing store for CRAM0 (OS + app code)
// AXI4 addresses arrive as 0x30xxxxxx (CRAM0) or 0x31xxxxxx (CRAM1)
// from cpu_system. Word address = addr[23:2].
reg [31:0] psram_mem [0:4194303];  // 16 MW = 16 MB (CRAM0 only)

// Backdoor write port for preloading OS binary
always @(posedge clk)
    if (ps_bd_we) psram_mem[ps_bd_addr[21:0]] <= ps_bd_wdata;

// Read FSM
reg psram_r_pending;
reg [7:0] psram_r_cnt, psram_r_len;
reg [31:0] psram_r_addr;
assign psram_arready = !psram_r_pending;
assign psram_rvalid = psram_r_pending;
assign psram_rdata = psram_mem[psram_r_addr[23:2]];
assign psram_rresp = 2'b00;
assign psram_rlast = (psram_r_cnt == psram_r_len);

// Write FSM
reg psram_w_active;
reg [31:0] psram_w_addr;
reg psram_b_pending;
assign psram_awready = !psram_w_active && !psram_b_pending;
assign psram_wready = psram_w_active;
assign psram_bvalid = psram_b_pending;
assign psram_bresp = 2'b00;

always @(posedge clk) begin
    if (!reset_n) begin
        psram_r_pending <= 0;
        psram_r_cnt <= 0;
        psram_r_len <= 0;
        psram_r_addr <= 0;
        psram_w_active <= 0;
        psram_w_addr <= 0;
        psram_b_pending <= 0;
    end else begin
        // Read handling
        if (psram_arvalid && psram_arready) begin
            psram_r_pending <= 1;
            psram_r_cnt <= 0;
            psram_r_len <= psram_arlen;
            psram_r_addr <= psram_araddr;
        end else if (psram_r_pending) begin
            if (psram_rlast) begin
                psram_r_pending <= 0;
            end else begin
                psram_r_cnt <= psram_r_cnt + 1;
                psram_r_addr <= psram_r_addr + 4;
            end
        end

        // Write handling
        if (psram_awvalid && psram_awready) begin
            psram_w_active <= 1;
            psram_w_addr <= psram_awaddr;
        end
        if (psram_w_active && psram_wvalid) begin
            // Byte-strobe write
            if (psram_wstrb[0]) psram_mem[psram_w_addr[23:2]][7:0]   <= psram_wdata[7:0];
            if (psram_wstrb[1]) psram_mem[psram_w_addr[23:2]][15:8]  <= psram_wdata[15:8];
            if (psram_wstrb[2]) psram_mem[psram_w_addr[23:2]][23:16] <= psram_wdata[23:16];
            if (psram_wstrb[3]) psram_mem[psram_w_addr[23:2]][31:24] <= psram_wdata[31:24];
            psram_w_addr <= psram_w_addr + 4;
            if (psram_wlast) begin
                psram_w_active <= 0;
                psram_b_pending <= 1;
            end
        end
        if (psram_b_pending)
            psram_b_pending <= 0;
    end
end

// Local AXI4 bus (BRAM)
wire        local_arvalid, local_arready;
wire [31:0] local_araddr;
wire [7:0]  local_arlen;
wire        local_rvalid, local_rlast;
wire [31:0] local_rdata;
wire [1:0]  local_rresp;
wire        local_awvalid, local_awready;
wire [31:0] local_awaddr;
wire [7:0]  local_awlen;
wire        local_wvalid, local_wready, local_wlast;
wire [31:0] local_wdata;
wire [3:0]  local_wstrb;
wire        local_bvalid;
wire [1:0]  local_bresp;
wire        local_rready = 1'b1;  // Always accept read data

// I-fetch AXI4 bus (from cpu_system's fetch port → BRAM)
wire        fetch_arvalid, fetch_arready;
wire [31:0] fetch_araddr;
wire [7:0]  fetch_arlen;
wire        fetch_rvalid, fetch_rlast;
wire [31:0] fetch_rdata;
wire [1:0]  fetch_rresp;

cpu_system cpu (
    .clk(clk), .reset_n(reset_n),
    // SDRAM
    .m_sdram_arvalid(sdram_arvalid), .m_sdram_arready(sdram_arready),
    .m_sdram_araddr(sdram_araddr), .m_sdram_arlen(sdram_arlen),
    .m_sdram_rvalid(sdram_rvalid), .m_sdram_rdata(sdram_rdata),
    .m_sdram_rresp(sdram_rresp), .m_sdram_rlast(sdram_rlast),
    .m_sdram_awvalid(sdram_awvalid), .m_sdram_awready(sdram_awready),
    .m_sdram_awaddr(sdram_awaddr), .m_sdram_awlen(sdram_awlen),
    .m_sdram_wvalid(sdram_wvalid), .m_sdram_wready(sdram_wready),
    .m_sdram_wdata(sdram_wdata), .m_sdram_wstrb(sdram_wstrb),
    .m_sdram_wlast(sdram_wlast),
    .m_sdram_bvalid(sdram_bvalid), .m_sdram_bresp(sdram_bresp),
    // PSRAM
    .m_psram_arvalid(psram_arvalid), .m_psram_arready(psram_arready),
    .m_psram_araddr(psram_araddr), .m_psram_arlen(psram_arlen),
    .m_psram_rvalid(psram_rvalid), .m_psram_rdata(psram_rdata),
    .m_psram_rresp(psram_rresp), .m_psram_rlast(psram_rlast),
    .m_psram_awvalid(psram_awvalid), .m_psram_awready(psram_awready),
    .m_psram_awaddr(psram_awaddr), .m_psram_awlen(psram_awlen),
    .m_psram_wvalid(psram_wvalid), .m_psram_wready(psram_wready),
    .m_psram_wdata(psram_wdata), .m_psram_wstrb(psram_wstrb),
    .m_psram_wlast(psram_wlast),
    .m_psram_bvalid(psram_bvalid), .m_psram_bresp(psram_bresp),
    // Local
    .m_local_arvalid(local_arvalid), .m_local_arready(local_arready),
    .m_local_araddr(local_araddr), .m_local_arlen(local_arlen),
    .m_local_rvalid(local_rvalid), .m_local_rdata(local_rdata),
    .m_local_rresp(local_rresp), .m_local_rlast(local_rlast),
    .m_local_awvalid(local_awvalid), .m_local_awready(local_awready),
    .m_local_awaddr(local_awaddr), .m_local_awlen(local_awlen),
    .m_local_wvalid(local_wvalid), .m_local_wready(local_wready),
    .m_local_wdata(local_wdata), .m_local_wstrb(local_wstrb),
    .m_local_wlast(local_wlast),
    .m_local_bvalid(local_bvalid), .m_local_bresp(local_bresp),
    .int_m_external(1'b0),
    .int_m_timer(1'b0)
);

// ============================================================
// BRAM: serves both I-fetch and local (uncached) access
// ============================================================
bram_model #(.ADDR_BITS(16)) bram (
    .clk(clk), .reset_n(reset_n),
    // I-fetch port
    .fetch_ar_valid(fetch_arvalid), .fetch_ar_ready(fetch_arready),
    .fetch_ar_addr(fetch_araddr), .fetch_ar_len(fetch_arlen),
    .fetch_r_valid(fetch_rvalid), .fetch_r_ready(fetch_rready),
    .fetch_r_data(fetch_rdata), .fetch_r_resp(fetch_rresp),
    .fetch_r_last(fetch_rlast),
    // Local port
    .local_ar_valid(local_arvalid), .local_ar_ready(local_arready),
    .local_ar_addr(local_araddr), .local_ar_len(local_arlen),
    .local_r_valid(local_rvalid), .local_r_ready(1'b1),
    .local_r_data(local_rdata), .local_r_resp(local_rresp),
    .local_r_last(local_rlast),
    .local_aw_valid(local_awvalid), .local_aw_ready(local_awready),
    .local_aw_addr(local_awaddr), .local_aw_len(local_awlen),
    .local_w_valid(local_wvalid), .local_w_ready(local_wready),
    .local_w_data(local_wdata), .local_w_strb(local_wstrb),
    .local_w_last(local_wlast),
    .local_b_valid(local_bvalid), .local_b_ready(1'b1),
    .local_b_resp(local_bresp),
    // Backdoor
    .bd_we(bd_we), .bd_addr(bd_addr), .bd_wdata(bd_wdata), .bd_rdata(bd_rdata),
    // UART
    .uart_tx_valid(uart_tx_valid), .uart_tx_byte(uart_tx_byte)
);

// ============================================================
// SDRAM: fast behavioral model (~4 cycle latency)
// ============================================================
sdram_fast_model sdram_fast (
    .clk(clk), .reset_n(reset_n),
    .s_axi_arvalid(sdram_arvalid), .s_axi_arready(sdram_arready),
    .s_axi_araddr(sdram_araddr), .s_axi_arlen(sdram_arlen),
    .s_axi_rvalid(sdram_rvalid), .s_axi_rready(1'b1),
    .s_axi_rdata(sdram_rdata), .s_axi_rresp(sdram_rresp), .s_axi_rlast(sdram_rlast),
    .s_axi_awvalid(sdram_awvalid), .s_axi_awready(sdram_awready),
    .s_axi_awaddr(sdram_awaddr), .s_axi_awlen(sdram_awlen),
    .s_axi_wvalid(sdram_wvalid), .s_axi_wready(sdram_wready),
    .s_axi_wdata(sdram_wdata), .s_axi_wstrb(sdram_wstrb), .s_axi_wlast(sdram_wlast),
    .s_axi_bvalid(sdram_bvalid), .s_axi_bready(1'b1), .s_axi_bresp(sdram_bresp),
    .bd_we(sd_bd_we), .bd_word_addr(sd_bd_addr), .bd_wdata(sd_bd_wdata)
);

// ============================================================
// I-fetch routing: cpu_system exposes fetch bus, route to BRAM
// ============================================================
// cpu_system internally routes FetchL1Axi4 to the local bus for
// address range 0x00000000-0x0003FFFF. The BRAM's fetch port
// handles these requests separately from local R/W.

// Note: cpu_system routes fetch to the local bus (same as BRAM).
// The BRAM model has separate fetch and local ports.
// We need to check if cpu_system exposes the fetch bus separately
// or merges it with local. Let me check...

// Actually, cpu_system merges fetch into the local bus based on
// address decode. The BRAM's local port handles both fetch and
// local accesses. The separate fetch port on bram_model is not
// used in this configuration — tie it off.

// For now, tie off the BRAM fetch port (cpu_system handles routing)
assign fetch_arvalid = 0;

// Debug: expose bus activity for PC tracing
assign dbg_fetch_addr = local_arvalid ? local_araddr :
                         sdram_arvalid ? sdram_araddr : 32'h0;
assign dbg_fetch_valid = local_arvalid | sdram_arvalid;
assign dbg_lsu_addr = local_awvalid ? local_awaddr :
                       sdram_awvalid ? sdram_awaddr : 32'h0;
assign dbg_lsu_valid = local_awvalid | sdram_awvalid;
assign fetch_araddr = 0;
assign fetch_arlen = 0;
wire fetch_rready = 1;

endmodule
