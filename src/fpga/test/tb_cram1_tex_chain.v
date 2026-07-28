// Integration TB: GPU texture read from CRAM1 via SYNC BURST, byte-exact,
// against the REAL controller + PHY + behavioral chip model.
//
//   gpu_tex_cache -> gpu_cram1_tex_adapter -> cram1_controller(+cram1_phy) -> cram_chip_model
//
// Two paths proven:
//   A. Backdoor-preloaded texels -> GPU tex fill (sync-burst read) -> correct texel.
//   B. CPU upload via word_wr (async write, chip in BCR 0x641F sync-burst) ->
//      GPU tex fill of those words -> correct texel.  Proves async-write and
//      sync-burst-read coexist on the same chip (the whole point of CRAM1).
//
// Expected texel for byte addr A (non-wide) = A[7:0], because chip word W holds,
// per byte k, ((W*4+k) & 0xFF) — same trivially-checkable pattern as the CRAM0
// chain TB.
`timescale 1ns/1ps
`default_nettype none

module tb_cram1_tex_chain;
    reg clk = 0; always #5 clk = ~clk;   // 100 MHz
    reg reset_n = 0;

    // ---- gpu_tex_cache port A ----
    reg         req_valid = 0; wire req_ready;
    reg  [25:0] req_addr = 0;  reg req_wide = 0;
    wire        resp_valid; wire [15:0] resp_data;

    // ---- tex cache <-> adapter AXI fill ----
    wire        tex_arvalid, tex_arready; wire [31:0] tex_araddr; wire [7:0] tex_arlen;
    wire        tex_rvalid; wire [31:0] tex_rdata; wire tex_rlast;

    // ---- adapter <-> controller burst ----
    wire        burst_rd; wire [21:0] burst_addr; wire [4:0] burst_len;
    wire [31:0] burst_q; wire burst_q_valid, burst_busy;

    // ---- controller <-> chip pins ----
    wire [21:16] cram_a;
    wire [15:0]  cram_ctrl_dq_out; wire cram_ctrl_dq_oe; wire [15:0] cram_chip_dq_out;
    wire [15:0]  cram_dq_to_ctrl = cram_ctrl_dq_oe ? cram_ctrl_dq_out : cram_chip_dq_out;
    wire cram_wait, cram_clk, cram_adv_n, cram_cre, cram_ce0_n, cram_ce1_n, cram_oe_n, cram_we_n, cram_ub_n, cram_lb_n;

    // ---- CPU word path (texture upload + readback) ----
    reg         word_rd = 0, word_wr = 0;
    reg  [21:0] word_addr = 0; reg [31:0] word_data = 0; reg [3:0] word_wstrb = 4'hF;
    wire [31:0] word_q; wire word_busy, word_q_valid;

    // ---- BCR config ----
    reg         config_en = 0, config_bank_sel = 0;
    wire        raw_busy, bcr_init_done;

    // ---- chip backdoor preload ----
    reg         bd_we = 0; reg [20:0] bd_word_addr = 0; reg [31:0] bd_wdata_word = 0;
    wire [15:0] cram_errors;

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

    gpu_cram1_tex_adapter adapter (
        .clk(clk), .reset_n(reset_n),
        .arvalid(tex_arvalid), .arready(tex_arready), .araddr(tex_araddr), .arlen(tex_arlen),
        .rvalid(tex_rvalid), .rdata(tex_rdata), .rlast(tex_rlast),
        .burst_rd(burst_rd), .burst_addr(burst_addr), .burst_len(burst_len),
        .burst_q(burst_q), .burst_q_valid(burst_q_valid), .burst_busy(burst_busy)
    );

    cram1_controller #(.CLOCK_SPEED(100.0)) ctrl (
        .clk(clk), .reset_n(reset_n),
        .word_rd(word_rd), .word_wr(word_wr), .word_addr(word_addr),
        .word_data(word_data), .word_wstrb(word_wstrb),
        .word_q(word_q), .word_busy(word_busy), .word_q_valid(word_q_valid),
        .burst_rd(burst_rd), .burst_addr(burst_addr), .burst_len(burst_len),
        .burst_q(burst_q), .burst_q_valid(burst_q_valid), .burst_busy(burst_busy),
        .config_en(config_en), .config_data(16'h641F), .config_bank_sel(config_bank_sel),
        .raw_busy(raw_busy), .bcr_init_done(bcr_init_done),
        .cram_a(cram_a), .cram_dq_out(cram_ctrl_dq_out), .cram_dq_oe(cram_ctrl_dq_oe),
        .cram_dq_in(cram_dq_to_ctrl), .cram_wait(cram_wait), .cram_clk(cram_clk),
        .cram_adv_n(cram_adv_n), .cram_cre(cram_cre),
        .cram_ce0_n(cram_ce0_n), .cram_ce1_n(cram_ce1_n),
        .cram_oe_n(cram_oe_n), .cram_we_n(cram_we_n),
        .cram_ub_n(cram_ub_n), .cram_lb_n(cram_lb_n)
    );

    cram_chip_model #(.POWERUP_CYCLES(8'd4)) chip (
        .clk(clk), .cram_clk(clk), .reset_n(reset_n),
        .cram_a(cram_a),
        .cram_dq_in(cram_ctrl_dq_oe ? cram_ctrl_dq_out : 16'h0),
        .cram_dq_out(cram_chip_dq_out), .cram_dq_oe(cram_ctrl_dq_oe),
        .cram_wait_out(cram_wait),
        .cram_adv_n(cram_adv_n), .cram_cre(cram_cre),
        .cram_ce0_n(cram_ce0_n), .cram_ce1_n(cram_ce1_n),
        .cram_oe_n(cram_oe_n), .cram_we_n(cram_we_n),
        .cram_ub_n(cram_ub_n), .cram_lb_n(cram_lb_n),
        .cram_wr_strobe(1'b0),
        .bd_we(bd_we), .bd_word_addr(bd_word_addr), .bd_wdata_word(bd_wdata_word),
        .error_count(cram_errors)
    );

    // ---- BCR-init FSM: pulse config_en per die (sync burst 0x641F) ----
    reg [3:0] bcr_st = 0; integer warm = 0;
    localparam B_WAIT=0, B_P0=1, B_B0=2, B_I0=3, B_P1=4, B_B1=5, B_I1=6, B_DONE=7;
    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin bcr_st<=B_WAIT; config_en<=0; config_bank_sel<=0; warm<=0; end
        else begin
            config_en <= 1'b0;
            case (bcr_st)
            B_WAIT: if (warm < 20) warm <= warm + 1; else bcr_st <= B_P0;
            B_P0:   begin config_bank_sel<=1'b0; config_en<=1'b1; bcr_st<=B_B0; end
            B_B0:   if (raw_busy) bcr_st<=B_I0;
            B_I0:   if (!raw_busy) bcr_st<=B_P1;
            B_P1:   begin config_bank_sel<=1'b1; config_en<=1'b1; bcr_st<=B_B1; end
            B_B1:   if (raw_busy) bcr_st<=B_I1;
            B_I1:   if (!raw_busy) bcr_st<=B_DONE;
            B_DONE: ;
            endcase
        end
    end

    // ---- expected pattern: chip word W -> per byte k, (W*4+k)&0xFF ----
    function [31:0] pat(input [21:0] w);
        reg [7:0] b0;
        begin b0 = {w[5:0], 2'b0}; pat = { (b0+8'd3), (b0+8'd2), (b0+8'd1), b0 }; end
    endfunction
    function [15:0] expect_texel(input [25:0] a, input wide);
        reg [31:0] word;
        begin
            word = pat(a[23:2]);
            if (wide) expect_texel = a[1] ? word[31:16] : word[15:0];
            else case (a[1:0])
                2'd0: expect_texel = {8'b0, word[7:0]};
                2'd1: expect_texel = {8'b0, word[15:8]};
                2'd2: expect_texel = {8'b0, word[23:16]};
                default: expect_texel = {8'b0, word[31:24]};
            endcase
        end
    endfunction

    // ---- test vectors: A = backdoor region, B = word_wr-uploaded region,
    //      C = interleave (fills + uploads in flight simultaneously) ----
    localparam NT = 12;       // phase A+B vectors
    localparam NT_ALL = 15;   // + phase-C vectors
    reg [25:0] vaddr [0:NT_ALL-1]; reg vwide [0:NT_ALL-1];
    integer kk;
    initial begin
        // A region (backdoor-preloaded): word base 0x100 (byte 0x400)
        vaddr[0]=26'h000400; vwide[0]=0;   // line 0x400, lane 0
        vaddr[1]=26'h000405; vwide[1]=0;   // line 0x400, lane 1, word 1
        vaddr[2]=26'h00040E; vwide[2]=0;   // lane 2, word 3
        vaddr[3]=26'h000410; vwide[3]=0;   // next line
        vaddr[4]=26'h000402; vwide[4]=1;   // wide, high half of word 0
        vaddr[5]=26'h000400; vwide[5]=1;   // wide, low half
        // B region (uploaded via word_wr): byte 0x800
        vaddr[6]=26'h000800; vwide[6]=0;
        vaddr[7]=26'h000807; vwide[7]=0;
        vaddr[8]=26'h00080C; vwide[8]=0;
        vaddr[9]=26'h000810; vwide[9]=0;
        vaddr[10]=26'h000812; vwide[10]=1;
        vaddr[11]=26'h00081F; vwide[11]=0;
        // C region: 12 = fresh line (forces a fill the master interleaves a
        // word_wr into), 13 = the word uploaded MID-BURST in phase C (its
        // fetch then issues a burst_rd while that write drains — the reverse
        // interleave), 14 = second fresh line (post-interleave sanity).
        vaddr[12]=26'h000C00; vwide[12]=0;
        vaddr[13]=26'h000900; vwide[13]=0;
        vaddr[14]=26'h000C10; vwide[14]=0;
    end

    // ---- port-A GPU tex driver (gated on `go`) ----
    reg go = 0;
    reg [7:0] nt_lim = NT;   // master raises to NT_ALL for phase C
    integer ai = 0, errors = 0, served = 0;
    reg [2:0] s = 0;
    reg [25:0] cur_a; reg cur_w; reg [15:0] exp;
    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin s<=0; ai<=0; req_valid<=0; served<=0; errors<=0; end
        else if (go) case (s)
        0: if (ai < nt_lim) begin
               cur_a <= vaddr[ai]; cur_w <= vwide[ai]; exp <= expect_texel(vaddr[ai], vwide[ai]);
               req_addr <= vaddr[ai]; req_wide <= vwide[ai]; req_valid <= 1; s <= 1;
           end
        1: if (req_ready) begin req_valid <= 0; s <= 2; end
        2: if (resp_valid) begin
               if (resp_data !== exp) begin
                   errors <= errors + 1;
                   $display("FAIL idx=%0d addr=%06x wide=%0d got=%04x exp=%04x", ai, cur_a, cur_w, resp_data, exp);
               end
               served <= served + 1; ai <= ai + 1; s <= 0;
           end
        endcase
    end

    // ---- master sequence ----
    integer i; integer t;
    task cpu_write(input [21:0] w, input [31:0] d);
        begin
            @(posedge clk); word_addr <= w; word_data <= d; word_wstrb <= 4'hF; word_wr <= 1'b1;
            @(posedge clk); word_wr <= 1'b0;
            // saw-busy: wait busy high then low
            @(posedge clk); while (!word_busy) @(posedge clk);
            while (word_busy) @(posedge clk);
        end
    endtask

    initial begin
        #20 reset_n = 1;
        // wait BCR init
        @(posedge clk); while (!bcr_init_done) @(posedge clk);
        repeat (8) @(posedge clk);

        // Test A: backdoor-preload words for byte region 0x400..0x41F (word 0x100..0x107)
        for (i = 0; i < 8; i = i + 1) begin
            @(posedge clk); bd_we <= 1'b1; bd_word_addr <= 21'h100 + i[20:0]; bd_wdata_word <= pat(22'h100 + i[21:0]);
        end
        @(posedge clk); bd_we <= 1'b0;

        // Test B: upload words for byte region 0x800..0x81F (word 0x200..0x207) via word_wr
        for (i = 0; i < 8; i = i + 1)
            cpu_write(22'h200 + i[21:0], pat(22'h200 + i[21:0]));

        repeat (4) @(posedge clk);
        go = 1;

        for (t = 0; t < 100000 && served < NT; t = t + 1) @(posedge clk);

        // ---- Test C: interleaved traffic (the historically-missing stimulus:
        // the 2026-06 campaign validated upload and fetch in SEPARATE phases,
        // so the controller's ST_IDLE-only pulse sampling dropped commands
        // that arrive mid-op on real HW).  Two directions:
        //   C1: word_wr pulsed while a tex fill burst is mid-flight
        //       (historically: upload word silently LOST -> stale texel).
        //   C2: the next fetch's burst_rd lands while that write drains
        //       (historically: fill request LOST -> tex cache deadlock).
        // ----
        // Preload the two fresh C lines (bytes 0xC00..0xC1F).
        for (i = 0; i < 8; i = i + 1) begin
            @(posedge clk); bd_we <= 1'b1; bd_word_addr <= 21'h300 + i[20:0]; bd_wdata_word <= pat(22'h300 + i[21:0]);
        end
        @(posedge clk); bd_we <= 1'b0;
        repeat (2) @(posedge clk);
        nt_lim = NT_ALL;   // driver issues vector 12 -> miss -> fill burst
        @(posedge clk); while (!burst_busy) @(posedge clk);
        // C1: pulse the upload NOW, mid-burst.
        word_addr <= 22'h240; word_data <= pat(22'h240); word_wstrb <= 4'hF; word_wr <= 1'b1;
        @(posedge clk); word_wr <= 1'b0;
        // Saw-busy completion must still work: busy must be high NOW (raised
        // at the pulse by the pending capture) and fall only when the pended
        // write has actually completed after the burst.
        @(posedge clk);
        if (!word_busy) begin
            $display("RESULT: FAIL (word_busy low right after mid-burst word_wr pulse — pend capture broken)");
            $finish;
        end
        while (word_busy) @(posedge clk);
        // Driver fetches vector 13 (the just-uploaded word) and 14; C2 is
        // exercised on whichever fetch lands while a word op drains.
        for (t = 0; t < 100000 && served < NT_ALL; t = t + 1) @(posedge clk);

        if (served == NT_ALL && errors == 0 && cram_errors == 16'd0)
            $display("RESULT: PASS  (%0d texels byte-exact via CRAM1 sync-burst; backdoor+word_wr upload; mid-burst interleave lossless; chip_err=0)", served);
        else
            $display("RESULT: FAIL  (served=%0d/%0d errors=%0d chip_err=%0d)", served, NT_ALL, errors, cram_errors);
        $finish;
    end
    initial begin #2000000 $display("RESULT: FAIL (timeout served=%0d bcr_done=%0b)", served, bcr_init_done); $finish; end
endmodule

`default_nettype wire
