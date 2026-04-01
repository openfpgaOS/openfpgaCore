//
// Behavioral Bridge Responder for Verilator Save Path Testing
//
// Simulates the APF bridge: when target_dataslot_write fires, reads
// data from the PSRAM model (CRAM1) and writes it into a bridge write
// FIFO for drain via bridge_master → word_sdram_arbiter → SDRAM.
//
// Simplified model — no async FIFO CDC, everything in clk domain.
//

`default_nettype none

module bridge_responder (
    input wire clk,
    input wire reset_n,

    // Dataslot command interface (from periph/bram model)
    input  wire        target_dataslot_write,  // Pulse: save command
    input  wire        target_dataslot_read,   // Pulse: load command
    input  wire [15:0] target_dataslot_id,
    input  wire [31:0] target_dataslot_bridgeaddr,
    input  wire [31:0] target_dataslot_length,
    output reg         target_dataslot_ack,
    output reg         target_dataslot_done,

    // CRAM1 read interface (direct memory read from psram model)
    output reg         cram_rd,
    output reg  [21:0] cram_addr,       // Word address within CRAM1 (22-bit = 16MB)
    input  wire [31:0] cram_rdata,
    input  wire        cram_rdata_valid,

    // Bridge write FIFO output (to bridge_master)
    output reg  [55:0] fifo_q,          // {addr[23:0], wdata[31:0]}
    output reg         fifo_empty,
    input  wire        fifo_rdreq
);

wire reset = ~reset_n;

// FSM states
localparam S_IDLE     = 3'd0;
localparam S_ACK      = 3'd1;  // Assert ACK for a few cycles
localparam S_READ     = 3'd2;  // Read CRAM1 word
localparam S_WAIT_RD  = 3'd3;  // Wait for CRAM read data
localparam S_ENQUEUE  = 3'd4;  // Push to FIFO
localparam S_DRAIN    = 3'd5;  // Wait for bridge_master to drain
localparam S_DONE     = 3'd6;  // Assert DONE

reg [2:0] state;
reg [31:0] bridge_addr;   // Current bridge address (SDRAM destination)
reg [31:0] cram_offset;   // Current CRAM1 read offset (bytes)
reg [31:0] remaining;     // Bytes remaining
reg [31:0] read_data;     // Captured CRAM read data
reg [7:0]  ack_count;     // Cycles to hold ACK

// FIFO: single-entry (bridge_master drains before we enqueue next)
reg        fifo_has_data;
reg [55:0] fifo_data;

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
        fifo_q <= 0;
        fifo_empty <= 1;
        fifo_has_data <= 0;
        fifo_data <= 0;
        ds_write_prev <= 0;
        bridge_addr <= 0;
        cram_offset <= 0;
        remaining <= 0;
        read_data <= 0;
        ack_count <= 0;
    end else begin
        ds_write_prev <= target_dataslot_write;
        cram_rd <= 0;

        // FIFO drain: when bridge_master reads, dequeue
        if (fifo_rdreq && fifo_has_data) begin
            fifo_has_data <= 0;
            fifo_empty <= 1;
        end

        case (state)

        S_IDLE: begin
            target_dataslot_ack <= 0;
            target_dataslot_done <= 0;
            if (ds_write_rising) begin
                // Capture command parameters
                bridge_addr <= target_dataslot_bridgeaddr;
                cram_offset <= target_dataslot_bridgeaddr - 32'h30000000;
                remaining <= target_dataslot_length;
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
            // Issue CRAM1 read
            cram_rd <= 1;
            cram_addr <= cram_offset[23:2];  // Word address
            state <= S_WAIT_RD;
        end

        S_WAIT_RD: begin
            if (cram_rdata_valid) begin
                read_data <= cram_rdata;
                state <= S_ENQUEUE;
            end
        end

        S_ENQUEUE: begin
            // Push to FIFO (SDRAM word address in upper 24 bits)
            if (!fifo_has_data) begin
                fifo_data <= {bridge_addr[25:2], read_data};
                fifo_q <= {bridge_addr[25:2], read_data};
                fifo_has_data <= 1;
                fifo_empty <= 0;
                // Advance
                bridge_addr <= bridge_addr + 4;
                cram_offset <= cram_offset + 4;
                remaining <= remaining - 4;
                state <= S_DRAIN;
            end
        end

        S_DRAIN: begin
            // Wait for bridge_master to drain the FIFO entry
            if (!fifo_has_data) begin
                if (remaining == 0)
                    state <= S_DONE;
                else
                    state <= S_READ;
            end
        end

        S_DONE: begin
            target_dataslot_done <= 1;
            target_dataslot_ack <= 0;
            // Hold DONE until deasserted by testbench
            state <= S_IDLE;
        end

        default: state <= S_IDLE;
        endcase
    end
end

endmodule
