// Arbiter TB (T2): two clients (CDC-pulse + adapter-hold) issue 8 reads each
// concurrently to one behavioral controller. Every op must complete with the
// correct data (0xC0DE0000|addr), both clients fully served (no starvation),
// one op outstanding at a time.
`timescale 1ns/1ps
`default_nettype none

module tb_cram0_arb;
    reg clk=0, reset_n=0; always #5 clk=~clk;

    // client 0 (CDC-like, pulse)
    reg        c0_rd=0; wire c0_busy; wire [31:0] c0_rdata; wire c0_rdata_valid;
    reg [21:0] c0_addr=0;
    // client 1 (adapter-like, held)
    reg        c1_rd=0; wire c1_accept, c1_busy, c1_q_valid; wire [31:0] c1_q;
    reg [21:0] c1_addr=0;
    // controller
    wire        word_rd, word_wr; wire [21:0] word_addr; wire [31:0] word_wdata; wire [3:0] word_wstrb;
    reg  [31:0] ctrl_q=0; reg ctrl_busy=0, ctrl_q_valid=0;

    cram0_arb dut(.clk(clk),.reset_n(reset_n),
        .c0_rd(c0_rd),.c0_wr(1'b0),.c0_addr(c0_addr),.c0_wdata(32'd0),.c0_wstrb(4'd0),
        .c0_busy(c0_busy),.c0_rdata(c0_rdata),.c0_rdata_valid(c0_rdata_valid),
        .c1_rd(c1_rd),.c1_addr(c1_addr),.c1_accept(c1_accept),
        .c1_busy(c1_busy),.c1_q(c1_q),.c1_q_valid(c1_q_valid),
        .word_rd(word_rd),.word_wr(word_wr),.word_addr(word_addr),.word_wdata(word_wdata),.word_wstrb(word_wstrb),
        .ctrl_q(ctrl_q),.ctrl_busy(ctrl_busy),.ctrl_q_valid(ctrl_q_valid));

    // behavioral controller: 4-cycle async word, q_valid near end, pattern by addr
    reg [3:0] cnt=0; reg [21:0] cur=0;
    always @(posedge clk or negedge reset_n) begin
        if(!reset_n) begin ctrl_busy<=0; ctrl_q_valid<=0; cnt<=0; end
        else begin
            ctrl_q_valid<=0;
            if ((word_rd||word_wr) && !ctrl_busy) begin ctrl_busy<=1; cnt<=4; cur<=word_addr; end
            else if (ctrl_busy) begin
                cnt<=cnt-1;
                if (cnt==2) begin ctrl_q_valid<=1; ctrl_q<=32'hC0DE0000|{10'b0,cur}; end
                if (cnt==1) ctrl_busy<=0;
            end
        end
    end

    localparam [21:0] B0=22'h001000, B1=22'h002000;
    localparam N=8;
    integer rcv0=0, rcv1=0, err=0;
    reg [1:0] s0=0, s1=0; reg sb0=0, sb1=0; integer i0=0, i1=0;

    // client 0
    always @(posedge clk or negedge reset_n) begin
        if(!reset_n) begin s0<=0; c0_rd<=0; i0<=0; end
        else begin
            c0_rd<=0;
            case(s0)
            0: if (i0<N && !c0_busy) begin c0_rd<=1; c0_addr<=B0+i0[21:0]; sb0<=0; s0<=1; end
            1: begin
                if (c0_busy) sb0<=1;
                if (c0_rdata_valid) begin
                    if (c0_rdata !== (32'hC0DE0000|{10'b0,(B0+i0[21:0])})) begin err<=err+1; $display("c0 bad @%0d %08x",i0,c0_rdata); end
                    rcv0<=rcv0+1;
                end
                if (sb0 && !c0_busy) begin i0<=i0+1; s0<=0; end
            end
            endcase
        end
    end
    // client 1 (held request)
    always @(posedge clk or negedge reset_n) begin
        if(!reset_n) begin s1<=0; c1_rd<=0; i1<=0; end
        else begin
            case(s1)
            0: if (i1<N) begin c1_rd<=1; c1_addr<=B1+i1[21:0]; sb1<=0; s1<=1; end
            1: if (c1_accept) begin c1_rd<=0; s1<=2; end
            2: begin
                if (c1_busy) sb1<=1;
                if (c1_q_valid) begin
                    if (c1_q !== (32'hC0DE0000|{10'b0,(B1+i1[21:0])})) begin err<=err+1; $display("c1 bad @%0d %08x",i1,c1_q); end
                    rcv1<=rcv1+1;
                end
                if (sb1 && !c1_busy) begin i1<=i1+1; s1<=0; end
            end
            endcase
        end
    end

    integer t;
    initial begin
        #20 reset_n=1;
        for (t=0; t<2000 && (rcv0<N || rcv1<N); t=t+1) @(posedge clk);
        if (rcv0==N && rcv1==N && err==0)
            $display("RESULT: PASS  (c0=%0d c1=%0d, no errors, both served)", rcv0, rcv1);
        else
            $display("RESULT: FAIL  (c0=%0d/%0d c1=%0d/%0d err=%0d)", rcv0,N, rcv1,N, err);
        $finish;
    end
endmodule

`default_nettype wire
