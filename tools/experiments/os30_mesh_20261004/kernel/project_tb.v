`default_nettype none
module project_tb(
    input wire clk,reset_n,cancel,start,host_we,
    input wire [7:0] host_addr,
    input wire [31:0] host_data,
    output wire busy,done,ownership_violation,
    output wire [223:0] output_words
);
reg [31:0] scratch[0:255];
wire [7:0] ra,rb,wa;
wire we;
wire [31:0] wd;
reg [31:0] qa,qb;
wire signed [31:0] ma,mb;
reg signed [63:0] product;
wire ds;
wire [31:0] den;
reg ddone;
reg [31:0] quotient;
reg [5:0] countdown;
assign ownership_violation=we && (wa<232||wa>238);
always @(posedge clk)begin
    qa<=scratch[ra];qb<=scratch[rb];product<=ma*mb;
    if(host_we)scratch[host_addr]<=host_data;
    else if(we)scratch[wa]<=wd;
    if(!reset_n||cancel)begin countdown<=0;ddone<=0;end
    else begin
        ddone<=0;
        if(ds)begin quotient<=(64'd1<<38)/den;countdown<=41;end
        else if(countdown!=0)begin countdown<=countdown-1'b1;if(countdown==1)ddone<=1;end
    end
end
genvar i;
generate for(i=0;i<7;i=i+1)begin:outputs
    assign output_words[i*32+:32]=scratch[232+i];
end endgenerate
gpu_mesh_project dut(
    .clk(clk),.reset_n(reset_n),.cancel(cancel),.start(start),
    .material_mode(1'b0),.src_ptr(8'd64),.dst_ptr(8'd232),.busy(busy),.done(done),
    .scratch_ra(ra),.scratch_rb(rb),.scratch_qa(qa),.scratch_qb(qb),
    .scratch_we(we),.scratch_wa(wa),.scratch_wd(wd),
    .mul_a(ma),.mul_b(mb),.product(product),
    .div_start(ds),.div_den(den),.div_done(ddone),.quotient(quotient)
);
endmodule
`default_nettype wire
