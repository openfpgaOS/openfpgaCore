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
    output wire        dbg_can_push_beat
);

// Tie-offs for peripheral inputs we don't exercise.
wire target_dataslot_ack   = 1'b0;
wire target_dataslot_done  = 1'b0;
wire [2:0] target_dataslot_err = 3'b0;
wire [31:0] link_reg_rdata = 32'b0;
wire [8:0] audio_fifo_level = 9'b0;
wire audio_fifo_full = 1'b0;
wire uart_tx_active = 1'b0;
wire uart_rx_dv = 1'b0;
wire [7:0] uart_rx_byte = 8'b0;
wire shutdown_pending = 1'b0;
wire [5:0] mix_active_count = 6'b0;
wire [21:0] mix_voice_pos = 22'b0;
wire [47:0] mix_irq_pending = 48'b0;
wire [31:0] dt_query_data = 32'b0;
wire dt_query_valid = 1'b0;
wire [7:0] snac_pin_in = 8'b0;
wire [31:0] gpu_reg_rdata = 32'b0;
wire link_irq = 1'b0;
wire mix_voice_end_irq = 1'b0;
wire bridge_wr_idle = 1'b1;
wire dataslot_allcomplete = 1'b1;
wire vsync = 1'b0;
wire [31:0] cont1_key = 32'b0;
wire [31:0] cont1_joy = 32'b0;
wire [15:0] cont1_trig = 16'b0;
wire [31:0] cont2_key = 32'b0;
wire [31:0] cont2_joy = 32'b0;
wire [15:0] cont2_trig = 16'b0;
wire [31:0] app_id = 32'b0;

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
    .target_dataslot_ack (target_dataslot_ack),
    .target_dataslot_done(target_dataslot_done),
    .target_dataslot_err (target_dataslot_err),
    .bridge_wr_idle      (bridge_wr_idle),

    .color_mode     (),
    .fb_display_addr(),

    .pal_wr  (), .pal_addr(), .pal_data(),

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

    .audio_sample_wr  (),
    .audio_sample_data(),
    .audio_fifo_level (audio_fifo_level),
    .audio_fifo_full  (audio_fifo_full),

    .link_reg_wr   (),
    .link_reg_rd   (),
    .link_reg_addr (),
    .link_reg_wdata(),
    .link_reg_rdata(link_reg_rdata),

    .save_dt_slot   (),
    .save_dt_size   (),
    .save_dt_commit (),

    .uart_tx_dv    (),
    .uart_tx_byte  (),
    .uart_tx_active(uart_tx_active),
    .uart_rx_dv    (uart_rx_dv),
    .uart_rx_byte  (uart_rx_byte),

    .app_id         (app_id),
    .shutdown_pending(shutdown_pending),
    .shutdown_ack   (),

    .mix_voice_wr    (),
    .mix_voice_field (),
    .mix_voice_sel   (),
    .mix_voice_wdata (),
    .mix_enable      (),
    .mix_active_count(mix_active_count),
    .mix_voice_pos   (mix_voice_pos),
    .mix_irq_clear_wr(),
    .mix_irq_clear_data(),
    .mix_irq_pending (mix_irq_pending),

    .timer_irq  (),
    .uart_rx_irq(),

    .link_irq        (link_irq),
    .mix_voice_end_irq(mix_voice_end_irq),
    .ext_irq         (),

    .dt_query_addr  (),
    .dt_query_toggle(),
    .dt_query_data  (dt_query_data),
    .dt_query_valid (dt_query_valid),

    .vrr_v_total(),

    .snac_pin_out(),
    .snac_pin_dir(),
    .snac_pin_in (snac_pin_in),
    .snac_enable (),

    .gpu_reg_wr   (),
    .gpu_reg_addr (),
    .gpu_reg_wdata(),
    .gpu_reg_rdata(gpu_reg_rdata)
);

// Hoist FSM internals for debug
assign dbg_state         = dut.state;
assign dbg_bram_hold     = dut.bram_hold;
assign dbg_can_push_beat = dut.can_push_beat;

endmodule
