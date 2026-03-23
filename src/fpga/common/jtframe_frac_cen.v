//------------------------------------------------------------------------------
// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2020, Jose Tejada Gomez
//------------------------------------------------------------------------------
//
// Fractional Clock Enable Generator
//
// Copyright (c) 2020, Jose Tejada Gomez <@topapate>
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, version 3.
//
// This program is distributed in the hope that it will be useful, but
// WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
// General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with this program. If not, see <http://www.gnu.org/licenses/>.
//
//------------------------------------------------------------------------------
// Generates clock enable signals at fractional frequencies based on the
// division ratio defined by 'n' (numerator) and 'm' (denominator).
// 'W' parameter sets the width of the output array, where each bit in 'cen'
// represents a clock enable signal at a successively halved frequency.
// Set 'n' and 'm' to control the output frequencies.
//------------------------------------------------------------------------------

module jtframe_frac_cen
    #(
         parameter W = 2             //! Width of the output signals (number of bits)
     ) (
         input  wire         clk,    //! Input clock signal
         input  wire   [9:0] n,      //! Numerator for division ratio
         input  wire   [9:0] m,      //! Denominator for division ratio
         output  reg [W-1:0] cen,    //! Clock enable signal (in-phase)
         output  reg [W-1:0] cenb    //! Clock enable signal (180-degree shifted)
     );

    wire [10:0] step   = {1'b0, n};  //! Step size for counter based on numerator
    wire [10:0] lim    = {1'b0, m};  //! Limit for counter based on denominator
    wire [10:0] absmax = lim + step; //! Maximum count before resetting

    reg  [10:0] cencnt = 11'd0;      //! Main counter for generating clock enables
    reg  [10:0] next, next2;         //! Intermediate values for counter logic

    // Combinational logic for updating counter values
    always @(*) begin : counterUpdate
        next  = cencnt + step; // Calculate next value of the counter by adding the step size
        next2 = next - lim;    // Determine overflow amount when counter exceeds the limit
    end

    // Flags for generating shifted clock enables
    reg  half    = 1'b0;                        //! Halfway flag for 180-degree shift
    wire over    = next >= lim;                 //! Flag for counter overflow
    wire halfway = next >= (lim >> 1) && !half; //! Flag for halfway point

    // Edge counter for generating cen signal
    reg  [W-1:0] edgecnt = {W{1'b0}};              //! Current edge count for cen toggling
    wire [W-1:0] next_edgecnt = edgecnt + 1'b1;    //! Next edge count, incremented by 1
    wire [W-1:0] toggle = next_edgecnt & ~edgecnt; //! Toggle logic for cen (detects edge)

    always @(posedge clk) begin: cenGenerator
        cen  <= {W{1'b0}};
        cenb <= {W{1'b0}};

        if(cencnt >= absmax) begin
            cencnt <= 11'd0;     // Something went wrong: reset counter
        end
        else begin
            if(halfway) begin
                half    <= 1'b1;
                cenb[0] <= 1'b1; // Set cenb for 180-degree shift
            end
        end
        if(over) begin
            cencnt  <= next2;
            half    <= 1'b0;
            edgecnt <= next_edgecnt;
            cen     <= { toggle[W-2:0], 1'b1 };  // Update cen
        end
        else begin
            cencnt <= next; // Continue counting
        end
    end

endmodule