// Simulation-only wrapper around byte-identical retained-A CRAM1 RTL.
module texture_memory #(parameter REFRESH_EXTRA_CYCLES=0)(
    input wire clk, reset_n,
    input wire arvalid, output wire arready, input wire [31:0] araddr,
    input wire [7:0] arlen, output wire rvalid, output wire [31:0] rdata,
    output wire rlast,
    input wire up_wr, input wire [21:0] up_addr, input wire [31:0] up_data,
    output wire up_busy,
    output wire bcr_done, output wire [15:0] errors
);
wire burst_rd,burst_busy,burst_q_valid;
wire [21:0] burst_addr;
wire [4:0] burst_len;
wire [31:0] burst_q;
wire [21:16] cram_a;
wire [15:0] dq_out,chip_dq;
wire dq_oe,cram_wait,cram_clk,adv_n,cre,ce0_n,ce1_n,oe_n,we_n,ub_n,lb_n;
wire [15:0] dq_in=dq_oe?dq_out:chip_dq;
reg config_en=0,config_bank_sel=0;
wire raw_busy;
gpu_cram1_tex_adapter adapter(
    .clk(clk),.reset_n(reset_n),.arvalid(arvalid),.arready(arready),.araddr(araddr),.arlen(arlen),
    .rvalid(rvalid),.rdata(rdata),.rlast(rlast),.burst_rd(burst_rd),.burst_addr(burst_addr),
    .burst_len(burst_len),.burst_q(burst_q),.burst_q_valid(burst_q_valid),.burst_busy(burst_busy)
);
cram1_controller #(.CLOCK_SPEED(100.0)) ctrl(
    .clk(clk),.reset_n(reset_n),.word_rd(1'b0),.word_wr(up_wr),.word_addr(up_addr),
    .word_data(up_data),.word_wstrb(4'hf),.word_q(),.word_busy(up_busy),.word_q_valid(),
    .burst_rd(burst_rd),.burst_addr(burst_addr),.burst_len(burst_len),.burst_q(burst_q),
    .burst_q_valid(burst_q_valid),.burst_busy(burst_busy),.config_en(config_en),
    .config_data(16'h241f),.config_bank_sel(config_bank_sel),.raw_busy(raw_busy),.bcr_init_done(),
    .cram_a(cram_a),.cram_dq_out(dq_out),.cram_dq_oe(dq_oe),.cram_dq_in(dq_in),
    .cram_wait(cram_wait),.cram_clk(cram_clk),.cram_adv_n(adv_n),.cram_cre(cre),
    .cram_ce0_n(ce0_n),.cram_ce1_n(ce1_n),.cram_oe_n(oe_n),.cram_we_n(we_n),
    .cram_ub_n(ub_n),.cram_lb_n(lb_n)
);
cram_chip_model #(.POWERUP_CYCLES(4),.CHECK_AS1C_TIMING(1),.CLOCK_MHZ(100.0),
                  .REFRESH_EXTRA_CYCLES(REFRESH_EXTRA_CYCLES)) chip(
    .clk(clk),.cram_clk(cram_clk),.reset_n(reset_n),.cram_a(cram_a),
    .cram_dq_in(dq_oe?dq_out:16'b0),.cram_dq_out(chip_dq),.cram_dq_oe(dq_oe),
    .cram_wait_out(cram_wait),.cram_adv_n(adv_n),.cram_cre(cre),.cram_ce0_n(ce0_n),
    .cram_ce1_n(ce1_n),.cram_oe_n(oe_n),.cram_we_n(we_n),.cram_ub_n(ub_n),.cram_lb_n(lb_n),
    .cram_wr_strobe(1'b0),.bd_we(1'b0),.bd_word_addr(22'b0),.bd_wdata_word(32'b0),.error_count(errors)
);
// Same two-die BCR sequencing as Pocket, after model power-up.
reg [3:0] state=0;
reg [7:0] warm=0;
always @(posedge clk or negedge reset_n) begin
    if(!reset_n)begin state<=0;warm<=0;config_en<=0;config_bank_sel<=0;end
    else begin
        config_en<=0;
        case(state)
        0:if(warm<20)warm<=warm+1;else state<=1;
        1:begin config_bank_sel<=0;config_en<=1;state<=2;end
        2:if(raw_busy)state<=3;
        3:if(!raw_busy)state<=4;
        4:begin config_bank_sel<=1;config_en<=1;state<=5;end
        5:if(raw_busy)state<=6;
        6:if(!raw_busy)state<=7;
        default:;
        endcase
    end
end
assign bcr_done=(state==7);
always @(posedge clk) if(reset_n&&errors) $fatal(1,"CRAM1 model error");
endmodule
