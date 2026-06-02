//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

//
// cram0_sim_model.v — behavioural CRAM0 backing for tb_system.v.
//
// CRAM0 on hardware is an async pseudo-SRAM on the APF bridge clock,
// driven through cram0_controller + cram0_phy.  For sim we skip both
// and present the same word-level interface cram0_controller exposes,
// running directly on clk_74a.
//
// Behaviour:
//   - word_rd  → combinational read of mem[word_addr], delivered 2
//                clk_74a cycles later with word_q_valid pulsed.
//   - word_wr  → byte-enabled write into mem[word_addr]; word_busy
//                rises for 3 cycles so the cram0_cdc "saw-busy" gate
//                sees a clean rising edge.
//
// 16 MB = 4 Mword × 32-bit.  Uninitialised on reset; the tb_system
// testbench poke-loads os.bin into mem[] through the public_flat_rw
// backdoor before releasing reset_n.
//

`default_nettype none

module cram0_sim_model (
    input  wire        clk,
    input  wire        reset_n,

    // Word interface (same as cram0_controller)
    input  wire        word_rd,
    input  wire        word_wr,
    input  wire [21:0] word_addr,
    input  wire [31:0] word_data,
    input  wire [3:0]  word_wstrb,

    output reg  [31:0] word_q,
    output reg         word_busy,
    output reg         word_q_valid
);

localparam MEM_WORDS = 4 * 1024 * 1024;
reg [31:0] mem [0:MEM_WORDS-1] /*verilator public_flat_rw*/;

// Simple 3-state FSM: IDLE → BUSY1 → BUSY2 → back to IDLE.  For reads
// the second busy cycle drives word_q_valid; for writes it just drops
// busy so cram0_cdc's FSM sees the falling edge.
localparam S_IDLE  = 2'd0;
localparam S_BUSY1 = 2'd1;
localparam S_BUSY2 = 2'd2;

reg [1:0]  state;
reg        op_is_read;
reg [21:0] op_addr;
reg [31:0] op_wdata;
reg [3:0]  op_wstrb;

integer i;
initial begin
    for (i = 0; i < MEM_WORDS; i = i + 1)
        mem[i] = 32'h0;
end

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        state        <= S_IDLE;
        word_q       <= 32'd0;
        word_busy    <= 1'b0;
        word_q_valid <= 1'b0;
        op_is_read   <= 1'b0;
        op_addr      <= 22'd0;
        op_wdata     <= 32'd0;
        op_wstrb     <= 4'd0;
    end else begin
        word_q_valid <= 1'b0;

        case (state)
        S_IDLE: begin
            word_busy <= 1'b0;
            if (word_rd) begin
                op_is_read <= 1'b1;
                op_addr    <= word_addr;
                word_busy  <= 1'b1;
                state      <= S_BUSY1;
            end else if (word_wr) begin
                op_is_read <= 1'b0;
                op_addr    <= word_addr;
                op_wdata   <= word_data;
                op_wstrb   <= word_wstrb;
                word_busy  <= 1'b1;
                state      <= S_BUSY1;
            end
        end

        S_BUSY1: begin
            word_busy <= 1'b1;
            state     <= S_BUSY2;
        end

        S_BUSY2: begin
            word_busy <= 1'b0;  // deassert next cycle
            if (op_is_read) begin
                word_q       <= mem[op_addr];
                word_q_valid <= 1'b1;
            end else begin
                if (op_wstrb[0]) mem[op_addr][ 7: 0] <= op_wdata[ 7: 0];
                if (op_wstrb[1]) mem[op_addr][15: 8] <= op_wdata[15: 8];
                if (op_wstrb[2]) mem[op_addr][23:16] <= op_wdata[23:16];
                if (op_wstrb[3]) mem[op_addr][31:24] <= op_wdata[31:24];
            end
            state <= S_IDLE;
        end

        default: state <= S_IDLE;
        endcase
    end
end

endmodule
