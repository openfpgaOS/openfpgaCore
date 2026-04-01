//
// VexiiRiscv CPU System — Wishbone + Word-level bus routing
// - VexiiRiscv RISC-V CPU with 3-bus architecture:
//   FetchL1Wishbone (I-cache, read-only, burst via CTI)
//   LsuL1Wishbone   (D-cache, read+write, burst via CTI)
//   LsuPlugin IO    (uncached, single-beat cmd/rsp)
// - Per-bus address decode → {SDRAM, PSRAM, Local}
// - All downstream paths: direct word-level (no AXI4, no Wishbone forwarding)
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
// VexiiRiscv Wishbone signals
// ============================================

wire reset = ~reset_n;

// FetchL1Wishbone (I-cache, read-only)
wire        fetch_cyc;
wire        fetch_stb;
reg         fetch_ack;
wire        fetch_we;        // Always 0 for fetch
wire [29:0] fetch_adr;       // Word address
reg  [31:0] fetch_dat_miso;
wire [31:0] fetch_dat_mosi;  // Unused for fetch
wire [3:0]  fetch_sel;
reg         fetch_err;
wire [2:0]  fetch_cti;
wire [1:0]  fetch_bte;

// LsuL1Wishbone (D-cache, read+write)
wire        lsu_cyc;
wire        lsu_stb;
reg         lsu_ack;
wire        lsu_we;
wire [29:0] lsu_adr;
reg  [31:0] lsu_dat_miso;
wire [31:0] lsu_dat_mosi;
wire [3:0]  lsu_sel;
reg         lsu_err;
wire [2:0]  lsu_cti;
wire [1:0]  lsu_bte;

// LsuPlugin IO bus (simple cmd/rsp, uncached data) — unchanged
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

    // LsuL1Wishbone (D-cache)
    .LsuL1WishbonePlugin_logic_bus_CYC(lsu_cyc),
    .LsuL1WishbonePlugin_logic_bus_STB(lsu_stb),
    .LsuL1WishbonePlugin_logic_bus_ACK(lsu_ack),
    .LsuL1WishbonePlugin_logic_bus_WE(lsu_we),
    .LsuL1WishbonePlugin_logic_bus_ADR(lsu_adr),
    .LsuL1WishbonePlugin_logic_bus_DAT_MISO(lsu_dat_miso),
    .LsuL1WishbonePlugin_logic_bus_DAT_MOSI(lsu_dat_mosi),
    .LsuL1WishbonePlugin_logic_bus_SEL(lsu_sel),
    .LsuL1WishbonePlugin_logic_bus_ERR(lsu_err),
    .LsuL1WishbonePlugin_logic_bus_CTI(lsu_cti),
    .LsuL1WishbonePlugin_logic_bus_BTE(lsu_bte),

    // FetchL1Wishbone (I-cache, read-only)
    .FetchL1WishbonePlugin_logic_bus_CYC(fetch_cyc),
    .FetchL1WishbonePlugin_logic_bus_STB(fetch_stb),
    .FetchL1WishbonePlugin_logic_bus_ACK(fetch_ack),
    .FetchL1WishbonePlugin_logic_bus_WE(fetch_we),
    .FetchL1WishbonePlugin_logic_bus_ADR(fetch_adr),
    .FetchL1WishbonePlugin_logic_bus_DAT_MISO(fetch_dat_miso),
    .FetchL1WishbonePlugin_logic_bus_DAT_MOSI(fetch_dat_mosi),
    .FetchL1WishbonePlugin_logic_bus_SEL(fetch_sel),
    .FetchL1WishbonePlugin_logic_bus_ERR(fetch_err),
    .FetchL1WishbonePlugin_logic_bus_CTI(fetch_cti),
    .FetchL1WishbonePlugin_logic_bus_BTE(fetch_bte),

    // LsuPlugin IO bus (uncached data) — unchanged
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

// Wishbone: CYC+STB = active request. No separate read/write arbitration needed.
wire fetch_req = fetch_cyc & fetch_stb;
wire lsu_req   = lsu_cyc & lsu_stb;

