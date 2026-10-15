`default_nettype none
module geometry_worker_tb (
    input wire clk, reset_n, enable, soft_reset, ring_reset,
    input wire host_write, host_publish, consume,
    input wire [31:0] host_data,
    output wire [31:0] data,
    output wire [5:0] in_read, out_write,
    output reg [5:0] host_ptr, read_ptr,
    output wire busy,
    output wire [6:0] worker_phase,
    input wire mesh_reset_n,mesh_arready,mesh_rvalid,mesh_rlast,
    input wire [31:0] mesh_rdata,
    output wire mesh_arvalid,
    output wire [31:0] mesh_araddr,
    output wire [7:0] mesh_arlen
);
reg [31:0] memory [0:63];
reg [5:0] published;
wire [5:0] address;
reg [31:0] input_data;
always @(posedge clk) begin
    input_data <= memory[address];
    if(host_write) memory[host_ptr] <= host_data;
    if(!reset_n || ring_reset) begin host_ptr<=0; published<=0; end
    else begin
        if(host_write) host_ptr<=host_ptr+1'b1;
        if(host_publish) published<=host_ptr;
    end
    if(!reset_n || ring_reset || soft_reset) read_ptr<=0;
    else if(consume) read_ptr<=read_ptr+1'b1;
end
gpu_geometry_worker #(.ADDR_BITS(6),.QUEUE_BITS(4),.INCLUDE_NATIVE_MESH(`TEST_NATIVE),.INCLUDE_PARTICLES(`TEST_PARTICLES)) worker (
    .clk(clk),.reset_n(reset_n),.enable(enable),.soft_reset(soft_reset),.ring_reset(ring_reset),
    .input_wrptr(published),.input_rdptr(in_read),.input_addr(address),.input_data(input_data),
    .output_rdptr(read_ptr),.output_addr(read_ptr+{5'd0,consume}),
    .output_data(data),.output_wrptr(out_write),.busy(busy),
    .mesh_reset_n(mesh_reset_n),.mesh_arvalid(mesh_arvalid),.mesh_arready(mesh_arready),
    .mesh_araddr(mesh_araddr),.mesh_arlen(mesh_arlen),.mesh_rvalid(mesh_rvalid),
    .mesh_rdata(mesh_rdata),.mesh_rlast(mesh_rlast)
);
assign worker_phase=worker.state;
always @(posedge clk) begin
    if (reset_n && !soft_reset && !ring_reset && worker.state == 25 && worker.index > 6)
        $fatal(1, "EMIT counter reachable-range invariant failed");
end

wire [5:0] reference_in_read, reference_address, reference_out_write;
wire [31:0] reference_data, reference_mesh_araddr;
wire [7:0] reference_mesh_arlen;
wire reference_busy, reference_mesh_arvalid;
gpu_geometry_worker_reference #(.ADDR_BITS(6),.QUEUE_BITS(4),.INCLUDE_NATIVE_MESH(`TEST_NATIVE),.INCLUDE_PARTICLES(`TEST_PARTICLES)) reference_worker (
    .clk(clk),.reset_n(reset_n),.enable(enable),.soft_reset(soft_reset),.ring_reset(ring_reset),
    .input_wrptr(published),.input_rdptr(reference_in_read),.input_addr(reference_address),.input_data(input_data),
    .output_rdptr(read_ptr),.output_addr(read_ptr+{5'd0,consume}),
    .output_data(reference_data),.output_wrptr(reference_out_write),.busy(reference_busy),
    .mesh_reset_n(mesh_reset_n),.mesh_arvalid(reference_mesh_arvalid),.mesh_arready(mesh_arready),
    .mesh_araddr(reference_mesh_araddr),.mesh_arlen(reference_mesh_arlen),.mesh_rvalid(mesh_rvalid),
    .mesh_rdata(mesh_rdata),.mesh_rlast(mesh_rlast)
);
reg [63:0] observed_cycles = 0;
reg [63:0] published_while_busy = 0;
reg [63:0] raw_divides = 0, clip_divides = 0, native_divides = 0, particle_divides = 0;
always @(negedge clk) begin
    if (reset_n && !soft_reset && !ring_reset) begin
        observed_cycles = observed_cycles + 1;
        if (host_publish && worker.state != 0) published_while_busy = published_while_busy + 1;
        if ({in_read,address,out_write,busy,mesh_arvalid,mesh_araddr,mesh_arlen,worker.state,worker.mode}
            !== {reference_in_read,reference_address,reference_out_write,reference_busy,
                 reference_mesh_arvalid,reference_mesh_araddr,reference_mesh_arlen,
                 reference_worker.state,reference_worker.mode})
            $fatal(1,"Cycle/state/architectural-port lockstep mismatch at cycle %0d",observed_cycles);
        if (read_ptr != out_write && data !== reference_data)
            $fatal(1,"Published output lockstep mismatch at cycle %0d",observed_cycles);
        if (worker.state == 9 && worker.divisor !== reference_worker.active_nearclip)
            $fatal(1,"Staged near plane differs from current owner threshold at DIV_INIT");
        if (worker.state == 10 && worker.divisor !== reference_worker.divisor)
            $fatal(1,"Clamped divisor lockstep mismatch");
        if (enable && worker.state == 9) case (worker.mode)
            2: raw_divides=raw_divides+1;
            3: clip_divides=clip_divides+1;
            5: native_divides=native_divides+1;
            7: particle_divides=particle_divides+1;
            default: $fatal(1,"Unexpected DIV_INIT mode");
        endcase
    end
end
final begin
    if (raw_divides == 0 || clip_divides == 0 || published_while_busy == 0)
        $fatal(1,"Missing legacy/busy-publication lockstep coverage");
    if (`TEST_NATIVE && native_divides == 0) $fatal(1,"Missing native lockstep coverage");
    if (`TEST_PARTICLES && particle_divides == 0) $fatal(1,"Missing particle lockstep coverage");
    $display("PASS cycle lockstep %0d cycles, busy-publication %0d; divides raw=%0d clip=%0d native=%0d particle=%0d",
             observed_cycles,published_while_busy,raw_divides,clip_divides,native_divides,particle_divides);
end

endmodule
`default_nettype wire
