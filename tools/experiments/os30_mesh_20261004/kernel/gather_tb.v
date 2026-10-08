`default_nettype none
module gather_tb(
    input wire clk,reset_n,cancel,start,host_we,host_bank,
    input wire [7:0] host_addr,
    input wire [31:0] host_data,
    input wire [5:0] tri_index,
    output wire busy,done,ownership_violation,
    output wire [671:0] polygon,
    output wire [31:0] clip_or,clip_and
);
reg [31:0] scratch[0:255],bank[0:255];
wire [7:0] ra,rb,wa,ba;
wire we;
wire [31:0] wd;
reg [31:0] qa,qb,bq;
assign ownership_violation=we&&wa!=62&&wa!=63&&(wa<64||wa>84);
always @(posedge clk)begin
    qa<=scratch[ra];qb<=scratch[rb];bq<=bank[ba];
    if(host_we)begin
        if(host_bank)bank[host_addr]<=host_data;else scratch[host_addr]<=host_data;
    end else if(we)scratch[wa]<=wd;
end
genvar i;
generate for(i=0;i<21;i=i+1)begin:g_output
    assign polygon[i*32+:32]=scratch[64+i];
end endgenerate
assign clip_or=scratch[62];assign clip_and=scratch[63];
gpu_mesh_gather dut(
    .clk(clk),.reset_n(reset_n),.cancel(cancel),.start(start),
    .tri_index(tri_index),.dst_ptr(8'd64),.mask_reg(8'd62),.busy(busy),.done(done),
    .scratch_ra(ra),.scratch_rb(rb),.scratch_qa(qa),.scratch_qb(qb),
    .scratch_we(we),.scratch_wa(wa),.scratch_wd(wd),
    .bank_addr(ba),.bank_rdata(bq)
);
endmodule
`default_nettype wire
