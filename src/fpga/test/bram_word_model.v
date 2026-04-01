//
// Behavioral BRAM + Peripheral Word-Level Model
//
// Replaces bram_model.v (AXI4) with a word-level interface matching
// the periph_slave protocol. For Verilator simulation.
//
// Address map:
//   0x00000000-0x0003FFFF: BRAM (256KB, 64K words)
//   0x40000000-0x400000FF: System registers (stubs)
//   0x4F000004:            UART TX
//
// Timing:
//   BRAM reads:  1 beat per cycle (burst supported)
//   BRAM writes: immediate, wr_done next cycle
//

`default_nettype none

module bram_word_model (
    input wire clk,
    input wire reset_n,

    // Word-level slave interface (same as periph_slave)
    input  wire        req_rd,
    input  wire        req_wr,
    input  wire [31:0] req_addr_in,
    input  wire [31:0] req_wdata_in,
    input  wire [3:0]  req_wstrb_in,
    input  wire [7:0]  req_burst_len_in,
    output reg  [31:0] rsp_rdata,
    output reg         rsp_rdata_valid,
    output reg         rsp_rdata_last,
    output reg         rsp_wr_done,
    output wire        rsp_busy,

    // Backdoor BRAM access
    input  wire        bd_we,
    input  wire [15:0] bd_addr,
    input  wire [31:0] bd_wdata,
    output wire [31:0] bd_rdata,

    // UART output (intercepts writes to 0x4F000004)
    output reg         uart_tx_valid,
    output reg  [7:0]  uart_tx_byte,

    // Dataslot interface (directly exposed to top-level for bridge)
    output reg         target_dataslot_read,
    output reg         target_dataslot_write,
    output reg  [15:0] target_dataslot_id,
    output reg  [31:0] target_dataslot_bridgeaddr,
    output reg  [31:0] target_dataslot_length,
    input  wire        target_dataslot_ack,
    input  wire        target_dataslot_done,

    // Save datatable
    output reg  [3:0]  save_dt_slot,
    output reg  [31:0] save_dt_size,
    output reg         save_dt_commit
);

    // ============================================================
    // Memory array: 64K words (256KB)
    // ============================================================
    reg [31:0] mem [0:65535];

    integer i;
    initial begin
        for (i = 0; i < 65536; i = i + 1)
            mem[i] = 32'h00000013;  // NOP (addi x0, x0, 0)
    end

    // Backdoor read (active anytime)
    assign bd_rdata = mem[bd_addr];

    // ============================================================
    // Cycle counter for SYS_CYCLE registers
    // ============================================================
    reg [63:0] sys_cycle;
    wire [63:0] fast_cycle = sys_cycle * 10;  // 10x speedup

    always @(posedge clk)
        if (!reset_n) sys_cycle <= 0;
        else sys_cycle <= sys_cycle + 1;

    // ============================================================
    // Dataslot handshake state
    // ============================================================
    reg        ds_ack_latched;
    reg        ds_done_latched;
    reg        ds_ready;

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            ds_ack_latched  <= 1'b0;
            ds_done_latched <= 1'b0;
            ds_ready        <= 1'b1;
        end else begin
            if (target_dataslot_ack)
                ds_ack_latched <= 1'b1;
            if (target_dataslot_done) begin
                ds_done_latched <= 1'b1;
                ds_ready        <= 1'b1;
            end
        end
    end

    // ============================================================
    // FSM
    // ============================================================
    localparam S_IDLE    = 2'd0,
               S_RD_DATA = 2'd1,
               S_WR_DONE = 2'd2;

    reg [1:0]  state;
    reg [31:0] rd_addr;
    reg [7:0]  rd_len;
    reg [7:0]  rd_cnt;

    assign rsp_busy = (state != S_IDLE);

    // Helper: read data from address
    function [31:0] read_data;
        input [31:0] a;
        begin
            if (a[31:24] == 8'h4F && a[3:2] == 2'b00)
                read_data = 32'h00000003;  // UART status: present + TX ready
            else if (a[31:24] == 8'h40) begin
                case (a[7:0])
                8'h00: read_data = 32'h00000003;            // SYS_STATUS: ready + allcomplete
                8'h04: read_data = fast_cycle[31:0];         // SYS_CYCLE_LO
                8'h08: read_data = fast_cycle[63:32];        // SYS_CYCLE_HI
                8'h3C: read_data = {24'd0, ds_ready, 3'b000, ds_done_latched, ds_ack_latched, 2'b00};  // DS_STATUS
                8'h68: read_data = 32'h00000000;             // SYS_GAME_ID
                8'hD0: read_data = 32'h00000000;             // DMA_STATUS: idle
                default: read_data = 32'h00000000;
                endcase
            end else if (a[31:20] == 12'h000)
                read_data = mem[a[17:2]];
            else
                read_data = 32'h00000000;
        end
    endfunction

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            state           <= S_IDLE;
            rsp_rdata       <= 32'd0;
            rsp_rdata_valid <= 1'b0;
            rsp_rdata_last  <= 1'b0;
            rsp_wr_done     <= 1'b0;
            uart_tx_valid   <= 1'b0;
            uart_tx_byte    <= 8'd0;
            rd_addr         <= 32'd0;
            rd_len          <= 8'd0;
            rd_cnt          <= 8'd0;
            target_dataslot_read  <= 1'b0;
            target_dataslot_write <= 1'b0;
            target_dataslot_id    <= 16'd0;
            target_dataslot_bridgeaddr <= 32'd0;
            target_dataslot_length     <= 32'd0;
            save_dt_slot    <= 4'd0;
            save_dt_size    <= 32'd0;
            save_dt_commit  <= 1'b0;
        end else begin
            // Default: pulses deassert each cycle
            rsp_rdata_valid <= 1'b0;
            rsp_rdata_last  <= 1'b0;
            rsp_wr_done     <= 1'b0;
            uart_tx_valid   <= 1'b0;
            save_dt_commit  <= 1'b0;
            target_dataslot_read  <= 1'b0;
            target_dataslot_write <= 1'b0;

            case (state)
            S_IDLE: begin
                if (req_rd) begin
                    // Start burst read
                    rd_addr <= req_addr_in;
                    rd_len  <= req_burst_len_in;
                    rd_cnt  <= 8'd0;
                    state   <= S_RD_DATA;
                end else if (req_wr) begin
                    // Handle write immediately
                    if (req_addr_in[31:24] == 8'h4F && req_addr_in[3:2] == 2'b01) begin
                        // UART TX: write to 0x4F000004
                        uart_tx_valid <= 1'b1;
                        uart_tx_byte  <= req_wdata_in[7:0];
                    end else if (req_addr_in[31:24] == 8'h40) begin
                        // System register writes
                        case (req_addr_in[7:0])
                        8'h20: target_dataslot_id <= req_wdata_in[15:0];          // DS_SLOT_ID
                        8'h28: target_dataslot_bridgeaddr <= req_wdata_in;        // DS_BRIDGE_ADDR
                        8'h2C: target_dataslot_length <= req_wdata_in;            // DS_LENGTH
                        8'h38: begin                                               // DS_COMMAND
                            if (req_wdata_in[7:0] == 8'd1)
                                target_dataslot_read <= 1'b1;
                            else if (req_wdata_in[7:0] == 8'd2)
                                target_dataslot_write <= 1'b1;
                            // Clear latched state for new command
                            ds_ack_latched  <= 1'b0;
                            ds_done_latched <= 1'b0;
                            ds_ready        <= 1'b0;
                        end
                        8'h48: save_dt_slot <= req_wdata_in[3:0];                 // SAVE_DT_SLOT
                        8'h4C: begin                                               // SAVE_DT_SIZE
                            save_dt_size   <= req_wdata_in;
                            save_dt_commit <= 1'b1;
                        end
                        default: ;  // Ignore other register writes
                        endcase
                    end else if (req_addr_in[31:20] == 12'h000) begin
                        // BRAM write with byte enables
                        if (req_wstrb_in[0]) mem[req_addr_in[17:2]][7:0]   <= req_wdata_in[7:0];
                        if (req_wstrb_in[1]) mem[req_addr_in[17:2]][15:8]  <= req_wdata_in[15:8];
                        if (req_wstrb_in[2]) mem[req_addr_in[17:2]][23:16] <= req_wdata_in[23:16];
                        if (req_wstrb_in[3]) mem[req_addr_in[17:2]][31:24] <= req_wdata_in[31:24];
                    end
                    state <= S_WR_DONE;
                end
            end

            S_RD_DATA: begin
                rsp_rdata       <= read_data(rd_addr);
                rsp_rdata_valid <= 1'b1;
                rsp_rdata_last  <= (rd_cnt == rd_len);
                rd_addr         <= rd_addr + 4;
                rd_cnt          <= rd_cnt + 1;
                if (rd_cnt == rd_len) begin
                    state <= S_IDLE;
                end
            end

            S_WR_DONE: begin
                rsp_wr_done <= 1'b1;
                state       <= S_IDLE;
            end

            default: state <= S_IDLE;
            endcase
        end

        // Backdoor write (from C++ harness, active during reset or runtime)
        if (bd_we) begin
            mem[bd_addr] <= bd_wdata;
        end
    end

endmodule
