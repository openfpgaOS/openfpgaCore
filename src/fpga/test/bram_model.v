//
// Behavioral BRAM model with AXI4 slave interface
//
// Responds to both I-fetch (read-only) and local (read/write) accesses.
// Single-cycle read latency for cached burst fetches.
// Supports firmware loading from C++ via backdoor write port.
//
// Address map: 0x00000000 - 0x0003FFFF (256KB, matching VexiiRiscv region)
//

`default_nettype none

module bram_model #(
    parameter ADDR_BITS = 16  // 64K words = 256KB
)(
    input wire clk,
    input wire reset_n,

    // AXI4 Read-only port (I-fetch)
    input  wire        fetch_ar_valid,
    output reg         fetch_ar_ready,
    input  wire [31:0] fetch_ar_addr,
    input  wire [7:0]  fetch_ar_len,

    output reg         fetch_r_valid,
    input  wire        fetch_r_ready,
    output reg  [31:0] fetch_r_data,
    output reg  [1:0]  fetch_r_resp,
    output reg         fetch_r_last,

    // AXI4 Read/Write port (local peripheral access)
    input  wire        local_ar_valid,
    output reg         local_ar_ready,
    input  wire [31:0] local_ar_addr,
    input  wire [7:0]  local_ar_len,

    output reg         local_r_valid,
    input  wire        local_r_ready,
    output reg  [31:0] local_r_data,
    output reg  [1:0]  local_r_resp,
    output reg         local_r_last,

    input  wire        local_aw_valid,
    output reg         local_aw_ready,
    input  wire [31:0] local_aw_addr,
    input  wire [7:0]  local_aw_len,

    input  wire        local_w_valid,
    output reg         local_w_ready,
    input  wire [31:0] local_w_data,
    input  wire [3:0]  local_w_strb,
    input  wire        local_w_last,

    output reg         local_b_valid,
    input  wire        local_b_ready,
    output reg  [1:0]  local_b_resp,

    // Backdoor: C++ can read/write memory directly
    input  wire                   bd_we,
    input  wire [ADDR_BITS-1:0]   bd_addr,
    input  wire [31:0]            bd_wdata,
    output wire [31:0]            bd_rdata,

    // UART output (intercepts writes to 0x4F000004)
    output reg                    uart_tx_valid,
    output reg  [7:0]             uart_tx_byte
);

// Cycle counter for SYS_CYCLE registers (1000x speed for fast sim)
reg [63:0] sys_cycle;
wire [63:0] fast_cycle = sys_cycle * 1000;
always @(posedge clk)
    if (!reset_n) sys_cycle <= 0;
    else sys_cycle <= sys_cycle + 1;

// Memory array
reg [31:0] mem [0:(1<<ADDR_BITS)-1];
wire [ADDR_BITS-1:0] addr_mask = {ADDR_BITS{1'b1}};

// Backdoor access
assign bd_rdata = mem[bd_addr];
always @(posedge clk)
    if (bd_we) mem[bd_addr] <= bd_wdata;

integer i;
initial begin
    for (i = 0; i < (1<<ADDR_BITS); i = i + 1)
        mem[i] = 32'h00000013;  // NOP (addi x0, x0, 0)
    fetch_ar_ready = 0; fetch_r_valid = 0;
    local_ar_ready = 0; local_r_valid = 0;
    local_aw_ready = 0; local_w_ready = 0;
    local_b_valid = 0;
    uart_tx_valid = 0; uart_tx_byte = 0;
end

// ============================================================
// I-Fetch read FSM (burst read)
// ============================================================
reg [1:0] f_state;
reg [31:0] f_addr;
reg [7:0] f_len;
reg [7:0] f_cnt;

localparam F_IDLE = 0, F_DATA = 1;

always @(posedge clk) begin
    fetch_ar_ready <= 0;
    fetch_r_valid <= 0;

    if (!reset_n) begin
        f_state <= F_IDLE;
    end else case (f_state)
    F_IDLE: begin
        if (fetch_ar_valid) begin
            fetch_ar_ready <= 1;
            f_addr <= fetch_ar_addr;
            f_len <= fetch_ar_len;
            f_cnt <= 0;
            f_state <= F_DATA;
        end
    end
    F_DATA: begin
        fetch_r_valid <= 1;
        fetch_r_data <= mem[f_addr[ADDR_BITS+1:2]];
        fetch_r_resp <= 2'b00;
        fetch_r_last <= (f_cnt == f_len);
        if (fetch_r_ready) begin
            f_addr <= f_addr + 4;
            f_cnt <= f_cnt + 1;
            if (f_cnt == f_len) begin
                f_state <= F_IDLE;
            end
        end else begin
            fetch_r_valid <= 1;  // Hold valid
        end
    end
    endcase
end

// ============================================================
// Local read/write FSM
// ============================================================
reg [2:0] l_state;
reg [31:0] l_raddr, l_waddr;
reg [7:0] l_rlen, l_rcnt;

localparam L_IDLE = 0, L_RDATA = 1, L_WDATA = 2, L_BRESP = 3;

always @(posedge clk) begin
    local_ar_ready <= 0;
    local_aw_ready <= 0;
    local_w_ready <= 0;
    local_r_valid <= 0;
    local_b_valid <= 0;
    uart_tx_valid <= 0;

    if (!reset_n) begin
        l_state <= L_IDLE;
    end else case (l_state)
    L_IDLE: begin
        if (local_ar_valid) begin
            local_ar_ready <= 1;
            l_raddr <= local_ar_addr;
            l_rlen <= local_ar_len;
            l_rcnt <= 0;
            l_state <= L_RDATA;
        end else if (local_aw_valid) begin
            local_aw_ready <= 1;
            l_waddr <= local_aw_addr;
            l_state <= L_WDATA;
        end
    end
    L_RDATA: begin
        local_r_valid <= 1;
        if (l_raddr[31:24] == 8'h4F && l_raddr[3:2] == 2'b00)
            local_r_data <= 32'h00000003;  // UART: present(bit0) + TX ready(bit1)
        else if (l_raddr[31:24] == 8'h40) begin
            // System register stubs (0x40000000+)
            case (l_raddr[7:0])
            8'h00: local_r_data <= 32'h00000003;  // SYS_STATUS: ready + all_complete
            8'h04: local_r_data <= fast_cycle[31:0];  // SYS_CYCLE_LO
            8'h08: local_r_data <= fast_cycle[63:32]; // SYS_CYCLE_HI
            8'h3C: local_r_data <= 32'h00000023;  // DS_STATUS: ACK+DONE+READY
            8'h68: local_r_data <= 32'h00000000;  // SYS_GAME_ID
            8'hD0: local_r_data <= 32'h00000000;  // DMA_STATUS: idle
            default: local_r_data <= 32'h00000000;
            endcase
        end else
            local_r_data <= mem[l_raddr[ADDR_BITS+1:2]];
        local_r_resp <= 2'b00;
        local_r_last <= (l_rcnt == l_rlen);
        if (local_r_ready) begin
            l_raddr <= l_raddr + 4;
            l_rcnt <= l_rcnt + 1;
            if (l_rcnt == l_rlen)
                l_state <= L_IDLE;
        end else begin
            local_r_valid <= 1;
        end
    end
    L_WDATA: begin
        if (local_w_valid) begin
            local_w_ready <= 1;
            if (l_waddr[31:24] == 8'h4F && l_waddr[3:2] == 2'b01) begin
                // UART TX: intercept write to 0x4F000004
                uart_tx_valid <= 1;
                uart_tx_byte <= local_w_data[7:0];
            end else if (l_waddr[ADDR_BITS+1:2] < (1 << ADDR_BITS)) begin
                // BRAM write (only within address range)
                if (local_w_strb[0]) mem[l_waddr[ADDR_BITS+1:2]][7:0]   <= local_w_data[7:0];
                if (local_w_strb[1]) mem[l_waddr[ADDR_BITS+1:2]][15:8]  <= local_w_data[15:8];
                if (local_w_strb[2]) mem[l_waddr[ADDR_BITS+1:2]][23:16] <= local_w_data[23:16];
                if (local_w_strb[3]) mem[l_waddr[ADDR_BITS+1:2]][31:24] <= local_w_data[31:24];
            end
            l_waddr <= l_waddr + 4;
            if (local_w_last)
                l_state <= L_BRESP;
        end
    end
    L_BRESP: begin
        local_b_valid <= 1;
        local_b_resp <= 2'b00;
        if (local_b_ready)
            l_state <= L_IDLE;
        else
            local_b_valid <= 1;
    end
    endcase
end

endmodule
