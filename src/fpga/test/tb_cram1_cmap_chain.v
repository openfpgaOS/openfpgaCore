// Integration TB: GPU COLORMAP read (port B) from CRAM1 via SYNC BURST,
// byte-exact, against the REAL controller + PHY + behavioral chip model.
//
//   gpu_tex_cache PORT B -> gpu_cram1_tex_adapter -> cram1_controller(+cram1_phy)
//                                                  -> cram_chip_model
//
// This fills the coverage hole left by tb_cram1_tex_chain (which tied port B
// to 0 and only exercised port A) and by the SDRAM-stub acceptance suite
// (which exercises port B but through a stub R-beat pattern, NOT the real
// cram1 sync-burst + S_DRAIN burst_q_valid pattern).
//
// HW symptom being reproduced: with GPU texture/colormap fetches routed to
// CRAM1, TEXELS render correctly but the per-pixel COLORMAP (palookup) byte
// returns the WRONG palette index.  Rendered-but-wrong-byte, NOT a hang.
//
// We drive BOTH ports concurrently:
//   * Port B (colormap): faithful gpu_core handshake — present req_valid_b
//     unconditionally from a pending slot, accept on req_ready_b, wait
//     resp_valid_b, capture resp_data_b[7:0], pulse resp_pop_b for ONE cycle
//     on the capture.  Two outstanding allowed (held skid + pipe slot), so
//     we model a 2-stage consumer (a staging slot + a consuming slot).
//   * Port A (texture): continuously issue texture reads whose cache SETS
//     ALIAS the colormap region (16 KB-stride collision) to force eviction
//     thrash between colormap and texture lines — the real Quake regime.
//
// Expected byte for byte addr A (non-wide) = A[7:0], because chip word W
// holds, per byte k, ((W*4+k) & 0xFF).  Same trivially-checkable scheme as
// tb_cram1_tex_chain's pat().
//
//`timescale 1ns/1ps
`default_nettype none

module tb_cram1_cmap_chain;
    reg clk = 0; always #5 clk = ~clk;   // 100 MHz
    reg reset_n = 0;

    // ---- gpu_tex_cache port A (texture) ----
    reg         req_valid = 0; wire req_ready;
    reg  [25:0] req_addr = 0;  reg req_wide = 0;
    wire        resp_valid; wire [15:0] resp_data;

    // ---- gpu_tex_cache port B (colormap) ----
    reg         req_valid_b = 0; wire req_ready_b;
    reg  [25:0] req_addr_b = 0;  reg req_wide_b = 0;
    wire        resp_valid_b; wire [15:0] resp_data_b;
    reg         resp_pop_b = 0; reg resp_flush_b = 0;

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

    // ---- CPU word path (unused here; tied off) ----
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
        .req_valid_b(req_valid_b), .req_ready_b(req_ready_b),
        .req_addr_b(req_addr_b), .req_wide_b(req_wide_b),
        .resp_valid_b(resp_valid_b), .resp_data_b(resp_data_b),
        .resp_pop_b(resp_pop_b), .resp_flush_b(resp_flush_b),
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
    // Expected byte at byte-address A (non-wide) = A[7:0].
    function [7:0] expect_byte(input [25:0] a);
        reg [31:0] word;
        begin
            word = pat(a[23:2]);
            case (a[1:0])
                2'd0: expect_byte = word[7:0];
                2'd1: expect_byte = word[15:8];
                2'd2: expect_byte = word[23:16];
                default: expect_byte = word[31:24];
            endcase
        end
    endfunction

    // =====================================================================
    // PORT B (colormap) consumer model — faithful to gpu_core.v
    // =====================================================================
    // Two stages, exactly like the real consumer:
    //   * staging slot (cmap_pending): presents req_valid_b unconditionally,
    //     cleared on accept (req_ready_b).  Holds (addr, expected).
    //   * consuming slot (p2b): a fragment waiting for its response at the
    //     queue head.  Stalls until resp_valid_b, then pulses resp_pop_b for
    //     ONE cycle and retires.
    // The cache holds up to 2 outstanding (held skid + pipe slot), so we
    // allow the staging slot to issue a 2nd request while the 1st sits in
    // the consuming slot — but only when the cache accepts it.
    //
    // We sweep light=0..LMAX over a couple of texels:
    //   addr = {light[7:0], texel[7:0]} = light*256 + texel, all in [0,0x4000)
    // Consecutive PIXELS keep the SAME light and walk texel through a 16-byte
    // line (texel = base..base+15) so most are HITS within a line; we miss on
    // line/light boundaries.  Do NOT force a fresh miss per pixel.
    // =====================================================================
    localparam integer LMAX   = 63;   // light 0..63
    localparam integer NTEX   = 16;   // texel walk length per light (one 16-B line region)
    localparam integer NB     = (LMAX+1)*NTEX;   // total port-B pixels

    // Per-issued-pixel shadow FIFO (issue order) for ordering verification.
    reg [25:0] sh_addr [0:NB-1];
    reg [7:0]  sh_exp  [0:NB-1];

    integer issue_i = 0;   // next pixel to STAGE (present to cache)
    integer cons_i  = 0;   // next pixel to CONSUME (capture response)

    // Build the colormap pixel sequence: for each light, texel base anchored
    // so consecutive texels share a 16-byte line, miss at boundaries.
    integer li, ti, idx;
    initial begin
        idx = 0;
        for (li = 0; li <= LMAX; li = li + 1) begin
            for (ti = 0; ti < NTEX; ti = ti + 1) begin
                // base texel anchored at li*4 (mod something) so the line
                // boundary moves and we get a fresh-line miss now and then,
                // not every pixel.
                sh_addr[idx] = li[7:0]*26'd256 + ti[7:0];   // in [0,0x4000)
                sh_exp[idx]  = expect_byte(sh_addr[idx]);
                idx = idx + 1;
            end
        end
    end

    // Staging slot (== gpu_core cmap_pending_*).  Carries its own pixel
    // index so the (addr, exp, idx) that get accepted are exactly the ones
    // we presented — no aliasing with issue_i.
    reg        pendB_valid = 0;
    reg [25:0] pendB_addr  = 0;
    reg [7:0]  pendB_exp   = 0;
    integer    pendB_idx   = 0;

    // In-order outstanding queue (== the cache's 2-deep response queue: the
    // held skid + pipe slot).  Accepted requests whose response hasn't been
    // consumed yet wait here in issue order.  Depth 2 mirrors the cache.
    localparam integer QD = 2;
    reg        q_valid [0:QD-1];
    reg [25:0] q_addr  [0:QD-1];
    reg [7:0]  q_exp   [0:QD-1];
    integer    q_idx   [0:QD-1];
    integer    q_cnt = 0;          // number of valid entries (head at index 0)

    // Bookkeeping / classification
    integer served_b = 0;
    integer errors_b = 0;
    reg [7:0] gotB [0:NB-1];        // captured bytes (for anti-uniform check)
    reg       got_valid [0:NB-1];

    reg go = 0;
    integer qk;

    // Port-B FSM.  Faithful to gpu_core:
    //   * req_valid_b = pendB_valid (registered staging slot), presented
    //     UNCONDITIONALLY (driven combinationally below).
    //   * on accept (req_valid_b && req_ready_b): the STAGED pixel enters the
    //     tail of the in-order outstanding queue and the staging slot frees
    //     (== cmap_pending_valid<=0 on cmap_req_ready_b; fragment p1->p2->p2b).
    //   * the HEAD of the queue is the fragment in p2b waiting for its byte;
    //     when resp_valid_b is high it captures resp_data_b[7:0], pulses
    //     resp_pop_b for ONE cycle (retiring the cache's oldest response),
    //     and the queue shifts.
    //   * up to QD=2 outstanding mirrors the cache's held-skid + pipe slot,
    //     so requests can be issued back-to-back (1px/cycle) on hits.
    //
    // Composed via next-state temporaries so a same-cycle accept + consume
    // (the steady-state pipelined case) is atomic and order-preserving.
    integer    n_cnt;
    reg        pop_now, acc_now;

    // pop_now / resp_pop_b are COMBINATIONAL and asserted on the SAME cycle
    // the response is captured — faithful to gpu_core, where cmap_resp_pop_b
    // is a wire that pulses on the p2b->p3 shift that also captures the byte
    // into p3_color.  (Registering resp_pop_b a cycle late lets the cache
    // push an already-consumed response into its held skid, which corrupts
    // the NEXT read — a TB artifact, not the RTL bug.)
    always @(*) begin
        pop_now    = go && (q_cnt > 0) && resp_valid_b;
        resp_pop_b = pop_now;
    end

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            pendB_valid <= 0; pendB_addr <= 0; pendB_exp <= 0; pendB_idx <= 0;
            q_cnt <= 0;
            for (qk = 0; qk < QD; qk = qk + 1) q_valid[qk] <= 1'b0;
            issue_i <= 0; served_b <= 0; errors_b <= 0;
        end else if (go) begin
            acc_now = req_valid_b && req_ready_b;
            n_cnt   = q_cnt;

            // -------- CONSUME (head retires) --------
            if (pop_now) begin
                gotB[q_idx[0]]      <= resp_data_b[7:0];
                got_valid[q_idx[0]] <= 1'b1;
                if (resp_data_b[7:0] !== q_exp[0]) begin
                    errors_b <= errors_b + 1;
                    $display("[B] FAIL idx=%0d addr=%06x light=%0d texel=%0d got=%02x exp=%02x  (cyc=%0t)",
                             q_idx[0], q_addr[0], q_addr[0][13:8], q_addr[0][7:0],
                             resp_data_b[7:0], q_exp[0], $time);
                    $display("    EVID held_valid_b=%0b held_data_b=%02x pipe_b_live=%0b resp_valid_b=%0b resp_data_b=%04x",
                             tex.held_valid_b, tex.held_data_b[7:0], tex.pipe_b_live,
                             resp_valid_b, resp_data_b);
                    $display("    EVID pipe_addr_b=%06x pipe_byte_b_r=%0d pipe_hit_b=%0b fill_resp_valid_b=%0b lat_port=%0b state=%0d",
                             tex.pipe_addr_b, tex.pipe_byte_b_r, tex.pipe_hit_b,
                             tex.fill_resp_valid_b, tex.lat_port, tex.state);
                    $display("    EVID adapter.state=%0d burst_busy=%0b burst_q_valid=%0b burst_q=%08x rlast=%0b ctrl.state=%0d",
                             adapter.state, burst_busy, burst_q_valid, burst_q,
                             tex_rlast, ctrl.state);
                end
                served_b   <= served_b + 1;
                // shift queue down by one
                for (qk = 0; qk < QD-1; qk = qk + 1) begin
                    q_valid[qk] <= q_valid[qk+1];
                    q_addr[qk]  <= q_addr[qk+1];
                    q_exp[qk]   <= q_exp[qk+1];
                    q_idx[qk]   <= q_idx[qk+1];
                end
                q_valid[QD-1] <= 1'b0;
                n_cnt = n_cnt - 1;
            end

            // -------- ACCEPT (staged pixel enters queue tail) --------
            // The cache's req_ready_b already self-gates on the held-skid
            // being free, so it never accepts a 3rd outstanding — n_cnt
            // cannot exceed QD here.  Guard anyway to catch a TB drift.
            if (acc_now) begin
                if (n_cnt >= QD) begin
                    $display("TB-BUG: accept overflowed outstanding queue (n_cnt=%0d) at cyc=%0t", n_cnt, $time);
                    $finish;
                end
                pendB_valid <= 1'b0;
                q_valid[n_cnt] <= 1'b1;
                q_addr[n_cnt]  <= pendB_addr;
                q_exp[n_cnt]   <= pendB_exp;
                q_idx[n_cnt]   <= pendB_idx;
                n_cnt = n_cnt + 1;
            end

            q_cnt <= n_cnt;

            // -------- STAGE: refill the staging slot whenever it is free (or
            // freeing via accept this cycle) and pixels remain.  Flow control
            // on outstanding depth is enforced by the cache's req_ready_b
            // (it won't accept the staged request until its queue has room) —
            // exactly like gpu_core staging on every p1->p2 shift.
            if (issue_i < NB
                && (!pendB_valid || acc_now)) begin
                pendB_valid <= 1'b1;
                pendB_addr  <= sh_addr[issue_i];
                pendB_exp   <= sh_exp[issue_i];
                pendB_idx   <= issue_i;
                issue_i     <= issue_i + 1;
            end
        end
    end

    // req_valid_b / req_addr_b combinational drive from staging slot.
    // Faithful to gpu_core: cmap_req_valid_b = cmap_pending_valid (registered
    // slot), presented unconditionally.
    always @(*) begin
        req_valid_b = pendB_valid;
        req_addr_b  = pendB_addr;
        req_wide_b  = 1'b0;        // colormap reads are BYTE reads
    end

    // =====================================================================
    // PORT A (texture) driver — concurrent thrash.  Texture region byte
    // base 0x10000 (= 4 * 0x4000) so its cache SETS exactly alias the
    // colormap region's sets, forcing eviction thrash.  We walk texels
    // through lines too (mostly hits, occasional miss).  Byte reads.
    // =====================================================================
    localparam integer TEX_BASE = 26'h010000;   // aliases colormap sets
    localparam integer NTA = 512;                // port-A reads to run
    integer a_i = 0;
    integer served_a = 0, errors_a = 0;
    reg [2:0] sa = 0;
    reg [25:0] a_addr; reg [7:0] a_exp;

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            sa <= 0; a_i <= 0; req_valid <= 0; req_wide <= 0;
            served_a <= 0; errors_a <= 0;
        end else if (go) case (sa)
        0: begin
               // address: sweep over a 16 KB window so sets collide with cmap
               a_addr = TEX_BASE + ((a_i*26'd4) % 26'h4000);
               a_exp  = expect_byte(a_addr);
               req_addr <= a_addr; req_wide <= 1'b0; req_valid <= 1'b1; sa <= 1;
           end
        1: if (req_ready) begin req_valid <= 1'b0; sa <= 2; end
        2: if (resp_valid) begin
               if (resp_data[7:0] !== a_exp) begin
                   errors_a <= errors_a + 1;
                   $display("[A] FAIL i=%0d addr=%06x got=%02x exp=%02x (cyc=%0t)",
                            a_i, a_addr, resp_data[7:0], a_exp, $time);
               end
               served_a <= served_a + 1;
               a_i <= a_i + 1;
               sa <= 3'd0;   // loop forever to keep thrashing port-B lines
           end
        endcase
    end

    // ---- Fill-regime instrumentation: count port-A and port-B fills so the
    // summary can show the test really exercised the eviction/thrash regime
    // (real port-B misses through the burst chain, not a trivial all-hit pass).
    integer fillA = 0, fillB = 0, drain_visits = 0, bqv_words = 0;
    reg [2:0] tex_st_p = 0; reg [1:0] adp_st_p = 0;
    always @(posedge clk) begin
        // count a fill each time the cache enters S_FILL_AR (state 1)
        if (go && tex.state == 3'd1 && tex_st_p != 3'd1) begin
            if (tex.lat_port == 1'b0) fillA <= fillA + 1;
            else                      fillB <= fillB + 1;
        end
        tex_st_p <= tex.state;
        // count adapter S_DRAIN entries and burst_q_valid words: confirms the
        // real cram1 sync-burst R-beat pattern (NOT the SDRAM stub) is driven.
        if (go && adapter.state == 2'd2 && adp_st_p != 2'd2) drain_visits <= drain_visits + 1;
        adp_st_p <= adapter.state;
        if (go && burst_q_valid) bqv_words <= bqv_words + 1;
    end

    // =====================================================================
    // Watchdog: HANG vs WRONG-BYTE classification.
    // =====================================================================
    integer last_served_b = 0;
    integer stall_cnt = 0;
    integer wd;
    reg hang_flag = 0;
    always @(posedge clk) begin
        if (go) begin
            if (served_b == last_served_b) stall_cnt <= stall_cnt + 1;
            else begin stall_cnt <= 0; last_served_b <= served_b; end
            if (stall_cnt > 5000 && !hang_flag && served_b < NB) begin
                hang_flag <= 1'b1;
                $display("CLASS: HANG  port B stopped being served at served_b=%0d/%0d (cyc=%0t)",
                         served_b, NB, $time);
                $display("  (HW symptom is WRONG-BYTE; a HANG here = unfaithful TB handshake)");
                $display("  state: pendB_valid=%0b q_cnt=%0d resp_valid_b=%0b req_ready_b=%0b",
                         pendB_valid, q_cnt, resp_valid_b, req_ready_b);
                $display("  tex: pipe_b_live=%0b held_valid_b=%0b state=%0d pipe_miss_b=%0b",
                         tex.pipe_b_live, tex.held_valid_b, tex.state, tex.pipe_miss_b);
                $finish;
            end
        end
    end

    // =====================================================================
    // Master sequence
    // =====================================================================
    integer i, t;
    integer distinct_count;
    integer shifted_cnt, uniform_cnt, region_cnt;
    reg [7:0] first_byte;
    integer fail_total;

    initial begin
        #20 reset_n = 1;
        @(posedge clk); while (!bcr_init_done) @(posedge clk);
        repeat (8) @(posedge clk);

        // ---- Backdoor-preload COLORMAP region: bytes 0..0x4000 ----
        //   word index 0..0xFFF, bank 0.  pat(W) gives the per-byte ramp.
        for (i = 0; i < 'h1000; i = i + 1) begin
            @(posedge clk);
            bd_we <= 1'b1; bd_word_addr <= i[20:0]; bd_wdata_word <= pat(i[21:0]);
        end
        // ---- Backdoor-preload TEXTURE region: bytes 0x10000.. ----
        //   word index 0x4000.. (byte 0x10000 = word 0x4000), bank 0.
        for (i = 0; i < 'h1000; i = i + 1) begin
            @(posedge clk);
            bd_we <= 1'b1; bd_word_addr <= 21'h4000 + i[20:0];
            bd_wdata_word <= pat(22'h4000 + i[21:0]);
        end
        @(posedge clk); bd_we <= 1'b0;

        repeat (4) @(posedge clk);
        go = 1;

        // Run until port B serves all pixels (or global timeout).
        for (t = 0; t < 4000000 && served_b < NB; t = t + 1) @(posedge clk);

        // ---------------- Classification ----------------
        // anti-uniform: count distinct captured bytes.
        distinct_count = 0;
        first_byte = gotB[0];
        uniform_cnt = 0;
        for (i = 0; i < NB; i = i + 1) begin
            if (got_valid[i] && gotB[i] == first_byte) uniform_cnt = uniform_cnt + 1;
        end
        // distinct via O(n^2) lite: count how many differ from first.
        distinct_count = 1;
        for (i = 1; i < NB; i = i + 1)
            if (got_valid[i] && gotB[i] !== first_byte) begin distinct_count = 2; end

        // failure-class tally over the mismatching reads
        shifted_cnt = 0; region_cnt = 0; fail_total = 0;
        for (i = 0; i < NB; i = i + 1) begin
            if (got_valid[i] && gotB[i] !== sh_exp[i]) begin
                fail_total = fail_total + 1;
                // shifted-by-one: equals the neighbouring issued addr's byte
                if (i > 0 && gotB[i] == sh_exp[i-1]) shifted_cnt = shifted_cnt + 1;
                else if (i+1 < NB && gotB[i] == sh_exp[i+1]) shifted_cnt = shifted_cnt + 1;
                else region_cnt = region_cnt + 1;
            end
        end

        $display("----------------------------------------------------------------");
        $display("port B: served=%0d/%0d errors=%0d   port A: served=%0d (concurrent thrash) errors=%0d   chip_err=%0d",
                 served_b, NB, errors_b, served_a, errors_a, cram_errors);
        $display("anti-uniform: distinct_count=%0d (uniform would be 1)  uniform_match=%0d/%0d",
                 distinct_count, uniform_cnt, NB);
        $display("thrash regime: port-B fills=%0d (real misses through burst chain), port-A fills=%0d (aliasing evictions)",
                 fillB, fillA);
        $display("real cram1 burst path exercised: adapter S_DRAIN entries=%0d, burst_q_valid words assembled=%0d",
                 drain_visits, bqv_words);
        if (errors_b != 0) begin
            $display("FAIL-CLASS tally: total_wrong=%0d  uniform=%0d  shifted_by_one=%0d  wrong_region=%0d",
                     fail_total,
                     (distinct_count == 1) ? fail_total : 0,
                     shifted_cnt, region_cnt);
        end

        if (served_b == NB && errors_b == 0 && errors_a == 0
            && cram_errors == 16'd0 && distinct_count > 1) begin
            $display("RESULT: PASS  (%0d colormap bytes byte-exact via CRAM1 sync-burst PORT B; port A %0d byte-exact concurrently; ordering ok; chip_err=0; varies)",
                     served_b, served_a);
            $display("CONCLUSION: port-B data path through the REAL cram1 chain is SOUND. The wrong-palette bug is NOT in gpu_tex_cache port B + adapter + cram1_controller. Next step: route tb_gpu's real-gpu_core fills through the cram1 chain.");
        end else begin
            $display("RESULT: FAIL  (served_b=%0d/%0d errors_b=%0d errors_a=%0d chip_err=%0d distinct=%0d)",
                     served_b, NB, errors_b, errors_a, cram_errors, distinct_count);
            if (errors_b != 0)
                $display("CONCLUSION: REPRODUCED wrong-byte on PORT B through the real cram1 chain (see [B] FAIL lines for failing addresses / class above).");
        end
        $finish;
    end

    initial begin
        #6000000;
        $display("RESULT: FAIL (global timeout served_b=%0d/%0d bcr_done=%0b)", served_b, NB, bcr_init_done);
        $finish;
    end
endmodule

`default_nettype wire
