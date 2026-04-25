//
// Behavioral stub for altsyncram, supporting both SINGLE_PORT and
// BIDIR_DUAL_PORT (dual-clock) modes — enough for facade and
// axi_periph_slave testbenches under Verilator.
//
// Cyclone V M10K timing model:
//   The address is ALWAYS clocked (synchronous read), regardless of
//   outdata_reg_*.  outdata_reg_a/b only controls the optional
//   *output* pipeline register stage.
//     UNREGISTERED → 1 cycle latency  (addr-reg → mem-read → q)
//     REGISTERED   → 2 cycle latency  (addr-reg → mem-read → out-reg → q)
//   Earlier versions of this stub returned `mem[address]` combinationally
//   for UNREGISTERED mode, modelling 0-cycle reads — which doesn't match
//   real hardware and causes consumers (like axi_periph_slave's burst
//   pre-fetch) to read off-by-one in simulation while working on HW.
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
    output wire                     eccstatus,
    output wire [width_b-1:0]       q_b,
    input  wire                     rden_a,
    input  wire                     rden_b
);

reg [width_a-1:0] mem [0:numwords_a-1] /*verilator public_flat_rw*/;

// Stage-1 (address-register-clocked) and stage-2 (output-register-clocked)
// shadow values.  Real M10K always has stage-1; stage-2 is controlled by
// outdata_reg_*.
reg [width_a-1:0] q_a_reg;      // post-stage-1 (1 cycle after addr)
reg [width_b-1:0] q_b_reg;
reg [width_a-1:0] q_a_reg2;     // post-stage-2 (only used when REGISTERED)
reg [width_b-1:0] q_b_reg2;

integer i;
initial begin
    for (i = 0; i < numwords_a; i = i + 1)
        mem[i] = 32'h00000013;  // NOP pattern (works as a default for
                                // periph; facade overwrites before
                                // reading, so init value is don't-care).
    q_a_reg  = {width_a{1'b0}};
    q_b_reg  = {width_b{1'b0}};
    q_a_reg2 = {width_a{1'b0}};
    q_b_reg2 = {width_b{1'b0}};
end

// Port A — clock0.  Writes always synchronous; q_a output may be
// either combinational (UNREGISTERED) or registered.
//
// Byteena handling:
//   width_byteena_a == 1 → single enable for the whole word.
//   width_byteena_a == 4 → per-byte enables.
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

// Port B — clock1.  In DUAL_PORT mode port-A is write-only (no q_a) and
// port-B is read-only (q_b).  In BIDIR_DUAL_PORT both ports do R/W.
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

// Output mux: 1-cycle (UNREGISTERED) vs 2-cycle (REGISTERED) latency.
// Real M10K always has the address register; outdata_reg_* only adds
// the optional pipeline register on top of that.
assign q_a = (outdata_reg_a == "UNREGISTERED") ? q_a_reg : q_a_reg2;
assign q_b = (outdata_reg_b == "UNREGISTERED") ? q_b_reg : q_b_reg2;

assign eccstatus = 1'b0;

endmodule
