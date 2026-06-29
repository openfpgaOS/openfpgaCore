//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

//
// link_lite.v — IRQ-driven link peripheral (simplified)
//
// Minimal hardware: shift register + clock divider + 1-word TX/RX.
// CPU ISR handles buffering, CRC, and polling via timer.
//
// Register map (word offsets, at 0x4D000000):
//   0x00 CTRL/STATUS  RW  W:[0]=enable [1]=reset [2]=master
//                         R:[0]=enable [2]=master [4]=tx_empty [5]=rx_ready
//   0x04 TX_DATA      WO  Write word to transmit (starts slot if master)
//   0x08 RX_DATA      RO  Read received word (clears rx_ready)
//   0x0C DIVISOR      WO  Half-period clock divider (default from param)
//
// Protocol: 33-bit full-duplex slots, {valid, data[31:0]} MSB-first.
// Master drives SCK. Slave samples on SCK edges.
// IRQ asserted when rx_ready (received word available).
//

`default_nettype none

module link_lite #(
    parameter integer CLK_HZ = 100000000,
    parameter integer SCK_HZ = 256000
) (
    input  wire        clk,
    input  wire        reset_n,

    // MMIO
    input  wire        reg_wr,
    input  wire        reg_rd,
    input  wire [4:0]  reg_addr,
    input  wire [31:0] reg_wdata,
    output reg  [31:0] reg_rdata,

    // Physical link signals
    input  wire        link_si_i,
    output reg         link_so_o,
    output wire        link_so_oe,
    input  wire        link_sck_i,
    output reg         link_sck_o,
    output wire        link_sck_oe,
    input  wire        link_sd_i,
    output wire        link_sd_o,
    output wire        link_sd_oe,

    output wire        irq
);

// Clock divider defaults
localparam integer HALF_DIV_DEFAULT = CLK_HZ / (SCK_HZ * 2);
localparam integer DIV_W = 16;

// State
reg        ctrl_enable;
reg        ctrl_master;

reg [DIV_W-1:0] half_div;       // configurable half-period
reg [DIV_W-1:0] clk_div_ctr;

// 1-word TX/RX registers (no FIFO)
reg [31:0] tx_data;
reg        tx_pending;           // CPU wrote a word, not yet shifted out
reg [31:0] rx_data;
reg        rx_ready;             // shift-in complete, CPU hasn't read yet

// Shift state
reg        slot_active;
reg        slot_phase_high;
reg [5:0]  slot_bit_idx;         // 32 downto 0
reg [32:0] tx_shift;             // {valid, data[31:0]}
reg [32:0] rx_shift;

// SCK synchroniser (slave mode)
reg [2:0] sck_sync;
wire sck_rise = (sck_sync[2:1] == 2'b01);
wire sck_fall = (sck_sync[2:1] == 2'b10);

// SI synchroniser: link_si_i is an asynchronous external pin (driven from
// port_tran_si in core_top). Sample it through a 2-flop synchroniser before
// any logic consumes it; link_si_sync is the de-metastabilised value. The
// SI bit is sampled relative to the SCK edge, so the matched ~2-cycle delay
// through this chain mirrors the SCK synchroniser and preserves bit timing.
reg [1:0] si_sync;
wire link_si_sync = si_sync[1];

// Outputs
assign link_so_oe  = ctrl_enable;
assign link_sck_oe = ctrl_enable & ctrl_master;
assign link_sd_o   = 1'b0;
assign link_sd_oe  = 1'b0;
assign irq         = ctrl_enable & rx_ready;

// Register read
//
// reg_addr is the full 5-bit word offset (byte addr[6:2], 32 words). Decode
// against all 5 bits so only the four mapped word offsets (0..3) respond;
// using a truncated field (e.g. reg_addr[1:0]) would alias word offsets
// 4,8,12,... onto the real registers.
always @(*) begin
    case (reg_addr)
        5'd0: reg_rdata = {26'b0, rx_ready, ~tx_pending, ctrl_master, 1'b0, ctrl_enable};
        5'd1: reg_rdata = 32'd0;
        5'd2: reg_rdata = rx_data;
        5'd3: reg_rdata = {16'b0, half_div};
        default: reg_rdata = 32'd0;
    endcase
end

// Slot completion: latch RX, clear TX
task slot_done;
begin
    slot_active <= 1'b0;
    link_so_o <= 1'b0;
    if (rx_shift[32])  // peer sent valid data
        begin rx_data <= rx_shift[31:0]; rx_ready <= 1'b1; end
end
endtask

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        ctrl_enable <= 0; ctrl_master <= 0;
        half_div <= HALF_DIV_DEFAULT[DIV_W-1:0];
        clk_div_ctr <= 0;
        tx_data <= 0; tx_pending <= 0;
        rx_data <= 0; rx_ready <= 0;
        slot_active <= 0; slot_phase_high <= 0;
        slot_bit_idx <= 0;
        tx_shift <= 0; rx_shift <= 0;
        sck_sync <= 0;
        si_sync <= 0;
        link_so_o <= 0; link_sck_o <= 0;
    end else begin
        sck_sync <= {sck_sync[1:0], link_sck_i};
        si_sync  <= {si_sync[0], link_si_i};

        // ---- CPU writes ----
        if (reg_wr) begin
            case (reg_addr)
                5'd0: begin  // CTRL
                    if (reg_wdata[1]) begin  // reset
                        ctrl_enable <= 0; ctrl_master <= 0;
                        tx_pending <= 0; rx_ready <= 0;
                        slot_active <= 0;
                        link_so_o <= 0; link_sck_o <= 0;
                    end else begin
                        ctrl_enable <= reg_wdata[0];
                        ctrl_master <= reg_wdata[2];
                    end
                end
                5'd1: begin  // TX_DATA
                    tx_data <= reg_wdata;
                    tx_pending <= 1'b1;
                end
                5'd3: begin  // DIVISOR
                    half_div <= reg_wdata[DIV_W-1:0];
                end
            endcase
        end

        // ---- CPU reads (side effect: clear rx_ready) ----
        if (reg_rd && reg_addr == 5'd2)
            rx_ready <= 1'b0;

        // ---- Master mode ----
        if (ctrl_enable && ctrl_master) begin
            if (!slot_active) begin
                // Start a slot when CPU has data to send (or idle poll)
                if (tx_pending) begin
                    tx_shift <= {1'b1, tx_data};
                    tx_pending <= 1'b0;
                    link_so_o <= 1'b1;  // valid bit
                end else begin
                    tx_shift <= 33'd0;
                    link_so_o <= 1'b0;
                end
                slot_active <= 1'b1;
                slot_phase_high <= 1'b0;
                slot_bit_idx <= 6'd32;
                rx_shift <= 33'd0;
                clk_div_ctr <= 0;
                link_sck_o <= 1'b0;
            end else begin
                if (clk_div_ctr == half_div) begin
                    clk_div_ctr <= 0;
                    if (!slot_phase_high) begin
                        // Rising: sample SI
                        slot_phase_high <= 1'b1;
                        link_sck_o <= 1'b1;
                        rx_shift[slot_bit_idx] <= link_si_sync;
                    end else begin
                        // Falling: advance
                        slot_phase_high <= 1'b0;
                        link_sck_o <= 1'b0;
                        if (slot_bit_idx == 0) begin
                            slot_done;
                        end else begin
                            slot_bit_idx <= slot_bit_idx - 1'b1;
                            link_so_o <= tx_shift[slot_bit_idx - 1'b1];
                        end
                    end
                end else begin
                    clk_div_ctr <= clk_div_ctr + 1'b1;
                end
            end
        end

        // ---- Slave mode ----
        if (ctrl_enable && !ctrl_master) begin
            link_sck_o <= 1'b0;

            // Preload SO for next slot
            if (!slot_active) begin
                if (tx_pending) begin
                    tx_shift <= {1'b1, tx_data};
                    link_so_o <= 1'b1;
                end else begin
                    tx_shift <= 33'd0;
                    link_so_o <= 1'b0;
                end
            end

            if (sck_rise) begin
                if (!slot_active) begin
                    slot_active <= 1'b1;
                    slot_bit_idx <= 6'd32;
                    rx_shift <= 33'd0;
                    rx_shift[32] <= link_si_sync;
                    if (tx_pending) tx_pending <= 1'b0;
                end else begin
                    rx_shift[slot_bit_idx] <= link_si_sync;
                end
            end

            if (sck_fall && slot_active) begin
                if (slot_bit_idx == 0) begin
                    slot_done;
                end else begin
                    slot_bit_idx <= slot_bit_idx - 1'b1;
                    link_so_o <= tx_shift[slot_bit_idx - 1'b1];
                end
            end
        end

        // Disabled: hold outputs low
        if (!ctrl_enable) begin
            link_sck_o <= 1'b0;
            link_so_o <= 1'b0;
        end
    end
end

wire _unused = &{1'b0, link_sd_i};

endmodule
