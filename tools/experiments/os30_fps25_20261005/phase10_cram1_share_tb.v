// Integration TB: GPU texture read from CRAM1 via SYNC BURST, byte-exact,
// against the REAL controller + PHY + behavioral chip model.
//
//   gpu_tex_cache -> gpu_cram1_tex_adapter -> cram1_controller(+cram1_phy) -> cram_chip_model
//
// Two paths proven:
//   A. Backdoor-preloaded texels -> GPU tex fill (sync-burst read) -> correct texel.
//   B. CPU upload via word_wr (async write, chip in BCR 0x241F sync-burst) ->
//      GPU tex fill of those words -> correct texel.  Proves async-write and
//      sync-burst-read coexist on the same chip (the whole point of CRAM1).
//
// Expected texel for byte addr A (non-wide) = A[7:0], because chip word W holds,
// per byte k, ((W*4+k) & 0xFF) — same trivially-checkable pattern as the CRAM0
// chain TB.
`timescale 1ns/1ps
`default_nettype none

module tb_geometry_cram1_share #(parameter EXTRA_REFRESH_CYCLES=0);
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

    reg [1:0] arvalid=0;
    wire [1:0] arready,rvalid,rlast;
    reg [31:0] araddr[0:1]; reg [7:0] arlen[0:1];
    wire [31:0] rdata[0:1];
    wire arvalid_m,arready_m,rvalid_m,rlast_m;
    wire [31:0] araddr_m,rdata_m;wire [7:0] arlen_m;
    geometry_cram1_read_arbiter arbiter(
      .clk(clk),.reset_n(reset_n),
      .s0_arvalid(arvalid[0]),.s0_arready(arready[0]),.s0_araddr(araddr[0]),.s0_arlen(arlen[0]),
      .s0_rvalid(rvalid[0]),.s0_rdata(rdata[0]),.s0_rlast(rlast[0]),
      .s1_arvalid(arvalid[1]),.s1_arready(arready[1]),.s1_araddr(araddr[1]),.s1_arlen(arlen[1]),
      .s1_rvalid(rvalid[1]),.s1_rdata(rdata[1]),.s1_rlast(rlast[1]),
      .m_arvalid(arvalid_m),.m_arready(arready_m),.m_araddr(araddr_m),.m_arlen(arlen_m),
      .m_rvalid(rvalid_m),.m_rdata(rdata_m),.m_rlast(rlast_m));

    gpu_cram1_tex_adapter adapter (
        .clk(clk), .reset_n(reset_n),
        .arvalid(arvalid_m), .arready(arready_m), .araddr(araddr_m), .arlen(arlen_m),
        .rvalid(rvalid_m), .rdata(rdata_m), .rlast(rlast_m),
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
        .config_en(config_en), .config_data(16'h241F), .config_bank_sel(config_bank_sel),
        .raw_busy(raw_busy), .bcr_init_done(bcr_init_done),
        .cram_a(cram_a), .cram_dq_out(cram_ctrl_dq_out), .cram_dq_oe(cram_ctrl_dq_oe),
        .cram_dq_in(cram_dq_to_ctrl), .cram_wait(cram_wait), .cram_clk(cram_clk),
        .cram_adv_n(cram_adv_n), .cram_cre(cram_cre),
        .cram_ce0_n(cram_ce0_n), .cram_ce1_n(cram_ce1_n),
        .cram_oe_n(cram_oe_n), .cram_we_n(cram_we_n),
        .cram_ub_n(cram_ub_n), .cram_lb_n(cram_lb_n)
    );

    cram_chip_model #(.POWERUP_CYCLES(8'd4), .CHECK_AS1C_TIMING(1), .REFRESH_EXTRA_CYCLES(EXTRA_REFRESH_CYCLES)) chip (
        .clk(clk), .cram_clk(cram_clk), .reset_n(reset_n),
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

    // ---- BCR-init FSM: pulse config_en per die (sync burst 0x241F) ----
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


    integer cycles=0,bursts[0:1],words[0:1],wait_cycles[0:1],response_cycles[0:1];
    reg [1:0] inflight=0;
    integer k;
    always @(posedge clk)begin
      cycles<=cycles+1;
      if(reset_n)begin
        if(arready==2'b11 || rvalid==2'b11)$fatal(1,"dual ownership");
        for(integer p=0;p<2;p=p+1)begin
          if(arready[p]&&!arvalid[p])$fatal(1,"spurious ARREADY");
          if(rvalid[p]&&!inflight[p])$fatal(1,"response without owner %0d",p);
        end
      end
    end
    function automatic [31:0] pat(input [21:0] w);
      pat=32'h91a65c03 ^ (w*32'h9e3779b9);
    endfunction
    task automatic preload(input [21:0] base,input integer n);
      for(integer i=0;i<n;i=i+1)begin
        @(negedge clk);bd_we=1;bd_word_addr={base[21],1'b0,base[18:0]}+i;
        bd_wdata_word=pat(base+i);
      end
      @(negedge clk);bd_we=0;
    endtask
    task automatic read_burst(input integer p,input [31:0] a,input integer n);
      integer start,accepted,seen;
      @(negedge clk);araddr[p]=a;arlen[p]=n-1;arvalid[p]=1;start=cycles;
      do @(posedge clk);while(!arready[p]);
      accepted=cycles;inflight[p]=1;
      @(negedge clk);arvalid[p]=0;
      seen=0;
      while(seen<n)begin
        @(posedge clk);
        if(rvalid[p])begin
          if(rdata[p]!==pat(a[23:2]+seen))$fatal(1,"data p=%0d addr=%h beat=%0d got=%h expected=%h",p,a,seen,rdata[p],pat(a[23:2]+seen));
          if(rlast[p] !== (seen==n-1))$fatal(1,"last p=%0d beat=%0d",p,seen);
          seen=seen+1;
        end
      end
      @(negedge clk);inflight[p]=0;
      bursts[p]=bursts[p]+1;words[p]=words[p]+n;
      wait_cycles[p]=wait_cycles[p]+accepted-start;
      response_cycles[p]=response_cycles[p]+cycles-accepted;
    endtask
    task automatic upload(input [21:0] w);
      @(negedge clk);word_addr=w;word_data=pat(w);word_wr=1;
      @(negedge clk);word_wr=0;
      while(!word_busy)@(negedge clk);
      while(word_busy)@(negedge clk);
    endtask
    initial begin
      araddr[0]=0;araddr[1]=0;arlen[0]=0;arlen[1]=0;
      for(k=0;k<2;k=k+1)begin bursts[k]=0;words[k]=0;wait_cycles[k]=0;response_cycles[k]=0;end
      #20 reset_n=1;
      while(bcr_st!=B_DONE)@(negedge clk);
      preload(22'h40000,1024); // 1 MiB geometry/test arena, no modeled address wrap
      preload(22'h240000,128); // same offset in physical die1
      // Uncontended lengths, including controller's maximum32-word burst.
      for(k=1;k<=32;k=k+1)read_burst(1,32'h10100000,k);
      // Arrive on the exact cycle following adapter command capture. A purely
      // combinational arbiter would transfer the delayed acknowledgement.
      fork
        read_burst(1,32'h10100080,16);
        begin wait(adapter.burst_rd);read_burst(0,32'h10100100,4);end
      join
      // Both clients continuously active; each gets another turn at burst
      // boundaries. Include varied tail lengths, both dies and random gaps.
      fork
        begin for(integer t=0;t<100;t=t+1)begin
          repeat((t*13)%5)@(negedge clk);
          read_burst(0,32'h10100000+((t*17)%200)*4,4);
        end end
        begin for(integer t=0;t<100;t=t+1)begin
          repeat((t*7)%3)@(negedge clk);
          read_burst(1,(t%2?32'h10900000:32'h10100000)+((t*3)%64)*4,1+(t%16));
        end end
        begin wait(burst_busy);upload(22'h40300);end
      join
      read_burst(0,32'h10100c00,1); // upload issued during queued reader traffic
      // Fabric reset resets the chip, adapter, arbiter and both consumers.
      // CPU/media reset must never be wired here in a production integration.
      @(negedge clk);reset_n=0;repeat(3)@(negedge clk);reset_n=1;
      while(bcr_st!=B_DONE)@(negedge clk);
      read_burst(1,32'h10100040,16);
      if(cram_errors!=0)$fatal(1,"chip errors %0d",cram_errors);
      $display("RESULT PASS refresh=%0d texture_bursts=%0d geometry_bursts=%0d texture_words=%0d geometry_words=%0d texture_arwait=%0d geometry_arwait=%0d texture_response=%0d geometry_response=%0d cycles=%0d",EXTRA_REFRESH_CYCLES,bursts[0],bursts[1],words[0],words[1],wait_cycles[0],wait_cycles[1],response_cycles[0],response_cycles[1],cycles);
      $finish;
    end
    initial begin #2000000;$fatal(1,"timeout");end
endmodule
`default_nettype wire
