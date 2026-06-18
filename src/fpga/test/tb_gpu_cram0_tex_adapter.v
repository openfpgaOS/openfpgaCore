// Unit TB (T1) for gpu_cram0_tex_adapter: one AXI fill burst (arlen=3) must
// produce exactly 4 R-beats, rlast on the 4th, rdata = the CRAM0 pattern for
// the 4 contiguous word addresses (base, base+1, base+2, base+3).
`timescale 1ns/1ps
`default_nettype none

module tb_gpu_cram0_tex_adapter;
    reg clk = 0; reg reset_n = 0;
    always #5 clk = ~clk;   // 100 MHz

    // AXI fill master stimulus
    reg         arvalid = 0;
    wire        arready;
    reg  [31:0] araddr  = 0;
    reg  [7:0]  arlen   = 0;
    wire        rvalid;
    wire [31:0] rdata;
    wire        rlast;

    // adapter <-> (behavioral) CRAM0 controller
    wire        word_rd;
    wire [21:0] word_addr;
    wire        word_accept;
    reg  [31:0] word_q = 0;
    reg         word_busy = 0;
    reg         word_q_valid = 0;

    gpu_cram0_tex_adapter dut (
        .clk(clk), .reset_n(reset_n),
        .arvalid(arvalid), .arready(arready), .araddr(araddr), .arlen(arlen),
        .rvalid(rvalid), .rdata(rdata), .rlast(rlast),
        .word_rd(word_rd), .word_addr(word_addr), .word_accept(word_accept),
        .word_q(word_q), .word_busy(word_busy), .word_q_valid(word_q_valid)
    );

    // Behavioral CRAM0 word controller: accept immediately, 4-cycle async access,
    // word_q_valid one cycle before busy falls.  Pattern = 0xC0DE0000 | addr.
    assign word_accept = word_rd & ~word_busy;
    reg [3:0]  rd_cnt = 0;
    reg [21:0] rd_addr = 0;
    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            word_busy <= 0; word_q_valid <= 0; rd_cnt <= 0;
        end else begin
            word_q_valid <= 0;
            if (word_accept) begin
                word_busy <= 1; rd_cnt <= 4; rd_addr <= word_addr;
            end else if (word_busy) begin
                rd_cnt <= rd_cnt - 1;
                if (rd_cnt == 2) begin
                    word_q_valid <= 1;
                    word_q <= 32'hC0DE0000 | {10'b0, rd_addr};
                end
                if (rd_cnt == 1) word_busy <= 0;
            end
        end
    end

    integer beats = 0;
    integer errors = 0;
    integer i;
    reg [21:0] expect_base;

    // capture R beats
    always @(posedge clk) begin
        if (reset_n && rvalid) begin
            if (rdata !== (32'hC0DE0000 | {10'b0, (expect_base + beats[21:0])})) begin
                $display("FAIL beat %0d: rdata=%08x expected=%08x",
                         beats, rdata, 32'hC0DE0000 | {10'b0, (expect_base + beats[21:0])});
                errors = errors + 1;
            end
            if (beats == 3 && !rlast) begin $display("FAIL: rlast not set on beat 3"); errors=errors+1; end
            if (beats != 3 && rlast)  begin $display("FAIL: rlast set early on beat %0d", beats); errors=errors+1; end
            beats = beats + 1;
        end
    end

    initial begin
        #20 reset_n = 1;
        #20;
        // issue a fill burst: line-aligned byte addr 0x600400 (CRAM0 tex region)
        // -> base word = 0x600400>>2 = 0x180100
        araddr = 32'h00600400; arlen = 8'd3; expect_base = 22'h180100;
        @(posedge clk); arvalid = 1;
        @(posedge clk); while (!arready) @(posedge clk);
        arvalid = 0;
        // wait for 4 beats (or timeout)
        i = 0;
        while (beats < 4 && i < 300) begin @(posedge clk); i = i + 1; end
        if (beats != 4) begin $display("FAIL: got %0d beats, expected 4", beats); errors=errors+1; end
        if (errors == 0) $display("RESULT: PASS  (4 beats, rlast ok, base 0x%05x..+3)", expect_base);
        else             $display("RESULT: FAIL  (%0d errors, %0d beats)", errors, beats);
        $finish;
    end

    initial begin #5000 $display("RESULT: FAIL (timeout)"); $finish; end
endmodule

`default_nettype wire
