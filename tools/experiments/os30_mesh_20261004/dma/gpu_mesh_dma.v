// SPDX-License-Identifier: Apache-2.0
// Private os30 mesh experiment. Read-only M2 DMA; storage is in the wrapper.
`default_nettype none
module gpu_mesh_dma (
    input wire clk,
    input wire reset_n,
    input wire abort,

    // Sequential segments assemble one job. The first claims a free bank;
    // commit publishes it only after every beat has reached the RAM.
    input wire desc_valid,
    output wire desc_ready,
    input wire desc_bank,
    input wire [31:0] desc_addr,
    input wire [7:0] desc_dst,
    input wire [8:0] desc_words,
    input wire desc_commit,
    output reg [1:0] bank_owned,
    output reg [1:0] bank_ready,
    output reg [1:0] bank_error,
    input wire [1:0] bank_release,
    output wire fetch_busy,

    // Connect directly to the selected bank's guaranteed-available write port.
    output wire wr_en,
    output wire wr_bank,
    output wire [7:0] wr_index,
    output wire [31:0] wr_data,

    output wire m_arvalid,
    input wire m_arready,
    output wire [31:0] m_araddr,
    output wire [7:0] m_arlen,
    input wire m_rvalid,
    output wire m_rready,
    input wire [31:0] m_rdata,
    input wire [1:0] m_rresp,
    input wire m_rlast
);
    localparam IDLE = 2'd0, ADDR = 2'd1, READ = 2'd2;
    reg [1:0] state;
    reg active_bank, active_commit, cancelled, failed;
    reg [31:0] addr;
    reg [7:0] index;
    reg [8:0] remaining;
    reg [3:0] burst_left;
    reg [2:0] burst_m1;

    // Eight-beat aligned bursts bound the time for which mesh traffic holds
    // the shared arbiter. Splitting at 32-byte boundaries also obeys AXI's
    // 4 KiB restriction without a wide boundary comparator.
    function [3:0] burst_words;
        input [2:0] word_offset;
        input [8:0] words;
        reg [3:0] room;
        begin
            room = 4'd8 - {1'b0, word_offset};
            burst_words = (words < {5'd0, room}) ? words[3:0] : room;
        end
    endfunction

    wire [9:0] desc_end = {2'd0, desc_dst} + {1'b0, desc_words};
    wire [32:0] desc_byte_end = {1'b0, desc_addr} +
                               {22'd0, desc_words, 2'b00};
    // End==2^32 is legal: the final addressed word still starts below it.
    wire bad_desc = (desc_addr[1:0] != 2'b00) || (desc_words == 0) ||
                    (desc_words > 9'd256) || (desc_end > 10'd256) ||
                    (desc_byte_end[32] && (desc_byte_end[31:0] != 0));
    wire bad_beat = (m_rresp != 2'b00) ||
                    (m_rlast != (burst_left == 4'd1));
    wire [31:0] next_addr = addr + 32'd4;
    wire [8:0] next_remaining = remaining - 9'd1;
    wire [3:0] first_burst = burst_words(desc_addr[4:2], desc_words);
    wire [3:0] next_burst = burst_words(next_addr[4:2], next_remaining);

    assign desc_ready = (state == IDLE) && !cancelled && !abort &&
                        !bank_ready[desc_bank] && reset_n;
    assign fetch_busy = (state != IDLE) || cancelled;
    assign m_arvalid = (state == ADDR) && reset_n;
    assign m_araddr = addr;
    assign m_arlen = {5'd0, burst_m1};
    // There is no response skid buffer: the bank write port belongs to DMA
    // throughout filling, so one beat can always retire every cycle.
    assign m_rready = (state == READ) && reset_n;
    assign wr_en = m_rvalid && m_rready && !cancelled && !abort &&
                   !failed && !bad_beat;
    assign wr_bank = active_bank;
    assign wr_index = index;
    assign wr_data = m_rdata;

    always @(posedge clk) begin
        if (!reset_n) begin
            state <= IDLE;
            bank_owned <= 2'b00;
            bank_ready <= 2'b00;
            bank_error <= 2'b00;
            active_bank <= 1'b0;
            active_commit <= 1'b0;
            cancelled <= 1'b0;
            failed <= 1'b0;
            addr <= 0;
            index <= 0;
            remaining <= 0;
            burst_left <= 0;
            burst_m1 <= 0;
        end else begin
            // Ready includes the consumer's processing ownership. A release
            // of a bank still being assembled is ignored, never an overwrite.
            bank_owned <= bank_owned & ~(bank_release & bank_ready);
            bank_ready <= bank_ready & ~bank_release;
            bank_error <= bank_error & ~(bank_release & bank_ready);

            case (state)
                IDLE: begin
                    cancelled <= 1'b0;
                    if (desc_valid && desc_ready) begin
                        bank_owned[desc_bank] <= 1'b1;
                        bank_error[desc_bank] <= bad_desc;
                        active_bank <= desc_bank;
                        active_commit <= desc_commit;
                        failed <= 1'b0;
                        if (bad_desc) begin
                            bank_ready[desc_bank] <= 1'b1;
                        end else begin
                            addr <= desc_addr;
                            index <= desc_dst;
                            remaining <= desc_words;
                            burst_left <= first_burst;
                            burst_m1 <= first_burst[2:0] - 3'd1;
                            state <= ADDR;
                        end
                    end
                end
                ADDR: begin
                    // Abort may not withdraw a stalled ARVALID. Finish that
                    // handshake, then consume the cancelled response burst.
                    if (abort) cancelled <= 1'b1;
                    if (m_arready) state <= READ;
                end
                READ: begin
                    if (abort) cancelled <= 1'b1;
                    if (m_rvalid) begin
                        if (burst_left != 0) burst_left <= burst_left - 4'd1;
                        if (bad_beat) failed <= 1'b1;
                        if (m_rlast) begin
                            if (cancelled || abort) begin
                                cancelled <= 1'b0;
                                state <= IDLE;
                            end else if (failed || bad_beat) begin
                                bank_ready[active_bank] <= 1'b1;
                                bank_error[active_bank] <= 1'b1;
                                state <= IDLE;
                            end else if (remaining == 9'd1) begin
                                if (active_commit) bank_ready[active_bank] <= 1'b1;
                                state <= IDLE;
                            end else begin
                                addr <= next_addr;
                                index <= index + 8'd1;
                                remaining <= next_remaining;
                                burst_left <= next_burst;
                                burst_m1 <= next_burst[2:0] - 3'd1;
                                state <= ADDR;
                            end
                        end else if (!cancelled && !abort && !failed && !bad_beat) begin
                            addr <= next_addr;
                            index <= index + 8'd1;
                            remaining <= next_remaining;
                        end
                    end
                end
                default: state <= IDLE;
            endcase

            // Abort invalidates all job ownership, but does not reset the AXI
            // fabric. New jobs are blocked until the existing burst drains.
            if (abort) begin
                bank_owned <= 2'b00;
                bank_ready <= 2'b00;
                bank_error <= 2'b00;
            end
        end
    end
endmodule
`default_nettype wire
