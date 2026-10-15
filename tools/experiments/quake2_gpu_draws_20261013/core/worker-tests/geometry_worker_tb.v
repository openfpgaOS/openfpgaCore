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
gpu_geometry_worker #(.ADDR_BITS(6),.QUEUE_BITS(4),.INCLUDE_NATIVE_MESH(1),.INCLUDE_PARTICLES(1)) worker (
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
endmodule
`default_nettype wire
