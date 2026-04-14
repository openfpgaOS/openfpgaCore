// CRAM1 Controller — 32-bit word interface on a single 16-bit PSRAM chip
//
// Save data only. Async access, no BCR, no burst.
// Two-phase: LO halfword at addr*2, HI halfword at addr*2+1.
// Designed to run on clk_74a (bridge clock domain).

`default_nettype none

module psram_cram1 #(
    parameter CLOCK_SPEED = 74.25
) (
    input wire clk,
    input wire reset_n,

    // 32-bit word interface
    input wire         word_rd,
    input wire         word_wr,
    input wire  [21:0] word_addr,
    input wire  [31:0] word_data,
    input wire  [3:0]  word_wstrb,
    output reg  [31:0] word_q,
    output reg         word_busy,
    output reg         word_q_valid,

    // Physical signals
    output wire [21:16] cram_a,
    inout  wire [15:0]  cram_dq,
    input  wire         cram_wait,
    output wire         cram_clk,
    output wire         cram_adv_n,
    output wire         cram_cre,
    output wire         cram_ce0_n,
    output wire         cram_ce1_n,
    output wire         cram_oe_n,
    output wire         cram_we_n,
    output wire         cram_ub_n,
    output wire         cram_lb_n
);

localparam [3:0] ST_IDLE       = 4'd0;
localparam [3:0] ST_WR_LO     = 4'd1;
localparam [3:0] ST_WR_LO_BSY = 4'd2;
localparam [3:0] ST_WR_LO_WAI = 4'd3;
localparam [3:0] ST_WR_HI     = 4'd4;
localparam [3:0] ST_WR_HI_BSY = 4'd5;
localparam [3:0] ST_WR_HI_WAI = 4'd6;
localparam [3:0] ST_DONE      = 4'd7;
localparam [3:0] ST_RD_LO     = 4'd8;
localparam [3:0] ST_RD_LO_BSY = 4'd9;
localparam [3:0] ST_RD_LO_WAI = 4'd10;
localparam [3:0] ST_RD_HI     = 4'd11;
localparam [3:0] ST_RD_HI_BSY = 4'd12;
localparam [3:0] ST_RD_HI_WAI = 4'd13;

reg [3:0] state;
reg [31:0] latched_data;
reg [21:0] latched_addr;
reg latched_chip_sel;
reg [3:0] latched_wstrb;
reg [15:0] lo_captured;

reg         psram_write_en;
reg         psram_read_en;
reg  [21:0] psram_addr;
reg         psram_bank_sel;
reg  [15:0] psram_data_in;
reg         psram_write_high;
reg         psram_write_low;

wire [15:0] psram_data_out;
wire        psram_busy;

wire [21:0] addr_lo = {latched_addr[20:0], 1'b0};
wire [21:0] addr_hi = {latched_addr[20:0], 1'b1};

psram #(
    .CLOCK_SPEED(CLOCK_SPEED)
) psram_inst (
    .clk(clk),
    .bank_sel(psram_bank_sel),
    .addr(psram_addr),
    .write_en(psram_write_en),
    .data_in(psram_data_in),
    .write_high_byte(psram_write_high),
    .write_low_byte(psram_write_low),
    .read_en(psram_read_en),
    .sync_burst_en(1'b0),
    .sync_burst_len(6'd0),
    .config_en(1'b0),
    .config_data(16'd0),
    .read_avail(),
    .data_out(psram_data_out),
    .busy(psram_busy),
    .cram_a(cram_a),
    .cram_dq(cram_dq),
    .cram_wait(cram_wait),
    .cram_clk(cram_clk),
    .cram_adv_n(cram_adv_n),
    .cram_cre(cram_cre),
    .cram_ce0_n(cram_ce0_n),
    .cram_ce1_n(cram_ce1_n),
    .cram_oe_n(cram_oe_n),
    .cram_we_n(cram_we_n),
    .cram_ub_n(cram_ub_n),
    .cram_lb_n(cram_lb_n),
    .dbg_wait_seen(),
    .dbg_wait_cycles(),
    .dbg_burst_count(),
    .dbg_stale_count()
);

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        state <= ST_IDLE;
        word_busy <= 1'b0;
        word_q <= 32'b0;
        word_q_valid <= 1'b0;
        latched_data <= 32'b0;
        latched_addr <= 22'b0;
        latched_chip_sel <= 1'b0;
        latched_wstrb <= 4'b1111;
        lo_captured <= 16'b0;
        psram_write_en <= 1'b0;
        psram_read_en <= 1'b0;
        psram_addr <= 22'b0;
        psram_bank_sel <= 1'b0;
        psram_data_in <= 16'b0;
        psram_write_high <= 1'b1;
        psram_write_low <= 1'b1;
    end else begin
        psram_write_en <= 1'b0;
        psram_read_en <= 1'b0;
        word_q_valid <= 1'b0;

        case (state)
            ST_IDLE: begin
                word_busy <= 1'b0;
                if (word_wr) begin
                    word_busy <= 1'b1;
                    latched_data <= word_data;
                    latched_addr <= word_addr;
                    latched_chip_sel <= word_addr[21];
                    latched_wstrb <= word_wstrb;
                    if (word_wstrb[1:0] == 2'b00)
                        state <= ST_WR_HI;
                    else
                        state <= ST_WR_LO;
                end else if (word_rd) begin
                    word_busy <= 1'b1;
                    latched_addr <= word_addr;
                    latched_chip_sel <= word_addr[21];
                    state <= ST_RD_LO;
                end
            end

            ST_DONE: begin
                word_busy <= 1'b0;
                state <= ST_IDLE;
            end

            // Write LO
            ST_WR_LO: begin
                psram_bank_sel <= latched_chip_sel;
                psram_addr <= addr_lo;
                psram_data_in <= latched_data[15:0];
                psram_write_low <= latched_wstrb[0];
                psram_write_high <= latched_wstrb[1];
                psram_write_en <= 1'b1;
                state <= ST_WR_LO_BSY;
            end
            ST_WR_LO_BSY: if (psram_busy) state <= ST_WR_LO_WAI;
            ST_WR_LO_WAI: begin
                if (!psram_busy) begin
                    if (latched_wstrb[3:2] == 2'b00)
                        state <= ST_DONE;
                    else
                        state <= ST_WR_HI;
                end
            end

            // Write HI
            ST_WR_HI: begin
                psram_bank_sel <= latched_chip_sel;
                psram_addr <= addr_hi;
                psram_data_in <= latched_data[31:16];
                psram_write_low <= latched_wstrb[2];
                psram_write_high <= latched_wstrb[3];
                psram_write_en <= 1'b1;
                state <= ST_WR_HI_BSY;
            end
            ST_WR_HI_BSY: if (psram_busy) state <= ST_WR_HI_WAI;
            ST_WR_HI_WAI: if (!psram_busy) state <= ST_DONE;

            // Read LO
            ST_RD_LO: begin
                psram_bank_sel <= latched_chip_sel;
                psram_addr <= addr_lo;
                psram_read_en <= 1'b1;
                state <= ST_RD_LO_BSY;
            end
            ST_RD_LO_BSY: if (psram_busy) state <= ST_RD_LO_WAI;
            ST_RD_LO_WAI: begin
                if (!psram_busy) begin
                    lo_captured <= psram_data_out;
                    state <= ST_RD_HI;
                end
            end

            // Read HI
            ST_RD_HI: begin
                psram_bank_sel <= latched_chip_sel;
                psram_addr <= addr_hi;
                psram_read_en <= 1'b1;
                state <= ST_RD_HI_BSY;
            end
            ST_RD_HI_BSY: if (psram_busy) state <= ST_RD_HI_WAI;
            ST_RD_HI_WAI: begin
                if (!psram_busy) begin
                    word_q <= {psram_data_out, lo_captured};
                    word_q_valid <= 1'b1;
                    state <= ST_DONE;
                end
            end

            default: state <= ST_IDLE;
        endcase
    end
end

endmodule
