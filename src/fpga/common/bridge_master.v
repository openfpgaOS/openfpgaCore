//
// Bridge Master — Bridge FIFO Drains + Bridge Reads → Word-Level SDRAM
//
// Converts bridge FIFO drain writes and bridge SDRAM reads into
// word-level transactions for word_sdram_arbiter.
//
// Pure register/LUT — 0 M10K.
//

`default_nettype none

module bridge_master (
    input wire clk,
    input wire reset_n,

    // Bridge write FIFO interface (dcfifo output, clk_ram_controller domain)
    input  wire [55:0] fifo_q,       // {addr[23:0], wdata[31:0]}
    input  wire        fifo_empty,
    output reg         fifo_rdreq,

    // Bridge read interface (sync'd to clk_ram_controller)
    input  wire        bridge_rd_req,     // Level: high while read pending
    input  wire [23:0] bridge_rd_addr,    // Word address [25:2]
    output reg  [31:0] bridge_rd_data,    // Captured read data
    output reg         bridge_rd_done,    // Pulse: read complete

    // Word-level SDRAM master interface (to word_sdram_arbiter)
    output reg         m_rd,
    output reg         m_wr,
    output reg  [23:0] m_addr,
    output reg  [31:0] m_wdata,
    output wire [3:0]  m_wstrb,
    output wire [3:0]  m_burst_len,
    output wire [3:0]  m_burst_wr_len,
    input  wire [31:0] m_rdata,
    input  wire        m_busy,
    input  wire        m_accepted,
    input  wire        m_rdata_valid,

    // Status
    output wire        idle,
    output wire        wr_idle
);

wire reset = ~reset_n;

// All transactions are single-beat, full word
assign m_wstrb = 4'b1111;
assign m_burst_len = 4'd0;
assign m_burst_wr_len = 4'd0;

localparam S_IDLE  = 2'd0;
localparam S_RD    = 2'd1;  // Read: hold rd until accepted, wait rdata_valid
localparam S_WR    = 2'd2;  // Write: hold wr until accepted, wait !busy

reg [1:0] state;
reg       rd_done_sent;
reg       wr_started;

assign idle = (state == S_IDLE) && fifo_empty && !bridge_rd_req;
assign wr_idle = (state != S_WR) && fifo_empty;

always @(posedge clk or posedge reset) begin
    if (reset) begin
        state <= S_IDLE;
        m_rd <= 0;
        m_wr <= 0;
        m_addr <= 0;
        m_wdata <= 0;
        fifo_rdreq <= 0;
        bridge_rd_data <= 0;
        bridge_rd_done <= 0;
        rd_done_sent <= 0;
        wr_started <= 0;
    end else begin
        fifo_rdreq <= 0;
        bridge_rd_done <= 0;

        if (!bridge_rd_req)
            rd_done_sent <= 0;

        case (state)
        S_IDLE: begin
            m_rd <= 0;
            m_wr <= 0;
            wr_started <= 0;
            if (bridge_rd_req && !rd_done_sent) begin
                m_rd <= 1;
                m_addr <= bridge_rd_addr;
                state <= S_RD;
            end else if (!fifo_empty) begin
                fifo_rdreq <= 1;
                m_wr <= 1;
                m_addr <= fifo_q[55:32];
                m_wdata <= fifo_q[31:0];
                state <= S_WR;
            end
        end

        S_RD: begin
            m_rd <= 1;  // Hold until accepted
            if (m_accepted) begin
                m_rd <= 0;
            end
            if (m_rdata_valid) begin
                bridge_rd_data <= m_rdata;
                bridge_rd_done <= 1;
                rd_done_sent <= 1;
                m_rd <= 0;
                state <= S_IDLE;
            end
        end

        S_WR: begin
            m_wr <= 1;  // Hold until accepted
            if (m_accepted) begin
                m_wr <= 0;
                wr_started <= 1;
            end
            if (wr_started && !m_busy) begin
                m_wr <= 0;
                state <= S_IDLE;
            end
        end

        default: state <= S_IDLE;
        endcase
    end
end

endmodule
