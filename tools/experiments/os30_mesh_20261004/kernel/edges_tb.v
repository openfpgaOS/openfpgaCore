`default_nettype none
module edges_tb(
    input wire clk,reset_n,cancel,start,
    input wire host_we,
    input wire [7:0] host_addr,
    input wire [31:0] host_data,clipx,
    output wire busy,done,empty,
    output wire [287:0] packet
);
reg [31:0] scratch[0:255];
wire [7:0] ra,rb,wa;
wire we;
wire [31:0] wd;
reg [31:0] qa,qb;
wire signed [31:0] ma,mb;
reg signed [63:0] product;
wire ds;
wire [31:0] dn;
wire [15:0] dd;
reg ddone;
reg [31:0] quotient;
reg [5:0] countdown;
always @(posedge clk) begin
    qa<=scratch[ra];qb<=scratch[rb];product<=ma*mb;
    if(host_we)scratch[host_addr]<=host_data;
    else if(we)scratch[wa]<=wd;
    if(!reset_n||cancel)begin countdown<=0;ddone<=0;end
    else begin
        ddone<=0;
        if(ds)begin quotient<=dn/dd;countdown<=34;end
        else if(countdown!=0)begin
            countdown<=countdown-1'b1;
            if(countdown==1)ddone<=1;
        end
    end
end
assign packet[0*32+:32]=scratch[91];
assign packet[1*32+:32]=scratch[92];
assign packet[2*32+:32]=scratch[80];
assign packet[3*32+:32]=scratch[82];
assign packet[4*32+:32]=scratch[84];
assign packet[5*32+:32]=scratch[93];
assign packet[6*32+:32]={scratch[87][15:0],scratch[86][15:0]};
assign packet[7*32+:32]={13'd0,scratch[12][0],scratch[89][0],scratch[90][0],scratch[88][15:0]};
assign packet[8*32+:32]=clipx;
assign empty=$signed(scratch[86]) >= $signed(scratch[88]);
gpu_mesh_edges dut(
    .clk(clk),.reset_n(reset_n),.cancel(cancel),.start(start),.busy(busy),.done(done),
    .scratch_ra(ra),.scratch_rb(rb),.scratch_qa(qa),.scratch_qb(qb),
    .scratch_we(we),.scratch_wa(wa),.scratch_wd(wd),
    .mul_a(ma),.mul_b(mb),.product(product),
    .div_start(ds),.div_num(dn),.div_divisor(dd),.div_done(ddone),.quotient(quotient)
);
endmodule
`default_nettype wire
