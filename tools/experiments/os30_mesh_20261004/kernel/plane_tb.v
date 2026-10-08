`default_nettype none
module plane_tb(
    input wire clk,reset_n,cancel,start,
    input wire host_we,host_bank,
    input wire [7:0] host_addr,
    input wire [31:0] host_data,
    output wire busy,done,
    output wire [31:0] out_du,out_dv,out_origin
);
reg [31:0] scratch[0:255],bank[0:255];
wire [7:0] ra,rb,wa,ba;
wire we;
wire [31:0] wd;
reg [31:0] qa,qb,bq;
wire signed[31:0] ma,mb;
reg signed[63:0] product;
always @(posedge clk) begin
    qa<=scratch[ra];qb<=scratch[rb];bq<=bank[ba];product<=ma*mb;
    if(host_we)begin
        if(host_bank)bank[host_addr]<=host_data;
        else scratch[host_addr]<=host_data;
    end else if(we)scratch[wa]<=wd;
end
assign out_du=scratch[114];assign out_dv=scratch[115];assign out_origin=scratch[116];
gpu_mesh_plane dut(
    .clk(clk),.reset_n(reset_n),.cancel(cancel),.start(start),.batch(1'b0),
    .ptr0(8'd0),.ptr1(8'd1),.ptr2_reg(8'd112),.busy(busy),.done(done),
    .scratch_ra(ra),.scratch_rb(rb),.scratch_qa(qa),.scratch_qb(qb),
    .scratch_we(we),.scratch_wa(wa),.scratch_wd(wd),
    .bank_addr(ba),.bank_rdata(bq),.mul_a(ma),.mul_b(mb),.product(product),
    .out_valid(),.out_data(),.out_ready(1'b1)
);
endmodule
`default_nettype wire
