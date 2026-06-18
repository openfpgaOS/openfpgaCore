// Integration TB (T3): full CRAM0 texture read chain, byte-exact.
//
//   gpu_tex_cache (port A)  ->  gpu_cram0_tex_adapter  ->  cram0_arb  ->  behavioral CRAM0
//
// Proves a texture request to a CRAM0 byte address returns the correct texel
// after the cache misses, the adapter turns the 4-beat AXI fill into 4 async
// CRAM0 word reads, and the arbiter serializes them against a CONCURRENT client
// 0 (the CPU/bridge path).  The behavioral CRAM0 stores, at every word, the low
// byte of each of its 4 byte addresses, so the expected texel for byte addr A
// is simply A[7:0] (non-wide) or the matching halfword (wide) — computed here
// from the same pattern function the cache extracts from.
`timescale 1ns/1ps
`default_nettype none

module tb_cram0_tex_chain;
    reg clk=0, reset_n=0; always #5 clk=~clk;   // 100 MHz

    // ---- tex-cache port A driver ----
    reg         req_valid=0; wire req_ready;
    reg  [25:0] req_addr=0;  reg req_wide=0;
    wire        resp_valid; wire [15:0] resp_data;

    // ---- tex-cache <-> adapter AXI fill ----
    wire        tex_arvalid, tex_arready;
    wire [31:0] tex_araddr;  wire [7:0] tex_arlen;
    wire        tex_rvalid;  wire [31:0] tex_rdata; wire tex_rlast;

    // ---- adapter <-> arbiter client 1 ----
    wire        c1_rd; wire [21:0] c1_addr; wire c1_accept;
    wire        c1_busy; wire [31:0] c1_q; wire c1_q_valid;

    // ---- arbiter client 0 (concurrent CPU/bridge pulse) ----
    reg         c0_rd=0; reg [21:0] c0_addr=0;
    wire        c0_busy; wire [31:0] c0_rdata; wire c0_rdata_valid;

    // ---- arbiter <-> behavioral CRAM0 controller ----
    wire        word_rd, word_wr; wire [21:0] word_addr;
    wire [31:0] word_wdata; wire [3:0] word_wstrb;
    reg  [31:0] ctrl_q=0; reg ctrl_busy=0, ctrl_q_valid=0;

    // texture cache (port B tied off)
    gpu_tex_cache tex (
        .clk(clk), .reset_n(reset_n), .flush(1'b0),
        .req_valid(req_valid), .req_ready(req_ready), .req_addr(req_addr), .req_wide(req_wide),
        .resp_valid(resp_valid), .resp_data(resp_data),
        .req_valid_b(1'b0), .req_ready_b(), .req_addr_b(26'd0), .req_wide_b(1'b0),
        .resp_valid_b(), .resp_data_b(), .resp_pop_b(1'b0), .resp_flush_b(1'b0),
        .axi_arvalid(tex_arvalid), .axi_arready(tex_arready),
        .axi_araddr(tex_araddr), .axi_arlen(tex_arlen),
        .axi_rvalid(tex_rvalid), .axi_rdata(tex_rdata), .axi_rlast(tex_rlast)
    );

    gpu_cram0_tex_adapter adapter (
        .clk(clk), .reset_n(reset_n),
        .arvalid(tex_arvalid), .arready(tex_arready), .araddr(tex_araddr), .arlen(tex_arlen),
        .rvalid(tex_rvalid), .rdata(tex_rdata), .rlast(tex_rlast),
        .word_rd(c1_rd), .word_addr(c1_addr), .word_accept(c1_accept),
        .word_q(c1_q), .word_busy(c1_busy), .word_q_valid(c1_q_valid)
    );

    cram0_arb arb (
        .clk(clk), .reset_n(reset_n),
        .c0_rd(c0_rd), .c0_wr(1'b0), .c0_addr(c0_addr), .c0_wdata(32'd0), .c0_wstrb(4'd0),
        .c0_busy(c0_busy), .c0_rdata(c0_rdata), .c0_rdata_valid(c0_rdata_valid),
        .c1_rd(c1_rd), .c1_addr(c1_addr), .c1_accept(c1_accept),
        .c1_busy(c1_busy), .c1_q(c1_q), .c1_q_valid(c1_q_valid),
        .word_rd(word_rd), .word_wr(word_wr), .word_addr(word_addr),
        .word_wdata(word_wdata), .word_wstrb(word_wstrb),
        .ctrl_q(ctrl_q), .ctrl_busy(ctrl_busy), .ctrl_q_valid(ctrl_q_valid)
    );

    // ---- behavioral CRAM0: word W holds, per byte k=0..3, ((W*4+k) & 0xFF) ----
    function [31:0] cram0_pat(input [21:0] w);
        reg [7:0] b0;
        begin
            b0 = {w[5:0], 2'b0};            // (W*4) low 8 bits
            cram0_pat = { (b0+8'd3), (b0+8'd2), (b0+8'd1), b0 };
        end
    endfunction
    reg [3:0] cnt=0; reg [21:0] cur=0;
    always @(posedge clk or negedge reset_n) begin
        if(!reset_n) begin ctrl_busy<=0; ctrl_q_valid<=0; cnt<=0; end
        else begin
            ctrl_q_valid<=0;
            if((word_rd||word_wr)&&!ctrl_busy) begin ctrl_busy<=1; cnt<=4; cur<=word_addr; end
            else if(ctrl_busy) begin
                cnt<=cnt-1;
                if(cnt==4'd2) begin ctrl_q_valid<=1; ctrl_q<=cram0_pat(cur); end
                if(cnt==4'd1) ctrl_busy<=0;
            end
        end
    end

    // expected texel for byte addr A, given wide flag
    function [15:0] expect_texel(input [25:0] a, input wide);
        reg [31:0] word;
        begin
            word = cram0_pat(a[23:2]);   // CRAM0 word containing byte A
            if (wide)
                expect_texel = a[1] ? word[31:16] : word[15:0];
            else case (a[1:0])
                2'd0: expect_texel = {8'b0, word[7:0]};
                2'd1: expect_texel = {8'b0, word[15:8]};
                2'd2: expect_texel = {8'b0, word[23:16]};
                default: expect_texel = {8'b0, word[31:24]};
            endcase
        end
    endfunction

    // ---- port A test vectors (CRAM0 tex region byte addresses) ----
    localparam NT = 14;
    reg [25:0] vaddr [0:NT-1];
    reg        vwide [0:NT-1];
    integer k;
    initial begin
        // fresh-line misses across the region
        vaddr[0]=26'h600000; vwide[0]=0;
        vaddr[1]=26'h600010; vwide[1]=0;
        vaddr[2]=26'h600020; vwide[2]=0;
        vaddr[3]=26'h7A0134; vwide[3]=0;   // arbitrary deep line, byte 4 of line
        vaddr[4]=26'h8000F0; vwide[4]=0;
        // hits within already-filled lines (same line as [0])
        vaddr[5]=26'h600004; vwide[5]=0;
        vaddr[6]=26'h600008; vwide[6]=0;
        vaddr[7]=26'h60000C; vwide[7]=0;
        vaddr[8]=26'h600001; vwide[8]=0;   // byte lane 1
        vaddr[9]=26'h60000E; vwide[9]=0;   // byte lane 2
        // wide (halfword) reads
        vaddr[10]=26'h600000; vwide[10]=1;
        vaddr[11]=26'h600002; vwide[11]=1; // A[1]=1 -> high halfword
        vaddr[12]=26'h7A0136; vwide[12]=1;
        vaddr[13]=26'h8000F2; vwide[13]=1;
    end

    // ---- port A driver FSM ----
    integer ai=0, errors=0, served=0, warm=0;
    reg [1:0] s=0;
    reg [25:0] cur_a; reg cur_w; reg [15:0] exp;
    always @(posedge clk or negedge reset_n) begin
        if(!reset_n) begin s<=0; ai<=0; req_valid<=0; warm<=0; end
        else begin
            if (warm < 1500) warm <= warm + 1;   // let S_INIT clear the cache
            else case(s)
            0: if (ai<NT) begin
                   cur_a <= vaddr[ai]; cur_w <= vwide[ai];
                   exp   <= expect_texel(vaddr[ai], vwide[ai]);
                   req_addr <= vaddr[ai]; req_wide <= vwide[ai]; req_valid <= 1;
                   s <= 1;
               end
            1: if (req_ready) begin   // req_valid is high -> accepted this cycle
                   req_valid <= 0;
                   s <= 2;
               end
            2: if (resp_valid) begin
                   if (resp_data !== exp) begin
                       errors <= errors + 1;
                       $display("FAIL A[%0d] addr=%06x wide=%0d got=%04x exp=%04x",
                                ai, cur_a, cur_w, resp_data, exp);
                   end
                   served <= served + 1;
                   ai <= ai + 1;
                   s  <= 0;
               end
            endcase
        end
    end

    // ---- concurrent client 0: periodic pulse reads, self-checked ----
    integer c0_served=0, c0_errors=0, gap=0;
    reg [1:0] cs=0; reg [21:0] c0_cur=0; reg c0sb=0; integer c0i=0;
    always @(posedge clk or negedge reset_n) begin
        if(!reset_n) begin cs<=0; c0_rd<=0; c0i<=0; gap<=0; end
        else begin
            c0_rd<=0;
            case(cs)
            0: begin
                if (gap<7) gap<=gap+1;
                else if(!c0_busy) begin
                    c0_addr <= 22'h001000 + c0i[21:0];
                    c0_cur  <= 22'h001000 + c0i[21:0];
                    c0_rd<=1; c0sb<=0; gap<=0; cs<=1;
                end
            end
            1: begin
                if (c0_busy) c0sb<=1;
                if (c0_rdata_valid) begin
                    if (c0_rdata !== cram0_pat(c0_cur)) begin
                        c0_errors<=c0_errors+1;
                        $display("FAIL C0 @%06x got=%08x exp=%08x", c0_cur, c0_rdata, cram0_pat(c0_cur));
                    end
                    c0_served<=c0_served+1;
                end
                if (c0sb && !c0_busy) begin c0i<=c0i+1; cs<=0; end
            end
            endcase
        end
    end

    integer t;
    initial begin
        #20 reset_n=1;
        for (t=0; t<60000 && served<NT; t=t+1) @(posedge clk);
        if (served==NT && errors==0 && c0_errors==0 && c0_served>0)
            $display("RESULT: PASS  (texels=%0d/%0d, client0=%0d ok, 0 errors)", served, NT, c0_served);
        else
            $display("RESULT: FAIL  (texels=%0d/%0d errA=%0d  client0=%0d errC0=%0d)",
                     served, NT, errors, c0_served, c0_errors);
        $finish;
    end
    initial begin #700000 $display("RESULT: FAIL (timeout, served=%0d)", served); $finish; end
endmodule

`default_nettype wire
