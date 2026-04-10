//
// VexiiRiscv CPU System — AXI4 router.
//
// Two CPU AXI4 master inputs (mem_axi cached, p_axi uncached) →
// three downstream slaves (sdram, psram, local). Serialized FSM
// with mem_axi priority and per-channel W-data ordering.
//
// W-channel ordering: Block A (capture next W beat from the CPU)
// must NOT re-fire while a slave-side wvalid is still pending
// acceptance. Otherwise back-to-back write storms with a back-
// pressuring slave would silently lose a beat — the CPU would
// see two wready handshakes but the slave would only see the
// second beat, and the burst would stall waiting for wlast on
// a beat that the CPU had already considered ack'd.
//

`default_nettype none

module cpu_system (
    input wire clk,
    input wire reset_n,

    // ---- SDRAM AXI4 master ---------------------------------------
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

    // ---- PSRAM AXI4 master ---------------------------------------
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

    // ---- Local peripheral AXI4 master ----------------------------
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
    input  wire [1:0]  m_local_bresp,

    // ---- Interrupts ---------------------------------------------
    input  wire        int_m_external,
    input  wire        int_m_timer
);

wire reset = ~reset_n;

// ============================================================
// CPU AXI4 master ports (from OpenFpgaVexii top)
// mem_axi : cached path, has 2-bit id (lsuL1 source bit + 1)
// p_axi   : uncached LSU IO, no id
// ============================================================

// mem_axi
wire        mem_arvalid;  wire        mem_arready;
wire [31:0] mem_araddr;
wire [1:0]  mem_arid;
wire [7:0]  mem_arlen;
wire [2:0]  mem_arsize;   wire [1:0]  mem_arburst;
wire        mem_rvalid;   wire        mem_rready;
wire [31:0] mem_rdata;
wire [1:0]  mem_rid;      wire [1:0]  mem_rresp;
wire        mem_rlast;

wire        mem_awvalid;  wire        mem_awready;
wire [31:0] mem_awaddr;
wire [1:0]  mem_awid;
wire [7:0]  mem_awlen;
wire [2:0]  mem_awsize;   wire [1:0]  mem_awburst;
wire        mem_awallStrb;
wire        mem_wvalid;   wire        mem_wready;
wire [31:0] mem_wdata;    wire [3:0]  mem_wstrb;
wire        mem_wlast;
wire        mem_bvalid;   wire        mem_bready;
wire [1:0]  mem_bid;      wire [1:0]  mem_bresp;

// p_axi
wire        per_arvalid;  wire        per_arready;
wire [31:0] per_araddr;
wire [7:0]  per_arlen;
wire [2:0]  per_arsize;   wire [1:0]  per_arburst;
wire        per_rvalid;   wire        per_rready;
wire [31:0] per_rdata;    wire [1:0]  per_rresp;
wire        per_rlast;

wire        per_awvalid;  wire        per_awready;
wire [31:0] per_awaddr;
wire [7:0]  per_awlen;
wire [2:0]  per_awsize;   wire [1:0]  per_awburst;
wire        per_awallStrb;
wire        per_wvalid;   wire        per_wready;
wire [31:0] per_wdata;    wire [3:0]  per_wstrb;
wire        per_wlast;
wire        per_bvalid;   wire        per_bready;
wire [1:0]  per_bresp;

OpenFpgaVexii cpu (
    .clk(clk),
    .reset(reset),

    .int_m_timer   (int_m_timer),
    .int_m_software(1'b0),
    .int_m_external(int_m_external),

    .mem_axi_awvalid  (mem_awvalid),
    .mem_axi_awready  (mem_awready),
    .mem_axi_awaddr   (mem_awaddr),
    .mem_axi_awid     (mem_awid),
    .mem_axi_awlen    (mem_awlen),
    .mem_axi_awsize   (mem_awsize),
    .mem_axi_awburst  (mem_awburst),
    .mem_axi_awallStrb(mem_awallStrb),
    .mem_axi_wvalid   (mem_wvalid),
    .mem_axi_wready   (mem_wready),
    .mem_axi_wdata    (mem_wdata),
    .mem_axi_wstrb    (mem_wstrb),
    .mem_axi_wlast    (mem_wlast),
    .mem_axi_bvalid   (mem_bvalid),
    .mem_axi_bready   (mem_bready),
    .mem_axi_bid      (mem_bid),
    .mem_axi_bresp    (mem_bresp),
    .mem_axi_arvalid  (mem_arvalid),
    .mem_axi_arready  (mem_arready),
    .mem_axi_araddr   (mem_araddr),
    .mem_axi_arid     (mem_arid),
    .mem_axi_arlen    (mem_arlen),
    .mem_axi_arsize   (mem_arsize),
    .mem_axi_arburst  (mem_arburst),
    .mem_axi_rvalid   (mem_rvalid),
    .mem_axi_rready   (mem_rready),
    .mem_axi_rdata    (mem_rdata),
    .mem_axi_rid      (mem_rid),
    .mem_axi_rresp    (mem_rresp),
    .mem_axi_rlast    (mem_rlast),

    .p_axi_awvalid    (per_awvalid),
    .p_axi_awready    (per_awready),
    .p_axi_awaddr     (per_awaddr),
    .p_axi_awlen      (per_awlen),
    .p_axi_awsize     (per_awsize),
    .p_axi_awburst    (per_awburst),
    .p_axi_awallStrb  (per_awallStrb),
    .p_axi_wvalid     (per_wvalid),
    .p_axi_wready     (per_wready),
    .p_axi_wdata      (per_wdata),
    .p_axi_wstrb      (per_wstrb),
    .p_axi_wlast      (per_wlast),
    .p_axi_bvalid     (per_bvalid),
    .p_axi_bready     (per_bready),
    .p_axi_bresp      (per_bresp),
    .p_axi_arvalid    (per_arvalid),
    .p_axi_arready    (per_arready),
    .p_axi_araddr     (per_araddr),
    .p_axi_arlen      (per_arlen),
    .p_axi_arsize     (per_arsize),
    .p_axi_arburst    (per_arburst),
    .p_axi_rvalid     (per_rvalid),
    .p_axi_rready     (per_rready),
    .p_axi_rdata      (per_rdata),
    .p_axi_rresp      (per_rresp),
    .p_axi_rlast      (per_rlast)
);

// ============================================================
// Address decode → target slave
// ============================================================
localparam TGT_SDRAM = 2'd0;
localparam TGT_PSRAM = 2'd1;
localparam TGT_LOCAL = 2'd2;

function [1:0] decode_target;
    input [31:0] addr;
    begin
        if (addr[31:26] == 6'b000100 || addr[31:26] == 6'b010100)
            decode_target = TGT_SDRAM;
        else if (addr[31:27] == 5'b00110 || addr[31:27] == 5'b00111)
            decode_target = TGT_PSRAM;
        else
            decode_target = TGT_LOCAL;
    end
endfunction

// ============================================================
// Serialized FSM (mem_axi priority over p_axi)
// ============================================================
localparam BUS_NONE = 2'd0;
localparam BUS_MEM  = 2'd1;
localparam BUS_PER  = 2'd2;

localparam FSM_IDLE   = 4'd0;
localparam FSM_RD_AR  = 4'd1;
localparam FSM_RD_R   = 4'd2;
localparam FSM_WR_AW  = 4'd3;
localparam FSM_WR_W   = 4'd4;
localparam FSM_WR_B   = 4'd5;

reg [3:0] fsm_state;
reg [1:0] active_bus;
reg [1:0] target_mem;
reg [1:0] active_id;
reg [7:0] burst_len_r;
reg [7:0] burst_count;
reg       last_grant_mem;

reg cpu_mem_arready_r, cpu_per_arready_r;
reg cpu_mem_awready_r, cpu_per_awready_r;
reg cpu_mem_wready_r,  cpu_per_wready_r;

assign mem_arready = cpu_mem_arready_r;
assign per_arready = cpu_per_arready_r;
assign mem_awready = cpu_mem_awready_r;
assign per_awready = cpu_per_awready_r;
assign mem_wready  = cpu_mem_wready_r;
assign per_wready  = cpu_per_wready_r;

reg        mem_rvalid_r;  reg [31:0] mem_rdata_r;
reg [1:0]  mem_rid_r;     reg [1:0]  mem_rresp_r;
reg        mem_rlast_r;
reg        mem_bvalid_r;  reg [1:0]  mem_bid_r;  reg [1:0] mem_bresp_r;

assign mem_rvalid = mem_rvalid_r;
assign mem_rdata  = mem_rdata_r;
assign mem_rid    = mem_rid_r;
assign mem_rresp  = mem_rresp_r;
assign mem_rlast  = mem_rlast_r;
assign mem_bvalid = mem_bvalid_r;
assign mem_bid    = mem_bid_r;
assign mem_bresp  = mem_bresp_r;

reg        per_rvalid_r;  reg [31:0] per_rdata_r;
reg [1:0]  per_rresp_r;   reg        per_rlast_r;
reg        per_bvalid_r;  reg [1:0]  per_bresp_r;

assign per_rvalid = per_rvalid_r;
assign per_rdata  = per_rdata_r;
assign per_rresp  = per_rresp_r;
assign per_rlast  = per_rlast_r;
assign per_bvalid = per_bvalid_r;
assign per_bresp  = per_bresp_r;

wire mem_arready_slave = (target_mem == TGT_SDRAM) ? m_sdram_arready :
                         (target_mem == TGT_PSRAM) ? m_psram_arready : m_local_arready;
wire mem_awready_slave = (target_mem == TGT_SDRAM) ? m_sdram_awready :
                         (target_mem == TGT_PSRAM) ? m_psram_awready : m_local_awready;
wire mem_wready_slave  = (target_mem == TGT_SDRAM) ? m_sdram_wready :
                         (target_mem == TGT_PSRAM) ? m_psram_wready : m_local_wready;
wire        slv_rvalid = (target_mem == TGT_SDRAM) ? m_sdram_rvalid :
                         (target_mem == TGT_PSRAM) ? m_psram_rvalid : m_local_rvalid;
wire [31:0] slv_rdata  = (target_mem == TGT_SDRAM) ? m_sdram_rdata :
                         (target_mem == TGT_PSRAM) ? m_psram_rdata : m_local_rdata;
wire [1:0]  slv_rresp  = (target_mem == TGT_SDRAM) ? m_sdram_rresp :
                         (target_mem == TGT_PSRAM) ? m_psram_rresp : m_local_rresp;
wire        slv_rlast  = (target_mem == TGT_SDRAM) ? m_sdram_rlast :
                         (target_mem == TGT_PSRAM) ? m_psram_rlast : m_local_rlast;
wire        slv_bvalid = (target_mem == TGT_SDRAM) ? m_sdram_bvalid :
                         (target_mem == TGT_PSRAM) ? m_psram_bvalid : m_local_bvalid;
wire [1:0]  slv_bresp  = (target_mem == TGT_SDRAM) ? m_sdram_bresp :
                         (target_mem == TGT_PSRAM) ? m_psram_bresp : m_local_bresp;

wire beat_is_last = (burst_count == burst_len_r);

wire mem_rd_req = mem_arvalid;
wire mem_wr_req = mem_awvalid;
wire per_rd_req = per_arvalid;
wire per_wr_req = per_awvalid;
wire mem_req    = mem_rd_req | mem_wr_req;
wire per_req    = per_rd_req | per_wr_req;

wire grant_mem    = mem_req & (~per_req | ~last_grant_mem);
wire grant_per    = per_req & ~grant_mem;
wire grant_mem_rd = grant_mem & mem_rd_req;
wire grant_mem_wr = grant_mem & ~mem_rd_req & mem_wr_req;
wire grant_per_rd = grant_per & per_rd_req;
wire grant_per_wr = grant_per & ~per_rd_req & per_wr_req;

// ============================================================
// Main FSM
// ============================================================
always @(posedge clk or posedge reset) begin
    if (reset) begin
        fsm_state <= FSM_IDLE; active_bus <= BUS_NONE; target_mem <= TGT_SDRAM;
        active_id <= 0; burst_len_r <= 0; burst_count <= 0; last_grant_mem <= 0;
        cpu_mem_arready_r <= 0; cpu_per_arready_r <= 0;
        cpu_mem_awready_r <= 0; cpu_per_awready_r <= 0;
        cpu_mem_wready_r  <= 0; cpu_per_wready_r  <= 0;
        mem_rvalid_r <= 0; mem_rdata_r <= 0; mem_rid_r <= 0; mem_rresp_r <= 0; mem_rlast_r <= 0;
        mem_bvalid_r <= 0; mem_bid_r <= 0; mem_bresp_r <= 0;
        per_rvalid_r <= 0; per_rdata_r <= 0; per_rresp_r <= 0; per_rlast_r <= 0;
        per_bvalid_r <= 0; per_bresp_r <= 0;
        m_sdram_arvalid<=0; m_sdram_araddr<=0; m_sdram_arlen<=0;
        m_sdram_awvalid<=0; m_sdram_awaddr<=0; m_sdram_awlen<=0;
        m_sdram_wvalid<=0;  m_sdram_wdata<=0;  m_sdram_wstrb<=0; m_sdram_wlast<=0;
        m_psram_arvalid<=0; m_psram_araddr<=0; m_psram_arlen<=0;
        m_psram_awvalid<=0; m_psram_awaddr<=0; m_psram_awlen<=0;
        m_psram_wvalid<=0;  m_psram_wdata<=0;  m_psram_wstrb<=0; m_psram_wlast<=0;
        m_local_arvalid<=0; m_local_araddr<=0; m_local_arlen<=0;
        m_local_awvalid<=0; m_local_awaddr<=0; m_local_awlen<=0;
        m_local_wvalid<=0;  m_local_wdata<=0;  m_local_wstrb<=0; m_local_wlast<=0;
    end else begin
        // Defaults: deassert single-cycle pulses
        cpu_mem_arready_r <= 0; cpu_per_arready_r <= 0;
        cpu_mem_awready_r <= 0; cpu_per_awready_r <= 0;
        cpu_mem_wready_r  <= 0; cpu_per_wready_r  <= 0;
        // Deassert responses only when the CPU has accepted them
        if (mem_rready) mem_rvalid_r <= 0;
        if (mem_bready) mem_bvalid_r <= 0;
        if (per_rready) per_rvalid_r <= 0;
        if (per_bready) per_bvalid_r <= 0;

        case (fsm_state)
        FSM_IDLE: begin
            m_sdram_arvalid<=0; m_psram_arvalid<=0; m_local_arvalid<=0;
            m_sdram_awvalid<=0; m_psram_awvalid<=0; m_local_awvalid<=0;
            m_sdram_wvalid<=0;  m_psram_wvalid<=0;  m_local_wvalid<=0;

            if (grant_mem_rd) begin
                cpu_mem_arready_r <= 1;
                active_bus <= BUS_MEM; active_id <= mem_arid;
                burst_len_r <= mem_arlen; burst_count <= 0;
                target_mem <= decode_target(mem_araddr); last_grant_mem <= 1;
                fsm_state <= FSM_RD_AR;
                case (decode_target(mem_araddr))
                TGT_SDRAM: begin m_sdram_arvalid<=1; m_sdram_araddr<=mem_araddr; m_sdram_arlen<=mem_arlen; end
                TGT_PSRAM: begin m_psram_arvalid<=1; m_psram_araddr<=mem_araddr; m_psram_arlen<=mem_arlen; end
                default:   begin m_local_arvalid<=1; m_local_araddr<=mem_araddr; m_local_arlen<=mem_arlen; end
                endcase
            end else if (grant_mem_wr) begin
                cpu_mem_awready_r <= 1;
                active_bus <= BUS_MEM; active_id <= mem_awid;
                burst_len_r <= mem_awlen; burst_count <= 0;
                target_mem <= decode_target(mem_awaddr); last_grant_mem <= 1;
                fsm_state <= FSM_WR_AW;
                case (decode_target(mem_awaddr))
                TGT_SDRAM: begin m_sdram_awvalid<=1; m_sdram_awaddr<=mem_awaddr; m_sdram_awlen<=mem_awlen; end
                TGT_PSRAM: begin m_psram_awvalid<=1; m_psram_awaddr<=mem_awaddr; m_psram_awlen<=mem_awlen; end
                default:   begin m_local_awvalid<=1; m_local_awaddr<=mem_awaddr; m_local_awlen<=mem_awlen; end
                endcase
            end else if (grant_per_rd) begin
                cpu_per_arready_r <= 1;
                active_bus <= BUS_PER; burst_len_r <= per_arlen; burst_count <= 0;
                target_mem <= decode_target(per_araddr); last_grant_mem <= 0;
                fsm_state <= FSM_RD_AR;
                case (decode_target(per_araddr))
                TGT_SDRAM: begin m_sdram_arvalid<=1; m_sdram_araddr<=per_araddr; m_sdram_arlen<=per_arlen; end
                TGT_PSRAM: begin m_psram_arvalid<=1; m_psram_araddr<=per_araddr; m_psram_arlen<=per_arlen; end
                default:   begin m_local_arvalid<=1; m_local_araddr<=per_araddr; m_local_arlen<=per_arlen; end
                endcase
            end else if (grant_per_wr) begin
                cpu_per_awready_r <= 1;
                active_bus <= BUS_PER; burst_len_r <= per_awlen; burst_count <= 0;
                target_mem <= decode_target(per_awaddr); last_grant_mem <= 0;
                fsm_state <= FSM_WR_AW;
                case (decode_target(per_awaddr))
                TGT_SDRAM: begin m_sdram_awvalid<=1; m_sdram_awaddr<=per_awaddr; m_sdram_awlen<=per_awlen; end
                TGT_PSRAM: begin m_psram_awvalid<=1; m_psram_awaddr<=per_awaddr; m_psram_awlen<=per_awlen; end
                default:   begin m_local_awvalid<=1; m_local_awaddr<=per_awaddr; m_local_awlen<=per_awlen; end
                endcase
            end
        end

        FSM_RD_AR: begin
            if (mem_arready_slave) begin
                m_sdram_arvalid<=0; m_psram_arvalid<=0; m_local_arvalid<=0;
                fsm_state <= FSM_RD_R;
            end
        end

        FSM_RD_R: begin
            if (slv_rvalid) begin
                if (active_bus == BUS_MEM) begin
                    mem_rvalid_r <= 1; mem_rdata_r <= slv_rdata;
                    mem_rresp_r  <= slv_rresp; mem_rid_r <= active_id;
                    mem_rlast_r  <= beat_is_last;
                end else begin
                    per_rvalid_r <= 1; per_rdata_r <= slv_rdata;
                    per_rresp_r  <= slv_rresp; per_rlast_r <= beat_is_last;
                end
                burst_count <= burst_count + 8'd1;
                if (beat_is_last) fsm_state <= FSM_IDLE;
            end
        end

        FSM_WR_AW: begin
            if (mem_awready_slave) begin
                m_sdram_awvalid<=0; m_psram_awvalid<=0; m_local_awvalid<=0;
                fsm_state <= FSM_WR_W;
            end
        end

        FSM_WR_W: begin
            // W-channel race fix: do NOT capture next beat while
            // a slave-side wvalid is still pending acceptance.
            if (active_bus == BUS_MEM) begin
                if (mem_wvalid && !cpu_mem_wready_r &&
                    !m_sdram_wvalid && !m_psram_wvalid && !m_local_wvalid) begin
                    cpu_mem_wready_r <= 1;
                    case (target_mem)
                    TGT_SDRAM: begin m_sdram_wvalid<=1; m_sdram_wdata<=mem_wdata; m_sdram_wstrb<=mem_wstrb; m_sdram_wlast<=mem_wlast; end
                    TGT_PSRAM: begin m_psram_wvalid<=1; m_psram_wdata<=mem_wdata; m_psram_wstrb<=mem_wstrb; m_psram_wlast<=mem_wlast; end
                    default:   begin m_local_wvalid<=1; m_local_wdata<=mem_wdata; m_local_wstrb<=mem_wstrb; m_local_wlast<=mem_wlast; end
                    endcase
                end
            end else begin
                if (per_wvalid && !cpu_per_wready_r &&
                    !m_sdram_wvalid && !m_psram_wvalid && !m_local_wvalid) begin
                    cpu_per_wready_r <= 1;
                    case (target_mem)
                    TGT_SDRAM: begin m_sdram_wvalid<=1; m_sdram_wdata<=per_wdata; m_sdram_wstrb<=per_wstrb; m_sdram_wlast<=per_wlast; end
                    TGT_PSRAM: begin m_psram_wvalid<=1; m_psram_wdata<=per_wdata; m_psram_wstrb<=per_wstrb; m_psram_wlast<=per_wlast; end
                    default:   begin m_local_wvalid<=1; m_local_wdata<=per_wdata; m_local_wstrb<=per_wstrb; m_local_wlast<=per_wlast; end
                    endcase
                end
            end

            if (mem_wready_slave && (m_sdram_wvalid || m_psram_wvalid || m_local_wvalid)) begin
                m_sdram_wvalid<=0; m_psram_wvalid<=0; m_local_wvalid<=0;
                burst_count <= burst_count + 8'd1;
                if (beat_is_last) fsm_state <= FSM_WR_B;
            end
        end

        FSM_WR_B: begin
            if (slv_bvalid) begin
                if (active_bus == BUS_MEM) begin
                    mem_bvalid_r <= 1; mem_bid_r <= active_id; mem_bresp_r <= slv_bresp;
                end else begin
                    per_bvalid_r <= 1; per_bresp_r <= slv_bresp;
                end
                fsm_state <= FSM_IDLE;
            end
        end

        default: fsm_state <= FSM_IDLE;
        endcase
    end
end

endmodule
// EOF — dead split-channel code removed
