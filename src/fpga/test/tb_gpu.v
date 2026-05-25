//
// Verilator Testbench: GPU Core
//
// Instantiates: gpu_core -> gpu_tex_cache (internal)
// Provides:
//   - Simplified SDRAM model (flat memory, AXI4 read+write)
//   - SRAM model (word-level, for Z-buffer)
//   - MMIO register interface (from C++ harness)
//   - Framebuffer readback port (from C++ harness)
//

`default_nettype none

module tb_gpu (
    input  wire        clk,
    input  wire        reset_n,

    // MMIO register interface (driven from C++)
    input  wire        reg_wr,
    input  wire [3:0]  reg_addr,
    input  wire [31:0] reg_wdata,
    output wire [31:0] reg_rdata,

    // Status outputs
    output wire        busy,
    output wire [31:0] fence_reached,
    output wire [5:0]  dbg_state,
    output wire [5:0]  dbg_setup_step,
    output wire [31:0] dbg_aux,
    output wire [31:0] dbg_frag,
    output reg  [31:0] dbg_aw_count,
    output reg  [31:0] dbg_aw_burst_count,
    output reg  [7:0]  dbg_aw_max_len,

    // CMD_FLIP side-port (observed by C++ harness for the drain test)
    output wire        gpu_swap_req,
    output wire [1:0]  gpu_swap_idx,

    // External diag inputs — driven by C++ harness for the drain test
    input  wire        slave_swap_pending,

    // SDRAM backdoor write (preload textures, ring buffer, etc.)
    input  wire        bd_we,
    input  wire [23:0] bd_addr,    // word address
    input  wire [31:0] bd_wdata,

    // SDRAM backdoor read (verify framebuffer)
    input  wire [23:0] bd_rd_addr,
    output wire [31:0] bd_rd_data
);

// ============================================================
// GPU AXI4 Read Master signals
// ============================================================
wire        gpu_rd_arvalid;
wire        gpu_rd_arready;
wire [31:0] gpu_rd_araddr;
wire [7:0]  gpu_rd_arlen;
wire        gpu_rd_rvalid;
wire [31:0] gpu_rd_rdata;
wire        gpu_rd_rlast;

// ============================================================
// GPU AXI4 Write Master signals
// ============================================================
wire        gpu_wr_awvalid;
wire        gpu_wr_awready;
wire [31:0] gpu_wr_awaddr;
wire [7:0]  gpu_wr_awlen;
wire        gpu_wr_wvalid;
wire        gpu_wr_wready;
wire [31:0] gpu_wr_wdata;
wire [3:0]  gpu_wr_wstrb;
wire        gpu_wr_wlast;
wire        gpu_wr_bvalid;

// ============================================================
// GPU SRAM scratch signals
// ============================================================
wire        gpu_sram_rd;
wire        gpu_sram_wr;
wire        gpu_sram_rd_half;
wire        gpu_sram_rd_hi;
wire [21:0] gpu_sram_addr;
wire [31:0] gpu_sram_wdata;
wire [3:0]  gpu_sram_wstrb;
wire [31:0] gpu_sram_rdata;
wire        gpu_sram_busy;
wire        gpu_sram_rdata_valid;

// ============================================================
// GPU Core
// ============================================================
gpu_core gpu (
    .clk(clk),
    .reset_n(reset_n),
    .gpu_enable(1'b1),
    // AXI4 read
    .m_rd_arvalid(gpu_rd_arvalid),
    .m_rd_arready(gpu_rd_arready),
    .m_rd_araddr(gpu_rd_araddr),
    .m_rd_arlen(gpu_rd_arlen),
    .m_rd_rvalid(gpu_rd_rvalid),
    .m_rd_rdata(gpu_rd_rdata),
    .m_rd_rlast(gpu_rd_rlast),
    // AXI4 write
    .m_wr_awvalid(gpu_wr_awvalid),
    .m_wr_awready(gpu_wr_awready),
    .m_wr_awaddr(gpu_wr_awaddr),
    .m_wr_awlen(gpu_wr_awlen),
    .m_wr_wvalid(gpu_wr_wvalid),
    .m_wr_wready(gpu_wr_wready),
    .m_wr_wdata(gpu_wr_wdata),
    .m_wr_wstrb(gpu_wr_wstrb),
    .m_wr_wlast(gpu_wr_wlast),
    .m_wr_bvalid(gpu_wr_bvalid),
    // SRAM scratch
    .sram_rd(gpu_sram_rd),
    .sram_wr(gpu_sram_wr),
    .sram_rd_half(gpu_sram_rd_half),
    .sram_rd_hi(gpu_sram_rd_hi),
    .sram_addr(gpu_sram_addr),
    .sram_wdata(gpu_sram_wdata),
    .sram_wstrb(gpu_sram_wstrb),
    .sram_rdata(gpu_sram_rdata),
    .sram_busy(gpu_sram_busy),
    .sram_rdata_valid(gpu_sram_rdata_valid),
    // CMD_FLIP side-port
    .gpu_swap_req(gpu_swap_req),
    .gpu_swap_idx(gpu_swap_idx),
    // External backpressure input (only the drain test drives it from
    // the C++ harness).
    .slave_swap_pending(slave_swap_pending),
    // MMIO
    .reg_wr(reg_wr),
    .reg_addr(reg_addr),
    .reg_wdata(reg_wdata),
    .reg_rdata(reg_rdata),
    // Status
    .busy(busy),
    .fence_reached(fence_reached),
    .dbg_state(dbg_state),
    .dbg_setup_step(dbg_setup_step),
    .dbg_aux(dbg_aux),
    .dbg_frag(dbg_frag)
);

// ============================================================
// SRAM Model (word-level, GPU private scratch)
// ============================================================
reg [31:0] sram_mem [0:65535];
reg        sram_busy_r;
reg        sram_rvalid_r;
reg [1:0]  sram_delay;
reg        sram_op_read;
reg        sram_half_r;
reg        sram_hi_r;
reg [15:0] sram_addr_r;
reg [31:0] sram_wdata_r;
reg [3:0]  sram_wstrb_r;

assign gpu_sram_busy = sram_busy_r;
assign gpu_sram_rdata_valid = sram_rvalid_r;
assign gpu_sram_rdata = sram_half_r
                       ? (sram_hi_r
                          ? {sram_mem[sram_addr_r][31:16], 16'd0}
                          : {16'd0, sram_mem[sram_addr_r][15:0]})
                       : sram_mem[sram_addr_r];

always @(posedge clk) begin
    if (!reset_n) begin
        sram_busy_r   <= 1'b0;
        sram_rvalid_r <= 1'b0;
        sram_delay    <= 2'd0;
        sram_op_read  <= 1'b0;
        sram_half_r    <= 1'b0;
        sram_hi_r      <= 1'b0;
        sram_addr_r   <= 16'd0;
        sram_wdata_r  <= 32'd0;
        sram_wstrb_r  <= 4'd0;
    end else begin
        sram_rvalid_r <= 1'b0;
        if (!sram_busy_r && (gpu_sram_rd || gpu_sram_wr)) begin
            sram_busy_r  <= 1'b1;
            sram_delay   <= 2'd2;
            sram_op_read <= gpu_sram_rd;
            sram_half_r   <= gpu_sram_rd && gpu_sram_rd_half;
            sram_hi_r     <= gpu_sram_rd_hi;
            sram_addr_r  <= gpu_sram_addr[15:0];
            sram_wdata_r <= gpu_sram_wdata;
            sram_wstrb_r <= gpu_sram_wstrb;
        end else if (sram_busy_r) begin
            if (sram_delay != 2'd0) begin
                sram_delay <= sram_delay - 2'd1;
            end else begin
                if (sram_op_read) begin
                    sram_rvalid_r <= 1'b1;
                end else begin
                    if (sram_wstrb_r[0]) sram_mem[sram_addr_r][7:0]   <= sram_wdata_r[7:0];
                    if (sram_wstrb_r[1]) sram_mem[sram_addr_r][15:8]  <= sram_wdata_r[15:8];
                    if (sram_wstrb_r[2]) sram_mem[sram_addr_r][23:16] <= sram_wdata_r[23:16];
                    if (sram_wstrb_r[3]) sram_mem[sram_addr_r][31:24] <= sram_wdata_r[31:24];
                end
                sram_busy_r <= 1'b0;
            end
        end
    end
end

// ============================================================
// Simplified SDRAM Model (flat 1M word = 4MB, fast)
// ============================================================
// Combined read+write AXI4 responder with 2-cycle read latency.

reg [31:0] sdram_mem [0:1048575];  // 1M words = 4MB

// Backdoor write
always @(posedge clk) begin
    if (bd_we)
        sdram_mem[bd_addr[19:0]] <= bd_wdata;
end

// Backdoor read
assign bd_rd_data = sdram_mem[bd_rd_addr[19:0]];

// ---- AXI4 Read responder ----
reg        rd_active;
reg [23:0] rd_addr;
reg [7:0]  rd_beats_left;
reg [1:0]  rd_delay;

assign gpu_rd_arready = !rd_active;
assign gpu_rd_rvalid  = rd_active && (rd_delay == 0);
assign gpu_rd_rdata   = sdram_mem[rd_addr[19:0]];
assign gpu_rd_rlast   = rd_active && (rd_delay == 0) && (rd_beats_left == 0);

always @(posedge clk) begin
    if (!reset_n) begin
        rd_active <= 0;
        rd_delay <= 0;
    end else begin
        if (!rd_active && gpu_rd_arvalid) begin
            rd_active     <= 1;
            rd_addr       <= gpu_rd_araddr[25:2];
            rd_beats_left <= gpu_rd_arlen;
            rd_delay      <= 2;  // 2-cycle initial latency
        end else if (rd_active) begin
            if (rd_delay > 0) begin
                rd_delay <= rd_delay - 1;
            end else begin
                // Beat delivered
                if (rd_beats_left == 0) begin
                    rd_active <= 0;
                end else begin
                    rd_addr       <= rd_addr + 1;
                    rd_beats_left <= rd_beats_left - 1;
                    rd_delay      <= 0;  // back-to-back beats
                end
            end
        end
    end
end

// ---- AXI4 Write responder ----
reg        wr_aw_active;
reg [23:0] wr_addr;
reg        wr_b_pending;

assign gpu_wr_awready = !wr_aw_active && !wr_b_pending;
assign gpu_wr_wready  = wr_aw_active;
assign gpu_wr_bvalid  = wr_b_pending;

always @(posedge clk) begin
    if (!reset_n) begin
        wr_aw_active <= 0;
        wr_b_pending <= 0;
        dbg_aw_count <= 0;
        dbg_aw_burst_count <= 0;
        dbg_aw_max_len <= 0;
    end else begin
        // Accept AW
        if (!wr_aw_active && !wr_b_pending && gpu_wr_awvalid) begin
            wr_aw_active <= 1;
            wr_addr      <= gpu_wr_awaddr[25:2];
            dbg_aw_count <= dbg_aw_count + 32'd1;
            if (gpu_wr_awlen != 8'd0)
                dbg_aw_burst_count <= dbg_aw_burst_count + 32'd1;
            if (gpu_wr_awlen > dbg_aw_max_len)
                dbg_aw_max_len <= gpu_wr_awlen;
        end

        // Accept W beats
        if (wr_aw_active && gpu_wr_wvalid) begin
            // Byte-strobe write
            if (gpu_wr_wstrb[0]) sdram_mem[wr_addr[19:0]][7:0]   <= gpu_wr_wdata[7:0];
            if (gpu_wr_wstrb[1]) sdram_mem[wr_addr[19:0]][15:8]  <= gpu_wr_wdata[15:8];
            if (gpu_wr_wstrb[2]) sdram_mem[wr_addr[19:0]][23:16] <= gpu_wr_wdata[23:16];
            if (gpu_wr_wstrb[3]) sdram_mem[wr_addr[19:0]][31:24] <= gpu_wr_wdata[31:24];

            if (gpu_wr_wlast) begin
                wr_aw_active <= 0;
                wr_b_pending <= 1;
            end else begin
                wr_addr <= wr_addr + 1;
            end
        end

        // B response consumed (single cycle)
        if (wr_b_pending)
            wr_b_pending <= 0;
    end
end

endmodule
