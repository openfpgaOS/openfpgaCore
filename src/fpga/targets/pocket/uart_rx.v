//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

// Simple UART RX (8N1, no parity)
// Directly adapted from nandland uart_tx.v pattern
module uart_rx
  #(parameter CLKS_PER_BIT)
  (
   input        i_Clock,
   input        i_Rx_Serial,
   output       o_Rx_DV,      // Data Valid pulse (1 cycle)
   output [7:0] o_Rx_Byte
   );

  parameter s_IDLE         = 3'b000;
  parameter s_RX_START_BIT = 3'b001;
  parameter s_RX_DATA_BITS = 3'b010;
  parameter s_RX_STOP_BIT  = 3'b011;
  parameter s_CLEANUP      = 3'b100;

  reg [2:0]    r_SM_Main     = 0;
  reg [15:0]   r_Clock_Count = 0;
  reg [2:0]    r_Bit_Index   = 0;
  reg [7:0]    r_Rx_Byte     = 0;
  reg          r_Rx_DV       = 0;

  // Double-flop synchronizer for input
  reg          r_Rx_Data_R   = 1;
  reg          r_Rx_Data     = 1;

  always @(posedge i_Clock) begin
    r_Rx_Data_R <= i_Rx_Serial;
    r_Rx_Data   <= r_Rx_Data_R;
  end

  always @(posedge i_Clock) begin
    case (r_SM_Main)
      s_IDLE: begin
        r_Rx_DV       <= 1'b0;
        r_Clock_Count <= 0;
        r_Bit_Index   <= 0;
        if (r_Rx_Data == 1'b0)          // Start bit detected
          r_SM_Main <= s_RX_START_BIT;
        else
          r_SM_Main <= s_IDLE;
      end

      // Sample middle of start bit
      s_RX_START_BIT: begin
        if (r_Clock_Count == (CLKS_PER_BIT-1)/2) begin
          if (r_Rx_Data == 1'b0) begin
            r_Clock_Count <= 0;  // Found middle, reset counter
            r_SM_Main     <= s_RX_DATA_BITS;
          end else
            r_SM_Main <= s_IDLE;  // False start
        end else begin
          r_Clock_Count <= r_Clock_Count + 1;
          r_SM_Main     <= s_RX_START_BIT;
        end
      end

      // Sample data bits
      s_RX_DATA_BITS: begin
        if (r_Clock_Count < CLKS_PER_BIT-1) begin
          r_Clock_Count <= r_Clock_Count + 1;
          r_SM_Main     <= s_RX_DATA_BITS;
        end else begin
          r_Clock_Count          <= 0;
          r_Rx_Byte[r_Bit_Index] <= r_Rx_Data;
          if (r_Bit_Index < 7) begin
            r_Bit_Index <= r_Bit_Index + 1;
            r_SM_Main   <= s_RX_DATA_BITS;
          end else begin
            r_Bit_Index <= 0;
            r_SM_Main   <= s_RX_STOP_BIT;
          end
        end
      end

      // Wait for stop bit
      s_RX_STOP_BIT: begin
        if (r_Clock_Count < CLKS_PER_BIT-1) begin
          r_Clock_Count <= r_Clock_Count + 1;
          r_SM_Main     <= s_RX_STOP_BIT;
        end else begin
          r_Rx_DV       <= 1'b1;
          r_Clock_Count <= 0;
          r_SM_Main     <= s_CLEANUP;
        end
      end

      s_CLEANUP: begin
        r_SM_Main <= s_IDLE;
        r_Rx_DV   <= 1'b0;
      end

      default:
        r_SM_Main <= s_IDLE;
    endcase
  end

  assign o_Rx_DV   = r_Rx_DV;
  assign o_Rx_Byte = r_Rx_Byte;

endmodule
