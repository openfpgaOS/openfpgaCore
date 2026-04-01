//
// Behavioral PSRAM Word Model for Verilator
//
// Simple word-level PSRAM model for CRAM1 save path testing.
// Supports single-word reads/writes and burst reads.
// 4M words (16MB) — covers one CRAM chip.
//

`default_nettype none

module psram_word_model (
    input wire clk,
    input wire reset_n,

    // Word-level interface (matches cpu_system PSRAM output)
    input  wire        word_rd,
    input  wire        word_wr,
    input  wire [25:0] word_addr,       // addr[27:2] from cpu_system
    input  wire [31:0] word_wdata,
    input  wire [3:0]  word_wstrb,
    output reg  [31:0] word_rdata,
    output reg         word_busy,
    output reg         word_rdata_valid,

    // Burst read interface
    input  wire        burst_rd,
    input  wire [5:0]  burst_len,
    output reg         burst_rdata_valid,
    output reg  [31:0] burst_rdata,

    // Direct read port (for bridge responder to read CRAM1)
    input  wire        direct_rd,
    input  wire [21:0] direct_addr,     // Word address within this model
    output reg  [31:0] direct_rdata,
    output reg         direct_rdata_valid,

    // Backdoor write
    input  wire        bd_we,
    input  wire [21:0] bd_addr,
    input  wire [31:0] bd_wdata
);

wire reset = ~reset_n;

// 4M words (16MB)
reg [31:0] mem [0:4194303];

// FSM
localparam S_IDLE       = 3'd0;
localparam S_RD_DELAY   = 3'd1;
localparam S_RD_DATA    = 3'd2;
localparam S_BURST_DATA = 3'd3;
localparam S_WR_BUSY    = 3'd4;

reg [2:0] state;
reg [25:0] addr_r;
reg [5:0]  burst_remaining;
reg [3:0]  delay_count;

// Address mapping: word_addr[25:22] carries addr[27:24] for target decode
// CRAM1 = addr[27:24] == 0x1 or 0x9
// We use word_addr[21:0] as the memory index (22-bit = 4M words)
wire [21:0] mem_addr = word_addr[21:0];

integer i;
initial begin
    for (i = 0; i < 4194304; i = i + 1)
        mem[i] = 32'hFFFFFFFF;
end

always @(posedge clk or posedge reset) begin
    if (reset) begin
        state <= S_IDLE;
        word_busy <= 0;
        word_rdata <= 0;
        word_rdata_valid <= 0;
        burst_rdata_valid <= 0;
        burst_rdata <= 0;
        direct_rdata <= 0;
        direct_rdata_valid <= 0;
        addr_r <= 0;
        burst_remaining <= 0;
        delay_count <= 0;
    end else begin
        // Defaults
        word_rdata_valid <= 0;
        burst_rdata_valid <= 0;
        direct_rdata_valid <= 0;

        // Backdoor write (always available)
        if (bd_we)
            mem[bd_addr] <= bd_wdata;

        // Direct read port (1-cycle latency for bridge responder)
        if (direct_rd) begin
            direct_rdata <= mem[direct_addr];
            direct_rdata_valid <= 1;
        end

        case (state)

        S_IDLE: begin
            word_busy <= 0;
            if (word_wr) begin
                // Single-word write: apply byte strobes
                if (word_wstrb[0]) mem[mem_addr][7:0]   <= word_wdata[7:0];
                if (word_wstrb[1]) mem[mem_addr][15:8]  <= word_wdata[15:8];
                if (word_wstrb[2]) mem[mem_addr][23:16] <= word_wdata[23:16];
                if (word_wstrb[3]) mem[mem_addr][31:24] <= word_wdata[31:24];
                word_busy <= 1;
                delay_count <= 2;
                state <= S_WR_BUSY;
            end else if (word_rd) begin
                addr_r <= word_addr;
                word_busy <= 1;
                delay_count <= 2;
                state <= S_RD_DELAY;
            end else if (burst_rd) begin
                addr_r <= word_addr;
                burst_remaining <= burst_len;
                word_busy <= 1;
                delay_count <= 3;
                state <= S_RD_DELAY;
            end
        end

        S_RD_DELAY: begin
            if (delay_count > 0)
                delay_count <= delay_count - 1;
            else begin
                if (burst_remaining > 0 || (state == S_RD_DELAY && burst_remaining == 0 && burst_len > 0))
                    state <= S_BURST_DATA;
                else
                    state <= S_RD_DATA;
            end
        end

        S_RD_DATA: begin
            // Single-word read result
            word_rdata <= mem[addr_r[21:0]];
            word_rdata_valid <= 1;
            word_busy <= 0;
            state <= S_IDLE;
        end

        S_BURST_DATA: begin
            // Burst read: one word per cycle
            burst_rdata <= mem[addr_r[21:0]];
            burst_rdata_valid <= 1;
            addr_r <= addr_r + 1;
            if (burst_remaining == 0) begin
                word_busy <= 0;
                state <= S_IDLE;
            end else begin
                burst_remaining <= burst_remaining - 1;
            end
        end

        S_WR_BUSY: begin
            if (delay_count > 0)
                delay_count <= delay_count - 1;
            else begin
                word_busy <= 0;
                state <= S_IDLE;
            end
        end

        default: state <= S_IDLE;
        endcase
    end
end

endmodule