wire lsu_grant   = lsu_req & (~fetch_req | ~last_grant_lsu);
wire fetch_grant = fetch_req & ~lsu_grant;
wire io_grant    = io_cmd_valid & ~lsu_grant & ~fetch_grant;

// ============================================
// Wishbone address → byte address reconstruction
// ============================================
// ADR is 30-bit word address. Byte address = {ADR, 2'b00}.
wire [31:0] fetch_byte_addr = {fetch_adr, 2'b00};
wire [31:0] lsu_byte_addr   = {lsu_adr, 2'b00};

// Burst length: cache line = 64B = 16 words → burst_len = 15
// CTI=010 (incrementing burst) or CTI=111 (end of burst) → burst mode
// CTI=000 (classic) → single beat
wire fetch_is_burst = (fetch_cti == 3'b010);
wire lsu_is_burst   = (lsu_cti == 3'b010);
wire [3:0] wb_burst_len = 4'd15;  // 16 beats per cache line

// ============================================
// Memory access FSM
// ============================================
localparam FSM_IDLE           = 4'd0;
localparam FSM_SDRAM_RD       = 4'd1;
localparam FSM_SDRAM_WR       = 4'd2;
localparam FSM_SDRAM_WR_BURST = 4'd3;
localparam FSM_SDRAM_WR_DONE  = 4'd4;
localparam FSM_PSRAM_BURST    = 4'd5;
localparam FSM_PSRAM_RD       = 4'd6;
localparam FSM_PSRAM_WR       = 4'd7;
localparam FSM_LOCAL_RD       = 4'd8;
localparam FSM_LOCAL_WR       = 4'd9;

reg [3:0] fsm_state;

// Latched request fields
reg [31:0] req_addr_r;
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
        burst_len_r <= 0;
        burst_count <= 0;
        last_grant_lsu <= 0;
        target_mem <= TGT_SDRAM;

        cmd_issued <= 0;
        started <= 0;
        psram_started <= 0;
        issue_wait <= 0;
        wr_busy_seen <= 0;

        fetch_ack <= 0;
        fetch_dat_miso <= 0;
        fetch_err <= 0;

        lsu_ack <= 0;
        lsu_dat_miso <= 0;
        lsu_err <= 0;

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
        fetch_ack <= 0;
        fetch_err <= 0;
        lsu_ack <= 0;
        lsu_err <= 0;
        io_cmd_ready <= 0;
        io_rsp_valid <= 0;
        m_psram_burst_rd <= 0;

        case (fsm_state)

        // ============================================
        // IDLE: Accept new Wishbone or IO request
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

            if (lsu_grant) begin
                active_bus <= BUS_LSU;
                is_write_r <= lsu_we;
                req_addr_r <= lsu_byte_addr;
                burst_len_r <= lsu_is_burst ? wb_burst_len : 8'd0;
                burst_count <= 0;
                last_grant_lsu <= 1;

                if (lsu_we) begin
                    // LSU write
                    if (lsu_byte_addr[31:26] == 6'b000100 || lsu_byte_addr[31:26] == 6'b010100) begin
                        target_mem <= TGT_SDRAM;
                        m_sdram_wr <= 1;
                        m_sdram_addr <= lsu_byte_addr[25:2];
                        m_sdram_wdata <= lsu_dat_mosi;
                        m_sdram_wstrb <= lsu_sel;
                        m_sdram_burst_wr_len <= lsu_is_burst ? wb_burst_len : 4'd0;
                        fsm_state <= FSM_SDRAM_WR;
                    end else if (lsu_byte_addr[31:27] == 5'b00110) begin
                        target_mem <= TGT_PSRAM;
                        m_psram_addr <= lsu_byte_addr[27:2];
                        m_psram_wdata <= lsu_dat_mosi;
                        m_psram_wstrb <= lsu_sel;
                        fsm_state <= FSM_PSRAM_WR;
                    end else begin
                        target_mem <= TGT_LOCAL;
                        m_local_wr <= 1;
                        m_local_addr <= lsu_byte_addr;
                        m_local_wdata <= lsu_dat_mosi;
                        m_local_wstrb <= lsu_sel;
                        fsm_state <= FSM_LOCAL_WR;
                    end
                end else begin
                    // LSU read
                    if (lsu_byte_addr[31:26] == 6'b000100 || lsu_byte_addr[31:26] == 6'b010100) begin
                        target_mem <= TGT_SDRAM;
                        m_sdram_rd <= 1;
                        m_sdram_addr <= lsu_byte_addr[25:2];
                        m_sdram_burst_len <= lsu_is_burst ? wb_burst_len : 4'd0;
                        fsm_state <= FSM_SDRAM_RD;
                    end else if (lsu_byte_addr[31:27] == 5'b00110) begin
                        target_mem <= TGT_PSRAM;
                        m_psram_addr <= lsu_byte_addr[27:2];
                        if (lsu_byte_addr[27:24] == 4'hA) begin
                            fsm_state <= FSM_PSRAM_RD;
                        end else begin
                            m_psram_burst_rd <= 1;
                            m_psram_burst_len <= lsu_is_burst ? {2'b0, wb_burst_len} : 6'd0;
                            fsm_state <= FSM_PSRAM_BURST;
                        end
                    end else begin
                        target_mem <= TGT_LOCAL;
                        m_local_rd <= 1;
                        m_local_addr <= lsu_byte_addr;
                        m_local_burst_len <= lsu_is_burst ? {4'b0, wb_burst_len} : 8'd0;
                        fsm_state <= FSM_LOCAL_RD;
                    end
                end

            end else if (fetch_grant) begin
                active_bus <= BUS_FETCH;
                is_write_r <= 0;
                req_addr_r <= fetch_byte_addr;
                burst_len_r <= fetch_is_burst ? wb_burst_len : 8'd0;
                burst_count <= 0;
                last_grant_lsu <= 0;

                if (fetch_byte_addr[31:26] == 6'b000100 || fetch_byte_addr[31:26] == 6'b010100) begin
                    target_mem <= TGT_SDRAM;
                    m_sdram_rd <= 1;
                    m_sdram_addr <= fetch_byte_addr[25:2];
                    m_sdram_burst_len <= fetch_is_burst ? wb_burst_len : 4'd0;
                    fsm_state <= FSM_SDRAM_RD;
                end else if (fetch_byte_addr[31:28] == 4'b0011) begin
                    target_mem <= TGT_PSRAM;
                    m_psram_addr <= fetch_byte_addr[27:2];
                    if (fetch_byte_addr[27:24] == 4'hA) begin
                        fsm_state <= FSM_PSRAM_RD;
                    end else begin
                        m_psram_burst_rd <= 1;
                        m_psram_burst_len <= fetch_is_burst ? {2'b0, wb_burst_len} : 6'd0;
                        fsm_state <= FSM_PSRAM_BURST;
                    end
                end else begin
                    target_mem <= TGT_LOCAL;
                    m_local_rd <= 1;
                    m_local_addr <= fetch_byte_addr;
                    m_local_burst_len <= fetch_is_burst ? {4'b0, wb_burst_len} : 8'd0;
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
        // SDRAM read: hold rd until accepted, ACK each rdata beat
        // ============================================
        FSM_SDRAM_RD: begin
            if (!cmd_issued) begin
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
                        fetch_ack <= 1;
                        fetch_dat_miso <= m_sdram_rdata;
                    end else if (active_bus == BUS_LSU) begin
                        lsu_ack <= 1;
                        lsu_dat_miso <= m_sdram_rdata;
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
                m_sdram_wdata <= (active_bus == BUS_LSU) ? lsu_dat_mosi : m_sdram_wdata;
                m_sdram_wstrb <= (active_bus == BUS_LSU) ? lsu_sel : m_sdram_wstrb;
                m_sdram_burst_wr_len <= burst_len_r[3:0];
                if (m_sdram_accepted) begin
                    cmd_issued <= 1;
                    started <= 1;
                    m_sdram_wr <= 0;
                    // ACK first beat to Wishbone
                    if (active_bus == BUS_LSU)
                        lsu_ack <= 1;
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
        // SDRAM burst write: ACK each beat, track completion
        // ============================================
        FSM_SDRAM_WR_BURST: begin
            // When SDRAM needs next word, provide data and ACK to CPU
            if (m_sdram_wr_data_next) begin
                burst_count <= burst_count + 1;
                if (active_bus == BUS_LSU && lsu_cyc && lsu_stb) begin
                    lsu_ack <= 1;
                    m_sdram_wdata <= lsu_dat_mosi;
                    m_sdram_wstrb <= lsu_sel;
                end
            end
            if (m_sdram_busy) wr_busy_seen <= 1;
            // SDRAM done: ACK the final beat and go to IDLE.
            // The last beat may not have gotten an ACK via wr_data_next
            // (CPU STB may lag), so ACK it now unconditionally.
            if (wr_busy_seen && !m_sdram_busy) begin
                cmd_issued <= 0;
                started <= 0;
                if (active_bus == BUS_LSU && lsu_cyc && lsu_stb)
                    lsu_ack <= 1;
                else if (active_bus == BUS_IO) begin
                    io_rsp_valid <= 1;
                    io_rsp_data <= 0;
                    io_rsp_error <= 0;
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
                end
                fsm_state <= FSM_IDLE;
            end
        end

        // ============================================
        // PSRAM burst read (CRAM targets): ACK each burst beat
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
                        fetch_ack <= 1;
                        fetch_dat_miso <= m_psram_burst_rdata;
                    end else begin
                        lsu_ack <= 1;
                        lsu_dat_miso <= m_psram_burst_rdata;
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
                        fetch_ack <= 1;
                        fetch_dat_miso <= m_psram_rdata;
                    end else if (active_bus == BUS_LSU) begin
                        lsu_ack <= 1;
                        lsu_dat_miso <= m_psram_rdata;
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
                    else
                        req_addr_r <= req_addr_r + 32'd4;
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
                    m_psram_wdata <= (active_bus == BUS_LSU) ? lsu_dat_mosi : m_psram_wdata;
                    m_psram_wstrb <= (active_bus == BUS_LSU) ? lsu_sel : m_psram_wstrb;
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
                        if (active_bus == BUS_LSU)
                            lsu_ack <= 1;
                        else if (active_bus == BUS_IO) begin
                            io_rsp_valid <= 1;
                            io_rsp_data <= 0;
                            io_rsp_error <= 0;
                        end
                        fsm_state <= FSM_IDLE;
                    end else begin
                        // ACK current beat, advance to next
                        if (active_bus == BUS_LSU)
                            lsu_ack <= 1;
                        req_addr_r <= req_addr_r + 32'd4;
                        // Next beat: CPU will present new STB+DAT_MOSI after ACK
                    end
                end
            end
        end

        // ============================================
        // LOCAL_RD: Wait for rdata_valid beats, ACK each
        // ============================================
        FSM_LOCAL_RD: begin
            m_local_rd <= 0;
            if (m_local_rdata_valid) begin
                if (active_bus == BUS_FETCH) begin
                    fetch_ack <= 1;
                    fetch_dat_miso <= m_local_rdata;
                end else if (active_bus == BUS_LSU) begin
                    lsu_ack <= 1;
                    lsu_dat_miso <= m_local_rdata;
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
        // LOCAL_WR: Wait for wr_done, ACK
        // ============================================
        FSM_LOCAL_WR: begin
            m_local_wr <= 0;
            if (m_local_wr_done) begin
                burst_count <= burst_count + 1;
                if (beat_is_last) begin
                    if (active_bus == BUS_LSU)
                        lsu_ack <= 1;
                    else if (active_bus == BUS_IO) begin
                        io_rsp_valid <= 1;
                        io_rsp_data <= 0;
                        io_rsp_error <= 0;
                    end
                    fsm_state <= FSM_IDLE;
                end else begin
                    // ACK current beat, wait for next STB from CPU
                    if (active_bus == BUS_LSU)
                        lsu_ack <= 1;
                    req_addr_r <= req_addr_r + 32'd4;
                    // Next beat: CPU provides new STB after ACK, we issue another wr
                end
            end
        end

        default: fsm_state <= FSM_IDLE;

        endcase
    end
end

endmodule
