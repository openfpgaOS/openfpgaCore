//
// VexiiRiscv CPU System — AXI4 bus routing
// - VexiiRiscv RISC-V CPU with 3-bus architecture:
//   FetchL1Axi4 (I-cache, read-only)
//   LsuL1Axi4   (D-cache, read+write)
//   LsuPlugin IO (uncached, single-beat cmd/rsp)
// - Per-bus address decode → {SDRAM, PSRAM, Local} AXI4 masters
//
// All peripheral/local logic (BRAM, colormap, system registers, CDC, terminal,
// DMA/Span/ATM/Audio/Link dispatch) lives in axi_periph_slave.v.
//

`default_nettype none

module cpu_system (
    input wire clk,           // CPU clock (100 MHz)
    input wire reset_n,

    // SDRAM AXI4 master interface (to axi_sdram_slave via core_top)
    output reg         m_sdram_arvalid,
    input  wire        m_sdram_arready,
    output reg  [31:0] m_sdram_araddr,
    output reg  [7:0]  m_sdram_arlen,

    input  wire        m_sdram_rvalid,
    input  wire [31:0] m_sdram_rdata,
    input  wire [1:0]  m_sdram_rresp,
    input  wire        m_sdram_rlast,

    output reg         m_sdram_awvalid,
    input  wire        m_sdram_awready,
    output reg  [31:0] m_sdram_awaddr,
    output reg  [7:0]  m_sdram_awlen,

    output reg         m_sdram_wvalid,
    input  wire        m_sdram_wready,
    output reg  [31:0] m_sdram_wdata,
    output reg  [3:0]  m_sdram_wstrb,
    output reg         m_sdram_wlast,

    input  wire        m_sdram_bvalid,
    input  wire [1:0]  m_sdram_bresp,

    // PSRAM AXI4 master interface (to axi_psram_slave via core_top)
    output reg         m_psram_arvalid,
    input  wire        m_psram_arready,
    output reg  [31:0] m_psram_araddr,
    output reg  [7:0]  m_psram_arlen,

    input  wire        m_psram_rvalid,
    input  wire [31:0] m_psram_rdata,
    input  wire [1:0]  m_psram_rresp,
    input  wire        m_psram_rlast,

    output reg         m_psram_awvalid,
    input  wire        m_psram_awready,
    output reg  [31:0] m_psram_awaddr,
    output reg  [7:0]  m_psram_awlen,

    output reg         m_psram_wvalid,
    input  wire        m_psram_wready,
    output reg  [31:0] m_psram_wdata,
    output reg  [3:0]  m_psram_wstrb,
    output reg         m_psram_wlast,

    input  wire        m_psram_bvalid,
    input  wire [1:0]  m_psram_bresp,

    // Local peripheral AXI4 master interface (to axi_periph_slave via core_top)
    output reg         m_local_arvalid,
    input  wire        m_local_arready,
    output reg  [31:0] m_local_araddr,
    output reg  [7:0]  m_local_arlen,

    input  wire        m_local_rvalid,
    input  wire [31:0] m_local_rdata,
    input  wire [1:0]  m_local_rresp,
    input  wire        m_local_rlast,

    output reg         m_local_awvalid,
    input  wire        m_local_awready,
    output reg  [31:0] m_local_awaddr,
    output reg  [7:0]  m_local_awlen,

    output reg         m_local_wvalid,
    input  wire        m_local_wready,
    output reg  [31:0] m_local_wdata,
    output reg  [3:0]  m_local_wstrb,
    output reg         m_local_wlast,

    input  wire        m_local_bvalid,
    input  wire [1:0]  m_local_bresp
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
wire        io_cmd_ready;    // driven by dual-FSM assign below
wire        io_cmd_write;
wire [31:0] io_cmd_addr;
wire [31:0] io_cmd_data;
wire [3:0]  io_cmd_mask;

wire        io_rsp_valid;    // driven by dual-FSM assign below
wire        io_rsp_error;
wire [31:0] io_rsp_data;

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
// Dual-channel FSM: independent read and write paths
// Allows D-cache writebacks (AW+W+B) to proceed in parallel
// with D-cache refills (AR+R) and I-cache fetches.
// AXI4 channels are independent by spec — both can be
// in-flight to the same target simultaneously.
// ============================================

localparam BUS_NONE  = 2'd0;
localparam BUS_FETCH = 2'd1;
localparam BUS_LSU   = 2'd2;
localparam BUS_IO    = 2'd3;

localparam TGT_SDRAM = 2'd0;
localparam TGT_PSRAM = 2'd1;
localparam TGT_LOCAL = 2'd2;

// ============================================
// Address decode helpers
// ============================================
function [1:0] addr_to_tgt_cached;
    input [31:0] addr;
    if (addr[31:26] == 6'b000100 || addr[31:26] == 6'b010100)
        addr_to_tgt_cached = TGT_SDRAM;
    else if (addr[31:27] == 5'b00110)
        addr_to_tgt_cached = TGT_PSRAM;
    else
        addr_to_tgt_cached = TGT_LOCAL;
endfunction

function [1:0] addr_to_tgt_fetch;
    input [31:0] addr;
    if (addr[31:26] == 6'b000100 || addr[31:26] == 6'b010100)
        addr_to_tgt_fetch = TGT_SDRAM;
    else if (addr[31:28] == 4'b0011)
        addr_to_tgt_fetch = TGT_PSRAM;
    else
        addr_to_tgt_fetch = TGT_LOCAL;
endfunction

function [1:0] addr_to_tgt_io;
    input [31:0] addr;
    if (addr[31:26] == 6'b010100)
        addr_to_tgt_io = TGT_SDRAM;
    else if (addr[31:27] == 5'b00111)
        addr_to_tgt_io = TGT_PSRAM;
    else
        addr_to_tgt_io = TGT_LOCAL;
endfunction

// ============================================
// READ FSM — handles AR+R for Fetch, LSU reads, IO reads
// ============================================
localparam RD_IDLE   = 2'd0;
localparam RD_MEM_AR = 2'd1;
localparam RD_MEM_R  = 2'd2;

reg [1:0]  rd_state;
reg [1:0]  rd_active_bus;
reg [1:0]  rd_target;
reg [0:0]  rd_id_r;
reg [7:0]  rd_burst_len;
reg [7:0]  rd_burst_count;
reg        rd_last_grant_lsu;
reg        rd_io_busy;          // IO read in progress

wire rd_beat_is_last = (rd_burst_count == rd_burst_len);

// Read target mux
wire rd_mem_arready = (rd_target == TGT_SDRAM) ? m_sdram_arready :
                      (rd_target == TGT_PSRAM) ? m_psram_arready :
                                                  m_local_arready;
wire rd_mem_rvalid  = (rd_target == TGT_SDRAM) ? m_sdram_rvalid :
                      (rd_target == TGT_PSRAM) ? m_psram_rvalid :
                                                  m_local_rvalid;
wire [31:0] rd_mem_rdata = (rd_target == TGT_SDRAM) ? m_sdram_rdata :
                           (rd_target == TGT_PSRAM) ? m_psram_rdata :
                                                       m_local_rdata;

// Read arbitration: IO read > Fetch > LSU read (round-robin Fetch/LSU)
wire io_rd_req = io_cmd_valid & !io_cmd_write & !rd_io_busy & !wr_io_busy;
wire fetch_req = fetch_ar_valid;
wire lsu_rd_req = lsu_ar_valid;
wire rd_io_grant = io_rd_req;
wire rd_lsu_grant = lsu_rd_req & !rd_io_grant & (!fetch_req | !rd_last_grant_lsu);
wire rd_fetch_grant = fetch_req & !rd_io_grant & !rd_lsu_grant;

// IO cmd/rsp from read FSM
reg io_cmd_ready_rd;
reg io_rsp_valid_rd;
reg [31:0] io_rsp_data_rd;

// ============================================
// WRITE FSM — handles AW+W+B for LSU writes, IO writes
// ============================================
localparam WR_IDLE       = 3'd0;
localparam WR_MEM_AW     = 3'd1;
localparam WR_MEM_W      = 3'd2;
localparam WR_MEM_B      = 3'd3;
localparam WR_WRITE_NEXT = 3'd4;

reg [2:0]  wr_state;
reg [1:0]  wr_active_bus;
reg [1:0]  wr_target;
reg [0:0]  wr_id_r;
reg [31:0] wr_addr_r;
reg [31:0] wr_wdata_r;
reg [3:0]  wr_wstrb_r;
reg [7:0]  wr_burst_len;
reg [7:0]  wr_burst_count;
reg        wr_io_busy;          // IO write in progress

wire wr_beat_is_last = (wr_burst_count == wr_burst_len);

// Write target mux
wire wr_mem_awready = (wr_target == TGT_SDRAM) ? m_sdram_awready :
                      (wr_target == TGT_PSRAM) ? m_psram_awready :
                                                  m_local_awready;
wire wr_mem_wready  = (wr_target == TGT_SDRAM) ? m_sdram_wready :
                      (wr_target == TGT_PSRAM) ? m_psram_wready :
                                                  m_local_wready;
wire wr_mem_bvalid  = (wr_target == TGT_SDRAM) ? m_sdram_bvalid :
                      (wr_target == TGT_PSRAM) ? m_psram_bvalid :
                                                  m_local_bvalid;

// Write arbitration: IO write > LSU write
wire io_wr_req = io_cmd_valid & io_cmd_write & !wr_io_busy & !rd_io_busy;
wire lsu_wr_req = lsu_aw_valid;
wire wr_io_grant = io_wr_req;
wire wr_lsu_grant = lsu_wr_req & !wr_io_grant;

// IO cmd/rsp from write FSM
reg io_cmd_ready_wr;
reg io_rsp_valid_wr;

// ============================================
// IO bus mux: combine read and write FSM IO signals
// ============================================
assign io_cmd_ready = io_cmd_ready_rd | io_cmd_ready_wr;
assign io_rsp_valid = io_rsp_valid_rd | io_rsp_valid_wr;
assign io_rsp_data  = io_rsp_data_rd;
assign io_rsp_error = 1'b0;

// ============================================
// READ FSM logic
// ============================================
always @(posedge clk or posedge reset) begin
    if (reset) begin
        rd_state <= RD_IDLE;
        rd_active_bus <= BUS_NONE;
        rd_target <= TGT_SDRAM;
        rd_id_r <= 0;
        rd_burst_len <= 0;
        rd_burst_count <= 0;
        rd_last_grant_lsu <= 0;
        rd_io_busy <= 0;
        io_cmd_ready_rd <= 0;
        io_rsp_valid_rd <= 0;
        io_rsp_data_rd <= 0;
        fetch_ar_ready <= 0;
        fetch_r_valid <= 0; fetch_r_data <= 0; fetch_r_id <= 0; fetch_r_last <= 0;
        lsu_ar_ready <= 0;
        lsu_r_valid <= 0; lsu_r_data <= 0; lsu_r_id <= 0; lsu_r_last <= 0;
        m_sdram_arvalid <= 0; m_sdram_araddr <= 0; m_sdram_arlen <= 0;
        m_psram_arvalid <= 0; m_psram_araddr <= 0; m_psram_arlen <= 0;
        m_local_arvalid <= 0; m_local_araddr <= 0; m_local_arlen <= 0;
    end else begin
        fetch_ar_ready <= 0;
        fetch_r_valid <= 0;
        lsu_ar_ready <= 0;
        lsu_r_valid <= 0;
        io_cmd_ready_rd <= 0;
        io_rsp_valid_rd <= 0;

        case (rd_state)
        RD_IDLE: begin
            m_sdram_arvalid <= 0;
            m_psram_arvalid <= 0;
            m_local_arvalid <= 0;

            if (rd_io_grant) begin
                io_cmd_ready_rd <= 1;
                rd_active_bus <= BUS_IO;
                rd_burst_len <= 0;
                rd_burst_count <= 0;
                rd_io_busy <= 1;
                rd_target <= addr_to_tgt_io(io_cmd_addr);
                rd_state <= RD_MEM_AR;
                case (addr_to_tgt_io(io_cmd_addr))
                TGT_SDRAM: begin m_sdram_arvalid <= 1; m_sdram_araddr <= io_cmd_addr; m_sdram_arlen <= 0; end
                TGT_PSRAM: begin m_psram_arvalid <= 1; m_psram_araddr <= io_cmd_addr; m_psram_arlen <= 0; end
                default:   begin m_local_arvalid <= 1; m_local_araddr <= io_cmd_addr; m_local_arlen <= 0; end
                endcase
            end else if (rd_lsu_grant) begin
                lsu_ar_ready <= 1;
                rd_active_bus <= BUS_LSU;
                rd_id_r <= lsu_ar_id;
                rd_burst_len <= lsu_ar_len;
                rd_burst_count <= 0;
                rd_last_grant_lsu <= 1;
                rd_target <= addr_to_tgt_cached(lsu_ar_addr);
                rd_state <= RD_MEM_AR;
                case (addr_to_tgt_cached(lsu_ar_addr))
                TGT_SDRAM: begin m_sdram_arvalid <= 1; m_sdram_araddr <= lsu_ar_addr; m_sdram_arlen <= lsu_ar_len; end
                TGT_PSRAM: begin m_psram_arvalid <= 1; m_psram_araddr <= lsu_ar_addr; m_psram_arlen <= lsu_ar_len; end
                default:   begin m_local_arvalid <= 1; m_local_araddr <= lsu_ar_addr; m_local_arlen <= lsu_ar_len; end
                endcase
            end else if (rd_fetch_grant) begin
                fetch_ar_ready <= 1;
                rd_active_bus <= BUS_FETCH;
                rd_id_r <= fetch_ar_id;
                rd_burst_len <= fetch_ar_len;
                rd_burst_count <= 0;
                rd_last_grant_lsu <= 0;
                rd_target <= addr_to_tgt_fetch(fetch_ar_addr);
                rd_state <= RD_MEM_AR;
                case (addr_to_tgt_fetch(fetch_ar_addr))
                TGT_SDRAM: begin m_sdram_arvalid <= 1; m_sdram_araddr <= fetch_ar_addr; m_sdram_arlen <= fetch_ar_len; end
                TGT_PSRAM: begin m_psram_arvalid <= 1; m_psram_araddr <= fetch_ar_addr; m_psram_arlen <= fetch_ar_len; end
                default:   begin m_local_arvalid <= 1; m_local_araddr <= fetch_ar_addr; m_local_arlen <= fetch_ar_len; end
                endcase
            end
        end

        RD_MEM_AR: begin
            if (rd_mem_arready) begin
                m_sdram_arvalid <= 0;
                m_psram_arvalid <= 0;
                m_local_arvalid <= 0;
                rd_state <= RD_MEM_R;
            end
        end

        RD_MEM_R: begin
            if (rd_active_bus == BUS_FETCH) begin
                if (fetch_r_valid && !fetch_r_ready)
                    fetch_r_valid <= 1;
                else if (rd_mem_rvalid) begin
                    fetch_r_valid <= 1;
                    fetch_r_data <= rd_mem_rdata;
                    fetch_r_id <= rd_id_r;
                    fetch_r_last <= rd_beat_is_last;
                    rd_burst_count <= rd_burst_count + 1;
                    if (rd_beat_is_last) rd_state <= RD_IDLE;
                end
            end else if (rd_active_bus == BUS_LSU) begin
                if (lsu_r_valid && !lsu_r_ready)
                    lsu_r_valid <= 1;
                else if (rd_mem_rvalid) begin
                    lsu_r_valid <= 1;
                    lsu_r_data <= rd_mem_rdata;
                    lsu_r_id <= rd_id_r;
                    lsu_r_last <= rd_beat_is_last;
                    rd_burst_count <= rd_burst_count + 1;
                    if (rd_beat_is_last) rd_state <= RD_IDLE;
                end
            end else if (rd_mem_rvalid) begin
                // IO read response
                io_rsp_valid_rd <= 1;
                io_rsp_data_rd <= rd_mem_rdata;
                rd_io_busy <= 0;
                rd_state <= RD_IDLE;
            end
        end
        endcase
    end
end

// ============================================
// WRITE FSM logic
// ============================================
always @(posedge clk or posedge reset) begin
    if (reset) begin
        wr_state <= WR_IDLE;
        wr_active_bus <= BUS_NONE;
        wr_target <= TGT_SDRAM;
        wr_id_r <= 0;
        wr_addr_r <= 0;
        wr_wdata_r <= 0;
        wr_wstrb_r <= 0;
        wr_burst_len <= 0;
        wr_burst_count <= 0;
        wr_io_busy <= 0;
        io_cmd_ready_wr <= 0;
        io_rsp_valid_wr <= 0;
        lsu_aw_ready <= 0;
        lsu_w_ready <= 0;
        lsu_b_valid <= 0; lsu_b_id <= 0;
        m_sdram_awvalid <= 0; m_sdram_awaddr <= 0; m_sdram_awlen <= 0;
        m_sdram_wvalid <= 0; m_sdram_wdata <= 0; m_sdram_wstrb <= 0; m_sdram_wlast <= 0;
        m_psram_awvalid <= 0; m_psram_awaddr <= 0; m_psram_awlen <= 0;
        m_psram_wvalid <= 0; m_psram_wdata <= 0; m_psram_wstrb <= 0; m_psram_wlast <= 0;
        m_local_awvalid <= 0; m_local_awaddr <= 0; m_local_awlen <= 0;
        m_local_wvalid <= 0; m_local_wdata <= 0; m_local_wstrb <= 0; m_local_wlast <= 0;
    end else begin
        lsu_aw_ready <= 0;
        lsu_w_ready <= 0;
        lsu_b_valid <= 0;
        io_cmd_ready_wr <= 0;
        io_rsp_valid_wr <= 0;

        case (wr_state)
        WR_IDLE: begin
            m_sdram_awvalid <= 0; m_sdram_wvalid <= 0;
            m_psram_awvalid <= 0; m_psram_wvalid <= 0;
            m_local_awvalid <= 0; m_local_wvalid <= 0;

            if (wr_io_grant) begin
                io_cmd_ready_wr <= 1;
                wr_active_bus <= BUS_IO;
                wr_burst_len <= 0;
                wr_burst_count <= 0;
                wr_wdata_r <= io_cmd_data;
                wr_wstrb_r <= io_cmd_mask;
                wr_io_busy <= 1;
                wr_target <= addr_to_tgt_io(io_cmd_addr);
                wr_state <= WR_MEM_AW;
                case (addr_to_tgt_io(io_cmd_addr))
                TGT_SDRAM: begin m_sdram_awvalid <= 1; m_sdram_awaddr <= io_cmd_addr; m_sdram_awlen <= 0; end
                TGT_PSRAM: begin m_psram_awvalid <= 1; m_psram_awaddr <= io_cmd_addr; m_psram_awlen <= 0; end
                default:   begin m_local_awvalid <= 1; m_local_awaddr <= io_cmd_addr; m_local_awlen <= 0; end
                endcase
            end else if (wr_lsu_grant) begin
                lsu_aw_ready <= 1;
                wr_active_bus <= BUS_LSU;
                wr_id_r <= lsu_aw_id;
                wr_addr_r <= lsu_aw_addr;
                wr_burst_len <= lsu_aw_len;
                wr_burst_count <= 0;
                wr_target <= addr_to_tgt_cached(lsu_aw_addr);

                if (lsu_w_valid) begin
                    lsu_w_ready <= 1;
                    wr_wdata_r <= lsu_w_data;
                    wr_wstrb_r <= lsu_w_strb;
                    wr_state <= WR_MEM_AW;
                    case (addr_to_tgt_cached(lsu_aw_addr))
                    TGT_SDRAM: begin m_sdram_awvalid <= 1; m_sdram_awaddr <= lsu_aw_addr; m_sdram_awlen <= lsu_aw_len; end
                    TGT_PSRAM: begin m_psram_awvalid <= 1; m_psram_awaddr <= lsu_aw_addr; m_psram_awlen <= lsu_aw_len; end
                    default:   begin m_local_awvalid <= 1; m_local_awaddr <= lsu_aw_addr; m_local_awlen <= lsu_aw_len; end
                    endcase
                end else begin
                    wr_state <= WR_WRITE_NEXT;
                end
            end
        end

        WR_MEM_AW: begin
            if (wr_mem_awready) begin
                m_sdram_awvalid <= 0; m_psram_awvalid <= 0; m_local_awvalid <= 0;
                wr_state <= WR_MEM_W;
                case (wr_target)
                TGT_SDRAM: begin m_sdram_wvalid <= 1; m_sdram_wdata <= wr_wdata_r; m_sdram_wstrb <= wr_wstrb_r; m_sdram_wlast <= wr_beat_is_last; end
                TGT_PSRAM: begin m_psram_wvalid <= 1; m_psram_wdata <= wr_wdata_r; m_psram_wstrb <= wr_wstrb_r; m_psram_wlast <= wr_beat_is_last; end
                default:   begin m_local_wvalid <= 1; m_local_wdata <= wr_wdata_r; m_local_wstrb <= wr_wstrb_r; m_local_wlast <= wr_beat_is_last; end
                endcase
            end
        end

        WR_MEM_W: begin
            if (wr_mem_wready) begin
                m_sdram_wvalid <= 0; m_psram_wvalid <= 0; m_local_wvalid <= 0;
                wr_burst_count <= wr_burst_count + 1;
                if (wr_beat_is_last)
                    wr_state <= WR_MEM_B;
                else begin
                    wr_addr_r <= wr_addr_r + 32'd4;
                    wr_state <= WR_WRITE_NEXT;
                end
            end
        end

        WR_MEM_B: begin
            if (wr_mem_bvalid) begin
                if (wr_active_bus == BUS_IO) begin
                    io_rsp_valid_wr <= 1;
                    wr_io_busy <= 0;
                end else begin
                    lsu_b_valid <= 1;
                    lsu_b_id <= wr_id_r;
                end
                wr_state <= WR_IDLE;
            end
        end

        WR_WRITE_NEXT: begin
            if (lsu_w_valid) begin
                lsu_w_ready <= 1;
                wr_wdata_r <= lsu_w_data;
                wr_wstrb_r <= lsu_w_strb;
                if (wr_burst_count == 0 && !m_sdram_awvalid && !m_psram_awvalid && !m_local_awvalid) begin
                    wr_state <= WR_MEM_AW;
                    case (wr_target)
                    TGT_SDRAM: begin m_sdram_awvalid <= 1; m_sdram_awaddr <= wr_addr_r; m_sdram_awlen <= wr_burst_len; end
                    TGT_PSRAM: begin m_psram_awvalid <= 1; m_psram_awaddr <= wr_addr_r; m_psram_awlen <= wr_burst_len; end
                    default:   begin m_local_awvalid <= 1; m_local_awaddr <= wr_addr_r; m_local_awlen <= wr_burst_len; end
                    endcase
                end else begin
                    wr_state <= WR_MEM_W;
                    case (wr_target)
                    TGT_SDRAM: begin m_sdram_wvalid <= 1; m_sdram_wdata <= lsu_w_data; m_sdram_wstrb <= lsu_w_strb; m_sdram_wlast <= (wr_burst_count == wr_burst_len); end
                    TGT_PSRAM: begin m_psram_wvalid <= 1; m_psram_wdata <= lsu_w_data; m_psram_wstrb <= lsu_w_strb; m_psram_wlast <= (wr_burst_count == wr_burst_len); end
                    default:   begin m_local_wvalid <= 1; m_local_wdata <= lsu_w_data; m_local_wstrb <= lsu_w_strb; m_local_wlast <= (wr_burst_count == wr_burst_len); end
                    endcase
                end
            end
        end

        default: wr_state <= WR_IDLE;
        endcase
    end
end

endmodule
