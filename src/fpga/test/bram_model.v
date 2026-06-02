//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

//
// bram_model.v — behavioural BRAM for tb_system.v
//
// 32 KB × 32-bit = 8192 words.  Implements the same interface as the
// altsyncram instance inside axi_periph_slave.v, so this module can be
// substituted for the `ram` instance via a Verilator pin-compatible
// drop-in named `altsyncram`.  It is imported via `bram_model.v` rather
// than `altsyncram_stub.v` to pick up the firmware.hex init file.
//
// Verilator's `$readmemh` does not parse Altera .mif directly, so the
// Makefile strips the header into `firmware.hex`: one hex word per line,
// starting at index 0.  That format is what `$readmemh` expects.
//

`default_nettype none

module altsyncram #(
    parameter operation_mode = "SINGLE_PORT",
    parameter width_a = 32,
    parameter widthad_a = 13,
    parameter numwords_a = 8192,
    parameter width_byteena_a = 4,
    parameter width_b = 32,
    parameter widthad_b = 13,
    parameter numwords_b = 8192,
    parameter width_byteena_b = 4,
    parameter lpm_type = "altsyncram",
    parameter outdata_reg_a = "UNREGISTERED",
    parameter outdata_reg_b = "UNREGISTERED",
    parameter init_file = "",
    parameter intended_device_family = "Cyclone V",
    parameter read_during_write_mode_port_a = "NEW_DATA_NO_NBE_READ",
    parameter read_during_write_mode_port_b = "NEW_DATA_NO_NBE_READ",
    parameter read_during_write_mode_mixed_ports = "OLD_DATA",
    parameter clock_enable_input_a = "BYPASS",
    parameter clock_enable_input_b = "BYPASS",
    parameter clock_enable_output_a = "BYPASS",
    parameter clock_enable_output_b = "BYPASS",
    parameter power_up_uninitialized = "FALSE",
    parameter address_reg_b = "CLOCK1",
    parameter rdcontrol_reg_b = "CLOCK1",
    parameter address_aclr_a = "NONE",
    parameter address_aclr_b = "NONE",
    parameter outdata_aclr_a = "NONE",
    parameter outdata_aclr_b = "NONE",
    parameter byteena_aclr_a = "NONE",
    parameter byteena_aclr_b = "NONE",
    parameter byte_size = 8,
    parameter wrcontrol_aclr_a = "NONE",
    parameter wrcontrol_aclr_b = "NONE",
    parameter wrcontrol_wraddress_reg_b = "CLOCK1",
    parameter indata_aclr_a = "NONE",
    parameter indata_aclr_b = "NONE",
    parameter indata_reg_b = "CLOCK1",
    parameter ram_block_type = "AUTO",
    parameter init_file_layout = "PORT_A",
    parameter maximum_depth = 0,
    parameter enable_ecc = "FALSE",
    parameter clock_enable_core_a = "USE_INPUT_CLKEN",
    parameter clock_enable_core_b = "USE_INPUT_CLKEN"
)(
    input  wire                     clock0,
    input  wire [widthad_a-1:0]     address_a,
    input  wire [width_a-1:0]       data_a,
    input  wire                     wren_a,
    input  wire [width_byteena_a-1:0] byteena_a,
    output wire [width_a-1:0]       q_a,
    input  wire                     aclr0,
    input  wire                     aclr1,
    input  wire                     addressstall_a,
    input  wire                     addressstall_b,
    input  wire [widthad_b-1:0]     address_b,
    input  wire [width_b-1:0]       data_b,
    input  wire                     wren_b,
    input  wire [width_byteena_b-1:0] byteena_b,
    input  wire                     clock1,
    input  wire                     clocken0,
    input  wire                     clocken1,
    input  wire                     clocken2,
    input  wire                     clocken3,
    output wire [2:0]               eccstatus,
    output wire [width_b-1:0]       q_b,
    input  wire                     rden_a,
    input  wire                     rden_b
);

reg [width_a-1:0] mem [0:numwords_a-1] /*verilator public_flat_rw*/;

// Stage-1 (address-clocked) and stage-2 (output-clocked) shadows.  Real
// Cyclone V M10K always has stage-1.  outdata_reg_* selects whether the
// optional stage-2 register is enabled.  Earlier versions returned
// `mem[address]` combinationally for UNREGISTERED — that's 0-cycle
// latency and doesn't match silicon (which is 1-cycle even when the
// output reg is bypassed).
reg [width_a-1:0] q_a_reg;
reg [width_b-1:0] q_b_reg;
reg [width_a-1:0] q_a_reg2;
reg [width_b-1:0] q_b_reg2;

integer i;
initial begin
    for (i = 0; i < numwords_a; i = i + 1)
        mem[i] = 32'h00000013;  // NOP pattern
    q_a_reg  = {width_a{1'b0}};
    q_b_reg  = {width_b{1'b0}};
    q_a_reg2 = {width_a{1'b0}};
    q_b_reg2 = {width_b{1'b0}};
    // tb_system_main.cpp preloads BRAM via the public_flat_rw mem[]
    // backdoor after parsing firmware.mif, so no $readmemh is needed
    // here.  Leaving it out keeps the module usable when the harness
    // uses its own loader.
`ifdef BRAM_INIT_HEX
    $readmemh(`BRAM_INIT_HEX, mem);
`endif
end

// Port A — clock0.  Byte-enable writes, combinational or registered read.
always @(posedge clock0) begin
    if (wren_a) begin
        if (width_byteena_a == 1) begin
            if (byteena_a[0]) mem[address_a] <= data_a;
        end else begin
            if (byteena_a[0])                          mem[address_a][ 7: 0] <= data_a[ 7: 0];
            if (width_byteena_a > 1 && byteena_a[1])   mem[address_a][15: 8] <= data_a[15: 8];
            if (width_byteena_a > 2 && byteena_a[2])   mem[address_a][23:16] <= data_a[23:16];
            if (width_byteena_a > 3 && byteena_a[3])   mem[address_a][31:24] <= data_a[31:24];
        end
    end
    q_a_reg  <= mem[address_a];
    q_a_reg2 <= q_a_reg;
end

always @(posedge clock1) begin
    if ((operation_mode == "BIDIR_DUAL_PORT") && wren_b) begin
        if (width_byteena_b == 1) begin
            if (byteena_b[0]) mem[address_b] <= data_b;
        end else begin
            if (byteena_b[0])                          mem[address_b][ 7: 0] <= data_b[ 7: 0];
            if (width_byteena_b > 1 && byteena_b[1])   mem[address_b][15: 8] <= data_b[15: 8];
            if (width_byteena_b > 2 && byteena_b[2])   mem[address_b][23:16] <= data_b[23:16];
            if (width_byteena_b > 3 && byteena_b[3])   mem[address_b][31:24] <= data_b[31:24];
        end
    end
    if (operation_mode == "BIDIR_DUAL_PORT" || operation_mode == "DUAL_PORT") begin
        q_b_reg  <= mem[address_b];
        q_b_reg2 <= q_b_reg;
    end
end

// 1-cycle (UNREGISTERED) vs 2-cycle (REGISTERED) latency.  Address is
// always clocked on real M10K; outdata_reg_* only adds the optional
// pipeline register.
assign q_a = (outdata_reg_a == "UNREGISTERED") ? q_a_reg : q_a_reg2;
assign q_b = (outdata_reg_b == "UNREGISTERED") ? q_b_reg : q_b_reg2;

assign eccstatus = 3'b000;

endmodule
