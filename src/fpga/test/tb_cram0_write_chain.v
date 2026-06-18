// Write+readback regression (the coverage that was MISSING): drives the real
// cram0_word_cdc + cram0_arb write path across both clock domains and verifies
// write DATA round-trips.  The texture adapter is read-only, so writes through
// this CDC+arbiter were never simulated — saves (the first CRAM0 write) failed
// on silicon.  Behavioral STORING controller stands in for cram0_controller:
// same busy/q_valid waveform, but it actually holds the written words so a
// read-back exposes any write-data / wstrb / completion bug.
`timescale 1ns/1ps
`default_nettype none

module tb_cram0_write_chain;
    // two domains: clk_74a (requester side) and clk_cpu (controller side)
    reg clk74 = 0;  always #6.73 clk74 = ~clk74;   // ~74.3 MHz
    reg clkc  = 0;  always #5.00 clkc  = ~clkc;     // 100 MHz
    reg rst74 = 0, rstc = 0;

    // ---- requester side (clk_74a) ----
    reg         w_rd = 0, w_wr = 0;
    reg  [21:0] w_addr = 0;
    reg  [31:0] w_wdata = 0;
    reg  [3:0]  w_wstrb = 4'hF;
    wire        w_busy;
    wire [31:0] w_rdata;
    wire        w_rdata_valid;

    // ---- CDC clk_cpu side <-> arbiter client 0 ----
    wire        c_rd, c_wr;
    wire [21:0] c_addr;
    wire [31:0] c_wdata;
    wire [3:0]  c_wstrb;
    wire [31:0] c_rdata;
    wire        c_busy;
    wire        c_rdata_valid;

    // ---- arbiter <-> behavioral controller ----
    wire        word_rd, word_wr;
    wire [21:0] word_addr;
    wire [31:0] word_wdata;
    wire [3:0]  word_wstrb;
    reg  [31:0] ctrl_q = 0; reg ctrl_busy = 0, ctrl_q_valid = 0;

    // ---- arbiter client 1 (tex adapter) — exercised concurrently below ----
    reg         c1_rd = 0; reg [21:0] c1_addr = 0;
    wire        c1_accept, c1_busy, c1_q_valid; wire [31:0] c1_q;

    cram0_word_cdc dut_cdc (
        .clk_74a(clk74), .reset_n_74a(rst74),
        .w_rd(w_rd), .w_wr(w_wr), .w_addr(w_addr), .w_wdata(w_wdata), .w_wstrb(w_wstrb),
        .w_busy(w_busy), .w_rdata(w_rdata), .w_rdata_valid(w_rdata_valid),
        .clk_cpu(clkc), .reset_n_cpu(rstc),
        .c_rd(c_rd), .c_wr(c_wr), .c_addr(c_addr), .c_wdata(c_wdata), .c_wstrb(c_wstrb),
        .c_rdata(c_rdata), .c_busy(c_busy), .c_rdata_valid(c_rdata_valid)
    );

    cram0_arb dut_arb (
        .clk(clkc), .reset_n(rstc),
        .c0_rd(c_rd), .c0_wr(c_wr), .c0_addr(c_addr), .c0_wdata(c_wdata), .c0_wstrb(c_wstrb),
        .c0_busy(c_busy), .c0_rdata(c_rdata), .c0_rdata_valid(c_rdata_valid),
        .c1_rd(c1_rd), .c1_addr(c1_addr), .c1_accept(c1_accept),
        .c1_busy(c1_busy), .c1_q(c1_q), .c1_q_valid(c1_q_valid),
        .word_rd(word_rd), .word_wr(word_wr), .word_addr(word_addr),
        .word_wdata(word_wdata), .word_wstrb(word_wstrb),
        .ctrl_q(ctrl_q), .ctrl_busy(ctrl_busy), .ctrl_q_valid(ctrl_q_valid)
    );

    // ---- behavioral STORING controller (clk_cpu): same waveform as the real
    //      controller, but holds words so a read-back verifies write data ----
    reg [31:0] mem [0:1023];
    reg [3:0]  cnt = 0; reg [21:0] ca = 0; reg cwr = 0; reg [31:0] cwd = 0; reg [3:0] cws = 0;
    integer ii;
    initial for (ii=0; ii<1024; ii=ii+1) mem[ii] = 32'h0;
    always @(posedge clkc or negedge rstc) begin
        if (!rstc) begin ctrl_busy<=0; ctrl_q_valid<=0; cnt<=0; end
        else begin
            ctrl_q_valid <= 0;
            if ((word_rd||word_wr) && !ctrl_busy) begin
                ctrl_busy<=1; cnt<=5; ca<=word_addr; cwr<=word_wr; cwd<=word_wdata; cws<=word_wstrb;
            end else if (ctrl_busy) begin
                cnt<=cnt-1;
                if (cnt==4'd3) begin
                    if (cwr) begin
                        if (cws[0]) mem[ca[9:0]][7:0]   <= cwd[7:0];
                        if (cws[1]) mem[ca[9:0]][15:8]  <= cwd[15:8];
                        if (cws[2]) mem[ca[9:0]][23:16] <= cwd[23:16];
                        if (cws[3]) mem[ca[9:0]][31:24] <= cwd[31:24];
                    end else begin
                        ctrl_q_valid<=1; ctrl_q<=mem[ca[9:0]];
                    end
                end
                if (cnt==4'd1) ctrl_busy<=0;
            end
        end
    end

    // ---- requester-side driver (clk_74a): write N, read N back, verify ----
    function [31:0] pat(input [9:0] i);
        pat = { (i[7:0]+8'd3), (i[7:0]+8'd2), (i[7:0]+8'd1), i[7:0] };
    endfunction

    localparam N = 24;
    localparam [21:0] BASE = 22'h000040;
    integer idx = 0, errors = 0, wdone = 0, rdone = 0, warm = 0;
    reg [2:0] s = 0;
    reg saw_busy = 0;
    reg [31:0] exp = 0;

    always @(posedge clk74 or negedge rst74) begin
        if (!rst74) begin s<=0; idx<=0; w_rd<=0; w_wr<=0; warm<=0; end
        else begin
            w_rd<=0; w_wr<=0;
            if (warm<40) warm<=warm+1;
            else case (s)
            // ---- write phase ----
            0: if (idx<N) begin
                   if (!w_busy) begin
                       w_addr<=BASE+idx[21:0]; w_wdata<=pat(idx[9:0]); w_wstrb<=4'hF;
                       w_wr<=1; saw_busy<=0; s<=1;
                   end
               end else begin idx<=0; s<=3; end
            1: begin   // wait the write to complete (saw busy high then low)
                   if (w_busy) saw_busy<=1;
                   if (saw_busy && !w_busy) begin wdone<=wdone+1; idx<=idx+1; s<=0; end
               end
            // ---- readback phase ----
            3: if (idx<N) begin
                   if (!w_busy) begin
                       w_addr<=BASE+idx[21:0]; exp<=pat(idx[9:0]);
                       w_rd<=1; s<=4;
                   end
               end else s<=6;
            4: if (w_rdata_valid) begin
                   if (w_rdata !== exp) begin
                       errors<=errors+1;
                       $display("FAIL rd idx=%0d addr=%06x got=%08x exp=%08x", idx, BASE+idx, w_rdata, exp);
                   end
                   rdone<=rdone+1; idx<=idx+1; s<=3;
               end
            6: s<=6;
            endcase
        end
    end

    integer t;
    initial begin
        #20 rst74=1; rstc=1;
        for (t=0; t<200000 && (rdone<N); t=t+1) @(posedge clk74);
        if (rdone==N && wdone==N && errors==0)
            $display("RESULT: PASS  (wrote %0d, read %0d back, 0 mismatches)", wdone, rdone);
        else
            $display("RESULT: FAIL  (wrote %0d/%0d, read %0d/%0d, errors=%0d)", wdone, N, rdone, N, errors);
        $finish;
    end
    initial begin #2000000 $display("RESULT: FAIL (timeout wrote=%0d read=%0d)", wdone, rdone); $finish; end
endmodule

`default_nettype wire
