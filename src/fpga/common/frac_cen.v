//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------
//
// Fractional clock-enable generator.
//
// cen[0] strobes, on average, n out of every m input-clock cycles, evenly
// distributed by a phase accumulator (acc += n each cycle; when acc reaches m,
// subtract m and strobe) -- the textbook DDA/Bresenham fractional divider.
// cen[k] for k>0 is cen[0] divided by 2^k (a strobe once every 2^k cen[0]
// strobes).  cenb[0] strobes near the half-period between cen[0] strobes
// (~180 degrees); cenb[k>0] are unused and held 0.  Requires W >= 2.
//
// Independent implementation of the standard algorithm; not derived from any
// other source.
//------------------------------------------------------------------------------

module frac_cen #(
    parameter W = 2
) (
    input  wire         clk,
    input  wire  [9:0]  n,    // numerator
    input  wire  [9:0]  m,    // denominator
    output reg  [W-1:0] cen,  // in-phase enables: cen[0]=clk*n/m, cen[k]=cen[0]/2^k
    output reg  [W-1:0] cenb  // ~180-degree-shifted enable on bit 0
);

    reg  [10:0]  acc    = 11'd0;        // phase accumulator, 0..m-1
    reg          half   = 1'b0;         // half-period reached within this period
    reg  [W-1:0] divcnt = {W{1'b0}};    // counts cen[0] strobes; carries drive cen[1..]

    wire [11:0] acc_n    = {1'b0, acc} + {2'b0, n};   // acc + n
    wire        strobe   = acc_n >= {2'b0, m};        // cen[0] fires this cycle
    wire [10:0] acc_wrap = acc_n[10:0] - m;           // accumulator after a strobe
    wire        half_hit = !half && !strobe && (acc_n >= {3'b0, m[9:1]}); // crossed m/2

    wire [W-1:0] divnext = divcnt + 1'b1;
    wire [W-1:0] divedge = divnext & ~divcnt;         // bit that carried up

    always @(posedge clk) begin
        cen  <= {W{1'b0}};
        cenb <= {W{1'b0}};
        if (strobe) begin
            acc    <= acc_wrap;
            half   <= 1'b0;
            divcnt <= divnext;
            cen    <= { divedge[W-2:0], 1'b1 };
        end else begin
            acc <= acc_n[10:0];
            if (half_hit) begin
                half    <= 1'b1;
                cenb[0] <= 1'b1;
            end
        end
    end

endmodule
