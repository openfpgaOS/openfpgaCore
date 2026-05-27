//
// AXI4 slave for CPU access to CRAM1.
//
// CRAM1 is used as executable/cacheable storage.  Reads use the PSRAM
// controller's sync-burst port whenever the AXI request length fits the
// controller's 64-word limit, which covers VexiiRiscv I$/D$ line refills.
// Writes remain one 32-bit word at a time; loaders write CRAM1 rarely and
// flush before executing.
//

`default_nettype none

module axi_cram1_slave (
    input wire clk,
    input wire reset_n,

    input  wire        s_axi_arvalid,
    output reg         s_axi_arready,
    input  wire [31:0] s_axi_araddr,
    input  wire [7:0]  s_axi_arlen,

    output reg         s_axi_rvalid,
    input  wire        s_axi_rready,
    output reg  [31:0] s_axi_rdata,
    output reg  [1:0]  s_axi_rresp,
    output reg         s_axi_rlast,

    input  wire        s_axi_awvalid,
    output reg         s_axi_awready,
    input  wire [31:0] s_axi_awaddr,
    input  wire [7:0]  s_axi_awlen,

    input  wire        s_axi_wvalid,
    output reg         s_axi_wready,
    input  wire [31:0] s_axi_wdata,
    input  wire [3:0]  s_axi_wstrb,
    input  wire        s_axi_wlast,

    output reg         s_axi_bvalid,
    input  wire        s_axi_bready,
    output reg  [1:0]  s_axi_bresp,

    output reg         psram_rd,
    output reg         psram_wr,
    output reg  [21:0] psram_addr,
    output reg  [31:0] psram_wdata,
    output reg  [3:0]  psram_wstrb,
    input  wire [31:0] psram_rdata,
    input  wire        psram_busy,
    input  wire        psram_rdata_valid,

    output reg         psram_burst_rd,
    output reg  [21:0] psram_burst_addr,
    output reg  [5:0]  psram_burst_len,
    input  wire [31:0] psram_burst_rdata,
    input  wire        psram_burst_rdata_valid
);

localparam [2:0] S_IDLE       = 3'd0;
localparam [2:0] S_RD_CMD     = 3'd1;
localparam [2:0] S_RD_WAIT    = 3'd2;
localparam [2:0] S_BURST_RECV = 3'd3;
localparam [2:0] S_WR_NEXT    = 3'd4;
localparam [2:0] S_WR_WAIT    = 3'd5;
localparam [2:0] S_WR_RESP    = 3'd6;

reg [2:0]  state;
reg [31:0] addr_r;
reg [7:0]  burst_len;
reg [7:0]  beat_count;
reg        psram_started;

reg [31:0] rskid_data;
reg        rskid_last;
reg        rskid_valid;

wire beat_is_last = (beat_count == burst_len);

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        state <= S_IDLE;
        addr_r <= 32'd0;
        burst_len <= 8'd0;
        beat_count <= 8'd0;
        psram_started <= 1'b0;

        s_axi_arready <= 1'b0;
        s_axi_rvalid <= 1'b0;
        s_axi_rdata <= 32'd0;
        s_axi_rresp <= 2'b00;
        s_axi_rlast <= 1'b0;
        s_axi_awready <= 1'b0;
        s_axi_wready <= 1'b0;
        s_axi_bvalid <= 1'b0;
        s_axi_bresp <= 2'b00;

        psram_rd <= 1'b0;
        psram_wr <= 1'b0;
        psram_addr <= 22'd0;
        psram_wdata <= 32'd0;
        psram_wstrb <= 4'd0;
        psram_burst_rd <= 1'b0;
        psram_burst_addr <= 22'd0;
        psram_burst_len <= 6'd0;

        rskid_data <= 32'd0;
        rskid_last <= 1'b0;
        rskid_valid <= 1'b0;
    end else begin
        s_axi_arready <= 1'b0;
        s_axi_awready <= 1'b0;
        s_axi_wready  <= 1'b0;
        psram_rd <= 1'b0;
        psram_wr <= 1'b0;
        psram_burst_rd <= 1'b0;

        if (s_axi_rvalid && s_axi_rready) begin
            if (rskid_valid) begin
                s_axi_rdata <= rskid_data;
                s_axi_rlast <= rskid_last;
                rskid_valid <= 1'b0;
            end else begin
                s_axi_rvalid <= 1'b0;
            end
        end

        if (s_axi_bvalid && s_axi_bready)
            s_axi_bvalid <= 1'b0;

        case (state)
        S_IDLE: begin
            psram_started <= 1'b0;
            if (s_axi_arvalid && !psram_busy && !s_axi_rvalid && !rskid_valid) begin
                s_axi_arready <= 1'b1;
                addr_r <= s_axi_araddr;
                burst_len <= s_axi_arlen;
                beat_count <= 8'd0;

                if (s_axi_arlen <= 8'd63) begin
                    psram_burst_addr <= s_axi_araddr[23:2];
                    psram_burst_len <= s_axi_arlen[5:0];
                    psram_burst_rd <= 1'b1;
                    state <= S_BURST_RECV;
                end else begin
                    state <= S_RD_CMD;
                end
            end else if (s_axi_awvalid && !psram_busy && !s_axi_bvalid) begin
                s_axi_awready <= 1'b1;
                addr_r <= s_axi_awaddr;
                burst_len <= s_axi_awlen;
                beat_count <= 8'd0;
                state <= S_WR_NEXT;
            end
        end

        S_RD_CMD: begin
            if (!psram_busy) begin
                psram_addr <= addr_r[23:2];
                psram_rd <= 1'b1;
                state <= S_RD_WAIT;
            end
        end

        S_RD_WAIT: begin
            if (psram_rdata_valid) begin
                if (!s_axi_rvalid || (s_axi_rvalid && s_axi_rready && !rskid_valid)) begin
                    s_axi_rvalid <= 1'b1;
                    s_axi_rdata <= psram_rdata;
                    s_axi_rresp <= 2'b00;
                    s_axi_rlast <= beat_is_last;
                end else if (!rskid_valid) begin
                    rskid_valid <= 1'b1;
                    rskid_data <= psram_rdata;
                    rskid_last <= beat_is_last;
                end

                if (beat_is_last) begin
                    state <= S_IDLE;
                end else begin
                    beat_count <= beat_count + 8'd1;
                    addr_r <= addr_r + 32'd4;
                    state <= S_RD_CMD;
                end
            end
        end

        S_BURST_RECV: begin
            if (psram_burst_rdata_valid) begin
                if (!s_axi_rvalid || (s_axi_rvalid && s_axi_rready && !rskid_valid)) begin
                    s_axi_rvalid <= 1'b1;
                    s_axi_rdata <= psram_burst_rdata;
                    s_axi_rresp <= 2'b00;
                    s_axi_rlast <= beat_is_last;
                end else if (!rskid_valid) begin
                    rskid_valid <= 1'b1;
                    rskid_data <= psram_burst_rdata;
                    rskid_last <= beat_is_last;
                end

                if (beat_is_last) begin
                    state <= S_IDLE;
                end else begin
                    beat_count <= beat_count + 8'd1;
                end
            end
        end

        S_WR_NEXT: begin
            if (s_axi_wvalid && !psram_busy) begin
                s_axi_wready <= 1'b1;
                psram_addr <= addr_r[23:2];
                psram_wdata <= s_axi_wdata;
                psram_wstrb <= s_axi_wstrb;
                psram_wr <= 1'b1;
                psram_started <= 1'b0;
                state <= S_WR_WAIT;
            end
        end

        S_WR_WAIT: begin
            if (psram_busy) begin
                psram_started <= 1'b1;
            end else if (psram_started) begin
                if (beat_is_last) begin
                    s_axi_bvalid <= 1'b1;
                    s_axi_bresp <= 2'b00;
                    state <= S_WR_RESP;
                end else begin
                    beat_count <= beat_count + 8'd1;
                    addr_r <= addr_r + 32'd4;
                    state <= S_WR_NEXT;
                end
            end
        end

        S_WR_RESP: begin
            if (!s_axi_bvalid)
                state <= S_IDLE;
        end

        default: state <= S_IDLE;
        endcase
    end
end

endmodule
