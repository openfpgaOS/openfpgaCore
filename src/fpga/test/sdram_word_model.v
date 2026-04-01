//
// Behavioral SDRAM Word-Level Model
//
// Replaces sdram_fast_model.v (AXI4) with a word-level interface
// matching the io_sdram output signals. For Verilator simulation.
//
// Timing:
//   Burst reads:  3 cycle latency, then 1 beat per cycle
//   Burst writes: accept first beat immediately, then 3 cycle latency
//                 before wr_data_next for subsequent beats
//
// 32MB memory (8M x 32-bit words), matching real SDRAM capacity.
//

`default_nettype none

module sdram_word_model (
    input wire clk,
    input wire reset_n,

    // Word interface (same as io_sdram)
    input  wire        word_rd,
    input  wire        word_wr,
    input  wire [23:0] word_addr,
    input  wire [31:0] word_data,
    input  wire [3:0]  word_wstrb,
    input  wire [3:0]  word_burst_len,
    input  wire [3:0]  word_burst_wr_len,
    output reg  [31:0] word_q,
    output reg         word_busy,
    output reg         word_q_valid,
    output reg         word_wr_data_next,

    // Burst write direct data (from arbiter)
    input  wire [31:0] burst_wr_direct_data,
    input  wire [3:0]  burst_wr_direct_strb,

    // Backdoor write (for firmware preloading)
    input  wire        bd_we,
    input  wire [23:0] bd_word_addr,
    input  wire [31:0] bd_wdata
);

    // 32MB = 8M words of 32 bits
    reg [31:0] mem [0:8388607];  // 8M x 32

    // FSM states
    localparam S_IDLE    = 3'd0,
               S_RD_WAIT = 3'd1,
               S_RD_DATA = 3'd2,
               S_WR_WAIT = 3'd3,
               S_WR_DATA = 3'd4;

    reg [2:0]  state;
    reg [22:0] addr;       // word address (23 bits for 8M words)
    reg [3:0]  burst_cnt;  // remaining beats
    reg [2:0]  wait_cnt;   // latency counter

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            state          <= S_IDLE;
            word_q         <= 32'd0;
            word_busy      <= 1'b0;
            word_q_valid   <= 1'b0;
            word_wr_data_next <= 1'b0;
            addr           <= 23'd0;
            burst_cnt      <= 4'd0;
            wait_cnt       <= 3'd0;
        end else begin
            // Defaults: pulses deassert each cycle
            word_q_valid      <= 1'b0;
            word_wr_data_next <= 1'b0;

            case (state)
            S_IDLE: begin
                if (word_rd) begin
                    addr      <= word_addr[22:0];
                    burst_cnt <= word_burst_len;
                    wait_cnt  <= 3'd2;  // 3 cycles total (0,1,2 -> first data on 3rd)
                    word_busy <= 1'b1;
                    state     <= S_RD_WAIT;
                end else if (word_wr) begin
                    // Accept first write beat immediately
                    if (word_wstrb[0]) mem[word_addr[22:0]][7:0]   <= word_data[7:0];
                    if (word_wstrb[1]) mem[word_addr[22:0]][15:8]  <= word_data[15:8];
                    if (word_wstrb[2]) mem[word_addr[22:0]][23:16] <= word_data[23:16];
                    if (word_wstrb[3]) mem[word_addr[22:0]][31:24] <= word_data[31:24];
                    addr <= word_addr[22:0] + 1;
                    if (word_burst_wr_len > 0) begin
                        burst_cnt <= word_burst_wr_len;  // remaining beats (not counting first)
                        wait_cnt  <= 3'd2;               // 3 cycle latency before wr_data_next
                        word_busy <= 1'b1;
                        state     <= S_WR_WAIT;
                    end else begin
                        // Single write: done immediately, pulse busy for 1 cycle
                        word_busy <= 1'b1;
                        state     <= S_IDLE;
                        // busy will be cleared next idle iteration
                    end
                end else begin
                    word_busy <= 1'b0;
                end
            end

            S_RD_WAIT: begin
                if (wait_cnt == 0) begin
                    // Deliver first beat
                    word_q       <= mem[addr];
                    word_q_valid <= 1'b1;
                    if (burst_cnt == 0) begin
                        state <= S_IDLE;
                    end else begin
                        addr      <= addr + 1;
                        burst_cnt <= burst_cnt - 1;
                        state     <= S_RD_DATA;
                    end
                end else begin
                    wait_cnt <= wait_cnt - 1;
                end
            end

            S_RD_DATA: begin
                word_q       <= mem[addr];
                word_q_valid <= 1'b1;
                if (burst_cnt == 0) begin
                    state <= S_IDLE;
                end else begin
                    addr      <= addr + 1;
                    burst_cnt <= burst_cnt - 1;
                end
            end

            S_WR_WAIT: begin
                if (wait_cnt == 0) begin
                    // Signal ready for next beat
                    word_wr_data_next <= 1'b1;
                    state <= S_WR_DATA;
                end else begin
                    wait_cnt <= wait_cnt - 1;
                end
            end

            S_WR_DATA: begin
                // Accept burst write data from direct port
                if (burst_wr_direct_strb[0]) mem[addr][7:0]   <= burst_wr_direct_data[7:0];
                if (burst_wr_direct_strb[1]) mem[addr][15:8]  <= burst_wr_direct_data[15:8];
                if (burst_wr_direct_strb[2]) mem[addr][23:16] <= burst_wr_direct_data[23:16];
                if (burst_wr_direct_strb[3]) mem[addr][31:24] <= burst_wr_direct_data[31:24];
                burst_cnt <= burst_cnt - 1;
                addr      <= addr + 1;
                if (burst_cnt == 1) begin
                    // Last beat accepted
                    state <= S_IDLE;
                end else begin
                    word_wr_data_next <= 1'b1;
                end
            end

            default: state <= S_IDLE;
            endcase
        end

        // Backdoor write (from C++ harness, active during reset or runtime)
        if (bd_we) begin
            mem[bd_word_addr[22:0]] <= bd_wdata;
        end
    end

endmodule
