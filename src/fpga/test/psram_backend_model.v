//
// Behavioral Backend Model for axi_psram_slave Verilator Testing
//
// Simulates the word-level PSRAM interface (what psram_controller + target_mux
// provides in real hardware). Maintains a flat memory array indexed by
// {target_select[3:0], local_addr[15:0]} to isolate CRAM0/CRAM1/SRAM spaces.
//
// Responds to single-word reads/writes and burst reads with configurable latency
// matching the real psram_controller handshake patterns.
//

`default_nettype none

module psram_backend_model #(
    parameter RD_LATENCY = 8,         // Cycles from rd capture to rdata_valid
    parameter WR_LATENCY = 8,         // Cycles busy stays high after wr capture
    parameter BURST_INIT_LATENCY = 4  // Cycles before first burst word streams
)(
    input wire clk,
    input wire reset_n,

    // Word interface (from axi_psram_slave)
    input wire         psram_rd,
    input wire         psram_wr,
    input wire  [25:0] psram_addr,
    input wire  [31:0] psram_wdata,
    input wire  [3:0]  psram_wstrb,
    output reg  [31:0] psram_rdata,
    output reg         psram_busy,
    output reg         psram_rdata_valid,

    // Burst read interface (from axi_psram_slave)
    input wire         psram_burst_rd,
    input wire  [5:0]  psram_burst_len,
    output reg         psram_burst_rdata_valid,
    output reg  [31:0] psram_burst_rdata,

    // Burst write interface (from axi_psram_slave)
    input wire         psram_burst_wr,
    input wire  [5:0]  psram_burst_wr_len,
    input wire  [31:0] psram_burst_wdata,
    input wire  [3:0]  psram_burst_wstrb,
    output reg         psram_burst_wdata_next
);

// Memory: {addr[25:22] target, addr[15:0] local} = 20-bit index
// 1M words = 4MB, isolates CRAM0/CRAM1/SRAM address spaces
localparam MEM_BITS = 20;
reg [31:0] mem [0:(1<<MEM_BITS)-1];

wire [MEM_BITS-1:0] addr_idx = {psram_addr[25:22], psram_addr[15:0]};

// FSM
localparam [2:0] S_IDLE         = 3'd0;
localparam [2:0] S_RD           = 3'd1;
localparam [2:0] S_WR           = 3'd2;
localparam [2:0] S_BURST_WAIT   = 3'd3;
localparam [2:0] S_BURST_STREAM = 3'd4;
localparam [2:0] S_BURST_WR     = 3'd5;
localparam [2:0] S_BURST_WR_WAIT = 3'd6;

reg [2:0] state;
reg [7:0] counter;

// Latched command state
reg [MEM_BITS-1:0] latched_idx;
reg [31:0] latched_wdata;
reg [3:0]  latched_wstrb;

// Burst state
reg [3:0]  burst_target;
reg [15:0] burst_local;
reg [5:0]  burst_remaining;
wire [MEM_BITS-1:0] burst_idx = {burst_target, burst_local};

integer i;
initial begin
    for (i = 0; i < (1 << MEM_BITS); i = i + 1)
        mem[i] = 32'hDEAD_DEAD;
end

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        state <= S_IDLE;
        psram_busy <= 0;
        psram_rdata <= 0;
        psram_rdata_valid <= 0;
        psram_burst_rdata_valid <= 0;
        psram_burst_rdata <= 0;
        psram_burst_wdata_next <= 0;
        counter <= 0;
        latched_idx <= 0;
        latched_wdata <= 0;
        latched_wstrb <= 0;
        burst_target <= 0;
        burst_local <= 0;
        burst_remaining <= 0;
    end else begin
        // Default: clear pulsed outputs
        psram_rdata_valid <= 0;
        psram_burst_rdata_valid <= 0;
        psram_burst_wdata_next <= 0;

        case (state)
        S_IDLE: begin
            psram_busy <= 0;
            if (psram_burst_wr) begin
                psram_busy <= 1;
                burst_target <= psram_addr[25:22];
                burst_local <= psram_addr[15:0];
                burst_remaining <= psram_burst_wr_len;
                state <= S_BURST_WR;
            end else if (psram_burst_rd) begin
                psram_busy <= 1;
                burst_target <= psram_addr[25:22];
                burst_local <= psram_addr[15:0];
                burst_remaining <= psram_burst_len;
                counter <= BURST_INIT_LATENCY;
                state <= S_BURST_WAIT;
            end else if (psram_rd) begin
                psram_busy <= 1;
                latched_idx <= addr_idx;
                counter <= RD_LATENCY;
                state <= S_RD;
            end else if (psram_wr) begin
                psram_busy <= 1;
                latched_idx <= addr_idx;
                latched_wdata <= psram_wdata;
                latched_wstrb <= psram_wstrb;
                counter <= WR_LATENCY;
                state <= S_WR;
            end
        end

        S_RD: begin
            if (counter == 0) begin
                psram_rdata <= mem[latched_idx];
                psram_rdata_valid <= 1;
                psram_busy <= 0;
                state <= S_IDLE;
            end else
                counter <= counter - 8'd1;
        end

        S_WR: begin
            if (counter == 0) begin
                // Apply byte strobes via read-modify-write
                mem[latched_idx] <= {
                    latched_wstrb[3] ? latched_wdata[31:24] : mem[latched_idx][31:24],
                    latched_wstrb[2] ? latched_wdata[23:16] : mem[latched_idx][23:16],
                    latched_wstrb[1] ? latched_wdata[15:8]  : mem[latched_idx][15:8],
                    latched_wstrb[0] ? latched_wdata[7:0]   : mem[latched_idx][7:0]
                };
                psram_busy <= 0;
                state <= S_IDLE;
            end else
                counter <= counter - 8'd1;
        end

        S_BURST_WAIT: begin
            if (counter == 0)
                state <= S_BURST_STREAM;
            else
                counter <= counter - 8'd1;
        end

        S_BURST_STREAM: begin
            psram_burst_rdata <= mem[burst_idx];
            psram_burst_rdata_valid <= 1;
            burst_local <= burst_local + 16'd1;
            if (burst_remaining == 0) begin
                psram_busy <= 0;
                state <= S_IDLE;
            end else
                burst_remaining <= burst_remaining - 6'd1;
        end

        S_BURST_WR: begin
            // Write current word using burst_wdata/burst_wstrb
            mem[{burst_target, burst_local}] <= {
                psram_burst_wstrb[3] ? psram_burst_wdata[31:24] : mem[{burst_target, burst_local}][31:24],
                psram_burst_wstrb[2] ? psram_burst_wdata[23:16] : mem[{burst_target, burst_local}][23:16],
                psram_burst_wstrb[1] ? psram_burst_wdata[15:8]  : mem[{burst_target, burst_local}][15:8],
                psram_burst_wstrb[0] ? psram_burst_wdata[7:0]   : mem[{burst_target, burst_local}][7:0]
            };
            burst_local <= burst_local + 16'd1;
            if (burst_remaining == 0) begin
                psram_busy <= 0;
                state <= S_IDLE;
            end else begin
                burst_remaining <= burst_remaining - 6'd1;
                psram_burst_wdata_next <= 1;
                state <= S_BURST_WR_WAIT;
            end
        end

        S_BURST_WR_WAIT: begin
            // Wait 1 cycle for AXI slave to update psram_burst_wdata (NBA)
            state <= S_BURST_WR;
        end

        default: state <= S_IDLE;
        endcase
    end
end

endmodule
