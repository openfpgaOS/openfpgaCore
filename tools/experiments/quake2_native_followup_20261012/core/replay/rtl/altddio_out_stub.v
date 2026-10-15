// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
// Edge behavior used by the CRAM1 forwarded clock (no physical delay model).
module altddio_out #(
    parameter width = 1,
    parameter intended_device_family = "Cyclone V",
    parameter power_up_high = "OFF"
)(
    input wire outclock, outclocken,
    input wire [width-1:0] datain_h, datain_l,
    input wire aclr, aset, sclr, sset, oe,
    output reg [width-1:0] dataout = 0,
    output wire [width-1:0] oe_out
);
    assign oe_out = {width{oe}};
    always @(posedge outclock or negedge outclock or posedge aclr or posedge aset) begin
        if (aclr) dataout <= 0;
        else if (aset) dataout <= {width{1'b1}};
        else if (outclocken) begin
            if (sclr) dataout <= 0;
            else if (sset) dataout <= {width{1'b1}};
            else dataout <= outclock ? datain_h : datain_l;
        end
    end
endmodule
