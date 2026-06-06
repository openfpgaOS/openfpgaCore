//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

// 3-stage synchronizer — extracted from the Pocket APF framework's
// common.v (io_sdram.v instantiates it) so the MiSTer target does not
// pull in the APF tree.

module synch_3 #(parameter WIDTH = 1) (
   input  wire [WIDTH-1:0] i,     // input signal
   output reg  [WIDTH-1:0] o,     // synchronized output
   input  wire             clk,   // clock to synchronize on
   output wire             rise,  // one-cycle rising edge pulse
   output wire             fall   // one-cycle falling edge pulse
);

reg [WIDTH-1:0] stage_1;
reg [WIDTH-1:0] stage_2;
reg [WIDTH-1:0] stage_3;

assign rise = (WIDTH == 1) ? (o & ~stage_3) : 1'b0;
assign fall = (WIDTH == 1) ? (~o & stage_3) : 1'b0;
always @(posedge clk)
   {stage_3, o, stage_2, stage_1} <= {o, stage_2, stage_1, i};

endmodule
