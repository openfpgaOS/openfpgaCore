//
// Behavioral Bridge Responder for Verilator Save Path Testing
//
// Simulates the APF bridge save flow:
//   1. CPU triggers target_dataslot_write
//   2. Bridge reads CRAM1 data at bridge_addr
//   3. Bridge "writes to SD" (captured in save_buf for C++ verification)
//   4. Bridge asserts ACK then DONE
//
// The captured save data is accessible via backdoor read port.
//

`default_nettype none

module bridge_responder (
    input wire clk,
    input wire reset_n,

    // Dataslot command interface (from bram_word_model)
    input  wire        target_dataslot_write,
    input  wire        target_dataslot_read,
    input  wire [15:0] target_dataslot_id,
    input  wire [31:0] target_dataslot_bridgeaddr,
    input  wire [31:0] target_dataslot_length,
    output reg         target_dataslot_ack,
    output reg         target_dataslot_done,

    // CRAM1 read interface (direct memory read from psram model)
    output reg         cram_rd,
    output reg  [21:0] cram_addr,
    input  wire [31:0] cram_rdata,
    input  wire        cram_rdata_valid,

    // Save capture status (readable from C++ harness)
    output reg         save_complete,
    output reg  [31:0] save_captured_words,
    output reg  [15:0] save_slot_id,

    // Backdoor read of captured save data
    input  wire [15:0] save_bd_addr,     // Word address into capture buffer
    output wire [31:0] save_bd_rdata
);

wire reset = ~reset_n;

// Save capture buffer: 64K words max (256KB, one full save slot)
reg [31:0] save_buf [0:65535];
assign save_bd_rdata = save_buf[save_bd_addr];

// FSM
localparam S_IDLE    = 3'd0;
localparam S_ACK     = 3'd1;
localparam S_READ    = 3'd2;
localparam S_WAIT_RD = 3'd3;
localparam S_DONE    = 3'd4;

reg [2:0] state;
reg [31:0] cram_offset;
reg [31:0] remaining;
reg [15:0] buf_idx;
reg [7:0]  ack_count;

// Detect rising edge of target_dataslot_write
reg ds_write_prev;
wire ds_write_rising = target_dataslot_write && !ds_write_prev;

always @(posedge clk or posedge reset) begin
    if (reset) begin
        state <= S_IDLE;
        target_dataslot_ack <= 0;
        target_dataslot_done <= 0;
        cram_rd <= 0;
        cram_addr <= 0;
        cram_offset <= 0;
        remaining <= 0;
        buf_idx <= 0;
        ack_count <= 0;
        ds_write_prev <= 0;
        save_complete <= 0;
        save_captured_words <= 0;
        save_slot_id <= 0;
    end else begin
        ds_write_prev <= target_dataslot_write;
        cram_rd <= 0;

        case (state)

        S_IDLE: begin
            target_dataslot_ack <= 0;
            target_dataslot_done <= 0;
            if (ds_write_rising) begin
                cram_offset <= target_dataslot_bridgeaddr - 32'h30000000;
                remaining <= target_dataslot_length;
                buf_idx <= 0;
                save_slot_id <= target_dataslot_id;
                save_complete <= 0;
                save_captured_words <= 0;
                ack_count <= 3;
                state <= S_ACK;
            end
        end

        S_ACK: begin
            target_dataslot_ack <= 1;
            if (ack_count > 0)
                ack_count <= ack_count - 1;
            else begin
                if (remaining == 0)
                    state <= S_DONE;
                else
                    state <= S_READ;
            end
        end

        S_READ: begin
            cram_rd <= 1;
            cram_addr <= cram_offset[23:2];
            state <= S_WAIT_RD;
        end

        S_WAIT_RD: begin
            if (cram_rdata_valid) begin
                save_buf[buf_idx] <= cram_rdata;
                buf_idx <= buf_idx + 1;
                cram_offset <= cram_offset + 4;
                remaining <= remaining - 4;
                if (remaining <= 4) begin
                    save_captured_words <= {16'b0, buf_idx} + 1;
                    state <= S_DONE;
                end else begin
                    state <= S_READ;
                end
            end
        end

        S_DONE: begin
            target_dataslot_done <= 1;
            target_dataslot_ack <= 0;
            save_complete <= 1;
            state <= S_IDLE;
        end

        default: state <= S_IDLE;
        endcase
    end
end

endmodule
