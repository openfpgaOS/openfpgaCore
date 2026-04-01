//
// VexiiRiscv CPU System — Word-level bus routing
// - VexiiRiscv RISC-V CPU with 3-bus architecture:
//   FetchL1Axi4 (I-cache, read-only)
//   LsuL1Axi4   (D-cache, read+write)
//   LsuPlugin IO (uncached, single-beat cmd/rsp)
// - Per-bus address decode → {SDRAM, PSRAM, Local}
// - SDRAM/PSRAM: direct word-level output (no AXI4 intermediate)
// - Local: AXI4 master output (to axi_periph_slave)
//

`default_nettype none

module cpu_system (
    input wire clk,           // CPU clock (100 MHz)
    input wire reset_n,

    // SDRAM word-level master interface (to word_sdram_arbiter)
    output reg         m_sdram_rd,
    output reg         m_sdram_wr,
    output reg  [23:0] m_sdram_addr,
    output reg  [31:0] m_sdram_wdata,
    output reg  [3:0]  m_sdram_wstrb,
    output reg  [3:0]  m_sdram_burst_len,
    output reg  [3:0]  m_sdram_burst_wr_len,
    input  wire [31:0] m_sdram_rdata,
    input  wire        m_sdram_busy,
    input  wire        m_sdram_accepted,
    input  wire        m_sdram_rdata_valid,
    input  wire        m_sdram_wr_data_next,

    // PSRAM word-level master interface (direct to psram_controller)
    output reg         m_psram_rd,
    output reg         m_psram_wr,
    output reg  [25:0] m_psram_addr,
    output reg  [31:0] m_psram_wdata,
    output reg  [3:0]  m_psram_wstrb,
    input  wire [31:0] m_psram_rdata,
    input  wire        m_psram_busy,
    input  wire        m_psram_rdata_valid,
    output reg         m_psram_burst_rd,
    output reg  [5:0]  m_psram_burst_len,
    input  wire        m_psram_burst_rdata_valid,
    input  wire [31:0] m_psram_burst_rdata,

    // Local peripheral word-level master interface (to periph_slave)
    output reg         m_local_rd,
    output reg         m_local_wr,
    output reg  [31:0] m_local_addr,
    output reg  [31:0] m_local_wdata,
    output reg  [3:0]  m_local_wstrb,
    output reg  [7:0]  m_local_burst_len,

    input  wire [31:0] m_local_rdata,
    input  wire        m_local_rdata_valid,
    input  wire        m_local_rdata_last,
    input  wire        m_local_wr_done,
    input  wire        m_local_busy
);

// ============================================
// VexiiRiscv AXI4 signals
// ============================================

// Active-high reset for VexiiRiscv
wire reset = ~reset_n;

// FetchL1Axi4 (I-cache, read-only): AR + R channels
wire        fetch_ar_valid;
reg         fetch_ar_ready;
wire [31:0] fetch_ar_addr;
wire [0:0]  fetch_ar_id;
wire [7:0]  fetch_ar_len;

reg         fetch_r_valid;
wire        fetch_r_ready;
reg  [31:0] fetch_r_data;
reg  [0:0]  fetch_r_id;
wire [1:0]  fetch_r_resp = 2'b00;
reg         fetch_r_last;

// LsuL1Axi4 (D-cache, full AXI4): AW + W + B + AR + R channels
wire        lsu_aw_valid;
reg         lsu_aw_ready;
wire [31:0] lsu_aw_addr;
wire [0:0]  lsu_aw_id;
wire [7:0]  lsu_aw_len;

wire        lsu_w_valid;
reg         lsu_w_ready;
wire [31:0] lsu_w_data;
wire [3:0]  lsu_w_strb;
wire        lsu_w_last;

reg         lsu_b_valid;
wire        lsu_b_ready;
reg  [0:0]  lsu_b_id;
wire [1:0]  lsu_b_resp = 2'b00;

wire        lsu_ar_valid;
reg         lsu_ar_ready;
wire [31:0] lsu_ar_addr;
wire [0:0]  lsu_ar_id;
wire [7:0]  lsu_ar_len;

reg         lsu_r_valid;
wire        lsu_r_ready;
reg  [31:0] lsu_r_data;
reg  [0:0]  lsu_r_id;
wire [1:0]  lsu_r_resp = 2'b00;
reg         lsu_r_last;

// LsuPlugin IO bus (simple cmd/rsp, uncached data)
wire        io_cmd_valid;
reg         io_cmd_ready;
wire        io_cmd_write;
wire [31:0] io_cmd_addr;
wire [31:0] io_cmd_data;
wire [3:0]  io_cmd_mask;

reg         io_rsp_valid;
reg         io_rsp_error;
reg  [31:0] io_rsp_data;

// 64-bit rdtime counter for PrivilegedPlugin
reg [63:0] rdtime_counter;
always @(posedge clk or posedge reset) begin
    if (reset)
        rdtime_counter <= 64'd0;
    else
        rdtime_counter <= rdtime_counter + 64'd1;
end

// ============================================
// VexiiRiscv CPU instantiation
// ============================================
VexiiRiscv cpu (
    .clk(clk),
    .reset(reset),

    .PrivilegedPlugin_logic_rdtime(rdtime_counter),
    .PrivilegedPlugin_logic_harts_0_int_m_timer(1'b0),
    .PrivilegedPlugin_logic_harts_0_int_m_software(1'b0),
    .PrivilegedPlugin_logic_harts_0_int_m_external(1'b0),

    // LsuL1Axi4 (D-cache)
    .LsuL1Axi4Plugin_logic_axi_aw_valid(lsu_aw_valid),
    .LsuL1Axi4Plugin_logic_axi_aw_ready(lsu_aw_ready),
    .LsuL1Axi4Plugin_logic_axi_aw_payload_addr(lsu_aw_addr),
    .LsuL1Axi4Plugin_logic_axi_aw_payload_id(lsu_aw_id),
    .LsuL1Axi4Plugin_logic_axi_aw_payload_len(lsu_aw_len),
    .LsuL1Axi4Plugin_logic_axi_aw_payload_size(),
    .LsuL1Axi4Plugin_logic_axi_aw_payload_burst(),
    .LsuL1Axi4Plugin_logic_axi_aw_payload_cache(),
    .LsuL1Axi4Plugin_logic_axi_aw_payload_prot(),

    .LsuL1Axi4Plugin_logic_axi_w_valid(lsu_w_valid),
    .LsuL1Axi4Plugin_logic_axi_w_ready(lsu_w_ready),
    .LsuL1Axi4Plugin_logic_axi_w_payload_data(lsu_w_data),
    .LsuL1Axi4Plugin_logic_axi_w_payload_strb(lsu_w_strb),
    .LsuL1Axi4Plugin_logic_axi_w_payload_last(lsu_w_last),

    .LsuL1Axi4Plugin_logic_axi_b_valid(lsu_b_valid),
    .LsuL1Axi4Plugin_logic_axi_b_ready(lsu_b_ready),
    .LsuL1Axi4Plugin_logic_axi_b_payload_id(lsu_b_id),
    .LsuL1Axi4Plugin_logic_axi_b_payload_resp(lsu_b_resp),

    .LsuL1Axi4Plugin_logic_axi_ar_valid(lsu_ar_valid),
    .LsuL1Axi4Plugin_logic_axi_ar_ready(lsu_ar_ready),
    .LsuL1Axi4Plugin_logic_axi_ar_payload_addr(lsu_ar_addr),
    .LsuL1Axi4Plugin_logic_axi_ar_payload_id(lsu_ar_id),
    .LsuL1Axi4Plugin_logic_axi_ar_payload_len(lsu_ar_len),
    .LsuL1Axi4Plugin_logic_axi_ar_payload_size(),
    .LsuL1Axi4Plugin_logic_axi_ar_payload_burst(),
    .LsuL1Axi4Plugin_logic_axi_ar_payload_cache(),
    .LsuL1Axi4Plugin_logic_axi_ar_payload_prot(),

    .LsuL1Axi4Plugin_logic_axi_r_valid(lsu_r_valid),
    .LsuL1Axi4Plugin_logic_axi_r_ready(lsu_r_ready),
    .LsuL1Axi4Plugin_logic_axi_r_payload_data(lsu_r_data),
    .LsuL1Axi4Plugin_logic_axi_r_payload_id(lsu_r_id),
    .LsuL1Axi4Plugin_logic_axi_r_payload_resp(lsu_r_resp),
    .LsuL1Axi4Plugin_logic_axi_r_payload_last(lsu_r_last),

    // FetchL1Axi4 (I-cache, read-only)
    .FetchL1Axi4Plugin_logic_axi_ar_valid(fetch_ar_valid),
    .FetchL1Axi4Plugin_logic_axi_ar_ready(fetch_ar_ready),
    .FetchL1Axi4Plugin_logic_axi_ar_payload_addr(fetch_ar_addr),
    .FetchL1Axi4Plugin_logic_axi_ar_payload_id(fetch_ar_id),
    .FetchL1Axi4Plugin_logic_axi_ar_payload_len(fetch_ar_len),
    .FetchL1Axi4Plugin_logic_axi_ar_payload_size(),
    .FetchL1Axi4Plugin_logic_axi_ar_payload_burst(),
    .FetchL1Axi4Plugin_logic_axi_ar_payload_cache(),
    .FetchL1Axi4Plugin_logic_axi_ar_payload_prot(),

    .FetchL1Axi4Plugin_logic_axi_r_valid(fetch_r_valid),
    .FetchL1Axi4Plugin_logic_axi_r_ready(fetch_r_ready),
    .FetchL1Axi4Plugin_logic_axi_r_payload_data(fetch_r_data),
    .FetchL1Axi4Plugin_logic_axi_r_payload_id(fetch_r_id),
    .FetchL1Axi4Plugin_logic_axi_r_payload_resp(fetch_r_resp),
    .FetchL1Axi4Plugin_logic_axi_r_payload_last(fetch_r_last),

    // LsuPlugin IO bus (uncached data)
    .LsuPlugin_logic_bus_cmd_valid(io_cmd_valid),
    .LsuPlugin_logic_bus_cmd_ready(io_cmd_ready),
    .LsuPlugin_logic_bus_cmd_payload_write(io_cmd_write),
    .LsuPlugin_logic_bus_cmd_payload_address(io_cmd_addr),
    .LsuPlugin_logic_bus_cmd_payload_data(io_cmd_data),
    .LsuPlugin_logic_bus_cmd_payload_size(),
    .LsuPlugin_logic_bus_cmd_payload_mask(io_cmd_mask),
    .LsuPlugin_logic_bus_cmd_payload_io(),
    .LsuPlugin_logic_bus_cmd_payload_fromHart(),
    .LsuPlugin_logic_bus_cmd_payload_uopId(),
    .LsuPlugin_logic_bus_rsp_valid(io_rsp_valid),
    .LsuPlugin_logic_bus_rsp_payload_error(io_rsp_error),
    .LsuPlugin_logic_bus_rsp_payload_data(io_rsp_data)
);

// ============================================
// Request arbitration
// ============================================
localparam BUS_NONE  = 2'd0;
localparam BUS_FETCH = 2'd1;
localparam BUS_LSU   = 2'd2;
localparam BUS_IO    = 2'd3;

reg last_grant_lsu;

wire fetch_req = fetch_ar_valid;
wire lsu_rd_req = lsu_ar_valid;
wire lsu_wr_req = lsu_aw_valid;
wire lsu_req = lsu_rd_req | lsu_wr_req;

// Priority: LSU > Fetch with round-robin, IO lowest
wire lsu_grant = lsu_req & (~fetch_req | ~last_grant_lsu);
wire fetch_grant = fetch_req & ~lsu_grant;
wire lsu_rd_grant = lsu_grant & lsu_rd_req;
wire lsu_wr_grant = lsu_grant & ~lsu_rd_req;
wire io_grant = io_cmd_valid & ~lsu_grant & ~fetch_grant;

// ============================================
// Memory access FSM
// ============================================
localparam FSM_IDLE           = 4'd0;
// SDRAM word-level states
localparam FSM_SDRAM_RD       = 4'd1;  // Hold rd until accepted, stream rdata
localparam FSM_SDRAM_WR       = 4'd2;  // Hold wr until accepted
localparam FSM_SDRAM_WR_BURST = 4'd3;  // Stream burst write via wr_data_next
localparam FSM_SDRAM_WR_DONE  = 4'd4;  // Wait for single write completion
// PSRAM word-level states
localparam FSM_PSRAM_BURST    = 4'd5;  // CRAM burst read stream
localparam FSM_PSRAM_RD       = 4'd6;  // SRAM single-word read
localparam FSM_PSRAM_WR       = 4'd7;  // PSRAM write (single word per command)
// Local word-level states
localparam FSM_LOCAL_RD       = 4'd8;  // Wait for rdata_valid beats
localparam FSM_LOCAL_WR       = 4'd9;  // Wait for wr_done
// Shared
localparam FSM_WR_NEXT        = 4'd10;

reg [3:0] fsm_state;

// Latched request fields
reg [31:0] req_addr_r;
reg [31:0] req_wdata_r;
reg [3:0]  req_wstrb_r;
reg [0:0]  req_id_r;
reg [1:0]  active_bus;
reg        is_write_r;

// Burst tracking
reg [7:0]  burst_len_r;
reg [7:0]  burst_count;

// Memory target
localparam TGT_SDRAM = 2'd0;
localparam TGT_PSRAM = 2'd1;
localparam TGT_LOCAL = 2'd2;
reg [1:0] target_mem;

// Word-level handshake tracking
reg        cmd_issued;
reg        started;
reg        psram_started;
reg [7:0]  issue_wait;
reg        wr_busy_seen;
reg        is_sram_target;  // SRAM (no burst) vs CRAM (burst)

wire beat_is_last = (burst_count == burst_len_r);

// ============================================
// Main FSM
// ============================================
always @(posedge clk or posedge reset) begin
    if (reset) begin
        fsm_state <= FSM_IDLE;
        active_bus <= BUS_NONE;
        is_write_r <= 0;
        req_addr_r <= 0;
        req_wdata_r <= 0;
        req_wstrb_r <= 0;
        req_id_r <= 0;
        burst_len_r <= 0;
        burst_count <= 0;
        last_grant_lsu <= 0;
        target_mem <= TGT_SDRAM;

        cmd_issued <= 0;
        started <= 0;
        psram_started <= 0;
        issue_wait <= 0;
        wr_busy_seen <= 0;
        is_sram_target <= 0;

        fetch_ar_ready <= 0;
        fetch_r_valid <= 0;
        fetch_r_data <= 0;
        fetch_r_id <= 0;
        fetch_r_last <= 0;

        lsu_aw_ready <= 0;
        lsu_w_ready <= 0;
        lsu_ar_ready <= 0;
        lsu_r_valid <= 0;
        lsu_r_data <= 0;
        lsu_r_id <= 0;
        lsu_r_last <= 0;
        lsu_b_valid <= 0;
        lsu_b_id <= 0;

        io_cmd_ready <= 0;
        io_rsp_valid <= 0;
        io_rsp_error <= 0;
        io_rsp_data <= 0;

        m_sdram_rd <= 0;
        m_sdram_wr <= 0;
        m_sdram_addr <= 0;
        m_sdram_wdata <= 0;
        m_sdram_wstrb <= 0;
        m_sdram_burst_len <= 0;
        m_sdram_burst_wr_len <= 0;

        m_psram_rd <= 0;
        m_psram_wr <= 0;
        m_psram_addr <= 0;
        m_psram_wdata <= 0;
        m_psram_wstrb <= 0;
        m_psram_burst_rd <= 0;
        m_psram_burst_len <= 0;

        m_local_rd <= 0;
        m_local_wr <= 0;
        m_local_addr <= 0;
        m_local_wdata <= 0;
        m_local_wstrb <= 0;
        m_local_burst_len <= 0;
    end else begin
        // Defaults: deassert single-cycle pulses
        fetch_ar_ready <= 0;
        fetch_r_valid <= 0;
        lsu_aw_ready <= 0;
        lsu_w_ready <= 0;
        lsu_ar_ready <= 0;
        lsu_r_valid <= 0;
        lsu_b_valid <= 0;
        io_cmd_ready <= 0;
        io_rsp_valid <= 0;
        m_psram_burst_rd <= 0;

        case (fsm_state)

        // ============================================
        // IDLE: Accept new request, decode target
        // ============================================
        FSM_IDLE: begin
            m_sdram_rd <= 0;
            m_sdram_wr <= 0;
            m_psram_rd <= 0;
            m_psram_wr <= 0;
            m_local_rd <= 0;
            m_local_wr <= 0;
            cmd_issued <= 0;
            started <= 0;
            psram_started <= 0;
            issue_wait <= 0;
            wr_busy_seen <= 0;

            if (lsu_rd_grant) begin
                lsu_ar_ready <= 1;
                active_bus <= BUS_LSU;
                is_write_r <= 0;
                req_addr_r <= lsu_ar_addr;
                req_id_r <= lsu_ar_id;
                burst_len_r <= lsu_ar_len;
                burst_count <= 0;
                last_grant_lsu <= 1;

                if (lsu_ar_addr[31:26] == 6'b000100 || lsu_ar_addr[31:26] == 6'b010100) begin
                    target_mem <= TGT_SDRAM;
                    m_sdram_rd <= 1;
                    m_sdram_addr <= lsu_ar_addr[25:2];
                    m_sdram_burst_len <= lsu_ar_len[3:0];
                    fsm_state <= FSM_SDRAM_RD;
                end else if (lsu_ar_addr[31:27] == 5'b00110) begin
                    target_mem <= TGT_PSRAM;
                    is_sram_target <= (lsu_ar_addr[27:24] == 4'hA);
                    m_psram_addr <= lsu_ar_addr[27:2];
                    if (lsu_ar_addr[27:24] == 4'hA) begin
                        fsm_state <= FSM_PSRAM_RD;
                    end else begin
                        m_psram_burst_rd <= 1;
                        m_psram_burst_len <= lsu_ar_len[5:0];
                        fsm_state <= FSM_PSRAM_BURST;
                    end
                end else begin
                    target_mem <= TGT_LOCAL;
                    m_local_rd <= 1;
                    m_local_addr <= lsu_ar_addr;
                    m_local_burst_len <= lsu_ar_len;
                    fsm_state <= FSM_LOCAL_RD;
                end

            end else if (lsu_wr_grant) begin
                lsu_aw_ready <= 1;
                active_bus <= BUS_LSU;
                is_write_r <= 1;
                req_addr_r <= lsu_aw_addr;
                req_id_r <= lsu_aw_id;
                burst_len_r <= lsu_aw_len;
                burst_count <= 0;
                last_grant_lsu <= 1;

                if (lsu_aw_addr[31:26] == 6'b000100 || lsu_aw_addr[31:26] == 6'b010100)
                    target_mem <= TGT_SDRAM;
                else if (lsu_aw_addr[31:27] == 5'b00110)
                    target_mem <= TGT_PSRAM;
                else
                    target_mem <= TGT_LOCAL;

                if (lsu_w_valid) begin
                    lsu_w_ready <= 1;
                    req_wdata_r <= lsu_w_data;
                    req_wstrb_r <= lsu_w_strb;

                    if (lsu_aw_addr[31:26] == 6'b000100 || lsu_aw_addr[31:26] == 6'b010100) begin
                        m_sdram_wr <= 1;
                        m_sdram_addr <= lsu_aw_addr[25:2];
                        m_sdram_wdata <= lsu_w_data;
                        m_sdram_wstrb <= lsu_w_strb;
                        m_sdram_burst_wr_len <= lsu_aw_len[3:0];
                        fsm_state <= FSM_SDRAM_WR;
                    end else if (lsu_aw_addr[31:27] == 5'b00110) begin
                        m_psram_addr <= lsu_aw_addr[27:2];
                        m_psram_wdata <= lsu_w_data;
                        m_psram_wstrb <= lsu_w_strb;
                        fsm_state <= FSM_PSRAM_WR;
                    end else begin
                        m_local_wr <= 1;
                        m_local_addr <= lsu_aw_addr;
                        m_local_wdata <= lsu_w_data;
                        m_local_wstrb <= lsu_w_strb;
                        fsm_state <= FSM_LOCAL_WR;
                    end
                end else begin
                    fsm_state <= FSM_WR_NEXT;
                end

            end else if (fetch_grant) begin
                fetch_ar_ready <= 1;
                active_bus <= BUS_FETCH;
                is_write_r <= 0;
                req_addr_r <= fetch_ar_addr;
                req_id_r <= fetch_ar_id;
                burst_len_r <= fetch_ar_len;
                burst_count <= 0;
                last_grant_lsu <= 0;

                if (fetch_ar_addr[31:26] == 6'b000100 || fetch_ar_addr[31:26] == 6'b010100) begin
                    target_mem <= TGT_SDRAM;
                    m_sdram_rd <= 1;
                    m_sdram_addr <= fetch_ar_addr[25:2];
                    m_sdram_burst_len <= fetch_ar_len[3:0];
                    fsm_state <= FSM_SDRAM_RD;
                end else if (fetch_ar_addr[31:28] == 4'b0011) begin
                    target_mem <= TGT_PSRAM;
                    is_sram_target <= (fetch_ar_addr[27:24] == 4'hA);
                    m_psram_addr <= fetch_ar_addr[27:2];
                    if (fetch_ar_addr[27:24] == 4'hA) begin
                        fsm_state <= FSM_PSRAM_RD;
                    end else begin
                        m_psram_burst_rd <= 1;
                        m_psram_burst_len <= fetch_ar_len[5:0];
                        fsm_state <= FSM_PSRAM_BURST;
                    end
                end else begin
                    target_mem <= TGT_LOCAL;
                    m_local_rd <= 1;
                    m_local_addr <= fetch_ar_addr;
                    m_local_burst_len <= fetch_ar_len;
                    fsm_state <= FSM_LOCAL_RD;
                end

            end else if (io_grant) begin
                io_cmd_ready <= 1;
                active_bus <= BUS_IO;
                burst_len_r <= 0;
                burst_count <= 0;

                if (io_cmd_addr[31:26] == 6'b010100)
                    target_mem <= TGT_SDRAM;
                else if (io_cmd_addr[31:27] == 5'b00111)
                    target_mem <= TGT_PSRAM;
                else
                    target_mem <= TGT_LOCAL;

                if (io_cmd_write) begin
                    is_write_r <= 1;
                    req_addr_r <= io_cmd_addr;
                    req_wdata_r <= io_cmd_data;
                    req_wstrb_r <= io_cmd_mask;

                    if (io_cmd_addr[31:26] == 6'b010100) begin
                        m_sdram_wr <= 1;
                        m_sdram_addr <= io_cmd_addr[25:2];
                        m_sdram_wdata <= io_cmd_data;
                        m_sdram_wstrb <= io_cmd_mask;
                        m_sdram_burst_wr_len <= 0;
                        fsm_state <= FSM_SDRAM_WR;
                    end else if (io_cmd_addr[31:27] == 5'b00111) begin
                        m_psram_addr <= io_cmd_addr[27:2];
                        m_psram_wdata <= io_cmd_data;
                        m_psram_wstrb <= io_cmd_mask;
                        fsm_state <= FSM_PSRAM_WR;
                    end else begin
                        m_local_wr <= 1;
                        m_local_addr <= io_cmd_addr;
                        m_local_wdata <= io_cmd_data;
                        m_local_wstrb <= io_cmd_mask;
                        fsm_state <= FSM_LOCAL_WR;
                    end
                end else begin
                    is_write_r <= 0;
                    req_addr_r <= io_cmd_addr;

                    if (io_cmd_addr[31:26] == 6'b010100) begin
                        m_sdram_rd <= 1;
                        m_sdram_addr <= io_cmd_addr[25:2];
                        m_sdram_burst_len <= 0;
                        fsm_state <= FSM_SDRAM_RD;
                    end else if (io_cmd_addr[31:27] == 5'b00111) begin
                        is_sram_target <= 1;  // IO uncached PSRAM = single-word
                        m_psram_addr <= io_cmd_addr[27:2];
                        fsm_state <= FSM_PSRAM_RD;
                    end else begin
                        m_local_rd <= 1;
                        m_local_addr <= io_cmd_addr;
                        m_local_burst_len <= 0;
                        fsm_state <= FSM_LOCAL_RD;
                    end
                end
            end
        end

        // ============================================
        // SDRAM read: hold rd until accepted, forward rdata beats
        // ============================================
        FSM_SDRAM_RD: begin
            if (!cmd_issued) begin
                // Hold rd (already set in IDLE or re-assert if retrying)
                m_sdram_rd <= 1;
                m_sdram_addr <= req_addr_r[25:2];
                m_sdram_burst_len <= burst_len_r[3:0];
                if (m_sdram_accepted) begin
                    cmd_issued <= 1;
                    started <= 1;
                    m_sdram_rd <= 0;
                end
            end else begin
                m_sdram_rd <= 0;
                if (m_sdram_rdata_valid) begin
                    if (active_bus == BUS_FETCH) begin
                        fetch_r_valid <= 1;
                        fetch_r_data <= m_sdram_rdata;
                        fetch_r_id <= req_id_r;
                        fetch_r_last <= beat_is_last;
                    end else if (active_bus == BUS_LSU) begin
                        lsu_r_valid <= 1;
                        lsu_r_data <= m_sdram_rdata;
                        lsu_r_id <= req_id_r;
                        lsu_r_last <= beat_is_last;
                    end else begin
                        io_rsp_valid <= 1;
                        io_rsp_data <= m_sdram_rdata;
                        io_rsp_error <= 0;
                    end
                    burst_count <= burst_count + 1;
                    if (beat_is_last)
                        fsm_state <= FSM_IDLE;
                end
            end
        end

        // ============================================
        // SDRAM write: hold wr until accepted
        // ============================================
        FSM_SDRAM_WR: begin
            if (!cmd_issued) begin
                m_sdram_wr <= 1;
                m_sdram_addr <= req_addr_r[25:2];
                m_sdram_wdata <= req_wdata_r;
                m_sdram_wstrb <= req_wstrb_r;
                m_sdram_burst_wr_len <= burst_len_r[3:0];
                if (m_sdram_accepted) begin
                    cmd_issued <= 1;
                    started <= 1;
                    m_sdram_wr <= 0;
                    if (burst_len_r == 0)
                        fsm_state <= FSM_SDRAM_WR_DONE;
                    else begin
                        wr_busy_seen <= 0;
                        fsm_state <= FSM_SDRAM_WR_BURST;
                    end
                end
            end
        end

        // ============================================
        // SDRAM burst write: stream data via wr_data_next
        // ============================================
        FSM_SDRAM_WR_BURST: begin
            if (m_sdram_wr_data_next) begin
                burst_count <= burst_count + 1;
                if (lsu_w_valid) begin
                    lsu_w_ready <= 1;
                    m_sdram_wdata <= lsu_w_data;
                    m_sdram_wstrb <= lsu_w_strb;
                end
            end
            if (m_sdram_busy) wr_busy_seen <= 1;
            if (wr_busy_seen && !m_sdram_busy) begin
                burst_count <= burst_count + 1;
                cmd_issued <= 0;
                started <= 0;
                if (active_bus == BUS_IO) begin
                    io_rsp_valid <= 1;
                    io_rsp_data <= 0;
                    io_rsp_error <= 0;
                end else begin
                    lsu_b_valid <= 1;
                    lsu_b_id <= req_id_r;
                end
                fsm_state <= FSM_IDLE;
            end
        end

        // ============================================
        // SDRAM single write done: wait for !busy
        // ============================================
        FSM_SDRAM_WR_DONE: begin
            if (started && !m_sdram_busy) begin
                cmd_issued <= 0;
                started <= 0;
                if (active_bus == BUS_IO) begin
                    io_rsp_valid <= 1;
                    io_rsp_data <= 0;
                    io_rsp_error <= 0;
                end else begin
                    lsu_b_valid <= 1;
                    lsu_b_id <= req_id_r;
                end
                fsm_state <= FSM_IDLE;
            end
        end

        // ============================================
        // PSRAM burst read (CRAM targets)
        // ============================================
        FSM_PSRAM_BURST: begin
            if (!cmd_issued) begin
                if (!m_psram_busy) begin
                    m_psram_burst_rd <= 1;
                    m_psram_addr <= req_addr_r[27:2];
                    m_psram_burst_len <= burst_len_r[5:0];
                    cmd_issued <= 1;
                end
            end else begin
                if (m_psram_burst_rdata_valid) begin
                    if (active_bus == BUS_FETCH) begin
                        fetch_r_valid <= 1;
                        fetch_r_data <= m_psram_burst_rdata;
                        fetch_r_id <= req_id_r;
                        fetch_r_last <= beat_is_last;
                    end else begin
                        lsu_r_valid <= 1;
                        lsu_r_data <= m_psram_burst_rdata;
                        lsu_r_id <= req_id_r;
                        lsu_r_last <= beat_is_last;
                    end
                    burst_count <= burst_count + 1;
                    if (beat_is_last) begin
                        cmd_issued <= 0;
                        fsm_state <= FSM_IDLE;
                    end
                end
            end
        end

        // ============================================
        // PSRAM single-word read (SRAM targets)
        // ============================================
        FSM_PSRAM_RD: begin
            if (!cmd_issued) begin
                if (!m_psram_busy) begin
                    m_psram_rd <= 1;
                    m_psram_addr <= req_addr_r[27:2];
                    cmd_issued <= 1;
                    psram_started <= 0;
                    issue_wait <= 0;
                end
            end else begin
                if (!psram_started) begin
                    if (m_psram_busy) begin
                        psram_started <= 1;
                        issue_wait <= 0;
                    end else begin
                        issue_wait <= issue_wait + 1;
                        if (&issue_wait) begin
                            cmd_issued <= 0;
                            issue_wait <= 0;
                        end
                    end
                end
                if (m_psram_rdata_valid) begin
                    if (active_bus == BUS_FETCH) begin
                        fetch_r_valid <= 1;
                        fetch_r_data <= m_psram_rdata;
                        fetch_r_id <= req_id_r;
                        fetch_r_last <= beat_is_last;
                    end else if (active_bus == BUS_LSU) begin
                        lsu_r_valid <= 1;
                        lsu_r_data <= m_psram_rdata;
                        lsu_r_id <= req_id_r;
                        lsu_r_last <= beat_is_last;
                    end else begin
                        io_rsp_valid <= 1;
                        io_rsp_data <= m_psram_rdata;
                        io_rsp_error <= 0;
                    end
                    burst_count <= burst_count + 1;
                    cmd_issued <= 0;
                    psram_started <= 0;
                    if (beat_is_last)
                        fsm_state <= FSM_IDLE;
                    else begin
                        req_addr_r <= req_addr_r + 32'd4;
                    end
                end
            end
        end

        // ============================================
        // PSRAM write (single word per command)
        // ============================================
        FSM_PSRAM_WR: begin
            if (!cmd_issued) begin
                if (!m_psram_busy) begin
                    m_psram_wr <= 1;
                    m_psram_addr <= req_addr_r[27:2];
                    m_psram_wdata <= req_wdata_r;
                    m_psram_wstrb <= req_wstrb_r;
                    cmd_issued <= 1;
                    psram_started <= 0;
                    issue_wait <= 0;
                end
            end else begin
                if (!psram_started && m_psram_busy)
                    psram_started <= 1;
                else if (!psram_started) begin
                    issue_wait <= issue_wait + 1;
                    if (&issue_wait) begin
                        cmd_issued <= 0;
                        issue_wait <= 0;
                    end
                end else if (psram_started && !m_psram_busy) begin
                    burst_count <= burst_count + 1;
                    cmd_issued <= 0;
                    psram_started <= 0;
                    if (beat_is_last) begin
                        if (active_bus == BUS_IO) begin
                            io_rsp_valid <= 1;
                            io_rsp_data <= 0;
                            io_rsp_error <= 0;
                        end else begin
                            lsu_b_valid <= 1;
                            lsu_b_id <= req_id_r;
                        end
                        fsm_state <= FSM_IDLE;
                    end else begin
                        req_addr_r <= req_addr_r + 32'd4;
                        fsm_state <= FSM_WR_NEXT;
                    end
                end
            end
        end

        // ============================================
        // LOCAL_RD: Wait for rdata_valid beats from periph_slave
        // ============================================
        FSM_LOCAL_RD: begin
            m_local_rd <= 0;  // Pulse already sent
            if (m_local_rdata_valid) begin
                if (active_bus == BUS_FETCH) begin
                    fetch_r_valid <= 1;
                    fetch_r_data <= m_local_rdata;
                    fetch_r_id <= req_id_r;
                    fetch_r_last <= m_local_rdata_last;
                end else if (active_bus == BUS_LSU) begin
                    lsu_r_valid <= 1;
                    lsu_r_data <= m_local_rdata;
                    lsu_r_id <= req_id_r;
                    lsu_r_last <= m_local_rdata_last;
                end else begin
                    io_rsp_valid <= 1;
                    io_rsp_data <= m_local_rdata;
                    io_rsp_error <= 0;
                end
                burst_count <= burst_count + 1;
                if (m_local_rdata_last)
                    fsm_state <= FSM_IDLE;
            end
        end

        // ============================================
        // LOCAL_WR: Wait for wr_done from periph_slave
        // ============================================
        FSM_LOCAL_WR: begin
            m_local_wr <= 0;  // Pulse already sent
            if (m_local_wr_done) begin
                burst_count <= burst_count + 1;
                if (beat_is_last) begin
                    if (active_bus == BUS_IO) begin
                        io_rsp_valid <= 1;
                        io_rsp_data <= 0;
                        io_rsp_error <= 0;
                    end else begin
                        lsu_b_valid <= 1;
                        lsu_b_id <= req_id_r;
                    end
                    fsm_state <= FSM_IDLE;
                end else begin
                    req_addr_r <= req_addr_r + 32'd4;
                    fsm_state <= FSM_WR_NEXT;
                end
            end
        end

        // ============================================
        // WR_NEXT: Accept next W beat from CPU, route to target
        // ============================================
        FSM_WR_NEXT: begin
            if (lsu_w_valid) begin
                lsu_w_ready <= 1;
                req_wdata_r <= lsu_w_data;
                req_wstrb_r <= lsu_w_strb;

                if (target_mem == TGT_SDRAM) begin
                    m_sdram_wr <= 1;
                    m_sdram_addr <= req_addr_r[25:2];
                    m_sdram_wdata <= lsu_w_data;
                    m_sdram_wstrb <= lsu_w_strb;
                    m_sdram_burst_wr_len <= burst_len_r[3:0];
                    cmd_issued <= 0;
                    started <= 0;
                    fsm_state <= FSM_SDRAM_WR;
                end else if (target_mem == TGT_PSRAM) begin
                    m_psram_wdata <= lsu_w_data;
                    m_psram_wstrb <= lsu_w_strb;
                    cmd_issued <= 0;
                    psram_started <= 0;
                    issue_wait <= 0;
                    fsm_state <= FSM_PSRAM_WR;
                end else begin
                    m_local_wr <= 1;
                    m_local_addr <= req_addr_r;
                    m_local_wdata <= lsu_w_data;
                    m_local_wstrb <= lsu_w_strb;
                    fsm_state <= FSM_LOCAL_WR;
                end
            end
        end

        default: fsm_state <= FSM_IDLE;

        endcase
    end
end

endmodule
