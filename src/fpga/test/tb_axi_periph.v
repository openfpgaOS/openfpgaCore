//
// Verilator testbench for axi_periph_slave.v — exercises the BRAM
// fetch path (192KB region) and peripheral polling reads that the
// boot stub uses on real HW.  tb_system.v uses `bram_model.v` for
// LOCAL, so `axi_periph_slave` itself has never been tested in
// simulation.
//

`default_nettype none

module tb_axi_periph (
    input wire clk,
    input wire reset_n,

    // AXI slave ports — driven by the C++ harness
    input  wire        s_axi_arvalid,
    output wire        s_axi_arready,
    input  wire [31:0] s_axi_araddr,
    input  wire [7:0]  s_axi_arlen,

    output wire        s_axi_rvalid,
    input  wire        s_axi_rready,
    output wire [31:0] s_axi_rdata,
    output wire [1:0]  s_axi_rresp,
    output wire        s_axi_rlast,

    input  wire        s_axi_awvalid,
    output wire        s_axi_awready,
    input  wire [31:0] s_axi_awaddr,
    input  wire [7:0]  s_axi_awlen,
    input  wire [1:0]  s_axi_awburst,

    input  wire        s_axi_wvalid,
    output wire        s_axi_wready,
    input  wire [31:0] s_axi_wdata,
    input  wire [3:0]  s_axi_wstrb,
    input  wire        s_axi_wlast,

    output wire        s_axi_bvalid,
    input  wire        s_axi_bready,
    output wire [1:0]  s_axi_bresp,

    // Observability / debug
    output wire [3:0]  dbg_state,
    output wire        dbg_bram_hold,
    output wire        dbg_can_push_beat,

    // GPU MMIO outputs exposed so the harness can count pulses vs
    // AXI writes submitted — the gpudemo freeze-after-N-frames
    // investigation needs this to prove / disprove whether the AXI
    // periph slave is dropping MMIO writes on the way to the GPU.
    output wire        dbg_gpu_reg_wr,
    output wire [3:0]  dbg_gpu_reg_addr,
    output wire [31:0] dbg_gpu_reg_wdata,
    output wire [3:0]  dbg_save_dt_slot,
    output wire [31:0] dbg_save_dt_size,
    output wire        dbg_save_dt_commit,
    output wire [2:0]  dbg_analogizer_wr_toggle,
    output wire [31:0] dbg_analogizer_wr_settings,
    output wire [31:0] dbg_analogizer_wr_hoffset,
    output wire [31:0] dbg_analogizer_wr_voffset,
    output wire [7:0]  dbg_snac_pin_out,
    output wire [7:0]  dbg_snac_pin_dir,
    output wire        dbg_snac_enable,
    output wire        dbg_pal_commit,
    output reg  [7:0]  dbg_pal_commit_count,
    input  wire        pal_busy_in,

    // CMD_FLIP side-port (from gpu_core in production).  Tests can
    // pulse this and read FB_SWAP_CTRL via the AXI slave to verify
    // fb_swap_pending toggles.  vsync is also exposed so tests can
    // synthesize the rising edge that consumes the pending swap.
    input  wire        gpu_swap_req_in,
    input  wire [1:0]  gpu_swap_idx_in,
    input  wire        vsync_in,
    input  wire        target_dataslot_ack_in,
    input  wire        target_dataslot_done_in,
    input  wire [2:0]  target_dataslot_err_in,
    input  wire        bridge_wr_idle_in,

    input  wire [31:0] cont1_key_in,
    input  wire [31:0] cont1_joy_in,
    input  wire [15:0] cont1_trig_in,
    input  wire [31:0] cont2_key_in,
    input  wire [31:0] cont2_joy_in,
    input  wire [15:0] cont2_trig_in,
    input  wire [31:0] cont3_key_in,
    input  wire [31:0] cont3_joy_in,
    input  wire [15:0] cont3_trig_in,
    input  wire [31:0] cont4_key_in,
    input  wire [31:0] cont4_joy_in,
    input  wire [15:0] cont4_trig_in,
    input  wire [31:0] dt_query_data_in,
    input  wire        dt_query_valid_in,
    input  wire [7:0]  snac_pin_in_in,
    output wire        ext_irq_out
);

// Tie-offs / harness-driven peripheral inputs.
wire target_dataslot_ack   = target_dataslot_ack_in;
wire target_dataslot_done  = target_dataslot_done_in;
wire [2:0] target_dataslot_err = target_dataslot_err_in;
wire [31:0] link_reg_rdata = 32'b0;
wire [8:0] audio_fifo_level = 9'b0;
wire audio_fifo_full = 1'b0;
wire uart_tx_active = 1'b0;
wire uart_rx_dv = 1'b0;
wire [7:0] uart_rx_byte = 8'b0;
wire shutdown_pending = 1'b0;
wire [31:0] dt_query_data = dt_query_data_in;
wire dt_query_valid = dt_query_valid_in;
wire [7:0] snac_pin_in = snac_pin_in_in;
// Mock GPU register-read mux: returns a unique sentinel per address so
// the harness can verify that each AR returns the data of the requested
// register (catches the "read returns previous read's value" bug from
// the registered gpu_reg_rdata_r boundary that was removed in core_top).
reg  [31:0] gpu_reg_rdata;
always @(*) begin
    case (dbg_gpu_reg_addr)
        4'd1:  gpu_reg_rdata = 32'h1111_1111;
        4'd4:  gpu_reg_rdata = 32'h4444_4444;
        4'd5:  gpu_reg_rdata = 32'h5555_5555;
        4'd6:  gpu_reg_rdata = 32'h6666_6666;
        default: gpu_reg_rdata = 32'h0;
    endcase
end
wire link_irq = 1'b0;
wire bridge_wr_idle = bridge_wr_idle_in;
wire dataslot_allcomplete = 1'b1;
wire vsync = vsync_in;
wire [31:0] cont1_key = cont1_key_in;
wire [31:0] cont1_joy = cont1_joy_in;
wire [15:0] cont1_trig = cont1_trig_in;
wire [31:0] cont2_key = cont2_key_in;
wire [31:0] cont2_joy = cont2_joy_in;
wire [15:0] cont2_trig = cont2_trig_in;
wire [31:0] cont3_key = cont3_key_in;
wire [31:0] cont3_joy = cont3_joy_in;
wire [15:0] cont3_trig = cont3_trig_in;
wire [31:0] cont4_key = cont4_key_in;
wire [31:0] cont4_joy = cont4_joy_in;
wire [15:0] cont4_trig = cont4_trig_in;
wire [31:0] app_id = 32'b0;
wire pal_commit_w;

assign dbg_pal_commit = pal_commit_w;

always @(posedge clk or negedge reset_n) begin
    if (!reset_n)
        dbg_pal_commit_count <= 8'd0;
    else if (pal_commit_w)
        dbg_pal_commit_count <= dbg_pal_commit_count + 8'd1;
end

// Model core_top's one-cycle register boundary between audio_mixer's
// combinational position mux and axi_periph_slave's read data input.
wire [4:0]  mix_voice_sel_rd;
reg  [21:0] mix_pos_readback_r;
always @(posedge clk or negedge reset_n) begin
    if (!reset_n)
        mix_pos_readback_r <= 22'd0;
    else
        mix_pos_readback_r <= 22'h100 + {17'd0, mix_voice_sel_rd};
end

// DUT
axi_periph_slave dut (
    .clk    (clk),
    .reset_n(reset_n),

    .s_axi_arvalid(s_axi_arvalid), .s_axi_arready(s_axi_arready),
    .s_axi_araddr (s_axi_araddr),  .s_axi_arlen  (s_axi_arlen),
    .s_axi_rvalid (s_axi_rvalid),  .s_axi_rready (s_axi_rready),
    .s_axi_rdata  (s_axi_rdata),   .s_axi_rresp  (s_axi_rresp),
    .s_axi_rlast  (s_axi_rlast),
    .s_axi_awvalid(s_axi_awvalid), .s_axi_awready(s_axi_awready),
    .s_axi_awaddr (s_axi_awaddr),  .s_axi_awlen  (s_axi_awlen),
    .s_axi_awburst(s_axi_awburst),
    .s_axi_wvalid (s_axi_wvalid),  .s_axi_wready (s_axi_wready),
    .s_axi_wdata  (s_axi_wdata),   .s_axi_wstrb  (s_axi_wstrb),
    .s_axi_wlast  (s_axi_wlast),
    .s_axi_bvalid (s_axi_bvalid),  .s_axi_bready (s_axi_bready),
    .s_axi_bresp  (s_axi_bresp),

    .dataslot_allcomplete(dataslot_allcomplete),
    .vsync               (vsync),
    .cont1_key           (cont1_key),
    .cont1_joy           (cont1_joy),
    .cont1_trig          (cont1_trig),
    .cont2_key           (cont2_key),
    .cont2_joy           (cont2_joy),
    .cont2_trig          (cont2_trig),
    .cont3_key           (cont3_key),
    .cont3_joy           (cont3_joy),
    .cont3_trig          (cont3_trig),
    .cont4_key           (cont4_key),
    .cont4_joy           (cont4_joy),
    .cont4_trig          (cont4_trig),
    .target_dataslot_ack (target_dataslot_ack),
    .target_dataslot_done(target_dataslot_done),
    .target_dataslot_err (target_dataslot_err),
    .bridge_wr_idle      (bridge_wr_idle),

    .color_mode     (),
    .fb_display_addr(),

    .pal_wr  (), .pal_addr(), .pal_data(), .pal_commit(pal_commit_w),
    .pal_busy(pal_busy_in),

    .target_dataslot_read      (),
    .target_dataslot_write     (),
    .target_dataslot_openfile  (),
    .target_dataslot_getfile   (),
    .target_dataslot_id        (),
    .target_dataslot_slotoffset(),
    .target_dataslot_bridgeaddr(),
    .target_dataslot_length    (),
    .target_buffer_param_struct(),
    .target_buffer_resp_struct (),

    .audio_fifo_level (audio_fifo_level),
    .audio_fifo_full  (audio_fifo_full),

    .mix_enable(),
    .mix_voice_wr(),
    .mix_voice_sel(),
    .mix_voice_field(),
    .mix_voice_wdata(),
    .mix_irq_clear_wr(),
    .mix_irq_clear(),
    .mix_master_vol(),
    .mix_group_vol_0(),
    .mix_group_vol_1(),
    .mix_group_vol_2(),
    .mix_group_vol_3(),
    .mix_voice_group_packed(),
    .mix_voice_sel_rd(mix_voice_sel_rd),
    .mix_active_mask(32'hA5A5_1234),
    .mix_pos_readback(mix_pos_readback_r),
    .mix_voice_end_pending(32'h0000_00C0),

    .link_reg_wr   (),
    .link_reg_rd   (),
    .link_reg_addr (),
    .link_reg_wdata(),
    .link_reg_rdata(link_reg_rdata),

    .save_dt_slot   (dbg_save_dt_slot),
    .save_dt_size   (dbg_save_dt_size),
    .save_dt_commit (dbg_save_dt_commit),

    .uart_tx_dv    (),
    .uart_tx_byte  (),
    .uart_tx_active(uart_tx_active),
    .uart_rx_dv    (uart_rx_dv),
    .uart_rx_byte  (uart_rx_byte),

    .app_id         (app_id),
    .analogizer_settings(32'h00008C43),
    .analogizer_hoffset (32'hFFFF_FFF9),
    .analogizer_voffset (32'h0000_0005),
    .analogizer_cpu_wr_toggle(dbg_analogizer_wr_toggle),
    .analogizer_cpu_wr_settings(dbg_analogizer_wr_settings),
    .analogizer_cpu_wr_hoffset(dbg_analogizer_wr_hoffset),
    .analogizer_cpu_wr_voffset(dbg_analogizer_wr_voffset),
    .shutdown_pending(shutdown_pending),
    .shutdown_ack   (),

    .timer_irq  (),
    .uart_rx_irq(),

    .link_irq        (link_irq),
    .ext_irq         (ext_irq_out),

    .dt_query_addr  (),
    .dt_query_toggle(),
    .dt_query_data  (dt_query_data),
    .dt_query_valid (dt_query_valid),

    .vrr_v_total(),
    .analogizer_enabled(1'b0),

    .snac_pin_out(dbg_snac_pin_out),
    .snac_pin_dir(dbg_snac_pin_dir),
    .snac_pin_in (snac_pin_in),
    .snac_enable (dbg_snac_enable),

    .gpu_reg_wr   (dbg_gpu_reg_wr),
    .gpu_reg_addr (dbg_gpu_reg_addr),
    .gpu_reg_wdata(dbg_gpu_reg_wdata),
    .gpu_reg_rdata(gpu_reg_rdata),
    // No gpu_core in this bench — tie the CMD_FLIP side-port low.
    .gpu_swap_req (gpu_swap_req_in),
    .gpu_swap_idx (gpu_swap_idx_in)
);

// Hoist FSM internals for debug
assign dbg_state         = dut.state;
assign dbg_bram_hold     = dut.bram_hold;
assign dbg_can_push_beat = dut.can_push_beat;

endmodule
